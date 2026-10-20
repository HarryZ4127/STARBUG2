# Handoff: compiler study (written 2026-10-20 at the end of a cloud session)

For the next session, most likely a local one on Harry's laptop (WSL, `~/starbug`). Read
[`CLAUDE.md`](CLAUDE.md) first (conventions and ground rules), then this file. Background:
- [`docs/07-design-space-exploration.md`](docs/07-design-space-exploration.md): hardware sweep;
- [`docs/08-compiler.md`](docs/08-compiler.md): review of Leo's compiler, gaps G1–G5, roadmap.

## 1. Where things stand
- **Branch:** `dse-and-compiler-review`, not merged into `main`.
  - Its first commit (`e547e3b`) is authored `Claude <noreply@anthropic.com>`, because a stop hook
    forced a commit. Harry may amend author or message: `git commit --amend --reset-author`, then
    force-push the branch.
- **Done in the cloud session:**
  - `vliw_model.make_cfg()` knobs for every structural option; the defaults reproduce the 10-13
    results byte for byte;
  - 31 tests (`cd mpact_run && python3 -m unittest discover -s tests`);
  - `dse.py` sweeps and their results (`mpact_run/results/dse/`, raw output in `results/raw/dse_*.json`);
  - `bundle_check.py` lane rules;
  - docs 07 and 08.
- **Slide deck:** two slides summarising docs 07–08, at https://claude.ai/artifact/Tq92fG9qhbkbh16WRxCsMf
  (private to Harry; not in this repo).
- **Compiler numbers so far are model stand-ins, not Leo's compiler.** Every policy in doc 07 §6 and
  doc 08 §4 (greedy packing, list scheduling, modulo bound) re-schedules **GCC 10.2** binaries
  inside `vliw_model.py`. Leo's clang has **not** been built or run in this repo. The only measured
  numbers for it are Leo's own RTL runs: 1.40× over `rv32gc` scalar on 5 kernels, 0.956× of hand
  code (`leomarek/starbug-llvm` `docs/paper/02-results.md`).

## 2. The task Harry asked for next
> 1. Try to build Leo's compiler.
> 2. Use a standard GCC as a baseline.
> 3. Explore how different compiler methods affect the speedup.

Done when:
- Leo's clang builds;
- the 16 kernels compile with it, run correctly (TEST PASS / SNR) and pass `bundle_check.py`;
- a table compares GCC `-O3` scalar, clang `-O3` scalar, and Leo's compiler under the flag
  variants of §3.5, with cycles from the model (and RTL where Questa is available);
- a short `docs/09-compiler-study.md` interprets the table, and the model stand-ins in doc 08 §4
  are checked against the real compiler.

## 3. Suggested plan

### 3.1 Environment (laptop WSL already has most of this)
- **Already installed:**
  - `riscv64-unknown-elf-gcc` 10.2 + picolibc;
  - `binutils-riscv64-unknown-elf` (2.42 also works: the cloud session reproduced every result with it);
  - Bazel/MPACT in `~/starbug/rvr-starbug`, patched by `sim/rvr-starbug-trace.patch`.
- **Inputs without rebuilding:** unpack `mpact_run/results/elfs.tar.xz` and `traces.tar.xz` into
  `$STARBUG_HOME` (default `~/starbug`).
- **For `validate.py`:** `$CVW_LMFP`, a checkout of rvru/cvw `lm/fp` (`a3daf96b4`).
- **For the LLVM build:** `cmake`, `ninja-build`, `clang` or `g++`, `lld`, plus ~30 GB of disk and
  16 GB+ RAM.

### 3.2 Build Leo's compiler
The Starbug passes are already on the fork's branch (`llvm/lib/Target/RISCV/Starbug*`, commit
`4a1edc71`); the `starbug-llvm` overlay scripts are not needed.
```bash
cd ~/starbug
git clone --depth 1 -b codex/starbug-vliw https://github.com/leomarek/llvm-project
git clone https://github.com/leomarek/starbug-llvm          # tools/, starbug-tests/, docs/paper/
cmake -S llvm-project/llvm -B llvm-project/build-starbug -G Ninja \
  -DLLVM_ENABLE_PROJECTS="clang;lld" -DLLVM_TARGETS_TO_BUILD=RISCV \
  -DCMAKE_BUILD_TYPE=Release -DLLVM_ENABLE_ASSERTIONS=ON -DLLVM_PARALLEL_LINK_JOBS=1
ninja -C llvm-project/build-starbug -j3 clang lld llvm-objdump llvm-mc   # -j3: Leo was OOM-killed above that
```
- **Smoke test:**
  `clang --target=riscv32-unknown-elf -march=rv32imafc -mabi=ilp32 -mcpu=starbug-vliw -O3 -S starbug-llvm/starbug-tests/fir_filter.c`.
  Expect `c.li zero,N` lines.
- **Check the result:** run `python3 mpact_run/bundle_check.py <obj>`; expect 0 flagged.
- **Assertions are on deliberately:** Leo's build had them off. If an assertion fires, that is a
  finding; report it to Leo.

### 3.3 Build the 16 kernels with every compiler
The kernel list is in `mpact_run/sweep_isa.py`: 10 Embench DSP, 4 integer DSP/ML, 2 ALU.

| Variant | How | Status |
|---|---|---|
| `scalar` (GCC 10 `-O3`) | `build_embench_dsp.sh`, `build_extra.sh` | ELFs and traces exist |
| `unroll` (GCC 10 `-O3` + unroll flags) | same | exist |
| `vliw` (Leo's hand-written asm, DSP only) | same | exist |
| **`clang_scalar`** (clang `-O3 -mcpu=generic-rv32`, no Starbug) | new | Separates "clang vs GCC" from "bundling". Leo lists this missing baseline as his Tier-1 item 1.2 |
| **`starbug`** (Leo's compiler, his defaults) | cvw `make starbug-compile` (DSP); a clang variant of `build_extra.sh` (others) | new |
| **`starbug_<flag>`** | §3.5 | new |

Gotchas for `starbug-compile` (`examples/C/embench_starbug/common/bench_common.mk` on cvw `lm/fp`):
- **Paths:** set `STARBUG_LLVM_BUILD=~/starbug/llvm-project/build-starbug`.
- **Headers and libraries:** it takes C headers from `$(STARBUG_GCC) -print-sysroot`, and
  Ubuntu's bare-metal GCC has none. Pass picolibc as the GCC builds do:
  ```
  BENCH_CPPFLAGS="-isystem /usr/lib/picolibc/riscv64-unknown-elf/include"
  STARBUG_LIBS="-L/usr/lib/picolibc/riscv64-unknown-elf/lib/rv32imac/ilp32 -lm -lc $(riscv64-unknown-elf-gcc -march=rv32imac -mabi=ilp32 -print-libgcc-file-name)"
  ```
  **Never plain `-lgcc`**: it picks the rv64 libgcc (`docs/02-mpact-simulator.md`). It links with
  GCC (`STARBUG_LINKER`), so the rest of the Wally runtime is unchanged.
- **Leo's default flags turn things off:** `STARBUG_MLLVM` sets
  `-starbug-vliw-force-unroll=false -starbug-vliw-enable-machine-pipeliner=false`.
  Add variants through `EXTRA="..."`.
- **Stale objects:** the object rule doesn't depend on the flags. Run
  `rm -rf */.build-starbug */*_starbug.elf` between variants.
- **Hand-asm cleanup:** strip `.attribute arch` lines if binutils complains (done in
  `build_embench_dsp.sh`).

### 3.4 Trace and time them
- **Trace** each new ELF on the traced MPACT `rv32g_sim`. `run_traces.sh` only globs
  `*_wally.elf` and `extra/*.elf`, so extend the glob. The `c.li x0,N` hints are no-ops there, so
  the trace is just the PC and address stream.
  - The model needs the symbols `start_trigger`/`stop_trigger` in each ELF; check that clang keeps
    them (not inlined or GC'd).
- **Time** each one:
  - Leo's actual bundles under SB1 rules: `vm.run_as_written(elf, trace, vm.SB1)`. Line-straddle
    fallback and N ≤ 4 are modelled; lane legality is not, so `bundle_check.py` must be clean.
  - Baselines: `vm.run_as_written(scalar_elf, trace, vm.RV32GC, honor_hints=False)`.
  - The doc 08 §4 stand-ins on the same clang code, for a like-for-like check of the model's
    policies against Leo's real packetizer: `vm.run_scheduled(clang_scalar_elf, trace, cfgs=...)`.
- **Extend `sweep_isa.paths()`** with the new variant names rather than adding a parallel path scheme.
- **RTL truth, if Questa is reachable:** run Leo's `run_questa_vliw.py --build-target starbug-compile --configs starbug`.
  Logs are overwritten on every run; copy them first. Report RTL next to the model, never instead
  of it.

### 3.5 Compiler methods to vary
All are `-mllvm` options passed through `EXTRA` (definitions in `StarbugVLIWConfig.cpp`; ablations in
Leo's `docs/paper/07-reproduction.md` §6). Change one at a time from Leo's defaults, then try the
best combination.

| Method | Flags / settings | Expectation (doc 07/08) |
|---|---|---|
| Unrolling | `-starbug-vliw-force-unroll=true`, `-starbug-vliw-unroll-factor={4,8,16,32}`, `-starbug-vliw-max-unroll` | Big on non-CMSIS kernels; the knee is ~8 (Leo); spills hurt past it |
| Packetizer look-ahead | `-starbug-vliw-packetizer-lookahead={8,16,64,128}` | Bounds how far ops move |
| WAR in packets | `-starbug-vliw-allow-intra-packet-war={true,false}` | Small; Leo's ablation |
| FP in worker lanes | `-starbug-vliw-packetize-fp=false` | Large loss on float DSP (model: −15%) |
| I$-line layout | `-starbug-vliw-bundle-layout={true,false}`, with and without `-mno-relax` | +2–3% on RTL (Leo) |
| Alias assumptions | `-starbug-vliw-assume-no-memory-alias=true` / `-starbug-vliw-assume-disjoint-memory=true`, or `restrict` in the kernels | Model: +12% on SB1. **Check outputs: unsafe if pointers alias** |
| Trace scheduler | `-starbug-vliw-enable-trace-scheduler=true` (needs the alias flag) | Unknown |
| Machine pipeliner | `-starbug-vliw-enable-machine-pipeliner=true` | **Expected no-op** (doc 08 G1: `NoSchedModel`). Confirm by diffing the `.s` |
| Packet scoring | `-starbug-vliw-prefer-lsu-anchored-packets`, `-starbug-vliw-prefer-mac-overlap`, `-starbug-vliw-reserve-lane0-for-any` (each `=false`) | Heuristic tuning |
| Scalar compiler | GCC `-O3` vs clang `-O3` vs GCC unroll flags | Isolates the compiler difference from bundling |

For each variant, record:
- cycles (as-written SB1 model; RTL if available);
- geomean vs GCC `-O3` scalar on RV32GC;
- ops per bundle;
- hints emitted;
- `bundle_check` result;
- correctness.

Write a `mpact_run/compiler_sweep.py` that produces `results/raw/compiler_flags.json` plus a
`dse_summary.py`-style table.

### 3.6 Stretch: the scheduling model (open item A7)
Add `RISCVSchedStarbug.td` (doc 08 §5 step 1), switch `STARBUG_VLIW` in `RISCVProcessors.td` from
`NoSchedModel` to it, then re-run the pipeliner row of §3.5. That is the experiment that tests doc
08's main claim.

## 4. Gotchas the cloud session hit
- **`pkill -f "<pattern>"` also kills your own shell** if the pattern appears in the command line.
  Kill by PID.
- **`nettle-sha256` has a 615-instruction loop body with 319 memory ops.** Anything super-linear per
  block (e.g. the Karp max-mean-cycle that was replaced in `modulo_ii`) takes tens of minutes on it.
- **Timing:** the full `dse.py all` takes ~15 min on 4 cores; `sweep_isa.py` ~20 s.
- **Stale configs in Leo's repo:** `starbug-llvm/llvm-overlay/config/starbug_vliw_lanes.yaml` and
  `starbug-tests/compile_starbug.sh` (unroll 32/128) disagree with the C++ defaults (8/16, FP and
  logic ops in worker lanes). Trust the C++.
- **Latent bugs:** `bundle_check.py` flags two in Leo's hand-written kernels (open item A6). They are
  not compiler bugs, so don't let them confuse the compiler results.

## 5. Conventions (from CLAUDE.md)
- **Git:** propose commit messages and let Harry choose author and message.
- **Public repo:** nothing from the team drive, the textbook, or stakeholder contacts.
- **Speedups:** state the baseline and the compiler next to every one.
- **When done:** move settled items from `docs/06-open-questions.md` to `docs/00-history.md`
  with a date.
