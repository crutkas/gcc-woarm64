/* { dg-do preprocess { target aarch64*-*-msys* } } */

#ifndef __MSYS__
# error "__MSYS__ is not defined"
#endif

#ifndef __CYGWIN__
# error "__CYGWIN__ compatibility is not defined"
#endif

#ifdef __MINGW32__
# error "__MINGW32__ must not be defined"
#endif

#if __SIZEOF_LONG__ != 8
# error "MSYS uses the LP64 data model"
#endif
