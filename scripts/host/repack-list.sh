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
done
