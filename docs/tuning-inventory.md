# crownlte (SM-N960F, Exynos 9810): performance and power tuning inventory, 4.9 → 6.12

Written 2026-11-06 (read-only research, no phone access). Goal: list every performance and
power-efficiency tuning that Samsung, the Lineage device tree and the ROM apply under the running
4.9 kernel, and show what the 6.12 port (`crownlte-integration-4`, b4f756bed) still lacks.

## Sources and abbreviations

| Tag | Path |
|---|---|
| K49 | gekko `/root/crownlte-hid-kernel` (HEAD 8665533d, running 4.9.337) |
| DT49 | `K49/arch/arm64/boot/dts/exynos/exynos9810-crownlte_eur_open_26.dts` (flattened, 14,722 lines) |
| CFG49 | `K49/out/.config` |
| T | 6.12 repo `/mnt/ssd/host-builds/kernel-upgrade/android16-6.12-port`, branch `crownlte-integration-4` (b4f756bed) |
| DTS612 | `T:arch/arm64/boot/dts/exynos/exynos9810-crownlte-delayed-ufs-diag.dts` (the board DT every trial includes) |
| CFG612 | `T:arch/arm64/configs/crownlte_android_defconfig` + effective build config `/dev/shm/build-integration-4/.config` |
| V / S | Vendor / system partition (mounted read-only at `rom-ram/testroot`, = `backups-20261103/partitions/{VENDOR,SYSTEM,ODM}.img`) |
| ECT | Exynos Characteristic Table: bootloader copy in `LUN-sdb.img` (boot LUN) at byte offset 0x4FC00 (`C:\Users\Patrick\tools\backup-full\20261105-215828\LUN-sdb.img`), "PARA" v`004x`, 40,936 bytes. 4.9 reads the RAM copy at 0xA0000000 (DT49:13025-13028 `ect { parameter_address = 0xa0000000; parameter_size = 204800 }`). Decoded for this inventory (decoder: `C:\Users\Patrick\.t3\scratch\2026-11-02-what-are-all-the-worst-50973f50\ect_decode.py`, follows `K49/drivers/soc/samsung/ect_parser.c`) |
| Q15 | 6.12 trial q15 serial log `C:\Users\Patrick\tools\trial-acpm-q15-20261103\serial-ready.bin` (cpufreq/devfreq self-tests print the CAL/FVMap/ECT tables of this chip) |

Status legend: **done** = in integration-4 and HW-verified; **ported** = in integration-4, not
HW-verified for this function; **partial**; **missing**; **n/a** = not needed on 6.12.
Effort: S ≤ 1 day, M = a few days, L = a week or more. Risk = what can go wrong on the phone.

---

## 1. Headline findings

1. **The 6.12 scheduler sees eight equal CPUs.** 6.12 has no `capacity-dmips-mhz` and registers
   no energy model (`T:arch/arm64/boot/dts/exynos/exynos9810.dtsi:65-128`). So there is no
   asymmetric capacity, no misfit migration and no EAS. 4.9 EMS gives LITTLE a capacity of about
   299 against big 1024 (DT49:4776-4795, `K49/kernel/sched/ems/energy.c:549-561,782-797`).
   Fixing this is a small change with the largest performance effect.
2. **6.12 never suspends the SoC.** There are no Exynos suspend ops and PSCI is v0.1, so
   Android's `mem` maps to s2idle. 4.9 enters SYS_SLEEP through exynos-pm (DT49:4872-4893).
   This is the largest standby-battery gap.
3. **6.12 idles in WFI only.** The C2 driver is in, but integration-4 arms no CPU outside the
   diagnostic self-test (`T:drivers/cpuidle/cpuidle-exynos9810.c:20-36`; no `samsung,c2-boot-cpus`
   in any DT). CPD and SICD are not ported. 4.9 runs C2 on all eight cores plus CPD and SICD
   (DT49:4748-4773, 4895-4935).
4. **The display refreshes on every TE.** 6.12 fetches about 1 GB/s at 60 Hz even on a static
   screen (`T:Documentation/crownlte/display-port.md:165-167`). 4.9 uses command-mode PSR and
   DECON hibernation after 3 idle frames (DT49:5264-5265, `K49/drivers/video/fbdev/exynos/dpu_9810/decon.h:75`).
5. **Why big is capped at 1794 MHz:** 4.9 itself only allows more than 1794 MHz when 1–2 big
   cores are online (hotplug governor "bigTurbo"); see section 2.
6. **LITTLE loses its top step on 6.12.** 4.9 almost certainly runs LITTLE up to **1950 MHz**
   (1081.25 mV). The 6.12 driver stops at the CAL maximum of 1794 MHz (section 2).
7. **The ECT holds the real thermal policy, and 6.12 ignores it.** For example, LITTLE does
   throttle on 4.9 (76 °C → 1690 MHz … ≥96 °C → 455 MHz). The 6.12 DT comment that "4.9 LITTLE
   never throttles" is wrong (section 3).
8. **No input/touch booster on 6.12.** On 4.9 the in-kernel input booster carries the touch
   boost (big ≥1066 MHz, LITTLE ≥832 MHz, EMS top-app boost). The Lineage power HAL only adds
   LITTLE ≥1456 MHz for 100 ms on INTERACTION (V `etc/powerhint.json:35-43,98-103`).

---

## 2. Big-cluster cap: why 4.9 runs above 1794 MHz, and when

Measured CAL/FVMap tables of this chip (Q15 lines 713-731):

- **CPUCL0 (LITTLE):** CAL levels 2002, **1950**, 1794, 1690, 1456, 1248, 1053, 949, 832, 715,
  598, 455, 299, 208 MHz. `cal_max` = 1794. Voltages: 1950 @ 1081.25 mV, 1794 @ 1006.25 mV.
- **CPUCL1 (big):** CAL levels 2886 … 2704, 2652, 2496, **2314** @ 987.5 mV, 2106, 2002, 1924,
  1794 @ 825 mV … 598. `cal_max` = 2704.
- **MIF:** ECT up to 2093/2094, CAL max 1794. **INT:** CAL max 534 (DT asks for 667).

How 4.9 sets the limits:

1. **The table ceiling comes from DT `max-freq`, which replaces the CAL maximum** (`K49/drivers/cpufreq/exynos-acme.c:1114-1124`):
   - big `max-freq = 2314000`; LITTLE `max-freq = 1950000` (DT49:10513-10556).
   - 4.9 `cal_dfs_set_rate()` has no range check (`K49/drivers/soc/samsung/cal-if/cal-if.c:49-66`).
   - So the 4.9 tables are LITTLE 455–1950 and big 650–2314. The ECT THERMAL "LITTLE" function's
     top range is also 1950000 (section 3), which confirms 1950 is intended.
   - 6.12 instead uses `min(cal_max, dt_max)` and `samsung,max-freq-cap` (`T:drivers/cpufreq/exynos9810-acpm-cpufreq.c:756-769`).
     Its `cal_dfs_set_rate()` refuses rates above `vclk->max_freq` (`T:drivers/soc/samsung/cal-if/cal-if.c:115-116`).
     Q15: LITTLE 455–1794 (10 of 14 levels), big 650–1794 (11 of 21).
2. **`policy-max = 1794000`** (DT49:10551) is written to `policy->max` once at init (`exynos-acme.c:817-828`).
   It is not the steady-state cap, because the user policy keeps the table maximum.
3. **The hotplug governor ("bigTurbo") sets the cap.** DT49:10442-10460; `K49/drivers/soc/samsung/exynos-hotplug_governor.c`; `CONFIG_EXYNOS_HOTPLUG_GOVERNOR=y` (CFG49:4661):
   - It turns itself on 40 s after boot (`:30`, `:985-990`, `:1217-1218`).
   - Its CPUFREQ_ADJUST notifier caps big at `maxfreq_table[mode]` (`:172-205`):
     single = 2314, dual = 2314, triple = 1794, quad = 1794 MHz.
   - It also refuses a big core coming online if the current frequency exceeds the next mode's
     maximum (`:124-167`).
   - Mode choice (`:595-750`), sampled every 100 ms (single/dual/quad_change_ms):
     - per-CPU `util_avg` ≥ 640 counts as heavy on big (≥ 200 on LITTLE), ≥ 113 as active on big;
       LITTLE idle threshold 59;
     - "system busy" = both clusters busier than `cl_busy_ratio` 65 % → QUAD;
     - one heavy big thread and no heavy LITTLE thread → SINGLE (one big core online);
     - two heavy big threads, or one big and one LITTLE → DUAL.
   - Result: **2314 MHz only while ≤ 2 big cores are online. With 3–4 big cores online, big
     stays ≤ 1794 MHz.**
4. **Boot:** `pm_qos-booting = 1794000` pins big min = max = 1794 MHz, and LITTLE at its table
   maximum (1950), for the first 40 s (`exynos-acme.c:903-921`).
5. **OCP:** DT `exynos-ocp { max-freq-wo-ocp = 1794000 }` (DT49:4946-4955) shows that 1794 MHz is
   the electrical limit without over-current protection.
   - `CONFIG_EXYNOS_OCP` is **not set** (CFG49:4664), so on 4.9 the 2-core rule above is the
     only protection.
   - Do not run all four big cores above 1794 MHz on 6.12.
6. **Thermal limits that apply to the turbo:**
   - BIG uses power_allocator with ECT PIDTM: switch-on 60 °C, control 83 °C, sustainable 2000 mW,
     k_po 100, k_pu 300, k_i 20, i_max 1000, integral_cutoff 23.
   - Thermal hotplug offlines big cores at 96 °C and brings them back at 91 °C
     (`K49/drivers/thermal/samsung/exynos_tmu.c:1155-1185,1436-1525`).
   - ECT `limited_frequency` is 2496000, applied through the ACPM TMU status 2 path (`:863-881`).
     It cannot bind below the 2314 ceiling.
   - Hardware trip: 115 °C (bootloader-armed rise7 at 112–115 °C).

**Verify on the 4.9 phone (read-only):**
- `cat /sys/devices/system/cpu/cpufreq/policy{0,4}/scaling_available_frequencies` (expect a 1950
  top for LITTLE and 2314 for big);
- `cat /sys/power/cpuhotplug/governor/enabled` (expect 1);
- `scaling_max_freq` of policy4 under a 1-thread and a 4-thread load (expect 2314 / 1794);
- `cpu*/online` while it runs.

---

## 3. ECT thermal policy (decoded from the device's own ECT)

4.9 overrides the DT trips with these values (`exynos_tmu.c:1380-1525`):
- non-power_allocator zones take temperature → max-frequency ranges from the THERMAL block,
  through `tbps[i].value` → `get_cooling_level` (`K49/drivers/thermal/of-thermal.c:201`);
- power_allocator zones take trips and PID parameters from the PIDTM block.

The DT `<0 0>` cooling maps for LITTLE (DT49:10768-10886) are placeholders.

| ECT block / function | Values |
|---|---|
| THERMAL `LITTLE` (step_wise) | 20–75 °C: 1950 MHz; 76–80: 1690; 81–85: 1456; 86–90: 1248; 91–95: 1053; 96–100: 455; 101–114: 455; 115: 455 |
| THERMAL `ISP` (isp_cooling, fps cap) | 20–94 °C: 60; 95–99: 60; 100–104: 15; 105–109: 5; 110–115: 5 |
| PIDTM `BIG` (power_allocator) | trips 20 (cold), 60 (switch-on), 83 (control), 90, 100, 105, 110, 115 (hot) °C; k_po 100, k_pu 300, k_i 20, i_max 1000, integral_cutoff 23, p_control_t (= sustainable power) 2000 mW, hotplug_out 96 °C, hotplug_in 91 °C, limited_frequency 2496000 |
| PIDTM `G3D` (power_allocator) | trips 20, 80 (switch-on), 88 (control), 95, 100, 105, 110, 115 °C; k_po 60, k_pu 200, k_i 10, i_max 400, integral_cutoff 8, sustainable 2000 mW |
| Other ECT blocks | PLL, DVFS, ASV, GEN, NEWTIME, MINLOCK (6.12 already uses DVFS/ASV/MINLOCK through cal-if/fvmap and `T:drivers/soc/samsung/ect_parser.c`; `dvfs_cpucl0` has no MINLOCK entry, Q15:747) |

6.12 has the ECT parser and the THERMAL/PIDTM block handles (`ect_get_block(BLOCK_AP_THERMAL|BLOCK_PIDTM)`),
so the TMU driver can read these values at probe instead of hard-coding them.

---

## 4. Inventory

### 4.1 CPU frequency

| Area | What 4.9 / vendor does (values, evidence) | Lives in | Applies unchanged on 6.12? | 6.12 status | Effort / risk |
|---|---|---|---|---|---|
| LITTLE range | 455–1950 MHz. DT `min-freq 455000`, `max-freq 1950000` replaces CAL max (DT49:10513-10541, exynos-acme.c:1114-1124); 1950 @ 1081.25 mV (Q15:714) | kernel DT + driver | No: 6.12 takes `min(cal_max 1794, dt_max)` and CAL refuses > max | **partial** (455–1794) | S / medium: runs a level above the ASV max, as 4.9 does daily; relax the cpucl0 range check to DT max-freq only for ECT levels with an FVMap voltage |
| big range and turbo | Table 650–2314 (DT max-freq overrides CAL 2704); hotplug governor caps 2314 with 1–2 big cores online, 1794 with 3–4 (section 2) | DT + `exynos-hotplug_governor.c` | No | **partial**: hard cap 1794 (`samsung,max-freq-cap`, DTS612:261) | M / **high**: >1794 on 3–4 cores exceeds the 4.9 electrical envelope (OCP off); needs thermal parity first |
| Initial policy-max | big `policy-max 1794000` at init (DT49:10551, exynos-acme.c:817-828) | DT + driver | yes (same property) | **done** (`policy-max` honoured, `T:…acpm-cpufreq.c:772-777`) | — |
| Boot QoS | 40 s min = max = table max (LITTLE 1950) and big `pm_qos-booting 1794000` (exynos-acme.c:903-921, DT49:10553) | DT + driver | n/a (no 6.12 equivalent) | **missing** for CPU (MIF/INT boot QoS ported) | S / low (heat for 40 s) |
| CPU → MIF constraints | `dm-constraints mif-perf` tables (e.g. big 1794 → MIF 1794, 1469 → 845) + ECT MINLOCK skew (DT49:10526-10571) | DT + DVFS manager | yes (same tables) | **done** (Q15:730-731; v21a no skew warnings) | — |
| User frequency control (UFC) | `cpufreq-ufc` min/max-limit maps, `user-default-qos` 715000/962000 (DT49:10484-10509); `/sys/power/cpufreq_{min,max}_limit`, `cpufreq_table` (V init.samsungexynos9810.rc:448-453) | DT + `exynos-ufc.c` | No: nodes absent | **missing** | S / low. Only user: `V/lib64/libbauthserver.so` (fingerprint server) references `/sys/power/cpufreq_max_limit`; unverified whether it boosts or just reads |
| schedutil tuning | freqvar-tune: up/down rate limit 5000 µs, boost 0, upscale ratio 5, both domains (DT49:10415-10434); `schedutil_gov` domain1 `expired_time 80`, `qos_min_class` (DT49:10462-10482); rc only chowns `policy*/schedutil/freqvar_*` (rc:206-221) | DT + `ems/freqvar_tune.c` | No: freqvar nodes absent (no writer, harmless) | **different**: 6.12 rate limit = 750 µs (transition 500 µs × 1.5, `T:…acpm-cpufreq.c:79-83`, Q15:712) | S / low: consider `rate_limit_us` 2000–5000 for battery; each step is an ACPM IPC of ~0.4 ms |
| OCP | `exynos-ocp` `max-freq-wo-ocp 1794000`, release threshold 50, duration 15 (DT49:4946-4955) but `CONFIG_EXYNOS_OCP` not set (CFG49:4664) | DT | n/a | n/a (inactive on 4.9 too) | — (explains the 1794 rule) |
| CPU suspend/resume rate | CAL resume rate (acme `resume_freq`) | driver | yes | **done** (suspend_freq LITTLE 1053, big 741, Q15:712,724) | — |

### 4.2 Scheduler (EMS/EAS, capacity, schedtune/uclamp, boosts)

| Area | What 4.9 / vendor does | Lives in | Applies unchanged? | 6.12 status | Effort / risk |
|---|---|---|---|---|---|
| CPU capacity | EMS `capacity-mips` LITTLE 272, big 785; cap = f·mips·1024/(f_max_big·mips_big) → LITTLE@1950 ≈ 299, big@2314 = 1024; `topology_set_cpu_scale()` (DT49:4776-4795, ems/energy.c:549-561,782-797) | DT + EMS | No | **missing**: no `capacity-dmips-mhz`, all CPUs 1024 | S / low. Add `capacity-dmips-mhz = <272>` (A55) / `<785>` (M3); 6.12 then computes 355/1024 with the 1794 caps, 299/1024 at 1950/2314 |
| Energy model | `power-coefficient` LITTLE 160, big 800 (AArch32 "s": 130/506); P[mW] = C·f[MHz]·V[mV]²/10⁹ (ems/energy.c:508-541). Examples with this chip's FVMap: big 2314 = 1805 mW, 1794 = 977 mW, 650 = 228 mW; LITTLE 1950 = 365 mW, 1794 = 291 mW, 455 = 26 mW | DT + EMS | No | **missing**: `ENERGY_MODEL=y` but nothing registers one | S–M / low. `em_dev_register_perf_domain()` in `exynos9810-acpm-cpufreq.c` with `cal_volt[]`; the same formula as DT `dynamic-power-coefficient` |
| EAS | `DEFAULT_USE_ENERGY_AWARE=y`, `SCHED_EMS=y` (CFG49:172-173) | defconfig | — | **missing** (needs capacity + EM; ACK EAS turns on by itself with schedutil) | comes with the two rows above |
| Ontime migration | LITTLE→big up at 42 % (AArch32 30 %), big lower boundary 7 % (10 %), coverage 100/80 (DT49:4799-4818) | DT + EMS | No | **n/a** (mainline misfit/capacity margin ~80 %) once capacity exists | — |
| Load-balance trigger | overutil level0 ratios 25 (0-3) / 30 (4-7), level1 80 (DT49:4820-4832) | DT + EMS | No | n/a (mainline LB) | — |
| Fluid RT (FRT/ERT) | coverage 30/30, active 25/5 (DT49:4834-4845); rc writes `/sys/kernel/ert/rt_ratio_for_freq 0` (rc:232), chowns `ert/*` (rc:224-231) | DT + EMS + vendor rc | No: `/sys/kernel/ert` absent (write fails, harmless) | n/a (mainline RT capacity awareness + uclamp) | — |
| prefer-perf service | top-app boost=1 → prefer CPUs 4-7 then 0-3; light-task threshold 10 (6) (DT49:4860-4868); stune `prefer_perf` chowned (rc:465-473) | DT + EMS | partly | **ported**: `prefer_perf` file exists (`T:kernel/sched/stune_compat.c:203`); behaviour not checked against EMS | S / low (check semantics after capacity lands) |
| schedtune groups | vendor `cgroups.json` schedtune `/dev/stune`; task_profiles: MaxPerformance→top-app, HighPerformance→foreground, HighEnergySaving/ServicePerformance/Dex2oat→background, CpuPolicySpread→prefer_idle 1 (V etc/cgroups.json, etc/task_profiles.json); system init.rc makes fg/bg/top-app/rt, nnapi-hal `schedtune.boost 1`, `prefer_idle 1` (S init.rc:121-124,234-241); vendor `bg_cached`, `bg_abnormal` (rc:704-712). No rc or HAL sets a top-app boost | vendor/system rc + JSON | yes | **ported** (`CONFIG_CGROUP_SCHEDTUNE` compat, 305 lines; boost via `android_rvh_cpu_util_cfs_boost`) — not yet run under Android | — / verify in ROM-in-RAM with `/dev/stune/*/tasks` |
| uclamp | none on 4.9. System init.rc: `cpuctl/nnapi-hal/cpu.uclamp.min 1`, `latency_sensitive 1` (S init.rc:214-215); system `CpuPolicySpread` sets `cpu.uclamp.latency_sensitive` (overridden by the vendor profile) | system rc | yes (nodes exist: `UCLAMP_TASK_GROUP`, ACK `latency_sensitive` in `T:kernel/sched/core.c`) | **done** (config) | S / low: optional top-app `cpu.uclamp.min` as a 6.12-only replacement for the EMS boost |
| Input-driven global boost | input booster calls `kpp_request(STUNE_TOPAPP/STUNE_FOREGROUND, 1)` (`K49/include/linux/input/input.h:66-90`) | kernel | No | **missing** (see 4.8) | in 4.8 |
| CFS sysctls | `sched_latency_ns 10000000`, `sched_wakeup_granularity_ns 2000000`, `sched_child_runs_first 0`, `sched_tunable_scaling 0` (S init.rc:333-336) | system rc | No: EEVDF has no such sysctls (writes fail, harmless) | n/a | — |
| cpusets | top-app 0-7, foreground 0-2,4-7, background 0-1, system-background 0-2, restricted 0-2, cached/abnormal 0-3 (rc:685-731) | vendor rc | yes (`CPUSETS_V1=y`) | **done** (config) | — |
| CPU bandwidth groups | `/dev/cpuctl/bg_cached`, `bg_abnormal` `cfs_period_us 20000`, `cfs_quota_us 10000` (rc:664-682) | vendor rc | yes (`CFS_BANDWIDTH=y`) | **done** (config; only Samsung framework uses them) | — |
| Tick / preemption | HZ=100, NO_HZ_IDLE, PREEMPT, RT_GROUP_SCHED=y, HRTICK (CFG49:93-118,159,505-514) | defconfig | — | HZ=250, NO_HZ_IDLE, PREEMPT_DYNAMIC, no RT_GROUP_SCHED (CFG612) | — (ACK defaults, fine) |

### 4.3 cpuidle, CPD, SICD

| Area | What 4.9 does | Lives in | Applies unchanged? | 6.12 status | Effort / risk |
|---|---|---|---|---|---|
| C2 per core | PSCI 0x10000; boot cluster 70/160/2000 µs, big 235/220/3500 µs, all 8 CPUs (DT49:4748-4773); `ARM64_EXYNOS_CPUIDLE=y`, menu governor (CFG49:677-687) | DT + cpuidle-exynos64 + exynos-powermode | DT values copied (`T:exynos9810.dtsi:140-160`) | **partial**: driver ported; **no CPU armed** outside the self-test (no `samsung,c2-boot-cpus`); big C2 HW-verified (q44, ~4000 cycles); LITTLE C2 loses a core in EL3 with 3 LITTLE cores cycling (q22b/q40/q43, c2diag-a/b) → policy ≤ 2 LITTLE (`crownlte-little-c2-soak` 6f709c3b6) | S to arm big (`c2-boot-cpus = 0xf0`) / medium (a lost core hangs, WDT recovers); LITTLE: L / high |
| CPD (cluster power-down) | `cpd_enabled 1`, `cpd_residency 10000` µs (DT49:4895-4898) | DT + exynos-powermode | No | **missing** (opt-in code a1660042b on `crownlte-cpuidle`, not in integration-4) | M / medium-high |
| SICD (system idle clock-down) | `sicd_enabled 1`, `sicd_residency 10000`; idle-ip list (~80 IPs: i2c/spi/ufs/usb/mailbox/pcie/PDs…), `SYS_SICD` mode 0 ref-idle-ip mask, `fix-idle-ip acpm_dvfs` (DT49:4895-4935) | DT + exynos-powermode + every driver's idle-ip votes | No | **missing** | L / high (every active IP must veto) |
| Tick broadcast | 4.9 MCT local timers | driver | — | **done** (MCT broadcast, q21b stage A) | — |

### 4.4 Hotplug / core control

| Area | What 4.9 does | Lives in | Applies unchanged? | 6.12 status | Effort / risk |
|---|---|---|---|---|---|
| Boot hotplug lock | `cpu_hotplug { boot_lock_time 40; fast_hp_cpus "5-7" }` (DT49:10436-10440) | DT + exynos-cpu_hotplug | No | n/a | — |
| bigTurbo governor | section 2; thresholds big_heavy 640, lit_heavy 200, big_idle 113, lit_idle 59, ldsum off, cl_busy 65 %, 100 ms; freq 2314/2314/1794/1794 (DT49:10442-10460); rc chowns `/sys/power/cpuhotplug/*` (rc:476-502) | DT + driver + vendor rc | No: nodes absent (no writer in Lineage) | **missing** | M / high. Don't port real hotplug (Android 16 dislikes CPUs going offline); implement a frequency policy instead: max 2314 while ≤ 2 big CPUs are busy, else 1794 |
| Thermal hotplug | big cores out at 96 °C, back at 91 °C (ECT PIDTM; exynos_tmu.c:1155-1185) | ECT + TMU driver | No | **missing** | S once big > 1794 exists / low (an equivalent freq_qos cap is enough) |
| Online-CPU QoS users | `PM_QOS_CPU_ONLINE_MIN/MAX` from hpgov, fimc-is camera, UFC | drivers | No | n/a | — |

### 4.5 Bus devfreq and PM QoS

| Area | What 4.9 does | Lives in | Applies unchanged? | 6.12 status | Effort / risk |
|---|---|---|---|---|---|
| MIF | `freq_info <2093000 208000 1014000 208000 2093000 2093000>` (initial, floor, suspend, min, max, reboot), boot 40 s @ 1794000, down-delay 20 ms, simple-interactive (QoS only), `use_tmu` (DT49:9234-9256); CAL max 1794 (`exynos9810_bus_mif.c:241-261`) | DT + driver | yes (values copied) | **done** (`T:…crownlte-clocks.dtsi:86-99`; q15 421 ↔ 1794, 8 levels) | — |
| INT | `<667000 178000 107000 107000 667000 667000>`, boot 40 s @ 667000 (DT49:9257-9278); CAL max 534 | DT + driver | yes | **done** (q15: 6 levels 107–534, floor 178) | — |
| MIF → INT lock | ECT MINLOCK (2093/2002/1794 → INT 400, 1539/1352 → 336, ≤1014 → 107; Q15:745) + FVP upload | ECT + ACPM | yes | **done** | — |
| DISP | `<640000 200000 640000 200000 640000 640000>`, boot 640000 (DT49:9299-9318) | DT + driver | No (no node) | **missing** (stays at the bootloader rate) | S–M / low |
| FSYS0 (UFS/PCIe bus) | `<336000 73000 336000 73000 336000 336000>` (DT49:9399-9418); UFS requests 200 MHz while active (`ufs-pm-qos-fsys0 200000`, `ufs-exynos.c:696-708,1186`) | DT + driver | No | **missing** (bootloader rate; probably 336 MHz, so fast but not saving) | S–M / low |
| INTCAM / CAM / IVA / SCORE / AUD | INTCAM 690/630, CAM 690/590, IVA & SCORE 34 (max 534), AUD 295 (max 1180) (DT49:9279-9398) | DT + driver | No | **missing** (only needed with camera / NPU / audio) | S each, later |
| PM QoS requesters | 4.9 requesters (grep of `pm_qos_add_request`): DECON BTS (DISP 400 MHz, INT 534, MIF 1794/1352 in high-bandwidth modes, `dpu_9810/bts.c:332-417`), UFS (FSYS0), Wi-Fi DHD (BUS_THROUGHPUT, CPU_FREQ_MIN), modem shmem (MIF, INT, CL0/CL1 min), ABOX audio (AUD, INT, MIF, CL0/CL1 min), fimc-is camera (CAM, INTCAM, CL limits, online CPUs), G2D (INT), Mali (MIF, CL limits), input booster, Trustonic sec-os-booster (CL1 min), DW-MMC (INT) | drivers | No | **partial**: only cpufreq (MIF floor) and Mali (MIF floor, `T:drivers/gpu/arm/exynos9810/mali_exynos9810_dvfs.c:248-256`) | M overall / low. The display one matters first: continuous scanout with no MIF floor from DECON |
| Vendor nodes | rc chowns `17000010.devfreq_mif/devfreq/…/scaling_devfreq_min` and `available_frequencies` for radio (rc:454-457,508-509) | vendor rc | No: 6.12 device is `devfreq-mif`, no `scaling_devfreq_min` | **missing** (no vendor binary references it in V/bin, V/lib64) | — |

### 4.6 GPU (Mali-G72 MP18)

| Area | What 4.9 does | Lives in | Applies unchanged? | 6.12 status | Effort / risk |
|---|---|---|---|---|---|
| DVFS table | 572/546/455/338/299/260 MHz; min/max util 78/100, 78/99, 78/85…; down-stay 5,1,1,1,1,1; MIF floor 1794/1794/1539/1352/1014/421 (DT49:5055-5062) | DT + gpex | yes (copied) | **done** (`T:exynos9810.dtsi:403-410`; q27b readback; Mali rendering in ROM-RAM v16) | — |
| Governor | interactive `<338000 94 0>`, polling 30 ms, interval 5 (DT49:5053-5054,5078,5086) | DT + gpex | yes | **done** | — |
| Clock limits & boosts | max 572000, max_limit 572000, min 260000, start 260000; `mo_min_clock 455000`, `boost_egl_min_lock 1872000`, `vk_boost_max_lock 338000`, `vk_boost_mif_min_lock 1794000`, `pmqos_mif_max_clock 1794000` (base 572000), `cl_dvfs_start_base 455000`, `MALI_EXYNOS_CL_BOOST=y`, BTS MO (DT49:5065-5098, CFG49:3226-3237) | DT + gpex | No | **partial**: limits yes; boosts, MO, CL boost not ported (`T:Documentation/crownlte/mali-port.md:41-43,120`) | M / low |
| GPU power | `inter_frame_pm 1` (IFPO), `runtime_pm_delay_time 50`, `default_wakeup_lock 1`, `MALI_EXYNOS_RTPM=y` (DT49:5079-5085) | DT + gpex | No | **partial**: G3D runtime power-domain control exists, enabled only in the combined diag DT (`samsung,g3d-power-control = "runtime"`, off-delay 150 ms); IFPO missing | M / medium (G3D rail via ACPM + PMIC) |
| GPU thermal | power_allocator, ECT PIDTM G3D (section 3), `gpu_temp_throttling 572/546/455/338/260/260`, `gpu_power_coeff 625`, `gpu_tmu_control 1` (DT49:5072-5076,10887-10977) | ECT + DT + gpex | No | **partial**: step_wise at 88 °C through `thermal-gpufreq-0` (DTS612:170-191) | M / low (power model + IPA) |
| Power HAL GPU nodes | `/sys/kernel/gpu/gpu_max_clock` {572000, 546000, 455000}, `gpu_min_clock` {455000, 338000, 299000, 260000} (powerhint.json:3-24) | HAL config | yes | **done** (`mali_exynos9810_dvfs.c:540-625`, RW) | — |
| Vendor-only nodes | rc chowns `17500000.mali/dvfs_{min,max}_lock`, `dvfs_table`, `/sys/kernel/gpu/gpu_info` (rc:516-524) | vendor rc | No (device path differs) | **missing** (no vendor binary references them) | — |

### 4.7 Thermal

| Area | What 4.9 does | Lives in | Applies unchanged? | 6.12 status | Effort / risk |
|---|---|---|---|---|---|
| BIG zone | power_allocator, polling 50/1000 ms; ECT PIDTM trips and PID values (section 3); cooling = big domain full range (DT49:10675-10767) | ECT + DT + TMU driver | No | **partial**: step_wise on 83 °C passive, critical 110 °C (DTS612:125-153) | M / low (IPA needs the EM from 4.2) |
| LITTLE zone | ECT THERMAL ladder 1950 → 1690 (76 °C) → 1456 (81) → 1248 (86) → 1053 (91) → 455 (96+) | ECT + TMU driver | No | **missing**: the 6.12 DT omits LITTLE throttling (DTS612:112-124 comment is wrong) | S / low (DT trips + cooling-maps by level, or read ECT at probe) |
| G3D zone | power_allocator, ECT PIDTM G3D (control 88 °C) | ECT + DT | No | **partial** (step_wise 88 °C) | see 4.6 |
| ISP zone | ECT fps cap 60 → 15 (100 °C) → 5 (105 °C) through `isp_cooling` | ECT + camera | No | **n/a until camera**; stub `thermal-isp-0` for the HAL (`T:drivers/thermal/samsung/exynos9810_acpm_tmu.c:376-392`) | — |
| Thermal hotplug / limited freq | 96/91 °C hotplug; 2496 MHz on ACPM status 2 | ECT + TMU | No | **missing** (only needed above 1794) | S / low |
| Sensor IRQ vs polling | LITTLE, ISP and G3D zones are interrupt-driven (polling 0); TMU DT `tmu_default_trip_temp 115` (DT49:10576-10670) | DT | No | **different**: every zone polls at 1000 ms (4 wakeups/s) | M / low (small idle cost) |
| Hardware trip | bootloader arms THERM_TRIP rise7 112–115 °C | firmware | yes | **done** (verified 0f6eb9410) | — |
| Thermal HAL | pixel HAL monitors only (no `BindedCdevInfo`): BIG/LITTLE/G3D/ISP SEVERE 96 °C, SHUTDOWN 115 °C; `ac` EMERGENCY 60 °C (Monitor); `battery` SHUTDOWN 60 °C; cdevs thermal-cpufreq-0/1, thermal-gpufreq-0, thermal-isp-0 (V etc/thermal_info_config.json) | HAL config | yes, if the names exist | **done/ported**: zones, cooling devices with 4.9 names (`T:…acpm-cpufreq.c:602-627`), ISP stub; `ac`/`battery` come from max77705-ro + SoC ADC (`T:Documentation/crownlte/max77705-battery.md`) | — |
| dexopt thermal cutoff | `dalvik.vm.dexopt.thermal-cutoff=2` (build.prop) | system prop | yes (uses HAL status) | done | — |

### 4.8 Input and touch boosters

| Area | What 4.9 does | Lives in | Applies unchanged? | 6.12 status | Effort / risk |
|---|---|---|---|---|---|
| In-kernel input booster | Built when `CONFIG_INPUT_BOOSTER` is **unset** (`K49/drivers/input/input.c:32,426-700`, `include/linux/input/input.h`). On each event it sets CL1-min (`cpu_freqs`), CL0-min (`kfc_freqs`), MIF/INT min, and an EMS top-app/foreground boost (`hmp_boost`). DT49:4315-4470: **TOUCH** head 130 ms big 1066/LITTLE 832 → tail 500 ms big 858/LITTLE 832; **MULTITOUCH** 1000 ms 1066/832 → 500 ms 858/832; **KEY** 500+500 ms big 1066; **TOUCHKEY** 200 ms big 1066; **KEYBOARD** 130+130 ms 1066/832; **MOUSE** 130 ms 1066/832 → 500 ms 858/832; **MOUSE WHEEL** 130 ms 1066/832; **PEN HOVER** 130 ms 1066/832 → 500 ms 858/832; **PEN** 200 ms 1170/832 → 600 ms 858/832; **KEY_TWO** 700+700 ms big 1469/LITTLE 1053. MIF/INT 0 everywhere | kernel driver + DT | No | **missing** | M / low. An input handler with `freq_qos` on policy0/policy4 + stune/uclamp boost; reuse the DT tables |
| Booster sysfs | rc chowns `/sys/class/input_booster/{level,head,tail,touchkey/*}` (rc:526-547) | vendor rc | No | missing (no vendor binary uses them) | — |
| Power HAL INTERACTION | `CPULittleClusterMinFreq` `cpu0/cpufreq/scaling_min_freq` 1456000 for 100 ms (powerhint.json:35-43,98-103) | HAL config | **yes** (1456000 is in the 6.12 LITTLE table) | **done** | — |
| Touch-driver latency QoS | sec_ts `PM_QOS_CPU_DMA_LATENCY` (`drivers/input/touchscreen/sec_ts/*`) | driver | No | missing (touch stage 1 is plain evdev) | S / low |

### 4.9 Display and refresh

| Area | What 4.9 does | Lives in | Applies unchanged? | 6.12 status | Effort / risk |
|---|---|---|---|---|---|
| Command-mode PSR | DECON-F `psr_mode 2`, `trig_mode 0` (HW/TE), `dsi_mode 0` (DT49:5252-5267): frames are sent only on update | DT + dpu_9810 | — | **missing**: trigger left unmasked → refresh on every TE, ~1 GB/s (`display-port.md:165-167`) | M / medium |
| DECON hibernation | after `DECON_ENTER_HIBER_CNT` = 3 idle frames: DPU power off, DECON/DSIM clocks off, panel on (`decon.h:75,308`; `CONFIG_EXYNOS_HIBERNATION_THREAD=y`, CFG49:3363) | driver | — | **missing** | L / medium (DSIM ULPS, DPU power domain) |
| DISP/INT/MIF from DECON | BTS: DISP 400 MHz, INT 534, MIF 1794 or 1352 depending on the window bandwidth (`dpu_9810/bts.c:332-417`); `EXYNOS9810_BTS=y` | driver | No | **missing** (`display-port.md:261`, S2-10) | M / low |
| Composition | HWC client target AFBC (`EXYNOS_AFBC=y`); DPP AFBC decode | driver + vendor HWC | — | **partial**: AFBC rejected → linear client target + kernel bounce copy (ROM-RAM v12: ~11.5 ms per copy); `crownlte-dpu-scale` working on composition parity | M / medium (CPU and MIF cost on every frame until done) |
| Refresh and SF properties | fixed 60 Hz; `ro.surface_flinger.max_frame_buffer_acquired_buffers=3`, `game_default_frame_rate_override=60`, `use_context_priority=true`, `supports_background_blur=1`; `vendor.hwc.exynos.vsync_mode=0`; `vendor.powerhal.disp.idle_support=false` (build.prop) | system/vendor props | yes | done (kernel-agnostic) | — |
| Multi-resolution / DSU | `EXYNOS_MULTIRESOLUTION=y` (CFG49:3373) | driver | — | missing (S2-10) | M / low |
| Green-screen fix | `init.greenfix.rc`: writes `…/panel_drv@001/…/lcd/panel/fix_green_screen` 0/1 | vendor rc | path must match | **partial** (`fix_green_screen` in `T:drivers/video/fbdev/exynos/dpu_9810/panel_bright.c`; path not checked) | S |

### 4.10 Memory, LMK, swap

| Area | What 4.9 / vendor does | Lives in | Applies unchanged? | 6.12 status | Effort / risk |
|---|---|---|---|---|---|
| zram | `zram0` 2 GiB, `max_comp_streams=8` (V fstab); compressor **zstd** (4.9 `default_compressor = "zstd"`, `K49/drivers/block/zram/zram_drv.c:44`) | fstab + kernel | partly: 6.12 default is **lz4** (`ZRAM_DEF_COMP="lz4"`); `max_comp_streams` still accepted | **partial** | S / low: pick zstd (4.9 ratio parity) or keep lz4 (speed); a defconfig line |
| VM sysctls | `min_free_kbytes 32768`, `page-cluster 0`, `swappiness 80` (V init.swap.rc); system: `dirty_expire/writeback_centisecs 200`, `dirty_background_ratio 5`, `watermark_boost_factor 0`, `overcommit_memory 1`, `min_free_order_shift 4` (S init.rc:495,1162-1188) | rc | yes, except `min_free_order_shift` (4.9 Android-only; write fails, harmless) | **done** | — |
| lmkd | `ro.lmk.use_psi=true`, `kill_heaviest_task=true`, `kill_timeout_ms=100`, `swap_free_low_percentage=20`, `swap_util_max=90`, `thrashing_limit=30`, `thrashing_limit_decay=50` (build.prop); 4.9 `PSI=y` (CFG49:112) | build.prop + PSI | yes | **done** (`PSI=y`, `MEMCG=y`) | — |
| Reclaim | 4.9 classic LRU, `MEMCG_FORCE_USE_VM_SWAPPINESS` | kernel | — | 6.12 MGLRU on (`LRU_GEN_ENABLED`): expected better | — |
| CMA / heaps | CMA 26 MB, ION Exynos (+ RBIN heap) (CFG49:1331,4426-4428) | defconfig | — | CMA 16 MB + dma-buf heaps (system, cma) | S / low (watch the camera/DPU pools later) |
| Freezer | vendor mounts cgroup v1 freezer at `/dev/freezer` (frozen/thaw/olaf) for Samsung freecess (rc:394-428); Android 16 uses the cgroup v2 freezer | vendor rc | yes (`CGROUP_FREEZER=y`) | **done** (config) | — |

### 4.11 I/O and UFS

| Area | What 4.9 does | Lives in | Applies unchanged? | 6.12 status | Effort / risk |
|---|---|---|---|---|---|
| UFS link | HS-G3 × 2 lanes, rate B (DT49 ufs `pmd-attr-lane 2`, `pmd-attr-gear 3`) | DT | yes | **done** (`T:…crownlte-diag.dts:186-187`) | — |
| UFS clock gating + hibern8 | `UFSHCD_CAP_CLK_GATING \| HIBERN8_WITH_CLK_GATING \| INTR_AGGR` (`K49/drivers/scsi/ufs/ufs-exynos.c:561-563`); H8 time caps rx-min-activate 3, rx/tx-hibern8 2 (DT49 ufs) | driver + DT | — | **missing by default**: module param `clkgate` = 0 (`T:drivers/ufs/host/ufs-exynos9810.c:599-600,724-726`) | S / medium (H8 enter/exit errors could stall I/O; test read-only first) |
| UFS runtime/system PM | ufshcd rpm/spm with START STOP UNIT | driver | — | **missing**: the diagnostic read-only guard blocks power-mode SSU (`ufs-write-plan.md:121`) | M / medium |
| FSYS0 QoS | 200 MHz while active | driver | — | missing (4.5) | S |
| WriteBooster | n/a: UFS 2.1 device (`ufs-write-plan.md:24`) | — | — | n/a | — |
| Inline crypto (FMP) | `SCSI_UFS_EXYNOS_FMP=y`; `/data` uses `inlinecrypt_optimized` (fstab) | driver + fstab | yes, if FMP works | **ported, untested** (`SCSI_UFS_CRYPTO=y` + FMP struct fix 21d7dcb7f). If it fails, `BLK_INLINE_ENCRYPTION_FALLBACK` does AES on the CPU: a large CPU/battery cost on all `/data` I/O | M / **high** (data) — part of the planned crypto KAT step |
| I/O scheduler | CFQ default, deadline/noop built, MQ deadline built (CFG49:363-370); read-ahead 128 KB | defconfig | — | **different**: no I/O scheduler (`MQ_IOSCHED_DEADLINE` off, kyber off) → `none` | S / low: enable mq-deadline (and BFQ if wanted); compare app-launch times |
| f2fs / discard | `/data` f2fs `noatime, discard, fsync_mode=nobarrier, reserve_root=32768, inlinecrypt` (V fstab); `discard_max_bytes 134217728` (S init.rc:1221-1222) | fstab + rc | yes | **done** (F2FS in config) | — |
| blkio cgroup | `BLK_CGROUP=y` | defconfig | yes | done (+ `BLK_CGROUP_IOPRIO`) | — |

### 4.12 Power HAL (pixel libperfmgr, `V/etc/powerhint.json`)

| Area | What it does | Lives in | Applies unchanged? | 6.12 status | Effort / risk |
|---|---|---|---|---|---|
| LAUNCH / CAMERA_LAUNCH / CAMERA_SHOT / AUDIO_LAUNCH | holds `/dev/cpu_dma_latency` = 44 µs for 5000/1000/1000/2000 ms (keeps CPUs out of C2, whose exit latency is 160/220 µs) | HAL | yes | **done** (kernel cpu_latency_qos) | — |
| INTERACTION | LITTLE min 1456 MHz for 100 ms | HAL | yes | **done** | — |
| SUSTAINED_PERFORMANCE | GPU max 455 MHz | HAL | yes | **done** (Mali sysfs) | — |
| EXPENSIVE_RENDERING | GPU min 338 MHz, max 572 MHz | HAL | yes | **done** | — |
| DOUBLE_TAP_TO_WAKE | `/sys/class/sec/tsp/cmd` `aod_enable,0/1` | HAL | No | **missing** (touch stage 1 has no sec_cmd; also used by `vendor.lineage.touch-service.samsung`) | S–M / low |
| Not in the HAL | no big-cluster boost, no uclamp, no dex2oat cpuset/threads (`vendor.powerhal.dalvik.*` props unset) | — | — | — | the 4.9 input booster covered big boosts (4.8) |

### 4.13 Suspend and wakeup

| Area | What 4.9 does | Lives in | Applies unchanged? | 6.12 status | Effort / risk |
|---|---|---|---|---|---|
| System sleep | exynos-pm: `suspend_mode_idx 8` (SYS_SLEEP), `suspend_psci_idx 133`, `cp_call_mode_idx 10`, `usbl2_suspend_mode_idx 12`, GIC/EINT wakeup stats, 4 wakeup-mask registers (DT49:4872-4893, 4936-4944) | DT + exynos-pm + flexpmu + ACPM | No | **missing**: `SUSPEND=y` without platform ops (`roadmap/ROADMAP.md:356`); PSCI v0.1 → `mem` = s2idle, SoC never enters SYS_SLEEP | L / high (a failed wake needs a long-press) |
| Autosleep / wakelocks | `PM_AUTOSLEEP=y`, `WAKELOCK`, `PM_WAKELOCKS` (CFG49:642-652) | defconfig | — | `PM_WAKELOCKS=y`, no autosleep (Android 16 SystemSuspend does not need it) | n/a |
| Freeze timeout | `/sys/power/pm_freeze_timeout 1000` (rc:505) | vendor rc | yes | **done** (node in `T:kernel/power/main.c`) | — |
| Sync on suspend | `SUSPEND_SKIP_SYNC=y` | defconfig | — | not set (6.12 default sync) | S / low |
| Wakeup keys | `/sys/class/sec/sec_key/wakeup_keys 116,172` (V hw/init.samsung.rc:438; `gpio_keys.c:562`) | vendor rc + Samsung gpio_keys | No (node absent) | **missing** (use DT `wakeup-source` on the key nodes) | S / low |
| MIF/INT in suspend | MIF suspend 1014 MHz, INT 107 MHz (freq_info[2]) | DT | yes | **done** (CAL suspend/resume rates) | — |

### 4.14 Battery and charging (efficiency-relevant limits)

| Area | What 4.9 does (sec-battery + MAX77705; DT49:13607-13776) | Lives in | Applies unchanged? | 6.12 status | Effort / risk |
|---|---|---|---|---|---|
| Charge currents | `max_charging_current 2850` mA, `max_input_voltage 9000` mV / `max_input_current 3000` mA, PD `15000` mW, `chg_charging_limit_current 1700` | DT + sec_battery | No | **partial**: read-only monitor + watchdog kick; charger keeps the SBoot settings (CC ~450 mA, CV 4350 mV; `power-gates.md:75-126`) → slow charging | L / **high** (battery safety) |
| Float / termination | `chg_float_voltage 4350`, full: SOC 93 % + VCELL 4250 mV, recharge: SOC 98 % / 4280 mV, total time 14400 s, recharge time 5400 s | DT | No | **partial** (CV 4350 left as SBoot set it; no termination/recharge logic) | L / high |
| Swelling (temperature) | stop ≥ 41.0 °C (recover 39.0); cold 1st 15/20 °C (950 mA, topoff 200), 2nd 5/10 °C; high-temp current 1400; drop float to 4150 mV | DT | No | **partial**: the kick stops outside 10–41 °C, CC ≤ 950 mA below 20 °C; no CV drop | — |
| Battery ageing | `age_data`: cycles 0/300/400/700/1000 → float 4350/4330/4310/4290/4240 mV, full-condition VCELL 4250…4140, SOC 93…89 % | DT | No | **missing** | M / medium (longevity) |
| SIOP / LCD-on limits | `siop_input_limit_current 1200`, `siop_charging_limit_current 1000`, HV 500/1000, wireless 550/850; WPC LCD-on 450 mA above 38 °C; `chg_high_temp 460/440` | DT | No | **missing** | M / low (charging heat during use) |
| Charging control | Lineage `battery/batt_slate_mode` | kernel node | No | deliberately absent (`max77705-battery.md`) | — |

### 4.15 Other boosters and QoS that come with unported drivers

| Area | What 4.9 does | 6.12 status |
|---|---|---|
| Trustonic sec-os-booster | `PM_QOS_CLUSTER1_FREQ_MIN` while the TEE runs a heavy TA (`drivers/gud/gud-exynos9810/sec-os-booster`, `SECURE_OS_BOOSTER_API=y`) | missing (TEE node disabled) |
| Wi-Fi bcmdhd | `PM_QOS_BUS_THROUGHPUT`, `PM_QOS_CPU_FREQ_MIN` at high throughput | missing (Wi-Fi branch in progress) |
| Modem (SS310AP shmem) | MIF / INT / CL0 / CL1 min QoS for data bursts | missing (no modem yet) |
| Audio ABOX | AUD devfreq, INT/MIF, CL0/CL1 min for low-latency audio | missing (no audio yet) |
| Camera fimc-is | CAM / INTCAM / MIF / INT, CPU min and max, online CPUs, ECT ISP fps throttle | missing (camera last) |

---

## 5. Every node a vendor/system rc file or HAL **writes**, and whether 6.12 provides it

| Writer | Node and value | 6.12 |
|---|---|---|
| V init.samsungexynos9810.rc:232 | `/sys/kernel/ert/rt_ratio_for_freq` 0 | **no** (EMS only; harmless) |
| rc:396-428 | mount v1 freezer `/dev/freezer`; `frozen/thaw/olaf/freezer.state` | yes (`CGROUP_FREEZER=y`; unverified at runtime) |
| rc:505 | `/sys/power/pm_freeze_timeout` 1000 | yes |
| rc:671-682 | `/dev/cpuctl/bg_{cached,abnormal}/cpu.cfs_{period,quota}_us` 20000/10000 | yes (`CFS_BANDWIDTH`) |
| rc:685-731 | `/dev/cpuset/{cached,abnormal,top-app,foreground,background,system-background,restricted}/cpus` | yes |
| rc:704-712 | mkdir `/dev/stune/bg_abnormal`, `bg_cached` | yes (stune compat) |
| rc:805 + fstab | `swapon_all` → zram0 `disksize` 2 GiB, `max_comp_streams` 8 | yes |
| rc:957 | `/proc/ecd/load_firmware` 1 | **no** (debugger; harmless) |
| V init.swap.rc | `vm/min_free_kbytes` 32768, `page-cluster` 0, `swappiness` 80 | yes |
| V init.greenfix.rc | `panel_drv@001/…/fix_green_screen` | partial (attribute exists; path not checked) |
| V hw/init.samsung.rc:438 | `/sys/class/sec/sec_key/wakeup_keys` 116,172 | **no** |
| S init.rc:214-215 | `cpuctl/nnapi-hal/cpu.uclamp.min` 1, `cpu.uclamp.latency_sensitive` 1 | yes |
| S init.rc:240-241 | `stune/nnapi-hal/schedtune.boost` 1, `schedtune.prefer_idle` 1 | yes (compat) |
| S init.rc:333-336 | `sched_tunable_scaling`, `sched_latency_ns`, `sched_wakeup_granularity_ns`, `sched_child_runs_first` | **no** (EEVDF; harmless) |
| S init.rc:495,1162-1188 | `vm/watermark_boost_factor`, `dirty_*`, `overcommit_memory` | yes |
| S init.rc:1188 | `vm/min_free_order_shift` 4 | **no** (harmless) |
| S init.rc:1221-1222 | `/dev/sys/block/by-name/{userdata,rootdisk}/queue/discard_max_bytes` 134217728 | yes |
| powerhint.json | `/sys/kernel/gpu/gpu_max_clock`, `gpu_min_clock` | yes (with `&gpu` enabled) |
| powerhint.json | `/dev/cpu_dma_latency` (hold fd) | yes |
| powerhint.json | `/sys/devices/system/cpu/cpu0/cpufreq/scaling_min_freq` | yes |
| powerhint.json | `/sys/class/sec/tsp/cmd` | **no** |
| thermal_symlinks + thermal HAL | zone/cdev names (read only) | yes, except `thermal-isp-0` = stub, `ac`/`battery` need ADC + battery monitor enabled |
| libbauthserver.so (fingerprint) | references `/sys/power/cpufreq_max_limit` | **no** (UFC; usage unverified, could be checked with REA) |

Nodes that rc files only chown (Samsung framework users; no Lineage writer found in V/bin, V/lib64):
- `/sys/kernel/ehmp/*`;
- `policy{0,4}/schedutil/freqvar_*`;
- `/sys/kernel/ert/*`;
- `/sys/power/cpufreq_{min,max}_limit`, `cpufreq_table`;
- `devfreq_mif/scaling_devfreq_min`;
- `/sys/power/cpuhotplug/**`;
- `17500000.mali/dvfs_*`, `/sys/kernel/gpu/gpu_info`;
- `/sys/class/input_booster/**`;
- `cpu{1..7}/online`;
- `stune/*/schedtune.prefer_{idle,perf}`;
- `/sys/class/adc_meter/s2mps18_adc/*`;
- `/sys/power/rtc_status`.

A missing chown target only logs an init warning.

---

## 6. Top 10 to port (performance and battery), with the next step

1. **CPU capacity + energy model → EAS** (perf and battery; S–M, low risk).
   - Next step: add `capacity-dmips-mhz = <272>` (cpu0-3) and `<785>` (cpu4-7) to `exynos9810.dtsi`.
   - Call `em_dev_register_perf_domain()` from `exynos9810-acpm-cpufreq.c` with P = C·f·V²/10⁹
     (C = 160 / 800, V from `cal_volt[]`).
   - Check: `cpu_capacity` = 355/1024 (1794 caps), `/sys/kernel/debug/energy_model/*`, and
     `SD_ASYM_CPUCAPACITY` in the sched domain flags.
2. **System suspend to SYS_SLEEP** (standby battery; L, high risk).
   - First, on a ROM-in-RAM boot, measure s2idle standby current (`battery/current_now`, screen off, 10 min).
   - Then port `exynos-pm.c`: `suspend_mode_idx 8`, PSCI power_state for index 133, wakeup masks
     DT49:4936-4944, flexpmu/pmucal_system.
   - Test with RTC and power-key wake in a diag image under the watchdog.
3. **Arm C2 in production, then CPD** (idle battery; S then M).
   - Add `samsung,c2-boot-cpus = <0xf0>` (big only) to the Android DT after a 30-min screen-on/off
     soak. Read `cpu*/cpuidle/state1/{usage,time}`.
   - Then the ≤2-LITTLE policy (cpu1-2) from `crownlte-little-c2-soak`.
   - Then CPD on big (`cpd_residency 10000`).
4. **Display PSR and hibernation** (screen-on battery; M–L, medium).
   - Arm the DECON HW trigger only when a frame is committed and mask it after the latch,
     as in 4.9 `decon_core.c`.
   - Then hibernation after 3 idle frames (DSIM ULPS + DPU power domain) and the DECON BTS MIF/INT/DISP requests.
   - Check: MIF residency at 421 MHz on a static screen.
5. **Thermal parity from the device ECT** (prerequisite for 7; S–M, low).
   - Have `exynos9810_acpm_tmu.c` read ECT THERMAL `LITTLE` (step ladder) and PIDTM `BIG`/`G3D`
     at probe.
   - Enable `THERMAL_GOV_POWER_ALLOCATOR` with sustainable 2000 mW and the PID constants (needs item 1's EM).
   - Fix the DTS612:112-124 comment.
   - Test: synthetic load, watch the LITTLE cap step at 76/81/86 °C and the big control at 83 °C.
6. **Frequency parity** (perf; S each, medium risk):
   - **(a)** LITTLE 1950 MHz: accept DT `max-freq` above `cal_max` for ECT levels that have an
     FVMap voltage, as 4.9 does.
   - **(b)** CPU boot QoS (40 s).
   - **(c)** Confirm on the 4.9 phone first (section 2 checklist).
7. **big turbo 2314 MHz on ≤ 2 busy big cores** (perf; M, high).
   - Only after item 5 is HW-verified.
   - A small policy in the cpufreq driver: every 100 ms count big CPUs with `util_avg` ≥ 113
     (heavy ≥ 640). With ≤ 2 busy and LITTLE not busy, set freq_qos max 2314, otherwise 1794.
   - Add 96/91 °C thermal fallback to 1794. Then drop `samsung,max-freq-cap`.
8. **Input/touch booster** (UI latency; M, low).
   - A `crownlte-input-booster` input handler that reads the 4.9 `input_booster` DT node
     (TOUCH 130 ms big 1066/LITTLE 832 → 500 ms 858) and sets freq_qos min on policy4/policy0.
   - It also sets `schedtune.boost`/`uclamp.min` on top-app for the head time.
   - Test with `getevent` + `time_in_state`.
9. **UFS power and I/O** (battery + I/O latency; S–M, medium).
   - Trial `ufs_exynos9810.clkgate=1` read-only and count H8 entries/errors, then make it the default.
   - Enable `MQ_IOSCHED_DEADLINE`.
   - Add FSYS0 and DISP devfreq instances to the lean ACPM devfreq driver (4.9 freq_info values).
   - Verify FMP inline crypto (or `/data` I/O runs on CPU AES).
10. **GPU power, then charger policy.**
    - **GPU** (battery; M, medium): make `samsung,g3d-power-control = "runtime"` the default once
      the q31c-style soak passes; then IFPO and the vk/egl/MIF boost locks.
    - **Charger** (charging speed, efficiency and longevity; L, high): follow
      `decisions-20261105/3-charger.md`:
      - a guarded CC write first (≤ 1500 mA in 10–41 °C);
      - then the swelling CV drop to 4150 mV, `age_data` float reduction and SIOP limits.

Smaller follow-ups:
- zram compressor (zstd for 4.9 parity);
- schedutil `rate_limit_us` (4.9 used 5 ms);
- UFC `/sys/power/cpufreq_max_limit` for the fingerprint server;
- `sec/tsp/cmd` for double-tap-to-wake;
- `wakeup-source` on the power/home keys;
- interrupt-driven TMU instead of 1 s polling.

## 7. Read-only checks to run on the 4.9 phone when it is free

- `cat /sys/devices/system/cpu/cpufreq/policy{0,4}/{scaling_available_frequencies,scaling_max_freq,cpuinfo_max_freq}`
- `cat /sys/power/cpuhotplug/governor/enabled`; `cat /sys/devices/system/cpu/online`, sampled under 1-thread and 4-thread loads.
- `cat /sys/devices/system/cpu/cpu*/cpu_capacity` (expect ~299 / 1024).
- `for z in /sys/class/thermal/thermal_zone*; do cat $z/type $z/policy $z/trip_point_*_temp; done` (confirms the ECT overrides in section 3).
- `cat /sys/block/zram0/comp_algorithm /sys/block/sda/queue/scheduler`.
- `cat /sys/devices/system/cpu/cpu*/cpuidle/state*/{name,usage}` (C2 on all cores) and `/sys/power/cpuhotplug/...`.
- Standby current with the screen off (`/sys/class/power_supply/battery/current_now` over 10 min), as the 6.12 baseline target.
