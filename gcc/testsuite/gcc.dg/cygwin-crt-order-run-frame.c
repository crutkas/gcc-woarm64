/* { dg-skip-if "part" { *-*-* } } */

#include <stdlib.h>

extern volatile int startup_state;
extern int register_cleanup (int *);

static int key = 1;

static void
frame_ctor (void) __attribute__ ((constructor));

static void
frame_ctor (void)
{
  if (startup_state != 0)
    abort ();
  startup_state = key;
  if (register_cleanup (&key))
    abort ();
}
