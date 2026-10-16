// Integer DSP kernels missing from Embench: int8 depthwise 3x3 convolution (TinyML style) and a
// Q15 FIR. Each is checked against a straightforward reference computed outside the timed region.
#include <stdint.h>
#include <stdio.h>

void start_trigger(void);
void stop_trigger(void);
int get_ccnt(void);

#define H 18
#define W 18
#define C 16
#define OH (H - 2)
#define OW (W - 2)

static int8_t in_hwc[H * W * C];
static int8_t wt[9 * C];
static int32_t bias[C];
static int32_t out_dw[OH * OW * C], ref_dw[OH * OW * C];

#define NTAPS 32
#define NSAMP 512
static int16_t x_q15[NSAMP + NTAPS], h_q15[NTAPS];
static int16_t y_q15[NSAMP], ref_q15[NSAMP];

static uint32_t lfsr = 0x1234567u;
static int32_t rnd(void) {
  lfsr ^= lfsr << 13; lfsr ^= lfsr >> 17; lfsr ^= lfsr << 5;
  return (int32_t)lfsr;
}

// Depthwise 3x3, stride 1, HWC layout: inner loop over channels, as CMSIS-NN does.
void __attribute__((noinline)) dwconv3x3(const int8_t* restrict in, const int8_t* restrict w,
                                         const int32_t* restrict b, int32_t* restrict out) {
  for (int y = 0; y < OH; y++)
    for (int x = 0; x < OW; x++)
      for (int c = 0; c < C; c++) {
        int32_t acc = b[c];
        for (int ky = 0; ky < 3; ky++)
          for (int kx = 0; kx < 3; kx++)
            acc += (int32_t)in[((y + ky) * W + (x + kx)) * C + c] * w[(ky * 3 + kx) * C + c];
        out[(y * OW + x) * C + c] = acc;
      }
}

void __attribute__((noinline)) fir_q15(const int16_t* restrict x, const int16_t* restrict h,
                                       int16_t* restrict y, int n) {
  for (int i = 0; i < n; i++) {
    int32_t acc = 0;
    for (int k = 0; k < NTAPS; k++) acc += (int32_t)x[i + k] * h[k];
    acc >>= 15;
    y[i] = acc > 32767 ? 32767 : (acc < -32768 ? -32768 : acc);
  }
}

static void ref(void) {
  for (int y = 0; y < OH; y++)
    for (int x = 0; x < OW; x++)
      for (int c = 0; c < C; c++) {
        volatile int32_t acc = bias[c];
        for (int k = 0; k < 9; k++)
          acc += (int32_t)in_hwc[((y + k / 3) * W + (x + k % 3)) * C + c] * wt[k * C + c];
        ref_dw[(y * OW + x) * C + c] = acc;
      }
  for (int i = 0; i < NSAMP; i++) {
    volatile int32_t acc = 0;
    for (int k = 0; k < NTAPS; k++) acc += (int32_t)x_q15[i + k] * h_q15[k];
    acc >>= 15;
    ref_q15[i] = acc > 32767 ? 32767 : (acc < -32768 ? -32768 : acc);
  }
}

int main(void) {
  for (int i = 0; i < H * W * C; i++) in_hwc[i] = (int8_t)rnd();
  for (int i = 0; i < 9 * C; i++) wt[i] = (int8_t)rnd();
  for (int i = 0; i < C; i++) bias[i] = rnd() >> 20;
  for (int i = 0; i < NSAMP + NTAPS; i++) x_q15[i] = (int16_t)(rnd() >> 4);
  for (int i = 0; i < NTAPS; i++) h_q15[i] = (int16_t)(rnd() >> 6);
  ref();

  start_trigger();
#ifdef KERNEL_FIR
  fir_q15(x_q15, h_q15, y_q15, NSAMP);
#else
  dwconv3x3(in_hwc, wt, bias, out_dw);
#endif
  stop_trigger();

  int bad = 0;
#ifdef KERNEL_FIR
  for (int i = 0; i < NSAMP; i++) bad += y_q15[i] != ref_q15[i];
#else
  for (int i = 0; i < OH * OW * C; i++) bad += out_dw[i] != ref_dw[i];
#endif
  printf("%s CCNT = %d\n", bad ? "TEST FAIL" : "TEST PASS", get_ccnt());
  return bad;
}
