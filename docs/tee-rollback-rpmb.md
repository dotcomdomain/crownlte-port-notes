# Trustonic TEE on 6.12: res 32 (DOWNGRADE_NOT_AUTHORIZED) and the RPMB relay

Status: research only (2026-11-07). Read-only, no phone access, nothing built.
Scope: why Kinibi (t-base 400C) refuses CryptoManager, keymaster and gatekeeper on 6.12,
how 4.9's ufs-srpmb works, and the smallest safe next step.

## TL;DR

- **Most likely root cause (confidence about 80%).** 6.12 has no NWd RPMB relay (ufs-srpmb). Without it the secure world cannot read its anti-rollback records from RPMB. It then fails closed with `MC_MCP_RET_ERR_DOWNGRADE_NOT_AUTHORIZED` (32) for every blob whose MCLF `service_version` is non-zero.
- **Discriminator.** The blobs that load (sec driver, TIMA) have `service_version = 0`. All three refused blobs have `service_version >= 1`.
- **What 4.9 does.** At every boot, 4.9 performs RPMB **reads only** exactly while mobicore loads its drivers and keymaster opens its TA: 17-20 `READ_DATA` requests, 0 counter reads, 0 writes up to and past `earlyBootEnded`. RPMB **writes** appear only when the lock screen is unlocked (consistent with gatekeeper).
- **Next step.** Smallest safe next step is **stage R1, a read-only RPMB proxy**: register the WSM, serve only `READ_DATA` whose frame is an Authenticated Data Read (0x0004), and refuse everything else in the kernel. Run it with the SWd log forwarding on. This should unblock T3 (CryptoManager, keymaster open, gatekeeper TA open).
- **Limit.** T4 (pattern unlock) cannot work read-only, because gatekeeper verify writes RPMB on 4.9. That needs an owner decision (see the end).

## 1. Evidence

### 1.1 MCLF headers (vendor `/vendor/app/mcRegistry`, RO image on gekko)

Header layout: `tee/drivers/gud/gud-exynos9810/MobiCoreDriver/mci/mcloadformat.h:88-120`. `service_version` is at file offset 0x48 (line 119). All five blobs are MCLF v2.5 (0x00020005) with the EXTENDED_LAYOUT flag.

| blob | type | flags | drv id | threads | **service_version** | hwcfg | 6.12 result |
|---|---|---|---|---|---|---|---|
| FFFFFFFF..01.drbin (sec driver) | 1 driver | 0x9 (PERMANENT) | 0x40000 | 6 | **0x0** | 0 | loaded |
| ffffffffd0..0a.tlbin (TIMA) | 1 driver | 0x8 | 0x40003 | 2 | **0x0** | 0 | loaded |
| ffffffffd0..16.tlbin (CryptoManager) | 1 driver | 0x8 | 0x40006 | 4 | **0x1** | 0 | res 32 |
| ffffffff..3e.tlbin (keymaster) | 3 system TA | 0x8 | - | 1 | **0x10001** | 1 | res 32 |
| 0813..00.tlbin (gatekeeper) | 3 system TA | 0x8 | - | 1 | **0x1** | 0 | res 32 |

Code 32 is documented as "Service version is lower than the one installed" (`mci/mcimcp.h:91-92`). The check only makes sense for versioned blobs. Version 0 apparently means "not rollback-protected", so those blobs never consult the store. All other blobs in the registry fall into the same two groups, for example d..45.drbin = 0 and d..0e/14/17 = 1.

### 1.2 Who talks to RPMB in the secure world

Strings are from the plaintext MCLF text segments. No decryption was involved.

- **Sec driver FFFF..01** contains an SRPMB client: `[srpmb] Driver: SRPMB IPC memory mapping`, `SRPMB status flag mapping`, `check magic fail`, `partition ctx loading fail`, `register_partition`, `reserved partition[%d] is added`, `dynamic partition[%d] is added`, `This TA is not registered`, `read data error in LDFW F/W`, `write error in LDFW F/W`, `RPMB work is out of retry_cnt`, **`[srpmb] Initialized magic number to RPMB`** (a write on a failed magic check). It manages per-TA RPMB partitions keyed by UUID (`UUID:`, `print RPMB partition`).
- **CryptoManager d..16** has `RPMB: error in CryptoManager F/W`, `RPMB: exceed maximum SMC retry count`, and `[KEYMAN] Driver: LDFW or SecDrv hasn't been loaded yet`.
- **The RPMB engine is Samsung LDFW `srpmb_fw`**, loaded by sboot. The 4.9 last_kmsg shows `ldfw: 3th ldfw's version 0x24181227 name : srpmb_fw`, `bl_init: rpmb is provisioned` and `initialize_secdata_rpmb: usable! (0x52504d42)`. On some boots sboot itself logs `RPMB: Magic Block Write: success`, so the write counter already moves at boot, independent of the kernel.
- The chain is: TA/Kinibi -> sec driver -> LDFW srpmb_fw (builds and MAC-checks the frames) -> **NWd WSM + IRQ** -> UFS RPMB W-LUN. The NWd is a dumb pipe. The SWd cannot reach UFS without it.

### 1.3 4.9 RPMB traffic

Source: owner's daily 4.9 kernel, Codex captures in `C:\Users\Patrick\Downloads\Codex Projects\kernel-upgrade\research\diagnostic-capture\`.

The boot in `trial-delayed-ufs-20261016\post-restore\2-last_kmsg.bin` (other post-restore and first-return captures show the same pattern):

```
3.39  sd 0:0:0:1: srpmb dma addr ... phy(97e280000)      (WSM registered at UFS LUN1 probe)
      [SECLOG] [RPMB] wsm init is done.
8.646 init: starting service 'mobicore'  (-r sec, TIMA, CryptoManager, d..45)
8.661 Trustonic TEE: admin_open: daemon connection open
8.677-8.682  3x  srpmb command(3) READ_DATA      <- during the -r driver loads
8.657 vendor.keymaster-3-0 started
8.795-8.811 15x  srpmb command(3) READ_DATA      <- keymaster TA open
9.02  vdc keymaster earlyBootEnded               (no RPMB writes)
~10 s  1-2x READ_DATA
```

Per-boot totals in four captures (`post-restore` x3, `trial-rom-art-roottrace-20261025\first-return`) are 15-20 `READ_DATA`, **0** `GET_WRITE_COUNTER` and **0** `WRITE_DATA` until user interaction.

Writes only happen later. In `trial-rom-art-roottrace-20261025` at about 200 s: 8 x cmd1 + 8 x cmd2 + 4 x cmd3. In `pretrial-usi-reset-20261015-complete` at 81683 s and 81732 s, each burst has the shape READ, then 4 x (GET_WRITE_COUNTER + WRITE_DATA), and comes immediately after touch and haptic events. That fits pattern entry on the lock screen, i.e. a gatekeeper verify. The gatekeeper TA has `upgrade gatekeeper: handle_version`, and gatekeeper failure records are normally written before verify. Attributing the bursts to gatekeeper is an inference.

### 1.4 6.12 state

- The 6.12 DT deliberately omits the relay: `tee/arch/arm64/boot/dts/exynos/exynos9810-crownlte-clocks.dtsi:123-124` says "Do NOT add 4.9 "ufs-srpmb" (SPI 460 …) or "seclog"". The `SMC_SRPMB_WSM` registration is therefore never made. The UFS RO guard (`tee/drivers/ufs/core/ufshcd.c:3054-3066` + `ufshcd-readonly.h`) also rejects every data-out command and the opcodes 0xA2/0xB5.
- T3a2 capture (`C:\Users\Patrick\tools\trial-romram-20261104\tee-t3a2\serial-ready.bin`): FSD2 and DebugSession are up. Sec driver loads at 07.657, TIMA at 07.665, CryptoManager LOAD_DRIVER at 07.681, then `mcp_cmd: open session: res 32` and EPERM at 07.761 (80 ms). The keymaster `mcOpenTrustlet` gets rc 0x9. Daemon strace: 0 writes to /efs or the registry.
- SWd log forwarding is **off** in both 4.9 and 6.12 (`MobiCoreDriver/logging.c:276-278`, debugfs `swd_debug` default false). That is why no `[srpmb]`/Kinibi reason text appears in our logs.

## 2. Answers

### Q1. What the downgrade check compares, and where the minimums live

It compares the blob's MCLF `service_version` with the "installed" (previously seen) version recorded by the secure world. The record is in RPMB, reached through the sec driver's SRPMB partition manager and LDFW `srpmb_fw`.

Evidence:
- The 4.9 RPMB reads happen exactly at driver load and TA open (§1.3).
- The sec driver's per-UUID RPMB partitions (§1.2).
- The version-0 / non-zero split (§1.1).

Why the sec driver and TIMA pass: `service_version = 0`, so no RPMB lookup is made. CryptoManager, keymaster and gatekeeper are versioned. The lookup cannot complete without the NWd relay, and Kinibi fails closed.

Candidates ruled out:
- **FSD2 / TbStorage (Store_N.tf).** /efs/TEE has no Store_1.tf on either kernel, and the 4.9 daemon starts at post-fs with the same layout. A missing filesystem would also give 33 `SYSTEM_NOT_READY` (`mcimcp.h:94`), not 32.
- **Auth token / root container.** `LOAD_TOKEN` succeeds and TIMA loads.
- **Registry containers.** System TAs (spid 0xfffffffe) are loaded from the blob. On 4.9 /data is not mounted when the drivers load, and the registry search paths are identical.
- **TA header versus a fused counter.** That would fail identically on 4.9, which has the same blobs and the same sboot.

Not proven: the exact Kinibi code path that turns "RPMB unavailable" into 32. The Kinibi core is not readable and was not attacked. Stage R0/R1 below will show the SWd's own log line.

### Q2. How 4.9 ufs-srpmb works

**Discovery and setup.**
- `sd_probe` for `0:0:0:1` calls `init_wsm` (`drivers/scsi/sd.c:3377-3386`, `scsi_srpmb.h:43`).
- The DT node is `ufs-srpmb { compatible = "samsung,ufs-srpmb"; interrupts = <0 460 0>; }` (`exynos9810-crownlte_eur_open_26.dts:10328-10331`).
- `init_wsm` (`drivers/scsi/scsi_srpmb.c:286-379`) does the following:
  - `dma_alloc_coherent(sizeof(Rpmb_Req) + 32 KiB)` (:300).
  - Maps the IRQ and requests it `IRQF_TRIGGER_RISING` (:334).
  - Allocates the workqueue `srpmb_wq` (WQ_MEM_RECLAIM | UNBOUND | HIGHPRI, max_active 1; :326).
  - Registers a PM notifier (:342).
  - Calls **`exynos_smc(SMC_SRPMB_WSM = 0x82003811, phys, hwirq, 0)`** (:348, `include/linux/smc.h:74`). Here hwirq is the GIC hwirq (SPI 460 = 492).
  - The SWd confirms with `[SECLOG] [RPMB] wsm init is done. [buffer:97e280000]`.

**Shared buffer.** `Rpmb_Req { u32 cmd; volatile u32 status_flag; u32 type; u32 data_len; u32 inlen; u32 outlen; u8 rpmb_data[]; }` (`include/scsi/scsi_ioctl.h:50-58`). `rpmb_data` holds JEDEC 512-byte frames in wire (big-endian) order. `swap_packet` (`scsi_srpmb.c:49-54`) reverses the whole 512-byte frame into `struct rpmb_packet` (`scsi_srpmb.h:62-72`). The request/response type is therefore the big-endian u16 at frame bytes 510-511, and the address is at bytes 504-505.

**Notification.**
- The SWd writes the request and raises SPI 460.
- `rpmb_irq_handler` sets `status_flag = RPMB_IN_PROGRESS` (0xDCDC) and queues the work (`scsi_srpmb.c:267-284`).
- The worker sets `RPMB_PASSED` (0xBABA) or an error code (0x601-0x60C, `scsi_srpmb.h:25-39`).
- The SWd polls `status_flag`, with retry limits per `[srpmb] RPMB work is out of retry_cnt`.
- During suspend the flag is set to `RPMB_FAIL_SUSPEND_STATUS` (`scsi_srpmb.c:229-265`).

**Operations** (`srpmb_worker`, `scsi_srpmb.c:66-227`):

| type | name | NWd sequence | SWd frame type |
|---|---|---|---|
| 1 | GET_WRITE_COUNTER | SECURITY PROTOCOL OUT 512 B, then IN `data_len` | 0x0002 read counter |
| 2 | WRITE_DATA | OUT `data_len` (512 B to 64 x 512 B), then the NWd builds a RESULT_READ_REQ (0x0005) and sends OUT 512 B, then IN 512 B, then checks `result` | 0x0003 authenticated write (or anything the SWd put there) |
| 3 | READ_DATA | OUT 512 B, then IN `data_len` | 0x0004 authenticated read |

The kernel **never checks the frame type**. Whatever the SWd puts in `rpmb_data` is sent, including 0x0001 key programming, under any type. `MAX_BUFFLEN` limits one transfer to 16 KiB (`scsi_ioctl.c:33`).

**SCSI side.**
- `srpmb_scsi_ioctl` (`drivers/scsi/scsi_ioctl.c:360-410`) builds a 12-byte CDB `B5|A2, EC, 00 01, 00 00, len(be32), 00, 00` (SECU_PROT_UFS 0xEC, SPEC 0x0001: `include/scsi/scsi_proto.h:88-89`).
- It first clears UNIT ATTENTION on the RPMB W-LUN once with REQUEST SENSE (`wlun_clr_uac`, `ufs/ufshcd.c:7127-7140`).
- `ufshcd_get_scsi_lun` re-routes every SECURITY PROTOCOL IN/OUT to the RPMB W-LUN 0xC4 (`ufs/ufshcd.c:2016-2023`), even though it is issued on LUN 1.

**When requests happen.**
- Boot: driver load and TA open, read only (§1.3).
- Runtime: gatekeeper verify. Reads plus counter and authenticated-write pairs.
- Possible but not observed: first registration of a new TA partition, the SecDrv magic re-init, rollback-version bumps when a newer blob is loaded.

**Is a read-only proxy feasible?** Yes. Serve type 3 only, after checking that the private copy of the OUT frame is 0x0004. Refuse type 1 and type 2 by setting an error status without touching the device: `WRITE_COUTNER_SECURITY_OUT_ERROR` 0x602 and `WRITE_DATA_SECURITY_OUT_ERROR` 0x605, which are the same codes 4.9 uses for a failed SCSI command. Answer an unknown type with `RPMB_INVALID_COMMAND` 0x60B; 4.9 leaves it hanging in IN_PROGRESS.

GET_WRITE_COUNTER is non-mutating, but 4.9 never sends it at boot and it is only a prelude to writes. Refusing it makes any write flow fail one step earlier.

**SWd reaction to a refused write.** Per the SecDrv strings, `write data failed` / `write error in LDFW F/W`, then retries until `RPMB work is out of retry_cnt`, then an error is returned to the calling TA. Nothing reaches the device, so no counter increment and no data change. Expected effects:
- Gatekeeper enroll and verify fail, so no unlock.
- A first-time TA partition registration fails, so that TA does not open.
- If a read ever looked corrupt, the SecDrv "Initialized magic number to RPMB" re-init would be refused. **That refusal is the key safety property.**

**What an RPMB read needs on 6.12.**
1. A DT node for the relay (SPI 460, rising edge).
2. The WSM buffer and the SMC 0x82003811 registration, before mcDriverDaemon starts.
3. A `scsi_device` reference for the RPMB W-LUN. 6.12 `ufshcd_scsi_add_wlus` creates it and then drops the reference (`ufshcd.c:8299-8306`); look it up by the W-LUN number 0xC144 instead (`ufshcd_upiu_wlun_to_scsi_wlun(0xC4)` = (0xC4 & ~0x80) | SCSI_W_LUN_BASE 0xC100, `ufshcd.c:2976-2979`). Use `scsi_execute_cmd()` with runtime-PM get.
4. One REQUEST SENSE to clear UA. 0x03 is already allowed by the guard.
5. A **narrow** guard exception in `ufshcd_queuecommand`:
   - 0xB5 data-out only if the target is the RPMB W-LUN, cdb[1] = 0xEC, cdb[2..3] = 00 01, cdb[4..5] = 0, the length is exactly 512, the control byte is 0, and `scsi_sg_copy_to_buffer` shows `be16(frame[510]) == 0x0004`.
   - 0xA2 data-in only for the RPMB W-LUN with the same CDB checks and length 512..16 KiB in steps of 512.
   - Everything else stays rejected.

   bsg and sg stay disabled. The 6.12 ARPMB path (`ufs_bsg.c:67+`) needs UFSHCI 4.0 plus bsg and is unreachable on this phone.

### Q3. Other explanations

- **No "system ready" or "boot completed" message exists.** The NWd-to-SWd command set is OPEN/CLOSE/MAP/UNMAP/SUSPEND/RESUME/GET_VERSION/CLOSE_MCP/LOAD_TOKEN/CHECK_LOAD_TA (`mcimcp.h` enum cmd_id). The filesystem-not-ready case has its own code (33).
- **gud diff 4.9 vs 6.12** (`diff -rq` plus a grep of `mcp.c`, `fastcall.c`, `nq.c`, `main.c`, `admin.c`). The only changes are the port adaptations, the self-test counters and the session/blob/registry guards. No MCP command or fastcall is missing. Time handover is equivalent: `getnstimeofday` became `ktime_get_real_ts64` and `getrawmonotonic` became `ktime_get_raw_ts64` (`nq.c`). The SWd-reported version is identical (t-base 400C, mci 0x10006, driver API 6.3).
- **TUI.** The 4.9 defconfig has no TUI option, so 4.9 has no TUI device either. sec-os-ctrl and booster only affect CPU placement and frequency.
- **RTC / time.** The sec driver has an `RTC tick timer isn't set yet` path. It is used for HDCP/secure time, the version check does not use time, and the 6.12 wall clock is passed the same way. Low likelihood.
- **The 6.12 OS version or patch level.** The keymaster props (160000/202608) are only used by `configure` after open, and the refusal happens at open. Not a factor.
- **Residual alternative (about 15%).** The SWd needs some other boot-time NWd service that only 4.9 provides. The only other 4.9 secure-firmware NWd services found are `exynos-seclog` (SMC_CMD_SEC_LOG_INFO, a log buffer) and the EL3 monitor. Neither is a plausible version source. R0/R1's SWd log settles it.

### Q4. Irreversible RPMB operations and how to guarantee none happen

| JEDEC request (frame bytes 510-511) | effect | proxy |
|---|---|---|
| 0x0001 Authentication Key Programming | one-time key burn. Already provisioned here (`bl_init: rpmb is provisioned`). | refuse |
| 0x0003 Authenticated Data Write | changes data and increments the 32-bit write counter, which is monotonic and can never decrease; RPMB is permanently read-only at 0xFFFFFFFF | refuse |
| 0x0006 Auth. Device Config / Secure Write Protect write (newer UFS/eMMC) | persistent configuration | refuse |
| Purge / ARPMB (UFS 4.0) | n/a (UFSHCI < 4.0) | unreachable |
| 0x0002 read counter, 0x0004 read data, 0x0005 result read | no state change | R1 allows **0x0004 only** |

The guarantee rests on two independent kernel checks plus safe handling of the shared buffer:

- **(a) The relay** decides by `Rpmb_Req.type` and then by the frame type in a **private kernel copy** of the OUT frame. The copy is what gets sent, so the SWd cannot change it after the check. type 3 needs outlen 512 and frame 0x0004. Any other type, frame, or length gets an error status and no SCSI command.
- **(b) The UFS queuecommand guard** independently re-checks the opcode, LUN, CDB, length and payload frame type before DMA.
- **WSM handling.** `data_len` is bounded (512..16 KiB, a multiple of 512) and the IN copy-back is bounded by the buffer size. `status_flag` is written last with a barrier.

Fixture tests (like `blob-guard-fixture.c`) should cover every request type and the boundary lengths. Each request logs type, frame type, address (bytes 504-505), block count and decision, with sysfs counters `reads_ok / refused_write / refused_counter / refused_other`.

## 3. Staged plan

**R0 (optional, zero new SWd capability).** Turn on SWd log forwarding (DT `samsung,tee-swd-log`, which sets `log_ctx.enabled` at init) and re-run T3a2 unchanged. Expect `[srpmb]`/Kinibi lines that state the reason, for example SRPMB not initialised or a read failure. The cost is one phone slot. It can be folded into R1 instead, which is my recommendation.

**R1 (recommended next step): read-only RPMB proxy plus SWd log.**
- Kernel: new `crownlte-srpmb-ro` built from `scsi_srpmb.c` with the Q4 policy.
  - DT node `ufs-srpmb` (SPI 460, `IRQ_TYPE_EDGE_RISING`), disabled by default and enabled only in the R1 DTB.
  - WSM registration after the UFS RPMB W-LUN exists (the delayed-UFS path) and before the daemon starts, with sysfs `ready=1`. The ROM-RAM runner must refuse to start the daemon until `ready=1`.
  - The narrow UFS guard exception from Q2.
  - SWd log on.
- Ramdisk: the T3a2 and T3b0 runners as they are now (pins, masks, seccomp, no key creation). Add a check that `refused_*` counters and `reads_ok` are captured.
- PASS:
  - The CryptoManager LOAD_DRIVER succeeds and keymaster `mcOpenTrustlet` returns rc 0.
  - `reads_ok` is about 15-20, matching 4.9.
  - `refused_write = refused_counter = refused_other = 0`.
  - No `diagnostic rejects SCSI opcode` other than the expected ones.
- STOP rule: if any refusal occurs, log the frame type and address only, do not retry with writes, and report.
- Expected side effects: none on the device. Reads do not change RPMB, and every write path ends at the relay.

**R2 (T3b, gatekeeper open plus keymaster configure/addRngEntropy).** Same image as R1. 4.9 shows only reads for these, at ~10 s and during earlyBootEnded.

**R3 (needed for T4 pattern unlock): writes.** Gatekeeper verify writes RPMB (4 counter+write pairs per attempt on 4.9). Options are in the decisions list below. If writes are ever enabled, enable them only for addresses already seen in R1/R2 refusal logs for the gatekeeper partition. Never allow 0x0001 or 0x0006. Never allow writes while any read returned a MAC/verify error. Give it its own Sol review.

## 4. Risks and notes

- **Hard lesson.** A relay that allows writes but returns bad read data could trigger the SecDrv "Initialized magic number to RPMB" path. That would reformat the TA partition table and lose the gatekeeper/keymaster RPMB state on 4.9 as well: lost keys and lockout. Read-only first is non-negotiable for that reason.
- WSM registration (SMC 0x82003811) is runtime configuration. It is redone on every boot (4.9 at 3.4 s), so it is not persistent.
- IRQ: give the DT the rising edge to avoid the q29 GIC WARN class. The SMC must get the GIC hwirq (492), as in 4.9.
- Suspend: keep 4.9's PM-notifier semantics (FAIL_SUSPEND status). ROM-RAM trials do not suspend.
- Optional: port `exynos-seclog` (reserved mem 0xc0000000 / 0x80000 is already in the diag DT, SPI 218, SMC_CMD_SEC_LOG_INFO) to see LDFW `[RPMB]` lines. It is a second SMC registration and not required.

## 5. Owner decisions needed

1. **Approve R1:** register the SRPMB WSM with the secure world and let it issue authenticated **reads** to the real RPMB W-LUN, with all writes, counter reads and key programming refused in the kernel. This reverses the explicit "Do NOT add ufs-srpmb" note in the 6.12 DT.
2. **R0 first or not:** a separate log-only run, or fold it into R1 (recommended: fold).
3. **Write policy for T4** (pattern unlock needs gatekeeper RPMB writes):
   - (a) no RPMB writes from 6.12 until it becomes the daily kernel, so T4 is deferred.
   - (b) address-allowlisted gatekeeper writes derived from R1/R2 refusal logs, with a separate review.
   - (c) the full 4.9-equivalent relay, only at the real-boot stage (F/G).
4. **Optional:** port exynos-seclog for LDFW diagnostics.
