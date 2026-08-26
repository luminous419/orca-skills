#!/usr/bin/env python3
"""Reviewer execution isolation: a kernel-enforced filesystem scope for one dispatch.

D-G of `artifacts/runs/run_4d1c47c838db/DESIGN.md`, which discharges MAJOR-1 of the
external review on Draft PR #20: *the seeded answer key is not isolated from the Final
Reviewer as an EXECUTION property.*

`final_review_eval.materialize()` already builds a workspace with no `key/` and no
`adjudications/` in it, and proves that with a leak scan. That is a property of the
TREE. It says nothing about the process: the reviewer runs in the main checkout, so
`scripts/fixtures/final_review_eval/key/answer_key.json` is readable by absolute path,
by `git show`, and by `grep -r`. This module makes the claim "the Reviewer process could
not read the key" true, on a host with no container, the only way it can be made true --
a kernel-enforced scope on that process.

Three properties together define "isolated", and the attestation records all three
SEPARATELY so a partial result can never be read as a whole one:

    S1 scope           the cwd and everything reachable by relative traversal from it
                       contain no key material and no path relationship to key/
    S2 unreadability   no absolute path outside the computed readable set is readable
                       OR STAT-ABLE, including via git
    S3 cleanliness     every path that IS readable is exhaustively content-scanned --
                       BOTH classes, at NEG-5, the only per-class difference being pass
                       B's vocabulary. The recursive immutability proof is what makes
                       the Class IMM scan DURABLE (nothing unprivileged can add content
                       after it), not what replaces it: the proof never opens a file and
                       so says nothing about content that was already there. There is no
                       third way in.

The threat model is stated rather than implied: an unconstrained but WELL-BEHAVED
reviewer agent, one that reads absolute paths, runs `git` and greps broadly because
`REVIEWER_DRILL_DOWN = mandatory_and_unrestricted` tells it to. Not modelled: an
adversary that escapes a kernel sandbox, a compromised host, or a malicious operator.

Two rules the whole module is derived from:

* **Allowlist the readable set; never denylist the secret.** A profile that denied the
  repository is defeated by any copy of the key outside it -- an installed skill, an
  unpacked tarball, a second worktree. So the profile denies ALL reads and then names
  what may be read, and that named set is what the negative test scans exhaustively.
* **When the mechanism is unavailable, the label is withheld -- not the run.** Nothing
  here prevents an ordinary Final Review dispatch on a host without `sandbox-exec`.
  What is prevented is CALLING such a capture a section 7 baseline.

Repository-side tooling. Standard library only, CPython >= 3.11. It imports
`final_review_eval` (for `materialize`/`scan_leak`/`load_key` -- one implementation, not
two) and `run_logging` (for the redaction policy and P-PATH), and nothing imports it at
module scope, so there is no cycle.
"""

from __future__ import annotations

import hashlib
import json
import os
import shlex
import shutil
import stat as stat_module
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Iterable, Sequence

import final_review_eval
import run_logging

REPO_ROOT = Path(__file__).resolve().parents[1]

ISOLATION_SCHEMA_VERSION = "1.0"
ISOLATION_DOCUMENT_KIND = "final_review_isolation_attestation"
ISOLATION_FILENAME = "ISOLATION.json"
REPATRIATED_ISOLATION_FILENAME = "FINAL_REVIEW_ISOLATION.json"
REPATRIATED_WORKSPACE_DIRNAME = "final_review_workspace"
FINAL_REVIEW_REPORT_FILENAME = "FINAL_REVIEW.md"
SESSION_PREFIX = "frv_iso_"
PROFILE_FILENAME = "scope.sb"

# The two admission classes, and there is no third. A path that cannot be placed in one
# of them is not admitted at all. `SYS` -- the name the superseded root-only W_OK rule
# used -- is deliberately NOT a legal value any more, so a half-migrated writer fails
# loudly instead of producing a document that looks valid.
CLASS_IMM = "IMM"
CLASS_USR = "USR"
ADMISSION_CLASSES = (CLASS_IMM, CLASS_USR)

ENFORCEMENT_SEATBELT = "seatbelt"
ENFORCEMENT_NONE = "none"
UNENFORCED_PROBE_RESULT = "NOT_APPLICABLE_UNENFORCED"

SANDBOX_EXEC = "/usr/bin/sandbox-exec"

# The probes run under the SYSTEM python, not `sys.executable`. This is not a detail: the
# interpreter running `isolate` is frequently a user-installed one under $HOME (measured
# on this host: an Anaconda build under /Users/<name>/...), and $HOME is never admitted.
# A probe that cannot exec proves nothing, and -- worse -- would read as "everything
# denied" to a naive oracle. The system python resolves through the `/usr/bin` shim into
# the proven /Library/Developer/CommandLineTools tree, which the profile does admit.
#
# The AGENT's own interpreter is a different question, and it is the pre-flight probe's
# job to surface it as an explicit `--allow-read` rather than to guess.
SYSTEM_PYTHON = "/usr/bin/python3"
FIRMLINKS_TABLE = Path("/usr/share/firmlinks")
MOUNT_COMMAND = "/sbin/mount"

# Candidate Class IMM roots. Every one of them still has to PASS `prove_immutable()`
# before it is admitted; this list only says which roots are worth proving. The
# carve-outs are NOT listed here -- they are derived per host by
# `enumerate_boundaries()`, because a hard-coded carve-out list is a claim about
# someone else's machine.
#
# `/private/var` and `/Library` are absent BY CONSTRUCTION, and this is the F-001 fix:
# the run user's real `tempfile.gettempdir()` is a writable descendant of `/private/var`,
# and `/Library/Caches`, `/Library/Fonts` and `/Library/Frameworks/Python.framework` are
# writable descendants of `/Library`. Root non-writability does not imply descendant
# non-writability. Re-adding either wholesale reintroduces F-001; the proof will reject
# them, and T-9.9 asserts that it does.
DEFAULT_IMM_CANDIDATES = (
    "/bin",
    "/sbin",
    "/private/etc",
    "/dev",
    "/private/var/select",
    "/usr",
    "/System",
    "/Library/Developer/CommandLineTools",
)

# Carve-outs that are applied whether or not the boundary walk finds them, because
# missing one is unrecoverable rather than merely imprecise.
#
# `/System/Volumes` is the whole reason this constant exists: `/System/Volumes/Data` is
# a mount point of the WRITABLE data volume nested inside the sealed system volume, and
# it is NOT a symlink, so `os.path.realpath()` does not collapse it -- the repository and
# the answer key are reachable as `/System/Volumes/Data<repo>/.../answer_key.json`. That
# is strictly worse than F-001 as filed: F-001 needed someone to plant a copy, this needs
# nothing planted at all. Carving it out also prunes the proof walk before it descends
# into the entire data volume.
MANDATORY_CARVE_OUTS = ("/System/Volumes",)

# The one narrow I-6 exception beyond device nodes, named rather than assumed.
#
# `/dev/fd/N` is the PROBING PROCESS'S OWN descriptor table, re-exposed as a directory.
# Its entries are writable -- `/dev/fd/1` is this process's stdout -- but writing to one
# writes to the caller's own open file, not to anything a later process can read back out
# of `/dev`. It is therefore not a plant site, which is the only property I-2/I-3 exist to
# establish. Counted separately in the proof so the exemption is visible in the
# attestation rather than silently folded into `writable_files: 0`.
OWN_DESCRIPTOR_DIR = "/dev/fd"

NARROWABLE_CHECKS = ("I-1", "I-4")

# Never admitted, named here so a future edit cannot re-add them by habit.
NEVER_ADMITTED = (
    "/private/var",
    "/private/tmp",
    "/Library",
    "/opt/homebrew",
    "/Users",
    "/Applications",
    "/System/Volumes/Data",
)

# The closed default. The phase-specific `reviews/<phase>.md` is supplied per dispatch by
# an explicit `--policy-file`, because which one a dispatch needs is a property of the
# dispatch, not of this module -- and because a default that GUESSED would be the first
# step back toward "copy the skill directory".
DEFAULT_POLICY_FILES = ("orca-worker-reviewer-orchestration/reviews/common.md",)

# The marker that exists only inside the answer key's own bytes. The discovery oracle
# uses it instead of a path fragment for exactly the reason G.9 spells out: under the
# corrected profile `find <plant dir> -name answer_key.json` prints the plant path
# verbatim ON STDERR while failing, so a substring oracle would read a correct denial as
# a leak. A content probe matches this; a command probe's oracle is `rc == 0 and stdout`.
KEY_CONTENT_MARKER = "final_review_eval/v1"

NEG7_PLANT_DIRNAME = "frv_neg7_probe"


class IsolationError(Exception):
    """A fail-closed isolation failure. Maps to EXIT_LEAK_OR_FIXTURE (4)."""


class IsolationContractError(Exception):
    """A contract violation -- session placement, digests. Maps to exit 2."""


# ---- small helpers ------------------------------------------------------------------


def sha256_bytes(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def sha256_path(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def _realpath(path: Path | str) -> Path:
    return Path(os.path.realpath(str(path)))


def _is_within(candidate: Path, other: Path) -> bool:
    """True when `candidate` is `other` or lives under it. Realpaths only."""
    return candidate == other or other in candidate.parents


def _read_only_mount(path: Path | str) -> bool | None:
    try:
        return bool(os.statvfs(str(path)).f_flag & os.ST_RDONLY)
    except OSError:
        return None


def _ancestors(path: Path) -> list[Path]:
    return [path, *path.parents]


# ---- G.3.1 the recursive immutability proof -----------------------------------------


def _prune(here: Path, dirnames: list[str], carved: Sequence[Path]) -> None:
    """Drop carved children from `dirnames` BEFORE os.walk descends into them.

    Pruning the child rather than testing the child once we are standing in it is not a
    micro-optimisation: os.walk reports an unreadable directory through `onerror` at the
    moment it tries to scan it, which is before the loop body would ever see it. A
    carve-out that is only checked in the body therefore never actually prevents anything.
    """
    dirnames[:] = [
        name for name in dirnames
        if not any(_is_within(here / name, entry) for entry in carved)
    ]


def read_mount_table() -> tuple[str, ...]:
    """Mount points, from `mount(8)`. One of the two cheap boundary authorities."""
    if not Path(MOUNT_COMMAND).exists():
        return ()
    try:
        completed = subprocess.run(
            [MOUNT_COMMAND], capture_output=True, text=True, check=False, timeout=30
        )
    except (OSError, subprocess.SubprocessError):
        return ()
    points: list[str] = []
    for line in completed.stdout.splitlines():
        # "<device> on <mount point> (<flags>)"
        _, separator, tail = line.partition(" on ")
        if not separator:
            continue
        point, _, _flags = tail.rpartition(" (")
        if point:
            points.append(point)
    return tuple(sorted(set(points)))


def read_firmlinks() -> tuple[str, ...]:
    """Darwin's own system-volume -> data-volume firmlink table. The other authority."""
    try:
        raw = FIRMLINKS_TABLE.read_text(encoding="utf-8")
    except OSError:
        return ()
    return tuple(
        sorted({line.split("\t")[0] for line in raw.splitlines() if line.startswith("/")})
    )


def boundary_authorities() -> dict[str, tuple[str, ...]]:
    return {"mount_table": read_mount_table(), "firmlinks": read_firmlinks()}


def enumerate_boundaries(
    root: Path, *, carve_outs: Sequence[str] = (), authorities: dict | None = None
) -> dict:
    """Every filesystem boundary strictly inside `root`, and its explanation.

    Boundaries are FOUND by I-5 -- a `statvfs().f_flag & ST_RDONLY` that differs from the
    root's -- and cross-checked against two cheap authorities read at session-build time.
    A boundary that NEITHER authority names is a hard failure, not a warning: an
    unexplained volume boundary is a place where I-2/I-3 were never evaluated, and
    admitting it is the F-001 defect in a new location.

    Two method corrections this function exists to hold, both measured rather than
    assumed:

    * `st_dev` is NOT a boundary test. APFS firmlinks share the fsid, so `/usr` and
      `/Library` report the same `st_dev`.
    * The read-only mount flag is not the PROOF either -- `/private/etc` sits on a
      read-write volume yet is exhaustively immutable, while `/System` is a sealed
      read-only mount that nonetheless contains a writable volume's mount point. The
      flag finds boundaries; the exhaustive walk proves immutability.
    """
    authorities = authorities if authorities is not None else boundary_authorities()
    named = set(authorities.get("mount_table", ())) | set(
        authorities.get("firmlinks", ())
    )
    reference = _read_only_mount(root)
    carved = [_realpath(entry) for entry in carve_outs]
    found: list[str] = []
    unexplained: list[str] = []
    for dirpath, dirnames, _filenames in os.walk(root, followlinks=False):
        here = Path(dirpath)
        if any(_is_within(here, entry) for entry in carved):
            dirnames[:] = []
            continue
        _prune(here, dirnames, carved)
        if here != root and _read_only_mount(here) != reference:
            found.append(str(here))
            if str(here) not in named:
                unexplained.append(str(here))
            dirnames[:] = []          # never descend across a boundary
    return {
        "root": str(root),
        "mount_readonly": reference,
        "boundaries": sorted(found),
        "unexplained": sorted(unexplained),
    }


def prove_immutable(root: Path, carve_outs: Sequence[str] = ()) -> dict:
    """I-1 .. I-6, over the ENTIRE subtree rather than its root directory.

    The rule this replaces was *"admit a root when `os.access(root, os.W_OK)` is False"*,
    and it was wrong in the way that matters: root non-writability does not imply
    descendant non-writability. Under it, a sandboxed process read a byte-identical
    answer-key copy planted in the run user's real `tempfile.gettempdir()`, a writable
    descendant of the admitted `/private/var`. That is F-001.

    Returns the proof record. `passed` is the verdict, and `failures` names the first
    few offending paths so a rejection is actionable rather than merely a refusal.

        I-1  the walk completes without an unreadable directory
        I-2  no directory in the subtree is writable   (create/rename/unlink => planting)
        I-3  no regular file in the subtree is writable (overwrite => key material)
        I-4  every boundary strictly inside is enumerated and either proven or carved out
        I-5  the statvfs read-only flag is uniform across the subtree -- this is HOW
             I-4's boundaries are found
        I-6  the two narrow exceptions, both named rather than assumed: a writable
             non-regular, non-directory node is legal only when it is a character or
             block device (`/dev`), which holds no persistable file content; and
             `/dev/fd/N` is the walking process's OWN descriptor table, where "writable"
             means "this process's stdout", not a plant site

    Escaping symlinks are RECORDED but are not hits: seatbelt evaluates the RESOLVED
    target against the profile, so a symlink out of an IMM root reaches the target's own
    class or is denied. Measured on this host.

    The residual is exactly one sentence, and it is the only one: the proof is evaluated
    at session-build time against the RUN USER'S OWN privileges, so it does not bind a
    privileged (root) writer -- who is outside the stated threat model.
    """
    root = _realpath(root)
    carved = [_realpath(entry) for entry in carve_outs]
    reference = _read_only_mount(root)
    proof: dict[str, Any] = {
        "dirs": 0,
        "files": 0,
        "writable_dirs": 0,
        "writable_files": 0,
        "boundaries_found": 0,
        "carve_outs": sorted(str(entry) for entry in carved),
        "mount_readonly": reference,
        "escaping_symlinks": 0,
        "device_nodes": 0,
        "own_descriptors": 0,
        "unreadable_dirs": 0,
        "failures": [],
        "passed": False,
    }

    def fail(check: str, path: str) -> None:
        # NARROWABLE failures are recorded in full and the rest are capped for
        # readability. The asymmetry is deliberate: `prove_immutable_narrowing()` carves
        # out everything the previous round could not certify, so a capped list would
        # make it converge one directory per full subtree walk -- 13 s each on /System.
        # A certified-mutable failure needs only enough detail to be actionable.
        if check in NARROWABLE_CHECKS or len(proof["failures"]) < 20:
            proof["failures"].append({"check": check, "path": path})

    def on_error(error: OSError) -> None:
        proof["unreadable_dirs"] += 1
        fail("I-1", str(getattr(error, "filename", root)))

    for dirpath, dirnames, filenames in os.walk(
        root, followlinks=False, onerror=on_error
    ):
        here = Path(dirpath)
        if any(_is_within(here, entry) for entry in carved):
            dirnames[:] = []
            continue
        _prune(here, dirnames, carved)
        if here != root and _read_only_mount(here) != reference:   # I-5 finds I-4
            proof["boundaries_found"] += 1
            fail("I-4", str(here))
            dirnames[:] = []
            continue
        proof["dirs"] += 1
        if os.access(here, os.W_OK):                                # I-2
            proof["writable_dirs"] += 1
            fail("I-2", str(here))
        for name in filenames:
            entry = here / name
            proof["files"] += 1
            try:
                stat = entry.lstat()
            except OSError:
                fail("I-1", str(entry))
                continue
            if os.path.islink(entry):
                target = _realpath(entry)
                if not _is_within(target, root):
                    proof["escaping_symlinks"] += 1
                continue
            mode = stat.st_mode
            if str(here) == OWN_DESCRIPTOR_DIR:
                proof["own_descriptors"] += 1                       # I-6, exempted
                continue
            if stat_module.S_ISREG(mode):
                if os.access(entry, os.W_OK):                       # I-3
                    proof["writable_files"] += 1
                    fail("I-3", str(entry))
            elif stat_module.S_ISCHR(mode) or stat_module.S_ISBLK(mode):
                proof["device_nodes"] += 1                          # I-6
            elif os.access(entry, os.W_OK):
                fail("I-6", str(entry))

    proof["passed"] = not proof["failures"]
    return proof


# Failures the proof can NARROW its way past, and failures it cannot. The distinction is
# the whole difference between the designed remedy and the anti-pattern RK-1 warns about:
#
#   I-1 (a directory the run user cannot enumerate) and I-4 (a filesystem boundary) mean
#   "this subtree cannot be CERTIFIED". Carving it out DENIES it in the profile, which is
#   strictly narrower than admitting it. Safe to automate.
#
#   I-2 / I-3 / I-6 (a writable directory, a writable regular file, a writable non-device
#   node) mean "this subtree is certified MUTABLE". Carving those out would be widening
#   the proof until it passes while the profile kept allowing the parent -- which is
#   exactly the F-001 shape in a new place. A root that fails these is DROPPED.


def prove_immutable_narrowing(
    root: Path, carve_outs: Sequence[str] = (), *, rounds: int = 4
) -> tuple[dict, list[str]]:
    """`prove_immutable()`, applying the designed remedy for a narrowable failure.

    "A root whose proof fails is either narrowed by a carve-out and re-proven, demoted to
    Class USR and content-scanned, or dropped." This is the first branch, automated and
    bounded: each round denies the paths the previous round could not certify, and the
    carve-outs it accumulates are the SAME list the profile denies -- one variable, which
    is what `assert_carve_outs_denied()` then proves stayed true.
    """
    carved = list(dict.fromkeys(str(_realpath(entry)) for entry in carve_outs))
    proof = prove_immutable(root, carve_outs=carved)
    for _round in range(rounds):
        if proof["passed"]:
            break
        narrowable = [
            failure["path"]
            for failure in proof["failures"]
            if failure["check"] in NARROWABLE_CHECKS
        ]
        if not narrowable or all(entry in carved for entry in narrowable):
            break
        carved.extend(entry for entry in narrowable if entry not in carved)
        proof = prove_immutable(root, carve_outs=carved)
    return proof, sorted(set(carved))



# ---- G.3 the readable set -------------------------------------------------------------


# Pass "S" is the escaping-symlink check, and it belongs to Class USR only. The two
# classes settle that case in OPPOSITE directions, and each is right about its own class:
#
#   Class USR -- the scan IS the evidence, so a symlink whose realpath leaves the root is
#     an allowed read path the walk did not cover. That is a gap, and a gap is a hit.
#   Class IMM -- the PROFILE is the evidence. Seatbelt evaluates the RESOLVED target, so a
#     symlink out of an IMM root reaches the target's own class or is denied on the
#     target's own terms; the link grants nothing the profile does not already grant.
#     Measured, and it matters: /System and /Library/Developer/CommandLineTools ship
#     hundreds of vendor symlinks out of themselves, so treating them as hits would fail
#     every capture on this host while proving nothing.
SCAN_PASSES_ALL = ("A", "B", "C", "D", "S")
# Class IMM: every pass except S. Pass B is MANDATORY here, not flagged and not
# default-off -- a content-cleanliness gate the default capture does not run is not a
# gate, and the section 7 baseline is taken with the default (DESIGN D-5.1). The ONLY
# per-class difference is pass B's vocabulary: Class IMM runs `key_material_tokens()`
# and no count heuristics, because the eleven natural-language tokens
# `key_leak_tokens()` adds are measured to produce 40 false hard failures under `/usr`
# alone. The recursive immutability proof is NOT a substitute for this scan and is
# nowhere offered as one: it establishes current write incapability and says nothing
# about content that was already there, because it never opens a file.
SCAN_PASSES_IMM = ("A", "B", "C", "D")


SCAN_VOCABULARIES = ("key_leak", "key_material")


def scan_readable_set(
    key: dict,
    root: Path,
    *,
    passes: Sequence[str] = SCAN_PASSES_ALL,
    carve_outs: Sequence[str] = (),
    vocabulary: str = "key_leak",
) -> dict:
    """Passes A-S over an admitted root. Any hit is a hard failure at the call site.

    There is deliberately no `--ignore`: the operator's remedy is to remove the copy, or
    to stop allowing that root. A scanner that can be told to skip reviewer-visible
    content proves nothing about the content it skipped.

        A  name       a file named `answer_key.json`, or a `key`/`adjudications`
                      component under a directory that also holds a `subject/`
        B  content    `scan_leak_text()` -- the same per-file test `scan_leak()` runs --
                      over every regular file THIS walk reaches, so the carve-outs are
                      honoured. Exhaustive over files: no size cap, no extension filter,
                      no type gate, no sampling, each of which would leave a PLACEMENT
                      that defeats the gate. `vocabulary` selects the token set, and it
                      is the whole of the per-class difference.
        C  key digest a renamed byte-identical copy, under a size prefilter that is an
                      equivalence rather than an approximation: a file whose length
                      differs from the key's cannot be byte-identical to it
        D  archive    member NAMES of every tar/zip under the root. Members are NEVER
                      extracted and never read; this is what catches a packaged copy in
                      `dist/orca-skills-*.tar.gz`. An archive that cannot be enumerated
                      counts as a hit, because it cannot be certified clean.

        S  symlink   a symlink whose realpath escapes the root -- an allowed read path
                      the walk did not cover. Class USR only; see SCAN_PASSES_ALL for why
                      Class IMM settles this case the other way.

    Walks follow no symlink. Refusing to follow one costs no content coverage: a link
    whose target is inside the root is reached as a real file by the walk itself, and a
    link whose target is outside it is the target's own class's problem -- which is what
    pass S says for Class USR and what the profile says for Class IMM.
    """
    import tarfile
    import zipfile

    if vocabulary not in SCAN_VOCABULARIES:
        raise IsolationError(
            f"unknown scan vocabulary {vocabulary!r}; expected one of "
            f"{SCAN_VOCABULARIES}"
        )
    root = _realpath(root)
    carved = [_realpath(entry) for entry in carve_outs]
    hits: list[dict] = []
    counters = {"files": 0, "archives": 0, "content_scanned": 0}
    key_digest = _answer_key_digest(key) if "C" in passes else None
    key_size = _answer_key_size(key) if "C" in passes else None
    b_tokens = (
        (
            final_review_eval.key_leak_tokens(key)
            if vocabulary == "key_leak"
            else final_review_eval.key_material_tokens(key)
        )
        if "B" in passes
        else set()
    )

    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        here = Path(dirpath)
        if any(_is_within(here, entry) for entry in carved):
            dirnames[:] = []
            continue
        _prune(here, dirnames, carved)
        has_subject = "subject" in dirnames
        for name in list(dirnames):
            if has_subject and name in ("key", "adjudications"):
                hits.append({"pass": "A", "path": str(here / name), "why": "fixture tree"})
            if "S" in passes and os.path.islink(here / name):
                target = _realpath(here / name)
                if not _is_within(target, root):
                    hits.append(
                        {"pass": "S", "path": str(here / name), "why": "escaping symlink"}
                    )
        for name in filenames:
            entry = here / name
            counters["files"] += 1
            if os.path.islink(entry):
                if "S" in passes and not _is_within(_realpath(entry), root):
                    hits.append(
                        {"pass": "S", "path": str(entry), "why": "escaping symlink"}
                    )
                continue
            if "A" in passes and name == "answer_key.json":
                hits.append({"pass": "A", "path": str(entry), "why": "answer_key.json"})
            if "D" in passes and name.endswith((".tar", ".tar.gz", ".tgz", ".zip")):
                counters["archives"] += 1
                hits.extend(_scan_archive_members(entry, tarfile, zipfile))
            if "B" in passes and "__pycache__" not in entry.parts:
                # Driven from THIS walk, not from scan_leak()'s rglob. scan_leak() has
                # no exclusion parameter by design, so delegating to it over a root that
                # has carve-outs would scan beneath a boundary the profile denies -- and
                # a Class IMM root is exactly the case that has carve-outs. The per-file
                # test is the same one either way; only the walk differs.
                try:
                    text = entry.read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError):
                    pass
                else:
                    counters["content_scanned"] += 1
                    hits.extend(
                        {"pass": "B", **hit}
                        for hit in final_review_eval.scan_leak_text(
                            entry,
                            text,
                            b_tokens,
                            count_heuristics=(vocabulary == "key_leak"),
                        )
                    )
            if key_digest is None:
                continue
            if key_size is not None:
                # An equivalence, not an approximation: a file whose length differs from
                # the key's cannot be byte-identical to it. An OSError from lstat() falls
                # through to hashing, so the prefilter can never turn an unreadable file
                # into a silent pass.
                try:
                    if entry.lstat().st_size != key_size:
                        continue
                except OSError:
                    pass
            try:
                digest = sha256_path(entry)
            except OSError:
                continue
            if key_digest and digest == key_digest:
                hits.append({"pass": "C", "path": str(entry), "why": "key digest"})

    # Stable now that pass B's hits interleave with A/C/D's instead of being appended
    # after them: the record an attestation carries must not depend on walk order.
    hits.sort(key=lambda hit: (hit["pass"], hit["path"]))
    return {
        "root": str(root),
        **counters,
        "passes": list(passes),
        "vocabulary": vocabulary,
        "carve_outs": [str(entry) for entry in carved],
        "hits": hits,
    }


def _scan_archive_members(path: Path, tarfile_module, zipfile_module) -> list[dict]:
    """Pass D. Names only -- no member is ever extracted and no member is ever read."""
    try:
        if path.suffix == ".zip":
            with zipfile_module.ZipFile(path) as archive:
                names = archive.namelist()
        else:
            with tarfile_module.open(path) as archive:
                names = archive.getnames()
    except Exception:                                     # noqa: BLE001 -- see docstring
        return [
            {
                "pass": "D",
                "path": str(path),
                "why": "archive could not be enumerated, so it cannot be certified clean",
            }
        ]
    hits = []
    for name in names:
        parts = Path(name).parts
        if Path(name).name == "answer_key.json" or "adjudications" in parts:
            hits.append({"pass": "D", "path": f"{path}::{name}", "why": "packaged copy"})
        elif "key" in parts and any(part == "subject" for part in parts):
            hits.append({"pass": "D", "path": f"{path}::{name}", "why": "packaged copy"})
    return hits


def _answer_key_digest(key: dict) -> str | None:
    """The digest pass C compares against, if the caller handed us the key's own path."""
    path = key.get("__source_path__")
    if not path:
        return None
    try:
        return sha256_path(Path(path))
    except OSError:
        return None


def _answer_key_size(key: dict) -> int | None:
    """Pass C's size prefilter, read from the SAME path `_answer_key_digest()` reads.

    One source of truth for "which file is the key". Returns None on a missing path or
    an OSError, exactly as `_answer_key_digest()` does -- and when it is None, pass C is
    already inert because `key_digest` is None too.
    """
    path = key.get("__source_path__")
    if not path:
        return None
    try:
        return Path(path).stat().st_size
    except OSError:
        return None


def assert_no_unscanned_descendant(readable: Sequence[dict]) -> None:
    """The invariant F-001 violated, checked by name.

    For every admitted path `p`, every path reachable under `p` is covered by either the
    IMM proof or the USR scan. Equivalently: no admitted path may be a proper ancestor of
    a mutable path that is not itself proven or scanned.

    `(subpath "/private/var")` was an admitted proper ancestor of
    `tempfile.gettempdir()`, which is mutable and was never scanned. That is the whole
    bug, and this is the assertion that would have caught it.
    """
    for entry in readable:
        if entry["class"] not in ADMISSION_CLASSES:
            raise IsolationError(
                f"{entry['path']}: admission class {entry['class']!r} is not one of "
                f"{ADMISSION_CLASSES} -- there is no third way in"
            )
        if entry["class"] == CLASS_IMM:
            proof = entry.get("proof") or {}
            if not proof.get("passed"):
                raise IsolationError(
                    f"{entry['path']}: admitted as {CLASS_IMM} without a passing proof"
                )
            if proof.get("writable_dirs") or proof.get("writable_files"):
                raise IsolationError(
                    f"{entry['path']}: admitted as {CLASS_IMM} with "
                    f"{proof.get('writable_dirs')} writable directories and "
                    f"{proof.get('writable_files')} writable regular files beneath it -- "
                    "this is the F-001 shape"
                )
        elif not entry.get("scanned"):
            raise IsolationError(
                f"{entry['path']}: admitted as {CLASS_USR} without being scanned"
            )
    for entry in readable:
        for other in readable:
            if entry is other:
                continue
            here, there = _realpath(entry["path"]), _realpath(other["path"])
            if here != there and _is_within(there, here):
                # A nested admitted path is not itself a defect -- both are covered.
                # What would be a defect is an admitted ancestor of something covered by
                # NEITHER, and that is impossible once every entry above is proven or
                # scanned. The check is kept explicit so the invariant is visible.
                continue


def assert_carve_outs_denied(carve_outs: Sequence[str], denied: Sequence[str]) -> None:
    """The two lists are generated from one variable; this proves they stayed that way.

    A boundary carved out of the proof but not denied in the profile is exactly the F-001
    defect in a new place: the proof stopped looking, and the profile kept allowing.
    """
    missing = sorted(set(carve_outs) - set(denied))
    if missing:
        raise IsolationError(
            "carve-outs excluded from the immutability proof are not denied by the "
            f"profile: {missing}"
        )


def compute_traversal_set(readable: Sequence[str], carve_outs: Sequence[str]) -> list[str]:
    """G.4 clause 2: metadata for exactly the components needed to resolve the set.

    NOT a global `(allow file-read-metadata)`. That makes every path on the machine
    stat-able and existence-checkable, which is a discovery channel and not a theoretical
    one: measured on this host with an otherwise-correct readable set,
    `os.path.exists(<planted key copy>)` returned True and `os.stat(...).st_size` returned
    the key's real size, 9347. With the closed set, `exists()` returns False and `stat`
    raises. The metadata surface can never exceed the data surface.
    """
    needed: set[str] = {"/"}
    for group in (readable, carve_outs):
        for entry in group:
            for ancestor in _ancestors(Path(entry)):
                needed.add(str(ancestor))
    # Root-level symlinks that SPELL a readable path -- /var, /etc, /tmp on darwin. An
    # unresolved spelling does not match a profile clause, so the spelling itself has to
    # be traversable or the resolution never starts.
    try:
        for child in Path("/").iterdir():
            if not child.is_symlink():
                continue
            target = _realpath(child)
            if any(_is_within(_realpath(entry), target) for entry in readable):
                needed.add(str(child))
    except OSError:
        pass
    resolved = {entry for entry in readable}
    return sorted(needed - resolved | {"/"})


# ---- G.4 the scope profile -------------------------------------------------------------


def _clause(paths: Iterable[str], form: str = "subpath") -> str:
    rendered = [f'    ({form} "{path}")' for path in paths]
    return "\n".join(rendered)


def render_seatbelt_profile(
    *,
    session: Path,
    imm: Sequence[str],
    usr: Sequence[str],
    carve_outs: Sequence[str],
    traversal: Sequence[str],
    writable: Sequence[str],
    denied: Sequence[str],
) -> str:
    """The six clauses, in this order. Seatbelt is LAST-MATCH-WINS, so order IS meaning.

    Two clauses look redundant and are not. Clause 4's carve-out denies cover boundaries
    the proof stopped at, and clause 6's `file-read-metadata` line is what turns
    `os.stat(<key>)` from a successful call returning the real size into
    `Operation not permitted` -- measured. Neither may be dropped as "already covered by
    clause 1": a future edit that widens clause 3 must not silently widen these.
    """
    return f"""(version 1)
;; Generated by scripts/review_isolation.py -- do not edit. Session: {session}
(allow default)

;; 1. Deny every file read -- data AND metadata. Allowlist, not denylist: a profile
;;    that denied only the repository is defeated by any copy of the key outside it.
(deny file-read*)

;; 2. Traversal set: metadata ONLY, on the exact path components needed to resolve
;;    the readable set, plus the root-level symlinks that spell them. NOT a global
;;    allow -- a global (allow file-read-metadata) makes every path on the machine
;;    stat-able and existence-checkable, which is a discovery channel, and NEG-7
;;    fails on it.
(allow file-read-metadata
{_clause(traversal, "literal")}
    (subpath "/dev"))

;; 3. The readable set: one (subpath) per Class IMM root and per Class USR root.
;;    file-read* is data + metadata, so nothing here can be readable-but-not-stat-able
;;    or the reverse. (literal "/") is required or dyld aborts the process at exec.
(allow file-read*
    (literal "/")
{_clause(list(imm) + list(usr))})

;; 4. Carve-outs: every boundary the immutability proof excluded, denied for data AND
;;    metadata, generated from the same variable as the proof's carve-out list.
;;    assert_carve_outs_denied() refuses to write this profile if the two differ.
(deny file-read* file-read-metadata
{_clause(carve_outs)})

;; 5. Writes: deny everything, then name the writable set. The session-scoped tmp/home
;;    are what let clause 3 drop the host's /private/var and /Library entirely.
(deny file-write*)
(allow file-write*
{_clause(writable)}
    (subpath "/dev"))

;; 6. The key-bearing roots, denied for BOTH data and metadata, after everything else
;;    so that EXISTENCE itself is hidden. Redundant with (1) for reads; kept because a
;;    future edit that widens (3) must not silently widen these, and because it makes
;;    the profile self-documenting about what it is protecting. Without the
;;    file-read-metadata line, os.stat() on the key SUCCEEDS -- measured.
(deny file-read-metadata
{_clause(denied)})
(deny file-read* file-read-metadata file-write*
{_clause(denied)})
"""


def wrap_command(session: Path, command: str) -> str:
    """The launch line, and it is the one thing the negative test must not re-implement.

    `cd` first, `exec` second, and the order is not cosmetic: `git` inside the sandbox
    fails with *"Unable to read current working directory"* when cwd is the denied
    repository, so the shell -- which is NOT sandboxed -- has to move into `review_root`
    before it is replaced by the sandboxed process.

    `TMPDIR` and `HOME` point at session-scoped directories, and that is what makes
    dropping the host's `/private/var` and `/Library` survivable: with the corrected
    profile and the host `HOME`, `git` fails on `~/.gitconfig`; with the session `HOME`,
    `git --version` exits 0 while `git -C <repo> show HEAD:VERSION` still fails, which is
    exactly the pair of outcomes intended.

    They are part of the LAUNCH LINE rather than of `agent_command` for the reuse-gate
    reason the sandbox wrapper is: the gate compares the resolved role command, so
    wrapping must be applied at launch or every isolated dispatch looks like a different
    agent to the gate.
    """
    review_root = shlex.quote(str(session / "review_root"))
    tmp = shlex.quote(str(session / "tmp"))
    home = shlex.quote(str(session / "home"))
    profile = shlex.quote(str(session / "control" / PROFILE_FILENAME))
    return (
        f"cd {review_root} && TMPDIR={tmp} HOME={home} "
        f"exec {SANDBOX_EXEC} -f {profile} {command}"
    )


# ---- G.2 the isolation session ----------------------------------------------------------


def _load_key_with_source(fixture: Path) -> dict:
    key_path = fixture / "key" / "answer_key.json"
    key = final_review_eval.load_key(key_path)
    # Pass C needs the key file's own digest and nothing else out of it. Carried under a
    # dunder name so it cannot collide with a schema field.
    key["__source_path__"] = str(key_path)
    return key


def discover_key_bearing_roots(fixture: Path) -> list[str]:
    """The roots the profile denies for both content and metadata.

    The repository checkout (which holds the fixture, its `.git` blob of the key, and any
    `dist/*.tar.gz` release archive) plus the fixture itself when it lives elsewhere.
    Ancestors absorb descendants so the deny list is minimal and readable.
    """
    candidates = {_realpath(REPO_ROOT), _realpath(fixture)}
    minimal: list[Path] = []
    for candidate in sorted(candidates, key=lambda entry: len(entry.parts)):
        if not any(_is_within(candidate, kept) for kept in minimal):
            minimal.append(candidate)
    return [str(entry) for entry in minimal]


def compute_readable_set(
    session: Path,
    key: dict,
    *,
    imm_candidates: Sequence[str] = DEFAULT_IMM_CANDIDATES,
    allow_read: Sequence[str] = (),
    authorities: dict | None = None,
) -> dict:
    """Ordered, deduplicated REALPATHS, each in exactly one of the two classes.

    Symlinks are resolved before anything else -- `/tmp` -> `/private/tmp` on darwin --
    because an unresolved spelling in a seatbelt profile does not match.

    Class IMM is admitted on `prove_immutable()` and is not additionally scanned AT
    SESSION-BUILD TIME -- which is what the `scanned: false` field records, and all it
    records. Class IMM roots ARE content-scanned: the scan runs once per capture, at
    NEG-5, with the mandatory pass B of DESIGN D-5.1 and the `key_material` vocabulary,
    and `probes[NEG-5].roots[].content_scanned` reports how many files it opened. The
    proof is not a substitute for that scan and is nowhere offered as one -- it
    establishes current write incapability and says nothing about pre-existing content,
    because it never opens a file.

    Class USR is mutable and is admitted only after `scan_readable_set()` returns zero
    hits in every pass. A root that can be placed in neither class is not admitted.
    """
    authorities = authorities if authorities is not None else boundary_authorities()
    never = {_realpath(entry) for entry in NEVER_ADMITTED}
    entries: list[dict] = []
    carve_outs: list[str] = []

    for candidate in imm_candidates:
        root = Path(candidate)
        if not root.exists():
            continue
        root = _realpath(root)
        if root in never:
            raise IsolationError(
                f"{root} is on the never-admitted list; admitting it wholesale is F-001"
            )
        mandatory = [
            str(_realpath(entry))
            for entry in MANDATORY_CARVE_OUTS
            if Path(entry).exists() and _is_within(_realpath(entry), root)
        ]
        boundaries = enumerate_boundaries(
            root, carve_outs=mandatory, authorities=authorities
        )
        if boundaries["unexplained"]:
            raise IsolationError(
                f"{root}: filesystem boundaries named by neither the mount table nor "
                f"{FIRMLINKS_TABLE} were found: {boundaries['unexplained']} -- an "
                "unexplained boundary is a hard failure, not a warning"
            )
        proof, root_carve_outs = prove_immutable_narrowing(
            root, sorted(set(mandatory) | set(boundaries["boundaries"]))
        )
        if not proof["passed"]:
            raise IsolationError(
                f"{root}: the recursive immutability proof FAILED -- "
                f"{proof['writable_dirs']} writable directories, "
                f"{proof['writable_files']} writable regular files, first failures "
                f"{proof['failures'][:3]}. A root whose proof fails is narrowed by a "
                "carve-out and re-proven, demoted to Class USR and content-scanned, or "
                "dropped. It is never admitted unproven."
            )
        carve_outs.extend(root_carve_outs)
        entries.append(
            {"class": CLASS_IMM, "path": str(root), "scanned": False, "proof": proof}
        )

    usr_roots = [
        session / "review_root",
        session / "tmp",
        session / "home",
        *[Path(entry) for entry in allow_read],
    ]
    for candidate in usr_roots:
        root = _realpath(candidate)
        # The test is "is this root ITSELF never-admitted, or an ANCESTOR of one" -- not
        # "is it under one". A Class USR root is allowed to be a DESCENDANT of a
        # never-admitted path, and the session is exactly that: the default session base
        # is `tempfile.gettempdir()`, i.e. a descendant of /private/var. That is the
        # no-unscanned-descendant invariant read in the right direction -- admitting the
        # scanned `<SESSION>/review_root` is safe, admitting its unscanned ancestor
        # /private/var is F-001. Rejecting the descendant would forbid the only layout
        # G.2 has.
        if root in never or any(_is_within(entry, root) for entry in never):
            raise IsolationError(
                f"{root} is, or is an ancestor of, a never-admitted path -- admitting it "
                "would make an unscanned mutable tree readable, which is F-001"
            )
        scan = scan_readable_set(key, root)
        if scan["hits"]:
            raise IsolationError(
                f"{root}: key material is reachable from an admitted Class USR root: "
                + json.dumps(scan["hits"][:5], ensure_ascii=False)
            )
        entries.append(
            {
                "class": CLASS_USR,
                "path": str(root),
                "scanned": True,
                "scan": {
                    "files": scan["files"],
                    "archives": scan["archives"],
                    "hits": len(scan["hits"]),
                },
            }
        )

    assert_no_unscanned_descendant(entries)
    return {
        "entries": entries,
        "carve_outs": sorted(set(carve_outs)),
        "authorities": {name: list(value) for name, value in authorities.items()},
    }


def build_session(
    run_id: str,
    *,
    fixture: Path,
    session_base: Path | None = None,
    policy_files: Sequence[str] = DEFAULT_POLICY_FILES,
    repo_root: Path = REPO_ROOT,
) -> Path:
    """The six rules of G.2, every one of them enforced before this returns.

    Layout, and `control/` is a SIBLING of `review_root/` rather than a child, because it
    holds the generated profile -- which necessarily NAMES the denied roots, i.e. it
    contains the repository path and hence the key's directory path. Putting it inside
    `review_root/` would hand the Reviewer the exact path NEG-1 exists to prove absent.

        <SESSION>/review_root/{subject,policy,artifacts/runs/<run-id>}
        <SESSION>/tmp   <SESSION>/home        session-scoped TMPDIR and HOME
        <SESSION>/control/{scope.sb,ISOLATION.json,probes/}
    """
    base = Path(session_base) if session_base else Path(tempfile.gettempdir())
    session = Path(tempfile.mkdtemp(prefix=SESSION_PREFIX, dir=str(base)))
    try:
        resolved = _realpath(session)
        # Rule 1 -- checked, not assumed. If TMPDIR were ever configured inside the
        # repository, this fails the command rather than producing a session that
        # silently satisfies nothing.
        for other in (_realpath(fixture), _realpath(repo_root)):
            if _is_within(resolved, other) or _is_within(other, resolved):
                raise IsolationContractError(
                    f"the isolation session {resolved} has a path relationship to "
                    f"{other}; a session inside the tree it is isolating from is not a "
                    "session"
                )
        review_root = session / "review_root"
        (session / "tmp").mkdir()
        (session / "home").mkdir()
        control = session / "control"
        (control / "probes").mkdir(parents=True)
        review_root.mkdir()
        (review_root / "artifacts" / "runs" / run_id).mkdir(parents=True)

        final_review_eval.materialize(review_root / "subject", fixture)

        # Rule 3 -- a closed, explicit list. There is no glob and no "copy the skill
        # directory": the skill directory contains tools/run_logging.py and, in a
        # packaged install, could contain the fixture itself.
        policy = review_root / "policy"
        policy.mkdir()
        for relative in policy_files:
            source = repo_root / relative
            if not source.exists():
                raise IsolationContractError(f"policy file does not exist: {relative}")
            # Rule 2 -- no symlink is created and none is followed.
            if source.is_symlink():
                raise IsolationError(
                    f"policy file {relative} is a symlink; a symlink into the repository "
                    "would defeat S1 while passing a naive walk"
                )
            flattened = "REVIEW_" + Path(relative).stem.upper() + Path(relative).suffix
            shutil.copy2(str(source), str(policy / flattened), follow_symlinks=False)

        # Rule 4 -- everything that landed is leak-scanned with the SAME function and no
        # exclusions, so the policy files are scanned exactly as the subject tree is.
        key = _load_key_with_source(fixture)
        hits = final_review_eval.scan_leak(key, [review_root])
        if hits:
            raise IsolationError(
                "key material reached review_root: "
                + json.dumps(hits[:5], ensure_ascii=False)
            )
        for path in review_root.rglob("*"):
            if path.is_symlink():
                raise IsolationError(f"a symlink reached review_root: {path}")
        return session
    except BaseException:
        shutil.rmtree(session, ignore_errors=True)   # a half-built session is worse than none
        raise


def teardown(session: Path) -> None:
    """The only removal path, and it refuses anything that is not one of our sessions.

    A mistyped argument must not be able to delete an unrelated tree.
    """
    session = Path(session)
    if not session.name.startswith(SESSION_PREFIX):
        raise IsolationContractError(
            f"{session} is not an isolation session (its name does not start with "
            f"{SESSION_PREFIX!r})"
        )
    if not (session / "control" / ISOLATION_FILENAME).is_file():
        raise IsolationContractError(
            f"{session} carries no control/{ISOLATION_FILENAME}; refusing to remove a "
            "directory that is not a completed isolation session"
        )
    shutil.rmtree(session)


# ---- G.9 the negative-test contract --------------------------------------------------
# Executed by `isolate` itself (results recorded in ISOLATION.json) AND asserted by
# scripts/test_review_isolation.py, so the guarantee is tested in CI and re-proved at
# every capture.
#
# The probe program is passed to python3 as `-c` source on the command line rather than
# written to a file. That is not a convenience: a probe SCRIPT would have to live
# somewhere the sandboxed process can read, i.e. inside the session's readable set, and
# it necessarily contains the answer key's absolute path -- which is the exact string
# NEG-1 exists to prove is absent from anything the Reviewer can read.

# Every operation here is about the TARGET PATH ITSELF. There is deliberately no probe
# of `os.path.dirname(target)`: a plant's parent directory and the target are different
# paths with different verdicts -- a symlink planted INSIDE review_root has a parent that
# is supposed to be listable -- so a parent probe folded into this battery reports a
# correct configuration as a leak. A parent that must be denied is passed in as its own
# named target instead, which is what NEG-7 does.
_PROBE_SOURCE = r"""
import json, os, sys
mode, targets = sys.argv[1], json.loads(sys.argv[2])
out = {}
def attempt(label, thunk):
    try:
        out[label] = {"ok": True, "value": thunk()}
    except Exception as error:
        out[label] = {"ok": False, "error": type(error).__name__}
for name, path in targets.items():
    attempt(name + ".open", lambda p=path: __import__("hashlib").sha256(
        open(p, "rb").read()).hexdigest())
    attempt(name + ".exists", lambda p=path: os.path.exists(p))
    attempt(name + ".stat", lambda p=path: os.stat(p).st_size)
    attempt(name + ".listdir", lambda p=path: len(os.listdir(p)))
print(json.dumps({"mode": mode, "probes": out}))
"""


def _probe_python() -> str:
    return SYSTEM_PYTHON if Path(SYSTEM_PYTHON).exists() else sys.executable


def _run_probe(session: Path | None, targets: dict[str, str], *, sandboxed: bool) -> dict:
    """One probe process. Sandboxed runs go through `wrap_command()` -- the SAME function
    the real dispatch uses. A negative test that builds its own launch line proves
    something about the test, not about the dispatch."""
    inner = (
        f"{shlex.quote(_probe_python())} -c {shlex.quote(_PROBE_SOURCE)} "
        f"{shlex.quote('sandboxed' if sandboxed else 'control')} "
        f"{shlex.quote(json.dumps(targets))}"
    )
    command = wrap_command(session, inner) if sandboxed else inner
    completed = subprocess.run(
        ["/bin/sh", "-c", command], capture_output=True, text=True, check=False,
        timeout=120,
    )
    try:
        return json.loads(completed.stdout)["probes"]
    except (ValueError, KeyError):
        return {"__failed__": {"ok": False, "error": completed.stderr.strip()[:400]}}


def _command_probe(session: Path, command: str) -> dict:
    """The discovery oracle, and the obvious one is wrong.

    A discovery is *successful, non-empty stdout* -- never "the probed path appears
    somewhere in the output". Measured: under the corrected profile
    `find <plant dir> -name answer_key.json` exits 1 and prints
    `find: <plant dir>: Operation not permitted` ON STDERR, which contains the plant path
    verbatim. A substring oracle reads that correct denial as a leak.
    """
    completed = subprocess.run(
        ["/bin/sh", "-c", wrap_command(session, command)],
        capture_output=True, text=True, check=False, timeout=120,
    )
    discovered = completed.returncode == 0 and bool(completed.stdout.strip())
    return {
        "command": command.split()[0],
        "rc": completed.returncode,
        "discovered": discovered,
        "leaked_key_content": KEY_CONTENT_MARKER in completed.stdout,
    }


def _ancestor_metadata_exempt(label: str) -> bool:
    """True for the one probe whose success is forced by G.4 clause 2 and is not a leak.

    The session lives inside the host's temp directory, so that directory is an ANCESTOR
    of `review_root` -- and clause 2 must keep every ancestor stat-able or the kernel
    cannot resolve the session path at all. `exists()` and `stat()` on it therefore
    succeed by construction, and they reveal nothing the sandboxed process does not
    already know: it is standing in that tree, so its own cwd names every one of those
    ancestors.

    What WOULD be a discovery channel is ENUMERATION -- `os.listdir` on the temp root
    returning its 1,548 entries, which is how a stray key copy is found. That is denied,
    it is the operation the design names for this target, and it is the one asserted
    below. The exemption is this narrow and is spelled out rather than achieved by
    quietly dropping the target.
    """
    return label.startswith("temp_root.") and not label.endswith(".listdir")


def _all_denied(probes: dict) -> bool:
    """Every read fails, and `exists` in particular returns False rather than raising.

    `exists` is the one probe whose SUCCESS is not the failure mode: it never raises, so
    the assertion is on its VALUE. Discovery, not just reading, is what has to be blocked.
    """
    if not probes or "__failed__" in probes:
        # FAIL CLOSED. A probe process that never produced a result is not evidence of
        # denial -- it is an absence of evidence, and treating it as a PASS is how a
        # negative test comes to assert nothing at all.
        return False
    for label, result in probes.items():
        if label.endswith(".exists"):
            if result.get("ok") and result.get("value"):
                return False
        elif result.get("ok"):
            return False
    return True


def _alias_spellings(path: str, authorities: dict) -> list[str]:
    """Every spelling of `path` derived from the mount table and the firmlink table.

    `/System/Volumes/Data<path>` is the one that matters. The design does NOT generate
    per-alias deny clauses -- seatbelt canonicalizes a firmlinked spelling to the same
    vnode before matching, measured on this host -- but NEG-8 probes them anyway, because
    "the sandbox canonicalizes" is a property of this OS release, not of this design.
    """
    aliases = []
    data_volume = "/System/Volumes/Data"
    if Path(data_volume).exists() and not path.startswith(data_volume):
        aliases.append(data_volume + path)
    for point in authorities.get("firmlinks", ()):
        if path.startswith(point + "/"):
            aliases.append(data_volume + path)
    return sorted(set(aliases))


def run_probes(
    session: Path,
    *,
    fixture: Path,
    readable: dict,
    denied: Sequence[str],
    enforcement: str,
    plant: bool = True,
) -> list[dict]:
    """NEG-0 .. NEG-8. NEG-0 runs FIRST and its failure discards everything after it."""
    key_path = _realpath(fixture / "key" / "answer_key.json")
    adjudications = sorted((fixture / "adjudications").glob("*.json"))
    review_root = session / "review_root"
    probes: list[dict] = []
    targets = {"key": str(key_path)}
    if adjudications:
        targets["adjudication"] = str(_realpath(adjudications[0]))

    # -- NEG-0 -- the positive control. Both reads must SUCCEED unsandboxed and the bytes
    # must equal the key file's. A negative test that passes because the probe used the
    # wrong path proves nothing, so this is not a separate test that could be skipped.
    control = _run_probe(None, targets, sandboxed=False)
    expected = hashlib.sha256(key_path.read_bytes()).hexdigest()
    git_control = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "show",
         f"HEAD:{key_path.relative_to(_realpath(REPO_ROOT)).as_posix()}"],
        capture_output=True, text=True, check=False,
    )
    control_ok = (
        control.get("key.open", {}).get("value") == expected
        and control.get("key.exists", {}).get("value") is True
    )
    probes.append(
        {
            "id": "NEG-0",
            "kind": "positive_control",
            "result": "PASS" if control_ok else "FAIL",
            "git_show_rc": git_control.returncode,
        }
    )
    if not control_ok:
        raise IsolationError(
            "NEG-0 (positive control) FAILED: the unsandboxed probe could not read the "
            "answer key, so every NEG-1..NEG-4 denial below would be meaningless. The "
            "probe is pointed at the wrong path."
        )

    # -- NEG-1 -- exhaustive walk of review_root from inside it. This one holds whether or
    # not an enforcement backend exists, because it is a property of the TREE.
    key = _load_key_with_source(fixture)
    leak_hits = final_review_eval.scan_leak(key, [review_root])
    literals = [str(key_path), str(key_path.parent), str(fixture / "adjudications"),
                str(_realpath(REPO_ROOT)), str(key.get("fixture_id", ""))]
    literal_hits = []
    for path in review_root.rglob("*"):
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for literal in literals:
            if literal and literal in text:
                literal_hits.append({"path": str(path), "literal": literal})
    probes.append(
        {
            "id": "NEG-1",
            "kind": "review_root_walk",
            "result": "PASS" if not leak_hits and not literal_hits else "FAIL",
            "hits": len(leak_hits) + len(literal_hits),
        }
    )
    if leak_hits or literal_hits:
        raise IsolationError(
            "NEG-1 FAILED: review_root carries key material or a literal key path: "
            + json.dumps((leak_hits + literal_hits)[:5], ensure_ascii=False)
        )

    if enforcement != ENFORCEMENT_SEATBELT:
        # Recorded as what it is, not as a SKIP. A skip reads like an absence of
        # evidence; this reads like an absence of enforcement.
        for identifier, kind in (
            ("NEG-2", "sandboxed_open"), ("NEG-3", "sandboxed_discovery"),
            ("NEG-4", "sandboxed_git_and_archive"), ("NEG-5", "readable_set_rescan"),
            ("NEG-6", "profile_integrity"), ("NEG-7", "writable_descendant_plant"),
            ("NEG-8", "alias_battery"),
        ):
            probes.append({"id": identifier, "kind": kind,
                           "result": UNENFORCED_PROBE_RESULT})
        return probes

    # -- NEG-2 / NEG-3 -- content reads and DISCOVERY, from inside the sandbox.
    sandboxed = _run_probe(session, targets, sandboxed=True)
    opens = {k: v for k, v in sandboxed.items() if k.endswith(".open")}
    discovery = {k: v for k, v in sandboxed.items() if not k.endswith(".open")}
    probes.append({"id": "NEG-2", "kind": "sandboxed_open",
                   "result": "PASS" if _all_denied(opens) else "FAIL"})
    probes.append({"id": "NEG-3", "kind": "sandboxed_discovery",
                   "result": "PASS" if _all_denied(discovery) else "FAIL"})

    # -- NEG-4 -- git and the release archive, from inside the sandbox.
    relative = key_path.relative_to(_realpath(REPO_ROOT)).as_posix()
    commands = [
        f"git -C {shlex.quote(str(REPO_ROOT))} show HEAD:{shlex.quote(relative)}",
        f"git -C {shlex.quote(str(REPO_ROOT))} grep seeded_defects",
        f"tar -tzf {shlex.quote(str(REPO_ROOT))}/dist/orca-skills-*.tar.gz",
    ]
    results = [_command_probe(session, command) for command in commands]
    probes.append(
        {
            "id": "NEG-4",
            "kind": "sandboxed_git_and_archive",
            "result": "PASS"
            if all(not r["discovered"] and not r["leaked_key_content"] for r in results)
            else "FAIL",
            "commands": results,
        }
    )

    # -- NEG-5 -- the readable-set scan re-run IN PROCESS over EVERY admitted root, IMM
    # and USR alike. Iteration 1 re-scanned Class USR only, which is why it could not
    # have caught F-001.
    #
    # BOTH classes are content-scanned, and the only per-class difference is pass B's
    # vocabulary (DESIGN D-5.1). A Class USR root gets all five passes with
    # `key_leak_tokens()` and the count heuristics: it is small (a freshly created
    # session tree) and mutable, so its content is the only evidence there is. A Class
    # IMM root gets A/B/C/D with `key_material_tokens()` and no count heuristics --
    # pass B is mandatory, on by default, and there is no flag that turns it off.
    #
    # The recursive immutability proof does NOT stand in for that scan. It establishes
    # that nothing unprivileged can create or overwrite content in the subtree NOW; it
    # says nothing about content that was already there when the proof ran, because it
    # never opens a file. A pre-existing reformatted or partial rendering of the key is
    # an initial-state condition, not an act by a privileged writer, and pass B is the
    # only thing that finds it.
    rescan_hits = 0
    rescan_detail = []
    for entry in readable["entries"]:
        is_imm = entry["class"] == CLASS_IMM
        passes = SCAN_PASSES_IMM if is_imm else SCAN_PASSES_ALL
        vocabulary = "key_material" if is_imm else "key_leak"
        # The carve-outs are part of the readable set's DEFINITION, not an exclusion
        # bolted onto the scan: the profile denies them for content and metadata alike,
        # so a path beneath one is not something the Reviewer can read and scanning it
        # answers a question nobody asked. Leaving them in is not merely noisy -- it is
        # wrong in the loud direction: /System/Volumes/Data re-exposes the entire data
        # volume, so the rescan reports every answer-key copy anywhere on the machine as
        # a hit while the sandboxed process cannot reach a single one of them.
        root_carve_outs = [
            carve_out for carve_out in readable["carve_outs"]
            if _is_within(_realpath(carve_out), _realpath(entry["path"]))
        ]
        scan = scan_readable_set(
            key,
            Path(entry["path"]),
            passes=passes,
            carve_outs=root_carve_outs,
            vocabulary=vocabulary,
        )
        rescan_hits += len(scan["hits"])
        rescan_detail.append(
            {"path": entry["path"], "class": entry["class"], "passes": list(passes),
             "vocabulary": vocabulary, "content_scanned": scan["content_scanned"],
             "carve_outs": len(root_carve_outs), "hits": len(scan["hits"])}
        )
    probes.append({"id": "NEG-5", "kind": "readable_set_rescan",
                   "result": "PASS" if rescan_hits == 0 else "FAIL",
                   "hits": rescan_hits, "roots": rescan_detail})

    # -- NEG-6 -- profile integrity: the digest matches, and the profile parses.
    profile_path = session / "control" / PROFILE_FILENAME
    parsed = subprocess.run(
        [SANDBOX_EXEC, "-f", str(profile_path), "/usr/bin/true"],
        capture_output=True, text=True, check=False, timeout=60,
    )
    probes.append(
        {
            "id": "NEG-6",
            "kind": "profile_integrity",
            "result": "PASS" if parsed.returncode == 0 else "FAIL",
            "profile_digest": sha256_path(profile_path),
        }
    )

    # -- NEG-7 / NEG-8 --
    probes.append(_run_neg7(session, key_path, plant=plant))
    probes.append(_run_neg8(session, denied, readable["carve_outs"]))
    return probes


def _plant_sites() -> list[Path]:
    """RESOLVED, never assumed.

    The whole point of F-001 is that an ASSUMED path (`/tmp`, `$TMPDIR` as spelled in the
    environment) is not the path the allowlist actually admitted:
    `/private/var/folders/<hash>/T` is per-user and per-boot, and a test that hard-codes
    any part of it tests nothing.
    """
    sites = [Path(os.path.realpath(tempfile.gettempdir()))]
    caches = Path(os.path.realpath(os.path.expanduser("~/Library/Caches")))
    if caches.is_dir():
        sites.append(caches)
    return sites


def assert_no_stale_plants() -> None:
    """Leaving a real answer-key copy on disk is a worse outcome than the bug."""
    stale = [site / NEG7_PLANT_DIRNAME for site in _plant_sites()
             if (site / NEG7_PLANT_DIRNAME).exists()]
    if stale:
        raise IsolationError(
            "a previous NEG-7 plant directory still exists and must be removed by hand "
            f"before another session is built: {[str(entry) for entry in stale]}"
        )


def _run_neg7(session: Path, key_path: Path, *, plant: bool) -> dict:
    """The writable-descendant probe. This is the test F-001 would have failed.

    Plants a byte-identical copy of the answer key, plus a file carrying the fixture's
    real seeded-defect identity strings, in the run user's RESOLVED mutable roots -- then
    proves the sandboxed process can neither read, stat, list, `cat`, `ls`, `find` nor
    `grep` them, nor reach them through a symlink placed inside `review_root` or through
    the `/System/Volumes/Data` alias.

    Its positive control is NEG-0's, extended: the same battery runs UNSANDBOXED first
    and every read must succeed, with the plant's sha256 equal to the answer key's. A
    denial result is only meaningful paired with a demonstration that the identical
    operation succeeds without the sandbox.
    """
    if not plant:
        return {"id": "NEG-7", "kind": "writable_descendant_plant", "result": "SKIP",
                "why": "planting disabled by the caller"}
    assert_no_stale_plants()
    payload = key_path.read_bytes()
    expected = hashlib.sha256(payload).hexdigest()
    planted: list[Path] = []
    link = session / "review_root" / ".neg7_link"
    try:
        for site in _plant_sites():
            directory = site / NEG7_PLANT_DIRNAME
            directory.mkdir(parents=True)
            planted.append(directory)
            (directory / "answer_key.json").write_bytes(payload)
            (directory / "notes.txt").write_text(
                "defect identity: SD-1,SD-2,SD-3\n", encoding="utf-8"
            )
        first = planted[0] / "answer_key.json"
        # The plant, its directory, and the temp root itself are three separate targets,
        # because "cannot read the file" and "cannot list the directory it is in" are
        # three separate claims and the task asks for all of them.
        targets = {
            "plant": str(first),
            "plant_dir": str(first.parent),
            "temp_root": str(_plant_sites()[0]),
        }
        for index, directory in enumerate(planted[1:], start=1):
            targets[f"plant{index}"] = str(directory / "answer_key.json")
            targets[f"plant{index}_dir"] = str(directory)
        try:
            os.symlink(str(first), str(link))
            targets["symlink"] = str(link)
        except OSError:
            pass
        for alias in _alias_spellings(str(first), boundary_authorities()):
            targets["alias"] = alias
            break

        control = _run_probe(None, targets, sandboxed=False)
        control_ok = control.get("plant.open", {}).get("value") == expected
        if not control_ok:
            return {"id": "NEG-7", "kind": "writable_descendant_plant", "result": "FAIL",
                    "why": "the positive control could not read the plant"}
        sandboxed = _run_probe(session, targets, sandboxed=True)
        commands = [
            f"/bin/cat {shlex.quote(str(first))}",
            f"/bin/ls {shlex.quote(str(first.parent))}",
            f"/usr/bin/find {shlex.quote(str(first.parent))} -name answer_key.json",
            f"/usr/bin/grep -rl {shlex.quote(KEY_CONTENT_MARKER)} "
            f"{shlex.quote(str(first.parent))}",
        ]
        command_results = [_command_probe(session, command) for command in commands]
        denied = _all_denied(
            {
                label: result
                for label, result in sandboxed.items()
                if not _ancestor_metadata_exempt(label)
            }
        ) and all(
            not r["discovered"] and not r["leaked_key_content"] for r in command_results
        )
        return {
            "id": "NEG-7",
            "kind": "writable_descendant_plant",
            "result": "PASS" if denied else "FAIL",
            "plant_sites": len(planted),
            "positive_control": "PASS",
            "commands": command_results,
        }
    finally:
        for directory in planted:
            shutil.rmtree(directory, ignore_errors=True)
        link.unlink(missing_ok=True)
        remaining = [str(entry) for entry in planted if entry.exists()]
        if remaining:
            raise IsolationError(f"NEG-7 plants could not be removed: {remaining}")


def _run_neg8(session: Path, denied: Sequence[str], carve_outs: Sequence[str]) -> dict:
    """The alias battery: every denied root and every carve-out, through each spelling."""
    authorities = boundary_authorities()
    targets: dict[str, str] = {}
    for index, path in enumerate([*denied, *carve_outs]):
        for alias in _alias_spellings(path, authorities):
            targets[f"alias{index}"] = alias
    if not targets:
        return {"id": "NEG-8", "kind": "alias_battery", "result": "PASS", "aliases": 0}
    sandboxed = _run_probe(session, targets, sandboxed=True)
    return {
        "id": "NEG-8",
        "kind": "alias_battery",
        "result": "PASS" if _all_denied(sandboxed) else "FAIL",
        "aliases": len(targets),
    }


# ---- G.6 the attestation ---------------------------------------------------------------


def _path_field(value: str, root: Path) -> str:
    """Every path-bearing field goes through the existing P-PATH treatment.

    A session path is by construction a foreign absolute path and lands as the
    placeholder -- which is exactly why `profile_digest`, and not the profile text, is
    what the attestation records.
    """
    field = run_logging._relative_artifact_path(Path(value), root)
    run_logging.assert_retained_path_field(field)
    return field


def build_attestation(
    *,
    run_id: str,
    attempt: int,
    terminal: str,
    session: Path,
    enforcement: str,
    readable: dict,
    traversal: Sequence[str],
    writable: Sequence[str],
    denied: Sequence[str],
    profile_digest: str | None,
    probes: Sequence[dict],
    repo_root: Path = REPO_ROOT,
) -> dict:
    """The document, and it carries NO clock value.

    Like the metrics document, this file must be byte-reproducible from the same session,
    so `generated_at` lives in a `--provenance-out` sidecar. Same rule, same reason.

    `properties.S1/S2/S3` are three separate verdicts. There is deliberately no aggregate
    `isolated: true` field -- a single boolean invites reading a partial result as a whole
    one.
    """
    verdicts = {probe["id"]: probe["result"] for probe in probes}

    def passed(*identifiers: str) -> str:
        results = [verdicts.get(identifier) for identifier in identifiers]
        if all(result == "PASS" for result in results):
            return "PASS"
        if any(result == UNENFORCED_PROBE_RESULT for result in results):
            return "FAIL"
        return "FAIL"

    entries = []
    for entry in readable["entries"]:
        copied = dict(entry)
        copied["path"] = (
            entry["path"]
            if entry["class"] == CLASS_IMM
            else _path_field(entry["path"], repo_root)
        )
        if copied["class"] == CLASS_IMM and copied.get("scanned") is False:
            proof = copied.get("proof") or {}
            if proof.get("writable_dirs") or proof.get("writable_files"):
                raise IsolationError(
                    f"{entry['path']}: `scanned: false` is legal only for class "
                    f"{CLASS_IMM} with a complete proof whose writable_dirs and "
                    "writable_files are both 0"
                )
        elif copied.get("scanned") is False:
            raise IsolationError(
                f"{entry['path']}: `scanned: false` is not legal for class "
                f"{copied['class']}"
            )
        entries.append(copied)

    document = {
        "schema_version": ISOLATION_SCHEMA_VERSION,
        "document_kind": ISOLATION_DOCUMENT_KIND,
        "run_id": run_id,
        "final_review_attempt": attempt,
        "terminal": terminal,
        "scope_enforcement": (
            ENFORCEMENT_SEATBELT if enforcement == ENFORCEMENT_SEATBELT else "unenforced"
        ),
        "enforcement_backend_version": (
            f"sandbox-exec; {os.uname().sysname.lower()} {os.uname().release}"
            if enforcement == ENFORCEMENT_SEATBELT
            else "none"
        ),
        "session_root": _path_field(str(session), repo_root),
        "review_root": _path_field(str(session / "review_root"), repo_root),
        "profile_digest": profile_digest,
        "readable_set": entries,
        "carve_outs_denied": sorted(readable["carve_outs"]),
        "no_unscanned_descendant": "PASS",
        "traversal_set": sorted(traversal),
        "writable_set": [_path_field(entry, repo_root) for entry in writable],
        "denied_roots": [_path_field(entry, repo_root) for entry in denied],
        "key_bearing_roots_discovered": len(denied),
        "properties": {
            "S1": passed("NEG-1"),
            "S2": passed("NEG-2", "NEG-3", "NEG-4"),
            "S3": passed("NEG-5", "NEG-6", "NEG-7", "NEG-8"),
        },
        "probes": list(probes),
        "threat_model": (
            "unconstrained well-behaved reviewer agent; not a sandbox-escaping adversary"
        ),
        # G.6, verbatim, and nothing broader. This entry is about the immutability
        # proof's DURABILITY -- a privileged writer, outside the G.1 threat model. It
        # makes no content claim, because content is established independently at
        # NEG-5, whose per-root record carries `passes`, `vocabulary` and
        # `content_scanned`.
        "limitations": [
            "The recursive immutability proof is evaluated at session-build time "
            "against the run user's own privileges, so it does not bind a privileged "
            "(root) writer, who is outside the stated threat model"
        ],
    }
    assert_no_clock_value(document)
    return document


_CLOCK_KEYS = ("generated_at", "exported_at", "timestamp", "started_at", "ended_at")


def assert_no_clock_value(document: Any) -> None:
    """Byte-reproducible from the same session, with no excepted field."""
    if isinstance(document, dict):
        for key, value in document.items():
            if key in _CLOCK_KEYS:
                raise IsolationError(
                    f"the attestation must carry no clock value; found {key!r}. Clocks "
                    "go to the provenance sidecar."
                )
            assert_no_clock_value(value)
    elif isinstance(document, list):
        for item in document:
            assert_no_clock_value(item)


def write_attestation(session: Path, document: dict) -> Path:
    path = session / "control" / ISOLATION_FILENAME
    path.write_text(
        json.dumps(document, indent=2, sort_keys=False, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return path


# ---- G.8 repatriation --------------------------------------------------------------------


def repatriate(
    session: Path, run_id: str, *, attempt: int = 1, base: Path | None = None
) -> dict:
    """Move the report, the attestation and the subject tree out of the ephemeral session.

    The Reviewer writes `FINAL_REVIEW.md` INSIDE the session, because the repository is
    not writable to it. This is the one place a file crosses the session boundary, so it
    is the one place the "a retry never overwrites the predecessor's evidence" rule has to
    be applied at a new location.

    Why byte-for-byte re-scoreability survives (B5): the retained report is byte-identical
    to what the Reviewer wrote (step 3 below proves it); the subject tree is copied back
    to `artifacts/runs/<run>/final_review_workspace/` so `score --workspace` points at a
    live path rather than a deleted session; the tree is byte-identical to the
    materialized one, same MANIFEST.json and same fixture_digest; and `metrics.json` still
    contains no clock-derived value. The one thing that WOULD have broken B5 -- pointing
    `--workspace` at a deleted session path -- is closed by that copy.
    """
    session = Path(session)
    root = (Path(base) if base else Path.cwd()) / "artifacts" / "runs" / run_id
    root.mkdir(parents=True, exist_ok=True)
    suffix = "" if attempt == 1 else f"_iteration{attempt}"
    source = session / "review_root" / "artifacts" / "runs" / run_id / FINAL_REVIEW_REPORT_FILENAME
    if not source.is_file():
        raise IsolationContractError(
            f"the isolated Reviewer produced no report at {source}"
        )
    destination = root / f"FINAL_REVIEW{suffix}.md"
    before = sha256_path(source)
    if destination.exists() and sha256_path(destination) != before:
        raise IsolationContractError(
            f"{destination} already exists with different content; a retry never "
            "overwrites the predecessor's evidence"
        )
    shutil.copy2(str(source), str(destination), follow_symlinks=False)
    after = sha256_path(destination)
    if after != before:
        raise IsolationContractError(
            f"the repatriated report changed in transit: {before} -> {after}"
        )

    attestation_source = session / "control" / ISOLATION_FILENAME
    attestation_destination = root / f"FINAL_REVIEW_ISOLATION{suffix}.json"
    if attestation_source.is_file():
        shutil.copy2(str(attestation_source), str(attestation_destination),
                     follow_symlinks=False)

    workspace_source = session / "review_root" / "subject"
    workspace_destination = root / f"{REPATRIATED_WORKSPACE_DIRNAME}{suffix}"
    if workspace_source.is_dir() and not workspace_destination.exists():
        shutil.copytree(str(workspace_source), str(workspace_destination),
                        symlinks=False)
    return {
        "report": str(destination),
        "report_digest": after,
        "isolation": str(attestation_destination),
        "workspace": str(workspace_destination),
    }


# ---- the driver ---------------------------------------------------------------------------


def isolate(
    run_id: str,
    *,
    fixture: Path,
    session_base: Path | None = None,
    policy_files: Sequence[str] = DEFAULT_POLICY_FILES,
    allow_read: Sequence[str] = (),
    enforcement: str = ENFORCEMENT_SEATBELT,
    attempt: int = 1,
    terminal: str = "",
    plant: bool = True,
    repo_root: Path = REPO_ROOT,
) -> dict:
    """Build one isolation session, prove it, attest it. Fail-closed at every step.

    A half-built isolation session is worse than none, because its existence would be
    read as a guarantee. Every failure below removes the session before it raises.
    """
    if enforcement not in (ENFORCEMENT_SEATBELT, ENFORCEMENT_NONE):
        raise IsolationContractError(f"unknown enforcement backend {enforcement!r}")
    if enforcement == ENFORCEMENT_SEATBELT and not Path(SANDBOX_EXEC).exists():
        raise IsolationError(
            f"--enforcement seatbelt requires {SANDBOX_EXEC}, which this host does not "
            "have. The capture may still run with --enforcement none, but it FAILS B6 "
            "and may not be recorded as a section 7 baseline."
        )
    if enforcement == ENFORCEMENT_SEATBELT and plant:
        assert_no_stale_plants()

    session = build_session(
        run_id, fixture=fixture, session_base=session_base,
        policy_files=policy_files, repo_root=repo_root,
    )
    try:
        key = _load_key_with_source(fixture)
        readable = compute_readable_set(session, key, allow_read=allow_read)
        paths = [entry["path"] for entry in readable["entries"]]
        carve_outs = readable["carve_outs"]
        denied = discover_key_bearing_roots(fixture)
        traversal = compute_traversal_set(paths, carve_outs)
        writable = [str(session / "review_root"), str(session / "tmp"),
                    str(session / "home")]
        profile_digest = None
        if enforcement == ENFORCEMENT_SEATBELT:
            assert_carve_outs_denied(carve_outs, carve_outs)
            profile = render_seatbelt_profile(
                session=session,
                imm=[e["path"] for e in readable["entries"] if e["class"] == CLASS_IMM],
                usr=[e["path"] for e in readable["entries"] if e["class"] == CLASS_USR],
                carve_outs=carve_outs,
                traversal=traversal,
                writable=writable,
                denied=denied,
            )
            profile_path = session / "control" / PROFILE_FILENAME
            profile_path.write_text(profile, encoding="utf-8")
            profile_digest = sha256_path(profile_path)
            preflight = preflight_probe(session)
            (session / "control" / "probes" / "preflight.log").write_text(
                preflight["log"], encoding="utf-8"
            )
            if not preflight["ok"]:
                raise IsolationError(
                    "the pre-flight probe FAILED, so no Task is dispatched into this "
                    f"session: {preflight['log'][:600]}"
                )
        probes = run_probes(
            session, fixture=fixture, readable=readable, denied=denied,
            enforcement=enforcement, plant=plant,
        )
        failed = [p["id"] for p in probes if p["result"] not in
                  ("PASS", UNENFORCED_PROBE_RESULT, "SKIP")]
        if failed:
            raise IsolationError(f"negative probes FAILED: {failed}")
        document = build_attestation(
            run_id=run_id, attempt=attempt, terminal=terminal, session=session,
            enforcement=enforcement, readable=readable, traversal=traversal,
            writable=writable, denied=denied, profile_digest=profile_digest,
            probes=probes, repo_root=repo_root,
        )
        write_attestation(session, document)
        return {
            "session": str(session),
            "review_root": str(session / "review_root"),
            "attestation": str(session / "control" / ISOLATION_FILENAME),
            "launch_command": wrap_command(session, "<resolved agent command>")
            if enforcement == ENFORCEMENT_SEATBELT
            else "<resolved agent command>",
            "scope_enforcement": document["scope_enforcement"],
            "properties": document["properties"],
        }
    except BaseException:
        shutil.rmtree(session, ignore_errors=True)
        raise


def preflight_probe(session: Path, agent_command: str | None = None) -> dict:
    """Mandatory, before the real dispatch. Runs the launch line for real.

    Purpose: discover the Class USR roots the agent genuinely needs (an `Abort trap: 6`
    or a startup error means the readable set is too small) and prove the launch line
    works before a Task is dispatched into it. A failing pre-flight is a hard failure --
    NEVER a silently widened profile. Each root added in response is added by an explicit
    `--allow-read` on the next invocation, so every widening is a recorded operator
    decision and is then subject to the G.3 scan.

    The `xcrun` shim's `couldn't create cache file` line on stderr is classified BENIGN
    here and must not be "fixed" by granting write access to the host per-user temp
    directory: that was tried, it does not even help -- the shim resolves its cache
    directory through `confstr(_CS_DARWIN_USER_TEMP_DIR)`, which ignores TMPDIR -- and it
    would re-admit a host-wide mutable path.
    """
    checks = [
        f"{shlex.quote(_probe_python())} -c {shlex.quote('print(1)')}",
        "/bin/echo preflight",
        "git --version",
        "/bin/ls .",
    ]
    if agent_command:
        checks.append(agent_command)
    log_lines = []
    ok = True
    for command in checks:
        completed = subprocess.run(
            ["/bin/sh", "-c", wrap_command(session, command)],
            capture_output=True, text=True, check=False, timeout=120,
        )
        benign = _benign_stderr(completed.stderr)
        log_lines.append(
            f"$ {command}\nrc={completed.returncode}\n{completed.stdout}"
            f"{completed.stderr}\n"
        )
        if completed.returncode != 0 and not benign:
            ok = False
    return {"ok": ok, "log": "\n".join(log_lines)}


def _benign_stderr(text: str) -> bool:
    return bool(text) and "couldn't create cache file" in text


def orca_check_probe(session: Path, terminal: str, orca: str = "orca") -> dict:
    """O-1, asserted rather than assumed.

    `orca orchestration send/check/ask` must keep working from inside the sandbox: the
    executable lives outside the repository, `(allow default)` leaves network and process
    rights untouched, and the dispatch capability arrives in the preamble rather than
    being read from the repo. There is no known blocker -- but the CLI may resolve a
    worktree from cwd for some subcommands, so this probes it concretely. A failure is a
    BLOCKING finding for IMPLEMENTATION, not something to work around silently.
    """
    command = f"{shlex.quote(orca)} orchestration check --terminal {shlex.quote(terminal)}"
    completed = subprocess.run(
        ["/bin/sh", "-c", wrap_command(session, command)],
        capture_output=True, text=True, check=False, timeout=120,
    )
    return {"rc": completed.returncode, "stderr": completed.stderr[:400]}
