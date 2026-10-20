# Handoff context: STARBUG 2 analysis repo

Read this first if you are an AI or cloud session continuing this work. It holds the context that used
to live on the author's laptop and notes vault, which you **do not** have access to.

## The project in one paragraph
Starbug is a VLIW extension of the CORE-V Wally RV32 core, built by a Rice research group (`rvru` on
GitHub). The ELEC 494 Fall 2026 capstone team "Starbug 2", sponsored by Prof. Ray Simar, is defining
the next revision.

The **current RTL (Starbug 1)** works like this:
- A 16-bit RVC HINT, `c.li x0, N`, marks the next N (1–4) instructions as one bundle, which issues on
  4 lanes in lockstep.
- **Only lane 0 is wired to the load/store unit, the PC redirect and the privileged unit.**

**Starbug 2 proposes:**
- a 32-bit custom-0 packet header with per-op predication (C6x-style creg/z);
- possibly a second memory path;
- possibly 8 lanes;
- a compiler (Leo's LLVM fork).

This repo is the analysis side: what the RTL actually does, what each proposed change is worth, and
why. Nothing here modifies the RTL.

## People and naming
- **"Leo"** is leomarek on GitHub. Leo wrote the `lm/*` branches of rvru/cvw, the Starbug benchmarks,
  the hand-VLIW assembly kernels, the Questa RTL cycle counts used for validation
  (`examples/C/embench_starbug/presentation_assets/benchmark_summary.csv` on `lm/fp`), and an LLVM
  fork. That fork was built on a lab server and is not available here.
- **"SB1"** = Starbug 1 as built today: 4-wide, one memory op per bundle and only in lane 0, no
  branch predictor.
- The repo author is **Harry (Haolin Zhu)**. This is Harry's personal workspace, **not the team's
  deliverable**. Don't add other contributors or team-facing boilerplate; team deliverables live
  elsewhere.

## Environment facts
- The original work ran on Windows + **WSL Ubuntu 22.04**, with a work dir at `~/starbug`.
  - That dir held `rvr-starbug/` (MPACT, patched by `sim/rvr-starbug-trace.patch`), `ex/` (ELFs),
    `traces/` and `embench-iot/`.
  - Scripts default to `STARBUG_HOME=~/starbug`.
- Paths like `D:\Rice\2026 Fall Semester\ELEC 494\Design\...` or `/mnt/d/...` in
  `docs/notes/MPACT_NOTES.md` refer to that laptop. Ignore them.
- **You can re-run the model without building anything.**
  - Unpack `mpact_run/results/elfs.tar.xz` and `traces.tar.xz` into `$STARBUG_HOME`.
  - You need `riscv64-unknown-elf-objdump` (apt `binutils-riscv64-unknown-elf`) and python3. The
    scripts use the standard library only.
  - The full rebuild path (Bazel 7.1.2, clang, GCC 10 + picolibc) is in `mpact_run/README.md`.
- MPACT builds natively on Linux; WSL was only needed on Windows. Build Bazel trees on a local disk,
  not a network or mounted drive (too slow).

## Ground rules learned the hard way (see docs/01-design-rationale.md for why)
1. **Lane legality comes from the RTL.** Memory ops, branches/jumps and CSR/privileged ops go in
   lane 0 only. An instruction-set simulator such as MPACT will happily run illegal bundles and report
   correct output.
2. **MPACT cycle counts are an upper bound.**
   - MPACT counts 1 cycle per bundle.
   - It runs a bundle's ops *sequentially*, while the RTL reads all sources in parallel.
   - It doesn't model stalls or caches.
   - Use the trace-driven model (`mpact_run/vliw_model.py`, ~4% median error vs RTL) for timing.
3. **Always state the baseline and the compiler assumption next to a speedup.**
   - Compare against the same binary without bundling, *and* against the best scalar code (-O2/-O3).
   - Check that both runs use the same machine config. `config/starbug` has `BPRED_SUPPORTED = 0`,
     while `config/rv32gc` has gshare.
4. **Don't mix the team's 10-08/09 estimates with the 10-13 model numbers** (2.7–3.1× and "4×" vs
   1.29–1.37× and ~1.07×). They used different baselines.
5. Suspected RTL bugs in `docs/03-rtl-analysis.md` are **unconfirmed**: they come from code reading
   only. Say so whenever you repeat them.

## Working conventions
- **Git:** Harry chooses commit author, email and message. Propose a message and let him decide.
  Don't invent or silently rewrite identity or messages.
- **This repo is public.** Do not add course-internal documents, the Fisher/Faraboschi/Young
  textbook, stakeholder contact details or anything from the team's private drive without asking.
- When you settle an open question in `docs/06-open-questions.md`, move it into `docs/00-history.md`
  with a date. Also update `docs/01-design-rationale.md` if it changes a decision.

## Where to continue
**Start with [`HANDOFF.md`](HANDOFF.md):** the current task (build Leo's compiler, GCC baseline,
compiler-method study), with a step-by-step plan and the gotchas found so far.

`docs/06-open-questions.md` has the prioritised list. The top items:
1. `bundle_check.py` now enforces the lane-0 rule (done 10-16). Still to do: make MPACT's VLIW
   decoder enforce it, re-measure the dp kernel with a legal schedule, and report the two latent
   hand-kernel violations (A6).
2. Confirm the three suspected RTL bugs with a small assembly test under `wsim` (Questa or Verilator)
   on cvw `lm/fp`.
3. Settle the STARBUG 2 packet-length rule with the designer: "always 4 ops" vs "ends at the first p=0".
4. Port MPACT's decoder and executor to the custom-0 header: snapshot sources, apply predicates, allow
   one memory op per packet.
5. Compiler: give `starbug-vliw` a scheduling model (A7, `docs/08-compiler.md`).

## Model and sweeps
- `vliw_model.make_cfg()` knobs all default to the 10-13 behaviour.
  `python3 -m unittest discover -s tests` (in `mpact_run/`) checks that, and that `isa_sweep.json`
  is reproduced exactly. Run it after any model change.
- `dse.py` sweeps. `dse_summary.py` rebuilds `results/dse/` from `results/raw/dse_*.json` without
  re-running.
