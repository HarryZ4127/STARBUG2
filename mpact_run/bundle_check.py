"""List STARBUG bundles (c.li zero,N + N insts) in an ELF and flag hazards that MPACT hides.

MPACT executes bundle members sequentially, the RTL executes lanes in parallel, so:
  RAW  - a member reads a register written by an earlier member (MPACT: new value, RTL: old value)
  WAW  - two members write the same register (RTL controller assumes this never happens)
  SIZE - N > 4 (RTL accepts 1..4 only)
"""
import re
import subprocess
import sys

elf = sys.argv[1]
dis = subprocess.run(["riscv64-unknown-elf-objdump", "-d", "-M", "no-aliases,numeric", elf],
                     capture_output=True, text=True, check=True).stdout
insts = []  # (addr, mnemonic, operands)
for line in dis.splitlines():
    m = re.match(r"\s*([0-9a-f]+):\s+[0-9a-f]+\s+(\S+)\s*(.*)", line)
    if m:
        insts.append((int(m.group(1), 16), m.group(2), m.group(3).split("#")[0].strip()))

STORES_BRANCHES = re.compile(r"^(c\.)?(s[bhwd]|sw|fs[wd]|b\w+|c\.beqz|c\.bnez|c\.j|c\.jr|jalr?|c\.swsp)$")


def regs(ops):
    return re.findall(r"\bx(\d+)\b", ops)


def dst_src(mn, ops):
    r = regs(ops)
    if not r:
        return None, []
    if STORES_BRANCHES.match(mn):
        return None, r
    if mn.startswith("c.") and mn in ("c.add", "c.addi", "c.slli", "c.srli", "c.srai", "c.andi",
                                       "c.and", "c.or", "c.xor", "c.sub", "c.addi16sp"):
        return r[0], r  # two-operand compressed: rd is also a source
    return r[0], r[1:]


i, n_bundles, problems = 0, 0, 0
while i < len(insts):
    addr, mn, ops = insts[i]
    if mn == "c.li" and re.match(r"x0,\s*-?\d+", ops):
        n = int(ops.split(",")[1])
        members = insts[i + 1:i + 1 + n]
        n_bundles += 1
        written, notes = {}, []
        if n > 4:
            notes.append(f"SIZE {n} > 4")
        for (a, m2, o2) in members:
            d, s = dst_src(m2, o2)
            for r in s:
                if r in written and r != "0":
                    notes.append(f"RAW x{r} ({written[r]} -> {m2} {o2})")
            if d and d != "0":
                if d in written:
                    notes.append(f"WAW x{d}")
                written[d] = f"{m2} {o2}"
        body = " | ".join(f"{m2} {o2}" for (_, m2, o2) in members)
        flag = ("  <-- " + "; ".join(notes)) if notes else ""
        problems += bool(notes)
        print(f"{addr:08x} [{n}] {body}{flag}")
        i += 1 + n
    else:
        i += 1
print(f"\n{n_bundles} bundles, {problems} with hazards MPACT would not catch")
