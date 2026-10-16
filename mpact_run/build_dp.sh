#!/bin/bash
SB="${STARBUG_HOME:-$HOME/starbug}"   # WSL/Linux work dir (rvr-starbug, ELFs, traces)
# Build the dp benchmark ELFs with Ubuntu's riscv64-unknown-elf-gcc + picolibc (semihosted).
set -u
LEN=${1:-256}
mkdir -p $SB/dp_build && cd $SB/dp_build
cp ../rvr-starbug/benchmarks/dp/{dp_main.c,dp.h,dp_scalar.c,dp_empty.c,dp_vliw.S,dp_vliw_better.S,gen_data.py} .
python3 gen_data.py "$LEN" > data.S
# GCC 14 emitted ISA-version attributes that binutils 2.38 rejects; drop them (metadata only).
sed -i '/^\s*\.attribute\s\+arch/d' dp_vliw.S dp_vliw_better.S
# MPACT's ELF loader puts .data at its VMA, so crt0's flash->RAM copy would clobber it with zeros.
# Use picolibc's default script with initialized data loaded in place (LMA == VMA).
sed 's/AT>flash :ram_init/AT>ram :ram_init/; s/AT>flash :tls :ram_init/AT>ram :tls :ram_init/' \
  /usr/lib/picolibc/riscv64-unknown-elf/lib/picolibc.ld > mpact.ld
F="-O0 -march=rv32imac -mabi=ilp32 -Wall --specs=picolibc.specs --oslib=semihost --crt0=hosted -T mpact.ld \
 -Wl,--defsym=__flash=0x80000000 -Wl,--defsym=__flash_size=0x800000 \
 -Wl,--defsym=__ram=0x80800000 -Wl,--defsym=__ram_size=0x800000 -Wl,--defsym=__stack_size=0x10000"
for v in scalar empty vliw_better vliw; do
  src=$(ls dp_$v.c dp_$v.S 2>/dev/null | head -1)
  echo "== $v ($src)"
  riscv64-unknown-elf-gcc $F -o dp_$v.elf dp_main.c "$src" data.S "$(dirname "$0")/mpact_stdio.c" 2>&1 | head -8
done
ls -la *.elf
