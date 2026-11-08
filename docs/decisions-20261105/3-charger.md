# Decision 3: MAX77705 charger, keeping the battery charging safely on 6.12

2026-11-05. Research only. `K49` = `/root/crownlte-hid-kernel`; `CHG` = `K49/drivers/battery_v2/max77705_charger.c`;
`REG` = `K49/include/linux/mfd/max77705-private.h`; `DT49` = 4.9 crownlte DTS. The 6.12 read-only monitor is
`max77705-ro-battery.c` (`integration/Documentation/crownlte/max77705-battery.md`). Charger I2C is slave 0x69 on HSI2C 0x14360000.
This is a plain I2C bus, not ACPM.

## TL;DR

The 6.12 read-only monitor's own logs (`C:\Users\Patrick\tools\trial-acpm-q34-adc-s2-20261104\serial-ready.bin`,
`...q48-dispmatrix-s2...\serial-ready.bin`) show that **SBoot leaves charging on**:

| when | CHG_DTLS | CNFG_00 | CC / CV / ILIM | BC1.2 | battery current |
|---|---|---|---|---|---|
| ~2.6 s (probe) | 0x1 **fast charge CC** | 0x15 = mode 5 (buck+charger), **WDTEN=1** | 450 mA / 4350 mV / **500 mA** | 0x81 = SDP (PC port) | **-939 mA** (q48), -788 mA (q34) |
| ~108 s (self-test) | **0xB = watchdog expired** | 0x15 (unchanged) | same | SDP | -708 mA |

There are two separate problems:
1. **No one kicks the watchdog,** so charging stops at about 80 s (GUESS for the period: MAX7770x tWD; bounded here as 2.6 s < t < 108 s).
2. **Even while charging, the battery drains.** A 500 mA SDP input limit cannot feed a 6.12 system with no cpuidle, HZ_PERIODIC and the panel at 44700 (HBM).

A watchdog kick alone therefore restores *charging state*, not net charge, on a PC port.

**Recommendation:** kick the watchdog, guarded by a temperature and voltage supervisor. **Never disable the watchdog.** The watchdog is the hardware safety net: if 6.12 hangs, charging stops within about 80 s.

## 1. What 4.9 writes, and when

| When | Write | Where |
|---|---|---|
| probe → `max77705_charger_initialize` | CNFG_06 (0xBD) bits 3:2=11 (CHGPROT unlock) + 0x60 slowest LX; CNFG_01 (0xB8) fast-charge timer off, restart off, prequal off, RECYCLE_EN; CNFG_02 OTG limit; B2SOVRC; CNFG_03 top-off 150 mA/30 min; **CNFG_04 CV = 4350 mV** (`chg_float_voltage`, DT49:13722); CNFG_12 VCHGIN_REG; CNFG_07 FMBST/FGSRC; **CNFG_00 WDTEN=1**; PMIC MAINCTRL1 bit 0; **CNFG_09 bit 7 CHG_EN**; CNFG_11 VBYPSET | CHG:996-1090 (WDTEN at :1078) |
| probe | WDT kick | CHG:2662 |
| **every health read** (sec_battery monitor; `polling_time = <10 30 30 30 3600>` s, DT49:13628) | **kick = `update_reg(CNFG_06, 0x01, mask 0x03)`** (WDTCLR=01, other bits preserved) | CHG:227-235, :300 |
| health read, if charging and CHG_DTLS ∈ {0x8, **0xB**} | **restart:** CNFG_00 CHG bit 0 → 0, CV rewritten, CHG bit 0 → 1 | CHG:343-349, `set_charger_state` CHG:793-813 |
| cable/current changes | CNFG_09 CHGIN_ILIM = mA/25 − 1 (500 mA → 0x13, 400 mA → 0x0F); CNFG_02 CC | CHG:677-720 |
| **shutdown** | CNFG_00=**0x04** (buck only, charger off, WDT off), CNFG_09=**0x0F** (400 mA, CHG_EN=0), CNFG_10=0x10, CNFG_12=0x60 | CHG:2800-2826 |

Registers: DETAILS_01 0xB4, CNFG_00 0xB7, CNFG_06 0xBD, CNFG_09 0xC0, CNFG_12 0xC3 (REG:75-90). CNFG_00 bits: MODE 3:0 (CHG=b0, BUCK=b2), WDTEN=b4 (`drivers/battery_v2/include/charger/max77705_charger.h:120-135`).

4.9 safety limits (DT49:13663-13737) that a 6.12 supervisor must not exceed:

| Limit | 4.9 behaviour |
|---|---|
| Battery high | block 50.0 °C, recover 45.0 °C |
| Battery low | block 0 °C, recover 5 °C |
| Swelling high | ≥ 41.0 °C: CV drops to 4.15 V and current to 1400 mA (recover 39.0 °C) |
| Swelling low | < 15 °C: 950 mA; < 5 °C: second tier |
| Charger NTC | 46 °C starts limiting (`chg_high_temp`), recover 44 °C |
| Float voltage | 4.35 V |
| Recharge | ≤ 4.28 V or SOC 98 % |
| Full check | 400 → 200 mA |

## 2. Options for "keep charging safely"

| Option | Writes | Safety if 6.12 hangs | Verdict |
|---|---|---|---|
| **A. Kick only** (CNFG_06 bits 1:0 = 01 RMW), gated by a supervisor | 1 register, 2 bits | WDT expires in ~80 s → charging stops by itself | **Recommended** |
| B. Disable WDT (CNFG_00 b4=0) | 1 bit | charging continues **unsupervised** at SBoot's settings, with no software thermal stop in 6.12 | Reject |
| C. Full 4.9 parity (initialize + per-cable currents + restart + shutdown) | ~15 registers | as 4.9 | Later (L effort; needs `sec_battery` logic) |

**Supervisor for A.** It reuses the existing read-only monitor: SoC ADC ch4 battery NTC, ch3 charger NTC, FG VCELL. Kick only when **all** of the following hold:
- battery 10 °C ≤ T < 41 °C. This is stricter than 4.9, because option A cannot lower CV/CC the way 4.9's swelling logic does. Resume at < 39 °C.
- charger NTC < 44 °C.
- VCELL ≤ 4.40 V.
- CNFG_04 still reads CV 4350 mV.
- CNFG_02/CNFG_09 read the SBoot values seen at probe (CC 450 mA, ILIM ≤ 500 mA). Anything else, e.g. AFC or the MCU auto-IBUS raising them, means stop kicking and log.
- no FG or ADC read errors in the last poll.
- battery present.

When kicking stops, the watchdog ends charging within about 80 s. Kick every 10 s, matching 4.9's charging poll. That leaves a margin of 8 against 80 s.

**Already expired at probe (DTLS 0xB).** Kicking does not restart charging. 4.9 toggles CNFG_00 b0 (CHG:343-349). Make that a separate, second opt-in (stage C2): one mask-0x01 toggle, only when the supervisor is green. GUESS: if the driver kicks from probe (~2.6 s), the WDT never expires in a normal boot, so C2 may be unnecessary.

**Shutdown hook.** It is not needed for safety under option A, because the WDT expires and SBoot reprograms the charger on every boot (CNFG_00 0x15 observed at probe. Inference: these trials were entered via `adb reboot recovery` from 4.9, whose shutdown hook had written 0x04, so SBoot re-enabled charging). Port it with option C.

**Net charge.** For the battery to gain charge, the input must exceed the load:
- Use a **wall charger (BC1.2 DCP)** for 6.12 sessions. GUESS: SBoot then sets a higher ILIM. The monitor will show it, and the supervisor accepts any SBoot value ≤ 1500 mA.
- Reduce load: cpuidle (in progress) and a lower panel brightness. The latter is impossible on this panel; see decision 1.
- Raising CNFG_09 ourselves is a separate decision (C3) with the 4.9 encoding, mA/25 − 1, mask CHGIN_LIM.

## 3. Risk analysis

| Risk | Reversible? | Protection | Worst case |
|---|---|---|---|
| Kick lets charging continue while hot | yes | supervisor stricter than 4.9 (41 °C stop); 4.35 V CV is the charger's hardware limit; WDT backstop | battery charges at 450 mA up to 41 °C; MAX77705 thermal regulation still active |
| Stale or wrong temperature (NTC table/ADC bug) | yes | two NTCs must agree within 5 °C, else stop; ADC errors stop kicking | a wrong reading stops charging (fail-safe) |
| RMW on CNFG_06 clobbers CHGPROT/SLOWLX | yes | read-modify-write mask 0x03 only, plus readback of bits 7:2 unchanged | CHGPROT relock → later writes ignored; harmless for A |
| Write to wrong slave/register | yes (registers reset on reboot) | write path compiled only for (0x69, 0xBD, mask 0x03); `#pragma GCC poison` stays for everything else | changes charger config until reboot |
| 6.12 hang or panic while charging | — | WDT expires ~80 s later; charging stops | none beyond 4.9 |
| **Battery not charging** (status quo) | — | — | discharge 0.7-0.9 A; a deep discharge ends the session (shutdown at 0 %). No damage |
| Over-discharge from long 6.12 sessions | yes | FG shutdown at 0 % / 3.4 V (driver reports capacity 0) | phone powers off; recharge in 4.9 |

There are no irreversible actions: the MAX77705 charger registers are volatile, and nothing touches the FG model, OTP, or the USB-C MCU firmware.

## 4. Staged test plan

1. **C0, read-only, no code.** Already done (q34/q48). Add one run on a **wall charger** to see SBoot's ILIM and CC for a DCP.
2. **C1a, dry run.** The supervisor runs and logs "would kick" decisions for 120 s; no writes.
3. **C1b, kick on PC USB.** Expect CHG_DTLS 0x1 (or 0x3/0x4) at 108 s instead of 0xB; current is still negative because of the input limit.
4. **C1c, kick on a wall charger, about 10 min,** with temperatures logged every 10 s. Pass = positive battery current at idle and T < 41 °C.
5. **C2,** only if C1 shows an expiry before probe: the one-shot CNFG_00 b0 restart.
6. **C3 / option C,** later: full parity port.

## 5. Owner decisions

| # | Decision | Recommendation |
|---|---|---|
| 1 | Allow a MAX77705 write path limited to the WDT kick (0x69/0xBD bits 1:0) | **Yes** |
| 2 | Kick vs disable the WDT | **Kick; never disable** |
| 3 | Supervisor thresholds (10-41 °C battery, < 44 °C charger NTC, ≤ 4.40 V) | **Yes**, stricter than 4.9 on purpose |
| 4 | Allow the one-shot charge restart after expiry (CNFG_00 b0) | Only if C1 shows it is needed |
| 5 | Raise the input limit ourselves (CNFG_09) | **No for now**; use a wall charger and let SBoot set it |
