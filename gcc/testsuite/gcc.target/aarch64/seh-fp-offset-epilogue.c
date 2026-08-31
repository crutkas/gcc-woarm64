/* { dg-do compile { target aarch64*-*-mingw* } } */
/* { dg-options "-O2 -fno-omit-frame-pointer" } */

extern void escape (void *);
extern void escape10 (void *, int, int, int, int, int, int, int, int, int);

__attribute__ ((noinline))
void
seh_fp_offset_epilogue (unsigned long long size)
{
  volatile unsigned char fixed[512];
  void *dynamic = __builtin_alloca (size);

  fixed[0] = 1;
  escape ((void *) fixed);
  escape (dynamic);
  escape10 (dynamic, 1, 2, 3, 4, 5, 6, 7, 8, 9);
}

/* The epilogue operation SP = FP - 16 reverses the prologue's
   FP = SP + 16 operation, so both use the same unwind directive.  */
/* { dg-final { scan-assembler "sub\tsp, x29, #16\n\t\\.seh_add_fp\t16(?:\n|$)" } } */
/* { dg-final { scan-assembler-times "\\.seh_add_fp\t16(?:\n|$)" 2 } } */
