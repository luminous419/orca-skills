#!/usr/bin/env python3
"""Tests for the OS-28 decision policy contract (DESIGN D4).

Anti-vacuity rule (DESIGN D4-F): every data-driven loop asserts its collection's
expected cardinality INSIDE the same test function, before the loop. A guard in a
separate test can be deleted or skipped independently of the loop it protects; a
co-located guard cannot. Six loops in this file carry such a guard, each marked
`# D4-F guard`.
"""

from __future__ import annotations

import inspect
import json
import unittest
from pathlib import Path

from scripts.decision_policy import (
    AXIS_TOKENS,
    CANONICAL_INDEPENDENT_AXES,
    DECISION_STATES,
    DECLARATIVE_KEYS,
    STATE_SELECTION_INPUTS,
    TRANSITION_VALUES,
    WORKFLOW_VALUES,
    DecisionPolicyError,
    codes_for_state,
    load_decision_policy,
    parse_decision_policy,
    permitted_states,
    validate_record,
    validate_transition,
)
from scripts.skill_policy import load_policy_contract

REPO_ROOT = Path(__file__).resolve().parents[1]
SKILL_NAMES = ("orca-worker-reviewer-loop", "orca-worker-reviewer-orchestration")
FIXTURES = REPO_ROOT / "scripts" / "fixtures" / "decision_policy"

EXPECTED_CODE_COUNT = 18  # UD-4: 17 ANALYSIS-confirmed + unclassifiable_decision (OQ-5(c))
EXPECTED_PER_STATE = {"ASSUMPTION_ALLOWED": 4, "NEEDS_INPUT": 11, "CONFLICT": 3}
EXPECTED_FORBIDDEN_CELLS = {
    ("NEEDS_INPUT", "ASSUMPTION_ALLOWED"),
    ("CONFLICT", "ASSUMPTION_ALLOWED"),
}
EXPECTED_EVIDENCE_COUNTS = {"ASSUMPTION_ALLOWED": 5, "NEEDS_INPUT": 4, "CONFLICT": 3}
EXPECTED_REJECT_LIST = {
    "model_confidence",
    "timeout",
    "no_response",
    "worker_reviewer_agreement",
    "recommended_default",
}
EXPECTED_HIGH_IMPACT = [
    "monetary_cost",
    "security",
    "privacy",
    "compliance",
    "long_term_lock_in",
]


def load_fixture(relative: str) -> dict:
    return json.loads((FIXTURES / relative).read_text(encoding="utf-8"))


class DecisionPolicyTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.policy = load_decision_policy(
            REPO_ROOT / "orca-worker-reviewer-orchestration" / "SKILL.md"
        )
        cls.block = load_policy_contract(
            REPO_ROOT / "orca-worker-reviewer-orchestration" / "SKILL.md"
        )["decision_policy"]


class Requirement1StateVocabulary(DecisionPolicyTestCase):
    def test_contract_states_are_exactly_the_four(self) -> None:
        self.assertEqual(set(self.policy.states), set(DECISION_STATES))

    def test_state_outside_the_four_is_rejected(self) -> None:
        record = load_fixture("invalid/schema/fifth_state.json")
        with self.assertRaises(DecisionPolicyError):
            validate_record(self.policy, record)

    def test_a_fifth_state_in_the_contract_is_rejected(self) -> None:
        mutated = json.loads(json.dumps(self.block))
        mutated["states"]["DEFERRED"] = {
            "workflow": "continue",
            "user_decision_required": False,
            "reason_code_required": False,
        }
        with self.assertRaises(DecisionPolicyError):
            parse_decision_policy(mutated)


class Requirement2Transitions(DecisionPolicyTestCase):
    def test_forbidden_transitions_are_rejected(self) -> None:
        forbidden = {
            pair for pair, rule in self.policy.transitions.items() if rule == "forbidden"
        }
        # D4-F guard: co-located, so emptying the contract cannot silently skip the loop.
        self.assertEqual(forbidden, EXPECTED_FORBIDDEN_CELLS)
        for source, target in sorted(forbidden):
            with self.subTest(transition=f"{source}->{target}"):
                with self.assertRaises(DecisionPolicyError):
                    validate_transition(self.policy, source, target, {})

    def test_needs_input_to_assumption_allowed_is_forbidden_even_with_a_user_decision(
        self,
    ) -> None:
        """T-F2: the highest-value rule. A user_decision does NOT enable this edge --
        an answered question yields a decision, so the item goes to CLEAR instead."""
        case = load_fixture("invalid/transition/tf2_with_user_decision.json")
        with self.assertRaises(DecisionPolicyError):
            validate_transition(self.policy, case["from"], case["to"], case["record"])

    def test_conflict_to_assumption_allowed_is_forbidden_even_with_a_user_decision(
        self,
    ) -> None:
        case = load_fixture("invalid/transition/tf3_with_user_decision.json")
        with self.assertRaises(DecisionPolicyError):
            validate_transition(self.policy, case["from"], case["to"], case["record"])

    def test_needs_input_to_clear_requires_a_user_decision(self) -> None:
        case = load_fixture("invalid/transition/tf1_no_decision.json")
        with self.assertRaises(DecisionPolicyError):
            validate_transition(self.policy, case["from"], case["to"], case["record"])

    def test_conflict_to_clear_requires_a_user_decision(self) -> None:
        case = load_fixture("invalid/transition/tf4_no_decision.json")
        with self.assertRaises(DecisionPolicyError):
            validate_transition(self.policy, case["from"], case["to"], case["record"])

    def test_assumption_allowed_to_clear_requires_a_retraction(self) -> None:
        case = load_fixture("invalid/transition/aa_to_clear_no_retraction.json")
        with self.assertRaises(DecisionPolicyError):
            validate_transition(self.policy, case["from"], case["to"], case["record"])

    def test_an_incomplete_user_decision_does_not_satisfy_the_edge(self) -> None:
        case = load_fixture("invalid/transition/tf6_downstream.json")
        with self.assertRaises(DecisionPolicyError):
            validate_transition(self.policy, case["from"], case["to"], case["record"])

    def test_a_valid_user_decision_permits_needs_input_to_clear(self) -> None:
        record = {
            "user_decision": {
                "source": "explicit_user_reply",
                "where_recorded": "USER_DECISIONS.md#UD-1",
                "resolves": "the open item",
            }
        }
        validate_transition(self.policy, "NEEDS_INPUT", "CLEAR", record)

    def test_the_downstream_rule_is_stated_in_the_contract(self) -> None:
        self.assertIn("may not be reported CLEAR", self.policy.downstream_rule)


class Requirement3ReasonCodesAndEvidence(DecisionPolicyTestCase):
    def test_reason_code_is_required_for_each_non_clear_state(self) -> None:
        states = [s for s in DECISION_STATES if self.policy.states[s].reason_code_required]
        # D4-F guard
        self.assertEqual(len(states), 3)
        for state in states:
            with self.subTest(state=state):
                with self.assertRaises(DecisionPolicyError):
                    validate_record(self.policy, load_fixture(f"invalid/evidence/no_reason_{state}.json"))

    def test_reason_code_outside_the_closed_set_is_rejected(self) -> None:
        with self.assertRaises(DecisionPolicyError):
            validate_record(self.policy, load_fixture("invalid/evidence/unknown_code.json"))

    def test_clear_must_not_carry_a_reason_code(self) -> None:
        with self.assertRaises(DecisionPolicyError):
            validate_record(self.policy, {"state": "CLEAR", "reason_code": "repository_policy"})

    def test_each_required_evidence_field_is_enforced(self) -> None:
        counts = {
            state: len(fields)
            for state, fields in self.policy.required_evidence.items()
            if fields
        }
        # D4-F guard
        self.assertEqual(counts, EXPECTED_EVIDENCE_COUNTS)
        checked = 0
        for state, fields in sorted(self.policy.required_evidence.items()):
            if not fields:
                continue
            code = codes_for_state(self.policy, state)[0]
            base = load_fixture(f"valid/{code}.json")
            for field in fields:
                if field == "reason_code":
                    continue
                with self.subTest(state=state, field=field):
                    broken = {k: v for k, v in base.items() if k != field}
                    checked += 1
                    with self.assertRaises(DecisionPolicyError):
                        validate_record(self.policy, broken)
        self.assertEqual(checked, sum(EXPECTED_EVIDENCE_COUNTS.values()) - 3)

    def test_an_empty_required_field_is_rejected_like_a_missing_one(self) -> None:
        record = load_fixture("valid/unclassifiable_decision.json")
        record["classification_attempted"] = []
        with self.assertRaises(DecisionPolicyError):
            validate_record(self.policy, record)


class Requirement4HighImpactIsNotWeakened(DecisionPolicyTestCase):
    def test_high_impact_irreversible_cannot_be_assumption_allowed(self) -> None:
        record = load_fixture("invalid/inv4/irreversible_repository.json")
        with self.assertRaises(DecisionPolicyError):
            validate_record(self.policy, record)
        self.assertNotIn("ASSUMPTION_ALLOWED", permitted_states(self.policy, record))

    def test_inv4_is_not_lifted_by_a_determining_policy_source(self) -> None:
        record = load_fixture("invalid/inv4/irreversible_repository_with_determining_policy.json")
        with self.assertRaises(DecisionPolicyError):
            validate_record(self.policy, record)

    def test_inv4_is_not_lifted_by_a_user_decision(self) -> None:
        record = load_fixture("invalid/inv4/irreversible_repository_with_user_decision.json")
        with self.assertRaises(DecisionPolicyError):
            validate_record(self.policy, record)

    def test_each_high_impact_element_alone_forbids_assumption_allowed(self) -> None:
        elements = list(self.policy.assumption_allowed_forbidden_when["any_true_of"])
        # D4-F guard
        self.assertEqual(elements, EXPECTED_HIGH_IMPACT)
        for element in elements:
            with self.subTest(element=element):
                record = load_fixture(f"invalid/inv4/{element}.json")
                with self.assertRaises(DecisionPolicyError):
                    validate_record(self.policy, record)

    def test_inv4_declares_no_exception(self) -> None:
        self.assertIs(
            self.policy.assumption_allowed_forbidden_when["exception_allowed"], False
        )


class Requirement5SafeItemIsPermitted(DecisionPolicyTestCase):
    def test_a_safe_reversible_item_is_permitted_to_be_assumption_allowed(self) -> None:
        """UD-2: PERMISSION LEVEL ONLY. This asserts the contract PERMITS
        ASSUMPTION_ALLOWED for a safe, reversible, scope-local item. It does NOT and
        cannot show that a real model produces that state -- a contract-level test
        cannot detect a model's over-escalation. That belongs to OS-32 and is not
        claimed here."""
        record = load_fixture("valid/repository_policy.json")
        validate_record(self.policy, record)
        self.assertIn("ASSUMPTION_ALLOWED", permitted_states(self.policy, record))

    def test_the_contract_does_not_require_needs_input_for_a_safe_item(self) -> None:
        """UD-2: permission level only -- see the sibling test's docstring."""
        record = load_fixture("valid/repository_policy.json")
        permitted = permitted_states(self.policy, record)
        self.assertNotEqual(permitted, frozenset({"NEEDS_INPUT"}))
        self.assertIn("CLEAR", permitted)


class Requirement6ConfidenceIsNeverAuthority(DecisionPolicyTestCase):
    def test_forbidden_authority_list_is_exactly_five_entries(self) -> None:
        self.assertEqual(set(self.policy.forbidden_authority_sources), EXPECTED_REJECT_LIST)

    def test_forbidden_authority_source_cannot_justify_a_transition(self) -> None:
        sources = sorted(self.policy.forbidden_authority_sources)
        # D4-F guard
        self.assertEqual(set(sources), EXPECTED_REJECT_LIST)
        for source in sources:
            with self.subTest(source=source):
                case = load_fixture(f"invalid/authority/{source}.json")
                with self.assertRaises(DecisionPolicyError):
                    validate_transition(
                        self.policy, case["from"], case["to"], case["record"]
                    )


class Requirement7RiskIndependence(DecisionPolicyTestCase):
    """DESIGN D4-G. Replaces iteration 1's single vacuous test, which iterated risk
    strings that reached no function argument."""

    def test_every_contract_key_is_classified_as_input_or_declarative(self) -> None:
        """7.1 / R-A2. The completeness guarantee: an unclassified key fails, so the
        enumeration cannot silently become incomplete."""
        self.assertEqual(set(self.block), STATE_SELECTION_INPUTS | DECLARATIVE_KEYS)
        self.assertFalse(STATE_SELECTION_INPUTS & DECLARATIVE_KEYS)

    def test_no_axis_token_appears_in_a_state_selection_input(self) -> None:
        """7.2 / R-A3. Exact-token, not substring: `quality_attribute_id` and
        `long_term_lock_in` must stay legal."""
        hits = []

        def walk(node, path):
            if isinstance(node, dict):
                for key, value in node.items():
                    if key in AXIS_TOKENS:
                        hits.append(f"{path}/{key} (key)")
                    walk(value, f"{path}/{key}")
            elif isinstance(node, list):
                for index, value in enumerate(node):
                    walk(value, f"{path}[{index}]")
            elif isinstance(node, str) and node in AXIS_TOKENS:
                hits.append(f"{path} (value)")

        # D4-F guard: the walk must actually visit every selection input.
        self.assertEqual(len(STATE_SELECTION_INPUTS & set(self.block)), 16)
        for key in sorted(STATE_SELECTION_INPUTS):
            walk(self.block[key], key)
        self.assertEqual(hits, [])

    def test_enumerated_positions_use_only_closed_values(self) -> None:
        """7.3 / R-A4. Catches a risk-conditional value string that contains no exact
        axis token -- mutation M-17, which R-A3 misses."""
        for source, row in self.block["transitions"].items():
            for target, rule in row.items():
                self.assertIn(rule, TRANSITION_VALUES, f"transitions[{source}][{target}]")
        for name, spec in self.block["states"].items():
            self.assertIn(spec["workflow"], WORKFLOW_VALUES, f"states[{name}].workflow")

    def test_permitted_states_signature_has_no_risk_parameter(self) -> None:
        """7.4. Adding a risk parameter later fails a test instead of passing silently."""
        parameters = set(inspect.signature(permitted_states).parameters)
        self.assertEqual(parameters, {"policy", "facts"})
        self.assertFalse(parameters & AXIS_TOKENS)

    def test_a_risk_fact_does_not_change_permitted_states(self) -> None:
        """7.5. Four calls with genuinely different `facts` mappings. The baseline
        comparison is the anti-vacuity move: it proves risk is INERT, not merely
        consistently consulted. If permitted_states branched on facts["risk"] at all,
        at least one call would diverge."""
        base = load_fixture("valid/repository_policy.json")
        baseline = permitted_states(self.policy, base)
        results = {}
        for level in ("low", "medium", "high"):
            results[level] = permitted_states(self.policy, {**base, "risk": level})
        self.assertEqual(len(set(results.values())), 1)
        for level, value in results.items():
            with self.subTest(risk=level):
                self.assertEqual(value, baseline)

    def test_a_risk_fact_does_not_change_a_high_impact_verdict_either(self) -> None:
        base = load_fixture("invalid/inv4/irreversible_repository.json")
        baseline = permitted_states(self.policy, base)
        for level in ("low", "medium", "high"):
            with self.subTest(risk=level):
                self.assertEqual(
                    permitted_states(self.policy, {**base, "risk": level}), baseline
                )
                self.assertNotIn("ASSUMPTION_ALLOWED", baseline)

    def test_independent_axes_names_exactly_the_three_canonical_axes(self) -> None:
        """7.x / R-B. A positive equality, so the declarative position cannot forbid
        itself -- which is what made iteration 1's C11 fail the correct contract."""
        self.assertEqual(self.policy.independent_axes, CANONICAL_INDEPENDENT_AXES)


class Requirement9FailClosedSchema(DecisionPolicyTestCase):
    def test_unknown_schema_version_raises(self) -> None:
        with self.assertRaises(DecisionPolicyError):
            parse_decision_policy(load_fixture("invalid/schema/version_99.json"))

    def test_missing_schema_version_raises(self) -> None:
        with self.assertRaises(DecisionPolicyError):
            parse_decision_policy(load_fixture("invalid/schema/no_version.json"))

    def test_malformed_contract_raises_and_does_not_return_none(self) -> None:
        """R-5 / PLAN V3 guard: this loader must NOT copy load_risk_contract's
        return-None convention, which its caller reads as 'no axis' -- a fail-open."""
        malformed = load_fixture("invalid/schema/malformed.json")
        with self.assertRaises(DecisionPolicyError):
            parse_decision_policy(malformed)

    def test_a_non_object_block_raises(self) -> None:
        with self.assertRaises(DecisionPolicyError):
            parse_decision_policy("not-an-object")

    def test_skill_policy_source_declares_no_schema_version_gate(self) -> None:
        """UD-3, SOURCE SCOPE ONLY -- renamed for RI-N1.

        The previous name promised a behavioural guarantee this body does not give:
        it reads source text and checks for token absence, nothing more. Keeping the
        old name would be exactly the "claim wider than the evidence" defect this run
        kept hitting. The behavioural half is the characterization test below.
        """
        import scripts.skill_policy as skill_policy

        source = Path(skill_policy.__file__).read_text(encoding="utf-8")
        self.assertNotIn("SUPPORTED_SCHEMA_VERSIONS", source)
        self.assertNotIn("schema_version", source)

    def test_evaluate_invocation_still_accepts_an_unknown_top_level_schema_version(
        self,
    ) -> None:
        """UD-3 characterization -- added for RI-N1. EXECUTES the shipped path.

        This pins a PRE-EXISTING DEFECT's behaviour on purpose: evaluate_invocation()
        has no schema_version gate, so a contract declaring an unsupported top-level
        version still evaluates normally. UD-3 puts fixing that out of scope, so this
        test asserts the defect is UNCHANGED -- neither fixed nor worsened by OS-28.

        If a later change adds the gate, this test fails. That failure is the SIGNAL,
        not a bug in the test: whoever adds the gate owns updating this test, and the
        follow-up ticket for the defect is where that belongs. Do not "fix" it by
        weakening the assertion.

        Note the asymmetry this makes concrete: the same malformed-version input that
        this shipped path accepts is REJECTED by the new decision-policy loader
        (test_unknown_schema_version_raises). That contrast is requirement 9's scope.
        """
        import shutil
        import tempfile

        from scripts.skill_policy import evaluate_invocation

        source_skill = REPO_ROOT / "orca-worker-reviewer-orchestration"
        with tempfile.TemporaryDirectory() as raw:
            temporary = Path(raw)
            skill_dir = temporary / source_skill.name
            skill_dir.mkdir()
            text = (source_skill / "SKILL.md").read_text(encoding="utf-8")
            # The TOP-LEVEL schema_version is the two-space-indented one; the
            # decision_policy block's own key is indented four. Replacing only the
            # first keeps this test about the pre-existing path.
            marker = '\n  "schema_version": 1,\n'
            self.assertEqual(text.count(marker), 1)
            (skill_dir / "SKILL.md").write_text(
                text.replace(marker, '\n  "schema_version": 99,\n', 1), encoding="utf-8"
            )

            binaries = temporary / "bin"
            binaries.mkdir()
            for command in ("claude-glm", "claude-gemma"):
                executable = binaries / command
                executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
                executable.chmod(0o755)

            decision = evaluate_invocation(
                skill_dir / "SKILL.md",
                f"/{source_skill.name} analysis for the login module",
                project_root=temporary,
                home=temporary,
                which=lambda command: shutil.which(command, path=str(binaries)),
            )

        self.assertEqual(decision.status, "VALID")
        self.assertTrue(decision.should_execute)


class ReasonCodeLiveness(DecisionPolicyTestCase):
    """DESIGN D4-B. RA-4 was an evidence failure and RA-5 an entry-condition failure,
    and a rejection-only suite would have passed with both defects in place."""

    def test_reason_code_count_is_eighteen(self) -> None:
        self.assertEqual(len(self.policy.reason_codes), EXPECTED_CODE_COUNT)
        per_state = {
            state: len(codes_for_state(self.policy, state))
            for state in EXPECTED_PER_STATE
        }
        self.assertEqual(per_state, EXPECTED_PER_STATE)

    def test_every_reason_code_has_exactly_one_fixture(self) -> None:
        """Bidirectional: a fixture with no code fails too, so a stale fixture cannot
        linger after a code is removed."""
        on_disk = {path.stem for path in (FIXTURES / "valid").glob("*.json")}
        self.assertEqual(on_disk, set(self.policy.reason_codes))

    def test_every_reason_code_has_a_constructible_record(self) -> None:
        """C1 entry / C2 evidence / C3 invariants, positively, for all 18 codes."""
        # D4-F guard
        self.assertEqual(len(self.policy.reason_codes), EXPECTED_CODE_COUNT)
        for name, code in sorted(self.policy.reason_codes.items()):
            with self.subTest(reason_code=name):
                record = load_fixture(f"valid/{name}.json")
                # C1 entry: the record's state matches, and the clause the contract
                # assigns this code exists in that state's entry_clauses.
                self.assertEqual(record["state"], code.state)
                if code.state in self.policy.entry_clauses:
                    self.assertIn(code.clause, self.policy.entry_clauses[code.state])
                else:
                    self.assertIsNone(code.clause)
                # C2 evidence: every effective required field is present and non-empty.
                for field in code.required_evidence:
                    self.assertIn(field, record, f"{name} missing {field}")
                    self.assertTrue(record[field], f"{name} has empty {field}")
                if code.boundary_element is not None:
                    self.assertEqual(record["boundary_element"], code.boundary_element)
                # C3 invariants: no INV-3/4/5 violation.
                validate_record(self.policy, record)


class BothSkillsAgree(DecisionPolicyTestCase):
    def test_both_skills_carry_an_identical_decision_policy(self) -> None:
        blocks = [
            load_policy_contract(REPO_ROOT / name / "SKILL.md")["decision_policy"]
            for name in SKILL_NAMES
        ]
        self.assertEqual(blocks[0], blocks[1])

    def test_both_skills_load_to_an_equal_policy(self) -> None:
        policies = [
            load_decision_policy(REPO_ROOT / name / "SKILL.md") for name in SKILL_NAMES
        ]
        self.assertEqual(policies[0].reason_codes, policies[1].reason_codes)
        self.assertEqual(policies[0].transitions, policies[1].transitions)


class OptionalDecisionRecord(DecisionPolicyTestCase):
    def test_absence_of_the_decision_record_section_is_valid(self) -> None:
        """UD-1: the section is OPTIONAL. Its absence is not a contract violation, and
        the format is validated only when the section is present."""
        instance = load_fixture("clear/absence_is_valid.json")
        self.assertNotIn("state", instance)
        self.assertNotIn("reason_code", instance)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
