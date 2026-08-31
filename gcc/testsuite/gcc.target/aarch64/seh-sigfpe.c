/* { dg-do run { target aarch64*-*-mingw* } } */
/* { dg-options "-O1 -fno-omit-frame-pointer" } */

#include <setjmp.h>
#include <signal.h>
#include <stdlib.h>

static jmp_buf env;
static volatile sig_atomic_t caught;

static void
handle_sigfpe (int sig)
{
  caught = sig;
  longjmp (env, 1);
}

__attribute__ ((noinline))
static void
raise_from_frame (void)
{
  volatile char frame[256];

  frame[0] = 1;
  asm volatile ("" : : "m" (frame));
  if (raise (SIGFPE) != 0)
    abort ();
  abort ();
}

int
main (void)
{
  if (signal (SIGFPE, handle_sigfpe) == SIG_ERR)
    return 1;
  if (setjmp (env) == 0)
    raise_from_frame ();
  return caught != SIGFPE;
}
