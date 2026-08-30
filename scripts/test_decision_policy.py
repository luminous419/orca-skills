#!/usr/bin/env python3
"""Tests for the OS-28 decision policy contract (DESIGN D4).

Anti-vacuity rule (DESIGN D4-F): every data-driven loop asserts its collection's
expected cardinality INSIDE the same test function, before the loop. A guard in a
separate test can be deleted or skipped independently of the loop it protects; a
co-located guard cannot. Six loops in this file carry such a guard, each marked
`# D4-F guard`.
"""

from __future__ import annotations

import ast
import inspect
import json
import unittest
from pathlib import Path

from scripts.decision_policy import (
    AXIS_TOKENS,
    _validate_declared_facts,
    _element_is_triggering,
    _domain_defect,
    ENTRY_PREDICATES,
    _evaluate_predicate,
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
EXPECTED_USER_DECISION_SOURCES = {
    "explicit_user_reply",
    "prior_explicit_user_authorization",
}
# FR-2: aliases of the forbidden CATEGORIES, none of which appears in any denylist.
# These are the three the Final Reviewer used to demonstrate the bypass, plus an
# invented source, an empty source, and a missing field.
ADVERSARIAL_AUTHORITY_SOURCES = (
    "high_confidence",
    "worker_reviewer_consensus",
    "automated_default",
    "an_entirely_invented_source",
    "empty_source",
    "missing_source",
)
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


#: RI9-1: a policy_source must carry a non-empty textual locator (A4-1 row 10), so a
#: role/kind pair alone is NOT a valid source. Tests used that shorthand throughout and
#: therefore leaned on the very fail-open RI9-1 reported. Both helpers below produce a
#: complete source; anything asserting a source is VALID must use them.
LOCATOR = "docs/policy.md#rule-1"


def supporting_source(**overrides) -> dict:
    return {"role": "supports", "kind": "file_path", "locator": LOCATOR, **overrides}


def determining_source(**overrides) -> dict:
    return {"role": "determines", "kind": "file_path", "locator": LOCATOR, **overrides}


def error_of(fn, policy, *args) -> str:
    """The message `fn` raises for these arguments, or "" when it accepts them.

    Comparing the CONCEPT-SPECIFIC message rather than "did it raise at all" is what
    makes a cross-API parity claim meaningful: two APIs can both reject a record for
    different reasons and look consistent while judging the concept under test
    differently."""
    try:
        fn(policy, *args)
        return ""
    except DecisionPolicyError as error:
        return str(error)


def assumption_allowed_record(policy, **overrides) -> dict:
    """A complete, valid ASSUMPTION_ALLOWED record, usable as BOTH a record and a
    facts mapping so the two APIs can be handed identical input."""
    name = codes_for_state(policy, "ASSUMPTION_ALLOWED")[0]
    code = policy.reason_codes[name]
    record = {
        "state": "ASSUMPTION_ALLOWED",
        "reason_code": code.name,
        "policy_source": supporting_source(),
        "blast_radius": "current_change",
    }
    for field in code.required_evidence:
        if field in record:
            continue
        spec = policy.boundary_elements.get(field)
        record[field] = spec.values[0] if spec and spec.kind == "enum" else "x"
    record.update(overrides)
    return record


def complete_decision(source: str = "explicit_user_reply") -> dict:
    """A user_decision carrying every field the contract declares.

    FR-5: tests that built `{"source": ...}` by hand were asserting that a category
    claim is authority. Anything asserting a decision is VALID must use this."""
    return {
        "source": source,
        "where_recorded": "USER_DECISIONS.md#UD-1",
        "resolves": "the open item",
    }


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
        # The substantive UD-2 property: the contract does not FORCE NEEDS_INPUT here.
        self.assertNotEqual(permitted, frozenset({"NEEDS_INPUT"}))
        self.assertNotIn("NEEDS_INPUT", permitted)
        self.assertIn("ASSUMPTION_ALLOWED", permitted)
        # FR-4 changed the correct answer for CLEAR here, and the old assertion was
        # wrong rather than merely outdated: a safe item with a SUPPORTING policy
        # source still has an open decision, which is why it is ASSUMPTION_ALLOWED.
        # A3-1 admits CLEAR only when nothing is open, a policy source DETERMINES the
        # choice, or an authorization decides it -- none of which holds here.
        self.assertNotIn("CLEAR", permitted)


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


class Requirement4EntryConditionsAreEvaluated(DecisionPolicyTestCase):
    """FR-4. permitted_states() previously fixed its result to
    {CLEAR, NEEDS_INPUT, CONFLICT} and computed only whether to add
    ASSUMPTION_ALLOWED, so an irreversible, external-system, security-relevant item
    with no policy source and no authorization was reported as permitting CLEAR.
    The old requirement-4 test asserted only the ABSENCE of ASSUMPTION_ALLOWED --
    a narrower property than the one being claimed."""

    HIGH_IMPACT = {
        "reversibility": "irreversible",
        "blast_radius": "external_system",
        "security": True,
    }

    def test_unauthorized_high_impact_permits_only_needs_input(self) -> None:
        """The FR-4 case itself. Negative AND positive in one assertion: CLEAR and
        ASSUMPTION_ALLOWED are both refused, and NEEDS_INPUT is the sole survivor."""
        permitted = permitted_states(self.policy, self.HIGH_IMPACT)
        self.assertEqual(permitted, frozenset({"NEEDS_INPUT"}))
        self.assertNotIn("CLEAR", permitted)
        self.assertNotIn("ASSUMPTION_ALLOWED", permitted)

    def test_each_high_impact_element_alone_refuses_clear_without_authority(self) -> None:
        """One triggering element is enough. Co-located guard so emptying the
        contract's element set cannot make this loop vacuous."""
        cases = {
            "reversibility": "irreversible",
            "blast_radius": "external_system",
            "monetary_cost": True,
            "security": True,
            "privacy": True,
            "compliance": True,
            "long_term_lock_in": True,
            "explicit_user_authority": "reserved",
        }
        # D4-F guard
        self.assertEqual(len(cases), 8)
        for element, value in cases.items():
            with self.subTest(element=element):
                permitted = permitted_states(self.policy, {element: value})
                self.assertNotIn("CLEAR", permitted, f"{element} allowed CLEAR")
                self.assertIn("NEEDS_INPUT", permitted)

    def test_a_determining_policy_source_permits_clear(self) -> None:
        """Positive control: A3-1 admits CLEAR when a policy source DETERMINES the
        choice. Without this the fix could be over-blocking."""
        permitted = permitted_states(
            self.policy, {**self.HIGH_IMPACT, "policy_source": determining_source()}
        )
        self.assertIn("CLEAR", permitted)
        self.assertNotIn("ASSUMPTION_ALLOWED", permitted)

    def test_an_allowlisted_authorization_permits_clear(self) -> None:
        """Positive control: a COMPLETE explicit user authorization decides the item.

        FR-5: this fixture was source-only, so the test was pinning the defect --
        it asserted that a bare category claim buys CLEAR. The record now carries
        every field `user_decision_fields` declares, which is what A5-3 requires and
        what makes the claim checkable by someone else."""
        for source in sorted(self.policy.user_decision_sources):
            with self.subTest(source=source):
                permitted = permitted_states(
                    self.policy,
                    {**self.HIGH_IMPACT, "user_decision": complete_decision(source)},
                )
                self.assertIn("CLEAR", permitted)

    def test_a_forbidden_authority_source_does_not_permit_clear(self) -> None:
        """FR-2 and FR-4 meet here: the allowlist gates this route too, so a
        non-user source cannot buy CLEAR for a high-impact item."""
        for source in sorted(self.policy.forbidden_authority_sources) + ["invented"]:
            with self.subTest(source=source):
                # A COMPLETE record, so the source is the only reason it fails. A
                # source-only record would pass this test for the wrong reason.
                permitted = permitted_states(
                    self.policy,
                    {**self.HIGH_IMPACT, "user_decision": complete_decision(source)},
                )
                self.assertEqual(permitted, frozenset({"NEEDS_INPUT"}))

    def test_nothing_open_permits_clear(self) -> None:
        permitted = permitted_states(self.policy, {"open_decision_item": False})
        self.assertEqual(permitted, frozenset({"CLEAR"}))

    def test_conflict_is_not_permitted_without_a_declared_contradiction(self) -> None:
        """CONFLICT was previously in the fixed starting set, so it was 'permitted'
        for every input. It now requires a declared contradiction."""
        for facts in ({"open_decision_item": False}, self.HIGH_IMPACT,
                      load_fixture("valid/repository_policy.json")):
            with self.subTest(facts=sorted(facts)):
                self.assertNotIn("CONFLICT", permitted_states(self.policy, facts))

    def test_a_declared_contradiction_permits_conflict(self) -> None:
        for clause in sorted(self.policy.entry_clauses["CONFLICT"]):
            with self.subTest(clause=clause):
                self.assertIn(
                    "CONFLICT",
                    permitted_states(self.policy, {"conflict_clause": clause}),
                )

    def test_an_unknown_entry_predicate_fails_to_load(self) -> None:
        mutated = json.loads(json.dumps(self.block))
        mutated["entry_conditions"]["CLEAR"]["any_of"].append("anything_goes")
        with self.assertRaises(DecisionPolicyError):
            parse_decision_policy(mutated)

    def test_relaxing_clear_to_admit_a_triggered_element_changes_the_verdict(self) -> None:
        """Mutation resistance, executed in-process: if CLEAR's condition were
        widened, the FR-4 case would permit CLEAR again. Proves this suite reads the
        contract rather than a hardcoded expectation."""
        mutated = json.loads(json.dumps(self.block))
        mutated["entry_conditions"]["CLEAR"]["any_of"].append("unclassifiable_item")
        widened = parse_decision_policy(mutated)
        self.assertNotIn("CLEAR", permitted_states(widened, self.HIGH_IMPACT))
        self.assertIn(
            "CLEAR",
            permitted_states(widened, {**self.HIGH_IMPACT, "unclassifiable": True}),
        )


class AuthorityPrecedenceAcrossPredicates(DecisionPolicyTestCase):
    """RI3-1. The entry predicates were evaluated independently, so each was right
    alone and wrong in combination: a determining policy source satisfied CLEAR while
    also suppressing NEEDS_INPUT for a reserved-authority item, and left CLEAR
    alongside CONFLICT for a declared contradiction. A4-0 names exactly two cells a
    policy source cannot resolve, and the contract now carries them."""

    DETERMINES = {"policy_source": {"kind": "file_path", "locator": "x", "role": "determines"}}
    SUPPORTS = {"policy_source": {"kind": "file_path", "locator": "x", "role": "supports"}}
    AUTHORIZED = {
        "user_decision": {
            "source": "explicit_user_reply",
            "where_recorded": "USER_DECISIONS.md#UD-1",
            "resolves": "the item",
        }
    }
    FORBIDDEN = {
        "user_decision": {
            "source": "model_confidence",
            "where_recorded": "x",
            "resolves": "y",
        }
    }

    def test_a_policy_source_cannot_un_reserve_user_authority(self) -> None:
        """RI3-1 case 1. A4-0: 'a policy source cannot un-reserve it -> NEEDS_INPUT'."""
        facts = {"explicit_user_authority": "reserved", **self.DETERMINES}
        self.assertEqual(permitted_states(self.policy, facts), frozenset({"NEEDS_INPUT"}))

    def test_a_policy_source_cannot_arbitrate_a_declared_contradiction(self) -> None:
        """RI3-1 case 2. A4-0: 'a policy source cannot arbitrate two explicit
        requirements -> CONFLICT'."""
        for clause in sorted(self.policy.entry_clauses["CONFLICT"]):
            with self.subTest(clause=clause):
                facts = {"conflict_clause": clause, **self.DETERMINES}
                self.assertEqual(
                    permitted_states(self.policy, facts), frozenset({"CONFLICT"})
                )

    def test_a_supporting_policy_source_resolves_neither(self) -> None:
        for label, facts in (
            ("reserved", {"explicit_user_authority": "reserved"}),
            ("contradiction", {"conflict_clause": "C-1"}),
        ):
            with self.subTest(case=label):
                expected = "NEEDS_INPUT" if label == "reserved" else "CONFLICT"
                self.assertEqual(
                    permitted_states(self.policy, {**facts, **self.SUPPORTS}),
                    frozenset({expected}),
                )

    def test_a_forbidden_authority_source_resolves_neither(self) -> None:
        """FR-2's allowlist gates this route too."""
        for label, facts, expected in (
            ("reserved", {"explicit_user_authority": "reserved"}, "NEEDS_INPUT"),
            ("contradiction", {"conflict_clause": "C-1"}, "CONFLICT"),
        ):
            with self.subTest(case=label):
                self.assertEqual(
                    permitted_states(self.policy, {**facts, **self.FORBIDDEN}),
                    frozenset({expected}),
                )

    def test_a_determining_policy_source_still_resolves_ordinary_elements(self) -> None:
        """POSITIVE CONTROL, and the anti-over-blocking half. Every element A4-0 routes
        to CLEAR with a determining policy source must still reach CLEAR."""
        resolvable = {
            "security": True,
            "privacy": True,
            "compliance": True,
            "monetary_cost": True,
            "long_term_lock_in": True,
            "ambiguity": True,
            "reversibility": "irreversible",
            "blast_radius": "external_system",
        }
        # D4-F guard: every boundary element that can trigger, minus the two A4-0 says
        # a policy source cannot resolve.
        triggering = {
            name
            for name, spec in self.policy.boundary_elements.items()
            if spec.triggering is not None
        }
        self.assertEqual(
            set(resolvable), triggering - set(self.policy.policy_source_cannot_resolve)
        )
        for element, value in sorted(resolvable.items()):
            with self.subTest(element=element):
                self.assertEqual(
                    permitted_states(self.policy, {element: value, **self.DETERMINES}),
                    frozenset({"CLEAR"}),
                )

    def test_an_allowlisted_authorization_resolves_both_cells(self) -> None:
        """POSITIVE CONTROL: A4-0's authorization column routes both 'cannot' rows to
        CLEAR, so the precedence bar must not block a real user decision."""
        for label, facts in (
            ("reserved", {"explicit_user_authority": "reserved"}),
            ("C-1", {"conflict_clause": "C-1"}),
            ("C-2", {"conflict_clause": "C-2"}),
            ("C-3", {"conflict_clause": "C-3"}),
        ):
            with self.subTest(case=label):
                self.assertEqual(
                    permitted_states(self.policy, {**facts, **self.AUTHORIZED}),
                    frozenset({"CLEAR"}),
                )

    def test_no_open_decision_item_is_false_when_something_is_open(self) -> None:
        """A3-1 clause 1 is 'no decision item is open'. Declaring open_decision_item
        false alongside a triggering element or a contradiction is self-contradictory
        and must not yield CLEAR."""
        for label, facts, expected in (
            ("triggered element", {"security": True}, "NEEDS_INPUT"),
            ("contradiction", {"conflict_clause": "C-1"}, "CONFLICT"),
            ("reserved", {"explicit_user_authority": "reserved"}, "NEEDS_INPUT"),
        ):
            with self.subTest(case=label):
                self.assertEqual(
                    permitted_states(self.policy, {"open_decision_item": False, **facts}),
                    frozenset({expected}),
                )

    def test_no_open_decision_item_alone_still_yields_clear(self) -> None:
        """POSITIVE CONTROL for the refinement above."""
        self.assertEqual(
            permitted_states(self.policy, {"open_decision_item": False}),
            frozenset({"CLEAR"}),
        )

    def test_undeclared_facts_permit_nothing_by_design(self) -> None:
        """Empty facts return an EMPTY set, and that is deliberate, not an oversight.
        A3-1 admits CLEAR only on an affirmative 'no decision item is open'; a caller
        that has declared nothing has not asserted that. Returning CLEAR for silence
        would make the absence of analysis look like a clean result, which is the
        failure mode this contract exists to prevent. Documented in DESIGN D2-2c."""
        self.assertEqual(permitted_states(self.policy, {}), frozenset())

    def test_no_combination_permits_a_continuing_state_without_a_resolver(self) -> None:
        """The invariant behind RI3-1, over the whole combination space rather than the
        two reported cases: for every triggering element, every CONFLICT clause, and
        every pair of the two, a continuing state (CLEAR or ASSUMPTION_ALLOWED) is
        permitted ONLY with a determining policy source that A4-0 allows to resolve
        that item, or an allowlisted user decision.

        COUNT, corrected: this asserts **63** cases -- 21 fact-cases (9 triggering
        elements + 3 CONFLICT clauses + 9 element-with-C-1 pairs) x the 3 resolver
        states that carry NO authority (none / supporting / forbidden). The earlier
        "105" was an ad-hoc probe that also swept the two resolver states which DO
        carry authority; those belong in the sibling test below, not here, because
        their expected outcome is CLEAR rather than "no continuing state". A label
        wider than what the code executes is the exact defect this run keeps hitting,
        so the number now matches `assertEqual(checked, 21 * 3)`."""
        triggers = {
            name: (spec.triggering[0] if isinstance(spec.triggering, (list, tuple))
                   else True)
            for name, spec in self.policy.boundary_elements.items()
            if spec.triggering is not None and spec.triggering != "at_minimum"
        }
        # D4-F guard
        self.assertEqual(len(triggers), 9)
        clauses = sorted(self.policy.entry_clauses["CONFLICT"])
        self.assertEqual(len(clauses), 3)

        cases = [({name: value}, {name}, False) for name, value in triggers.items()]
        cases += [({"conflict_clause": c}, set(), True) for c in clauses]
        cases += [
            ({name: value, "conflict_clause": "C-1"}, {name}, True)
            for name, value in triggers.items()
        ]
        no_resolver = {
            "none": {},
            "supporting": self.SUPPORTS,
            "forbidden": self.FORBIDDEN,
        }
        checked = 0
        for facts, _, _ in cases:
            for label, resolver in no_resolver.items():
                with self.subTest(facts=sorted(facts), resolver=label):
                    permitted = permitted_states(self.policy, {**facts, **resolver})
                    self.assertFalse(
                        permitted & {"CLEAR", "ASSUMPTION_ALLOWED"},
                        f"{sorted(facts)} + {label} leaked {sorted(permitted)}",
                    )
                    self.assertTrue(permitted, "a declared item must permit something")
                    checked += 1
        self.assertEqual(checked, 21 * 3)

    def test_a_resolver_bearing_authority_yields_clear_across_the_same_space(self) -> None:
        """The other 42 of the ad-hoc 105, made permanent rather than dropped.

        Same 21 fact-cases against the 2 resolver states that DO carry authority.
        An allowlisted user decision resolves every case; a determining policy source
        resolves every case EXCEPT the two A4-0 says it cannot. This is the positive
        control for the sibling test above -- without it, refusing everything would
        satisfy that one."""
        triggers = {
            name: (spec.triggering[0] if isinstance(spec.triggering, (list, tuple))
                   else True)
            for name, spec in self.policy.boundary_elements.items()
            if spec.triggering is not None and spec.triggering != "at_minimum"
        }
        # D4-F guard
        self.assertEqual(len(triggers), 9)
        clauses = sorted(self.policy.entry_clauses["CONFLICT"])
        self.assertEqual(len(clauses), 3)
        cases = [({n: v}, {n}, False) for n, v in triggers.items()]
        cases += [({"conflict_clause": c}, set(), True) for c in clauses]
        cases += [({n: v, "conflict_clause": "C-1"}, {n}, True) for n, v in triggers.items()]
        self.assertEqual(len(cases), 21)

        checked = 0
        for facts, trig, has_conflict in cases:
            unresolvable = has_conflict or bool(
                trig & set(self.policy.policy_source_cannot_resolve)
            )
            with self.subTest(facts=sorted(facts), resolver="authorized"):
                self.assertEqual(
                    permitted_states(self.policy, {**facts, **self.AUTHORIZED}),
                    frozenset({"CLEAR"}),
                )
                checked += 1
            with self.subTest(facts=sorted(facts), resolver="determining"):
                permitted = permitted_states(self.policy, {**facts, **self.DETERMINES})
                if unresolvable:
                    self.assertNotIn("CLEAR", permitted)
                else:
                    self.assertEqual(permitted, frozenset({"CLEAR"}))
                checked += 1
        self.assertEqual(checked, 21 * 2)

    def test_every_entry_predicate_is_satisfiable_and_falsifiable(self) -> None:
        """Downstream revalidation, defect type (a): a predicate that can never be
        true is a dead clause, and one that can never be false fixes its combinator's
        outcome. Both directions, for all twelve."""
        witnesses = {
            "no_open_decision_item": ({"open_decision_item": False}, {}),
            "determining_policy_source": (self.DETERMINES, {}),
            "explicit_user_authorization": (self.AUTHORIZED, {}),
            "reversible_in_run": (
                {"reversibility": "reversible_in_run"},
                {"reversibility": "irreversible"},
            ),
            "blast_radius_within_scope": (
                {"blast_radius": "current_change"},
                {"blast_radius": "external_system"},
            ),
            "no_high_impact_element": ({}, {"security": True}),
            "supporting_policy_source": (self.SUPPORTS, {}),
            "no_reserved_user_authority": ({}, {"explicit_user_authority": "reserved"}),
            "undetermined_boundary_element": ({"security": True}, {}),
            "absent_user_intent": ({"user_intent_absent": True}, {}),
            "unclassifiable_item": ({"unclassifiable": True}, {}),
            "declared_contradiction": ({"conflict_clause": "C-1"}, {}),
        }
        # D4-F guard: a witness for every predicate in the closed vocabulary, and none
        # left over, so adding a predicate without a witness fails here.
        self.assertEqual(set(witnesses), set(ENTRY_PREDICATES))
        self.assertEqual(len(witnesses), 12)
        for name, (true_facts, false_facts) in sorted(witnesses.items()):
            with self.subTest(predicate=name):
                self.assertTrue(
                    _evaluate_predicate(name, self.policy, true_facts),
                    f"{name} is unreachable -- a dead clause",
                )
                self.assertFalse(
                    _evaluate_predicate(name, self.policy, false_facts),
                    f"{name} cannot be falsified -- its combinator is fixed",
                )

    def test_every_predicate_is_used_by_some_entry_condition(self) -> None:
        """The other half of (a): a predicate defined but never referenced is dead too."""
        used = set()
        for condition in self.policy.entry_conditions.values():
            for predicates in condition.values():
                used |= set(predicates)
        self.assertEqual(used, set(ENTRY_PREDICATES))

    def test_a_triggering_value_outside_the_elements_own_enum_is_rejected(self) -> None:
        """Downstream revalidation, defect type (e) on the new surface: `triggering`
        was checked for presence but never against the element's own value set. An
        orphan value can never be matched -- _validate_declared_facts rejects
        out-of-enum declarations -- so the element became a DEAD TRIGGER and stopped
        escalating silently."""
        mutated = json.loads(json.dumps(self.block))
        mutated["boundary_elements"]["reversibility"]["triggering"] = ["not_a_member"]
        with self.assertRaises(DecisionPolicyError):
            parse_decision_policy(mutated)

    def test_every_shipped_triggering_value_is_a_member_of_its_element(self) -> None:
        """Positive control for the check above, over the real contract."""
        checked = 0
        for name, spec in sorted(self.policy.boundary_elements.items()):
            if spec.kind != "enum" or not isinstance(spec.triggering, (list, tuple)):
                continue
            for value in spec.triggering:
                with self.subTest(element=name, triggering=value):
                    self.assertIn(value, spec.values)
                    checked += 1
        # D4-F guard: irreversible + repository + external_system.
        self.assertEqual(checked, 3)

    def test_both_pausing_states_are_permitted_when_both_are_declared(self) -> None:
        """Not a defect, and recorded because my first sweep expectation said it was.
        A3-1 makes NEEDS_INPUT 'missing information' and CONFLICT 'contradictory
        information' -- different decision items. OQ-1 settled that state is per ITEM
        with a per-check aggregate, so facts declaring both legitimately permit both;
        `aggregate_order` is what reduces a multi-item check to one reported state.
        Neither is a continuing state, so nothing is weakened."""
        permitted = permitted_states(
            self.policy, {"security": True, "conflict_clause": "C-1"}
        )
        self.assertEqual(permitted, frozenset({"NEEDS_INPUT", "CONFLICT"}))
        self.assertEqual(self.policy.aggregate_order[0], "CONFLICT")

    def test_widening_the_precedence_list_changes_the_verdict(self) -> None:
        """Mutation resistance in-process: emptying the list restores the RI3-1 defect,
        proving this suite reads the contract rather than a hardcoded expectation."""
        mutated = json.loads(json.dumps(self.block))
        mutated["authority_precedence"]["policy_source_cannot_resolve"] = []
        widened = parse_decision_policy(mutated)
        facts = {"explicit_user_authority": "reserved", **self.DETERMINES}
        self.assertIn("CLEAR", permitted_states(widened, facts))
        self.assertNotIn("CLEAR", permitted_states(self.policy, facts))


class Requirement3BoundaryElementMustMatchTheCode(DecisionPolicyTestCase):
    """FR-3. validate_record() checked only that the evidence field was non-empty, so
    `security_impact` could be filed with boundary_element `privacy`. Misclassification
    -- the thing a Reviewer is required to be able to judge -- was not machine-checkable.
    The liveness test compared the two values in TEST code, which proves the fixture is
    consistent, not that production rejects an inconsistent one."""

    def test_every_bound_code_rejects_a_mismatched_boundary_element(self) -> None:
        """One mismatch injected per boundary-bound code, with a co-located guard."""
        bound = {
            name: code.boundary_element
            for name, code in self.policy.reason_codes.items()
            if code.boundary_element is not None
        }
        # D4-F guard: 10 of the 11 NEEDS_INPUT codes bind an element;
        # unclassifiable_decision deliberately does not.
        self.assertEqual(len(bound), 10)
        for name, element in sorted(bound.items()):
            with self.subTest(reason_code=name):
                record = load_fixture(f"valid/{name}.json")
                self.assertEqual(record["boundary_element"], element)
                wrong = next(
                    other
                    for other in self.policy.boundary_elements
                    if other != element
                )
                record["boundary_element"] = wrong
                with self.assertRaises(DecisionPolicyError):
                    validate_record(self.policy, record)

    def test_every_bound_code_accepts_its_declared_element(self) -> None:
        """Positive control for the same 10 codes, so the check above cannot be
        satisfied by rejecting everything."""
        bound = [
            name
            for name, code in self.policy.reason_codes.items()
            if code.boundary_element is not None
        ]
        # D4-F guard
        self.assertEqual(len(bound), 10)
        for name in sorted(bound):
            with self.subTest(reason_code=name):
                validate_record(self.policy, load_fixture(f"valid/{name}.json"))

    def test_unclassifiable_decision_binds_no_element_and_must_not_declare_one(
        self,
    ) -> None:
        """The deliberate override, kept as a separate positive control: the code has
        no bound element, its fixture validates, and smuggling one in is rejected."""
        code = self.policy.reason_codes["unclassifiable_decision"]
        self.assertIsNone(code.boundary_element)
        self.assertNotIn("boundary_element", code.required_evidence)
        record = load_fixture("valid/unclassifiable_decision.json")
        self.assertNotIn("boundary_element", record)
        validate_record(self.policy, record)
        with self.assertRaises(DecisionPolicyError):
            validate_record(self.policy, {**record, "boundary_element": "security"})


class DeclaredFactsMustBeConsistentWithTheContract(DecisionPolicyTestCase):
    """The FR-3 axis, swept beyond the reported location. Three more fields were
    checked for presence but never for membership in the set the contract declares.
    An unrecognised enum value did not raise -- it matched no triggering value, so
    permitted_states returned an EMPTY set: degenerate, not fail-closed."""

    def test_an_enum_element_outside_its_closed_values_is_rejected(self) -> None:
        cases = {
            "reversibility": "sort_of_reversible",
            "blast_radius": "the_whole_internet",
        }
        # D4-F guard: exactly the enum-kind elements the contract declares.
        enum_elements = [
            name
            for name, spec in self.policy.boundary_elements.items()
            if spec.kind == "enum"
        ]
        self.assertEqual(sorted(enum_elements), sorted(cases))
        base = load_fixture("valid/repository_policy.json")
        for element, bogus in sorted(cases.items()):
            with self.subTest(element=element):
                record = {**base, element: bogus}
                with self.assertRaises(DecisionPolicyError):
                    validate_record(self.policy, record)
                with self.assertRaises(DecisionPolicyError):
                    permitted_states(self.policy, record)

    def test_every_declared_enum_value_is_accepted(self) -> None:
        """Positive control: each legal member of each enum passes, so the check
        above cannot be satisfied by rejecting all values."""
        checked = 0
        for name, spec in sorted(self.policy.boundary_elements.items()):
            if spec.kind != "enum":
                continue
            for value in spec.values:
                with self.subTest(element=name, value=value):
                    permitted_states(self.policy, {name: value})
                    checked += 1
        # D4-F guard: 3 reversibility values + 4 blast_radius values.
        self.assertEqual(checked, 7)

    def test_a_policy_source_kind_outside_the_closed_set_is_rejected(self) -> None:
        base = load_fixture("valid/repository_policy.json")
        record = {**base, "policy_source": {**base["policy_source"], "kind": "model_hunch"}}
        with self.assertRaises(DecisionPolicyError):
            validate_record(self.policy, record)

    def test_every_declared_policy_source_kind_is_accepted(self) -> None:
        """Positive control for the same check."""
        base = load_fixture("valid/repository_policy.json")
        kinds = list(self.policy.policy_source_kinds)
        # D4-F guard
        self.assertEqual(len(kinds), 4)
        for kind in kinds:
            with self.subTest(kind=kind):
                validate_record(
                    self.policy,
                    {**base, "policy_source": {**base["policy_source"], "kind": kind}},
                )

    def test_omitting_an_element_remains_legal(self) -> None:
        """Over-blocking guard: only DECLARED values are checked, so a record that
        simply does not mention an element is still valid."""
        validate_record(self.policy, load_fixture("valid/repository_policy.json"))
        self.assertEqual(
            permitted_states(self.policy, {"open_decision_item": False}),
            frozenset({"CLEAR"}),
        )


class Requirement6UserAuthorityIsAnAllowlist(DecisionPolicyTestCase):
    """FR-2. User authority was an open string minus five exact tokens, so
    `high_confidence` and `worker_reviewer_consensus` -- the same categories as the
    listed `model_confidence` and `worker_reviewer_agreement`, differently spelled --
    satisfied a requires_user_decision transition. A denylist of spellings cannot
    enforce a categorical rule. Enforcement is now membership in a closed positive
    vocabulary, and an unrecognised source is rejected."""

    def test_the_positive_vocabulary_is_exactly_the_two_recognised_shapes(self) -> None:
        self.assertEqual(
            set(self.policy.user_decision_sources), EXPECTED_USER_DECISION_SOURCES
        )

    def test_genuine_user_evidence_is_accepted(self) -> None:
        """The other half of the FR-2 fix: over-blocking is also a wrong
        implementation. The ticket says classifying everything as NEEDS_INPUT is a
        defect, so the allowlist must still admit real user decisions."""
        directory = FIXTURES / "authority_valid"
        cases = sorted(directory.glob("*.json"))
        # D4-F guard
        self.assertEqual(len(cases), 2)
        accepted = set()
        for path in cases:
            with self.subTest(fixture=path.name):
                case = json.loads(path.read_text(encoding="utf-8"))
                validate_transition(
                    self.policy, case["from"], case["to"], case["record"]
                )
                accepted.add(case["record"]["user_decision"]["source"])
        self.assertEqual(accepted, EXPECTED_USER_DECISION_SOURCES)

    def test_every_recognised_source_has_a_passing_fixture(self) -> None:
        """Bidirectional: a vocabulary entry with no fixture proving it usable would
        be a dead entry, the RA-4 defect in a new place."""
        proven = {
            json.loads(p.read_text(encoding="utf-8"))["record"]["user_decision"]["source"]
            for p in (FIXTURES / "authority_valid").glob("*.json")
        }
        self.assertEqual(proven, set(self.policy.user_decision_sources))

    def test_alias_and_unknown_sources_are_rejected(self) -> None:
        """The adversarial half. None of these appears in any denylist, which is
        exactly why a denylist could not stop them."""
        # D4-F guard
        self.assertEqual(len(ADVERSARIAL_AUTHORITY_SOURCES), 6)
        for name in ADVERSARIAL_AUTHORITY_SOURCES:
            with self.subTest(source=name):
                case = load_fixture(f"invalid/authority/{name}.json")
                with self.assertRaises(DecisionPolicyError):
                    validate_transition(
                        self.policy, case["from"], case["to"], case["record"]
                    )

    def test_a_case_variant_of_a_recognised_source_is_rejected(self) -> None:
        """Membership is exact. `EXPLICIT_USER_REPLY` is not `explicit_user_reply`."""
        for variant in ("EXPLICIT_USER_REPLY", "Explicit_User_Reply", " explicit_user_reply"):
            with self.subTest(variant=variant):
                record = {
                    "user_decision": {
                        "source": variant,
                        "where_recorded": "x",
                        "resolves": "y",
                    }
                }
                with self.assertRaises(DecisionPolicyError):
                    validate_transition(self.policy, "NEEDS_INPUT", "CLEAR", record)

    def test_the_denylist_no_longer_enforces_but_guards_the_allowlist(self) -> None:
        """The retained denylist has one job now: the two sets must stay disjoint, so
        adding a forbidden category to the positive vocabulary fails at load time."""
        self.assertFalse(
            set(self.policy.user_decision_sources)
            & set(self.policy.forbidden_authority_sources)
        )

    def test_a_forbidden_source_added_to_the_vocabulary_fails_to_load(self) -> None:
        mutated = json.loads(json.dumps(self.block))
        mutated["user_decision_sources"] = list(mutated["user_decision_sources"]) + [
            "recommended_default"
        ]
        with self.assertRaises(DecisionPolicyError):
            parse_decision_policy(mutated)

    def test_an_empty_positive_vocabulary_fails_to_load(self) -> None:
        """Over-blocking guard: emptying the allowlist would make every user decision
        unrepresentable, which the ticket also calls a wrong implementation."""
        mutated = json.loads(json.dumps(self.block))
        mutated["user_decision_sources"] = []
        with self.assertRaises(DecisionPolicyError):
            parse_decision_policy(mutated)

    def test_the_four_recorded_user_decisions_are_expressible(self) -> None:
        """The empirical test of the vocabulary's width. UD-1..UD-4 were each an
        answer to a structured question the Coordinator put to the repository owner,
        so each is `explicit_user_reply` with a locator into USER_DECISIONS.md. If the
        vocabulary could not express them it would be too narrow."""
        for identifier in ("UD-1", "UD-2", "UD-3", "UD-4"):
            with self.subTest(user_decision=identifier):
                record = {
                    "user_decision": {
                        "source": "explicit_user_reply",
                        "where_recorded": f"USER_DECISIONS.md#{identifier}",
                        "resolves": f"the question recorded as {identifier}",
                    }
                }
                validate_transition(self.policy, "NEEDS_INPUT", "CLEAR", record)
                validate_transition(self.policy, "CONFLICT", "CLEAR", record)


class UserDecisionJudgementIsSharedByBothApis(DecisionPolicyTestCase):
    """FR-5. permitted_states() accepted a user_decision carrying only a `source`,
    so a bare category claim bought CLEAR for a reserved-authority, security-relevant
    item, while validate_transition() rejected the same mapping. Two production APIs
    answered the same evidence opposite ways and the permissive one gated the
    high-impact path. Both now read one helper."""

    HIGH_IMPACT = {
        "reversibility": "irreversible",
        "blast_radius": "external_system",
        "security": True,
    }

    def test_the_two_apis_agree_on_every_field_omission(self) -> None:
        """The parity test -- the recurrence guard for this defect. For each field the
        contract declares, and for the complete record, permitted_states() and
        validate_transition() must reach the SAME authorization verdict."""
        fields = list(self.policy.user_decision_fields)
        # D4-F guard: every declared field is exercised, plus the complete record.
        self.assertEqual(len(fields), 3)
        checked = 0
        for dropped in fields + [None]:
            with self.subTest(dropped=dropped or "(complete)"):
                decision = complete_decision()
                if dropped:
                    decision.pop(dropped)
                facts = {"security": True, "user_decision": decision}
                clear_permitted = "CLEAR" in permitted_states(self.policy, facts)
                try:
                    validate_transition(self.policy, "NEEDS_INPUT", "CLEAR", facts)
                    transition_ok = True
                except DecisionPolicyError:
                    transition_ok = False
                self.assertEqual(
                    clear_permitted,
                    transition_ok,
                    f"the two APIs disagree when {dropped!r} is missing",
                )
                # And the verdict itself must be right, not merely consistent.
                self.assertEqual(clear_permitted, dropped is None)
                checked += 1
        self.assertEqual(checked, 4)

    def test_an_empty_field_is_not_evidence_either(self) -> None:
        """Closes the gap mutation N-4 exposed. Every test above removes a field with
        `pop`, so a helper that checked only `field not in decision` -- presence
        without content, recurring defect type (e) -- passed the whole suite. A
        `where_recorded` of "" is a filled-in form with nothing written on it.
        Both APIs must refuse it, exactly as they refuse the missing field."""
        fields = list(self.policy.user_decision_fields)
        # Whitespace-only is deliberately NOT in this tuple: `_is_empty` treats
        # "   " as content, so a whitespace `where_recorded` is accepted as
        # evidence today. That is reported as TR4-3, not asserted here in either
        # direction -- pinning it would freeze the defect.
        empties = ("", None, [], {})
        # D4-F guards, co-located: neither loop may be empty.
        self.assertEqual(len(fields), 3)
        self.assertEqual(len(empties), 4)
        checked = 0
        for field in fields:
            for empty in empties:
                with self.subTest(field=field, empty=repr(empty)):
                    decision = complete_decision()
                    decision[field] = empty
                    facts = {"security": True, "user_decision": decision}
                    self.assertNotIn("CLEAR", permitted_states(self.policy, facts))
                    with self.assertRaises(DecisionPolicyError):
                        validate_transition(self.policy, "NEEDS_INPUT", "CLEAR", facts)
                    checked += 1
        self.assertEqual(checked, 12)
        # POSITIVE CONTROL: the same record with real content is still accepted, so
        # this test cannot be satisfied by refusing everything.
        self.assertIn(
            "CLEAR",
            permitted_states(
                self.policy, {"security": True, "user_decision": complete_decision()}
            ),
        )

    def test_the_two_apis_agree_on_every_source(self) -> None:
        """Parity over the source axis: allowlisted, each forbidden category, and an
        invented spelling — always with a COMPLETE record, so the source is the only
        variable."""
        sources = sorted(self.policy.user_decision_sources) + sorted(
            self.policy.forbidden_authority_sources
        ) + ["an_invented_source"]
        # D4-F guard: 2 allowlisted + 5 forbidden + 1 invented.
        self.assertEqual(len(sources), 8)
        for source in sources:
            with self.subTest(source=source):
                facts = {"security": True, "user_decision": complete_decision(source)}
                clear_permitted = "CLEAR" in permitted_states(self.policy, facts)
                try:
                    validate_transition(self.policy, "NEEDS_INPUT", "CLEAR", facts)
                    transition_ok = True
                except DecisionPolicyError:
                    transition_ok = False
                self.assertEqual(clear_permitted, transition_ok)
                self.assertEqual(
                    clear_permitted, source in self.policy.user_decision_sources
                )

    def test_an_incomplete_decision_never_permits_clear_anywhere(self) -> None:
        """Cardinality-guarded negative sweep. For every situation that requires user
        authority — reserved authority, each CONFLICT clause, and the high-impact case
        — a source-only record and each single-field omission must NOT permit CLEAR."""
        situations = {
            "reserved_authority": {"explicit_user_authority": "reserved"},
            "conflict_C-1": {"conflict_clause": "C-1"},
            "conflict_C-2": {"conflict_clause": "C-2"},
            "conflict_C-3": {"conflict_clause": "C-3"},
            "high_impact": dict(self.HIGH_IMPACT),
        }
        fields = list(self.policy.user_decision_fields)
        # D4-F guards, co-located: 5 situations (reserved + 3 clauses + high-impact)
        # and 4 incomplete records (source-only + one per dropped field).
        self.assertEqual(len(situations), 1 + len(self.policy.entry_clauses["CONFLICT"]) + 1)
        self.assertEqual(len(fields), 3)

        incomplete = {"source_only": {"source": "explicit_user_reply"}}
        for dropped in fields:
            decision = complete_decision()
            decision.pop(dropped)
            incomplete[f"missing_{dropped}"] = decision
        self.assertEqual(len(incomplete), 4)

        checked = 0
        for situation, facts in sorted(situations.items()):
            for label, decision in sorted(incomplete.items()):
                with self.subTest(situation=situation, decision=label):
                    permitted = permitted_states(
                        self.policy, {**facts, "user_decision": decision}
                    )
                    self.assertNotIn(
                        "CLEAR",
                        permitted,
                        f"{situation} + {label} bought CLEAR without complete evidence",
                    )
                    self.assertTrue(permitted, "a declared item must permit something")
                    checked += 1
        self.assertEqual(checked, 5 * 4)

    def test_a_complete_decision_still_permits_clear_everywhere(self) -> None:
        """POSITIVE CONTROL for the sweep above, over the same 5 situations and both
        genuine sources. Refusing everything must not satisfy this pair."""
        situations = [
            {"explicit_user_authority": "reserved"},
            {"conflict_clause": "C-1"},
            {"conflict_clause": "C-2"},
            {"conflict_clause": "C-3"},
            dict(self.HIGH_IMPACT),
        ]
        sources = sorted(self.policy.user_decision_sources)
        # D4-F guard
        self.assertEqual((len(situations), len(sources)), (5, 2))
        checked = 0
        for facts in situations:
            for source in sources:
                with self.subTest(facts=sorted(facts), source=source):
                    self.assertEqual(
                        permitted_states(
                            self.policy,
                            {**facts, "user_decision": complete_decision(source)},
                        ),
                        frozenset({"CLEAR"}),
                    )
                    checked += 1
        self.assertEqual(checked, 5 * 2)


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

        # D4-F guard: the walk must actually visit every selection input. The count
        # has risen 16 -> 17 (FR-2 user_decision_sources) -> 18 (FR-4 entry_conditions)
        # -> 19 (RI3-1 authority_precedence). Each time the guard flagged a legitimate
        # change, which is what a cardinality guard is for.
        self.assertEqual(len(STATE_SELECTION_INPUTS & set(self.block)), 19)
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


class CrossApiConceptParity(DecisionPolicyTestCase):
    """T2(i). FR-5 was one instance of a shape: the SAME concept judged in two
    places, reaching different verdicts, with the permissive side gating the risky
    path. This class sweeps the other concepts that more than one public API judges
    and pins the ones that agree, so a future edit cannot quietly split them.

    Two concepts are asserted here because both are routed through the shared
    `_validate_declared_facts`: boundary-element enum membership and
    `policy_source.kind` membership. Two further divergences were found by this
    sweep and are REPORTED, not asserted either way, because closing them changes
    evaluator semantics: `policy_source.role` membership (TR4-1) and the
    ASSUMPTION_ALLOWED middle band (TR4-2). Asserting today's divergent behaviour
    would pin a defect -- which is exactly the mistake FR-5 found in two tests.
    """

    SUPPORTS = supporting_source()

    def _record(self, **extra) -> dict:
        code = self.policy.reason_codes[codes_for_state(self.policy, "ASSUMPTION_ALLOWED")[0]]
        record = {
            "state": "ASSUMPTION_ALLOWED",
            "reason_code": code.name,
            "policy_source": dict(self.SUPPORTS),
        }
        if code.boundary_element:
            record["boundary_element"] = code.boundary_element
        for field in code.required_evidence:
            if field in record:
                continue
            # A required field that is itself an enum boundary element must carry a
            # LEGAL value, or the record fails on that instead of on what is under
            # test -- the mistake FR-5 found in two tests, made here in miniature.
            spec = self.policy.boundary_elements.get(field)
            record[field] = spec.values[0] if spec and spec.kind == "enum" else "x"
        record.update(extra)
        return record

    def _rejects(self, fn, *args) -> bool:
        try:
            fn(self.policy, *args)
            return False
        except DecisionPolicyError:
            return True

    def test_enum_membership_is_judged_identically_by_both_apis(self) -> None:
        """An out-of-set boundary value must be REJECTED by permitted_states() and by
        validate_record() alike -- one helper, not two opinions."""
        enums = {
            name: spec
            for name, spec in self.policy.boundary_elements.items()
            if spec.kind == "enum"
        }
        # D4-F guard, co-located: the loop below must not be empty.
        self.assertGreaterEqual(len(enums), 2)
        checked = 0
        for name, spec in sorted(enums.items()):
            with self.subTest(element=name):
                good = spec.values[0]
                self.assertFalse(self._rejects(permitted_states, {name: good}))
                self.assertFalse(self._rejects(validate_record, self._record(**{name: good})))
                bad = f"{good}_not_a_member"
                self.assertTrue(self._rejects(permitted_states, {name: bad}))
                self.assertTrue(self._rejects(validate_record, self._record(**{name: bad})))
                checked += 1
        self.assertEqual(checked, len(enums))

    def test_policy_source_kind_membership_is_judged_identically(self) -> None:
        kinds = sorted(self.policy.policy_source_kinds) + ["invented_kind"]
        # D4-F guard: every declared kind plus one invented spelling.
        self.assertEqual(len(kinds), len(self.policy.policy_source_kinds) + 1)
        for kind in kinds:
            with self.subTest(kind=kind):
                source = supporting_source(kind=kind)
                legal = kind in self.policy.policy_source_kinds
                self.assertEqual(
                    self._rejects(permitted_states, {"policy_source": source}), not legal
                )
                self.assertEqual(
                    self._rejects(validate_record, self._record(policy_source=source)),
                    not legal,
                )

    def test_the_two_apis_agree_on_every_inv4_hard_case(self) -> None:
        """The band that matters: whenever the item is irreversible, carries ANY
        high-impact element, or reserves authority to the user, both APIs must refuse
        ASSUMPTION_ALLOWED. TR4-2 is the middle band (reversible_with_effort, or a
        wider blast radius with no high-impact flag), where they still disagree; it is
        reported rather than asserted. This test pins the dangerous half so INV-4 and
        the entry condition cannot drift apart where it counts."""
        flags = tuple(self.policy.assumption_allowed_forbidden_when["any_true_of"])
        # D4-F guard: the five high-impact flags INV-4 names.
        self.assertEqual(len(flags), 5)
        hard = [{"reversibility": "irreversible"}, {"explicit_user_authority": "reserved"}]
        hard += [{flag: True} for flag in flags]
        self.assertEqual(len(hard), 7)
        for facts in hard:
            with self.subTest(facts=sorted(facts)):
                permitted = permitted_states(
                    self.policy, {**facts, "policy_source": dict(self.SUPPORTS)}
                )
                self.assertNotIn("ASSUMPTION_ALLOWED", permitted)
                self.assertTrue(self._rejects(validate_record, self._record(**facts)))

    def test_a_safe_item_is_still_assumption_allowed_by_both(self) -> None:
        """POSITIVE CONTROL for the test above. Refusing every item would satisfy the
        hard-case sweep; it must not satisfy this."""
        facts = {
            "reversibility": "reversible_in_run",
            "blast_radius": self.policy.boundary_elements["blast_radius"].values[0],
        }
        self.assertIn(
            "ASSUMPTION_ALLOWED",
            permitted_states(self.policy, {**facts, "policy_source": dict(self.SUPPORTS)}),
        )
        self.assertFalse(self._rejects(validate_record, self._record(**facts)))


class Tr41PolicySourceRoleParity(DecisionPolicyTestCase):
    """TR4-1. `policy_source.role` membership was enforced by permitted_states() and
    not by validate_record(), so the evaluator rejected `invented_role` while record
    validation accepted the identical value. The check moved into
    `_validate_declared_facts`, beside the `kind` check that was always shared."""

    def _both(self, role: str) -> tuple[bool, bool]:
        record = assumption_allowed_record(
            self.policy, policy_source=supporting_source(role=role)
        )
        marker = "unknown policy_source role"
        return (
            marker in error_of(permitted_states, self.policy, record),
            marker in error_of(validate_record, self.policy, record),
        )

    def test_an_invented_role_is_rejected_by_both_apis(self) -> None:
        for role in ("invented_role", "determiness", "DETERMINES", " supports"):
            with self.subTest(role=role):
                self.assertEqual(self._both(role), (True, True))

    def test_every_legal_role_is_accepted_by_both_apis(self) -> None:
        """POSITIVE CONTROL: rejecting every role would satisfy the test above."""
        roles = sorted(self.policy.policy_source_roles)
        # D4-F guard, co-located.
        self.assertEqual(len(roles), 2)
        for role in roles:
            with self.subTest(role=role):
                self.assertEqual(self._both(role), (False, False))

    def test_role_membership_is_judged_by_the_shared_helper(self) -> None:
        """Structural: the rule is reached through _validate_declared_facts, so it
        cannot be enforced on one API only. Deleting the call from either side is a
        mutation both directions of the parity test above catch."""
        self.assertIn(
            "unknown policy_source role",
            error_of(
                _validate_declared_facts,
                self.policy,
                # RI9-1: a COMPLETE source with only the role wrong, so this test
                # fails on the role rather than on a missing locator.
                {"policy_source": supporting_source(role="invented_role")},
            ),
        )


class Tr42AssumptionAllowedHasOneNormativeRule(DecisionPolicyTestCase):
    """TR4-2. `permitted_states()` evaluated `entry_conditions` while
    `validate_record()` checked only `assumption_allowed_forbidden_when`, so the two
    disagreed on six of forty-eight boundary combinations, with record validation the
    permissive side.

    The NORMATIVE rule is the entry condition (ANALYSIS A3-1). INV-4 is a
    prohibition; "not forbidden" was never the same claim as "permitted". Both APIs
    now read `_entry_condition_defect`, and validate_record keeps the INV-4 check as
    well because INV-4 has no exception (A4-0, C9).
    """

    SUPPORTS = supporting_source()

    #: The six combinations the Reviewer enumerated, all with security false and no
    #: reserved authority. Each was permitted=False / record_valid=True before the fix.
    MIDDLE_BAND = (
        ("reversible_in_run", "repository"),
        ("reversible_in_run", "external_system"),
        ("reversible_with_effort", "current_change"),
        ("reversible_with_effort", "module"),
        ("reversible_with_effort", "repository"),
        ("reversible_with_effort", "external_system"),
    )

    def _both_permit(self, **facts) -> tuple[bool, bool]:
        record = assumption_allowed_record(self.policy, **facts)
        return (
            "ASSUMPTION_ALLOWED" in permitted_states(self.policy, record),
            error_of(validate_record, self.policy, record) == "",
        )

    def test_both_apis_refuse_every_middle_band_case(self) -> None:
        """Bidirectional, all six. Not merely consistent -- both must REFUSE, because
        the entry condition is the normative rule and none of these satisfies it."""
        # D4-F guard, co-located: the enumeration must not shrink silently.
        self.assertEqual(len(self.MIDDLE_BAND), 6)
        checked = 0
        for reversibility, blast_radius in self.MIDDLE_BAND:
            with self.subTest(reversibility=reversibility, blast_radius=blast_radius):
                self.assertEqual(
                    self._both_permit(
                        reversibility=reversibility, blast_radius=blast_radius
                    ),
                    (False, False),
                )
                checked += 1
        self.assertEqual(checked, 6)

    def test_both_apis_still_permit_the_safe_cases(self) -> None:
        """POSITIVE CONTROL. The fix must not narrow legitimate ASSUMPTION_ALLOWED:
        an item reversible within the run whose blast radius stays inside the
        requested scope is still permitted -- by BOTH APIs."""
        within_scope = ("current_change", "module")
        checked = 0
        for blast_radius in within_scope:
            with self.subTest(blast_radius=blast_radius):
                self.assertEqual(
                    self._both_permit(
                        reversibility="reversible_in_run", blast_radius=blast_radius
                    ),
                    (True, True),
                )
                checked += 1
        self.assertEqual(checked, 2)

    def test_both_apis_refuse_every_hard_case(self) -> None:
        """NEGATIVE CONTROL on the other side: irreversible, any of the five
        high-impact flags, or reserved authority. These agreed before the fix too;
        they must still agree, so the fix did not trade one divergence for another."""
        flags = tuple(self.policy.assumption_allowed_forbidden_when["any_true_of"])
        self.assertEqual(len(flags), 5)
        hard = [
            {"reversibility": "irreversible"},
            {"explicit_user_authority": "reserved"},
        ] + [{flag: True} for flag in flags]
        self.assertEqual(len(hard), 7)
        for facts in hard:
            with self.subTest(facts=sorted(facts)):
                self.assertEqual(self._both_permit(**facts), (False, False))

    def test_the_two_apis_agree_on_all_forty_eight_combinations(self) -> None:
        """The Reviewer's whole enumeration, re-run as a test. 48 combinations, zero
        disagreements -- and a co-located guard that the permitted set is neither
        empty (over-blocking) nor everything (no rule at all)."""
        reversibility = self.policy.boundary_elements["reversibility"].values
        blast_radius = self.policy.boundary_elements["blast_radius"].values
        # D4-F guard: 3 x 4 x 2 x 2 = 48.
        self.assertEqual(len(reversibility) * len(blast_radius) * 2 * 2, 48)
        permitted = 0
        checked = 0
        for rev in reversibility:
            for blast in blast_radius:
                for security in (False, True):
                    for authority in (None, "reserved"):
                        facts = {
                            "reversibility": rev,
                            "blast_radius": blast,
                            "security": security,
                        }
                        if authority:
                            facts["explicit_user_authority"] = authority
                        with self.subTest(**facts):
                            evaluator, validator = self._both_permit(**facts)
                            self.assertEqual(evaluator, validator)
                            permitted += evaluator
                            checked += 1
        self.assertEqual(checked, 48)
        # Anti-over-blocking and anti-vacuity in one assertion.
        self.assertEqual(permitted, 2)


class Tr43WhitespaceIsNotEvidence(DecisionPolicyTestCase):
    """TR4-3. `_is_empty` treated "   " as content, so a decision whose
    `where_recorded` was three spaces bought CLEAR. A field that must prove WHERE the
    decision is written down cannot be satisfied by blanks."""

    WHITESPACE = ("   ", "\t", "\n", " \t\n ")

    def test_whitespace_only_evidence_is_refused_by_both_apis(self) -> None:
        fields = list(self.policy.user_decision_fields)
        # D4-F guards, co-located: neither loop may be empty.
        self.assertEqual(len(fields), 3)
        self.assertEqual(len(self.WHITESPACE), 4)
        checked = 0
        for field in fields:
            for blank in self.WHITESPACE:
                with self.subTest(field=field, blank=repr(blank)):
                    decision = complete_decision()
                    decision[field] = blank
                    facts = {"security": True, "user_decision": decision}
                    self.assertNotIn("CLEAR", permitted_states(self.policy, facts))
                    with self.assertRaises(DecisionPolicyError):
                        validate_transition(
                            self.policy, "NEEDS_INPUT", "CLEAR", facts
                        )
                    checked += 1
        self.assertEqual(checked, 12)

    def test_real_evidence_is_still_accepted(self) -> None:
        """POSITIVE CONTROL: stripping must not reject text that merely contains
        spaces. `USER_DECISIONS.md#UD-1 (see note)` is legitimate."""
        for value in ("USER_DECISIONS.md#UD-1", " USER_DECISIONS.md#UD-1 ", "a b"):
            with self.subTest(value=value):
                decision = complete_decision()
                decision["where_recorded"] = value
                self.assertIn(
                    "CLEAR",
                    permitted_states(
                        self.policy, {"security": True, "user_decision": decision}
                    ),
                )

    def test_whitespace_is_empty_for_required_evidence_too(self) -> None:
        """The rule is one definition, not a user_decision special case: a required
        evidence field of blanks is empty for validate_record as well."""
        record = assumption_allowed_record(self.policy, impact="   ")
        self.assertIn("requires a non-empty", error_of(validate_record, self.policy, record))


class FactReadingApisShareEveryRule(DecisionPolicyTestCase):
    """The structural end of the FR-5 / TR4-1 / TR4-2 family.

    Three times now the same concept was judged in two places and answered
    differently: user-decision authorization (FR-5), `policy_source.role` membership
    (TR4-1), and ASSUMPTION_ALLOWED permission (TR4-2). Each time the cause was the
    same -- a rule written inline in one API instead of in a helper both call.

    `permitted_states()` and `validate_record()` both read declared boundary facts,
    so any rule about those facts must be reachable from both. This test compares
    their call closures. An inline judgement added to one and not the other changes
    one closure and fails here, which is a cheaper signal than enumerating the
    concepts again by hand.
    """

    @staticmethod
    def _closure(name: str) -> frozenset[str]:
        source = (REPO_ROOT / "scripts" / "decision_policy.py").read_text(
            encoding="utf-8"
        )
        tree = ast.parse(source)
        functions = {
            node.name: node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef)
        }

        def walk(target: str, seen: set[str]) -> set[str]:
            if target in seen or target not in functions:
                return seen
            seen.add(target)
            for node in ast.walk(functions[target]):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                    walk(node.func.id, seen)
            return seen

        return frozenset(walk(name, set()) & set(functions)) - {name}

    #: Rules validate_record() reaches that permitted_states() legitimately does not.
    #: These judge a RECORD -- whether its declared evidence justifies the state it
    #: claims -- which the evaluator cannot ask, having neither a reason code nor a
    #: claimed state. The set is pinned by name rather than allowed as a blanket
    #: asymmetry, so adding a rule here is a deliberate edit that shows in the diff
    #: instead of silently reopening the gap TR4-1 closed.
    RECORD_ONLY_RULES = frozenset({"_grounds_defect", "_triggering_text"})

    def test_both_fact_reading_apis_reach_the_same_helpers(self) -> None:
        evaluator = self._closure("permitted_states")
        validator = self._closure("validate_record")
        # D4-F guard: the closure must be non-trivial, or equality is vacuous.
        self.assertGreaterEqual(len(evaluator), 6)
        # The direction that matters, kept absolute: a rule about declared FACTS must
        # never be reachable from the evaluator alone. That is the TR4-1 shape.
        self.assertEqual(
            evaluator - validator,
            frozenset(),
            "a rule is reachable from permitted_states() but not validate_record(); "
            "that is how FR-5, TR4-1 and TR4-2 each began",
        )
        # And the other direction is bounded by the named list above, so a new
        # one-sided rule fails here even though the legitimate ones are admitted.
        self.assertEqual(
            validator - evaluator,
            self.RECORD_ONLY_RULES,
            "validate_record() reaches a rule that is neither shared with the "
            "evaluator nor declared as a record-only rule",
        )

    def test_the_evaluator_delegates_every_judgement(self) -> None:
        """Closes the half of the closure device's blind spot that matters.

        Executed check: an INLINE `_require` added to one API changes no name in its
        call closure, so `test_both_fact_reading_apis_reach_the_same_helpers` passes
        through it. The device catches a rule added as a HELPER CALL to one side, and
        that is where FR-5, TR4-1 and TR4-2 each actually lived -- but the claim is
        narrower than "any judgement".

        For `permitted_states` the gap can be closed outright: it reads nothing but
        declared facts, so it has no legitimate inline rule and today has exactly
        zero. Pinning that means a fact rule cannot be added to the evaluator alone,
        by helper or inline. The mirror is deliberately NOT asserted:
        `validate_record` has twelve inline `_require` calls that are record-SHAPE
        rules -- reason_code/state agreement, citation minimum, evidence presence --
        which are not about declared facts and have no business in the evaluator.
        """
        source = (REPO_ROOT / "scripts" / "decision_policy.py").read_text(
            encoding="utf-8"
        )
        functions = {
            node.name: node
            for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.FunctionDef)
        }
        body = functions["permitted_states"]
        inline = [
            node
            for node in ast.walk(body)
            if isinstance(node, ast.Raise)
            or (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "_require"
            )
        ]
        self.assertEqual(
            inline,
            [],
            "permitted_states() judges declared facts inline instead of through a "
            "helper validate_record() also calls -- the TR4-1 shape",
        )
        # POSITIVE CONTROL: the AST walk must actually be able to see such a node,
        # or the assertion above is satisfied by a broken query rather than by
        # clean code.
        probe = ast.parse("def f():\n    _require(x, 'y')\n    raise E('z')\n")
        found = [
            node
            for node in ast.walk(probe)
            if isinstance(node, ast.Raise)
            or (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "_require"
            )
        ]
        self.assertEqual(len(found), 2)

    def test_the_shared_helpers_include_every_fact_rule(self) -> None:
        """Names the rules explicitly, so deleting one from both APIs at once -- which
        equality alone would not notice -- still fails."""
        shared = self._closure("permitted_states")
        for helper in (
            "_validate_declared_facts",
            "_entry_condition_defect",
            "_evaluate_predicate",
            "_user_decision_defect",
            "_assumption_allowed_is_forbidden",
            "_element_is_triggering",
            "_is_empty",
        ):
            with self.subTest(helper=helper):
                self.assertIn(helper, shared)


class Fr6DeclaredEvidenceMustJustifyTheState(DecisionPolicyTestCase):
    """FR-6. validate_record() checked the reason code, the state, the required fields
    and the boundary element's NAME -- FR-3 made that name an exact match -- but never
    the VALUE behind the name. `security_impact` filed with `security: false` was
    accepted: a pause_and_ask record for a boundary that never fired, and no way for a
    Reviewer to reject the misclassification from the machine-readable contract.

    The triggering test is `_element_is_triggering`, the same helper permitted_states()
    reaches through `_evaluate_predicate`, so the two cannot answer "did this element
    fire?" differently.
    """

    #: The eight variants reproduced against the shipped fixtures, each flipping the
    #: element its reason code rests on to a value that does NOT fire.
    NON_TRIGGERING = (
        ("security_impact", "security", False),
        ("privacy_impact", "privacy", False),
        ("monetary_cost", "monetary_cost", False),
        ("compliance_impact", "compliance", False),
        ("long_term_lock_in", "long_term_lock_in", False),
        ("irreversible_action", "reversibility", "reversible_in_run"),
        ("blast_radius_beyond_scope", "blast_radius", "current_change"),
        # RI8-1: for this element the domain is now the triggering set, so a
        # non-triggering value is also an out-of-domain value. Both are rejections,
        # and the coincidence is the point -- see the parity assertion below.
        ("authority_reserved_to_user", "explicit_user_authority", "delegated"),
    )

    def _rejects(self, record) -> bool:
        return error_of(validate_record, self.policy, record) != ""

    def test_a_non_triggering_boundary_value_is_rejected(self) -> None:
        # D4-F guard, co-located: the enumeration must not shrink silently.
        self.assertEqual(len(self.NON_TRIGGERING), 8)
        checked = 0
        for fixture, element, value in self.NON_TRIGGERING:
            with self.subTest(fixture=fixture):
                record = dict(load_fixture(f"valid/{fixture}.json"))
                record[element] = value
                self.assertTrue(
                    self._rejects(record),
                    f"{fixture} was accepted with {element}={value!r}, a boundary "
                    "that never fired",
                )
                checked += 1
        self.assertEqual(checked, 8)

    def test_every_shipped_valid_fixture_still_passes(self) -> None:
        """POSITIVE CONTROL, and the anti-over-blocking guard. Refusing more is not a
        fix if it refuses records the contract calls valid."""
        directory = FIXTURES / "valid"
        fixtures = sorted(directory.glob("*.json"))
        # D4-F guard: all eighteen, or the loop is not proving what it claims.
        self.assertEqual(len(fixtures), 18)
        for path in fixtures:
            with self.subTest(fixture=path.stem):
                self.assertEqual(
                    error_of(validate_record, self.policy, load_fixture(f"valid/{path.name}")),
                    "",
                )

    def test_the_boundary_element_must_be_declared_at_all(self) -> None:
        """Omitting the element is the same defect as declaring it false: a pause
        cannot rest on an element the record never asserts."""
        for fixture, element, _ in self.NON_TRIGGERING:
            with self.subTest(fixture=fixture):
                record = {
                    key: value
                    for key, value in load_fixture(f"valid/{fixture}.json").items()
                    if key != element
                }
                self.assertTrue(self._rejects(record))

    def test_a_code_binding_no_boundary_element_is_still_accepted(self) -> None:
        """The deliberate exception: `unclassifiable_decision` binds no element, and
        `ambiguity` is declared BY being named (A4-1 row 1), so neither carries a
        separate value to check."""
        self.assertFalse(self._rejects(load_fixture("valid/unclassifiable_decision.json")))
        self.assertFalse(self._rejects(load_fixture("valid/ambiguous_requirement.json")))
        self.assertFalse(self._rejects(load_fixture("valid/missing_user_intent.json")))
        # But a value that IS present and false is still wrong.
        record = dict(load_fixture("valid/ambiguous_requirement.json"))
        record["ambiguity"] = False
        self.assertTrue(self._rejects(record))

    def test_clear_grounds_must_satisfy_the_clear_entry_condition(self) -> None:
        """Requirement 4. Declared grounds are judged; declaring none stays valid,
        because required_evidence[CLEAR] is empty and UD-1 keeps the section
        optional."""
        self.assertEqual(self.policy.required_evidence["CLEAR"], ())
        good = (
            {"state": "CLEAR"},
            {"state": "CLEAR", "open_decision_item": False},
            {"state": "CLEAR", "policy_source": determining_source()},
            {"state": "CLEAR", "user_decision": complete_decision()},
        )
        bad = (
            {"state": "CLEAR", "policy_source": supporting_source()},
            {"state": "CLEAR", "user_decision": {"source": "explicit_user_reply"}},
            {"state": "CLEAR", "user_decision": complete_decision("timeout")},
        )
        # D4-F guards, co-located.
        self.assertEqual((len(good), len(bad)), (4, 3))
        for record in good:
            with self.subTest(good=sorted(record)):
                self.assertFalse(self._rejects(record))
        for record in bad:
            with self.subTest(bad=sorted(record)):
                self.assertTrue(self._rejects(record))

    def test_a_conflict_record_may_not_declare_another_codes_clause(self) -> None:
        """Requirement 3. A3-1a fixed three distinct clauses; filing C-1 evidence
        under a C-3 code is a misclassification the contract can now reject."""
        base = {
            "state": "CONFLICT",
            "reason_code": "requirement_contradiction",
            "citations": ["OS-28#req-a", "OS-28#req-b"],
            "why_they_cannot_both_hold": "satisfying either falsifies the other",
        }
        clauses = list(self.policy.entry_clauses["CONFLICT"])
        # D4-F guard.
        self.assertEqual(len(clauses), 3)
        own = self.policy.reason_codes["requirement_contradiction"].clause
        self.assertFalse(self._rejects(base))  # positive: declaring none is fine
        self.assertFalse(self._rejects({**base, "conflict_clause": own}))
        for clause in clauses:
            if clause == own:
                continue
            with self.subTest(clause=clause):
                self.assertTrue(self._rejects({**base, "conflict_clause": clause}))

    def test_the_triggering_test_is_the_evaluators_own(self) -> None:
        """Parity, on identical input: for each element the evaluator treats as
        triggering, a record resting on it is accepted, and vice versa. The two must
        not develop separate opinions about what 'fired' means."""
        checked = 0
        for fixture, element, non_triggering in self.NON_TRIGGERING:
            with self.subTest(element=element):
                spec = self.policy.boundary_elements[element]
                record = dict(load_fixture(f"valid/{fixture}.json"))
                fired = _element_is_triggering(spec, record[element])
                self.assertTrue(fired)
                self.assertFalse(self._rejects(record))
                record[element] = non_triggering
                self.assertFalse(_element_is_triggering(spec, non_triggering))
                self.assertTrue(self._rejects(record))
                checked += 1
        self.assertEqual(checked, 8)


class Fr7EveryBoundCodeIsJudgedByValueNotName(DecisionPolicyTestCase):
    """FR-7. The suite injected a boundary-element NAME mismatch for all ten bound
    codes (FR-3) but never a non-triggering VALUE or a missing boundary fact. That is
    why 1426 tests and 642 checks were green while FR-6 was live: the tests had the
    same defect as the code they were guarding -- membership checked, value not.

    Every case here is derived from the contract rather than listed by hand, so a new
    bound code is covered the day it is added instead of the day someone remembers to
    extend a tuple.
    """

    def _non_triggering(self, spec) -> object:
        """A value the CONTRACT says does not fire, derived from its own spec."""
        if spec.kind == "enum":
            return next(v for v in spec.values if v not in tuple(spec.triggering))
        if spec.triggering is True:
            return False
        if isinstance(spec.triggering, (list, tuple)):
            return f"not_{spec.triggering[0]}"
        raise AssertionError(f"no non-triggering value derivable for {spec.kind}")

    def _bound(self) -> dict:
        return {
            name: code.boundary_element
            for name, code in self.policy.reason_codes.items()
            if code.boundary_element is not None
        }

    def test_every_bound_code_accepts_its_shipped_triggering_value(self) -> None:
        """(a) POSITIVE CONTROL for all ten, so the negatives below cannot be
        satisfied by rejecting everything."""
        bound = self._bound()
        # D4-F guard: 10 of the 11 NEEDS_INPUT codes bind an element.
        self.assertEqual(len(bound), 10)
        checked = 0
        for name, element in sorted(bound.items()):
            with self.subTest(reason_code=name):
                record = load_fixture(f"valid/{name}.json")
                spec = self.policy.boundary_elements[element]
                if element in record:
                    # The shipped value must actually be a triggering one, or the
                    # positive control proves nothing about triggering.
                    self.assertTrue(_element_is_triggering(spec, record[element]))
                self.assertEqual(error_of(validate_record, self.policy, record), "")
                checked += 1
        self.assertEqual(checked, 10)

    def test_every_bound_code_rejects_a_non_triggering_value(self) -> None:
        """(b) The negative FR-7 found missing, for all ten -- including the two
        `ambiguity` codes, where a value that IS present and false is still wrong."""
        bound = self._bound()
        self.assertEqual(len(bound), 10)
        checked = 0
        for name, element in sorted(bound.items()):
            with self.subTest(reason_code=name):
                spec = self.policy.boundary_elements[element]
                value = self._non_triggering(spec)
                # Anti-vacuity: the injected value must really not fire, or the
                # rejection below could be for some unrelated reason.
                self.assertFalse(_element_is_triggering(spec, value))
                record = dict(load_fixture(f"valid/{name}.json"))
                record[element] = value
                self.assertNotEqual(
                    error_of(validate_record, self.policy, record),
                    "",
                    f"{name} accepted with {element}={value!r}, which does not fire",
                )
                checked += 1
        self.assertEqual(checked, 10)

    def test_an_absent_boundary_fact_is_judged_by_the_elements_kind(self) -> None:
        """The other half of (b), and NOT uniform -- which is the point.

        For a value-carrying element, omitting the fact is the same defect as
        declaring it false: the pause rests on an element the record never asserts.
        For a `declared` element, A4-1 row 1 makes naming it in `boundary_element` the
        declaration itself, so absence is CORRECT and must stay accepted. Asserting
        one rule for both would be over-blocking dressed as coverage.
        """
        bound = self._bound()
        declared_kind = {
            name: element
            for name, element in bound.items()
            if self.policy.boundary_elements[element].kind == "declared"
        }
        value_carrying = {
            name: element for name, element in bound.items() if name not in declared_kind
        }
        # D4-F guards, co-located: both partitions non-empty and summing to ten.
        self.assertEqual(len(declared_kind), 2)
        self.assertEqual(len(value_carrying), 8)
        self.assertEqual(len(declared_kind) + len(value_carrying), len(bound))

        for name, element in sorted(value_carrying.items()):
            with self.subTest(reason_code=name, kind="value-carrying"):
                record = {
                    key: value
                    for key, value in load_fixture(f"valid/{name}.json").items()
                    if key != element
                }
                self.assertNotEqual(error_of(validate_record, self.policy, record), "")
        for name, element in sorted(declared_kind.items()):
            with self.subTest(reason_code=name, kind="declared"):
                record = load_fixture(f"valid/{name}.json")
                self.assertNotIn(element, record)
                self.assertEqual(error_of(validate_record, self.policy, record), "")


class Fr7ConflictClauseAndCitationsBidirectional(DecisionPolicyTestCase):
    """FR-7 item 2. A3-1a fixed three distinct clauses and a citation minimum; the
    suite checked neither direction of the code/clause link on a record."""

    def _base(self, name: str) -> dict:
        return dict(load_fixture(f"valid/{name}.json"))

    def _conflict_codes(self) -> dict:
        return {
            name: code.clause
            for name, code in self.policy.reason_codes.items()
            if code.state == "CONFLICT"
        }

    def test_each_conflict_code_accepts_its_own_clause_and_rejects_the_others(
        self,
    ) -> None:
        codes = self._conflict_codes()
        clauses = list(self.policy.entry_clauses["CONFLICT"])
        # D4-F guards: three codes, three clauses, one clause each.
        self.assertEqual(len(codes), 3)
        self.assertEqual(len(clauses), 3)
        self.assertEqual(sorted(codes.values()), sorted(clauses))
        accepted = rejected = 0
        for name, own in sorted(codes.items()):
            record = self._base(name)
            with self.subTest(reason_code=name, clause=own):
                # POSITIVE: its own clause, and declaring none at all.
                self.assertEqual(
                    error_of(validate_record, self.policy, {**record, "conflict_clause": own}), ""
                )
                self.assertEqual(error_of(validate_record, self.policy, record), "")
                accepted += 2
            for other in clauses:
                if other == own:
                    continue
                with self.subTest(reason_code=name, wrong_clause=other):
                    self.assertNotEqual(
                        error_of(
                            validate_record, self.policy, {**record, "conflict_clause": other}
                        ),
                        "",
                    )
                    rejected += 1
        self.assertEqual((accepted, rejected), (6, 6))

    def test_the_citation_minimum_is_enforced_at_its_boundary(self) -> None:
        """Bidirectional on the count itself: exactly the minimum is accepted, one
        fewer is rejected. A test that only checked zero citations would pass against
        a minimum of one."""
        minimum = self.policy.citation_minimum["CONFLICT"]
        self.assertEqual(minimum, 2)
        codes = sorted(self._conflict_codes())
        self.assertEqual(len(codes), 3)
        for name in codes:
            record = self._base(name)
            citations = list(record["citations"])
            self.assertGreaterEqual(len(citations), minimum)
            with self.subTest(reason_code=name, citations=minimum):
                self.assertEqual(
                    error_of(
                        validate_record, self.policy, {**record, "citations": citations[:minimum]}
                    ),
                    "",
                )
            with self.subTest(reason_code=name, citations=minimum - 1):
                self.assertNotEqual(
                    error_of(
                        validate_record,
                        self.policy,
                        {**record, "citations": citations[: minimum - 1]},
                    ),
                    "",
                )


class Fr7ClearGroundsBothWays(DecisionPolicyTestCase):
    """FR-7 item 3. FR-6 made CLEAR judge its declared grounds; this covers each
    accepting ground and its nearest rejecting neighbour, derived from the contract's
    own CLEAR entry predicates."""

    def test_each_clear_entry_predicate_has_a_satisfying_record(self) -> None:
        """POSITIVE, one per predicate, so the negatives cannot be met by refusing
        every CLEAR record."""
        (_, predicates), = self.policy.entry_conditions["CLEAR"].items()
        satisfying = {
            "no_open_decision_item": {"state": "CLEAR", "open_decision_item": False},
            "determining_policy_source": {
                "state": "CLEAR",
                "policy_source": determining_source(),
            },
            "explicit_user_authorization": {
                "state": "CLEAR",
                "user_decision": complete_decision(),
            },
        }
        # D4-F guard: every predicate the contract declares is exercised.
        self.assertEqual(set(satisfying), set(predicates))
        self.assertEqual(len(predicates), 3)
        for predicate, record in sorted(satisfying.items()):
            with self.subTest(predicate=predicate):
                self.assertEqual(error_of(validate_record, self.policy, record), "")

    def test_each_near_miss_is_rejected(self) -> None:
        """NEGATIVE: grounds that look like the real thing and are not. Each one is a
        claim of CLEAR the evaluator would refuse."""
        near_miss = {
            "supporting_not_determining": {
                "state": "CLEAR",
                "policy_source": supporting_source(),
            },
            "source_only_decision": {
                "state": "CLEAR",
                "user_decision": {"source": "explicit_user_reply"},
            },
            "forbidden_authority_source": {
                "state": "CLEAR",
                "user_decision": complete_decision("timeout"),
            },
            "open_item_still_open": {"state": "CLEAR", "open_decision_item": True},
        }
        # D4-F guard, co-located.
        self.assertEqual(len(near_miss), 4)
        for label, record in sorted(near_miss.items()):
            with self.subTest(near_miss=label):
                self.assertNotEqual(error_of(validate_record, self.policy, record), "")

    def test_declaring_no_grounds_at_all_remains_valid(self) -> None:
        """UD-1 and the empty required_evidence: a CLEAR record need not carry
        grounds. This is the anti-over-blocking control for the class."""
        self.assertEqual(self.policy.required_evidence["CLEAR"], ())
        self.assertEqual(error_of(validate_record, self.policy, {"state": "CLEAR"}), "")


class Fr8BooleanBoundariesFailClosed(DecisionPolicyTestCase):
    """FR-8. `enum` elements had a membership check; `boolean` elements had no
    counterpart, so any non-boolean value simply "did not fire" and the boundary was
    bypassed. `security: 'yes'` and `security: 1` -- both plainly true to a reader --
    left an irreversible, security-relevant item reporting ASSUMPTION_ALLOWED. A
    contract that fails closed everywhere else was failing OPEN on a malformed value:
    the wrong input bought autonomy instead of a pause.
    """

    SAFE = {
        "reversibility": "reversible_in_run",
        "blast_radius": "current_change",
        "policy_source": supporting_source(),
    }

    #: The seven values reproduced in the finding, each of which returned
    #: ['ASSUMPTION_ALLOWED'] before the fix.
    NON_BOOLEAN = ("yes", 1, 0, {"a": 1}, None, "false", [])

    def _boolean_elements(self) -> list:
        return sorted(
            name
            for name, spec in self.policy.boundary_elements.items()
            if spec.kind == "boolean"
        )

    def test_a_non_boolean_value_is_rejected_for_every_boolean_element(self) -> None:
        elements = self._boolean_elements()
        # D4-F guards, co-located: five booleans, seven values, both non-empty.
        self.assertEqual(len(elements), 5)
        self.assertEqual(len(self.NON_BOOLEAN), 7)
        checked = 0
        for element in elements:
            for value in self.NON_BOOLEAN:
                with self.subTest(element=element, value=repr(value)):
                    facts = {**self.SAFE, element: value}
                    with self.assertRaises(DecisionPolicyError):
                        permitted_states(self.policy, facts)
                    checked += 1
        self.assertEqual(checked, 35)

    def test_one_and_zero_are_rejected_rather_than_read_as_true_and_false(self) -> None:
        """`isinstance(True, int)` is why bool is tested before int. 1 and 0 are
        exactly the values that made this fail open, so they get their own assertion
        rather than living only inside the sweep above."""
        for value in (1, 0):
            with self.subTest(value=value):
                with self.assertRaises(DecisionPolicyError):
                    permitted_states(self.policy, {**self.SAFE, "security": value})

    def test_real_booleans_still_work_in_both_directions(self) -> None:
        """POSITIVE CONTROL. Rejecting every value would satisfy the sweep above; it
        must not satisfy this. True fires the boundary, False does not."""
        elements = self._boolean_elements()
        self.assertEqual(len(elements), 5)
        for element in elements:
            with self.subTest(element=element):
                fired = permitted_states(self.policy, {**self.SAFE, element: True})
                self.assertNotIn("ASSUMPTION_ALLOWED", fired)
                self.assertIn("NEEDS_INPUT", fired)
                safe = permitted_states(self.policy, {**self.SAFE, element: False})
                self.assertIn("ASSUMPTION_ALLOWED", safe)

    def test_omitting_the_element_remains_legal(self) -> None:
        """The other positive control, and the line the fix must not cross: not
        declaring something and declaring it wrongly are different acts."""
        self.assertIn(
            "ASSUMPTION_ALLOWED", permitted_states(self.policy, dict(self.SAFE))
        )
        for element in self._boolean_elements():
            with self.subTest(element=element):
                self.assertNotIn(element, self.SAFE)

    def test_both_apis_reject_a_malformed_value_identically(self) -> None:
        """Parity on identical input -- the property that failed four times in this
        run. Each mapping is handed to both APIs unchanged."""
        record = assumption_allowed_record(self.policy)
        checked = 0
        for element in self._boolean_elements():
            for value in self.NON_BOOLEAN:
                with self.subTest(element=element, value=repr(value)):
                    candidate = {**record, element: value}
                    evaluator = error_of(permitted_states, self.policy, candidate) != ""
                    validator = error_of(validate_record, self.policy, candidate) != ""
                    self.assertTrue(evaluator)
                    self.assertEqual(evaluator, validator)
                    checked += 1
        self.assertEqual(checked, 35)

    def test_every_element_kind_has_a_stated_domain_disposition(self) -> None:
        """The exhaustive half of FR-8: no element kind may be left unconsidered.
        Every kind the contract uses is either domain-checked or listed here as
        declaring no domain -- a new kind fails this until someone decides which."""
        kinds = {spec.kind for spec in self.policy.boundary_elements.values()}
        checked_kinds = {"enum", "boolean", "declared", "citations"}
        open_kinds = {"policy_source", "user_decision"}
        # D4-F guard: the partition must cover exactly the kinds in use.
        self.assertEqual(kinds, checked_kinds | open_kinds)
        self.assertFalse(checked_kinds & open_kinds)
        for name, spec in sorted(self.policy.boundary_elements.items()):
            if spec.kind not in checked_kinds:
                continue
            with self.subTest(element=name, kind=spec.kind):
                bad = object()  # outside every declared domain
                self.assertIsNotNone(_domain_defect(spec, bad))

    def test_a_bogus_conflict_clause_is_rejected(self) -> None:
        """The contract declares this domain in entry_clauses, so an unknown clause is
        rejected rather than quietly making the item look non-contradictory."""
        known = list(self.policy.entry_clauses["CONFLICT"])
        self.assertEqual(len(known), 3)
        with self.assertRaises(DecisionPolicyError):
            permitted_states(self.policy, {"conflict_clause": "C-9"})
        for clause in known:  # POSITIVE CONTROL
            with self.subTest(clause=clause):
                self.assertIn(
                    "CONFLICT", permitted_states(self.policy, {"conflict_clause": clause})
                )


class Ri81AuthorityBoundaryFailsClosed(DecisionPolicyTestCase):
    """RI8-1. `explicit_user_authority` carries the user's reservation of authority --
    the boundary this ticket exists to protect -- and it was fail-open: `'RESERVED'`,
    one shifted key, returned ASSUMPTION_ALLOWED for a reserved item. A typo silently
    removed the reservation and allowed autonomous progress.

    I recorded this in iteration 8 as an accepted residual limit, arguing that closing
    the domain would reject `delegated`, "the legitimate non-reserved case". That was
    wrong on the facts: `delegated` occurs zero times in either SKILL.md and zero times
    in ANALYSIS.md -- my example, reasoned from as though the contract had defined it.
    """

    SAFE = {
        "reversibility": "reversible_in_run",
        "blast_radius": "current_change",
        "policy_source": supporting_source(),
    }
    ELEMENT = "explicit_user_authority"
    BAD = ("RESERVED", "Reserved", "reserverd", "anything", "delegated", 1, {"a": 1}, None, [])

    def test_only_the_declared_triggering_value_is_admitted(self) -> None:
        spec = self.policy.boundary_elements[self.ELEMENT]
        # D4-F guards, co-located: the domain is the contract's own triggering list,
        # and it is a single value -- if the contract ever declares more, this fails
        # rather than silently admitting them untested.
        self.assertEqual(list(spec.triggering), ["reserved"])
        self.assertEqual(len(self.BAD), 9)
        checked = 0
        for value in self.BAD:
            with self.subTest(value=repr(value)):
                with self.assertRaises(DecisionPolicyError):
                    permitted_states(self.policy, {**self.SAFE, self.ELEMENT: value})
                checked += 1
        self.assertEqual(checked, 9)

    def test_the_declared_value_still_reserves_authority(self) -> None:
        """POSITIVE CONTROL 1. Rejecting everything would satisfy the test above."""
        permitted = permitted_states(
            self.policy, {**self.SAFE, self.ELEMENT: "reserved"}
        )
        self.assertEqual(permitted, frozenset({"NEEDS_INPUT"}))

    def test_omitting_the_element_remains_the_way_to_say_not_reserved(self) -> None:
        """POSITIVE CONTROL 2, and the reason closing the domain loses nothing: the
        case iteration 8 thought needed an open domain already had a representation."""
        self.assertNotIn(self.ELEMENT, self.SAFE)
        self.assertIn(
            "ASSUMPTION_ALLOWED", permitted_states(self.policy, dict(self.SAFE))
        )

    def test_both_apis_reject_an_unrecognised_authority_value_identically(self) -> None:
        """Parity on identical input -- the property that has failed repeatedly in this
        run. The same mapping is handed to both APIs unchanged."""
        record = assumption_allowed_record(self.policy)
        for value in self.BAD:
            with self.subTest(value=repr(value)):
                candidate = {**record, self.ELEMENT: value}
                evaluator = error_of(permitted_states, self.policy, candidate) != ""
                validator = error_of(validate_record, self.policy, candidate) != ""
                self.assertTrue(evaluator)
                self.assertEqual(evaluator, validator)

    def test_the_authority_domain_equals_its_triggering_set(self) -> None:
        """After RI8-1 these two coincide for this element, which is what makes an
        unrecognised value impossible to read as 'not reserved'. Stated as its own
        assertion because other tests describe the same values as 'non-triggering' and
        the reader should know why both descriptions are now true."""
        spec = self.policy.boundary_elements[self.ELEMENT]
        for value in tuple(spec.triggering):
            with self.subTest(value=value):
                self.assertIsNone(_domain_defect(spec, value))
                self.assertTrue(_element_is_triggering(spec, value))
        self.assertIsNotNone(_domain_defect(spec, "not_reserved"))

    def test_an_element_that_cannot_fire_is_left_open_deliberately(self) -> None:
        """The judgement recorded for `repository_project_policy`, pinned so it is a
        decision and not an oversight: its `triggering` is null, so no value can make
        it fire and therefore no value can suppress a firing. A domain check would
        protect nothing. If the contract ever gives it a triggering value, this fails
        and the decision gets revisited."""
        spec = self.policy.boundary_elements["repository_project_policy"]
        self.assertEqual(spec.kind, "policy_source")
        self.assertIsNone(spec.triggering)
        self.assertFalse(_element_is_triggering(spec, "anything"))
        self.assertIsNone(_domain_defect(spec, "anything"))
        # The boundary it names is enforced through the policy_source OBJECT instead,
        # and both of those positions ARE closed.
        self.assertTrue(self.policy.policy_source_roles)
        self.assertTrue(self.policy.policy_source_kinds)


class Ri91LocatorShapeIsEnforced(DecisionPolicyTestCase):
    """RI9-1. A `policy_source` could claim policy supports a decision while pointing
    nowhere: a missing `locator`, `""`, whitespace, a number, or null all returned
    ASSUMPTION_ALLOWED. That defeats the ticket's requirement that an automatic
    decision records the policy it applied.

    Iterations 8 and 9 left this open because "checking a locator needs I/O and this
    layer is a pure function". That was half right, and bundling the halves cost both:
    the SHAPE -- non-empty text -- needs no I/O. Only EXISTENCE does.
    """

    SAFE = {"reversibility": "reversible_in_run", "blast_radius": "current_change"}
    BAD_LOCATORS = ("", "   ", "\t", 42, None, {"a": 1}, [], True)

    def test_a_source_without_a_locator_is_rejected(self) -> None:
        source = {"role": "supports", "kind": "file_path"}
        self.assertNotIn("locator", source)
        with self.assertRaises(DecisionPolicyError):
            permitted_states(self.policy, {**self.SAFE, "policy_source": source})

    def test_every_malformed_locator_is_rejected(self) -> None:
        # D4-F guard, co-located.
        self.assertEqual(len(self.BAD_LOCATORS), 8)
        checked = 0
        for value in self.BAD_LOCATORS:
            with self.subTest(locator=repr(value)):
                with self.assertRaises(DecisionPolicyError):
                    permitted_states(
                        self.policy,
                        {**self.SAFE, "policy_source": supporting_source(locator=value)},
                    )
                checked += 1
        self.assertEqual(checked, 8)

    def test_real_locators_still_pass(self) -> None:
        """POSITIVE CONTROL, using the locators the shipped fixtures actually carry --
        so this cannot drift away from what the repository really files."""
        real = [
            source["locator"]
            for source in (
                load_fixture(f"valid/{name}.json").get("policy_source")
                for name in sorted(self.policy.reason_codes)
            )
            if isinstance(source, dict)
        ]
        # D4-F guard: the four fixtures that declare a policy_source.
        self.assertEqual(len(real), 4)
        for locator in real + ["docs/x.md", " padded/but/real.md "]:
            with self.subTest(locator=locator):
                self.assertIn(
                    "ASSUMPTION_ALLOWED",
                    permitted_states(
                        self.policy,
                        {**self.SAFE, "policy_source": supporting_source(locator=locator)},
                    ),
                )

    def test_both_apis_judge_the_locator_identically(self) -> None:
        """Parity on identical input."""
        record = assumption_allowed_record(self.policy)
        for value in self.BAD_LOCATORS:
            with self.subTest(locator=repr(value)):
                candidate = {**record, "policy_source": supporting_source(locator=value)}
                evaluator = error_of(permitted_states, self.policy, candidate) != ""
                validator = error_of(validate_record, self.policy, candidate) != ""
                self.assertTrue(evaluator)
                self.assertEqual(evaluator, validator)

    def test_existence_is_not_checked_and_is_not_claimed(self) -> None:
        """The half that genuinely needs I/O. A locator pointing at nothing is ACCEPTED
        -- this layer is a pure function of (contract, declared facts) and performs no
        filesystem access. Asserted so the limit is a recorded decision rather than an
        assumption a reader might make in either direction."""
        self.assertIn(
            "ASSUMPTION_ALLOWED",
            permitted_states(
                self.policy,
                {
                    **self.SAFE,
                    "policy_source": supporting_source(
                        locator="no/such/file/anywhere.md#nope"
                    ),
                },
            ),
        )


class Ri91SweptShapeGaps(DecisionPolicyTestCase):
    """The same shape-versus-existence lens applied to every other text position.

    Two more were found. Both had been described in my iteration-8 table as "non-empty
    text" while only the emptiness half was enforced -- the claim was wider than the
    check, which is the recurring failure of this run.
    """

    def _decision(self, **overrides) -> dict:
        return {**complete_decision(), **overrides}

    def test_user_decision_text_fields_must_be_text(self) -> None:
        fields = list(self.policy.user_decision_fields)
        non_text = (42, True, {"a": 1}, ["x"], 3.5)
        # D4-F guards, co-located.
        self.assertEqual(len(fields), 3)
        self.assertEqual(len(non_text), 5)
        checked = 0
        for field in fields:
            for value in non_text:
                with self.subTest(field=field, value=repr(value)):
                    facts = {
                        "security": True,
                        "user_decision": self._decision(**{field: value}),
                    }
                    self.assertNotIn("CLEAR", permitted_states(self.policy, facts))
                    with self.assertRaises(DecisionPolicyError):
                        validate_transition(self.policy, "NEEDS_INPUT", "CLEAR", facts)
                    checked += 1
        self.assertEqual(checked, 15)

    def test_a_real_decision_is_still_accepted(self) -> None:
        """POSITIVE CONTROL for the sweep above."""
        facts = {"security": True, "user_decision": complete_decision()}
        self.assertIn("CLEAR", permitted_states(self.policy, facts))

    def test_conflict_citations_must_each_be_text(self) -> None:
        base = load_fixture("valid/requirement_contradiction.json")
        real = list(base["citations"])
        self.assertGreaterEqual(len(real), 2)
        for citations in ([1, 2], [1, real[1]], [[], []], ["   ", real[1]]):
            with self.subTest(citations=repr(citations)):
                with self.assertRaises(DecisionPolicyError):
                    validate_record(self.policy, {**base, "citations": citations})
        # POSITIVE CONTROL: the shipped citations still validate.
        self.assertEqual(error_of(validate_record, self.policy, base), "")

    def test_an_element_that_cannot_fire_has_nothing_to_check(self) -> None:
        """`repository_project_policy` re-examined under the same lens rather than
        carried forward. It has no declared domain, `triggering` is null so no value
        can make it fire, and no reason code binds it -- so no value it could carry
        changes any decision. Its machine-checkable half is the `policy_source`
        OBJECT, whose kind, role and now locator are all enforced."""
        spec = self.policy.boundary_elements["repository_project_policy"]
        self.assertIsNone(spec.triggering)
        self.assertEqual(spec.values, ())
        bound = {
            code.boundary_element
            for code in self.policy.reason_codes.values()
            if code.boundary_element
        }
        self.assertNotIn("repository_project_policy", bound)
        self.assertFalse(_element_is_triggering(spec, "anything"))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
