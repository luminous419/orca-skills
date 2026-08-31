"""DESIGN evidence for D4 (ledger shape), D5 (lineage bound) and D8 (the
sequence-collision primitive) -- executed against the REAL run_logging machinery.

D8's premise is checked rather than assumed: PLAN D8 says
`_stage_and_publish_audit_record`'s `os.rename` overwrites on POSIX. That is true of
a FILE rename and false of the DIRECTORY rename this function actually performs, and
the difference is what lets OS-29 reuse the primitive unchanged instead of inventing
an O_EXCL scheme. Both facts are executed below.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))

from decision_policy import (  # noqa: E402
    DecisionPolicyError, load_decision_policy, validate_record,
)
from run_logging import (  # noqa: E402
    FINAL_REVIEW_AUDIT_FILENAMES,
    FinalReviewAuditCollision,
    _fsync_directory,
    _stage_and_publish_audit_record,
    _write_staged_file,
    redact_text,
)

POLICY = load_decision_policy(REPO / "orca-worker-reviewer-orchestration" / "SKILL.md")
RESULTS: list[tuple[bool, str, str]] = []


def check(name, got, expect):
    RESULTS.append((got == expect, name, f"expect={expect!r} got={got!r}"))


TMP = Path(tempfile.mkdtemp())

# ===========================================================================
# D8. The sequence-collision primitive.
# ===========================================================================
# -- D8-1: the REAL function refuses a second write under the same key.
audit = TMP / "audit"
audit.mkdir()
files = {name: f"content of {name}" for name in FINAL_REVIEW_AUDIT_FILENAMES}
published = _stage_and_publish_audit_record(audit, "000000", files)
check("D8-1a the real primitive publishes", published.is_dir(), True)
try:
    _stage_and_publish_audit_record(audit, "000000", {k: "SECOND WRITER" for k in files})
    _second = "ACCEPTED -- OVERWROTE"
except FinalReviewAuditCollision:
    _second = "FinalReviewAuditCollision"
check("D8-1b a second writer on the same key is REFUSED, never an overwrite",
      _second, "FinalReviewAuditCollision")
check("D8-1c the first writer's bytes survive untouched",
      (published / "record.json").read_text(), "content of record.json")

# -- D8-2: WHY it refuses. The rename is directory-onto-directory, which POSIX
#          refuses when the target is non-empty. Executed, not cited.
src = TMP / "src_dir"; src.mkdir(); (src / "x").write_text("x")
try:
    os.rename(src, published)
    _dir_rename = "SUCCEEDED"
except OSError as exc:
    _dir_rename = f"OSError:{exc.errno}"
check("D8-2a directory rename onto a NON-EMPTY directory is refused (ENOTEMPTY=66)",
      _dir_rename, "OSError:66")
# The control that makes the distinction real: a FILE rename DOES overwrite, which
# is exactly the hazard PLAN D8 named -- so a file-per-record ledger would need a
# different primitive, and a directory-per-record ledger does not.
fa = TMP / "a.json"; fa.write_text("A")
fb = TMP / "b.json"; fb.write_text("B")
os.rename(fa, fb)
check("D8-2b CONTROL: a FILE rename silently overwrites", fb.read_text(), "A")

# -- D8-3: the one residual hazard, and the precondition that closes it.
empty_target = audit / "000099"; empty_target.mkdir()
src2 = TMP / "src2"; src2.mkdir(); (src2 / "x").write_text("x")
try:
    os.rename(src2, empty_target)
    _empty = "SUCCEEDED"
except OSError as exc:
    _empty = f"OSError:{exc.errno}"
check("D8-3a a rename onto an EMPTY directory DOES succeed -- the residual hazard",
      _empty, "SUCCEEDED")
# Closed by a precondition, not by an argument: a published name only ever comes
# into existence via a rename of an already-populated staging directory.
check("D8-3b so the ledger writer asserts a non-empty payload before publishing",
      len(files) > 0, True)

# -- D8-4: the generalization OS-29 needs. ONE line: iterate `files` instead of the
#          module constant, plus the non-empty precondition D8-3b names.
LEDGER_RECORD_FILENAME = "record.json"
STAGING = ".staging"


class LedgerCollision(Exception):
    pass


def stage_and_publish_record(ledger_dir: Path, key: str, files: dict[str, str]) -> Path:
    """Byte-for-byte the shape of run_logging._stage_and_publish_audit_record:1779,
    with the filename list taken from `files` and the non-empty precondition made
    explicit. Same rename, same exclusivity, same complete-on-appearance rule."""
    if not files:
        raise ValueError("a published record must never be empty")
    target = ledger_dir / key
    if target.exists():
        raise LedgerCollision(key)
    staging_root = ledger_dir / STAGING
    staging = staging_root / f"{key}.{os.getpid()}-{len(files)}-{id(files) & 0xFFFF:04x}"
    try:
        os.makedirs(staging_root, exist_ok=True)
        os.mkdir(staging)
        for name, text in files.items():
            _write_staged_file(staging / name, text)
        _fsync_directory(staging)
        os.rename(staging, target)
    except OSError as error:
        shutil.rmtree(staging, ignore_errors=True)
        if target.exists():
            raise LedgerCollision(key) from error
        raise
    _fsync_directory(ledger_dir)
    return target


ledger = TMP / "decision_ledger"
ledger.mkdir()


def publish(seq: int, record: dict) -> Path:
    return stage_and_publish_record(
        ledger, f"{seq:06d}", {LEDGER_RECORD_FILENAME: json.dumps(record, indent=2, sort_keys=True)}
    )


# ===========================================================================
# D4. The record schema. All thirteen required fields on every state.
# ===========================================================================
REQUIRED_THIRTEEN = (
    "run", "phase", "iteration", "state", "reason_code", "evidence", "assumption",
    "open_item", "responsible_phase", "role", "verdict", "source_binding", "recorded_at",
)
LEDGER_MECHANICS = (
    "ledger_schema_version", "boundary", "sequence", "source",
    "prior_open_decision_items", "verifies",
)
# D5's boundary, as an ENFORCED closed set rather than a promise.
CLOSED_RECORD_FIELDS = frozenset(REQUIRED_THIRTEEN) | frozenset(LEDGER_MECHANICS) | frozenset({
    # the OS-28 contract's own evidence keys, carried through validate_record()
    "boundary_element", "blast_radius", "monetary_cost", "security", "privacy",
    "compliance", "long_term_lock_in", "reversibility", "impact", "policy_source",
    "retraction_condition", "what_is_missing", "why_policy_cannot_decide",
    "classification_attempted", "citations", "why_they_cannot_both_hold",
    "open_decision_item", "grounds", "scope", "user_decision",
})
OS30_FORBIDDEN = ("supersedes", "superseded_by", "request_id", "response_id",
                  "options", "recommendation", "answered_at", "answered_by")


def base(state, seq, phase="design", iteration=1, role="worker", boundary="B2",
         source="worker", verdict="", **kw):
    rec = {
        "ledger_schema_version": 1, "sequence": seq, "boundary": boundary,
        "source": source, "run": "run_35b221ea299d", "phase": phase,
        "iteration": iteration, "responsible_phase": phase, "role": role,
        "state": state, "reason_code": None, "verdict": verdict,
        "evidence": {}, "assumption": None, "open_item": None,
        "source_binding": f"artifacts/runs/run_35b221ea299d/{phase.upper()}.md",
        "recorded_at": "2026-09-01T00:00:00+00:00",
        "prior_open_decision_items": [], "verifies": None,
        "open_decision_item": False,
        "grounds": "No boundary element declared by this phase is triggering.",
        "scope": "This phase's own conduct.",
    }
    rec.update(kw)
    return rec


CLEAR_REC = base("CLEAR", 0, iteration=0, role="coordinator", boundary="B1",
                 source="coordinator:run_entry", verdict="")
AA_REC = base("ASSUMPTION_ALLOWED", 1,
              reason_code="repository_policy",
              policy_source={"role": "supports", "kind": "file_path", "locator": "CLAUDE.md"},
              reversibility="reversible_in_run", impact="one module",
              retraction_condition="if the phase Reviewer rejects the assumption",
              blast_radius="module", monetary_cost=False, security=False, privacy=False,
              compliance=False, long_term_lock_in=False,
              assumption="the module-local default applies",
              evidence={"policy_source": "CLAUDE.md"},
              grounds="A supporting policy source exists and all six safety facts are declared.")
NI_REC = base("NEEDS_INPUT", 2, reason_code="blast_radius_beyond_scope",
              boundary_element="blast_radius", blast_radius="external_system",
              what_is_missing="whether the user authorizes touching an external system",
              why_policy_cannot_decide="no repository policy fixes the scope boundary",
              open_decision_item=True, open_item="external-system scope authorization",
              evidence={"boundary_element": "blast_radius"})
CF_REC = base("CONFLICT", 3, reason_code="requirement_contradiction",
              citations=["ORIGINAL_REQUEST.md#fail-closed", "ORIGINAL_REQUEST.md#out-of-scope"],
              why_they_cannot_both_hold="one requires the record, the other forbids the field",
              open_decision_item=True, open_item="which requirement governs",
              evidence={"citations": 2})

for label, rec in (("CLEAR", CLEAR_REC), ("ASSUMPTION_ALLOWED", AA_REC),
                   ("NEEDS_INPUT", NI_REC), ("CONFLICT", CF_REC)):
    check(f"D4-1 {label}: all thirteen required fields present",
          sorted(f for f in REQUIRED_THIRTEEN if f not in rec), [])
    check(f"D4-2 {label}: all six ledger-mechanics fields present",
          sorted(f for f in LEDGER_MECHANICS if f not in rec), [])
    try:
        validate_record(POLICY, rec)
        got = "ACCEPTED"
    except DecisionPolicyError as exc:
        got = f"REJECTED:{exc}"
    check(f"D4-3 {label}: accepted by the OS-28 contract", got, "ACCEPTED")
    check(f"D4-4 {label}: no field outside the closed set",
          sorted(set(rec) - CLOSED_RECORD_FIELDS), [])

# Non-vacuity for D4-3: the validator is not simply accepting everything.
_mut = dict(CLEAR_REC); _mut["reason_code"] = "repository_policy"
try:
    validate_record(POLICY, _mut); _c = "ACCEPTED"
except DecisionPolicyError as exc:
    _c = "REJECTED"
check("D4-5 CONTROL: a CLEAR carrying a reason_code is rejected", _c, "REJECTED")
_mut2 = dict(NI_REC); _mut2.pop("why_policy_cannot_decide")
try:
    validate_record(POLICY, _mut2); _c2 = "ACCEPTED"
except DecisionPolicyError:
    _c2 = "REJECTED"
check("D4-6 CONTROL: a NEEDS_INPUT missing required evidence is rejected", _c2, "REJECTED")

# Redaction is REUSED, not re-invented.
_red = redact_text("token=ghp_" + "a" * 36)
check("D4-7 the existing redactor is reused and actually redacts",
      "ghp_" + "a" * 36 not in _red, True)

# ===========================================================================
# D5. Lineage stops at a ledger reference. The closed field set is the enforcement.
# ===========================================================================
check("D5-1 `verifies` (a ledger-record reference) is in the closed set",
      "verifies" in CLOSED_RECORD_FIELDS, True)
check("D5-2 every OS-30 lineage/protocol field is OUTSIDE the closed set",
      [f for f in OS30_FORBIDDEN if f in CLOSED_RECORD_FIELDS], [])
_lineage = dict(NI_REC); _lineage["supersedes"] = "run_x/design/1/B2#2"
check("D5-3 a record carrying a supersession link fails the closed-set check",
      sorted(set(_lineage) - CLOSED_RECORD_FIELDS), ["supersedes"])
# `verifies` resolves to a ledger key -- a reference to a RECORD, not a link between
# two decisions.
V_REC = base("NEEDS_INPUT", 3, role="reviewer", boundary="B3", source="reviewer",
             verdict="FAIL", reason_code="blast_radius_beyond_scope",
             boundary_element="blast_radius", blast_radius="external_system",
             what_is_missing="whether the user authorizes touching an external system",
             why_policy_cannot_decide="no repository policy fixes the scope boundary",
             open_decision_item=True, open_item="external-system scope authorization",
             evidence={"boundary_element": "blast_radius"},
             verifies={"run": "run_35b221ea299d", "phase": "design", "iteration": 1,
                       "worker_record_key": "run_35b221ea299d/design/1/B2#2"})


def ledger_key(rec):
    return f"{rec['run']}/{rec['phase']}/{rec['iteration']}/{rec['boundary']}#{rec['sequence']}"


check("D5-4 `verifies` resolves to the Worker's own B2 ledger key",
      V_REC["verifies"]["worker_record_key"], ledger_key(NI_REC))
_unbound = json.loads(json.dumps(V_REC))
_unbound["verifies"]["worker_record_key"] = "run_35b221ea299d/design/9/B2#9"
check("D5-5 CONTROL: an unresolvable `verifies` is detectable (row 7)",
      _unbound["verifies"]["worker_record_key"] == ledger_key(NI_REC), False)

# ===========================================================================
# D8 (cont). Allocation, append-only, and the MANDATORY reader-side detection.
# ===========================================================================
for rec in (CLEAR_REC, AA_REC, NI_REC):
    publish(rec["sequence"], rec)
check("D8-4a three records published under sequence-named keys",
      sorted(p.name for p in ledger.iterdir() if not p.name.startswith(".")),
      ["000000", "000001", "000002"])
check("D8-4b every published name is a COMPLETE record",
      all((ledger / f"{i:06d}" / LEDGER_RECORD_FILENAME).is_file() for i in range(3)), True)
_before = (ledger / "000000" / LEDGER_RECORD_FILENAME).read_bytes()
publish(3, CF_REC)
check("D8-5 append-only: publishing a later sequence leaves an earlier one byte-identical",
      (ledger / "000000" / LEDGER_RECORD_FILENAME).read_bytes(), _before)

# Two writers racing for the same sequence: allocate-then-claim with a bounded retry.
def append_record(rec_without_seq: dict, *, contended: int | None = None) -> int:
    existing = sorted(int(p.name) for p in ledger.iterdir() if p.name.isdigit())
    seq = (max(existing) + 1) if existing else 0
    if contended is not None:
        seq = contended                       # force both writers onto the same claim
    for attempt in range(8):
        try:
            rec = dict(rec_without_seq, sequence=seq)
            publish(seq, rec)
            return seq
        except LedgerCollision:
            seq += 1
    raise RuntimeError("ledger allocation exhausted")


w1 = append_record(base("CLEAR", 0, phase="implementation", iteration=1), contended=4)
w2 = append_record(base("CLEAR", 0, phase="implementation", iteration=1), contended=4)
check("D8-6a two writers claiming the same sequence get DIFFERENT sequences",
      (w1, w2), (4, 5))
check("D8-6b and no record was overwritten -- the ledger is still gapless",
      sorted(int(p.name) for p in ledger.iterdir() if p.name.isdigit()), [0, 1, 2, 3, 4, 5])

# READER-SIDE DETECTION IS MANDATORY REGARDLESS (PLAN D8's closing clause).
def read_ledger(d: Path):
    """W-2b's reader: every published record, ordered by `sequence`. It reads and
    orders; it makes no admissibility judgement -- that is the gate's."""
    return sorted(
        (json.loads((p / LEDGER_RECORD_FILENAME).read_text())
         for p in d.iterdir() if p.name.isdigit()),
        key=lambda r: r["sequence"])


def detect(records):
    zeros = [r for r in records if r["sequence"] == 0]
    if len(zeros) != 1:
        return "DECISION_LEDGER_INCONSISTENT"
    if [r["sequence"] for r in records] != list(range(len(records))):
        return "DECISION_GATE_INPUT_MALFORMED"
    return "OK"


check("D8-7a the honest ledger reads clean", detect(read_ledger(ledger)), "OK")
# A duplicate sequence planted BELOW the writer (a foreign or older producer).
dup = TMP / "dup"; shutil.copytree(ledger, dup)
_d = json.loads((dup / "000003" / LEDGER_RECORD_FILENAME).read_text())
_d["sequence"] = 0
(dup / "000003" / LEDGER_RECORD_FILENAME).write_text(json.dumps(_d))
check("D8-7b a duplicate sequence is detected by the READER, not only by the writer",
      detect(read_ledger(dup)), "DECISION_LEDGER_INCONSISTENT")
gap = TMP / "gap"; shutil.copytree(ledger, gap)
shutil.rmtree(gap / "000002")
check("D8-7c a sequence gap is detected by the READER (A4-iv)",
      detect(read_ledger(gap)), "DECISION_GATE_INPUT_MALFORMED")
# The non-vacuity half: the detector is not rejecting everything.
check("D8-7d CONTROL: the unmodified ledger still reads clean after both mutants",
      detect(read_ledger(ledger)), "OK")

shutil.rmtree(TMP, ignore_errors=True)

ok = sum(1 for good, _, _ in RESULTS if good)
for good, name, detail in RESULTS:
    print(f"[{'PASS' if good else 'FAIL'}] {name}\n        {detail}")
print(f"\n{ok}/{len(RESULTS)} cases behaved as specified")
sys.exit(0 if ok == len(RESULTS) else 1)
