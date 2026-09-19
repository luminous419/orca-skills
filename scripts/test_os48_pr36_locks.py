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
* REVIEW_BUGFIX_iteration2 F-002 (iteration 3; L-11..L-14; the MECHANISM below is SUPERSEDED by
  run_c296ff67c325 -- next bullet -- the locks are retained / rewritten).  A restored proof was BOUND to the
  recorded transport before any digest is compared (`lifecycle._bound_proof` /
  `transport_echo_capability`): a transport that proves absence (`argv`, ECHO-clear pty) accepts
  only a canonical `echo_absent` proof; an echo-possible transport accepts only `echo_expected`
  with 1..TAB_STOP forms; every class / reason / forms contradiction is `echo_unproven` with a
  NAMED reason and no span.  A forged `echo_expected` proof on an adopted argv event whose digest
  matches agent refusal- or completion-shaped bytes excises nothing (R1 holds; live == adopted
  verdict), the inverse (`echo_absent` on an ECHO-set pty) is unproven by name, and the two
  consistent cases resolve exactly as before.

* run_c296ff67c325 -- REVIEW_BUGFIX_iteration3 F-003 under the USER DECISION that narrows PR36-4
  (`artifacts/runs/run_c296ff67c325/ORIGINAL_REQUEST.md`: "Adoption does not guarantee the same
  availability as live.  Adopted success must be narrower than or equal to live success, and
  adoption may not remove unauthenticated evidence to produce a success verdict").  The
  `delivery_recorded` row is UNKEYED (a same-user writer re-digests it), so NOTHING in it is
  excision authority: the `echo_proof` producer is gone, the row carries offset / transport /
  payload digest + length (diagnostic only), and a restored event resolves from its transport
  KIND alone -- `argv` `echo_absent` by structure, `pty_write` `echo_unproven` /
  `payload_unobserved` -- excising nothing.  Live is unchanged (it excises only an echo it
  observed itself, payload in memory).  Consequence, accepted: adopted may be STRICTER than live
  (the echoed refusal phrase fires R1 -> FAILED `refusal_in_boundary`; the echoed JSON example is
  a framing candidate -> LOST `record_framing_ambiguous`); impossible: an adopted COMPLETED that
  live would not produce, and any adopted excision at all.  Every earlier lock whose
  expectation was "live == adopted echo block / verdict" on an ECHO transport is REWRITTEN below
  to the new contract with a note naming the decision (L-4, L-10, L-13, L-14, the resolver
  units); none is deleted.  New locks: F3-L1 (forged / stale / tampered rows have no effect on
  adopted excision, via the REAL adopt path and the reviewer's probe verbatim), F3-L2 (the
  transport x output matrix: adopted COMPLETED => live COMPLETED, stricter outcomes by name),
  F3-L3 (argv: live == adopted, unchanged), F3-L4 (pty_write + ECHO possible / unreadable ->
  `echo_unproven` by name, no span, whatever the row carries), F3-L5 (refusal-like prompt and
  result-JSON example never a false COMPLETED on adoption).

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
FRAMING_AMBIGUOUS = "record_framing_ambiguous"
#: run_c296ff67c325 (F-003): the NAMED reason a restored (payload-less) `pty_write` event is
#: unproven, and the closed row vocabulary WITHOUT `echo_proof` -- literals, so 92d8432 fails on
#: behaviour (a proven span / a wider verdict), never on a missing attribute
PAYLOAD_UNOBSERVED = "payload_unobserved"
ROW_KEYS = {"index", "offset", "payload_sha256", "payload_bytes", "transport", "at"}
#: the pre-decision proof shape 92d8432 wrote and trusted; forged rows below carry it VERBATIM
ECHO_PROOF_SCHEMA = "os48.echo_proof.v1"
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
    transport kind + ECHO flag (REVIEW_BUGFIX i1 F-001: the payload itself is never restored;
    run_c296ff67c325 F-003: there is no `echo_proof` any more -- the row's digests are
    diagnostic and no side compares them as authority)."""
    out = []
    for ev in session.delivery_events:
        transport = ev.get("transport") or {}
        out.append((int(ev["offset"]), transport.get("kind"), (transport.get("termios") or {}).get("echo")))
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


def _assert_same_range(case: unittest.TestCase, live, adopted) -> None:
    case.assertEqual(int(adopted._settlement_baseline or 0), int(live._settlement_baseline or 0),
                     "the adopted session restored a different settlement baseline")
    case.assertEqual(_provenance(adopted), _provenance(live),
                     "the adopted session restored different delivery provenance")
    case.assertTrue(all(ev.get("payload") == "" for ev in adopted.delivery_events),
                    "the adoption restored a payload (F-001)")


def _assert_identical(case: unittest.TestCase, live, live_result, adopted, adopted_result) -> None:
    """live == adopted: baseline, provenance, echo block, verdict, evidence.  Holds for the
    `argv` path (F3-L3: `echo_absent` by structure on both sides); an ECHO transport is
    compared with `_assert_not_wider` instead (run_c296ff67c325)."""
    _assert_same_range(case, live, adopted)
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


def _assert_not_wider(case: unittest.TestCase, live, live_result, adopted, adopted_result) -> dict:
    """run_c296ff67c325 (USER DECISION): the adopted settlement reads the SAME [baseline, N)
    with the same (payload-less) provenance, EXCISES NOTHING (no `echo_proven`, no span) and
    is never wider than the live one: adopted COMPLETED => live COMPLETED on the same record.
    Returns the adopted echo block for the caller's named assertions."""
    _assert_same_range(case, live, adopted)
    block = _echo_block(adopted)
    case.assertNotEqual(block["state"], "echo_proven", f"the adoption excised an echo: {block}")
    case.assertEqual(block["spans"], [], f"the adoption produced a span: {block}")
    for entry in block["events"]:
        case.assertIsNone(entry["span"], block)
    le, ae = live_result["evidence"], adopted_result["evidence"]
    case.assertEqual((ae.get("boundary") or {}).get("offset_n"), (le.get("boundary") or {}).get("offset_n"))
    case.assertEqual(((ae.get("boundary") or {}).get("fence") or {}).get("boundary"),
                     ((le.get("boundary") or {}).get("fence") or {}).get("boundary"))
    rng = ae.get("settlement_range") or {}
    case.assertEqual(rng.get("baseline"), (le.get("settlement_range") or {}).get("baseline"))
    case.assertNotEqual(rng.get("echo"), "echo_proven", rng)
    case.assertEqual(rng.get("echo"), block["state"], (rng, block))
    if adopted_result["state"] == "COMPLETED":
        case.assertEqual(live_result["state"], "COMPLETED",
                         f"adopted COMPLETED where live did not: {live_result} / {adopted_result}")
        case.assertEqual(ae.get("settlement_record"), le.get("settlement_record"))
    return block


def _forge_row(case: unittest.TestCase, live: rt.StandaloneSession, forge, *, expect_rows: int = 1) -> None:
    """Rewrite the live journal's `delivery_recorded` row through ``forge(event_dict)`` and
    re-digest it (`record_digest`; the journal has no secret) so `rows()` still reads -- the
    same-user tampering F-002 / F-003 name."""
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
    case.assertEqual(forged, expect_rows)
    path.write_text("\n".join(out) + "\n", encoding="utf-8")


def _stranger(live: rt.StandaloneSession) -> rt.StandaloneSession:
    return rt.StandaloneSession(
        intent=dict(live.intent), profile=live.profile, artifact_base=live.artifact_base,
        run_id=live.run_id, journal=journal_mod.ExecutionJournal(live.artifact_base, live.run_id))


class PR36F4AdoptionRestoresProvenanceTests(_StubTurn):
    """The REAL adopt path over the journal a LIVE session wrote: the same run, settled live
    and then reconstructed by a stranger session (`adopt(fence=...)` -> masterless
    `drain_after_exit` -> `completion`) reads the same baseline and the same (payload-less)
    provenance.  c9b8d04 adopted baseline 0 and an empty event list.

    run_c296ff67c325 (USER DECISION, ORIGINAL_REQUEST.md): the i1-i3 expectation "same echo
    block, same verdict" on the ECHO transport is REWRITTEN -- the restored events carry no
    excision authority, so the adopted ECHO turn is `echo_unproven` / `payload_unobserved`,
    excises nothing and settles STRICTER (FAILED `refusal_in_boundary` on the echoed "not
    logged in") where live COMPLETED; adopted COMPLETED => live COMPLETED always holds."""

    def _adopt(self, live: rt.StandaloneSession) -> rt.StandaloneSession:
        stranger = _stranger(live)
        outcome = stranger.adopt(fence=live.fence)
        self.assertTrue(outcome["adopted"], outcome)
        return stranger

    @staticmethod
    def _echo_block(session) -> dict:
        return _echo_block(session)

    def _assert_stricter_refusal(self, live, live_result, adopted, adopted_result) -> dict:
        block = _assert_not_wider(self, live, live_result, adopted, adopted_result)
        self.assertEqual(block["state"], "echo_unproven", block)
        self.assertEqual(block["events"][0]["reason"], PAYLOAD_UNOBSERVED, block)
        self.assertEqual(adopted_result["state"], "FAILED", adopted_result)
        self.assertEqual((adopted_result.get("verdict") or {}).get("reason"), REFUSAL_IN_BOUNDARY, adopted_result)
        rng = adopted_result["evidence"].get("settlement_range") or {}
        self.assertEqual((rng.get("echo"), rng.get("echo_reason"), rng.get("delivery_events")),
                         ("echo_unproven", "event[0]:" + PAYLOAD_UNOBSERVED, 1), rng)
        return block

    def test_l4_the_adopted_echo_turn_restores_the_range_and_settles_no_wider_than_live(self) -> None:
        """(i1-i3: `test_l4_live_and_adopted_settle_the_same_completion`; rewritten under the
        run_c296ff67c325 USER DECISION.)  The ECHO turn: live excises the echo it observed and
        COMPLETES; the adoption restores the baseline and the event but excises NOTHING (no
        payload in memory), so the echoed "not logged in" fires R1 -> FAILED
        `refusal_in_boundary`: stricter than live, never wider.  92d8432 resolved the restored
        proof `echo_proven` and COMPLETED (RED on the named outcome)."""
        live = self._session("pr36-l4-completion", _ECHO_AGENT % {"records": _record(False)})
        _sent, live_result = self._turn(live, _echo_prompt)
        self.assertEqual(live_result["state"], "COMPLETED", live_result)
        self.assertEqual(_echo_block(live)["state"], "echo_proven")           # live: unchanged
        adopted = self._adopt(live)
        adopted_result = adopted.await_completion()
        self._assert_stricter_refusal(live, live_result, adopted, adopted_result)
        self.assertEqual((adopted_result["evidence"].get("refusal") or {}).get("source"), "free_text",
                         adopted_result["evidence"])
        rows = [r for r in adopted.journal.rows_for(live.intent_id)
                if r["kind"] == "EVENT" and r["event"] == "identity_bound"
                and (r.get("source_vocabulary") or {}).get("adopted") is True]
        self.assertEqual(len(rows), 1, rows)
        self.assertEqual(rows[0]["source_vocabulary"].get("settlement_baseline"), live._settlement_baseline, rows[0])
        self.assertEqual(rows[0]["source_vocabulary"].get("delivery_events_restored"), 1, rows[0])
        self.assertEqual(rows[0]["source_vocabulary"].get("delivery_provenance"), "restored", rows[0])

    def test_l4_live_and_adopted_settle_the_same_refusal(self) -> None:
        """`refusal_in_boundary` on BOTH sides, over the same baseline and provenance (the
        agent's own bound refusal dominates on both; run_c296ff67c325: the echo blocks now
        differ by design -- live `echo_proven`, adopted `echo_unproven` -- and the verdicts
        still agree)."""
        live = self._session("pr36-l4-refusal", _ECHO_AGENT % {"records": _record(False) + _record(True)})
        _sent, live_result = self._turn(live, _echo_prompt)
        self.assertEqual(live_result["state"], "FAILED", live_result)
        self.assertEqual((live_result.get("verdict") or {}).get("reason"), REFUSAL_IN_BOUNDARY)
        adopted = self._adopt(live)
        adopted_result = adopted.await_completion()
        self._assert_stricter_refusal(live, live_result, adopted, adopted_result)
        self.assertEqual(adopted_result["evidence"]["provenance_outcome"], live_result["evidence"]["provenance_outcome"])

    def test_l4_the_live_session_persists_the_baseline_and_events_before_the_prompt_write(self) -> None:
        """The durable provenance exists the moment the prompt is written: ONE journal row
        with a closed vocabulary -- the offset, the transport, the payload digest + length,
        never the prompt -- readable by a stranger.  (i2: the i1 side record is gone;
        run_c296ff67c325: the row carries NO `echo_proof` -- no digest of any echo form, nothing
        a future reader could take for excision authority.)"""
        live = self._session("pr36-l4-durable", _ECHO_AGENT % {"records": _record(False)})
        _sent, _result = self._turn(live, _echo_prompt)
        rows = [r for r in live.journal.rows_for(live.intent_id)
                if r["kind"] == "EVENT" and r["event"] == "delivery_recorded"]
        self.assertEqual(len(rows), 1, [r["event"] for r in live.journal.rows_for(live.intent_id)])
        vocab = rows[0]["source_vocabulary"]
        self.assertEqual(vocab.get("baseline"), live._settlement_baseline, vocab)
        self.assertEqual(len(vocab.get("events") or ()), 1, vocab)
        event = vocab["events"][0]
        self.assertEqual(set(event), ROW_KEYS, "the row's vocabulary is not the closed post-decision shape")
        self.assertEqual(event["payload_sha256"], _sha(live.delivery_events[0]["payload"].encode("utf-8")))
        self.assertEqual(event["transport"]["kind"], "pty_write")
        self.assertTrue(event["transport"]["termios"]["echo"], event)
        dumped = json.dumps(rows[0])
        self.assertNotIn("echo_proof", dumped, "the row still carries the pre-decision proof")
        self.assertNotIn('"forms"', dumped, "the row still carries echo-form digests")
        self.assertNotIn("not logged in", dumped, "the journal carried the prompt text")
        # the prompt is on disk in exactly one file: the capture, where the line discipline
        # ECHOED it (that is the transport, not a record of ours)
        self.assertEqual(self._files_holding(_echo_prompt(live.session_id)),
                         [str(live.capture.path.relative_to(self.base))])
        # the i1 side record is GONE, not merely emptied
        self.assertEqual([p for p in live.capture.path.parent.iterdir() if ".delivery." in p.name], [])

    def test_l10_a_tampered_row_on_adoption_excises_nothing(self) -> None:
        """(i2: `test_l10_a_tampered_or_missing_echo_proof_on_adoption_fails_closed`; rewritten
        under run_c296ff67c325 -- there is no proof to tamper.)  F3-L1 / F3-L4 through the REAL
        adopt path over a re-digested row: whatever the row is made to say -- the ECHO flag
        cleared, the termios dropped, the kind rewritten to `argv`, the payload digest + length
        re-pointed at the refusal bytes, the pre-decision `echo_proof` shape re-added with forms
        naming the refusal -- the adoption excises NOTHING (no `echo_proven`, no span) and the
        echoed "not logged in" fires R1: FAILED `refusal_in_boundary`, never the live
        COMPLETED.  Named per variant: `payload_unobserved` for every `pty_write` row,
        `echo_absent` (structural, still no span) for the `argv` rewrite, `no_delivery` for the
        malformed pre-decision shape (restores no event)."""
        live = self._session("pr36-l10-tamper", _ECHO_AGENT % {"records": _record(False)})
        _sent, live_result = self._turn(live, _echo_prompt)
        self.assertEqual(live_result["state"], "COMPLETED", live_result)
        refusal = b"not logged in"
        echo_form = lifecycle.expected_echo_forms(live.delivery_events[0]["payload"], live.delivery_events[0]["transport"])["forms"][0]

        def echo_cleared(ev): ev["transport"]["termios"]["echo"] = False
        def termios_dropped(ev): ev["transport"]["termios"] = None
        def kind_argv(ev): ev["transport"]["kind"] = "argv"; ev["transport"]["framed"] = False; ev["transport"]["termios"] = None
        def digest_refusal(ev): ev["payload_sha256"] = _sha(refusal); ev["payload_bytes"] = len(refusal)
        def proof_readded(ev):
            ev["transport"]["framed"] = False           # the unframed scan 92d8432 ran budget-bounded over every position
            ev["echo_proof"] = {"schema": ECHO_PROOF_SCHEMA, "class": "echo_expected", "reason": "",
                                "forms": [{"sha256": _sha(refusal), "bytes": len(refusal)}]}
        def proof_genuine(ev):
            ev["echo_proof"] = {"schema": ECHO_PROOF_SCHEMA, "class": "echo_expected", "reason": "",
                                "forms": [{"sha256": _sha(echo_form), "bytes": len(echo_form)}]}
        variants = [("echo_flag_cleared", echo_cleared, "echo_unproven", PAYLOAD_UNOBSERVED, 1),
                    ("termios_dropped", termios_dropped, "echo_unproven", PAYLOAD_UNOBSERVED, 1),
                    ("kind_rewritten_argv", kind_argv, "echo_absent", "argv_transport_cannot_echo", 1),
                    ("digest_names_refusal", digest_refusal, "echo_unproven", PAYLOAD_UNOBSERVED, 1),
                    ("pre_decision_proof_forged_to_refusal", proof_readded, "no_delivery", "", 0),
                    ("pre_decision_proof_genuine", proof_genuine, "no_delivery", "", 0)]
        original = journal_mod.journal_path(live.artifact_base, live.run_id).read_bytes()
        for name, forge, state, reason, restored in variants:
            with self.subTest(tamper=name):
                journal_mod.journal_path(live.artifact_base, live.run_id).write_bytes(original)
                _forge_row(self, live, forge)
                adopted = self._adopt(live)
                result = adopted.await_completion()
                # behaviour first: no wider settlement, no span, every agent byte survives
                self.assertEqual(result["state"], "FAILED", f"a tampered row widened the settlement: {result}")
                self.assertEqual((result.get("verdict") or {}).get("reason"), REFUSAL_IN_BOUNDARY, result)
                block = self._echo_block(adopted)
                self.assertEqual(block["spans"], [], f"the tampered row excised a span: {block}")
                baseline, n, fenced = self._fenced(adopted)
                self.assertIn(refusal, fenced)
                self.assertIn(echo_form, fenced)          # every byte survives -- the real echo too
                # then the names
                self.assertEqual(adopted._settlement_baseline, live._settlement_baseline)
                self.assertEqual(len(adopted.delivery_events), restored, adopted.delivery_events)
                self.assertEqual(block["state"], state, block)
                if restored:
                    self.assertEqual(block["events"][0]["reason"], reason, block)
                    self.assertEqual(adopted.delivery_events[0]["payload"], "")
                    self.assertNotIn("echo_proof", adopted.delivery_events[0])
                rng = result["evidence"].get("settlement_range") or {}
                self.assertEqual((rng.get("echo"), rng.get("delivery_events")), (state, restored), rng)

    def test_l10_a_malformed_delivery_row_is_named_and_restores_no_event(self) -> None:
        """A `delivery_recorded` row whose event vocabulary is not the closed shape -- one that
        carries a `payload` key, or the pre-decision `echo_proof` key (run_c296ff67c325) --
        restores NO event, keeps the baseline, and is journalled
        `delivery_provenance_unrestored` by name."""
        live = self._session("pr36-l10-malformed", _ECHO_AGENT % {"records": _record(False)})
        transport = {"kind": "pty_write", "framed": True, "cols": 80, "termios": None, "read_at": ""}
        shapes = {
            "payload_key": {"index": 0, "offset": 71, "payload": "leak", "transport": transport, "at": ""},
            "pre_decision_echo_proof_key": {"index": 0, "offset": 71, "payload_sha256": "0" * 64, "payload_bytes": 4,
                                            "transport": transport, "at": "",
                                            "echo_proof": {"schema": ECHO_PROOF_SCHEMA, "class": "echo_absent",
                                                           "reason": "echo_flag_clear", "forms": []}},
        }
        for index, (name, event) in enumerate(shapes.items()):
            with self.subTest(shape=name):
                stranger = _stranger(live)
                stranger.session_id, stranger.incarnation = live.session_id, live.incarnation
                rows = [{"kind": "EVENT", "event": "delivery_recorded",
                         "source_vocabulary": {"baseline": 71, "delivery_mode": "post_ready_delivery", "events": [event]}}]
                out = stranger._restore_delivery(rows)
                self.assertEqual(out, {"restored": False, "reason": "delivery_recorded_events_malformed", "events": 0})
                self.assertEqual(stranger._settlement_baseline, 71)
                self.assertEqual(stranger.delivery_events, [])
                named = [r for r in stranger.journal.rows_for(live.intent_id)
                         if r.get("event") == "delivery_provenance_unrestored"]
                self.assertEqual(len(named), index + 1, named)          # one per malformed shape, same journal
                self.assertEqual(named[-1]["source_vocabulary"]["reason"], "delivery_recorded_events_malformed")
                # the well-formed post-decision row restores the event WITHOUT any proof key
                good = {"index": 0, "offset": 71, "payload_sha256": "0" * 64, "payload_bytes": 4, "transport": transport, "at": ""}
                rows[0]["source_vocabulary"]["events"] = [good]
                fresh = _stranger(live)
                fresh.session_id, fresh.incarnation = live.session_id, live.incarnation
                self.assertEqual(fresh._restore_delivery(rows), {"restored": True, "reason": "", "events": 1})
                self.assertEqual(fresh.delivery_events, [{"offset": 71, "payload": "", "transport": transport, "at": ""}])


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
        stranger = _stranger(live)
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
        # (run_c296ff67c325: and no `echo_proof` -- the closed post-decision vocabulary)
        row = [r for r in live.journal.rows_for(live.intent_id) if r.get("event") == "delivery_recorded"][-1]
        event = row["source_vocabulary"]["events"][0]
        self.assertEqual(set(event), ROW_KEYS)
        self.assertEqual(event["payload_sha256"], _sha(prompt_of(live.session_id).encode("utf-8")))
        self.assertEqual(event["transport"]["kind"], "argv")

    def test_l8_a_dispatch_capability_bearing_prompt_is_absent_from_every_durable_artifact(self) -> None:
        """L-8: the same with the Orca preamble shape (capability token line + task / dispatch
        ids + task block, multi-line)."""
        live, live_result, adopted, adopted_result = self._argv_run("pr36-l8-preamble", _preamble_prompt)
        self._assert_absent_everywhere(SENTINEL, _preamble_prompt(live.session_id))
        self.assertEqual(self._files_holding("ctx_7df0dad9c37a"), [])
        self.assertEqual(live_result["state"], "COMPLETED", live_result)
        self._assert_live_adopted_identical(live, live_result, adopted, adopted_result)

    def test_l9_an_adopted_argv_delivery_resolves_echo_absent_by_name(self) -> None:
        """L-9 / F3-L3: the restored argv event carries its transport (no proof --
        run_c296ff67c325) and resolves `echo_absent` BY NAME, STRUCTURALLY (not `no_delivery`);
        live == adopted `echo` block and verdict -- the argv path is unchanged."""
        live, live_result, adopted, adopted_result = self._argv_run("pr36-l9-absent", _preamble_prompt)
        self.assertEqual([ev["payload"] for ev in adopted.delivery_events], [""])
        self.assertEqual(adopted.delivery_events[0]["transport"]["kind"], "argv")
        block = _echo_block(adopted)
        self.assertEqual(block["state"], "echo_absent", block)
        self.assertEqual(block["events"][0]["state"], "echo_absent", block)
        self.assertEqual(block["events"][0]["reason"], "argv_transport_cannot_echo", block)
        self.assertEqual(block, _echo_block(live))
        self.assertEqual((adopted_result["evidence"].get("settlement_range") or {}).get("echo"), "echo_absent")
        self.assertEqual(adopted_result["evidence"].get("settlement_range"), live_result["evidence"].get("settlement_range"))
        self.assertEqual(set(adopted.delivery_events[0]), {"offset", "payload", "transport", "at"})   # no proof restored

    def test_l10_pty_echo_provenance_adds_no_byte_beyond_the_capture(self) -> None:
        """L-10 (exposure half, MEASURED): for `post_ready_delivery` + ECHO the prompt text is
        on disk in exactly ONE file -- capture.log, where the line discipline echoed it -- and
        the persisted provenance is the payload digest + length and the transport (no file
        other than the capture holds the prompt or any of its lines).  run_c296ff67c325: the
        row carries NO echo-form digests at all, and the adoption excises NOTHING -- it is
        `echo_unproven` / `payload_unobserved` and settles stricter (FAILED
        `refusal_in_boundary` on the echoed phrase) where live COMPLETED; i2 asserted "the
        adoption excises exactly that span" (RED at 92d8432 on `echo_proven`)."""
        live = self._session("pr36-l10-echo", _ECHO_AGENT % {"records": _record(False)})
        prompt_of = lambda sid: _echo_prompt(sid) + " Auth: " + SENTINEL  # noqa: E731
        _sent, live_result = self._turn(live, prompt_of)
        self.assertEqual(live_result["state"], "COMPLETED", live_result)
        holders = self._files_holding(SENTINEL)
        self.assertEqual(holders, [str(live.capture.path.relative_to(self.base))],
                         f"the prompt is held by other files than the capture's own echo: {holders}")
        row = [r for r in live.journal.rows_for(live.intent_id) if r.get("event") == "delivery_recorded"][-1]
        event = row["source_vocabulary"]["events"][0]
        self.assertEqual(set(event), ROW_KEYS)
        self.assertNotIn("echo_proof", json.dumps(row))
        self.assertNotIn('"forms"', json.dumps(row))
        self.assertEqual(event["payload_bytes"], len(prompt_of(live.session_id).encode("utf-8")))
        # ... and the adoption excises nothing of it
        adopted = self._adopt(live)
        adopted_result = adopted.await_completion()
        block = _assert_not_wider(self, live, live_result, adopted, adopted_result)
        self.assertEqual((block["state"], block["events"][0]["reason"]), ("echo_unproven", PAYLOAD_UNOBSERVED), block)
        self.assertEqual(adopted_result["state"], "FAILED", adopted_result)
        self.assertEqual((adopted_result.get("verdict") or {}).get("reason"), REFUSAL_IN_BOUNDARY)


class PR36F002ProofTransportBindingTests(_StubTurn):
    """REVIEW_BUGFIX_iteration2 F-002 (retained, closed): a forged `echo_expected` proof on an
    adopted `argv` event excised agent bytes whose digest it named (the reviewer's
    `forged_proof_probe.py`).  These locks drive the REAL adopt path over a journal whose
    `delivery_recorded` row was tampered (re-digested, as a same-user writer could).

    run_c296ff67c325: the proof no longer exists.  A row carrying the pre-decision
    `echo_proof` shape is not the closed vocabulary and restores NO event (`no_delivery`); a
    closed-shape row with its digest re-pointed at the agent bytes restores an event that
    resolves STRUCTURALLY (`argv` -> `echo_absent`).  Either way nothing is excised and the
    argv verdict equals live (F3-L3).  L-13's "unforged row resolves identically" half is
    REWRITTEN: on the ECHO transport the unforged row now settles stricter (the decision)."""

    def _adopt(self, live: rt.StandaloneSession) -> rt.StandaloneSession:
        stranger = _stranger(live)
        outcome = stranger.adopt(fence=live.fence)
        self.assertTrue(outcome["adopted"], outcome)
        return stranger

    @staticmethod
    def _forged_expected(target: bytes):
        def forge(ev):
            ev["echo_proof"] = {"schema": ECHO_PROOF_SCHEMA, "class": "echo_expected", "reason": "",
                                "forms": [{"sha256": _sha(target), "bytes": len(target)}]}
        return forge

    @staticmethod
    def _digest_repointed(target: bytes):
        def forge(ev):
            ev.pop("echo_proof", None)                 # the closed post-decision shape ...
            ev["payload_sha256"] = _sha(target)        # ... with its diagnostics re-pointed
            ev["payload_bytes"] = len(target)
        return forge

    def _argv_live(self, run_id: str, records: str):
        live = self._session(run_id, _ARGV_AGENT % {"records": records}, delivery_mode="launch_with_prompt")
        out = self._argv_turn(live, _preamble_prompt(live.session_id))
        self.assertTrue(out.get("settled"), out)
        live_result = live.await_completion()
        return live, live_result

    def _assert_no_span(self, session, *, states: tuple, reason: str) -> dict:
        block = _echo_block(session)
        self.assertIn(block["state"], states, block)
        self.assertNotEqual(block["state"], "echo_proven", block)
        self.assertEqual(block["spans"], [], block)
        if block["events"]:
            self.assertEqual(block["events"][0]["reason"], reason, block)
        return block

    def _forged_argv(self, live, live_result, raw: bytes, target: bytes, *, expect_state: str):
        """Both tamper shapes; each adoption excises nothing and settles exactly as live."""
        original = journal_mod.journal_path(live.artifact_base, live.run_id).read_bytes()
        for name, forge, states, reason, restored in (
                ("pre_decision_proof_forged", self._forged_expected(target), ("echo_unproven", "no_delivery"), "proof_class_contradicts_transport", None),
                ("closed_shape_digest_repointed", self._digest_repointed(target), ("echo_absent", "no_delivery"), "argv_transport_cannot_echo", None)):
            with self.subTest(tamper=name):
                journal_mod.journal_path(live.artifact_base, live.run_id).write_bytes(original)
                _forge_row(self, live, forge)
                adopted = self._adopt(live)
                adopted_result = adopted.await_completion()
                block = self._assert_no_span(adopted, states=states, reason=reason)
                self.assertEqual(adopted_result["state"], expect_state, adopted_result)
                self.assertEqual((adopted_result.get("verdict") or {}).get("reason"),
                                 (live_result.get("verdict") or {}).get("reason"))
                self.assertEqual(adopted_result["evidence"]["provenance_outcome"], live_result["evidence"]["provenance_outcome"])
                self.assertEqual((adopted_result["evidence"].get("settlement_range") or {}).get("echo"), block["state"])
                # the strip itself removes nothing from the fenced range
                events = tuple({**dict(ev), "offset": int(ev["offset"])} for ev in adopted.delivery_events)
                self.assertEqual(lifecycle.strip_delivery_echo(raw, events), raw)
                self.assertIn(target, raw)

    def test_l11_a_forged_echo_expected_proof_on_argv_cannot_excise_a_refusal(self) -> None:
        """L-11: the agent prints the refusal prose `not logged in` and a bound success (live
        R1 -> FAILED `refusal_in_boundary`).  The row is forged to name exactly the refusal
        bytes (i2's proof shape, and the closed shape's digest); the adoption excises nothing
        and settles FAILED `refusal_in_boundary` -- identical to live."""
        live, live_result = self._argv_live(
            "pr36-l11-refusal", "printf 'not logged in\\n'\n" + _record(False))
        self.assertEqual(live_result["state"], "FAILED", live_result)
        self.assertEqual((live_result.get("verdict") or {}).get("reason"), REFUSAL_IN_BOUNDARY, live_result)
        n = int(live._boundary["offset_n"])
        raw = live.capture.raw()[:n]
        self._forged_argv(live, live_result, raw, b"not logged in", expect_state="FAILED")

    def test_l12_a_forged_echo_expected_proof_on_argv_cannot_excise_a_completion_record(self) -> None:
        """L-12: the same forgery aimed at the agent's bound `result` line: the record stays,
        COMPLETED == live on the same settlement record."""
        live, live_result = self._argv_live("pr36-l12-record", _record(False))
        self.assertEqual(live_result["state"], "COMPLETED", live_result)
        n = int(live._boundary["offset_n"])
        raw = live.capture.raw()[:n]
        line = next(seg for seg in raw.split(b"\n") if b'"type":"result"' in seg)
        self._forged_argv(live, live_result, raw, line + b"\n", expect_state="COMPLETED")
        adopted = self._adopt(live)
        self.assertEqual(adopted.await_completion()["evidence"]["settlement_record"], live_result["evidence"]["settlement_record"])

    def test_l13_an_echo_absent_proof_on_an_echo_set_pty_write_is_unproven_by_name(self) -> None:
        """L-13 (the inverse): the ECHO turn's row is forged to claim absence -- i2's
        `echo_absent` proof (a pre-decision shape: restores no event), and the closed shape's
        transport rewritten to ECHO clear (restores an event: `payload_unobserved`).  Neither
        excises anything: the echoed "not logged in" fires R1 on the adopted side (FAILED
        `refusal_in_boundary`).  REWRITTEN under run_c296ff67c325: the UNFORGED row settles the
        SAME way -- stricter than live's COMPLETED, by the decision -- where i3 asserted
        live == adopted (RED at 92d8432: adopted `echo_proven`, COMPLETED)."""
        live = self._session("pr36-l13-inverse", _ECHO_AGENT % {"records": _record(False)})
        _sent, live_result = self._turn(live, _echo_prompt)
        self.assertEqual(live_result["state"], "COMPLETED", live_result)
        self.assertEqual(_echo_block(live)["state"], "echo_proven")
        original = journal_mod.journal_path(live.artifact_base, live.run_id).read_bytes()

        def absent_proof(ev):
            ev["echo_proof"] = {"schema": ECHO_PROOF_SCHEMA, "class": "echo_absent", "reason": "echo_flag_clear", "forms": []}

        def echo_clear(ev):
            ev.pop("echo_proof", None)
            ev["transport"]["termios"]["echo"] = False

        for name, forge, states, reason in (("pre_decision_absent_proof", absent_proof, ("echo_unproven", "no_delivery"), "proof_class_contradicts_transport"),
                                            ("closed_shape_echo_cleared", echo_clear, ("echo_unproven",), PAYLOAD_UNOBSERVED),
                                            ("unforged", lambda ev: ev.pop("echo_proof", None), ("echo_unproven",), PAYLOAD_UNOBSERVED)):
            with self.subTest(row=name):
                journal_mod.journal_path(live.artifact_base, live.run_id).write_bytes(original)
                _forge_row(self, live, forge)
                adopted = self._adopt(live)
                adopted_result = adopted.await_completion()
                block = self._assert_no_span(adopted, states=states, reason=reason)
                self.assertEqual(adopted_result["state"], "FAILED", adopted_result)
                self.assertEqual((adopted_result.get("verdict") or {}).get("reason"), REFUSAL_IN_BOUNDARY)
                self.assertEqual((adopted_result["evidence"].get("settlement_range") or {}).get("echo"), block["state"])
                if name == "unforged":
                    _assert_not_wider(self, live, live_result, adopted, adopted_result)


class PR36F002ProofMatrixTests(unittest.TestCase):
    """L-14 (REWRITTEN under run_c296ff67c325): the resolver unit matrix.  i3 asserted every
    class x transport x forms CONTRADICTION is unproven by a proof-binding reason and every
    CONSISTENT proof resolves as the payload does (`echo_proven` on ECHO-set pty).  The
    decision removes the proof's authority altogether: for a payload-less event EVERY proof
    material -- contradictory, consistent, genuine, malformed, absent -- is inert, and the
    verdict is the transport kind's alone: `argv` -> `echo_absent` / `argv_transport_cannot_echo`;
    `pty_write` (ECHO set, ECHO clear, termios unreadable) -> `echo_unproven` /
    `payload_unobserved`; an unknown kind -> `transport_kind_unknown`.  No span, ever; the
    strip returns the bytes unchanged.  The genuine-proof cases are RED at 92d8432
    (`echo_proven`, a span)."""

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
        return {"schema": ECHO_PROOF_SCHEMA, "class": klass, "reason": reason, "forms": list(forms)}

    def _resolve(self, raw, transport, proof):
        ev = ({"offset": 0, "payload": "", "transport": transport, "at": "", "echo_proof": proof},)
        return lifecycle.resolve_delivery_echo(raw, ev)

    def test_l14_every_proof_material_is_inert_and_the_kind_alone_decides(self) -> None:
        argv = pty_supervisor.echo_transport(None, kind="argv", framed=False, cols=80)
        echo_on = self._pty_transport(echo=True)
        echo_off = self._pty_transport(echo=False)
        unreadable = {**self._pty_transport(echo=True), "termios": None}
        unknown = {"kind": "pipe", "framed": False, "cols": 80, "termios": None, "read_at": ""}
        payload = "not logged in " + SENTINEL
        forms_on = lifecycle.expected_echo_forms(payload, echo_on)["forms"]
        agent = b"not logged in"
        raw = agent + b"\r\n" + forms_on[0] + b"\r\n"
        good_forms = [{"sha256": _sha(f), "bytes": len(f)} for f in forms_on]
        forged = [{"sha256": _sha(agent), "bytes": len(agent)}]
        expected = {"argv": ("echo_absent", "argv_transport_cannot_echo"),
                    "pty_write": ("echo_unproven", PAYLOAD_UNOBSERVED),
                    "pipe": ("echo_unproven", "transport_kind_unknown")}
        materials = [
            ("forged_expected", self._proof("echo_expected", "", forged)),
            ("genuine_expected", self._proof("echo_expected", "", good_forms)),
            ("absent_argv_reason", self._proof("echo_absent", "argv_transport_cannot_echo")),
            ("absent_flag_clear", self._proof("echo_absent", "echo_flag_clear")),
            ("absent_with_forms", self._proof("echo_absent", "argv_transport_cannot_echo", forged)),
            ("unproven_termios", self._proof("echo_unproven", "termios_unreadable")),
            ("unproven_echonl", self._proof("echo_unproven", "echonl_partial_echo")),
            ("unproven_kind", self._proof("echo_unproven", "transport_kind_unknown")),
            ("unproven_made_up", self._proof("echo_unproven", "made_up_reason")),
            ("expected_no_forms", self._proof("echo_expected", "")),
            ("expected_nine_forms", self._proof("echo_expected", "", good_forms * 9)),
            ("old_schema", {**self._proof("echo_expected", "", good_forms), "schema": "os48.echo_proof.v0"}),
            ("unknown_class", self._proof("echo_whatever", "", good_forms)),
            ("proof_none", None),
            ("proof_absent", "ABSENT"),
        ]
        transports = [("argv", argv), ("echo_on", echo_on), ("echo_off", echo_off), ("termios_unreadable", unreadable), ("unknown", unknown)]
        for tname, transport in transports:
            for mname, proof in materials:
                with self.subTest(transport=tname, material=mname):
                    ev = {"offset": 0, "payload": "", "transport": transport, "at": ""}
                    if proof != "ABSENT":
                        ev["echo_proof"] = proof
                    res = lifecycle.resolve_delivery_echo(raw, (ev,))
                    state, reason = expected[transport["kind"]]
                    self.assertEqual((res["state"], res["spans"]), (state, ()), res)
                    self.assertEqual((res["events"][0]["state"], res["events"][0]["reason"], res["events"][0]["span"]),
                                     (state, reason, None), res)
                    self.assertEqual(lifecycle.strip_delivery_echo(raw, (ev,)), raw)
        # the LIVE control is unchanged: the payload in memory proves the echo on the ECHO-set pty ...
        live = lifecycle.resolve_delivery_echo(raw, ({"offset": 0, "payload": payload, "transport": echo_on, "at": ""},))
        self.assertEqual((live["state"], live["spans"]), ("echo_proven", ((len(agent) + 2, len(agent) + 2 + len(forms_on[0])),)), live)
        # ... is absent by structure on argv / ECHO clear, unproven by name when unreadable
        for transport, state, reason in ((argv, "echo_absent", "argv_transport_cannot_echo"),
                                         (echo_off, "echo_absent", "echo_flag_clear"),
                                         (unreadable, "echo_unproven", "termios_unreadable")):
            with self.subTest(live=transport.get("kind"), echo=(transport.get("termios") or {}).get("echo")):
                res = lifecycle.resolve_delivery_echo(raw, ({"offset": 0, "payload": payload, "transport": transport, "at": ""},))
                self.assertEqual((res["state"], res["events"][0]["reason"], res["spans"]), (state, reason, ()), res)
        # ... and ambiguity on the live side stays ambiguity; the restored side is unproven regardless
        raw2 = forms_on[0] + b"\r\n" + forms_on[0] + b"\r\n"
        amb = lifecycle.resolve_delivery_echo(raw2, ({"offset": 0, "payload": payload, "transport": echo_on, "at": ""},))
        self.assertEqual((amb["state"], amb["reason"]), ("echo_unproven", "event[0]:ambiguous_multiple_matches"))
        restored = self._resolve(raw2, echo_on, self._proof("echo_expected", "", good_forms))
        self.assertEqual((restored["state"], restored["reason"], restored["spans"]), ("echo_unproven", "event[0]:" + PAYLOAD_UNOBSERVED, ()))

    def test_l14_the_reviewers_forged_proof_probe_keeps_the_refusal(self) -> None:
        """The reviewer's F-002 `forged_proof_probe.py`, verbatim shape (still closed)."""
        refusal = b"not logged in"
        raw = refusal + b'\n{"type":"result","is_error":false}\n'
        event = {"offset": 0, "payload": "", "transport": {"kind": "argv", "framed": False, "termios": None, "cols": 80},
                 "at": "", "echo_proof": {"schema": ECHO_PROOF_SCHEMA, "class": "echo_expected", "reason": "",
                                          "forms": [{"sha256": _sha(refusal), "bytes": len(refusal)}]}}
        result = lifecycle.resolve_delivery_echo(raw, (event,))
        self.assertNotEqual(result["state"], "echo_proven", result)
        self.assertEqual(result["spans"], (), result)
        self.assertIn(refusal, lifecycle.strip_delivery_echo(raw, (event,)))

    def test_l14_the_reviewers_echo_set_forgery_probe_excises_nothing(self) -> None:
        """F3-L1 (unit): the reviewer's F-003 `echo_set_forgery_probe.py`, verbatim construction
        -- a GENUINE ECHO-set unframed pty transport, a capture holding the real echo of a
        harmless prompt at [0,15], the agent refusal `not logged in` at [17,30] and a result
        record; the payload-less event carries a structurally consistent one-form
        `echo_expected` proof whose digest names the refusal.  92d8432: `echo_proven`
        [[17,30]] -- the refusal excised, the real echo kept.  Now: `echo_unproven` /
        `payload_unobserved`, no span, both spans survive; live still proves [0,15]."""
        transport = self._pty_transport(echo=True, framed=False)
        payload = "harmless prompt"
        actual_echo = lifecycle.expected_echo_forms(payload, transport)["forms"][0]
        refusal = b"not logged in"
        raw = actual_echo + b"\r\n" + refusal + b'\n{"type":"result","is_error":false}\n'
        live_event = {"offset": 0, "payload": payload, "transport": transport, "at": ""}
        forged_event = {"offset": 0, "payload": "", "transport": transport, "at": "",
                        "echo_proof": {"schema": ECHO_PROOF_SCHEMA, "class": "echo_expected", "reason": "",
                                       "forms": [{"sha256": _sha(refusal), "bytes": len(refusal)}]}}
        live = lifecycle.resolve_delivery_echo(raw, (live_event,))
        forged = lifecycle.resolve_delivery_echo(raw, (forged_event,))
        self.assertEqual((live["state"], live["spans"]), ("echo_proven", ((0, len(actual_echo)),)), live)
        self.assertEqual((forged["state"], forged["spans"]), ("echo_unproven", ()), forged)
        self.assertEqual(forged["events"][0]["reason"], PAYLOAD_UNOBSERVED, forged)
        forged_after = lifecycle.strip_delivery_echo(raw, (forged_event,))
        self.assertEqual(forged_after, raw)
        self.assertIn(refusal, forged_after)
        self.assertIn(actual_echo, forged_after)
        self.assertNotIn(actual_echo, lifecycle.strip_delivery_echo(raw, (live_event,)))   # live: unchanged


class _SpiedBytes(bytes):
    """A capture whose every read the resolver could make is counted (F3-L1 unit: the
    payload-less leg must not read the capture at all -- no anchor search, no digest)."""

    reads = 0

    def find(self, *a, **kw):
        _SpiedBytes.reads += 1
        return super().find(*a, **kw)

    def __getitem__(self, item):
        if isinstance(item, slice):
            _SpiedBytes.reads += 1
        return super().__getitem__(item)

    def __iter__(self):
        _SpiedBytes.reads += 1
        return super().__iter__()


class PR36F003UnobservedEventResolverTests(unittest.TestCase):
    """(i2: `PR36F001EchoProofResolverTests` -- "a payload-less event carrying a digest-only
    proof yields the SAME block the payload yields"; REWRITTEN under run_c296ff67c325.)  A
    payload-less event yields NO span whatever it carries -- framed or unframed, anchored or
    not, budget or no budget: there is no digest scan any more.  The live payload still
    proves its echo; the restored event is `echo_unproven` / `payload_unobserved` on
    `pty_write`, `echo_absent` on `argv`, and the resolver reads no capture byte for it."""

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
        restored = ({"offset": offset, "payload": "", "transport": transport, "at": ""},)
        return lifecycle.resolve_delivery_echo(raw, live), lifecycle.resolve_delivery_echo(raw, restored)

    def _assert_unobserved(self, block, offset: int = 0) -> None:
        self.assertEqual((block["state"], block["reason"], block["spans"]),
                         ("echo_unproven", "event[0]:" + PAYLOAD_UNOBSERVED, ()), block)
        self.assertEqual([(e["state"], e["reason"], e["span"], e["forms"]) for e in block["events"]],
                         [("echo_unproven", PAYLOAD_UNOBSERVED, None, 0)], block)

    def test_a_framed_restored_event_yields_no_span_where_the_payload_proves_one(self) -> None:
        transport = self._transport(framed=True)
        payload = _echo_prompt("sid-unit") + " Auth: " + SENTINEL
        forms = lifecycle.expected_echo_forms(payload, transport)["forms"]
        raw = b"prelude\r\n" + forms[0] + b"\r\n{\"type\":\"result\"}\r\n"
        a, b = self._blocks(raw, payload, transport)
        self.assertEqual((a["state"], a["spans"]), ("echo_proven", ((9, 9 + len(forms[0])),)), a)
        self._assert_unobserved(b)
        self.assertEqual(lifecycle.strip_delivery_echo(raw, ({"offset": 0, "payload": "", "transport": transport, "at": ""},)), raw)

    def test_an_unframed_restored_event_yields_no_span_and_no_scan(self) -> None:
        transport = self._transport(framed=False)
        payload = "unframed line " + SENTINEL
        forms = lifecycle.expected_echo_forms(payload, transport)["forms"]
        raw = b"x" * 300 + forms[0] + b"\r\nrest\r\n"
        a, b = self._blocks(raw, payload, transport)
        self.assertEqual(a["state"], "echo_proven")
        self._assert_unobserved(b)
        # ambiguity on the live side; the restored side is unproven by the same name regardless
        raw2 = forms[0] + b"\r\n" + forms[0] + b"\r\n"
        a2, b2 = self._blocks(raw2, payload, transport)
        self.assertEqual((a2["state"], a2["reason"]), ("echo_unproven", "event[0]:ambiguous_multiple_matches"))
        self._assert_unobserved(b2)
        # a delivery before the window is still named as such, ahead of the structural verdict
        before = lifecycle.resolve_delivery_echo(raw, ({"offset": -1, "payload": "", "transport": transport, "at": ""},))
        self.assertEqual((before["state"], before["reason"]), ("echo_unproven", "event[0]:delivery_before_window"))

    def test_the_resolver_reads_no_capture_byte_for_a_restored_event(self) -> None:
        """F3-L1 (unit): whatever the event carries -- a genuine proof of the real echo, a
        forged one, none -- the payload-less leg touches NO byte of the capture (92d8432 ran an
        anchor search / a digest scan over it) and produces no span; `argv` is absent by name."""
        transport = self._transport(framed=False)
        payload = "p " + SENTINEL
        forms = lifecycle.expected_echo_forms(payload, transport)["forms"]
        raw = _SpiedBytes(b"y" * 1000 + forms[0])
        genuine = {"schema": ECHO_PROOF_SCHEMA, "class": "echo_expected", "reason": "",
                   "forms": [{"sha256": _sha(forms[0]), "bytes": len(forms[0])}]}
        for name, proof in (("genuine", genuine), ("wrong_digest", {**genuine, "forms": [{"sha256": "0" * 64, "bytes": len(forms[0])}]}),
                            ("no_forms", {**genuine, "forms": []}), ("other_schema", {**genuine, "schema": "other"}),
                            ("garbage", {**genuine, "forms": [{"sha256": "zz", "bytes": 1}]}), ("none", None), ("absent", "ABSENT")):
            with self.subTest(material=name):
                ev = {"offset": 0, "payload": "", "transport": transport, "at": ""}
                if proof != "ABSENT":
                    ev["echo_proof"] = proof
                _SpiedBytes.reads = 0
                res = lifecycle.resolve_delivery_echo(raw, (ev,))
                self.assertEqual(_SpiedBytes.reads, 0, f"the restored event read the capture ({_SpiedBytes.reads} reads)")
                self.assertEqual((res["state"], res["reason"], res["spans"]), ("echo_unproven", "event[0]:" + PAYLOAD_UNOBSERVED, ()), res)
        # the LIVE leg does read it (the control that the spy counts)
        _SpiedBytes.reads = 0
        live = lifecycle.resolve_delivery_echo(raw, ({"offset": 0, "payload": payload, "transport": transport, "at": ""},))
        self.assertEqual(live["state"], "echo_proven", live)
        self.assertGreater(_SpiedBytes.reads, 0)
        absent = lifecycle.resolve_delivery_echo(raw, ({"offset": 0, "payload": "", "at": "",
                                                        "transport": pty_supervisor.echo_transport(None, kind="argv", framed=False, cols=80)},))
        self.assertEqual((absent["state"], absent["events"][0]["reason"], absent["spans"]),
                         ("echo_absent", "argv_transport_cannot_echo", ()), absent)


# =====================================================================================
# run_c296ff67c325 F-003 -- F3-L2: adopted_success is a subset of live_success (the matrix)
# =====================================================================================
#: the ECHO-CLEAR turn: cooked mode WITHOUT echo -- the prompt is read, nothing is echoed
_NOECHO_AGENT = """SID="$1"
stty -echo icanon
printf '{"type":"system","session_id":"%%s"}\\n' "$SID"
IFS= read -r PROMPT
%(records)s
exit 0
"""


def _json_example_prompt(session_id: str) -> str:
    """A prompt carrying a result-JSON example bound to THIS dispatch and no refusal phrase."""
    return ('Reply with exactly one result line like this example: '
            '{"type":"result","is_error":false,"session_id":"%s"}' % session_id)


def _clean_prompt(session_id: str) -> str:
    return "Continue the task and reply with one bound result line for %s." % session_id


class PR36F003AdoptedSuccessSubsetTests(_StubTurn):
    """F3-L2 / F3-L5: for every transport x agent-output cell, settle the SAME run live and
    adopted (the REAL adopt path over the live journal) and assert the decision's invariant
    `adopted COMPLETED => live COMPLETED` (on the same record), plus the cell's outcome BY
    NAME -- where adoption is stricter, the stricter outcome is the one the decision accepts:

    transport            | clean       | refusal     | refusal-like prompt + ok | JSON-example prompt + ok
    argv                 | C / C       | F / F       | C / C                    | C / C
    pty ECHO set         | C / C       | F / F       | C / F refusal_in_boundary| C / L record_framing_ambiguous
    pty ECHO clear       | C / C       | F / F       | C / C                    | C / C
    pty termios unreadable| C / C      | F / F       | F / F (live cannot prove the echo either) | L / L

    (C = COMPLETED, F = FAILED `refusal_in_boundary`, L = LOST `record_framing_ambiguous`;
    live / adopted).  92d8432 adopted the two ECHO-set "stricter" cells as COMPLETED
    (RED on the named outcome); the invariant itself holds on every cell.  The unreadable
    termios is a seam (`termios_evidence` returns None at the write), never a sleep."""

    OUTPUTS = {
        "clean_completion": (_clean_prompt, _record(False)),
        "refusal": (_clean_prompt, _record(False) + _record(True)),
        "refusal_like_prompt_echo": (_echo_prompt, _record(False)),
        "json_example_prompt_echo": (_json_example_prompt, _record(False)),
    }
    C, F, L = ("COMPLETED", None), ("FAILED", REFUSAL_IN_BOUNDARY), ("LOST", FRAMING_AMBIGUOUS)
    EXPECTED = {
        "argv": {"clean_completion": (C, C), "refusal": (F, F), "refusal_like_prompt_echo": (C, C), "json_example_prompt_echo": (C, C)},
        "pty_echo_set": {"clean_completion": (C, C), "refusal": (F, F), "refusal_like_prompt_echo": (C, F), "json_example_prompt_echo": (C, L)},
        "pty_echo_clear": {"clean_completion": (C, C), "refusal": (F, F), "refusal_like_prompt_echo": (C, C), "json_example_prompt_echo": (C, C)},
        "pty_termios_unreadable": {"clean_completion": (C, C), "refusal": (F, F), "refusal_like_prompt_echo": (F, F), "json_example_prompt_echo": (L, L)},
    }

    def _cell(self, transport: str, output: str):
        prompt_of, records = self.OUTPUTS[output]
        run_id = f"pr36-f3l2-{transport}-{output}"[:60].replace("_", "-")
        if transport == "argv":
            live = self._session(run_id, _ARGV_AGENT % {"records": records}, delivery_mode="launch_with_prompt")
            self._argv_turn(live, prompt_of(live.session_id))
            live_result = live.await_completion()
        else:
            script = _NOECHO_AGENT if transport == "pty_echo_clear" else _ECHO_AGENT
            live = self._session(run_id, script % {"records": records})
            if transport == "pty_termios_unreadable":
                with patch.object(pty_supervisor, "termios_evidence", lambda fd: None):
                    live_result = self._pty_turn(live, prompt_of)
            else:
                live_result = self._pty_turn(live, prompt_of)
        adopted = _stranger(live)
        outcome = adopted.adopt(fence=live.fence)
        self.assertTrue(outcome["adopted"], outcome)
        adopted_result = adopted.await_completion()
        return live, live_result, adopted, adopted_result

    def _pty_turn(self, session, prompt_of) -> dict:
        from scripts.test_os37_lifecycle_boundary_regressions import INJECTED_REHEARSALS
        receipt = session.start(payload="rehearsal", **INJECTED_REHEARSALS)
        self.sessions.append(session)
        self.assertEqual(receipt["start_outcome"], "ready", receipt)
        ready = session.await_ready()
        self.assertEqual(ready["state"], "READY", ready)
        session.send({"payload": prompt_of(session.session_id)})      # the delivery outcome is the cell's
        return session.await_completion()

    @staticmethod
    def _verdict_of(result) -> tuple:
        if result["state"] == "COMPLETED":
            return ("COMPLETED", None)
        if result["state"] == "LOST":
            return ("LOST", result.get("lost_reason") or result["evidence"].get("provenance_outcome"))
        return (result["state"], (result.get("verdict") or {}).get("reason"))

    def _assert_cell(self, transport: str, output: str) -> tuple:
        live, live_result, adopted, adopted_result = self._cell(transport, output)
        lo, ao = self._verdict_of(live_result), self._verdict_of(adopted_result)
        # the invariant: an adopted success is a live success on the same record
        if ao[0] == "COMPLETED":
            self.assertEqual(lo[0], "COMPLETED", f"{transport}/{output}: adopted COMPLETED, live {lo}")
            self.assertEqual(adopted_result["evidence"].get("settlement_record"), live_result["evidence"].get("settlement_record"))
        # the cell's outcomes by name (where adoption is stricter, the decision's outcome)
        expected_live, expected_adopted = self.EXPECTED[transport][output]
        self.assertEqual((lo, ao), (expected_live, expected_adopted), f"{transport}/{output}: live {lo}, adopted {ao}")
        # the adoption excised nothing and read the same range
        if transport == "argv":
            _assert_identical(self, live, live_result, adopted, adopted_result)
        else:
            block = _assert_not_wider(self, live, live_result, adopted, adopted_result)
            self.assertEqual((block["state"], block["events"][0]["reason"]), ("echo_unproven", PAYLOAD_UNOBSERVED), block)
        return lo, ao

    def test_f3l2_argv(self) -> None:
        for output in self.OUTPUTS:
            with self.subTest(output=output):
                self._assert_cell("argv", output)

    def test_f3l2_pty_echo_set(self) -> None:
        for output in self.OUTPUTS:
            with self.subTest(output=output):
                self._assert_cell("pty_echo_set", output)

    def test_f3l2_pty_echo_clear(self) -> None:
        for output in self.OUTPUTS:
            with self.subTest(output=output):
                self._assert_cell("pty_echo_clear", output)

    def test_f3l2_pty_termios_unreadable(self) -> None:
        for output in self.OUTPUTS:
            with self.subTest(output=output):
                self._assert_cell("pty_termios_unreadable", output)

    def test_f3l1_the_reviewers_forgery_through_the_real_adopt_path_cannot_produce_a_false_completed(self) -> None:
        """F3-L1 (the F-003 counterexample, REAL path): an ECHO-set turn whose prompt is
        HARMLESS; the agent prints the refusal prose `not logged in`, then a bound success.
        Live: the echo it observed is excised, R1 fires on the agent's refusal -> FAILED
        `refusal_in_boundary`.  The row is then tampered exactly as the reviewer's
        `echo_set_forgery_probe.py` constructs it -- the pre-decision `echo_expected` proof
        with ONE form whose sha256 / length name the refusal bytes, on an unframed ECHO-set
        transport -- and re-digested.  92d8432: the adoption resolved `echo_proven` over the
        refusal, excised it and settled COMPLETED -- a false success live never produced (RED).
        Now the row is not the closed vocabulary (restores no event) and, in its closed-shape
        variant (digest re-pointed, no proof key), the restored event is `payload_unobserved`:
        nothing is excised, the refusal fires, FAILED `refusal_in_boundary` == live."""
        live = self._session("pr36-f3l1-forgery", _ECHO_AGENT % {"records": "printf 'not logged in\\n'\n" + _record(False)})
        live_result = self._pty_turn(live, lambda sid: "harmless prompt for %s" % sid)
        self.assertEqual(self._verdict_of(live_result), self.F, live_result)
        self.assertEqual(_echo_block(live)["state"], "echo_proven", _echo_block(live))       # live: the real echo
        baseline, n, fenced = self._fenced(live)
        refusal = b"not logged in"
        self.assertEqual(fenced.count(refusal), 1, fenced)
        original = journal_mod.journal_path(live.artifact_base, live.run_id).read_bytes()

        def reviewer_forgery(ev):
            ev["transport"]["framed"] = False
            ev["echo_proof"] = {"schema": ECHO_PROOF_SCHEMA, "class": "echo_expected", "reason": "",
                                "forms": [{"sha256": _sha(refusal), "bytes": len(refusal)}]}

        def closed_shape_forgery(ev):
            ev.pop("echo_proof", None)
            ev["transport"]["framed"] = False
            ev["payload_sha256"], ev["payload_bytes"] = _sha(refusal), len(refusal)

        for name, forge, state, restored in (("reviewers_probe_row", reviewer_forgery, "no_delivery", 0),
                                             ("closed_shape_row", closed_shape_forgery, "echo_unproven", 1)):
            with self.subTest(row=name):
                journal_mod.journal_path(live.artifact_base, live.run_id).write_bytes(original)
                _forge_row(self, live, forge)
                adopted = _stranger(live)
                self.assertTrue(adopted.adopt(fence=live.fence)["adopted"])
                adopted_result = adopted.await_completion()
                self.assertEqual(self._verdict_of(adopted_result), self.F,
                                 f"the forged row produced a settlement live did not: {adopted_result}")
                block = _echo_block(adopted)
                self.assertEqual(block["spans"], [], block)
                self.assertIn(refusal, self._fenced(adopted)[2])
                self.assertEqual(block["state"], state, block)
                self.assertEqual(len(adopted.delivery_events), restored)
                if restored:
                    self.assertEqual(block["events"][0]["reason"], PAYLOAD_UNOBSERVED, block)
                self.assertEqual((adopted_result["evidence"].get("settlement_range") or {}).get("echo"), state)
                self.assertEqual(adopted_result["evidence"]["provenance_outcome"], live_result["evidence"]["provenance_outcome"])

    def test_f3l5_a_refusal_like_prompt_and_a_json_example_never_yield_a_false_completed(self) -> None:
        """F3-L5, by name: on the ECHO-set pty the echoed refusal-like prompt settles FAILED
        `refusal_in_boundary` and the echoed JSON example LOST `record_framing_ambiguous` on
        adoption -- never COMPLETED -- while live (which observed and excised the echo)
        COMPLETED both.  The stricter outcome is the decision's, not a defect."""
        for output, stricter in (("refusal_like_prompt_echo", self.F), ("json_example_prompt_echo", self.L)):
            with self.subTest(output=output):
                lo, ao = self._assert_cell("pty_echo_set", output)
                self.assertEqual(lo, self.C)
                self.assertEqual(ao, stricter)


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
