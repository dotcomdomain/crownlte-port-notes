# crownlte 6.12: system suspend (SYS_SLEEP) design

2026-11-06. Research only: nothing was built, flashed or run. No phone access.

Sources:
- K49 = 4.9 kernel of the running system, gekko `/root/crownlte-hid-kernel` (HEAD 8665533d).
- DT49 = `K49/arch/arm64/boot/dts/exynos/exynos9810-crownlte_eur_open_26.dts`.
- T = 6.12 port, branch `crownlte-integration-4` b4f756bed (worktree `/dev/shm/integration-4`).
- APM = `next-research/apm.bin` (APM firmware, Thumb), read with llvm-objdump-18.

The EL3 monitor is encrypted and was not touched. Its behaviour is inferred from what 4.9 passes to it.

Confidence tags:
- **[src]**: read in source.
- **[apm]**: read in the APM disassembly.
- **[inf]**: inferred, needs a trial to confirm.

## 0. Summary

1. **How 4.9 sleeps.** 4.9 does not use PSCI SYSTEM_SUSPEND. Its platform `.enter` calls PSCI **CPU_SUSPEND**:
   - x0 = `0xC4000001`, x1 power_state = `0x03000000` (affinity level 3, "system"), x2 = `__pa(cpu_resume)`, x3 = 0.
   - Before that, a syscore hook programs the PMU: wakeup masks, `CPU_INFORM[cpu0] = 4` (SLEEP), ~1100 CMU registers saved, and the APM "core down" interrupt for cpu0 armed.
   - When cpu0 powers down in EL3, the PMU raises GRP2 to the APM. The APM firmware sees INFORM = 4 and runs the SoC and MIF sleep sequencers: pad retention, DRAM self-refresh, TCXO off, PWREN low.
   - Wake reverses this. EL3 warm-boots cpu0 into `cpu_resume`.
   - The kernel then releases pad retention, restores the CMU registers and reads WAKEUP_STAT.
2. **What 6.12 already has.**
   - The whole CAL layer (`cal_pm_enter/exit/earlywakeup`, the 4.9 flexpmu tables, `pmucal_system_init` at boot).
   - PSCI fn IDs from DT.
   - Kinibi PM notifier, UFS vops suspend/resume, devfreq/cpufreq suspend rates.
   - The cpu_pm syscore, which saves and restores the GIC-400 distributor.
3. **What 6.12 is missing.** The platform driver (exynos-pm), plus seven blockers:
   1. a correct PSCI finisher: the generic one passes **entry point 0** for 0x03000000 (§3.1);
   2. the EINT wake mask, which is never written: there is no 9810 pinctrl retention data;
   3. the PMIC IRQ, and with it the RTC alarm wake;
   4. a CPU hotplug-out hook;
   5. MCT FRC restart before timekeeping resume;
   6. DECON stop;
   7. the TMU suspend IPC.
   Mali vetoes suspend on purpose.
4. **Mandatory wakeups** for a usable phone, all EINTs on GPIO_ALIVE plus the CP mailbox SPIs:
   - PMIC IRQ (RTC alarm) gpa0-1;
   - power key gpa2-4;
   - MAX77705 IRQ (charger/USB/CC) gpa1-6;
   - modem (CP mailbox SPIs 69/70/71/74/75/440-442 in WAKEUP_STAT).
   Wi-Fi OOB (gpa0-7), BT host-wake (gpa2-3) and sensorhub (gpa1-7/gpa0-5) are needed for normal use, but not to "wake for the alarm".
5. **Biggest risks:**
   - not waking: there is no timer that runs in SLEEP except the PMIC RTC, and the watchdog clock (OSC) stops;
   - CPU_SUSPEND with entry 0;
   - the arch counter stopping (MCT FRC) on resume;
   - LITTLE hotplug hitting the known EL3 LITTLE bug;
   - SLEEP with the CP never booted: 4.9 refuses when `PMU CP_STAT == 0`.
   The data-loss risk is low while 6.12 has no RW /data.
6. **Plan.**
   - S0a-d: four self-recovering steps (register snapshot, hotplug loops, `pm_test=core`, early-wake dry run).
   - S1: real SLEEP with a PMIC RTC alarm after 10 s. **The owner must be present for the first run** (manual reset is the rollback).
   - Then S2 power key, S3 charger/USB, modem later.

---

## 1. The exact 4.9 suspend-to-RAM sequence

### 1.1 Kernel side, in order

| # | Step | Where (K49 unless noted) |
|---|---|---|
| 1 | Userspace writes `mem` → `pm_suspend(PM_SUSPEND_MEM)`. Platform ops are `exynos_pm_ops` {`.enter = exynos_pm_enter`, `.valid = suspend_valid_only_mem`}. | `drivers/soc/samsung/exynos-pm.c:386-389`, `suspend_set_ops` at :625, `arch_initcall` at :640 |
| 2 | `PM_SUSPEND_PREPARE` notifiers. Kinibi `mc_scheduler_suspend()` sets the MCP flag `sleep_req = MC_FLAG_REQ_TO_SLEEP`. The SWd acknowledges with `ready_to_sleep`. On `PM_POST_SUSPEND` → `nq_resume()`. | `drivers/gud/gud-exynos9810/MobiCoreDriver/main.c:282-317, 452-453`; `nq.c:473-500`; `MC_PM_RUNTIME` in `platform.h:44` |
| 3 | Device suspend: UFS, dwc3, DECON, TMU, devfreq, cpufreq, ... (§3). The TMU sends ACPM `TMU_IPC_AP_SUSPEND` (type 0x04, rsvd = CP net state) when the last zone suspends. | `drivers/thermal/samsung/exynos_tmu.c:1670-1699`, `exynos_acpm_tmu.c:157-190`, `exynos_acpm_tmu.h:43-45` |
| 4 | Nonboot CPUs go offline: PSCI CPU_OFF `0x84000002`. On each dying CPU, `exynos_hotplug_out_callback()` runs `cal_cpu_disable()`: `CPU_INFORM[cpu] = 1`, the core-off sequence, and GRP2 armed. The last big core also runs `big_reset_control(0)` + `cal_cluster_disable()` (INFORM = 2, CPD). | `exynos-powermode.c:708-721, 106-113, 927-934`; `cal-if/pmucal_cpu.c:52-75, 162-180` |
| 5 | `syscore_suspend` (reverse registration order: exynos-pm first, cpu_pm last). | — |
| 5a | `exynos_pm_syscore_suspend()`: refuse if `PMU CP_STAT (0x14060038) == 0` ("sleep canceled by CP reset"). Pick mode 12 (SLEEP_USBL2) if `usb_is_connect()` (dwc3 `otg.c:838`), else mode 8 (SYS_SLEEP). | `exynos-pm.c:291-316`; `exynos-pmu.c:172-180` |
| 5b | `exynos_prepare_sys_powerdown(8)`: IDLE_IP masks (SLEEP has no idle_ip node, so all stay `0xffffffff`). | `exynos-powermode.c:646-671` |
| 5c | `exynos_set_wakeupmask(8)`: <ul><li>PMU `0x60C` EINT_WAKEUP_MASK = pinctrl `exynos_eint_wake_mask` (bit cleared per `enable_irq_wake`; bit = `2*eint_offset + pin`, so gpa0 = 0-7, gpa1 = 8-15, gpa2 = 16-23, gpa3 = 24-31; gpa4 overflows)</li><li>`0x610` = 0xD00F7E7E</li><li>`0x614` = 0xFFFF00FF</li><li>`0x618` = 0xFFFF00FF</li><li>`0x644` = 0x0</li></ul>The last four come from the DT `wakeup-masks` column 8. | `exynos-powermode.c:632-644`; `pinctrl-exynos.c:448-469`; DT49:4914-4936 |
| 5d | `cal_pm_enter(8)` = `pmucal_system_enter()`: <ol><li>`pmucal_powermode_hint(CPU_INFORM_SLEEP = 4)` writes PMU `0x14060860 + 4*phycpu`</li><li>`save_sleep[]` (~1100 CMU/PLL/QCH registers)</li><li>`enter_sleep[]` (3 writes): INTR_GEN `0x14070200` GRP2_INTR_BID_ENABLE bit4 = 1; clear GRP1 bit4 (`0x1407010c`); PMU `0x14067f08` EVENT_INTERRUPT_ENABLE_GRP2 bit4 = 1. Bit 4 = LITTLE cpu0.</li></ol> | `cal-if/pmucal_system.c:15-50`; `pmucal_powermode.c:21-28`; `exynos9810/cal_data.c:173-174`; `flexpmu_cal_system_exynos9810.h:125-130 (enter), 131-1235 (save)` |
| 5e | `cpu_pm` syscore: `cpu_pm_enter()` + `cpu_cluster_pm_enter()` → GIC-400 CPU interface and distributor saved. | `kernel/cpu_pm.c` (same in T: `kernel/cpu_pm.c:176-201`; T `drivers/irqchip/irq-gic.c:708-722`) |
| 6 | `exynos_pm_enter()`: optional self-IPI (debugfs `test_early_wakeup`), log `acpm_get_mifdn_count()` / `acpm_get_mif_request()` (flexpmu debug SRAM), then `arm_cpuidle_suspend(133)`. | `exynos-pm.c:341-384` |
| 7 | `psci_cpu_suspend_enter(133)`: index ≥ 128 → `cpu_suspend(133, psci_suspend_customized_finisher)`. <ul><li>`__cpu_suspend_enter` saves the context.</li><li>The finisher packs `PSCI_SYSTEM_SLEEP` = `psci_power_state_pack(0,0,3)` = **0x03000000** and calls `psci_ops.cpu_suspend(0x03000000, virt_to_phys(cpu_resume))`.</li></ul> | `arch/arm64/kernel/cpuidle.c:39-43`; `drivers/firmware/psci.c:446-495`; `include/linux/psci.h:20-26` |
| 8 | **SMC**: x0 = `0xC4000001` (DT `psci { cpu_suspend = <0xC4000001> }`, PSCI 0.1 fn-id table), x1 = `0x03000000`, x2 = phys(`cpu_resume`), x3 = 0. `cpu_off = 0x84000002`, `cpu_on = 0xC4000003`. | `drivers/firmware/psci.c:173-181`; DT49:4938-4944 |

Notes:
- The DT `suspend_psci_idx = 133` = 128 + 5 = `PSCI_SYSTEM_SLEEP`. `suspend_mode_idx = 8` = SYS_SLEEP. `usbl2_suspend_mode_idx = 12`. `cp_call_mode_idx = 10`. (DT49:4872-4893.)
- The power_state has TYPE bit 16 = 0. Samsung's EL3 does not use the standard StateType bit; the context is always saved by `cpu_suspend()`. **[src]**

### 1.2 EL3 (encrypted, inferred)

**[inf]** from the call contract and the PMU tables:
- EL3 records the per-core entry point (x2).
- It may save its own GIC Group-0 and secure state (4.9 never saves the secure GIC state itself).
- It executes WFI on cpu0. The PMU sees cpu0 STANDBYWFI with CPU_INFORM = 4 and GRP2 bit4 enabled, and raises GRP2 to the APM.

**Resume entry.** The kernel never writes a resume address to any PMU INFORM register: there are no INFORM0/1 or `cpu_resume` writers outside psci.c. The only resume address passed is x2 of CPU_SUSPEND.

**Resume path.** On wake:
1. cpu0 comes out of reset into the boot ROM / EL3 warm-boot path.
2. EL3 finds its suspend record.
3. It ERETs to EL1 at x2 with the MMU off.
4. `cpu_resume` restores the context, and `cpu_suspend()` returns 0.

**Early wake.** If a wake source is already pending, EL3 or the APM aborts and CPU_SUSPEND returns nonzero. `exynos_pm_enter()` then logs "return to originator" and runs `cal_pm_earlywakeup()`. **[src]** for the kernel half, **[apm]** for the APM "EarlyRet"/"upend_wk" tags.

### 1.3 APM (ACPM FlexPMU plugin) side

The APM sequences are in the same 4.9 header under `#else` of `#ifndef ACPM_FRAMEWORK` (`flexpmu_cal_system_exynos9810.h:2565-3046`). They are the firmware copy and are not executed by the kernel.

| Phase | APM sequence | Key actions |
|---|---|---|
| SoC down | `soc_sequencer_sleep_down[]` :2641-2681 | <ul><li>Mask GRP1</li><li>FSYS0/CPUCL1/TOP_BUS off</li><li>CMU_TOP PLLs off</li><li>HOSTIRAM retention</li><li>**PAD_RETENTION TOP/SPI/BOOTLDO0/1/PCIE/UART enter** (`0x14063060/3080/30a0/30c0/3120/31a0` = 0)</li><li>logic reset, clamps</li><li>`OSCCLK_SLEEP`</li><li>**EXT_REGULATOR (PWREN) off** `0x14063600`</li><li>big BISR/OTP retention</li><li>arm GRP4 (wakeup)</li></ul> |
| MIF down | `mif_sequencer_sleep_down[]` :2825-2851 | <ul><li>APM/CMGP/VTS/CHUB bus mux to OSC (VTS/CHUB only if powered)</li><li>MIF bus/PSCDC off</li><li>DDRPHY DLL/clock stop</li><li>**PAD_RETENTION_LPDDR4** (DRAM self-refresh)</li><li>`SLEEP_IO_CTRL`</li><li>MIF resets</li><li>EXT_REGULATOR_MIF off</li><li>**TCXO off** `0x140682e0`</li><li>arm GRP5</li></ul> |
| MIF up | `mif_sequencer_sleep_up[]` :2853-2888 | TCXO and MIF regulator on (waits on status), DDRPHY restart, LPDDR4 retention release, MIF bus on |
| SoC up | `soc_sequencer_sleep_up[]` :2683-2714 | <ul><li>Clear WAKEUP_MASK bit31</li><li>regulators on</li><li>OSCCLK</li><li>resets/clamps released</li><li>**only PAD_RETENTION_UART released**</li><li>HOSTIRAM</li><li>PLLs</li><li>TOP_BUS, CPUCL1, FSYS0 on</li></ul> |

**[apm]** The APM binary has the matching log tags:
- Power-mode entry function at APM `0x109e4` (tag `ente_pm+`). It reads the mode from CPU_INFORM low 16 bits (`0x11348`); the mode table maps 4/5 → SLEEP (roadmap `little-c2-apm-re.md`).
- `early_pm`/`upend_wk`/`EarlyRet` (early-wake abort): literals at `0x10d28/0x10d34/0x10d38`, code `0x10b32`, `0x10bbe`.
- `ent_soc+/-` (`0x10bf0`, `0x10c3a`); `en_mif_f` (`0x104ba`); `ex_mif_f` (`0x10242`); `ex_soc_f` (`0x10602`).
- `SLEEP---` (`0x10588`, `0x10b9e`) and `SLEEP+++`. `SLEEP+++` is logged from the dispatcher's mode-table path at `0x11576`.

The exact order of these tags is **[inf]**. They land in the APM log ring (SRAM APM `0x6f08` rear / `0x6f0c` front / `0x6f10` data, 16-byte entries × 250; AP phys = `0x02039000` + APM offset), so a post-resume dump shows the firmware's own sequence (§5 self-tests).

**Counters 4.9 logs** (`exynos-flexpmu-dbg.c:60-123`, DT49:9216-9222, data-base `0x0204F800` = APM `0x16800`, 16-byte lines, value at +8 / +12):
- `DID_AP_COUNT_SLEEP` = line 22: AP-down counter;
- `DID_MIF_COUNT_SLEEP` = line 23: MIF-down counter, read by `acpm_get_mifdn_count()` at `0x0204F97C`;
- `DID_SW_FLAG` = line 10, bits 23:16: MIF requester mask ("MIF blocked by master 0x..").
- `PMU 0x14060880` FLEXPMU_INFORM is read after resume.

### 1.4 Kernel resume (4.9)

1. `cpu_suspend()` returns 0. `exynos_pm_enter()` logs the MIF count delta ("MIF down" or "MIF blocked") and reads `FLEXPMU_INFORM` (`exynos-pm.c:373-384`).
2. `cpu_pm` syscore resume restores the GIC.
3. `exynos_pm_syscore_resume()` (`exynos-pm.c:318-330`) → `cal_pm_exit(8)` = `pmucal_system_exit()` (`pmucal_system.c:60-99`):
   - `exit_sleep[]` (:1237-1278): disable GRP2 bit4 and clear it; re-enable DRCG on ~30 bus components; **release PAD_RETENTION TOP/SPI/BOOTLDO0/BOOTLDO1/PCIE/UART** (`0x14063060…31a0` bit0 = 1);
   - `pmucal_rae_restore_seq(save_sleep)`.
4. `exynos_show_wakeup_reason()` (:196-232) prints WAKEUP_STAT `0x14060600`, `0x14060640` (extra) and EINT_PEND (`GPIO_ALIVE + 0xA00`). It maps bits through DT `wakeup_stat_irqno` / pending EINTs to `log_wakeup_reason()`.
5. Nonboot CPUs come online via PSCI CPU_ON `0xC4000003`. `exynos_hotplug_in_callback()` runs `cal_cpu_enable` and, for the first big core, `cluster_enable` + `big_reset_control(1)`.
6. Devices resume. TMU sends `TMU_IPC_AP_RESUME` (0x10) and re-reads temperatures. Kinibi resumes.

---

## 2. Wakeup sources

### 2.1 PMU WAKEUP_MASK (`0x610`) in SYS_SLEEP = 0xD00F7E7E

0 = wake enabled. SPI owners come from DT49 `wakeup_stat_irqno` (:4885) and the nodes that own those SPIs.

| bit | SPI / owner | SLEEP |
|---|---|---|
| 0 | EINT (GPIO_ALIVE, gated by `0x60C`) | **on** |
| 1 / 2 | 31 / 32: **AP RTC** alarm / tick (`rtc@141E0000`, `exynos8-rtc`) | masked (so `exynos_rtc_wakeup()` returns -ENXIO, `exynos-powermode.c:794-805`) |
| 3, 4 | 33, 34 (unknown) | masked |
| 5, 6 | 253/254: PCIe0/1 | masked |
| 7, 8, 15, 27 | no SPI listed (internal: APM/CHUB/VTS?) | **on** |
| 11 | 249: dwmmc2 | masked |
| 14 | 466: MCT G0 | masked |
| 17 | 95: USB PHY (`phy@11100000`) | masked; **on in SLEEP_USBL2** (0xD00D7E7E) |
| 19 | 68: ACPM mailbox | masked |
| 20-26, 29 | 440, 441, 442, 75, 69 (`mcu_ipc@14170000` CP→AP mailbox), 74 (`mif_pdata` = modem), 71, 70: **modem** | **on** |

Other mask registers:
- `0x614` / `0x618` = 0xFFFF00FF: bits 8-15 on. Probably the CMGP wakeup EINTs (gpm*); **[inf]**.
- `0x644` = 0: all on. Extra stat `0x640` bits → SPIs 100, 98, 35 (SPEEDY), 422 (`uart@10840000`), 422, 108.

### 2.2 GPIO_ALIVE EINTs (DT49:230-545, gpio_keys :12978-13030)

| Pin | Function | 4.9 wake? | Phone needs it for |
|---|---|---|---|
| gpa0-1 | **S2MPS18 PMIC IRQ** (`pmic-irq`, `s2mps18,wakeup = "enabled"`, IRQ low; `s2mps18_core.c:503, 548-549`) | yes | **RTC alarm** (RTCA0 = INT2 bit2, `s2mps18_irq.c:70`), PMIC events |
| gpa2-4 | **KEY_POWER** gpio-keys (`gpio-key,wakeup = <1>`) | yes | **power key** |
| gpa0-6 | KEY_WINK (Bixby), wakeup = 1 | yes | optional |
| gpa0-3/0-4 | volume keys | no | — |
| gpa1-6 | **MAX77705** IRQ (charger, MUIC/CC, FG; `max77705.c`, `max77705_charger.c` enable_irq_wake) | yes | **charger / USB plug**, battery alerts |
| gpa3-1 / gpa2-0 | wireless charger int / det (`mfc_charger.c`) | yes | wireless charging |
| gpa0-7 | **WLAN host wake** (OOB, con-pdn 3, pud-pdn 1; bcmdhd `dhd_pcie_linux.c`) | yes | push messages and Wi-Fi calling while asleep |
| gpa2-3 | BT host wake | yes | BT |
| gpa1-7 / gpa0-5 / gpa4-0 | SSP sensorhub host-req / batch-wake / shub-int | yes (gpa4 cannot be set: bit 32 overflow in both kernels) | sensors, step count, lift-to-wake |
| gpa3-3, gpa3-7 | S Pen (epen) int | yes | pen removal wakes |
| gpa3-6 / gpa0-2 | hall / certify hall | yes | cover |
| gpa1-3 | fingerprint (btp) | yes | screen-off unlock |
| gpa3-0 | grip sensor | yes | RF SAR |
| gpa1-0 | touch attn | no (4.9 LPM only) | — |

### 2.3 Mandatory for a usable phone

1. **RTC alarm.** Android alarms use `/dev/rtc0` via `alarmtimer`. On this phone that is the S2MPS18 RTC, and the wake path is RTCA0 → PMIC IRQ → gpa0-1 EINT → PMU bit0. The AP RTC is masked in SLEEP on 4.9.
2. **Power key**: gpa2-4 EINT.
3. **Calls / SMS**: the CP→AP mailbox SPIs (bits 20-26/29). They are enabled in the SLEEP mask, but need a running CP and a 6.12 modem (SIPC) driver.
4. **Charger / USB plug**: MAX77705 IRQ gpa1-6. While charging, the battery driver must also wake at least every ~60 s: the MAX77705 charger watchdog stops charging at ~80 s without a kick (`power-gates.md` §2). 4.9's `sec_battery` polls with wakeup alarms.

Not mandatory for "wakes for the alarm", but needed for daily use: Wi-Fi OOB, BT host wake, sensorhub, S Pen.

### 2.4 6.12 status of each

| Source | 6.12 today |
|---|---|
| EINT wake mask write | **Missing.** `exynos_pinctrl_set_eint_wakeup_mask()` needs `drvdata->retention_ctrl->priv` (PMU regmap). 9810 pin ctrl has no `.retention_data` (T `pinctrl-exynos-arm64.c:1166-1176`), so it logs "Wake-up mask will not be set" and `0x60C` keeps its reset/SBoot value. The mask register (0x60C, `EXYNOS5433_EINT_WAKEUP_MASK`) and the bit formula match 4.9 (T `pinctrl-exynos.c:438-474, 540-557`). |
| PMIC IRQ / RTCA0 | **Missing.** <ul><li>RTC driver: "No PMIC interrupt is wired yet, so alarms are not delivered" (T `rtc-s2mps18-acpm.c:13-15`). Alarm 0 writes exist only with `samsung,rtc-write-enable`.</li><li>SBoot leaves INT2M (0x08) = 0xFF (RTCA0 masked). INT1M 0xFF, INT3M 0xFC. TOP INTSRC_MASK = 0x06, so the PMIC source is already unmasked (q24 log).</li><li>Unmasking RTCA0 needs one PMIC write outside the sealed policy (T `s2mps18-acpm.c:412-434`).</li></ul> |
| Power key | **Missing**: no gpio-keys node in any crownlte DT. |
| MAX77705 IRQ | **Missing** (RO battery driver, no IRQ, write API poisoned). |
| Modem | No driver. CP never booted → PMU `CP_STAT` probably 0, and 4.9 refuses sleep in that state (§4). |
| Wi-Fi OOB | crownlte-wifi branch in progress; must call `enable_irq_wake` on gpa0-7 and set the pin PDN state. |
| Pin sleep states | **Missing**: DT49 has 82 `samsung,pin-con-pdn` / 76 `pin-pud-pdn` entries. T has 0. The T driver supports both properties (`pinctrl-samsung.c:44-45, 1333-1430`). |

---

## 3. What each 6.12 driver needs

### 3.1 New platform driver `drivers/soc/samsung/exynos9810-pm.c` (port of exynos-pm.c + the system half of exynos-powermode.c)

- `platform_suspend_ops` {`.valid = suspend_valid_only_mem`, `.begin`, `.prepare_late`, `.enter`, `.wake`} and `syscore_ops` {`.suspend` = CP gate + masks + `cal_pm_enter(8)`; `.resume` = `cal_pm_exit` or `cal_pm_earlywakeup` + wake reason}. Same split as 4.9.
- Registering the ops makes `mem_sleep` default to `deep`, so Android's `mem` then means SYS_SLEEP. Keep `s2idle` selectable through `/sys/power/mem_sleep`.
- **Finisher. Do not call `psci_cpu_suspend_enter()`.**
  - In T it takes the "retention" branch for a state without TYPE bit 16. It calls `psci_ops.cpu_suspend(0x03000000, 0)` without saving context (T `drivers/firmware/psci/psci.c:488-510`, `:100-107`).
  - EL3 would resume at physical address 0.
  - Use `cpu_suspend(0, e9810_sleep_finisher)`, where the finisher returns `psci_ops.cpu_suspend(0x03000000, __pa_symbol(cpu_resume))`. That is the 4.9 customized-finisher contract.
  - The C2 state 0x10000 has bit 16 set, which is why cpuidle is unaffected.
- **MCT FRC.** Right after `cpu_suspend()` returns, and before `syscore_resume()` (`timekeeping_resume` is a syscore op), check that the MCT FRC (`0x10040100/104`, G_TCON `0x10040240` bit8) is counting. Start it if not, through a helper exported from `exynos9810-mct-bc.c`, the same as 4.9 `exynos4_frc_resume()` (K49 `exynos_mct.c:215-226`).
  - Log CNTPCT before and after.
  - **[inf]** On recent Exynos the generic-timer counter is fed by the MCT FRC, and TOP/PERIS lose power in SLEEP. The 6.12 mct-bc driver only starts the FRC at probe (T `exynos9810-mct-bc.c:34, 277`), and its `.tick_resume` only shuts the comparator (:217).
- **Wake masks.** Write `0x610/0x614/0x618/0x644` from DT, columns as in DT49. Write `0x60C` from pinctrl, or from the driver with the same value. Write IDLE_IP_MASK `0x3F0..0x3FC` = 0xFFFFFFFF (4.9 parity).
- **CP gate.** Log `CP_STAT 0x14060038`, `CP_CTRL_NS` and `CENTRAL_SEQ_CP_STATUS`. Refuse when CP_STAT == 0 unless the DT has `samsung,sleep-with-cp-off` (§4 R5).
- **USB L2 mode.** Pick mode 12 only once dwc3 reports a host connection (later stage). Until then use mode 8.
- **Wake reason.** WAKEUP_STAT `0x600` and `0x640`, `0x14050A00` EINT_PEND, GICD_ISPENDR (`0x10101200`). Map them with the DT `wakeup_stat_irqno` table and feed `pm_wakeup` / `log_irq_wakeup_reason`.
- **Debug.**
  - flexpmu counters at `0x0204F800` (AP_COUNT_SLEEP line 22, MIF_COUNT_SLEEP line 23, SW_FLAG line 10);
  - APM log ring snapshot (§5);
  - `FLEXPMU_INFORM 0x14060880`;
  - debugfs `test_early_wakeup` (self-IPI to cpu0 before CPU_SUSPEND, as `exynos-pm.c:358-359`).
- **Watchdog interplay**: see §3.11.

### 3.2 CPU hotplug (`drivers/cpuidle/cpuidle-exynos9810.c` or the new pm driver)

- T has only the online hook (`e9810_cpu_online` → `cal_cpu_enable`, :614-631, cpuhp :778).
- Add the 4.9 teardown hook on the dying CPU:
  - `cal_cpu_disable(phycpu)` (INFORM = 1 + core-off sequence + GRP2), with the existing stale-GRP2 guard;
  - when the cluster becomes empty: `big_reset_control(0)` + `cal_cluster_disable()` (K49 `exynos-powermode.c:708-721`, `exynos-reboot.c:182`);
  - on the first big core coming online: `cluster_enable` + `big_reset_control(1)`;
  - CPU_DEAD: poll the core power status ≤ 2 s and log it. Do not BUG().
- Without this, EL3 CPU_OFF reaches the APM with INFORM = 0 (`invldpwm` in the dispatcher). Cores may stay powered and block SYS_SLEEP. **[inf]**
- **Risk:** LITTLE cores 1-3 have the known EL3 hang in C2. Hotplug uses the same APM/EL3 path, so S0b must loop hotplug on every core before any sleep.

### 3.3 pinctrl (`drivers/pinctrl/samsung/pinctrl-exynos-arm64.c`, DT)

- Add `exynos9810_retention_data` for ALIVE:
  - PMU regmap via `samsung,pmu-syscon`;
  - regs = PAD_RETENTION_{TOP 0x3060, SPI 0x3080, BOOTLDO_0 0x30a0, BOOTLDO_1 0x30c0, PCIE 0x3120, UART 0x31a0}, release value BIT(0).
  - Then the generic late-suspend writes `0x60C` (`exynos_set_wakeup`, T `pinctrl-exynos.c:764-790`), and resume releases retention. A double release alongside `cal_pm_exit` is harmless; 4.9 releases them in `exit_sleep`.
- **Without the release all GPIO pads stay latched after wake**: I2C, SPI, UART and the touch/UFS sidebands are frozen.
- DT: copy every `samsung,pin-con-pdn` / `samsung,pin-pud-pdn` from DT49 (82/76 entries). Otherwise sleep pad states are whatever the PDN registers hold after reset.
- Leave gpa4 (bit 32 overflow) as 4.9 does.

### 3.4 PMIC / RTC (`drivers/mfd/s2mps18-acpm.c`, `drivers/rtc/rtc-s2mps18-acpm.c`, `soc/samsung/acpm/acpm_mfd.c`)

- IRQ domain on gpa0-1 (level low, threaded). The handler reads INT1..INT7 (`0x00-0x06`, read-to-clear) over ACPM channel 2 (AP-polled, so it works in the threaded/noirq context).
- Sealed policy extension (owner decision D1): allow only
  - INT2M `0x08` := 0xFB (unmask RTCA0) and back to 0xFF;
  - plus the existing alarm-0 range (`samsung,rtc-write-enable`).
  - Nothing else; INTSRC_MASK already reads 0x06.
- RTC: `rtc_update_irq(RTC_AF)` on RTCA0. `device_init_wakeup(dev, true)`; `enable_irq_wake` in `.suspend`, `disable_irq_wake` in `.resume` (4.9 `s2mps18_core.c:548-566`).
- Keep alarm 1 (power-on alarm) untouched.

### 3.5 UFS (`drivers/ufs/host/ufs-exynos9810.c`)

- **Done:**
  - vops `.suspend` (POST_CHANGE: HCI_GPIO_OUT = 0 = device reset low, PHY power off) and `.resume` (PHY on, sysreg, SMU, FMP), T :963-987;
  - system PM ops `ufshcd_system_suspend/resume` + prepare/complete, :1177-1181.
  - This mirrors 4.9 `__exynos_ufs_suspend/resume` (K49 `drivers/scsi/ufs/ufs-exynos.c:884-920`).
- **Set `hba->spm_lvl = UFS_PM_LVL_5`** (POWERDOWN + link OFF), the 4.9 default (K49 `ufshcd-pltfrm.c:241`). FSYS0 is powered off in SLEEP (`FSYS0_CONFIGURATION = 0`), so the host loses its state anyway. Resume = full host reset + link startup, the boot path already proven on 6.12 (HS-G3 x2).
- The ROM-RAM UFS write guard allows START STOP UNIT only with START/ACTIVE (`ufs-write-plan.md:13,121`). System suspend sends SSU with power condition POWERDOWN/SLEEP. Extend the guard to exactly "SSU to the DEVICE W-LUN 0xD0 with POWER CONDITION ∈ {ACTIVE, SLEEP, POWERDOWN}, START = 0/1, no LOEJ" (owner decision D3). Otherwise suspend aborts with -EIO, which is safe but not useful.
- **Data safety:**
  - the kernel syncs filesystems before freezing (default, SUSPEND_SKIP_SYNC not set);
  - ufshcd flushes the device cache through SSU;
  - 6.12 has no RW /data yet (T5 pending).

### 3.6 Display (DECON/DSIM/panel)

- T `dpu_fb_blank()` ignores blanking in adopt mode (`decon_drv.c:1983-1988`). The handover path only does `fb_set_suspend` (`exynos9810-decon-handover.c:555, 600`).
- **DECON keeps scanning out of DRAM** into a power domain that SLEEP turns off. Bus LPI handshakes in the APM SoC-down sequence may stall on an active DMA master. **[inf]**
- Required before S1, as a `.suspend` (late) in the DPU / handover driver:
  1. DECON stop at frame end (shadow update off, wait idle);
  2. DSIM: panel display-off + sleep-in, then ULPS/off. The S2-9 blank work in `display-port.md:260` already plans this.
  3. On resume, leave the panel off; S2 adds unblank.
- If the driver cannot stop DECON, `.prepare` must veto suspend and log it.

### 3.7 USB / dwc3 + USB-DRD PHY

- Upstream dwc3 system PM exists (T `drivers/usb/dwc3/core.c`). In mode 8 the PHY is powered down, so the host sees a **disconnect: COM4 (serial log) drops** and re-enumerates on resume.
- Capture must tolerate this. pstore console/pmsg (ramoops) keeps every line across the gap.
- 4.9 `usb_is_connect` → SLEEP_USBL2 (mode 12) keeps the link in L2 with VBUS present. Port it only in S3: `register_usb_is_connect` equivalent, mode-12 masks (`0x610` = 0xD00D7E7E), `enter_sleep_usbl2` QCH writes.

### 3.8 TEE (Kinibi)

- The 4.9 PM notifier is already in T (`drivers/gud/gud-exynos9810/MobiCoreDriver/main.c:303-352, 491-500`) under `MC_PM_RUNTIME`: SUSPEND/RESUME via the MCP `sleep_req` flag. `sec_os_booster` has its own notifier (registered at T `sec_os_booster.c:329`).
- Needed: a self-test line that the SWd reported `ready_to_sleep` before SYS_SLEEP, and a timeout path. 4.9 `nq_suspended()` loops.
- No SMC beyond PSCI is involved. 4.9's only secure power SMC is `SMC_CMD_PREAPRE_PD_ONOFF 0x82000410` for some local PDs (`exynos-el3_mon.c:327-337`, `pmucal_local.c:94`); it is not part of system sleep.

### 3.9 Timers: MCT and arch timer

- arch timer (`arm,armv8-timer`, `clock-frequency = 26 MHz`, no `always-on`): `arch_timer_c3stop`, so no SUSPEND_NONSTOP flag. timekeeping resets `cycle_last` and injects sleep time from rtc0 (S2MPS18, `RTC_HCTOSYS=y`). sched_clock suspend/resume re-reads the epoch.
- The FRC restart (§3.1) is the only hard requirement.
- MCT-bc G0 comparator: the clockevents suspend/resume path reprograms it. Add a `.resume` that re-runs the comparator init when TCON reads 0 after SLEEP.

### 3.10 GIC-400

- Upstream `irq-gic.c` saves and restores the distributor and CPU interface on `CPU_CLUSTER_PM_ENTER/EXIT` (T :708-722). The `cpu_pm` syscore drives it (T `kernel/cpu_pm.c:176-201`, `CONFIG_CPU_PM=y`). Nothing new is needed.
- GIC-400 has no GICR_WAKER. The `little-c2-apm-re.md` E1 step that reads GICR registers does not apply to this SoC.

### 3.11 Watchdog (`drivers/watchdog/s3c2410_wdt.c`)

- Upstream `s3c2410wdt_suspend` stops the WDT in the normal device-suspend phase (T :1244-1284). That leaves noirq, syscore, the EL3/APM handshake and early resume unguarded, and those are the dangerous parts.
- For crownlte (DT `samsung,keep-armed-in-suspend`):
  - suspend = ping + set timeout 60 s (max ≈ 82 s at 26 MHz/256/128) + keep the reset enabled;
  - the pm driver pings again immediately before CPU_SUSPEND;
  - resume = re-init (WTCON/WTDAT may be lost with PERIS) + ping.
- **[inf]** The WDT runs from OSCCLK, and the MIF sequencer turns TCXO off. So the WDT stops in real SLEEP and cannot fire during a 10 s sleep. If the SoC never actually powers down (stuck mid-way with clocks on), it fires and resets, and the shim bounce returns to Lineage.
- Read WTCNT before and after to confirm. The WDT cannot rescue "slept and never woke".

### 3.12 ACPM, cpufreq / devfreq, thermal, Mali

- **ACPM IPC:** no PM hooks in 4.9 or T. Requirements:
  - no IPC may be in flight at SYS_SLEEP entry;
  - after `dpm_suspend_noirq`, use only polled channels: PMIC ch2 already is;
  - log the ACPM ring front/rear before and after.
- **cpufreq / devfreq:** done. Suspend rates: LITTLE 1053 MHz, big 741 MHz; devfreq `xdf_suspend` forces the CAL resume rate (T `exynos9810-acpm-devfreq.c:478-515`). MIF suspend 1014 MHz as 4.9.
- **Thermal:** T `exynos9810_acpm_tmu.c` has only INIT/READ_TEMP. Add 4.9's `TMU_IPC_AP_SUSPEND` (0x04, rsvd = CP-net flag, 0 for now) at suspend and `TMU_IPC_AP_RESUME` (0x10) at resume on channel 12 (K49 `exynos_acpm_tmu.c:157-260`). The APM TMU plugin then stops polling during SLEEP. **[inf]** Skipping it may keep the TMU plugin running or block MIF-down.
- **Mali:** T vetoes suspend on `PM_SUSPEND_PREPARE` while the GPU is bound (`mali_exynos9810_platform.c:63-70, 210`). Keep the veto until kbase PM suspend plus G3D PD off/on (already proven at runtime, q30b/q31b) are wired into system PM. S1 vehicles must not bind Mali.

### 3.13 Touch, charger, sensors, modem, Wi-Fi

- **Touch (sec_ts y771):** no PM. For S1 do not bind it, or add `.suspend` = `disable_irq` + rails off (the existing off path, `power-gates.md` §1 step 7). That avoids an IRQ storm on gpa1-0 and leaks.
- **MAX77705 RO battery:** add `.suspend`/`.resume` that cancel and restart the 10 s poll. While charging, require an alarmtimer wake ≤ 60 s for the WDT kick (S3), or explicitly accept that charging stops during sleep (owner decision D4).
- **Modem, sensorhub, Wi-Fi:** later; see §2.4.

---

## 4. Risks

| # | Risk | Effect | Mitigation |
|---|---|---|---|
| R1 | **No wake: phone sleeps forever.** Only the PMIC RTC runs in SLEEP. The WDT clock stops. Any mistake in the PMIC IRQ, INT2M, EINT mask, `0x610` bit0, the EINT filter or pin PDN state leaves the phone asleep. | Not self-recovering. | **Owner present for the first S1 run.** Rollback is the Samsung force restart (Power + Vol-Down ~7-10 s). **[inf]** It is done by the PMIC manual-reset logic (S2MPS18 INT1 has an MRB bit), independent of the SoC. Confirm once on 4.9 beforehand. Prove every link without sleeping first (S0a: register readback; S1-pre: alarm fires as a normal IRQ while awake). |
| R2 | CPU_SUSPEND with entry 0 (the generic PSCI path) | EL3 jumps to 0 → hang | Custom finisher (§3.1); assert `entry == __pa_symbol(cpu_resume)` and log it before the SMC. |
| R3 | Arch counter frozen or reset after SLEEP | Timers never fire: a hang after "resumed" | FRC restart in `.enter` before syscore resume; log CNTPCT; the WDT is re-armed early and catches it. |
| R4 | LITTLE hotplug hits the EL3 LITTLE bug (cores stuck in EL3, as with C2) | Suspend never completes, or resume CPU_ON fails | S0b hotplug loops first. If LITTLE fails, sleep can still proceed: offlining is required, but a failed CPU_ON after resume leaves the phone on fewer cores; the WDT/restart guard handles a hang. |
| R5 | CP never booted: 4.9 refuses at `CP_STAT == 0`. Unknown whether the PMU or APM SLEEP sequence needs the CP handshake. | Possible hang in the APM SoC sequencer, or the MIF never goes down | S0a reads CP_STAT / CP_CTRL_NS / CENTRAL_SEQ_CP_STATUS. S0d (early wake) exercises the APM entry. Gate with `samsung,sleep-with-cp-off`. The WDT covers a mid-way stall. |
| R6 | Active DMA masters at entry (DECON scanout, Mali, UFS) | Bus LPI stall → SoC hangs mid-sequence | DECON stop hook, Mali veto, UFS suspended (or never bound in S1). |
| R7 | Pad retention never released | All GPIO-based buses dead after resume | `cal_pm_exit(8)` + pinctrl retention release; the self-test reads `0x14063060…31a0`. |
| R8 | UFS resume failure | I/O errors; with RW /data, possible fs damage | S1 without UFS; S1b with UFS read-only; extend the guard only for SSU power conditions; no RW /data until T5. Sync before suspend is default. |
| R9 | PMIC rails in sleep: PWREN drops and the PMIC applies per-rail sleep modes left by SBoot or 4.9 (6.12 writes none) | A rail needed in sleep (DRAM, ALIVE) could drop → lost RAM = cold boot | Same PMIC state as 4.9 after a warm reboot, which sleeps fine. Snapshot PMIC rail CTRL registers (RO) in S0a and compare against 4.9. |
| R10 | Charging stops during sleep (MAX77705 WDT, ~80 s) | Plugged phone does not charge overnight | S3: periodic wake ≤ 60 s while charging, or owner decision. |
| R11 | Serial (COM4) disappears during sleep | Lost live log | pstore console/pmsg; capture script reopens COM4. |
| R12 | Spurious immediate wake (pending EINT, CP SPIs floating) | Sleep aborts (early wake), safe | Log WAKEUP_STAT / EINT_PEND / GICD_ISPENDR; narrowed mask variant (`0x610` = 0xF7FF7E7E masks the modem bits). |

Data loss: there are no writes to UFS in S0/S1. ROM-RAM keeps the UFS write guard. The real risk starts only with RW /data (T5), and there it is no larger than any resume bug: sync before suspend, plus SSU cache flush.

---

## 5. Staged plan

All changes are DT-gated (`exynos-pm` node `status = "okay"` only in suspend DTBs). Nothing changes for other trials or for Lineage BOOT.

**Vehicles:** RECOVERY only (shim loader + delayed-UFS DTB/ramdisk, or ROM-RAM).

**Rollback for every stage:**
- restore bright RECOVERY 8763860c;
- the WDT + restart guard + shim bounce cover hangs while the SoC is awake;
- R1 needs the owner.

**Log prefix:** `e9810-pm:`, plus a step number on every line, so the last step before a hang shows in `pmsg-ramoops-0`/console pstore after the bounce.

### S0a: read-only snapshot (self-recovering, phone-idle OK)

- **Kernel:** `exynos9810-pm.c` probe in snapshot mode (DT `samsung,pm-snapshot-only`). Zero writes. ioremap PMU, GPIO_ALIVE, flexpmu dbg, APM SRAM ring.
- **Self-test lines:**
  - `e9810-pm: S0a CP_STAT=%#x CP_CTRL_NS=%#x SEQ_CP=%#x INFORM0..7=... WAKEUP_MASK 0x60c=%#x 0x610=%#x 0x614=%#x 0x618=%#x 0x644=%#x WAKEUP_STAT=%#x/%#x PAD_RET top=%#x spi=%#x uart=%#x ... IDLE_IP=...`
  - `e9810-pm: S0a flexpmu ap_sleep=%u mif_sleep=%u sw_flag=%#x mif_req=%#x`
  - `e9810-pm: S0a apm-ring rear=%u front=%u last8=[tag:val ...]`
  - `pmic selftest: INT1M..INT7M ... rail CTRL 0x1c..0x76` (exists)
- **Pass:** all mapped, CP_STAT recorded.
- **Also:** on the live 4.9 phone, with owner permission and read-only: `cat /sys/kernel/debug/exynos-flexpmu-dbg/lpm_count`, a `dmesg` after one screen-off sleep ("wake enabled for irq", "WAKEUP_STAT", "MIF down", "Resume caused by"), and `/sys/power/wakeup_count`. That gives the reference values.

### S0b: CPU hotplug (self-recovering)

- **Kernel:** the teardown hook (§3.2).
- **Self-test:** `samsung,hotplug-selftest`. Offline/online each of cpu7..cpu1 × 20 (big first, LITTLE last). Log INFORM, STATUS and the APM ring per step.
- **Lines:** `e9810-pm: S0b cpu%u off#%u inform=%#x status=%#x ok`, `... on#%u ok`, `S0b PASS 140/140`.
- **Pass:** all cycles. A LITTLE failure is recorded, and S1 then runs with `maxcpus` handling noted.

### S0c: `pm_test` (self-recovering; no firmware sleep)

- **Config:** `CONFIG_PM_DEBUG`, `CONFIG_PM_SLEEP_DEBUG`, `CONFIG_PM_TEST_SUSPEND` (diag only).
- **PID1:** for each level in `freezer devices platform processors core`, write the level to `/sys/power/pm_test`, then `mem` to `/sys/power/state`. The core level runs the full syscore path including `cal_pm_enter(8)`/`cal_pm_exit(8)`, with `.enter` stubbed by pm_test.
- **Lines:** `PM: suspend debug: Waiting for 5 second(s).`, `e9810-pm: syscore suspend mode 8 masks ...`, `e9810-pm: syscore resume exit ok`, `PM: suspend exit`.
- **Pass:** 5/5 levels; serial back; WDT never fires.

### S0d: early-wake dry run (self-recovering with high probability; run with S1 wake sources armed)

- **Kernel:** the full `.enter` with `test_early_wakeup = 1`: self-IPI to cpu0 before the SMC. A pending interrupt makes WFI fall through, so EL3/APM abort (APM `EarlyRet`).
- **Lines:**
  - `e9810-pm: enter fn=0xc4000001 state=0x03000000 entry=%#lx`
  - `e9810-pm: CPU_SUSPEND returned %d (early)`
  - `e9810-pm: earlywakeup seq ok`
  - `apm-ring: ... early_pm/EarlyRet`
  - `MIF blocked req=%#x`
- **Pass:** early return; FRC counting; all CPUs back.
- **Owner:** run when the owner can be present. If EL3 ignores the IPI, this becomes a real S1.

### S1: real SYS_SLEEP, PMIC RTC wake after 10 s, no other wake sources

- **Kernel:**
  - `exynos9810-pm.c` (full);
  - pinctrl retention data + 0x60C;
  - S2MPS18 IRQ domain + RTCA0 (policy D1);
  - MCT FRC resume helper;
  - WDT keep-armed;
  - TMU suspend/resume IPC;
  - DECON stop hook;
  - CPU hotplug hook.
- **DT (new `exynos9810-crownlte-delayed-ufs-suspend-s1-diag.dts`):**
  - `exynos-pm` {reg GPIO_ALIVE `0x14050000`, GICD_ISPENDR `0x10101200`; `samsung,sleep-mode-idx = <8>`; `samsung,sleep-psci-state = <0x03000000>`; mask regs `<0x610 0x614 0x618 0x644>` = `<0xd00f7e7e 0xffff00ff 0xffff00ff 0x0>` (4.9 verbatim); flexpmu-dbg `0x0204F800`; `samsung,sleep-with-cp-off`; `samsung,suspend-selftest = <10>`};
  - pmic `samsung,rtc-write-enable` + `samsung,rtc-alarm-irq`;
  - pmic-irq pinctrl on gpa0-1 with con-pdn/pud-pdn from DT49;
  - the WDT flag;
  - no Mali, no touch, no TEE, UFS never probed (no `CROWNLTE-UFS-PROBE-1`).
- **Self-test (kernel, ~100 s uptime, before the 120 s PID1 restart):**
  1. `rtc_set_alarm(now + 10 s)` with `enabled = 1`;
  2. read back A0 registers, INT2M = 0xFB, `0x60C` bit1 = 0;
  3. `pm_suspend(PM_SUSPEND_MEM)`;
  4. after return, a report.
- **S1-pre** (same image, earlier): a 3 s alarm while awake must deliver `RTCA0` through the IRQ path. Abort S1 if not.
- **Expected lines:**
  ```
  e9810-pm: S1 pre: alarm irq RTCA0 delivered in 3.0 s (INT2=0x04) PASS
  e9810-pm: S1 arm: alarm 10 s at <rtc>, wake EINT 0x60c=0xfffffffd 0x610=0xd00f7e7e CP_STAT=%#x
  e9810-pm: step 10 hotplug off cpu1-7 ok
  e9810-pm: step 20 syscore: cal_pm_enter(8) ok INFORM0=0x4 GRP2en=1 mifdn=%u
  e9810-pm: step 30 SMC fn=0xc4000001 state=0x03000000 entry=%#lx wdt=60s cntpct=%llu
  e9810-pm: step 40 resumed ret=0 cntpct=%llu frc=running mifdn %u->%u (MIF down) apsleep %u->%u
  e9810-pm: step 50 cal_pm_exit(8) ok PAD_RET top=1 spi=1 uart=1 ...
  e9810-pm: wake WAKEUP_STAT=0x00000001 EINT_PEND gpa0=0x02 -> irq <pmic> (s2mps18 RTCA0)
  e9810-pm: step 60 cpus online 8/8; TMU resume temps ...
  e9810-pm: S1 PASS slept %u ms (rtc %u s) apm-ring: ente_pm+ ent_soc+ ... SLEEP+++ ...
  ```
  `mifdn` increasing proves MIF really went down.
- **Vehicle:** delayed-UFS diag (no storage). Owner present. Capture: `run-trial.sh` with a capture that tolerates the COM4 drop. Read `pmsg-ramoops-0` after return.
- **Rollback:**
  - no wake → owner force restart (R1);
  - hang awake → WDT → shim bounce;
  - restore bright RECOVERY.
- **Then S1b:** the same with UFS probed (delayed-UFS + PROBE, read-only), `spm_lvl` 5, guard D3. Lines: `ufshcd: ... SSU POWERDOWN ok`, resume `link startup ok HS-G3 x2`, plus a read checksum of a fixed LBA range before and after.

### S2: power key

- **Kernel:** none new (gpio-keys upstream).
- **DT:** `gpio-keys` {power: gpa2-4, `linux,code = <116>`, `wakeup-source`, debounce as 4.9; vol up/down gpa0-3/0-4 without wake; Bixby gpa0-6 optional wake}; pin PDN states from DT49.
- **Self-test:** arm the RTC at +60 s as a backup. The owner presses power during sleep.
- **Lines:** `wake enabled for irq N (gpa2-4)`, `e9810-pm: wake ... EINT_PEND gpa2=0x10 -> gpio-keys KEY_POWER`, `S2 PASS woke by power key after %u ms`.
- **Vehicle:** delayed-UFS diag; owner present. Rollback as S1.

### S3: charger / USB

- **Kernel:**
  - MAX77705 IRQ demux (RO reads of INTSRC/CHG_INT/MUIC/CC; unmasking needs owner decision D2 on the poisoned write API);
  - `enable_irq_wake` on gpa1-6;
  - battery driver `.suspend`/`.resume` and a ≤ 60 s alarmtimer wake while charging for the WDT kick (D4);
  - dwc3 `usb_is_connect` → mode 12 + USBL2 masks (`0x610` = 0xD00D7E7E) + `enter_sleep_usbl2`.
- **Self-test:** sleep with the cable in → periodic wake every ≤ 60 s while charging. Then (owner) unplug and replug during sleep: `wake ... gpa1=0x40 -> max77705`.
- **Vehicle:** ROM-RAM (Lineage up, SystemSuspend driving autosleep). Also check that Android's `mem` maps to deep and that `/sys/power/wakeup_count` works.

### Later

- **Modem.** Needs the SIPC/modem_v1 driver, CP boot, mcu_ipc wake SPIs (already enabled in the SLEEP mask), the 4.9 CP_STAT gate restored, and `TMU_IPC_AP_SUSPEND` with the real CP-net flag. Also CP-call mode 10 for calls in progress.
- **Wi-Fi OOB** (gpa0-7) with the crownlte-wifi branch, BT host-wake, sensorhub, S Pen, fingerprint, hall.
- **Mali system PM** (remove the veto).
- **Display unblank** on resume.

---

## 6. Owner decisions needed

| ID | Decision | Recommendation |
|---|---|---|
| D1 | Allow PMIC writes INT2M `0x08` := 0xFB/0xFF (RTCA0 unmask) plus RTC alarm 0. | Yes. Exact 4.9 behaviour; it is the only alarm wake path. |
| D2 | MAX77705 interrupt mask writes for charger/MUIC wake. | Later (S3), list exact registers first. |
| D3 | UFS guard: allow SSU power conditions (SLEEP/POWERDOWN/ACTIVE) to W-LUN 0xD0. | Yes for S1b. |
| D4 | Charging during sleep: periodic wake for the WDT kick vs. accept stop. | Periodic wake (4.9 parity). |
| D5 | First S1 run needs the owner nearby (force restart is the rollback). | Required. |

## 7. Open questions (answered by S0a/S0d, not by more reading)

- PMU CP_STAT value on 6.12 and whether SLEEP works with the CP never booted.
- Whether the arch counter (CNTPCT) depends on the MCT FRC on 9810, and whether the FRC survives SLEEP.
- The meaning of WAKEUP_MASK bits 7/8/15/27 and of `0x614/0x618`. Not needed for S1, since 4.9 values are used verbatim.
- The exact APM tag order for SLEEP entry and exit. A post-S1 ring dump gives it directly.
