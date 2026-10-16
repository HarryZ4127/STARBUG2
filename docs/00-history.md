# Project history

A dated record of what was done on the analysis side, what each step produced, and what it changed.
The reasoning behind each step is in [`01-design-rationale.md`](01-design-rationale.md).

---

## 2026-10-01 → 10-02: Locating and bringing up the MPACT simulator
**What:**
- Cloned rvru/cvw (Starbug RTL) and found that it contains **no simulator code**.
- Found the MPACT fork, rvru/rvr-starbug, through the `rvru` GitHub org. Nothing in cvw links to it.
- Identified the ~5 files the fork changes relative to google/mpact-riscv: a VLIW decoder that turns
  `c.li x0, N` into one "fat" instruction with N children, plus a cycle-count print.
- MPACT doesn't build on native Windows, so it was built under WSL Ubuntu 22.04. The first build took
  2 minutes, using Bazel 7.1.2 via bazelisk and clang 14.

**Problems hit and fixed:** the original ELFs were built on macOS with newlib, and Ubuntu ships
picolibc. None of these fixes touch the simulator.
1. picolibc's default `crt0` ends in `j .` and never exits. Fixed with `--crt0=hosted`.
2. `.data` read as zero. MPACT's loader ignores LMA, so crt0's flash→RAM copy wiped the data. Fixed by
   linking with LMA == VMA: [`sim/picolibc-mpact.ld`](../sim/picolibc-mpact.ld).
3. No printf output. MPACT lacks `SYS_WRITEC` and `SYS_WRITE0`. Fixed by routing stdout through
   `SYS_WRITE` (`mpact_run/mpact_stdio.c`).
4. Leo's GCC 14 `.attribute arch` lines are rejected by binutils 2.38. They are stripped; they're
   metadata only.

**Result: dot-product kernel, cycles measured around the call only.** Every output is bit-identical to
the scalar run.

| len | scalar -O0 | scalar -O2 | `dp_vliw_better.S` on VLIW sim | same ELF on scalar sim |
|---|---|---|---|---|
| 256 | 15,680 | 2,322 | 562 | 1,858 |
| 1024 | 62,528 | 9,234 | 2,098 | 7,282 |
| 4096 | 249,920 | 36,882 | 8,242 | 28,978 |

So the speedup is 3.3–3.5× against the same code without bundling and 4.1–4.5× against `gcc -O2`.
The original `profile.py` reported 28–30× because it compared against `-O0`.
→ Rationale §1, §2. Details: [`02-mpact-simulator.md`](02-mpact-simulator.md).

## 2026-10-03: ISA reference, header proposal, and the RTL read-through
- **RV32 ISA reference** ([`riscv-isa-reference.md`](riscv-isa-reference.md)). Instructions are grouped
  by extension and function, with the extensions `config/starbug` enables marked. FP is enabled only on
  `lm/fp`.
- **Interpreted the team's STARBUG 2 header proposal** ([`spec/starbug2-custom0-header.pdf`](../spec/)).
  The proposal is a 32-bit custom-0 header carrying four creg/z predicate nibbles and four p-bits.
  Identified the open questions, of which the largest is that the header does not encode the packet
  length. → [`04-starbug2-header.md`](04-starbug2-header.md).
- **Read the RTL's fetch and decode path on `lm/fp`** (`ifu.sv`, `wallypipelinedcore.sv`,
  `controller.sv`, `regfile_widened.sv`). → [`03-rtl-analysis.md`](03-rtl-analysis.md). Findings:
  - **Only lane 0 is connected** to the LSU, the PC redirect, CSRs and the illegal-instruction trap. In
    lanes 1–3, loads write 0, branches and CSR ops are ignored, and illegal instructions become nops,
    all silently.
  - **Consequence for the 10-02 result.** `dp_vliw_better.S` puts `c.lw`/`sw` in worker lanes, so its
    3.3–4.5× MPACT speedup **cannot run correctly on the RTL**. Across the older MPACT-era kernels, 16
    memory ops sit in lanes ≥ 1. All 513 memory ops in `lm/fp`'s `embench_starbug` are in lane 0.
  - **Suspected bugs:**
    - every lane uses the header's PC, so `auipc` is wrong and a lane-0 branch lands 2 bytes early;
    - `jal` inside a bundle links to op 0;
    - the one-cycle full-line extraction walk is a timing risk.
  - `mpact_run/bundle_check.py` was written on 10-02. It lists bundles in an ELF and flags
    intra-bundle RAW/WAW and width problems that MPACT hides. It found 29 + 7 bundles, all ≤ 4 wide
    with no intra-bundle RAW/WAW. It did **not** yet check the lane-0 rule, which was only discovered
    here.

## 2026-10-08 → 10-09: Team meeting with Dr. Simar, Design Foundation Document, weekly report
These were team-level activities; the documents are on the team's drive and are not in this repo.
- The team set the priority order: (1) 32-bit reserved header, (2) compiler, (3) 8-wide with two or
  more load paths. Branch features came at lower priority.
- Two technical issues were raised:
  - **8-wide header encoding:** chain 32-bit packets with p-bits, or add a second header word for
    creg/z?
  - **Dual loads:** two full load/store paths risk memory hazards when both access memory in the same
    cycle. The alternative is one load/store path plus one load-only path, possibly with interleaved
    memory.
- The team had estimates of **2.7–3.1×** for a second load path (matmul / depthwise conv) and **up to
  4×** for low-overhead branches (SHA-256 / CRC-32). The 10-13 study later superseded both.
- Harry owned Design Foundation Document §2.
- Local files were reorganised into `Design/` (repos, notes, scripts) and `Documents/` (reports).

## 2026-10-13: ISA trade study
**Built a trace-driven timing model**, so each proposed change could be priced on the same code. The
pipeline:
1. A patch to MPACT `rv32g_sim` logs every retired PC and data address, and stubs the 16550 UART so
   cvw's bare-metal ELFs run unchanged ([`sim/rvr-starbug-trace.patch`](../sim/)).
2. The team's 10 Embench DSP f32 kernels were built, plus 4 Embench-IoT kernels and 2 new int8/Q15
   kernels, using GCC 10.2 `-O3` with and without Leo's unrolling flags.
3. All 42 ELFs were traced.
4. `vliw_model.py` re-schedules each basic block for each machine config. It models caches, latencies,
   branch prediction, lane rules and bank conflicts.

**Validation:**
- The model was checked against Leo's Questa RTL cycle counts: ~4% median error.
- Two mechanisms showed up only through validation error patterns, and both were then modelled:
  - the D-cache: table-heavy dct4/rfft first came out 18–30% fast;
  - the baseline's branch predictor: scalar FIR first came out 28% slow.

**Swept 15 configurations over 16 kernels.** Results are in
[`05-isa-trade-study.md`](05-isa-trade-study.md).
- The second memory path is the lever: 1.29× load-only and 1.37× full load/store, conflict-free.
  Naive banking gives back about half.
- 8-wide adds ~1%. Branch features add 5–7% each. The header costs 0 cycles. Predication buys about 0.
- **Superseded** the team's 10-08/09 estimates.
- **Found** that the RTL headline of 1.40× compares `config/starbug` (no predictor) with
  `config/rv32gc` (gshare).

**Recommendation:**
- one load/store path plus one load-only path;
- compiler/linker bank placement;
- re-enable gshare;
- add 8-wide only after the second memory path.

## 2026-10-14: Weekly report
The trade-study results went into the team's 10/14 weekly report.

## 2026-10-16: This repository
- Collected the scripts, results, patches, spec and notes into one repo.
- Made the scripts location-independent through the `STARBUG_HOME` and `CVW_LMFP` environment
  variables.
- Packed the ELFs and traces, 1.5 MB compressed.
- Wrote these docs for handoff.
- Checked reproducibility: `validate.py` run from the tarballs reproduced `results/raw/validate.json`
  exactly.

## 2026-10-16: Design-space exploration and compiler review
- **Model.** Extended `vliw_model.make_cfg()` with a knob for every structural option: latencies,
  shared units, RF ports, 2/4/8 word or XOR banks, cache size/penalty, fetch bandwidth, line-straddle
  policy per header, predication, a greedy-packetizer scheduler and a software-pipelining bound.
  - Defaults reproduce the 10-13 sweep and the RTL validation byte for byte.
  - 31 unit tests in `mpact_run/tests/`.
- **Ran** a 222-config full factorial, 106 one-knob changes around SB1 and the SB2 candidate, and a
  36-config compiler study (`dse.py`). Results: [`07`](07-design-space-exploration.md).
- **Found:**
  - SB1 bundles average 1.5–2.3 ops of 4; 2 lanes cost only 6% on SB1.
  - Banks matter as much as ports: 1 LS + 1 LO is 1.27× with 2 banks, 1.33× with 4, 1.44×
    conflict-free; XOR hashing doesn't help at 2 banks.
  - 8-wide pays only with four conflict-free memory ops.
  - Line-straddle fallback costs 9–19%; ≥ 16 B/cycle fetch is enough.
  - Perfect caches are worth +10–12%.
  - FP must stay in worker lanes (−15% otherwise).
  - Predication: 0.
- **Read Leo's compiler** (`leomarek/llvm-project` `codex/starbug-vliw`). Found that `starbug-vliw`
  uses `NoSchedModel`, so the machine pipeliner the pipeline enables never runs. See
  [`08`](08-compiler.md).
  - Model: latency-aware scheduling is worth up to +27%, modulo scheduling a further +7–17%,
    alias information +12–20%.
- **`bundle_check.py`** now enforces lane rules. It found a masked RAW in `arm_bitreversal_16` and a
  slot-1 branch in `arm_dct4_f32` (hand-written kernels; latent, not affecting measured results).

