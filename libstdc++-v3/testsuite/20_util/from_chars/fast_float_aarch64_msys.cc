// Copyright (C) 2026 Free Software Foundation, Inc.
//
// This file is part of the GNU ISO C++ Library.  This library is free
// software; you can redistribute it and/or modify it under the
// terms of the GNU General Public License as published by the
// Free Software Foundation; either version 3, or (at your option)
// any later version.
//
// This library is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
// GNU General Public License for more details.
//
// You should have received a copy of the GNU General Public License along
// with this library; see the file COPYING3.  If not see
// <http://www.gnu.org/licenses/>.

// { dg-do compile { target aarch64*-*-msys* } }
// { dg-options "-std=gnu++17 -O2" }

#ifndef _WIN64
# error "_WIN64 is not defined"
#endif

#ifndef __CYGWIN__
# error "__CYGWIN__ compatibility is not defined"
#endif

#ifndef __MSYS__
# error "__MSYS__ is not defined"
#endif

#ifndef __aarch64__
# error "__aarch64__ is not defined"
#endif

#if !defined(__SIZEOF_INT128__) || __SIZEOF_INT128__ != 16
# error "128-bit integer support is required"
#endif

#ifdef __MINGW32__
# error "__MINGW32__ must not be defined"
#endif

#ifdef _MSC_VER
# error "_MSC_VER must not be defined for the GNU target"
#endif

#include <charconv>
#include <cfloat>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <limits>
#include <system_error>
#include <type_traits>
#include <intrin.h>

// The target's Windows headers define this before fast_float selects a path.
#ifndef _M_ARM64
# error "_M_ARM64 is not defined"
#endif

#pragma GCC poison __umulh _umul128

#include "../../../src/c++17/fast_float/fast_float.h"

fast_float::value128
test_full_multiplication (uint64_t a, uint64_t b)
{
  return fast_float::full_multiplication (a, b);
}

// The GNU __uint128_t multiplication lowers to the AArch64 high-half multiply.
// { dg-final { scan-assembler-times {\tumulh\t} 1 } }
