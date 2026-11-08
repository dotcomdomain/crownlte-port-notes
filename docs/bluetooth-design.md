# crownlte Bluetooth on 6.12: design (BCM4361 BT over UART)

Status: research only, nothing built or run. Date 2026-11-06. Author: research sub-agent (read-only).

Path abbreviations used in the evidence:

| Tag | Path |
|---|---|
| K | `/root/crownlte-hid-kernel` (the running 4.9 kernel) |
| KDT | `K/arch/arm64/boot/dts/exynos/exynos9810-crownlte_eur_open_26.dts` (flat, decompiled 4.9 DT) |
| KCFG | `K/out/.config` (the built 4.9 config) |
| V | `/mnt/ssd/host-builds/kernel-upgrade/rom-ram/testroot/vendor` (Lineage 23 vendor, ro) |
| L | `V/lib64/libbt-vendor.so` (addresses are vaddrs, `llvm-objdump-18 -d`) |
| T | `android16-6.12-port`, branch `crownlte-wifi` (15cebae03) |
| TCFG | `panel-bright-work/ko-wifi-s1/config` (the Wi-Fi build .config) |

## 0. Short answers

1. **Hardware path.** BT HCI runs on the dedicated BT UART `UART_BT` = 4.9 `uart@10840000` (ttySAC1), GIC SPI 422,
   pins gpq0-0..3 (RXD/TXD/RTS/CTS, alive pinctrl), 256-byte FIFO, hardware flow control. libbt opens it at
   115200 and then switches to 3,000,000 baud with CRTSCTS. BT_REG_ON is **not a SoC pin**: it is pin 7 (P0_7) of a
   PCAL6524 I2C GPIO expander (addr 0x22 on `hsi2c@10930000` = USI10_I2C, SPI 437, pins gpp6-2/3). BT_WAKE
   (host to chip) = gpg2-0, output, active high. BT_HOST_WAKE (chip to host) = gpa2-3, alive wakeup EINT
   (GIC SPI 55), active high.
2. **What the HAL needs.** HIDL `android.hardware.bluetooth@1.0-service` (AOSP default impl) + Samsung-flavoured
   `libbt-vendor.so`. Kernel side: `/dev/ttySAC1`; a `RFKILL_TYPE_BLUETOOTH` rfkill whose sysfs
   `state` is **`/sys/devices/platform/bluetooth/rfkill/rfkill0/state`** (vendor rc chowns `rfkill0`, vendor sepolicy
   labels exactly that path). There are **no** `/proc/bluetooth/sleep` nodes and no LPM ioctls: libbt's BT_WAKE hooks
   are no-ops, so 4.9 drove BT_WAKE entirely in the kernel (serial-core TX hook + 1 s timer).
3. **Mainline or port?** Port the 4.9 `samsung,bcm43xx` rfkill/LPM driver (404 lines, ~250 after modernisation). Mainline
   `hci_bcm`/`btbcm` over serdev would take the UART away from userspace (no `/dev/ttySAC1`) and this vendor has no
   btlinux HAL; `rfkill-gpio` powers the radio **on** at probe and has no BT_WAKE / host-wake handling. Kernel
   `CONFIG_BT` is not needed (4.9 had it on, but no kernel HCI driver: `BT_HCIUART` was off).
4. **Wi-Fi coexistence.** Separate enables (WL_REG_ON = gpg0-5 SoC pin, BT_REG_ON = expander P0_7), no regulators in
   DT for either, no cross-sequencing in 4.9, no WBRC (Wi-Fi/BT REG_ON coordinator) built in either kernel. Coex is
   in-chip firmware. The one real interaction on 6.12 is the **rfkill index**: enabling `CONFIG_RFKILL` makes cfg80211
   register a `phy0` rfkill too, and the BT one must stay `rfkill0`.
5. **Plan.** B1 = UART_BT + expander bus + rfkill driver (BT_WAKE held high while powered), a kernel-only self-test
   (HCI Reset + Read Local Version over ttySAC1) and then the real HAL in ROM-RAM. B2 = 4.9-style LPM (TX hook in
   samsung_tty, 1 s idle timer, host-wake IRQ with wakeup sources and IRQ wake), after suspend S0.

## 1. Hardware path (Q1)

| Item | Value | Evidence |
|---|---|---|
| BT DT node | `/bluetooth`, `compatible = "samsung,bcm43xx"`, `gpios = <expander 7 0>, <gpg2 0 0>, <gpa2 3 15>` (order: bt_en, bt_wake, bt_hostwake), pinctrl bt-hostwake + bt-btwake | KDT:13918-13924; K drivers/bluetooth/bcm43xx.c:272,286,301 (of_get_gpio index 0/1/2) |
| BT_REG_ON (bt_en) | PCAL6524 pin 7 (port 0 bit 7; 4.9 global GPIO 307), output, high = on | KDT:13920 (`<76 7 0>`), phandle 76 = `pcal6524@22` KDT:6761-6779 (`gpio_start = <300>`) |
| Expander bus | `hsi2c@10930000` (4.9 alias hsi2c25), SPI 437, clocks `<PERIC1_USI_I2C>`(282) + `<GATE_USI10_I2C>`(260) = **USI10_I2C**, pins gpp6-2/gpp6-3 function 3, no `clock-frequency` | KDT:6741-6760, :2933-2939 (hsi2c25-bus); T include/dt-bindings/clock/exynos9810.h:111,122,144 (250+10, 250+32) |
| USI10_I2C SW_CONF | 4.9 never writes it: `usi@10811040` (PERIC1 SYSREG + 0x1040, the USI10_I2C slot) is `disabled`; the hsi2c driver only clears USI_CON reset (+0xC4) | KDT PERIC1 usi@1081101C..10811048 list (USI06..USI11 + _I2C, 12 slots), usi@1081103C = USI10 "i2c" KDT:5553; K drivers/i2c/busses/i2c-exynos5.c:188,535-544 |
| Expander reset | gpg3-6 (PERIC1), active low; 4.9 drives it high and **pulses it low 100 us at subsys_initcall**, then reprograms all 24 pins (all inputs, out 0, push-pull, drive 0.25x, no latch) and panics after 6 failed tries | KDT:6771 (`pcal6524,reset-gpio = <gpg3 6 0>`), :3369-3377; K drivers/gpio/gpio-pcal6524.c:418-450, :230-300, :850-880, :985 |
| Other expander users | P0_1 DP sbu_sw_sel, P0_4 DP aux_sw_oe, P1_0 (8) wireless-charging `wpc_en`, P1_1 (9) NFC firm, P1_3 (11) NFC VEN, P2_5 (21) force-sensor channel | KDT:5235-5236, :13643, :6354, :6352, :6042 |
| BT_WAKE (dev wake) | gpg2-0 (PERIC0), output, pud 0, con-pdn 3 (keep state in power-down); high = assert | KDT:2655-2662 (bt-btwake), :1764 (gpg2 = phandle 127); bcm43xx.c:131 |
| BT_HOST_WAKE | gpa2-3 (ALIVE), input, pud 0; IRQ via `gpio_to_irq`, rising at request, ISR flips rising/falling; high = chip has data | KDT:531-537, :199-206 (gpa2 = SPI 52..59, so pin 3 = SPI 55); bcm43xx.c:209-210,252-254; T exynos9810-pinctrl.dtsi:54-66 |
| UART | `uart@10840000`, 4.9 alias `uart1` -> ttySAC1, `samsung,exynos-uart` + `samsung,usi-serial-v2` + `separate-uart-clk`, `fifo-size = <256>`, SPI 422, clocks `GATE_UART_BT`(262) + `UART_BT`(268) | KDT:9460-9475, :4610; T exynos9810.h:124,130 |
| UART pins | gpq0-0 RXD, gpq0-1 TXD, gpq0-2 RTS, gpq0-3 CTS; function 2, pull-up (pud 3), drv 0; extra state uart1-bus-rxd pud 3 | KDT:268-282 (`pinctrl-0 = <288 289>`) |
| UART USI wrapper | no SYSREG SW_CONF for UART_BT (a fixed-function BT UART, not a USI slot); the driver still resets USI_CON (+0xC4: write 1, then 0) and sets USI_OPTION (+0xC8) = CLKREQ_ON at every port init/resume | K drivers/tty/serial/samsung.c:1555-1570, :1046, :1730; K include/linux/serial_s3c.h:48-54 |
| Baud / framing | libbt: 115200 for the first HCI Reset, then 3,000,000; CRTSCTS set in `userial_vendor_open` | L `hw_config_start` 0x6980/0x6acc (`mov/movk 0x2dc6c0`), `userial_vendor_init` 0x91a8, `userial_vendor_open` 0x9340 (`orr 0x80000000` = CRTSCTS); strings "bt vendor lib: set UART baud 115200", "baud_2: set UART baud %i" |
| Baud clock | 4.9 uses `clk_get_rate(UART_BT)` and never sets a rate (bootloader/CAL rate) | K samsung.c:1176-1186; no `clk_set_rate` in samsung.c |
| Flow control in idle | 4.9 SICD notifier drops AFC and forces RTS high on SICD entry, restores on exit; `rts-gpio-control` is **not** set on crownlte UART1, so no RTS pin muxing | K samsung.c:1985-2045, :2122-2123; KDT:9460-9475 (no such property) |
| Power-on timing | (4.9, LPM build) set irq wake, BT_REG_ON=1, `msleep(100)`; off: release irq wake, BT_REG_ON=0 | bcm43xx.c:80-115 |
| LPM (4.9) | every `uart_start()` calls `wake_peer` -> BT_WAKE=1 + 1 s hrtimer; timer expiry -> BT_WAKE=0 + 0.5 s wakelock; host-wake high -> wakelock held, low -> 1 s timed wakelock; `exynos_update_ip_idle_status("bluetooth")` blocks SICD while busy | K serial_core.c:130-137, samsung.c:121-122,930-931,1373-1378,1442; bcm43xx.c:122-232,240; KDT:4900 (idle-ip "bluetooth") |
| Chip sleep params | libbt sends HCI VSC 0xFC27 (Write Sleep Mode) with `01 18 18 01 01 01 01 00 00 00 00 00`: UART sleep mode 1, idle thresholds 0x18, **BT_WAKE active high, HOST_WAKE active high**, allow sleep in SCO, combine sleep+LPM; HAL idle timeout = 1500 ms | L `hw_lpm_enable` 0x7918-0x79c4, `.data` 0xb02a (12 bytes), `hw_lpm_get_idle_timeout` 0x7a08 |
| Supplies | none in DT (BT node has no supply; Wi-Fi node neither) | KDT:13918-13924; T Documentation/crownlte/wifi-port.md section 1 "Regulators / clocks" |
| 4.9 config | `CONFIG_BT=y`, `BT_BCM43XX=y`, `BT_HCIUART` **not set**, `RFKILL=y`, `GPIO_PCAL6524=y`, `SERIAL_SAMSUNG_HWACG=y`, `RFKILL_GPIO` not set | KCFG |

Hardware path in one picture:

```
                 PERIC1 hsi2c@10930000 (USI10_I2C, SPI 437, gpp6-2/3)
 AP ──I2C 0x22──> PCAL6524  P0_7 ─────────────────────> BT_REG_ON ┐
 AP gpg2-0 ────────────────────────────────────────────> BT_WAKE   │  BCM4361 (B2)
 AP gpa2-3 (EINT, SPI 55) <──────────────────────────── HOST_WAKE  │  BT core
 AP UART_BT 0x10840000 (SPI 422) gpq0-0..3 <─RX/TX/RTS/CTS─> UART  ┘
 AP gpg0-5 ────────────────────────────────────────────> WL_REG_ON ─ Wi-Fi core (PCIe ch0, already up)
```

## 2. What the vendor Bluetooth HAL needs from the kernel (Q2)

| Need | Value | Evidence |
|---|---|---|
| HAL service | `vendor.bluetooth-1-0` = `/vendor/bin/hw/android.hardware.bluetooth@1.0-service`, user/group `bluetooth`, capabilities `BLOCK_SUSPEND NET_ADMIN SYS_NICE` (NET_ADMIN is required: rfkill `state` writes check `CAP_NET_ADMIN`) | V/etc/init/android.hardware.bluetooth@1.0-service.rc:1-7; T net/rfkill/core.c:769,827 |
| HAL impl | AOSP `VendorInterface` dlopens `libbt-vendor.so`, logs `lpm_timeout_ms`, then enables LPM through the vendor lib | V/lib64/hw/android.hardware.bluetooth@1.0-impl.so strings |
| tty | `/dev/ttySAC1` hard-coded (property `sys.bluetooth.tty` also referenced). Kernel must name the port ttySAC**1**: 6.12 samsung_tty takes the index from the `serial` alias | L strings "/dev/ttySAC1", "sys.bluetooth.tty"; T samsung_tty.c:1951 |
| tty perms | vendor rc `on boot`: `chown bluetooth bluetooth /dev/ttySAC1`, `chmod 0660`; SELinux type `bt_device` for `/dev/ttySAC[0-9]` | V/etc/init/init.samsungexynos9810.rc:442 (on boot), :645-647; V/etc/selinux/vendor_file_contexts:280; vendor_sepolicy.cil:3421 |
| rfkill lookup | libbt scans `/sys/class/rfkill/rfkill%d/type` for `bluetooth`, then writes `0`/`1` to `.../rfkill%d/state`; name not checked; `ro.rfkilldisabled` skips rfkill | L strings "/sys/class/rfkill/rfkill%d/type", ".../state", "init_rfkill", "ro.rfkilldisabled" |
| rfkill perms | rc chowns and chmods **`rfkill0`** only (`state` 0660 bluetooth, `type` bluetooth) | init.samsungexynos9810.rc:650-652 |
| rfkill label | `genfscon sysfs "/devices/platform/bluetooth/rfkill/rfkill0/state"` -> `sysfs_rfkill_writable`, which `hal_bluetooth_default` may write. So: platform device **named `bluetooth`** (DT node `/bluetooth`) and rfkill index **0** | V/etc/selinux/vendor_sepolicy.cil:45, :4753 |
| LPM nodes | **none**. No `/proc/bluetooth/sleep/*` strings; `upio_set()` only stores the requested state; `userial_vendor_ioctl()` is a bare `ret`. BT_WAKE is the kernel's job | L `upio_set` 0x9af8-0x9b34, `userial_vendor_ioctl` 0x95e0, `hw_lpm_set_wake_state` 0x7a40 |
| Firmware | read directly by libbt from `/vendor/firmware/` (no `request_firmware`): `bcm4361B2_murata.hcd` (108066 B, sha256 5cb87462...eb96) or `bcm4361B2_semco.hcd` (108067 B, sha256 11f81616...26ea). Module type from `/data/vendor/conn/.cid.info`, else an HCI "module info" query (OTP) | V/firmware; L strings "/vendor/firmware/", "/data/vendor/conn/.cid.info", "hw_config_access_module_info", "Fail to find right firmware" |
| Config file | `/etc/bluetooth/bt_vendor.conf` is looked up but does not exist; compiled defaults apply | L string; no bt_vendor.conf in system or vendor |
| BD address | `ro.bt.bdaddr_path = /efs/bluetooth/bt_addr` (userspace) | init.samsungexynos9810.rc:649, :655-661 |
| SELinux in ROM-RAM | permissive (Lineage userdebug), so label/index mistakes will **not** show as failures there; check `ls -Z` and avc lines | rom-ram/RUN-SHEET.md:75 |

## 3. Mainline drivers or a port? (Q3)

| Option | Verdict | Why (evidence) |
|---|---|---|
| serdev + `hci_bcm` (+ `btbcm`) | **No** | A serdev child under the UART turns the port into a serdev controller: no `/dev/ttySAC1` for libbt. The vendor ships only the HIDL @1.0 default HAL, no `@1.0-service.btlinux` (V/bin/hw). `hci_bcm` has no 4361 entry (T drivers/bluetooth/hci_bcm.c:1597-1610). 4.9 also never used the kernel HCI layer (KCFG `BT_HCIUART` not set). |
| `rfkill-gpio` | Stop-gap only | DT support exists, `shutdown-gpios` on the expander would work, but probe drives both GPIOs to logical 1 = **radio on at boot** (T net/rfkill/rfkill-gpio.c:151-155), no post-power-on delay (4.9 waits 100 ms), no BT_WAKE semantics, no host-wake. |
| Port 4.9 `samsung,bcm43xx` | **Yes** | Small (K drivers/bluetooth/bcm43xx.c, 404 lines), exactly the HAL contract (rfkill "bcm43xx Bluetooth", type bluetooth, starts blocked). Rewrite with gpiod, `devm_*`, wakeup sources instead of wakelocks, no `exynos_update_ip_idle_status` (no SICD on 6.12). |
| Kernel `CONFIG_BT` | Leave off | The HAL path needs only the tty and rfkill. Put the new driver outside `drivers/bluetooth/` (that directory is only entered with `CONFIG_BT`), e.g. `drivers/misc/`. |

The serial side needs two small 6.12 changes, because there is no 9810 UART node or compatible yet
(T exynos9810.dtsi and exynos9810-crownlte-clocks.dtsi have no serial node; T samsung_tty.c:2625-2640 has no 9810 entry):

- **samsung_tty**: add `samsung,exynos9810-uart` with GS101-like data: 32-bit register access, `has_fracval`, one baud
  clock (`clk_uart_baud0`), FIFO size from `samsung,uart-fifosize` (T samsung_tty.c:2493-2517, :1988-2000, :1873).
  4.9 used byte access to UTXH/URXH (K samsung.c:614,733,757), so either width works; 32-bit matches the later USI
  UARTs and is the safer choice.
- **USI_CON reset**: 6.12 samsung_tty never touches +0xC4/+0xC8. The 9810 USI wrapper (T drivers/soc/samsung/exynos-usi.c:87-103)
  does, but requires `samsung,sysreg` (:231-237) and reads SW_CONF back (:176-196). UART_BT (and USI10_I2C, see below) have no
  SW_CONF in 4.9. Add an opt-in `samsung,no-sw-conf` to the 9810 variant: skip the SW_CONF write and readback, still clear
  USI_CON reset and set CLKREQ_ON, same resume_noirq. This keeps one pattern for touch, BT and the expander bus.

The expander needs no new driver: mainline `gpio-pca953x` supports `nxp,pcal6524` (T drivers/gpio/gpio-pca953x.c:104, :1336).
Its probe marks an empty regcache dirty and syncs output/direction from it, then writes the polarity-inversion
registers to 0 (:1156, :1019-1040); with an empty cache the sync should write nothing, so the other expander pins
(NFC, wireless charging, DP switch) keep their bootloader state. **Verify this** with the register dump in B1a (before/after).
Do **not** give it `reset-gpios` at first: pca953x would only de-assert reset, but if the line is currently low,
releasing it resets every expander pin, which 4.9 then re-initialised and 6.12 would not.

## 4. Coexistence with Wi-Fi (Q4)

| Topic | Finding | Evidence |
|---|---|---|
| Power enables | Independent: WL_REG_ON gpg0-5 (SoC), BT_REG_ON expander P0_7. Each core can be on alone | T wifi-port.md section 1 (WLAN_REG_ON); KDT:13920 |
| Supplies, clocks | No regulator or clock in DT for either core. The 32 kHz sleep clock (LPO) is shared on Broadcom combos; Wi-Fi already runs on 6.12, so it is present (inference, not measured) | KDT:13918-13924; wifi-port.md "Regulators / clocks: none" |
| Sequencing in 4.9 | None between the two. DHD powers WL_REG_ON at `late_initcall_sync`, then powers the chip off again until wlan0 up; BT power only on HAL request | T Kbuild:231 (`USE_LATE_INITCALL_SYNC`), dhd_linux.c:13634; bcm43xx.c:80-115 |
| WBRC | `wb_regon_coordinator.c` exists in the DHD tree but is built in neither kernel | T Kbuild:300 (dhd-y list); no wb_regon object in K out/drivers/net/wireless/bcmdhd_101_16 |
| BT coex | In-chip (firmware GCI/SECI). DHD only has the Android `BTCOEXMODE` DHCP helper (`wl_cfg_btcoex.o`), built in both | T Kbuild:300; K out dir |
| rfkill index | 6.12 Wi-Fi config has `RFKILL` off (TCFG). Turning it on makes cfg80211 register a wiphy rfkill in `wiphy_register()` (T wl_cfg80211.c:10251), called from `dhd_module_init` at `late_initcall_sync`. The BT driver probes at `device_initcall`, or from deferred probe, which `deferred_probe_initcall` (late_initcall) flushes. So BT should get `rfkill0`, Wi-Fi `rfkill1`. If the BT probe ends up after that point (async probe, a missing provider), BT becomes rfkill1 and the vendor rc/sepolicy (section 2) no longer match | TCFG; T dhd_linux.c:13634 |
| Recommendations | Never toggle both REG_ONs at the same instant (Broadcom combo convention, not checked against a datasheet here). A BT power-cycle while Wi-Fi is on resets only the BT core; to recover a wedged chip, both must go low. Test BT with Wi-Fi off, then with Wi-Fi scanning, then toggle Wi-Fi while BT is on | — |

## 5. Staged plan (Q5)

Build rules for whoever implements this: one worktree in /dev/shm at most (cap ~2.5 GB, check `df -h /dev/shm`),
base on `crownlte-wifi` (or integration-5 once reviewed), Sol review loop before any phone trial.

### B1: UART + expander + rfkill (BT_WAKE held high), HAL switches BT on and loads firmware

**Kernel files**

| File | Change |
|---|---|
| `drivers/soc/samsung/exynos-usi.c` | `samsung,no-sw-conf` for the 9810 variant (no sysreg lookup, no SW_CONF write/readback; USI_CON reset clear + CLKREQ_ON; log `USIv2 configured: sw_conf=none con=%08x option=%08x`) |
| `drivers/tty/serial/samsung_tty.c` | compatible `samsung,exynos9810-uart` (GS101-like drv data, name "Samsung Exynos9810 UART") |
| `drivers/misc/samsung-bcm43xx-bt.c` (+ Kconfig `SAMSUNG_BCM43XX_BT` depends on RFKILL && GPIOLIB && OF, Makefile) | Platform driver for `samsung,bcm43xx`. gpiod `bt-en` (may sleep: I2C expander), `bt-wake`, `bt-host-wake`. Probe: BT_WAKE low, BT_REG_ON low (4.9 parity, bcm43xx.c:317-319), rfkill "bcm43xx Bluetooth", type bluetooth, registered **blocked**, log the index and sysfs path. Unblock: BT_WAKE=1, BT_REG_ON=1, `msleep(100)` (bcm43xx.c:93-99). Block: BT_REG_ON=0, BT_WAKE=0. Host-wake: request a both-edge IRQ and only count edges (no IRQ wake, no wakeup source in B1). Self-test thread (below), armed only by DT |
| `arch/arm64/configs/crownlte_bt.fragment` | `RFKILL=y`, `GPIO_PCA953X=y`, `SAMSUNG_BCM43XX_BT=y`; keep `# CONFIG_BT is not set`, `# CONFIG_SERIAL_DEV_BUS is not set`, `# CONFIG_RFKILL_GPIO is not set` |
| `Documentation/crownlte/bt-port.md` | this design, results |

**DT** (`exynos9810-crownlte-clocks.dtsi`, everything `disabled` by default; alive pins drop the 4.9 `*-pdn` fields as for Wi-Fi):

```dts
/ {
	aliases { serial1 = &serial_bt; };          /* ttySAC1 for libbt */

	bluetooth {                                  /* node name fixed by vendor genfscon */
		compatible = "samsung,bcm43xx";
		bt-en-gpios = <&bt_expander 7 GPIO_ACTIVE_HIGH>;
		bt-wake-gpios = <&gpg2 0 GPIO_ACTIVE_HIGH>;
		bt-host-wake-gpios = <&gpa2 3 GPIO_ACTIVE_HIGH>;
		interrupt-parent = <&gpa2>;
		interrupts = <3 IRQ_TYPE_EDGE_BOTH>;
		pinctrl-names = "default";
		pinctrl-0 = <&bt_hostwake &bt_btwake>;
		status = "disabled";
	};
};
&pinctrl_alive {
	uart_bt_bus: uart-bt-bus-pins { samsung,pins = "gpq0-3", "gpq0-2", "gpq0-1", "gpq0-0";
		samsung,pin-function = <2>; samsung,pin-pud = <3>; samsung,pin-drv = <0>; };
	bt_hostwake: bt-hostwake-pins { samsung,pins = "gpa2-3";
		samsung,pin-function = <0xf>; samsung,pin-pud = <0>; };
};
&pinctrl_peric0 {
	bt_btwake: bt-btwake-pins { samsung,pins = "gpg2-0"; samsung,pin-function = <1>;
		samsung,pin-pud = <0>; samsung,pin-con-pdn = <3>; samsung,pin-pud-pdn = <0>; };
};
&pinctrl_peric1 {
	hsi2c25_bus: hsi2c25-bus-pins { samsung,pins = "gpp6-2", "gpp6-3";
		samsung,pin-function = <3>; samsung,pin-pud = <0>; samsung,pin-drv = <0>; };
};
&soc {
	usi_uart_bt: usi@108400c0 {
		compatible = "samsung,exynos9810-usi";
		reg = <0x108400c0 0x20>;
		clocks = <&legacy_cmu GATE_UART_BT>, <&legacy_cmu UART_BT>;
		clock-names = "pclk", "ipclk";
		samsung,mode = <USI_V2_UART>;
		samsung,no-sw-conf;
		samsung,clkreq-on;
		#address-cells = <1>; #size-cells = <1>; ranges;
		status = "disabled";
		serial_bt: serial@10840000 {
			compatible = "samsung,exynos9810-uart";
			reg = <0x10840000 0xc0>;
			interrupts = <GIC_SPI 422 IRQ_TYPE_LEVEL_HIGH>;
			clocks = <&legacy_cmu GATE_UART_BT>, <&legacy_cmu UART_BT>;
			clock-names = "uart", "clk_uart_baud0";
			samsung,uart-fifosize = <256>;
			pinctrl-names = "default";
			pinctrl-0 = <&uart_bt_bus>;
			status = "disabled";
		};
	};
	usi_i2c_exp: usi@109300c0 {
		compatible = "samsung,exynos9810-usi";
		reg = <0x109300c0 0x20>;
		clocks = <&legacy_cmu GATE_USI10_I2C>, <&legacy_cmu PERIC1_USI_I2C>;
		clock-names = "pclk", "ipclk";
		samsung,mode = <USI_V2_I2C>;
		samsung,no-sw-conf;              /* 4.9 usi@10811040 is disabled */
		#address-cells = <1>; #size-cells = <1>; ranges;
		status = "disabled";
		hsi2c_exp: i2c@10930000 {
			compatible = "samsung,exynos9810-hsi2c";
			reg = <0x10930000 0xc0>;
			interrupts = <GIC_SPI 437 IRQ_TYPE_LEVEL_HIGH>;
			#address-cells = <1>; #size-cells = <0>;
			clocks = <&legacy_cmu PERIC1_USI_I2C>, <&legacy_cmu GATE_USI10_I2C>;
			clock-names = "hsi2c", "hsi2c_pclk";
			clock-frequency = <100000>;
			pinctrl-names = "default";
			pinctrl-0 = <&hsi2c25_bus>;
			status = "disabled";
			bt_expander: gpio@22 {
				compatible = "nxp,pcal6524";
				reg = <0x22>;
				gpio-controller; #gpio-cells = <2>;
				/* no reset-gpios, no interrupts: see section 3 */
				status = "disabled";
			};
		};
	};
};
```

Clock IDs exist in the legacy CMU: `GATE_UART_BT`, `UART_BT`, `GATE_USI10_I2C`, `PERIC1_USI_I2C`
(T drivers/clk/samsung/clk-exynos9810-legacy.c:339, :444, :348, :456). Pin banks exist: gpa2 (EINTW), gpq0 (EINTN), gpg2,
gpp6, gpg3 (T drivers/pinctrl/samsung/pinctrl-exynos-arm64.c:1080-1160). 4.9 wrote the UART SPI with flags 0; use level-high.

Vehicles:
- `exynos9810-crownlte-delayed-ufs-bt-diag.dts`: kernel-only (delayed-UFS RECOVERY, `pack-acpm-q.py`, READY only).
- ROM-RAM: `panel-bright-work/dtb/romram-bt-b1-gen.py`, generated from the **romram-wifi-s1** flat DTS so Wi-Fi and BT are
  both present (this is what exercises the rfkill ordering). Not in the tree, like the other ROM-RAM DTBs.

**Self-test** (`bt selftest:`, armed by `samsung,bt-selftest`, delay `samsung,bt-selftest-ms`, optional
`samsung,bt-selftest-no-power`). Runs once in a kthread; skips with `SKIP userspace owns BT` if the rfkill is unblocked or the tty is open.

1. `bus`: SYSREG PERIC1 +0x1040 SW_CONF (read-only, informational), GPG3CON/DAT for the expander reset line (read-only),
   expander bound, full PCAL6524 register dump (input, output, config, polarity, pulls; 3 ports each), compared with
   the dump at probe time.
2. `uart`: ttySAC1 registered, USI_CON/USI_OPTION readback, `clk_get_rate(UART_BT)` and the baud error for 115200 and
   3,000,000 (needs at least 48 MHz; FAIL above 2 %).
3. `rfkill`: index == 0, type bluetooth, path `/sys/devices/platform/bluetooth/rfkill/rfkill0`. FAIL otherwise.
4. (`-no-power` stops here with `RESULT PASS-NOPOWER`.) `power`: driver's own power-on path under the rfkill mutex.
5. `hci`: open ttySAC1 inside the kernel the way `drivers/tty/serdev/serdev-ttyport.c` does (`tty_kopen_exclusive`,
   `tty->ops->open`, temporary `client_ops` for receive, `tty_set_termios` 115200 8N1 CRTSCTS). Send HCI Reset
   `01 03 0c 00`, expect `04 0e 04 01 03 0c 00` within 1 s (3 tries, 100 ms apart). Then Read Local Version `01 01 10 00`:
   print HCI version, LMP subversion, manufacturer (Broadcom = 0x000f). Read BD_ADDR `01 09 10 00`: print the OUI only.
   No patchram download (that is the HAL's job). Count host-wake edges during the exchange.
6. `power off`: BT_REG_ON low, BT_WAKE low, tty closed and released, rfkill still blocked. `RESULT PASS ...` or
   `RESULT FAIL stage=<bus|uart|rfkill|power|hci>`.

Expected kernel lines (proposed wording):
```
exynos-usi 109300c0.usi: USIv2 configured: sw_conf=none con=00000000 option=0000000x
exynos-usi 108400c0.usi: USIv2 configured: sw_conf=none con=00000000 option=00000002
10840000.serial: ttySAC1 at MMIO 0x10840000 (irq = N, base_baud = M) is a S3C6400/10
pca953x N-0022: ...    (gpiochip registered, 24 lines; N = i2c adapter number)
samsung-bcm43xx-bt bluetooth: rfkill "bcm43xx Bluetooth" = rfkill0 (blocked), host-wake irq N
bt selftest: bus sw_conf=0x... exp_reset=1 exp[out]=...,...,... exp[cfg]=...,...,...
bt selftest: uart clk=NNN Hz err115200=+x.xx% err3000000=+x.xx%
bt selftest: rfkill0 /sys/devices/platform/bluetooth/rfkill/rfkill0 type=bluetooth
bt selftest: power on (bt_wake=1 reg_on=1), hci reset ok in NN ms (try 1)
bt selftest: local version hci=0x.. rev=0x.... lmp_subver=0x.... manuf=0x000f bdaddr_oui=xx:xx:xx
bt selftest: power off, host_wake_edges=N
bt selftest: RESULT PASS
```

Trial order:
- **B1a-0** kernel-only, `-no-power`: proves the I2C bus, expander, UART and rfkill without powering anything.
- **B1a-1** kernel-only, full: proves power, UART pins, clock, flow control (HCI Reset at 115200).
- **B1b** ROM-RAM + adb, no kernel self-test (or the skip path), Wi-Fi present:
  ```
  cat /sys/class/rfkill/rfkill*/name /sys/class/rfkill/rfkill*/type     # rfkill0 = bcm43xx Bluetooth / bluetooth
  ls -lZ /dev/ttySAC1 /sys/devices/platform/bluetooth/rfkill/rfkill0/state  # bluetooth:bluetooth, bt_device / sysfs_rfkill_writable
  cmd bluetooth_manager enable; cmd bluetooth_manager wait-for-state:STATE_ON   # fallback: svc bluetooth enable
  dumpsys bluetooth_manager | grep -iE "state|address|name"
  logcat -d | grep -E "bt_hwcfg|bt_userial|bt_upio|bt_vendor|bluetooth@1.0"
  dmesg | grep -E "avc.*(bluetooth|bt_device|rfkill)"                  # permissive hides denials
  ```
  Expected logcat (strings from L): `upio_set_bluetooth_power(on: 1)`, `userial vendor open: opening /dev/ttySAC1`,
  `bt vendor lib: set UART baud 115200`, `Chipset BCM4361B2` / `Target name = [BCM4361B2]`,
  `FW patchfile: /vendor/firmware/bcm4361B2_semco.hcd` (or murata; Wi-Fi picked nvram r02b, so semco is likely),
  `bt vendor lib baud_2: set UART baud 3000000`, `Settlement delay -- N ms`, `vendor lib fwcfg completed`, and from the
  HAL `lpm_timeout_ms 1500`. Then: enable Wi-Fi + `cmd wifi start-scan` with BT on; turn Wi-Fi off/on; BT must stay ON.
  Disable BT: rfkill0 state 0, kernel `power off`.

Risks (B1):
1. **Expander bus config.** USI10_I2C SW_CONF is unknown on 6.12 (4.9 never wrote it). If I2C times out, B1a-0 shows the
   SW_CONF value; fallback = write I2C mode there (one register, a 4.9 deviation).
2. **Expander held in reset** (gpg3-6 low): NACK at 0x22. Fallback = pinctrl output-high / `reset-gpios`, knowing that
   releasing reset puts all 24 pins back to inputs.
3. **Side effects on other expander pins** (NFC VEN, wireless-charging enable, DP switch). The driver touches only P0_7; the
   B1a dump before/after must show nothing else changed.
4. **rfkill index / sysfs name.** Any other rfkill registered first breaks the enforcing build silently in permissive ROM-RAM.
   Self-test step 3 fails loudly.
5. **ttySAC index.** Without the `serial1` alias the port becomes ttySAC0 and libbt cannot open it.
6. **Baud clock.** If the CAL rate of `UART_BT` cannot do 3 Mbaud within 2 %, a `clk_set_rate` is needed (4.9 never set one).
7. **Chip sleep.** libbt puts the chip into UART sleep mode (BT_WAKE active high). B1 keeps BT_WAKE high whenever BT is
   powered, so the chip never sleeps: correct, but costs idle current while BT is on (not while off).
8. **Firmware choice.** No `.cid.info` in ROM-RAM; libbt then asks the module over HCI. If both fail it logs
   `Fail to find right firmware`.
9. **In-kernel tty use** in the self-test is the most delicate code; keep it under the DT flag and mirror serdev-ttyport
   exactly. If review finds it too risky, drop step 5 and rely on B1b.

### B2: wake / LPM (4.9 parity) and suspend

Depends on: B1 PASS, suspend S0 (EINT wakeup mask; gpa2-3 is already listed in roadmap/suspend-design.md:173).

**Kernel files**

| File | Change |
|---|---|
| `drivers/tty/serial/samsung_tty.c` | Per-line `wake_peer` callback (exported `samsung_tty_set_wake_peer(line, fn)`), called at the top of `s3c24xx_serial_start_tx()` (same point and context as 4.9 `__uart_start` -> `wake_peer`, port lock held, IRQs off; K serial_core.c:130-137) |
| `drivers/misc/samsung-bcm43xx-bt.c` | LPM: callback sets BT_WAKE=1 (SoC GPIO, atomic-safe; never the expander) and restarts a 1 s hrtimer; expiry sets BT_WAKE=0 and `pm_wakeup_event(500 ms)`. Wakeup sources `BT_bt_wake`, `BT_host_wake`. Host-wake IRQ both edges: high -> `pm_stay_awake`, low -> `pm_wakeup_event(1000 ms)` (4.9: bcm43xx.c:179-232). `enable_irq_wake` on power-on, `disable_irq_wake` on power-off (bcm43xx.c:86-109). Only arm the hook while powered. B1's "BT_WAKE held high" becomes a module parameter `lpm=0` fallback |
| DT | `wakeup-source;` on `/bluetooth`; EINT wake mask for gpa2-3 via the suspend branch |
| debugfs | `/sys/kernel/debug/bcm43xx_bt/{lpm,bt_wake_asserts,host_wake_edges}` |

Not ported: `exynos_update_ip_idle_status("bluetooth")` and the SICD RTS/AFC dance (K samsung.c:1985-2045). 6.12 has no SICD.
When system idle modes are ported, the BT UART must again drop AFC and hold RTS high on SICD entry.

**Self-test (B2, ROM-RAM + adb)**: BT on through the HAL; idle 3 s -> `lpm` shows bt_wake=0; any HCI traffic (BT on/off,
a BLE scan from an app) -> `bt_wake_asserts` and `host_wake_edges` grow; `/sys/kernel/debug/wakeup_sources` shows
`BT_host_wake`/`BT_bt_wake` activity; BT keeps working for 10 minutes of idle with periodic traffic (no HCI timeouts in
logcat). With suspend S0: BT on, `echo mem > /sys/power/state`, a nearby device or a host-wake edge resumes the system,
wake reason names the gpa2-3 EINT, and HCI keeps working after resume.

Expected lines: `samsung-bcm43xx-bt bluetooth: lpm on (idle 1000 ms)`, debug-level `bt_wake 1->0`, `host_wake 1/0`,
and in suspend logs a wakeup by `BT_host_wake`.

Risks (B2):
1. **Lost host TX** if any TX path skips `start_tx` while the chip sleeps -> HCI command timeout, HAL restarts BT.
   Watch for `HCI ... timeout` in logcat.
2. **Atomic context**: the hook runs under the port spinlock; it must not touch the I2C expander or sleep.
3. **RX across suspend**: crownlte 4.9 had no RTS-GPIO forcing, it relied on host-wake + the chip waiting. If bytes are lost
   around suspend/resume, add RTS high on suspend (4.9 `rts-gpio-control` path, samsung.c:2350-2357).
4. **Host-wake storm or stuck high** keeps the system awake; the counters show it.
5. **Polarity**: libbt configures both wake lines active high (L 0xb02a); 4.9 matches. Do not "fix" it in DT.

## 6. Open points (not verifiable read-only)

- UART_BT clock rate on 6.12 (B1a step 2).
- Expander reset line state and USI10_I2C SW_CONF as left by the bootloader (B1a-0).
- Whether pca953x probe writes the output/direction registers on this chip (B1a dump).
- BCM4361B2 LMP subversion / exact firmware file chosen (B1a-1, B1b).
- Whether `cmd bluetooth_manager enable` works from the adb shell user on this build (fallback `svc bluetooth enable`).
- BT idle current with BT_WAKE held high (B1) vs LPM (B2): needs a battery current readout, which 6.12 does not have yet.

## 7. Related documents

- roadmap/ROADMAP.md:313 (Bluetooth row; its "hciattach/hci_uart, HCI reset" check is replaced by the HAL path above)
- roadmap/vendor-abi/vendor-abi.json:883 (BT vendor ABI)
- roadmap/suspend-design.md:173 (gpa2-3 BT host wake as a wake source)
- T Documentation/crownlte/wifi-port.md (Wi-Fi on the same chip)
