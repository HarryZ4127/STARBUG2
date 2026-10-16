#!/usr/bin/env python3
"""Sensitivity to the compiler: the same branch/memory options scheduled from the plain -O3
build (no unrolling) instead of the unrolled build. Writes ~/starbug/unroll_sensitivity.json."""
import json
import os
import sys
from multiprocessing import Pool

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import vliw_model as vm
from sweep_isa import DSP, INT_DSP, ALU, paths

PICK = [vm.SB1["name"], "+ gshare predictor (as RV32GC)", "+ zero-overhead loops",
        "+ 1 LS + 1 load-only, conflict-free", "+ 2 LS, conflict-free"]
CF = [c for c in vm.CONFIGS if c["name"] in PICK]


def one(b):
    out = {}
    for v in ("scalar", "unroll"):
        r = vm.run_scheduled(*paths(b, v), cfgs=CF)
        base = r["configs"][vm.SB1["name"]]["cycles"]
        out[v] = {n: base / r["configs"][n]["cycles"] for n in PICK}
        out[v]["sb1_cycles"] = base
    return b, out


if __name__ == "__main__":
    benches = DSP + INT_DSP + ALU
    with Pool(16) as p:
        res = dict(p.map(one, benches))
    json.dump(res, open(os.path.join(os.path.expanduser(os.environ.get("STARBUG_HOME", "~/starbug")), "unroll_sensitivity.json"), "w"), indent=1)
    print(f"{'benchmark':34s} {'SB1 cyc -O3/unroll':>20s}" + "".join(f"{n[:18]:>21s}" for n in PICK[1:]))
    for b in benches:
        r = res[b]
        print(f"{b:34s} {r['scalar']['sb1_cycles']:>9d}/{r['unroll']['sb1_cycles']:<10d}" +
              "".join(f"{r['scalar'][n]:>10.2f}/{r['unroll'][n]:<10.2f}" for n in PICK[1:]))
