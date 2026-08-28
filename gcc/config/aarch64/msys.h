/* Operating system definitions for AArch64 MSYS.
   Copyright (C) 2026 Free Software Foundation, Inc.

This file is part of GCC.

GCC is free software; you can redistribute it and/or modify it under
the terms of the GNU General Public License as published by the Free
Software Foundation; either version 3, or (at your option) any later
version.

GCC is distributed in the hope that it will be useful, but WITHOUT ANY
WARRANTY; without even the implied warranty of MERCHANTABILITY or
FITNESS FOR A PARTICULAR PURPOSE.  See the GNU General Public License
for more details.

You should have received a copy of the GNU General Public License
along with GCC; see the file COPYING3.  If not see
<http://www.gnu.org/licenses/>.  */

#ifndef GCC_AARCH64_MSYS_H
#define GCC_AARCH64_MSYS_H

#undef EXTRA_OS_CPP_BUILTINS
#define EXTRA_OS_CPP_BUILTINS()					\
  do									\
    {									\
      builtin_define ("__CYGWIN__");					\
      builtin_define ("__MSYS__");					\
      builtin_define_std ("unix");					\
    }									\
  while (0)

#undef MULTILIB_DEFAULTS
#define MULTILIB_DEFAULTS { "mabi=lp64" }

#undef LIB_SPEC
#define LIB_SPEC "\
  %{pg:-lgmon} \
  %{pthread: } \
  -lmsys-2.0 \
  %{mwindows:-lgdi32 -lcomdlg32} \
  %{fvtable-verify=preinit:-lvtv -lpsapi; \
    fvtable-verify=std:-lvtv -lpsapi} \
  -ladvapi32 -lshell32 -luser32 -lkernel32"

#undef SUB_LINK_ENTRY64
#define SUB_LINK_ENTRY64 "-e _msys_dll_entry"

#undef LINK_SPEC
#define LINK_SPEC SUB_LINK_SPEC "\
  %{mwindows:--subsystem windows} \
  %{mconsole:--subsystem console} \
  " CXX_WRAP_SPEC " \
  %{shared: %{mdll: %eshared and mdll are not compatible}} \
  %{shared: --shared} %{mdll:--dll} \
  %{static:-Bstatic} %{!static:-Bdynamic} \
  %{shared|mdll: " SUB_LINK_ENTRY " --enable-auto-image-base} \
  %(shared_libgcc_undefs) \
  --dll-search-prefix=msys- \
  %{rdynamic: --export-all-symbols} \
  %{!shared: %{!mdll: %{" SPEC_32 ":--large-address-aware} --tsaware}}"

#undef LIBGCC_SONAME
#define LIBGCC_SONAME "msys-gcc_s-seh-1.dll"

#endif /* GCC_AARCH64_MSYS_H */
