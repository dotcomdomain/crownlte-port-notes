t() { echo $EPOCHREALTIME; }
for m in 01 80; do for r in 1 2 3; do s=$(t); taskset $m sh -c "dd if=/dev/zero bs=1048576 count=1500 2>/dev/null | sha256sum >/dev/null"; e=$(t); echo "mask=$m run=$r s=$(echo $s $e | awk '{printf "%.3f", $2-$1}')"; done; done
s=$(t); for i in 1 2 3 4 5 6 7 8; do (dd if=/dev/zero bs=1048576 count=800 2>/dev/null | sha256sum >/dev/null) & done; wait; e=$(t); echo "all8 s=$(echo $s $e | awk '{printf "%.3f", $2-$1}')"
echo temps $(cat /sys/class/thermal/thermal_zone*/temp)
