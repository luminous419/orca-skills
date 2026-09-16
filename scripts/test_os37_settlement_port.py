"""OS-37 D2.3 rows 9-18.  The nine call sites reached through the ``graph.py`` default alias.

``graph.py`` assigns ``settlement = settlement_port if settlement_port is not None else
adapter``, so a ``StandaloneAdapter`` receives all five ``LifecycleSettlementPort`` methods
whether or not anybody wired them -- nine of the eighteen direct call sites, plus a tenth
reached through the explicit ``settlement_port=adapter`` in ``launcher``.  DD-1 makes that
alias correct rather than accidental by implementing all five honourably.

The tests are organised by the rule each method must not break, because that is what the
rows differ on:

* ``open_dispatches`` RAISES, never returns a short tuple.  Two call sites read it and one
  of them deliberately swallows to ``[]`` -- the adapter must still raise, so the swallow
  stays the CALLER's decision.
* ``recover_handle`` returns a handle only for ``listing_verified``; ``listing_candidate``
  must not be acted on.
* ``account_dispatch`` is read-only, issues zero commands, and is safe to repeat.
* ``recover_dispatch`` marks ``recovered``, never ``settled``.
* ``release_terminal`` is the only mutating verb, and it releases only what was requested.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts.deterministic_workflow import standalone_journal as journal_mod
from scripts.deterministic_workflow.standalone_adapter import (DISPATCH_UNACCOUNTED,
                                                                StandaloneAdapter)
from scripts.deterministic_workflow.runtime_state import InMemoryRuntimeStateStore


CR = b"\r"
CRLF = CR + b"\n"
#: A CR-LF transcript exactly as a pty produces one.  Built from an explicit CRLF
#: constant rather than written inline, so no tool that rewrites this file can
#: silently turn the carriage returns into real newlines and make the test vacuous.
RAW_GATE_BODY = (b"STATUS: COMPLETE" + CRLF + b"```decision-gate" + CRLF
                 + b"{}" + CRLF + b"```" + CRLF)
RAW_GATE_FENCE = (b"```decision-gate" + CRLF + b'{"state": "CLEAR"}' + CRLF
                  + b"```" + CRLF)
RAW_TWO_LINES = b"line one" + CRLF + b"line two" + CRLF


def event(**overrides) -> dict:
    fields = dict(kind="EVENT", derived_from="pty", intent_id="intent-1",
                  dispatch_id="dispatch-1", task_id="task-1", session_id="s-1",
                  process_incarnation="i-1", event="spawned", state="STARTING",
                  source_vocabulary={"pty_id": "pty-1", "session_digest": "digest-1",
                                     "pid": 4242, "captured_tty": "ttys042"})
    fields.update(overrides)
    return journal_mod.make_record(**fields)


def live_table(*rows: dict, readable: bool = True):
    """An injected tty-scoped process table -- the LIVE authority `recover_handle` verifies
    the journal's candidate against (consolidated review finding 8)."""
    import time as _time

    def reader(tty: str) -> dict:
        return {"tty": tty, "captured_at": _time.time(), "readable": readable,
                "rows": tuple(rows)}
    return reader


AGENT_ROW = {"pid": 4242, "ppid": 4241, "pgid": 4242, "sid": 4241, "tty": "ttys042",
             "stat": "S+"}


def write_spawn_record(base, *, intent_id="intent-1", incarnation="i-1", pid=4242,
                       argv_digest="digest-1") -> None:
    """The CHILD-written spawn record, the second live-side authority."""
    from scripts.deterministic_workflow import standalone_pty as pty_supervisor
    pty_supervisor.write_spawn_record(
        pty_supervisor.spawn_record_path(base, "run_1", intent_id, incarnation),
        {"session_id": "s-1", "process_incarnation": incarnation, "pid": pid,
         "pgid": pid, "sid": 4241, "boot_id": "", "proc_start_ticks": 0,
         "argv_digest": argv_digest, "env_digest": "e", "started_at": ""})


class _Base(unittest.TestCase):

    def setUp(self) -> None:
        self.base = tempfile.mkdtemp()
        self.journal = journal_mod.ExecutionJournal(self.base, "run_1")
        self.ledger = InMemoryRuntimeStateStore()
        self.adapter = StandaloneAdapter(
            None, runtime_state=self.ledger, settlement_journal=self.journal,
            artifact_base=self.base, run_id="run_1", table_reader=live_table(AGENT_ROW))


# =====================================================================================
class OpenDispatchesTests(_Base):
    """Rows 13, 14."""

    def test_open_dispatches_reconstructs_from_disk_alone(self) -> None:
        """Answerable by a process holding none of the creating process's objects."""
        self.journal.append(event())
        self.journal.append(event(intent_id="intent-2"))
        stranger = StandaloneAdapter(
            None, runtime_state=self.ledger,
            settlement_journal=journal_mod.ExecutionJournal(self.base, "run_1"),
            artifact_base=self.base, run_id="run_1")
        self.assertEqual(stranger.open_dispatches(), ("intent-1", "intent-2"))

    def test_a_terminal_record_closes_the_row(self) -> None:
        self.journal.append(event())
        self.journal.append(event(kind="SETTLEMENT_OBSERVED",
                                  derived_from="runtime_state", state="COMPLETED",
                                  outcome="succeeded", message_id="m1",
                                  reported_by="h1"))
        self.assertEqual(self.adapter.open_dispatches(), ())

    def test_open_dispatches_raises_when_unreadable(self) -> None:
        """RAISES rather than returning a short tuple -> ``DISPATCH_UNACCOUNTED``.

        A short tuple would read as "no dispatch is open", which is the one answer a
        pause path must never be given wrongly.
        """
        self.journal.append(event())
        self.journal.path.write_text("{corrupt\n")
        with self.assertRaises(journal_mod.JournalUnreadable):
            self.adapter.open_dispatches()

    def test_open_dispatches_raises_when_no_journal_is_wired(self) -> None:
        unwired = StandaloneAdapter(None, runtime_state=self.ledger,
                                    artifact_base=self.base, run_id="run_1")
        with self.assertRaises(RuntimeError) as caught:
            unwired.open_dispatches()
        self.assertIn(DISPATCH_UNACCOUNTED, str(caught.exception))

    def test_abandon_path_survives_raise(self) -> None:
        """Row 14: the ADAPTER raises; the caller's existing swallow is unchanged.

        ``executor``'s abandon path deliberately swallows to ``[]``.  That is the caller's
        decision and it must stay one -- so the adapter is asserted to raise, and the
        caller's disposition is reproduced here to show the pair still composes.
        """
        self.journal.append(event())
        self.journal.path.write_text("{corrupt\n")
        try:
            rows = self.adapter.open_dispatches()
        except Exception:                      # noqa: BLE001 - the caller's own swallow
            rows = []
        self.assertEqual(rows, [], "the abandon path must still reach a decision")


# =====================================================================================
class RecoverHandleTests(_Base):
    """Rows 9, 15, 18."""

    def test_recover_handle_three_outcomes(self) -> None:
        # not_listed: the authority ANSWERED and holds no handle.
        self.assertEqual(self.adapter.recover_handle("intent-none")["handle_recovery"],
                         "not_listed")
        # listing_verified: the journal NAMES the candidate and two LIVE authorities prove
        # it -- the tty-scoped process table holds the recorded pid in the recorded group,
        # and the child's own spawn record names the same pid and argv digest
        # (consolidated review finding 8: the journal alone never verifies itself).
        self.journal.append(event())
        write_spawn_record(self.base)
        verified = self.adapter.recover_handle("intent-1")
        self.assertEqual(verified["handle_recovery"], "listing_verified")
        self.assertEqual(verified["handle"], "pty-1")
        # listing_candidate: a match with NO verifier.  No handle is returned.
        candidate = journal_mod.recover_handle(self.journal, "intent-1",
                                               verified_digest="")
        self.assertEqual(candidate["handle_recovery"], "listing_candidate")
        self.assertIsNone(candidate["handle"],
                          "a candidate must not be actionable; it exists so an abandon "
                          "report can NAME the resource")
        self.assertEqual(candidate["candidate"], "pty-1")

    def test_recover_handle_detects_a_pty_lost_after_a_supervisor_crash(self) -> None:
        """Finding 8.  The same journal, the process GONE: never `listing_verified`.

        Before the fix the "verified" digest and the candidate digest were both read from
        the journal, so this case -- the pty and its process lost after a supervisor crash
        -- was reported `listing_verified` with an actionable handle.
        """
        self.journal.append(event())
        write_spawn_record(self.base)
        gone = StandaloneAdapter(
            None, runtime_state=self.ledger, settlement_journal=self.journal,
            artifact_base=self.base, run_id="run_1", table_reader=live_table())
        lost = gone.recover_handle("intent-1")
        self.assertEqual(lost["handle_recovery"], "not_listed")
        self.assertIsNone(lost["handle"])
        # An UNREADABLE table is unknown, never verified: a candidate, not a handle.
        blind = StandaloneAdapter(
            None, runtime_state=self.ledger, settlement_journal=self.journal,
            artifact_base=self.base, run_id="run_1",
            table_reader=live_table(readable=False))
        unknown = blind.recover_handle("intent-1")
        self.assertEqual(unknown["handle_recovery"], "listing_candidate")
        self.assertIsNone(unknown["handle"])
        # A pid recycled onto the tty in ANOTHER process group is not our resource.
        recycled = StandaloneAdapter(
            None, runtime_state=self.ledger, settlement_journal=self.journal,
            artifact_base=self.base, run_id="run_1",
            table_reader=live_table({**AGENT_ROW, "pgid": 99}))
        self.assertEqual(recycled.recover_handle("intent-1")["handle_recovery"],
                         "not_listed")
        # A live process whose child-written spawn record CONTRADICTS the journal's digest
        # is present but not proven ours.
        write_spawn_record(self.base, argv_digest="digest-OTHER")
        contradicted = self.adapter.recover_handle("intent-1")
        self.assertEqual(contradicted["handle_recovery"], "unverified")
        self.assertIsNone(contradicted["handle"])

    def test_recover_handle_raises_when_unreadable(self) -> None:
        self.journal.append(event())
        self.journal.path.write_text("{corrupt\n")
        with self.assertRaises(journal_mod.JournalUnreadable):
            self.adapter.recover_handle("intent-1")

    def test_residual_row_best_effort(self) -> None:
        """Rows 15/16: best-effort AT THE CALLER, and the adapter still raises."""
        self.journal.append(event())
        self.journal.path.write_text("{corrupt\n")
        handle, accounted = "", {}
        try:
            handle = self.adapter.recover_handle("intent-1").get("handle") or ""
        except Exception:                      # noqa: BLE001 - the caller's own swallow
            handle = ""
        try:
            accounted = dict(self.adapter.account_dispatch("intent-1"))
        except Exception:                      # noqa: BLE001
            accounted = {}
        self.assertEqual(handle, "")
        self.assertEqual(accounted, {})

    def test_disposition_record_candidate_address(self) -> None:
        """Row 18: called OUTSIDE the graph; the caller turns any exception into ``""``."""
        self.journal.append(event())
        write_spawn_record(self.base)
        address = self.adapter.recover_handle("intent-1").get("handle") or ""
        self.assertEqual(address, "pty-1")
        self.journal.path.write_text("{corrupt\n")
        try:
            address = self.adapter.recover_handle("intent-1").get("handle") or ""
        except Exception:                      # noqa: BLE001 - pause_runtime's own
            address = ""
        self.assertEqual(address, "")


# =====================================================================================
class AccountDispatchTests(_Base):
    """Rows 10, 16."""

    def test_account_dispatch_is_read_only(self) -> None:
        """A SYSCALL SPY proves it issues no command and writes nothing.

        Read-only is what makes it safe to repeat after a crash, and "safe to repeat" is
        the property the pause path depends on.
        """
        self.journal.append(event())
        before = self.journal.path.read_bytes()
        commands: list = []

        class _SpyingJournal:
            def __init__(self, inner):
                self._inner = inner

            def axes_for(self, intent_id):
                return self._inner.axes_for(intent_id)

            def append(self, record):
                commands.append(("append", record))
                raise AssertionError("account_dispatch wrote to the journal")

            def rows_for(self, intent_id):
                return self._inner.rows_for(intent_id)

        spy = StandaloneAdapter(None, runtime_state=self.ledger,
                                settlement_journal=_SpyingJournal(self.journal),
                                artifact_base=self.base, run_id="run_1")
        first = dict(spy.account_dispatch("intent-1"))
        second = dict(spy.account_dispatch("intent-1"))
        self.assertEqual(commands, [])
        self.assertEqual(first, second, "repeating it produced a different answer")
        self.assertEqual(self.journal.path.read_bytes(), before)

    def test_account_dispatch_always_reports_all_four_axes(self) -> None:
        self.journal.append(event())
        row = self.adapter.account_dispatch("intent-1")
        for axis in ("settlement", "worker_resource", "process_liveness",
                     "cleanup_authority"):
            self.assertIn(axis, row, f"axis {axis} was omitted; an omitted axis reads as "
                                     "'irrelevant' at every call site")

    def test_an_unobserved_dispatch_defaults_to_maximal_ignorance(self) -> None:
        """A dispatch nobody has observed is not a dispatch that is fine.

        The default is now stated in the PAUSE AUTHORITY's own vocabulary (external review
        #5).  It used to read `settlement="unknown"` / `process_liveness="unverifiable"`,
        two members `pause_policy` does not accept -- so this row could not be validated
        AND `executor._settlement_row`'s `settlement == "not_settled"` recovery branch was
        never entered for it.  `not_settled` and `disputed` say exactly the same thing about
        what is known, in words the authority can act on: neither promotes anything, and
        `already exited`/`live`/`settled` remain unreachable from ignorance.
        """
        row = self.adapter.account_dispatch("intent-never")
        self.assertEqual(row["settlement"], "not_settled")
        self.assertEqual(row["process_liveness"], "disputed")
        self.assertEqual(row["cleanup_authority"], "unknown")
        self.assertNotIn(row["process_liveness"], ("live", "already exited"),
                         "ignorance may never be reported as a known liveness")


# =====================================================================================
class RecoverDispatchTests(_Base):
    """Rows 11, 17."""

    def test_recover_dispatch_never_reports_settled(self) -> None:
        result = self.adapter.recover_dispatch("intent-1", reason="pause")
        self.assertEqual(result["settlement"], "recovered")
        self.assertNotEqual(
            result["settlement"], "settled",
            "a recovered dispatch has been accounted for; a settled one has produced a "
            "verdict, and reporting the first as the second discharges unfinished work")
        self.assertIn("outcome_unknown", result["recovery"])

    def test_abandon_residual_never_claimed_discharged(self) -> None:
        """Row 17: on failure the row stays ``not_settled`` and is reported RESIDUAL."""
        unwired = StandaloneAdapter(None, runtime_state=self.ledger,
                                    artifact_base=self.base, run_id="run_1")
        with self.assertRaises(RuntimeError):
            unwired.recover_dispatch("intent-1", reason="abandon:c1")
        # And the row it could not touch is still open, not discharged.
        self.journal.append(event())
        self.assertIn("intent-1", self.adapter.open_dispatches())

    def test_the_recovered_row_is_recorded_as_lost_not_completed(self) -> None:
        self.adapter.recover_dispatch("intent-1", reason="pause")
        rows = self.journal.rows_for("intent-1")
        self.assertTrue(rows)
        self.assertEqual(rows[-1]["state"], "LOST")
        self.assertEqual(rows[-1]["lost_reason"], "settlement_unconfirmed")


# =====================================================================================
class ReleaseTerminalTests(_Base):
    """Row 12: the ONLY mutating verb."""

    def test_release_only_what_was_requested(self) -> None:
        """With no live session in THIS process, the answer is ``retained`` -- and says so.

        Not a silent success.  A process that does not own the resource cannot release it,
        and reporting a release it did not perform is exactly how a live process gets
        recorded as cleaned up.
        """
        self.journal.append(event())
        result = self.adapter.release_terminal("intent-1", authority="authorized")
        self.assertEqual(result["recovery"], "retained:none")
        self.assertEqual(result["refusal"], "no_live_session_in_this_process")

    def test_release_is_refused_without_the_journal(self) -> None:
        unwired = StandaloneAdapter(None, runtime_state=self.ledger,
                                    artifact_base=self.base, run_id="run_1")
        with self.assertRaises(RuntimeError):
            unwired.release_terminal("intent-1", authority="authorized")

    def test_release_is_the_only_mutating_verb_of_the_five(self) -> None:
        """STATIC: of the five methods, only two append, and only one of them mutates a resource.

        ``recover_dispatch`` appends a row -- recording that a dispatch was accounted for is
        not a mutation OF THE RESOURCE -- and ``release_terminal`` appends only after the
        release actually happened.  The other three write nothing.
        """
        import ast
        import inspect
        for name in ("open_dispatches", "recover_handle", "account_dispatch"):
            source = inspect.getsource(getattr(StandaloneAdapter, name))
            tree = ast.parse(source.lstrip().replace("\n    ", "\n"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                    self.assertNotIn(
                        node.func.attr, ("append", "release", "kill", "killpg", "write"),
                        f"{name} calls .{node.func.attr}(; it must be read-only")


# =====================================================================================
class SettlementTests(_Base):
    """Rows 2, 5: ``None`` only to prove absence; unreadable RAISES."""

    def test_settlement_none_is_proven_absent(self) -> None:
        self.assertIsNone(self.adapter.settlement("intent-never"))

    def test_unreadable_authority_raises(self) -> None:
        self.journal.append(event())
        self.journal.path.write_text("{corrupt\n")
        with self.assertRaises(journal_mod.JournalUnreadable):
            self.adapter.settlement("intent-1")

    def test_a_stranger_process_reads_the_same_settlement(self) -> None:
        """Row 5: the recovery ladder's rung 1, from a process that created nothing."""
        stranger = StandaloneAdapter(
            None, runtime_state=self.ledger,
            settlement_journal=journal_mod.ExecutionJournal(self.base, "run_1"),
            artifact_base=self.base, run_id="run_1")
        self.assertIsNone(stranger.settlement("intent-1"))


if __name__ == "__main__":
    unittest.main()


# =====================================================================================
class BlockingStartContractTests(unittest.TestCase):
    """``start`` must RUN THE WHOLE DISPATCH and settle before returning.

    This is the engine's contract, not a preference, and getting it wrong is silent:
    ``executor._settle_now`` calls ``adapter.start(...)`` and then immediately requires
    ``adapter.settlement(intent_id)`` to answer, so an adapter that returned once the
    process was merely spawned made every real run raise the generic
    ``OUT_OF_ORDER_EVENT:settlement missing`` -- a message that names nothing.  The executor
    says so in its own comment (*"``start`` is the long blocking call -- minutes, not
    milliseconds"*), ``LeaseKeeper`` exists for it, and both other adapters satisfy it.

    Found by driving the real CLI, not by reading the code.
    """

    def test_the_engine_requires_a_settlement_immediately_after_start(self) -> None:
        """The contract, read out of the ENGINE rather than transcribed."""
        import inspect

        from scripts.deterministic_workflow import executor
        source = inspect.getsource(executor._settle_now)
        self.assertIn("adapter.start(intent, lease_token=lease_token)", source)
        after = source.split("adapter.start(", 1)[1]
        self.assertIn("adapter.settlement(", after,
                      "the executor no longer reads a settlement after start")
        self.assertIn('raise StateError("OUT_OF_ORDER_EVENT:settlement missing")', after,
                      "the executor no longer refuses a missing settlement, so this "
                      "contract may have changed and this test must be re-derived")
        # The phrase wraps across a comment line break in the source, so the comparison is
        # whitespace-insensitive -- matching the raw text would fail on a reflow rather than
        # on the contract changing, which is the opposite of what this asserts.
        import re as _re
        # Comment markers are stripped as well as whitespace: the sentence spans several
        # comment lines, so each continuation carries a `#` that a naive collapse leaves in
        # the middle of the phrase.
        flattened = _re.sub(r"\s+", " ", _re.sub(r"#", "", source))
        self.assertIn(
            "``start`` is the long blocking call", flattened,
            "the executor's own statement of the contract is gone; if it really changed, "
            "this test must be re-derived rather than relaxed")

    def test_all_three_adapters_settle_inside_start(self) -> None:
        """The property, asserted across every adapter rather than only the new one.

        If the standalone adapter were the odd one out, that would be the divergence; if all
        three do it, it is the contract.
        """
        import ast
        import inspect
        import textwrap

        from scripts.deterministic_workflow.fake_adapter import FakeAdapter
        from scripts.deterministic_workflow.orca_adapter import OrcaAdapter
        from scripts.deterministic_workflow.standalone_adapter import StandaloneAdapter
        from scripts.deterministic_workflow.standalone_runtime import StandaloneSession
        # The standalone start path is `run_dispatch` -> `_complete` -> `_settle`: the two
        # exits of `run_dispatch` (the two delivery modes) both hand over to `_complete`,
        # which awaits completion and settles a SUCCESS and a TYPED FAILURE alike.  The
        # sources are read together so the property is "this path settles", not "this one
        # function contains the call".
        for label, functions in (("orca", (OrcaAdapter.start,)),
                                 ("fake", (FakeAdapter.start,)),
                                 ("standalone", (StandaloneSession.run_dispatch,
                                                 StandaloneSession._complete))):
            with self.subTest(adapter=label):
                settles = []
                for function in functions:
                    tree = ast.parse(textwrap.dedent(inspect.getsource(function)))
                    settles += [n for n in ast.walk(tree)
                                if isinstance(n, ast.Call)
                                and isinstance(n.func, ast.Attribute)
                                and n.func.attr in ("settle", "_settle")]
                self.assertTrue(
                    settles,
                    f"{label}'s start path never settles, so the engine's immediate "
                    "settlement read would find nothing")
        # ... and `run_dispatch` really does reach `_complete` on BOTH of its exits, so the
        # split above cannot hide a mode that returns without settling.
        dispatch_source = inspect.getsource(StandaloneSession.run_dispatch)
        self.assertEqual(
            dispatch_source.count("self._complete("), 2,
            "run_dispatch has an exit that does not reach the settling path")
        # And the port method really is the blocking one, not the spawn-only one.
        self.assertIn("run_dispatch", inspect.getsource(StandaloneAdapter.start))
        self.assertIn("session.start", inspect.getsource(StandaloneAdapter.spawn_only))

    def test_completion_requires_both_gates(self) -> None:
        """A structured result WITHOUT a proven exit is not a completion, and vice versa.

        Either half alone is exactly the confusion this ticket exists to prevent: a report
        from a process that may still be running, or a process that ended without saying
        what it did.
        """
        import inspect

        from scripts.deterministic_workflow.standalone_runtime import StandaloneSession
        source = inspect.getsource(StandaloneSession.await_completion)
        self.assertIn('evidence["settlement_record"] is not None and evidence["exit_proven"]',
                      source, "await_completion does not require BOTH gates")
        self.assertIn('"state": "LOST"', source,
                      "await_completion has no LOST branch, so half-evidence would pass")
        self.assertNotIn('"state": "COMPLETED", "evidence": evidence, "lost_reason": ""}\n'
                         '        if evidence["settlement_record"] is None', source)

    def test_a_failed_stage_raises_a_NAMED_failure(self) -> None:
        """Every stage that cannot proceed says which stage and why."""
        from scripts.deterministic_workflow.standalone_runtime import StandaloneDispatchFailed
        error = StandaloneDispatchFailed("readiness_timed_out", "deadline_expired", {})
        self.assertIn("STANDALONE_DISPATCH_FAILED:readiness_timed_out", str(error))
        self.assertIn("deadline_expired", str(error))
        self.assertEqual(error.stage, "readiness_timed_out")

    def test_the_settlement_result_uses_the_SHARED_policy_parser(self) -> None:
        """AC-37-20: the result vocabulary is workflow policy, not a per-runtime choice.

        A standalone-specific parser would be exactly the divergence the criterion forbids,
        so the standalone path feeds the agent's transcript to the same
        ``decision_contract.parse_agent_settlement`` the Orca and fake paths use.
        """
        import inspect

        from scripts.deterministic_workflow import standalone_runtime
        source = inspect.getsource(standalone_runtime._default_result_parser)
        self.assertIn("parse_agent_settlement", source)
        self.assertIn("decision_contract", source)
        orca_source = inspect.getsource(
            __import__("scripts.deterministic_workflow.orca_adapter",
                       fromlist=["_default_result_parser"])._default_result_parser)
        self.assertIn("parse_agent_settlement", orca_source,
                      "the Orca adapter no longer uses this parser, so 'shared' is false")


# =====================================================================================
class TranscriptNormalisationTests(unittest.TestCase):
    """The pty's CR-LF translation is a TRANSPORT artefact and must not reach a parser.

    MEASURED, and it broke a real gate: ``decision_gate.GATE_RECORD_BLOCK`` is anchored as
    ``^```decision-gate\n``, which never matches ``` ```decision-gate\r\n ```, while
    ``FIELD_LINE`` ends in ``\s*$`` and tolerates the ``\r``.  So a standalone agent's
    ``DECISION_GATE_STATE`` line was read and its fenced record was not: the run repaired
    twice and terminated ``DECISION_GATE_REPAIR_EXHAUSTED`` on output that was well formed.
    """

    def setUp(self) -> None:
        from scripts.deterministic_workflow.standalone_capture import BoundedCapture
        self.store = BoundedCapture(Path(tempfile.mkdtemp()) / "capture.log")

    def test_the_transcript_undoes_the_translation_and_text_does_not(self) -> None:
        self.store.append(RAW_GATE_BODY, at="t")
        self.assertIn(CR.decode(), self.store.text(),
                      "text() must stay VERBATIM; the capture file is evidence")
        self.assertNotIn(CR.decode(), self.store.transcript())
        self.assertIn("\n```decision-gate\n",
                      self.store.transcript(),
                      "the fence must sit on its own LF-terminated line, which is what "
                      "the engine gate pattern anchors to")

    def test_the_real_gate_pattern_matches_the_transcript_and_not_the_raw_text(self) -> None:
        """Asserted against the ENGINE's own pattern, not a copy of it."""
        from scripts import decision_gate
        self.store.append(RAW_GATE_FENCE, at="t")
        self.assertIsNone(
            decision_gate.GATE_RECORD_BLOCK.search(self.store.text()),
            "the raw capture matched, so this defect no longer exists and this test must "
            "be re-derived rather than deleted")
        self.assertIsNotNone(
            decision_gate.GATE_RECORD_BLOCK.search(self.store.transcript()),
            "the normalised transcript still does not match the engine's gate pattern")

    def test_the_capture_file_on_disk_is_never_rewritten(self) -> None:
        raw = RAW_TWO_LINES
        self.store.append(raw, at="t")
        self.assertEqual(self.store.path.read_bytes(), raw,
                         "the capture file was rewritten; it is the agent's own transcript")

    def test_every_parsing_read_uses_the_transcript(self) -> None:
        """STATIC: no parsing path reads the verbatim text.

        The two views exist so the distinction is explicit, which only helps if the call
        sites actually observe it.
        """
        import ast

        from scripts.deterministic_workflow import standalone_runtime
        source = Path(standalone_runtime.__file__).read_text()
        tree = ast.parse(source)
        verbatim = [n.lineno for n in ast.walk(tree)
                    if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                    and n.func.attr == "text"
                    and isinstance(n.func.value, ast.Attribute)
                    and n.func.value.attr == "capture"]
        self.assertEqual(
            verbatim, [],
            f"standalone_runtime reads capture.text() at lines {verbatim}; a parsing read "
            "must use capture.transcript() so the pty's CR-LF translation cannot break a "
            "line-anchored pattern")


# =====================================================================================
class IngestRateMeasurementTests(unittest.TestCase):
    """The rate is measured on a THROWAWAY pty, bounded, and never on the agent's.

    Two things went wrong when it used the agent's, and both were serious: four kilobytes of
    padding written into a live agent's stdin IS INPUT, and the write BLOCKS FOREVER when the
    child is not currently reading -- an unbounded hang in the prompt-delivery path, which is
    worse than any fail-closed refusal because nothing is reported at all.
    """

    def test_it_takes_no_file_descriptor_at_all(self) -> None:
        """The signature is the enforcement: there is no agent fd to pass by mistake."""
        import inspect

        from scripts.deterministic_workflow import standalone_preflight as preflight
        parameters = inspect.signature(preflight.measure_ingest_rate).parameters
        self.assertNotIn(
            "master_fd", parameters,
            f"measure_ingest_rate still accepts a descriptor: {tuple(parameters)}. It must "
            "create and destroy its own pty, or a caller can hand it the agent's")
        for name in parameters.values():
            self.assertEqual(name.kind, inspect.Parameter.KEYWORD_ONLY)

    def test_it_is_bounded_and_returns_a_usable_rate(self) -> None:
        import time as _time

        from scripts.deterministic_workflow import standalone_preflight as preflight
        started = _time.time()
        rate = preflight.measure_ingest_rate(budget_ms=200)
        elapsed = _time.time() - started
        self.assertGreater(rate, 0.0)
        self.assertLess(elapsed, 5.0,
                        f"the measurement took {elapsed:.1f}s; it must be bounded")

    def test_a_zero_measurement_falls_back_to_the_conservative_floor(self) -> None:
        from scripts.deterministic_workflow import standalone_preflight as preflight
        self.assertEqual(
            preflight.measure_ingest_rate(probe_bytes=0, floor_bytes_per_ms=7.0), 7.0,
            "a measurement that read nothing must answer the floor, never zero -- a zero "
            "rate makes the settle gate infinite rather than uncapped")

    def test_the_session_never_measures_on_its_own_pty(self) -> None:
        import ast
        import inspect
        import textwrap

        from scripts.deterministic_workflow.standalone_runtime import StandaloneSession
        tree = ast.parse(textwrap.dedent(inspect.getsource(StandaloneSession.send)))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                    and node.func.attr == "measure_ingest_rate":
                self.assertEqual(
                    node.args, [],
                    "send() passes an argument to measure_ingest_rate; it must take none, "
                    "so the agent's pty cannot be measured on")


# =====================================================================================
class DerivedTaskAndDispatchIdentityTests(unittest.TestCase):
    """AC-37-01's six axes stay REAL when the intent carries no Orca task or dispatch.

    The canonical ``ActionIntent`` has no ``task_id`` and no ``dispatch_id`` -- those are
    Orca Task/Dispatch identities that ``OrcaAdapter`` gets from ``create_task`` and
    ``run_existing_task``.  A standalone runtime has no external system to ask, and the wrong
    answer is a blank: ``OwnershipRecord`` requires every field, so a blank would either
    refuse every run or record an identity that binds nothing.
    """

    def test_the_canonical_intent_really_carries_neither(self) -> None:
        """The premise, checked, so this class cannot outlive its reason."""
        from scripts.deterministic_workflow.contracts import ActionIntent
        keys = set(ActionIntent.__annotations__)
        self.assertNotIn("task_id", keys)
        self.assertNotIn("dispatch_id", keys)

    def _session(self, **overrides):
        from scripts.deterministic_workflow import standalone_journal as sj
        from scripts.deterministic_workflow.standalone_runtime import StandaloneSession
        from scripts.deterministic_workflow.standalone_profile import (
            CompletionSelector, DeliveryProofSelector, ReadinessSelector,
            StandaloneProfile)
        base = Path(tempfile.mkdtemp())
        intent = {"intent_id": "intent-x", "command_id": "c", "payload_digest": "d",
                  "run_id": "run_i", "phase": "IMPLEMENTATION", "role": "WORKER",
                  "round_kind": "PHASE_GATE"}
        intent.update(overrides)
        profile = StandaloneProfile(
            driver="claude", binary="claude", supported_range=((0, 0, 0), (9, 9, 9)),
            readiness_records=(ReadinessSelector(channel="structured",
                                                 record_type="system",
                                                 session_field="session_id"),),
            delivery_mode="post_ready_delivery",
            identity_binding="minted_echo", identity_flag="--session-id",
            delivery_proofs=(DeliveryProofSelector(channel="structured",
                                                   record_type="assistant"),),
            completion_records=(CompletionSelector(channel="structured",
                                                   record_type="result",
                                                   error_field="is_error"),))
        return StandaloneSession(intent=intent, profile=profile, artifact_base=base,
                                 run_id="run_i",
                                 journal=sj.ExecutionJournal(base, "run_i"))

    def test_they_are_derived_from_the_stable_intent_and_the_incarnation(self) -> None:
        session = self._session()
        self.assertEqual(session.task_id, "task:intent-x",
                         "the task identity must be the stable unit of work")
        self.assertEqual(session.dispatch_id,
                         f"dispatch:intent-x:{session.incarnation}",
                         "the dispatch identity must distinguish one ATTEMPT from another")
        self.assertTrue(session.task_id and session.dispatch_id,
                        "neither may be blank; OwnershipRecord requires every field")

    def test_an_explicit_identity_on_the_intent_always_wins(self) -> None:
        session = self._session(task_id="task-real", dispatch_id="dispatch-real")
        self.assertEqual(session.task_id, "task-real")
        self.assertEqual(session.dispatch_id, "dispatch-real")

    def test_two_incarnations_of_one_task_are_two_dispatches(self) -> None:
        """A retry after a failed spawn is a new DISPATCH of the same TASK.

        That is what S-3's stale-dispatch refusal compares, so deriving the dispatch id from
        the incarnation means the journal's ``dispatch_id`` and its identity fence can never
        disagree about which attempt a record belongs to.
        """
        first, second = self._session(), self._session()
        self.assertEqual(first.task_id, second.task_id)
        self.assertNotEqual(first.dispatch_id, second.dispatch_id)
        self.assertIn(first.incarnation, first.dispatch_id)
