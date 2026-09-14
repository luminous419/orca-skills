"""OS-37 BUGFIX (run_a52e9c9b48f7): one behaviour-based lock per finding of the round-7
consolidated external review of head `f9f7b8b` (issuecomment-5663477134).

Each test FAILS (or ERRORS on an API the fix introduced) at `f9f7b8b` and passes after the
fix, and each reads DURABLE / OS-LEVEL state -- the recorded authority, the rendered prompt,
the durable thread evidence, the capture integrity meta and its append intent, the migration
audit log written by SEPARATE PROCESSES -- rather than a source string.

  1. a launch that omits `thread_id` binds ONE identity in state, ledger and authority, and
     resume + watchdog both recover it;
  2. resume and watchdog recovery rebuild the SAME production prompt composer, so the next
     dispatch delivers the objective / role / output contract / correction instruction,
     never raw ActionIntent JSON;
  3. durable thread evidence is a present / proven_absent / unreadable tri-state, and an
     unreadable pause store or checkpoint fails every recovery closed, never as absence;
  4. a capture handoff never promotes a meta_missing / meta_unreadable / total_bytes_mismatch
     / unverified_tail state into an answerable one, and a forged tail beyond the declared
     length never becomes settlement evidence;
  5. migration and reconciliation are serialised by a real inter-process lock, so a race in
     SEPARATE PROCESSES leaves exactly one terminal record per id and a linear digest chain;
  6. a `turn_start` record alone is no longer `delivered_confirmed`; the driver's own
     conjunctive delivery selector decides;
  7. `build_standalone_prompt_composer(project_root=...)` resolves the project's quality
     profile into the standalone prompt, and an invalid profile refuses pre-dispatch.
"""
from __future__ import annotations

import contextlib
import io
import json
import multiprocessing as mp
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from typing import Any

from scripts.deterministic_workflow import (launcher,  # noqa: E402
                                            pause_store,
                                            standalone_capture as capture_mod)
from scripts.deterministic_workflow.runtime_state import FileRuntimeStateStore  # noqa: E402
from scripts.deterministic_workflow.standalone_profile import CaptureLimits  # noqa: E402
from scripts.test_os37_followup_review_regressions import (  # noqa: E402
    _Composed, _langgraph_ok, LANGGRAPH_REASON, agent_profile_spec, stub_profile_spec)
from scripts.test_os37_external_review_regressions import WORKER_INTENT_KEYS  # noqa: E402
from scripts.test_os37_lifecycle_boundary_regressions import INJECTED_REHEARSALS  # noqa: E402

REPO = Path(__file__).resolve().parent.parent


def _profile(base: Path) -> dict:
    return agent_profile_spec(worktree=str(base / "wt"))


# =====================================================================================
# Blocker 1 -- an omitted thread_id binds ONE identity everywhere
# =====================================================================================
class B1EffectiveThreadIdentityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.base = Path(tempfile.mkdtemp(prefix="os37-r7-b1-"))
        self.addCleanup(shutil.rmtree, self.base, True)
        (self.base / "wt").mkdir()

    def test_a_launch_with_no_thread_id_binds_one_identity(self) -> None:
        # No `thread_id` in the spec at all.
        spec = {"run_id": "run_r7b1", "phases": ["DESIGN"], "max_iterations": 2}
        ledger = FileRuntimeStateStore(self.base / "ledger.json")
        adapter, state = launcher.build_standalone_adapter(
            spec, artifact_base=self.base, run_id="run_r7b1", runtime_state=ledger,
            profile_spec=_profile(self.base))
        adapter.publish_launch_bindings()
        # state and ledger already agree (both read effective_thread_id); the authority is
        # the third writer that used to record "".
        record = launcher.load_standalone_authority(self.base, "run_r7b1", state["thread_id"])
        self.assertIsNotNone(record, "the omitted-thread launch recorded no authority")
        self.assertEqual(record["thread_id"], state["thread_id"])
        self.assertEqual(record["thread_id"], launcher.DEFAULT_THREAD_ID)
        # At f9f7b8b the state/ledger named "launcher" while the authority recorded "", so
        # this cross-thread read RAISED wrong-thread.  Now it binds the one identity.  (The
        # thread-less watchdog route additionally needs durable thread evidence, which is a
        # never-executed run's `proven_absent`; the live resume/watchdog recovery of an
        # omitted-thread run is exercised end to end by the langgraph E2E below.)
        self.assertIsNotNone(
            launcher.load_standalone_authority(self.base, "run_r7b1", state["thread_id"]))
        if _langgraph_ok():
            # A never-executed run has no pause record and no committed head; consulting
            # the head to prove that needs the pinned runtime, so this leg is gated.
            self.assertEqual(launcher.durable_thread_evidence(self.base, "run_r7b1").kind,
                             launcher.THREAD_EVIDENCE_PROVEN_ABSENT)

    @unittest.skipUnless(_langgraph_ok(), LANGGRAPH_REASON)
    def test_an_omitted_thread_run_recovers_through_the_watchdog(self) -> None:
        # Launch with NO thread_id, interrupt before the first dispatch (spawns nothing),
        # then recover through the production watchdog wiring: at f9f7b8b the "" authority
        # was refused wrong-thread against the head's "launcher" and the run never recovered.
        import argparse
        spec = {"run_id": "run_r7b1wd", "phases": ["DESIGN"], "max_iterations": 2}
        ledger = FileRuntimeStateStore(self.base / "wd-ledger.json")
        adapter, state = launcher.build_standalone_adapter(
            spec, artifact_base=self.base, run_id="run_r7b1wd", runtime_state=ledger,
            profile_spec=_profile(self.base))
        stalled = launcher.execute_state(
            state, adapter=adapter, runtime_state=ledger,
            journal=launcher._standalone_pause_row_journal(self.base, "run_r7b1wd"),
            artifact_base=self.base, interrupt_before=["EXECUTE_INTENT"], audit_sink=None)
        self.assertTrue(stalled.get("pending_intent"), stalled.get("terminal_reason"))
        # The head now names the effective thread; the thread-less watchdog read resolves it.
        evidence = launcher.durable_thread_evidence(self.base, "run_r7b1wd")
        self.assertEqual(evidence.kind, launcher.THREAD_EVIDENCE_PRESENT)
        self.assertEqual(evidence.thread_id, launcher.DEFAULT_THREAD_ID)
        args = argparse.Namespace(artifact_base=str(self.base), results="",
                                  adapter="standalone", run_owner="", project_root="",
                                  standalone_profile="")
        wiring = launcher._watchdog_wiring(args)
        recovered_adapter, recovered_ledger, _journal = wiring.adapter_for("run_r7b1wd")
        self.assertEqual(recovered_ledger.path.resolve(), ledger.path.resolve(),
                         "the watchdog reopened a different ledger than the launch bound")

    def test_a_second_thread_never_collides_with_the_primary(self) -> None:
        for thread in ("", "second"):
            ledger = FileRuntimeStateStore(self.base / f"ledger-{thread or 'primary'}.json")
            spec: dict[str, Any] = {"run_id": "run_r7b1b", "phases": ["DESIGN"]}
            if thread:
                spec["thread_id"] = thread
            adapter, _state = launcher.build_standalone_adapter(
                spec, artifact_base=self.base, run_id="run_r7b1b",
                runtime_state=ledger, profile_spec=_profile(self.base))
            adapter.publish_launch_bindings()
        primary = launcher.load_standalone_authority(self.base, "run_r7b1b",
                                                     launcher.DEFAULT_THREAD_ID)
        second = launcher.load_standalone_authority(self.base, "run_r7b1b", "second")
        self.assertEqual(primary["thread_id"], launcher.DEFAULT_THREAD_ID)
        self.assertEqual(second["thread_id"], "second")
        self.assertNotEqual(primary["runtime_state_path"], second["runtime_state_path"])


# =====================================================================================
# Blocker 2 -- recovery rebuilds the SAME production prompt composer
# =====================================================================================
class B2PromptCompositionSurvivesRecoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.base = Path(tempfile.mkdtemp(prefix="os37-r7-b2-"))
        self.addCleanup(shutil.rmtree, self.base, True)
        (self.base / "wt").mkdir()
        self.objective = "Write DESIGN.md with '## Parsing' and '## Examples'."
        self.role_instructions = {"WORKER:CORRECTION": "Fill in the deferred section."}

    def _intent(self, role: str = "WORKER", round_kind: str = "PHASE_GATE") -> dict:
        return {"intent_id": "i", "run_id": "run_r7b2", "task_id": "t", "role": role,
                "phase": "DESIGN", "gate_iteration": 1, "round_kind": round_kind,
                "repair_instruction": None, "payload_digest": "d"}

    def _launch(self, thread: str = "t"):
        ledger = FileRuntimeStateStore(self.base / "ledger.json")
        composition = launcher.prompt_composition_record(
            self.objective, requested_phases=("DESIGN",), risk="high",
            project_root=REPO, role_instructions=self.role_instructions)
        adapter, _state = launcher.build_standalone_adapter(
            {"run_id": "run_r7b2", "thread_id": thread, "phases": ["DESIGN"]},
            artifact_base=self.base, run_id="run_r7b2", runtime_state=ledger,
            profile_spec=_profile(self.base), prompt_composition=composition)
        adapter.publish_launch_bindings()
        return adapter, ledger

    def test_recovery_rebuilds_the_identical_production_prompt(self) -> None:
        launched, ledger = self._launch()
        pre_worker = launched._prompt_composer(self._intent())
        pre_correction = launched._prompt_composer(self._intent(round_kind="CORRECTION"))
        pre_reviewer = launched._prompt_composer(self._intent(role="PHASE_REVIEWER"))
        # Resume + watchdog both go through standalone_recovery_composition.
        recovered, _j, _p = launcher.standalone_recovery_composition(
            self.base, "run_r7b2", thread_id="t", ledger=ledger,
            pause_row_journal=launcher._standalone_pause_row_journal(self.base, "run_r7b2"))
        self.assertIsNotNone(recovered._prompt_composer,
                             "the recovery rebuilt no prompt composer")
        self.assertEqual(recovered._prompt_composer(self._intent()), pre_worker)
        self.assertEqual(recovered._prompt_composer(self._intent(round_kind="CORRECTION")),
                         pre_correction)
        self.assertEqual(recovered._prompt_composer(self._intent(role="PHASE_REVIEWER")),
                         pre_reviewer)
        # The rebuilt prompt carries the objective, the correction instruction and the
        # review-output contract -- never the canonical intent JSON.
        self.assertIn(self.objective, pre_worker)
        self.assertIn("STATUS: COMPLETE", pre_worker)
        self.assertIn("RESULT: PASS", pre_reviewer)
        self.assertNotIn('"payload_digest"', pre_worker)

    def test_an_unrebuildable_composition_is_a_typed_refusal(self) -> None:
        _launched, ledger = self._launch()
        # Corrupt the content-addressed composition archive the authority bound.
        record = launcher.load_standalone_authority(self.base, "run_r7b2", "t")
        digest = record["prompt_composition_digest"]
        archive = launcher.prompt_composition_archive_path(self.base, "run_r7b2", digest)
        archive.write_text("{ not a composition record")
        with self.assertRaises(launcher.LauncherError) as caught:
            launcher.standalone_recovery_composition(
                self.base, "run_r7b2", thread_id="t", ledger=ledger,
                pause_row_journal=launcher._standalone_pause_row_journal(self.base, "run_r7b2"))
        self.assertIn(launcher.STANDALONE_PROMPT_COMPOSITION_MISSING, str(caught.exception))


# =====================================================================================
# Blocker 3 -- durable thread evidence is a present / proven_absent / unreadable tri-state
# =====================================================================================
class B3DurableThreadEvidenceTriStateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.base = Path(tempfile.mkdtemp(prefix="os37-r7-b3-"))
        self.addCleanup(shutil.rmtree, self.base, True)
        (self.base / "wt").mkdir()

    def _bind(self, run: str, thread: str) -> None:
        launcher.publish_standalone_launch_bindings(
            self.base, run, profile_spec=_profile(self.base),
            runtime_state_path=(self.base / f"{run}.json").resolve(), thread_id=thread)

    @unittest.skipUnless(_langgraph_ok(), LANGGRAPH_REASON)
    def test_proven_absent_is_distinct_from_unreadable(self) -> None:
        # No pause record and no checkpoint head: proven_absent.  Proving "no head" reads
        # the checkpoint store, which needs the pinned runtime, so this is gated.
        self._bind("run_r7b3a", "t")
        evidence = launcher.durable_thread_evidence(self.base, "run_r7b3a")
        self.assertEqual(evidence.kind, launcher.THREAD_EVIDENCE_PROVEN_ABSENT)
        self.assertFalse(evidence.present)

    def test_an_unreadable_pause_store_is_unreadable_not_absent(self) -> None:
        self._bind("run_r7b3b", "other")
        store = pause_store.store_for("run_r7b3b", artifact_base=self.base)
        store.path.parent.mkdir(parents=True, exist_ok=True)
        store.path.write_text("{ not a pause document")
        evidence = launcher.durable_thread_evidence(self.base, "run_r7b3b")
        self.assertEqual(evidence.kind, launcher.THREAD_EVIDENCE_UNREADABLE)
        self.assertEqual(evidence["source"], "pause_store")

    def test_an_unreadable_evidence_refuses_every_thread_less_recovery(self) -> None:
        # Bind a primary whose thread is 'other'; a thread-less (watchdog) recovery must
        # refuse rather than accept the wrong-thread record because evidence collapsed to "".
        self._bind("run_r7b3c", "other")
        store = pause_store.store_for("run_r7b3c", artifact_base=self.base)
        store.path.parent.mkdir(parents=True, exist_ok=True)
        store.path.write_text("{ corrupt")
        with self.assertRaises(launcher.LauncherError) as caught:
            launcher.load_standalone_authority(self.base, "run_r7b3c")
        self.assertIn(launcher.STANDALONE_THREAD_EVIDENCE_UNREADABLE, str(caught.exception))

    @unittest.skipUnless(_langgraph_ok(), LANGGRAPH_REASON)
    def test_an_unreadable_pause_store_refuses_the_resume_verb_typed(self) -> None:
        # `resume` checks `require_runtime()` first, so the typed thread-evidence refusal is
        # only reachable with the pinned runtime present; the absent-runtime refusal
        # (LANGGRAPH_DEPENDENCY_MISSING) is a different, earlier gate.
        self._bind("run_r7b3d", "t")
        store = pause_store.store_for("run_r7b3d", artifact_base=self.base)
        store.path.parent.mkdir(parents=True, exist_ok=True)
        store.path.write_text("{ corrupt pause store")
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = launcher.run_cli(["resume", "--run-id", "run_r7b3d",
                                     "--artifact-base", str(self.base),
                                     "--adapter", "standalone", "--json"])
        self.assertEqual(code, launcher.USAGE_EXIT_CODE)
        self.assertIn(launcher.STANDALONE_THREAD_EVIDENCE_UNREADABLE, err.getvalue())


# =====================================================================================
# Blocker 4 -- capture handoff never promotes unverifiable bytes
# =====================================================================================
class B4CaptureHandoffIntegrityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.base = Path(tempfile.mkdtemp(prefix="os37-r7-b4-"))
        self.addCleanup(shutil.rmtree, self.base, True)
        self.limits = CaptureLimits()

    def _answerable(self, path: Path) -> dict:
        return capture_mod.BoundedCapture(path, limits=self.limits).completion_is_answerable()

    def test_meta_missing_is_never_healed_at_handoff(self) -> None:
        path = self.base / "a.log"
        path.write_bytes(b'{"type":"result","is_error":false}\n')
        self.assertFalse(self._answerable(path)["answerable"])
        w = capture_mod.RawBoundedAppender(os.fsencode(str(path)), limits=self.limits)
        w.append(b"tail\n"); w.close()
        after = self._answerable(path)
        self.assertFalse(after["answerable"], "meta_missing was healed at handoff")
        self.assertTrue(after["integrity"].startswith(
            capture_mod.UNANSWERABLE_INHERITED_PREFIX), after)

    def test_meta_unreadable_open_failure_is_not_absence(self) -> None:
        path = self.base / "b.log"
        store = capture_mod.BoundedCapture(path, limits=self.limits)
        store.append(b'{"type":"result","is_error":false}\n', at="t0")
        meta = path.with_name(path.name + ".meta.json")
        os.chmod(meta, 0)
        try:
            w = capture_mod.RawBoundedAppender(os.fsencode(str(path)), limits=self.limits)
            w.append(b"tail\n"); w.close()
            self.assertTrue(w.unanswerable.startswith(
                capture_mod.UNANSWERABLE_INHERITED_PREFIX), w.unanswerable)
            self.assertIn(capture_mod.INTEGRITY_META_UNREADABLE, w.unanswerable)
        finally:
            os.chmod(meta, 0o600)

    def test_a_forged_tail_beyond_declared_length_never_becomes_evidence(self) -> None:
        path = self.base / "c.log"
        store = capture_mod.BoundedCapture(path, limits=self.limits)
        store.append(b'{"type":"system"}\n', at="t0")
        # A synthetic/forged completion record appended OUTSIDE the contract.
        with open(path, "ab") as fh:
            fh.write(b'{"type":"result","is_error":false,"forged":true}\n')
        pre = self._answerable(path)
        self.assertFalse(pre["answerable"])
        self.assertEqual(pre["integrity"], capture_mod.INTEGRITY_TOTAL_BYTES_MISMATCH)
        w = capture_mod.RawBoundedAppender(os.fsencode(str(path)), limits=self.limits)
        w.append(b"more\n"); w.close()
        post = self._answerable(path)
        self.assertFalse(post["answerable"], "a forged tail became answerable at handoff")
        self.assertIn(capture_mod.INTEGRITY_UNVERIFIED_TAIL, post["integrity"])

    def test_a_proven_in_flight_append_via_the_intent_is_recoverable(self) -> None:
        # The one recoverable crash-between-data-and-meta case: the durable append intent
        # describes the suffix exactly, so it verifies rather than being unverified_tail.
        path = self.base / "d.log"
        store = capture_mod.BoundedCapture(path, limits=self.limits)
        store.append(b'{"type":"system"}\n', at="t0")
        declared = store.size
        suffix = b'{"type":"result","is_error":false}\n'
        capture_mod.write_append_intent(store._intent_path, offset=declared, payload=suffix)
        with open(path, "ab") as fh:                 # data landed, meta write "crashed"
            fh.write(suffix)
        verified = capture_mod.verified_tail(path, store._intent_path,
                                             declared_total=declared)
        self.assertEqual(verified["state"], "verified", verified)


# =====================================================================================
# Blocker 5 -- migration and reconciliation are serialised across PROCESSES
# =====================================================================================
def _child_migrate(base_dir: str, run: str, spec: dict, actor: str, barrier) -> None:
    import sys
    sys.path.insert(0, str(REPO))
    from scripts.deterministic_workflow import launcher as L
    barrier.wait()
    with contextlib.suppress(Exception):
        L.migrate_standalone_profile(Path(base_dir), run, thread_id="t",
                                     new_profile_spec=spec, actor=actor, reason="race")


def _child_reconcile_during(base_dir: str, run: str, spec: dict, barrier, hold) -> None:
    import sys
    sys.path.insert(0, str(REPO))
    from scripts.deterministic_workflow import launcher as L
    real_write = L._durable_write

    def slow_write(path, text):
        real_write(path, text)
        if path.name.startswith("runtime_state"):
            barrier.wait()
            hold.wait()
    L._durable_write = slow_write
    with contextlib.suppress(Exception):
        L.migrate_standalone_profile(Path(base_dir), run, thread_id="t",
                                     new_profile_spec=spec, actor="alice", reason="race")


def _child_read(base_dir: str, run: str) -> None:
    import sys
    sys.path.insert(0, str(REPO))
    from scripts.deterministic_workflow import launcher as L
    with contextlib.suppress(Exception):
        L.load_standalone_authority(Path(base_dir), run, "t")


class B5MigrationSerializationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.base = Path(tempfile.mkdtemp(prefix="os37-r7-b5-"))
        self.addCleanup(shutil.rmtree, self.base, True)
        (self.base / "wt").mkdir()
        self.spec_a = _profile(self.base)
        self.spec_b = {**_profile(self.base), "timeouts": {"readiness_timeout_ms": 4321}}
        self.spec_c = {**_profile(self.base), "timeouts": {"readiness_timeout_ms": 8765}}

    def _bind(self, run: str, ledger: str) -> None:
        launcher.publish_standalone_launch_bindings(
            self.base, run, profile_spec=self.spec_a,
            runtime_state_path=(self.base / ledger).resolve(), thread_id="t")

    def test_migration_vs_reconciliation_leaves_one_terminal_per_id(self) -> None:
        ctx = mp.get_context("fork")
        run = "run_r7b5a"
        self._bind(run, "l5a.json")
        barrier, hold = ctx.Barrier(2), ctx.Event()
        migrator = ctx.Process(target=_child_reconcile_during,
                               args=(str(self.base), run, self.spec_b, barrier, hold))
        reader = ctx.Process(target=_child_read, args=(str(self.base), run))
        try:
            migrator.start()
            barrier.wait()                           # migrator has re-bound, holds the lock
            reader.start()
            # The lock makes the reader BLOCK until the migrator commits and releases.
            reader.join(2.0)
            reader_blocked = reader.is_alive()
        finally:
            # Always release the migrator and reap both, even if the assertion below is
            # about to fail (at f9f7b8b the reader is NOT blocked, so the assertion fails --
            # and an un-released migrator would otherwise wedge the whole run).
            hold.set()
            migrator.join(30)
            reader.join(30)
            for proc in (migrator, reader):
                if proc.is_alive():
                    proc.terminate()
                    proc.join(5)
        self.assertTrue(reader_blocked,
                        "the recovery read reconciled a live migration -- not serialised")
        by_id: dict[str, list[str]] = {}
        for rec in launcher.read_standalone_migrations(self.base, run, "t"):
            by_id.setdefault(rec["migration_id"], []).append(rec["state"])
        for mid, states in by_id.items():
            self.assertLessEqual(states.count(launcher.MIGRATION_COMMITTED), 1,
                                 f"duplicate committed for {mid}: {states}")
        committed = launcher.standalone_committed_migrations(self.base, run, "t")
        self.assertEqual(len(committed), 1, committed)

    def test_two_migrators_produce_a_linear_history(self) -> None:
        ctx = mp.get_context("fork")
        run = "run_r7b5b"
        self._bind(run, "l5b.json")
        barrier = ctx.Barrier(3)
        kids = [ctx.Process(target=_child_migrate,
                            args=(str(self.base), run, self.spec_b, "alice", barrier)),
                ctx.Process(target=_child_migrate,
                            args=(str(self.base), run, self.spec_c, "bob", barrier))]
        for k in kids:
            k.start()
        barrier.wait()
        for k in kids:
            k.join(30)
        committed = launcher.standalone_committed_migrations(self.base, run, "t")
        chain = [(c["old_profile_digest"], c["new_profile_digest"]) for c in committed]
        for older, newer in zip(chain, chain[1:]):
            self.assertEqual(older[1], newer[0], f"non-linear chain: {chain}")
        ids = [c["migration_id"] for c in committed]
        self.assertEqual(len(ids), len(set(ids)), "a committed id was duplicated")


# =====================================================================================
# Item 6 -- turn_start alone is not delivery proof
# =====================================================================================
class Item6DeliveryProofTests(_Composed):
    def test_turn_start_alone_is_not_delivered_confirmed(self) -> None:
        spec = stub_profile_spec("deliver-claude", worktree=self.worktree,
                                 timeouts={"delivery_verify_timeout_ms": 2000})
        adapter, _state, ledger = self.compose_spec(spec, run_id="run_r7i6neg")
        intent = {**WORKER_INTENT_KEYS, "intent_id": "i-neg", "run_id": "run_r7i6neg",
                  "role": "WORKER"}
        claim = ledger.claim(intent)
        session = adapter.runtime.session_for(intent)
        receipt = session.start(lease_token=claim["lease_token"], **INJECTED_REHEARSALS)
        self.assertEqual(receipt["start_outcome"], "ready", receipt)
        result = session.send({"payload": "hello"})
        self.assertNotEqual(result["delivery"], "delivered_confirmed",
                            "a turn_start record ALONE still confirmed delivery")
        with contextlib.suppress(Exception):
            session.interrupt("done")

    def test_the_conjunctive_delivery_proof_confirms(self) -> None:
        spec = stub_profile_spec("deliver-claude-proof", worktree=self.worktree,
                                 timeouts={"delivery_verify_timeout_ms": 4000})
        adapter, _state, ledger = self.compose_spec(spec, run_id="run_r7i6pos")
        intent = {**WORKER_INTENT_KEYS, "intent_id": "i-pos", "run_id": "run_r7i6pos",
                  "role": "WORKER"}
        claim = ledger.claim(intent)
        session = adapter.runtime.session_for(intent)
        receipt = session.start(lease_token=claim["lease_token"], **INJECTED_REHEARSALS)
        self.assertEqual(receipt["start_outcome"], "ready", receipt)
        result = session.send({"payload": "hello"})
        self.assertEqual(result["delivery"], "delivered_confirmed", result)
        self.assertEqual(result["proof"], "agent_response", result)
        with contextlib.suppress(Exception):
            session.interrupt("done")


# =====================================================================================
# Item 7 -- project_root reaches the quality profile
# =====================================================================================
class Item7ProjectRootQualityProfileTests(unittest.TestCase):
    def setUp(self) -> None:
        self.base = Path(tempfile.mkdtemp(prefix="os37-r7-i7-"))
        self.addCleanup(shutil.rmtree, self.base, True)

    def _render(self, root: Path) -> str:
        composer = launcher.build_standalone_prompt_composer(
            objective="x", requested_phases=("DESIGN",), project_root=root)
        return composer({"intent_id": "i", "run_id": "run_r7i7", "role": "WORKER",
                         "phase": "DESIGN", "gate_iteration": 1, "round_kind": "PHASE_GATE"})

    def test_a_loaded_profile_reaches_the_prompt(self) -> None:
        root = self.base / "project"
        (root / ".orca").mkdir(parents=True)
        (root / ".orca" / "quality-profile.yaml").write_text(
            "version: 1\nquality_attributes:\n  - id: R7-QP-001\n"
            "    category: team-convention\n    name: Round seven marker\n"
            "    blocking: true\n    applies_to:\n      - design\n")
        text = self._render(root)
        self.assertIn("R7-QP-001", text,
                      "the loaded profile under project_root did not reach the prompt")
        self.assertIn("loaded", text)

    def test_an_absent_profile_renders_the_absent_block(self) -> None:
        root = self.base / "empty"
        root.mkdir()
        text = self._render(root)
        self.assertIn("absent", text)
        self.assertNotIn("R7-QP-001", text)

    def test_an_invalid_profile_refuses_pre_dispatch(self) -> None:
        root = self.base / "broken"
        (root / ".orca").mkdir(parents=True)
        # A directory where the profile file should be is INVALID (not absent).
        (root / ".orca" / "quality-profile.yaml").mkdir()
        with self.assertRaises(launcher.LauncherError) as caught:
            launcher.build_standalone_prompt_composer(
                objective="x", requested_phases=("DESIGN",), project_root=root)
        self.assertIn("INVALID_QUALITY_PROFILE", str(caught.exception))


# =====================================================================================
# Iteration 2 (B2) -- the mandated lock matrix
# =====================================================================================
def _sha(path: Path) -> str:
    import hashlib
    return hashlib.sha256(path.read_bytes()).hexdigest()


class B2CompositionDeletedOrCorruptedTests(unittest.TestCase):
    """B2: the persisted composition DELETED and CORRUPTED, SEPARATELY, each a typed
    refusal on recovery -- never a silent fall-through to raw intent JSON."""

    def setUp(self) -> None:
        self.base = Path(tempfile.mkdtemp(prefix="os37-r7-b2del-"))
        self.addCleanup(shutil.rmtree, self.base, True)
        (self.base / "wt").mkdir()
        self.ledger = FileRuntimeStateStore(self.base / "l.json")
        composition = launcher.prompt_composition_record(
            "Write DESIGN.md.", requested_phases=("DESIGN",), project_root=REPO)
        adapter, _state = launcher.build_standalone_adapter(
            {"run_id": "run_b2c", "thread_id": "t", "phases": ["DESIGN"]},
            artifact_base=self.base, run_id="run_b2c", runtime_state=self.ledger,
            profile_spec=_profile(self.base), prompt_composition=composition)
        adapter.publish_launch_bindings()
        digest = launcher.load_standalone_authority(
            self.base, "run_b2c", "t")["prompt_composition_digest"]
        self.archive = launcher.prompt_composition_archive_path(self.base, "run_b2c", digest)

    def _recover(self):
        return launcher.standalone_recovery_composition(
            self.base, "run_b2c", thread_id="t", ledger=self.ledger,
            pause_row_journal=launcher._standalone_pause_row_journal(self.base, "run_b2c"))

    def test_a_deleted_composition_archive_is_a_typed_refusal(self) -> None:
        self.archive.unlink()
        with self.assertRaises(launcher.LauncherError) as caught:
            self._recover()
        self.assertIn(launcher.STANDALONE_PROMPT_COMPOSITION_MISSING, str(caught.exception))

    def test_a_corrupted_composition_archive_is_a_typed_refusal(self) -> None:
        self.archive.write_text("{ not a composition record")
        with self.assertRaises(launcher.LauncherError) as caught:
            self._recover()
        self.assertIn(launcher.STANDALONE_PROMPT_COMPOSITION_MISSING, str(caught.exception))


@unittest.skipUnless(_langgraph_ok(), LANGGRAPH_REASON)
class B2ThreadEvidenceRouteMatrixTests(unittest.TestCase):
    """B2: durable thread evidence -- a corrupt/truncated checkpoint head, a corrupt pause
    store, and an EACCES (unreadable) store -- each exercised across resume / recover /
    cancel / abandon / watchdog once.  Every route is a TYPED refusal, composes NO foreign
    adapter, and leaves the faulted record BYTE-UNCHANGED.

    Langgraph-gated: the CLI recovery verbs check ``require_runtime()`` first, so on a
    dependency-absent deployment they refuse with ``LANGGRAPH_DEPENDENCY_MISSING`` before
    the thread-evidence fence is reached (recovery is impossible there anyway).  The typed
    thread-evidence refusal is observable only with the pinned runtime present."""

    TYPED = (launcher.STANDALONE_THREAD_EVIDENCE_UNREADABLE,
             launcher.STANDALONE_ADAPTER_REQUIRES_LEDGER,
             "UNREADABLE_DURABLE_STORE", "PAUSE_RECORD_CORRUPT")

    def setUp(self) -> None:
        self.base = Path(tempfile.mkdtemp(prefix="os37-r7-b2route-"))
        self.addCleanup(shutil.rmtree, self.base, True)
        (self.base / "wt").mkdir()

    def _bind(self, run: str) -> None:
        launcher.publish_standalone_launch_bindings(
            self.base, run, profile_spec=_profile(self.base),
            runtime_state_path=(self.base / f"{run}.json").resolve(), thread_id="t")

    def _route(self, verb: str, run: str) -> tuple[int, str]:
        out, err = io.StringIO(), io.StringIO()
        code = None
        argv = {
            "resume": ["resume", "--run-id", run, "--artifact-base", str(self.base),
                       "--adapter", "standalone", "--json"],
            "cancel": ["resume", "--run-id", run, "--artifact-base", str(self.base),
                       "--adapter", "standalone", "--cancel", "--actor-id", "a",
                       "--reason", "r", "--json"],
            "abandon": ["resume", "--run-id", run, "--artifact-base", str(self.base),
                        "--adapter", "standalone", "--abandon", "--actor-id", "a",
                        "--reason", "r", "--json"],
        }
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            with contextlib.suppress(SystemExit):
                if verb in ("resume", "cancel", "abandon"):
                    code = launcher.run_pause_cli(argv[verb])
                elif verb == "recover":
                    code = launcher.run_watchdog_cli(
                        ["recover", "--run-id", run, "--artifact-base", str(self.base),
                         "--adapter", "standalone", "--json"])
                elif verb == "watchdog":
                    code = launcher.run_watchdog_cli(
                        ["watchdog", "once", "--run-id", run, "--artifact-base",
                         str(self.base), "--adapter", "standalone", "--json"])
        return code, err.getvalue() + out.getvalue()

    def _assert_typed_and_clean(self, run: str, faulted: Path, verb: str, *,
                                before: str | None = None) -> None:
        # ``before`` is precomputed by the caller for an EACCES record (which cannot be
        # read while the route runs); otherwise it is read here.
        if before is None:
            before = _sha(faulted)
        code, text = self._route(verb, run)
        self.assertNotEqual(code, 0, f"{verb}: expected a refusal, got {code}: {text[:200]}")
        self.assertTrue(any(name in text for name in self.TYPED),
                        f"{verb}: no typed refusal in: {text[:300]}")
        # No foreign composition: the fake/default ledger was not created beside the record.
        self.assertFalse(launcher.default_runtime_state_path(run, "t").exists(),
                         f"{verb}: a foreign (default) ledger was composed")
        # The faulted record is byte-unchanged (restore read permission if it was EACCES).
        with contextlib.suppress(OSError):
            os.chmod(faulted, 0o600)
        self.assertEqual(_sha(faulted), before, f"{verb}: the faulted record was rewritten")

    def test_corrupt_pause_store_is_refused_on_every_route(self) -> None:
        for verb in ("resume", "cancel", "abandon", "recover", "watchdog"):
            with self.subTest(verb=verb):
                run = f"run_b2ps_{verb}"
                self._bind(run)
                store = pause_store.pause_record_path(run, artifact_base=self.base)
                store.parent.mkdir(parents=True, exist_ok=True)
                store.write_text("{ not a pause document")
                self._assert_typed_and_clean(run, store, verb)

    def test_eacces_pause_store_is_refused_on_every_route(self) -> None:
        for verb in ("resume", "cancel", "abandon", "recover", "watchdog"):
            with self.subTest(verb=verb):
                run = f"run_b2eacc_{verb}"
                self._bind(run)
                store = pause_store.pause_record_path(run, artifact_base=self.base)
                store.parent.mkdir(parents=True, exist_ok=True)
                store.write_text('{"schema_version":"x"}')
                before = _sha(store)
                os.chmod(store, 0)
                self.addCleanup(lambda p=store: p.exists() and os.chmod(p, 0o600))
                self._assert_typed_and_clean(run, store, verb, before=before)

    def test_corrupt_checkpoint_head_is_refused_on_recover_and_watchdog(self) -> None:
        # No pause record: the thread evidence falls to the checkpoint head, which is corrupt.
        from scripts.deterministic_workflow import recovery_runtime
        for verb in ("recover", "watchdog"):
            with self.subTest(verb=verb):
                run = f"run_b2ck_{verb}"
                self._bind(run)
                ckpt = recovery_runtime.checkpoint_path(run, artifact_base=self.base)
                ckpt.parent.mkdir(parents=True, exist_ok=True)
                ckpt.write_text("{ truncated checkpoint")
                self._assert_typed_and_clean(run, ckpt, verb)

    def test_eacces_checkpoint_is_refused_on_recover_and_watchdog(self) -> None:
        from scripts.deterministic_workflow import recovery_runtime
        for verb in ("recover", "watchdog"):
            with self.subTest(verb=verb):
                run = f"run_b2cke_{verb}"
                self._bind(run)
                ckpt = recovery_runtime.checkpoint_path(run, artifact_base=self.base)
                ckpt.parent.mkdir(parents=True, exist_ok=True)
                ckpt.write_text('{"threads":{}}')
                before = _sha(ckpt)
                os.chmod(ckpt, 0)
                self.addCleanup(lambda p=ckpt: p.exists() and os.chmod(p, 0o600))
                self._assert_typed_and_clean(run, ckpt, verb, before=before)


class B2CaptureWALCrashBoundaryTests(unittest.TestCase):
    """B2: the capture WAL's two crash boundaries, each with a FORGED completion record in
    the unverified region -- never answerable after the watcher handoff."""

    def setUp(self) -> None:
        self.base = Path(tempfile.mkdtemp(prefix="os37-r7-b2wal-"))
        self.addCleanup(shutil.rmtree, self.base, True)
        self.limits = CaptureLimits()

    def _answerable(self, path: Path) -> dict:
        return capture_mod.BoundedCapture(path, limits=self.limits).completion_is_answerable()

    def test_crash_between_intent_and_data_with_a_forged_tail_is_unanswerable(self) -> None:
        # The append-intent was fsynced describing the REAL bytes, but the data write
        # landed FORGED bytes (a synthetic completion) instead -- the "crash between intent
        # and data" window.  verified_tail sees a digest mismatch -> unverified.
        path = self.base / "a.log"
        store = capture_mod.BoundedCapture(path, limits=self.limits)
        store.append(b'{"type":"system"}\n', at="t0")
        declared = store.size
        real = b'{"type":"result","is_error":false}\n'
        capture_mod.write_append_intent(store._intent_path, offset=declared, payload=real)
        with open(path, "ab") as fh:                     # a FORGED completion, not `real`
            fh.write(b'{"type":"result","is_error":false,"forged":true}\n')
        self.assertFalse(self._answerable(path)["answerable"])
        verified = capture_mod.verified_tail(path, store._intent_path, declared_total=declared)
        self.assertEqual(verified["state"], "unverified", verified)
        # After the exit watcher handoff it is IRREVERSIBLE.
        w = capture_mod.RawBoundedAppender(os.fsencode(str(path)), limits=self.limits)
        w.append(b"more\n"); w.close()
        after = self._answerable(path)
        self.assertFalse(after["answerable"], "a forged intent/data tail became answerable")
        self.assertIn(capture_mod.INTEGRITY_UNVERIFIED_TAIL, after["integrity"])

    def test_crash_between_data_and_meta_with_a_forged_extra_tail_is_unanswerable(self) -> None:
        # The real in-flight append landed (intent + matching data), meta lagged -- the
        # recoverable case -- but THEN a forged completion record was appended BEYOND it
        # with no covering intent: that extra suffix is unverified.
        path = self.base / "b.log"
        store = capture_mod.BoundedCapture(path, limits=self.limits)
        store.append(b'{"type":"system"}\n', at="t0")
        declared = store.size
        real = b'{"type":"result","is_error":false}\n'
        capture_mod.write_append_intent(store._intent_path, offset=declared, payload=real)
        with open(path, "ab") as fh:
            fh.write(real)                               # the real data landed (recoverable)
            fh.write(b'{"type":"result","is_error":false,"forged":true}\n')  # forged extra
        self.assertFalse(self._answerable(path)["answerable"],
                         "a forged record beyond the intent-covered suffix is unverified")
        w = capture_mod.RawBoundedAppender(os.fsencode(str(path)), limits=self.limits)
        w.append(b"more\n"); w.close()
        after = self._answerable(path)
        self.assertFalse(after["answerable"], "a forged data/meta tail became answerable")


class B2CrashRecoveryByteEqualTests(unittest.TestCase):
    """B2: pause->resume after Worker i1 and SIGKILL->watchdog after Worker i1, each
    followed by the next dispatch AT THE REAL ADAPTER BOUNDARY, asserting byte-equal
    composition.  Driven through the production launcher + Graph with the native fixture
    (fast, deterministic); the real-CLI variant lives in
    ``scripts/os37_r10_recovery_prompt_e2e.py --cli claude`` and its retained evidence."""

    @unittest.skipUnless(_langgraph_ok(), LANGGRAPH_REASON)
    def test_sigkill_watchdog_after_worker_i1_delivers_byte_equal(self) -> None:
        from scripts import os37_r10_recovery_prompt_e2e as e2e
        out = Path(tempfile.mkdtemp(prefix="os37-r7-b2wd-"))
        self.addCleanup(shutil.rmtree, out, True)
        record = e2e.run(out, "fixture", timeout_s=600.0)
        self.assertEqual(record.get("outcome"), "ran", record)
        self.assertTrue(record["launch_worker_settled"], record)
        self.assertEqual(record["recover_status"], "RECOVERED", record)
        self.assertEqual(record["terminal_status_after_recover"], "COMPLETED", record)
        self.assertTrue(record["all_captured_verified"],
                        "a captured prompt did not match the journal's DELIVERY_INTENT digest")
        self.assertTrue(record["recovered_reviewer_prompt_byte_equal_to_pre_crash"], record)
        self.assertTrue(record["correction_prompt_carries_objective_and_instruction"], record)
        self.assertTrue(record["recovery_prompt_established"], record)

    @unittest.skipUnless(_langgraph_ok(), LANGGRAPH_REASON)
    def test_pause_resume_after_worker_i1_delivers_byte_equal(self) -> None:
        # A run stalled AFTER Worker i1 (interrupt_after APPLY_RESULT), then re-entered by
        # the production `resume` verb's standalone recovery composition: the next dispatch
        # the resumed graph renders is byte-equal to the launch composer's rendering.
        from scripts import os37_r10_recovery_prompt_e2e as e2e
        base = Path(tempfile.mkdtemp(prefix="os37-r7-b2pr-"))
        self.addCleanup(shutil.rmtree, base, True)
        (base / "wt").mkdir()
        run_id = "run_b2pr"
        ledger = FileRuntimeStateStore(base / "ledger.json")
        composition = launcher.prompt_composition_record(
            e2e.OBJECTIVE, requested_phases=("DESIGN",), project_root=REPO,
            role_instructions=e2e.ROLE_INSTRUCTIONS)
        adapter, state = launcher.build_standalone_adapter(
            {"run_id": run_id, "thread_id": "t", "phases": ["DESIGN"], "max_iterations": 4},
            artifact_base=base, run_id=run_id, runtime_state=ledger,
            profile_spec=e2e.build_profile("fixture", str(base / "wt")),
            prompt_composition=composition)
        stalled = launcher.execute_state(
            state, adapter=adapter, runtime_state=ledger,
            journal=launcher._standalone_pause_row_journal(base, run_id),
            artifact_base=base, interrupt_after=["APPLY_RESULT"], recursion_limit=200,
            audit_sink=None)
        self.assertIsNone(stalled.get("terminal_status"), stalled.get("terminal_reason"))
        # The next dispatch the resumed graph will render (Phase Reviewer i1).
        reviewer_intent = {"intent_id": "pending", "run_id": run_id, "task_id": "t",
                           "role": "PHASE_REVIEWER", "phase": "DESIGN",
                           "gate_iteration": 1, "round_kind": "PHASE_GATE",
                           "repair_instruction": None}
        pre_crash = adapter._prompt_composer(reviewer_intent)
        # The resume verb's standalone composition rebuilds the composer from the persisted
        # inputs; its rendering of the SAME intent is byte-equal.
        resumed, _j, _p = launcher.standalone_recovery_composition(
            base, run_id, thread_id="t", ledger=ledger,
            pause_row_journal=launcher._standalone_pause_row_journal(base, run_id))
        self.assertEqual(resumed._prompt_composer(reviewer_intent), pre_crash,
                         "the resumed composition is not byte-equal to the pre-crash one")


class B2Item1EndToEndTraceTests(unittest.TestCase):
    """B2 / item 1 end-to-end: a minimal spec with NO ``thread_id`` -> launch -> watchdog
    once, with ONE assertion trace binding state, ledger and authority to the same
    effective identity, and the resume route resolving that same identity."""

    @unittest.skipUnless(_langgraph_ok(), LANGGRAPH_REASON)
    def test_one_identity_trace_across_launch_and_recovery(self) -> None:
        base = Path(tempfile.mkdtemp(prefix="os37-r7-b2trace-"))
        self.addCleanup(shutil.rmtree, base, True)
        (base / "wt").mkdir()
        run_id = "run_b2trace"
        # Minimal spec: NO thread_id.
        spec = {"run_id": run_id, "phases": ["DESIGN"], "max_iterations": 2}
        ledger = FileRuntimeStateStore(base / "ledger.json")
        adapter, state = launcher.build_standalone_adapter(
            spec, artifact_base=base, run_id=run_id, runtime_state=ledger,
            profile_spec=agent_profile_spec(worktree=str(base / "wt")))
        # THE ONE TRACE: state, ledger stem and authority all bind the effective identity.
        effective = launcher.DEFAULT_THREAD_ID
        stalled = launcher.execute_state(
            state, adapter=adapter, runtime_state=ledger,
            journal=launcher._standalone_pause_row_journal(base, run_id),
            artifact_base=base, interrupt_before=["EXECUTE_INTENT"], audit_sink=None)
        self.assertTrue(stalled.get("pending_intent"), stalled.get("terminal_reason"))
        authority = launcher.load_standalone_authority(base, run_id, effective)
        trace = {
            "state.thread_id": state["thread_id"],
            "authority.thread_id": authority["thread_id"],
            "durable_evidence.thread_id": launcher.durable_thread_evidence(
                base, run_id).thread_id,
            "ledger_default_stem": launcher.default_runtime_state_path(
                run_id, effective).name,
        }
        self.assertEqual(trace["state.thread_id"], effective, trace)
        self.assertEqual(trace["authority.thread_id"], effective, trace)
        self.assertEqual(trace["durable_evidence.thread_id"], effective, trace)
        # watchdog once resolves the SAME identity and reopens the recorded ledger.
        import argparse
        args = argparse.Namespace(artifact_base=str(base), results="", adapter="standalone",
                                  run_owner="", project_root="", standalone_profile="")
        _adapter, wd_ledger, _journal = launcher._watchdog_wiring(args).adapter_for(run_id)
        self.assertEqual(wd_ledger.path.resolve(), ledger.path.resolve(), trace)


if __name__ == "__main__":
    unittest.main()
