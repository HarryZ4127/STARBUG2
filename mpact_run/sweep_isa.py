#!/usr/bin/env python3
"""ISA what-if sweep: schedule every benchmark's unrolled code for each configuration in
vliw_model.CONFIGS, and time the plain -O3 scalar build on the RV32GC baseline as reference.
Writes ~/starbug/isa_sweep.json."""
import json
import os
import sys
from multiprocessing import Pool

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import vliw_model as vm

H = os.path.expanduser(os.environ.get("STARBUG_HOME", "~/starbug"))  # WSL/Linux work dir: traces, ELFs, JSON
DSP = ["fir_f32_taps256_n128", "fir_f32_taps256_n1", "lms_f32_taps256_n128", "lms_f32_taps256_n1",
       "biquad_cascade_df2T_f32_sos3_n128", "biquad_cascade_df2T_f32_sos3_n1", "dct4_2048_f32",
       "dct4_512_f32", "rfft2048_f32", "rfft512_f32"]
INT_DSP = ["matmult-int", "edn", "dwconv3x3_i8", "fir_q15"]
ALU = ["crc32", "nettle-sha256"]
GROUP = {**{b: "float DSP (team Embench suite)" for b in DSP},
         **{b: "integer DSP / ML" for b in INT_DSP}, **{b: "ALU / control" for b in ALU}}


def paths(b, v):
    if b in DSP:
        return f"{H}/ex/embench_starbug/{b}/{b}_{v}_wally.elf", f"{H}/traces/{b}_{v}_wally.trace"
    return f"{H}/ex/extra/{b}_{v}.elf", f"{H}/traces/{b}_{v}.trace"


def one(b):
    ref = vm.run_as_written(*paths(b, "scalar"), vm.RV32GC, honor_hints=False)
    sch = vm.run_scheduled(*paths(b, "unroll"))
    return b, dict(group=GROUP[b], scalar_rv32gc=ref, **sch)


if __name__ == "__main__":
    benches = DSP + INT_DSP + ALU
    with Pool(min(16, len(benches))) as p:
        res = dict(p.map(one, benches))
    json.dump(res, open(f"{H}/isa_sweep.json", "w"), indent=1)
    names = [c["name"] for c in vm.CONFIGS]
    print(f"{'benchmark':34s}" + "".join(f"{i:>7d}" for i in range(len(names))))
    for b in benches:
        r = res[b]
        sb1 = r["configs"][vm.SB1["name"]]["cycles"]
        print(f"{b:34s}" + "".join(f"{sb1 / r['configs'][n]['cycles']:7.2f}" for n in names))
    for i, n in enumerate(names):
        print(i, n)
