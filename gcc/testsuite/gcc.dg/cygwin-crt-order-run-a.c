/* { dg-skip-if "part" { *-*-* } } */

#include <stdlib.h>

extern volatile int startup_state;
extern int register_cleanup (int *);

static int key = 2;

static void
user_ctor_a (void) __attribute__ ((constructor));

static void
user_ctor_a (void)
{
  if ((startup_state & 1) == 0)
    abort ();
  startup_state |= key;
  if (register_cleanup (&key))
    abort ();
}
