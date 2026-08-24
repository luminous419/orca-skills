#!/usr/bin/env python3
"""Tests for scripts/run_logging.py: the ORCHESTRATOR_LOG.md/TIMING_LOG.md writer."""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from scripts import run_logging
from scripts.run_logging import (
    ORCHESTRATOR_LOG_COLUMNS,
    ORCHESTRATOR_LOG_FILENAME,
    RUN_STATUS_VALUES,
    TIMING_LOG_COLUMNS,
    TIMING_LOG_FILENAME,
    RunLoggingError,
    _ensure_run_artifact_root,
    elapsed_seconds,
    log_orchestrator_event,
    log_run_status,
    log_timing_event,
    main as cli_main,
    orchestrator_log_path,
    timing_log_path,
)
from scripts.task_context import TaskContextError, ensure_run_artifact_root

REPO_ROOT = Path(__file__).resolve().parents[1]


class TableWritingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary_directory.name)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_first_event_writes_a_header_and_a_divider(self) -> None:
        log_orchestrator_event(
            "run_1", base=self.base, event="run_start", detail="objective"
        )
        lines = orchestrator_log_path("run_1", base=self.base).read_text(
            encoding="utf-8"
        ).splitlines()
        self.assertEqual(lines[0], "| " + " | ".join(ORCHESTRATOR_LOG_COLUMNS) + " |")
        self.assertEqual(
            lines[1], "| " + " | ".join("---" for _ in ORCHESTRATOR_LOG_COLUMNS) + " |"
        )
        self.assertEqual(len(lines), 3)

    def test_a_second_event_appends_exactly_one_row_no_second_header(self) -> None:
        log_orchestrator_event("run_1", base=self.base, event="run_start")
        log_orchestrator_event("run_1", base=self.base, event="dispatch_settled")
        lines = orchestrator_log_path("run_1", base=self.base).read_text(
            encoding="utf-8"
        ).splitlines()
        self.assertEqual(len(lines), 4)
        self.assertEqual(lines.count("| " + " | ".join(ORCHESTRATOR_LOG_COLUMNS) + " |"), 1)

    def test_timing_log_gets_its_own_header_and_rows(self) -> None:
        log_timing_event("run_1", base=self.base, event="run_start")
        log_timing_event("run_1", base=self.base, event="run_end")
        lines = timing_log_path("run_1", base=self.base).read_text(
            encoding="utf-8"
        ).splitlines()
        self.assertEqual(lines[0], "| " + " | ".join(TIMING_LOG_COLUMNS) + " |")
        self.assertEqual(len(lines), 4)

    def test_timing_event_derives_duration_when_only_timestamps_are_given(
        self,
    ) -> None:
        """PR #15 second review round MINOR: the live-Coordinator CLI path only ever
        has timestamps to give timing-event, not a pre-computed duration -- so the
        writer itself must derive duration_s, or the CLI path's rows go blank while
        OrcaRuntimeHarness's own rows (which always computed it) stay populated.
        """
        log_timing_event(
            "run_1",
            base=self.base,
            event="dispatch_settled",
            started_at="2026-01-01T00:00:00+00:00",
            ended_at="2026-01-01T00:00:05+00:00",
        )
        text = timing_log_path("run_1", base=self.base).read_text(encoding="utf-8")
        self.assertIn("5.000", text)

    def test_an_explicit_duration_of_zero_is_not_overridden(self) -> None:
        log_timing_event(
            "run_1",
            base=self.base,
            event="dispatch_settled",
            started_at="2026-01-01T00:00:00+00:00",
            ended_at="2026-01-01T00:05:00+00:00",
            duration_seconds=0.0,
        )
        text = timing_log_path("run_1", base=self.base).read_text(encoding="utf-8")
        self.assertIn("0.000", text)
        self.assertNotIn("300.000", text)

    def test_a_missing_timestamp_leaves_duration_blank_not_guessed(self) -> None:
        log_timing_event(
            "run_1",
            base=self.base,
            event="dispatch_settled",
            started_at="2026-01-01T00:00:00+00:00",
        )
        lines = timing_log_path("run_1", base=self.base).read_text(
            encoding="utf-8"
        ).splitlines()
        last_row = [cell.strip() for cell in lines[-1].strip("|").split("|")]
        duration_index = TIMING_LOG_COLUMNS.index("duration_s")
        self.assertEqual(last_row[duration_index], "")

    def test_the_two_logs_are_independent_files_under_the_same_run_root(self) -> None:
        log_orchestrator_event("run_1", base=self.base, event="run_start")
        log_timing_event("run_1", base=self.base, event="run_start")
        run_root = self.base / "artifacts" / "runs" / "run_1"
        self.assertEqual(
            orchestrator_log_path("run_1", base=self.base),
            run_root / ORCHESTRATOR_LOG_FILENAME,
        )
        self.assertEqual(
            timing_log_path("run_1", base=self.base), run_root / TIMING_LOG_FILENAME
        )
        self.assertTrue((run_root / ORCHESTRATOR_LOG_FILENAME).is_file())
        self.assertTrue((run_root / TIMING_LOG_FILENAME).is_file())

    def test_a_pipe_or_newline_in_a_field_does_not_break_the_table(self) -> None:
        log_orchestrator_event(
            "run_1",
            base=self.base,
            event="dispatch_settled",
            detail="line one\nline two | with a pipe",
        )
        lines = orchestrator_log_path("run_1", base=self.base).read_text(
            encoding="utf-8"
        ).splitlines()
        # Still exactly one row: a raw "\n" in the source value would otherwise
        # split into two lines and desynchronize every later row from the header.
        self.assertEqual(len(lines), 3)
        self.assertIn("line one line two \\| with a pipe", lines[-1])


class CrossRunIsolationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary_directory.name)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_two_runs_get_different_paths(self) -> None:
        path_a = orchestrator_log_path("run_a", base=self.base)
        path_b = orchestrator_log_path("run_b", base=self.base)
        self.assertNotEqual(path_a, path_b)

    def test_events_for_one_run_never_reach_the_others_file(self) -> None:
        log_orchestrator_event(
            "run_a", base=self.base, event="run_start", detail="run A objective"
        )
        log_orchestrator_event(
            "run_b", base=self.base, event="run_start", detail="run B objective"
        )
        text_a = orchestrator_log_path("run_a", base=self.base).read_text(
            encoding="utf-8"
        )
        text_b = orchestrator_log_path("run_b", base=self.base).read_text(
            encoding="utf-8"
        )
        self.assertIn("run A objective", text_a)
        self.assertNotIn("run B objective", text_a)
        self.assertIn("run B objective", text_b)
        self.assertNotIn("run A objective", text_b)


class ElapsedSecondsTests(unittest.TestCase):
    def test_a_five_second_gap_is_five_point_zero(self) -> None:
        self.assertEqual(
            elapsed_seconds(
                "2026-01-01T00:00:00+00:00", "2026-01-01T00:00:05+00:00"
            ),
            5.0,
        )

    def test_a_missing_side_returns_an_empty_string_not_an_error(self) -> None:
        self.assertEqual(elapsed_seconds("", "2026-01-01T00:00:05+00:00"), "")
        self.assertEqual(elapsed_seconds("2026-01-01T00:00:00+00:00", ""), "")

    def test_a_malformed_timestamp_returns_an_empty_string_not_a_raise(self) -> None:
        self.assertEqual(elapsed_seconds("not-a-timestamp", "also-not-one"), "")


class RunStatusTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary_directory.name)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_all_four_documented_statuses_are_accepted(self) -> None:
        for index, status in enumerate(RUN_STATUS_VALUES):
            with self.subTest(status):
                log_run_status(f"run_{index}", status, base=self.base)

    def test_an_unrecognized_status_is_refused_not_written(self) -> None:
        with self.assertRaisesRegex(RunLoggingError, "unknown run status"):
            log_run_status("run_x", "COMPLETE", base=self.base)
        # Fail-closed like the rest of this repository's validation: an invalid
        # call must not silently create a run directory with a half-written log.
        self.assertFalse(
            (self.base / "artifacts" / "runs" / "run_x").exists()
        )

    def test_run_status_writes_a_row_to_both_logs_with_a_computed_duration(
        self,
    ) -> None:
        log_run_status(
            "run_y",
            "COMPLETED",
            base=self.base,
            reason="all phases passed",
            run_started_at="2026-01-01T00:00:00+00:00",
        )
        orchestrator_text = orchestrator_log_path("run_y", base=self.base).read_text(
            encoding="utf-8"
        )
        timing_text = timing_log_path("run_y", base=self.base).read_text(
            encoding="utf-8"
        )
        self.assertIn("run_end", orchestrator_text)
        self.assertIn("COMPLETED", orchestrator_text)
        self.assertIn("all phases passed", orchestrator_text)
        self.assertIn("run_end", timing_text)
        self.assertIn("2026-01-01T00:00:00+00:00", timing_text)


class CliTests(unittest.TestCase):
    """The Bash-facing entry point a live Coordinator uses instead of Python.

    OrcaRuntimeHarness never goes through this CLI -- it calls the functions
    above directly. This is the path SKILL.md's Coordinator procedure (a
    person or agent running real `orca` commands, not this repository's own
    Python) uses to write the same two files.
    """

    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary_directory.name)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_orchestrator_event_subcommand_appends_a_row(self) -> None:
        with redirect_stdout(StringIO()):
            exit_code = cli_main(
                [
                    "orchestrator-event",
                    "--run-id",
                    "run_cli",
                    "--base",
                    str(self.base),
                    "--event",
                    "run_start",
                    "--detail",
                    "objective text",
                ]
            )
        self.assertEqual(exit_code, 0)
        text = orchestrator_log_path("run_cli", base=self.base).read_text(
            encoding="utf-8"
        )
        self.assertIn("run_start", text)
        self.assertIn("objective text", text)

    def test_timing_event_subcommand_appends_a_row(self) -> None:
        with redirect_stdout(StringIO()):
            exit_code = cli_main(
                [
                    "timing-event",
                    "--run-id",
                    "run_cli",
                    "--base",
                    str(self.base),
                    "--event",
                    "dispatch",
                    "--started-at",
                    "2026-01-01T00:00:00+00:00",
                    "--ended-at",
                    "2026-01-01T00:00:05+00:00",
                    "--duration-seconds",
                    "5.0",
                ]
            )
        self.assertEqual(exit_code, 0)
        text = timing_log_path("run_cli", base=self.base).read_text(encoding="utf-8")
        self.assertIn("dispatch", text)
        self.assertIn("5.0", text)

    def test_timing_event_subcommand_derives_duration_without_the_flag(self) -> None:
        """PR #15 second review round MINOR: SKILL.md's own phase/iteration/
        dispatch_settled timing-event examples never pass --duration-seconds --
        only timestamps. This is the CLI path that has to fill it in on its own.
        """
        with redirect_stdout(StringIO()):
            exit_code = cli_main(
                [
                    "timing-event",
                    "--run-id",
                    "run_cli",
                    "--base",
                    str(self.base),
                    "--event",
                    "dispatch_settled",
                    "--started-at",
                    "2026-01-01T00:00:00+00:00",
                    "--ended-at",
                    "2026-01-01T00:00:05+00:00",
                ]
            )
        self.assertEqual(exit_code, 0)
        text = timing_log_path("run_cli", base=self.base).read_text(encoding="utf-8")
        self.assertIn("5.000", text)

    def test_orchestrator_event_subcommand_accepts_a_gate_result_and_review_verdict(
        self,
    ) -> None:
        with redirect_stdout(StringIO()):
            exit_code = cli_main(
                [
                    "orchestrator-event",
                    "--run-id",
                    "run_cli",
                    "--base",
                    str(self.base),
                    "--event",
                    "dispatch_settled",
                    "--role",
                    "reviewer",
                    "--gate-result",
                    "FAIL",
                    "--review-verdict",
                    "BLOCKED",
                ]
            )
        self.assertEqual(exit_code, 0)
        text = orchestrator_log_path("run_cli", base=self.base).read_text(
            encoding="utf-8"
        )
        self.assertIn("BLOCKED", text)
        self.assertIn("FAIL", text)

    def test_run_status_subcommand_writes_both_logs(self) -> None:
        with redirect_stdout(StringIO()):
            exit_code = cli_main(
                [
                    "run-status",
                    "--run-id",
                    "run_cli",
                    "--base",
                    str(self.base),
                    "--status",
                    "ESCALATED",
                    "--reason",
                    "max-iterations exhausted",
                ]
            )
        self.assertEqual(exit_code, 0)
        orchestrator_text = orchestrator_log_path("run_cli", base=self.base).read_text(
            encoding="utf-8"
        )
        timing_text = timing_log_path("run_cli", base=self.base).read_text(
            encoding="utf-8"
        )
        self.assertIn("ESCALATED", orchestrator_text)
        self.assertIn("max-iterations exhausted", orchestrator_text)
        self.assertIn("run_end", timing_text)

    def test_run_status_subcommand_rejects_an_unknown_status_at_the_cli(self) -> None:
        with redirect_stdout(StringIO()), self.assertRaises(SystemExit):
            cli_main(
                [
                    "run-status",
                    "--run-id",
                    "run_cli",
                    "--base",
                    str(self.base),
                    "--status",
                    "NOT_A_REAL_STATUS",
                ]
            )


class ArtifactRootParityTests(unittest.TestCase):
    """OS-17 review round 3 MAJOR-1: run_logging.py's `_ensure_run_artifact_root`
    is a deliberate, self-contained duplicate of
    scripts.task_context.run_artifact_root()/ensure_run_artifact_root() -- see the
    module docstring for why run_logging.py may not import task_context. The two
    must keep behaving identically or a Coordinator's real logging path
    (run_logging.py) could accept/reject a run_id differently than the rest of
    this repository's artifact-path contract does.
    """

    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary_directory.name)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_a_valid_run_id_produces_the_same_path_from_both_implementations(
        self,
    ) -> None:
        inlined = _ensure_run_artifact_root("run_parity", base=self.base)
        canonical = ensure_run_artifact_root("run_parity", base=self.base)
        self.assertEqual(inlined, canonical)
        self.assertTrue(inlined.is_dir())

    def test_both_implementations_reject_the_same_invalid_run_ids(self) -> None:
        for bad_run_id in ("", "..", ".", "a/b", "a\\b"):
            with self.subTest(bad_run_id):
                with self.assertRaises(RunLoggingError):
                    _ensure_run_artifact_root(bad_run_id, base=self.base)
                with self.assertRaises(TaskContextError):
                    ensure_run_artifact_root(bad_run_id, base=self.base)

    def test_omitting_base_resolves_against_the_current_directory_in_both(
        self,
    ) -> None:
        # Same default in both: neither implementation resolves relative to its
        # own __file__ location -- always the caller's cwd (or `base` override).
        inlined = _ensure_run_artifact_root("run_parity_cwd")
        canonical = ensure_run_artifact_root("run_parity_cwd")
        try:
            self.assertEqual(inlined, canonical)
            self.assertEqual(inlined, Path("artifacts") / "runs" / "run_parity_cwd")
        finally:
            shutil.rmtree(Path("artifacts") / "runs" / "run_parity_cwd", ignore_errors=True)


class InstalledSkillPortabilityTests(unittest.TestCase):
    """OS-17 review round 3 MAJOR-1: INSTALL.md's documented global install
    (`cp -R orca-worker-reviewer-orchestration ~/.claude/skills/`) copies only
    what lives inside that Skill directory -- SKILL.md, templates/, reviews/, and
    now tools/ -- never this repository's scripts/. A live Coordinator invoking
    `python3 scripts/run_logging.py` from a target project that is not this
    repository's own checkout would find no such file. This proves the packaged
    copy at orca-worker-reviewer-orchestration/tools/run_logging.py works
    completely standalone: installed into an isolated skills directory exactly
    the way INSTALL.md documents, then invoked as a real subprocess with cwd set
    to an unrelated target project directory -- this repository's checkout is
    nowhere on that subprocess's sys.path.
    """

    def test_the_installed_tools_copy_writes_logs_under_the_target_project(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as skills_home, tempfile.TemporaryDirectory() as target_project:
            # Exactly INSTALL.md section 4's documented command shape.
            shutil.copytree(
                REPO_ROOT / "orca-worker-reviewer-orchestration",
                Path(skills_home) / "orca-worker-reviewer-orchestration",
            )
            installed_tool = (
                Path(skills_home)
                / "orca-worker-reviewer-orchestration"
                / "tools"
                / "run_logging.py"
            )
            self.assertTrue(installed_tool.is_file())

            result = subprocess.run(
                [
                    sys.executable,
                    str(installed_tool),
                    "orchestrator-event",
                    "--run-id",
                    "run_installed",
                    "--event",
                    "run_start",
                    "--detail",
                    "objective text",
                ],
                cwd=target_project,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            written = (
                Path(target_project)
                / "artifacts"
                / "runs"
                / "run_installed"
                / ORCHESTRATOR_LOG_FILENAME
            )
            self.assertTrue(written.is_file())
            self.assertIn("objective text", written.read_text(encoding="utf-8"))
            # The repository checkout must not have been needed for this to work.
            self.assertFalse(
                (REPO_ROOT / "artifacts" / "runs" / "run_installed").exists()
            )


class RiskLoggingTests(unittest.TestCase):
    """OS-3 (T-19): the four new ORCHESTRATOR_LOG columns and the TIMING_LOG one."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.addCleanup(self.temporary.cleanup)

    def rows(self, path: Path) -> list[list[str]]:
        lines = path.read_text(encoding="utf-8").splitlines()
        return [[cell.strip() for cell in line.strip("|").split("|")] for line in lines]

    def test_the_documented_columns_are_present_in_order(self) -> None:
        self.assertEqual(
            run_logging.ORCHESTRATOR_LOG_COLUMNS[12:16],
            ("risk", "risk_source", "requested_phases", "round_kind"),
        )
        self.assertIn("risk", run_logging.TIMING_LOG_COLUMNS)

    def test_run_start_carries_risk_source_and_requested_phases(self) -> None:
        path = run_logging.log_orchestrator_event(
            "run_r",
            base=self.base,
            event="run_start",
            risk="low",
            risk_source="explicit",
            requested_phases="analysis,plan",
            detail="objective",
        )
        header, _divider, row = self.rows(path)
        record = dict(zip(header, row))
        self.assertEqual(record["risk"], "low")
        self.assertEqual(record["risk_source"], "explicit")
        self.assertEqual(record["requested_phases"], "analysis,plan")

    def test_dispatch_settled_carries_risk_and_round_kind(self) -> None:
        path = run_logging.log_orchestrator_event(
            "run_r",
            base=self.base,
            event="dispatch_settled",
            phase="implementation",
            role="reviewer",
            risk="high",
            round_kind="downstream_revalidation",
            gate_result="PASS",
        )
        record = dict(zip(*[self.rows(path)[0], self.rows(path)[-1]]))
        self.assertEqual(record["risk"], "high")
        self.assertEqual(record["round_kind"], "downstream_revalidation")

    def test_a_skipped_reviewer_gate_is_a_positive_row(self) -> None:
        """The absence of a reviewer row must never be the only evidence."""
        path = run_logging.log_orchestrator_event(
            "run_r",
            base=self.base,
            event="reviewer_gate_skipped",
            phase="implementation",
            risk="low",
            detail="risk=low: no phase Reviewer gate for this phase",
        )
        record = dict(zip(*[self.rows(path)[0], self.rows(path)[-1]]))
        self.assertEqual(record["event"], "reviewer_gate_skipped")
        self.assertEqual(record["risk"], "low")

    def test_timing_rows_carry_the_risk_a_duration_was_produced_under(self) -> None:
        path = run_logging.log_timing_event(
            "run_r",
            base=self.base,
            event="dispatch_settled",
            phase="implementation",
            role="worker",
            started_at="2026-01-01T00:00:00+00:00",
            ended_at="2026-01-01T00:00:05+00:00",
            risk="medium",
        )
        record = dict(zip(*[self.rows(path)[0], self.rows(path)[-1]]))
        self.assertEqual(record["risk"], "medium")
        self.assertEqual(record["duration_s"], "5.000")

    def test_run_status_carries_the_pair_on_both_logs(self) -> None:
        run_logging.log_run_status(
            "run_r", "COMPLETED", base=self.base, risk="low", risk_source="explicit"
        )
        orchestrator = self.rows(
            run_logging.orchestrator_log_path("run_r", base=self.base)
        )
        record = dict(zip(orchestrator[0], orchestrator[-1]))
        self.assertEqual((record["risk"], record["risk_source"]), ("low", "explicit"))
        timing = self.rows(run_logging.timing_log_path("run_r", base=self.base))
        self.assertEqual(dict(zip(timing[0], timing[-1]))["risk"], "low")

    def test_every_new_parameter_defaults_to_blank(self) -> None:
        """The compatibility guarantee: an existing call site writes blank cells,
        never a guessed value."""
        path = run_logging.log_orchestrator_event(
            "run_r", base=self.base, event="run_start", detail="objective"
        )
        record = dict(zip(*[self.rows(path)[0], self.rows(path)[-1]]))
        for column in ("risk", "risk_source", "requested_phases", "round_kind"):
            self.assertEqual(record[column], "")


class TimingCorrectnessRegressionTests(unittest.TestCase):
    """OS-19: TIMING_LOG.md must never record a negative duration or an
    out-of-order timestamp pair.

    Every case here is taken from the real TIMING_LOG.md that PR #16's OS-3 run
    (`run_e0cdf1afae58`) actually produced. That log has four `dispatch_settled`
    rows whose `duration_s` is negative -- IMPLEMENTATION reviewer iteration 2
    at -423s, TEST reviewer iteration 3 at -2267s, DESIGN reviewer iteration 7
    at -1296s, IMPLEMENTATION reviewer iteration 3 at -1998s -- plus a fifth,
    TEST reviewer iteration 4 at -2766s. Every one of them is a row whose
    `started_at` was chained off the PREVIOUS row's `ended_at` while its own
    `ended_at` came from real wall clock, which is what the fix has to make
    structurally impossible rather than merely detectable.
    """

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.addCleanup(self.temporary.cleanup)

    def rows(self, run_id: str = "run_os19") -> list[dict[str, str]]:
        lines = timing_log_path(run_id, base=self.base).read_text(
            encoding="utf-8"
        ).splitlines()
        columns = [cell.strip() for cell in lines[0].strip("|").split("|")]
        return [
            dict(zip(columns, (cell.strip() for cell in line.strip("|").split("|"))))
            for line in lines[2:]
        ]

    def assert_log_invariants(self, run_id: str = "run_os19") -> None:
        """`started_at <= ended_at` and `duration_s >= 0` on every row that has them."""
        for index, row in enumerate(self.rows(run_id)):
            with self.subTest(row=index, event=row["event"]):
                if row["duration_s"]:
                    self.assertGreaterEqual(
                        float(row["duration_s"]),
                        0.0,
                        f"negative duration_s in {row}",
                    )
                if row["started_at"] and row["ended_at"]:
                    self.assertLessEqual(
                        row["started_at"],
                        row["ended_at"],
                        f"started_at is after ended_at in {row}",
                    )

    # ---- A. The observed rows, replayed exactly -------------------------------

    def test_the_four_observed_negative_rows_are_never_written_as_negative(self) -> None:
        observed = (
            ("IMPLEMENTATION", 2, "2026-08-24T01:48:15Z", "2026-08-24T01:41:12Z"),
            ("TEST", 3, "2026-08-24T02:50:15Z", "2026-08-24T02:12:28Z"),
            ("DESIGN", 7, "2026-08-24T03:15:15Z", "2026-08-24T02:53:39Z"),
            ("IMPLEMENTATION", 3, "2026-08-24T03:36:15Z", "2026-08-24T03:02:57Z"),
            ("TEST", 4, "2026-08-24T03:58:15Z", "2026-08-24T03:12:09Z"),
        )
        for phase, iteration, started_at, ended_at in observed:
            log_timing_event(
                "run_os19",
                base=self.base,
                event="dispatch_settled",
                phase=phase,
                role="reviewer",
                iteration=iteration,
                started_at=started_at,
                ended_at=ended_at,
            )
        written = self.rows()
        self.assertEqual(len(written), len(observed))
        for row in written:
            # Not clamped to 0 and not absolute-valued: an out-of-order pair has
            # no knowable duration, so the cell stays empty and says why.
            self.assertEqual(row["duration_s"], "")
            self.assertIn(run_logging.TIMING_INVALID_ORDER, row["detail"])
        self.assert_log_invariants()

    def test_elapsed_seconds_refuses_to_return_a_negative_number(self) -> None:
        self.assertEqual(
            elapsed_seconds("2026-08-24T01:48:15Z", "2026-08-24T01:41:12Z"), ""
        )
        self.assertEqual(
            run_logging.resolve_duration(
                "2026-08-24T01:48:15Z", "2026-08-24T01:41:12Z"
            ),
            ("", run_logging.TIMING_INVALID_ORDER),
        )

    def test_an_explicitly_supplied_negative_duration_is_refused_too(self) -> None:
        """The observed rows could equally have arrived as a pre-computed
        `--duration-seconds -423`; a fail-safe that only covers the derived path
        would still let that reach the file.
        """
        log_timing_event(
            "run_os19",
            base=self.base,
            event="dispatch_settled",
            phase="IMPLEMENTATION",
            role="reviewer",
            iteration=2,
            duration_seconds=-423.0,
        )
        row = self.rows()[-1]
        self.assertEqual(row["duration_s"], "")
        self.assertIn(run_logging.TIMING_INVALID_DURATION, row["detail"])

    def test_a_mixed_awareness_pair_is_fail_safe_not_a_raise(self) -> None:
        """`datetime.fromisoformat` parses both sides, but subtracting an aware
        from a naive one raises TypeError -- which the pre-OS-19 code did not
        catch, so it escaped `elapsed_seconds()` into the caller's lifecycle path
        rather than staying inside logging.
        """
        self.assertEqual(
            elapsed_seconds("2026-08-24T01:00:00", "2026-08-24T01:05:00+00:00"), ""
        )
        log_timing_event(
            "run_os19",
            base=self.base,
            event="dispatch_settled",
            started_at="2026-08-24T01:00:00",
            ended_at="2026-08-24T01:05:00+00:00",
        )
        row = self.rows()[-1]
        self.assertEqual(row["duration_s"], "")
        self.assertIn(run_logging.TIMING_INVALID_TIMESTAMP, row["detail"])

    def test_a_malformed_timestamp_says_so_instead_of_going_quiet(self) -> None:
        log_timing_event(
            "run_os19",
            base=self.base,
            event="dispatch_settled",
            started_at="not-a-timestamp",
            ended_at="2026-08-24T01:05:00+00:00",
        )
        row = self.rows()[-1]
        self.assertEqual(row["duration_s"], "")
        self.assertIn(run_logging.TIMING_INVALID_TIMESTAMP, row["detail"])

    def test_a_fail_safe_marker_never_destroys_the_callers_own_detail(self) -> None:
        log_timing_event(
            "run_os19",
            base=self.base,
            event="dispatch_settled",
            started_at="2026-08-24T01:48:15Z",
            ended_at="2026-08-24T01:41:12Z",
            detail="task=task_x dispatch=ctx_x",
        )
        row = self.rows()[-1]
        self.assertIn("task=task_x dispatch=ctx_x", row["detail"])
        self.assertIn(run_logging.TIMING_INVALID_ORDER, row["detail"])


class AuthoritativeDispatchClockTests(unittest.TestCase):
    """OS-19: the CLI path must capture dispatch start/settlement times itself.

    The negative rows in the real OS-3 log are not a formatting slip -- they are
    what happens when the only clock is a Coordinator's recollection. SKILL.md
    section 9 asked it for `--started-at <dispatch 직전 시각>`, it had no clock,
    and so every `started_at` was reconstructed from the previous row's
    (itself estimated, often future-dated) `ended_at`. `timing-dispatch-start`
    is the authoritative source: the same `now_iso()` the Python harness uses,
    captured at the same point in the dispatch lifecycle.
    """

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.addCleanup(self.temporary.cleanup)

    def cli(self, *args: str) -> str:
        stream = StringIO()
        with redirect_stdout(stream):
            exit_code = cli_main([*args, "--base", str(self.base)])
        self.assertEqual(exit_code, 0)
        return stream.getvalue().strip()

    def rows(self, run_id: str = "run_clock") -> list[dict[str, str]]:
        lines = timing_log_path(run_id, base=self.base).read_text(
            encoding="utf-8"
        ).splitlines()
        columns = [cell.strip() for cell in lines[0].strip("|").split("|")]
        return [
            dict(zip(columns, (cell.strip() for cell in line.strip("|").split("|"))))
            for line in lines[2:]
        ]

    def dispatch(self, phase: str, role: str, iteration: int) -> None:
        """One full Coordinator-side dispatch: mark the start, then settle it.

        Note what is NOT passed: no `--started-at`, no `--ended-at`. That is the
        whole point -- the Coordinator has no clock to be wrong with.
        """
        self.cli(
            "timing-dispatch-start",
            "--run-id",
            "run_clock",
            "--phase",
            phase,
            "--role",
            role,
            "--iteration",
            str(iteration),
        )
        self.cli(
            "timing-event",
            "--run-id",
            "run_clock",
            "--event",
            "dispatch_settled",
            "--phase",
            phase,
            "--role",
            role,
            "--iteration",
            str(iteration),
        )

    def test_a_dispatch_settles_with_the_start_the_cli_itself_captured(self) -> None:
        captured = self.cli(
            "timing-dispatch-start",
            "--run-id",
            "run_clock",
            "--phase",
            "IMPLEMENTATION",
            "--role",
            "reviewer",
            "--iteration",
            "2",
        )
        self.cli(
            "timing-event",
            "--run-id",
            "run_clock",
            "--event",
            "dispatch_settled",
            "--phase",
            "IMPLEMENTATION",
            "--role",
            "reviewer",
            "--iteration",
            "2",
        )
        settled = [row for row in self.rows() if row["event"] == "dispatch_settled"][-1]
        self.assertEqual(settled["started_at"], captured)
        self.assertTrue(settled["ended_at"])
        self.assertGreaterEqual(float(settled["duration_s"]), 0.0)

    def test_the_os3_correction_loop_shape_produces_no_negative_duration(self) -> None:
        """The exact sequence that produced -423s: phase gate iteration 1
        (worker, reviewer FAIL), correction iteration 2 (worker, reviewer PASS).
        """
        self.dispatch("IMPLEMENTATION", "worker", 1)
        self.dispatch("IMPLEMENTATION", "reviewer", 1)
        self.cli(
            "timing-event",
            "--run-id",
            "run_clock",
            "--event",
            "iteration_end",
            "--phase",
            "IMPLEMENTATION",
            "--iteration",
            "1",
            "--detail",
            "FAIL",
        )
        self.dispatch("IMPLEMENTATION", "worker", 2)
        self.dispatch("IMPLEMENTATION", "reviewer", 2)
        self.cli(
            "timing-event",
            "--run-id",
            "run_clock",
            "--event",
            "iteration_end",
            "--phase",
            "IMPLEMENTATION",
            "--iteration",
            "2",
            "--detail",
            "PASS",
        )
        for row in self.rows():
            if row["duration_s"]:
                self.assertGreaterEqual(float(row["duration_s"]), 0.0, row)
            if row["started_at"] and row["ended_at"]:
                self.assertLessEqual(row["started_at"], row["ended_at"], row)

    def test_boundaries_bracket_the_dispatches_of_their_own_scope(self) -> None:
        self.dispatch("IMPLEMENTATION", "worker", 1)
        self.dispatch("IMPLEMENTATION", "reviewer", 1)
        self.dispatch("IMPLEMENTATION", "worker", 2)
        self.dispatch("IMPLEMENTATION", "reviewer", 2)
        self.cli(
            "timing-event",
            "--run-id",
            "run_clock",
            "--event",
            "phase_end",
            "--phase",
            "IMPLEMENTATION",
            "--detail",
            "PASS",
        )
        rows = self.rows()
        by_iteration: dict[str, list[dict[str, str]]] = {}
        for row in rows:
            if row["event"] == "dispatch_settled":
                by_iteration.setdefault(row["iteration"], []).append(row)
        for iteration, dispatches in by_iteration.items():
            start = next(
                row
                for row in rows
                if row["event"] == "iteration_start" and row["iteration"] == iteration
            )
            end = next(
                row
                for row in rows
                if row["event"] == "iteration_end" and row["iteration"] == iteration
            )
            self.assertTrue(start["started_at"])
            # The boundary is not merely present: it has a real duration, which
            # every iteration_end/phase_end row in the real OS-3 log lacked.
            self.assertTrue(end["duration_s"], f"iteration {iteration} end has no duration")
            for dispatch in dispatches:
                self.assertLessEqual(start["started_at"], dispatch["started_at"])
                self.assertLessEqual(dispatch["ended_at"], end["ended_at"])

    def test_the_next_iterations_time_never_lands_in_the_previous_ones(self) -> None:
        self.dispatch("IMPLEMENTATION", "worker", 1)
        self.dispatch("IMPLEMENTATION", "reviewer", 1)
        boundary_ended_at = [
            row for row in self.rows() if row["event"] == "dispatch_settled"
        ][-1]["ended_at"]
        self.dispatch("IMPLEMENTATION", "worker", 2)
        rows = self.rows()
        iteration_end = next(
            row
            for row in rows
            if row["event"] == "iteration_end" and row["iteration"] == "1"
        )
        # Closed at iteration 1's own last activity, not at "whenever iteration
        # 2's dispatch happened to settle".
        self.assertEqual(iteration_end["ended_at"], boundary_ended_at)
        iteration_2_start = next(
            row
            for row in rows
            if row["event"] == "iteration_start" and row["iteration"] == "2"
        )
        self.assertLessEqual(iteration_end["ended_at"], iteration_2_start["started_at"])

    def test_a_phase_transition_excludes_the_next_phases_time(self) -> None:
        self.dispatch("IMPLEMENTATION", "worker", 1)
        implementation_last_end = [
            row for row in self.rows() if row["event"] == "dispatch_settled"
        ][-1]["ended_at"]
        self.dispatch("TEST", "worker", 1)
        rows = self.rows()
        phase_end = next(
            row
            for row in rows
            if row["event"] == "phase_end" and row["phase"] == "IMPLEMENTATION"
        )
        self.assertEqual(phase_end["ended_at"], implementation_last_end)
        self.assertTrue(phase_end["duration_s"])
        self.assertGreaterEqual(float(phase_end["duration_s"]), 0.0)

    def test_final_review_timing_obeys_the_same_invariants(self) -> None:
        """Section 17's Final Adversarial Review is just another scope with its
        own iteration numbers -- including the re-opened upstream phases each
        failed attempt routes back into, which is where -1296s (DESIGN
        iteration 7, opened by a Final Review FAIL) actually came from.
        """
        self.dispatch("TEST", "worker", 1)
        self.dispatch("final_review", "reviewer", 1)
        self.cli(
            "timing-event",
            "--run-id",
            "run_clock",
            "--event",
            "iteration_end",
            "--phase",
            "final_review",
            "--iteration",
            "1",
            "--detail",
            "FAIL - routed to design",
        )
        self.dispatch("DESIGN", "worker", 7)
        self.dispatch("DESIGN", "reviewer", 7)
        self.dispatch("final_review", "reviewer", 2)
        rows = self.rows()
        final_rows = [row for row in rows if row["phase"] == "final_review"]
        self.assertTrue(final_rows)
        for row in rows:
            if row["duration_s"]:
                self.assertGreaterEqual(float(row["duration_s"]), 0.0, row)
            if row["started_at"] and row["ended_at"]:
                self.assertLessEqual(row["started_at"], row["ended_at"], row)
        # The re-opened DESIGN scope gets its own start, not the one it had
        # before final_review took over.
        design_starts = [
            row
            for row in rows
            if row["event"] == "iteration_start" and row["phase"] == "DESIGN"
        ]
        self.assertEqual(len(design_starts), 1)

    def test_run_status_closes_whatever_scope_is_still_open(self) -> None:
        self.cli(
            "timing-event", "--run-id", "run_clock", "--event", "run_start"
        )
        self.dispatch("IMPLEMENTATION", "worker", 1)
        self.cli(
            "run-status", "--run-id", "run_clock", "--status", "COMPLETED"
        )
        rows = self.rows()
        events = [row["event"] for row in rows]
        self.assertIn("iteration_end", events)
        self.assertIn("phase_end", events)
        self.assertIn("run_end", events)
        self.assertLess(events.index("phase_end"), events.index("run_end"))
        run_end = next(row for row in rows if row["event"] == "run_end")
        # run_start's own captured timestamp is remembered, so the Coordinator
        # does not have to hand `--run-started-at` back hours later.
        self.assertTrue(run_end["started_at"])
        self.assertGreaterEqual(float(run_end["duration_s"]), 0.0)
        for row in rows:
            if row["started_at"] and row["ended_at"]:
                self.assertLessEqual(row["started_at"], row["ended_at"], row)

    def test_a_supplied_timestamp_still_wins_but_is_still_validated(self) -> None:
        """An explicit `--started-at`/`--ended-at` remains accepted (an offline
        reconstruction is a legitimate use), but it is validated the same way,
        so the OS-3 shape cannot come back through the override.
        """
        self.cli(
            "timing-event",
            "--run-id",
            "run_clock",
            "--event",
            "dispatch_settled",
            "--phase",
            "IMPLEMENTATION",
            "--role",
            "reviewer",
            "--iteration",
            "2",
            "--started-at",
            "2026-08-24T01:48:15Z",
            "--ended-at",
            "2026-08-24T01:41:12Z",
        )
        row = self.rows()[-1]
        self.assertEqual(row["duration_s"], "")
        self.assertIn(run_logging.TIMING_INVALID_ORDER, row["detail"])


class InstalledToolsTimingParityTests(unittest.TestCase):
    """OS-19: the installed Skill CLI copy must reach the same duration and the
    same fail-safe judgement as the in-repo Python path for identical input.

    Byte-identity (RunLoggingTwinParityTests) proves the two FILES match; this
    proves the two PATHS agree behaviourally when actually executed as the
    installed Skill does -- a real subprocess, from an unrelated project
    directory, with this repository's checkout off sys.path.
    """

    CASES = (
        ("2026-08-24T01:41:30Z", "2026-08-24T02:03:00Z"),   # ordered
        ("2026-08-24T01:48:15Z", "2026-08-24T01:41:12Z"),   # the OS-3 shape
        ("not-a-timestamp", "2026-08-24T01:41:12Z"),        # malformed
        ("2026-08-24T01:00:00", "2026-08-24T01:05:00+00:00"),  # mixed awareness
        ("2026-08-24T01:41:30Z", ""),                        # missing side
    )

    @staticmethod
    def cell(path: Path, column: str) -> str:
        lines = path.read_text(encoding="utf-8").splitlines()
        columns = [text.strip() for text in lines[0].strip("|").split("|")]
        row = [text.strip() for text in lines[-1].strip("|").split("|")]
        return dict(zip(columns, row))[column]

    def test_both_paths_agree_on_duration_and_on_the_fail_safe_marker(self) -> None:
        with tempfile.TemporaryDirectory() as skills_home, tempfile.TemporaryDirectory() as target_project:
            shutil.copytree(
                REPO_ROOT / "orca-worker-reviewer-orchestration",
                Path(skills_home) / "orca-worker-reviewer-orchestration",
            )
            installed_tool = (
                Path(skills_home)
                / "orca-worker-reviewer-orchestration"
                / "tools"
                / "run_logging.py"
            )
            for index, (started_at, ended_at) in enumerate(self.CASES):
                with self.subTest(started_at=started_at, ended_at=ended_at):
                    run_id = f"run_parity_{index}"
                    result = subprocess.run(
                        [
                            sys.executable,
                            str(installed_tool),
                            "timing-event",
                            "--run-id",
                            run_id,
                            "--event",
                            "dispatch_settled",
                            "--phase",
                            "IMPLEMENTATION",
                            "--role",
                            "reviewer",
                            "--iteration",
                            "2",
                            "--started-at",
                            started_at,
                            "--ended-at",
                            ended_at,
                            "--detail",
                            "task=t dispatch=d",
                        ],
                        cwd=target_project,
                        text=True,
                        capture_output=True,
                        check=False,
                    )
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    installed_log = (
                        Path(target_project)
                        / "artifacts"
                        / "runs"
                        / run_id
                        / TIMING_LOG_FILENAME
                    )

                    python_base = Path(target_project) / "python-path"
                    log_timing_event(
                        run_id,
                        base=python_base,
                        event="dispatch_settled",
                        phase="IMPLEMENTATION",
                        role="reviewer",
                        iteration=2,
                        started_at=started_at,
                        ended_at=ended_at,
                        detail="task=t dispatch=d",
                    )
                    python_log = timing_log_path(run_id, base=python_base)

                    for column in ("started_at", "ended_at", "duration_s", "detail"):
                        self.assertEqual(
                            self.cell(installed_log, column),
                            self.cell(python_log, column),
                            f"{column} differs between the installed CLI and the "
                            "Python path",
                        )


class RunLoggingTwinParityTests(unittest.TestCase):
    """T-20: the installed Skill's copy stays byte-identical after any edit."""

    def test_the_two_copies_are_byte_identical(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        canonical = repo_root / "scripts" / "run_logging.py"
        installed = (
            repo_root
            / "orca-worker-reviewer-orchestration"
            / "tools"
            / "run_logging.py"
        )
        self.assertEqual(canonical.read_bytes(), installed.read_bytes())


if __name__ == "__main__":
    unittest.main()
