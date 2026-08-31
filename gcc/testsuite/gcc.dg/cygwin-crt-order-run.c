/* { dg-do run { target { { aarch64*-*-cygwin* aarch64*-*-msys* } && native } } } */
/* { dg-require-effective-target lto } */
/* { dg-additional-sources "cygwin-crt-order-run-a.c cygwin-crt-order-run-b.c cygwin-crt-order-run-frame.c" } */
/* { dg-options "-O2 -flto -ffunction-sections -fdata-sections -Wl,--gc-sections,-Map=cygwin-crt-order.map" } */

#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <stdlib.h>

extern int __cxa_atexit (void (*) (void *), void *, void *);
extern void *__dso_handle;

volatile int startup_state;
static volatile int cleanup_state;

static void
cleanup (void *arg)
{
  int key = *(int *) arg;

  if (key == 1)
    {
      if (startup_state != 7 || cleanup_state != 6)
	abort ();
    }
  else
    cleanup_state |= key;
}

int
register_cleanup (int *key)
{
  return __cxa_atexit (cleanup, key, (void *) &__dso_handle);
}

int
main (void)
{
  SYSTEM_INFO system_info;

  GetNativeSystemInfo (&system_info);
  if (system_info.wProcessorArchitecture != PROCESSOR_ARCHITECTURE_ARM64)
    abort ();

  if (startup_state != 7)
    abort ();

  return 0;
}

/* crtbegin precedes the LTO output and crtend follows it.  */
/* { dg-final { scan-file "cygwin-crt-order.map" "LOAD .*crtbegin\\.o(.|\n)*LOAD .*crtend\\.o" } } */
/* The crtend constructor entry follows all user constructor entries.  */
/* { dg-final { scan-file "cygwin-crt-order.map" "\\.ctors(.|\n)*ltrans.*\\.o(.|\n)*crtend\\.o" } } */
