/* { dg-do compile { target { *-*-cygwin* *-*-msys* *-*-mingw* } } } */
/* { dg-options "-std=gnu11" } */

#include <windows.h>

_Static_assert (sizeof (ULONG64) == 8, "ULONG64 must be 64-bit");
_Static_assert (sizeof (ULONG_PTR) == sizeof (void *),
		"ULONG_PTR must be pointer-sized");
_Static_assert (__builtin_types_compatible_p (PULONG_PTR, ULONG_PTR *),
		"PULONG_PTR must point to ULONG_PTR");

#if defined (__aarch64__)
typedef PRUNTIME_FUNCTION (NTAPI *lookup_function_type)
  (ULONG_PTR, PULONG_PTR, PUNWIND_HISTORY_TABLE);
#elif defined (__x86_64__)
typedef PRUNTIME_FUNCTION (NTAPI *lookup_function_type)
  (DWORD64, PDWORD64, PUNWIND_HISTORY_TABLE);
#endif

#if defined (__aarch64__) || defined (__x86_64__)
_Static_assert (__builtin_types_compatible_p
		(__typeof__ (&RtlLookupFunctionEntry), lookup_function_type),
		"RtlLookupFunctionEntry declaration");

PRUNTIME_FUNCTION
lookup (void *pc)
{
  ULONG_PTR image_base;
  return RtlLookupFunctionEntry ((ULONG_PTR) pc, &image_base, 0);
}
#endif

#ifdef _WIN64
_Static_assert (__builtin_types_compatible_p (ULONG64, ULONG_PTR),
		"_WIN64 selects 64-bit Windows integer aliases");
#endif
