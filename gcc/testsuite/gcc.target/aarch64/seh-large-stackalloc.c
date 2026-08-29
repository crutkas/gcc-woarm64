/* { dg-do compile { target aarch64*-*-mingw* } } */
/* { dg-options "-O1 -fno-omit-frame-pointer" } */

extern void consume (void *);

__attribute__ ((noinline))
int
seh_mid_frame (int x)
{
  volatile char local[50000];

  local[0] = x;
  local[49999] = x + 1;
  consume ((void *) local);
  return local[0] + local[49999];
}

__attribute__ ((noinline))
int
seh_large_frame (int x)
{
  volatile char local[100000];

  local[0] = x;
  local[99999] = x + 1;
  consume ((void *) local);
  return local[0] + local[99999];
}

__attribute__ ((noinline))
int
seh_oversized_frame (int x)
{
  volatile char local[300000000];

  local[0] = x;
  local[299999999] = x + 1;
  consume ((void *) local);
  return local[0] + local[299999999];
}

/* A stack allocation too large for alloc_m uses the 24-bit, scaled alloc_l
   encoding in both the prologue and epilogue.  */
/* { dg-final { scan-assembler-times "\\.seh_stackalloc\t98304" 2 } } */
/* Materializing a mid-sized adjustment needs a nop operation before the
   allocation operation, once in each direction.  */
/* { dg-final { scan-assembler-times "mov\tx12, 50016\n\t\\.seh_nop\n\t(?:add|sub)\tsp, sp, x12\n\t\\.seh_stackalloc\t50016" 2 } } */
/* An allocation larger than one alloc_l must be split into encodable,
   instruction-sized operations.  */
/* { dg-final { scan-assembler "\\.seh_stackalloc\t16773120" } } */
/* { dg-final { scan-assembler-not "\t(?:add|sub)\tsp, sp, x\[0-9\]+" } } */
