#!/usr/bin/env python3

# Copyright (C) 2026 Free Software Foundation, Inc.
#
# This file is part of GCC.
#
# GCC is free software; you can redistribute it and/or modify it under
# the terms of the GNU General Public License as published by the Free
# Software Foundation; either version 3, or (at your option) any later
# version.

import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest


SCRIPT = Path(__file__).with_name("gcc_source_archive.py")


class TestGccSourceArchive(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.git("init", "--quiet")
        self.git("config", "user.name", "Archive Test")
        self.git("config", "user.email", "archive@example.invalid")
        (self.repo / ".gitattributes").write_bytes(
            b"*.txt text eol=crlf\n"
        )
        (self.repo / "line-endings.txt").write_bytes(b"one\ntwo\n")
        unicode_root = self.repo / "unicode"
        unicode_root.mkdir()
        (unicode_root / "\u00c4foo.go").write_bytes(b"package p\n")
        (unicode_root / "\u00c4main.go").write_bytes(b"package p\n")
        executable = self.repo / "executable.sh"
        executable.write_bytes(b"#!/bin/sh\nexit 0\n")
        long_root = self.repo / ("long-" + "x" * 96)
        long_root.mkdir()
        (long_root / ("file-" + "y" * 80)).write_bytes(b"long\n")
        self.git("add", ".")
        self.git("update-index", "--chmod=+x", "executable.sh")
        environment = os.environ.copy()
        environment.update(
            {
                "GIT_AUTHOR_DATE": "2026-01-02T03:04:05Z",
                "GIT_COMMITTER_DATE": "2026-01-02T03:04:05Z",
            }
        )
        self.git("commit", "--quiet", "-m", "fixture", env=environment)
        self.commit = self.git("rev-parse", "HEAD").stdout.strip()

    def tearDown(self):
        self.temporary.cleanup()

    def git(self, *arguments, env=None):
        return subprocess.run(
            ["git", "-C", self.repo, *arguments],
            check=True,
            env=env,
            text=True,
            capture_output=True,
        )

    def tool(self, *arguments, check=True):
        return subprocess.run(
            [sys.executable, SCRIPT, *map(os.fspath, arguments)],
            check=check,
            text=True,
            capture_output=True,
        )

    def create(self, stem):
        archive = self.root / f"{stem}.tar"
        manifest = self.root / f"{stem}.json"
        self.tool(
            "create",
            "--repo",
            self.repo,
            "--commit",
            self.commit,
            "--archive",
            archive,
            "--manifest",
            manifest,
        )
        return archive, manifest

    def test_reproducible_full_tree_round_trip(self):
        archive_a, manifest_a = self.create("a")
        archive_b, manifest_b = self.create("b")
        self.assertEqual(archive_a.read_bytes(), archive_b.read_bytes())
        self.assertEqual(manifest_a.read_bytes(), manifest_b.read_bytes())

        value = json.loads(manifest_a.read_text(encoding="utf-8"))
        self.assertEqual(value["entry_count"], 6)
        paths = {entry["path"] for entry in value["entries"]}
        self.assertIn("unicode/\u00c4foo.go", paths)
        self.assertIn("unicode/\u00c4main.go", paths)
        executable = next(
            entry for entry in value["entries"]
            if entry["path"] == "executable.sh"
        )
        self.assertEqual(executable["mode"], "100755")

        with tarfile.open(archive_a, "r:", encoding="utf-8") as source:
            line_endings = source.extractfile(
                "line-endings.txt"
            ).read()
            self.assertEqual(line_endings, b"one\ntwo\n")
            self.assertNotIn(b"\r", line_endings)
            self.assertIsNotNone(
                source.getmember("unicode/\u00c4foo.go")
            )

        verification = self.tool(
            "verify-archive",
            "--repo",
            self.repo,
            "--commit",
            self.commit,
            "--archive",
            archive_a,
            "--manifest",
            manifest_a,
        )
        self.assertTrue(json.loads(verification.stdout)["passed"])

        extracted = self.root / "extracted"
        self.tool(
            "extract",
            "--repo",
            self.repo,
            "--commit",
            self.commit,
            "--archive",
            archive_a,
            "--manifest",
            manifest_a,
            "--output",
            extracted,
        )
        directory = self.tool(
            "verify-directory",
            "--repo",
            self.repo,
            "--commit",
            self.commit,
            "--manifest",
            manifest_a,
            "--root",
            extracted,
        )
        self.assertTrue(json.loads(directory.stdout)["passed"])
        self.assertEqual(
            (extracted / "unicode" / "\u00c4foo.go").read_bytes(),
            b"package p\n",
        )

    def test_archive_mutation_is_rejected(self):
        archive, manifest = self.create("mutated")
        data = bytearray(archive.read_bytes())
        data[512] ^= 1
        archive.write_bytes(data)
        result = self.tool(
            "verify-archive",
            "--repo",
            self.repo,
            "--commit",
            self.commit,
            "--archive",
            archive,
            "--manifest",
            manifest,
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)

    def test_directory_path_and_blob_mutations_are_rejected(self):
        archive, manifest = self.create("directory")
        extracted = self.root / "directory-root"
        self.tool(
            "extract",
            "--repo",
            self.repo,
            "--commit",
            self.commit,
            "--archive",
            archive,
            "--manifest",
            manifest,
            "--output",
            extracted,
        )
        (extracted / "line-endings.txt").write_bytes(b"changed\n")
        result = self.tool(
            "verify-directory",
            "--repo",
            self.repo,
            "--commit",
            self.commit,
            "--manifest",
            manifest,
            "--root",
            extracted,
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
