#!/usr/bin/env python3
# Scan llvm-objdump -d output (stdin) for 32-bit constants built with mov/movz+movk or
# mov #imm (full) that look like Linux ioctl numbers of a given type byte.
# usage: llvm-objdump -d --no-show-raw-insn X.so | ioctl_scan.py TYPE [TYPE...]   (TYPE as hex char code, e.g. 80 46 49)
import sys, re, collections
types = {int(t,16) for t in sys.argv[1:]}
mov = re.compile(r"\t(?:mov|movz)\t([wx]\d+), #(0x[0-9a-f]+|\d+)(?:, lsl #(\d+))?$")
movk = re.compile(r"\tmovk\t([wx]\d+), #(0x[0-9a-f]+|\d+), lsl #16$")
regs = {}
hits = collections.Counter()
first = {}
for line in sys.stdin:
    line = line.split("//")[0].rstrip()
    m = mov.search(line)
    if m:
        r = m.group(1)[1:]; v = int(m.group(2),0) << int(m.group(3) or 0)
        regs[r] = v
        full = v
    else:
        m = movk.search(line)
        if not m:
            continue
        r = m.group(1)[1:]
        if r not in regs: continue
        full = (regs.pop(r) & 0xffff) | (int(m.group(2),0) << 16)
    full &= 0xffffffff
    d = full >> 30; size = (full >> 16) & 0x3fff; typ = (full >> 8) & 0xff; nr = full & 0xff
    if typ in types and d != 0 and size and size < 0x1000:
        hits[full] += 1
        first.setdefault(full, line.split(":")[0].strip())
dirs = {1:"_IOW",2:"_IOR",3:"_IOWR"}
for k in sorted(hits, key=lambda x: ((x>>8)&0xff, x&0xff, x)):
    print("0x%08x %s(0x%02x,%3d,size=%d) count=%d first=%s" % (k, dirs[k>>30], (k>>8)&0xff, k&0xff, (k>>16)&0x3fff, hits[k], first[k]))
