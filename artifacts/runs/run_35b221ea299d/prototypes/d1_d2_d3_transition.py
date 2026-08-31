"""DESIGN evidence for D1 (terminal vocabulary), D2 (decision channel), D3
(non-consumption site) and PLAN P6b's transition table -- executed, not asserted.

Imports the REAL repository modules so every claim is checked against shipped code:
  * run_logging.RUN_STATUS_VALUES        -- D1: no fifth lifecycle value is added
  * e2e_harness.FIELD_LINE/_parse_choice -- D2: the channel reuses the existing parser
  * workflow_contract                    -- D2: the SKILL.md additions do not break the loader
  * decision_policy                      -- the OS-28 authority the gate calls
"""
from __future__ import annotations

import json
import re
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))

from decision_policy import (  # noqa: E402
    DecisionPolicyError,
    load_decision_policy,
    validate_record,
    validate_transition,
)
from e2e_harness import FIELD_LINE, OutputContractError  # noqa: E402
from run_logging import ROUND_KIND_VALUES, RUN_STATUS_VALUES  # noqa: E402
from workflow_contract import load_workflow_output_contract  # noqa: E402

RESULTS: list[tuple[bool, str, str]] = []


def check(name: str, got, expect) -> None:
    RESULTS.append((got == expect, name, f"expect={expect!r} got={got!r}"))


# ---------------------------------------------------------------------------
# D1. The terminal vocabulary. O1 = RUN_STATUS: BLOCKED + a closed REASON constant
#     + two sparse columns. Nothing is added to any of the four value sets.
# ---------------------------------------------------------------------------
DECISION_STATES = ("CLEAR", "ASSUMPTION_ALLOWED", "NEEDS_INPUT", "CONFLICT")
BLOCKING_STATES = ("NEEDS_INPUT", "CONFLICT")

# The closed OS-29 reason set. Every member is a `reason` string on an EXISTING
# RUN_STATUS: BLOCKED -- the same shape MAX_ITERATIONS_REACHED already has.
GATE_REFUSAL_REASONS = (
    "DECISION_GATE_INPUT_MISSING",
    "DECISION_GATE_INPUT_MALFORMED",
    "DECISION_GATE_INPUT_UNBOUND",
    "DECISION_LEDGER_INCONSISTENT",
    "DECISION_LEDGER_SCHEMA_UNSUPPORTED",
    "DECISION_GATE_DECLARATION_DISAGREES_WITH_LEDGER",
    "DECISION_GATE_SUMMARY_DISAGREES_WITH_RECORD",
    "DECISION_DOWNGRADE_REJECTED",
)
BLOCK_REASON = re.compile(
    r"^DECISION_BLOCKED:(?P<state>NEEDS_INPUT|CONFLICT):(?P<code>[a-z][a-z0-9_]*)$"
)

check("D1-1 RUN_STATUS_VALUES unchanged", RUN_STATUS_VALUES,
      ("COMPLETED", "BLOCKED", "ERROR", "ESCALATED"))
check("D1-2 BLOCKED is already a member", "BLOCKED" in RUN_STATUS_VALUES, True)
check("D1-3 no WAITING_FOR_INPUT taken (OS-31)", "WAITING_FOR_INPUT" in RUN_STATUS_VALUES, False)
check("D1-4 ROUND_KIND_VALUES stays at four", len(ROUND_KIND_VALUES), 4)
check("D1-5 block reason grammar accepts a real code",
      bool(BLOCK_REASON.match("DECISION_BLOCKED:NEEDS_INPUT:blast_radius_beyond_scope")), True)
check("D1-6 block reason grammar rejects a non-blocking state",
      bool(BLOCK_REASON.match("DECISION_BLOCKED:CLEAR:none")), False)
check("D1-7 every refusal reason is closed and distinct",
      len(set(GATE_REFUSAL_REASONS)), len(GATE_REFUSAL_REASONS))

# Every reason code the shipped contract defines must be expressible in the grammar.
_policy = load_decision_policy(REPO / "orca-worker-reviewer-orchestration" / "SKILL.md")
_blocking_codes = [c for c, spec in _policy.reason_codes.items()
                   if spec.state in BLOCKING_STATES]
check("D1-8 all blocking reason codes fit the reason grammar",
      all(BLOCK_REASON.match(f"DECISION_BLOCKED:{_policy.reason_codes[c].state}:{c}")
          for c in _blocking_codes) and len(_blocking_codes) > 0, True)

# The Worker STATUS vocabulary is NOT widened (O3 rejected).
_contract = load_workflow_output_contract(REPO / "orca-worker-reviewer-orchestration" / "SKILL.md")
check("D1-9 worker vocabulary is still exactly the pair",
      {_contract.worker_complete, _contract.worker_blocked}, {"COMPLETE", "BLOCKED"})


# ---------------------------------------------------------------------------
# D2. The decision channel: a REQUIRED field line + a REQUIRED fenced JSON block,
#     in the agent's own result body. The record is the authority; the field line
#     is its projection; disagreement is terminal.
# ---------------------------------------------------------------------------
GATE_STATE_FIELD = "DECISION_GATE_STATE"
GATE_BLOCK = re.compile(r"(?ms)^```decision-gate\n(?P<body>.*?)\n```\s*$")


class GateInput(Exception):
    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(f"{reason}{(' -- ' + detail) if detail else ''}")
        self.reason = reason


def parse_gate_result(output: str, policy) -> dict:
    """Fail-closed. Returns the validated record, or raises GateInput."""
    values = [m.group("value") for m in FIELD_LINE.finditer(output)
              if m.group("field") == GATE_STATE_FIELD]
    if not values:
        raise GateInput("DECISION_GATE_INPUT_MISSING", f"no {GATE_STATE_FIELD} line")
    if len(values) != 1:
        raise GateInput("DECISION_GATE_INPUT_MALFORMED", f"{len(values)} {GATE_STATE_FIELD} lines")
    declared = values[0]
    if declared not in DECISION_STATES:
        raise GateInput("DECISION_GATE_INPUT_MALFORMED", f"unknown state {declared!r}")
    blocks = GATE_BLOCK.findall(output)
    if not blocks:
        raise GateInput("DECISION_GATE_INPUT_MISSING", "no ```decision-gate block")
    if len(blocks) != 1:
        raise GateInput("DECISION_GATE_INPUT_MALFORMED", f"{len(blocks)} decision-gate blocks")
    try:
        record = json.loads(blocks[0])
    except json.JSONDecodeError as exc:
        raise GateInput("DECISION_GATE_INPUT_MALFORMED", f"unparseable JSON: {exc}") from exc
    if not isinstance(record, dict):
        raise GateInput("DECISION_GATE_INPUT_MALFORMED", "the block is not an object")
    # THE AUTHORITY IS THE RECORD. The field line is reconciled against it.
    if record.get("state") != declared:
        raise GateInput("DECISION_GATE_SUMMARY_DISAGREES_WITH_RECORD",
                        f"{GATE_STATE_FIELD}={declared} but record.state={record.get('state')!r}")
    try:
        validate_record(policy, record)
    except DecisionPolicyError as exc:
        raise GateInput("DECISION_GATE_INPUT_MALFORMED", str(exc)) from exc
    return record


def body(state: str, *, record_state: str | None = None, extra: str = "", record_extra=None,
         omit_field=False, omit_block=False, dup_field=False) -> str:
    rec = {
        "ledger_schema_version": 1, "state": record_state or state,
        "reason_code": None, "open_decision_item": False,
        "run": "run_x", "phase": "design", "iteration": 1,
        "responsible_phase": "design", "role": "worker",
        "boundary": "B2", "sequence": 1, "source": "worker",
        "grounds": "No boundary element declared by this phase is triggering.",
        "scope": "This phase's own conduct.",
    }
    if record_extra:
        rec.update(record_extra)
        rec.pop("_", None)
    parts = ["# Worker Result", "", "STATUS: COMPLETE"]
    if not omit_field:
        parts.append(f"{GATE_STATE_FIELD}: {state}")
    if dup_field:
        parts.append(f"{GATE_STATE_FIELD}: {state}")
    parts += ["", "## Decision Record (optional)", "",
              # The NARRATIVE half. It uses DECISION_STATE, a DIFFERENT field name,
              # so it can never be read as the gate result (R-A2-1 preserved).
              f"DECISION_STATE: {state}", "REASON_CODE: none", extra, ""]
    if not omit_block:
        parts += ["```decision-gate", json.dumps(rec, indent=2), "```"]
    return "\n".join(parts) + "\n"


def gate(text) -> str:
    try:
        rec = parse_gate_result(text, _policy)
        return f"ADMITTED:{rec['state']}"
    except GateInput as exc:
        return exc.reason


check("D2-1 a valid CLEAR result admits", gate(body("CLEAR")), "ADMITTED:CLEAR")
check("D2-2 absent field line fails closed",
      gate(body("CLEAR", omit_field=True)), "DECISION_GATE_INPUT_MISSING")
check("D2-3 absent record block fails closed",
      gate(body("CLEAR", omit_block=True)), "DECISION_GATE_INPUT_MISSING")
check("D2-4 duplicate field line fails closed",
      gate(body("CLEAR", dup_field=True)), "DECISION_GATE_INPUT_MALFORMED")
check("D2-5 unknown state fails closed",
      gate(body("CLEAR").replace(f"{GATE_STATE_FIELD}: CLEAR", f"{GATE_STATE_FIELD}: MAYBE")),
      "DECISION_GATE_INPUT_MALFORMED")
check("D2-6 summary/record disagreement is its OWN terminal reason",
      gate(body("CLEAR", record_state="NEEDS_INPUT")),
      "DECISION_GATE_SUMMARY_DISAGREES_WITH_RECORD")

# THE MOTIVATING DRIFT CASE: this run's own ANALYSIS iteration-1 defect. The Markdown
# reads as correct ("CLEAR carries no reason code") while the machine record supplies
# a reason_code, which validate_record() rejects for CLEAR.
_f001 = body("CLEAR", record_extra={"reason_code": "(none - CLEAR carries no reason code)"})
check("D2-7 the ANALYSIS F-001 drift case fails closed",
      gate(_f001), "DECISION_GATE_INPUT_MALFORMED")

# R-A2-1 non-vacuity: the narrative section is present and says CLEAR in BOTH the
# passing and the failing case, so the gate verdict is attributable to the record.
check("D2-8 the narrative DECISION_STATE line is present in the rejected case",
      "DECISION_STATE: CLEAR" in _f001, True)
check("D2-9 the narrative line alone never admits: strip the gate field and it blocks",
      gate(_f001.replace(f"{GATE_STATE_FIELD}: CLEAR\n", "")),
      "DECISION_GATE_INPUT_MISSING")

# The existing STATUS parse is untouched by the new field (the OS-3 UNIT_TEST_STATUS
# precedent, fake_worker.py:28-32).
_statuses = [m.group("value") for m in FIELD_LINE.finditer(body("CLEAR"))
             if m.group("field") == "STATUS"]
check("D2-10 STATUS still parses exactly once", _statuses, ["COMPLETE"])

# A blocking result is a first-class, VALID gate input -- it is admitted as INPUT and
# then routed by the decision axis; it is never an input error.
_ni = {"ledger_schema_version": 1, "state": "NEEDS_INPUT",
       "reason_code": "blast_radius_beyond_scope", "boundary_element": "blast_radius",
       "blast_radius": "external_system",
       "what_is_missing": "whether the user authorizes touching files outside the declared scope",
       "why_policy_cannot_decide": "no repository policy or phase contract fixes the scope boundary",
       "open_decision_item": True, "run": "run_x", "phase": "design", "iteration": 1,
       "responsible_phase": "design", "role": "worker", "boundary": "B2", "sequence": 1,
       "source": "worker"}
_ni_body = ("# Worker Result\n\nSTATUS: COMPLETE\n"
            f"{GATE_STATE_FIELD}: NEEDS_INPUT\n\n```decision-gate\n"
            + json.dumps(_ni, indent=2) + "\n```\n")
check("D2-11 a well-formed NEEDS_INPUT is a VALID input, not an input error",
      gate(_ni_body), "ADMITTED:NEEDS_INPUT")


# ---------------------------------------------------------------------------
# D2 (cont). The SKILL.md / reviews-common vocabulary additions must not break
#            workflow_contract's loader. Proved on a real copy of the Skill tree.
# ---------------------------------------------------------------------------
_tmp = Path(tempfile.mkdtemp())
_skill = _tmp / "orca-worker-reviewer-orchestration"
shutil.copytree(REPO / "orca-worker-reviewer-orchestration", _skill)
_before = load_workflow_output_contract(_skill / "SKILL.md")

_line = f"{GATE_STATE_FIELD}: CLEAR | ASSUMPTION_ALLOWED | NEEDS_INPUT | CONFLICT"
_sk = (_skill / "SKILL.md").read_text(encoding="utf-8")
(_skill / "SKILL.md").write_text(
    _sk.replace("## 11. Reviewer Contract", _line + "\n\n## 11. Reviewer Contract", 1),
    encoding="utf-8")
_rc = (_skill / "reviews" / "common.md").read_text(encoding="utf-8")
(_skill / "reviews" / "common.md").write_text(_rc + "\n" + _line + "\n", encoding="utf-8")
try:
    _after = load_workflow_output_contract(_skill / "SKILL.md")
    check("D2-12 the SKILL/reviews additions leave the loaded contract identical",
          _after, _before)
except Exception as exc:  # noqa: BLE001
    check("D2-12 the SKILL/reviews additions leave the loaded contract identical",
          f"RAISED {type(exc).__name__}: {exc}", _before)

# The non-vacuity half: a four-value line whose values contain NO underscore DOES
# collide with REVIEW_VERDICT_LINE, which is why the state names' underscores are
# load-bearing rather than incidental.
(_skill / "reviews" / "common.md").write_text(
    _rc + "\nFOO: AAA | BBB | CCC | DDD\n", encoding="utf-8")
try:
    load_workflow_output_contract(_skill / "SKILL.md")
    _collide = "ACCEPTED"
except Exception as exc:  # noqa: BLE001
    _collide = type(exc).__name__
check("D2-13 control: an underscore-free four-value line DOES break the loader",
      _collide, "WorkflowContractError")
shutil.rmtree(_tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
# D3 + P6b. One risk-specific transition table, and non-consumption keyed on
#           `decision_block` in gate_attempts() -- ONE edit covering both shapes.
# ---------------------------------------------------------------------------
class Round:
    """The two ledgers gate_attempts() reads, plus the new decision_block field."""

    def __init__(self) -> None:
        self.worker_attempts: list[str] = []
        self.reviewer_attempts: list[str] = []
        self.final_status = "COMPLETED"
        self.reason: str | None = None
        self.decision_block: tuple[str, str] | None = None   # (state, reason_code)


def gate_attempts(result: Round, risk: str) -> int:
    """D3: the ONE edit. Keyed on decision_block, never on risk."""
    if result.decision_block is not None:
        return 0
    return len(result.worker_attempts if risk == "low" else result.reviewer_attempts)


def run_round(risk: str, worker_gate: str, reviewer_gate: str | None = None,
              *, worker_input_bad: str | None = None, reviewer_input_bad: str | None = None,
              reviewer_quality: str = "PASS", bound: bool = True) -> Round:
    """P6b, exactly: B2 rows 1-3 then B3 rows 4-10. No new dispatch site exists --
    the Reviewer below is the already-scheduled one."""
    r = Round()
    r.worker_attempts.append("worker")                       # e2e_harness.py:1012

    # ---- B2 (between :1012 and :1015). O-2: decision axis before quality axis.
    if worker_input_bad:                                     # row 3, risk-independent
        r.final_status, r.reason = "BLOCKED", worker_input_bad
        r.decision_block = ("INPUT", worker_input_bad)
        return r
    if worker_gate in BLOCKING_STATES:
        if risk == "low":                                    # row 2 LOW -> terminal at B2
            code = "blast_radius_beyond_scope" if worker_gate == "NEEDS_INPUT" else "requirement_contradiction"
            r.final_status = "BLOCKED"
            r.reason = f"DECISION_BLOCKED:{worker_gate}:{code}"
            r.decision_block = (worker_gate, code)
            return r
        verification_only = True                             # row 2 MED/HIGH -> fall through
    else:
        verification_only = False

    if risk == "low":                                        # e2e_harness.py:1070
        r.final_status = "COMPLETED"
        return r

    r.reviewer_attempts.append("reviewer")                   # e2e_harness.py:1194 (:1158 dispatch)

    # ---- B3 on the SAME code path, two modes.
    if verification_only:                                    # B3-V, rows 4-7
        if reviewer_input_bad or not bound:                  # row 7
            reason = reviewer_input_bad or "DECISION_GATE_INPUT_UNBOUND"
            r.final_status, r.reason = "BLOCKED", reason
            r.decision_block = ("INPUT", reason)
            return r
        rv = reviewer_gate or worker_gate
        if rv in BLOCKING_STATES:                            # rows 4 and 5
            state = "CONFLICT" if "CONFLICT" in (worker_gate, rv) and rv == "CONFLICT" else worker_gate
            state = rv if rv != worker_gate else worker_gate
            code = "blast_radius_beyond_scope" if state == "NEEDS_INPUT" else "requirement_contradiction"
            r.final_status = "BLOCKED"
            r.reason = f"DECISION_BLOCKED:{state}:{code}"
            r.decision_block = (state, code)
            return r
        # row 6: a proposed downgrade. Decided SOLELY by validate_transition().
        try:
            validate_transition(_policy, worker_gate, rv, {"state": rv})
            accepted = True
        except DecisionPolicyError:
            accepted = False
        code = "blast_radius_beyond_scope" if worker_gate == "NEEDS_INPUT" else "requirement_contradiction"
        r.final_status = "BLOCKED"                            # terminal either way (L6)
        r.reason = "DECISION_DOWNGRADE_REJECTED" if not accepted else f"DECISION_BLOCKED:{worker_gate}:{code}"
        r.decision_block = (worker_gate, code)
        return r

    # B3-N, rows 8-10
    if reviewer_input_bad:                                   # row 10
        r.final_status, r.reason = "BLOCKED", reviewer_input_bad
        r.decision_block = ("INPUT", reviewer_input_bad)
        return r
    if reviewer_gate in BLOCKING_STATES:                     # row 8
        code = "blast_radius_beyond_scope" if reviewer_gate == "NEEDS_INPUT" else "requirement_contradiction"
        r.final_status = "BLOCKED"
        r.reason = f"DECISION_BLOCKED:{reviewer_gate}:{code}"
        r.decision_block = (reviewer_gate, code)
        return r
    if reviewer_quality == "PASS":                           # row 9, untouched
        r.final_status = "COMPLETED"
        return r
    r.final_status, r.reason = "FAIL_ROUND", None            # row 9 FAIL -> correction
    return r


def outcome(risk, **kw):
    r = run_round(risk, **kw)
    return (r.final_status, r.reason, gate_attempts(r, risk))


# Row 2/4 cross-risk EQUALITY: risk never expands decision authority.
_low = outcome("low", worker_gate="NEEDS_INPUT")
_med = outcome("medium", worker_gate="NEEDS_INPUT", reviewer_gate="NEEDS_INPUT")
_high = outcome("high", worker_gate="NEEDS_INPUT", reviewer_gate="NEEDS_INPUT")
check("P6b-1 LOW terminal at B2", _low,
      ("BLOCKED", "DECISION_BLOCKED:NEEDS_INPUT:blast_radius_beyond_scope", 0))
check("P6b-2 MEDIUM terminal at B3-V is IDENTICAL", _med, _low)
check("P6b-3 HIGH terminal at B3-V is IDENTICAL", _high, _low)
# Non-vacuity: the three runs really differ elsewhere.
check("P6b-4 control: LOW dispatched no Reviewer, MEDIUM did",
      (len(run_round("low", worker_gate="NEEDS_INPUT").reviewer_attempts),
       len(run_round("medium", worker_gate="NEEDS_INPUT", reviewer_gate="NEEDS_INPUT").reviewer_attempts)),
      (0, 1))
check("P6b-5 CONFLICT behaves the same way across risk",
      outcome("low", worker_gate="CONFLICT"),
      outcome("high", worker_gate="CONFLICT", reviewer_gate="CONFLICT"))
check("P6b-6 row 3 is risk-independent and never spends the Reviewer",
      [outcome(r, worker_gate="CLEAR", worker_input_bad="DECISION_GATE_INPUT_MISSING")
       for r in ("low", "medium", "high")],
      [("BLOCKED", "DECISION_GATE_INPUT_MISSING", 0)] * 3)
check("P6b-7 row 5: a STRICTER verification carries the Reviewer's state",
      outcome("high", worker_gate="NEEDS_INPUT", reviewer_gate="CONFLICT"),
      ("BLOCKED", "DECISION_BLOCKED:CONFLICT:requirement_contradiction", 0))
check("P6b-8 row 6: an unauthorized downgrade is rejected by validate_transition",
      outcome("high", worker_gate="NEEDS_INPUT", reviewer_gate="CLEAR"),
      ("BLOCKED", "DECISION_DOWNGRADE_REJECTED", 0))
check("P6b-9 row 6: NEEDS_INPUT -> ASSUMPTION_ALLOWED is forbidden unconditionally",
      outcome("high", worker_gate="NEEDS_INPUT", reviewer_gate="ASSUMPTION_ALLOWED"),
      ("BLOCKED", "DECISION_DOWNGRADE_REJECTED", 0))
check("P6b-10 row 7: an unbound verification record is its own reason",
      outcome("high", worker_gate="NEEDS_INPUT", reviewer_gate="NEEDS_INPUT", bound=False),
      ("BLOCKED", "DECISION_GATE_INPUT_UNBOUND", 0))
check("P6b-11 row 8: a Reviewer-discovered block charges no iteration",
      outcome("high", worker_gate="CLEAR", reviewer_gate="CONFLICT", reviewer_quality="FAIL"),
      ("BLOCKED", "DECISION_BLOCKED:CONFLICT:requirement_contradiction", 0))

# ---- D3 non-consumption, with its MANDATORY co-located control (NV-2).
check("D3-1 a decision-blocked LOW round charges 0",
      gate_attempts(run_round("low", worker_gate="NEEDS_INPUT"), "low"), 0)
check("D3-2 a decision-blocked HIGH round charges 0",
      gate_attempts(run_round("high", worker_gate="NEEDS_INPUT", reviewer_gate="NEEDS_INPUT"), "high"), 0)
# THE CONTROL: quality FAIL must STILL charge one, at both counting rules.
check("D3-3 CONTROL: a quality-FAIL HIGH round still charges 1",
      gate_attempts(run_round("high", worker_gate="CLEAR", reviewer_gate="CLEAR",
                              reviewer_quality="FAIL"), "high"), 1)
check("D3-4 CONTROL: a passing LOW round still charges 1",
      gate_attempts(run_round("low", worker_gate="CLEAR"), "low"), 1)
check("D3-5 CONTROL: a passing HIGH round still charges 1",
      gate_attempts(run_round("high", worker_gate="CLEAR", reviewer_gate="CLEAR"), "high"), 1)
# The dispatch ledgers are NEVER rewound (C-9): the attempt lists stay non-empty.
check("D3-6 C-9: the blocked HIGH round's dispatch ledgers are still non-empty",
      (len(run_round("high", worker_gate="NEEDS_INPUT", reviewer_gate="NEEDS_INPUT").worker_attempts),
       len(run_round("high", worker_gate="NEEDS_INPUT", reviewer_gate="NEEDS_INPUT").reviewer_attempts)),
      (1, 1))
# INV-D1 holds in every cell: at most one Reviewer event per iteration.
_all = [run_round(r, worker_gate=w, reviewer_gate=rv)
        for r in ("low", "medium", "high") for w in DECISION_STATES for rv in DECISION_STATES]
check("D3-7 INV-D1 holds in every cell of the table",
      all(len(x.reviewer_attempts) <= len(x.worker_attempts) <= 1 for x in _all), True)


# ---------------------------------------------------------------------------
ok = sum(1 for good, _, _ in RESULTS if good)
for good, name, detail in RESULTS:
    print(f"[{'PASS' if good else 'FAIL'}] {name}\n        {detail}")
print(f"\n{ok}/{len(RESULTS)} cases behaved as specified")
sys.exit(0 if ok == len(RESULTS) else 1)
