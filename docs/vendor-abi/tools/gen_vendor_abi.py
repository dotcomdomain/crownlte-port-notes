#!/usr/bin/env python3
"""Generate vendor-abi.json and the per-subsystem tables of VENDOR-ABI.md.

Single data source: ROWS below. Run on gekko:
  python3 -I gen_vendor_abi.py OUTDIR
Writes OUTDIR/vendor-abi.json and OUTDIR/vendor-abi-table.md (included into VENDOR-ABI.md).

Citation prefixes inside 'evidence':
  V:/path  file inside VENDOR.img (mounted as /vendor)
  O:/path  file inside ODM.img (/odm)
  S:/path  file inside SYSTEM.img (system-as-root; /system/... is S:/system/...)
  K49:     /root/crownlte-hid-kernel (4.9 source running on the phone)
  A:       6.12 worktree /mnt/ssd/host-builds/kernel-upgrade/android-config (branch crownlte-android-config)
  ACFG:    /mnt/ssd/host-builds/kernel-upgrade/build/android-config-clean-6.12.93/.config
  G:       6.12 worktree /root/kernel-upgrade/graphics-cap-6.12.93 (Mali/ION line)
  RAW:     /mnt/ssd/host-builds/kernel-upgrade/roadmap/vendor-abi/raw/
Priority: B = boot-blocking, F = feature-blocking, C = cosmetic.
"""
import json, sys, os

ROWS = []
def r(sub, item, value, evidence, stock612, drv49, prio, port612="", note="", guess=False):
    ROWS.append(dict(subsystem=sub, item=item, value=value, evidence=evidence,
                     stock_6_12=stock612, provider_4_9=drv49, port_6_12=port612,
                     priority=prio, guess=guess, note=note))

# ---------------------------------------------------------------- boot / cmdline / storage
S = "boot-core"
r(S, "androidboot.hardware", "androidboot.hardware=samsungexynos9810 (ro.hardware)",
  "V:/etc/init/init.samsungexynos9810.rc:804 `mount_all /vendor/etc/fstab.${ro.hardware}`; cmdline string in V:/etc/recovery.img (`androidboot.hardware=samsungexynos9810`)",
  "n/a (bootloader/boot-image cmdline)", "boot.img header cmdline + SBoot-appended androidboot.* args", "B",
  "diag DTS sets bootargs \"panic=30 ro\" (memory note); production boot must keep the boot.img cmdline and SBoot androidboot.* args (do not CMDLINE_FORCE)",
  "Without it init cannot find fstab.samsungexynos9810 nor init.samsungexynos9810.rc")
r(S, "UFS by-name links", "/dev/block/platform/11120000.ufs/by-name/{SYSTEM,VENDOR,ODM,USERDATA,CACHE,EFS,CPEFS,RADIO,PERSISTENT,STEADY,MISC,BOOT,RECOVERY}",
  "V:/etc/fstab.samsungexynos9810; V:/etc/init/init.samsungexynos9810.rc:5,8; V:/etc/init/init.baseband.rc (symlink RADIO->/dev/mbin0); V:/etc/ueventd.rc",
  "yes if the UFS platform device is named exactly `11120000.ufs` (DT node ufs@11120000) and GPT names are exposed", "K49 drivers/scsi/ufs (exynos ufs)", "B",
  "UFS port gated (memory: delayed-ufs, SCSI LUN guard)", "first-stage init creates by-name links from the platform device name; any rename breaks every fstab line")
r(S, "first_stage_mount fstab", "SYSTEM/VENDOR/ODM ext4 ro, flags wait,first_stage_mount (no avb=, no logical/super)",
  "V:/etc/fstab.samsungexynos9810 lines 2-4", "yes (ext4)", "ext4", "B", "",
  "First-stage fstab itself lives in the BOOT ramdisk (not inspected; GUESS it matches)", guess=True)
r(S, "/data filesystem", "f2fs USERDATA: noatime,nosuid,nodev,discard,usrquota,grpquota,fsync_mode=nobarrier,reserve_root=32768,resgid=5678,inlinecrypt + latemount,wait,check,quota,reservedsize=128M",
  "V:/etc/fstab.samsungexynos9810 line 7", "yes (F2FS_FS, F2FS quota, F2FS_FS_SECURITY)", "K49 fs/f2fs", "B",
  "ACFG: F2FS_FS=y", "fsync_mode=nobarrier and reserve_root/resgid are upstream f2fs options")
r(S, "FBE policy", "fileencryption=aes-256-xts:aes-256-cts:v2+inlinecrypt_optimized; NO metadata_encryption, NO wrappedkey, NO keydirectory",
  "V:/etc/fstab.samsungexynos9810 line 7 (grep confirms no metadata_encryption/wrappedkey anywhere in fstab)",
  "yes: fscrypt v2 + IV_INO_LBLK_64 + blk-crypto (inline engine or blk-crypto-fallback)", "K49 fs/crypto + FMP (drivers/crypto/fmp) inline engine", "B",
  "ACFG: FS_ENCRYPTION=y FS_ENCRYPTION_INLINE_CRYPT=y BLK_INLINE_ENCRYPTION=y; A commit 21d7dcb7f (FMP struct_group fix)",
  "Ciphertext must be bit-identical to 4.9 FMP output (AES-256-XTS, IV_INO_LBLK_64). Keys are stored keymaster-wrapped by vold -> needs Trustonic keymaster (see tee rows)")
r(S, "EFS mount", "EFS ext4 -> /mnt/vendor/efs (errors=panic, wait,check); bind to /efs; CPEFS ext4 ro,noload,nofail -> /mnt/vendor/cpefs",
  "V:/etc/fstab.samsungexynos9810 lines 8-9; V:/etc/init/init.samsungexynos9810.rc:807-808", "yes (ext4)", "ext4", "B",
  "", "errors=panic => any ext4 error on EFS panics the kernel. mcDriverDaemon reads MC_AUTH_TOKEN_PATH=/efs (V:/etc/init/mobicore.rc), so EFS gates the TEE")
r(S, "CACHE mount", "CACHE ext4 noatime,...,errors=panic wait,check", "V:/etc/fstab.samsungexynos9810 line 6", "yes", "ext4", "F")
r(S, "zram swap", "/dev/block/zram0 zramsize=2147483648,max_comp_streams=8 (swapon_all)", "V:/etc/fstab.samsungexynos9810 last line; V:/etc/init/init.swap.rc",
  "yes (ZRAM)", "zram", "F", "ACFG: ZRAM=y")
r(S, "SD card / USB OTG vold paths", "/devices/platform/11500000.dwmmc2/mmc_host* (sdcard), /devices/platform/10c00000.usb/10c00000.dwc3* (usb)",
  "V:/etc/fstab.samsungexynos9810 lines 12-13", "yes if DT device names match", "dw_mmc exynos; dwc3 exynos", "F")
r(S, "binderfs", "mount binder /dev/binderfs; /dev/{binder,hwbinder,vndbinder} symlinks; features/{extended_error,freeze_notification,oneway_spam_detection}",
  "S:/system/etc/init/hw/init.rc:270-284; V:/lib64/libbinder.so strings", "yes (ANDROID_BINDER_IPC, ANDROID_BINDERFS)", "binder", "B", "ACFG: ANDROID_BINDERFS=y")
r(S, "PSI (lmkd)", "ro.lmk.use_psi=true; lmkd is `critical`", "V:/build.prop (# LMK); S:/system/etc/init/lmkd.rc:6", "yes (PSI)", "PSI backport", "B",
  "ACFG: PSI=y", "lmkd crash loop => init reboots to bootloader (critical service)")
r(S, "cgroups (system)", "v1 blkio:/dev/blkio, cpu:/dev/cpuctl, cpuset:/dev/cpuset; v2 /sys/fs/cgroup with freezer (cgroup.freeze) and optional memory; task_profiles use cpu.uclamp.{min,max,latency_sensitive}",
  "S:/system/etc/cgroups.json; S:/system/etc/task_profiles.json", "yes (cpu.uclamp.latency_sensitive is ACK-only)", "cgroup v1 + backports", "B",
  "ACFG: CGROUPS CGROUP_SCHED CPUSETS CGROUP_FREEZER MEMCG UCLAMP_TASK(_GROUP)=y",
  "Severity of a missing controller is a GUESS: libprocessgroup logs and continues, but SetTaskProfiles failures can break zygote/app start", guess=True)
r(S, "schedtune cgroup", "vendor cgroups.json: Controller schedtune at /dev/stune; task_profiles schedtune.boost / schedtune.prefer_idle; rc mkdir /dev/stune/{bg_abnormal,bg_cached}",
  "V:/etc/cgroups.json; V:/etc/task_profiles.json:5-128; V:/etc/init/init.samsungexynos9810.rc:704-712; S:/system/etc/init/hw/init.rc:121-140",
  "no (schedtune never upstream)", "K49 kernel/sched/tune.c (CONFIG_SCHED_TUNE=y)", "F",
  "roadmap: ported in 8819c221c (other branch); not in ACFG",
  "Alternative: drop schedtune from V:/etc/cgroups.json+task_profiles.json (vendor overlay) and rely on uclamp")
r(S, "freezer v1 (freecess)", "mount cgroup none /dev/freezer freezer; frozen/thaw/olaf", "V:/etc/init/init.samsungexynos9810.rc:395-429",
  "yes (CGROUP_FREEZER v1)", "cgroup freezer", "C", "", "Samsung freecess only; Lineage framework uses the v2 freezer")
r(S, "BPF (netd/netbpfload)", "bpfloader `reboot_on_failure reboot,netbpfload-missing`; real loader in com.android.tethering apex", "S:/system/etc/init/netbpfload.rc",
  "yes (BPF_SYSCALL, CGROUP_BPF, BPF_JIT...)", "4.9 + Lineage workarounds", "B", "ACFG: BPF_SYSCALL=y",
  "Exact config list not extracted (capex compressed); use the Android 16 kernel requirements list", guess=True)
r(S, "memfd vs ashmem", "vendor libcutils has has_memfd_support() + ashmem fallback; only libcutils references /dev/ashmem", "V:/lib64/libcutils.so strings; RAW/elf-paths.tsv",
  "memfd yes; ashmem removed upstream", "ashmem staging", "C", "A: drivers/staging/android ashmem (Rust)", "With MEMFD_CREATE (ACFG=y) Android 16 uses memfd; ashmem not needed by vendor blobs (GUESS: no blob dlsym's ashmem ioctls directly)", guess=True)
r(S, "firmware search path", "ueventd firmware_directories /etc/firmware/ /odm/firmware/ /vendor/firmware/ /firmware/image/; all blobs live in /vendor/firmware",
  "S:/system/etc/ueventd.rc:4; V:/firmware/ listing", "yes only with FW_LOADER_USER_HELPER(+_FALLBACK) or firmware_class.path=/vendor/firmware", "CONFIG_FW_LOADER_USER_HELPER(_FALLBACK)=y (K49/out/.config)", "F",
  "ACFG: FW_LOADER=y, USER_HELPER not set", "Affects Wi-Fi, BT .hcd, MFC, FIMC-IS, ABOX/VTS, DSM, SCORE, NFC firmware")
r(S, "pstore / last_kmsg", "/sys/fs/pstore/console-ramoops(-0), /proc/last_kmsg (charger + health service `file` lines)",
  "V:/etc/init/android.hardware.health-service.samsung.rc", "pstore yes; /proc/last_kmsg is Samsung", "sec_debug last_kmsg", "C")
r(S, "vendor recovery reflash", "install-recovery.sh flashes V:/etc/recovery.img to RECOVERY if persist.vendor.recovery_update=true",
  "V:/etc/init/vendor_flash_recovery.rc; V:/bin/install-recovery.sh", "n/a", "n/a", "C", "",
  "Not an ABI item: would overwrite the diag RECOVERY slot if that property is ever set")

# ---------------------------------------------------------------- graphics
S = "gpu"
r(S, "Mali DDK version", "libGLES_mali.so 1.4 Bifrost \"r38p1-01bet0\" (r38p1-01bet0-mbs2v41_0), MaliG72_r38p1_LT_T",
  "V:/lib64/egl/libGLES_mali.so strings; V:/build.prop ro.hardware.egl=mali ro.hardware.vulkan=mali", "no (panfrost not ABI-compatible)",
  "K49 drivers/gpu/arm/bv_r38p1 (MALI_RELEASE_NAME r38p1-01bet0)", "B", "G: drivers/gpu/arm/bv_r38p1 + exynos")
r(S, "kbase UK version", "VERSION_CHECK sends major=11 minor=35 (`mov w10,#0xb; movk w10,#0x23,lsl#16` at 0x1a3d0c0)",
  "V:/lib64/egl/libGLES_mali.so disasm 0x1a3d0c0; K49 include/uapi/gpu/arm/bv_r38p1/jm/mali_kbase_jm_ioctl.h:131-132", "no", "bv_r38p1 JM", "B", "G")
r(S, "kbase device node", "/dev/mali0 (opened via \"/dev/mali\"+index, O_RDWR|O_CLOEXEC, must be S_ISCHR); misc device name mali0",
  "V:/lib64/egl/libGLES_mali.so disasm 0x1a3f278-0x1a3f2ac; V:/etc/ueventd.rc (/dev/mali0 0666 system system); V:/etc/selinux/vendor_file_contexts /dev/mali[0-9]",
  "no", "bv_r38p1 misc device", "B", "G")
r(S, "kbase ioctls used", "type 0x80 nr {0 VERSION_CHECK,1 SET_FLAGS,2 JOB_SUBMIT,3 GET_GPUPROPS,5 MEM_ALLOC,6 MEM_QUERY,7 MEM_FREE,12 DISJOINT_QUERY,13 GET_DDK_VERSION,14 MEM_JIT_INIT (24B),15 MEM_SYNC,17 GET_CONTEXT_ID,18 TLSTREAM_ACQUIRE,20 MEM_COMMIT,21 MEM_ALIAS,22 MEM_IMPORT,23 MEM_FLAGS_CHANGE,24 STREAM_CREATE,25 FENCE_VALIDATE,27 MEM_PROFILE_ADD,28 SOFT_EVENT_UPDATE,29/30 STICKY_RESOURCE_(UN)MAP,38 MEM_EXEC_INIT,42 Samsung MALI_EXYNOS_IOCTL_EHMP,50 GET_CPU_GPU_TIMEINFO,51 KINSTR_JM_FD,54 CONTEXT_PRIORITY_CHECK} (+ size-0 _IO 4 POST_TERM / 19 TLSTREAM_FLUSH not scanned)",
  "RAW/libGLES_mali-ioctls.txt; K49 include/uapi/gpu/arm/bv_r38p1/mali_kbase_ioctl.h, jm/mali_kbase_jm_ioctl.h; K49 include/uapi/gpu/arm/exynos/mali_exynos_ioctl.h:152",
  "no", "bv_r38p1 + gpex", "B", "G", "nr 14 seen with size 24 (= kbase_ioctl_mem_jit_init, 11.20+ layout). nr 42 is Samsung-private; an unknown ioctl returns -EINVAL/-ENOTTY, so its absence is probably non-fatal (GUESS)")
r(S, "memtrack", "/sys/class/misc/mali0/device/* (reads per-process GPU memory)", "V:/bin/hw/android.hardware.memtrack-service.samsung-mali strings", "no", "bv_r38p1 sysfs", "C", "G")
r(S, "GPU DVFS sysfs (power HAL)", "/sys/kernel/gpu/gpu_max_clock, gpu_min_clock (values 572000/546000/455000 ... 299000), gpu_freq_table, gpu_governor, gpu_info",
  "V:/etc/powerhint.json Nodes GPUMaxFreq/GPUMinFreq; V:/etc/init/init.samsungexynos9810.rc chowns", "no", "K49 drivers/gpu/arm/exynos (gpex sysfs)", "F", "G (exynos dir present)", "Missing nodes only make libperfmgr log errors")

S = "memory"
r(S, "allocator selection", "libion_exynos getDefaultExporter(): open(\"/dev/dma_heap/system\") ok -> mode 3 (dma-heap); else open /dev/ion + ION_IOC_FREE(handle 0): errno ENOTTY -> mode 1 (modern ION), other -> mode 2 (legacy ION)",
  "V:/lib64/libion_exynos.so disasm 0x2ff0-0x3100 (strings 0x13a1 \"/dev/dma_heap/\", 0x14ad \"system\", 0x1319 \"/dev/ion\")",
  "dma-heap: yes (DMABUF_HEAPS_SYSTEM)", "legacy ION /dev/ion (K49 drivers/staging/android/ion, CONFIG_ION_EXYNOS=y)", "B",
  "ACFG: DMABUF_HEAPS=y DMABUF_HEAPS_SYSTEM=y DMABUF_HEAPS_CMA=y -> the android-config kernel already makes libion_exynos take the dma-heap path; G also carries legacy ION",
  "CORRECTS ROADMAP 4.2: legacy ION is NOT required by gralloc/libion_exynos. dma-heap is preferred whenever /dev/dma_heap/system exists")
r(S, "dma-heap names", "/dev/dma_heap/<name>[-secure|-uncached]: heap-id bit -> name: 0 system, 1 crypto, 2 reserved(vfw), 3 system-secure-vstream, 4 reserved, 5 vframe, 6 vscaler, 7 reserved(vnfw), 8 gpu_crc, 9 system-secure-gpu_buffer, 10 camera, 11 secure_camera, 12 reserved, 13 ext_ui, 14 system(vendor_system_heap). Suffix: flags&0x10010 -> \"-secure\"; else !(flags&ION_FLAG_CACHED=1) -> \"-uncached\"",
  "V:/lib64/libion_exynos.so alloc_dma_heap disasm 0x24e0-0x2690, table .data.rel.ro 0x5048 (24-byte entries {ion_name, dmaheap_name, len})",
  "upstream has only `system` (+cma `reserved`/`default_cma_region`); `system-uncached` is ACK-only", "n/a (4.9 uses ION ids)", "B",
  "A: drivers/dma-buf/heaps/system_heap.c:534 \"system\", :542 \"system-uncached\"",
  "Boot needs system + system-uncached (gralloc non-cached buffers). camera/secure_camera/vframe/vscaler/crypto/system-secure-* are feature-level (camera, DRM video). S:/system/etc/ueventd.rc:54-56 sets perms for system, system-uncached, system-secure")
r(S, "dma-heap alloc ioctl", "DMA_HEAP_IOCTL_ALLOC 0xc0184800 (_IOWR('H',0,24))", "ioctl scan of V:/lib64/libion_exynos.so (alloc_dma_heap 0x26a0-0x26b0)", "yes", "n/a", "B")
r(S, "legacy ION ioctls (fallback)", "ION_IOC_ALLOC 0xc0204900 (legacy 32B) / 0xc0184900 (modern 24B), FREE 0xc0044901, SHARE 0xc0084904, IMPORT 0xc0084905, SYNC 0xc0084907, HEAP_QUERY 0xc0184908, 0xc0184909 (_IOWR('I',9,24))",
  "ioctl scan V:/lib64/libion_exynos.so, V:/lib64/libion.so", "no (ION removed in 5.11)", "K49 ION + exynos ion", "C", "G: drivers/staging/android/ion", "Only used if /dev/dma_heap/system is absent")
r(S, "dmabuf sync / container", "DMA_BUF_IOCTL_SYNC 0x40086200; Samsung /dev/dmabuf_container: 'b' 13 merge (0xc018620d), 14 set/get mask (0x4004620e/0x8004620e)",
  "ioctl scan V:/lib64/libion_exynos.so; strings \"/dev/dmabuf_container\", \"dma_buf_merge\"", "SYNC yes; container no", "K49 dma-buf container (CONFIG_DMABUF_CONTAINER, GUESS)", "C", "",
  "dma_buf_merge failure only disables buffer merging (GUESS)", guess=True)
r(S, "ION sysfs", "/sys/class/ion_cma/ion_video_ext/isolated", "V:/etc/init/init.samsungexynos9810.rc (on fs)", "no", "exynos ION cma", "C")
r(S, "gralloc", "gralloc.universal9810.so -> libion_exynos (exynos_ion_alloc/import_handle/free_handle; import/free are no-ops unless legacy mode)",
  "V:/lib64/hw/gralloc.universal9810.so NEEDED/strings; V:/lib64/libion_exynos.so import_handle disasm 0x2a38-0x2a48 (mode==2 check)", "n/a", "n/a", "B", "",
  "allocator@2.0-service + mapper@2.0-impl-2.1 (HIDL) load gralloc.universal9810 via libhardware")

S = "display"
r(S, "HWC interface selection", "ExynosDevice::getDeviceInterfaceType(): access(\"/dev/dri/card0\",F_OK)==0 -> DRM interface, else fbdev (/dev/graphics/fb0)",
  "V:/lib64/libexynosdisplay.so disasm 0x3f2b0-0x3f2d0, string 0x20737 \"/dev/dri/card0\"", "n/a", "fbdev path (no DRM on 4.9)", "B",
  "ACFG: DRM not set, FB=y FB_SIMPLE=y (good)",
  "Any DRM driver that creates /dev/dri/card0 (simpledrm, panfrost, exynos-drm) flips HWC into its DRM backend, which expects Samsung exynos-drm properties (DQE_FD, HDR_FD, partial_region, eotf_lut, ... in V:/lib64/libdrmresource.so)")
r(S, "fb device", "/dev/graphics/fb0 (primary, decon_f), /dev/graphics/fb1 (decon_t / DP)", "V:/lib64/libexynosdisplay.so strings; S:/system/etc/ueventd.rc:7,69",
  "fbdev core yes; DECON driver no", "K49 drivers/video/fbdev/exynos/dpu_9810 (CONFIG_EXYNOS_DECON_9810/_FB)", "B", "none")
r(S, "DECON ioctls (primary)", "S3CFB_WIN_CONFIG _IOW('F',209,1416B)=0x458846d1; S3CFB_SET_VSYNC_INT 0x400446ce; S3CFB_POWER_MODE 0x400446df; S3CFB_DECON_SELF_REFRESH 0x400446cf; FBIO_WAITFORVSYNC 0x40044620 (gralloc fb)",
  "RAW/display-ioctls.txt (V:/lib64/libexynosdisplay.so, V:/lib64/hw/gralloc.universal9810.so); K49 dpu_9810/decon.h:1726-1750", "no", "dpu_9810 decon_core.c:2914-3299", "B", "none",
  "nr>255 is OR'ed into the type byte by _IOC(): 260->'G'4, 300->'G'44, 400->'G'144, 600-602->'F'88-90, 700-705->'F'188-193, 800->'G'32, 850->'G'82")
r(S, "DECON ioctls (info/modes)", "EXYNOS_DISP_INFO 0x40f84704 (248B); EXYNOS_GET/SET_DISPLAY_MODE(_NUM/_CURRENT) 0x400446bc/0x401c46bd/0x401c46be/0x400446c1; EXYNOS_GET/SET_COLOR_MODE(_NUM) 0x40044658/0x40084659/0x4004465a; S3CFB_GET_HDR_CAPABILITIES 0x40104790; EXYNOS_GET_EDID 0x42044720; EXYNOS_GET_DISPLAYPORT_CONFIG 0x4124472c; EXYNOS_GET_VSYNC_CHANGE_TIMELINE 0x40204752; DECON_WIN_CURSOR_POS 0x400846de",
  "RAW/display-ioctls.txt; K49 dpu_9810/decon.h:1734-1785", "no", "dpu_9810", "B", "none", "EXYNOS_DISP_INFO feeds xres/yres/xdpi; failure leaves the primary display unconfigured (GUESS)", guess=True)
r(S, "HWC ioctls with no 4.9 handler", "0x4d084705 ('F'261, 3336B), 0x400446d3 ('F'211, logged as S3CFB_LOW_PERSISTENCE), 0x40084662..0x40014667 ('F'610-615)",
  "RAW/display-ioctls.txt; not present in K49 decon.h/decon_core.c", "no", "none (4.9 returns -ENOTTY today)", "C", "",
  "Must stay tolerated: return -ENOTTY like 4.9")
r(S, "vsync / PSR sysfs", "/sys/devices/platform/16030000.decon_f/vsync, .../psr_info, /sys/devices/platform/16050000.decon_t/vsync",
  "V:/lib64/libexynosdisplay.so strings; genfscon in V:/etc/selinux/vendor_sepolicy.cil (RAW/sepolicy-genfscon.txt)", "no", "dpu_9810 decon sysfs", "B", "",
  "Platform device names must be exactly 16030000.decon_f / 16050000.decon_t")
r(S, "DP hotplug extcon", "/sys/devices/platform/%s/extcon/extcon0/cable.%d/state", "V:/lib64/libexynosdisplay.so strings; V:/etc/selinux/vendor_file_contexts (sysfs_extcon)", "extcon yes", "displayport + ccic", "F")
r(S, "fence sync", "release/acquire fences via sync_file (libsync)", "V:/lib64/libexynosdisplay.so NEEDED libsync.so", "yes (SYNC_FILE, SW_SYNC)", "DPU_2_0_RELEASE_FENCE + sync_file", "B", "ACFG: SYNC_FILE=y SW_SYNC=y")
r(S, "G2D (libacryl)", "/dev/g2d (+ /dev/fimg2d probe); 'M' ioctls 3,4 (272/304B), 5, 6, 7; G2D_IOC_PROCESS=_IOWR('M',4), PRIORITY 5, PERFORMANCE 6",
  "ioctl scan V:/lib64/libacryl.so; V:/etc/ueventd.rc (/dev/g2d 0660 system graphics); K49 drivers/gpu/exynos/g2d/g2d_uapi.h:272-274", "no", "K49 drivers/gpu/exynos/g2d", "F", "none",
  "HWC uses G2D for composition/clear; HWC falls back to GLES if acryl fails (GUESS)", guess=True)
r(S, "MSCL scaler (libacryl/libhwjpeg)", "/dev/video50 (m2m1shot_scaler0 alias), V4L2 m2m ioctls 'V' 0,5,8,15,17,18,19,28,60,72,193", "ioctl scan V:/lib64/libacryl.so; V:/etc/ueventd.rc", "V4L2 core yes; driver no", "K49 VIDEO_EXYNOS_SCALER", "F")
r(S, "backlight", "/sys/class/backlight/panel/{brightness,max_brightness} (max 44700 per roadmap); device path panel_drv@001/backlight/panel",
  "V:/bin/hw/android.hardware.light-service.samsung strings; V:/lib64/libexynosdisplay.so; genfscon /devices/platform/panel_drv@001/backlight/panel/*", "class yes; driver no", "K49 drivers/video/fbdev/exynos/panel", "F")
r(S, "panel / mdnie sysfs", "/sys/class/lcd/panel/* (31 attrs incl. power_reduce, alpm, hmt_*, fix_green_screen), /sys/class/mdnie/mdnie/{mode,mode_max,scenario,accessibility,sensorRGB,lux,night_mode,...}",
  "V:/etc/init/init.samsungexynos9810.rc; V:/etc/init/init.greenfix.rc; V:/bin/hw/vendor.lineage.livedisplay-service.samsung-exynos strings", "no", "K49 panel + mdnie", "C")
r(S, "display idle hint", "/sys/class/graphics/fb0/device/idle_state (or /sys/class/drm/card0/device/idle_state)", "V:/lib64/libdisppower-pixel.so strings", "no", "none on 4.9 either (GUESS)", "C", guess=True)
r(S, "charger-mode UI", "minui on /dev/graphics/fb0 (or /dev/dri)", "V:/bin/hw/android.hardware.health-service.samsung strings (/dev/graphics/fb0, /dev/dri); V:/etc/init/android.hardware.health-service.samsung.rc (`on charger chown /dev/graphics/fb0`)",
  "fbdev/simplefb ok", "dpu_9810", "F")

# ---------------------------------------------------------------- TEE
S = "tee"
r(S, "Trustonic nodes", "/dev/mobicore (0700 system, admin), /dev/mobicore-user (0666 radio:system), /dev/t-base-tui",
  "V:/etc/ueventd.rc; V:/bin/mcDriverDaemon strings; V:/lib64/libMcClient.so strings", "no", "K49 drivers/gud/gud-exynos9810/MobiCoreDriver (CONFIG_TRUSTONIC_TEE=y)", "B",
  "A: drivers/gud/gud-exynos9810, ACFG TRUSTONIC_TEE=y")
r(S, "MobiCore user API", "MCDRVMODULEAPI 6.3; user (/dev/mobicore-user) 'M' 0 OPEN_SESSION 64B, 1 OPEN_TRUSTLET 40B, 2 CLOSE_SESSION, 3 NOTIFY, 4 WAIT 12B, 5 MAP 40B, 6 UNMAP 40B, 7 ERR 8B, 9 VERSION 96B, GP 20 INIT_CONTEXT, 21 REGISTER_SHM 40B, 23 RELEASE_SHM, 24 GP_OPEN_SESSION 224B, 25 GP_CLOSE, 26 INVOKE 192B, 27 CANCEL 168B; admin (/dev/mobicore, daemon) 'M' 0 GET_DRIVER_REQUEST 32B, 1 GET_INFO 8B, 2 LOAD_DRIVER, 3 LOAD_TOKEN, 4 LOAD_CHECK (24B); all match 4.9 numbering and sizes as scanned",
  "ioctl scan V:/bin/mcDriverDaemon, V:/lib64/libMcClient.so; K49 drivers/gud/gud-exynos9810/MobiCoreDriver/public/mc_user.h:18-19,282-314, mc_admin.h:68-76", "no", "gud-exynos9810", "B", "A",
  "libMcClient checks the driver version (\"driver version mismatch detected: expected version major %d\")")
r(S, "mcDriverDaemon + TAs", "service mobicore loads FFFFFFFF000000000000000000000001.drbin, ffffffffd0000000000000000000000a.tlbin, ...16.tlbin, ...45.drbin from /vendor/app/mcRegistry; auth token /efs (MC_AUTH_TOKEN_PATH), /efs/TEE, /data/vendor/mcRegistry",
  "V:/etc/init/mobicore.rc; V:/app/mcRegistry (30 TAs/drivers)", "n/a", "n/a", "B", "", "/sys/kernel/debug/trustonic_tee/crashdump optional")
r(S, "keymaster / gatekeeper", "android.hardware.keymaster@3.0-service (class early_hal) + gatekeeper@1.0-service -> keystore.mdfpp.so / gatekeeper.exynos9810.so -> libMcClient -> Kinibi TAs",
  "V:/etc/init/android.hardware.keymaster@3.0-service.rc, keymaster@3.0-override.rc, android.hardware.gatekeeper@1.0-service.rc; V:/build.prop ro.hardware.keystore=mdfpp",
  "n/a", "Trustonic", "B", "", "vold stores FBE keys keymaster-wrapped -> no TEE = no /data unlock (init_user0 failure). Risk: keymaster key upgrade on os_version change (ROADMAP risk 3)")
r(S, "secure memory / DRM", "/dev/s5p-smem, /dev/ion (liboemcrypto), system-secure-* dma-heaps, /dev/tzic, /dev/hdcp2", "V:/lib/liboemcrypto.so strings; V:/etc/ueventd.rc", "no", "K49 secmem/content path protection", "F")

# ---------------------------------------------------------------- USB
S = "usb"
r(S, "configfs gadget", "mount configfs /sys/kernel/config; g1 with functions mtp.0 ptp.0 acm.0 dm.0 rndis.0 conn_gadget.0 midi.0 accessory.0 audio_source.0 ncm.0 ffs.adb; VID 0x04e8 PID 0x685d/0x6860/0x6862-0x6865",
  "V:/etc/init/init.samsungexynos9810.usb.rc", "configfs+FFS+acm+rndis+ncm+midi yes; mtp/ptp/dm/conn_gadget/accessory/audio_source no (Android/Samsung kernel functions)",
  "K49 USB_CONFIGFS_F_MTP/PTP/DM, USB_ANDROID_SAMSUNG_COMPOSITE", "F", "ACFG: USB_CONFIGFS_F_FS=y (adb proven per ROADMAP)",
  "adb only needs ffs.adb. MTP is f_mtp (/dev/mtp_usb) because no ffs.mtp instance exists in the rc")
r(S, "UDC name", "UDC \"10c00000.dwc3\" (hard-coded) and ${sys.usb.controller}", "V:/etc/init/init.samsungexynos9810.usb.rc", "yes if dwc3 device is named 10c00000.dwc3", "dwc3 exynos", "F")
r(S, "android_usb class", "/sys/class/android_usb/android0/{enable,functions} (144 writes)", "V:/etc/init/init.samsungexynos9810.usb.rc", "no", "Samsung composite", "C", "", "Writes fail harmlessly")
r(S, "USB HAL", "/sys/class/typec/port0/{power_role,data_role,port_type}, -partner/{accessory_mode,supports_usb_power_delivery}; /sys/devices/virtual/sec/ccic/water; /sys/devices/virtual/usb_notify/usb_control/usb_data_enabled",
  "V:/etc/init/android.hardware.usb-service.samsung.rc; V:/bin/hw/android.hardware.usb-service.samsung strings", "typec class yes; MAX77705 CCIC driver no", "K49 CCIC_MAX77705 + TYPEC=y + USB_NOTIFY_LAYER", "F")

# ---------------------------------------------------------------- audio
S = "audio"
r(S, "ALSA card", "card 0 from `samsung,star-madera` (ABOX + CS47L93 + MAX98512); tinyalsa /dev/snd/pcmC%uD%u%c, controlC%u, comprC%uD%u (offload), hwC0D%d",
  "V:/lib64/libaudioproxy.so, V:/lib64/libtinyalsa.so strings; K49 DT49 sound node (line 12694)", "ASoC core yes; ABOX no; madera core + cs47l92 codec (covers CS47L93) partly; max98512 no",
  "K49 sound/soc/samsung (ABOX), sound/soc/codecs (madera, max98512)", "F", "none", "Audio HAL failure does not block boot (GUESS)", guess=True)
r(S, "mixer controls", "141 unique control names in V:/etc/mixer_paths.xml (ABOX *, madera EQ/HPOUT/IN/DMIC, `SPK Analog Left/Right Gain`, `Rcv Digital Gain`)", "RAW/mixer-ctls.txt",
  "names must match the 4.9 drivers exactly", "ABOX + madera + max98512", "F")
r(S, "mixer path from DT", "/proc/device-tree/sound/mixer-paths (optional override of mixer_paths.xml)", "V:/lib64/hw/audio.primary.universal9810.so strings", "DT", "DT49 (no such property today)", "C")
r(S, "ABOX/VTS nodes", "/dev/vts_fio_dev (0660 audioserver), /dev/seiren, /sys/devices/platform/17c50000.abox/0.abox_debug/, /sys/kernel/debug/abox/, /data/vendor/log/abox",
  "V:/etc/ueventd.rc; V:/lib64/libaudioproxy.so strings", "no", "K49 sound/soc/samsung abox/vts", "F")
r(S, "speaker amp DSM", "/sys/class/dsm/{max98512,maxdsm_cal,maxdsm_power}/*; /efs/maxim; firmware dsm.bin dsm_tune.bin", "V:/etc/init/init.samsungexynos9810.rc; V:/firmware", "no", "K49 maxdsm", "F", "",
  "ROADMAP risk 9: no playback without DSM limits")
r(S, "audio firmware", "AP_AUDIO_SLSI.bin, APBargeIn/APBiBF/APDV_AUDIO_SLSI.bin, vts.bin, cs47l92-dsp1-trace.wmfw, calliope_{dram,sram,iva}.bin, SoundBoosterParam.bin", "V:/firmware listing", "fw loader", "ABOX/VTS/madera", "F")

# ---------------------------------------------------------------- RIL / modem
S = "ril"
r(S, "modem io devices", "/dev/umts_boot0, umts_ipc0/1, umts_rfs0, umts_router, umts_dm0, umts_csd, umts_cass, umts_ramdump0, umts_ciq0, smd4, multipdp(_hiprio), rmnet0-7 (netdev)",
  "DT49 iod,name list; V:/lib64/libsec-ril-impl.so strings; V:/etc/ueventd.rc; V:/etc/selinux/vendor_file_contexts", "no", "K49 drivers/misc/modem_v1 (UMTS_MODEM_SS310AP, LINK_DEVICE_SHMEM)", "F", "none")
r(S, "cbd boot daemon", "cbd -d -tss310 -bm -mm -P platform/11120000.ufs/by-name/RADIO; reads /dev/mbin0 (RADIO symlink), ioctls _IO('o',0x19..0x59) incl. MODEM_ON/OFF/RESET/BOOT_ON/BOOT_OFF/BOOT_DONE/STATUS/DL_START/XMIT_BOOT(0x40)/SECURITY_REQ(0x53); also 0x36,0x65,0xde (no 4.9 handler)",
  "V:/etc/init/init.baseband.rc; disasm of V:/bin/cbd (mov w1,#0x6fNN counts); K49 drivers/misc/modem_v1/modem_prj.h:74-128", "no", "modem_v1", "F")
r(S, "RIL ioctls", "libsec-ril-impl: _IO('o',0x27 STATUS,0x30/0x31 NET_SUSPEND/RESUME,0x34 FORCE_CRASH_EXIT,0x61)", "disasm V:/lib64/libsec-ril-impl.so", "no", "modem_v1", "F")
r(S, "modem sysfs/DT", "/sys/devices/platform/10000.mif_pdata/sim/ds_detect; /sys/module/modem_ctrl_ss310ap/parameters/ds_detect; /proc/device-tree/model_info-system_rev (cbd); /sys/devices/virtual/misc/umts_dm0/dm_state; /sys/kernel/debug/svnet/mem_dump",
  "V:/etc/ueventd.rc; V:/etc/init/init.baseband.rc; V:/bin/cbd strings", "no", "modem_v1 + DT root property model_info-system_rev", "F")
r(S, "RIL misc", "/dev/block/param, /dev/drb, /dev/ramdump_memshare, /dev/nr_boot0 (absent on 9810), /dev/tun, /proc/net/xt_qtaguid/iface_stat_fmt (absent on 6.12, tolerated GUESS), /sys/module/mcps/*, /dev/network_throughput",
  "V:/lib64/libsec-ril-impl.so strings; V:/etc/init/init.samsungexynos9810.rc", "tun yes; rest no", "Samsung misc", "C", guess=True)
r(S, "EFS/NV safety", "RFS path via /dev/umts_rfs0 writes NV under /efs", "ROADMAP risk 4", "n/a", "modem_v1 rfs", "F", "", "Bring up CP with EFS backup in hand (backups-20261103)")

# ---------------------------------------------------------------- Wi-Fi / BT
S = "wifi-bt"
r(S, "Wi-Fi driver API", "nl80211 (cfg80211) + Broadcom vendor nl80211 commands (libwifi-hal BCMDHD vendor HAL) + Android private DRIVER commands via SIOCDEVPRIVATE+1 (0x89f1) from wpa_supplicant/hostapd (and 0x89f0 in hostapd)",
  "disasm V:/bin/hw/wpa_supplicant, V:/bin/hw/hostapd (mov w1,#0x89f1); V:/lib64/libwifi-hal.so strings (\"BCMDHD vendor HAL\")", "cfg80211 yes; bcmdhd no; brcmfmac lacks BCM4361 + Android private cmds",
  "K49 drivers/net/wireless/bcmdhd_101_16 (BCM4361, CUSTOMER_HW4_PRIVATE_CMD)", "F", "none; ACFG lacks CFG80211",
  "6.12 routes SIOCDEVPRIVATE through ndo_siocdevprivate (since 5.15): DHD must implement it, not only ndo_do_ioctl. wext not used")
r(S, "Wi-Fi interface / module", "wlan0 (wifi.interface), p2p/ap ifaces; /sys/module/dhd/parameters/firmware_path (module name must be `dhd`); modprobe -d /vendor/lib/modules dhd.ko (directory absent -> built-in today)",
  "V:/lib64/libwifi-hal.so strings; V:/etc/init/wifi_brcm.rc; V:/etc/init/wifi.rc", "n/a", "bcmdhd built-in", "F")
r(S, "Wi-Fi firmware", "request_firmware names bcmdhd_sta.bin_b0/_b2, bcmdhd_mfg.bin_b*, bcmdhd_mon.bin_b2, nvram.txt_{murata_r041,r02a,r02b}_b2, bcmdhd_clm.blob in /vendor/firmware; /data/vendor/conn/.*.info, /data/vendor/firmware/wifi/",
  "V:/firmware listing; K49 bcmdhd_101_16/Makefile:424-427 (DHD_LINUX_STD_FW_API, DHD_FW_NAME), dhd_custom_cis.c:250-255", "fw loader", "bcmdhd", "F", "", "Needs /vendor/firmware in the firmware search path (see boot-core firmware row)")
r(S, "Wi-Fi sysfs", "/sys/wifi/{mac_addr,cid,ant,pm,softap,wifiver,memdump,frameburst,control_he_enab,bad_ap_list,otp}, /sys/devices/platform/116a0000.pcie0/pcie_sysfs", "V:/etc/init/init.samsungexynos9810.rc; genfscon /wifi", "no", "bcmdhd + exynos PCIe", "C")
r(S, "Bluetooth", "HIDL bluetooth@1.0 + libbt-vendor: /dev/ttySAC1 (UART HCI), /sys/class/rfkill/rfkill%d/{state,type}, firmware bcm4361B2_{murata,semco}.hcd, BD addr /efs/bluetooth/bt_addr",
  "V:/lib64/libbt-vendor.so strings; V:/etc/init/init.samsungexynos9810.rc:649-661; V:/firmware", "serial + rfkill yes; BT power/rfkill driver no", "K49 drivers/bluetooth/bcm43xx.c + samsung serial", "F", "",
  "Userspace HCI over raw tty (not hci_uart). /dev/ttySAC1 must be the BT UART")
r(S, "GNSS", "gpsd/lhd: /dev/ttyBCM*, /dev/bbd_{control,packet,reliable,sensor,sio,patch,urgent_patch}, /sys/bbd/lk_{enable,timeout}, /sys/class/sec/gps/GPS_PWR_EN/value", "V:/etc/ueventd.rc; V:/bin/hw/lhd strings; V:/etc/init/init.gps.rc", "no", "K49 drivers/sensorhub/brcm/bbdpl + gps", "F")
r(S, "NFC", "/dev/sec-nfc ioctls 'S' 1 SET_MODE,2 SLEEP,3 WAKEUP; /dev/p3 (eSE); firmware V:/firmware/nfc", "ioctl scan V:/lib64/nfc_nci_sec.so; K49 drivers/nfc/sec_nfc.h:23-30", "no (s3fwrn5 ABI differs)", "K49 drivers/nfc (SEC_NFC, ESE_P3_LSI)", "F")

# ---------------------------------------------------------------- sensors / biometrics / input
S = "sensors"
r(S, "sensor hub", "/dev/ssp_sensorhub, /dev/batch_io, /dev/ssp_data_injection, iio:device* (buffer/enable, buffer/length, poll_delay under /sys/devices/platform/13ac0000.spi/spi_master/spi20/spi20.0/), input event devices, /sys/class/sensors/ssp_sensor/{enable,ssp_control,ssp_flush}, /sys/class/sensors/<sensor>/name",
  "V:/lib64/sensors.sensorhub.so strings; V:/etc/ueventd.rc; V:/etc/selinux/vendor_file_contexts (13ac0000.spi sysfs_iio)", "IIO core yes; SSP driver no", "K49 drivers/sensorhub/brcm (SENSORS_SSP_BBD)", "F", "none")
r(S, "HRM / grip", "/dev/max_hrm, /sys/class/sensors/hrm_sensor/*, /sys/class/sensors/grip_sensor*/*, /sys/class/sensor_event/symlink/", "V:/lib64/sensors.bio.so, sensors.grip.so strings", "no", "K49 drivers/optics (max86915), sx9320", "C")
r(S, "hall / keys", "/sys/devices/virtual/sec/sec_key/{hall_detect,certify_hall_detect,wakeup_keys}", "V:/lib64/sensors.sensorhub.so; V:/etc/init/hw/init.samsung.rc", "no (sec class)", "K49 hall.c, certify_hall.c, sec_key", "C")
r(S, "fingerprint", "/dev/esfp0 (Egis ET5xx), ioctls 'k' 0 (IOC_MESSAGE 32B),3,8,18,19,20,22,23,26; /sys/class/fingerprint/fingerprint/{type_check,name,vendor,adm,bfs_values}; also probes /dev/vfsspi /dev/goodix_fp /dev/qbt2000_*",
  "ioctl scan V:/lib64/libbauthserver.so; strings; K49 drivers/fingerprint/et5xx.h:193", "no", "K49 drivers/fingerprint (SENSORS_ET5XX)", "F", "", "Needs Trustonic fingerprint TA too")
r(S, "touch HAL", "/sys/class/sec/tsp/{cmd,cmd_list,cmd_result,input/enabled}, /sys/class/sec/sec_epen/epen_gestures, /sys/class/sec/sec_touchkey/input/enabled", "V:/bin/hw/vendor.lineage.touch-service.samsung strings", "no", "K49 sec_ts y771 + sec_cmd, wacom", "C",
  "", "Touch input itself is plain evdev; the HAL only adds gestures/glove")
r(S, "input generic", "/dev/input/event*, /dev/uinput (fingerprint HAL), /dev/uhid (sensors/fp groups)", "V:/etc/ueventd.rc; V:/bin/hw/android.hardware.biometrics.fingerprint-service.samsung strings", "yes", "input core", "B",
  "ACFG: INPUT_UINPUT=y UHID=y", "Touchscreen driver itself is feature-level but usability-critical")

# ---------------------------------------------------------------- power / thermal / health / lights / vibrator
S = "power"
r(S, "power HAL (libperfmgr)", "nodes: /sys/kernel/gpu/gpu_{max,min}_clock, /dev/cpu_dma_latency (44/100), /sys/devices/system/cpu/cpu0/cpufreq/scaling_min_freq (0/1456000), /sys/class/sec/tsp/cmd (aod_enable); hints AUDIO_LAUNCH CAMERA_* EXPENSIVE_RENDERING INTERACTION LAUNCH SUSTAINED_PERFORMANCE DOUBLE_TAP_TO_WAKE; /proc/vendor_sched/* (Pixel, absent today)",
  "V:/etc/powerhint.json; V:/bin/hw/android.hardware.power-service.pixel-libperfmgr strings", "cpufreq + cpu_dma_latency yes", "Exynos ACME cpufreq, gpex", "F",
  "integration branch cpufreq (HW-verified q15)", "Missing nodes => libperfmgr errors only")
r(S, "Samsung CPU knobs", "/sys/power/cpufreq_{min,max}_limit, cpufreq_table, /sys/power/cpuhotplug/*, /sys/kernel/ehmp/*, /sys/kernel/ert/*, /dev/cpu_online_{min,max}, /dev/cluster{0,1,2}_freq_{min,max}, /dev/{bus,device,mfc,npu}_throughput(_max), /dev/gpu_freq_{min,max}, cpufreq/interactive/* (ueventd)",
  "V:/etc/init/init.samsungexynos9810.rc; V:/etc/ueventd.rc; V:/etc/selinux/vendor_file_contexts (pm_qos_device); V:/lib64/libbauthserver.so (cpufreq_max_limit)", "no", "K49 exynos cpufreq/cpuhotplug/EMS/pm_qos misc", "C")
r(S, "thermal HAL", "thermal zones by type: BIG, LITTLE, G3D, ISP, ac, battery; cooling devices: thermal-cpufreq-0, thermal-cpufreq-1, thermal-gpufreq-0, thermal-isp-0; /dev/thermal/{tz,cdev}-by-name symlinks; /sys/class/powercap, /sys/bus/iio/devices",
  "V:/etc/thermal_info_config.json; V:/bin/thermal_symlinks; V:/etc/init/pixel-thermal-symlinks.rc, android.hardware.thermal-service.pixel.rc", "thermal core yes; names differ (upstream cpufreq cooling = cpufreq-cpu0/cpufreq-cpu4)",
  "K49 exynos_tmu + exynos cpufreq/gpu cooling", "F", "acpm-tmu branch: 4 TMU zones verified (zone type names not checked here)",
  "HAL only starts after vendor.thermal.link_ready=1; zone/cdev type strings must match or edit the JSON")
r(S, "health HAL", "/sys/class/power_supply/* scanned by healthd; names on 4.9: battery, usb, ac, wireless, ps, otg, max77705-charger, max77705-fuelgauge, mfc-charger; extras /sys/class/power_supply/battery/batt_slate_mode, /sys/class/sec/switch/afc_disable; BPF uevent filter /sys/fs/bpf/vendor/prog_filterPowerSupplyEvents_skfilter_power_supply; /sys/power/{state,wakeup_count}",
  "V:/bin/hw/android.hardware.health-service.samsung, vendor.lineage.health-service.default strings; K49 drivers/battery_v2 (.name strings); genfscon max77705-* paths", "power_supply core yes; MAX77705/sec_battery no", "K49 drivers/battery_v2", "F", "",
  "No battery => framework sees no battery (boots, wrong state). Charge control writes are a hazard (ROADMAP risk 6)")
r(S, "battery rc nodes", "/sys/class/power_supply/battery/* (238 chown/chmod lines), /dev/batt_misc, /dev/ccic_misc", "V:/etc/init/init.samsungexynos9810.rc", "no", "battery_v2, ccic", "C")
r(S, "lights HAL", "/sys/class/backlight/panel/{brightness,max_brightness}, /sys/class/sec/led/led_blink, /sys/class/sec/sec_touchkey/brightness, /sys/class/misc/backlightnotification/notification_led; rc also /sys/class/leds/led_{r,g,b}/*",
  "V:/bin/hw/android.hardware.light-service.samsung strings; V:/etc/init/init.samsungexynos9810.rc", "backlight/leds class yes", "K49 panel, LEDS_MAX77705_RGB", "F")
r(S, "vibrator HAL", "/sys/class/timed_output/vibrator/{enable,intensity,cp_trigger_index}; alternative path /sys/class/sec_vib_inputff/control/use_sep_index + /dev/input FF effects",
  "V:/bin/hw/android.hardware.vibrator-service.samsung strings; V:/etc/init/android.hardware.vibrator-service.samsung.rc", "timed_output removed from mainline; input FF yes",
  "K49 drivers/motor (MOTOR_DRV_MAX77705) + timed_output", "F", "", "Cheapest 6.12 route: input FF device + sec_vib_inputff control node (GUESS: HAL selects FF when that node exists)", guess=True)
r(S, "suspend", "/sys/power/state, /sys/power/wakeup_count, pm_freeze_timeout=1000", "V:/bin/hw/android.hardware.health-service.samsung strings; V:/etc/init/init.samsungexynos9810.rc:505", "yes", "PM core", "F", "ACFG: PM_WAKELOCKS=y")
r(S, "RTC", "/dev/rtc0 (s2mps18-rtc, hctosys)", "S:/system/etc/ueventd.rc:67; genfscon /devices/platform/141c0000.speedy/i2c-15/15-0000/s2mps18-rtc/rtc/rtc0/hctosys", "RTC class yes; s2mps18 RTC no (s2mps rtc covers 13/14/15 only, GUESS)", "K49 s2mps18 rtc", "F", "", "", guess=True)

# ---------------------------------------------------------------- camera / media
S = "camera-media"
r(S, "camera V4L2 nodes", "/dev/video{0-3,101-225}, /dev/v4l-subdev{0-21,32}, /dev/media{0,1,2}; opened by number through libexynosv4l2 (name lookup /sys/class/video4linux/video%d/name)",
  "V:/etc/ueventd.rc; V:/lib64/libexynosv4l2.so, libexynoscamera3.so strings", "V4L2 core yes; FIMC-IS2 no", "K49 drivers/media/platform/exynos/fimc-is2", "F", "none", "Fixed video_device numbers matter (ueventd perms per number)")
r(S, "camera sysfs", "/sys/class/camera/{rear,front,ois,flash,secure}/* (camfw, sensorid, mtf_exif, awb, fw_update, rear_flash...), /sys/devices/svc/Camera/*, /sys/kernel/mm/vmscan/mem_boost_mode", "V:/lib64/libexynoscamera3.so, V:/lib64/hw/camera.exynos9810.so strings", "no", "fimc-is2 sysfs", "F")
r(S, "camera firmware", "fimc_is_lib.bin, fimc_is_rta_2l3_3h1.bin, setfile_{2l3,3h1,3m3}.bin, ois_fw_{dom,sec}.bin, HFD*, score/, iva20_rt-lhotse.bin", "V:/firmware listing", "fw loader", "fimc-is2, score, iva", "F")
r(S, "camera aux", "/dev/iva_ctl, /dev/vertex0/1, /dev/score", "V:/lib64/libiva_android_hal.so strings; V:/etc/ueventd.rc", "no", "K49 iva/score/vertex", "C")
r(S, "MFC codec", "/dev/video6-11 (mediacodec:mediadrm), firmware mfc_fw.bin; OMX via libExynosOMX + libexynosv4l2", "V:/etc/ueventd.rc; V:/firmware/mfc_fw.bin; genfscon /devices/platform/178d0000.mfc0/video4linux/video{6,7,8}/name", "s5p-mfc upstream stops at v10 (GUESS no v12)", "K49 VIDEO_EXYNOS_MFC", "F", "", "", guess=True)
r(S, "JPEG / misc m2m", "/dev/video12,13 (libhwjpeg, SMFC), /dev/m2m1shot_jpeg, /dev/video55, /dev/tsmux, /dev/repeater, /dev/hdcp2, /dev/media2", "V:/lib64/libhwjpeg.so strings; V:/etc/ueventd.rc", "no", "K49 SMFC, repeater, tsmux", "C")

# ---------------------------------------------------------------- misc Samsung
S = "samsung-misc"
r(S, "/sys/class/sec/*", "ap_pmic, ccic, disp_pmic, gps, hdm, led, mmc, rtc, sdcard, sec_abc, sec_debug, sec_epen, sec_hw_param, sec_key, sec_touchkey, switch, tsp, sensorhub, ... (rc chown targets)", "RAW/rc-paths.txt", "no", "K49 drivers/staging/samsung (sec_class)", "C",
  "", "init chown/chmod/write on a missing path only logs an error; only nodes read by HALs (tsp/cmd, switch/afc_disable, led/led_blink, sec_key/*) matter")
r(S, "sec_debug / proc", "/proc/{reset_reason,reset_summary,auto_comment,last_kmsg,store_lastkmsg,...}, /sys/module/sec_debug/parameters/*", "V:/etc/init/init.samsungexynos9810.rc", "no", "K49 SEC_DEBUG*", "C")
r(S, "ecd firmware", "write /proc/ecd/load_firmware 1", "V:/etc/init/init.samsungexynos9810.rc:957", "no", "K49 ecd", "C")
r(S, "clat sysfs", "/sys/kernel/clat/{xlat_plat,xlat_addrs,xlat_v4_addrs}", "V:/etc/init/init.samsungexynos9810.rc", "no", "Samsung clat", "C")

# ---------------------------------------------------------------- policy
S = "sepolicy"
r(S, "file_contexts /dev", "84 /dev entries (e.g. /dev/mali[0-9] gpu_device, /dev/g2d graphics_device, /dev/mobicore* tee_device, /dev/umts_* vendor_radio_device, /dev/ssp_sensorhub ssp_device, pm_qos misc nodes)", "RAW/sepolicy-dev-contexts.txt (V:/etc/selinux/vendor_file_contexts + O:/etc/selinux/odm_file_contexts)", "n/a", "n/a", "F",
  "", "Node names must match for enforcing mode; ODM carries precompiled_sepolicy")
r(S, "genfscon sysfs", "115 path-based genfscon labels (decon vsync, panel_drv@001 backlight, max77705 power_supply, 13ac0000.spi iio, s2mps18-rtc, ...)", "RAW/sepolicy-genfscon.txt (V:/etc/selinux/vendor_sepolicy.cil)", "n/a", "n/a", "F",
  "", "In enforcing mode a renamed sysfs path loses its label -> HAL denials. RECOVERY cmdline uses androidboot.selinux=permissive; BOOT not inspected")
r(S, "ueventd perms", "V:/etc/ueventd.rc node list (mali0 0666, ion 0666, g2d, video*, mobicore*, umts*, bbd*, ssp*, iio)", "V:/etc/ueventd.rc", "n/a", "n/a", "F")

def main(out):
    os.makedirs(out, exist_ok=True)
    meta = {
        "title": "Kernel ABI expected by LineageOS 23 vendor/odm/system on SM-N960F (crownlte)",
        "generated": "2026-11-04",
        "images": {"VENDOR": "backups-20261103/partitions/VENDOR.img", "ODM": "ODM.img", "SYSTEM": "SYSTEM.img",
                   "build": "lineage_crownlte 16 BP4A.251205.006 (V:/build.prop ro.vendor.build.fingerprint)"},
        "priority_legend": {"B": "boot-blocking", "F": "feature-blocking", "C": "cosmetic"},
        "citation_prefixes": {"V:": "VENDOR.img (/vendor)", "O:": "ODM.img (/odm)", "S:": "SYSTEM.img (system-as-root)",
                               "K49": "/root/crownlte-hid-kernel", "A": "/mnt/ssd/host-builds/kernel-upgrade/android-config",
                               "ACFG": "build/android-config-clean-6.12.93/.config", "G": "/root/kernel-upgrade/graphics-cap-6.12.93",
                               "RAW": "roadmap/vendor-abi/raw/"},
    }
    with open(os.path.join(out, "vendor-abi.json"), "w") as f:
        json.dump({"meta": meta, "rows": ROWS}, f, indent=1)
    lines = []
    cur = None
    def esc(s): return s.replace("|", "\\|").replace("\n", " ")
    for row in ROWS:
        if row["subsystem"] != cur:
            cur = row["subsystem"]
            lines.append(f"\n### {cur}\n")
            lines.append("| Pri | Item | Exact path / ioctl / version | Evidence (in image) | Stock 6.12 | 4.9 provider | 6.12 port | Notes |")
            lines.append("|---|---|---|---|---|---|---|---|")
        g = " **GUESS**" if row["guess"] else ""
        lines.append("| {} | {} | {} | {} | {} | {} | {} | {}{} |".format(
            row["priority"], esc(row["item"]), esc(row["value"]), esc(row["evidence"]), esc(row["stock_6_12"]),
            esc(row["provider_4_9"]), esc(row["port_6_12"]), esc(row["note"]), g))
    with open(os.path.join(out, "vendor-abi-table.md"), "w") as f:
        f.write("\n".join(lines) + "\n")
    print(len(ROWS), "rows;", sum(1 for x in ROWS if x["priority"] == "B"), "boot-blocking")

if __name__ == "__main__":
    main(sys.argv[1])
