#!/usr/bin/env python3

# Copyright (C) 2026 Free Software Foundation, Inc.
#
# This file is part of GCC.
#
# GCC is free software; you can redistribute it and/or modify it under
# the terms of the GNU General Public License as published by the Free
# Software Foundation; either version 3, or (at your option) any later
# version.
#
# GCC is distributed in the hope that it will be useful, but WITHOUT ANY
# WARRANTY; without even the implied warranty of MERCHANTABILITY or
# FITNESS FOR A PARTICULAR PURPOSE.  See the GNU General Public License
# for more details.
#
# You should have received a copy of the GNU General Public License
# along with GCC; see the file COPYING3.  If not see
# <http://www.gnu.org/licenses/>.

import ast
from fnmatch import fnmatchcase
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]

PIC_CONFIGURES = (
    Path("gcc/configure"),
    Path("libada/configure"),
    Path("libgcc/configure"),
    Path("libiberty/configure"),
)

PLUGIN_CONTROLLERS = (
    Path("config/gcc-plugin.m4"),
    Path("libtool.m4"),
)

GCC_PLUGIN_CONFIGURES = (
    Path("configure"),
    Path("libiberty/configure"),
)

LIBTOOL_PLUGIN_CONFIGURES = (
    Path("gcc/configure"),
    Path("libatomic/configure"),
    Path("libbacktrace/configure"),
    Path("libcc1/configure"),
    Path("libffi/configure"),
    Path("libgfortran/configure"),
    Path("libgm2/configure"),
    Path("libgomp/configure"),
    Path("libgrust/configure"),
    Path("libitm/configure"),
    Path("libobjc/configure"),
    Path("libphobos/configure"),
    Path("libquadmath/configure"),
    Path("libsanitizer/configure"),
    Path("libssp/configure"),
    Path("libstdc++-v3/configure"),
    Path("libvtv/configure"),
    Path("lto-plugin/configure"),
    Path("zlib/configure"),
)

PLUGIN_CONFIGURES = GCC_PLUGIN_CONFIGURES + LIBTOOL_PLUGIN_CONFIGURES

PLUGIN_CANDIDATES = {
    "cygwin": ("cyglto_plugin.dll", "cyglto_plugin-0.dll"),
    "msys": ("msys-lto_plugin.dll", "msys-lto_plugin-0.dll"),
    "mingw": ("liblto_plugin.dll", "liblto_plugin-0.dll"),
    "other": ("liblto_plugin.so",),
}
ALL_PLUGIN_NAMES = tuple(
    name
    for candidates in PLUGIN_CANDIDATES.values()
    for name in candidates
)

EXPANDED_ASM_SPEC = (
    "%{march=*:-march=%:rewrite_march(%{march=*:%*});"
    "mcpu=*:-march=%:rewrite_mcpu(%{mcpu=*:%*})}"
)


def read_source(path):
    return (ROOT / path).read_text(encoding="utf-8")


def logical_lines(text):
    result = []
    pending = ""
    for line in text.splitlines():
        if pending:
            line = pending + line.strip()
        if line.rstrip().endswith("\\"):
            pending = line.rstrip()[:-1] + " "
        else:
            result.append(line)
            pending = ""
    if pending:
        result.append(pending)
    return result


def pic_case(path):
    text = read_source(path)
    match = re.search(
        r'case "\$\{(?:\$2|host|target)\}" in\n'
        r"    # PIC is the default on some targets or must not be used\.\n"
        r"(.*?)^esac$",
        text,
        re.MULTILINE | re.DOTALL,
    )
    if match is None:
        raise AssertionError(f"PIC case not found in {path}")

    lines = logical_lines(match.group(1))
    arm_indexes = [
        index
        for index, line in enumerate(lines)
        if re.match(r"^ {4}\S.*\)$", line)
    ]
    arms = []
    for position, index in enumerate(arm_indexes):
        end = (
            arm_indexes[position + 1]
            if position + 1 < len(arm_indexes)
            else len(lines)
        )
        patterns = lines[index].strip()[:-1].split("|")
        patterns = tuple(
            pattern.strip().replace("[[", "[").replace("]]", "]")
            for pattern in patterns
        )
        arms.append((patterns, "\n".join(lines[index + 1:end])))
    return arms


def picflag_for(path, target):
    for patterns, body in pic_case(path):
        if any(fnmatchcase(target, pattern) for pattern in patterns):
            assignment = re.search(
                r"(?:\$1|PICFLAG(?:_FOR_TARGET)?)="
                r"(?:'([^']*)'|([^\s;]+))",
                body,
            )
            if assignment is None:
                return ""
            return (
                assignment.group(1)
                if assignment.group(1) is not None
                else assignment.group(2)
            )
    raise AssertionError(f"no PIC case matched {target} in {path}")


def plugin_discovery_block(path, source=None):
    text = read_source(path) if source is None else source
    matches = re.findall(
        r'^\[?(case "\$\{host\}" in\n'
        r'  \*-\*-cygwin\*\) plugin_names=.*?^done)$',
        text,
        re.MULTILINE | re.DOTALL,
    )
    if len(matches) != 1:
        raise AssertionError(
            f"expected one plugin discovery block in {path}, "
            f"got {len(matches)}"
        )
    return matches[0]


def libtool_archive_probe_block(path, source=None):
    text = read_source(path) if source is None else source
    discovery = plugin_discovery_block(path, text)
    search_from = text.index(discovery) + len(discovery)
    start_marker = 'test -z "$AR" && AR=ar\n'
    start = text.index(start_marker, search_from) + len(start_marker)
    end = text.index('test -z "$AR_FLAGS"', start)
    return text[start:end].rstrip()


def libtool_ranlib_block(path, source=None):
    text = read_source(path) if source is None else source
    discovery = plugin_discovery_block(path, text)
    search_from = text.index(discovery) + len(discovery)
    match = re.search(
        r'^if test -n "\$plugin_option" && '
        r'test "\$RANLIB" != ":"; then\n'
        r'  if \$RANLIB --help 2>&1 \| grep -q "\\--plugin"; then\n'
        r'    RANLIB="\$RANLIB \$plugin_option"\n'
        r'  fi\n'
        r'fi$',
        text[search_from:],
        re.MULTILINE,
    )
    if match is None:
        raise AssertionError(f"libtool RANLIB block not found in {path}")
    return match.group(0)


def normalized_libtool_probe(block):
    return "\n".join(
        line
        for line in block.splitlines()
        if "WARNING: Failed: $AR $plugin_option rc" not in line
        and "AC_MSG_WARN([Failed: $AR $plugin_option rc])" not in line
    )


def configure_line_markers(path):
    markers = []
    for line_number, line in enumerate(
        read_source(path).splitlines(), start=1
    ):
        match = re.fullmatch(r'#line (\d+) "configure"', line)
        if match is not None:
            markers.append((line_number, int(match.group(1))))
    return markers


def tm_headers(body):
    return tuple(
        header
        for value in re.findall(
            r'tm_file="\$\{tm_file\} ([^"]+)"', body
        )
        for header in value.split()
    )


def tm_defines(body):
    return {
        define
        for value in re.findall(
            r'tm_defines="\$\{tm_defines\} ([^"]+)"', body
        )
        for define in value.split()
    }


def aarch64_target_config(target):
    text = read_source("gcc/config.gcc")
    if (
        fnmatchcase(target, "aarch64-*-cygwin*")
        or fnmatchcase(target, "aarch64-*-msys*")
    ):
        match = re.search(
            r"^aarch64-\*-cygwin\* \| aarch64-\*-msys\*\)\n"
            r"(.*?)^aarch64-\*-mingw\*\)\n",
            text,
            re.MULTILINE | re.DOTALL,
        )
        if match is None:
            raise AssertionError(
                "AArch64 Cygwin/MSYS config.gcc arm not found"
            )
        body = match.group(1)
        nested = re.search(
            r"^\tcase \$\{target\} in\n(.*?)^\tesac$",
            body,
            re.MULTILINE | re.DOTALL,
        )
        if nested is None:
            raise AssertionError(
                "AArch64 MSYS nested config.gcc arm not found"
            )
        headers = list(tm_headers(body[:nested.start()]))
        if fnmatchcase(target, "*-msys*"):
            headers.extend(tm_headers(nested.group(1)))
        headers.extend(tm_headers(body[nested.end():]))
        return tuple(headers), tm_defines(body)

    if fnmatchcase(target, "aarch64-*-mingw*"):
        match = re.search(
            r"^aarch64-\*-mingw\*\)\n"
            r"(.*?)^aarch64\*-wrs-vxworks\*\)\n",
            text,
            re.MULTILINE | re.DOTALL,
        )
        if match is None:
            raise AssertionError("AArch64 MinGW config.gcc arm not found")
        return tm_headers(match.group(1)), tm_defines(match.group(1))

    raise AssertionError(f"not an AArch64 Windows target: {target}")


def condition_value(expression, macros):
    match = re.fullmatch(
        r"(!?)defined\s*\(\s*([A-Za-z_]\w*)\s*\)", expression.strip()
    )
    if match is None:
        raise AssertionError(
            f"unsupported preprocessor condition: {expression}"
        )
    value = match.group(2) in macros
    return not value if match.group(1) else value


def effective_object_macro(
    headers, initial_macros, macro_name, source_overrides=None
):
    macros = set(initial_macros)
    value = None
    origin = None
    source_overrides = source_overrides or {}

    for header in headers:
        path = Path("gcc/config") / header
        text = source_overrides.get(path, read_source(path))
        lines = text.splitlines()
        relevant = [
            index
            for index, line in enumerate(lines)
            if re.match(
                rf"^\s*#\s*(?:define|undef)\s+"
                rf"{re.escape(macro_name)}\b",
                line,
            )
        ]
        if not relevant:
            continue

        active = True
        conditions = []
        for line in lines[:relevant[-1] + 1]:
            directive = re.match(
                r"^\s*#\s*(ifn?def|if|else|endif|define|undef)"
                r"(?:\s+(.*))?$",
                line,
            )
            if directive is None:
                continue
            name = directive.group(1)
            argument = (directive.group(2) or "").strip()
            if name in ("ifdef", "ifndef"):
                condition = argument in macros
                if name == "ifndef":
                    condition = not condition
                conditions.append((active, condition))
                active = active and condition
            elif name == "if":
                condition = condition_value(argument, macros)
                conditions.append((active, condition))
                active = active and condition
            elif name == "else":
                parent, condition = conditions[-1]
                active = parent and not condition
            elif name == "endif":
                parent, unused = conditions.pop()
                active = parent
            elif active and name == "undef":
                macro = argument.split()[0]
                macros.discard(macro)
                if macro == macro_name:
                    value = None
                    origin = None
            elif active and name == "define":
                definition = re.match(
                    r"([A-Za-z_]\w*)(?:\s+(.*))?$", argument
                )
                if definition is None:
                    raise AssertionError(f"bad macro definition: {line}")
                macro = definition.group(1)
                macros.add(macro)
                if macro == macro_name:
                    value = definition.group(2)
                    origin = header

    return value, origin


def effective_asm_spec(headers, initial_macros):
    value, origin = effective_object_macro(
        headers, initial_macros, "ASM_SPEC"
    )
    return ast.literal_eval(value), origin


def multilib_defaults(headers, initial_macros, source_overrides=None):
    value, origin = effective_object_macro(
        headers,
        initial_macros,
        "MULTILIB_DEFAULTS",
        source_overrides,
    )
    if value is None:
        return ("",), None
    strings = re.findall(r'"(?:\\.|[^"\\])*"', value)
    if not strings:
        raise AssertionError(f"bad MULTILIB_DEFAULTS definition: {value}")
    return tuple(ast.literal_eval(string) for string in strings), origin


def jit_multilib_arguments(defaults):
    text = read_source("gcc/jit/jit-playback.cc")
    function = re.search(
        r"add_multilib_driver_arguments \(vec <char \*> \*argvec\)\n"
        r"\{(.*?)^\}",
        text,
        re.MULTILINE | re.DOTALL,
    )
    if function is None:
        raise AssertionError("JIT multilib driver function not found")
    self_body = function.group(1)
    if "if (multilib_defaults_raw[i][0])" not in self_body:
        raise AssertionError("JIT no longer filters empty multilib defaults")
    prefix = re.search(
        r'concat \(("(?:\\.|[^"\\])*"), '
        r"multilib_defaults_raw\[i\], NULL\)",
        self_body,
    )
    if prefix is None:
        raise AssertionError("JIT multilib argument construction not found")
    prefix_value = ast.literal_eval(prefix.group(1))
    return tuple(prefix_value + default for default in defaults if default)


def object_macro(path, name):
    lines = read_source(path).splitlines()
    for index, line in enumerate(lines):
        match = re.match(
            rf"^\s*#\s*define\s+{re.escape(name)}(?:\s+(.*))?$", line
        )
        if match is None:
            continue
        parts = [match.group(1) or ""]
        while lines[index].rstrip().endswith("\\"):
            parts[-1] = parts[-1].rstrip()[:-1]
            index += 1
            parts.append(lines[index].strip())
        return " ".join(part for part in parts if part).strip()
    raise AssertionError(f"macro {name} not found in {path}")


def expand_object_macro(path, name, seen=()):
    if name in seen:
        raise AssertionError(f"recursive macro expansion for {name}")
    body = object_macro(path, name)
    strings = re.findall(r'"(?:\\.|[^"\\])*"', body)
    if strings:
        return "".join(ast.literal_eval(string) for string in strings)
    if re.fullmatch(r"[A-Za-z_]\w*", body):
        return expand_object_macro(path, body, seen + (name,))
    raise AssertionError(f"cannot expand macro {name}: {body}")


def expanded_asm_spec(raw_spec):
    aarch64_header = Path("gcc/config/aarch64/aarch64.h")
    specs = object_macro(aarch64_header, "EXTRA_SPECS")
    match = re.search(
        r'\{\s*"asm_cpu_spec"\s*,\s*([A-Za-z_]\w*)\s*\}', specs
    )
    if match is None:
        raise AssertionError("asm_cpu_spec mapping not found")
    replacement = expand_object_macro(aarch64_header, match.group(1))
    return raw_spec.replace("%(asm_cpu_spec)", replacement)


class TestArm64WindowsRouting(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.shell = shutil.which("sh")
        if cls.shell is None:
            raise AssertionError("sh is required to test mkfixinc.sh routing")

    def canonical_target(self, target):
        result = subprocess.run(
            [self.shell, str(ROOT / "config.sub"), target],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        self.assertEqual(
            result.returncode,
            0,
            f"config.sub failed for {target}: {result.stderr}",
        )
        return result.stdout.strip()

    def run_plugin_discovery(
        self, block, host, available, target=None
    ):
        environment = os.environ.copy()
        environment.update(
            {
                "AVAILABLE": " ".join(available),
                "host": host,
                "target": target or host,
            }
        )
        script = r'''
fake_cc ()
{
  name=
  for argument
  do
    name=$argument
  done
  case " $AVAILABLE " in
    *" $name "*) printf "/plugins/%s\n" "$name" ;;
    *) printf "%s\n" "$name" ;;
  esac
}
CC=fake_cc
CFLAGS=
''' + block + r'''
printf "%s\n" "$plugin_option"
'''
        result = subprocess.run(
            [self.shell, "-c", script],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
        )
        self.assertEqual(
            result.returncode,
            0,
            f"plugin discovery failed: {result.stderr}",
        )
        return result.stdout.strip()

    def run_libtool_archive_probe(
        self,
        discovery,
        archive_probe,
        ranlib_probe,
        host,
        available,
        ar_status,
        ar_advertises=True,
    ):
        warning = "      AC_MSG_WARN([Failed: $AR $plugin_option rc])"
        self.assertIn(warning, archive_probe)
        archive_probe = archive_probe.replace(warning, "      :")
        environment = os.environ.copy()
        environment.update(
            {
                "AR_ADVERTISES": "yes" if ar_advertises else "no",
                "AR_STATUS": str(ar_status),
                "AVAILABLE": " ".join(available),
                "host": host,
                "target": host,
            }
        )
        script = r'''
fake_cc ()
{
  name=
  for argument
  do
    name=$argument
  done
  case " $AVAILABLE " in
    *" $name "*) printf "/plugins/%s\n" "$name" ;;
    *) printf "%s\n" "$name" ;;
  esac
}
fake_ar ()
{
  if test "$1" = "--help"; then
    test "$AR_ADVERTISES" = yes && printf "%s\n" "--plugin"
    return 0
  fi
  return "$AR_STATUS"
}
fake_ranlib ()
{
  if test "$1" = "--help"; then
    printf "%s\n" "--plugin"
  fi
  return 0
}
CC=fake_cc
CFLAGS=
AR=fake_ar
RANLIB=fake_ranlib
''' + discovery + "\n" + archive_probe + "\n" + ranlib_probe + r'''
printf "plugin_option=%s\n" "$plugin_option"
printf "AR=%s\n" "$AR"
printf "RANLIB=%s\n" "$RANLIB"
'''
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run(
                [self.shell, "-c", script],
                cwd=directory,
                env=environment,
                capture_output=True,
                text=True,
            )
        self.assertEqual(
            result.returncode,
            0,
            f"libtool archive probe failed: {result.stderr}",
        )
        return dict(
            line.split("=", 1)
            for line in result.stdout.splitlines()
        )

    def fixincludes_kind(self, target):
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            shutil.copy2(ROOT / "fixincludes/mkfixinc.sh", temp)
            shutil.copy2(ROOT / "fixincludes/fixinc.in", temp)
            environment = os.environ.copy()
            environment["srcdir"] = "."
            result = subprocess.run(
                [self.shell, "./mkfixinc.sh", target],
                cwd=temp,
                env=environment,
                capture_output=True,
                text=True,
            )
            self.assertEqual(
                result.returncode,
                0,
                f"mkfixinc.sh failed for {target}: {result.stderr}",
            )
            generated = (temp / "fixinc.sh").read_bytes()
            if generated == (temp / "fixinc.in").read_bytes():
                return "real"
            self.assertEqual(generated, b"#! /bin/sh\nexit 0\n")
            return "no-op"

    def host_lto_plugin_soname(self, host):
        environment = os.environ.copy()
        environment["host"] = host
        environment["target"] = host
        result = subprocess.run(
            [
                self.shell,
                "-c",
                '. ./gcc/config.host; printf "%s" "$host_lto_plugin_soname"',
            ],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
        )
        self.assertEqual(
            result.returncode,
            0,
            f"config.host failed for {host}: {result.stderr}",
        )
        return result.stdout

    def test_fixincludes_routing(self):
        matrix = {
            "aarch64-pc-msys": "real",
            "aarch64-pc-msys2": "real",
            "aarch64-pc-cygwin": "real",
            "x86_64-pc-msys": "real",
            "i686-pc-cygwin": "no-op",
            "aarch64-w64-mingw32": "no-op",
            "aarch64-pc-msy": "real",
        }
        for target, expected in matrix.items():
            with self.subTest(target=target):
                self.assertEqual(self.fixincludes_kind(target), expected)

    def test_picflag_routing_and_generated_copies(self):
        expected_patterns = [
            patterns
            for patterns, unused in pic_case(Path("config/picflag.m4"))
        ]
        expected_consumers = {ROOT / path for path in PIC_CONFIGURES}
        actual_consumers = {
            path
            for path in ROOT.rglob("configure")
            if "# PIC is the default on some targets or must not be used."
            in path.read_text(encoding="utf-8")
        }
        self.assertEqual(actual_consumers, expected_consumers)

        matrix = {
            "aarch64-pc-cygwin": "",
            "aarch64-pc-msys": "",
            "aarch64-w64-mingw32": "",
            "x86_64-pc-cygwin": "",
            "x86_64-pc-msys": "",
            "x86_64-w64-mingw32": "",
            "i686-pc-cygwin": "",
            "i686-pc-msys": "",
            "i686-w64-mingw32": "",
            "aarch64-pc-linux-gnu": "-fPIC",
            "arm64-pc-msys": "-fPIC",
            "aarch64-pc-msy": "-fPIC",
        }
        for path in (Path("config/picflag.m4"),) + PIC_CONFIGURES:
            with self.subTest(path=path):
                patterns = [
                    arm_patterns
                    for arm_patterns, unused in pic_case(path)
                ]
                self.assertEqual(patterns, expected_patterns)
                for target, expected in matrix.items():
                    self.assertEqual(picflag_for(path, target), expected)

        for path in PIC_CONFIGURES:
            variable = (
                "PICFLAG_FOR_TARGET"
                if path == Path("gcc/configure")
                else "PICFLAG"
            )
            assignments = {
                assignment
                for unused, body in pic_case(path)
                for assignment in re.findall(
                    r"\b(PICFLAG(?:_FOR_TARGET)?)=", body
                )
            }
            self.assertEqual(assignments, {variable})

    def test_effective_asm_specs(self):
        cygwin_headers = (
            "aarch64/aarch64-abi-ms.h",
            "aarch64/aarch64-coff.h",
            "aarch64/cygming.h",
            "i386/cygwin.h",
            "i386/cygwin-w64.h",
            "i386/cygwin-stdint.h",
            "mingw/winnt.h",
            "mingw/winnt-dll.h",
        )
        matrix = {
            "aarch64-pc-cygwin": cygwin_headers,
            "aarch64-pc-msys": cygwin_headers + ("aarch64/msys.h",),
            "aarch64-w64-mingw32": (
                "aarch64/aarch64-abi-ms.h",
                "aarch64/aarch64-coff.h",
                "aarch64/cygming.h",
                "mingw/mingw32.h",
                "mingw/mingw-stdint.h",
                "mingw/winnt.h",
                "mingw/winnt-dll.h",
            ),
        }
        for target, expected_headers in matrix.items():
            with self.subTest(target=target):
                headers, defines = aarch64_target_config(target)
                self.assertEqual(headers, expected_headers)
                self.assertIn("TARGET_AARCH64_MS_ABI=1", defines)
                macro_names = {
                    define.split("=", 1)[0] for define in defines
                }
                raw_spec, origin = effective_asm_spec(
                    headers, macro_names
                )
                self.assertEqual(raw_spec, "%(asm_cpu_spec)")
                self.assertEqual(origin, "aarch64/cygming.h")
                self.assertEqual(expanded_asm_spec(raw_spec),
                                 EXPANDED_ASM_SPEC)

        x86_spec, x86_origin = effective_asm_spec(
            ("i386/cygwin.h", "i386/cygwin-w64.h"), set()
        )
        self.assertEqual(x86_spec, "%{m32:--32} %{m64:--64}")
        self.assertEqual(x86_origin, "i386/cygwin-w64.h")

    def test_jit_multilib_defaults(self):
        for raw_target in ("aarch64-pc-cygwin", "arm64-pc-msys"):
            with self.subTest(target=raw_target):
                target = self.canonical_target(raw_target)
                self.assertTrue(target.startswith("aarch64-"))
                headers, defines = aarch64_target_config(target)
                macro_names = {
                    define.split("=", 1)[0] for define in defines
                }
                defaults, origin = multilib_defaults(
                    headers, macro_names
                )
                self.assertEqual(defaults, ("mabi=lp64",))
                self.assertEqual(origin, "aarch64/cygming.h")
                self.assertEqual(
                    jit_multilib_arguments(defaults),
                    ("-mabi=lp64",),
                )

        x86_defaults, x86_origin = multilib_defaults(
            ("i386/cygwin.h", "i386/cygwin-w64.h"), set()
        )
        self.assertEqual(x86_defaults, ("m64",))
        self.assertEqual(x86_origin, "i386/cygwin-w64.h")
        self.assertEqual(jit_multilib_arguments(x86_defaults), ("-m64",))

        mingw = self.canonical_target("aarch64-w64-mingw32")
        mingw_headers, mingw_defines = aarch64_target_config(mingw)
        mingw_defaults, mingw_origin = multilib_defaults(
            mingw_headers,
            {define.split("=", 1)[0] for define in mingw_defines},
        )
        self.assertEqual(mingw_defaults, ("",))
        self.assertIsNone(mingw_origin)
        self.assertEqual(jit_multilib_arguments(mingw_defaults), ())

    def test_plugin_routing_and_generated_copies(self):
        expected_consumers = {ROOT / path for path in PLUGIN_CONFIGURES}
        actual_consumers = {
            path
            for path in ROOT.rglob("configure")
            if "for plugin in $plugin_names; do"
            in path.read_text(encoding="utf-8")
        }
        self.assertEqual(actual_consumers, expected_consumers)

        gcc_controller = plugin_discovery_block(
            Path("config/gcc-plugin.m4")
        )
        libtool_controller = plugin_discovery_block(Path("libtool.m4"))
        self.assertEqual(gcc_controller, libtool_controller)
        gcc_plugin_callers = {
            path.relative_to(ROOT)
            for path in ROOT.rglob("configure.ac")
            if "GCC_PLUGIN_OPTION(" in path.read_text(encoding="utf-8")
        }
        self.assertEqual(
            gcc_plugin_callers,
            {Path("configure.ac"), Path("libiberty/configure.ac")},
        )
        self.assertIn(
            "-avoid-version", read_source("lto-plugin/Makefile.am")
        )
        for path in PLUGIN_CONFIGURES:
            with self.subTest(path=path):
                self.assertEqual(
                    plugin_discovery_block(path), gcc_controller
                )
                markers = configure_line_markers(path)
                self.assertEqual(
                    bool(markers), path in LIBTOOL_PLUGIN_CONFIGURES
                )
                for physical, marker in markers:
                    self.assertEqual(physical, marker)

        actual_gcc_consumers = {
            path
            for path in PLUGIN_CONFIGURES
            if 'AR="$AR $plugin_option"' not in read_source(path)
        }
        self.assertEqual(
            actual_gcc_consumers, set(GCC_PLUGIN_CONFIGURES)
        )
        actual_libtool_consumers = (
            set(PLUGIN_CONFIGURES) - actual_gcc_consumers
        )
        self.assertEqual(
            actual_libtool_consumers, set(LIBTOOL_PLUGIN_CONFIGURES)
        )

        controller_probe = libtool_archive_probe_block(
            Path("libtool.m4")
        )
        controller_ranlib = libtool_ranlib_block(Path("libtool.m4"))
        for path in LIBTOOL_PLUGIN_CONFIGURES:
            with self.subTest(libtool_consumer=path):
                self.assertEqual(
                    normalized_libtool_probe(
                        libtool_archive_probe_block(path)
                    ),
                    normalized_libtool_probe(controller_probe),
                )
                self.assertEqual(
                    libtool_ranlib_block(path), controller_ranlib
                )

        matrix = (
            ("arm64-pc-msys", "msys"),
            ("x86_64-pc-msys", "msys"),
            ("i686-pc-msys", "msys"),
            ("aarch64-pc-cygwin", "cygwin"),
            ("aarch64-w64-mingw32", "mingw"),
            ("aarch64-pc-linux-gnu", "other"),
        )
        for raw_host, family in matrix:
            host = self.canonical_target(raw_host)
            candidates = PLUGIN_CANDIDATES[family]
            with self.subTest(host=raw_host):
                if raw_host.startswith("arm64-"):
                    self.assertTrue(host.startswith("aarch64-"))
                self.assertEqual(
                    self.host_lto_plugin_soname(host), candidates[0]
                )
                for controller in PLUGIN_CONTROLLERS:
                    block = plugin_discovery_block(controller)
                    selected = self.run_plugin_discovery(
                        block, host, ALL_PLUGIN_NAMES
                    )
                    self.assertEqual(
                        selected, f"--plugin /plugins/{candidates[0]}"
                    )
                    for candidate in candidates:
                        selected = self.run_plugin_discovery(
                            block, host, (candidate,)
                        )
                        self.assertEqual(
                            selected, f"--plugin /plugins/{candidate}"
                        )

        msys_host = self.canonical_target("arm64-pc-msys")
        linux_host = self.canonical_target("aarch64-pc-linux-gnu")
        for controller in PLUGIN_CONTROLLERS:
            block = plugin_discovery_block(controller)
            with self.subTest(controller=controller, gate="foreign-host"):
                self.assertEqual(
                    self.run_plugin_discovery(
                        block,
                        linux_host,
                        PLUGIN_CANDIDATES["msys"],
                        target=msys_host,
                    ),
                    "",
                )
            with self.subTest(controller=controller, gate="cross-family"):
                self.assertEqual(
                    self.run_plugin_discovery(
                        block,
                        msys_host,
                        (
                            *PLUGIN_CANDIDATES["cygwin"],
                            *PLUGIN_CANDIDATES["mingw"],
                            *PLUGIN_CANDIDATES["other"],
                        ),
                        target=linux_host,
                    ),
                    "",
                )

        rejected = self.run_libtool_archive_probe(
            libtool_controller,
            controller_probe,
            controller_ranlib,
            msys_host,
            (PLUGIN_CANDIDATES["msys"][0],),
            ar_status=1,
        )
        self.assertEqual(rejected["plugin_option"], "")
        self.assertEqual(rejected["AR"], "fake_ar")
        self.assertEqual(rejected["RANLIB"], "fake_ranlib")

        unsupported = self.run_libtool_archive_probe(
            libtool_controller,
            controller_probe,
            controller_ranlib,
            msys_host,
            (PLUGIN_CANDIDATES["msys"][0],),
            ar_status=0,
            ar_advertises=False,
        )
        self.assertEqual(unsupported["plugin_option"], "")
        self.assertEqual(unsupported["AR"], "fake_ar")
        self.assertEqual(unsupported["RANLIB"], "fake_ranlib")

        accepted = self.run_libtool_archive_probe(
            libtool_controller,
            controller_probe,
            controller_ranlib,
            msys_host,
            (PLUGIN_CANDIDATES["msys"][0],),
            ar_status=0,
        )
        option = "--plugin /plugins/msys-lto_plugin.dll"
        self.assertEqual(accepted["plugin_option"], option)
        self.assertEqual(accepted["AR"], f"fake_ar {option}")
        self.assertEqual(accepted["RANLIB"], f"fake_ranlib {option}")

    def test_required_mutation_controls(self):
        msys_host = self.canonical_target("arm64-pc-msys")
        linux_host = self.canonical_target("aarch64-pc-linux-gnu")

        for controller in PLUGIN_CONTROLLERS:
            block = plugin_discovery_block(controller)
            loop_mutant = block.replace(
                "for plugin in $plugin_names; do",
                "for plugin in $plugin_name; do",
            )
            self.assertNotEqual(loop_mutant, block)
            self.assertEqual(
                self.run_plugin_discovery(
                    loop_mutant,
                    msys_host,
                    (PLUGIN_CANDIDATES["msys"][0],),
                ),
                "",
            )

            gate_mutant, count = re.subn(
                r'^case "\$\{host\}" in\n.*?^esac\n',
                f'plugin_names="{" ".join(ALL_PLUGIN_NAMES)}"\n',
                block,
                count=1,
                flags=re.MULTILINE | re.DOTALL,
            )
            self.assertEqual(count, 1)
            self.assertEqual(
                self.run_plugin_discovery(
                    gate_mutant,
                    linux_host,
                    (PLUGIN_CANDIDATES["msys"][0],),
                    target=msys_host,
                ),
                "--plugin /plugins/msys-lto_plugin.dll",
            )

            for family in ("cygwin", "msys", "mingw"):
                actual_name = PLUGIN_CANDIDATES[family][0]
                name_mutant = block.replace(actual_name + " ", "")
                self.assertNotEqual(name_mutant, block)
                family_host = self.canonical_target(
                    {
                        "cygwin": "aarch64-pc-cygwin",
                        "msys": "arm64-pc-msys",
                        "mingw": "aarch64-w64-mingw32",
                    }[family]
                )
                self.assertEqual(
                    self.run_plugin_discovery(
                        name_mutant, family_host, (actual_name,)
                    ),
                    "",
                )

        probe = libtool_archive_probe_block(Path("libtool.m4"))
        clear_line = (
            "      AC_MSG_WARN([Failed: $AR $plugin_option rc])\n"
            "      plugin_option=\n"
        )
        clear_mutant = probe.replace(
            clear_line,
            "      AC_MSG_WARN([Failed: $AR $plugin_option rc])\n",
        )
        self.assertNotEqual(clear_mutant, probe)
        result = self.run_libtool_archive_probe(
            plugin_discovery_block(Path("libtool.m4")),
            clear_mutant,
            libtool_ranlib_block(Path("libtool.m4")),
            msys_host,
            (PLUGIN_CANDIDATES["msys"][0],),
            ar_status=1,
        )
        self.assertNotEqual(result["plugin_option"], "")
        self.assertIn("--plugin", result["RANLIB"])

        header = Path("gcc/config/i386/cygwin-w64.h")
        guarded = (
            "#if !defined (TARGET_AARCH64_MS_ABI)\n"
            "# undef MULTILIB_DEFAULTS\n"
            '# define MULTILIB_DEFAULTS { "m64" }\n'
            "#endif\n"
        )
        unguarded = (
            "# undef MULTILIB_DEFAULTS\n"
            '# define MULTILIB_DEFAULTS { "m64" }\n'
        )
        source = read_source(header)
        multilib_mutant = source.replace(guarded, unguarded)
        self.assertNotEqual(multilib_mutant, source)
        headers, defines = aarch64_target_config(msys_host)
        defaults, unused = multilib_defaults(
            headers,
            {define.split("=", 1)[0] for define in defines},
            {header: multilib_mutant},
        )
        self.assertEqual(jit_multilib_arguments(defaults), ("-m64",))

    def test_libgo_msys_stack_remains_dormant(self):
        for path in (Path("configure.ac"), Path("configure")):
            text = read_source(path)
            self.assertRegex(
                text,
                r"\*-\*-darwin\* \| \*-\*-cygwin\* \| \*-\*-msys\* "
                r"\| \*-\*-mingw\* \| bpf-\* \)\s+"
                r'unsupported_languages="\$unsupported_languages go"',
            )
            self.assertRegex(
                text,
                r"\*-\*-cygwin\* \| \*-\*-msys\* \| \*-\*-mingw\*\)"
                r"\s+noconfigdirs="
                r'"\$noconfigdirs target-libgo"',
            )
        self.assertNotIn(ROOT / "libgo/configure",
                         {ROOT / path for path in PLUGIN_CONFIGURES})


if __name__ == "__main__":
    unittest.main()
