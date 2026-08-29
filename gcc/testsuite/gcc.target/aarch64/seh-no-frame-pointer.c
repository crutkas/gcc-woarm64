/* { dg-do compile { target aarch64*-*-mingw* } } */
/* { dg-options "-O2 -fomit-frame-pointer" } */

extern int consume_one (int);

__attribute__ ((noinline))
int
seh_frameless (int x)
{
  return consume_one (x) + 1;
}

/* { dg-final { scan-assembler-times "\\.seh_save_reg_x\tx30, 16" 2 } } */
/* { dg-final { scan-assembler-not "mov\tx29, sp" } } */
