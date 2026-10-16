# MPACT simulator for Starbug

How the Starbug MPACT fork works, how to build and run it, and the fixes needed to use Ubuntu's toolchain.
The original bring-up notes, with laptop paths, are in [`notes/MPACT_NOTES.md`](notes/MPACT_NOTES.md).

## What the fork changes in google/mpact-riscv
[rvru/rvr-starbug](https://github.com/rvru/rvr-starbug) @ `ea203a0` is a fork of google/mpact-riscv,
forked 2025-08-28 with its last commit on 2025-11-10. About five files differ, all under `riscv/`:

| File | Change |
|---|---|
| `riscv32g.isa` | The `cli_hint` semantic function becomes `RV32::RiscVExVLIW` (was `RiscVINop`) |
| `riscv_i_instructions.cc` | `RiscVExVLIW` walks `instruction->child()->next()…` and `Execute()`s each child **in order** |
| `riscv32_vliw_decoder.cc` | Copy of `riscv32_decoder.cc`. On `kCliHint` it reads N from the immediate, decodes the next N instructions as children, and sets the parent's size to 2 + children's bytes, so the PC skips the bundle |
| `riscv32_vliw_encoding.{h,cc}`, `rv32_vliw_sim.cc` | Copies of the rv32g versions that use the VLIW decoder |
| `rv32g_sim.cc` | Prints `Cycle count = <mcycle>` on stderr, which `profile.py` parses |
| `riscv/BUILD` | Adds the `riscv32_vliw_decoder` and `rv32_vliw_sim` targets |

**Cycle model.** `RiscVTop` adds 1 to `mcycle` per top-level instruction, so a bundle costs 1 cycle.
Stalls, caches and latencies are not modelled.

## Gaps between MPACT and the RTL
| | MPACT | RTL (cvw `lm/fp`) |
|---|---|---|
| Bundle size | Any N; a negative imm wraps through `uint8_t` to 255 | 1–4, otherwise treated as a nop hint |
| Placement | Anywhere | Must fit in one I-cache line, in cacheable memory, not mid-spill |
| Intra-bundle semantics | **Sequential**: child k sees writes from children 0..k−1 | **Parallel**: all lanes read the register file together |
| Lane legality | Not checked | Memory, branches and CSRs in lane 0 only; worker lanes drop them silently |
| `nop`s after bundles | Each costs a full cycle | Needed for hazards |

A bundle can therefore pass on MPACT and still break on the RTL. See
[`01-design-rationale.md` §3](01-design-rationale.md#3-lane-legality-why-a-correct-output-on-mpact-isnt-proof-of-a-correct-schedule).

## Building (Linux; on Windows use WSL Ubuntu 22.04)
```bash
sudo apt install clang g++ default-jre-headless gcc-riscv64-unknown-elf picolibc-riscv64-unknown-elf \
                 python3-numpy python3-matplotlib
# Bazel 7.1.2 via bazelisk, e.g. ~/bin/bazel
mkdir -p ~/starbug && cd ~/starbug
git clone https://github.com/rvru/rvr-starbug && cd rvr-starbug && git checkout ea203a0
git submodule update --init            # benchmarks = leomarek/starbug-benchmarks
bazel build -c opt //riscv:rv32g_sim //riscv:rv32_vliw_sim     # ~2 min the first time
# binaries: bazel-bin/riscv/{rv32g_sim,rv32_vliw_sim}
```
- `.bazelrc` sets `CC=clang`, and the code needs C++17.
- Build on a local disk. Bazel over a mounted Windows drive (`/mnt/d`) is very slow.

## Running
```bash
rv32_vliw_sim --semihost_arm prog.elf      # semihosted picolibc programs (dp benchmark)
rv32g_sim --exit_on_tohost prog.elf        # cvw bare-metal ELFs (needs the tracing patch's UART stub)
```
Useful flags:
- `-i`: interactive shell with `break <addr>`, `run`, `wait`, `reg pc`, `reg mcycle`, `quit`;
  commands can be piped in on stdin;
- `--semihost_htif`, `--exit_on_tohost`;
- `--icache/--dcache <cfg>`;
- `--output_dir`: exports the counters, which is how `results/mpact_counters/*.proto` were made;
- `--quiet`.

The VLIW decoder `LOG(INFO)`s every decoded instruction, so stderr is very noisy.

## Ubuntu's picolibc vs. the original macOS newlib: four fixes
None of these touch the simulator.
1. **`--crt0=hosted`.** picolibc's default crt0 ends in `j .` after `main` and never exits.
2. **Link with LMA == VMA.** Use [`sim/picolibc-mpact.ld`](../sim/picolibc-mpact.ld), which is
   `picolibc.ld` with `AT>flash` changed to `AT>ram` for `.data`/`.tdata`.
   - Cause: MPACT's ELF loader places segments at their VMA and ignores the LMA, so crt0's flash→RAM
     copy overwrote `.data` with zeros.
   - Symptoms: `len == 0`, no output, and a clean exit.
   - Diagnosis: break at the first use of a global and compare its value with the `PhysAddr`/`VirtAddr`
     columns of `readelf -l`.
3. **`mpact_run/mpact_stdio.c`.** It routes stdout through semihost `SYS_WRITE` (fd 1), because MPACT
   doesn't implement `SYS_WRITEC`/`SYS_WRITE0`, which picolibc uses by default. Note that MPACT's
   `SYS_WRITE` returns bytes *written*, whereas ARM semihosting specifies bytes *not* written.
4. **Strip `.attribute arch` lines.** binutils 2.38 rejects them in Leo's GCC 14 assembly. They are
   metadata only.

The prebuilt `dp_vliw.elf` in the benchmarks repo contains **no bundle hints**; it is a scalar build.
Don't use it to test the VLIW sim.

## Tracing patch (for the trade study)
[`sim/rvr-starbug-trace.patch`](../sim/rvr-starbug-trace.patch) applies to `ea203a0`, touching
`riscv/riscv_top.cc` and `riscv/rv32g_sim.cc`. [`mpact_run/trace_patch.py`](../mpact_run/trace_patch.py)
applies the same edits idempotently.

- **Trace output.** With `STARBUG_TRACE=<file>`, `rv32g_sim` writes `p<pc>` for each retired instruction
  and `l<addr> <bytes>` / `s<addr> <bytes>` for each data access.
- **UART stub.** It stubs the 16550 UART: LSR reads THRE, and bytes written to THR go to stdout. Without
  it, cvw's polled-UART printf spins forever on MPACT's flat memory. With it, cvw's bare-metal ELFs
  (`crt.S`, `syscalls.c`, `test.ld`) run unmodified.
- **Sanity check.** Over the timed region of scalar FIR, MPACT's instruction count (232,722) was within
  1% of Leo's RTL cycle count (233,653). Wally runs that code at about 1 IPC.

## Building the cvw Embench DSP suite with Ubuntu GCC 10
These notes are encoded in `mpact_run/build_embench_dsp.sh`.
- Use `ARCH=rv32imafc`. GCC 10 has no `_zicsr` suffix; zicsr is implied.
- Take headers from picolibc (`-isystem /usr/lib/picolibc/riscv64-unknown-elf/include`) and link
  `-lm -lc`. picolibc keeps libm inside libc.a.
- **Never use plain `-lgcc`.** There's no rv32imafc/ilp32 multilib, so GCC silently picks the rv64
  libgcc, which fails with "ELFCLASS64 incompatible". Link the rv32imac/ilp32 `libgcc.a` explicitly; it
  also has the soft-float helpers picolibc's libm needs.

## Results: dot product, 2026-10-02
Cycles are kernel-only (`csrr mcycle` around the call). Every output matches the scalar run.

| len | scalar -O0 | scalar -O2 | `dp_vliw_better.S`, VLIW sim | same ELF, scalar sim | `dp_vliw.S`, VLIW sim |
|---|---|---|---|---|---|
| 256 | 15,680 | 2,322 | **562** | 1,858 | 751 |
| 1024 | 62,528 | 9,234 | **2,098** | 7,282 | 2,863 |
| 4096 | 249,920 | 36,882 | **8,242** | 28,978 | 11,311 |

**These are not achievable on the RTL.** `dp_vliw_better.S` puts memory ops in worker lanes. See
[`03-rtl-analysis.md`](03-rtl-analysis.md).
