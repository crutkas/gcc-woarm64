#!/usr/bin/env python3

# Copyright (C) 2026 Free Software Foundation, Inc.
#
# This file is part of GCC.
#
# GCC is free software; you can redistribute it and/or modify it under
# the terms of the GNU General Public License as published by the Free
# Software Foundation; either version 3, or (at your option) any later
# version.

"""Fail-closed acceptance gate for native ARM64 Windows test results."""

import argparse
from collections import defaultdict
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re


SCHEMA_VERSION = 1
RESULT_PATTERN = re.compile(r"^([A-Z][A-Z ]*):\s+(.+?)\s*$")
COUNTER_PATTERN = re.compile(r"^\s*# of ([^0-9]+?)\s+([0-9]+)\s*$")
BAD_STATUSES = {
    "ERROR",
    "FAIL",
    "KFAIL",
    "UNRESOLVED",
    "UNTESTED",
    "WARNING",
    "XPASS",
}
ROSTER_ALLOWED = {"PASS", "XFAIL"}
BAD_COUNTERS = {
    "unexpected failures",
    "unexpected successes",
    "unresolved testcases",
}


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest().upper()


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def parse_mapping(values, option):
    result = {}
    for value in values:
        if "=" not in value:
            raise ValueError(f"{option} requires TOOL=PATH: {value!r}")
        tool, path = value.split("=", 1)
        if not tool or not path or tool in result:
            raise ValueError(f"invalid or duplicate {option}: {value!r}")
        result[tool] = Path(path).resolve()
    return result


def read_required_file(path, description):
    if not path.is_file():
        raise ValueError(f"missing {description}: {path}")
    data = path.read_bytes()
    if not data:
        raise ValueError(f"empty {description}: {path}")
    return data.decode("utf-8", errors="replace")


def parse_results(text):
    results = []
    counters = {}
    diagnostics = []
    for line_number, line in enumerate(text.splitlines(), 1):
        result = RESULT_PATTERN.match(line)
        if result:
            status = result.group(1).strip()
            results.append(
                {
                    "line": line_number,
                    "status": status,
                    "test": result.group(2),
                }
            )
            if status in ("ERROR", "WARNING"):
                diagnostics.append(line)
        counter = COUNTER_PATTERN.match(line)
        if counter:
            counters[counter.group(1).strip().lower()] = int(
                counter.group(2)
            )
        if re.match(r"^(ERROR|WARNING):", line):
            diagnostics.append(line)
    return results, counters, diagnostics


def validate_roster(roster):
    if roster.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unsupported test roster schema")
    tools = roster.get("tools")
    rosters = roster.get("rosters")
    if not isinstance(tools, dict) or set(tools) != {
        "gcc",
        "g++",
        "libstdc++",
    }:
        raise ValueError("roster must define exactly gcc, g++, libstdc++")
    if not isinstance(rosters, dict):
        raise ValueError("missing test rosters")
    if len(rosters.get("seh", [])) != 17:
        raise ValueError("SEH roster must contain exactly 17 tests")
    if len(rosters.get("secondary", [])) != 10:
        raise ValueError("secondary roster must contain exactly 10 tests")
    seen = set()
    for group, entries in rosters.items():
        for entry in entries:
            path = entry.get("path")
            tool = entry.get("tool")
            if (
                not path
                or PurePosixPath(path).is_absolute()
                or ".." in PurePosixPath(path).parts
                or tool not in tools
                or path in seen
            ):
                raise ValueError(f"invalid {group} roster entry: {entry!r}")
            seen.add(path)
    return tools


def has_source_xfail(source):
    return re.search(
        r"\b(?:dg-xfail-[A-Za-z0-9_-]+|xfail)\b",
        source,
        flags=re.IGNORECASE,
    ) is not None


def roster_matches(test_name, roster_path):
    normalized = test_name.replace("\\", "/")
    return (
        normalized == roster_path
        or normalized.endswith("/" + roster_path)
        or PurePosixPath(normalized).name
        == PurePosixPath(roster_path).name
    )


def validate_results(source_root, roster_path, summaries, logs):
    roster = load_json(roster_path)
    tools = validate_roster(roster)
    if set(summaries) != set(tools) or set(logs) != set(tools):
        raise ValueError("summary/log inputs must cover all roster tools")

    parsed = {}
    evidence = {}
    for tool, config in tools.items():
        floor = config.get("expected_pass_floor")
        if not isinstance(floor, int) or floor <= 0:
            raise ValueError(
                f"{tool} expected_pass_floor is not pinned above zero"
            )
        summary_text = read_required_file(
            summaries[tool], f"{tool} summary"
        )
        log_text = read_required_file(logs[tool], f"{tool} log")
        heading = config.get("summary_heading")
        if not heading or heading not in summary_text:
            raise ValueError(f"{tool} summary block is missing or truncated")
        results, counters, diagnostics = parse_results(
            summary_text + "\n" + log_text
        )
        expected_passes = counters.get("expected passes")
        if expected_passes is None or expected_passes < floor:
            raise ValueError(
                f"{tool} expected passes {expected_passes!r} below {floor}"
            )
        for name in BAD_COUNTERS:
            if counters.get(name, 0) != 0:
                raise ValueError(
                    f"{tool} has nonzero {name}: {counters[name]}"
                )
        bad_results = [
            result for result in results
            if result["status"] in BAD_STATUSES
        ]
        if bad_results or diagnostics:
            raise ValueError(
                f"{tool} has unexpected results or diagnostics"
            )
        parsed[tool] = results
        evidence[tool] = {
            "counters": counters,
            "log": os.fspath(logs[tool]),
            "log_sha256": sha256_file(logs[tool]),
            "result_lines": len(results),
            "summary": os.fspath(summaries[tool]),
            "summary_sha256": sha256_file(summaries[tool]),
        }

    roster_evidence = defaultdict(list)
    for group, entries in roster["rosters"].items():
        for entry in entries:
            path = entry["path"]
            tool = entry["tool"]
            source_path = Path(source_root, *PurePosixPath(path).parts)
            source_text = read_required_file(
                source_path, f"roster source {path}"
            )
            matches = [
                result for result in parsed[tool]
                if roster_matches(result["test"], path)
            ]
            if not matches:
                raise ValueError(f"roster test is absent: {path}")
            for result in matches:
                if result["status"] not in ROSTER_ALLOWED:
                    raise ValueError(
                        f"roster test has {result['status']}: {path}"
                    )
                if (
                    result["status"] == "XFAIL"
                    and not has_source_xfail(source_text)
                ):
                    raise ValueError(
                        f"roster XFAIL is not declared in source: {path}"
                    )
            roster_evidence[group].append(
                {
                    "path": path,
                    "results": matches,
                    "source_sha256": sha256_file(source_path),
                    "tool": tool,
                }
            )

    return {
        "passed": True,
        "roster": os.fspath(Path(roster_path).resolve()),
        "roster_sha256": sha256_file(roster_path),
        "rosters": dict(roster_evidence),
        "schema_version": SCHEMA_VERSION,
        "source_root": os.fspath(Path(source_root).resolve()),
        "tools": evidence,
    }


def write_json(path, value):
    encoded = (
        json.dumps(
            value,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")
    Path(path).write_bytes(encoded)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--source-tree", required=True)
    parser.add_argument("--roster", required=True)
    parser.add_argument("--summary", action="append", default=[])
    parser.add_argument("--log", action="append", default=[])
    parser.add_argument("--output", required=True)
    arguments = parser.parse_args()
    result = validate_results(
        arguments.source_root,
        arguments.roster,
        parse_mapping(arguments.summary, "--summary"),
        parse_mapping(arguments.log, "--log"),
    )
    result["source_commit"] = arguments.source_commit
    result["source_tree"] = arguments.source_tree
    write_json(arguments.output, result)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
