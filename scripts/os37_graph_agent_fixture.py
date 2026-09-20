"""Build the OS-37 GRAPH-LEVEL agent fixture as a NATIVE executable.

External review B-03 requires every finding to be exercised through the real production
composition root -- ``run_workflow.py --adapter standalone`` -> profile -> adapter -> the
LangGraph graph -> settlement/correction -- rather than by calling ``adapter.start`` or a
private executor helper.  Those runs need an agent that binds its answer to the dispatch it
was handed AND can be steered, per run, into the leg a given finding is about.

`os37-r10-agent` does the first but not the second and its behaviour is frozen (USER
DIRECTIVE D-H), so the scenario switches live in a SEPARATE fixture,
`scripts/fixtures/os37/bin/os37-graph-agent`, compiled here against the SAME reviewed C
trampoline `os37_native_stub` already uses -- because R-A leg 4 (DESIGN §D5.3(4)) is an
executable-IMAGE identity and a ``#!``-script's image is its interpreter.

Nothing is written into the repository: the build output is architecture specific and lands
in a per-user temp directory keyed by the sources' digest, exactly as the other two fixture
builders do.
"""
from __future__ import annotations

import hashlib
import os
import subprocess
import tempfile
from pathlib import Path

from scripts import os37_native_stub as native_stub

REPO = Path(__file__).resolve().parent.parent
AGENT_SCRIPT = REPO / "scripts" / "fixtures" / "os37" / "bin" / "os37-graph-agent"

#: The exact reason a caller must use when it skips.  Same gate as the native stub's.
NO_COMPILER_REASON = native_stub.NO_COMPILER_REASON

BINARY_NAME = "os37-graph-agent"

_cache: dict[str, Path | None] = {}


def native_agent_dir() -> Path | None:
    """A directory holding an executable ``os37-graph-agent`` whose IMAGE is that file.

    ``None`` when it cannot be built.  Callers SKIP on ``None`` rather than falling back to
    the shell script: a fallback would let the readiness proof close against an interpreter
    image, which is precisely the weakening R-A forbids.
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
    out_dir = Path(tempfile.gettempdir()) / f"os37-graph-agent-{os.getuid()}-{key}"
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
