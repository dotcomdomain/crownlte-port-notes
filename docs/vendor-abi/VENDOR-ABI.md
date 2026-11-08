# Kernel ABI expected by LineageOS 23 userspace on SM-N960F (crownlte, Exynos 9810)

Research snapshot 2026-11-04. Read-only: no phone access, no builds. The source is the raw partition images
`backups-20261103/partitions/{VENDOR,ODM,SYSTEM}.img`, mounted `ro,loop,noload` under `../../vendor-ro/` and
unmounted afterwards. Build: `lineage_crownlte 16 BP4A.251205.006` (V:/build.prop).

Every claim cites a file inside an image. Prefixes: `V:` = VENDOR.img (`/vendor`), `O:` = ODM.img (`/odm`),
`S:` = SYSTEM.img (system-as-root), `K49` = `/root/crownlte-hid-kernel`, `A` = 6.12 worktree `android-config`,
`ACFG` = `build/android-config-clean-6.12.93/.config`, `G` = `/root/kernel-upgrade/graphics-cap-6.12.93`,
`RAW` = `raw/` next to this file. Inferences are marked **GUESS**.

Files in this directory:
- `VENDOR-ABI.md`: this report (the tables are generated)
- `vendor-abi.json`: the same 102 rows, machine-readable (`meta` plus `rows[]` with subsystem, item, value, evidence, stock_6_12, provider_4_9, port_6_12, priority, guess, note)
- `tools/gen_vendor_abi.py`: the single data source that writes the JSON and the tables. Edit it, then run `python3 -I tools/gen_vendor_abi.py .` and `cat tools/head.md vendor-abi-table.md > VENDOR-ABI.md`
- `tools/ioctl_scan.py`: rebuilds 32-bit ioctl constants from `llvm-objdump-18 -d` output (handles `mov`/`movk` pairs)
- `raw/`: `rc-paths.txt` (2,119 /dev,/sys,/proc references in vendor rc files, with file:line), `elf-paths.tsv` (841 path strings from vendor ELFs), `libGLES_mali-ioctls.txt`, `display-ioctls.txt`, `mixer-ctls.txt`, `sepolicy-{dev-contexts,sys-file-contexts,genfscon}.txt`

Priority legend: **B** = blocks boot to a usable home screen (including /data unlock and touch). **F** = blocks a feature. **C** = cosmetic or diagnostic only.

## How "hard requirement" was judged

The task brief treats every `chmod`/`chown`/`write` in vendor `.rc` files as a hard requirement. Android init does not:
a failing `chown`, `chmod`, `write`, `mkdir` or `symlink` logs an error and the action continues. The `.rc` path list
(`raw/rc-paths.txt`) is therefore an inventory of what Samsung drivers exposed, not a list of boot gates. The real
gates are:
- `mount_all` entries without `nofail`
- services marked `critical` (lmkd) or carrying `reboot_on_failure` (bpfloader, boringssl self-tests)
- `init_user0` (vold FBE unlock)
- the HAL processes that SurfaceFlinger and system_server block on: allocator, composer, keystore/keymaster

Where a HAL reads a node, the HAL binary is cited, not only the `.rc`.

## Corrections to ROADMAP.md §4.2 (from image evidence)

1. **Memory: legacy ION is not required.** `libion_exynos.so` (used by gralloc, HWC and camera) picks its backend at
   first use (V:/lib64/libion_exynos.so `getDefaultExporter`, disasm 0x2ff0–0x3100):
   - it first opens `/dev/dma_heap/system`; if that works it uses mode 3, dma-heap
   - otherwise it opens `/dev/ion` and probes `ION_IOC_FREE`: ENOTTY selects modern ION, anything else selects legacy ION

   In dma-heap mode, the ION heap-id bit maps to a heap name (table at .data.rel.ro 0x5048):
   0 `system`, 1 `crypto`, 3 `system-secure-vstream`, 5 `vframe`, 6 `vscaler`, 8 `gpu_crc`, 9 `system-secure-gpu_buffer`,
   10 `camera`, 11 `secure_camera`, 13 `ext_ui`, 14 `system`; 2, 4, 7 and 12 map to `reserved`. The library appends
   `-secure` when `flags & 0x10010`, otherwise `-uncached` when `!(flags & ION_FLAG_CACHED)`. The roadmap's
   "camera_contig=12" heap is a `[reserved]` slot here.
   - The android-config kernel already registers `system` and `system-uncached` (A:drivers/dma-buf/heaps/system_heap.c:534,542) with `DMABUF_HEAPS_SYSTEM=y` (ACFG). Gralloc on that kernel would take the dma-heap path even if the ION port from G were also present.
   - The heaps that boot needs are `system` and `system-uncached`. The camera and secure heaps are feature-level.
2. **Display: the HWC has a DRM backend and selects it automatically.**
   `ExynosDevice::getDeviceInterfaceType()` returns DRM when `access("/dev/dri/card0", F_OK) == 0`
   (V:/lib64/libexynosdisplay.so 0x3f2b0). The DRM backend expects Samsung exynos-drm properties
   (`DQE_FD`, `HDR_FD`, `partial_region`, `eotf_lut`, writeback, … in V:/lib64/libdrmresource.so).
   - Any DRM driver that creates card0 (simpledrm, panfrost, a mainline exynos-drm) diverts the HWC away from the fbdev ABI it can actually drive. Keep `CONFIG_DRM` off, as ACFG does today, unless the full Samsung DRM property set is implemented.
   - With nr > 255, `_IOW('F', nr, …)` ORs the overflow into the type byte. 260 becomes 'G' 4, 700 becomes 'F' 188, and so on. All 4.9 DECON ioctls appear in the blob under those encodings (table: display).
   - Three ioctl groups have no 4.9 handler and must keep returning -ENOTTY.
   - The vsync path is device-name based: `/sys/devices/platform/16030000.decon_f/vsync`.
3. **GPU (confirmed from the blob, not only from 4.9 source).** The blob is `r38p1-01bet0` (MaliG72_r38p1_LT_T) and
   sends UK 11.35 (`mov w10,#0xb; movk w10,#0x23,lsl#16` at 0x1a3d0c0). It uses 28 sized kbase ioctls plus
   Samsung-private nr 42 (`MALI_EXYNOS_IOCTL_EHMP`, K49 include/uapi/gpu/arm/exynos/mali_exynos_ioctl.h:152).
4. **TEE blocks boot, not only biometrics.** fstab uses FBE v2 (`aes-256-xts:aes-256-cts:v2+inlinecrypt_optimized`)
   with no `metadata_encryption` and no `wrappedkey`. vold stores the FBE keys keymaster-wrapped, and keymaster@3.0
   (`keystore.mdfpp.so`) runs on Trustonic. mcDriverDaemon takes its auth token from `/efs`. The boot chain for an
   existing /data is therefore: EFS mount, then /dev/mobicore(-user) with API 6.3, then mcDriverDaemon and the TAs,
   then keymaster, then vold `init_user0`.
5. **USB MTP uses the kernel f_mtp function** (`functions/mtp.0`, `ptp.0`), not FunctionFS. Only adb uses FFS
   (V:/etc/init/init.samsungexynos9810.usb.rc). The UDC name `10c00000.dwc3` is hard-coded.
6. **Open questions from ROADMAP §5 item 3:**
   - The vendor fstab has no `avb`/`verify` flags, so there is no dm-verity on system/vendor/odm. The first-stage fstab in the BOOT ramdisk was not inspected (**GUESS**: same).
   - MTP uses f_mtp, not FFS.
   - `/dev/ashmem` is referenced only by libcutils, which prefers memfd (`has_memfd_support`). The vendor blobs do not need ashmem (**GUESS**: no blob issues ashmem ioctls directly).
7. **Firmware** for Wi-Fi, BT, MFC, FIMC-IS, ABOX/VTS, DSM, SCORE and NFC lives only in `/vendor/firmware`.
   - 4.9 DHD uses `request_firmware` (`DHD_LINUX_STD_FW_API`, K49 bcmdhd_101_16/Makefile:424-427).
   - 4.9 reaches `/vendor/firmware` through `FW_LOADER_USER_HELPER(_FALLBACK)=y` plus ueventd `firmware_directories` (S:/system/etc/ueventd.rc:4).
   - ACFG has no user helper. Set `firmware_class.path=/vendor/firmware` or enable the fallback.
8. **Modem.** The 4.9 DT defines more io devices than the roadmap listed: `umts_ipc1`, `umts_csd`, `umts_cass`,
   `umts_ramdump0`, `umts_ciq0`, `smd4`, `multipdp(_hiprio)` and `rmnet0-7`. cbd uses `_IO('o', 0x19…0x59)`, plus
   0x36, 0x65 and 0xde, which have no 4.9 handler.
9. **Sensors.** The HAL uses `/dev/ssp_sensorhub`, `/dev/batch_io`, IIO under `13ac0000.spi/spi20/spi20.0` and evdev.
   `/dev/shub_*` appears only in sepolicy.
10. **Thermal HAL names.** The HAL needs zones `BIG LITTLE G3D ISP ac battery` and cooling devices
    `thermal-cpufreq-0/1 thermal-gpufreq-0 thermal-isp-0`. Upstream cpufreq cooling names are `cpufreq-cpuN`, so the
    6.12 driver must either use the 4.9 names or the JSON must be edited.
11. **Vibrator.** The HAL accepts `/sys/class/timed_output/vibrator/*` (gone from mainline) or
    `/sys/class/sec_vib_inputff/control/use_sep_index` plus an input force-feedback device. The second is the natural
    6.12 route (**GUESS**: selection logic not disassembled).
12. **Wi-Fi.** It uses nl80211, not wext, plus Broadcom vendor nl80211 commands and Android `DRIVER` private commands
    through `SIOCDEVPRIVATE+1` (0x89f1). On 6.12 these arrive via `ndo_siocdevprivate` (since 5.15), so the DHD port
    must implement that hook. libwifi-hal also writes `/sys/module/dhd/parameters/firmware_path`, so the module name
    must be `dhd`. `wifi_brcm.rc` modprobes `/vendor/lib/modules/dhd.ko`, which does not exist; the driver is built in today.

## Top 20 boot-blocking requirements (dependency order)

| # | Requirement | Stock 6.12 | Where it comes from today (4.9) | Evidence |
|---|---|---|---|---|
| 1 | Bootloader cmdline passed through: `androidboot.hardware=samsungexynos9810` and the other SBoot `androidboot.*` args (do not let the diag DTS `bootargs` or `CMDLINE_FORCE` replace them) | n/a | boot.img cmdline + SBoot | V:/etc/init/init.samsungexynos9810.rc:804; cmdline in V:/etc/recovery.img |
| 2 | UFS host as platform device **`11120000.ufs`** with GPT by-name links (SYSTEM, VENDOR, ODM, USERDATA, EFS, CPEFS, CACHE, RADIO, PERSISTENT, STEADY) | yes (ufs-exynos), subject to DT naming | exynos UFS | V:/etc/fstab.samsungexynos9810 |
| 3 | ext4 read-only first-stage mount of SYSTEM, VENDOR and ODM (no AVB or verity flags in the vendor fstab) | yes | ext4 | fstab lines 2-4 |
| 4 | binderfs with binder, hwbinder and vndbinder; feature files `extended_error`, `freeze_notification`, `oneway_spam_detection` | yes | binder | S:/system/etc/init/hw/init.rc:270-284; V:/lib64/libbinder.so |
| 5 | cgroups: v1 `cpu` /dev/cpuctl, `cpuset` /dev/cpuset, `blkio` /dev/blkio; v2 /sys/fs/cgroup with `cgroup.freeze`; uclamp files | yes (schedtune optional, see table) | cgroup v1 + backports | S:/system/etc/cgroups.json, task_profiles.json |
| 6 | PSI, because lmkd is `critical` and `ro.lmk.use_psi=true` | yes | PSI backport | S:/system/etc/init/lmkd.rc; V:/build.prop |
| 7 | eBPF for netbpfload (`reboot_on_failure`); exact option list not extracted (**GUESS**) | yes | Lineage 4.9 workarounds | S:/system/etc/init/netbpfload.rc |
| 8 | EFS ext4 mounted at /mnt/vendor/efs and bind-mounted to /efs (`errors=panic`, `wait,check`) | yes | ext4 | fstab line 8; init.samsungexynos9810.rc:807 |
| 9 | Trustonic driver: `/dev/mobicore` (admin) and `/dev/mobicore-user`, MCDRVMODULEAPI **6.3**, 'M' ioctls 0–9/20–27, admin 0–4 | no | K49 drivers/gud/gud-exynos9810 | V:/bin/mcDriverDaemon, V:/lib64/libMcClient.so |
| 10 | mcDriverDaemon loads `/vendor/app/mcRegistry/*.drbin/.tlbin` and the auth token from /efs; Kinibi SMC path and secure memory working | no | same | V:/etc/init/mobicore.rc |
| 11 | keymaster@3.0 (`keystore.mdfpp`) and gatekeeper (Trustonic TAs), needed for vold FBE key unwrap (`init_user0`) | no | same | V:/etc/init/android.hardware.keymaster@3.0-service.rc, keymaster@3.0-override.rc |
| 12 | Inline encryption: fscrypt v2, AES-256-XTS, IV_INO_LBLK_64 (`inlinecrypt_optimized`), blk-crypto (FMP or fallback) producing ciphertext identical to 4.9 | yes (fallback); FMP no | FMP | fstab line 7 |
| 13 | f2fs /data with `inlinecrypt,usrquota,grpquota,fsync_mode=nobarrier,reserve_root=32768,resgid=5678,discard` and quota | yes | f2fs | fstab line 7 |
| 14 | Device-node permissions from V:/etc/ueventd.rc (mali0 0666, g2d, mobicore*, …) plus S:/system/etc/ueventd.rc (dma_heap, dri, graphics, input) | n/a | ueventd | both ueventd.rc |
| 15 | `/dev/dma_heap/system` and `system-uncached` (gralloc through libion_exynos); legacy `/dev/ion` only as fallback | `system` yes; `-uncached` ACK-only (present in A) | ION (CONFIG_ION_EXYNOS) | V:/lib64/libion_exynos.so 0x2ff0, 0x24e0 |
| 16 | sync_file fences for HWC and SurfaceFlinger | yes | sync_file | V:/lib64/libexynosdisplay.so NEEDED libsync |
| 17 | Mali kbase r38p1 at `/dev/mali0`, UK 11.35, JM ioctls listed in the gpu table, Samsung ioctl 42; GPU power, clock and DVFS underneath | no | K49 bv_r38p1 + gpex (port in G) | V:/lib64/egl/libGLES_mali.so |
| 18 | DECON fbdev at `/dev/graphics/fb0`: S3CFB_WIN_CONFIG (1416 B), SET_VSYNC_INT, POWER_MODE, EXYNOS_DISP_INFO, display and color mode ioctls; vsync sysfs at `/sys/devices/platform/16030000.decon_f/vsync`; **no `/dev/dri/card0`** | no | K49 dpu_9810 | V:/lib64/libexynosdisplay.so |
| 19 | Panel S6E3HA8 lit through DSIM, plus backlight class device `panel` (`/sys/class/backlight/panel/{brightness,max_brightness}`) | class yes, driver no | K49 panel/ | V:/bin/hw/android.hardware.light-service.samsung |
| 20 | Touchscreen as an evdev input device (sec_ts y771). Not needed for `sys.boot_completed`, but needed for a usable phone | no | K49 sec_ts | V:/etc/ueventd.rc /dev/input/event* |

Under all of these sit the platform items the 6.12 port already tracks: clocks/CMU, power domains, sysmmu, ACPM,
PMIC regulators, cpufreq/devfreq and TMU. Several boot-critical services depend on SELinux labels; enforcing mode
needs the genfscon paths in the sepolicy table to match device names exactly.

## Per-subsystem tables (generated from tools/gen_vendor_abi.py)

### boot-core

| Pri | Item | Exact path / ioctl / version | Evidence (in image) | Stock 6.12 | 4.9 provider | 6.12 port | Notes |
|---|---|---|---|---|---|---|---|
| B | androidboot.hardware | androidboot.hardware=samsungexynos9810 (ro.hardware) | V:/etc/init/init.samsungexynos9810.rc:804 `mount_all /vendor/etc/fstab.${ro.hardware}`; cmdline string in V:/etc/recovery.img (`androidboot.hardware=samsungexynos9810`) | n/a (bootloader/boot-image cmdline) | boot.img header cmdline + SBoot-appended androidboot.* args | diag DTS sets bootargs "panic=30 ro" (memory note); production boot must keep the boot.img cmdline and SBoot androidboot.* args (do not CMDLINE_FORCE) | Without it init cannot find fstab.samsungexynos9810 nor init.samsungexynos9810.rc |
| B | UFS by-name links | /dev/block/platform/11120000.ufs/by-name/{SYSTEM,VENDOR,ODM,USERDATA,CACHE,EFS,CPEFS,RADIO,PERSISTENT,STEADY,MISC,BOOT,RECOVERY} | V:/etc/fstab.samsungexynos9810; V:/etc/init/init.samsungexynos9810.rc:5,8; V:/etc/init/init.baseband.rc (symlink RADIO->/dev/mbin0); V:/etc/ueventd.rc | yes if the UFS platform device is named exactly `11120000.ufs` (DT node ufs@11120000) and GPT names are exposed | K49 drivers/scsi/ufs (exynos ufs) | UFS port gated (memory: delayed-ufs, SCSI LUN guard) | first-stage init creates by-name links from the platform device name; any rename breaks every fstab line |
| B | first_stage_mount fstab | SYSTEM/VENDOR/ODM ext4 ro, flags wait,first_stage_mount (no avb=, no logical/super) | V:/etc/fstab.samsungexynos9810 lines 2-4 | yes (ext4) | ext4 |  | First-stage fstab itself lives in the BOOT ramdisk (not inspected; GUESS it matches) **GUESS** |
| B | /data filesystem | f2fs USERDATA: noatime,nosuid,nodev,discard,usrquota,grpquota,fsync_mode=nobarrier,reserve_root=32768,resgid=5678,inlinecrypt + latemount,wait,check,quota,reservedsize=128M | V:/etc/fstab.samsungexynos9810 line 7 | yes (F2FS_FS, F2FS quota, F2FS_FS_SECURITY) | K49 fs/f2fs | ACFG: F2FS_FS=y | fsync_mode=nobarrier and reserve_root/resgid are upstream f2fs options |
| B | FBE policy | fileencryption=aes-256-xts:aes-256-cts:v2+inlinecrypt_optimized; NO metadata_encryption, NO wrappedkey, NO keydirectory | V:/etc/fstab.samsungexynos9810 line 7 (grep confirms no metadata_encryption/wrappedkey anywhere in fstab) | yes: fscrypt v2 + IV_INO_LBLK_64 + blk-crypto (inline engine or blk-crypto-fallback) | K49 fs/crypto + FMP (drivers/crypto/fmp) inline engine | ACFG: FS_ENCRYPTION=y FS_ENCRYPTION_INLINE_CRYPT=y BLK_INLINE_ENCRYPTION=y; A commit 21d7dcb7f (FMP struct_group fix) | Ciphertext must be bit-identical to 4.9 FMP output (AES-256-XTS, IV_INO_LBLK_64). Keys are stored keymaster-wrapped by vold -> needs Trustonic keymaster (see tee rows) |
| B | EFS mount | EFS ext4 -> /mnt/vendor/efs (errors=panic, wait,check); bind to /efs; CPEFS ext4 ro,noload,nofail -> /mnt/vendor/cpefs | V:/etc/fstab.samsungexynos9810 lines 8-9; V:/etc/init/init.samsungexynos9810.rc:807-808 | yes (ext4) | ext4 |  | errors=panic => any ext4 error on EFS panics the kernel. mcDriverDaemon reads MC_AUTH_TOKEN_PATH=/efs (V:/etc/init/mobicore.rc), so EFS gates the TEE |
| F | CACHE mount | CACHE ext4 noatime,...,errors=panic wait,check | V:/etc/fstab.samsungexynos9810 line 6 | yes | ext4 |  |  |
| F | zram swap | /dev/block/zram0 zramsize=2147483648,max_comp_streams=8 (swapon_all) | V:/etc/fstab.samsungexynos9810 last line; V:/etc/init/init.swap.rc | yes (ZRAM) | zram | ACFG: ZRAM=y |  |
| F | SD card / USB OTG vold paths | /devices/platform/11500000.dwmmc2/mmc_host* (sdcard), /devices/platform/10c00000.usb/10c00000.dwc3* (usb) | V:/etc/fstab.samsungexynos9810 lines 12-13 | yes if DT device names match | dw_mmc exynos; dwc3 exynos |  |  |
| B | binderfs | mount binder /dev/binderfs; /dev/{binder,hwbinder,vndbinder} symlinks; features/{extended_error,freeze_notification,oneway_spam_detection} | S:/system/etc/init/hw/init.rc:270-284; V:/lib64/libbinder.so strings | yes (ANDROID_BINDER_IPC, ANDROID_BINDERFS) | binder | ACFG: ANDROID_BINDERFS=y |  |
| B | PSI (lmkd) | ro.lmk.use_psi=true; lmkd is `critical` | V:/build.prop (# LMK); S:/system/etc/init/lmkd.rc:6 | yes (PSI) | PSI backport | ACFG: PSI=y | lmkd crash loop => init reboots to bootloader (critical service) |
| B | cgroups (system) | v1 blkio:/dev/blkio, cpu:/dev/cpuctl, cpuset:/dev/cpuset; v2 /sys/fs/cgroup with freezer (cgroup.freeze) and optional memory; task_profiles use cpu.uclamp.{min,max,latency_sensitive} | S:/system/etc/cgroups.json; S:/system/etc/task_profiles.json | yes (cpu.uclamp.latency_sensitive is ACK-only) | cgroup v1 + backports | ACFG: CGROUPS CGROUP_SCHED CPUSETS CGROUP_FREEZER MEMCG UCLAMP_TASK(_GROUP)=y | Severity of a missing controller is a GUESS: libprocessgroup logs and continues, but SetTaskProfiles failures can break zygote/app start **GUESS** |
| F | schedtune cgroup | vendor cgroups.json: Controller schedtune at /dev/stune; task_profiles schedtune.boost / schedtune.prefer_idle; rc mkdir /dev/stune/{bg_abnormal,bg_cached} | V:/etc/cgroups.json; V:/etc/task_profiles.json:5-128; V:/etc/init/init.samsungexynos9810.rc:704-712; S:/system/etc/init/hw/init.rc:121-140 | no (schedtune never upstream) | K49 kernel/sched/tune.c (CONFIG_SCHED_TUNE=y) | roadmap: ported in 8819c221c (other branch); not in ACFG | Alternative: drop schedtune from V:/etc/cgroups.json+task_profiles.json (vendor overlay) and rely on uclamp |
| C | freezer v1 (freecess) | mount cgroup none /dev/freezer freezer; frozen/thaw/olaf | V:/etc/init/init.samsungexynos9810.rc:395-429 | yes (CGROUP_FREEZER v1) | cgroup freezer |  | Samsung freecess only; Lineage framework uses the v2 freezer |
| B | BPF (netd/netbpfload) | bpfloader `reboot_on_failure reboot,netbpfload-missing`; real loader in com.android.tethering apex | S:/system/etc/init/netbpfload.rc | yes (BPF_SYSCALL, CGROUP_BPF, BPF_JIT...) | 4.9 + Lineage workarounds | ACFG: BPF_SYSCALL=y | Exact config list not extracted (capex compressed); use the Android 16 kernel requirements list **GUESS** |
| C | memfd vs ashmem | vendor libcutils has has_memfd_support() + ashmem fallback; only libcutils references /dev/ashmem | V:/lib64/libcutils.so strings; RAW/elf-paths.tsv | memfd yes; ashmem removed upstream | ashmem staging | A: drivers/staging/android ashmem (Rust) | With MEMFD_CREATE (ACFG=y) Android 16 uses memfd; ashmem not needed by vendor blobs (GUESS: no blob dlsym's ashmem ioctls directly) **GUESS** |
| F | firmware search path | ueventd firmware_directories /etc/firmware/ /odm/firmware/ /vendor/firmware/ /firmware/image/; all blobs live in /vendor/firmware | S:/system/etc/ueventd.rc:4; V:/firmware/ listing | yes only with FW_LOADER_USER_HELPER(+_FALLBACK) or firmware_class.path=/vendor/firmware | CONFIG_FW_LOADER_USER_HELPER(_FALLBACK)=y (K49/out/.config) | ACFG: FW_LOADER=y, USER_HELPER not set | Affects Wi-Fi, BT .hcd, MFC, FIMC-IS, ABOX/VTS, DSM, SCORE, NFC firmware |
| C | pstore / last_kmsg | /sys/fs/pstore/console-ramoops(-0), /proc/last_kmsg (charger + health service `file` lines) | V:/etc/init/android.hardware.health-service.samsung.rc | pstore yes; /proc/last_kmsg is Samsung | sec_debug last_kmsg |  |  |
| C | vendor recovery reflash | install-recovery.sh flashes V:/etc/recovery.img to RECOVERY if persist.vendor.recovery_update=true | V:/etc/init/vendor_flash_recovery.rc; V:/bin/install-recovery.sh | n/a | n/a |  | Not an ABI item: would overwrite the diag RECOVERY slot if that property is ever set |

### gpu

| Pri | Item | Exact path / ioctl / version | Evidence (in image) | Stock 6.12 | 4.9 provider | 6.12 port | Notes |
|---|---|---|---|---|---|---|---|
| B | Mali DDK version | libGLES_mali.so 1.4 Bifrost "r38p1-01bet0" (r38p1-01bet0-mbs2v41_0), MaliG72_r38p1_LT_T | V:/lib64/egl/libGLES_mali.so strings; V:/build.prop ro.hardware.egl=mali ro.hardware.vulkan=mali | no (panfrost not ABI-compatible) | K49 drivers/gpu/arm/bv_r38p1 (MALI_RELEASE_NAME r38p1-01bet0) | G: drivers/gpu/arm/bv_r38p1 + exynos |  |
| B | kbase UK version | VERSION_CHECK sends major=11 minor=35 (`mov w10,#0xb; movk w10,#0x23,lsl#16` at 0x1a3d0c0) | V:/lib64/egl/libGLES_mali.so disasm 0x1a3d0c0; K49 include/uapi/gpu/arm/bv_r38p1/jm/mali_kbase_jm_ioctl.h:131-132 | no | bv_r38p1 JM | G |  |
| B | kbase device node | /dev/mali0 (opened via "/dev/mali"+index, O_RDWR\|O_CLOEXEC, must be S_ISCHR); misc device name mali0 | V:/lib64/egl/libGLES_mali.so disasm 0x1a3f278-0x1a3f2ac; V:/etc/ueventd.rc (/dev/mali0 0666 system system); V:/etc/selinux/vendor_file_contexts /dev/mali[0-9] | no | bv_r38p1 misc device | G |  |
| B | kbase ioctls used | type 0x80 nr {0 VERSION_CHECK,1 SET_FLAGS,2 JOB_SUBMIT,3 GET_GPUPROPS,5 MEM_ALLOC,6 MEM_QUERY,7 MEM_FREE,12 DISJOINT_QUERY,13 GET_DDK_VERSION,14 MEM_JIT_INIT (24B),15 MEM_SYNC,17 GET_CONTEXT_ID,18 TLSTREAM_ACQUIRE,20 MEM_COMMIT,21 MEM_ALIAS,22 MEM_IMPORT,23 MEM_FLAGS_CHANGE,24 STREAM_CREATE,25 FENCE_VALIDATE,27 MEM_PROFILE_ADD,28 SOFT_EVENT_UPDATE,29/30 STICKY_RESOURCE_(UN)MAP,38 MEM_EXEC_INIT,42 Samsung MALI_EXYNOS_IOCTL_EHMP,50 GET_CPU_GPU_TIMEINFO,51 KINSTR_JM_FD,54 CONTEXT_PRIORITY_CHECK} (+ size-0 _IO 4 POST_TERM / 19 TLSTREAM_FLUSH not scanned) | RAW/libGLES_mali-ioctls.txt; K49 include/uapi/gpu/arm/bv_r38p1/mali_kbase_ioctl.h, jm/mali_kbase_jm_ioctl.h; K49 include/uapi/gpu/arm/exynos/mali_exynos_ioctl.h:152 | no | bv_r38p1 + gpex | G | nr 14 seen with size 24 (= kbase_ioctl_mem_jit_init, 11.20+ layout). nr 42 is Samsung-private; an unknown ioctl returns -EINVAL/-ENOTTY, so its absence is probably non-fatal (GUESS) |
| C | memtrack | /sys/class/misc/mali0/device/* (reads per-process GPU memory) | V:/bin/hw/android.hardware.memtrack-service.samsung-mali strings | no | bv_r38p1 sysfs | G |  |
| F | GPU DVFS sysfs (power HAL) | /sys/kernel/gpu/gpu_max_clock, gpu_min_clock (values 572000/546000/455000 ... 299000), gpu_freq_table, gpu_governor, gpu_info | V:/etc/powerhint.json Nodes GPUMaxFreq/GPUMinFreq; V:/etc/init/init.samsungexynos9810.rc chowns | no | K49 drivers/gpu/arm/exynos (gpex sysfs) | G (exynos dir present) | Missing nodes only make libperfmgr log errors |

### memory

| Pri | Item | Exact path / ioctl / version | Evidence (in image) | Stock 6.12 | 4.9 provider | 6.12 port | Notes |
|---|---|---|---|---|---|---|---|
| B | allocator selection | libion_exynos getDefaultExporter(): open("/dev/dma_heap/system") ok -> mode 3 (dma-heap); else open /dev/ion + ION_IOC_FREE(handle 0): errno ENOTTY -> mode 1 (modern ION), other -> mode 2 (legacy ION) | V:/lib64/libion_exynos.so disasm 0x2ff0-0x3100 (strings 0x13a1 "/dev/dma_heap/", 0x14ad "system", 0x1319 "/dev/ion") | dma-heap: yes (DMABUF_HEAPS_SYSTEM) | legacy ION /dev/ion (K49 drivers/staging/android/ion, CONFIG_ION_EXYNOS=y) | ACFG: DMABUF_HEAPS=y DMABUF_HEAPS_SYSTEM=y DMABUF_HEAPS_CMA=y -> the android-config kernel already makes libion_exynos take the dma-heap path; G also carries legacy ION | CORRECTS ROADMAP 4.2: legacy ION is NOT required by gralloc/libion_exynos. dma-heap is preferred whenever /dev/dma_heap/system exists |
| B | dma-heap names | /dev/dma_heap/<name>[-secure\|-uncached]: heap-id bit -> name: 0 system, 1 crypto, 2 reserved(vfw), 3 system-secure-vstream, 4 reserved, 5 vframe, 6 vscaler, 7 reserved(vnfw), 8 gpu_crc, 9 system-secure-gpu_buffer, 10 camera, 11 secure_camera, 12 reserved, 13 ext_ui, 14 system(vendor_system_heap). Suffix: flags&0x10010 -> "-secure"; else !(flags&ION_FLAG_CACHED=1) -> "-uncached" | V:/lib64/libion_exynos.so alloc_dma_heap disasm 0x24e0-0x2690, table .data.rel.ro 0x5048 (24-byte entries {ion_name, dmaheap_name, len}) | upstream has only `system` (+cma `reserved`/`default_cma_region`); `system-uncached` is ACK-only | n/a (4.9 uses ION ids) | A: drivers/dma-buf/heaps/system_heap.c:534 "system", :542 "system-uncached" | Boot needs system + system-uncached (gralloc non-cached buffers). camera/secure_camera/vframe/vscaler/crypto/system-secure-* are feature-level (camera, DRM video). S:/system/etc/ueventd.rc:54-56 sets perms for system, system-uncached, system-secure |
| B | dma-heap alloc ioctl | DMA_HEAP_IOCTL_ALLOC 0xc0184800 (_IOWR('H',0,24)) | ioctl scan of V:/lib64/libion_exynos.so (alloc_dma_heap 0x26a0-0x26b0) | yes | n/a |  |  |
| C | legacy ION ioctls (fallback) | ION_IOC_ALLOC 0xc0204900 (legacy 32B) / 0xc0184900 (modern 24B), FREE 0xc0044901, SHARE 0xc0084904, IMPORT 0xc0084905, SYNC 0xc0084907, HEAP_QUERY 0xc0184908, 0xc0184909 (_IOWR('I',9,24)) | ioctl scan V:/lib64/libion_exynos.so, V:/lib64/libion.so | no (ION removed in 5.11) | K49 ION + exynos ion | G: drivers/staging/android/ion | Only used if /dev/dma_heap/system is absent |
| C | dmabuf sync / container | DMA_BUF_IOCTL_SYNC 0x40086200; Samsung /dev/dmabuf_container: 'b' 13 merge (0xc018620d), 14 set/get mask (0x4004620e/0x8004620e) | ioctl scan V:/lib64/libion_exynos.so; strings "/dev/dmabuf_container", "dma_buf_merge" | SYNC yes; container no | K49 dma-buf container (CONFIG_DMABUF_CONTAINER, GUESS) |  | dma_buf_merge failure only disables buffer merging (GUESS) **GUESS** |
| C | ION sysfs | /sys/class/ion_cma/ion_video_ext/isolated | V:/etc/init/init.samsungexynos9810.rc (on fs) | no | exynos ION cma |  |  |
| B | gralloc | gralloc.universal9810.so -> libion_exynos (exynos_ion_alloc/import_handle/free_handle; import/free are no-ops unless legacy mode) | V:/lib64/hw/gralloc.universal9810.so NEEDED/strings; V:/lib64/libion_exynos.so import_handle disasm 0x2a38-0x2a48 (mode==2 check) | n/a | n/a |  | allocator@2.0-service + mapper@2.0-impl-2.1 (HIDL) load gralloc.universal9810 via libhardware |

### display

| Pri | Item | Exact path / ioctl / version | Evidence (in image) | Stock 6.12 | 4.9 provider | 6.12 port | Notes |
|---|---|---|---|---|---|---|---|
| B | HWC interface selection | ExynosDevice::getDeviceInterfaceType(): access("/dev/dri/card0",F_OK)==0 -> DRM interface, else fbdev (/dev/graphics/fb0) | V:/lib64/libexynosdisplay.so disasm 0x3f2b0-0x3f2d0, string 0x20737 "/dev/dri/card0" | n/a | fbdev path (no DRM on 4.9) | ACFG: DRM not set, FB=y FB_SIMPLE=y (good) | Any DRM driver that creates /dev/dri/card0 (simpledrm, panfrost, exynos-drm) flips HWC into its DRM backend, which expects Samsung exynos-drm properties (DQE_FD, HDR_FD, partial_region, eotf_lut, ... in V:/lib64/libdrmresource.so) |
| B | fb device | /dev/graphics/fb0 (primary, decon_f), /dev/graphics/fb1 (decon_t / DP) | V:/lib64/libexynosdisplay.so strings; S:/system/etc/ueventd.rc:7,69 | fbdev core yes; DECON driver no | K49 drivers/video/fbdev/exynos/dpu_9810 (CONFIG_EXYNOS_DECON_9810/_FB) | none |  |
| B | DECON ioctls (primary) | S3CFB_WIN_CONFIG _IOW('F',209,1416B)=0x458846d1; S3CFB_SET_VSYNC_INT 0x400446ce; S3CFB_POWER_MODE 0x400446df; S3CFB_DECON_SELF_REFRESH 0x400446cf; FBIO_WAITFORVSYNC 0x40044620 (gralloc fb) | RAW/display-ioctls.txt (V:/lib64/libexynosdisplay.so, V:/lib64/hw/gralloc.universal9810.so); K49 dpu_9810/decon.h:1726-1750 | no | dpu_9810 decon_core.c:2914-3299 | none | nr>255 is OR'ed into the type byte by _IOC(): 260->'G'4, 300->'G'44, 400->'G'144, 600-602->'F'88-90, 700-705->'F'188-193, 800->'G'32, 850->'G'82 |
| B | DECON ioctls (info/modes) | EXYNOS_DISP_INFO 0x40f84704 (248B); EXYNOS_GET/SET_DISPLAY_MODE(_NUM/_CURRENT) 0x400446bc/0x401c46bd/0x401c46be/0x400446c1; EXYNOS_GET/SET_COLOR_MODE(_NUM) 0x40044658/0x40084659/0x4004465a; S3CFB_GET_HDR_CAPABILITIES 0x40104790; EXYNOS_GET_EDID 0x42044720; EXYNOS_GET_DISPLAYPORT_CONFIG 0x4124472c; EXYNOS_GET_VSYNC_CHANGE_TIMELINE 0x40204752; DECON_WIN_CURSOR_POS 0x400846de | RAW/display-ioctls.txt; K49 dpu_9810/decon.h:1734-1785 | no | dpu_9810 | none | EXYNOS_DISP_INFO feeds xres/yres/xdpi; failure leaves the primary display unconfigured (GUESS) **GUESS** |
| C | HWC ioctls with no 4.9 handler | 0x4d084705 ('F'261, 3336B), 0x400446d3 ('F'211, logged as S3CFB_LOW_PERSISTENCE), 0x40084662..0x40014667 ('F'610-615) | RAW/display-ioctls.txt; not present in K49 decon.h/decon_core.c | no | none (4.9 returns -ENOTTY today) |  | Must stay tolerated: return -ENOTTY like 4.9 |
| B | vsync / PSR sysfs | /sys/devices/platform/16030000.decon_f/vsync, .../psr_info, /sys/devices/platform/16050000.decon_t/vsync | V:/lib64/libexynosdisplay.so strings; genfscon in V:/etc/selinux/vendor_sepolicy.cil (RAW/sepolicy-genfscon.txt) | no | dpu_9810 decon sysfs |  | Platform device names must be exactly 16030000.decon_f / 16050000.decon_t |
| F | DP hotplug extcon | /sys/devices/platform/%s/extcon/extcon0/cable.%d/state | V:/lib64/libexynosdisplay.so strings; V:/etc/selinux/vendor_file_contexts (sysfs_extcon) | extcon yes | displayport + ccic |  |  |
| B | fence sync | release/acquire fences via sync_file (libsync) | V:/lib64/libexynosdisplay.so NEEDED libsync.so | yes (SYNC_FILE, SW_SYNC) | DPU_2_0_RELEASE_FENCE + sync_file | ACFG: SYNC_FILE=y SW_SYNC=y |  |
| F | G2D (libacryl) | /dev/g2d (+ /dev/fimg2d probe); 'M' ioctls 3,4 (272/304B), 5, 6, 7; G2D_IOC_PROCESS=_IOWR('M',4), PRIORITY 5, PERFORMANCE 6 | ioctl scan V:/lib64/libacryl.so; V:/etc/ueventd.rc (/dev/g2d 0660 system graphics); K49 drivers/gpu/exynos/g2d/g2d_uapi.h:272-274 | no | K49 drivers/gpu/exynos/g2d | none | HWC uses G2D for composition/clear; HWC falls back to GLES if acryl fails (GUESS) **GUESS** |
| F | MSCL scaler (libacryl/libhwjpeg) | /dev/video50 (m2m1shot_scaler0 alias), V4L2 m2m ioctls 'V' 0,5,8,15,17,18,19,28,60,72,193 | ioctl scan V:/lib64/libacryl.so; V:/etc/ueventd.rc | V4L2 core yes; driver no | K49 VIDEO_EXYNOS_SCALER |  |  |
| F | backlight | /sys/class/backlight/panel/{brightness,max_brightness} (max 44700 per roadmap); device path panel_drv@001/backlight/panel | V:/bin/hw/android.hardware.light-service.samsung strings; V:/lib64/libexynosdisplay.so; genfscon /devices/platform/panel_drv@001/backlight/panel/* | class yes; driver no | K49 drivers/video/fbdev/exynos/panel |  |  |
| C | panel / mdnie sysfs | /sys/class/lcd/panel/* (31 attrs incl. power_reduce, alpm, hmt_*, fix_green_screen), /sys/class/mdnie/mdnie/{mode,mode_max,scenario,accessibility,sensorRGB,lux,night_mode,...} | V:/etc/init/init.samsungexynos9810.rc; V:/etc/init/init.greenfix.rc; V:/bin/hw/vendor.lineage.livedisplay-service.samsung-exynos strings | no | K49 panel + mdnie |  |  |
| C | display idle hint | /sys/class/graphics/fb0/device/idle_state (or /sys/class/drm/card0/device/idle_state) | V:/lib64/libdisppower-pixel.so strings | no | none on 4.9 either (GUESS) |  |  **GUESS** |
| F | charger-mode UI | minui on /dev/graphics/fb0 (or /dev/dri) | V:/bin/hw/android.hardware.health-service.samsung strings (/dev/graphics/fb0, /dev/dri); V:/etc/init/android.hardware.health-service.samsung.rc (`on charger chown /dev/graphics/fb0`) | fbdev/simplefb ok | dpu_9810 |  |  |

### tee

| Pri | Item | Exact path / ioctl / version | Evidence (in image) | Stock 6.12 | 4.9 provider | 6.12 port | Notes |
|---|---|---|---|---|---|---|---|
| B | Trustonic nodes | /dev/mobicore (0700 system, admin), /dev/mobicore-user (0666 radio:system), /dev/t-base-tui | V:/etc/ueventd.rc; V:/bin/mcDriverDaemon strings; V:/lib64/libMcClient.so strings | no | K49 drivers/gud/gud-exynos9810/MobiCoreDriver (CONFIG_TRUSTONIC_TEE=y) | A: drivers/gud/gud-exynos9810, ACFG TRUSTONIC_TEE=y |  |
| B | MobiCore user API | MCDRVMODULEAPI 6.3; user (/dev/mobicore-user) 'M' 0 OPEN_SESSION 64B, 1 OPEN_TRUSTLET 40B, 2 CLOSE_SESSION, 3 NOTIFY, 4 WAIT 12B, 5 MAP 40B, 6 UNMAP 40B, 7 ERR 8B, 9 VERSION 96B, GP 20 INIT_CONTEXT, 21 REGISTER_SHM 40B, 23 RELEASE_SHM, 24 GP_OPEN_SESSION 224B, 25 GP_CLOSE, 26 INVOKE 192B, 27 CANCEL 168B; admin (/dev/mobicore, daemon) 'M' 0 GET_DRIVER_REQUEST 32B, 1 GET_INFO 8B, 2 LOAD_DRIVER, 3 LOAD_TOKEN, 4 LOAD_CHECK (24B); all match 4.9 numbering and sizes as scanned | ioctl scan V:/bin/mcDriverDaemon, V:/lib64/libMcClient.so; K49 drivers/gud/gud-exynos9810/MobiCoreDriver/public/mc_user.h:18-19,282-314, mc_admin.h:68-76 | no | gud-exynos9810 | A | libMcClient checks the driver version ("driver version mismatch detected: expected version major %d") |
| B | mcDriverDaemon + TAs | service mobicore loads FFFFFFFF000000000000000000000001.drbin, ffffffffd0000000000000000000000a.tlbin, ...16.tlbin, ...45.drbin from /vendor/app/mcRegistry; auth token /efs (MC_AUTH_TOKEN_PATH), /efs/TEE, /data/vendor/mcRegistry | V:/etc/init/mobicore.rc; V:/app/mcRegistry (30 TAs/drivers) | n/a | n/a |  | /sys/kernel/debug/trustonic_tee/crashdump optional |
| B | keymaster / gatekeeper | android.hardware.keymaster@3.0-service (class early_hal) + gatekeeper@1.0-service -> keystore.mdfpp.so / gatekeeper.exynos9810.so -> libMcClient -> Kinibi TAs | V:/etc/init/android.hardware.keymaster@3.0-service.rc, keymaster@3.0-override.rc, android.hardware.gatekeeper@1.0-service.rc; V:/build.prop ro.hardware.keystore=mdfpp | n/a | Trustonic |  | vold stores FBE keys keymaster-wrapped -> no TEE = no /data unlock (init_user0 failure). Risk: keymaster key upgrade on os_version change (ROADMAP risk 3) |
| F | secure memory / DRM | /dev/s5p-smem, /dev/ion (liboemcrypto), system-secure-* dma-heaps, /dev/tzic, /dev/hdcp2 | V:/lib/liboemcrypto.so strings; V:/etc/ueventd.rc | no | K49 secmem/content path protection |  |  |

### usb

| Pri | Item | Exact path / ioctl / version | Evidence (in image) | Stock 6.12 | 4.9 provider | 6.12 port | Notes |
|---|---|---|---|---|---|---|---|
| F | configfs gadget | mount configfs /sys/kernel/config; g1 with functions mtp.0 ptp.0 acm.0 dm.0 rndis.0 conn_gadget.0 midi.0 accessory.0 audio_source.0 ncm.0 ffs.adb; VID 0x04e8 PID 0x685d/0x6860/0x6862-0x6865 | V:/etc/init/init.samsungexynos9810.usb.rc | configfs+FFS+acm+rndis+ncm+midi yes; mtp/ptp/dm/conn_gadget/accessory/audio_source no (Android/Samsung kernel functions) | K49 USB_CONFIGFS_F_MTP/PTP/DM, USB_ANDROID_SAMSUNG_COMPOSITE | ACFG: USB_CONFIGFS_F_FS=y (adb proven per ROADMAP) | adb only needs ffs.adb. MTP is f_mtp (/dev/mtp_usb) because no ffs.mtp instance exists in the rc |
| F | UDC name | UDC "10c00000.dwc3" (hard-coded) and ${sys.usb.controller} | V:/etc/init/init.samsungexynos9810.usb.rc | yes if dwc3 device is named 10c00000.dwc3 | dwc3 exynos |  |  |
| C | android_usb class | /sys/class/android_usb/android0/{enable,functions} (144 writes) | V:/etc/init/init.samsungexynos9810.usb.rc | no | Samsung composite |  | Writes fail harmlessly |
| F | USB HAL | /sys/class/typec/port0/{power_role,data_role,port_type}, -partner/{accessory_mode,supports_usb_power_delivery}; /sys/devices/virtual/sec/ccic/water; /sys/devices/virtual/usb_notify/usb_control/usb_data_enabled | V:/etc/init/android.hardware.usb-service.samsung.rc; V:/bin/hw/android.hardware.usb-service.samsung strings | typec class yes; MAX77705 CCIC driver no | K49 CCIC_MAX77705 + TYPEC=y + USB_NOTIFY_LAYER |  |  |

### audio

| Pri | Item | Exact path / ioctl / version | Evidence (in image) | Stock 6.12 | 4.9 provider | 6.12 port | Notes |
|---|---|---|---|---|---|---|---|
| F | ALSA card | card 0 from `samsung,star-madera` (ABOX + CS47L93 + MAX98512); tinyalsa /dev/snd/pcmC%uD%u%c, controlC%u, comprC%uD%u (offload), hwC0D%d | V:/lib64/libaudioproxy.so, V:/lib64/libtinyalsa.so strings; K49 DT49 sound node (line 12694) | ASoC core yes; ABOX no; madera core + cs47l92 codec (covers CS47L93) partly; max98512 no | K49 sound/soc/samsung (ABOX), sound/soc/codecs (madera, max98512) | none | Audio HAL failure does not block boot (GUESS) **GUESS** |
| F | mixer controls | 141 unique control names in V:/etc/mixer_paths.xml (ABOX *, madera EQ/HPOUT/IN/DMIC, `SPK Analog Left/Right Gain`, `Rcv Digital Gain`) | RAW/mixer-ctls.txt | names must match the 4.9 drivers exactly | ABOX + madera + max98512 |  |  |
| C | mixer path from DT | /proc/device-tree/sound/mixer-paths (optional override of mixer_paths.xml) | V:/lib64/hw/audio.primary.universal9810.so strings | DT | DT49 (no such property today) |  |  |
| F | ABOX/VTS nodes | /dev/vts_fio_dev (0660 audioserver), /dev/seiren, /sys/devices/platform/17c50000.abox/0.abox_debug/, /sys/kernel/debug/abox/, /data/vendor/log/abox | V:/etc/ueventd.rc; V:/lib64/libaudioproxy.so strings | no | K49 sound/soc/samsung abox/vts |  |  |
| F | speaker amp DSM | /sys/class/dsm/{max98512,maxdsm_cal,maxdsm_power}/*; /efs/maxim; firmware dsm.bin dsm_tune.bin | V:/etc/init/init.samsungexynos9810.rc; V:/firmware | no | K49 maxdsm |  | ROADMAP risk 9: no playback without DSM limits |
| F | audio firmware | AP_AUDIO_SLSI.bin, APBargeIn/APBiBF/APDV_AUDIO_SLSI.bin, vts.bin, cs47l92-dsp1-trace.wmfw, calliope_{dram,sram,iva}.bin, SoundBoosterParam.bin | V:/firmware listing | fw loader | ABOX/VTS/madera |  |  |

### ril

| Pri | Item | Exact path / ioctl / version | Evidence (in image) | Stock 6.12 | 4.9 provider | 6.12 port | Notes |
|---|---|---|---|---|---|---|---|
| F | modem io devices | /dev/umts_boot0, umts_ipc0/1, umts_rfs0, umts_router, umts_dm0, umts_csd, umts_cass, umts_ramdump0, umts_ciq0, smd4, multipdp(_hiprio), rmnet0-7 (netdev) | DT49 iod,name list; V:/lib64/libsec-ril-impl.so strings; V:/etc/ueventd.rc; V:/etc/selinux/vendor_file_contexts | no | K49 drivers/misc/modem_v1 (UMTS_MODEM_SS310AP, LINK_DEVICE_SHMEM) | none |  |
| F | cbd boot daemon | cbd -d -tss310 -bm -mm -P platform/11120000.ufs/by-name/RADIO; reads /dev/mbin0 (RADIO symlink), ioctls _IO('o',0x19..0x59) incl. MODEM_ON/OFF/RESET/BOOT_ON/BOOT_OFF/BOOT_DONE/STATUS/DL_START/XMIT_BOOT(0x40)/SECURITY_REQ(0x53); also 0x36,0x65,0xde (no 4.9 handler) | V:/etc/init/init.baseband.rc; disasm of V:/bin/cbd (mov w1,#0x6fNN counts); K49 drivers/misc/modem_v1/modem_prj.h:74-128 | no | modem_v1 |  |  |
| F | RIL ioctls | libsec-ril-impl: _IO('o',0x27 STATUS,0x30/0x31 NET_SUSPEND/RESUME,0x34 FORCE_CRASH_EXIT,0x61) | disasm V:/lib64/libsec-ril-impl.so | no | modem_v1 |  |  |
| F | modem sysfs/DT | /sys/devices/platform/10000.mif_pdata/sim/ds_detect; /sys/module/modem_ctrl_ss310ap/parameters/ds_detect; /proc/device-tree/model_info-system_rev (cbd); /sys/devices/virtual/misc/umts_dm0/dm_state; /sys/kernel/debug/svnet/mem_dump | V:/etc/ueventd.rc; V:/etc/init/init.baseband.rc; V:/bin/cbd strings | no | modem_v1 + DT root property model_info-system_rev |  |  |
| C | RIL misc | /dev/block/param, /dev/drb, /dev/ramdump_memshare, /dev/nr_boot0 (absent on 9810), /dev/tun, /proc/net/xt_qtaguid/iface_stat_fmt (absent on 6.12, tolerated GUESS), /sys/module/mcps/*, /dev/network_throughput | V:/lib64/libsec-ril-impl.so strings; V:/etc/init/init.samsungexynos9810.rc | tun yes; rest no | Samsung misc |  |  **GUESS** |
| F | EFS/NV safety | RFS path via /dev/umts_rfs0 writes NV under /efs | ROADMAP risk 4 | n/a | modem_v1 rfs |  | Bring up CP with EFS backup in hand (backups-20261103) |

### wifi-bt

| Pri | Item | Exact path / ioctl / version | Evidence (in image) | Stock 6.12 | 4.9 provider | 6.12 port | Notes |
|---|---|---|---|---|---|---|---|
| F | Wi-Fi driver API | nl80211 (cfg80211) + Broadcom vendor nl80211 commands (libwifi-hal BCMDHD vendor HAL) + Android private DRIVER commands via SIOCDEVPRIVATE+1 (0x89f1) from wpa_supplicant/hostapd (and 0x89f0 in hostapd) | disasm V:/bin/hw/wpa_supplicant, V:/bin/hw/hostapd (mov w1,#0x89f1); V:/lib64/libwifi-hal.so strings ("BCMDHD vendor HAL") | cfg80211 yes; bcmdhd no; brcmfmac lacks BCM4361 + Android private cmds | K49 drivers/net/wireless/bcmdhd_101_16 (BCM4361, CUSTOMER_HW4_PRIVATE_CMD) | none; ACFG lacks CFG80211 | 6.12 routes SIOCDEVPRIVATE through ndo_siocdevprivate (since 5.15): DHD must implement it, not only ndo_do_ioctl. wext not used |
| F | Wi-Fi interface / module | wlan0 (wifi.interface), p2p/ap ifaces; /sys/module/dhd/parameters/firmware_path (module name must be `dhd`); modprobe -d /vendor/lib/modules dhd.ko (directory absent -> built-in today) | V:/lib64/libwifi-hal.so strings; V:/etc/init/wifi_brcm.rc; V:/etc/init/wifi.rc | n/a | bcmdhd built-in |  |  |
| F | Wi-Fi firmware | request_firmware names bcmdhd_sta.bin_b0/_b2, bcmdhd_mfg.bin_b*, bcmdhd_mon.bin_b2, nvram.txt_{murata_r041,r02a,r02b}_b2, bcmdhd_clm.blob in /vendor/firmware; /data/vendor/conn/.*.info, /data/vendor/firmware/wifi/ | V:/firmware listing; K49 bcmdhd_101_16/Makefile:424-427 (DHD_LINUX_STD_FW_API, DHD_FW_NAME), dhd_custom_cis.c:250-255 | fw loader | bcmdhd |  | Needs /vendor/firmware in the firmware search path (see boot-core firmware row) |
| C | Wi-Fi sysfs | /sys/wifi/{mac_addr,cid,ant,pm,softap,wifiver,memdump,frameburst,control_he_enab,bad_ap_list,otp}, /sys/devices/platform/116a0000.pcie0/pcie_sysfs | V:/etc/init/init.samsungexynos9810.rc; genfscon /wifi | no | bcmdhd + exynos PCIe |  |  |
| F | Bluetooth | HIDL bluetooth@1.0 + libbt-vendor: /dev/ttySAC1 (UART HCI), /sys/class/rfkill/rfkill%d/{state,type}, firmware bcm4361B2_{murata,semco}.hcd, BD addr /efs/bluetooth/bt_addr | V:/lib64/libbt-vendor.so strings; V:/etc/init/init.samsungexynos9810.rc:649-661; V:/firmware | serial + rfkill yes; BT power/rfkill driver no | K49 drivers/bluetooth/bcm43xx.c + samsung serial |  | Userspace HCI over raw tty (not hci_uart). /dev/ttySAC1 must be the BT UART |
| F | GNSS | gpsd/lhd: /dev/ttyBCM*, /dev/bbd_{control,packet,reliable,sensor,sio,patch,urgent_patch}, /sys/bbd/lk_{enable,timeout}, /sys/class/sec/gps/GPS_PWR_EN/value | V:/etc/ueventd.rc; V:/bin/hw/lhd strings; V:/etc/init/init.gps.rc | no | K49 drivers/sensorhub/brcm/bbdpl + gps |  |  |
| F | NFC | /dev/sec-nfc ioctls 'S' 1 SET_MODE,2 SLEEP,3 WAKEUP; /dev/p3 (eSE); firmware V:/firmware/nfc | ioctl scan V:/lib64/nfc_nci_sec.so; K49 drivers/nfc/sec_nfc.h:23-30 | no (s3fwrn5 ABI differs) | K49 drivers/nfc (SEC_NFC, ESE_P3_LSI) |  |  |

### sensors

| Pri | Item | Exact path / ioctl / version | Evidence (in image) | Stock 6.12 | 4.9 provider | 6.12 port | Notes |
|---|---|---|---|---|---|---|---|
| F | sensor hub | /dev/ssp_sensorhub, /dev/batch_io, /dev/ssp_data_injection, iio:device* (buffer/enable, buffer/length, poll_delay under /sys/devices/platform/13ac0000.spi/spi_master/spi20/spi20.0/), input event devices, /sys/class/sensors/ssp_sensor/{enable,ssp_control,ssp_flush}, /sys/class/sensors/<sensor>/name | V:/lib64/sensors.sensorhub.so strings; V:/etc/ueventd.rc; V:/etc/selinux/vendor_file_contexts (13ac0000.spi sysfs_iio) | IIO core yes; SSP driver no | K49 drivers/sensorhub/brcm (SENSORS_SSP_BBD) | none |  |
| C | HRM / grip | /dev/max_hrm, /sys/class/sensors/hrm_sensor/*, /sys/class/sensors/grip_sensor*/*, /sys/class/sensor_event/symlink/ | V:/lib64/sensors.bio.so, sensors.grip.so strings | no | K49 drivers/optics (max86915), sx9320 |  |  |
| C | hall / keys | /sys/devices/virtual/sec/sec_key/{hall_detect,certify_hall_detect,wakeup_keys} | V:/lib64/sensors.sensorhub.so; V:/etc/init/hw/init.samsung.rc | no (sec class) | K49 hall.c, certify_hall.c, sec_key |  |  |
| F | fingerprint | /dev/esfp0 (Egis ET5xx), ioctls 'k' 0 (IOC_MESSAGE 32B),3,8,18,19,20,22,23,26; /sys/class/fingerprint/fingerprint/{type_check,name,vendor,adm,bfs_values}; also probes /dev/vfsspi /dev/goodix_fp /dev/qbt2000_* | ioctl scan V:/lib64/libbauthserver.so; strings; K49 drivers/fingerprint/et5xx.h:193 | no | K49 drivers/fingerprint (SENSORS_ET5XX) |  | Needs Trustonic fingerprint TA too |
| C | touch HAL | /sys/class/sec/tsp/{cmd,cmd_list,cmd_result,input/enabled}, /sys/class/sec/sec_epen/epen_gestures, /sys/class/sec/sec_touchkey/input/enabled | V:/bin/hw/vendor.lineage.touch-service.samsung strings | no | K49 sec_ts y771 + sec_cmd, wacom |  | Touch input itself is plain evdev; the HAL only adds gestures/glove |
| B | input generic | /dev/input/event*, /dev/uinput (fingerprint HAL), /dev/uhid (sensors/fp groups) | V:/etc/ueventd.rc; V:/bin/hw/android.hardware.biometrics.fingerprint-service.samsung strings | yes | input core | ACFG: INPUT_UINPUT=y UHID=y | Touchscreen driver itself is feature-level but usability-critical |

### power

| Pri | Item | Exact path / ioctl / version | Evidence (in image) | Stock 6.12 | 4.9 provider | 6.12 port | Notes |
|---|---|---|---|---|---|---|---|
| F | power HAL (libperfmgr) | nodes: /sys/kernel/gpu/gpu_{max,min}_clock, /dev/cpu_dma_latency (44/100), /sys/devices/system/cpu/cpu0/cpufreq/scaling_min_freq (0/1456000), /sys/class/sec/tsp/cmd (aod_enable); hints AUDIO_LAUNCH CAMERA_* EXPENSIVE_RENDERING INTERACTION LAUNCH SUSTAINED_PERFORMANCE DOUBLE_TAP_TO_WAKE; /proc/vendor_sched/* (Pixel, absent today) | V:/etc/powerhint.json; V:/bin/hw/android.hardware.power-service.pixel-libperfmgr strings | cpufreq + cpu_dma_latency yes | Exynos ACME cpufreq, gpex | integration branch cpufreq (HW-verified q15) | Missing nodes => libperfmgr errors only |
| C | Samsung CPU knobs | /sys/power/cpufreq_{min,max}_limit, cpufreq_table, /sys/power/cpuhotplug/*, /sys/kernel/ehmp/*, /sys/kernel/ert/*, /dev/cpu_online_{min,max}, /dev/cluster{0,1,2}_freq_{min,max}, /dev/{bus,device,mfc,npu}_throughput(_max), /dev/gpu_freq_{min,max}, cpufreq/interactive/* (ueventd) | V:/etc/init/init.samsungexynos9810.rc; V:/etc/ueventd.rc; V:/etc/selinux/vendor_file_contexts (pm_qos_device); V:/lib64/libbauthserver.so (cpufreq_max_limit) | no | K49 exynos cpufreq/cpuhotplug/EMS/pm_qos misc |  |  |
| F | thermal HAL | thermal zones by type: BIG, LITTLE, G3D, ISP, ac, battery; cooling devices: thermal-cpufreq-0, thermal-cpufreq-1, thermal-gpufreq-0, thermal-isp-0; /dev/thermal/{tz,cdev}-by-name symlinks; /sys/class/powercap, /sys/bus/iio/devices | V:/etc/thermal_info_config.json; V:/bin/thermal_symlinks; V:/etc/init/pixel-thermal-symlinks.rc, android.hardware.thermal-service.pixel.rc | thermal core yes; names differ (upstream cpufreq cooling = cpufreq-cpu0/cpufreq-cpu4) | K49 exynos_tmu + exynos cpufreq/gpu cooling | acpm-tmu branch: 4 TMU zones verified (zone type names not checked here) | HAL only starts after vendor.thermal.link_ready=1; zone/cdev type strings must match or edit the JSON |
| F | health HAL | /sys/class/power_supply/* scanned by healthd; names on 4.9: battery, usb, ac, wireless, ps, otg, max77705-charger, max77705-fuelgauge, mfc-charger; extras /sys/class/power_supply/battery/batt_slate_mode, /sys/class/sec/switch/afc_disable; BPF uevent filter /sys/fs/bpf/vendor/prog_filterPowerSupplyEvents_skfilter_power_supply; /sys/power/{state,wakeup_count} | V:/bin/hw/android.hardware.health-service.samsung, vendor.lineage.health-service.default strings; K49 drivers/battery_v2 (.name strings); genfscon max77705-* paths | power_supply core yes; MAX77705/sec_battery no | K49 drivers/battery_v2 |  | No battery => framework sees no battery (boots, wrong state). Charge control writes are a hazard (ROADMAP risk 6) |
| C | battery rc nodes | /sys/class/power_supply/battery/* (238 chown/chmod lines), /dev/batt_misc, /dev/ccic_misc | V:/etc/init/init.samsungexynos9810.rc | no | battery_v2, ccic |  |  |
| F | lights HAL | /sys/class/backlight/panel/{brightness,max_brightness}, /sys/class/sec/led/led_blink, /sys/class/sec/sec_touchkey/brightness, /sys/class/misc/backlightnotification/notification_led; rc also /sys/class/leds/led_{r,g,b}/* | V:/bin/hw/android.hardware.light-service.samsung strings; V:/etc/init/init.samsungexynos9810.rc | backlight/leds class yes | K49 panel, LEDS_MAX77705_RGB |  |  |
| F | vibrator HAL | /sys/class/timed_output/vibrator/{enable,intensity,cp_trigger_index}; alternative path /sys/class/sec_vib_inputff/control/use_sep_index + /dev/input FF effects | V:/bin/hw/android.hardware.vibrator-service.samsung strings; V:/etc/init/android.hardware.vibrator-service.samsung.rc | timed_output removed from mainline; input FF yes | K49 drivers/motor (MOTOR_DRV_MAX77705) + timed_output |  | Cheapest 6.12 route: input FF device + sec_vib_inputff control node (GUESS: HAL selects FF when that node exists) **GUESS** |
| F | suspend | /sys/power/state, /sys/power/wakeup_count, pm_freeze_timeout=1000 | V:/bin/hw/android.hardware.health-service.samsung strings; V:/etc/init/init.samsungexynos9810.rc:505 | yes | PM core | ACFG: PM_WAKELOCKS=y |  |
| F | RTC | /dev/rtc0 (s2mps18-rtc, hctosys) | S:/system/etc/ueventd.rc:67; genfscon /devices/platform/141c0000.speedy/i2c-15/15-0000/s2mps18-rtc/rtc/rtc0/hctosys | RTC class yes; s2mps18 RTC no (s2mps rtc covers 13/14/15 only, GUESS) | K49 s2mps18 rtc |  |  **GUESS** |

### camera-media

| Pri | Item | Exact path / ioctl / version | Evidence (in image) | Stock 6.12 | 4.9 provider | 6.12 port | Notes |
|---|---|---|---|---|---|---|---|
| F | camera V4L2 nodes | /dev/video{0-3,101-225}, /dev/v4l-subdev{0-21,32}, /dev/media{0,1,2}; opened by number through libexynosv4l2 (name lookup /sys/class/video4linux/video%d/name) | V:/etc/ueventd.rc; V:/lib64/libexynosv4l2.so, libexynoscamera3.so strings | V4L2 core yes; FIMC-IS2 no | K49 drivers/media/platform/exynos/fimc-is2 | none | Fixed video_device numbers matter (ueventd perms per number) |
| F | camera sysfs | /sys/class/camera/{rear,front,ois,flash,secure}/* (camfw, sensorid, mtf_exif, awb, fw_update, rear_flash...), /sys/devices/svc/Camera/*, /sys/kernel/mm/vmscan/mem_boost_mode | V:/lib64/libexynoscamera3.so, V:/lib64/hw/camera.exynos9810.so strings | no | fimc-is2 sysfs |  |  |
| F | camera firmware | fimc_is_lib.bin, fimc_is_rta_2l3_3h1.bin, setfile_{2l3,3h1,3m3}.bin, ois_fw_{dom,sec}.bin, HFD*, score/, iva20_rt-lhotse.bin | V:/firmware listing | fw loader | fimc-is2, score, iva |  |  |
| C | camera aux | /dev/iva_ctl, /dev/vertex0/1, /dev/score | V:/lib64/libiva_android_hal.so strings; V:/etc/ueventd.rc | no | K49 iva/score/vertex |  |  |
| F | MFC codec | /dev/video6-11 (mediacodec:mediadrm), firmware mfc_fw.bin; OMX via libExynosOMX + libexynosv4l2 | V:/etc/ueventd.rc; V:/firmware/mfc_fw.bin; genfscon /devices/platform/178d0000.mfc0/video4linux/video{6,7,8}/name | s5p-mfc upstream stops at v10 (GUESS no v12) | K49 VIDEO_EXYNOS_MFC |  |  **GUESS** |
| C | JPEG / misc m2m | /dev/video12,13 (libhwjpeg, SMFC), /dev/m2m1shot_jpeg, /dev/video55, /dev/tsmux, /dev/repeater, /dev/hdcp2, /dev/media2 | V:/lib64/libhwjpeg.so strings; V:/etc/ueventd.rc | no | K49 SMFC, repeater, tsmux |  |  |

### samsung-misc

| Pri | Item | Exact path / ioctl / version | Evidence (in image) | Stock 6.12 | 4.9 provider | 6.12 port | Notes |
|---|---|---|---|---|---|---|---|
| C | /sys/class/sec/* | ap_pmic, ccic, disp_pmic, gps, hdm, led, mmc, rtc, sdcard, sec_abc, sec_debug, sec_epen, sec_hw_param, sec_key, sec_touchkey, switch, tsp, sensorhub, ... (rc chown targets) | RAW/rc-paths.txt | no | K49 drivers/staging/samsung (sec_class) |  | init chown/chmod/write on a missing path only logs an error; only nodes read by HALs (tsp/cmd, switch/afc_disable, led/led_blink, sec_key/*) matter |
| C | sec_debug / proc | /proc/{reset_reason,reset_summary,auto_comment,last_kmsg,store_lastkmsg,...}, /sys/module/sec_debug/parameters/* | V:/etc/init/init.samsungexynos9810.rc | no | K49 SEC_DEBUG* |  |  |
| C | ecd firmware | write /proc/ecd/load_firmware 1 | V:/etc/init/init.samsungexynos9810.rc:957 | no | K49 ecd |  |  |
| C | clat sysfs | /sys/kernel/clat/{xlat_plat,xlat_addrs,xlat_v4_addrs} | V:/etc/init/init.samsungexynos9810.rc | no | Samsung clat |  |  |

### sepolicy

| Pri | Item | Exact path / ioctl / version | Evidence (in image) | Stock 6.12 | 4.9 provider | 6.12 port | Notes |
|---|---|---|---|---|---|---|---|
| F | file_contexts /dev | 84 /dev entries (e.g. /dev/mali[0-9] gpu_device, /dev/g2d graphics_device, /dev/mobicore* tee_device, /dev/umts_* vendor_radio_device, /dev/ssp_sensorhub ssp_device, pm_qos misc nodes) | RAW/sepolicy-dev-contexts.txt (V:/etc/selinux/vendor_file_contexts + O:/etc/selinux/odm_file_contexts) | n/a | n/a |  | Node names must match for enforcing mode; ODM carries precompiled_sepolicy |
| F | genfscon sysfs | 115 path-based genfscon labels (decon vsync, panel_drv@001 backlight, max77705 power_supply, 13ac0000.spi iio, s2mps18-rtc, ...) | RAW/sepolicy-genfscon.txt (V:/etc/selinux/vendor_sepolicy.cil) | n/a | n/a |  | In enforcing mode a renamed sysfs path loses its label -> HAL denials. RECOVERY cmdline uses androidboot.selinux=permissive; BOOT not inspected |
| F | ueventd perms | V:/etc/ueventd.rc node list (mali0 0666, ion 0666, g2d, video*, mobicore*, umts*, bbd*, ssp*, iio) | V:/etc/ueventd.rc | n/a | n/a |  |  |
