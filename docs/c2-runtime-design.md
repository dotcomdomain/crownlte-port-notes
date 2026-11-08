# Runtime C2 on the crownlte 6.12 kernel: why it is off, how 4.9 does it, staged plan

2026-11-07. Read-only research and design. No builds, no commits, no phone trials.
The only phone access was read-only adb against the running 4.9.337 kernel, for section 3.
One slip: a snapshot helper wrote `/data/local/tmp/s0` (271 bytes). I deleted it at once.

Sources:
- 6.12: `crownlte-integration-6` 4148bf525 (git show on gekko). The int6 ROM-RAM DTB is
  `panel-bright-work/ko-int6-6aa6/.../exynos9810-crownlte-romram-v23a-int6.dtb` (297eb4f4); I
  decompiled it. Its config is `panel-bright-work/ko-int6-6aa6/config`.
- 4.9: `src/close-range-fix-4.9` and `/root/crownlte-hid-kernel/out/.config`.
- Earlier results: `roadmap/little-c2-apm-re.md`, `C:\Users\Patrick\tools\el3-re\little-c2-results-20261106.md`,
  `Documentation/crownlte/cpuidle-secondaries-q22b.md`, and the q22b/q40/q43/q44/c2diag/c2soak notes.

---

## 0. Key findings

1. **C2 is off on int6 because no CPU is ever armed.** The int6 DTB has neither
   `samsung,c2-boot-cpus` nor `samsung,cpuidle-selftest`. Every C2 state therefore keeps
   `CPUIDLE_STATE_DISABLED_BY_DRIVER`, and the menu governor never picks it. sysfs `disable`
   shows only the *user* bit, so it reads 0. Adding `samsung,c2-boot-cpus` alone would not
   help either: the probe-time arming is refused because cpuidle probes before the MCT
   broadcast driver (link order). Details in §1.
2. **The kernel side of 4.9's C2 is already reproduced exactly:**
   - PSCI 0x10000;
   - the same CAL sequences (byte-identical tables);
   - the same order: cpu_pm_enter → cal_cpu_disable → PSCI → cal_cpu_enable → cpu_pm_exit;
   - the same lock (E3).

   4.9 never uses a cluster-level state on the LITTLE (boot) cluster. CPD is big-only, and
   SICD needs all 8 CPUs in C2. **The three brief hypotheses (cluster-aware power_state,
   missing CPD, PMU written before EL3 finishes) are not supported by the code** (§2.3).
3. **4.9 runs C2 on all four LITTLE cores, CPU0 included, at about 84 % residency and 80-110
   entries/s per core.** I measured this today, screen off, USB attached (§3). So three or four
   LITTLE cores in C2 at once is routine on 4.9. The 6.12 hang is triggered by something that
   differs in 6.12's *event pattern*, not by a missing kernel step.
4. **The most likely cause (medium-low confidence) is a per-core state race inside EL3 in the
   LITTLE suspend/warm-boot path.** 6.12's wake pattern exposes it:
   - The tick broadcast wakes several LITTLE cores with one multi-target SGI in the same cycle.
     4.9 has **zero** broadcast IPIs: every CPU ticks on its own MCT local timer SPI.
   - HZ is 250 instead of 100.
   - CPU0 never sleeps.

   Supporting evidence: PSCI CPU_ON to the lost core returns 0, so EL3 thinks the core is OFF,
   not SUSPENDED. Valid 0x10000 requests sporadically get -22. q43 lost a core with at most two
   LITTLE cores in C2 at once, so the loss needs a third core *cycling*, not three down at once.
   §2.4 lists five cheap discriminating experiments. The candidate fix is 4.9 parity: per-CPU
   MCT local timers.
5. **The plan:**
   - S1a: big C2 on cpus 4-7. S1b, opt-in: LITTLE C2 on cpu1-2.
   - Before any of it: a self-recovery chain that does not exist yet (lost-core detector,
     buddy hard-lockup panic, a WDT stop-petting hook, a persistent quarantine marker), plus
     the arming-order fix.
   - S2: discriminate and fix the 3-core hang.
   - S3: big-cluster CPD (port of a1660042b).
   - SICD is noted as S4. With the USB cable attached, 4.9's own SICD is probably blocked by
     the dwc3 idle-IP.

---

## 1. Q1: the exact gate on integration-6

There are three layers. Any one of them alone keeps C2 at usage 0.

| # | Gate | Where | int6 state |
|---|---|---|---|
| G1 | Every C2 state is registered with `CPUIDLE_FLAG_OFF`. `e9810_take_gate()` then moves it to `CPUIDLE_STATE_DISABLED_BY_DRIVER` on every CPU. The menu governor skips any state whose `states_usage[i].disable != 0` (menu.c:311). | `cpuidle-exynos9810.c` `e9810_parse_states()` :472, `e9810_take_gate()` :514 | set at probe; cleared only by `e9810_arm()` → `e9810_state_disable(cpu, false)` |
| G2 | `e9810_enter_c2()` (:292) runs WFI for any CPU not in `e9810_armed` (counted as `unarmed`). | :302 | `e9810_armed` empty |
| G3 | CPUs are armed only by (a) the DT self-test (`samsung,cpuidle-selftest`, phases at 90 s, disarmed afterwards unless `samsung,cpuidle-selftest-keep-armed`) or (b) `samsung,c2-boot-cpus` at probe (`e9810_arm_boot_cpus()` :1917). | probe :1936 | **neither property is in the int6 DTB.** The cpuidle node holds only `compatible/reg/samsung,pmu-syscon/status=okay`. The MCT-bc node is okay with `samsung,mct-bc-report`. |

**Why sysfs shows `disable=0`:** `show_state_disable()` returns
`state_usage->disable & CPUIDLE_STATE_DISABLED_BY_USER` (drivers/cpuidle/sysfs.c:279-285). The
driver bit is invisible there. `.../stateN/default_status` reads `disabled` (FLAG_OFF), which
confirms the gate read-only. The dmesg lines are `LITTLE C2 disabled (WFI only), cluster-0 C2
limit 2` and `registered: cluster0=1 cluster1=1, C2 disarmed on every CPU`. There is no
`C2 armed at boot` line.

**Latent G4 (arming order):**
- `e9810_arm_refused()` returns `"MCT tick broadcast not active"` until `mct_bc_probe()` sets
  `mct_ready` (:610).
- Both drivers are `builtin_platform_driver` (device_initcall). `drivers/Makefile` links
  `cpuidle/` (line 132) before `clocksource/` (line 140), so the cpuidle probe, and with it
  `e9810_arm_boot_cpus()`, runs first.
- Result: `samsung,c2-boot-cpus` would print `C2 boot arming of ... refused: MCT tick broadcast
  not active` and C2 would stay off. This matches the ROM-RAM v4 note "C2 off because cpuidle
  probes before MCT-bc". S1 must fix it (§4.1 C1).

LITTLE C2 has one more gate: `samsung,c2-little-enable`, which is absent, so arming cpu0-3 is
refused. CPU0 is excluded by design (the self-test never arms it). Any CAL or PSCI failure
streak latches `e9810_deep_off`.

---

## 2. Q2: 4.9 vs 6.12, C2 entry and exit step by step

### 2.1 Power states (PSCI v0.1: cpu_suspend 0xC4000001, cpu_on 0xC4000003, in both kernels)

| State | 4.9 power_state | How 4.9 issues it | 6.12 |
|---|---|---|---|
| C2 (core down) | **0x00010000** (DT `arm,psci-suspend-param = <65536>`, LITTLE `bootcl-cpu-sleep`, big `nobootcl-cpu-sleep`) | `arm_cpuidle_suspend(1)` → `psci_suspend_finisher` → CPU_SUSPEND(0x10000, cpu_resume) | **0x10000**, same values and latencies (70+160 / 235+220 µs, residency 2000 / 3500 µs) |
| CPD (cluster down) | **0x01000000** = `psci_power_state_pack(0,0,1)` | index `PSCI_CLUSTER_SLEEP` (128) → `psci_suspend_customized_finisher()` (psci.c:446-473), via cpu_suspend with cpu_resume (type bit 0, but the cores really power off) | not implemented. Port on `crownlte-cpuidle` a1660042b (opt-in, never run) |
| SICD (system idle clock down) | **0x00000001** = pack(1,0,0) | `PSCI_SYSTEM_IDLE` | not implemented |
| SICD+CPD | 0x01000001 | defined, not used by exynos-powermode.c | — |
| CP call / system sleep | 0x02000000 / 0x03000000 | modem / suspend | — |

### 2.2 Ordering

4.9 path: `cpuidle-exynos64.c` `exynos_enter_idle()`; then `exynos-powermode.c`
`exynos_cpu_pm_enter()` :521 and `exynos_cpu_pm_exit()` :561. 6.12 path: `e9810_enter_c2()`.

| Step | 4.9 | 6.12 int6 |
|---|---|---|
| 1 | `cpu_pm_enter()` (GIC `gic_cpu_save`, PMU, FPSIMD notifiers) | same |
| 2 | `spin_lock(c2_lock)` | none (the E3 lock exists only on the diag branch; no effect on the hang) |
| 3 | — | W1C of the core's own latched GRP2 event (`stale_grp2`) |
| 4 | `cal_cpu_disable(phy)` under `pmucal_cpu_lock`: CPU_INFORM[phy] = 1 (C2); INTR_GEN GRP2 enable bit; GRP1 pend W1C if set; PMU_EVENT_INTERRUPT_ENABLE_GRP2 bit | **identical** (cal-if/pmucal tables diff to zero except `fallthrough;`) |
| 5 | c2_mask |= cpu; if the caller's next event ≥ 10 ms: CPD check (big only), SICD check (boot cluster, all online CPUs in c2_mask, idle-IP) | none |
| 6 | `spin_unlock(c2_lock)`; PSCI CPU_SUSPEND(state) | PSCI CPU_SUSPEND(0x10000) |
| 7 | (EL3 → WFI → PMU/APM power the core off on GRP2; GRP1 wake → APM powers it on → EL3 warm boot → cpu_resume) | same firmware path |
| 8 | `spin_lock(c2_lock)`; `cal_cpu_enable(phy)`: CPU_INFORM = 0; GRP2 enable bit off; GRP2 pend W1C; EVENT_INTERRUPT_ENABLE_GRP2 bit off | `cal_cpu_enable(phy)`, identical |
| 9 | if the cluster is in CPD: `cal_cluster_enable()`; if SICD was entered: `exynos_wakeup_sys_powerdown()`; c2_mask clear; unlock | none |
| 10 | `cpu_pm_exit()` | same |
| CPU online | cpuhp `exynos_hotplug_in_callback()`: `cal_cluster_enable` if first in its cluster, then `cal_cpu_enable` | cpuhp `e9810_cpu_online()` → `cal_cpu_enable` (added after q22b) |

In both kernels `cal_cpu_enable` runs on the resumed core after PSCI has returned, so EL3 has
finished with that core by then. The only cross-core overlap is read-modify-write of the
shared GRP2-enable registers (0x14070200, 0x14067f08) by other cores, under
`pmucal_cpu_lock`. 4.9 has exactly the same overlap.

### 2.3 The other items in the brief

- **GIC/IPI:** both kernels use GIC-400 (GICv2) with the same `gic_notifier` (save/restore the
  per-CPU SGI/PPI state, `gic_cpu_if_up` keeps the bypass-disable bits).
  - IPI traffic differs a lot. 4.9 /proc/interrupts after 691 s uptime (screen off) shows:
    Timer broadcast IPIs **0** on every CPU; `arch_timer` 0; `mct_tick0..7` (SPI 470-477, one
    per CPU) 36k-64k; rescheduling IPIs about 60k-97k per CPU; function-call IPIs 1.1k-12k.
  - So 4.9 C2 cores wake on plenty of SGIs, which therefore work as a class. Every *timer*
    wake, however, comes from a per-core SPI with its own countdown.
  - 6.12: the per-CPU tick runs on the arch timer (C3STOP). Every timer wake of a C2 core is
    an `IPI_TIMER` SGI sent from CPU0's MCT-G0 broadcast handler via
    `tick_broadcast()` → `smp_cross_call(mask, IPI_TIMER)` (arch/arm64/kernel/smp.c:1133).
    That is **one GICD_SGIR write per expiry, aimed at every CPU whose timer expired**.
- **idle-IP:** only used for SICD (system mode). It plays no part in C2 or CPD.
  `fix-idle-ip = "acpm_dvfs"`. The list includes `10c00000.usb`; dwc3-exynos.c:631/644 marks
  USB busy while it is resumed, so **with the USB cable connected, SICD is probably blocked on
  4.9 too**.
- **CPD conditions (4.9 `is_cpd_available()` :441):**
  - `cpd_enabled` (DT 1);
  - not during a big cpufreq transition (PRE/POSTCHANGE notifier :755, which also IPIs a
    cluster that is down);
  - not while `block_cpd()` is held (hotplug governor with a cpu-min boost request);
  - **never for the boot cluster (:458)**;
  - all online cluster CPUs in c2_mask;
  - the caller's next event ≥ `cpd_residency` 10 ms.

  Quirk: `get_next_event_time_us(cpu)` ignores `cpu` and returns the *caller's*
  `tick_nohz_get_sleep_length()` for every CPU in the mask.
- **Last core in the cluster:** the last big core to enter does `cal_cluster_disable(1)`
  (MAILBOX_APM2AP 0x14100010 bit 24 = 1) and issues 0x01000000. The first big core to return
  clears the bit.
  - `cluster0_off` is the same mailbox bit, but 4.9 never calls it at runtime.
  - `big_reset_control()` acts only when the DFD GPR dump is enabled.
  - The last LITTLE core can only become SICD (needs the big cluster down too), never a
    LITTLE cluster power-down.
- **Per-core quirks:**
  - In 4.9 **CPU0 is not special**: it has C2 (`cpu-idle-states = <61>` on cpu0-3) and
    measured 84 % C2 residency.
  - The only asymmetries are cluster-level: CPD is non-boot only, SICD boot-cluster only.
  - The 4.9 hotplug governor (big SINGLE/DUAL/QUAD for 2314 MHz) does not offline cores at
    idle (QUAD at low load).

**Verdict on the three hypotheses:**
- (a) "4.9 uses cluster-aware states once all non-boot LITTLE cores are down": **refuted.**
  4.9 issues 0x10000 for every LITTLE C2, CPU0 included. The only LITTLE-side cluster-aware
  state is SICD, which needs all 8 CPUs in C2. That can never happen on 6.12 (CPU0 never
  sleeps), and q43 failed with at most two LITTLE cores in C2.
- (b) "the missing CPD step makes EL3 believe the cluster is powering down": **refuted for
  LITTLE**, because 4.9 never does CPD on the boot cluster. The big cluster has no CPD on 6.12
  either and never failed.
- (c) "the exit path writes the PMU before EL3 is finished": **not supported.** The per-core
  order is identical, and serializing the kernel side (E3) changed nothing. A race between
  EL3 and another core's kernel RMW on a shared PMU register cannot be fully excluded
  (EL3 is encrypted), but 4.9 performs the same RMWs.

### 2.4 Most likely cause of the 3-LITTLE hang, and how to pin it

What the data says:
1. Single-core and two-core LITTLE C2 pass. The c2soak ran cpu1-2 for 20 s, about 3800
   cycles. Three *armed* LITTLE cores lose a core within 20-255 attempts, even with at most
   two in C2 at once (q43). The failure needs three LITTLE cores cycling.
2. Lost-core state:
   - powered off, inform = 1 (C2), GRP1 wake enabled by APM, but GRP1 pend never latched
     (q43). Seen from the PMU, the wake never arrived.
   - Later it is powered on but executing, not in WFI.
   - **CPU_ON returns 0.** A PSCI implementation answers CPU_ON for a *suspended* core with
     ALREADY_ON. EL3 therefore holds this core as OFF: **EL3's per-core bookkeeping is
     inconsistent**, so the core is most likely spinning in a warm-boot path that waits for
     a state that never comes.
3. About 1 in 200 LITTLE CPU_SUSPEND(0x10000) calls return -22 (INVALID_PARAMETERS) with a
   constant, valid argument (q40/q43). EL3 sometimes sees an inconsistent state; this is
   plausibly the benign form of the same race.
4. The APM C2 code has no cluster-0 logic (APM RE). E2 showed no APM core events around the
   loss. The fault is in EL3, the PMU or the GIC wake path.
5. On 4.9 the kernel issues the same sequence on all four LITTLE cores at 80-110 entries/s
   each without losses.

**Ranked hypotheses:**

| Rank | Hypothesis | Why |
|---|---|---|
| H1 | **Synchronized multi-core LITTLE wakes.** The broadcast IPI_TIMER is one multi-target SGI, so 2-3 LITTLE cores power up and run the EL3 warm-boot path in the same cycle while another enters. A per-core EL3 state race (lock, per-cluster count, or PMU/GIC wake latch) is lost. | Fits 1-5. Specific to 6.12: 4.9 sends zero broadcast IPIs and every core counts down its own MCT local timer, so wakes are de-synchronized. HZ 250 vs 100 also means 2.5× more aligned expiries. |
| H2 | CPU0 sitting in WFI (never powered down) while three other LITTLE cores cycle puts the DSU/cluster in a combination EL3 does not expect. | 6.12-specific (4.9 CPU0 idles in C2 most of the time), but it does not explain point 2 by itself. |
| H3 | LITTLE DVFS (ACPM) transitions overlap core power transitions. | E2 showed DVFS traffic near the loss, but 4.9 also scales LITTLE while idle. Weak. |
| H4 | The hang is in early kernel resume (cpu_resume, MMU off), not in EL3. | The stage marker cannot tell "EL3" from "cpu_resume before the C code". Unlikely (the path is per-CPU and works 1-2 cores), but cheap to exclude. |

**Discriminating experiments**, all on the delayed-UFS vehicle with the CPU0-pinned PID1
(ramdisk 506232b1) and the S0 recovery chain:

- **X1, staggered broadcast** (cheapest, tests H1 directly). A diag static key in
  `tick_broadcast()`: send single-target IPI_TIMERs, LITTLE targets spaced by 20-50 µs, big
  unchanged. Also count how many broadcasts hit ≥2 LITTLE cores. Run little-3 (cpu1-3) for
  5 min.
  - PASS → H1 confirmed. The fix is X2, or keep the staggering.
  - FAIL → H1 unlikely.
- **X2, per-CPU MCT local timers** (4.9 parity, the probable real fix). Extend
  `exynos9810-mct-bc` with L0-L7 as per-CPU clockevents:
  - SPI 470+N, `irq_force_affinity` to CPU N, rating 500 (above arch_timer 450), no C3STOP;
  - register sequence from 4.9 `exynos_mct.c` :509-526 / :590.

  The tick broadcast then stays idle, as on 4.9: timer wakes become per-core SPIs. It also
  lets CPU0 take C2 later, as on 4.9.
  - Gate: an FRC/IRQ self-check per local timer before registering, like G0 today.
  - Run little-3, then little-4 (with cpu0).
- **X3, CPU0 kept out of WFI** (tests H2): during the little-3 phase, a SCHED_IDLE busy
  kthread pinned to CPU0.
- **X4, LITTLE frequency pinned** (tests H3): freq-QoS min = max for policy0 during the phase.
- **X5, resume marker** (tests H4): pass PSCI an entry trampoline. It writes
  `{magic, cpu, counter}` to a no-map uncached page (MMU off, physical address) and branches
  to `cpu_resume`. The lost core's slot then shows whether EL3 ever returned control.
- Order: build X5 + X1 + X3 + X4 into one diag image with each selected per DT phase. Then X2.
  Each phase must still go through the S0 recovery chain, because a loss is expected in the
  control phase.

---

## 3. Q3: what 4.9 does at runtime (code + measurements today)

Policy (code): every CPU 0-7 has WFI plus C2 (menu governor, HZ 100, NO_HZ_IDLE, HIGH_RES).
- **CPD** on the big cluster when all online big CPUs are in C2, the caller's next event is
  ≥ 10 ms, there is no cpufreq transition and no boost block.
- **SICD** from a LITTLE CPU when all 8 online CPUs are in C2, the next event is ≥ 10 ms, the
  idle-IP register shows every listed IP idle, and no JIG is attached.
- Ticks come from the per-CPU MCT local timers (arch timer is clocksource only, DT
  `use-clocksource-only`).

**Measured, 4.9.337, screen off (mWakefulness=Asleep), USB powered.** Uptime 463 → 523 s,
just after a reboot, so the background is a little busy:

| CPU | WFI/s | WFI % | C2/s | C2 % | mean C2 | not idle |
|---|---|---|---|---|---|---|
| cpu0 | 60 | 4.8 | 109 | 84.3 | 7.7 ms | 10.9 % |
| cpu1 | 57 | 5.2 | 87 | 84.5 | 9.7 ms | 10.3 % |
| cpu2 | 63 | 6.0 | 79 | 84.3 | 10.7 ms | 9.7 % |
| cpu3 | 60 | 6.0 | 81 | 84.3 | 10.4 ms | 9.7 % |
| cpu4 | 22 | 3.3 | 51 | 90.3 | 17.7 ms | 6.4 % |
| cpu5 | 21 | 3.0 | 44 | 90.3 | 20.6 ms | 6.7 % |
| cpu6 | 18 | 2.7 | 40 | 91.5 | 22.7 ms | 5.8 % |
| cpu7 | 14 | 2.0 | 46 | 91.8 | 20.0 ms | 6.2 % |

(C2 time includes CPD/SICD periods, which run under the C2 index.) Counters since boot
(452 s) gave the same picture: C2 73-79 % on all eight CPUs.

CPD/SICD counts are not readable as shell: `/sys/power/cpd`, `/sys/power/sicd`,
`/sys/class/cpuidle/cpuidle_profiler/profile` and `/sys/class/power_supply/*` are all
SELinux-denied.

**Read-only adb commands** (Git Bash: `export MSYS_NO_PATHCONV=1`). Do not redirect into
files on the device.

```sh
adb shell uname -r                                   # must be 4.9.337-...
adb shell 'dumpsys power | grep -m1 mWakefulness'     # Asleep = screen off
# idle snapshot (repeat after N s; residency = delta time / delta uptime)
adb shell 'cat /proc/uptime; for c in 0 1 2 3 4 5 6 7; do d=/sys/devices/system/cpu/cpu$c/cpuidle; for s in $d/state*; do echo "cpu$c $(basename $s) $(cat $s/name) $(cat $s/usage) $(cat $s/time)"; done; done'
adb shell cat /proc/interrupts                       # mct_tickN, IPI rates
adb shell cat /sys/devices/system/cpu/cpu0/cpufreq/stats/time_in_state   # and cpu4
adb shell dumpsys battery                            # level, charge counter, USB powered
```

Root-only on 4.9 (for the owner, if ever rooted): `echo 1 >
/sys/class/cpuidle/cpuidle_profiler/profile`, wait, `echo 0`, then `cat` it. It reports
C2/CPD/SICD counts and residency plus which idle-IP blocked SICD. Also
`/sys/class/power_supply/battery/current_now` and `current_avg`.

---

## 4. Q4: staged plan

### 4.0 S0: self-recovery chain (a prerequisite for every stage)

Today a lost core is detected only inside the self-test. In c2diag-b the kernel kept petting
the WDT and the phone hung until a manual reset. The lockup detectors are off in int6
(`# CONFIG_SOFTLOCKUP_DETECTOR`, `# CONFIG_HARDLOCKUP_DETECTOR`), and `PANIC_TIMEOUT=0` is
only covered by the `panic=30` bootarg. The chain:

1. **Runtime lost-core detector** in `cpuidle-exynos9810.c`, active whenever anything is armed.
   - Every 2 s a housekeeping work on CPU0 sends an async csd ping to each armed CPU that has
     sat in `E9810_ST_FIRMWARE` for > 1 s without a transition (per-CPU last-transition
     timestamp). It re-pings once.
   - No answer within 2 × 200 ms means lost.
   - Cost: at most 0.5 extra wakes/s per sleeping CPU (4.9 CPUs take 40-110 C2 entries/s).
   - On lost:
     a. `pr_emerg` a "C2-LOST cpuN" line with the existing register dump (goes to
        console/pstore);
     b. write the quarantine marker (item 4);
     c. disarm everything and `wake_up_all_idle_cpus()`;
     d. call a new `crownlte_wdt_stop_petting("c2-lost")` (the same action as the existing
        INT_MAX restart guard), so a WDT reset is guaranteed within 30 s;
     e. `orderly_reboot()` to sync /data if userspace still works.
2. **Generic backstop:** `CONFIG_HARDLOCKUP_DETECTOR=y`, `HARDLOCKUP_DETECTOR_PREFER_BUDDY=y`
   (arm64 also offers the perf detector, which needs pseudo-NMI, unavailable on GICv2),
   `BOOTPARAM_HARDLOCKUP_PANIC=y` (selects SOFTLOCKUP_DETECTOR; keep
   `BOOTPARAM_SOFTLOCKUP_PANIC=n`). `DEBUG_KERNEL=y` is already set.
   - The buddy CPU sees the lost CPU's watchdog hrtimer count stop within about 12-24 s.
   - That panics; panic runs with IRQs off, so the WDT stops being petted.
   - Recovery is then `panic=30` or the WDT, whichever comes first.
   - Check: no false positive from long IRQ-off windows. The worst known was about 4.9 s
     (q46), under the 10 s threshold.
3. **WDT** is already armed at boot with `timeout-sec = 30` and `samsung,start-at-boot`. The
   restart guard (INT_MAX reboot notifier) stays.
4. **Persistent quarantine marker** so an unattended phone cannot loop.
   - A 4 KB `no-map` reserved-memory region next to ramoops (ramoops content survived the
     q35/q37 WDT resets) holding {magic, boot count, last loss cpu/cluster/uptime/reason}.
   - At probe: a recorded LITTLE loss leaves LITTLE C2 off this boot (big still armed); a
     recorded big loss leaves all C2 off.
   - Log `C2 quarantined: ...`. Clear only by a root sysfs write (`c2_quarantine_clear`).
5. **Observability:**
   - root-readable sysfs under the cpuidle platform device: `c2_armed`, `c2_lost`,
     `c2_stats` (per CPU: enter, resumed, early, psci_fail, unarmed, guarded, wfi_fallback,
     stale_grp2, last_err), plus write-only `c2_disarm` as a kill switch;
   - a KERN_INFO `c2 stats:` summary every 10 min.
6. **One-time drills** (ROM-RAM vehicle, each its own DTB):
   - (a) `samsung,c2-lost-drill`: the detector treats cpu2 as lost after 60 s, which proves
     log → marker → stop-petting → reboot → `C2 quarantined` with no real loss;
   - (b) arm cpu1-3, the known failure, once, to prove the chain end to end on the real
     fault.

PASS for S0:
- (a) and (b) each return to Lineage without a button press, within ≤ 90 s of the loss;
- the next boot logs `C2 quarantined (LITTLE)` and arms only the big cluster;
- pmsg-ramoops holds the C2-LOST dump.

### 4.1 S1: runtime C2 policy that is likely safe now

**S1a (default): big C2 on cpus 4-7.** Big C2 has never failed: q44 ~4000 cycles plus the
cpufreq/devfreq overlap, q45, q47.
**S1b (opt-in after its soak): add LITTLE C2 on cpu1-2.** cpu0 and cpu3 stay WFI.

Code (on top of int6):
- **C1, arming order:** if the probe-time arming fails only on `"MCT tick broadcast not
  active"`, retry from a delayed work every 1 s for up to 60 s. Any other refusal is final.
  Add DT `samsung,c2-arm-delay-ms` (suggest 45000, i.e. after the 40 s boot QoS) so C2 starts
  only after early boot.
  - Alternative: have the cpuidle probe return `-EPROBE_DEFER` until `mct_bc_probe()` has
    run, via a new `exynos9810_mct_bc_state()`: {not probed, ready, failed}.
- **C2:** all of S0.
- Keep the `samsung,c2-little-limit = <2>` guard and the fail-streak auto-disable.

DT (new ROM-RAM DTB `romram-v23a-int6-c2`, later the real-boot DTB):
- S1a: `samsung,c2-boot-cpus = <0xf0>;`
- S1b: `samsung,c2-boot-cpus = <0xf6>; samsung,c2-little-enable; samsung,c2-little-limit = <2>;`
- Never bit 0 or bit 3: q43 showed that **arming** a third LITTLE core is what fails.

Tests:
1. Delayed-UFS self-test, new phase `s1-mix` = cpus {1,2,4,5,6,7} for 10 s. Big C2 and LITTLE
   C2 have never run together, and the mix adds multi-target broadcast IPIs across both
   clusters.
2. ROM-RAM (Lineage in RAM with adb, build-19, DEADLINE ≥ 3600):
   - S1a for 60 min: 15 min screen on (touch, scrolling, Wi-Fi), 30 min screen off (Wi-Fi
     associated), 15 min load (app launches, `adb shell` busy loops on 2 CPUs);
   - then S1b the same way, for ≥ 60 min.
   - Take a read-only cpuidle and /proc/interrupts snapshot every 60 s over adb.
3. Only then the real BOOT DTB, and only with S0 in place.

PASS lines:
- `C2 armed for 4-7` (S1b: `1-2,4-7`) `after MCT broadcast ready at <t> ms`;
- no `C2-LOST`, `deep idle disabled`, `hard LOCKUP` or RCU stall;
- per CPU: resumed + early + psci_fail = enter at every snapshot;
- psci_fail only -22 and < 1 % of LITTLE entries; big psci_fail = 0;
- the 10-min `c2 stats:` lines keep coming;
- the final restart is normal (the reboot notifier disarms).
- Residency at screen off: big ≥ 85 %, cpu1-2 ≥ 75 % (4.9: 90 % / 84 %).

**Residual risk (be explicit with the owner).** 3800 LITTLE cycles without a loss bounds the
per-cycle loss rate only to < 8e-4 (95 %, rule of three). A 60-min S1b soak at about 160
LITTLE cycles/s (~576k cycles) bounds it to < 5.2e-6, i.e. a mean time to loss of only ≳ 20 min at
95 % (T/3). No feasible soak proves days. S1b in daily use is therefore safe only *because of* S0:
the worst case is one unattended reboot, after which LITTLE C2 stays quarantined. Most of the
energy gain sits in the big Mongoose cores anyway, so S1a alone is the conservative default.

### 4.2 S2: fix the 3-core hang

1. Run X5 + X1 + X3 + X4 (§2.4) as phases of one diag image. Control phase little-3 is
   expected to fail; the S0 chain recovers it.
2. If X1 passes, or regardless of X1 as the 4.9-parity move, implement X2 (per-CPU MCT local
   timers). Retest little-3, then little-4 with cpu0 (lift the CPU0 exclusion; the q16
   CPU0 failure predates the GRP2 hook reset).
3. If X5 shows a kernel-resume hang (the marker was written), the problem is ours: debug
   `cpu_resume`/`__cpu_setup` on A55.
4. If nothing helps: LITTLE C2 stays limited to cpu1-2. Fallback C is to offline cpu3 at screen
   off (PSCI CPU_OFF, hotplug INFORM bit 16), which needs its own soak. Record it as an EL3
   limitation.

PASS:
- little-3 for 10 min (≥ 150k LITTLE cycles, 0 lost), with the -22 rate reported;
- then little-4 for 10 min;
- then a ROM-RAM 60 min soak with all 8 CPUs armed;
- then real BOOT with S0.

Efficiency gate: LITTLE C2 residency within 5 points of 4.9 (84 %).

### 4.3 S3: big-cluster power-down (CPD)

- Rebase a1660042b (`crownlte-cpuidle`) onto the int6 driver. It adds a deepest state
  `cpd-big`, psci 0x1000000, min-residency 10000 µs, latencies from C2 plus margin.
  Conditions as 4.9 :441:
  - all online big CPUs inside the C2 hooks;
  - the caller's next event ≥ 10 ms;
  - not during a big cpufreq transition (PRE/POSTCHANGE notifier, wake the cluster first);
  - not during an input-boost block.
- Enter: `cal_cluster_disable(1)` (MAILBOX_APM2AP bit 24) under a c2 lock, then CPU_SUSPEND
  through `cpu_suspend` with `cpu_resume`.
- Exit: the first big core back calls `cal_cluster_enable(1)`.
- No CPD on the LITTLE cluster, ever (4.9 :458).
- `big_reset_control` is skipped (DFD-only).
- Tests:
  - delayed-UFS phases `cpd-1` (cpus 4-7 armed, idle);
  - `cpd-freq` (a forced big frequency change every 50 ms);
  - `cpd-soak` for 60 s;
  - then ROM-RAM for 60 min.
- PASS:
  - CPD entries > 0;
  - CLUSTER1_NONCPU_STATUS (PMU 0x14062044) seen off during CPD;
  - no lost core;
  - no cpufreq error;
  - the mailbox bit is back to 0 after each exit.
- Rollback: drop the DT state.

**S4 (later, largest at screen off without a cable): SICD.** It needs:
- CPU0 C2 (S2);
- idle-IP bookkeeping in UFS, USB, I2C/SPI, mailbox and the power domains;
- wakeup masks;
- `cal_pm_enter(SYS_SICD)`;
- power_state 0x1.

With the USB cable attached, 4.9 itself is probably blocked by dwc3's idle-IP bit, so SICD
does not explain any gap measured with the cable in.

### 4.4 Measuring efficiency against 4.9

- **Conditions:**
  - same Lineage build and apps;
  - screen off for ≥ 2 min before sampling;
  - Wi-Fi associated in both cases;
  - same battery-level band and temperature;
  - ≥ 10 min after boot;
  - three 10-min windows per kernel.
- **Idle metrics** (identical sysfs on both kernels, script in §3): per-CPU C2 % and C2
  entries/s, WFI share, `/proc/interrupts` deltas (6.12: broadcast IPI rate, mct-bc), and
  `time_in_state` deltas. The 4.9 reference is the table in §3.
- **Power proxy with the cable always attached:**
  1. Best: an inline USB-C power meter on VBUS, with the battery held in a steady charger
     state, either FULL / charge done or Lineage "Charging control" holding a limit, and
     with the charger confirmed to supply the system from VBUS. VBUS current is then about
     the system load, for both kernels alike.
  2. Fuel gauge: on 6.12, the MAX77705 FG CURRENT/AVGCURRENT through our read-only driver.
     On 4.9 `current_now` is SELinux-denied for adb, so use an app that reads
     `BatteryManager.BATTERY_PROPERTY_CURRENT_NOW` (works on both kernels if the 6.12 health
     HAL exposes the property). Compare only in the same charger state: while charging, the
     FG current is charge minus load.
  3. Coarse: `dumpsys battery` charge-counter slope over ≥ 30 min with charging held off.
- Report idle residency and the power proxy side by side for 4.9, int6 (WFI only), S1a, S1b,
  S2 and S3.

---

## 5. Open points

- Which config the earlier c2diag/q43 images used (HZ_PERIODIC vs NO_HZ_IDLE/HIGH_RES like
  int6). Rerun the little-3 control on the int6 config inside S2 so the hang is confirmed in
  the configuration that will ship.
- The lost-core detector's ping cadence vs the energy cost. It is negligible on paper; check
  the C2 entry rate with the detector on and off in the S1 ROM-RAM run.
- CPU0 C2 (4.9 parity) is possible even with the MCT-G0 broadcast (a hardware broadcast device
  does not pin its owner). It is excluded today only because of q16, which predates the hook
  reset. Retest inside S2.
