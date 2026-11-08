# LITTLE-core C2 hang: APM (ACPM) firmware reverse engineering

2026-11-05. Static analysis only; nothing executed, no phone access.

## Inputs
- EL3 monitor: encrypted in the boot LUN. The boot ROM/BL1 decrypts it with the OTP-fused key (sboot has OTP ENABLE_AES / USE_ROM_SEC_BOOT_KEY handling at 0x13fed0..). sboot only issues SMC -300 "load secure payload" (FUN_0008523c -> FUN_0007f0b8 -> SMC 0xfffffffffffffed4, arg 0x20). It does not decrypt EL3 itself. Not pursued: obtaining the key is out of scope.
- APM firmware: plaintext Cortex-M (thumbv7m). next-research/apm.bin = sdb[0x2fc00 : +0x20000], loaded at APM address 0. Two images:
  - framework/MFD 0x0..~0x8000, build "92dbdb1 yongjin_man";
  - FlexPMU power-mode plugin 0xb400..0x12300, build "194e69e yongjin_man" (plugin literals resolve with delta 0).
- REA: the Ghidra raw loader does not support Cortex-M/Thumb-only (stated limitation), so the plugin was read with llvm-18 objdump --triple=thumbv7m on apm.elf. sboot was analysed in REA (Ghidra arm64, watchdog-research/sdb-analysis.elf); evidence ev_37b338d2 (FUN_0008523c), ev_47720a59, ev_dfc77cf1 (xrefs).

## Register map used by the APM (decoded jump tables)
| APM routine | Registers (APM 0xa407xxxx = AP 0x1407xxxx, INTR_GEN) |
|---|---|
| en_read(g) 0x10db4 / en_write(g,v) 0x10e1c | 0x14070000 + 0x100*g |
| pend_read(g) 0x10e7c | +0x4 |
| pend_clear(g,m) 0x11074 | +0xc |
| grp1_raw_pend(cpu) 0x111e0 | 0x14070108 |

- cpu → group bit: LITTLE cpu0..3 = bits 4..7, big = bits 0..3 (0x11480), as in 4.9 core0x tables.
- CPU_INFORM[cpu] = PMU 0x14060860 + 4*cpu: low 16 bits = mode (0x11348), bit 16 = hotplug flag (0x11408).
- Modes: 1 C2, 2 CPD, 3 SICD, 4/5 SLEEP, 6 STOP.

## Dispatcher (0x114a0, index = INTR_GEN group)
- **GRP2 (0x200) = core down** (0x114f4):
  - INFORM bit 16 → "HOTPLG_O", core recorded in hotplug mask [0x12204+0x44];
  - mode table 0x12124: 1 → C2 entry 0x10638, 2 → CPD 0x10714, 4/5 → SLEEP; 0 or >6 → "invldpwm".
- **GRP1 (0x100) = core wake** (0x11658):
  - INFORM bit 16 → "HotPLG_e", pend cleared, no power-on;
  - mode <= 0 → "invldnIR"/"GRP1_err", no power-on;
  - mode 1/2 → exit 0xfea4, then pend cleared.
- **C2 entry 0x10638:**
  1. "ente_c2+";
  2. APM core-off sequence: CONFIGURATION[3:0] = 0, then the core's GRP1 enable is set;
  3. if the core is in the hotplug mask or mode is SLEEP: GRP1 enable cleared and pend cleared (never woken);
  4. else if raw GRP1 pend (0x14070108) is already set: immediate exit (0xfdfc) + pend clear (0x11220).
- **Exit 0xfdfc:**
  - "released" mask [+0x48]: the first time a core exits, the "release_" sequence runs (CONFIGURATION[19:16] = 0xF);
  - afterwards "exit_c2+" = CONFIGURATION[3:0] = 0xF (+ STATUS wait for core00), GRP1 disabled.
- APM-side sequences = the 4.9 flexpmu_cal_cpu_exynos9810.h ACPM_FRAMEWORK branch (lines ~267-420). LITTLE and big are symmetric (big adds BISR/OTP retention writes). **No cluster-0-specific logic in the APM C2 path.**
- APM log ring:
  - initdata at SRAM 0x7f00 (acpm-q12-tmu/sram-dump.txt): rear @0x6f08, front @0x6f0c, data @0x6f10, 16-byte entries × 250;
  - entry = u32 header, 8-char tag, u32 value (written by 0xef1c).

## What q40/q43 show
Lost core q43 cpu1 / q40 cpu3 FAIL-state:
- status 0x880000 (off), config 0, inform 0x1 (C2, bit 16 clear);
- grp1_en has the core bit (APM armed wake), raw grp1_pend lacks it;
- the IPI gave no wake latch within 100 ms.

That rules out:
- the hotplug-flag path (bit 16 clear; that path disables GRP1 enable);
- invalid inform;
- the early-wake race (that path powers the core on).

**The APM behaved as for a big core. The PMU never got a wake request for that LITTLE core.** Once something powers it, it sits in EL3.

## Hypothesis (confidence low-medium)
- **H1 (EL3/GIC):** the SGI is pending at the core's redistributor, but no WakeRequest reaches the PMU (A55/DSU wake path, ProcessorSleep handling in EL3).
- **H2 (kernel ordering):** the IPI lands between cal_cpu_disable (GRP2 armed) and the EL3 WFI. EL3 consumes or masks it, so nothing stays pending after power-off.

Fails only with >= 2-3 LITTLE armed (IPI traffic). 4.9 additionally serializes exynos_cpu_pm_enter/exit under a global c2_lock (exynos-powermode.c ~500-583) and uses SICD at idle.

## Next steps
- **E1 (read-only):** at FAIL-state also dump the lost core's GICR_WAKER (RD + 0x14: ProcessorSleep bit 1, ChildrenAsleep bit 2), GICR_ISPENDR0 (SGI base + 0x200), GICR_ISACTIVER0 (+0x300), and PMU CLUSTER0_CPUn CONFIGURATION/STATUS (0x14062020+8n / +4).
  - Pending + ProcessorSleep + no latch → H1.
  - Not pending → H2.
- **E2 (read-only):** snapshot the APM log ring (SRAM 0x6f08/0x6f0c/0x6f10) at FAIL-state via the existing ACPM SRAM mapping. It shows the APM's own GRP2/C2/GRP1 events for that core and their order.
- **E3 (code):** mirror 4.9's global c2_lock around cal_cpu_disable (+ stale-GRP2 clear) and cal_cpu_enable for all CPUs (check whether pmucal_cpu_lock already covers both). Re-run little-3 with E1/E2.
- If H1 is confirmed: no kernel fix is possible without EL3 visibility. Keep LITTLE in WFI (current BIG-only C2).
