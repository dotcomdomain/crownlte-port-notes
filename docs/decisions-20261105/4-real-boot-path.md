# Decision 4: from ROM-in-RAM to a real installed 6.12 Lineage boot

2026-11-05. Research only. Sources:
- `rom-ram/RUN-SHEET.md` ("Diagnostic deviations")
- `Documentation/crownlte/trustonic-tee.md` on branch `crownlte-tee` (T1-T5)
- `roadmap/ROADMAP.md` §2.2, §2.5-2.12
- `roadmap/forced-reset-boot-selection.md`
- backups in `C:\Users\Patrick\tools\backup-20261103`

Effort is in agent-days and is a GUESS: S ≤ 1, M 2-5, L 1-3 weeks, XL > 3 weeks.

## TL;DR

- **The safest order is TEE → keys (read-only) → BOOT switch → read-write /data, with HALs last.** Each step is reversible until the first read-write mount of /data. That mount is the one-way door: take a full raw USERDATA backup first. The phone's USERDATA is about 110 GB (GUESS for a 128 GB unit). It does not fit on gekko, which has 4.2 GB free with the root disk full. The Windows PC C: has 189 GB free.
- **Keep the keymaster version inputs identical to today's:**
  - current BOOT and RECOVERY headers: `os_version 16.0.0`, `os_patch_level 2026-08`;
  - same Lineage system/vendor build;
  - same security-patch properties.

  Any difference can trigger a **key upgrade**, which rewrites the wrapped FBE keys. 4.9 might then be unable to read /data.
- **Never:** RPMB writes (`ufs-srpmb` stays unported; the RPMB W-LUN stays blocked), writes to EFS, CPEFS, KEYSTORAGE, PERSISTENT or PARAM before their own stage, or a TEE secure-storage commit outside tmpfs.

## 1. What ROM-in-RAM fakes today (RUN-SHEET "Diagnostic deviations")

- First-stage init is emulated; the `androidboot.*` cmdline is substituted, not the real SBoot values; SELinux is permissive.
- `mount_all`/`swapon_all` are commented out; `init_user0` is bounded to 60 s.
- Bypasses: `keystore.module_hash.sent` and `odsign.*`.
- `/data` and `/cache` are tmpfs; EFS, USERDATA, CACHE, PERSISTENT, CPEFS and MISC are never opened.
- The UFS SCSI guard blocks every write; TEE off; software keymaster.
- The kernel boots from RECOVERY via the uniLoader shim, and the 300 s timer returns the phone to 4.9 BOOT.

## 2. Steps beyond ROM-in-RAM

| # | Step | What it takes | Irreversible? | Effort |
|---|---|---|---|---|
| A | **TEE T1**: kernel starts Kinibi, self-test only | 4.9 starts the TEE inside `mobicore_probe` (`K49/drivers/gud/gud-exynos9810/MobiCoreDriver/main.c:624-723`): MEM_TRACE 0xFF00000A, INIT 0xFF000001, N_SIQ, INFO poll with no timeout, IRQ SPI 233, GET_VERSION, SWAP_CPU 0xFF000036 to CPU1. Ported unchanged on `crownlte-tee` 4e81ce065, started from a kthread; no TA, no storage | no (SoC reset clears it) | S + 1 trial |
| B | **TEE T2/T3**: mcDriverDaemon + keymaster@3.0 (`keystore.mdfpp`) in ROM-RAM, `getHardwareInfo`/`addRngEntropy` only | EFS from a **read-only copy**, `/data/vendor/mcRegistry` on tmpfs, strace/inotify audit of write attempts | no, provided EFS is RO and the registry is tmpfs | M |
| C | **Storage write policy** | replace the all-writes-blocked `ufshcd-readonly.h` with a per-partition allowlist: USERDATA (+ CACHE) only; boot LUNs sdb/sdc, the RPMB W-LUN, EFS, CPEFS, PERSISTENT, PARAM, UP_PARAM, STEADY, KEYSTORAGE and MISC stay read-only | the policy itself, no | M |
| D | **Crypto known-answer test**: FMP inline crypto vs `blk-crypto-fallback` on a scratch fscrypt image (v2, AES-256-XTS/CTS, IV_INO_LBLK_64, `inlinecrypt_optimized`) | adapter a0a1b647d (never run); fallback is config-only | no | M |
| E | **T4: real key unwrap, /data read-only**: real first-stage mounts (system/vendor/odm RO), real `vold init_user0` against USERDATA mounted **ro**; read back known files | keymaster through the TEE; gatekeeper too if the owner has a lock-screen PIN (CE keys need the credential, which needs **touch**, decision 2) | **can be**: vold/keystore2 may try to *upgrade* or *fixate* keys. With /data RO those writes fail; confirm vold treats them as non-fatal | M-L |
| F | **BOOT instead of RECOVERY** | see §3 | no, with the dual-kernel design | M |
| G | **T5: first read-write boot** | /data rw via the allowlist, real fstab, SELinux enforcing, no bypasses, real SBoot cmdline passed through the shim | **yes**: /data metadata, key versions, app data. Only reversible by restoring the raw USERDATA backup (keys stay valid if no upgrade happened) | S after E |
| H | **Charger/fuel gauge, touch** | decisions 3 and 2 | no | S-M each |

### HALs (after G; each is optional for booting, most only remove a feature)

| HAL | 4.9 driver | State | Risk | Effort |
|---|---|---|---|---|
| Display + GPU | DECON fbdev + Mali r38p1 | **works** (adopt mode); panel-dark issue is decision 1 | soft | — |
| Touch | sec_ts y771 | stage 1 built; power is decision 2 | soft; no firmware flash | S |
| Fingerprint (Egis ET5xx) | `drivers/fingerprint` + Trustonic TA | none | soft; needs TEE (B) | M |
| Wi-Fi (BCM4361 PCIe) + BT | bcmdhd 192 k lines, bcm43xx rfkill | none | soft | L / S-M |
| Sensors (SSP on BCM4773) + GNSS | `sensorhub/brcm`, bbdpl | none | soft | L |
| Audio (ABOX + CS47L93 + MAX98512) | `sound/soc/samsung` | none | **damage** (amp without DSM limits) | XL |
| Modem (Shannon SIPC) | `misc/modem_v1` | none | **EFS/NV writes via RFS**: fresh EFS/CPEFS backup first | XL |
| Camera (FIMC-IS2) | 116 k lines | none | soft (flash/IR LED heat) | XL |
| cpuidle / suspend | flexpmu, powermode | in progress | soft; needed for battery life | L |

## 3. Booting from BOOT

SBoot picks RECOVERY only when PARAM `REBOOT_MODE == 4`, which it sets from INFORM3 on `adb reboot recovery`. Otherwise it boots BOOT (forced-reset-boot-selection.md §1.1). Partition sizes: BOOT 55 MiB, RECOVERY 65 MiB. Uncompressed kernel Images: 4.9 37.6 MB, 6.12 20.5 MB.

Options:
1. **Swap**: 6.12 boot.img in BOOT, 4.9 kept in RECOVERY. This is simple, but every hang or forced reset lands in 6.12, and RECOVERY would then need the Lineage boot ramdisk (`bootmode=2` is appended; GUESS whether Lineage init cares).
2. **Dual-kernel BOOT (recommended).**
   - BOOT = shim + compressed 4.9 + compressed 6.12 + both DTBs + the Lineage ramdisk; 4.9 is the default.
   - 6.12 runs only when a **volatile one-shot token** is set: an INFORM value written by 4.9 at `reboot 612`, which the shim clears before it jumps. This is the same "volatile only" idea as shim fix bbea959.
   - Any watchdog, panic or forced reset returns to 4.9.
   - Requirements: shim decompression and pass-through of SBoot's real `bootargs`/`androidboot.*` and DTB to whichever kernel runs. The 4.9 DTB comes from the DTB partition, so check whether SBoot or the shim supplies it.
   - The BOOT header must keep **os_version 16.0.0 / patch 2026-08**.
   - Reversible: Odin BOOT restore from `backup-20261103\BOOT.img`. Download Mode is in SBoot, which is never touched.
3. Keep RECOVERY for diagnostics only, as today.

## 4. Irreversible steps and their guards

| Hazard | Where it could happen | Guard |
|---|---|---|
| RPMB write (anti-rollback, TEE storage) | `ufs-srpmb`, TEE storage via RPMB | not ported; RPMB W-LUN in the UFS deny list forever; log any RPMB command |
| Keymaster key upgrade / wrapped-key rewrite | E, G (keystore2/vold on version mismatch) | identical os_version/patch, same build; /data RO in E; record `ro.build.version.security_patch`, `ro.vendor.build.security_patch`, `ro.build.version.release` from 4.9 first; compare at E |
| /efs writes (TEE auth token, modem NV) | B (daemon), modem stage | EFS mounted from a RO copy until the modem stage; backups EFS.img/CPEFS.img 2026-11-03 |
| TEE secure-storage (TbStorage) commit | B, E | registry on tmpfs in B; in E/G only after B showed which files it writes |
| /data writes | G onward | full raw USERDATA backup (via 4.9 root `dd`, read-only) to the PC first |
| PARAM/MISC/KEYSTORAGE writes | reboot-to-recovery paths, vold | deny-list in C; reboot selection stays INFORM-based (volatile) |

## 5. Recommended sequence

1. **Read-only prep.** Record the 4.9 properties, confirm the 2026-11-03 backups, and take a raw USERDATA backup to the PC.
2. **A** (TEE T1) in ROM-RAM.
3. **B** (T2/T3) in ROM-RAM.
4. **D** (crypto KAT).
5. **C** (write allowlist, still with no data partition allowed).
6. **F** (dual-kernel BOOT, still booting ROM-RAM mode via token).
7. **E** (T4 /data RO). Touch (decision 2) is needed first if the owner has a PIN.
8. **G** (T5 RW) after an explicit owner go.
9. HALs: charger, Wi-Fi, sensors, fingerprint, audio, modem, camera.

Rough total to a daily-usable RW boot without Wi-Fi, audio or modem: about 4-6 agent-weeks. Each XL HAL adds 3+ weeks.

## 6. Owner decisions

| # | Decision | Recommendation |
|---|---|---|
| 1 | Approve TEE T1 (kernel start, no TA) | **Yes**, now |
| 2 | Approve T2/T3 with a RO EFS copy and tmpfs registry | **Yes** after T1 passes |
| 3 | Boot-selection design: dual-kernel BOOT with volatile one-shot token vs swap BOOT/RECOVERY | **Dual-kernel** |
| 4 | Pin os_version/patch to 16.0.0 / 2026-08 and freeze Lineage OTAs until G is done | **Yes** (an OTA changes the key-upgrade inputs) |
| 5 | Take a raw USERDATA backup to the Windows PC before E | **Yes, mandatory** before G; recommended before E |
| 6 | Does the phone have a lock-screen PIN/pattern? | Owner to answer: it decides whether touch is needed before E |
| 7 | First read-write /data boot (G) | Explicit go only after E shows identical keys and no upgrade attempts |
| 8 | Modem/EFS read-write | Last; only after a fresh EFS/CPEFS backup |
