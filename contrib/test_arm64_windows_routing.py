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

PLUGIN_CONFIGURES = (
    Path("configure"),
    Path("gcc/configure"),
    Path("libatomic/configure"),
    Path("libbacktrace/configure"),
    Path("libcc1/configure"),
    Path("libffi/configure"),
    Path("libgfortran/configure"),
    Path("libgm2/configure"),
    Path("libgomp/configure"),
    Path("libgrust/configure"),
    Path("libiberty/configure"),
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

PLUGIN_NAMES = (
    "liblto_plugin.so",
    "liblto_plugin-0.dll",
    "cyglto_plugin-0.dll",
    "msys-lto_plugin.dll",
    "msys-lto_plugin-0.dll",
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


def plugin_names(path):
    matches = re.findall(
        r'^plugin_names="([^"]+)"$', read_source(path), re.MULTILINE
    )
    if len(matches) != 1:
        raise AssertionError(
            f"expected one plugin_names assignment in {path}, "
            f"got {len(matches)}"
        )
    return tuple(matches[0].split())


def discovered_plugin(names, available):
    return next((name for name in names if name in available), None)


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


def effective_asm_spec(headers, initial_macros):
    macros = set(initial_macros)
    value = None
    origin = None

    for header in headers:
        lines = read_source(Path("gcc/config") / header).splitlines()
        relevant = [
            index
            for index, line in enumerate(lines)
            if re.match(r"^\s*#\s*(?:define|undef)\s+ASM_SPEC\b", line)
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
                if macro == "ASM_SPEC":
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
                if macro == "ASM_SPEC":
                    value = ast.literal_eval(definition.group(2))
                    origin = header

    return value, origin


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

    def test_plugin_routing_and_generated_copies(self):
        expected_consumers = {ROOT / path for path in PLUGIN_CONFIGURES}
        actual_consumers = {
            path
            for path in ROOT.rglob("configure")
            if re.search(
                r'^plugin_names="', path.read_text(encoding="utf-8"),
                re.MULTILINE,
            )
        }
        self.assertEqual(actual_consumers, expected_consumers)

        for path in PLUGIN_CONTROLLERS + PLUGIN_CONFIGURES:
            with self.subTest(path=path):
                self.assertEqual(plugin_names(path), PLUGIN_NAMES)

        sonames = {
            "aarch64-pc-msys": "msys-lto_plugin.dll",
            "x86_64-pc-msys": "msys-lto_plugin.dll",
            "i686-pc-msys": "msys-lto_plugin.dll",
            "aarch64-pc-cygwin": "cyglto_plugin.dll",
            "aarch64-w64-mingw32": "liblto_plugin.dll",
            "aarch64-pc-msy": "liblto_plugin.so",
        }
        for host, expected in sonames.items():
            with self.subTest(host=host):
                self.assertEqual(self.host_lto_plugin_soname(host), expected)

        for available in PLUGIN_NAMES:
            self.assertEqual(
                discovered_plugin(PLUGIN_NAMES, {available}), available
            )
        self.assertEqual(
            discovered_plugin(
                PLUGIN_NAMES,
                {"msys-lto_plugin.dll", "msys-lto_plugin-0.dll"},
            ),
            "msys-lto_plugin.dll",
        )
        self.assertIsNone(discovered_plugin(PLUGIN_NAMES, {"missing"}))

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
