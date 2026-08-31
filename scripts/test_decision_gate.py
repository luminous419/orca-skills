#!/usr/bin/env python3
"""OS-29: the gate parser/evaluator, the A1-A6 admissibility rule, and F1-F14.

Every negative case in this module differs from an ADMITTED positive in exactly one
field or one fact, and every non-vacuity control sits in the SAME test function as
the claim it protects -- the repository's own rule, recorded at
scripts/test_decision_policy.py:4-8. A negative assertion whose control lives in
another test proves only that some other test passed.
"""

from __future__ import annotations

import copy
import inspect
import json
import unittest
from pathlib import Path

from scripts import decision_gate
from scripts.decision_policy import load_decision_policy

REPO_ROOT = Path(__file__).resolve().parents[1]
SKILL_NAMES = ("orca-worker-reviewer-loop", "orca-worker-reviewer-orchestration")
FIXTURES = REPO_ROOT / "scripts" / "fixtures" / "decision_gate"
RUN = "run_fixture"


def load_fixture(kind: str, name: str) -> dict:
    return json.loads((FIXTURES / kind / f"{name}.json").read_text(encoding="utf-8"))


def gate_body(state: str, record: dict | None = None, *, declared: str | None = None,
              omit_field: bool = False, omit_block: bool = False,
              duplicate_field: bool = False, raw_block: str | None = None,
              narrative: bool = True) -> str:
    """A result body in the shape a real agent emits, with one seam per negative."""
    payload = record if record is not None else {"state": state}
    lines = ["# Worker Result", "", "STATUS: COMPLETE"]
    if not omit_field:
        lines.append(f"{decision_gate.GATE_STATE_FIELD}: {declared or state}")
    if duplicate_field:
        lines.append(f"{decision_gate.GATE_STATE_FIELD}: {declared or state}")
    if narrative:
        # The OPTIONAL narrative section, present in the positive AND in the
        # negatives, so a refusal is never attributable to its absence.
        lines += ["", "## Decision Record (optional)", "", f"DECISION_STATE: {state}",
                  "REASON_CODE: none", ""]
    if raw_block is not None:
        lines += ["```decision-gate", raw_block, "```"]
    elif not omit_block:
        lines += ["```decision-gate", json.dumps(payload, indent=2, sort_keys=True), "```"]
    return "\n".join(lines) + "\n"


def decision_half(record: dict) -> dict:
    """The half an AGENT emits: the ledger mechanics the harness stamps are dropped."""
    return {
        key: value
        for key, value in record.items()
        if key not in decision_gate.LEDGER_MECHANICS_FIELDS
        and key not in ("run", "phase", "iteration", "role", "verdict",
                        "source_binding", "recorded_at", "evidence", "assumption",
                        "open_item")
    }


class PolicyMixin(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.policy = load_decision_policy(
            REPO_ROOT / "orca-worker-reviewer-orchestration" / "SKILL.md"
        )

    def refusal(self, callable_, *args, **kwargs) -> str:
        with self.assertRaises(decision_gate.GateRefusal) as caught:
            callable_(*args, **kwargs)
        return caught.exception.reason


class GateResultParsingTests(PolicyMixin):
    """Scenario 13 / F1-F6: no path reaches CLEAR from an absent or broken result."""

    def test_a_valid_declaration_is_admitted_and_every_defect_is_refused(self) -> None:
        clear = decision_half(load_fixture("valid", "worker_clear"))

        admitted = decision_gate.parse_gate_result(gate_body("CLEAR", clear), self.policy)

        self.assertEqual(admitted.state, "CLEAR")
        self.assertIsNone(admitted.reason_code)
        # Each negative differs from the ADMITTED body above in exactly one thing.
        cases = {
            "F1 no field line": (
                gate_body("CLEAR", clear, omit_field=True),
                decision_gate.GATE_INPUT_MISSING,
            ),
            "F2 no record block": (
                gate_body("CLEAR", clear, omit_block=True),
                decision_gate.GATE_INPUT_MISSING,
            ),
            "F3 two field lines": (
                gate_body("CLEAR", clear, duplicate_field=True),
                decision_gate.GATE_INPUT_MALFORMED,
            ),
            "F4 unknown state": (
                gate_body("CLEAR", clear).replace(
                    f"{decision_gate.GATE_STATE_FIELD}: CLEAR",
                    f"{decision_gate.GATE_STATE_FIELD}: MAYBE",
                ),
                decision_gate.GATE_INPUT_MALFORMED,
            ),
            "F5 unparseable record": (
                gate_body("CLEAR", raw_block="{not json"),
                decision_gate.GATE_INPUT_MALFORMED,
            ),
            "F6 record is not an object": (
                gate_body("CLEAR", raw_block="[]"),
                decision_gate.GATE_INPUT_MALFORMED,
            ),
        }
        for label, (body, expected) in cases.items():
            with self.subTest(case=label):
                self.assertEqual(
                    self.refusal(decision_gate.parse_gate_result, body, self.policy),
                    expected,
                )

    def test_the_summary_and_the_record_are_reconciled_and_the_record_wins(self) -> None:
        clear = decision_half(load_fixture("valid", "worker_clear"))
        needs_input = decision_half(load_fixture("valid", "worker_needs_input"))

        # The declaration says CLEAR; the record says NEEDS_INPUT. Neither half wins.
        body = gate_body("NEEDS_INPUT", needs_input, declared="CLEAR")
        self.assertEqual(
            self.refusal(decision_gate.parse_gate_result, body, self.policy),
            decision_gate.SUMMARY_DISAGREES_WITH_RECORD,
        )
        # CONTROL: the same record with an agreeing declaration is admitted, so the
        # refusal above is attributable to the disagreement and not to the record.
        agreed = decision_gate.parse_gate_result(
            gate_body("NEEDS_INPUT", needs_input), self.policy
        )
        self.assertEqual(agreed.state, "NEEDS_INPUT")
        self.assertEqual(agreed.reason_code, "blast_radius_beyond_scope")
        # And the well-formed CLEAR still parses, so nothing above is a blanket refusal.
        self.assertEqual(
            decision_gate.parse_gate_result(gate_body("CLEAR", clear), self.policy).state,
            "CLEAR",
        )

    def test_the_markdown_narrative_alone_never_admits(self) -> None:
        """R-A2-1: the optional `## Decision Record` section is a DIFFERENT object.

        This is the ANALYSIS iteration-1 defect of this very run, promoted to a
        required fixture: prose that reads as correct beside a record the OS-28
        contract rejects.
        """
        drifted = decision_half(load_fixture("invalid", "clear_carries_a_reason_code"))
        body = gate_body("CLEAR", drifted)

        # The narrative section is PRESENT and says CLEAR in the refused document.
        self.assertIn("DECISION_STATE: CLEAR", body)
        self.assertEqual(
            self.refusal(decision_gate.parse_gate_result, body, self.policy),
            decision_gate.GATE_INPUT_MALFORMED,
        )
        # Strip the GATE field and only the narrative remains: still a refusal, which
        # is the half that proves the narrative can never stand in for the gate.
        narrative_only = body.replace(
            f"{decision_gate.GATE_STATE_FIELD}: CLEAR\n", ""
        )
        self.assertIn("DECISION_STATE: CLEAR", narrative_only)
        self.assertEqual(
            self.refusal(
                decision_gate.parse_gate_result, narrative_only, self.policy
            ),
            decision_gate.GATE_INPUT_MISSING,
        )

    def test_the_closed_field_set_is_d5s_lineage_boundary(self) -> None:
        clear = decision_half(load_fixture("valid", "worker_clear"))

        for field in decision_gate.OS30_RESERVED_FIELDS:
            with self.subTest(field=field):
                self.assertNotIn(field, decision_gate.CLOSED_LEDGER_RECORD_FIELDS)
                smuggled = dict(clear, **{field: "run_x/plan/1/B2#1"})
                self.assertEqual(
                    self.refusal(
                        decision_gate.parse_gate_result,
                        gate_body("CLEAR", smuggled),
                        self.policy,
                    ),
                    decision_gate.GATE_INPUT_MALFORMED,
                )
        # CONTROL: `verifies` -- a reference to a ledger RECORD, which the audit needs
        # -- is inside the set, so the closed set is not simply rejecting extras.
        self.assertIn("verifies", decision_gate.CLOSED_LEDGER_RECORD_FIELDS)
        self.assertEqual(
            decision_gate.parse_gate_result(gate_body("CLEAR", clear), self.policy).state,
            "CLEAR",
        )


class LedgerRecordValidationTests(PolicyMixin):
    """F13 / F14: A4 is a property of EVERY record, not only of sequence 0."""

    def test_every_ledger_record_declares_a_supported_schema_version(self) -> None:
        good = load_fixture("valid", "run_entry_declaration")
        decision_gate.validate_ledger_record(self.policy, good)  # the control

        cases = {
            "f13_ledger_schema_version_absent": decision_gate.GATE_INPUT_MALFORMED,
            "f13_ledger_schema_version_text": decision_gate.GATE_INPUT_MALFORMED,
            "f13_ledger_schema_version_bool": decision_gate.GATE_INPUT_MALFORMED,
            "f13_policy_block_version_smuggled": decision_gate.GATE_INPUT_MALFORMED,
            "f14_ledger_schema_version_unsupported": (
                decision_gate.LEDGER_SCHEMA_UNSUPPORTED
            ),
        }
        for name, expected in cases.items():
            with self.subTest(fixture=name):
                self.assertEqual(
                    self.refusal(
                        decision_gate.validate_ledger_record,
                        self.policy,
                        load_fixture("invalid", name),
                    ),
                    expected,
                )
        # "this build is too old for this ledger" must never be confused with
        # "this record is broken": they are different reasons on purpose.
        self.assertNotEqual(
            decision_gate.LEDGER_SCHEMA_UNSUPPORTED, decision_gate.GATE_INPUT_MALFORMED
        )
        # The two version constants are separate objects with separate owners.
        from scripts import decision_policy as policy_module

        self.assertIsNot(
            decision_gate.SUPPORTED_LEDGER_RECORD_SCHEMA_VERSIONS,
            policy_module.SUPPORTED_SCHEMA_VERSIONS,
        )

    def test_every_required_field_is_required(self) -> None:
        good = load_fixture("valid", "worker_clear")
        decision_gate.validate_ledger_record(self.policy, good)

        required = (
            decision_gate.REQUIRED_LEDGER_RECORD_FIELDS
            + decision_gate.LEDGER_MECHANICS_FIELDS
        )
        self.assertEqual(len(required), 19)
        for field in required:
            if field == "ledger_schema_version":
                continue  # its own clause, asserted above with its own reason
            with self.subTest(field=field):
                missing = {k: v for k, v in good.items() if k != field}
                self.assertEqual(
                    self.refusal(
                        decision_gate.validate_ledger_record, self.policy, missing
                    ),
                    decision_gate.GATE_INPUT_MALFORMED,
                )

    def test_each_state_carries_all_thirteen_fields_and_the_contract_accepts_it(
        self,
    ) -> None:
        for name in (
            "run_entry_declaration",
            "worker_clear",
            "worker_assumption_allowed",
            "worker_needs_input",
            "worker_conflict",
        ):
            with self.subTest(fixture=name):
                record = load_fixture("valid", name)
                for field in decision_gate.REQUIRED_LEDGER_RECORD_FIELDS:
                    self.assertIn(field, record)
                decision_gate.validate_ledger_record(self.policy, record)
        # CONTROLS, co-located: the validator is not accepting everything.
        self.assertEqual(
            self.refusal(
                decision_gate.validate_ledger_record,
                self.policy,
                load_fixture("invalid", "clear_carries_a_reason_code"),
            ),
            decision_gate.GATE_INPUT_MALFORMED,
        )
        self.assertEqual(
            self.refusal(
                decision_gate.validate_ledger_record,
                self.policy,
                load_fixture("invalid", "needs_input_missing_required_evidence"),
            ),
            decision_gate.GATE_INPUT_MALFORMED,
        )


class AdmissibilityTests(PolicyMixin):
    """P6a A1-A6, in the fixed evaluation order A1 -> A2 -> A4 -> A3 -> A6 -> A5."""

    def setUp(self) -> None:
        self.red = load_fixture("valid", "run_entry_declaration")
        self.worker_clear = load_fixture("valid", "worker_clear")
        self.worker_open = load_fixture("valid", "worker_needs_input")

    def admit(self, records, *, expected=None):
        return decision_gate.admit_head(
            self.policy, records, run_id=RUN, expected_settled_round=expected
        )

    def test_the_run_entry_declaration_is_admissible_at_exactly_one_position(
        self,
    ) -> None:
        # POSITIVE: the run's first boundary, ledger = [RED].
        head = self.admit([self.red])
        self.assertEqual(head["sequence"], 0)

        # POSITIVE: a later boundary whose head is the settled agent record.
        head = self.admit(
            [self.red, self.worker_clear], expected=(RUN, "implementation", 1)
        )
        self.assertEqual(decision_gate.ledger_key(head), f"{RUN}/implementation/1/B2#1")

        # F11, THE HOLE PROOF: delete the settled record and the RED is the head
        # again. It must NOT stand in for the agent judgement that is missing.
        self.assertEqual(
            self.refusal(self.admit, [self.red], expected=(RUN, "implementation", 1)),
            decision_gate.GATE_INPUT_UNBOUND,
        )
        # ...and a head that binds a DIFFERENT round is equally unbound.
        self.assertEqual(
            self.refusal(
                self.admit,
                [self.red, self.worker_clear],
                expected=(RUN, "implementation", 2),
            ),
            decision_gate.GATE_INPUT_UNBOUND,
        )

    def test_absence_and_inconsistency_are_refusals_never_clear(self) -> None:
        # A1
        self.assertEqual(self.refusal(self.admit, []), decision_gate.GATE_INPUT_MISSING)
        # A2: two sequence-0 records
        self.assertEqual(
            self.refusal(self.admit, [self.red, dict(self.red)]),
            decision_gate.LEDGER_INCONSISTENT,
        )
        # A2: sequence 0 is not this run's declaration
        self.assertEqual(
            self.refusal(self.admit, [dict(self.red, source="worker", boundary="B2")]),
            decision_gate.LEDGER_INCONSISTENT,
        )
        self.assertEqual(
            self.refusal(self.admit, [dict(self.red, run="run_somebody_else")]),
            decision_gate.LEDGER_INCONSISTENT,
        )
        # A4-iv: a gap
        self.assertEqual(
            self.refusal(
                self.admit,
                [self.red, dict(self.worker_clear, sequence=2)],
                expected=(RUN, "implementation", 1),
            ),
            decision_gate.GATE_INPUT_MALFORMED,
        )
        # CONTROL: the honest ledger still admits after every mutant above.
        self.assertEqual(self.admit([self.red])["state"], "CLEAR")

    def test_the_declaration_is_recomputed_and_an_open_item_blocks(self) -> None:
        open_key = decision_gate.ledger_key(self.worker_open)

        # A6: the declaration claims nothing is open while the ledger says otherwise.
        self.assertEqual(
            self.refusal(
                self.admit,
                [dict(self.red, prior_open_decision_items=[]), self.worker_open],
                expected=(RUN, "implementation", 1),
            ),
            decision_gate.DECLARATION_DISAGREES_WITH_LEDGER,
        )
        # A5: an HONEST declaration of the same ledger blocks on the open item, and
        # the reason NAMES the state and the code -- scenario 12's fail-closed half.
        reason = self.refusal(
            self.admit,
            [dict(self.red, prior_open_decision_items=[open_key]), self.worker_open],
            expected=(RUN, "implementation", 1),
        )
        self.assertEqual(reason, "DECISION_BLOCKED:NEEDS_INPUT:blast_radius_beyond_scope")
        self.assertTrue(decision_gate.BLOCK_REASON_PATTERN.match(reason))
        # CONTROL: the same ledger with a CLEAR agent record admits, so A5/A6 are not
        # simply refusing every two-record ledger.
        self.assertEqual(
            self.admit(
                [self.red, self.worker_clear], expected=(RUN, "implementation", 1)
            )["state"],
            "CLEAR",
        )

    def test_a_worker_reviewer_agreement_never_resolves_an_open_item(self) -> None:
        """Scenario 10 at the ledger level: agreement is not a transition."""
        open_key = decision_gate.ledger_key(self.worker_open)
        agreeing_reviewer = dict(
            self.worker_open,
            sequence=2,
            role="reviewer",
            boundary="B3",
            source="reviewer",
            verdict="FAIL",
        )
        records = [
            dict(
                self.red,
                prior_open_decision_items=[
                    open_key, decision_gate.ledger_key(agreeing_reviewer)
                ],
            ),
            self.worker_open,
            agreeing_reviewer,
        ]

        reason = self.refusal(
            self.admit, records, expected=(RUN, "implementation", 1)
        )

        self.assertTrue(reason.startswith("DECISION_BLOCKED:"))
        # PRECHECK that the injected value really is the forbidden category, on the
        # test_decision_policy.py:2157-2178 pattern.
        self.assertIn(
            "worker_reviewer_agreement", self.policy.forbidden_authority_sources
        )
        self.assertEqual(len(self.policy.forbidden_authority_sources), 5)


class VerificationTests(PolicyMixin):
    """P6b rows 4-7: what the already-scheduled Reviewer may and may not do."""

    def setUp(self) -> None:
        self.worker = decision_gate.GateResult(
            declared_state="NEEDS_INPUT",
            record=decision_half(load_fixture("valid", "worker_needs_input")),
        )

    def reviewer(self, name: str, **kw) -> decision_gate.GateResult:
        record = dict(decision_half(load_fixture("valid", name)), **kw)
        return decision_gate.GateResult(declared_state=record["state"], record=record)

    def test_a_confirmation_is_byte_identical_to_the_low_terminal(self) -> None:
        outcome = decision_gate.evaluate_verification(
            self.policy, self.worker, self.reviewer("worker_needs_input")
        )

        self.assertEqual(
            outcome.reason,
            decision_gate.block_reason("NEEDS_INPUT", "blast_radius_beyond_scope"),
        )
        self.assertEqual(outcome.block, ("NEEDS_INPUT", "blast_radius_beyond_scope"))

    def test_a_stricter_verification_carries_the_reviewers_own_state(self) -> None:
        outcome = decision_gate.evaluate_verification(
            self.policy, self.worker, self.reviewer("worker_conflict")
        )

        self.assertEqual(outcome.block, ("CONFLICT", "requirement_contradiction"))
        # CONTROL: a confirmation carries the WORKER's, so "stricter" is a real
        # branch rather than the only branch.
        self.assertEqual(
            decision_gate.evaluate_verification(
                self.policy, self.worker, self.reviewer("worker_needs_input")
            ).block,
            ("NEEDS_INPUT", "blast_radius_beyond_scope"),
        )

    def test_a_downgrade_is_decided_by_the_shared_contract_alone(self) -> None:
        """Scenario 4/10 -- OS-29 writes no downgrade rule of its own."""
        to_clear = decision_gate.evaluate_verification(
            self.policy, self.worker, self.reviewer("worker_clear")
        )
        to_assumption = decision_gate.evaluate_verification(
            self.policy, self.worker, self.reviewer("worker_assumption_allowed")
        )

        self.assertEqual(to_clear.reason, decision_gate.DOWNGRADE_REJECTED)
        self.assertEqual(to_assumption.reason, decision_gate.DOWNGRADE_REJECTED)
        # PRECHECK: the two rejections come from two DIFFERENT contract rules, so
        # this is the contract deciding and not one blanket refusal.
        self.assertEqual(
            self.policy.transitions[("NEEDS_INPUT", "ASSUMPTION_ALLOWED")], "forbidden"
        )
        self.assertEqual(
            self.policy.transitions[("NEEDS_INPUT", "CLEAR")], "requires_user_decision"
        )
        # ...and the block still carries the WORKER's classification: a rejected
        # downgrade does not erase what was classified.
        self.assertEqual(to_clear.block, ("NEEDS_INPUT", "blast_radius_beyond_scope"))

    def test_an_unbound_verification_record_is_its_own_defect(self) -> None:
        worker_key = f"{RUN}/implementation/1/B2#1"
        bound = self.reviewer(
            "worker_needs_input",
            verifies={
                "run": RUN,
                "phase": "implementation",
                "iteration": 1,
                "worker_record_key": worker_key,
            },
        )

        self.assertIsNone(
            decision_gate.verification_binding_defect(
                bound, worker_key=worker_key, run_id=RUN, phase="implementation",
                iteration=1,
            )
        )
        # Each negative differs from the bound record in exactly one thing.
        for label, record in {
            "absent": self.reviewer("worker_needs_input"),
            "wrong key": self.reviewer(
                "worker_needs_input",
                verifies={
                    "run": RUN, "phase": "implementation", "iteration": 1,
                    "worker_record_key": f"{RUN}/implementation/9/B2#9",
                },
            ),
            "wrong round": self.reviewer(
                "worker_needs_input",
                verifies={
                    "run": RUN, "phase": "implementation", "iteration": 2,
                    "worker_record_key": worker_key,
                },
            ),
        }.items():
            with self.subTest(case=label):
                self.assertIsNotNone(
                    decision_gate.verification_binding_defect(
                        record, worker_key=worker_key, run_id=RUN,
                        phase="implementation", iteration=1,
                    )
                )


class RiskIndependenceTests(PolicyMixin):
    """Scenario 7 P1: risk is INERT here, not merely absent.

    Mirrors test_decision_policy.py's own signature assertion for permitted_states,
    whose comment names this as the anti-vacuity move.
    """

    GATE_FUNCTIONS = (
        "parse_gate_result",
        "parse_declared_state",
        "validate_gate_record",
        "validate_ledger_record",
        "admit_head",
        "evaluate_verification",
        "verification_binding_defect",
        "open_items",
        "block_reason",
        "decision_columns",
        "ledger_key",
    )
    AXIS_TOKENS = ("risk", "profile", "quality_profile", "agent_profile", "severity")

    def test_no_gate_function_takes_a_risk_or_profile_parameter(self) -> None:
        checked = 0
        for name in self.GATE_FUNCTIONS:
            function = getattr(decision_gate, name)
            parameters = set(inspect.signature(function).parameters)
            checked += 1
            for token in self.AXIS_TOKENS:
                with self.subTest(function=name, token=token):
                    self.assertNotIn(token, parameters)
        # The anti-vacuity half: the loop really inspected the functions, and the
        # inspector really can see a parameter when one exists.
        self.assertEqual(checked, len(self.GATE_FUNCTIONS))
        self.assertIn(
            "policy", set(inspect.signature(decision_gate.admit_head).parameters)
        )

    def test_the_module_source_never_branches_on_a_risk_level(self) -> None:
        source = (REPO_ROOT / "scripts" / "decision_gate.py").read_text(
            encoding="utf-8"
        )
        code = "\n".join(
            line for line in source.splitlines() if not line.strip().startswith("#")
        )

        for level in ("low", "medium", "high"):
            with self.subTest(level=level):
                self.assertNotIn(f'"{level}"', code)
        # CONTROL: the scan is looking at real source that does contain other
        # quoted literals, so "not found" is a fact and not an empty read.
        self.assertIn('"CLEAR"', code)


class ReasonVocabularyTests(PolicyMixin):
    """D1: the terminal vocabulary is the EXISTING one, plus closed reason strings."""

    def test_the_refusal_reasons_are_closed_and_distinct(self) -> None:
        self.assertEqual(
            len(set(decision_gate.GATE_REFUSAL_REASONS)),
            len(decision_gate.GATE_REFUSAL_REASONS),
        )
        self.assertEqual(len(decision_gate.GATE_REFUSAL_REASONS), 8)
        for reason in decision_gate.GATE_REFUSAL_REASONS:
            with self.subTest(reason=reason):
                self.assertIsNone(decision_gate.BLOCK_REASON_PATTERN.match(reason))
                self.assertEqual(
                    decision_gate.decision_columns(reason),
                    (decision_gate.INPUT_DEFECT_STATE, reason),
                )

    def test_every_blocking_reason_code_fits_the_block_grammar(self) -> None:
        blocking = [
            code
            for code, spec in self.policy.reason_codes.items()
            if spec.state in decision_gate.BLOCKING_STATES
        ]

        self.assertTrue(blocking)
        for code in blocking:
            state = self.policy.reason_codes[code].state
            with self.subTest(code=code):
                reason = decision_gate.block_reason(state, code)
                self.assertTrue(decision_gate.BLOCK_REASON_PATTERN.match(reason))
                self.assertEqual(decision_gate.decision_columns(reason), (state, code))
        # CONTROL: a non-blocking state cannot be spelled as a decision block.
        self.assertIsNone(
            decision_gate.BLOCK_REASON_PATTERN.match(
                decision_gate.block_reason("CLEAR", "none")
            )
        )

    def test_the_gate_vocabulary_matches_the_shipped_contract_in_both_skills(self) -> None:
        for name in SKILL_NAMES:
            with self.subTest(skill=name):
                policy = load_decision_policy(REPO_ROOT / name / "SKILL.md")
                self.assertEqual(
                    tuple(policy.states), tuple(decision_gate.DECISION_STATES)
                )


class SchemaVersionCompatibilityTests(PolicyMixin):
    """A newer ledger written by a newer build can only make a run BLOCK."""

    def test_a_future_version_fails_closed_in_both_directions(self) -> None:
        red = load_fixture("valid", "run_entry_declaration")
        future = copy.deepcopy(red)
        future["ledger_schema_version"] = (
            max(decision_gate.SUPPORTED_LEDGER_RECORD_SCHEMA_VERSIONS) + 1
        )

        with self.assertRaises(decision_gate.GateRefusal) as caught:
            decision_gate.admit_head(
                self.policy, [future], run_id=RUN, expected_settled_round=None
            )

        self.assertEqual(caught.exception.reason, decision_gate.LEDGER_SCHEMA_UNSUPPORTED)
        # CONTROL: the supported version admits, so the refusal is the version.
        self.assertEqual(
            decision_gate.admit_head(
                self.policy, [red], run_id=RUN, expected_settled_round=None
            )["state"],
            "CLEAR",
        )


if __name__ == "__main__":
    unittest.main()
