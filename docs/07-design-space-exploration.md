# STARBUG 2 design-space exploration (2026-10-16)

The 10-13 trade study changed one thing at a time on top of SB1. This study tests **every
combination** of the main structural choices, then varies every other microarchitectural parameter
one at a time around two anchors, then asks how much of the result depends on the compiler.
- Compiler side: [`08-compiler.md`](08-compiler.md).
- All tables: [`mpact_run/results/dse/SUMMARY.md`](../mpact_run/results/dse/SUMMARY.md).
- CSVs: [`mpact_run/results/dse/`](../mpact_run/results/dse/).
- Raw output: `mpact_run/results/raw/dse_*.json`.

## What was run

Same 16 kernels, traces and timing model as the trade study ([`05-isa-trade-study.md`](05-isa-trade-study.md)).
The model was extended (`mpact_run/vliw_model.py`) so each structural choice is a `make_cfg()` knob.
With the new knobs at their defaults it reproduces the 10-13 results and the RTL validation byte
for byte. That check is a unit test (`mpact_run/tests/`, 31 tests).

| Sweep | What varies | Configs |
|---|---|---|
| Full factorial | issue width {2, 4, 6, 8} × memory paths {1 LS, 1 LS + 1 load-only, 2 LS, 2 LS + 2 load-only} × banks {conflict-free, 4, 2 word-interleaved} × branch handling {none, static BTFN, gshare} × zero-overhead loops {off, on} | 222 + SB1 + RV32GC, each on unrolled and on plain `-O3` code |
| Sensitivity | 53 one-knob changes, each applied to **SB1** and to the **SB2 candidate** (4-wide, 1 LS + 1 load-only, 2 banks, gshare, ZOL) | 106 + anchors |
| Compiler | greedy packetizer / latency-aware list scheduler / + modulo scheduling × alias information on/off × `-O3` / unrolled code × 3 machines | 36 |

The one-knob changes cover:
- issue width;
- load, mul and FP latency;
- branch penalty and branch handling;
- I$/D$ size, miss penalty, perfect caches;
- 2/4/8 banks, word-interleaved or XOR-hashed (the scrambled mapping Rice proposed);
- mul/div units and FP units per bundle;
- register-file read and write ports;
- FP or mul/div restricted to lane 0;
- fetch bandwidth (8/16/32 B/cycle) under the 16-bit hint and the 32-bit header;
- what a line-straddling bundle costs (+1 cycle, or issued as scalar ops);
- creg/z if-conversion;
- the scheduler; software pipelining.

Unless stated otherwise:
- speedups are **geomeans over the 16 kernels** relative to **SB1 running the same code**;
- the code is the **unrolled** build, the stand-in for a VLIW compiler's input.

## Findings

### 1. SB1's lanes are mostly idle: width is not what limits it
- On SB1, scheduled bundles average **1.5–2.3 ops out of 4** (`ops_per_bundle` in the raw JSON).
- Cutting SB1 to **2 lanes costs only 6%**; 3 lanes cost 1%; 6 or 8 lanes gain under 1%.
- With a second memory path, 2 lanes cost 18%. Four lanes are needed once there are two memory
  ops per bundle, but going wider still buys ≤ 1%.

| lanes (gshare + ZOL) | 1 LS | 1 LS + 1 LO, 2 banks | 2 LS, 2 banks | 2 LS + 2 LO, conflict-free |
|---|---|---|---|---|
| 2 | 1.03 | 1.04 | 1.06 | — |
| 4 | 1.09 | 1.27 | 1.33 | 1.68 |
| 8 | 1.10 | 1.28 | 1.34 | 1.81 |

**8-wide is only worth building if it comes with four conflict-free memory ops per bundle**
(1.68 → 1.81). Every other 8-wide configuration is within 1–2% of its 4-wide twin.

### 2. The memory system is the lever, and banks matter as much as ports
Main effect of each factor, as a geomean over all settings of the others:

| factor | levels → speedup |
|---|---|
| memory paths | 1 LS 1.04 · 1 LS+1 LO 1.24 · 2 LS 1.30 · 2 LS+2 LO 1.44 |
| banks (≥ 2 paths) | 2 banks 1.21 · 4 banks 1.30 · conflict-free 1.44 |
| width | 2: 1.06 · 4: 1.32 · 6: 1.35 · 8: 1.35 |
| branch | none 1.25 · BTFN 1.29 · gshare 1.31 |
| ZOL | off 1.25 · on 1.31 |

The second path's value depends on how many banks serve it (4-wide, gshare, ZOL):

| | 2 banks | 4 banks | conflict-free |
|---|---|---|---|
| 1 LS + 1 load-only | 1.27 | **1.33** | 1.44 |
| 2 LS | 1.33 | **1.40** | 1.53 |
| 2 LS + 2 load-only | 1.27 | 1.43 | 1.68 |

- **More ports without more banks makes things worse.** Four memory ops over 2 banks (1.27) is
  slower than two over 2 banks (1.33), because conflicts grow with the number of accesses per bundle.
- **Four word-interleaved banks recover about a third** of the gap between 2 banks and
  conflict-free; 8 banks recover about two thirds (SB2 anchor: 2 banks 1.00, 4 banks 1.05,
  8 banks 1.09, conflict-free 1.14).
- **XOR-hashing the bank index does not help with 2 banks** (0.99×). With 4 banks it adds 2%
  (1.05 → 1.07), mostly on integer kernels. Conflicts come from where two arrays sit relative to
  each other, which no fixed hash fixes. Bank count and **data placement** are what matter.

### 3. Branch hardware matters mainly when the compiler doesn't unroll or pipeline
| on SB1 | unrolled code | `-O3` code |
|---|---|---|
| static BTFN | 1.05 | 1.20 |
| gshare | 1.07 | 1.22 |
| zero-overhead loops | 1.07 | 1.26 |
| branch penalty 1 instead of 2 | 1.04 | 1.10 |

- On the SB2 candidate (already gshare + ZOL), removing ZOL costs 3%, and the branch penalty
  barely matters (±1%).
- gshare is a config flag in Wally (`BPRED_SUPPORTED`). It is the cheapest 7% available and also
  makes RTL comparisons fair.

### 4. Front end: 16 B/cycle of fetch, and handle line-straddling packets in hardware
| | SB1 | SB2 candidate |
|---|---|---|
| fetch 8 B/cycle, 16-bit hint | 0.93 | 0.84 |
| fetch 8 B/cycle, 32-bit header | 0.88 | 0.77 |
| fetch 16 B/cycle, 32-bit header | 1.00 | 1.00 |
| straddling bundle +1 cycle, 32-bit header | 0.94 | 0.91 |
| straddling bundle issued as scalar ops, 16-bit hint (**SB1 today**) | 0.91 | 0.83 |
| straddling bundle issued as scalar ops, 32-bit header | 0.90 | 0.81 |

- **A 16 B/cycle fetch with a small queue is enough** for 4-wide, with either header. 8 B/cycle
  costs 12–23% once the header is 32 bits.
- **Line straddles are the front end's real cost.** Falling back to scalar costs 9–10% on SB1 and
  17–19% on the SB2 candidate, since the bundles there are fuller.
  - The model's layout is idealised: blocks laid end to end with no alignment. Treat these as
    "if nothing is done" numbers.
  - Leo's layout pass recovers part of the cost in software (+2–3% on RTL).
  - STARBUG 2 has no scalar fallback at all (header doc), so the fetch unit must either assemble
    packets across a line (a fetch queue that spans lines, as the Oct-8 meeting suggested: cost ≈ 0)
    or pay a cycle (−6 to −9%).
  - Recommend the queue.
- The 32-bit header itself still costs **0 cycles** when fetch is ≥ 16 B/cycle and straddles are
  handled.

### 5. Microarchitecture parameters
On the SB2 candidate (SB1 in brackets where different):

| parameter | effect |
|---|---|
| load latency 1 / 3 / 4 (now 2) | +5% / −7% / −13% (ALU/control kernels −15% / −26%) |
| FP latency 1 / 3 / 4 (now 2) | +5% / −4% / −9% (float DSP −14% at 4) |
| mul latency 1 / 3 | ±1% (integer DSP ±4%) |
| I$ + D$ 4 KB / 8 KB / 32 KB (now 16 KB) | −15% / −4% / +2% |
| **perfect caches** | **+12% (+10%)**; float DSP +18% |
| miss penalty 9 / 36 (now 18) | +5% / −8% |
| one FP unit per bundle / two | −6% / −1% |
| one mul/div unit per bundle | −1% (0%) |
| RF: 4 read ports per file / 2 write ports per file | −5% / **−12%** (−1% / −3%) |
| **FP restricted to lane 0** | **−15%**, float DSP −23% (confirms Leo's compiler fix) |
| mul/div restricted to lane 0 | −3%, integer DSP −11% (SB1: −19%) |
| predication (if-convert hammocks ≤ 8 ops) | **0%** |

Takeaways:
- **Keep FP and mul in all lanes**, but **two FP units are enough** (−1%).
- Don't skimp on register-file **write** ports: one per lane.
- **D$ misses are worth as much as a branch predictor** (perfect caches +10–12%). A banked,
  tightly coupled data memory (scratchpad/TCM) for DSP buffers would give the dual-bank port and
  remove the misses in one structure. That is the C6x L1D / X-Y memory approach, worth considering
  instead of banking the D$.
- **Predication buys nothing** on these kernels: GCC already converts the hammocks to branch-free
  code, and only `fir_q15` has any. Keep the creg/z encoding space, but implement it last.

### 6. The compiler matters more than any single hardware change
On the same traces (details in [`08-compiler.md`](08-compiler.md) §4; speedup over `-O3` scalar on
RV32GC):

| | SB1 | SB2 candidate |
|---|---|---|
| greedy packetizer (the current LLVM pass's policy) | 1.53 | 1.96 |
| latency-aware list scheduler | 1.95 | 2.47 |
| + modulo scheduling (bound) | 2.09 | 2.88 |
| list scheduler, no alias information | 1.74 | 2.05 |
| `-O3` code (not unrolled) + modulo scheduling | 1.54 | 2.47 |

- Today's hardware with a latency-aware, software-pipelining compiler (2.09) outruns the SB2
  hardware with greedy packing (1.96).
- The SB2 hardware gain over SB1 *grows* with compiler quality: 1.28× with greedy packing,
  1.27× list, 1.38× with modulo scheduling.
- Modulo scheduling on plain `-O3` code reaches what unrolling + list scheduling reaches (2.47 on
  SB2), so the compiler doesn't need aggressive unrolling once it pipelines.

## Recommended STARBUG 2 configuration

This updates [rationale §12](01-design-rationale.md#12-recommended-starbug-2-direction-from-the-evidence-above).
Cycle counts only: area, power and the 50 MHz timing target are not modelled.

1. **Second memory path:** 1 LS + 1 load-only, with **4 banks** (1.33× over SB1) or 2 banks plus
   compiler/linker placement (1.27 → up to 1.44). A full second LS path adds +5% (1.40× at 4
   banks), at the cost of dual-store hazards.
   - Better still, a **banked data TCM** for DSP buffers: it gives the second port and removes D$
     misses (+10–12%).
2. **Stay 4-wide.** Two lanes lose 18% once there are two memory ops. 8 lanes add ≤ 1% unless
   paired with four conflict-free memory ops.
3. **gshare on** (a config flag; +7%). Add **zero-overhead loops** if the compiler won't
   software-pipeline (+7% unrolled, +26% at `-O3`; much less once SWP exists).
4. **32-bit header with a fetch queue** that delivers ≥ 16 B/cycle and **assembles packets across
   I$ lines**. That removes a 9–19% straddle cost and makes the header free.
5. **Keep FP and mul/div in every lane, at least 2 FP units, and one RF write port per lane.**
   Keep load and FP latency at 2: each extra cycle costs 4–7%.
6. **Defer predication**: reserve the encoding, implement after everything above.
7. **Fund the compiler** (doc 08): a scheduling model, latency-aware packing and modulo
   scheduling are worth +36–47% on the same hardware. That is more than any hardware item above.

Expected result with items 1–5 and list-scheduled unrolled code: about **1.33×** over SB1 (2.59×
over RV32GC `-O3` scalar). For scale, modulo scheduling takes the 2-bank SB2 candidate from 2.47×
to 2.88× over RV32GC; expect similar or more on the 4-bank version.

## Further improvements worth studying
These were outside what the model can answer today:
- **Area/power/frequency.** Each option needs a synthesis estimate: second port, banks, RF ports,
  fetch queue. Use the FPGA flow Leo already has (`presentation_assets` utilisation data).
- **Bank-aware placement in the model.** Model a per-kernel tuned layout (e.g. offset the second
  stream by one word) to bound what a linker-side fix recovers between "2 banks" and "conflict-free".
- **TCM vs D$.** Add a scratchpad option to `make_cfg()` (no misses in a TCM address range, banked).
- **Post-increment addressing.** Folds pointer bumps. Free on SB1 (idle lanes); may matter on the
  memory-rich configs where bundles fill up.
- **Clustered register files** for 8-wide. Only relevant if 8-wide with four memory ops is pursued.
- **Validate the new knobs on RTL.** Only the SB1 baseline is validated (~4% median error).
  Re-measure at least one second-path config on RTL once it exists.

## Caveats
- **Software pipelining is a bound.** Steady-state iterations cost MII = max(ResMII, RecMII). Real
  modulo schedules can be 1 cycle worse per loop, and register pressure is ignored. The
  `-O3 + SWP` numbers are therefore optimistic.
- **The greedy-packetizer stand-in is cruder than Leo's pass.** His scoring, the 64-op look-ahead
  and the pre-RA scheduler's default load latency recover part of the gap. Treat 1.53 vs 1.95 as
  an upper bound on what latency awareness is worth. Leo's measured compiler is at 0.956× of
  hand-written code on RTL.
- **Memory disambiguation is an oracle from the trace** unless stated (`alias none` rows).
- **Line straddle numbers assume an unaligned end-to-end layout.**
- **ELFs are GCC 10.2**; the unrolled build stands in for a VLIW compiler's output.
