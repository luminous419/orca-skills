"""Build the OS-37 R10 agent fixture as a NATIVE executable.

R-A leg 4 (DESIGN §D5.3(4)) is an executable-IMAGE identity, and a ``#!``-script's image
is its interpreter, so the R10 workflow E2E cannot be driven by a shell fixture directly.
This module compiles the SAME reviewed C trampoline `os37_native_stub` already uses --
`scripts/fixtures/os37/src/os37_stub_cli.c` -- against a different ``-DSTUB_SCRIPT`` so
that the foreground image is a real native binary whose child is
`scripts/fixtures/os37/bin/os37-r10-agent`.

Nothing is written into the repository: the build output is architecture specific and
lands in a per-user temp directory keyed by the source's digest, exactly as
`os37_native_stub` does.
"""
from __future__ import annotations

import hashlib
import os
import subprocess
import tempfile
from pathlib import Path

from scripts import os37_native_stub as native_stub

REPO = Path(__file__).resolve().parent.parent
AGENT_SCRIPT = REPO / "scripts" / "fixtures" / "os37" / "bin" / "os37-r10-agent"

#: The exact reason a caller must use when it skips.  Same gate as the native stub's.
NO_COMPILER_REASON = native_stub.NO_COMPILER_REASON

BINARY_NAME = "os37-r10-agent"

_cache: dict[str, Path | None] = {}


def native_agent_dir() -> Path | None:
    """A directory holding an executable ``os37-r10-agent`` whose IMAGE is that file.

    ``None`` when it cannot be built.  Callers SKIP on ``None`` rather than falling back
    to the shell script: a fallback would let the readiness proof close against an
    interpreter image, which is precisely the weakening R-A forbids.
    """
    if "dir" not in _cache:
        _cache["dir"] = _build()
    return _cache["dir"]


def _build() -> Path | None:
    source = native_stub.STUB_SRC
    if not source.exists() or not AGENT_SCRIPT.exists():
        return None
    tool = native_stub.compiler()
    if tool is None:
        return None
    key = hashlib.sha256(
        source.read_bytes() + AGENT_SCRIPT.read_bytes()
        + str(AGENT_SCRIPT).encode()).hexdigest()[:16]
    out_dir = Path(tempfile.gettempdir()) / f"os37-r10-agent-{os.getuid()}-{key}"
    target = out_dir / BINARY_NAME
    if target.exists() and os.access(target, os.X_OK):
        return out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    staged = out_dir / f"{BINARY_NAME}.{os.getpid()}"
    result = subprocess.run(
        [tool, "-O0", "-o", str(staged), f'-DSTUB_SCRIPT="{AGENT_SCRIPT}"', str(source)],
        capture_output=True, text=True, check=False, timeout=120)
    if result.returncode != 0 or not staged.exists():
        return None
    staged.chmod(0o755)
    os.replace(staged, target)          # atomic: a parallel builder never sees a partial
    return out_dir
