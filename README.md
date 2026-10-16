# STARBUG 2: analysis, tooling and design notes

Harry Zhu's personal working repository for the **Starbug 2** capstone (Rice ELEC 494, Fall 2026, sponsor
Prof. Ray Simar). This is a workspace for analysis and notes, **not the team's official deliverable**.
Starbug is a 4-lane VLIW extension of the [CORE-V Wally](https://github.com/openhwgroup/cvw) RV32 core,
aimed at DSP and ML kernels. Starbug 2 is the next revision of its ISA and microarchitecture.

This repo holds everything produced on the analysis side so far:
- the documents that explain what was found and **why** each decision was made;
- the simulator patch and build fixes;
- the trace-driven timing model and the ISA trade-study scripts;
- the raw results, plus ELFs and traces, so the study can be re-run without rebuilding anything.

> **No RTL or simulator source has been changed upstream yet.** The team's RTL lives in
> [rvru/cvw](https://github.com/rvru/cvw) and the simulator in [rvru/rvr-starbug](https://github.com/rvru/rvr-starbug).
> Both are used unmodified, apart from the local tracing patch in [`sim/`](sim/). Every "change" so far
> is analysis, tooling or a design proposal.

## Start here

| If you want… | Read |
|---|---|
| Context for an AI/cloud session picking this up | [`CLAUDE.md`](CLAUDE.md) |
| What happened, in order | [`docs/00-history.md`](docs/00-history.md) |
| **The reasoning behind every finding and decision** | [`docs/01-design-rationale.md`](docs/01-design-rationale.md) |
| How the current RTL fetches/decodes bundles, and its suspected bugs | [`docs/03-rtl-analysis.md`](docs/03-rtl-analysis.md) |
| The proposed STARBUG 2 32-bit packet header | [`docs/04-starbug2-header.md`](docs/04-starbug2-header.md) + [`spec/`](spec/) |
| How much each proposed ISA change buys | [`docs/05-isa-trade-study.md`](docs/05-isa-trade-study.md) |
| **Every combination of the structural options, and what to build** | [`docs/07-design-space-exploration.md`](docs/07-design-space-exploration.md) |
| **The compiler: what exists, what each change needs, how to get the best code** | [`docs/08-compiler.md`](docs/08-compiler.md) |
| What's still open | [`docs/06-open-questions.md`](docs/06-open-questions.md) |
| Building and running the MPACT simulator | [`docs/02-mpact-simulator.md`](docs/02-mpact-simulator.md) |
| RV32 instruction reference, marked with what Starbug enables | [`docs/riscv-isa-reference.md`](docs/riscv-isa-reference.md) |

## Headline findings (as of 2026-10-16)

1. **A second memory path is the main lever, not issue width.** Geomean speedup over Starbug 1 across 16 kernels:
   - load-only second path: 1.29× conflict-free, 1.14× with naive two-bank interleaving;
   - second full load/store path: 1.37× conflict-free, 1.21× with two banks;
   - 8-wide with one memory op: 1.005×;
   - gshare, static branch prediction or zero-overhead loops: 1.05–1.07× each.
2. **The 32-bit header costs no cycles. Predication saves almost nothing** on GCC `-O3` code; only the Q15 FIR has hammocks it could convert.
3. **Earlier team estimates are superseded.** On 10-08/09 the team estimated 2.7–3.1× for a second load path and up to 4× for low-overhead branches. Those used different baselines.
4. **The RTL headline of 1.40× compares unequal configs.** `config/starbug` has the branch predictor off, while the `rv32gc` baseline has gshare.
5. **Only lane 0 can do memory, branches or CSRs on the current RTL.** In lanes 1–3 these ops are dropped silently: loads write 0, branches are ignored, and there is no trap. MPACT doesn't model this, so MPACT speedups of hand-scheduled kernels that put loads in worker lanes are upper bounds.
6. **Three suspected RTL bugs**, found by code reading and not yet confirmed in simulation:
   - all lanes use the header's PC;
   - `jal` inside a bundle links to op 0;
   - worker lanes drop ops silently (finding 5).

7. **Design-space exploration (10-16).** Covers 222 combinations plus one-knob sensitivity
   ([`docs/07`](docs/07-design-space-exploration.md)).
   - SB1 bundles average 1.5–2.3 ops of 4.
   - Memory ports *and banks* set performance: 1 LS + 1 load-only is 1.27× with 2 banks, 1.33×
     with 4, 1.44× conflict-free.
   - 8-wide pays only with four conflict-free memory ops.
   - Line-straddle fallback costs 9–19%.
   - Perfect caches are worth +10–12%.
   - Predication buys 0.
8. **Compiler (10-16).** Leo's `starbug-vliw` CPU has no scheduling model, so its machine pipeliner
   never runs ([`docs/08`](docs/08-compiler.md)). In the model, latency-aware scheduling, modulo
   scheduling and alias information together are worth +37–47% on the same hardware: more than any
   hardware change.

## Repository layout

```
CLAUDE.md                 handoff context for an AI/cloud session (environment, conventions, open tasks)
docs/
  00-history.md           dated log of everything done so far
  01-design-rationale.md  the thought process behind each finding and decision
  02-mpact-simulator.md   MPACT fork: what it changes, building on Linux/WSL, picolibc fixes, tracing
  03-rtl-analysis.md      cvw lm/fp: fetch, decode, lanes, memory system, suspected bugs (file:line refs)
  04-starbug2-header.md   interpretation of the custom-0 execute-packet header + open questions
  05-isa-trade-study.md   method, validation against RTL, results, what it overturned, limits
  06-open-questions.md    open design questions and next tasks, prioritised
  07-design-space-exploration.md  full-factorial + sensitivity sweep, recommendations
  08-compiler.md          LLVM fork review, compiler work per hardware change, roadmap
  riscv-isa-reference.md  RV32 ISA organised by extension, Starbug support marked
  notes/MPACT_NOTES.md    original working notes from the simulator bring-up (local paths kept as-is)
spec/
  starbug2-custom0-header.pdf   the team's one-page header proposal
sim/
  rvr-starbug-trace.patch PC/data-address tracing + 16550 UART stub for MPACT rv32g_sim
  picolibc-mpact.ld       picolibc linker script with LMA == VMA (BSD-3, Keith Packard)
mpact_run/                scripts: builds, MPACT runs, trace-driven VLIW model, sweeps, report,
                          dse.py / dse_summary.py (design-space sweeps), bundle_check.py (lane rules)
  tests/                  unit + regression tests for the model (python3 -m unittest discover -s tests)
  kernels/                int8 depthwise conv, Q15 FIR, Embench board glue (written for the study)
  results/                CSV/JSON/HTML results, raw model output, MPACT counters, dse/ tables,
                          elfs.tar.xz (42 benchmark ELFs) and traces.tar.xz (215 MB unpacked)
```

## Upstream code this work is based on

| Repo | Commit used | Role |
|---|---|---|
| [rvru/cvw](https://github.com/rvru/cvw) `origin/lm/fp` | `a3daf96b4` (src last touched `93eb1c3d8`) | Starbug RTL, FP on the worker lanes, `examples/C/embench_starbug` |
| [rvru/cvw](https://github.com/rvru/cvw) `main` | `8e63d1d86` | Older Starbug RTL; same structure |
| [rvru/rvr-starbug](https://github.com/rvru/rvr-starbug) | `ea203a0` | MPACT fork with the VLIW decoder |
| [leomarek/starbug-benchmarks](https://github.com/leomarek/starbug-benchmarks) | `a82f8ca` | dp / fir / fft kernels and `profile.py` |
| [embench/embench-iot](https://github.com/embench/embench-iot) | `54fd9a0` | crc32, nettle-sha256, matmult-int, edn |
| [leomarek/starbug-llvm](https://github.com/leomarek/starbug-llvm) | — | Leo's LLVM fork (`-mcpu=starbug-vliw`); not used here |

## Re-running the trade study (Linux, no rebuild needed)

```bash
sudo apt install python3 binutils-riscv64-unknown-elf     # the model only needs objdump
git clone https://github.com/rvru/cvw -b lm/fp ~/starbug/cvw-lmfp   # only validate.py needs it (RTL cycle CSV)
mkdir -p ~/starbug && cd ~/starbug
tar xJf <repo>/mpact_run/results/elfs.tar.xz && tar xJf <repo>/mpact_run/results/traces.tar.xz
cd <repo>/mpact_run
python3 validate.py && python3 sweep_isa.py && python3 width_vs_ports.py && python3 unroll_sensitivity.py
python3 summarize.py && python3 gen_report.py      # -> results/*.csv, digest.json, starbug_isa_study.html
python3 dse.py all && python3 dse_summary.py       # design-space sweeps (~15 min on 4 cores) -> results/dse/
python3 -m unittest discover -s tests              # model tests, incl. regression against results/raw/
```

`STARBUG_HOME` (default `~/starbug`) and `CVW_LMFP` (default `$STARBUG_HOME/cvw-lmfp`) override the
locations. Running `validate.py` this way on 2026-10-16 reproduced `results/raw/validate.json` byte for byte.
To rebuild everything from source, including MPACT and the ELFs, see [`mpact_run/README.md`](mpact_run/README.md).
