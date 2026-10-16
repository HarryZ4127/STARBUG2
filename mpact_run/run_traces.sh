#!/bin/bash
SB="${STARBUG_HOME:-$HOME/starbug}"   # WSL/Linux work dir (rvr-starbug, ELFs, traces)
# Run every benchmark ELF on the traced MPACT rv32g_sim (see trace_patch.py), 8 at a time.
# Output: $SB/traces/<elf>.trace and $SB/traces/results.tsv (elf, pass, CCNT).
set -u
SIM=$SB/rvr-starbug/bazel-bin/riscv/rv32g_sim
T=$SB/traces
mkdir -p "$T"
one() {
  elf=$1; name=$(basename "$elf" .elf)
  STARBUG_TRACE="$T/$name.trace" timeout 900 "$SIM" --exit_on_tohost --quiet "$elf" > "$T/$name.out" 2> "$T/$name.err"
  res=$(grep -oE "TEST (PASS|FAIL)" "$T/$name.out" | head -1)
  ccnt=$(grep -oE "CCNT = [0-9]+" "$T/$name.out" | grep -oE "[0-9]+$")
  printf "%s\t%s\t%s\n" "$name" "${res:-NONE}" "${ccnt:-?}"
}
export -f one; export SIM T
ls $SB/ex/embench_starbug/*/*_wally.elf $SB/ex/extra/*.elf | xargs -P 8 -I{} bash -c 'one {}' | sort > "$T/results.tsv"
cat "$T/results.tsv"
du -sh "$T"
