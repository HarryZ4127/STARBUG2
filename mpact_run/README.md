# mpact_run: build, simulate and model scripts

Every script reads its locations from the environment:

| Variable | Default | What it points to |
|---|---|---|
| `STARBUG_HOME` | `~/starbug` | Work dir: `rvr-starbug/` (MPACT checkout), `ex/` (ELFs), `traces/`, `embench-iot/`, `dp_build/`, model JSON output |
| `CVW_LMFP` | `$STARBUG_HOME/cvw-lmfp` | Checkout of rvru/cvw `lm/fp`: Embench DSP sources and Leo's RTL cycle CSV |

Results the report reads are written into `./results/`. Raw model output is written to
`$STARBUG_HOME/*.json`; copies from the 2026-10-13 run are in `results/raw/`.

## Scripts

### dp benchmark: MPACT speedup study, 2026-10-02
| Script | Purpose |
|---|---|
| `build_dp.sh [len]` | Builds the dp ELFs (scalar, empty, vliw, vliw_better) with picolibc, semihosted, using the LMA==VMA linker script |
| `run_dp.sh` | Runs them on `rv32g_sim` / `rv32_vliw_sim` and checks outputs against scalar |
| `sweep.sh [lens…]` | Kernel-only `mcycle` per length for -O0, -O2, vliw_better (VLIW sim and scalar sim) and vliw, with output checks |
| `dp_main_timed.c` | dp driver that reads `mcycle`/`minstret` around the kernel only |
| `mpact_stdio.c` | picolibc stdout through semihost `SYS_WRITE`, which MPACT supports, instead of `SYS_WRITEC` |
| `bundle_check.py <elf> [--quiet]` | Lists `c.li zero,N` bundles. Flags intra-bundle RAW/WAW (x and f registers), width > 4, memory/branch/CSR/`auipc` outside slot 0, and more than one memory op. Exits 1 if anything is flagged |

### ISA trade study, 2026-10-13
Run in this order:

| Step | Script | Output |
|---|---|---|
| 1 | `trace_patch.py [rvr-starbug dir]` | Applies the tracing + UART-stub patch (same as `../sim/rvr-starbug-trace.patch`); then `bazel build -c opt //riscv:rv32g_sim` |
| 2 | `build_embench_dsp.sh` | Copies `$CVW_LMFP/examples/C/{common,embench_starbug}` to `$STARBUG_HOME/ex` and builds scalar, VLIW and unrolled ELFs (GCC 10, rv32imafc, picolibc headers/libc) |
| 3 | `build_extra.sh` | Embench-IoT crc32/nettle-sha256/matmult-int/edn and `kernels/dsp_int.c` (dwconv int8, Q15 FIR), scalar and unrolled. Needs `$STARBUG_HOME/embench-iot` (embench/embench-iot @ `54fd9a0`) |
| 4 | `run_traces.sh` | Traces all 42 ELFs, 8 in parallel, into `$STARBUG_HOME/traces/` (215 MB) and writes `results.tsv` |
| 5 | `validate.py` | Model vs Leo's Questa RTL counts → `validate.json` |
| 6 | `sweep_isa.py` | Every config in `vliw_model.CONFIGS` × every kernel → `isa_sweep.json` |
| 7 | `width_vs_ports.py` | 4/8/16 lanes × 1/2/4 memory ops → `width_vs_ports.json` |
| 8 | `unroll_sensitivity.py` | Same options on the plain `-O3` build → `unroll_sensitivity.json` |
| 9 | `summarize.py` | → `results/isa_speedup_vs_sb1.csv`, `results/validation.csv`, `results/digest.json` |
| 10 | `gen_report.py` | → `results/starbug_isa_study.html` |

`vliw_model.py` is the trace-driven timing model; the docstrings explain each part. `kernels/` holds
the two new kernels and the Embench board glue.

### Design-space exploration, 2026-10-16
`vliw_model.make_cfg()` gained knobs for every structural choice under discussion. They all default
to "unlimited / as before", so steps 5–10 above reproduce the 2026-10-13 results byte for byte.
- **Latencies:** per-class overrides.
- **Shared units and ports:** mul/div and FP units per bundle; register-file read/write ports.
- **Memory:** 2/4/8 banks, word-interleaved or XOR-hashed.
- **Caches:** I$/D$ size and miss penalty.
- **Front end:** fetch bandwidth with a fetch queue; how a line-straddling bundle is handled
  (free, +1 cycle, or issued scalar) under the 16-bit hint or 32-bit header.
- **Predication:** creg/z if-conversion of short hammocks.
- **Scheduling:** a greedy packetizer like the LLVM one, and software pipelining (modulo-schedule
  lower bound).

| Script | Output |
|---|---|
| `dse.py factorial` | 224 configs: width {2,4,6,8} × memory {1LS, 1LS+1LO, 2LS, 2LS+2LO} × banks {ideal,2,4} × branch {none,BTFN,gshare} × ZOL, on unrolled and `-O3` code → `$STARBUG_HOME/dse_factorial.json` |
| `dse.py sensitivity` | 53 one-knob changes around SB1 and around the SB2 candidate → `dse_sensitivity.json` |
| `dse.py compiler` | scheduler (packetizer / list / +modulo) × alias info × code variant × 3 machines → `dse_compiler.json` |
| `dse_summary.py` | → `results/dse/*.csv` and `results/dse/SUMMARY.md` |

`python3 dse.py all && python3 dse_summary.py` takes about 15 minutes on 4 cores.

### Tests
```bash
python3 -m unittest discover -s tests -v     # 31 tests; the regression test needs $STARBUG_HOME/traces
```
The tests cover every knob on small hand-built programs. The regression test checks that the
extended model still reproduces `results/raw/isa_sweep.json` exactly.

## Shortcut: skip steps 1–4
```bash
mkdir -p ~/starbug && cd ~/starbug
tar xJf <repo>/mpact_run/results/elfs.tar.xz       # ex/embench_starbug/*/…_{scalar,vliw,unroll}_wally.elf, ex/extra/*.elf
tar xJf <repo>/mpact_run/results/traces.tar.xz     # traces/*.trace, *.out, *.err, results.tsv
```
Then run steps 5–10. Only `riscv64-unknown-elf-objdump` and python3 (stdlib) are needed. Step 5 also
needs `$CVW_LMFP` for the RTL CSV.

## Full rebuild prerequisites (Ubuntu 22.04)
```bash
sudo apt install clang g++ default-jre-headless gcc-riscv64-unknown-elf picolibc-riscv64-unknown-elf \
                 binutils-riscv64-unknown-elf rsync python3
# bazelisk as ~/bin/bazel (fetches Bazel 7.1.2)
git clone https://github.com/rvru/rvr-starbug ~/starbug/rvr-starbug && (cd ~/starbug/rvr-starbug && git checkout ea203a0 && git submodule update --init)
git clone -b lm/fp https://github.com/rvru/cvw ~/starbug/cvw-lmfp
git clone https://github.com/embench/embench-iot ~/starbug/embench-iot && (cd ~/starbug/embench-iot && git checkout 54fd9a0)
```
Toolchain notes, such as why plain `-lgcc` must never be used, are in [`../docs/02-mpact-simulator.md`](../docs/02-mpact-simulator.md).

## results/
| File | Content |
|---|---|
| `isa_speedup_vs_sb1.csv` | Per-kernel speedup of each config over SB1 |
| `validation.csv` | Model vs RTL cycles, per kernel and run |
| `digest.json` | Everything the report uses: geomeans, SB1 anatomy, bank conflicts, header cost, predication |
| `starbug_isa_study.html` | Rendered report |
| `raw/*.json`, `raw/trace_results.tsv` | Raw output of steps 4–8 from the 2026-10-13 run |
| `mpact_counters/*.proto` | MPACT `--output_dir` counter exports for each traced ELF |
| `elfs.tar.xz`, `traces.tar.xz` | Inputs for steps 5–8 (see shortcut above) |

**Third-party code in the ELFs.** The ELFs include code compiled from cvw `lm/fp` (`examples/C`) and
Embench-IoT (GPL-3.0). Their sources are at the commits above, and the build scripts here reproduce
the binaries.
