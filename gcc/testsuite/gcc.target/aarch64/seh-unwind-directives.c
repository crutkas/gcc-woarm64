/* { dg-do compile { target aarch64*-*-mingw* } } */
/* { dg-options "-O1 -fno-omit-frame-pointer" } */

extern void consume (long, long);
extern long consume_one (long);
extern void consume_pair (long, long);

__attribute__ ((noinline))
long
seh_fplr_x (long x)
{
  return consume_one (x) + 1;
}

__attribute__ ((noinline))
long
seh_frame (long x)
{
  volatile char local[512];
  register long r19 asm ("x19") = x;
  register long r20 asm ("x20") = x + 1;

  local[x & 7] = 1;
  asm volatile ("" : "+r" (r19), "+r" (r20) : "m" (local));
  consume (r19, r20);
  return r19 + r20 + local[0];
}

__attribute__ ((noinline))
long
seh_nonconsecutive (long x)
{
  register long r19 asm ("x19") = x;
  register long r21 asm ("x21") = x + 1;

  asm volatile ("" : "+r" (r19), "+r" (r21));
  consume_pair (r19, r21);
  return r19 + r21;
}

/* { dg-final { scan-assembler-times "\\.seh_stackalloc\t" 2 } } */
/* { dg-final { scan-assembler-times "\\.seh_save_fplr_x\t16" 2 } } */
/* { dg-final { scan-assembler-times "\\.seh_save_fplr\t0" 2 } } */
/* { dg-final { scan-assembler-times "\\.seh_save_regp\tx19, 16" 2 } } */
/* { dg-final { scan-assembler-times "\\.seh_save_reg\tx19," 2 } } */
/* { dg-final { scan-assembler-times "\\.seh_save_reg\tx21," 2 } } */
/* { dg-final { scan-assembler-not "(?:stp|ldp)\tx19, x21," } } */
/* { dg-final { scan-assembler "\\.seh_set_fp" } } */
/* { dg-final { scan-assembler "\\.seh_startepilogue" } } */
/* { dg-final { scan-assembler "\\.seh_endepilogue" } } */
