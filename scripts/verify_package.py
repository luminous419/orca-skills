#!/usr/bin/env python3
"""Verify source and archive contents for an Orca Skills release."""

from __future__ import annotations

import argparse
import tarfile
from pathlib import Path, PurePosixPath

from release_manifest import (
    REPO_ROOT,
    PackageError,
    archive_mode,
    read_version,
    verify_source_tree,
)


def verify_archive(archive_path: Path, root: Path = REPO_ROOT) -> None:
    expected_root = f"orca-skills-{read_version(root)}"
    expected = {
        f"{expected_root}/{path.relative_to(root).as_posix()}": path
        for path in verify_source_tree(root)
    }
    try:
        with tarfile.open(archive_path, mode="r:gz") as archive:
            members = archive.getmembers()
            archived_data = {}
            for member in members:
                extracted = archive.extractfile(member) if member.isfile() else None
                if extracted is not None:
                    archived_data[member.name] = extracted.read()
    except (OSError, tarfile.TarError) as exc:
        raise PackageError(f"cannot read release archive: {exc}") from exc

    names = {member.name for member in members}
    if len(names) != len(members):
        raise PackageError("release archive contains duplicate paths")
    expected_names = set(expected)
    if names != expected_names:
        missing = sorted(expected_names - names)
        extra = sorted(names - expected_names)
        raise PackageError(f"archive manifest mismatch; missing={missing}, extra={extra}")

    for member in members:
        path = PurePosixPath(member.name)
        if path.is_absolute() or ".." in path.parts:
            raise PackageError(f"unsafe archive path: {member.name}")
        if not member.isfile():
            raise PackageError(f"archive member is not a regular file: {member.name}")
        relative = member.name.removeprefix(f"{expected_root}/")
        expected_mode = archive_mode(relative)
        if (
            member.mtime != 0
            or member.uid != 0
            or member.gid != 0
            or member.mode != expected_mode
        ):
            raise PackageError(f"non-deterministic archive metadata: {member.name}")
        if archived_data.get(member.name) != expected[member.name].read_bytes():
            raise PackageError(f"archive content differs from source: {member.name}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", type=Path)
    args = parser.parse_args()
    try:
        files = verify_source_tree()
        if args.archive:
            verify_archive(args.archive)
    except PackageError as exc:
        print(f"Package verification FAILED: {exc}")
        return 1
    print(f"Package verification PASSED ({len(files)} source files)")
    if args.archive:
        print(f"Verified archive: {args.archive}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
