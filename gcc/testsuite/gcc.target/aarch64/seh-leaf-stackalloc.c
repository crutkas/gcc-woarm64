/* { dg-do compile { target aarch64*-*-mingw* } } */
/* { dg-options "-O1 -fomit-frame-pointer" } */

__attribute__ ((noinline))
int
seh_leaf_mid_frame (int x)
{
  volatile char local[50000];

  local[0] = x;
  local[49999] = x + 1;
  asm volatile ("" : : "r" (local));
  return local[0] + local[49999];
}

/* The epilogue reuses the prologue's x12 materialization.  */
/* { dg-final { scan-assembler-times "\\.seh_stackalloc\t50000" 2 } } */
/* { dg-final { scan-assembler-times "mov\tx12, 50000" 1 } } */
/* { dg-final { scan-assembler "add\tsp, sp, x12\n\t\\.seh_stackalloc\t50000" } } */
