/* { dg-do run { target { { aarch64*-*-cygwin* aarch64*-*-msys* } && native } } } */
/* { dg-options "-std=gnu11" } */

#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <stdlib.h>

extern int __cxa_atexit (void (*) (void *), void *, void *);

static int order;

static void
cleanup (void *arg)
{
  order = order * 10 + *(int *) arg;
}

static void
verify (void)
{
  if (order != 21)
    abort ();
}

int
main (void)
{
  SYSTEM_INFO system_info;
  static int dso;
  static int one = 1;
  static int two = 2;

  GetNativeSystemInfo (&system_info);
  if (system_info.wProcessorArchitecture != PROCESSOR_ARCHITECTURE_ARM64)
    abort ();

  if (atexit (verify)
      || __cxa_atexit (cleanup, &one, &dso)
      || __cxa_atexit (cleanup, &two, &dso))
    return 1;

  return 0;
}
