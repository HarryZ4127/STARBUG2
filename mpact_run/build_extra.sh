#!/bin/bash
SB="${STARBUG_HOME:-$HOME/starbug}"   # WSL/Linux work dir (rvr-starbug, ELFs, traces)
# Build the extra integer kernels (Embench-IoT crc32/nettle-sha256/matmult-int/edn + dsp_int.c)
# against the cvw bare-metal runtime, in scalar (-O3) and unrolled (-O3 + Leo's VLIW-seed flags) forms.
# Run build_embench_dsp.sh first: it copies cvw's examples/C/common to $SB/ex/common.
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
EX=$SB/ex
CVWC=$EX/common
EIOT=$SB/embench-iot
OUT=$EX/extra
mkdir -p "$OUT/kernels"
cp "$HERE"/kernels/*.c "$OUT/kernels/"
PICO=/usr/lib/picolibc/riscv64-unknown-elf
SOFTGCC=$(riscv64-unknown-elf-gcc -march=rv32imac -mabi=ilp32 -print-libgcc-file-name)
BASE=(-O3 -march=rv32imafc -mabi=ilp32 -mcmodel=medany -nostdlib -static -std=gnu17
      -fno-tree-loop-distribute-patterns -ffunction-sections -fdata-sections -Wall
      -Wno-unused-function -Wno-format -isystem $PICO/include -I$EIOT/support -I$CVWC)
UNROLL=(-funroll-loops -funroll-all-loops -fpeel-loops -ftracer -frename-registers -fweb -fomit-frame-pointer
        -fvariable-expansion-in-unroller --param=max-variable-expansions-in-unroller=4)
LINK=(-T$CVWC/test.ld -Wl,-gc-sections $CVWC/crt.S $CVWC/syscalls.c -L$PICO/lib/rv32imac/ilp32 -lc $SOFTGCC)

build() {  # name defs srcs...
  local name=$1 defs=$2; shift 2
  riscv64-unknown-elf-gcc "${BASE[@]}" $defs "$@" "${LINK[@]}" -o "$OUT/${name}_scalar.elf" 2>&1 | grep -E "error|undefined" | head -3
  riscv64-unknown-elf-gcc "${BASE[@]}" "${UNROLL[@]}" $defs "$@" "${LINK[@]}" -o "$OUT/${name}_unroll.elf" 2>&1 | grep -E "error|undefined" | head -3
}
# Embench-IoT: CPU_MHZ=1 and a small scale factor keep traces small; verify_benchmark checks the
# result of the last repetition, so fewer repetitions don't change what is verified.
for b in crc32:crc_32.c:4 nettle-sha256:nettle-sha256.c:4 matmult-int:matmult-int.c:2 edn:libedn.c:2; do
  IFS=: read -r name src lsf <<< "$b"
  mkdir -p "$OUT/src/$name"
  sed "s/#define LOCAL_SCALE_FACTOR .*/#define LOCAL_SCALE_FACTOR $lsf/" "$EIOT/src/$name/$src" > "$OUT/src/$name/$src"
  build "$name" "-DCPU_MHZ=1" "$OUT/src/$name/$src" "$EIOT/support/beebsc.c" "$OUT/kernels/embench_board.c"
done
BS=$EX/embench_starbug/common/boardsupport.c
build dwconv3x3_i8 "" "$OUT/kernels/dsp_int.c" "$BS"
build fir_q15 "-DKERNEL_FIR" "$OUT/kernels/dsp_int.c" "$BS"
ls "$OUT"/*.elf | wc -l
