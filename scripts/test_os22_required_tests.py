#!/usr/bin/env python3
"""OS-22 TEST phase: the cases PLAN's T-1..T-6 groups require that no existing test
already discharges.

IMPLEMENTATION already covers the bulk of PLAN's TEST work items in the module that
owns each subject (`test_run_logging.py`, `test_e2e_harness.py`,
`test_orca_runtime_contract.py`, `test_final_review_eval.py`,
`test_validate_skills.py`). This module holds only the residue -- the cross-cutting
cases that have no single owning module, and the cases whose existing coverage stops
one step short of what PLAN's case list actually asks for. Each class names its T
group and each test names the PLAN case it discharges, so the TEST artifact's
group-by-group assessment maps onto real code rather than onto prose.

Nothing here fixes production behaviour: TEST verifies, it does not implement.
"""

from __future__ import annotations

import ast
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

# `test_final_review_eval` imports `run_logging` by its bare name, which resolves only
# when `scripts/` is itself on the path -- true under `unittest discover -s scripts`,
# not true when this module is imported by dotted name. Reusing that module's fixtures
# rather than restating them is deliberate: its report constants are shaped by the
# key, and a second copy of key-shaped content in a second file is exactly what
# finding R2-T1 was about.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from scripts import run_logging
from scripts import test_final_review_eval as eval_fixtures
from scripts.run_logging import (
    FINAL_REVIEW_AUDIT_DIRNAME,
    FINAL_REVIEW_AUDIT_INPUT_FILENAME,
    FINAL_REVIEW_AUDIT_RECORD_FILENAME,
    FINAL_REVIEW_AUDIT_REPORT_FILENAME,
    ORCHESTRATOR_LOG_COLUMNS,
    ORCHESTRATOR_LOG_FILENAME,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
INSTALLED_RUN_LOGGING = (
    REPO_ROOT / "orca-worker-reviewer-orchestration" / "tools" / "run_logging.py"
)

PASSING_REPORT = (
    "# Final Adversarial Review\n\nRESULT: PASS\nREVIEW_VERDICT: PASS\n\n"
    "## Findings\n\nID: R1\nBlocking: NO\nLocation: scripts/x.py\n"
)

# release_manifest.USER_PATH_PATTERNS refuses a literal home-directory path anywhere
# under scripts/, so the one below is assembled at runtime -- the same technique
# test_run_logging.py's audit fixtures use.
_HOME = "/" + "Users" + "/"

# A stand-in for the spec `orca orchestration task-list --json` hands back. It carries
# one value from each redaction category so the retained input is not trivially clean.
STORED_SPEC = (
    "=== TASK ===\nphase: final_review\n"
    "orca orchestration send --dispatch-capability dcap_Zm9vYmFyYmF6cXV4MDEyMzQ1Njc4\n"
    f"workspace: {_HOME}someone/aiAssistedProjects/orca-skills\n"
)


def _audit_log_rows(root: Path) -> list[dict[str, str]]:
    """The ORCHESTRATOR_LOG rows this run wrote, parsed back into column dicts."""
    path = root / ORCHESTRATOR_LOG_FILENAME
    if not path.exists():
        return []
    rows: list[dict[str, str]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.startswith("|") or line.startswith("| ---"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) != len(ORCHESTRATOR_LOG_COLUMNS):
            continue
        row = dict(zip(ORCHESTRATOR_LOG_COLUMNS, cells))
        if row["event"] == "event":  # the header line
            continue
        rows.append(row)
    return rows


class _RunRootTestCase(unittest.TestCase):
    RUN_ID = "run_os22_test"

    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary_directory.name)
        self.root = self.base / "artifacts" / "runs" / self.RUN_ID
        self.root.mkdir(parents=True)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def write_report(self, text: str, attempt: int = 1) -> Path:
        suffix = "" if attempt == 1 else f"_iteration{attempt}"
        path = self.root / f"FINAL_REVIEW{suffix}.md"
        path.write_text(text, encoding="utf-8")
        return path

    def write_record(self, **kwargs) -> Path:
        kwargs.setdefault("final_review_attempt", 1)
        kwargs.setdefault("task_id", "task_aaa")
        kwargs.setdefault("dispatch_id", "ctx_bbb")
        kwargs.setdefault("capture", False)
        return run_logging.write_final_review_audit_record(
            self.RUN_ID, base=self.base, **kwargs
        )

    def record_json(self, directory: Path) -> dict:
        return json.loads(
            (directory / FINAL_REVIEW_AUDIT_RECORD_FILENAME).read_text(encoding="utf-8")
        )


class LogInputReportIdentityJoinTests(_RunRootTestCase):
    """T-1, last case: log <-> input <-> report identity consistency.

    `FinalReviewAuditEmissionTests.test_the_record_is_joined_to_the_log_on_the_
    existing_columns` already joins the log row to `record.json`. It stops there:
    the two RETAINED ARTIFACTS are never re-read off disk, so a record that named an
    input or a report it did not actually publish would still pass it. These tests
    close the join at the file level -- from the log row's `task_id`/`dispatch_id`
    columns through to the bytes of `input.md` and `report.md`.
    """

    def published_with_capture(self) -> Path:
        self.write_report(PASSING_REPORT)
        with patch.object(
            run_logging, "capture_stored_task_spec", return_value=(STORED_SPEC, "")
        ), patch.object(
            run_logging,
            "capture_delivery_evidence",
            return_value=(
                {"dispatch_id": "ctx_bbb", "dispatched_at": "2026-08-26T00:00:00Z"},
                "",
            ),
        ):
            return self.write_record(
                capture=True, provenance_state="accepted", settlement_state="settled"
            )

    def test_the_log_row_names_the_directory_holding_both_artifacts(self) -> None:
        published = self.published_with_capture()

        rows = [
            row
            for row in _audit_log_rows(self.root)
            if row["event"].startswith("final_review_audit")
        ]
        self.assertTrue(rows, "the write produced no audit row to join on")
        for row in rows:
            with self.subTest(event=row["event"]):
                key = run_logging.final_review_dispatch_key(
                    1, row["task_id"], row["dispatch_id"]
                )
                # The join is made from the LOG's own columns, not from the path the
                # test happens to hold, so a row that named another dispatch fails.
                self.assertEqual(
                    (self.root / FINAL_REVIEW_AUDIT_DIRNAME / key).resolve(),
                    published.resolve(),
                )
                self.assertTrue(
                    (published / FINAL_REVIEW_AUDIT_INPUT_FILENAME).is_file()
                )
                self.assertTrue(
                    (published / FINAL_REVIEW_AUDIT_REPORT_FILENAME).is_file()
                )
                self.assertIn(
                    f"{FINAL_REVIEW_AUDIT_DIRNAME}/{key}/"
                    f"{FINAL_REVIEW_AUDIT_RECORD_FILENAME}",
                    row["detail"],
                )

    def test_both_retained_artifacts_rehash_to_the_digests_the_record_states(
        self,
    ) -> None:
        published = self.published_with_capture()
        record = self.record_json(published)

        for filename, section in (
            (FINAL_REVIEW_AUDIT_INPUT_FILENAME, "stored_task_spec"),
            (FINAL_REVIEW_AUDIT_REPORT_FILENAME, "report"),
        ):
            with self.subTest(artifact=filename):
                data = (published / filename).read_bytes()
                self.assertEqual(
                    run_logging.sha256_bytes(data),
                    record[section]["artifact_digest_post_redaction"],
                )
                self.assertEqual(
                    len(data), record[section]["byte_length_post_redaction"]
                )
                # The record must point at the file it hashed, by the run-relative
                # path a reader would follow.
                self.assertEqual(
                    record[section]["artifact_path"],
                    f"{FINAL_REVIEW_AUDIT_DIRNAME}/{record['dispatch_key']}/{filename}",
                )

    def test_the_record_and_both_artifacts_agree_on_one_dispatch_identity(self) -> None:
        published = self.published_with_capture()
        record = self.record_json(published)

        rows = [
            row
            for row in _audit_log_rows(self.root)
            if row["event"].startswith("final_review_audit")
        ]
        self.assertEqual(
            {(row["task_id"], row["dispatch_id"]) for row in rows},
            {(record["task_id"], record["dispatch_id"])},
        )
        self.assertEqual(published.name, record["dispatch_key"])
        self.assertIn(record["task_id"], record["dispatch_key"])
        self.assertIn(record["dispatch_id"], record["dispatch_key"])

    def test_a_second_dispatch_joins_to_its_own_row_and_its_own_artifacts(self) -> None:
        """Two dispatches in one attempt: the join must not collapse them."""
        first = self.published_with_capture()
        second_spec = STORED_SPEC + "retry: yes\n"
        self.write_report(PASSING_REPORT.replace("R1", "R2"))
        with patch.object(
            run_logging, "capture_stored_task_spec", return_value=(second_spec, "")
        ), patch.object(
            run_logging, "capture_delivery_evidence", return_value=(None, "unavailable")
        ):
            second = self.write_record(
                task_id="task_ccc",
                dispatch_id="ctx_ddd",
                capture=True,
                provenance_state="voided",
                void_reason="superseded_by_retry",
                settlement_state="settled",
            )

        self.assertNotEqual(first, second)
        by_key = {
            row["event"] + "|" + row["task_id"] + "|" + row["dispatch_id"]: row
            for row in _audit_log_rows(self.root)
            if row["event"].startswith("final_review_audit")
        }
        self.assertTrue(any("task_aaa|ctx_bbb" in key for key in by_key))
        self.assertTrue(any("task_ccc|ctx_ddd" in key for key in by_key))
        self.assertNotEqual(
            (first / FINAL_REVIEW_AUDIT_INPUT_FILENAME).read_bytes(),
            (second / FINAL_REVIEW_AUDIT_INPUT_FILENAME).read_bytes(),
        )
        self.assertEqual(
            self.record_json(first)["stored_task_spec"]["input_digest_pre_redaction"],
            run_logging.sha256_text(STORED_SPEC),
        )
        self.assertEqual(
            self.record_json(second)["stored_task_spec"]["input_digest_pre_redaction"],
            run_logging.sha256_text(second_spec),
        )


class FailureEvidenceWithoutBaselineTests(_RunRootTestCase):
    """T-2, third case: the retained failure record satisfies section 3 while leaving
    the section 7 baseline unsatisfied until a dispatch settles with a usable report.

    `AuditProvenanceTests` proves each half separately -- that a voided record is
    never returned as a verdict, and that an attempt with no accepted dispatch
    produced none. Neither proves them TOGETHER, which is the whole claim: evidence
    retained (section 3 satisfied) and baseline still open (section 7 unsatisfied) is
    a single state, and the failure mode PLAN names is reporting the first as if it
    were the second.
    """

    def dispatch_input_failure(self) -> Path:
        with patch.object(
            run_logging, "capture_stored_task_spec", return_value=(STORED_SPEC, "")
        ), patch.object(
            run_logging, "capture_delivery_evidence", return_value=(None, "no dispatch")
        ):
            return self.write_record(
                task_id="task_failed",
                dispatch_id="ctx_failed",
                capture=True,
                provenance_state="voided",
                void_reason="dispatch_input_rejected",
                settlement_state="not_settled",
                failure_detail="agent_prompt_blocked",
                observed_input_bytes=len(STORED_SPEC.encode("utf-8")),
            )

    def test_section_3_evidence_is_retained_and_section_7_stays_unsatisfied(
        self,
    ) -> None:
        published = self.dispatch_input_failure()
        record = self.record_json(published)

        # Section 3: the pre-failure input evidence survived the failure.
        self.assertEqual(record["stored_task_spec"]["capture_status"], "captured")
        self.assertEqual(
            record["stored_task_spec"]["input_digest_pre_redaction"],
            run_logging.sha256_text(STORED_SPEC),
        )
        self.assertTrue(
            (published / FINAL_REVIEW_AUDIT_INPUT_FILENAME).read_bytes(),
            "the retained input is empty, so nothing was preserved",
        )
        self.assertEqual(record["failure_detail"], "agent_prompt_blocked")
        self.assertEqual(
            record["observed_input_bytes"], len(STORED_SPEC.encode("utf-8"))
        )
        self.assertEqual(record["void_reason"], "dispatch_input_rejected")

        # Section 7: the same run still has no accepted verdict to score.
        provenance = run_logging.read_final_review_attempt_provenance(
            self.RUN_ID, 1, base=self.base
        )
        self.assertIsNone(provenance["accepted_dispatch_key"])
        self.assertIn(
            "no_accepted_dispatch",
            [violation["code"] for violation in provenance["violations"]],
        )
        self.assertEqual(provenance["records"], [published.name])
        # And there is no usable report behind the failed dispatch either.
        self.assertEqual(record["report"]["capture_status"], "absent")

    def test_the_baseline_opens_only_once_a_later_dispatch_settles_with_a_report(
        self,
    ) -> None:
        failed = self.dispatch_input_failure()
        before = (failed / FINAL_REVIEW_AUDIT_RECORD_FILENAME).read_bytes()

        self.write_report(PASSING_REPORT)
        settled = self.write_record(
            task_id="task_retry",
            dispatch_id="ctx_retry",
            provenance_state="accepted",
            settlement_state="settled",
        )

        provenance = run_logging.read_final_review_attempt_provenance(
            self.RUN_ID, 1, base=self.base
        )
        self.assertEqual(provenance["accepted_dispatch_key"], settled.name)
        self.assertNotIn(
            "no_accepted_dispatch",
            [violation["code"] for violation in provenance["violations"]],
        )
        # The failed dispatch's evidence is untouched by the retry that replaced it.
        self.assertEqual((failed / FINAL_REVIEW_AUDIT_RECORD_FILENAME).read_bytes(),
                         before)
        self.assertIn(failed.name, provenance["records"])
        self.assertEqual(
            self.record_json(settled)["report"]["parsed"]["result"], "PASS"
        )

    def test_a_settled_dispatch_with_no_usable_report_still_leaves_it_unsatisfied(
        self,
    ) -> None:
        """Settling is not the bar -- a usable report is. `report_missing` must not
        become an accepted verdict merely because the dispatch came back."""
        provenance, void_reason, settlement = (
            run_logging.resolve_final_review_provenance(
                settled=True,
                report_capture_status="absent",
                report_parse_status="not_attempted",
            )
        )
        self.assertEqual(provenance, "voided")
        self.assertEqual(void_reason, "report_missing")

        self.write_record(
            task_id="task_empty",
            dispatch_id="ctx_empty",
            provenance_state=provenance,
            void_reason=void_reason,
            settlement_state=settlement,
        )

        read = run_logging.read_final_review_attempt_provenance(
            self.RUN_ID, 1, base=self.base
        )
        self.assertIsNone(read["accepted_dispatch_key"])


class ObservedSizeThresholdGuardTests(unittest.TestCase):
    """T-2, guard case: no observed `agent_prompt_blocked` size is a product constant.

    `RetainedArtifactSecurityTests.test_the_implementation_hard_codes_no_observed_
    input_size` guards the OS-22 section of `scripts/run_logging.py` alone. The
    threshold PLAN forbids could just as easily be written at an EMISSION site or in
    the scorer, neither of which that test reads. This widens the guard to every
    OS-22 production surface, including the installed twin.
    """

    FORBIDDEN = ("14805", "5553", "2269", "14.8", "5.5", "2.3")

    def os22_section(self, path: Path) -> str:
        source = path.read_text(encoding="utf-8")
        self.assertIn("# ---- OS-22:", source, f"{path} lost its OS-22 marker")
        return source.split("# ---- OS-22:")[1]

    def test_neither_run_logging_copy_carries_a_threshold_constant(self) -> None:
        for path in (REPO_ROOT / "scripts" / "run_logging.py", INSTALLED_RUN_LOGGING):
            for forbidden in self.FORBIDDEN:
                with self.subTest(path=path.name, constant=forbidden):
                    self.assertNotIn(forbidden, self.os22_section(path))

    def test_no_emission_site_carries_a_threshold_constant(self) -> None:
        """The two places that actually call the writer."""
        for module, function in (
            ("orca_runtime_harness.py", "_log_final_review_audit"),
            ("e2e_harness.py", "_write_final_review_audit"),
        ):
            path = REPO_ROOT / "scripts" / module
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source)
            bodies = [
                ast.get_source_segment(source, node) or ""
                for node in ast.walk(tree)
                if isinstance(node, ast.FunctionDef) and node.name == function
            ]
            self.assertTrue(bodies, f"{module} no longer defines {function}")
            for body in bodies:
                for forbidden in self.FORBIDDEN:
                    with self.subTest(module=module, constant=forbidden):
                        self.assertNotIn(forbidden, body)

    FORBIDDEN_NUMBERS = (14805, 5553, 2269, 14.8, 5.5, 2.3)

    def test_the_scorer_carries_no_threshold_constant_either(self) -> None:
        """Checked as NUMERIC LITERALS rather than as substrings: PLAN forbids these
        values as thresholds, and a substring scan of a whole module would also fire
        on a digest or a version string that merely contains the digits."""
        source = (REPO_ROOT / "scripts" / "final_review_eval.py").read_text(
            encoding="utf-8"
        )
        numbers = [
            node.value
            for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.Constant) and isinstance(node.value, (int, float))
            and not isinstance(node.value, bool)
        ]
        self.assertTrue(numbers, "no numeric literal was read, so nothing was checked")
        for forbidden in self.FORBIDDEN_NUMBERS:
            with self.subTest(constant=forbidden):
                self.assertNotIn(forbidden, numbers)

    def test_observed_input_bytes_is_never_compared_against_anything(self) -> None:
        """The field is data the runtime reports. A comparison would make it a
        threshold whatever number sat on the other side of the operator."""
        source = (REPO_ROOT / "scripts" / "run_logging.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Compare):
                continue
            operands = [node.left, *node.comparators]
            for operand in operands:
                name = (
                    operand.id
                    if isinstance(operand, ast.Name)
                    else operand.value
                    if isinstance(operand, ast.Constant) and isinstance(operand.value, str)
                    else None
                )
                self.assertNotEqual(
                    name,
                    "observed_input_bytes",
                    f"observed_input_bytes is compared at line {node.lineno}",
                )


class NoWriteSideGitOnAnyWorkflowPathTests(unittest.TestCase):
    """T-5 / DEC-6: retention is not "commit everything", so no workflow path may run
    `git add`, `commit` or `push` on run artifacts.

    `EvidenceBundleTests.test_this_module_runs_no_write_side_git_command` asserts
    this for `run_logging.py` only. The modules that DRIVE a run -- and the scorer
    that reads a fixture -- are exactly where an "and then commit the evidence" line
    would be added, so the guard has to cover them too.
    """

    WRITE_SIDE = {
        "add",
        "commit",
        "push",
        "rm",
        "reset",
        "checkout",
        "clean",
        "stash",
        "tag",
        "merge",
    }
    MODULES = (
        "run_logging.py",
        "orca_runtime_harness.py",
        "e2e_harness.py",
        "final_review_eval.py",
        "task_context.py",
    )

    def git_argv_literals(self, path: Path) -> list[list[ast.expr]]:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        found = []
        for node in ast.walk(tree):
            if not isinstance(node, (ast.List, ast.Tuple)) or not node.elts:
                continue
            first = node.elts[0]
            if isinstance(first, ast.Constant) and first.value == "git":
                found.append(list(node.elts[1:]))
        return found

    def git_helper_arguments(self, path: Path) -> list[str]:
        """`run_logging.py` shells out as `["git", *args]` through a local `_git()`
        wrapper, so the subcommand never appears in the argv literal at all. The
        wrapper's own call sites carry it, and they are what this reads."""
        tree = ast.parse(path.read_text(encoding="utf-8"))
        wrappers = {
            node.name
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and any(
                isinstance(inner, ast.List)
                and inner.elts
                and isinstance(inner.elts[0], ast.Constant)
                and inner.elts[0].value == "git"
                for inner in ast.walk(node)
            )
        }
        arguments: list[str] = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = node.func.id if isinstance(node.func, ast.Name) else None
            if name not in wrappers:
                continue
            arguments.extend(
                argument.value
                for argument in node.args
                if isinstance(argument, ast.Constant)
                and isinstance(argument.value, str)
            )
        return arguments

    def test_the_readers_actually_find_the_git_calls_they_are_guarding(self) -> None:
        """Non-vacuity: a negative assertion over a walker that finds nothing proves
        nothing. `run_logging.py` provably shells out to git, so both readers that
        clear the other modules are known to work."""
        path = REPO_ROOT / "scripts" / "run_logging.py"
        self.assertTrue(
            self.git_argv_literals(path), "the git argv literals were not found at all"
        )
        self.assertIn("rev-parse", self.git_helper_arguments(path))

    def test_every_git_subcommand_either_module_passes_is_read_only(self) -> None:
        read_only = {"rev-parse", "status", "--porcelain", "HEAD", "--abbrev-ref"}
        for path in (REPO_ROOT / "scripts" / "run_logging.py", INSTALLED_RUN_LOGGING):
            arguments = self.git_helper_arguments(path)
            self.assertTrue(arguments, f"{path.name} exposed no git arguments to read")
            for argument in arguments:
                with self.subTest(path=path.name, argument=argument):
                    self.assertNotIn(argument, self.WRITE_SIDE)
                    self.assertIn(argument, read_only)

    def test_no_module_on_a_workflow_path_runs_a_write_side_git_subcommand(
        self,
    ) -> None:
        for module in self.MODULES:
            path = REPO_ROOT / "scripts" / module
            for argv in self.git_argv_literals(path):
                for element in argv:
                    if not isinstance(element, ast.Constant) or not isinstance(
                        element.value, str
                    ):
                        continue
                    with self.subTest(module=module, arg=element.value):
                        self.assertNotIn(element.value, self.WRITE_SIDE)

    def test_the_installed_twin_is_covered_by_the_same_guard(self) -> None:
        for argv in self.git_argv_literals(INSTALLED_RUN_LOGGING):
            for element in argv:
                if isinstance(element, ast.Constant) and isinstance(
                    element.value, str
                ):
                    with self.subTest(arg=element.value):
                        self.assertNotIn(element.value, self.WRITE_SIDE)

    def test_no_shelled_out_git_string_smuggles_a_write_side_subcommand(self) -> None:
        """A `git add` written as one string rather than an argv list would slip past
        the AST guard above, so the source text is checked for it as well."""
        pattern = re.compile(
            r"""git\s+(add|commit|push|reset|checkout|clean|stash|rm)\b"""
        )
        for module in self.MODULES:
            path = REPO_ROOT / "scripts" / module
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source)
            # Docstrings and comments are prose about the rule; only real string
            # literals that could reach a shell are in scope.
            docstrings = set()
            for node in ast.walk(tree):
                if isinstance(
                    node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
                ):
                    doc = ast.get_docstring(node, clean=False)
                    if doc:
                        docstrings.add(doc)
            for node in ast.walk(tree):
                if not isinstance(node, ast.Constant) or not isinstance(
                    node.value, str
                ):
                    continue
                if node.value in docstrings:
                    continue
                match = pattern.search(node.value)
                with self.subTest(module=module, line=node.lineno):
                    self.assertIsNone(
                        match,
                        f"{module}:{node.lineno} carries {match.group(0) if match else ''!r}",
                    )


class OrcaRuntimeDispatchPathNeutralityTests(unittest.TestCase):
    """T-6, third assertion, on the OTHER dispatch path.

    `FinalReviewObservabilityNeutralityTests.test_the_audit_module_is_not_reachable_
    from_the_dispatch_path` proves non-invocation through `e2e_harness`. The live
    orchestration runtime is a separate module with its own spec assembly and its own
    dispatch call, and it is the one a real Final Review actually goes through, so it
    needs the same tripwire rather than an argument by analogy.
    """

    TRIPWIRES = (
        "redact_text",
        "capture_stored_task_spec",
        "capture_delivery_evidence",
        "write_final_review_audit_record",
    )

    def test_no_audit_surface_is_reached_before_the_orca_runtime_dispatch_returns(
        self,
    ) -> None:
        from scripts import orca_runtime_harness as runtime_module
        from scripts.test_orca_runtime_contract import (
            EchoingTerminalExec,
            FINAL_REVIEW_PHASE,
            FinalReviewAuditEmissionTests,
        )

        case = FinalReviewAuditEmissionTests("test_a_final_review_dispatch_writes_one_record")
        case.setUp()
        try:
            recorder = EchoingTerminalExec()
            harness = case.started_harness(recorder)
            case.write_report(harness.run_id, 1)
            case.arm(recorder, "ctx_neutral", "task_neutral")

            patches = [
                patch.object(
                    run_logging,
                    name,
                    side_effect=AssertionError(
                        f"run_logging.{name} was reached from the orca runtime's "
                        "spec-assembly -> dispatch path"
                    ),
                )
                for name in self.TRIPWIRES
            ]
            # The settlement-path emission is SUPPOSED to call the writer, so it is
            # suppressed rather than left to trip its own tripwire. Anything that
            # trips now did so before the dispatch settled.
            with patch.object(
                runtime_module.OrcaRuntimeHarness,
                "_log_final_review_audit",
                lambda *args, **kwargs: None,
            ):
                for entry in patches:
                    entry.start()
                try:
                    attempt, _body = harness.run_attempt(
                        "reviewer",
                        1,
                        "pass",
                        phase=FINAL_REVIEW_PHASE,
                        round_kind="final_review",
                    )
                finally:
                    for entry in reversed(patches):
                        entry.stop()

            self.assertEqual(attempt.outcome, "succeeded")
            self.assertTrue(
                recorder.specs, "no Task spec was assembled, so nothing was proved"
            )
            self.assertEqual(harness._logging_errors, [])
        finally:
            case.tearDown()

    def test_the_runtime_module_reaches_the_writer_from_settlement_only(self) -> None:
        """The structural half of the same claim: `write_final_review_audit_record`
        has exactly one call site in the runtime module, and it is inside the
        settlement-path method -- not inside spec assembly or the dispatch call."""
        source = (REPO_ROOT / "scripts" / "orca_runtime_harness.py").read_text(
            encoding="utf-8"
        )
        tree = ast.parse(source)
        enclosing: list[str] = []
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            segment = ast.get_source_segment(source, node) or ""
            if "write_final_review_audit_record" in segment:
                # Only the innermost function that literally contains the name.
                inner = [
                    child.name
                    for child in ast.walk(node)
                    if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and child is not node
                    and "write_final_review_audit_record"
                    in (ast.get_source_segment(source, child) or "")
                ]
                if not inner:
                    enclosing.append(node.name)
        self.assertEqual(enclosing, ["_log_final_review_audit"])

    def test_the_spec_builder_never_names_an_audit_surface(self) -> None:
        source = (REPO_ROOT / "scripts" / "orca_runtime_harness.py").read_text(
            encoding="utf-8"
        )
        tree = ast.parse(source)
        builders = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef)
            and "render_task_spec" in (ast.get_source_segment(source, node) or "")
        ]
        self.assertTrue(builders, "no spec-assembly function was found")
        for node in builders:
            segment = ast.get_source_segment(source, node) or ""
            for name in self.TRIPWIRES:
                with self.subTest(function=node.name, surface=name):
                    self.assertNotIn(name, segment)


# --- iteration 4, downstream revalidation (§17 T5a) -------------------------------
# DESIGN's D-C/D-E were corrected and IMPLEMENTATION landed redaction/1.1 + the
# closed-world false-positive rate. Both corrections changed shipped behaviour that
# PLAN's T-1/T-3 and T-4 groups own, so the two classes below carry the residue this
# module exists for: the cases the owning modules stop one step short of.


# A foreign absolute root of the shape the shipped baseline actually leaked, and the
# one-segment root D3-001 named. Assembled at runtime for the same reason _HOME is.
_SCRATCH_ROOT = "/private/" + "tmp/claude-501/-Users-someone-orca-skills/s-1/scratchpad"
_ONE_SEGMENT_ROOT = "/" + "luminous"

# The stored spec, carrying one value from category 5 as well: a deep session-scratch
# path and a one-segment root, neither of which redaction/1.0's three-home allowlist
# recognised.
STORED_SPEC_WITH_FOREIGN_PATHS = (
    STORED_SPEC
    + f"report: {_SCRATCH_ROOT}/REPORT.md\n"
    + f"root: {_ONE_SEGMENT_ROOT}\n"
)


class ForeignAbsolutePathAcrossThePublishedUnitTests(_RunRootTestCase):
    """T-3 x T-1, iteration 4: redaction/1.1 category 5 over the WHOLE published unit.

    `test_run_logging.ForeignAbsolutePathRedactionTests` proves the pattern, and
    `RetainedPathFieldRecordTests` sweeps `record.json`. Neither reads `input.md`,
    `report.md` or `ORCHESTRATOR_LOG.md` back off disk, and the log file is not part of
    any record -- so the published unit as a whole (three files plus the row that names
    them) has no owning module. That cross-file surface is what these tests close, and
    they close it with the PRODUCTION pattern rather than a restated one, so a future
    edit to `_FOREIGN_ABSOLUTE_PATH` cannot leave this test asserting the old rule.
    """

    PLACEHOLDER = run_logging.FOREIGN_PATH_PLACEHOLDER

    def published_from_a_foreign_report(self, scratch: Path) -> Path:
        """Publish one record whose report lives outside every root the ladder knows,
        with a stored spec that carries two category-5 values."""
        outside = scratch / "REPORT.md"
        outside.write_text(PASSING_REPORT, encoding="utf-8")
        with patch.object(
            run_logging,
            "capture_stored_task_spec",
            return_value=(STORED_SPEC_WITH_FOREIGN_PATHS, ""),
        ), patch.object(
            run_logging,
            "capture_delivery_evidence",
            return_value=(
                {"dispatch_id": "ctx_bbb", "dispatched_at": "2026-08-26T00:00:00Z"},
                "",
            ),
        ):
            return self.write_record(
                capture=True,
                provenance_state="accepted",
                settlement_state="settled",
                report_path=outside,
            )

    def test_no_file_of_the_published_unit_matches_the_category_five_pattern(
        self,
    ) -> None:
        """The unit is a fixed point of the corrected rule -- checked with the shipped
        pattern, over every published byte INCLUDING the log the record never sees."""
        with tempfile.TemporaryDirectory() as scratch:
            published = self.published_from_a_foreign_report(Path(scratch))

        files = [
            published / FINAL_REVIEW_AUDIT_INPUT_FILENAME,
            published / FINAL_REVIEW_AUDIT_REPORT_FILENAME,
            published / FINAL_REVIEW_AUDIT_RECORD_FILENAME,
            self.root / ORCHESTRATOR_LOG_FILENAME,
        ]
        for path in files:
            with self.subTest(published=path.name):
                self.assertTrue(path.is_file(), f"{path.name} was not published")
                text = path.read_text(encoding="utf-8")
                self.assertEqual(
                    run_logging._FOREIGN_ABSOLUTE_PATH.findall(text),
                    [],
                    f"a foreign absolute path survived into {path.name}",
                )
                for fragment in ("claude-501", "luminous", "/private/"):
                    self.assertNotIn(fragment, text)

    def test_the_record_counts_the_new_category_and_stamps_the_executable_policy(
        self,
    ) -> None:
        """The category is EXERCISED here, not merely present in the table: the count
        the record publishes must be non-zero, and the stamp must be the one policy
        version that is executable."""
        self.assertIn(
            "foreign_absolute_path",
            [name for name, _pattern, _replacement in run_logging.REDACTION_CATEGORIES],
        )
        with tempfile.TemporaryDirectory() as scratch:
            published = self.published_from_a_foreign_report(Path(scratch))

        record = self.record_json(published)
        counts = {
            entry["category"]: entry["count"]
            for entry in record["stored_task_spec"]["redactions"]
        }
        # Two category-5 values went in: the deep scratch path and the one-segment root.
        self.assertGreaterEqual(counts.get("foreign_absolute_path", 0), 2)
        # Category 4 still owns the home path and still leaves the tail readable, so
        # category 5 did not swallow it.
        self.assertGreaterEqual(counts.get("absolute_local_path", 0), 1)
        self.assertIn(
            "aiAssistedProjects/orca-skills",
            (published / FINAL_REVIEW_AUDIT_INPUT_FILENAME).read_text(encoding="utf-8"),
        )
        self.assertEqual(
            record["stored_task_spec"]["redaction_policy_version"],
            run_logging.FINAL_REVIEW_REDACTION_POLICY_VERSION,
        )
        self.assertEqual(
            run_logging.FINAL_REVIEW_REDACTION_POLICY_VERSION, "redaction/1.1"
        )

    def test_the_superseded_policy_cannot_be_re_executed_over_the_retained_input(
        self,
    ) -> None:
        """The old rule is not merely out of favour, it is unavailable -- which is what
        stops a reader from re-deriving the retained digests under redaction/1.0 and
        concluding the one-segment root was fine."""
        with self.assertRaises(run_logging.RunLoggingError):
            run_logging.redact_text(
                STORED_SPEC_WITH_FOREIGN_PATHS, policy_version="redaction/1.0"
            )
        redacted, _counts = run_logging.redact_text(STORED_SPEC_WITH_FOREIGN_PATHS)
        # No segment-count floor: the one-segment root is replaced WHOLE, and nothing
        # of it -- not even the leading separator -- is borrowed into the output.
        self.assertNotIn(_ONE_SEGMENT_ROOT, redacted)
        self.assertIn(f"root: {self.PLACEHOLDER}\n", redacted)

    def test_the_identity_join_still_holds_when_the_report_path_is_replaced_whole(
        self,
    ) -> None:
        """T-1's join must not be collateral damage of T-3's fix: the record may no
        longer say WHERE the report was, but it must still say WHICH bytes it was, and
        the log row must still reach them."""
        with tempfile.TemporaryDirectory() as scratch:
            published = self.published_from_a_foreign_report(Path(scratch))

        record = self.record_json(published)
        self.assertEqual(record["report"]["contract_path"], self.PLACEHOLDER)
        # Every closed-table field is still one of P1..P4 after the write.
        for dotted in run_logging.FINAL_REVIEW_RETAINED_PATH_FIELDS:
            section, _, field = dotted.partition(".")
            with self.subTest(field=dotted):
                run_logging.assert_retained_path_field(record[section][field])

        rows = [
            row
            for row in _audit_log_rows(self.root)
            if row["event"].startswith("final_review_audit")
        ]
        self.assertTrue(rows, "the write produced no audit row to join on")
        for row in rows:
            with self.subTest(event=row["event"]):
                key = run_logging.final_review_dispatch_key(
                    1, row["task_id"], row["dispatch_id"]
                )
                self.assertEqual(
                    (self.root / FINAL_REVIEW_AUDIT_DIRNAME / key).resolve(),
                    published.resolve(),
                )
        for filename, section in (
            (FINAL_REVIEW_AUDIT_INPUT_FILENAME, "stored_task_spec"),
            (FINAL_REVIEW_AUDIT_REPORT_FILENAME, "report"),
        ):
            with self.subTest(artifact=filename):
                data = (published / filename).read_bytes()
                self.assertEqual(
                    run_logging.sha256_bytes(data),
                    record[section]["artifact_digest_post_redaction"],
                )
                self.assertEqual(
                    record[section]["artifact_path"],
                    f"{FINAL_REVIEW_AUDIT_DIRNAME}/{record['dispatch_key']}/{filename}",
                )
        # And the pre-redaction identity of the report is still the untouched source,
        # so "which bytes" survives the loss of "which path".
        self.assertEqual(
            record["report"]["report_digest_pre_redaction"],
            run_logging.sha256_text(PASSING_REPORT),
        )


class ClosedWorldMetricContractTests(unittest.TestCase):
    """T-4, iteration 4: section 6's unmatched-finding rule after the D-E correction.

    PLAN's T-4 case reads "an unmatched finding is UNADJUDICATED, never auto-FP".
    The corrected section 6 keeps the second half exactly and qualifies the first: an
    explicit, signed closed-world attestation reclassifies it as
    ATTESTED_FALSE_POSITIVE. `test_final_review_eval.ClosedWorldFalsePositiveRateTests`
    proves the arithmetic at the function boundary. What it does not do -- and what
    PLAN's case actually asks for -- is show that the SAME findings document takes both
    answers only because the adjudication input changed, end to end through the CLI,
    and that no other route reaches the attested class at all.
    """

    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        report = self.root / "report.md"
        report.write_text(
            eval_fixtures.PERFECT_REPORT + eval_fixtures.RESOLVED_NOISE_FINDING,
            encoding="utf-8",
        )
        self.findings = self.root / "findings.json"
        completed = eval_fixtures.run_cli(
            "parse-report", "--report", str(report), "--out", str(self.findings)
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def score(self, adjudications: dict | None) -> tuple[dict, int]:
        argv = [
            "score",
            "--findings",
            str(self.findings),
            "--key",
            str(eval_fixtures.KEY_PATH),
            "--out",
            str(self.root / "metrics.json"),
            "--require-precision",
        ]
        if adjudications is not None:
            path = self.root / "adjudications.json"
            path.write_text(json.dumps(adjudications), encoding="utf-8")
            argv += ["--adjudications", str(path)]
        completed = eval_fixtures.run_cli(*argv)
        return (
            json.loads((self.root / "metrics.json").read_text(encoding="utf-8")),
            completed.returncode,
        )

    def unmatched(self, metrics: dict) -> dict:
        entries = metrics["unmatched_findings"]
        self.assertEqual(len(entries), 1, entries)
        return entries[0]

    def test_the_default_for_an_unmatched_finding_is_still_unadjudicated(self) -> None:
        """The half of PLAN's case the correction did NOT change, re-pinned at the CLI
        boundary so the iteration-1 claim keeps a live test behind it."""
        metrics, returncode = self.score(None)

        entry = self.unmatched(metrics)
        self.assertEqual(entry["reason"], "no_key_match")
        self.assertEqual(entry["classification"], "UNADJUDICATED")
        self.assertEqual(metrics["attested_false_positives"], 0)
        self.assertIsNone(metrics["precision"])
        self.assertIsNone(metrics["false_positive_rate"])
        self.assertEqual(metrics["precision_status"], "REFUSED")
        self.assertEqual(returncode, 3)

    def test_the_same_findings_become_an_attested_false_positive_under_attestation(
        self,
    ) -> None:
        """Only the adjudication input differs, and it is an explicit signed claim --
        so the reclassification is attested, never inferred."""
        metrics, returncode = self.score(eval_fixtures.attestation())

        entry = self.unmatched(metrics)
        self.assertEqual(entry["reason"], "no_key_match")
        self.assertEqual(entry["classification"], "ATTESTED_FALSE_POSITIVE")
        self.assertEqual(metrics["attested_false_positives"], 1)
        self.assertEqual(metrics["adjudicated_false_positives"], 0)
        self.assertEqual(metrics["unadjudicated_count"], 0)
        self.assertEqual(metrics["adjudication_status"], "complete_by_attestation")
        self.assertEqual(metrics["precision_status"], "COMPUTED")
        # The R3 regression, by value: the rate is the finding's share, NOT 0.0.
        self.assertNotEqual(metrics["false_positive_rate"], 0.0)
        self.assertAlmostEqual(
            metrics["false_positive_rate"], 1 / metrics["findings_total"]
        )
        self.assertAlmostEqual(
            metrics["precision"] + metrics["false_positive_rate"], 1.0
        )
        self.assertEqual(returncode, 0)

    def test_the_two_metrics_are_one_decision_on_both_inputs(self) -> None:
        for adjudications in (None, eval_fixtures.attestation()):
            with self.subTest(closed_world=adjudications is not None):
                metrics, _returncode = self.score(adjudications)

                self.assertEqual(
                    metrics["precision_status"], metrics["false_positive_rate_status"]
                )
                if metrics["precision_status"] == "COMPUTED":
                    self.assertEqual(metrics["unadjudicated_count"], 0)

    def test_no_route_but_the_closed_world_branch_reaches_the_attested_class(
        self,
    ) -> None:
        """"Never auto-FP" as a source-level property, which no per-call test gives:
        the class is assigned in exactly one function, under a closed_world guard."""
        source = (REPO_ROOT / "scripts" / "final_review_eval.py").read_text(
            encoding="utf-8"
        )
        tree = ast.parse(source)

        def assigns_the_class(node: ast.AST) -> bool:
            return any(
                isinstance(inner, ast.Name) and inner.id == "ATTESTED_FALSE_POSITIVE"
                for statement in ast.walk(node)
                if isinstance(statement, (ast.Assign, ast.AnnAssign))
                for inner in ast.walk(statement.value)
                if statement.value is not None
            )

        assigning = [
            node.name
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and assigns_the_class(node)
        ]
        self.assertEqual(assigning, ["classify_unmatched"])

        classifier = next(
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == "classify_unmatched"
        )
        guards = [
            branch
            for branch in ast.walk(classifier)
            if isinstance(branch, ast.If)
            and assigns_the_class(branch)
            and any(
                isinstance(name, ast.Name) and name.id == "closed_world"
                for name in ast.walk(branch.test)
            )
        ]
        self.assertTrue(
            guards, "the attested class is assigned outside any closed_world guard"
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
