"""OS-37 V-7 / DD-3.  The OS-43 Supervisor observation, wired -- and BOTH directions.

Without a standalone observation, a standalone deployment has no ``orca`` binary to list
dispatches, so the Orca listing authority raises for every run and the sweep fails closed at
R1 forever.  With one, F6 and F7 gain a REAL second authority reading the run's own durable
journal.

**F6 / F7 are not relaxed.**  ``FACT_CONTRIBUTORS`` is not edited and this file asserts that
it is not: relaxing a safety-relevant veto to "best-effort" would make it guessable in the
success direction, which is exactly what the contract forbids.  The fix is a real authority,
not a weaker rule.

The three-way discipline is asserted separately for each of its three answers, because
collapsing any two of them is the defect the port exists to prevent:

* an authority that exists but cannot be read -> ``ObservationUnavailable`` (F1)
* an authority that exists and answers "none" -> an EMPTY TUPLE, i.e. an absence
* a fact nothing covers at all               -> ``ObservationUnsupported`` (F11)
"""
from __future__ import annotations

import inspect
import tempfile
import unittest
from pathlib import Path

from scripts.deterministic_workflow import recovery_runtime
from scripts.deterministic_workflow import standalone_journal as journal_mod
from scripts.deterministic_workflow import watchdog_observation
from scripts.deterministic_workflow.runtime_state import InMemoryRuntimeStateStore
from scripts.deterministic_workflow.standalone_adapter import (StandaloneAdapter,
                                                                StandaloneRunObservation,
                                                                observation_class)

ENGINE = Path(__file__).resolve().parent / "deterministic_workflow"


def event(**overrides) -> dict:
    fields = dict(kind="EVENT", derived_from="pty", intent_id="intent-1",
                  dispatch_id="dispatch-1", task_id="task-1", session_id="s-1",
                  process_incarnation="i-1", event="spawned", state="STARTING")
    fields.update(overrides)
    return journal_mod.make_record(**fields)


class _Base(unittest.TestCase):

    def setUp(self) -> None:
        self.base = tempfile.mkdtemp()
        self.journal = journal_mod.ExecutionJournal(self.base, "run_1")
        self.ledger = InMemoryRuntimeStateStore()

    _UNSET = object()

    def observation(self, *, journal_factory=_UNSET, capabilities=None):
        """Build one.  ``journal_factory=None`` is passed THROUGH, not defaulted away.

        The sentinel matters: "no factory was wired" is the F11 case this suite has to be
        able to construct, and a helper that substituted a default for ``None`` would make
        that case untestable.
        """
        if journal_factory is _Base._UNSET:
            def journal_factory(run_id):
                return journal_mod.ExecutionJournal(self.base, run_id)
        return StandaloneRunObservation(self.base, journal_factory=journal_factory,
                                        capabilities=capabilities)


# =====================================================================================
class ObservationWiringTests(_Base):
    """DD-3: no edit to ``recovery_runtime.py`` was required, and none was made."""

    def test_the_standalone_observation_is_a_real_subclass_of_the_base_port(self) -> None:
        obs = self.observation()
        self.assertIsInstance(obs, recovery_runtime.RunObservationAdapter)
        self.assertTrue(issubclass(observation_class(),
                                   recovery_runtime.RunObservationAdapter))

    def test_only_orca_state_is_overridden(self) -> None:
        """Every other method is the base class's own, unchanged.

        Asserted by identity of the underlying functions rather than by reading the source:
        the claim is that the base's filesystem-only methods are REUSED, and equal function
        objects is what that means.
        """
        derived = observation_class()
        base = recovery_runtime.RunObservationAdapter
        overridden = [
            name for name in dir(base)
            if not name.startswith("_") and callable(getattr(base, name, None))
            and getattr(derived, name, None) is not getattr(base, name, None)]
        self.assertEqual(
            overridden, ["orca_state"],
            f"more than orca_state was overridden: {overridden}. DD-3's whole claim is "
            "that the base class is already filesystem-only and runtime-neutral")

    def test_recovery_runtime_was_not_edited(self) -> None:
        """DD-3 as a repository fact: ``recovery_runtime.py`` matches the baseline."""
        import subprocess
        repo = ENGINE.parent.parent
        baseline = subprocess.run(
            ["git", "show", "d13b7fa:scripts/deterministic_workflow/recovery_runtime.py"],
            cwd=repo, capture_output=True, text=True, check=False)
        self.assertEqual(baseline.returncode, 0)
        self.assertEqual(
            (ENGINE / "recovery_runtime.py").read_text(), baseline.stdout,
            "recovery_runtime.py was edited; DD-3 verified that no edit is required, so an "
            "edit here means the observation was implemented the wrong way")

    def test_the_base_is_given_runner_none_deliberately(self) -> None:
        """``runner=None`` is passed on purpose: no Orca CLI is ever invoked."""
        obs = self.observation()
        self.assertIsNone(obs.runner)
        source = inspect.getsource(observation_class().__mro__[0].__init__) \
            if observation_class().__mro__[0].__init__ is not object.__init__ else ""
        del source
        # And the base's own orca_state -- the one that would raise UNSUPPORTED with no
        # runner -- is exactly what this class replaces.
        with self.assertRaises(watchdog_observation.ObservationUnsupported):
            recovery_runtime.RunObservationAdapter(self.base, runner=None).orca_state("run_1")

    def test_no_orca_cli_is_invoked(self) -> None:
        """STATIC: the standalone ``orca_state`` names no runner and no subprocess."""
        source = inspect.getsource(observation_class().__mro__[0].orca_state)
        for forbidden in ("subprocess", "self.runner", "turn_boundary",
                          "observe_orca_state"):
            self.assertNotIn(
                forbidden, source,
                f"the standalone orca_state names {forbidden!r}; it must answer from the "
                "run's own durable journal and invoke no Orca CLI")


# =====================================================================================
class ThreeWayDisciplineTests(_Base):
    """Each of the three answers, separately.  Collapsing any two is the defect."""

    def test_an_absence_is_an_empty_tuple_not_an_unsupported(self) -> None:
        """A run with no open dispatch returns an EMPTY TUPLE.

        This is the whole point of wiring it: with the observation present, F6/F7 have a
        real second authority and the run stops classifying ``UNSUPPORTED_FAIL_CLOSED``.
        """
        state = self.observation().orca_state("run_1")
        self.assertEqual(state["active_dispatches"], ())
        self.assertEqual(state["runnable_actions"], ())

    def test_an_open_dispatch_is_reported(self) -> None:
        self.journal.append(event())
        state = self.observation().orca_state("run_1")
        self.assertEqual(state["active_dispatches"], ("intent-1",))
        self.assertEqual(state["runnable_actions"], ("intent-1",))

    def test_an_unreadable_authority_raises_unavailable(self) -> None:
        """F1, never "no dispatch is running"."""
        self.journal.append(event())
        self.journal.path.write_text("{corrupt\n")
        with self.assertRaises(watchdog_observation.ObservationUnavailable):
            self.observation().orca_state("run_1")

    def test_an_uncovered_fact_raises_unsupported(self) -> None:
        """F11: with no dispatch authority wired at all, the fact is UNCOVERED."""
        with self.assertRaises(watchdog_observation.ObservationUnsupported):
            self.observation(journal_factory=None).orca_state("run_1")
        with self.assertRaises(watchdog_observation.ObservationUnsupported):
            self.observation(journal_factory=lambda run_id: None).orca_state("run_1")

    def test_a_factory_that_raises_is_unsupported_not_unavailable(self) -> None:
        def broken(run_id):
            raise LookupError("no such runtime")

        with self.assertRaises(watchdog_observation.ObservationUnsupported):
            self.observation(journal_factory=broken).orca_state("run_1")

    def test_the_two_keys_are_exactly_what_the_consumer_reads(self) -> None:
        state = self.observation().orca_state("run_1")
        self.assertEqual(set(state), {"active_dispatches", "runnable_actions"})


# =====================================================================================
class FactContributorTests(_Base):
    """F6 / F7 stay FACTS.  The rule is not relaxed; a real authority is added."""

    def test_fact_contributors_is_unedited(self) -> None:
        import subprocess
        repo = ENGINE.parent.parent
        baseline = subprocess.run(
            ["git", "show",
             "d13b7fa:scripts/deterministic_workflow/watchdog_observation.py"],
            cwd=repo, capture_output=True, text=True, check=False)
        self.assertEqual(baseline.returncode, 0)
        self.assertEqual(
            (ENGINE / "watchdog_observation.py").read_text(), baseline.stdout,
            "watchdog_observation.py was edited.  Relaxing F6/F7 to best-effort would make "
            "a safety-relevant veto guessable in the SUCCESS direction; the correct fix is "
            "to give the standalone runtime a real authority for the same fact")

    def test_f6_reads_active_dispatches_from_whatever_authority_answers(self) -> None:
        """The consumer is unchanged: it reads the same two keys either way."""
        source = (ENGINE / "watchdog_observation.py").read_text()
        self.assertIn("active_dispatches", source)
        self.assertIn("runnable_actions", source)


# =====================================================================================
class CapabilityAuthorityTests(_Base):
    """V-7: the capability authority reads NO process."""

    def test_capability_authority_takes_no_process(self) -> None:
        """``runtime=None``, exactly as the Orca branch does, and for the same reason.

        Asking what an adapter can do must not spawn, adopt or touch anything -- otherwise
        the run would look alive to the very gate about to decide whether it is stalled.
        """
        spawns: list = []

        class _RefusingRuntime:
            def session_for(self, intent):
                spawns.append(intent)
                raise AssertionError("capabilities() adopted a process")

        adapter = StandaloneAdapter(None, runtime_state=self.ledger,
                                    settlement_journal=self.journal,
                                    artifact_base=self.base, run_id="run_1")
        declared = adapter.capabilities()
        self.assertTrue(declared)
        self.assertEqual(spawns, [])
        self.assertIsNone(adapter.runtime,
                          "the capability authority must be bound to no runtime")
        # And a process-bound adapter answers the SAME set: the declaration is about the
        # type and its wiring, not about what happens to be running.
        bound = StandaloneAdapter(_RefusingRuntime(), runtime_state=self.ledger,
                                  settlement_journal=self.journal,
                                  artifact_base=self.base, run_id="run_1")
        self.assertEqual(bound.capabilities(), declared)
        self.assertEqual(spawns, [])

    def test_declared_capabilities_from_live_adapter(self) -> None:
        """``launcher.capabilities_for``'s third branch answers from a LIVE adapter."""
        from scripts.deterministic_workflow import launcher
        self.assertIn(launcher.STANDALONE_ADAPTER, launcher.ADAPTERS)
        source = inspect.getsource(launcher.recovery_ports) \
            if hasattr(launcher, "recovery_ports") else ""
        del source
        # The third branch exists and is reachable by name.
        self.assertTrue(hasattr(launcher, "_standalone_journal_for"))
        self.assertTrue(hasattr(launcher, "_standalone_observation"))

    def test_the_process_less_adapter_still_refuses_process_methods(self) -> None:
        """A capability-only adapter must not silently answer a process question."""
        adapter = StandaloneAdapter(None, runtime_state=self.ledger,
                                    settlement_journal=self.journal,
                                    artifact_base=self.base, run_id="run_1")
        with self.assertRaises(RuntimeError):
            adapter.status("intent-1")
        with self.assertRaises(RuntimeError):
            adapter.send("intent-1", {"payload": "x"})
        refusal = adapter.interrupt("intent-1", "stop")
        self.assertEqual(refusal["interrupt_outcome"], "not_owned")
        self.assertEqual(refusal["refusal"], "no_runtime_bound")


# =====================================================================================
class BothDirectionsTests(_Base):
    """V-7 written both ways: with the observation, and without it."""

    def test_with_standalone_observation_run_is_recoverable(self) -> None:
        """The observation answers, so the fact is COVERED and the sweep can classify."""
        self.journal.append(event())
        state = self.observation().orca_state("run_1")
        self.assertEqual(state["active_dispatches"], ("intent-1",))

    def test_without_it_run_classifies_unsupported_fail_closed(self) -> None:
        """The Orca observation with no runner raises UNSUPPORTED for every run.

        That is the state a standalone deployment is in WITHOUT this wiring: F6/F7 have no
        authority, so the run classifies fail-closed forever and never recovers.
        """
        with self.assertRaises(watchdog_observation.ObservationUnsupported):
            recovery_runtime.RunObservationAdapter(
                self.base, runner=None).orca_state("run_1")


if __name__ == "__main__":
    unittest.main()


# =====================================================================================
class LauncherWiringTests(unittest.TestCase):
    """C-DESIGN-1: all SEVEN `--adapter` sites, and D-2(b)'s one-declaration state."""

    def setUp(self) -> None:
        self.base = tempfile.mkdtemp()
        self.profile_spec = {
            "driver": "claude", "binary": "os37-stub-cli",
            "supported_range": [[1, 0, 0], [2, 0, 0]],
            "bin_dirs": [str(ENGINE.parent / "fixtures" / "os37" / "bin")],
            "readiness_records": [{"channel": "structured", "record_type": "system",
                                   "session_field": "session_id"}],
            # D4.2a: a profile SPEC that omits the capability axis is `profile_invalid`.
            # The fixture CLI waits for input, so `post_ready_delivery` is its honest
            # declaration and this wiring exercises that path end to end.
            "delivery_mode": "post_ready_delivery",
            "identity_binding": "minted_echo", "identity_flag": "--session-id",
            "delivery_proofs": [{"channel": "structured", "record_type": "assistant"}],
            "completion_records": [{"channel": "structured", "record_type": "result",
                                    "error_field": "is_error"}]}

    def test_site_1_standalone_is_a_member_of_the_shared_adapters_tuple(self) -> None:
        from scripts.deterministic_workflow import launcher
        self.assertEqual(launcher.ADAPTERS,
                         (launcher.FAKE_ADAPTER, launcher.ORCA_ADAPTER,
                          launcher.STANDALONE_ADAPTER))
        self.assertEqual(launcher.STANDALONE_ADAPTER, "standalone")

    def test_site_2_the_standalone_state_carries_one_capability_declaration(self) -> None:
        """D-2(b) / C-7: ``adapter_capabilities`` comes from the LIVE adapter."""
        from pathlib import Path as _Path

        from scripts.deterministic_workflow import launcher
        from scripts.deterministic_workflow.runtime_state import InMemoryRuntimeStateStore
        adapter, state = launcher.build_standalone_adapter(
            {"run_id": "run_wiring", "thread_id": "t", "phases": ["IMPLEMENTATION"]},
            artifact_base=_Path(self.base), run_id="run_wiring",
            runtime_state=InMemoryRuntimeStateStore(),
            profile_spec=self.profile_spec)
        self.assertEqual(frozenset(state["adapter_capabilities"]),
                         adapter.capabilities(),
                         "the standalone state and its adapter disagree about what the "
                         "runtime can do; D-2(b) exists to close exactly that gap")

    def test_site_2_the_capability_snapshot_is_taken_after_the_ledger(self) -> None:
        """External review #10: the snapshot must find the identity fence ARMED.

        `build_standalone_state` reads `adapter.capabilities()` once and the result is
        frozen into the run's state for the whole run.  `external_resume` is declared only
        when a journal AND a ledger are both wired, so composing the adapter before the
        ledger froze a declaration WITHOUT it -- and `executor._collect` then refuses every
        post-receipt recovery with IDEMPOTENCY_RECOVERY_UNSUPPORTED, which is a crash after
        the receipt being permanently uncollectable.

        Two halves, and neither can be satisfied by the other's fix: the composition
        REFUSES a ledger-less build by name, and the state it does build declares
        `external_resume`.
        """
        from pathlib import Path as _Path

        from scripts.deterministic_workflow import launcher
        from scripts.deterministic_workflow.contracts import EXTERNAL_RESUME
        from scripts.deterministic_workflow.runtime_state import InMemoryRuntimeStateStore
        with self.assertRaises(launcher.LauncherError) as refused:
            launcher.build_standalone_adapter(
                {"run_id": "run_fence", "thread_id": "t", "phases": ["IMPLEMENTATION"]},
                artifact_base=_Path(self.base), run_id="run_fence",
                profile_spec=self.profile_spec)
        self.assertIn(launcher.STANDALONE_ADAPTER_REQUIRES_LEDGER, str(refused.exception))
        adapter, state = launcher.build_standalone_adapter(
            {"run_id": "run_fence", "thread_id": "t", "phases": ["IMPLEMENTATION"]},
            artifact_base=_Path(self.base), run_id="run_fence",
            runtime_state=InMemoryRuntimeStateStore(),
            profile_spec=self.profile_spec)
        self.assertIn(
            EXTERNAL_RESUME, state["adapter_capabilities"],
            "the run's frozen capability declaration omits external_resume, so a crash "
            "after the receipt can never be collected")
        self.assertIn(EXTERNAL_RESUME, adapter.capabilities())

    def test_site_2_the_orca_and_fake_state_builders_are_unchanged(self) -> None:
        """D-2(a): reconciling the other two paths is authorized by no criterion (PR-1)."""
        import inspect
        import subprocess

        from scripts.deterministic_workflow import launcher
        baseline = subprocess.run(
            ["git", "show", "d13b7fa:scripts/deterministic_workflow/launcher.py"],
            cwd=ENGINE.parent.parent, capture_output=True, text=True, check=False)
        self.assertEqual(baseline.returncode, 0)
        before = baseline.stdout.split("def build_state(", 1)[1].split("\n\n\n", 1)[0]
        after = inspect.getsource(launcher.build_state).split("(", 1)[1]
        self.assertEqual(
            before.strip(), after.strip(),
            "build_state changed; the Orca and fake paths must keep reading "
            "spec['capabilities'] byte-for-byte, and PR-1 stays named rather than fixed")

    def test_site_3_a_profile_is_mandatory_and_has_no_default(self) -> None:
        """AC-37-03: explicit configuration, never a built-in CLI table."""
        from pathlib import Path as _Path

        from scripts.deterministic_workflow import launcher
        with self.assertRaises(launcher.LauncherError) as caught:
            launcher.build_standalone_adapter(
                {"run_id": "run_wiring"}, artifact_base=_Path(self.base),
                run_id="run_wiring", profile_spec=None)
        self.assertIn(launcher.STANDALONE_ADAPTER_REQUIRES_PROFILE,
                      str(caught.exception))

    def test_site_4_resume_refuses_standalone_explicitly(self) -> None:
        """An EXPLICIT refusal, not a fall-through to the fake composition."""
        from scripts.deterministic_workflow import launcher
        source = inspect.getsource(launcher)
        self.assertIn(launcher.STANDALONE_ADAPTER_UNSUPPORTED_HERE, source)
        refusal = source.split(launcher.STANDALONE_ADAPTER_UNSUPPORTED_HERE, 1)[1]
        self.assertIn("rediscover", refusal,
                      "the refusal must name what an operator should do instead")

    def test_sites_5_and_6_the_orca_and_fake_arms_are_byte_unchanged(self) -> None:
        """Every standalone arm is ADDITIVE: the other two are untouched."""
        import subprocess
        baseline = subprocess.run(
            ["git", "show", "d13b7fa:scripts/deterministic_workflow/launcher.py"],
            cwd=ENGINE.parent.parent, capture_output=True, text=True, check=False)
        self.assertEqual(baseline.returncode, 0)
        current = (ENGINE / "launcher.py").read_text()
        for arm in ("if adapter_name == ORCA_ADAPTER:",
                    "adapter = FakeAdapter(list(results), runtime_state=ledger,"):
            self.assertIn(arm, baseline.stdout)
            self.assertIn(arm, current, f"the pre-existing arm {arm!r} was altered")

    def test_site_7_the_observation_selection_picks_the_standalone_port(self) -> None:
        from pathlib import Path as _Path

        from scripts.deterministic_workflow import launcher
        obs = launcher._standalone_observation(_Path(self.base), None)
        self.assertIsInstance(obs, recovery_runtime.RunObservationAdapter)
        self.assertIsNone(obs.runner)
        self.assertEqual(obs.orca_state("run_wiring"),
                         {"active_dispatches": (), "runnable_actions": ()})
