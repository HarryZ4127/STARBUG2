// dp_main.c plus mcycle/minstret reads around the kernel, so printf cost is excluded.
#include <stdint.h>
#include <stdio.h>
#include "dp.h"

extern uint64_t len;
extern int32_t input_1[];
extern int32_t input_2[];
extern int32_t output[];

static inline uint32_t rd_mcycle(void) { uint32_t v; __asm__ volatile("csrr %0, mcycle" : "=r"(v)); return v; }
static inline uint32_t rd_minstret(void) { uint32_t v; __asm__ volatile("csrr %0, minstret" : "=r"(v)); return v; }

int main() {
    uint32_t c0 = rd_mcycle(), i0 = rd_minstret();
    dot_product(input_1, input_2, output, len);
    uint32_t c1 = rd_mcycle(), i1 = rd_minstret();

    for (uint64_t i = 0; i < len; i++) {
        printf("output[%llu] = %ld\n", i, output[i]);
    }
    printf("kernel_cycles = %lu\nkernel_instret = %lu\n",
           (unsigned long)(c1 - c0), (unsigned long)(i1 - i0));
    return 0;
}
