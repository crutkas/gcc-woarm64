/* { dg-do run { target aarch64*-*-mingw* } } */
/* { dg-options "-O1 -fno-omit-frame-pointer" } */

#include <setjmp.h>

static jmp_buf env;

__attribute__ ((noinline, noreturn))
static void
jump_back (void)
{
  longjmp (env, 1);
}

int
main (void)
{
  if (setjmp (env) == 0)
    jump_back ();
  return 0;
}
