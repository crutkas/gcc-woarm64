/* { dg-do compile { target aarch64*-*-cygwin* } } */
/* { dg-options "-std=c11" } */

#ifndef __CYGWIN__
# error "__CYGWIN__ is not defined"
#endif

#ifndef _WIN64
# error "_WIN64 is not defined"
#endif

#ifdef __MSYS__
# error "__MSYS__ must not be defined for Cygwin"
#endif

#ifdef __MINGW32__
# error "__MINGW32__ must not be defined for Cygwin"
#endif

#if defined (_WIN32) || defined (WIN32) || defined (__WIN32__)
# error "Cygwin must not enable the optional Win32 macro family by default"
#endif

#if defined (WIN64) || defined (__WIN64__)
# error "Cygwin must not enable MinGW's Win64 aliases"
#endif

_Static_assert (sizeof (void *) == 8, "Cygwin pointers are 64-bit");
_Static_assert (sizeof (long) == 8, "Cygwin uses LP64");
_Static_assert (__builtin_types_compatible_p (__SIZE_TYPE__,
					      unsigned long),
		"Cygwin size_t is unsigned long");
_Static_assert (__builtin_types_compatible_p (__PTRDIFF_TYPE__, long),
		"Cygwin ptrdiff_t is long");
