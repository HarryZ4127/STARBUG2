#!/bin/bash
SB="${STARBUG_HOME:-$HOME/starbug}"   # WSL/Linux work dir (rvr-starbug, ELFs, traces)
# Build the team's Embench DSP suite (cvw lm/fp examples/C/embench_starbug) for MPACT tracing.
# Copies the sources into WSL home (fast FS), then builds scalar and hand-VLIW Wally ELFs.
set -u
SRC="${CVW_LMFP:-$SB/cvw-lmfp}/examples/C"   # checkout of rvru/cvw lm/fp
DST=$SB/ex
mkdir -p "$DST"
rsync -a --delete --exclude '*.elf' --exclude '.build-*' "$SRC/common" "$SRC/embench_starbug" "$DST/"
cd "$DST/embench_starbug"
# GCC 14 output carries ISA-version attributes that binutils 2.38 rejects (metadata only).
find common/vliw_asm -name '*.S' -exec sed -i '/^\s*\.attribute\s\+arch/d' {} +
# GCC 10 predates the _zicsr suffix (zicsr is implied in its ISA version).
# Ubuntu's bare-metal GCC has no libc headers: borrow picolibc's headers, and its libc for the
# math functions (picolibc keeps libm inside libc.a). Project objects come first, so only the
# missing math symbols are pulled from it.
PICO=/usr/lib/picolibc/riscv64-unknown-elf
# GCC 10 has no rv32imafc/ilp32 multilib, so plain -lgcc would pick the rv64 one. The rv32imac
# libgcc has the integer helpers plus the soft-float ones picolibc's ilp32 libm needs.
# (Only snr.c's log10f is soft-float, after the timed region.)
SOFTGCC=$(riscv64-unknown-elf-gcc -march=rv32imac -mabi=ilp32 -print-libgcc-file-name)
rm -f */*.elf
: > build.log
for mk in */Makefile; do
  d=$(dirname "$mk")
  [ "$d" = common ] && continue
  make -k -C "$d" scalar-wally vliw-wally ARCH=rv32imafc \
    BENCH_CPPFLAGS="-isystem $PICO/include" \
    WALLY_LIBS="-L$PICO/lib/rv32imac/ilp32 -lm -lc $SOFTGCC" >> build.log 2>&1
  # Unrolled scalar build: the flags Leo used to seed the hand-VLIW assembly, plus variable
  # expansion (several accumulators per reduction, as a VLIW compiler would). This is the input
  # the VLIW scheduling model works from.
  make -k -C "$d" scalar-wally ARCH=rv32imafc SCALAR_WALLY_ELF="${d}_unroll_wally.elf" \
    BENCH_CPPFLAGS="-isystem $PICO/include -funroll-loops -funroll-all-loops -fpeel-loops -ftracer -frename-registers -fweb -fomit-frame-pointer -fvariable-expansion-in-unroller --param=max-variable-expansions-in-unroller=4" \
    WALLY_LIBS="-L$PICO/lib/rv32imac/ilp32 -lm -lc $SOFTGCC" >> build.log 2>&1
done
grep -iE "error|undefined" build.log | sort | uniq -c | head
ls */*_wally.elf | wc -l
