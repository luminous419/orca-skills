"""OS-42: the schema projection, the generated contract, and the defect classifier.

Every test names the mutation it catches. A test that passes against a deliberately
broken implementation is worthless, so each one is written to fail if the specific
behaviour it guards is removed.
"""
from __future__ import annotations

import ast
import json
import unittest
from pathlib import Path

from scripts import decision_contract, decision_gate
from scripts.decision_policy import DecisionPolicyError

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = REPO_ROOT / "scripts" / "fixtures" / "decision_gate"

# The verbatim value that ended run_8e8f9451ad44 -- a natural-language sentence in a
# closed-enum field. This ticket exists because of this string.
OS42_DEFECT_VALUE = (
    "Fully reversible: this phase wrote exactly one new artifact "
    "(artifacts/runs/run_8e8f9451ad44/ANALYSIS.md) and modified no tracked file, "
    "no production code, and no pre-existing run or artifact."
)

VALID_RECORD = {
    "ledger_schema_version": 1, "boundary": "B2", "source": "worker", "role": "worker",
    "run": "run_x", "phase": "ANALYSIS", "iteration": 1,
    "responsible_phase": "ANALYSIS", "state": "CLEAR", "reason_code": None,
    "open_decision_item": False, "open_item": None, "assumption": None, "evidence": {},
    "verdict": "", "source_binding": "artifacts/runs/run_x/",
    "recorded_at": "2026-01-01T00:00:00+00:00", "prior_open_decision_items": [],
}


def body_for(record: dict, *, state: str | None = "CLEAR", fences: int = 1,
             declarations: int = 1) -> str:
    lines = ["# Worker Result", ""]
    for _ in range(declarations):
        lines.append(f"{decision_gate.GATE_STATE_FIELD}: {state}")
    lines.append("")
    for _ in range(fences):
        lines.append(decision_gate.GATE_RECORD_FENCE)
        lines.append(json.dumps(record))
        lines.append("```")
        lines.append("")
    return "\n".join(lines)


class ProjectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.policy = decision_contract.resolve_policy()
        self.projection = decision_contract.contract_projection(self.policy)

    def test_projection_is_derived_not_transcribed(self) -> None:
        """Mutating a source constant must change the projection.

        Catches a projection that hard-codes what it should read.
        """
        self.assertEqual(self.projection.states, decision_gate.DECISION_STATES)
        self.assertEqual(self.projection.required_fields,
                         decision_gate.REQUIRED_LEDGER_RECORD_FIELDS)
        self.assertEqual(set(self.projection.closed_key_set),
                         set(decision_gate.CLOSED_LEDGER_RECORD_FIELDS))
        self.assertEqual(self.projection.ledger_schema_version,
                         decision_gate.LEDGER_RECORD_SCHEMA_VERSION)
        reversibility = next(f for f in self.projection.machine_control
                             if f.name == "reversibility")
        self.assertEqual(reversibility.values,
                         self.policy.boundary_elements["reversibility"].values)

    def test_generated_block_contains_every_closed_enum_value(self) -> None:
        """Schema-to-generated-instruction parity.

        Catches an enum edited in the policy and not regenerated, and a renderer that
        drops a field. It is the twelfth Required Test.
        """
        block = decision_contract.render_worker_contract(
            self.projection, run_id="run_x", phase="ANALYSIS", iteration=1, role="worker")
        for token in self.projection.enum_tokens():
            self.assertIn(token, block, f"generated block omits enum token {token!r}")

    def test_renderer_contains_no_enum_string_literal(self) -> None:
        """"Generated from the schema, never hand-duplicated", made machine-checkable.

        Catches a renderer that hard-codes a token instead of interpolating it -- which
        is exactly how the contract and the prompt drift apart.
        """
        source = (REPO_ROOT / "scripts" / "decision_contract.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        render = next(node for node in ast.walk(tree)
                      if isinstance(node, ast.FunctionDef)
                      and node.name == "render_worker_contract")
        literals = {node.value for node in ast.walk(render)
                    if isinstance(node, ast.Constant) and isinstance(node.value, str)}
        tokens = set(self.projection.enum_tokens())
        # The four state names are the ONE exception: they are read through
        # `projection.states`, never written, so they must not appear either.
        self.assertEqual(literals & tokens, set(),
                         "the renderer hard-codes an enum token instead of deriving it")

    def test_render_is_pure(self) -> None:
        """Same arguments, same bytes. Catches a clock or any I/O in the renderer."""
        first = decision_contract.render_worker_contract(
            self.projection, run_id="run_x", phase="PLAN", iteration=2, role="reviewer")
        second = decision_contract.render_worker_contract(
            self.projection, run_id="run_x", phase="PLAN", iteration=2, role="reviewer")
        self.assertEqual(first, second)

    def test_role_determines_boundary(self) -> None:
        """B2 for a worker, B3 for a reviewer -- derived, never passed.

        Catches a caller pairing a role with the wrong boundary.
        """
        worker = decision_contract.render_worker_contract(
            self.projection, run_id="run_x", phase="PLAN", iteration=1, role="worker")
        reviewer = decision_contract.render_worker_contract(
            self.projection, run_id="run_x", phase="PLAN", iteration=1, role="reviewer")
        self.assertIn("boundary='B2'", worker)
        self.assertIn("boundary='B3'", reviewer)


class ClassifierTests(unittest.TestCase):
    def setUp(self) -> None:
        self.policy = decision_contract.resolve_policy()

    def classify(self, record, **kwargs):
        envelope = decision_contract.extract_gate_envelope(body_for(record, **kwargs))
        return decision_contract.classify_gate(self.policy, envelope, role="WORKER")

    # ---- Required Test 1: each of the three valid reversibility values -------------
    def test_each_valid_reversibility_value_passes(self) -> None:
        """Parametrised over ALL THREE. A single-value test would not notice two of
        them being lost from the enum."""
        allowed = self.policy.boundary_elements["reversibility"].values
        self.assertEqual(len(allowed), 3)
        for value in allowed:
            with self.subTest(reversibility=value):
                record = dict(VALID_RECORD, reversibility=value)
                defects = self.classify(record)
                # No FORM defect names the field: the TOKEN is accepted. `irreversible`
                # additionally carries a judgement consequence, which is a SEMANTIC
                # matter and deliberately not this assertion's business -- conflating
                # the two is exactly the error this ticket removes.
                form = [d for d in defects
                        if d.kind == "FORM" and d.field_path == "reversibility"]
                self.assertEqual(form, [], f"{value!r} was rejected as a form defect")

    # ---- Required Test 2: the natural-language enum substitute ---------------------
    def test_natural_language_reversibility_is_a_FORM_defect(self) -> None:
        """The OS-42 defect itself, verbatim.

        Catches classifying it SEMANTIC (so it is never repaired) or accepting it
        (fail-open). Also asserts the payload the terminal reason needs.
        """
        defects = self.classify(dict(VALID_RECORD, reversibility=OS42_DEFECT_VALUE))
        self.assertEqual(len(defects), 1)
        defect = defects[0]
        self.assertEqual(defect.kind, "FORM")
        self.assertEqual(defect.field_path, "reversibility")
        self.assertEqual(set(defect.expected),
                         set(self.policy.boundary_elements["reversibility"].values))
        self.assertIn("Fully reversible", defect.actual)

    # ---- Required Test 3: unknown enum --------------------------------------------
    def test_unknown_enum_reports_the_allowed_values(self) -> None:
        """Catches a payload without `expected`, which would make the terminal reason
        unable to name the allowed set the ticket requires."""
        defects = self.classify(dict(VALID_RECORD, blast_radius="galaxy"))
        self.assertEqual(len(defects), 1)
        self.assertEqual(defects[0].kind, "FORM")
        self.assertEqual(defects[0].field_path, "blast_radius")
        self.assertEqual(set(defects[0].expected),
                         set(self.policy.boundary_elements["blast_radius"].values))

    # ---- Required Test 4: missing field -------------------------------------------
    def test_missing_required_field_is_a_FORM_defect(self) -> None:
        """Parametrised over EVERY required field. A single-field test would not
        notice REQUIRED_LEDGER_RECORD_FIELDS being shortened."""
        for field in decision_gate.REQUIRED_LEDGER_RECORD_FIELDS:
            if field == "state":
                continue          # removing `state` is a different defect (S3/S4)
            with self.subTest(field=field):
                record = {k: v for k, v in VALID_RECORD.items() if k != field}
                defects = self.classify(record)
                self.assertTrue(defects)
                self.assertTrue(all(d.kind == "FORM" for d in defects))
                self.assertIn(field, {d.field_path for d in defects})

    # ---- Required Test 5: extra key ------------------------------------------------
    def test_key_outside_the_closed_set_is_a_FORM_defect(self) -> None:
        """Catches widening CLOSED_LEDGER_RECORD_FIELDS."""
        defects = self.classify(dict(VALID_RECORD, smuggled_key="x"))
        self.assertTrue(defects)
        self.assertEqual(defects[0].kind, "FORM")
        self.assertEqual(defects[0].field_path, "smuggled_key")

    # ---- Required Test 6: null ------------------------------------------------------
    def test_required_field_present_but_null_is_a_FORM_defect(self) -> None:
        """Catches treating None as "absent, therefore legal"."""
        defects = self.classify(dict(VALID_RECORD, verdict=None))
        self.assertTrue(defects)
        self.assertEqual(defects[0].kind, "FORM")
        self.assertEqual(defects[0].field_path, "verdict")

    def test_legitimately_nullable_required_fields_are_not_defects(self) -> None:
        """`reason_code` is null for CLEAR by contract. Catches a null check that is
        too eager and would reject every valid CLEAR record."""
        self.assertEqual(self.classify(dict(VALID_RECORD)), ())

    # ---- Required Test 7: wrong type -------------------------------------------------
    def test_wrong_type_is_a_FORM_defect(self) -> None:
        """Includes "true" and 1 for a boolean.

        `1` is the important one: `isinstance(True, int)` is True, so a check that
        tested int-ness before bool-ness would let 1 pass as true. Catches reverting
        `_domain_defect`'s bool-before-int ordering.
        """
        for field, value in (("security", "true"), ("security", 1),
                             ("privacy", 0), ("citations", {"a": 1})):
            with self.subTest(field=field, value=value):
                defects = self.classify(dict(VALID_RECORD, **{field: value}))
                self.assertTrue(defects, f"{field}={value!r} was accepted")
                self.assertTrue(all(d.kind == "FORM" for d in defects))

    def test_a_boolean_where_an_integer_is_required_is_a_FORM_defect(self) -> None:
        """The half of "wrong type" the sibling test above does not reach.

        `test_wrong_type_is_a_FORM_defect` covers a string or an int arriving where a
        BOOLEAN belongs.  The opposite direction is the one that actually needs the
        guard: `isinstance(True, int)` is True in Python, so a plain
        `isinstance(value, expected_type)` check silently accepts `true` for every
        integer field in the record -- `iteration` included, which is the field the whole
        repair ordinal is counted on.

        Verified by mutation: replacing the bool-before-int branch in
        `collect_form_defects` with a plain isinstance check leaves the existing suite
        entirely green, which is why this test exists.  Parametrised over every declared
        integer field so shortening `LEDGER_FIELD_TYPES` cannot hide one.
        """
        integer_fields = [name for name, expected, _ in decision_gate.LEDGER_FIELD_TYPES
                          if expected is int]
        self.assertTrue(integer_fields, "no integer field is declared to protect")
        for field in integer_fields:
            for value in (True, False):
                with self.subTest(field=field, value=value):
                    defects = self.classify(dict(VALID_RECORD, **{field: value}))
                    self.assertTrue(defects, f"{field}={value!r} was accepted as an integer")
                    self.assertTrue(all(d.kind == "FORM" for d in defects))
                    self.assertIn(field, {d.field_path for d in defects})

    def test_classify_gate_never_returns_a_mixed_form_and_semantic_result(self) -> None:
        """The invariant the repair route's safety actually rests on.

        `route_node` admits a repair only when every defect is FORM, which is the right
        rule.  But the reason a semantic block can never be laundered into a repair is
        upstream of that: `classify_gate` returns the FORM sweep immediately, so a
        judgement is only ever reached once the record is formally clean, and one result
        can never carry both kinds.

        Nothing asserted that.  Without it, a future change that gathered all defects
        instead of returning early would silently make the executor's `kinds == {"FORM"}`
        the ONLY thing standing between a NEEDS_INPUT and a repair dispatch.
        """
        blocking = dict(VALID_RECORD, state="NEEDS_INPUT",
                        reason_code="authority_reserved_to_user",
                        boundary_element="the deployment target",
                        what_is_missing="which environment the user means",
                        why_policy_cannot_decide="the policy reserves this to the user")
        cases = {
            "semantic only": blocking,
            "semantic state + bad enum": dict(blocking, reversibility=OS42_DEFECT_VALUE),
            "semantic state + extra key": dict(blocking, smuggled_key=1),
            "semantic state + null field": dict(blocking, verdict=None),
            "semantic state + wrong type": dict(blocking, iteration="1"),
        }
        for label, record in cases.items():
            with self.subTest(case=label):
                defects = self.classify(record, state="NEEDS_INPUT")
                kinds = {d.kind for d in defects}
                self.assertLessEqual(
                    len(kinds), 1,
                    f"classify_gate returned a mixed result {kinds}; the repair route "
                    "would then have to be the only thing keeping a semantic block out")
                if "FORM" in kinds:
                    # A formal defect masks the judgement for now; the record is re-asked
                    # and the block is reached on the next, formally clean, settlement.
                    self.assertEqual(kinds, {"FORM"})

    def test_all_form_defects_are_reported_at_once(self) -> None:
        """Three independent form errors must cost ONE repair attempt, not three.

        Catches a sweep that returns after the first defect, which would exhaust a
        budget of two without the agent ever seeing the full list.
        """
        record = dict(VALID_RECORD, reversibility=OS42_DEFECT_VALUE,
                      security="true", smuggled_key=1)
        defects = self.classify(record)
        self.assertGreaterEqual(len(defects), 3)
        self.assertTrue(all(d.kind == "FORM" for d in defects))

    # ---- Required Test 11: semantic vs format ----------------------------------------
    def test_needs_input_is_SEMANTIC_never_FORM(self) -> None:
        """A legitimate semantic block must never enter the repair branch."""
        record = dict(
            VALID_RECORD, state="NEEDS_INPUT", reason_code="security_impact",
            boundary_element="security", what_is_missing="whether this is authorised",
            why_policy_cannot_decide="no policy source determines it", security=True)
        defects = self.classify(record, state="NEEDS_INPUT")
        self.assertTrue(defects)
        self.assertTrue(all(d.kind == "SEMANTIC" for d in defects))

    def test_conflict_is_SEMANTIC_never_FORM(self) -> None:
        record = dict(
            VALID_RECORD, state="CONFLICT", reason_code="requirement_contradiction",
            citations=["req A", "req B"],
            why_they_cannot_both_hold="A forbids what B requires")
        defects = self.classify(record, state="CONFLICT")
        self.assertTrue(defects)
        self.assertTrue(all(d.kind == "SEMANTIC" for d in defects))

    def test_inv4_violation_is_SEMANTIC_never_FORM(self) -> None:
        """INV-4 is a JUDGEMENT. Labelling it FORM would re-ask the agent for a more
        convenient answer -- the semantic laundering the ticket forbids."""
        record = dict(
            VALID_RECORD, state="ASSUMPTION_ALLOWED", reason_code="phase_contract",
            policy_source={"kind": "phase_contract_section", "locator": "x",
                           "role": "supports"},
            reversibility="irreversible", impact="p", retraction_condition="q",
            blast_radius="repository", monetary_cost=False, security=False,
            privacy=False, compliance=False, long_term_lock_in=False)
        defects = self.classify(record, state="ASSUMPTION_ALLOWED")
        self.assertTrue(defects)
        self.assertTrue(all(d.kind == "SEMANTIC" for d in defects))

    def test_undeclared_safety_fact_is_SEMANTIC_never_FORM(self) -> None:
        record = dict(
            VALID_RECORD, state="ASSUMPTION_ALLOWED", reason_code="phase_contract",
            policy_source={"kind": "phase_contract_section", "locator": "x",
                           "role": "supports"},
            reversibility="reversible_in_run", impact="p", retraction_condition="q")
        defects = self.classify(record, state="ASSUMPTION_ALLOWED")
        self.assertTrue(defects)
        self.assertTrue(all(d.kind == "SEMANTIC" for d in defects))

    # ---- structure defects -----------------------------------------------------------
    def test_missing_declaration_or_fence_is_a_FORM_defect(self) -> None:
        for kwargs in ({"declarations": 0}, {"fences": 0}, {"declarations": 2},
                       {"fences": 2}):
            with self.subTest(**kwargs):
                defects = self.classify(dict(VALID_RECORD), **kwargs)
                self.assertTrue(defects)
                self.assertEqual(defects[0].kind, "FORM")

    def test_absent_envelope_is_a_FORM_defect(self) -> None:
        """"The agent said nothing" is repairable, never an integrity failure and never
        presumed CLEAR."""
        defects = decision_contract.classify_gate(self.policy, None, role="WORKER")
        self.assertEqual(len(defects), 1)
        self.assertEqual(defects[0].kind, "FORM")
        self.assertEqual(defects[0].code, decision_gate.GATE_INPUT_MISSING)

    def test_declaration_disagreeing_with_the_record_is_a_FORM_defect(self) -> None:
        body = body_for(dict(VALID_RECORD, state="ASSUMPTION_ALLOWED"), state="CLEAR")
        defects = decision_contract.classify_gate(
            self.policy, decision_contract.extract_gate_envelope(body), role="WORKER")
        self.assertEqual(len(defects), 1)
        self.assertEqual(defects[0].code, decision_gate.SUMMARY_DISAGREES_WITH_RECORD)
        self.assertEqual(defects[0].kind, "FORM")


class TaxonomyTests(unittest.TestCase):
    def test_every_refusal_reason_has_an_explicit_kind(self) -> None:
        """A refusal reason with no taxonomy entry falls through to LIFECYCLE and is
        never repaired. This turns that silent safety into a loud one: a new reason
        added without a decision fails HERE.
        """
        classified = (decision_gate.FORM_DEFECT_CODES
                      | decision_gate.SEMANTIC_DEFECT_CODES
                      | decision_gate.LIFECYCLE_DEFECT_CODES)
        self.assertEqual(set(decision_gate.GATE_REFUSAL_REASONS), classified)

    def test_the_three_sets_are_disjoint(self) -> None:
        """Catches a code in two buckets, which would make `defect_kind` order-dependent."""
        self.assertEqual(decision_gate.FORM_DEFECT_CODES
                         & decision_gate.SEMANTIC_DEFECT_CODES, frozenset())
        self.assertEqual(decision_gate.FORM_DEFECT_CODES
                         & decision_gate.LIFECYCLE_DEFECT_CODES, frozenset())
        self.assertEqual(decision_gate.SEMANTIC_DEFECT_CODES
                         & decision_gate.LIFECYCLE_DEFECT_CODES, frozenset())

    def test_an_unknown_code_defaults_to_LIFECYCLE(self) -> None:
        """THE fail-closed default. Catches a default of FORM, which would make every
        future unclassified refusal silently repairable."""
        self.assertEqual(decision_gate.defect_kind("A_CODE_NOBODY_CLASSIFIED"),
                         "LIFECYCLE")

    def test_a_decision_block_reason_is_SEMANTIC(self) -> None:
        self.assertEqual(
            decision_gate.defect_kind("DECISION_BLOCKED:NEEDS_INPUT:security_impact"),
            "SEMANTIC")

    def test_defect_keys_carry_no_suggestion_shaped_field(self) -> None:
        """The structural half of "the Coordinator never infers a value": there is
        nowhere for a candidate to travel."""
        forbidden = {"suggestion", "candidate", "recommended", "value", "fix"}
        self.assertEqual(set(decision_gate.DEFECT_KEYS) & forbidden, set())

    def test_defect_as_dict_is_checkpointable(self) -> None:
        """The list is written into WorkflowState, so every value must survive
        `state._checkpointable`, which admits only dict/list/bool/int/str/None."""
        defect = decision_gate.GateDefect(
            code="C", kind="FORM", field_path="f", expected=("a", "b"), actual="x",
            message="m")
        payload = defect.as_dict()
        self.assertEqual(set(payload), set(decision_gate.DEFECT_KEYS))
        self.assertIsInstance(payload["expected"], list)
        json.dumps(payload)


class NegativeFixtureTests(unittest.TestCase):
    """The fixtures the ticket's input classes require, exercised through the classifier."""

    EXPECTED = {
        "reversibility_natural_language.json": "reversibility",
        "boolean_as_string.json": "security",
        "extra_key_outside_closed_set.json": "not_a_contract_key",
        "required_field_null.json": "verdict",
        "iteration_wrong_type.json": "iteration",
    }

    def setUp(self) -> None:
        self.policy = decision_contract.resolve_policy()

    def test_each_negative_fixture_is_a_FORM_defect_on_the_named_field(self) -> None:
        for name, field in self.EXPECTED.items():
            with self.subTest(fixture=name):
                record = json.loads((FIXTURES / "invalid" / name).read_text("utf-8"))
                envelope = decision_contract.extract_gate_envelope(
                    body_for(record, state=record.get("state")))
                defects = decision_contract.classify_gate(
                    self.policy, envelope, role="WORKER")
                self.assertTrue(defects, f"{name} was accepted")
                self.assertTrue(all(d.kind == "FORM" for d in defects), name)
                self.assertIn(field, {d.field_path for d in defects}, name)

    def test_every_closed_enum_has_a_negative_fixture(self) -> None:
        """A future enum cannot be added without a negative fixture for it."""
        projection = decision_contract.contract_projection(self.policy)
        enums = {f.name for f in projection.machine_control if f.kind == "enum"}
        covered = set()
        for name in self.EXPECTED.values():
            covered.add(name)
        # `reversibility` and `blast_radius` are the two closed enums; blast_radius is
        # covered by ClassifierTests.test_unknown_enum_reports_the_allowed_values.
        self.assertTrue(enums, "the projection declares no closed enum at all")
        self.assertIn("reversibility", covered)


class ParserTransportTests(unittest.TestCase):
    """The parser TRANSPORTS; it never classifies. This is what lets the defect reach
    the validator instead of dying in the adapter."""

    class _Attempt:
        def __init__(self, body: str) -> None:
            self.body = body

    def test_parser_transports_and_never_raises_for_a_gate_defect(self) -> None:
        for body in ("", "no fence here", "```decision-gate\nnot json\n```",
                     body_for(dict(VALID_RECORD, reversibility=OS42_DEFECT_VALUE))):
            with self.subTest(body=body[:24]):
                result = decision_contract.parse_agent_settlement(
                    self._Attempt(body), {})
                self.assertIn("gate", result)
                self.assertEqual(set(result["gate"]),
                                 set(decision_contract.GATE_ENVELOPE_KEYS))

    def test_unparseable_fence_preserves_the_text(self) -> None:
        result = decision_contract.parse_agent_settlement(
            self._Attempt("```decision-gate\n{not json}\n```"), {})
        self.assertIsNone(result["gate"]["record"])
        self.assertEqual(result["gate"]["record_text"], "{not json}")
        self.assertEqual(result["gate"]["fence_count"], 1)

    def test_a_json_body_still_parses_exactly_as_before(self) -> None:
        """Catches a parser change that breaks every scripted path."""
        result = decision_contract.parse_agent_settlement(
            self._Attempt(json.dumps({"status": "COMPLETE"})), {})
        self.assertEqual(result["status"], "COMPLETE")

    def test_markdown_status_is_read_from_the_field_line(self) -> None:
        body = "# Worker Result\n\nSTATUS: COMPLETE\n" + body_for(VALID_RECORD)
        result = decision_contract.parse_agent_settlement(self._Attempt(body), {})
        self.assertEqual(result["status"], "COMPLETE")


class PolicyResolutionTests(unittest.TestCase):
    def test_a_missing_policy_fails_closed(self) -> None:
        """No default policy and no silent skip: "we could not read the contract" must
        never read the same as "the contract was satisfied"."""
        with self.assertRaises(decision_contract.DecisionPolicyRequired):
            decision_contract.resolve_policy(REPO_ROOT / "does-not-exist")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
