#!/bin/bash
set -euo pipefail
SRC=/mnt/ssd/host-builds/kernel-upgrade/src/close-range-fix-4.9
OUT=/mnt/ssd/host-builds/kernel-upgrade/build/close-range-fix-4.9
BASELINE=/root/kernel-upgrade/baseline/kernel.config
RELEASE=4.9.337-ies-g8665533db42e
trap 'result=$?; printf "%s\n" "$result" > "$OUT/build.status"' EXIT
test -z "$(git -C "$SRC" status --porcelain)"
test "$(git -C "$SRC" rev-parse HEAD)" = "$(cat "$OUT/source.commit")"
clang-18 --version > "$OUT/compiler.txt"
aarch64-linux-gnu-ld --version > "$OUT/linker.txt"
cp "$BASELINE" "$OUT/.config"
sha256sum "$BASELINE" "$OUT/.config" > "$OUT/config-input.sha256"
cd "$SRC"
make -j2 O="$OUT" ARCH=arm64 CC=clang-18 CROSS_COMPILE=aarch64-linux-gnu- CLANG_TRIPLE=aarch64-linux-gnu- KERNELRELEASE="$RELEASE" olddefconfig
cmp "$BASELINE" "$OUT/.config"
make -j2 O="$OUT" ARCH=arm64 CC=clang-18 CROSS_COMPILE=aarch64-linux-gnu- CLANG_TRIPLE=aarch64-linux-gnu- KERNELRELEASE="$RELEASE" Image dtbs
cmp "$BASELINE" "$OUT/.config"
test "$(git -C "$SRC" rev-parse HEAD)" = "$(cat "$OUT/source.commit")"
test -z "$(git -C "$SRC" status --porcelain)"
sha256sum "$OUT/arch/arm64/boot/Image" "$OUT/arch/arm64/boot/dts/exynos/exynos9810-crownlte_eur_open_26.dtb" "$OUT/.config" "$OUT/source.patch" > "$OUT/artifacts.sha256"

