#!/usr/bin/env python3
"""Summarise validate.json, isa_sweep.json and width_vs_ports.json into CSV tables and a JSON digest
under mpact_run/results/ (read by the report)."""
import csv
import json
import math
import os

H = os.path.expanduser(os.environ.get("STARBUG_HOME", "~/starbug"))  # WSL/Linux work dir: traces, ELFs, JSON
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results")
os.makedirs(OUT, exist_ok=True)

val = json.load(open(f"{H}/validate.json"))
sw = json.load(open(f"{H}/isa_sweep.json"))
wp = json.load(open(f"{H}/width_vs_ports.json"))
SB1 = "SB1 today: 4-wide, 1 mem op (lane 0), no predictor"
RV = "RV32GC baseline: 1-wide, gshare"


def gmean(xs):
    xs = list(xs)
    return math.exp(sum(math.log(x) for x in xs) / len(xs))


digest = {}
# 1. validation
rows = []
for v in val:
    for col, mkey, rkey in (("scalar on RV32GC", "scalar", "scalar_cycles"),
                            ("hand-VLIW on RV32GC", "vliw_rv32gc", "vliw_cycles_rv32gc"),
                            ("hand-VLIW on Starbug", "starbug", "starbug_cycles")):
        m, r = v[mkey]["cycles"], v["rtl"][rkey]
        rows.append(dict(benchmark=v["benchmark"], run=col, model=m, rtl=r, error=round(m / r - 1, 4)))
with open(f"{OUT}/validation.csv", "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=list(rows[0]))
    w.writeheader()
    w.writerows(rows)
digest["validation"] = rows
for col in ("scalar on RV32GC", "hand-VLIW on RV32GC", "hand-VLIW on Starbug"):
    errs = [abs(r["error"]) for r in rows if r["run"] == col]
    digest.setdefault("validation_summary", {})[col] = dict(
        median_abs_error=sorted(errs)[len(errs) // 2], within_5pct=sum(e <= 0.05 for e in errs),
        within_10pct=sum(e <= 0.10 for e in errs), n=len(errs))
# model speedup of hand-VLIW Starbug over scalar RV32GC vs RTL's
digest["starbug_vs_scalar_geomean"] = dict(
    model=gmean(v["scalar"]["cycles"] / v["starbug"]["cycles"] for v in val),
    rtl=gmean(v["rtl"]["scalar_cycles"] / v["rtl"]["starbug_cycles"] for v in val))

# 2. ISA sweep: speedup over SB1 today, per benchmark and geomean per group
cfg_names = list(next(iter(sw.values()))["configs"])
groups = {}
for b, r in sw.items():
    groups.setdefault(r["group"], []).append(b)
table = []
for b, r in sw.items():
    sb1 = r["configs"][SB1]["cycles"]
    row = dict(benchmark=b, group=r["group"], sb1_cycles=sb1,
               sb1_over_rv32gc_scalar=round(r["scalar_rv32gc"]["cycles"] / sb1, 3))
    for n in cfg_names:
        row[n] = round(sb1 / r["configs"][n]["cycles"], 3)
    table.append(row)
with open(f"{OUT}/isa_speedup_vs_sb1.csv", "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=list(table[0]))
    w.writeheader()
    w.writerows(table)
digest["configs"] = cfg_names
digest["per_benchmark"] = table
digest["group_geomean"] = {g: {n: gmean(row[n] for row in table if row["group"] == g) for n in cfg_names}
                           for g in groups}
digest["group_geomean"]["all"] = {n: gmean(row[n] for row in table) for n in cfg_names}
digest["sb1_over_rv32gc_geomean"] = {g: gmean(row["sb1_over_rv32gc_scalar"] for row in table if row["group"] == g)
                                     for g in groups}
digest["sb1_over_rv32gc_geomean"]["all"] = gmean(row["sb1_over_rv32gc_scalar"] for row in table)
# over the RV32GC baseline (the customer-facing number) for selected configs
digest["over_rv32gc_geomean"] = {
    n: {g: gmean(sw[b]["scalar_rv32gc"]["cycles"] / sw[b]["configs"][n]["cycles"] for b in bs)
        for g, bs in list(groups.items()) + [("all", list(sw))]} for n in cfg_names}
# stall anatomy for SB1 today
digest["sb1_anatomy"] = {b: {k: r["configs"][SB1][k] for k in ("cycles", "bundles", "branch_penalties",
                                                                 "icache_misses", "dcache_misses")}
                         for b, r in sw.items()}
digest["bank_conflicts"] = {b: {n: r["configs"][n]["bank_conflicts"] for n in cfg_names if r["configs"][n]["bank_conflicts"]}
                            for b, r in sw.items()}

# 3. width vs ports
digest["width_vs_ports"] = {k: gmean(v[k] for v in wp.values()) for k in next(iter(wp.values()))}
digest["width_vs_ports_by_group"] = {g: {k: gmean(wp[b][k] for b in bs) for k in next(iter(wp.values()))}
                                     for g, bs in groups.items()}

# 4. header encodings: code size of the scheduled kernels, and line-straddling bundles
hdr = {}
for n in (SB1, "8-wide, 2 LS, 2 banks"):
    tot = {}
    cross = {}
    bundles = sum(sw[b]["configs"][n]["bundles"] for b in sw)
    for b, r in sw.items():
        for h, v in r["configs"][n]["code_bytes"].items():
            tot[h] = tot.get(h, 0) + v
        for h, v in r["configs"][n]["line_crossings"].items():
            cross[h] = cross.get(h, 0) + v
    hdr[n] = dict(code_bytes=tot, crossing_per_1000_bundles={h: 1000 * c / bundles for h, c in cross.items()},
                  crossing_cycle_share={h: c / sum(sw[b]["configs"][n]["cycles"] for b in sw) for h, c in cross.items()})
digest["headers"] = hdr

# 5. predication opportunity: short forward hammocks; on SB1 today each taken one costs 2 cycles
pred = {}
for b, r in sw.items():
    p = r["predication"]
    sb1 = r["configs"][SB1]["cycles"]
    pred[b] = dict(p, penalty_cycles_sb1=2 * p["dynamic_taken"], share_of_sb1_cycles=2 * p["dynamic_taken"] / sb1)
digest["predication"] = pred

json.dump(digest, open(f"{OUT}/digest.json", "w"), indent=1)
print(json.dumps({k: digest[k] for k in ("validation_summary", "starbug_vs_scalar_geomean", "sb1_over_rv32gc_geomean",
                                        "width_vs_ports", "headers")}, indent=1))
print(json.dumps(digest["group_geomean"], indent=1))
print(json.dumps({b: (v["dynamic"], v["dynamic_taken"], round(v["share_of_sb1_cycles"], 4)) for b, v in pred.items()}))
