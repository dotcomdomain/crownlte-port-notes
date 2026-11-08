#!/bin/bash
# One RECOVERY diagnostic trial, end to end.
# usage: run-trial.sh <trial-dir> [capture-seconds] [capture-script]
#   trial-dir: Windows-side dir holding recovery.img (+ manifest.json)
#   capture-script: defaults to <trial-dir>/ready-capture.ps1 (READY frame only)
set -u
export MSYS_NO_PATHCONV=1
A=/c/Users/Patrick/tools/platform-tools/adb.exe
D=$1; SECS=${2:-180}; CAP=${3:-$D/ready-capture.ps1}
RESTORE=8763860c11a272cb6c341f2fc561e38adb5ea621a16ef7a5b083d9b6d28a8a0e
log() { echo "[$(date +%H:%M:%S)] $*"; }
WD=$(cygpath -w "$D")
H=$(sha256sum "$D/recovery.img" | cut -c1-64)
log "trial $D image $H"

log "push"
$A push "$WD\\recovery.img" /data/local/tmp/trial-recovery.img >/dev/null || { log "push failed"; exit 1; }
PH=$($A shell "su -c 'sha256sum /data/local/tmp/trial-recovery.img'" | cut -c1-64)
[ "$PH" = "$H" ] || { log "staged hash mismatch $PH"; exit 1; }

log "wait AshLooper boot=booted"
for i in $(seq 1 60); do
  s=$($A shell "su -c 'grep ^boot= /data/adb/modules/AshLooper/settings.prop'" | tr -d '\r')
  [ "$s" = boot=booted ] && break; sleep 10
done
[ "$s" = boot=booted ] || { log "not booted ($s)"; exit 1; }

log "flash RECOVERY"
$A shell "su -c 'dd if=/data/local/tmp/trial-recovery.img of=/dev/block/by-name/RECOVERY bs=4194304 conv=fsync 2>&1 | tail -1; sync'"
RH=$($A shell "su -c 'sha256sum /dev/block/by-name/RECOVERY'" | cut -c1-64)
BH=$($A shell "su -c 'sha256sum /dev/block/by-name/BOOT'" | cut -c1-64)
[ "$RH" = "$H" ] || { log "RECOVERY readback mismatch $RH"; exit 1; }
log "RECOVERY=$RH BOOT=$BH"

log "capture ${SECS}s + reboot recovery"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$(cygpath -w "$CAP")" -Seconds "$SECS" -Out "$WD\\serial-ready.bin" > "$D/capture.log" 2>&1 &
CP=$!
sleep 2
$A reboot recovery
wait $CP
log "capture done: $(tail -1 "$D/capture.log" | tr -d '\r')"

log "wait for Lineage"
for i in $(seq 1 ${RETURN_SECS:-420}); do
  $A get-state 2>/dev/null | grep -q device && break; sleep 1
done
$A get-state 2>/dev/null | grep -q device || { log "PHONE DID NOT RETURN (hang?)"; exit 2; }
for i in $(seq 1 120); do
  [ "$($A shell getprop sys.boot_completed 2>/dev/null | tr -d '\r')" = 1 ] && break; sleep 5
done
log "kernel $($A shell uname -r | tr -d '\r')"

$A shell "su -c 'cat /sys/fs/pstore/pmsg-ramoops-0 2>/dev/null'" > "$D/first-return-pmsg.bin"
$A shell "su -c 'cat /proc/reset_reason 2>/dev/null; tr \" \" \"\\n\" < /proc/cmdline | grep -i rst_stat'" > "$D/reset-reason.txt"
$A shell "su -c 'dd if=/data/local/tmp/bright-recovery.img of=/dev/block/by-name/RECOVERY bs=4194304 conv=fsync 2>&1 | tail -1; sync; rm -f /data/local/tmp/trial-recovery.img'"
RH=$($A shell "su -c 'sha256sum /dev/block/by-name/RECOVERY'" | cut -c1-64)
[ "$RH" = "$RESTORE" ] && log "RECOVERY restored" || log "RECOVERY RESTORE MISMATCH $RH"
log "serial bytes $(stat -c %s "$D/serial-ready.bin" 2>/dev/null)"
