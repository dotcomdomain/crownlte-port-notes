#!/bin/bash
# Repack queued trials with shim bbea959 (forced-reset bounce). name image dtb
set -eu
cd /mnt/ssd/host-builds/kernel-upgrade
SHIM=bbea9590ba9bf57c673cee723e4e6817c08cdea7
B=build
while read -r name img dtb; do
  [ -z "$name" ] && continue
  W=acpm-$name-s2
  [ -e "$W/recovery.img" ] && { echo "skip $W"; continue; }
  rm -rf "$W"
  IS=$(sha256sum "$img" | cut -c1-64); DS=$(sha256sum "$dtb" | cut -c1-64)
  SHIM=$SHIM IMAGE="$img" IMAGE_SHA256="$IS" DTB="$dtb" python3 pack-acpm-q-paths.py "$W" "$DS" > "$W.pack.log" 2>&1 || { echo "FAIL $W"; tail -5 "$W.pack.log"; exit 1; }
  git -C /root/kernel-upgrade/boot-shim/crownlte-usi-reset-diagnostic worktree remove --force "$PWD/$W/shim-src" 2>/dev/null || rm -rf "$W/shim-src"
  rm -rf "$W/shim-out"; mv "$W.pack.log" "$W/pack.log"
  echo "$W $(sha256sum $W/recovery.img | cut -c1-64)"
done <<LIST
q35-wdtst2 $B/watchdog-6.12.93/arch/arm64/boot/Image $B/watchdog-6.12.93/arch/arm64/boot/dts/exynos/exynos9810-crownlte-delayed-ufs-wdt-selftest2-diag.dtb
q36-wdtlpi $B/watchdog-6.12.93/arch/arm64/boot/Image $B/watchdog-6.12.93/arch/arm64/boot/dts/exynos/exynos9810-crownlte-delayed-ufs-wdt-selftest2-lpi-diag.dtb
q21b-idlea $B/idle-a2-6.12.93/arch/arm64/boot/Image $B/idle-a2-6.12.93/arch/arm64/boot/dts/exynos/exynos9810-crownlte-delayed-ufs-diag.dtb
q22b-idleb $B/idle-b-6.12.93/arch/arm64/boot/Image $B/idle-b-6.12.93/arch/arm64/boot/dts/exynos/exynos9810-crownlte-delayed-ufs-diag.dtb
q24-rtclatch $B/integration-android-6.12.93/arch/arm64/boot/Image $B/integration-android-6.12.93/arch/arm64/boot/dts/exynos/exynos9810-crownlte-delayed-ufs-rtc-latch-diag.dtb
q25-display $B/integration-android-6.12.93/arch/arm64/boot/Image $B/integration-android-6.12.93/arch/arm64/boot/dts/exynos/exynos9810-crownlte-delayed-ufs-diag.dtb
q26-display2 $B/display-6.12.93/s2/Image $B/display-6.12.93/s2/exynos9810-crownlte-delayed-ufs-display-s2-diag.dtb
q27-mali $B/mali-6.12.93/artifacts/Image $B/mali-6.12.93/artifacts/exynos9810-crownlte-delayed-ufs-diag.dtb
q28-dpuiommu $B/dpu-iommu-6.12.93/artifacts/Image $B/dpu-iommu-6.12.93/artifacts/exynos9810-crownlte-delayed-ufs-display-s2-iommu-diag.dtb
q29-tee $B/tee-6.12.93/artifacts/Image $B/tee-6.12.93/artifacts/exynos9810-crownlte-delayed-ufs-diag.dtb
q30-g3dpd $B/next-6.12.93/artifacts/Image $B/next-6.12.93/artifacts/exynos9810-crownlte-delayed-ufs-g3dpd-diag.dtb
q31-g3drt $B/next-6.12.93/artifacts/Image $B/next-6.12.93/artifacts/exynos9810-crownlte-delayed-ufs-g3dpd-runtime-diag.dtb
q32-touch $B/touch-6.12.93/artifacts/Image $B/touch-6.12.93/artifacts/exynos9810-crownlte-delayed-ufs-diag.dtb
q34-adc $B/adc-6.12.93/artifacts/Image $B/adc-6.12.93/artifacts/exynos9810-crownlte-delayed-ufs-diag.dtb
LIST
