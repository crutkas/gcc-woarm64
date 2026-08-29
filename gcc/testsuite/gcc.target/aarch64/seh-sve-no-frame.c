/* { dg-do compile { target aarch64*-*-mingw* } } */
/* { dg-options "-O2 -march=armv8.2-a+sve" } */

int
seh_sve_no_frame (int x)
{
  asm volatile ("ptrue p0.b" : : : "p0");
  return x + 1;
}

/* { dg-final { scan-assembler "ptrue p0\\.b" } } */
