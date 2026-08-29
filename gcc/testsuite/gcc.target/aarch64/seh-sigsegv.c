/* { dg-do compile { target aarch64*-*-mingw* } } */
/* { dg-options "-O1 -fno-omit-frame-pointer" } */

#include <setjmp.h>
#include <signal.h>

static jmp_buf env;
static volatile sig_atomic_t caught;

static void
handle_sigsegv (int sig)
{
  caught = sig;
  longjmp (env, 1);
}

__attribute__ ((noinline))
static void
keep_frame (volatile void *ptr)
{
  asm volatile ("" : : "r" (ptr) : "memory");
}

__attribute__ ((noinline))
static void
fault_from_frame (void)
{
  volatile char frame[256];
  void *zero = 0;

  frame[0] = 1;
  keep_frame (frame);
  asm volatile ("ldr wzr, [%0]" : : "r" (zero) : "memory");
  __builtin_abort ();
}

int
main (void)
{
  if (signal (SIGSEGV, handle_sigsegv) == SIG_ERR)
    return 1;
  if (setjmp (env) == 0)
    fault_from_frame ();
  return caught != SIGSEGV;
}

/* { dg-final { scan-assembler "fault_from_frame:" } } */
/* { dg-final { scan-assembler "\\.seh_save_fplr_x\t272" } } */
/* { dg-final { scan-assembler "ldr wzr, \\[x\[0-9\]+\\]" } } */
