#!/usr/bin/env python3
"""Build a deterministic source archive for the Orca Skill packages."""

from __future__ import annotations

import argparse
import gzip
import io
import tarfile
from pathlib import Path

from release_manifest import REPO_ROOT, archive_mode, read_version, verify_source_tree


def build_archive(output: Path, root: Path = REPO_ROOT) -> Path:
    files = verify_source_tree(root)
    version = read_version(root)
    archive_root = f"orca-skills-{version}"
    output.parent.mkdir(parents=True, exist_ok=True)

    with output.open("wb") as raw_output:
        with gzip.GzipFile(fileobj=raw_output, mode="wb", filename="", mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode="w", format=tarfile.PAX_FORMAT) as archive:
                for path in files:
                    relative = path.relative_to(root).as_posix()
                    data = path.read_bytes()
                    info = tarfile.TarInfo(f"{archive_root}/{relative}")
                    info.size = len(data)
                    info.mode = archive_mode(relative)
                    info.mtime = 0
                    info.uid = 0
                    info.gid = 0
                    info.uname = ""
                    info.gname = ""
                    archive.addfile(info, io.BytesIO(data))
    return output


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    version = read_version()
    output = args.output or REPO_ROOT / "dist" / f"orca-skills-{version}.tar.gz"
    built = build_archive(output)
    print(f"Built reproducible release archive: {built}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
