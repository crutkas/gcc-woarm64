/* { dg-do compile { target { *-*-cygwin* *-*-msys* } } } */
/* { dg-options "-O2 -std=gnu11 -Werror=implicit-function-declaration -Werror=incompatible-pointer-types" } */

typedef void (*cxa_callback_type) (void *);
typedef int (*cxa_atexit_type) (cxa_callback_type, void *, void *);

extern int __cxa_atexit (void (*) (void *), void *, void *);
extern void *__dso_handle;

_Static_assert (__builtin_types_compatible_p
		(__typeof__ (&__cxa_atexit), cxa_atexit_type),
		"__cxa_atexit declaration");

/* Keep the registration when the callback is empty, as it is for SEH.  */
static void __attribute__((noipa))
cleanup (void *arg)
{
  (void) arg;
}

int
register_cleanup (void)
{
  return __cxa_atexit (cleanup, 0, (void *) &__dso_handle);
}

/* { dg-final { scan-assembler "__cxa_atexit" } } */
/* { dg-final { scan-assembler "__dso_handle" } } */
