#!/bin/bash
# Wait for the phone to come back on Lineage (after a manual force restart),
# then restore the stock RECOVERY exactly like run-trial.sh does.
export MSYS_NO_PATHCONV=1
A=/c/Users/Patrick/tools/platform-tools/adb.exe
RESTORE=8763860c11a272cb6c341f2fc561e38adb5ea621a16ef7a5b083d9b6d28a8a0e
log() { echo "[$(date +%H:%M:%S)] $*"; }
until $A get-state 2>/dev/null | grep -q device; do sleep 10; done
log "phone back: waiting for boot_completed"
for i in $(seq 1 120); do [ "$($A shell getprop sys.boot_completed 2>/dev/null | tr -d '\r')" = 1 ] && break; sleep 5; done
log "kernel $($A shell uname -r | tr -d '\r')"
$A shell "su -c 'cat /proc/reset_reason 2>/dev/null'" | tr -d '\r'
$A shell "su -c 'dd if=/data/local/tmp/bright-recovery.img of=/dev/block/by-name/RECOVERY bs=4194304 conv=fsync 2>&1 | tail -1; sync; rm -f /data/local/tmp/trial-recovery.img'"
RH=$($A shell "su -c 'sha256sum /dev/block/by-name/RECOVERY'" | cut -c1-64)
BH=$($A shell "su -c 'sha256sum /dev/block/by-name/BOOT'" | cut -c1-64)
[ "$RH" = "$RESTORE" ] && log "RECOVERY restored ($RH)" || log "RECOVERY RESTORE MISMATCH $RH"
log "BOOT $BH"
