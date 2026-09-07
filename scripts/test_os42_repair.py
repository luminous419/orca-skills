"""OS-42: the bounded repair loop, its budget, its replay behaviour and its terminals.

Every reachability test drives `executor.route_node` or the compiled graph, NEVER
`routing.route` in isolation. That is deliberate: `route_node` calls `route(state)` only
when `terminal_reason` is falsy, so a test that called `route` directly would have passed
against a design in which the repair branch was unreachable.
"""
from __future__ import annotations

import json
import unittest
from copy import deepcopy

from scripts import decision_contract, decision_gate
from scripts.deterministic_workflow import routing
from scripts.deterministic_workflow.contracts import (BASE_CAPABILITIES,
                                                      GATE_REPAIR_EXHAUSTED,
                                                      MAX_REPAIR_ATTEMPTS,
                                                      REPAIR_INSTRUCTION_KEYS,
                                                      make_intent,
                                                      make_settlement_event)
from scripts.deterministic_workflow.executor import (apply_result_node,
                                                     consume_without_apply,
                                                     prepare_intent_node, route_node,
                                                     terminal_node,
                                                     validate_settlement_node)
from scripts.deterministic_workflow.state import StateError, initial_state, validate_state

OS42_DEFECT_VALUE = (
    "Fully reversible: this phase wrote exactly one new artifact "
    "(artifacts/runs/run_8e8f9451ad44/ANALYSIS.md) and modified no tracked file, "
    "no production code, and no pre-existing run or artifact."
)

CLEAN_RECORD = {
    "ledger_schema_version": 1, "boundary": "B2", "source": "worker", "role": "worker",
    "run": "run_os42", "phase": "ANALYSIS", "iteration": 1,
    "responsible_phase": "ANALYSIS", "state": "CLEAR", "reason_code": None,
    "open_decision_item": False, "open_item": None, "assumption": None, "evidence": {},
    "verdict": "", "source_binding": "artifacts/runs/run_os42/",
    "recorded_at": "2026-01-01T00:00:00+00:00", "prior_open_decision_items": [],
}


def envelope_for(record, *, state="CLEAR"):
    return {"declared_state": state, "declaration_count": 1, "fence_count": 1,
            "record": deepcopy(record), "record_text": None, "truncated": False}


class RepairLoopTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.policy = decision_contract.resolve_policy()
        self.node = validate_settlement_node(self.policy, decision_contract.classify_gate)
        self.state = dict(initial_state(
            run_id="run_os42", thread_id="t", phases=("ANALYSIS",),
            capabilities=frozenset(BASE_CAPABILITIES), risk="high", max_iterations=5))

    def settle(self, state, *, gate, status="COMPLETE", role="WORKER"):
        """Prepare -> settle -> VALIDATE_SETTLEMENT -> APPLY_RESULT, using the real nodes."""
        prepared = prepare_intent_node({**state, "route_token": (
            "PREPARE_REPAIR" if state.get("pending_gate_defect") else "PREPARE_WORKER")})
        intent = prepared["pending_intent"]
        result = {"status": status, "gate": gate}
        event = make_settlement_event(intent, result, occurred_at="1970-01-01T00:00:00Z")
        prepared = {**prepared, "pending_event": event, "intent_status": "SETTLED"}
        validated = self.node(prepared)
        applied = apply_result_node(validated)
        return intent, validated, applied


class ReachabilityTests(RepairLoopTestCase):
    """F-001: the repair branch has to be REACHED, not merely written."""

    def test_form_defect_reaches_prepare_repair_through_route_node(self) -> None:
        """THE test the earlier design would have failed.

        Catches setting `terminal_reason` for a repairable FORM defect, which makes
        `route_node` short-circuit to BLOCK and the whole feature a no-op.
        """
        _, validated, applied = self.settle(
            self.state, gate=envelope_for(dict(CLEAN_RECORD,
                                               reversibility=OS42_DEFECT_VALUE)))
        self.assertIsNone(validated.get("terminal_reason"))
        self.assertEqual(validated["pending_gate_defect"]["code"],
                         "DECISION_GATE_FORM_DEFECT")
        routed = route_node(applied)
        self.assertEqual(routed["route_token"], "PREPARE_REPAIR")

    def test_validate_settlement_sets_no_terminal_reason_for_any_gate_defect(self) -> None:
        """For ALL THREE kinds, not just the repairable one.

        Catches re-adding a terminal reason "because it is terminal anyway", which works
        today and breaks the moment the budget rule changes.
        """
        cases = {
            "DECISION_GATE_FORM_DEFECT": dict(CLEAN_RECORD, reversibility="nonsense"),
            "DECISION_GATE_SEMANTIC_BLOCK": dict(
                CLEAN_RECORD, state="NEEDS_INPUT", reason_code="security_impact",
                boundary_element="security", what_is_missing="w",
                why_policy_cannot_decide="y", security=True),
        }
        for code, record in cases.items():
            with self.subTest(code=code):
                state = deepcopy(self.state)
                _, validated, _ = self.settle(
                    state, gate=envelope_for(record, state=record["state"]))
                self.assertIsNone(validated.get("terminal_reason"))
                self.assertEqual(validated["pending_gate_defect"]["code"], code)

    def test_semantic_block_routes_to_block_and_spends_no_repair_budget(self) -> None:
        """NEEDS_INPUT can never enter the repair branch, and cannot even exhaust it."""
        record = dict(CLEAN_RECORD, state="CONFLICT",
                      reason_code="requirement_contradiction",
                      citations=["a", "b"], why_they_cannot_both_hold="z")
        _, _, applied = self.settle(
            self.state, gate=envelope_for(record, state="CONFLICT"))
        routed = route_node(applied)
        self.assertEqual(routed["route_token"], "BLOCK")
        self.assertEqual(routed["repair_attempts"], 0)
        self.assertEqual(routed["remaining_repair_budget"], MAX_REPAIR_ATTEMPTS)

    def test_exhausted_form_defect_routes_to_block(self) -> None:
        """The budget is spent by the time ROUTE sees the defect, exactly as it is after
        MAX_REPAIR_ATTEMPTS real repairs."""
        _, _, applied = self.settle(
            self.state, gate=envelope_for(dict(CLEAN_RECORD, reversibility="nonsense")))
        exhausted = dict(applied, repair_attempts=MAX_REPAIR_ATTEMPTS,
                         remaining_repair_budget=0)
        self.assertEqual(route_node(exhausted)["route_token"], "BLOCK")

    def test_event_rejection_still_short_circuits_route(self) -> None:
        """Trace E: the pre-OS-42 terminal path is byte-identical.

        `route` is replaced with a raiser, so the test fails if the short-circuit is
        removed rather than merely producing the same token by accident.
        """
        original = routing.route
        state = dict(self.state, terminal_reason={"code": "MALFORMED_EVENT",
                                                  "message": "x"})
        try:
            routing.route = lambda _state: (_ for _ in ()).throw(
                AssertionError("route must not be called when terminal_reason is set"))
            import scripts.deterministic_workflow.executor as executor_module
            executor_module.route = routing.route
            self.assertEqual(route_node(state)["route_token"], "BLOCK")
        finally:
            routing.route = original
            import scripts.deterministic_workflow.executor as executor_module
            executor_module.route = original

    def test_apply_result_preserves_pending_gate_defect(self) -> None:
        """Catches a "tidy-up" that clears the defect in APPLY_RESULT, moving F-001 one
        node later."""
        _, _, applied = self.settle(
            self.state, gate=envelope_for(dict(CLEAN_RECORD, reversibility="nonsense")))
        self.assertIsNotNone(applied["pending_gate_defect"])
        self.assertIsNotNone(applied["repair_binding"])

    def test_a_gate_defect_never_sets_worker_result(self) -> None:
        """Which is what makes a Reviewer unreachable for a defective round:
        `PREPARE_PHASE_REVIEWER` requires `worker_result is not None`."""
        _, _, applied = self.settle(
            self.state, gate=envelope_for(dict(CLEAN_RECORD, reversibility="nonsense")))
        self.assertIsNone(applied["worker_result"])
        self.assertNotEqual(route_node(applied)["route_token"], "PREPARE_PHASE_REVIEWER")

    def test_consume_without_apply_reads_both_sources(self) -> None:
        self.assertIsNone(consume_without_apply(self.state))
        self.assertEqual(
            consume_without_apply({**self.state,
                                   "terminal_reason": {"code": "MALFORMED_EVENT"}}),
            "MALFORMED_EVENT")
        self.assertEqual(
            consume_without_apply({**self.state,
                                   "pending_gate_defect": {"code": "X", "defects": []}}),
            "X")


class RepairInstructionTests(RepairLoopTestCase):
    """F-002: the repair dispatch must CARRY the defect, and never a chosen value."""

    def prepared_repair(self):
        _, _, applied = self.settle(
            self.state, gate=envelope_for(dict(CLEAN_RECORD,
                                               reversibility=OS42_DEFECT_VALUE)))
        routed = route_node(applied)
        self.assertEqual(routed["route_token"], "PREPARE_REPAIR")
        return prepare_intent_node(routed)

    def test_repair_intent_carries_the_defects(self) -> None:
        """THE test the earlier design would have failed: it cleared the defect state
        before `make_intent` and the repair carried nothing."""
        prepared = self.prepared_repair()
        instruction = prepared["pending_intent"]["repair_instruction"]
        self.assertIsNotNone(instruction)
        self.assertEqual(set(instruction), set(REPAIR_INSTRUCTION_KEYS))
        self.assertEqual(instruction["attempt"], 1)
        self.assertEqual(instruction["max_attempts"], MAX_REPAIR_ATTEMPTS)
        self.assertEqual(instruction["defects"][0]["field_path"], "reversibility")
        self.assertIn("Fully reversible", instruction["defects"][0]["actual"])

    def test_pending_gate_defect_is_cleared_only_after_make_intent(self) -> None:
        prepared = self.prepared_repair()
        self.assertIsNone(prepared["pending_gate_defect"])
        self.assertIsNotNone(prepared["pending_intent"]["repair_instruction"])
        # the binding survives: it says which round owns the counter
        self.assertIsNotNone(prepared["repair_binding"])

    def test_ordinary_intent_carries_no_repair_instruction(self) -> None:
        prepared = prepare_intent_node({**self.state, "route_token": "PREPARE_WORKER"})
        self.assertIsNone(prepared["pending_intent"]["repair_instruction"])
        self.assertEqual(prepared["pending_intent"]["repair_attempt"], 0)

    def test_repair_attempt_and_instruction_are_biconditional(self) -> None:
        """"A repair dispatch with no defect payload" is UNREPRESENTABLE, not merely
        discouraged."""
        forged = dict(self.state, repair_attempts=1, remaining_repair_budget=1)
        with self.assertRaises(ValueError):
            make_intent(forged, "WORKER", "PHASE_GATE")

    def test_repair_instruction_is_digest_bound(self) -> None:
        """Mutating one character in the defect changes payload_digest and intent_id."""
        prepared = self.prepared_repair()
        intent = prepared["pending_intent"]
        mutated = deepcopy(prepared)
        mutated["pending_gate_defect"] = {
            "code": "DECISION_GATE_FORM_DEFECT",
            "defects": [dict(intent["repair_instruction"]["defects"][0],
                             actual="something else")]}
        mutated["repair_attempts"] = 0
        mutated["remaining_repair_budget"] = MAX_REPAIR_ATTEMPTS
        mutated["route_token"] = "PREPARE_REPAIR"
        other = prepare_intent_node(mutated)["pending_intent"]
        self.assertNotEqual(intent["payload_digest"], other["payload_digest"])
        self.assertNotEqual(intent["intent_id"], other["intent_id"])

    def test_repair_attempt_makes_the_command_id_distinct(self) -> None:
        """Without this the repair is refused as an already-processed command, or -- worse
        -- served the first attempt's cached receipt so repair silently does nothing."""
        ordinary = prepare_intent_node(
            {**self.state, "route_token": "PREPARE_WORKER"})["pending_intent"]
        repair = self.prepared_repair()["pending_intent"]
        self.assertNotEqual(ordinary["command_id"], repair["command_id"])
        self.assertEqual(ordinary["gate_iteration"], repair["gate_iteration"])
        self.assertEqual(ordinary["artifact_contract_path"],
                         repair["artifact_contract_path"])

    def test_repair_without_a_defect_fails_closed(self) -> None:
        with self.assertRaises(StateError):
            prepare_intent_node({**self.state, "route_token": "PREPARE_REPAIR"})

    def test_repair_block_lists_the_entire_allowed_set(self) -> None:
        """THE anti-inference test. A rendering that names ONE token fails."""
        prepared = self.prepared_repair()
        projection = decision_contract.contract_projection(self.policy)
        block = decision_contract.render_repair_instruction(
            prepared["pending_intent"]["repair_instruction"], projection)
        allowed = self.policy.boundary_elements["reversibility"].values
        for token in allowed:
            self.assertIn(token, block)

    def test_repair_block_has_no_suggestion(self) -> None:
        prepared = self.prepared_repair()
        projection = decision_contract.contract_projection(self.policy)
        block = decision_contract.render_repair_instruction(
            prepared["pending_intent"]["repair_instruction"], projection).lower()
        for phrase in ("use ", "should be", "did you mean", "suggest", "recommend"):
            self.assertNotIn(phrase, block, f"the repair block suggests a value: {phrase!r}")

    def test_the_repair_block_singles_out_no_allowed_value(self) -> None:
        """The anti-inference guard, stated structurally instead of as a phrase blocklist.

        `test_repair_block_has_no_suggestion` greps for "use ", "should be", "did you
        mean", "suggest" and "recommend".  A finite blocklist cannot enforce "never
        suggests a value": verified by mutation, a renderer that appends
        `closest match:  <token>` passes that test untouched while doing precisely what
        the ticket forbids -- picking one enum token for the agent.

        The property that does hold whatever the wording: the block presents the allowed
        set NEUTRALLY, so every token appears the same number of times.  Singling one out
        -- by repeating it, or by naming only it -- breaks the equality.
        """
        prepared = self.prepared_repair()
        projection = decision_contract.contract_projection(self.policy)
        block = decision_contract.render_repair_instruction(
            prepared["pending_intent"]["repair_instruction"], projection)
        allowed = tuple(self.policy.boundary_elements["reversibility"].values)
        counts = {token: block.count(token) for token in allowed}
        self.assertTrue(all(counts.values()),
                        f"the repair block omits part of the allowed set: {counts}")
        self.assertEqual(
            len(set(counts.values())), 1,
            f"the repair block mentions one allowed value more often than the others, "
            f"which singles it out: {counts}")

    def test_a_mixed_defect_list_is_never_classified_as_repairable(self) -> None:
        """Defence in depth for "NEEDS_INPUT/CONFLICT are never bypassed by schema repair".

        `classify_gate` returns its FORM sweep immediately, so today it never hands the
        engine a list carrying both a FORM and a SEMANTIC defect, and the equality in
        `validate_settlement_node` (`kinds == {"FORM"}` rather than `"FORM" in kinds`) is
        therefore an EQUIVALENT MUTANT against the real classifier -- verified: relaxing it
        breaks no other test in this suite.  An untested guard is one a later refactor
        deletes as dead weight, and the day the classifier stops returning early it is the
        only thing between a NEEDS_INPUT and a repair dispatch.

        So this drives the node through its injected classifier seam with a list the real
        classifier would not currently produce, and requires the mixed result to be
        classified as a block rather than as something repairable.
        """
        def mixed_classifier(policy, envelope, *, role, binding=None):
            del policy, envelope, role, binding
            return (
                decision_gate.GateDefect(
                    code="DECISION_GATE_INPUT_MALFORMED", kind="FORM",
                    field_path="reversibility",
                    expected=tuple(self.policy.boundary_elements["reversibility"].values),
                    actual=OS42_DEFECT_VALUE, message="prose where a token belongs"),
                decision_gate.GateDefect(
                    code="DECISION_BLOCKED_NEEDS_INPUT", kind="SEMANTIC",
                    field_path="state", expected=(), actual="NEEDS_INPUT",
                    message="the record declares a legitimate semantic block"),
            )

        node = validate_settlement_node(self.policy, mixed_classifier)
        prepared = prepare_intent_node({**self.state, "route_token": "PREPARE_WORKER"})
        intent = prepared["pending_intent"]
        event = make_settlement_event(
            intent, {"status": "COMPLETE", "gate": envelope_for(dict(CLEAN_RECORD))},
            occurred_at="1970-01-01T00:00:00Z")
        validated = node({**prepared, "pending_event": event, "intent_status": "SETTLED"})

        self.assertEqual(validated["pending_gate_defect"]["code"],
                         "DECISION_GATE_SEMANTIC_BLOCK",
                         "a list carrying a SEMANTIC defect was called repairable")
        routed = route_node(apply_result_node(validated))
        self.assertNotEqual(routed["route_token"], "PREPARE_REPAIR",
                            "a semantic block was routed into the repair branch")
        self.assertEqual(routed["repair_attempts"], 0,
                         "a semantic block spent repair budget")

    def test_repair_instruction_keys_carry_no_candidate_field(self) -> None:
        forbidden = {"suggestion", "candidate", "recommended", "value", "fix"}
        self.assertEqual(set(REPAIR_INSTRUCTION_KEYS) & forbidden, set())


class BudgetTests(RepairLoopTestCase):
    def test_repair_consumes_no_phase_or_final_budget(self) -> None:
        before = deepcopy(self.state)
        _, _, applied = self.settle(
            self.state, gate=envelope_for(dict(CLEAN_RECORD, reversibility="nonsense")))
        routed = route_node(applied)
        prepared = prepare_intent_node(routed)
        for field in ("phase_iterations", "remaining_phase_budget",
                      "final_review_iterations", "remaining_final_budget"):
            self.assertEqual(prepared[field], before[field], field)
        self.assertEqual(prepared["repair_attempts"], 1)
        self.assertEqual(prepared["remaining_repair_budget"], MAX_REPAIR_ATTEMPTS - 1)

    def test_repair_budget_domain_cannot_be_forged(self) -> None:
        """The sum alone is not enough: (-100, 105) satisfies it and grants 105 attempts."""
        for consumed, remaining in ((-100, 105), (True, MAX_REPAIR_ATTEMPTS - 1),
                                    (MAX_REPAIR_ATTEMPTS, MAX_REPAIR_ATTEMPTS)):
            with self.subTest(consumed=consumed, remaining=remaining):
                forged = dict(self.state, repair_attempts=consumed,
                              remaining_repair_budget=remaining)
                with self.assertRaises(StateError):
                    validate_state(forged, expected_thread_id="t")

    def test_an_ordinary_prepare_resets_the_repair_domain(self) -> None:
        used = dict(self.state, repair_attempts=1,
                    remaining_repair_budget=MAX_REPAIR_ATTEMPTS - 1,
                    repair_binding={"phase": "ANALYSIS", "phase_iteration": 0,
                                    "role": "WORKER", "round_kind": "PHASE_GATE"},
                    route_token="PREPARE_WORKER")
        prepared = prepare_intent_node(used)
        self.assertEqual(prepared["repair_attempts"], 0)
        self.assertEqual(prepared["remaining_repair_budget"], MAX_REPAIR_ATTEMPTS)
        self.assertIsNone(prepared["repair_binding"])


class TerminalReasonTests(RepairLoopTestCase):
    def test_exhaustion_terminal_carries_the_full_payload(self) -> None:
        """The ticket requires the validation error, the field path, the allowed values
        and the retry count. `terminal_node` REBUILDS terminal_reason, so this test
        catches the payload being dropped there."""
        _, _, applied = self.settle(
            self.state,
            gate=envelope_for(dict(CLEAN_RECORD, reversibility=OS42_DEFECT_VALUE)))
        exhausted = dict(applied, repair_attempts=MAX_REPAIR_ATTEMPTS,
                         remaining_repair_budget=0)
        routed = route_node(exhausted)
        self.assertEqual(routed["route_token"], "BLOCK")
        terminal = terminal_node(routed)
        reason = terminal["terminal_reason"]
        self.assertEqual(terminal["terminal_status"], "BLOCKED")
        self.assertEqual(reason["code"], GATE_REPAIR_EXHAUSTED)
        self.assertEqual(reason["repair_attempts"], MAX_REPAIR_ATTEMPTS)
        self.assertEqual(reason["max_repair_attempts"], MAX_REPAIR_ATTEMPTS)
        defect = reason["defects"][0]
        self.assertEqual(defect["field_path"], "reversibility")
        self.assertIn("Fully reversible", defect["actual"])
        self.assertEqual(set(defect["expected"]),
                         set(self.policy.boundary_elements["reversibility"].values))

    def test_a_non_gate_terminal_reason_keeps_its_three_keys(self) -> None:
        """`extras` must be empty for every terminal that is not a gate terminal."""
        state = dict(self.state, route_token="BLOCK",
                     terminal_reason={"code": "MALFORMED_EVENT", "message": "x"})
        terminal = terminal_node(state)
        self.assertEqual(set(terminal["terminal_reason"]), {"code", "message", "phase"})

    def test_semantic_terminal_carries_defects_but_no_repair_counters(self) -> None:
        record = dict(CLEAN_RECORD, state="NEEDS_INPUT",
                      reason_code="security_impact", boundary_element="security",
                      what_is_missing="w", why_policy_cannot_decide="y", security=True)
        _, _, applied = self.settle(
            self.state, gate=envelope_for(record, state="NEEDS_INPUT"))
        routed = route_node(applied)
        self.assertEqual(routed["route_token"], "BLOCK")
        terminal = terminal_node(routed)
        reason = terminal["terminal_reason"]
        self.assertEqual(reason["code"], "DECISION_GATE_SEMANTIC_BLOCK")
        self.assertIn("defects", reason)
        self.assertNotIn("repair_attempts", reason)


class ReplayTests(RepairLoopTestCase):
    def test_replayed_settlement_passes_through_apply_result(self) -> None:
        """F-003. Before the guard this raised
        `TypeError: 'NoneType' object is not subscriptable`.

        The test FAILS without the guard, which is the point.
        """
        prepared = prepare_intent_node({**self.state, "route_token": "PREPARE_WORKER"})
        intent = prepared["pending_intent"]
        event = make_settlement_event(
            intent, {"status": "COMPLETE", "gate": envelope_for(CLEAN_RECORD)},
            occurred_at="1970-01-01T00:00:00Z")
        replayed = {**prepared, "pending_event": event, "intent_status": "SETTLED",
                    "processed_event_ids": [event["event_id"]]}
        validated = self.node(replayed)
        self.assertIsNone(validated["pending_intent"])
        applied = apply_result_node(validated)          # must not raise
        self.assertEqual(applied["processed_event_ids"], [event["event_id"]])
        self.assertIsNone(applied["worker_result"])

    def test_identical_stored_settlement_replay_is_a_no_op(self) -> None:
        """Replay = re-processing a STORED event. No second repair, no second budget
        move, no second id."""
        _, _, applied = self.settle(
            self.state, gate=envelope_for(dict(CLEAN_RECORD, reversibility="nonsense")))
        first_ids = list(applied["processed_event_ids"])
        second = apply_result_node(applied)             # pending_* are already None
        self.assertEqual(second["processed_event_ids"], first_ids)
        self.assertEqual(second["repair_attempts"], applied["repair_attempts"])

    def test_repair_then_success_completes_in_the_same_phase_iteration(self) -> None:
        """Required Test 8: retry success after first failure."""
        before_iterations = deepcopy(self.state["phase_iterations"])
        _, _, applied = self.settle(
            self.state, gate=envelope_for(dict(CLEAN_RECORD,
                                               reversibility=OS42_DEFECT_VALUE)))
        routed = route_node(applied)
        self.assertEqual(routed["route_token"], "PREPARE_REPAIR")
        # the repair dispatch now returns a CLEAN record
        _, validated, repaired = self.settle(routed, gate=envelope_for(CLEAN_RECORD))
        self.assertIsNone(validated.get("pending_gate_defect"))
        self.assertEqual(repaired["worker_result"]["status"], "COMPLETE")
        self.assertEqual(repaired["phase_iterations"], before_iterations)
        self.assertEqual(repaired["round_kind"], "PHASE_GATE")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()


def _langgraph_ok() -> bool:
    import importlib.metadata
    try:
        import langgraph  # noqa: F401
        import langgraph.graph  # noqa: F401
    except ImportError:
        return False
    try:
        return importlib.metadata.version("langgraph") == "0.2.76"
    except importlib.metadata.PackageNotFoundError:
        return False


def malformed_gate_result():
    """A scripted settlement carrying the verbatim OS-42 defect."""
    return {"status": "COMPLETE", "unit_test_status": "NOT_APPLICABLE",
            "gate": envelope_for(dict(CLEAN_RECORD, reversibility=OS42_DEFECT_VALUE))}


@unittest.skipUnless(_langgraph_ok(), "requires pinned langgraph 0.2.76")
class CompiledGraphTests(unittest.TestCase):
    """The full loop on the REAL compiled graph, not on nodes in isolation."""

    def run_graph(self, results, phases=("ANALYSIS",), **kwargs):
        from scripts.deterministic_workflow.fake_adapter import FakeAdapter
        from scripts.deterministic_workflow.graph import build_graph
        from scripts.deterministic_workflow.runtime_state import InMemoryRuntimeStateStore
        adapter = FakeAdapter(results)
        state = initial_state(run_id="run_os42", thread_id="thread", phases=phases,
                              capabilities=frozenset(BASE_CAPABILITIES), **kwargs)
        graph = build_graph(adapter, runtime_state=InMemoryRuntimeStateStore(),
                            require_durable_checkpointer=False)
        return graph.invoke(state, config={"recursion_limit": 300}), adapter

    @staticmethod
    def tokens(out):
        return [entry.get("route") for entry in out["logical_trace"]
                if entry.get("node") == "ROUTE"]

    def test_full_repair_cycle_through_the_compiled_graph(self) -> None:
        """malformed -> PREPARE_REPAIR -> clean -> the round proceeds.

        Asserts PREPARE_REPAIR appears exactly once and that no Reviewer was prepared
        before the repair settled -- the "blocked before Reviewer dispatch" requirement,
        read off the real route-token stream.
        """
        out, _ = self.run_graph([
            malformed_gate_result(),                       # Worker attempt 1: malformed
            {"status": "COMPLETE", "unit_test_status": "NOT_APPLICABLE"},  # the repair
            {"result": "PASS"},                            # the Phase Reviewer
            {"result": "PASS", "findings": []},            # the Final Review
        ])
        stream = self.tokens(out)
        self.assertEqual(stream.count("PREPARE_REPAIR"), 1)
        repair_at = stream.index("PREPARE_REPAIR")
        self.assertNotIn("PREPARE_PHASE_REVIEWER", stream[:repair_at])
        self.assertEqual(out["terminal_status"], "COMPLETED")
        # the repair cost no phase iteration
        self.assertEqual(out["phase_iterations"]["ANALYSIS"], 1)

    def test_repair_exhaustion_blocks_with_the_full_payload(self) -> None:
        """Required Test 9. Two malformed bodies exhaust MAX_REPAIR_ATTEMPTS... and the
        third one has nowhere to go."""
        out, _ = self.run_graph([malformed_gate_result() for _ in range(4)])
        self.assertEqual(out["terminal_status"], "BLOCKED")
        reason = out["terminal_reason"]
        self.assertEqual(reason["code"], GATE_REPAIR_EXHAUSTED)
        self.assertEqual(reason["repair_attempts"], MAX_REPAIR_ATTEMPTS)
        self.assertEqual(reason["defects"][0]["field_path"], "reversibility")
        self.assertTrue(reason["defects"][0]["expected"])
        # no phase or final-review budget was spent on any of it
        self.assertEqual(out["phase_iterations"]["ANALYSIS"], 0)
        self.assertEqual(out["final_review_iterations"], 0)

    def test_a_clean_run_never_enters_the_repair_branch(self) -> None:
        out, _ = self.run_graph([
            {"status": "COMPLETE", "unit_test_status": "NOT_APPLICABLE"},
            {"result": "PASS"},
            {"result": "PASS", "findings": []},
        ])
        self.assertNotIn("PREPARE_REPAIR", self.tokens(out))
        self.assertEqual(out["terminal_status"], "COMPLETED")
        self.assertEqual(out["repair_attempts"], 0)
        self.assertEqual(out["remaining_repair_budget"], MAX_REPAIR_ATTEMPTS)
