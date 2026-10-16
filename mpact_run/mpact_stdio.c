// stdio for picolibc on MPACT: route stdout/stderr through semihost SYS_WRITE,
// since MPACT's ARM semihosting does not implement SYS_WRITEC (picolibc's default).
#include <stdio.h>
#include <semihost.h>

static int mpact_putc(char c, FILE *f) {
  (void)f;
  sys_semihost_write(1, &c, 1);  // MPACT returns bytes written, not bytes left; ignore it
  return (unsigned char)c;
}

static FILE mpact_stdio = FDEV_SETUP_STREAM(mpact_putc, NULL, NULL, _FDEV_SETUP_WRITE);
FILE *const stdin = &mpact_stdio;
FILE *const stdout = &mpact_stdio;
FILE *const stderr = &mpact_stdio;
