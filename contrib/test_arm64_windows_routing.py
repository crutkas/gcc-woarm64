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
import difflib
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

# The call sites that turn a discovered plugin option into the AR and
# RANLIB makefile variables.  Both the hand written source and the shipped
# script are listed, because the script is what actually runs.
PLUGIN_WIRING_SOURCES = (
    Path("configure.ac"),
    Path("configure"),
    Path("libiberty/configure.ac"),
    Path("libiberty/configure"),
)

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


def read_blob_bytes(path):
    return subprocess.run(
        ["git", "-C", ROOT, "cat-file", "blob", f"HEAD:{path.as_posix()}"],
        check=True,
        stdout=subprocess.PIPE,
    ).stdout


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


def plugin_helpers_block(path, source=None):
    """The shell helpers the probes call, as they appear in the source.

    They sit between the discovery loop and the archiver checks, so neither
    of the other extractors covers them.  A runner that omits them would
    silently exercise an empty option and prove nothing.
    """
    text = read_source(path) if source is None else source
    start = text.index("# Quote a value so a later")
    definitions = [
        match.start()
        for match in re.finditer(r"^func_plugin_\w+ \(\)$", text, re.MULTILINE)
    ]
    end = text.index("\n}\n", definitions[-1]) + len("\n}\n")
    return text[start:end].rstrip()


def gcc_plugin_archive_probe_block(path, source=None):
    text = read_source(path) if source is None else source
    discovery = plugin_discovery_block(path, text)
    search_from = text.index(discovery) + len(discovery)
    start = text.index('if test "${AR}" = "" ; then', search_from)
    end = text.index(
        'if test -n "$plugin_option"; then\n  $1="$plugin_option"',
        start,
    )
    return text[start:end].rstrip()


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
        r'test "\$RANLIB" != ":"; then\n.*?^fi$',
        text[search_from:],
        re.MULTILINE | re.DOTALL,
    )
    if match is None:
        raise AssertionError(f"libtool RANLIB block not found in {path}")
    return match.group(0)


def libtool_archive_templates_block(path, source=None):
    text = read_source(path) if source is None else source
    start = text.index(
        "# Determine commands to create old-style static archives."
    )
    end = text.index("\ncase $host_os in", start)
    return text[start:end].rstrip()


def normalized_libtool_block(block):
    return "\n".join(
        line
        for line in block.splitlines()
        if "Failed: $AR" not in line
        and "Failed: $RANLIB" not in line
    )


def plugin_make_wiring_block(path, source=None):
    """The configure body that feeds PLUGIN_OPTION through the helper.

    Pinning func_plugin_make_quote and the makefile recipe only protects a
    build if the call site actually reaches the helper, so the wiring is
    extracted here and executed rather than assumed.
    """
    text = read_source(path) if source is None else source
    marker = "AR_PLUGIN_OPTION=\nRANLIB_PLUGIN_OPTION=\n"
    starts = [match.start() for match in re.finditer(re.escape(marker), text)]
    if len(starts) != 1:
        raise AssertionError(
            f"expected one plugin make wiring block in {path}, "
            f"got {len(starts)}"
        )
    # The block ends at the first column zero `fi', which closes the
    # `if test -n "$PLUGIN_OPTION"' that opens it; every inner `fi' is
    # indented.  Anchoring on structure rather than on one of the
    # assignments keeps a reverted assignment reportable as a failed
    # assertion instead of an extraction error.
    end = text.index("\nfi\n", starts[0]) + len("\nfi\n")
    return text[starts[0]:end].rstrip()


def normalized_wiring_block(block):
    """Reduce a wiring block to the form shared by all four call sites.

    AC_SUBST expands to nothing in the shipped script and AC_MSG_WARN
    expands to a two line diagnostic, so both are folded away; everything
    that decides which value reaches make is left untouched.
    """
    lines = []
    for line in block.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("AC_SUBST("):
            continue
        if (
            stripped.startswith("AC_MSG_WARN(")
            or "WARNING: plugin path is not representable" in stripped
        ):
            if lines[-1:] != ["<warning>"]:
                lines.append("<warning>")
            continue
        lines.append(line)
    return "\n".join(lines)


def wiring_runnable(block):
    """Turn an extracted wiring block into text a shell can run.

    AC_SUBST is an autoconf directive with no run time effect -- the
    shipped configure carries a blank line where it sits -- so dropping it
    keeps the executed text faithful to what a build really runs.
    """
    return shell_ready(
        "\n".join(
            line
            for line in block.splitlines()
            if not line.strip().startswith("AC_SUBST(")
        )
    )


def expected_make_quoted(value):
    """An independent model of the accept and refuse contract.

    The shell helper is the implementation under test, so the expected
    text is derived here instead of by running it; otherwise the wiring
    cases would only prove the helper agrees with itself.
    """
    if any(character in value for character in ("\n", "$", "`", '"')):
        return ""
    quoted = "'" + value.replace("'", "'\\''") + "'"
    return quoted.replace("$", "$$").replace("#", "\\#")


M4_RESIDUE = re.compile(r"\b(?:AC_[A-Z_]+|AS_[A-Z_]+|_LT_[A-Z_]+|m4_\w+)\s*\(|^dnl\b",
                        re.MULTILINE)

# Every mutation instance executed by test_required_mutation_controls
# records its name here so the documented count can be reconciled with the
# count the suite actually runs.
MUTATION_LEDGER = []
EXECUTED_MUTATION_INSTANCES = 58


def shell_ready(block, error_status=97):
    """Turn an extracted m4 block into text a shell can actually run.

    Neutralisation keys off the macro *name* rather than a full literal
    line, so a mutation that edits the text inside AC_MSG_WARN cannot leave
    raw m4 behind and make the mutant abort for an unrelated reason.
    """
    block = re.sub(
        r"^(\s*)AC_MSG_ERROR\(\[.*?\]\)\s*$",
        lambda m: f"{m.group(1)}return {error_status}",
        block,
        flags=re.MULTILINE | re.DOTALL,
    )
    block = re.sub(
        r"^(\s*)AC_MSG_WARN\(\[[^\n]*\]\)\s*$",
        lambda m: f"{m.group(1)}:",
        block,
        flags=re.MULTILINE,
    )
    return block


def assert_no_m4_residue(testcase, script):
    leftover = M4_RESIDUE.search(script)
    if leftover is not None:
        testcase.fail(
            "script still contains unexpanded m4 and would fail for an "
            f"unrelated reason: {leftover.group(0)!r}"
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


REAL_SH = shutil.which("sh")
REAL_MAKE = shutil.which("make")


def real_make_available():
    """True only for a genuine POSIX shell plus GNU make.

    A non-GNU make would not implement the recursive FLAGS_TO_PASS
    forwarding this test depends on, so guessing from the name alone
    would produce a misleading failure rather than a clean skip.
    """
    if not REAL_SH or not REAL_MAKE:
        return False
    try:
        banner = subprocess.run(
            [REAL_MAKE, "--version"], capture_output=True, text=True
        ).stdout
    except OSError:
        return False
    return "GNU Make" in banner


REAL_MAKE_LOGS = (
    "ar-direct.log",
    "ranlib-direct.log",
    "ar-fwd.log",
    "ranlib-fwd.log",
)

REAL_MAKE_SIDE_EFFECTS = ("injected", "inj2", "inj3", "inj4")

# Path shapes fed through the real recipe.  "accept" means the option has
# to survive as one exact argv element; "refuse" means the controller has
# to drop it, because libiberty forwards AR inside double quotes where
# these characters would still be expanded by the sub-make's shell.
REAL_MAKE_PATHS = (
    ("plain", "/plugins/lto.so", "accept"),
    ("space", "/plugin path/lto.so", "accept"),
    ("glob-star", "/plug*ins/lto.so", "accept"),
    ("glob-question", "/plug?ins/lto.so", "accept"),
    ("glob-bracket", "/plug[ab]ins/lto.so", "accept"),
    ("brace", "/plug{a,b}ins/lto.so", "accept"),
    ("semicolon", "/meta;touch injected;#/lto.so", "accept"),
    ("hash", "/plug#ins/lto.so", "accept"),
    ("double-hash", "/plug##ins/lto.so", "accept"),
    ("single-quote", "/plug'ins/lto.so", "accept"),
    ("backslash", "/back\\slash/lto.so", "accept"),
    ("redirect-and-pipe", "/a&b|c>d<e/lto.so", "accept"),
    ("leading-dash", "-/plugins/lto.so", "accept"),
    ("tab", "/plug\tins/lto.so", "accept"),
    ("bang", "/plug!ins/lto.so", "accept"),
    ("tilde", "~/plugins/lto.so", "accept"),
    ("parens", "/plug(ins)/lto.so", "accept"),
    ("space-and-glob", "/space dir/glob*/lto.so", "accept"),
    ("percent", "/plug%ins/lto.so", "accept"),
    ("colon", "/plug:ins/lto.so", "accept"),
    ("equals", "/plug=ins/lto.so", "accept"),
    ("command-substitution", "/meta$(touch inj2)/lto.so", "refuse"),
    ("backtick", "/meta`touch inj3`/lto.so", "refuse"),
    ("double-quote", '/met"a/lto.so', "refuse"),
    ("newline", "/new\nline/lto.so", "refuse"),
)


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

    def run_shell_script(self, script, cwd, environment):
        script_path = Path(cwd) / "routing-probe.sh"
        script_path.write_text(script, encoding="utf-8", newline="\n")
        return subprocess.run(
            [self.shell, script_path.name],
            cwd=cwd,
            env=environment,
            capture_output=True,
            text=True,
        )

    def read_nul_arguments(self, path):
        if not path.exists():
            return ()
        parts = path.read_bytes().split(b"\0")
        self.assertEqual(parts.pop(), b"")
        return tuple(part.decode("utf-8") for part in parts)

    def plugin_discovery_result(
        self, block, host, available, target=None, plugin_dir="/plugins"
    ):
        environment = os.environ.copy()
        environment.update(
            {
                "AVAILABLE": " ".join(available),
                "INJECTION_FILE": "injected",
                "PLUGIN_DIR": plugin_dir,
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
    *" $name "*) printf "%s/%s\n" "$PLUGIN_DIR" "$name" ;;
    *) printf "%s\n" "$name" ;;
  esac
}
CC=fake_cc
CFLAGS=
''' + block + r'''
printf "%s\n" "$plugin_option"
'''
        with tempfile.TemporaryDirectory() as directory:
            result = self.run_shell_script(script, directory, environment)
        return result

    def run_plugin_discovery(
        self, block, host, available, target=None, plugin_dir="/plugins"
    ):
        result = self.plugin_discovery_result(
            block, host, available, target, plugin_dir
        )
        self.assertEqual(
            result.returncode,
            0,
            f"plugin discovery failed: {result.stderr}",
        )
        self.assertEqual(result.stderr, "")
        return result.stdout.strip()

    def run_gcc_plugin_archive_probe(
        self,
        discovery,
        archive_probe,
        host,
        available,
        plugin_dir,
        ar_status=0,
        ar_command="fake_ar --configured-ar-argument",
        helpers=None,
    ):
        if helpers is None:
            helpers = plugin_helpers_block(Path("config/gcc-plugin.m4"))
        archive_probe = shell_ready(archive_probe)
        assert_no_m4_residue(self, archive_probe)
        assert_no_m4_residue(self, discovery)
        assert_no_m4_residue(self, helpers)
        environment = os.environ.copy()
        environment.update(
            {
                "AR_COMMAND": ar_command,
                "AR_STATUS": str(ar_status),
                "AVAILABLE": " ".join(available),
                "INJECTION_FILE": "injected",
                "PLUGIN_DIR": plugin_dir,
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
     *" $name "*) printf "%s/%s\n" "$PLUGIN_DIR" "$name" ;;
    *) printf "%s\n" "$name" ;;
  esac
}
fake_ar ()
{
  printf '%s\000' "$@" >> ar.log
  return "$AR_STATUS"
}
run_controller ()
{
  CC=fake_cc
  CFLAGS=
  AR=${AR_COMMAND}
''' + discovery + "\n" + helpers + "\n" + archive_probe + r'''
  printf "plugin_option=%s\n" "$plugin_option"
}
run_controller
printf "controller_status=%s\n" "$?"
'''
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            result = self.run_shell_script(script, temp, environment)
            arguments = self.read_nul_arguments(temp / "ar.log")
        values = dict(
            line.split("=", 1) for line in result.stdout.splitlines()
        )
        return result, values, arguments

    def run_libtool_archive_probe(
        self,
        discovery,
        archive_probe,
        ranlib_probe,
        archive_templates,
        host,
        available,
        ar_status,
        ar_advertises=True,
        ranlib_status=0,
        ranlib_advertises=True,
        plugin_dir="/plugins",
        ar_command="fake_ar --configured-ar-argument",
        ranlib_command="fake_ranlib --configured-ranlib-argument",
        helpers=None,
    ):
        if helpers is None:
            helpers = plugin_helpers_block(Path("libtool.m4"))
        archive_probe = shell_ready(archive_probe)
        ranlib_probe = shell_ready(ranlib_probe)
        assert_no_m4_residue(self, archive_probe)
        assert_no_m4_residue(self, ranlib_probe)
        assert_no_m4_residue(self, discovery)
        assert_no_m4_residue(self, helpers)
        assert_no_m4_residue(self, archive_templates)
        environment = os.environ.copy()
        environment.update(
            {
                "AR_ADVERTISES": "yes" if ar_advertises else "no",
                "AR_COMMAND": ar_command,
                "AR_STATUS": str(ar_status),
                "AVAILABLE": " ".join(available),
                "INJECTION_FILE": "injected",
                "PLUGIN_DIR": plugin_dir,
                "RANLIB_ADVERTISES": (
                    "yes" if ranlib_advertises else "no"
                ),
                "RANLIB_COMMAND": ranlib_command,
                "RANLIB_STATUS": str(ranlib_status),
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
   *" $name "*) printf "%s/%s\n" "$PLUGIN_DIR" "$name" ;;
    *) printf "%s\n" "$name" ;;
  esac
}
fake_ar ()
{
  for argument
  do
    if test "$argument" = "--help"; then
      printf '%s\000' "$@" >> "$AR_HELP_LOG"
      test "$AR_ADVERTISES" = yes && printf "%s\n" "--plugin"
      return 0
    fi
  done
  printf '%s\000' "$@" >> "$AR_LOG"
  return "$AR_STATUS"
}
fake_ranlib ()
{
  for argument
  do
    if test "$argument" = "--help"; then
      printf '%s\000' "$@" >> "$RANLIB_HELP_LOG"
      test "$RANLIB_ADVERTISES" = yes && printf "%s\n" "--plugin"
      return 0
    fi
  done
  printf '%s\000' "$@" >> "$RANLIB_LOG"
  return "$RANLIB_STATUS"
}
CC=fake_cc
CFLAGS=
ECHO=echo
SED=sed
sed_quote_subst='s/\(["`$\\]\)/\\\1/g'
AR=${AR_COMMAND}
RANLIB=${RANLIB_COMMAND}
AR_LOG=ar-probe.log
RANLIB_LOG=ranlib-probe.log
AR_HELP_LOG=ar-help.log
RANLIB_HELP_LOG=ranlib-help.log
host_os=msys
AR_FLAGS=rc
oldlib=conftest.a
oldobjs=' conftest.o'
''' + discovery + r'''
''' + helpers + r'''
ar_plugin_option=
ranlib_plugin_option=
''' + archive_probe + "\n" + ranlib_probe + "\n" + archive_templates + r'''
ar_command_status=not-run
ranlib_command_status=not-run
if test -n "$ar_plugin_option"; then
  AR_LOG=ar-command.log
  ar_command=${old_archive_cmds%%~*}
  # libtool's func_execute_cmds expands the template with one eval and then
  # runs the result through func_show_eval, which evals it again.  Both are
  # reproduced here so the archive command is exercised exactly as built.
  eval ar_command=\"$ar_command\"
  eval "$ar_command"
  ar_command_status=$?
fi
if test -n "$ranlib_plugin_option"; then
  RANLIB_LOG=ranlib-command.log
  ranlib_command=${old_archive_cmds#*~}
  eval ranlib_command=\"$ranlib_command\"
  eval "$ranlib_command"
  ranlib_command_status=$?
fi
printf "plugin_option=%s\n" "$plugin_option"
printf "AR=%s\n" "$AR"
printf "RANLIB=%s\n" "$RANLIB"
printf "ar_plugin_option=%s\n" "$ar_plugin_option"
printf "ranlib_plugin_option=%s\n" "$ranlib_plugin_option"
printf "old_archive_cmds=%s\n" "$old_archive_cmds"
printf "ar_command_status=%s\n" "$ar_command_status"
printf "ranlib_command_status=%s\n" "$ranlib_command_status"
if test -e injected; then
  printf "injected=yes\n"
else
  printf "injected=no\n"
fi
'''
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            glob_dir = temp / "dir" / "glob-expanded"
            glob_dir.mkdir(parents=True)
            for name in ALL_PLUGIN_NAMES:
                (glob_dir / name).touch()
            result = self.run_shell_script(script, temp, environment)
            arguments = {
                "ar_probe": self.read_nul_arguments(
                    temp / "ar-probe.log"
                ),
                "ranlib_probe": self.read_nul_arguments(
                    temp / "ranlib-probe.log"
                ),
                "ar_command": self.read_nul_arguments(
                    temp / "ar-command.log"
                ),
                "ranlib_command": self.read_nul_arguments(
                    temp / "ranlib-command.log"
                ),
                "ar_help": self.read_nul_arguments(temp / "ar-help.log"),
                "ranlib_help": self.read_nul_arguments(
                    temp / "ranlib-help.log"
                ),
            }
            injected = (temp / "injected").exists()
        values = dict(
            line.split("=", 1)
            for line in result.stdout.splitlines()
            if "=" in line
        )
        values.update(arguments)
        values["injected_file"] = injected
        values["returncode"] = result.returncode
        values["stderr"] = result.stderr
        return values

    def run_plugin_make_wiring(
        self,
        block,
        plugin_option,
        ar_advertises=True,
        ranlib_advertises=True,
    ):
        """Execute the real configure wiring, file in and file out.

        The plugin path never crosses a command line and the two results
        come back NUL separated, so no layer between the test and the
        shell can alter either end.  Any file the run did not expect is
        returned as well, so an injected command is observed rather than
        inferred.
        """
        script = wiring_runnable(block)
        assert_no_m4_residue(self, script)
        expected_files = {
            "in.txt",
            "out.log",
            "config.log",
            "fake_ar",
            "fake_ranlib",
            "routing-probe.sh",
        }
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            (work / "in.txt").write_bytes(plugin_option.encode())
            for name, advertises in (
                ("fake_ar", ar_advertises),
                ("fake_ranlib", ranlib_advertises),
            ):
                usage = (
                    "usage: fake tool [--plugin PLUGIN]"
                    if advertises
                    else "usage: fake tool"
                )
                stub = work / name
                stub.write_bytes(
                    ("#!/bin/sh\nprintf '%s\\n' '" + usage + "'\n"
                     "exit 0\n").encode()
                )
                stub.chmod(0o755)
            full = (
                plugin_helpers_block(Path("config/gcc-plugin.m4"))
                + "\nas_echo='printf %s\\n'\n"
                "as_me=configure\n"
                "exec 5>config.log\n"
                "PLUGIN_OPTION=$(cat in.txt; printf X)\n"
                "PLUGIN_OPTION=${PLUGIN_OPTION%X}\n"
                "AR=./fake_ar\n"
                "RANLIB=./fake_ranlib\n"
                + script
                + "\nprintf '%s\\000%s\\000' \"$AR_PLUGIN_OPTION\""
                ' "$RANLIB_PLUGIN_OPTION" > out.log\n'
            )
            result = self.run_shell_script(full, work, os.environ.copy())
            self.assertEqual(
                result.returncode, 0, f"wiring failed: {result.stderr}"
            )
            values = self.read_nul_arguments(work / "out.log")
            unexpected = sorted(
                entry.name
                for entry in work.iterdir()
                if entry.name not in expected_files
            )
        self.assertEqual(len(values), 2)
        return values, unexpected

    def test_plugin_make_wiring_runs_the_quoting_helper(self):
        """A pinned helper is worthless if the call site bypasses it.

        The suite already pins func_plugin_make_quote and the makefile
        recipe, but a wiring line that assigned $PLUGIN_OPTION straight to
        AR_PLUGIN_OPTION would satisfy both while shipping the raw value.
        These cases execute the wiring out of all four call sites, so the
        line that reaches the helper is itself under test.
        """
        blocks = {
            path: plugin_make_wiring_block(path)
            for path in PLUGIN_WIRING_SOURCES
        }
        reference = normalized_wiring_block(blocks[Path("configure.ac")])

        benign = "--plugin=/plugins/liblto_plugin.dll"
        hostile = (
            "--plugin=/plug ins/meta;touch INJECTED;"
            "#/glob*/lib'lto.dll"
        )
        refused = "--plugin=/plugins/$(touch INJECTED)/liblto_plugin.dll"
        self.assertNotEqual(expected_make_quoted(hostile), "")
        self.assertEqual(expected_make_quoted(refused), "")

        # The empty option is the default configuration rather than an
        # edge case: most builds discover no plugin at all, so it is
        # listed first and asserted byte exactly.
        cases = (
            ("empty", "", True, True, "", ""),
            (
                "benign",
                benign,
                True,
                True,
                expected_make_quoted(benign),
                expected_make_quoted(benign),
            ),
            (
                "hostile",
                hostile,
                True,
                True,
                expected_make_quoted(hostile),
                expected_make_quoted(hostile),
            ),
            ("refused", refused, True, True, "", ""),
            (
                "ranlib-has-no-plugin-support",
                benign,
                True,
                False,
                expected_make_quoted(benign),
                "",
            ),
            (
                "ar-has-no-plugin-support",
                benign,
                False,
                True,
                "",
                expected_make_quoted(benign),
            ),
            ("neither-tool-supports-plugins", benign, False, False, "", ""),
        )

        for path, block in blocks.items():
            with self.subTest(path=path):
                for name, option, ar, ranlib, want_ar, want_ranlib in cases:
                    with self.subTest(case=name):
                        values, unexpected = self.run_plugin_make_wiring(
                            block, option, ar, ranlib
                        )
                        self.assertEqual(values, (want_ar, want_ranlib))
                        self.assertEqual(unexpected, [])
                        if option and want_ar:
                            self.assertNotEqual(values[0], option)

        # The text is pinned only after the behaviour has been shown on
        # every call site, so a wiring line that stopped calling the
        # helper is reported as a wrong value first and as a text
        # mismatch second, rather than the other way around.
        for required in (
            "plugin_make_arg=$PLUGIN_OPTION; func_plugin_make_quote",
            "AR_PLUGIN_OPTION=$plugin_make_quoted",
            "RANLIB_PLUGIN_OPTION=$plugin_make_quoted",
        ):
            self.assertIn(required, reference)
        for path, block in blocks.items():
            with self.subTest(pinned=path):
                self.assertEqual(normalized_wiring_block(block), reference)

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
        gcc_archive_probe = gcc_plugin_archive_probe_block(
            Path("config/gcc-plugin.m4")
        )
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
            if "ar_plugin_option=" not in read_source(path)
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
        controller_templates = libtool_archive_templates_block(
            Path("libtool.m4")
        )
        for path in LIBTOOL_PLUGIN_CONFIGURES:
            with self.subTest(libtool_consumer=path):
                self.assertEqual(
                    normalized_libtool_block(
                        libtool_archive_probe_block(path)
                    ),
                    normalized_libtool_block(controller_probe),
                )
                self.assertEqual(
                    normalized_libtool_block(libtool_ranlib_block(path)),
                    normalized_libtool_block(controller_ranlib),
                )
                self.assertEqual(
                    libtool_archive_templates_block(path),
                    controller_templates,
                )
                text = read_source(path)
                self.assertNotIn("$AR $plugin_option rc", text)
                self.assertNotIn("$RANLIB $plugin_option conftest.a", text)

        for path in GCC_PLUGIN_CONFIGURES:
            text = read_source(path)
            with self.subTest(gcc_plugin_consumer=path):
                # The probe has to run the option through an eval with
                # pathname expansion disabled, because that is what the
                # archive templates do at link time.  Passing it as a
                # bare or double quoted word would validate a shape that
                # is never actually executed.
                self.assertIn(
                    "  plugin_quote_arg=$plugin_option; func_plugin_quote\n"
                    '  (set -f; eval "${AR} $plugin_quoted rc conftest.a '
                    'conftest.c")\n',
                    text,
                )
                for unsafe in (
                    '${AR} "$plugin_option" rc conftest.a conftest.c',
                    "${AR} $plugin_option rc conftest.a conftest.c",
                    'eval "${AR} $plugin_option rc',
                ):
                    self.assertNotIn(unsafe, text)

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
                        selected, f"--plugin=/plugins/{candidates[0]}"
                    )
                    for candidate in candidates:
                        selected = self.run_plugin_discovery(
                            block, host, (candidate,)
                        )
                        self.assertEqual(
                            selected, f"--plugin=/plugins/{candidate}"
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

        selected_name = PLUGIN_CANDIDATES["msys"][0]
        hostile_directories = (
            "/plugins/space dir/glob[*]?$dollar&meta",
            "space dir/glob*",
            '/plugins/meta;$(touch "$INJECTION_FILE")&name',
            "/plugins/quote'part",
        )
        for plugin_dir in hostile_directories:
            expected_option = f"--plugin={plugin_dir}/{selected_name}"
            with self.subTest(controller="gcc", plugin_dir=plugin_dir):
                result, values, arguments = (
                    self.run_gcc_plugin_archive_probe(
                        gcc_controller,
                        gcc_archive_probe,
                        msys_host,
                        (selected_name,),
                        plugin_dir,
                    )
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stderr, "")
                self.assertEqual(values["controller_status"], "0")
                self.assertEqual(
                    values["plugin_option"], expected_option
                )
                self.assertEqual(
                    arguments,
                    (
                        "--configured-ar-argument",
                        expected_option,
                        "rc",
                        "conftest.a",
                        "conftest.c",
                    ),
                )

        rejected = self.run_libtool_archive_probe(
            libtool_controller,
            controller_probe,
            controller_ranlib,
            controller_templates,
            msys_host,
            (selected_name,),
            ar_status=1,
        )
        self.assertEqual(rejected["plugin_option"], "")
        self.assertEqual(
            rejected["AR"], "fake_ar --configured-ar-argument"
        )
        self.assertEqual(
            rejected["RANLIB"], "fake_ranlib --configured-ranlib-argument"
        )
        self.assertEqual(rejected["ranlib_probe"], ())
        self.assertEqual(rejected["ranlib_command"], ())

        unsupported = self.run_libtool_archive_probe(
            libtool_controller,
            controller_probe,
            controller_ranlib,
            controller_templates,
            msys_host,
            (selected_name,),
            ar_status=0,
            ar_advertises=False,
        )
        self.assertEqual(unsupported["plugin_option"], "")
        self.assertEqual(unsupported["ar_probe"], ())
        self.assertEqual(unsupported["ranlib_probe"], ())

        ranlib_rejected = self.run_libtool_archive_probe(
            libtool_controller,
            controller_probe,
            controller_ranlib,
            controller_templates,
            msys_host,
            (selected_name,),
            ar_status=0,
            ranlib_status=1,
        )
        self.assertNotEqual(ranlib_rejected["ar_plugin_option"], "")
        self.assertEqual(ranlib_rejected["ranlib_plugin_option"], "")
        self.assertEqual(ranlib_rejected["ranlib_command"], ())

        for plugin_dir in hostile_directories:
            with self.subTest(controller="libtool", plugin_dir=plugin_dir):
                accepted = self.run_libtool_archive_probe(
                    libtool_controller,
                    controller_probe,
                    controller_ranlib,
                    controller_templates,
                    msys_host,
                    (selected_name,),
                    ar_status=0,
                    plugin_dir=plugin_dir,
                )
                option = f"--plugin={plugin_dir}/{selected_name}"
                self.assertEqual(accepted["returncode"], 0)
                self.assertEqual(accepted["stderr"], "")
                self.assertEqual(accepted["plugin_option"], option)
                self.assertEqual(
                    accepted["AR"],
                    "fake_ar --configured-ar-argument",
                )
                self.assertEqual(
                    accepted["RANLIB"],
                    "fake_ranlib --configured-ranlib-argument",
                )
                expected_ar = (
                    "--configured-ar-argument",
                    option,
                    "rc",
                    "conftest.a",
                    "conftest.o",
                )
                expected_ar_probe = expected_ar[:-1] + ("conftest.c",)
                expected_ranlib = (
                    "--configured-ranlib-argument",
                    option,
                    "conftest.a",
                )
                self.assertEqual(
                    accepted["ar_probe"], expected_ar_probe
                )
                self.assertEqual(accepted["ranlib_probe"], expected_ranlib)
                self.assertEqual(accepted["ar_command"], expected_ar)
                self.assertEqual(
                    accepted["ranlib_command"], expected_ranlib
                )
                self.assertEqual(accepted["ar_command_status"], "0")
                self.assertEqual(
                    accepted["ranlib_command_status"], "0"
                )
                self.assertEqual(accepted["injected"], "no")
                self.assertFalse(accepted["injected_file"])

    def real_make_quote(self, work, path):
        """Run the committed func_plugin_make_quote, file in and file out.

        The value never crosses a command line, so no layer between the
        test and the shell can alter it.
        """
        (work / "in.txt").write_bytes(("--plugin=" + path).encode())
        script = (
            plugin_helpers_block(Path("config/gcc-plugin.m4"))
            + "\nplugin_make_arg=$(cat in.txt; printf X)\n"
            "plugin_make_arg=${plugin_make_arg%X}\n"
            "func_plugin_make_quote\n"
            'printf %s "$plugin_make_quoted" > out.txt\n'
        )
        (work / "quote.sh").write_bytes(script.encode())
        subprocess.run(
            [REAL_SH, "quote.sh"], cwd=work, check=True, capture_output=True
        )
        return (work / "out.txt").read_bytes().decode()

    @unittest.skipUnless(
        real_make_available(), "requires a POSIX shell and GNU make"
    )
    def test_real_make_preserves_plugin_option_argv(self):
        """Expand the committed recipe with real make, not a model of it.

        The archiver and ranlib are recording stubs that dump their argv
        NUL separated, so a split word, a glob expansion or an injected
        command is visible directly rather than inferred.
        """
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            (work / "bin").mkdir()
            for name, variable in (
                ("fake_ar", "AR_LOG"),
                ("fake_ranlib", "RANLIB_LOG"),
            ):
                stub = work / "bin" / name
                stub.write_bytes(
                    "#!/bin/sh\n"
                    f'printf \'%s\\000\' "$@" >> "${variable}"\n'
                    "exit 0\n".encode()
                )
                stub.chmod(0o755)

            for name, path, expectation in REAL_MAKE_PATHS:
                with self.subTest(path=name):
                    option = self.real_make_quote(work, path)
                    if expectation == "refuse":
                        self.assertEqual(option, "")
                        continue
                    self.assertNotEqual(option, "")
                    observed, effects, argument = self.real_make_case(
                        work, option, path
                    )
                    self.assertEqual(effects, [])
                    self.assertEqual(
                        observed["ar-direct.log"],
                        ("--configured-ar", argument, "rc",
                         "libtest.a", "foo.o"),
                    )
                    self.assertEqual(
                        observed["ranlib-direct.log"],
                        ("--configured-ranlib", argument, "libtest.a"),
                    )
                    self.assertEqual(
                        observed["ar-fwd.log"],
                        ("--configured-ar", argument, "rc",
                         "libsub.a", "bar.o"),
                    )
                    self.assertEqual(
                        observed["ranlib-fwd.log"],
                        ("--configured-ranlib", argument, "libsub.a"),
                    )

            # Negative control.  Substituting the option the way the base
            # tree does must still be observably unsafe here, otherwise
            # the assertions above would pass for a scaffolding reason
            # rather than because the controller quotes the option.
            for path, effect in (
                ("/meta;touch injected;#/lto.so", "injected"),
                ("/meta`touch inj3`/lto.so", "inj3"),
            ):
                with self.subTest(control=effect):
                    observed, effects, argument = self.real_make_case(
                        work, "--plugin=" + path, path
                    )
                    self.assertIn(effect, effects)
            with self.subTest(control="word-splitting"):
                path = "/plugin path/lto.so"
                observed, effects, argument = self.real_make_case(
                    work, "--plugin=" + path, path
                )
                self.assertEqual(effects, [])
                self.assertEqual(
                    observed["ar-direct.log"],
                    ("--configured-ar", "--plugin=/plugin", "path/lto.so",
                     "rc", "libtest.a", "foo.o"),
                )

            # An archiver without plugin support leaves the option empty,
            # which is the ordinary case.  Quoting the option inside the
            # recipe instead of at substitution time would hand ar an
            # empty argument on every such build, so pin the argv here.
            with self.subTest(control="empty-option"):
                observed, effects, argument = self.real_make_case(
                    work, "", ""
                )
                self.assertEqual(effects, [])
                self.assertEqual(
                    observed["ar-direct.log"],
                    ("--configured-ar", "rc", "libtest.a", "foo.o"),
                )
                self.assertEqual(
                    observed["ranlib-fwd.log"],
                    ("--configured-ranlib", "libsub.a"),
                )

    def real_make_case(self, work, option, path):
        """Expand the real recipe text with real make and return the outcome.

        Nothing here is modelled: the AR/RANLIB definitions, the
        FLAGS_TO_PASS forwarding lines and the archive recipe are the
        bytes committed in libiberty/Makefile.in, and GNU make performs
        the variable expansion and hands the words to a real shell.
        """
        makefile_in = read_source(Path("libiberty/Makefile.in")).split("\n")
        ar_line = makefile_in[52]
        ranlib_line = makefile_in[58]
        forward_ar = makefile_in[78]
        forward_ranlib = makefile_in[91]
        recipe = makefile_in[252:255]
        self.assertEqual(ar_line, "AR = @AR@ @AR_PLUGIN_OPTION@")
        self.assertEqual(
            ranlib_line, "RANLIB = @RANLIB@ @RANLIB_PLUGIN_OPTION@"
        )
        self.assertEqual(forward_ar, '\t"AR=$(AR)" \\')
        self.assertEqual(forward_ranlib, '\t"RANLIB=$(RANLIB)" \\')
        self.assertEqual(recipe[0], "\t$(AR) $(AR_FLAGS) $(TARGETLIB) \\")
        self.assertEqual(recipe[2], "\t$(RANLIB) $(TARGETLIB)")

        top = "\n".join(
            [
                ar_line.replace("@AR@", "fake_ar --configured-ar").replace(
                    "@AR_PLUGIN_OPTION@", option
                ),
                ranlib_line.replace(
                    "@RANLIB@", "fake_ranlib --configured-ranlib"
                ).replace("@RANLIB_PLUGIN_OPTION@", option),
                "AR_FLAGS = rc",
                "TARGETLIB = libtest.a",
                "OBJECTS = foo.o",
                "",
                "direct:",
                recipe[0],
                "\t  $(OBJECTS)",
                recipe[2],
                "",
                "forward:",
                "\t$(MAKE) --no-print-directory -f sub.mk \\",
                forward_ar,
                forward_ranlib,
                "\trun",
                "",
            ]
        )
        sub = "\n".join(
            [
                "AR_FLAGS = rc",
                "TARGETLIB = libsub.a",
                "OBJECTS = bar.o",
                "",
                "run:",
                recipe[0],
                "\t  $(OBJECTS)",
                recipe[2],
                "",
            ]
        )
        (work / "Makefile").write_bytes(top.encode())
        (work / "sub.mk").write_bytes(sub.encode())
        for name in REAL_MAKE_LOGS + REAL_MAKE_SIDE_EFFECTS:
            (work / name).unlink(missing_ok=True)

        for target, ar_log, ranlib_log in (
            ("direct", "ar-direct.log", "ranlib-direct.log"),
            ("forward", "ar-fwd.log", "ranlib-fwd.log"),
        ):
            subprocess.run(
                [
                    REAL_SH,
                    "-c",
                    "PATH=$PWD/bin:$PATH "
                    f"AR_LOG=$PWD/{ar_log} RANLIB_LOG=$PWD/{ranlib_log} "
                    f"make -s {target}",
                ],
                cwd=work,
                capture_output=True,
            )

        argument = "--plugin=" + path
        observed = {
            name: self.read_nul_arguments(work / name)
            for name in REAL_MAKE_LOGS
        }
        effects = [
            name
            for name in REAL_MAKE_SIDE_EFFECTS
            if (work / name).exists()
        ]
        return observed, effects, argument

    def mutate(self, name, source, old, new, occurrences=1):
        """Apply one surgical, accounted mutation to real source text.

        The mutation must match the expected number of times, must change
        only the lines it names, and must never disturb a diagnostic or
        remaining m4 line -- a mutant that stops the script for an unrelated
        reason proves nothing about the assertion it is meant to guard.
        """
        found = source.count(old)
        self.assertEqual(
            found,
            occurrences,
            f"{name}: expected {occurrences} occurrence(s) of the "
            f"mutated text, found {found}",
        )
        mutant = source.replace(old, new)
        self.assertNotEqual(mutant, source, f"{name}: mutation was a no-op")
        before = source.splitlines()
        after = mutant.splitlines()

        def diagnostics(lines):
            return sorted(
                line
                for line in lines
                if "AC_MSG" in line or "_LT_" in line or "dnl" in line
            )

        self.assertEqual(
            diagnostics(before),
            diagnostics(after),
            f"{name}: mutation altered a diagnostic or m4 line",
        )
        changed = sum(
            max(i2 - i1, j2 - j1)
            for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(
                None, before, after
            ).get_opcodes()
            if tag != "equal"
        )
        budget = occurrences * max(
            len(old.splitlines()), len(new.splitlines()), 1
        )
        self.assertLessEqual(
            changed,
            budget,
            f"{name}: mutation changed {changed} lines, budget {budget}",
        )
        self.assertNotIn(
            name,
            MUTATION_LEDGER,
            f"{name}: duplicate mutation name",
        )
        MUTATION_LEDGER.append(name)
        return mutant

    def record_mutation(self, name):
        self.assertNotIn(name, MUTATION_LEDGER, f"{name}: duplicate name")
        MUTATION_LEDGER.append(name)

    def test_required_mutation_controls(self):
        msys_host = self.canonical_target("arm64-pc-msys")
        linux_host = self.canonical_target("aarch64-pc-linux-gnu")
        selected_name = PLUGIN_CANDIDATES["msys"][0]
        space_dir = "space dir/glob*"
        payload_dir = '/plugins/meta;$(touch "$INJECTION_FILE")&name'

        for controller in PLUGIN_CONTROLLERS:
            tag = controller.name
            block = plugin_discovery_block(controller)
            loop_mutant = self.mutate(
                f"{tag}:discovery-singular-loop",
                block,
                "for plugin in $plugin_names; do",
                "for plugin in $plugin_name; do",
            )
            self.assertEqual(
                self.run_plugin_discovery(
                    loop_mutant,
                    msys_host,
                    (selected_name,),
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
            self.record_mutation(f"{tag}:discovery-host-gate")
            self.assertEqual(
                self.run_plugin_discovery(
                    gate_mutant,
                    linux_host,
                    (selected_name,),
                    target=msys_host,
                ),
                "--plugin=/plugins/msys-lto_plugin.dll",
            )

            for family in ("cygwin", "msys", "mingw"):
                actual_name, compat_name = PLUGIN_CANDIDATES[family]
                family_host = self.canonical_target(
                    {
                        "cygwin": "aarch64-pc-cygwin",
                        "msys": "arm64-pc-msys",
                        "mingw": "aarch64-w64-mingw32",
                    }[family]
                )
                name_mutant = self.mutate(
                    f"{tag}:discovery-current-name-{family}",
                    block,
                    actual_name + " ",
                    "",
                )
                self.assertEqual(
                    self.run_plugin_discovery(
                        name_mutant, family_host, (actual_name,)
                    ),
                    "",
                )
                compat_mutant = self.mutate(
                    f"{tag}:discovery-compat-name-{family}",
                    block,
                    " " + compat_name,
                    "",
                )
                self.assertEqual(
                    self.run_plugin_discovery(
                        compat_mutant, family_host, (compat_name,)
                    ),
                    "",
                )
                self.assertEqual(
                    self.run_plugin_discovery(
                        block, family_host, (compat_name,)
                    ),
                    f"--plugin=/plugins/{compat_name}",
                )

            expected = f"--plugin={space_dir}/{selected_name}"
            quote_mutants = (
                self.mutate(
                    f"{tag}:discovery-print-prog-comparison-quotes",
                    block,
                    'test "x$plugin_so" = "x$plugin"',
                    "test x$plugin_so = x$plugin",
                ),
                self.mutate(
                    f"{tag}:discovery-print-file-comparison-quotes",
                    block,
                    'test "x$plugin_so" != "x$plugin"',
                    "test x$plugin_so != x$plugin",
                ),
            )
            for quote_mutant in quote_mutants:
                result = self.plugin_discovery_result(
                    quote_mutant,
                    msys_host,
                    (selected_name,),
                    plugin_dir=space_dir,
                )
                self.assertFalse(
                    result.returncode == 0
                    and result.stderr == ""
                    and result.stdout.strip() == expected
                )

            scalar_mutant = self.mutate(
                f"{tag}:discovery-scalar-option",
                block,
                'plugin_option="--plugin=$plugin_so"',
                'plugin_option="--plugin $plugin_so"',
            )
            self.assertNotEqual(
                self.run_plugin_discovery(
                    scalar_mutant,
                    msys_host,
                    (selected_name,),
                    plugin_dir=space_dir,
                ),
                expected,
            )

        gcc_discovery = plugin_discovery_block(
            Path("config/gcc-plugin.m4")
        )
        gcc_probe = gcc_plugin_archive_probe_block(
            Path("config/gcc-plugin.m4")
        )
        gcc_expected = (
            "--configured-ar-argument",
            "*",
            f"--plugin={space_dir}/{selected_name}",
            "rc",
            "conftest.a",
            "conftest.c",
        )
        hostile_ar = "fake_ar --configured-ar-argument *"
        unused, gcc_values, gcc_arguments = (
            self.run_gcc_plugin_archive_probe(
                gcc_discovery,
                gcc_probe,
                msys_host,
                (selected_name,),
                space_dir,
                ar_command=hostile_ar,
            )
        )
        self.assertEqual(gcc_arguments, gcc_expected)

        for name, old, new in (
            (
                "gcc-plugin.m4:probe-scalar-quoting",
                'eval "${AR} $plugin_quoted rc conftest.a conftest.c"',
                'eval "${AR} $plugin_option rc conftest.a conftest.c"',
            ),
            (
                "gcc-plugin.m4:probe-pathname-expansion-guard",
                '(set -f; eval "${AR} $plugin_quoted rc'
                ' conftest.a conftest.c")',
                '(eval "${AR} $plugin_quoted rc conftest.a conftest.c")',
            ),
        ):
            gcc_probe_mutant = self.mutate(name, gcc_probe, old, new)
            unused, unused_values, mutant_arguments = (
                self.run_gcc_plugin_archive_probe(
                    gcc_discovery,
                    gcc_probe_mutant,
                    msys_host,
                    (selected_name,),
                    space_dir,
                    ar_command=hostile_ar,
                )
            )
            self.assertNotEqual(mutant_arguments, gcc_expected, name)

        gcc_clear_mutant = self.mutate(
            "gcc-plugin.m4:probe-clears-option-on-failure",
            gcc_probe,
            "    AC_MSG_WARN([Failed: $AR \"$plugin_option\" rc])\n"
            "    plugin_option=\n",
            "    AC_MSG_WARN([Failed: $AR \"$plugin_option\" rc])\n",
        )
        unused, mutant_values, unused_arguments = (
            self.run_gcc_plugin_archive_probe(
                gcc_discovery,
                gcc_clear_mutant,
                msys_host,
                (selected_name,),
                space_dir,
                ar_status=1,
            )
        )
        self.assertNotEqual(mutant_values["plugin_option"], "")
        unused, kept_values, unused_arguments = (
            self.run_gcc_plugin_archive_probe(
                gcc_discovery,
                gcc_probe,
                msys_host,
                (selected_name,),
                space_dir,
                ar_status=1,
            )
        )
        self.assertEqual(kept_values["plugin_option"], "")

        discovery = plugin_discovery_block(Path("libtool.m4"))
        probe = libtool_archive_probe_block(Path("libtool.m4"))
        ranlib = libtool_ranlib_block(Path("libtool.m4"))
        templates = libtool_archive_templates_block(Path("libtool.m4"))
        clear_line = (
            '      AC_MSG_WARN([Failed: $AR "$plugin_option" rc])\n'
            "      plugin_option=\n"
        )
        clear_mutant = self.mutate(
            "libtool.m4:ar-rejection-clears-plugin-option",
            probe,
            clear_line,
            '      AC_MSG_WARN([Failed: $AR "$plugin_option" rc])\n',
        )
        result = self.run_libtool_archive_probe(
            discovery,
            clear_mutant,
            ranlib,
            templates,
            msys_host,
            (selected_name,),
            ar_status=1,
        )
        self.assertNotEqual(result["plugin_option"], "")
        self.assertNotEqual(result["ranlib_probe"], ())

        def has_exact_libtool_arguments(result, plugin_dir):
            option = f"--plugin={plugin_dir}/{selected_name}"
            expected_ar = (
                "--configured-ar-argument",
                option,
                "rc",
                "conftest.a",
                "conftest.o",
            )
            expected_ar_probe = expected_ar[:-1] + ("conftest.c",)
            expected_ranlib = (
                "--configured-ranlib-argument",
                option,
                "conftest.a",
            )
            return (
                result["returncode"] == 0
                and result["stderr"] == ""
                and result["ar_probe"] == expected_ar_probe
                and result["ranlib_probe"] == expected_ranlib
                and result["ar_command"] == expected_ar
                and result["ranlib_command"] == expected_ranlib
                and not result["injected_file"]
            )

        def assert_libtool_mutant_fails(
            *,
            archive_probe=probe,
            ranlib_probe=ranlib,
            archive_templates=templates,
            plugin_dir=space_dir,
        ):
            mutant_result = self.run_libtool_archive_probe(
                discovery,
                archive_probe,
                ranlib_probe,
                archive_templates,
                msys_host,
                (selected_name,),
                ar_status=0,
                plugin_dir=plugin_dir,
            )
            self.assertFalse(
                has_exact_libtool_arguments(mutant_result, plugin_dir)
            )
            return mutant_result

        unquoted_ar_probe = self.mutate(
            "libtool.m4:ar-probe-scalar-quoting",
            probe,
            'eval "$AR $plugin_quoted rc conftest.a conftest.c"',
            'eval "$AR $plugin_option rc conftest.a conftest.c"',
        )
        assert_libtool_mutant_fails(archive_probe=unquoted_ar_probe)

        unquoted_ranlib_probe = self.mutate(
            "libtool.m4:ranlib-probe-scalar-quoting",
            ranlib,
            'eval "$RANLIB $plugin_quoted conftest.a"',
            'eval "$RANLIB $plugin_option conftest.a"',
        )
        assert_libtool_mutant_fails(ranlib_probe=unquoted_ranlib_probe)

        # Dropping `set -f' lets the shell glob the archiver's own words
        # after `eval' has split them.
        glob_ar = "fake_ar --configured-ar-argument *"
        glob_ranlib = "fake_ranlib --configured-ranlib-argument *"
        baseline_glob = self.run_libtool_archive_probe(
            discovery,
            probe,
            ranlib,
            templates,
            msys_host,
            (selected_name,),
            ar_status=0,
            ar_command=glob_ar,
            ranlib_command=glob_ranlib,
        )
        self.assertIn("*", baseline_glob["ar_probe"])
        self.assertIn("*", baseline_glob["ranlib_probe"])
        self.assertIn("*", baseline_glob["ar_help"])
        self.assertIn("*", baseline_glob["ranlib_help"])
        # At archive time libtool expands the AR/RANLIB prefix through its
        # own eval with pathname expansion on, exactly as it does upstream,
        # so the prefix may glob there.  What must survive is the option.
        option = f"--plugin=/plugins/{selected_name}"
        self.assertIn(option, baseline_glob["ar_command"])
        self.assertIn(option, baseline_glob["ranlib_command"])

        for name, block, old, new, key in (
            (
                "libtool.m4:ar-probe-pathname-expansion-guard",
                "archive_probe",
                '(set -f; eval "$AR $plugin_quoted rc'
                ' conftest.a conftest.c")',
                '(eval "$AR $plugin_quoted rc conftest.a conftest.c")',
                "ar_probe",
            ),
            (
                "libtool.m4:ar-help-pathname-expansion-guard",
                "archive_probe",
                'if (set -f; eval "$AR --help") 2>&1',
                'if (eval "$AR --help") 2>&1',
                "ar_help",
            ),
            (
                "libtool.m4:ranlib-probe-pathname-expansion-guard",
                "ranlib_probe",
                '(set -f; eval "$RANLIB $plugin_quoted conftest.a")',
                '(eval "$RANLIB $plugin_quoted conftest.a")',
                "ranlib_probe",
            ),
            (
                "libtool.m4:ranlib-help-pathname-expansion-guard",
                "ranlib_probe",
                'if (set -f; eval "$RANLIB --help") 2>&1',
                'if (eval "$RANLIB --help") 2>&1',
                "ranlib_help",
            ),
        ):
            source = probe if block == "archive_probe" else ranlib
            mutant = self.mutate(name, source, old, new)
            mutant_result = self.run_libtool_archive_probe(
                discovery,
                mutant if block == "archive_probe" else probe,
                mutant if block == "ranlib_probe" else ranlib,
                templates,
                msys_host,
                (selected_name,),
                ar_status=0,
                ar_command=glob_ar,
                ranlib_command=glob_ranlib,
            )
            self.assertNotIn("*", mutant_result[key], name)

        ar_quote_line = (
            '      ar_plugin_option=`$ECHO "$plugin_quoted" | '
            '$SED "$sed_quote_subst"`\n'
        )
        ranlib_quote_line = (
            '      ranlib_plugin_option=`$ECHO "$plugin_quoted" | '
            '$SED "$sed_quote_subst"`\n'
        )

        ar_quote_mutant = self.mutate(
            "libtool.m4:ar-option-escaping-removed",
            probe,
            ar_quote_line,
            "      ar_plugin_option=$plugin_option\n",
        )
        assert_libtool_mutant_fails(
            archive_probe=ar_quote_mutant,
            plugin_dir=payload_dir,
        )

        ranlib_quote_mutant = self.mutate(
            "libtool.m4:ranlib-option-escaping-removed",
            ranlib,
            ranlib_quote_line,
            "      ranlib_plugin_option=$plugin_option\n",
        )
        assert_libtool_mutant_fails(
            ranlib_probe=ranlib_quote_mutant,
            plugin_dir=payload_dir,
        )

        # Escaping the bare option instead of its single-quoted form leaves
        # the value exposed once the second `eval' re-parses it.
        unescaped_ar_quote = self.mutate(
            "libtool.m4:ar-option-quotes-unquoted-source",
            probe,
            ar_quote_line,
            '      ar_plugin_option=`$ECHO "$plugin_option" | '
            '$SED "$sed_quote_subst"`\n',
        )
        assert_libtool_mutant_fails(
            archive_probe=unescaped_ar_quote,
            plugin_dir=payload_dir,
        )

        unescaped_ranlib_quote = self.mutate(
            "libtool.m4:ranlib-option-quotes-unquoted-source",
            ranlib,
            ranlib_quote_line,
            '      ranlib_plugin_option=`$ECHO "$plugin_option" | '
            '$SED "$sed_quote_subst"`\n',
        )
        assert_libtool_mutant_fails(
            ranlib_probe=unescaped_ranlib_quote,
            plugin_dir=payload_dir,
        )

        # The pre-fix shape: escape the bare option, then wrap it in literal
        # double quotes.  libtool runs old_archive_cmds through two evals, so
        # the wrapping quotes are consumed by the first one, which leaves the
        # rest of the option unquoted and lets a `;' start a command.
        separator_dir = "/plugins/meta;touch injected;#name"
        double_eval_ar = self.mutate(
            "libtool.m4:ar-option-survives-second-eval",
            probe,
            ar_quote_line,
            '      ar_plugin_option=`$ECHO "$plugin_option" | '
            '$SED "$sed_quote_subst"`\n'
            '      ar_plugin_option="\\"$ar_plugin_option\\""\n',
        )
        injected = assert_libtool_mutant_fails(
            archive_probe=double_eval_ar,
            plugin_dir=separator_dir,
        )
        self.assertTrue(
            injected["injected_file"],
            "double-eval mutant should have executed the injected command",
        )

        double_eval_ranlib = self.mutate(
            "libtool.m4:ranlib-option-survives-second-eval",
            ranlib,
            ranlib_quote_line,
            '      ranlib_plugin_option=`$ECHO "$plugin_option" | '
            '$SED "$sed_quote_subst"`\n'
            '      ranlib_plugin_option="\\"$ranlib_plugin_option\\""\n',
        )
        injected = assert_libtool_mutant_fails(
            ranlib_probe=double_eval_ranlib,
            plugin_dir=separator_dir,
        )
        self.assertTrue(
            injected["injected_file"],
            "double-eval mutant should have executed the injected command",
        )

        # A RANLIB that refuses the option must leave no state behind.
        rejected = self.run_libtool_archive_probe(
            discovery,
            probe,
            ranlib,
            templates,
            msys_host,
            (selected_name,),
            ar_status=0,
            ranlib_status=1,
        )
        self.assertEqual(rejected["ranlib_plugin_option"], "")
        stale_ranlib = self.mutate(
            "libtool.m4:ranlib-rejection-leaves-no-state",
            ranlib,
            '    if test "$?" != 0; then\n',
            "    if false; then\n",
        )
        stale = self.run_libtool_archive_probe(
            discovery,
            probe,
            stale_ranlib,
            templates,
            msys_host,
            (selected_name,),
            ar_status=0,
            ranlib_status=1,
        )
        self.assertNotEqual(stale["ranlib_plugin_option"], "")

        ar_template_mutant = self.mutate(
            "libtool.m4:archive-template-wiring",
            templates,
            'old_archive_cmds=\'$AR \'"$ar_plugin_option"'
            "' $AR_FLAGS $oldlib$oldobjs'",
            "old_archive_cmds='$AR $plugin_option "
            "$AR_FLAGS $oldlib$oldobjs'",
        )
        assert_libtool_mutant_fails(
            archive_templates=ar_template_mutant
        )

        ranlib_template_mutant = self.mutate(
            "libtool.m4:ranlib-template-wiring",
            templates,
            "$ranlib_plugin_option",
            "$plugin_option",
            occurrences=templates.count("$ranlib_plugin_option"),
        )
        assert_libtool_mutant_fails(
            archive_templates=ranlib_template_mutant
        )

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
        multilib_mutant = self.mutate(
            "cygwin-w64.h:aarch64-jit-m64-exclusion",
            source,
            guarded,
            unguarded,
        )
        headers, defines = aarch64_target_config(msys_host)
        defaults, unused = multilib_defaults(
            headers,
            {define.split("=", 1)[0] for define in defines},
            {header: multilib_mutant},
        )
        self.assertEqual(jit_multilib_arguments(defaults), ("-m64",))

        wiring_hostile = (
            "--plugin=/plug ins/meta;touch INJECTED;"
            "#/glob*/lib'lto.dll"
        )
        wiring_expected = expected_make_quoted(wiring_hostile)
        for wiring_path in PLUGIN_WIRING_SOURCES:
            wiring_tag = wiring_path.as_posix()
            wiring = plugin_make_wiring_block(wiring_path)
            for name, old, new in (
                (
                    f"{wiring_tag}:wiring-bypasses-make-quote",
                    "  plugin_make_arg=$PLUGIN_OPTION; "
                    "func_plugin_make_quote\n",
                    "  plugin_make_quoted=$PLUGIN_OPTION\n",
                ),
                (
                    f"{wiring_tag}:wiring-ar-uses-raw-option",
                    "      AR_PLUGIN_OPTION=$plugin_make_quoted\n",
                    "      AR_PLUGIN_OPTION=$PLUGIN_OPTION\n",
                ),
                (
                    f"{wiring_tag}:wiring-ranlib-uses-raw-option",
                    "      RANLIB_PLUGIN_OPTION=$plugin_make_quoted\n",
                    "      RANLIB_PLUGIN_OPTION=$PLUGIN_OPTION\n",
                ),
                (
                    f"{wiring_tag}:wiring-refusal-gate",
                    '  if test -z "$plugin_make_quoted"; then\n',
                    '  if test -n "$plugin_make_quoted"; then\n',
                ),
            ):
                wiring_mutant = self.mutate(name, wiring, old, new)
                values, unused_files = self.run_plugin_make_wiring(
                    wiring_mutant, wiring_hostile
                )
                self.assertNotEqual(
                    values, (wiring_expected, wiring_expected), name
                )

        self.assertEqual(
            len(MUTATION_LEDGER),
            len(set(MUTATION_LEDGER)),
            "mutation names must be unique",
        )
        self.assertEqual(
            len(MUTATION_LEDGER),
            EXECUTED_MUTATION_INSTANCES,
            "the number of mutation instances this test actually executes "
            "must match the documented count; executed "
            f"{len(MUTATION_LEDGER)}, documented "
            f"{EXECUTED_MUTATION_INSTANCES}",
        )

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

    def test_libffi_aarch64_windows_never_writes_reserved_x18(self):
        """Windows AArch64 keeps the thread environment block in x18.

        gcc/config/aarch64/aarch64-abi-ms.h marks x18 fixed, never call
        clobbered, and moves the static chain to r17 precisely because of
        that.  libffi's ffi_call_SYSV writes the Go static chain into x18
        under an FFI_GO_CLOSURES guard and never restores it, and every
        ordinary ffi_call reaches that instruction with a NULL chain once
        the guard is satisfied, so the predicate that disables
        FFI_GO_CLOSURES has to cover every Windows AArch64 family, not
        just the ones that predefine _WIN32.
        """
        header = read_source(Path("libffi/src/aarch64/ffitarget.h"))
        self.assertIn(
            "#if defined(_WIN32) || defined(_WIN64) || defined(__CYGWIN__)\n"
            "#define FFI_AARCH64_WINDOWS 1\n"
            "#endif\n",
            header,
        )
        # Every ABI decision has to consult the shared predicate.  A
        # header that decided FFI_DEFAULT_ABI one way and FFI_SIZEOF_ARG
        # another would be inconsistent on exactly these targets.
        for guard in (
            "#elif defined(FFI_AARCH64_WINDOWS)",
            "#if defined(FFI_AARCH64_WINDOWS)\n    FFI_DEFAULT_ABI = FFI_WIN64",
            "#ifdef FFI_AARCH64_WINDOWS\n#define FFI_EXTRA_CIF_FIELDS",
            "#elif !defined(FFI_AARCH64_WINDOWS)",
        ):
            self.assertIn(guard, header)
        self.assertEqual(header.count("FFI_AARCH64_WINDOWS"), 6)
        # _WIN32 may only appear inside the predicate itself and in the
        # prose explaining it; no decision may test it directly.
        for line in header.split("\n"):
            if not line.startswith("#") or "_WIN32" not in line:
                continue
            self.assertEqual(
                line,
                "#if defined(_WIN32) || defined(_WIN64) "
                "|| defined(__CYGWIN__)",
                line,
            )

        assembly = read_source(Path("libffi/src/aarch64/sysv.S"))
        guard = assembly.index(
            "#if defined(FFI_GO_CLOSURES) && defined(FFI_AARCH64_WINDOWS)\n"
            '# error "FFI_GO_CLOSURES must stay disabled on Windows AArch64: '
            'x18 is reserved for the TEB"\n'
            "#endif\n"
        )
        chain = assembly.index("mov\tx18, x5")
        self.assertLess(guard, chain)
        # The store is guarded already.  What the assertion above prevents
        # is the predicate being satisfied, so pin the guarded shape too:
        # a future edit that lifted the store out of the #ifdef would make
        # the surrounding comment wrong and the #error insufficient.
        self.assertIn(
            "#ifdef FFI_GO_CLOSURES\n"
            "\tmov\tx18, x5\t\t\t/* install static chain */\n"
            "#endif\n",
            assembly,
        )

    @unittest.skipUnless(REAL_SH, "requires a POSIX shell")
    def test_libffi_predicate_evaluates_per_windows_family(self):
        """Evaluate the real predicate with a real preprocessor.

        Reading the text proves the shape; only a preprocessor proves the
        outcome for the macro sets these targets actually predefine.
        """
        compiler = os.environ.get("CC") or shutil.which("gcc") or \
            shutil.which("cc") or shutil.which("clang")
        if not compiler:
            self.skipTest("no C preprocessor available")
        header = read_source(Path("libffi/src/aarch64/ffitarget.h"))
        predicate = (
            "#if defined(_WIN32) || defined(_WIN64) || defined(__CYGWIN__)\n"
            "#define FFI_AARCH64_WINDOWS 1\n"
            "#endif\n"
        )
        self.assertIn(predicate, header)
        families = (
            # Cygwin and MSYS2 on AArch64 leave _WIN32 undefined.
            ("cygwin", ("_WIN64", "__CYGWIN__"), True),
            ("msys", ("_WIN64", "__CYGWIN__", "__MSYS__"), True),
            ("mingw", ("_WIN32", "_WIN64"), True),
            ("msvc", ("_WIN32", "_WIN64"), True),
            ("linux", (), False),
        )
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            for name, macros, windows in families:
                with self.subTest(family=name):
                    # Undefine first, so the outcome depends only on the
                    # macro set under test and never on whatever the host
                    # preprocessor happens to predefine for itself.
                    source = work / f"probe-{name}.c"
                    source.write_text(
                        "".join(
                            f"#undef {macro}\n"
                            for macro in ("_WIN32", "_WIN64", "__CYGWIN__",
                                          "__MSYS__")
                        )
                        + "".join(f"#define {macro} 1\n" for macro in macros)
                        + predicate
                        + "#ifdef FFI_AARCH64_WINDOWS\n"
                        "MARK_WINDOWS_ABI\n"
                        "#else\n"
                        "MARK_GO_CLOSURE_X18_WRITE_LIVE\n"
                        "#endif\n",
                        encoding="utf-8",
                        newline="\n",
                    )
                    result = subprocess.run(
                        [compiler, "-E", "-P", str(source)],
                        capture_output=True,
                        text=True,
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
                    expected = (
                        "MARK_WINDOWS_ABI"
                        if windows
                        else "MARK_GO_CLOSURE_X18_WRITE_LIVE"
                    )
                    self.assertIn(expected, result.stdout)
                    if windows:
                        self.assertNotIn(
                            "MARK_GO_CLOSURE_X18_WRITE_LIVE", result.stdout
                        )

    def test_ada_is_excluded_for_aarch64_windows(self):
        """Pin the Ada decision instead of leaving it to fail late.

        gcc/ada/Makefile.rtl only selects Windows runtime pairs for
        cygwin, mingw32 and pe, so an AArch64 Windows target would reach
        SELECTED_PAIRS=PAIRS_NONE and fail deep inside the build.  The
        top level therefore has to say so up front.
        """
        pattern = (
            r"aarch64\*-\*-cygwin\* \| aarch64\*-\*-msys\* "
            r"\| aarch64\*-\*-mingw\* \| aarch64\*-\*-win\*\s*\)\s+"
            r'unsupported_languages="\$unsupported_languages ada"'
        )
        for path in (Path("configure.ac"), Path("configure")):
            self.assertRegex(read_source(path), pattern)
        # The runtime selector really does lack an AArch64 Windows arm;
        # if that ever changes the exclusion above should be revisited.
        rtl = read_source(Path("gcc/ada/Makefile.rtl"))
        self.assertNotIn("msys", rtl)

    def test_generated_configure_files_keep_inherited_eof_bytes(self):
        """Compare EOF bytes exactly rather than by a layout heuristic.

        An earlier revision of this check compared a summary of the tail
        and missed a deleted blank line, so it now pins the bytes.
        """
        for path in PLUGIN_CONFIGURES:
            with self.subTest(path=str(path)):
                data = read_blob_bytes(path)
                self.assertEqual(data[-12:], b'" >&2;}\nfi\n\n', str(path))
                self.assertNotIn(b"\r", data, str(path))


if __name__ == "__main__":
    unittest.main()
