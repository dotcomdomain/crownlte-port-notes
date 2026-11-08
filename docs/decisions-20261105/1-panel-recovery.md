# Decision 1: panel recovery for the "dark from SBoot" boots

2026-11-05. Research only: no code, builds or phone access.
`K49` = `/root/crownlte-hid-kernel` (the 4.9 kernel on the phone). `DT49` = `K49/arch/arm64/boot/dts/exynos/exynos9810-crownlte_eur_open_26.dts`.
`PNL` = `K49/drivers/video/fbdev/exynos/panel`. `DPU` = `K49/drivers/video/fbdev/exynos/dpu_9810`. `HA8` = `PNL/s6e3ha8`.
**GUESS** marks inferences that have not been verified.

## TL;DR

- **The premise needs a correction.** 4.9 would *not* recover this fault on its own. The fault happens before the kernel starts. 4.9 clears the disp-det pending bit before it requests the falling-edge IRQ, so it never sees that edge. If disp-det is already low at probe, 4.9 marks the panel DISCONNECTED and holds reset low, so the screen stays black until the next reboot (details below). On 4.9 the realistic recovery path is the **power-key screen off/on**. That runs the full panel power-off, power-on and init sequence.
- **A cheap, near read-only check can settle the hypothesis.** The DDI has an error-flag register, `0xEE` (ERR_FG); bit 3 means **"ELVDD Error"**. The S2DOS05 display PMIC latches short-detection, over-current and UVLO faults in register `0x11`, and its nIRQ line is ALIVE pin gpa1-4. Reading these in a black boot and in a bright boot should show whether the OLED supply tripped.
- **Recommendation.** First, a detect-only trial. Then, with the owner's approval, an escalating recovery that copies 4.9's own screen-on path:
  - **L1:** DCS-only sleep cycle.
  - **L2:** reset pulse plus the 4.9 init table.
  - **L3:** full S2DOS05 rail cycle, only if L2 fails.

## 1. What 4.9 does

### Hardware (DT49)

| Item | Value | Where |
|---|---|---|
| Panel | S6E3HA8, ID 91 20 47, tables `crown_a3_s0_default` | panel-brightness.md, DT49:13383 |
| Reset | `gpio,lcd-reset = <gpa3 4>` (ALIVE 0x14050000; gpa3 CON 0x14050080, DAT 0x14050084 bit 4) | DT49:13373, 6.12 `pinctrl-exynos-arm64.c:1086` |
| Disp-det | `gpio,disp-det = <gpg2 2>` (PERIC0 0x10430000; gpg2 CON 0x104300C0, **DAT 0x104300C4 bit 2**), IRQ falling edge | DT49:13374, 6.12 `pinctrl-exynos-arm64.c:1147`, PNL/panel_drv.c:2127 |
| `pend,disp-det` | reg 0x11050A04, bit 2. **4.9 quirk:** that address is FSYS0, not gpg2 (gpg2's pend would be 0x10430A18), and `clear_disp_det_pend` clears `~pend_bit` = bit 1. The pend clear is therefore ineffective. | DT49:13384, PNL/panel_drv.c:103-113 |
| Rails | `regulator,3p0`=vdd5 = S2DOS05 LDO4 3.0 V (EN reg 0x03 bit 3); `1p8`=vdd2 = LDO1 1.8 V (bit 0); `1p6`=vdd1 = BUCK1 1.5-1.6 V (bit 4) | DT49:13375-13377, DT49:7156-7197, `K49/include/linux/regulator/s2dos05.h:72-135` |
| S2DOS05 | HSI2C 0x14350000 (USI-I2C v2, clocks `<26 95 26 105>`), addr 0x60, nIRQ = gpa1-4 (ALIVE DAT 0x14050044 bit 4, active low). **There is no 6.12 DT node for this bus yet.** | DT49:7124-7151 |
| ELVSS short detect | `CONFIG_DISP_PMIC_SSD=y`: before every power-on, reg 0x0E bits 6:3 are set to SSD enabled at 2 mA (8 mA in doze) | PNL/panel_drv.c:119-145, `K49/drivers/regulator/s2dos05.c:271-291` |

### Power-on, init and power-off (`CONFIG_OLD_DISP_TIMING` is not set)

Screen on: `dsim_enable` → `_dsim_enable` (DPU/dsim_drv.c:761) runs these steps in order:

1. DPHY reset is asserted.
2. `panel_power_on` → `__set_panel_power(ON)` (PNL/panel_drv.c:119):
   1. SSD is set to 2 mA.
   2. The regulators are enabled in the order **vdd5 3.0 V → vdd2 1.8 V → vdd1 1.6 V** (panel_drv.h:84-92; enable_time 50/50/350 µs).
   3. Wait 10 ms, set **reset high**, wait 5 ms.
3. DSIM is software-reset, then the clocks, PLL and lanes are set up and the DPHY is released (`dsim_reg_init`), then `dsim_reg_start`.
4. `panel_sleep_out` (PNL/panel_drv.c:1265) → `__panel_seq_init` (PNL/panel_drv.c:236) sends `PANEL_INIT_SEQ` (HA8/s6e3ha8_crown_a3_s0_panel.h:1224):

| step | bytes | notes |
|---|---|---|
| wait 5 ms | | |
| KEY1 on | `9F A5 A5` | level-1 key |
| DSC | type 0x07 compression mode, `00` | needs the DSI DSC/PPS packet types |
| PPS | type 0x0A, 128 bytes (2960x1440, slices) | panel.h:682-700 |
| KEY1 off | `9F 5A 5A` | |
| SLEEP_OUT | `11` | then wait **10 ms** |
| KEY1 on, SET_AREA `1A 1F 00 00 00 00`, TE_ON `35 00`, KEY1 off | | |
| KEY2 on `F0 5A 5A`, ERR_FG `ED 4C`, KEY3 on `FC 5A 5A`, FFC `C5 0D 10 B4 3E 01`, KEY3 off `FC A5 A5` | | ED 4C routes VLIN1, ELVDD and VLOUT3 errors to the ERR_FG output |
| TSP_HSYNC `B9 01 B0 91 09 00 00 00 11 03`, ISC `F6 43`, EDGE_DIMMING `C2 09 00 D8 D8 F4 F4`, KEY2 off `F0 A5 A5` | | |
| `PANEL_SET_BL_SEQ` | brightness table (on 6.12 this is the 44700 sequence already in `panel_bright.c`) | |

5. disp-det is polled up to 200 × 100 µs (20 ms). If it is still low, the result is -EAGAIN, followed by power off, 100 ms and a retry, up to 30 times (PNL/panel_drv.c:262-285, 1290-1300).
6. Later, `panel_display_on` sends `9F A5 A5`, `29`, `9F 5A 5A` (panel.h:1353).

Screen off and reboot use the same steps in reverse: `dsim_disable` (dsim_drv.c:941), `dsim_shutdown` (:1753) and `decon_shutdown` (DPU/decon_core.c:4388).
1. Display off `28`.
2. Exit sequence: KEY1, SLEEP_IN `10`, KEY1 off, wait 100 ms (panel.h:1365).
3. DSIM stop, PHY off.
4. Reset low, then **vdd1 → vdd2 → vdd5 off**.

### Disp-det recovery at runtime

The IRQ handler (panel_drv.c:2107) queues `disp_det_handler` (:2393). If disp-det reads 0 in NORMAL/ALPM state, the handler waits 10 ms and calls `panel_error_cb` → `decon_error_cb` (DPU/decon_dsi.c:237). That callback performs a full DSIM OFF→ON cycle (the sequence above) up to 5 times and then calls `BUG()` (disp_err.h:49, decon_dsi.c:307-315).

**Why this cannot catch a pre-kernel fault:** `panel_drv_set_gpios` (panel_drv.c:1881-1950) decides the boot state as follows:
- reset=1, det=1 → state NORMAL, nothing is sent;
- reset=1, det=0 → **DISCONNECT, reset driven low**, and every later panel op is skipped.

The IRQ is only armed after the pend clear (:2125-2128), and it is falling-edge, so an edge that happened during SBoot is lost.

### SBoot

SBoot has `set_panel_power`, `reset_panel`, `s6e3ha8_panel_read_id`, "panel was not connected" and "retry dsim write command" (strings at raw 0x1359a0, 0x1359b0, 0x156d88, 0x1569d8 in `research/sboot/sdb-analysis.elf`). No disp-det/ESD string exists, so SBoot does not appear to detect or recover this fault. SBoot can't be changed (it is signed). Its ordering and timing could be extracted read-only in Ghidra (functions named above; about 0.5 day).

## 2. Can a read detect the fault safely?

Yes. Four probes, from safest to least safe:

1. **DDI registers over the existing DSIM read path.** Read `0x0A` RDDPM (already 0x9f), `0x0E` RDDSM (0x80 = good), `0x0F` self-diag (bit 7 = OTP loaded), `0x05` DSI error count, `0xEA` DSI error, and, behind `F0 5A 5A` (already sent by panel_bright), **`0xEE` ERR_FG: 0x08 = ELVDD error, 0x04 = VLOUT3, 0x40 = VLIN1** (HA8/s6e3ha8.c:2217-2440, s6e3ha8.h:117-137). Risk: none beyond what the brightness code already does. GUESS: ERR_FG may be cleared on read, so read it once and log it.
2. **ALIVE GPIO levels**:
   - gpa3-4 (reset, expect 1);
   - **gpa1-4 (S2DOS05 nIRQ, 0 = a fault is latched)**. The ALIVE block is always powered and is already probed in 6.12.
3. **disp-det gpg2-2** at PERIC0 0x104300C4. Read it only if `pinctrl@10430000` is bound, with its clock on, in that image. Otherwise a raw read can hit a gated bus (soft hang → Odin). Hypothesis (GUESS): disp-det mirrors the ERR_FG output configured by `ED 4C`, so a black boot should read 0.
4. **S2DOS05 registers** 0x00-0x10, then **0x11 (IRQ status: OCD b0, UVLO b1, SCP b2, SSD b3, TSD b4; probably clear-on-read)** last. This needs a new 6.12 DT node for hsi2c@14350000 and a read-only client, like `max77705-ro-battery`. Risk: soft (bus bring-up).

A black boot showing **ERR_FG bit 3 and/or S2DOS05 SSD/SCP/OCD**, with a bright boot showing none of them, confirms the "supply protection tripped" hypothesis. If everything reads clean, the fault is elsewhere (e.g. GRAM or timing), and recovery should not start with a rail cycle.

## 3. Proposed minimal 6.12 recovery (escalating, each level opt-in by DT)

The trigger at every level must be a positive detection from §2 (ERR_FG ELVDD, S2DOS05 latch or disp-det = 0), at most once per boot, and logged packet by packet like panel_bright.

- **L1, DCS only (no rails, no reset):**
  1. `28`, then `9F A5 A5`/`10`/`9F 5A 5A`, wait 120 ms.
  2. `9F A5 A5`/`11`/`9F 5A 5A`, wait 10 ms.
  3. TE_ON, the rest of the init table, the 44700 sequence, then `29`.

  This needs a narrow `dsim_cmd.c` allowlist extension for 0x10/0x11/0x28/0x29/0x35, which are refused today. GUESS: L1 helps only if the DDI restarts ELVDD at sleep-out.
- **L2, reset pulse:**
  1. With the DECON trigger masked: gpa3-4 low for 5 ms, then high, then wait 10 ms. This is the 4.9 `dsim_reset_panel` timing, dsim_drv.c:594-611.
  2. Send the **full** init table, including the DSC (type 0x07) and PPS (type 0x0A) packets. These need new DSIM packet types and must be byte-identical to panel.h:681-700.
  3. Send the 44700 sequence, `29`, then unmask the trigger.

  The DSIM link stays up (GUESS: the panel latches LP-11 at reset release, as in the OLD_DISP_TIMING path). The DECON DSC encoder is untouched, so the PPS must match what SBoot programmed; read the DECON DSC registers before L2.
- **L3, full 4.9 power cycle:** reset low → S2DOS05 EN off (vdd1, vdd2, vdd5) → 100 ms → SSD 0x0E = 2 mA → vdd5, vdd2, vdd1 on → 10 ms → reset high → 5 ms → DSIM reinit → init table. This needs an S2DOS05 write path (only EN reg 0x03 bits 0/3/4 and 0x0E bits 6:3), the DSIM PLL/DPHY init and DECON stop/start. That is 2-4 days of work and the riskiest level.

## 4. Risks

| Risk | Level | Reversible? | Protection | Worst case |
|---|---|---|---|---|
| Read probes | §2 | yes | DDI reads already work; ALIVE is always on; PERIC0 read only when bound; 0x11 read last | soft hang → Odin RECOVERY restore |
| Wrong DCS bytes / order | L1, L2 | DDI RAM only; power cycle clears it | byte-compare with the compiled 4.9 `.data` (as done for 44700); no MTP/OTP/POC commands (0xC8 writes, F1/EB POC) allowed | garbled image until reboot |
| PPS mismatch with DECON DSC | L2 | yes | read SBoot's DECON DSC config first; refuse if different | black or garbled panel until reboot |
| Re-triggering the protection | L1-L3 | yes, if it is a protection trip | at most once per boot; no retry loop (4.9 retries 30× / 5× then BUG) | dark panel, as today |
| **Wrong rail sequence or voltage** | L3 | **possibly not**, for an already-damaged OLED | never write vsel (LDO1/2/3/4_CFG, BUCK_VOUT); only toggle EN bits in 4.9 order with 4.9 delays; refuse if vsel ≠ 4.9 values read at entry | VCI/VDDI out of order → DDI latch-up or further panel damage |
| Disabling SSD to "make it work" | any | — | **never**: SSD protects against the short that may be damaging the panel | burn/heat in the panel |

All rails are at fixed voltages and L1/L2 never touch them. Screen off/on (L3) is what 4.9 does every time the screen turns off and on, so with exact replication the electrical risk is no larger than daily 4.9 use.

## 5. Staged test plan

1. **D0, read-only, no code.** Ask the owner whether 4.9 ever boots black and whether power-key off/on fixes it. If it does, the full cycle (L3) is known to recover the panel.
2. **D1, read-only trial.** Add the §2 probes 1, 2 and 4 to the ROM-RAM image. Run about 4 boots with video and correlate black versus bright boots.
3. **D2, L1 only,** armed by DT and fired only on a positive detection. Video.
4. **D3, L2** (needs the DSC/PPS packet support and a DECON DSC read-back).
5. **D4, L3,** only if D3 fails and D1 showed an S2DOS05 latch.

## 6. Owner decisions

| # | Decision | Recommendation |
|---|---|---|
| 1 | Allow D1 (reads only, including the DDI `F0` key, ALIVE GPIO reads and an S2DOS05 read-only I2C client) | **Yes** |
| 2 | Lift "no panel power/init" for **L1 and L2** (DCS sleep cycle and reset pulse plus the exact 4.9 init table; no rails) after D1 shows a fault signature | **Yes**, one attempt per boot |
| 3 | Allow **L3** (S2DOS05 EN toggles in 4.9 order; no vsel writes) | Defer until D3 fails |
| 4 | Spend about 0.5 day on read-only Ghidra RE of SBoot's `set_panel_power`/`reset_panel` | Optional; only if D1 is ambiguous |
| 5 | Never disable SSD or raise its threshold | Confirm as a hard limit |
