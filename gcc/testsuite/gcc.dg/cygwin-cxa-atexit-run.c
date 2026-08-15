/* { dg-do run { target { { *-*-cygwin* *-*-msys* } && native } } } */
/* { dg-options "-std=gnu11" } */

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
  static int dso;
  static int one = 1;
  static int two = 2;

  if (atexit (verify)
      || __cxa_atexit (cleanup, &one, &dso)
      || __cxa_atexit (cleanup, &two, &dso))
    return 1;

  return 0;
}
