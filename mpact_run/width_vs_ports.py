#!/usr/bin/env python3
"""Separate issue width from memory ports: speedup over SB1 today for 4/8/16 lanes x 1/2/4 memory
ops per bundle (any lane, conflict-free, no predictor change). Writes ~/starbug/width_vs_ports.json."""
import json
import os
import sys
from multiprocessing import Pool

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import vliw_model as vm
from sweep_isa import DSP, INT_DSP, ALU, paths

GRID = [(w, m) for w in (4, 8, 16) for m in (1, 2, 4)]


def cfgs():
    out = [vm.SB1]
    for w, m in GRID:
        out.append(vm.make_cfg(f"w{w}_m{m}", width=w, mem_lanes=tuple(range(min(w, m))), max_mem=m,
                               max_stores=m, worker=vm.WORKER))
    return out


def one(b):
    r = vm.run_scheduled(*paths(b, "unroll"), cfgs=cfgs())
    base = r["configs"][vm.SB1["name"]]["cycles"]
    return b, {f"w{w}_m{m}": base / r["configs"][f"w{w}_m{m}"]["cycles"] for w, m in GRID}


if __name__ == "__main__":
    benches = DSP + INT_DSP + ALU
    with Pool(16) as p:
        res = dict(p.map(one, benches))
    json.dump(res, open(os.path.join(os.path.expanduser(os.environ.get("STARBUG_HOME", "~/starbug")), "width_vs_ports.json"), "w"), indent=1)
    print(f"{'benchmark':34s}" + "".join(f"{f'w{w}m{m}':>7s}" for w, m in GRID))
    for b in benches:
        print(f"{b:34s}" + "".join(f"{res[b][f'w{w}_m{m}']:7.2f}" for w, m in GRID))
