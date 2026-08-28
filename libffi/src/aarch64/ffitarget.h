/* Copyright (c) 2009, 2010, 2011, 2012 ARM Ltd.

Permission is hereby granted, free of charge, to any person obtaining
a copy of this software and associated documentation files (the
``Software''), to deal in the Software without restriction, including
without limitation the rights to use, copy, modify, merge, publish,
distribute, sublicense, and/or sell copies of the Software, and to
permit persons to whom the Software is furnished to do so, subject to
the following conditions:

The above copyright notice and this permission notice shall be
included in all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED ``AS IS'', WITHOUT WARRANTY OF ANY KIND,
EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT.
IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY
CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT,
TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE
SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.  */

#ifndef LIBFFI_TARGET_H
#define LIBFFI_TARGET_H

#ifndef LIBFFI_H
#error "Please do not include ffitarget.h directly into your source.  Use ffi.h instead."
#endif

/* AArch64 Windows environments disagree about which macros they predefine.
   MinGW and MSVC define both _WIN32 and _WIN64, but the Cygwin and MSYS2
   AArch64 targets define _WIN64 (and __CYGWIN__) while leaving _WIN32
   undefined unless -mwin32 is passed.  Testing _WIN32 on its own therefore
   mis-detects those targets even though configure.host selects
   TARGET=ARM_WIN64 for them and the compiler uses the Microsoft variant of
   the AArch64 procedure call standard.  Decide once, here, and use the
   result for every Windows ABI decision in this header so the whole macro
   set stays consistent across the MinGW, Cygwin and MSYS families.

   Note this deliberately covers only ABI/feature selection.  Tests for the
   Win32 *API* surface (windows.h, FlushInstructionCache, the absence of
   mmap-based dual mapping) legitimately remain plain _WIN32 tests
   elsewhere, because Cygwin and MSYS supply the POSIX implementations.  */
#if defined(_WIN32) || defined(_WIN64) || defined(__CYGWIN__)
#define FFI_AARCH64_WINDOWS 1
#endif

#ifndef LIBFFI_ASM
#ifdef __ILP32__
#define FFI_SIZEOF_ARG 8
#define FFI_SIZEOF_JAVA_RAW  4
typedef unsigned long long ffi_arg;
typedef signed long long ffi_sarg;
#elif defined(FFI_AARCH64_WINDOWS)
#define FFI_SIZEOF_ARG 8
typedef unsigned long long ffi_arg;
typedef signed long long ffi_sarg;
#else
typedef unsigned long ffi_arg;
typedef signed long ffi_sarg;
#endif

typedef enum ffi_abi
  {
    FFI_FIRST_ABI = 0,
    FFI_SYSV,
    FFI_WIN64,
    FFI_LAST_ABI,
#if defined(FFI_AARCH64_WINDOWS)
    FFI_DEFAULT_ABI = FFI_WIN64
#else
    FFI_DEFAULT_ABI = FFI_SYSV
#endif
  } ffi_abi;
#endif

/* ---- Definitions for closures ----------------------------------------- */

#define FFI_CLOSURES 1
#define FFI_NATIVE_RAW_API 0

#if defined (FFI_EXEC_TRAMPOLINE_TABLE) && FFI_EXEC_TRAMPOLINE_TABLE

#ifdef __MACH__
#define FFI_TRAMPOLINE_SIZE 16
#define FFI_TRAMPOLINE_CLOSURE_OFFSET 16
#else
#error "No trampoline table implementation"
#endif

#else
#define FFI_TRAMPOLINE_SIZE 24
#define FFI_TRAMPOLINE_CLOSURE_OFFSET FFI_TRAMPOLINE_SIZE
#endif

#ifdef FFI_AARCH64_WINDOWS
#define FFI_EXTRA_CIF_FIELDS unsigned is_variadic
#endif
#define FFI_TARGET_SPECIFIC_VARIADIC

/* ---- Internal ---- */

#if defined (__APPLE__)
#define FFI_EXTRA_CIF_FIELDS unsigned aarch64_nfixedargs
#elif !defined(FFI_AARCH64_WINDOWS)
/* iOS and Windows reserve x18 for the system.  Disable Go closures until
   a new static chain is chosen.  Windows on AArch64 keeps the thread
   environment block in x18 (see gcc/config/aarch64/aarch64-abi-ms.h, which
   marks it fixed and never call-clobbered), and ffi_call_SYSV installs the
   static chain into x18 for every call once FFI_GO_CLOSURES is enabled.  */
#define FFI_GO_CLOSURES 1
#endif

#ifndef FFI_AARCH64_WINDOWS
/* No complex type on Windows */
#define FFI_TARGET_HAS_COMPLEX_TYPE
#endif

#endif
