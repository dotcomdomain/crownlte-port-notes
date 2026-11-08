# crownlte port notes

Design notes, trial scripts and benchmark results for the Linux 6.12 port to the Galaxy
Note9 (SM-N960F).

- [`STATUS.md`](STATUS.md): what works on the phone now.
- `docs/`: roadmap and per-feature designs (dated snapshots; BT, suspend, C2 idle, audio, sensors, TEE).
- `scripts/pc/`: Windows trial runners and serial/adb capture.
- `scripts/host/`: build-host packing scripts.
- `results/`: CPU benchmark, 4.9 vs 6.12.

Code: [kernel](https://github.com/dotcomdomain/crownlte-linux-6.12) ·
[boot shim](https://github.com/dotcomdomain/crownlte-boot-shim) ·
[ROM-in-RAM](https://github.com/dotcomdomain/crownlte-rom-ram)
