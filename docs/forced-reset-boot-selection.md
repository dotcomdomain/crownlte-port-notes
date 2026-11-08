# Forced reset out of a hung RECOVERY diagnostic: why it re-enters RECOVERY, and the fix

Date: 2026-11-04. This was analysis and build only; the phone was not touched.
Source: sboot from the boot LUN `sdb` (sha256 09d0dd96..., N960FXXU9FVG2), opened through
`watchdog-research/sdb-analysis.elf` (raw offset = VA) in Ghidra 12.1.4 (REA) and llvm-objdump-18.
4.9 source: /root/crownlte-hid-kernel. PARAM/MISC: backups-20261103/partitions (read-only).

Tags: **[C]** = confirmed (code read or measured). **[I]** = inferred.

## TL;DR

- sboot does **not** choose the boot partition from INFORM3. It copies the INFORM3 mode into the
  **PARAM env `REBOOT_MODE` (index 0)**, saves that to flash and clears INFORM3. The boot decision
  then reads only `REBOOT_MODE`: 4 means RECOVERY. [C]
- `adb reboot recovery` on 4.9 therefore leaves `REBOOT_MODE = 4` stored in PARAM. It stays 4 until
  a reset arrives at sboot still carrying INFORM3 = 0x12345670. Only then does sboot write 0
  back. [C]
- INFORM survives the PMU software reset. That is why the diag kernel's own 120 s restart
  (INFORM normal, then SWRESET) returns to Lineage. [C, q19]
- The Power+VolDown force reset does not deliver INFORM3 to sboot. With DEBUG_LEVEL LOW, sboot
  programs the PMIC manual reset as a cold cycle; the observed result was RST_STAT = PINRESET
  0x10000 and reason MPON. sboot sees `REBOOT_MODE = 4` and boots RECOVERY again, which runs the
  diag image again. [C behaviour, q16/q20; I mechanism]
- So 3a3284e's INFORM write is correct but can never help a forced reset. Its commit message
  premise, "sboot … does not clear them", is wrong.
- **Fix (volatile-only), branch `crownlte-normal-inform-v2`, commit `bbea959`:** the shim still
  writes the normal INFORM2/3 values. It then checks how this RECOVERY boot was entered. If
  RST_STAT bit 29 (SWRESET) is **clear** (force reset, cold power-on or key combo), it does not
  start the diag kernel. It writes PMU SWRESET instead. sboot then sees INFORM3 = 0x12345670, sets
  `REBOOT_MODE` to 0 through its own normal path and boots Lineage. The bounce is itself a
  software reset, so it can run at most once per entry. Built; not packed or flashed.

## 1. How sboot chooses the boot target

### 1.1 Entry point: `mach_board_main` @ 0x856e8 [C]

Order of the relevant steps:

1. `iVar8 = INFORM2 (0x14060808)` is read at entry; the 0xb91c case is not relevant here.
2. TMU calibration, PMIC thermal-trip report, display and fuel-gauge init.
3. **`s5p_check_reboot_mode` @ 0xd3a88** (call at 0x85a60). Details in 1.2.
4. `tbnz RST_STAT(0x14060404), #16` (0x85a70). On PINRESET it runs the OCP/key info check (0x85d78 →
   `set_ocp_info` 0x89eac). This does not select a boot mode.
5. **`s5p_check_upload` @ 0xd6e80**: upload (ramdump) decision. With LOW + FORCE_UPLOAD 0 it
   returns 0. It also calls **`set_mrstb` 0x85628** (see 1.4).
6. **`s5p_check_download` @ 0xd53fc**: `REBOOT_MODE == 1 or 0xd` → download, and REBOOT_MODE is
   reset to 0. Also SUD (0xabcf….) and the key-combo download flag `_DAT_00443020`.
7. If not download: `get_param(4) != 0` → download (0x85d30). Otherwise `get_param(0)` (REBOOT_MODE):
   - `== 4` → unless FMM lock (`FUN_d31b4`), set board flag `+0x15c |= 0x80000` (0x86258) and
     print "recovery set!". **This is the only normal-path recovery selector.**
   - `== 6` → FOTA_BL: `set_param(0,0)`, bootloader update `FUN_90870`, reset.
   - other → normal BOOT.
8. `check_pm_status`: INFORM2 == 0x12345678 means "normal reset, do not enter LPM". For PINRESET plus
   PMIC info it prints "7 sec reset, continue". Otherwise LPM/charger and RTC-alarm logic. None of
   this changes RECOVERY vs BOOT.

The cmdline builder at 0x8a038 appends **" bootmode=2" iff flag 0x80000 is set** (`tbz w0,#19` at
0x8a2fc → 0x8a300). So `bootmode=2` = RECOVERY boot. [C]

Only two board-code sites set the recovery flag: 0x86258 (REBOOT_MODE == 4) and 0x89e58 in
`set_oneshot_recovery` @ 0x89e04, the key-combo path. That function also **writes**
`set_param(0, 4, save)` plus param 0x47 "BL:Recovery Mode Set by key". So the key combo is sticky
too. [C]

### 1.2 `s5p_check_reboot_mode` @ 0xd3a88: INFORM3 to PARAM [C]

```
v = INFORM3 (0x1406080c); print "INFORM3 = %x"
if ((v & 0xfffffff0) == 0x12345670 && get_param(0) != (v & 0xf)) {
    print "setting REBOOT_MODE to %d"; set_param(0, v & 0xf, NULL, save=1);  // PARAM write
    INFORM3 = 0; return;
}
0xabcd.... → DEBUG_LEVEL (+FORCE_UPLOAD), 0xabc4.... → FORCE_UPLOAD, 0xabce → SWITCH_SEL,
0xabcf → SUD, 0xabc3 → rtn_config, 0xabc5 → cp debug, 0xabc6 → user dram test; then INFORM3 = 0.
else "skip" (INFORM3 left as is).
```

`set_param` = 0x921cc. It stores into env int array 0x3fc890[idx]. If `save` is set, it rewrites the
0x1e300-byte env file (`adv-env.img` inside the PARAM ustar) via 0x91ed4. `get_param(idx)` =
`*FUN_925d8(idx)` (0x926f0). [C]

PARAM backup check: `PARAM.img` holds the ustar member `adv-env.img`, int array at image offset
**0x400**: `[0]=4 (REBOOT_MODE=recovery), [1]=3, [2]=0x4f4c (DEBUG_LEVEL LOW), [10]=0x55ff,
[0x1c]=0 (FORCE_UPLOAD)`. The backup was taken while the phone was in Lineage recovery, and it
matches this model. [C]

### 1.3 Who writes REBOOT_MODE (`set_param(0, …)`) [C, static scan of all direct `bl 0x921cc`]

| Site | Value | Trigger |
|---|---|---|
| 0xd3bfc `s5p_check_reboot_mode` | INFORM3 & 0xf | INFORM3 = 0x1234567X at boot |
| 0x89e60 `set_oneshot_recovery` | 4 | recovery key combo |
| 0x8fc70 (Odin handler 0x8edec) | 4 | **Odin flash of partition "CACHE"** ("set recovery mode for next time boot") |
| 0x85e68 | 0 | REBOOT_MODE == 6 (FOTA_BL) |
| 0xd54bc `s5p_check_download` | 0 | REBOOT_MODE == 1/0xd (download) |
| 0x8c01c | 0 | "recovery kernel rp check fail" → "set to bootmode as normal to update recovery" |

No boot counter or "previous boot failed → recovery" rule exists in this path. The only memory is
REBOOT_MODE. The "Recovery counter" strings at 0xc2c14 belong to the EM/engineering-mode module and
are not used for boot selection. [C for mach_board_main; I that nothing elsewhere overrides it]

Odin flashing RECOVERY alone does not set REBOOT_MODE. That is why after the Odin restore the phone
still came up in recovery: REBOOT_MODE was already 4 from the `adb reboot recovery` that started the
trial. [C for the CACHE site; I that no other Odin site writes it. 0x8f034/0x8f74c/0x8f7f0 are
RECOVERY/BOOT image checks, not REBOOT_MODE writes]

### 1.4 Inputs and reset types

| Input | Used for | Evidence |
|---|---|---|
| PMU INFORM3 0x1406080c | synced into PARAM REBOOT_MODE, then cleared | 0xd3a88 [C] |
| PMU INFORM2 0x14060808 | 0x12345678 = "normal reset, skip LPM"; upload magics | 0x856e8, 0xd6e80 [C] |
| PARAM REBOOT_MODE (env[0]) | **the** RECOVERY/BOOT/download selector | 0x85e40 [C] |
| PARAM env[4] | nonzero → download | 0x85d30 [C] |
| PARAM DEBUG_LEVEL/FORCE_UPLOAD | upload policy, PMIC manual-reset mode | 0xd6e80, 0x85628 [C] |
| DRAM 0x80000000 magic | upload (0x66262564), RTC-alarm path (0x66265999), **not** partition choice | 0xd6e80, 0x856e8 [C] |
| MISC BCB | **not referenced**: no "misc"/"MISC"/"boot-recovery" string in sboot | string scan [C]; the backup BCB "boot-recovery" is Lineage recovery's own and only matters to recovery |
| keys | recovery combo → `set_oneshot_recovery` (sticky); download combo | 0x89e04 [C]; its caller is indirect and was not resolved |
| RST_STAT 0x14060404 | bit16 PINRESET → OCP/key info, "7 sec reset"; cmdline `sec_debug.rst_stat` | 0x85a70 [C] |

RST_STAT bits (4.9 `drivers/staging/samsung/sec_debug_reset_reason.c:34-43`): bit16 PINRESET,
bit23 CLUSTER1_WDTRESET, bit24 CLUSTER0_WDTRESET, bit29 SWRESET. `sec_debug.reset_reason` codes:
`include/linux/sec_debug.h:157-169`, with 5 = RR_M "MPON" and 7 = RR_R "RPON".

Which reset keeps INFORM2/3 so that sboot sees them:

- **SW reset (PMU 0x14060400 = 1)**: kept. [C] The 6.12 diag restart writes INFORM normal + SWRESET
  (integration 6796f94fd `drivers/soc/samsung/exynos9810-diagnostic-restart.c:108-111,181`); q19
  came back to Lineage with rst_stat 0x20000000. sboot's own reset (0x7e1ac, used at 0xd47a4 after
  writing INFORM3 at 0xd4550) relies on the same behaviour.
- **WDT reset**: kept by design. sboot reads INFORM2 "watchdog magic" after a WDT reset
  (0xd7974). [I from code; not observed, because the q20 WDT never fired]
- **Manual Power+VolDown reset with DEBUG_LEVEL LOW**: **not delivered**. [C behaviour]
  - q20: the shim wrote INFORM3 = 0x12345670 (verified in q17), then the kernel hung and the forced
    reset still booted RECOVERY. If INFORM3 had reached 0xd3a88, REBOOT_MODE would have been reset
    to 0 and Lineage would have booted.
  - Mechanism [I]: `set_mrstb` 0x85628, which `s5p_check_upload` calls on every boot, programs
    S2MPS18 CTRL1 (0x14) / CTRL3 (0x16). LOW gives CTRL1 = 0x14 (manual reset enabled, bit4,
    per 4.9 `drivers/regulator/s2mps18.c:1043-1073`) and CTRL3 bit4 = 0. MID/HIGH gives CTRL3 = 0x14
    and CTRL1 = 0x1f. CTRL3 bit4 is most likely the "manual reset = warm reset" select. With LOW the
    manual reset is therefore a PMIC power cycle, which matches `sec_debug.reset_reason=5` (MPON)
    and `rst_stat=0x10000` (PINRESET only) seen after the post-Odin forced reset.
- **Cold power-on**: not kept. [I, standard]

## 2. What 4.9 writes on `reboot recovery`

`arm_pm_restart = sec_reboot` (`drivers/staging/samsung/sec_reboot.c:221-226`):

- `sec_reboot.c:156`: INFORM2 = 0x12345678 (SEC_POWER_RESET, "LPM mode prevention").
- `sec_reboot.c:164-165`: INFORM3 = 0x12345674 (SEC_RESET_REASON_RECOVERY; prefix defined at
  `:43`, enum at `:44-62`). A plain reboot writes 0x12345670 at `:208/:210`.
- Then `mach_restart(REBOOT_SOFT, "sw reset")` → `exynos_reboot` (`drivers/soc/samsung/exynos-reboot.c:263`):
  `exynos_acpm_reboot()`, DFD dump-GPR off if RESET_SEQUENCER_CONFIGURATION bit0
  (`:293-297`), then `SWRESET = 1` (`:305`). SYSIP_DAT0 is not written because cmd is "sw reset"
  (`:276-285`).
- The kernel writes nothing persistent: no PARAM, no MISC. [C]. **The persistence comes from
  sboot**, which turns INFORM3 = 0x12345674 into PARAM REBOOT_MODE = 4 at 0xd3bfc.

## 3. Why 3a3284e was not enough, and the fix

3a3284e assumed sboot boots from INFORM3 and never clears it. In fact sboot clears INFORM3 on
the trial entry and persists the mode to PARAM. The shim's later INFORM3 = normal write only acts
if it reaches sboot. A software reset delivers it; a LOW-level manual reset does not.

**Is there a purely volatile selector that survives the force reset?** No, as far as this analysis
found. sboot consults DRAM only for upload and RTC-alarm decisions; the MISC BCB is never read;
keys can only set recovery or download; INFORM does not survive. The remaining selector is
persistent PARAM REBOOT_MODE.

The fix does not need one. After a forced reset, sboot runs **our shim again**, because RECOVERY
still holds the diag image. The shim can detect the entry type and bounce, and sboot then performs
its own REBOOT_MODE 4→0 write. That is the same path every normal Android reboot from recovery
takes, and the same path the q19 diag restart already relies on.

### Commit bbea9590ba9bf57c673cee723e4e6817c08cdea7 (branch `crownlte-normal-inform-v2` off 3a3284e)

Worktree: `/mnt/ssd/host-builds/kernel-upgrade/shim-normal-inform-v2`; file
`board/samsung/board-crownlte.c`, `crownlte_normal_boot_reason()`:

1. Read RST_STAT (0x14060404).
2. Write INFORM2 = 0x12345678 and INFORM3 = 0x12345670, as before.
3. If `RST_STAT & BIT(29)` (SWRESET), the boot was entered by `reboot recovery` (or by the bounce
   itself). Return 0 and boot the diag kernel as before.
4. Else, if DFD dump is armed (0x14060500 bit0; LOW sets 0xf000e0, so it is clear), or an INFORM
   readback fails, return 0 and use the old behaviour.
5. Else `dsb sy; SWRESET (0x14060400) = 1; dsb sy`, then a bounded spin (~0.5 s) and return 0, so a
   reset that does not happen degrades to the old behaviour instead of hanging.

The bounce sequence matches sboot's own reset (0x7e1ac). That reset is preceded by cache/MMU off,
UFS close and DFD off (0xd47a4); the shim has no outstanding UFS I/O at that point, and its caches
and MMU state are irrelevant to a device-register write. [I: low risk, same as any panic or WDT
reset with UFS idle]

Expected sequences [I, not tested on the phone]:
- Trial start: `adb reboot recovery` → SWRESET entry → no bounce → diag runs (unchanged).
- Diag hangs → Power+VolDown → PINRESET → sboot: REBOOT_MODE 4 → RECOVERY → **shim bounce** →
  SWRESET with INFORM3 normal → sboot: "setting REBOOT_MODE to 0" → BOOT → Lineage
  (`sec_debug.rst_stat=0x20000000`, no `bootmode=2`).
- PARAM save failure, should it happen: the next entry is SWRESET → no second bounce → diag boots.
  No loop.
- Diag panics or restarts normally: unchanged (SWRESET with INFORM normal → Lineage).

Side effects to know:
- With a diag image in RECOVERY, *any* non-SW entry bounces to Lineage. That includes the
  recovery key combo, a cold power-on, and a forced reset after an Odin flash of the diag image
  with AutoReboot off. Start trials only with `adb reboot recovery` from Lineage. Download mode and
  Odin restore are unaffected, because sboot handles them before any image loads.
- The bounce causes sboot to write PARAM (REBOOT_MODE 4→0). That is sboot's own designed write;
  every normal reboot out of recovery and the q19 diag restart already cause it. The loader writes
  no storage.
- Never flash CACHE with Odin: sboot sets REBOOT_MODE = 4 on it (0x8fc64).

Build (compile check only): `build/shim-normal-inform-v2-out/uniLoader` sha256 c8b06b23... (embeds
integration-android Image, delayed-ufs-diag DTB and initramfs 7703405b only to satisfy
EMBED_LINUX). The shim config hash is f2b33db7, the same as the packer's. Disassembly of
`crownlte_normal_boot_reason` was checked. Real images: `SHIM=bbea9590ba9bf57c673cee723e4e6817c08cdea7
pack-acpm-q.py …`.

### Verification plan (when the phone is available, Odin ready)

1. Pack any known-good diag with SHIM=bbea959, then `adb reboot recovery`. Expect the trial to run
   normally (no bounce).
2. During the trial, before the 120 s restart, press Power+VolDown. Expect Lineage after one extra
   quick reset; the Note9 logo shows twice, with no diag serial in between. In Lineage:
   `/proc/cmdline` should have `sec_debug.rst_stat=0x20000000` and no `bootmode=2`. For a read-only
   check of PARAM, `dd if=/dev/block/by-name/param bs=4 skip=256 count=1 | xxd` should give
   REBOOT_MODE 0.
3. If step 2 lands in the diag again (bounce did not happen), fall back to Odin bright-recovery.tar
   as before.

### Not done / open
- CTRL3 bit4 semantics (S2MPS18 datasheet) are inferred. The fix does not depend on them.
- Making the force reset a warm reset (S2MPS18 CTRL3 bit4 = 1) was rejected: it needs a PMIC write
  (blocked by the ACPM gate), and it is unknown whether XnRESET keeps INFORM.
- The q20 WDT that never fired remains a separate open issue. A working WDT would return to Lineage
  directly with INFORM intact, without needing the bounce.
- The caller of `set_oneshot_recovery` (indirect) and the meaning of PARAM env[4] were not resolved.
