# Status (2026-11-08)

What has run on the phone with the 6.12 kernel. The docs in `docs/` are dated design
snapshots, so this file wins wherever they disagree.

## Works on hardware

| Area | Notes |
|---|---|
| Boot, 8 CPUs, UFS, USB | serial log and ADB over USB; UFS has only been used read-only so far |
| LineageOS 23 userspace | boots from RAM to the setup wizard and home screen (RAM `/data`) |
| Display | DPU composition through the SysMMU, brightness. Some boots come up black before the kernel starts (panel/bootloader); a panel recovery path rescues most of them |
| GPU (Mali-G72) | driver, power domain and DVFS; the Android UI renders on it |
| Touch | sec_ts Y771 |
| Wi-Fi | BCM4361; Android scans 2.4 and 5 GHz |
| Bluetooth | works in Android (branch `crownlte-bt`) |
| CPU performance | cpufreq, EAS, thermal, MIF/INT devfreq; CPU speed matches or beats 4.9 |
| CPU idle | big-core C2 (branch `crownlte-c2-runtime`) |
| Suspend groundwork | S0: freezer, devices and core level suspend/resume (branch `crownlte-suspend-s0`) |
| Battery | fuel gauge and thermistors (read-only), SoC ADC, charger watchdog kick |
| PMIC | RTC, touch power rails |
| Audio | probe only: ABOX DSP, codec and both amps answer; no sound yet |

## Not working yet

| Area | State |
|---|---|
| Sound output | stage A2 built and reviewed, not run |
| Real sleep (suspend to RAM) | S1 built, not run |
| LITTLE-core C2 idle | built and reviewed, not run |
| Writable `/data` | not mounted yet (needs the TEE for FBE keys) |
| Trustonic TEE | daemon runs; trustlet sessions are refused |
| Sensors, GNSS | designed, not built |
| Modem, camera, NFC, S Pen, vibration | not started or not verified |
