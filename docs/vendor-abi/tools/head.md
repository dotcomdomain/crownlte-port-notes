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
- `tools/gen_vendor_abi.py`: the single data source that writes the JSON and the tables. Edit it, then run `python3 -I tools/gen_vendor_abi.py .`
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
