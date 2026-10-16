"""List STARBUG bundles (c.li zero,N + N insts) in an ELF and flag hazards that MPACT hides.

MPACT executes bundle members sequentially and in any lane; the RTL executes lanes in parallel
and wires only lane 0 to the LSU, the PC redirect and the CSR unit. Flags:
  RAW  - a member reads a register (x or f) written by an earlier member
         (MPACT: new value, RTL: old value)
  WAW  - two members write the same register (RTL controller assumes this never happens)
  SIZE - N > 4 (RTL accepts 1..4 only)
  LANE - a load/store, branch/jump, CSR/system op or auipc in slot 1..3 (RTL: silently dropped,
         or computed against the bundle's PC)
  MEM  - more than one memory op in the bundle (one LSU)
Exit status is 1 if any bundle is flagged, so this can gate a build.

Usage: python3 bundle_check.py <elf> [--quiet]
"""
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from vliw_model import Op  # noqa: E402

LANE0_ONLY = {"load", "store", "branch", "jump", "csr", "sys"}


def check(members):
    """members: [Op] in slot order. Returns a list of problem strings."""
    notes, written = [], {}
    if len(members) > 4:
        notes.append(f"SIZE {len(members)} > 4")
    if sum(o.is_mem for o in members) > 1:
        notes.append("MEM more than one memory op")
    for slot, o in enumerate(members):
        if slot and (o.cls in LANE0_ONLY or o.mn == "auipc"):
            notes.append(f"LANE {o.mn} in slot {slot}")
        for r in o.src:
            if r in written:
                notes.append(f"RAW {r} ({written[r]} -> {o.mn} {o.ops})")
        for r in o.dst:
            if r in written:
                notes.append(f"WAW {r}")
            written[r] = f"{o.mn} {o.ops}"
    return notes


def main(elf, quiet=False):
    dis = subprocess.run(["riscv64-unknown-elf-objdump", "-d", "-M", "no-aliases,numeric", elf],
                         capture_output=True, text=True, check=True).stdout
    insts = []
    for line in dis.splitlines():
        m = re.match(r"^\s*([0-9a-f]+):\s+([0-9a-f]{4}|[0-9a-f]{8})\s+(\S+)\s*(.*)$", line)
        if m:
            insts.append(Op(int(m.group(1), 16), len(m.group(2)) // 2, m.group(3),
                            m.group(4).split("#")[0].strip()))
    i, n_bundles, problems = 0, 0, 0
    while i < len(insts):
        o = insts[i]
        if o.cls == "hint" and o.hint_n > 0:
            members = insts[i + 1:i + 1 + o.hint_n]
            n_bundles += 1
            notes = check(members)
            problems += bool(notes)
            if notes or not quiet:
                body = " | ".join(f"{m.mn} {m.ops}" for m in members)
                flag = ("  <-- " + "; ".join(notes)) if notes else ""
                print(f"{o.pc:08x} [{o.hint_n}] {body}{flag}")
            i += 1 + o.hint_n
        else:
            i += 1
    print(f"\n{n_bundles} bundles, {problems} with hazards or lane violations MPACT would not catch")
    return problems


if __name__ == "__main__":
    sys.exit(1 if main(sys.argv[1], "--quiet" in sys.argv[2:]) else 0)
