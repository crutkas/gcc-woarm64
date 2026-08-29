/* { dg-do compile { target aarch64*-*-mingw* } } */
/* { dg-options "-O1 -march=armv8.2-a+sve -fstack-clash-protection" } */
/* { dg-prune-output "warning: .* are mutually exclusive" } */

#pragma GCC aarch64 "arm_sve.h"

extern int take_stack_args (volatile void *, void *, int, int, int,
			    int, int, int, int);

int
seh_sve_stack_clash (int n) /* { dg-message "sorry, unimplemented: Windows SEH does not support scalable AArch64 stack frames" } */
{
  volatile int local[0x7ee4];
  volatile svint32_t sve_local;

  sve_local = svdup_s32 (n);
  take_stack_args (local, __builtin_alloca (n), 1, 2, 3, 4, 5, 6, 7);
  asm volatile ("" : : "w" (sve_local) : "x24", "x25", "x26");
  return local[0];
}
