#!/bin/bash
SB="${STARBUG_HOME:-$HOME/starbug}"   # WSL/Linux work dir (rvr-starbug, ELFs, traces)
# Run the dp ELFs on MPACT: scalar/empty on rv32g_sim, VLIW variants on rv32_vliw_sim.
BIN=$SB/rvr-starbug/bazel-bin/riscv
cd $SB/dp_build
run() {  # sim elf tag
  timeout 120 "$BIN/$1" --semihost_arm "$2" > out_$3.log 2> err_$3.log
  local rc=$?
  local cyc=$(grep -o 'Cycle count = [0-9]*' err_$3.log | grep -o '[0-9]*$')
  printf "%-12s %-14s rc=%-3s cycles=%-8s lines=%s\n" "$3" "$1" "$rc" "${cyc:-?}" "$(grep -c '^output\[' out_$3.log)"
}
run rv32g_sim      dp_scalar.elf      scalar
run rv32g_sim      dp_empty.elf       empty
run rv32_vliw_sim  dp_vliw.elf        vliw
run rv32_vliw_sim  dp_vliw_better.elf vliw_better
run rv32g_sim      dp_vliw_better.elf vliw_better_on_scalar_sim
for t in vliw vliw_better vliw_better_on_scalar_sim; do
  if diff -q out_scalar.log out_$t.log >/dev/null; then echo "$t: output MATCHES scalar"
  else echo "$t: output DIFFERS from scalar ($(diff out_scalar.log out_$t.log | grep -c '^<') lines)"; fi
done
