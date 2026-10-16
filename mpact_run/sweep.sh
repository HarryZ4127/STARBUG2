#!/bin/bash
SB="${STARBUG_HOME:-$HOME/starbug}"   # WSL/Linux work dir (rvr-starbug, ELFs, traces)
# Sweep vector length; build each variant with the timed driver and report kernel-only mcycle.
S=$(cd "$(dirname "$0")" && pwd)
B=$SB/rvr-starbug/bazel-bin/riscv
cd $SB/dp_build && cp "$S/dp_main_timed.c" "$S/mpact_stdio.c" .
cp ../rvr-starbug/benchmarks/dp/{dp.h,dp_scalar.c,dp_empty.c,dp_vliw.S,dp_vliw_better.S,gen_data.py} .
sed -i '/^\s*\.attribute\s\+arch/d' dp_vliw.S dp_vliw_better.S
sed 's/AT>flash :ram_init/AT>ram :ram_init/; s/AT>flash :tls :ram_init/AT>ram :tls :ram_init/' \
  /usr/lib/picolibc/riscv64-unknown-elf/lib/picolibc.ld > mpact.ld
F="-march=rv32imac -mabi=ilp32 -Wall -I. --specs=picolibc.specs --oslib=semihost --crt0=hosted -T mpact.ld \
 -Wl,--defsym=__flash=0x80000000 -Wl,--defsym=__flash_size=0x800000 \
 -Wl,--defsym=__ram=0x80800000 -Wl,--defsym=__ram_size=0x800000 -Wl,--defsym=__stack_size=0x10000"
kc() { grep -o 'kernel_cycles = [0-9]*' "$1" | grep -o '[0-9]*$'; }
printf "%6s | %10s %10s | %12s %12s %12s | %s\n" len scalar-O0 scalar-O2 vliw_better vb_on_g_sim dp_vliw "outputs"
for LEN in ${@:-32 64 128 256 512 1024}; do
  python3 gen_data.py "$LEN" > data.S
  riscv64-unknown-elf-gcc -O0 $F -o t_s0.elf dp_main_timed.c dp_scalar.c data.S mpact_stdio.c
  riscv64-unknown-elf-gcc -O2 $F -o t_s2.elf dp_main_timed.c dp_scalar.c data.S mpact_stdio.c
  riscv64-unknown-elf-gcc -O0 $F -o t_vb.elf dp_main_timed.c dp_vliw_better.S data.S mpact_stdio.c
  riscv64-unknown-elf-gcc -O0 $F -o t_v.elf  dp_main_timed.c dp_vliw.S data.S mpact_stdio.c
  $B/rv32g_sim     --semihost_arm t_s0.elf > o_s0 2>/dev/null
  $B/rv32g_sim     --semihost_arm t_s2.elf > o_s2 2>/dev/null
  $B/rv32_vliw_sim --semihost_arm t_vb.elf > o_vb 2>/dev/null
  $B/rv32g_sim     --semihost_arm t_vb.elf > o_vg 2>/dev/null
  $B/rv32_vliw_sim --semihost_arm t_v.elf  > o_v  2>/dev/null
  ok=""
  for o in o_s2 o_vb o_vg o_v; do
    [ "$(grep -c ^output $o)" = "$LEN" ] && diff -q <(grep ^output o_s0) <(grep ^output $o) >/dev/null && ok="$ok ok" || ok="$ok MISMATCH($o)"
  done
  printf "%6s | %10s %10s | %12s %12s %12s |%s\n" $LEN "$(kc o_s0)" "$(kc o_s2)" "$(kc o_vb)" "$(kc o_vg)" "$(kc o_v)" "$ok"
done
