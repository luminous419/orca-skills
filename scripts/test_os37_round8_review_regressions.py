"""OS-37 BUGFIX (run_e4962a4c229b): one behaviour-based lock per "must fix" item of the
consolidated external review of head `fe7ea84` (issuecomment-5672538284).

Each test FAILS at `fe7ea84` and passes after the fix, and each exercises the PRODUCTION
wiring -- `build_standalone_adapter` -> adapter -> runtime -> capture / lifecycle /
profile, the executor's recovery ladder, the launcher's authority read path, the watchdog
`recover` verb -- and reads DURABLE / OS-level state (the ledger, the journal, the archive
bytes, file modes, the migration audit log) rather than a helper's return value.

  1. `lookup()` returns EXACTLY the closed receipt shape, and the spawn-record-before-
     receipt crash window is COLLECTED through `executor._recover`, never
     `RuntimeStateCorrupt`;
  2. a parsed settlement record inside an UNANSWERABLE capture (a stranger's forged
     record past the declared length) never settles COMPLETED -- the capture's own typed
     refusal gates the ledger, the journal and the verdict, through `adapter.start`;
  3. an OMITTED profile worktree is frozen to the launching process's absolute cwd at the
     composition door: resume, watchdog wiring and the real `recover` from another cwd
     rebuild the launch cwd; a legacy empty archive is refused by name and recovered only
     through the audited migration; the write door refuses an unfrozen spec;
  4. the legacy-authority upgrade REFUSES a run that persisted no prompt composition
     (`STANDALONE_PROMPT_COMPOSITION_MISSING`, on resume AND watchdog, bytes untouched)
     and the explicit audited `migrate-standalone-prompt-composition` is the remedy;
  5. structured streams split on the protocol delimiter only: records holding U+2028 /
     U+2029 / U+0085 inside a string parse whole in the capture reader, the driver, the
     journal and the audit-log readers; `\\r\\n` delimits exactly like `\\n`;
  6. a process that exits between the last rung-2 probe and the G2 read is the proven
     exit `interrupted_confirmed`, not an ownership refusal, and no SIGKILL is sent;
  7. a migration retry after a rolled-back attempt carries its OWN attempt identity: a
     crash after the retry's re-bind reconciles to exactly one committed record for the
     live authority, with a linear per-attempt history;
  8. reconciliation loads the target archive through the production loader (digest AND
     schema) before committing, propagates an unreadable authority as its typed refusal,
     rolls back only with positive proof, and refuses an undecidable state by name;
  9. the credential seed is 0600 from its first byte -- observed at every point, under a
     permissive umask -- with no auth bytes retained anywhere.

Iteration 2 (CI job 104230478567 on `6908ea9`, F06 `outcome=failed, failure_reason=''`):
a final record that reaches the pty master only AFTER the exit is proven is read to the
HANGUP before any settlement decision (`drain_after_exit`, bounded); a failed receipt
carries its typed reason; and the exit watcher's appender records its append intent
BEFORE the bytes, so an in-flight suffix is `verified` and only a forged one is refused.

Iteration 3 (reviewer finding, `REVIEW_BUGFIX_iteration2.md` §5/§10): only EOF / `EIO`
is the hangup -- `EINTR` is retried, every other read / poll error is `master_unreadable`
with the errno named -- and `await_completion` GATES settlement on positive stream
finality: `ended != "hangup"` (`budget`, `master_unreadable`) is the typed LOST reason
`stream_end_unproven`, never COMPLETED, never a success row or ledger receipt.
"""
from __future__ import annotations

import contextlib
import errno
import io
import json
import os
import shutil
import stat
import tempfile
import threading
import time
import unittest
from pathlib import Path
from typing import Any

from scripts.deterministic_workflow import (executor,  # noqa: E402
                                            launcher,
                                            standalone_capture as capture_mod,
                                            standalone_drivers as drivers,
                                            standalone_interrupt as interrupt_mod,
                                            standalone_journal as journal_mod,
                                            standalone_pty as pty_supervisor,
                                            standalone_runtime as runtime_mod)
from scripts.deterministic_workflow.runtime_state import (  # noqa: E402
    FileRuntimeStateStore, RECEIPT_KEYS, RuntimeStateCorrupt, validate_record)
from scripts.test_os37_external_review_regressions import (  # noqa: E402
    STREAMS, WORKER_INTENT_KEYS, _ProductionPath, profile_spec, replay_profile)
from scripts.test_os37_followup_review_regressions import (  # noqa: E402
    LANGGRAPH_REASON, _langgraph_ok, agent_profile_spec, stub_profile_spec)
from scripts.test_os37_pty_supervisor import SignalSpy, profile as ladder_profile  # noqa: E402
from scripts.test_os37_pty_supervisor import record as ladder_record  # noqa: E402
from scripts.test_os37_pty_supervisor import snapshot as ladder_snapshot  # noqa: E402
from scripts.test_os37_round7_review_regressions import _cwd  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
PACKAGE = REPO / "scripts" / "deterministic_workflow"


def _wiring_args(base: Path):
    import argparse
    return argparse.Namespace(artifact_base=str(base), results="", adapter="standalone",
                              run_owner="", project_root="", standalone_profile="")


def _recover_cli(base: Path, run_id: str) -> tuple[int, dict, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = launcher.run_watchdog_cli(
            ["recover", "--run-id", run_id, "--artifact-base", str(base),
             "--adapter", "standalone", "--json"])
    lines = [line for line in out.getvalue().strip().split("\n") if line.strip()]
    summary: dict = {}
    if lines:
        with contextlib.suppress(ValueError):
            summary = json.loads(lines[-1])
    return code, summary, out.getvalue() + err.getvalue()


# =====================================================================================
# Item 1 -- the canonical lookup receipt, through the recovery ladder
# =====================================================================================
class Item1CanonicalLookupReceiptTests(_ProductionPath):
    """`lookup()` used to return an extra `source` key (and, from a stored receipt,
    whatever `task_id` / `dispatch_id` was there, `None` included); `executor._recover`
    hands that object to `record_receipt`, whose closed-set validator refused it as a
    CORRUPT ledger -- so the spawn-record-before-receipt window failed
    `RuntimeStateCorrupt` instead of collecting the in-flight effect."""

    class _CrashBeforeReceipt(Exception):
        pass

    def _crash_window(self, ledger, adapter, intent: dict) -> dict:
        """Produce the exact window: the child's spawn record is on disk, the ledger is
        still CLAIMED, because the supervisor died at its `record_receipt` write."""
        claim = ledger.claim(intent)
        real = ledger.record_receipt

        def crash(*_a, **_k):
            raise self._CrashBeforeReceipt("died between the spawn record and the receipt")
        ledger.record_receipt = crash                    # type: ignore[assignment]
        try:
            with self.assertRaises(self._CrashBeforeReceipt):
                adapter.spawn_only(intent, lease_token=claim["lease_token"], payload="work")
        finally:
            ledger.record_receipt = real                 # type: ignore[assignment]
        adapter.runtime.session(intent["intent_id"]).release()
        stored = ledger.get_receipt(intent["intent_id"])
        self.assertEqual(stored["status"], "CLAIMED", "not the pre-receipt window")
        probe = pty_supervisor.read_spawn_records(self.base, intent["run_id"],
                                                  intent["intent_id"])
        self.assertEqual(probe["outcome"], "present", "no spawn record: not the window")
        return {"claim": claim, "stored": stored}

    def test_lookup_from_a_spawn_record_is_exactly_the_closed_receipt_shape(self) -> None:
        profile = replay_profile(stream=STREAMS / "m14_claude_genuine_turn.stream",
                                 exit_code=0, worktree=self.worktree)
        ledger = FileRuntimeStateStore(self.base / "ledger.json")
        adapter, _s, _ = self.compose(profile, run_id="run_r8i1a", ledger=ledger)
        intent = self.intent("intent-r8i1a", run_id="run_r8i1a")
        window = self._crash_window(ledger, adapter, intent)
        successor, _s2, _ = self.compose(profile, run_id="run_r8i1a", ledger=ledger)
        found = successor.lookup(intent)
        self.assertIsNotNone(found)
        self.assertEqual(set(found), set(RECEIPT_KEYS),
                         f"lookup returned keys outside the closed receipt set: {found}")
        for key, value in found.items():
            self.assertIsInstance(value, str, f"{key} is not a string: {value!r}")
            self.assertTrue(value, f"{key} is empty")
        # The identities are the ones the crashed session would have written.
        incarnation = found["external_id"].partition(":")[2]
        self.assertEqual(found["task_id"], runtime_mod.task_identity(intent))
        self.assertEqual(found["dispatch_id"],
                         runtime_mod.dispatch_identity(intent, incarnation))
        # And the closed-set validator ACCEPTS it as an EFFECTED receipt.
        record = {**window["stored"], "status": "EFFECTED", "receipt": dict(found)}
        validate_record(intent["intent_id"], record)     # raises RuntimeStateCorrupt if not

    def test_the_pre_receipt_crash_window_is_collected_not_corrupt(self) -> None:
        """The COMPLETE production path: `executor._recover` -> `lookup` ->
        `record_receipt` -> `_collect` -> `resume` -> settle.  RED at fe7ea84 with
        `RuntimeStateCorrupt: receipt unknown keys ['source']`."""
        profile = replay_profile(stream=STREAMS / "m14_claude_genuine_turn.stream",
                                 exit_code=0, worktree=self.worktree)
        ledger = FileRuntimeStateStore(self.base / "ledger.json")
        adapter, _s, _ = self.compose(profile, run_id="run_r8i1b", ledger=ledger)
        intent = self.intent("intent-r8i1b", run_id="run_r8i1b")
        window = self._crash_window(ledger, adapter, intent)
        spawns_before = self._spawn_count("run_r8i1b", "intent-r8i1b")
        self.assertEqual(spawns_before, 1)
        successor, _s2, _ = self.compose(profile, run_id="run_r8i1b", ledger=ledger)
        try:
            collected = executor._recover(successor, ledger, intent, dict(window["stored"]),
                                          window["claim"]["lease_token"])
        except RuntimeStateCorrupt as exc:
            self.fail(f"the crash window ended in a corrupt ledger instead of a "
                      f"collected effect: {exc}")
        self.assertIsNotNone(collected, "the ladder returned no settlement")
        stored = ledger.get_receipt("intent-r8i1b")
        self.assertEqual(stored["status"], "SETTLED")
        self.assertEqual(set(stored["receipt"]), set(RECEIPT_KEYS))
        self.assertEqual(ledger.get_settlement("intent-r8i1b")["event_id"],
                         collected["event_id"])
        rows = [row for row in journal_mod.ExecutionJournal(self.base, "run_r8i1b")
                .rows_for("intent-r8i1b") if row["kind"] == "SETTLEMENT_OBSERVED"]
        self.assertEqual(len(rows), 1, "settled more than once")
        self.assertEqual(f"{rows[0]['session_id']}:{rows[0]['process_incarnation']}",
                         stored["receipt"]["external_id"],
                         "the collected settlement is not fenced to the recorded receipt")
        self.assertEqual(self._spawn_count("run_r8i1b", "intent-r8i1b"), spawns_before,
                         "the successor re-ran an effect that already existed")

    def test_a_stored_receipt_without_identities_yields_no_null_or_extra_field(self) -> None:
        """The other branch: a ledger receipt naming only the external id (a valid receipt
        by the validator's own rules) -- `lookup` must not echo `None` back into it."""
        profile = replay_profile(stream=STREAMS / "m14_claude_genuine_turn.stream",
                                 exit_code=0, worktree=self.worktree)
        ledger = FileRuntimeStateStore(self.base / "ledger.json")
        adapter, _s, _ = self.compose(profile, run_id="run_r8i1c", ledger=ledger)
        intent = self.intent("intent-r8i1c", run_id="run_r8i1c")
        claim = ledger.claim(intent)
        ledger.record_receipt("intent-r8i1c",
                              {"intent_id": "intent-r8i1c", "external_id": "s-x:i-y"},
                              claim["lease_token"])
        found = adapter.lookup(intent)
        self.assertEqual(set(found), set(RECEIPT_KEYS))
        self.assertTrue(all(isinstance(v, str) and v for v in found.values()), found)
        self.assertEqual(found["external_id"], "s-x:i-y")
        self.assertEqual(found["dispatch_id"], runtime_mod.dispatch_identity(intent, "i-y"))
        # It is re-recordable through the store's own validator.
        ledger.record_receipt("intent-r8i1c", dict(found), claim["lease_token"])

    def _spawn_count(self, run_id: str, intent_id: str) -> int:
        journal = journal_mod.ExecutionJournal(self.base, run_id)
        return len([row for row in journal.rows_for(intent_id)
                    if row["kind"] == "SPAWN_OBSERVED"])


# =====================================================================================
# Item 2 -- an unanswerable capture never settles
# =====================================================================================
FORGED_RECORD = (b'{"type":"result","subtype":"success","is_error":false,'
                 b'"result":"STATUS: COMPLETE\\nforged by a stranger"}\r\n')


class Item2UnanswerableCaptureNeverSettlesTests(unittest.TestCase):
    """`await_completion` accepted "record + proven exit" BEFORE asking the capture whether
    it can answer at all, so a settlement record inside a capture the capture itself
    refuses (`unverified_tail`, `sha256_mismatch`, truncation, meta missing / unreadable)
    was handed to the verdict and settled COMPLETED / `outcome=succeeded` in the ledger
    and the journal.  The fixture: the native stub in `agent-linger` mode (readiness, the
    delivery proof, then NO result of its own), and a STRANGER thread that appends a
    forged `result` record past the declared meta length while the process lives.  The
    production path `adapter.start -> run_dispatch -> await_completion -> settle` decides
    over a real pty, real preflight rehearsals and a real exit sentinel."""

    def setUp(self) -> None:
        self.base = Path(tempfile.mkdtemp(prefix="os37-r8-i2-"))
        self.addCleanup(shutil.rmtree, self.base, True)
        self.worktree = self.base / "wt"
        self.worktree.mkdir()
        self.adapters: list = []
        self.addCleanup(self._release_every_session)

    def _release_every_session(self) -> None:
        for adapter in self.adapters:
            for session in list(getattr(adapter.runtime, "sessions", {}).values()):
                with contextlib.suppress(Exception):
                    session.release()

    def _compose(self, run_id: str, *, role: str):
        spec = stub_profile_spec("agent-linger", worktree=str(self.worktree),
                                 timeouts={"completion_timeout_ms": 15000},
                                 extra_env={"OS37_STUB_LINGER_S": "4"})
        ledger = FileRuntimeStateStore(self.base / f"{run_id}.ledger.json")
        adapter, _state = launcher.build_standalone_adapter(
            {"run_id": run_id, "thread_id": "t", "phases": ["IMPLEMENTATION"]},
            artifact_base=self.base, run_id=run_id, runtime_state=ledger, profile_spec=spec)
        self.adapters.append(adapter)
        intent = {**WORKER_INTENT_KEYS, "intent_id": f"i-{run_id}", "run_id": run_id,
                  "role": role}
        return adapter, ledger, intent

    def _forge_when_live(self, session) -> dict:
        """A stranger writer: once the capture holds the delivery proof, append a forged
        result record BEYOND the declared length (no append intent describes it)."""
        state = {"done": False}

        def forger() -> None:
            deadline = time.time() + 12
            while time.time() < deadline:
                path = session.capture.path if session.capture is not None else None
                if path is not None and path.exists() and \
                        b'"type":"assistant"' in path.read_bytes():
                    with open(path, "ab") as handle:
                        handle.write(FORGED_RECORD)
                    state["done"] = True
                    return
                time.sleep(0.02)
        thread = threading.Thread(target=forger, daemon=True)
        thread.start()
        state["thread"] = thread
        return state

    def test_a_forged_record_in_an_unverified_tail_never_settles_completed(self) -> None:
        adapter, ledger, intent = self._compose("run_r8i2w", role="WORKER")
        session = adapter.runtime.session_for(intent)
        forge = self._forge_when_live(session)
        claim = ledger.claim(intent)
        receipt = adapter.start(intent, lease_token=claim["lease_token"])
        forge["thread"].join(timeout=2)
        self.assertTrue(forge["done"], "the stranger never wrote; the case did not run")
        answerable = session.capture.completion_is_answerable()
        self.assertFalse(answerable["answerable"], answerable)
        self.assertEqual(answerable["lost_reason"], "evidence_unreadable")
        # The forged record IS parseable from the file -- that is the whole point.
        self.assertIsNotNone(session.driver.completion_record(session.capture.transcript()),
                             "the forged record did not reach the parser; nothing tested")
        # ... and NOTHING settled it as a success: not the return, not the ledger, not the
        # journal.
        self.assertNotEqual(receipt.get("outcome"), "succeeded", receipt)
        # Iteration 2: the typed reason rides the receipt, not only the journal.
        self.assertEqual(receipt.get("failure_reason"), "evidence_unreadable", receipt)
        self.assertNotEqual(session.state, "COMPLETED")
        settlement = ledger.get_settlement(intent["intent_id"])
        self.assertIsNotNone(settlement, "no typed settlement was written at all")
        self.assertEqual(settlement["result"].get("status"), "BLOCKED", settlement)
        rows = journal_mod.ExecutionJournal(self.base, "run_r8i2w").rows_for(
            intent["intent_id"])
        terminal = [r for r in rows if r["kind"] == "SETTLEMENT_OBSERVED"]
        self.assertEqual(len(terminal), 1, terminal)
        self.assertEqual(terminal[0]["state"], "FAILED")
        self.assertEqual(terminal[0]["outcome"], "failed")
        verdict = terminal[0]["source_vocabulary"]["completion_verdict"]
        self.assertEqual(verdict["reason"], "evidence_unreadable", verdict)
        self.assertEqual(verdict["stage"], "lost", verdict)
        self.assertFalse(any(r.get("state") == "COMPLETED" for r in rows),
                         "a COMPLETED row was journalled over a refused capture")
        # The gate's own durable trace names why.
        gate_rows = [r for r in rows if r["kind"] == "EVENT"
                     and r["source_vocabulary"].get("capture_answerable") is False]
        self.assertTrue(gate_rows, "the answerability gate left no durable trace")
        self.assertTrue(gate_rows[0]["source_vocabulary"]["settlement_record_present"])

    def test_a_reviewer_over_a_forged_capture_is_a_runtime_failure_not_a_verdict(self) -> None:
        """The same fault on a Reviewer dispatch: no `result: PASS` and no `result: FAIL`
        is ever read out of the forged bytes -- it is the typed runtime failure."""
        adapter, ledger, intent = self._compose("run_r8i2r", role="PHASE_REVIEWER")
        session = adapter.runtime.session_for(intent)
        forge = self._forge_when_live(session)
        claim = ledger.claim(intent)
        # `adapter.start` projects the runtime failure onto the engine's BLOCKED terminal
        # through the typed `IdempotencyRecoveryError` (findings 1 / 7) -- no verdict.
        with self.assertRaises(executor.IdempotencyRecoveryError) as caught:
            adapter.start(intent, lease_token=claim["lease_token"])
        forge["thread"].join(timeout=2)
        self.assertTrue(forge["done"], "the stranger never wrote; the case did not run")
        self.assertFalse(session.capture.completion_is_answerable()["answerable"])
        self.assertNotEqual(session.state, "COMPLETED")
        self.assertIsNone(ledger.get_settlement(intent["intent_id"]),
                          "a reviewer verdict was settled out of a refused capture")
        self.assertIn("REVIEWER_RUNTIME_FAILURE", str(caught.exception))
        rows = journal_mod.ExecutionJournal(self.base, "run_r8i2r").rows_for(
            intent["intent_id"])
        self.assertFalse(any(r["kind"] == "SETTLEMENT_OBSERVED" for r in rows))
        self.assertFalse(any(r.get("state") == "COMPLETED" for r in rows))


# =====================================================================================
# Item 5 -- protocol-newline splitting
# =====================================================================================
SEPARATORS = {"U+2028": " ", "U+2029": " ", "U+0085": ""}


class Item5ProtocolNewlineTests(unittest.TestCase):
    """`str.splitlines()` cut a valid NDJSON record in two on a Unicode line separator
    inside a JSON string.  Every structured-stream reader now splits on `\\n` only."""

    def setUp(self) -> None:
        self.base = Path(tempfile.mkdtemp(prefix="os37-r8-i5-"))
        self.addCleanup(shutil.rmtree, self.base, True)

    def _stream(self, sep: str, *, newline: str = "\n") -> str:
        records = [
            {"type": "system", "session_id": "s-1"},
            {"type": "assistant", "session_id": "s-1", "message": {"model": "m"}},
            {"type": "result", "subtype": "success", "is_error": False,
             "result": f"STATUS: COMPLETE{sep}second line of the body"},
        ]
        return "".join(json.dumps(r, ensure_ascii=False) + newline for r in records)

    def test_the_capture_reader_keeps_a_record_holding_a_separator_whole(self) -> None:
        for name, sep in SEPARATORS.items():
            with self.subTest(separator=name):
                parsed = capture_mod.structured_lines(self._stream(sep))
                self.assertEqual(len(parsed), 3, [raw for _p, raw in parsed])
                self.assertTrue(all(p is not None for p, _raw in parsed))
                self.assertIn(sep, parsed[2][0]["result"])

    def test_crlf_delimits_exactly_like_lf(self) -> None:
        for name, sep in SEPARATORS.items():
            with self.subTest(separator=name):
                lf = capture_mod.structured_lines(self._stream(sep))
                crlf = capture_mod.structured_lines(self._stream(sep, newline="\r\n"))
                self.assertEqual([p for p, _ in lf], [p for p, _ in crlf])
        # And the protocol splitter itself: a trailing delimiter adds no empty piece.
        self.assertEqual(capture_mod.protocol_lines("a\nb\n"), ["a", "b"])
        self.assertEqual(capture_mod.protocol_lines("a\r\nb\r\n"), ["a\r", "b\r"])
        self.assertEqual(capture_mod.protocol_lines("a b\n"), ["a b"])

    def test_the_driver_finds_the_settlement_record_and_its_body(self) -> None:
        driver = drivers.driver_for(_profile_for_driver(self.base))
        for name, sep in SEPARATORS.items():
            with self.subTest(separator=name):
                text = self._stream(sep)
                record = driver.completion_record(text)
                self.assertIsNotNone(record, "the settlement record was split away")
                self.assertEqual(record["type"], "result")
                self.assertTrue(driver.completion_evidence(
                    text, exit_status=0, exit_proven=True)["settlement_record"])

    def test_the_journal_reads_back_a_record_holding_a_separator(self) -> None:
        journal = journal_mod.ExecutionJournal(self.base, "run_r8i5j")
        for name, sep in SEPARATORS.items():
            journal.append(journal_mod.make_record(
                kind="EVENT", derived_from="capture", intent_id=f"i-{name}",
                event="evidence_unreadable", state="RUNNING", session_id="s",
                process_incarnation="i",
                axes={"settlement": "not_settled", "worker_resource": "retain",
                      "process_liveness": "live", "cleanup_authority": "not_authorized"},
                source_vocabulary={"detail": f"the agent wrote{sep}two lines"}))
        rows = journal.rows()                             # raised JournalUnreadable before
        self.assertEqual(len(rows), len(SEPARATORS))
        for row, (name, sep) in zip(rows, SEPARATORS.items()):
            self.assertIn(sep, row["source_vocabulary"]["detail"])
            self.assertEqual(journal_mod.record_digest(row), row["digest"])

    def test_the_audit_log_readers_keep_a_separator_bearing_entry_whole(self) -> None:
        run = "run_r8i5m"
        for name, sep in SEPARATORS.items():
            launcher._append_migration_record(self.base, run, {
                "schema": launcher.STANDALONE_MIGRATION_SCHEMA, "migration_id": name,
                "attempt": 1, "run_id": run, "thread_id": "t", "state": "prepared",
                "reason": f"operator note{sep}continued", "actor": "a"})
        # The migration log is written `ensure_ascii` (escaped) -- the reader must still
        # not depend on that; write one RAW entry the way a foreign tool might.
        launcher._durable_append(launcher.standalone_migration_log_path(self.base, run),
                                 json.dumps({"schema": launcher.STANDALONE_MIGRATION_SCHEMA,
                                             "migration_id": "raw", "attempt": 1,
                                             "run_id": run, "thread_id": "t",
                                             "state": "prepared",
                                             "reason": "raw sep", "actor": "a"},
                                            ensure_ascii=False) + "\n")
        records = launcher.read_standalone_migrations(self.base, run, "t")
        self.assertEqual([r["migration_id"] for r in records], [*SEPARATORS, "raw"])
        launcher._durable_append(launcher._authority_upgrade_log_path(self.base, run),
                                 json.dumps({"schema": launcher.STANDALONE_AUTHORITY_UPGRADE_SCHEMA,
                                             "run_id": run, "reason": "x y"},
                                            ensure_ascii=False) + "\n")
        self.assertEqual(len(launcher.read_authority_upgrades(self.base, run)), 1)

    def test_no_structured_reader_in_the_package_uses_splitlines(self) -> None:
        """Source-level guard over the shipped package: the only `splitlines` left is in
        prose (docstrings / comments) -- no reader calls it."""
        import ast
        offenders = []
        for path in sorted(PACKAGE.glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                        and node.func.attr == "splitlines"):
                    offenders.append(f"{path.name}:{node.lineno}")
        self.assertEqual(offenders, [], offenders)


def _profile_for_driver(base: Path):
    from scripts.deterministic_workflow.standalone_profile import profile_from_mapping
    return profile_from_mapping(stub_profile_spec("agent", worktree=str(base)))


# =====================================================================================
# Item 6 -- G2 re-checks the fenced exit proof
# =====================================================================================
def _free_pid() -> int:
    for pid in range(60000, 90000, 7):
        try:
            os.kill(pid, 0)
        except OSError as exc:
            if exc.errno == errno.ESRCH:
                return pid
    raise AssertionError("no free pid found")


class Item6G2ExitProofTests(unittest.TestCase):
    """Deterministic race over an injected table and clock: present at G1 and at the one
    rung-2 probe, gone (from the tty AND the OS) at the G2 read."""

    def _race(self, *, at_g2):
        pid = _free_pid()
        record = ladder_record(pid=pid, pgid=pid, sid=pid)
        present = ladder_snapshot(rows=({"pid": pid, "ppid": 1, "pgid": pid, "sid": pid,
                                         "tty": "ttys042", "stat": "Ss"},))
        reads = {"n": 0}

        def reader(tty):
            reads["n"] += 1
            return present if reads["n"] <= 2 else at_g2
        ticks = {"t": 0.0}

        def clock() -> float:
            ticks["t"] += 0.011                          # deadline t0+0.020 -> ONE probe
            return ticks["t"]
        spy = SignalSpy()
        result = interrupt_mod.interrupt(
            "intent-1", "stop", record=record,
            profile=ladder_profile(graceful_force_timeout_ms=20),
            table_reader=reader, supervisor_pid=999, killpg=spy.send_group,
            kill=spy.send_one, sleep=lambda s: None, clock=clock)
        return result, spy, reads

    def test_an_exit_between_the_last_probe_and_g2_is_the_proven_exit(self) -> None:
        result, spy, reads = self._race(at_g2=ladder_snapshot(rows=()))
        self.assertEqual(reads["n"], 3, "the race did not land on the G2 read")
        self.assertEqual(result["interrupt_outcome"], "interrupted_confirmed", result)
        self.assertEqual(interrupt_mod.lifecycle_for(result["interrupt_outcome"]),
                         {"state": "INTERRUPTED", "lost_reason": ""})
        self.assertEqual([sig for _p, sig in spy.killpg], [15],
                         "SIGKILL was sent (or SIGTERM was not) around a proven exit")
        self.assertEqual(spy.kill, [])
        g2 = [s for s in result["ladder"] if s["rung"] == "G2"]
        self.assertTrue(g2 and g2[-1]["identity_verified"], result["ladder"])
        self.assertIn("natural exit", g2[-1]["detail"])

    def test_a_live_detached_process_at_g2_is_still_refused_not_proven(self) -> None:
        """Control: the pid EXISTS (this process's own) but is off the captured tty and
        has no start identity to compare -- NOT a proven exit, so the ownership refusal
        after a delivered signal stays `exit_unproven` and no SIGKILL is sent."""
        me = os.getpid()
        record = ladder_record(pid=me, pgid=me, sid=me)
        present = ladder_snapshot(rows=({"pid": me, "ppid": 1, "pgid": me, "sid": me,
                                         "tty": "ttys042", "stat": "Ss"},))
        detached = ladder_snapshot(rows=({"pid": me, "ppid": 1, "pgid": me, "sid": me,
                                          "tty": "ttys777", "stat": "Ss"},))
        reads = {"n": 0}

        def reader(tty):
            reads["n"] += 1
            return present if reads["n"] <= 2 else detached
        ticks = {"t": 0.0}

        def clock() -> float:
            ticks["t"] += 0.011
            return ticks["t"]
        spy = SignalSpy()
        result = interrupt_mod.interrupt(
            "intent-1", "stop", record=record,
            profile=ladder_profile(graceful_force_timeout_ms=20),
            table_reader=reader, supervisor_pid=999, killpg=spy.send_group,
            kill=spy.send_one, sleep=lambda s: None, clock=clock)
        self.assertEqual(result["interrupt_outcome"], "exit_unproven", result)
        self.assertEqual([sig for _p, sig in spy.killpg], [15])
        self.assertFalse(any(s["rung"] == "G2" and s["identity_verified"]
                             for s in result["ladder"]), result["ladder"])


# =====================================================================================
# Item 9 -- the credential seed is 0600 from the first byte
# =====================================================================================
class Item9SeedModeTests(unittest.TestCase):
    """Observed, not described: a stat watcher and a syscall trace record every mode the
    destination (or any temp beside it) carries, under a PERMISSIVE umask.  The source is
    a placeholder -- no credential byte exists in this test."""

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="os37-r8-i9-"))
        self.addCleanup(shutil.rmtree, self.root, True)
        self.source = self.root / "placeholder-seed.json"
        self.source.write_text('{"placeholder": "not a credential"}\n')
        self.home = self.root / "codex_home"
        self.old_umask = os.umask(0o000)
        self.addCleanup(os.umask, self.old_umask)

    def _driver(self):
        from scripts.deterministic_workflow.standalone_profile import profile_from_mapping
        return drivers.driver_for(profile_from_mapping({
            "driver": "codex", "binary": "codex", "supported_range": [[0, 0, 0], [99, 0, 0]],
            "delivery_mode": "launch_with_prompt", "identity_binding": "adopted",
            "readiness_records": [{"channel": "structured", "record_type": "thread.started",
                                   "session_field": "thread_id"}],
            "delivery_proofs": [{"channel": "structured", "record_type": "item.completed"}],
            "completion_records": [{"channel": "structured",
                                    "record_type": "turn.completed"}],
            "auth_seed_source": str(self.source), "auth_seed_dest_name": "auth.json",
            "config_root": str(self.home)}))

    def _observe(self, action):
        """Run `action` under a stat watcher and a trace of the driver's own file
        syscalls; return (result, modes-per-file, trace)."""
        trace: list[str] = []
        real_open, real_copyfile, real_chmod = os.open, shutil.copyfile, os.chmod

        def traced_open(path, flags, mode=0o777, *a, **k):
            if str(path).startswith(str(self.home)):
                trace.append(("open", Path(path).name, flags, mode))
            return real_open(path, flags, mode, *a, **k)

        def traced_copyfile(src, dst, *a, **k):
            trace.append(("copyfile", Path(dst).name))
            return real_copyfile(src, dst, *a, **k)

        def traced_chmod(path, mode, *a, **k):
            if str(path).startswith(str(self.home)):
                trace.append(("chmod", Path(path).name, mode))
            return real_chmod(path, mode, *a, **k)
        observed: dict[str, set[int]] = {}
        stop = threading.Event()

        def watcher() -> None:
            while not stop.is_set():
                try:
                    for entry in os.scandir(self.home):
                        st = entry.stat(follow_symlinks=False)
                        observed.setdefault(entry.name, set()).add(stat.S_IMODE(st.st_mode))
                except FileNotFoundError:
                    pass
                time.sleep(0)
        thread = threading.Thread(target=watcher, daemon=True)
        thread.start()
        os.open, shutil.copyfile, os.chmod = traced_open, traced_copyfile, traced_chmod
        try:
            result = action()
        finally:
            os.open, shutil.copyfile, os.chmod = real_open, real_copyfile, real_chmod
            stop.set()
            thread.join(timeout=2)
        return result, observed, trace

    def test_the_seed_is_never_observed_wider_than_0600_under_a_permissive_umask(self) -> None:
        driver = self._driver()
        result, observed, trace = self._observe(lambda: driver.seed_auth_home(str(self.home)))
        self.assertTrue(result["seeded"], result)
        destination = self.home / "auth.json"
        self.assertEqual(stat.S_IMODE(destination.stat().st_mode), 0o600)
        for name, modes in observed.items():
            self.assertEqual(modes, {0o600}, f"{name} was observed with modes "
                                             f"{sorted(oct(m) for m in modes)}")
        self.assertEqual(destination.read_bytes(), self.source.read_bytes())
        # The driver's own syscalls: created 0600 with O_CREAT|O_EXCL, never copyfile
        # then chmod.
        opens = [t for t in trace if t[0] == "open"]
        self.assertTrue(opens, trace)
        for _kind, _name, flags, mode in opens:
            self.assertEqual(mode, 0o600, trace)
            self.assertTrue(flags & os.O_CREAT and flags & os.O_EXCL, trace)
        self.assertFalse([t for t in trace if t[0] in ("copyfile", "chmod")], trace)
        self.assertEqual(sorted(p.name for p in self.home.iterdir()), ["auth.json"],
                         "a temp file was left beside the seed")
        # No auth bytes ride the result.
        self.assertEqual(set(result), {"seeded", "reason", "path"})
        self.assertNotIn("placeholder", json.dumps(result))

    def test_a_reseed_over_an_existing_destination_stays_0600_and_replaces_it(self) -> None:
        driver = self._driver()
        self.home.mkdir()
        stale = self.home / "auth.json"
        stale.write_text("stale")
        os.chmod(stale, 0o644)
        # A stale temp from a crashed earlier seed must not make O_EXCL refuse the seed.
        # (Created 0600 by the test itself, exactly as a crashed seeder would have left it,
        # so the watcher below observes only the production writer's modes.)
        stale_tmp = os.open(self.home / f"auth.json.{os.getpid()}.seed.tmp",
                            os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.write(stale_tmp, b"crashed")
        os.close(stale_tmp)
        result, observed, _trace = self._observe(lambda: driver.seed_auth_home(str(self.home)))
        self.assertTrue(result["seeded"], result)
        self.assertEqual(stat.S_IMODE(stale.stat().st_mode), 0o600)
        self.assertEqual(stale.read_bytes(), self.source.read_bytes())
        for name, modes in observed.items():
            if name == "auth.json":
                # The pre-existing 0644 file is the ONLY wider observation permitted, and
                # only until the atomic replace; the temp never is.
                self.assertTrue(modes <= {0o644, 0o600}, sorted(oct(m) for m in modes))
            else:
                self.assertEqual(modes, {0o600}, f"{name}: {sorted(oct(m) for m in modes)}")
        self.assertEqual(sorted(p.name for p in self.home.iterdir()), ["auth.json"])

    def test_an_unreadable_source_seeds_nothing_and_leaves_no_temp(self) -> None:
        driver = self._driver()
        self.source.unlink()
        result = driver.seed_auth_home(str(self.home))
        self.assertFalse(result["seeded"])
        self.assertEqual(result["reason"], "auth_seed_unreadable")
        self.assertEqual(sorted(p.name for p in self.home.iterdir()), [])

    def test_the_seeder_names_no_copy_then_chmod(self) -> None:
        import inspect
        source = inspect.getsource(drivers.CodexDriver.seed_auth_home)
        self.assertNotIn("copyfile(", source)
        self.assertNotIn("chmod(", source)
        helper = inspect.getsource(drivers._seed_file_0600)
        self.assertIn("O_EXCL", helper)
        self.assertIn("0o600", helper)
        self.assertIn("os.fsync", helper)
        self.assertIn("os.replace", helper)


# =====================================================================================
# Item 3 -- an OMITTED worktree is frozen to the launch cwd
# =====================================================================================
class Item3OmittedWorktreeFreezeTests(unittest.TestCase):
    """Pure (no graph): the freeze, the digest consequence, the write door and the read
    door for an OMITTED / EMPTY worktree -- the same model iteration 5 applied to a
    RELATIVE one.  RED at fe7ea84: the archive kept `""`, the doors accepted it."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="os37-r8-i3-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.launch = self.tmp / "launch"
        self.recovery = self.tmp / "recovery"
        self.launch.mkdir()
        self.recovery.mkdir()
        self.base = self.tmp / "base"

    def _omitted(self) -> dict:
        spec = agent_profile_spec(worktree="")
        spec.pop("worktree")
        return spec

    def test_an_omitted_or_empty_worktree_freezes_to_the_launch_cwd(self) -> None:
        for spec in (self._omitted(), agent_profile_spec(worktree="")):
            with self.subTest(worktree=spec.get("worktree", "<omitted>")):
                self.assertIn("worktree", launcher.profile_unfrozen_paths(spec))
                frozen = launcher.freeze_profile_worktree(spec, launch_base=self.launch)
                self.assertEqual(frozen["worktree"], str(self.launch))
                self.assertEqual(launcher.profile_unfrozen_paths(frozen), ())
                with _cwd(self.launch):
                    self.assertEqual(launcher.freeze_profile_worktree(spec)["worktree"],
                                     str(self.launch))
        # An absolute worktree is byte-unchanged (digest-stable), as before.
        absolute = agent_profile_spec(worktree=str(self.launch / "wt"))
        self.assertEqual(launcher.freeze_profile_worktree(absolute, launch_base=self.recovery),
                         absolute)

    def test_the_digest_binds_the_launch_cwd(self) -> None:
        spec = self._omitted()
        a = launcher.profile_digest(launcher.freeze_profile_worktree(spec, launch_base=self.launch))
        a_again = launcher.profile_digest(launcher.freeze_profile_worktree(spec, launch_base=self.launch))
        b = launcher.profile_digest(launcher.freeze_profile_worktree(spec, launch_base=self.recovery))
        self.assertEqual(a, a_again)
        self.assertNotEqual(a, b, "two launch cwds froze to one digest")

    def test_the_write_door_refuses_an_omitted_worktree(self) -> None:
        for spec in (self._omitted(), agent_profile_spec(worktree="")):
            with self.subTest(worktree=spec.get("worktree", "<omitted>")), \
                    self.assertRaises(launcher.LauncherError) as caught:
                launcher.persist_standalone_profile(self.base, "run_r8i3w", spec)
            self.assertIn(launcher.STANDALONE_PROFILE_WORKTREE_UNFROZEN, str(caught.exception))
        self.assertFalse(launcher.standalone_profile_path(self.base, "run_r8i3w").exists())

    def test_the_read_door_refuses_a_legacy_empty_archive_by_name(self) -> None:
        """A pre-fix archive holding `""` (and one omitting the key) is refused on the
        digest-bound read AND the current-profile read, bytes untouched, and the refusal
        names the audited migration."""
        run = "run_r8i3r"
        for spec in (agent_profile_spec(worktree=""), self._omitted()):
            digest = launcher.profile_digest(spec)
            archive = launcher.profile_archive_path(self.base, run, digest)
            archive.parent.mkdir(parents=True, exist_ok=True)
            archive.write_text(launcher.profile_payload(spec) + "\n")
            current = launcher.standalone_profile_path(self.base, run)
            current.write_text(launcher.profile_payload(spec) + "\n")
            before = archive.read_bytes()
            with _cwd(self.recovery):
                for call in (lambda: launcher.load_standalone_profile(self.base, run, digest=digest),
                             lambda: launcher.load_standalone_profile(self.base, run)):
                    with self.assertRaises(launcher.LauncherError) as caught:
                        call()
                    message = str(caught.exception)
                    self.assertIn(launcher.STANDALONE_PROFILE_WORKTREE_UNFROZEN, message)
                    self.assertIn("migrate-standalone-profile", message)
            self.assertEqual(archive.read_bytes(), before)


def _launch_omitted_worktree_run(base: Path, launch_cwd: Path, run_id: str, *,
                                 thread_id: str | None = "t"):
    """A REAL launch from ``launch_cwd`` with NO worktree in the profile, stalled before
    its first dispatch.  Returns ``(ledger, raw_spec)``."""
    ledger = FileRuntimeStateStore(base / "ledger.json")
    raw = agent_profile_spec(worktree="")
    raw.pop("worktree")
    spec: dict[str, Any] = {"run_id": run_id, "phases": ["DESIGN"], "max_iterations": 2}
    if thread_id is not None:
        spec["thread_id"] = thread_id
    with _cwd(launch_cwd):
        adapter, state = launcher.build_standalone_adapter(
            spec, artifact_base=base, run_id=run_id, runtime_state=ledger, profile_spec=raw)
        assert adapter.runtime.profile.worktree == str(launch_cwd), adapter.runtime.profile
        stalled = launcher.execute_state(
            state, adapter=adapter, runtime_state=ledger,
            journal=launcher._standalone_pause_row_journal(base, run_id),
            artifact_base=base, interrupt_before=["EXECUTE_INTENT"], audit_sink=None)
    assert stalled.get("pending_intent"), stalled.get("terminal_reason")
    return ledger, raw


@unittest.skipUnless(_langgraph_ok(), LANGGRAPH_REASON)
class Item3CrossCwdOmittedWorktreeTests(unittest.TestCase):
    """Through the production launcher + Graph + watchdog: launch from cwd A with NO
    worktree -> stall -> resume composition, watchdog wiring and the REAL `recover` from
    cwd B all run the next agent in A.  RED at fe7ea84: the archive held `""` and every
    recovery ran the agent in B."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="os37-r8-i3x-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.launch = self.tmp / "launch"
        self.recovery = self.tmp / "recovery"
        self.launch.mkdir()
        self.recovery.mkdir()
        self.base = self.tmp / "base"

    def test_resume_watchdog_and_real_recover_from_another_cwd_run_in_the_launch_cwd(self) -> None:
        run_id = "run_r8i3wd"
        ledger, _raw = _launch_omitted_worktree_run(self.base, self.launch, run_id)
        authority = launcher.load_standalone_authority(self.base, run_id, "t")
        archived = launcher.load_standalone_profile(self.base, run_id,
                                                    digest=authority["profile_digest"])
        self.assertEqual(archived["worktree"], str(self.launch),
                         "the archive does not hold the absolute launch cwd")
        with _cwd(self.recovery):
            adapter, wd_ledger, _j = launcher._watchdog_wiring(
                _wiring_args(self.base)).adapter_for(run_id)
            self.assertEqual(adapter.runtime.profile.worktree, str(self.launch))
            self.assertEqual(adapter.runtime.session_for(
                {"intent_id": "probe", "run_id": run_id}).worktree_path, str(self.launch))
            self.assertEqual(wd_ledger.path.resolve(), ledger.path.resolve())
            resumed, _j2, _p = launcher.standalone_recovery_composition(
                self.base, run_id, thread_id="t", ledger=ledger,
                pause_row_journal=launcher._standalone_pause_row_journal(self.base, run_id))
            self.assertEqual(resumed.runtime.profile.worktree, str(self.launch))
            code, summary, text = _recover_cli(self.base, run_id)
        self.assertEqual(code, 0, f"{summary!r}\n{text}")
        self.assertEqual(summary.get("status"), "RECOVERED", summary)
        rows = launcher._standalone_pause_row_journal(self.base, run_id)
        worktrees = {str(r.get("terminal_worktree") or "") for r in rows.rows().values()}
        self.assertIn(str(self.launch), worktrees, worktrees)
        self.assertNotIn(str(self.recovery), worktrees, worktrees)

    def test_relaunch_from_another_cwd_is_a_conflict_and_from_the_launch_cwd_a_restart(self) -> None:
        run_id = "run_r8i3rel"
        ledger, raw = _launch_omitted_worktree_run(self.base, self.launch, run_id)
        spec = {"run_id": run_id, "thread_id": "t", "phases": ["DESIGN"], "max_iterations": 2}
        with _cwd(self.recovery), self.assertRaises(launcher.LauncherError) as caught:
            launcher.build_standalone_adapter(spec, artifact_base=self.base, run_id=run_id,
                                              runtime_state=ledger, profile_spec=raw)
        self.assertIn(launcher.STANDALONE_AUTHORITY_CONFLICT, str(caught.exception))
        with _cwd(self.launch):
            adapter, _s = launcher.build_standalone_adapter(
                spec, artifact_base=self.base, run_id=run_id, runtime_state=ledger,
                profile_spec=raw)
        self.assertEqual(adapter.runtime.profile.worktree, str(self.launch))

    def test_a_legacy_empty_archive_is_refused_from_another_cwd_then_migrated(self) -> None:
        run_id = "run_r8i3leg"
        ledger, raw = _launch_omitted_worktree_run(self.base, self.launch, run_id)
        # Downgrade the durable binding to the pre-fix shape: the archive holds NO
        # worktree and the authority binds the digest over those raw bytes.
        raw_digest = launcher.profile_digest(raw)
        archive = launcher.profile_archive_path(self.base, run_id, raw_digest)
        archive.write_text(launcher.profile_payload(raw) + "\n")
        target = launcher.standalone_authority_path(self.base, run_id, "t", for_write=True)
        record = json.loads(target.read_text())
        record["profile_digest"] = raw_digest
        target.write_text(json.dumps(record, sort_keys=True, indent=2) + "\n")
        before = archive.read_bytes()
        rows = launcher._standalone_pause_row_journal(self.base, run_id)
        with _cwd(self.recovery):
            with self.assertRaises(launcher.LauncherError) as caught:
                launcher.standalone_recovery_composition(
                    self.base, run_id, thread_id="t", ledger=ledger, pause_row_journal=rows)
            self.assertIn(launcher.STANDALONE_PROFILE_WORKTREE_UNFROZEN, str(caught.exception))
            code, _summary, text = _recover_cli(self.base, run_id)
            self.assertNotEqual(code, 0)
            self.assertIn(launcher.STANDALONE_PROFILE_WORKTREE_UNFROZEN, text)
            self.assertEqual(archive.read_bytes(), before, "the legacy archive was rewritten")
            # The audited remedy names the launch cwd explicitly; recovery then runs in A.
            audit = launcher.migrate_standalone_profile(
                self.base, run_id, thread_id="t",
                new_profile_spec={**raw, "worktree": str(self.launch)},
                actor="operator", reason="round-8 item 3: bind the launch cwd")
            self.assertEqual(audit["old_profile_digest"], raw_digest)
            adapter, _j, _p = launcher.standalone_recovery_composition(
                self.base, run_id, thread_id="t", ledger=ledger, pause_row_journal=rows)
            self.assertEqual(adapter.runtime.profile.worktree, str(self.launch))
            code, summary, text = _recover_cli(self.base, run_id)
        self.assertEqual(code, 0, f"{summary!r}\n{text}")
        self.assertEqual(summary.get("status"), "RECOVERED", summary)
        worktrees = {str(r.get("terminal_worktree") or "") for r in rows.rows().values()}
        self.assertIn(str(self.launch), worktrees, worktrees)
        self.assertNotIn(str(self.recovery), worktrees, worktrees)


# =====================================================================================
# Item 4 -- the legacy upgrade never invents a composition
# =====================================================================================
OBJECTIVE_R8 = "Round-8 objective: make the widget frobnicate durably."


def _replay_legacy_run_without_composition(base: Path, run_id: str):
    """A real omitted-thread run launched WITH an objective (the production composer),
    stalled before its first dispatch, then downgraded to the exact pre-fix durable shape:
    `thread_id: ""`, no composition digest, and NO persisted composition at all."""
    ledger = FileRuntimeStateStore(base / "ledger.json")
    composition = launcher.prompt_composition_record(
        OBJECTIVE_R8, requested_phases=("DESIGN",), risk="high", project_root=base,
        role_instructions={})
    adapter, state = launcher.build_standalone_adapter(
        {"run_id": run_id, "phases": ["DESIGN"], "max_iterations": 2},
        artifact_base=base, run_id=run_id, runtime_state=ledger,
        profile_spec=agent_profile_spec(worktree=str(base / "wt")),
        prompt_composition=composition)
    stalled = launcher.execute_state(
        state, adapter=adapter, runtime_state=ledger,
        journal=launcher._standalone_pause_row_journal(base, run_id),
        artifact_base=base, interrupt_before=["EXECUTE_INTENT"], audit_sink=None)
    assert stalled.get("pending_intent"), stalled.get("terminal_reason")
    target = launcher.standalone_authority_path(base, run_id, "")
    current = json.loads(target.read_text())
    legacy = {k: current[k] for k in ("schema", "run_id", "adapter", "runtime_state_path",
                                      "approval_authority", "profile_digest")}
    legacy["thread_id"] = ""
    target.write_text(json.dumps(legacy, sort_keys=True, indent=2) + "\n")
    launcher.standalone_prompt_composition_path(base, run_id).unlink()
    shutil.rmtree(launcher.standalone_prompt_composition_path(base, run_id).parent
                  / "prompt_compositions")
    return ledger, target, composition


@unittest.skipUnless(_langgraph_ok(), LANGGRAPH_REASON)
class Item4LegacyCompositionNeverInventedTests(unittest.TestCase):
    def setUp(self) -> None:
        self.base = Path(tempfile.mkdtemp(prefix="os37-r8-i4-")).resolve()
        self.addCleanup(shutil.rmtree, self.base, True)
        (self.base / "wt").mkdir()

    def _assert_untouched(self, run_id: str, target: Path, before: bytes) -> None:
        self.assertEqual(target.read_bytes(), before, "the legacy authority was rewritten")
        self.assertEqual(launcher.read_authority_upgrades(self.base, run_id), ())
        self.assertFalse(launcher.standalone_prompt_composition_path(self.base, run_id).exists(),
                         "a composition was INVENTED and persisted")

    def test_resume_and_watchdog_refuse_by_name_and_touch_nothing(self) -> None:
        run_id = "run_r8i4ref"
        ledger, target, _c = _replay_legacy_run_without_composition(self.base, run_id)
        before = target.read_bytes()
        rows = launcher._standalone_pause_row_journal(self.base, run_id)
        # The read path every route shares.
        with self.assertRaises(launcher.LauncherError) as caught:
            launcher.load_standalone_authority(self.base, run_id)
        self.assertIn(launcher.STANDALONE_PROMPT_COMPOSITION_MISSING, str(caught.exception))
        self.assertIn("migrate-standalone-prompt-composition", str(caught.exception))
        self._assert_untouched(run_id, target, before)
        # The resume composition.
        with self.assertRaises(launcher.LauncherError) as caught:
            launcher.standalone_recovery_composition(
                self.base, run_id, thread_id=launcher.DEFAULT_THREAD_ID, ledger=ledger,
                pause_row_journal=rows)
        self.assertIn(launcher.STANDALONE_PROMPT_COMPOSITION_MISSING, str(caught.exception))
        # The real watchdog `recover` verb.
        code, _summary, text = _recover_cli(self.base, run_id)
        self.assertNotEqual(code, 0)
        self.assertIn(launcher.STANDALONE_PROMPT_COMPOSITION_MISSING, text)
        self._assert_untouched(run_id, target, before)
        self.assertEqual(len([r for r in rows.rows().values()]), 0,
                         "a dispatch was made over the refused authority")

    def test_the_audited_migration_supplies_the_composition_and_recovery_delivers_it(self) -> None:
        run_id = "run_r8i4mig"
        ledger, target, composition = _replay_legacy_run_without_composition(self.base, run_id)
        with self.assertRaises(launcher.LauncherError):
            launcher.load_standalone_authority(self.base, run_id)
        # An unattributed migration is refused; a well-formed one upgrades and binds.
        with self.assertRaises(launcher.LauncherError):
            launcher.migrate_standalone_prompt_composition(
                self.base, run_id, composition=composition, actor="", reason="x")
        audit = launcher.migrate_standalone_prompt_composition(
            self.base, run_id, composition=composition, actor="operator",
            reason="round-8 item 4: supply the launch composition")
        self.assertEqual(audit["composition_source"], "audited_migration")
        self.assertEqual(audit["actor"], "operator")
        self.assertEqual(audit["bound_prompt_composer"], launcher.PROMPT_COMPOSER_PRODUCTION)
        record = launcher.load_standalone_authority(self.base, run_id)
        self.assertEqual(record["thread_id"], launcher.DEFAULT_THREAD_ID)
        self.assertEqual(record["prompt_composition_digest"],
                         launcher.prompt_composition_digest(composition))
        self.assertEqual(len(launcher.read_authority_upgrades(self.base, run_id)), 1)
        # Idempotent replay: nothing appended, the same audit row returned.
        again = launcher.migrate_standalone_prompt_composition(
            self.base, run_id, composition=composition, actor="operator",
            reason="round-8 item 4: supply the launch composition")
        self.assertEqual(again["bound_prompt_composition_digest"],
                         audit["bound_prompt_composition_digest"])
        self.assertEqual(len(launcher.read_authority_upgrades(self.base, run_id)), 1)
        # Recovery now rebuilds the PRODUCTION composer and the real recovery delivers
        # the objective -- verified against the runtime's own delivery digest.
        adapter, _j, _p = launcher.standalone_recovery_composition(
            self.base, run_id, thread_id=launcher.DEFAULT_THREAD_ID, ledger=ledger,
            pause_row_journal=launcher._standalone_pause_row_journal(self.base, run_id))
        composer = getattr(adapter, "_prompt_composer", None)
        self.assertIsNotNone(composer, "recovery rebuilt no production composer")
        rendered = composer({"intent_id": "probe", "run_id": run_id, "role": "WORKER",
                             "phase": "DESIGN", "gate_iteration": 1,
                             "round_kind": "PHASE_GATE", "repair_instruction": None})
        self.assertIn(OBJECTIVE_R8, rendered)
        code, summary, text = _recover_cli(self.base, run_id)
        self.assertEqual(code, 0, f"{summary!r}\n{text}")
        self.assertEqual(summary.get("status"), "RECOVERED", summary)
        journal = journal_mod.ExecutionJournal(self.base, run_id)
        digests = {(r.get("source_vocabulary") or {}).get("prompt_digest")
                   for r in journal.rows() if r["kind"] == "DELIVERY_INTENT"}
        self.assertTrue(digests, "no dispatch was delivered after the migration")
        self.assertNotIn(drivers.prompt_digest(json.dumps({"intent_id": "probe"})), digests)
        # Every delivered prompt hashes to a rendering that carries the objective, never
        # to the canonical intent JSON.
        from scripts.deterministic_workflow.standalone_runtime import _canonical
        for row in journal.rows():
            if row["kind"] != "DELIVERY_INTENT":
                continue
            intent_id = row["intent_id"]
            self.assertNotEqual(row["source_vocabulary"]["prompt_digest"],
                                drivers.prompt_digest(_canonical({"intent_id": intent_id})),
                                "raw ActionIntent JSON was delivered")

    def test_the_cli_composer_none_declaration_is_attributed(self) -> None:
        run_id = "run_r8i4none"
        _ledger, _target, _c = _replay_legacy_run_without_composition(self.base, run_id)
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            # Neither an objective nor the declaration: refused.
            refused = launcher.run_migrate_cli(
                ["migrate-standalone-prompt-composition", "--run-id", run_id,
                 "--artifact-base", str(self.base), "--actor-id", "op", "--reason", "r"])
            code = launcher.run_migrate_cli(
                ["migrate-standalone-prompt-composition", "--run-id", run_id,
                 "--artifact-base", str(self.base), "--composer-none",
                 "--actor-id", "op", "--reason", "the launch delivered the intent", "--json"])
        self.assertNotEqual(refused, 0)
        self.assertIn(launcher.STANDALONE_MIGRATION_REFUSED, err.getvalue())
        self.assertEqual(code, 0, err.getvalue())
        audit = json.loads(out.getvalue().strip().split("\n")[-1])
        self.assertEqual(audit["bound_prompt_composer"], launcher.PROMPT_COMPOSER_NONE)
        self.assertEqual(audit["actor"], "op")
        self.assertEqual(audit["composition_source"], "audited_migration")
        record = launcher.load_standalone_authority(self.base, run_id)
        self.assertEqual(record["prompt_composition_digest"],
                         launcher.prompt_composition_digest(
                             launcher.prompt_composition_record(None)))

    def test_a_non_legacy_run_is_never_rebound_here(self) -> None:
        run_id = "run_r8i4nonleg"
        ledger = FileRuntimeStateStore(self.base / "ledger.json")
        adapter, _state = launcher.build_standalone_adapter(
            {"run_id": run_id, "thread_id": "t", "phases": ["DESIGN"]},
            artifact_base=self.base, run_id=run_id, runtime_state=ledger,
            profile_spec=agent_profile_spec(worktree=str(self.base / "wt")))
        adapter.publish_launch_bindings()
        with self.assertRaises(launcher.LauncherError) as caught:
            launcher.migrate_standalone_prompt_composition(
                self.base, run_id, composition=launcher.prompt_composition_record(None),
                actor="op", reason="r")
        self.assertIn(launcher.STANDALONE_MIGRATION_REFUSED, str(caught.exception))
        self.assertEqual(launcher.read_authority_upgrades(self.base, run_id), ())


# =====================================================================================
# Items 7 / 8 -- migration attempt identity and validated reconciliation
# =====================================================================================
class _MigrationCrash(Exception):
    pass


class _MigrationBase(unittest.TestCase):
    def setUp(self) -> None:
        self.base = Path(tempfile.mkdtemp(prefix="os37-r8-mig-")).resolve()
        self.addCleanup(shutil.rmtree, self.base, True)
        (self.base / "wt-a").mkdir()
        (self.base / "wt-b").mkdir()
        self.spec_a = stub_profile_spec("alive", worktree=str(self.base / "wt-a"))
        self.spec_b = stub_profile_spec("alive", worktree=str(self.base / "wt-b"))
        self.digest_a = launcher.profile_digest(self.spec_a)
        self.digest_b = launcher.profile_digest(self.spec_b)
        self._real_append = launcher._durable_append
        self._real_write = launcher._durable_write
        self.addCleanup(self._restore)

    def _restore(self) -> None:
        launcher._durable_append = self._real_append      # type: ignore[assignment]
        launcher._durable_write = self._real_write        # type: ignore[assignment]

    def _launch(self, run: str) -> None:
        launcher.publish_standalone_launch_bindings(
            self.base, run, profile_spec=self.spec_a,
            runtime_state_path=(self.base / f"{run}.ledger.json").resolve(), thread_id="t")

    def _migrate(self, run: str, spec=None) -> dict:
        return launcher.migrate_standalone_profile(
            self.base, run, thread_id="t", new_profile_spec=spec or self.spec_b,
            actor="alice", reason="retune")

    def _crash_after_prepared(self, run: str) -> None:
        def crash(path, text):
            self._real_append(path, text)
            if '"state": "prepared"' in text:
                raise _MigrationCrash("after the prepared append")
        launcher._durable_append = crash                  # type: ignore[assignment]
        try:
            with self.assertRaises(_MigrationCrash):
                self._migrate(run)
        finally:
            self._restore()

    def _crash_after_rebind(self, run: str) -> None:
        authority = launcher.standalone_authority_path(self.base, run, "t")

        def crash(path, text):
            self._real_write(path, text)
            if Path(path) == authority:
                raise _MigrationCrash("after the authority re-bind")
        launcher._durable_write = crash                   # type: ignore[assignment]
        try:
            with self.assertRaises(_MigrationCrash):
                self._migrate(run)
        finally:
            self._restore()

    def _log(self, run: str) -> list[tuple[str, int, str]]:
        return [(r["migration_id"], int(r.get("attempt") or 0), r["state"])
                for r in launcher.read_standalone_migrations(self.base, run, "t")]

    def _authority_digest(self, run: str) -> str:
        return launcher.load_standalone_authority(self.base, run, "t")["profile_digest"]

    def assert_linear_per_attempt(self, run: str) -> None:
        """The per-attempt invariant: every (id, attempt) has exactly one `prepared` and
        at most one terminal record, and every id has at most one `committed`."""
        # Grouped by the production reader, so an attempt-less legacy record is placed
        # positionally exactly as reconciliation places it.
        grouped = launcher._migration_attempt_states(self.base, run, "t")
        records = launcher.read_standalone_migrations(self.base, run, "t")
        self.assertEqual(sum(len(v) for v in grouped.values()), len(records),
                         "two records of one attempt share a state (a duplicate terminal)")
        for key, by_state in grouped.items():
            self.assertIn(launcher.MIGRATION_PREPARED, by_state, (key, sorted(by_state)))
            terminal = [s for s in by_state if s != launcher.MIGRATION_PREPARED]
            self.assertLessEqual(len(terminal), 1, (key, sorted(by_state)))
        committed_ids = [mid for mid, _a, state in self._log(run)
                         if state == launcher.MIGRATION_COMMITTED]
        self.assertEqual(len(committed_ids), len(set(committed_ids)), committed_ids)


class Item7MigrationAttemptIdentityTests(_MigrationBase):
    def test_rollback_retry_crash_after_rebind_reconciles_to_one_committed_record(self) -> None:
        run = "run_r8i7"
        self._launch(run)
        self._crash_after_prepared(run)
        launcher.load_standalone_authority(self.base, run, "t")       # reconciles: rolled back
        self.assertEqual([s for _m, _a, s in self._log(run)], ["prepared", "rolled_back"])
        self.assertEqual(self._authority_digest(run), self.digest_a)
        self._crash_after_rebind(run)                                  # the retry
        # The production read reconciles the RETRY on its own attempt state.
        self.assertEqual(self._authority_digest(run), self.digest_b)
        committed = launcher.standalone_committed_migrations(self.base, run, "t")
        self.assertEqual(len(committed), 1,
                         "the live authority has no committed record (the retry aliased "
                         "the rolled-back attempt)")
        self.assertEqual(committed[0]["new_profile_digest"], self.digest_b)
        self.assertEqual(committed[0]["attempt"], 2)
        self.assertEqual([(a, s) for _m, a, s in self._log(run)],
                         [(1, "prepared"), (1, "rolled_back"), (2, "prepared"), (2, "committed")])
        self.assertEqual(len({m for m, _a, _s in self._log(run)}), 1, "one operation id")
        self.assert_linear_per_attempt(run)
        # A replay of the identical operation is idempotent (the replay gate).
        replay = self._migrate(run)
        self.assertEqual(replay["state"], launcher.MIGRATION_COMMITTED)
        self.assertEqual(replay["attempt"], 2)
        self.assertEqual(len(launcher.standalone_committed_migrations(self.base, run, "t")), 1)
        self.assert_linear_per_attempt(run)

    def test_a_legacy_attempt_less_alias_history_reconciles_per_attempt(self) -> None:
        """A log written by the PRE-fix model (no `attempt` field) holding exactly the
        alias shape -- prepared, rolled_back, prepared -- with the authority re-bound:
        positional attempt assignment reconciles the second prepared as attempt 2."""
        run = "run_r8i7leg"
        self._launch(run)
        mid = launcher._migration_id(run, "t", self.digest_a, self.digest_b, "alice",
                                     "retune", 0)
        base_record = {"schema": launcher.STANDALONE_MIGRATION_SCHEMA, "migration_id": mid,
                       "run_id": run, "thread_id": "t", "operation_epoch": 0,
                       "old_profile_digest": self.digest_a,
                       "new_profile_digest": self.digest_b, "actor": "alice",
                       "reason": "retune"}
        for state in ("prepared", "rolled_back", "prepared"):
            launcher._append_migration_record(self.base, run, {**base_record, "state": state})
        launcher.persist_standalone_profile(self.base, run, self.spec_b)
        target = launcher.standalone_authority_path(self.base, run, "t", for_write=True)
        record = json.loads(target.read_text())
        record["profile_digest"] = self.digest_b
        target.write_text(json.dumps(record, sort_keys=True, indent=2) + "\n")
        self.assertEqual(self._authority_digest(run), self.digest_b)
        committed = launcher.standalone_committed_migrations(self.base, run, "t")
        self.assertEqual(len(committed), 1)
        self.assertEqual(committed[0]["attempt"], 2)
        self.assert_linear_per_attempt(run)

    def test_a_crash_at_every_boundary_keeps_the_per_attempt_history_linear(self) -> None:
        for crash_after in range(1, 6):
            with self.subTest(crash_after=crash_after):
                run = f"run_r8i7c{crash_after}"
                self._launch(run)
                n = {"i": 0}

                def wrap(fn):
                    def inner(*a, **k):
                        n["i"] += 1
                        out = fn(*a, **k)
                        if n["i"] == crash_after:
                            raise _MigrationCrash("boom")
                        return out
                    return inner
                launcher._durable_append = wrap(self._real_append)   # type: ignore[assignment]
                launcher._durable_write = wrap(self._real_write)     # type: ignore[assignment]
                try:
                    with self.assertRaises(_MigrationCrash):
                        self._migrate(run)
                finally:
                    self._restore()
                recovered = self._authority_digest(run)
                committed = launcher.standalone_committed_migrations(self.base, run, "t")
                if recovered == self.digest_b:
                    self.assertEqual(len(committed), 1)
                else:
                    self.assertEqual(recovered, self.digest_a)
                    self.assertEqual(committed, ())
                self.assert_linear_per_attempt(run)
                self._migrate(run)                                   # retry / replay
                self.assertEqual(self._authority_digest(run), self.digest_b)
                self.assertEqual(
                    len(launcher.standalone_committed_migrations(self.base, run, "t")), 1)
                self.assert_linear_per_attempt(run)


class Item8ValidatedReconciliationTests(_MigrationBase):
    def _open_prepared(self, run: str, *, old: str, new: str, mid: str = "deadbeef00000001") -> None:
        launcher._append_migration_record(self.base, run, {
            "schema": launcher.STANDALONE_MIGRATION_SCHEMA, "migration_id": mid, "attempt": 1,
            "run_id": run, "thread_id": "t", "operation_epoch": 0,
            "old_profile_digest": old, "new_profile_digest": new, "actor": "alice",
            "reason": "retune", "state": launcher.MIGRATION_PREPARED,
            "prepared_at": "2026-01-01T00:00:00Z"})

    def test_a_corrupt_target_archive_is_never_committed(self) -> None:
        run = "run_r8i8cor"
        self._launch(run)
        self._crash_after_rebind(run)
        archive = launcher.profile_archive_path(self.base, run, self.digest_b)
        archive.write_text('{"driver": "claude", "binary": 42, "not": "a profile"}\n')
        with self.assertRaises(launcher.LauncherError) as caught:
            launcher.load_standalone_authority(self.base, run, "t")
        self.assertIn(launcher.STANDALONE_ADAPTER_REQUIRES_PROFILE, str(caught.exception))
        self.assertEqual([s for _m, _a, s in self._log(run)], ["prepared"],
                         "reconciliation appended a terminal record over a corrupt archive")
        self.assertEqual(launcher.standalone_committed_migrations(self.base, run, "t"), ())
        # Repairing the archive (the operator restores the profile bytes) lets the SAME
        # read roll the attempt forward -- over VALID state.
        archive.write_text(launcher.profile_payload(self.spec_b) + "\n")
        self.assertEqual(self._authority_digest(run), self.digest_b)
        self.assertEqual([s for _m, _a, s in self._log(run)], ["prepared", "committed"])
        self.assert_linear_per_attempt(run)

    def test_a_missing_target_archive_is_never_committed(self) -> None:
        run = "run_r8i8mis"
        self._launch(run)
        self._crash_after_rebind(run)
        launcher.profile_archive_path(self.base, run, self.digest_b).unlink()
        with self.assertRaises(launcher.LauncherError) as caught:
            launcher.load_standalone_authority(self.base, run, "t")
        self.assertIn(launcher.STANDALONE_ADAPTER_REQUIRES_PROFILE, str(caught.exception))
        self.assertEqual([s for _m, _a, s in self._log(run)], ["prepared"])

    def test_an_unreadable_authority_is_a_typed_refusal_not_a_rollback(self) -> None:
        run = "run_r8i8unr"
        self._launch(run)
        self._open_prepared(run, old=self.digest_a, new=self.digest_b)
        authority = launcher.standalone_authority_path(self.base, run, "t")
        authority.write_text('{"schema": "os37.standalone_authority.v1", "run_id": "ru')
        with self.assertRaises(launcher.LauncherError) as caught:
            launcher.load_standalone_authority(self.base, run, "t")
        self.assertIn(launcher.STANDALONE_ADAPTER_REQUIRES_LEDGER, str(caught.exception))
        self.assertEqual([s for _m, _a, s in self._log(run)], ["prepared"],
                         "an unreadable authority was collapsed into a rollback")

    def test_rollback_needs_positive_proof_of_the_source_digest(self) -> None:
        run = "run_r8i8prf"
        self._launch(run)
        # (a) the authority still names the SOURCE digest: positive proof -> rolled back.
        self._open_prepared(run, old=self.digest_a, new=self.digest_b)
        self.assertEqual(self._authority_digest(run), self.digest_a)
        self.assertEqual([s for _m, _a, s in self._log(run)], ["prepared", "rolled_back"])
        # (b) an attempt whose SOURCE is not what the authority names (a third digest):
        # neither proven applied nor proven unapplied -> the typed refusal, nothing
        # appended.
        self._open_prepared(run, old="0000000000000000", new=self.digest_b,
                            mid="deadbeef00000002")
        with self.assertRaises(launcher.LauncherError) as caught:
            launcher.load_standalone_authority(self.base, run, "t")
        self.assertIn(launcher.STANDALONE_MIGRATION_UNRECONCILABLE, str(caught.exception))
        self.assertEqual([s for _m, _a, s in self._log(run)],
                         ["prepared", "rolled_back", "prepared"])

    def test_the_reconciler_names_the_production_loader(self) -> None:
        import inspect
        source = inspect.getsource(launcher._reconcile_standalone_migrations_locked)
        self.assertIn("_validated_profile_archive", source)
        self.assertNotIn(".exists()", source.split("_validated_profile_archive")[-1])
        helper = inspect.getsource(launcher._validated_profile_archive)
        self.assertIn("load_standalone_profile(", helper)
        self.assertIn("profile_from_mapping(", helper)


# =====================================================================================
# Iteration 2 -- a final record visible only AFTER the proven exit (the F06 CI ordering)
# =====================================================================================
def _late_final_record_agent(delay_s: str, *, emit_final: bool = True) -> str:
    """The F06 agent with ITS LAST RECORD delayed past its own proven exit: it writes the
    runtime's fenced exit sentinel (path + fence handed to it by the test through
    `./.late-sentinel` in its cwd, the worktree), waits, and only then writes its `-o`
    body and the final `turn.completed` record.  A late WRITER cannot model this on darwin
    (the slave is revoked when the session leader exits), so the ordering is injected
    through the runtime's own evidence channel instead -- the observable is identical to
    the loaded Linux runner's: a fenced proven exit while the final bytes are still in
    flight, then the pty hangup."""
    from scripts.test_os37_recovery_boundary_regressions import F06ResultPathIsAbsoluteTests
    import textwrap
    original_tail = textwrap.dedent('''\
        if [ -n "$OUT" ]; then
          mkdir -p "$(dirname "$OUT")" 2>/dev/null
          printf 'F6 BODY written by the agent\\nSTATUS: COMPLETE\\n' > "$OUT"
        fi
        printf '{"type":"turn.completed","usage":{"input_tokens":1}}\\n'
        exit 0
    ''')
    final = ('''printf '{"type":"turn.completed","usage":{"input_tokens":1}}\\n'\n'''
             if emit_final else "")
    late_tail = textwrap.dedent(f'''\
        if [ -f ./.late-sentinel ]; then
          SENTINEL="$(sed -n 1p ./.late-sentinel)"; FENCE="$(sed -n 2p ./.late-sentinel)"
          mkdir -p "$(dirname "$SENTINEL")" 2>/dev/null
          printf '0\\t%s\\n' "$FENCE" > "$SENTINEL.tmp" && mv "$SENTINEL.tmp" "$SENTINEL"
        fi
        sleep {delay_s}
        if [ -n "$OUT" ]; then
          mkdir -p "$(dirname "$OUT")" 2>/dev/null
          printf 'F6 BODY written by the agent\\nSTATUS: COMPLETE\\n' > "$OUT"
        fi
        {final}exit 0
    ''')
    agent = F06ResultPathIsAbsoluteTests.AGENT.replace(original_tail, late_tail)
    assert agent != F06ResultPathIsAbsoluteTests.AGENT, "the late-record substitution did not apply"
    return agent


class _F06LateBase(unittest.TestCase):
    """The exact F06 composition (native `-o` agent, cwd outside the worktree, RELATIVE
    artifact base) with the sentinel-first agent fixtures; no tests of its own."""

    def setUp(self) -> None:
        from scripts.test_os37_recovery_boundary_regressions import compile_native_agent
        self.base = Path(tempfile.mkdtemp(prefix="os37-r8-i2f06-")).resolve()
        self.addCleanup(shutil.rmtree, self.base, True)
        self.worktree = self.base / "worktree"
        self.worktree.mkdir()
        self._compile = compile_native_agent
        self.adapters: list = []
        self.addCleanup(self._release_every_session)
        previous = os.getcwd()
        launcher_cwd = self.base / "launcher-cwd"
        launcher_cwd.mkdir()
        os.chdir(launcher_cwd)                           # cwd != worktree, as in F06
        self.addCleanup(os.chdir, previous)

    def _release_every_session(self) -> None:
        for adapter in self.adapters:
            for session in list(getattr(adapter.runtime, "sessions", {}).values()):
                with contextlib.suppress(Exception):
                    session.release()

    def _dispatch(self, run_id: str, agent_script: str):
        from scripts.test_os37_recovery_boundary_regressions import F06ResultPathIsAbsoluteTests
        from scripts.deterministic_workflow.runtime_state import InMemoryRuntimeStateStore
        bin_dir = self._compile(self.base, f"f6-{run_id}", agent_script)

        class _Shape:
            worktree = str(self.worktree)
        spec = F06ResultPathIsAbsoluteTests._spec(_Shape(), bin_dir)   # the F06 profile
        spec["binary"] = f"f6-{run_id}"
        relative = Path("rel-artifacts")
        ledger = InMemoryRuntimeStateStore()
        adapter, _state = launcher.build_standalone_adapter(
            {"run_id": run_id, "thread_id": "t", "phases": ["IMPLEMENTATION"]},
            artifact_base=relative, run_id=run_id, runtime_state=ledger, profile_spec=spec)
        self.adapters.append(adapter)
        intent = {**WORKER_INTENT_KEYS, "intent_id": f"i-{run_id}", "run_id": run_id,
                  "role": "WORKER"}
        session = adapter.runtime.session_for(intent)
        sentinel = pty_supervisor.exit_sentinel_path(session.artifact_base, run_id,
                                                     session.session_id, session.incarnation)
        (self.worktree / ".late-sentinel").write_text(f"{sentinel}\n{session.fence}\n")
        claim = ledger.claim(intent)
        receipt = adapter.start(intent, lease_token=claim["lease_token"])
        rows = journal_mod.ExecutionJournal(relative, run_id).rows_for(intent["intent_id"])
        settled = [r for r in rows if r["kind"] == "SETTLEMENT_OBSERVED"]
        return receipt, settled, session

class Iteration2LateFinalRecordTests(_F06LateBase):
    """RED at 6908ea9: `await_completion` settled on "a candidate record AND a proven exit"
    without reading the stream to its hangup, so the final `turn.completed` that reached
    the master after the sentinel was never read and the F06 dispatch settled
    `FAILED / completion_record_undeclared` over its penultimate record -- with a receipt
    that reported `failure_reason=''`."""

    def test_a_final_record_visible_only_after_the_proven_exit_is_read_to_the_hangup(self) -> None:
        receipt, settled, session = self._dispatch("run_r8i2late",
                                                   _late_final_record_agent("0.3"))
        self.assertEqual(receipt["outcome"], "succeeded", receipt)
        self.assertEqual(receipt["failure_reason"], "")
        self.assertEqual(len(settled), 1, settled)
        self.assertEqual(settled[0]["state"], "COMPLETED")
        vocab = settled[0]["source_vocabulary"]
        self.assertEqual(vocab["result_body_source"], "output_last_message_path",
                         "the body written by the agent was not read back")
        drain = vocab["post_exit_drain"]
        self.assertEqual(drain["ended"], "hangup", drain)
        self.assertGreater(drain["bytes"], 0, "the post-exit drain read nothing; the "
                                              "final record was settled without")
        self.assertIn('"turn.completed"', session.capture.transcript())
        self.assertTrue(session.capture.completion_is_answerable()["answerable"])

    def test_a_failed_receipt_carries_its_typed_reason(self) -> None:
        # The SAME ordering, but the agent never writes its final record: the verdict is
        # `completion_record_undeclared` over `item.completed`, and the RECEIPT says so.
        receipt, settled, _session = self._dispatch(
            "run_r8i2nofinal", _late_final_record_agent("0.1", emit_final=False))
        self.assertEqual(receipt["outcome"], "failed", receipt)
        self.assertEqual(receipt["failure_reason"], "completion_record_undeclared", receipt)
        self.assertEqual(len(settled), 1)
        self.assertEqual(settled[0]["state"], "FAILED")
        self.assertEqual(settled[0]["source_vocabulary"]["completion_verdict"]["reason"],
                         receipt["failure_reason"])
        self.assertEqual(settled[0]["source_vocabulary"]["post_exit_drain"]["ended"], "hangup")


class Iteration2DrainAfterExitTests(unittest.TestCase):
    """`drain_after_exit` over a real pty pair: a quiet `select` does NOT end it, the
    hangup does, and a slave that never hangs up ends it by the BUDGET -- reported as such."""

    def setUp(self) -> None:
        import pty
        from scripts.deterministic_workflow.standalone_profile import profile_from_mapping
        from scripts.deterministic_workflow.standalone_runtime import StandaloneSession
        self.base = Path(tempfile.mkdtemp(prefix="os37-r8-i2drain-"))
        self.addCleanup(shutil.rmtree, self.base, True)
        self.master, self.slave = pty.openpty()
        self.addCleanup(self._close_fds)
        profile = profile_from_mapping(stub_profile_spec("alive", worktree=str(self.base)))
        self.session = StandaloneSession(
            intent={"intent_id": "i-drain", "run_id": "run_drain", "role": "WORKER"},
            profile=profile, artifact_base=self.base, run_id="run_drain",
            journal=journal_mod.ExecutionJournal(self.base, "run_drain"))
        self.session.capture = capture_mod.BoundedCapture(self.base / "capture.log")
        self.session.pty = {"master_fd": self.master, "pty_id": "pty-drain"}

    def _close_fds(self) -> None:
        for fd in (self.master, self.slave):
            with contextlib.suppress(OSError):
                os.close(fd)

    def test_silence_does_not_end_the_drain_but_the_hangup_does(self) -> None:
        def writer() -> None:
            time.sleep(0.25)                             # > the old 10 ms silence window
            os.write(self.slave, b'{"type":"turn.completed"}\n')
            time.sleep(0.05)
            os.close(self.slave)                         # the hangup
        thread = threading.Thread(target=writer, daemon=True)
        thread.start()
        drained = self.session.drain_after_exit(budget_ms=3000)
        thread.join(timeout=2)
        self.assertEqual(drained["ended"], "hangup", drained)
        self.assertGreater(drained["bytes"], 0)
        self.assertIn('"turn.completed"', self.session.capture.transcript())
        # And the old shape, for contrast: `pump` returns on the first quiet select.
        os.close(self.master)

    def test_a_slave_that_never_hangs_up_ends_the_drain_by_budget(self) -> None:
        os.write(self.slave, b'{"type":"item.completed"}\n')
        started = time.monotonic()
        drained = self.session.drain_after_exit(budget_ms=300)
        elapsed = time.monotonic() - started
        self.assertEqual(drained["ended"], "budget", drained)
        self.assertGreater(drained["bytes"], 0)
        self.assertGreaterEqual(elapsed, 0.25)
        self.assertLess(elapsed, 2.0, "the bound was not honoured")


class Iteration2WatcherAppendIntentTests(unittest.TestCase):
    """The exit watcher's appender wrote bytes and THEN its meta with no append intent, so
    a stranger reading between the two saw an `unverified_tail` -- the same integrity
    verdict a forged record gets -- for bytes that were merely not yet fully visible.  Both
    writers now record the intent BEFORE the bytes; the in-flight suffix is `verified`."""

    def setUp(self) -> None:
        self.base = Path(tempfile.mkdtemp(prefix="os37-r8-i2int-"))
        self.addCleanup(shutil.rmtree, self.base, True)
        self.path = self.base / "capture.log"
        # A supervisor-written prefix with its meta, exactly as the watcher inherits it.
        supervisor = capture_mod.BoundedCapture(self.path)
        supervisor.append(b'{"type":"system","session_id":"s"}\n', at="t0")
        self.reader = capture_mod.BoundedCapture(self.path)   # a stranger's view

    def test_a_reader_between_the_bytes_and_the_meta_sees_a_verified_tail(self) -> None:
        from scripts.deterministic_workflow.standalone_profile import CaptureLimits
        appender = capture_mod.RawBoundedAppender(os.fsencode(str(self.path)),
                                                  limits=CaptureLimits())
        observed: dict[str, Any] = {}
        real_save = appender.save_meta

        def observe_then_save() -> None:
            # The window: bytes on disk, meta not yet rewritten.
            self.reader.refresh()
            observed["integrity"] = self.reader.integrity()
            observed["answerable"] = self.reader.completion_is_answerable()
            real_save()
        appender.save_meta = observe_then_save              # type: ignore[assignment]
        appender.append(b'{"type":"result","subtype":"success"}\n')
        appender.close()
        self.assertTrue(observed, "the window was never observed")
        self.assertTrue(observed["integrity"]["consistent"], observed)
        self.assertTrue(observed["answerable"]["answerable"], observed)
        # After the meta lands the same reader agrees, and a FORGED suffix (no intent)
        # is still refused -- the gate distinguishes in-flight from forged.
        self.reader.refresh()
        self.assertTrue(self.reader.integrity()["consistent"])
        with open(self.path, "ab") as handle:
            handle.write(b'{"type":"result","subtype":"success","forged":true}\n')
        self.reader.refresh()
        integrity = self.reader.integrity()
        self.assertFalse(integrity["consistent"])
        self.assertEqual(integrity.get("tail"), capture_mod.INTEGRITY_UNVERIFIED_TAIL, integrity)


# =====================================================================================
# Iteration 3 -- read-error classification and POSITIVE stream finality
# =====================================================================================
def _sentinel_first_agent(*, after_sentinel: str) -> str:
    """The F06 agent with the runtime's fenced exit sentinel written FIRST (path + fence
    handed over through `./.late-sentinel`), followed by ``after_sentinel`` (shell)."""
    from scripts.test_os37_recovery_boundary_regressions import F06ResultPathIsAbsoluteTests
    import textwrap
    original_tail = textwrap.dedent('''\
        if [ -n "$OUT" ]; then
          mkdir -p "$(dirname "$OUT")" 2>/dev/null
          printf 'F6 BODY written by the agent\\nSTATUS: COMPLETE\\n' > "$OUT"
        fi
        printf '{"type":"turn.completed","usage":{"input_tokens":1}}\\n'
        exit 0
    ''')
    late_tail = textwrap.dedent('''\
        if [ -f ./.late-sentinel ]; then
          SENTINEL="$(sed -n 1p ./.late-sentinel)"; FENCE="$(sed -n 2p ./.late-sentinel)"
          mkdir -p "$(dirname "$SENTINEL")" 2>/dev/null
          printf '0\\t%s\\n' "$FENCE" > "$SENTINEL.tmp" && mv "$SENTINEL.tmp" "$SENTINEL"
        fi
    ''') + textwrap.dedent(after_sentinel)
    agent = F06ResultPathIsAbsoluteTests.AGENT.replace(original_tail, late_tail)
    assert agent != F06ResultPathIsAbsoluteTests.AGENT, "the substitution did not apply"
    return agent


FINAL_RECORD_TAIL = '''\
    sleep 0.3
    if [ -n "$OUT" ]; then
      mkdir -p "$(dirname "$OUT")" 2>/dev/null
      printf 'F6 BODY written by the agent\\nSTATUS: COMPLETE\\n' > "$OUT"
    fi
    printf '{"type":"turn.completed","usage":{"input_tokens":1}}\\n'
    exit 0
'''
HUNG_AGENT_TAIL = '''\
    sleep 30
    exit 0
'''


class Iteration3StreamFinalityTests(_F06LateBase):
    """RED on the iteration-2 tree: every `OSError` from the master read was labelled
    `hangup`, and `await_completion` recorded `post_exit_drain.ended` without gating on
    it -- so a structured success could be authorised from a stream whose durable end was
    never observed.  Production-wired: the F06 composition through `adapter.start`, the
    read seam driven over the REAL pty, the fenced exit proven before the final bytes."""

    def _dispatch_with_reader(self, run_id: str, agent_script: str, reader_factory,
                              *, budget_ms: int | None = None):
        """Like `_dispatch`, but the session's master reader is replaced by
        `reader_factory(session, sentinel_path)` before the dispatch starts."""
        from scripts.test_os37_recovery_boundary_regressions import F06ResultPathIsAbsoluteTests
        from scripts.deterministic_workflow.runtime_state import InMemoryRuntimeStateStore
        bin_dir = self._compile(self.base, f"f6-{run_id}", agent_script)

        class _Shape:
            worktree = str(self.worktree)
        spec = F06ResultPathIsAbsoluteTests._spec(_Shape(), bin_dir)
        spec["binary"] = f"f6-{run_id}"
        relative = Path("rel-artifacts")
        ledger = InMemoryRuntimeStateStore()
        adapter, _state = launcher.build_standalone_adapter(
            {"run_id": run_id, "thread_id": "t", "phases": ["IMPLEMENTATION"]},
            artifact_base=relative, run_id=run_id, runtime_state=ledger, profile_spec=spec)
        self.adapters.append(adapter)
        intent = {**WORKER_INTENT_KEYS, "intent_id": f"i-{run_id}", "run_id": run_id,
                  "role": "WORKER"}
        session = adapter.runtime.session_for(intent)
        sentinel = pty_supervisor.exit_sentinel_path(session.artifact_base, run_id,
                                                     session.session_id, session.incarnation)
        (self.worktree / ".late-sentinel").write_text(f"{sentinel}\n{session.fence}\n")
        if reader_factory is not None:
            session._master_reader = reader_factory(session, sentinel)
        if budget_ms is not None:
            session.POST_EXIT_DRAIN_BUDGET_MS = budget_ms
        claim = ledger.claim(intent)
        receipt = adapter.start(intent, lease_token=claim["lease_token"])
        rows = journal_mod.ExecutionJournal(relative, run_id).rows_for(intent["intent_id"])
        return receipt, rows, session, ledger, intent

    def _assert_no_success_anywhere(self, receipt, rows, session, ledger, intent,
                                    *, ended: str) -> None:
        self.assertEqual(receipt["outcome"], "failed", receipt)
        self.assertEqual(receipt["failure_reason"], "stream_end_unproven", receipt)
        self.assertNotEqual(session.state, "COMPLETED")
        self.assertFalse(any(r.get("state") == "COMPLETED" for r in rows),
                         "a COMPLETED row was journalled without stream finality")
        settled = [r for r in rows if r["kind"] == "SETTLEMENT_OBSERVED"]
        self.assertEqual(len(settled), 1, settled)
        self.assertEqual(settled[0]["state"], "FAILED")
        self.assertEqual(settled[0]["outcome"], "failed")
        verdict = settled[0]["source_vocabulary"]["completion_verdict"]
        self.assertEqual(verdict["reason"], "stream_end_unproven", verdict)
        stored = ledger.get_settlement(intent["intent_id"])
        self.assertIsNotNone(stored)
        self.assertEqual(stored["result"].get("status"), "BLOCKED", stored)
        gate = [r for r in rows if r["kind"] == "EVENT"
                and (r["source_vocabulary"].get("post_exit_drain") or {}).get("ended") == ended]
        self.assertTrue(gate, f"no finality-gate trace naming ended={ended!r}")
        self.assertTrue(gate[0]["source_vocabulary"]["settlement_record_present"],
                        "the case did not hold a parsed candidate; nothing was gated")

    def test_a_non_eio_read_failure_after_the_fenced_exit_is_a_typed_refusal(self) -> None:
        def factory(session, sentinel):
            def reader(fd, n):
                if os.path.exists(sentinel):
                    raise OSError(errno.EBADF, "mutated unreadable master")
                return os.read(fd, n)
            return reader
        receipt, rows, session, ledger, intent = self._dispatch_with_reader(
            "run_r8i3ebadf", _sentinel_first_agent(after_sentinel=FINAL_RECORD_TAIL), factory)
        self._assert_no_success_anywhere(receipt, rows, session, ledger, intent,
                                         ended="master_unreadable")
        drain = [r for r in rows if r["kind"] == "SETTLEMENT_OBSERVED"][0][
            "source_vocabulary"]["post_exit_drain"]
        self.assertEqual(drain["errno"], "EBADF", drain)

    def test_eintr_is_retried_and_the_stream_still_reads_to_the_hangup(self) -> None:
        def factory(session, sentinel):
            interrupts = {"n": 0}

            def reader(fd, n):
                if os.path.exists(sentinel) and interrupts["n"] < 3:
                    interrupts["n"] += 1
                    raise InterruptedError(errno.EINTR, "interrupted")
                return os.read(fd, n)
            session._interrupts = interrupts
            return reader
        receipt, rows, session, _ledger, _intent = self._dispatch_with_reader(
            "run_r8i3eintr", _sentinel_first_agent(after_sentinel=FINAL_RECORD_TAIL), factory)
        self.assertGreaterEqual(session._interrupts["n"], 1, "EINTR was never injected")
        self.assertEqual(receipt["outcome"], "succeeded", receipt)
        settled = [r for r in rows if r["kind"] == "SETTLEMENT_OBSERVED"]
        self.assertEqual(settled[0]["state"], "COMPLETED")
        drain = settled[0]["source_vocabulary"]["post_exit_drain"]
        self.assertEqual(drain["ended"], "hangup", drain)
        self.assertGreater(drain["bytes"], 0)

    def test_a_drain_that_ends_by_budget_with_a_candidate_is_fail_closed(self) -> None:
        # The agent writes the sentinel, then HANGS holding its slave open: the drain ends
        # by the bound (500 ms) with `item.completed` parsed -- never a success; the
        # ladder then terminates the hung process for the typed FAILED settlement.
        receipt, rows, session, ledger, intent = self._dispatch_with_reader(
            "run_r8i3budget", _sentinel_first_agent(after_sentinel=HUNG_AGENT_TAIL), None,
            budget_ms=500)
        self._assert_no_success_anywhere(receipt, rows, session, ledger, intent, ended="budget")


class Iteration3ReadClassificationTests(unittest.TestCase):
    """`drain_after_exit` over a real pty pair whose slave is STILL OPEN: only EOF / EIO
    is hangup; EBADF (the reviewer's mutation) and a failing poll are `master_unreadable`
    with the errno named; EINTR is retried."""

    def setUp(self) -> None:
        import pty
        from scripts.deterministic_workflow.standalone_profile import profile_from_mapping
        from scripts.deterministic_workflow.standalone_runtime import StandaloneSession
        self.base = Path(tempfile.mkdtemp(prefix="os37-r8-i3cls-"))
        self.addCleanup(shutil.rmtree, self.base, True)
        self.master, self.slave = pty.openpty()
        self.addCleanup(self._close_fds)
        profile = profile_from_mapping(stub_profile_spec("alive", worktree=str(self.base)))
        self.session = StandaloneSession(
            intent={"intent_id": "i-cls", "run_id": "run_cls", "role": "WORKER"},
            profile=profile, artifact_base=self.base, run_id="run_cls",
            journal=journal_mod.ExecutionJournal(self.base, "run_cls"))
        self.session.capture = capture_mod.BoundedCapture(self.base / "capture.log")
        self.session.pty = {"master_fd": self.master, "pty_id": "pty-cls"}

    def _close_fds(self) -> None:
        for fd in (self.master, self.slave):
            with contextlib.suppress(OSError):
                os.close(fd)

    def test_ebadf_with_the_slave_still_open_is_master_unreadable_not_hangup(self) -> None:
        os.write(self.slave, b'{"type":"item.completed"}\n')

        def reader(fd, n):
            raise OSError(errno.EBADF, "mutated unreadable master")
        self.session._master_reader = reader
        drained = self.session.drain_after_exit(budget_ms=2000)
        self.assertEqual(drained["ended"], "master_unreadable", drained)
        self.assertEqual(drained["errno"], "EBADF", drained)
        self.assertEqual(drained["bytes"], 0)

    def test_eintr_is_retried_and_eof_is_the_hangup(self) -> None:
        calls = {"n": 0}

        def reader(fd, n):
            calls["n"] += 1
            if calls["n"] <= 2:
                raise InterruptedError(errno.EINTR, "interrupted")
            return os.read(fd, n)
        self.session._master_reader = reader

        def writer() -> None:                            # darwin discards unread slave
            os.write(self.slave, b'{"type":"turn.completed"}\n')   # output on close, so
            time.sleep(0.3)                              # the bytes are read BEFORE the
            os.close(self.slave)                         # hangup, as on a real exit
        thread = threading.Thread(target=writer, daemon=True)
        thread.start()
        drained = self.session.drain_after_exit(budget_ms=3000)
        thread.join(timeout=2)
        self.assertEqual(drained["ended"], "hangup", drained)
        self.assertGreaterEqual(calls["n"], 3)
        self.assertIn('"turn.completed"', self.session.capture.transcript())

    def test_eio_is_the_hangup_and_other_errnos_are_named(self) -> None:
        for code, expected in ((errno.EIO, "hangup"), (errno.EACCES, "master_unreadable"),
                               (errno.ENXIO, "master_unreadable")):
            with self.subTest(errno=errno.errorcode[code]):
                os.write(self.slave, b"x\n")

                def reader(fd, n, code=code):
                    raise OSError(code, "injected")
                self.session._master_reader = reader
                drained = self.session.drain_after_exit(budget_ms=2000)
                self.assertEqual(drained["ended"], expected, drained)
                self.assertEqual(drained["errno"], errno.errorcode[code])
                os.read(self.master, 65536)              # clear the byte for the next case

    def test_a_failing_poll_is_master_unreadable(self) -> None:
        self.session.pty = {"master_fd": 10**6, "pty_id": "pty-bad"}   # not an open fd
        drained = self.session.drain_after_exit(budget_ms=500)
        self.assertEqual(drained["ended"], "master_unreadable", drained)
        self.assertIn(drained["errno"], ("EBADF", "ValueError"))

    def test_a_masterless_session_is_final_only_by_the_watchers_finalized_proof(self) -> None:
        """An ADOPTED session holds no master: its end-of-stream evidence is the exit
        watcher's fenced CAPTURE-FINALIZED proof (round-9 item 1), written after the
        watcher's own final drain, meta save and fsync and BEFORE its sentinel.  The
        sentinel alone -- which the watcher writes without draining whenever the
        supervisor was alive at the exit -- is `exit_sentinel_only` and NOT final; an exit
        proven only by the process table is `none`.  (Iteration 3 accepted the sentinel
        alone here; that was the round-9 blocker.  The full adopted path is
        `F01CrashedSupervisorDispatchIsCollectedTests`, which settles COMPLETED through
        exactly this rule.)"""
        from scripts.deterministic_workflow.standalone_runtime import _stream_is_final
        self.session.pty = None
        drained = self.session.drain_after_exit(budget_ms=500)
        self.assertEqual(drained["ended"], "no_master", drained)
        self.assertEqual(drained["finality"], "none")
        self.assertFalse(_stream_is_final(drained))
        sentinel = pty_supervisor.exit_sentinel_path(
            self.session.artifact_base, "run_cls", self.session.session_id,
            self.session.incarnation)
        sentinel.parent.mkdir(parents=True, exist_ok=True)
        pty_supervisor.write_exit_sentinel(sentinel, code=0, fence=self.session.fence)
        drained = self.session.drain_after_exit(budget_ms=500)
        self.assertEqual(drained["finality"], "exit_sentinel_only", drained)
        self.assertFalse(_stream_is_final(drained), "a sentinel alone was taken as final")
        # The watcher's proof, bound to the capture as it is and to the sentinel's code.
        self.session.capture.append(b'{"type":"turn.completed"}\n', at="t")
        proof = capture_mod.capture_finalized_path(self.session.capture.path,
                                                   self.session.incarnation)
        capture_mod.write_capture_finalized(
            proof, fence=self.session.fence, finality=capture_mod.FINALITY_PROVEN,
            writer=capture_mod.WRITER_EXIT_WATCHER, ended="hangup", errno_name="",
            total_bytes=self.session.capture.size, sha256=self.session.capture.sha256,
            records=1, exit_how="exit_sentinel", exit_code=0)
        drained = self.session.drain_after_exit(budget_ms=500)
        self.assertEqual(drained["finality"], "capture_finalized", drained)
        self.assertTrue(_stream_is_final(drained))
        # A proof that no longer describes the capture (bytes appended after it) is a
        # named mismatch, not finality.
        with open(self.session.capture.path, "ab") as handle:
            handle.write(b"stray\n")
        drained = self.session.drain_after_exit(budget_ms=500)
        self.assertEqual(drained["finality"], "mismatch", drained)
        self.assertEqual(drained["finality_detail"], "capture_length_mismatch")
        self.assertFalse(_stream_is_final(drained))
        # And a FOREIGN sentinel (another incarnation's) proves nothing.
        pty_supervisor.write_exit_sentinel(sentinel, code=0,
                                           fence=f"{self.session.session_id}:i-other")
        self.assertFalse(_stream_is_final(self.session.drain_after_exit(budget_ms=500)))
        for ended in ("budget", "master_unreadable", "hangup", "no_master"):
            self.assertFalse(_stream_is_final({"ended": ended, "finality": "exit_sentinel"}))
            self.assertFalse(_stream_is_final({"ended": ended, "finality": "none"}))
