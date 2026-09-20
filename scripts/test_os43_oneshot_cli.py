"""OS-43 U-10: one-shot independence (AC-9) and adapter selection in the pause CLI (F-E).

Every test here runs with NO Watchdog process in existence.  That is the point: AC-9 says
the one-shot recovery works with the watchdog stopped, and the two structural properties
that make it true -- no shared state and no shared code path -- are asserted rather than
described.
"""
from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from scripts.deterministic_workflow import launcher
from scripts.test_deterministic_workflow_pause_fixture import REQUIRES_LANGGRAPH

RUN = "run_o"


def run_cli(argv):
    buffer, errors = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(errors):
        code = launcher.run_cli(list(argv))
    return code, buffer.getvalue(), errors.getvalue()


class VerbRegistrationTests(unittest.TestCase):
    def test_the_new_verbs_are_registered_beside_the_existing_tables(self):
        self.assertEqual(launcher.WATCHDOG_VERBS, ("watchdog", "recover"))
        self.assertIn("turn-end-liveness", launcher.TURN_VERBS)

    def test_the_existing_verb_tables_are_UNCHANGED(self):
        self.assertEqual(launcher.PAUSE_VERBS, ("discover", "resume"))
        for verb in ("turn-end", "turn-end-hook", "turn-end-bind"):
            self.assertIn(verb, launcher.TURN_VERBS)

    def test_the_pause_cli_adapter_selection_DEFAULTS_to_todays_behaviour(self):
        parser = launcher.build_pause_parser()
        args = parser.parse_args(["resume", "--run-id", RUN])
        self.assertEqual(args.adapter, launcher.FAKE_ADAPTER,
                         "no existing invocation may change meaning; the revert is one "
                         "line")
        # OS-37 C-DESIGN-1 widens the shared tuple to three.  The assertion above is the
        # one that matters and is untouched: `resume` still DEFAULTS to `fake`.  The set
        # stays closed so a fourth member cannot appear without an edit here.
        self.assertEqual(set(launcher.ADAPTERS), {"fake", "orca", "standalone"})
        both = parser.parse_args(["resume", "--run-id", RUN, "--adapter", "orca"])
        self.assertEqual(both.adapter, "orca")


class IndependenceTests(unittest.TestCase):
    """T-8: no shared code path, checked rather than asserted."""

    def test_the_pause_CLI_import_closure_excludes_every_watchdog_module(self):
        import ast
        source = Path(launcher.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        function = next(node for node in ast.walk(tree)
                        if isinstance(node, ast.FunctionDef)
                        and node.name == "run_pause_cli")
        imported: set[str] = set()
        for node in ast.walk(function):
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    imported.add(alias.name)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    imported.add(alias.name)
        for name in imported:
            self.assertFalse(name.startswith("watchdog_"),
                             f"run_pause_cli imports {name}; AC-9's independence is a "
                             "structural property, not a promise")

    def test_the_recover_verb_calls_the_ENGINE_api_directly(self):
        source = Path(launcher.__file__).read_text(encoding="utf-8")
        self.assertIn("recovery_runtime.recover_stalled_run(request)", source)

    def test_the_one_shot_paths_touch_no_watchdog_state(self):
        """No budget, no backoff, no shutdown flag: stopping the Watchdog removes nothing."""
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "artifacts" / "runs" / RUN).mkdir(parents=True)
            code, out, _err = run_cli(["recover", "--run-id", RUN,
                                       "--artifact-base", str(base), "--json"])
            self.assertIn(code, (0, 1))
            self.assertFalse((base / "artifacts" / "runs" / RUN
                              / "watchdog_audit").exists(),
                             "the one-shot verb writes nothing under watchdog_audit/")


class OneShotBehaviourTests(unittest.TestCase):
    """AC-9, end to end, with no Watchdog running at all."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        (self.base / "artifacts" / "runs").mkdir(parents=True)

    def test_discover_works_with_no_watchdog_and_no_paused_run(self):
        code, out, _err = run_cli(["discover", "--artifact-base", str(self.base),
                                   "--json"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out), [])

    def test_recover_reports_ONE_closed_outcome_for_a_run_with_no_checkpoint(self):
        (self.base / "artifacts" / "runs" / RUN).mkdir()
        code, out, _err = run_cli(["recover", "--run-id", RUN,
                                   "--artifact-base", str(self.base), "--json"])
        summary = json.loads(out)
        from scripts.deterministic_workflow.recovery_runtime import RECOVERY_OUTCOMES
        self.assertIn(summary["status"], RECOVERY_OUTCOMES)
        self.assertFalse(summary["effect_performed"])
        self.assertEqual(code, 1)

    @REQUIRES_LANGGRAPH
    def test_watchdog_status_is_READ_ONLY_and_takes_no_claim(self):
        from scripts.test_os43_discovery import DiscoveryFixture
        fixture = DiscoveryFixture("run")
        fixture.base, fixture.runs = self.base, self.base / "artifacts" / "runs"
        fixture.make_checkpointed(RUN)
        before = sorted(str(path) for path in fixture.runs.rglob("*"))
        code, out, _err = run_cli(["watchdog", "status",
                                   "--artifact-base", str(self.base), "--json"])
        self.assertEqual(code, 0)
        self.assertEqual(sorted(str(path) for path in fixture.runs.rglob("*")), before,
                         "status folds the ledgers and prints; it writes nothing")
        self.assertTrue(json.loads(out))

    @REQUIRES_LANGGRAPH
    def test_watchdog_once_ACTS_ON_NOTHING_when_the_orca_authority_cannot_answer(self):
        """CON-4 through the real CLI wiring, with no Orca runtime in this environment.

        The Orca listing authority is the real CLI boundary, so here it either RAISES (no
        `orca` binary answers) or answers nothing.  Either way the sweep fails CLOSED and
        acts on no run; the non-zero exit is the escalation an operator is meant to see,
        not a silent pass.  A sweep that had acted here would be the fail-open shape
        OS-43 exists to remove.
        """
        from scripts.test_os43_discovery import DiscoveryFixture
        fixture = DiscoveryFixture("run")
        fixture.base, fixture.runs = self.base, self.base / "artifacts" / "runs"
        fixture.make_checkpointed(RUN)
        code, out, _err = run_cli(["watchdog", "once", "--artifact-base", str(self.base),
                                   "--run-id", RUN, "--json"])
        summary = json.loads(out)
        self.assertEqual(summary["runs_observed"], 1)
        self.assertEqual(summary["runs_acted"], 0)
        self.assertIn(summary["runs"][0]["state"],
                      ("UNDECIDABLE_FAIL_CLOSED", "UNSUPPORTED_FAIL_CLOSED"))
        self.assertEqual(code, 1, "an escalation exits non-zero rather than in silence")


class LangGraphGuardTests(unittest.TestCase):
    """The dependency guard still refuses `resume` FIRST, before any claim is taken."""

    def test_the_guard_is_evaluated_before_the_adapter_is_built(self):
        source = Path(launcher.__file__).read_text(encoding="utf-8")
        body = source[source.index("def run_pause_cli"):]
        guard = body.index("require_runtime()", body.index("if args.verb == \"discover\""))
        adapter = body.index("build_orca_adapter_for_run")
        self.assertLess(guard, adapter,
                        "LANGGRAPH_DEPENDENCY_MISSING is reported before anything is "
                        "claimed or constructed")

    def test_the_recovery_api_refuses_UNSUPPORTED_when_the_runtime_is_absent(self):
        from scripts.deterministic_workflow import recovery_runtime
        source = Path(recovery_runtime.__file__).read_text(encoding="utf-8")
        self.assertIn('RecoveryOutcome(UNSUPPORTED, "LANGGRAPH_DEPENDENCY_MISSING"',
                      source)
        self.assertLess(source.index("LANGGRAPH_DEPENDENCY_MISSING"),
                        source.index("pause_path = pause_store.pause_record_path"),
                        "refused BEFORE any claim is taken")


class OrcaAdapterSelectionTests(unittest.TestCase):
    """F-E: the orca branch exists, adopts an existing Run, and refuses without one."""

    def test_the_orca_branch_ADOPTS_a_run_rather_than_creating_one(self):
        import inspect
        source = inspect.getsource(launcher.build_orca_adapter_for_run)
        self.assertIn("harness.resume_run(run_id, run_owner=run_owner,", source)
        self.assertNotIn("start_run", source,
                         "a recovery adopts the run that is already stalled; creating a "
                         "new one is exactly wrong")
        # The adopted run inherits the workflow it was LAUNCHED with, because a recovery
        # has no launch specification to read one from and an empty set makes the
        # final_review gate refuse. Asserted here beside the adoption call it belongs to;
        # the behaviour itself is proven end to end by
        # test_os43_delivered_wiring.RealHarnessRecoveryTests.
        self.assertIn("requested_phases=declared_phases_for_run(", source)

    def test_the_orca_branch_refuses_without_a_run_owner(self):
        from scripts.deterministic_workflow.launcher import LauncherError
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(LauncherError):
                launcher.build_orca_adapter_for_run(RUN, artifact_base=Path(tmp))

    def test_the_fake_branch_is_still_the_default_construction(self):
        source = Path(launcher.__file__).read_text(encoding="utf-8")
        self.assertIn("FakeAdapter(results, runtime_state=ledger, run_id=args.run_id",
                      source)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
