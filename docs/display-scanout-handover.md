# Display handover: why the SBoot scanout was "not verified" (q25/q26), and the fix

2026-11-04. Analysis and build only; no phone access. K49 = /root/crownlte-hid-kernel (4.9 that runs on
the phone), SBOOT = `C:\Users\Patrick\Downloads\Codex Projects\kernel-upgrade\research\sboot\`
(Ghidra exports of sboot N960FXXU9FVG2).

## TL;DR

Both drivers read the wrong register bank. Each DPU DMA channel has two 4 KiB blocks:

| block | address (channel n) | 4.9 accessor | holds |
|---|---|---|---|
| DPP  | 0x16021000 + n·0x1000 | `dpp_read` (`dpp->res.regs`, DT reg[0]) | ENABLE, IRQ, IN_CON (DPP fmt + alpha sel), IMG_SIZE @0x18, CSC, scaler. **No address registers** (0x40 = CSC_COEF4) |
| IDMA | 0x16071000 + n·0x1000 | `dma_read` (`dpp->res.dma_regs`, DT reg[1]) | ENABLE, IRQ, IN_CON @0x08 (IDMA fmt [15:11], rot [10:8], AFBC b7, block b3), SRC_SIZE @0x10, SRC_OFFSET @0x14, IMG_SIZE @0x18, IN_BASE_ADDR_Y @0x40 |
| DMA common | 0x16070000 (0x110) | `dma_com_read` (DT reg[2] of dpp0 only) | QCH, SWRST, QoS LUTs |

- Stage 1 (`exynos9810-decon-handover.c`) mapped only the DPP block and read the IDMA offsets there.
- Stage 2 (`exynos9810-crownlte-delayed-ufs-display-s2-diag.dts`) mapped both banks with the names swapped:
  `"dpp-dma"` = 0x16021000 and `"dpp"` = 0x16071000.

Both trials therefore read the DPP block. That block has IMG_SIZE at 0x18 (hence `IMG=1440x2960` /
`0xb9005a0`, correct) and nothing at 0x10/0x40 (hence `SRC=0`, `BASE=0`). Its IN_CON=0 is the *DPP*
format (ARGB8888 = 0). The q25 line `win5 -> ch5 dpp0(G0): ENABLE=0x1 IN_CON=0x0 SRC=0x0 IMG=1440x2960
BASE=0` is exactly the DPP block of G0 as SBoot leaves it. Nothing was clock-gated, and the address is
very probably still 0xcc000000.

## Q1: registers, banks, mapping

Confirmed (source/binary evidence):

- 4.9 DT `exynos9810-crownlte_eur_open_26.dts:5183` `dpp@369233920` (0x16021000):
  `reg = <0 0x16021000 0x1000  0 0x16071000 0x1000  0 0x16070000 0x110>`; dpp1..5 at +0x1000 each with
  IDMA 0x16072000..0x16076000. Aliases dpp0..5 = idma_g0, g1, vg0, vg1, vgf0, vgf1 (`:4660`, `:14275`).
- 4.9 `dpp_drv.c:941-976` `dpp_init_resources()`: MEM 0 -> `res.regs` (DPP), MEM 1 -> `res.dma_regs`
  (IDMA), MEM 2 -> `res.dma_com_regs`. `dpp.h:263` `dpp_read` uses `regs`, `dpp.h:322` `dma_read` uses
  `dma_regs`.
- 4.9 `regs-dpp.h:87-243` IDMA_* offsets (table above), `regs-dpp.h` DPP_* section: DPP_IN_CON 0x08,
  DPP_IMG_SIZE 0x18, CSC 0x30-0x40.
- 4.9 `dpp_reg.c:435` `dma_reg_set_in_base_addr()` writes IDMA_IN_BASE_ADDR_Y/C via `dma_write`.
- SBoot DPP init (raw 0x100904, `SBOOT/cpu-blit-decomp.txt:2398`): `dpp->regs = 0x16021000;
  dpp->dma_regs = 0x16071000; dpp->dma_com = 0x16070000`. The base write (raw 0x100d64/0x100d74,
  `dpp-register-decomp.txt:2080`) is `*(dpp->dma_regs + 0x40) = addr`, i.e. **0x16071040**. The
  BLOCK_EN clear (raw 0x100da4) also goes to `dma_regs + 0x08`.
- Window -> channel -> DPP: 4.9 `helper.c:604` `dpu_dma_type_to_channel`: G0->5, G1->3, VG0->0,
  VG1->4, VGF0->1, VGF1->2. Both 6.12 drivers use the correct inverse. SBoot: DATA_PATH_CONTROL_0
  0x00100000 = win5 enabled, DP1 0x00500000 = win5 -> ch5 -> G0 (dpp0) = 4.9 `default_win=5,
  default_idma=0`.
- Shadow registers: DECON has a shadow copy at +0x7000 (stage 1 logs DP1 shadow = live). The IDMA/DPP
  base is latched at frame start by DECON's SHADOW_REG_UPDATE_REQ (0x60); 4.9 never reads shadows back.
- Clocks: 6.12 `clk-exynos9810-composite.c:1375` `cal_vclk_enable/disable` return early for
  `VCLK_GATE` without `VCLK_QCH_DIS`, so `GATE_DPU_DMA`/`GATE_DPU_DPP` (`clk-exynos9810-legacy.c:157`)
  can't be gated by `clk_disable_unused`. The DPP-block reads worked in q25, and IDMA sits behind the same
  kind of QCH gate. Both drivers still read only after PMU 0x14064024 reports the DPU domain on.
- Does 4.9 read SBoot's state? No. `decon_reg.c:1789` `decon_reg_init()` sees RUN_STATUS, prints
  "decon_reg_init already called by BOOTLOADER", runs `decon_reg_init_probe()` (re-programs DECON
  global/LCD setup, not windows) and masks the trigger (`DECON_TRIG_DISABLE`). `decon_initial_display()`
  (`decon_core.c:4049`) then stops at `decon_init_done` (CONFIG_EXYNOS_COMMON_PANEL). fb0 memory is a
  fresh ion buffer (`decon_core.c:3564` `decon_fb_alloc_memory`), so 4.9 never reuses SBoot's buffer.
  The 4.9 DT has no fb_handover/decon pool reserved-memory node for it.

## Q2: SBoot's scanout address

- Confirmed (static): SBoot programs 0xcc000000 (literal raw 0xfba5c, written to IDMA+0x40 by
  raw 0x100d74), draws with the CPU at `0xcc000000 + 4*(y*1440+x)` through an identity map (raw
  0xdb8a8..0xdbc80), and logs `0x%llx -- 0x%llx will skip dram test (lcd_buffer)` for
  0xcc000000..0xcd400000 (raw 0xa1620). IDMA format 0, SRC/IMG 1440x2960, offset 0.
- Confirmed (hardware, q25): simplefb readback 8/8 at 0xcc000000 (RAM, writable), SysMMU dpu2 CTRL=0x4
  (bit0 clear = bypass), so the IDMA address is a PA.
- Inferred: nothing seen so far says SBoot moves the buffer, and the q25/q26 zeros come from the wrong
  bank, not from a different address. q38 prints the real IDMA BASE; if it is not 0xcc000000, point
  simplefb/`samsung,boot-fb` at the logged value.
- 4.9 itself has no reserved node for it; the range is ordinary RAM to 4.9 (fb0 lives in an ion buffer).

## Q3: pushing frames on the command-mode panel

4.9 crownlte DECON-F: `psr_mode = 2` (MIPI command), `trig_mode = 0` (HW trigger from TE, gpd0-0;
4.9 `hw_te_on` pinctrl = function 0xf). Confirmed 4.9 sequence (`decon_core.c:1799-1805`,
`decon_reg.c:768`, `:1844`, `:2247`, `decon_core.c:2259-2286`):

1. wait `SHADOW_REG_UPDATE_REQ` (DECON+0x60) == 0
2. mask: `HW_SW_TRIG_CONTROL` (+0x70) clear HW_TRIG_EN b0, set HW_TRIG_MASK_DECON b4
3. program IDMA (base @+0x40, +0x44 C) / DPP / window registers
4. `SHADOW_REG_UPDATE_REQ |= BIT(31)` (global), [`GLOBAL_CONTROL |= EN|EN_F` at start only]
5. unmask: +0x70 set b0, clear b4 -> DECON sends a frame at the next TE and latches the shadows
6. wait vsync/frame done, wait +0x60 == 0, mask again (4.9 masks after every frame to save power)

Minimal safe sequences (no panel power, DSIM, PMIC, clocks or timing):

- **Same buffer, new pixels (stage 1):** step 5 only, i.e. one read-modify-write of DECON-F+0x70
  (0x3070 -> 0x3061). The trigger stays unmasked, so DECON re-sends the buffer on every TE (continuous
  refresh). SBoot leaves 0x3070 (masked; FRAME_COUNT=2 = SBoot's own frames). This is what
  `ho_trigger_hw_te()` does, and it's gated on the scanout being verified.
- **New buffer (stage 2 flip):** steps 1-6 with only IDMA_IN_BASE_ADDR_Y/C written in step 3 (format and
  size unchanged), leaving the trigger unmasked at the end. That's `dpu_apply()` in decon_drv.c.
- Inferred, not yet observed: a continuously unmasked HW trigger produces one frame per TE without
  further update requests (4.9 relies on this between unmask and mask; FRAME_COUNT after the write tells
  us). If FRAME_COUNT doesn't advance after the write, suspect the TE pad mux (gpd0-0 = GPIO bank
  0x108300a0; 4.9 sets function 0xf). Stage 1 does not touch pinctrl.

## Fixes

| branch | commit | change |
|---|---|---|
| crownlte-display | 6ac56b174 (on 2e507ed81) | handover: new `"dpp-dma"` resource 0x16071000+0x6000; IDMA regs read there; DPP block only for IN_CON/IMG cross-check; verify also needs linear IDMA (no rot/AFBC/block) and DPP IMG == IDMA IMG; no `dpp-dma` -> read-only |
| crownlte-display-s2 | dd4a8255e | same change (identical tree delta) on top of 0263a2095 |
| crownlte-display-s2 | c3b589012 | s2 DTS: un-swap `dpp-dma` (0x16071000) / `dpp` (0x16021000); `dpu_adopt()` also requires SRC h >= yres, 32-bit RGB IDMA fmt, no rot/AFBC/block, DPP IMG == IDMA IMG |

All guards are unchanged: refuse unless the scanout is verified, the PMU DPU domain is on and every DPU
SysMMU is in bypass. There are no new writes in stage 1, and nothing touches panel power, DSIM or PMIC.

Not fixed (outside scope): `crownlte-dpu-iommu` d41526fcb has the same swap in
`exynos9810-crownlte-delayed-ufs-display-s2-diag.dts` **and** in its own
`exynos9810-crownlte-delayed-ufs-display-s2-iommu-diag.dts` (`reg-names = "decon", "dpp-dma",
"dpp", ...` with 0x16021000 first). q28 (recovery 8d8f157c) would refuse to adopt exactly like q26.
Rebase on c3b589012 and fix the iommu DTS the same way before running q28.

## Build and trials

Build `build/display-6.12.93` (crownlte-display-s2 c3b589012, config a39d6aa8 = s2 config); artifacts
`build/display-6.12.93/s3/` (+SHA256SUMS). One Image carries both drivers; the DTB picks one.

| file | sha256 |
|---|---|
| Image | 52806c9911bc6a5673424bc2751f67c3e264993295ca06f1048d540239a2d675 |
| exynos9810-crownlte-delayed-ufs-diag.dtb (stage 1) | ff1f9dd2ee4c0483c28ab78d3635a644aa619500c95fa803e37f7af061030b06 |
| exynos9810-crownlte-delayed-ufs-display-s2-diag.dtb (stage 2) | d44e431d2b324b374df90bec513e328b7303383a88d0fcb591d8358ac24e6431 |

| trial | gekko dir | recovery.img sha256 | tests |
|---|---|---|---|
| q38-display3 | acpm-q38-display3 | 4d2c9154d616e5dc5fd9963b3516b594919a9b0afa5d352f62e38b656a0f6df7 | stage 1: simplefb on 0xcc000000 + one TE-trigger RMW, pattern at ~108 s |
| q39-display4 | acpm-q39-display4 | d9fd174d606734413f4d13f4d1b80bc18c594db31b4dccd813a5307780daf3dd | stage 2: DECON adopt, fb0 takeover, A/B CMA flips at ~108 s |

Both packed with SHIM bbea9590ba9b; local copies in `C:\Users\Patrick\tools\trial-acpm-q3{8,9}-display{3,4}-20261104\`.
Run q38 first. Run q39 only if q38 shows the buffer verified (it does real DPP/IDMA/window writes).

### Reading q38 (serial, `display handover:` / `display selftest:`)

- `win5 -> ch5 dpp0(G0): DPP ENABLE=0x1 IN_CON=0x0 IMG=1440x2960` then
  `... IDMA ENABLE=.. IN_CON=.. (fmt 0 rot 0 afbc 0 blk 0) SRC=1440x2960 IMG=1440x2960 OFF=0,0
  BASE=0xcc000000` -> bank fix confirmed, `scanout ... verified`.
- `WRITE DECON-F+0x70 HW_SW_TRIG_CONTROL 0x00003070 -> 0x00003061` and
  `FRAME_COUNT a -> b over 100 ms` with b - a ~ 6 -> TE frames flowing; fbcon becomes visible on the panel.
- Selftest PASS needs readback 8/8 **and** FRAME_COUNT advancing; a person must see the pattern and the
  ticking clock. Colour bars: RED/BLU swapped means SBoot fmt 0 is byte order A,R,G,B (expected caveat).
- `BASE=0x....` other than 0xcc000000 -> different buffer: point simplefb at it (if RAM) and repack.
- verified + write done but FRAME_COUNT flat -> TE not reaching DECON (check gpd0-0 mux) or the trigger
  needs a SHADOW_REG_UPDATE_REQ; stage 2's per-frame path does that.
- `fmt N` != 0 or rot/afbc/blk set -> refused by design; report the values.

### Reading q39 (`exynos9810-decon 16030000.decon_f:`)

- `win5 -> dpp0: IN_CON=0x.. SRC=0xb9005a0 IMG=0xb9005a0 OFF=0 BASE=0xcc000000 (DPP IMG=0xb9005a0)`
  then `adopted: win5 dpp0 IDMA format 0 at 0x00000000cc000000` and the trigger WRITE line.
- `decon selftest:` at ~108 s: flips between two CMA buffers; check latch/timeout counters in the log and
  the A/B colours on the panel.
- `no enabled window scans ...` again -> compare the logged IDMA values with q38.

## Confirmed vs inferred

Confirmed: register map and bank split (4.9 source, 4.9 DT, SBoot binary); SBoot writes 0xcc000000 to
0x16071040; the q25/q26 values match a DPP-bank read; the trigger semantics and 4.9 per-frame sequence;
SysMMU bypass and buffer writability (q25); vclk QCH gates can't be disabled by clk_disable_unused.

Inferred (q38 will show): the live IDMA BASE is 0xcc000000; an unmasked HW trigger refreshes from TE
continuously without update requests; SBoot left gpd0-0 muxed so that TE reaches DECON.
