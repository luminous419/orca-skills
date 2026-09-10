"""OS-37 V-11 / D9.7.  The settlement predicate, and FIVE TESTS THAT HOLD THE JOURNAL TO NON-AUTHORITY.

B-1 -- "OS-37 adds no claim, lease, fence, one-shot guard or idempotent-settlement
authority" -- is enforced here by tests rather than by an assurance.  Each of the five
forbids a specific way the journal could become an authority, and they are mechanical: an
AST sweep for a forbidding NAME, an AST sweep plus a write spy for a second ledger writer,
a live two-process check that the append lock is file-scoped, a deletion test proving the
journal is not load-bearing for exclusion, and a mutation test proving the fence value comes
from the receipt.

The settlement predicate is reproduced WHOLE, with both identity paths and the refusal.
Path (b) must exist -- a message-id-only check would refuse settlements Orca accepts, which
is a policy divergence under AC-37-20, not extra strictness -- and the refusal must exist
too.  "(b) without (c) is an amnesty; (c) without (b) is a divergence."
"""
from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path

from scripts.deterministic_workflow import standalone_journal as journal_mod
from scripts.deterministic_workflow.runtime_state import (InMemoryRuntimeStateStore,
                                                           default_owner_id)

ENGINE = Path(__file__).resolve().parent / "deterministic_workflow"
REPO = Path(__file__).resolve().parent.parent


def event(**overrides) -> dict:
    fields = dict(kind="EVENT", derived_from="pty", intent_id="intent-1",
                  dispatch_id="dispatch-1", task_id="task-1", session_id="s-1",
                  process_incarnation="i-1", event="spawned", state="STARTING")
    fields.update(overrides)
    return journal_mod.make_record(**fields)


def settlement(**overrides) -> dict:
    fields = dict(kind="SETTLEMENT_OBSERVED", derived_from="runtime_state",
                  intent_id="intent-1", dispatch_id="dispatch-1", task_id="task-1",
                  session_id="s-1", process_incarnation="i-1", state="COMPLETED",
                  outcome="succeeded", message_id="msg-1", reported_by="handle-1")
    fields.update(overrides)
    return journal_mod.make_record(**fields)


# =====================================================================================
class SettlementPredicateTests(unittest.TestCase):
    """V-11's three cases.  All three, because any two of them is a defect."""

    def setUp(self) -> None:
        self.journal = journal_mod.ExecutionJournal(tempfile.mkdtemp(), "run_1")
        self.receipt = {"dispatch_id": "dispatch-1", "dispatch_status": "COMPLETED",
                        "task_status": "COMPLETED", "message_id": "msg-1",
                        "from_handle": "handle-1"}
        self.candidate = {"dispatch_id": "dispatch-1", "dispatch_status": "COMPLETED",
                          "task_status": "COMPLETED", "provenance": "worker_report",
                          "outcome": "succeeded", "message_id": "msg-1",
                          "reported_by": "handle-1"}

    def test_settlement_predicate_case_a(self) -> None:
        """(a) the SAME message id confirms."""
        result = self.journal.settlement_confirmed(
            self.candidate, self.receipt, expected_outcome="succeeded")
        self.assertTrue(result["confirmed"], result)
        self.assertEqual(result["identity_path"], "a")

    def test_settlement_predicate_case_b(self) -> None:
        """(b) a DIFFERENT message id with the same handle and outcome ALSO confirms.

        This path is deliberate.  Refusing it would refuse a settlement Orca accepts, which
        is a policy divergence under AC-37-20 -- not extra strictness.
        """
        candidate = dict(self.candidate, message_id="msg-RETRY")
        result = self.journal.settlement_confirmed(
            candidate, self.receipt, expected_outcome="succeeded")
        self.assertTrue(result["confirmed"], result)
        self.assertEqual(result["identity_path"], "b")

    def test_settlement_predicate_case_c(self) -> None:
        """(c) a MATCHING message id with a NON-MATCHING dispatch id is REFUSED.

        Without this the predicate is an amnesty: a stale ``worker_done`` from a retried
        task would be harvested by the current dispatch.
        """
        candidate = dict(self.candidate, dispatch_id="dispatch-OTHER")
        result = self.journal.settlement_confirmed(
            candidate, self.receipt, expected_outcome="succeeded")
        self.assertFalse(result["confirmed"])
        self.assertEqual(result["refusal"], "unconfirmed_is_not_settled")
        self.assertIn("dispatch_id", result["failed_checks"])
        self.assertEqual(result["route"], "recovery")

    def test_every_exact_check_can_refuse_on_its_own(self) -> None:
        """No check is decorative: flipping any one of the five refuses."""
        for key, value in (("dispatch_status", "RUNNING"), ("task_status", "RUNNING"),
                           ("provenance", "coordinator_inference"),
                           ("outcome", "failed")):
            with self.subTest(key=key):
                candidate = dict(self.candidate, **{key: value})
                result = self.journal.settlement_confirmed(
                    candidate, self.receipt, expected_outcome="succeeded")
                self.assertFalse(result["confirmed"])
                self.assertIn(key, result["failed_checks"])

    def test_neither_identity_path_matching_is_a_named_refusal(self) -> None:
        candidate = dict(self.candidate, message_id="msg-X", reported_by="handle-X")
        result = self.journal.settlement_confirmed(
            candidate, self.receipt, expected_outcome="succeeded")
        self.assertFalse(result["confirmed"])
        self.assertEqual(result["identity_path"], "")
        self.assertEqual(result["refusal"], "unconfirmed_is_not_settled")

    def test_path_b_is_unavailable_when_the_receipt_names_no_handle(self) -> None:
        receipt = dict(self.receipt, from_handle=None)
        candidate = dict(self.candidate, message_id="msg-RETRY")
        result = self.journal.settlement_confirmed(
            candidate, receipt, expected_outcome="succeeded")
        self.assertFalse(result["confirmed"],
                         "path (b) fired with no from_handle to compare against")


# =====================================================================================
class IdempotentAdmissionTests(unittest.TestCase):
    """D9.3 S-0..S-7.  Every branch is total over the closed set; there is no ``else: True``."""

    def setUp(self) -> None:
        self.base = tempfile.mkdtemp()
        self.journal = journal_mod.ExecutionJournal(self.base, "run_1")
        self.ledger = InMemoryRuntimeStateStore()

    def test_s1_a_duplicate_message_id_is_no_effect(self) -> None:
        self.assertEqual(self.journal.admit(settlement())["outcome"], "admitted")
        again = self.journal.admit(settlement())
        self.assertEqual(again["outcome"], "no_effect")
        self.assertEqual(len(self.journal.rows_for("intent-1")), 1)

    def test_s2_an_idempotent_retry_is_admitted(self) -> None:
        self.journal.admit(settlement())
        retry = self.journal.admit(settlement(message_id="msg-2"))
        self.assertEqual(retry["outcome"], "admitted")
        self.assertEqual(retry["detail"], "accepted idempotent retry")

    def test_s3_a_stale_dispatch_is_refused(self) -> None:
        self.journal.admit(settlement())
        stale = self.journal.admit(settlement(message_id="msg-3",
                                              dispatch_id="dispatch-OLD"))
        self.assertEqual(stale["outcome"], "refused")
        self.assertEqual(stale["code"], journal_mod.SETTLEMENT_IDENTITY_MISMATCH)

    def test_s4_an_out_of_order_event_is_no_effect(self) -> None:
        """An event whose ``seq`` predates a terminal record has already been superseded.

        The sequence matters: an event is appended (seq 1), then a settlement (seq 2), then
        the SAME early event arrives again late.  It is ``no_effect`` rather than
        ``refused`` -- it is not wrong, it is simply already accounted for.
        """
        first = self.journal.append(event(event="turn_start_observed", state="RUNNING"))
        self.journal.admit(settlement())
        late = dict(event(event="turn_start_observed", state="RUNNING"))
        late["seq"] = first["seq"]
        result = self.journal.admit(late)
        self.assertEqual(result["outcome"], "no_effect")
        self.assertEqual(result["detail"],
                         "out-of-order arrival of a superseded event")

    def test_s5_a_foreign_incarnation_is_refused_against_the_LEDGER_fence(self) -> None:
        """The fence VALUE lives in the runtime-state receipt, not in this file."""
        intent = _intent("intent-1")
        claim = self.ledger.claim(intent)
        self.ledger.record_receipt("intent-1",
                                   {"intent_id": "intent-1", "task_id": "task-1",
                                    "dispatch_id": "dispatch-1",
                                    "external_id": "s-1:i-1"},
                                   claim["lease_token"])
        ok = self.journal.admit(event(event="turn_start_observed", state="RUNNING"),
                                runtime_state=self.ledger)
        self.assertEqual(ok["outcome"], "admitted")
        foreign = self.journal.admit(
            event(event="turn_start_observed", state="RUNNING",
                  process_incarnation="i-FOREIGN"), runtime_state=self.ledger)
        self.assertEqual(foreign["outcome"], "refused")
        self.assertEqual(foreign["code"], journal_mod.FOREIGN_INCARNATION)

    def test_s6_an_unreadable_authority_raises_rather_than_reading_as_empty(self) -> None:
        self.journal.append(event())
        path = self.journal.path
        path.write_text(path.read_text().replace('"state": "STARTING"',
                                                 '"state": "READY"'))
        with self.assertRaises(journal_mod.JournalUnreadable):
            self.journal.rows()
        with self.assertRaises(journal_mod.JournalUnreadable):
            self.journal.open_dispatches()

    def test_s0_defers_to_the_ledgers_settlement_verdict(self) -> None:
        """``admit`` READS the ledger's settlement; it does not re-decide it."""
        class _Contradicting:
            def get_settlement(self, intent_id):
                return {"outcome": "SUCCEEDED"}

            def get_receipt(self, intent_id):
                return None

        result = self.journal.admit(settlement(outcome="failed"),
                                    runtime_state=_Contradicting())
        self.assertEqual(result["outcome"], "refused")
        self.assertEqual(result["code"], journal_mod.SETTLEMENT_CONFLICT)

    def test_a_malformed_record_kind_is_refused_at_construction(self) -> None:
        with self.assertRaises(ValueError):
            journal_mod.make_record(kind="CLAIMED", derived_from="runtime_state",
                                    intent_id="i", state="STARTING")
        with self.assertRaises(ValueError):
            journal_mod.make_record(kind="EVENT", derived_from="journal",
                                    intent_id="i", state="STARTING")

    def test_seq_is_strictly_monotone_and_time_is_not_the_ordering_authority(self) -> None:
        seqs = [self.journal.append(event(event=name)).get("seq")
                for name in ("spawned", "identity_bound", "readiness_observed")]
        self.assertEqual(seqs, [1, 2, 3])
        rows = self.journal.rows()
        self.assertEqual([r["seq"] for r in rows], sorted(r["seq"] for r in rows))

    def test_settlement_none_is_proven_absent(self) -> None:
        self.assertIsNone(self.journal.settlement_of("intent-never"))

    def test_unreadable_authority_raises(self) -> None:
        self.journal.append(event())
        self.journal.path.write_text("{not json}\n")
        with self.assertRaises(journal_mod.JournalUnreadable):
            self.journal.settlement_of("intent-1")


# =====================================================================================
class JournalIsNotAnAuthorityTests(unittest.TestCase):
    """D9.7's five tests.  B-1, made mechanical."""

    def setUp(self) -> None:
        self.base = tempfile.mkdtemp()
        self.journal = journal_mod.ExecutionJournal(self.base, "run_1")
        self.ledger = InMemoryRuntimeStateStore()

    # -- 1 -------------------------------------------------------------------------------
    def test_journal_defines_no_claim(self) -> None:
        """AST: no module- or class-level name that could be a claim, lease, fence or takeover.

        And no import of ``recovery_store``, ``pause_store`` or ``pause_runtime``: a module
        that cannot reach the run-level authorities cannot duplicate them.
        """
        import re
        tree = ast.parse((ENGINE / "standalone_journal.py").read_text())
        forbidden = re.compile(r"(?i)claim|lease|takeover|acquire|run_lock")
        offenders: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                if forbidden.search(node.name):
                    offenders.append(f"{node.name} at line {node.lineno}")
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and forbidden.search(target.id):
                        offenders.append(f"{target.id} at line {node.lineno}")
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = ([alias.name for alias in node.names]
                         + [getattr(node, "module", "") or ""])
                for name in names:
                    for banned in ("recovery_store", "pause_store", "pause_runtime"):
                        if banned in name:
                            offenders.append(f"imports {banned} at line {node.lineno}")
        self.assertEqual(
            offenders, [],
            "standalone_journal defines or reaches a claim/lease/takeover mechanism; the "
            "journal is a DERIVED EVENT LOG and OS-37 adds no authority:\n"
            + "\n".join(offenders))
        # The `fence` word survives only where the journal COMPARES against the ledger's
        # value, which is the property; it must own no name that MINTS one.
        for name in ("mint_fence", "new_fence", "issue_fence", "acquire"):
            self.assertNotIn(name, (ENGINE / "standalone_journal.py").read_text())

    # -- 2 -------------------------------------------------------------------------------
    def test_journal_never_writes_runtime_state(self) -> None:
        """AST plus a WRITE SPY: zero ledger writes across a full ``admit`` sequence."""
        tree = ast.parse((ENGINE / "standalone_journal.py").read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                self.assertNotIn(
                    node.func.attr, ("claim", "record_receipt", "settle"),
                    f"standalone_journal calls .{node.func.attr}( at line {node.lineno}; "
                    "there is exactly one writer of the claim/receipt/settlement triple "
                    "and it is not this module")

        writes: list[str] = []

        class _SpyingLedger:
            def get_settlement(self, intent_id):
                return None

            def get_receipt(self, intent_id):
                return {"status": "EFFECTED",
                        "receipt": {"external_id": "s-1:i-1"}}

            def claim(self, intent):
                writes.append("claim")

            def record_receipt(self, *args, **kwargs):
                writes.append("record_receipt")

            def settle(self, *args, **kwargs):
                writes.append("settle")

        spy = _SpyingLedger()
        self.journal.admit(event(), runtime_state=spy)
        self.journal.admit(settlement(), runtime_state=spy)
        self.journal.admit(settlement(message_id="msg-2"), runtime_state=spy)
        self.assertEqual(writes, [],
                         f"the journal wrote to the ledger: {writes}")

    # -- 3 -------------------------------------------------------------------------------
    def test_append_lock_is_file_scoped(self) -> None:
        """While the ``flock`` is held, every real authority is still available.

        Run in a SEPARATE INTERPRETER while this process holds the lock, so the assertion
        is about the lock and not about re-entrancy.  Only a second appender to this one
        file waits.
        """
        script = textwrap.dedent(f"""
            import json, sys
            sys.path.insert(0, {str(REPO)!r})
            from scripts.deterministic_workflow import standalone_journal as sj
            from scripts.deterministic_workflow import recovery_store, pause_runtime
            from scripts.deterministic_workflow.runtime_state import InMemoryRuntimeStateStore
            done = {{}}
            ledger = InMemoryRuntimeStateStore()
            intent = {{"intent_id": "i-other", "task_id": "t", "dispatch_id": "d",
                       "payload_digest": "p", "command_id": "c", "run_id": "r",
                       "phase": "IMPLEMENTATION", "role": "WORKER",
                       "round_kind": "PHASE_GATE", "action_kind": "DISPATCH_AGENT"}}
            done["runtime_state_claim"] = ledger.claim(intent)["claim_outcome"]
            store = recovery_store.store_for("run_other", artifact_base={self.base!r})
            done["recovery_store_claim"] = store.claim(
                "run_other", thread_id="t", checkpoint_ns="", now_iso="2026-01-01T00:00:00Z",
                owner_kind="coordinator", takeover=False,
                continuation_token=None)["claim_outcome"]
            done["resume_run_is_callable"] = callable(pause_runtime.resume_run)
            journal = sj.ExecutionJournal({self.base!r}, "run_1")
            done["read_while_locked"] = len(journal.rows())
            print(json.dumps(done))
            """)
        with self.journal._append_lock():
            proc = subprocess.run([sys.executable, "-c", script], capture_output=True,
                                  text=True, timeout=60, cwd=str(REPO))
        self.assertEqual(proc.returncode, 0,
                         f"a real authority was blocked by the append lock:\n{proc.stderr}")
        result = json.loads(proc.stdout.strip().splitlines()[-1])
        self.assertEqual(result["runtime_state_claim"], "CREATED")
        self.assertEqual(result["recovery_store_claim"], "CREATED")
        self.assertTrue(result["resume_run_is_callable"])
        self.assertIsInstance(result["read_while_locked"], int)

    # -- 4 -------------------------------------------------------------------------------
    def test_deleting_the_journal_widens_nothing(self) -> None:
        """With the journal gone, a foreign fence is STILL refused, and absence still RAISES.

        Both halves matter.  The first proves the journal is not load-bearing for exclusion
        -- the fence value is the ledger's.  The second proves it is not load-bearing for
        ABSENCE either: with no authority to read, ``settlement()`` must raise rather than
        return ``None``, because ``None`` would be a claim that absence was proven.
        """
        intent = _intent("intent-1")
        claim = self.ledger.claim(intent)
        self.ledger.record_receipt("intent-1",
                                   {"intent_id": "intent-1", "task_id": "task-1",
                                    "dispatch_id": "dispatch-1",
                                    "external_id": "s-1:i-1"},
                                   claim["lease_token"])
        self.journal.append(event())
        os.unlink(self.journal.path)
        foreign = self.journal.admit(
            event(process_incarnation="i-FOREIGN"), runtime_state=self.ledger)
        self.assertEqual(foreign["outcome"], "refused")
        self.assertEqual(foreign["code"], journal_mod.FOREIGN_INCARNATION)

        from scripts.deterministic_workflow.standalone_adapter import StandaloneAdapter
        unwired = StandaloneAdapter(None, artifact_base=self.base, run_id="run_1")
        with self.assertRaises(journal_mod.JournalUnreadable):
            unwired.settlement("intent-1")

    # -- 5 -------------------------------------------------------------------------------
    def test_fence_value_comes_from_the_receipt(self) -> None:
        """Mutating the JOURNAL's copy changes no verdict; mutating the LEDGER's does."""
        intent = _intent("intent-1")
        claim = self.ledger.claim(intent)
        self.ledger.record_receipt("intent-1",
                                   {"intent_id": "intent-1", "task_id": "task-1",
                                    "dispatch_id": "dispatch-1",
                                    "external_id": "s-1:i-1"},
                                   claim["lease_token"])
        self.assertEqual(
            self.journal.admit(event(), runtime_state=self.ledger)["outcome"], "admitted")

        # Mutate the journal's own copy of the fence.  The verdict for a NEW record is
        # decided against the ledger, so nothing changes.
        raw = self.journal.path.read_text().replace('"s-1"', '"s-TAMPERED"')
        self.journal.path.write_text(raw)
        with self.assertRaises(journal_mod.JournalUnreadable):
            # The digest catches it -- which is itself the point: a tampered journal is
            # unreadable, not authoritative.
            self.journal.rows()

        # Now mutate the LEDGER's value, under the lease, and the verdict flips.
        fresh = journal_mod.ExecutionJournal(tempfile.mkdtemp(), "run_2")
        self.ledger.record_receipt("intent-1",
                                   {"intent_id": "intent-1", "task_id": "task-1",
                                    "dispatch_id": "dispatch-1",
                                    "external_id": "s-1:i-ROTATED"},
                                   claim["lease_token"])
        after = fresh.admit(event(), runtime_state=self.ledger)
        self.assertEqual(after["outcome"], "refused")
        self.assertEqual(after["code"], journal_mod.FOREIGN_INCARNATION)

    def test_no_second_pre_effect_claim_exists_anywhere_in_the_standalone_modules(self) -> None:
        """B-1 over ALL TWELVE modules, not only the journal.

        The one permitted new artifact is the child-side spawn record, and it is written
        AFTER the claim, by the child, and read by exactly one caller.
        """
        offenders: list[str] = []
        for path in sorted(ENGINE.glob("standalone_*.py")):
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.FunctionDef) and node.name in (
                        "claim", "acquire", "take_lock", "mint_lease", "takeover",
                        "run_lock", "one_shot"):
                    offenders.append(f"{path.name}:{node.lineno} defines {node.name}")
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                        and node.func.attr == "claim":
                    # Calling the LEDGER's claim would still be a second pre-effect claim,
                    # because the executor already took it before `adapter.start`.
                    offenders.append(f"{path.name}:{node.lineno} calls .claim(")
        self.assertEqual(
            offenders, [],
            "a standalone module defines or calls a claim; the intent-level claim is "
            "runtime_state.claim (taken by the executor BEFORE adapter.start) and the "
            "run-level one is recovery_store.claim:\n" + "\n".join(offenders))

    def test_the_standalone_modules_reuse_the_one_ledger_and_add_no_second(self) -> None:
        """AST: ``record_receipt`` and ``settle`` are REUSE; ``claim`` must not appear.

        The distinction is the whole of B-1, and it is easy to get backwards.
        ``runtime_state``'s ``claim -> record_receipt -> settle`` triple is ONE authority, and
        an adapter is *supposed* to write the last two: ``OrcaAdapter.start`` and
        ``FakeAdapter.start`` both call ``record_receipt`` and then
        ``runtime_state.settle(...)`` under the lease token, and ``settle`` is idempotent --
        it answers ``SETTLEMENT_CONFLICT`` on a conflicting second write, which is exactly why
        the executor can also call it immediately afterwards.  What B-1 forbids is a SECOND
        authority, not use of the one that exists.

        ``claim`` is different, and it is the one that must be absent: the executor takes the
        pre-effect claim BEFORE ``adapter.start`` is called, so an adapter that also claimed
        would be taking a second pre-effect claim on the stable intent -- the exact defect
        DESIGN F-001 was raised for.
        """
        writes: list[tuple[str, int, str]] = []
        for path in sorted(ENGINE.glob("standalone_*.py")):
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                        and node.func.attr in ("claim", "record_receipt", "settle"):
                    writes.append((path.name, node.lineno, node.func.attr))
        claims = [w for w in writes if w[2] == "claim"]
        self.assertEqual(
            claims, [],
            f"a standalone module calls .claim(: {claims}. The pre-effect claim is taken by "
            "the executor before adapter.start; a second one is what F-001 forbids")
        self.assertEqual(
            sorted({attr for _n, _l, attr in writes}), ["record_receipt", "settle"],
            f"the standalone ledger writes are not exactly the reused pair: {writes}")
        self.assertEqual(
            sorted({name for name, _l, _a in writes}), ["standalone_runtime.py"],
            f"more than one standalone module writes to the ledger: {writes}")
        name = "standalone_runtime.py"
        # Read the CALL NODE's arguments, not the module prose: prose near a call proves
        # nothing about what the call passes.
        tree = ast.parse((ENGINE / name).read_text())
        for attr in ("record_receipt", "settle"):
            fenced = False
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                        and node.func.attr == attr:
                    argument_names = {
                        child.id for argument in list(node.args)
                        + [kw.value for kw in node.keywords]
                        for child in ast.walk(argument) if isinstance(child, ast.Name)}
                    fenced = "lease_token" in argument_names
            with self.subTest(call=attr):
                self.assertTrue(
                    fenced,
                    f"{attr} was called without passing the caller's lease token; the "
                    "REUSED fence must be live in the standalone path, so the store refuses "
                    "a stale or absent token rather than writing an effect the owner does "
                    "not know about")
        self.assertNotIn(
            "settle", (ENGINE / "standalone_journal.py").read_text().split(
                "def admit", 1)[1].split("def ", 1)[0].replace("settled", "").replace(
                "settlement", ""),
            "standalone_journal.admit writes a settlement; it MIRRORS the ledger's verdict "
            "and never re-decides it")

    def test_stranger_process_reads_settlement(self) -> None:
        """V-6 condition 2: a SEPARATE INTERPRETER reconstructs the run.

        A separate interpreter, holding none of this process's objects, because that is what
        "re-readable by a stranger process" has to mean to be worth declaring.
        """
        self.journal.append(event())
        self.journal.append(settlement())
        script = textwrap.dedent(f"""
            import json, sys
            sys.path.insert(0, {str(REPO)!r})
            from scripts.deterministic_workflow import standalone_journal as sj
            snap = sj.rediscover("run_1", {self.base!r}, intent_ids=("intent-1",))
            print(json.dumps({{"intents": sorted(snap["intents"]),
                               "journal_present": snap["journal_present"],
                               "state": snap["intents"]["intent-1"].get("state")}}))
            """)
        proc = subprocess.run([sys.executable, "-c", script], capture_output=True,
                              text=True, timeout=60, cwd=str(REPO))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        result = json.loads(proc.stdout.strip().splitlines()[-1])
        self.assertEqual(result["intents"], ["intent-1"])
        self.assertTrue(result["journal_present"])
        self.assertIsNotNone(result["state"])

    def test_rediscover_reads_the_ledger_first(self) -> None:
        """D9.6: the LEDGER says what was claimed and effected; the journal only observed.

        With ledger status ``CLAIMED`` and no receipt, and no spawn record, the answer is
        ``never_started`` -- whatever the journal happens to hold.
        """
        intent = _intent("intent-2")
        self.ledger.claim(intent)
        self.journal.append(event(intent_id="intent-2", state="RUNNING",
                                  event="turn_start_observed"))
        snapshot = journal_mod.rediscover("run_1", self.base, runtime_state=self.ledger,
                                          intent_ids=("intent-2",))
        entry = snapshot["intents"]["intent-2"]
        self.assertEqual(entry["ledger_status"], "CLAIMED")
        self.assertEqual(entry["state"], "never_started")
        self.assertEqual(entry["spawn_record"], "absent")

    def test_a_missing_journal_still_yields_a_fail_closed_answer(self) -> None:
        intent = _intent("intent-3")
        claim = self.ledger.claim(intent)
        self.ledger.record_receipt("intent-3",
                                   {"intent_id": "intent-3", "task_id": "t",
                                    "dispatch_id": "d", "external_id": "s-9:i-9"},
                                   claim["lease_token"])
        snapshot = journal_mod.rediscover("run_1", self.base, runtime_state=self.ledger,
                                          intent_ids=("intent-3",))
        entry = snapshot["intents"]["intent-3"]
        self.assertEqual(entry["state"], "LOST")
        self.assertIn(entry["lost_reason"],
                      ("evidence_unreadable", "stop_unverified",
                       "process_table_unreadable"))

    def test_every_persisted_lease_is_unreconciled_on_load(self) -> None:
        intent = _intent("intent-4")
        self.ledger.claim(intent)
        snapshot = journal_mod.rediscover("run_1", self.base, runtime_state=self.ledger,
                                          intent_ids=("intent-4",))
        self.assertEqual(snapshot["intents"]["intent-4"]["lease"], "unreconciled",
                         "a restart must grant no writer on the strength of what the "
                         "previous process wrote")


def _intent(intent_id: str) -> dict:
    """An ``ActionIntent``-shaped mapping carrying every key ``claim`` reads.

    Built here rather than through ``contracts.make_intent`` so these tests exercise the
    REAL ledger against a minimal intent: the point is that the standalone path reuses the
    existing claim, so the claim must be the real one.
    """
    return {"intent_id": intent_id, "task_id": "task-1", "dispatch_id": "dispatch-1",
            "command_id": f"cmd-{intent_id}", "payload_digest": "digest",
            "run_id": "run_1", "action_kind": "DISPATCH_AGENT",
            "phase": "IMPLEMENTATION", "role": "WORKER", "round_kind": "PHASE_GATE"}


if __name__ == "__main__":
    unittest.main()


# =====================================================================================
class TwoSupervisorsCannotBothWinTests(unittest.TestCase):
    """The ticket's "두 Supervisor의 동일 run recovery claim 경쟁", asserted DIRECTLY.

    The other tests in this file prove the journal holds no authority.  That is necessary
    but not sufficient evidence for this requirement: it shows OS-37 added no SECOND
    mechanism, and says nothing about the first one still working.  So this class exercises
    the real run-level authority under contention and asserts exactly one winner.

    Both scopes are covered, because they are different mechanisms answering different
    questions: ``recovery_store.claim`` for the RUN and ``runtime_state.claim`` for the
    INTENT.  OS-37 reuses both and duplicates neither.
    """

    def setUp(self) -> None:
        self.base = tempfile.mkdtemp()

    def test_two_claimants_on_the_same_run_produce_exactly_one_CREATED(self) -> None:
        """RUN scope.  A live holder is refused to EVERY other attempt.

        Including a second attempt inside the SAME PROCESS -- a Watchdog against another
        Watchdog is refused exactly as a Watchdog against a Coordinator is, because the
        claimant is the attempt and not the identity.
        """
        from scripts.deterministic_workflow import recovery_store
        store = recovery_store.store_for("run_race", artifact_base=self.base)
        kwargs = dict(thread_id="t", checkpoint_ns="", now_iso="2026-09-10T00:00:00Z",
                      owner_kind="coordinator", takeover=False, continuation_token=None)
        first = store.claim("run_race", **kwargs)
        self.assertEqual(first["claim_outcome"], "CREATED")

        outcomes, refusals = [], []
        for attempt in range(3):
            with self.subTest(attempt=attempt):
                try:
                    outcomes.append(store.claim("run_race", **kwargs)["claim_outcome"])
                except Exception as exc:                       # noqa: BLE001
                    refusals.append(type(exc).__name__)
        self.assertNotIn(
            "CREATED", outcomes,
            f"a second claimant also got CREATED: {outcomes}; exactly one attempt may own "
            "the run")
        self.assertTrue(
            refusals or outcomes,
            "the second claimant neither won, lost, nor was refused -- it got nothing")

    def test_a_second_claimant_from_a_separate_interpreter_is_also_refused(self) -> None:
        """The same, across PROCESSES, because that is the real contention.

        Two Watchdog sweeps are two processes.  An in-process check could pass on a lock
        that only guards one interpreter's memory.
        """
        from scripts.deterministic_workflow import recovery_store
        store = recovery_store.store_for("run_race2", artifact_base=self.base)
        first = store.claim("run_race2", thread_id="t", checkpoint_ns="",
                            now_iso="2026-09-10T00:00:00Z", owner_kind="coordinator",
                            takeover=False, continuation_token=None)
        self.assertEqual(first["claim_outcome"], "CREATED")
        script = textwrap.dedent(f"""
            import json, sys, traceback
            sys.path.insert(0, {str(REPO)!r})
            from scripts.deterministic_workflow import recovery_store
            store = recovery_store.store_for("run_race2", artifact_base={self.base!r})
            try:
                out = store.claim("run_race2", thread_id="t", checkpoint_ns="",
                                  now_iso="2026-09-10T00:00:01Z",
                                  owner_kind=recovery_store.OWNER_KIND_RECOVERY,
                                  takeover=False, continuation_token=None)
                print(json.dumps({{"outcome": out["claim_outcome"]}}))
            except Exception as exc:
                print(json.dumps({{"refused": type(exc).__name__,
                                   "detail": str(exc)[:200]}}))
            """)
        proc = subprocess.run([sys.executable, "-c", script], capture_output=True,
                              text=True, timeout=60, cwd=str(REPO))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        result = json.loads(proc.stdout.strip().splitlines()[-1])
        self.assertNotEqual(
            result.get("outcome"), "CREATED",
            "a separate process also got CREATED while a live lease was held; two "
            "Supervisors would both believe they owned the run")
        # The refusal must be about the HELD AUTHORITY.  Asserted because this test used to
        # pass on an argument-validation error (`owner_kind="watchdog"` is not a declared
        # kind), which refuses before the lease is ever consulted -- so it proved the
        # argument validator worked and nothing at all about ownership.
        self.assertEqual(
            result.get("refused"), "RecoveryAuthorityHeld",
            f"the second claimant was refused for the wrong reason: {result}")

    # ---- the REAL race: two independent processes released together -----------------
    #
    # The two cases above establish that a live holder refuses every later attempt.  That
    # is refusal-against-an-existing-owner, and the review (F-004) correctly pointed out
    # that it is NOT a race: the parent's ``claim`` has already RETURNED before the loser
    # even starts.  Exclusion under contention is a different property, and it is the one
    # the ticket names ("두 Supervisor의 동일 run recovery claim 경쟁").
    #
    # So: two independent interpreters, no prior claim on the run at all, both parked on a
    # filesystem barrier, released together, each doing nothing between the release and the
    # claim but a spin on a single ``os.path.exists``.  Both results are collected and the
    # assertion is the invariant -- EXACTLY ONE ``CREATED``, and every other claimant
    # carries a NAMED refusal rather than silence.

    #: Repeated, because one round of a race can be won by scheduling luck without the lock
    #: ever being contended.  Each round is a fresh, previously UNCLAIMED run.
    RACE_ROUNDS = 5

    _CLAIMANT = textwrap.dedent("""
        import json, os, sys
        sys.path.insert(0, sys.argv[1])
        base, run_id, barrier, index, scope = sys.argv[2:7]
        from scripts.deterministic_workflow import recovery_store
        from scripts.deterministic_workflow import runtime_state as rs

        if scope == "run":
            store = recovery_store.store_for(run_id, artifact_base=base)
            def attempt():
                return store.claim(run_id, thread_id="t", checkpoint_ns="",
                                   now_iso="2026-09-10T00:00:0%s" % index,
                                   owner_kind=recovery_store.OWNER_KIND_RECOVERY,
                                   takeover=False,
                                   continuation_token=None)["claim_outcome"]
        else:
            store = rs.FileRuntimeStateStore(os.path.join(base, run_id + ".json"))
            intent = {"intent_id": run_id, "command_id": "c", "payload_digest": "d",
                      "run_id": run_id, "phase": "IMPLEMENTATION", "role": "WORKER",
                      "round_kind": "PHASE_GATE"}
            def attempt():
                return store.claim(intent)["claim_outcome"]

        # Park on the barrier.  Everything that can be done before the race IS done before
        # the race: imports, the store object, the intent dict.  What remains after the
        # release is the claim and nothing else.
        open(os.path.join(barrier, "ready." + index), "w").close()
        go = os.path.join(barrier, "go")
        while not os.path.exists(go):
            pass
        try:
            print(json.dumps({"claimant": index, "outcome": attempt()}))
        except Exception as exc:
            print(json.dumps({"claimant": index, "refused": type(exc).__name__,
                              "detail": str(exc)[:200]}))
    """)

    def _race(self, scope: str, run_id: str, *, claimants: int = 2) -> list[dict]:
        """Release ``claimants`` independent processes at the same instant.  Collect all."""
        barrier = Path(self.base) / f"barrier-{scope}-{run_id}"
        barrier.mkdir(parents=True, exist_ok=True)
        script = barrier / "claimant.py"
        script.write_text(self._CLAIMANT)

        procs = [subprocess.Popen(
            [sys.executable, str(script), str(REPO), str(self.base), run_id,
             str(barrier), str(index), scope],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd=str(REPO))
            for index in range(claimants)]
        try:
            deadline = time.time() + 60
            while time.time() < deadline:
                if all((barrier / f"ready.{i}").exists() for i in range(claimants)):
                    break
                for proc in procs:
                    if proc.poll() is not None:
                        raise AssertionError(
                            f"a claimant exited before reaching the barrier: "
                            f"{proc.communicate()[1][:400]}")
                time.sleep(0.01)
            else:
                raise AssertionError("the claimants never all reached the barrier")

            (barrier / "go").touch()           # <- the release; both are spinning on this
            results = []
            for proc in procs:
                out, err = proc.communicate(timeout=60)
                self.assertEqual(proc.returncode, 0, err[:800])
                self.assertTrue(out.strip(), f"a claimant printed nothing: {err[:400]}")
                results.append(json.loads(out.strip().splitlines()[-1]))
            return results
        finally:
            for proc in procs:
                if proc.poll() is None:        # pragma: no cover - only on a hung claimant
                    proc.kill()

    def test_two_concurrent_claimants_on_an_unclaimed_run_yield_exactly_one_winner(
            self) -> None:
        """RUN scope, contended.  Nobody owns the run when the barrier lifts."""
        for round_index in range(self.RACE_ROUNDS):
            run_id = f"run_true_race_{round_index}"
            with self.subTest(round=round_index, run=run_id):
                results = self._race("run", run_id)
                self.assertEqual(len(results), 2)
                winners = [r for r in results if r.get("outcome") == "CREATED"]
                self.assertEqual(
                    len(winners), 1,
                    f"two Supervisors raced for an unclaimed run and {len(winners)} of "
                    f"them got CREATED: {results}")
                losers = [r for r in results if r is not winners[0]]
                for loser in losers:
                    named = loser.get("refused") or loser.get("outcome")
                    self.assertTrue(
                        named,
                        f"the losing claimant neither won nor was refused by name: {loser}")
                    self.assertNotEqual(named, "CREATED")
                    # The refusal must come from the AUTHORITY, not from argument
                    # validation.  A loser refused for any other reason would mean the two
                    # claims never actually contended for the lock, and the round would
                    # prove nothing about exclusion.
                    self.assertEqual(
                        named, "RecoveryAuthorityHeld",
                        f"the loser was refused before it ever contended: {loser}")

    def test_two_concurrent_claimants_on_an_unclaimed_intent_yield_exactly_one_winner(
            self) -> None:
        """INTENT scope, contended.  The other authority OS-37 reuses, raced the same way.

        Both scopes are asserted because they are different mechanisms answering different
        questions, and OS-37's claim is that it added no third one.  A run-scope race
        passing tells you nothing about the ledger that guards the external effect.
        """
        for round_index in range(self.RACE_ROUNDS):
            run_id = f"intent_true_race_{round_index}"
            with self.subTest(round=round_index, intent=run_id):
                results = self._race("intent", run_id)
                self.assertEqual(len(results), 2)
                winners = [r for r in results if r.get("outcome") == "CREATED"]
                self.assertEqual(
                    len(winners), 1,
                    f"two Supervisors raced for an unclaimed intent and {len(winners)} of "
                    f"them got CREATED: {results}")
                for loser in (r for r in results if r is not winners[0]):
                    named = loser.get("refused") or loser.get("outcome")
                    self.assertTrue(named, f"the loser was neither refused nor named: {loser}")
                    self.assertNotEqual(named, "CREATED")
                    self.assertEqual(
                        named, "RuntimeStateLeaseHeld",
                        f"the loser was refused before it ever contended: {loser}")

    def test_the_race_harness_really_holds_both_claimants_until_the_release(self) -> None:
        """The barrier is load-bearing, so it is asserted rather than assumed.

        A barrier that let one claimant through early would turn the two tests above back
        into the sequential case the review rejected.  Here the release is DELAYED: both
        claimants must still be parked -- neither exited -- after their ready files appear
        and before ``go`` is created.
        """
        run_id = "run_barrier_probe"
        barrier = Path(self.base) / f"barrier-probe-{run_id}"
        barrier.mkdir(parents=True, exist_ok=True)
        script = barrier / "claimant.py"
        script.write_text(self._CLAIMANT)
        procs = [subprocess.Popen(
            [sys.executable, str(script), str(REPO), str(self.base), run_id,
             str(barrier), str(index), "run"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd=str(REPO))
            for index in range(2)]
        try:
            deadline = time.time() + 60
            while time.time() < deadline and not all(
                    (barrier / f"ready.{i}").exists() for i in range(2)):
                time.sleep(0.01)
            self.assertTrue(all((barrier / f"ready.{i}").exists() for i in range(2)),
                            "the claimants never reached the barrier")
            time.sleep(0.3)
            for index, proc in enumerate(procs):
                self.assertIsNone(
                    proc.poll(),
                    f"claimant {index} ran to completion before the barrier was released; "
                    "the race would be sequential")
            from scripts.deterministic_workflow import recovery_store
            record_path = recovery_store.recovery_record_path(
                run_id, artifact_base=self.base)
            self.assertFalse(
                Path(record_path).exists(),
                "the run was already claimed before the release; the race would not be "
                "against an unclaimed run")
            (barrier / "go").touch()
            outcomes = []
            for proc in procs:
                out, err = proc.communicate(timeout=60)
                self.assertEqual(proc.returncode, 0, err[:800])
                outcomes.append(json.loads(out.strip().splitlines()[-1]))
            self.assertEqual(
                len([r for r in outcomes if r.get("outcome") == "CREATED"]), 1, outcomes)
        finally:
            for proc in procs:
                if proc.poll() is None:        # pragma: no cover
                    proc.kill()

    def test_resuming_requires_the_minted_token_and_never_a_resemblance(self) -> None:
        """"There is deliberately no way to resume by RESEMBLING the holder."" """
        from scripts.deterministic_workflow import recovery_store
        store = recovery_store.store_for("run_race3", artifact_base=self.base)
        kwargs = dict(thread_id="t", checkpoint_ns="", now_iso="2026-09-10T00:00:00Z",
                      owner_kind="coordinator", takeover=False)
        first = store.claim("run_race3", continuation_token=None, **kwargs)
        resumed = store.claim("run_race3",
                              continuation_token=first["lease_token"], **kwargs)
        self.assertEqual(resumed["claim_outcome"], "RESUMED")
        with self.assertRaises(Exception):
            store.claim("run_race3", continuation_token="a-token-nobody-minted", **kwargs)

    def test_two_claimants_on_the_same_intent_produce_exactly_one_CREATED(self) -> None:
        """INTENT scope.  This is the claim OS-37's ``start`` path reuses.

        The executor takes it BEFORE ``adapter.start`` is called, which is why the
        standalone adapter writes only the receipt.
        """
        ledger = InMemoryRuntimeStateStore()
        first = ledger.claim(_intent("intent-race"))
        self.assertEqual(first["claim_outcome"], "CREATED")
        outcomes, refusals = [], []
        for attempt in range(3):
            try:
                outcomes.append(ledger.claim(_intent("intent-race"))["claim_outcome"])
            except Exception as exc:                           # noqa: BLE001
                refusals.append(type(exc).__name__)
        self.assertNotIn("CREATED", outcomes,
                         f"a second claimant also got CREATED on the intent: {outcomes}")
        self.assertTrue(refusals or outcomes)

    def test_the_standalone_modules_add_no_competing_run_scope_mechanism(self) -> None:
        """And the reason both authorities still work is that OS-37 added nothing beside them.

        Asserted over all twelve modules: none imports `recovery_store`, `pause_store` or
        `pause_runtime`, so none can hold, take over or resemble a run-level claim.
        """
        for path in sorted(ENGINE.glob("standalone_*.py")):
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                names: list[str] = []
                if isinstance(node, ast.Import):
                    names = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom):
                    names = [(node.module or "")] + [a.name for a in node.names]
                for name in names:
                    for banned in ("recovery_store", "pause_store", "pause_runtime"):
                        self.assertNotIn(
                            banned, name,
                            f"{path.name} imports {banned}; the run-level authority is "
                            "reused by the ENGINE, and a standalone module that could "
                            "reach it could duplicate it")


class DeliveryIntentTests(unittest.TestCase):
    """DESIGN §D4.3a / USER DIRECTIVE D-D.1.  The ATOMIC delivery intent.

    Three properties, and the third is the one that carries the guarantee:

    1. it records the spawn request AND the prompt digest as ONE record;
    2. it is journalled AFTER the existing claim and BEFORE the fork;
    3. **an append failure makes spawning UNREACHABLE** -- not "logged", not "retried
       without it": the append raises, so the structurally subsequent `fork` never runs.

    And a fourth, negative one that AC-37-20 depends on: it is NOT a claim.
    """

    def setUp(self) -> None:
        self.base = Path(tempfile.mkdtemp())
        self.journal = journal_mod.ExecutionJournal(self.base, "run_intent")

    def _intent(self, **overrides):
        fields = {"intent_id": "intent-1", "dispatch_id": "dispatch-1",
                  "task_id": "task-1", "session_id": "session-1",
                  "prompt_digest": "d" * 64, "argv_digest": "a" * 64,
                  "attempt_incarnation": "inc-1",
                  "delivery_mode": "launch_with_prompt"}
        fields.update(overrides)
        return fields

    def test_the_intent_carries_the_spawn_request_and_the_prompt_digest_as_one_record(self):
        row = self.journal.append_delivery_intent(self._intent())
        self.assertEqual(row["kind"], "DELIVERY_INTENT")
        self.assertEqual(row["derived_from"], "driver")
        self.assertEqual(row["source_vocabulary"]["prompt_digest"], "d" * 64)
        self.assertEqual(row["source_vocabulary"]["argv_digest"], "a" * 64)
        self.assertEqual(row["source_vocabulary"]["delivery_mode"], "launch_with_prompt")
        self.assertTrue(row["source_vocabulary"]["intended_at"])
        # ONE record, not two: a spawn request written separately from its prompt digest
        # would leave a window in which a successor sees one and not the other.
        rows = [r for r in self.journal.rows_for("intent-1")
                if r["kind"] == "DELIVERY_INTENT"]
        self.assertEqual(len(rows), 1)

    def test_an_incomplete_intent_is_refused(self) -> None:
        for missing in ("prompt_digest", "argv_digest", "attempt_incarnation",
                        "delivery_mode", "dispatch_id"):
            with self.subTest(missing):
                with self.assertRaises(ValueError):
                    self.journal.append_delivery_intent(self._intent(**{missing: ""}))

    def test_the_prompt_itself_is_never_journalled(self) -> None:
        """Only the DIGEST.  The journal is a plain file a stranger interpreter reads."""
        with self.assertRaises(ValueError):
            self.journal.append_delivery_intent(
                dict(self._intent(), prompt="the actual secret-bearing prompt"))
        with self.assertRaises(ValueError):
            self.journal.append_delivery_intent(
                dict(self._intent(), payload="the actual secret-bearing prompt"))

    def test_the_intent_is_not_a_claim(self) -> None:
        """AC-37-20.  The journal stays OBSERVATIONAL and the ledger stays the authority.

        `DELIVERY_INTENT` grants no exclusivity, settles nothing, and `admit`'s ladder never
        consults it to decide whether a settlement is authoritative.  The record-kind
        vocabulary still has no `CLAIMED` member, which is the same rule stated as a type.
        """
        self.assertNotIn("CLAIMED", journal_mod.RECORD_KINDS)
        row = self.journal.append_delivery_intent(self._intent())
        self.assertEqual(row["axes"]["settlement"], "unknown")
        self.assertEqual(row["axes"]["process_liveness"], "unverifiable",
                         "the process does not exist yet, so nothing about it is verifiable")
        self.assertEqual(row["axes"]["cleanup_authority"], "unknown")
        self.assertEqual(row["outcome"], "",
                         "a delivery intent settles nothing and outcomes nothing")
        # A second intent for the SAME dispatch is not refused by exclusivity -- the journal
        # holds no lease.  It is the runtime's digest comparison that refuses conflicting
        # work, and the ledger that refuses a second claim.
        self.journal.append_delivery_intent(self._intent(attempt_incarnation="inc-2"))
        self.assertEqual(
            len([r for r in self.journal.rows_for("intent-1")
                 if r["kind"] == "DELIVERY_INTENT"]), 2)

    def test_the_append_returns_only_after_the_record_is_durable(self) -> None:
        """O-3: the fsync has RETURNED before this call does.

        Asserted by reading the file back through a stranger `ExecutionJournal` -- a
        different object with no shared buffers -- immediately after the append returns.
        """
        self.journal.append_delivery_intent(self._intent())
        stranger = journal_mod.ExecutionJournal(self.base, "run_intent")
        found = stranger.delivery_intent_for("intent-1")
        self.assertIsNotNone(found, "the intent was not durable when the append returned")
        self.assertEqual(found["source_vocabulary"]["prompt_digest"], "d" * 64)

    def test_an_append_failure_raises_so_the_spawn_is_unreachable(self) -> None:
        """The guarantee, as CONTROL FLOW rather than as a promise.

        A returned boolean could be ignored by a caller and the process spawned anyway --
        and then a successor could neither prove the prompt was delivered nor prove it was
        not.  An exception cannot be ignored: `StandaloneSession.start` writes the spawn
        AFTER this call, so a raise makes the `fork` structurally unreachable.
        """
        class _Unwritable(journal_mod.ExecutionJournal):
            def append(self, record):
                raise OSError(28, "No space left on device")

        broken = _Unwritable(self.base, "run_intent")
        with self.assertRaises(journal_mod.ExecutionJournal.IntentNotDurable):
            broken.append_delivery_intent(self._intent(intent_id="intent-doomed"))
        # ...and nothing was recorded, so a successor's `lookup` PROVES no fork happened.
        self.assertIsNone(self.journal.delivery_intent_for("intent-doomed"))

    def test_absence_is_proved_by_reading_never_assumed(self) -> None:
        """`None` means ABSENT.  An unreadable journal RAISES instead."""
        self.assertIsNone(self.journal.delivery_intent_for("never-dispatched"))
        self.journal.path.write_text('{"seq": 1, "digest": "tampered"}\n')
        with self.assertRaises(journal_mod.JournalUnreadable):
            self.journal.delivery_intent_for("intent-1")

    def test_the_intent_precedes_the_spawn_record_for_the_same_dispatch(self) -> None:
        """D4.3e row 1: absence of this record PROVES no `fork` happened."""
        intent_row = self.journal.append_delivery_intent(self._intent())
        spawn_row = self.journal.append(journal_mod.make_record(
            kind="SPAWN_OBSERVED", derived_from="pty", event="identity_bound",
            state="STARTING", intent_id="intent-1", dispatch_id="dispatch-1",
            task_id="task-1", session_id="session-1", process_incarnation="inc-1"))
        self.assertLess(intent_row["seq"], spawn_row["seq"])
        kinds = [r["kind"] for r in self.journal.rows_for("intent-1")]
        self.assertEqual(kinds[0], "DELIVERY_INTENT")
