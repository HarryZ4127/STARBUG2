#!/usr/bin/env python3
"""Check the timing model against Leo's RTL measurements (Questa, cvw lm/fp,
examples/C/embench_starbug/presentation_assets/benchmark_summary.csv).

For each DSP benchmark, model three runs and compare with the RTL cycle counts:
  scalar ELF on RV32GC, hand-VLIW ELF on RV32GC (hints are nops), hand-VLIW ELF on Starbug.
Caveat: our ELFs are built with GCC 10.2; the RTL runs used Leo's (newer) GCC, so the C parts
of each program differ slightly. The hand-written VLIW kernels themselves are identical.
"""
import csv
import json
import os
import sys
from multiprocessing import Pool

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import vliw_model as vm

H = os.path.expanduser(os.environ.get("STARBUG_HOME", "~/starbug"))  # WSL/Linux work dir: traces, ELFs, JSON
EX = f"{H}/ex/embench_starbug"
TR = f"{H}/traces"
CVW_LMFP = os.path.expanduser(os.environ.get("CVW_LMFP", "~/starbug/cvw-lmfp"))  # checkout of rvru/cvw lm/fp
CSV = f"{CVW_LMFP}/examples/C/embench_starbug/presentation_assets/benchmark_summary.csv"


def one(b):
    e = lambda v: (f"{EX}/{b}/{b}_{v}_wally.elf", f"{TR}/{b}_{v}_wally.trace")
    s = vm.run_as_written(*e("scalar"), vm.RV32GC, honor_hints=False)
    v = vm.run_as_written(*e("vliw"), vm.RV32GC, honor_hints=False)
    sb = vm.run_as_written(*e("vliw"), vm.SB1)
    return b, s, v, sb


if __name__ == "__main__":
    rtl = {r["benchmark"]: r for r in csv.DictReader(open(CSV))}
    with Pool(10) as p:
        rows = p.map(one, sorted(rtl))
    out = []
    print(f"{'benchmark':36s} {'scalar model/RTL':>22s} {'VLIW@RV32GC model/RTL':>24s} {'Starbug model/RTL':>22s}")
    for b, s, v, sb in rows:
        r = rtl[b]
        trip = []
        for m, key in ((s, "scalar_cycles"), (v, "vliw_cycles_rv32gc"), (sb, "starbug_cycles")):
            trip.append((m["cycles"], int(r[key]), m["cycles"] / int(r[key]) - 1))
        print(f"{b:36s} " + " ".join(f"{a:>9d}/{c:<8d}{e:+6.1%}" for a, c, e in trip))
        out.append(dict(benchmark=b, scalar=s, vliw_rv32gc=v, starbug=sb,
                        rtl={k: int(r[k]) for k in ("scalar_cycles", "vliw_cycles_rv32gc", "starbug_cycles")}))
    json.dump(out, open(f"{H}/validate.json", "w"), indent=1)
