#!/bin/bash
# usage: v23a-summary.sh <serial-ready.bin>
# Panel/S2DOS05 summary of one v23a boot (kernel kmsg records only).
F=$1
grep -a '^[0-9],[0-9]*,[0-9]*,-;' "$F" | sed 's/^[0-9],[0-9]*,\([0-9]*\),-;/\1 /' |
  grep -a -E 's2dos05-diag: target_ms=[0-9]+ (TOPSYS|EN=|reg=0x0a|reg=0x0e|reg=0x11)|s2dos05-diag:.*IRQ reg=0x11|panel-recovery:.*(RESULT|level|EE|refus|skip)|EE 0x[0-9a-f]{2} \(|panel-bright:.*(RESULT|MATCH|FAIL)' |
  grep -a -v -E 'key (on|off)|^[^ ]* panel-recovery: (exit|init|copr|display off) ' |
  awk '{ t=$1/1e6; $1=""; printf "%8.3f%s\n", t, $0 }' | cut -c1-200
