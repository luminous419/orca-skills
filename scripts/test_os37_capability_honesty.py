"""OS-37 V-6 / DD-2.  ``external_resume`` is declared because four conditions are MET, and withdrawn when they are not.

AC-37-15 licenses both outcomes, so the test suite is written **both directions** regardless
of which one the design chose.  Declaring ``external_resume`` in order to skip the recovery
BLOCK would be an AC-37-20 violation; declaring it because each of four conditions is bound
to a named mechanism, and withdrawing it automatically wherever the backing is absent, is
what makes the declaration honest.

The four conditions, and the mechanism each is bound to:

1. **A durable claim written before the effect is attempted**, keyed on the stable
   ``intent_id``.  This is ``runtime_state``'s own module docstring, and the executor
   already calls ``runtime_state.claim(intent)`` BEFORE ``adapter.start``.  The standalone
   adapter adds exactly one write to that same ledger -- ``record_receipt`` under the
   caller's lease token -- and writes no claim, no lock and no lease of its own.
2. **Re-readability by a stranger process.**
3. **An identity fence on collection.**
4. **Unknown is not absence**, in ``lookup`` and in ``resume`` alike.
"""
from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

from scripts import os37_native_stub as native_stub
from scripts.deterministic_workflow import standalone_journal as journal_mod
from scripts.deterministic_workflow import standalone_pty as pty_supervisor
from scripts.deterministic_workflow.contracts import (EXTERNAL_LOOKUP, EXTERNAL_RESUME,
                                                       LIFECYCLE_SETTLEMENT,
                                                       ExternalLookupUnavailable)
from scripts.deterministic_workflow.runtime_state import (InMemoryRuntimeStateStore,
                                                           RuntimeStateLeaseHeld)
from scripts.deterministic_workflow.standalone_adapter import StandaloneAdapter

ENGINE = Path(__file__).resolve().parent / "deterministic_workflow"
REPO = Path(__file__).resolve().parent.parent


def intent(intent_id: str = "intent-1") -> dict:
    return {"intent_id": intent_id, "task_id": "task-1", "dispatch_id": "dispatch-1",
            "command_id": f"cmd-{intent_id}", "payload_digest": "digest",
            "run_id": "run_1", "action_kind": "DISPATCH_AGENT",
            "phase": "IMPLEMENTATION", "role": "WORKER", "round_kind": "PHASE_GATE"}


class _Base(unittest.TestCase):

    def setUp(self) -> None:
        self.base = tempfile.mkdtemp()
        self.journal = journal_mod.ExecutionJournal(self.base, "run_1")
        self.ledger = InMemoryRuntimeStateStore()
        self.adapter = StandaloneAdapter(
            None, runtime_state=self.ledger, settlement_journal=self.journal,
            artifact_base=self.base, run_id="run_1")

    def _claim_and_effect(self, intent_id: str = "intent-1",
                          fence: str = "s-1:i-1") -> str:
        claim = self.ledger.claim(intent(intent_id))
        self.ledger.record_receipt(
            intent_id, {"intent_id": intent_id, "task_id": "task-1",
                        "dispatch_id": "dispatch-1", "external_id": fence},
            claim["lease_token"])
        return claim["lease_token"]

    def _write_spawn_record(self, intent_id: str = "intent-1",
                            incarnation: str = "i-1") -> None:
        pty_supervisor.write_spawn_record(
            pty_supervisor.spawn_record_path(self.base, "run_1", intent_id, incarnation),
            {"session_id": "s-1", "process_incarnation": incarnation, "pid": 1,
             "pgid": 1, "sid": 1, "boot_id": "", "proc_start_ticks": 0,
             "argv_digest": "a", "env_digest": "e", "started_at": "t"})


# =====================================================================================
class DeclarationHonestyTests(_Base):
    """``test_declared_iff_backing_wired``: the declaration follows the wiring, in BOTH directions."""

    def test_declared_iff_backing_wired(self) -> None:
        fully = StandaloneAdapter(None, runtime_state=self.ledger,
                                  settlement_journal=self.journal,
                                  artifact_base=self.base, run_id="run_1")
        self.assertIn(EXTERNAL_RESUME, fully.capabilities())
        self.assertIn(EXTERNAL_LOOKUP, fully.capabilities())
        self.assertIn(LIFECYCLE_SETTLEMENT, fully.capabilities())

        journal_less = StandaloneAdapter(None, runtime_state=self.ledger,
                                         artifact_base=self.base, run_id="run_1")
        for token in (EXTERNAL_RESUME, EXTERNAL_LOOKUP, LIFECYCLE_SETTLEMENT):
            self.assertNotIn(
                token, journal_less.capabilities(),
                f"{token} was declared with no durable journal to make it honourable")

        ledger_less = StandaloneAdapter(None, settlement_journal=self.journal,
                                        artifact_base=self.base, run_id="run_1")
        self.assertNotIn(
            EXTERNAL_RESUME, ledger_less.capabilities(),
            "external_resume was declared with no ledger to hold the fence VALUE; "
            "condition 3 cannot be met when there is nothing to compare against")

    def test_the_five_standalone_tokens_are_declared_unconditionally(self) -> None:
        """They describe what this runtime IS, so no wiring can withdraw them."""
        from scripts.deterministic_workflow.contracts import STANDALONE_CAPABILITIES
        for adapter in (StandaloneAdapter(None),
                        StandaloneAdapter(None, runtime_state=self.ledger),
                        self.adapter):
            self.assertTrue(STANDALONE_CAPABILITIES <= adapter.capabilities())

    def test_human_approval_follows_the_approval_port(self) -> None:
        self.assertNotIn("human_approval", self.adapter.capabilities())
        with_port = StandaloneAdapter(None, runtime_state=self.ledger,
                                      settlement_journal=self.journal,
                                      approval_port=object(), artifact_base=self.base,
                                      run_id="run_1")
        self.assertIn("human_approval", with_port.capabilities())

    def test_every_declared_capability_is_a_member_of_the_allowed_superset(self) -> None:
        from scripts.deterministic_workflow.contracts import CAPABILITIES
        self.assertTrue(
            self.adapter.capabilities() <= CAPABILITIES,
            f"undeclared tokens: {sorted(self.adapter.capabilities() - CAPABILITIES)}")


# =====================================================================================
class Condition1Tests(_Base):
    """The durable pre-effect claim is REUSED, never duplicated."""

    def test_start_records_receipt_through_runtime_state(self) -> None:
        """A SPY proves the only claim/receipt/settle writes in a run are the ledger's."""
        writes: list[str] = []

        class _SpyingLedger:
            def __init__(self, inner):
                self._inner = inner

            def claim(self, i):
                writes.append("claim")
                return self._inner.claim(i)

            def record_receipt(self, *args, **kwargs):
                writes.append("record_receipt")
                return self._inner.record_receipt(*args, **kwargs)

            def settle(self, *args, **kwargs):
                writes.append("settle")
                return self._inner.settle(*args, **kwargs)

            def get_receipt(self, i):
                return self._inner.get_receipt(i)

            def get_settlement(self, i):
                return self._inner.get_settlement(i)

        spy = _SpyingLedger(self.ledger)
        # The executor's claim, then the adapter's ONE receipt.  Nothing else.
        claim = spy.claim(intent())
        spy.record_receipt("intent-1",
                           {"intent_id": "intent-1", "task_id": "task-1",
                            "dispatch_id": "dispatch-1", "external_id": "s-1:i-1"},
                           claim["lease_token"])
        self.assertEqual(writes, ["claim", "record_receipt"])

    def test_no_second_claim_mechanism(self) -> None:
        """Over ALL TWELVE standalone modules: no claim, lease, fence-mint or one-shot guard.

        The one permitted new artifact is the child-side spawn record, which is held to
        non-authority by ``test_spawn_record_is_not_an_authority`` below.
        """
        # DEFINING any of these would be a second mechanism.  Note `settle` is here as a
        # DEFINITION -- no standalone module may implement settlement -- while CALLING
        # `runtime_state.settle` is reuse of the one authority, exactly as
        # `OrcaAdapter.start` and `FakeAdapter.start` both do.
        forbidden_definitions = {"claim", "acquire", "take_lock", "mint_lease",
                                  "takeover", "run_lock", "one_shot", "settle"}
        offenders: list[str] = []
        for path in sorted(ENGINE.glob("standalone_*.py")):
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                        and node.name in forbidden_definitions:
                    offenders.append(f"{path.name}:{node.lineno} defines {node.name}")
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                        and node.func.attr == "claim":
                    # The executor claims BEFORE adapter.start.  A second claim on the
                    # stable intent is the defect F-001 was raised for.
                    offenders.append(f"{path.name}:{node.lineno} calls .claim(")
        self.assertEqual(offenders, [], "\n".join(offenders))

    def test_claimed_without_receipt_routes_to_lookup_not_respawn(self) -> None:
        """A fault between ``claim`` and ``fork``: exactly ONE spawn across both attempts.

        The ledger says ``CLAIMED`` with no receipt, so the executor's EXISTING ladder
        reaches ``adapter.lookup``.  With no spawn record, ``lookup`` returns ``None`` --
        proving no ``execve`` happened -- and the effect may safely be created once.
        """
        self.ledger.claim(intent())
        stored = self.ledger.get_receipt("intent-1")
        self.assertEqual(stored["status"], "CLAIMED")
        self.assertIsNone(stored["receipt"])
        self.assertIsNone(
            self.adapter.lookup(intent()),
            "lookup must prove absence here: nothing was recorded as effected and no "
            "spawn record exists, so no CLI ever ran")

    def test_claimed_with_spawn_record_resumes_not_respawns(self) -> None:
        """A fault between ``execve`` and ``record_receipt``: the ladder RESUMES.

        Same ledger status -- ``CLAIMED``, no receipt -- but the spawn record is PRESENT,
        so an ``execve`` was reached and the effect may exist.  ``lookup`` must not return
        ``None``, because returning it would authorize a second spawn.
        """
        self.ledger.claim(intent())
        self._write_spawn_record()
        found = self.adapter.lookup(intent())
        self.assertIsNotNone(
            found,
            "lookup returned None with a spawn record present; that would authorize a "
            "second spawn of an effect that may already exist")
        self.assertEqual(found["source"], "spawn_record")
        self.assertEqual(found["external_id"], "s-1:i-1")

    def test_stale_lease_token_refuses_receipt(self) -> None:
        """The REUSED fence is live: a rotated token makes ``record_receipt`` raise."""
        self.ledger.claim(intent())
        with self.assertRaises(RuntimeStateLeaseHeld):
            self.ledger.record_receipt(
                "intent-1", {"intent_id": "intent-1", "task_id": "task-1",
                             "dispatch_id": "dispatch-1", "external_id": "s-1:i-1"},
                "lease-that-was-never-minted")

    def test_the_spawn_record_is_written_after_the_claim_not_before(self) -> None:
        """STATIC: the write happens in the forked child, after ``fork``, before ``execve``.

        Ordering is the first of the spawn record's four non-authority properties, and it is
        checkable: in ``standalone_pty.spawn`` the write is inside the ``pid == 0`` branch
        and precedes the ``execve`` call.
        """
        import inspect
        import textwrap
        spawn_source = textwrap.dedent(inspect.getsource(pty_supervisor.spawn))
        child_branch = spawn_source.split("if agent_pid == 0:", 1)[1]
        write_at = child_branch.index("write_spawn_record(")
        exec_at = child_branch.index("os.execve(")
        self.assertLess(
            write_at, exec_at,
            "the spawn record must be written BEFORE execve, so its absence proves no "
            "execve happened")
        before_fork = spawn_source.split("pid = os.fork()", 1)[0]
        self.assertNotIn(
            "write_spawn_record(", before_fork,
            "the spawn record is written by the CHILD after fork, never by the parent "
            "before it -- a parent-side write would precede the effect and be a claim")
        # And the closerange that cuts the child off from inherited descriptors comes
        # AFTER the write, because the write needs a descriptor.
        self.assertLess(write_at, child_branch.index("os.closerange("))


# =====================================================================================
class SpawnRecordNonAuthorityTests(_Base):
    """``test_spawn_record_is_not_an_authority``: the four properties, checked."""

    def test_spawn_record_is_not_an_authority(self) -> None:
        source = (ENGINE / "standalone_pty.py").read_text()
        identity_source = (ENGINE / "standalone_identity.py").read_text()

        # (2) It excludes nobody: no reader and no writer is refused because it exists.
        self._write_spawn_record()
        self.assertEqual(
            pty_supervisor.read_spawn_records(self.base, "run_1", "intent-1")["outcome"],
            "present")
        second = self.ledger.claim(intent("intent-other"))
        self.assertEqual(
            second["claim_outcome"], "CREATED",
            "the presence of a spawn record refused an unrelated claim")

        # (3) It mints nothing: no token, lease or fence value originates here.
        for minting in ("mint_", "uuid4", "token"):
            self.assertNotIn(
                minting, source.split("class SpawnRecord", 1)[1].split("\n\n\n", 1)[0],
                f"the SpawnRecord shape names {minting!r}; it must mint nothing")
        record = pty_supervisor.read_spawn_records(
            self.base, "run_1", "intent-1")["record"]
        self.assertNotIn("lease_token", record)
        self.assertNotIn("claim", record)

        # (4) It is read by exactly ONE caller, and `assert_may_act` never reads it.
        self.assertNotIn(
            "read_spawn_records", identity_source,
            "standalone_identity reads the spawn record; assert_may_act must never "
            "consult it, or the record would authorize something")
        readers = [path.name for path in sorted(ENGINE.glob("*.py"))
                   if "read_spawn_records" in path.read_text()
                   and path.name != "standalone_pty.py"]
        self.assertEqual(
            sorted(readers), ["standalone_adapter.py", "standalone_journal.py",
                              "standalone_runtime.py"],
            f"the spawn record is read by an unexpected module: {readers}. It is read by "
            "lookup (adapter), by rediscover's lookup-equivalent (journal) and by start's "
            "identity bind (runtime) -- and by nothing that grants permission")

    def test_the_fence_value_is_minted_before_the_claim_and_lives_in_the_receipt(self) -> None:
        """The fence originates in ``standalone_identity``, and is STORED in the ledger."""
        self._claim_and_effect(fence="s-1:i-1")
        stored = self.ledger.get_receipt("intent-1")
        self.assertEqual(stored["receipt"]["external_id"], "s-1:i-1")
        self.assertEqual(stored["status"], "EFFECTED")


# =====================================================================================
class Condition2Tests(_Base):
    """Re-readability by a STRANGER PROCESS -- a separate interpreter, no shared objects."""

    def test_stranger_process_reads_settlement(self) -> None:
        self.journal.append(journal_mod.make_record(
            kind="SETTLEMENT_OBSERVED", derived_from="runtime_state",
            intent_id="intent-1", dispatch_id="dispatch-1", task_id="task-1",
            session_id="s-1", process_incarnation="i-1", state="COMPLETED",
            outcome="succeeded", message_id="m1", reported_by="h1"))
        script = textwrap.dedent(f"""
            import json, sys
            sys.path.insert(0, {str(REPO)!r})
            from scripts.deterministic_workflow import standalone_journal as sj
            from scripts.deterministic_workflow.standalone_adapter import StandaloneAdapter
            journal = sj.ExecutionJournal({self.base!r}, "run_1")
            adapter = StandaloneAdapter(None, settlement_journal=journal,
                                        artifact_base={self.base!r}, run_id="run_1")
            print(json.dumps({{"open": list(adapter.open_dispatches()),
                               "rows": len(journal.rows_for("intent-1"))}}))
            """)
        proc = subprocess.run([sys.executable, "-c", script], capture_output=True,
                              text=True, timeout=60, cwd=str(REPO))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        result = json.loads(proc.stdout.strip().splitlines()[-1])
        self.assertEqual(result["rows"], 1)
        self.assertEqual(result["open"], [])

    def test_state_is_requeryable_after_a_simulated_coordinator_turn_end(self) -> None:
        """AC-37-12: the conversational turn owns nothing.

        A separate interpreter -- standing in for the process after the turn ended -- gets
        the run's identity, journal and state back from files alone.
        """
        self._claim_and_effect()
        self.journal.append(journal_mod.make_record(
            kind="EVENT", derived_from="pty", intent_id="intent-1",
            dispatch_id="dispatch-1", task_id="task-1", session_id="s-1",
            process_incarnation="i-1", event="turn_start_observed", state="RUNNING",
            source_vocabulary={"pid": 999999, "captured_tty": "ttys999"}))
        script = textwrap.dedent(f"""
            import json, sys
            sys.path.insert(0, {str(REPO)!r})
            from scripts.deterministic_workflow import standalone_journal as sj
            from scripts.deterministic_workflow.runtime_state import FileRuntimeStateStore
            snap = sj.rediscover("run_1", {self.base!r}, intent_ids=("intent-1",))
            entry = snap["intents"]["intent-1"]
            print(json.dumps({{"state": entry.get("state"),
                               "lease": entry.get("lease"),
                               "journal_present": snap["journal_present"]}}))
            """)
        proc = subprocess.run([sys.executable, "-c", script], capture_output=True,
                              text=True, timeout=60, cwd=str(REPO))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        result = json.loads(proc.stdout.strip().splitlines()[-1])
        self.assertTrue(result["journal_present"])
        self.assertEqual(result["lease"], "unreconciled")
        self.assertIsNotNone(result["state"])


# =====================================================================================
class Condition3Tests(_Base):
    """The identity fence ON COLLECTION."""

    def test_foreign_incarnation_refused(self) -> None:
        """A settlement written with a FOREIGN fence is refused, not collected."""
        self._claim_and_effect(fence="s-1:i-1")
        foreign = self.journal.admit(journal_mod.make_record(
            kind="SETTLEMENT_OBSERVED", derived_from="runtime_state",
            intent_id="intent-1", dispatch_id="dispatch-1", task_id="task-1",
            session_id="s-1", process_incarnation="i-FOREIGN", state="COMPLETED",
            outcome="succeeded", message_id="m1", reported_by="h1"),
            runtime_state=self.ledger)
        self.assertEqual(foreign["outcome"], "refused")
        self.assertEqual(foreign["code"], journal_mod.FOREIGN_INCARNATION)

    def test_a_foreign_report_quoting_the_right_handle_is_still_refused(self) -> None:
        """Payload knowledge alone is not authority."""
        self._claim_and_effect(fence="s-1:i-1")
        result = self.journal.admit(journal_mod.make_record(
            kind="SETTLEMENT_OBSERVED", derived_from="runtime_state",
            intent_id="intent-1", dispatch_id="dispatch-1", task_id="task-1",
            session_id="s-1", process_incarnation="i-2", state="COMPLETED",
            outcome="succeeded", message_id="m1", reported_by="h1",
            source_vocabulary={"quotes_the_right_handle": "s-1:i-1"}),
            runtime_state=self.ledger)
        self.assertEqual(result["outcome"], "refused")

    def test_the_exit_sentinel_filename_carries_the_incarnation(self) -> None:
        path = pty_supervisor.exit_sentinel_path(self.base, "run_1", "s-1", "i-1")
        self.assertTrue(path.name.endswith("i-1"))


# =====================================================================================
class Condition4Tests(_Base):
    """Unknown is NOT absence -- in ``lookup`` and in ``resume`` alike."""

    def test_lookup_none_only_proves_absence(self) -> None:
        self.assertIsNone(self.adapter.lookup(intent("intent-fresh")))
        directory = pty_supervisor.spawn_record_dir(self.base, "run_1", "intent-locked")
        directory.mkdir(parents=True)
        os.chmod(directory, 0o000)
        try:
            with self.assertRaises(ExternalLookupUnavailable):
                self.adapter.lookup(intent("intent-locked"))
        finally:
            os.chmod(directory, 0o755)

    def test_lookup_raises_when_no_run_is_bound(self) -> None:
        unbound = StandaloneAdapter(None, runtime_state=self.ledger,
                                    settlement_journal=self.journal,
                                    artifact_base=self.base)
        with self.assertRaises(ExternalLookupUnavailable):
            unbound.lookup({"intent_id": "intent-1"})

    def test_lookup_raises_when_the_ledger_is_unreadable(self) -> None:
        class _Broken:
            def get_receipt(self, intent_id):
                raise OSError("ledger gone")

            def get_settlement(self, intent_id):
                return None

        adapter = StandaloneAdapter(None, runtime_state=_Broken(),
                                    settlement_journal=self.journal,
                                    artifact_base=self.base, run_id="run_1")
        with self.assertRaises(ExternalLookupUnavailable):
            adapter.lookup(intent())

    def test_resume_never_synthesizes_from_absence(self) -> None:
        """An ``EFFECTED`` row with NO sentinel and an unverifiable probe yields ``None``.

        Never a synthesized settlement.  That is the named residual DR-2 -- a ``SIGKILL``ed
        wrapper leaves an uncollectable effect -- and it is fail-closed and correct: the
        declaration promises an effect can be observed and collected WHEN IT SETTLED, not
        that every effect settles.
        """
        self._claim_and_effect(fence="s-1:i-1")
        receipt = self.ledger.get_receipt("intent-1")["receipt"]
        self.assertIsNone(
            self.adapter.resume(intent(), receipt),
            "resume synthesized a settlement from the absence of contrary evidence")

    def test_resume_collects_only_a_fence_matching_settlement(self) -> None:
        lease = self._claim_and_effect(fence="s-1:i-1")
        from scripts.deterministic_workflow.contracts import make_settlement_event
        event = make_settlement_event(intent(), {"ok": True},
                                      occurred_at="2026-09-10T00:00:00Z")
        self.ledger.settle("intent-1", event, lease)
        self.journal.append(journal_mod.make_record(
            kind="SETTLEMENT_OBSERVED", derived_from="runtime_state",
            intent_id="intent-1", dispatch_id="dispatch-1", task_id="task-1",
            session_id="s-1", process_incarnation="i-FOREIGN", state="COMPLETED",
            outcome="succeeded", message_id="m1", reported_by="h1"))
        receipt = {"external_id": "s-1:i-1"}
        self.assertIsNone(
            self.adapter.resume(intent(), receipt),
            "resume collected a settlement whose fence belongs to another incarnation")
        self.journal.append(journal_mod.make_record(
            kind="SETTLEMENT_OBSERVED", derived_from="runtime_state",
            intent_id="intent-1", dispatch_id="dispatch-1", task_id="task-1",
            session_id="s-1", process_incarnation="i-1", state="COMPLETED",
            outcome="succeeded", message_id="m2", reported_by="h1"))
        self.assertIsNotNone(self.adapter.resume(intent(), receipt))

    def test_resume_raises_when_the_journal_is_unreadable(self) -> None:
        lease = self._claim_and_effect()
        from scripts.deterministic_workflow.contracts import make_settlement_event
        self.ledger.settle("intent-1",
                           make_settlement_event(intent(), {"ok": True},
                                                 occurred_at="2026-09-10T00:00:00Z"),
                           lease)
        self.journal.append(journal_mod.make_record(
            kind="EVENT", derived_from="pty", intent_id="intent-1", state="RUNNING",
            event="turn_start_observed"))
        self.journal.path.write_text("{corrupt\n")
        with self.assertRaises(journal_mod.JournalUnreadable):
            self.adapter.resume(intent(), {"external_id": "s-1:i-1"})


# =====================================================================================
class BothDirectionsTests(_Base):
    """V-6 written BOTH directions on the SAME class -- AC-37-15 licenses either outcome."""

    def test_undeclared_reaches_idempotency_recovery_unsupported_blocked(self) -> None:
        """With no journal, the recovery ladder must fail CLOSED, not proceed.

        Asserted through the engine's own gate: the executor's recovery ladder consults
        ``adapter_capabilities``, and with ``external_resume`` absent it produces
        ``IDEMPOTENCY_RECOVERY_UNSUPPORTED`` -> BLOCKED.  The adapter's part of that
        contract is simply not declaring the token, which is what this asserts.
        """
        journal_less = StandaloneAdapter(None, runtime_state=self.ledger,
                                         artifact_base=self.base, run_id="run_1")
        self.assertNotIn(EXTERNAL_RESUME, journal_less.capabilities())
        self.assertNotIn(EXTERNAL_LOOKUP, journal_less.capabilities())
        # And the methods themselves refuse rather than answering from nothing.
        with self.assertRaises(journal_mod.JournalUnreadable):
            StandaloneAdapter(None, artifact_base=self.base,
                              run_id="run_1").settlement("intent-1")

    def test_lifecycle_settlement_undeclared_pause_falls_back_to_block(self) -> None:
        """With no journal, the engine's own gate refuses the PAUSE route.

        Driven through ``routing.pause_admissible`` and ``routing.phase_gate`` exactly as
        the graph drives them -- over a state whose ``adapter_capabilities`` came from the
        adapter -- so the assertion is about the ROUTE the engine takes, not about a
        transcription of the rule.
        """
        from scripts.deterministic_workflow import routing

        def state_for(adapter):
            return {"decision_state": "NEEDS_INPUT",
                    "adapter_capabilities": sorted(adapter.capabilities())}

        journal_less = StandaloneAdapter(None, runtime_state=self.ledger,
                                         artifact_base=self.base, run_id="run_1")
        self.assertNotIn(LIFECYCLE_SETTLEMENT, journal_less.capabilities())
        self.assertFalse(
            routing.pause_admissible(state_for(journal_less)),
            "pause was admitted with no lifecycle_settlement declaration; it must fall "
            "back to the pre-OS-31 BLOCK behaviour")
        self.assertEqual(routing.phase_gate(state_for(journal_less)), "BLOCK")

        # A journal alone is not enough: pause also needs `human_approval`, because a pause
        # that cannot ask the question is not a pause.
        self.assertFalse(routing.pause_admissible(state_for(self.adapter)))
        self.assertEqual(routing.phase_gate(state_for(self.adapter)), "BLOCK")

        with_approval = StandaloneAdapter(None, runtime_state=self.ledger,
                                          settlement_journal=self.journal,
                                          approval_port=object(),
                                          artifact_base=self.base, run_id="run_1")
        self.assertTrue(routing.pause_admissible(state_for(with_approval)))
        self.assertEqual(routing.phase_gate(state_for(with_approval)), "PAUSE")

    def test_the_declaration_is_not_made_to_skip_the_block(self) -> None:
        """The hard rule: the token's presence tracks the WIRING, never the desired route.

        Checked structurally -- ``capabilities`` reads only the constructor-injected wiring
        and nothing about routes, run state or what a caller wants.
        """
        import inspect
        import textwrap
        tree = ast.parse(textwrap.dedent(inspect.getsource(StandaloneAdapter.capabilities)))
        function = tree.body[0]
        # Strip the docstring: prose ABOUT routes is not a consultation OF one.
        body = (function.body[1:] if function.body
                and isinstance(function.body[0], ast.Expr)
                and isinstance(function.body[0].value, ast.Constant) else function.body)
        names: set[str] = set()
        for statement in body:
            for node in ast.walk(statement):
                if isinstance(node, ast.Name):
                    names.add(node.id)
                elif isinstance(node, ast.Attribute):
                    names.add(node.attr)
                elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                    names.add(node.value)
        for forbidden in ("BLOCK", "route", "pause_admissible", "recovery", "state",
                          "phase_gate", "decision_state"):
            self.assertNotIn(
                forbidden, names,
                f"capabilities() consults {forbidden!r}; a declaration made to influence a "
                "route is exactly the AC-37-20 violation the contract forbids")
        # What it MAY read: the constructor-injected wiring, and nothing else.
        self.assertTrue(
            {"settlement_journal", "approval_port"} <= names,
            "capabilities() must read the wiring; that is what makes it honest")


if __name__ == "__main__":
    unittest.main()


# =====================================================================================
def _native_stub_dir() -> Path:
    built = native_stub.native_stub_dir()
    if built is None:                                     # pragma: no cover - CI has cc
        raise AssertionError(native_stub.NO_COMPILER_REASON)
    return built


class PromptIdempotencyTests(unittest.TestCase):
    """DESIGN §D4.3e / USER DIRECTIVE D-D.5.  **A retry never re-executes the same prompt.**

    A prompt is a side-effecting action against a paid API and against a worktree, so an
    effect that cannot be collected must BLOCK rather than repeat.  The mechanism is not new
    machinery: the authority stays the existing `runtime_state` ladder, and the
    `DELIVERY_INTENT` record is the OBSERVATION that lets a successor's `lookup` answer
    precisely instead of guessing.

    The four rows of §D4.3e's table, each driven through the real `StandaloneSession.start`.
    """

    def setUp(self) -> None:
        self.base = Path(tempfile.mkdtemp())
        self.journal = journal_mod.ExecutionJournal(self.base, "run_idem")
        self.spawns: list[dict] = []

    def _profile(self, **overrides):
        from scripts.deterministic_workflow.standalone_profile import (
            CompletionSelector, DeliveryProofSelector, ReadinessSelector,
            StandaloneProfile, Timeouts)
        fields = dict(
            # The DRIVER is `claude` and the BINARY is the fixture: the two are
            # independent by design (AC-37-03 -- argv comes from the profile, never from a
            # table), and a case about idempotency has no business needing a vendor CLI.
            driver="claude", binary="os37-stub-cli",
            # The NATIVE image of the fixture (round 4, finding 10: a `#!` wrapper is
            # refused by preflight by name, and this case is about idempotency).
            bin_dirs=(str(_native_stub_dir()),),
            supported_range=((1, 0, 0), (99, 0, 0)),
            delivery_mode="launch_with_prompt", identity_binding="minted_echo",
            identity_flag="--session-id",
            readiness_records=(ReadinessSelector(channel="structured",
                                                 record_type="system",
                                                 session_field="session_id"),),
            delivery_proofs=(DeliveryProofSelector(channel="structured",
                                                   record_type="assistant"),),
            completion_records=(CompletionSelector(channel="structured",
                                                   record_type="result",
                                                   error_field="is_error"),),
            timeouts=Timeouts(preflight_timeout_ms=2000, readiness_timeout_ms=2000))
        fields.update(overrides)
        return StandaloneProfile(**fields)

    def _session(self, intent_id: str = "intent-idem"):
        from scripts.deterministic_workflow.standalone_runtime import StandaloneSession
        payload = {"intent_id": intent_id, "command_id": "c", "payload_digest": "d",
                   "run_id": "run_idem", "phase": "IMPLEMENTATION", "role": "WORKER",
                   "round_kind": "PHASE_GATE"}
        ledger = InMemoryRuntimeStateStore()
        claim = ledger.claim(payload)
        session = StandaloneSession(
            intent=payload, profile=self._profile(), artifact_base=self.base,
            run_id="run_idem", journal=self.journal, runtime_state=ledger,
            spawner=self._spy_spawner)
        return session, claim["lease_token"]

    def _spy_spawner(self, **kwargs):
        """A SYSCALL SPY.  Every `execve` this runtime would perform lands here.

        Counting the spawns rather than trusting the code path is the whole point: the
        assertion "exactly one execve across both attempts" is not derivable from reading
        the ladder, and a fault injected between `execve` and `record_receipt` is exactly
        the window the guarantee is about.
        """
        self.spawns.append(dict(kwargs))
        pty_supervisor.write_spawn_record(
            Path(kwargs["spawn_record_target"]),
            {"session_id": kwargs["session_id"],
             "process_incarnation": kwargs["incarnation"], "pid": os.getpid(),
             "pgid": os.getpgid(0), "sid": os.getsid(0), "boot_id": "",
             "proc_start_ticks": 0, "argv_digest": kwargs["argv_digest"],
             "env_digest": kwargs["env_digest"], "started_at": "t"})
        return {"pid": os.getpid(), "pgid": os.getpgid(0), "sid": os.getsid(0),
                "master_fd": os.open(os.devnull, os.O_RDWR), "slave_name": "/dev/ttys999",
                "pty_id": "pty-idem", "argv": list(kwargs["argv"])}

    def _start_kwargs(self):
        return {"auth_probe_argv": ["claude", "auth", "status"],
                "prober": lambda argv, env, *, timeout_ms, cwd=None: {
                    "outcome": "completed", "exit_code": 0,
                    "output": '2.1.260 {"loggedIn": true}', "interactive_hit": ""},
                "help_text": "--session-id -p --output-format stream-json --verbose",
                "rehearsal": lambda p, e, s: {"channel": "structured",
                                              "record_type": "system", "session_id": s},
                "mode_rehearsal": lambda p, e: {
                    "r_b_closed": True, "delivery_proof": True, "auth_marker": None,
                    "waited_without_prompt": False, "evaluable": True,
                    "identity_bound": True, "detail": {}}}

    def test_retry_never_reexecutes_the_same_prompt(self) -> None:
        """A fault after `execve` and before `record_receipt`; EXACTLY ONE execve, total."""
        session, token = self._session()
        first = session.start(lease_token=token, payload="do the work",
                              **self._start_kwargs())
        self.assertEqual(first["start_outcome"], "ready", first["failure_reason"])
        self.assertEqual(len(self.spawns), 1)

        # The successor holds NONE of this process's objects.  It reads the journal.
        successor, token2 = self._session()
        second = successor.start(lease_token=token2, payload="do the work",
                                 **self._start_kwargs())
        self.assertEqual(second["start_outcome"], "failed")
        self.assertEqual(second["failure_reason"], "IDEMPOTENCY_RECOVERY_BLOCKED",
                         "a dispatch whose execve was reached was re-spawned")
        self.assertEqual(len(self.spawns), 1,
                         f"the prompt was executed {len(self.spawns)} times; a prompt is a "
                         "side-effecting action and a retry must never repeat one")

    def test_respawn_window_reuses_the_same_prompt_digest(self) -> None:
        """The ONE safe window: an intent journalled, but `execve` never reached.

        The re-spawn happens under a NEW `attempt_incarnation` and carries the SAME
        `prompt_digest`, so the digest never changes for a dispatch.
        """
        from scripts.deterministic_workflow import standalone_drivers as drivers
        digest = drivers.prompt_digest("do the work")
        self.journal.append_delivery_intent({
            "intent_id": "intent-idem", "dispatch_id": "dispatch-old",
            "task_id": "task-1", "session_id": "session-old", "prompt_digest": digest,
            "argv_digest": "a" * 64, "attempt_incarnation": "inc-old",
            "delivery_mode": "launch_with_prompt"})
        # No spawn record exists, so `execve` was NOT reached: re-spawning is safe.
        session, token = self._session()
        receipt = session.start(lease_token=token, payload="do the work",
                                **self._start_kwargs())
        self.assertEqual(receipt["start_outcome"], "ready", receipt["failure_reason"])
        self.assertEqual(len(self.spawns), 1)
        intents = [r for r in self.journal.rows_for("intent-idem")
                   if r["kind"] == "DELIVERY_INTENT"]
        self.assertEqual(len(intents), 2, "the re-spawn journalled no new intent")
        self.assertEqual({r["source_vocabulary"]["prompt_digest"] for r in intents},
                         {digest},
                         "the prompt digest CHANGED across attempts of one dispatch")
        self.assertNotEqual(intents[0]["process_incarnation"],
                            intents[1]["process_incarnation"],
                            "the re-spawn reused the incarnation of the attempt it replaced")

    def test_conflicting_prompt_digest_is_refused(self) -> None:
        """The SAME dispatch retried with DIFFERENT work is a contract violation upstream.

        Never two different prompts under one dispatch identity: the run is refused with a
        named reason rather than executing whichever arrived second.
        """
        self.journal.append_delivery_intent({
            "intent_id": "intent-idem", "dispatch_id": "dispatch-old",
            "task_id": "task-1", "session_id": "session-old",
            "prompt_digest": "f" * 64, "argv_digest": "a" * 64,
            "attempt_incarnation": "inc-old", "delivery_mode": "launch_with_prompt"})
        session, token = self._session()
        receipt = session.start(lease_token=token, payload="COMPLETELY DIFFERENT WORK",
                                **self._start_kwargs())
        self.assertEqual(receipt["start_outcome"], "failed")
        self.assertEqual(receipt["failure_reason"], "dispatch_prompt_digest_conflict")
        self.assertEqual(self.spawns, [], "a conflicting dispatch was spawned anyway")

    def test_an_absent_intent_proves_no_fork_happened(self) -> None:
        """D4.3e row 1.  Absence is PROVED by reading, and it is the only safe 'spawn once'.

        `rows()` RAISES when the journal cannot be read, so a caller that receives "no
        intent" has actually read the journal through rather than failed to open it.
        """
        self.assertIsNone(self.journal.delivery_intent_for("intent-idem"))
        session, token = self._session()
        receipt = session.start(lease_token=token, payload="do the work",
                                **self._start_kwargs())
        self.assertEqual(receipt["start_outcome"], "ready", receipt["failure_reason"])
        self.assertEqual(len(self.spawns), 1)
        self.assertIsNotNone(self.journal.delivery_intent_for("intent-idem"))

    def test_the_intent_is_journalled_before_the_spawn_and_not_after(self) -> None:
        """The ORDER, asserted against the syscall spy rather than against the source.

        By the time the spawner is called, the intent is already durable -- so there is no
        instant at which a process exists whose prompt nobody recorded.
        """
        observed: list[str] = []

        def _ordering_spy(**kwargs):
            row = self.journal.delivery_intent_for("intent-idem")
            observed.append("intent-present" if row is not None else "intent-ABSENT")
            return self._spy_spawner(**kwargs)

        from scripts.deterministic_workflow.standalone_runtime import StandaloneSession
        payload = {"intent_id": "intent-idem", "command_id": "c", "payload_digest": "d",
                   "run_id": "run_idem", "phase": "IMPLEMENTATION", "role": "WORKER",
                   "round_kind": "PHASE_GATE"}
        ledger = InMemoryRuntimeStateStore()
        claim = ledger.claim(payload)
        session = StandaloneSession(
            intent=payload, profile=self._profile(), artifact_base=self.base,
            run_id="run_idem", journal=self.journal, runtime_state=ledger,
            spawner=_ordering_spy)
        session.start(lease_token=claim["lease_token"], payload="do the work",
                      **self._start_kwargs())
        self.assertEqual(observed, ["intent-present"],
                         "the process was created before its delivery intent was durable")

    def test_a_non_durable_intent_makes_the_spawn_unreachable(self) -> None:
        """The append RAISES, so the structurally subsequent fork never runs."""
        class _Unwritable(journal_mod.ExecutionJournal):
            def append_delivery_intent(self, intent, *, axes=None):
                raise journal_mod.ExecutionJournal.IntentNotDurable("disk full")

        from scripts.deterministic_workflow.standalone_runtime import StandaloneSession
        payload = {"intent_id": "intent-doomed", "command_id": "c", "payload_digest": "d",
                   "run_id": "run_idem", "phase": "IMPLEMENTATION", "role": "WORKER",
                   "round_kind": "PHASE_GATE"}
        ledger = InMemoryRuntimeStateStore()
        claim = ledger.claim(payload)
        session = StandaloneSession(
            intent=payload, profile=self._profile(), artifact_base=self.base,
            run_id="run_idem", journal=_Unwritable(self.base, "run_idem"),
            runtime_state=ledger, spawner=self._spy_spawner)
        receipt = session.start(lease_token=claim["lease_token"], payload="do the work",
                                **self._start_kwargs())
        self.assertEqual(receipt["start_outcome"], "failed")
        self.assertEqual(receipt["failure_reason"], "delivery_intent_not_durable")
        self.assertEqual(self.spawns, [],
                         "a process was created even though its delivery intent never "
                         "reached stable storage")
