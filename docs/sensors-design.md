# crownlte sensors on 6.12: Broadcom BCM47752 sensor hub (SSP over BBD)

Research and design only (read-only, 2026-11-06). Nothing was built, nothing touched the phone.
Sources:

| Tag | Path |
|---|---|
| K49 | gekko `/root/crownlte-hid-kernel` (running 4.9 kernel; built objects in `out/`) |
| DTS49 | K49 `arch/arm64/boot/dts/exynos/exynos9810-crownlte_eur_open_26.dts` (decompiled, phandles resolved by hand) |
| V | gekko `/mnt/ssd/host-builds/kernel-upgrade/rom-ram/testroot/vendor` (Lineage vendor image, mounted read-only) |
| T | 6.12 port repo `/mnt/ssd/host-builds/kernel-upgrade/android16-6.12-port`, branch `crownlte-integration-4` (b4f756bed) |
| LOG49 | `C:\Users\Patrick\Downloads\Codex Projects\kernel-upgrade\research\diagnostic-capture\trial-kmsg-20261015\first-return\2-last_kmsg.bin` (a real 4.9 boot on this phone) |
| LOG612 | `C:\Users\Patrick\tools\trial-romram-20261104\wifi-adb2\serial-ready.bin` (6.12 ROM-RAM run with the PMIC read-only dump) |
| ABI | `roadmap/vendor-abi/VENDOR-ABI.md` |

## 0. Summary

- **There is no separate sensor MCU and no flash.** The "sensor hub" is firmware that runs in the
  Broadcom **BCM47752** GNSS chip (`MODEL_NAME "BCM47752KUB1G"`, K49 `drivers/sensorhub/brcm/factory/mcu_bcm4774.c:21`).
  The chip boots from ROM. The sensor-hub application ("ESW") is a **RAM patch** of 416,468 bytes,
  loaded to chip address `0x00800161` by the vendor daemon **`lhd`** after **every** power-up.
  The patch comes from `/dev/bbd_patch`, a read-only character device that serves an array
  compiled into the kernel.
- **4.9 has no hub firmware update path at all.** It has no flash or bootloader protocol, no
  `request_firmware`, and no `mcu_update` sysfs node. `init.samsungexynos9810.rc:142` chowns
  `mcu_update`, but the BBD build never creates it. The firmware "version check" only reports the
  revision (`mcu_rev` = `BR01<built-in>,BR01<read from hub>`) and never triggers an action.
  Section 1.6 has the evidence and section 6 has the hard guards for 6.12.
- **The data path goes through userspace.** Kernel SSP ↔ `/dev/bbd_sensor` ↔ **lhd** (RPC framing)
  ↔ `/dev/ttyBCM` ↔ kernel SSI-over-SPI ↔ BCM47752. Sensors work only while the vendor `lhd`
  runs. GNSS (`gpsd`) uses the same lhd and chip, so the sensor port also unblocks GNSS.
- **Bus:** USI_CHUB00 SPI (`spi@13AC0000`, alias `spi20`, GIC SPI 104), mode 3, 25 MHz,
  8 bits per word, CPU (PIO) mode, GPIO chip-select gph0-3. 6.12 already has the clocks
  (`GATE_USI_CHUB00`, `CHUB_USI00`), the USIv2 wrapper driver with a 9810 variant, and upstream
  `spi-s3c64xx` (no 9810 compatible yet; the Exynos850 port config fits). `CONFIG_SPI` is **off** in
  the crownlte defconfig.
- **Power:** the hub I/O rail is S2MPS18 **LDO37** (`vdd_mcu_1p8`, 1.8 V). **The bootloader leaves it
  OFF.** The 6.12 PMIC dump shows `L37CTRL = 0x2c` (enable bits 00). On 4.9, lhd switches it
  (`/sys/class/sec/sensorhub/mcu_power`: 0, then 1 at every lhd start), then raises GPS_PWR_EN
  (gpg0-2, nSTANDBY). Any 6.12 hub bring-up therefore needs **one PMIC write** through the
  acpm_mfd write gate: a new exact-rail policy, as was done for touch.
- **Port strategy:** do it like the DHD port. Import the 4.9 sources verbatim: 33 built `.c` files,
  17,054 lines, plus the patch header and the 354-line GPS power glue. Add a 6.12 adaptation layer
  on top. Two places need real design work beyond mechanical API changes:
  1. **IIO record layout.** The HAL hard-codes 4.9 record sizes. 6.12's IIO core (a) refuses to
     enable a buffer with an empty scan mask and (b) pads records with a final
     `ALIGN(bytes, largest)`. Both break Samsung's one-timestamp-channel scheme. A small, gated
     compat hunk in `industrialio-buffer.c` fixes this.
  2. **Kernel file I/O.** The driver reads and writes `/efs` and `/data/log` from the kernel.
     6.12 cannot do this (no `set_fs`), and it must not. All of it gets compiled out.
- **Stages:** S1 is kernel-only: power, SPI and handshake, then read-only SSI register reads checked
  against values captured on 4.9, then power back off. **S1 cannot read the ESW firmware revision**,
  because that only exists after the RAM patch download. S2 is ROM-RAM with vendor lhd and the HAL,
  i.e. the RAM patch download that 4.9 does on every boot. S3 covers wake-up sensors, batching,
  suspend and recovery.
- **Owner decisions needed** (section 8): (1) the LDO37 enable write in S1; (2) agreeing that lhd's
  per-boot RAM patch download (volatile, byte-identical to 4.9) is *not* "flashing", before S2;
  (3) sensor calibration source (none, or a read-only RAM copy of `/efs/FactoryApp`).

---

## 1. Architecture (question 1)

### 1.1 Chip

| Item | Value | Evidence |
|---|---|---|
| Hub chip | BCM47752 (GNSS + Cortex-M sensor-hub firmware "ESW"); the DT and driver call it BCM4773/4774 | K49 `factory/mcu_bcm4774.c:21`; `bbdpl/bcm_gps_mem.c:68-73` (CM4 reset register comment) |
| ROM ASIC version | `0x004775B0` (patch header `<AsicVersion=0x004775B0>`) | K49 `drivers/sensorhub/brcm/bbdpl/q_os/bbd_patch_file_crown.h:4` |
| ESW identity after the patch | WHOAMI `0x55` (`DEVICE_ID`), FW rev `19121600` | K49 `ssp.h:78`, `ssp_firmware.c:31`; LOG49 lines 1584, 1592 |
| Attached sensors (behind the hub) | LSM6DSL accel/gyro, AK09918 mag, LPS22H baro, TMD4906 light/prox (AMS autocal, colour-ID), thermistors, "mobeam" barcode IR | K49 `out/.config:5157-5209`; built objects in `out/drivers/sensorhub/brcm/factory/` |
| Not on the hub (separate I2C drivers) | SX9320 grip (`sensors.grip.so`), MAX86915 HRM (`sensors.bio.so`), hall IC | V `etc/sensors/hals.conf`; ABI:245-246 |

### 1.2 Bus

| Item | Value | Evidence |
|---|---|---|
| Controller | `spi@13AC0000`, "samsung,exynos-spi", alias `spi20`, GIC SPI 104, fifosize 64, `swap-mode`, no `dma-mode` (so CPU mode) | DTS49:7969-7986, alias :4596; K49 `drivers/spi/spi-s3c64xx.c:1445-1450` |
| USI | USI_CHUB00, SW_CONF in the CHUB sysreg `usi@13A13000` = "spi" (alias `usi_00_chub`) | DTS49:5701-5707, :14384 |
| USI wrapper | 4.9 SPI driver writes `USI_CON (+0xC4) = 0` (reset released) only; it never writes USI_OPTION | K49 `spi-s3c64xx.c:151-154, 1362-1381` |
| Clocks | `spi` = GATE_USI_CHUB00 (86), `spi_busclk0` = CHUB_USI00 (89); `clk_set_rate(src, speed*4)` | DTS49:7978; K49 `include/dt-bindings/clock/exynos9810.h:34,37`; `spi-s3c64xx.c:820` |
| Device | `BCM4773@0`, "ssp,BCM4773", `spi-max-frequency = 25000000`, `spi-cpol`, `spi-cpha` | DTS49:7987-7993 |
| Mode / word | SPI_MODE_3; **8 bpw always**. `bcm_spi_sync()` ignores its `bits_per_word` argument, so the "32 bpw" constant is dead and `swap-mode` never matters | K49 `ssp_dev.c:709`; `bbdpl/bcm_gps_spi.c:625-652, 659` |
| Chip select | `controller-data { cs-gpio = gph0-3 }`: a GPIO CS | DTS49:8013-8016 |
| Pins | gph0-0/1/3 func 2 (MOSI/SCK/SSN), gph0-2 func 2 pull-down (MISO), all in CHUB pinctrl `pinctrl@13A80000` | DTS49:801-817 |
| Link protocol | Broadcom SSI "stream" mode: full duplex, 2-byte length, flow control (3 retries), frames up to 8192 bytes; debug mode (cmd bit 0x80) is used only by the kernel for indirect AHB register reads | K49 `bcm_gps_spi.c:59-61, 250-296`; `bcm_gps_regs.c:352-420`; LOG49:788-794 |

### 1.3 GPIOs and IRQs

| Signal | Pin | Direction / config | Use | Evidence |
|---|---|---|---|---|
| HOST_REQ ("ttyBCM" IRQ) | gpa1-7 (alive, EINT func 0xf, pull-down) | input, IRQ **level high**, wake source while ttyBCM is open | chip has data for the AP | DTS49:433-440, :7997; K49 `bcm_gps_spi.c:1165, 1241`, `:243` (enable_irq_wake) |
| MCU_REQ | gph0-5 | output, init 0 | AP wakes the chip ("hello": raise, poll MCU_RESP ≤ 100 ms, re-pulse every 20 ms) | DTS49:830-840, :7998; K49 `bcm_gps_spi.c:495-536` |
| MCU_RESP | gph0-4 | input | chip awake | DTS49:819-828, :7999 |
| AP_SLEEP (DT name `ssp-batch-wake-irq`) | gpa0-5 | **output**, init 1, pulsed 0→1 at suspend | tells the hub the AP is going to sleep. It is *not* an input, despite the DT name and suspend-design.md:174 | DTS49:423-431; K49 `ssp_dev.c:376-388, 1029-1031, 1057` |
| SHUB_INT | gpa4-0 (alive, EINT) | input, IRQ falling, only with HIFI batching (timestamp capture) | batch timestamps | DTS49:442-449, :8001; K49 `ssp_dev.c:390-399, 793-805` |
| GPS_PWR_EN (nSTANDBY) | gpg0-2 (PERIC0), pinctrl state "hub_en" | output, **driven 0 at boot by the kernel**; lhd drives it through `/sys/class/sec/gps/GPS_PWR_EN/value` (gpio_export link) | chip standby/reset | DTS49:2644-2652, :13910-13916; K49 `drivers/gps/sec_gps_bcm47531.c:255-257`; V `etc/sensor/lhd.conf:8` |
| SHUB_EN (`ssp-pwr-en`) | absent on crownlte | — | — | K49 `ssp_dev.c:361-374` (falls back to the regulator) |

### 1.4 Power

| Rail | Value | Evidence |
|---|---|---|
| `vdd_mcu_1p8` = S2MPS18 **LDO37**, 1.8 V, `regulator-initial-mode = <3>`, **not** always-on/boot-on | consumer via `regulator_get(NULL, "vdd_mcu_1p8")`; switched only through `mcu_power` sysfs | DTS49:9117-9125; K49 `ssp_dev.c:351-359`, `ssp_sysfs.c:533-571, 936, 1182-1184` |
| Register | `L37CTRL = 0x6E`; vsel[5:0] (0.7 V + 25 mV × code), enable[7:6] | K49 `include/linux/mfd/samsung/s2mps18-private.h:144, 241, 248, 251, 272`; `drivers/regulator/s2mps18.c:538` |
| 4.9 "on" value (derived) | initial-mode 3 → `s2mps18_of_map_mode(3)` = 0x1 → `set_mode` stores `0x1 << 6` → `s2m_enable` writes enable bits `01` → **expected `L37CTRL = 0x6C`** when on. **Unverified on hardware.** | K49 `drivers/regulator/s2mps18.c:99-110, 115-123, 151-164`; `drivers/regulator/core.c:1066-1072` |
| State under 6.12 (bootloader-left) | `pmic[0x69..0x76] = e8 ec 28 ac 3c 2c ac 1c 28 2c 8a f0 2c 3c`, so **`L37CTRL = 0x2c`: vsel 1.8 V, enable 00 = OFF** | LOG612 line 51827 (6.12 never writes the PMIC LDOs) |
| 4.9 sequence | lhd: `set_mcu_power: enable vdd_mcu_1p8(0), is enabled 0` (20.30 s), then `(1), is enabled 1` (20.36 s) | LOG49:753, 756 |
| Other rails or clocks | None in the DT for the hub. No PMIC 32 kHz output is referenced. The chip has its own TCXO (LheClockMHz=100) | DTS49 (no other supplies on the node); V `lhd.conf:5` |

CHUB block: 4.9 has no CHUB power-domain or CHUB-CPU driver (`grep -ri chub drivers/soc/samsung` finds
only CAL clock tables). The AP simply uses the CHUB pinctrl, sysreg and USI, so the block is up
from boot. T already has the CHUB clocks (`drivers/clk/samsung/clk-exynos9810-legacy.c:95`
`GATE_USI_CHUB00`, `:398` `CHUB_USI00`) and `pinctrl_chub` (`exynos9810.dtsi:279`).

### 1.5 Boot and reset sequence (4.9, from LOG49)

```
kernel (device_initcall):
  sec_gps_bcm47531: GPS_PWR_EN := 0, export link /sys/class/sec/gps/GPS_PWR_EN   (sec_gps_bcm47531.c:255-257)
  bcm_spi_probe: gpios, IRQ "ttyBCM" (disabled), misc /dev/ttyBCM, bbd_init()       (bcm_gps_spi.c:1150-1295)
  bbd_init: /dev/bbd_{sensor,control,patch} (major 239), /sys/bbd, PM notifier,
            then calls ssp_probe() directly on the BCM4773 spi_device               (bbd.c:1085-1186)
  ssp_probe: DT parse, IIO devices, sysfs, misc, bbd_register(callbacks)            (ssp_dev.c:657-830)
  regulator late cleanup: LDO37 unused, so it stays/goes off
userspace (class main, ~20.2 s):
  20.30 lhd  mcu_power 0 ; 20.36 mcu_power 1                                         LOG49:753,756
  (lhd GPS_PWR_EN handling, "ClearNstdbyPin() delay: 120us" in lhd strings)
  20.398 open bbd_control ; open ttyBCM (IRQ on, wake on, SSI stream setup,
         4 debug-mode AHB register reads printed)                                    LOG49:783-795
  20.399 "BBD:PassThru=1" -> LHD-killer timer 10 s                                   LOG49:796-798
  20.63..21.65 open/read /dev/bbd_patch (download ~1.0 s)                            LOG49:907-1326
  21.87 open bbd_sensor ; "BBD:PassThru=0" ; "ESW:READY"                             LOG49:1363-1369
  22.77 "[SSP]: MCU sensor init done" -> "Sensors of MCU are ready!" -> initialize_mcu:
        WHOAMI 85, positions, glass, scan state, mag fuse ROM, mag cal, thermistor table,
        "MCU Firm Rev : New = 19121600", accel range 8, cal data                    LOG49:1581-1604
  34.58 "GPSD:CORE_ON" (GNSS session through the same lhd)                          LOG49:2649
```

Reset or recovery: on an SSP timeout, `bbd_do_transfer` schedules `work_ssp_reset`
(`ssp_bbd.c:204`). That calls `bbd_mcu_reset()`, which with the LHD killer enabled sends **SIGKILL to
lhd** (`bbd.c:414-433`). init restarts lhd, and lhd repeats the whole power, standby, patch
sequence. `ESW:CRASHED` from lhd marks the hub crashed (`bbd.c:471-477`, `ssp_bbd.c:310-319`).
Every recovery reloads the patch into RAM. lhd gives up after `LheMaxMcuResetTries=3`
(`lhd.conf:44`, which overrides `:13`).

### 1.6 Firmware: where it lives, when 4.9 "updates" it, the version check

1. **Where:** the only hub firmware the AP ever handles is
   `drivers/sensorhub/brcm/bbdpl/q_os/bbd_patch_file_crown.h`, compiled into `static unsigned char bbd_patch[]`
   (`bbd.c:131-140`). It is always the `q_os` variant; `CONFIG_SENSORS_SSP_CROWN=y` (`out/.config:5205`).
   It is a text container with these fields: `FormatVersion=0x00010003`, `Crc=27`, `BlobLength=416468`,
   `AsicVersion=0x004775B0`, `PatchLevel=0`, `PostPatchLevel=1`, `CustomerVersion=None`,
   `Name=bcm_sensorhub_a1`, `Type=Application`, **`PatchAddress=0x00800161`**, then a hex `Blob`.
   - Bytes served by `/dev/bbd_patch` (text + NUL): **851,283 bytes, sha256 `5568dff066916b1092f4e5971bf400731eea0800abc08177d15410a808020537`**
   - Decoded blob: 416,468 bytes (matches BlobLength), sha256 `82e53dfde8eab0e93f731ec7734796d332200e20cf77fd6f6016be3018235aac`
   - Header file sha256 `27defc4f0f9b4b50525f523b33288fd10e26812b3ea9edcc3363ad926f6596c4`
2. **How it gets to the chip:** lhd opens `/dev/bbd_patch` (`LhePatch=/dev/bbd_patch`, `lhd.conf:11`)
   and streams it over `/dev/ttyBCM` with its "firmware downloader". The lhd strings show the states
   `FD_WAIT4VERSION_INIT` → (`ROM Version mismatch... Trying ASIC reset.`) → `FD_WAIT4DOWNLOAD_START`
   → `Patch loaded and verified` → `OnFirmwareDownloadDone() bOk=%d ulAsicVersion`. The target
   is chip RAM (PatchAddress 0x0080xxxx). This happens **on every lhd start**: each boot and each hub
   reset (LOG49:907-1369).
3. **When 4.9 "updates":** never in the flash sense. The kernel's `/dev/bbd_patch` has `.write = NULL`
   (`bbd.c:1068`). Nothing in the BBD tree implements a flash, bootloader or erase protocol:
   `ssp_firmware.c` is 50 lines and only defines `get_module_rev()`; `grep -i
   "update_firm|fw_update|bootloader|flash|request_firmware|download"` over `drivers/sensorhub/brcm`
   finds only comments in unrelated gyro files. The `mcu_update` sysfs node chowned by the vendor rc
   (`init.samsungexynos9810.rc:142`) **does not exist** in this build: `ssp_sysfs.c:911-939` has no
   such attribute.
4. **The "version check":** `initialize_mcu()` reads `MSG2SSP_AP_FIRMWARE_REV (0xF0)` into
   `uCurFirmRev` and logs it (`ssp_dev.c:276-278`). `mcu_rev` prints
   `BR01<SSP_FIRMWARE_REVISION_BCM>,BR01<uCurFirmRev>` (`factory/mcu_bcm4774.c:28-29`); the built-in
   value is 19121600 (`ssp_firmware.c:31`). **No code compares them or acts on a difference.** The only
   real version gate is in lhd: the ROM-version-vs-patch `AsicVersion` check before the RAM download.
5. **Other optional lhd inputs** (absent in this config and to stay absent): an `UrgentPatch=` config key
   (lhd strings: "Urgent patch found in %s", "Force urgent patch"), and the SELinux label for
   `/dev/bbd_urgent_patch` (V `vendor_file_contexts`). 4.9 never creates that device (`bbd.c:112-118`
   creates only shmd/sensor/control/patch), and the vendor `lhd.conf` has no `UrgentPatch` line.
6. **Kernel-originated chip writes:** in normal operation, only a write-1-to-clear of `HSI_ERROR_STATUS` in
   debug mode (`bcm_gps_spi.c:196`) when the PZC status shows an error. `bcm_filsafe_reset_CPU()`
   (writes the CMU unlock and CM4 reset, `bcm_gps_mem.c:68-73`) has no callers (`:229` is commented
   out). `bcm477x_rpc_get_version()` (`bcm_gps_regs.c:48-146`) has no callers.
   Userspace (lhd) **cannot** issue debug-mode SSI transactions: the kernel builds the control
   byte of every frame it sends for `/dev/ttyBCM` writes as `SSI_MODE_STREAM` (`bcm_gps_spi.c:268, 281`).

---

## 2. Kernel ↔ userspace ABI the vendor stack needs (question 2)

The consumers are `lhd` (V `bin/hw/lhd`, `etc/sensor/lhd.conf`, `etc/init/init.gps.rc:30`), `gpsd`
(talks to lhd over pipes and sockets only, with no kernel nodes), the Samsung sensors HAL
(`android.hardware.sensors@1.0-service` → `lib64/hw/android.hardware.sensors@1.0-impl.samsung.so` →
`hals.conf` → `sensors.sensorhub.so`, `sensors.grip.so`, `sensors.bio.so`), and init rc and ueventd.

### 2.1 Character devices

| Node | Driver / fops | User | Notes / evidence |
|---|---|---|---|
| `/dev/ttyBCM` (misc, dynamic minor) | `bcm_spi_fops`: open (IRQ and wake on, SSI setup, reg-read dump), read, write (queues TX work), poll; **no ioctl** | lhd | `bcm_gps_spi.c:452-459, 1251-1256`; ueventd `/dev/ttyBCM* 0660 system system` (ueventd.rc:229); label `bbd_device` |
| `/dev/bbd_sensor` (char 239,1) | read and poll only: SSP→hub packets, `{u16 size; payload}` | lhd | `bbd.c:339-352, 1044-1052` |
| `/dev/bbd_control` (239,2) | read (requests to lhd: `BBD:RESET_REQ`, `GPSD:SENSOR_ON/OFF`), write (strings from lhd: `ESW:READY/NOTREADY/CRASHED`, `BBD:PassThru=0/1`, `SSI:PassThru=…`, `GPSD:CORE_ON/OFF`, `LHD:STOP …`, debug toggles) | lhd | `bbd.h:43-69`; `bbd.c:451-520` |
| `/dev/bbd_patch` (239,3) | read-only, serves `bbd_patch[]` | lhd | `bbd.c:767-786, 1062-1069` |
| `/dev/ssp_sensorhub` (misc) | write (library/context commands to the hub), read (context events), ioctl `IOCTL_READ_BIG_CONTEXT_DATA = _IOR('S', 3, char *)` | HAL | `ssp_sensorhub.h:38-39`; `ssp_sensorhub.c:209-270, 649` |
| `/dev/batch_io` (misc) | ioctl only: cmd = `(0xFC << 8) | sensor_type`, arg `struct batch_config { s64 timeout; s64 delay; int flag; }` | HAL | `ssp_sysfs.c:20-26, 964-1066, 1192-1196`; ueventd `0600 system` |
| `/dev/ssp_data_injection` (misc) | read/write (sensor data injection) | HAL | `ssp_sysfs.c:1068-1155, 1199` |
| `/dev/iio:deviceN` | IIO kfifo, one record per event (layout in 2.3) | HAL | ueventd.rc:216 |

Major 239 for `bbd_*` is hard-coded (`bbd.h:33`, `bbd.c:1124`). Userspace finds nodes by name, so
6.12 may (and should) use a dynamic major.

### 2.2 sysfs

| Path | Attributes / behaviour | Evidence |
|---|---|---|
| `/sys/class/sec/gps/GPS_PWR_EN/value` | rw 0/1 (lhd `GpioNStdbyPath`); 4.9 implements it as a gpio_export link to `/sys/devices/platform/10430000.pinctrl/gpiochip39/gpio/gpio141/value` (SELinux `sysfs_gps_writable` by that exact path) | `sec_gps_bcm47531.c:255-257`; lhd.conf:8; init.gps.rc:3-4; V `vendor_sepolicy.cil` genfscon |
| `/sys/class/sec/sensorhub/mcu_power` | rw: read `regulator_is_enabled`, write 0/1 → LDO37 disable/enable, then msleep(20) (lhd `GpioNPowerPath`) | `ssp_sysfs.c:514-571, 936, 1182-1184`; lhd.conf:7 |
| `/sys/bbd/{bbd(w), pl(r), lk_timeout(rw), lk_enable(rw)}` | BBD control and LHD-killer config | `bbd.c:858-873`; genfscon `/bbd`, `/bbd/lk_*` |
| `/sys/class/sensors/ssp_sensor/` (`/sys/devices/virtual/sensors/ssp_sensor`) | `enable`, `enable_irq`, `ssp_flush` (w), `ssp_control` (w), `mcu_rev`, `mcu_name`, **`mcu_reset` (reading it resets the hub!)**, `mcu_test`, `mcu_sleep_test`, `data_injection_enable`, `sensor_dump`, `bcm_minidump`, `reset_info`, `sensor_state`, `thermistor_channel_0/1`, `register_rw` (0664, raw sensor register access through the hub), plus `*_poll_delay` attributes as chowned in rc | `ssp_sysfs.c:846, 911-939, 1171`; rc :137-174; HAL strings use `enable`, `ssp_control`, `ssp_flush` |
| `/sys/class/sensors/{accelerometer_sensor, gyro_sensor, magnetic_sensor, barometer_sensor, light_sensor, proximity_sensor, thermistor_sensor, hidden_hole}/…` | factory attributes (raw_data, selftest, calibration, vendor, name, …) | `factory/*.c` `sensors_register()` calls; rc :25-90 |
| `/sys/class/sensors/sensor_dev/flush`, `/sys/class/sensor_event/symlink/` | `sensors_core.c` classes `sensors` and `sensor_event` | `sensors_core.c:70-145` |
| `/sys/class/sec/sec_barcode_emul/*` | mobeam | rc :174-178 |
| `/sys/bus/iio/devices/iio:deviceN/{name, poll_delay, buffer/enable, buffer/length}` | `poll_delay` is added by SSP to each IIO device; `buffer/*` come from the IIO core | `ssp_sysfs.c:1186-1190`; HAL strings |
| `/sys/class/sensors/magnetic_sensor/name`, `/sys/class/sensors/hidden_hole/hh_check_coef` | read by the HAL | HAL strings |

**Hard path constraint:** ueventd sets `buffer/enable` and `buffer/length` to `0660 system` **only** under
`/sys/devices/platform/13ac0000.spi/spi_master/spi20/spi20.0/iio:device*` (ueventd.rc:217-218), and
SELinux labels `sysfs_iio` by `/sys/devices/platform/13ac0000.spi(/.*)` (vendor_file_contexts).
The HAL runs as `system`. If the path differs, `buffer/enable` stays `root 0644` and the HAL can't
start any sensor. So on 6.12, the SPI controller must be a **DT root-level** node named `spi@13ac0000`
(not under `soc@0`, not under a USI parent node), with alias `spi20`, and the hub at `reg = <0>`.
(The `poll_delay` rule is written as `/sys/bus/iio/...` and matches through the subsystem, so it does not
depend on the path.)

### 2.3 IIO devices (record layout the HAL expects)

The built 4.9 driver uses `ssp_iio.c`; `ssp_input.c` is **not** built (`out/drivers/sensorhub/brcm/`
has no `ssp_input.o`). Each IIO device has **one** channel of type `IIO_TIMESTAMP`, scan_index 0,
`realbits = storagebits = (len+8)*8 / repeat`, `repeat = bits/256 + 1`, and the buffer has
`scan_timestamp = true` (`ssp_iio.c:551-610`, `ssp_iio_ring.c:69-84`). The HAL only touches
`buffer/enable`, `buffer/length` and `poll_delay` (no `scan_elements`; HAL strings), so record
sizes are implicit. Each record is `report_data_len + 8` bytes (sensor_list.h:91-124):

| IIO name | data | record | | IIO name | data | record |
|---|---|---|---|---|---|---|
| accelerometer_sensor | 6 | 14 | | rotation_vector_sensor | 17 | 25 |
| geomagnetic_sensor | 8 | 16 | | game_rotation_vector | 17 | 25 |
| uncal_geomagnetic_sensor | 13 | 21 | | sig_motion_sensor | 1 | 9 |
| gyro_sensor | 12 | 20 | | step_det_sensor | 1 | 9 |
| uncal_gyro_sensor | 24 | 32 (2×16) | | step_cnt_sensor | 8 | 16 |
| interrupt_gyro_sensor | 12 | 20 | | tilt_detector | 1 | 9 |
| pressure_sensor | 12 | 20 | | pickup_gesture | 1 | 9 |
| light_sensor (2-byte gain) | 19 | 27 | | scontext_iio | 64 | 72 (3×24) |
| light_ir_sensor | 13 | 21 | | thermistor_sensor | 3 | 11 |
| light_cct_sensor | 19 | 27 | | proximity_pocket | 1 | 9 |
| light_flicker_sensor | 2 | 10 | | uncal_accel_sensor | 12 | 20 |
| proximity_sensor | 1 | 9 | | meta_event | 8 | 16 |
| proximity_alert_sensor | 1 | 9 | | wake_up_motion | 1 | 9 |

(geomagnetic_power and proximity_raw have report length 0, so no IIO device is created.)

4.9 sizes the record with `iio_compute_scan_bytes()` = the timestamp element only, with no final alignment
(K49 `drivers/iio/industrialio-buffer.c:545-565`), and accepts an empty scan mask when `scan_timestamp` is
set. **6.12 differs in two places** (T `drivers/iio/industrialio-buffer.c`):
- `:902-907`: `iio_verify_update()` returns -EINVAL when the inserted buffer's scan mask is empty. A
  timestamp-only device can never enable its buffer.
- `:793`: `bytes = ALIGN(bytes, largest)`. ALIGN uses power-of-two mask arithmetic, so for a 14-byte
  record it gives 18, and for 20 it gives 36. The kfifo element grows and `iio_push_to_buffers` copies
  past the pushed buffer. Every non-power-of-two record breaks.

### 2.4 Input devices

`ssp_context` (`ssp_sensorhub.c:624-645`) is the only input device the hub driver registers in the
built configuration. The HAL also searches `/dev/input` by name for grip and HRM (`sensors.grip.so`,
`sensors.bio.so`), which belong to other drivers.

### 2.5 Files the HAL itself opens (not kernel ABI, listed for completeness)

`/efs/FactoryApp/predefine0..15`, `/efs/FactoryApp/version` (HAL), `/sys/class/sec/tsp/{cmd,ear_detect_enable}`,
`/sys/devices/virtual/sec/sec_key/{hall_detect,certify_hall_detect}` (HAL strings). On 4.9, a HAL write to
`hidden_hole/hh_write_all_data` makes the **kernel** rewrite `/efs/FactoryApp/predefine*` at boot
(LOG49:583-590; `factory/light_colorid.c:160-170, 241-251, 646-656`).

---

## 3. Driver size, structure, portability (question 3)

### 3.1 What 4.9 actually builds

Built objects in K49 `out/drivers/sensorhub/brcm/` (33 `.c` files, **17,054 lines**, excluding sx9320.c
and headers), plus `drivers/gps/sec_gps_bcm47531.c` (354 lines) and the 851 KB patch header.

| Layer | Files | Role |
|---|---|---|
| SSI/SPI transport | `bbdpl/bcm_gps_spi.c` (1,428), `bcm_gps_regs.c`, `bcm_gps_mem.c`, `bcm_gps_spi.h` | spi_driver "ssp,BCM4773"; `/dev/ttyBCM`; IRQ/hello/rx-tx workqueue; debug register reads |
| BBD bridge | `bbdpl/bbd.c` (1,260), `bbd_rpc_lh.c` (690), `bbd.h` | `/dev/bbd_*`, control strings, LHD killer, PM notifier, RPC unframing of sensor packets |
| SSP core | `ssp_dev.c`, `ssp_bbd.c`, `ssp_i2c.c` (commands), `ssp_data.c` (parser), `ssp_sysfs.c`, `ssp_iio.c`, `ssp_iio_ring.c`, `ssp_sensorhub.c`, `ssp_misc.c`, `ssp_debug.c`, `ssp_dump.c`, `ssp_firmware.c`, `ssp_motorcallback.c`, `sensors_core.c` | protocol, sensors, sysfs, IIO, misc |
| Factory | `factory/{accel,gyro}_lsm6dsl.c`, `magnetic_common.c`, `pressure_lps22h.c`, `light_tmg399x.c`, `light_colorid.c`, `prox_autocal_ams.c`, `thermistor.c`, `barcode_emul_tmg3992.c`, `mcu_bcm4774.c` | sysfs per sensor, calibration |
| Power/standby | `drivers/gps/sec_gps_bcm47531.c` | GPS_PWR_EN export |

Unusual structure to keep in mind: `ssp_driver` is registered as an spi_driver with only an id_table
(`ssp_dev.c:1082-1096`), so it never binds. `bbd_init()` calls `pssp_driver->probe()` on the BCM4773
spi_device and registers a fake `dummy_spi` device used only for PM callbacks (`bbd.c:41-47, 1172-1186`).

### 3.2 Can it be ported like the DHD? Yes

The recipe that worked for Wi-Fi (`Documentation/crownlte/wifi-port.md` on `crownlte-wifi`):
1. **Provenance commit:** a verbatim copy of the built file set (section 3.1) plus the required headers and
   `q_os/bbd_patch_file_crown.h` only (drop the o_os/p_os and star/haechi patches and all unbuilt factory files).
2. **Kbuild:** the exact 4.9 define set (`CONFIG_SENSORS_SSP_{BBD,LSM6DSL,MAGNETIC_COMMON,AK09918,LPS22H,
   SENSORHUB,MOBEAM,TMD4906,PROX_AUTOCAL_AMS,LIGHT_REPORT_LUX,LIGHT_MAX_GAIN_2BYTE,LIGHT_COLORID,
   IRDATA_FOR_CAMERA,INTERRUPT_GYRO_SENSOR,HIFI_BATCHING,CROWN,THERMISTOR}`, `SENSORS_MULTIPLE_GLASS_TYPE`,
   `BCM_GPS_SPI_DRIVER`, `ANDROID_VERSION` as on 4.9) via `ccflags`, plus a forced
   `-include ssp_compat_612.h`.
3. **Adaptation commits**, grouped by API (counts are matches in the built set):

| 4.9 API | Count | 6.12 replacement |
|---|---|---|
| `wake_lock*` (Android wakelock API) | 54 | compat shim over `wakeup_source_register()` / `__pm_stay_awake` / `__pm_wakeup_event(ws, ms)` / `ws->active` |
| `get_fs/set_fs/KERNEL_DS`, `filp_open`, `vfs_read/vfs_write` (kernel file I/O) | 79 + 42 | **Delete, not port** (section 6, G7). Calibration loaders return -ENOENT; savers return -EPERM; dumps disabled |
| `struct timespec`, `ktime_to_timespec`, `timespec_sub`, `do_gettimeofday`, `struct timeval`, `getnstimeofday` | ~31 | `timespec64` / `ktime_get_real_ts64` / ktime arithmetic |
| `setup_timer`/`init_timer`, `(unsigned long data)` callbacks | 3 | `timer_setup` + `from_timer` |
| `class_create(THIS_MODULE, …)` | 4 | `class_create(name)` |
| spi_driver `.remove` returning int | 2 | void |
| `iio_device_alloc(0)`, `indio_dev->mlock`, `.driver_module`, `currentmode`, `iio_kfifo_allocate` + `iio_device_attach_buffer` | 8 | `iio_device_alloc(parent, 0)`; driver mutex; drop the rest; `devm_iio_kfifo_buffer_setup()` (or keep `iio_kfifo_allocate`, still exported in T `include/linux/iio/kfifo_buf.h:11`) **plus the IIO compat hunk (3.3)** |
| `sec_device_create` / `sec_class` | 12 | new tiny `crownlte-sec-class` (class "sec"); needed for `/sys/class/sec/{gps,sensorhub,sec_barcode_emul}` |
| `gpio_export` + `gpio_export_link` | 2 | **replace** with an attribute group named `GPS_PWR_EN` holding `value` on the sec "gps" device (same path for lhd; no `GPIO_SYSFS`, no global GPIO numbers) |
| legacy `of_get_named_gpio`, `gpio_request`, `gpio_*_value` | 50 | `devm_gpiod_get(…, "ssp-mcu-req", …)` etc. (the DT keeps the 4.9 property names with a `-gpios` alias, or uses `of_get_named_gpio` while it still exists in 6.12) |
| `regulator_get(NULL, "vdd_mcu_1p8")` + enable/disable | 4 | no S2MPS18 regulator driver exists; route `mcu_power` to a new **exact-rail** ACPM MFD call (section 4.1) |
| `psy_do_property(…POWER_SUPPLY_EXT_PROP_OVERHEAT_NOTIFY)` | 1 | stub (log only) until a battery driver exposes it |
| `muic_notifier_register` (cpuidle hook) | 7 | compile out |
| `kstat_irqs_cpu` (debug print) | 1 | `kstat_irqs_usr` or drop |
| `register_chrdev_region(MKDEV(239,…))` | 1 | `alloc_chrdev_region` |
| `send_sig(SIGKILL, task)` on a stored `task_struct *` (LHD killer) | 1 | keep, but take a pid ref (`get_task_pid`/`kill_pid`) to avoid a use-after-free when lhd exits |

Estimated adaptation size: ~1-1.5k changed lines, the same order as the DHD port. The protocol and parser
code (`ssp_data.c`, `ssp_i2c.c`, `bbd_rpc_lh.c`) should need almost no change.

### 3.3 IIO compat (the only core change)

Two hunks in T `drivers/iio/industrialio-buffer.c`, compiled only with a new
`CONFIG_IIO_SSP_LEGACY_LAYOUT` (selected by the SSP driver) and only active for devices that set a new
`indio_dev` flag (e.g. `iio_dev_opaque->ssp_legacy = true` through a helper):
1. In `iio_verify_update()`, allow an empty `scan_mask` when `scan_timestamp` is set (4.9 behaviour).
2. In `iio_compute_scan_bytes()`, skip the final `ALIGN(bytes, largest)` (4.9 behaviour). Alternatively,
   apply it only when `largest` is a power of two: that is identical for every upstream driver,
   because their element sizes are powers of two.

Also set `indio_dev->modes = INDIO_BUFFER_SOFTWARE` (4.9 relied on the ring setup ORing it in,
`ssp_iio_ring.c:84`). The selftest prints each device's `bytes_per_datum` and compares it with the table in 2.3.
A cleaner alternative is a private char device per sensor. It is rejected because the HAL locates devices
via `/sys/bus/iio/devices/iio:deviceN/name` and `/dev/iio:deviceN`, so they must be real IIO devices.

---

## 4. Dependencies (question 4)

### 4.1 Power

- **LDO37 must be switched by the kernel**, because the bootloader leaves it off (LOG612:51827).
  T has no regulator driver for S2MPS18, and `acpm_mfd` refuses every PMIC write unless a sealed policy
  allows it (T `drivers/soc/samsung/acpm/acpm_mfd.c:47-90`; the precedent is `touch_rails`).
- Proposal: add `policy.shub_rail`, sealed once from DT (`samsung,shub-rail-update` on the S2MPS18 node), allowing
  **only** a read-modify-write of `L37CTRL (0x6E)` that changes bits [7:6] and nothing else. Refuse if
  `vsel != 0x2c` (1.8 V). Allowed enable values are `00` (off) and the 4.9 "on" value. Derived "on" value: `01` → `0x6C`
  (section 1.4). Because this is derived, the first trial uses it only after the owner OKs it, and a read
  of L37CTRL on a running 4.9 phone (or the 4.9 `/sys/kernel/debug/regulator` dump) should confirm it.
  The selftest logs before and after values. `mcu_power` (lhd) maps to this call.
- GPS_PWR_EN (gpg0-2) is a GPIO, so it needs no PMIC access. Keep 4.9's boot state: **driven 0 at probe**.

### 4.2 Clocks and USI

- Clocks: `GATE_USI_CHUB00` and `CHUB_USI00` exist in T (`clk-exynos9810-legacy.c:95, 398`). 4.9 sets the
  source to 100 MHz (25 MHz × 4, `spi-s3c64xx.c:820`). T's s3c64xx also calls `clk_set_rate(src, speed*div)`
  (T `spi-s3c64xx.c:749`). **Risk:** the legacy VCLK may not support set_rate. S1 logs `clk_get_rate()` before
  and after, and falls back to `spi-max-frequency = rate/4` from the bootloader rate if set_rate fails.
  Mark both CHUB clocks so `clk_disable_unused` leaves them alone until the SPI holds them (as done for the ADC).
- USI: T `drivers/soc/samsung/exynos-usi.c:87-104` has the 9810 variant (SW_CONF via `samsung,sysreg`, `USI_V2_SPI`).
  Sysreg: `0x13A13000` (CHUB). 4.9 only cleared `USI_CON` and never touched `USI_OPTION`. S1 logs USI_OPTION before
  configuration, and the DT uses the same `samsung,clkreq-on` choice as the touch USI unless the log shows 4.9 left it different.
- **Node placement conflict:** T's exynos-usi populates children (`:301`), but nesting the SPI under the USI node,
  or under `soc@0`, changes the sysfs path the vendor ueventd and SELinux depend on (2.2). Proposal: root-level
  `usi@13ac00c0` and root-level `spi@13ac0000` as siblings. The USI driver registers before the SPI driver
  (drivers/soc links before drivers/spi, both device_initcall), and the SSP glue refuses to touch the bus unless SW_CONF reads back SPI.

### 4.3 SPI controller

- T `drivers/spi/spi-s3c64xx.c` has no 9810 compatible. The USIv2 SPI matches `exynos850_spi_port_config`
  (`:1574-1584`: fifo_depth 64, V2 level masks GENMASK(23,15)/(14,6), clk_div 4, clk_from_cmu). These are a
  superset of 4.9's `rx_lvl_offset 15` + mask 0x7f for a 64-byte FIFO (`spi-s3c64xx.c:1724`, `:2137-2143`).
  Proposal: `compatible = "samsung,exynos9810-spi", "samsung,exynos850-spi";` with no driver change (or a
  one-line match-table entry).
- PIO only (no `dmas`), like 4.9 (no `dma-mode`). 6.12 does IRQ-driven PIO for transfers ≥ FIFO depth
  (T `spi-s3c64xx.c:801-843` splits PIO transfers at or above FIFO depth with `use_irq`; `:615-640` waits). The largest transfer is an 8 KB SSI frame; 4.9 moved the 416 KB patch in ~1.0 s.
- CS: `cs-gpios = <&gph0 3 GPIO_ACTIVE_LOW>` (T sets `use_gpio_descriptors`, `:1309`); pinctrl gph0-0/1/2 func 2 only.
- Config: `CONFIG_SPI=y`, `CONFIG_SPI_S3C64XX=y` (both absent from T `crownlte_android_defconfig`),
  `CONFIG_EXYNOS_USI=y`, `CONFIG_IIO_KFIFO_BUF=y`.

### 4.4 Interrupts

- HOST_REQ gpa1-7 has its own alive wakeup interrupt in T (`exynos9810-pinctrl.dtsi`, gpa1 bank, GIC SPI 51
  for pin 7), so `gpiod_to_irq` + level-high works.
- SHUB_INT gpa4-0: the T gpa4 bank has no interrupts, and the alive muxed EINT is missing ("14050000.pinctrl:
  irq number for muxed EINTs not found", LOG612 line 222). Only HIFI batching timestamps use it (S3).
  In S1/S2, request it optionally and continue without it (4.9 already ignores a request_irq failure, `ssp_dev.c:794-803`).
- Wake: HOST_REQ `enable_irq_wake` needs the EINT wake mask, which T never writes
  (`roadmap/suspend-design.md:194`). This is S3.

---

## 5. Staged plan (question 5)

Common to all stages: new branch `crownlte-sensorhub` from integration-4. Delivery is the usual RECOVERY diag
(kernel-only) for S1 and ROM-RAM for S2/S3. Every write to the PMIC, GPIO or chip is listed below; anything not
listed is a read.

### S1: SPI, power, handshake, ROM-side identification (kernel only, no lhd, no patch)

Goal: prove that the bus, pins, power and chip are alive, using only the transactions 4.9 performs before the
patch download. Then leave the chip as found (off).

Note on "read the MCU firmware version": **the ESW revision (19121600) and WHOAMI (0x55) are answered by the RAM
patch**, so they can't be read without the download. S1 instead reads the ROM-side identity: the SSI/HSI registers
4.9 reads on every ttyBCM open, optionally plus the ROM `GetVersion` RPC (S1b).

Files (new or changed, T):
- `drivers/soc/samsung/crownlte-sec-class.c` (class "sec", `sec_device_create`).
- `drivers/sensorhub/brcm/bbdpl/bcm_gps_spi.c`: adapted transport, with ttyBCM **not** registered in S1 builds
  (`samsung,shub-selftest-only`).
- `drivers/sensorhub/brcm/crownlte_shub_selftest.c`: the selftest thread.
- `drivers/gps/sec_gps_bcm47531.c`: adapted, GPS_PWR_EN attribute group.
- `drivers/soc/samsung/acpm/acpm_mfd.c` + `drivers/mfd/s2mps18-acpm.c`: `shub_rail` policy + `s2mps18_shub_rail_set(bool)`.
- DT: `exynos9810-crownlte-sensorhub.dtsi` (all disabled) + `exynos9810-crownlte-delayed-ufs-shub-diag.dts`.
- defconfig fragment: SPI, SPI_S3C64XX, EXYNOS_USI, SENSORS_SSP_BBD_612 (S1 subset).

DT sketch (status "disabled" in the dtsi, enabled in the diag DTS):
```dts
/ {
	aliases { spi20 = &spi_20; };

	sysreg_chub_usi00: syscon@13a13000 {
		compatible = "samsung,exynos9810-chub-usi-sysreg", "syscon";
		reg = <0x0 0x13a13000 0x0 0x4>;
	};
	usi_chub00: usi@13ac00c0 {                    /* root level: keep 13ac0000.spi path */
		compatible = "samsung,exynos9810-usi";
		reg = <0x0 0x13ac00c0 0x0 0x20>;
		clocks = <&legacy_cmu GATE_USI_CHUB00>, <&legacy_cmu CHUB_USI00>;
		clock-names = "pclk", "ipclk";
		samsung,sysreg = <&sysreg_chub_usi00 0>;
		samsung,mode = <USI_V2_SPI>;
		samsung,clkreq-on;
	};
	spi_20: spi@13ac0000 {                         /* root level, 4.9 name */
		compatible = "samsung,exynos9810-spi", "samsung,exynos850-spi";
		reg = <0x0 0x13ac0000 0x0 0xc0>;
		interrupts = <GIC_SPI 104 IRQ_TYPE_LEVEL_HIGH>;
		clocks = <&legacy_cmu GATE_USI_CHUB00>, <&legacy_cmu CHUB_USI00>;
		clock-names = "spi", "spi_busclk0";
		pinctrl-names = "default";
		pinctrl-0 = <&shub_spi_bus>;               /* gph0-0,1 func2 np; gph0-2 func2 pd */
		cs-gpios = <&gph0 3 GPIO_ACTIVE_LOW>;
		num-cs = <1>;
		#address-cells = <1>; #size-cells = <0>;
		samsung,usi = <&usi_chub00>;               /* glue checks SW_CONF == SPI */

		sensorhub@0 {
			compatible = "ssp,BCM4773";
			reg = <0>;
			spi-max-frequency = <25000000>;
			spi-cpol; spi-cpha;
			pinctrl-names = "default";
			pinctrl-0 = <&ssp_host_req &ssp_mcu_req &ssp_mcu_resp &ssp_ap_sleep &ssp_shub_int>;
			ssp-host-req = <&gpa1 7 0>;
			ssp-mcu-req  = <&gph0 5 0>;
			ssp-mcu-resp = <&gph0 4 0>;
			ssp-batch-wake-irq = <&gpa0 5 0>;          /* AP_SLEEP output */
			ssp-shub-int = <&gpa4 0 0>;
			/* verbatim 4.9 values, DTS49:8002-8011 */
			ssp-acc-position = <6>; ssp-mag-position = <6>; ssp-sns-combination = <0>;
			ssp-ap-rev = <1>; ssp-glass-type = <0>; ssp-mag-type = <1>;
			ssp-mag-array = [1f 55 55 37 49 ae 00 b0 d7 37 e3 43 05 3f 01 8a f4 1a 14 61 bb ff 75 ff 0c 31 86];
			ssp-thermi-up = [...]; ssp-thermi-sub = [...];
			samsung,shub-selftest-ms = <112000>;
		};
	};
	gps {
		compatible = "samsung,exynos54xx-bcm4753";
		pinctrl-names = "hub_en";
		pinctrl-0 = <&ssp_gps_pwr_en>;             /* gpg0-2 out, pd, pdn prev */
		gpios = <&gpg0 2 GPIO_ACTIVE_HIGH>;
	};
};
```
(Use the root-level `#address-cells = <2>` form that T's root uses. Pin configs are copied from DTS49:423-449,
801-840, 2644-2652.)

Selftest sequence (one kthread at `samsung,shub-selftest-ms`; each step logs; any failure skips to "power down"):
1. **Read-only pre-dump:** SW_CONF @0x13A13000, USI_CON/USI_OPTION @0x13AC00C4/C8, `L37CTRL`, levels of GPS_PWR_EN,
   MCU_RESP, HOST_REQ, `clk_get_rate(CHUB_USI00)`. Abort if `L37CTRL & 0x3f != 0x2c` or SW_CONF != SPI.
2. **Write W1 (PMIC, gated):** L37CTRL enable bits := 4.9 value; read back; `msleep(20)` (as `set_mcu_power`).
3. **Write W2 (GPIO):** GPS_PWR_EN := 1 (nSTANDBY released); wait 120 µs + settle 50 ms (lhd "ClearNstdbyPin() delay: 120us").
4. **Hello:** MCU_REQ := 1, poll MCU_RESP ≤ 100 ms (4.9 `bcm477x_hello`); log the time.
5. **SSI debug reads** (exactly the 4.9 `bcm_reg32Iread` sequences, AHB read type 0x02) and compare with LOG49:791-794:
   - `HSI_CTRL  @40104090: 00000000 00000000 00000007 000034E8` (first word is a known artefact)
   - `RNGDMA_RX @40104040: 00914548 00002FFF 00000001 00000000`
   - `RNGDMA_TX @40104060: 00917548 000017FF 00000000 00000000`
   - `ADL_ABR   @401040A0: 00600000 00000001 00000000 00080000`
   Report "match/differ" per word; the ROM state should match, while DMA pointers may differ.
6. **S1b (optional, separate DTB `-shub-ver-diag`):** the ROM GetVersion RPC (autobaud 0x80×32, then the
   14-byte RPC from `bcm_gps_regs.c:62-66`) and print the response. This is a stream message, not a memory
   write, and it is what lhd sends first. Expect a version consistent with `AsicVersion 0x004775B0`.
7. **Power down (always):** MCU_REQ := 0, GPS_PWR_EN := 0, L37CTRL enable := 00 (restoring the pre-dump byte), read back.
8. `shub selftest: PASS|FAIL <reason>`.

Expected log lines (S1):
```
shub selftest: pre sw_conf=0x2 usi_con=0x0 usi_opt=0x? l37ctrl=0x2c pwr_en=0 mcu_resp=0 host_req=0 src_rate=<Hz>
shub selftest: ldo37 on l37ctrl 0x2c -> 0x6c        (on-value pending decision 8.1)
shub selftest: nstdby high
shub selftest: hello ok after N ms
shub selftest: REG HSI_CTRL  @40104090: 00000000 00000000 00000007 000034e8 (ref match 4/4)
shub selftest: REG RNGDMA_RX @40104040: ...
shub selftest: REG RNGDMA_TX @40104060: ...
shub selftest: REG ADL_ABR   @401040a0: ...
shub selftest: power down l37ctrl 0x6c -> 0x2c pwr_en=0
shub selftest: PASS
```
Risks (S1): CHUB block access faulting if a CHUB clock is gated (mitigation: the pre-dump reads SW_CONF before
any SPI register access; clocks are marked ignore-unused). Clock set_rate failing on the legacy VCLK (log and fall
back). Wrong LDO37 on-value (mitigation: owner OK, readback, immediate restore; the 1.8 V vsel is never touched).
Chip not answering (FAIL, power down; no retries beyond 4.9's). The 6.12 IRQ-PIO path for >64-byte frames is first
exercised in S2; S1 transfers are ≤ 32 bytes.

### S2: the HAL path for basic sensors (ROM-RAM, vendor lhd and HAL)

Prerequisite: the owner agrees that lhd's per-boot **RAM** patch download is not "firmware flashing" (section 8).

Files: the full verbatim import + adaptation (section 3); IIO compat (3.3); `crownlte-sec-class`; LDO37 policy
sealed with `shub_rail`; the BBD hard guards (section 6); DT as in S1, without `selftest-ms` (or with
`samsung,shub-selftest-passive`: logs only).
ROM-RAM: no overlay changes are needed for lhd, the HAL or ueventd (they are vendor, identical to 4.9). Add
`vendor/etc/sensor/lhd.conf` and `vendor/bin/hw/lhd` to the PID1 sha-verify list. `/data/vendor/gps` is RAM.

Self-test:
- Kernel (passive, ~60 s after boot): print `bytes_per_datum` per IIO device against the table in 2.3; print
  `bbd: patch sha256 ok`; print the hub state counters (resets, timeouts).
- Over adb (ROM-RAM adb over NCM works): `cat /sys/class/sensors/ssp_sensor/mcu_rev` → `BR0119121600,BR0119121600`;
  `cat …/sensor_state`; `dumpsys sensorservice` (sensor list, recent events: accelerometer |a| ≈ 9.8 m/s² at rest,
  light changes when covered, proximity near/far). **Never read `mcu_reset`** (reading it resets the hub,
  `factory/mcu_bcm4774.c:38-46` `mcu_reset_show`). Do not cat whole directories.
- Expected kernel log, the same as LOG49:753-1604, in this order:
```
[SSP] set_mcu_power: enable vdd_mcu_1p8(0), is enabled 0
[SSP] set_mcu_power: enable vdd_mcu_1p8(1), is enabled 1
bcm_spi_open++ ... [SSPBBD]: SPI @ ...: Full Duplex Strm mode, 2B Len, with FC, Frame Len 8192 : tx ctrl 58, rx ctrl 70
[SSPBBD]: REG HSI_CTRL  @ : ...           (same 4 lines as S1)
bbd_control : BBD:PassThru=1 / [SSPBBD] PatchTimer Start
bbd_common_open ... bbd_patch           (x2)
bbd_control : BBD:PassThru=0 / ESW:READY
[SSP]: MCU sensor init done / [SSPBBD] Sensors of MCU are ready!
[SSP] MCU device ID = 85, reading ID = 85
[SSP] Sensor Posision A : 6, G : 6, M: 6, P: 0
[SSP]: state: 1111111111111000001111111011010111111
[SSP] FUSE ROM Data 128 , 128, 128
[SSP] MCU Firm Rev : New = 19121600
[SSP] get_accel_range - Range : 8
[SSP] batch 0: delay ..., ...            (HAL activity)
```
Risks (S2): an IIO layout mismatch (caught by the kernel check); `sensors.grip.so` / `sensors.bio.so` finding no
devices (check whether the Samsung impl tolerates zero sensors from a sub-HAL in logcat; if not, a ROM-RAM overlay
drops them from `hals.conf`); lhd SELinux denials (ROM-RAM is permissive); LHD-killer interplay with a slow first
patch download (10 s timer, `bbd_enable_lk`); missing calibration (accel cal is 0,0,0 on 4.9 too; gyro and mag offsets
will be uncalibrated); `hh_write_all_data` and colour-ID writes refused (expected log lines, not errors); the 6.12 PIO
path at 25 MHz under load (the download must finish within lhd's timeouts).

### S3: the rest (fusion, wake-up sensors, batching, suspend, recovery, GNSS)

- **Fusion** (rotation vector, game rotation, step counter, significant motion, tilt, pickup, wake-up motion) runs
  inside ESW. The AP side is only IIO devices + `batch_io` flags, which arrive with S2. Test each through
  `dumpsys sensorservice` / an app.
- **Wake-up sensors and batching:** `/dev/batch_io` with timeouts; HIFI batching needs SHUB_INT gpa4-0 as an
  IRQ (add the alive muxed EINT to T pinctrl, 4.9 DT `pinctrl@14050000` wakeup-interrupt-controller); `ssp_batch_wq`.
- **Suspend:** `ssp_suspend` AP_SLEEP pulse (gpa0-5), `MSG2SSP_AP_STATUS_RESUME` on resume, the BBD PM notifier
  calling SSP suspend through `dummy_spi` (rework to direct calls), HOST_REQ as a wake source → needs the EINT wake
  mask from `suspend-design.md` (gpa1 bit 15). LDO37 stays on across suspend (4.9 never turns it off in suspend).
- **Recovery:** forced timeout → `work_ssp_reset` → LHD killer → lhd restart → re-download (RAM). Verify the reset
  counters and that `LheMaxMcuResetTries` stops a loop.
- **Others:** thermistor → battery overheat notify (needs the battery driver API); `SSP_MOTOR_CALLBACK` (haptics driver
  hook); mobeam/barcode (IR LED; low priority); `register_rw`, `mcu_test`, `mcu_sleep_test`, sensor selftests
  (factory; keep read-only or refuse writes).
- **Calibration (owner decision):** a read-only RAM import of `/efs/FactoryApp/{gyro_cal_data, calibration_data,
  baro_delta, hw_offset}` (like the FRP RAM copy), fed to the kernel through a new write-once sysfs attribute.
  Never kernel file I/O.
- **GNSS:** `gpsd` + `vendor.samsung.hardware.gnss@2.0-service` use the same lhd. Test after S2 ("GPSD:CORE_ON",
  LOG49:2649).

Risks (S3): the wake mask is not written in T today; muxed EINT for gpa4; the suspend path is untested in T as a whole;
`register_rw` misuse.

---

## 6. Hard guard: 6.12 never updates the hub firmware

Facts the guard rests on: the chip runs ROM + a RAM patch (PatchAddress 0x00800161, re-downloaded at every power-up);
4.9 contains no flash or update code; the kernel builds every SSI control byte, so userspace has no debug-mode access (`bcm_gps_spi.c:268, 281`).

| # | Guard | Where |
|---|---|---|
| G1 | **Import allowlist and forbidden-symbol check.** Only the files in 3.1 are imported. A Kbuild step fails the build if any of `update_mcu_bin`, `forced_to_download`, `ssp_dl`, `bootloader`, `request_firmware`, `mcu_update`, `bbd_urgent_patch`, `bcm_filsafe_reset_CPU`, `bcm_reg32Iwrite`, `RegWrite` (other than the HSI_ERROR_STATUS W1C) appears in `drivers/sensorhub/` | `drivers/sensorhub/brcm/Kbuild` + `scripts/crownlte-shub-guard.sh` |
| G2 | **Pinned patch.** At init, compute sha256 over `bbd_patch[]`. Register `/dev/bbd_patch` only if it equals `5568dff0…0537` (851,283 bytes) and the header says `AsicVersion=0x004775B0`, `Name=bcm_sensorhub_a1`, `PatchAddress=0x00800161`; else log `bbd: patch hash mismatch, refusing` and leave lhd without a patch (sensors stay off) | `bbd.c` init |
| G3 | `/dev/bbd_patch` stays read-only (no `.write`, no ioctl, `O_WRONLY` open → -EPERM). Never create `/dev/bbd_urgent_patch`, `/dev/bbd_reliable`, `bbd_sio`, or an `mcu_update` attribute | `bbd.c` fops, `ssp_sysfs.c` |
| G4 | Kernel-originated SSI debug-mode writes are limited to the `HSI_ERROR_STATUS` W1C; `bcm_gps_mem.c` (failsafe reset and dump) is not imported; `bcm477x_rpc_get_version` only in the S1b selftest | transport |
| G5 | `/dev/ttyBCM` writes are always framed by the kernel as `SSI_MODE_STREAM` (unchanged 4.9 logic); add a `WARN_ON_ONCE` if any code path ever sets `SSI_MODE_DEBUG` outside the allowlisted register read and W1C helpers | `bcm_gps_spi.c` |
| G6 | **lhd configuration pinned.** The ROM-RAM PID1 sha-verifies `vendor/bin/hw/lhd` and `vendor/etc/sensor/lhd.conf` (no `UrgentPatch=`, `LhePatch=/dev/bbd_patch`). The selftest greps the conf at runtime and logs it | rom-ram src |
| G7 | **No kernel file I/O** (not firmware, but irreversible-class `/efs`): delete the `filp_open`, `vfs_*` paths in `accel_lsm6dsl.c:211-220`, `magnetic_common.c:539, 939-952` (**hub-triggered** by a MAG cal packet, `ssp_data.c:812`), `light_colorid.c:160-170, 241-251, 646-656`, `ssp_debug.c:60-119`, `ssp_dump.c:84-94`, `ssp_sensorhub.c:550`; stores return -EPERM with a log line | factory/*, ssp_* |
| G8 | **PMIC:** `shub_rail` allows only L37CTRL bits [7:6], vsel must be 0x2c, seal-once policy, every other register refused | `acpm_mfd.c` |

Residual risk, stated plainly: from the kernel side it can't be proven that lhd's proprietary RPC set has no
non-volatile command (e.g. OTP). This is mitigated because the binary, config and patch bytes are identical to what the
owner's 4.9 runs at every boot (G2, G6).

---

## 7. Proposed commit series (all build-only until reviewed)

1. `sec: crownlte minimal sec class` (+ `sec_device_create`).
2. `mfd/acpm: exact LDO37 rail policy for the BCM47752 hub` (+ s2mps18 helper, selftest readback).
3. `spi: s3c64xx: exynos9810 compatible` (or DT-only fallback compatible) + `defconfig: SPI, S3C64XX, EXYNOS_USI`.
4. `gps: sec_gps_bcm47531 for 6.12 (GPS_PWR_EN attribute group)`.
5. `sensorhub: verbatim import of 4.9 drivers/sensorhub/brcm (built set) + crown q_os patch` (provenance only, not built).
6. `sensorhub: 6.12 adaptation (compat header, wakeup sources, time64, timers, class_create, spi remove, gpiod)`.
7. `sensorhub: remove kernel file I/O (G7)` + `sensorhub: firmware guards (G1-G5)`.
8. `iio: SSP legacy record layout (CONFIG_IIO_SSP_LEGACY_LAYOUT)` + `sensorhub: IIO on 6.12`.
9. `sensorhub: S1 selftest` + DT dtsi + `delayed-ufs-shub-diag` (+ `-shub-ver-diag`) DTBs.
10. `Documentation/crownlte/sensorhub-port.md`.

S1 needs commits 1-4 and 9 plus a transport-only subset of 6; S2 needs everything.

## 8. Open questions and owner decisions

1. **S1 write W1 (LDO37 on/off through ACPM):** reversible; 4.9 lhd does the same at every boot. The on-value `0x6C` is
   derived from 4.9 source. Confirm it with a read on a running 4.9 phone if possible
   (e.g. `/sys/kernel/debug/regulator/…/state` or the S2MPS18 debugfs), or approve using it as derived.
2. **S2 RAM patch download by the vendor lhd:** volatile, byte-identical (G2), the same as every 4.9 boot. Please confirm
   it is acceptable and not covered by the "no hub firmware flashing" rule.
3. **Calibration:** run uncalibrated (default), or import a read-only RAM copy of `/efs/FactoryApp/*`.
4. Reference data that would de-risk S2, captured read-only on 4.9 over adb if available: `dumpsys sensorservice`
   sensor list, `ls -l /sys/bus/iio/devices/*/scan_elements/` and `cat …/in_timestamp_type` for each device (confirms
   the record sizes in 2.3), `cat /sys/class/sensors/ssp_sensor/mcu_rev`.
