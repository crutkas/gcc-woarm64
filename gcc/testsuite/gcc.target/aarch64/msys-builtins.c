/* { dg-do compile { target aarch64*-*-msys* } } */
/* { dg-options "-std=c11" } */

#ifndef __MSYS__
# error "__MSYS__ is not defined"
#endif

#ifndef __CYGWIN__
# error "__CYGWIN__ compatibility is not defined"
#endif

#ifndef _WIN64
# error "_WIN64 is not defined"
#endif

#ifdef __MINGW32__
# error "__MINGW32__ must not be defined"
#endif

#if defined (_WIN32) || defined (WIN32) || defined (__WIN32__)
# error "MSYS must not enable the optional Win32 macro family by default"
#endif

#if defined (WIN64) || defined (__WIN64__)
# error "MSYS must not enable MinGW's Win64 aliases"
#endif

_Static_assert (sizeof (void *) == 8, "MSYS pointers are 64-bit");
_Static_assert (sizeof (long) == 8, "MSYS uses LP64");
_Static_assert (__builtin_types_compatible_p (__SIZE_TYPE__,
					      unsigned long),
		"MSYS size_t is unsigned long");
_Static_assert (__builtin_types_compatible_p (__PTRDIFF_TYPE__, long),
		"MSYS ptrdiff_t is long");
