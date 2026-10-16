# sim/: simulator patch and linker script

| File | What | Applies to |
|---|---|---|
| `rvr-starbug-trace.patch` | Adds `STARBUG_TRACE=<file>` tracing to `rv32g_sim`: `p<pc>` per retired instruction, `l/s<addr> <bytes>` per data access. Also stubs the 16550 UART (LSR reads THRE; THR bytes go to stdout) so cvw bare-metal ELFs run. Touches `riscv/riscv_top.cc` and `riscv/rv32g_sim.cc` | [rvru/rvr-starbug](https://github.com/rvru/rvr-starbug) @ `ea203a0` |
| `picolibc-mpact.ld` | picolibc's default `picolibc.ld` with `.data`/`.tdata` loaded in place (`AT>ram` instead of `AT>flash`). MPACT's ELF loader ignores LMA, so the stock script lets crt0 zero `.data`. BSD-3-Clause, © 2019 Keith Packard (header kept) | picolibc on Ubuntu 22.04 |

Apply the patch:
```bash
cd ~/starbug/rvr-starbug && git checkout ea203a0 && git apply <repo>/sim/rvr-starbug-trace.patch
bazel build -c opt //riscv:rv32g_sim
```
Alternatively, `python3 mpact_run/trace_patch.py ~/starbug/rvr-starbug` makes the same edits and is
safe to re-run.

Background is in [`../docs/02-mpact-simulator.md`](../docs/02-mpact-simulator.md).
