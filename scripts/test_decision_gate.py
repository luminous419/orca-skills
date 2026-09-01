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


class VerificationAdmissionTests(PolicyMixin):
    """Final Adversarial Review F-001: the ONE dispatch an open blocking head admits.

    P6b row 2 permits the already-scheduled current-phase Reviewer to verify a
    blocking classification at MEDIUM/HIGH. A5 used to refuse it exactly as it
    refuses a correction Worker, so the permitted transition could not happen at all.
    The exception below is bound on six axes at once; every negative here differs
    from the ADMITTED positive in exactly one of them, and the positive is asserted
    in the same test as each negative so no refusal is vacuous.
    """

    WORKER_KEY = f"{RUN}/implementation/1/B2#1"

    def setUp(self) -> None:
        self.red = load_fixture("valid", "run_entry_declaration")
        self.open_worker = dict(load_fixture("valid", "worker_needs_input"), sequence=1)
        # A6 recomputes the run-entry declaration, so the open item must be declared
        # there or the ledger fails A6 before A5 is ever reached.
        self.red = dict(self.red, prior_open_decision_items=[self.WORKER_KEY])
        self.settled = (RUN, "implementation", 1)

    def dispatch(self, **kw) -> decision_gate.VerificationDispatch:
        fields = {
            "role": "reviewer",
            "phase": "implementation",
            "iteration": 1,
            "verifies": self.WORKER_KEY,
        }
        fields.update(kw)
        return decision_gate.VerificationDispatch(**fields)

    def admit(self, records=None, *, verification, expected=None):
        return decision_gate.admit_head(
            self.policy,
            self.ledger() if records is None else records,
            run_id=RUN,
            expected_settled_round=self.settled if expected is None else expected,
            verification=verification,
        )

    def ledger(self) -> list[dict]:
        return [self.red, self.open_worker]

    def test_the_bound_current_phase_reviewer_is_admitted_and_nothing_else_is(
        self,
    ) -> None:
        # POSITIVE: every binding holds, so A5 admits and returns the Worker head.
        head = self.admit(verification=self.dispatch())
        self.assertEqual(decision_gate.ledger_key(head), self.WORKER_KEY)
        self.assertEqual(head["state"], "NEEDS_INPUT")

        # CONTROL: the SAME ledger with NO verification offered still refuses, which
        # is what proves the admission came from the binding and not from the ledger.
        self.assertTrue(
            self.refusal(
                decision_gate.admit_head,
                self.policy,
                self.ledger(),
                run_id=RUN,
                expected_settled_round=self.settled,
            ).startswith(decision_gate.BLOCK_REASON_PREFIX)
        )

        # NEGATIVES: one changed axis each, and each stays a DECISION_BLOCKED refusal
        # rather than a different, softer error.
        for label, verification in {
            "a correction Worker": self.dispatch(role="worker"),
            "the Final Reviewer": self.dispatch(role="final_reviewer"),
            "another phase's Reviewer": self.dispatch(phase="test"),
            "another iteration's Reviewer": self.dispatch(iteration=2),
            "an unbound verification": self.dispatch(verifies=None),
            "a verification bound elsewhere": self.dispatch(
                verifies=f"{RUN}/implementation/9/B2#9"
            ),
        }.items():
            with self.subTest(case=label):
                self.assertTrue(
                    self.refusal(
                        self.admit, verification=verification
                    ).startswith(decision_gate.BLOCK_REASON_PREFIX)
                )

    def test_the_exception_relaxes_a5_and_no_other_clause(self) -> None:
        """A1-A4/A3/A6 are evaluated identically with a verification offered."""
        good = self.dispatch()
        for label, records, expected, reason in (
            ("A1 empty", [], self.settled, decision_gate.GATE_INPUT_MISSING),
            (
                "A2 two entries",
                [self.red, dict(self.red), self.open_worker],
                self.settled,
                decision_gate.LEDGER_INCONSISTENT,
            ),
            (
                "A4-iv gap",
                [self.red, dict(self.open_worker, sequence=2)],
                self.settled,
                decision_gate.GATE_INPUT_MALFORMED,
            ),
            (
                "A3 unbound head",
                self.ledger(),
                (RUN, "implementation", 7),
                decision_gate.GATE_INPUT_UNBOUND,
            ),
            (
                # A declaration that OVERCLAIMS -- it names an item the ledger does
                # not hold open -- is still the producer defect A6 exists to name.
                "A6 overclaimed",
                [
                    dict(
                        self.red,
                        prior_open_decision_items=sorted(
                            {self.WORKER_KEY, f"{RUN}/analysis/1/B2#4"}
                        ),
                    ),
                    self.open_worker,
                ],
                self.settled,
                decision_gate.DECLARATION_DISAGREES_WITH_LEDGER,
            ),
            (
                # ...and so is a SECOND open item alongside the head, because the
                # exception admits one verification of one classification, never a
                # dispatch past an item nobody is verifying.
                "A6 a second open item",
                [
                    dict(
                        self.red,
                        prior_open_decision_items=[f"{RUN}/analysis/1/B2#1"],
                    ),
                    dict(
                        load_fixture("valid", "worker_conflict"),
                        sequence=1,
                        phase="analysis",
                    ),
                    dict(self.open_worker, sequence=2),
                ],
                (RUN, "implementation", 1),
                decision_gate.DECLARATION_DISAGREES_WITH_LEDGER,
            ),
        ):
            with self.subTest(clause=label):
                self.assertEqual(
                    self.refusal(
                        self.admit, records, verification=good, expected=expected
                    ),
                    reason,
                )
        # CONTROL: the same `good` dispatch DOES admit the well-formed ledger, so the
        # refusals above are attributable to their clause and not to the binding.
        self.assertEqual(
            decision_gate.ledger_key(self.admit(verification=good)), self.WORKER_KEY
        )
        # ...and the shape a REAL run actually produces is admitted too: the
        # run-entry declaration is written once, at run open, and therefore claims
        # NOTHING about the item this run itself opened a moment ago. Without this
        # the permitted verification would be unreachable outside a hand-edited
        # ledger, which is the way F-001's own reproduction had to be staged.
        self.assertEqual(
            decision_gate.ledger_key(
                self.admit(
                    [dict(self.red, prior_open_decision_items=[]), self.open_worker],
                    verification=good,
                )
            ),
            self.WORKER_KEY,
        )
        # CONTROL for THAT: the same undeclared ledger with no verification offered
        # is still the A6 refusal it has always been.
        self.assertEqual(
            self.refusal(
                decision_gate.admit_head,
                self.policy,
                [dict(self.red, prior_open_decision_items=[]), self.open_worker],
                run_id=RUN,
                expected_settled_round=self.settled,
            ),
            decision_gate.DECLARATION_DISAGREES_WITH_LEDGER,
        )

    def test_the_head_itself_must_be_that_workers_open_blocking_record(self) -> None:
        good = self.dispatch()
        self.assertEqual(
            decision_gate.ledger_key(self.admit(verification=good)), self.WORKER_KEY
        )
        # A head that is a REVIEWER's B3 record is not a Worker classification, even
        # when it is itself open and blocking -- this is what stops a SECOND
        # verification Reviewer, and it is checked on the record, not on a counter.
        reviewer_head = dict(
            self.open_worker, role="reviewer", source="reviewer", boundary="B3"
        )
        self.assertTrue(
            self.refusal(
                self.admit,
                [
                    dict(
                        self.red,
                        prior_open_decision_items=[
                            decision_gate.ledger_key(reviewer_head)
                        ],
                    ),
                    reviewer_head,
                ],
                verification=self.dispatch(
                    verifies=decision_gate.ledger_key(reviewer_head)
                ),
            ).startswith(decision_gate.BLOCK_REASON_PREFIX)
        )

    def test_only_one_reviewer_may_verify_one_worker_classification(self) -> None:
        """The already-published verification closes the exception for that record."""
        verification_record = dict(
            load_fixture("valid", "worker_needs_input"),
            sequence=2,
            role="reviewer",
            source="reviewer",
            boundary="B3",
            iteration=1,
            verifies={
                "run": RUN,
                "phase": "implementation",
                "iteration": 1,
                "worker_record_key": self.WORKER_KEY,
            },
        )
        second_key = decision_gate.ledger_key(verification_record)
        records = [
            dict(
                self.red,
                prior_open_decision_items=sorted({self.WORKER_KEY, second_key}),
            ),
            self.open_worker,
            verification_record,
        ]

        # The head is now the Reviewer's record, and a second verification bound to
        # the SAME Worker record is refused on the already-verified conjunct too.
        self.assertIsNotNone(
            decision_gate.verification_admission_defect(
                records, self.open_worker, {self.WORKER_KEY}, self.dispatch(),
                run_id=RUN,
            )
        )
        # CONTROL: the identical call WITHOUT the published verification admits.
        self.assertIsNone(
            decision_gate.verification_admission_defect(
                self.ledger(), self.open_worker, {self.WORKER_KEY}, self.dispatch(),
                run_id=RUN,
            )
        )

    def test_a_verification_record_resolves_nothing_whatever_it_says(self) -> None:
        """L6: even an ACCEPTED downgrade is recorded and still blocks (resume is OS-31).

        Without this the Reviewer's own CLEAR would close the Worker's item and
        re-admit the correction Worker and the next phase at the next B1 -- exactly
        the illegal dispatch this ticket exists to prevent.
        """
        downgrade = dict(
            load_fixture("valid", "worker_clear"),
            sequence=2,
            role="reviewer",
            source="reviewer",
            boundary="B3",
            iteration=1,
            # NEEDS_INPUT -> CLEAR is `requires_user_decision`, so this is an
            # ACCEPTED downgrade under the shared contract -- the strongest case
            # there is, and it must STILL leave the item open.
            user_decision={
                "source": "explicit_user_reply",
                "where_recorded": f"artifacts/runs/{RUN}/DECISION.md",
                "resolves": self.WORKER_KEY,
            },
            verifies={
                "run": RUN,
                "phase": "implementation",
                "iteration": 1,
                "worker_record_key": self.WORKER_KEY,
            },
        )
        records = [self.red, self.open_worker, downgrade]

        self.assertEqual(
            decision_gate.open_items(self.policy, records), {self.WORKER_KEY}
        )
        # CONTROL: the SAME CLEAR record with no `verifies` reference IS a resolution
        # -- so the clause turns on the verification edge, not on the state.
        self.assertEqual(
            decision_gate.open_items(
                self.policy, [self.red, self.open_worker, dict(downgrade, verifies=None)]
            ),
            set(),
        )
        # ...and the next boundary after the verification is therefore still refused,
        # for a correction Worker AND for a second Reviewer bound to the same record.
        for label, verification in {
            "correction worker": None,
            "second reviewer": self.dispatch(),
        }.items():
            with self.subTest(case=label):
                self.assertTrue(
                    self.refusal(
                        decision_gate.admit_head,
                        self.policy,
                        [
                            dict(
                                self.red,
                                prior_open_decision_items=[self.WORKER_KEY],
                            ),
                            self.open_worker,
                            downgrade,
                        ],
                        run_id=RUN,
                        expected_settled_round=self.settled,
                        verification=verification,
                    ).startswith(decision_gate.BLOCK_REASON_PREFIX)
                )


class RiskIndependenceTests(PolicyMixin):
    """Scenario 7 P1: risk is INERT here, not merely absent.

    Mirrors test_decision_policy.py's own signature assertion for permitted_states,
    whose comment names this as the anti-vacuity move.
    """

    GATE_FUNCTIONS = (
        "parse_gate_result",
        "parse_declared_state",
        "declares_gate_result",
        "validate_gate_record",
        "validate_ledger_record",
        "admit_head",
        "verification_admission_defect",
        "evaluate_verification",
        "verification_binding_defect",
        "open_items",
        "block_reason",
        "decision_columns",
        "ledger_key",
        "unresolved_block_reason",
        "declaration_understatement_defect",
        "verification_record_defect",
        "record_identity",
        "record_identity_defect",
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

    def test_the_gate_function_list_is_every_public_function_in_the_module(
        self,
    ) -> None:
        """The closed list above must not silently fall behind the module.

        TEST iteration 3. `GATE_FUNCTIONS` is hand-written, so scenario 7's structural
        claim -- "no gate function takes a risk or profile parameter" -- is only as
        wide as whoever last edited it. Final Review F-001's fix added
        `verification_admission_defect()`, a new decision-authority function, and the
        list did not gain it: the assertion above was still green while the newest
        gate function went uninspected. This guard makes the next such addition a
        FAILURE here rather than a silent narrowing.
        """
        public = {
            name
            for name, value in vars(decision_gate).items()
            if inspect.isfunction(value)
            and not name.startswith("_")
            and value.__module__ == decision_gate.__name__
        }

        self.assertEqual(public, set(self.GATE_FUNCTIONS))
        # CONTROLS, so the equality above is a fact about the module and not about an
        # empty or over-wide set: the enumeration really found functions; the private
        # helpers are excluded; and `validate_transition`, which decision_gate imports
        # from decision_policy and calls, is visible in the module namespace but is
        # correctly NOT claimed as one of this module's own gate functions.
        self.assertGreaterEqual(len(public), 13)
        self.assertNotIn("_closed_field_defect", public)
        self.assertIn("validate_transition", vars(decision_gate))
        self.assertNotIn("validate_transition", public)

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


# =====================================================================================
# OS-29 TEST phase: the named fixtures PLAN P4 places in THIS module and had no case of
# their own after IMPLEMENTATION. Additive; nothing above was edited.
# =====================================================================================


class TimeoutAndNonResponseTests(PolicyMixin):
    """Scenario 11 at the contract level: a timeout is not an answer.

    The e2e half -- that the same refusal charges no correction iteration -- lives in
    test_e2e_harness.DecisionGateNamedScenarioTests, because "was an iteration
    charged" is a question about a transition and not about this parser.
    """

    def setUp(self) -> None:
        self.worker = decision_gate.GateResult(
            declared_state="NEEDS_INPUT",
            record=decision_half(load_fixture("valid", "worker_needs_input")),
        )

    def downgrade_offering(self, source: str) -> decision_gate.GateResult:
        """A Reviewer proposing NEEDS_INPUT -> CLEAR and naming `source` as the user
        authority for it. This is the shape a fail-open would actually take: not a
        missing record, but a present one whose authority is not authority."""
        record = dict(
            decision_half(load_fixture("valid", "worker_clear")),
            user_decision={
                "source": source,
                "where_recorded": f"artifacts/runs/{RUN}/DECISION.md",
                "resolves": f"{RUN}/implementation/1/B2#1",
            },
        )
        return decision_gate.GateResult(declared_state="CLEAR", record=record)

    def test_timeout_no_response_is_never_an_approval(self) -> None:
        forbidden = self.policy.forbidden_authority_sources
        # D4-F guard, before the loop: the closed set really is the five the contract
        # names, so the loop below is not iterating over a set that quietly shrank.
        self.assertEqual(len(forbidden), 5)
        self.assertEqual(
            forbidden,
            frozenset(
                {
                    "timeout",
                    "no_response",
                    "model_confidence",
                    "recommended_default",
                    "worker_reviewer_agreement",
                }
            ),
        )

        for source in sorted(forbidden):
            with self.subTest(source=source):
                self.assertNotIn(source, self.policy.user_decision_sources)
                outcome = decision_gate.evaluate_verification(
                    self.policy, self.worker, self.downgrade_offering(source)
                )
                self.assertEqual(outcome.reason, decision_gate.DOWNGRADE_REJECTED)
                # The Worker's classification survives the rejected downgrade.
                self.assertEqual(
                    outcome.block, ("NEEDS_INPUT", "blast_radius_beyond_scope")
                )

        # THE CONTROL, co-located: the SAME downgrade offered with a source the
        # contract DOES admit is decided differently -- the transition is accepted and
        # the round is still terminal (L6). Without this, DOWNGRADE_REJECTED above
        # would also be produced by a gate that rejects every downgrade whatsoever.
        for source in sorted(self.policy.user_decision_sources):
            with self.subTest(admitted_source=source):
                outcome = decision_gate.evaluate_verification(
                    self.policy, self.worker, self.downgrade_offering(source)
                )
                self.assertNotEqual(outcome.reason, decision_gate.DOWNGRADE_REJECTED)
                self.assertEqual(
                    outcome.reason,
                    decision_gate.block_reason(
                        "NEEDS_INPUT", "blast_radius_beyond_scope"
                    ),
                )

    def test_a_timeout_never_resolves_an_open_ledger_item(self) -> None:
        """A5's half of the same claim: waiting does not close an item either."""
        red = load_fixture("valid", "run_entry_declaration")
        open_record = load_fixture("valid", "worker_needs_input")
        open_key = decision_gate.ledger_key(open_record)
        timed_out = dict(
            load_fixture("valid", "worker_clear"),
            sequence=2,
            role="reviewer",
            boundary="B3",
            source="reviewer",
            user_decision={
                "source": "no_response",
                "where_recorded": f"artifacts/runs/{RUN}/DECISION.md",
                "resolves": open_key,
            },
        )

        self.assertEqual(
            decision_gate.open_items(self.policy, [red, open_record, timed_out]),
            {open_key},
        )
        # THE CONTROL: an admissible authority in the same position DOES resolve it,
        # so "still open" is a fact about the source and not about open_items().
        answered = dict(
            timed_out,
            user_decision=dict(timed_out["user_decision"], source="explicit_user_reply"),
        )
        self.assertEqual(
            decision_gate.open_items(self.policy, [red, open_record, answered]), set()
        )


class DownstreamExpansionTests(PolicyMixin):
    """Scenario 8's drift rule: an expansion is a NEW decision event, never a link."""

    def test_downstream_expands_decision_opens_a_new_item_and_links_nothing(
        self,
    ) -> None:
        red = load_fixture("valid", "run_entry_declaration")
        earlier = dict(
            load_fixture("valid", "worker_assumption_allowed"),
            phase="analysis",
            responsible_phase="analysis",
        )
        # POSITIVE `downstream_expands_decision`: a later phase widens the earlier
        # assumption past its declared scope, which is a NEW blocking item.
        expanded = dict(
            load_fixture("valid", "worker_conflict"),
            sequence=2,
            phase="implementation",
            responsible_phase="implementation",
        )

        opened = decision_gate.open_items(self.policy, [red, earlier, expanded])

        self.assertEqual(opened, {decision_gate.ledger_key(expanded)})
        # It is a NEW event and NOT a lineage edge: no record may name the decision it
        # widened. That boundary is OS-30's (L3 / R-10) and it is a CHECK here.
        for record in (earlier, expanded):
            for reserved in decision_gate.OS30_RESERVED_FIELDS:
                with self.subTest(record=record["phase"], field=reserved):
                    self.assertNotIn(reserved, record)
                    smuggled = dict(record, **{reserved: decision_gate.ledger_key(earlier)})
                    self.assertEqual(
                        self.refusal(
                            decision_gate.validate_ledger_record, self.policy, smuggled
                        ),
                        decision_gate.GATE_INPUT_MALFORMED,
                    )
        # NEGATIVE `downstream_within_original_decision`: the same later phase staying
        # inside the earlier decision opens nothing, so "a new item appeared" above is
        # attributable to the expansion and not to the presence of a second record.
        inside = dict(
            load_fixture("valid", "worker_clear"),
            sequence=2,
            phase="implementation",
            responsible_phase="implementation",
        )
        self.assertEqual(
            decision_gate.open_items(self.policy, [red, earlier, inside]), set()
        )


class MarkdownVersusMachineDriftTests(PolicyMixin):
    """P7's anti-drift requirement, on the exact defect this run produced.

    At ANALYSIS iteration 1 this run wrote, in Markdown, a line that reads as correct
    -- `REASON_CODE: (none - CLEAR carries no reason code)` -- beside a machine record
    that supplied that very string as the record's `reason_code`. The prose is fine;
    the record is not. A validator that reads only the prose passes it.
    """

    #: The historical string, byte-for-byte. Not paraphrased: the point of the
    #: fixture is that it is the real defect and not a constructed lookalike.
    F001_PROSE_LINE = "REASON_CODE: (none - CLEAR carries no reason code)"

    def test_the_iteration_one_reason_code_string_is_refused_by_the_gate(self) -> None:
        defective = decision_half(load_fixture("invalid", "clear_carries_a_reason_code"))
        # The fixture IS the historical defect, asserted rather than assumed.
        self.assertEqual(defective["state"], "CLEAR")
        self.assertEqual(
            defective["reason_code"], "(none - CLEAR carries no reason code)"
        )

        body = gate_body("CLEAR", defective, narrative=False)
        body = body.replace(
            "STATUS: COMPLETE",
            "STATUS: COMPLETE\n\n## Decision Record (optional)\n\n"
            "DECISION_STATE: CLEAR\n" + self.F001_PROSE_LINE,
        )
        # The human half of the document reads exactly as it did in iteration 1 ...
        self.assertIn(self.F001_PROSE_LINE, body)
        self.assertIn("DECISION_STATE: CLEAR", body)
        # ... and the document is still refused, on the machine record.
        self.assertEqual(
            self.refusal(decision_gate.parse_gate_result, body, self.policy),
            decision_gate.GATE_INPUT_MALFORMED,
        )

        # THE CONTROL, co-located and differing in exactly one field: the SAME
        # document with `reason_code: null` -- the correction this run actually made
        # -- is ADMITTED, prose unchanged. So the refusal is attributable to the
        # record's reason_code and not to the prose, to the section, or to the state.
        corrected = dict(defective, reason_code=None)
        control = gate_body("CLEAR", corrected, narrative=False).replace(
            "STATUS: COMPLETE",
            "STATUS: COMPLETE\n\n## Decision Record (optional)\n\n"
            "DECISION_STATE: CLEAR\n" + self.F001_PROSE_LINE,
        )
        self.assertIn(self.F001_PROSE_LINE, control)
        self.assertEqual(
            decision_gate.parse_gate_result(control, self.policy).state, "CLEAR"
        )
        self.assertIsNone(
            decision_gate.parse_gate_result(control, self.policy).reason_code
        )

    def test_the_same_string_is_refused_as_a_ledger_record(self) -> None:
        """A4-iii: the defect is refused when the record is read BACK off the ledger
        too, not only when it is parsed out of an agent result."""
        defective = load_fixture("invalid", "clear_carries_a_reason_code")

        self.assertEqual(
            self.refusal(
                decision_gate.validate_ledger_record, self.policy, defective
            ),
            decision_gate.GATE_INPUT_MALFORMED,
        )
        # CONTROL: the same record with a null reason code validates.
        decision_gate.validate_ledger_record(
            self.policy, dict(defective, reason_code=None)
        )


class UnresolvedBlockReasonTests(PolicyMixin):
    """External re-review MAJOR: a terminal boundary must name the BLOCK.

    admit_head() evaluates A6 before A5, and A6 disagrees for every item the run
    itself opened, so the clause that fires first is the producer defect rather than
    the block. That ordering is harmless on a dispatch boundary -- the caller is
    refused either way -- but at the run's LAST boundary the refusal reason IS the
    recorded terminal classification, so it has to be the real state and reason code.
    """

    A6 = decision_gate.DECLARATION_DISAGREES_WITH_LEDGER

    def entry(self, **overrides) -> dict:
        record = load_fixture("valid", "run_entry_declaration")
        record.update(overrides)
        record.setdefault("run", RUN)
        return record

    def blocking(self, name: str, sequence: int = 1) -> dict:
        record = load_fixture("valid", name)
        record.update({"run": RUN, "sequence": sequence})
        return record

    def test_a_valid_open_block_is_named_with_its_state_and_reason_code(self) -> None:
        for name, state, code in (
            ("worker_needs_input", "NEEDS_INPUT", "blast_radius_beyond_scope"),
            ("worker_conflict", "CONFLICT", "requirement_contradiction"),
        ):
            with self.subTest(name=name):
                records = [self.entry(), self.blocking(name)]

                reason = decision_gate.unresolved_block_reason(
                    self.policy, records, refused_with=self.A6
                )

                self.assertEqual(reason, f"DECISION_BLOCKED:{state}:{code}")
                self.assertEqual(
                    decision_gate.decision_columns(reason), (state, code)
                )

    def test_a_clear_ledger_names_no_block(self) -> None:
        """The control: reclassification only fires when something is actually open."""
        records = [self.entry(), self.blocking("worker_clear")]

        self.assertIsNone(
            decision_gate.unresolved_block_reason(
                    self.policy, records, refused_with=self.A6
                )
        )

    def test_an_empty_ledger_names_no_block(self) -> None:
        self.assertIsNone(decision_gate.unresolved_block_reason(
            self.policy, [], refused_with=self.A6
        ))

    def test_an_invalid_record_is_never_laundered_into_a_valid_block(self) -> None:
        """The property that keeps this from becoming a fail-open path.

        A ledger that cannot be read is a genuine INPUT defect, and the caller must
        keep its own producer reason. If this returned a block for a malformed
        ledger, a defective record would be reported as a well-formed user decision.
        """
        broken = self.blocking("worker_needs_input")
        broken.pop("reason_code")
        records = [self.entry(), broken]

        self.assertIsNone(
            decision_gate.unresolved_block_reason(
                    self.policy, records, refused_with=self.A6
                )
        )

    def test_an_unsupported_ledger_schema_is_never_laundered_either(self) -> None:
        record = self.blocking("worker_needs_input")
        record["ledger_schema_version"] = (
            decision_gate.LEDGER_RECORD_SCHEMA_VERSION + 99
        )

        self.assertIsNone(
            decision_gate.unresolved_block_reason(
                self.policy, [self.entry(), record], refused_with=self.A6
            )
        )

    def test_only_the_a6_refusal_is_ever_reclassified(self) -> None:
        """The laundering guard. A ledger can be invalid AS A LEDGER while still
        holding individually valid blocking records, so every refusal except A6 --
        the one whose position in A1 -> A2 -> A4 -> A3 -> A6 proves the
        ledger-level clauses passed -- must be preserved unchanged."""
        records = [self.entry(), self.blocking("worker_needs_input")]
        # Positive control first: with A6 this ledger DOES reclassify, so the
        # negatives below fail for the reason under test and not by accident.
        self.assertEqual(
            decision_gate.unresolved_block_reason(
                self.policy, records, refused_with=self.A6
            ),
            "DECISION_BLOCKED:NEEDS_INPUT:blast_radius_beyond_scope",
        )

        for reason in decision_gate.GATE_REFUSAL_REASONS:
            if reason == self.A6:
                continue
            with self.subTest(refused_with=reason):
                self.assertIsNone(
                    decision_gate.unresolved_block_reason(
                        self.policy, records, refused_with=reason
                    )
                )

    def test_a_structurally_invalid_ledger_keeps_its_own_defect(self) -> None:
        """The three shapes the re-review named. Each holds a VALID blocking record,
        so only the refusal reason can tell them apart from a real block."""
        duplicate = [
            self.entry(),
            self.blocking("worker_needs_input", sequence=1),
            self.blocking("worker_conflict", sequence=1),  # same sequence twice
        ]
        gapped = [self.entry(), self.blocking("worker_needs_input", sequence=7)]
        wrong_run = [self.entry(), self.blocking("worker_needs_input")]
        wrong_run[1]["run"] = "run_someone_elses"

        for name, records, refused_with in (
            ("duplicate", duplicate, decision_gate.GATE_INPUT_MALFORMED),
            ("gapped", gapped, decision_gate.GATE_INPUT_MALFORMED),
            ("wrong_run", wrong_run, decision_gate.GATE_INPUT_UNBOUND),
        ):
            with self.subTest(shape=name):
                self.assertIsNone(
                    decision_gate.unresolved_block_reason(
                        self.policy, records, refused_with=refused_with
                    )
                )

    def test_an_overstated_declaration_stays_a_producer_defect(self) -> None:
        """A6 exists to name a declaration that disagrees with the ledger. A block
        alongside a PHANTOM claim is still that defect: `refused_with == A6` proves
        the structural clauses passed, not that the mismatch is the ordinary
        understatement (external re-review MAJOR)."""
        records = [
            self.entry(prior_open_decision_items=["phantom"]),
            self.blocking("worker_needs_input"),
        ]

        self.assertIsNone(
            decision_gate.unresolved_block_reason(
                self.policy, records, refused_with=self.A6
            )
        )

    def test_multiple_unrelated_open_items_stay_a_producer_defect(self) -> None:
        """The verification exception requires the ENTIRE discrepancy to be one bound
        item; a terminal reclassification may not be looser than the admission."""
        records = [
            self.entry(),
            self.blocking("worker_needs_input", sequence=1),
            self.blocking("worker_conflict", sequence=2),
        ]

        self.assertIsNone(
            decision_gate.unresolved_block_reason(
                self.policy, records, refused_with=self.A6
            )
        )

    def test_the_ordinary_single_understated_block_is_still_reclassified(self) -> None:
        """The positive control for the two negatives above: the permitted shape --
        one open item, it is the head, nothing overstated -- still reclassifies."""
        records = [self.entry(), self.blocking("worker_needs_input")]

        self.assertEqual(
            decision_gate.unresolved_block_reason(
                self.policy, records, refused_with=self.A6
            ),
            "DECISION_BLOCKED:NEEDS_INPUT:blast_radius_beyond_scope",
        )

    def test_the_shape_predicate_names_each_rejected_shape(self) -> None:
        head = self.blocking("worker_needs_input")
        key = decision_gate.ledger_key(head)

        self.assertIsNone(
            decision_gate.declaration_understatement_defect(set(), {key}, head)
        )
        self.assertIn(
            "not open",
            decision_gate.declaration_understatement_defect(
                {"phantom"}, {key}, head
            ),
        )
        self.assertIn(
            "not exactly the head",
            decision_gate.declaration_understatement_defect(
                set(), {key, "other"}, head
            ),
        )

    def test_a_forged_direct_terminal_identity_is_rejected_centrally(self) -> None:
        """Shape A's counterpart of the shape-B identity finding. A B2 record wearing
        a reviewer identity, or a B3 wearing a worker one, is schema-valid field by
        field and used to be laundered into a normal DECISION_BLOCKED terminal. It is
        now refused by validate_ledger_record(), so no classifier ever sees it."""
        for label, fields in (
            ("B2 with reviewer identity", {"source": "reviewer", "role": "reviewer"}),
            ("B3 with worker identity",
             {"boundary": "B3", "source": "worker", "role": "worker"}),
            ("mixed source", {"source": "reviewer"}),
            ("mixed role", {"role": "reviewer"}),
        ):
            with self.subTest(identity=label):
                forged = self.blocking("worker_needs_input")
                forged.update(fields)

                self.assertIsNotNone(decision_gate.record_identity_defect(forged))
                with self.assertRaises(decision_gate.GateRefusal) as caught:
                    decision_gate.validate_ledger_record(self.policy, forged)
                self.assertEqual(
                    caught.exception.reason, decision_gate.GATE_INPUT_MALFORMED
                )
                # ...and the classifier therefore refuses to name it a block.
                self.assertIsNone(
                    decision_gate.unresolved_block_reason(
                        self.policy, [self.entry(), forged], refused_with=self.A6
                    )
                )

    def test_only_a_reviewer_b3_record_may_claim_a_verification(self) -> None:
        """`verifies` is a claim a Worker never makes."""
        worker = self.blocking("worker_needs_input")
        worker["verifies"] = {
            "run": RUN, "phase": "implementation", "iteration": 1,
            "worker_record_key": "k",
        }

        defect = decision_gate.record_identity_defect(worker)

        self.assertIsNotNone(defect)
        self.assertIn("verifies", defect)

    def test_the_three_legitimate_identities_are_accepted(self) -> None:
        """The control: central validation narrows the schema, it does not break the
        identities the publisher actually stamps."""
        self.assertIsNone(decision_gate.record_identity_defect(self.entry()))
        self.assertIsNone(
            decision_gate.record_identity_defect(self.blocking("worker_needs_input"))
        )
        reviewer = self.blocking("worker_needs_input")
        reviewer.update(boundary="B3", source="reviewer", role="reviewer")
        self.assertIsNone(decision_gate.record_identity_defect(reviewer))

    def test_it_admits_nothing_and_takes_no_risk_parameter(self) -> None:
        """It reclassifies an existing refusal; it is never a second admission path,
        and like every other gate predicate it cannot read a risk level."""
        signature = inspect.signature(decision_gate.unresolved_block_reason)

        self.assertEqual(
            list(signature.parameters), ["policy", "records", "refused_with"]
        )


if __name__ == "__main__":
    unittest.main()
