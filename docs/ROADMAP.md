# Note9 (SM-N960F crownlte, Exynos 9810): Linux 6.12 → full LineageOS 23 roadmap

> **Outdated:** this is the 2026-11-04 starting point. Most of the "not working" items below
> have passed on hardware since; see [STATUS.md](../STATUS.md) for the current state.

Research snapshot: 2026-11-04 (UTC). Read-only study; no code, builds, or phone access.
Author: research sub-agent (Claude). **Every factual claim cites a path or commit. Anything marked
GUESS is an inference that has not been verified.**

## Citation shorthands

| Tag | Meaning |
|---|---|
| `P` | repo `/mnt/ssd/host-builds/kernel-upgrade/android16-6.12-port` (commits are cited by hash) |
| `T` | worktree `/mnt/ssd/host-builds/kernel-upgrade/acpm-tmu` (branch `crownlte-acpm-tmu`, HEAD **0f6eb9410**, one commit past 99edd1fc6) |
| `G` | worktree `/root/kernel-upgrade/graphics-cap-6.12.93` (HEAD 2630fc320: Mali/ION line) |
| `CFG` | real 6.12 config `/mnt/ssd/host-builds/kernel-upgrade/build/acpm-tmu-6.12.93/.config` |
| `K49` | 4.9 reference kernel `/root/crownlte-hid-kernel` @ 8665533d (4.9.337-ies); built config `K49/out/.config` |
| `DT49` | `K49/arch/arm64/boot/dts/exynos/exynos9810-crownlte_eur_open_26.dts` (flat decompiled DT, 14,722 lines, numeric phandles, no includes) |
| `R` | Windows `C:\Users\Patrick\Downloads\Codex Projects\kernel-upgrade\research\` |
| `DC` | `R\diagnostic-capture\` (per-trial `interpretation.json`) |
| `TR` | Codex transcript `...\kernel-upgrade\codex-session-01a12867-....md` (line numbers; it ends 2026-10-18, line 333045) |

"Built lines" for 4.9 means `wc -l` over the `.c` files that have a matching `.o` in `K49/out`.
That excludes headers, which for some drivers (panel tables, camera, sensorhub firmware) are much larger.

---

## 0. Executive summary

1. **What runs on the phone today.** The newest hardware-tested 6.12 line is `crownlte-acpm-tmu`
   (T @ 0f6eb9410). It is still a diagnostic: a RECOVERY-partition uniLoader shim boots it into a RAM
   PID1 with a USB-ACM console. Verified on 2026-11-03:
   - ACPM IPC, with channels 3/8/9/10 quarantined (56c789f17)
   - DVFS on ACPM channel 0, a LITTLE 1053→832→1053 MHz round trip (ab57dc619)
   - FVMap published (8c62332d3)
   - 8 CPUs online via PSCI (99edd1fc6)
   - **ACPM TMU: the plugin loads, and 4 zones read 43–45 °C** (0f6eb9410)
2. **Earlier Codex trials (Oct 13–25) went much further on userspace,** on kernel 56db0b918. UFS
   probed on real hardware (HS-G3 x2, KLUDG4U1EA 128 GB, LUNs sda–sde) with a *software* SCSI
   write-guard. SYSTEM and VENDOR (ext4) were mounted read-only. Lineage's real `init` got through
   first stage, SELinux, second stage, property service, servicemanager and authenticated adbd. apexd
   activated 7 APEXes, linkerconfig ran, and `art -showversion` worked. The work stopped at an
   untraced `derive_classpath` SIGSEGV (DC\trial-*; R\resume-current-20261025.json). Userdata, FBE and
   FMP were never touched.
3. **So the kernel core is roughly a "boots, schedules, talks to firmware, reads UFS" kernel.**
   - **No user-facing hardware works yet:** display, GPU, touch, pen, audio, modem, Wi-Fi/BT,
     sensors/GNSS, NFC, camera, PMIC regulators, the MAX77705 charger, fuel gauge and haptics, and
     suspend/cpuidle.
   - **The real config also lacks things Android needs** (CFG): SECCOMP, NETFILTER, FUSE, PSI,
     CPUSETS, HID/UHID, INPUT_EVDEV, DMABUF_HEAPS, SYNC_FILE, CMA, IOMMU, DRM/FB, CPU_IDLE, RTC.
4. **The vendor userspace pins the kernel ABI to Samsung's 4.9 interfaces** (details in §4):
   - Mali kbase **r38p1-01bet0, JM UK API 11.35**, `/dev/mali0`
   - legacy handle-based Samsung **ION** with heap ids 1/3/10/11/12
   - fbdev **DECON `S3CFB_WIN_CONFIG`**, used by the `hwc3-service.slsi` HWC built from the
     slsi-linaro source
   - `/dev/g2d`
   - Trustonic `/dev/mobicore*`
   - SIPC `/dev/umts_*`
   - ABOX/VTS
   - the SSP sensorhub
   - `/dev/stune`
   - `/sys/class/sec/*`

   Mainline drivers (panfrost, Exynos DRM/DSIM, brcmfmac, generic wacom/s3fwrn5/hci_bcm) cannot
   replace the Samsung drivers without replacing HALs/blobs. **The realistic path is to port the
   Samsung 4.9 drivers forward.**
5. **The critical path to "Lineage from UFS with display, touch and adb"** has three parts:
   - **(a) An Android-complete config.** This is build-only and can start today.
   - **(b) Trustonic TEE + keymaster runtime, plus FMP or blk-crypto-fallback FBE.** Without the TEE,
     vold cannot unwrap the CE/DE keys and `/data` cannot be mounted without wiping it.
   - **(c) The display stack:** sysmmu, ION, sync_file, dpu_9810 fbdev DECON/DSIM/DPP, the S6E3HA8
     panel, the S2DOS05 display PMIC and DISP devfreq/BTS. Then Mali r38p1 (already ported on the G
     line, never run) and sec_ts touch.
6. **The single biggest hazard is UFS writes.** The boot LUNs and RPMB hold the bootloader and TEE
   replay-protected state. The current write protection is only a kernel SCSI allowlist; hardware
   write-protect is off (DC\trial-delayed-ufs-20261016). Next come PMIC/charger register writes and
   EFS corruption by the modem. Everything else has so far been recoverable: Download Mode + Odin, or
   the automatic return to 4.9 BOOT.

---

## 1. Current state

### 1.1 Hardware-verified capabilities (6.12)

| Capability | Commit | Evidence |
|---|---|---|
| uniLoader shim in RECOVERY, kernel at 0x92000000, initramfs 0x84000000 | shim src `/root/kernel-upgrade/boot-shim/crownlte` | `T/Documentation/crownlte/diagnostic-initramfs.txt` |
| USB ACM serial console (DWC3 + 9810 USB2 PHY + MAX77705 D+/D− routing over HSI2C/USIv2) | ab935e399, 938c87358, 15fe129a7 | trial usblink-v2 2026-10-15, first live serial (`DC\trial-usblink-v2-20261015\interpretation.json`) |
| UFS probe, read-only (software guard) | 56db0b918 | `DC\trial-delayed-ufs-20261016\interpretation.json`: bind errno 0, "HS Gear3 two lanes", sda 31236096×4 KiB, sda1–25. **vcc-supply missing; supplies inherited from the bootloader** |
| ext4 RO mounts of SYSTEM (sda18) / VENDOR (sda19) | 56db0b918 | `DC\trial-rom-read-live-20261016` |
| Lineage init (1st/2nd stage, SELinux load, properties, servicemanager), adbd over FunctionFS, apexd (7 APEX), linkerconfig, ART 2.1.0 | 56db0b918 (+ BLK_DEV_WRITE_MOUNTED=y for apex) | trials #12–#30 (`DC\trial-*-20261017/18/25`); see §1.3 |
| ACPM IPC probe (quarantine ch 3/8/9/10), mailbox IRQ level-high | 56c789f17, d4d83c2a7 | commit messages: "Hardware result 2026-11-03" |
| ACPM DVFS on ch0, FREQ_REQ verified by CMU readback | ab57dc619 | commit message |
| FVMap published, `cal_dfs_is_ready()` for both CPU clusters | 8c62332d3 | commit message |
| SMP 8 CPUs (PSCI) | 99edd1fc6 | commit message: "smp: Brought up 1 node, 8 CPUs" |
| ACPM dynamic plugin load (TMU fw at 0xd800) + TMU sensor driver, 4 zones 43–45 °C | ffc63039f, 18d715b19, 087af91e9, 0f6eb9410 | 0f6eb9410 message. The bootloader leaves CORE_EN=1 and THERM_TRIP_EN=1 with rise7 at 112–115 °C, so a **hardware trip backstop exists** |
| Restart path (ACPM STOP ack, auto-return to 4.9 Lineage) | 98879750c, b9d1a4d76, 56c789f17 | trial usi-reset 2026-10-15; 56c789f17 |

### 1.2 Worktrees and branches (52 entries in `git -C P worktree list`)

How the history fits together:
- **Main line:** ACK `android16-6.12-2026-09_r4` (1dd27fbde, 6.12.92), then 87b8a89b7, then the Oct-11
  single-feature ports, consolidated as `crownlte-android16` (842bfdd30). Then the devfreq/QoS chain
  (785266a84…8c753834e), the ASB 6.12.93 merge (83f355302), the Oct 13–16 diagnostic fixes
  (2589dd869…56db0b918), and the Nov 3 ACPM work (56c789f17…0f6eb9410).
- **Side lines fork from 83f355302:** the graphics/thermal-legacy lines and the Oct-11 single-feature
  ports. `git cherry 99edd1fc6 <head>` shows **every Oct-11 side branch is already contained**
  (patch-equivalent) in the main line. The graphics-* and thermal/legacy-* lines are **not merged**.

"HW" = flashed and run on the phone.

| Worktree (path) | HEAD / branch | What it is | HW? / result |
|---|---|---|---|
| `P` (main checkout) | 842bfdd30 `crownlte-android16` | Consolidated Oct-11 port: SoC/pinctrl, UFS/USB baseline, CAL/ACPM/clock foundation (d8dcd1181, +46.6k lines), Trustonic (2567fd87e), custom HID/USB/cgroup behaviour, cpufreq v1 (624600a3e), schedtune compat (8819c221c) | No (build only) |
| `/mnt/ssd/.../acpm-channel-quarantine` | 00f7e8663 `crownlte-acpm-channel-quarantine` | Nov-3 ACPM line up to the 8-CPU DT. Same content as 99edd1fc6 minus TMU (`git diff 99edd1fc6 00f7e8663` only removes the TMU files); 00f7e8663 is a re-commit of the SMP DT change | **Yes**, 2026-11-03 trials q…q9: ACPM, DVFS, FVMap, SMP all pass (commit messages 56c789f17…99edd1fc6) |
| `/mnt/ssd/.../acpm-tmu` | **0f6eb9410** `crownlte-acpm-tmu` | Newest line: + dynamic plugin load, TMU driver, thermal zones, TMU slot-overlap fix | **Yes**, q10–q12 (`C:\Users\Patrick\tools\trial-acpm-q1{0,1,2}-20261103`): TMU attached, zones read 43–45 °C (0f6eb9410) |
| `/mnt/ssd/.../cpufreq` | 99edd1fc6 `crownlte-cpufreq-acme` + **uncommitted edits** (cpufreq driver, Kconfig, diag DT, doc) | **Active work by another agent**: acme-style ACPM cpufreq; big cluster capped at 1794 MHz until thermal is proven (`.../cpufreq/Documentation/exynos9810-cpufreq-port.md`) | Not yet |
| `/mnt/ssd/.../devfreq` | 99edd1fc6 `crownlte-devfreq` (clean) | **Active work by another agent**: MIF/INT devfreq | Not yet |
| `/root/.../diagnostic-restart-6.12` | ca976e55e (detached) | First diagnostic: real-clock USB + checked restart path (6.12.92) | **Yes**, trial #1 2026-10-13: **failed**. ACPM -71, then an alignment fault in `exynos_acpm_binary_update`, panic, no USB. Recovery needed Download Mode + Odin + battery drain (TR ~133144–135400) |
| `/root/.../diagnostic-acpm-security-6.12` | 1a62de7e6 (detached) | ACPM firmware-snapshot isolation | **Yes**, trial #2 2026-10-13: userspace reached, old panic gone, still no USB (MAX77705 I2C timeouts); manual restore (`DC\trial-acpm-20261013`) |
| `/root/.../diagnostic-usi-6.12.93` | 15fe129a7 (detached) | USIv2 initialised before HSI2C (fixes MAX77705 I2C) | Contained in b9d1a4d76, which was trialled |
| `/root/.../diagnostic-original-reset-6.12.93` | b9d1a4d76 `codex/original-reset-diagnostic-20261014` | Opt-in original-style reset after ACPM failure + USI fix | **Yes**, trials #3–#5 (2026-10-15): first auto-return to Lineage; then configfs ACM fix, then **first live USB serial** |
| `/root/.../diagnostic-delayed-ufs-6.12.93` | 56db0b918 | UFS probe held until a serial handshake (`CROWNLTE-UFS-PROBE-1`) + SCSI read-only guard (`drivers/ufs/core/ufshcd-readonly.h`) | **Yes**, trials #6–#19, #23–#35: UFS probe PASS, ROM read, ROM exec, Android init, adbd, system init (§1.3) |
| `/root/.../diagnostic-apex-config-6.12.93-20261018` | 56db0b918 | Same source; config adds loop/DM/verity | **Yes**, #20 apex-bootstrap: EBUSY (`BLK_DEV_WRITE_MOUNTED=n`) |
| `/root/.../diagnostic-apex-config-6.12.93-20261018-v2` | 56db0b918 | Same + `BLK_DEV_WRITE_MOUNTED=y`; **this config is the base of the Nov-3 builds** (memory file) | **Yes**, #21–#35: apexd PASS, linkerconfig PASS, ART runs, derive_classpath SIGSEGV unresolved |
| `/root/.../diagnostic-real-usb-6.12` | 44f510b3e `crownlte-diag-real-usb` | Real-clock USB PHY resources (cherry-picked as 2589dd869) | Via ca976e55e only (failed trial #1) |
| `/root/.../diagnostic-clock-port` | 853517026 `crownlte-diag-real-clocks` | Early real-clock diagnostic stack | No; superseded |
| `/root/.../acpm-readiness-port` | 4ea378cdd | Pre-ASB version of 1a62de7e6 | No; superseded |
| `/root/.../asb-cap-6.12.93` | 83f355302 | Merge of the Oct-2026 ACK 6.12.93 ASB into the frozen cap port | No (build) |
| `/root/.../baseline-compat-port` | 5f97c30bd | Legacy global cgroup swappiness (befac38ed) | No; contained |
| `/root/.../clock-port` | c32826b5c `crownlte-clocks` | CAL ASV/ACPM clock foundation + DVFS supplier readiness | No; contained |
| `/root/.../cpufreq-port` | 64f26f9bc `crownlte-cpufreq` | Calibrated cpufreq v1 with MIF constraints (624600a3e) + Trustonic boost guard | No; contained (being replaced by the `cpufreq` worktree) |
| `/root/.../custom-behavior-port` | 685c8352e `crownlte-custom-behavior` | HID touchpad, droidspaces cgroup aliases, DWC3/u_ether ownership fixes (7d4c00bc3, 2454cb2bb) | No: "No 6.12 custom behavior has been exercised on the phone" (`T/Documentation/crownlte-custom-behavior.md`) |
| `/root/.../schedtune-compat-port` | 3a3c41c65 | Private schedtune controller → `/dev/stune` compat (8819c221c) | No; contained |
| `/root/.../trustonic-port` | 142df223b `crownlte-trustonic-port` | Trustonic MobiCore 9810 driver (2567fd87e, 75 files, +16,859) | No. ABI matched (22 ioctls, 8 headers; `/root/kernel-upgrade/artifacts/trustonic-abi/result.json`), `runtime_verified: false` |
| `/root/.../devfreq-port` | dc4dab390 `crownlte-devfreq-6.12` | MIF BTS supplier | No (build); contained |
| `/root/.../bus-provider-port` | b7227c1dc `crownlte-bus-provider-6.12` | Checked bus transitions + AP-state protocol | No; contained |
| `/root/.../devfreq-abi-port` | b2f9f7804 | Exynos kHz sysfs ABI | No; contained |
| `/root/.../governor-port` | 37297ac89 | Exynos9810 QoS interactive governor | No; contained |
| `/root/.../provider-lifetime-port` | 23cdc918c | Dependency guards across consumer cleanup | No; contained |
| `/root/.../bus-registration-port` | 7a4eb3447 | Register calibrated bus providers | No; contained |
| `/root/.../bus-pm-port` | 65a4f47bf | Firmware sleep/reboot handling for devfreq | No; contained |
| `/root/.../qos-abi-port` | 299cd373a | Original bus QoS controls | No; contained |
| `/root/.../qos-lock-port` | d81b40957 | QoS callbacks outside the global mutex | No; contained |
| `/root/.../cap-coordination-port` | f49b9aea0 | Reverse bus ceilings | No; contained |
| `/root/.../cap-qos-integration-port` | 358e424ff | Exclude internal votes from QoS readback | No; contained |
| `/root/.../qos-exclusion-port` | c8a5d5dde | Variant of 358e424ff | No; contained |
| `/root/.../reverse-cap-plan-port` | 9b31c4971 | Bounded reverse cap reductions | No; contained |
| `/root/.../captured-provider-port` | b438e95ba | Pin bus instances across QoS updates | No; contained |
| `/root/.../supplier-down-gate-port` | e8cef37ab | Reject unsafe supplier decreases | No; contained |
| `/root/.../monotonic-cap-port` | 8c753834e | Cap reconciliation wakeups (end of the devfreq chain) | No; contained |
| `/root/.../graphics-port` | 7dbed7a0c `crownlte-graphics-port` | Mali r38p1 staging (331f966ba), 6.12 API adaptation (7f0f09939, d66ecbced), Samsung ION import (f341b80c4, d78e91127) on the early line | No |
| `/root/.../graphics-gate-20261013` | 03c830a26 | Frozen compile gate of the graphics line | No |
| `/root/.../graphics-qos-security-6.12.93` | bdbb3cc03 `graphics-qos-security-stage-20261013` | Merge of the Mali source gate into the ASB cap port | No |
| `/root/.../graphics-qos-clock-draft-6.12.93` | 821d6b185 | DRAFT G3D QoS supplier | No |
| `/root/.../graphics-backend-repair-6.12.93` | 1175e671f | Mali dump trylock fix | No |
| `/root/.../graphics-sci-idq-v1-6.12.93` | 3dc70fd1e | DRAFT SCI IDQ supplier / Mali BTS | No |
| `/root/.../thermal-model-v1-6.12.93` | 29ec4e710 | DRAFT GPU ECT policy + power model | No |
| `/root/.../graphics-cap-6.12.93` (`G`) | 2630fc320 | **Tip of the Mali line**: MAX votes serialized with clock changes. kbase `bv_r38p1` 148,655 lines + exynos platform 18,581. Branched from 83f355302, so it **lacks all Oct-13+ diagnostic and Nov-3 ACPM fixes** | No: "No full link, no runtime test, no G3D clock/DT node" (`R\graphics-port\README.md`) |
| `/root/.../legacy-pid-v1-6.12.93` | 875f02651 | DRAFT thermal legacy PID snapshots | No |
| `/root/.../thermal-checked-cdev-v0-6.12.93` | 36604518d | DRAFT aggregate cooling-device apply | No |
| `/root/.../legacy-core-v1-6.12.93` | d2074b43e | DRAFT legacy thermal policy transactions | No |
| `/root/.../legacy-core-v2-6.12.93` | 835ae123b | Thermal hysteresis behaviour | No |
| `/root/.../legacy-actor-v0-6.12.93` | a3739e8ae | Thermal actor vote transactions | No |
| `/root/.../legacy-core-kunit-v0-6.12.93` | 57267bd8f | KUnit tests for the above | No (KUnit/QEMU only) |

Assessment of the graphics-* and thermal/legacy-* drafts: they are a heavily abstracted
"checked transaction" design, layered on the old cap port. The new TMU driver (18d715b19) and the
cpufreq doc use a plain OF-thermal / step_wise design instead. **GUESS:** most of the legacy-* thermal
drafts will be discarded. The Mali/ION commits (331f966ba, 7f0f09939, d66ecbced, f341b80c4,
d78e91127, 5949aba3b, 2630fc320) are worth rebasing onto 0f6eb9410.

### 1.3 Which pieces already exist, by subsystem

| Subsystem | State in 6.12 port | Evidence |
|---|---|---|
| SoC/pinctrl/chipid | Done, upstream backport | 87b8a89b7; `T/Documentation/crownlte/provenance.txt` |
| Clocks (CMUCAL/vclk/ECT/ASV) | Ported; DVFS **proven** | d8dcd1181; `T/drivers/clk/samsung/clk-exynos9810-{composite,legacy}.c` (1753+561); ab57dc619 |
| ACPM IPC / plugins / FVMap / DVFS / restart | **Proven** | §1.1 |
| UFS host + CAL | **Probe proven, RO**; no regulators in DT | 56db0b918; `T/drivers/ufs/host/ufs-exynos9810.c` (1215), `ufs-cal-9810.c` (682) |
| UFS FMP inline crypto | Adapter staged, **never run**: zero keyslots, key in PRDT, SMCs 0xc2001810/50/60 | a0a1b647d; `T/drivers/ufs/host/ufs-exynos9810-fmp.h` (151): "Hardware operation has not been verified" |
| USB device (DWC3, PHY, MUIC routing) | **ACM + FunctionFS adbd proven** | trials #5, #16; `T/drivers/phy/samsung/phy-exynos9810-usbdrd.c` (411); `T/drivers/soc/samsung/exynos9810-max77705-muic.c` (226, routing only) |
| Thermal (TMU sensor) | **Proven** (read-only, no trip programming) | 0f6eb9410, 18d715b19 |
| cpufreq | In progress (other agent) | `cpufreq` worktree |
| MIF/INT devfreq | Old chain exists (785266a84…8c753834e, never on HW); new work in progress (other agent) | `devfreq` worktree |
| Trustonic TEE | Ported, ABI-matched, never run | 2567fd87e; `T/drivers/gud/gud-exynos9810` |
| schedtune `/dev/stune` | Ported (`kernel/sched/stune_compat.c`, 305) | 8819c221c |
| Mali r38p1 + ION | On the G line only, DRAFT, never linked into a booting image | §1.2 |
| Display / panel | **Nothing.** Framebuffer 0xcc000000 1440×2960 ARGB8888 is reserved; scanout preservation never observed | `T/Documentation/crownlte/diagnostic-initramfs.txt`; `R\kernel-bringup-readiness.md` |
| Everything else in §2 | Nothing | §2 |

### 1.4 What the last Codex agent concluded (UFS / real Android)

- **UFS works for reads.** "UFS probe passed". The guard is software only:
  `sda_hardware_write_protected: false`, and the review says the guard "does not prevent MMIO,
  secure firmware initialization, or normal UFS initialization queries"
  (`R\ufs-delayed-probe-20261016\README.md`, `independent-trial-review.md`). Userdata, keys and
  encrypted I/O were never requested (`userdata_or_keys_requested: false`,
  `encrypted_IO_proven: false`).
- **Encryption.** The fstab is `/data` f2fs with
  `fileencryption=aes-256-xts:aes-256-cts:v2+inlinecrypt_optimized` (`R\encryption-port\README.md`).
  The S9+ mainline donor reports FMP descriptor transfer failures. Codex proposed a software
  blk-crypto fallback as a no-reformat path (same ciphertext), and said key delivery needs
  Trustonic/keymaster. The policy is "No userdata write until that validation"
  (`R\kernel-bringup-readiness.md`).
- **Real Android.** Everything ran in an isolated RAM namespace under permissive SELinux with a
  minimal rc (TR:324718–324732).
  - **Proved:** init, coldboot, properties, servicemanager, adbd, apexd (7 APEX), linkerconfig, ART.
  - **Not proved:** normal first-stage fstab mounts, the full classpath (all 28 APEX), zygote,
    framework, graphics.
  - **Last blocker:** `derive_classpath` SIGSEGV when untraced; it succeeds under strace
    (`DC\trial-rom-art-directcap-20261025\interpretation.json`).
  - **Recommended next step:** a 32 KiB core-dump capture trial (built at
    `/mnt/ssd/host-builds/kernel-upgrade/rom-art-core-20261025`, never run)
    (`R\resume-current-20261025.json`).
  - Codex's own progress estimate on Oct 13 was "15–25%" (TR:155400).
- **GUESS on the derive_classpath crash:** it may stem from the missing config pieces in §2.13
  (SECCOMP, or a missing property/fs). Re-test after the Android-complete config before spending
  trials on core dumps.

---

## 2. Hardware and kernel-feature inventory

Difficulty scale (effort estimates; GUESS): **S** ≤ 1 agent-day, **M** 2–5 days, **L** 1–3 weeks,
**XL** > 3 weeks or needs reverse engineering.

HW-risk categories:
- **brick** = can make the phone unbootable without Odin/JTAG, or destroy data or keys
- **damage** = electrical, thermal or battery hazard
- **soft** = panic, reboot or hang, recoverable through the auto-return or Download Mode

"Diag test" describes how to test the block with the current RECOVERY flow: shim plus RAM PID1, ACM
serial after `CROWNLTE-UFS-READY-1`, pmsg/pstore, and auto-return after 120 s (memory file
`note9-612-port-setup.md`).

### 2.1 Platform core (needed by almost everything)

| Block | 4.9 driver (path, built lines) | Mainline 6.12 | In port | Diff | Depends on | HW risk | Diag test |
|---|---|---|---|---|---|---|---|
| ACPM IPC/plugins, CAL, ECT, FVMap, DVFS | `K49/drivers/soc/samsung/{acpm,cal-if,ect_parser.c}` (soc/samsung total 28,668 built) | none | **done** (§1.1) | — | — | soft | done |
| Clock provider | `K49/drivers/clk/samsung/clk-exynos9810.c` (2,777) | no 9810 CMU | done (composite+legacy) | — | CAL | soft | done |
| PMU / power domains (15 PDs, pmucal) | `K49/drivers/soc/samsung/exynos-pd*.c`, `pmucal_*`; DT `pd-*` | `exynos-pmu.c` (to gs101); `pm_domains.c` is a different HW model | pmucal imported in cal-if (`T/drivers/soc/samsung/cal-if/pmucal_*` ~1.7k); **no genpd provider** | M | CAL, ACPM | soft (wrong PD off = bus hang) | Power a PD (e.g. g3d) on and off, read status regs, log over serial |
| SysMMU / IOVMM | `K49/drivers/iommu/exynos-iommu.c` 1,865 + `exynos-iovmm.c` 760 (7,880 built); 16 sysmmu nodes, 8 bus domains (DT49) | `exynos-iommu.c` supports v1–v7; **version of the 9810 SysMMU unverified (GUESS v5–v6)** | none; `IOMMU_SUPPORT` off (CFG) | M–L (DT + IOVMM shim for vendor drivers that call `iovmm_*`) | PD, clocks | soft | Read the sysmmu version reg, map a buffer for a dummy master |
| ION (legacy Samsung) | `K49/drivers/staging/android/ion` (8,817; heaps crypto 1, vstream 3, camera 10, secure_camera 11, camera_contig 12; DT49:55–160) | removed upstream; dma-buf heaps (system, cma) exist (`T/drivers/dma-buf/heaps`) | ported on G (f341b80c4, d78e91127; rejects absent IOVMM) | M (rebase, enable, CMA regions) | CMA, IOMMU, secure-world SMC for the secure heaps | soft; secure heaps call EL3 | Allocate, mmap and free from each heap via ioctl in PID1 |
| CMA / reserved-memory | DT49 reserved-memory | yes | `CMA` off (CFG) | S | — | soft | — |
| MCT / arch timer | `exynos_mct.c` 685 | `exynos_mct.c` (4210/4412 only) | arch timer only (works) | S (optional) | — | soft | — |
| Watchdog | `s3c2410_wdt.c`; DT `watchdog_cl0/1` exynos8/exynos7-wdt | `s3c2410_wdt.c`, no 9810 compat | none | S–M (PMU wdt-reset bits) | PMU syscon | soft (an unexpected reset is the failure mode) | Arm with a 30 s timeout and stop petting; expect a reset to 4.9 |
| RTC | `rtc-s2mps18` (via ACPM MFD) | rtc-s5m (no s2mps18) | none; `RTC_CLASS` off | S–M | S2MPS18 MFD | soft | Read the RTC, compare with host time |
| PMIC S2MPS18 (+ S2MPB02/B03, S2DOS05) | `K49/drivers/mfd/s2mps18_*`, `regulator/s2mps18.c` (mfd 8,324 / regulator 12,214 built); DT `s2mps18mfd@00`, `s2mpb02_pmic@59`, `s2mpb03_pmic@56`, `s2dos05_pmic@60` | sec-core/s2mps11 only to s2mpu02; **no s2mps18** | ACPM PMIC client only (`T/drivers/soc/samsung/acpm/acpm_mfd.c`, 284) | L (regulators, IRQ, RTC, power-key, powermeter) | ACPM MFD/SPEEDY | **damage** (wrong voltage) | **Read-only first**: dump all regulator ctrl regs and compare with 4.9 `/sys/kernel/debug/regulator` values |
| GPIO keys (power/vol/Bixby) | `gpio_keys.c`; DT49 `gpio_keys` (KEY_WINK 703) | generic gpio-keys | none | S | pinctrl | soft | Press a key and log the input event over serial. **This also gives the diag PID1 a manual input path** |
| USI/I2C/SPI/UART | usi_v2 (42 nodes), i2c-exynos5 + i2c-speedy, spi-s3c64xx (22 nodes), samsung serial | exynos-usi (9810 compat ours), i2c-exynos5 (ours), **spi-s3c64xx no 9810 compat**, samsung_tty (no 9810) | USI + HSI2C done (15fe129a7) | S per bus (DT); SPI M | clocks, pinctrl | soft | Probe each I2C client address; SPI loopback ID read |
| DMA PL330 | `pdma0` arm,pl330 | pl330 | none | S | clocks | soft | dmatest |
| PCIe (for Wi-Fi) | `pci-exynos` + 9810 cal (4,393 built); DT `pcie0@116A0000` + pcie-sysmmu | `pci-exynos.c` only `samsung,exynos5433-pcie` (`T/drivers/pci/controller/dwc/pci-exynos.c:380`) | none | L | PD, PHY CAL, sysmmu | soft | Link training + config-space read of the BCM4361 vendor/device id |

### 2.2 Storage and encryption

| Block | 4.9 | 6.12 mainline | In port | Diff | Depends | HW risk | Diag test |
|---|---|---|---|---|---|---|---|
| UFS host | `K49/drivers/scsi/ufs/` 13,480 built (ufshcd 9,129, ufs-exynos 1,289, ufs-cal-9810 682) | `ufs-exynos.c` (gs101/7/autov9/fsd; no 9810) | **done, RO-proven** | — | — | **brick** if writes reach boot LUNs/RPMB | done |
| UFS regulators (vcc/vccq/vccq2) | DT49 ufs node supplies | — | missing; inherited from bootloader | S (after S2MPS18) | PMIC | soft | Confirm no "vcc-supply" warning; runtime-PM cycles |
| RO guard → production policy | — | — | `ufshcd-readonly.h` blocks all writes | M: replace with a **per-LUN/partition allowlist** (data/cache/metadata/misc/persist only; never boot LUNs sdb/sdc or RPMB W-LUN) | — | **brick** | Write and read back a scratch file on `/cache` only (GUESS: cache exists in the by-name list, TR:1839ff) |
| FMP inline crypto | `K49/drivers/crypto/fmp/` 674 + `ufs-exynos-fmp.c` 196 + `ufshcd-crypto.c` 508; SMCs via EL3 | ACK blk-crypto + UFS crypto core | adapter staged (a0a1b647d), never run | L | EL3 SMCs, Trustonic for keys | brick-class for **data** (wrong IV/key → unreadable or corrupted /data) | **Read-only known-answer test**: read an fscrypt file with a known key on a test file system; compare FMP and software fallback. Never write /data until it matches |
| blk-crypto fallback | — | `BLK_INLINE_ENCRYPTION_FALLBACK=y` (CFG) | config on | S | — | same | Same test |
| SRPMB (TEE RPMB) | `K49/drivers/scsi/scsi_srpmb.c` 417 (`UFS_SRPMB=y`, K49/out/.config:1543) | none | none | M | UFS, Trustonic | **brick-class** (RPMB counter/keys; TEE secure storage) | Read-only RPMB counter query only |
| f2fs / ext4 / EROFS / dm-verity | 4.9 has f2fs, ext4, EROFS, DM_VERITY+FEC | yes | f2fs/ext4/erofs/dm-verity y; **DM_VERITY_FEC, F2FS compression off** (CFG) | S | — | soft | Mount system/vendor through dm-verity as Lineage's fstab does (GUESS: whether Lineage uses verity on crownlte is unverified) |

**Lineage 23 FBE requirements on this phone:** fscrypt v2, AES-256-XTS/CTS, `inlinecrypt_optimized`
(IV_INO_LBLK_64), standard (not wrapped) keys, and no metadata encryption found
(`R\encryption-port\README.md`). **GUESS:** this means `DM_DEFAULT_KEY` is not needed.
6.12 supports all of these. The hard dependencies are FMP correctness or the fallback, and above all
**keymaster through Trustonic** (vold's key-encryption keys are keymaster-bound).

### 2.3 USB

| Block | 4.9 | 6.12 | In port | Diff | Depends | Risk | Diag test |
|---|---|---|---|---|---|---|---|
| DWC3 + USB2/3 PHY | dwc3 9,856; phy-exynos-usbdrd 1,929 + usb3p1 1,017 + usbdp 500 | dwc3 generic; phy-exynos5-usbdrd (no 9810) | **USB2 device mode proven**; USB3/DP PHY not ported | M (USB3, DP combo PHY) | clocks, PD | soft | Enumerate at SuperSpeed on a USB3 host |
| Gadget functions | `drivers/usb/gadget` 29,526: f_mtp, f_ptp, f_accessory, f_audio_source, f_conn_gadget, f_dm, f_hid, FunctionFS (`USB_CONFIGFS_F_MTP=y` K49/out/.config:4016; `USB_ANDROID_SAMSUNG_COMPOSITE=y` :3962) | ACK has F_FS/ACC/AUDIO_SRC (F_MTP is not in 6.12) | F_FS, NCM, HID (custom) | S–M | — | soft | Run the Lineage `init.*.usb.rc` compositions. **GUESS:** Lineage MTP on crownlte may use FunctionFS MtpServer; this must be verified in the vendor/device rc |
| Type-C/PD, MUIC, host mode, notifier | `drivers/ccic` 12,337 (max77705_usbc/pd), `drivers/muic` 4,104, `drivers/usb/notify` 5,831 | TCPM/typec class exist; no max77705 PD | routing only (226 lines) | L | MAX77705 MFD | soft; **damage** if PD negotiates a wrong voltage | Read CC/PD status regs; OTG host with a flash drive |

### 2.4 Display and graphics

| Block | 4.9 | 6.12 | In port | Diff | Depends | Risk | Diag test |
|---|---|---|---|---|---|---|---|
| DECON/DSIM/DPP (fbdev "dpu_9810") | `K49/drivers/video/fbdev/exynos/dpu_9810/` 28,604 built + 9,263 h; DT `decon_f`, `decon_t`, 6× dpp, `dsim`, `disp_ss`; `CONFIG_EXYNOS_DECON_9810` | Exynos DRM only 5433/7 DECON; samsung-dsim only imx8m (`T/drivers/gpu/drm/bridge/samsung-dsim.c:2136`) | none; `FB`/`DRM` off (CFG) | **XL** | sysmmu disp, ION, sync_file, PD disp, DISP devfreq, BTS, S2DOS05, MIPI PHY (`phy-exynos8895-mipi.c` 461) | soft (panel stays dark); **GUESS** low damage risk | Phase 1: **simplefb** on 0xcc000000 if SBoot leaves the panel lit. Phase 2: DECON window-config test pattern; read back DECON status over serial |
| Panel S6E3HA8 + panel framework, MDNIE, COPR, AOD | `K49/drivers/video/fbdev/exynos/panel/` 18,548 built + 133,465 h; DT `panel_drv@001`, "s6e3ha8_crown_a3_s0_*" | upstream panels only s6e3ha2/fa7 | none | L | DSIM, S2DOS05, LCD SPI | soft | Read the panel ID over DSI (DCS 0x04) |
| DisplayPort / HDCP | dpu_9810 displayport, `EXYNOS_HDCP2` | none | none | L (late) | USB-C DP alt mode, SMC | soft | — |
| Mali-G72 kbase | `K49/drivers/gpu/arm/bv_r38p1` 65,001 built + exynos gpex 7,121; `MALI_RELEASE_NAME "r38p1-01bet0"` (Kbuild:74); JM UK **11.35** (`include/uapi/gpu/arm/bv_r38p1/jm/mali_kbase_jm_ioctl.h:131-132`); DT `mali@17500000` | panfrost lists `GPU_MODEL(g72, 0x6001)` (`T/drivers/gpu/drm/panfrost/panfrost_gpu.c:222`) but **is not ABI-compatible with the vendor blobs** | ported on G (331f966ba…2630fc320); 51 ioctls / 16 UAPI headers verified (`R\graphics-port\abi-result.json`); never linked or run | L (rebase + G3D PD/DVFS/DT + test) | PD g3d, G3D DVFS (ACPM cal-id), ION/dma-buf legacy compat, devfreq/BTS, thermal | soft (GPU hang); **GUESS** low damage given firmware-chosen voltages | `/dev/mali0` open, version-check ioctl (expects 11.35), GPU_PROP query, a tiny job via a kbase test client |
| G2D | `K49/drivers/gpu/exynos/g2d` 4,379; `/dev/g2d` | none | none | M | sysmmu g2d, ION | soft | Run a blit through `/dev/g2d` |
| Scaler / SMFC / ASTC / MFC v12 | scaler 5,138; smfc 3,053; astc 3,511; MFC 21,479 + 7,609 h | V4L2 s5p-mfc only to v10 (**GUESS**: no v12) | none | M / M / S / L | sysmmu, ION, devfreq | soft | v4l2-compliance; decode one H.264 clip |
| sync_file / fences | `SYNC_FILE`, `DPU_2_0_RELEASE_FENCE` | yes | `SYNC_FILE` off (CFG) | S | — | — | — |

### 2.5 Input

| Block | 4.9 | 6.12 | In port | Diff | Depends | Risk | Diag test |
|---|---|---|---|---|---|---|---|
| Touchscreen | `K49/drivers/input/touchscreen/sec_ts/y771/` 13,771 + `sec_cmd.c` 647; DT `touchscreen@48` "sec,sec_ts"; `/sys/class/sec/tsp` | none (stmfts/zinitix/mip4 only) | none | M | I2C, pinctrl, regulator (S2MPS18 LDO or fixed), firmware file | soft. **Don't let it flash touch firmware** (firmware update on probe can brick the touch IC: GUESS, plausible) | Log touch coordinates to serial while tapping |
| S Pen (W9018) | `K49/drivers/input/wacom/` 5,527; DT `wacom@56` "wacom,w90xx" | generic `wacom_i2c.c` (274), different protocol (GUESS) | none | M | I2C, `wacom_ldo_en`, pen-detect GPIO | soft (same firmware-flash caution) | Hover/tip events to serial |
| Hall sensors, keys, input booster | hall.c 414, certify_hall.c 400, gpio_keys | gpio-keys | none | S | pinctrl | soft | Toggle the cover and log |

### 2.6 Audio

| Block | 4.9 | 6.12 | In port | Diff | Depends | Risk | Diag test |
|---|---|---|---|---|---|---|---|
| ABOX (+GIC, RDMA/WDMA, UAIF), VTS, mailbox | `K49/sound/soc/samsung` 22,071 built; DT `abox@...` + children, `vts`, `mailbox`; firmware + `abox_rmem` | none | none | **XL** (DSP firmware boot, sysmmu aud, AUD devfreq, IPC) | sysmmu aud, PD aud, clocks, firmware files on vendor | soft | Boot ABOX firmware, read its version via IPC |
| Codec CS47L93 (madera) + MAX98512 amp + Maxim DSM | `K49/sound/soc/codecs` 19,801 built; DT `cs47l93@0` (SPI), `max98512@39` | `madera-core.c` supports cs47l93 (`T/drivers/mfd/madera-core.c:382`); max98512 not upstream (GUESS: max98373/98390 are different) | none | M (madera via SPI) + M (amp) | SPI, regulators, ABOX UAIF | **damage**: amp/speaker overdrive without DSM protection (GUESS) | Read codec chip ID over SPI; amp ID over I2C. Don't play audio until DSM limits are ported |

### 2.7 Connectivity

| Block | 4.9 | 6.12 | In port | Diff | Depends | Risk | Diag test |
|---|---|---|---|---|---|---|---|
| Wi-Fi BCM4361 (PCIe) | `K49/drivers/net/wireless/bcmdhd_101_16` **192,787** built; fw `/etc/wifi/bcmdhd_sta.bin`, `nvram_net.txt`; DT `wlan` "samsung,brcm-wlan" | brcmfmac has no 4361 id (`brcm_hw_ids.h`) | none; `CFG80211`, `RFKILL` off | L (forward-port DHD; it is self-contained, but huge) | PCIe, sysmmu, WLAN reserved mem, cfg80211 | soft | Firmware download + `wlan0` scan |
| Bluetooth | `drivers/bluetooth/bcm43xx.c` 404 (rfkill/power); HCI over UART from userspace; DT `bluetooth` | hci_bcm has no 4361 entry | none; `BT` off | S–M | UART (samsung_tty 9810 compat), rfkill, GPIOs | soft | `hciattach`/hci_uart, HCI reset |
| Modem (Shannon SS310AP SIPC) | `K49/drivers/misc/modem_v1` 12,673 + `mcu_ipc`/`shm_ipc` 1,289 + pmucal_cp; DT `mif_pdata`, `shmem@F6E00000`, `mcu_ipc@14170000`; reserved `modem_if` 146 MB; nodes `/dev/umts_boot0/ipc0/rfs0/...` | none | none | **XL** | PMU CP cal, ACPM (dvfs_cp), mailbox, reserved mem, secure-world CP image auth (GUESS) | **brick-class for EFS**: CP writes NV/EFS via RFS (`umts_rfs0`). Back up EFS/sec_efs/cpefs first | Boot CP with `cbd` from vendor in a RAM PID1; observe the CP-online state. No RFS writes until backups exist |
| GNSS (BCM4773/47531) + BBD | `drivers/gps/sec_gps_bcm47531.c` 354; transport `drivers/sensorhub/brcm/bbdpl/bcm_gps_spi.c`; DT `gps`, `BCM4773@0` | none | none | M (shared with sensors) | SPI (s3c64xx 9810) | soft | See sensors |
| NFC + eSE | `drivers/nfc` (sec_nfc, ese_p3) 2,110; DT `sec-nfc@27`, `ese_spi@0` | s3fwrn5 (NCI; vendor expects `/dev/sec-nfc`) | none | S–M | I2C, SPI, GPIO | soft (eSE is secure-world related; avoid writes) | Chip reset + firmware-version NCI command |

### 2.8 Sensors, biometrics, misc

| Block | 4.9 | 6.12 | In port | Diff | Depends | Risk | Diag test |
|---|---|---|---|---|---|---|---|
| Sensor hub (SSP on BCM4773 "BBD") | `K49/drivers/sensorhub/brcm` 18,551 built (`SENSORS_SSP_BBD=y`, K49/out/.config:5152); `/dev/ssp_sensorhub`, classes sensors/bbd | none | none | L | SPI, GNSS chip power, firmware | soft | Hub firmware handshake; read the accelerometer |
| HRM MAX86915, grip SX9320 | `drivers/optics/max86915.c` 6,074; DT `sx9320-i2c@22` | none (GUESS sx9310 is not 9320) | none | S–M each | I2C | soft | ID read |
| Fingerprint (Egis ET5xx) | `drivers/fingerprint` 2,814; DT `etspi-spi@0` | none | none | S–M | SPI, **Trustonic TA** (GUESS: the SPI bus is handed to the secure world during auth) | soft | Device node present; the TA works only with the TEE |
| Iris | 5F1 secure camera sensor + S2MPB02 IR LED (no separate driver) | none | none | part of camera (XL) | camera, secure camera ION 11, TEE | soft | Last |
| Vibrator | `drivers/motor` 1,057 (MAX77705 haptic); timed_output + sec motor | max77693-haptic only (77693/77843) | none | S–M | MAX77705 MFD | soft | Pulse 100 ms |
| LEDs | MAX77705 RGB, S2MPB02 flash (`drivers/leds` 3,317) | none | none | S | MFDs | soft (flash LED heat: keep the current low) | Blink |
| Samsung sysfs (`/sys/class/sec/*`), `sec_debug`, `sec_param`, `sec_reboot` | `K49/drivers/staging/samsung` 9,666 built | none | none | M (only the nodes Lineage HALs read) | — | soft | grep the vendor rc/HAL sources for `/sys/class/sec` paths |

### 2.9 Camera

| Block | 4.9 | 6.12 | In port | Diff | Depends | Risk | Diag test |
|---|---|---|---|---|---|---|---|
| FIMC-IS2 (ISP, CSIS, PDP, sensors 2L3/3M3/3H1/5F1, actuators, OIS, EEPROM, flash) | `K49/drivers/media/platform/exynos/fimc-is2` **116,096** built + 233,998 h | none | none | **XL**, last | sysmmu isp, ION camera heaps, secure camera SMC, CAM/INTCAM devfreq, S2MPB02/B03, ISP thermal | soft (**GUESS**: flash/IR-LED heat if misconfigured) | Sensor ID read over I2C first |

### 2.10 Power: charger, fuel gauge, battery

| Block | 4.9 | 6.12 | In port | Diff | Depends | Risk | Diag test |
|---|---|---|---|---|---|---|---|
| MAX77705 MFD core | `K49/drivers/mfd/max77705*` | only `maxim,max77705-battery` cells in simple-mfd-i2c (`T/drivers/mfd/simple-mfd-i2c.c:86-119`) | MUIC routing hack only | M | HSI2C (works), IRQ (pinctrl wakeup EINT) | soft | Dump the PMIC ID + IRQ regs |
| Charger + fuel gauge + `sec_battery` (+ IDT wireless `mfc-charger@3b`, CISD) | `K49/drivers/battery_v2` 21,072 | no max77705 charger; max17042 FG may partly fit (GUESS) | none | L | MAX77705 MFD, thermistors (ADC), TMU | **damage** (charge current/voltage, wireless coil) | **Read-only first**: report SoC/voltage/current from the fuel gauge; compare with 4.9 `/sys/class/power_supply` |

Note (GUESS): during 6.12 sessions without a charger driver, MAX77705 keeps the
bootloader/hardware-default charge settings. Android healthd will report no battery
(power_supply missing). Lineage may then misbehave: it might shut down for low battery or show no
battery. **A fuel-gauge read-only driver should land before the first full-Lineage boot.**

### 2.11 CPU/power management

| Block | 4.9 | 6.12 | In port | Diff | Depends | Risk | Diag test |
|---|---|---|---|---|---|---|---|
| cpufreq (ACME) | `exynos-acme.c` 1,342 (+UFC; cpufreq dir 6,085) | none | **other agent** (`cpufreq` worktree) | M | ACPM DVFS (done), thermal (done) | soft | `scaling_available_frequencies` + readback |
| MIF/INT/DISP/CAM/AUD/... devfreq + DVFS manager | `K49/drivers/devfreq` 6,471 built (9 domains); `exynos-dm` | none | old chain (never on HW) + **other agent** (`devfreq` worktree) | L | ACPM, BTS | soft (MIF too low = memory errors) | MIF level readback via CAL |
| Thermal throttling (cooling maps, GPU/ISP cooling, sec_thermistor) | `K49/drivers/thermal` 10,504 (exynos_tmu 1,881, gpu_cooling 1,134, isp_cooling 576) | generic OF thermal | sensor **done**; trips "logged, not programmed" (18d715b19) | M | cpufreq, GPU devfreq | **damage** without throttling at sustained load (hardware trip at 112–115 °C remains, per 0f6eb9410) | Load the big cluster and watch the cap engage at 83 °C (per the cpufreq doc) |
| cpuidle (C2/CPD/SICD) | `cpuidle-exynos64.c` 227 + `exynos-powermode.c` 987 (ACPM/flexpmu) | `cpuidle-psci` (needs PSCI idle-states in DT; the PSCI firmware must implement them; GUESS) | `CPU_IDLE` off | M (PSCI states) to L (cluster power-down via powermode/flexpmu) | ACPM, PMU | soft (hangs in idle) | Residency counters in `/sys/devices/system/cpu/cpu*/cpuidle` |
| Suspend-to-RAM | `exynos-pm.c` 648, flexpmu, pmucal_system; ACPM | none | `SUSPEND=y` but no platform ops | L | everything wake-capable, ACPM, PMIC, GIC wakeup | soft (fails to wake: needs a power-key long-press) | `echo mem > /sys/power/state` with an RTC/power-key wake |
| Hotplug / EMS scheduler | `exynos-cpu_hotplug.c` 768, `SCHED_EMS=y` (K49/out/.config:172) | EAS + uclamp in ACK | schedtune compat (8819c221c) | M (energy model from ECT, `SCHED_MC`) | cpufreq | soft | — |

### 2.12 Secure world

| Block | 4.9 | 6.12 | In port | Diff | Depends | Risk | Diag test |
|---|---|---|---|---|---|---|---|
| Trustonic MobiCore (TEE, keymaster/gatekeeper/keymint backends, TUI) | `K49/drivers/gud/gud-exynos9810` 10,939 built; DT `tee` "samsung,exynos-tee"; `/dev/mobicore`, `/dev/mobicore-user` | none | **ported** (2567fd87e), ABI-matched, never run; `TRUSTONIC_TEE=y` (CFG) | M–L (runtime: NWd/SWd IRQ, fastcalls, sec-os-booster with cpufreq) | ACPM? (booster), cpufreq, `mcDriverDaemon` from vendor, RPMB/SRPMB | **brick-class for keys/data** if the TEE secure storage is corrupted (GUESS) | Start `mcDriverDaemon` in PID1, open a session to the keymaster TA, `getHardwareInfo`. No key generation or upgrade on the real `/data` until verified |
| Other SMC users (secmem, CPP, HDCP, exyswd-rng, seclog) | `exynos-smc.c`, `secmem.c`, exyswd-rng | none | `exynos9810-cal-smc.c` (39) | S each | — | soft | hwrng read |
| Knox/RKP/TIMA/DEFEX/FIVE/PROCA | **not built** in 4.9 (UH, RKP, TIMA off; DEFEX/FIVE/PROCA absent) | — | — | none needed | — | — | — |

### 2.13 Android kernel requirements

The 4.9 kernel already carries many backports: fscrypt v2 + inline crypt, binderfs, EROFS, FUSE_BPF,
PSI, cgroup BPF, schedtune/EMS. The table compares the 6.12 port config (CFG) against them.

| Requirement | CFG state | Action |
|---|---|---|
| Binder + binderfs, memfd (ashmem gone in ACK 6.12) | y | done. **GUESS:** Lineage 23 libcutils uses memfd when ashmem is absent; verify the vendor blobs don't open `/dev/ashmem` directly |
| SECCOMP / SECCOMP_FILTER | **off** | must enable (zygote/app sandbox) |
| NETFILTER, iptables/ip6tables, xt_bpf/owner/quota2, conntrack, NET_SCHED/cls_bpf, TUN, VETH | **off** | must enable (netd, tethering, VPN) |
| BPF_SYSCALL/JIT, CGROUP_BPF | y | done (netd BPF needs netfilter too) |
| FUSE (+ passthrough / FUSE_BPF) | **off** | must enable (MediaProvider `/sdcard`) |
| PSI, CPUSETS, MEMCG, UCLAMP_TASK(+GROUP), SCHED_MC, ENERGY_MODEL | PSI/CPUSETS/SCHED_MC/EM **off**; MEMCG, UCLAMP y | enable (lmkd needs PSI; task_profiles need cpusets) |
| INCREMENTAL_FS | off | enable (GUESS: needed for adb incremental installs only) |
| ZRAM (+ zsmalloc) | off | enable |
| DMABUF_HEAPS (system/cma), SYNC_FILE, CMA | off | enable (+ legacy ION for vendor) |
| HID, UHID, INPUT_EVDEV | **off** | must enable (no input at all otherwise) |
| fscrypt v2 + inline crypt, blk-crypto fallback | y | done |
| DM_VERITY_FEC, DM_DEFAULT_KEY, DM_BOW, DM_USER, DM_SNAPSHOT | off | enable FEC; others probably unneeded (no metadata encryption, no dynamic partitions, no VAB; GUESS) |
| F2FS compression | off | GUESS: not used by this fstab |
| STATIC_USERMODEHELPER, MODULES/MODULE_SIG | off | monolithic is fine (the vendor has no modules; GUESS); keep MODULES off for now |
| PSTORE_PMSG, RTC | off | enable (pmsg feeds last-logcat; RTC needed for alarms/time) |
| SELinux, AUDIT | y (DEVELOP, permissive used in trials) | enforcing must eventually work (needs a correct sysfs/dev label set) |
| CFI/SCS/LTO, hardening | off | optional; enable late |
| Samsung legacy expectations: `/dev/stune`, `/dev/cpu_online_{min,max}`, sec sysfs, `sdcardfs` (off in 4.9 too) | stune done | add pm_qos misc devices + sec sysfs stubs |
| `BLK_DEV_WRITE_MOUNTED` | off in CFG, y in the Oct-18 v2 config | must be y for apexd (trial #20 vs #22) |

**Recommended approach (GUESS):** derive the production config from
`T/arch/arm64/configs/gki_defconfig` (648 `=y` lines) plus a crownlte fragment. Do not keep growing
the diagnostic config. VINTF and the Android 16 kernel compatibility matrix: the vendor
`first_api_level` is 27 (`/root/kernel-upgrade/diagnostic-system-init-20261018-build/rootfs/android-root/prop.default`)
and the phone already boots Android 16 on 4.9. **GUESS:** either kernel-requirement checks are
relaxed in this build, or they target a legacy FCM level; 6.12 will satisfy at least as much as 4.9.

---

## 3. Dependency-ordered plan

### 3.1 Dependency graph (condensed)

```
ACPM/CAL/ECT/FVMap (DONE) ─┬─ cpufreq [agent] ─┬─ thermal cooling ─ EAS/energy model
                           │                   └─ Trustonic sec-os-booster
                           ├─ devfreq MIF/INT [agent] ── BTS ── DISP/CAM/AUD/G3D devfreq
                           ├─ TMU (DONE) ── cooling maps
                           ├─ S2MPS18 MFD (RO→RW) ── regulators ── RTC ── power key
                           │                       └─ UFS vcc/vccq, touch/pen/panel LDOs
                           └─ PMU/genpd ── sysmmu ── ION/CMA ─┬─ DECON/DSIM/DPP ── S6E3HA8 ── HWC/SF
                                                             ├─ Mali r38p1 (G line) ── GLES/Vulkan
                                                             ├─ G2D, MFC, scaler, SMFC
                                                             ├─ ABOX ── madera/max98512 ── audio HAL
                                                             └─ FIMC-IS2 (camera, iris)
Android config (SECCOMP/NETFILTER/FUSE/PSI/CPUSETS/HID/EVDEV/heaps) ─ real init from UFS
Trustonic runtime ─ keymaster ─ vold ─ FMP or blk-crypto fallback ─ /data RW ─ full Lineage boot
SPI s3c64xx 9810 ─ SSP/BBD sensorhub + GNSS, fingerprint, eSE, CS47L93 codec
PCIe exynos 9810 ─ bcmdhd 4361 Wi-Fi;  UART ─ BT
MAX77705 MFD ─ fuel gauge (RO) ─ charger ─ PD/CCIC, haptic, RGB LED
CP PMU/mailbox/shmem ─ modem_v1 SIPC ─ RIL   (after EFS backups)
cpuidle (PSCI) ─ suspend (exynos-pm/flexpmu, all wake sources)
```

### 3.2 Milestones

**M0 — Done:** diagnostic boot, ACM console, UFS RO, ACPM/DVFS/FVMap, SMP, TMU (§1.1).

**M1 — Platform core.** Largely build-only, plus small hardware selftests.
1. cpufreq *[in progress, other agent]* and MIF/INT devfreq *[in progress, other agent]*.
2. Thermal cooling maps on the TMU zones (after cpufreq); keep the big cap until throttling is
   observed.
3. S2MPS18 MFD **read-only** + RTC + power-key; gpio-keys (volume keys). These give the diag PID1 an
   input channel.
4. PMU genpd provider (15 PDs) + exynos-iommu DT for the 9810 SysMMUs + CMA + ION rebase (from G).
5. Watchdog (gives an automatic reset on hangs instead of waiting for the 120 s auto-return).
6. MAX77705 MFD + fuel gauge **read-only** (battery %, current) → healthd input.

Gate: one diag image that reports regulator dump, RTC, keys, PD on/off, an sysmmu map test, ION
alloc and fuel gauge values over serial.

**M2 — Android-complete kernel.** Build-only; parallel with M1.
1. Production config = gki_defconfig + crownlte fragment (§2.13 list). Keep the diag config as a
   separate fragment.
2. Samsung userspace shims: `/dev/cpu_online_{min,max}`, the `/sys/class/sec` nodes that Lineage
   HALs/rc touch. This needs a grep of the **vendor partition**: see action item A1.
3. Production DT: split `exynos9810-crownlte-delayed-ufs-diag.dts` into an `exynos9810-crownlte.dtsi`
   board file plus per-subsystem `.dtsi` (one owner per file, to avoid merge conflicts between
   agents).
4. Re-run Codex's RAM-namespace Android stages (system-init → apex → linkerconfig → ART) on the new
   kernel; check whether `derive_classpath` still crashes (§1.4 GUESS).

Gate: the ART + derive_classpath stage passes untraced.

**M3 — Secure world + storage write path.** Hardware; slow and careful.
1. Prerequisites: full **backups via 4.9** of EFS, sec_efs, cpefs, persist, param and every
   non-userdata partition. GUESS for partition names; check `/dev/block/by-name`.
2. Trustonic runtime: start `mcDriverDaemon` from vendor in PID1; keymaster TA `getHardwareInfo`.
3. UFS write policy: allowlist (never boot LUNs/RPMB) → first writes to `/cache` only.
4. FMP known-answer test vs blk-crypto fallback on a scratch f2fs image (loop file on `/cache`).
   Choose FMP if it matches; otherwise run the fallback (slower, but the same on-disk format).
5. SRPMB read-only query, then (only if needed by the TEE) the RW path.

Gate: vold-equivalent unlock of the DE keys on the real `/data`, mounted **read-only** first.

**M4 — Lineage boots from UFS, headless.** The 6.12 kernel stays in RECOVERY; BOOT keeps 4.9 as the
fallback (as in all trials so far).
1. RECOVERY = shim + 6.12 + **Lineage's normal first-stage ramdisk** (the BOOT ramdisk, 1,512,594
   bytes; header of `C:\Users\Patrick\tools\backup-20261103\BOOT.img`) instead of the diag PID1.
   GUESS: the BOOT ramdisk is Magisk-patched.
2. Full fstab mount, `/data` RW, adbd. **GUESS:** without HWC, SurfaceFlinger will crash-loop and
   `sys.boot_completed` won't be reached. The goal is a stable adb shell + logcat.

Gate: adb shell in the real Lineage; logcat shows system_server waiting on SF/graphics only.

**M5 — Display, GPU, touch: "Lineage UI on 6.12".**
1. Interim: simplefb on 0xcc000000 (only useful if SBoot leaves the panel powered; unverified).
2. dpu_9810 (DECON-F, DSIM, DPP, win-config, vsync, fences, BTS) + S6E3HA8 panel + S2DOS05 + DISP
   devfreq. This is the XL item.
3. Mali r38p1 rebased from G onto the main line + G3D PD/DVFS/DT; check UK 11.35 against the vendor
   `libGLES_mali.so`.
4. G2D (HWC uses it; GUESS: the HWC can fall back to GLES if G2D is absent).
5. sec_ts touch + W9018 pen. Touch firmware update **disabled**.

Gate: **Lineage boots to the lock screen from real UFS with display, touch and adb.** This is the
requested target.

**M6 — Daily-driver parity.**
- Charger (RW, after RO validation) + PD/CCIC + wireless charging
- Wi-Fi (PCIe + bcmdhd) and BT
- Audio (ABOX + madera + MAX98512 with DSM limits)
- Modem/RIL (after M3 backups)
- Sensorhub + GNSS (SPI s3c64xx 9810)
- MFC v12 (video playback/recording)
- Vibrator, LEDs, hall, NFC
- Fingerprint (needs the TEE)
- cpuidle (PSCI states), then suspend-to-RAM
- SELinux enforcing

**M7 — Full parity:** camera FIMC-IS2 (+ iris), DisplayPort alt-mode + HDCP, scaler/SMFC/ASTC, the
remaining Samsung sysfs.

**M8 — Full performance:**
- Lift the big-cluster cap to 2314 MHz once thermal throttling is proven (cpufreq doc).
- Energy model from ECT + EAS/uclamp (replacing EMS).
- GPU DVFS + IPA/power_allocator.
- devfreq governors and MIF floors tied to CPU levels.
- Then benchmark against 4.9 (GUESS: sustained performance will hinge on the thermal policy).

### 3.3 What can run in parallel (build-only, no phone)

The phone is a serial resource. Each trial costs about 8–10 min, counting the auto-return, the
RECOVERY restore and the AshLooper `boot=booted` wait (memory file). **Batch several driver
self-tests into each diag image.**

Independent lanes; each needs only the 4.9 source + the 6.12 tree + compile checks:

| Lane | Items | Coupling |
|---|---|---|
| L1 config/integration | §2.13 production config; Samsung shim devices; DT restructuring | touches everyone's DT: assign **one DT owner** |
| L2 PMIC/power | S2MPS18 MFD/regulator/RTC/pwrkey, S2MPB02/B03, S2DOS05, genpd, watchdog | independent of cpufreq/devfreq |
| L3 memory/IOMMU | exynos-iommu 9810 DT + IOVMM shim, CMA, ION rebase from G | needed by L5/L7/L9/L10 |
| L4 MAX77705 | MFD core, fuel gauge (RO), charger, CCIC/PD, haptic, RGB LED | independent |
| L5 display | dpu_9810 + panel forward-port (fbdev) | needs L3 APIs (can stub them) |
| L6 GPU | rebase the Mali/ION G line onto 0f6eb9410; G3D PD/DVFS/DT; link it | needs L3; coordinate with the devfreq agent |
| L7 input | sec_ts y771, W9018, gpio-keys, hall | independent |
| L8 connectivity | PCIe exynos 9810 + bcmdhd_101_16; BT UART/rfkill; sec_nfc | independent |
| L9 audio | ABOX/VTS/mailbox + madera CS47L93 (SPI) + MAX98512 | needs L3, SPI |
| L10 sensors/GNSS | spi-s3c64xx 9810 compat; SSP/BBD sensorhub; GNSS power; HRM/grip; ET5xx | SPI shared with L9 |
| L11 modem | modem_v1 + mcu_ipc + shm_ipc + CP pmucal | independent; HW test only after EFS backup |
| L12 secure world | Trustonic runtime readiness, SRPMB, FMP KAT harness (host-side) | gates M3 |
| L13 camera | FIMC-IS2 (start last; huge) | needs L3, devfreq |
| L14 vendor ABI audit | see A1 | unblocks L1/L5/L6/L9 decisions |

Already being worked by other agents (do not duplicate): cpufreq, MIF/INT devfreq, TMU debugging.

### 3.4 Immediate action items

- **A1 (highest leverage, no risk):** obtain read-only copies of `/vendor`, `/odm`, `/system`
  (ext4, sda18–20) for offline analysis. Pull them with the 4.9 Lineage over adb into a
  non-sandbox directory, e.g. `/mnt/ssd/host-builds/kernel-upgrade/vendor-ro/`. Then grep the
  blobs/rc files for every `/dev/*`, `/sys/*` and ioctl the HALs use, plus:
  - `libGLES_mali.so` version strings (confirm r38p1 / UK 11.35)
  - the libion flavour (legacy vs dma-heap)
  - the `init.*.usb.rc` MTP implementation
  - the Wi-Fi/BT/ABOX firmware paths

  No such copy exists on gekko outside the forbidden sandbox. A search of
  `/mnt/ssd/{host-builds,rea-work}` and `/root/kernel-upgrade` found no vendor images.
- **A2:** back up all small non-userdata partitions (especially EFS-class ones) before any M3/M6
  hardware work.
- **A3:** rebase the G (Mali/ION) line onto `crownlte-acpm-tmu`; drop or park the legacy-* thermal
  drafts.

---

## 4. Risks and vendor ABI constraints

### 4.1 Top risks

| # | Risk | Class | Mitigation |
|---|---|---|---|
| 1 | UFS writes reaching boot LUNs (sdb/sdc hold the bootloader; GUESS from typical Samsung UFS layout) or the RPMB W-LUN | **bricking** (may need JTAG/EDL-equivalent; Download Mode lives on those LUNs) | Keep the SCSI guard as a per-LUN allowlist, even in production; never expose sdb/sdc/RPMB block devices to userspace except read-only |
| 2 | `/data` corruption from FMP IV/key mismatch or f2fs bugs | **data loss** | KAT on a scratch image; first `/data` mount RO; backup of userdata ciphertext (large) or accept the risk explicitly |
| 3 | TEE secure storage / RPMB counter corruption; keymaster key upgrade on a different kernel | **data loss** (keys) | Never let keystore upgrade or generate keys on the real `/data` until the TEE is verified; GUESS: Trustonic may bind keys to the OS patch level/version via keymaster `os_version`, so a kernel/patch-level change might trigger key upgrades. Check the boot image header os_version handling |
| 4 | EFS/NV corruption via the modem RFS | **IMEI loss** (effectively bricks telephony) | Back up EFS/sec_efs/cpefs first; RFS RO for the first CP boots |
| 5 | PMIC regulator voltage/current errors (S2MPS18, S2DOS05, S2MPB02) | **hardware damage** | RO dump first; compare against the 4.9 values; copy constraints verbatim from DT49; no voltage writes until compared |
| 6 | Charger / PD / wireless-charger misconfiguration | **battery hazard** | RO fuel gauge first; charger RW only after register-by-register parity with 4.9 |
| 7 | Thermal runaway at full clocks | damage-limited | Hardware trip is armed by the bootloader (rise7 112–115 °C, 0f6eb9410); keep the big cap until throttling is proven |
| 8 | Touch/pen/NFC firmware auto-update in ported drivers | **component brick** | Compile out firmware-update paths until needed |
| 9 | Amp without DSM protection | speaker damage | No playback until DSM/limits are ported |
| 10 | Panic or hang on boot | soft | Auto-return to 4.9 BOOT (since b9d1a4d76); watchdog; Download Mode + Odin worked in trial #1 (TR ~133144–135400) |

### 4.2 Kernel ABI the Lineage vendor userspace pins

Lineage build facts (TR:1681–1700, TR:94512):
- `lineage-23.2-20260814-UNOFFICIAL-crownlte`, Android 16 (SDK 36.1), build BP4A.251205.006
- bootloader N960FXXU9FVG2
- vendor `first_api_level=27`
- no dynamic partitions; system/vendor/odm are ext4 sda18/19/20

| Interface | Pinned ABI | Source |
|---|---|---|
| GPU | Mali kbase **r38p1-01bet0**, JM UK **11.35**, `/dev/mali0`, Samsung private ioctls, `MALI_DMA_BUF_LEGACY_COMPAT` | `K49/drivers/gpu/arm/bv_r38p1/Kbuild:74`; `.../jm/mali_kbase_jm_ioctl.h:131-132`; `T/Documentation/crownlte/bringup.txt` |
| Memory | Legacy (handle-based) Samsung **ION** `/dev/ion`, heaps crypto=1, vstream=3, camera=10, secure_camera=11, camera_contig=12; `libion_exynos.so` | DT49:55–160; `G/drivers/staging/android/uapi/exynos_ion_uapi.h:12` |
| Display | fbdev DECON: `S3CFB_WIN_CONFIG` ('F',209), `S3CFB_SET_VSYNC_INT` 206, `S3CFB_POWER_MODE` 223, `EXYNOS_DISP_INFO` 260, HDR 400/401, display-mode 700–705; HWC = `hwc3-service.slsi` built from slsi-linaro lineage-21 source (`ExynosPrimaryDisplayFbInterface`) | `K49/drivers/video/fbdev/exynos/dpu_9810/decon.h:1726-1767`; `R\android-hwc-runtime-original.json`; `R\hwc-brightness\source-manifest.json` |
| Backlight/panel sysfs | `/sys/class/backlight/panel/max_brightness` (44700), `/sys/class/lcd/panel/*`, mdnie | `R\android-display-mapping-original.json` |
| 2D | `/dev/g2d` (Samsung G2D uapi) | §2.4 |
| TEE | `/dev/mobicore`, `/dev/mobicore-user`, `/dev/t-base-tui`; `mcDriverDaemon`, keymaster/keymint/gatekeeper Trustonic HALs | `/root/kernel-upgrade/artifacts/trustonic-abi/result.json` |
| Modem | `/dev/umts_boot0`, `umts_ipc0/1`, `umts_rfs0`, `umts_router`, `umts_dm0`, `umts_cass`; `rild`/`cbd` | Agent extraction from vendor file_contexts (`/root/kernel-upgrade/diagnostic-system-init-20261018-build/rootfs/android-root/vendor_file_contexts`) |
| Audio | ABOX/VTS (`/dev/vts_fio_dev`), madera, MAX98512 DSM | same file_contexts |
| Sensors/GNSS | `/dev/ssp_sensorhub`, `/dev/shub_*`, `/dev/ttyBCM*` | same |
| Scheduling | `/dev/stune` (schedtune) | TR:14875; ported (8819c221c) |
| Samsung pm_qos | `/dev/cpu_online_min`, `/dev/cpu_online_max` | vendor file_contexts (agent) |
| USB | configfs + FunctionFS adbd (proven); MTP via f_mtp in 4.9 (`K49/out/.config:4016`); GUESS whether Lineage uses FFS-MTP | §2.3 |
| Encryption | fscrypt v2, `inlinecrypt_optimized`, standard keys | `R\encryption-port\README.md` |

**Consequence:** Mainline-style replacements would require replacing the matching vendor HALs and
blobs. Where the HAL is built from source, that is feasible: the HWC (slsi-linaro), and possibly
gralloc/libion. It is not feasible for Mali (closed blob), Trustonic, the RIL or the camera HALs.
**Recommendation:** forward-port the Samsung 4.9 drivers with their UAPI unchanged, as Codex already
did for Mali, ION and Trustonic.

---

## 5. Open questions (unverified; cheap to resolve)

1. Does SBoot leave the panel lit at kernel entry, so that simplefb can work? (Static SBoot analysis
   only: `diagnostic-initramfs.txt`.)
2. Exact touch IC (Y761 vs Y771 family), NFC chip model, and the 9810 SysMMU version.
3. Does Lineage 23 on crownlte use dm-verity, FFS-MTP, or `/dev/ashmem` from vendor blobs? (A1.)
4. Do the PSCI firmware's idle states exist? Check the 4.9 `exynos,idle-state` vs PSCI suspend
   params in DT49.
5. The cause of the derive_classpath SIGSEGV (§1.4).
6. Whether Trustonic keymaster keys are bound to the OS version/patch level (risk #3).
