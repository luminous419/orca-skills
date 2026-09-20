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
from typing import Any, Mapping
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
    """run_d6391487ff44 (the unkeyed `delivery_recorded.baseline` merge blocker; rewritten from
    the i1-i3 "adopted baseline == live baseline"): the adopted session NEVER derives a baseline
    from the unkeyed row -- it settles over the FULL fenced prefix ``[0, N)`` (baseline 0), a
    SUPERSET of the live-observed ``[live_baseline, N)``.  So the adopted baseline is 0 (never
    the live one) and the adopted range is never narrower than live's -- the property the
    forged-baseline defect violated.  Provenance offsets are still the recorded ones (they carry
    no excision authority; run_c296ff67c325) and the events stay payload-less (F-001)."""
    case.assertEqual(int(adopted._settlement_baseline or 0), 0,
                     "the adopted session did not settle over the full fenced prefix [0, N)")
    case.assertGreaterEqual(int(live._settlement_baseline or 0), 0)
    case.assertLessEqual(int(adopted._settlement_baseline or 0), int(live._settlement_baseline or 0),
                         "the adopted examined range is narrower than the live one")
    case.assertEqual(_provenance(adopted), _provenance(live),
                     "the adopted session restored different delivery provenance")
    case.assertTrue(all(ev.get("payload") == "" for ev in adopted.delivery_events),
                    "the adoption restored a payload (F-001)")


def _assert_identical(case: unittest.TestCase, live, live_result, adopted, adopted_result) -> None:
    """live == adopted: baseline, provenance, echo block, verdict, evidence.  Holds for the
    `argv` path (F3-L3: `echo_absent` by structure on both sides, and the live `argv` baseline
    is itself 0 -- `send()` is never called -- so adoption's baseline-0 rule leaves the range
    identical); an ECHO transport (where live has a non-zero baseline) is compared with
    `_assert_not_wider` instead (run_c296ff67c325, run_d6391487ff44)."""
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
    """run_c296ff67c325 + run_d6391487ff44 (USER DECISION / the baseline merge blocker): the
    adopted settlement examines the FULL fenced prefix ``[0, N)`` -- a superset of live's
    ``[live_baseline, N)`` -- with payload-less provenance, EXCISES NOTHING (no `echo_proven`,
    no span) and is never wider than the live one: adopted COMPLETED => live COMPLETED on the
    same record.  Returns the adopted echo block for the caller's named assertions."""
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
    # run_d6391487ff44: the adopted examined range starts at 0 (the full prefix), not the live
    # baseline; a value <= the live baseline is never wider.
    case.assertEqual(rng.get("baseline"), 0, "the adoption did not examine [0, N)")
    case.assertLessEqual(rng.get("baseline"), (le.get("settlement_range") or {}).get("baseline") or 0)
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
        # run_d6391487ff44: the adopted settlement baseline is 0 (the full fenced prefix), and
        # the live baseline it did NOT adopt survives only as `journal_baseline_diagnostic`.
        self.assertEqual(rows[0]["source_vocabulary"].get("settlement_baseline"), 0, rows[0])
        self.assertGreater(live._settlement_baseline, 0)
        self.assertEqual(rows[0]["source_vocabulary"].get("journal_baseline_diagnostic"),
                         live._settlement_baseline, rows[0])
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
                # then the names.  run_d6391487ff44: the adopted baseline is 0 (the full fenced
                # prefix), whatever the unkeyed row's baseline; the live baseline it did not
                # adopt survives as a diagnostic only.
                self.assertEqual(adopted._settlement_baseline, 0)
                self.assertGreater(live._settlement_baseline, 0)
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
        restores NO event and is journalled `delivery_provenance_unrestored` by name.
        run_d6391487ff44 (the baseline merge blocker; rewritten from "keeps the baseline"): after
        the rejection NOTHING from the row is applied -- the baseline stays 0 (the row's
        `baseline` 71 was NEVER a settlement authority and survives only as the diagnostic
        `journal_baseline_diagnostic`), so a malformed row + forged baseline cannot narrow the
        examined range."""
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
                self.assertEqual(out, {"restored": False, "reason": "delivery_recorded_events_malformed",
                                       "events": 0, "journal_baseline_diagnostic": 71})
                self.assertEqual(stranger._settlement_baseline, 0)      # NOT 71: nothing from the row is applied
                self.assertEqual(stranger.delivery_events, [])
                named = [r for r in stranger.journal.rows_for(live.intent_id)
                         if r.get("event") == "delivery_provenance_unrestored"]
                self.assertEqual(len(named), index + 1, named)          # one per malformed shape, same journal
                self.assertEqual(named[-1]["source_vocabulary"]["reason"], "delivery_recorded_events_malformed")
                self.assertEqual(named[-1]["source_vocabulary"]["settlement_baseline"], 0)
                self.assertEqual(named[-1]["source_vocabulary"]["journal_baseline_diagnostic"], 71)
                # the well-formed post-decision row restores the event WITHOUT any proof key,
                # and STILL never adopts the row's baseline (it stays 0)
                good = {"index": 0, "offset": 71, "payload_sha256": "0" * 64, "payload_bytes": 4, "transport": transport, "at": ""}
                rows[0]["source_vocabulary"]["events"] = [good]
                fresh = _stranger(live)
                fresh.session_id, fresh.incarnation = live.session_id, live.incarnation
                self.assertEqual(fresh._restore_delivery(rows),
                                 {"restored": True, "reason": "", "events": 1, "journal_baseline_diagnostic": 71})
                self.assertEqual(fresh._settlement_baseline, 0)
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
    pty ECHO set         | C / B       | F / F       | C / F refusal_in_boundary| C / L record_framing_ambiguous
    pty ECHO clear       | C / B       | F / F       | C / B                    | C / B
    pty termios unreadable| C / B      | F / F       | F / F (live cannot prove the echo either) | L / L

    (C = COMPLETED, F = FAILED `refusal_in_boundary`, L = LOST `record_framing_ambiguous`,
    B = LOST `adopted_baseline_unknown`; live / adopted).  92d8432 adopted the two ECHO-set
    "stricter" cells as COMPLETED (RED on the named outcome); the invariant itself holds on
    every cell.  USER_DECISION_C2.md (run_11b4061df84d): the pty transports are
    `post_ready_delivery`, whose ADOPTED settlement is never COMPLETED -- the five adopted
    `C` cells of i1-i3 are now `B` by name (37b3f58 settled them COMPLETED); the argv column
    (`launch_with_prompt`, baseline 0) is unchanged.  The unreadable termios is a seam
    (`termios_evidence` returns None at the write), never a sleep."""

    OUTPUTS = {
        "clean_completion": (_clean_prompt, _record(False)),
        "refusal": (_clean_prompt, _record(False) + _record(True)),
        "refusal_like_prompt_echo": (_echo_prompt, _record(False)),
        "json_example_prompt_echo": (_json_example_prompt, _record(False)),
    }
    C, F, L = ("COMPLETED", None), ("FAILED", REFUSAL_IN_BOUNDARY), ("LOST", FRAMING_AMBIGUOUS)
    #: USER_DECISION_C2.md (run_11b4061df84d): an ADOPTED settlement of a `post_ready_delivery`
    #: dispatch (the three pty transports) never returns a success -- every adopted COMPLETED
    #: cell below is the named LOST `adopted_baseline_unknown`; refusal / framing dominance
    #: (F, L) is preserved first; the argv (`launch_with_prompt`, baseline 0) column is unchanged.
    B = ("LOST", "adopted_baseline_unknown")
    EXPECTED = {
        "argv": {"clean_completion": (C, C), "refusal": (F, F), "refusal_like_prompt_echo": (C, C), "json_example_prompt_echo": (C, C)},
        "pty_echo_set": {"clean_completion": (C, B), "refusal": (F, F), "refusal_like_prompt_echo": (C, F), "json_example_prompt_echo": (C, L)},
        "pty_echo_clear": {"clean_completion": (C, B), "refusal": (F, F), "refusal_like_prompt_echo": (C, B), "json_example_prompt_echo": (C, B)},
        "pty_termios_unreadable": {"clean_completion": (C, B), "refusal": (F, F), "refusal_like_prompt_echo": (F, F), "json_example_prompt_echo": (L, L)},
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
# run_d6391487ff44 -- the unkeyed `delivery_recorded.baseline` is NOT a settlement authority
# (L-1 forged later baseline, L-2 malformed events + forged baseline, L-3 missing / multiple
# rows).  Every counterexample is RED at 991847f (where `_restore_delivery` assigned
# `_settlement_baseline = row.baseline` BEFORE validating the events and left it applied on
# rejection) and GREEN after the fix (baseline stays 0; the row's baseline survives only as
# `journal_baseline_diagnostic`).  Rewrites nothing -- these are new locks for the merge blocker.
# =====================================================================================
def _rewrite_delivery_rows(case, live, mutate, *, expect_rows: int = 1) -> None:
    """Rewrite every `delivery_recorded` row of ``live``'s intent through ``mutate(row)`` and
    re-digest it (`record_digest`; the journal has no secret), so `rows()` still verifies -- the
    same-user tampering the unkeyed row invites."""
    path = journal_mod.journal_path(live.artifact_base, live.run_id)
    lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    out, forged = [], 0
    for line in lines:
        row = json.loads(line)
        if row.get("intent_id") == live.intent_id and row.get("event") == "delivery_recorded":
            mutate(row)
            row["digest"] = journal_mod.record_digest(row)
            forged += 1
        out.append(json.dumps(row, sort_keys=True, ensure_ascii=False))
    case.assertEqual(forged, expect_rows)
    path.write_text("\n".join(out) + "\n", encoding="utf-8")


def _append_forged_last_row(case, live, *, baseline: int) -> None:
    """Append a SECOND `delivery_recorded` row (a copy of the first, its baseline forged) with a
    higher ``seq`` so it becomes the LAST such row -- the substitution `_restore_delivery`'s
    `named[-1]` would trust.  Re-digested so the journal reads."""
    path = journal_mod.journal_path(live.artifact_base, live.run_id)
    lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    rows = [json.loads(ln) for ln in lines]
    orig = next(r for r in rows if r.get("intent_id") == live.intent_id and r.get("event") == "delivery_recorded")
    dup = json.loads(json.dumps(orig))
    dup["seq"] = max(int(r.get("seq") or 0) for r in rows) + 1
    dup["source_vocabulary"]["baseline"] = baseline
    dup.pop("digest", None)
    dup["digest"] = journal_mod.record_digest(dup)
    rows.append(dup)
    path.write_text("\n".join(json.dumps(r, sort_keys=True, ensure_ascii=False) for r in rows) + "\n",
                    encoding="utf-8")


def _remove_delivery_rows(case, live) -> int:
    path = journal_mod.journal_path(live.artifact_base, live.run_id)
    lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    rows = [json.loads(ln) for ln in lines]
    kept = [r for r in rows if not (r.get("intent_id") == live.intent_id and r.get("event") == "delivery_recorded")]
    path.write_text("\n".join(json.dumps(r, sort_keys=True, ensure_ascii=False) for r in kept) + "\n",
                    encoding="utf-8")
    return len(rows) - len(kept)


class PR36BaselineNotAuthorityTests(_StubTurn):
    """The merge blocker: a forged/stale `delivery_recorded.baseline` narrowing the adopted
    fenced prefix past a refusal.  A live ECHO-set turn whose HARMLESS prompt is delivered; the
    agent prints the refusal prose `not logged in`, then a bound success.  Live excises the echo
    it observed and R1 fires on `not logged in` -> FAILED `refusal_in_boundary`.  A same-user
    writer then rewrites the row's `baseline` to a value PAST the refusal and re-digests it: at
    991847f the adoption examined `[baseline, N)`, which STARTS after the refusal, saw only the
    success record and settled COMPLETED -- a false success live never produced.  After the fix
    the adopted baseline is 0 regardless of the row, so the refusal is always in `[0, N)` and the
    verdict is FAILED, never COMPLETED."""

    F = ("FAILED", REFUSAL_IN_BOUNDARY)

    def _live_refusal_after_echo(self, run_id: str):
        """A live run that settled FAILED `refusal_in_boundary` with the refusal AFTER the
        prompt echo.  Returns (live, live_result, forged_baseline) where forged_baseline is a
        value PAST the refusal (the start of the success record) -- the value a forger would
        pick to hide the refusal."""
        live = self._session(run_id, _ECHO_AGENT % {"records": "printf 'not logged in\\n'\n" + _record(False)})
        live_result = self._pty_turn(live, lambda sid: "harmless prompt for %s" % sid)
        self.assertEqual((live_result["state"], (live_result.get("verdict") or {}).get("reason")), self.F, live_result)
        raw = live.capture.raw()
        refusal_end = raw.index(b"not logged in") + len(b"not logged in")
        success = raw.index(b'{"type":"result"', refusal_end)
        forged_baseline = success                     # [forged, N) excludes the refusal, keeps the success
        self.assertGreater(forged_baseline, int(live._settlement_baseline or 0))
        return live, live_result, forged_baseline

    def _pty_turn(self, session, prompt_of) -> dict:
        from scripts.test_os37_lifecycle_boundary_regressions import INJECTED_REHEARSALS
        receipt = session.start(payload="rehearsal", **INJECTED_REHEARSALS)
        self.sessions.append(session)
        self.assertEqual(receipt["start_outcome"], "ready", receipt)
        ready = session.await_ready()
        self.assertEqual(ready["state"], "READY", ready)
        session.send({"payload": prompt_of(session.session_id)})
        return session.await_completion()

    def _adopt_and_settle(self, live):
        adopted = _stranger(live)
        self.assertTrue(adopted.adopt(fence=live.fence)["adopted"])
        return adopted, adopted.await_completion()

    def _assert_not_completed_over_full_prefix(self, live, adopted, adopted_result, *, forged_baseline):
        self.assertNotEqual(adopted_result["state"], "COMPLETED",
                            f"the forged baseline produced a COMPLETED live did not: {adopted_result}")
        self.assertEqual(adopted._settlement_baseline, 0, "adoption adopted the forged baseline")
        rng = adopted_result["evidence"].get("settlement_range") or {}
        self.assertEqual(rng.get("baseline"), 0, f"the examined range was narrowed: {rng}")
        # the refusal the forged baseline tried to hide is inside the examined range
        self.assertIn(b"not logged in", adopted.capture.raw()[0:int(adopted._boundary["offset_n"])])
        self.assertGreater(forged_baseline, 0)

    def test_l1_a_forged_later_baseline_cannot_hide_a_refusal_on_adoption(self) -> None:
        """L-1: rewrite the row's `baseline` PAST the refusal and re-digest -> adopted is NOT
        COMPLETED (FAILED `refusal_in_boundary`), via the REAL adopt path.  991847f: COMPLETED."""
        live, live_result, forged = self._live_refusal_after_echo("pr36-bl-l1")
        _rewrite_delivery_rows(self, live, lambda row: row["source_vocabulary"].__setitem__("baseline", forged))
        adopted, adopted_result = self._adopt_and_settle(live)
        self._assert_not_completed_over_full_prefix(live, adopted, adopted_result, forged_baseline=forged)
        self.assertEqual(((adopted_result.get("verdict") or {}).get("reason")), REFUSAL_IN_BOUNDARY, adopted_result)
        # the live baseline survives only as a diagnostic; the forged row value is inert
        self.assertGreater(live._settlement_baseline, 0)

    def test_l2_a_malformed_event_row_with_a_forged_baseline_does_not_narrow_the_range(self) -> None:
        """L-2: the same forged later baseline AND a malformed event list (a `payload` key; a
        non-list) -> the range is NOT narrowed (settlement_range.baseline == 0) and the verdict
        is not COMPLETED.  991847f left the forged baseline applied even after rejecting the
        events, so `[forged, N)` hid the refusal and it settled COMPLETED."""
        for name, malform in (
            ("payload_key", lambda row: [ev.__setitem__("payload", "x") for ev in row["source_vocabulary"]["events"]]),
            ("events_not_a_list", lambda row: row["source_vocabulary"].__setitem__("events", "nope")),
        ):
            with self.subTest(shape=name):
                live, live_result, forged = self._live_refusal_after_echo(f"pr36-bl-l2-{name}"[:60])

                def mutate(row, _malform=malform):
                    row["source_vocabulary"]["baseline"] = forged
                    _malform(row)

                _rewrite_delivery_rows(self, live, mutate)
                adopted, adopted_result = self._adopt_and_settle(live)
                self._assert_not_completed_over_full_prefix(live, adopted, adopted_result, forged_baseline=forged)
                # nothing from the malformed row was restored
                self.assertEqual(adopted.delivery_events, [], adopted.delivery_events)
                named = [r for r in adopted.journal.rows_for(live.intent_id)
                         if r.get("event") == "delivery_provenance_unrestored"]
                self.assertTrue(named, "the malformed row was not named")
                self.assertEqual(named[-1]["source_vocabulary"]["settlement_baseline"], 0)
                self.assertEqual(named[-1]["source_vocabulary"]["journal_baseline_diagnostic"], forged)

    def test_l3_missing_and_multiple_rows_never_narrow_the_range(self) -> None:
        """L-3: (a) a MISSING `delivery_recorded` row -> baseline 0, full `[0, N)`, verdict no
        wider than live; (b) a SECOND row appended with a forged later baseline as the LAST row
        -> the last row does NOT substitute an authority (baseline stays 0, not COMPLETED).
        991847f trusted `named[-1]`, so the appended forged row narrowed the range to COMPLETED."""
        # (a) missing row: baseline 0 at both 991847f and after -- the fail-closed property lock
        live_a, result_a, _ = self._live_refusal_after_echo("pr36-bl-l3-missing")
        self.assertEqual(_remove_delivery_rows(self, live_a), 1)
        adopted_a, adopted_result_a = self._adopt_and_settle(live_a)
        self.assertEqual(adopted_a._settlement_baseline, 0)
        self.assertNotEqual(adopted_result_a["state"], "COMPLETED", adopted_result_a)
        self.assertEqual((adopted_result_a["evidence"].get("settlement_range") or {}).get("baseline"), 0)
        self.assertEqual(adopted_a.delivery_events, [], "a missing row restored an event")
        # (b) multiple rows, forged last row past the refusal
        live_b, result_b, forged = self._live_refusal_after_echo("pr36-bl-l3-multi")
        _append_forged_last_row(self, live_b, baseline=forged)
        named_rows = [r for r in journal_mod.ExecutionJournal(live_b.artifact_base, live_b.run_id).rows_for(live_b.intent_id)
                      if r.get("event") == "delivery_recorded"]
        self.assertEqual(len(named_rows), 2, "the second row was not appended")
        self.assertEqual(named_rows[-1]["source_vocabulary"]["baseline"], forged)
        adopted_b, adopted_result_b = self._adopt_and_settle(live_b)
        self._assert_not_completed_over_full_prefix(live_b, adopted_b, adopted_result_b, forged_baseline=forged)
        self.assertEqual(((adopted_result_b.get("verdict") or {}).get("reason")), REFUSAL_IN_BOUNDARY, adopted_result_b)


# =====================================================================================
# L-6 -- both implementation copies byte-identical
# =====================================================================================
class PR36L6ParityTests(unittest.TestCase):
    MODULES = ("standalone_runtime.py", "standalone_capture.py", "standalone_pty.py",
               "standalone_drivers.py", "standalone_journal.py", "standalone_lifecycle.py",
               # run_11b4061df84d (L-7): the profile validator is where the R3 carrier fix lives
               "standalone_profile.py")

    def test_l6_the_deploy_copy_and_the_mirror_are_byte_identical(self) -> None:
        deploy = REPO / "orca-worker-reviewer-orchestration" / "tools" / "deterministic_workflow"
        mirror = REPO / "scripts" / "deterministic_workflow"
        for name in self.MODULES:
            with self.subTest(module=name):
                self.assertEqual((deploy / name).read_bytes(), (mirror / name).read_bytes(),
                                 f"{name}: the deploy copy and the mirror differ")


# =====================================================================================
# run_11b4061df84d -- adopted settlement never mints positive authority from [0, N)
# (PR #36 comments 5747199243 §3 / 5747098383 [P1] at 37b3f58; REVIEW_BUGFIX F-001/F-002;
#  USER_DECISION_C2.md)
# =====================================================================================
#: the C2 outcome, spelled as a literal so 37b3f58 fails on BEHAVIOUR (an adopted COMPLETED),
#: never on a missing attribute
ADOPTED_BASELINE_UNKNOWN = "adopted_baseline_unknown"


def _c2_rule():
    """The production C2 function -- or the IDENTITY on a tree without it (37b3f58), so the
    locks below fail on BEHAVIOUR (an adopted success not withheld), never on an attribute."""
    return getattr(rt, "withhold_adopted_post_ready_success", None) or (lambda selection, **_kw: dict(selection))


def _r3_spec(delivery_mode: str, binding_mode: str, carrier: bool, *, worktree: str = "/tmp",
             record_type: str = "result", field: str | None = None) -> dict:
    """An `sh` claude-driver profile MAPPING (the production loader's shape) for one cell of
    the delivery_mode x binding_mode x carrier matrix.  `binding_field` follows the mode
    (`session_field` binds on `session_id`, `sidecar_file` on `thread_id`), the carrier is
    `thread.started` when declared."""
    completion = {"channel": "structured", "record_type": record_type, "error_field": "is_error",
                  "binding_mode": binding_mode}
    if field is None:
        field = {"session_field": "session_id", "sidecar_file": "thread_id"}.get(binding_mode, "")
    if field:
        completion["binding_field"] = field
    if carrier:
        completion["carrier_type"] = "thread.started"
    spec = {
        "driver": "claude", "binary": "sh", "supported_range": [[1, 0, 0], [9, 0, 0]],
        "bin_dirs": ["/bin"], "worktree": worktree,
        "readiness_records": [{"channel": "structured", "record_type": "system",
                               "session_field": "session_id"}],
        "completion_records": [completion],
        "delivery_mode": delivery_mode, "identity_binding": "minted_echo",
        "identity_flag": "--session-id",
    }
    if delivery_mode == "launch_with_prompt":
        spec["delivery_proofs"] = [{"channel": "structured", "record_type": "assistant"}]
    return spec


def _r3_profile_direct(spec: dict):
    """The SAME cell through the direct dataclass constructor (no loader)."""
    from scripts.deterministic_workflow.standalone_profile import (CompletionSelector,
                                                                   DeliveryProofSelector,
                                                                   ReadinessSelector,
                                                                   StandaloneProfile)
    c = spec["completion_records"][0]
    return StandaloneProfile(
        driver=spec["driver"], binary=spec["binary"],
        supported_range=tuple(tuple(b) for b in spec["supported_range"]),
        bin_dirs=tuple(spec["bin_dirs"]), worktree=spec["worktree"],
        delivery_mode=spec["delivery_mode"], identity_binding=spec["identity_binding"],
        identity_flag=spec["identity_flag"],
        readiness_records=tuple(ReadinessSelector(**r) for r in spec["readiness_records"]),
        delivery_proofs=tuple(DeliveryProofSelector(**d) for d in spec.get("delivery_proofs", ())),
        completion_records=(CompletionSelector(
            channel=c["channel"], record_type=c["record_type"], error_field=c["error_field"],
            binding_mode=c["binding_mode"], binding_field=c.get("binding_field", ""),
            carrier_type=c.get("carrier_type", "")),))


#: every cell of the supported matrix -- ALL admitted (USER_DECISION_C2.md: the i1
#: `PreBaselineCarrierRefused` is removed; no live profile surface is forbidden)
R3_CELLS = tuple((mode, binding, carrier)
                 for mode in ("launch_with_prompt", "post_ready_delivery")
                 for binding in ("session_field", "sidecar_file", "single_record_optin")
                 for carrier in (True, False))


class _RangeSettler:
    """The production selector over `[live_baseline, N)` (what the LIVE session examines: under
    `post_ready_delivery` `send()` sets `_settlement_baseline = capture.size` AFTER readiness,
    so the baseline is the byte after the last pre-delivery record; under `launch_with_prompt`
    `send()` is never called and the baseline is 0) and over `[0, N)` (what the ADOPTED session
    examines since run_d6391487ff44: no unkeyed journal baseline) -- the adopted selection then
    passes through the PRODUCTION C2 rule `standalone_runtime.withhold_adopted_post_ready_success`
    exactly as `StandaloneSession.completion()` applies it.  `verdict_of` classifies a selection
    as the runtime does: COMPLETED only when the selector returns a record with no outcome AND
    `completion_verdict` (exit 0) succeeds; otherwise the NAMED outcome."""

    READY = {"type": "system", "session_id": "S"}
    CARRIER = {"type": "thread.started", "thread_id": "S"}
    CARRIER_OTHER = {"type": "thread.started", "thread_id": "OTHER"}

    def __init__(self, case: unittest.TestCase, profile) -> None:
        from scripts.deterministic_workflow import standalone_drivers as drivers
        self.case = case
        self.profile = profile
        self.driver = drivers.driver_for(profile)

    @staticmethod
    def lines(records) -> str:
        return "".join((json.dumps(r) if isinstance(r, dict) else str(r)) + "\n" for r in records)

    def select(self, text: str, **kw) -> dict:
        raw = text.encode()
        return self.driver.select_completion(text, raw=raw, **kw)

    def verdict_of(self, sel: Mapping[str, Any]) -> str:
        if sel["outcome"] is not None:
            return f"LOST/FAILED {sel['outcome']}"
        if sel["record"] is None:
            return "FAILED no_completion_record"
        verdict = self.driver.completion_verdict(sel["record"], exit_status=0)
        if verdict["outcome"] == "succeeded":
            return "COMPLETED"
        return f"{verdict['outcome'].upper()} {verdict['reason']}"

    def settle(self, pre: list, post: list, **kw) -> tuple[str, str, dict, dict, dict]:
        """``pre`` records are emitted BEFORE the prompt delivery, ``post`` after.  Returns
        (live verdict, adopted verdict, live selection, adopted selection, the RAW adopted
        selection before the C2 rule)."""
        pre_text, post_text = self.lines(pre), self.lines(post)
        whole = pre_text + post_text
        baseline = len(pre_text.encode()) if self.profile.delivery_mode == "post_ready_delivery" else 0
        rule = _c2_rule()
        live = rule(self.select(whole.encode()[baseline:].decode(), **kw), adopted=False,
                    delivery_mode=self.profile.delivery_mode)
        raw_adopted = self.select(whole, **kw)
        adopted = rule(raw_adopted, adopted=True, delivery_mode=self.profile.delivery_mode)
        return self.verdict_of(live), self.verdict_of(adopted), live, adopted, raw_adopted


def _r3_completion(*, ok: bool, bound_field: str | None = None) -> dict:
    rec: dict = {"type": "result", "is_error": not ok}
    if bound_field:
        rec[bound_field] = "S"
    return rec


class PR36R3CarrierAuthorityTests(unittest.TestCase):
    """run_11b4061df84d.  The adopted range `[0, N)` (baseline 0, run_d6391487ff44) is a SUPERSET
    of the live `[baseline, N)`.  Range growth is monotone-safe for R1 / R2 / framing / scan
    (more evidence can only REJECT) but NOT for two positive paths: the R3 `sidecar_file`
    carrier fallback (a carrier BEFORE the live baseline binds a record live could not -- the
    P1 at 37b3f58) and, more generally, a sole bound completion record emitted BEFORE the prompt
    was delivered (REVIEW_BUGFIX F-001: `session_field` / `single_record_optin` / record-field
    `sidecar_file` under `post_ready_delivery`).  Nothing in the fenced bytes marks the delivery
    instant, so no baseline-independent selector rule can tell the two apart.

    USER DECISION C2 (USER_DECISION_C2.md): an ADOPTED settlement of a `post_ready_delivery`
    dispatch never returns a success -- refusal / reader-failure dominance is preserved first
    and a would-be success is the NAMED LOST outcome `adopted_baseline_unknown`
    (`standalone_runtime.withhold_adopted_post_ready_success`, applied in
    `StandaloneSession.completion()`, the one place every adopted settlement passes).  LIVE
    `post_ready_delivery` and BOTH `launch_with_prompt` paths (baseline 0: live range ==
    adopted range) are unchanged.  The i1 profile refusal `PreBaselineCarrierRefused` is
    REMOVED: with C2 the combination cannot produce an adopted-only success and its live
    behaviour was never the defect.  Driver-level locks here (the production selector + the
    production C2 function); the real-session locks are in `PR36R3CarrierAuthorityNativeTests`."""

    P1 = ("post_ready_delivery", "sidecar_file", True)

    # ---- L-1 (a): the P1 profile is ADMITTED; live binds iff the carrier is in range; adopted never
    def test_l1a_the_p1_profile_is_admitted_and_can_never_settle_adopted_only(self) -> None:
        """Both constructors admit `post_ready_delivery + sidecar_file + carrier` (no
        ProfileError of any name); live binds through the carrier iff the carrier lies inside
        `[baseline, N)` (pre-baseline -> `provenance_unbound`, post-baseline -> COMPLETED);
        adopted is LOST `adopted_baseline_unknown` in both shapes -- never COMPLETED, and never
        the pre-baseline-carrier success 37b3f58 produced."""
        spec = _r3_spec(*self.P1)
        for name, build in (("profile_from_mapping", lambda: profile_from_mapping(spec)),
                            ("StandaloneProfile(...)", lambda: _r3_profile_direct(spec))):
            with self.subTest(constructor=name):
                profile = build()                       # raises -> the surface was forbidden
                settler = _RangeSettler(self, profile)
                lv, av, live, adopted, raw = settler.settle([settler.READY, settler.CARRIER], [_r3_completion(ok=True)],
                                                            bound_value="S", sidecar_present=True)
                self.assertEqual(lv, "LOST/FAILED provenance_unbound", live)
                self.assertEqual(av, f"LOST/FAILED {ADOPTED_BASELINE_UNKNOWN}", adopted)
                self.assertEqual(settler.verdict_of(raw), "COMPLETED", "the raw [0, N) selection is not the P1 shape")
                self.assertIsNone(adopted["record"], adopted)
                self.assertEqual(adopted["adoption"]["withheld_record_type"], "result", adopted)
                lv, av, live, adopted, raw = settler.settle([settler.READY], [settler.CARRIER, _r3_completion(ok=True)],
                                                            bound_value="S", sidecar_present=True)
                self.assertEqual(lv, "COMPLETED", live)          # live: unchanged positive path
                self.assertEqual(av, f"LOST/FAILED {ADOPTED_BASELINE_UNKNOWN}", adopted)

    def test_l1a_every_cell_of_the_supported_matrix_is_admitted(self) -> None:
        """USER_DECISION_C2.md: no live profile surface is forbidden -- all 12 cells load
        through both constructors."""
        for cell in R3_CELLS:
            with self.subTest(cell=cell):
                spec = _r3_spec(*cell)
                profile_from_mapping(spec)
                _r3_profile_direct(spec)

    # ---- L-1 (c): no admitted profile binds through a carrier live could not see -------------
    def test_l1c_no_admitted_carrier_profile_binds_through_a_carrier_live_could_not_see(self) -> None:
        """For every admitted cell that declares a carrier: `session_field` /
        `single_record_optin` never consult it (selection identical with and without the
        carrier), `launch_with_prompt` examines the same range on both sides (identical
        selection), and under `post_ready_delivery` the adopted side is never COMPLETED."""
        for mode in ("launch_with_prompt", "post_ready_delivery"):
            for binding in ("session_field", "sidecar_file", "single_record_optin"):
                settler = _RangeSettler(self, profile_from_mapping(_r3_spec(mode, binding, True)))
                sidecar = binding == "sidecar_file"
                bound_field = {"session_field": "session_id"}.get(binding)
                for shape, pre, post in (
                        ("carrier_pre/bound_post", [settler.READY, settler.CARRIER], [_r3_completion(ok=True, bound_field=bound_field)]),
                        ("carrier_post/bound_post", [settler.READY], [settler.CARRIER, _r3_completion(ok=True, bound_field=bound_field)]),
                        ("carrier_absent/bound_post", [settler.READY], [_r3_completion(ok=True, bound_field=bound_field)])):
                    with self.subTest(cell=(mode, binding), shape=shape):
                        lv, av, live, adopted, _raw = settler.settle(pre, post, bound_value="S", sidecar_present=sidecar)
                        if av == "COMPLETED":
                            self.assertEqual(lv, "COMPLETED", f"adopted-only success: live {lv} / adopted {av}")
                            self.assertEqual(adopted["record"], live["record"])
                        if mode == "post_ready_delivery":
                            self.assertNotEqual(av, "COMPLETED", f"an adopted post_ready_delivery success: {adopted}")
                        else:
                            self.assertEqual(live, adopted, "launch_with_prompt: live and adopted examined the same range and differ")
                        if binding != "sidecar_file":
                            lv2, av2, _, _, _ = settler.settle([r for r in pre if r is not settler.CARRIER],
                                                                [r for r in post if r is not settler.CARRIER],
                                                                bound_value="S", sidecar_present=sidecar)
                            self.assertEqual((lv, av), (lv2, av2), f"the carrier changed a {binding} selection")

    # ---- L-2: the normal carrier path keeps its positive behaviour (live, both modes) -------
    def test_l2_the_normal_carrier_path_keeps_its_positive_behaviour(self) -> None:
        """The L-02b contract on the LIVE side under both delivery modes: record lacking the
        field, the carrier before it (inside the live range) carries it, sidecar present ⇒
        bound; a different / empty / absent thread, a missing sidecar, an empty bound value ⇒
        `provenance_unbound`.  On adoption `launch_with_prompt` is identical and
        `post_ready_delivery` is never COMPLETED.  GREEN at 37b3f58 for the live legs (a pure
        regression guard); RED there for the post_ready adopted leg."""
        for mode in ("launch_with_prompt", "post_ready_delivery"):
            settler = _RangeSettler(self, profile_from_mapping(_r3_spec(mode, "sidecar_file", True)))
            bound = [settler.READY, settler.CARRIER, _r3_completion(ok=True)]
            with self.subTest(mode=mode, case="bound"):
                lv, av, live, adopted, _ = settler.settle([settler.READY], bound[1:], bound_value="S", sidecar_present=True)
                self.assertEqual(lv, "COMPLETED", live)
                self.assertEqual(live["record"], _r3_completion(ok=True))
                self.assertEqual(av, "COMPLETED" if mode == "launch_with_prompt" else f"LOST/FAILED {ADOPTED_BASELINE_UNKNOWN}", adopted)
            for name, records, kw in (
                    ("different thread", [settler.READY, settler.CARRIER_OTHER, _r3_completion(ok=True)], dict(bound_value="S", sidecar_present=True)),
                    ("empty thread", [settler.READY, {"type": "thread.started", "thread_id": ""}, _r3_completion(ok=True)], dict(bound_value="S", sidecar_present=True)),
                    ("absent carrier", [settler.READY, _r3_completion(ok=True)], dict(bound_value="S", sidecar_present=True)),
                    ("missing sidecar", bound, dict(bound_value="S", sidecar_present=False)),
                    ("empty bound", bound, dict(bound_value="", sidecar_present=True))):
                with self.subTest(mode=mode, case=name):
                    lv, av, live, adopted, _ = settler.settle([records[0]], records[1:], **kw)
                    self.assertEqual((lv, av), ("LOST/FAILED provenance_unbound",) * 2, (live, adopted))
                    self.assertIsNone(live["record"])

    # ---- L-3 (driver level): the shipping profiles load, unchanged ---------------------------
    def test_l3_the_shipping_profiles_load_unchanged(self) -> None:
        """`codex_profile` (launch_with_prompt + sidecar_file + carrier thread.started) and
        `claude_profile` (launch_with_prompt + session_field -- BOTH shipping profiles are
        `launch_with_prompt`, `scripts/os37_r10_real_agent.py`) construct exactly as before; a
        codex-shaped capture (thread.started, then the sole turn.completed lacking thread_id)
        settles COMPLETED over the same range live and adopted (baseline 0 ⇒ same range; C2
        does not apply), and a missing sidecar / a different thread is `provenance_unbound` on
        both."""
        from scripts.os37_r10_real_agent import claude_profile, codex_profile
        codex = codex_profile("/tmp", "/tmp")
        self.assertEqual(codex.delivery_mode, "launch_with_prompt")
        self.assertEqual((codex.completion_records[0].binding_mode, codex.completion_records[0].binding_field,
                          codex.completion_records[0].carrier_type), ("sidecar_file", "thread_id", "thread.started"))
        claude = claude_profile("/tmp")
        self.assertEqual(claude.delivery_mode, "launch_with_prompt")
        self.assertEqual((claude.completion_records[0].binding_mode, claude.completion_records[0].carrier_type),
                         ("session_field", ""))
        settler = _RangeSettler(self, codex)
        started = {"type": "thread.started", "thread_id": "T1"}
        done = {"type": "turn.completed", "usage": {"input_tokens": 1}}
        lv, av, live, adopted, _ = settler.settle([started, done], [], bound_value="T1", sidecar_present=True)
        self.assertEqual((lv, av), ("COMPLETED", "COMPLETED"), (live, adopted))
        self.assertEqual(live, adopted)
        self.assertEqual(live["record"], done)
        for name, records, kw in (("missing sidecar", [started, done], dict(bound_value="T1", sidecar_present=False)),
                                  ("different thread", [{"type": "thread.started", "thread_id": "T2"}, done],
                                   dict(bound_value="T1", sidecar_present=True))):
            with self.subTest(case=name):
                lv, av, live, adopted, _ = settler.settle(records, [], **kw)
                self.assertEqual((lv, av), ("LOST/FAILED provenance_unbound",) * 2, (live, adopted))
                self.assertEqual(live, adopted)

    # ---- L-6: the asserted invariant matrix ----------------------------------------------------
    SHAPES = (
        # (name, pre-delivery records, post-delivery records)  -- CARRIER = `thread.started`
        ("carrier_pre/bound_post", ["READY", "CARRIER"], ["OK"]),
        ("carrier_post/bound_post", ["READY"], ["CARRIER", "OK"]),
        ("carrier_absent/bound_post", ["READY"], ["OK"]),
        ("carrier_pre/refusing_post", ["READY", "CARRIER"], ["ERR"]),
        ("carrier_post/refusing_post", ["READY"], ["CARRIER", "ERR"]),
        ("carrier_absent/refusing_post", ["READY"], ["ERR"]),
        ("refusal_pre/bound_post", ["READY", "ERR"], ["OK"]),
        ("prose_refusal_pre/bound_post", ["READY", "not logged in"], ["OK"]),
        ("echo_example_pre/bound_post", ["READY", "EXAMPLE"], ["OK"]),
        ("carrier_pre/two_bound_post", ["READY", "CARRIER"], ["OK", "OK"]),
        ("carrier_pre/nothing_post", ["READY", "CARRIER"], []),
        # REVIEW_BUGFIX F-002: the pre-delivery completion shapes, ASSERTED
        ("sole_completion_pre_delivery", ["READY", "CARRIER", "OK"], []),
        ("completion_pre/refusal_post", ["READY", "CARRIER", "OK"], ["ERR"]),
        ("completion_pre/second_completion_post", ["READY", "CARRIER", "OK"], ["OK"]),
        ("completion_pre/prose_refusal_post", ["READY", "CARRIER", "OK"], ["not logged in"]),
    )

    def _records(self, settler: _RangeSettler, names: list[str], binding: str) -> list:
        bound_field = {"session_field": "session_id"}.get(binding)
        table = {"READY": settler.READY, "CARRIER": settler.CARRIER,
                 "OK": _r3_completion(ok=True, bound_field=bound_field),
                 "ERR": _r3_completion(ok=False, bound_field=bound_field),
                 "EXAMPLE": "example: " + json.dumps(_r3_completion(ok=True, bound_field=bound_field))}
        return [table.get(n, n) for n in names]

    def test_l6_the_supported_profile_matrix_never_settles_adopted_only(self) -> None:
        """delivery_mode x binding_mode x carrier declared/not (12 cells, ALL admitted) x 15
        capture shapes incl. the pre-delivery completion shapes: on every cell/shape
        `adopted COMPLETED ⇒ live COMPLETED` on the same record; `launch_with_prompt` is
        identical on both sides; under `post_ready_delivery` the adopted side is NEVER
        COMPLETED and is `adopted_baseline_unknown` exactly where the raw `[0, N)` selection
        would have been a success (the C2 rule withholds a success and nothing else -- every
        refusal / reader-failure outcome is preserved).  The matrix is printed for BUGFIX.md."""
        rows: list[str] = []
        for mode, binding, carrier in R3_CELLS:
            cell = f"{mode} + {binding} + carrier={'yes' if carrier else 'no'}"
            spec = _r3_spec(mode, binding, carrier)
            profile_from_mapping(spec)
            _r3_profile_direct(spec)
            settler = _RangeSettler(self, profile_from_mapping(spec))
            sidecar = binding == "sidecar_file"
            outcomes = []
            for shape, pre, post in self.SHAPES:
                with self.subTest(cell=cell, shape=shape):
                    lv, av, live, adopted, raw = settler.settle(self._records(settler, pre, binding),
                                                                 self._records(settler, post, binding),
                                                                 bound_value="S", sidecar_present=sidecar)
                    if av == "COMPLETED":
                        self.assertEqual(lv, "COMPLETED", f"{cell} / {shape}: adopted-only success (live {lv})")
                        self.assertEqual(adopted["record"], live["record"], f"{cell} / {shape}: different record")
                    if mode == "launch_with_prompt":
                        self.assertEqual(live, adopted, f"{cell} / {shape}: baseline 0 yet live != adopted")
                    else:
                        self.assertNotEqual(av, "COMPLETED", f"{cell} / {shape}: an adopted post_ready_delivery success")
                        rv = settler.verdict_of(raw)
                        if rv == "COMPLETED":
                            self.assertEqual(av, f"LOST/FAILED {ADOPTED_BASELINE_UNKNOWN}", f"{cell} / {shape}: a raw success not withheld by name")
                            self.assertIsNone(adopted["record"])
                        else:
                            self.assertEqual(av, rv, f"{cell} / {shape}: C2 changed a non-success outcome")
                    outcomes.append(f"{shape}: {lv} / {av}")
            rows.append(f"| {cell} | " + "<br>".join(outcomes) + " |")
        print("\nL-6 supported profile matrix (live / adopted per capture shape; ALL 12 cells admitted):")
        print("| cell | shapes |\n|---|---|")
        print("\n".join(rows))

    def test_l6_the_c2_rule_withholds_only_a_success(self) -> None:
        """`withhold_adopted_post_ready_success` itself: inert for live and for
        `launch_with_prompt`; every non-success selection (refusal, ambiguity, unbound,
        framing, scan) passes through unchanged; a success becomes `adopted_baseline_unknown`
        with the record withheld and the diagnostic naming the withheld type."""
        success = {"record": {"type": "result", "is_error": False}, "outcome": None, "refusal": None, "candidates": 1}
        refusing = {"record": {"type": "result", "is_error": True}, "outcome": None,
                    "refusal": {"source": "error_field"}, "candidates": 1}
        rule = _c2_rule()
        for name, sel in (("success/live", success), ("success/launch", success)):
            adopted = name.endswith("launch")
            out = rule(sel, adopted=adopted, delivery_mode="launch_with_prompt" if adopted else "post_ready_delivery")
            self.assertEqual(out, sel, name)
        out = rule(success, adopted=True, delivery_mode="post_ready_delivery")
        self.assertEqual((out.get("outcome"), out.get("record"), (out.get("adoption") or {}).get("withheld_record_type")),
                         (ADOPTED_BASELINE_UNKNOWN, None, "result"), out)
        self.assertEqual(rule(refusing, adopted=True, delivery_mode="post_ready_delivery"), refusing)
        none_reached = {"record": None, "outcome": None, "refusal": None, "candidates": 0}
        self.assertEqual(rule(none_reached, adopted=True, delivery_mode="post_ready_delivery"), none_reached,
                         "no record reached the verdict: FAILED no_completion_record stays")
        for outcome in ("refusal_in_boundary", "provenance_ambiguous", "provenance_unbound",
                        "record_framing_ambiguous", "record_scan_incomplete"):
            sel = {"record": None, "outcome": outcome, "refusal": None, "candidates": 0}
            self.assertEqual(rule(sel, adopted=True, delivery_mode="post_ready_delivery"), sel, outcome)
        self.assertIn(ADOPTED_BASELINE_UNKNOWN, lifecycle.LOST_REASONS)
        self.assertIn(ADOPTED_BASELINE_UNKNOWN, lifecycle.OS48_LOST_OUTCOMES)
        self.assertEqual(getattr(capture_mod, "OUTCOME_ADOPTED_BASELINE_UNKNOWN", ""), ADOPTED_BASELINE_UNKNOWN)


class PR36R3CarrierAuthorityNativeTests(_StubTurn):
    """The same contract through the REAL `StandaloneSession` live-vs-`adopt()` path over the
    native stub (as `PR36F003AdoptedSuccessSubsetTests` does) and through the production
    recovery path `StandaloneAdapter._collect_in_flight`."""

    #: post-ready turn (no echo): readiness, then the CARRIER (pre-delivery by construction),
    #: then the prompt read, then the completion WITHOUT the binding field
    _CARRIER_AGENT = """SID="$1"
stty -echo icanon
printf '{"type":"system","session_id":"%%s"}\\n' "$SID"
printf '{"type":"thread.started","thread_id":"%%s"}\\n' "$SID"
IFS= read -r PROMPT
%(records)s
exit 0
"""
    #: the sole-pre-delivery shape: readiness, a BOUND success, THEN the prompt read, nothing after
    _PRE_DELIVERY_AGENT = """SID="$1"
stty -echo icanon
printf '{"type":"system","session_id":"%%s"}\\n' "$SID"
%(records)s
IFS= read -r PROMPT
exit 0
"""
    #: argv turn (launch_with_prompt): readiness, the CARRIER, the delivery proof, the completion
    _CODEX_SHAPED_AGENT = """SID="$1"; PROMPT="$2"; THREAD="%(thread)s"
printf '{"type":"system","session_id":"%%s"}\\n' "$SID"
printf '{"type":"thread.started","thread_id":"%%s"}\\n' "$THREAD"
sleep 0.2
printf '{"type":"assistant","session_id":"%%s","request_id":"req_stub_agent_1","message":{"model":"stub-agent-model-1","id":"msg_stub_agent_1","usage":{"input_tokens":2,"output_tokens":1}}}\\n' "$SID"
printf '{"type":"turn.completed","usage":{"input_tokens":1}}\\n'
exit 0
"""

    def _sidecar_session(self, run_id: str, script_body: str, *, delivery_mode: str, completion: dict,
                         sidecar: bool):
        """A stub session whose profile declares `output_last_message_path` (so the runtime
        mints the dispatch-scoped sidecar and the watcher snapshots its presence at the
        boundary) and the given completion selector.  ``sidecar`` pre-creates the file."""
        script = self.base / f"agent-{run_id}.sh"
        script.write_text(script_body)
        spec = _stub_spec(str(self.base), str(script))
        spec["delivery_mode"] = delivery_mode
        if delivery_mode == "launch_with_prompt":
            spec["delivery_proofs"] = [{"channel": "structured", "record_type": "assistant"}]
        spec["completion_records"] = [completion]
        spec["output_last_message_path"] = str(self.base / "last_message.md")
        profile = profile_from_mapping(spec)            # raises -> the surface was forbidden
        session = rt.StandaloneSession(
            intent={"intent_id": f"i-{run_id}", "run_id": run_id, "role": "WORKER",
                    "task_id": f"t-{run_id}", "dispatch_id": f"d-{run_id}"},
            profile=profile, artifact_base=self.base / "art", run_id=run_id,
            journal=journal_mod.ExecutionJournal(self.base / "art", run_id))
        self.assertTrue(session.last_message_path, "the runtime minted no dispatch-scoped sidecar path")
        if sidecar:
            Path(session.last_message_path).parent.mkdir(parents=True, exist_ok=True)
            Path(session.last_message_path).write_text("final body\n")
        return session

    @staticmethod
    def _verdict(result) -> tuple:
        return PR36F003AdoptedSuccessSubsetTests._verdict_of(result)

    def _adopted(self, live):
        adopted = _stranger(live)
        outcome = adopted.adopt(fence=live.fence)
        self.assertTrue(outcome["adopted"], outcome)
        return adopted, adopted.await_completion()

    def _assert_withheld(self, live, live_result, adopted, adopted_result, *, withheld_type: str = "result") -> None:
        """The C2 outcome BY NAME on the real path: LOST `adopted_baseline_unknown`, no
        settlement record, the diagnostic naming the withheld record type, journalled; the
        adoption examined [0, N) (baseline 0) and excised nothing."""
        self.assertEqual(self._verdict(adopted_result), ("LOST", ADOPTED_BASELINE_UNKNOWN), adopted_result)
        self.assertIsNone(adopted_result["evidence"].get("settlement_record"), adopted_result["evidence"])
        vocab = adopted_result["evidence"].get("source_vocabulary") or {}
        self.assertEqual((vocab.get("adoption") or {}).get("withheld_record_type"), withheld_type, vocab)
        rows = [r for r in adopted.journal.rows_for(live.intent_id)
                if (r.get("source_vocabulary") or {}).get("provenance_outcome") == ADOPTED_BASELINE_UNKNOWN]
        self.assertGreaterEqual(len(rows), 1, "the C2 outcome was not journalled by name")
        self.assertEqual(rows[-1]["source_vocabulary"].get("adoption", {}).get("withheld_record_type"), withheld_type, rows[-1])
        _assert_not_wider(self, live, live_result, adopted, adopted_result)

    # ---- L-1 (b): the P1 counterexample on the real session path ----------------------------
    def test_l1b_the_p1_counterexample_never_settles_adopted_only_on_the_real_session_path(self) -> None:
        """The reviewers' counterexample END TO END: `post_ready_delivery + sidecar_file +
        carrier thread.started` (ADMITTED); the agent emits the carrier at readiness (BEFORE
        `send()` sets the baseline) and, after the prompt, a `result` lacking `thread_id`; the
        sidecar is present.  Live: LOST `provenance_unbound` (the carrier is outside
        [baseline, N)).  Adopted: LOST `adopted_baseline_unknown` by name -- 37b3f58 settled
        COMPLETED here (RED, the reproduction itself)."""
        session = self._sidecar_session(
            "pr36-r3-l1b", self._CARRIER_AGENT % {"records": _r3_lines(ok=True)},
            delivery_mode="post_ready_delivery",
            completion={"channel": "structured", "record_type": "result", "error_field": "is_error",
                        "binding_mode": "sidecar_file", "binding_field": "thread_id",
                        "carrier_type": "thread.started"}, sidecar=True)
        live_result = PR36F003AdoptedSuccessSubsetTests._pty_turn(self, session, lambda sid: "Continue the task.")
        self.assertGreater(int(session._settlement_baseline or 0), 0, "post_ready_delivery set no baseline")
        baseline, n, fenced = self._fenced(session)
        self.assertNotIn(b'"thread.started"', fenced, "the carrier is not pre-baseline")
        self.assertEqual(session._sidecar_state, capture_mod.SIDECAR_STATE_PRESENT, session._sidecar_state)
        adopted, adopted_result = self._adopted(session)
        lo, ao = self._verdict(live_result), self._verdict(adopted_result)
        print(f"\nL-1b: live {lo} range [{baseline},{n}) / adopted {ao} range "
              f"[{(adopted_result['evidence'].get('settlement_range') or {}).get('baseline')},{n})")
        self.assertEqual(lo, ("LOST", "provenance_unbound"), live_result)
        self._assert_withheld(session, live_result, adopted, adopted_result)

    # ---- L-3: the shipping Codex shape, live and adopted (unchanged) --------------------------
    def _codex_shaped(self, run_id: str, *, thread: str, sidecar: bool):
        session = self._sidecar_session(
            run_id, self._CODEX_SHAPED_AGENT % {"thread": thread},
            delivery_mode="launch_with_prompt",
            completion={"channel": "structured", "record_type": "turn.completed",
                        "binding_mode": "sidecar_file", "binding_field": "thread_id",
                        "carrier_type": "thread.started"}, sidecar=sidecar)
        try:
            self._argv_turn(session, "Continue the task.")
        except rt.StandaloneDispatchFailed:
            pass                                    # a LOST dispatch raises; the settlement is read below
        live_result = session.await_completion()
        self.assertEqual(int(session._settlement_baseline or 0), 0, "launch_with_prompt set a non-zero baseline")
        adopted, adopted_result = self._adopted(session)
        return session, live_result, adopted, adopted_result

    def test_l3_the_shipping_codex_shape_settles_the_same_live_and_adopted(self) -> None:
        """`launch_with_prompt + sidecar_file + carrier`: thread.started then a sole
        turn.completed lacking thread_id, sidecar present ⇒ COMPLETED live AND adopted on the
        same record over the SAME range (baseline 0 on both; C2 does not apply); a missing
        sidecar or a different thread ⇒ LOST `provenance_unbound` on both."""
        live, live_result, adopted, adopted_result = self._codex_shaped("pr36-r3-l3-ok", thread="$SID", sidecar=True)
        self.assertEqual(self._verdict(live_result), ("COMPLETED", None), live_result)
        self.assertEqual(self._verdict(adopted_result), ("COMPLETED", None), adopted_result)
        self.assertEqual(adopted_result["evidence"]["settlement_record"].get("type"), "turn.completed")
        _assert_identical(self, live, live_result, adopted, adopted_result)
        for name, kw in (("missing sidecar", dict(thread="$SID", sidecar=False)),
                         ("different thread", dict(thread="OTHER", sidecar=True))):
            with self.subTest(case=name):
                live, live_result, adopted, adopted_result = self._codex_shaped(f"pr36-r3-l3-{name[:4]}", **kw)
                self.assertEqual(self._verdict(live_result), ("LOST", "provenance_unbound"), live_result)
                self.assertEqual(self._verdict(adopted_result), ("LOST", "provenance_unbound"), adopted_result)
                _assert_identical(self, live, live_result, adopted, adopted_result)

    # ---- L-4: session_field / single_record_optin, post_ready: live COMPLETED, adopted withheld
    def test_l4_session_field_and_single_record_optin_post_ready_adoption_is_withheld_by_name(self) -> None:
        """`post_ready_delivery` with a pre-baseline `thread.started` record present:
        `session_field` binds on the record itself and `single_record_optin` binds nothing --
        live COMPLETED on both; adopted LOST `adopted_baseline_unknown` on both (USER DECISION
        C2; i1 asserted COMPLETED / COMPLETED here).  The pty `session_field` cells of
        `PR36F003AdoptedSuccessSubsetTests` cover the transport x output matrix."""
        for name, completion, records in (
                ("session_field", {"channel": "structured", "record_type": "result", "error_field": "is_error",
                                   "binding_mode": "session_field", "binding_field": "session_id"}, _record(False)),
                ("single_record_optin", {"channel": "structured", "record_type": "result", "error_field": "is_error",
                                         "binding_mode": "single_record_optin"}, _r3_lines(ok=True))):
            with self.subTest(binding=name):
                session = self._sidecar_session(
                    f"pr36-r3-l4-{name[:7]}", self._CARRIER_AGENT % {"records": records},
                    delivery_mode="post_ready_delivery", completion=completion, sidecar=False)
                live_result = PR36F003AdoptedSuccessSubsetTests._pty_turn(self, session, lambda sid: "Continue the task.")
                self.assertGreater(int(session._settlement_baseline or 0), 0)
                self.assertNotIn(b'"thread.started"', self._fenced(session)[2])
                self.assertEqual(self._verdict(live_result), ("COMPLETED", None), live_result)
                adopted, adopted_result = self._adopted(session)
                self._assert_withheld(session, live_result, adopted, adopted_result)

    # ---- L-8: the sole-pre-delivery completion on the real path (REVIEW_BUGFIX F-001 / F-002)
    def test_l8_a_sole_completion_emitted_before_the_prompt_never_settles_adopted_only(self) -> None:
        """`post_ready_delivery + session_field`: readiness, a BOUND success, THEN the prompt
        read, exit 0 -- the i1 residual measurement, now asserted.  Live: no candidate in
        [baseline, N) -> not COMPLETED (`no_completion_record`).  Adopted: the sole record is
        inside [0, N) and binds -- 37b3f58 settled COMPLETED (RED); now LOST
        `adopted_baseline_unknown` by name."""
        session = self._session("pr36-r3-l8", self._PRE_DELIVERY_AGENT % {"records": _record(False)})
        live_result = PR36F003AdoptedSuccessSubsetTests._pty_turn(self, session, lambda sid: "Continue the task.")
        baseline, n, fenced = self._fenced(session)
        self.assertGreater(baseline, 0)
        self.assertNotIn(b'"result"', fenced, "the sole completion is not pre-baseline")
        self.assertIn(b'"result"', session.capture.raw()[:baseline])
        self.assertNotEqual(live_result["state"], "COMPLETED", live_result)
        self.assertEqual((live_result.get("verdict") or {}).get("reason"), "no_completion_record", live_result)
        adopted, adopted_result = self._adopted(session)
        print(f"\nL-8: live {self._verdict(live_result)} range [{baseline},{n}) / adopted {self._verdict(adopted_result)} range [0,{n})")
        self._assert_withheld(session, live_result, adopted, adopted_result)

    def test_l8_a_pre_delivery_completion_followed_by_a_refusal_or_a_second_completion_is_dominated_on_both_sides(self) -> None:
        """The other pre-delivery variants on the real path: completion BEFORE the prompt then
        a refusal after it -> FAILED `refusal_in_boundary` on both sides (R1 dominates the
        withheld success); completion before then a SECOND completion after -> live COMPLETED
        (one candidate in its range), adopted LOST `provenance_ambiguous` (two candidates in
        [0, N)) -- never `adopted_baseline_unknown`, which only ever replaces a success."""
        agent = self._PRE_DELIVERY_AGENT.replace("IFS= read -r PROMPT\n", "IFS= read -r PROMPT\n%(after)s")
        for name, after, live_expect, adopted_expect in (
                # live sees ONLY the refusing record (the sole error-field refusal leg,
                # `error_field_set`); adopted sees success + refusal -> R1 `refusal_in_boundary`
                ("refusal_after", _record(True), ("FAILED", "error_field_set"), ("FAILED", REFUSAL_IN_BOUNDARY)),
                ("second_completion_after", _record(False), ("COMPLETED", None), ("LOST", PROVENANCE_AMBIGUOUS))):
            with self.subTest(case=name):
                session = self._session(f"pr36-r3-l8-{name[:6]}", agent % {"records": _record(False), "after": after})
                live_result = PR36F003AdoptedSuccessSubsetTests._pty_turn(self, session, lambda sid: "Continue the task.")
                self.assertEqual(self._verdict(live_result), live_expect, live_result)
                adopted, adopted_result = self._adopted(session)
                self.assertEqual(self._verdict(adopted_result), adopted_expect, adopted_result)
                _assert_not_wider(self, session, live_result, adopted, adopted_result)

    # ---- L-9: the production recovery path settles the C2 outcome by name --------------------
    def test_l9_the_production_collect_in_flight_recovery_settles_adopted_baseline_unknown(self) -> None:
        """`StandaloneAdapter._collect_in_flight` -> `StandaloneRuntime.adopt_session` ->
        `StandaloneSession.collect()` over a `post_ready_delivery` dispatch whose live
        supervisor settled COMPLETED: `collect()` raises the LOST `adopted_baseline_unknown`,
        which the adapter turns into the TYPED FAILED settlement (`settle_failed`: the
        AGENT_SETTLED event carries `result.status == BLOCKED` with
        `standalone_failure.reason == adopted_baseline_unknown`, and the fenced
        SETTLEMENT_OBSERVED row the adapter reads back is state FAILED with that verdict
        reason) -- never a COMPLETED settlement.  The same recovery over a
        `launch_with_prompt` dispatch settles COMPLETED (unchanged)."""
        from scripts.deterministic_workflow.standalone_adapter import StandaloneAdapter
        for mode, expect in (("post_ready_delivery", ("FAILED", ADOPTED_BASELINE_UNKNOWN)),
                             ("launch_with_prompt", ("COMPLETED", None))):
            with self.subTest(delivery_mode=mode):
                run_id = f"pr36-r3-l9-{mode[:5]}"
                if mode == "post_ready_delivery":
                    live = self._session(run_id, _NOECHO_AGENT % {"records": _record(False)})
                    live_result = PR36F003AdoptedSuccessSubsetTests._pty_turn(self, live, lambda sid: "Continue the task.")
                else:
                    live = self._session(run_id, _ARGV_AGENT % {"records": _record(False)}, delivery_mode="launch_with_prompt")
                    self._argv_turn(live, "Continue the task.")
                    live_result = live.await_completion()
                self.assertEqual(live_result["state"], "COMPLETED", live_result)
                intent = dict(live.intent, command_id="c", payload_digest="0" * 64)
                runtime = rt.StandaloneRuntime(artifact_base=live.artifact_base, run_id=live.run_id,
                                               profile=live.profile,
                                               journal=journal_mod.ExecutionJournal(live.artifact_base, live.run_id))
                adapter = StandaloneAdapter(runtime, settlement_journal=runtime.journal,
                                            artifact_base=live.artifact_base, run_id=live.run_id)
                event = adapter._collect_in_flight(intent, live.fence)
                self.assertIsNotNone(event, "the production recovery settled nothing")
                rows = [r for r in runtime.journal.rows_for(live.intent_id)
                        if r.get("kind") == "SETTLEMENT_OBSERVED" and r.get("reported_by") == live.fence
                        and (r.get("source_vocabulary") or {}).get("event", {}).get("event_id") == event.get("event_id")]
                self.assertEqual(len(rows), 1, "the adapter's fenced settlement row is missing or duplicated")
                verdict = (rows[0].get("source_vocabulary") or {}).get("completion_verdict") or {}
                if expect[0] == "FAILED":
                    self.assertEqual((event.get("result") or {}).get("status"), "BLOCKED", event)
                    self.assertEqual(((event.get("result") or {}).get("standalone_failure") or {}).get("reason"),
                                     ADOPTED_BASELINE_UNKNOWN, event)
                    self.assertEqual((rows[0].get("state"), rows[0].get("outcome")), ("FAILED", "failed"), rows[0])
                    self.assertEqual(verdict.get("reason"), expect[1], verdict)
                    self.assertEqual(verdict.get("stage"), "lost", verdict)
                    withheld = [r for r in runtime.journal.rows_for(live.intent_id)
                                if (r.get("source_vocabulary") or {}).get("provenance_outcome") == ADOPTED_BASELINE_UNKNOWN]
                    self.assertTrue(withheld, "the C2 outcome was not journalled by name on the recovery path")
                else:
                    self.assertNotIn("standalone_failure", event.get("result") or {}, event)
                    self.assertEqual((rows[0].get("state"), rows[0].get("outcome")), ("COMPLETED", "succeeded"), rows[0])


def _r3_lines(*, ok: bool) -> str:
    """A `result` record WITHOUT any binding field (the sidecar carrier shape)."""
    return 'printf \'{"type":"result","is_error":%s}\\n\'\n' % ("false" if ok else "true")


if __name__ == "__main__":
    unittest.main()
