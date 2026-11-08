#!/bin/bash
# Run trials in order; stop on the first failure or low battery.
# usage: run-queue.sh name1 name2 ...   (names like q24-rtclatch; dir trial-acpm-<name>-s2-20261104)
set -u
export MSYS_NO_PATHCONV=1
A=/c/Users/Patrick/tools/platform-tools/adb.exe
T=/c/Users/Patrick/tools
for n in "$@"; do
  D=$T/trial-acpm-$n-s2-20261104
  [ -d "$D" ] || D=$T/trial-acpm-$n-20261104
  [ -f "$D/recovery.img" ] || { echo "STOP no trial dir for $n"; exit 4; }
  lvl=$($A shell dumpsys battery 2>/dev/null | tr -d '\r' | awk '$1=="level:"{print $2; exit}')
  echo "=== $n battery=$lvl% $(date +%H:%M:%S)"
  [ -n "$lvl" ] && [ "$lvl" -lt 15 ] && { echo "STOP low battery"; exit 3; }
  bash $T/run-trial.sh "$D" 200 > "$D/run.log" 2>&1
  rc=$?
  tail -4 "$D/run.log"
  echo "=== $n rc=$rc serial=$(stat -c %s $D/serial-ready.bin 2>/dev/null)"
  [ $rc -eq 0 ] && grep -q "RECOVERY restored" "$D/run.log" || { echo "STOP after $n"; exit 1; }
done
echo "=== queue done"
