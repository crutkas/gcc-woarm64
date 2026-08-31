/* { dg-do compile } */
/* { dg-options "-O2" } */

extern long long consume (long long, long long, long long, long long,
			  long long, long long, long long, long long,
			  long long, long long, long long, long long);

long long
stack_args (long long a0, long long a1, long long a2, long long a3,
	    long long a4, long long a5, long long a6, long long a7,
	    long long a8, long long a9, long long a10, long long a11)
{
  long long result = consume (a0, a1, a2, a3, a4, a5,
			      a6, a7, a8, a9, a10, a11);

  return result + a0 + 2 * a1 + 3 * a2 + 4 * a3 + 5 * a4 + 6 * a5
    + 7 * a6 + 8 * a7 + 9 * a8 + 10 * a9 + 11 * a10 + 12 * a11;
}

/* Stack argument loads into callee-saved registers are not unwind
   operations, even when scheduled before the end of the prologue.  */
/* { dg-final { scan-assembler "ldp\tx\[0-9\]+, x\[0-9\]+, \\[sp, \[0-9\]+\\]\n\t\\.seh_nop" } } */
