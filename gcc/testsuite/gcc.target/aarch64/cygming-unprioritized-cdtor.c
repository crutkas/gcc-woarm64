/* { dg-do compile { target { *-*-cygwin* *-*-msys* *-*-mingw* } } } */
/* { dg-options "-O2" } */

extern void register_frame (void);
extern void deregister_frame (void);

static void
frame_ctor (void) __attribute__ ((constructor));

static void
frame_ctor (void)
{
  register_frame ();
}

static void
frame_dtor (void) __attribute__ ((destructor));

static void
frame_dtor (void)
{
  deregister_frame ();
}

/* { dg-final { scan-assembler-times "\\.section\t\\.ctors, \"aw\"" 1 } } */
/* { dg-final { scan-assembler-times "\\.section\t\\.dtors, \"aw\"" 1 } } */
/* { dg-final { scan-assembler-not "\\.init_array" } } */
/* { dg-final { scan-assembler-not "\\.fini_array" } } */
