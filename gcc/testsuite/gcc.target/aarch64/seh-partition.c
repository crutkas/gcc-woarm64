/* { dg-do compile { target aarch64*-*-mingw* } } */
/* { dg-options "-O2 -freorder-blocks-and-partition" } */

__attribute__ ((noinline, cold))
static int
cold_path (int x)
{
  return x - 1;
}

int
seh_partition (int x)
{
  if (__builtin_expect (x == 0, 0))
    return cold_path (x);
  return x + 1;
}

/* SEH does not yet reconstruct unwind state for a second text range.  */
/* { dg-final { scan-assembler-not "\\.seh_proc\tseh_partition\\.cold" } } */
/* { dg-final { scan-assembler-times "\\.seh_proc\tseh_partition\n" 1 } } */
