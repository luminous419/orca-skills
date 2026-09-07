"""OS-42 F-001: the mechanics identity of a settlement record, at the VALIDATOR.

The finding this file answers is precise: `classify_gate` accepted its expected `role`
and immediately executed `del role`, and no classification path ever called
`decision_gate.record_identity_defect()` or bound the record to the active dispatch. A
Worker settlement could therefore declare the Reviewer's identity `B3/reviewer/reviewer`,
the impossible combination `B2/reviewer/worker`, or the unknown boundary `B9`, and the
production classifier returned no defect at all.

Every test here submits a MISMATCHED RECORD DIRECTLY TO THE VALIDATOR, or drives one
through the real `VALIDATE_SETTLEMENT` -> `APPLY_RESULT` -> `ROUTE` nodes. None of them
inspects a string the generator emitted: that is exactly the class of test the Final
Adversarial Review found insufficient (`test_role_determines_boundary` proves only that
the generated instructions mention B2/B3).

The FORM/LIFECYCLE split asserted here is the contract:

* a mechanics field whose VALUE is outside its own closed domain -- an unknown boundary,
  a source that is not a source, a schema version this build does not support -- is a
  correctable format error and classifies FORM, so the existing bounded repair path can
  act on it exactly as it does for `reversibility`.
* a triple whose three fields are each in-domain but which is not a real record
  identity, or which IS a real identity but not THIS dispatch's, is identity forgery and
  classifies LIFECYCLE. `routing.route` never repairs a LIFECYCLE defect, so such a
  record fails closed and is never repaired into acceptance.
"""
from __future__ import annotations

import json
import unittest
from copy import deepcopy

from scripts import decision_contract, decision_gate, run_logging
from scripts.deterministic_workflow import artifact_identity
from scripts.deterministic_workflow.contracts import (BASE_CAPABILITIES,
                                                      MAX_REPAIR_ATTEMPTS,
                                                      make_settlement_event)
from scripts.deterministic_workflow.executor import (apply_result_node,
                                                     prepare_intent_node, route_node,
                                                     terminal_node,
                                                     validate_settlement_node)
from scripts.deterministic_workflow.state import initial_state
from scripts.test_orca_runtime_contract import OfflineHarnessTestCase, RecordingExec

RUN = "run_os42"
PHASE = "ANALYSIS"

# The record a WORKER dispatch of run_os42/ANALYSIS/1 is contractually required to send.
WORKER_RECORD = {
    "ledger_schema_version": 1, "boundary": "B2", "source": "worker", "role": "worker",
    "run": RUN, "phase": PHASE, "iteration": 1, "responsible_phase": PHASE,
    "state": "CLEAR", "reason_code": None, "open_decision_item": False,
    "open_item": None, "assumption": None, "evidence": {}, "verdict": "",
    "source_binding": f"artifacts/runs/{RUN}/",
    "recorded_at": "2026-01-01T00:00:00+00:00", "prior_open_decision_items": [],
}
# The same record wearing the Reviewer's identity. Every other field is untouched, so a
# defect reported for it can only come from the mechanics identity.
REVIEWER_IDENTITY = {"boundary": "B3", "source": "reviewer", "role": "reviewer"}

BINDING = {"run": RUN, "phase": PHASE.lower(), "iteration": 1}


def envelope_for(record, *, state="CLEAR"):
    return {"declared_state": state, "declaration_count": 1, "fence_count": 1,
            "record": deepcopy(record), "record_text": None, "truncated": False}


class ValidatorIdentityTests(unittest.TestCase):
    """The direct half: mismatched records handed straight to `classify_gate`."""

    def setUp(self) -> None:
        self.policy = decision_contract.resolve_policy()

    def classify(self, record, *, role="WORKER", binding=None, state="CLEAR"):
        return decision_contract.classify_gate(
            self.policy, envelope_for(record, state=state), role=role, binding=binding)

    # ---- the three records the Final Reviewer drove through and got nothing for -----

    def test_a_worker_settlement_declaring_the_reviewer_identity_fails_closed(self) -> None:
        """Finding F-001, case 1, verbatim.

        Catches the `del role` this ticket exists to remove: with the parameter
        discarded this record is indistinguishable from a clean one.
        """
        defects = self.classify(dict(WORKER_RECORD, **REVIEWER_IDENTITY))
        self.assertEqual(len(defects), 1, "the forged Reviewer identity was accepted")
        self.assertEqual(defects[0].kind, "LIFECYCLE")
        self.assertEqual(defects[0].code, decision_gate.GATE_INPUT_UNBOUND)

    def test_an_impossible_boundary_source_role_combination_fails_closed(self) -> None:
        """Finding F-001, case 2: `B2/reviewer/worker`.

        Each field is individually legal, so a field-by-field check passes it. Only the
        relational rule in `decision_gate.record_identity_defect` rejects it, which is
        the validator the classification path never called.
        """
        defects = self.classify(dict(WORKER_RECORD, source="reviewer"))
        self.assertEqual(len(defects), 1)
        self.assertEqual(defects[0].kind, "LIFECYCLE")
        self.assertEqual(defects[0].code, decision_gate.GATE_INPUT_UNBOUND)

    def test_an_unknown_boundary_is_a_repairable_FORM_defect(self) -> None:
        """Finding F-001, case 3: `B9`.

        FORM, not LIFECYCLE: an out-of-domain enum token in a mechanics field is the
        same correctable class as an out-of-domain `reversibility`, and the ticket
        requires correctable mechanics format errors to reach the bounded repair path.
        The defect must carry the COMPLETE allowed set, or the repair instruction cannot
        name it.
        """
        defects = self.classify(dict(WORKER_RECORD, boundary="B9"))
        self.assertEqual(len(defects), 1, "the unknown boundary was accepted")
        self.assertEqual(defects[0].kind, "FORM")
        self.assertEqual(defects[0].field_path, "boundary")
        self.assertEqual(set(defects[0].expected), set(decision_gate.BOUNDARIES))

    # ---- the same split, swept over every mechanics field ---------------------------

    def test_every_mechanics_field_outside_its_closed_domain_is_a_FORM_defect(self) -> None:
        """Parametrised, so shortening the mechanics sweep to one field fails here."""
        cases = {"boundary": decision_gate.BOUNDARIES,
                 "source": decision_gate.SOURCES,
                 "role": decision_gate.ROLES}
        for field, domain in cases.items():
            with self.subTest(field=field):
                defects = self.classify(dict(WORKER_RECORD, **{field: "not_a_token"}))
                self.assertTrue(defects, f"{field}='not_a_token' was accepted")
                self.assertTrue(all(d.kind == "FORM" for d in defects))
                named = [d for d in defects if d.field_path == field]
                self.assertTrue(named, f"no defect names {field!r}")
                self.assertEqual(set(named[0].expected), set(domain))

    def test_an_absent_mechanics_field_is_a_FORM_defect(self) -> None:
        """`boundary`, `source` and `ledger_schema_version` are NOT members of
        `REQUIRED_LEDGER_RECORD_FIELDS`, so `collect_form_defects` never noticed them
        missing. A record with no boundary at all claims no identity and must not pass.
        """
        for field in ("ledger_schema_version", "boundary", "source", "role"):
            with self.subTest(field=field):
                record = {k: v for k, v in WORKER_RECORD.items() if k != field}
                defects = self.classify(record)
                self.assertTrue(defects, f"a record with no {field!r} was accepted")
                self.assertTrue(all(d.kind == "FORM" for d in defects))
                self.assertIn(field, {d.field_path for d in defects})

    def test_an_unsupported_ledger_schema_version_is_a_FORM_defect(self) -> None:
        """A version this build cannot read is a correctable declaration, not forgery."""
        for value in (2, "1", True):
            with self.subTest(value=value):
                defects = self.classify(dict(WORKER_RECORD, ledger_schema_version=value))
                self.assertTrue(defects, f"ledger_schema_version={value!r} was accepted")
                self.assertIn("ledger_schema_version", {d.field_path for d in defects})
                self.assertTrue(all(d.kind == "FORM" for d in defects))

    # ---- the role parameter is READ, in both directions -----------------------------

    def test_the_expected_role_changes_the_verdict(self) -> None:
        """THE anti-`del role` test.

        One record, two expected roles, opposite outcomes. A classifier that discards
        its `role` cannot make this pass, and neither can one that hard-codes B2.
        """
        worker_record = dict(WORKER_RECORD)
        reviewer_record = dict(WORKER_RECORD, **REVIEWER_IDENTITY)
        self.assertEqual(self.classify(worker_record, role="WORKER"), ())
        self.assertTrue(self.classify(worker_record, role="PHASE_REVIEWER"))
        self.assertEqual(self.classify(reviewer_record, role="PHASE_REVIEWER"), ())
        self.assertTrue(self.classify(reviewer_record, role="WORKER"))

    def test_every_dispatch_role_alias_expects_its_own_identity(self) -> None:
        """Swept over all four agent role spellings the engine and the harness use."""
        for role in ("WORKER", "worker", "PHASE_REVIEWER", "FINAL_REVIEWER"):
            with self.subTest(role=role):
                boundary, source, record_role = decision_contract.expected_identity(role)
                good = dict(WORKER_RECORD, boundary=boundary, source=source,
                            role=record_role)
                self.assertEqual(self.classify(good, role=role), ())
                other = ("B3", "reviewer", "reviewer") if boundary == "B2" else (
                    "B2", "worker", "worker")
                forged = dict(WORKER_RECORD, boundary=other[0], source=other[1],
                              role=other[2])
                defects = self.classify(forged, role=role)
                self.assertEqual(len(defects), 1)
                self.assertEqual(defects[0].kind, "LIFECYCLE")

    def test_an_unknown_dispatch_role_fails_closed_and_never_raises(self) -> None:
        """`classify_gate` returns defects; it does not raise into VALIDATE_SETTLEMENT.

        Catches a fix that resolves the expected identity with a bare dict lookup.
        """
        defects = decision_contract.classify_gate(
            self.policy, envelope_for(dict(WORKER_RECORD)), role="ARCHITECT")
        self.assertTrue(defects)
        self.assertEqual(defects[0].kind, "LIFECYCLE")

    # ---- the binding half -----------------------------------------------------------

    def test_a_record_bound_to_another_context_fails_closed(self) -> None:
        """run / phase / iteration each bind the record to the ACTIVE dispatch.

        CORRECTED taxonomy (round-2 F-001). This test previously REQUIRED these three to
        classify FORM, which made `route_node` PREPARE REPAIR for a record claiming
        another run, another phase or another gate iteration -- an identity claim being
        re-asked instead of refused. `run` and `phase` have no closed enum the schema
        declares and `iteration` is any one-based ordinal, so every WELL-FORMED value is
        a claim about WHICH dispatch produced the record. A claim that names a different
        dispatch is forgery, not a transcription slip, and must fail closed.

        Catches a fix that closes the boundary/source/role hole and leaves the binding
        half repairable.
        """
        cases = {"run": "run_somewhere_else", "phase": "PLAN", "iteration": 7}
        for field, value in cases.items():
            with self.subTest(field=field):
                defects = self.classify(dict(WORKER_RECORD, **{field: value}),
                                        binding=BINDING)
                self.assertTrue(defects, f"{field}={value!r} was accepted")
                self.assertTrue(
                    all(d.kind == "LIFECYCLE" for d in defects),
                    f"a foreign {field} is repairable: {[d.as_dict() for d in defects]}")
                self.assertEqual({d.code for d in defects},
                                 {decision_gate.GATE_INPUT_UNBOUND})
                self.assertIn(field, {d.field_path for d in defects})

    def test_an_out_of_domain_binding_value_stays_a_repairable_FORM_defect(self) -> None:
        """The other side of the same line, so neither can be moved without failing.

        `iteration: 0` is not a foreign dispatch -- it is outside the one-based ordinal
        domain entirely, i.e. a format error. A wrongly-TYPED `run` or `phase` is the
        same class and is owned by the record's own type sweep. All of these stay FORM
        and stay repairable; only a well-formed value naming another dispatch fails
        closed.
        """
        for field, value in (("iteration", 0), ("iteration", -3),
                             ("run", 17), ("phase", ["PLAN"])):
            with self.subTest(field=field, value=value):
                defects = self.classify(dict(WORKER_RECORD, **{field: value}),
                                        binding=BINDING)
                self.assertTrue(defects, f"{field}={value!r} was accepted")
                self.assertTrue(
                    all(d.kind == "FORM" for d in defects),
                    f"an out-of-domain {field} is not repairable: "
                    f"{[d.as_dict() for d in defects]}")
                self.assertIn(field, {d.field_path for d in defects})

    def test_an_unknown_boundary_token_is_never_reclassified_as_forgery(self) -> None:
        """The pair the review names explicitly: `B9` is out-of-domain and repairable,
        `B3` on a Worker settlement is an identity claim and fails closed.

        `record_identity_defect` reports BOTH as "not one of RECORD_IDENTITIES", so a
        fix that simply forwards its verdict would call the unknown token forgery.
        """
        unknown = self.classify(dict(WORKER_RECORD, boundary="B9"), binding=BINDING)
        self.assertTrue(all(d.kind == "FORM" for d in unknown))
        foreign = self.classify(dict(WORKER_RECORD, **REVIEWER_IDENTITY), binding=BINDING)
        self.assertTrue(all(d.kind == "LIFECYCLE" for d in foreign))

    def test_the_identity_triple_and_its_field_names_stay_aligned(self) -> None:
        """`expected_identity()` returns a positional triple and the comparison zips it
        against `MECHANICS_IDENTITY_FIELDS`. Reordering either would silently compare
        `boundary` against the expected `source`, which no behavioural test catches
        because both are wrong together only for some inputs.
        """
        self.assertEqual(decision_contract.MECHANICS_IDENTITY_FIELDS,
                         ("boundary", "source", "role"))
        for role, expected in (("WORKER", ("B2", "worker", "worker")),
                               ("PHASE_REVIEWER", ("B3", "reviewer", "reviewer"))):
            with self.subTest(role=role):
                self.assertEqual(decision_contract.expected_identity(role), expected)
                self.assertIn(expected, decision_gate.AGENT_TERMINAL_IDENTITIES)

    def test_the_ingress_and_the_engine_reach_the_same_verdict(self) -> None:
        """CORRECTED (round-3 F-001). One contract, one answer.

        `mechanics_identity_defects` used to judge only what a record DECLARED, so a
        record omitting its identity was a FORM defect to `classify_gate` and no defect
        at all to the live ingress -- and the permissive one runs first and writes the
        ledger. This test drives BOTH validators over the same records and requires the
        same KIND from each, which is what makes the disagreement impossible to
        reintroduce quietly.
        """
        cases = {
            "omits everything": {"state": "CLEAR"},
            "omits the triple": dict(WORKER_RECORD, boundary=None, source=None,
                                     role=None),
            "omits the binding": {"state": "CLEAR", "boundary": "B2",
                                  "source": "worker", "role": "worker",
                                  "ledger_schema_version": 1},
            "unknown token": dict(WORKER_RECORD, boundary="B9"),
            "foreign identity": dict(WORKER_RECORD, **REVIEWER_IDENTITY),
            "foreign run": dict(WORKER_RECORD, run="run_other"),
            "conforming": dict(WORKER_RECORD),
        }
        for label, record in cases.items():
            with self.subTest(case=label):
                record = {k: v for k, v in record.items() if v is not None
                          or k in ("reason_code", "open_item", "assumption")}
                ingress = decision_contract.mechanics_identity_defects(
                    record, role="WORKER", binding=BINDING)
                engine = self.classify(record, binding=BINDING)
                self.assertEqual(
                    {d.kind for d in ingress}, {d.kind for d in engine},
                    f"the two validators disagree about {label}: "
                    f"ingress={[d.as_dict() for d in ingress]} "
                    f"engine={[d.as_dict() for d in engine]}")

    def test_an_omitted_mechanics_field_is_a_repairable_FORM_defect_at_the_ingress(self) -> None:
        """Round-3 F-001, at the validator. Absence is the THIRD arm of the taxonomy.

        Swept one field at a time so a fix that only requires the triple, or only the
        binding, fails here. FORM, never LIFECYCLE: the contract GIVES the agent these
        values, so omitting one is a correctable format error and the bounded repair
        loop re-asks for a complete record.
        """
        for field in ("ledger_schema_version", "boundary", "source", "role",
                      "run", "phase", "iteration"):
            with self.subTest(field=field):
                record = {k: v for k, v in WORKER_RECORD.items() if k != field}
                defects = decision_contract.mechanics_identity_defects(
                    record, role="WORKER", binding=BINDING)
                self.assertTrue(defects, f"a record with no {field!r} was accepted")
                self.assertTrue(
                    all(d.kind == "FORM" for d in defects),
                    f"an absent {field} is not repairable: "
                    f"{[d.as_dict() for d in defects]}")
                self.assertIn(field, {d.field_path for d in defects})

    def test_a_complete_conforming_record_is_still_accepted_at_the_ingress(self) -> None:
        """The control: requiring completeness must not reject the record the generated
        contract actually asks for."""
        self.assertEqual(
            decision_contract.mechanics_identity_defects(
                dict(WORKER_RECORD), role="WORKER", binding=BINDING), ())

    def test_the_matching_binding_produces_no_defect(self) -> None:
        """The phase comparison is case-insensitive on purpose: the engine says
        `ANALYSIS` and the generated contract says `analysis`, and a case-sensitive
        check would reject every conforming record."""
        self.assertEqual(self.classify(dict(WORKER_RECORD), binding=BINDING), ())
        self.assertEqual(
            self.classify(dict(WORKER_RECORD, phase=PHASE.lower()), binding=BINDING), ())

    def test_no_binding_argument_leaves_binding_unchecked(self) -> None:
        """The CLI validates a body with no dispatch context; it must not invent one."""
        self.assertEqual(self.classify(dict(WORKER_RECORD, run="run_other")), ())


class NodeReachabilityTests(unittest.TestCase):
    """The engine half: the same records through the REAL nodes.

    A validator that reports a defect nobody routes on is not a gate, so every
    assertion below goes through `VALIDATE_SETTLEMENT` -> `APPLY_RESULT` -> `ROUTE`.
    """

    def setUp(self) -> None:
        self.policy = decision_contract.resolve_policy()
        self.node = validate_settlement_node(self.policy,
                                             decision_contract.classify_gate)
        self.state = dict(initial_state(
            run_id=RUN, thread_id="t", phases=(PHASE,),
            capabilities=frozenset(BASE_CAPABILITIES), risk="high", max_iterations=5))

    def settle(self, record, *, state=None, status="COMPLETE"):
        prepared = prepare_intent_node({**self.state, "route_token": "PREPARE_WORKER"})
        intent = prepared["pending_intent"]
        gate = envelope_for(record, state=state or record["state"])
        event = make_settlement_event(intent, {"status": status, "gate": gate},
                                      occurred_at="1970-01-01T00:00:00Z")
        validated = self.node({**prepared, "pending_event": event,
                               "intent_status": "SETTLED"})
        return intent, validated, apply_result_node(validated)

    def test_a_forged_identity_blocks_before_any_reviewer_is_prepared(self) -> None:
        """The pre-dispatch blocking requirement, expressed on the route token.

        `PREPARE_PHASE_REVIEWER` is the only token that dispatches a Reviewer, and
        BLOCK is the only acceptable answer here. It must also cost no repair budget:
        forgery is not a representation error.
        """
        _, validated, applied = self.settle(dict(WORKER_RECORD, **REVIEWER_IDENTITY))
        self.assertIsNone(validated.get("terminal_reason"))
        self.assertEqual(validated["pending_gate_defect"]["code"],
                         "DECISION_GATE_LIFECYCLE_DEFECT")
        routed = route_node(applied)
        self.assertEqual(routed["route_token"], "BLOCK")
        self.assertEqual(routed["repair_attempts"], 0)
        self.assertEqual(routed["remaining_repair_budget"], MAX_REPAIR_ATTEMPTS)

    def test_the_forged_identity_terminal_names_the_defect(self) -> None:
        """The terminal reason has to say what was wrong, not merely that it was."""
        _, _, applied = self.settle(dict(WORKER_RECORD, **REVIEWER_IDENTITY))
        terminal = terminal_node(route_node(applied))
        self.assertEqual(terminal["terminal_status"], "BLOCKED")
        reason = terminal["terminal_reason"]
        self.assertEqual(reason["code"], "DECISION_GATE_LIFECYCLE_DEFECT")
        self.assertTrue(reason["defects"])

    def test_a_forged_identity_never_becomes_a_worker_result(self) -> None:
        """`apply_result_node` must not record a settlement it refused."""
        _, _, applied = self.settle(dict(WORKER_RECORD, **REVIEWER_IDENTITY))
        self.assertIsNone(applied.get("worker_result"))
        self.assertIsNone(applied.get("reviewer_result"))

    def test_an_unknown_boundary_reaches_the_bounded_repair_branch(self) -> None:
        """The FORM arm, through `route_node` -- never `routing.route` in isolation,
        which would pass even if `terminal_reason` made the branch unreachable."""
        _, validated, applied = self.settle(dict(WORKER_RECORD, boundary="B9"))
        self.assertIsNone(validated.get("terminal_reason"))
        self.assertEqual(validated["pending_gate_defect"]["code"],
                         "DECISION_GATE_FORM_DEFECT")
        self.assertEqual(route_node(applied)["route_token"], "PREPARE_REPAIR")

    def test_the_node_binds_the_record_to_the_active_dispatch(self) -> None:
        """Proves the ENGINE supplies the binding, not merely that `classify_gate`
        could check one. Catches wiring the new parameter nowhere.

        CORRECTED taxonomy (round-2 F-001): a record naming another run is an identity
        claim, so the route token must be BLOCK. It previously asserted PREPARE_REPAIR,
        which is the engine-level form of "forgery re-asked instead of refused".
        """
        _, validated, applied = self.settle(dict(WORKER_RECORD, run="run_elsewhere"))
        defect = validated.get("pending_gate_defect")
        self.assertIsNotNone(defect, "a record naming another run was accepted")
        self.assertEqual(defect["code"], "DECISION_GATE_LIFECYCLE_DEFECT")
        self.assertIn("run", {d["field_path"] for d in defect["defects"]})
        routed = route_node(applied)
        self.assertEqual(routed["route_token"], "BLOCK")
        self.assertEqual(routed["repair_attempts"], 0,
                         "a foreign run spent repair budget")

    def test_a_conforming_record_still_passes_untouched(self) -> None:
        """The regression guard: the identity checks must not reject the record the
        generated contract actually asks for."""
        _, validated, applied = self.settle(dict(WORKER_RECORD))
        self.assertIsNone(validated.get("pending_gate_defect"))
        self.assertEqual(route_node(applied)["route_token"],
                         "PREPARE_PHASE_REVIEWER")

    def test_the_binding_the_node_derives_matches_the_dispatched_contract(self) -> None:
        """Ingress/egress parity: the record the Worker is TOLD to write is the record
        the validator accepts. Catches binding to `intent["phase"]` for a Final
        Reviewer, whose contract is rendered with the `final_review` phase.
        """
        projection = decision_contract.contract_projection(self.policy)
        for role in ("WORKER", "PHASE_REVIEWER", "FINAL_REVIEWER"):
            with self.subTest(role=role):
                phase = artifact_identity.contract_phase(role, PHASE)
                block = decision_contract.render_worker_contract(
                    projection, run_id=RUN, phase=phase, iteration=1, role=role)
                # The FIRST fenced skeleton is the CLEAR one; the others carry
                # `<one of: ...>` placeholders for the reason code by design.
                fenced = decision_gate.GATE_RECORD_BLOCK.finditer(block)
                skeleton = json.loads(next(fenced).group("body"))
                defects = decision_contract.classify_gate(
                    self.policy, envelope_for(skeleton), role=role,
                    binding={"run": RUN, "phase": phase, "iteration": 1})
                self.assertEqual(
                    [d.as_dict() for d in defects], [],
                    "the generated skeleton is rejected by the generated validator")


class LiveHarnessIngressTests(OfflineHarnessTestCase):
    """The FIRST live consumer of a settlement, which runs before any engine node.

    Round-2 F-001. `OrcaRuntimeHarness._record_decision_from_attempt` parsed the agent's
    record with `parse_gate_result` -- whose policy validation binds nothing to the
    active dispatch -- and then UNCONDITIONALLY overwrote `run`, `phase`, `iteration`,
    `boundary`, `source` and `role` with the Coordinator's own values before appending
    the result to the decision ledger. A Worker settlement declaring the internally valid
    but foreign identity `run_foreign/design/99/B3/reviewer/reviewer` returned CLEAR and
    the ledger then held `run_live/implementation/1/B2/worker/worker`. The forgery was
    not rejected; it was rewritten into a valid-looking row, and no later classifier can
    withdraw an accepted ledger write.

    Every test here drives that production method with a RAW body, and reads the real
    ledger afterwards. Nothing is asserted about the engine classifier: this boundary has
    to hold on its own.
    """

    RUN = "run_live_ingress"
    PHASE = "implementation"

    def harness(self):
        harness = self.build(RecordingExec())
        harness.run_id = self.RUN
        harness.requested_phases = (self.PHASE,)
        run_logging.open_decision_ledger(
            self.RUN, base=self.artifact_dir, phases=(self.PHASE,), risk="high",
            ledger_schema_version=decision_gate.LEDGER_RECORD_SCHEMA_VERSION)
        return harness

    def ledger(self):
        return run_logging.read_decision_ledger(self.RUN, base=self.artifact_dir)

    @staticmethod
    def body(record):
        return ("STATUS: COMPLETE\nDECISION_GATE_STATE: "
                f"{record['state']}\n\n```decision-gate\n"
                + json.dumps(record) + "\n```\n")

    @classmethod
    def complete(cls) -> dict:
        """A record that declares its COMPLETE mechanics identity, matching."""
        return dict(cls.FORGED, run=cls.RUN, phase=cls.PHASE, iteration=1,
                    boundary="B2", source="worker", role="worker",
                    responsible_phase=cls.PHASE,
                    source_binding=f"artifacts/runs/{cls.RUN}/")

    @staticmethod
    def attempt(body, *, role="phase_worker", iteration=1, dispatch_id="ctx_x"):
        from scripts.orca_runtime_harness import RuntimeAttempt
        return RuntimeAttempt(
            role=role, iteration=iteration, task_id="task_x", dispatch_id=dispatch_id,
            outcome="succeeded", task_status="completed", dispatch_status="completed",
            worker_state="settled", terminal_state="live", lifecycle_action="release",
            worker_done_count=1, execution_path="supervised", body=body)

    def settle(self, harness, record, *, role="phase_worker", iteration=1,
               dispatch_id=None):
        # A DISTINCT dispatch id per settlement by default: a dispatch decides once, and
        # the harness now answers a second call for the same one with what it already
        # decided. A repair is a NEW dispatch of the same round, which is exactly what
        # the default models.
        self._dispatches = getattr(self, "_dispatches", 0) + 1
        return harness._record_decision_from_attempt(
            phase=self.PHASE,
            attempt=self.attempt(self.body(record), role=role, iteration=iteration,
                                 dispatch_id=dispatch_id or f"ctx_{self._dispatches}"),
            event="dispatch_settled")

    # The record the Final Reviewer submitted, verbatim in shape: every field is
    # internally valid and every one of them names a different dispatch.
    FORGED = {
        "ledger_schema_version": 1, "boundary": "B3", "source": "reviewer",
        "role": "reviewer", "run": "run_foreign", "phase": "design", "iteration": 99,
        "responsible_phase": "design", "state": "CLEAR", "reason_code": None,
        "open_decision_item": False, "open_item": None, "assumption": None,
        "evidence": {}, "verdict": "", "source_binding": "artifacts/runs/run_foreign/",
        "recorded_at": "2026-01-01T00:00:00+00:00", "prior_open_decision_items": [],
        "grounds": "g", "scope": "s",
    }

    def test_a_forged_identity_is_refused_and_never_reaches_the_ledger(self) -> None:
        """THE round-2 reproduction. Catches normalising the claim into a local row."""
        harness = self.harness()
        before = len(self.ledger())
        state, code = self.settle(harness, dict(self.FORGED))
        self.assertEqual(state, decision_gate.INPUT_DEFECT_STATE,
                         "the forged identity settled as a decision")
        self.assertEqual(code, decision_gate.GATE_INPUT_UNBOUND)
        self.assertEqual(len(self.ledger()), before,
                         "a rewritten record was appended for a forged identity")

    def test_each_forged_mechanics_field_alone_is_refused(self) -> None:
        """One field at a time, so a fix that only checks the triple, or only the
        binding, fails here. Every value below is WELL-FORMED and names another
        dispatch."""
        cases = {
            "run": "run_foreign",
            "phase": "design",
            "iteration": 99,
            "boundary": "B3",
            "source": "reviewer",
            "role": "reviewer",
        }
        clean = self.complete()
        for field, value in cases.items():
            with self.subTest(field=field):
                harness = self.harness()
                before = len(self.ledger())
                state, code = self.settle(harness, dict(clean, **{field: value}))
                self.assertEqual(state, decision_gate.INPUT_DEFECT_STATE,
                                 f"a foreign {field} settled as a decision")
                self.assertEqual(code, decision_gate.GATE_INPUT_UNBOUND)
                self.assertEqual(len(self.ledger()), before,
                                 f"a foreign {field} was published")

    def test_a_forged_identity_admits_no_repair_dispatch(self) -> None:
        """Fail-closed means the round cannot be re-asked either.

        `_last_input_defect` is what `_b1_guard` consults to admit a bounded repair of a
        round that produced an INPUT defect. Forgery must not arm it, and must CLEAR a
        value armed by an earlier, genuinely repairable attempt on the same round --
        otherwise a malformed first attempt would buy a forged second one a re-ask.
        """
        harness = self.harness()
        harness._last_input_defect = (self.RUN, self.PHASE, 1)
        self.settle(harness, dict(self.FORGED))
        self.assertIsNone(harness._last_input_defect,
                          "a forged identity left the repair path armed")
        with self.assertRaises(Exception) as caught:
            harness._b1_guard(phase=self.PHASE, role="phase_worker", iteration=1,
                              repair_instruction={"attempt": 1, "max_attempts": 2,
                                                  "defects": []})
        self.assertIn("DECISION_GATE", str(caught.exception))

    def test_an_out_of_domain_token_stays_repairable_at_the_ingress(self) -> None:
        """The line the review draws, asserted at the SAME boundary.

        `B9` is not another agent's identity -- it is no identity at all -- so it stays a
        format defect the bounded repair loop may re-ask. Catches a fix that makes every
        mechanics mismatch fail closed, which would break OS-42's whole reason to exist.
        """
        harness = self.harness()
        clean = self.complete()
        before = len(self.ledger())
        state, code = self.settle(harness, dict(clean, boundary="B9"))
        self.assertEqual(state, decision_gate.INPUT_DEFECT_STATE)
        self.assertEqual(code, decision_gate.GATE_INPUT_MALFORMED)
        self.assertEqual(len(self.ledger()), before,
                         "an out-of-domain token was published")
        self.assertEqual(harness._last_input_defect, (self.RUN, self.PHASE, 1),
                         "a repairable ingress defect did not arm the repair path")

    # ---- round-3 F-001: OMISSION, at the live ingress -------------------------------
    # This REPLACES `test_a_record_that_declares_no_mechanics_is_still_published`, which
    # required the acceptance the re-review found. Its argument -- that an omitted
    # mechanics field is Coordinator-owned rather than a claim -- is the assumption that
    # did not hold: the contract GIVES every dispatched record those fields, so omitting
    # them is a correctable FORM defect and bounded repair is what re-asks for them.

    OMITTED = {"state": "CLEAR", "reason_code": None, "open_decision_item": False,
               "grounds": "g", "scope": "s"}

    def test_an_omitted_mechanics_identity_writes_no_ledger_row(self) -> None:
        """The re-review's reproduction, inverted.

        It supplied NO mechanics fields, got CLEAR, and the ledger then held a locally
        synthesised identity the agent never declared. Nothing may be published for it.
        """
        harness = self.harness()
        before = len(self.ledger())
        state, code = self.settle(harness, dict(self.OMITTED))
        self.assertEqual(state, decision_gate.INPUT_DEFECT_STATE,
                         "a record declaring no identity settled as a decision")
        self.assertEqual(code, decision_gate.GATE_INPUT_MALFORMED)
        self.assertEqual(len(self.ledger()), before,
                         "an identity the agent never declared was published")

    def test_an_omitted_identity_prepares_bounded_repair_not_a_reviewer(self) -> None:
        """FORM, so the round is RE-ASKED rather than failed closed.

        `_last_input_defect` is what `_b1_guard` consults to admit the bounded repair of
        a round that produced an INPUT defect, and it is the difference between this arm
        and the foreign-value arm beside it. Asserted through the guard itself: a repair
        dispatch is admitted, and an ordinary one -- the Reviewer -- is not.
        """
        harness = self.harness()
        self.settle(harness, dict(self.OMITTED))
        self.assertEqual(harness._last_input_defect, (self.RUN, self.PHASE, 1),
                         "omission did not arm the bounded repair path")
        harness._b1_guard(phase=self.PHASE, role="phase_worker", iteration=1,
                          repair_instruction={"attempt": 1, "max_attempts": 2,
                                              "defects": []})
        with self.assertRaises(Exception) as caught:
            harness._b1_guard(phase=self.PHASE, role="phase_reviewer", iteration=1)
        self.assertIn("DECISION_GATE", str(caught.exception))

    def test_a_partial_identity_is_refused_field_by_field(self) -> None:
        """One missing field is enough. Catches a fix that requires the triple and
        forgets the binding, or the reverse."""
        for field in ("ledger_schema_version", "boundary", "source", "role",
                      "run", "phase", "iteration"):
            with self.subTest(field=field):
                harness = self.harness()
                before = len(self.ledger())
                record = {k: v for k, v in self.complete().items() if k != field}
                state, code = self.settle(harness, record)
                self.assertEqual(state, decision_gate.INPUT_DEFECT_STATE,
                                 f"a record with no {field!r} settled as a decision")
                self.assertEqual(code, decision_gate.GATE_INPUT_MALFORMED)
                self.assertEqual(len(self.ledger()), before,
                                 f"a record with no {field!r} was published")

    def test_the_round_succeeds_only_after_a_complete_corrected_record(self) -> None:
        """The whole bounded-repair cycle at this boundary, in one test.

        Attempt 1 omits the identity: no row, repair armed. The repair supplies a
        COMPLETE and matching record: exactly one row is published, carrying the identity
        the agent declared -- not one the Coordinator invented for it.
        """
        harness = self.harness()
        before = len(self.ledger())
        self.settle(harness, dict(self.OMITTED))
        self.assertEqual(len(self.ledger()), before)

        state, code = self.settle(harness, self.complete())

        self.assertEqual((state, code), ("CLEAR", ""))
        rows = self.ledger()
        self.assertEqual(len(rows), before + 1)
        self.assertEqual(
            [rows[-1][key] for key in ("run", "phase", "iteration", "boundary",
                                       "source", "role")],
            [self.RUN, self.PHASE, 1, "B2", "worker", "worker"])
        self.assertIsNone(harness._last_input_defect,
                          "the repair path stayed armed after a clean settlement")

    def test_a_matching_declaration_is_published_unchanged(self) -> None:
        """An agent that DOES follow the generated contract and declares the correct
        mechanics must be accepted, not punished for saying so."""
        harness = self.harness()
        before = len(self.ledger())
        record = self.complete()
        state, code = self.settle(harness, record)
        self.assertEqual((state, code), ("CLEAR", ""))
        self.assertEqual(len(self.ledger()), before + 1)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
