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

"""Create and verify byte-exact, deterministic GCC source archives.

The archive is populated from Git objects, never the working tree or Git's
platform-dependent archive conversion path.  Its manifest binds every raw
path, mode, object type and object ID to the commit and tree.
"""

import argparse
from dataclasses import dataclass
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import subprocess
import tarfile
import tempfile


SCHEMA_VERSION = 1
ARCHIVE_FORMAT = "python-tarfile-gnu-v1"


def git(repo, *arguments, input_bytes=None):
    return subprocess.run(
        ["git", "-C", os.fspath(repo), *arguments],
        check=True,
        input=input_bytes,
        stdout=subprocess.PIPE,
    ).stdout


@dataclass(frozen=True)
class TreeEntry:
    mode: str
    object_type: str
    oid: str
    size: int
    path_bytes: bytes

    @property
    def path(self):
        return self.path_bytes.decode("utf-8", errors="strict")

    def manifest_record(self):
        return {
            "mode": self.mode,
            "object_type": self.object_type,
            "oid": self.oid,
            "path": self.path,
            "path_bytes_hex": self.path_bytes.hex(),
            "size": self.size,
        }


def resolve_source(repo, commit):
    resolved = git(repo, "rev-parse", f"{commit}^{{commit}}").decode().strip()
    tree = git(repo, "rev-parse", f"{resolved}^{{tree}}").decode().strip()
    timestamp = int(
        git(repo, "show", "-s", "--format=%ct", resolved).decode().strip()
    )
    object_format = git(
        repo, "rev-parse", "--show-object-format"
    ).decode().strip()
    if object_format != "sha1":
        raise ValueError(f"unsupported Git object format: {object_format}")
    return resolved, tree, timestamp, object_format


def read_tree(repo, commit):
    output = git(
        repo,
        "ls-tree",
        "-r",
        "-z",
        "-l",
        "--full-tree",
        commit,
    )
    entries = []
    for raw_record in output.split(b"\0"):
        if not raw_record:
            continue
        metadata, path_bytes = raw_record.split(b"\t", 1)
        mode, object_type, oid, size = metadata.split()
        if object_type != b"blob":
            raise ValueError(
                f"unsupported tree object {object_type!r}: {path_bytes!r}"
            )
        path = path_bytes.decode("utf-8", errors="strict")
        validate_archive_path(path)
        entries.append(
            TreeEntry(
                mode.decode("ascii"),
                object_type.decode("ascii"),
                oid.decode("ascii"),
                int(size),
                path_bytes,
            )
        )
    return entries


def validate_archive_path(path):
    pure = PurePosixPath(path)
    if (
        not path
        or pure.is_absolute()
        or "\\" in path
        or any(part in ("", ".", "..") for part in pure.parts)
    ):
        raise ValueError(f"unsafe or noncanonical Git path: {path!r}")


class GitObjectReader:
    def __init__(self, repo):
        self.process = subprocess.Popen(
            ["git", "-C", os.fspath(repo), "cat-file", "--batch"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
        )

    def read(self, entry):
        self.process.stdin.write(entry.oid.encode("ascii") + b"\n")
        self.process.stdin.flush()
        header = self.process.stdout.readline().rstrip(b"\n").split()
        if len(header) != 3 or header[0].decode() != entry.oid:
            raise ValueError(f"unexpected cat-file response: {header!r}")
        if header[1] != b"blob" or int(header[2]) != entry.size:
            raise ValueError(f"Git object metadata mismatch: {entry.path!r}")
        data = self.process.stdout.read(entry.size)
        if len(data) != entry.size or self.process.stdout.read(1) != b"\n":
            raise ValueError(f"truncated Git object: {entry.path!r}")
        return data

    def close(self):
        if self.process.stdin:
            self.process.stdin.close()
        return_code = self.process.wait()
        if return_code:
            raise subprocess.CalledProcessError(
                return_code, self.process.args
            )

    def __enter__(self):
        return self

    def __exit__(self, exception_type, exception, traceback):
        if exception_type is not None:
            self.process.kill()
            self.process.wait()
            return
        self.close()


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest().upper()


def git_blob_oid(data):
    header = f"blob {len(data)}\0".encode("ascii")
    return hashlib.sha1(header + data).hexdigest()


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


def build_manifest(
    commit, tree, timestamp, object_format, archive, entries
):
    return {
        "archive": {
            "format": ARCHIVE_FORMAT,
            "sha256": sha256_file(archive),
            "size": Path(archive).stat().st_size,
        },
        "commit": commit,
        "commit_timestamp": timestamp,
        "entries": [entry.manifest_record() for entry in entries],
        "entry_count": len(entries),
        "object_format": object_format,
        "schema_version": SCHEMA_VERSION,
        "tree": tree,
    }


def create_archive(repo, commit, archive, manifest):
    repo = Path(repo).resolve()
    archive = Path(archive).resolve()
    manifest = Path(manifest).resolve()
    resolved, tree, timestamp, object_format = resolve_source(repo, commit)
    entries = read_tree(repo, resolved)
    archive.parent.mkdir(parents=True, exist_ok=True)
    manifest.parent.mkdir(parents=True, exist_ok=True)

    descriptor, temporary_name = tempfile.mkstemp(
        prefix=archive.name + ".", suffix=".tmp", dir=archive.parent
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        with GitObjectReader(repo) as objects:
            with tarfile.open(
                temporary,
                mode="w",
                format=tarfile.GNU_FORMAT,
                encoding="utf-8",
                errors="strict",
            ) as output:
                for entry in entries:
                    data = objects.read(entry)
                    info = tarfile.TarInfo(entry.path)
                    info.mode = int(entry.mode[-3:], 8)
                    info.uid = 0
                    info.gid = 0
                    info.uname = ""
                    info.gname = ""
                    info.mtime = timestamp
                    info.type = tarfile.REGTYPE
                    info.size = len(data)
                    output.addfile(info, io.BytesIO(data))
        os.replace(temporary, archive)
    finally:
        if temporary.exists():
            temporary.unlink()

    value = build_manifest(
        resolved, tree, timestamp, object_format, archive, entries
    )
    write_json(manifest, value)
    return value


def load_manifest(path):
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if value.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unsupported source manifest schema")
    if value.get("archive", {}).get("format") != ARCHIVE_FORMAT:
        raise ValueError("unsupported source archive format")
    return value


def expected_manifest(repo, commit, archive):
    resolved, tree, timestamp, object_format = resolve_source(repo, commit)
    entries = read_tree(repo, resolved)
    return build_manifest(
        resolved, tree, timestamp, object_format, archive, entries
    ), entries


def verify_archive(repo, commit, archive, manifest):
    archive = Path(archive).resolve()
    recorded = load_manifest(manifest)
    expected, entries = expected_manifest(repo, commit, archive)
    if recorded != expected:
        raise ValueError("source manifest does not match Git tree or archive")

    with tarfile.open(
        archive, mode="r:", encoding="utf-8", errors="strict"
    ) as source:
        members = source.getmembers()
        if len(members) != len(entries):
            raise ValueError(
                f"archive entry count {len(members)} != {len(entries)}"
            )
        for member, entry in zip(members, entries):
            if member.name.encode("utf-8") != entry.path_bytes:
                raise ValueError(f"archive path mismatch: {member.name!r}")
            if not member.isfile() or member.mode != int(entry.mode[-3:], 8):
                raise ValueError(f"archive type/mode mismatch: {entry.path!r}")
            if (
                member.uid != 0
                or member.gid != 0
                or member.uname
                or member.gname
                or member.mtime != recorded["commit_timestamp"]
            ):
                raise ValueError(
                    f"archive metadata mismatch: {entry.path!r}"
                )
            extracted = source.extractfile(member)
            data = extracted.read()
            if len(data) != entry.size or git_blob_oid(data) != entry.oid:
                raise ValueError(
                    f"archive blob mismatch: {entry.path!r}"
                )
    return {
        "archive_sha256": expected["archive"]["sha256"],
        "commit": expected["commit"],
        "entry_count": len(entries),
        "manifest_sha256": sha256_file(manifest),
        "passed": True,
        "tree": expected["tree"],
    }


def extract_archive(repo, commit, archive, manifest, output):
    verification = verify_archive(repo, commit, archive, manifest)
    output = Path(output).resolve()
    if output.exists():
        raise ValueError(f"extraction root already exists: {output}")
    output.mkdir(parents=True)
    with tarfile.open(
        archive, mode="r:", encoding="utf-8", errors="strict"
    ) as source:
        for member in source:
            validate_archive_path(member.name)
            target = output.joinpath(*PurePosixPath(member.name).parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            data = source.extractfile(member).read()
            target.write_bytes(data)
            target.chmod(member.mode)
            os.utime(target, (member.mtime, member.mtime))
    verification["output"] = os.fspath(output)
    return verification


def verify_directory(repo, commit, manifest, root):
    recorded = load_manifest(manifest)
    resolved, tree, _timestamp, _object_format = resolve_source(repo, commit)
    if recorded["commit"] != resolved or recorded["tree"] != tree:
        raise ValueError("directory manifest source identity mismatch")
    expected = {
        bytes.fromhex(entry["path_bytes_hex"]): entry
        for entry in recorded["entries"]
    }
    root = Path(root).resolve()
    actual_paths = {}
    for base, directories, files in os.walk(root):
        directories.sort()
        files.sort()
        base_path = Path(base)
        for name in files:
            path = base_path / name
            relative = path.relative_to(root).as_posix()
            raw = relative.encode("utf-8", errors="strict")
            if raw in actual_paths:
                raise ValueError(f"duplicate extracted path: {relative!r}")
            actual_paths[raw] = path
    if set(actual_paths) != set(expected):
        missing = sorted(set(expected) - set(actual_paths))
        extra = sorted(set(actual_paths) - set(expected))
        raise ValueError(
            f"extracted path mismatch: {len(missing)} missing, "
            f"{len(extra)} extra"
        )
    for raw_path, path in actual_paths.items():
        entry = expected[raw_path]
        data = path.read_bytes()
        if len(data) != entry["size"] or git_blob_oid(data) != entry["oid"]:
            raise ValueError(
                f"extracted blob mismatch: {raw_path!r}"
            )
    return {
        "commit": resolved,
        "entry_count": len(expected),
        "manifest_sha256": sha256_file(manifest),
        "passed": True,
        "root": os.fspath(root),
        "tree": tree,
    }


def record_result(path, result):
    if path:
        write_json(path, result)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


def main():
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    def add_source_arguments(command):
        command.add_argument("--repo", required=True)
        command.add_argument("--commit", required=True)

    create = subparsers.add_parser("create")
    add_source_arguments(create)
    create.add_argument("--archive", required=True)
    create.add_argument("--manifest", required=True)

    verify = subparsers.add_parser("verify-archive")
    add_source_arguments(verify)
    verify.add_argument("--archive", required=True)
    verify.add_argument("--manifest", required=True)
    verify.add_argument("--record")

    extract = subparsers.add_parser("extract")
    add_source_arguments(extract)
    extract.add_argument("--archive", required=True)
    extract.add_argument("--manifest", required=True)
    extract.add_argument("--output", required=True)
    extract.add_argument("--record")

    directory = subparsers.add_parser("verify-directory")
    add_source_arguments(directory)
    directory.add_argument("--manifest", required=True)
    directory.add_argument("--root", required=True)
    directory.add_argument("--record")

    arguments = parser.parse_args()
    if arguments.command == "create":
        manifest = create_archive(
            arguments.repo,
            arguments.commit,
            arguments.archive,
            arguments.manifest,
        )
        result = {
            "archive_sha256": manifest["archive"]["sha256"],
            "commit": manifest["commit"],
            "entry_count": manifest["entry_count"],
            "manifest_sha256": sha256_file(arguments.manifest),
            "passed": True,
            "tree": manifest["tree"],
        }
    elif arguments.command == "verify-archive":
        result = verify_archive(
            arguments.repo,
            arguments.commit,
            arguments.archive,
            arguments.manifest,
        )
    elif arguments.command == "extract":
        result = extract_archive(
            arguments.repo,
            arguments.commit,
            arguments.archive,
            arguments.manifest,
            arguments.output,
        )
    else:
        result = verify_directory(
            arguments.repo,
            arguments.commit,
            arguments.manifest,
            arguments.root,
        )
    record_result(getattr(arguments, "record", None), result)


if __name__ == "__main__":
    main()
