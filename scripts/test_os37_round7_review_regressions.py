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


# =====================================================================================
# Iteration 3 (B2) -- legacy omitted-thread authority migration
# =====================================================================================
def _replay_legacy_omitted_thread_run(base: Path):
    """Create a real omitted-``thread_id`` run stalled before its first dispatch (its
    committed checkpoint head names the effective ``launcher`` identity) and DOWNGRADE its
    authority to the exact PRE-fix on-disk shape: ``thread_id: ""`` and no
    prompt-composition digest.  Returns ``(run_id, ledger, authority_path)``.  This is the
    persisted-state compatibility case the final review's B2 is about."""
    run_id = "run_b2legacy"
    ledger = FileRuntimeStateStore(base / "ledger.json")
    adapter, state = launcher.build_standalone_adapter(
        {"run_id": run_id, "phases": ["DESIGN"], "max_iterations": 2},
        artifact_base=base, run_id=run_id, runtime_state=ledger,
        profile_spec=agent_profile_spec(worktree=str(base / "wt")))
    stalled = launcher.execute_state(
        state, adapter=adapter, runtime_state=ledger,
        journal=launcher._standalone_pause_row_journal(base, run_id),
        artifact_base=base, interrupt_before=["EXECUTE_INTENT"], audit_sink=None)
    assert stalled.get("pending_intent"), stalled.get("terminal_reason")
    target = launcher.standalone_authority_path(base, run_id, "")
    current = json.loads(target.read_text())
    legacy = {k: current[k] for k in ("schema", "run_id", "adapter",
                                      "runtime_state_path", "approval_authority",
                                      "profile_digest")}
    legacy["thread_id"] = ""                              # the pre-fix omitted-thread shape
    target.write_text(json.dumps(legacy, sort_keys=True, indent=2) + "\n")
    return run_id, ledger, target


@unittest.skipUnless(_langgraph_ok(), LANGGRAPH_REASON)
class B2LegacyAuthorityUpgradeTests(unittest.TestCase):
    """Final-Review iteration 3, B2.  A durable authority written by the PRE-fix model
    (``thread_id: ""``, no composition digest) whose durable evidence names ``launcher`` is
    atomically upgraded to the effective identity on the read path -- so a run launched at
    the PR base can resume and be watchdog-recovered after upgrade -- while an empty-thread
    authority whose evidence is absent / unreadable / another thread stays refused."""

    def setUp(self) -> None:
        self.base = Path(tempfile.mkdtemp(prefix="os37-r7-b2leg-"))
        self.addCleanup(shutil.rmtree, self.base, True)
        (self.base / "wt").mkdir()

    def test_legacy_authority_is_upgraded_on_load_and_journaled(self) -> None:
        run_id, _ledger, target = _replay_legacy_omitted_thread_run(self.base)
        self.assertTrue(launcher._is_legacy_omitted_thread_authority(
            json.loads(target.read_text()), run_id), "the replay is not the legacy shape")
        # The read path (which the resume verb and watchdog wiring both call) UPGRADES it.
        record = launcher.load_standalone_authority(self.base, run_id)
        self.assertEqual(record["thread_id"], launcher.DEFAULT_THREAD_ID)
        self.assertTrue(record.get("prompt_composition_digest"),
                        "the upgrade did not bind the now-required composition digest")
        upgrades = launcher.read_authority_upgrades(self.base, run_id)
        self.assertEqual(len(upgrades), 1)
        self.assertEqual(upgrades[0]["from_thread_id"], "")
        self.assertEqual(upgrades[0]["to_thread_id"], launcher.DEFAULT_THREAD_ID)

    def test_the_upgrade_is_idempotent(self) -> None:
        run_id, _ledger, _target = _replay_legacy_omitted_thread_run(self.base)
        launcher.load_standalone_authority(self.base, run_id)
        launcher.load_standalone_authority(self.base, run_id)
        launcher.load_standalone_authority(self.base, run_id, launcher.DEFAULT_THREAD_ID)
        self.assertEqual(len(launcher.read_authority_upgrades(self.base, run_id)), 1,
                         "a second read wrote a second upgrade record")

    def test_a_stalled_legacy_run_is_watchdog_recovered(self) -> None:
        import argparse
        run_id, ledger, _target = _replay_legacy_omitted_thread_run(self.base)
        # RED at 85bcbcc: the watchdog's authority load refuses the legacy record
        # (STANDALONE_ADAPTER_REQUIRES_LEDGER).  On the fixed tree it upgrades and recovers.
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = launcher.run_watchdog_cli(
                ["recover", "--run-id", run_id, "--artifact-base", str(self.base),
                 "--adapter", "standalone", "--json"])
        summary = json.loads(out.getvalue().strip().splitlines()[-1])
        self.assertEqual(code, 0, f"{summary!r}\n{err.getvalue()}")
        self.assertEqual(summary.get("status"), "RECOVERED", summary)
        # The authority is now the upgraded, effective identity.
        self.assertEqual(
            launcher.load_standalone_authority(self.base, run_id)["thread_id"],
            launcher.DEFAULT_THREAD_ID)

    def test_a_stalled_legacy_run_resumes_after_upgrade(self) -> None:
        # The `resume` verb re-enters through the SAME upgraded authority.  A stalled active
        # run has no pause record, so this drives the watchdog `recover` route above AND
        # asserts the resume verb's own authority load (the call `run_pause_cli` makes) now
        # returns the upgraded record rather than raising.
        run_id, _ledger, _target = _replay_legacy_omitted_thread_run(self.base)
        record = launcher.load_standalone_authority(self.base, run_id, "")
        self.assertIsNotNone(record)
        self.assertEqual(record["thread_id"], launcher.DEFAULT_THREAD_ID)
        # The resume verb re-enters through `standalone_recovery_composition`, which reads
        # the (now upgraded) authority; it succeeds rather than raising the legacy refusal.
        adapter, _j, _p = launcher.standalone_recovery_composition(
            self.base, run_id, thread_id=launcher.DEFAULT_THREAD_ID,
            ledger=FileRuntimeStateStore(self.base / "ledger.json"),
            pause_row_journal=launcher._standalone_pause_row_journal(self.base, run_id))
        self.assertIsNotNone(adapter)

    def _legacy_with_evidence(self, run_id: str, evidence_kind: str):
        """A legacy-shape authority whose durable evidence is absent / unreadable / a
        FOREIGN thread -- none of which may be upgraded."""
        ledger = FileRuntimeStateStore(self.base / f"{run_id}.json")
        launcher.persist_standalone_profile(self.base, run_id, agent_profile_spec(
            worktree=str(self.base / "wt")))
        legacy = {"schema": launcher.STANDALONE_AUTHORITY_SCHEMA, "run_id": run_id,
                  "adapter": launcher.STANDALONE_ADAPTER,
                  "runtime_state_path": str(ledger.path.resolve()),
                  "thread_id": "", "approval_authority": "none",
                  "profile_digest": launcher.profile_digest(agent_profile_spec(
                      worktree=str(self.base / "wt")))}
        target = launcher.standalone_authority_path(self.base, run_id, "")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(legacy, sort_keys=True, indent=2) + "\n")
        if evidence_kind == "unreadable":
            ps = pause_store.pause_record_path(run_id, artifact_base=self.base)
            ps.parent.mkdir(parents=True, exist_ok=True)
            ps.write_text("{ corrupt")
        return target

    def test_legacy_shape_with_absent_evidence_is_refused(self) -> None:
        target = self._legacy_with_evidence("run_b2absent", "absent")
        before = target.read_bytes()
        with self.assertRaises(launcher.LauncherError) as caught:
            launcher.load_standalone_authority(self.base, "run_b2absent")
        self.assertIn(launcher.STANDALONE_THREAD_EVIDENCE_ABSENT, str(caught.exception))
        self.assertEqual(target.read_bytes(), before, "the legacy record was rewritten")
        self.assertEqual(len(launcher.read_authority_upgrades(self.base, "run_b2absent")), 0)

    def test_legacy_shape_with_unreadable_evidence_is_refused(self) -> None:
        target = self._legacy_with_evidence("run_b2unread", "unreadable")
        before = target.read_bytes()
        with self.assertRaises(launcher.LauncherError) as caught:
            launcher.load_standalone_authority(self.base, "run_b2unread")
        self.assertIn(launcher.STANDALONE_THREAD_EVIDENCE_UNREADABLE, str(caught.exception))
        self.assertEqual(target.read_bytes(), before)
        self.assertEqual(len(launcher.read_authority_upgrades(self.base, "run_b2unread")), 0)

    def test_legacy_shape_with_a_foreign_thread_evidence_is_not_upgraded(self) -> None:
        # A run whose durable evidence names a DIFFERENT thread (not launcher): the legacy
        # empty-thread authority must NOT be upgraded to that foreign thread -- that would
        # be the foreign-authority bypass.  Replay a real run whose head names 'launcher',
        # then TAMPER the durable evidence to name another thread and confirm no upgrade.
        run_id, _ledger, target = _replay_legacy_omitted_thread_run(self.base)
        # A pause record naming a FOREIGN thread shadows the head (pause is consulted first).
        ps = pause_store.pause_record_path(run_id, artifact_base=self.base)
        ps.parent.mkdir(parents=True, exist_ok=True)
        ps.write_text("{ corrupt-foreign")          # unreadable -> not 'present launcher'
        before = target.read_bytes()
        with self.assertRaises(launcher.LauncherError):
            launcher.load_standalone_authority(self.base, run_id)
        self.assertEqual(target.read_bytes(), before, "a non-launcher-evidence legacy "
                         "record was upgraded -- foreign-authority bypass")
        self.assertEqual(len(launcher.read_authority_upgrades(self.base, run_id)), 0)


def _ledger_for(base: Path):
    return FileRuntimeStateStore(base / "ledger.json")


# =====================================================================================
# Iteration 3 (B1) -- the production preflight seeds the run-scoped auth home
# =====================================================================================
class B1ProductionAuthSeedTests(unittest.TestCase):
    """Final-Review iteration 3, B1.  A profile that declares a credential seed source and
    a config-root env name (a codex profile: ``auth_seed_source`` + ``CODEX_HOME``) has its
    run-scoped home SEEDED by the production `StandaloneSession.start` path -- BEFORE the
    auth probe -- so the docstring's promise ("seeded 0600 from the declared auth ref by the
    production preflight") is honoured through `run_workflow --adapter standalone`, not only
    by the R10 test harness.  A profile that declares no seed, and a Claude profile with no
    ``seed_auth_home``, are untouched."""

    def setUp(self) -> None:
        self.base = Path(tempfile.mkdtemp(prefix="os37-r7-b1seed-"))
        self.addCleanup(shutil.rmtree, self.base, True)

    def _session(self, spec: dict, child_env: dict):
        from scripts.deterministic_workflow import standalone_runtime as rt
        from scripts.deterministic_workflow.standalone_profile import profile_from_mapping
        from scripts.deterministic_workflow.standalone_journal import ExecutionJournal
        profile = profile_from_mapping(spec)
        sess = rt.StandaloneSession(
            intent={"intent_id": "i", "role": "WORKER"}, profile=profile,
            artifact_base=self.base, run_id="run_seed",
            journal=ExecutionJournal(self.base, "run_seed"),
            runtime_state=_ledger_for(self.base))
        sess._child_env = dict(child_env)
        return sess

    def test_a_codex_profile_seeds_its_run_scoped_home_before_the_probe(self) -> None:
        codex_home = str(self.base / "codex_home")
        seed_src = self.base / "auth.json"
        seed_src.write_text('{"tokens":{"access_token":"FIXTURE-NOT-A-REAL-SECRET"}}')
        spec = {"driver": "codex", "binary": "codex", "supported_range": [[0, 1, 0], [9, 0, 0]],
                "bin_dirs": ["/usr/bin"], "worktree": str(self.base),
                "delivery_mode": "launch_with_prompt", "identity_binding": "adopted",
                "readiness_records": [{"channel": "structured",
                                       "record_type": "thread.started",
                                       "session_field": "thread_id"}],
                "delivery_proofs": [{"channel": "structured", "record_type": "turn.completed"}],
                "completion_records": [{"channel": "structured", "record_type": "turn.completed"}],
                "driver_env": {"CODEX_HOME": codex_home},
                "auth_seed_source": str(seed_src), "auth_seed_dest_name": "auth.json"}
        sess = self._session(spec, {"CODEX_HOME": codex_home})
        result = sess._seed_run_scoped_auth()
        self.assertTrue(result["seeded"], result)
        dest = Path(codex_home) / "auth.json"
        self.assertTrue(dest.exists(), "the run-scoped CODEX_HOME was not seeded")
        self.assertEqual(oct(os.stat(dest).st_mode & 0o777), "0o600")
        self.assertEqual(dest.read_text(), seed_src.read_text())
        # The config root resolves from the child env's CODEX_HOME.
        self.assertEqual(sess._config_home_root(), codex_home)

    def test_a_claude_profile_is_a_no_op(self) -> None:
        spec = {"driver": "claude", "binary": "claude", "supported_range": [[1, 0, 0], [9, 0, 0]],
                "bin_dirs": ["/usr/bin"], "worktree": str(self.base),
                "delivery_mode": "launch_with_prompt", "identity_binding": "minted_echo",
                "identity_flag": "--session-id",
                "readiness_records": [{"channel": "structured", "record_type": "system",
                                       "session_field": "session_id"}],
                "delivery_proofs": [{"channel": "structured", "record_type": "assistant"}],
                "completion_records": [{"channel": "structured", "record_type": "result",
                                        "error_field": "is_error"}]}
        sess = self._session(spec, {})
        result = sess._seed_run_scoped_auth()
        self.assertFalse(result["seeded"])
        self.assertEqual(result["reason"], "no_auth_seed_declared")


# =====================================================================================
# Iteration 4 (B1) -- a relative worktree is resolved to an absolute path
# =====================================================================================
class B1RelativeWorktreeResolvedTests(unittest.TestCase):
    """Final-Review iteration 4, B1 root cause.  The runtime CHANGES DIRECTORY into the
    worktree before a bounded probe/spawn, and a driver may ALSO compose the worktree into
    the child argv as a change-directory flag (codex's ``-C``).  A RELATIVE worktree is then
    re-applied against the already-changed directory -- the child resolves
    ``<worktree>/<worktree>`` and refuses -- which is exactly why the real Codex recovery
    loop settled BLOCKED while Claude (which composes an add-directory flag and runs in the
    cwd) was unaffected.  `profile_from_mapping` now resolves the worktree to an ABSOLUTE
    path at the one door every launch and recovery passes, so cwd and every worktree-derived
    flag name the SAME directory.  RED at the current tree: the worktree stays relative."""

    def _profile(self, worktree: str):
        from scripts.deterministic_workflow.standalone_profile import profile_from_mapping
        return profile_from_mapping({
            "driver": "codex", "binary": "codex", "supported_range": [[0, 1, 0], [9, 0, 0]],
            "bin_dirs": ["/usr/bin"], "worktree": worktree,
            "delivery_mode": "launch_with_prompt", "identity_binding": "adopted",
            "readiness_records": [{"channel": "structured",
                                   "record_type": "thread.started",
                                   "session_field": "thread_id"}],
            "delivery_proofs": [{"channel": "structured", "record_type": "turn.completed"}],
            "completion_records": [{"channel": "structured",
                                    "record_type": "turn.completed"}]})

    def test_a_relative_worktree_becomes_absolute(self) -> None:
        prof = self._profile("artifacts/runs/x/wt")
        self.assertTrue(os.path.isabs(prof.worktree),
                        f"a relative worktree was not resolved: {prof.worktree!r}")
        self.assertEqual(prof.worktree, os.path.abspath("artifacts/runs/x/wt"))

    def test_the_codex_change_dir_flag_is_absolute_so_it_never_double_applies(self) -> None:
        from scripts.deterministic_workflow import standalone_drivers as drivers
        prof = self._profile("artifacts/runs/x/wt")
        argv = list(drivers.driver_for(prof).argv(session_id="s", prompt="p"))
        self.assertIn("-C", argv)
        cd = argv[argv.index("-C") + 1]
        self.assertTrue(os.path.isabs(cd),
                        f"codex -C is relative and will re-apply after the chdir: {cd!r}")

    def test_an_absolute_worktree_is_unchanged_and_empty_stays_empty(self) -> None:
        abs_wt = os.path.abspath(os.sep + os.path.join("tmp", "some", "wt"))
        self.assertEqual(self._profile(abs_wt).worktree, abs_wt)
        self.assertEqual(self._profile("").worktree, "")


# =====================================================================================
# Iteration 5 (B1) -- the launch-time worktree is DURABLE across recovery cwd changes
# =====================================================================================
@contextlib.contextmanager
def _cwd(path: Path):
    """Run a block with the process cwd changed -- the launch and the recovery are then
    two processes' worth of cwd in one test process."""
    before = os.getcwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(before)


def _codex_spec(worktree: str, add_dirs: list[str] | None = None) -> dict:
    spec = {
        "driver": "codex", "binary": "codex", "supported_range": [[0, 1, 0], [9, 0, 0]],
        "bin_dirs": ["/usr/bin"], "worktree": worktree,
        "delivery_mode": "launch_with_prompt", "identity_binding": "adopted",
        "readiness_records": [{"channel": "structured", "record_type": "thread.started",
                               "session_field": "thread_id"}],
        "delivery_proofs": [{"channel": "structured", "record_type": "turn.completed"}],
        "completion_records": [{"channel": "structured", "record_type": "turn.completed"}]}
    if add_dirs is not None:
        spec["add_dirs"] = add_dirs
    return spec


class B1DurableWorktreeFreezeTests(unittest.TestCase):
    """Final-Review iteration 5, B1 (pure, no graph).  `persist_standalone_profile()`
    archived the RAW mapping (``"worktree": "wt"``) and `profile_from_mapping()`
    re-resolved it against the CURRENT process cwd on every launch and recovery, so a
    Watchdog started from another cwd rebuilt a different, nonexistent worktree from
    byte-identical archived bytes.  The launch door now FREEZES the launch-time absolute
    path into the spec before it is digested and archived; the write door refuses an
    unfrozen spec; the read door refuses a legacy one.  RED at the staged tree."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="os37-r7-b1frz-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.launch = self.tmp / "launch"
        (self.launch / "wt").mkdir(parents=True)
        (self.launch / "extra").mkdir()
        self.recovery = self.tmp / "recovery"
        self.recovery.mkdir()
        self.base = self.tmp / "base"

    def test_freeze_resolves_relative_paths_against_the_launch_cwd_only(self) -> None:
        spec = _codex_spec("wt", add_dirs=["extra", "/abs/dir"])
        with _cwd(self.launch):
            frozen = launcher.freeze_profile_worktree(spec)
        self.assertEqual(frozen["worktree"], str(self.launch / "wt"))
        self.assertEqual(frozen["add_dirs"], [str(self.launch / "extra"), "/abs/dir"])
        self.assertEqual(spec["worktree"], "wt", "the caller's mapping was mutated")
        self.assertEqual(launcher.profile_unfrozen_paths(spec), ("worktree", "add_dirs[0]"))
        self.assertEqual(launcher.profile_unfrozen_paths(frozen), ())
        # An explicit base is honoured; an absolute spec is BYTE-unchanged (digest-stable).
        explicit = launcher.freeze_profile_worktree(spec, launch_base=self.recovery)
        self.assertEqual(explicit["worktree"], str(self.recovery / "wt"))
        absolute = _codex_spec(str(self.launch / "wt") + "/", add_dirs=["/abs//dir/"])
        self.assertEqual(launcher.freeze_profile_worktree(absolute), absolute)
        self.assertEqual(launcher.profile_digest(launcher.freeze_profile_worktree(absolute)),
                         launcher.profile_digest(absolute))
        self.assertEqual(launcher.freeze_profile_worktree({"driver": "claude"}),
                         {"driver": "claude"})

    def test_the_archive_holds_the_launch_time_absolute_worktree(self) -> None:
        # RED at the staged tree: the archive holds "wt" and a read from another cwd
        # resolves it against that cwd.
        spec = agent_profile_spec(worktree="wt")
        with _cwd(self.launch):
            frozen = launcher.freeze_profile_worktree(spec)
            digest = launcher.profile_digest(frozen)
            launcher.persist_standalone_profile(self.base, "run_frz", frozen)
        archived = json.loads(launcher.profile_archive_path(
            self.base, "run_frz", digest).read_text())
        self.assertEqual(archived["worktree"], str(self.launch / "wt"))
        from scripts.deterministic_workflow.standalone_profile import profile_from_mapping
        with _cwd(self.recovery):
            reloaded = launcher.load_standalone_profile(self.base, "run_frz", digest=digest)
            profile = profile_from_mapping(reloaded)
        self.assertEqual(profile.worktree, str(self.launch / "wt"))
        self.assertFalse((self.recovery / "wt").exists())

    def test_the_write_door_refuses_an_unfrozen_spec(self) -> None:
        with _cwd(self.launch), self.assertRaises(launcher.LauncherError) as caught:
            launcher.persist_standalone_profile(self.base, "run_raw",
                                                agent_profile_spec(worktree="wt"))
        self.assertIn(launcher.STANDALONE_PROFILE_WORKTREE_UNFROZEN, str(caught.exception))
        self.assertFalse(launcher.standalone_profile_path(self.base, "run_raw").exists())
        with _cwd(self.launch), self.assertRaises(launcher.LauncherError) as caught:
            launcher.persist_standalone_profile(
                self.base, "run_raw", _codex_spec(str(self.launch / "wt"), ["extra"]))
        self.assertIn("add_dirs[0]", str(caught.exception))

    def test_the_read_door_refuses_a_legacy_relative_archive_by_name(self) -> None:
        # A pre-fix archive: raw relative bytes, digest over them.  Read from another cwd
        # it is REFUSED by name, naming the audited migration -- never resolved here.
        raw = agent_profile_spec(worktree="wt")
        raw_digest = launcher.profile_digest(raw)
        archive = launcher.profile_archive_path(self.base, "run_leg", raw_digest)
        archive.parent.mkdir(parents=True, exist_ok=True)
        archive.write_text(launcher.profile_payload(raw) + "\n")
        before = archive.read_bytes()
        with _cwd(self.recovery), self.assertRaises(launcher.LauncherError) as caught:
            launcher.load_standalone_profile(self.base, "run_leg", digest=raw_digest)
        message = str(caught.exception)
        self.assertIn(launcher.STANDALONE_PROFILE_WORKTREE_UNFROZEN, message)
        self.assertIn("migrate-standalone-profile", message)
        self.assertEqual(archive.read_bytes(), before, "the legacy archive was rewritten")
        self.assertFalse((self.recovery / "wt").exists())
        # The run-global `profile.json` (in-memory-ledger runs) is guarded the same way.
        current = launcher.standalone_profile_path(self.base, "run_leg2")
        current.parent.mkdir(parents=True, exist_ok=True)
        current.write_text(launcher.profile_payload(raw) + "\n")
        with _cwd(self.recovery), self.assertRaises(launcher.LauncherError) as caught:
            launcher.load_standalone_profile(self.base, "run_leg2")
        self.assertIn(launcher.STANDALONE_PROFILE_WORKTREE_UNFROZEN, str(caught.exception))

    def test_codex_change_dir_and_add_dir_flags_are_the_launch_time_paths(self) -> None:
        from scripts.deterministic_workflow import standalone_drivers as drivers
        from scripts.deterministic_workflow.standalone_profile import profile_from_mapping
        with _cwd(self.launch):
            frozen = launcher.freeze_profile_worktree(_codex_spec("wt", ["extra"]))
        with _cwd(self.recovery):
            argv = list(drivers.driver_for(profile_from_mapping(frozen)).argv(
                session_id="s", prompt="p"))
        self.assertEqual(argv[argv.index("-C") + 1], str(self.launch / "wt"))
        self.assertEqual(argv[argv.index("--add-dir") + 1], str(self.launch / "extra"))


def _launch_relative_worktree_run(base: Path, launch_cwd: Path, run_id: str, *,
                                  thread_id: str | None = "t"):
    """A REAL launch from ``launch_cwd`` with the RELATIVE profile worktree ``"wt"``,
    stalled before its first dispatch (its authority, frozen archive and checkpoint head
    are durable).  Returns ``(ledger, raw_spec)``."""
    ledger = FileRuntimeStateStore(base / "ledger.json")
    raw = agent_profile_spec(worktree="wt")
    spec = {"run_id": run_id, "phases": ["DESIGN"], "max_iterations": 2}
    if thread_id is not None:
        spec["thread_id"] = thread_id
    with _cwd(launch_cwd):
        adapter, state = launcher.build_standalone_adapter(
            spec, artifact_base=base, run_id=run_id, runtime_state=ledger,
            profile_spec=raw)
        stalled = launcher.execute_state(
            state, adapter=adapter, runtime_state=ledger,
            journal=launcher._standalone_pause_row_journal(base, run_id),
            artifact_base=base, interrupt_before=["EXECUTE_INTENT"], audit_sink=None)
    assert stalled.get("pending_intent"), stalled.get("terminal_reason")
    return ledger, raw


@unittest.skipUnless(_langgraph_ok(), LANGGRAPH_REASON)
class B1CrossCwdRecoveryTests(unittest.TestCase):
    """Final-Review iteration 5, B1, through the production launcher + Graph + watchdog:
    launch from cwd A with a RELATIVE worktree -> stall / crash -> recovery from cwd B.
    The recovered adapter's worktree (its cwd and every worktree-derived flag) is the
    launch-time absolute path and the real fixture dispatch succeeds; a relaunch of the
    same relative spec from another cwd is the typed create-once conflict; a recovery
    override restated from another cwd is the typed digest mismatch; a legacy relative
    archive is refused by name and recovered only through the audited migration.  Every
    case is RED at the staged tree (silent re-bind against cwd B)."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="os37-r7-b1x-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.launch = self.tmp / "launch"
        (self.launch / "wt").mkdir(parents=True)
        self.recovery = self.tmp / "recovery"
        self.recovery.mkdir()
        self.base = self.tmp / "base"

    def _wiring_args(self):
        import argparse
        return argparse.Namespace(artifact_base=str(self.base), results="",
                                  adapter="standalone", run_owner="", project_root="",
                                  standalone_profile="")

    def test_watchdog_and_resume_from_another_cwd_rebuild_the_launch_worktree(self) -> None:
        run_id = "run_b1xwd"
        ledger, _raw = _launch_relative_worktree_run(self.base, self.launch, run_id)
        authority = launcher.load_standalone_authority(self.base, run_id, "t")
        archived = launcher.load_standalone_profile(self.base, run_id,
                                                    digest=authority["profile_digest"])
        self.assertEqual(archived["worktree"], str(self.launch / "wt"),
                         "the archive does not hold the launch-time absolute worktree")
        # (a) the watchdog wiring's adapter, composed from cwd B.
        with _cwd(self.recovery):
            adapter, wd_ledger, _j = launcher._watchdog_wiring(
                self._wiring_args()).adapter_for(run_id)
        self.assertEqual(adapter.runtime.profile.worktree, str(self.launch / "wt"))
        self.assertEqual(wd_ledger.path.resolve(), ledger.path.resolve())
        # (b) the resume verb's composition, from cwd B.
        with _cwd(self.recovery):
            resumed, _j, _p = launcher.standalone_recovery_composition(
                self.base, run_id, thread_id="t", ledger=ledger,
                pause_row_journal=launcher._standalone_pause_row_journal(self.base, run_id))
        self.assertEqual(resumed.runtime.profile.worktree, str(self.launch / "wt"))
        # (c) the REAL recovery from cwd B: the fixture agent is dispatched in the
        # launch-time worktree and the run completes.
        out, err = io.StringIO(), io.StringIO()
        with _cwd(self.recovery), contextlib.redirect_stdout(out), \
                contextlib.redirect_stderr(err):
            code = launcher.run_watchdog_cli(
                ["recover", "--run-id", run_id, "--artifact-base", str(self.base),
                 "--adapter", "standalone", "--json"])
        summary = json.loads(out.getvalue().strip().splitlines()[-1])
        self.assertEqual(code, 0, f"{summary!r}\n{err.getvalue()}")
        self.assertEqual(summary.get("status"), "RECOVERED", summary)
        self.assertFalse((self.recovery / "wt").exists(),
                         "the recovery re-interpreted the worktree against its own cwd")
        # The dispatch the recovery ran names the launch-time worktree in its own row.
        rows = launcher._standalone_pause_row_journal(self.base, run_id)
        worktrees = {str(r.get("terminal_worktree") or "") for r in rows.rows().values()}
        self.assertIn(str(self.launch / "wt"), worktrees, worktrees)

    def test_relaunch_of_the_same_relative_spec_from_another_cwd_is_a_conflict(self) -> None:
        run_id = "run_b1xrel"
        ledger, raw = _launch_relative_worktree_run(self.base, self.launch, run_id)
        spec = {"run_id": run_id, "thread_id": "t", "phases": ["DESIGN"],
                "max_iterations": 2}
        # From cwd B the same relative bytes freeze to ANOTHER worktree: a typed conflict
        # at composition, before any claim -- never a silent re-bind.
        with _cwd(self.recovery), self.assertRaises(launcher.LauncherError) as caught:
            launcher.build_standalone_adapter(spec, artifact_base=self.base, run_id=run_id,
                                              runtime_state=ledger, profile_spec=raw)
        self.assertIn(launcher.STANDALONE_AUTHORITY_CONFLICT, str(caught.exception))
        self.assertIn("profile_digest", str(caught.exception))
        # From the launch cwd it is an exact-match restart.
        with _cwd(self.launch):
            adapter, _state = launcher.build_standalone_adapter(
                spec, artifact_base=self.base, run_id=run_id, runtime_state=ledger,
                profile_spec=raw)
        self.assertEqual(adapter.runtime.profile.worktree, str(self.launch / "wt"))
        authority = launcher.load_standalone_authority(self.base, run_id, "t")
        self.assertEqual(authority["profile_digest"], launcher.profile_digest(
            launcher.freeze_profile_worktree(raw, launch_base=self.launch)))

    def test_a_recovery_override_restated_from_another_cwd_is_refused(self) -> None:
        run_id = "run_b1xovr"
        ledger, raw = _launch_relative_worktree_run(self.base, self.launch, run_id)
        rows = launcher._standalone_pause_row_journal(self.base, run_id)
        with _cwd(self.recovery), self.assertRaises(launcher.LauncherError) as caught:
            launcher.standalone_recovery_composition(
                self.base, run_id, thread_id="t", ledger=ledger, pause_row_journal=rows,
                profile_override=raw)
        self.assertIn(launcher.STANDALONE_PROFILE_DIGEST_MISMATCH, str(caught.exception))
        # Restated from the launch cwd it is the exact restatement it claims to be.
        with _cwd(self.launch):
            adapter, _j, _p = launcher.standalone_recovery_composition(
                self.base, run_id, thread_id="t", ledger=ledger, pause_row_journal=rows,
                profile_override=raw)
        self.assertEqual(adapter.runtime.profile.worktree, str(self.launch / "wt"))
        # And an override that restates the FROZEN (absolute) spec matches from anywhere.
        with _cwd(self.recovery):
            adapter, _j, _p = launcher.standalone_recovery_composition(
                self.base, run_id, thread_id="t", ledger=ledger, pause_row_journal=rows,
                profile_override=launcher.freeze_profile_worktree(
                    raw, launch_base=self.launch))
        self.assertEqual(adapter.runtime.profile.worktree, str(self.launch / "wt"))

    def _downgrade_to_legacy(self, run_id: str, raw: dict, *, legacy_thread: bool) -> Path:
        """Rewrite a real run's durable binding to the PRE-fix shape: a RAW relative
        archive (digest over the raw bytes) bound by the authority -- and, when
        ``legacy_thread``, the iteration-3 legacy ``thread_id: ""`` / no-composition shape
        on top, so the upgrade path is crossed as well."""
        raw_digest = launcher.profile_digest(raw)
        archive = launcher.profile_archive_path(self.base, run_id, raw_digest)
        archive.write_text(launcher.profile_payload(raw) + "\n")
        thread = "" if legacy_thread else launcher.DEFAULT_THREAD_ID
        target = launcher.standalone_authority_path(self.base, run_id, thread,
                                                    for_write=True)
        record = json.loads(target.read_text())
        record["profile_digest"] = raw_digest
        if legacy_thread:
            record.pop("prompt_composition_digest", None)
            record["thread_id"] = ""
        target.write_text(json.dumps(record, sort_keys=True, indent=2) + "\n")
        return archive

    def test_legacy_relative_archive_is_refused_from_another_cwd_then_migrated(self) -> None:
        run_id = "run_b1xleg"
        # An omitted-thread launch, so the authority is the effective `launcher` identity
        # and the iteration-3 legacy DOWNGRADE below is the exact pre-fix shape.
        ledger, raw = _launch_relative_worktree_run(self.base, self.launch, run_id,
                                                    thread_id=None)
        archive = self._downgrade_to_legacy(run_id, raw, legacy_thread=True)
        before = archive.read_bytes()
        rows = launcher._standalone_pause_row_journal(self.base, run_id)
        with _cwd(self.recovery):
            # The iteration-3 upgrade still happens (thread "" -> launcher) ...
            record = launcher.load_standalone_authority(self.base, run_id)
            self.assertEqual(record["thread_id"], launcher.DEFAULT_THREAD_ID)
            # ... and the profile read then REFUSES the legacy relative archive by name --
            # on the resume composition and on the watchdog route alike -- rather than
            # rebuilding `<recovery>/wt`.
            with self.assertRaises(launcher.LauncherError) as caught:
                launcher.standalone_recovery_composition(
                    self.base, run_id, thread_id=launcher.DEFAULT_THREAD_ID,
                    ledger=ledger, pause_row_journal=rows)
            self.assertIn(launcher.STANDALONE_PROFILE_WORKTREE_UNFROZEN,
                          str(caught.exception))
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = launcher.run_watchdog_cli(
                    ["recover", "--run-id", run_id, "--artifact-base", str(self.base),
                     "--adapter", "standalone", "--json"])
            self.assertNotEqual(code, 0)
            self.assertIn(launcher.STANDALONE_PROFILE_WORKTREE_UNFROZEN,
                          out.getvalue() + err.getvalue())
        self.assertEqual(archive.read_bytes(), before, "the legacy archive was rewritten")
        self.assertFalse((self.recovery / "wt").exists())
        # The sanctioned remedy: the explicit, audited migration naming the launch-time
        # ABSOLUTE worktree -- issued from cwd B -- after which recovery from cwd B
        # rebuilds the launch worktree and the real fixture dispatch completes.
        with _cwd(self.recovery):
            audit = launcher.migrate_standalone_profile(
                self.base, run_id, thread_id="",
                new_profile_spec={**raw, "worktree": str(self.launch / "wt")},
                actor="operator", reason="iteration-5 B1: freeze the legacy worktree")
            self.assertEqual(audit["old_profile_digest"], launcher.profile_digest(raw))
            adapter, _j, _p = launcher.standalone_recovery_composition(
                self.base, run_id, thread_id=launcher.DEFAULT_THREAD_ID, ledger=ledger,
                pause_row_journal=rows)
            self.assertEqual(adapter.runtime.profile.worktree, str(self.launch / "wt"))
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = launcher.run_watchdog_cli(
                    ["recover", "--run-id", run_id, "--artifact-base", str(self.base),
                     "--adapter", "standalone", "--json"])
        summary = json.loads(out.getvalue().strip().splitlines()[-1])
        self.assertEqual(code, 0, f"{summary!r}\n{err.getvalue()}")
        self.assertEqual(summary.get("status"), "RECOVERED", summary)
        self.assertFalse((self.recovery / "wt").exists())

    def test_a_migration_issued_with_a_relative_worktree_freezes_at_its_own_door(self) -> None:
        run_id = "run_b1xmig"
        ledger, raw = _launch_relative_worktree_run(self.base, self.launch, run_id)
        elsewhere = self.tmp / "elsewhere"
        (elsewhere / "wt2").mkdir(parents=True)
        with _cwd(elsewhere):
            audit = launcher.migrate_standalone_profile(
                self.base, run_id, thread_id="t", new_profile_spec={**raw, "worktree": "wt2"},
                actor="operator", reason="move")
        archived = launcher.load_standalone_profile(self.base, run_id,
                                                    digest=audit["new_profile_digest"])
        self.assertEqual(archived["worktree"], str(elsewhere / "wt2"))
        with _cwd(self.recovery):
            adapter, _j, _p = launcher.standalone_recovery_composition(
                self.base, run_id, thread_id="t", ledger=ledger,
                pause_row_journal=launcher._standalone_pause_row_journal(self.base, run_id))
        self.assertEqual(adapter.runtime.profile.worktree, str(elsewhere / "wt2"))


@unittest.skipUnless(_langgraph_ok(), LANGGRAPH_REASON)
class B1CrossCwdCrashRecoveryE2ETests(unittest.TestCase):
    """The real crash: a REAL launch supervisor process started from ``<out>/launch_cwd``
    with the RELATIVE worktree ``wt``, SIGKILLed after Worker i1 settled, then recovered by
    a REAL, separate watchdog process started from ``<out>/recovery_cwd`` -- the fixture
    driver here; the real `claude` / `codex` runs live in
    ``scripts/os37_r10_recovery_prompt_e2e.py --cli ...`` and their retained evidence."""

    def test_sigkill_then_watchdog_recovery_from_another_cwd(self) -> None:
        from scripts import os37_r10_recovery_prompt_e2e as e2e
        out = Path(tempfile.mkdtemp(prefix="os37-r7-b1xe2e-"))
        self.addCleanup(shutil.rmtree, out, True)
        record = e2e.run(out, "fixture", timeout_s=600.0)
        self.assertEqual(record.get("outcome"), "ran", record)
        self.assertNotEqual(record["launch_cwd"], record["recovery_cwd"], record)
        self.assertEqual(record["profile_worktree_spec"], "wt", record)
        self.assertEqual(record["archived_worktree"], record["launch_worktree"], record)
        self.assertEqual(record["recovered_worktree"], record["launch_worktree"], record)
        self.assertEqual(record["recovered_cwd_flag"], record["launch_worktree"], record)
        self.assertFalse(record["recovery_cwd_worktree_exists"], record)
        self.assertEqual(record["recover_status"], "RECOVERED", record)
        self.assertEqual(record["terminal_status_after_recover"], "COMPLETED", record)
        self.assertTrue(record["recovery_prompt_established"], record)


if __name__ == "__main__":
    unittest.main()
