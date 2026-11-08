# crownlte 6.12: suspend stage S1, the first real SoC sleep

2026-11-07. Research and design only: nothing was built, flashed or run, and the phone was not touched.

Builds on gekko `roadmap/suspend-design.md` (here "D") and `Documentation/crownlte/suspend-s0.md` on `crownlte-suspend-s0` 60774ac87 (here "S0"). S0a-S0d passed on hardware on 2026-11-07 (pack 416fb7f3).

Sources:
- K49 = gekko `/mnt/ssd/host-builds/kernel-upgrade/src/close-range-fix-4.9` (c987368f). Paths below are relative to it.
- DT49 = `arch/arm64/boot/dts/exynos/exynos9810-crownlte_eur_open_26.dts` in K49.
- T = 6.12 branch `crownlte-suspend-s0` 60774ac87.
- APMBIN = gekko `next-research/apm.bin`, the 128 KB window of the UFS boot LUN that holds the APM firmware.
- S0R3 = the S0 r3 hardware logs: `C:\Users\Patrick\tools\trial-acpm-suspend-s0-r3-20261107\{serial-ready.bin,first-return-pmsg.bin}`.

Confidence tags:
- **[src]**: read in source.
- **[hw]**: seen on 6.12 hardware.
- **[apm]**: read in a firmware binary.
- **[inf]**: inferred; a trial must confirm it.

---

## 0. Summary

1. **Recommended first wake source: the SoC's own RTC alarm. It needs no PMIC write and no owner go under the PMIC rule.**
   - The source is the AP RTC at `0x141E0000` (DT49:9431, `samsung,exynos8-rtc`, SPI 31 alarm / SPI 32 tick). It sits in the ALIVE/APM block and is wired to PMU WAKEUP bit 1 (alarm) / bit 2 (tick).
   - 4.9 enables exactly these two bits as wake sources in SICD (DT49 wakeup-mask column 0 = `0x400001E0`). 4.9 masks them in SYS_SLEEP only because Android uses the PMIC RTC [src].
   - Footprint:
     - arm: `ALMSEC` and `RTCALM = 0x41` (seconds-only match), possibly a `RTCCON.RTCEN` toggle;
     - PMU `0x610 = 0xD00F7E7C` instead of `0xD00F7E7E` (bit 1 cleared);
     - disarm: `RTCALM = 0` and `INTP = 0x2` (write 1 to clear).
     All of these are SoC MMIO.
   - What still needs proof is listed in §3.1:
     - the block is non-secure (4.9 never touches it);
     - its 32 kHz clock runs (PMIC `RTCBUF` = 0x45 [hw]: buffer bits on);
     - the alarm reaches the PMU in SLEEP.
     S1a and S1b prove the first two, and the alarm logic itself, without sleeping.
2. **The PMIC RTC alarm (the production 4.9 path) needs an owner go (D1).** Its writes:
   - INT2M `0x08`: `0xFF → 0xFB → 0xFF`;
   - alarm 0 `0x0C-0x12`, written twice;
   - UPDATE `0x02` AUDR strobes.
   It also needs the whole S2MPS18 IRQ → gpa0-1 EINT chain, which 6.12 does not have. It moves to S1f.
3. **Nothing resets the SoC if it sleeps and never wakes.**
   - In real SLEEP the CL0 watchdog has neither power nor clock. The APM SoC sequencer takes TOP_BUS down (PMU `0x2c00`), asserts LOGIC_RESET (`0x2d00`) and OSCCLK_SLEEP (`0x2d60`). The MIF sequencer turns TCXO off. The WDT is in PERIS, which is in TOP.
   - There is no APM watchdog the kernel can see: no 4.9 ACPM code, no APM strings.
   - The only resets left are the owner's force restart (Power + Vol Down) or a flat battery.
   - The WDT does cover a hang **while TOP still runs**: entry up to the SMC, an APM stall before TOP_BUS goes off (PMU `AUTOMATIC_WDT_RESET_DISABLE` bit 24 = 0 [hw]), and resume after EL3 returns.
   - The 60 s kept-armed timeout is enough for those windows if the alarm is ≤ 40 s.
4. **New hazard found: the watchdog may come back from SLEEP enabled with a ~2.6 s timeout.**
   - The bootloader leaves `WTCON = 0x8021`, `WTDAT = 0x8000` [hw S0R3]. These are the reset values: enabled, reset on, prescaler 128, /16 at 26 MHz → 2.6 s.
   - The PMU does not mask the reset (bit 24 = 0 [hw]).
   - If PERIS is reset in SLEEP, a resume that reaches the watchdog's device-resume later than ~2.6 s after wake gets reset.
   - 4.9 re-arms the CL0 WDT in **syscore** resume (`drivers/watchdog/s3c2410_wdt.c:1051-1086`). S1 must do the same, right after `cpu_suspend()` returns.
5. **Before the first SMC, S1 needs 4.9 parity in five places.** Each is small; each is ranked in §2.
   - the real PSCI finisher;
   - armed `cal_pm_enter(8)`;
   - MCT FRC restart before syscore resume;
   - the early CL0 WDT re-arm;
   - TMU `AP_SUSPEND`/`AP_RESUME` IPC;
   - DECON stop.
   S1 does not need:
   - pinctrl retention data: CAL `exit_sleep` releases PAD_RETENTION, proven in S0c;
   - the 0x60C EINT mask (EINT wake is not used);
   - PMIC IRQs;
   - UFS (stays unbound);
   - the modem: CP_STAT reads `0x1` [hw], so even 4.9's CP gate would pass.
6. **Staged plan.**
   - S1a: read-only probe.
   - S1b: alarm fires while awake.
   - S1c: real SMC with a pending self-IPI **and** a 12 s RTC alarm as backup. The result is either an early return or a short real sleep.
   - S1d: one real 10 s sleep.
   - S1e: 5 cycles plus a 35 s sleep.
   - S1f (owner D1): the PMIC RTC route.
   S1a/S1b are self-recovering. S1c-S1e carry one residual risk that only a human can clear (§4.4): see owner item O1 in §7.

---

## 1. The 4.9 real-suspend sequence on 9810 (Q1)

D §1 has the full table. This is the condensed order, with the items D did not have marked **new**.

### 1.1 Kernel, entry

| # | Step | K49 |
|---|---|---|
| 1 | `pm_suspend(mem)` → `PM_SUSPEND_PREPARE`: Kinibi `sleep_req` (not in the S1 vehicle) | `drivers/gud/gud-exynos9810/MobiCoreDriver/main.c:282-317` |
| 2 | Device suspend: <ul><li>TMU: `exynos_tmu_control(false)` per zone, then on the last zone `exynos_acpm_tmu_set_suspend(cp_net)` = ACPM IPC type `0x04` (rsvd = CP network flag, 0 with no modem)</li><li>DECON: `decon_disable()` → `decon_reg_stop()` = per-frame off, update request, wait RUN off (1/fps + 20 % + 5 ms), instant-off fallback, `decon_reg_reset()`. On 4.9 this has always run before sleep, because the screen is off.</li><li>UFS: SSU + link off</li><li>dwc3</li></ul> | `drivers/thermal/samsung/exynos_tmu.c:1670-1699`, `exynos_acpm_tmu.c:156-190`; `drivers/video/fbdev/exynos/dpu_9810/decon_reg.c:1911-1990`, `decon_core.c:858` |
| 3 | Nonboot CPUs off: PSCI CPU_OFF `0x84000002`; dying-CPU `cal_cpu_disable` / `cal_cluster_disable` (already ported, S0b) | `exynos-powermode.c:708-721` |
| 4 | syscore suspend, in reverse registration order:<ul><li>**(new)** the CL0 WDT syscore: `keepalive`, then save WTCON/WTDAT. The WDT is **not** stopped.</li><li>`exynos_pm_syscore_suspend`: refuse if `CP_STAT (0x14060038) == 0`; mode 8 (12 if a USB host is connected); IDLE_IP masks; wakeup masks `0x60c` (pinctrl) / `0x610 = 0xD00F7E7E` / `0x614 = 0x618 = 0xFFFF00FF` / `0x644 = 0`; `cal_pm_enter(8)` = INFORM hint 4, `save_sleep` (~1100 registers), `enter_sleep` incl. **INTR_GEN `0x14070200` bit 4 (CPU0 GRP2 = APM hand-off)**</li><li>`cpu_pm` (GIC save)</li></ul> | `drivers/watchdog/s3c2410_wdt.c:1036-1049`; `drivers/soc/samsung/exynos-pm.c:289-316`; `exynos-pmu.c:172-180` |
| 5 | `exynos_pm_enter`: optional self-IPI (debugfs `test_early_wakeup`), MIF counter before, `arm_cpuidle_suspend(133)` → customized finisher → **SMC `0xC4000001`, x1 = `0x03000000`, x2 = `__pa(cpu_resume)`** | `exynos-pm.c:336-384`; `drivers/firmware/psci.c:446-495` |

### 1.2 EL3 (encrypted; inferred from the contract) [inf]

1. EL3 records x2 as CPU0's warm entry.
2. It saves its own state (secure GIC group 0, ...).
3. It executes WFI. The PMU sees CPU0 STANDBYWFI with `CPU_INFORM0 = 4` and GRP2 bit 4 enabled, and raises the CPU0 core-down event to the APM.

If an interrupt is already pending, WFI falls through and CPU_SUSPEND returns nonzero. The kernel then runs `cal_pm_earlywakeup`.

On wake, CPU0 restarts through the boot ROM / warm-boot path. EL3 finds its record and ERETs to EL1 at `cpu_resume` with the MMU off. `cpu_suspend()` returns 0.

**(new) [apm, low confidence]** APMBIN `0x1000-0x1118` is not APM Thumb code. It is AArch64 boot code that sets VBAR by CurrentEL. Its three helpers:
- read `RST_STAT 0x14060404` and test bit 28;
- compare a `0xBABA` magic at `0x020414D4`;
- set **MCT G_TCON `0x10040240` bit 8**, which starts the FRC.

So some boot stage starts the MCT FRC. Whether that stage also runs on a SLEEP wake is unknown. The kernel must check and restart the FRC itself, as 4.9 does (`drivers/clocksource/exynos_mct.c` `exynos4_frc_resume`).

### 1.3 APM (ACPM FlexPMU) and the MIF/APM handshake

The firmware copy of the sequences is at `drivers/soc/samsung/cal-if/exynos9810/flexpmu_cal_system_exynos9810.h:2565-3046` (`#else` of `ACPM_FRAMEWORK`). It is not executed by Linux.

1. **Hand-off.** CPU0's core-down event reaches the APM through INTR_GEN GRP2. The APM dispatcher reads `CPU_INFORM0` low 16 bits, maps 4 → SLEEP (tag `ente_pm+`, APM `0x109e4`) and checks for a pending wake (`early_pm`/`upend_wk`/`EarlyRet`).
2. **`soc_sequencer_sleep_down`** (:2641-2681):
   1. GRP1 masked;
   2. FSYS0 (UFS/USB) off, CPUCL1 off;
   3. TOP_BUS `0x2c00` 6 → 0;
   4. CMU_TOP PLLs off;
   5. HOSTIRAM retention;
   6. PAD_RETENTION TOP/SPI/BOOTLDO0/1/PCIE/UART = 0;
   7. LOGIC_RESET `0x2d00` = 0, TOP clamps;
   8. **OSCCLK_SLEEP `0x2d60` = 0**;
   9. **EXT_REGULATOR `0x3600` = 0**: PWREN low, so the PMIC applies its rail sleep modes;
   10. big BISR/OTP retention;
   11. **GRP4 enable**: the wakeup event reaches the APM.
3. **`mif_sequencer_sleep_down`** (:2825-2851) runs only when every MIF requester has released:
   - APM/CMGP/VTS/CHUB bus mux to OSC;
   - DDRPHY stop;
   - LPDDR4 pad retention (DRAM self-refresh);
   - EXT_REGULATOR_MIF off;
   - **TCXO off** `0x140682e0`.
   Otherwise it logs "MIF blocked": the SoC is asleep and the MIF stays up. The CP is one requester. Here the CP is powered (`CP_STAT = 0x1`, `SEQ_CP status = 0` [hw]) but never booted, so **"MIF blocked by CP" is a plausible S1 outcome and is not a failure**.
4. **Wake.**
   1. The PMU sees an unmasked WAKEUP source and raises GRP4 to the APM.
   2. MIF up (TCXO, regulator, DDRPHY, retention release).
   3. `soc_sequencer_sleep_up` (:2683-2714): clears WAKEUP_MASK bit 31; regulators and OSC back; resets and clamps released; **only PAD_RETENTION_UART released**; PLLs on; TOP_BUS, CPUCL1, FSYS0 on.
   4. CPU0 powers up into the warm boot.
5. **Counters.** FlexPMU debug SRAM `0x0204F800`: line 22 = AP sleep count, line 23 = MIF sleep count, line 10 bits 23:16 = MIF requester mask (`drivers/soc/samsung/exynos-flexpmu-dbg.c:60-142`). The APM log ring holds the tags.

### 1.4 Kernel, resume (4.9)

1. `cpu_suspend()` returns 0. `exynos_pm_enter` logs the MIF count delta ("MIF down" / "MIF blocked") and reads `FLEXPMU_INFORM 0x14060880`.
2. **(new) CL0 WDT syscore resume:**
   - PMU `0x408` bit 24 = 0 (automatic disable off);
   - WTDAT, WTCNT, WTCON = the saved values;
   - PMU `0x40c` bit 24 = 0 (reset not masked).
   (`s3c2410_wdt.c:1051-1086`, `:328-367`.)
3. `cpu_pm` resume (GIC restore).
4. `exynos_pm_syscore_resume` → `cal_pm_exit(8)`: GRP2 off; DRCG; **PAD_RETENTION release** of TOP/SPI/BOOTLDO0/1/PCIE/UART; `save_sleep` restore. Then the wake reason (`0x600`, `0x640`, EINT_PEND, GICD_ISPENDR).
5. CPUs on.
6. Devices resume. TMU sends `AP_RESUME` (0x10); DECON stays off until unblank.

### 1.5 What else 4.9 does around suspend, and what S1 needs

| Item | 4.9 | 6.12 (T) | Needed for a safe first S1? |
|---|---|---|---|
| ACPM "sleep IPC" | None beyond TMU: no ACPM PM hooks in `drivers/soc/samsung/acpm/` [src] | same | Only TMU (next row) |
| TMU suspend IPC | `AP_SUSPEND 0x04` / `AP_RESUME 0x10` on the TMU channel | INIT/READ_TEMP only | **Yes.** **[inf]** An APM TMU plugin that keeps polling the TMU (PERIS, TOP) after TOP_BUS is off could stall the APM, and with it the wake path. |
| PMIC sleep states | APM drops PWREN; the PMIC uses per-rail CTRL bits 7:6 (mode 1 = on only with PWREN, 2 = with PWREN_MIF, 3 = always). 4.9 writes the opmode **only when it enables a rail that was off**, so the boot-on core rails keep their SBoot modes in 4.9 too [src `drivers/regulator/s2mps18.c:113-163`]. 4.9 also writes `RTCBUF |= 0x04`, B1-B4 `OUT2 = 0x20`, `PWREN_SEL2 = 0xFF` (:1269-1279). | No PMIC writes. `RTCBUF` already reads 0x45 (bit 2 set) [hw]. | **Read-only compare first** (S1a): `0x1E/0x21/0x24/0x27` (B1-4 OUT2) and `0xC8/0xC9` (PWREN_SEL1/2) on 6.12 vs. live 4.9. A difference is not a blocker for one 10 s sleep. It only decides which rails switch off; it does not affect DRAM retention. |
| GIC save/restore | `cpu_pm` cluster notifier | Same upstream code (`irq-gic.c` CPU_CLUSTER_PM_ENTER/EXIT, `kernel/cpu_pm.c` syscore) | Already there. Log GICD_ISPENDR around the sleep. |
| MCT | `exynos4_frc_resume` restarts the FRC | mct-bc starts the FRC only at probe | **Yes**: restart in `.enter` right after `cpu_suspend()` returns, before syscore resume (`timekeeping_resume` is a syscore op registered before ours) |
| CL0 WDT | Kept running; re-armed in syscore resume | Kept armed (60 s); re-armed only in device resume | **Yes**: early re-arm (§0.4) |
| Pinctrl retention | `cal_pm_exit` releases PAD_RET; pinctrl writes 0x60c | Warns "No retention data configured bank ... Wake-up mask will not be set" for 14050000 (ALIVE) and 14220000 (CMGP) [hw S0R3]. Benign: 0x60c stays at its boot value 0x0. | **No.** CAL `exit_sleep` releases PAD_RET (S0c PASS). The retention data port belongs to S2 (power-key EINT). |
| PAD_RET release on resume | `exit_sleep` | Same tables, proven in S0c | Yes, already there. Add a read of PAD_RET **before** `cal_pm_exit`: 0 there is the proof that the SoC sequencer ran. |
| UFS | SSU + link off; FSYS0 off | Vehicle never binds UFS | No (keep it unbound). S1-UFS later needs D3. |
| USB | Mode 8 with a gadget = PHY off; host sees a disconnect | dwc3 suspend/resume proven in S0c, **not across a real FSYS0 power-off** | Not a gate. Serial drops and returns; pstore holds the log. |
| Display | DECON stopped before sleep (screen off) | DECON-F keeps scanning the SBoot framebuffer (`decon_handover`, no system PM) | **Yes**: an active DPU DMA master at TOP_BUS/MIF power-down is the main bus-stall risk (D R6). Port the 4.9 `decon_reg_stop` (per-frame, then instant). |
| Modem / CP | Refuse when `CP_STAT == 0`. CP_CALL mode 10. TMU flag = CP net. | `CP_STAT = 0x1` [hw]: the 4.9 gate passes | No. Expect "MIF blocked" (CP requester); log SW_FLAG. Keep `samsung,sleep-with-cp-off` as a harmless override. |
| Mali, touch, Wi-Fi, BT, TEE | Own PM | Not bound in the vehicle | No |

---

## 2. Code changes S1 needs (ranked)

All of them go on top of `crownlte-suspend-s0` 60774ac87. All are DT-gated (`samsung,suspend-s1`). Without that property the S0 behaviour is unchanged.

| # | Change | Where | 4.9 basis |
|---|---|---|---|
| C1 | **Real finisher.** `e9810_sleep_finisher()` returns `psci_ops.cpu_suspend(e9pm.psci_state, __pa_symbol(cpu_resume))`. Assert before the call: the DT 0.1 fn id is `0xc4000001` (`get_psci_0_1_function_ids()`), state `0x03000000`, entry ≠ 0, `num_online_cpus() == 1`, IRQs off. **Never call `psci_cpu_suspend_enter()`**: it passes entry 0 (D §3.1). | `drivers/soc/samsung/exynos9810-pm.c` | `drivers/firmware/psci.c:446-495` |
| C2 | **Armed entry.** syscore uses `cal_pm_enter(8)` (GRP2 bit 4 written) in S1c-S1e only. It checks that GRP2 en bit 4 = 1 and `INFORM0 = 4` before returning 0. S1a/S1b keep `cal_pm_enter_unarmed`. | same | `cal-if/pmucal_system.c:15-50` |
| C3 | **Post-return block in `.enter`**, before anything else and with IRQs off, in this order: <ol><li>read CNTPCT, MCT FRC, G_TCON;</li><li>FRC restart if G_TCON bit 8 = 0 or the FRC does not move in 2 reads;</li><li>WDT early re-arm (C4);</li><li>AP RTC disarm + `INTP` clear;</li><li>reads of PAD_RET ×6, WAKEUP_STAT `0x600/0x640`, RST_STAT `0x404`, WDT PMU `0x408/0x40c`, FlexPMU lines 10/22/23, `FLEXPMU_INFORM`, GICD_ISPENDR0..15.</li></ol>Store everything; print only after syscore resume. | same + `exynos9810-mct-bc.c` export `e9810_mct_frc_resume()` | `exynos_mct.c` `exynos4_frc_resume`; `exynos-pm.c:367-384` |
| C4 | **WDT early re-arm.** `s3c2410wdt_crownlte_resume_early()`: <ul><li>log WTCON/WTDAT/WTCNT and PMU `0x408/0x40c` as found (this shows whether PERIS was reset);</li><li>then the 4.9 syscore resume: PMU `0x408` bit 24 = 0, WTDAT = WTCNT = the kept-armed 60 s count, WTCON = the saved kept-armed value, `0x40c` bit 24 = 0.</li></ul>It is a reload, so it obeys the crownlte deadline and stop-petting rules. Called from C3, single CPU, IRQs off. | `drivers/watchdog/s3c2410_wdt.c`, `include/soc/samsung/crownlte-wdt.h` | `s3c2410_wdt.c:1051-1086` |
| C5 | **AP RTC alarm helper**: map `0x141E0000` size `0x100` (new DT `reg-names = "ap-rtc"` in the `system_suspend` node; no `rtc-s3c` node, no RTC class device, never sets time). Functions: `aprtc_snapshot`, `aprtc_arm_sec(delta)`, `aprtc_disarm`. Exact sequence in §3.1. | `exynos9810-pm.c` | Upstream T `drivers/rtc/rtc-s3c.c` register layout (`rtc-s3c.h:13-65`); K49 `rtc-s3c.c:806-816` (exynos8 variant) |
| C6 | **S1 wake mask.** DT `samsung,wakeup-masks` for S1 = `0xd00f7e7c 0xffff00ff 0xffff00ff 0x0` (bit 1 = RTC alarm cleared). Add bit 1 to the allowed-difference list in the mask check. 0x60c is not written (boot value 0x0; no EINT is configured as a wake source). | DT + driver check | DT49:4912-4935 (column 0 clears bits 1-2) |
| C7 | **TMU suspend IPC**: `AP_SUSPEND` (0x04, rsvd 0) when the last zone suspends, `AP_RESUME` (0x10) on the first resume. Response required. Logs the 4 reply words. | `drivers/thermal/samsung/exynos9810_acpm_tmu.c` | `exynos_acpm_tmu.c:156-260`, `exynos_tmu.c:1670-1699` |
| C8 | **DECON stop** at system suspend (`.suspend` of `decon_handover`, DT `samsung,stop-on-suspend`):<ul><li>4.9 `decon_reg_stop_perframe` (trigger disable if command mode + HW trigger, per-frame off, global update request, wait RUN_STATUS off, timeout 1/fps + 20 % + 5 ms);</li><li>`decon_reg_stop_inst` on timeout;</li><li>no reset; never restarts on resume.</li></ul>The pm driver refuses S1c-S1e (`step 20 refused: DECON running`) unless the handover reports it stopped. | `drivers/video/fbdev/exynos/exynos9810-decon-handover.c` | `decon_reg.c:1911-1990` |
| C9 | **S1 stage runner** (reuses the S0 deadline, recovery gate and xcall). Stages a-e from DT, one per boot window. Each real-sleep stage first checks: <ul><li>all S0 gates;</li><li>AP RTC armed and read back;</li><li>DECON stopped;</li><li>TMU ack;</li><li>`step 20` armed.</li></ul> | `exynos9810-pm.c` | — |
| C10 | **Diagnostics only:** a bigger pstore console (DT ramoops `console-size` 64 KB if the reserved region allows), so one S1 cycle fits in the tail | DT | — |

Not in S1: pinctrl retention data, 0x60C writes, the PMIC IRQ domain, MAX77705 IRQs, UFS SSU, Mali system PM, panel unblank.

---

## 3. Wake sources (Q2)

### 3.0 What the PMU can use in SYS_SLEEP (mode 8)

| Source | Wake path | Footprint | Unattended? | Owner go? |
|---|---|---|---|---|
| **AP RTC alarm** (`0x141E0000`, SPI 31) | PMU WAKEUP bit 1 (masked in 4.9 SLEEP, enabled in 4.9 SICD) | SoC MMIO only: ≤ 4 RTC writes + 1 PMU mask value | **Yes** | **No** (no PMIC write). It is new MMIO outside 4.9's footprint: Sol review + S1a/S1b proof. |
| AP RTC tick (SPI 32) | WAKEUP bit 2 | `TICCNT` + `RTCCON.TICEN` (writes the shared control register) | Yes | No. Fallback only if the alarm cannot be written. |
| **PMIC RTC alarm 0** (S2MPS18 RTCA0) | RTCA0 → INT2 bit 2 → PMIC IRQ pin → gpa0-1 EINT → PMU `0x60c` bit 1 → WAKEUP bit 0 | 4+ PMIC writes + an IRQ demux driver + EINT pin setup | Yes | **Yes (D1)** |
| APM firmware timer | Unknown. APMBIN has `systick`/`timer` strings, but 4.9 has no interface to ask the APM for a timed wake; WAKEUP bits 7/8/15/27 are internal and unexplained. | Firmware RE | — | Not usable |
| USB VBUS / cable | MAX77705 IRQ gpa1-6 (EINT), or USB PHY WAKEUP bit 17 only in mode 12 (SLEEP_USBL2, 4.9 `0x610 = 0xD00D7E7E`) | MAX77705 interrupt-mask writes; or the USBL2 tables + a host-driven resume | Only with a switchable hub or a human | **Yes (D2)** for MAX77705. USBL2 needs no PMIC write but is S3 work. |
| Power key | gpa2-4 EINT → `0x60c` bit 20 → WAKEUP bit 0 | pinctrl EINT setup + retention data + gpio-keys DT (SoC only) | **No**: a human | No. That is S2. |
| CP / modem | Mailbox SPIs 69-75, 440-442 (WAKEUP bits 20-26, 29, enabled in SLEEP) | Modem driver + booted CP | — | Not usable (CP never boots) |
| MCT (bit 14), PCIe, dwmmc | Masked in SLEEP; MCT is in TOP (off) | — | — | No |
| CHUB / VTS internal | Bits 7/8/15/27 enabled | Their firmware is not loaded on 6.12 | — | No |
| CL0 watchdog | Not a wake source. Unpowered in SLEEP (§4). | — | — | — |

**Smallest new-write footprint: the AP RTC seconds alarm.** No PMIC, charger or UFS command. It is the only unattended source that needs no owner go.

### 3.1 AP RTC: exact registers (base `0x141E0000`)

Layout from upstream `rtc-s3c.h`:

| Offset | Name |
|---|---|
| `0x30` | INTP (bit 1 ALM, bit 0 TIC; write 1 to clear) |
| `0x40` | RTCCON (bit 0 RTCEN, bit 3 CLKRST, bit 8 TICEN) |
| `0x44` | TICCNT |
| `0x50` | RTCALM (bit 6 ALMEN, bit 0 SECEN) |
| `0x54..0x68` | ALMSEC..ALMYEAR |
| `0x70..0x88` | BCDSEC..BCDYEAR |
| `0x90` | CURTICCNT |

Read-only proof, S1a:
1. Read all of the above twice, 2.1 s apart.
2. Read CMU_APM `QCH_CON_APBIF_RTC` `0x14003028`. 4.9 reads it in the `save_sleep` list (`flexpmu_cal_system_exynos9810.h:1154`), and S0c already read it.
3. PASS needs all of:
   - no abort or stall;
   - BCDSEC valid BCD and advanced by 2 (mod 60);
   - RTCCON.CLKRST = 0;
   - **RTCALM.ALMEN = 0** (nobody else uses the alarm);
   - INTP = 0.
4. If ALMEN is already set, or TICEN is set, something else owns the block: S1b refuses.

Arm (`aprtc_arm_sec(d)`, d ≤ 50):
1. If INTP bit 1 is set: write `INTP = 0x2`.
2. `s = bcd2bin(BCDSEC)`; `ALMSEC = bin2bcd((s + d) % 60)`. Do it only when s ≤ 57, otherwise wait for the next second, so a carry cannot race the write.
3. `RTCALM = 0x41` (ALMEN | SECEN; the seconds field alone must match).
4. Read back ALMSEC and RTCALM.
5. If a readback is wrong, set `RTCCON |= RTCEN`, write again, restore RTCCON, and log `rtcen-needed=1`.
   - Writes on S3C RTCs may be gated by RTCEN: upstream sets it at probe (`s3c24xx_rtc_enable`).

Disarm: `RTCALM = 0` (same RTCEN rule), then `INTP = 0x2`.

**The alarm repeats every minute while enabled, so the post-return block disarms it first.**

Evidence for the wake path:
- WAKEUP_STAT bit 1 ↔ SPI 31 (DT49 `wakeup_stat_irqno` index 1 = 31, rtc node `interrupts = <0 31 0 0 32 0>`).
- SICD mask `0x400001E0` keeps bits 1-2 enabled.
- `exynos_rtc_wakeup()` (`exynos-powermode.c:794-805`) checks exactly these two bits.
- 32 kHz in the SoC: CAL has `RTCCLK_VTS` fixed at 32767 Hz (`cmucal-node.c:1793`), VTS needs it in sleep, and PMIC RTCBUF = 0x45 [hw].
- **Still [inf]:** that the AP RTC counts and is non-secure. S1a settles both. Also that SLEEP honours bit 1. Only S1c/S1d settle that.

### 3.2 PMIC RTC: exact writes (owner go D1, S1f only)

PMIC bank (ACPM `TYPE_PMIC`):
1. Read INT1..INT7 `0x00-0x06` to clear stale bits. A stale RTCA0 is possible: alarm 0 is still enabled for 2026-11-06 22:57:08 from Android, `A0 = 88 b9 d6 a0 86 8b 9a` [hw S0R3].
2. **INT2M `0x08`: `0xFF` → `0xFB`** (unmask bit 2 = RTCA0; `s2mps18_irq.c:70`).
3. After the test: back to `0xFF`.

TOP INTSRC_MASK `0x02` already reads 0x06 (PMIC source unmasked): no write.

RTC bank (ACPM `TYPE_RTC`; the T driver already has these under `samsung,rtc-write-enable`, 4.9 `s2m_rtc_set_alarm`):
1. A0SEC..A0YEAR `0x0C-0x12` = target time, enable bits clear.
2. UPDATE `0x02` = `update_base (0x00) | AUDR 0x10`, wait 1-2 ms.
3. The same 7 bytes with bit 7 set.
4. AUDR strobe again.
5. Disarm: the same with bit 7 clear.

Reading the time also needs the RUDR strobe (`0x02 = 0x01`), which is already approved as `rtc-read-latch`. Never touch alarm 1 (`0x13-0x19`), WTSR_SMPL, CTRL, CAPSEL or OSCCTRL.

SoC side, no owner go:
- gpa0-1 as EINT (con 0xF), level-low, filter;
- unmask GPIO_ALIVE EINT mask bit;
- `0x60c` bit 1 = 0;
- `0x610` bit 0 = 0 (already in the 4.9 value);
- a threaded IRQ handler that reads INT1..INT7 over ACPM ch 2.

### 3.3 Recommendation

Use the AP RTC alarm for S1a-S1e (no owner go). Add the PMIC RTC in S1f once the owner approves D1. From S1f on, keep the AP RTC armed as a backup alarm a few seconds behind the PMIC one, in every sleep test.

---

## 4. Recovery for an unattended phone (Q3)

### 4.1 Is the CL0 watchdog counting during SoC sleep?

**No (strong inference).**
- The CL0 WDT (`0x10050000`) is a PERIS block; its Q-channel lives in CMU_PERIS `0x10020000` (`flexpmu_cal_system_exynos9810.h:283,293`).
- In SLEEP, the APM SoC sequence turns TOP_BUS off (`0x2c00 = 0`), asserts LOGIC_RESET (`0x2d00 = 0`) and `OSCCLK_SLEEP` (`0x2d60 = 0`). The MIF sequence switches TCXO off.
- The WDT clock is `26 MHz / 2048` [hw S0R3: "802 ticks in 64000 us"], i.e. OSC-derived.
- 4.9 rewrites WTDAT, WTCNT and WTCON in syscore resume. That only makes sense if the state can be lost.

**Two consequences:**
1. A "slept and never woke" SoC is not reset by the WDT.
2. **New hazard:** if PERIS comes back at reset values, the WDT runs at the bootloader/reset defaults.
   - `WTCON = 0x8021` (enabled, reset on), `WTDAT = WTCNT = 0x8000`: about 2.6 s at 26 MHz / 129 / 16.
   - The PMU does not mask it (`AUTOMATIC_WDT_RESET_DISABLE = MASK_WDT_RESET_REQUEST = 0xfeffffff`, bit 24 = 0 [hw S0R3]).
   - The 6.12 re-arm runs only in device resume, after CPU hotplug-in. C4 closes this gap the way 4.9 does.
   - If C4 is missing and resume is slow, the signature is a WDT reset a few seconds after wake (RST_STAT WDT bit, pstore tail ends in resume). That is safe, but it destroys the result.

### 4.2 If the SoC never wakes, what resets it?

| Failure | Reset by |
|---|---|
| Hang before the SMC (devices, noirq, hotplug, syscore) | Kept-armed WDT, 60 s → SBoot → shim bounce (proven: q35/q37/S0) |
| EL3 or APM stalls **while TOP still runs** (before TOP_BUS off / OSCCLK_SLEEP) | WDT keeps counting: PMU auto-disable bit 24 = 0 [hw], unless EL3/APM set it at entry. That is unknown; C3 logs `0x408` after resume to show it. |
| Real SLEEP reached, wake source never fires (AP RTC not counting, bit 1 ignored, alarm missed) | **Nothing.** The owner's force restart (Power + Vol Down ~7-10 s, PMIC manual reset), or the battery. |
| Wake fires but MIF-up / SoC-up / EL3 warm boot hangs with TOP still off | **Nothing**, as above |
| Wake OK, kernel resume hangs | C4 re-arms the WDT within microseconds of the return → 60 s → reset. Without C4: the 2.6 s default WDT, if PERIS was reset. |

- **No APM-side watchdog the kernel can rely on.** K49 `drivers/soc/samsung/acpm/` has no watchdog code. APMBIN strings have no wdt/watch tag. `QCH_CON_WDT_APM` (`0x140030ac`) exists in the save list, so the APM has a WDT block. Whether the firmware arms it in sleep is unknown, and its clock would also stop with TCXO.
- **PMIC:** no autonomous timer reset. WTSR is triggered by the SoC's watchdog reset output. RTC alarm 1 only powers on from OFF.

### 4.3 Is the 60 s kept-armed timeout enough?

Yes, for every window in which the WDT can count:
- entry from device suspend to the SMC: < 2 s (S0 r3: S0d's whole `pm_suspend` was 41 ms, plus the hotplug of 7 CPUs);
- resume after C4.

Keep every alarm at ≤ 40 s. If the SoC only half-sleeps with TOP still clocked, the 60 s WDT then fires *after* the planned wake, never before. The maximum at this prescaler is ~82 s. Do not raise it for S1.

### 4.4 Residual risk and mitigation

The one case the phone cannot leave on its own is "TOP off, then no wake or a hang on the way up". S1a/S1b prove every link of the wake path except "SLEEP honours WAKEUP bit 1" and the APM/EL3 up path itself. S1c lowers the risk further: its pending self-IPI makes an early return likely, and the 12 s AP RTC alarm backs it up.

**Before S1c the owner must accept that a failure here leaves the phone dark until a force restart (O1).** The owner need not be present, but should be reachable. Keep the PC fallback alarm (`C:\Users\Patrick\tools\fallback-alarm.ps1`, "Note9-fallback-alarm" scheduled task) armed for the trial window. `run-trial.sh` already reports `PHONE DID NOT RETURN`.

---

## 5. Staged S1 plan (Q4)

**Vehicle for every stage:**
- the delayed-UFS RECOVERY trial: shim `bbea959`, ramdisk `506232b1` (CPU0-pinned PID1, restart at 120 s CLOCK_MONOTONIC, fallback at 150 s), packer `pack-acpm-q-paths.py`;
- host sends `CROWNLTE-UFS-READY-1` once and **never** `PROBE`;
- capture ≥ 400 s with the r3 `ready-capture.ps1` (it logs every reopen);
- after return: save `pmsg-ramoops-0` and `dmesg-ramoops-*`, restore bright RECOVERY `8763860c`, and wait for AshLooper `boot=booted` before the next reboot.

Suspended time does not advance CLOCK_MONOTONIC, so the 120 s restart moves later by the time slept. The WDT deadline (boottime) gets no RTC sleep injection, because rtc0 reads are unlatched. Both are fine.

New DTS: `exynos9810-crownlte-delayed-ufs-suspend-s1-diag.dts`. It includes the S0 DTS and changes:

```
&system_suspend {
	/delete-property/ samsung,suspend-s0;
	samsung,suspend-s1 = "a", "b";            /* per pack: "a","b" | "a","b","c" | ... */
	samsung,suspend-s1-alarm-s = <10>;        /* S1d/e; S1c uses 12 */
	samsung,suspend-s1-cycles = <5>;          /* S1e */
	samsung,suspend-s1-long-s = <35>;         /* S1e last cycle, <= 40 */
	samsung,wakeup-masks = <0xd00f7e7c 0xffff00ff 0xffff00ff 0x0>;
	reg = <... existing ...>, <0x0 0x141e0000 0x0 0x100>;
	reg-names = ..., "ap-rtc";
	/* deadline stays 145000 ms; start 20000; end-by 100000 */
};
&decon_handover { samsung,stop-on-suspend; };
&acpm_tmu { samsung,suspend-ipc; };
/* no samsung,rtc-write-enable, no PMIC IRQ: no PMIC writes in S1a-S1e */
```

Each stage ships as its own pack, in this order. A FAIL stops the series.

### S1a: AP RTC and sleep-state probe (read-only, self-recovering, phone idle OK)

- **Code:** C5 snapshot + C9 runner. No writes beyond S0 (S0a-S0b still run as the regression base: `"a","b"` of S0 inside S1a).
- **Reads:**
  - AP RTC as in §3.1;
  - CMU_APM `0x14003028`;
  - PMU `0x404/0x408/0x40c`;
  - PMIC (read-only, existing selftest path) extended by `0x1E 0x21 0x24 0x27 0xC8 0xC9`.
- **PASS lines:**
  ```
  suspend s1: S1a ap-rtc RTCCON=<x> (rtcen=<0|1> clkrst=0 ticen=0) INTP=0x0 RTCALM=0x0 TICCNT=<x> QCH_APBIF_RTC=<x>
  suspend s1: S1a ap-rtc t1=<yy-mm-dd hh:mm:ss> t2=<..> advance 2 s over 2100 ms (bcd ok) -> counting
  suspend s1: S1a pmu RST_STAT=<x> WDT_AUTO_DISABLE=0xfeffffff WDT_MASK_RESET=0xfeffffff
  pmic selftest: pmic[0x1e..]=.. [0xc8..0xc9]=..
  suspend s1: S1a PASS (read-only)
  ```
- **Failure signatures:**
  - `synchronous external abort` in `aprtc_snapshot` (pstore `dmesg-ramoops`) → secure-only. Panic → reboot → bounce. **AP RTC route dead → S1f (D1).**
  - Log stops at the snapshot → APB stall → WDT ≤ 30 s → bounce. Same conclusion.
  - `not counting` / `bcd invalid` / `CLKRST set` → no 32 kHz or held in reset. Route dead. Do not "fix" it with RTCCON writes without a new review.
  - `RTCALM.ALMEN=1` or `TICEN=1` → the block has another owner. Stop.
- **Also (phone use, read-only, needs the phone idle):** on live 4.9 Lineage, read PMIC `0x1E 0x21 0x24 0x27 0xC8 0xC9 0x13` and the B/L CTRL range through debugfs `/sys/kernel/debug/s2mps18-regs/{i2caddr,i2cdata}`. Compare with S1a. A difference is recorded, not a gate.

### S1b: AP RTC alarm fires while awake (self-recovering, no sleep)

- **Code:** C5 arm/disarm.
- **Writes, all SoC:** `INTP` (clear), `ALMSEC`, `RTCALM`, possibly a `RTCCON.RTCEN` set and restore.
- **Test:**
  1. Arm +3 s.
  2. Poll INTP bit 1 every 50 ms for up to 5 s.
  3. Read GICD `ISPENDR1` bit 31 (INTID 63 = SPI 31; read-only, 4.9 reads ISPENDR) and WAKEUP_STAT.
  4. Disarm and clear.
  5. Repeat with +7 s.
- **PASS lines:**
  ```
  suspend s1: S1b arm +3 s: ALMSEC=0x<bcd> RTCALM=0x41 readback ok rtcen-needed=<0|1>
  suspend s1: S1b alarm INTP.ALM set after <2.0..3.99> s, GICD SPI31 pending=<0|1>, WAKEUP_STAT=<x> -> disarmed, INTP=0x0
  suspend s1: S1b PASS 2/2
  ```
- **FAIL:**
  - `readback wrong even with RTCEN` (write-protected block);
  - `INTP.ALM not set within 5 s`;
  - `INTP not cleared`.
  Any of these → AP RTC route dead → S1f.

### S1c: real CPU_SUSPEND with a pending self-IPI, AP RTC alarm +12 s as backup

**Owner item O1 must be accepted before this pack runs.**

- **Code:** C1-C4 and C6-C9.
  - syscore uses `cal_pm_enter(8)` (armed).
  - `.enter` sends the 4.9 self-IPI (as in S0d), then calls the real finisher.
- **What happens.** If EL3 sees the pending SGI, CPU_SUSPEND returns nonzero (early; APM ring `upend_wk`/`EarlyRet`; `cal_pm_earlywakeup`). If it does not, the SoC really sleeps until the alarm. **Both are PASS**, and the log says which.
- **PASS lines (early path):**
  ```
  suspend s1: step 20 cal_pm_enter(8) armed: INFORM0=0x4 GRP2 en bit4=1 masks 0x610=0xd00f6e7c ...
  suspend s1: step 25 decon stopped (RUN_STATUS 0) tmu AP_SUSPEND ack=<w0..w3> ap-rtc armed +12 s ALMSEC=<..>
  suspend s1: step 30 SMC fn=0xc4000001 state=0x3000000 entry=<__pa(cpu_resume)> cpu0 self-IPI pending=1 wdt=60s cntpct=<a> frc=<b>
  suspend s1: step 40 CPU_SUSPEND returned <nonzero> (early) frc <b>-><b'> running wdt found WTCON=<..> (no reset) ap_sleep <n>-><n|n+1> mif_sleep <m>-><m>
  suspend s1: step 50 syscore resume earlywakeup ok ... PAD_RET all 1 WAKEUP_STAT=<x>
  suspend s1: apm-ring ... ente_pm+ ... EarlyRet|upend_wk ...
  suspend s1: S1c PASS early-return (firmware abort path proven)
  ```
- **PASS lines (slept path):** as S1d, with `S1c PASS slept (self-IPI ignored by EL3)`.

### S1d: one real SYS_SLEEP, AP RTC alarm +10 s, no self-IPI

- **Code:** same as S1c, without the IPI.
- **Expected PASS lines:**
  ```
  suspend s1: step 30 SMC fn=0xc4000001 state=0x3000000 entry=<..> ap-rtc ALMSEC=<s+10> wdt=60s cntpct=<a> frc=<b>
  suspend s1: step 40 CPU_SUSPEND returned 0 (resumed via cpu_resume) cntpct <a>-><a'> frc <b>-><b'> (<running|restarted: G_TCON was 0x0>)
  suspend s1: step 40 wdt found WTCON=<..> WTDAT=<..> WTCNT=<..> auto_disable=<..> mask=<..> -> re-armed 60 s
  suspend s1: step 40 PAD_RET before exit: top=0 spi=0 bootldo0=0 bootldo1=0 pcie=0 uart=1   <- SoC sequencer ran
  suspend s1: step 40 WAKEUP_STAT=0x00000002/0x0 (bit1 RTC alarm) RST_STAT=<unchanged> GICD SPI31 pending=1
  suspend s1: step 40 flexpmu ap_sleep <n>-><n+1> mif_sleep <m>-><m+1 "MIF down" | m "MIF blocked by req=<mask>"> FLEXPMU_INFORM=<..>
  suspend s1: step 40 ap-rtc slept <~10> s (rtc) vs cntpct <..> ms
  suspend s1: step 50 syscore resume exit ok (ret 0/0): INFORM0=0x0 PAD_RET all 1 ...
  suspend s1: step 55 tmu AP_RESUME ack, temps <..>
  suspend s1: step 60 wake: 8 CPUs online
  suspend s1: apm-ring ... ente_pm+ ent_soc+ [en_mif_f] SLEEP+++ ... SLEEP--- [ex_mif_f] ex_soc_f ...
  suspend s1: S1d PASS slept <ms> ms, woke by ap-rtc, ap_sleep +1, mif <down|blocked>
  ```
- **Failure signatures:**

| Signature | Meaning | Next |
|---|---|---|
| `step 20 refused: DECON running` / `TMU AP_SUSPEND no ack` / `GRP2 not armed` | A gate failed before the SMC | Nothing slept; normal 120 s restart |
| `CPU_SUSPEND returned <err>` with no `EarlyRet` in the ring, ap_sleep unchanged | EL3 refused the call (state or entry rejected; PSCI errno) | Self-recovering. Check C1 parameters. |
| `CPU_SUSPEND returned nonzero (early)` with WAKEUP_STAT bit 1 | Alarm already fired (armed too late) | Rerun with a larger delta |
| Log ends at `step 30`, phone back via WDT (RST_STAT WDT bit) within ~60-70 s | Stall with TOP still clocked (APM sequencer / LPI / EL3) | Self-recovered. The APM ring is lost, but the pstore tail shows step 30 as last. Next: dump the ring at step 30, check DECON/TMU. |
| Log ends at `step 30`, **phone dark**, `PHONE DID NOT RETURN` | Real SLEEP with no wake, or a hang on the way up | **Owner force restart (O1).** Then the AP RTC wake in SLEEP is unproven: next is S1f (PMIC RTC, D1). The pstore console survives a warm reset and may survive a PMIC manual reset (DRAM in self-refresh). Read it. |
| Phone resets ~1-3 s after wake (pstore ends inside resume, RST_STAT WDT) | PERIS came back at the WDT reset value and C4 was too late or missing | Fix C4 placement |
| `step 40 ... FRC STOPPED` and then no output | Counter frozen; timers never fire | WDT (C4 armed it) → reset ≤ 60 s. Fix C3. |
| `step 50 syscore resume exit FAILED` | CAL exit sequence error after real sleep | Recorded; WDT covers a hang |
| `CPU-LOST cpuN` on resume | Hotplug-in after real sleep failed (EL3 LITTLE bug path) | As S0: counted offline, normal restart |
| Serial never returns, pstore shows `PASS` | dwc3 resume after FSYS0 power-off broken | Not an S1 failure; USB fix later |

### S1e: repeatability

Same code. 5 cycles of 10 s, then one cycle of 35 s, 3 s apart. PASS needs every cycle with:
- `ret 0`;
- ap_sleep +1 each;
- PAD_RET 0 before exit;
- WAKEUP_STAT bit 1;
- no LOST CPU;
- FRC handled;
- the 120 s restart still happening.

Line: `suspend s1: S1e PASS 6/6 slept <total> ms, mif down <k>/6`.

It must end by monotonic ~100 s, so the PMIC selftest at ~106 s does not overlap.

### S1f: PMIC RTC alarm (owner go D1)

- **Code:**
  - S2MPS18 IRQ domain on gpa0-1 (threaded, INT1..7 read-to-clear over ACPM ch 2);
  - the RTC alarm IRQ (`rtc_update_irq(RTC_AF)`, `enable_irq_wake`);
  - pinctrl `exynos9810_retention_data` (so the `0x60c` write happens);
  - DT `samsung,rtc-write-enable` + the INT2M allowance.
- **Steps:**
  1. Awake test: alarm +3 s delivered as an IRQ, `INT2 = 0x04`.
  2. Sleep with the PMIC alarm at +10 s and the AP RTC backup at +20 s.
  3. Restore INT2M to `0xFF` and disarm A0 at the end.
- **PASS:** `WAKEUP_STAT bit0 (EINT)`, `EINT_PEND gpa0=0x02`, `woke by s2mps18 RTCA0` before the AP RTC backup time.

Later stages are unchanged from D §5: S2 power key, S3 charger/USB (D2/D4), UFS-bound sleep (D3).

---

## 6. Did the SoC really sleep? (evidence after return)

From strongest to weakest:

1. **`cpu_suspend()` returned 0.** Only the `cpu_resume` path returns 0. It means CPU0 lost its context and came back through EL3 warm boot.
2. **PAD_RETENTION read 0 before `cal_pm_exit`** (TOP/SPI/BOOTLDO0/1/PCIE). Only the APM SoC sleep sequencer clears them, and only UART is released by the APM on the way up. In S0 they always read 1.
3. **FlexPMU `ap_sleep` (line 22) +1.** `mif_sleep` (line 23) +1 = MIF/DRAM self-refresh too. Unchanged `mif_sleep` with `sw_flag` bits 23:16 ≠ 0 = "MIF blocked" (expected with the CP powered).
4. **APM log ring:** `ente_pm+`, `ent_soc+`, `SLEEP+++` ... `SLEEP---`, `ex_soc_f`. With MIF: `en_mif_f`/`ex_mif_f`. `EarlyRet`/`upend_wk` = aborted entry.
5. **WAKEUP_STAT `0x600` bit 1** (AP RTC alarm), `0x640` extra. A real wake latches the source; an early abort usually shows nothing or the IPI in GICD_ISPENDR0 bit 1.
6. **Timers:** MCT G_TCON bit 8 cleared or the FRC reset (TOP reset). WTCON found at `0x8021` (PERIS reset). CNTPCT jump or reset vs the AP RTC elapsed seconds: a slept interval shows ~10 s of RTC time with little or no counter time.
7. **RST_STAT `0x404` unchanged**: no reset in between.
8. **pstore:** the S1 lines after `step 40` exist only if the kernel resumed.

---

## 7. Owner-gated items

| ID | Item | Needed for | Status / recommendation |
|---|---|---|---|
| **O1** (new; replaces D5 "present") | Accept that a failed S1c/S1d/S1e (real SLEEP, then no wake or a hang with TOP off) leaves the phone dark until a force restart. This is not a PMIC write, but it is the only non-self-recovering risk. | S1c, S1d, S1e | Ask before S1c. Recommend: owner reachable, PC fallback alarm armed. |
| D1 | PMIC writes: INT2M `0x08` `0xFF ↔ 0xFB`; RTC alarm 0 `0x0C-0x12` (twice per arm/disarm) + UPDATE `0x02` AUDR (`0x10`) strobes. Read-to-clear of INT1-7 is a read. | S1f only | Not needed for S1a-S1e |
| D2 | MAX77705 interrupt-mask writes (VBUS/charger wake) | S3 | Later |
| D3 | UFS guard: SSU power conditions to W-LUN 0xD0 | UFS-bound sleep | Later |
| D4 | Charging during sleep: periodic ≤ 60 s wake | S3 | Later |
| — | Read-only PMIC register read on the live 4.9 phone (debugfs) | S1a comparison | Read-only phone use; needs the phone idle |

**Not owner-gated (no PMIC write):**
- the AP RTC reads and writes;
- PMU `0x610` bit 1;
- the WDT PMU `0x408/0x40c` writes (4.9 values);
- the DECON stop;
- the TMU IPC;
- the MCT FRC start.

They still go through Sol review, because the AP RTC is MMIO that 4.9 never touches. That breaks the S0 rule "only registers 4.9 reaches by non-secure MMIO", and S1a is how the exception gets justified on hardware.

---

## 8. Open questions (answered by the trials, not by more reading)

1. Is the AP RTC non-secure and counting (S1a)? Does SLEEP honour WAKEUP bit 1 (S1c/S1d)?
2. Does EL3 abort CPU_SUSPEND on a pending SGI (S1c)?
3. Does PERIS (WDT, MCT) lose state in SLEEP? Does anything set PMU `0x408` bit 24 during sleep (C3/C4 logs)?
4. Does the FRC stop, or reset, in SLEEP? Does any firmware restart it on wake (APMBIN `0x10c8` helper)?
5. MIF down or blocked with the CP powered but not booted (`sw_flag`)?
6. The exact APM tag order (ring dump after S1d).
7. Do the DECON per-frame stop and the TMU `AP_SUSPEND` ack work on 6.12 (S1c gates)?
