# Starbug ISA trade study (2026-10-13)

How much each proposed STARBUG 2 change buys, measured on the same code with a timing model checked
against the RTL. The reasoning behind the method and the conclusions is in
[`01-design-rationale.md` §9–§12](01-design-rationale.md#9-why-build-a-trace-driven-model-instead-of-sweeping-rtl-configs).

- **Generated report:** [`mpact_run/results/starbug_isa_study.html`](../mpact_run/results/starbug_isa_study.html)
  (open it in a browser).
- **Data:**
  - [`isa_speedup_vs_sb1.csv`](../mpact_run/results/isa_speedup_vs_sb1.csv)
  - [`validation.csv`](../mpact_run/results/validation.csv)
  - [`digest.json`](../mpact_run/results/digest.json)
  - raw model output in [`results/raw/`](../mpact_run/results/raw/)

## Method

### Pipeline
```
cvw lm/fp examples/C/embench_starbug  ─┐   build_embench_dsp.sh (GCC 10.2 -O3, rv32imafc;
Embench-IoT crc32/sha256/matmult/edn  ─┼─► build_extra.sh         scalar, unrolled, hand-VLIW)
kernels/dsp_int.c (int8 dwconv, Q15 FIR)┘        │  42 ELFs
                                                 ▼
          MPACT rv32g_sim + sim/rvr-starbug-trace.patch  (run_traces.sh)  ─►  PC + data-address traces
                                                 │
                                                 ▼
  vliw_model.py: basic blocks from objdump + trace ─► list-schedule each block per machine config
                 ─► caches, latencies, branch prediction, lane rules, bank conflicts ─► cycles
                                                 │
     validate.py (vs RTL) · sweep_isa.py · width_vs_ports.py · unroll_sensitivity.py
                                                 ▼
                         summarize.py ─► CSV / digest.json ─► gen_report.py ─► HTML
```

### Model assumptions (`vliw_model.py`)
- **Issue:** in-order, lock-step.
- **Latency:** load, mul and FP = 2; ALU = 1; taken branch or mispredict = 2.
- **Caches:** I$ and D$ are 16 KB, 4-way, 64 B lines, LRU, write-back/write-allocate, 18-cycle fill.
  This matches Wally.
- **Baseline front end:** `RV32GC` has gshare (2¹⁰ two-bit counters, 10-bit history, 1 K BTB). `SB1`
  has no predictor, matching `config/starbug`.
- **Lane rules:** memory ops, branches and CSRs only in lane 0, unless the config adds memory lanes.
- **Scheduling:** a list scheduler re-packs each basic block; oracle memory disambiguation comes from
  the trace.
- **Hand-VLIW kernels:** `run_as_written` runs the actual bundles as laid out in the binary, using
  Starbug 1 hint semantics.
- **Banks:** two-bank configs replay the traced addresses with word interleaving and charge a stall on
  each conflict.

### Kernels (16)
- **The team's Embench DSP f32 suite:** FIR, LMS, biquad (each at n=1 and n=128), dct4 512/2048,
  rfft 512/2048.
- **Embench-IoT:** crc32, nettle-sha256, matmult-int, edn.
- **New, written for this study:** int8 depthwise 3×3 conv and Q15 FIR (`mpact_run/kernels/dsp_int.c`).

The unrolled build adds Leo's VLIW-seed flags plus `-fvariable-expansion-in-unroller`, which gives
several accumulators per reduction. That build is the model's stand-in for a VLIW compiler's input.

## Validation against the RTL
Reference: Leo's Questa cycle counts on cvw `lm/fp`
(`examples/C/embench_starbug/presentation_assets/benchmark_summary.csv`). There are 10 DSP kernels × 3
runs. Per-run numbers are in [`validation.csv`](../mpact_run/results/validation.csv).

| Run | Median abs error | within 5% | within 10% |
|---|---|---|---|
| scalar on RV32GC | 4.0% | 6/10 | 9/10 |
| hand-VLIW on RV32GC (hints are nops) | 3.9% | 7/10 | 10/10 |
| hand-VLIW on Starbug | 4.4% | 6/10 | 8/10 |

- Geomean speedup of hand-VLIW Starbug over scalar RV32GC: **model 1.49×, RTL 1.40×**.
- Single-sample kernels come out 13–15% low.
- Caveat: our ELFs use GCC 10.2, while the RTL runs used Leo's newer GCC. The C glue differs slightly;
  the hand-written VLIW kernels are identical.

## Results: speedup over SB1 today (4-wide, 1 memory op in lane 0, no predictor)
These use the unrolled code. The `SB1/RV32GC` column is SB1 over scalar RV32GC; every other column is
over SB1.

| Kernel | group | SB1/RV32GC | +gshare | +ZOL | LS+LO 2 banks | LS+LO no conflict | 2LS no conflict | 8-wide 1 mem | combined 4-wide | ceiling |
|---|---|---|---|---|---|---|---|---|---|---|
| fir_f32_taps256_n128 | float DSP | 2.73 | 1.11 | 1.11 | 1.24 | 1.63 | 1.64 | 1.00 | 1.42 | 3.15 |
| fir_f32_taps256_n1 | float DSP | 2.60 | 1.06 | 1.13 | 1.36 | 1.41 | 1.64 | 1.00 | 1.63 | 3.09 |
| lms_f32_taps256_n128 | float DSP | 2.53 | 1.09 | 1.12 | 1.30 | 1.50 | 1.72 | 1.00 | 1.51 | 3.25 |
| lms_f32_taps256_n1 | float DSP | 2.52 | 1.06 | 1.13 | 1.39 | 1.42 | 1.70 | 1.00 | 1.65 | 3.19 |
| biquad_cascade_df2T_f32_sos3_n128 | float DSP | 1.45 | 1.04 | 1.05 | 1.00 | 1.00 | 1.00 | 1.00 | 1.06 | 1.06 |
| biquad_cascade_df2T_f32_sos3_n1 | float DSP | 1.04 | 1.03 | 1.00 | 1.11 | 1.12 | 1.15 | 1.00 | 1.15 | 1.27 |
| dct4_2048_f32 | float DSP | 1.87 | 1.04 | 1.07 | 1.08 | 1.16 | 1.27 | 1.01 | 1.16 | 1.51 |
| dct4_512_f32 | float DSP | 2.04 | 1.05 | 1.09 | 1.10 | 1.19 | 1.35 | 1.01 | 1.21 | 1.72 |
| rfft2048_f32 | float DSP | 1.95 | 1.04 | 1.07 | 1.07 | 1.16 | 1.24 | 1.02 | 1.15 | 1.46 |
| rfft512_f32 | float DSP | 1.96 | 1.04 | 1.07 | 1.05 | 1.14 | 1.25 | 1.02 | 1.14 | 1.50 |
| matmult-int | int DSP/ML | 1.82 | 1.04 | 1.06 | 1.24 | 1.63 | 1.68 | 1.01 | 1.33 | 1.96 |
| edn | int DSP/ML | 2.65 | 1.05 | 1.07 | 1.15 | 1.41 | 1.47 | 1.00 | 1.24 | 2.09 |
| dwconv3x3_i8 | int DSP/ML | 1.83 | 1.06 | 1.10 | 1.03 | 1.40 | 1.47 | 1.00 | 1.15 | 1.71 |
| fir_q15 | int DSP/ML | 2.60 | 1.10 | 1.09 | 1.18 | 1.44 | 1.44 | 1.00 | 1.33 | 2.26 |
| crc32 | ALU/control | 1.07 | 1.23 | 1.01 | 1.00 | 1.00 | 1.05 | 1.00 | 1.23 | 1.30 |
| nettle-sha256 | ALU/control | 1.80 | 1.01 | 1.00 | 1.10 | 1.22 | 1.24 | 1.00 | 1.10 | 1.29 |
| **geomean** | | **1.95** | **1.07** | **1.07** | **1.14** | **1.29** | **1.37** | **1.00** | **1.27** | **1.86** |

Abbreviations:
- **LS+LO** = one load/store path plus one load-only path.
- **2LS** = two full load/store paths.
- **ZOL** = zero-overhead loops.
- **combined 4-wide** = LS+LO + 2 banks + gshare + ZOL.
- **ceiling** = 16-wide, 4 memory ops in any lane, conflict-free, gshare, ZOL.

The CSV has the remaining configs: static BTFN (1.05×), 2LS with 2 banks (1.21×), and 8-wide with
LS+LO or 2LS.

### Issue width vs memory ports
Geomean, conflict-free, unrolled ([`raw/width_vs_ports.json`](../mpact_run/results/raw/width_vs_ports.json)):

| lanes \ mem ops/bundle | 1 | 2 | 4 |
|---|---|---|---|
| 4 | 1.00× | 1.38× | 1.54× |
| 8 | 1.00× | 1.39× | 1.62× |
| 16 | 1.01× | 1.39× | 1.62× |

### Compiler sensitivity
From [`raw/unroll_sensitivity.json`](../mpact_run/results/raw/unroll_sensitivity.json):
- **ZOL speedup, plain `-O3` vs unrolled:**
  - FIR f32 1.50× vs 1.11×
  - LMS 1.50× vs 1.12×
  - matmult 1.34× vs 1.06×
  - crc32 1.09× vs 1.01×
- **Unrolling itself** (with multiple accumulators) is worth 1.7–2.6× on FIR, LMS, edn, matmul and
  Q15 FIR.

### Header encoding (SB1 schedule)
- **Static code size:** the 16-bit hint gives 38,758 B. The 32-bit header gives 44,122 B (+14%), with
  either P-bit chaining or a second word above 4 ops. 8-wide bundles average under 4 ops, so the two
  encodings give the same size.
- **Line crossing:** about 83 per 1000 bundles with the hint and about 93 per 1000 with the 32-bit
  header straddle a 64 B line. Today's fetch can't issue those as bundles.
- **Cycles:** the header costs none.

### Predication
- Short forward hammocks that creg/z could if-convert appear only in the Q15 FIR, at 2.9% of its SB1
  cycles. GCC already removes the rest.
- Saving: about 0 on today's code.

## What it overturned
- **The team's 10-08/09 estimates.** They were 2.7–3.1× for a second load path (matmul / dwconv) and up
  to 4× for low-overhead branches (SHA-256 / CRC-32). The model gives 1.4–1.7× and about 1.0×. The
  estimates used different, non-unrolled baselines. Use these numbers instead, and don't mix the two
  sets.
- **The RTL headline 1.40× (Starbug over scalar)** ran the baseline on `config/rv32gc`, which has
  gshare, and Starbug on `config/starbug`, which has `BPRED_SUPPORTED = 0`.

## Answers to the team's open questions (10-09 agenda)
| Question | Answer |
|---|---|
| Load-only vs full second path? | The load-only path keeps 76% of the full-LS gain (1.29× vs 1.37×), without dual-store hazards |
| Is the marginal ≥ 1.2× target met? | Yes with bank-aware placement (1.29×); no with naive interleaving (1.14×) |
| 8-wide header: P-bit chaining vs second word? | Same code size; choose on decode simplicity |
| Order of work? | Second memory path before 8-wide. 8-wide alone adds about 1% |

## Limits
- Cycle counts only: no area, power or clock frequency. A second port or 8 lanes may cost frequency
  against the 50 MHz target.
- The list scheduler stands in for a compiler. It does no software pipelining or modulo scheduling, so
  real compiled code could do better or worse.
- Bank conflicts use the linker's actual placement. A per-kernel tuned layout lands between the
  "2 banks" and "no conflicts" columns.
- The ELFs use GCC 10.2 from Ubuntu 22.04, not the team's toolchain.

## Reproduce
See the [README](../README.md#re-running-the-trade-study-linux-no-rebuild-needed) to re-run from the
packed ELFs and traces. See [`mpact_run/README.md`](../mpact_run/README.md) to rebuild from source. To
add a machine configuration, add an entry to `CONFIGS` in `vliw_model.py`, then re-run `sweep_isa.py`,
`summarize.py` and `gen_report.py`.
