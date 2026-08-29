/* { dg-do compile { target aarch64*-*-mingw* } } */
/* { dg-options "-O2" } */

extern long consume_pair (long, long);

__attribute__ ((noinline))
long
seh_body_pair (long *values)
{
  register long r19 asm ("x19") = values[0];
  register long r21 asm ("x21") = values[1];

  asm volatile ("" : "+r" (r19), "+r" (r21));
  return consume_pair (r19, r21);
}

/* Body accesses can still fuse even when they use nonconsecutive callee-saved
   registers; only frame saves and restores must remain separate.  */
/* { dg-final { scan-assembler "ldp\tw19, w21, \\[x0\\]" } } */
/* { dg-final { scan-assembler-not "stp\tx19, x21," } } */
