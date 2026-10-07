#!/usr/bin/env python3
"""OS-14 TRACK A: the normal workflow prepares, verifies and admits the Worker/Reviewer
pair itself, and the preparation is DURABLY recorded.

Every behaviour below is driven through PRODUCTION entry points -- the real
``launcher.build_orca_adapter`` / ``build_orca_adapter_for_run``, the real
``OrcaAdapter.start``, the real ``executor.execute_intent_node`` over a real
``FileRuntimeStateStore``, and the real ``launcher.execute_state``.  No test calls
``_prepare_pair``, ``_prepare_role``, ``_assert_preparation_binding``,
``_refuse_preparation``, ``create_fake_terminal``, ``verify_model_identity``,
``_recover``, ``_collect`` or ``_same_command_pair``.  In every INTEGRATION scenario -- any
test that drives a workflow through ``OrcaAdapter.start``, ``execute_intent_node`` or
``execute_state`` -- no test calls ``register_terminal`` or ``adopt_prepared_terminal``
either, and none pre-admits a pair, pre-claims a ledger record or pre-registers a terminal.
The two registration operations are named explicitly because the successor's terminal
ledger row for a prepared session has to be created by PRODUCTION adoption and by nothing
else, or the test would prove the fixture rather than the design.
``AdoptPreparedTerminalTests`` is the ONE group that calls ``adopt_prepared_terminal`` (and
``register_terminal``) directly: it is the isolated unit test OF that public operation, it
drives no workflow and it pre-admits nothing.

``T9`` is the only test group permitted to hand-write or delete a preparation entry or a
launch record -- that is its subject -- and ``T7(g)`` the only one permitted to delete a
launch record between a predecessor and its child.

REFERENCE DRIVER, NOT A PROVIDER.  Every green assertion here is evidence about the
WIRING: ``InProcessModelDriver``'s observation leg re-reads the value its own request leg
stored.  Nothing in this file is evidence about any real Claude or company model.

RESOURCE COUNTS.  Every count is stated for its OWN setup and asserted off that test's own
recorder, which sees only the commands that test issued.  No test enumerates, counts,
touches or closes any pre-existing residual session.
"""
from __future__ import annotations

import importlib.metadata
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import agent_profile
from scripts.deterministic_workflow import (executor, launcher, pause_policy,
                                            pause_store)
from scripts.deterministic_workflow.fake_adapter import InProcessModelDriver
from scripts.deterministic_workflow.orca_adapter import (
    PAIR_PREPARATION_REFUSAL_CODES, OrcaAdapter)
from scripts.deterministic_workflow.runtime_state import (DEFAULT_LEASE_SECONDS,
                                                          FileRuntimeStateStore,
                                                          ManualLeaseClock)
from scripts.orca_runtime_harness import (MODEL_EVIDENCE_NONE,
                                          MODEL_EVIDENCE_REQUESTED,
                                          MODEL_EVIDENCE_VERIFIED,
                                          SUPPORTED_ORCA_APP_VERSIONS,
                                          OrcaCommandRefused, OrcaRuntimeError,
                                          OrcaRuntimeHarness)

REPO_ROOT = Path(__file__).resolve().parent.parent


def _langgraph_ok() -> bool:
    try:
        import langgraph  # noqa: F401
        import langgraph.graph  # noqa: F401
    except ImportError:
        return False
    try:
        return importlib.metadata.version("langgraph") == "0.2.76"
    except importlib.metadata.PackageNotFoundError:
        return False


LANGGRAPH_REASON = "requires pinned langgraph 0.2.76"
REQUIRES_LANGGRAPH = unittest.skipUnless(_langgraph_ok(), LANGGRAPH_REASON)

COMPLETED_AT = "2026-01-01T00:00:00Z"
RUN_ID = "run_os14"
#: The one phase these scenarios run.  Deliberately NOT ``implementation``: the Markdown
#: settlement path carries no ``UNIT_TEST_STATUS`` field (``decision_contract
#: .parse_agent_settlement`` maps only ``STATUS`` and ``RESULT``), and ``routing
#: .phase_gate`` requires ``unit_test_status == "PASS"`` for IMPLEMENTATION / BUGFIX /
#: REFACTORING -- so an implementation-phase run could never reach its Reviewer turn
#: through a real agent body, and the Reviewer turn is exactly what the adoption path
#: needs.  Nothing about pair admission is phase-specific.
PHASE = "design"
PHASE_UPPER = "DESIGN"

#: The same shape as OS-49's ``SPLIT_PROFILE`` (``test_os49_delivery_barrier``): ONE
#: command for both roles, so the pair rule applies, and TWO DIFFERENT declared models, so
#: the two verifications must resolve to different values.
PAIR_PROFILE = (
    "version: 2\n"
    "profiles:\n"
    "  split:\n"
    "    phases:\n"
    f"      {PHASE}:\n"
    "        worker:\n"
    "          command: claude\n"
    "          model: glm-5.2\n"
    "        reviewer:\n"
    "          command: claude\n"
    "          model: glm-5.3-flash\n"
    "    final_review:\n"
    "      reviewer:\n"
    "        command: claude\n"
    "        model: glm-5.2\n"
)

#: Model-aware, but the two roles run DIFFERENT commands, so `pair_admission_required` is
#: False and no preparation happens.
DISTINCT_PROFILE = (
    "version: 2\n"
    "profiles:\n"
    "  split:\n"
    "    phases:\n"
    f"      {PHASE}:\n"
    "        worker:\n"
    "          command: claude\n"
    "          model: glm-5.2\n"
    "        reviewer:\n"
    "          command: codex\n"
    "          model: glm-5.3-flash\n"
    "    final_review:\n"
    "      reviewer:\n"
    "        command: claude\n"
    "        model: glm-5.2\n"
)

#: No model anywhere: the legacy / non-model path.  The two commands DIFFER because
#: Gate A's effective-identity rule refuses a pair of identical declared identities with
#: no model to distinguish them -- which is itself the pre-OS-14 behaviour being preserved.
LEGACY_PROFILE = (
    "version: 1\n"
    "profiles:\n"
    "  split:\n"
    "    phases:\n"
    f"      {PHASE}:\n"
    "        worker: claude\n"
    "        reviewer: codex\n"
    "    final_review:\n"
    "      reviewer: claude\n"
)


# ---- agent bodies --------------------------------------------------------------------
def _body(header: str, field_line: str, record: dict) -> str:
    return (f"# {header}\n\n{field_line}\nDECISION_GATE_STATE: CLEAR\n\n"
            "```decision-gate\n" + json.dumps(record) + "\n```\n")


def _worker_record(*, phase: str, iteration: int, run_id: str = RUN_ID) -> dict:
    return {"ledger_schema_version": 1, "boundary": "B2", "source": "worker",
            "role": "worker", "run": run_id, "phase": phase, "iteration": iteration,
            "responsible_phase": phase, "state": "CLEAR", "reason_code": None,
            "open_decision_item": False, "open_item": None, "assumption": None,
            "evidence": {}, "verdict": "",
            "source_binding": f"artifacts/runs/{run_id}/",
            "recorded_at": "2026-01-01T00:00:00+00:00",
            "prior_open_decision_items": []}


def _reviewer_record(*, phase: str, iteration: int, verdict: str = "PASS",
                     run_id: str = RUN_ID) -> dict:
    return {"ledger_schema_version": 1, "boundary": "B3", "source": "reviewer",
            "role": "reviewer", "run": run_id, "phase": phase, "iteration": iteration,
            "responsible_phase": phase, "state": "CLEAR", "reason_code": None,
            "open_decision_item": False, "open_item": None, "assumption": None,
            "evidence": {}, "verdict": verdict,
            "source_binding": f"artifacts/runs/{run_id}/",
            "recorded_at": "2026-01-01T00:00:00+00:00",
            "prior_open_decision_items": []}


def worker_body(*, phase: str = PHASE, iteration: int = 1,
                run_id: str = RUN_ID) -> str:
    return _body("Worker Result", "STATUS: COMPLETE",
                 _worker_record(phase=phase, iteration=iteration, run_id=run_id))


def reviewer_body(*, phase: str = PHASE, iteration: int = 1, verdict: str = "PASS",
                  run_id: str = RUN_ID) -> str:
    return _body("Review Result", f"RESULT: {verdict}",
                 _reviewer_record(phase=phase, iteration=iteration, verdict=verdict,
                                  run_id=run_id))


def final_body(*, iteration: int = 1, run_id: str = RUN_ID) -> str:
    return _body("Review Result", "RESULT: PASS",
                 _reviewer_record(phase="final_review", iteration=iteration,
                                  run_id=run_id))


def scripted_bodies(run_id: str = RUN_ID) -> list[str]:
    """The three bodies a single-phase run at HIGH risk settles with."""
    return [worker_body(run_id=run_id), reviewer_body(run_id=run_id),
            final_body(run_id=run_id)]


# ---- the process boundary ------------------------------------------------------------
class PairRecorder:
    """Deterministic stand-in for ``OrcaRuntimeHarness._exec_orca`` -- the harness's ONE
    process boundary.  ``harness.call()`` still runs for real, so JSON parsing, the
    ``self._raw`` lifecycle log and the ok/returncode check keep their production
    behaviour -- including the NEW typed ``OrcaCommandRefused``.

    It stands in for ``orca``, never for the harness: the terminal listing it answers
    ``terminal list`` with is the set of sessions IT handed out, which is what a real
    runtime would report, and the handles are the real ones, so
    ``pause_policy.terminal_digest(handle)`` genuinely matches a stored digest.
    """

    def __init__(self, bodies=(), *, run_id: str = RUN_ID,
                 create_failures: dict[int, tuple[int, str]] | None = None,
                 same_handle: bool = False,
                 listing: list[dict] | None = None,
                 list_failure: bool = False,
                 fail_verbs: set[str] | None = None,
                 worktree_id: str = "repo_os14::/project") -> None:
        self.bodies = list(bodies)
        self.commands: list[tuple[str, ...]] = []
        self.created: list[str] = []
        #: (handle, title) for PAIR creates only, in order.
        self.created_pairs: list[tuple[str, str]] = []
        self.terminals = 0
        self.dispatches = 0
        self.last_task_id = "task_0"
        #: create ordinal (1-based, counting only `terminal create`) -> (returncode, stdout)
        self.create_failures = dict(create_failures or {})
        self.same_handle = same_handle
        self.list_failure = list_failure
        #: Verbs the runtime answers with an UNPARSED body, so the failure is UNKNOWN
        #: rather than a refusal.
        self.fail_verbs = set(fail_verbs or ())
        #: What the world outside this process reports for `terminal list`.  Seeded by the
        #: caller for a successor process; otherwise grown by this recorder's own creates.
        self.listing: list[dict] = list(listing or [])
        self.worktree_id = worktree_id
        self.results: dict = {
            "status": {"runtime": {"state": "ready",
                                   "appVersion": SUPPORTED_ORCA_APP_VERSIONS[0],
                                   "runtimeId": "rt_os14"}},
            "current": {"worktree": {"id": worktree_id,
                                     "repoId": worktree_id.split("::")[0],
                                     "path": worktree_id.split("::")[-1]}},
            "show": {"worktree": {"id": worktree_id}},
            "run-create": {"run": {"id": run_id}},
            "wait": {"wait": {"satisfied": True}},
            "send": {}, "close": {}, "ack": {},
            "worker-retain": {"state": "retained"},
            "task-list": {"tasks": []},
            "list": {"terminals": list(self.listing)},
        }

    # -- public, non-mutating counters the tests assert on --------------------------
    @property
    def verbs(self) -> list[str]:
        return [c[1] if len(c) > 1 else c[0] for c in self.commands]

    def count(self, *verb: str) -> int:
        return sum(1 for c in self.commands if c[:len(verb)] == tuple(verb))

    @property
    def pair_titles(self) -> list[str]:
        return [c[c.index("--title") + 1] for c in self.commands
                if c[:2] == ("terminal", "create") and "--title" in c
                and "-pair-" in c[c.index("--title") + 1]]

    @property
    def pair_handles(self) -> list[str]:
        """The handles handed out for PAIR creates, in order."""
        return [handle for handle, _title in self.created_pairs]

    def next_body(self) -> str:
        if not self.bodies:
            raise AssertionError("the recorder ran out of scripted agent bodies")
        return self.bodies.pop(0)

    def __call__(self, args: tuple[str, ...]) -> tuple[int, str]:
        args = tuple(args)
        verb = args[1] if len(args) > 1 else args[0]
        if args[:2] == ("terminal", "create"):
            self.terminals += 1
            self.commands.append(args)
            failure = self.create_failures.get(self.terminals)
            if failure is not None:
                return failure
            handle = (self.created[0] if self.same_handle and self.created
                      else f"term_os14_{self.terminals}")
            self.created.append(handle)
            title = args[args.index("--title") + 1] if "--title" in args else ""
            if "-pair-" in title:
                self.created_pairs.append((handle, title))
            self.listing.append({"handle": handle, "title": title, "orphaned": False})
            self.results["list"] = {"terminals": list(self.listing)}
            return 0, json.dumps({"ok": True,
                                  "result": {"terminal": {"handle": handle}}})
        if args[:2] == ("terminal", "list") and self.list_failure:
            self.commands.append(args)
            return 1, "not json at all"
        if verb in self.fail_verbs:
            self.commands.append(args)
            return 1, "<html>gateway timeout</html>"
        if verb == "task-create":
            self.dispatches += 1
            self.last_task_id = f"task_os14_{self.dispatches}"
            self.results["task-create"] = {"task": {"id": self.last_task_id}}
            self.results["task-list"] = {
                "tasks": [{"id": self.last_task_id, "status": "completed"}]}
        elif verb == "worker-start":
            task_id = self.last_task_id
            dispatch_id = f"ctx_os14_{self.dispatches}"
            body = self.next_body()
            self.results["worker-start"] = {"dispatchId": dispatch_id, "state": "ready"}
            self.results["worker-show"] = {
                "dispatch": {"status": "completed", "completed_at": COMPLETED_AT},
                "worker": {"state": "settled"},
                "terminalResource": {"releaseState": "live",
                                     "processState": "running"}}
            self.results["dispatch-show"] = {
                "dispatch": {"id": dispatch_id, "status": "completed",
                             "completed_at": COMPLETED_AT}}
            self.results["worker-release"] = {"state": "released",
                                              "processAction": "none"}
            self.results["check"] = {
                "deliveryId": f"dlv_{dispatch_id}", "timedOut": False,
                "messages": [{"id": f"msg_{dispatch_id}", "type": "worker_done",
                              "payload": json.dumps({"taskId": task_id,
                                                     "dispatchId": dispatch_id,
                                                     "outcome": "succeeded"}),
                              "body": body}]}
        self.commands.append(args)
        return 0, json.dumps({"ok": True, "result": self.results.get(verb, {})})


REFUSED_CREATE = (1, json.dumps(
    {"ok": False, "error": {"code": "TERMINAL_CREATE_REFUSED",
                            "message": "the runtime refused this create"}}))
UNPARSED_CREATE = (1, "<html>gateway timeout</html>")


class Launch:
    """One launched (or adopted) Orca-adapter run, with every production object the
    assertions read.  Nothing here is a private harness map."""

    def __init__(self, *, adapter, state, recorder, driver, harness, ledger, project):
        self.adapter = adapter
        self.state = state
        self.recorder = recorder
        self.driver = driver
        self.harness = harness
        self.ledger = ledger
        self.project = project

    # -- public, non-mutating reads -------------------------------------------------
    def binding(self):
        return self.adapter.pair_binding.binding()

    def entry(self, role, *, phase=PHASE, iteration=1):
        return self.adapter.pair_preparation.entry(phase, iteration, role)

    def pairs(self):
        return self.adapter.pair_preparation.pairs()

    def run_root(self, run_id=RUN_ID):
        return self.project / "artifacts" / "runs" / run_id

    def prepared_intent(self, state=None):
        """The intent the ENGINE's own nodes prepare -- never hand-built."""
        current = dict(state if state is not None else self.state)
        current = executor.validate_node(current)
        current = executor.route_node(current)
        return executor.prepare_intent_node(current)


class ClaimAuditingStore(FileRuntimeStateStore):
    """The REAL durable store with ONE addition: it records what ``claim`` ANSWERED.

    It overrides no decision, changes no outcome and fabricates no record --
    ``super().claim`` does all of the work and its reply is returned unchanged.  The log
    exists because ``claim_outcome`` is deliberately NOT persisted, so it is the only
    honest way for a successor PROCESS to report whether its first claim was ``CREATED``
    (a fresh workflow over an empty ledger) or ``RESUMED`` (a takeover of the predecessor's
    own CLAIMED record) -- which is precisely the distinction T7 and T8 exist to make.

    It is an OBSERVER, never a fixture: it writes nothing of its own, and the records the
    parent asserts on afterwards are read back through the ordinary public
    ``get_receipt`` / ``get_settlement`` API of a plain ``FileRuntimeStateStore``.
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.claim_log: list[tuple[str, str]] = []

    def claim(self, intent):
        record = super().claim(intent)
        self.claim_log.append((intent["intent_id"], record.get("claim_outcome")))
        return record


class PairRoom(unittest.TestCase):
    """A project with an agent profile, agent-command shims and a temp artifact base."""

    profile_text = PAIR_PROFILE

    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.root = Path(self.temporary_directory.name)
        self.project = self.root / "project"
        (self.project / ".orca").mkdir(parents=True)
        (self.project / ".orca" / "agent-profiles.yaml").write_text(
            self.profile_text, encoding="utf-8")
        self.binaries = self.project / "bin"
        self.binaries.mkdir()
        for command in ("claude", "codex"):
            shim = self.binaries / command
            shim.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            shim.chmod(0o755)

    # -- production launch ----------------------------------------------------------
    def harness_factory(self, recorder, captured):
        def factory(artifact_base, **kwargs):
            with patch.dict(os.environ, {"ORCA_CLI_COMMAND": "/opt/orca-dev"}):
                harness = OrcaRuntimeHarness(Path(artifact_base), **kwargs)
            harness._exec_orca = recorder
            harness.preflight = lambda: {}      # the raw `orca skills get` boundary
            captured["harness"] = harness
            captured.setdefault("kwargs", []).append(dict(kwargs))
            return harness
        return factory

    def launch(self, *, recorder=None, driver="reference", risk="high",
               profile_name="split", phases=(PHASE_UPPER,), bodies=None,
               ledger_name="ledger.json", run_id=RUN_ID,
               **recorder_kwargs) -> Launch:
        """The REAL ``launcher.build_orca_adapter``, with only the process boundary and
        the model driver substituted."""
        recorder = recorder if recorder is not None else PairRecorder(
            bodies if bodies is not None else scripted_bodies(run_id),
            run_id=run_id, **recorder_kwargs)
        if driver == "reference":
            driver = InProcessModelDriver(resolve=lambda requested: requested)
        captured: dict = {}
        ledger = FileRuntimeStateStore(self.root / ledger_name)
        spec = {"thread_id": "os14", "phases": list(phases), "risk": risk}
        with patch.dict(os.environ, {"PATH": str(self.binaries)}):
            adapter, state = launcher.build_orca_adapter(
                spec, objective="OS-14 pair preparation",
                artifact_base=self.project, runtime_state=ledger,
                agent_profile_name=profile_name, project_root=self.project,
                harness_factory=self.harness_factory(recorder, captured),
                model_driver=driver)
        return Launch(adapter=adapter, state=state, recorder=recorder, driver=driver,
                      harness=captured["harness"], ledger=ledger, project=self.project)

    def adopt(self, *, recorder, driver=None, ledger, profile_name="",
              run_id=RUN_ID) -> OrcaAdapter:
        """The REAL ``launcher.build_orca_adapter_for_run`` -- the adoption door."""
        captured: dict = {}
        with patch.dict(os.environ, {"PATH": str(self.binaries)}):
            adapter = launcher.build_orca_adapter_for_run(
                run_id, artifact_base=self.project, runtime_state=ledger,
                run_owner="term_owner", project_root=self.project,
                harness_factory=self.harness_factory(recorder, captured),
                agent_profile_name=profile_name, model_driver=driver)
        adapter.captured = captured
        return adapter

    def execute(self, launch, *, state=None, adapter=None, ledger=None):
        """The REAL ``launcher.execute_state`` -- the boundary that PROJECTS a typed
        adapter refusal onto a BLOCKED terminal state."""
        with patch.dict(os.environ, {"PATH": str(self.binaries)}):
            return launcher.execute_state(
                dict(state if state is not None else launch.state),
                adapter=adapter if adapter is not None else launch.adapter,
                runtime_state=ledger if ledger is not None else launch.ledger,
                artifact_base=self.project)

    def node(self, launch, *, adapter=None, ledger=None):
        """The REAL ``executor.execute_intent_node`` -- the only thing that writes a
        CLAIMED runtime-state record and the only thing that runs the recovery ladder."""
        return executor.execute_intent_node(
            adapter if adapter is not None else launch.adapter,
            ledger if ledger is not None else launch.ledger)

    # -- assertions ----------------------------------------------------------------
    def assertBlocked(self, final, code):
        self.assertEqual(final.get("terminal_status"), "BLOCKED",
                         f"expected BLOCKED {code}, got {final.get('terminal_reason')}")
        self.assertEqual((final.get("terminal_reason") or {}).get("code"), code)
        self.assertEqual(launcher.summarize(final)["exit_code"],
                         launcher.EXIT_CODES["BLOCKED"])

    def assertNoEffects(self, recorder, *, creates=0, tasks=0, starts=0):
        self.assertEqual(recorder.count("terminal", "create"), creates)
        self.assertEqual(sum(1 for c in recorder.commands
                             if c[1:2] == ("task-create",)), tasks)
        self.assertEqual(sum(1 for c in recorder.commands
                             if c[1:2] == ("worker-start",)), starts)
        self.assertEqual(recorder.count("terminal", "send"), 0)


TWIN_MODULE_SOURCE = '''
"""A module-level, equally capable model-selection driver whose class ``__name__`` is
deliberately IDENTICAL to its sibling's.  The F-004 fixture: `type(...).__name__` cannot
tell the two apart and `driver_type_id` must."""


class ModelDriver:
    REQUEST_METHOD = "driver_select_and_verify"
    OBSERVATION_METHOD = "in_process_session_state"

    def select_and_verify(self, ticket):   # pragma: no cover - a Gate A fixture only
        raise AssertionError("this twin is a Gate A fixture and verifies nothing")
'''


class _TwinModules:
    """Two sibling modules written into a temp directory and put on ``sys.path``."""

    def __init__(self, directory: Path, test: unittest.TestCase) -> None:
        package = directory / "os14_twins"
        package.mkdir(parents=True, exist_ok=True)
        (package / "__init__.py").write_text("", encoding="utf-8")
        for name in ("alpha", "beta"):
            (package / f"{name}.py").write_text(TWIN_MODULE_SOURCE, encoding="utf-8")
        sys.path.insert(0, str(directory))
        test.addCleanup(self._forget, str(directory))
        import importlib
        self.alpha = importlib.import_module("os14_twins.alpha")
        self.beta = importlib.import_module("os14_twins.beta")

    @staticmethod
    def _forget(entry: str) -> None:
        if entry in sys.path:
            sys.path.remove(entry)
        for name in [key for key in sys.modules if key.startswith("os14_twins")]:
            sys.modules.pop(name, None)


# ======================================================================================
# I-0  `agent_profile.driver_type_id` / `resolve_driver_type`
# ======================================================================================
class DriverTypeIdentityTests(unittest.TestCase):
    """The DRIVER portion of the launch identity is a layout-normalised, ROUND-TRIP
    VERIFIED import path -- never `type(...).__name__`, which two classes can share."""

    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)

    def test_no_driver_is_a_positive_empty_statement(self) -> None:
        self.assertEqual(agent_profile.driver_type_id(None), "")

    def test_the_reference_driver_normalises_to_the_flat_layout_spelling(self) -> None:
        identifier = agent_profile.driver_type_id(InProcessModelDriver())
        self.assertEqual(identifier,
                         "deterministic_workflow.fake_adapter:InProcessModelDriver")
        self.assertIsNotNone(agent_profile.resolve_driver_type(identifier))

    def test_both_layout_spellings_of_one_class_bind_equal(self) -> None:
        """The repository layout and the installed FLAT layout must produce the SAME
        identifier, and the round trip must reach the class under EITHER spelling -- in
        this tree one source file is reachable under both module names at once."""
        import importlib
        sys.path.insert(0, str(REPO_ROOT / "scripts"))
        self.addCleanup(lambda: sys.path.remove(str(REPO_ROOT / "scripts"))
                        if str(REPO_ROOT / "scripts") in sys.path else None)
        flat = importlib.import_module("deterministic_workflow.fake_adapter")
        repo_driver = InProcessModelDriver()
        flat_driver = flat.InProcessModelDriver()
        self.assertIsNot(type(repo_driver), type(flat_driver),
                         "the two layouts really are two class objects over one file")
        self.assertEqual(agent_profile.driver_type_id(repo_driver),
                         agent_profile.driver_type_id(flat_driver))
        self.assertEqual(Path(type(repo_driver).__module__ and flat.__file__).resolve(),
                         (REPO_ROOT / "scripts" / "deterministic_workflow"
                          / "fake_adapter.py").resolve())

    def test_a_dynamically_created_class_is_refused_by_name(self) -> None:
        dynamic = type("ModelDriver", (), {"select_and_verify": lambda self, t: None})()
        with self.assertRaises(agent_profile.AgentProfileError) as caught:
            agent_profile.driver_type_id(dynamic)
        self.assertEqual(caught.exception.reason,
                         agent_profile.DRIVER_TYPE_UNIDENTIFIABLE)

    def test_a_function_local_class_is_refused_by_name(self) -> None:
        def make():
            class ModelDriver:
                def select_and_verify(self, ticket):   # pragma: no cover
                    return None
            return ModelDriver()
        with self.assertRaises(agent_profile.AgentProfileError) as caught:
            agent_profile.driver_type_id(make())
        self.assertEqual(caught.exception.reason,
                         agent_profile.DRIVER_TYPE_UNIDENTIFIABLE)

    def test_two_distinct_classes_with_one_name_get_distinct_identifiers(self) -> None:
        """The F-004 regression, at the derivation itself."""
        twins = _TwinModules(Path(self.temporary_directory.name), self)
        a, b = twins.alpha.ModelDriver(), twins.beta.ModelDriver()
        self.assertEqual(type(a).__name__, type(b).__name__)
        self.assertNotEqual(agent_profile.driver_type_id(a),
                            agent_profile.driver_type_id(b))
        self.assertIs(agent_profile.resolve_driver_type(
            agent_profile.driver_type_id(a)), type(a))
        self.assertIs(agent_profile.resolve_driver_type(
            agent_profile.driver_type_id(b)), type(b))

    def test_a_malformed_identifier_resolves_to_nothing(self) -> None:
        for identifier in ("", "no-colon", ":Driver", "module:"):
            self.assertIsNone(agent_profile.resolve_driver_type(identifier))


# ======================================================================================
# I-1"  `OrcaRuntimeHarness.routing_binding()`
# ======================================================================================
class RoutingBindingTests(PairRoom):
    """ONE derivation, read by the recorder and by the checker."""

    def _routing(self, text, *, driver=None, risk="high"):
        (self.project / ".orca" / "agent-profiles.yaml").write_text(text,
                                                                    encoding="utf-8")
        with patch.dict(os.environ, {"PATH": str(self.binaries)}):
            return launcher.orca_run_routing(
                agent_profile_name="split", requested_phases=(PHASE,), risk=risk,
                project_root=self.project, model_driver=driver)

    def _harness(self, routing, driver):
        with patch.dict(os.environ, {"ORCA_CLI_COMMAND": "/opt/orca-dev"}):
            return OrcaRuntimeHarness(self.project, agent_routing=routing,
                                      model_driver=driver)

    def test_the_emitted_key_set_is_exactly_the_launch_identity_tuple(self) -> None:
        driver = InProcessModelDriver()
        harness = self._harness(self._routing(PAIR_PROFILE, driver=driver), driver)
        self.assertEqual(set(harness.routing_binding()),
                         set(pause_store.PAIR_LAUNCH_IDENTITY_KEYS))

    def test_a_legacy_routing_states_false_with_empty_digest_and_driver(self) -> None:
        harness = self._harness(self._routing(LEGACY_PROFILE), None)
        binding = harness.routing_binding()
        self.assertEqual(binding["model_aware"], "false")
        self.assertEqual(binding["routing_digest"], "")
        self.assertEqual(binding["driver_type_id"], "")
        self.assertEqual(binding["profile_name"], "split")

    def test_no_routing_at_all_states_false(self) -> None:
        harness = self._harness(None, None)
        self.assertEqual(harness.routing_binding()["model_aware"], "false")
        self.assertEqual(harness.routing_binding()["runtime"], "")

    def test_two_equal_routings_digest_equal_and_a_changed_cell_changes_it(self) -> None:
        driver = InProcessModelDriver()
        first = self._harness(self._routing(PAIR_PROFILE, driver=driver), driver)
        second = self._harness(self._routing(PAIR_PROFILE, driver=driver), driver)
        self.assertEqual(first.routing_binding()["routing_digest"],
                         second.routing_binding()["routing_digest"])
        changed = self._harness(self._routing(DISTINCT_PROFILE, driver=driver), driver)
        self.assertNotEqual(first.routing_binding()["routing_digest"],
                            changed.routing_binding()["routing_digest"])

    def test_risk_is_part_of_the_digest_basis(self) -> None:
        """`required_roles` marks a phase Reviewer required only for medium/high, that
        flag lands on every `RoleRouting.required`, and the digest covers it."""
        driver = InProcessModelDriver()
        high = self._harness(self._routing(PAIR_PROFILE, driver=driver, risk="high"),
                             driver)
        low = self._harness(self._routing(PAIR_PROFILE, driver=driver, risk="low"),
                            driver)
        self.assertNotEqual(high.routing_binding()["routing_digest"],
                            low.routing_binding()["routing_digest"])

    def test_the_driver_cell_is_the_type_id_not_the_short_name(self) -> None:
        """The F-004 regression, at the RECORDER: two classes whose `__name__` is equal
        produce DIFFERENT cells while `type(...).__name__` compares equal."""
        twins = _TwinModules(self.root / "twins", self)
        a, b = twins.alpha.ModelDriver(), twins.beta.ModelDriver()
        self.assertEqual(type(a).__name__, type(b).__name__)
        routing_a = self._routing(PAIR_PROFILE, driver=a)
        first = self._harness(routing_a, a).routing_binding()
        second = self._harness(self._routing(PAIR_PROFILE, driver=b), b).routing_binding()
        self.assertEqual(first["routing_digest"], second["routing_digest"],
                         "the routing is identical; only the driver class differs")
        self.assertNotEqual(first["driver_type_id"], second["driver_type_id"])
        self.assertNotEqual(first["driver_type_id"], type(a).__name__)

    def test_an_unidentifiable_driver_refuses_rather_than_falling_back(self) -> None:
        dynamic = type("ModelDriver", (), {"select_and_verify": lambda self, t: None})()
        harness = self._harness(self._routing(PAIR_PROFILE, driver=InProcessModelDriver()),
                                dynamic)
        with self.assertRaises(agent_profile.AgentProfileError) as caught:
            harness.routing_binding()
        self.assertEqual(caught.exception.reason,
                         agent_profile.DRIVER_TYPE_UNIDENTIFIABLE)


# ======================================================================================
# I-1"'  `OrcaRuntimeHarness.adopt_prepared_terminal()`
# ======================================================================================
class AdoptPreparedTerminalTests(PairRoom):
    """Registers PROVENANCE and NO verification authority.

    This group is the one place the operation is exercised in isolation, as the step that
    makes a digest-proved handle USABLE in a fresh process.  Every other group reaches it
    only through production `_prepare_role`.
    """

    MODEL_MAPS = ("_model_identity", "_model_session_identity", "_model_role_history",
                  "_model_session_history")

    def _harness(self):
        with patch.dict(os.environ, {"PATH": str(self.binaries)}):
            routing = launcher.orca_run_routing(
                agent_profile_name="split", requested_phases=(PHASE,), risk="high",
                project_root=self.project, model_driver=InProcessModelDriver())
        with patch.dict(os.environ, {"ORCA_CLI_COMMAND": "/opt/orca-dev"}):
            return OrcaRuntimeHarness(self.project, agent_routing=routing,
                                      model_driver=InProcessModelDriver())

    def test_an_unseen_handle_is_recorded_adopted_with_no_resolved_model(self) -> None:
        harness = self._harness()
        harness.adopt_prepared_terminal("term_pre", "worker", phase=PHASE)
        row = harness.ledger_terminal("term_pre")
        self.assertEqual(row["role"], "active_worker")
        self.assertEqual(row["origin"], "adopted")
        self.assertEqual(row["intended_role"], "phase_worker")
        self.assertEqual(row["agent_command"], "claude")
        self.assertEqual(row["requested_model"], "glm-5.2")
        self.assertEqual(row["model_state"], MODEL_EVIDENCE_REQUESTED)
        self.assertEqual(row["resolved_model"], "",
                         "a resolved model is written by GATE B only, never at adoption")

    def test_a_reviewer_handle_takes_the_reviewer_intended_role_and_model(self) -> None:
        harness = self._harness()
        harness.adopt_prepared_terminal("term_pre_r", "reviewer", phase=PHASE)
        row = harness.ledger_terminal("term_pre_r")
        self.assertEqual(row["intended_role"], "phase_reviewer")
        self.assertEqual(row["requested_model"], "glm-5.3-flash")

    def test_no_declared_model_leaves_the_evidence_state_none(self) -> None:
        with patch.dict(os.environ, {"ORCA_CLI_COMMAND": "/opt/orca-dev"}):
            harness = OrcaRuntimeHarness(self.project)
        harness.adopt_prepared_terminal("term_plain", "worker")
        row = harness.ledger_terminal("term_plain")
        self.assertEqual(row["requested_model"], "")
        self.assertEqual(row["model_state"], MODEL_EVIDENCE_NONE)

    def test_none_of_the_four_model_maps_is_touched(self) -> None:
        harness = self._harness()
        before = {name: dict(getattr(harness, name)) for name in self.MODEL_MAPS}
        harness.adopt_prepared_terminal("term_pre", "worker", phase=PHASE)
        for name in self.MODEL_MAPS:
            self.assertEqual(dict(getattr(harness, name)), before[name], name)

    def test_adoption_issues_no_orca_command(self) -> None:
        harness = self._harness()
        recorder = PairRecorder()
        harness._exec_orca = recorder
        harness.adopt_prepared_terminal("term_pre", "worker", phase=PHASE)
        self.assertEqual(recorder.commands, [])

    def test_a_self_created_session_keeps_its_own_origin(self) -> None:
        """Provenance-preserving on a SAME-PROCESS re-entry."""
        harness = self._harness()
        harness.register_terminal("term_mine", role="active_worker",
                                  origin="self_created")
        harness.adopt_prepared_terminal("term_mine", "worker", phase=PHASE)
        self.assertEqual(harness.ledger_terminal("term_mine")["origin"], "self_created")

    def test_adoption_widens_no_cleanup_authority(self) -> None:
        harness = self._harness()
        harness.adopt_prepared_terminal("term_pre", "worker", phase=PHASE)
        self.assertNotEqual(harness.ledger_terminal("term_pre")["cleanup_authority"],
                            "authorized")


# ======================================================================================
# I-2  the two durable documents, and the pure resolver
# ======================================================================================
class PairStoreTests(unittest.TestCase):
    """The stores' own closed contracts, before any adapter wiring exists."""

    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.base = Path(self.temporary_directory.name)
        self.binding = pause_store.pair_binding_for(RUN_ID, artifact_base=self.base)
        self.prep = pause_store.pair_preparation_for(RUN_ID, artifact_base=self.base)

    MODEL_AWARE = {"runtime": "orchestration", "profile_name": "split",
                   "profile_source": "project_local", "routing_schema_version": "2",
                   "routing_digest": "d" * 64, "model_aware": "true",
                   "driver_type_id": "pkg.mod:Driver"}
    LEGACY = {"runtime": "orchestration", "profile_name": "split",
              "profile_source": "project_local", "routing_schema_version": "1",
              "routing_digest": "", "model_aware": "false", "driver_type_id": ""}

    # -- the timestamp helper (review F-002) ---------------------------------------
    def test_iso_now_is_a_string_in_the_one_documented_format(self) -> None:
        from datetime import datetime
        value = pause_store._iso_now()
        self.assertIs(type(value), str)
        self.assertEqual(datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").strftime(
            "%Y-%m-%dT%H:%M:%SZ"), value)

    def test_the_lease_clock_helper_is_untouched_and_still_returns_a_float(self) -> None:
        store = pause_store.store_for(RUN_ID, artifact_base=self.base)
        self.assertIs(type(store._now()), float)
        self.assertFalse(hasattr(pause_store, "_now"),
                         "a module-level `_now` would re-create the F-002 name clash")

    # -- the launch record ----------------------------------------------------------
    def test_an_absent_launch_record_is_none_and_no_verdict(self) -> None:
        self.assertIsNone(self.binding.binding())

    def test_record_binding_is_create_once_and_idempotent(self) -> None:
        first = self.binding.record_binding(**self.MODEL_AWARE)
        second = self.binding.record_binding(**self.MODEL_AWARE)
        self.assertEqual(first["recorded_at"], second["recorded_at"],
                         "the FIRST record's provenance stands")
        self.assertEqual(set(first), set(pause_store.PAIR_BINDING_KEYS))

    def test_a_differing_identity_cell_is_refused(self) -> None:
        self.binding.record_binding(**self.MODEL_AWARE)
        with self.assertRaises(pause_store.PairPreparationCorrupt):
            self.binding.record_binding(**{**self.MODEL_AWARE,
                                           "routing_digest": "e" * 64})

    def test_a_legacy_statement_carrying_a_digest_is_unforgeable(self) -> None:
        with self.assertRaises(pause_store.PairPreparationCorrupt):
            pause_store.new_pair_binding(run_id=RUN_ID, recorded_by="o",
                                         recorded_at="t",
                                         **{**self.LEGACY, "routing_digest": "x" * 64})
        with self.assertRaises(pause_store.PairPreparationCorrupt):
            pause_store.new_pair_binding(run_id=RUN_ID, recorded_by="o",
                                         recorded_at="t",
                                         **{**self.LEGACY,
                                            "driver_type_id": "pkg.mod:Driver"})

    def test_a_model_aware_statement_without_a_digest_is_refused(self) -> None:
        with self.assertRaises(pause_store.PairPreparationCorrupt):
            pause_store.new_pair_binding(run_id=RUN_ID, recorded_by="o",
                                         recorded_at="t",
                                         **{**self.MODEL_AWARE, "routing_digest": ""})

    def test_model_aware_is_a_string_and_a_bool_is_refused(self) -> None:
        with self.assertRaises(pause_store.PairPreparationCorrupt):
            pause_store.new_pair_binding(run_id=RUN_ID, recorded_by="o",
                                         recorded_at="t",
                                         **{**self.MODEL_AWARE, "model_aware": True})

    def test_an_unknown_launch_record_schema_version_blocks_rather_than_reads(self) -> None:
        path = pause_store.pair_binding_path(RUN_ID, artifact_base=self.base)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"schema_version": "os14.pair_launch_binding.v0",
                                    "binding": {}}), encoding="utf-8")
        with self.assertRaises(pause_store.PairPreparationCorrupt) as caught:
            self.binding.binding()
        self.assertIn("INCOMPATIBLE_DURABLE_STORE", str(caught.exception))

    def test_a_foreign_run_id_in_the_record_is_refused(self) -> None:
        other = pause_store.pair_binding_for("run_other", artifact_base=self.base)
        other.record_binding(**self.MODEL_AWARE)
        path = pause_store.pair_binding_path("run_other", artifact_base=self.base)
        mine = pause_store.FilePairBindingStore(path, run_id=RUN_ID)
        with self.assertRaises(pause_store.PairPreparationCorrupt):
            mine.binding()

    def test_the_launch_identity_tuple_excludes_provenance_and_run_id(self) -> None:
        self.assertNotIn("run_id", pause_store.PAIR_LAUNCH_IDENTITY_KEYS)
        self.assertNotIn("recorded_by", pause_store.PAIR_LAUNCH_IDENTITY_KEYS)
        self.assertNotIn("recorded_at", pause_store.PAIR_LAUNCH_IDENTITY_KEYS)
        self.assertEqual(
            set(pause_store.PAIR_LAUNCH_IDENTITY_KEYS) | {"run_id"},
            set(pause_store.PAIR_BINDING_IDENTITY_KEYS))

    # -- the preparation entries ----------------------------------------------------
    def test_a_first_record_fills_every_closed_key(self) -> None:
        entry = self.prep.record(PHASE, 1, "worker", stage="CREATE_INTENDED",
                                 terminal_title="t", terminal_worktree="id:r::/p")
        self.assertEqual(set(entry), set(pause_store.PAIR_ENTRY_KEYS))
        self.assertEqual(entry["create_attempt"], "1")
        self.assertEqual(entry["gate_iteration"], "1")

    def test_promotion_is_monotonic(self) -> None:
        self.prep.record(PHASE, 1, "worker", stage="CREATE_INTENDED")
        self.prep.record(PHASE, 1, "worker", stage="CREATED", terminal_digest="d")
        with self.assertRaises(pause_store.PairPreparationCorrupt) as caught:
            self.prep.record(PHASE, 1, "worker", stage="CREATE_INTENDED")
        self.assertIn("non-monotonic promotion", str(caught.exception))
        self.assertEqual(self.prep.entry(PHASE, 1, "worker")["stage"], "CREATED")

    def test_refused_and_created_are_never_promoted_into_each_other(self) -> None:
        self.prep.record(PHASE, 1, "worker", stage="CREATE_INTENDED")
        self.prep.record(PHASE, 1, "worker", stage="CREATE_REFUSED",
                         refusal_error_code="X")
        with self.assertRaises(pause_store.PairPreparationCorrupt) as caught:
            self.prep.record(PHASE, 1, "worker", stage="CREATED", terminal_digest="d")
        self.assertIn("ALTERNATIVE successors", str(caught.exception))
        on_disk = self.prep.entry(PHASE, 1, "worker")
        self.assertEqual(on_disk["stage"], "CREATE_REFUSED")
        self.assertEqual(on_disk["terminal_digest"], "")

    def test_a_new_attempt_is_published_as_create_intended_and_clears_the_last_one(self):
        self.prep.record(PHASE, 1, "worker", stage="CREATE_INTENDED")
        self.prep.record(PHASE, 1, "worker", stage="CREATE_REFUSED",
                         refusal_error_code="X", refusal_receipt_digest="d")
        with self.assertRaises(pause_store.PairPreparationCorrupt):
            self.prep.record(PHASE, 1, "worker", stage="CREATED", create_attempt="2",
                             terminal_digest="d")
        retry = self.prep.record(PHASE, 1, "worker", stage="CREATE_INTENDED",
                                 create_attempt="2", terminal_title="t2")
        self.assertEqual(retry["create_attempt"], "2")
        self.assertEqual(retry["refusal_error_code"], "")
        self.assertEqual(retry["refusal_receipt_digest"], "")

    def test_re_recording_verified_is_rank_equal_and_refreshes(self) -> None:
        for stage in ("CREATE_INTENDED", "CREATED", "VERIFIED"):
            self.prep.record(PHASE, 1, "worker", stage=stage, terminal_digest="d")
        refreshed = self.prep.record(PHASE, 1, "worker", stage="VERIFIED",
                                     resolved_model_observed="glm-5.2",
                                     verified_at="2026-01-01T00:00:01Z")
        self.assertEqual(refreshed["resolved_model_observed"], "glm-5.2")

    def test_an_unknown_stage_is_refused(self) -> None:
        with self.assertRaises(pause_store.PairPreparationCorrupt):
            self.prep.record(PHASE, 1, "worker", stage="LAUNCHED")

    def test_an_unknown_role_is_refused(self) -> None:
        with self.assertRaises(pause_store.PairPreparationCorrupt):
            self.prep.record(PHASE, 1, "auditor", stage="CREATE_INTENDED")

    def test_a_phase_carrying_the_reserved_separator_is_refused(self) -> None:
        with self.assertRaises(pause_store.PairPreparationCorrupt):
            self.prep.record("design#2", 1, "worker", stage="CREATE_INTENDED")

    def test_has_any_separates_a_lost_record_from_an_absent_one(self) -> None:
        self.assertFalse(self.prep.has_any())
        self.prep.record(PHASE, 1, "worker", stage="CREATE_INTENDED")
        self.assertTrue(self.prep.has_any())

    def test_an_entry_filed_under_a_disagreeing_key_is_refused(self) -> None:
        self.prep.record(PHASE, 1, "worker", stage="CREATE_INTENDED")
        path = pause_store.pair_preparation_path(RUN_ID, artifact_base=self.base)
        document = json.loads(path.read_text(encoding="utf-8"))
        slot = document["pairs"].pop(f"{PHASE}#1")
        document["pairs"]["other#9"] = slot
        path.write_text(json.dumps(document), encoding="utf-8")
        with self.assertRaises(pause_store.PairPreparationCorrupt) as caught:
            self.prep.pairs()
        self.assertIn("entry identity", str(caught.exception))

    def test_the_stage_vocabulary_touches_no_journal_stage(self) -> None:
        self.assertEqual(pause_store.PAIR_PREPARATION_STAGES,
                         ("CREATE_INTENDED", "CREATE_REFUSED", "CREATED", "VERIFIED"))
        self.assertEqual(pause_store.JOURNAL_STAGES,
                         ("PLANNED", "OPENED", "INTENDED", "ACCOUNTED", "DISPOSED"))
        self.assertFalse(set(pause_store.PAIR_PREPARATION_STAGES)
                         & set(pause_store.JOURNAL_STAGES))


class PreparedResolverTests(unittest.TestCase):
    """The pure decision table, row by row.  No adapter, no harness, no I/O."""

    TITLE = f"{RUN_ID}-pair-{PHASE}-1-worker"

    def entry(self, **fields):
        base = {"run_id": RUN_ID, "phase": PHASE, "gate_iteration": "1",
                "role": "worker", "stage": "CREATED", "create_attempt": "1",
                "terminal_title": self.TITLE, "terminal_worktree": "id:r::/p",
                "terminal_digest": pause_policy.terminal_digest("term_a"),
                "requested_model": "glm-5.2", "resolved_model_observed": "",
                "observed_at_run": "", "refusal_command": "", "refusal_error_code": "",
                "refusal_receipt_digest": "", "recorded_by": "host:pid1",
                "create_intended_at": "t", "create_settled_at": "t", "verified_at": ""}
        base.update(fields)
        return base

    def resolve(self, entry, listing, **kwargs):
        return pause_policy.resolve_prepared_terminal(entry, listing, run_id=RUN_ID,
                                                      **kwargs)

    LISTED = [{"handle": "term_a", "title": TITLE, "orphaned": False}]

    def test_an_absent_entry_is_create_and_consults_no_listing(self) -> None:
        verdict = self.resolve(None, None)
        self.assertEqual(verdict["action"], "create")
        self.assertEqual(verdict["handle_recovery"], "not_attempted")

    def test_a_foreign_run_id_blocks_as_an_ownership_mismatch(self) -> None:
        verdict = self.resolve(self.entry(run_id="run_other"), self.LISTED)
        self.assertEqual(verdict["action"], "block")
        self.assertEqual(verdict["code"], "PAIR_PREPARATION_OWNERSHIP_MISMATCH")

    def test_a_differing_recorded_by_alone_does_not_block(self) -> None:
        verdict = self.resolve(self.entry(recorded_by="other_host:pid99"), self.LISTED)
        self.assertEqual(verdict["action"], "adopt")

    def test_a_parsed_refusal_is_confirmed_absence_and_permits_a_retry(self) -> None:
        verdict = self.resolve(self.entry(stage="CREATE_REFUSED", terminal_digest=""),
                               None)
        self.assertEqual(verdict["action"], "create")
        self.assertTrue(verdict["confirmed_absent"])

    def test_create_intended_blocks_as_unknown_whatever_the_listing_says(self) -> None:
        for listing in (None, [], self.LISTED):
            verdict = self.resolve(
                self.entry(stage="CREATE_INTENDED", terminal_digest=""), listing)
            self.assertEqual(verdict["action"], "block")
            self.assertEqual(verdict["code"], "PAIR_PREPARATION_OUTCOME_UNKNOWN")
            self.assertFalse(verdict["confirmed_absent"])

    def test_a_digest_proved_session_is_adopted(self) -> None:
        verdict = self.resolve(self.entry(), self.LISTED)
        self.assertEqual(verdict["action"], "adopt")
        self.assertEqual(verdict["handle"], "term_a")
        self.assertEqual(verdict["handle_recovery"], "listing_verified")

    def test_a_title_match_the_digest_contradicts_is_unverified(self) -> None:
        verdict = self.resolve(self.entry(),
                               [{"handle": "term_other", "title": self.TITLE}])
        self.assertEqual(verdict["code"], "PAIR_PREPARATION_SESSION_UNVERIFIED")
        self.assertIsNone(verdict["handle"])

    def test_a_provable_absence_blocks_rather_than_re_creating(self) -> None:
        verdict = self.resolve(self.entry(), [])
        self.assertEqual(verdict["code"], "PAIR_PREPARATION_SESSION_ABSENT")

    def test_an_unresolved_scope_is_unknown_not_empty(self) -> None:
        verdict = self.resolve(self.entry(), [], scope_resolved=False)
        self.assertEqual(verdict["code"], "PAIR_PREPARATION_SCOPE_UNRESOLVED")

    def test_an_unreadable_listing_is_unknown_not_empty(self) -> None:
        verdict = self.resolve(self.entry(), None)
        self.assertEqual(verdict["code"], "PAIR_PREPARATION_SCOPE_UNRESOLVED")

    def test_two_digest_matches_are_an_anomaly_and_never_a_guess(self) -> None:
        verdict = self.resolve(self.entry(),
                               [{"handle": "term_a", "title": self.TITLE},
                                {"handle": "term_a", "title": self.TITLE}])
        self.assertEqual(verdict["code"], "PAIR_PREPARATION_SESSION_UNVERIFIED")

    def test_every_block_code_is_in_the_adapters_closed_set(self) -> None:
        for code in pause_policy._PREPARED_BLOCK_CODES.values():
            self.assertIn(code, PAIR_PREPARATION_REFUSAL_CODES)

    def test_the_shipped_handle_recovery_vocabulary_gained_no_member(self) -> None:
        self.assertEqual(pause_policy.HANDLE_RECOVERY_OUTCOMES,
                         ("in_process", "listing_verified", "listing_candidate",
                          "not_listed", "unverified", "scope_unresolved",
                          "not_attempted"))


# ======================================================================================
# I-3'  `launcher.declared_run_identity_for_run` -- the durable risk derivation
# ======================================================================================
@REQUIRES_LANGGRAPH
class DeclaredRunIdentityTests(unittest.TestCase):
    """Phases AND risk, from ONE read of ONE committed head, or a NAMED refusal.

    Driven over a REAL committed checkpoint store: the head is written by a real
    ``execute_state`` run, never hand-assembled, because the question is whether the
    launcher can read what the engine actually commits.
    """

    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.base = Path(self.temporary_directory.name)

    def _commit_head(self, run_id: str, risk: str) -> Path:
        from scripts.deterministic_workflow.fake_adapter import FakeAdapter
        worker = {"status": "COMPLETE", "unit_test_status": "NOT_APPLICABLE"}
        review = {"result": "PASS"}
        ledger = FileRuntimeStateStore(self.base / f"{run_id}.ledger.json")
        state = launcher.build_state({"run_id": run_id, "thread_id": "t",
                                      "phases": ["ANALYSIS"], "risk": risk})
        results = ([worker, review, review] if risk != "low" else [worker, review])
        final = launcher.execute_state(
            state, adapter=FakeAdapter(results, runtime_state=ledger),
            runtime_state=ledger, artifact_base=self.base)
        self.assertEqual(final["terminal_status"], "COMPLETED")
        return launcher.resolve_checkpoint_path(run_id, "t", artifact_base=self.base)

    def test_every_member_of_the_closed_risk_set_round_trips(self) -> None:
        from scripts.deterministic_workflow.contracts import RISKS
        for risk in RISKS:
            run_id = f"run_risk{risk}"
            self._commit_head(run_id, risk)
            phases, declared = launcher.declared_run_identity_for_run(
                run_id, artifact_base=self.base)
            self.assertEqual(declared, risk)
            self.assertEqual(phases, ("analysis",))

    def test_an_absent_head_is_refused_by_name_rather_than_defaulted(self) -> None:
        with self.assertRaises(launcher.LauncherError) as caught:
            launcher.declared_run_identity_for_run("run_nevercommitted",
                                                   artifact_base=self.base)
        self.assertIn(launcher.ORCA_RUN_DECLARATION_UNREADABLE, str(caught.exception))

    @staticmethod
    def corrupt_risk(path: Path, value: str) -> None:
        """Rewrite the committed ``risk`` channel to a value outside ``contracts.RISKS``.

        The channel blobs are msgpack-encoded, so the value is re-encoded as a msgpack
        fixstr rather than text-substituted: a replace over the JSON envelope would leave
        the real cell untouched and the test would pass for the wrong reason.
        """
        import base64
        document = json.loads(path.read_text(encoding="utf-8"))
        encoded = base64.b64encode(
            bytes([0xA0 | len(value)]) + value.encode("utf-8")).decode("ascii")
        for thread in document["threads"].values():
            for namespace in thread["namespaces"].values():
                for version in namespace["blobs"].get("risk", {}):
                    namespace["blobs"]["risk"][version] = {"type": "msgpack",
                                                           "payload_b64": encoded}
        path.write_text(json.dumps(document), encoding="utf-8")

    def test_a_corrupted_risk_cell_is_refused_by_name(self) -> None:
        path = self._commit_head("run_badrisk", "high")
        self.corrupt_risk(path, "catastrophic")
        with self.assertRaises(launcher.LauncherError) as caught:
            launcher.declared_run_identity_for_run("run_badrisk",
                                                   artifact_base=self.base)
        self.assertIn(launcher.ORCA_RUN_DECLARATION_UNREADABLE, str(caught.exception))

    def test_an_unreadable_head_is_refused_by_name(self) -> None:
        path = self._commit_head("run_unreadable", "high")
        path.write_text("{ not json", encoding="utf-8")
        with self.assertRaises(launcher.LauncherError) as caught:
            launcher.declared_run_identity_for_run("run_unreadable",
                                                   artifact_base=self.base)
        self.assertIn(launcher.ORCA_RUN_DECLARATION_UNREADABLE, str(caught.exception))

    def test_the_shipped_phases_accessor_still_answers_empty_for_the_same_store(self):
        """The OS-43 accessor is provably UNCHANGED: it answers `()` rather than raising,
        which every shipped recovery depends on."""
        path = self._commit_head("run_unreadable2", "high")
        path.write_text("{ not json", encoding="utf-8")
        self.assertEqual(
            launcher.declared_phases_for_run("run_unreadable2",
                                             artifact_base=self.base), ())
        self.assertEqual(
            launcher.declared_phases_for_run("run_nevercommitted2",
                                             artifact_base=self.base), ())


# ======================================================================================
# T1  M1: normal pair preparation and dependency-respecting delivery
# ======================================================================================
@REQUIRES_LANGGRAPH
class T1NormalPairPreparationTests(PairRoom):
    """The capability OS-14 exists for, through the REAL launcher and the REAL graph."""

    def setUp(self) -> None:
        super().setUp()
        self.launch_fixture = self.launch()
        self.final = self.execute(self.launch_fixture)

    def test_the_run_completes_through_the_production_path(self) -> None:
        self.assertEqual(self.final.get("terminal_status"), "COMPLETED",
                         self.final.get("terminal_reason"))

    def test_the_worker_turn_creates_two_distinct_sessions_before_delivering(self) -> None:
        recorder = self.launch_fixture.recorder
        self.assertEqual(len(recorder.pair_titles), 2)
        self.assertEqual(len(set(recorder.pair_handles)), 2)
        self.assertEqual(sorted(recorder.pair_titles),
                         sorted([f"{RUN_ID}-pair-{PHASE}-1-worker",
                                 f"{RUN_ID}-pair-{PHASE}-1-reviewer"]))
        first_delivery = next(index for index, c in enumerate(recorder.commands)
                              if c[1:2] == ("worker-start",))
        pair_creates = [index for index, c in enumerate(recorder.commands)
                        if c[:2] == ("terminal", "create") and "-pair-" in str(c)]
        self.assertEqual(len(pair_creates), 2)
        self.assertTrue(all(index < first_delivery for index in pair_creates),
                        "both sessions exist before the FIRST delivery")
        first_observe = next(index for index, c in enumerate(recorder.commands)
                             if c[1:2] == ("worker-start",))
        self.assertLess(pair_creates[-1], first_observe)

    def test_the_first_pass_issues_no_terminal_list(self) -> None:
        """Nothing was prepared yet, so there is no title and no digest to resolve."""
        creates = [index for index, c in enumerate(self.launch_fixture.recorder.commands)
                   if c[:2] == ("terminal", "create") and "-pair-" in str(c)]
        lists = [index for index, c in enumerate(self.launch_fixture.recorder.commands)
                 if c[:2] == ("terminal", "list")]
        self.assertTrue(all(index > creates[-1] for index in lists),
                        "the only `terminal list` belongs to the REVIEWER turn")
        self.assertEqual(len([index for index in lists if index < creates[0]]), 0)

    def test_the_reviewer_turn_adopts_and_creates_nothing(self) -> None:
        recorder = self.launch_fixture.recorder
        self.assertEqual(len(recorder.pair_titles), 2,
                         "the Reviewer turn created NO new pair session")
        self.assertEqual(recorder.count("terminal", "list"), 1)

    def test_exactly_one_task_create_and_one_worker_start_per_dispatch(self) -> None:
        recorder = self.launch_fixture.recorder
        self.assertEqual(sum(1 for c in recorder.commands
                             if c[1:2] == ("task-create",)), 3,
                         "worker, phase reviewer, final reviewer")
        self.assertEqual(sum(1 for c in recorder.commands
                             if c[1:2] == ("worker-start",)), 3)

    def test_both_roles_read_verified_with_the_two_different_models(self) -> None:
        harness = self.launch_fixture.harness
        handles = dict(zip(("worker", "reviewer"),
                           self.launch_fixture.recorder.pair_handles))
        resolved = {role: harness.ledger_terminal(handle)
                    for role, handle in handles.items()}
        self.assertEqual(resolved["worker"]["model_state"], MODEL_EVIDENCE_VERIFIED)
        self.assertEqual(resolved["reviewer"]["model_state"], MODEL_EVIDENCE_VERIFIED)
        self.assertEqual(resolved["worker"]["resolved_model"], "glm-5.2")
        self.assertEqual(resolved["reviewer"]["resolved_model"], "glm-5.3-flash")

    def test_both_entries_are_verified_with_distinct_digests_and_titles(self) -> None:
        worker = self.launch_fixture.entry("worker")
        reviewer = self.launch_fixture.entry("reviewer")
        self.assertEqual(worker["stage"], "VERIFIED")
        self.assertEqual(reviewer["stage"], "VERIFIED")
        self.assertNotEqual(worker["terminal_digest"], reviewer["terminal_digest"])
        self.assertNotEqual(worker["terminal_title"], reviewer["terminal_title"])
        self.assertEqual(worker["resolved_model_observed"], "glm-5.2")
        self.assertEqual(reviewer["resolved_model_observed"], "glm-5.3-flash")
        self.assertEqual(worker["observed_at_run"], RUN_ID)

    def test_every_entry_timestamp_is_the_one_documented_format(self) -> None:
        from datetime import datetime
        for role in ("worker", "reviewer"):
            entry = self.launch_fixture.entry(role)
            for cell in ("create_intended_at", "create_settled_at", "verified_at"):
                value = entry[cell]
                self.assertIs(type(value), str)
                self.assertEqual(
                    datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").strftime(
                        "%Y-%m-%dT%H:%M:%SZ"), value, f"{role}.{cell}")

    def test_the_launch_record_digest_equals_the_live_derivation(self) -> None:
        binding = self.launch_fixture.binding()
        live = self.launch_fixture.harness.routing_binding()
        self.assertEqual(binding["routing_digest"], live["routing_digest"])
        self.assertEqual(binding["model_aware"], "true")
        self.assertEqual(binding["driver_type_id"],
                         "deterministic_workflow.fake_adapter:InProcessModelDriver")

    def test_preparation_evidence_names_no_task_and_delivery_evidence_does(self) -> None:
        """`task_id=""` at preparation STATES that no Task exists yet; the ACCEPTED
        delivery evidence carries this attempt's real Task id."""
        tickets = self.launch_fixture.driver.tickets
        preparation = [t for t in tickets if t.task_id == ""]
        delivery = [t for t in tickets if t.task_id != ""]
        self.assertTrue(preparation, "preparation ran through the driver seam")
        self.assertTrue(delivery, "Gate B re-minted a ticket with the real Task id")
        self.assertTrue(all(t.task_id.startswith("task_os14_") for t in delivery))

    def test_verify_model_identity_has_a_production_caller_in_the_engine(self) -> None:
        """C1, asserted STATICALLY: the public pre-pass is no longer test-only."""
        engine = REPO_ROOT / "scripts" / "deterministic_workflow"
        callers = [path.name for path in engine.rglob("*.py")
                   if "verify_model_identity(" in path.read_text(encoding="utf-8")]
        self.assertIn("orca_adapter.py", callers)


# ======================================================================================
# T2  M2: a second-session failure delivers NOTHING
# ======================================================================================
@REQUIRES_LANGGRAPH
class T2SecondSessionFailureTests(PairRoom):
    """Each subcase drives the PRODUCTION `call()` by substituting `_exec_orca`."""

    def test_a_parsed_refusal_of_the_second_create_delivers_nothing(self) -> None:
        """(a) The runtime's own `ok:false` receipt: CONFIRMED absence."""
        launch = self.launch(create_failures={3: REFUSED_CREATE})
        node = self.node(launch)
        with self.assertRaises(OrcaCommandRefused) as caught:
            node(launch.prepared_intent())
        self.assertIs(caught.exception.ok, False,
                      "`ok` is echoed as the PRIMITIVE the runtime reported")
        self.assertEqual(caught.exception.command[:2], ("terminal", "create"))
        self.assertEqual(caught.exception.error_code, "TERMINAL_CREATE_REFUSED")
        self.assertEqual(launch.entry("worker")["stage"], "CREATED")
        reviewer = launch.entry("reviewer")
        self.assertEqual(reviewer["stage"], "CREATE_REFUSED")
        self.assertTrue(reviewer["refusal_command"])
        self.assertEqual(json.loads(reviewer["refusal_command"])[:2],
                         ["terminal", "create"])
        self.assertEqual(reviewer["refusal_error_code"], "TERMINAL_CREATE_REFUSED")
        self.assertEqual(len(reviewer["refusal_receipt_digest"]), 64)
        self.assertEqual(len(launch.recorder.pair_titles), 2,
                         "two create COMMANDS were issued")
        self.assertEqual(len(launch.recorder.pair_handles), 1,
                         "exactly ONE session came into existence")
        self.assertNoEffects(launch.recorder, creates=3)

    def test_a_non_verified_reviewer_evidence_delivers_nothing(self) -> None:
        """(b) The OS-49 refusal keeps its shipped identity and is not wrapped."""
        driver = _RoleScriptedDriver(states={"reviewer": "unverifiable"})
        launch = self.launch(driver=driver)
        node = self.node(launch)
        prepared = launch.prepared_intent()
        with self.assertRaises(OrcaRuntimeError) as caught:
            node(prepared)
        self.assertIs(type(caught.exception), OrcaRuntimeError,
                      "the OS-49 refusal keeps its shipped TYPE, unwrapped")
        self.assertIn("model_selection_unsupported", str(caught.exception))
        self.assertIn("only 'verified' admits a delivery", str(caught.exception))
        self.assertEqual(len(launch.recorder.pair_handles), 2)
        self.assertEqual(launch.entry("worker")["stage"], "VERIFIED")
        self.assertEqual(launch.entry("reviewer")["stage"], "CREATED",
                         "a refusal promotes nothing")
        self.assertNoEffects(launch.recorder, creates=3)

    def test_one_handle_for_both_roles_is_refused_before_any_verification(self) -> None:
        """(c) One physical session cannot be both sides of a pair."""
        launch = self.launch(same_handle=True)
        final = self.execute(launch)
        self.assertBlocked(final, "PAIR_PREPARATION_SESSION_NOT_DISTINCT")
        self.assertEqual(len(launch.recorder.pair_titles), 2)
        self.assertEqual(len(set(launch.recorder.pair_handles)), 1)
        self.assertEqual(launch.driver.requests, [],
                         "the refusal precedes BOTH verifications")
        self.assertNoEffects(launch.recorder, creates=3)

    def test_a_driver_that_collapses_two_aliases_is_refused(self) -> None:
        """(d) A collapse is refused, and the refusal keeps its OS-49 identity.

        Two refusals exist on this path and both are asserted, because they are
        different facts.  The REFERENCE driver owns satisfaction and reports `mismatch`
        itself when a resolution differs from the request, so an honest driver that
        collapses is refused `model_selection_mismatch` one leg earlier.  A driver that
        collapses AND claims `verified` is refused by the pair's effective-identity leg
        (`WORKER_REVIEWER_MUST_DIFFER`) -- the leg that protects the pair when a driver
        lies."""
        honest = self.launch(driver=InProcessModelDriver(
            resolve=lambda requested: "glm-5.2"))
        with self.assertRaises(OrcaRuntimeError) as caught:
            self.node(honest)(honest.prepared_intent())
        self.assertIn("model_selection_mismatch", str(caught.exception))
        self.assertEqual(len(honest.recorder.pair_handles), 2)
        self.assertNoEffects(honest.recorder, creates=3)

        lying = self.launch(driver=_CollapsingDriver(), ledger_name="ledger_d2.json",
                            run_id="run_os14b")
        with self.assertRaises(OrcaRuntimeError) as caught:
            self.node(lying)(lying.prepared_intent())
        self.assertIn("WORKER_REVIEWER_MUST_DIFFER", str(caught.exception))
        self.assertIn("two distinct declared tokens that resolve to one model are not "
                      "two agents", str(caught.exception))
        self.assertEqual(len(lying.recorder.pair_handles), 2)
        self.assertNoEffects(lying.recorder, creates=3)

    def test_an_unparsed_failure_is_unknown_and_blocks_identically_on_re_entry(self):
        """(e) An unparsed failure is NOT a refusal: no durable write, and the FIRST
        attempt settles BLOCKED through `execute_state` rather than propagating a bare
        `OrcaRuntimeError` out of the graph."""
        launch = self.launch(create_failures={3: UNPARSED_CREATE})
        first = self.execute(launch)
        self.assertBlocked(first, "PAIR_PREPARATION_OUTCOME_UNKNOWN")
        reviewer = launch.entry("reviewer")
        self.assertEqual(reviewer["stage"], "CREATE_INTENDED")
        self.assertEqual(reviewer["terminal_digest"], "")
        self.assertEqual(reviewer["refusal_error_code"], "")
        creates_after_first = launch.recorder.count("terminal", "create")
        second = self.execute(launch)
        self.assertBlocked(second, "PAIR_PREPARATION_OUTCOME_UNKNOWN")
        self.assertEqual(launch.recorder.count("terminal", "create"),
                         creates_after_first,
                         "the re-entry creates nothing further")
        self.assertEqual(len(launch.recorder.pair_handles), 1)
        self.assertNoEffects(launch.recorder, creates=creates_after_first)


class _CollapsingDriver:
    """An ADVERSARIAL reference-driver variant: it resolves every request to ONE model
    and claims `verified` anyway.

    It exists to exercise the pair rule's own leg -- a driver that reports satisfaction it
    did not observe is exactly what `effective_identity_independent` is for.  Still a
    reference driver: its observation leg re-reads the value its own request leg stored,
    so nothing here is evidence about a real provider.
    """

    REQUEST_METHOD = "driver_select_and_verify"
    OBSERVATION_METHOD = "in_process_session_state"
    COLLAPSED = "glm-5.2"

    def __init__(self) -> None:
        self.sessions: dict[str, str] = {}
        self.requests: list[tuple[str, int]] = []
        self.tickets: list = []

    def select_and_verify(self, ticket):
        from scripts.deterministic_workflow.contracts import (
            MODEL_SELECTION_VERIFIED)
        from scripts.orca_runtime_harness import ModelEvidence
        self.tickets.append(ticket)
        request_stamp = ticket.stamp()
        self.requests.append(("request", request_stamp))
        self.sessions[ticket.terminal] = ticket.requested_model
        observe_stamp = ticket.stamp()
        self.requests.append(("observe", observe_stamp))
        return ModelEvidence(
            state="verified", requested_model=ticket.requested_model,
            resolved_model=self.COLLAPSED, selection_token=ticket.token,
            request_method=self.REQUEST_METHOD, request_stamp=request_stamp,
            observation_method=self.OBSERVATION_METHOD, observe_stamp=observe_stamp,
            capability=MODEL_SELECTION_VERIFIED, observed_at_run=ticket.run_id,
            observed_at_task=ticket.task_id, observed_at_terminal=ticket.terminal,
            observed_at_role=ticket.role, observed_at_phase=ticket.phase,
            observed_at_attempt=ticket.attempt)


class _AttestingDriver:
    """A reference driver that reports `verified` for whatever it resolved.

    It exists because the CROSS-PROCESS drift block is unreachable with
    `InProcessModelDriver`: that driver owns satisfaction and reports `mismatch` itself
    whenever a resolution differs from the request, so a drifted resolution never reaches
    a `verified` state at all.  The real situation the block exists for is a PROVIDER
    whose alias moved under a request that is still spelled the same -- a driver that
    honestly observes a different model and reports it as the resolution.  This class
    expresses exactly that, and ONE class is used by both the predecessor and the
    successor of T7(h)/(i), because the driver CLASS is part of the launch identity by
    design and a different class is refused before any effect.

    Still a reference driver: its observation leg re-reads the value its own request leg
    stored, so nothing it proves is evidence about any real provider model.
    """

    REQUEST_METHOD = "driver_select_and_verify"
    OBSERVATION_METHOD = "in_process_session_state"

    def __init__(self, *, resolve_map=None, refuse_roles=()) -> None:
        self.resolve_map = dict(resolve_map or {})
        self.refuse_roles = set(refuse_roles)
        self.sessions: dict[str, str] = {}
        self.requests: list[tuple[str, int]] = []
        self.tickets: list = []

    def select_and_verify(self, ticket):
        from scripts.deterministic_workflow.contracts import (
            MODEL_SELECTION_VERIFIED)
        from scripts.orca_runtime_harness import ModelEvidence
        self.tickets.append(ticket)
        request_stamp = ticket.stamp()
        self.requests.append(("request", request_stamp))
        self.sessions[ticket.terminal] = ticket.requested_model
        resolved = self.resolve_map.get(ticket.requested_model, ticket.requested_model)
        observe_stamp = ticket.stamp()
        self.requests.append(("observe", observe_stamp))
        role = "reviewer" if str(ticket.role).endswith("reviewer") else "worker"
        return ModelEvidence(
            state=("unverifiable" if role in self.refuse_roles else "verified"),
            requested_model=ticket.requested_model, resolved_model=resolved,
            selection_token=ticket.token, request_method=self.REQUEST_METHOD,
            request_stamp=request_stamp, observation_method=self.OBSERVATION_METHOD,
            observe_stamp=observe_stamp, capability=MODEL_SELECTION_VERIFIED,
            observed_at_run=ticket.run_id, observed_at_task=ticket.task_id,
            observed_at_terminal=ticket.terminal, observed_at_role=ticket.role,
            observed_at_phase=ticket.phase, observed_at_attempt=ticket.attempt)


class _RoleScriptedDriver(InProcessModelDriver):
    """A reference driver whose evidence STATE is scripted per routing role.

    Still the reference driver -- its observation leg re-reads what its own request leg
    stored -- so a green assertion remains evidence about the WIRING only.
    """

    def __init__(self, *, states=None, resolve_map=None, raises=None) -> None:
        super().__init__(resolve=lambda requested: requested)
        self.states = dict(states or {})
        self.resolve_map = dict(resolve_map or {})
        self.raises = dict(raises or {})

    def select_and_verify(self, ticket):
        role = "reviewer" if str(ticket.role).endswith("reviewer") else "worker"
        failure = self.raises.get(role)
        if failure is not None:
            raise failure
        previous_state, previous_resolve = self.state, self.resolve
        self.state = self.states.get(role, previous_state)
        if role in self.resolve_map:
            mapped = self.resolve_map[role]
            self.resolve = lambda requested: mapped
        try:
            return super().select_and_verify(ticket)
        finally:
            self.state, self.resolve = previous_state, previous_resolve


# ======================================================================================
# T3  M8: the existing non-model paths and the OS-31/37/49 protections
# ======================================================================================
class T3NoDriverStaysFailClosedTests(PairRoom):
    """(a) The production default passes no driver and a declared model is refused."""

    def test_a_declared_model_with_no_driver_is_refused_before_any_run(self) -> None:
        recorder = PairRecorder(scripted_bodies())
        with self.assertRaises(launcher.LauncherError) as caught:
            self.launch(recorder=recorder, driver=None)
        self.assertIn("AGENT_MODEL_NOT_SUPPORTED", str(caught.exception))
        self.assertEqual(recorder.commands, [],
                         "Gate A refuses before a harness, Run, Task or terminal exists")
        self.assertFalse((self.project / "artifacts" / "runs" / RUN_ID).exists())


@REQUIRES_LANGGRAPH
class T3DistinctCommandPairTests(PairRoom):
    """(b) A model-aware DISTINCT-command pair prepares nothing."""

    profile_text = DISTINCT_PROFILE

    def test_no_preparation_and_one_session_per_dispatch(self) -> None:
        launch = self.launch()
        final = self.execute(launch)
        self.assertEqual(final.get("terminal_status"), "COMPLETED",
                         final.get("terminal_reason"))
        self.assertEqual(launch.recorder.pair_titles, [])
        self.assertEqual(launch.recorder.count("terminal", "list"), 0)
        self.assertEqual(launch.recorder.count("worktree", "current"), 0,
                         "no `worktree current` is added on a non-prepared run")
        self.assertFalse(launch.harness.pair_admission_required("worker", PHASE))

    def test_the_run_root_gains_only_the_launch_record(self) -> None:
        launch = self.launch()
        self.execute(launch)
        names = {path.name for path in launch.run_root().iterdir()}
        self.assertIn(".pair_launch_binding.json", names)
        self.assertNotIn(".pair_preparation.json", names)
        binding = launch.binding()
        self.assertEqual(binding["model_aware"], "true")
        self.assertTrue(binding["routing_digest"])

    def test_the_launch_record_is_written_at_construction(self) -> None:
        """(e) The unconditional pre-graph write EXECUTED -- the constructor returned an
        adapter rather than raising -- and its timestamp has the one documented format."""
        from datetime import datetime
        launch = self.launch()
        self.assertIsInstance(launch.adapter, OrcaAdapter)
        recorded_at = launch.binding()["recorded_at"]
        self.assertIs(type(recorded_at), str)
        self.assertEqual(
            datetime.strptime(recorded_at, "%Y-%m-%dT%H:%M:%SZ").strftime(
                "%Y-%m-%dT%H:%M:%SZ"), recorded_at)


@REQUIRES_LANGGRAPH
class T3LegacyPathTests(PairRoom):
    """(c) A model-less run: byte-identical behaviour, authorised by a POSITIVE "false"."""

    profile_text = LEGACY_PROFILE

    def test_the_legacy_run_delivers_exactly_as_before(self) -> None:
        launch = self.launch(driver=None)
        final = self.execute(launch)
        self.assertEqual(final.get("terminal_status"), "COMPLETED",
                         final.get("terminal_reason"))
        self.assertEqual(launch.recorder.pair_titles, [])
        self.assertEqual(launch.recorder.count("terminal", "list"), 0)
        self.assertEqual(sum(1 for c in launch.recorder.commands
                             if c[1:2] == ("worker-start",)), 3)

    def test_the_launch_record_states_false_and_nothing_else_appears(self) -> None:
        launch = self.launch(driver=None)
        self.execute(launch)
        binding = launch.binding()
        self.assertEqual(binding["model_aware"], "false")
        self.assertEqual(binding["routing_digest"], "")
        self.assertEqual(binding["driver_type_id"], "")
        names = {path.name for path in launch.run_root().iterdir()}
        self.assertIn(".pair_launch_binding.json", names)
        self.assertNotIn(".pair_preparation.json", names)

    def test_the_launch_record_is_written_at_construction_on_the_legacy_lane(self) -> None:
        """(e), the other lane: the write is NOT conditional on model awareness."""
        from datetime import datetime
        launch = self.launch(driver=None)
        self.assertIsInstance(launch.adapter, OrcaAdapter)
        recorded_at = launch.binding()["recorded_at"]
        self.assertEqual(
            datetime.strptime(recorded_at, "%Y-%m-%dT%H:%M:%SZ").strftime(
                "%Y-%m-%dT%H:%M:%SZ"), recorded_at)

    def test_a_legacy_re_entry_with_no_profile_is_authorised_by_the_record(self) -> None:
        launch = self.launch(driver=None)
        self.execute(launch)
        successor_recorder = PairRecorder(scripted_bodies())
        adapter = self.adopt(recorder=successor_recorder,
                             ledger=FileRuntimeStateStore(self.root / "l2.json"))
        state = dict(launch.state)
        prepared = adapter  # the adoption built a real adapter over the same run root
        self.assertEqual(prepared.pair_binding.binding()["model_aware"], "false")
        self.assertIsNone(prepared.pair_preparation.entry(PHASE, 1, "worker"))
        del state


class T3StaticProtectionTests(unittest.TestCase):
    """(d) The source-level invariants, asserted statically rather than narrated."""

    TOUCHED = ("scripts/agent_profile.py", "scripts/orca_runtime_harness.py",
               "scripts/deterministic_workflow/pause_store.py",
               "scripts/deterministic_workflow/pause_policy.py",
               "scripts/deterministic_workflow/orca_adapter.py",
               "scripts/deterministic_workflow/launcher.py")

    def source(self, relative):
        return (REPO_ROOT / relative).read_text(encoding="utf-8")

    def test_no_provider_cli_flag_or_stream_event_field_is_introduced(self) -> None:
        banned = ("--model ", "system/init.model", "assistant.message.model",
                  "modelUsage", "num_turns", "--resume")
        for relative in ("scripts/deterministic_workflow/orca_adapter.py",
                         "scripts/deterministic_workflow/pause_store.py",
                         "scripts/deterministic_workflow/pause_policy.py"):
            text = self.source(relative)
            for token in banned:
                self.assertNotIn(token, text, f"{relative} must not name {token!r}")

    #: The modules this change ADDS logic to.  `agent_profile.py` and
    #: `orca_runtime_harness.py` are excluded from the literal scans below only because
    #: they carry PRE-EXISTING prose naming example model tokens; the cells this change
    #: adds to them are covered by the vocabulary and capability locks instead.
    NEW_SURFACE = ("scripts/deterministic_workflow/pause_store.py",
                   "scripts/deterministic_workflow/pause_policy.py",
                   "scripts/deterministic_workflow/orca_adapter.py",
                   "scripts/deterministic_workflow/launcher.py")

    def test_no_alias_table_is_introduced_in_common_code(self) -> None:
        for relative in self.NEW_SURFACE:
            text = self.source(relative)
            for token in ("glm-5", "claude-opus", "sonnet", "haiku", "gpt-"):
                self.assertNotIn(token, text, f"{relative} must name no model alias")

    def test_the_model_selection_vocabularies_gained_no_member(self) -> None:
        from scripts.orca_runtime_harness import (
            MODEL_SELECTION_OBSERVATION_METHODS, MODEL_SELECTION_REQUEST_METHODS)
        self.assertEqual(MODEL_SELECTION_REQUEST_METHODS,
                         ("driver_select_and_verify",))
        self.assertEqual(MODEL_SELECTION_OBSERVATION_METHODS,
                         ("in_process_session_state",))

    def test_the_adapter_declares_no_new_capability_token(self) -> None:
        from scripts.deterministic_workflow.contracts import (BASE_CAPABILITIES,
                                                              LIFECYCLE_SETTLEMENT)
        adapter = OrcaAdapter(None)
        self.assertNotIn("model_selection_verified", adapter.capabilities())
        self.assertNotIn(LIFECYCLE_SETTLEMENT, adapter.capabilities())
        self.assertTrue(BASE_CAPABILITIES <= adapter.capabilities())
        journalled = OrcaAdapter(None, settlement_journal=object())
        self.assertIn(LIFECYCLE_SETTLEMENT, journalled.capabilities())
        prepared = OrcaAdapter(None, pair_binding=object(), pair_preparation=object())
        self.assertEqual(prepared.capabilities(), adapter.capabilities(),
                         "wiring the pair stores declares NO capability")

    def test_both_production_constructions_wire_a_pair_binding_store(self) -> None:
        """The construction-level axis cannot be skipped by omission."""
        import inspect
        for function in (launcher.build_orca_adapter,
                         launcher.build_orca_adapter_for_run):
            text = inspect.getsource(function)
            self.assertIn("pair_binding=", text, function.__name__)
            self.assertIn("pair_preparation=", text, function.__name__)

    def test_the_capabilities_only_construction_stays_unwired(self) -> None:
        adapter = OrcaAdapter(None)
        self.assertIsNone(adapter.pair_binding)
        self.assertIsNone(adapter.pair_preparation)

    def test_the_reference_driver_is_never_called_real_model_verification(self) -> None:
        """Docstrings and log lines say "reference driver" / "wiring", never that a real
        provider model was verified."""
        for relative in self.TOUCHED:
            text = self.source(relative).lower()
            for claim in ("real model verified", "verified the real model",
                          "proves the real model", "actual provider model verified"):
                self.assertNotIn(claim, text, relative)

    def test_the_refusal_code_set_is_closed_and_complete(self) -> None:
        self.assertEqual(sorted(PAIR_PREPARATION_REFUSAL_CODES), [
            "PAIR_PREPARATION_BINDING_LOST",
            "PAIR_PREPARATION_BINDING_MISMATCH",
            "PAIR_PREPARATION_BINDING_UNVERIFIABLE",
            "PAIR_PREPARATION_LAUNCH_RECORD_ABSENT",
            "PAIR_PREPARATION_MODEL_DRIFT",
            "PAIR_PREPARATION_MODEL_UNOBSERVED",
            "PAIR_PREPARATION_OUTCOME_UNKNOWN",
            "PAIR_PREPARATION_OWNERSHIP_MISMATCH",
            "PAIR_PREPARATION_RECORD_CORRUPT",
            "PAIR_PREPARATION_SCOPE_UNRESOLVED",
            "PAIR_PREPARATION_SESSION_ABSENT",
            "PAIR_PREPARATION_SESSION_NOT_DISTINCT",
            "PAIR_PREPARATION_SESSION_UNVERIFIED",
        ])

    @staticmethod
    def _intent_for(run_id=RUN_ID):
        """The intent the ENGINE's own nodes prepare -- never hand-built."""
        state = launcher.build_state({"run_id": run_id, "thread_id": "os14",
                                      "phases": [PHASE_UPPER], "risk": "high"})
        prepared = executor.prepare_intent_node(
            executor.route_node(executor.validate_node(state)))
        return prepared["pending_intent"]

    def test_a_harness_that_cannot_state_its_routing_is_refused_by_name(self) -> None:
        """An unanswered question is a NAMED refusal, never an AttributeError.

        A duck-typed harness that does not implement `routing_binding()` cannot state its
        own routing identity, so this process cannot reconcile the run's launch record
        against anything.  Same discipline `_pair_admission_required` applies to its
        predicate and `OrcaRuntimeHarness._routing_is_model_aware` applies to an object
        that cannot answer: unknown, not false -- and never a crash out of a fail-closed
        guard.  Reached through the production entry point `OrcaAdapter.start`.
        """
        import tempfile as _tempfile
        from scripts.deterministic_workflow import pause_store as store_module

        class HarnessWithoutRoutingBinding:
            run_id = RUN_ID

        with _tempfile.TemporaryDirectory() as directory:
            binding = store_module.pair_binding_for(RUN_ID,
                                                    artifact_base=Path(directory))
            binding.record_binding(runtime="", profile_name="", profile_source="",
                                   routing_schema_version="", routing_digest="",
                                   model_aware="false", driver_type_id="")
            adapter = OrcaAdapter(
                HarnessWithoutRoutingBinding(), pair_binding=binding,
                pair_preparation=store_module.pair_preparation_for(
                    RUN_ID, artifact_base=Path(directory)))
            with self.assertRaises(executor.IdempotencyRecoveryError) as caught:
                adapter.start(self._intent_for())
        self.assertEqual(caught.exception.code,
                         "PAIR_PREPARATION_BINDING_UNVERIFIABLE")
        self.assertIn("routing_binding", caught.exception.detail)

    def test_an_absent_record_is_named_even_when_the_harness_cannot_answer(self) -> None:
        """The two absent-record rows do not consult the live derivation at all, so a
        harness that cannot answer must not hide them behind an AttributeError."""
        import tempfile as _tempfile
        from scripts.deterministic_workflow import pause_store as store_module

        class HarnessWithoutRoutingBinding:
            run_id = RUN_ID

        with _tempfile.TemporaryDirectory() as directory:
            adapter = OrcaAdapter(
                HarnessWithoutRoutingBinding(),
                pair_binding=store_module.pair_binding_for(
                    RUN_ID, artifact_base=Path(directory)),
                pair_preparation=store_module.pair_preparation_for(
                    RUN_ID, artifact_base=Path(directory)))
            with self.assertRaises(executor.IdempotencyRecoveryError) as absent:
                adapter.start(self._intent_for())
            self.assertEqual(absent.exception.code,
                             "PAIR_PREPARATION_LAUNCH_RECORD_ABSENT")
            adapter.pair_preparation.record(PHASE, 1, "worker",
                                            stage="CREATE_INTENDED",
                                            terminal_title="t")
            with self.assertRaises(executor.IdempotencyRecoveryError) as lost:
                adapter.start(self._intent_for())
            self.assertEqual(lost.exception.code, "PAIR_PREPARATION_BINDING_LOST")

    def test_preparation_catches_no_control_flow_exception(self) -> None:
        """KeyboardInterrupt / SystemExit / GeneratorExit propagate as themselves.

        Asserted over the PARSED handlers rather than over the source text, so a comment
        that merely says so cannot satisfy it."""
        import ast
        import inspect
        import textwrap
        from scripts.deterministic_workflow import orca_adapter
        banned = {"BaseException", "KeyboardInterrupt", "SystemExit", "GeneratorExit"}
        for name in ("_prepare_pair", "_prepare_role", "_assert_preparation_binding",
                     "_prepared_listing", "_refuse_preparation"):
            tree = ast.parse(textwrap.dedent(
                inspect.getsource(getattr(orca_adapter.OrcaAdapter, name))))
            for node in ast.walk(tree):
                if not isinstance(node, ast.ExceptHandler) or node.type is None:
                    continue
                caught = {part.id for part in ast.walk(node.type)
                          if isinstance(part, ast.Name)}
                self.assertFalse(caught & banned, f"{name} catches {caught & banned}")
            self.assertFalse(
                any(isinstance(node, ast.ExceptHandler) and node.type is None
                    for node in ast.walk(tree)), f"{name} has a bare except")


# ======================================================================================
# T4  C5: correction / re-review evidence validity
# ======================================================================================
@REQUIRES_LANGGRAPH
class T4CorrectionRoundTests(PairRoom):
    """A correction round is a DIFFERENT gate round, and it prepares a FRESH pair."""

    def _correction_run(self, *, second_round_driver=None):
        """Round 1 fails its phase gate, so round 2 is a CORRECTION for the same phase."""
        bodies = [worker_body(), reviewer_body(verdict="FAIL"),
                  worker_body(iteration=2), reviewer_body(iteration=2),
                  final_body()]
        launch = self.launch(bodies=bodies)
        return launch

    def test_round_two_gets_its_own_key_and_a_fresh_pair(self) -> None:
        launch = self._correction_run()
        final = self.execute(launch)
        self.assertEqual(final.get("terminal_status"), "COMPLETED",
                         final.get("terminal_reason"))
        pairs = launch.pairs()
        self.assertIn(f"{PHASE}#1", pairs)
        self.assertIn(f"{PHASE}#2", pairs)
        round_two_titles = [entry["terminal_title"]
                            for entry in pairs[f"{PHASE}#2"].values()]
        self.assertEqual(sorted(round_two_titles),
                         sorted([f"{RUN_ID}-pair-{PHASE}-2-worker",
                                 f"{RUN_ID}-pair-{PHASE}-2-reviewer"]))
        self.assertEqual(len(launch.recorder.pair_titles), 4,
                         "two sessions per gate round, created per round")

    def test_round_one_evidence_alone_does_not_admit_round_two(self) -> None:
        """Round 2's own driver legs are what admit it: a driver failure in round 2
        refuses even though round 1 verified.

        The refusal keeps its SHIPPED OS-49 identity and propagation -- it is an
        `OrcaRuntimeError` from the barrier, deliberately NOT translated into a
        `PAIR_PREPARATION_*` code and not given a BLOCKED terminal it does not have
        today."""
        driver = _AttemptScriptedDriver(fail_from_request=7)
        launch = self.launch(bodies=[worker_body(), reviewer_body(verdict="FAIL"),
                                     worker_body(iteration=2)],
                             driver=driver)
        with self.assertRaises(OrcaRuntimeError) as caught:
            self.execute(launch)
        self.assertIn("model_selection", str(caught.exception))
        self.assertEqual(launch.pairs()[f"{PHASE}#1"]["worker"]["stage"], "VERIFIED")
        self.assertIn(f"{PHASE}#2", launch.pairs(),
                      "round 2 opened its OWN entry rather than reusing round 1's")

    def test_a_round_that_re_resolves_a_role_away_from_its_baseline_is_refused(self):
        """Leg (i): a re-verified round must resolve to the SAME model per role."""
        driver = _AttemptScriptedDriver(drift_from_request=7,
                                        drift_value="glm-5.9-other")
        launch = self.launch(bodies=[worker_body(), reviewer_body(verdict="FAIL"),
                                     worker_body(iteration=2)],
                             driver=driver)
        with self.assertRaises(OrcaRuntimeError) as caught:
            self.execute(launch)
        self.assertIn("model_selection", str(caught.exception))


class _AttemptScriptedDriver(InProcessModelDriver):
    """A reference driver that changes behaviour from the Nth REQUEST leg onwards."""

    def __init__(self, *, fail_from_request=None, drift_from_request=None,
                 drift_value="") -> None:
        super().__init__(resolve=lambda requested: requested)
        self.fail_from_request = fail_from_request
        self.drift_from_request = drift_from_request
        self.drift_value = drift_value
        self.calls = 0

    def select_and_verify(self, ticket):
        self.calls += 1
        previous_state, previous_resolve = self.state, self.resolve
        if (self.fail_from_request is not None
                and self.calls >= self.fail_from_request):
            self.state = "unverifiable"
        if (self.drift_from_request is not None
                and self.calls >= self.drift_from_request):
            drifted = self.drift_value
            self.resolve = lambda requested: drifted
        try:
            return super().select_and_verify(ticket)
        finally:
            self.state, self.resolve = previous_state, previous_resolve


# ======================================================================================
# T5  M5: timeout and interrupt at verification, and the post-failure resource state
# ======================================================================================
class T5VerificationFailureResourceStateTests(PairRoom):
    """Injected through the already-supported `model_driver` seam -- no signal, no
    SIGALRM, no busy loop, no wall clock and no main-thread requirement."""

    def _snapshot(self, launch, handles):
        return {role: dict(launch.harness.ledger_terminal(handle))
                for role, handle in handles.items()}

    def _drive(self, driver):
        launch = self.launch(driver=driver)
        node = self.node(launch)
        intent = launch.prepared_intent()
        return launch, node, intent

    def test_a_driver_timeout_normalises_to_the_existing_member(self) -> None:
        """(a) `subprocess.TimeoutExpired` IS an `Exception`, so the harness stales the
        Reviewer session and normalises to the EXISTING vocabulary member."""
        failure = subprocess.TimeoutExpired(cmd="claude", timeout=1)
        launch, node, intent = self._drive(
            _RoleScriptedDriver(raises={"reviewer": failure}))
        with self.assertRaises(OrcaRuntimeError) as caught:
            node(intent)
        self.assertIn("model_selection_unverified", str(caught.exception))
        self._assert_post_failure_state(launch, intent)

    def test_a_keyboard_interrupt_propagates_unwrapped(self) -> None:
        """(b) Control flow is NEVER caught: it leaves as itself."""
        launch, node, intent = self._drive(
            _RoleScriptedDriver(raises={"reviewer": KeyboardInterrupt()}))
        with self.assertRaises(KeyboardInterrupt):
            node(intent)
        self._assert_post_failure_state(launch, intent)

    def test_a_plain_in_vocabulary_refusal_leaves_the_same_state(self) -> None:
        """(c) After a SUCCESSFUL Worker verification."""
        launch, node, intent = self._drive(
            _RoleScriptedDriver(states={"reviewer": "unverifiable"}))
        with self.assertRaises(OrcaRuntimeError):
            node(intent)
        self._assert_post_failure_state(launch, intent)

    def _assert_post_failure_state(self, launch, intent) -> None:
        recorder, harness = launch.recorder, launch.harness
        self.assertEqual(len(launch.recorder.pair_handles), 2,
                         "sessions created BY THIS TEST")
        self.assertNoEffects(recorder, creates=3)
        self.assertEqual(recorder.count("terminal", "close"), 0)
        self.assertEqual(sum(1 for c in recorder.commands
                             if c[1:2] in (("worker-release",), ("worker-abandon",))), 0,
                         "both prepared sessions are RETAINED")
        receipt = launch.ledger.get_receipt(intent["pending_intent"]["intent_id"])
        self.assertEqual(receipt["status"], "CLAIMED")
        self.assertFalse(receipt["receipt"],
                         "the CLAIMED -> EFFECTED flip never ran")
        worker_handle, reviewer_handle = launch.recorder.pair_handles
        worker_row = harness.ledger_terminal(worker_handle)
        reviewer_row = harness.ledger_terminal(reviewer_handle)
        self.assertEqual(worker_row["model_state"], MODEL_EVIDENCE_VERIFIED)
        self.assertEqual(worker_row["resolved_model"], "glm-5.2")
        self.assertNotEqual(reviewer_row["model_state"], MODEL_EVIDENCE_VERIFIED)
        self.assertEqual(reviewer_row["resolved_model"], "",
                         "the Reviewer's model cells are cleared, the Worker's are not")
        self.assertEqual(worker_row["requested_model"], "glm-5.2")
        self.assertEqual(reviewer_row["requested_model"], "glm-5.3-flash",
                         "requested_model is UNCHANGED on both: no pair-wide revocation")


# ======================================================================================
# T6  M3: same-process re-entry and retry
# ======================================================================================
class T6SameProcessReentryTests(PairRoom):
    """Through `execute_intent_node` over ONE real `FileRuntimeStateStore`."""

    def test_a_refused_second_create_is_retried_at_attempt_two(self) -> None:
        """(a) Only the MISSING Reviewer is created on the retry; the Worker is adopted."""
        recorder = PairRecorder(scripted_bodies(), create_failures={3: REFUSED_CREATE})
        launch = self.launch(recorder=recorder)
        node = self.node(launch)
        intent = launch.prepared_intent()
        with self.assertRaises(OrcaCommandRefused):
            node(intent)
        self.assertEqual(len(recorder.pair_handles), 1)
        receipt = launch.ledger.get_receipt(intent["pending_intent"]["intent_id"])
        self.assertEqual(receipt["status"], "CLAIMED")

        recorder.create_failures.clear()
        settled = node(intent)
        self.assertEqual(settled["intent_status"], "SETTLED")
        self.assertEqual(len(recorder.pair_handles), 2,
                         "exactly one successful create per role, across both attempts")
        self.assertEqual(len(recorder.pair_titles), 3,
                         "three create COMMANDS: worker, refused reviewer, retried one")
        self.assertEqual(launch.entry("reviewer")["create_attempt"], "2")
        self.assertEqual(launch.entry("reviewer")["stage"], "VERIFIED")
        self.assertEqual(launch.entry("worker")["create_attempt"], "1")
        self.assertEqual(sum(1 for c in recorder.commands
                             if c[1:2] == ("task-create",)), 1)
        self.assertEqual(sum(1 for c in recorder.commands
                             if c[1:2] == ("worker-start",)), 1)
        dispatches = {c for c in recorder.commands if c[1:2] == ("worker-start",)}
        self.assertEqual(len(dispatches), 1)

    def test_a_non_verified_reviewer_creates_nothing_on_the_retry(self) -> None:
        """(b) Both sessions already exist, so the retry adopts both and re-verifies."""
        driver = _RoleScriptedDriver(states={"reviewer": "unverifiable"})
        launch = self.launch(driver=driver)
        node = self.node(launch)
        intent = launch.prepared_intent()
        with self.assertRaises(OrcaRuntimeError):
            node(intent)
        self.assertEqual(len(launch.recorder.pair_handles), 2)
        driver.states.clear()
        settled = node(intent)
        self.assertEqual(settled["intent_status"], "SETTLED")
        self.assertEqual(len(launch.recorder.pair_handles), 2,
                         "NO session is created on the retry")
        self.assertEqual(sum(1 for c in launch.recorder.commands
                             if c[1:2] == ("task-create",)), 1)
        self.assertEqual(sum(1 for c in launch.recorder.commands
                             if c[1:2] == ("worker-start",)), 1)

    def test_a_verification_timeout_creates_nothing_on_the_retry(self) -> None:
        """(c) Same shape as (b), reached through a timeout."""
        failure = subprocess.TimeoutExpired(cmd="claude", timeout=1)
        driver = _RoleScriptedDriver(raises={"reviewer": failure})
        launch = self.launch(driver=driver)
        node = self.node(launch)
        intent = launch.prepared_intent()
        with self.assertRaises(OrcaRuntimeError):
            node(intent)
        driver.raises.clear()
        node(intent)
        self.assertEqual(len(launch.recorder.pair_handles), 2)
        self.assertEqual(sum(1 for c in launch.recorder.commands
                             if c[1:2] == ("worker-start",)), 1)

    def test_a_reopened_store_in_the_same_process_is_not_a_process_boundary(self) -> None:
        """(d) A REOPENED-STORE subcase, named as such and EXPLICITLY NOT a
        cross-process test.

        Freshly constructed store objects over the same paths prove the DURABLE READ and
        nothing whatever about process memory: this test runs in THIS interpreter, which
        still holds the harness's terminal ledger and its model authority.  The real
        new-OS-process evidence is `T7NewProcessRecoveryTests`, which spawns a `python3`
        child with `subprocess.run`."""
        recorder = PairRecorder(scripted_bodies(), create_failures={3: REFUSED_CREATE})
        launch = self.launch(recorder=recorder)
        intent = launch.prepared_intent()
        own_pid = os.getpid()
        with self.assertRaises(OrcaCommandRefused):
            self.node(launch)(intent)

        reopened_ledger = FileRuntimeStateStore(self.root / "ledger.json")
        reopened_prep = pause_store.pair_preparation_for(
            RUN_ID, artifact_base=self.project)
        reopened_binding = pause_store.pair_binding_for(
            RUN_ID, artifact_base=self.project)
        self.assertIsNot(reopened_ledger, launch.ledger)
        self.assertIsNot(reopened_prep, launch.adapter.pair_preparation)
        self.assertEqual(
            reopened_ledger.get_receipt(intent["pending_intent"]["intent_id"])["status"],
            "CLAIMED")
        self.assertEqual(reopened_prep.entry(PHASE, 1, "worker")["stage"], "CREATED")
        self.assertEqual(reopened_prep.entry(PHASE, 1, "reviewer")["stage"],
                         "CREATE_REFUSED")
        self.assertEqual(reopened_binding.binding()["model_aware"], "true")

        recorder.create_failures.clear()
        settled = self.node(launch)(intent)
        self.assertEqual(settled["intent_status"], "SETTLED")
        self.assertEqual(len(recorder.pair_handles), 2)
        self.assertEqual(sum(1 for c in recorder.commands
                             if c[1:2] == ("worker-start",)), 1)
        self.assertEqual(os.getpid(), own_pid,
                         "no process boundary was crossed anywhere in this test")

    def test_preparation_after_create_task_would_not_be_re_entered(self) -> None:
        """The NEGATIVE CONTROL for the placement decision, demonstrated once.

        Preparation sits BEFORE `create_task` precisely because the durable receipt is
        written only after it.  Once a receipt exists the record is EFFECTED, the
        recovery ladder declines `external_resume`, and the run stops at
        `IDEMPOTENCY_RECOVERY_UNSUPPORTED` without re-entering preparation at all."""
        launch = self.launch()
        node = self.node(launch)
        intent = launch.prepared_intent()
        node(intent)
        creates_after_first = len(launch.recorder.pair_handles)
        pending = intent["pending_intent"]
        record = launch.ledger.get_receipt(pending["intent_id"])
        self.assertEqual(record["status"], "SETTLED")
        self.assertTrue(record["receipt"].get("task_id"),
                        "the durable receipt is written only AFTER create_task")
        self.assertEqual(len(launch.recorder.pair_handles), creates_after_first)
        self.assertNotIn("external_resume", launch.adapter.capabilities(),
                         "the adapter declines external_resume, so a record that has "
                         "reached EFFECTED stops at IDEMPOTENCY_RECOVERY_UNSUPPORTED "
                         "without re-entering preparation")


# ======================================================================================
# T7  M4: the record is re-read in a NEW OS PROCESS
# ======================================================================================
#: The child driver script.  It is executed by `subprocess.run([sys.executable, ...])`,
#: so every assertion T7 makes about "a successor process" is made about a REAL new
#: Python process with its own memory -- never about a store reopened in this one.  It
#: spawns no `claude`, no `codex` and no Orca session: its only process is itself.
CHILD_SCRIPT = r'''
import json, os, sys
from pathlib import Path

options = json.loads(sys.argv[1])
sys.path.insert(0, options["repo_root"])

from scripts.deterministic_workflow import launcher
from scripts.deterministic_workflow.fake_adapter import InProcessModelDriver
from scripts.deterministic_workflow.runtime_state import ManualLeaseClock
from scripts.test_os14_pair_preparation import (ClaimAuditingStore, PairRecorder,
                                                scripted_bodies)

project = Path(options["project"])
os.environ["PATH"] = options["path"]

recorder = PairRecorder(scripted_bodies(options["run_id"]),
                        run_id=options["run_id"],
                        listing=options["listing"],
                        worktree_id=options["worktree_id"])
drift = options.get("drift") or {}
if options["mode"] != "rebound":
    driver = None
elif options.get("driver_class") == "attesting":
    from scripts.test_os14_pair_preparation import _AttestingDriver
    driver = _AttestingDriver(resolve_map=drift)
else:
    driver = InProcessModelDriver(
        resolve=lambda requested: drift.get(requested, requested))


def harness_factory(artifact_base, **kwargs):
    from scripts.orca_runtime_harness import OrcaRuntimeHarness
    os.environ["ORCA_CLI_COMMAND"] = "/opt/orca-dev"
    harness = OrcaRuntimeHarness(Path(artifact_base), **kwargs)
    harness._exec_orca = recorder
    harness.preflight = lambda: {}
    return harness


report = {"construction_error": None, "execute_error": None}
#: The PREDECESSOR's ledger, at its exact path -- this is the whole point of T7: the
#: successor must take over the durable execution claim the dead process left behind, not
#: start a workflow of its own.  The clock is the store's own documented injection point
#: and it is set ONE FULL LEASE past the predecessor's claim, which is the real-world
#: condition of a successor: the dead owner stopped renewing and its lease lapsed.  Using
#: the clock rather than sleeping is what makes `CLAIMED -> RESUMED` DETERMINISTIC instead
#: of a race against `DEFAULT_LEASE_SECONDS` of wall time.
ledger = ClaimAuditingStore(Path(options["ledger"]),
                            clock=ManualLeaseClock(options["clock_start"]))
try:
    adapter = launcher.build_orca_adapter_for_run(
        options["run_id"], artifact_base=project, runtime_state=ledger,
        run_owner="term_child_owner", project_root=project,
        harness_factory=harness_factory,
        agent_profile_name=("split" if options["mode"] == "rebound" else ""),
        model_driver=driver)
except Exception as exc:                      # reported, never swallowed
    report["construction_error"] = f"{type(exc).__name__}: {exc}"
    adapter = None

#: THE LOOKUP RUNG, observed.  `executor._recover` reaches `adapter.lookup` only for a
#: RESUMED record that is still CLAIMED -- it is the rung that PROVES whether an external
#: effect exists before anything may be re-run -- and a `CREATED` claim never reaches it.
#: This wrapper calls the production method and returns its answer UNCHANGED; it decides
#: nothing, exactly like `ClaimAuditingStore`.
lookup_log = []
if adapter is not None:
    _production_lookup = adapter.lookup

    def _observed_lookup(intent):
        found = _production_lookup(intent)
        lookup_log.append([intent["intent_id"], found is None])
        return found

    adapter.lookup = _observed_lookup

    state = launcher.build_state({"run_id": options["run_id"], "thread_id": "os14",
                                  "phases": options["phases"],
                                  "risk": options["risk"]})
    try:
        final = launcher.execute_state(state, adapter=adapter, runtime_state=ledger,
                                       artifact_base=project)
        report["terminal_status"] = final.get("terminal_status")
        report["terminal_reason"] = final.get("terminal_reason")
        report["exit_code"] = launcher.summarize(final)["exit_code"]
    except BaseException as exc:              # reported, never swallowed
        report["execute_error"] = f"{type(exc).__name__}: {exc}"

report["pair_creates"] = len(recorder.pair_titles)
report["pair_handles"] = list(recorder.pair_handles)
report["task_creates"] = sum(1 for c in recorder.commands
                             if c[1:2] == ("task-create",))
report["worker_starts"] = sum(1 for c in recorder.commands
                              if c[1:2] == ("worker-start",))
#: The Task each `worker-start` was issued against: a SET, so a Task delivered twice
#: would shrink it below `worker_starts`.
report["started_task_ids"] = sorted({c[c.index("--task") + 1] for c in recorder.commands
                                     if c[1:2] == ("worker-start",) and "--task" in c})
report["sends"] = recorder.count("terminal", "send")
report["driver_legs"] = [] if driver is None else list(driver.requests)
report["registered_from_test_code"] = False
#: THE RECOVERY AUDIT.  `claim_log` is what the durable store ANSWERED this process, in
#: order; `[..., "RESUMED"]` for the predecessor's intent is the proof that this child took
#: over an existing CLAIMED record instead of opening a fresh one.  `lookup_rungs` is the
#: ladder's LOOKUP rung -- the rung only a RESUMED-and-still-CLAIMED record reaches
#: (`executor._recover`) -- with `True` meaning the lookup PROVED no Task existed.
#: `task_list_reads` counts the raw `orchestration task-list` commands, of which the rung
#: is one; it is used only to place the rung BEFORE any external effect.
report["claim_log"] = [list(item) for item in ledger.claim_log]
report["ledger_owner"] = ledger.owner_id
_lookups = [i for i, c in enumerate(recorder.commands)
            if c[:2] == ("orchestration", "task-list")]
_first_task_create = next((i for i, c in enumerate(recorder.commands)
                           if c[1:2] == ("task-create",)), None)
report["lookup_rungs"] = lookup_log
report["task_list_reads"] = len(_lookups)
report["lookup_preceded_every_effect"] = bool(_lookups) and (
    _first_task_create is None or _lookups[0] < _first_task_create)
#: Proof that this really is a new OS process rather than a reopened store.
report["pid"] = os.getpid()
print("RESULT_JSON " + json.dumps(report))
'''


class ChildProcessRoom(PairRoom):
    """A `PairRoom` that can run a predecessor to a named boundary and then spawn a REAL
    successor `python3` process over the same durable documents AND the same durable
    EXECUTION LEDGER.

    Carries no test method of its own, so no subclass re-runs another's cases.

    THE LEDGER IS SHARED, and that is the point (review F-001).  The child is pointed at
    the predecessor's exact `FileRuntimeStateStore` path, so its first `claim` meets the
    predecessor's own `CLAIMED` record and must answer `RESUMED`; the executor then runs
    the real recovery ladder -- `RESUMED` -> `_recover` -> the LOOKUP rung
    (`orca orchestration task-list --run`) -> `_settle_now` re-entry -- instead of opening
    a fresh workflow.  A child over a FRESH ledger would prove only that two JSON
    documents can be re-read by another interpreter.

    The lapse is produced by the store's own documented clock injection rather than by
    sleeping: each child's clock starts one full `DEFAULT_LEASE_SECONDS` beyond the
    previous process's, which is exactly the real-world condition of a successor -- the
    dead owner stopped renewing and its lease ran out -- and makes the takeover
    deterministic instead of a race against wall time.  Nothing else about the predecessor
    is altered: it claims with the production default lease and the production clock.

    Apart from the ledger the child is handed ONLY facts about the world OUTSIDE both
    processes -- the terminal listing and the worktree identity a real runtime would answer
    with.  It is handed NO ledger row for a prepared session (that row is exactly what
    PRODUCTION adoption must create) and NO model authority (no durable record carries any,
    and `resume_run` restores none).
    """

    #: The ledger file every process in one test shares.
    LEDGER_NAME = "ledger.json"

    def setUp(self) -> None:
        super().setUp()
        #: Each spawned child sits one full lease beyond the previous process, so a
        #: SECOND child (T7(g) runs two) takes over the FIRST child's claim just as
        #: legitimately as the first took over the predecessor's.
        self._epoch = time.time()
        self._generation = 0

    def predecessor(self, *, stop_at, drift=None, risk="high", bodies=None):
        """Run a predecessor to the named boundary and let it exit.

        Every predecessor is driven through `launcher.execute_state`, so the run has a
        REAL committed checkpoint head carrying its own `requested_phases` and `risk` --
        which is what makes the successor's `declared_run_identity_for_run` read feasible
        rather than fixture-supplied.
        """
        # The predecessor's driver must be the SAME CLASS the child injects, because the
        # driver class is part of the launch identity by design -- a different class is
        # refused `PAIR_PREPARATION_BINDING_MISMATCH` before any effect (T9(n)).  So the
        # stop point is produced by the REFERENCE driver's own satisfaction rule: a
        # resolution that differs from the request makes it report `mismatch` itself.
        recorder = PairRecorder(scripted_bodies(),
                                fail_verbs={"current"} if stop_at == "B0" else None)
        unsatisfiable = {"B0": {},
                         "B7": {"glm-5.2": "glm-5.9-unsatisfied"},
                         "B8": {"glm-5.3-flash": "glm-5.9-unsatisfied"}}
        if stop_at not in unsatisfiable:           # pragma: no cover - a typo guard
            raise AssertionError(stop_at)
        mapping = unsatisfiable[stop_at]
        if stop_at == "B8":
            # The drift subcases need a driver whose ATTESTATION survives a changed
            # resolution, for the reason `_AttestingDriver`'s docstring gives.
            driver = _AttestingDriver(refuse_roles={"reviewer"})
        else:
            driver = InProcessModelDriver(
                resolve=lambda requested: mapping.get(requested, requested))
        if bodies is not None:
            recorder.bodies = list(bodies)
        launch = self.launch(recorder=recorder, driver=driver, risk=risk)
        try:
            self.execute(launch)
        except BaseException:                     # the predecessor "dies" here
            pass
        self.assertIsNotNone(
            self._head(), "the predecessor committed a real checkpoint head")
        return launch

    def _head(self):
        from scripts.deterministic_workflow import recovery_runtime
        return recovery_runtime.resolve_head(RUN_ID, artifact_base=self.project)

    def predecessor_at_create_boundary(self, *, ordinal):
        """A predecessor that really created `ordinal` sessions and died between the
        `ordinal`-th `terminal create` returning and its `CREATED` result-record -- the
        B3 (ordinal 1) and B6 (ordinal 2) windows.

        The interruption is placed by making the `CREATED` write itself fail, so the
        create really happened and its durable result really did not; the run is driven
        through `launcher.execute_state`, so the predecessor leaves a real committed
        checkpoint head behind exactly as every other predecessor does.
        """
        launch = self.launch()
        store = launch.adapter.pair_preparation
        original = store.record
        seen = {"creates": 0}

        def failing_record(phase, iteration, role, *, stage, **fields):
            if stage == "CREATED":
                seen["creates"] += 1
                if seen["creates"] == ordinal:
                    raise RuntimeError("the process died before the CREATED write")
            return original(phase, iteration, role, stage=stage, **fields)

        store.record = failing_record
        try:
            self.execute(launch)
        except BaseException:                     # the predecessor "dies" here
            pass
        store.record = original
        self.assertEqual(len(launch.recorder.pair_handles), ordinal,
                         f"{ordinal} session(s) came into existence")
        self.assertIsNotNone(
            self._head(), "the predecessor committed a real checkpoint head")
        return launch

    def predecessor_at_admission(self):
        """A predecessor with BOTH roles `VERIFIED` that died at the admission boundary,
        before `create_task` -- the B9 window.  No Task was issued, so a successor's
        lookup can prove absence honestly."""
        launch = self.launch()
        original = launch.harness.create_task
        seen = {"calls": 0}

        def failing_create_task(spec, **kwargs):
            seen["calls"] += 1
            if seen["calls"] == 1:
                raise RuntimeError("the process died at the admission boundary")
            return original(spec, **kwargs)

        launch.harness.create_task = failing_create_task
        try:
            self.execute(launch)
        except BaseException:                     # the predecessor "dies" here
            pass
        launch.harness.create_task = original
        self.assertEqual(launch.entry("worker")["stage"], "VERIFIED")
        self.assertEqual(launch.entry("reviewer")["stage"], "VERIFIED")
        self.assertEqual(len(launch.recorder.pair_handles), 2)
        self.assertEqual(sum(1 for c in launch.recorder.commands
                             if c[1:2] == ("task-create",)), 0,
                         "the predecessor issued NO task-create")
        self.assertIsNotNone(
            self._head(), "the predecessor committed a real checkpoint head")
        return launch

    def child(self, *, mode, listing, drift=None, risk="high",
              ledger_name=None, driver_class="reference"):
        """Spawn the successor process over the predecessor's OWN ledger."""
        self._generation += 1
        options = {
            "repo_root": str(REPO_ROOT), "project": str(self.project),
            "ledger": str(self.root / (ledger_name or self.LEDGER_NAME)),
            "clock_start": self._epoch + (DEFAULT_LEASE_SECONDS + 1.0) * self._generation,
            "run_id": RUN_ID,
            "listing": listing, "worktree_id": "repo_os14::/project",
            "mode": mode, "drift": drift or {}, "phases": [PHASE_UPPER], "risk": risk,
            "path": str(self.binaries), "driver_class": driver_class,
        }
        script = self.root / "child.py"
        script.write_text(CHILD_SCRIPT, encoding="utf-8")
        completed = subprocess.run(
            [sys.executable, str(script), json.dumps(options)],
            capture_output=True, text=True, cwd=str(REPO_ROOT), timeout=300)
        marker = "RESULT_JSON "
        line = next((line for line in completed.stdout.splitlines()
                     if line.startswith(marker)), None)
        self.assertIsNotNone(
            line, f"child produced no report\nSTDOUT:{completed.stdout}\n"
                  f"STDERR:{completed.stderr}")
        report = json.loads(line[len(marker):])
        self.assertNotEqual(report["pid"], os.getpid(),
                            "this subcase must run in a REAL separate OS process")
        return report

    def listing_of(self, launch):
        """The sessions the predecessor really created, as the world outside reports
        them.  The handles are the REAL ones, so `terminal_digest(handle)` genuinely
        matches the stored digest and the resolver reaches `listing_verified` honestly."""
        return [{"handle": handle, "title": title, "orphaned": False}
                for handle, title in launch.recorder.created_pairs]

    # -- the cross-process RECOVERY assertions (review F-001) -------------------------
    def interrupted_intent_id(self, launch):
        """The id of the intent the predecessor CLAIMED and then died inside.

        Derived by the ENGINE's own nodes from the predecessor's own state -- never
        hand-built -- so it is the same stable identity the successor's rebuilt state
        produces, which is what makes the takeover possible at all.
        """
        return launch.prepared_intent()["pending_intent"]["intent_id"]

    def assertLiveUnsettledClaim(self, launch, intent_id):
        """The predecessor really did leave a CLAIMED, unsettled, un-receipted record."""
        record = launch.ledger.get_receipt(intent_id)
        self.assertIsNotNone(record, "the predecessor CLAIMED this intent before dying")
        self.assertEqual(record["status"], "CLAIMED",
                         "a CLAIMED record names no external effect, which is what sends "
                         "a successor to the LOOKUP rung")
        self.assertIsNone(record["receipt"])
        self.assertIsNone(record["settlement"])
        return record

    def assertResumedThatClaim(self, report, launch, intent_id, before):
        """The child TOOK OVER the predecessor's claim and ran the real ladder.

        `RESUMED` (never `CREATED`) for the predecessor's own intent id, the LOOKUP rung
        read exactly once and strictly before any external effect, a rotated lease under a
        DIFFERENT owner, and every stable identity cell unchanged -- i.e. the same record,
        re-owned, not a new one.
        """
        self.assertEqual(report["claim_log"][:1], [[intent_id, "RESUMED"]],
                         f"the child's FIRST claim must RESUME the predecessor's own "
                         f"record, not open a new one; log={report['claim_log']}")
        self.assertNotIn("CREATED", [outcome for claimed, outcome in report["claim_log"]
                                     if claimed == intent_id])
        self.assertEqual(report["lookup_rungs"], [[intent_id, True]],
                         "`_recover` took the LOOKUP rung exactly once, for the one "
                         "CLAIMED record it inherited, and the lookup PROVED no Task "
                         f"existed before anything was re-run; got {report['lookup_rungs']}")
        self.assertTrue(report["lookup_preceded_every_effect"],
                        "the Task listing was read before any external effect")
        after = launch.ledger.get_receipt(intent_id)
        self.assertEqual(after["owner_id"], report["ledger_owner"])
        self.assertNotEqual(after["owner_id"], before["owner_id"],
                            "a successor PROCESS, not the predecessor")
        self.assertNotEqual(after["lease_token"], before["lease_token"],
                            "the takeover rotated the lease token (the fence)")
        for cell in ("intent_id", "command_id", "payload_digest", "run_id", "phase",
                     "role", "round_kind"):
            self.assertEqual(after[cell], before[cell],
                             f"the SAME record was re-owned, not replaced ({cell})")
        return after

    def assertOneEffectPerIntent(self, launch, report, *, predecessor_effects=0):
        """Exactly one Task, one Dispatch and one settlement per intent, across BOTH
        processes -- read back through the ordinary public store API.

        `report["claim_log"]` names every intent this run executed, so the check is over
        the run's real intent set rather than a hard-coded list.
        """
        intents = [claimed for claimed, _outcome in report["claim_log"]]
        self.assertEqual(len(set(intents)), len(intents),
                         "no intent was claimed twice in one process")
        tasks, dispatches, events = [], [], []
        for intent_id in intents:
            record = launch.ledger.get_receipt(intent_id)
            self.assertEqual(record["status"], "SETTLED", intent_id)
            tasks.append(record["receipt"]["task_id"])
            dispatches.append(record["receipt"]["dispatch_id"])
            events.append(launch.ledger.get_settlement(intent_id)["event_id"])
        self.assertEqual(len(set(tasks)), len(intents), "one Task per intent")
        self.assertEqual(len(set(dispatches)), len(intents), "one Dispatch per intent")
        self.assertEqual(len(set(events)), len(intents), "one settlement per intent")
        predecessor = sum(1 for c in launch.recorder.commands
                          if c[1:2] == ("task-create",))
        self.assertEqual(predecessor, predecessor_effects,
                         "the predecessor's own Task count, asserted off ITS recorder")
        self.assertEqual(report["task_creates"] + predecessor, len(intents),
                         "one `task-create` per intent ACROSS BOTH PROCESSES")
        self.assertEqual(report["worker_starts"] + predecessor, len(intents),
                         "one `worker-start` per intent ACROSS BOTH PROCESSES")
        self.assertEqual(len(report["started_task_ids"]), report["worker_starts"],
                         "every delivery was issued against its own Task; none was reused")
        return intents


@REQUIRES_LANGGRAPH
class T7NewProcessRecoveryTests(ChildProcessRoom):
    """A REAL separate `python3` process re-reads the durable documents AND takes over the
    predecessor's durable EXECUTION CLAIM.

    The child runs over the predecessor's own `FileRuntimeStateStore` path, so every
    subcase below asserts the real recovery ladder -- `CLAIMED` -> `RESUMED` -> the LOOKUP
    rung -> `_settle_now` re-entry -- and not merely that two JSON documents can be re-read
    by another interpreter.  See `assertResumedThatClaim`.

    The child is handed ONLY facts about the world OUTSIDE both processes -- the terminal
    listing and the worktree identity a real runtime would answer with.  It is handed NO
    ledger row for a prepared session (that row is exactly what PRODUCTION adoption must
    create) and NO model authority (no durable record carries any, and `resume_run`
    restores none).

    The reusable half -- running a predecessor to a named boundary and spawning the
    child -- lives on `ChildProcessRoom`, which carries no test method of its own, so no
    subclass re-runs another class's subcases.
    """

    # -- (a) ------------------------------------------------------------------------
    def test_a_successor_recovers_by_digest_and_re_verifies_in_its_own_process(self):
        """(a) The whole cross-process guarantee, on the predecessor's OWN ledger:
        `CLAIMED` -> `RESUMED` -> LOOKUP -> `_settle_now` re-entry, with no duplicate
        session, Task, Dispatch or settlement."""
        launch = self.predecessor(stop_at="B7")
        self.assertEqual(launch.entry("worker")["stage"], "CREATED")
        self.assertEqual(launch.entry("reviewer")["stage"], "CREATED")
        interrupted = self.interrupted_intent_id(launch)
        before = self.assertLiveUnsettledClaim(launch, interrupted)
        report = self.child(mode="rebound", listing=self.listing_of(launch))
        self.assertIsNone(report["construction_error"])
        self.assertIsNone(report["execute_error"], report.get("execute_error"))
        self.assertEqual(report["terminal_status"], "COMPLETED",
                         report.get("terminal_reason"))
        self.assertResumedThatClaim(report, launch, interrupted, before)
        self.assertEqual(report["pair_creates"], 0, "nothing was re-created")
        self.assertGreaterEqual(len(report["driver_legs"]), 4,
                                "BOTH identities were re-verified in the CHILD process")
        self.assertEqual(launch.entry("worker")["stage"], "VERIFIED")
        self.assertEqual(len(launch.recorder.pair_handles), 2,
                         "the predecessor's two sessions are RETAINED")
        intents = self.assertOneEffectPerIntent(launch, report)
        self.assertEqual(intents[0], interrupted,
                         "the RECOVERED intent is the run's first, and it got exactly "
                         "ONE Task, ONE Dispatch and ONE settlement across both processes")

    # -- (b) ------------------------------------------------------------------------
    def test_a_title_match_the_digest_contradicts_blocks_by_name(self) -> None:
        launch = self.predecessor(stop_at="B7")
        listing = [{**row, "handle": f"other_{row['handle']}"}
                   for row in self.listing_of(launch)]
        interrupted = self.interrupted_intent_id(launch)
        before = self.assertLiveUnsettledClaim(launch, interrupted)
        report = self.child(mode="rebound", listing=listing)
        self.assertEqual(report["terminal_status"], "BLOCKED")
        self.assertEqual(report["terminal_reason"]["code"],
                         "PAIR_PREPARATION_SESSION_UNVERIFIED")
        self.assertResumedThatClaim(report, launch, interrupted, before)
        self.assertEqual([report["pair_creates"], report["task_creates"],
                          report["worker_starts"], report["sends"]], [0, 0, 0, 0])
        self.assertEqual(launch.ledger.get_receipt(interrupted)["status"], "CLAIMED",
                         "a blocked recovery leaves the claim unsettled and un-receipted")

    # -- (c) ------------------------------------------------------------------------
    def test_a_provable_absence_blocks_by_name(self) -> None:
        launch = self.predecessor(stop_at="B7")
        interrupted = self.interrupted_intent_id(launch)
        before = self.assertLiveUnsettledClaim(launch, interrupted)
        report = self.child(mode="rebound", listing=[])
        self.assertEqual(report["terminal_status"], "BLOCKED")
        self.assertEqual(report["terminal_reason"]["code"],
                         "PAIR_PREPARATION_SESSION_ABSENT")
        self.assertResumedThatClaim(report, launch, interrupted, before)
        self.assertEqual([report["pair_creates"], report["task_creates"],
                          report["worker_starts"], report["sends"]], [0, 0, 0, 0])

    # -- (d) ------------------------------------------------------------------------
    def test_the_shipped_recovery_construction_blocks_instead_of_delivering(self) -> None:
        """The DESIGNED outcome replacing today's silent unverified resumed delivery."""
        launch = self.predecessor(stop_at="B7")
        interrupted = self.interrupted_intent_id(launch)
        before = self.assertLiveUnsettledClaim(launch, interrupted)
        report = self.child(mode="shipped", listing=self.listing_of(launch))
        self.assertIsNone(report["construction_error"])
        self.assertEqual(report["terminal_status"], "BLOCKED")
        self.assertEqual(report["terminal_reason"]["code"],
                         "PAIR_PREPARATION_BINDING_UNVERIFIABLE")
        self.assertResumedThatClaim(report, launch, interrupted, before)
        self.assertEqual([report["pair_creates"], report["task_creates"],
                          report["worker_starts"], report["sends"]], [0, 0, 0, 0])

    # -- (e) / (f) --------------------------------------------------------------------
    def test_the_b0_window_blocks_under_the_shipped_construction(self) -> None:
        launch = self.predecessor(stop_at="B0")
        self.assertEqual(len(launch.recorder.pair_handles), 0)
        self.assertFalse(
            (launch.run_root() / ".pair_preparation.json").exists(),
            "nothing was prepared in the B0 window")
        interrupted = self.interrupted_intent_id(launch)
        before = self.assertLiveUnsettledClaim(launch, interrupted)
        report = self.child(mode="shipped", listing=[])
        self.assertEqual(report["terminal_status"], "BLOCKED")
        self.assertEqual(report["terminal_reason"]["code"],
                         "PAIR_PREPARATION_BINDING_UNVERIFIABLE")
        self.assertResumedThatClaim(report, launch, interrupted, before)
        self.assertEqual([report["pair_creates"], report["task_creates"],
                          report["worker_starts"], report["sends"]], [0, 0, 0, 0])

    def test_the_b0_window_recovers_under_a_re_bound_construction(self) -> None:
        """(f) The positive control: the child RESUMES the predecessor's claim and
        prepares both roles from scratch, because nothing had been created yet."""
        launch = self.predecessor(stop_at="B0")
        interrupted = self.interrupted_intent_id(launch)
        before = self.assertLiveUnsettledClaim(launch, interrupted)
        report = self.child(mode="rebound", listing=[])
        self.assertIsNone(report["construction_error"])
        self.assertEqual(report["terminal_status"], "COMPLETED",
                         report.get("terminal_reason") or report.get("execute_error"))
        self.assertResumedThatClaim(report, launch, interrupted, before)
        self.assertEqual(report["pair_creates"], 2)
        self.assertEqual(len(launch.recorder.pair_handles), 0,
                         "the two processes together created exactly the child's 2")
        intents = self.assertOneEffectPerIntent(launch, report)
        self.assertEqual(intents[0], interrupted,
                         "the RECOVERED intent produced exactly ONE Task / Dispatch / "
                         "settlement across both processes -- no duplicate of the claim "
                         "the predecessor died inside")

    # -- (g) ------------------------------------------------------------------------
    def test_a_lost_launch_record_blocks_for_both_constructions(self) -> None:
        """(g) An adoption never writes a launch record, so it cannot repair an absence."""
        launch = self.predecessor(stop_at="B0")
        record = self.project / "artifacts" / "runs" / RUN_ID / ".pair_launch_binding.json"
        self.assertTrue(record.exists())
        record.unlink()
        interrupted = self.interrupted_intent_id(launch)
        before = self.assertLiveUnsettledClaim(launch, interrupted)
        for mode in ("shipped", "rebound"):
            with self.subTest(mode=mode):
                # Both children run over the SAME ledger, each one lease further on, so
                # the second takes over the first child's claim exactly as legitimately
                # as the first took over the predecessor's.
                report = self.child(mode=mode, listing=[])
                self.assertEqual(report["terminal_status"], "BLOCKED")
                self.assertEqual(report["terminal_reason"]["code"],
                                 "PAIR_PREPARATION_LAUNCH_RECORD_ABSENT")
                before = self.assertResumedThatClaim(report, launch, interrupted, before)
                self.assertEqual([report["pair_creates"], report["task_creates"],
                                  report["worker_starts"], report["sends"]],
                                 [0, 0, 0, 0])
                self.assertFalse(record.exists(),
                                 "the adoption did NOT repair the absence")

    # -- (h) / (i) --------------------------------------------------------------------
    def test_a_changed_resolved_model_blocks_and_preserves_the_observation(self) -> None:
        """(h) The cross-process drift block, and the F-001 regression assertion."""
        launch = self.predecessor(stop_at="B8")
        observed = launch.entry("worker")["resolved_model_observed"]
        self.assertEqual(observed, "glm-5.2")
        self.assertEqual(launch.entry("worker")["stage"], "VERIFIED")
        self.assertEqual(launch.entry("reviewer")["stage"], "CREATED")
        interrupted = self.interrupted_intent_id(launch)
        before = self.assertLiveUnsettledClaim(launch, interrupted)
        report = self.child(mode="rebound", listing=self.listing_of(launch),
                            drift={"glm-5.2": "glm-5.9-drifted"},
                            driver_class="attesting")
        self.assertEqual(report["terminal_status"], "BLOCKED",
                         report.get("execute_error"))
        self.assertEqual(report["terminal_reason"]["code"],
                         "PAIR_PREPARATION_MODEL_DRIFT")
        self.assertResumedThatClaim(report, launch, interrupted, before)
        self.assertEqual([report["pair_creates"], report["task_creates"],
                          report["worker_starts"], report["sends"]], [0, 0, 0, 0])
        self.assertGreaterEqual(len(report["driver_legs"]), 2,
                                "the block came from a REAL re-verification")
        after = pause_store.pair_preparation_for(
            RUN_ID, artifact_base=self.project).entry(PHASE, 1, "worker")
        self.assertEqual(after["resolved_model_observed"], observed,
                         "the prior observation was PRESERVED, never overwritten")

    def test_an_equal_resolved_model_recovers_and_refreshes(self) -> None:
        """(i) The equal-model positive control."""
        launch = self.predecessor(stop_at="B8")
        before = pause_store.pair_preparation_for(
            RUN_ID, artifact_base=self.project).entry(PHASE, 1, "worker")
        interrupted = self.interrupted_intent_id(launch)
        claimed = self.assertLiveUnsettledClaim(launch, interrupted)
        report = self.child(mode="rebound", listing=self.listing_of(launch),
                            driver_class="attesting")
        self.assertEqual(report["terminal_status"], "COMPLETED",
                         report.get("terminal_reason") or report.get("execute_error"))
        self.assertResumedThatClaim(report, launch, interrupted, claimed)
        self.assertEqual(report["pair_creates"], 0,
                         "the child registered no terminal of its own")
        intents = self.assertOneEffectPerIntent(launch, report)
        self.assertEqual(intents[0], interrupted)
        after = pause_store.pair_preparation_for(
            RUN_ID, artifact_base=self.project).entry(PHASE, 1, "worker")
        self.assertEqual(after["resolved_model_observed"], "glm-5.2")
        self.assertTrue(after["resolved_model_observed"])
        self.assertEqual(after["observed_at_run"], RUN_ID)
        self.assertGreaterEqual(after["verified_at"], before["verified_at"])


# ======================================================================================
# T8  M5: interruption at every boundary
# ======================================================================================
class T8BoundaryInterruptionTests(PairRoom):
    """Each boundary is interrupted through the recorder / driver seam, then re-entered.

    Counts are stated PER SETUP and read off this test's own recorder.
    """

    def test_before_the_first_create_blocks_as_unknown(self) -> None:
        """(a) The entry sits at CREATE_INTENDED: a write landed, no effect followed."""
        launch = self.launch(create_failures={2: UNPARSED_CREATE})
        node = self.node(launch)
        intent = launch.prepared_intent()
        with self.assertRaises(executor.IdempotencyRecoveryError) as caught:
            node(intent)
        self.assertEqual(caught.exception.code, "PAIR_PREPARATION_OUTCOME_UNKNOWN")
        self.assertEqual(launch.entry("worker")["stage"], "CREATE_INTENDED")
        self.assertEqual(len(launch.recorder.pair_handles), 0)
        with self.assertRaises(executor.IdempotencyRecoveryError) as again:
            node(intent)
        self.assertEqual(again.exception.code, "PAIR_PREPARATION_OUTCOME_UNKNOWN")
        self.assertEqual(len(launch.recorder.pair_handles), 0)
        self.assertEqual(sum(1 for c in launch.recorder.commands
                             if c[1:2] == ("task-create",)), 0)

    def test_the_listing_is_reported_and_never_changes_the_unknown_verdict(self) -> None:
        """(a), the second half: `not_listed` under a CREATE_INTENDED entry is NOT
        read as absence."""
        launch = self.launch(create_failures={2: UNPARSED_CREATE})
        node = self.node(launch)
        intent = launch.prepared_intent()
        with self.assertRaises(executor.IdempotencyRecoveryError):
            node(intent)
        before = launch.recorder.count("terminal", "list")
        with self.assertRaises(executor.IdempotencyRecoveryError) as again:
            node(intent)
        self.assertEqual(again.exception.code, "PAIR_PREPARATION_OUTCOME_UNKNOWN")
        self.assertGreater(launch.recorder.count("terminal", "list"), before,
                           "the listing WAS read, so the detail can report it")
        self.assertIn("handle_recovery=", again.exception.detail)
        self.assertEqual(len(launch.recorder.pair_handles), 0)

    def test_after_the_first_create_before_its_record_blocks_as_unknown(self) -> None:
        """(b) A session may exist: neither success nor confirmed absence.

        The interruption is placed by making the `CREATED` write itself fail, so the
        create really happened and its result-record really did not."""
        launch = self.launch()
        node = self.node(launch)
        intent = launch.prepared_intent()
        store = launch.adapter.pair_preparation
        original = store.record
        state = {"creates": 0}

        def failing_record(phase, iteration, role, *, stage, **fields):
            if stage == "CREATED":
                state["creates"] += 1
                if state["creates"] == 1:
                    raise RuntimeError("the process died before the CREATED write")
            return original(phase, iteration, role, stage=stage, **fields)

        store.record = failing_record
        with self.assertRaises(RuntimeError):
            node(intent)
        store.record = original
        self.assertEqual(len(launch.recorder.pair_handles), 1,
                         "ONE session came into existence")
        self.assertEqual(launch.entry("worker")["stage"], "CREATE_INTENDED")
        with self.assertRaises(executor.IdempotencyRecoveryError) as caught:
            node(intent)
        self.assertEqual(caught.exception.code, "PAIR_PREPARATION_OUTCOME_UNKNOWN")
        self.assertEqual(len(launch.recorder.pair_handles), 1,
                         "the re-entry creates NO second session")
        self.assertEqual(sum(1 for c in launch.recorder.commands
                             if c[1:2] == ("task-create",)), 0)

    def test_between_the_two_creates_adopts_one_and_creates_the_other(self) -> None:
        """(a') AFTER the FIRST session's result-record, BEFORE the SECOND session's
        create -- the one window of the request's "각 세션 생성 전후, 결과 기록 전후"
        list that the other five subcases do not reach.

        (a) and (b) interrupt before the FIRST create and before its record; (c)
        interrupts after the SECOND create.  None of them leaves the asymmetric state
        this window leaves: one role DIGEST-PROVED on disk, the other with NO entry at
        all.  That pairing drives `resolve_prepared_terminal` down two DIFFERENT
        branches in one pass -- `adopt` for the worker, `create` at `create_attempt == 1`
        for the reviewer -- which is a distinct path from T6(a), where the reviewer's
        `CREATE_REFUSED` entry makes the retry `create_attempt == 2`.

        The interruption is placed by failing the REVIEWER's `CREATE_INTENDED` write, so
        the worker's `CREATED` record really landed and the reviewer's create really
        never happened.
        """
        launch = self.launch()
        node = self.node(launch)
        intent = launch.prepared_intent()
        store = launch.adapter.pair_preparation
        original = store.record
        state = {"intents": 0}

        def failing_record(phase, iteration, role, *, stage, **fields):
            if stage == "CREATE_INTENDED":
                state["intents"] += 1
                if state["intents"] == 2:
                    raise RuntimeError("the process died before the reviewer's create")
            return original(phase, iteration, role, stage=stage, **fields)

        store.record = failing_record
        with self.assertRaises(RuntimeError):
            node(intent)
        store.record = original
        self.assertEqual(launch.entry("worker")["stage"], "CREATED",
                         "the FIRST role's result-record landed")
        self.assertTrue(launch.entry("worker")["terminal_digest"])
        self.assertIsNone(launch.entry("reviewer"),
                          "the SECOND role has no entry at all: its intent never landed")
        self.assertEqual(len(launch.recorder.pair_handles), 1,
                         "exactly ONE session came into existence in this setup")
        self.assertEqual(sum(1 for c in launch.recorder.commands
                             if c[1:2] == ("task-create",)), 0)

        worker_handle = launch.recorder.pair_handles[0]
        settled = node(intent)
        self.assertEqual(settled["intent_status"], "SETTLED")
        self.assertEqual(len(launch.recorder.pair_handles), 2,
                         "the retry created ONLY the missing reviewer: one session "
                         "per role across BOTH attempts")
        self.assertEqual(launch.recorder.pair_handles[0], worker_handle,
                         "the worker session was ADOPTED by digest, never re-created")
        self.assertEqual(launch.entry("worker")["terminal_digest"],
                         pause_policy.terminal_digest(worker_handle))
        self.assertEqual(launch.entry("worker")["create_attempt"], "1")
        self.assertEqual(launch.entry("reviewer")["create_attempt"], "1",
                         "an ABSENT entry starts at attempt 1, unlike a CREATE_REFUSED "
                         "one, which starts at 2")
        self.assertEqual(launch.entry("worker")["stage"], "VERIFIED")
        self.assertEqual(launch.entry("reviewer")["stage"], "VERIFIED")
        self.assertEqual(sum(1 for c in launch.recorder.commands
                             if c[1:2] == ("task-create",)), 1)
        self.assertEqual(sum(1 for c in launch.recorder.commands
                             if c[1:2] == ("worker-start",)), 1)
        record = launch.ledger.get_receipt(intent["pending_intent"]["intent_id"])
        self.assertEqual(record["status"], "SETTLED")
        self.assertEqual(len({record["receipt"]["dispatch_id"]}), 1)
    def test_after_the_second_create_before_its_record_blocks_as_unknown(self) -> None:
        """(c) The same, one role later: two sessions exist, one record is missing."""
        launch = self.launch()
        node = self.node(launch)
        intent = launch.prepared_intent()
        store = launch.adapter.pair_preparation
        original = store.record
        state = {"creates": 0}

        def failing_record(phase, iteration, role, *, stage, **fields):
            if stage == "CREATED":
                state["creates"] += 1
                if state["creates"] == 2:
                    raise RuntimeError("the process died before the CREATED write")
            return original(phase, iteration, role, stage=stage, **fields)

        store.record = failing_record
        with self.assertRaises(RuntimeError):
            node(intent)
        store.record = original
        self.assertEqual(len(launch.recorder.pair_handles), 2)
        self.assertEqual(launch.entry("worker")["stage"], "CREATED")
        self.assertEqual(launch.entry("reviewer")["stage"], "CREATE_INTENDED")
        with self.assertRaises(executor.IdempotencyRecoveryError) as caught:
            node(intent)
        self.assertEqual(caught.exception.code, "PAIR_PREPARATION_OUTCOME_UNKNOWN")
        self.assertEqual(len(launch.recorder.pair_handles), 2)
        self.assertEqual(sum(1 for c in launch.recorder.commands
                             if c[1:2] == ("task-create",)), 0)

    def test_at_admission_the_re_entry_recovers_with_one_dispatch(self) -> None:
        """(d) Both VERIFIED, interrupted before `create_task`: a clean recovery."""
        launch = self.launch()
        node = self.node(launch)
        intent = launch.prepared_intent()
        original = launch.harness.create_task
        state = {"calls": 0}

        def failing_create_task(spec, **kwargs):
            state["calls"] += 1
            if state["calls"] == 1:
                raise RuntimeError("the process died at the admission boundary")
            return original(spec, **kwargs)

        launch.harness.create_task = failing_create_task
        with self.assertRaises(RuntimeError):
            node(intent)
        launch.harness.create_task = original
        self.assertEqual(launch.entry("worker")["stage"], "VERIFIED")
        self.assertEqual(launch.entry("reviewer")["stage"], "VERIFIED")
        self.assertEqual(len(launch.recorder.pair_handles), 2)
        settled = node(intent)
        self.assertEqual(settled["intent_status"], "SETTLED")
        self.assertEqual(len(launch.recorder.pair_handles), 2,
                         "0 NEW creates on the recovery")
        self.assertEqual(sum(1 for c in launch.recorder.commands
                             if c[1:2] == ("task-create",)), 1)
        self.assertEqual(sum(1 for c in launch.recorder.commands
                             if c[1:2] == ("worker-start",)), 1)

    def test_at_dispatch_the_pre_existing_block_is_unchanged(self) -> None:
        """(e) After `create_task`, before `worker-start`: the SHIPPED
        IDEMPOTENCY_RECOVERY_UNSUPPORTED, which this design does not change."""
        launch = self.launch()
        node = self.node(launch)
        intent = launch.prepared_intent()
        original = launch.harness.run_existing_task
        state = {"calls": 0}

        def failing_run(*args, **kwargs):
            state["calls"] += 1
            if state["calls"] == 1:
                raise RuntimeError("the process died at the dispatch boundary")
            return original(*args, **kwargs)

        launch.harness.run_existing_task = failing_run
        with self.assertRaises(RuntimeError):
            node(intent)
        launch.harness.run_existing_task = original
        self.assertEqual(len(launch.recorder.pair_handles), 2)
        with self.assertRaises(executor.IdempotencyRecoveryError) as caught:
            node(intent)
        self.assertEqual(caught.exception.code, "IDEMPOTENCY_RECOVERY_UNSUPPORTED")
        self.assertEqual(len(launch.recorder.pair_handles), 2,
                         "no second session")
        self.assertEqual(sum(1 for c in launch.recorder.commands
                             if c[1:2] == ("task-create",)), 1,
                         "no second Task")

    def test_after_dispatch_the_pre_existing_block_is_unchanged(self) -> None:
        """(f) `worker-start` issued, settlement unknown."""
        launch = self.launch()
        node = self.node(launch)
        intent = launch.prepared_intent()
        original = launch.harness.wait_for_done
        state = {"calls": 0}

        def failing_wait(*args, **kwargs):
            state["calls"] += 1
            if state["calls"] == 1:
                raise RuntimeError("the process died after the dispatch")
            return original(*args, **kwargs)

        launch.harness.wait_for_done = failing_wait
        with self.assertRaises(RuntimeError):
            node(intent)
        launch.harness.wait_for_done = original
        with self.assertRaises(executor.IdempotencyRecoveryError) as caught:
            node(intent)
        self.assertEqual(caught.exception.code, "IDEMPOTENCY_RECOVERY_UNSUPPORTED")
        self.assertEqual(len(launch.recorder.pair_handles), 2)
        self.assertEqual(sum(1 for c in launch.recorder.commands
                             if c[1:2] == ("task-create",)), 1)
        self.assertEqual(sum(1 for c in launch.recorder.commands
                             if c[1:2] == ("worker-start",)), 1)


@REQUIRES_LANGGRAPH
class T8BoundaryFinalStateTests(ChildProcessRoom):
    """(a)-(d) again in a REAL successor process over the PREDECESSOR'S OWN ledger.

    Each case asserts the FINAL workflow state -- `terminal_status`,
    `terminal_reason["code"]` and `exit_code` through the child's own
    `launcher.summarize` -- plus that the successor RESUMED the predecessor's claim and
    produced ZERO duplicate effects.  Session counts are stated PER SETUP: (a) 0, (b) 1,
    (c) 2, (d) 2, all created by the PREDECESSOR, none by the child.
    """

    def test_before_the_first_create_settles_blocked_in_a_successor(self) -> None:
        """(a) The worker entry sits at `CREATE_INTENDED`: a write landed, no effect
        followed.  The successor re-reads it and names the same block."""
        recorder = PairRecorder(scripted_bodies(), create_failures={2: UNPARSED_CREATE})
        launch = self.launch(recorder=recorder)
        first = self.execute(launch)
        self.assertBlocked(first, "PAIR_PREPARATION_OUTCOME_UNKNOWN")
        self.assertEqual(launch.entry("worker")["stage"], "CREATE_INTENDED")
        self.assertEqual(len(launch.recorder.pair_handles), 0)
        interrupted = self.interrupted_intent_id(launch)
        before = self.assertLiveUnsettledClaim(launch, interrupted)
        report = self.child(mode="rebound", listing=[])
        self.assertEqual(report["terminal_status"], "BLOCKED")
        self.assertEqual(report["terminal_reason"]["code"],
                         "PAIR_PREPARATION_OUTCOME_UNKNOWN")
        self.assertEqual(report["exit_code"], launcher.EXIT_CODES["BLOCKED"])
        self.assertResumedThatClaim(report, launch, interrupted, before)
        self.assertEqual([report["pair_creates"], report["task_creates"],
                          report["worker_starts"], report["sends"]], [0, 0, 0, 0])

    def test_after_the_first_create_before_its_record_settles_blocked_in_a_successor(self):
        """(b) ONE session exists and its result-record does not: neither success nor
        confirmed absence, in the successor process too."""
        launch = self.predecessor_at_create_boundary(ordinal=1)
        self.assertEqual(launch.entry("worker")["stage"], "CREATE_INTENDED")
        interrupted = self.interrupted_intent_id(launch)
        before = self.assertLiveUnsettledClaim(launch, interrupted)
        report = self.child(mode="rebound", listing=self.listing_of(launch))
        self.assertEqual(report["terminal_status"], "BLOCKED",
                         report.get("terminal_reason") or report.get("execute_error"))
        self.assertEqual(report["terminal_reason"]["code"],
                         "PAIR_PREPARATION_OUTCOME_UNKNOWN")
        self.assertEqual(report["exit_code"], launcher.EXIT_CODES["BLOCKED"])
        self.assertResumedThatClaim(report, launch, interrupted, before)
        self.assertEqual([report["pair_creates"], report["task_creates"],
                          report["worker_starts"], report["sends"]], [0, 0, 0, 0],
                         "ZERO duplicate effects: no second session, Task, delivery")
        self.assertEqual(len(launch.recorder.pair_handles), 1,
                         "the ONE session this setup created is still the only one")
        self.assertEqual(launch.ledger.get_receipt(interrupted)["status"], "CLAIMED")

    def test_after_the_second_create_before_its_record_settles_blocked_in_a_successor(self):
        """(c) The same, one role later: TWO sessions exist, one record is missing."""
        launch = self.predecessor_at_create_boundary(ordinal=2)
        self.assertEqual(launch.entry("worker")["stage"], "CREATED")
        self.assertEqual(launch.entry("reviewer")["stage"], "CREATE_INTENDED")
        interrupted = self.interrupted_intent_id(launch)
        before = self.assertLiveUnsettledClaim(launch, interrupted)
        report = self.child(mode="rebound", listing=self.listing_of(launch))
        self.assertEqual(report["terminal_status"], "BLOCKED",
                         report.get("terminal_reason") or report.get("execute_error"))
        self.assertEqual(report["terminal_reason"]["code"],
                         "PAIR_PREPARATION_OUTCOME_UNKNOWN")
        self.assertEqual(report["exit_code"], launcher.EXIT_CODES["BLOCKED"])
        self.assertResumedThatClaim(report, launch, interrupted, before)
        self.assertEqual([report["pair_creates"], report["task_creates"],
                          report["worker_starts"], report["sends"]], [0, 0, 0, 0],
                         "ZERO duplicate effects")
        self.assertEqual(len(launch.recorder.pair_handles), 2,
                         "the TWO sessions this setup created are still the only ones")
        self.assertEqual(launch.ledger.get_receipt(interrupted)["status"], "CLAIMED")

    def test_an_admission_boundary_interruption_recovers_in_a_successor(self) -> None:
        """(d) Both roles `VERIFIED`, interrupted before `create_task`: the successor
        RECOVERS with exactly ONE Task / Dispatch / settlement for the claim it inherited
        and ZERO new sessions."""
        launch = self.predecessor_at_admission()
        interrupted = self.interrupted_intent_id(launch)
        before = self.assertLiveUnsettledClaim(launch, interrupted)
        report = self.child(mode="rebound", listing=self.listing_of(launch))
        self.assertEqual(report["terminal_status"], "COMPLETED",
                         report.get("terminal_reason") or report.get("execute_error"))
        self.assertEqual(report["exit_code"], launcher.EXIT_CODES["COMPLETED"])
        self.assertResumedThatClaim(report, launch, interrupted, before)
        self.assertEqual(report["pair_creates"], 0, "0 NEW creates on the recovery")
        self.assertEqual(len(launch.recorder.pair_handles), 2,
                         "the predecessor's two sessions are RETAINED")
        intents = self.assertOneEffectPerIntent(launch, report)
        self.assertEqual(intents[0], interrupted)


# ======================================================================================
# T10  M7: no duplicate create, dispatch or settlement
# ======================================================================================
class T10DuplicatePreventionTests(PairRoom):

    def test_a_second_start_for_one_intent_short_circuits(self) -> None:
        """(a) The idempotency short-circuit precedes every effect, preparation included."""
        launch = self.launch()
        node = self.node(launch)
        intent = launch.prepared_intent()
        node(intent)
        handles = len(launch.recorder.pair_handles)
        tasks = sum(1 for c in launch.recorder.commands if c[1:2] == ("task-create",))
        receipt = launch.adapter.start(intent["pending_intent"])
        self.assertTrue(receipt["task_id"])
        self.assertEqual(len(launch.recorder.pair_handles), handles,
                         "0 NEW sessions")
        self.assertEqual(sum(1 for c in launch.recorder.commands
                             if c[1:2] == ("task-create",)), tasks,
                         "0 NEW task-create")

    def test_a_settled_intent_returns_its_stored_settlement(self) -> None:
        """(b)+(c) `claim` answers ALREADY_SETTLED and nothing external happens."""
        launch = self.launch()
        node = self.node(launch)
        intent = launch.prepared_intent()
        first = node(intent)
        event_ids = {first["pending_event"]["event_id"]}
        commands = len(launch.recorder.commands)
        for _turn in range(2):
            again = node(intent)
            event_ids.add(again["pending_event"]["event_id"])
        self.assertEqual(len(event_ids), 1,
                         "exactly ONE settlement event id across all turns")
        self.assertEqual(len(launch.recorder.commands), commands,
                         "nothing external happened on the later turns")

    def test_dispatch_ids_are_a_set_of_size_one_per_intent(self) -> None:
        launch = self.launch()
        node = self.node(launch)
        intent = launch.prepared_intent()
        for _turn in range(3):
            node(intent)
        starts = [c for c in launch.recorder.commands if c[1:2] == ("worker-start",)]
        self.assertEqual(len(starts), 1)
        tasks = {c[c.index("--task") + 1] for c in starts if "--task" in c}
        self.assertEqual(len(tasks), 1)

    def test_a_dispatched_window_never_creates_a_second_effect(self) -> None:
        """(d) B10/B11: the pre-existing block, with nothing new created."""
        launch = self.launch()
        node = self.node(launch)
        intent = launch.prepared_intent()
        original = launch.harness.wait_for_done
        state = {"calls": 0}

        def failing_wait(*args, **kwargs):
            state["calls"] += 1
            if state["calls"] == 1:
                raise RuntimeError("the process died after the dispatch")
            return original(*args, **kwargs)

        launch.harness.wait_for_done = failing_wait
        with self.assertRaises(RuntimeError):
            node(intent)
        launch.harness.wait_for_done = original
        with self.assertRaises(executor.IdempotencyRecoveryError) as caught:
            node(intent)
        self.assertEqual(caught.exception.code, "IDEMPOTENCY_RECOVERY_UNSUPPORTED")
        self.assertEqual(sum(1 for c in launch.recorder.commands
                             if c[1:2] == ("task-create",)), 1)
        self.assertEqual(sum(1 for c in launch.recorder.commands
                             if c[1:2] == ("worker-start",)), 1)
        self.assertEqual(len(launch.recorder.pair_handles), 2)


# ======================================================================================
# T9  M6: corrupt / missing / old-version / ownership-mismatched records
# ======================================================================================
@REQUIRES_LANGGRAPH
class T9RecordIntegrityTests(ChildProcessRoom):
    """The ONLY group permitted to hand-write or delete a preparation entry or a launch
    record: that is its subject, and its name says so."""

    def binding_path(self, run_id=RUN_ID):
        return self.project / "artifacts" / "runs" / run_id / ".pair_launch_binding.json"

    def preparation_path(self, run_id=RUN_ID):
        return self.project / "artifacts" / "runs" / run_id / ".pair_preparation.json"

    def write_entry(self, **overrides):
        """Write ONE preparation entry directly, bypassing the store's validator."""
        entry = {key: "" for key in pause_store.PAIR_ENTRY_KEYS}
        entry.update({"run_id": RUN_ID, "phase": PHASE, "gate_iteration": "1",
                      "role": "worker", "stage": "CREATED", "create_attempt": "1",
                      "terminal_title": f"{RUN_ID}-pair-{PHASE}-1-worker",
                      "terminal_worktree": "id:repo_os14::/project",
                      "terminal_digest": pause_policy.terminal_digest("term_gone"),
                      "requested_model": "glm-5.2", "recorded_by": "host:pid1",
                      "create_intended_at": "2026-01-01T00:00:00Z",
                      "create_settled_at": "2026-01-01T00:00:00Z"})
        schema = overrides.pop("schema_version", pause_store.PAIR_PREPARATION_SCHEMA_VERSION)
        drop = overrides.pop("drop", ())
        entry.update(overrides)
        for key in drop:
            entry.pop(key, None)
        path = self.preparation_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "schema_version": schema,
            "pairs": {f"{PHASE}#1": {"worker": entry}}}), encoding="utf-8")

    def adopt_and_execute(self, *, profile_name="", driver=None, recorder=None,
                          risk="high", phases=(PHASE_UPPER,), run_id=RUN_ID,
                          bodies=None, listing=None, ledger_name="adopt.json"):
        """Adopt the run IN THIS PROCESS and drive it through `launcher.execute_state`.

        In-process on purpose: these subcases are about the RECORDS, not about process
        memory.  The cross-process evidence is `T7NewProcessRecoveryTests`.
        """
        recorder = recorder if recorder is not None else PairRecorder(
            bodies if bodies is not None else scripted_bodies(run_id), run_id=run_id,
            listing=listing or [])
        ledger = FileRuntimeStateStore(self.root / ledger_name)
        adapter = self.adopt(recorder=recorder, driver=driver, ledger=ledger,
                             profile_name=profile_name, run_id=run_id)
        state = launcher.build_state({"run_id": run_id, "thread_id": "os14",
                                      "phases": list(phases), "risk": risk})
        final = self.execute(None, state=state, adapter=adapter, ledger=ledger)
        return adapter, recorder, final

    # -- (a) (b) (c) ----------------------------------------------------------------
    def test_an_entry_missing_a_closed_key_blocks_as_corrupt(self) -> None:
        launch = self.launch()
        self.write_entry(drop=("verified_at",))
        final = self.execute(launch)
        self.assertBlocked(final, "PAIR_PREPARATION_RECORD_CORRUPT")
        self.assertEqual(len(launch.recorder.pair_handles), 0,
                         "never read as 'nothing prepared'")

    def test_an_entry_with_an_unknown_stage_blocks_as_corrupt(self) -> None:
        launch = self.launch()
        self.write_entry(stage="LAUNCHED")
        final = self.execute(launch)
        self.assertBlocked(final, "PAIR_PREPARATION_RECORD_CORRUPT")
        self.assertEqual(len(launch.recorder.pair_handles), 0)

    def test_an_older_schema_version_blocks_by_name(self) -> None:
        launch = self.launch()
        self.write_entry(schema_version="os14.pair_preparation.v0")
        final = self.execute(launch)
        self.assertBlocked(final, "PAIR_PREPARATION_RECORD_CORRUPT")
        self.assertEqual(len(launch.recorder.pair_handles), 0)

    # -- (d) ------------------------------------------------------------------------
    def test_an_absent_preparation_document_prepares_from_scratch(self) -> None:
        """Safe ONLY because the CREATE_INTENDED write precedes the first create, so the
        two are asserted TOGETHER rather than the verdict alone."""
        launch = self.launch()
        self.assertFalse(self.preparation_path().exists())
        self.assertIsNotNone(launch.binding())
        order: list[str] = []
        store = launch.adapter.pair_preparation
        original = store.record

        def tracing_record(phase, iteration, role, *, stage, **fields):
            order.append(f"write:{stage}:{role}")
            return original(phase, iteration, role, stage=stage, **fields)

        store.record = tracing_record
        original_create = launch.harness.create_fake_terminal

        def tracing_create(role, mode, **kwargs):
            order.append(f"effect:create:{role}")
            return original_create(role, mode, **kwargs)

        launch.harness.create_fake_terminal = tracing_create
        self.node(launch)(launch.prepared_intent())
        store.record = original
        launch.harness.create_fake_terminal = original_create
        self.assertEqual(order[:4],
                         ["write:CREATE_INTENDED:worker", "effect:create:worker",
                          "write:CREATED:worker", "write:CREATE_INTENDED:reviewer"],
                         "intent is written BEFORE the effect, identity AFTER it")
        self.assertEqual(len(launch.recorder.pair_handles), 2)

    # -- (e) ------------------------------------------------------------------------
    def test_an_entry_naming_a_different_run_blocks_as_an_ownership_mismatch(self):
        launch = self.launch()
        self.write_entry(run_id="run_foreign")
        final = self.execute(launch)
        self.assertBlocked(final, "PAIR_PREPARATION_OWNERSHIP_MISMATCH")
        self.assertEqual(len(launch.recorder.pair_handles), 0)

    def test_a_differing_recorded_by_alone_does_not_block(self) -> None:
        launch = self.launch()
        handles = ["term_os14_2", "term_os14_3"]
        for role, handle in zip(("worker", "reviewer"), handles):
            self.write_entry()          # seeds the document, then both roles below
        path = self.preparation_path()
        document = json.loads(path.read_text(encoding="utf-8"))
        slot = {}
        for role, handle in zip(("worker", "reviewer"), handles):
            entry = dict(document["pairs"][f"{PHASE}#1"]["worker"])
            entry.update({"role": role, "recorded_by": "a_different_host:pid999",
                          "stage": "CREATED",
                          "terminal_title": f"{RUN_ID}-pair-{PHASE}-1-{role}",
                          "terminal_digest": pause_policy.terminal_digest(handle),
                          "requested_model": ("glm-5.2" if role == "worker"
                                              else "glm-5.3-flash")})
            slot[role] = entry
        document["pairs"][f"{PHASE}#1"] = slot
        path.write_text(json.dumps(document), encoding="utf-8")
        launch.recorder.listing = [
            {"handle": handle, "title": f"{RUN_ID}-pair-{PHASE}-1-{role}",
             "orphaned": False}
            for role, handle in zip(("worker", "reviewer"), handles)]
        launch.recorder.results["list"] = {"terminals": list(launch.recorder.listing)}
        settled = self.node(launch)(launch.prepared_intent())
        self.assertEqual(settled["intent_status"], "SETTLED")
        self.assertEqual(len(launch.recorder.pair_handles), 0,
                         "both entries were ADOPTED, not re-created")

    # -- (f) ------------------------------------------------------------------------
    def test_a_disagreeing_routing_digest_blocks_as_a_binding_mismatch(self) -> None:
        launch = self.launch()
        path = self.binding_path()
        document = json.loads(path.read_text(encoding="utf-8"))
        document["binding"]["routing_digest"] = "f" * 64
        path.write_text(json.dumps(document), encoding="utf-8")
        final = self.execute(launch)
        self.assertBlocked(final, "PAIR_PREPARATION_BINDING_MISMATCH")
        self.assertEqual(len(launch.recorder.pair_handles), 0)

    # -- (g) ------------------------------------------------------------------------
    def test_the_prepared_titles_cannot_collide_with_each_other_or_the_os31_scheme(self):
        worker_title = f"{RUN_ID}-pair-{PHASE}-1-worker"
        reviewer_title = f"{RUN_ID}-pair-{PHASE}-1-reviewer"
        self.assertFalse(pause_policy.match_terminal_title(worker_title,
                                                           reviewer_title))
        self.assertFalse(pause_policy.match_terminal_title(reviewer_title,
                                                           worker_title))
        os31_title = f"os31-{RUN_ID}-intent_abc"
        self.assertFalse(pause_policy.match_terminal_title(worker_title, os31_title))
        self.assertFalse(pause_policy.match_terminal_title(os31_title, worker_title))
        self.assertTrue(pause_policy.match_terminal_title(worker_title, worker_title))

    def test_each_dispatch_journal_row_targets_its_own_prepared_title(self) -> None:
        """G-9: on the pair path the PLANNED row carries the PREPARED title, so the row's
        title and digest describe the SAME session."""
        from scripts.deterministic_workflow import pause_store as store_module
        journal = store_module.journal_for(RUN_ID, artifact_base=self.project)
        launch = self.launch()
        launch.adapter.settlement_journal = journal
        node = self.node(launch)
        intent = launch.prepared_intent()
        node(intent)
        row = journal.row(intent["pending_intent"]["intent_id"])
        self.assertEqual(row["terminal_title"], f"{RUN_ID}-pair-{PHASE}-1-worker")
        self.assertEqual(row["terminal_digest"],
                         pause_policy.terminal_digest(launch.recorder.pair_handles[0]))
        self.assertNotEqual(row["terminal_title"], f"{RUN_ID}-pair-{PHASE}-1-reviewer")

    # -- (h) ------------------------------------------------------------------------
    def test_a_deleted_launch_record_with_entries_blocks_as_binding_lost(self) -> None:
        launch = self.launch()
        self.write_entry()
        self.binding_path().unlink()
        final = self.execute(launch)
        self.assertBlocked(final, "PAIR_PREPARATION_BINDING_LOST")
        self.assertEqual(len(launch.recorder.pair_handles), 0,
                         "NOT read as 'nothing was prepared' and preparation never "
                         "restarts")

    # -- (i) ------------------------------------------------------------------------
    def test_a_model_aware_record_read_with_no_routing_blocks_as_unverifiable(self):
        self.predecessor(stop_at="B0")
        _adapter, recorder, final = self.adopt_and_execute(profile_name="")
        self.assertBlocked(final, "PAIR_PREPARATION_BINDING_UNVERIFIABLE")
        self.assertEqual([len(recorder.pair_titles),
                          sum(1 for c in recorder.commands
                              if c[1:2] == ("task-create",)),
                          sum(1 for c in recorder.commands
                              if c[1:2] == ("worker-start",))], [0, 0, 0])

    # -- (j1) (j2) --------------------------------------------------------------------
    def test_both_records_absent_blocks_for_both_constructions(self) -> None:
        self.predecessor(stop_at="B0")
        self.binding_path().unlink()
        self.assertFalse(self.preparation_path().exists())
        for name, profile, driver in (
                ("j1_shipped", "", None),
                ("j2_rebound", "split",
                 InProcessModelDriver(resolve=lambda requested: requested))):
            with self.subTest(construction=name):
                _adapter, recorder, final = self.adopt_and_execute(
                    profile_name=profile, driver=driver,
                    ledger_name=f"adopt_{name}.json")
                self.assertBlocked(final, "PAIR_PREPARATION_LAUNCH_RECORD_ABSENT")
                self.assertEqual(len(recorder.pair_titles), 0,
                                 "preparation does NOT restart")
                self.assertFalse(self.binding_path().exists(),
                                 "an adoption never writes a launch record, so it "
                                 "cannot repair the absence")

    # -- (k) ------------------------------------------------------------------------
    def test_a_legacy_record_read_by_a_model_aware_process_blocks(self) -> None:
        launch = self.launch()
        path = self.binding_path()
        document = json.loads(path.read_text(encoding="utf-8"))
        document["binding"].update({"model_aware": "false", "routing_digest": "",
                                    "driver_type_id": ""})
        path.write_text(json.dumps(document), encoding="utf-8")
        final = self.execute(launch)
        self.assertBlocked(final, "PAIR_PREPARATION_BINDING_MISMATCH")
        self.assertEqual(len(launch.recorder.pair_handles), 0)

    # -- (l) ------------------------------------------------------------------------
    def test_a_non_monotonic_promotion_is_refused_and_leaves_the_entry_unchanged(self):
        store = pause_store.pair_preparation_for(RUN_ID, artifact_base=self.project)
        store.record(PHASE, 1, "worker", stage="CREATE_INTENDED", terminal_title="t")
        store.record(PHASE, 1, "worker", stage="CREATED", terminal_digest="d")
        before = store.entry(PHASE, 1, "worker")
        with self.assertRaises(pause_store.PairPreparationCorrupt):
            store.record(PHASE, 1, "worker", stage="CREATE_INTENDED")
        self.assertEqual(store.entry(PHASE, 1, "worker"), before)
        store.record(PHASE, 1, "worker", stage="VERIFIED",
                     resolved_model_observed="glm-5.2")
        refused = pause_store.pair_preparation_for(RUN_ID, artifact_base=self.project)
        with self.assertRaises(pause_store.PairPreparationCorrupt):
            refused.record(PHASE, 1, "worker", stage="CREATED")
        self.assertEqual(refused.entry(PHASE, 1, "worker")["stage"], "VERIFIED")

    # -- (m) (n) (o) (p): the DRIVER portion of the launch identity ------------------
    def test_the_same_driver_class_is_admitted(self) -> None:
        """(m) The POSITIVE control, without which (n) would pass vacuously."""
        launch = self.predecessor(stop_at="B7")
        _adapter, recorder, final = self.adopt_and_execute(
            profile_name="split",
            driver=InProcessModelDriver(resolve=lambda requested: requested),
            listing=self.listing_of(launch))
        self.assertEqual(final.get("terminal_status"), "COMPLETED",
                         final.get("terminal_reason"))
        self.assertEqual(len(recorder.pair_titles), 0, "0 new sessions")
        self.assertEqual(sum(1 for c in recorder.commands
                             if c[1:2] == ("task-create",)), 3)
        self.assertEqual(sum(1 for c in recorder.commands
                             if c[1:2] == ("worker-start",)), 3)

    def test_a_different_capable_driver_class_with_one_name_is_refused(self) -> None:
        """(n) The F-004 regression at the CHECKER: two module-level classes whose
        `type(...).__name__` is IDENTICAL, so the comparison cannot pass vacuously."""
        twins = _TwinModules(self.root / "twins", self)
        alpha, beta = twins.alpha.ModelDriver(), twins.beta.ModelDriver()
        self.assertEqual(type(alpha).__name__, type(beta).__name__)
        self.assertNotEqual(agent_profile.driver_type_id(alpha),
                            agent_profile.driver_type_id(beta))
        recorder = PairRecorder(scripted_bodies(), fail_verbs={"current"})
        launch = self.launch(recorder=recorder, driver=alpha)
        try:
            self.execute(launch)
        except BaseException:
            pass
        self.assertEqual(launch.binding()["driver_type_id"],
                         agent_profile.driver_type_id(alpha))

        successor = PairRecorder(scripted_bodies(), listing=[])
        adapter, successor_recorder, final = self.adopt_and_execute(
            profile_name="split", driver=beta, recorder=successor)
        live = adapter.harness.routing_binding()
        self.assertEqual(live["routing_digest"], launch.binding()["routing_digest"],
                         "the ROUTING is identical; only the driver class differs")
        self.assertBlocked(final, "PAIR_PREPARATION_BINDING_MISMATCH")
        self.assertEqual([len(successor_recorder.pair_titles),
                          sum(1 for c in successor_recorder.commands
                              if c[1:2] == ("task-create",)),
                          sum(1 for c in successor_recorder.commands
                              if c[1:2] == ("worker-start",)),
                          successor_recorder.count("terminal", "send")], [0, 0, 0, 0])
        self.assertEqual(beta.requests if hasattr(beta, "requests") else [], [],
                         "the refusal precedes every verification leg")
        self.assertFalse(self.preparation_path().exists(),
                         "no durable write to either document")

    def test_the_refusal_detail_names_the_differing_cell(self) -> None:
        """(n), the detail.  `terminal_node` REBUILDS `terminal_reason` and sets its
        `message` to the code, so the detail is asserted where it exists -- on the raised
        `IdempotencyRecoveryError` at the adapter boundary."""
        twins = _TwinModules(self.root / "twins", self)
        alpha, beta = twins.alpha.ModelDriver(), twins.beta.ModelDriver()
        recorder = PairRecorder(scripted_bodies(), fail_verbs={"current"})
        launch = self.launch(recorder=recorder, driver=alpha)
        try:
            self.execute(launch)
        except BaseException:
            pass
        successor = PairRecorder(scripted_bodies(), listing=[])
        ledger = FileRuntimeStateStore(self.root / "detail.json")
        adapter = self.adopt(recorder=successor, driver=beta, ledger=ledger,
                             profile_name="split")
        state = launcher.build_state({"run_id": RUN_ID, "thread_id": "os14",
                                      "phases": [PHASE_UPPER], "risk": "high"})
        node = executor.execute_intent_node(adapter, ledger)
        prepared = executor.prepare_intent_node(
            executor.route_node(executor.validate_node(state)))
        with self.assertRaises(executor.IdempotencyRecoveryError) as caught:
            node(prepared)
        self.assertEqual(caught.exception.code, "PAIR_PREPARATION_BINDING_MISMATCH")
        self.assertIn("driver_type_id", caught.exception.detail)

    def test_an_absent_or_incapable_driver_is_a_different_named_outcome(self) -> None:
        """(o) `AGENT_MODEL_NOT_SUPPORTED` at Gate A, NOT conflated with (n)."""
        self.predecessor(stop_at="B0")

        class NotCallable:
            select_and_verify = "not a callable"

        for label, driver in (("absent", None), ("incapable", NotCallable())):
            with self.subTest(driver=label):
                recorder = PairRecorder(scripted_bodies(), listing=[])
                ledger = FileRuntimeStateStore(self.root / f"adopt_{label}.json")
                with self.assertRaises(launcher.LauncherError) as caught:
                    self.adopt(recorder=recorder, driver=driver, ledger=ledger,
                               profile_name="split")
                self.assertIn("AGENT_MODEL_NOT_SUPPORTED", str(caught.exception))
                self.assertNotIn("AGENT_MODEL_DRIVER_UNIDENTIFIABLE",
                                 str(caught.exception))
                self.assertEqual(recorder.commands, [],
                                 "no harness, Run, Task, Dispatch or terminal exists")

    def test_an_unidentifiable_driver_is_a_third_named_outcome(self) -> None:
        """(p) `AGENT_MODEL_DRIVER_UNIDENTIFIABLE`, on BOTH doors."""
        dynamic = type("ModelDriver", (), {"select_and_verify": lambda self, t: None})()
        self.predecessor(stop_at="B0")
        recorder = PairRecorder(scripted_bodies(), listing=[])
        ledger = FileRuntimeStateStore(self.root / "adopt_unidentifiable.json")
        with self.assertRaises(launcher.LauncherError) as caught:
            self.adopt(recorder=recorder, driver=dynamic, ledger=ledger,
                       profile_name="split")
        self.assertIn("AGENT_MODEL_DRIVER_UNIDENTIFIABLE", str(caught.exception))
        self.assertEqual(recorder.commands, [])

    def test_an_unidentifiable_driver_cannot_open_a_model_aware_run(self) -> None:
        """(p), the LAUNCH door: no run root file is written at all."""
        dynamic = type("ModelDriver", (), {"select_and_verify": lambda self, t: None})()
        launch_recorder = PairRecorder(scripted_bodies(), run_id="run_os14c")
        with self.assertRaises(launcher.LauncherError) as caught:
            self.launch(recorder=launch_recorder, driver=dynamic, run_id="run_os14c")
        self.assertIn("AGENT_MODEL_DRIVER_UNIDENTIFIABLE", str(caught.exception))
        self.assertEqual(launch_recorder.commands, [])
        self.assertFalse(
            (self.project / "artifacts" / "runs" / "run_os14c").exists(),
            "an un-nameable driver can never open a model-aware run")

    # -- (q) (r): the DURABLE RISK derivation --------------------------------------
    def test_a_low_risk_run_reconstructs_its_own_digest(self) -> None:
        """(q) `low` is the ONLY value that can prove this: `required_roles` marks a
        phase Reviewer required for `("medium", "high")` only, so `medium` and `high`
        yield the SAME required set and therefore the same digest."""
        # At LOW risk `pair_admission_required` is False, so the B0 recipe's own
        # interruption never fires; the predecessor is stopped instead by handing it NO
        # scripted agent body, which stops it at its FIRST delivery and still leaves a
        # real committed head.
        launch = self.predecessor(stop_at="B0", risk="low", bodies=[])
        recorded = launch.binding()["routing_digest"]
        adapter, recorder, final = self.adopt_and_execute(
            profile_name="split",
            driver=InProcessModelDriver(resolve=lambda requested: requested),
            risk="low", bodies=[worker_body(), final_body()])
        self.assertEqual(final.get("terminal_status"), "COMPLETED",
                         final.get("terminal_reason"))
        self.assertEqual(adapter.harness.routing_binding()["routing_digest"], recorded)
        self.assertEqual(adapter.harness.risk, "low",
                         "the adopted harness carries the RUN's risk, not the "
                         "constructor's default")
        # Non-vacuity: the same profile and phases at the constructor's DEFAULT `high`
        # produce a DIFFERENT digest, so this test would fail if the durable risk were
        # ignored.
        with patch.dict(os.environ, {"PATH": str(self.binaries)}):
            default_routing = launcher.orca_run_routing(
                agent_profile_name="split", requested_phases=(PHASE,), risk="high",
                project_root=self.project,
                model_driver=InProcessModelDriver())
        with patch.dict(os.environ, {"ORCA_CLI_COMMAND": "/opt/orca-dev"}):
            default_harness = OrcaRuntimeHarness(
                self.project, agent_routing=default_routing,
                model_driver=InProcessModelDriver())
        self.assertNotEqual(default_harness.routing_binding()["routing_digest"],
                            recorded)
        # And the behavioural consequence the threaded risk buys: no dependent Reviewer
        # Task is created at LOW risk.
        self.assertEqual(sum(1 for c in recorder.commands
                             if c[1:2] == ("task-create",)), 2,
                         "worker and final review only -- no phase Reviewer Task")

    def test_an_unreadable_declaration_is_refused_and_never_defaulted(self) -> None:
        """(r) Both subcases, and the LEGACY adoption of the SAME run still succeeds."""
        self.predecessor(stop_at="B0")
        head_path = launcher.resolve_checkpoint_path(RUN_ID, "os14",
                                                     artifact_base=self.project)
        self.assertTrue(head_path.is_file())
        original = head_path.read_text(encoding="utf-8")

        for label, mutate in (
                ("absent", lambda: head_path.unlink()),
                ("corrupt_risk",
                 lambda: DeclaredRunIdentityTests.corrupt_risk(head_path,
                                                               "catastrophic"))):
            with self.subTest(declaration=label):
                head_path.write_text(original, encoding="utf-8")
                mutate()
                recorder = PairRecorder(scripted_bodies(), listing=[])
                ledger = FileRuntimeStateStore(self.root / f"adopt_{label}.json")
                with self.assertRaises(launcher.LauncherError) as caught:
                    self.adopt(recorder=recorder,
                               driver=InProcessModelDriver(
                                   resolve=lambda requested: requested),
                               ledger=ledger, profile_name="split")
                self.assertIn(launcher.ORCA_RUN_DECLARATION_UNREADABLE,
                              str(caught.exception))
                self.assertEqual(recorder.commands, [],
                                 "no harness, Run, Task, Dispatch or terminal")
                self.assertFalse(self.preparation_path().exists(),
                                 "no durable write")
                # The fail-closed rule is scoped to the MODEL-AWARE lane: the legacy
                # adoption of the SAME unreadable run behaves exactly as it does today.
                legacy_recorder = PairRecorder(scripted_bodies(), listing=[])
                legacy = self.adopt(
                    recorder=legacy_recorder, driver=None,
                    ledger=FileRuntimeStateStore(self.root / f"legacy_{label}.json"),
                    profile_name="")
                self.assertIsInstance(legacy, OrcaAdapter)
                self.assertIsNone(legacy.harness.agent_routing)
