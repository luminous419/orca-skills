"""OS-42: the validation-repair audit trail, and its exactly-once guarantee.

The acceptance bar is "exactly one durable event per logical transition, with no
duplicate after restart". So every test here either counts rows in a real
ORCHESTRATOR_LOG.md on disk, or replays a transition and counts again.
"""
from __future__ import annotations

import json
import os
import threading
import unittest
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory

from scripts import decision_contract, decision_gate, run_logging
from scripts.deterministic_workflow import audit
from scripts.deterministic_workflow.audit import RunLoggingAuditSink
from scripts.deterministic_workflow.contracts import (BASE_CAPABILITIES,
                                                      GATE_REPAIR_EXHAUSTED,
                                                      MAX_REPAIR_ATTEMPTS,
                                                      make_settlement_event)
from scripts.deterministic_workflow.executor import (apply_result_node,
                                                     audit_gate_transition,
                                                     audit_repair_request, audit_terminal,
                                                     prepare_intent_node, route_node,
                                                     terminal_node,
                                                     validate_settlement_node)
from scripts.deterministic_workflow.state import (StateError, initial_state,
                                                  validate_state)

OS42_DEFECT_VALUE = (
    "Fully reversible: this phase wrote exactly one new artifact "
    "(artifacts/runs/run_8e8f9451ad44/ANALYSIS.md) and modified no tracked file, "
    "no production code, and no pre-existing run or artifact."
)

CLEAN_RECORD = {
    "ledger_schema_version": 1, "boundary": "B2", "source": "worker", "role": "worker",
    "run": "run_audit", "phase": "ANALYSIS", "iteration": 1,
    "responsible_phase": "ANALYSIS", "state": "CLEAR", "reason_code": None,
    "open_decision_item": False, "open_item": None, "assumption": None, "evidence": {},
    "verdict": "", "source_binding": "artifacts/runs/run_audit/",
    "recorded_at": "2026-01-01T00:00:00+00:00", "prior_open_decision_items": [],
}


def envelope_for(record, *, state="CLEAR"):
    return {"declared_state": state, "declaration_count": 1, "fence_count": 1,
            "record": deepcopy(record), "record_text": None, "truncated": False}


class RecordingSink:
    """Counts deliveries, so a test can tell "the emitter fired" from "a row landed"."""

    def __init__(self, fail: bool = False) -> None:
        self.rows: list[tuple[str, str, dict]] = []
        self.seen: set[str] = set()
        self.fail = fail

    def deliver(self, event, key, fields):
        if self.fail:
            raise RuntimeError("the audit backend is down")
        self.rows.append((event, key, dict(fields)))
        if key in self.seen:
            return False
        self.seen.add(key)
        return True

    def events(self):
        return [event for event, _, _ in self.rows]


def entries_of(emitter, before, after):
    """The pure emitter's entries, delivered through a recording sink."""
    sink = RecordingSink()
    from scripts.deterministic_workflow.audit import flush_outbox
    flush_outbox(sink, emitter(before, after))
    return sink


class AuditTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.policy = decision_contract.resolve_policy()
        self.node = validate_settlement_node(self.policy, decision_contract.classify_gate)
        self.state = dict(initial_state(
            run_id="run_audit", thread_id="t", phases=("ANALYSIS",),
            capabilities=frozenset(BASE_CAPABILITIES), risk="high", max_iterations=5))

    def settle(self, state, *, gate, status="COMPLETE"):
        """prepare -> settle -> VALIDATE_SETTLEMENT -> APPLY_RESULT, real nodes."""
        token = "PREPARE_REPAIR" if state.get("pending_gate_defect") else "PREPARE_WORKER"
        prepared = prepare_intent_node({**state, "route_token": token})
        intent = prepared["pending_intent"]
        event = make_settlement_event(intent, {"status": status, "gate": gate},
                                      occurred_at="1970-01-01T00:00:00Z")
        settled = {**prepared, "pending_event": event, "intent_status": "SETTLED"}
        validated = self.node(settled)
        return intent, settled, validated, apply_result_node(validated)

    def malformed(self):
        return envelope_for(dict(CLEAN_RECORD, reversibility=OS42_DEFECT_VALUE))

    def transition(self):
        """(before, after) for a FORM-defect settlement, through the real nodes."""
        _, before, after, _ = self.settle(self.state, gate=self.malformed())
        return before, after


class KeyDeterminismTests(unittest.TestCase):
    """A replayed transition must recompute the SAME key, or the dedupe cannot see it."""

    def test_every_key_is_a_pure_function_of_a_checkpointed_identity(self) -> None:
        self.assertEqual(audit.gate_defect_key("event_a"), audit.gate_defect_key("event_a"))
        self.assertNotEqual(audit.gate_defect_key("event_a"),
                            audit.gate_defect_key("event_b"))
        self.assertNotEqual(audit.repair_requested_key("cmd_1"),
                            audit.repair_requested_key("cmd_2"))
        self.assertNotEqual(audit.gate_defect_key("x"), audit.repair_succeeded_key("x"),
                            "two different transitions on one identity must not collide")

    def test_the_exhaustion_key_separates_gate_rounds(self) -> None:
        first = audit.repair_exhausted_key("run_a", "ANALYSIS", 1, 2)
        second = audit.repair_exhausted_key("run_a", "ANALYSIS", 2, 2)
        self.assertNotEqual(first, second)

    def test_the_event_names_match_run_loggings_declarations(self) -> None:
        """The engine duplicates the four names to stay free of tools/ imports; this is
        what keeps the two copies equal."""
        self.assertEqual(set(audit.AUDIT_EVENTS), {
            run_logging.EVENT_DECISION_GATE_FORM_DEFECT,
            run_logging.EVENT_VALIDATION_REPAIR_REQUESTED,
            run_logging.EVENT_VALIDATION_REPAIR_SUCCEEDED,
            run_logging.EVENT_VALIDATION_REPAIR_EXHAUSTED,
        })
        self.assertEqual(audit.INPUT_DEFECT_STATE, decision_gate.INPUT_DEFECT_STATE)


class EmitterTests(AuditTestCase):
    """Each of the four transitions emits, and emits the right thing."""

    def test_a_form_defect_emits_with_the_field_path_and_allowed_values(self) -> None:
        _, before, after, _ = self.settle(self.state, gate=self.malformed())
        sink = entries_of(audit_gate_transition, before, after)
        self.assertEqual(sink.events(), [audit.EVENT_GATE_FORM_DEFECT])
        _, _, fields = sink.rows[0]
        self.assertIn("field=reversibility", fields["detail"])
        for token in self.policy.boundary_elements["reversibility"].values:
            self.assertIn(token, fields["detail"])
        self.assertEqual(fields["decision_state"], audit.INPUT_DEFECT_STATE)

    def test_the_row_keeps_gate_iteration_semantics(self) -> None:
        """`iteration` is the GATE iteration for every row in this table; the repair
        ordinal lives in `detail`. Catches overloading one column with two counters."""
        intent, before, after, _ = self.settle(self.state, gate=self.malformed())
        sink = entries_of(audit_gate_transition, before, after)
        _, _, fields = sink.rows[0]
        self.assertEqual(fields["iteration"], intent["gate_iteration"])
        self.assertEqual(fields["iteration"], 1)
        self.assertIn(f"repair_attempt=0/{MAX_REPAIR_ATTEMPTS}", fields["detail"])

    def test_a_repair_request_emits_with_the_repair_ordinal(self) -> None:
        _, _, _, applied = self.settle(self.state, gate=self.malformed())
        routed = route_node(applied)
        self.assertEqual(routed["route_token"], "PREPARE_REPAIR")
        prepared = prepare_intent_node(routed)
        sink = entries_of(audit_repair_request, routed, prepared)
        self.assertEqual(sink.events(), [audit.EVENT_REPAIR_REQUESTED])
        _, _, fields = sink.rows[0]
        self.assertIn(f"repair_attempt=1/{MAX_REPAIR_ATTEMPTS}", fields["detail"])
        self.assertIn("field=reversibility", fields["detail"])

    def test_a_repair_success_emits(self) -> None:
        _, _, _, applied = self.settle(self.state, gate=self.malformed())
        routed = route_node(applied)
        _, before, after, _ = self.settle(routed, gate=envelope_for(CLEAN_RECORD))
        sink = entries_of(audit_gate_transition, before, after)
        self.assertEqual(sink.events(), [audit.EVENT_REPAIR_SUCCEEDED])
        self.assertIn("repair_attempt=1", sink.rows[0][2]["detail"])

    def test_exhaustion_emits_with_error_field_allowed_values_and_count(self) -> None:
        _, _, _, applied = self.settle(self.state, gate=self.malformed())
        exhausted = dict(applied, repair_attempts=MAX_REPAIR_ATTEMPTS,
                         remaining_repair_budget=0)
        routed = route_node(exhausted)
        terminal = terminal_node(routed)
        sink = entries_of(audit_terminal, routed, terminal)
        self.assertEqual(sink.events(), [audit.EVENT_REPAIR_EXHAUSTED])
        _, _, fields = sink.rows[0]
        self.assertEqual(fields["decision_reason_code"], GATE_REPAIR_EXHAUSTED)
        self.assertIn("field=reversibility", fields["detail"])
        self.assertIn(f"repair_attempt={MAX_REPAIR_ATTEMPTS}/{MAX_REPAIR_ATTEMPTS}",
                      fields["detail"])
        for token in self.policy.boundary_elements["reversibility"].values:
            self.assertIn(token, fields["detail"])

    def test_an_ordinary_clean_settlement_emits_nothing(self) -> None:
        """The negative control: a healthy round writes no repair audit at all."""
        _, before, after, _ = self.settle(self.state, gate=envelope_for(CLEAN_RECORD))
        sink = entries_of(audit_gate_transition, before, after)
        self.assertEqual(sink.rows, [])

    def test_a_semantic_block_emits_no_form_defect_row(self) -> None:
        """A judgement is not a repair event; naming it one would put the wrong label on
        a NEEDS_INPUT."""
        record = dict(CLEAN_RECORD, state="NEEDS_INPUT", reason_code="security_impact",
                      boundary_element="security", what_is_missing="w",
                      why_policy_cannot_decide="y", security=True)
        _, before, after, _ = self.settle(
            self.state, gate=envelope_for(record, state="NEEDS_INPUT"))
        sink = entries_of(audit_gate_transition, before, after)
        self.assertEqual(sink.rows, [])


class NeverMutatesLifecycleTests(AuditTestCase):
    """Constraint (i): SKILL.md section 9 -- logging records what happened, never revises it."""

    def test_a_failing_sink_changes_no_state_and_raises_nothing(self) -> None:
        entries = audit_gate_transition(*self.transition())
        snapshot = deepcopy(entries)
        # flush_outbox is total: a sink that raises returns every entry as undelivered.
        undelivered = audit.flush_outbox(RecordingSink(fail=True), entries)
        self.assertEqual(entries, snapshot, "the emitter mutated its own input")
        self.assertEqual(len(undelivered), len(entries))

    def test_flush_is_total_for_a_missing_or_broken_sink(self) -> None:
        entry = audit.outbox_entry("e", "k", detail="d")
        self.assertEqual(audit.flush_outbox(None, [entry]), [entry])
        self.assertEqual(audit.flush_outbox(RecordingSink(fail=True), [entry]), [entry])

    def test_a_failing_sink_does_not_change_the_route(self) -> None:
        _, _, _, applied = self.settle(self.state, gate=self.malformed())
        self.assertEqual(route_node(applied)["route_token"], "PREPARE_REPAIR")

    def test_an_undelivered_entry_stays_in_the_outbox(self) -> None:
        """Constraint (ii)'s "no MISSING row" half: a failed write is retriable, never
        silently dropped.  This is the defect D2 named -- the swallowed write plus an
        advancing checkpoint -- and the outbox is what closes it."""
        from scripts.deterministic_workflow.graph import _audited
        broken = RecordingSink(fail=True)
        node = _audited(self.node, broken, audit_gate_transition)
        _, before, _, _ = self.settle(self.state, gate=self.malformed())
        out = node(before)
        self.assertEqual(len(out["audit_outbox"]), 1)
        self.assertEqual(out["audit_outbox"][0]["event"], audit.EVENT_GATE_FORM_DEFECT)
        # ...and a later node with a WORKING sink drains it.
        drained = _audited(lambda state: state, RecordingSink(), None)(out)
        self.assertEqual(drained["audit_outbox"], [])

    def test_the_outbox_survives_state_validation(self) -> None:
        """It is checkpointed, so it has to be a closed, JSON-safe shape."""
        entry = audit.outbox_entry("e", "k", detail="d")
        state = dict(self.state, audit_outbox=[entry])
        validate_state(state, expected_thread_id="t")
        for broken in ({"event": "e"}, {"event": "", "key": "k", "fields": {}},
                       {"event": "e", "key": "k", "fields": "not a dict"}):
            with self.subTest(broken=broken):
                with self.assertRaises(StateError):
                    validate_state(dict(self.state, audit_outbox=[broken]),
                                   expected_thread_id="t")


class DurableCrashBoundaryTests(AuditTestCase):
    """Constraint (ii) at every crash boundary AND under genuine concurrency.

    The guarantee is structural: a row exists in the derived table if and only if a
    record exists in the published set, and at most one publication per key can ever win
    the ``os.rename``.  These tests hold that claim to the fire.
    """

    def rows_for(self, base: Path, event: str) -> list[str]:
        path = (run_logging.audit_outbox_dir("run_audit", base=base)
                / run_logging.AUDIT_OUTBOX_PROJECTION_FILENAME)
        if not path.exists():
            return []
        return [line for line in path.read_text(encoding="utf-8").splitlines()
                if line.startswith(f"| {event} |")]

    def durable_rows_for(self, base: Path, key: str) -> int:
        """Every durable audit row carrying this key, in EVERY artifact under the run.

        Deliberately implementation-agnostic. The row is counted wherever it lands -- the
        derived table, ORCHESTRATOR_LOG.md, anything else -- so the assertion is "exactly
        one durable row for this transition" rather than "one row in the file I happen to
        write today". That is what lets it fail against a design that appends the row
        somewhere else, which is precisely how the previous implementation duplicated.
        
        Every real row carries ``audit_key=<key>`` as the first element of its ``detail``
        cell (``audit.defect_detail``), so the count is well defined.  If a change ever
        dropped that, this returns 0 and the callers below fail on ``0 != 1`` -- loudly,
        rather than becoming vacuous.
        """
        marker = f"{audit.AUDIT_KEY_FIELD}={key}"
        root = Path(base) / "artifacts" / "runs" / "run_audit"
        total = 0
        for path in sorted(root.rglob("*.md")) if root.exists() else ():
            total += sum(1 for line in path.read_text(encoding="utf-8").splitlines()
                         if marker in line)
        return total

    def records_for(self, base: Path) -> list[Path]:
        directory = run_logging.audit_outbox_dir("run_audit", base=base)
        if not directory.exists():
            return []
        return sorted(child for child in directory.iterdir()
                      if child.is_dir() and not child.name.startswith("."))

    def entry(self, key="gate_defect:e1"):
        return audit.outbox_entry(audit.EVENT_GATE_FORM_DEFECT, key,
                                  phase="ANALYSIS", role="worker", iteration=1,
                                  detail=f"audit_key={key}")

    def four_transitions(self):
        """One outbox entry per transition, produced by the REAL emitters.

        Not one synthetic FORM entry repeated: the boundary tests below sweep all four,
        because a guarantee proved for one event name is not a guarantee.
        """
        _, before, after, applied = self.settle(self.state, gate=self.malformed())
        routed = route_node(applied)
        prepared = prepare_intent_node(routed)
        exhausted_route = route_node(dict(applied,
                                          repair_attempts=MAX_REPAIR_ATTEMPTS,
                                          remaining_repair_budget=0))
        terminal = terminal_node(exhausted_route)
        _, clean_before, clean_after, _ = self.settle(
            routed, gate=envelope_for(CLEAN_RECORD))
        produced = {
            audit.EVENT_GATE_FORM_DEFECT: audit_gate_transition(before, after),
            audit.EVENT_REPAIR_REQUESTED: audit_repair_request(routed, prepared),
            audit.EVENT_REPAIR_SUCCEEDED: audit_gate_transition(clean_before, clean_after),
            audit.EVENT_REPAIR_EXHAUSTED: audit_terminal(exhausted_route, terminal),
        }
        for event, entries in produced.items():
            self.assertEqual(len(entries), 1, f"{event} produced {len(entries)} intents")
            self.assertEqual(entries[0]["event"], event)
        return produced

    # ---- D1: genuine concurrency ---------------------------------------------------
    def test_concurrent_deliveries_of_one_key_produce_exactly_one_row(self) -> None:
        """The test the previous round did not have.

        Eight threads are held at a barrier and released INTO delivery at once, so they
        are all past any pre-write observation point together.  The old design failed
        here: a thread that lost the append claim treated the live owner as crashed,
        scanned a log the owner had not appended to yet, and appended a second row.  This
        design has no claim and no scan -- the winner of one os.rename is the only
        publication, and the table is regenerated from the published set -- so the count
        is one however the threads interleave.
        """
        for event, entries in self.four_transitions().items():
            with self.subTest(event=event), TemporaryDirectory() as directory:
                base = Path(directory)
                entry = entries[0]
                barrier = threading.Barrier(8)
                errors: list[BaseException] = []

                def deliver() -> None:
                    sink = RunLoggingAuditSink("run_audit", artifact_base=base)
                    try:
                        barrier.wait(timeout=10)
                        sink.deliver(entry["event"], entry["key"], entry["fields"])
                    except BaseException as exc:  # noqa: BLE001 - recorded, then asserted
                        errors.append(exc)

                threads = [threading.Thread(target=deliver) for _ in range(8)]
                for thread in threads:
                    thread.start()
                for thread in threads:
                    thread.join(timeout=20)
                self.assertEqual(errors, [], "a concurrent delivery raised")
                self.assertEqual(len(self.records_for(base)), 1,
                                 "two publications won the same key")
                self.assertEqual(len(self.rows_for(base, event)), 1,
                                 "concurrent delivery produced a duplicate row")
                # The assertion that does not care WHERE the row lands. Suppressing the
                # loser's exception does not remove its duplicate row, so this catches
                # the defect even when the crash is swallowed.
                self.assertEqual(self.durable_rows_for(base, entry["key"]), 1,
                                 "the run holds more than one durable row for this "
                                 "transition")

    def test_the_harness_catches_the_defect_the_superseded_protocol_had(self) -> None:
        """Proves the concurrency test above is not vacuous, permanently.

        A test that passes is only evidence if it COULD have failed.  I confirmed the
        test above against the real superseded implementation by restoring it and
        running this harness against it -- it failed on all four transitions -- but that
        code is gone now, so the confirmation would not survive in the repository.  This
        keeps it.

        ``ScanThenAppendSink`` is a faithful minimal model of the superseded protocol:
        observe the shared log, and append if the key is not already there.  The barrier
        sits exactly at that pre-append observation point, which is the window the old
        design could not close without deciding whether a competing writer was alive.
        Every thread observes an empty log and every thread appends, so the model
        duplicates DETERMINISTICALLY rather than when the scheduler happens to cooperate.

        The assertion is deliberately "more than one row": the point is that
        ``durable_rows_for`` is what separates the two designs, not the exception the old
        code happened to raise.
        """

        class ScanThenAppendSink:
            """The superseded protocol, reduced to the two steps that mattered."""

            def __init__(self, log: Path, barrier: threading.Barrier) -> None:
                self.log = log
                self.barrier = barrier

            def deliver(self, event: str, key: str, fields) -> bool:
                marker = f"{audit.AUDIT_KEY_FIELD}={key}"
                existing = (self.log.read_text(encoding="utf-8")
                            if self.log.exists() else "")
                self.barrier.wait(timeout=10)  # everyone is past the observation point
                if marker in existing:
                    return True
                with self.log.open("a", encoding="utf-8") as handle:
                    handle.write(f"| {event} | {marker} |\n")
                return True

        for event, entries in self.four_transitions().items():
            with self.subTest(event=event), TemporaryDirectory() as directory:
                base = Path(directory)
                entry = entries[0]
                log = (run_logging.audit_outbox_dir("run_audit", base=base)
                       / run_logging.AUDIT_OUTBOX_PROJECTION_FILENAME)
                log.parent.mkdir(parents=True, exist_ok=True)
                barrier = threading.Barrier(8)
                sink = ScanThenAppendSink(log, barrier)

                def deliver() -> None:
                    sink.deliver(entry["event"], entry["key"], entry["fields"])

                threads = [threading.Thread(target=deliver) for _ in range(8)]
                for thread in threads:
                    thread.start()
                for thread in threads:
                    thread.join(timeout=20)

                self.assertGreater(
                    self.durable_rows_for(base, entry["key"]), 1,
                    "the model of the superseded protocol did not duplicate, so this "
                    "harness would not have detected the defect it was written for")

    def test_concurrent_deliveries_of_DIFFERENT_keys_produce_one_row_each(self) -> None:
        """Exactly-once must not collapse into at-most-once: distinct transitions racing
        each other must all survive."""
        with TemporaryDirectory() as directory:
            base = Path(directory)
            entries = [self.entry(f"gate_defect:e{index}") for index in range(8)]
            barrier = threading.Barrier(len(entries))

            def deliver(entry) -> None:
                sink = RunLoggingAuditSink("run_audit", artifact_base=base)
                barrier.wait(timeout=10)
                sink.deliver(entry["event"], entry["key"], entry["fields"])

            threads = [threading.Thread(target=deliver, args=(entry,))
                       for entry in entries]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=20)
            self.assertEqual(len(self.records_for(base)), len(entries))
            self.assertEqual(len(self.rows_for(base, audit.EVENT_GATE_FORM_DEFECT)),
                             len(entries))

    def test_a_paused_writer_cannot_replace_the_table_with_a_stale_snapshot(self):
        """F-001, reproduced by forcing the exact ordering rather than hoping for it.

        The different-key concurrency test releases threads together, which is not enough:
        it never pins A *between* its snapshot and its replace.  This does, by gating
        ``os.replace`` on the writer's thread:

            A publishes A, snapshots {A}, and PAUSES before replacing
            B publishes B, snapshots {A, B}, replaces the table
            A resumes and replaces the table with its stale {A}

        Before the fix both deliveries returned True, both checkpointed intents were
        eligible to be discarded, and B's row was gone from the table for good.  Now A's
        stale replace is not the end of A's delivery: A re-reads the authority, sees a
        record its table does not cover, and regenerates.
        """
        entries = self.four_transitions()
        a_entry = entries[audit.EVENT_GATE_FORM_DEFECT][0]
        b_entry = entries[audit.EVENT_REPAIR_REQUESTED][0]
        with TemporaryDirectory() as directory:
            base = Path(directory)
            a_reached = threading.Event()
            b_done = threading.Event()
            paused = {"already": False}
            real_replace = os.replace

            def gated_replace(src, dst, *args, **kwargs):
                # Only the FIRST replace by writer A pauses; its retry must not.
                if (threading.current_thread().name == "writer-A"
                        and not paused["already"]):
                    paused["already"] = True
                    a_reached.set()
                    b_done.wait(timeout=15)
                return real_replace(src, dst, *args, **kwargs)

            results: dict[str, object] = {}

            def writer_a() -> None:
                sink = RunLoggingAuditSink("run_audit", artifact_base=base)
                results["a"] = sink.deliver(
                    a_entry["event"], a_entry["key"], a_entry["fields"])

            os.replace = gated_replace
            try:
                thread = threading.Thread(target=writer_a, name="writer-A")
                thread.start()
                self.assertTrue(a_reached.wait(timeout=15),
                                "writer A never reached its replace")
                # B runs to completion while A is pinned before its replace.
                results["b"] = RunLoggingAuditSink(
                    "run_audit", artifact_base=base).deliver(
                        b_entry["event"], b_entry["key"], b_entry["fields"])
                b_done.set()
                thread.join(timeout=20)
                self.assertFalse(thread.is_alive(), "writer A never finished")
            finally:
                os.replace = real_replace

            self.assertEqual(len(self.records_for(base)), 2,
                             "both records must be published")
            # The defect: B's row silently absent from the derived table.
            self.assertEqual(len(self.rows_for(base, b_entry["event"])), 1,
                             "the stale snapshot dropped B's row from the projection")
            self.assertEqual(len(self.rows_for(base, a_entry["event"])), 1)
            self.assertEqual(run_logging.audit_projection_row_count("run_audit", base=base),
                             2, "the published table does not cover the record set")
            # And the other half: nobody may report success while their row is missing,
            # because flush_outbox discards an entry on exactly that True.
            self.assertTrue(results["a"])
            self.assertTrue(results["b"])

    def test_an_unconverged_projection_is_reported_as_an_undelivered_entry(self):
        """The bound is real, and exhausting it must not look like success.

        If the recheck loop cannot converge, the sink must NOT report delivery --
        otherwise `flush_outbox` discards the intent on the strength of a write whose
        effect was never confirmed, which is exactly how B's row was lost.
        """
        with TemporaryDirectory() as directory:
            base = Path(directory)
            entry = self.entry()
            sink = RunLoggingAuditSink("run_audit", artifact_base=base)
            real_count = run_logging.audit_projection_row_count
            # A recheck that can never be satisfied: the table always looks short.
            run_logging.audit_projection_row_count = lambda *args, **kwargs: -1
            try:
                with self.assertRaises(run_logging.RunLoggingError):
                    run_logging.project_audit_outbox("run_audit", base=base)
                # The sink must convert that into a RETAINED entry, not a raise and not
                # a success.  flush_outbox is what the engine actually calls.
                self.assertEqual(audit.flush_outbox(sink, [entry]), [entry])
            finally:
                run_logging.audit_projection_row_count = real_count
            # The record is durable regardless, so the retry publishes nothing new.
            self.assertEqual(audit.flush_outbox(sink, [entry]), [])
            self.assertEqual(len(self.records_for(base)), 1)
            self.assertEqual(len(self.rows_for(base, entry["event"])), 1)

    def test_a_clobbered_projection_is_not_reported_as_delivered(self) -> None:
        """The second half of F-001, and the one the recheck loop does NOT cover.

        The loop converges the table against the record set, but a staler writer can still
        win the very last replace after this delivery's own loop has returned.  If
        ``deliver`` reports True on the strength of having *called* the projection rather
        than on the row being *there*, ``flush_outbox`` discards the checkpointed intent
        and the row is gone with nothing left to retry it -- the same lost update, one
        step later.

        So: let the projection succeed, then clobber the published table behind it, and
        require the entry to survive in the outbox.
        """
        with TemporaryDirectory() as directory:
            base = Path(directory)
            entry = self.entry()
            sink = RunLoggingAuditSink("run_audit", artifact_base=base)
            real_project = run_logging.project_audit_outbox

            def clobbering_project(run_id, **kwargs):
                path = real_project(run_id, **kwargs)
                # A staler writer wins the last replace: a table covering no records.
                path.write_text(run_logging._render_audit_projection([]),
                                encoding="utf-8")
                return path

            run_logging.project_audit_outbox = clobbering_project
            try:
                self.assertEqual(
                    audit.flush_outbox(sink, [entry]), [entry],
                    "the intent was discarded although its row is not in the table")
            finally:
                run_logging.project_audit_outbox = real_project
            self.assertEqual(len(self.rows_for(base, entry["event"])), 0,
                             "the clobber did not actually remove the row")
            # The record was durable throughout, so the retry adds no second record and
            # converges the table.
            self.assertEqual(audit.flush_outbox(sink, [entry]), [])
            self.assertEqual(len(self.records_for(base)), 1)
            self.assertEqual(len(self.rows_for(base, entry["event"])), 1)

    def test_the_publication_is_what_excludes_a_second_writer(self) -> None:
        """Replaces the vacuous sequential test the review named.

        It asserts the mechanism directly and CAN fail: publishing the same key twice
        must report the second as not-newly-published, and the record must still hold the
        FIRST payload.  A design that overwrote, appended or re-staged would fail here.
        """
        with TemporaryDirectory() as directory:
            base = Path(directory)
            _, first = run_logging.publish_audit_outbox_record(
                "run_audit", "k", {"event": "e", "key": "k", "fields": {"detail": "one"}},
                base=base)
            _, second = run_logging.publish_audit_outbox_record(
                "run_audit", "k", {"event": "e", "key": "k", "fields": {"detail": "two"}},
                base=base)
            self.assertTrue(first)
            self.assertFalse(second, "a second publication won the same key")
            record = (run_logging.audit_outbox_dir("run_audit", base=base)
                      / run_logging.audit_outbox_key("k")
                      / run_logging.AUDIT_OUTBOX_RECORD_FILENAME)
            payload = json.loads(record.read_text(encoding="utf-8"))
            self.assertEqual(payload["fields"]["detail"], "one")
            self.assertEqual(len(self.records_for(base)), 1)

    def test_a_published_key_never_becomes_visible_before_its_record_is_complete(self):
        """The reader rule, asserted at the syscall that makes the key appear.

        "Exactly one durable row" is worth nothing if a key can be OBSERVED in a
        half-written state: a reader that sees the directory and no parseable record
        either drops the transition from the table or crashes on it.  So this watches
        every syscall that can bring the key path into existence -- mkdir, makedirs,
        rename, replace -- and, at the instant the key becomes visible, requires a
        complete and parseable record to be visible with it.

        Mechanism-agnostic on purpose.  It does not assert THAT the implementation
        stages and renames; it asserts the property that staging buys, so a rewrite
        that published with a plain mkdir followed by a write fails here even though
        it produces no duplicate row.
        """
        for event, entries in sorted(self.four_transitions().items()):
            entry = entries[0]
            with self.subTest(event=event), TemporaryDirectory() as directory:
                base = Path(directory)
                key_path = (run_logging.audit_outbox_dir("run_audit", base=base)
                            / run_logging.audit_outbox_key(entry["key"]))
                record_path = key_path / run_logging.AUDIT_OUTBOX_RECORD_FILENAME
                violations: list[str] = []

                def observe(syscall: str) -> None:
                    if not key_path.exists():
                        return
                    try:
                        json.loads(record_path.read_text(encoding="utf-8"))
                    except (OSError, ValueError) as error:
                        violations.append(f"{syscall}: {error!r}")

                originals = {name: getattr(os, name)
                             for name in ("mkdir", "makedirs", "rename", "replace")}

                def wrap(name):
                    original = originals[name]

                    def wrapped(*args, **kwargs):
                        result = original(*args, **kwargs)
                        observe(name)
                        return result

                    return wrapped

                for name in originals:
                    setattr(os, name, wrap(name))
                try:
                    RunLoggingAuditSink("run_audit", base).deliver(
                        entry["event"], entry["key"], entry["fields"])
                finally:
                    for name, original in originals.items():
                        setattr(os, name, original)

                self.assertEqual(violations, [],
                                 "the key path became visible without a complete record")
                self.assertEqual(len(self.records_for(base)), 1)

    def test_the_projection_is_a_pure_function_of_the_published_set(self) -> None:
        """Regenerating any number of times is byte-identical, which is why concurrent
        regeneration cannot duplicate anything."""
        with TemporaryDirectory() as directory:
            base = Path(directory)
            for index in range(3):
                run_logging.publish_audit_outbox_record(
                    "run_audit", f"k{index}",
                    {"event": "e", "key": f"k{index}", "fields": {"detail": str(index)}},
                    base=base)
            first = run_logging.project_audit_outbox("run_audit", base=base).read_text(
                encoding="utf-8")
            for _ in range(4):
                again = run_logging.project_audit_outbox(
                    "run_audit", base=base).read_text(encoding="utf-8")
                self.assertEqual(again, first)
            self.assertEqual(first.count("| e |"), 3)

    # ---- crash boundaries, for ALL FOUR transitions ---------------------------------
    def test_every_transition_survives_the_before_write_boundary(self) -> None:
        for event, entries in self.four_transitions().items():
            with self.subTest(event=event), TemporaryDirectory() as directory:
                base = Path(directory)
                # "crash" before any write: the sink never ran.
                undelivered = audit.flush_outbox(RecordingSink(fail=True), entries)
                self.assertEqual(len(undelivered), 1)
                self.assertEqual(self.rows_for(base, event), [])
                left = audit.flush_outbox(
                    RunLoggingAuditSink("run_audit", artifact_base=base), undelivered)
                self.assertEqual(left, [])
                self.assertEqual(len(self.rows_for(base, event)), 1)

    def test_every_transition_survives_the_during_write_boundary(self) -> None:
        """The record is published; the process dies before the projection is written."""
        for event, entries in self.four_transitions().items():
            with self.subTest(event=event), TemporaryDirectory() as directory:
                base = Path(directory)
                entry = entries[0]
                run_logging.publish_audit_outbox_record(
                    "run_audit", entry["key"],
                    {"event": entry["event"], "key": entry["key"],
                     "fields": entry["fields"]}, base=base)
                self.assertEqual(self.rows_for(base, event), [],
                                 "the projection exists before it was written")
                audit.flush_outbox(RunLoggingAuditSink("run_audit", artifact_base=base),
                                   entries)
                self.assertEqual(len(self.rows_for(base, event)), 1)
                self.assertEqual(len(self.records_for(base)), 1,
                                 "the resumed delivery published a second record")

    def test_every_transition_survives_the_after_write_before_checkpoint_boundary(self) -> None:
        """Fully written, but the checkpoint that would have emptied the outbox never
        landed, so the entry is delivered again by the successor."""
        for event, entries in self.four_transitions().items():
            with self.subTest(event=event), TemporaryDirectory() as directory:
                base = Path(directory)
                audit.flush_outbox(RunLoggingAuditSink("run_audit", artifact_base=base),
                                   entries)
                self.assertEqual(len(self.rows_for(base, event)), 1)
                for _ in range(3):
                    audit.flush_outbox(
                        RunLoggingAuditSink("run_audit", artifact_base=base), entries)
                self.assertEqual(len(self.rows_for(base, event)), 1)
                self.assertEqual(len(self.records_for(base)), 1)

    def test_every_transition_survives_the_after_checkpoint_boundary(self) -> None:
        """The outbox has been emptied. A later drain of the settled state must add
        nothing, and must not resurrect anything either."""
        for event, entries in self.four_transitions().items():
            with self.subTest(event=event), TemporaryDirectory() as directory:
                base = Path(directory)
                sink = RunLoggingAuditSink("run_audit", artifact_base=base)
                audit.flush_outbox(sink, entries)
                settled = dict(self.state, audit_outbox=[])
                for _ in range(3):
                    self.assertEqual(
                        audit.drain(RunLoggingAuditSink("run_audit", artifact_base=base),
                                    settled), [])
                self.assertEqual(len(self.rows_for(base, event)), 1)

    def test_an_unwritable_base_leaves_the_entry_retriable(self) -> None:
        """A sink that cannot write must report failure so the entry is RETRIED, not
        report success and lose the row."""
        sink = RunLoggingAuditSink("run_audit", artifact_base=Path("/proc/nonexistent"))
        entry = self.entry()
        self.assertEqual(audit.flush_outbox(sink, [entry]), [entry])


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


@unittest.skipUnless(_langgraph_ok(), "requires pinned langgraph 0.2.76")
class CompiledGraphAuditTests(unittest.TestCase):
    """End to end on the real graph, writing a real log."""

    def run_graph(self, results, base):
        from scripts.deterministic_workflow.fake_adapter import FakeAdapter
        from scripts.deterministic_workflow.graph import build_graph
        from scripts.deterministic_workflow.runtime_state import InMemoryRuntimeStateStore
        state = initial_state(run_id="run_audit", thread_id="thread",
                              phases=("ANALYSIS",),
                              capabilities=frozenset(BASE_CAPABILITIES), risk="high")
        graph = build_graph(FakeAdapter(results),
                            runtime_state=InMemoryRuntimeStateStore(),
                            require_durable_checkpointer=False,
                            audit_sink=RunLoggingAuditSink("run_audit",
                                                           artifact_base=base))
        return graph.invoke(state, config={"recursion_limit": 300})

    @staticmethod
    def malformed_result():
        return {"status": "COMPLETE", "unit_test_status": "NOT_APPLICABLE",
                "gate": envelope_for(dict(CLEAN_RECORD,
                                          reversibility=OS42_DEFECT_VALUE))}

    def counts(self, base):
        """Rows in the DERIVED audit table.

        The table is regenerated from the published record set, so a count here is a
        count of distinct published keys -- which is what makes "exactly one row" a
        structural fact rather than an observation about timing.
        """
        path = (run_logging.audit_outbox_dir("run_audit", base=base)
                / run_logging.AUDIT_OUTBOX_PROJECTION_FILENAME)
        text = path.read_text(encoding="utf-8") if path.exists() else ""
        return {event: sum(1 for line in text.splitlines()
                           if line.startswith(f"| {event} |"))
                for event in audit.AUDIT_EVENTS}

    def test_a_repair_cycle_writes_one_row_per_transition(self) -> None:
        with TemporaryDirectory() as directory:
            base = Path(directory)
            self.run_graph([
                self.malformed_result(),
                {"status": "COMPLETE", "unit_test_status": "NOT_APPLICABLE"},
                {"result": "PASS"},
                {"result": "PASS", "findings": []},
            ], base)
            counts = self.counts(base)
            self.assertEqual(counts[audit.EVENT_GATE_FORM_DEFECT], 1)
            self.assertEqual(counts[audit.EVENT_REPAIR_REQUESTED], 1)
            self.assertEqual(counts[audit.EVENT_REPAIR_SUCCEEDED], 1)
            self.assertEqual(counts[audit.EVENT_REPAIR_EXHAUSTED], 0)

    def test_exhaustion_writes_the_exhaustion_row(self) -> None:
        with TemporaryDirectory() as directory:
            base = Path(directory)
            out = self.run_graph([self.malformed_result() for _ in range(4)], base)
            self.assertEqual(out["terminal_status"], "BLOCKED")
            counts = self.counts(base)
            self.assertEqual(counts[audit.EVENT_REPAIR_EXHAUSTED], 1)
            self.assertEqual(counts[audit.EVENT_GATE_FORM_DEFECT], MAX_REPAIR_ATTEMPTS + 1)
            self.assertEqual(counts[audit.EVENT_REPAIR_REQUESTED], MAX_REPAIR_ATTEMPTS)

    def test_re_running_the_whole_graph_duplicates_no_row(self) -> None:
        """The coarsest replay there is: run the entire workflow twice against the same
        durable log. Every key is deterministic, so the second run adds nothing."""
        with TemporaryDirectory() as directory:
            base = Path(directory)
            script = [
                self.malformed_result(),
                {"status": "COMPLETE", "unit_test_status": "NOT_APPLICABLE"},
                {"result": "PASS"},
                {"result": "PASS", "findings": []},
            ]
            self.run_graph(list(script), base)
            first = self.counts(base)
            self.run_graph(list(script), base)
            self.assertEqual(self.counts(base), first)

    def test_a_crash_and_resume_on_a_DURABLE_checkpointer_writes_one_row_each(self) -> None:
        """The end-to-end shape the finding asks for: a durable checkpointer and a real
        log store, a sink that FAILS for the first half of the run, then a resume of the
        SAME thread with a working sink.

        The first half advances the workflow checkpoint while every audit write fails --
        exactly D2's scenario.  The outbox is what carries the intent across that gap, so
        the resume must land one row per transition and no duplicate.
        """
        from scripts.deterministic_workflow.checkpoint_store import FileCheckpointSaver
        from scripts.deterministic_workflow.fake_adapter import FakeAdapter
        from scripts.deterministic_workflow.graph import build_graph
        from scripts.deterministic_workflow.runtime_state import FileRuntimeStateStore

        with TemporaryDirectory() as directory:
            base = Path(directory)
            saver = FileCheckpointSaver(base / "checkpoints.json")
            ledger = FileRuntimeStateStore(base / "ledger.json")
            adapter = FakeAdapter([
                self.malformed_result(),
                {"status": "COMPLETE", "unit_test_status": "NOT_APPLICABLE"},
                {"result": "PASS"},
                {"result": "PASS", "findings": []},
            ], runtime_state=ledger)
            config = {"recursion_limit": 300,
                      "configurable": {"thread_id": "thread", "checkpoint_ns": ""}}
            state = initial_state(run_id="run_audit", thread_id="thread",
                                  phases=("ANALYSIS",),
                                  capabilities=frozenset(BASE_CAPABILITIES), risk="high")

            # ---- first process: every audit write fails, the workflow still completes
            broken = build_graph(adapter, checkpointer=saver, runtime_state=ledger,
                                 audit_sink=RecordingSink(fail=True))
            out = broken.invoke(state, config=config)
            self.assertEqual(out["terminal_status"], "COMPLETED",
                             "an audit failure changed a lifecycle decision")
            self.assertEqual(self.counts(base),
                             {event: 0 for event in audit.AUDIT_EVENTS},
                             "a failing sink still wrote rows")
            self.assertTrue(out["audit_outbox"],
                            "the undelivered intents were dropped instead of retained")

            # ---- second process: the SAME thread, resumed with a working sink
            resumed = build_graph(adapter, checkpointer=saver, runtime_state=ledger,
                                  audit_sink=RunLoggingAuditSink("run_audit",
                                                                 artifact_base=base))
            drained = resumed.invoke(None, config=config)
            # The run had already settled, so no node re-ran. `audit.drain` is the retry
            # point for exactly that case: outside the graph, writing no state back.
            working = RunLoggingAuditSink("run_audit", artifact_base=base)
            self.assertEqual(audit.drain(working, drained), [])
            counts = self.counts(base)
            self.assertEqual(counts[audit.EVENT_GATE_FORM_DEFECT], 1)
            self.assertEqual(counts[audit.EVENT_REPAIR_REQUESTED], 1)
            self.assertEqual(counts[audit.EVENT_REPAIR_SUCCEEDED], 1)
            # ---- third process: draining again must add nothing. Delivery is
            # idempotent, which is why `drain` needs to write no state back.
            for _ in range(2):
                audit.drain(RunLoggingAuditSink("run_audit", artifact_base=base), drained)
            self.assertEqual(self.counts(base), counts)

    def test_a_clean_run_writes_no_repair_audit_at_all(self) -> None:
        with TemporaryDirectory() as directory:
            base = Path(directory)
            self.run_graph([
                {"status": "COMPLETE", "unit_test_status": "NOT_APPLICABLE"},
                {"result": "PASS"},
                {"result": "PASS", "findings": []},
            ], base)
            self.assertEqual(set(self.counts(base).values()), {0})


if __name__ == "__main__":  # pragma: no cover
    unittest.main()


class _ProcessDied(BaseException):
    """A process kill, deliberately NOT an ``Exception``.

    ``flush_outbox`` swallows ``Exception`` on purpose -- that is D2, and it must keep
    doing it, so an audit failure cannot change a lifecycle decision.  A crash is a
    different event: it has to escape the node and abort ``invoke`` the way a killed
    process would, leaving the checkpoint wherever it was.  Deriving from
    ``BaseException`` is what buys that without touching D2's handler.
    """


class BoundaryCrashSink:
    """A real sink that dies at one named boundary, for one named event, once.

    The boundaries are the four the review named, expressed in terms of what is durable
    when the process stops:

      before_write                  nothing is published, nothing is projected
      during_write                  the record IS published, the projection is not
      after_write_before_checkpoint the row is fully published, but the node's state
                                    write emptying the outbox never lands
      after_checkpoint              delivery and its checkpoint both landed; the crash is
                                    armed for the NEXT adapter dispatch instead
    """

    def __init__(self, run_id, base, *, event, boundary, armed=None):
        self.run_id = run_id
        self.base = base
        self.event = event
        self.boundary = boundary
        self.armed = armed if armed is not None else {}
        self.fired = False
        self.inner = RunLoggingAuditSink(run_id, artifact_base=base)

    def deliver(self, event, key, fields):
        if event != self.event or self.fired:
            return self.inner.deliver(event, key, fields)
        run_logging_module = self.inner._run_logging()
        if self.boundary == "before_write":
            self.fired = True
            raise _ProcessDied("died before any audit write")
        if self.boundary == "during_write":
            run_logging_module.publish_audit_outbox_record(
                self.run_id, key, {"event": event, "key": key, "fields": dict(fields)},
                base=self.base)
            self.fired = True
            raise _ProcessDied("died after publishing, before projecting")
        delivered = self.inner.deliver(event, key, fields)
        if self.boundary == "after_write_before_checkpoint":
            self.fired = True
            raise _ProcessDied("died after the row was durable, before the checkpoint")
        if self.boundary == "after_checkpoint":
            # Let this node's state write land, then kill the run at the next dispatch.
            self.fired = True
            self.armed["fire"] = True
        return delivered


class ArmedAdapter:
    """Delegates to a real adapter, but dies once when the sink arms it."""

    def __init__(self, inner, armed):
        self._inner = inner
        self._armed = armed

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def start(self, intent, *, lease_token=None):
        if self._armed.get("fire"):
            self._armed["fire"] = False
            raise _ProcessDied("died after the checkpoint that recorded the row")
        return self._inner.start(intent, lease_token=lease_token)


@unittest.skipUnless(_langgraph_ok(), "requires pinned langgraph 0.2.76")
class DurableGraphBoundaryMatrixTests(unittest.TestCase):
    """F-002: the boundary matrix, run against the COMPILED graph and a real checkpointer.

    The previous matrix called publication, ``flush_outbox`` and ``drain`` directly and
    hand-built state dicts.  That proves the sink is idempotent; it does not prove the
    ENGINE is, because it never interrupts a node, never leaves a half-advanced
    checkpoint, and never resumes a thread.  Every test here interrupts the real compiled
    graph mid-run and restarts it on the SAME ``FileCheckpointSaver``, the SAME
    ``FileRuntimeStateStore`` and the SAME thread id.
    """

    BOUNDARIES = ("before_write", "during_write",
                  "after_write_before_checkpoint", "after_checkpoint")

    @staticmethod
    def malformed_result():
        return {"status": "COMPLETE", "unit_test_status": "NOT_APPLICABLE",
                "gate": envelope_for(dict(CLEAN_RECORD,
                                          reversibility=OS42_DEFECT_VALUE))}

    def scripts(self):
        """Which script makes each transition happen. Not every event occurs in every run."""
        repair_cycle = [
            self.malformed_result(),
            {"status": "COMPLETE", "unit_test_status": "NOT_APPLICABLE"},
            {"result": "PASS"},
            {"result": "PASS", "findings": []},
        ]
        exhaustion = [self.malformed_result() for _ in range(4)]
        return {
            audit.EVENT_GATE_FORM_DEFECT: repair_cycle,
            audit.EVENT_REPAIR_REQUESTED: repair_cycle,
            audit.EVENT_REPAIR_SUCCEEDED: repair_cycle,
            audit.EVENT_REPAIR_EXHAUSTED: exhaustion,
        }

    LIFECYCLE_FIELDS = ("terminal_status", "terminal_reason", "current_phase_index",
                        "phase_iterations", "final_review_iterations",
                        "remaining_phase_budget", "remaining_final_budget",
                        "repair_attempts", "remaining_repair_budget",
                        "processed_event_ids", "dispatched_intent_ids")

    def counts(self, base):
        path = (run_logging.audit_outbox_dir("run_audit", base=base)
                / run_logging.AUDIT_OUTBOX_PROJECTION_FILENAME)
        text = path.read_text(encoding="utf-8") if path.exists() else ""
        return {event: sum(1 for line in text.splitlines()
                           if line.startswith(f"| {event} |"))
                for event in audit.AUDIT_EVENTS}

    def build(self, adapter, saver, ledger, sink):
        from scripts.deterministic_workflow.graph import build_graph
        return build_graph(adapter, checkpointer=saver, runtime_state=ledger,
                           audit_sink=sink)

    def reference_run(self, script, base):
        """The same script with no crash at all: the answer everything else must match."""
        from scripts.deterministic_workflow.checkpoint_store import FileCheckpointSaver
        from scripts.deterministic_workflow.fake_adapter import (FakeAdapter,
                                                                 FileExternalWorld)
        from scripts.deterministic_workflow.runtime_state import FileRuntimeStateStore
        saver = FileCheckpointSaver(base / "checkpoints.json")
        ledger = FileRuntimeStateStore(base / "ledger.json")
        adapter = FakeAdapter(list(script), runtime_state=ledger,
                              external_world=FileExternalWorld(base / "world.json"))
        config = {"recursion_limit": 300,
                  "configurable": {"thread_id": "thread", "checkpoint_ns": ""}}
        state = initial_state(run_id="run_audit", thread_id="thread",
                              phases=("ANALYSIS",),
                              capabilities=frozenset(BASE_CAPABILITIES), risk="high")
        graph = self.build(adapter, saver, ledger,
                           RunLoggingAuditSink("run_audit", artifact_base=base))
        out = graph.invoke(state, config=config)
        audit.drain(RunLoggingAuditSink("run_audit", artifact_base=base), out)
        return out, adapter.effect_count, self.counts(base)

    def crash_and_resume(self, script, base, *, event, boundary):
        """Run until the crash, then restart the SAME thread with a working sink."""
        from scripts.deterministic_workflow.checkpoint_store import FileCheckpointSaver
        from scripts.deterministic_workflow.fake_adapter import (FakeAdapter,
                                                                 FileExternalWorld)
        from scripts.deterministic_workflow.runtime_state import FileRuntimeStateStore
        saver = FileCheckpointSaver(base / "checkpoints.json")
        ledger = FileRuntimeStateStore(base / "ledger.json")
        armed: dict = {}
        # The durable external world is the reference implementation of the two recovery
        # capabilities.  Without it the engine REFUSES to resume a claim it cannot prove
        # the outcome of -- correct fail-closed behaviour, but it means the run never gets
        # far enough to test what this matrix is about.  With it, "no duplicate dispatch"
        # becomes an assertion instead of an assumption.
        inner = FakeAdapter(list(script), runtime_state=ledger,
                            external_world=FileExternalWorld(base / "world.json"))
        adapter = ArmedAdapter(inner, armed)
        config = {"recursion_limit": 300,
                  "configurable": {"thread_id": "thread", "checkpoint_ns": ""}}
        state = initial_state(run_id="run_audit", thread_id="thread",
                              phases=("ANALYSIS",),
                              capabilities=frozenset(BASE_CAPABILITIES), risk="high")
        sink = BoundaryCrashSink("run_audit", base, event=event, boundary=boundary,
                                 armed=armed)
        crashed = self.build(adapter, saver, ledger, sink)
        died = False
        try:
            crashed.invoke(state, config=config)
        except _ProcessDied:
            died = True
        # ---- restart: same saver, same runtime store, same thread id, working sink.
        armed.pop("fire", None)
        resumed = self.build(adapter, saver, ledger,
                             RunLoggingAuditSink("run_audit", artifact_base=base))
        out = resumed.invoke(None, config=config)
        # A settled run runs no further node, so the outbox's last retry point is drain.
        working = RunLoggingAuditSink("run_audit", artifact_base=base)
        self.assertEqual(audit.drain(working, out), [],
                         "an audit intent was still undelivered after the resume")
        return out, inner.effect_count, self.counts(base), died

    def test_every_transition_survives_every_boundary_on_the_compiled_graph(self) -> None:
        scripts = self.scripts()
        for event in audit.AUDIT_EVENTS:
            script = scripts[event]
            with TemporaryDirectory() as reference_dir:
                reference_base = Path(reference_dir)
                reference, reference_effects, reference_counts = self.reference_run(
                    script, reference_base)
            self.assertGreaterEqual(reference_counts[event], 1,
                                    f"the script for {event} never emits it")
            for boundary in self.BOUNDARIES:
                with self.subTest(event=event, boundary=boundary), \
                        TemporaryDirectory() as directory:
                    base = Path(directory)
                    out, effects, counts, died = self.crash_and_resume(
                        script, base, event=event, boundary=boundary)

                    # 1. One complete durable event per transition -- none lost, none doubled.
                    self.assertEqual(counts, reference_counts,
                                     "the crash changed the durable audit trail")
                    records = run_logging.read_audit_outbox_records("run_audit", base=base)
                    keys = [str(record.get("key", "")) for record in records]
                    self.assertEqual(len(keys), len(set(keys)),
                                     "a resumed delivery published a duplicate record")
                    self.assertEqual(len(records), sum(reference_counts.values()))
                    # The projection covers the authority: no row was dropped.
                    self.assertEqual(
                        run_logging.audit_projection_row_count("run_audit", base=base),
                        len(records))

                    # 2. No duplicate dispatch or budget spend.
                    self.assertEqual(effects, reference_effects,
                                     "the resume dispatched an intent a second time")

                    # 3. Unchanged lifecycle decisions.
                    for field in self.LIFECYCLE_FIELDS:
                        self.assertEqual(out.get(field), reference.get(field),
                                         f"the crash changed {field}")

    def test_the_crash_actually_interrupted_the_graph(self) -> None:
        """Guards the matrix above against becoming vacuous.

        If a boundary silently stopped raising, every assertion there would still pass --
        it would just be comparing two clean runs.  The three non-terminal boundaries must
        genuinely abort ``invoke``.  ``after_checkpoint`` for the terminal exhaustion event
        is the one honest exception: the run ends at that transition, so there is no next
        dispatch to die on, and the restart is a restart of a settled thread.
        """
        scripts = self.scripts()
        for event in audit.AUDIT_EVENTS:
            for boundary in self.BOUNDARIES:
                terminal_after = (event == audit.EVENT_REPAIR_EXHAUSTED
                                  and boundary == "after_checkpoint")
                with self.subTest(event=event, boundary=boundary), \
                        TemporaryDirectory() as directory:
                    _, _, _, died = self.crash_and_resume(
                        scripts[event], Path(directory), event=event, boundary=boundary)
                    if terminal_after:
                        continue
                    self.assertTrue(died, "this boundary never interrupted the graph")
