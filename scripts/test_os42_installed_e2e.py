"""OS-42 F-002: the bounded repair loop on the INSTALLED Skill's real Orca path.

The finding: the installed launcher accepted only ``--adapter fake``, and ``OrcaAdapter``
-- the production path -- was handed its runtime by injection from
``scripts/orca_runtime_harness.py``, which shipped nowhere. So the only checkpointed
execution of the repair loop used scripted FAKE settlements, and the feature could not
reach the path where OS-42 actually failed.

Every test in this file runs a SEPARATE PYTHON PROCESS whose ``sys.path`` contains the
installed Skill's ``tools/`` directory and nothing from this repository, with a working
directory outside the repository. `import scripts` fails in that interpreter, and each
driver asserts so before it does anything else -- which is what makes "the installed copy"
a fact rather than a claim. The real launcher, the real graph, the real durable
checkpointer, the real ``OrcaAdapter`` and the real ``OrcaRuntimeHarness`` all execute.

The ONLY substitution is ``OrcaRuntimeHarness._exec_orca``, the harness's single
subprocess boundary -- the same seam every offline runtime test in this repository uses,
and the one its own docstring names ("Offline tests replace THIS method -- never
call()"). ``call()``, its JSON handling, its ok/returncode gate and its raw command log
all run for real, so the assertions below are about commands the harness actually issued.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

from scripts import ci_lane, release_manifest

REPO_ROOT = Path(__file__).resolve().parents[1]
SKILL = REPO_ROOT / release_manifest.ORCHESTRATION_SKILL_NAME
INSTALLED_TOOLS = SKILL / "tools"

# The agent commands this module routes to. They are NOT host tools: `drive()` writes an
# executable shim for each one into the temporary project and puts that directory first on
# the child's PATH. Naming a host agent here -- `claude` -- is what made every test below
# fail on a GitHub runner with `AGENT_COMMAND_NOT_FOUND` before reaching F-001/F-002, and
# made a green local run mean only "this developer has a Claude CLI installed".
#
# The names still have to clear the real agent-command trust boundary, so they are not
# arbitrary: they match the pinned `custom_agent_command_pattern` from SKILL.md
# (`(?:claude|codex)-[A-Za-z0-9._-]+`). So the routing below is validated by the SAME
# token, allowlist and PATH gates a production profile is, rather than by a relaxed one.
WORKER_COMMAND = "claude-os42-e2e-worker"
REVIEWER_COMMAND = "claude-os42-e2e-reviewer"
SHIM_COMMANDS = (WORKER_COMMAND, REVIEWER_COMMAND)

# Host agent CLIs that must never appear in the routing above. `known_agent_commands` in
# SKILL.md names the real ones; a test that routes to any of them is asserting a fact
# about the developer's machine instead of about the installed Skill.
HOST_AGENT_COMMANDS = frozenset({"claude", "codex", "claude-glm", "claude-gemma"})

# The profile that routes every role of the requested phase to a real agent command.
# `--adapter orca` refuses to run without one, because the harness's no-routing fallback
# is this repository's fake-agent shim, which does not exist in an installed tree.
AGENT_PROFILE = (
    "version: 1\n"
    "profiles:\n"
    "  installed:\n"
    "    defaults:\n"
    f"      worker: {WORKER_COMMAND}\n"
    f"      reviewer: {REVIEWER_COMMAND}\n"
    "    final_review:\n"
    f"      reviewer: {REVIEWER_COMMAND}\n"
)

# The verbatim value that ended run_8e8f9451ad44: a natural-language sentence where a
# closed enum token belongs. This is the defect OS-42 exists to repair, so it is the one
# the production path has to be shown repairing.
OS42_DEFECT_VALUE = (
    "Fully reversible: this phase wrote exactly one new artifact and modified no "
    "tracked file, no production code, and no pre-existing run or artifact."
)

_PRELUDE = '''
import json, os, sys
from pathlib import Path

sys.path.insert(0, os.environ["INSTALLED_TOOLS"])
# The whole point of this driver: prove the installed package stands alone. A repository
# module reachable here would make every result below meaningless.
try:
    import scripts  # noqa: F401
except ImportError:
    pass
else:  # pragma: no cover - the guard firing IS the failure
    raise SystemExit("REPOSITORY_ON_PATH: this driver is not testing the installed copy")

import orca_runtime_harness as runtime
from deterministic_workflow import launcher
'''

# A stand-in for the harness's ONE process boundary. It answers the verbs a real
# dispatch issues with the shapes the live runtime returns, hands back a fresh
# terminal/task/dispatch identity per dispatch, and serves one scripted agent BODY per
# dispatch -- which is the only thing the scenarios below differ in.
_RECORDER = '''
COMPLETED_AT = "2026-01-01T00:00:00Z"


class Recorder:
    def __init__(self, bodies, last_body=None):
        self.bodies = list(bodies)
        self.last_body = last_body
        self.commands = []
        self.terminals = 0
        self.dispatches = 0
        self.last_task_id = "task_0"
        self.results = {
            "status": {"runtime": {"state": "ready",
                                   "appVersion": runtime.SUPPORTED_ORCA_APP_VERSIONS[0],
                                   "runtimeId": "rt_installed"}},
            "current": {"worktree": {"id": "repo_installed::/project",
                                     "repoId": "repo_installed",
                                     "path": "/project"}},
            "show": {"worktree": {"id": "repo_installed::/project"}},
            "run-create": {"run": {"id": "run_installed"}},
            "wait": {"wait": {"satisfied": True}},
            "send": {},
            "close": {},
            "ack": {},
            "worker-retain": {"state": "retained"},
            "task-list": {"tasks": []},
        }

    def next_body(self):
        if self.bodies:
            return self.bodies.pop(0)
        if self.last_body is None:
            raise AssertionError("the driver ran out of scripted agent bodies")
        return self.last_body

    def __call__(self, args):
        args = tuple(args)
        verb = args[1] if len(args) > 1 else args[0]
        if verb == "create" and args[0] == "terminal":
            self.terminals += 1
            self.commands.append(args)
            return 0, json.dumps({"ok": True, "result": {
                "terminal": {"handle": "term_%d" % self.terminals}}})
        if verb == "task-create":
            self.dispatches += 1
            self.last_task_id = "task_%d" % self.dispatches
            self.results["task-create"] = {"task": {"id": self.last_task_id}}
            self.results["task-list"] = {
                "tasks": [{"id": self.last_task_id, "status": "completed"}]}
        elif verb == "worker-start":
            task_id = self.last_task_id
            dispatch_id = "ctx_%d" % self.dispatches
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
                "deliveryId": "dlv_%s" % dispatch_id, "timedOut": False,
                "messages": [{"id": "msg_%s" % dispatch_id, "type": "worker_done",
                              "payload": json.dumps({"taskId": task_id,
                                                     "dispatchId": dispatch_id,
                                                     "outcome": "succeeded"}),
                              "body": body}]}
        self.commands.append(args)
        return 0, json.dumps({"ok": True, "result": self.results.get(verb, {})})


def build_harness(artifact_dir, **kwargs):
    harness = runtime.OrcaRuntimeHarness(artifact_dir, **kwargs)
    harness._exec_orca = RECORDER
    return harness
'''

_RUN = '''
spec = {"thread_id": "installed", "phases": ["ANALYSIS"], "risk": "high",
        "max_iterations": 5}
base = Path(os.environ["ARTIFACT_BASE"])
from deterministic_workflow.runtime_state import FileRuntimeStateStore
ledger = FileRuntimeStateStore(base / "runtime-state.json")
adapter, state = launcher.build_orca_adapter(
    spec, objective="OS-42 installed production path", artifact_base=base,
    runtime_state=ledger, agent_profile_name="installed", project_root=Path.cwd(),
    harness_factory=build_harness)
final = launcher.execute_state(
    state, adapter=adapter, runtime_state=ledger, artifact_base=base,
    checkpoint_store_path=str(base / "checkpoints.json"))
import run_logging
# The authoritative ledger the live OS-29 ingress publishes to, read back from the
# installed copy's own reader. Sequence 0 is the run-entry declaration `start_run`
# writes; every later row is a settled agent boundary.
try:
    decision_ledger = [
        {key: row.get(key) for key in
         ("sequence", "run", "phase", "iteration", "boundary", "source", "role")}
        for row in run_logging.read_decision_ledger(final["run_id"], base=base)]
except Exception as exc:  # pragma: no cover - reported, never swallowed
    decision_ledger = [{"error": str(exc)}]
tokens = [entry.get("route") for entry in final["logical_trace"]
          if entry.get("node") == "ROUTE"]
roles = [entry.get("role") for entry in final["logical_trace"]
         if entry.get("node") == "PREPARE_INTENT"]
print("RESULT_JSON " + json.dumps({
    "run_id": final["run_id"],
    "terminal_status": final["terminal_status"],
    "terminal_reason": final["terminal_reason"],
    "tokens": tokens,
    "prepared_roles": roles,
    "repair_attempts": final["repair_attempts"],
    "remaining_repair_budget": final["remaining_repair_budget"],
    "phase_iterations": final["phase_iterations"],
    "ledger": decision_ledger,
    "adapter": type(adapter).__name__,
    "harness": type(adapter.harness).__name__,
    "worker_start_commands": [list(c) for c in RECORDER.commands
                              if "worker-start" in c],
}))
'''


def _record(body):
    """A conforming Worker record for the installed run, as a JSON fence body."""
    return {
        "ledger_schema_version": 1, "boundary": "B2", "source": "worker",
        "role": "worker", "run": "run_installed", "phase": "analysis",
        "iteration": 1, "responsible_phase": "analysis", "state": "CLEAR",
        "reason_code": None, "open_decision_item": False, "open_item": None,
        "assumption": None, "evidence": {}, "verdict": "",
        "source_binding": "artifacts/runs/run_installed/",
        "recorded_at": "2026-01-01T00:00:00+00:00", "prior_open_decision_items": [],
        **body,
    }


def _reviewer_record(*, phase, iteration):
    return {
        "ledger_schema_version": 1, "boundary": "B3", "source": "reviewer",
        "role": "reviewer", "run": "run_installed", "phase": phase,
        "iteration": iteration, "responsible_phase": phase, "state": "CLEAR",
        "reason_code": None, "open_decision_item": False, "open_item": None,
        "assumption": None, "evidence": {}, "verdict": "PASS",
        "source_binding": "artifacts/runs/run_installed/",
        "recorded_at": "2026-01-01T00:00:00+00:00", "prior_open_decision_items": [],
    }


def _body(header, field_line, record):
    return (f"# {header}\n\n{field_line}\nDECISION_GATE_STATE: CLEAR\n\n"
            "```decision-gate\n" + json.dumps(record) + "\n```\n")


WORKER_MALFORMED = _body("Worker Result", "STATUS: COMPLETE",
                         _record({"reversibility": OS42_DEFECT_VALUE}))
WORKER_CLEAN = _body("Worker Result", "STATUS: COMPLETE", _record({}))
# Round-2 F-001. Every field below is internally valid and every one of them names a
# DIFFERENT dispatch: the identity a Worker settlement may not claim, the run it does
# not belong to, a phase it was not sent for and an iteration it is not.
WORKER_FORGED = _body("Worker Result", "STATUS: COMPLETE", _record({
    "boundary": "B3", "source": "reviewer", "role": "reviewer",
    "run": "run_foreign", "phase": "design", "iteration": 99,
    "responsible_phase": "design",
    "source_binding": "artifacts/runs/run_foreign/",
}))
# The other side of the line, on the same path: `B9` is no identity at all, so it stays
# an out-of-domain format error and the bounded repair loop may re-ask it.
WORKER_UNKNOWN_BOUNDARY = _body("Worker Result", "STATUS: COMPLETE",
                                _record({"boundary": "B9"}))
# Round-3 F-001. A record that declares its DECISION and no mechanics identity at all --
# the shape the re-review drove through the live ingress and got a published, locally
# synthesised ledger row for.
WORKER_OMITS_MECHANICS = _body("Worker Result", "STATUS: COMPLETE", {
    "state": "CLEAR", "reason_code": None, "open_decision_item": False,
    "open_item": None, "assumption": None, "evidence": {}, "verdict": "",
    "responsible_phase": "analysis", "recorded_at": "2026-01-01T00:00:00+00:00",
    "source_binding": "artifacts/runs/run_installed/",
    "prior_open_decision_items": [],
})
REVIEWER_PASS = _body("Review Result", "RESULT: PASS",
                      _reviewer_record(phase="analysis", iteration=1))
FINAL_PASS = _body("Review Result", "RESULT: PASS",
                   _reviewer_record(phase="final_review", iteration=1))


# Reports, from inside the child, where its agent commands actually resolve.
PROBE_AGENT_RESOLUTION = """
import shutil
# `shutil.which` returns the PATH entry verbatim; the sandbox root is compared after
# symlink resolution (/var -> /private/var on macOS), so both sides are realpaths.
def _real(found):
    return os.path.realpath(found) if found else found


commands = {commands}
host = {host}
print("RESULT_JSON " + json.dumps({{
    "path": os.environ["PATH"],
    "resolved": {{name: _real(shutil.which(name)) for name in commands}},
    "host_resolved": {{name: _real(shutil.which(name)) for name in host}},
    "project": os.path.realpath(os.getcwd()),
}}))
"""


# Every driver below that calls `launcher.execute_state` builds and runs the REAL compiled
# graph inside the child, so it needs the pinned LangGraph runtime exactly as the engine
# tests do. Declared through `ci_lane` rather than a private copy so the CI lanes and this
# module can never disagree about what "installed" means -- and so the dependency-absent
# lane records these as accounted-for langgraph skips instead of ModuleNotFoundError.
#
# The tests that assert PACKAGING and ROUTING -- the closure, the CLI refusals, and the
# host-independence checks below -- are deliberately NOT gated: they must hold in both lanes.
REQUIRES_LANGGRAPH = unittest.skipUnless(
    ci_lane.langgraph_available(),
    f"requires pinned langgraph {ci_lane.pinned_langgraph_version()}")


def agent_bin(project: Path) -> Path:
    """The one directory the run's agent commands may be resolved from."""
    return project / "agent-bin"


def write_agent_shims(project: Path) -> Path:
    """Create an executable shim per routed agent command inside the sandbox.

    The launcher's availability gate is ``shutil.which(command)``, so what the gate needs
    is a real executable on the child's PATH -- not a Claude CLI. These shims are that,
    and nothing more: the harness's only process boundary is stubbed at ``_exec_orca``, so
    no shim is ever executed. Making them runnable anyway keeps the fixture honest -- the
    gate passes because a command genuinely exists, not because a check was relaxed.
    """
    directory = agent_bin(project)
    directory.mkdir(parents=True, exist_ok=True)
    for command in SHIM_COMMANDS:
        shim = directory / command
        shim.write_text(
            "#!/bin/sh\n"
            f"echo '{command}: OS-42 installed-E2E shim; the harness never runs it' >&2\n"
            "exit 0\n",
            encoding="utf-8")
        shim.chmod(0o755)
    return directory


class InstalledProductionPathTestCase(unittest.TestCase):
    """Runs a driver script against the installed copy, in its own interpreter."""

    maxDiff = None

    @staticmethod
    def sandbox_path(shim_directory: Path) -> str:
        """The child's PATH: the sandbox shims, then the system directories only.

        The developer's PATH is deliberately NOT inherited. Inheriting it is what let a
        host ``claude`` satisfy this fixture on a laptop while the same tests failed on
        every CI runner, so the sandbox has to be the whole answer and the parent
        environment must not be able to complete it.

        ``/usr/bin`` and ``/bin`` are kept because the child still needs a POSIX shell for
        the shims' shebang; neither the driver interpreter (``sys.executable``) nor the
        fake Orca CLI (``ORCA_CLI_COMMAND``) is resolved through PATH at all.
        """
        return os.pathsep.join([str(shim_directory), "/usr/bin", "/bin"])

    def drive(self, driver_body: str, *, expect_success: bool = True) -> dict:
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            (project / ".orca").mkdir(parents=True)
            (project / ".orca" / "agent-profiles.yaml").write_text(
                AGENT_PROFILE, encoding="utf-8")
            write_agent_shims(project)
            # The two verbs `preflight` issues through a RAW subprocess rather than
            # through `_exec_orca` need a real executable, so this is the only Orca
            # command file the driver needs. Its guide text is DERIVED from the pinned
            # grammar the harness itself requires, never transcribed.
            from scripts.orca_runtime_harness import (
                REQUIRED_ORCA_CLI_GUIDE_SNIPPETS,
                REQUIRED_ORCHESTRATION_GUIDE_SNIPPETS,
            )
            guides = {
                "orchestration": "\n".join(REQUIRED_ORCHESTRATION_GUIDE_SNIPPETS),
                "orca-cli": "\n".join(REQUIRED_ORCA_CLI_GUIDE_SNIPPETS),
            }
            fake_orca = project / "fake-orca"
            fake_orca.write_text(
                # An ABSOLUTE interpreter, not `/usr/bin/env python3`: the child's PATH
                # below is deliberately minimal, and a shebang that has to search it
                # would reintroduce exactly the host dependency this sandbox removes.
                f"#!{sys.executable}\n"
                "import json, sys\n"
                f"GUIDES = {guides!r}\n"
                "args = sys.argv[1:]\n"
                "if args[:2] == ['skills', 'get']:\n"
                "    sys.stdout.write(GUIDES.get(args[2], ''))\n"
                "    raise SystemExit(0)\n"
                "sys.stdout.write(json.dumps({'ok': True, 'result': {}}))\n",
                encoding="utf-8")
            fake_orca.chmod(0o755)
            driver = project / "driver.py"
            driver.write_text(driver_body, encoding="utf-8")
            environment = {
                "PATH": self.sandbox_path(agent_bin(project)),
                "HOME": str(project),          # never the developer's real profile file
                "INSTALLED_TOOLS": str(INSTALLED_TOOLS),
                "ARTIFACT_BASE": str(project),
                "ORCA_CLI_COMMAND": str(fake_orca),
                "ORCA_OS40_RUNTIME_STATE_DIR": str(project / "ledger"),
                "PYTHONDONTWRITEBYTECODE": "1",
            }
            completed = subprocess.run(
                [sys.executable, str(driver)], cwd=str(project), env=environment,
                text=True, capture_output=True, check=False)
        if expect_success and completed.returncode != 0:
            self.fail(f"the installed driver failed ({completed.returncode}):\n"
                      f"{completed.stdout}\n{completed.stderr}")
        for line in completed.stdout.splitlines():
            if line.startswith("RESULT_JSON "):
                return json.loads(line[len("RESULT_JSON "):])
        if expect_success:
            self.fail(f"the driver printed no result:\n{completed.stdout}\n"
                      f"{completed.stderr}")
        return {"returncode": completed.returncode, "stderr": completed.stderr,
                "stdout": completed.stdout}


class HostAgentIndependenceTests(InstalledProductionPathTestCase):
    """The installed E2E must prove things about the SKILL, never about the host.

    Every test in this module used to route `analysis.worker` at `claude` and hand the
    child the developer's own PATH. On a machine with a Claude CLI that passed; on all
    three CI runners it raised

        ORCA_ADAPTER_REQUIRES_AGENT_PROFILE: AGENT_COMMAND_NOT_FOUND:
        analysis.worker: 'claude' was not found on PATH

    before a single F-001/F-002 assertion ran. These tests fail if that dependency ever
    comes back -- by naming a host agent in the routing, or by letting the parent
    environment supply a command the sandbox did not create.
    """

    def test_the_routing_names_no_host_agent_cli(self) -> None:
        """Read off the profile the fixture actually writes, not off a comment."""
        routed = {line.split(":", 1)[1].strip()
                  for line in AGENT_PROFILE.splitlines()
                  if line.strip().startswith(("worker:", "reviewer:"))}
        self.assertEqual(routed, set(SHIM_COMMANDS))
        self.assertEqual(routed & HOST_AGENT_COMMANDS, set(),
                         "the installed E2E routes at a host agent CLI again")

    def test_every_routed_command_clears_the_real_agent_trust_boundary(self) -> None:
        """The sandbox names must not be a hole in the gate they pass through.

        Derived from the pinned policy contract rather than transcribed, so renaming a
        shim to something a production profile could not use fails here.
        """
        from scripts import skill_policy

        contract = skill_policy.load_policy_contract(SKILL / "SKILL.md")
        known = set(contract["known_agent_commands"])
        # Keep the "never route at these" list tied to the policy rather than to a copy
        # that can rot: if a new host agent CLI is added to the contract, it lands in
        # HOST_AGENT_COMMANDS too, and the routing test above starts checking it.
        self.assertEqual(
            known, set(HOST_AGENT_COMMANDS),
            "HOST_AGENT_COMMANDS has drifted from the policy's known_agent_commands")
        custom = re.compile(str(contract["custom_agent_command_pattern"]), re.ASCII)
        for command in SHIM_COMMANDS:
            with self.subTest(command=command):
                self.assertTrue(
                    skill_policy.AGENT_COMMAND_PATTERN.fullmatch(command),
                    f"{command!r} is not a simple PATH command token")
                self.assertTrue(
                    command in known or custom.fullmatch(command),
                    f"{command!r} is outside the agent trust boundary")

    def test_the_child_resolves_every_agent_command_inside_its_own_sandbox(self) -> None:
        """The decisive one: ask the CHILD where its agent commands came from.

        `shutil.which` is the exact call the launcher's availability gate makes, so this
        reports the resolution the run itself will get. Every routed command must resolve
        under the temporary project, and no host agent CLI may be reachable at all --
        which is what makes the pass below independent of the machine it runs on.
        """
        driver = _PRELUDE + textwrap.dedent(PROBE_AGENT_RESOLUTION).format(
            commands=repr(list(SHIM_COMMANDS)),
            host=repr(sorted(HOST_AGENT_COMMANDS)))
        result = self.drive(driver)
        for command, resolved in result["resolved"].items():
            with self.subTest(command=command):
                self.assertIsNotNone(
                    resolved,
                    f"{command} is not on the sandbox PATH; the availability gate "
                    "would refuse the run")
                self.assertTrue(
                    resolved.startswith(result["project"]),
                    f"{command} resolved to {resolved}, outside the test sandbox")
        self.assertEqual(
            {name: found for name, found in result["host_resolved"].items() if found},
            {},
            "a host agent CLI is reachable from the sandbox; a green run here would "
            "again be a fact about this machine")
        self.assertNotEqual(
            result["path"], os.environ.get("PATH", ""),
            "the child inherited the developer's PATH")

    @REQUIRES_LANGGRAPH
    def test_the_production_path_still_runs_with_no_host_agent_available(self) -> None:
        """The half that stops this becoming a hollow fix.

        MAJOR 1 could be 'fixed' by no longer reaching the adapter at all. This drives the
        SAME malformed -> repair -> success scenario F-002 asks for and asserts it
        completed through the real `OrcaAdapter`, while the sandbox above guarantees no
        host agent was available to it.
        """
        driver = (_PRELUDE + _RECORDER
                  + "RECORDER = Recorder("
                  + repr([WORKER_MALFORMED, WORKER_CLEAN, REVIEWER_PASS, FINAL_PASS])
                  + ")\n" + _RUN)
        result = self.drive(driver)
        self.assertEqual(result["adapter"], "OrcaAdapter")
        self.assertEqual(result["terminal_status"], "COMPLETED", result)
        self.assertEqual(result["tokens"].count("PREPARE_REPAIR"), 1,
                         "the bounded repair branch was not reached without a host agent")


class InstalledClosureTests(InstalledProductionPathTestCase):
    """The packaging half of F-002, asserted by importing, not by reading a list."""

    def test_the_installed_package_imports_the_orca_runtime_with_nothing_else(self) -> None:
        """Catches shipping a prefix of the closure: an ImportError here is exactly what
        an installed Coordinator would hit on its first production dispatch."""
        driver = _PRELUDE + textwrap.dedent('''
            from deterministic_workflow.orca_adapter import OrcaAdapter
            harness = runtime.OrcaRuntimeHarness
            print("RESULT_JSON " + json.dumps({
                "adapters": list(launcher.ADAPTERS),
                "harness_module": harness.__module__,
                "adapter_module": OrcaAdapter.__module__,
                "skill_md": str(runtime.SKILL_MD_PATH),
            }))
        ''')
        result = self.drive(driver)
        self.assertIn("orca", result["adapters"],
                      "the installed launcher still offers no production adapter")
        self.assertEqual(result["harness_module"], "orca_runtime_harness")
        self.assertEqual(result["adapter_module"],
                         "deterministic_workflow.orca_adapter")
        self.assertTrue(result["skill_md"].endswith("SKILL.md"))

    def test_the_installed_harness_resolves_its_own_skill_and_project(self) -> None:
        """`parents[1]` is the repository root in one layout and the SKILL ROOT in the
        other. Catches shipping the module with the repository's directory shape baked
        in, which resolves silently to the wrong place instead of failing.
        """
        driver = _PRELUDE + textwrap.dedent('''
            print("RESULT_JSON " + json.dumps({
                "project_root": str(runtime.PROJECT_ROOT),
                "cwd": os.getcwd(),
                "skill_md_exists": Path(runtime.SKILL_MD_PATH).is_file(),
            }))
        ''')
        result = self.drive(driver)
        self.assertEqual(result["project_root"], result["cwd"],
                         "the installed harness treats the Skill directory as the project")
        self.assertTrue(result["skill_md_exists"])


_CLI_DRIVER = '''
from deterministic_workflow.launcher import (LauncherError, build_parser,
                                             build_orca_adapter)

parsed = build_parser().parse_args(
    ["--adapter", "orca", "--state", "s.json", "--objective", "o",
     "--agent-profile", "installed"])
refusals = {}
spec = {"phases": ["ANALYSIS"], "risk": "high"}
base = Path(os.environ["ARTIFACT_BASE"])


def refuse(label, **kwargs):
    try:
        build_orca_adapter(spec, artifact_base=base, project_root=Path.cwd(), **kwargs)
    except LauncherError as exc:
        refusals[label] = str(exc)
    else:
        refusals[label] = ""


refuse("no_objective", objective="", agent_profile_name="installed")
refuse("no_profile", objective="o", agent_profile_name="")
refuse("unknown_profile", objective="o", agent_profile_name="absent")
print("RESULT_JSON " + json.dumps({
    "adapter": parsed.adapter,
    "objective": parsed.objective,
    "agent_profile": parsed.agent_profile,
    "refusals": refusals,
    "effects": sorted(p.name for p in base.iterdir()),
}))
'''


class InstalledCommandLineTests(InstalledProductionPathTestCase):
    """`run_workflow.py --adapter orca` is selectable, and refuses BEFORE any effect.

    The shipped command line is the door an operator actually uses, so its refusals are
    part of the feature: an installed run that cannot name a real agent must stop with a
    named reason rather than create a terminal that can never settle.
    """

    def test_the_shipped_cli_offers_the_adapter_and_refuses_an_incomplete_run(self) -> None:
        result = self.drive(_PRELUDE + _CLI_DRIVER)
        self.assertEqual(result["adapter"], "orca")
        self.assertEqual(result["objective"], "o")
        self.assertEqual(result["agent_profile"], "installed")
        self.assertTrue(result["refusals"]["no_objective"].startswith(
            "ORCA_ADAPTER_REQUIRES_OBJECTIVE"), result["refusals"])
        self.assertTrue(result["refusals"]["no_profile"].startswith(
            "ORCA_ADAPTER_REQUIRES_AGENT_PROFILE"), result["refusals"])
        self.assertTrue(result["refusals"]["unknown_profile"].startswith(
            "ORCA_ADAPTER_REQUIRES_AGENT_PROFILE"), result["refusals"])
        # Each refusal happened before an Orca Run could exist, so no run root was
        # provisioned: nothing was created that an operator would have to clean up.
        self.assertNotIn("artifacts", result["effects"])


@REQUIRES_LANGGRAPH
class InstalledRepairLoopTests(InstalledProductionPathTestCase):
    """The behavioural half: malformed -> bounded repair -> success, and exhaustion."""

    def test_malformed_output_is_repaired_on_the_installed_orca_path(self) -> None:
        """THE test F-002 asks for.

        A real `OrcaAdapter.start` -> `run_existing_task` -> `dispatch_context` ->
        `worker-start` dispatch returns the verbatim OS-42 defect; the run re-asks the
        SAME phase and iteration; the repaired settlement is accepted and the run
        completes. Nothing here is scripted at the adapter: the defect is carried in an
        agent BODY and classified by the production validator.
        """
        driver = (_PRELUDE + _RECORDER
                  + f"RECORDER = Recorder({[WORKER_MALFORMED, WORKER_CLEAN, REVIEWER_PASS, FINAL_PASS]!r})\n"
                  + _RUN)
        result = self.drive(driver)
        self.assertEqual(result["adapter"], "OrcaAdapter")
        self.assertEqual(result["harness"], "OrcaRuntimeHarness")
        self.assertEqual(result["terminal_status"], "COMPLETED",
                         f"the installed production run did not complete: {result}")
        self.assertEqual(result["tokens"].count("PREPARE_REPAIR"), 1,
                         "the bounded repair branch was not reached on the real path")
        # The repair must precede any Reviewer: a defective settlement never reaches one.
        tokens = result["tokens"]
        self.assertLess(tokens.index("PREPARE_REPAIR"),
                        tokens.index("PREPARE_PHASE_REVIEWER"),
                        "a Reviewer was dispatched before the repair settled")
        self.assertEqual(result["repair_attempts"], 0,
                         "the repair counter was not cleared by the clean settlement")
        # Same phase, same iteration: a repair is a re-ask, not a new round.
        self.assertEqual(result["phase_iterations"]["ANALYSIS"], 1)
        self.assertEqual(len(result["worker_start_commands"]), 4,
                         "the repair did not become its own real dispatch")

    def test_retry_exhaustion_records_the_error_field_values_and_count(self) -> None:
        """Every attempt malformed. The run must BLOCK with the exhaustion reason, and
        the reason must carry the validation error, the field path, the allowed set and
        the retry count -- read off the terminal the installed engine produced.
        """
        driver = (_PRELUDE + _RECORDER
                  + f"RECORDER = Recorder([], last_body={WORKER_MALFORMED!r})\n"
                  + _RUN)
        result = self.drive(driver)
        self.assertEqual(result["terminal_status"], "BLOCKED")
        reason = result["terminal_reason"]
        self.assertEqual(reason["code"], "DECISION_GATE_REPAIR_EXHAUSTED")
        self.assertEqual(reason["repair_attempts"], reason["max_repair_attempts"])
        self.assertGreaterEqual(reason["repair_attempts"], 1)
        defect = reason["defects"][0]
        self.assertEqual(defect["field_path"], "reversibility")
        self.assertIn("Fully reversible", defect["actual"])
        self.assertIn("reversible_in_run", defect["expected"])
        self.assertTrue(defect["message"])
        # Exhaustion is bounded: one initial dispatch plus MAX_REPAIR_ATTEMPTS repairs,
        # and never a Reviewer.
        self.assertEqual(len(result["worker_start_commands"]),
                         reason["max_repair_attempts"] + 1)
        self.assertNotIn("PREPARE_PHASE_REVIEWER", result["tokens"])


@REQUIRES_LANGGRAPH
class InstalledIdentityIngressTests(InstalledProductionPathTestCase):
    """Round-2 F-001, on the installed Orca path rather than on a validator in isolation.

    The defect this class exists for lives at the FIRST live consumer of a settlement --
    `OrcaRuntimeHarness._record_decision_from_attempt` -- which parsed the record with a
    validator that binds nothing to the dispatch, then overwrote `run`, `phase`,
    `iteration`, `boundary`, `source` and `role` with local values and appended the
    result to the decision ledger. A downstream refusal cannot undo that write, so the
    proof has to be the LEDGER: after a forged settlement it must hold nothing but the
    run-entry declaration `start_run` wrote.
    """

    @staticmethod
    def agent_rows(result):
        """Every published row that is not the sequence-0 run-entry declaration."""
        return [row for row in result["ledger"] if row.get("sequence") not in (0, None)]

    def test_a_forged_identity_creates_no_ledger_row_no_repair_and_no_reviewer(self) -> None:
        """THE test the round-2 finding asks for.

        A Worker settlement declaring the internally valid but foreign identity
        `run_foreign/design/99/B3/reviewer/reviewer` reaches the live ingress through a
        real dispatch. It must produce: no ledger row, no repair dispatch, no Reviewer
        dispatch, and a BLOCKED run. Catches a fix applied only to the engine classifier,
        which would still let the harness publish the rewritten row first.
        """
        driver = (_PRELUDE + _RECORDER
                  + f"RECORDER = Recorder([], last_body={WORKER_FORGED!r})\n"
                  + _RUN)
        result = self.drive(driver)
        self.assertEqual(
            self.agent_rows(result), [],
            f"a forged identity was published to the decision ledger: {result['ledger']}")
        self.assertEqual(result["terminal_status"], "BLOCKED", result["terminal_reason"])
        self.assertNotIn("PREPARE_REPAIR", result["tokens"],
                         "identity forgery was routed into the bounded repair branch")
        self.assertNotIn("PREPARE_PHASE_REVIEWER", result["tokens"],
                         "a Reviewer was dispatched for a forged settlement")
        self.assertEqual(result["repair_attempts"], 0,
                         "identity forgery spent repair budget")
        self.assertEqual(len(result["worker_start_commands"]), 1,
                         "the forged settlement was re-asked instead of refused")

    def test_the_run_entry_declaration_is_the_only_row_and_is_intact(self) -> None:
        """The ledger is append-only, so the fix must refuse the write rather than
        publish and correct it. Reads the run-entry row back to prove nothing else
        touched it."""
        driver = (_PRELUDE + _RECORDER
                  + f"RECORDER = Recorder([], last_body={WORKER_FORGED!r})\n"
                  + _RUN)
        result = self.drive(driver)
        self.assertEqual(len(result["ledger"]), 1, result["ledger"])
        entry = result["ledger"][0]
        self.assertEqual(entry["sequence"], 0)
        self.assertEqual(entry["boundary"], "B1")
        self.assertEqual(entry["run"], result["run_id"])

    def test_an_unknown_token_is_still_repaired_on_the_same_installed_path(self) -> None:
        """The line, asserted in both directions on ONE path.

        `boundary: "B9"` names no agent, so it stays an out-of-domain format error: the
        bounded repair loop re-asks it and the repaired settlement completes the run.
        Catches a fix that makes every mechanics mismatch fail closed -- which would
        remove the feature OS-42 exists to add.
        """
        driver = (_PRELUDE + _RECORDER
                  + "RECORDER = Recorder("
                  + repr([WORKER_UNKNOWN_BOUNDARY, WORKER_CLEAN, REVIEWER_PASS,
                          FINAL_PASS]) + ")\n"
                  + _RUN)
        result = self.drive(driver)
        self.assertEqual(result["terminal_status"], "COMPLETED",
                         f"an unknown boundary token was not repairable: {result}")
        self.assertEqual(result["tokens"].count("PREPARE_REPAIR"), 1)
        # And the repaired settlement IS published: the ledger gains real rows.
        self.assertTrue(self.agent_rows(result),
                        "a completed run published no decision record")
        for row in self.agent_rows(result):
            self.assertEqual(row["run"], result["run_id"])
            self.assertIn(row["boundary"], ("B2", "B3"))


@REQUIRES_LANGGRAPH
class InstalledOmittedIdentityTests(InstalledProductionPathTestCase):
    """Round-3 F-001 on the installed Orca path: OMISSION is a repairable FORM defect.

    The re-review's reproduction supplied no mechanics fields at all, got `CLEAR`, and
    the ledger then held an identity built entirely from local context -- a second shape
    of the same irreversible ordering problem the foreign-value arm already closed. These
    tests drive that record through the installed launcher, adapter and harness and read
    the real decision ledger back afterwards.
    """

    @staticmethod
    def agent_rows(result):
        return [row for row in result["ledger"] if row.get("sequence") not in (0, None)]

    def test_an_omitted_identity_is_repaired_and_only_then_published(self) -> None:
        """THE test the re-review asks for: no ledger row, bounded repair, then success.

        Attempt 1 declares no mechanics. The run must re-ask the SAME phase and iteration
        rather than dispatch a Reviewer or publish a synthesised row; the repaired
        settlement declares a complete identity and only THAT one reaches the ledger.
        """
        driver = (_PRELUDE + _RECORDER
                  + "RECORDER = Recorder("
                  + repr([WORKER_OMITS_MECHANICS, WORKER_CLEAN, REVIEWER_PASS,
                          FINAL_PASS]) + ")\n"
                  + _RUN)
        result = self.drive(driver)
        self.assertEqual(result["terminal_status"], "COMPLETED",
                         f"the omission was not repairable on the real path: {result}")
        self.assertEqual(result["tokens"].count("PREPARE_REPAIR"), 1,
                         "an omitted identity did not reach the bounded repair branch")
        tokens = result["tokens"]
        self.assertLess(tokens.index("PREPARE_REPAIR"),
                        tokens.index("PREPARE_PHASE_REVIEWER"),
                        "a Reviewer was dispatched before the identity was supplied")
        # Same phase, same iteration -- a repair is a re-ask, not a new round.
        self.assertEqual(result["phase_iterations"]["ANALYSIS"], 1)
        # And every published row carries an identity that was DECLARED, in this run.
        rows = self.agent_rows(result)
        self.assertTrue(rows, "a completed run published no decision record")
        for row in rows:
            self.assertEqual(row["run"], result["run_id"])
            self.assertIn(row["boundary"], ("B2", "B3"))

    def test_omission_exhaustion_publishes_nothing_and_dispatches_no_reviewer(self) -> None:
        """Every attempt omits the identity. Bounded, then BLOCKED -- and the ledger is
        left holding nothing but the run-entry declaration `start_run` wrote."""
        driver = (_PRELUDE + _RECORDER
                  + f"RECORDER = Recorder([], last_body={WORKER_OMITS_MECHANICS!r})\n"
                  + _RUN)
        result = self.drive(driver)
        self.assertEqual(result["terminal_status"], "BLOCKED")
        self.assertEqual(result["terminal_reason"]["code"],
                         "DECISION_GATE_REPAIR_EXHAUSTED")
        self.assertEqual(
            self.agent_rows(result), [],
            f"an undeclared identity was published: {result['ledger']}")
        self.assertEqual(len(result["ledger"]), 1, result["ledger"])
        self.assertEqual(result["ledger"][0]["sequence"], 0)
        self.assertNotIn("PREPARE_PHASE_REVIEWER", result["tokens"])
        # Bounded: one dispatch plus MAX_REPAIR_ATTEMPTS re-asks, and no more.
        self.assertEqual(len(result["worker_start_commands"]),
                         result["terminal_reason"]["max_repair_attempts"] + 1)

    def test_the_exhaustion_terminal_names_the_absent_field(self) -> None:
        """The terminal reason must say WHAT was missing, or an operator cannot act on
        it. Catches a fix that refuses omission with a bare code and no field path."""
        driver = (_PRELUDE + _RECORDER
                  + f"RECORDER = Recorder([], last_body={WORKER_OMITS_MECHANICS!r})\n"
                  + _RUN)
        result = self.drive(driver)
        fields = {defect["field_path"]
                  for defect in result["terminal_reason"]["defects"]}
        self.assertTrue(
            fields & {"ledger_schema_version", "boundary", "source", "role",
                      "run", "phase", "iteration"},
            f"the terminal names no absent mechanics field: {fields}")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
