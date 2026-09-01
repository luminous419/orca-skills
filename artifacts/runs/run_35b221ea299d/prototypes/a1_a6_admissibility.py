"""Executable prototype of PLAN P6a's COMPLETE A1-A6 reader/admissibility path.

Purpose: prove by execution (not assertion) that the exact run-entry declaration
(RED) specified in PLAN.md P6a is admissible under ALL SIX rules -- including A4's
ledger-RECORD schema-version clause -- and that the negative controls are refused.

Import direction mirrors C1/W-1: this module imports `decision_policy` only.
"""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

sys.path.insert(0, "scripts")
from decision_policy import (  # noqa: E402
    DecisionPolicyError,
    load_decision_policy,
    validate_record,
    validate_transition,
)

# ---------------------------------------------------------------------------
# The OS-29 ledger-RECORD schema version. DELIBERATELY NOT
# decision_policy.SUPPORTED_SCHEMA_VERSIONS, which versions the SKILL policy
# CONTRACT BLOCK parsed by parse_decision_policy() (decision_policy.py:42, :200-214).
# Two different objects, two different constants, two independent evolutions.
LEDGER_RECORD_SCHEMA_VERSION: int = 1
SUPPORTED_LEDGER_RECORD_SCHEMA_VERSIONS: tuple[int, ...] = (1,)

OPEN_STATES = ("NEEDS_INPUT", "CONFLICT")
RED_SOURCE = "coordinator:run_entry"
AGENT_SOURCES = ("worker", "reviewer")


class GateRefusal(Exception):
    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(f"{reason}{(' -- ' + detail) if detail else ''}")
        self.reason = reason
        self.detail = detail


def ledger_key(rec) -> str:
    return f"{rec.get('run')}/{rec.get('phase')}/{rec.get('iteration')}/{rec.get('boundary')}#{rec.get('sequence')}"


def _open_items(policy, records) -> set[str]:
    """A5's recomputation: open blocking items not resolved by a LATER record."""
    open_keys: dict[str, dict] = {}
    for rec in records:
        if rec.get("state") in OPEN_STATES and rec.get("open_decision_item") is True:
            open_keys[ledger_key(rec)] = rec
    resolved: set[str] = set()
    for key, opened in open_keys.items():
        for later in records:
            if later.get("sequence", -1) <= opened.get("sequence", -1):
                continue
            if (later.get("run"), later.get("phase")) != (opened.get("run"), opened.get("phase")):
                continue
            try:
                validate_transition(policy, str(opened["state"]), str(later["state"]), later)
            except DecisionPolicyError:
                continue
            resolved.add(key)
            break
    return set(open_keys) - resolved


def read_decision_ledger(records):
    """W-2b's reader: records ordered by `sequence`. No admissibility judgement here."""
    return sorted(records, key=lambda r: r.get("sequence", 0))


def admit_head(policy, raw_records, *, run_id, expected_settled_round):
    """A1-A6. Returns the admitted head record, or raises GateRefusal.

    `expected_settled_round` is None at the run's FIRST B1 and otherwise the
    (run, phase, iteration) of the round that just settled. It is supplied by the
    B1 caller from the harness's own round state; it is never read off the ledger,
    which is what makes A3 a binding check rather than a restatement.
    """
    L = read_decision_ledger(raw_records)

    # ---- A1: non-empty -----------------------------------------------------
    if not L:
        raise GateRefusal("DECISION_GATE_INPUT_MISSING", "the producer did not run")

    # ---- A2: exactly one sequence-0 record, and it IS the RED --------------
    zeros = [r for r in L if r.get("sequence") == 0]
    if len(zeros) != 1:
        raise GateRefusal("DECISION_LEDGER_INCONSISTENT", f"{len(zeros)} records carry sequence 0")
    red = zeros[0]
    if not (
        red.get("source") == RED_SOURCE
        and red.get("boundary") == "B1"
        and red.get("run") == run_id
    ):
        raise GateRefusal("DECISION_LEDGER_INCONSISTENT", "sequence 0 is not this run's RED")

    # ---- A4: schema version, contract validity, gapless sequence ----------
    # (evaluated before A3's head selection: an unreadable ledger cannot be
    #  head-selected from. Ordering does not change any outcome below.)
    for rec in L:
        key = ledger_key(rec)
        if "ledger_schema_version" not in rec:
            raise GateRefusal(
                "DECISION_GATE_INPUT_MALFORMED", f"{key} has no ledger_schema_version (A4-i)"
            )
        version = rec["ledger_schema_version"]
        if isinstance(version, bool) or not isinstance(version, int):
            raise GateRefusal(
                "DECISION_GATE_INPUT_MALFORMED",
                f"{key} ledger_schema_version must be an integer, got {version!r} (A4-i)",
            )
        if version not in SUPPORTED_LEDGER_RECORD_SCHEMA_VERSIONS:
            raise GateRefusal(
                "DECISION_LEDGER_SCHEMA_UNSUPPORTED",
                f"{key} declares ledger_schema_version {version}; this build supports "
                f"{list(SUPPORTED_LEDGER_RECORD_SCHEMA_VERSIONS)} (A4-ii)",
            )
        try:
            validate_record(policy, rec)
        except DecisionPolicyError as exc:
            raise GateRefusal("DECISION_GATE_INPUT_MALFORMED", f"{key}: {exc} (A4-iii)") from exc
    if [r.get("sequence") for r in L] != list(range(len(L))):
        raise GateRefusal(
            "DECISION_GATE_INPUT_MALFORMED",
            f"sequences {[r.get('sequence') for r in L]} are not a gapless 0..n-1 (A4-iv)",
        )

    # ---- A3: head selection ------------------------------------------------
    head = L[0] if len(L) == 1 else L[-1]
    if len(L) == 1:
        if expected_settled_round is not None:
            raise GateRefusal(
                "DECISION_GATE_INPUT_UNBOUND",
                "the RED is the head but this is not the run's first B1; the settled "
                f"record for {expected_settled_round} is absent",
            )
    else:
        if head.get("source") not in AGENT_SOURCES or head.get("boundary") not in ("B2", "B3"):
            raise GateRefusal("DECISION_GATE_INPUT_UNBOUND", "head is not a settled agent record")
        if expected_settled_round is None:
            raise GateRefusal(
                "DECISION_GATE_INPUT_UNBOUND", "first B1 but the head is an agent record"
            )
        if (head.get("run"), head.get("phase"), head.get("iteration")) != expected_settled_round:
            raise GateRefusal(
                "DECISION_GATE_INPUT_UNBOUND",
                f"head binds {(head.get('run'), head.get('phase'), head.get('iteration'))}, "
                f"expected {expected_settled_round}",
            )

    # ---- A6: the declaration is RECOMPUTED, never trusted ------------------
    declared = set(red.get("prior_open_decision_items") or ())
    recomputed = _open_items(policy, [r for r in L if r is not red])
    if declared != recomputed:
        raise GateRefusal(
            "DECISION_GATE_DECLARATION_DISAGREES_WITH_LEDGER",
            f"declared={sorted(declared)} recomputed={sorted(recomputed)}",
        )

    # ---- A5: no unresolved open blocking item -----------------------------
    open_now = _open_items(policy, L)
    if open_now:
        blocker = next(r for r in L if ledger_key(r) in open_now)
        raise GateRefusal(
            f"DECISION_BLOCKED:{blocker.get('state')}:{blocker.get('reason_code')}",
            f"open at {ledger_key(blocker)}",
        )

    return head


# ---------------------------------------------------------------------------
# THE EXACT RED FROM PLAN.md P6a (with the A4 field this iteration adds)
RUN_ID = "run_35b221ea299d"
RED = {
    "ledger_schema_version": 1,
    "state": "CLEAR",
    "reason_code": None,
    "open_decision_item": False,
    "run": RUN_ID,
    "phase": "plan",
    "iteration": 0,
    "responsible_phase": "plan",
    "role": "coordinator",
    "boundary": "B1",
    "sequence": 0,
    "source": "coordinator:run_entry",
    "prior_open_decision_items": [],
    "grounds": (
        "This run's append-only decision ledger was empty when the run root was provisioned, "
        "so no decision item is open at run entry. This declares the ledger's state; it declares "
        "nothing about any phase's judgement, which is produced at B2/B3 and is fail-closed there."
    ),
    "scope": "Run entry only. It authorizes the first phase-entry transition and nothing after it.",
}

# The B2 worker ledger record shape (C10/R-P1), carrying the SAME version field.
B2_CLEAR = {
    "ledger_schema_version": 1,
    "state": "CLEAR",
    "reason_code": None,
    "open_decision_item": False,
    "run": RUN_ID,
    "phase": "plan",
    "iteration": 1,
    "responsible_phase": "plan",
    "role": "worker",
    "boundary": "B2",
    "sequence": 1,
    "source": "worker",
    "grounds": (
        "No boundary element declared by this phase is triggering and no contradiction between "
        "two explicit requirements was found, so no decision item is open at this boundary."
    ),
    "scope": "The PLAN phase's own conduct at iteration 1.",
}

B2_NEEDS_INPUT = {
    "ledger_schema_version": 1,
    "state": "NEEDS_INPUT",
    "reason_code": "blast_radius_beyond_scope",
    "boundary_element": "blast_radius",
    "blast_radius": "external_system",
    "what_is_missing": "whether the user authorizes touching files outside the declared scope",
    "why_policy_cannot_decide": "no repository policy or phase contract fixes the scope boundary",
    "open_decision_item": True,
    "run": RUN_ID,
    "phase": "plan",
    "iteration": 1,
    "responsible_phase": "plan",
    "role": "worker",
    "boundary": "B2",
    "sequence": 1,
    "source": "worker",
}


def mutate(base, **kw):
    out = copy.deepcopy(base)
    for k, v in kw.items():
        if v is _DELETE:
            out.pop(k, None)
        else:
            out[k] = v
    return out


class _DELETE:
    pass


def run_case(name, policy, records, *, run_id=RUN_ID, expected_settled_round=None, expect):
    try:
        head = admit_head(policy, records, run_id=run_id, expected_settled_round=expected_settled_round)
        got = f"ADMITTED head={ledger_key(head)} state={head.get('state')}"
        reason = "ADMITTED"
    except GateRefusal as exc:
        got = f"REFUSED {exc}"
        reason = exc.reason
    ok = (reason == expect) if expect != "ADMITTED" else (reason == "ADMITTED")
    print(f"[{'PASS' if ok else 'FAIL'}] {name}\n        expect={expect}\n        got   ={got}")
    return ok


def main() -> int:
    policy = load_decision_policy(Path("orca-worker-reviewer-orchestration/SKILL.md"))
    print(f"policy block schema_version = {policy.schema_version}  "
          f"(decision_policy.SUPPORTED_SCHEMA_VERSIONS -- the POLICY BLOCK)")
    print(f"ledger record schema version = {LEDGER_RECORD_SCHEMA_VERSION}  "
          f"(SUPPORTED_LEDGER_RECORD_SCHEMA_VERSIONS -- the RECORD)\n")

    results = []
    # ---- POSITIVE CONTROLS -------------------------------------------------
    results.append(run_case(
        "P1  exact RED, first B1, complete A1-A6", policy, [RED], expect="ADMITTED"))
    results.append(run_case(
        "P2  settled B2 head at a later boundary (A3 second branch)", policy,
        [RED, B2_CLEAR], expected_settled_round=(RUN_ID, "plan", 1), expect="ADMITTED"))

    # ---- A4 NEGATIVE CONTROLS (the finding) --------------------------------
    results.append(run_case(
        "N1  RED with ledger_schema_version MISSING (A4-i)", policy,
        [mutate(RED, ledger_schema_version=_DELETE)], expect="DECISION_GATE_INPUT_MALFORMED"))
    results.append(run_case(
        "N2  RED with UNSUPPORTED ledger_schema_version 2 (A4-ii)", policy,
        [mutate(RED, ledger_schema_version=2)], expect="DECISION_LEDGER_SCHEMA_UNSUPPORTED"))
    results.append(run_case(
        "N3  RED with ledger_schema_version '1' as text (A4-i)", policy,
        [mutate(RED, ledger_schema_version="1")], expect="DECISION_GATE_INPUT_MALFORMED"))
    results.append(run_case(
        "N4  RED with ledger_schema_version True (bool is not int) (A4-i)", policy,
        [mutate(RED, ledger_schema_version=True)], expect="DECISION_GATE_INPUT_MALFORMED"))
    results.append(run_case(
        "N5  AGENT record with unsupported version, RED fine (A4-ii, every record)", policy,
        [RED, mutate(B2_CLEAR, ledger_schema_version=99)],
        expected_settled_round=(RUN_ID, "plan", 1), expect="DECISION_LEDGER_SCHEMA_UNSUPPORTED"))
    results.append(run_case(
        "N6  policy-block version smuggled in as `schema_version` only (A4-i)", policy,
        [mutate(RED, ledger_schema_version=_DELETE, schema_version=1)],
        expect="DECISION_GATE_INPUT_MALFORMED"))

    # ---- THE OTHER FIVE RULES ---------------------------------------------
    results.append(run_case("N7  empty ledger (A1)", policy, [], expect="DECISION_GATE_INPUT_MISSING"))
    results.append(run_case(
        "N8  two sequence-0 records (A2)", policy,
        [RED, mutate(RED, source="coordinator:run_entry")], expect="DECISION_LEDGER_INCONSISTENT"))
    results.append(run_case(
        "N9  RED offered at a LATER boundary -- F11 hole proof (A3)", policy,
        [RED], expected_settled_round=(RUN_ID, "plan", 1), expect="DECISION_GATE_INPUT_UNBOUND"))
    results.append(run_case(
        "N10 RED carrying a reason_code -- the iteration-1 defect (A4-iii)", policy,
        [mutate(RED, reason_code="(none - CLEAR carries no reason code)")],
        expect="DECISION_GATE_INPUT_MALFORMED"))
    results.append(run_case(
        "N11 sequence gap 0,2 (A4-iv)", policy,
        [RED, mutate(B2_CLEAR, sequence=2)], expected_settled_round=(RUN_ID, "plan", 1),
        expect="DECISION_GATE_INPUT_MALFORMED"))
    results.append(run_case(
        "N12 unresolved open NEEDS_INPUT in the ledger (A5)", policy,
        [mutate(RED, prior_open_decision_items=[ledger_key(B2_NEEDS_INPUT)]), B2_NEEDS_INPUT],
        expected_settled_round=(RUN_ID, "plan", 1),
        expect="DECISION_BLOCKED:NEEDS_INPUT:blast_radius_beyond_scope"))
    results.append(run_case(
        "N13 RED declares [] while an open item exists -- F10 (A6)", policy,
        [mutate(RED, prior_open_decision_items=[]), B2_NEEDS_INPUT],
        expected_settled_round=(RUN_ID, "plan", 1),
        expect="DECISION_GATE_DECLARATION_DISAGREES_WITH_LEDGER"))

    # ---- ON-DISK ROUND TRIP: producer -> file -> reader -> A1-A6 ----------
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / RUN_ID / "decision_ledger"
        root.mkdir(parents=True)
        # open_decision_ledger()'s write: staged then published under a sequence key.
        (root / "000000.json").write_text(json.dumps(RED, indent=2, sort_keys=True) + "\n")
        on_disk = [json.loads(f.read_text()) for f in sorted(root.glob("*.json"))]
        print("\n-- on-disk round trip --")
        print(f"   published keys: {[f.name for f in sorted(root.glob('*.json'))]}")
        print(f"   ledger_schema_version read back from disk: "
              f"{on_disk[0]['ledger_schema_version']!r}")
        results.append(run_case("D1  RED read from disk, complete A1-A6", policy, on_disk,
                                expect="ADMITTED"))
        bumped = mutate(on_disk[0], ledger_schema_version=LEDGER_RECORD_SCHEMA_VERSION + 1)
        (root / "000000.json").write_text(json.dumps(bumped, indent=2, sort_keys=True) + "\n")
        future = [json.loads(f.read_text()) for f in sorted(root.glob("*.json"))]
        results.append(run_case(
            "D2  a FUTURE-version ledger written by a newer build fails closed", policy, future,
            expect="DECISION_LEDGER_SCHEMA_UNSUPPORTED"))

    print()
    print(f"{sum(results)}/{len(results)} cases behaved as specified")
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
