# Decision 2: powering the touch IC (S2MPS18 LDO35/LDO43) through the ACPM write gate

2026-11-05. Research only. `K49` = `/root/crownlte-hid-kernel`; `DT49` = its crownlte DTS; `I` = 6.12 tree
`/mnt/ssd/host-builds/kernel-upgrade/integration` (crownlte-display-flipfix 15b2d1d78). Touch stage 1 is described in
`I/Documentation/crownlte/touch-port.md`.

## TL;DR

There are only two register updates. Each copies exactly what 4.9 sends through ACPM:

| rail | reg | SBoot leaves | 4.9 enable: `exynos_acpm_update_reg(type 0x01, reg, val, mask 0xC0)` | result |
|---|---|---|---|---|
| LDO35 `tsp_io` 1.8 V | **0x6C** | 0x2C (vsel 1.8 V, off) | val **0x80** (mode "on while PWREN_MIF") | **0xAC** |
| LDO43 `tsp_avdd` 3.0 V | **0x74** | 0x30 (vsel 3.0 V, off) | val **0xC0** (always on) | **0xF0** |

Order: LDO35, then 1 ms, then LDO43, then 70 ms before talking to the IC. The proposed gate exception allows only these two `(reg, mask, value)` triples plus their exact "off" twins. It refuses if the voltage bits differ from the values above, and it checks the result by reading back.

## 1. What 4.9 does

- **Consumer.** `touchscreen@48` (DT49:6012-6056) names `sec,regulator_dvdd = "tsp_io"` and `sec,regulator_avdd = "tsp_avdd"` with `sec,regulator_boot_on` (DT49:6048-6050).
- **Rails.** DT49:8762-8782:
  - `LDO35 tsp_io`: 1.8 V min=max, `regulator-initial-mode = <2>`.
  - `LDO43 tsp_avdd`: 3.0 V min=max, `initial-mode = <3>`.
  - Both have ramp 12 mV/µs and `regulator-boot-on`.
- **Power sequence.** `sec_ts_power()` (`K49/drivers/input/touchscreen/sec_ts/y771/sec_ts.c:1715-1775`):
  - on: `regulator_enable(dvdd)` → `sec_ts_delay(1)` (1 ms) → `regulator_enable(avdd)`;
  - off: avdd off → 4 ms → dvdd off.
  - Probe calls it at :2393 and skips the 70 ms settle because of `regulator_boot_on` (:2394).
  - `sec_ts_start_device` waits 70 ms after power-on (:3450), then `sec_ts_wait_for_ready` (BOOT_COMPLETE event, up to 100 × 20 ms).
- **Register write.** `s2m_enable()` (`K49/drivers/regulator/s2mps18.c:151-164`) → `s2mps18_update_reg(pmic 0x01, enable_reg, opmode[id], 0xC0)` → `exynos_acpm_update_reg(0x01, reg, val, mask)` (`K49/drivers/mfd/s2mps18_core.c:116-129`). That is a firmware-side SPEEDY read-modify-write, so only bits 7:6 change.
- **Mode bits.**
  - `s2mps18_of_map_mode` (s2mps18.c:99-111) maps SEC_OPMODE_MIF (2) → 0x2 and SEC_OPMODE_ON (3) → 0x3 (enum `K49/include/linux/mfd/samsung/s2mps18.h:58-65`).
  - `s2m_set_mode` (:113-124) stores `mode << 6`, giving opmode 0x80 for LDO35 and 0xC0 for LDO43.
  - Registers: `S2MPS18_PMIC_REG_L35CTRL 0x6C`, `L43CTRL 0x74` (`s2mps18-private.h:142,150`).
  - Voltage: LDO35 uses MIN1 0.7 V + 25 mV × 0x2C = 1.8 V; LDO43 uses MIN3 1.8 V + 25 mV × 0x30 = 3.0 V (`s2mps18.c:534-551`). 4.9 never writes vsel here, because min = max and the bootloader value already matches.
- **On 6.12.** The bootloader leaves both rails off (0x2C/0x30, q23 PMIC dump), so the IC does not answer at 0x48. Touch stage 1 already has the I2C, IRQ and protocol pieces; only power is missing.

## 2. Proposed minimal 6.12 implementation

**A. The ACPM gate (`I/drivers/soc/samsung/acpm/acpm_mfd.c`).** Today `exynos_acpm_update_reg` (:338-385) allows only the G3D rail triple. Add a second exact-match predicate, `acpm_mfd_touch_rail_allowed()`, shaped like `acpm_mfd_g3d_rail_allowed` (:323-336):
- Allow only `type == ACPM_MFD_TYPE_PMIC (0x1)` with `mask == 0xC0` and one of:
  - `(reg 0x6C, val 0x80)`
  - `(0x6C, 0x00)`
  - `(0x74, 0xC0)`
  - `(0x74, 0x00)`
- Enable it with a new sealed-policy bit `touch_rails` (default 0) that a DT property sets. `seal_write_policy` already logs every bit.
- Keep generic `FUNC_WRITE`/`bulk_write` to PMIC refused. The firmware RMW cannot spill outside bits 7:6, which resolves the concern in the gate's comment at :346-351.

**B. One caller, a touch power helper** (in the sec_ts_y771 driver or a small `crownlte-touch-power` driver). It refuses unless every check passes:

1. Read 0x6C and 0x74 with `exynos_acpm_read_reg`. Require **vsel (bits 5:0) == 0x2C and 0x30** exactly, and bits 7:6 ∈ {00, expected}. Otherwise log and stop, and never enable.
2. Enable LDO35 (0x80/0xC0), read back, and require 0xAC.
3. Wait 1-2 ms.
4. Enable LDO43 (0xC0/0xC0), read back, and require 0xF0.
5. Wait 70 ms, then let the existing stage-1 probe run (`sec_ts_wait_for_ready` while IRQ is low).
6. Order is enforced in the helper: LDO43 on is refused unless LDO35 reads on. Off goes LDO43, 4 ms, LDO35, and is used only on probe failure or shutdown.
7. A per-boot counter allows at most 2 power cycles.
8. Make no other PMIC access: no vsel, ramp, BUCK or other LDO registers.

**C. DT.** The gate opt-in sits on the ACPM MFD node. The touch node gets the rails as plain properties, not regulator-framework supplies, to avoid pulling in a whole S2MPS18 regulator driver. A full regulator driver can come later.

Effort: about 1 day of code plus a Sol review loop (PMIC = high-risk category).

## 3. Risks

| What could go wrong | Reversible? | Protection | Worst case |
|---|---|---|---|
| Wrong register (e.g. 0x6B L34 or 0x75 L44) | Yes, by reboot; the PMIC reverts on reset | exact constants in two places (gate + helper), read-before-write, readback, unit test of the predicate | turning on an unrelated rail at its stored voltage, powering a device in an unknown state (soft) |
| **LDO overvoltage** | — | **Not possible through this path**: vsel is never written, and the helper refuses unless vsel is 0x2C/0x30 = the 4.9 DT voltages | if SBoot ever left a different vsel, the helper refuses (no power) instead of enabling |
| Reversed order (AVDD before DVDD) | probably | ordering enforced in the helper, and the gate refuses 0x74 enable unless 0x6C reads on | IC latch-up or stress (GUESS, small; 4.9 never does it) |
| Other loads on LDO35/43 | — | DT49 lists only sec_ts as consumer; 4.9 itself switches both rails off when touch stops (`sec_ts.c:2617`, stop_device) and SBoot leaves them off while the rest of the phone works | GUESS: tsp_io may also feed the hsi2c10 pull-ups. Harmless, because that bus has only the touch IC |
| LDO35 mode 0b10 "on while PWREN_MIF" | — | same mode as 4.9; MIF stays up because 6.12 has no suspend | after suspend arrives, tsp_io drops in deep sleep (same as 4.9) |
| Touch firmware flash / NVM writes | — | stage 1 has no firmware, flash or NVM opcodes (allowlist, touch-port.md) | a bricked touch IC is prevented by design |
| ACPM firmware RMW fails mid-way | yes | firmware status checked; readback mismatch → off sequence, stop | rail left on; harmless |

Battery cost is a few mA. Nothing irreversible: no OTP, no non-volatile PMIC state. The S2MPS18 resets its enable bits on every reboot, as SBoot's 0x2C/0x30 shows.

## 4. Staged test plan

1. **T0, read-only.** It already exists: the q23 PMIC dump `pmic[0x69..0x76]`. Re-run it once on the ROM-RAM image to confirm 0x6C=0x2C and 0x74=0x30 after a 4.9 reboot. Also confirm under 4.9 with `cat /sys/class/regulator/*/{name,state,microvolts}` that tsp_io is 1800000 enabled and tsp_avdd is 3000000 enabled.
2. **T1, gate dry-run.** Policy bit on, but the helper only logs what it would send ("would update 0x6C val 0x80 mask 0xC0") after the vsel check. No write.
3. **T2, power on with the IC held passive.** Enable both rails, read back, wait 70 ms. Read only the touch IDs: 0x22 device id, 0x55 boot status, 0xAF status. Do not send SENSE_ON. Pass = 0xAC/0xF0 and an ID read.
4. **T3, the full stage-1 touch self-test** (SENSE_ON + SET_TOUCHFUNCTION, events logged), with a person tapping the screen.
5. **T4, under Lineage (ROM-RAM).** The input device registers and taps work in the setup wizard.

## 5. Owner decisions

| # | Decision | Recommendation |
|---|---|---|
| 1 | Widen the ACPM write gate to exactly the 4 `(reg, value, mask 0xC0)` triples for 0x6C/0x74, policy-bit gated | **Yes** |
| 2 | Use 4.9's exact modes (LDO35 0b10, LDO43 0b11), not "both 0b11" | **Yes**, exact 4.9 parity |
| 3 | Rails as a one-purpose helper now, a full S2MPS18 regulator driver later | **Yes**, helper now |
| 4 | Any touch firmware update path | **No** (stays out of the port) |
