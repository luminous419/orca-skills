"""OS-37 correction R5 -- where a standalone dispatch FAILURE actually goes.

The Final Adversarial Review's R5 finding was that a standalone dispatch failure "is not
directly routed to correction as required", because a failed dispatch with no report reaches
OS-42's bounded validation-repair loop first.

The user's ruling narrowed that: OS-42's contract must **not** be bypassed, `PREPARE_CORRECTION`
must **not** be advanced ahead of it, `OrcaAdapter`'s failure semantics must be matched with no
standalone-only policy branch -- and the routing for **auth failure**, **non-zero exit** and
**missing/malformed result** must each be locked by its own regression test.

So this module MEASURES the route each of those three causes really takes through a real
`run_workflow.py --adapter standalone` run, and locks it.  What the measurement shows:

1.  Nothing escapes `run_cli` as a traceback.  `StandaloneAdapter.start` converts every
    `StandaloneDispatchFailed` into a typed FAILED settlement through `settle_failed`.
2.  That settlement carries no `DECISION_GATE_STATE` declaration -- a dispatch that died
    wrote no report -- so the OS-42 classifier reports a **FORM** defect, and
    `routing.route` sends it to `PREPARE_REPAIR` while the repair budget lasts.  This is
    the ordering the ruling requires: the repairable failure is repaired FIRST, and the
    phase gate is never consulted for a defective round.
3.  A repair that succeeds lets the run continue and the phase iteration is NOT spent -- a
    repair is not an attempt at the gate.  A failure that persists (an expired credential
    fails every turn) spends the bounded budget and terminates
    `DECISION_GATE_REPAIR_EXHAUSTED`: bounded, named, and never an unbounded retry.
4.  `PREPARE_CORRECTION` is reached by a failed dispatch whose settlement is well-formed --
    the ordinary correction path, driven here through the REAL router over the state a
    failed standalone settlement produces.  It is not, and must not be, reached ahead of
    the repair branch.

None of that is standalone-specific, and `NoStandaloneBranchInThePolicyModulesTests` asserts
it by inspection of the pinned modules rather than by assurance.

**Round 4 (run_61c62f0bf91b, finding 6) narrows clauses 2-4 to the WORKER.**  For a
Reviewer, a runtime failure -- a crash, a silent exit, an auth expiry, a timeout -- is NOT
a settlement at all: `result: FAIL` is a judgement about the work, and routing it (through
the repair loop or straight to correction) spent paid turns and phase iterations on a
finding nobody made.  The two Reviewer-scoped cases below therefore lock the typed
`REVIEWER_RUNTIME_FAILURE` terminal: no settlement row, no ledger settlement, no repair
dispatch, no correction Worker, no phase iteration, and the process proven dead.  The
Worker case (cause 1) keeps the OS-42 ordering exactly as the ruling required.

**One product change belongs to R5.**  `StandaloneDispatchFailed` dropped the observed exit
status, so a dispatch that DIED non-zero and one that exited CLEANLY without ever writing a
completion record settled to byte-identical durable evidence -- both `LOST` /
`exit_code_unmapped`.  Three regression tests over three indistinguishable records would lock
one behaviour three times, so the status now travels with the refusal and lands in the
journal's `completion_verdict`.  `None` still means "never observed", never "exited 0".
"""
from __future__ import annotations

import ast
import importlib.metadata
import os
import re
import shutil
import tempfile
import unittest
from pathlib import Path

from scripts.deterministic_workflow import routing
from scripts.test_os37_external_review_regressions import execute_graph_cli


def _langgraph_ok() -> bool:
    try:
        import langgraph  # noqa: F401
        import langgraph.graph  # noqa: F401
    except ImportError:
        return False
    try:
        return importlib.metadata.version("langgraph") == "0.2.76"
    except importlib.metadata.PackageNotFoundError:
        return False


LANGGRAPH_REASON = "requires pinned langgraph 0.2.76"
ENGINE = Path(__file__).resolve().parent / "deterministic_workflow"


class _FailingRun:
    """One real standalone graph run steered into ONE named failure cause.

    A mixin, so unittest never collects it: each cause is its own class with its own room,
    because the causes differ in the driver's behaviour and sharing a run would make the
    three cases three views of one measurement rather than three locks.
    """

    RUN = ""
    #: `driver_env`, as a function of the room, so a one-shot sentinel path can be named.
    ENV: staticmethod = staticmethod(lambda room: {})

    @classmethod
    def setUpClass(cls) -> None:
        cls.room = Path(tempfile.mkdtemp(prefix=f"os37-r5-{cls.RUN}-"))
        cls.graph = execute_graph_cli(cls.room, run_id=cls.RUN,
                                    driver_env=cls.ENV(cls.room),
                                    timeouts={"completion_timeout_ms": 4_000})

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.room, ignore_errors=True)

    # ---- shared assertions --------------------------------------------------------------
    def assert_nothing_escaped(self) -> None:
        if self.graph.escaped is not None:
            raise AssertionError(
                f"the dispatch failure escaped `run_cli` as "
                f"{type(self.graph.escaped).__name__}: {self.graph.escaped!s}; a dispatch that "
                "cannot produce a verdict must still SETTLE one, so the workflow's own "
                "policy decides what happens next")

    def failed_rows(self) -> list[dict]:
        return [row for row in self.graph.settlement_rows() if row["outcome"] == "failed"]

    def verdicts(self) -> list[dict]:
        return [(row.get("source_vocabulary") or {}).get("completion_verdict") or {}
                for row in self.failed_rows()]

    def test_nothing_escaped_the_graph_as_a_traceback(self) -> None:
        """R5's first clause, for this cause specifically."""
        self.assert_nothing_escaped()
        self.assertTrue(self.graph.summary,
                        "the CLI printed no machine-readable summary at all, so it did not "
                        "return a terminal the workflow decided on")



class _ReviewerRuntimeFailureRun(_FailingRun):
    """A run whose PHASE_REVIEWER suffers a runtime failure.  Round 4, finding 6."""

    def runtime_failures(self) -> list[dict]:
        return [row for row in self.graph.journal_rows()
                if (row.get("source_vocabulary") or {}).get("runtime_failure")]

    def verdicts(self) -> list[dict]:
        return [(row.get("source_vocabulary") or {}).get("runtime_failure") or {}
                for row in self.runtime_failures()]

    def checkpoint_head(self) -> dict:
        from scripts.deterministic_workflow.checkpoint_store import FileCheckpointSaver
        saver = FileCheckpointSaver(self.graph.checkpoint_path)
        stored = saver.get_tuple({"configurable": {"thread_id": "graph",
                                                   "checkpoint_ns": ""}})
        return dict((stored.checkpoint or {}).get("channel_values") or {}) if stored else {}

    def test_the_failure_became_a_typed_runtime_failure_not_a_settlement(self) -> None:
        """Finding 6: a Reviewer's runtime failure is journalled by name and settles
        NOTHING -- there is no verdict to route on, and none is invented."""
        self.assert_nothing_escaped()
        failures = self.runtime_failures()
        self.assertEqual(len(failures), 1, "the failing reviewer left no runtime-failure row")
        self.assertEqual(failures[0]["source_vocabulary"]["code"], "REVIEWER_RUNTIME_FAILURE")
        self.assertEqual(failures[0]["source_vocabulary"]["role"], "PHASE_REVIEWER")
        self.assertEqual(failures[0]["axes"]["settlement"], "not_settled")
        self.assertEqual(failures[0]["axes"]["process_liveness"], "already exited")
        self.assertEqual([row for row in self.graph.settlement_rows()
                          if row["intent_id"] == failures[0]["intent_id"]], [],
                         "the reviewer runtime failure was settled as a verdict")
        self.assertEqual(self.failed_rows(), [], "a FAILED settlement exists for this run")
        self.assertEqual(self.graph.summary.get("terminal_status"), "BLOCKED")
        self.assertEqual((self.graph.summary.get("terminal_reason") or {}).get("code"),
                         "REVIEWER_RUNTIME_FAILURE")


@unittest.skipUnless(_langgraph_ok(), LANGGRAPH_REASON)
class AuthFailureRoutingTests(_FailingRun, unittest.TestCase):
    """Cause 1 -- an EXPIRED CREDENTIAL.  Every turn emits the measured M-15 shape.

    `{"type":"result","subtype":"success","is_error":true,"terminal_reason":"api_error"}`
    followed by `exit 1` -- the capture the standalone drivers were built against.  It is
    the one cause that is UNREPAIRABLE by construction: the credential is still expired on
    the retry, so the bounded repair budget is spent and the run must terminate, named.
    """

    RUN = "run_r5auth"
    ENV = staticmethod(lambda room: {"OS37_GA_AUTH_FAIL": "1"})

    def test_the_failure_became_a_typed_failed_settlement(self) -> None:
        """R5's second clause: a VERDICT in the engine's own vocabulary, not an absence.
        The failing dispatch here is the WORKER (the first turn), whose runtime failure
        is the workflow's own `BLOCKED` status -- round 4 removed this clause for the
        Reviewer roles only."""
        self.assert_nothing_escaped()
        rows = self.failed_rows()
        self.assertTrue(
            rows, "the failing dispatch left no failed settlement in the durable journal; "
                  "there is then nothing for the workflow to route on")
        for row in rows:
            with self.subTest(intent=row["intent_id"]):
                self.assertEqual(row["state"], "FAILED")
                self.assertEqual(row["kind"], "SETTLEMENT_OBSERVED")
                self.assertEqual(row["source_vocabulary"].get("terminal_role"), "WORKER")
                event = (row["source_vocabulary"] or {}).get("event") or {}
                self.assertTrue(event.get("event_id"),
                                "the settlement carries no event id, so the engine's own "
                                "immediate `settlement(intent_id)` read finds nothing")

    def test_the_named_cause_is_the_error_field_not_a_generic_failure(self) -> None:
        """`error_field_set`, so an operator sees WHICH leg of the predicate refused."""
        self.assert_nothing_escaped()
        reasons = {verdict.get("reason") for verdict in self.verdicts()}
        self.assertEqual(reasons, {"error_field_set"},
                         f"the auth failure is not named as the declared error field being "
                         f"set: {reasons}")

    def test_the_settlement_is_never_a_success_however_the_record_is_shaped(self) -> None:
        """The M-15 record says `subtype:"success"`.  It is still a failure, and the
        settlement says so -- this is external review #2, re-asserted for this cause."""
        self.assert_nothing_escaped()
        outcomes = {row["outcome"] for row in self.graph.settlement_rows()}
        self.assertEqual(outcomes, {"failed"},
                         f"a measured authentication failure settled as something other "
                         f"than a failure: {outcomes}")

    def test_the_run_spends_the_bounded_repair_budget_and_terminates_named(self) -> None:
        """The ordering the ruling requires, measured: OS-42 FIRST, bounded, then a
        terminal that NAMES the exhaustion rather than a generic block.

        Mutation-sensitivity: route a failed dispatch straight to correction -- the change
        R5's finding literally asked for and the ruling forbids -- and the terminal stops
        being `DECISION_GATE_REPAIR_EXHAUSTED`.
        """
        self.assert_nothing_escaped()
        reason = self.graph.summary.get("terminal_reason") or {}
        self.assertEqual(self.graph.summary.get("terminal_status"), "BLOCKED")
        self.assertEqual(reason.get("code"), "DECISION_GATE_REPAIR_EXHAUSTED",
                         f"an unrepairable dispatch failure did not end at the bounded "
                         f"repair contract: {reason!r}")
        self.assertEqual(reason.get("repair_attempts"), reason.get("max_repair_attempts"),
                         f"the run stopped without spending the declared budget: {reason!r}")
        self.assertEqual([defect["kind"] for defect in reason.get("defects") or ()],
                         ["FORM"],
                         "the defect a report-less dispatch produces is not the FORM defect "
                         "the repair branch is gated on")

    def test_the_repair_really_re_dispatched_rather_than_looping_in_place(self) -> None:
        """A bounded retry that never re-dispatches would be a budget spent on nothing."""
        self.assert_nothing_escaped()
        reason = self.graph.summary.get("terminal_reason") or {}
        attempts = int(reason.get("repair_attempts") or 0)
        self.assertGreaterEqual(
            len(self.graph.spawn_rows()), attempts + 1,
            f"the run recorded {len(self.graph.spawn_rows())} real spawns for "
            f"{attempts} repair attempts; the repairs cannot all have been dispatched")


@unittest.skipUnless(_langgraph_ok(), LANGGRAPH_REASON)
class NonZeroExitRoutingTests(_ReviewerRuntimeFailureRun, unittest.TestCase):
    """Cause 2 -- the process DIES non-zero with no completion record of any kind.

    An OOM kill, a crash, a `set -e` abort.  Scoped to the first PHASE_REVIEWER dispatch.
    Round 4 (finding 6): the run STOPS, typed, rather than repairing or correcting -- an
    OOM-killed Reviewer produced no judgement, and the runtime hands the engine none.
    """

    RUN = "run_r5exit"
    EXIT_STATUS = 9
    ENV = staticmethod(lambda room: {"OS37_GA_EXIT_CODE_ONCE": str(room / "once"),
                                     "OS37_GA_EXIT_CODE": "9",
                                     "OS37_GA_EXIT_CODE_ROLE": "PHASE_REVIEWER"})

    def test_the_durable_record_names_the_exit_status_the_kernel_reported(self) -> None:
        """R5's product change.  Before it, this record was byte-identical to the
        missing-result case's and the two causes could not be told apart at all.

        Mutation-sensitivity: drop `exit_status=` from the `StandaloneDispatchFailed` raise
        in `_complete` and this reports `None` for a process that demonstrably exited 9.
        """
        self.assert_nothing_escaped()
        statuses = {verdict.get("exit_status") for verdict in self.verdicts()}
        self.assertEqual(statuses, {self.EXIT_STATUS},
                         f"the failed settlement does not name the observed exit status: "
                         f"{statuses}")

    def test_the_cause_is_reported_as_a_loss_not_as_a_reviewer_judgement(self) -> None:
        """`stage=lost`: the dispatch produced NO verdict, which is a different fact from a
        Reviewer that considered the work and failed it -- and (round 4) NO verdict is
        what the engine receives."""
        self.assert_nothing_escaped()
        self.assertEqual({verdict.get("stage") for verdict in self.verdicts()}, {"lost"})
        self.assertEqual({verdict.get("reason") for verdict in self.verdicts()},
                         {"exit_code_unmapped"})

    def test_the_run_stops_typed_without_spending_a_phase_iteration_or_a_repair(self) -> None:
        """Finding 6.  No correction Worker, no repair dispatch, no phase iteration:
        the run's own committed head still holds no reviewer result and the phase and
        repair counters at zero.

        Mutation-sensitivity: settle the Reviewer runtime failure as `result: FAIL`
        again and the head gains a `reviewer_result`, a spent phase iteration and a
        correction (or repair) `DELIVERY_INTENT` after the failure.
        """
        self.assert_nothing_escaped()
        self.assertEqual(self.graph.summary.get("terminal_status"), "BLOCKED",
                         f"{self.graph.summary!r}\n{self.graph.stderr}")
        head = self.checkpoint_head()
        self.assertIsNone(head.get("reviewer_result"))
        self.assertEqual(head.get("phase_iterations"), {"DESIGN": 0},
                         "a runtime failure was charged to the phase budget")
        self.assertEqual(head.get("repair_attempts", 0), 0,
                         "a runtime failure spent the validation-repair budget")
        failure = self.runtime_failures()[0]
        later = [row for row in self.graph.journal_rows()
                 if row["kind"] == "DELIVERY_INTENT" and row["seq"] > failure["seq"]]
        self.assertEqual(later, [], "a dispatch followed the reviewer runtime failure")

    def test_exactly_one_dispatch_failed_and_the_worker_settled_normally(self) -> None:
        """The steering really was one-shot: the Worker settled, the Reviewer failed at
        the runtime, and the process is proven dead."""
        self.assert_nothing_escaped()
        self.assertEqual(len(self.runtime_failures()), 1)
        settled = self.graph.settlement_rows()
        self.assertEqual([row["source_vocabulary"].get("terminal_role") for row in settled],
                         ["WORKER"])
        self.assertEqual(len(self.graph.spawn_rows()), 2)
        pid = int(self.runtime_failures()[0]["source_vocabulary"]["pid"])
        with self.assertRaises(ProcessLookupError):
            os.kill(pid, 0)


@unittest.skipUnless(_langgraph_ok(), LANGGRAPH_REASON)
class MissingResultRoutingTests(_ReviewerRuntimeFailureRun, unittest.TestCase):
    """Cause 3 -- a CLEAN exit with no completion record: a silent CLI.

    The process reached readiness, proved delivery, and then exited 0 having declared
    nothing.  `exit 0` is exactly why this must not be read as success: the runtime requires
    BOTH gates, and an exit status alone is not a verdict.
    """

    RUN = "run_r5miss"
    EXIT_STATUS = 0
    ENV = staticmethod(lambda room: {"OS37_GA_NO_RESULT_ONCE": str(room / "once"),
                                     "OS37_GA_NO_RESULT_ROLE": "PHASE_REVIEWER"})

    def test_a_clean_exit_with_no_record_is_a_failure_not_a_success(self) -> None:
        """The both-gates rule, at the graph.  `exit 0` buys nothing on its own."""
        self.assert_nothing_escaped()
        statuses = {verdict.get("exit_status") for verdict in self.verdicts()}
        self.assertEqual(statuses, {self.EXIT_STATUS},
                         f"the runtime failure does not record the clean exit it observed: "
                         f"{statuses}")
        self.assertEqual(self.failed_rows(), [], "exit 0 without a record was SETTLED")

    def test_it_is_distinguishable_from_a_crash_in_the_durable_record(self) -> None:
        """The whole point of R5's product change, asserted directly against cause 2.

        Both causes reach `LOST/exit_code_unmapped`, so the LOST REASON alone cannot
        separate them; the exit status can, and does.
        """
        self.assert_nothing_escaped()
        self.assertEqual({verdict.get("reason") for verdict in self.verdicts()},
                         {"exit_code_unmapped"},
                         "the shared lost reason changed; this case and the non-zero-exit "
                         "case no longer measure the same discrimination")
        self.assertNotEqual(self.EXIT_STATUS, NonZeroExitRoutingTests.EXIT_STATUS,
                            "the two causes now declare the same exit status, so neither "
                            "test distinguishes anything")

    def test_the_run_stops_typed_and_charges_nothing(self) -> None:
        self.assert_nothing_escaped()
        self.assertEqual(self.graph.summary.get("terminal_status"), "BLOCKED",
                         f"{self.graph.summary!r}\n{self.graph.stderr}")
        head = self.checkpoint_head()
        self.assertIsNone(head.get("reviewer_result"))
        self.assertEqual(head.get("phase_iterations"), {"DESIGN": 0})
        self.assertEqual(head.get("repair_attempts", 0), 0)
        self.assertEqual(len(self.runtime_failures()), 1)


# =====================================================================================
# The ORDERING, and the correction path itself, through the REAL router
# =====================================================================================
class FailedDispatchRoutingOrderTests(unittest.TestCase):
    """`routing.route` over the states a failed standalone settlement produces.

    Driven through the engine's own router rather than through a graph run, because the
    thing being locked is an ORDER between two branches and a run only ever shows the
    winner.  Nothing here is standalone-specific: the states are ordinary workflow states.
    """

    def _state(self, **overrides):
        from scripts.deterministic_workflow.state import initial_state
        from scripts.deterministic_workflow.contracts import BASE_CAPABILITIES
        state = dict(initial_state(run_id="run_r5route", thread_id="t",
                                   phases=("DESIGN",), capabilities=BASE_CAPABILITIES,
                                   risk="high", max_iterations=5))
        state.update(overrides)
        return state

    def test_a_repairable_defect_is_repaired_before_the_phase_gate_is_consulted(self) -> None:
        """The ruling's ordering clause: OS-42 sits ABOVE the gate, and stays there.

        Mutation-sensitivity: move the `pending_gate_defect` branch below the gate in
        `routing.route` and a defective round reaches `PREPARE_PHASE_REVIEWER` instead.
        """
        state = self._state(
            worker_result={"status": "COMPLETE", "unit_test_status": "PASS"},
            pending_gate_defect={"code": "DECISION_GATE_FORM_DEFECT", "defects": []},
            remaining_repair_budget=2)
        self.assertEqual(routing.route(state), "PREPARE_REPAIR")

    def test_an_exhausted_repair_budget_blocks_rather_than_correcting(self) -> None:
        """Bounded.  The budget's floor is a BLOCK, never a fall-through to correction."""
        state = self._state(
            worker_result={"status": "COMPLETE", "unit_test_status": "PASS"},
            pending_gate_defect={"code": "DECISION_GATE_FORM_DEFECT", "defects": []},
            remaining_repair_budget=0)
        self.assertEqual(routing.route(state), "BLOCK")

    def test_an_unrepairable_defect_never_spends_a_repair_attempt(self) -> None:
        """A SEMANTIC or LIFECYCLE defect is not repairable, and the budget is not consulted."""
        for code in ("DECISION_GATE_SEMANTIC_BLOCK", "DECISION_GATE_LIFECYCLE_DEFECT"):
            with self.subTest(code=code):
                state = self._state(
                    worker_result={"status": "COMPLETE", "unit_test_status": "PASS"},
                    pending_gate_defect={"code": code, "defects": []},
                    remaining_repair_budget=2)
                self.assertEqual(routing.route(state), "BLOCK")

    def test_a_well_formed_failed_reviewer_settlement_reaches_the_correction_path(self) -> None:
        """R5's "existing correction path", once the gate defect is discharged.

        This is the state `settle_failed` produces for a PHASE_REVIEWER -- `result=FAIL`,
        from `FAILED_RESULT_DEFAULT` -- and `routing.phase_gate` sends it to
        `PREPARE_CORRECTION` with no CLI-specific branch anywhere in the path.
        """
        state = self._state(
            worker_result={"status": "COMPLETE", "unit_test_status": "PASS"},
            reviewer_result={"result": "FAIL", "review_verdict": "FAIL", "findings": []})
        self.assertEqual(routing.phase_gate(state), "FAIL")
        self.assertEqual(routing.route(state), "PREPARE_CORRECTION")

    def test_a_failed_worker_settlement_blocks_exactly_as_any_blocked_worker_does(
            self) -> None:
        """`OrcaAdapter`'s semantics, matched: `settle_failed` writes the WORKFLOW's own
        BLOCKED vocabulary for a Worker, and the shared gate treats it like any other.

        This is the clause that forbids a standalone-only policy branch: there is no route
        by which a standalone Worker failure reaches an outcome an Orca Worker failure
        would not, because the router cannot see which adapter produced the settlement.
        """
        from scripts.deterministic_workflow import standalone_lifecycle
        field, value = standalone_lifecycle.FAILED_RESULT_BY_ROLE["WORKER"]
        self.assertEqual((field, value), ("status", "BLOCKED"))
        standalone = self._state(worker_result={"status": "BLOCKED",
                                                "unit_test_status": "BLOCKED"})
        orca_equivalent = self._state(worker_result={"status": "BLOCKED",
                                                     "unit_test_status": "BLOCKED"})
        self.assertEqual(routing.route(standalone), routing.route(orca_equivalent))
        self.assertEqual(routing.route(standalone), "BLOCK")

    def test_the_failure_vocabulary_is_the_shared_one_for_every_other_role(self) -> None:
        """A Reviewer failure is `result=FAIL`; nothing invents a per-runtime verdict."""
        from scripts.deterministic_workflow import standalone_lifecycle
        self.assertEqual(standalone_lifecycle.FAILED_RESULT_DEFAULT, ("result", "FAIL"))
        self.assertEqual(sorted(standalone_lifecycle.FAILED_RESULT_BY_ROLE), ["WORKER"],
                         "a role grew its own failure vocabulary; the workflow's result "
                         "fields are policy and a per-role table is where a standalone-only "
                         "branch would start")


class NoStandaloneBranchInThePolicyModulesTests(unittest.TestCase):
    """The pinned policy modules know nothing about this runtime.  Measured, not promised."""

    PINNED = ("routing.py", "graph.py", "executor.py", "state.py", "pause_policy.py")
    #: Every spelling by which a policy module could name this runtime, as WHOLE WORDS.
    #: Anchored at the START of a word only: `StandaloneAdapter`, `CODEX_HOME` and `pty_id`
    #: must all match, while `empty` must not.  A plain `\b...\b` would miss the first two
    #: (a letter follows) and a bare substring search would hit the third, and either way
    #: the inspection stops meaning what it says.
    NEEDLES = re.compile(r"(?<![A-Za-z])(standalone|pty|claude|codex)", re.IGNORECASE)

    def test_no_pinned_policy_module_mentions_the_standalone_runtime(self) -> None:
        offenders = []
        for module in self.PINNED:
            text = (ENGINE / module).read_text(encoding="utf-8")
            for number, line in enumerate(text.splitlines(), start=1):
                if self.NEEDLES.search(line):
                    offenders.append(f"{module}:{number}: {line.strip()}")
        self.assertEqual(offenders, [],
                         "a pinned policy module names the standalone runtime:\n"
                         + "\n".join(offenders))

    def test_the_inspection_really_would_notice_one(self) -> None:
        """The needle set is checked against a line that DOES name the runtime, so a
        pattern that had stopped matching anything could not pass vacuously."""
        for spelling in ("StandaloneAdapter", "a pty session", "the claude driver",
                         "CODEX_HOME"):
            with self.subTest(spelling=spelling):
                self.assertTrue(self.NEEDLES.search(f"    # {spelling}"))
        for innocent in ("the map is empty and nothing is claimed",
                         "an Empty listing is unknown, never absent"):
            with self.subTest(innocent=innocent):
                self.assertIsNone(self.NEEDLES.search(innocent),
                                  "ordinary prose matched, so this inspection would fail "
                                  "for a reason that has nothing to do with a branch")

    def test_no_pinned_policy_module_imports_a_standalone_module(self) -> None:
        """A branch can also arrive as an import.  Both spellings are refused."""
        offenders = []
        for module in self.PINNED:
            tree = ast.parse((ENGINE / module).read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                names: list[str] = []
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    names = [node.module or ""] + [alias.name for alias in node.names]
                for name in names:
                    if "standalone" in name:
                        offenders.append(f"{module}:{node.lineno}: {name}")
        self.assertEqual(offenders, [], "\n".join(offenders))


if __name__ == "__main__":       # pragma: no cover - convenience only
    unittest.main()
