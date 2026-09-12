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
def _is_codeish(line: str) -> bool:
    """True for a line that could execute; False for prose inside a docstring.

    Deliberately crude and INCLUSIVE -- anything it is unsure about counts as code, so the
    guard errs towards failing rather than towards letting an edit through.
    """
    stripped = line.strip()
    if not stripped or stripped.startswith(("#", "*", "|")):
        return False
    return any(token in stripped for token in ("=", "(", "return", "raise", "import"))


def _native_stub_dir():
    from scripts import os37_native_stub as native_stub
    built = native_stub.native_stub_dir()
    if built is None:                                     # pragma: no cover - CI has cc
        raise AssertionError(native_stub.NO_COMPILER_REASON)
    return built


class LauncherWiringTests(unittest.TestCase):
    """C-DESIGN-1: all SEVEN `--adapter` sites, and D-2(b)'s one-declaration state."""

    def setUp(self) -> None:
        self.base = tempfile.mkdtemp()
        self.profile_spec = {
            "driver": "claude", "binary": "os37-stub-cli",
            "supported_range": [[1, 0, 0], [2, 0, 0]],
            # The NATIVE image (round 4, finding 10: a `#!` wrapper is refused by name).
            "bin_dirs": [str(_native_stub_dir())],
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

    #: The ONE change to `build_state` this ticket's correction R4 authorises: the optional
    #: declared decision block, without which `decision_state` is written by no code on the
    #: launch path at all and the graph's PAUSE node is unreachable from the CLI for EVERY
    #: adapter.  Spelled out line by line, stripped, so the guard below stays a BYTE-level
    #: check with exactly one enumerated exception rather than becoming a loose one.
    DECLARED_DECISION_INSERTION = (
        'declared = spec.get("decision_state")',
        'if declared is not None:',
        'state = dict(validate_state(',
        '{**state, **typed_update(',
        '"SET_DECISION", decision_state=declared,',
        'decision_reason_code=spec.get("decision_reason_code"))},',
        'expected_thread_id=state["thread_id"]))',
        'return state',
        # The `return` had to become a binding: the declaration is applied to the state the
        # baseline returned directly, so the state now needs a name.  Enumerated with its
        # exact baseline counterpart below rather than waived.
        'state = dict(initial_state(',
    )

    #: The ONLY baseline lines the insertion above is allowed to displace, each with the
    #: line that replaces it.  Anything else removed or rewritten fails.
    AUTHORISED_REPLACEMENTS = {
        'return dict(initial_state(': 'state = dict(initial_state(',
        # The docstring's first line lost its closing quotes when the prose that explains
        # the optional declaration was appended.  The SENTENCE is unchanged, and it is
        # matched here in full so a rewrite of it would still be reported.
        '"""Build a validated initial state from a small JSON launch specification."""':
            '"""Build a validated initial state from a small JSON launch specification.',
    }

    def test_site_2_the_orca_and_fake_state_builders_are_unchanged(self) -> None:
        """D-2(a): reconciling the other two paths is authorized by no criterion (PR-1).

        Still a byte-level guard, and deliberately still anchored on `d13b7fa` -- the
        pre-OS-37 revision -- rather than re-frozen on the current text.  What changed is
        that the single authorised insertion is now ENUMERATED instead of the whole function
        being required to be identical:

        * **nothing may be removed or modified.**  The line sequence of the baseline body
          must still appear, in order, with no `delete` and no `replace` -- so a change to
          how `spec["capabilities"]` is read, or a capability reconciliation slipped into the
          Orca/fake path, fails exactly as it did before;
        * **the only additions are the declared decision block**, matched line for line
          against the tuple above.  Any other insertion fails and is named.

        Plus two properties the byte check was standing in for, now asserted directly, so
        PR-1 cannot be quietly closed even by an edit that somehow satisfied the above.
        """
        import difflib
        import inspect
        import subprocess

        from scripts.deterministic_workflow import launcher
        baseline = subprocess.run(
            ["git", "show", "d13b7fa:scripts/deterministic_workflow/launcher.py"],
            cwd=ENGINE.parent.parent, capture_output=True, text=True, check=False)
        self.assertEqual(baseline.returncode, 0)
        before = baseline.stdout.split("def build_state(", 1)[1].split("\n\n\n", 1)[0]
        after = inspect.getsource(launcher.build_state).split("(", 1)[1]
        before_lines = [line.strip() for line in before.strip().splitlines() if line.strip()]
        after_lines = [line.strip() for line in after.strip().splitlines() if line.strip()]
        matcher = difflib.SequenceMatcher(a=before_lines, b=after_lines, autojunk=False)
        removed: list[str] = []
        inserted: list[str] = []
        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag in ("delete", "replace"):
                removed.extend(before_lines[i1:i2])
            if tag in ("insert", "replace"):
                inserted.extend(after_lines[j1:j2])
        unauthorised = [line for line in removed if line not in self.AUTHORISED_REPLACEMENTS]
        self.assertEqual(
            unauthorised, [],
            "build_state LOST or CHANGED a line it had at d13b7fa that this correction is "
            "not authorised to touch; the Orca and fake paths must keep reading "
            f"spec['capabilities'] byte-for-byte: {unauthorised}")
        for gone, replacement in self.AUTHORISED_REPLACEMENTS.items():
            if gone in removed:
                self.assertIn(
                    replacement, after_lines,
                    f"{gone!r} was removed and its authorised replacement "
                    f"{replacement!r} is not there either")
        unexpected = [line for line in inserted
                      if line not in self.DECLARED_DECISION_INSERTION
                      and not line.startswith(("#", '"""', "*", "`"))
                      and not line.endswith('"""')]
        # Prose lines of the docstring are not code and are filtered above; every remaining
        # inserted line must be one of the enumerated ones.
        unexpected = [line for line in unexpected if _is_codeish(line)]
        self.assertEqual(
            unexpected, [],
            "build_state gained code beyond the one authorised declared-decision "
            f"insertion; PR-1 must stay named rather than fixed: {unexpected}")

    def test_site_2b_build_state_still_consults_no_adapter(self) -> None:
        """PR-1, asserted as the property rather than only as a byte pattern.

        `build_standalone_state` reconciles the standalone path's declaration against the
        LIVE adapter on purpose; `build_state` must not, for any adapter, because doing so
        would change routing for existing Orca runs and for historical replay.
        """
        import ast
        import inspect

        from scripts.deterministic_workflow import launcher
        tree = ast.parse(inspect.getsource(launcher.build_state).lstrip())
        names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
        attrs = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
        self.assertNotIn("adapter", names,
                         "build_state names an adapter; PR-1 would then be fixed here "
                         "rather than named, for the Orca and fake paths too")
        self.assertNotIn("capabilities", attrs,
                         "build_state calls `.capabilities()`; the Orca and fake paths "
                         "must keep reading the operator's declaration")
        source = inspect.getsource(launcher.build_state)
        self.assertIn('spec.get("capabilities")', source,
                      "build_state no longer reads the operator's capability declaration")

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

    def test_site_4_resume_composes_standalone_and_refuses_a_foreign_selection(self) -> None:
        """Site 4 (the `resume` verb) is neither a fall-through NOR a refusal any more.

        Follow-up review finding 2: `--adapter standalone` re-enters a paused run with
        the composition the Watchdog already recovers with (`standalone_recovery_composition`
        -- recorded profile, recorded ledger, recorded approval authority), and selecting
        any OTHER adapter on a standalone-launched run is refused by name
        (`STANDALONE_RUN_ADAPTER_MISMATCH`) rather than composed.  The old refusal
        constant survives for its documentation history only and is composed at no site.
        """
        from scripts.deterministic_workflow import launcher
        source = inspect.getsource(launcher.run_pause_cli)
        self.assertIn("standalone_recovery_composition(", source,
                      "the resume verb no longer composes the standalone runtime")
        self.assertIn("refuse_foreign_composition(", source,
                      "the resume verb no longer refuses a foreign adapter selection")
        self.assertNotIn(launcher.STANDALONE_ADAPTER_UNSUPPORTED_HERE, source,
                         "the resume verb still refuses --adapter standalone by name")

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
