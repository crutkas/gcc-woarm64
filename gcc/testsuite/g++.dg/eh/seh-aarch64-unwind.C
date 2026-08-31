/* { dg-do run { target aarch64*-*-mingw* } } */
/* { dg-options "-O1 -fno-omit-frame-pointer" } */

__attribute__ ((noinline))
static void
raise_exception (int value)
{
  volatile int copy = value;
  throw copy;
}

int
main ()
{
  try
    {
      raise_exception (42);
    }
  catch (int value)
    {
      return value != 42;
    }
  return 1;
}
