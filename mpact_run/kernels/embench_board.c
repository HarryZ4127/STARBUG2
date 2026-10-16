// Embench-IoT board support for the cvw bare-metal runtime: mcycle-based triggers, and a main()
// that runs one benchmark between start_trigger/stop_trigger and reports verify + CCNT.
#include <stdint.h>
#include <stdio.h>
#include "support.h"

static uint32_t t0, t1;
static inline uint32_t rd_mcycle(void) { uint32_t c; __asm__ volatile("csrr %0, mcycle" : "=r"(c)); return c; }

void initialise_board(void) {}
void __attribute__((noinline)) start_trigger(void) { __asm__ volatile("" ::: "memory"); t0 = rd_mcycle(); }
void __attribute__((noinline)) stop_trigger(void) { t1 = rd_mcycle(); __asm__ volatile("" ::: "memory"); }
int get_ccnt(void) { return (int)(t1 - t0); }

int main(void) {
  initialise_board();
  initialise_benchmark();
  warm_caches(1);
  start_trigger();
  volatile int result = benchmark();
  stop_trigger();
  int ok = verify_benchmark(result);
  printf("%s CCNT = %d\n", ok ? "TEST PASS" : "TEST FAIL", get_ccnt());
  return !ok;
}
