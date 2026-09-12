"""OS-37 V-2 / V-12.  The four ownership refusals and the ownership gate, over an INJECTED table.

Every refusal test asserts, via a syscall spy, that **NO SIGNAL WAS SENT**.  A test that
only checked the returned verdict would pass for an implementation that refused in its
report and signalled anyway, and that implementation is the one this whole module exists to
make impossible.

The process table is injected rather than real, so the recycled-pid and stale-snapshot cases
are exercised deterministically.  Those two are the dangerous ones and they are almost
impossible to provoke on a live host: a bounded wait is exactly long enough for a pid to be
reaped and reused by an unrelated process, and that is when a supervisor SIGKILLs a stranger.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from scripts import os37_native_stub as native_stub
from scripts.deterministic_workflow import standalone_identity as identity
from scripts.deterministic_workflow import standalone_interrupt as interrupt_mod
from scripts.deterministic_workflow import standalone_pty as pty_supervisor
from scripts.deterministic_workflow.standalone_profile import (CompletionSelector,
                                                            DeliveryProofSelector,
                                                            ReadinessSelector,
                                                                StandaloneProfile, Timeouts)

TTY = "ttys042"
CHILD_PID = 4242


def profile(**timeouts) -> StandaloneProfile:
    return StandaloneProfile(
        driver="claude", binary="claude", supported_range=((0, 0, 0), (99, 0, 0)),
        readiness_records=(ReadinessSelector(channel="structured", record_type="system",
                                             session_field="session_id"),),
        delivery_mode="post_ready_delivery",
        identity_binding="minted_echo", identity_flag="--session-id",
        delivery_proofs=(DeliveryProofSelector(channel="structured",
                                               record_type="assistant"),),
        completion_records=(CompletionSelector(channel="structured",
                                               record_type="result",
                                               error_field="is_error"),),
        timeouts=Timeouts(**{"graceful_force_timeout_ms": 20, "force_retry_ms": 1,
                             "physical_exit_timeout_ms": 20, "staleness_budget_ms": 1000,
                             **timeouts}))


def record(**overrides) -> dict:
    fields = dict(
        run_id="run_os37", repo_id="repo-1",
        worktree_selector=identity.stable_worktree_selector("repo-1", "/tmp/wt"),
        agent_id="agent-1", task_id="task-1", dispatch_id="dispatch-1",
        session_id="s-abc", pid=CHILD_PID, pgid=CHILD_PID, sid=CHILD_PID,
        captured_tty=TTY, pty_id="pty-1", process_incarnation="i-1",
        host_scope="local", spawn_token="t-1", started_at="2026-09-10T00:00:00Z",
        argv_digest="ad", env_digest="ed", created_by_this_runtime=True,
        resource_kind="pty_session", user_taken_over=False)
    fields.update(overrides)
    return identity.make_record(**fields)


def snapshot(rows=None, *, age_s: float = 0.0, readable: bool = True,
             tty: str = TTY) -> dict:
    if rows is None:
        rows = ({"pid": CHILD_PID, "ppid": 1, "pgid": CHILD_PID, "sid": CHILD_PID,
                 "tty": TTY, "stat": "Ss"},)
    return {"tty": tty, "captured_at": time.time() - age_s, "rows": tuple(rows),
            "readable": readable}


class SignalSpy:
    """Records every signal that WOULD have been sent, and sends none."""

    def __init__(self) -> None:
        self.killpg: list[tuple[int, int]] = []
        self.kill: list[tuple[int, int]] = []

    def send_group(self, pgid: int, sig: int) -> None:
        self.killpg.append((pgid, sig))

    def send_one(self, pid: int, sig: int) -> None:
        self.kill.append((pid, sig))

    @property
    def total(self) -> int:
        return len(self.killpg) + len(self.kill)


# =====================================================================================
class OwnershipRefusalTests(unittest.TestCase):
    """V-2 / AC-37-02: four refusals, each sending nothing."""

    def _signal(self, rec, snap, *, supervisor_pid=999):
        decision = pty_supervisor.check_ownership(
            rec, snap, staleness_budget_ms=1000, supervisor_pid=supervisor_pid)
        spy = SignalSpy()
        observed = pty_supervisor.row_for(snap, int(rec["pid"]))
        try:
            permit = identity.assert_may_act(rec, "signal", observed=observed)
        except identity.OwnershipRefused:
            # The gate refused before a permit existed.  That is the strongest possible
            # form of "no signal": the call could not be made at all.
            return decision, spy, None
        sent = pty_supervisor.signal_target(
            rec, decision, 15, permit=permit, snapshot=snap,
            killpg=spy.send_group, kill=spy.send_one)
        return decision, spy, sent

    def test_refuses_unbound_tty(self) -> None:
        """R-OWN-1: an unbound tty is ``not_owned`` and NO signal is sent."""
        rec = dict(record())
        rec["captured_tty"] = "??"
        decision = pty_supervisor.check_ownership(
            rec, snapshot(), staleness_budget_ms=1000, supervisor_pid=999)
        self.assertEqual(decision["verdict"], "refused")
        self.assertEqual(decision["refusal"], "unbound_tty")
        self.assertEqual(decision["scope"], "none")
        spy = SignalSpy()
        sent = pty_supervisor.signal_target(
            rec, decision, 15, permit=_forged_permit_is_impossible(self, rec),
            snapshot=snapshot(), killpg=spy.send_group, kill=spy.send_one)
        self.assertEqual(spy.total, 0, "a signal was sent on an unbound tty")
        self.assertEqual(sent["sent"], ())

    def test_refuses_shared_tty(self) -> None:
        """R-OWN-2: the supervisor on the same tty downgrades to ROOT scope, never killpg.

        Not a plain refusal, because there is still a legitimate action -- signal the single
        pid.  What must never happen is a group signal, which would reach this process.
        """
        snap = snapshot(rows=(
            {"pid": CHILD_PID, "ppid": 1, "pgid": CHILD_PID, "sid": CHILD_PID,
             "tty": TTY, "stat": "Ss"},
            {"pid": os.getpid(), "ppid": 1, "pgid": os.getpid(), "sid": os.getpid(),
             "tty": TTY, "stat": "S+"}))
        decision, spy, sent = self._signal(record(), snap, supervisor_pid=os.getpid())
        self.assertEqual(decision["verdict"], "owned")
        self.assertEqual(decision["refusal"], "tty_shared_with_driver")
        self.assertEqual(decision["scope"], "root")
        self.assertEqual(spy.killpg, [],
                         "killpg was used on a tty the supervisor itself is on")
        self.assertEqual(spy.kill, [(CHILD_PID, 15)])

    def test_refuses_captured_tty_mismatch(self) -> None:
        """R-OWN-3: a recycled pid -- present, on another tty -- gets NO signal."""
        snap = snapshot(rows=({"pid": CHILD_PID, "ppid": 1, "pgid": 7, "sid": 7,
                               "tty": "ttys999", "stat": "Ss"},))
        decision, spy, _sent = self._signal(record(), snap)
        self.assertEqual(decision["verdict"], "refused")
        self.assertEqual(decision["refusal"], "captured_tty_mismatch")
        self.assertEqual(spy.total, 0, "a recycled pid was signalled")

    def test_refuses_stale_snapshot(self) -> None:
        """R-OWN-4: a snapshot older than the budget is refused, never served."""
        decision = pty_supervisor.check_ownership(
            record(), snapshot(age_s=5.0), staleness_budget_ms=1000, supervisor_pid=999)
        self.assertEqual(decision["verdict"], "refused")
        self.assertEqual(decision["refusal"], "stale_snapshot")
        self.assertEqual(decision["scope"], "none")
        spy = SignalSpy()
        pty_supervisor.signal_target(
            record(), decision, 15,
            permit=identity.assert_may_act(record(), "signal",
                                           observed=pty_supervisor.row_for(snapshot(),
                                                                           CHILD_PID)),
            snapshot=snapshot(age_s=5.0), killpg=spy.send_group, kill=spy.send_one)
        self.assertEqual(spy.total, 0, "a stale snapshot was signalled from")

    def test_an_unreadable_table_raises_rather_than_reading_as_exited(self) -> None:
        """Unreadable is UNKNOWN.  It must not collapse into "the process is gone"."""
        with self.assertRaises(pty_supervisor.ProcessTableUnreadable):
            pty_supervisor.check_ownership(record(), snapshot(readable=False),
                                           staleness_budget_ms=1000, supervisor_pid=999)
        proof = pty_supervisor.exit_proven(record(), snapshot(readable=False))
        self.assertFalse(proof["proven"])
        self.assertEqual(proof["reason"], "process_table_unreadable")

    def test_kill_ordering_puts_descendant_groups_before_the_leader(self) -> None:
        """C4 / DR-1: the exec wrapper makes this a real ordering, not a no-op."""
        snap = snapshot(rows=(
            {"pid": CHILD_PID, "ppid": 1, "pgid": CHILD_PID, "sid": CHILD_PID,
             "tty": TTY, "stat": "Ss"},
            {"pid": 5000, "ppid": CHILD_PID, "pgid": 5000, "sid": CHILD_PID,
             "tty": TTY, "stat": "S"}))
        decision, spy, _sent = self._signal(record(), snap)
        self.assertEqual(decision["scope"], "group")
        self.assertEqual([pgid for pgid, _ in spy.killpg], [5000, CHILD_PID],
                         "the session leader must be signalled LAST, or it reaps its "
                         "children before they are signalled")


def _forged_permit_is_impossible(case: unittest.TestCase, rec) -> object:
    """A real permit for an unbound-tty record, obtained the only legal way.

    ``assert_may_act`` refuses the unbound tty itself, so this helper proves the gate is the
    outer defence and ``signal_target``'s decision check is the inner one -- both must hold.
    """
    try:
        return identity.assert_may_act(rec, "signal",
                                       observed={"pid": rec["pid"], "tty": rec["captured_tty"],
                                                 "pgid": rec["pgid"], "sid": rec["sid"]})
    except identity.OwnershipRefused:
        # The gate refused.  Reach in through the module-private token so the inner check
        # is still exercised: the point of this test is that BOTH layers refuse.
        return identity.Permit(
            action="signal", fence=identity.fence(rec), session_id=rec["session_id"],
            process_incarnation=rec["process_incarnation"], pid=rec["pid"],
            pgid=rec["pgid"], captured_tty=rec["captured_tty"], verified_at_seq=0,
            _token=identity._PERMIT_TOKEN)


# =====================================================================================
class PermitTests(unittest.TestCase):
    """D8.2 / AC-37-10: nothing happens before ownership is re-verified."""

    def test_a_permit_cannot_be_constructed_outside_the_gate(self) -> None:
        with self.assertRaises(identity.OwnershipRefused):
            identity.Permit(action="signal", fence="s:i", session_id="s",
                            process_incarnation="i", pid=1, pgid=1, captured_tty=TTY,
                            verified_at_seq=0)

    def test_no_module_outside_standalone_identity_constructs_a_permit(self) -> None:
        """STATIC: the token is module-private and nothing else names it."""
        import ast
        engine = Path(__file__).resolve().parent / "deterministic_workflow"
        offenders: list[str] = []
        for path in sorted(engine.glob("*.py")):
            if path.name == "standalone_identity.py":
                continue
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    func = node.func
                    name = (func.attr if isinstance(func, ast.Attribute)
                            else getattr(func, "id", ""))
                    if name == "Permit":
                        offenders.append(f"{path.name}:{node.lineno}")
                if isinstance(node, ast.Name) and node.id == "_PERMIT_TOKEN":
                    offenders.append(f"{path.name}:{node.lineno} names the private token")
        self.assertEqual(
            offenders, [],
            "a module outside standalone_identity constructs a Permit or names its private "
            "token; the gate is the only source of permission:\n" + "\n".join(offenders))

    def test_the_gated_action_set_is_exactly_the_six_the_ticket_names(self) -> None:
        self.assertEqual(
            set(identity.GATED_ACTIONS),
            {"signal", "write_input", "reuse_session", "resume_run", "release_terminal",
             "settle"})

    def test_every_gated_action_is_refused_for_a_resource_we_did_not_create(self) -> None:
        for action in identity.GATED_ACTIONS:
            for overrides, why in (
                ({"created_by_this_runtime": False}, "not created by this runtime"),
                ({"user_taken_over": True}, "user has taken it over"),
            ):
                with self.subTest(action=action, why=why):
                    rec = dict(record())
                    rec.update(overrides)
                    with self.assertRaises(identity.OwnershipRefused):
                        identity.assert_may_act(rec, action,
                                                observed=pty_supervisor.row_for(snapshot(),
                                                                                CHILD_PID))

    def test_a_non_local_host_scope_is_refused_rather_than_localised(self) -> None:
        rec = dict(record())
        rec["host_scope"] = None      # what parse_host_scope returns for anything else
        with self.assertRaises(identity.OwnershipRefused):
            identity.assert_may_act(rec, "signal",
                                    observed=pty_supervisor.row_for(snapshot(), CHILD_PID))

    def test_a_permit_for_one_record_does_not_cover_another(self) -> None:
        mine = record()
        theirs = record(session_id="s-other", process_incarnation="i-other")
        permit = identity.assert_may_act(
            mine, "signal", observed=pty_supervisor.row_for(snapshot(), CHILD_PID))
        identity.require_permit(permit, mine, "signal")
        with self.assertRaises(identity.OwnershipRefused):
            identity.require_permit(permit, theirs, "signal")
        with self.assertRaises(identity.OwnershipRefused):
            identity.require_permit(permit, mine, "release_terminal")

    def test_reuse_refused_on_mismatched_incarnation(self) -> None:
        """V-12 case 1: reuse is refused and NO INPUT IS WRITTEN.

        The dangerous case: the session is alive, the handle resolves, and the only thing
        wrong is that it belongs to a different incarnation.
        """
        result = identity.reuse_allowed(
            record(), pty_supervisor.row_for(snapshot(), CHILD_PID),
            offered_incarnation="i-different")
        self.assertEqual(result["verdict"], "not_owned")
        self.assertEqual(result["reason"], "incarnation_mismatch")

    def test_an_untrimmed_incarnation_is_unverifiable_never_exited(self) -> None:
        for bad in (" i-1", "i-1 ", "", "\ti-1"):
            with self.subTest(incarnation=bad):
                rec = dict(record())
                rec["process_incarnation"] = bad
                result = identity.verify(rec, pty_supervisor.row_for(snapshot(), CHILD_PID))
                self.assertEqual(result["verdict"], "unverifiable")
                self.assertNotEqual(result["verdict"], "exited")

    def test_spawn_token_alone_authorizes_nothing(self) -> None:
        """The token is diagnostic evidence.  It appears in no gate."""
        import inspect
        source = inspect.getsource(identity.assert_may_act)
        self.assertNotIn(
            "spawn_token", source,
            "assert_may_act reads spawn_token; it is diagnostic evidence and authorizes "
            "nothing")


# =====================================================================================
class FenceAndReleaseTests(unittest.TestCase):
    """V-12: a fence acts on nothing, and a release releases only what was requested."""

    def test_fence_performs_no_action(self) -> None:
        spy = SignalSpy()
        result = interrupt_mod.fence(record())
        self.assertEqual(result["actions_taken"], ())
        self.assertEqual(result["fence"], "s-abc:i-1")
        self.assertEqual(spy.total, 0)

    def test_fence_never_upgraded_to_stop(self) -> None:
        """There is no code path from a fence to a stop -- asserted structurally.

        ``assert_may_act`` REFUSES the ``fence`` action outright, so a caller cannot obtain
        a permit for a fence and then use it to signal.  And ``fence_only`` calls no signal
        primitive at all.
        """
        with self.assertRaises(identity.OwnershipRefused):
            identity.assert_may_act(record(), identity.FENCE_ACTION,
                                    observed=pty_supervisor.row_for(snapshot(), CHILD_PID))
        self.assertNotIn(identity.FENCE_ACTION, identity.GATED_ACTIONS)
        import inspect
        source = inspect.getsource(identity.fence_only)
        for forbidden in ("kill", "signal_target", "killpg", "unlink", "rmtree", "remove"):
            self.assertNotIn(forbidden, source,
                             f"fence_only names {forbidden!r}; a fence performs zero "
                             "process and zero filesystem actions")

    def test_release_only_what_was_requested(self) -> None:
        """V-12 case 2: the scope names ONE pty session, and nothing else."""
        scope = identity.release_scope(record())
        self.assertEqual(scope["resource_kind"], "pty_session")
        self.assertEqual(scope["session_id"], "s-abc")
        self.assertNotIn("worktree_selector", scope,
                         "a release scope must not be able to name a worktree")
        self.assertNotIn("run_id", scope,
                         "a release scope must not be able to name the whole run")

    def test_release_requires_both_axis_gates(self) -> None:
        observed = pty_supervisor.row_for(snapshot(), CHILD_PID)
        for authority, resource, expected in (
            ("not_authorized", "release", "cleanup_authority_not_authorized"),
            ("authorized", "retain", "worker_resource_not_release"),
            ("authorized", "reuse", "worker_resource_not_release"),
        ):
            with self.subTest(authority=authority, worker_resource=resource):
                result = interrupt_mod.release_terminal(
                    record(), authority=authority, worker_resource=resource,
                    observed=observed)
                self.assertFalse(result["released"])
                self.assertEqual(result["refusal"], expected)
        granted = interrupt_mod.release_terminal(
            record(), authority="authorized", worker_resource="release", observed=observed)
        self.assertTrue(granted["released"])


# =====================================================================================
class SpawnRecordTests(unittest.TestCase):
    """D3.4a: the child's exec evidence, and its three-way answer."""

    def setUp(self) -> None:
        self.base = Path(tempfile.mkdtemp())

    def test_absent_proves_no_execve_happened(self) -> None:
        probe = pty_supervisor.read_spawn_records(self.base, "run_1", "intent-1")
        self.assertEqual(probe["outcome"], "absent")
        directory = pty_supervisor.spawn_record_dir(self.base, "run_1", "intent-2")
        directory.mkdir(parents=True)
        self.assertEqual(
            pty_supervisor.read_spawn_records(self.base, "run_1", "intent-2")["outcome"],
            "absent", "a readable, empty intent directory proves absence")

    def test_present_means_the_effect_may_exist(self) -> None:
        target = pty_supervisor.spawn_record_path(self.base, "run_1", "intent-3", "i-9")
        pty_supervisor.write_spawn_record(target, {
            "session_id": "s-1", "process_incarnation": "i-9", "pid": 1, "pgid": 1,
            "sid": 1, "boot_id": "", "proc_start_ticks": 0, "argv_digest": "a",
            "env_digest": "e", "started_at": "t"})
        probe = pty_supervisor.read_spawn_records(self.base, "run_1", "intent-3")
        self.assertEqual(probe["outcome"], "present")
        self.assertEqual(probe["record"]["process_incarnation"], "i-9")

    def test_a_partial_write_is_never_observed(self) -> None:
        """``write`` -> ``fsync`` -> ``rename`` -> ``fsync(dir)``: no half record exists."""
        target = pty_supervisor.spawn_record_path(self.base, "run_1", "intent-4", "i-9")
        target.parent.mkdir(parents=True, exist_ok=True)
        (target.parent / "spawn.i-9.tmp").write_text('{"parti')
        probe = pty_supervisor.read_spawn_records(self.base, "run_1", "intent-4")
        self.assertEqual(probe["outcome"], "absent",
                         "a .tmp file must not be read as a spawn record")

    def test_an_unreadable_directory_is_unknown_not_absence(self) -> None:
        directory = pty_supervisor.spawn_record_dir(self.base, "run_1", "intent-5")
        directory.mkdir(parents=True)
        os.chmod(directory, 0o000)
        try:
            probe = pty_supervisor.read_spawn_records(self.base, "run_1", "intent-5")
            self.assertEqual(probe["outcome"], "unknown")
        finally:
            os.chmod(directory, 0o755)

    def test_start_binds_identity_before_readiness(self) -> None:
        """I-1's ordering, at the runtime level: the record exists before readiness is asked.

        Asserted through the ownership record's own requirements: it cannot be constructed
        without a pid, a pgid, a sid and a pinned tty, all of which come from the spawn.  A
        readiness question therefore cannot precede identity, because there is nothing to
        ask about.
        """
        with self.assertRaises(identity.IdentityError):
            identity.make_record(**{**dict(record()), "captured_tty": ""})
        with self.assertRaises(identity.IdentityError):
            fields = dict(record())
            fields.pop("pid")
            identity.make_record(**fields)

    def test_an_alias_worktree_selector_is_refused(self) -> None:
        for alias in ("current", "active", ""):
            with self.subTest(alias=alias):
                with self.assertRaises(identity.IdentityError):
                    identity.make_record(**{**dict(record()),
                                            "worktree_selector": alias})


# =====================================================================================
class ExitSentinelTests(unittest.TestCase):
    """D3.4: an OS-sourced exit status a STRANGER process can read, fenced by incarnation."""

    def setUp(self) -> None:
        self.base = Path(tempfile.mkdtemp())
        self.path = pty_supervisor.exit_sentinel_path(self.base, "run_1", "s-1", "i-1")
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def test_a_matching_fence_yields_the_exit_code(self) -> None:
        self.path.write_text("0\ts-1:i-1\n")
        result = pty_supervisor.read_exit_sentinel(self.path, fence="s-1:i-1")
        self.assertEqual(result, {"outcome": "exited", "code": 0})

    def test_a_foreign_fence_withholds_the_code(self) -> None:
        """A foreign exit status must not be harvested as this incarnation's."""
        self.path.write_text("0\ts-1:i-OTHER\n")
        result = pty_supervisor.read_exit_sentinel(self.path, fence="s-1:i-1")
        self.assertEqual(result["outcome"], "foreign")
        self.assertIsNone(result["code"])

    def test_an_absent_sentinel_is_absent_not_zero(self) -> None:
        result = pty_supervisor.read_exit_sentinel(self.path, fence="s-1:i-1")
        self.assertEqual(result["outcome"], "absent")
        self.assertIsNone(result["code"], "an absent exit status must never read as 0")

    def test_a_malformed_sentinel_is_unreadable(self) -> None:
        self.path.write_text("not a status\n")
        self.assertEqual(
            pty_supervisor.read_exit_sentinel(self.path, fence="s-1:i-1")["outcome"],
            "unreadable")

    def test_the_sentinel_writer_is_cli_agnostic_and_carries_the_fence(self) -> None:
        """The sentinel is written by the pty SESSION LEADER, not by a shell wrapper.

        There is no shell between the pty and the agent any more: a wrapper would make
        ``/bin/sh`` the foreground image and R-A leg 4 (§D5.3(4)) could then never be the
        executable-identity equality the DESIGN requires.  The leader writes the same
        record, from the same ``waitpid`` status the shell's ``$?`` came from, and the
        fence and the tmp+rename are unchanged.
        """
        target = self.path.parent / "written"
        pty_supervisor.write_exit_sentinel(target, code=7, fence="s-1:i-1")
        self.assertEqual(target.read_text(), "7\ts-1:i-1\n")
        self.assertEqual(pty_supervisor.read_exit_sentinel(target, fence="s-1:i-1"),
                         {"outcome": "exited", "code": 7})
        self.assertFalse((self.path.parent / "written.tmp").exists(),
                         "the tmp file must be renamed, never left behind")
        self.assertFalse(
            hasattr(pty_supervisor, "wrapper_argv"),
            "a shell wrapper would put /bin/sh in the pty foreground and make R-A leg 4 "
            "unsatisfiable; it must not come back")

    def test_a_signalled_agent_is_reported_as_128_plus_the_signal(self) -> None:
        """The shell's ``$?`` convention, preserved across the mechanism change."""
        self.assertEqual(pty_supervisor._wait_status_to_code(0), 0)
        self.assertEqual(pty_supervisor._wait_status_to_code(3 << 8), 3)
        self.assertEqual(pty_supervisor._wait_status_to_code(9), 128 + 9)


# =====================================================================================
class InterruptLadderTests(unittest.TestCase):
    """D8: gates between rungs, and an unproven exit is never a termination."""

    def test_the_ladder_refuses_before_any_signal_when_ownership_fails(self) -> None:
        spy = SignalSpy()
        mismatched = snapshot(rows=({"pid": CHILD_PID, "ppid": 1, "pgid": 7, "sid": 7,
                                     "tty": "ttys999", "stat": "Ss"},))
        result = interrupt_mod.interrupt(
            "intent-1", "stop", record=record(), profile=profile(),
            table_reader=lambda tty: mismatched, supervisor_pid=999,
            killpg=spy.send_group, kill=spy.send_one, sleep=lambda s: None)
        self.assertEqual(result["interrupt_outcome"], "not_owned")
        self.assertEqual(spy.total, 0, "the ladder signalled a process it did not own")

    def test_a_natural_exit_at_rung_two_is_interrupted_confirmed(self) -> None:
        calls = {"n": 0}

        def reader(tty):
            calls["n"] += 1
            if calls["n"] <= 1:
                return snapshot()
            return snapshot(rows=())       # the child is gone

        spy = SignalSpy()
        result = interrupt_mod.interrupt(
            "intent-1", "stop", record=record(), profile=profile(),
            table_reader=reader, supervisor_pid=999, killpg=spy.send_group,
            kill=spy.send_one, sleep=lambda s: None)
        self.assertEqual(result["interrupt_outcome"], "interrupted_confirmed")
        self.assertEqual([sig for _pgid, sig in spy.killpg], [15],
                         "SIGKILL was sent although the child had already exited")

    def test_ownership_changing_during_the_wait_cancels_the_escalation(self) -> None:
        """The recycled-pid case: the escalation is CANCELLED, never retried."""
        calls = {"n": 0}
        recycled = snapshot(rows=({"pid": CHILD_PID, "ppid": 1, "pgid": 99, "sid": 99,
                                   "tty": TTY, "stat": "Ss"},))

        def reader(tty):
            calls["n"] += 1
            return snapshot() if calls["n"] <= 1 else recycled

        spy = SignalSpy()
        result = interrupt_mod.interrupt(
            "intent-1", "stop", record=record(), profile=profile(),
            table_reader=reader, supervisor_pid=999, killpg=spy.send_group,
            kill=spy.send_one, sleep=lambda s: None)
        # Consolidated review finding 11.  Rung 1 DELIVERED a SIGTERM, so the cancelled
        # escalation is `exit_unproven` -- something happened and its outcome is unknown
        # -- and never `not_owned`, which means "no signal sent, no edge taken" and which
        # `lifecycle_for` maps to NO transition.  This test used to assert `not_owned`.
        self.assertEqual(result["interrupt_outcome"], "exit_unproven")
        self.assertEqual(interrupt_mod.lifecycle_for(result["interrupt_outcome"]),
                         {"state": "LOST", "lost_reason": "stop_unverified"})
        self.assertEqual([sig for _p, sig in spy.killpg], [15],
                         "SIGKILL reached a recycled pid")
        self.assertIn("escalation cancelled", result["ladder"][-1]["detail"])

    def test_an_unproven_exit_is_lost_never_terminated(self) -> None:
        spy = SignalSpy()
        result = interrupt_mod.interrupt(
            "intent-1", "stop", record=record(), profile=profile(),
            table_reader=lambda tty: snapshot(), supervisor_pid=999,
            killpg=spy.send_group, kill=spy.send_one, sleep=lambda s: None)
        self.assertEqual(result["interrupt_outcome"], "exit_unproven")
        mapped = interrupt_mod.lifecycle_for("exit_unproven")
        self.assertEqual(mapped["state"], "LOST")
        self.assertEqual(mapped["lost_reason"], "stop_unverified")

    def test_an_unreadable_table_after_a_signal_is_exit_unproven(self) -> None:
        """After a signal, "cannot see" is UNKNOWN -- not ``not_owned``, not terminated."""
        calls = {"n": 0}

        def reader(tty):
            calls["n"] += 1
            return snapshot() if calls["n"] <= 1 else snapshot(readable=False)

        result = interrupt_mod.interrupt(
            "intent-1", "stop", record=record(), profile=profile(),
            table_reader=reader, supervisor_pid=999, sleep=lambda s: None,
            killpg=lambda p, s: None, kill=lambda p, s: None)
        self.assertEqual(result["interrupt_outcome"], "exit_unproven")

    def test_every_ladder_step_records_whether_identity_was_verified(self) -> None:
        result = interrupt_mod.interrupt(
            "intent-1", "stop", record=record(), profile=profile(),
            table_reader=lambda tty: snapshot(), supervisor_pid=999,
            sleep=lambda s: None, killpg=lambda p, s: None, kill=lambda p, s: None)
        self.assertTrue(result["ladder"])
        for step in result["ladder"]:
            self.assertIn("identity_verified", step)
            self.assertIn(step["rung"], interrupt_mod.RUNGS + tuple(interrupt_mod.GATES))

    def test_completed_and_failed_are_unreachable_from_the_ladder(self) -> None:
        """V-3, as a NEGATIVE assertion over the whole closed outcome set."""
        for outcome in interrupt_mod.INTERRUPT_OUTCOMES:
            with self.subTest(outcome=outcome):
                self.assertNotIn(interrupt_mod.lifecycle_for(outcome)["state"],
                                 ("COMPLETED", "FAILED"))


if __name__ == "__main__":
    unittest.main()


# =====================================================================================
class DrainIsATeardownObligationTests(unittest.TestCase):
    """A REAL spawn: without draining the pty master, an exiting child never becomes reapable.

    Measured on this host, and it is the reason :func:`standalone_pty.drain` exists.  A
    session leader that exits while the pty slave holds unflushed output and nobody reads the
    master wedges in the kernel's "trying to exit" state: ``ps`` reports ``E``, the process
    leaves the tty, and ``waitpid`` keeps reporting it as a live child that has not changed
    state.  Every path that must PROVE an exit is then permanently unable to -- fail-closed,
    but fail-closed for the wrong reason, which looks exactly like the contract working.

    Both directions are asserted, because the negative half is what makes the positive one
    mean something.
    """

    def setUp(self) -> None:
        self.base = Path(tempfile.mkdtemp())
        self.stub = native_stub.native_stub_dir()

    def _spawn(self):
        # In the helper, not in `setUp`: the STATIC members of this class (the AST sweep of
        # the pre-exec path, the descriptor-range check, `release`'s drain ordering) assert
        # source properties and must keep running on a host with no compiler.
        if self.stub is None:
            self.skipTest(native_stub.NO_COMPILER_REASON)
        env = {"PATH": f"{self.stub}:/usr/bin:/bin",
               "OS37_STUB_MODE": "ready-slow", "TERM": "xterm-256color"}
        return pty_supervisor.spawn(
            argv=(str(self.stub / "os37-stub-cli"), "--session-id", "s-drain"),
            env=env, profile=profile(), session_id="s-drain",
            incarnation="i1", spawn_record_target=str(self.base / "spawn.i1"),
            sentinel=str(self.base / "exit.i1"), fence="s-drain:i1")

    def _settle(self, session, *, seconds: float = 1.0) -> None:
        deadline = time.time() + seconds
        while time.time() < deadline:
            pass

    #: Platforms on which THIS SUITE HAS REALLY RUN.  Not a claim about kernel behaviour --
    #: a claim about where these 56 cases have been executed and have passed.
    #:
    #: `darwin`: the development host.
    #: `linux`:  ubuntu-latest, GitHub Actions run 34541433633 on commit 5f9f4c4, all six
    #:           matrix jobs green.  `test_os37_pty_supervisor` is in NEITHER skip manifest
    #:           and no pty skip appears in that run's tolerated-skip reconciliation
    #:           (`platform=linux ... expected-here=54 matched=54 missing=0 unexpected=0`),
    #:           so every case here EXECUTED on a real Linux runner -- the real spawn, the
    #:           real `execve`, the real pty master, the real teardown and the portable
    #:           drain obligation below.
    #:
    #: This retires the standing limitation that Linux pty behaviour was "reasoned from
    #: code rather than measured".  It does NOT retire the narrower one immediately below,
    #: and the two are separate declarations for exactly that reason.
    PTY_SUITE_EXERCISED_PLATFORMS = ("darwin", "linux")

    #: Whether THIS platform is known to wedge an exiting session leader whose pty master
    #: nobody reads.  It is a MEASUREMENT, not a portable law, and it is written down here
    #: so the assertion below can be honest about which half of it is which.
    #:
    #: `darwin`: MEASURED on this host -- the leader stays unreapable until the master is
    #: drained, which is the whole reason `standalone_pty.drain` exists.
    #: everything else: **the wedge itself is UNMEASURED**, Linux included.  Running the
    #: suite on Linux did not measure it: the equality below sits behind a `sys.platform`
    #: BRANCH, not behind a skip, so the CI run executed the case and evaluated only the
    #: portable half.  A green Linux job is therefore evidence that the drain obligation
    #: holds there and NO evidence at all about the wedge, and adding `linux` here would
    #: assert a kernel behaviour nobody has observed -- which is the same class of claim
    #: that made the original unconditioned form fail on a Linux runner for a reason that
    #: says nothing about this repository.
    WEDGE_MEASURED_PLATFORMS = ("darwin",)

    def test_without_draining_the_exiting_child_is_not_reapable(self) -> None:
        """The NEGATIVE half, stated per platform rather than per lucky host.

        Two assertions, and BOTH are real work:

        * on a platform where the wedge is MEASURED, the leader must still be unreapable
          with no drain -- byte-for-byte the original assertion, at full strength;
        * on every platform -- and since commit 5f9f4c4 that demonstrably includes a real
          ubuntu-latest runner, not only this developer host -- draining afterwards must
          recover REAL BYTES from the master.
          That is the portable statement of the same obligation and it is what
          `StandaloneSession._prove_teardown`'s "Drain BEFORE waiting" depends on: the
          child wrote output nobody has read, so a teardown path that does not drain is
          proving an exit against an unread pipe whatever the kernel does with the leader.

        Neither assertion can be satisfied by the other's fix.
        """
        session = self._spawn()
        try:
            self._settle(session)
            os.killpg(session["pgid"], 9)
            self._settle(session, seconds=0.5)
            # The AGENT is a grandchild and was never this process's to reap; the pty
            # SESSION LEADER is, and it is the process the wedge happens to.
            reaped = os.waitpid(session["leader_pid"], os.WNOHANG)
            if sys.platform in self.WEDGE_MEASURED_PLATFORMS:
                self.assertEqual(
                    reaped, (0, 0),
                    "the child became reapable with no drain at all.  If this ever starts "
                    "passing the platform behaviour changed, and standalone_pty.drain's "
                    "rationale must be re-measured rather than deleted")
            drained = pty_supervisor.drain(session["master_fd"])
            self.assertGreater(
                drained, 0,
                "nothing was waiting on the pty master, so this run proves nothing about "
                "the drain obligation on either platform")
        finally:
            pty_supervisor.drain(session["master_fd"])
            try:
                os.waitpid(session["leader_pid"], 0)
            except OSError:
                pass
            pty_supervisor.release(session)

    def test_draining_makes_the_exiting_child_reapable(self) -> None:
        """The POSITIVE half: with the drain, the same child reaps immediately."""
        session = self._spawn()
        try:
            self._settle(session)
            drained = pty_supervisor.drain(session["master_fd"])
            self.assertGreater(drained, 0, "the stub wrote nothing to drain")
            os.killpg(session["pgid"], 9)
            self._settle(session, seconds=0.5)
            reaped = os.waitpid(session["leader_pid"], os.WNOHANG)
            self.assertEqual(
                reaped[0], session["leader_pid"],
                "the child was still unreapable after draining; teardown proof and the "
                "interrupt ladder's rung 4 both depend on this")
        finally:
            pty_supervisor.release(session)

    def test_release_drains_before_closing(self) -> None:
        """``release`` must not close a master that still holds bytes.

        Closing without draining is the same failure with a different trigger: the child is
        left unreapable and the runtime has leaked a process it cannot account for.
        """
        import ast
        import inspect
        source = inspect.getsource(pty_supervisor.release)
        tree = ast.parse(source.strip())
        calls = [node.func.id if isinstance(node.func, ast.Name) else node.func.attr
                 for node in ast.walk(tree) if isinstance(node, ast.Call)]
        self.assertIn("drain", calls, "release does not drain")
        self.assertLess(calls.index("drain"), calls.index("close"),
                        "release closes the master before draining it")

    def test_the_ladder_and_the_teardown_path_both_drain(self) -> None:
        """STATIC: every path that must prove an exit empties the pipe first."""
        import inspect

        from scripts.deterministic_workflow import standalone_interrupt, standalone_runtime
        self.assertIn(
            "drain", tuple(inspect.signature(standalone_interrupt.interrupt).parameters),
            "the interrupt ladder cannot be given a drain, so a real spawn's rung 4 would "
            "report exit_unproven for a process that died")
        teardown = inspect.getsource(standalone_runtime.StandaloneSession._prove_teardown)
        self.assertIn("self.pump(", teardown,
                      "_prove_teardown waits without draining")
        interrupting = inspect.getsource(standalone_runtime.StandaloneSession.interrupt)
        self.assertIn("drain=", interrupting,
                      "the session does not hand the ladder a drain")

    def test_the_child_closes_a_bounded_descriptor_range(self) -> None:
        """``closerange`` must be bounded by what is actually open, not by SC_OPEN_MAX.

        FACT on this host: ``SC_OPEN_MAX`` is 1 048 576, so the original
        ``closerange(3, SC_OPEN_MAX)`` issued a million ``close()`` calls on the pre-exec
        path of every single spawn.
        """
        highest = pty_supervisor.highest_open_fd()
        self.assertGreaterEqual(highest, 4096)
        self.assertLessEqual(
            highest, 65_536,
            f"highest_open_fd returned {highest}; the pre-exec close range must be bounded")
        import inspect
        spawn_source = inspect.getsource(pty_supervisor.spawn)
        self.assertIn("close_up_to", spawn_source)
        self.assertNotIn("SC_OPEN_MAX", spawn_source)

    def test_nothing_spawns_a_process_between_fork_and_execve(self) -> None:
        """Async-signal safety on the pre-exec path.

        Between ``fork()`` and ``execve()`` the child holds the parent's address space with
        none of its threads.  Running Python's subprocess machinery there is unsafe, and the
        earlier version did exactly that -- ``boot_id()`` ran ``sysctl`` and
        ``proc_start_ticks()`` ran ``ps``, both inside the forked child.
        """
        import ast
        import inspect
        import textwrap
        tree = ast.parse(textwrap.dedent(inspect.getsource(pty_supervisor.spawn)))
        # BOTH forked branches: the session leader (the exit watcher) and the agent.  Found
        # by shape -- `<something>_pid == 0` -- rather than by one hard-coded name, so a
        # renamed variable cannot silently empty this test's search.
        child_branches = [node for node in ast.walk(tree)
                          if isinstance(node, ast.If)
                          and isinstance(node.test, ast.Compare)
                          and isinstance(node.test.left, ast.Name)
                          and node.test.left.id.endswith("pid")
                          and len(node.test.ops) == 1
                          and isinstance(node.test.ops[0], ast.Eq)]
        self.assertEqual(
            len(child_branches), 2,
            "the two forked branches (session leader, agent) could not both be located")
        for branch in child_branches:
            for node in ast.walk(branch):
                if isinstance(node, ast.Call):
                    name = (node.func.attr if isinstance(node.func, ast.Attribute)
                            else getattr(node.func, "id", ""))
                    self.assertNotIn(
                        name, ("run", "Popen", "check_output", "popen", "system",
                               "boot_id"),
                        f"the forked child calls {name}( before execve; only "
                        "async-signal-safe work is permitted there")


class TtyNormalisationTests(unittest.TestCase):
    """The captured tty must be spelled the way ``ps -o tty=`` reports it, on BOTH platforms.

    A mismatch here is silent and total: every ownership check compares
    ``record["captured_tty"]`` against the table's ``tty`` column, so a wrong spelling makes
    every signal refuse with ``captured_tty_mismatch`` -- fail-closed, and permanently wrong.
    Darwin's ``ttyname`` gives ``/dev/ttys002`` while ``ps`` reports ``ttys002``; Linux gives
    ``/dev/pts/3`` while ``ps`` reports ``pts/3``.  The two strip differently.
    """

    def test_both_platform_spellings_normalise_to_what_ps_reports(self) -> None:
        from scripts.deterministic_workflow.standalone_runtime import _tty_name
        for raw, expected in (("/dev/ttys002", "ttys002"),   # darwin
                              ("/dev/ttys0", "ttys0"),
                              ("/dev/pts/3", "pts/3"),        # linux
                              ("/dev/pts/17", "pts/17"),
                              ("ttys002", "ttys002")):        # already normalised
            with self.subTest(raw=raw):
                self.assertEqual(_tty_name(raw), expected)

    def test_the_real_spawn_records_a_tty_the_process_table_can_find(self) -> None:
        """End to end, on this host: the recorded tty really resolves in ``ps``.

        The strongest form of the assertion -- it compares the runtime's own recorded value
        against a live, tty-scoped read rather than against a transcription of the platform's
        conventions.
        """
        stub = native_stub.native_stub_dir()
        if stub is None:
            self.skipTest(native_stub.NO_COMPILER_REASON)
        from scripts.deterministic_workflow.standalone_runtime import _tty_name
        base = Path(tempfile.mkdtemp())
        env = {"PATH": f"{stub}:/usr/bin:/bin", "OS37_STUB_MODE": "ready-slow",
               "TERM": "xterm-256color"}
        session = pty_supervisor.spawn(
            argv=(str(stub / "os37-stub-cli"), "--session-id", "s-tty"),
            env=env, profile=profile(), session_id="s-tty",
            incarnation="i1", spawn_record_target=str(base / "spawn.i1"),
            sentinel=str(base / "exit.i1"), fence="s-tty:i1")
        try:
            tty = _tty_name(session["slave_name"])
            deadline = time.time() + 3
            snapshot = {"readable": False, "rows": ()}
            while time.time() < deadline:
                snapshot = pty_supervisor.read_process_table(tty)
                if snapshot["readable"] and snapshot["rows"]:
                    break
            self.assertTrue(
                snapshot["readable"],
                f"the process table for the recorded tty {tty!r} could not be read; every "
                "ownership check would then fail closed on this platform")
            self.assertIsNotNone(
                pty_supervisor.row_for(snapshot, session["pid"]),
                f"the spawned pid is absent from its own tty-scoped table for {tty!r}")
        finally:
            pty_supervisor.drain(session["master_fd"])
            try:
                os.killpg(session["pgid"], 9)
            except OSError:
                pass
            pty_supervisor.drain(session["master_fd"])
            try:
                os.waitpid(session["leader_pid"], 0)
            except OSError:
                pass
            pty_supervisor.release(session)


# =====================================================================================
class ReadinessProcessProofTests(unittest.TestCase):
    """R-A (DESIGN §D5.3(4)): the four legs, and what may NOT stand in for leg 4.

    Leg 4 is an executable-IDENTITY proof -- "that foreground process's resolved executable
    equals the preflight-resolved ``realpath(profile.binary)`` -- not a shell, not an
    updater helper."  Three weaker things were tried during implementation and every one of
    them is asserted REJECTED here, because a green suite that accepts them proves the
    opposite of what R-A exists to prove:

    * a match on the foreground process's COMMAND LINE,
    * a match on some OTHER process in the child's session or process group,
    * ``argv[0]`` or ``ps`` output, which the target process itself chooses.

    The last one is not hypothetical.  MEASURED, and asserted live below: a process exec'd
    from image ``X`` with ``argv[0] = "totally-not-the-image"`` is reported as
    ``totally-not-the-image`` by both ``ps -o comm=`` and ``ps -o args=``.  Identity resting
    on either is identity resting on a string the impersonator supplies -- the same class of
    error as resting it on a terminal title, which this ticket forbids outright.
    """

    EXPECTED = "/opt/agent/bin/os37-agent"
    FOREIGN = "/bin/sh"

    def _proof(self, *, fg_pgid=CHILD_PID, rows=None, images=None, expected=None):
        images = images or {}
        return pty_supervisor.liveness_proof(
            record(), snapshot=snapshot(rows=rows), master_fd=None,
            expected_binary=self.EXPECTED if expected is None else expected,
            waitpid_status=lambda: True,
            tcgetpgrp=lambda: fg_pgid,
            resolve_executable=lambda pid: images.get(pid, ""))

    def test_leg_4_holds_when_the_foreground_image_is_the_profile_binary(self) -> None:
        proof = self._proof(images={CHILD_PID: self.EXPECTED})
        self.assertTrue(proof["foreground_executable_matches"])
        self.assertTrue(pty_supervisor.liveness_satisfied(proof))

    def test_a_command_line_naming_the_binary_does_not_satisfy_leg_4(self) -> None:
        """The foreground is a SHELL that carries the expected path as an argument.

        This is the exact impersonation leg 4 exists to refuse: an interpreter, wrapper or
        updater helper can hold the expected binary's path in its command line while the
        running image is something else entirely.
        """
        proof = self._proof(images={CHILD_PID: self.FOREIGN})
        self.assertFalse(proof["foreground_executable_matches"])
        self.assertFalse(pty_supervisor.liveness_satisfied(proof))
        self.assertEqual(proof["observed"]["foreground_executable"], self.FOREIGN)

    def test_the_command_line_is_not_even_an_input_to_the_proof(self) -> None:
        """Structural, not behavioural: there is no seam through which it could return.

        A behavioural assertion alone would pass again the moment somebody re-added a
        command-line fallback under a different name, so the SEAM is asserted gone too.
        """
        import inspect
        params = inspect.signature(pty_supervisor.liveness_proof).parameters
        self.assertNotIn("resolve_command_line", params)
        self.assertFalse(hasattr(pty_supervisor, "resolve_command"),
                         "a command-line reader is back on the readiness path")
        self.assertFalse(hasattr(pty_supervisor, "_names_binary"),
                         "a command-line token matcher is back on the readiness path")
        source = inspect.getsource(pty_supervisor.liveness_proof)
        for banned in ("args=", "command", "cmdline"):
            self.assertNotIn(f"{banned}(", source)

    def test_a_matching_process_elsewhere_in_the_session_does_not_satisfy_leg_4(self) -> None:
        """"Some process in this scope is the binary" is not "the foreground is the binary".

        It is satisfied while a shell holds the pty foreground and the agent sits stopped,
        backgrounded, or already reparented -- which is precisely the state a readiness
        proof must refuse.
        """
        rows = (
            {"pid": CHILD_PID, "ppid": 1, "pgid": CHILD_PID, "sid": CHILD_PID,
             "tty": TTY, "stat": "Ss"},
            {"pid": CHILD_PID + 7, "ppid": CHILD_PID, "pgid": CHILD_PID,
             "sid": CHILD_PID, "tty": TTY, "stat": "S"},
        )
        proof = self._proof(rows=rows,
                            images={CHILD_PID: self.FOREIGN,
                                    CHILD_PID + 7: self.EXPECTED})
        self.assertFalse(
            proof["foreground_executable_matches"],
            "leg 4 was satisfied by a process that is NOT the pty's foreground")

    def test_leg_3_is_an_equality_not_a_descendant_search(self) -> None:
        """A descendant group holding the foreground is not the child's group holding it."""
        rows = (
            {"pid": CHILD_PID, "ppid": 1, "pgid": CHILD_PID, "sid": CHILD_PID,
             "tty": TTY, "stat": "Ss"},
            {"pid": CHILD_PID + 9, "ppid": CHILD_PID, "pgid": CHILD_PID + 9,
             "sid": CHILD_PID, "tty": TTY, "stat": "S+"},
        )
        proof = self._proof(fg_pgid=CHILD_PID + 9, rows=rows,
                            images={CHILD_PID + 9: self.EXPECTED})
        self.assertFalse(proof["foreground_is_child_group"])
        self.assertFalse(pty_supervisor.liveness_satisfied(proof))

    def test_an_unanswerable_platform_fails_leg_4_closed(self) -> None:
        """No answer is not a match.  Unknown never advances anything (RULE 4)."""
        proof = self._proof(images={})
        self.assertFalse(proof["foreground_executable_matches"])

    def test_no_expected_binary_fails_leg_4_closed(self) -> None:
        proof = self._proof(expected="", images={CHILD_PID: self.EXPECTED})
        self.assertFalse(proof["foreground_executable_matches"])


# =====================================================================================
class LiveExecutableIdentityTests(unittest.TestCase):
    """The same proof against REAL processes, because the deterministic half above uses an
    injected resolver and therefore cannot catch a resolver that reads the wrong thing.
    """

    def setUp(self) -> None:
        self.stub_dir = native_stub.native_stub_dir()
        if self.stub_dir is None:
            self.skipTest(native_stub.NO_COMPILER_REASON)
        self.native = self.stub_dir / "os37-stub-cli"
        self.script = (Path(__file__).resolve().parent / "fixtures" / "os37" / "bin"
                       / "os37-stub-cli")
        self.reap: list[int] = []

    def tearDown(self) -> None:
        for pid in self.reap:
            try:
                os.kill(pid, 9)
                os.waitpid(pid, 0)
            except OSError:
                pass

    def _run(self, image: Path, argv: list[str]) -> int:
        pid = os.fork()
        if pid == 0:                                        # pragma: no cover
            try:
                devnull = os.open(os.devnull, os.O_RDWR)
                os.dup2(devnull, 1)
                os.dup2(devnull, 2)
                os.execve(str(image), argv,
                          {"OS37_STUB_MODE": "auth-interactive", "PATH": "/usr/bin:/bin"})
            except BaseException:
                pass
            os._exit(127)
        self.reap.append(pid)
        # Wait for the EXECVE, not merely for the fork.  Between `fork` and `execve` the
        # child's kernel image is still THIS interpreter's, so a loop that breaks on the
        # first non-empty answer can sample the parent's image and make the assertion below
        # fail for a process that was about to become the fixture.  Measured: it does,
        # under load.  Waiting for the image to stop being ours is the real readiness
        # condition, and it cannot mask a failure -- if the execve never happens the loop
        # runs to its deadline and the assertion still reports whatever was resolved.
        ours = os.path.realpath(sys.executable)
        deadline = time.time() + 5
        while time.time() < deadline:
            resolved = pty_supervisor._resolve_executable(pid)
            if resolved and os.path.realpath(resolved) != ours:
                break
            time.sleep(0.005)
        return pid

    def test_a_lying_argv0_cannot_impersonate_or_disown_an_image(self) -> None:
        """The kernel's answer, not the process's own claim about itself.

        ``ps`` is asserted to report the LIE here, deliberately: that is the measurement
        that makes "do not read ``ps``" a fact rather than a preference, and if a future
        platform stops reporting the lie this test says so instead of quietly agreeing.
        """
        pid = self._run(self.native, ["totally-not-the-image"])
        self.assertEqual(pty_supervisor._resolve_executable(pid),
                         os.path.realpath(self.native),
                         "the resolver did not report the kernel's image path")
        reported = subprocess.run(["ps", "-p", str(pid), "-o", "args="],
                                  capture_output=True, text=True, check=False).stdout
        self.assertIn("totally-not-the-image", reported,
                      "this platform no longer lets argv[0] lie to ps; the rationale for "
                      "reading the kernel image instead must be re-measured, not deleted")
        self.assertNotIn(os.path.basename(str(self.native)), reported)

    def test_a_script_whose_command_line_names_the_binary_fails_leg_4(self) -> None:
        """The END-TO-END form of the rejected impersonation, with no injection at all.

        The shell fixture's command line contains its own path; its IMAGE is the
        interpreter.  A command-line comparison would accept it.  Leg 4 must not.
        """
        if not self.script.exists():
            self.skipTest("the OS-37 shell stub fixture is absent")
        pid = self._run(self.script, [str(self.script)])
        image = pty_supervisor._resolve_executable(pid)
        self.assertNotEqual(image, os.path.realpath(self.script),
                            "a `#!` script's image is its interpreter; this platform "
                            "disagrees and the fixture's premise must be re-measured")
        proof = pty_supervisor.liveness_proof(
            record(pid=pid, pgid=pid, sid=pid),
            snapshot=snapshot(rows=({"pid": pid, "ppid": 1, "pgid": pid, "sid": pid,
                                     "tty": TTY, "stat": "S+"},)),
            master_fd=None, expected_binary=str(self.script),
            waitpid_status=lambda: True, tcgetpgrp=lambda: pid)
        self.assertFalse(proof["foreground_executable_matches"])
        self.assertFalse(pty_supervisor.liveness_satisfied(proof))

    def test_the_real_spawn_puts_the_profile_binary_in_the_pty_foreground(self) -> None:
        """The positive end-to-end: leg 3 and leg 4 both hold against a live spawn."""
        base = Path(tempfile.mkdtemp())
        env = {"PATH": f"{self.stub_dir}:/usr/bin:/bin", "TERM": "xterm-256color",
               "OS37_STUB_MODE": "ready-slow", "OS37_STUB_SESSION_ID": "s-ident"}
        session = pty_supervisor.spawn(
            argv=(str(self.native), "--session-id", "s-ident"), env=env,
            profile=profile(), session_id="s-ident", incarnation="i1",
            spawn_record_target=str(base / "spawn.i1"),
            sentinel=str(base / "exit.i1"), fence="s-ident:i1")
        try:
            from scripts.deterministic_workflow.standalone_runtime import _tty_name
            tty = _tty_name(session["slave_name"])
            deadline = time.time() + 5
            proof: dict = {}
            while time.time() < deadline:
                snap = pty_supervisor.read_process_table(tty)
                if not snap["readable"]:
                    continue
                proof = pty_supervisor.liveness_proof(
                    dict(record(pid=session["pid"], pgid=session["pgid"],
                                sid=session["sid"], captured_tty=tty)),
                    snapshot=snap, master_fd=session["master_fd"],
                    expected_binary=str(self.native))
                if pty_supervisor.liveness_satisfied(proof):
                    break
            self.assertTrue(
                pty_supervisor.liveness_satisfied(proof),
                f"R-A did not hold against a real spawn: "
                f"{ {k: v for k, v in proof.items() if k != 'observed'} } "
                f"{proof.get('observed')}")
            self.assertEqual(proof["observed"]["foreground_executable"],
                             os.path.realpath(self.native))
            self.assertEqual(os.tcgetpgrp(session["master_fd"]), session["pgid"])
        finally:
            pty_supervisor.drain(session["master_fd"])
            try:
                os.killpg(session["pgid"], 9)
            except OSError:
                pass
            pty_supervisor.drain(session["master_fd"])
            try:
                os.waitpid(session["leader_pid"], 0)
            except OSError:
                pass
            pty_supervisor.release(session)
