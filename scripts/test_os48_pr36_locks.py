"""OS-48 locks for PR #36 consolidated review comment 5739898842 (run_9af92a7f320d), each
through the PRODUCTION caller the reviewer named, RED at head ``c9b8d04`` and GREEN with the fix.

* PR36-1 [P1] (L-1, L-2).  With a VERIFIED fence the settlement reads EXACTLY ``[baseline, N)``
  -- one bounded read of ``N - baseline`` bytes, never read-to-EOF-then-slice -- and the
  answerability / integrity a settlement rests on is the fence's own recorded fact about
  ``[0, N)`` (`capture_at_publish`), never the live whole-file state.  A post-N diagnostic
  tail that is over the capture limit (`line_bytes` cut, `total_bytes` drop), or too large to
  allocate (a `MemoryError` on an unbounded read), changes NOTHING about an already-bound
  settlement: a bound success stays COMPLETED, a bound refusal stays FAILED
  `refusal_in_boundary`; `capture_truncated` / `record_scan_incomplete` never come from
  bytes past N, and the post-N state is recorded as diagnostic evidence only.
* PR36-2 [P2] (L-3).  The fenced selector receives the REAL prompt-echo provenance -- the
  session's `delivery_events` (payload + transport + offset, translated into the fenced
  range's coordinates), not the payload-less `delivery_intent` -- and a PROVEN echo is
  excised before records, candidates and framing are read, so `post_ready_delivery` + ECHO
  prompt text carrying "not logged in", "answer y/n" and a result-JSON example is never a
  refusal / framing / completion candidate.
* PR36-3 [P2] (L-5).  The FENCE and RELEASE markers are written through a descriptor opened
  SEPARATELY on the slave device (its own open file description), so the agent's 0/1/2 and
  every descendant's inherited descriptors keep their file-status flags: ``O_NONBLOCK`` is
  observed invariant from INSIDE the agent subtree before, during (a full output FIFO that
  forces the bounded-write retry loop) and after the marker write; the marker still lands
  in-band after every agent byte, measured over 256 KiB.
* PR36-4 [P2] (L-4).  The live session persists its settlement baseline and delivery events
  durably (journal row `delivery_recorded` + the delivery record beside the capture) and
  `adopt()` restores both, so a run settled live and the same run adopted from its journal
  produce identical baseline, provenance and verdict.
* L-6.  Both implementation copies are byte-identical.
* REVIEW_BUGFIX i1 F-001 (iteration 2; L-7..L-10).  The durable delivery provenance carries NO
  byte of the prompt: the `delivery_recorded` journal row holds, per event, the offset, the
  transport, the payload's sha256 + length and a DIGEST-ONLY `echo_proof` (`echo_absent` by name
  for `argv` / ECHO-clear pty writes; the sha256 + length of each echo form for an echo-possible
  pty write); the i1 side record `capture.log.delivery.<inc>.json` (which held the payload) no
  longer exists.  A sentinel secret in an argv prompt -- including the Orca dispatch-capability
  preamble shape -- is absent from EVERY file the run wrote while live and adopted settle
  identically; an adopted argv event resolves `echo_absent` by name; for pty+ECHO the persisted
  material adds no byte to what capture.log already holds (measured: the prompt appears in
  capture.log alone), and a tampered / missing proof on adoption resolves `echo_unproven` /
  excludes nothing -- never a wider excision.
* REVIEW_BUGFIX_iteration2 F-002 (iteration 3; L-11..L-14).  A restored proof is BOUND to the
  recorded transport before any digest is compared (`lifecycle._bound_proof` /
  `transport_echo_capability`): a transport that proves absence (`argv`, ECHO-clear pty) accepts
  only a canonical `echo_absent` proof; an echo-possible transport accepts only `echo_expected`
  with 1..TAB_STOP forms; every class / reason / forms contradiction is `echo_unproven` with a
  NAMED reason and no span.  A forged `echo_expected` proof on an adopted argv event whose digest
  matches agent refusal- or completion-shaped bytes excises nothing (R1 holds; live == adopted
  verdict), the inverse (`echo_absent` on an ECHO-set pty) is unproven by name, and the two
  consistent cases resolve exactly as before.

Every native lock orders its adversarial events with seams -- a go-file the test writes, a pipe
the descendant signals, a FULL output FIFO, an injected reader -- never a sleep aimed at a window.
"""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import select
import shutil
import sys
import tempfile
import time
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from scripts.deterministic_workflow import standalone_capture as capture_mod  # noqa: E402
from scripts.deterministic_workflow import standalone_journal as journal_mod  # noqa: E402
from scripts.deterministic_workflow import standalone_lifecycle as lifecycle  # noqa: E402
from scripts.deterministic_workflow import standalone_pty as pty_supervisor  # noqa: E402
from scripts.deterministic_workflow import standalone_runtime as rt  # noqa: E402
from scripts.deterministic_workflow.standalone_profile import (CaptureLimits,  # noqa: E402
                                                               profile_from_mapping)
from scripts.os48_cut_harness import successor, wait_for  # noqa: E402
from scripts.os48_lock_support import PYTHON, Room, sh_profile, spawn_session  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
#: names the contract uses, spelled as literals so a checkpoint without them fails on
#: BEHAVIOUR (a wrong state), never on a missing attribute
REFUSAL_IN_BOUNDARY = "refusal_in_boundary"
PROVENANCE_AMBIGUOUS = "provenance_ambiguous"
SCAN_INCOMPLETE = "record_scan_incomplete"
CAPTURE_TRUNCATED = "capture_truncated"

#: a bound success, then (optionally) a bound refusal, then fork a helper that HOLDS the slave
#: (inherited fd 1), waits for the test's go-file and only then writes the diagnostic tail
_TAIL_ROOT = """import os, json, sys, time
sid = os.environ['OS48_TEST_SID']
go = os.environ['OS48_GO_FILE']
tail = int(os.environ['OS48_TAIL_BYTES'])
for is_error in %(records)s:
    os.write(1, (json.dumps(dict(type='result', is_error=is_error, session_id=sid, result='body')) + '\\n').encode())
pid = os.fork()
if pid == 0:
    while not os.path.exists(go):
        time.sleep(0.01)
    line = (b'D' * tail) + b'\\n'          # ONE line, past every limit the profile sets
    view = memoryview(line)
    while view:
        n = os.write(1, view)
        view = view[n:]
    os._exit(0)
os._exit(0)
"""

#: the ECHO turn: cooked mode WITH echo (the reviewer's `post_ready_delivery + ECHO`), the
#: readiness record, the prompt read, a BOUND success
_ECHO_AGENT = """SID="$1"
stty echo icanon
printf '{"type":"system","session_id":"%%s"}\\n' "$SID"
IFS= read -r PROMPT
%(records)s
exit 0
"""


def _record(is_error: bool) -> str:
    err = "true" if is_error else "false"
    return 'printf \'{"type":"result","is_error":%s,"session_id":"%%s"}\\n\' "$SID"\n' % err


def _echo_prompt(session_id: str) -> str:
    """The reviewer's prompt: a refusal phrase, a y/n question and a result-JSON example
    carrying THIS dispatch's binding -- all of it prompt echo, none of it agent evidence."""
    return ('Continue the task. If the CLI says you are not logged in, answer y/n. '
            'Reply with exactly one result line like this example: '
            '{"type":"result","is_error":false,"session_id":"%s"}' % session_id)


def _limits(**kw: int) -> CaptureLimits:
    base = {"max_total_bytes": 1_000_000, "max_line_bytes": 512, "max_records": 200_000,
            "read_chunk": 65_536}
    base.update(kw)
    return CaptureLimits(**base)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# =====================================================================================
# PR36-1 -- a post-N diagnostic tail never changes an already-bound settlement (L-1, L-2)
# =====================================================================================
class PR36F1DiagnosticTailTests(unittest.TestCase):
    """The reviewer's counterexample through the production spawn + `await_completion`: a
    valid bound record, the fence, then a ~70 KB diagnostic line written by a lingering
    subtree member AFTER the fence is bound.  At c9b8d04 the line trips the capture limit
    (`line_bytes` / `total_bytes`), `completion()` consults the WHOLE capture's
    answerability and the bound settlement flips to ``state=LOST lost_reason=capture_truncated
    has_record=True``; the same tail read to EOF by `capture.raw(baseline)` and failing to
    allocate flips it to `record_scan_incomplete`."""

    def setUp(self) -> None:
        self.room = Room()
        self.addCleanup(self.room.close)

    def _spawn(self, run_id: str, *, records: str, limits: CaptureLimits, tail_bytes: int = 70_000):
        root = self.room.path / f"root-{run_id}.py"
        root.write_text(_TAIL_ROOT % {"records": records})
        go = self.room.path / f"go-{run_id}"
        profile = replace(sh_profile(str(self.room.path), binding_mode="session_field",
                                     binding_field="session_id"), capture=limits)
        session, sentinel = spawn_session(
            self.room, "", run_id=run_id, argv=[PYTHON, str(root)], image=PYTHON, profile=profile,
            extra_env={"OS48_GO_FILE": str(go), "OS48_TAIL_BYTES": str(tail_bytes)})
        return session, sentinel, go

    def _info(self, session, sentinel) -> dict:
        return {"art": str(self.room.path / "art"), "run_id": session.run_id,
                "session_id": session.session_id, "incarnation": session.incarnation,
                "fence": session.fence, "fence_nonce": session.fence_nonce,
                "capture": str(session.capture.path), "sentinel": str(sentinel),
                "agent_pid": session.pty["pid"], "leader_pid": session.pty["leader_pid"]}

    def _bound(self, session, first: dict) -> tuple[int, bytes]:
        self.assertIsNotNone(session._boundary, first)
        n = int(session._boundary["offset_n"])
        prefix = session.capture.raw()[:n]
        self.assertEqual(_sha(prefix), session._boundary["fence"]["boundary"]["sha256_prefix"])
        return n, prefix

    def _land_tail(self, session, go: Path, *, expect: str) -> None:
        """The go-file releases the helper; the PRODUCTION pump reads its line into the
        capture until the store names the truncation the profile's limit produces."""
        go.write_text("go")
        wait_for(lambda: (session.pump(timeout_ms=20), session.capture.truncated)[1],
                 seconds=20, what=f"the post-N tail to trip the {expect} limit")
        self.assertEqual(session.capture.truncation, expect)
        for _ in range(5):
            session.pump(timeout_ms=50)

    def _assert_bound_success(self, result, n: int) -> None:
        self.assertEqual(result["state"], "COMPLETED", result)
        self.assertEqual((result.get("verdict") or {}).get("outcome"), "succeeded", result)
        evidence = result["evidence"]
        self.assertTrue(evidence["capture_answerable"], evidence)
        self.assertEqual(evidence.get("lost_reason"), "", evidence)
        self.assertIsNone(evidence["provenance_outcome"], evidence)
        self.assertEqual((evidence.get("boundary") or {}).get("offset_n"), n, evidence)

    def _assert_bound_refusal(self, result, n: int) -> None:
        self.assertEqual(result["state"], "FAILED", result)
        self.assertEqual((result.get("verdict") or {}).get("reason"), REFUSAL_IN_BOUNDARY, result)
        evidence = result["evidence"]
        self.assertTrue(evidence["capture_answerable"], evidence)
        self.assertEqual(evidence.get("lost_reason"), "", evidence)
        self.assertEqual(evidence["provenance_outcome"], REFUSAL_IN_BOUNDARY, evidence)
        self.assertEqual((evidence.get("boundary") or {}).get("offset_n"), n, evidence)

    # ---- L-1: over-limit tail after a bound SUCCESS ---------------------------------------
    def test_l1_an_over_limit_tail_after_a_bound_success_does_not_change_the_settlement(self) -> None:
        """The contract's counterexample, verbatim: at c9b8d04 the second `completion()` /
        `await_completion()` answers ``LOST capture_truncated`` with the record present."""
        for cause, limits in (("line_bytes", _limits()),
                              ("total_bytes", _limits(max_line_bytes=512, max_total_bytes=1_500))):
            with self.subTest(cause=cause):
                run_id = f"pr36-l1-success-{cause}"
                session, sentinel, go = self._spawn(run_id, records="(False,)", limits=limits)
                first = session.await_completion()
                n, prefix = self._bound(session, first)
                self._assert_bound_success(first, n)
                self._land_tail(session, go, expect=cause)
                self.assertGreater(session.capture.size, n, "the tail never reached the capture")
                evidence = session.completion()
                self.assertTrue(evidence["capture_answerable"],
                                f"the post-N tail changed answerability: {evidence.get('lost_reason')!r}")
                self.assertEqual(evidence.get("lost_reason"), "", evidence)
                self.assertIsNotNone(evidence["settlement_record"], evidence)
                self.assertNotEqual(evidence["provenance_outcome"], SCAN_INCOMPLETE, evidence)
                again = session.await_completion()               # the pre-bound production path
                self._assert_bound_success(again, n)
                self.assertEqual(session.capture.raw()[:n], prefix, "[0, N) changed")
                # the post-N state IS recorded -- as diagnostic evidence, by name
                post = again["evidence"].get("post_boundary") or {}
                self.assertEqual(post.get("truncation"), cause, again["evidence"])
                # ... and a SUCCESSOR over the same artifacts (the adopted reader) agrees
                later = successor(self.room, self._info(session, sentinel))
                adopted = later.await_completion()
                self._assert_bound_success(adopted, n)

    # ---- L-1: over-limit tail after a bound REFUSAL ---------------------------------------
    def test_l1_an_over_limit_tail_after_a_bound_refusal_does_not_change_the_settlement(self) -> None:
        run_id = "pr36-l1-refusal"
        session, sentinel, go = self._spawn(run_id, records="(False, True)", limits=_limits())
        first = session.await_completion()
        n, prefix = self._bound(session, first)
        self._assert_bound_refusal(first, n)
        self.assertIn(b'"is_error": true', prefix)
        self._land_tail(session, go, expect="line_bytes")
        evidence = session.completion()
        self.assertTrue(evidence["capture_answerable"], evidence)
        self.assertEqual(evidence["provenance_outcome"], REFUSAL_IN_BOUNDARY, evidence)
        again = session.await_completion()
        self._assert_bound_refusal(again, n)
        later = successor(self.room, self._info(session, sentinel))
        self._assert_bound_refusal(later.await_completion(), n)

    # ---- L-1: the whole-tail allocation failure variant -----------------------------------
    def test_l1_a_whole_tail_allocation_failure_never_reaches_a_bound_settlement(self) -> None:
        """The reviewer's second construction: the post-N tail is too large to allocate.  The
        seam raises `MemoryError` for ANY capture read that is not bounded to ``[.., N]`` --
        exactly the read c9b8d04 issues (`raw(baseline)` to EOF) -- and never for a bounded
        one.  c9b8d04: `record_scan_incomplete`, no record; fixed: the bound success."""
        run_id = "pr36-l1-alloc"
        session, sentinel, go = self._spawn(run_id, records="(False,)", limits=_limits(max_line_bytes=65_536))
        first = session.await_completion()
        n, _prefix = self._bound(session, first)
        self._assert_bound_success(first, n)
        go.write_text("go")
        wait_for(lambda: (session.pump(timeout_ms=20), session.capture.size >= n + 70_000)[1],
                 seconds=20, what="the 70 KB post-N tail to land")
        real_raw = session.capture.raw
        faults: list = []

        def bounded_only(cursor: int = 0, limit=None, *args, **kwargs):
            if limit is None or int(cursor) + int(limit) > n:
                faults.append((cursor, limit))
                raise MemoryError("deterministic whole-tail allocation seam")
            return real_raw(cursor, limit, *args, **kwargs)

        with patch.object(session.capture, "raw", bounded_only):
            evidence = session.completion()
            again = session.await_completion()
        self.assertEqual(faults, [], f"the settlement reader read past N: {faults}")
        self.assertNotEqual(evidence["provenance_outcome"], SCAN_INCOMPLETE, evidence)
        self.assertIsNotNone(evidence["settlement_record"], evidence)
        self._assert_bound_success(again, n)

    # ---- L-2: the settlement reader reads exactly N - baseline bytes ----------------------
    def test_l2_the_settlement_reader_reads_exactly_n_minus_baseline_bytes(self) -> None:
        """Assert on the read seam: ONE settlement read, at ``baseline``, of length
        ``N - baseline``, returning exactly that many bytes, and no read whose range extends
        past N.  c9b8d04 reads ``raw(baseline)`` to EOF (limit None, length > N - baseline)."""
        run_id = "pr36-l2-reader"
        session, _sentinel, go = self._spawn(run_id, records="(False,)", limits=_limits(max_line_bytes=65_536), tail_bytes=20_000)
        first = session.await_completion()
        n, _prefix = self._bound(session, first)
        self._assert_bound_success(first, n)
        go.write_text("go")
        wait_for(lambda: (session.pump(timeout_ms=20), session.capture.size >= n + 20_000)[1],
                 seconds=20, what="the post-N tail to land")
        baseline = int(session._settlement_baseline or 0)
        real_raw = session.capture.raw
        reads: list[tuple] = []

        def spy(cursor: int = 0, limit=None, *args, **kwargs):
            out = real_raw(cursor, limit, *args, **kwargs) if limit is not None else real_raw(cursor)
            reads.append((int(cursor), limit, len(out)))
            return out

        with patch.object(session.capture, "raw", spy):
            evidence = session.completion()
        self.assertIsNotNone(evidence["settlement_record"], evidence)
        settlement = [r for r in reads if r[0] == baseline]
        self.assertEqual(len(settlement), 1, f"reads during completion(): {reads}")
        cursor, limit, got = settlement[0]
        self.assertEqual(limit, n - baseline, f"not a bounded read of N - baseline: {settlement[0]}")
        self.assertEqual(got, n - baseline, f"the read returned other than N - baseline bytes: {settlement[0]}")
        for cursor, limit, got in reads:
            self.assertLessEqual(cursor + (limit if limit is not None else got), n,
                                 f"a read during completion() reached past N: {reads}")

    def test_l2_raw_with_a_limit_issues_one_bounded_read_of_exactly_limit_bytes(self) -> None:
        """The physical read: `BoundedCapture.raw(cursor, limit)` seeks to ``cursor`` and asks
        the file for ``limit`` bytes ONCE -- never a read to EOF followed by a slice."""
        base = Path(tempfile.mkdtemp(prefix="pr36-l2-raw-"))
        self.addCleanup(shutil.rmtree, base, True)
        store = capture_mod.BoundedCapture(base / "capture.log")
        data = bytes(range(256)) * 40                       # 10,240 bytes
        (base / "capture.log").write_bytes(data)
        asked: list[int | None] = []
        real_open = io.open

        class Spy(io.FileIO):
            def read(self, size=-1):
                asked.append(size)
                return super().read(size)

        def opener(path, mode="r", *a, **kw):
            if str(path) == str(base / "capture.log") and "b" in mode:
                return Spy(path, mode.replace("b", ""))
            return real_open(path, mode, *a, **kw)

        import inspect
        bounded = "limit" in inspect.signature(store.raw).parameters
        with patch("builtins.open", opener):
            got = store.raw(1_000, 2_048) if bounded else store.raw(1_000)[:2_048]
        self.assertEqual(got, data[1_000:3_048])
        self.assertEqual(asked, [2_048], f"the bounded read asked for {asked}")


# =====================================================================================
# PR36-2 -- the fenced selector receives the real prompt-echo provenance (L-3)
# =====================================================================================
def _stub_dir() -> Path | None:
    from scripts import os37_native_stub as native_stub
    return native_stub.native_stub_dir()


def _stub_spec(worktree: str, script: str, **timeouts: int) -> dict:
    """The OS-37 native stub in `agent-script` mode: the WHOLE turn is ``script``, the
    completion selector is `session_field`-bound (the OS-48 R3 binding)."""
    from scripts.test_os37_followup_review_regressions import stub_profile_spec
    spec = stub_profile_spec("agent-script", worktree=worktree,
                             extra_env={"OS37_STUB_AGENT_SCRIPT": script},
                             timeouts={"completion_timeout_ms": 15_000, "post_exit_drain_budget_ms": 3_000,
                                       "readiness_timeout_ms": 10_000, "delivery_verify_timeout_ms": 5_000,
                                       **timeouts})
    spec["completion_records"] = [{"channel": "structured", "record_type": "result",
                                   "binding_mode": "session_field", "binding_field": "session_id",
                                   "error_field": "is_error"}]
    return spec


class _StubTurn(unittest.TestCase):
    """A REAL `start` -> `await_ready` -> `send` -> `await_completion` over the native stub."""

    @classmethod
    def setUpClass(cls) -> None:
        if _stub_dir() is None:                      # pragma: no cover - CI has cc
            from scripts import os37_native_stub as native_stub
            raise unittest.SkipTest(native_stub.NO_COMPILER_REASON)

    def setUp(self) -> None:
        self.base = Path(tempfile.mkdtemp(prefix="pr36-stub-")).resolve()
        self.addCleanup(shutil.rmtree, self.base, True)
        self.sessions: list = []
        self.addCleanup(self._reap)

    def _reap(self) -> None:
        from scripts.test_os37_followup_review_regressions import kill_and_reap
        for session in self.sessions:
            pty = session.pty or {}
            targets = [int(pty[k]) for k in ("pid", "leader_pid") if pty.get(k)]
            if targets:
                kill_and_reap(*targets)

    def _session(self, run_id: str, script_body: str, *, delivery_mode: str = "post_ready_delivery",
                 journal: journal_mod.ExecutionJournal | None = None) -> rt.StandaloneSession:
        script = self.base / f"agent-{run_id}.sh"
        script.write_text(script_body)
        spec = _stub_spec(str(self.base), str(script))
        spec["delivery_mode"] = delivery_mode
        if delivery_mode == "launch_with_prompt":
            spec["delivery_proofs"] = [{"channel": "structured", "record_type": "assistant"}]
        profile = profile_from_mapping(spec)
        journal = journal or journal_mod.ExecutionJournal(self.base / "art", run_id)
        session = rt.StandaloneSession(
            intent={"intent_id": f"i-{run_id}", "run_id": run_id, "role": "WORKER",
                    "task_id": f"t-{run_id}", "dispatch_id": f"d-{run_id}"},
            profile=profile, artifact_base=self.base / "art", run_id=run_id, journal=journal)
        return session

    def _turn(self, session: rt.StandaloneSession, prompt_of) -> tuple[dict, dict]:
        from scripts.test_os37_lifecycle_boundary_regressions import INJECTED_REHEARSALS
        receipt = session.start(payload="rehearsal", **INJECTED_REHEARSALS)
        self.sessions.append(session)
        self.assertEqual(receipt["start_outcome"], "ready", receipt)
        ready = session.await_ready()
        self.assertEqual(ready["state"], "READY", ready)
        sent = session.send({"payload": prompt_of(session.session_id)})
        self.assertEqual(sent["delivery"], "delivered_confirmed", sent)
        return sent, session.await_completion()

    @staticmethod
    def _fenced(session) -> tuple[int, int, bytes]:
        n = int(session._boundary["offset_n"])
        baseline = int(session._settlement_baseline or 0)
        return baseline, n, session.capture.raw()[baseline:n]

    def _argv_turn(self, session: rt.StandaloneSession, prompt: str) -> dict:
        """A REAL `launch_with_prompt` dispatch through the production `run_dispatch` (start ->
        readiness -> the argv delivery event + its durable row -> completion -> settle)."""
        session.intent.setdefault("command_id", "c")
        session.intent.setdefault("payload_digest", "0" * 64)
        rehearsal = lambda p, e, s: {"channel": "structured", "record_type": "system", "session_id": s}  # noqa: E731
        mode_rehearsal = lambda p, e: {"r_b_closed": True, "delivery_proof": True, "auth_marker": None,  # noqa: E731
                                       "waited_without_prompt": False, "evaluable": True,
                                       "identity_bound": True, "detail": {}}
        out = session.run_dispatch(payload=prompt,
                                   result_parser=lambda attempt, intent: {"status": "COMPLETE", "body": attempt.body},
                                   rehearsal=rehearsal, mode_rehearsal=mode_rehearsal)
        self.sessions.append(session)
        return out

    def _files_holding(self, needle: str) -> list[str]:
        """Every file under the test's base (the artifact base, the agent script, the stub's
        worktree -- everything the run could have written) whose bytes contain ``needle``."""
        hits = []
        for path in sorted(self.base.rglob("*")):
            if path.is_file() and not path.is_symlink():
                try:
                    if needle.encode("utf-8") in path.read_bytes():
                        hits.append(str(path.relative_to(self.base)))
                except OSError:
                    hits.append(f"unreadable:{path.relative_to(self.base)}")
        return hits


class PR36F2EchoProvenanceTests(_StubTurn):
    """`post_ready_delivery` + ECHO: the agent puts its tty in cooked mode with echo, the
    runtime's framed prompt is echoed into the capture inside ``[baseline, N)``, and the prompt
    carries a refusal phrase, a y/n question and a bound result-JSON example.  At c9b8d04
    `completion()` hands the selector an EMPTY event tuple (it looks for
    `delivery_intent.payload`, which `make_delivery_intent` never sets), so the echo's
    "not logged in" fires R1 (`FAILED refusal_in_boundary`, source `free_text`) and, absent
    that phrase, the echoed example is a second completion candidate (`provenance_ambiguous`)."""

    def _assert_completed_over_the_echo(self, session, result, *, echo_prompt: str) -> None:
        baseline, n, fenced = self._fenced(session)
        self.assertIn(b"^[[200~", fenced, "the ECHO of the framed prompt is not inside [baseline, N)")
        self.assertIn(b"not logged in", fenced)
        self.assertGreater(baseline, 0)
        events = tuple({**dict(ev), "offset": int(ev["offset"]) - baseline} for ev in session.delivery_events)
        self.assertTrue(events, "the session recorded no delivery event")
        resolution = lifecycle.resolve_delivery_echo(fenced, events)
        self.assertEqual(resolution["state"], "echo_proven", resolution)
        self.assertEqual(result["state"], "COMPLETED", result)
        evidence = result["evidence"]
        self.assertIsNone(evidence["provenance_outcome"], evidence)
        self.assertIsNone(evidence.get("refusal"), evidence)
        self.assertEqual((evidence.get("settlement_record") or {}).get("is_error"), False, evidence)
        self.assertEqual((result.get("verdict") or {}).get("outcome"), "succeeded", result)

    def test_l3_echoed_refusal_phrases_and_json_examples_are_not_agent_evidence(self) -> None:
        session = self._session("pr36-l3-echo", _ECHO_AGENT % {"records": _record(False)})
        sent, result = self._turn(session, _echo_prompt)
        self.assertEqual(sent.get("proof"), "screen_echo", sent)   # the echo is REAL and proven at delivery
        self._assert_completed_over_the_echo(session, result, echo_prompt=_echo_prompt(session.session_id))

    def test_l3_the_echoed_json_example_alone_is_not_a_second_candidate(self) -> None:
        """Without the refusal phrase the c9b8d04 failure is R2: two `result` candidates in
        the range (the echoed example and the agent's record) -> LOST `provenance_ambiguous`."""
        def prompt(sid: str) -> str:
            return ('Reply with exactly one result line like this example: '
                    '{"type":"result","is_error":false,"session_id":"%s"}' % sid)
        session = self._session("pr36-l3-json", _ECHO_AGENT % {"records": _record(False)})
        _sent, result = self._turn(session, prompt)
        baseline, n, fenced = self._fenced(session)
        self.assertEqual(fenced.count(b'"type":"result"'), 2, fenced)     # the echo AND the record
        self.assertEqual(result["state"], "COMPLETED", result)
        self.assertNotEqual(result["evidence"]["provenance_outcome"], PROVENANCE_AMBIGUOUS, result["evidence"])
        self.assertIsNone(result["evidence"]["provenance_outcome"], result["evidence"])

    def test_l3_an_agent_refusal_after_the_echo_still_dominates(self) -> None:
        """The control in the other direction: the same echoed prompt, and the agent's OWN
        bound refusal after it -> FAILED `refusal_in_boundary` (R1), never masked by the
        exclusion of the echo."""
        session = self._session("pr36-l3-refusal", _ECHO_AGENT % {"records": _record(False) + _record(True)})
        _sent, result = self._turn(session, _echo_prompt)
        self.assertEqual(result["state"], "FAILED", result)
        self.assertEqual((result.get("verdict") or {}).get("reason"), REFUSAL_IN_BOUNDARY, result)
        self.assertEqual((result["evidence"].get("refusal") or {}).get("source"), "error_field", result["evidence"])

    def test_l3_the_selector_excises_a_proven_echo_before_reading_records(self) -> None:
        """Unit-level, no pty: `select_completion` over a fenced range whose head is the PROVEN
        echo of the delivered prompt (a `pty_write` transport with ECHO set).  The echoed
        JSON example must not be a candidate and the echoed phrase must not be a refusal."""
        from scripts.deterministic_workflow import standalone_drivers as drivers
        profile = sh_profile(str(REPO), binding_mode="session_field", binding_field="session_id")
        driver = drivers.driver_for(profile)
        sid = "sid-unit"
        prompt = _echo_prompt(sid)
        import pty as _pty
        import termios
        master, slave = _pty.openpty()
        self.addCleanup(lambda: [os.close(fd) for fd in (master, slave)])
        mode = termios.tcgetattr(slave)
        mode[3] |= termios.ECHO | termios.ICANON            # the agent's `stty echo icanon`
        termios.tcsetattr(slave, termios.TCSANOW, mode)
        transport = pty_supervisor.echo_transport(master, kind="pty_write", framed=True, cols=200)
        derived = lifecycle.expected_echo_forms(prompt, transport)
        self.assertEqual(derived["state"], "echo_expected", derived)
        echo = derived["forms"][0]
        record = json.dumps({"type": "result", "is_error": False, "session_id": sid}).encode()
        raw = echo + b"\r\n" + record + b"\r\n"
        events = ({"offset": 0, "payload": prompt, "transport": transport, "at": ""},)
        text = capture_mod.BoundedCapture.transcript_of(raw)
        selection = driver.select_completion(text, raw=raw, bound_value=sid, delivery_events=events)
        self.assertIsNone(selection["refusal"], selection)
        self.assertIsNone(selection["outcome"], selection)
        self.assertEqual(selection["candidates"], 1, selection)
        self.assertEqual((selection["record"] or {}).get("session_id"), sid, selection)
        # fail-closed control: the SAME bytes with NO provenance are ambiguous / a refusal
        bare = driver.select_completion(text, raw=raw, bound_value=sid, delivery_events=())
        self.assertIsNotNone(bare["refusal"] or bare["outcome"], bare)

    def test_l3_launch_with_prompt_provenance_reaches_the_selector_as_echo_absent(self) -> None:
        """The other delivery mode's provenance: an event whose transport is `argv` (the prompt
        left with the `execve`) reaches the fenced selector and resolves `echo_absent` --
        nothing excluded, nothing unproven -- and the run completes on the bound record.  The
        event is recorded exactly as `run_dispatch` records it for `launch_with_prompt`."""
        room = Room()
        self.addCleanup(room.close)
        root = room.path / "root-argv.py"
        root.write_text(_TAIL_ROOT % {"records": "(False,)"})
        session, _sentinel = spawn_session(
            room, "", run_id="pr36-l3-argv", argv=[PYTHON, str(root)], image=PYTHON,
            binding_mode="session_field", binding_field="session_id",
            extra_env={"OS48_GO_FILE": str(room.path / "never"), "OS48_TAIL_BYTES": "1"})
        session.delivery_events.append({
            "offset": 0, "payload": _echo_prompt(session.session_id),
            "transport": pty_supervisor.echo_transport(None, kind="argv", framed=False,
                                                       cols=session.profile.cols),
            "at": ""})
        result = session.await_completion()
        self.assertEqual(result["state"], "COMPLETED", result)
        rng = result["evidence"].get("settlement_range") or {}
        self.assertEqual(rng.get("echo"), "echo_absent", result["evidence"])
        self.assertEqual(rng.get("delivery_events"), 1, result["evidence"])
        (room.path / "never").write_text("go")


# =====================================================================================
# PR36-3 -- the marker descriptor never mutates the agent subtree's file-status flags (L-5)
# =====================================================================================
_FLAG_ROOT = """import os, fcntl, json, time
room = os.environ['OS48_ROOM']; tag = os.environ['OS48_TAG']
window = float(os.environ['OS48_WINDOW_S'])
tty = os.ttyname(1)
before = fcntl.fcntl(1, fcntl.F_GETFL)
r, w = os.pipe()
pid = os.fork()
if pid == 0:
    os.close(r)
    # Fill the tty OUTPUT FIFO through a SEPARATE open file description (never fd 1's), so
    # the watcher's marker write after the root's reap meets a FULL queue and must take its
    # bounded retry path -- the window in which c9b8d04 holds O_NONBLOCK on the SHARED
    # description.  The supervisor withholds its pump until this report is written.
    fd = os.open(tty, os.O_WRONLY | os.O_NOCTTY | os.O_NONBLOCK)
    filled = 0
    while True:
        try:
            filled += os.write(fd, b'F' * 4096)
        except BlockingIOError:
            break
    os.write(w, b'go'); os.close(w)          # the root may exit now
    samples = {}; nonblock = 0; count = 0
    start = time.monotonic()
    while time.monotonic() - start < window:
        fl = fcntl.fcntl(1, fcntl.F_GETFL)
        samples[str(fl)] = samples.get(str(fl), 0) + 1
        count += 1
        if fl & os.O_NONBLOCK:
            nonblock += 1
        time.sleep(0.0005)
    after = fcntl.fcntl(1, fcntl.F_GETFL)
    stdin_after = fcntl.fcntl(0, fcntl.F_GETFL)
    report = dict(before=before, after=after, stdin_after=stdin_after, samples=samples,
                  count=count, nonblock_samples=nonblock, filled=filled, tty=tty,
                  nonblock_bit=os.O_NONBLOCK)
    tmp = os.path.join(room, 'report-%s.tmp' % tag)
    with open(tmp, 'w') as fh:
        json.dump(report, fh)
    os.rename(tmp, os.path.join(room, 'report-%s.json' % tag))
    os.close(fd)
    os._exit(0)
os.close(w)
os.read(r, 2)
os._exit(0)
"""

_BULK_ROOT = """import os, json, hashlib
sid = os.environ['OS48_TEST_SID']
room = os.environ['OS48_ROOM']; tag = os.environ['OS48_TAG']
digest = hashlib.sha256(); total = 0
line = (json.dumps(dict(type='system', session_id=sid, pad='x' * 200)) + '\\n').encode()
while total < 256 * 1024:
    view = memoryview(line)
    while view:
        n = os.write(1, view); view = view[n:]
    digest.update(line); total += len(line)
final = (json.dumps(dict(type='result', is_error=False, session_id=sid)) + '\\n').encode()
view = memoryview(final)
while view:
    n = os.write(1, view); view = view[n:]
digest.update(final); total += len(final)
with open(os.path.join(room, 'bulk-%s.json' % tag), 'w') as fh:
    json.dump(dict(total=total, sha256=digest.hexdigest()), fh)
os._exit(0)
"""


class PR36F3MarkerDescriptorTests(unittest.TestCase):
    """Observed from INSIDE the agent subtree on a native pty: a descendant that inherited the
    agent's stdio samples `F_GETFL` on fd 1 (the description the watcher's `slave_fd` shares
    at c9b8d04) before, during and after the fence-marker write, with the output FIFO held
    FULL so the watcher is forced into its bounded retry loop.  c9b8d04 sets ``O_NONBLOCK`` on
    that shared description for the whole loop; the fix writes the marker through a
    separately opened slave descriptor and the samples never carry the bit."""

    def setUp(self) -> None:
        self.room = Room()
        self.addCleanup(self.room.close)

    def _spawn(self, run_id: str, root_source: str, *, window_s: float = 0.35):
        root = self.room.path / f"root-{run_id}.py"
        root.write_text(root_source)
        return spawn_session(
            self.room, "", run_id=run_id, argv=[PYTHON, str(root)], image=PYTHON,
            binding_mode="session_field", binding_field="session_id", pump_until_sentinel=False,
            extra_env={"OS48_TAG": run_id, "OS48_WINDOW_S": str(window_s)})

    def test_l5_agent_and_descendant_o_nonblock_state_is_invariant_across_the_marker_write(self) -> None:
        run_id = "pr36-l5-flags"
        session, sentinel = self._spawn(run_id, _FLAG_ROOT)
        report_path = self.room.path / f"report-{run_id}.json"
        # NO pump until the descendant has reported: the FIFO it filled stays full, so the
        # watcher's marker write (after the root's reap) cannot complete at once.
        wait_for(report_path.exists, seconds=20, what="the descendant's flag report")
        report = json.loads(report_path.read_text())
        self.assertGreater(report["filled"], 0, report)
        self.assertGreater(report["count"], 50, report)
        bit = int(report["nonblock_bit"])
        self.assertFalse(int(report["before"]) & bit, report)
        self.assertEqual(report["nonblock_samples"], 0,
                         f"the marker write set O_NONBLOCK on the agent subtree's stdio: {report}")
        self.assertFalse(int(report["after"]) & bit, report)
        self.assertFalse(int(report["stdin_after"]) & bit, report)
        self.assertEqual(set(report["samples"]), {str(report["before"])}, report)
        # now the supervisor pumps: the FIFO drains, the bounded write lands the marker
        # in-band and the fence binds -- the O-3 guarantee under a REAL full queue
        wait_for(lambda: (session.pump(timeout_ms=20), sentinel.exists())[1], seconds=20,
                 what="the exit sentinel after the marker landed")
        session.pump(timeout_ms=50)
        drained = session.drain_after_exit()
        self.assertEqual(drained.get("finality"), rt.FINALITY_CAPTURE_FINALIZED, drained)
        n = int(drained["offset_n"])
        raw = session.capture.raw()
        self.assertIn(b"<<OS48-FENCE", raw[n:n + 32], raw[max(0, n - 8):n + 48])
        # every byte the descendant pushed into the FULL queue is inside [0, N): the marker
        # landed in-band AFTER them, through the separate descriptor
        self.assertEqual(raw[:n].count(b"F"), report["filled"], "the filled bytes are not all inside [0, N)")
        self.assertEqual(raw[:n].replace(b"F", b"").replace(b"\r", b"").replace(b"\n", b""), b"",
                         "bytes other than the descendant's fill precede the marker")

    def test_l5_the_marker_lands_in_band_after_every_agent_byte_through_the_separate_descriptor(self) -> None:
        """DESIGN §1.1's positive fact, re-measured on this host with the new descriptor: the
        root writes 256 KiB of records and a bound result; N equals its byte count and
        sha256(capture[0:N)) equals the digest the root computed of what it wrote."""
        run_id = "pr36-l5-inband"
        session, sentinel = self._spawn(run_id, _BULK_ROOT)
        wait_for(lambda: (session.pump(timeout_ms=20), sentinel.exists())[1], seconds=30,
                 what="the exit sentinel")
        session.pump(timeout_ms=50)
        result = session.await_completion()
        self.assertEqual(result["state"], "COMPLETED", result)
        n = int(session._boundary["offset_n"])
        bulk = json.loads((self.room.path / f"bulk-{run_id}.json").read_text())
        prefix = session.capture.raw()[:n]
        self.assertEqual(session._boundary["fence"]["boundary"]["sha256_prefix"], _sha(prefix))
        # the pty translates the root's LF to CRLF (ONLCR; darwin was MEASURED emitting a
        # stray `\r\r\n` once per ~90 KB at its output-queue high-water mark), and the root
        # writes no CR of its own: with every CR removed, [0, N) IS what the root wrote --
        # every byte, nothing after it
        written = prefix.replace(b"\r", b"")
        self.assertEqual(len(written), bulk["total"], "the marker did not land exactly after the root's last byte")
        self.assertEqual(_sha(written), bulk["sha256"])
        self.assertGreaterEqual(bulk["total"], 256 * 1024)

    def test_l5_the_marker_writer_never_touches_the_shared_descriptor_flags(self) -> None:
        """Unit-level, real pty, no agent: `_write_marker_bounded` given the WATCHER's slave
        fd and the slave's device path must leave the given fd's `F_GETFL` untouched while the
        marker still arrives on the master."""
        import pty as _pty
        master, slave = _pty.openpty()
        self.addCleanup(lambda: [os.close(fd) for fd in (master, slave) if fd >= 0])
        name = os.ttyname(slave)
        before = fcntl_getfl(slave)
        seen: list[int] = []
        real_fcntl = pty_supervisor.fcntl.fcntl

        def spy(fd, cmd, *args):
            if fd == slave and cmd == pty_supervisor.fcntl.F_SETFL:
                seen.append(args[0] if args else -1)
            return real_fcntl(fd, cmd, *args)

        marker = capture_mod.marker_bytes("0" * 32)
        import inspect
        kwargs = ({"slave_name": name}
                  if "slave_name" in inspect.signature(pty_supervisor._write_marker_bounded).parameters else {})
        with patch.object(pty_supervisor.fcntl, "fcntl", spy):
            ok = pty_supervisor._write_marker_bounded(slave, marker, master, None, **kwargs)
        self.assertTrue(ok)
        self.assertEqual(seen, [], "F_SETFL was applied to the shared slave descriptor")
        self.assertEqual(fcntl_getfl(slave), before)
        got = b""
        deadline = time.time() + 2
        while capture_mod.marker_span(got, "0" * 32)[2] != capture_mod.EVIDENCE_FINAL and time.time() < deadline:
            ready, _, _ = select.select([master], [], [], 0.2)
            if ready:
                got += os.read(master, 65536)
        self.assertEqual(capture_mod.marker_span(got, "0" * 32)[2], capture_mod.EVIDENCE_FINAL, got)

    def test_l5_an_exhausted_bound_leaves_the_marker_unwritten_and_the_flags_untouched(self) -> None:
        """The O-3 half of the contract: a FULL output FIFO that nobody drains, a small attempt
        bound -> `False` (the marker is unwritten; a successor names `boundary_unproven`), the
        shared descriptor's flags still untouched, no indefinite block."""
        import pty as _pty
        master, slave = _pty.openpty()
        self.addCleanup(lambda: [os.close(fd) for fd in (master, slave) if fd >= 0])
        name = os.ttyname(slave)
        filler = os.open(name, os.O_WRONLY | os.O_NOCTTY | os.O_NONBLOCK)
        self.addCleanup(os.close, filler)
        filled = 0
        while True:
            try:
                filled += os.write(filler, b"F" * 4096)
            except BlockingIOError:
                break
        self.assertGreater(filled, 0)
        before = fcntl_getfl(slave)
        seen: list[int] = []
        real_fcntl = pty_supervisor.fcntl.fcntl

        def spy(fd, cmd, *args):
            if fd == slave and cmd == pty_supervisor.fcntl.F_SETFL:
                seen.append(args[0] if args else -1)
            return real_fcntl(fd, cmd, *args)

        import inspect
        kwargs = ({"slave_name": name}
                  if "slave_name" in inspect.signature(pty_supervisor._write_marker_bounded).parameters else {})
        started = time.monotonic()
        with patch.object(pty_supervisor.fcntl, "fcntl", spy):
            ok = pty_supervisor._write_marker_bounded(slave, capture_mod.marker_bytes("1" * 32), master, None,
                                                      attempts=5, **kwargs)
        self.assertFalse(ok, "a marker was reported written into a queue nobody drained")
        self.assertLess(time.monotonic() - started, 5.0, "the bounded write did not return promptly")
        self.assertEqual(seen, [], "F_SETFL was applied to the shared slave descriptor")
        self.assertEqual(fcntl_getfl(slave), before)
        # drain: only the filler's bytes are in the queue, no marker
        got = b""
        while True:
            ready, _, _ = select.select([master], [], [], 0.2)
            if not ready:
                break
            got += os.read(master, 65536)
        self.assertEqual(got.replace(b"F", b""), b"", got[-64:])
        self.assertEqual(capture_mod.marker_span(got, "1" * 32)[2], capture_mod.EVIDENCE_UNKNOWN)


def fcntl_getfl(fd: int) -> int:
    import fcntl
    return fcntl.fcntl(fd, fcntl.F_GETFL)


# =====================================================================================
# PR36-4 -- adoption restores the settlement baseline and the delivery events (L-4)
# =====================================================================================

def _provenance(session) -> list[tuple]:
    """The provenance an event carries, in the form BOTH sides can be compared on: offset,
    transport kind + ECHO flag, and the digest-only `echo_proof` -- derived from the payload on
    the live side, restored from the journal on the adopted side (REVIEW_BUGFIX i1 F-001: the
    payload itself is never restored, so it is not part of the comparison)."""
    out = []
    for ev in session.delivery_events:
        transport = ev.get("transport") or {}
        proof = (dict(ev["echo_proof"]) if isinstance(ev.get("echo_proof"), dict)
                 else lifecycle.echo_proof(str(ev.get("payload") or ""), transport))
        out.append((int(ev["offset"]), transport.get("kind"),
                    (transport.get("termios") or {}).get("echo"), json.dumps(proof, sort_keys=True)))
    return out


def _echo_block(session) -> dict:
    """The resolver's verdict over the session's fenced range and its (live or restored) events."""
    baseline = int(session._settlement_baseline or 0)
    n = int(session._boundary["offset_n"])
    events = tuple({**dict(ev), "offset": int(ev["offset"]) - baseline} for ev in session.delivery_events)
    res = lifecycle.resolve_delivery_echo(session.capture.raw()[baseline:n], events)
    return {"state": res["state"], "reason": res["reason"], "spans": [list(sp) for sp in res["spans"]],
            "events": [{k: (list(e[k]) if k == "span" and e[k] else e[k])
                        for k in ("index", "offset", "state", "reason", "span", "forms")}
                       for e in res["events"]]}


def _assert_identical(case: unittest.TestCase, live, live_result, adopted, adopted_result) -> None:
    case.assertEqual(int(adopted._settlement_baseline or 0), int(live._settlement_baseline or 0),
                     "the adopted session restored a different settlement baseline")
    case.assertEqual(_provenance(adopted), _provenance(live),
                     "the adopted session restored different delivery provenance")
    case.assertTrue(all(ev.get("payload") == "" for ev in adopted.delivery_events),
                    "the adoption restored a payload (F-001)")
    case.assertEqual(_echo_block(adopted), _echo_block(live), "live and adopted resolve the echo differently")
    case.assertEqual(adopted_result["state"], live_result["state"], (live_result, adopted_result))
    case.assertEqual((adopted_result.get("verdict") or {}).get("reason"),
                     (live_result.get("verdict") or {}).get("reason"))
    case.assertEqual(adopted_result.get("lost_reason", ""), live_result.get("lost_reason", ""))
    le, ae = live_result["evidence"], adopted_result["evidence"]
    case.assertEqual(ae["provenance_outcome"], le["provenance_outcome"])
    case.assertEqual((ae.get("boundary") or {}).get("offset_n"), (le.get("boundary") or {}).get("offset_n"))
    case.assertEqual(((ae.get("boundary") or {}).get("fence") or {}).get("boundary"),
                     ((le.get("boundary") or {}).get("fence") or {}).get("boundary"))
    case.assertEqual(ae.get("settlement_range"), le.get("settlement_range"))


class PR36F4AdoptionRestoresProvenanceTests(_StubTurn):
    """The REAL adopt path over the journal a LIVE session wrote: the same run, settled live
    and then reconstructed by a stranger session (`adopt(fence=...)` -> masterless
    `drain_after_exit` -> `completion`) must produce the same baseline, the same delivery
    provenance and the same verdict.  c9b8d04 adopts baseline 0 and an empty event list."""

    def _adopt(self, live: rt.StandaloneSession) -> rt.StandaloneSession:
        stranger = rt.StandaloneSession(
            intent=dict(live.intent), profile=live.profile, artifact_base=live.artifact_base,
            run_id=live.run_id, journal=journal_mod.ExecutionJournal(live.artifact_base, live.run_id))
        outcome = stranger.adopt(fence=live.fence)
        self.assertTrue(outcome["adopted"], outcome)
        return stranger

    def _assert_identical(self, live, live_result, adopted, adopted_result) -> None:
        _assert_identical(self, live, live_result, adopted, adopted_result)

    @staticmethod
    def _echo_block(session) -> dict:
        return _echo_block(session)

    def test_l4_live_and_adopted_settle_the_same_completion(self) -> None:
        """The ECHO turn: live excludes the proven echo and completes; c9b8d04's adoption
        (baseline 0, no events) reads the echo as a refusal -> the verdicts differ."""
        live = self._session("pr36-l4-completion", _ECHO_AGENT % {"records": _record(False)})
        _sent, live_result = self._turn(live, _echo_prompt)
        self.assertEqual(live_result["state"], "COMPLETED", live_result)
        adopted = self._adopt(live)
        adopted_result = adopted.await_completion()
        self._assert_identical(live, live_result, adopted, adopted_result)
        rows = [r for r in adopted.journal.rows_for(live.intent_id)
                if r["kind"] == "EVENT" and r["event"] == "identity_bound"
                and (r.get("source_vocabulary") or {}).get("adopted") is True]
        self.assertEqual(len(rows), 1, rows)
        self.assertEqual(rows[0]["source_vocabulary"].get("settlement_baseline"), live._settlement_baseline, rows[0])

    def test_l4_live_and_adopted_settle_the_same_refusal(self) -> None:
        """`refusal_in_boundary` on BOTH sides, over the same baseline and provenance."""
        live = self._session("pr36-l4-refusal", _ECHO_AGENT % {"records": _record(False) + _record(True)})
        _sent, live_result = self._turn(live, _echo_prompt)
        self.assertEqual(live_result["state"], "FAILED", live_result)
        self.assertEqual((live_result.get("verdict") or {}).get("reason"), REFUSAL_IN_BOUNDARY)
        adopted = self._adopt(live)
        adopted_result = adopted.await_completion()
        self._assert_identical(live, live_result, adopted, adopted_result)
        self.assertEqual((adopted_result.get("verdict") or {}).get("reason"), REFUSAL_IN_BOUNDARY)

    def test_l4_the_live_session_persists_the_baseline_and_events_before_the_prompt_write(self) -> None:
        """The durable provenance exists the moment the prompt is written: ONE journal row
        with a closed vocabulary -- digests and transport facts, never the prompt -- readable
        by a stranger.  (i2: the i1 side record `capture.log.delivery.<inc>.json` no longer
        exists -- it carried the payload, REVIEW_BUGFIX F-001 -- so the row IS the record.)"""
        live = self._session("pr36-l4-durable", _ECHO_AGENT % {"records": _record(False)})
        _sent, _result = self._turn(live, _echo_prompt)
        rows = [r for r in live.journal.rows_for(live.intent_id)
                if r["kind"] == "EVENT" and r["event"] == "delivery_recorded"]
        self.assertEqual(len(rows), 1, [r["event"] for r in live.journal.rows_for(live.intent_id)])
        vocab = rows[0]["source_vocabulary"]
        self.assertEqual(vocab.get("baseline"), live._settlement_baseline, vocab)
        self.assertEqual(len(vocab.get("events") or ()), 1, vocab)
        event = vocab["events"][0]
        self.assertEqual(set(event), {"index", "offset", "payload_sha256", "payload_bytes", "transport", "at", "echo_proof"})
        self.assertEqual(event["payload_sha256"], _sha(live.delivery_events[0]["payload"].encode("utf-8")))
        self.assertEqual(event["echo_proof"], lifecycle.echo_proof(live.delivery_events[0]["payload"],
                                                                   live.delivery_events[0]["transport"]))
        self.assertEqual(event["echo_proof"]["class"], "echo_expected", event)
        self.assertNotIn("not logged in", json.dumps(rows[0]), "the journal carried the prompt text")
        # the prompt is on disk in exactly one file: the capture, where the line discipline
        # ECHOED it (that is the transport, not a record of ours)
        self.assertEqual(self._files_holding(_echo_prompt(live.session_id)),
                         [str(live.capture.path.relative_to(self.base))])
        # the i1 side record is GONE, not merely emptied
        self.assertEqual([p for p in live.capture.path.parent.iterdir() if ".delivery." in p.name], [])

    def test_l10_a_tampered_or_missing_echo_proof_on_adoption_fails_closed(self) -> None:
        """L-10 (fail-closed half): the adoption restores the proof, and a proof that does not
        verify against the capture -- a wrong form digest, or no proof at all -- resolves
        `echo_unproven` / `no_delivery`: NOTHING is excised (the echoed "not logged in" then
        fires R1 as it must), never a wider excision.  The live verdict is COMPLETED."""
        live = self._session("pr36-l10-tamper", _ECHO_AGENT % {"records": _record(False)})
        _sent, live_result = self._turn(live, _echo_prompt)
        self.assertEqual(live_result["state"], "COMPLETED", live_result)
        for how in ("wrong_digest", "no_proof", "wrong_length"):
            with self.subTest(how=how):
                adopted = self._adopt(live)
                self.assertEqual(adopted._settlement_baseline, live._settlement_baseline)
                self.assertEqual(len(adopted.delivery_events), 1)
                event = adopted.delivery_events[0]
                self.assertEqual(event["payload"], "")
                if how == "wrong_digest":
                    event["echo_proof"]["forms"][0]["sha256"] = "0" * 64
                elif how == "wrong_length":
                    event["echo_proof"]["forms"][0]["bytes"] += 1
                else:
                    del event["echo_proof"]
                result = adopted.await_completion()
                block = self._echo_block(adopted)
                self.assertEqual(block["spans"], [], block)
                self.assertIn(block["state"], ("echo_unproven", "no_delivery"), block)
                self.assertEqual(result["state"], "FAILED", "a tampered proof widened the excision")
                self.assertEqual((result.get("verdict") or {}).get("reason"), REFUSAL_IN_BOUNDARY)
                self.assertEqual((result["evidence"].get("settlement_range") or {}).get("echo"), block["state"])

    def test_l10_a_malformed_delivery_row_is_named_and_restores_no_event(self) -> None:
        """A `delivery_recorded` row whose event vocabulary is not the closed shape -- one that
        carries a `payload` key, say -- restores NO event, keeps the baseline, and is
        journalled `delivery_provenance_unrestored` by name."""
        live = self._session("pr36-l10-malformed", _ECHO_AGENT % {"records": _record(False)})
        stranger = rt.StandaloneSession(
            intent=dict(live.intent), profile=live.profile, artifact_base=live.artifact_base,
            run_id=live.run_id, journal=journal_mod.ExecutionJournal(live.artifact_base, live.run_id))
        stranger.session_id, stranger.incarnation = live.session_id, live.incarnation
        rows = [{"kind": "EVENT", "event": "delivery_recorded",
                 "source_vocabulary": {"baseline": 71, "delivery_mode": "post_ready_delivery",
                                       "events": [{"index": 0, "offset": 71, "payload": "leak", "transport": None,
                                                   "at": "", "echo_proof": None}]}}]
        out = stranger._restore_delivery(rows)
        self.assertEqual(out, {"restored": False, "reason": "delivery_recorded_events_malformed", "events": 0})
        self.assertEqual(stranger._settlement_baseline, 71)
        self.assertEqual(stranger.delivery_events, [])
        named = [r for r in stranger.journal.rows_for(live.intent_id)
                 if r.get("event") == "delivery_provenance_unrestored"]
        self.assertEqual(len(named), 1, named)
        self.assertEqual(named[0]["source_vocabulary"]["reason"], "delivery_recorded_events_malformed")


# =====================================================================================
# REVIEW_BUGFIX i1 F-001 -- no plaintext prompt at rest (L-7, L-8, L-9, L-10)
# =====================================================================================
#: the `launch_with_prompt` turn: readiness, the conjunctive delivery proof, a bound success --
#: the prompt arrives on ARGV (`$2`) and is never written to the tty
_ARGV_AGENT = """SID="$1"; PROMPT="$2"
printf '{"type":"system","session_id":"%%s"}\n' "$SID"
sleep 0.2
printf '{"type":"assistant","session_id":"%%s","request_id":"req_stub_agent_1","message":{"model":"stub-agent-model-1","id":"msg_stub_agent_1","usage":{"input_tokens":2,"output_tokens":1}}}\n' "$SID"
%(records)s
exit 0
"""

SENTINEL = "dcap_SENTINEL_9f3e7c2b1a0d4e6f8b9c0d1e2f3a4b5c6d7e8f90"


def _preamble_prompt(session_id: str) -> str:
    """The Orca dispatch preamble shape: a capability-token line, task / dispatch ids, the
    task block -- the exact material a real dispatch prompt carries."""
    return ("You are working inside Orca, a multi-agent IDE. You are a dispatched worker.\n"
            "Your task ID is: task_c83cfc63cbbc\n"
            "  orca orchestration send --from term_d5140739 --dispatch-capability " + SENTINEL + " \\\n"
            "    --type worker_done --task-id task_c83cfc63cbbc --dispatch-id ctx_7df0dad9c37a\n"
            "=== TASK ===\nReply with one result line bound to " + session_id + ".\n")


class PR36F001NoPlaintextAtRestTests(_StubTurn):
    """REVIEW_BUGFIX i1 F-001: a delivery's durable provenance must add no plaintext prompt at
    rest.  The i1 candidate wrote `capture.log.delivery.<inc>.json` with every event's full
    `payload` -- for `argv` deliveries a NEW copy of a prompt that is not in capture.log
    (the reviewer's `plaintext_probe.py`: `secret_in_capture=false,
    secret_in_delivery_record=true`).  RED on the i1 tree, GREEN after: the row carries
    digests and the echo class only, and every file the run wrote is scanned."""

    def _adopt(self, live: rt.StandaloneSession) -> rt.StandaloneSession:
        stranger = rt.StandaloneSession(
            intent=dict(live.intent), profile=live.profile, artifact_base=live.artifact_base,
            run_id=live.run_id, journal=journal_mod.ExecutionJournal(live.artifact_base, live.run_id))
        outcome = stranger.adopt(fence=live.fence)
        self.assertTrue(outcome["adopted"], outcome)
        return stranger

    def _argv_run(self, run_id: str, prompt_of) -> tuple[rt.StandaloneSession, dict, rt.StandaloneSession, dict]:
        live = self._session(run_id, _ARGV_AGENT % {"records": _record(False)}, delivery_mode="launch_with_prompt")
        prompt = prompt_of(live.session_id)
        out = self._argv_turn(live, prompt)
        self.assertEqual(out.get("outcome"), "succeeded", out)
        self.assertTrue(out.get("settled"), out)
        # the argv delivery event was recorded by the production path with its transport
        self.assertEqual([ev["transport"]["kind"] for ev in live.delivery_events], ["argv"])
        adopted = self._adopt(live)
        adopted_result = adopted.await_completion()
        live_result = live.await_completion()             # the same bound fence, re-read
        return live, live_result, adopted, adopted_result

    def _assert_absent_everywhere(self, secret: str, prompt: str) -> None:
        self.assertNotIn(secret, "", "sanity")
        self.assertEqual(self._files_holding(secret), [], "the sentinel secret is on disk")
        for line in [ln for ln in prompt.splitlines() if len(ln.strip()) >= 16]:
            self.assertEqual(self._files_holding(line.strip()), [], f"a prompt line is on disk: {line!r}")

    def _assert_live_adopted_identical(self, live, live_result, adopted, adopted_result) -> None:
        _assert_identical(self, live, live_result, adopted, adopted_result)

    def test_l7_an_argv_prompt_secret_is_absent_from_every_durable_artifact(self) -> None:
        """L-7: the sentinel from a `launch_with_prompt` prompt is in NO file under the run's
        base (capture.log, journal, meta, fence, release, members, last_message, the stub's
        worktree, ...) while live and adopted settle identically (COMPLETED both)."""
        secret = SENTINEL
        prompt_of = lambda sid: f"Do the task. Auth: {secret}. Reply with one bound result line for {sid}."  # noqa: E731
        live, live_result, adopted, adopted_result = self._argv_run("pr36-l7-argv", prompt_of)
        self._assert_absent_everywhere(secret, prompt_of(live.session_id))
        self.assertEqual(live_result["state"], "COMPLETED", live_result)
        self._assert_live_adopted_identical(live, live_result, adopted, adopted_result)
        # the journal row names the transport and the digest, and nothing else of the prompt
        row = [r for r in live.journal.rows_for(live.intent_id) if r.get("event") == "delivery_recorded"][-1]
        event = row["source_vocabulary"]["events"][0]
        self.assertEqual(set(event), {"index", "offset", "payload_sha256", "payload_bytes", "transport", "at", "echo_proof"})
        self.assertEqual(event["payload_sha256"], _sha(prompt_of(live.session_id).encode("utf-8")))
        self.assertEqual(event["echo_proof"], {"schema": "os48.echo_proof.v1", "class": "echo_absent",
                                               "reason": "argv_transport_cannot_echo", "forms": []})

    def test_l8_a_dispatch_capability_bearing_prompt_is_absent_from_every_durable_artifact(self) -> None:
        """L-8: the same with the Orca preamble shape (capability token line + task / dispatch
        ids + task block, multi-line)."""
        live, live_result, adopted, adopted_result = self._argv_run("pr36-l8-preamble", _preamble_prompt)
        self._assert_absent_everywhere(SENTINEL, _preamble_prompt(live.session_id))
        self.assertEqual(self._files_holding("ctx_7df0dad9c37a"), [])
        self.assertEqual(live_result["state"], "COMPLETED", live_result)
        self._assert_live_adopted_identical(live, live_result, adopted, adopted_result)

    def test_l9_an_adopted_argv_delivery_resolves_echo_absent_by_name(self) -> None:
        """L-9: the restored argv event carries its transport and proof and resolves
        `echo_absent` BY NAME (not `no_delivery`); live == adopted `echo` block and verdict."""
        live, live_result, adopted, adopted_result = self._argv_run("pr36-l9-absent", _preamble_prompt)
        self.assertEqual([ev["payload"] for ev in adopted.delivery_events], [""])
        self.assertEqual(adopted.delivery_events[0]["transport"]["kind"], "argv")
        self.assertEqual(adopted.delivery_events[0]["echo_proof"]["class"], "echo_absent")
        block = _echo_block(adopted)
        self.assertEqual(block["state"], "echo_absent", block)
        self.assertEqual(block["events"][0]["state"], "echo_absent", block)
        self.assertEqual(block["events"][0]["reason"], "argv_transport_cannot_echo", block)
        self.assertEqual(block, _echo_block(live))
        self.assertEqual((adopted_result["evidence"].get("settlement_range") or {}).get("echo"), "echo_absent")
        self.assertEqual(adopted_result["evidence"].get("settlement_range"), live_result["evidence"].get("settlement_range"))

    def test_l10_pty_echo_provenance_adds_no_byte_beyond_the_capture(self) -> None:
        """L-10 (exposure half, MEASURED): for `post_ready_delivery` + ECHO the prompt text is
        on disk in exactly ONE file -- capture.log, where the line discipline echoed it --
        and the persisted provenance is digests only (the row's proof forms are
        {sha256, bytes}; no file other than the capture holds the prompt or any of its
        lines)."""
        live = self._session("pr36-l10-echo", _ECHO_AGENT % {"records": _record(False)})
        prompt_of = lambda sid: _echo_prompt(sid) + " Auth: " + SENTINEL  # noqa: E731
        _sent, live_result = self._turn(live, prompt_of)
        self.assertEqual(live_result["state"], "COMPLETED", live_result)
        holders = self._files_holding(SENTINEL)
        self.assertEqual(holders, [str(live.capture.path.relative_to(self.base))],
                         f"the prompt is held by other files than the capture's own echo: {holders}")
        row = [r for r in live.journal.rows_for(live.intent_id) if r.get("event") == "delivery_recorded"][-1]
        event = row["source_vocabulary"]["events"][0]
        self.assertEqual(event["echo_proof"]["class"], "echo_expected")
        self.assertTrue(event["echo_proof"]["forms"], event)
        for form in event["echo_proof"]["forms"]:
            self.assertEqual(set(form), {"sha256", "bytes"})
            self.assertRegex(form["sha256"], r"^[0-9a-f]{64}$")
        # every persisted form digest is the digest of bytes that ARE in the capture (the echo)
        raw = live.capture.raw()
        for form in event["echo_proof"]["forms"]:
            length = form["bytes"]
            self.assertTrue(any(_sha(raw[i:i + length]) == form["sha256"]
                                for i in range(0, len(raw) - length + 1)), "a persisted form digest matches no capture span")
        # ... and the adoption excises exactly that span
        adopted = self._adopt(live)
        adopted_result = adopted.await_completion()
        _assert_identical(self, live, live_result, adopted, adopted_result)
        block = _echo_block(adopted)
        self.assertEqual(block["state"], "echo_proven", block)
        self.assertEqual(len(block["spans"]), 1, block)


class PR36F002ProofTransportBindingTests(_StubTurn):
    """REVIEW_BUGFIX_iteration2 F-002: the payload-less resolver trusted the proof's declared
    class independently of the recorded transport, so a forged `echo_expected` proof on an
    adopted `argv` event excised agent bytes whose digest it named (the reviewer's
    `forged_proof_probe.py`: `not logged in` removed before R1).  These locks drive the REAL
    adopt path over a journal whose `delivery_recorded` row was tampered (the row re-digested,
    as a same-user writer could), RED on the iteration-2 tree, GREEN after."""

    def _forge_row(self, live: rt.StandaloneSession, forge) -> None:
        """Rewrite the live journal's `delivery_recorded` row through ``forge(event_dict)`` and
        re-digest it (`record_digest`; the journal has no secret) so `rows()` still reads."""
        path = journal_mod.journal_path(live.artifact_base, live.run_id)
        lines = path.read_text(encoding="utf-8").splitlines()
        out, forged = [], 0
        for line in lines:
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get("intent_id") == live.intent_id and row.get("event") == "delivery_recorded":
                for ev in row["source_vocabulary"]["events"]:
                    forge(ev)
                row["digest"] = journal_mod.record_digest(row)
                forged += 1
            out.append(json.dumps(row, sort_keys=True, ensure_ascii=False))
        self.assertEqual(forged, 1)
        path.write_text("\n".join(out) + "\n", encoding="utf-8")

    def _adopt(self, live: rt.StandaloneSession) -> rt.StandaloneSession:
        stranger = rt.StandaloneSession(
            intent=dict(live.intent), profile=live.profile, artifact_base=live.artifact_base,
            run_id=live.run_id, journal=journal_mod.ExecutionJournal(live.artifact_base, live.run_id))
        outcome = stranger.adopt(fence=live.fence)
        self.assertTrue(outcome["adopted"], outcome)
        return stranger

    @staticmethod
    def _forged_expected(target: bytes):
        def forge(ev):
            ev["echo_proof"] = {"schema": lifecycle.ECHO_PROOF_SCHEMA, "class": "echo_expected", "reason": "",
                                "forms": [{"sha256": _sha(target), "bytes": len(target)}]}
        return forge

    def _argv_live(self, run_id: str, records: str):
        live = self._session(run_id, _ARGV_AGENT % {"records": records}, delivery_mode="launch_with_prompt")
        out = self._argv_turn(live, _preamble_prompt(live.session_id))
        self.assertTrue(out.get("settled"), out)
        live_result = live.await_completion()
        return live, live_result

    def _assert_unproven_no_span(self, session, reason: str) -> dict:
        block = _echo_block(session)
        self.assertEqual(block["state"], "echo_unproven", block)
        self.assertEqual(block["spans"], [], block)
        self.assertEqual(block["events"][0]["reason"], reason, block)
        return block

    def test_l11_a_forged_echo_expected_proof_on_argv_cannot_excise_a_refusal(self) -> None:
        """L-11: the agent prints the refusal prose `not logged in` and a bound success (live
        R1 -> FAILED `refusal_in_boundary`).  The journal row's argv proof is forged into an
        `echo_expected` proof whose digest names exactly the refusal bytes.  i2: the adoption
        resolved `echo_proven`, excised the refusal and COMPLETED; now: `echo_unproven` /
        `proof_class_contradicts_transport`, no span, the refusal survives, FAILED
        `refusal_in_boundary` -- identical to live."""
        live, live_result = self._argv_live(
            "pr36-l11-refusal", "printf 'not logged in\\n'\n" + _record(False))
        self.assertEqual(live_result["state"], "FAILED", live_result)
        self.assertEqual((live_result.get("verdict") or {}).get("reason"), REFUSAL_IN_BOUNDARY, live_result)
        n = int(live._boundary["offset_n"])
        raw = live.capture.raw()[:n]
        target = b"not logged in"
        self.assertIn(target, raw)
        self._forge_row(live, self._forged_expected(target))
        adopted = self._adopt(live)
        self.assertEqual(adopted.delivery_events[0]["echo_proof"]["class"], "echo_expected")   # the forgery arrived
        adopted_result = adopted.await_completion()
        self._assert_unproven_no_span(adopted, "proof_class_contradicts_transport")
        self.assertEqual(adopted_result["state"], "FAILED", adopted_result)
        self.assertEqual((adopted_result.get("verdict") or {}).get("reason"), REFUSAL_IN_BOUNDARY, adopted_result)
        self.assertEqual(adopted_result["evidence"]["provenance_outcome"], live_result["evidence"]["provenance_outcome"])
        self.assertEqual((adopted_result["evidence"].get("settlement_range") or {}).get("echo"), "echo_unproven")
        # the strip itself removes nothing from the fenced range
        events = tuple({**dict(ev), "offset": int(ev["offset"])} for ev in adopted.delivery_events)
        self.assertEqual(lifecycle.strip_delivery_echo(raw, events), raw)

    def test_l12_a_forged_echo_expected_proof_on_argv_cannot_excise_a_completion_record(self) -> None:
        """L-12: the same forgery aimed at the agent's bound `result` line.  i2: the record was
        excised and the adoption settled FAILED `no_completion_record` while live COMPLETED;
        now: unproven by name, the record stays, COMPLETED == live."""
        live, live_result = self._argv_live("pr36-l12-record", _record(False))
        self.assertEqual(live_result["state"], "COMPLETED", live_result)
        n = int(live._boundary["offset_n"])
        raw = live.capture.raw()[:n]
        line = next(seg for seg in raw.split(b"\n") if b'"type":"result"' in seg)
        target = line + b"\n"                                         # the record line, as captured
        self.assertIn(target, raw)
        self._forge_row(live, self._forged_expected(target))
        adopted = self._adopt(live)
        adopted_result = adopted.await_completion()
        self._assert_unproven_no_span(adopted, "proof_class_contradicts_transport")
        self.assertEqual(adopted_result["state"], "COMPLETED", adopted_result)
        self.assertEqual((adopted_result.get("verdict") or {}).get("outcome"), "succeeded")
        self.assertEqual(adopted_result["evidence"]["settlement_record"], live_result["evidence"]["settlement_record"])

    def test_l13_an_echo_absent_proof_on_an_echo_set_pty_write_is_unproven_by_name(self) -> None:
        """L-13 (the inverse): the ECHO turn's row is forged into a canonical `echo_absent`
        proof.  i2 trusted it (`echo_absent`, live `echo_proven` -> the echo blocks diverged);
        now `echo_unproven` / `proof_class_contradicts_transport`, no span -- the echoed
        "not logged in" then fires R1 on the adopted side exactly as it would for any unproven
        echo (fail closed).  The unforged row still resolves identically live and adopted."""
        live = self._session("pr36-l13-inverse", _ECHO_AGENT % {"records": _record(False)})
        _sent, live_result = self._turn(live, _echo_prompt)
        self.assertEqual(live_result["state"], "COMPLETED", live_result)
        self.assertEqual(_echo_block(live)["state"], "echo_proven")

        def forge(ev):
            ev["echo_proof"] = {"schema": lifecycle.ECHO_PROOF_SCHEMA, "class": "echo_absent",
                                "reason": "echo_flag_clear", "forms": []}
        self._forge_row(live, forge)
        adopted = self._adopt(live)
        adopted_result = adopted.await_completion()
        block = self._assert_unproven_no_span(adopted, "proof_class_contradicts_transport")
        self.assertEqual(adopted_result["state"], "FAILED", adopted_result)
        self.assertEqual((adopted_result.get("verdict") or {}).get("reason"), REFUSAL_IN_BOUNDARY)
        self.assertEqual((adopted_result["evidence"].get("settlement_range") or {}).get("echo"), block["state"])
        # ... and the UNFORGED row still resolves identically live and adopted (the consistent case)
        self._forge_row(live, lambda ev: ev.__setitem__("echo_proof", lifecycle.echo_proof(
            live.delivery_events[0]["payload"], live.delivery_events[0]["transport"])))
        restored = self._adopt(live)
        _assert_identical(self, live, live_result, restored, restored.await_completion())


class PR36F002ProofMatrixTests(unittest.TestCase):
    """L-14: the resolver unit matrix -- every class x transport x forms contradiction is
    `echo_unproven` with the NAMED reason and no span; the consistent cases resolve as before."""

    def _pty_transport(self, *, echo: bool, framed: bool = True):
        import pty as _pty
        import termios
        master, slave = _pty.openpty()
        self.addCleanup(lambda: [os.close(fd) for fd in (master, slave)])
        mode = termios.tcgetattr(slave)
        if echo:
            mode[3] |= termios.ECHO | termios.ICANON
        else:
            mode[3] &= ~termios.ECHO
        termios.tcsetattr(slave, termios.TCSANOW, mode)
        return pty_supervisor.echo_transport(master, kind="pty_write", framed=framed, cols=200)

    @staticmethod
    def _proof(klass, reason="", forms=()):
        return {"schema": lifecycle.ECHO_PROOF_SCHEMA, "class": klass, "reason": reason, "forms": list(forms)}

    def _resolve(self, raw, transport, proof):
        ev = ({"offset": 0, "payload": "", "transport": transport, "at": "", "echo_proof": proof},)
        return lifecycle.resolve_delivery_echo(raw, ev)

    def test_l14_every_contradiction_is_unproven_by_name_with_no_span(self) -> None:
        argv = pty_supervisor.echo_transport(None, kind="argv", framed=False, cols=80)
        echo_on = self._pty_transport(echo=True)
        echo_off = self._pty_transport(echo=False)
        unknown = {"kind": "pipe", "framed": False, "cols": 80, "termios": None, "read_at": ""}
        payload = "not logged in " + SENTINEL
        forms_on = lifecycle.expected_echo_forms(payload, echo_on)["forms"]
        agent = b"not logged in"
        raw = agent + b"\r\n" + forms_on[0] + b"\r\n"
        good_forms = [{"sha256": _sha(f), "bytes": len(f)} for f in forms_on]
        forged = [{"sha256": _sha(agent), "bytes": len(agent)}]
        cases = [
            # (transport, proof, expected NAMED reason)
            (argv, self._proof("echo_expected", "", forged), "proof_class_contradicts_transport"),
            (argv, self._proof("echo_unproven", "termios_unreadable"), "proof_class_contradicts_transport"),
            (argv, self._proof("echo_absent", "echo_flag_clear"), "proof_reason_contradicts_transport"),
            (argv, self._proof("echo_absent", "argv_transport_cannot_echo", forged), "proof_forms_contradict_class"),
            (echo_off, self._proof("echo_expected", "", forged), "proof_class_contradicts_transport"),
            (echo_off, self._proof("echo_absent", "argv_transport_cannot_echo"), "proof_reason_contradicts_transport"),
            (echo_off, self._proof("echo_unproven", "echonl_partial_echo"), "proof_class_contradicts_transport"),  # no ECHONL+ICANON here
            (echo_on, self._proof("echo_absent", "echo_flag_clear"), "proof_class_contradicts_transport"),
            (echo_on, self._proof("echo_absent", "argv_transport_cannot_echo"), "proof_class_contradicts_transport"),
            (echo_on, self._proof("echo_expected", ""), "proof_forms_contradict_class"),
            (echo_on, self._proof("echo_expected", "", good_forms * 9), "proof_forms_count_contradicts_transport"),
            (echo_on, self._proof("echo_unproven", "made_up_reason"), "proof_reason_contradicts_transport"),
            (unknown, self._proof("echo_expected", "", forged), "proof_class_contradicts_transport"),
            (unknown, self._proof("echo_absent", "argv_transport_cannot_echo"), "proof_class_contradicts_transport"),
            (unknown, self._proof("echo_unproven", "transport_kind_unknown"), "transport_kind_unknown"),
            (echo_on, {**self._proof("echo_expected", "", good_forms), "schema": "os48.echo_proof.v0"}, "proof_unrecorded"),
            (echo_on, self._proof("echo_whatever", "", good_forms), "proof_unrecorded"),
        ]
        for transport, proof, reason in cases:
            with self.subTest(kind=transport.get("kind"), klass=proof.get("class"), reason=reason):
                res = self._resolve(raw, transport, proof)
                self.assertEqual(res["state"], "echo_unproven", res)
                self.assertEqual(res["spans"], (), res)
                self.assertEqual(res["events"][0]["reason"], reason, res)
                self.assertEqual(lifecycle.strip_delivery_echo(
                    raw, ({"offset": 0, "payload": "", "transport": transport, "at": "", "echo_proof": proof},)), raw)
        # the CONSISTENT cases, unchanged
        absent = self._resolve(raw, argv, self._proof("echo_absent", "argv_transport_cannot_echo"))
        self.assertEqual((absent["state"], absent["events"][0]["reason"]), ("echo_absent", "argv_transport_cannot_echo"))
        absent_off = self._resolve(raw, echo_off, self._proof("echo_absent", "echo_flag_clear"))
        self.assertEqual((absent_off["state"], absent_off["events"][0]["reason"]), ("echo_absent", "echo_flag_clear"))
        proven = self._resolve(raw, echo_on, self._proof("echo_expected", "", good_forms))
        live = lifecycle.resolve_delivery_echo(raw, ({"offset": 0, "payload": payload, "transport": echo_on, "at": ""},))
        self.assertEqual(live["state"], "echo_proven", live)
        self.assertEqual((proven["state"], proven["spans"]), (live["state"], live["spans"]))
        # uniqueness still decides: the form twice -> ambiguous on both sides
        raw2 = forms_on[0] + b"\r\n" + forms_on[0] + b"\r\n"
        amb = self._resolve(raw2, echo_on, self._proof("echo_expected", "", good_forms))
        self.assertEqual((amb["state"], amb["reason"]), ("echo_unproven", "event[0]:ambiguous_multiple_matches"))
        # the recorded proof of every live transport passes its own binding (live == adopted)
        for transport in (argv, echo_on, echo_off):
            with self.subTest(consistent=transport.get("kind"), echo=(transport.get("termios") or {}).get("echo")):
                proof = lifecycle.echo_proof(payload, transport)
                res = self._resolve(raw, transport, proof)
                live_res = lifecycle.resolve_delivery_echo(raw, ({"offset": 0, "payload": payload, "transport": transport, "at": ""},))
                self.assertEqual((res["state"], res["reason"], res["spans"]), (live_res["state"], live_res["reason"], live_res["spans"]))

    def test_l14_the_reviewers_forged_proof_probe_keeps_the_refusal(self) -> None:
        """The reviewer's `forged_proof_probe.py`, verbatim shape."""
        refusal = b"not logged in"
        raw = refusal + b'\n{"type":"result","is_error":false}\n'
        event = {"offset": 0, "payload": "", "transport": {"kind": "argv", "framed": False, "termios": None, "cols": 80},
                 "at": "", "echo_proof": {"schema": lifecycle.ECHO_PROOF_SCHEMA, "class": "echo_expected", "reason": "",
                                          "forms": [{"sha256": _sha(refusal), "bytes": len(refusal)}]}}
        result = lifecycle.resolve_delivery_echo(raw, (event,))
        self.assertEqual((result["state"], result["spans"]), ("echo_unproven", ()), result)
        self.assertEqual(result["events"][0]["reason"], "proof_class_contradicts_transport")
        self.assertIn(refusal, lifecycle.strip_delivery_echo(raw, (event,)))


class PR36F001EchoProofResolverTests(unittest.TestCase):
    """Unit-level: `resolve_delivery_echo` over a payload-less event carrying a digest-only
    `echo_proof` yields the SAME block the payload yields -- anchored (framed) and unframed
    (bounded full scan); the scan budget and a malformed proof fail closed by name."""

    def _transport(self, *, framed: bool):
        import pty as _pty
        import termios
        master, slave = _pty.openpty()
        self.addCleanup(lambda: [os.close(fd) for fd in (master, slave)])
        mode = termios.tcgetattr(slave)
        mode[3] |= termios.ECHO | termios.ICANON
        termios.tcsetattr(slave, termios.TCSANOW, mode)
        return pty_supervisor.echo_transport(master, kind="pty_write", framed=framed, cols=200)

    def _blocks(self, raw: bytes, payload: str, transport, offset: int = 0):
        live = ({"offset": offset, "payload": payload, "transport": transport, "at": ""},)
        proof = lifecycle.echo_proof(payload, transport)
        restored = ({"offset": offset, "payload": "", "transport": transport, "at": "", "echo_proof": proof},)
        return lifecycle.resolve_delivery_echo(raw, live), lifecycle.resolve_delivery_echo(raw, restored), proof

    def test_a_framed_proof_resolves_the_same_span_as_the_payload(self) -> None:
        transport = self._transport(framed=True)
        payload = _echo_prompt("sid-unit") + " Auth: " + SENTINEL
        forms = lifecycle.expected_echo_forms(payload, transport)["forms"]
        raw = b"prelude\r\n" + forms[0] + b"\r\n{\"type\":\"result\"}\r\n"
        a, b, proof = self._blocks(raw, payload, transport)
        self.assertEqual(a["state"], "echo_proven", a)
        self.assertEqual((b["state"], b["reason"], b["spans"]), (a["state"], a["reason"], a["spans"]))
        self.assertEqual([(e["state"], e["reason"], e["span"], e["forms"]) for e in b["events"]],
                         [(e["state"], e["reason"], e["span"], e["forms"]) for e in a["events"]])
        self.assertNotIn(SENTINEL.encode(), json.dumps(proof).encode())
        self.assertEqual(set(proof), {"schema", "class", "reason", "forms"})

    def test_an_unframed_proof_resolves_by_bounded_full_scan(self) -> None:
        transport = self._transport(framed=False)
        payload = "unframed line " + SENTINEL
        forms = lifecycle.expected_echo_forms(payload, transport)["forms"]
        raw = b"x" * 300 + forms[0] + b"\r\nrest\r\n"
        a, b, _proof = self._blocks(raw, payload, transport)
        self.assertEqual(a["state"], "echo_proven")
        self.assertEqual((b["state"], b["spans"]), (a["state"], a["spans"]))
        # ambiguity is decided identically: the form twice -> unproven on both sides
        raw2 = forms[0] + b"\r\n" + forms[0] + b"\r\n"
        a2, b2, _p = self._blocks(raw2, payload, transport)
        self.assertEqual((a2["state"], a2["reason"]), ("echo_unproven", "event[0]:ambiguous_multiple_matches"))
        self.assertEqual((b2["state"], b2["reason"]), (a2["state"], a2["reason"]))

    def test_the_scan_budget_and_a_malformed_proof_fail_closed_by_name(self) -> None:
        transport = self._transport(framed=False)
        payload = "p " + SENTINEL
        forms = lifecycle.expected_echo_forms(payload, transport)["forms"]
        raw = b"y" * 1000 + forms[0]
        with patch.object(lifecycle, "ECHO_PROOF_SCAN_BUDGET_BYTES", 10):
            _a, b, _p = self._blocks(raw, payload, transport)
        self.assertEqual((b["state"], b["reason"]), ("echo_unproven", "event[0]:proof_scan_bounded"))
        self.assertEqual(b["spans"], ())
        proof = lifecycle.echo_proof(payload, transport)
        for broken in ({**proof, "forms": [{"sha256": "0" * 64, "bytes": forms[0].__len__()}]},
                       {**proof, "class": "echo_expected", "forms": []},
                       {**proof, "schema": "other"}, {**proof, "forms": [{"sha256": "zz", "bytes": 1}]}, None):
            with self.subTest(broken=str(broken)[:60]):
                ev = ({"offset": 0, "payload": "", "transport": transport, "at": "", "echo_proof": broken},)
                res = lifecycle.resolve_delivery_echo(raw, ev)
                self.assertIn(res["state"], ("echo_unproven", "no_delivery"), res)
                self.assertEqual(res["spans"], ())
        absent = lifecycle.resolve_delivery_echo(raw, ({"offset": 0, "payload": "", "at": "",
                                                        "transport": pty_supervisor.echo_transport(None, kind="argv", framed=False, cols=80),
                                                        "echo_proof": lifecycle.echo_proof("secret", {"kind": "argv"})},))
        self.assertEqual(absent["state"], "echo_absent", absent)
        self.assertEqual(absent["events"][0]["reason"], "argv_transport_cannot_echo")


# =====================================================================================
# L-6 -- both implementation copies byte-identical
# =====================================================================================
class PR36L6ParityTests(unittest.TestCase):
    MODULES = ("standalone_runtime.py", "standalone_capture.py", "standalone_pty.py",
               "standalone_drivers.py", "standalone_journal.py", "standalone_lifecycle.py")

    def test_l6_the_deploy_copy_and_the_mirror_are_byte_identical(self) -> None:
        deploy = REPO / "orca-worker-reviewer-orchestration" / "tools" / "deterministic_workflow"
        mirror = REPO / "scripts" / "deterministic_workflow"
        for name in self.MODULES:
            with self.subTest(module=name):
                self.assertEqual((deploy / name).read_bytes(), (mirror / name).read_bytes(),
                                 f"{name}: the deploy copy and the mirror differ")


if __name__ == "__main__":
    unittest.main()
