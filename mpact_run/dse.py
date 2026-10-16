#!/usr/bin/env python3
"""Design-space exploration for STARBUG 2: every combination of the main structural choices,
one-factor-at-a-time sensitivity around two anchor designs, and a compiler-strategy study.

  python3 dse.py factorial     width x memory paths x banks x branch handling x ZOL
                               on the unrolled and the plain -O3 code       -> $STARBUG_HOME/dse_factorial.json
  python3 dse.py sensitivity   one knob at a time around SB1 and the SB2 candidate
                                                                             -> $STARBUG_HOME/dse_sensitivity.json
  python3 dse.py compiler      scheduler x alias analysis x code variant x machine,
                               plus the hand-VLIW kernels as written       -> $STARBUG_HOME/dse_compiler.json
  python3 dse.py all

Then `python3 dse_summary.py` writes the tables in results/dse/. Uses the same ELFs and traces as
sweep_isa.py (see README); only python3 and riscv64-unknown-elf-objdump are needed.
"""
import itertools
import json
import os
import sys
from multiprocessing import Pool

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import vliw_model as vm
from sweep_isa import DSP, INT_DSP, ALU, GROUP, paths

H = os.path.expanduser(os.environ.get("STARBUG_HOME", "~/starbug"))
BENCHES = DSP + INT_DSP + ALU
mk = vm.make_cfg

# ---------------------------------------------------------------- factorial
WIDTHS = (2, 4, 6, 8)
MEMORY = {  # name: make_cfg kwargs (memory ops per bundle and which lanes may issue them)
    "1LS": dict(),
    "1LS+1LO": dict(load_lanes=(1,), max_mem=2),
    "2LS": dict(mem_lanes=(0, 1), max_mem=2, max_stores=2),
    "2LS+2LO": dict(mem_lanes=(0, 1), load_lanes=(2, 3), max_mem=4, max_stores=2),
}
BANKS = {"ideal": dict(banks=0), "2 banks": dict(banks=2), "4 banks": dict(banks=4)}
BRANCH = ("none", "btfn", "gshare")


def factorial_cfgs():
    out = [vm.RV32GC, vm.SB1]
    for w, (mn, mkw), (bn, bkw), br, zol in itertools.product(WIDTHS, MEMORY.items(), BANKS.items(),
                                                             BRANCH, (False, True)):
        if mkw.get("max_mem", 1) > w:
            continue
        if mn == "1LS" and bn != "ideal":
            continue  # one memory op per bundle can't conflict
        name = f"w{w} | {mn} | {bn} | {br} | {'ZOL' if zol else 'no ZOL'}"
        out.append(mk(name, width=w, branch=br, zol=zol, **mkw, **bkw))
    return out


# ---------------------------------------------------------------- sensitivity
SB2 = dict(load_lanes=(1,), max_mem=2, banks=2, branch="gshare", zol=True)  # "combined 4-wide"
ANCHORS = {"SB1": dict(), "SB2 candidate": SB2}
H32 = "32-bit header, 4 ops/header (P-bit chaining)"
H16 = "16-bit hint (SB1)"
# (factor, setting label, overrides). Each is applied on top of each anchor.
KNOBS = [
    ("issue width", "2", dict(width=2)), ("issue width", "3", dict(width=3)),
    ("issue width", "6", dict(width=6)), ("issue width", "8", dict(width=8)),
    ("load latency", "1", dict(lat={"load": 1})), ("load latency", "3", dict(lat={"load": 3})),
    ("load latency", "4", dict(lat={"load": 4})),
    ("mul latency", "1", dict(lat={"mul": 1})), ("mul latency", "3", dict(lat={"mul": 3})),
    ("FP latency", "1", dict(lat={"fp": 1})), ("FP latency", "3", dict(lat={"fp": 3})),
    ("FP latency", "4", dict(lat={"fp": 4})),
    ("branch penalty", "1", dict(penalty=1)), ("branch penalty", "3", dict(penalty=3)),
    ("branch penalty", "4", dict(penalty=4)),
    ("branch handling", "none", dict(branch="none")), ("branch handling", "btfn", dict(branch="btfn")),
    ("branch handling", "gshare", dict(branch="gshare")),
    ("zero-overhead loops", "on", dict(zol=True)), ("zero-overhead loops", "off", dict(zol=False)),
    ("I$ and D$ size", "4 KB", dict(icache_kb=4, dcache_kb=4)),
    ("I$ and D$ size", "8 KB", dict(icache_kb=8, dcache_kb=8)),
    ("I$ and D$ size", "32 KB", dict(icache_kb=32, dcache_kb=32)),
    ("miss penalty", "9", dict(miss=9, writeback=8)), ("miss penalty", "36", dict(miss=36, writeback=32)),
    ("caches", "perfect", dict(caches=False)),
    ("memory banks", "2, word", dict(banks=2, bank_hash="word")),
    ("memory banks", "2, XOR-hashed", dict(banks=2, bank_hash="xor")),
    ("memory banks", "4, word", dict(banks=4, bank_hash="word")),
    ("memory banks", "4, XOR-hashed", dict(banks=4, bank_hash="xor")),
    ("memory banks", "8, word", dict(banks=8)),
    ("memory banks", "conflict-free", dict(banks=0)),
    ("mul/div units per bundle", "1", dict(mul_units=1)), ("mul/div units per bundle", "2", dict(mul_units=2)),
    ("FP units per bundle", "1", dict(fp_units=1)), ("FP units per bundle", "2", dict(fp_units=2)),
    ("RF read ports per file", "4", dict(max_reads=4)), ("RF read ports per file", "6", dict(max_reads=6)),
    ("RF write ports per file", "2", dict(max_writes=2)),
    ("FP in worker lanes", "no (lane 0 only)", dict(worker=vm.WORKER - {"fp", "fcvti", "fmvx", "fdiv"})),
    ("mul/div in worker lanes", "no (lane 0 only)", dict(worker=vm.WORKER - {"mul", "div"})),
    ("fetch bandwidth", "8 B/cycle, 16-bit hint", dict(fetch_bytes=8, header=H16)),
    ("fetch bandwidth", "8 B/cycle, 32-bit header", dict(fetch_bytes=8, header=H32)),
    ("fetch bandwidth", "16 B/cycle, 16-bit hint", dict(fetch_bytes=16, header=H16)),
    ("fetch bandwidth", "16 B/cycle, 32-bit header", dict(fetch_bytes=16, header=H32)),
    ("fetch bandwidth", "32 B/cycle, 32-bit header", dict(fetch_bytes=32, header=H32)),
    ("line-straddling bundle", "+1 cycle, 16-bit hint", dict(cross="stall", header=H16)),
    ("line-straddling bundle", "+1 cycle, 32-bit header", dict(cross="stall", header=H32)),
    ("line-straddling bundle", "issues scalar, 16-bit hint", dict(cross="scalar", header=H16)),
    ("line-straddling bundle", "issues scalar, 32-bit header", dict(cross="scalar", header=H32)),
    ("predication (if-conversion)", "on", dict(predicate=True)),
    ("scheduler", "greedy packetizer", dict(sched="packetizer")),
    ("software pipelining", "on", dict(swp=True)),
]


def sensitivity_cfgs():
    out = [vm.RV32GC]
    for an, akw in ANCHORS.items():
        out.append(mk(f"{an} | anchor | -", **akw))
        for fac, lab, kw in KNOBS:
            k = dict(akw)
            k.update(kw)
            if k.get("max_mem", 1) > k.get("width", 4):
                k["load_lanes"] = ()
                k["max_mem"] = 1
            out.append(mk(f"{an} | {fac} | {lab}", **k))
    return out


# ---------------------------------------------------------------- compiler strategies
MACHINES = {
    "SB1": dict(),
    "SB2 candidate": SB2,
    "8-wide 2LS 2 banks gshare ZOL": dict(width=8, mem_lanes=(0, 1), max_mem=2, max_stores=2, banks=2,
                                         branch="gshare", zol=True),
}
SCHEDS = {"greedy packetizer": dict(sched="packetizer"), "list scheduler": dict(),
          "list + modulo scheduling": dict(swp=True)}


def compiler_cfgs():
    return [vm.SB1] + [mk(f"{m} | {s}", **mkw, **skw) for (m, mkw), (s, skw)
                       in itertools.product(MACHINES.items(), SCHEDS.items())]


# ---------------------------------------------------------------- runners
def _cycles(res):
    return {n: {k: v for k, v in r.items() if k in ("cycles", "bundles", "bank_conflicts", "branch_penalties",
                                                    "icache_misses", "dcache_misses", "line_crossing_bundles",
                                                    "fetch_stall_cycles", "ops_per_bundle")}
            for n, r in res["configs"].items()}


def job_factorial(args):
    b, variant = args
    return b, variant, _cycles(vm.run_scheduled(*paths(b, variant), cfgs=factorial_cfgs()))


def job_sensitivity(args):
    b, variant = args
    return b, variant, _cycles(vm.run_scheduled(*paths(b, variant), cfgs=sensitivity_cfgs()))


def job_compiler(args):
    b, variant, alias = args
    return b, variant, alias, _cycles(vm.run_scheduled(*paths(b, variant), cfgs=compiler_cfgs(), alias=alias))


def job_ref(b):
    """Reference points: -O3 scalar on RV32GC, and (DSP suite) the hand-VLIW kernels as written."""
    out = {"scalar_rv32gc": vm.run_as_written(*paths(b, "scalar"), vm.RV32GC, honor_hints=False)["cycles"]}
    if b in DSP:
        out["hand_vliw_sb1"] = vm.run_as_written(*paths(b, "vliw"), vm.SB1)["cycles"]
    return b, out


def _pool(fn, jobs):
    # biggest kernels first so the pool stays busy
    with Pool(os.cpu_count()) as p:
        return p.map(fn, jobs, chunksize=1)


def run(which):
    refs = dict(_pool(job_ref, BENCHES))
    meta = dict(group=GROUP, refs=refs)
    if which in ("factorial", "all"):
        rows = _pool(job_factorial, [(b, v) for v in ("unroll", "scalar") for b in BENCHES])
        out = dict(meta, configs=[c["name"] for c in factorial_cfgs()], results={})
        for b, v, r in rows:
            out["results"].setdefault(v, {})[b] = r
        json.dump(out, open(f"{H}/dse_factorial.json", "w"))
        print("factorial:", len(out["configs"]), "configs x", len(BENCHES), "kernels x 2 code variants")
    if which in ("sensitivity", "all"):
        rows = _pool(job_sensitivity, [(b, v) for v in ("unroll", "scalar") for b in BENCHES])
        out = dict(meta, configs=[c["name"] for c in sensitivity_cfgs()], results={})
        for b, v, r in rows:
            out["results"].setdefault(v, {})[b] = r
        json.dump(out, open(f"{H}/dse_sensitivity.json", "w"))
        print("sensitivity:", len(out["configs"]), "configs")
    if which in ("compiler", "all"):
        rows = _pool(job_compiler, [(b, v, a) for v in ("unroll", "scalar") for a in ("oracle", "none")
                                    for b in BENCHES])
        out = dict(meta, configs=[c["name"] for c in compiler_cfgs()], results={})
        for b, v, a, r in rows:
            out["results"].setdefault(f"{v} | alias {a}", {})[b] = r
        json.dump(out, open(f"{H}/dse_compiler.json", "w"))
        print("compiler:", len(out["configs"]), "configs")


if __name__ == "__main__":
    run(sys.argv[1] if len(sys.argv) > 1 else "all")
