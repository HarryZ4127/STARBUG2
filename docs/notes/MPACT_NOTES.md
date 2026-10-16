> **Archived working notes** from the 2026-10-02 bring-up, copied verbatim apart from this banner and one link fix. Paths such as `/mnt/d/...` and `~/starbug` refer to the author's laptop and WSL. For the maintained version see [`../02-mpact-simulator.md`](../02-mpact-simulator.md).

# MPACT simulator for the STARBUG VLIW (cvw) project

## Where everything lives

| Repo (cloned here) | What it is |
|---|---|
| `cvw/` (github.com/rvru/cvw) | Wally RTL with the STARBUG VLIW changes. **Contains no MPACT code.** Newest work is on branch `origin/lm/fp` (Aug 2026); `main` stops at Apr 2026. |
| `rvr-starbug/` (github.com/rvru/rvr-starbug) | **The MPACT simulator.** A fork of `google/mpact-riscv` (forked 2025-08-28, last commit 2025-11-10) with a VLIW decoder added. Not linked from cvw; found via the `rvru` org. |
| `rvr-starbug/benchmarks` = `cvw/examples/C/starbug_benchmarks` | Same repo (github.com/leomarek/starbug-benchmarks). dp / fir_filter / fft kernels plus `profile.py`, which drives MPACT. (`cvw/starbug/be` is a broken gitlink to the same repo with no `.gitmodules` entry.) |

The project used MPACT during early architecture exploration (Sep–Nov 2025). Later work (cvw `lm/fp`, `examples/C/embench_starbug`) checks results on the RTL with `wsim`/Verilator instead, and nothing there calls MPACT.

## How the VLIW support works (only about 5 files differ from upstream)

**Bundle encoding.** A bundle starts with the 16-bit RVC HINT `c.li x0, N`. The next N ordinary RV32 instructions (16- or 32-bit) belong to the bundle. On any other core this is a legal no-op, so VLIW binaries still run on scalar sims and hardware.

```asm
    c.li x0,3        # bundle of 3
    lw   t2,0(a0)
    addi a0,a0,16
    addi a1,a1,16
```

**Changes in `rvr-starbug/riscv/` compared with upstream:**

1. `riscv32g.isa`: the `cli_hint` semantic function changes from `RiscVINop` to `RV32::RiscVExVLIW`.
2. `riscv_i_instructions.cc`: `RiscVExVLIW` walks `instruction->child()->next()...` and calls `Execute()` on each child in order.
3. `riscv32_vliw_decoder.cc`: a copy of `riscv32_decoder.cc`. In `DecodeInstruction`, when the opcode is `kCliHint` it reads N from the immediate (`Source(1)`). It then recursively decodes the next N instructions, `AppendChild`s each one to the hint instruction, and sets the hint's `size` to 2 plus the children's bytes. The result is one "fat" instruction, and the PC jumps over the whole bundle.
4. `riscv32_vliw_encoding.{h,cc}`: a verbatim copy of `riscv32g_encoding` (only the include changes).
5. `rv32_vliw_sim.cc`: a verbatim copy of `rv32g_sim.cc` that includes the VLIW decoder. `rv32g_sim.cc` gained a `Cycle count = <mcycle>` print on stderr, which `profile.py` parses.
6. `riscv/BUILD`: new `riscv32_vliw_decoder` library and `rv32_vliw_sim` binary targets.

**Cycle model.** MPACT is a functional ISS. `RiscVTop` adds 1 to `mcycle` per top-level instruction, so a whole bundle costs 1 cycle and every `nop` costs 1 cycle. Stalls, cache misses, branch penalties, and mul/load latency are not modeled. The count is an ideal "bundles + scalar instructions retired" figure, not RTL cycles.

## Gaps between MPACT and the RTL (cvw `lm/fp` `src/ifu/ifu.sv`, `src/ieu/controller.sv`)

| | MPACT | RTL |
|---|---|---|
| Bundle size | Any N; a negative imm wraps through `uint8_t` to 255 | 1–4 lanes only; otherwise treated as a nop hint |
| Bundle placement | Anywhere | Must fit in one I-cache line, cacheable memory, no spill |
| Intra-bundle semantics | **Sequential**: child k sees writes from children 0..k-1 | Parallel lanes; same-register writes within a bundle are assumed not to happen |
| Lane legality (which op in which lane) | Not checked | Lane 0 = full core, worker lanes restricted (FP added later) |
| Memory ops | Flat functional memory: any number of loads/stores per bundle, in any lane | **One LSU, wired to lane 0 only** (`wallypipelinedcore.sv` `lsu` instance). A load/store in lanes 1–3 is **not trapped**: the store is dropped and the load writes 0 (`ReadDataW_1..3 = '0`). The newer `lm/fp` kernels keep all 513 memory ops in lane 0. The older MPACT-era benchmarks have 16 in lanes ≥ 1 (e.g. `dp_vliw_better.S`), so they would compute wrong results on the RTL. |
| Hand-inserted `nop`s after bundles (see `dp_vliw.S`) | Each costs a full cycle | Needed for hazards at the time |

So a bundle can pass on MPACT and still break on RTL: a RAW dependency inside one bundle works in MPACT but not on hardware, and a 5-wide bundle runs in MPACT but the RTL won't accept it.

## How to run it (the original author used macOS / Linux)

Requirements: Bazel 7.1.2 (`.bazelversion`, use bazelisk), clang (`.bazelrc` sets `CC=clang`), C++17, and `riscv64-unknown-elf-gcc` with newlib (`semihost.specs`) to build the ELFs. MPACT does not build natively on Windows, so use WSL Ubuntu.

```bash
# build the sims
cd rvr-starbug
bazel build //riscv:rv32g_sim //riscv:rv32_vliw_sim

# build a benchmark (default target = semihosted, -O0, rv32imac, link.ld @ 0x80000000)
cd benchmarks/dp
python3 gen_data.py 256 > data.S
make scalar empty vliw      # NOTE: the Makefile's VLIW_SRC=dp_vliw_basic.S does not exist in this commit;
                            # use VLIW_SRC=dp_vliw.S (or dp_vliw_better.S)

# run
cd ../../riscv
bazel run :rv32g_sim     -- --semihost_arm "$(realpath ../benchmarks/dp/dp_scalar.elf)"
bazel run :rv32_vliw_sim -- --semihost_arm "$(realpath ../benchmarks/dp/dp_vliw.elf)"
#   stderr: "Simulation done: ...", "Cycle count = N"; stdout: program printf output

# or sweep all lengths, compare outputs, and plot cycle counts (VLIW vs scalar vs empty-kernel baseline):
cd ../benchmarks/dp && python3 profile.py     # needs numpy, matplotlib, and the `script` utility
```

Other useful flags (from `rv32_vliw_sim.cc`): `-i` for the interactive debug shell, `--semihost_htif`, `--exit_on_tohost`, `--icache/--dcache <cfg>`, `--output_dir` (counter export), and `--quiet`. The VLIW decoder `LOG(INFO)`s every decoded opcode, so stderr is very noisy. Comment those lines out for real runs.

## Running it on this laptop (WSL Ubuntu 22.04, verified 2026-10-02)

What's installed: clang 14, g++, OpenJDK 11, `gcc-riscv64-unknown-elf` 10.2 + picolibc (Ubuntu ships no newlib), numpy/matplotlib, and bazelisk at `~/bin/bazel` (it fetches 7.1.2).
- The simulator lives in the WSL home folder at `~/starbug/rvr-starbug`, because Bazel over `/mnt/d` is very slow. The first build took 2 minutes: `~/bin/bazel build -c opt //riscv:rv32g_sim //riscv:rv32_vliw_sim`.
- Binaries: `~/starbug/rvr-starbug/bazel-bin/riscv/{rv32g_sim,rv32_vliw_sim}`. Build/run directory: `~/starbug/dp_build`.

Helper scripts are in [`mpact_run/`](../../mpact_run/). Run them from a WSL shell, e.g. MobaXterm → Session → WSL:

```bash
bash "/mnt/d/Rice/2026 Fall Semester/ELEC 494/mpact_run/sweep.sh" 32 256 1024   # kernel-only cycles + output check
bash "/mnt/d/Rice/2026 Fall Semester/ELEC 494/mpact_run/build_dp.sh" 256        # plain dp ELFs (original driver)
bash "/mnt/d/Rice/2026 Fall Semester/ELEC 494/mpact_run/run_dp.sh"              # whole-program cycle counts
python3 "/mnt/d/Rice/2026 Fall Semester/ELEC 494/mpact_run/bundle_check.py" ~/starbug/dp_build/t_vb.elf  # hazard scan
```

**Three fixes were needed to use Ubuntu's picolibc instead of the original macOS newlib** (none touch the simulator):
1. `--crt0=hosted`. picolibc's default `crt0` ends with `j .` after `main` and never exits.
2. A linker script with `.data`/`.tdata` loaded in place (`AT>ram`). MPACT's ELF loader puts `.data` at its run address (VMA), so crt0's flash→RAM copy overwrote it with zeros. Symptom: `len == 0` and no output.
3. `mpact_stdio.c` routes stdout through semihost `SYS_WRITE(fd 1)`, because MPACT doesn't implement `SYS_WRITEC`/`SYS_WRITE0`, which picolibc uses by default. MPACT's `SYS_WRITE` also returns bytes *written* rather than ARM's bytes *not written*.

Also, `.attribute arch` lines from Leo's GCC 14 must be stripped for binutils 2.38. They are metadata only.

The prebuilt `dp_vliw.elf` checked into the benchmarks repo contains **no** bundle hints; it's a scalar build. Don't use it to test the VLIW sim.

**Results** (dp kernel only, read with `csrr mcycle` around the `dot_product()` call; outputs bit-identical to the scalar reference at every length):

| len | scalar C -O0 | scalar C -O2 | `dp_vliw_better.S` on vliw sim | same ELF on rv32g_sim (no bundling) | `dp_vliw.S` on vliw sim |
|---|---|---|---|---|---|
| 256 | 15,680 | 2,322 | **562** | 1,858 | 751 |
| 1024 | 62,528 | 9,234 | **2,098** | 7,282 | 2,863 |
| 4096 | 249,920 | 36,882 | **8,242** | 28,978 | 11,311 |

So on this ideal 1-cycle-per-bundle model, `dp_vliw_better.S` comes out 3.3–3.5× faster than the same code without bundling and 4.1–4.5× faster than `gcc -O2`. The original `profile.py` compares against **-O0** scalar C (28–30×), which overstates the gain. `bundle_check.py` found 29 + 7 bundles, all ≤ 4 wide and with no intra-bundle RAW/WAW. **However, both kernels put loads/stores in worker lanes**, e.g. `lw | c.lw | addi | addi` and `mul | sw`. The RTL only serves memory from lane 0, so these kernels would not run correctly on the current hardware. A fair MPACT model needs a "one memory op per bundle, lane 0" rule (see the table above).

Metric used by `profile.py`: `improvement = ((scalar - empty) - (vliw - empty)) / (scalar - empty)`. "Empty" is the same driver with an empty kernel, so its count is the setup/loop overhead that gets subtracted.

## ISA trade study (2026-10-13)

`mpact_run/` now traces benchmarks on a patched MPACT and models what each proposed ISA change buys:
- **Traces:** `trace_patch.py` adds a PC + data-address trace and a UART stub to `rv32g_sim`, so the cvw bare-metal ELFs run unchanged.
- **Benchmarks:** `build_embench_dsp.sh` builds the team's 10 Embench DSP kernels; `build_extra.sh` builds crc32, sha256, matmult-int, edn, int8 depthwise conv and Q15 FIR. `run_traces.sh` traces all of them.
- **Model:** `vliw_model.py` holds the timing model and per-configuration scheduler. `validate.py` checks it against Leo's RTL cycle counts (median error about 4%).
- **Sweeps:** `sweep_isa.py`, `width_vs_ports.py`, `unroll_sensitivity.py`.
- **Outputs:** `summarize.py` and `gen_report.py` write `results/` (CSVs, `digest.json`, `starbug_isa_study.html`).

Headline: a second memory path is the main lever (+29% load-only, +37% full LS over SB1 today, geomean, if paired accesses don't share a bank; about half that with naive 2-bank interleaving). 8-wide adds ~1% with 1–2 memory ports. Branch features add 5–7% on unrolled code. The 32-bit header costs no cycles (+14% hot-code size). Predication has almost nothing to remove in these kernels. Compiler unrolling (1.7–2.6×) beats every hardware change.
