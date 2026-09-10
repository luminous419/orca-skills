"""Build the OS-37 NATIVE stub CLI fixture, once per interpreter.

DESIGN §D5.3(4) R-A requires the pty's foreground process to have the profile's binary as
its executable IMAGE.  A shell script cannot satisfy that anywhere -- the kernel loads its
interpreter -- and both real MVP CLIs are native executables, so the fixture that stands in
for them has to be one too.  `fixtures/os37/src/os37_stub_cli.c` is that fixture; it runs
the reviewed shell fixture as a child and reimplements none of its behaviour.

The build output is deliberately NOT written into the repository: it is architecture
specific and would be an untracked binary in `scripts/fixtures/`.  It goes to a per-user
temp directory keyed by the source's own digest, so a changed source rebuilds and an
unchanged one is compiled once per machine rather than once per test.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
STUB_SRC = REPO / "scripts" / "fixtures" / "os37" / "src" / "os37_stub_cli.c"
STUB_SCRIPT = REPO / "scripts" / "fixtures" / "os37" / "bin" / "os37-stub-cli"

#: The reason a caller must use when it skips.  Declared in `tolerated_skip_manifest.txt`.
NO_COMPILER_REASON = ("the OS-37 native stub fixture needs a C compiler (cc/clang/gcc); "
                      "R-A leg 4 is an executable-image identity and a script fixture "
                      "cannot stand in for a native CLI")

_cache: dict[str, Path | None] = {}


def compiler() -> str | None:
    for name in ("cc", "clang", "gcc"):
        found = shutil.which(name)
        if found:
            return found
    return None


def native_stub_dir() -> Path | None:
    """A directory holding an executable ``os37-stub-cli`` whose IMAGE is that file.

    ``None`` when the fixture cannot be built (no source, no script, no compiler).  Callers
    skip on ``None`` rather than falling back to the shell fixture: falling back would make
    the readiness proof pass against an interpreter image, which is precisely the weakening
    R-A forbids.
    """
    if "dir" in _cache:
        return _cache["dir"]
    _cache["dir"] = _build()
    return _cache["dir"]


def _build() -> Path | None:
    if not STUB_SRC.exists() or not STUB_SCRIPT.exists():
        return None
    tool = compiler()
    if tool is None:
        return None
    key = hashlib.sha256(
        STUB_SRC.read_bytes() + str(STUB_SCRIPT).encode()).hexdigest()[:16]
    out_dir = Path(tempfile.gettempdir()) / f"os37-native-stub-{os.getuid()}-{key}"
    target = out_dir / "os37-stub-cli"
    if target.exists() and os.access(target, os.X_OK):
        return out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    staged = out_dir / f"os37-stub-cli.{os.getpid()}"
    result = subprocess.run(
        [tool, "-O0", "-o", str(staged), f'-DSTUB_SCRIPT="{STUB_SCRIPT}"', str(STUB_SRC)],
        capture_output=True, text=True, check=False, timeout=120)
    if result.returncode != 0 or not staged.exists():
        return None
    staged.chmod(0o755)
    os.replace(staged, target)          # atomic, so a parallel builder never sees a partial
    return out_dir
