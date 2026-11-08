# crownlte audio on 6.12: ABOX + CS47L93 + 2x MAX98512 (design, research only)

Status: research and design only, 2026-11-07. No builds, no commits, no phone access.
Author: Opus 5.5 research sub-agent.

Paths and prefixes used for evidence:

- `K49` = `/mnt/ssd/host-builds/kernel-upgrade/src/close-range-fix-4.9` (source of the running BOOT kernel)
- `DT49` = `K49/arch/arm64/boot/dts/exynos/exynos9810-crownlte_eur_open_26.dts` (flattened, decimal numbers; converted to hex below)
- `T` = `android16-6.12-port` at `crownlte-integration-6` 4148bf525 (read with `git show` / `git grep`)
- `V` = VENDOR.img (`backups-20261103/partitions/VENDOR.img`), already mounted `ro,norecovery` at `rom-ram/testroot/vendor`
- `VABI` = `roadmap/vendor-abi/VENDOR-ABI.md`, `raw/mixer-ctls.txt`

Inferences are marked **GUESS**. Owner-gated steps are marked **[OWNER GATE Gn]**.

---

## 0. Summary (read this first)

1. **Parts.** ABOX (Samsung "Calliope" audio DSP, own firmware, own SysMMU, own GIC, in power
   domain `pd-aud`), Cirrus **CS47L93** codec on SPI (USI01, 0x10470000), **two MAX98512**
   smart amps on HSI2C 0x108C0000 at 0x39 (left) and 0x3A (right), Maxim **DSM** speaker
   protection running *inside the ABOX firmware*, VTS (voice trigger, separate DSP), BT SCO over
   ABOX UAIF3. No CS35L41, no TFA98xx, no separate earpiece amp.
2. **The earpiece is driven by a MAX98512, not by the codec.** Lineage `media-handset` sets the
   amp control `One Stop Mode = Receiver Left` and routes ABOX SIFS0 to UAIF1 (the amp link)
   (`V/etc/mixer_paths.xml:344-346,473-477`). The Note9 top receiver doubles as the second
   stereo speaker. So "first audio on the earpiece" is an amp write and is owner-gated. The
   only first-audio target without any amp write is the **3.5 mm headset** (codec HPOUT2).
3. **No TEE dependency.** On 9810 the ABOX boot needs no Trustonic call. `is_secure_gic()` is
   true only on Exynos8895 (`K49/sound/soc/samsung/abox/abox_util.c:143-149`), so the secure
   SMC 0x82000501 path is skipped; the ABOX GIC is programmed directly (`GIC_IS_SECURE_FREE`,
   `abox_gic.c:26`). The only EL3 call is the DTZPC PD SMC 0x82000410 for `pd-aud`, which the
   6.12 tree already issues for G3D (`T/drivers/soc/samsung/exynos9810-cal-smc.c`).
4. **Strategy.** Mainline 6.12 drivers for the codec (madera MFD + cs47l92 codec, both cover
   CS47L93), verbatim vendor import + adaptation layer (the Wi-Fi model) for ABOX, MAX98512,
   Maxim DSM, the `star-madera` machine driver and `extcon-madera` (jack detection, not in
   mainline). Rough size: ~33k lines imported, ~5-7k lines of adaptation and new glue.
5. **Safety.** The 4.9 MAX98512 driver writes boost voltage, boost current limit, brownout
   limiter and gain registers **at probe time** (soft reset, then ~45 writes,
   `K49/sound/soc/codecs/max98512.c:2156-2330`), before any playback. So even binding the amp
   driver is owner-gated. The speaker is protected only while ABOX runs DSM with live I/V
   feedback. The plan keeps the amps unbound until A6.
6. **Blocking dependencies.** `pd-aud` genpd (pattern exists for G3D), ABOX SysMMU instance
   (driver exists, needs DT + an unmanaged domain), SPI controller 9810 compat + USI01 in SPI
   mode, HSI2C18 DT node, S2MPS18 LDO34 enable (PMIC write, owner gate), AP CLKOUT0 26 MHz for
   codec MCLK, AUD DVFS (optional at first), `/vendor/firmware` path (done in ROM-RAM), `/efs/maxim`
   calibration files in the ROM-RAM EFS_RO overlay.

---

## 1. Hardware inventory

### 1.1 ABOX (audio DSP subsystem)

| Item | Value | Evidence |
|---|---|---|
| Compatible | `samsung,abox` (+ children `abox-rdma`, `abox-wdma`, `abox-uaif`, `abox-dsif`, `abox-effect`, `abox-debug`, `abox-vss`, `abox-adaptation`, `ext_bin@0..7`) | DT49:9995-10317 |
| SFR | 0x17C50000, 64 KiB (`sfr`) | DT49:9997 (398786560) |
| SYSREG_AUD | 0x17C10000, 64 KiB (`sysreg`); driver writes `MISC_CON` 0x32C = 1, `L2_CACHE_CON` 0x328 only with the VTS-SRAM quirk | DT49:9997; `abox.c:5560-5563`; `abox.h:347-348` |
| SRAM | 0x17D00000, 0x31000 (196 KiB); `calliope_sram.bin` is copied here; IPC mailboxes inside: tx 0x30000, rx 0x30300, tx_ack 0x302FC, rx_ack 0x305FC | DT49:9997,10003-10006; `abox.c:5129-5132` |
| ABOX GIC | `samsung,abox_gic`: GICD 0x17CF1000, GICC 0x17CF2000; AP-side IRQ **SPI 76** | DT49:9985-9993 |
| RDMA (playback) | 8 x 0x100 at 0x17C51000..0x17C51700: id0-2 normal, id3 sync (ION buffer), id4 call, id5 compress (+0x17D30600), id6-7 realtime | DT49:10020-10098 |
| WDMA (capture) | 5 x 0x100 at 0x17C52000..0x17C52400: id0 realtime, id1 normal (ION), id2 call, id3 realtime, **id4 vi-sensing** (amp I/V feedback for DSM) | DT49:10100-10148 |
| UAIF (I2S/TDM ports) | UAIF0 0x17C50500 -> codec AIF1; UAIF1 0x17C50510 -> **MAX98512 pair** (with `abox_adaptation` as platform); UAIF2 0x17C50520 -> codec AIF3 (call/voice); UAIF3 0x17C50530 -> BT (dummy codec); DSIF 0x17C50550 (DSD, dummy codec) | DT49:10150-10210, sound node 12694+ |
| UAIF pins (pinctrl_aud 0x17C60000, inside pd-aud) | I2S0 gpb0-1..4, I2S1 gpb1-0..3, I2S2 gpb1-4..7 (shared with DSD gpb1-4..6), I2S3 gpb2-0..3, codec MCLK gpb0-0 func 2; idle state func 0 | DT49:540-668 |
| Clocks | pll = 669 `PLL_OUT_AUD`, cpu = 656 `GATE_ABOX_DUMMY`, audif = 662 `DOUT_CLK_ABOX_AUDIF`, dmic = 664, bus = 661 `DOUT_CLK_ABOX_ACLK`; UAIFn bclk 665-668 + gates 652-655; DSIF 663/651 | DT49:10011-10012; ids in `T/include/dt-bindings/clock/exynos9810.h:236-257`; registered in `T/drivers/clk/samsung/clk-exynos9810-legacy.c:61-70,386-394` |
| SysMMU | `sysmmu@17E00000`, 0x9000, **SPI 79**, clock 658 `GATE_SMMU_ABOX`, port "Abox", `sysmmu,no-suspend`, qos 15 | DT49:8611-8622 |
| Power domain | `pd-aud@14064000`, cal_id 0xB1380000, `need_smc` 0x17C10204 (DTZPC restore/save target), `power-down-ok` | DT49:3672-3681 |
| AUD DVFS | `devfreq_aud` dfs_id 0xB040009 (`dvfs_aud`), 295..1180 MHz, boot 295 MHz; ABOX `pm_qos_aud` gears 1180000/800000/590000/394000/0 kHz | DT49:9339-9355, 10017; `T/drivers/soc/samsung/cal-if/exynos9810/acpm_dvfs_exynos9810.h:12,28` |
| PMU registers (9810) | ABOX_CPU_CONFIGURATION 0x415C (bit0 LOCAL_PWR_CFG), ABOX_CPU_STATUS 0x4160, ABOX_CPU_OPTION 0x4164 (bit28 ENABLE_CPU), ABOX_CPU_STANDBY 0x3804 (bit28 WFI), GPIO_MODE_ABOX_SYS_PWR_REG 0x1424, PAD_RETENTION_ABOX_OPTION 0x4170, ABOX_MAGIC 0x0814 (8895 only) | `abox.c:81-93` |
| Fixed IOVAs used by the firmware | DRAM fw 0x80000000 (18 MiB, `dma_alloc_coherent`), IVA fw 0x90000000 (512 KiB), RDMA 0x91000000+, WDMA 0x92000000+, COMPR 0x93000000+, VDMA 0x94000000+, VSS fw 0xA0000000 / params 0xA1000000 (modem shared memory), dump 0xD0000000, chip-id page 0x10000000 identity | `abox.h:354-370`; `abox.c:6043-6083` |
| Reserved memory | `abox_rmem` 0xFB200000, 40 MiB (debug/dump only, `abox_debug` memory-region) | DT49:22-27, 10221-10227 |
| Firmware files (V/firmware) | `calliope_sram.bin` 34,784 B (sha256 b24bf5fa...), `calliope_dram.bin` 2,513,712 B (2c26b1c4...), `calliope_iva.bin` 57,728 B (238fac89...); ext_bin (all into DRAM area 1): `dsm.bin` @0x502000 75,024 B (b8f136e6...), `dsm_tune.bin` @0x601000 2,384 B (fac8a16f...), `AP_AUDIO_SLSI.bin` @0x7E0000 102,742 B, `APBargeIn_AUDIO_SLSI.bin` @0x7DC000, `SoundBoosterParam.bin` @0x4FC000, `APDV_AUDIO_SLSI.bin` @0x4D0000, `APBiBF_AUDIO_SLSI.bin` @0x7DF000 | DT49:10239-10307; `sha256sum` on V/firmware |

### 1.2 Codec: Cirrus CS47L93 ("madera" family)

| Item | Value | Evidence |
|---|---|---|
| Bus | SPI `spi@10470000` (USI01 SPI; SW_CONF at 0x1041100C `usi_v2_mode = "spi"`), SPI 400, CS0, 25 MHz, `samsung,spi-feedback-delay = 1`, PIO (no dmas) | DT49 spi@10470000 (~7474-7491), usi@1041100c 5400-5407; clocks GATE_USI01 (203) + USI01 (222) |
| SPI pins | gpp0-4/5/6 func 2 (bus), gpp0-7 func 2 (CS) | DT49 `spi2-bus`/`spi2-cs-func` |
| IRQ | gpa2-2 (wakeup EINT, function 0xF, no pull) | DT49:451-459, `interrupts = <2 0 0>`, parent gpa2 (GIC SPI 52-59 per pin) |
| Reset | `reset-gpios` gpg1-0, output | DT49:2319-2327, 7505 |
| Supplies | AVDD, DBVDD1, CPVDD1 = S2MPS18 **LDO33** `vdd_pmic_codec_1p8` 1.8 V, always-on, boot-on (L33CTRL 0x6A). CPVDD2, DCVDD = S2MPS18 **LDO34** `vdd_codec_1p2` 1.2 V, **not always-on** (L34CTRL 0x6B; enable bits 7:6 = 0b11; enable-ramp 5 ms) | DT49:7500-7504, 9096-9115; `K49/include/linux/mfd/samsung/s2mps18-private.h:140-141,251,272`; `K49/drivers/regulator/s2mps18.c:1183` |
| MCLK | AP CLKOUT0 (`xclkout` = 921 = `OSC_AUD`, CLK_CLKOUT_BASE+1) on gpb0-0; FLL1 ref = MCLK1 26 MHz; FLL2 ref = source 8 (AIF bit clock) | DT49 sound node: `cirrus,fll1-refclk = <1 0 26000000>`, `cirrus,fll2-refclk = <2 8 26000000>`; `T` header line 331-333 |
| Clocking | SYSCLK 98.304 MHz (FLL1), ASYNCCLK 98.304 MHz (FLL2), DSPCLK 147.456 MHz, OUTCLK | DT49 sound node |
| Pdata | `cirrus,inmode = <2 2 0 0 2 2 1 1>`, `cirrus,out-mono = <0 0 1 0 0 0>`, `cirrus,dmic-ref = <0 1 1 1>`, `cirrus,auxpdm-falling-edge`, `cirrus,gpsw = <3 0>`, GPIO9/10 io, AIF1/AIF3 pin groups | DT49:7505-7547 |
| MIC bias | MICVDD 3.0 V (internal CP2), MICBIAS1 1.8 V ext-cap (1A/1B/1C), MICBIAS2 2.8 V ext-cap soft-start (2A) | DT49:7550-7583 |
| Codec DSP firmware | only `cs47l92-dsp1-trace.wmfw` (9,312 B) present; the DT also names "dsd" but no such file ships. Not needed for basic playback/capture | DT49:7612-7637; V/firmware |
| Outputs used | HPOUT2L/R = 3.5 mm headphone. HPOUT3 is declared as "RECEIVER" in the card routing and OUT3 is mono, but Lineage never routes HPOUT3; the earpiece goes through the amp (section 1.3). **GUESS:** HPOUT3 may be unconnected on crown, or wired to the same receiver (a Star board leftover). Never enable HPOUT3 until proven | DT49 sound `samsung,routing`; `V/etc/mixer_paths.xml` (no HPOUT3 control) |
| Mic inputs | DMIC1 = IN3L (MICBIAS1A), DMIC2 = IN2AL (MICBIAS1B), DMIC3 = IN3R (MICBIAS1C), DMIC4 = IN4L (MICBIAS1B), headset mic = IN1BL (MICBIAS2A), FM = IN2BL/R; main mic path AIF1TX1/2 <- IN3L/IN3R, sub mic IN4L | DT49 sound routing; mixer_paths 323-342 |
| Jack detect | Cirrus accessory detect on the codec (`cirrus,accdet` acc@1: JD2, micd ranges, HP impedance detect, moisture detect on pin 7, hpd-pins <2 5 0 0>); 4.9 driver `extcon-madera.c` (3,666 lines) reports extcon + input `SW_HEADPHONE_INSERT`/`SW_MICROPHONE_INSERT` + KEY_MEDIA etc. Samsung sysfs `/sys/class/audio/earjack/{state,key_state,mic_adc,select_jack}` | DT49:7585-7611; `K49/drivers/extcon/extcon-madera.c:66-91,576-599`; `V/etc/init/init.samsungexynos9810.rc:248-251` |

### 1.3 Speaker / earpiece amplifiers: 2 x MAX98512

| Item | Value | Evidence |
|---|---|---|
| Bus | `hsi2c@108C0000` (HSI2C18, PERIC1 USI), SPI 430, pins gpp4-4/5 func 2, clocks 275/251 | DT49:6236-6254 |
| Devices | primary (left) **0x39**; `maxim,sub_reg = <58>` -> second amp (right) at **0x3A** (driver creates the second regmap); `maxim,mono_stereo = <1>` (stereo pair), `interleave_mode = <1>` | DT49:6256-6279; `max98512.c:2642-2681` |
| Which amp drives what | "One Stop Mode" enum: Stereo / Stereo Left / Stereo Right / Mono / **Receiver Left** / Receiver Right. Lineage: handset = `Receiver Left` (earpiece = **left amp 0x39**), speaker = `Stereo Right` (bottom speaker = **right amp 0x3A**), speaker2 = `Stereo Left`, dual = `Stereo` | `max98512.c:1693-1696,1270-1302`; `V/etc/mixer_paths.xml:344-358` |
| IRQ pins | `amp-int` gpm2-0, gpm3-0 (pinctrl@14220000), no pull | DT49 ~1325-1334 |
| Reset / enable GPIO | none in DT (amps are powered from the battery rail and gated by register 0x400/0x500 GLOBAL_EN). **GUESS:** VBAT/VPH direct, no regulator in DT | DT49 max98512 node |
| Register map | 16-bit addresses, 8-bit values; two silicon revisions with remapped addresses: rev A ID at 0x0402, GLOBAL_SHDN 0x0400; rev B ID at 0x0600, GLOBAL_SHDN 0x0500 (plus range shifts) | `max98512.c:46-79,2450-2462`; `max98512.h:143-145,266-268` |
| DT tuning | `spk-gain = 5`, `spk-gain-rcv = 1`, `digital-gain-rcv = 52` (0x34, "-3 dB" per driver comment), `sysclk 12.288 MHz`, `vmon_slot 0`, `platform_info <2 0 0 0 0 52>`, `ppr_param_info <3900 3900 3800 3800>`, `boost_step <15 x14, 2, 0>`, `adc_threshold 7`, `registers-of-amp <0x1F 0x00, 0x21 0xC0, 0x2D 0x14, 0x2F 0x00>` | DT49:6256-6277 |
| Protection DSP | Maxim DSM (Dynamic Speaker Management) as an ABOX ERAP module (`dsm.bin` + `dsm_tune.bin` in ABOX DRAM). I/V sense from both amps over UAIF1 TX -> NSRC3 -> WDMA4 ("vi-sensing"); enabled by `ABOX ERAP info DSM On = 1` (`erap-amp-reference`) | `abox_adaptation.c:41-115` (IPC_ERAP REALTIME_EXTRA); mixer_paths 280-287, 492-501 |
| Calibration | `/efs/maxim/rdc_cal`, `rdc_cal_r` (voice-coil DC resistance, per amp), `temp_cal` (ambient at cal); read by the kernel (`maxdsm_cal_read_file`) and **written by the kernel** from sysfs stores (`rdc_store`, `temp_store`) with `O_CREAT\|O_TRUNC` | `K49/include/sound/maxim_dsm_cal.h:20-22`; `maxim_dsm_cal.c:38-90,925-1050` |
| HAL sysfs | `/sys/class/dsm/max98512/*` (dsm_log, spk_excu_*, spk_temp_*, spk_amp_reg_*), `/sys/class/dsm/maxdsm_cal/*`, `/sys/class/dsm/maxdsm_power/*` | `V/etc/init/init.samsungexynos9810.rc:252-310` |

### 1.4 Other audio paths

| Path | Hardware / route | Kernel need | Evidence |
|---|---|---|---|
| BT SCO | BCM4361 PCM <-> ABOX UAIF3 (I2S3 pins gpb2-0..3), dummy codec, 16 kHz default; primary HAL owns SCO (`Bt Sco*` device ports) | ABOX UAIF3 + dummy codec link | mixer_paths 213,557-563; `V/etc/audio_policy_configuration.xml:69-142` |
| BT A2DP / LE | software, `audio.bluetooth.default` + BT audio AIDL | none (BT already works) | V/lib/hw |
| USB-C audio | USB Audio Class via `audio.usb.default`; 4.9 also had ABOX USB offload (`usb_audio_offloading = <1>`, `sound/usb/exynos_usb_audio.c` 651 lines) which Lineage does not use (**GUESS**: the generic USB HAL does not use the offload path) | `CONFIG_SND_USB_AUDIO`, USB **host** mode (MAX77705 CCIC/OTG, not ported yet) | DT49:5329-5330; `V/etc/usb_audio_policy_configuration.xml` |
| DisplayPort audio (DeX) | `dp_audio` link, dummy CPU DAI, PCM link 15 | later, with DP | DT49 sound `dp_audio@0`; mixer_paths `playback_aux_digital_link 15` |
| SPDIF | none on crown (DSIF is a DSD output, dummy codec) | none | DT49 abox_dsif |
| VTS (voice trigger) | own DSP at 0x13870000 (`samsung,vts`, `vts.bin`), own DMIC pins gpt0-0..2, `/dev/vts_fio_dev`, PCM links 13/14 | later (or keep only placeholder links) | DT49:11759-11800; `V/ueventd.rc:189-190` |
| FM radio | IN2BL/R | none on N960F (**GUESS**) | routing |

### 1.5 What the Lineage HAL expects (V)

- HAL: Samsung SLSI `audio.primary.universal9810.so` + `libaudioproxy.so` + tinyalsa,
  `android.hardware.audio.service` as `audioserver` (`V/etc/init/android.hardware.audio.service.rc`).
- ALSA card 0 from `samsung,star-madera`; PCM numbering is the DAI-link order and must not move:
  RDMA0-7 = pcm 0-7, WDMA0-4 = 8-12, VTS-Trigger 13, VTS-Record 14, DP Audio 15, then UAIF0-3,
  DSIF, SIFS0-2 (`K49/sound/soc/samsung/star_madera.c:1080-1336`). mixer_paths uses links
  0 (primary), 2 (low latency), 3 (jam/mmap), 4 (baseband), 5 (offload), 8 (call fwd),
  9 (capture), 10 (baseband capture), 13/14 (VTS), 15 (aux digital), 24/31 (call rec / call mic,
  **GUESS**: not present on crown; the HAL then fails only those features), card 2 = ABOX PCM dump.
- 141 mixer control names (`VABI raw/mixer-ctls.txt`): ~60 `ABOX *`, madera names, amp names
  (`One Stop Mode`, `SPK Analog Left/Right Gain`, `Rcv Digital Gain`, DSM), machine pin switches
  `DMIC1..4 Switch`, `FM Switch` (`star_madera.c:1692-1696`). Vendor-only codec names to check:
  `HPOUT2L/R Impedance Volume`, `MICBias Bypass Mode`, `HPOUT2 EDRE Switch`. libaudioroute skips
  unknown controls with an error, so a missing name breaks that route but not the HAL.
- Device nodes and sysfs: `/dev/snd/pcmC%uD%u%c`, `controlC%u`, `comprC%uD%u`, `hwC0D%d`;
  `/dev/vts_fio_dev`, `/dev/seiren` (ueventd only); `/sys/devices/platform/17c50000.abox/{reset,service}`
  and `0.abox_debug/{gpr,calliope_dram,calliope_iva,calliope_sram}`; `/sys/kernel/debug/abox/`;
  `/data/vendor/log/abox/`; `/sys/class/audio/earjack/*`; `/sys/class/dsm/*`; `/efs/maxim/*`.
- Firmware loader: everything comes from `/vendor/firmware` (int6 already has
  `FW_LOADER_USER_HELPER(_FALLBACK)` for Wi-Fi; integration-6.md section 2).
- Jack events: input switch events from extcon-madera's input device (**GUESS**: Lineage's
  crownlte overlay sets `config_useDevInputEventForAudioJack`; check
  `/product/overlay` or framework-res overlay on SYSTEM.img before A5).

---

## 2. What the 4.9 stack does at boot

Order is by initcall and probe deferral; times are not measured (no 4.9 dmesg with audio lines
was found on gekko or the PC). **GUESS** for exact ordering.

1. **PMIC.** S2MPS18 regulator probe: LDO33 (codec 1.8 V) stays on (always-on, boot-on).
   LDO34 (codec 1.2 V DCVDD) is registered off-by-default and enabled by madera.
2. **pd-aud.** `exynos-pd` registers `pd-aud` and calls `cal_pd_set_smc_id(0xB1380000, 0x17C10204)`
   (`K49/drivers/soc/samsung/exynos-pd.c:334-341`). Every PD on/off runs `pmucal_local_enable/disable`
   (index 0) plus the DTZPC SMC 0x82000410 (restore on power-on, save before power-off). No PMIC
   side effect for AUD (the 0x2A PMIC update is G3D-only, `K49/drivers/soc/samsung/cal-if/cal-if.c:168-187`).
3. **ABOX SysMMU** `sysmmu@17E00000` probes (`no-suspend`).
4. **ABOX probe** (`abox.c:5955-6255`): ION client, map SFR/SYSREG/SRAM, attach its IOMMU domain,
   `dma_alloc_coherent` 18 MiB DRAM firmware buffer + 512 KiB IVA buffer, `iommu_map` them at
   0x80000000 / 0x90000000, map modem VSS shared memory at 0xA0000000/0xA1000000, identity-map
   the chip-id page 0x10000000, get 5 clocks, PM QoS requests (AUD, INT, MIF, CL0, CL1), register
   ABOX-GIC IRQ handlers, regmap, `pm_runtime_get()` (async), register the ASoC component with
   its DAIs, then `of_platform_populate` the children (RDMA/WDMA/UAIF/DSIF/effect/debug/vss/adaptation).
5. **First runtime resume = firmware boot** (`abox_enable`, `abox.c:5531-5605`):
   ABOX-GIC IRQ on; CPU gear max; (8895-only secure SMC skipped); `SYSREG MISC_CON = 1`;
   enable cpu clock; `audif` rate = AUDIF_RATE_HZ; restore regmap; init ABOX GIC (GICC PRIMASK 0xFF,
   GICD CTRL 3, priorities 0x10101010, GICC CTRL 3); UAIF pins "default"; **download firmware**:
   the first time this is deferred (`request_firmware_nowait("calliope_sram.bin")`, then
   `calliope_dram.bin`, `calliope_iva.bin`, the 7 ext_bin files) and returns `-EAGAIN`
   ("SRAM firmware downloading is deferred"); the next enable copies SRAM image into ABOX SRAM
   (`memcpy_toio`), DRAM image into the 18 MiB buffer, IVA image, ext_bins at their DRAM offsets;
   then `abox_request_dram_on`, PMU `ABOX_CPU_CONFIGURATION` bit0 = 1 (CPU power),
   PMU `ABOX_CPU_OPTION` bit28 = 1 (enable CPU) and poll `ABOX_CPU_STATUS` bit0, then pad
   retention release (`PAD_RETENTION_ABOX_OPTION` bit28, `GPIO_MODE_ABOX_SYS_PWR_REG` bit0).
   The DSP answers over IPC: `"Calliope is ready to sing (version:%c%c%c%c)"` (`abox.c:4462-4473`),
   then `abox_boot_done_work_func` sends `ABOX_RESUME`, restores component data, drops the gear.
   Runtime suspend (`abox_disable`) sends `ABOX_SUSPEND`, waits for WFI in `ABOX_CPU_STANDBY`,
   saves registers, idles the pins, re-asserts pad retention. Autosuspend 500 ms.
6. **Codec** (madera SPI): enable AVDD/DBVDD1/CPVDD1, assert reset gpg1-0, enable DCVDD (LDO34),
   release reset, wait for boot, read device ID, apply the CS47L92/93 register patch, register
   the codec, pinctrl-madera (GPIO defaults), arizona-micsupp (MICVDD), extcon-madera (accessory
   detect, HP impedance, moisture). Codec DSP firmware (`trace` wmfw) loads on demand only.
7. **Amps** (`max98512_i2c_probe` -> `max98512_probe`, `max98512.c:2156-2330`): read rev ID,
   read BOOST_CTRL0, **soft reset**, write MEAS_EN (VI sense on), PCM TX Hi-Z, monomix,
   initial volume 0x40, SPK_GAIN 5, DC blocker on, IMON/VMON DC blocker, **BOOST_CTRL0 = 0x1C
   (boost output voltage), BOOST_CTRL1 = 0x3E (boost current limit), BOOST_CTRL3 = 0x01**,
   measurement ADC config, squelch, **brownout enable 0x07, levels, current limits, gain
   reductions**, DT `registers-of-amp`. GLOBAL_EN stays 0. Maxim DSM, DSM cal and DSM power
   classes register (`/sys/class/dsm/*`).
8. **Card** `samsung,star-madera` binds when all components exist; `star_late_probe`
   (`star_madera.c:1011`) sets codec clocks from `xclkout` (AP CLKOUT0 26 MHz) and registers
   the jack sysfs callback (`register_madera_jack_cb`).
9. **Userspace.** ueventd sets `/dev/vts_fio_dev`; init chowns DSM/earjack/abox sysfs and
   creates `/efs/maxim` files (`init.samsungexynos9810.rc:247-310`); the HAL opens card 0,
   loads `mixer_paths.xml` defaults (ABOX rates, ASRC, codec EQ/OSR/ramps), and at each
   speaker use enables `erap-amp-reference` (DSM on) and the VI path.
10. **DSM at playback.** `maxim_dsm` sends parameters (incl. rdc/temp calibration read from
    `/efs/maxim`) to the ERAP module over ABOX IPC (`maxim_dsm_write`, `abox_adaptation.c`);
    the amps are enabled by `__max98512_spk_enable` (`max98512.c:1240-1410`): gains, MEAS_EN,
    brownout thresholds by battery temperature (> 50 C: 0x30/0x10 else 0x40/0x20), boost mode
    (rev B: automatic bypass, else bypass off), PCM RX enable, AMP_EN, DEM, GLOBAL_EN.

---

## 3. Porting strategy

### 3.1 Decision per block

| Block | Choice | Why |
|---|---|---|
| Madera MFD + CS47L93 codec + pinctrl + MICVDD | **Mainline 6.12** (`drivers/mfd/madera-*.c` with `cirrus,cs47l93` at `T/drivers/mfd/madera-core.c:382`, `sound/soc/codecs/cs47l92.c` + `madera.c`, `drivers/pinctrl/cirrus/pinctrl-cs47l92.c`, `regulator/arizona-micsupp.c`) | same register map, maintained, 6.12 APIs already right. DT property names match the 4.9 node (`cirrus,inmode`, `cirrus,out-mono`, `cirrus,dmic-ref`, `cirrus,gpsw`, supply names). Diff the HAL control names at A3 |
| Jack detection | **Vendor import** `extcon-madera.c` (+ pdata headers, 4.0k lines), adapted to mainline madera core (mainline keeps the hooks it needs: `out_clamp`, `out_shorted`, `hp_ena` in `T/include/linux/mfd/madera/core.h:167-206`); plus `jack_madera_sysfs_cb.c` (125) for `/sys/class/audio/earjack` | mainline has no madera accessory detect (0 `micd`/`jack` hits in `T/sound/soc/codecs/madera.c`) |
| ABOX | **Vendor import + adaptation layer** (`sound/soc/samsung/abox/` 14,081 + 1,900 header lines) | no mainline driver; firmware ABI (IPC, fixed IOVAs, register defaults) is in this code |
| VTS | defer; keep two placeholder DAI links (dummy) so PCM numbering stays | not needed for calls/media |
| Machine driver | **Port** `star_madera.c` (2,018) to the 6.12 card API, keep the link order and control names | HAL ABI |
| MAX98512 + Maxim DSM | **Vendor import + adaptation** (`max98512.c` 2,939 + `.h` 619, `maxim_dsm.c` 3,434, `maxim_dsm_cal.c` 1,377, `maxim_dsm_power.c` 726, headers 826) | mainline has max98520/98373/98390, not 98512; the DSM ABI (`/sys/class/dsm`, ERAP param offsets) is vendor |
| USB audio | mainline `SND_USB_AUDIO`; no ABOX offload | Lineage uses `audio.usb.default` |

### 3.2 Adaptation layer (like `linuxver_crownlte612.h` for Wi-Fi)

One header plus small `.c` shims under `sound/soc/samsung/abox/crownlte/`, so the vendor files
change as little as possible. Main 4.9 -> 6.12 breakages:

| Area | 4.9 use | 6.12 replacement |
|---|---|---|
| ASoC core | `snd_soc_codec`, `snd_soc_platform`, `snd_soc_register_codec/platform`, `snd_soc_codec_get_drvdata`, `snd_soc_add_codec_controls`, `codec->control_data`, `cache_bypass` (abox_rdma 32 uses, vdma 16, wdma 12, dump 9, adaptation, max98512, star_madera) | `snd_soc_component` everywhere; PCM ops move into `snd_soc_component_driver` (`.open/.hw_params/.trigger/.pointer(component, substream)`, `.pcm_construct`); `snd_pcm_set_managed_buffer`; `snd_pcm_lib_ioctl` gone |
| DAI links | `codec_name`, `platform_name = "17c51000.abox_rdma"`, `rtd->codec_dai`, `rtd->cpu_dai` | `SND_SOC_DAILINK_DEFS` / `snd_soc_dai_link_component`; `snd_soc_rtd_to_codec()/to_cpu()` |
| Compress offload | 4.9 compr ops on the platform | `snd_compress_ops` in the component |
| IOMMU | `get_domain_from_dev`, `iovmm_set_fault_handler`, `exynos_sysmmu_tlb_invalidate`, `iommu_map(..., prot)` | own unmanaged domain: `iommu_paging_domain_alloc(dev)` + `iommu_attach_device()` with `driver_managed_dma = true`; `iommu_map(..., prot, GFP_KERNEL)`; `iommu_set_fault_handler`; TLB flush is inside the 9810 SysMMU driver (`T/drivers/iommu/exynos-iommu.c`). The firmware needs fixed IOVAs, so the ABOX cannot use the DMA-API default domain the DPU uses |
| ION | `exynos_ion_client_create`, ION buffers for RDMA3/WDMA1 (mmap / AAudio) | drop ION: set those DMAs to `buffer_type = "dma"` in DT; `abox_mmapfd.c` via `dma_buf` export later if AAudio MMAP is wanted |
| PM QoS | `pm_qos_add_request(PM_QOS_AUD_THROUGHPUT / DEVICE / BUS / CLUSTER0_FREQ_MIN / CLUSTER1)` | AUD: new `dvfs_aud` devfreq in `exynos9810-acpm-devfreq.c` + `dev_pm_qos`; INT/MIF via the int6 devfreq (`exynos9810-legacy-qos`); CPU mins via `freq_qos`. First stages: stubs (fixed boot level 295 MHz) |
| PMU / SMC | `exynos_pmu_read/update`, `exynos_smc` | syscon regmap of the PMU (as the int6 PM code), `arm_smccc_smc`; DTZPC through `exynos9810-cal-smc.c` |
| Power domain | `samsung,power-domain = <&pd_aud>` (exynos-pd genpd) | genpd provider for pd-aud only, cloned from the Mali G3D PD code (`T/drivers/gpu/arm/exynos9810/mali_exynos9810_pd.c`, `g3d-power-domain.md`): `cal_pd_status`, `cal_pd_set_smc_id(0xB1380000, 0x17C10204)`, `cal_pd_control` |
| Firmware | `request_firmware_nowait(..., FW_ACTION_HOTPLUG, ...)` | `FW_ACTION_UEVENT` |
| File IO in DSM cal | `set_fs(KERNEL_DS)` + `filp_open` + `vfs_read/vfs_write` | read: `kernel_read_file_from_path()`; **write: removed** (see 4.3) |
| Modem / VSS | `shm_get_vss_base/size`, `shm_get_vparam_*`, `register_modem_event_notifier` | stub: no VSS mapping and no call path until the modem is ported (**GUESS**: the firmware touches VSS only in call mode) |
| Misc | `itmon_notifier_chain_register`, `setup_timer`, `of_find_device_by_node` refcounts, `devm_request_and_map_byname` vendor helper, `pm_wakeup_event`, panic notifier, `__clk_get_enable_count` | stub / `timer_setup` / `put_device` / `devm_platform_ioremap_resource_byname` / unchanged |
| SPI | vendor `samsung,exynos-spi` with `swap-mode`, `controller-data` | mainline `spi-s3c64xx` needs a 9810 compat (FIFO 64, port config) and USI01 SW_CONF = SPI in `exynos-usi.c` (9810 variant exists for I2C) |

Rough size: imported ~33k lines (ABOX 16k, amps+DSM 10k, extcon 4.4k, machine 2k, sysfs 0.5k);
new/adapted ~5-7k (machine driver rewrite ~1.5k, ASoC component conversion ~2k, IOMMU/PM/PD/
QoS/SPI glue ~1.5k, self-tests ~1k). Larger than Wi-Fi in API churn (ASoC changed a lot between
4.9 and 6.12), smaller in code.

### 3.3 Main risks

| # | Risk | Effect | Mitigation |
|---|---|---|---|
| R1 | Speaker overdrive / overheating without DSM | **hardware damage** | amps unbound until A6; A6 only with DSM proven live (section 4) |
| R2 | `pd-aud` powered off while something touches AUD registers. `pinctrl_aud` (0x17C60000) is in pd-aud and is already enabled in int6 (`T/arch/arm64/boot/dts/exynos/exynos9810.dtsi:462-465`) without a power domain | SError / bus hang | A1 reads the PD status first. Give `pinctrl_aud` `power-domains = <&pd_aud>` or keep pd-aud always on until the PD driver exists. **GUESS:** SBoot leaves pd-aud on (int6 S0c suspended/resumed all devices, which saves pinctrl_aud registers, without a fault) |
| R3 | ABOX DRAM firmware buffer, IOVA layout or IPC offsets differ under 6.12 allocator | DSP crash / IOMMU fault | fixed IOVAs as 4.9; fault handler logs and never panics (9810 SysMMU driver behaviour) |
| R4 | Codec DCVDD rail is a PMIC write | wrong voltage would damage the codec | enable-bits-only update with vsel check (section 4.2, G1) |
| R5 | ASoC conversion bugs move PCM numbers | HAL opens the wrong device (e.g. speaker path instead of headset) | A5 PASS requires the exact 4.9 link table; amp links stay dummy until A6 |
| R6 | Lineage HAL calls `erap-dsm-bypass` or sets amp controls before DSM is ready | unprotected speaker | amps unbound in A5; at A6 a kernel gate refuses amp GLOBAL_EN unless DSM is live |
| R7 | DSM calibration files writable | `/efs` corruption | kernel never writes `/efs`; ROM-RAM `/efs` is a read-only tmpfs |
| R8 | HPOUT3 wiring unknown | double-driving the earpiece, or nothing | never route HPOUT3 |

---

## 4. Safety

### 4.1 Writes that can harm hardware

**MAX98512 (both 0x39 and 0x3A; addresses for rev A, rev B remapped per `max98512.c:46-79`):**

| Register | Function | Harm if wrong | 4.9 value |
|---|---|---|---|
| 0x0400 / rev B 0x0500 GLOBAL_SHDN (EN) | amp on/off | starts driving the coil | 0 at probe, 1 only in `__max98512_spk_enable` |
| 0x0038 AMP_EN (+ DEM bits) | amp channel on | as above | per mode |
| 0x003E BOOST_CTRL0 | boost output voltage | higher voltage = more power into the coil | 0x1C at probe (**GUESS** ~10 V: driver comments give 0x0C = 8 V, 0x10 = 8.5 V, i.e. 6.5 V + 0.125 V/step); per-volume `boost_step` table from DT |
| 0x0040 BOOST_CTRL1, 0x003F BOOST_CTRL3, 0x0087 BOOST_BYPASS_2 | boost current limit, boost mode | over-current, battery brownout | 0x3E, 0x01, rev-dependent |
| 0x0035 AMP_VOL_CTRL, 0x003A SPK_GAIN | digital volume, analog gain | over-excursion | 0x40 init; gain 5 (speaker) / 1 (receiver); digital gain rcv 0x34 |
| 0x0036 AMP_DSP_CFG (DC blocker), 0x0037 TONE_GEN_DC_CFG | DC blocking, tone/DC generator | **DC into the voice coil** = heating | DC blocker 0x01 (on); 0x37 never written |
| 0x003C MEAS_EN, 0x003D MEAS_DSP_CFG, 0x001A-0x001F PCM TX | I/V sense on, its slots | DSM goes blind (no protection while playing) | VI on (0x003C), slots from DT |
| 0x0014-0x0016 thermal warn / shutdown / hysteresis | die over-temperature protection | amp keeps running too hot | never written (POR 0x75/0x8C/0x08) |
| 0x0050-0x007F brownout enable, thresholds, current limits, gain reduction | battery-sag protection | brownout reset of the phone, amp clipping | 0x07, 0xA0/0x30/0x10 (or 0x40/0x20 by battery temp), etc. |
| 0x0012 WDOG_CTRL | amp watchdog | — | 0 |
| 0x0401 / 0x0501 SOFT_RESET | reset | restarts the amp with POR settings (harmless but a write) | at probe |
| DT `registers-of-amp` | 0x1F=0x00, 0x21=0xC0, 0x2D=0x14, 0x2F=0x00 | slot/ICC config | at probe |

**Maxim DSM (inside ABOX):** every ERAP parameter write (`maxim_dsm_write` -> IPC_ERAP),
including the calibration values (rdc, temp), thermal and excursion limits, `ABOX ERAP info DSM On`
and `ABOX ERAP info Bypass On`, and the content of `dsm.bin` / `dsm_tune.bin`. Writable sysfs in
`/sys/class/dsm/{max98512,maxdsm_cal,maxdsm_power}` (e.g. `target_temp`, `threshold_level`,
`rdc`, `temp`, calibration start).

**Calibration storage:** `/efs/maxim/{rdc_cal,rdc_cal_r,temp_cal}`. The 4.9 sysfs stores write
these files from the kernel. A wrong rdc makes DSM mis-estimate coil temperature.

**PMIC:** S2MPS18 L33CTRL 0x6A and L34CTRL 0x6B (codec rails). Only bits 7:6 (enable) may change;
vsel must read 1.8 V / 1.2 V before any enable.

**Codec (no damage path to the phone, but ear safety and possible double drive):** HPOUT2 volume
and EDRE; HPOUT3 routing (R8); MICBIAS voltages are fixed by DT.

**ABOX / PMU:** no damage path known (DSP RAM is volatile, PMU writes are the 4.9 values), but
the pad-retention registers decide whether the UAIF pins toggle; with amps unbound this cannot
reach a speaker.

### 4.2 How 4.9 protects the speaker

1. **DSM (main protection).** The Maxim DSM module in the ABOX firmware reads per-sample current
   and voltage from both amps (amp I/V slots -> UAIF1 TX -> NSRC3 -> WDMA4 "vi-sensing"),
   models voice-coil temperature (using `rdc_cal`/`temp_cal`) and cone excursion, and limits the
   signal before it goes to UAIF1. The HAL enables it per route (`erap-amp-reference` =
   `ABOX ERAP info DSM On`) for every speaker route; receiver routes do not use it.
2. **maxdsm_power** (`maxim_dsm_power.c`): long-term power/temperature policy through sysfs
   (target temp, threshold level, cutoff frequency).
3. **Amp hardware:** die thermal warning/shutdown at POR thresholds, brownout limiter
   (3 levels, battery-temperature-dependent thresholds), boost current limit, DC blockers, clock
   monitor (no BCLK -> no output), squelch.
4. **Receiver mode:** VI sense off (`vimon = 0`), DSM not in the loop; protection is the lower
   fixed gain (rcv digital gain 0x34, spk gain 1 vs 5) and the DEM setting 0x40.
5. **Battery temperature** feeds the brownout thresholds (`maxdsm_cal_get_temp_from_power_supply`,
   power_supply "battery"; int6 has the MAX77705 battery).

There is no protection if: the amp is enabled while ABOX/DSM is not running, `DSM On` is 0 or
`Bypass On` is 1 on a speaker route, VI sense is off, or the calibration is missing/wrong.

### 4.3 Rules for every stage

- **Amps:** no MAX98512 driver bound and no amp DT node with a compatible before A6. A1 does
  I2C **reads only** with a whitelist (rev ID at 0x0402 and 0x0600, GLOBAL_SHDN at 0x0400 and
  0x0500, INT_RAW 0x0001-0x0003). A 16-bit-address read sends the address bytes, which is not a
  register write.
- **Kernel gate at A6:** the ported amp driver refuses `GLOBAL_EN = 1` in any speaker mode unless
  (a) ABOX state is ENABLED, (b) `DSM On` = 1 and `Bypass On` = 0 have been acknowledged by
  the firmware, (c) WDMA4 VI data arrived within the last 100 ms, (d) rdc/temp calibration loaded
  and in range. Receiver mode only needs (a). Refusal logs and keeps the amp off.
- **Volume cap:** a module parameter caps amp digital volume / gain at the 4.9 values (or lower),
  and a test cap for A6 (e.g. -30 dB digital) that only a rebuild can lift.
- **No `/efs` writes:** remove `maxdsm_cal_write_file` and every store that calls it; the
  calibration sysfs becomes read-only. Calibration (rdc measurement) is never run.
- **Never enable HPOUT3** (R8). Never write 0x0037 (tone/DC) on the amps.
- **PMIC:** LDO34 enable through the sealed ACPM policy only (pattern of the touch rails,
  `T/Documentation/crownlte/power-gates.md` section 1): FUNC_UPDATE type 1, reg 0x6B, mask 0xC0,
  value 0xC0, precondition vsel(0x6B) = the 4.9 1.2 V code and L33 (0x6A) already on; at most 2
  enables per boot; off on probe failure and shutdown.

### 4.4 Owner gates

| Gate | Step | What the owner approves |
|---|---|---|
| **[OWNER GATE G1]** | A3 | One PMIC write class: S2MPS18 LDO34 (codec DCVDD 1.2 V) enable bits, vsel checked, as 4.9 |
| **[OWNER GATE G2]** | A4 | First audible output: codec tone into a **headset held away from the ears**, fixed low level. No amp write. Owner plugs the headset (physical help) |
| **[OWNER GATE G3]** | A6a | Any MAX98512 write: binding the amp driver (soft reset + probe writes incl. boost/brownout), then earpiece (receiver mode, left amp only) at capped volume |
| **[OWNER GATE G4]** | A6b | Speaker playback with DSM live, capped volume, short tone, then music |
| **[OWNER GATE G5]** | any | Changing any DSM parameter, `Bypass On`, boost/thermal registers away from 4.9 values, or providing calibration other than the files copied read-only from 4.9 `/efs/maxim` |

---

## 5. Staged plan

All stages use the delayed-UFS RECOVERY trial procedure (serial READY, 120 s auto-return) or
ROM-RAM, as in the existing runbooks. Each stage is a DT opt-in, so int6 stays unchanged
without it.

### A1. Read-only probe (no writes except what int6 already does)

Build: diag DT nodes, self-test `audio selftest:` in a small read-only driver.

Reads:
- PMU: pd-aud status `AUD_STATUS` 0x14064004, bits 3:0 = 0xF when on (the value `aud_on`
  waits for, `K49/drivers/soc/samsung/cal-if/exynos9810/flexpmu_cal_local_exynos9810.h:4-5`;
  `AUD_CONFIGURATION` 0x14064000), ABOX_CPU_STATUS 0x4160, ABOX_CPU_OPTION 0x4164, ABOX_CPU_STANDBY 0x3804, PAD_RETENTION_ABOX_OPTION 0x4170.
- Only if pd-aud is on: ABOX SFR version/ID words at 0x17C50000 (first 0x10 bytes), SYSREG MISC_CON
  0x17C1032C, ABOX SysMMU VERSION (0x17E00000 + version offset, read through the 9810 SysMMU
  probe which already refuses non-v7 and logs CTRL), pinctrl_aud CON registers for gpb0-gpb2.
- PMIC (ACPM read): L33CTRL 0x6A, L34CTRL 0x6B.
- GPIO levels: codec reset gpg1-0, codec IRQ gpa2-2, amp-int gpm2-0/gpm3-0.
- HSI2C18 (0x108C0000) bring-up as int6 does for other HSI2C buses, then whitelisted reads on
  0x39 and 0x3A: 0x0402, 0x0600, 0x0400, 0x0500, 0x0001-0x0003.
- No SPI traffic yet (codec DCVDD may be off).

PASS line: `audio selftest A1: PASS pd_aud=<on|off> abox_cpu=<off|on> l33=0x.. (1.8V on) l34=0x.. amp39 rev=0x.. en=0 amp3a rev=0x.. en=0 writes=0`

Expected: LDO33 on, LDO34 off (**GUESS**), both amps GLOBAL_EN = 0, ABOX CPU off.

Failure signatures:
- no line / SError at the ABOX SFR read -> pd-aud was off; the self-test must not read SFRs when
  `cal_pd_status` says off (bug).
- I2C NACK at 0x39/0x3A -> wrong bus/pins/USI mode, or the amp has no supply (record, do not retry
  writes).
- amp GLOBAL_EN = 1 -> warm state left over from 4.9; report and stop (owner decides; do not write).
- L33 off -> codec has no I/O supply; stop.

### A2. ABOX firmware boot (volatile DSP RAM only)

Needs: pd-aud genpd (or verified always-on), ABOX SysMMU DT node + unmanaged domain,
ABOX core + GIC + RDMA/WDMA/UAIF drivers (no machine card yet, or a card with dummy codecs),
firmware from `/vendor/firmware` (ROM-RAM) or the initramfs (delayed-UFS diag; the files are
proprietary, keep the pack private). No codec, no amp.

Writes: PMU ABOX CPU power/enable + pad retention (4.9 values), SYSREG MISC_CON, ABOX GIC,
ABOX SRAM/DRAM contents, SysMMU page tables, DTZPC SMC on PD transitions, AUD clocks.
All volatile; a reboot clears them.

PASS lines:
- `abox: SRAM firmware loaded`, `calliope_dram.bin is loaded`, `calliope_iva.bin is loaded`, ext_bin loaded x7
- `Calliope is ready to sing (version:XXXX)` within 2 s of `abox_cpu_enable(1)`
- `/sys/devices/platform/17c50000.abox/calliope_version` readable
- runtime suspend/resume: `ABOX_SUSPEND` acked (WFI seen), resume boots again (`ready to sing` x2)
- 0 SysMMU faults
- `audio selftest A2: PASS boots=2 faults=0`

Failure signatures:
- `SRAM firmware downloading is deferred` and nothing after -> firmware path (user helper /
  ueventd) not working in this image.
- `abox cpu enable timeout` -> PMU sequence or pd-aud/clock missing.
- no `ready to sing`, no ABOX-GIC IRQ 76 -> GIC programming, IPC offsets, or the DSP crashed:
  read `ABOX_CPU_PC` (SFR 0x2C3C) and the GPRs via `abox_debug` (the 4.9 failsafe dumps them).
- `abox_iommu_fault_handler` lines (IOVA 0x80000000.. / 0x10000000 / 0xA0000000) -> missing
  mapping (VSS stub is the likely one) or wrong domain type.
- SError on SFR access -> pd-aud off or clock gated.

### A3. Codec probe **[OWNER GATE G1]**

Needs: SPI s3c64xx 9810 compat + USI01 SPI mode, mainline madera/cs47l93 DT node with the 4.9
properties, LDO34 enable through the sealed ACPM policy, AP CLKOUT0 (OSC_AUD) clock, pinctrl
gpb0-0 MCLK function. Machine card with UAIF0/UAIF2 -> codec, UAIF1 -> **dummy** codec (keeps
numbering), UAIF3 -> dummy (BT). extcon-madera can come here or in A5.

Writes: PMIC 0x6B bits 7:6 (G1); codec register writes (reset, patch, defaults; codec outputs
stay disabled).

PASS lines:
- `acpm pmic: L34 on 0x6B: 0x.. -> 0x..` (vsel unchanged)
- `cs47l93 spi0.0: CS47L93 revision X` (madera-core ID read; silicon ID `CS47L92_SILICON_ID`
  0x6371 covers CS42L92/CS47L92/CS47L93, `T/drivers/mfd/madera-core.c:33,652-660`)
- card registered: `/proc/asound/cards` shows card 0, `/proc/asound/pcm` lists 00-0 .. 15-0 in
  the 4.9 order (section 1.5)
- `tinymix` dump: diff of control names against `VABI raw/mixer-ctls.txt` printed (missing names listed)
- `audio selftest A3: PASS dcvdd=on id=0x6371 rev=.. links=<n> missing_ctls=<n>`

Failure signatures:
- `madera: Failed to read ID` / `Unknown device ID` -> SPI mode/pins/feedback delay, DCVDD off,
  reset polarity.
- `-EPROBE_DEFER` loops on DCVDD -> policy not sealed or LDO34 refused (log shows which precondition).
- madera IRQ storm on gpa2-2 -> wrong trigger type (4.9 cell is 0; madera wants level low, **GUESS**).
- card does not register -> a DAI-link component missing (dummy for UAIF1/3, VTS, DP).

### A4. Headset tone **[OWNER GATE G2]**

Two parts, both headset only (codec HPOUT2), amps unbound.

- **A4a codec-only tone.** madera Tone Generator 1 -> HPOUT2L/R mixer input, SYSCLK from FLL1
  (MCLK1 26 MHz), `HPOUT2 Digital Volume` at a fixed low level (e.g. -40 dB), 1 kHz for 3 s, then
  all off. Uses no ABOX. Owner holds the headset near (not in) the ear, or uses a cheap headset /
  a 3.5 mm plug into a PC line-in for a recording.
- **A4b ABOX to headset.** `tinyplay` a 1 kHz -20 dBFS 48 kHz file on pcm 0 (RDMA0) with only
  `media-headset` controls set (`route-sifs0-to-uaif0`, `HPOUT2L/R Input 1 = AIF1RX1/2`), same
  volume cap.
- Optional A4c: headset mic and DMIC capture (`route-main-mic`, pcm 9) for 3 s -> RMS > noise floor.

PASS lines:
- A4a: `audio selftest A4a: PASS fll1=locked sysclk=98304000 hpout2=on 3000ms` + owner hears the tone
- A4b: `tinyplay` exits 0, no xrun, `ABOX ... RDMA0 ... start/stop`, owner hears the tone
- jack: if extcon-madera is in, insert/remove gives `SW_HEADPHONE_INSERT` 1/0 and HP impedance in
  dmesg

Failure signatures: FLL not locked (no MCLK: CLKOUT0 or gpb0-0 function); HPOUT2 short-circuit
flag (`out_shorted`); DMA underrun / no IRQ from RDMA (ABOX IPC); silence with counters running
(UAIF0 format/master mismatch: codec must be clock slave or master exactly as 4.9).

### A5. Audio HAL in ROM-RAM (amps still unbound)

Needs: ROM-RAM pack with the audio DT, `/vendor` (HAL, mixer_paths, firmware), EFS_RO_DIR extended
with `/efs/maxim/{rdc_cal,rdc_cal_r,temp_cal}` copied read-only from 4.9 (needed only at A6, but
the HAL may read them), extcon-madera input device, earjack sysfs, `/dev/vts_fio_dev` absent or
a stub. `max98512` link bound to a dummy codec: the HAL's speaker controls (`One Stop Mode`,
gains, DSM) are missing, so speaker/earpiece routes do nothing.

PASS lines:
- `android.hardware.audio.service` stays up (no crash loop), `dumpsys media.audio_flinger` lists
  the primary module with outputs
- headset plugged: Android routes media to the wired headset; a ringtone / media sample plays
  at low volume (owner holds the headset)
- `cmd media.audio_flinger`/`dumpsys audio` shows `AUDIO_DEVICE_OUT_WIRED_HEADSET` on insert
- headset mic capture works in a recorder app
- `audio selftest A5: PASS amp=unbound speaker_routes_dropped=<n>` (driver counter of refused
  speaker route attempts)

Failure signatures: HAL `cannot open device` on pcm N (link order wrong); `mixer_get_ctl_by_name`
failures for ABOX controls (ABOX component name mismatch); HAL crash in `libaudioproxy` at
`calliope_*` sysfs (missing abox_debug files); audioserver restarts every few seconds; no jack
event (overlay uses a different jack mechanism; then add extcon `h2w` or input as needed).

### A6. Earpiece and speaker (amps) **[OWNER GATE G3] then [OWNER GATE G4]**

- **A6-pre (G3):** bind the ported MAX98512 driver with the kernel gate and volume cap (4.3).
  PASS: `max98512 rev A|B left 0x39 right 0x3a`, probe writes logged as a list and read back
  equal to the 4.9 values, GLOBAL_EN = 0 on both, `/sys/class/dsm/*` present, `/efs` untouched.
- **A6a earpiece (G3):** receiver mode (`One Stop Mode = Receiver Left`, left amp only), HAL
  call-less `media-handset` route or `tinyplay` with the same controls, digital volume capped at
  the 4.9 receiver value or lower, 1 kHz 3 s. PASS: owner hears the earpiece; right amp stays
  GLOBAL_EN = 0 (read back); no brownout/thermal IRQ on amp-int.
- **A6b speaker (G4):** first prove DSM is live with the amp *off*: `ABOX ERAP info DSM On = 1`,
  DSM parameters loaded (`maxim_dsm` read-back of rdc/temp equals the `/efs/maxim` values),
  `dsm_log` readable. Then speaker mode (`Stereo Right`, bottom speaker) at a capped level
  (-30 dB digital), 1 kHz 2 s; check the gate saw VI data (WDMA4 frames > 0, I/V values
  non-zero, coil temperature estimate plausible ~ ambient), then lift the cap to the 4.9 value
  in a second run, then dual speaker, then music through the HAL.
  PASS lines: `max98512 gate: speaker enable allowed dsm=on vi_age=<ms> rdc=0x.. temp=0x..`,
  `dsm_log` temperature rise small (< 10 C over 30 s at normal volume), no amp IRQ.

Failure signatures: gate refuses (`dsm=off` / `vi_age` too old) -> do not override, debug the VI
path (UAIF1 TX slots, NSRC3 -> WDMA4, interleave mode); amp-int IRQ with INT_RAW thermal/brownout
bits -> stop, owner decision; I2C errors mid-play -> stop and power-off the amps (GLOBAL_EN = 0
on both addresses).

### Later stages (not in this plan's gates)

BT SCO (UAIF3, after A5), USB audio (needs OTG host), VTS, DP audio, AUD DVFS gears, ABOX
runtime PM with system suspend (needs the suspend S1 work), AAudio MMAP (`abox_mmapfd` with
dma-buf), calls (modem + VSS).

---

## 6. Dependencies on things not yet ported

| Dependency | Needed by | State in int6 (4148bf525) | Work |
|---|---|---|---|
| `pd-aud` genpd provider (cal_id 0xB1380000, DTZPC 0x17C10204) | A2 (A1 reads status only) | PD code exists only inside the Mali driver (`mali_exynos9810_pd.c`); `cal_pd_*` + SMC available | small generic provider for pd-aud, or reuse the G3D code; link `pinctrl_aud` to it (R2) |
| ABOX SysMMU (0x17E00000, SPI 79, clock 658) | A2 | 9810 SysMMU driver exists (`T/drivers/iommu/exynos-iommu.c`, DPU proven on HW) | DT node + ABOX unmanaged domain; check `no-suspend` vs runtime PM device links |
| AUD clocks (PLL_AUD, DIV_AUD_*, UAIF bclk) | A2 | registered in `clk-exynos9810-legacy.c:61-70,386-394` | verify rates; `audif` set_rate |
| AUD DVFS (`dvfs_aud`, ACPM) | A2 (optional), A5+ for load | CAL vclk entry exists, no devfreq | add AUD domain to `exynos9810-acpm-devfreq.c`; until then fixed boot level |
| PMU syscon access to ABOX CPU registers | A2 | used by the int6 PM code | none |
| ACPM PMIC policy entry for LDO34 | A3 | sealed policy exists (touch rails) | add reg 0x6B mask 0xC0 to the whitelist (G1) |
| S2MPS18 full regulator driver | not needed | ACPM PMIC client only | none (fixed-regulator stand-ins in DT for LDO33; LDO34 via the policy) |
| SPI controller (spi-s3c64xx 9810) + USI01 SPI mode | A3 | no 9810 compat (`T/drivers/spi/spi-s3c64xx.c:1639-1668`); USI 9810 variant exists for I2C | compat + DT |
| HSI2C18 0x108C0000 | A1 | HSI2C driver works on other buses; this node is not in the 6.12 DT | DT node |
| AP CLKOUT0 26 MHz (`OSC_AUD` = 921) | A4 | clock id registered (`clk-exynos9810-legacy.c:475`) | verify output on gpb0-0 |
| `/vendor/firmware` loader | A2 | user helper on in int6 (Wi-Fi) | none; delayed-UFS diag needs blobs in initramfs |
| `/efs/maxim` read-only | A5/A6 | EFS_RO_DIR (build-32) | add 3 files to the manifest |
| Battery temperature (power_supply "battery") | A6 | MAX77705 battery cell exists (`max77705-battery.md`) | check the name |
| TEE | none | — | ABOX on 9810 needs no Trustonic call (section 0, item 3) |
| Modem shared memory (VSS) | calls only | not ported | stub |
| ION | none (DT switch to "dma") | not in int6 | none |
| USB host / CCIC | USB audio | MUIC routing hack only | later |

---

## 7. Open questions (to settle in A1/A2)

1. Is `pd-aud` on when 6.12 boots (SBoot state)? Read `AUD_STATUS` 0x14064004 (0xF = on).
2. Rev A or rev B MAX98512 (selects the register remap)?
3. Is LDO34 on after SBoot? (Decides whether A3 needs G1 at all; the madera driver still has to
   own it to be safe.)
4. Is HPOUT3 connected on crown? (Can be answered without output: with HPOUT3 disabled, read
   the codec's HP short/impedance detect on OUT3 only if Cirrus allows it without driving; else
   leave unknown and never use it.)
5. Lineage jack mechanism (input switch vs extcon uevent): read the framework overlay on SYSTEM.img.
6. Whether 4.9's SPI controller settings (`swap-mode`, feedback delay 1) need a 6.12 equivalent
   for reliable 25 MHz reads; A3 can start at a lower SPI clock.
