#!/usr/bin/env python3

# Copyright (C) 2026 Free Software Foundation, Inc.
#
# This file is part of GCC.
#
# GCC is free software; you can redistribute it and/or modify it under
# the terms of the GNU General Public License as published by the Free
# Software Foundation; either version 3, or (at your option) any later
# version.

import hashlib
import json
from pathlib import Path
import tempfile
import unittest

import arm64_windows_testsuite_gate as gate


class TestArm64WindowsTestsuiteGate(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.source = self.root / "source"
        self.source.mkdir()
        self.roster_path = self.root / "roster.json"
        self.summaries = {}
        self.logs = {}
        self.roster = {
            "schema_version": 1,
            "tools": {
                tool: {
                    "expected_pass_floor": 1,
                    "summary_heading": f"=== {tool} Summary ===",
                }
                for tool in ("gcc", "g++", "libstdc++")
            },
            "rosters": {
                "seh": [],
                "secondary": [],
            },
        }
        for index in range(17):
            tool = "g++" if index == 0 else "gcc"
            suffix = "C" if tool == "g++" else "c"
            path = f"tests/seh-{index}.{suffix}"
            self.roster["rosters"]["seh"].append(
                {"path": path, "tool": tool}
            )
            self.write_source(path, "int test;\n")
        for index in range(10):
            tool = "libstdc++" if index == 9 else "gcc"
            path = f"tests/secondary-{index}.cc"
            self.roster["rosters"]["secondary"].append(
                {"path": path, "tool": tool}
            )
            self.write_source(path, "int test;\n")
        self.write_roster()
        self.write_passing_results()

    def tearDown(self):
        self.temporary.cleanup()

    def write_source(self, relative, value):
        path = self.source / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value, encoding="utf-8", newline="\n")

    def write_roster(self):
        self.roster_path.write_text(
            json.dumps(self.roster), encoding="utf-8", newline="\n"
        )

    def write_passing_results(self):
        by_tool = defaultdict_list()
        for entries in self.roster["rosters"].values():
            for entry in entries:
                by_tool[entry["tool"]].append(
                    f"PASS: {entry['path']}"
                )
        for tool in self.roster["tools"]:
            summary = self.root / f"{tool}.sum"
            log = self.root / f"{tool}.log"
            lines = by_tool[tool]
            summary.write_text(
                "\n".join(
                    lines
                    + [
                        f"=== {tool} Summary ===",
                        f"# of expected passes {max(1, len(lines))}",
                    ]
                )
                + "\n",
                encoding="utf-8",
                newline="\n",
            )
            log.write_text(
                "\n".join(lines) + "\n",
                encoding="utf-8",
                newline="\n",
            )
            self.summaries[tool] = summary
            self.logs[tool] = log

    def validate(self):
        return gate.validate_results(
            self.source,
            self.roster_path,
            self.summaries,
            self.logs,
        )

    def assert_rejected(self):
        with self.assertRaises(ValueError):
            self.validate()

    def test_real_passing_summary(self):
        result = self.validate()
        self.assertTrue(result["passed"])
        self.assertEqual(len(result["rosters"]["seh"]), 17)
        self.assertEqual(len(result["rosters"]["secondary"]), 10)
        self.assertEqual(
            result["roster_sha256"],
            hashlib.sha256(self.roster_path.read_bytes())
            .hexdigest().upper(),
        )

    def test_missing_summary_is_rejected(self):
        self.summaries["gcc"].unlink()
        self.assert_rejected()

    def test_ignored_recipe_failure_without_summary_is_rejected(self):
        self.summaries["gcc"].unlink()
        self.logs["gcc"].write_text(
            "WARNING: could not find runtest\n"
            "make: [check-gcc] Error 127 (ignored)\n",
            encoding="utf-8",
        )
        self.assert_rejected()

    def test_empty_log_is_rejected(self):
        self.logs["gcc"].write_bytes(b"")
        self.assert_rejected()

    def test_truncated_summary_is_rejected(self):
        self.summaries["gcc"].write_text(
            "# of expected passes 20\n", encoding="utf-8"
        )
        self.assert_rejected()

    def test_zero_tests_are_rejected(self):
        self.summaries["gcc"].write_text(
            "=== gcc Summary ===\n# of expected passes 0\n",
            encoding="utf-8",
        )
        self.assert_rejected()

    def test_unexpected_failure_is_rejected(self):
        self.logs["gcc"].write_text(
            "FAIL: tests/seh-1.c\n", encoding="utf-8"
        )
        self.assert_rejected()

    def test_xpass_is_rejected(self):
        self.logs["gcc"].write_text(
            "XPASS: tests/seh-1.c\n", encoding="utf-8"
        )
        self.assert_rejected()

    def test_unresolved_is_rejected(self):
        self.logs["gcc"].write_text(
            "UNRESOLVED: tests/seh-1.c\n", encoding="utf-8"
        )
        self.assert_rejected()

    def test_absent_roster_test_is_rejected(self):
        value = self.summaries["gcc"].read_text(encoding="utf-8")
        self.summaries["gcc"].write_text(
            value.replace("PASS: tests/seh-1.c\n", ""),
            encoding="utf-8",
        )
        value = self.logs["gcc"].read_text(encoding="utf-8")
        self.logs["gcc"].write_text(
            value.replace("PASS: tests/seh-1.c\n", ""),
            encoding="utf-8",
        )
        self.assert_rejected()

    def test_unsupported_roster_test_is_rejected(self):
        value = self.summaries["gcc"].read_text(encoding="utf-8")
        self.summaries["gcc"].write_text(
            value.replace(
                "PASS: tests/seh-1.c",
                "UNSUPPORTED: tests/seh-1.c",
            ),
            encoding="utf-8",
        )
        self.assert_rejected()

    def test_xfail_requires_source_directive(self):
        value = self.summaries["gcc"].read_text(encoding="utf-8")
        self.summaries["gcc"].write_text(
            value.replace(
                "PASS: tests/seh-1.c", "XFAIL: tests/seh-1.c"
            ),
            encoding="utf-8",
        )
        value = self.logs["gcc"].read_text(encoding="utf-8")
        self.logs["gcc"].write_text(
            value.replace(
                "PASS: tests/seh-1.c", "XFAIL: tests/seh-1.c"
            ),
            encoding="utf-8",
        )
        self.assert_rejected()

    def test_unpinned_floor_is_rejected(self):
        self.roster["tools"]["gcc"]["expected_pass_floor"] = None
        self.write_roster()
        self.assert_rejected()


def defaultdict_list():
    return {tool: [] for tool in ("gcc", "g++", "libstdc++")}


if __name__ == "__main__":
    unittest.main()
