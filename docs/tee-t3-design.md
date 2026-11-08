# TEE T3 design: keymaster and gatekeeper TAs on the 6.12 ROM-RAM boot

Status: research and design only. Nothing in this document has been run. Written 2026-11-06
from static analysis of the Lineage 23 vendor image mounted at
`/mnt/ssd/host-builds/kernel-upgrade/rom-ram/testroot` (REA/Ghidra 12.1.4, llvm-objdump-18),
the 6.12 TEE driver on branch `crownlte-tee-3` (worktree `/mnt/ssd/host-builds/kernel-upgrade/tee`,
HEAD 566ee45af), the 4.9 source in `/root/crownlte-hid-kernel`, and the rom-ram runner on
branch `tee-t2` (3259275). No keys, Knox/DRM material or TEE storage contents were read.

How sure each claim is: **[obs]** = read directly from code or decompiled code;
**[inf]** = inferred from strings, literal pools or AOSP behaviour, not proven.

## 0. Findings that change the plan

1. **Both HALs use `mcOpenTrustlet`, not `mcOpenSession`.** Each HAL reads its TA blob from
   `/vendor/app/mcRegistry` and hands the bytes to the kernel itself. Keymaster uses
   `ffffffff00000000000000000000003e.tlbin` and gatekeeper uses
   `08130000000000000000000000000000.tlbin`. The current guard refuses every `open-trustlet`
   (it calls `mc_session_guard(NULL, ...)`, client.c:698), and the T2 seccomp refuses
   `OPEN_TRUSTLET 0xc0284d01`. T3 therefore needs a new guard that checks the blob itself
   (UUID + SHA-256 + service type, checked on the kernel's own copy; see §6). Allow-listing UUIDs
   for `OPEN_SESSION` does nothing for these two HALs. **[obs]**
2. **The gatekeeper TA keeps its failure records in an RPMB-backed secure FS.** It goes through
   the CryptoManager secure driver (`ffffffffd00000000000000000000016`, driver id 0x40006).
   Both CryptoManager and the sec driver `FFFFFFFF000000000000000000000001.drbin` hold Samsung's
   SWd **SRPMB** client. SRPMB needs the NWd `ufs-srpmb` proxy (4.9
   `drivers/scsi/scsi_srpmb.c`), and 6.12 does not have it. Two consequences:
   (a) **6.12 cannot write RPMB at all**, a strong safety property for T3;
   (b) **gatekeeper `verify()` probably cannot succeed on 6.12** until that proxy is ported,
   which makes it a T4 blocker. T3b is designed to confirm (b) without writing anything. **[obs]** for the strings, the driver id and the
   4.9 proxy; **[inf]** for "verify fails without it".
3. **The gatekeeper HAL rewrites `/efs/TEE/gk_context_<uid>` and `gk_context_sub_<uid>` after
   every `verify()`**, including failed ones and even when the TA could not be reached.
   `enroll()` writes them only on success. No HAL verify is read-only. **[obs]**
4. **The keymaster TA is configured from exactly two properties:** `ro.build.version.release`
   (→ OS_VERSION) and `ro.build.version.security_patch` (→ OS_PATCHLEVEL), sent at HAL start-up
   (`configure`). The Samsung helper copies the same two values into vendor tags
   0x30000325/0x30000326. `ro.build.type` only decides whether a TIMA measurement is attached
   (`user` builds only; this ROM is `userdebug`, so none is sent). **[obs]**
5. The keymaster TA keeps no NWd-visible storage (`delete_all_keys` is UNIMPLEMENTED) and the
   TA has no RPMB/storage strings. Key blobs are stateless. Its ICCC/Knox code is the only part
   with "write" strings ("Error writing Trustboot flag", "failed to write iccc lock").
   **[obs]** strings, **[inf]** semantics.

## 1. Keymaster (`android.hardware.keymaster@3.0-service`)

### Stack and init

- rc: `/vendor/etc/init/android.hardware.keymaster@3.0-service.rc` (user nobody, group drmrpc,
  class early_hal) is overridden by `keymaster@3.0-override.rc` (user **system**, group drmrpc).
- The service binary links no keymaster code; it loads `android.hardware.keymaster@3.0-impl.so`
  (passthrough, `HIDL_FETCH_IKeymasterDevice`). That opens hw module class `keystore` →
  `ro.hardware.keystore=mdfpp` (vendor/build.prop:74) → `/vendor/lib64/hw/keystore.mdfpp.so`
  (module "Keymaster MDFPP Mobicore HAL") → `libkeymaster2_mdfpp.so` →
  `libkeymaster_helper_vendor.so` → `libMcClient.so` → `/dev/mobicore-user`.
- impl → `keymaster::ng::CreateKeymasterDevice(keymaster2_device*)`
  (libkeymaster3device.so 0x11f640) calls **`ConfigureDevice(dev)` first**, then wraps the
  device in `Keymaster2PassthroughContext` + `AndroidKeymaster`. If configure fails, the HAL
  returns nullptr. **[obs]**
- `ConfigureDevice` (0x11f8a0) builds {0x300002c1 OS_VERSION, 0x300002c2 OS_PATCHLEVEL} and calls
  `dev->configure` (offset 0x88). `GetOsVersion`/`GetOsPatchlevel` read
  `ro.build.version.release` with regex `^([0-9]{1,2})(\.([0-9]{1,2}))?(\.([0-9]{1,2}))?` and
  `ro.build.version.security_patch` with `^([0-9]{4})-([0-9]{2})-([0-9]{2})$` (strings in
  libkeymaster3device.so). **[obs]**

### What the HAL does before any client call (start-up order)

1. `device_open` (libkeymaster2_mdfpp.so 0x102860): fills the keymaster2 vtable, then calls
   `nwd_swd_open_connection` → `nwd_open_connection` (helper 0x10dd80; logs
   `[WRN]Open MDFPP keymaster version 2.3.10`) → `nwd_tz_open` (0x10d718):
   - polls `vendor.sys.mobicoredaemon.enable` for `"true"` up to 150 × 50 ms = 7.5 s;
   - `mcOpenDevice(0)`, retried up to 10 times;
   - `OpenSession` (0x10daf0): `get_file_content("/vendor/app/mcRegistry/ffffffff00000000000000000000003e.tlbin")`,
     then **`mcOpenTrustlet(&sess, spid=0xfffffffe, blob, len, tci, 0x10)`**;
   - `mcMap` of two shared buffers (in/out).
2. `configure` (0x102020) → `nwd_configure` (helper 0x10a490):
   - `ro.build.type`: if it is exactly `user`, reads `/vendor/tima_measurement_info`
     and attaches it. This build is `userdebug` (system/build.prop:47), so nothing is attached;
   - `nwd_km_KM_INDATA_pack(cmd 0xf, ...)` (0x109380): for cmd 0xf it also computes the
     Samsung `get_os_version_uint()` (ro.build.version.release) and `get_os_patchlevel_uint()`
     (ro.build.version.security_patch, YYYYMM), and adds tags 0x30000325/0x30000326 (and
     0x2c1/0x2c2 if the caller did not send them);
   - stores the returned 0x2c1/0x2c2 values and registers `keymaster_reconnect_cb`. That
     callback re-runs `nwd_configure` after a TA restart.
3. No other TA command at start-up. The HIDL `getHardwareFeatures` is answered in the NWd by
   `AndroidKeymaster` (no TA round trip). **[obs]** for 1–2; **[inf]** for 3 (AOSP ng code).

### Exact values sent today (from the image)

| property | value in testroot | sent as |
|---|---|---|
| `ro.build.version.release` | `16` (system/build.prop:39) | OS_VERSION = 160000 (AOSP) and 0x325 (Samsung) |
| `ro.build.version.security_patch` | `2026-08-01` (system/build.prop:42) | OS_PATCHLEVEL = 202608 and 0x326 |
| `ro.build.type` | `userdebug` | no TIMA measurement |
| `ro.vendor.build.security_patch` | `2023-02-01` | not sent to a KM2 device (only used by the soft/KM4+ path) |

Per-command extra tags (0x109380): cmds 2 and 4 get 0x600002bd CREATION_DATETIME
(CLOCK_REALTIME ms); cmd 8 gets 0x60000320.

**Key-upgrade rule (T4):** the TA checks key blobs against the configured version
(`km_check_key_os_version_patchlevel`, `km_upgrade_key`, "No valid flag_do_upgrade tag!" in the
TA). If the 6.12 boot sends a higher version or patchlevel than the keys were created or upgraded with,
`begin` returns KEY_REQUIRES_UPGRADE and vold/keystore2 rewrite the blobs. Before T4, compare the
two properties above on the running 4.9 Lineage with the container's values. They must be equal.
The container boots the same system image, but this is not yet checked against the phone.

### Transport

Legacy Kinibi TCI, not GP: `mcOpenDevice` / `mcOpenTrustlet` / `mcMap` / `mcNotify` /
`mcWaitNotification(…, 40000 ms)` / `mcGetSessionErrorCode` / `mcUnmap` / `mcCloseSession`
(helper imports). The TA is an MCLF v2.5 **system trustlet** (service_type 3, 3 instances).

### Secure drivers it needs

From driver-id literals in the TA's code (aligned literal pool):
TIMA 0x40003 ×2 (0x1fbc4, 0xb09fc), CryptoManager 0x40006 (0x20608), sec driver 0x40000 ×6
(0xb09d8…, in the ICCC/TIMA library block that TAs 04/30/45/70 and the TIMA driver share).
VaultKeeper 0x4002e: none. **[inf]**. Literal-pool evidence, not traced call by call.

## 2. Gatekeeper (`android.hardware.gatekeeper@1.0-service`)

- rc: user system, group system, class hal. impl `android.hardware.gatekeeper@1.0-impl.so` →
  `gatekeeper.exynos9810.so` (links libMcClient).
- `exynos_gk_open` (0x102368) sets only close/enroll/verify. **`delete_user` and
  `delete_all_users` are NULL** (offsets 0x88/0x90 = 0). The TA cannot be wiped through this HAL.
  **[obs]**
- No TA work at start-up. Every `enroll`/`verify` opens the device and the session, runs one
  command, and closes both again.

### TA and transport

`TEE_Open_Session` (0x102a20): reads `/vendor/app/mcRegistry/08130000000000000000000000000000.tlbin`,
then calls **`mcOpenTrustlet(&s, spid=0xfffffffd, blob, len, tci, 0x50)`**. The TA is an MCLF
v2.5 system trustlet with UUID `08130000000000000000000000000000`, built "Nov 17 2020". **[obs]**

TCI (0x50 bytes) for **verify** (cmd 2, `TEE_Verify` 0x102c78), u32 indices:
[0]=cmd, [1]=TA return, [4]=uid, [6..7]=challenge, [8]=WSM addr (enrolled handle at +0,
≤0x400), [9]=handle len, [10]=WSM+0x800 provided password, [11]=len, [12..13]=CLOCK_BOOTTIME ms,
[14]=WSM+0xc00 auth-token out, [15]=token len, [16]=retry timeout out, byte [17]=request_reenroll,
[18]=WSM+0x400 context (main 0x6c bytes at +0x400, sub 0x6c bytes at +0x46c). The WSM is 4 KiB
(`mcMap(…, 0x1000)`). `mcWaitNotification` has **no timeout** (0xffffffff). Enroll is cmd 1, with a
5 KiB WSM.

### Where the throttling state lives

- **NWd copy, written by the HAL:** `/efs/TEE/gk_context_<uid %x>` and
  `/efs/TEE/gk_context_sub_<uid %x>` (fallbacks `/mnt/vendor/efs/TEE/gk_context_%x`,
  `/mnt/vendor/efs/gk_context_sub_%x`; the fallback is chosen if `mkdir("/efs/TEE",0700)` fails
  with anything other than EEXIST). Each file is 0x6c bytes of a TA-wrapped object
  (`tlApiWrapObjectExt`; TA strings "Mainfile's/Subfile's tlApiUnwrapObjectExt fail").
- **SWd copy:** TA `../Code/sfs_storage.c`, "Failure record file cannot be opened. UID=%u",
  "SFS read/write success", "secureFS and kinibi partition read failed.". The TA calls the
  CryptoManager driver (0x40006) through its tlApi call-driver stub (disassembly:
  `ldr r0,=0x00040006; bl 0x274c`, and 0x274c jumps through the tlApi table slot 0x8c with
  r0=8) in a family of 0x24-byte command wrappers at 0x2bfe–0x2c56. The CryptoManager driver's only storage
  strings are RPMB ones ("RPMB: error in CryptoManager F/W", "RPMB: exceed maximum SMC retry count").
  The sec driver (…01.drbin) holds the SRPMB client itself ("[srpmb] read writecounter",
  "Initialized magic number to RPMB", "register_partition"). So the failure record is
  **RPMB-backed**. **[inf]**, strong. "kinibi partition" is most likely the Kinibi FSD2
  partition `Store_%1X.tf` in `/efs/TEE` (daemon default path); none exists on the phone today.

### Who writes the gk_context files, and when [obs]

- `verify()` → `TEE_Verify`: reads both files (missing → error log, buffer stays zero), runs
  the TA, then **always** opens both with `O_WRONLY|O_CREAT` (0x41, mode 0600, no O_TRUNC) and
  writes 0x6c bytes from WSM+0x400 to **both** (the sub file gets the main buffer), then `fsync`.
  This happens whether the TA succeeded, failed, or was never reached (`mcOpenDevice` failure still falls
  through to the writes). If either open fails, the HAL returns 1, so the HAL reports failure even
  when the TA accepted the password.
- `enroll()` → `TEE_Enroll` (0x102510): writes both files only if the TA returned 0.
- **Persistent effects of one verify on 4.9:** 2 small `/efs` writes by the HAL. In the TA,
  AOSP gatekeeper logic increments the failure record *before* checking and clears it on success,
  which means RPMB write(s) through SRPMB **[inf]**.
- **Read-only verify:** not possible through the HAL. It is only possible with a custom TCI
  client that skips the file writes, and even then the TA itself writes its failure record (RPMB)
  for throttled handles.

## 3. The four `-r` preloads

| file | MCLF type / driver id | what it is (strings) | needed by |
|---|---|---|---|
| `FFFFFFFF000000000000000000000001.drbin` | driver 0x40000, 6 threads | Exynos **sec driver**: SSS memcpy, HDCP keys, `sec_get_rtc_tick_time`, **SRPMB client** | KM (ICCC block) [inf]; GK only indirectly if CryptoManager's RPMB path goes through it |
| `ffffffffd0000000000000000000000a.tlbin` | driver 0x40003 | **TIMA** driver (`TIMADriverIPC_Handler`) | KM (ICCC/TIMA measurements) [inf] |
| `ffffffffd00000000000000000000016.tlbin` | driver 0x40006 | **CryptoManager** ("[KEYMAN]", SSS AES/ECC, RPMB via CM firmware) | GK [obs disasm], KM [inf] |
| `ffffffffd00000000000000000000045.drbin` | driver 0x4002e | **VaultKeeper** (reads OTP) | neither. Referenced only by TAs …45, …59 and driver …1a |

The four are loaded via `MC_ADMIN_IO_LOAD_DRIVER` by the daemon (`-r`). SHA-256 values are in
the Appendix.

## 4. Persistent side effects (keymaster), per call

| step | NWd files | SWd storage | RPMB |
|---|---|---|---|
| HAL start (open + `mcOpenTrustlet`) | none (reads TA blob) | none known | none |
| `configure` (cmd 0xf) | none (`user` build would read `/vendor/tima_measurement_info`) | ICCC init code exists in the TA ("Error writing Trustboot flag", "failed to write iccc lock", `Iccc_phys_write`); the target looks like boot-time secure memory, not storage [inf]. 4.9 does exactly this on every boot. | none possible on 6.12 (no proxy) |
| `getHardwareFeatures` | none, NWd only | none | none |
| `addRngEntropy` (cmd 1) | none | none known (mixes entropy into the TA DRBG) | none |
| `generateKey` (cmd 2) / `begin` | none in the HAL; the blob goes back to the caller, who stores it (keystore2 DB or vold `keymaster_key_blob`) | none: no storage/RPMB strings in the TA, `delete_all_keys` is UNIMPLEMENTED (0x1023f0 returns -100) | none |
| attestation / SAK paths | `write_blob` (fopen/fwrite) is called only from `validate_installed_key`, `KM_generate_sak` and two more attestation helpers; it reads/writes under `/efs/DAK/` | n/a | n/a |

- **RPMB:** the KM TA has no RPMB/SFS strings. Its drivers (sec/TIMA) contain SRPMB code. On
  6.12 SRPMB has no NWd proxy, so any RPMB request fails (4.9 proxy: `scsi_srpmb.c` registers WSM
  with `SMC_SRPMB_WSM` (line 348), takes the SWd IRQ (line 334), and issues SCSI SECURITY PROTOCOL
  IN/OUT (lines 104–207)). 6.12 has `SMC_SRPMB_WSM` only as a define in `include/linux/smc.h:74`,
  with no driver and no `samsung,ufs-srpmb` DT node.
  Does the TA *require* RPMB? The KM TA does not, as far as the strings show [inf]. The GK TA needs it for its failure records [inf].
- **TbStorage/FSD2:** the KM TA has no FSD2 use visible. FSD2 itself would write
  `/efs/TEE/Store_<x>.tf` (daemon default; no file exists on the phone).
- **Rollback-resistance counters:** none found. `rollback_resistant` in the TA is an attestation
  ASN.1 field name (same as AOSP `attestation_record.cpp`). The device has no rollback-resistant
  key storage [inf]. vold first asks for ROLLBACK_RESISTANCE and falls back when it is refused.
- **Helper failure path:** on a TA communication error, `nwd_KM_INDATA_pack` (0x10e160, string
  "[WRN]restart TA" at 0x10e3fc) closes and reopens the TA and re-runs configure. The helper also
  carries `ctl.start` / `compact_dump` / `init.svc.compact_dump` strings (a dump on error) [inf].
  The test client must not be able to set properties (§7).

## 5. Who would call keymaster/gatekeeper in a ROM-RAM boot if the HALs were live

The current container keeps both off: `keystore.mdfpp.so`, `libMcClient.so` (both ABIs),
`mobicore.rc` and the gatekeeper rc are emptied, and gatekeeper is dropped from the VINTF
manifest (`rom-ram/src/gen_payload.py` EMPTY/VINTF_DROP). The keymaster@3.0 service runs
**software-only** and km_compat uses it as the "TEE" device.

If the TEE keymaster were exposed container-wide, these would reach it:

| caller | when | TA traffic | creates keys / writes state |
|---|---|---|---|
| keymaster@3.0 HAL itself | service start | open + configure | no |
| keystore2 (km_compat → Keymaster3 wrapper) | start, every op | getHardwareInfo (NWd), addRngEntropy before key generation, earlyBootEnded (not on KM3) | generates keys for its own users; blobs in `/data/misc/keystore/persistent.sqlite` (tmpfs here) |
| vold `init_user0` on tmpfs `/data` | boot | generateKey (AES-256-GCM "key storage" key, first with ROLLBACK_RESISTANCE, then without) + begin/update/finish to wrap new DE keys | **yes, new keys** (FBE `fileencryption=aes-256-xts:aes-256-cts:v2+inlinecrypt_optimized`, no metadata encryption, fstab line 8). Blobs go to tmpfs. |
| LockSettingsService / SyntheticPasswordManager | user 0 init | generateKey/encrypt for the SP protector (no credential on tmpfs) | yes, keystore keys (tmpfs) |
| odsign | boot | generates/uses an RSA signing key via keystore2 | yes (tmpfs) |
| `vdc keymaster earlyBootEnded` | boot (backgrounded) | none on KM3 | no |
| gatekeeperd | only on enroll/verify; on a cold boot (`/data/misc/gatekeeper/.coldboot` missing) it calls `deleteAllUsers` | **NOT_IMPLEMENTED with this HAL** (NULL pointer) | none in the SWd; would write `.coldboot` on `/data` |
| gatekeeper HAL | only on enroll/verify | TA + `/efs/TEE/gk_context*` writes | yes, `/efs` (see §2) |

With a tmpfs `/data`, all created key blobs die with the RAM, and the KM TA keeps no state for
them. The persistent risks are the gatekeeper's `/efs` + RPMB writes and (T4) key upgrades of
real blobs. The keystore.mdfpp/libMcClient masks stay in place for T3.

## 6. Kernel guard extension (shared by T3a/T3b/T4)

Current state: `mc_session_guard(uuid, gp, what)` (main.c:710) checks a 16-byte UUID list from DT
`samsung,tee-session-allow`. `client_open_trustlet` (client.c:698) and admin
`LOAD_DRIVER`/`LOAD_CHECK` (admin.c:952/991) pass NULL, so they are always refused.

Facts the extension relies on [obs]:
- `tee_object_read()` (admin.c:597) copies the MCLF header, checks the magic, and copies the
  rest of the user blob into a vmalloc'd `struct tee_object {length, header_length, data[]}`
  (mcp.h:22). Only SP trustlets (service_type 2) get a container header (`header_length != 0`).
- What the SWd loads is this kernel copy: `client_add_session` maps `obj->data`/`obj->length`
  (client.c:753/818), and `mcp_open_session` takes the UUID from the copy's header
  (mcp.c:552). Checking the copy has no TOCTOU window.
- `load_driver()` (admin.c:696) calls `tee_object_read()`, then `client_add_session()`.
- `CONFIG_CRYPTO_LIB_SHA256=y` in the tee3 build (`sha256()` from `<crypto/sha2.h>`).

Proposed change (one commit on crownlte-tee-3):

```c
/* DT: samsung,tee-trustlet-allow = [uuid(16) sha256(32)]...  (open-trustlet, system TAs only)
 *     samsung,tee-driver-allow   = [uuid(16) sha256(32)]...  (admin LOAD_DRIVER, drivers only) */
int mc_blob_guard(const struct tee_object *obj, bool driver, const char *what)
{
	const struct mclf_header_v2 *h;
	u8 d[SHA256_DIGEST_SIZE];

	if (!mc_guard_on)
		return 0;
	if (obj->header_length || obj->length < sizeof(*h))
		goto refuse;                       /* SP trustlets / short blobs: never */
	h = (const void *)obj->data;
	if (driver ? h->service_type != SERVICE_TYPE_DRIVER
		   : h->service_type != SERVICE_TYPE_SYSTEM_TRUSTLET)
		goto refuse;
	sha256(obj->data, obj->length, d);
	/* match (uuid, digest) against the list for this kind; log
	 * "tee session-guard: <what> uuid=%16phN type=%u sha256=%8phN by comm[pid]: allowed|REFUSED" */
	...
}
```

- `client_open_trustlet`: drop the early NULL guard. After `tee_object_read/copy` succeeds, call
  `mc_blob_guard(obj, false, "open-trustlet")`, and on refusal `vfree(obj)` and return -EPERM
  before `client_add_session`.
- `MC_ADMIN_IO_LOAD_DRIVER`: drop the early refusal (admin.c:952). In `load_driver()`, call
  `mc_blob_guard(obj, true, "admin-load-driver")` right after `tee_object_read`, before
  `client_cbuf_create`.
- `MC_ADMIN_IO_LOAD_CHECK` stays refused unconditionally while guarded.
- GP sessions (`gp-open-session`) and `OPEN_SESSION` keep the UUID-only list (needed for the
  SWd built-ins FSD2 and DebugSession, which have no blob).
- Malformed DT lists (length not a multiple of 48) allow nothing, as today.
- Pins: the SHA-256 is over the exact vendor file bytes, because `tee_object_read` copies the
  whole user buffer (`length` = file size for both HALs and the daemon `-r`).
- A host fixture (like `t2-seccomp-fixture.c`) should feed the 6 real blobs, a 1-byte-modified
  copy and an SP-trustlet header through the guard function, compiled for userspace.

## 7. T3a: keymaster TA reachable only by a dedicated test client

### Shape

- The container is unchanged: keystore.mdfpp/libMcClient stay masked, so keystore2, vold,
  odsign and the soft keymaster HAL never see the TEE. The vendor keymaster service keeps
  running software-only.
- A new runner mode `tee-t3-run km` (extends `tee-t2-run.c`): the daemon is started as in T2
  v5 (private user node, seccomp). It waits for the daemon's "TEE is ready" and then makes sure
  `vendor.sys.mobicoredaemon.enable=true` (the helper polls this for 7.5 s; set it from the
  root runner if the daemon has not set it).
- Test client `crownlte-t3-km` (aarch64 bionic, built with the NDK clang) runs as uid 1000 /
  gid 1000 + drmrpc in its **own** mount namespace. There:
  - the masks on `/vendor/lib64/hw/keystore.mdfpp.so` and `/vendor/lib64/libMcClient.so` are
    unmounted (like the daemon's user-node trick, tee-t2-run.c:117–123);
  - the private `/dev/mobicore-user` node is bound in;
  - `/dev/socket/property_service` is bound over with an empty RO file, so the helper's
    `ctl.start`/`__system_property_set` attempts fail;
  - the client binary is bind-mounted at `/vendor/bin/crownlte-t3-km`, so the vendor linker
    namespace resolves `/vendor/lib64` + `/vendor/lib64/hw`.
  The client `dlopen`s `/vendor/lib64/hw/keystore.mdfpp.so`, takes `HMI`, calls
  `methods->open(module, "keymaster", &dev)` and then only the calls of the current sub-stage.
  It never calls generate/import/begin/attest/upgrade/delete or the function table at offset ≥0x98.
- Sub-stages (one trial each, stop on any surprise):
  - **T3a-0**: no driver preloads. Open → close. Proves `open-trustlet` through the new guard,
    TA load, WSM map.
  - **T3a-1**: same, plus `add_rng_entropy(dev, 32 bytes from /dev/urandom)` (cmd 1).
  - **T3a-2**: daemon `-r` with the three pinned drivers (sec …01, TIMA d…0a,
    CryptoManager d…16; VaultKeeper stays out), then `configure` with exactly
    {OS_VERSION=160000, OS_PATCHLEVEL=202608} (the values the 4.9 boot sends, §1), then
    `add_rng_entropy`, then close. `getHardwareFeatures` is reported from the module header
    (name/author/flags), with no TA call.

### Kernel allow-list (DTB on top of tee-t3dbg 06a6cf36)

```
samsung,tee-session-guard;
samsung,tee-session-allow = [07 05 05 01 00 00 00 00 00 00 00 00 00 00 00 20   /* FSD2 */
                             08 05 05 00 00 00 00 00 00 00 00 00 00 00 00 00]; /* DebugSession */
samsung,tee-trustlet-allow = [ff ff ff ff 00 00 00 00 00 00 00 00 00 00 00 3e
    40 db c9 84 ec d4 47 99 5b 7a 00 21 a1 e0 c8 e6 21 57 30 89 42 2f e7 48 14 b4 22 fe 7f 5d 4e a7];
/* T3a-2 only: */
samsung,tee-driver-allow = [ff ff ff ff 00 00 00 00 00 00 00 00 00 00 00 01  7f64cbdd…(full digest)
                            ff ff ff ff d0 00 00 00 00 00 00 00 00 00 00 0a  a78e0c87…
                            ff ff ff ff d0 00 00 00 00 00 00 00 00 00 00 16  baa9a8cd…];
```

(Full digests are in the Appendix. UUID bytes are as stored in the MCLF headers, verified with xxd.)

### Seccomp

- Daemon filter (T2 v5) + `LOAD_DRIVER 0x40184d02` in T3a-2 only. `LOAD_CHECK 0x40184d04` stays refused.
- New client filter (aarch64 only, kill on other arch). On `'M'` ioctls allow only `VERSION
  0x80604d09`, `HAS_SESSIONS 0x4d08`, `OPEN_TRUSTLET 0xc0284d01`, `CLOSE 0x4d02`, `NOTIFY 0x4d03`,
  `WAIT 0x400c4d04`, `MAP 0xc0284d05`, `UNMAP 0x40284d06`, `ERR 0xc0084d07`. Refuse
  `OPEN_SESSION 0xc0404d00`, every GP_* (≥20), and every admin code. Also return EPERM for
  `socket(AF_UNIX/AF_INET*)`, `connect`, `mount`, `ptrace`, `execve` after start-up
  (`NO_NEW_PRIVS`).
- File writes: no seccomp path filtering. The namespace is the control (below), and strace
  counts every write/open-for-write as in T2.

### What stays masked or read-only

- Container-wide: `mobicore.rc` (the runner starts the daemon), the gatekeeper rc and VINTF entry,
  widevine/wvkprov/fingerprint rcs, `keystore.mdfpp.so`, `libMcClient.so` (32/64),
  `/dev/mobicore-user`.
- `/efs` is the read-only RAM copy (ro loop + `ro,noload`) and `/mnt/vendor/efs` does not exist
  (the KM helper and GK HAL fall back there). Registries `/data/vendor/mcRegistry`,
  `/data/misc/mcRegistry` stay tmpfs.
- No `/dev/block/sd*`, no `by-name` (the existing check), **and add: no `/dev/bsg/*` nodes and
  no `/dev/sg*`**, so nothing in the container can send SECURITY PROTOCOL to the UFS RPMB W-LU.
  tee-t2.sh should refuse to start if any exist.
- Private debugfs only for the three passive files (as T2); no crashdump.

### Expected log lines

- kmsg: `tee session-guard: ON, 2 allowed UUID(s)` + trustlet/driver pin counts;
  `tee session-guard: open-trustlet uuid=ffffffff00000000000000000000003e type=3 sha256=40dbc984… by crownlte-t3-km[N]: allowed`;
  T3a-2: three `admin-load-driver … type=1 … allowed` lines from mcDriverDaemon.
- logcat `keymaster2_mdfpp`: `entered device_open function`; `keymaster_tee`:
  `[WRN]Open MDFPP keymaster version 2.3.10`. T3a-2: `[WRN]build type : userdebug`. The
  `configure` result is printed by the client.
- Client stdout: `T3 km open rc=0`, `T3 km add_rng_entropy rc=0` (KM_ERROR_OK),
  T3a-2: `T3 km configure os_version=160000 patchlevel=202608 rc=0`, `T3 km close rc=0`.
- Trace totals: 0 writes to `/efs`, `/mnt/vendor/efs`, the registries; 0 `ctl.` property sets.
- Possible failure lines and what they mean: `[WRN]Property vendor.sys.mobicoredaemon.enable is different…`
  (property not set), `[WRN]mcOpenSession failed: 0x…` (guard or blob), `KEYMASTER_NOT_CONFIGURED`-
  type errors in T3a-1 (the TA needs configure first: move rng to T3a-2), CM/TIMA errors in TA logs
  without the drivers (expected in T3a-0/1).

### Residual risks

- TA-internal ICCC/Trustboot writes during `configure` go to SWd memory that 4.9 also writes on
  every boot [inf]. If any of them target RPMB, they fail on 6.12 (no proxy). The worst case is an
  ICCC error inside this boot only.
- Loading the sec driver (…01) starts its SRPMB client ("Initialized magic number to RPMB" path).
  Without the WSM registration it cannot reach RPMB [inf]. A pending SWd→NWd SRPMB IRQ with no
  6.12 handler is a spurious-IRQ risk only. Watch for an unhandled-IRQ storm.
- TA restart loop in the helper (re-configure) if the TA misbehaves. The client bounds the run (60 s) and
  the runner kills the process group.
- Unknown: whether the Kinibi 6.12 driver forwards the SWd log. Without it, failures inside the
  TA show only as return codes.

## 8. T3b: gatekeeper

### Can it be tested without any write?

- **Through the HAL: no.** `verify()` always writes both gk_context files (§2), and the HAL
  reports failure if it cannot. Do not start the gatekeeper HAL in T3.
- **Through a dedicated TCI client: yes for open/close, and almost for verify.**
  - **T3b-0 (no command):** `mcOpenDevice` → `mcOpenTrustlet(0813…)` → `mcCloseSession`. No TCI
    command is sent, so the TA does no SFS/RPMB work [inf: TA init only logs its version].
    No driver needed. Writes: none.
  - **T3b-1 (verify, fake identity, bounded):** daemon `-r` CryptoManager only (d…16, pinned). The
    client builds the TCI itself (layout §2) with uid `0x7ffffff0` (no Android user maps to it),
    a syntactically valid handle of the current version with random HMAC, and a random password.
    It uses `mcWaitNotification(…, 15000)`, never `0xffffffff`, and **writes no gk_context file**.
    TA side: it reads, then increments, the failure record for that fake uid in SFS → RPMB.
    On 6.12 this cannot reach RPMB (no proxy), so expect `SFS read fail` /
    `secureFS and kinibi partition read failed.` and a non-zero TA return. A "kinibi partition"
    fallback through FSD2 would try `/efs/TEE/Store_*.tf` and get EROFS on the RO copy.
    **Net persistent writes: none possible**, which is still a write *attempt* by the TA.
  - T3b-1 answers the T4 question: can gatekeeper verify work on 6.12 without SRPMB at all.

### Kernel allow-list

```
samsung,tee-trustlet-allow += [08 13 00 00 00 00 00 00 00 00 00 00 00 00 00 00
    50 62 a6 b0 d2 7a 8e bd 89 1f 51 ab d1 15 33 24 7b c6 c6 00 9c 5f 5a ce 0b 98 ff db d0 7c 3f 57];
samsung,tee-driver-allow   = [ff ff ff ff d0 00 00 00 00 00 00 00 00 00 00 16  baa9a8cd…];  /* T3b-1 only */
```

### Seccomp / masks

Same client filter as T3a. Same namespace, but the client needs only libMcClient (no HAL `.so`;
`gatekeeper.exynos9810.so` stays unused). Same masks. Gatekeeper HAL rc + VINTF stay dropped.

### Expected log lines

- `tee session-guard: open-trustlet uuid=08130000000000000000000000000000 type=3 sha256=5062a6b0… by crownlte-t3-gk[N]: allowed`.
- T3b-0: `T3 gk open rc=0 close rc=0`.
- T3b-1: `T3 gk verify uid=0x7ffffff0 rc=<mc rc> ta_ret=0x… retry_timeout=… reenroll=…`. If the
  SWd log is forwarded: `[TEE Gatekeeper] SFS read fail` and/or CryptoManager
  `RPMB: exceed maximum SMC retry count` / sec driver `[srpmb] RPMB work is out of retry_cnt`.
- Trace: 0 writes to `/efs`. Daemon FSD2: no `Store_` writes (EROFS at most).

### Residual risks

- SWd stall: a CryptoManager/SRPMB retry loop could hold a SWd thread for a long time. The
  bounded wait + `mcCloseSession` + runner kill cover the NWd. A wedged SWd needs a reboot (RAM-only state).
- If the CryptoManager path can reach RPMB by some route other than the NWd proxy (not found), the
  fake-uid failure record would be written once. That is harmless to the owner's lock screen
  because of the uid choice.

## 9. What T4 (read-only `/data` unlock with the owner's pattern) needs on top

1. **Working gatekeeper verify on 6.12.** If T3b-1 shows SFS failing without SRPMB (expected),
   T4 is blocked until the Samsung `ufs-srpmb` NWd proxy is ported (4.9 `scsi_srpmb.c`, 417
   lines: DT `samsung,ufs-srpmb`, `SMC_SRPMB_WSM`, IRQ → SCSI SECURITY PROTOCOL IN/OUT on the RPMB
   W-LU). Porting it **re-enables real RPMB writes**: the GK failure record increment/clear on
   every verify, which is what 4.9 does on every unlock. It also includes any sec-driver
   initialisation writes. That is a new irreversible class and needs its own review + owner go.
   Bound the proxy to SWd-initiated requests only; no userspace RPMB path.
2. **gk_context policy.** Each verify rewrites `/efs/TEE/gk_context_<uid>` + `_sub_` (and HAL
   failure if not writable). If those writes go only to a RAM copy while the RPMB failure record
   is updated for real, the 4.9 boot later sees a stale context against the RPMB record. The TA
   checks this ("Invalid handle version, current[%d], throttle[%d]"), and it **could lock the
   pattern** [inf]. Options: (a) mirror 4.9 exactly: write those two files to the real EFS
   (needs the C write-allowlist stage and an `/efs` RW mount scoped to the gatekeeper HAL namespace
   and these two names), or (b) prove first, on 4.9 with read-only hashing, how the files change
   per unlock. Recommendation: (a), with owner go, after C. The uid is the LockSettings fake uid
   (AOSP `100000 + userId` → `gk_context_186a0`) [inf]. Confirm by listing names on the phone.
3. **No re-enroll.** The TA can ask for re-enroll ("upgrade gatekeeper: handle_version from %d to
   %d"). LockSettings would then call `enroll` (new handle in `/data` + new contexts + SFS). For T4,
   preload a small shim into the gatekeeper HAL service (container rc `setenv LD_PRELOAD`) that
   refuses `enroll` and clears `request_reenroll` on the way out. Abort the trial if re-enroll was
   requested.
4. **Keymaster identity with 4.9:** `ro.build.version.release` and
   `ro.build.version.security_patch` equal to the live 4.9 values (`ro.build.type` too, for the
   TIMA branch). Driver set for KM = sec/TIMA/CryptoManager as on 4.9. Abort on any
   KEY_REQUIRES_UPGRADE (-62) in vold/keystore2 logs or any `keymaster_key_blob_upgraded` creation.
5. **TEE keymaster exposed to keystore2/vold/LockSettings:** unmask keystore.mdfpp + libMcClient
   container-wide. Widevine/fingerprint/wvkprov stay emptied. The guard allow-list stays exactly
   {FSD2, DebugSession, 003e, 0813, drivers 01/0a/16}.
6. **Read-only `/data` that still lets the framework run:** f2fs `ro,norecovery` plus
   `blockdev --setro` on the USERDATA node. Writable views via tmpfs binds (keystore2 DB copy,
   `/data/system`, `/data/misc/gatekeeper` keeps `.coldboot`, so gatekeeperd does not try
   `deleteAllUsers`). overlayfs over an fscrypt lower is unreliable; prefer explicit tmpfs binds.
   vold must take the "load existing keys" path only.
7. Already on the roadmap: FMP/inline-crypto known-answer test (stage D), adb in ROM-RAM for the
   unlock script (one attempt, lockout guard), owner's T4 approval (given 17:50), and a separate go
   for 1 and 2(a).

## 10. Open questions

- The exact tlApi call sites in the KM TA (driver ids come from the literal pool only).
- Whether `configure`'s ICCC branch writes anything persistent on a `userdebug` build.
- Whether the GK TA can verify with SFS unavailable ("kinibi partition" fallback). T3b-1 answers it.
- Whether the 6.12 Kinibi driver prints the SWd log buffer (needed for T3 diagnosis).
- Exact gk_context file names on the phone (fake uid) and the live 4.9 property values.

## Appendix A: SHA-256 pins (vendor image as mounted on gekko)

```
08130000000000000000000000000000.tlbin  5062a6b0d27a8ebd891f51abd11533247bc6c6009c5f5ace0b98ffdbd07c3f57  (GK TA, sys TA)
ffffffff00000000000000000000003e.tlbin  40dbc984ecd447995b7a0021a1e0c8e621573089422fe74814b42d5c84211ea7  (KM TA, sys TA)
FFFFFFFF000000000000000000000001.drbin  7f64cbdd78e2da8cb5f2d8b951b1cb3c7e5a3e15badc1263883bb9c973f21caf  (sec drv 0x40000)
ffffffffd0000000000000000000000a.tlbin  a78e0c874a99d79e96af5c464d093ae41f31f07e885c7de30cb4effab0d64fa8  (TIMA 0x40003)
ffffffffd00000000000000000000016.tlbin  baa9a8cd23afa8db5afec1a21a3f38fef3e2ae96776868b093740a7864667a1e  (CryptoManager 0x40006)
ffffffffd00000000000000000000045.drbin  67f4f9dfb761cc2a8b57a731f9b89d37e05f4cf4b8e23803bc59893047168d83  (VaultKeeper 0x4002e, not needed)
lib64/hw/keystore.mdfpp.so              9f3f1567ccf9a24e00b111b9a08fc885147e497a964f2a0a0729fb6fc423f02b
lib64/libkeymaster_helper_vendor.so     b79741c2de3c925aa63f7768a9dfe145788b5167da19c09f90058d51603049f6
lib64/libkeymaster2_mdfpp.so            24e981206e9fd001f07ba76a3f7c39781b142f796e6f69d0a5202edba87def12
lib64/hw/gatekeeper.exynos9810.so       8db7180fb5ad6c5894308b076ca0099fb018412c5b34e1a190c04425bd39f31f
lib64/libMcClient.so                    3606fe238f5a93d89578139c346d08840a7d2745dcc2ebecef048a5ef4e669d1
bin/mcDriverDaemon                      c7f6275bfa7715c4eb927f16123e90d6ad9bafeae8c7e5f03e8f720da8875073
lib64/libkeymaster3device.so            623e0aae32584a4b967c1b6b5c9f1809846cf60dc0ed0f81ebd2026995b770af
```

## Appendix B: evidence index

| claim | where |
|---|---|
| KM TA path + `mcOpenTrustlet(spid 0xfffffffe, tci 0x10)` | libkeymaster_helper_vendor.so `OpenSession` 0x10daf0 |
| daemon-ready property wait 7.5 s, mcMap of in/out | helper `nwd_tz_open` 0x10d718 |
| configure: build type, TIMA file, cmd 0xf, tags 0x2c1/0x2c2 | helper `nwd_configure` 0x10a490 |
| Samsung tags 0x325/0x326, CREATION_DATETIME on cmds 2/4 | helper `nwd_km_KM_INDATA_pack` 0x109380 |
| property names + regexes | helper `get_os_version_uint` 0x10b140, `get_os_patchlevel_uint` 0x10b2f0; libkeymaster3device strings |
| ConfigureDevice before wrapping | libkeymaster3device `ng::CreateKeymasterDevice(keymaster2_device*)` 0x11f640, `ConfigureDevice` 0x11f8a0 |
| device_open → open connection; delete_all_keys -100 | libkeymaster2_mdfpp.so 0x102860, 0x1023f0 |
| write_blob callers (attestation/SAK only) | helper 0x10f0b0 ← `validate_installed_key` 0x10ba40, `KM_generate_sak` 0x10cd60, 0x10c988, 0x10d020 |
| TA restart on comm failure | helper string "[WRN]restart TA" xref 0x10e3fc (in 0x10e160) |
| GK open (no delete_*), verify/enroll file I/O, TCI layout | gatekeeper.exynos9810.so 0x102368, 0x102c78, 0x102510, 0x102a20 |
| GK TA → CryptoManager driver | 08130…tlbin literal 0x00040006 at file 0x1c68, stubs 0x2bfe–0x2c56, call-driver stub 0x274c |
| SRPMB inside sec driver / RPMB in CryptoManager | strings of FFFFFFFF…01.drbin, ffffffffd…16.tlbin |
| 4.9 NWd SRPMB proxy | /root/crownlte-hid-kernel/drivers/scsi/scsi_srpmb.c:26,104–218,334,348; CONFIG_UFS_SRPMB=y; DT exynos9810-crownlte_eur_open_26.dts:10328 |
| 6.12 has no proxy | tee worktree: only `include/linux/smc.h:74,118` define `SMC_SRPMB_WSM`; no `samsung,ufs-srpmb` |
| guard + blob copy + load_driver | tee worktree main.c:690–727, client.c:640–720, admin.c:597–650, 696–735, 945–1000, mcp.c:531–556, mcp.h:22 |
| FSD2 default partition path | mcDriverDaemon strings `/efs/TEE`, `Store_%1X.tf`, `--Px PARTITION[x] PATH` |
| container masks today | rom-ram/src gen_payload.py EMPTY / VINTF_DROP / BACKGROUND_EXECS |
