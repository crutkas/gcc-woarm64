/* { dg-do preprocess { target aarch64*-*-cygwin* } } */

#ifndef __CYGWIN__
# error "__CYGWIN__ is not defined"
#endif

#ifdef __MSYS__
# error "__MSYS__ must not be defined for Cygwin"
#endif
