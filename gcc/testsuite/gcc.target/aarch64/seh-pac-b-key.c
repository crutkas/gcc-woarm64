/* { dg-do compile { target aarch64*-*-mingw* } } */
/* { dg-options "-O1 -mbranch-protection=pac-ret+b-key" } */

extern int consume_one (int);

__attribute__ ((noinline))
int
seh_pac_b_key (int x)
{
  return consume_one (x) + 1;
}

/* { dg-final { scan-assembler-times "hint\t27 // pacibsp" 1 } } */
/* { dg-final { scan-assembler-times "hint\t31 // autibsp" 1 } } */
/* { dg-final { scan-assembler-times "\\.seh_pac_sign_lr" 2 } } */
