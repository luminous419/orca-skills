#!/usr/bin/env python3
"""Tests for scripts/run_logging.py: the ORCHESTRATOR_LOG.md/TIMING_LOG.md writer."""

from __future__ import annotations

import ast
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime
from io import StringIO
from pathlib import Path
from unittest.mock import patch

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

    @staticmethod
    def parsed(value: str) -> datetime | None:
        try:
            return datetime.fromisoformat(value)
        except (TypeError, ValueError):
            return None

    def assert_log_invariants(self, run_id: str = "run_os19") -> None:
        """The OS-19 invariant itself, asserted over every emitted row.

        Review round 1 BF-001: the earlier version of this helper encoded a
        WEAKER rule than the one the task asks for -- it tolerated an emitted
        `started_at > ended_at` pair as long as the row carried a marker, which
        is exactly the row shape the reviewer rejected. It now asserts the
        requirement verbatim: `started_at <= ended_at` for every populated pair,
        `duration_s >= 0` always, and no unreadable value in either timestamp
        column. Evidence for a rejected pair belongs in `detail`, which
        `assert_quarantined` checks separately -- never in the timestamp
        columns, whose values are now always trustworthy on their face.
        """
        for index, row in enumerate(self.rows(run_id)):
            with self.subTest(row=index, event=row["event"]):
                if row["duration_s"]:
                    self.assertGreaterEqual(
                        float(row["duration_s"]),
                        0.0,
                        f"negative duration_s in {row}",
                    )
                started_at = self.parsed(row["started_at"]) if row["started_at"] else None
                ended_at = self.parsed(row["ended_at"]) if row["ended_at"] else None
                if row["started_at"]:
                    self.assertIsNotNone(
                        started_at, f"unreadable started_at emitted: {row}"
                    )
                if row["ended_at"]:
                    self.assertIsNotNone(
                        ended_at, f"unreadable ended_at emitted: {row}"
                    )
                if started_at is not None and ended_at is not None:
                    self.assertLessEqual(
                        started_at,
                        ended_at,
                        f"a row was emitted with started_at > ended_at: {row}",
                    )

    def assert_quarantined(
        self, row: dict[str, str], marker: str, started_at: str, ended_at: str
    ) -> None:
        """One rejected row: no duration, no timestamps, all of the evidence."""
        self.assertEqual(row["duration_s"], "", row)
        self.assertEqual(row["started_at"], "", row)
        self.assertEqual(row["ended_at"], "", row)
        self.assertIn(marker, row["detail"])
        self.assertIn(
            f"{run_logging.TIMING_INVALID_STARTED_AT_FIELD}={started_at}",
            row["detail"],
        )
        self.assertIn(
            f"{run_logging.TIMING_INVALID_ENDED_AT_FIELD}={ended_at}",
            row["detail"],
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
        for row, (_, _, started_at, ended_at) in zip(written, observed):
            # Not clamped to 0 and not absolute-valued: an out-of-order pair has
            # no knowable duration, so the cell stays empty and says why. And
            # (review round 1 BF-001) the pair itself does not reach the
            # timestamp columns -- it survives as evidence inside `detail`.
            self.assert_quarantined(
                row, run_logging.TIMING_INVALID_ORDER, started_at, ended_at
            )
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

    # ---- Final Review R1: the non-finite door ---------------------------------
    #
    # `--duration-seconds nan` is the Final Reviewer's own probe. It is not a
    # negative number, so a `value < 0` guard never fires on it: NaN compares
    # False against every ordering test, and +inf is simply not negative. Both
    # reached `duration_s` verbatim, and neither `nan` nor `inf` satisfies the
    # explicit `duration_s >= 0` requirement.

    NONFINITE_PROBE = ("nan", "inf", "-inf", "NaN", "Infinity")

    def test_a_non_finite_explicit_duration_is_refused_like_a_negative_one(
        self,
    ) -> None:
        """The Final Review R1 probe verbatim: a VALID timestamp pair (so the
        pair check cannot be what rejects the row) plus an explicit non-finite
        duration. The cell must come back empty with a marker that names the
        reason, never `nan`/`inf` -- and never clamped to 0 or `abs()`-ed into a
        number a later reader would mistake for a measurement.
        """
        for index, supplied in enumerate(self.NONFINITE_PROBE):
            with self.subTest(duration=supplied):
                log_timing_event(
                    "run_os19",
                    base=self.base,
                    event="dispatch_settled",
                    phase="IMPLEMENTATION",
                    role="reviewer",
                    iteration=index,
                    started_at="2026-01-01T00:00:00+00:00",
                    ended_at="2026-01-01T00:00:01+00:00",
                    duration_seconds=supplied,
                )
                row = self.rows()[-1]
                self.assertEqual(row["duration_s"], "", row)
                self.assertIn(
                    run_logging.TIMING_INVALID_NONFINITE_DURATION, row["detail"]
                )
                # The pair itself was fine, so it is NOT quarantined -- only the
                # impossible number was thrown away.
                self.assertEqual(row["started_at"], "2026-01-01T00:00:00+00:00")
                self.assertEqual(row["ended_at"], "2026-01-01T00:00:01+00:00")
        self.assert_log_invariants()

    def test_the_non_finite_floats_themselves_are_refused_not_only_their_spellings(
        self,
    ) -> None:
        """The same three values as floats rather than strings: a Python caller
        reaches `log_timing_event` directly, so `float("nan")` must be judged the
        same as the CLI's `"nan"` text.
        """
        for index, supplied in enumerate(
            (float("nan"), float("inf"), float("-inf"))
        ):
            with self.subTest(duration=supplied):
                self.assertEqual(
                    run_logging._validate_duration(supplied),
                    ("", run_logging.TIMING_INVALID_NONFINITE_DURATION),
                )
                log_timing_event(
                    "run_os19",
                    base=self.base,
                    event="dispatch_settled",
                    phase="TEST",
                    role="worker",
                    iteration=index,
                    duration_seconds=supplied,
                )
                row = self.rows()[-1]
                self.assertEqual(row["duration_s"], "", row)
                self.assertIn(
                    run_logging.TIMING_INVALID_NONFINITE_DURATION, row["detail"]
                )
        self.assert_log_invariants()

    def test_a_finite_explicit_duration_still_goes_through_untouched(self) -> None:
        """The other half of R1: rejecting non-finite values may not cost the
        ordinary case anything. Zero, a fraction and a large finite value are all
        still written exactly as supplied, with no marker.
        """
        for index, (supplied, expected) in enumerate(
            ((0, "0.000"), (0.5, "0.500"), (423, "423.000"), ("7", "7.000"))
        ):
            with self.subTest(duration=supplied):
                log_timing_event(
                    "run_os19",
                    base=self.base,
                    event="dispatch_settled",
                    phase="DESIGN",
                    role="worker",
                    iteration=index,
                    duration_seconds=supplied,
                )
                row = self.rows()[-1]
                self.assertEqual(row["duration_s"], expected, row)
                self.assertNotIn("timing_invalid=", row["detail"])
        self.assert_log_invariants()

    def test_the_cli_path_refuses_a_non_finite_duration_too(self) -> None:
        """`timing-event --duration-seconds nan` is what a live Coordinator would
        actually type, and the flag takes raw text -- so the runtime CLI path
        gets the same probe, asserted over every row the run emitted.
        """
        for index, supplied in enumerate(self.NONFINITE_PROBE):
            with self.subTest(duration=supplied):
                stream = StringIO()
                with redirect_stdout(stream):
                    exit_code = cli_main(
                        [
                            "timing-event",
                            "--run-id",
                            "run_os19_nonfinite",
                            "--base",
                            str(self.base),
                            "--event",
                            "dispatch_settled",
                            "--phase",
                            "IMPLEMENTATION",
                            "--role",
                            "reviewer",
                            "--iteration",
                            str(index),
                            "--started-at",
                            "2026-01-01T00:00:00+00:00",
                            "--ended-at",
                            "2026-01-01T00:00:01+00:00",
                            # `=` rather than a space: argparse would otherwise
                            # read `-inf` as an option name.
                            f"--duration-seconds={supplied}",
                        ]
                    )
                self.assertEqual(exit_code, 0)
                settled = [
                    row
                    for row in self.rows("run_os19_nonfinite")
                    if row["event"] == "dispatch_settled"
                ][-1]
                self.assertEqual(settled["duration_s"], "", settled)
                self.assertIn(
                    run_logging.TIMING_INVALID_NONFINITE_DURATION, settled["detail"]
                )
        self.assert_log_invariants("run_os19_nonfinite")

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
        self.assert_quarantined(
            self.rows()[-1],
            run_logging.TIMING_INVALID_TIMESTAMP,
            "2026-08-24T01:00:00",
            "2026-08-24T01:05:00+00:00",
        )
        self.assert_log_invariants()

    def test_a_malformed_timestamp_says_so_instead_of_going_quiet(self) -> None:
        log_timing_event(
            "run_os19",
            base=self.base,
            event="dispatch_settled",
            started_at="not-a-timestamp",
            ended_at="2026-08-24T01:05:00+00:00",
        )
        self.assert_quarantined(
            self.rows()[-1],
            run_logging.TIMING_INVALID_TIMESTAMP,
            "not-a-timestamp",
            "2026-08-24T01:05:00+00:00",
        )
        self.assert_log_invariants()

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
        self.assert_quarantined(
            row,
            run_logging.TIMING_INVALID_ORDER,
            "2026-08-24T01:48:15Z",
            "2026-08-24T01:41:12Z",
        )

    # ---- B. Review round 1 BF-001: the supplied-duration door ------------------

    # The reviewer's own probe, verbatim: an out-of-order pair and a malformed
    # pair, each handed in together with an explicit, perfectly non-negative
    # `duration_seconds=7`. Before the correction both were written as
    # `duration_s=7.000` with the impossible pair intact in the timestamp columns
    # and no marker anywhere, because `log_timing_event()` validated the supplied
    # duration INSTEAD OF the pair rather than as well as it.
    BF001_PROBE = (
        ("2026-08-24T02:00:00+00:00", "2026-08-24T01:00:00+00:00", "out-of-order"),
        ("broken", "2026-08-24T01:00:00+00:00", "malformed"),
    )

    def test_a_supplied_duration_does_not_buy_an_invalid_pair_a_way_in(self) -> None:
        for started_at, ended_at, label in self.BF001_PROBE:
            with self.subTest(pair=label):
                log_timing_event(
                    "run_os19",
                    base=self.base,
                    event="dispatch_settled",
                    phase="IMPLEMENTATION",
                    role="reviewer",
                    iteration=2,
                    started_at=started_at,
                    ended_at=ended_at,
                    duration_seconds=7,
                )
                row = self.rows()[-1]
                self.assertNotIn("7", row["duration_s"])
                self.assert_quarantined(
                    row,
                    run_logging.TIMING_INVALID_ORDER
                    if label == "out-of-order"
                    else run_logging.TIMING_INVALID_TIMESTAMP,
                    started_at,
                    ended_at,
                )
        self.assert_log_invariants()

    def test_a_supplied_duration_still_survives_a_pair_that_is_actually_valid(
        self,
    ) -> None:
        """The check added for BF-001 rejects impossible pairs, not explicit
        durations: an offline reconstruction that hands in a valid pair and its
        own measured duration is still written exactly as given.
        """
        log_timing_event(
            "run_os19",
            base=self.base,
            event="dispatch_settled",
            started_at="2026-08-24T01:00:00+00:00",
            ended_at="2026-08-24T02:00:00+00:00",
            duration_seconds=7,
        )
        row = self.rows()[-1]
        self.assertEqual(row["duration_s"], "7.000")
        self.assertEqual(row["started_at"], "2026-08-24T01:00:00+00:00")
        self.assertEqual(row["ended_at"], "2026-08-24T02:00:00+00:00")
        self.assertEqual(row["detail"], "")
        self.assert_log_invariants()

    def test_a_lone_timestamp_that_is_not_a_timestamp_is_refused_as_well(self) -> None:
        """A half-filled row has no pair to be out of order with, but a value
        that cannot be read as a timestamp still may not sit in a timestamp
        column -- the row would claim a start instant nothing can interpret.
        """
        log_timing_event(
            "run_os19",
            base=self.base,
            event="phase_start",
            phase="IMPLEMENTATION",
            started_at="broken",
        )
        self.assert_quarantined(
            self.rows()[-1], run_logging.TIMING_INVALID_TIMESTAMP, "broken", ""
        )
        self.assert_log_invariants()

    def test_a_missing_side_is_not_an_error_and_is_left_alone(self) -> None:
        """The ordinary half-filled row: an open boundary that has not ended yet
        keeps its real `started_at` and says nothing about anything being wrong.
        """
        log_timing_event(
            "run_os19",
            base=self.base,
            event="phase_start",
            phase="IMPLEMENTATION",
            started_at="2026-08-24T01:00:00+00:00",
        )
        row = self.rows()[-1]
        self.assertEqual(row["started_at"], "2026-08-24T01:00:00+00:00")
        self.assertEqual(row["ended_at"], "")
        self.assertEqual(row["duration_s"], "")
        self.assertEqual(row["detail"], "")
        self.assert_log_invariants()

    def test_the_cli_path_refuses_the_same_probe(self) -> None:
        """The same two probes through the runtime CLI path the reviewer used
        (`timing-event --duration-seconds 7`), including the boundary rows the
        tracker opens around them -- the invariant is asserted over every row the
        run emitted, not only over the settlement rows.
        """
        for started_at, ended_at, label in self.BF001_PROBE:
            with self.subTest(pair=label):
                stream = StringIO()
                with redirect_stdout(stream):
                    exit_code = cli_main(
                        [
                            "timing-event",
                            "--run-id",
                            "run_os19_cli",
                            "--base",
                            str(self.base),
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
                            "--duration-seconds",
                            "7",
                        ]
                    )
                self.assertEqual(exit_code, 0)
                settled = [
                    row
                    for row in self.rows("run_os19_cli")
                    if row["event"] == "dispatch_settled"
                ][-1]
                self.assert_quarantined(
                    settled,
                    run_logging.TIMING_INVALID_ORDER
                    if label == "out-of-order"
                    else run_logging.TIMING_INVALID_TIMESTAMP,
                    started_at,
                    ended_at,
                )
        self.assert_log_invariants("run_os19_cli")


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
        ("2026-08-24T01:41:30Z", "2026-08-24T01:41:30+00:00"),  # mixed spelling, 0s
    )

    # A missing side is deliberately NOT in the matrix above: `timing-event
    # --event dispatch_settled` is a lifecycle command run at the moment of
    # settlement, so an omitted `--ended-at` is filled from its own authoritative
    # clock, while log_timing_event() is the raw writer and leaves it blank.
    # That difference is the OS-19 fix, not a drift -- see
    # test_the_cli_fills_a_missing_settlement_time_and_the_raw_writer_does_not.

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

    def test_a_supplied_duration_cannot_smuggle_an_invalid_pair_through_the_installed_cli(
        self,
    ) -> None:
        """Review round 1 BF-001, on the path a live Coordinator actually runs.

        The reviewer's probe was a subprocess against the INSTALLED copy, so the
        regression is too: an out-of-order pair and a malformed pair, each with
        `--duration-seconds 7`. Both must come back quarantined -- no `7.000`, no
        timestamps in the timestamp columns, the evidence in `detail` -- and must
        match what the in-repo Python writer produces for the same input, so the
        fix cannot hold on one path and not the other.
        """
        probe = (
            (
                "2026-08-24T02:00:00+00:00",
                "2026-08-24T01:00:00+00:00",
                run_logging.TIMING_INVALID_ORDER,
            ),
            ("broken", "2026-08-24T01:00:00+00:00", run_logging.TIMING_INVALID_TIMESTAMP),
        )
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
            for index, (started_at, ended_at, marker) in enumerate(probe):
                with self.subTest(started_at=started_at, ended_at=ended_at):
                    run_id = f"run_bf001_{index}"
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
                            "--duration-seconds",
                            "7",
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
                    self.assertEqual(self.cell(installed_log, "duration_s"), "")
                    self.assertEqual(self.cell(installed_log, "started_at"), "")
                    self.assertEqual(self.cell(installed_log, "ended_at"), "")
                    detail = self.cell(installed_log, "detail")
                    self.assertIn(marker, detail)
                    self.assertIn(
                        f"{run_logging.TIMING_INVALID_STARTED_AT_FIELD}={started_at}",
                        detail,
                    )
                    self.assertIn(
                        f"{run_logging.TIMING_INVALID_ENDED_AT_FIELD}={ended_at}",
                        detail,
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
                        duration_seconds=7,
                    )
                    python_log = timing_log_path(run_id, base=python_base)
                    for column in ("started_at", "ended_at", "duration_s", "detail"):
                        self.assertEqual(
                            self.cell(installed_log, column),
                            self.cell(python_log, column),
                            f"{column} differs between the installed CLI and the "
                            "Python path",
                        )

    def test_a_non_finite_duration_is_refused_through_the_installed_cli_too(
        self,
    ) -> None:
        """Final Review R1, on the path a live Coordinator actually runs.

        The Final Reviewer's probe went through the INSTALLED copy, so the
        regression does too: a real subprocess, from an unrelated project
        directory, with this repository's checkout off sys.path. `nan`, `inf`
        and `-inf` must all come back with an empty `duration_s` and the
        non-finite marker, and must match what the in-repo Python writer
        produces for the same input -- byte-identity of the two files is not by
        itself proof that the executed behaviour agrees.
        """
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
            for index, supplied in enumerate(("nan", "inf", "-inf")):
                with self.subTest(duration=supplied):
                    run_id = f"run_nonfinite_{index}"
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
                            "2026-01-01T00:00:00+00:00",
                            "--ended-at",
                            "2026-01-01T00:00:01+00:00",
                            f"--duration-seconds={supplied}",
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
                    self.assertEqual(self.cell(installed_log, "duration_s"), "")
                    self.assertIn(
                        run_logging.TIMING_INVALID_NONFINITE_DURATION,
                        self.cell(installed_log, "detail"),
                    )

                    python_base = Path(target_project) / "python-path"
                    log_timing_event(
                        run_id,
                        base=python_base,
                        event="dispatch_settled",
                        phase="IMPLEMENTATION",
                        role="reviewer",
                        iteration=2,
                        started_at="2026-01-01T00:00:00+00:00",
                        ended_at="2026-01-01T00:00:01+00:00",
                        duration_seconds=supplied,
                    )
                    python_log = timing_log_path(run_id, base=python_base)
                    for column in ("started_at", "ended_at", "duration_s", "detail"):
                        self.assertEqual(
                            self.cell(installed_log, column),
                            self.cell(python_log, column),
                            f"{column} differs between the installed CLI and the "
                            "Python path",
                        )

    def test_the_cli_fills_a_missing_settlement_time_and_the_raw_writer_does_not(
        self,
    ) -> None:
        """The one intentional difference between the two, stated explicitly."""
        with tempfile.TemporaryDirectory() as base_directory:
            base = Path(base_directory)
            stream = StringIO()
            with redirect_stdout(stream):
                cli_main(
                    [
                        "timing-event",
                        "--run-id",
                        "run_fill",
                        "--base",
                        str(base),
                        "--event",
                        "dispatch_settled",
                        "--phase",
                        "IMPLEMENTATION",
                        "--role",
                        "reviewer",
                        "--iteration",
                        "2",
                        "--started-at",
                        "2026-08-24T01:41:30+00:00",
                    ]
                )
            cli_row = self.cell(timing_log_path("run_fill", base=base), "ended_at")
            self.assertTrue(cli_row, "the CLI must settle on its own clock")

            log_timing_event(
                "run_raw",
                base=base,
                event="dispatch_settled",
                started_at="2026-08-24T01:41:30+00:00",
            )
            self.assertEqual(
                self.cell(timing_log_path("run_raw", base=base), "ended_at"), ""
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


# ---- OS-22: the Final Review per-dispatch audit record family ----------------------
# release_manifest.USER_PATH_PATTERNS refuses a literal home-directory path anywhere
# under scripts/, and verify_package.py enforces that over every packaged file -- so
# the redaction fixtures below are assembled at runtime instead of written out. The
# placeholder form the redactor emits is exempt from that scan and stays literal.
_HOME = "/" + "Users" + "/"


def _local_path(user: str, rest: str = "") -> str:
    return f"{_HOME}{user}/{rest}"




class _AuditTestCase(unittest.TestCase):
    """One temporary run root, and the small helpers every audit test needs."""

    RUN_ID = "run_os22"

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

    def write_record(self, **kwargs):
        kwargs.setdefault("final_review_attempt", 1)
        kwargs.setdefault("task_id", "task_aaa")
        kwargs.setdefault("dispatch_id", "ctx_bbb")
        kwargs.setdefault("capture", False)
        return run_logging.write_final_review_audit_record(
            self.RUN_ID, base=self.base, **kwargs
        )

    def record_json(self, directory: Path) -> dict:
        return json.loads((directory / "record.json").read_text(encoding="utf-8"))

    def log_rows(self) -> list[str]:
        path = self.root / ORCHESTRATOR_LOG_FILENAME
        if not path.exists():
            return []
        return [
            line
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.startswith("| 2") or line.startswith("| 1")
        ]


PASSING_REPORT = (
    "# Final Adversarial Review\n\nRESULT: PASS\nREVIEW_VERDICT: PASS\n\n"
    "## Findings\n\nID: R1\nBlocking: NO\nLocation: scripts/x.py\n"
)
FAILING_REPORT = (
    "RESULT: FAIL\nREVIEW_VERDICT: FAIL\n\nID: R1\nBlocking: YES\nLocation: a.py\n\n"
    "ID: R2\nBlocking: NO\nLocation: b.py\n"
)


class FinalReviewDispatchKeyTests(_AuditTestCase):
    """A.2: the key is validated before any file is touched."""

    def test_the_key_leads_with_the_attempt_and_carries_both_ids(self) -> None:
        self.assertEqual(
            run_logging.final_review_dispatch_key(2, "task_a", "ctx_b"),
            "attempt2__task_a__ctx_b",
        )

    def test_a_missing_dispatch_id_reads_nodispatch(self) -> None:
        """A pre-dispatch failure has no dispatch id, and that is a real state."""
        self.assertEqual(
            run_logging.final_review_dispatch_key(1, "task_a"),
            "attempt1__task_a__nodispatch",
        )

    def test_every_component_is_validated_fail_closed(self) -> None:
        for attempt, task_id, dispatch_id in (
            (0, "task_a", "ctx_b"),
            (1, "../etc", "ctx_b"),
            (1, "task_a", "../etc"),
            (1, "", "ctx_b"),
            (1, ".hidden", "ctx_b"),
            (1, "task a", "ctx_b"),
        ):
            with self.subTest(attempt=attempt, task=task_id, dispatch=dispatch_id):
                with self.assertRaises(RunLoggingError):
                    run_logging.final_review_dispatch_key(
                        attempt, task_id, dispatch_id
                    )

    def test_a_bool_is_not_an_attempt_number(self) -> None:
        with self.assertRaises(RunLoggingError):
            run_logging.final_review_dispatch_key(True, "task_a", "ctx_b")

    def test_an_invalid_key_touches_no_file(self) -> None:
        with self.assertRaises(RunLoggingError):
            self.write_record(task_id="../escape")
        self.assertFalse((self.root / "final_review_audit").exists())


class AuditRecordWriteTests(_AuditTestCase):
    """A.3/A.4: what a published record is, and what it holds."""

    def test_the_published_unit_is_a_directory_holding_exactly_three_files(
        self,
    ) -> None:
        self.write_report(PASSING_REPORT)

        published = self.write_record(
            provenance_state="accepted", settlement_state="settled"
        )

        self.assertTrue(published.is_dir())
        self.assertEqual(
            sorted(entry.name for entry in published.iterdir()),
            ["input.md", "record.json", "report.md"],
        )
        self.assertEqual(published.name, "attempt1__task_aaa__ctx_bbb")

    def test_schema_version_is_the_first_key_of_the_file(self) -> None:
        published = self.write_record()

        text = (published / "record.json").read_text(encoding="utf-8")
        self.assertTrue(text.lstrip().startswith('{\n  "schema_version"'))
        self.assertTrue(text.endswith("\n"))
        self.assertEqual(
            self.record_json(published)["schema_version"],
            run_logging.FINAL_REVIEW_AUDIT_SCHEMA_VERSION,
        )

    def test_every_required_field_is_present(self) -> None:
        record = self.record_json(self.write_record())

        for field in run_logging._REQUIRED_RECORD_FIELDS:
            with self.subTest(field=field):
                self.assertIn(field, record)
        self.assertEqual(record["record_kind"], "final_review_dispatch_audit")
        self.assertEqual(record["dispatch_key"], "attempt1__task_aaa__ctx_bbb")
        self.assertTrue(record["stored_task_spec"]["is_stored_spec_not_delivered_bytes"])
        self.assertFalse(record["delivery_evidence"]["preamble_captured"])

    def test_the_writer_refuses_to_overwrite_a_published_record(self) -> None:
        """A retry must never clobber the record of the dispatch it replaced."""
        self.write_report(PASSING_REPORT)
        published = self.write_record(
            provenance_state="accepted", settlement_state="settled"
        )
        before = (published / "record.json").read_bytes()

        with self.assertRaises(run_logging.FinalReviewAuditCollision):
            self.write_record(provenance_state="unknown")

        self.assertEqual((published / "record.json").read_bytes(), before)
        self.assertTrue(
            any("final_review_audit_collision" in row for row in self.log_rows())
        )

    def test_a_retry_under_a_new_identity_produces_a_separate_record(self) -> None:
        self.write_report(PASSING_REPORT)

        first = self.write_record(
            task_id="task_one", dispatch_id="ctx_one",
            provenance_state="voided", void_reason="dispatch_input_rejected",
            settlement_state="not_settled", failure_detail="agent_prompt_blocked",
            observed_input_bytes=14805,
        )
        second = self.write_record(
            task_id="task_two", dispatch_id="ctx_two",
            provenance_state="accepted", settlement_state="settled",
        )

        self.assertNotEqual(first, second)
        self.assertEqual(self.record_json(first)["provenance_state"], "voided")
        self.assertEqual(self.record_json(second)["provenance_state"], "accepted")
        self.assertEqual(
            self.record_json(first)["failure_detail"], "agent_prompt_blocked"
        )
        self.assertEqual(self.record_json(first)["observed_input_bytes"], 14805)

    def test_the_report_snapshot_is_parsed_verbatim(self) -> None:
        self.write_report(FAILING_REPORT)

        record = self.record_json(
            self.write_record(provenance_state="accepted", settlement_state="settled")
        )

        parsed = record["report"]["parsed"]
        self.assertEqual(parsed["parse_status"], "ok")
        self.assertEqual(parsed["result"], "FAIL")
        self.assertEqual(parsed["review_verdict"], "FAIL")
        self.assertEqual(parsed["blocking_finding_ids"], ["R1"])
        self.assertEqual(parsed["non_blocking_finding_ids"], ["R2"])

    def test_review_verdict_is_not_collapsed_into_the_two_valued_gate(self) -> None:
        self.write_report("RESULT: PASS\nREVIEW_VERDICT: PASS WITH NOTES\n")

        record = self.record_json(self.write_record())

        self.assertEqual(record["report"]["parsed"]["result"], "PASS")
        self.assertEqual(
            record["report"]["parsed"]["review_verdict"], "PASS WITH NOTES"
        )

    def test_report_resolution_records_which_path_rule_applied(self) -> None:
        self.write_report(PASSING_REPORT, attempt=1)
        laddered = self.record_json(
            self.write_record(final_review_attempt=1, dispatch_id="ctx_one")
        )
        self.assertEqual(laddered["report"]["resolution"], "ladder")

        # Attempt 2 with no _iteration2 file: the deferred suffix defect, recorded
        # as data rather than silently absorbed.
        fallback = self.record_json(
            self.write_record(final_review_attempt=2, dispatch_id="ctx_two")
        )
        self.assertEqual(fallback["report"]["resolution"], "fallback_unsuffixed")
        self.assertEqual(fallback["report"]["capture_status"], "captured")

        explicit_path = self.base / "elsewhere.md"
        explicit_path.write_text(PASSING_REPORT, encoding="utf-8")
        explicit = self.record_json(
            self.write_record(dispatch_id="ctx_three", report_path=explicit_path)
        )
        self.assertEqual(explicit["report"]["resolution"], "explicit")

    def test_a_written_record_is_logged_with_no_new_column(self) -> None:
        self.write_report(PASSING_REPORT)

        self.write_record(provenance_state="accepted", settlement_state="settled")

        rows = self.log_rows()
        self.assertTrue(rows)
        row = rows[-1]
        self.assertIn("final_review_audit", row)
        self.assertIn("provenance=accepted", row)
        self.assertIn("task_aaa", row)
        self.assertIn("ctx_bbb", row)
        header = (
            (self.root / ORCHESTRATOR_LOG_FILENAME)
            .read_text(encoding="utf-8")
            .splitlines()[0]
        )
        self.assertEqual(
            header, "| " + " | ".join(ORCHESTRATOR_LOG_COLUMNS) + " |"
        )


class AuditProvenanceTests(_AuditTestCase):
    """D-B: the state machine, and the reader that refuses to pick a winner."""

    def test_the_default_provenance_is_unknown_and_never_accepted(self) -> None:
        record = self.record_json(self.write_record())

        self.assertEqual(record["provenance_state"], "unknown")
        self.assertEqual(record["void_reason"], "")

    def test_every_void_reason_round_trips(self) -> None:
        for index, reason in enumerate(run_logging.VOID_REASONS):
            with self.subTest(reason=reason):
                published = self.write_record(
                    task_id=f"task_v{index}",
                    provenance_state="voided",
                    void_reason=reason,
                    settlement_state="not_settled",
                )
                self.assertEqual(self.record_json(published)["void_reason"], reason)

    def test_the_writer_is_fail_closed_in_every_direction(self) -> None:
        for kwargs in (
            {"provenance_state": "APPROVED"},
            {"provenance_state": "voided"},
            {"provenance_state": "accepted", "void_reason": "report_missing"},
            {"provenance_state": "voided", "void_reason": "made_up"},
            {"settlement_state": "maybe"},
            {"input_altered_across_retry": "probably"},
        ):
            with self.subTest(**kwargs):
                with self.assertRaises(RunLoggingError):
                    self.write_record(**kwargs)

    def test_one_accepted_dispatch_is_returned(self) -> None:
        self.write_record(task_id="task_one", provenance_state="voided",
                          void_reason="dispatch_input_rejected")
        self.write_record(task_id="task_two", provenance_state="accepted",
                          settlement_state="settled")

        provenance = run_logging.read_final_review_attempt_provenance(
            self.RUN_ID, 1, base=self.base
        )

        self.assertEqual(
            provenance["accepted_dispatch_key"], "attempt1__task_two__ctx_bbb"
        )
        self.assertEqual(provenance["violations"], [])
        self.assertEqual(len(provenance["records"]), 2)

    def test_two_accepted_dispatches_are_reported_not_resolved(self) -> None:
        self.write_record(task_id="task_one", provenance_state="accepted",
                          settlement_state="settled")
        self.write_record(task_id="task_two", provenance_state="accepted",
                          settlement_state="settled")

        provenance = run_logging.read_final_review_attempt_provenance(
            self.RUN_ID, 1, base=self.base
        )

        self.assertIsNone(provenance["accepted_dispatch_key"])
        self.assertEqual(
            [violation["code"] for violation in provenance["violations"]],
            ["multiple_accepted_dispatches"],
        )

    def test_an_attempt_with_no_accepted_dispatch_produced_no_verdict(self) -> None:
        self.write_record(provenance_state="voided", void_reason="report_missing",
                          settlement_state="settled")

        provenance = run_logging.read_final_review_attempt_provenance(
            self.RUN_ID, 1, base=self.base
        )

        self.assertIsNone(provenance["accepted_dispatch_key"])
        self.assertEqual(
            [violation["code"] for violation in provenance["violations"]],
            ["no_accepted_dispatch"],
        )

    def test_a_voided_record_is_never_returned_as_a_verdict(self) -> None:
        self.write_report(FAILING_REPORT)
        self.write_record(provenance_state="voided", void_reason="superseded_by_retry",
                          settlement_state="settled")

        provenance = run_logging.read_final_review_attempt_provenance(
            self.RUN_ID, 1, base=self.base
        )

        self.assertIsNone(provenance["accepted_dispatch_key"])

    def test_attempt_grouping_comes_from_the_field_not_the_filename(self) -> None:
        self.write_record(final_review_attempt=1, task_id="task_one",
                          provenance_state="accepted", settlement_state="settled")
        self.write_record(final_review_attempt=2, task_id="task_two",
                          provenance_state="accepted", settlement_state="settled")

        first = run_logging.read_final_review_attempt_provenance(
            self.RUN_ID, 1, base=self.base
        )
        second = run_logging.read_final_review_attempt_provenance(
            self.RUN_ID, 2, base=self.base
        )

        self.assertEqual(first["records"], ["attempt1__task_one__ctx_bbb"])
        self.assertEqual(second["records"], ["attempt2__task_two__ctx_bbb"])


class AuditReaderCompatibilityTests(_AuditTestCase):
    """A.5: every failure mode reads unknown, never accepted."""

    def test_a_missing_record_reads_missing(self) -> None:
        record, status = run_logging.read_final_review_audit_record(
            self.root / "nope.json"
        )
        self.assertEqual((record, status), (None, "missing"))

    def test_an_unparseable_record_reads_malformed(self) -> None:
        path = self.root / "broken.json"
        path.write_text("{not json", encoding="utf-8")

        self.assertEqual(
            run_logging.read_final_review_audit_record(path), (None, "malformed")
        )

    def test_a_record_missing_a_required_field_reads_malformed(self) -> None:
        published = self.write_record(provenance_state="accepted",
                                      settlement_state="settled")
        record = self.record_json(published)
        del record["task_id"]
        (published / "record.json").write_text(json.dumps(record), encoding="utf-8")

        parsed, status = run_logging.read_final_review_audit_record(
            published / "record.json"
        )

        self.assertEqual((parsed, status), (None, "malformed"))

    def test_an_unknown_major_is_refused_outright(self) -> None:
        published = self.write_record(provenance_state="accepted",
                                      settlement_state="settled")
        record = self.record_json(published)
        record["schema_version"] = "2.0"
        (published / "record.json").write_text(json.dumps(record), encoding="utf-8")

        parsed, status = run_logging.read_final_review_audit_record(
            published / "record.json"
        )

        self.assertEqual((parsed, status), (None, "unknown_major"))

    def test_a_higher_minor_is_read_and_unknown_fields_ignored(self) -> None:
        published = self.write_record(provenance_state="accepted",
                                      settlement_state="settled")
        record = self.record_json(published)
        record["schema_version"] = "1.7"
        record["a_field_from_the_future"] = 42
        (published / "record.json").write_text(json.dumps(record), encoding="utf-8")

        parsed, status = run_logging.read_final_review_audit_record(
            published / "record.json"
        )

        self.assertEqual(status, "ok")
        self.assertEqual(parsed["provenance_state"], "accepted")

    def test_an_unreadable_record_can_never_be_the_accepted_one(self) -> None:
        published = self.write_record(provenance_state="accepted",
                                      settlement_state="settled")
        (published / "record.json").write_text("{}", encoding="utf-8")

        provenance = run_logging.read_final_review_attempt_provenance(
            self.RUN_ID, 1, base=self.base
        )

        self.assertIsNone(provenance["accepted_dispatch_key"])
        self.assertEqual(
            provenance["unreadable"],
            [{"dispatch_key": published.name, "status": "malformed"}],
        )


class AuditWriteBoundaryFaultTests(_AuditTestCase):
    """A.3 P4/P5/P6: a failure at any boundary publishes nothing and blocks nothing."""

    BOUNDARIES = ("mkdir", "write", "fsync", "rename")

    def _fail_at(self, boundary: str):
        if boundary == "mkdir":
            return patch("scripts.run_logging.os.mkdir", side_effect=OSError("boom"))
        if boundary == "write":
            return patch(
                "scripts.run_logging._write_staged_file", side_effect=OSError("boom")
            )
        if boundary == "fsync":
            return patch("scripts.run_logging.os.fsync", side_effect=OSError("boom"))
        return patch("scripts.run_logging.os.rename", side_effect=OSError("boom"))

    def test_a_failure_at_any_boundary_publishes_nothing(self) -> None:
        for boundary in self.BOUNDARIES:
            with self.subTest(boundary=boundary):
                audit_dir = self.root / "final_review_audit"
                shutil.rmtree(audit_dir, ignore_errors=True)
                with self._fail_at(boundary):
                    with self.assertRaises(run_logging.FinalReviewAuditWriteFailed):
                        self.write_record()
                self.assertFalse((audit_dir / "attempt1__task_aaa__ctx_bbb").exists())
                self.assertTrue(
                    any(
                        "final_review_audit_write_failed" in row
                        for row in self.log_rows()
                    )
                )

    def test_a_failed_write_never_blocks_the_same_dispatch_key_later(self) -> None:
        """The D-003 failure mode: the old protocol could orphan a dispatch forever."""
        with self._fail_at("write"):
            with self.assertRaises(run_logging.FinalReviewAuditWriteFailed):
                self.write_record()

        published = self.write_record(
            provenance_state="accepted", settlement_state="settled"
        )

        self.assertTrue(published.is_dir())
        self.assertEqual(self.record_json(published)["provenance_state"], "accepted")

    def test_an_abandoned_staging_entry_with_no_record_is_retained_and_reported(
        self,
    ) -> None:
        staging = self.root / "final_review_audit" / ".staging"
        staging.mkdir(parents=True)
        orphan = staging / "attempt9__task_dead__ctx_dead.1234-abcd"
        orphan.mkdir()
        (orphan / "input.md").write_text("partial", encoding="utf-8")

        incomplete = run_logging.sweep_final_review_audit_staging(
            self.RUN_ID, base=self.base
        )

        self.assertTrue(orphan.exists(), "the only evidence of that attempt")
        self.assertEqual(
            incomplete,
            [
                {
                    "dispatch_key": "attempt9__task_dead__ctx_dead",
                    "staging_dir": orphan.name,
                    "files_present": ["input.md"],
                    "files_absent": ["report.md", "record.json"],
                }
            ],
        )

    def test_an_abandoned_staging_entry_whose_record_published_is_scratch(
        self,
    ) -> None:
        published = self.write_record(provenance_state="accepted",
                                      settlement_state="settled")
        staging = self.root / "final_review_audit" / ".staging"
        staging.mkdir(parents=True, exist_ok=True)
        redundant = staging / f"{published.name}.9999-ffff"
        redundant.mkdir()

        incomplete = run_logging.sweep_final_review_audit_staging(
            self.RUN_ID, base=self.base
        )

        self.assertEqual(incomplete, [])
        self.assertFalse(redundant.exists())
        self.assertTrue(published.is_dir(), "the published bytes are untouched")

    def test_staging_is_never_read_as_a_record(self) -> None:
        staging = self.root / "final_review_audit" / ".staging"
        (staging / "attempt1__task_x__ctx_y.1-2").mkdir(parents=True)

        self.assertEqual(
            run_logging.iter_final_review_audit_records(self.RUN_ID, base=self.base),
            [],
        )
        provenance = run_logging.read_final_review_attempt_provenance(
            self.RUN_ID, 1, base=self.base
        )
        self.assertEqual(provenance["records"], [])
        self.assertIsNone(provenance["accepted_dispatch_key"])


class RedactionPolicyTests(unittest.TestCase):
    """D-C: deterministic, ordered, and carrying no redacted value."""

    def test_a_dispatch_capability_never_survives(self) -> None:
        redacted, counts = run_logging.redact_text(
            "dcap_wU0XeTEkK6NvqvqcHdWD7tmS7Q87vP3Ne8_bZ0crt04 is the token"
        )

        self.assertNotIn("dcap_wU0XeTEkK6", redacted)
        self.assertIn("<REDACTED:orca_dispatch_capability>", redacted)
        self.assertEqual(
            counts, ({"category": "orca_dispatch_capability", "count": 1},)
        )

    def test_only_the_user_segment_of_a_local_path_is_replaced(self) -> None:
        redacted, _counts = run_logging.redact_text(
            _local_path("someone", "aiAssistedProjects/orca-skills/scripts/x.py")
        )

        self.assertEqual(
            redacted,
            _HOME
            + "<REDACTED:absolute_local_path>/aiAssistedProjects/orca-skills"
            + "/scripts/x.py",
        )

    def test_an_already_placeheld_path_is_not_double_redacted(self) -> None:
        text = _HOME + "<name>/skills"
        self.assertEqual(run_logging.redact_text(text), (text, ()))

    def test_an_env_secret_keeps_its_name_and_loses_its_value(self) -> None:
        redacted, counts = run_logging.redact_text('GITHUB_TOKEN="ghp_abc123"')

        self.assertIn("GITHUB_TOKEN", redacted)
        self.assertNotIn("ghp_abc123", redacted)
        self.assertEqual(counts, ({"category": "env_secret_pattern", "count": 1},))

    def test_a_url_credential_loses_its_userinfo(self) -> None:
        redacted, counts = run_logging.redact_text(
            "clone https://alice:s3cret@example.com/repo.git"
        )

        self.assertNotIn("s3cret", redacted)
        self.assertNotIn("alice", redacted)
        self.assertEqual(counts, ({"category": "url_credential", "count": 1},))

    def test_identifiers_that_are_not_credentials_are_preserved(self) -> None:
        """Section 1 explicitly requires reviewer terminal identity."""
        text = (
            "term_6ac06c14-6bb5-4e56-ac30-4ecb313371f3 task_2d0a6f4fc5a4 "
            "ctx_ab12cd34ef56 capability_hash=a5f41c33c097c51c"
        )
        self.assertEqual(run_logging.redact_text(text), (text, ()))

    def test_redaction_is_deterministic(self) -> None:
        text = (
            f"dcap_AAAAAAAAAAAA {_local_path('one', 'a')} "
            f"{_local_path('two', 'b')} API_KEY=zzz "
            "https://u:p@h/x dcap_BBBBBBBBBBBB"
        )

        first = run_logging.redact_text(text)
        second = run_logging.redact_text(text)

        self.assertEqual(first, second)
        self.assertEqual(
            run_logging.sha256_text(first[0]), run_logging.sha256_text(second[0])
        )

    def test_the_counts_are_ordered_by_policy_and_omit_zero(self) -> None:
        redacted, counts = run_logging.redact_text(
            f"dcap_AAAAAAAAAAAA and {_local_path('one', 'a')} "
            f"and {_local_path('two', 'b')}"
        )

        self.assertEqual(
            counts,
            (
                {"category": "orca_dispatch_capability", "count": 1},
                {"category": "absolute_local_path", "count": 2},
            ),
        )
        self.assertNotIn("url_credential", redacted)

    def test_nothing_matched_reads_as_an_empty_list(self) -> None:
        self.assertEqual(run_logging.redact_text("plain text"), ("plain text", ()))

    def test_the_counts_carry_no_redacted_value_and_no_offset(self) -> None:
        _redacted, counts = run_logging.redact_text("dcap_SECRETVALUE1234")

        for entry in counts:
            self.assertEqual(set(entry), {"category", "count"})
            self.assertNotIn("SECRETVALUE", json.dumps(entry))

    def test_an_unknown_policy_version_is_refused(self) -> None:
        with self.assertRaises(RunLoggingError):
            run_logging.redact_text("x", policy_version="redaction/9.9")


class RetainedArtifactSecurityTests(_AuditTestCase):
    """T-3's shape: what reaches disk, and whether the digests re-derive."""

    SECRET_REPORT = (
        "RESULT: FAIL\nREVIEW_VERDICT: FAIL\n\n"
        "Location: " + _local_path("luminous", "orca-skills/scripts/x.py") + "\n"
        "capability: dcap_wU0XeTEkK6NvqvqcHdWD7tmS7Q87vP3Ne8_bZ0crt04\n"
    )

    def test_no_secret_survives_into_the_retained_report(self) -> None:
        self.write_report(self.SECRET_REPORT)

        published = self.write_record(provenance_state="accepted",
                                      settlement_state="settled")

        retained = (published / "report.md").read_text(encoding="utf-8")
        self.assertNotIn("dcap_wU0XeTEkK6", retained)
        self.assertNotIn(_local_path("luminous"), retained)
        self.assertNotIn("luminous", retained)
        self.assertIn("<REDACTED:orca_dispatch_capability>", retained)

    def test_the_retained_artifact_carries_the_redacted_text_and_nothing_else(
        self,
    ) -> None:
        """No header, no front matter: the digest is verifiable with read_bytes()."""
        self.write_report(self.SECRET_REPORT)

        published = self.write_record()

        record = self.record_json(published)
        data = (published / "report.md").read_bytes()
        self.assertEqual(
            run_logging.sha256_bytes(data),
            record["report"]["artifact_digest_post_redaction"],
        )
        self.assertEqual(len(data), record["report"]["byte_length_post_redaction"])
        self.assertEqual(
            data.decode("utf-8"), run_logging.redact_text(self.SECRET_REPORT)[0]
        )

    def test_the_pre_and_post_identity_is_rederivable(self) -> None:
        self.write_report(self.SECRET_REPORT)

        record = self.record_json(self.write_record())

        report = record["report"]
        self.assertEqual(
            report["report_digest_pre_redaction"],
            run_logging.sha256_text(self.SECRET_REPORT),
        )
        redacted, counts = run_logging.redact_text(self.SECRET_REPORT)
        self.assertEqual(
            report["artifact_digest_post_redaction"], run_logging.sha256_text(redacted)
        )
        self.assertEqual(report["redactions"], [dict(entry) for entry in counts])
        self.assertEqual(
            report["redaction_policy_version"],
            run_logging.FINAL_REVIEW_REDACTION_POLICY_VERSION,
        )

    def test_the_four_identity_fields_are_all_present(self) -> None:
        self.write_report(PASSING_REPORT)

        report = self.record_json(self.write_record())["report"]

        for field in (
            "report_digest_pre_redaction",
            "redaction_policy_version",
            "artifact_digest_post_redaction",
            "redactions",
        ):
            with self.subTest(field=field):
                self.assertIsNotNone(report[field])

    def test_the_implementation_hard_codes_no_observed_input_size(self) -> None:
        """ANALYSIS F6: an observed agent_prompt_blocked size is not a product
        constant. `observed_input_bytes` is data the runtime reports, never a
        threshold this module compares against."""
        source = (REPO_ROOT / "scripts" / "run_logging.py").read_text(encoding="utf-8")
        section = source.split("# ---- OS-22:")[1]
        for forbidden in ("14805", "5553", "2269", "14.8", "5.5", "2.3"):
            with self.subTest(constant=forbidden):
                self.assertNotIn(forbidden, section)


class RecordMetadataRedactionTests(_AuditTestCase):
    """record.json is a retained artifact too: its free-form metadata is redacted.

    Redaction of `input.md`/`report.md` alone leaves the record itself as a leak
    channel -- a workspace path in `process_incarnation`, a credential quoted into
    `last_failure`, an agent command line, a human note. Every one of those is
    exercised here through the real writer, against the bytes that reach disk.
    """

    CAPABILITY = "dcap_AAAAAAAAAAAAAAAAAAAA"
    SECRET = "ORCA_TOKEN=topsecretvalue"
    USER = "alice"

    def poisoned(self, label: str) -> str:
        return (
            f"{label}: {self.CAPABILITY} {self.SECRET} "
            + _local_path(self.USER, "private/repo")
        )

    def evidence(self) -> dict:
        return {
            "status": "failed",
            "contract_version": "1.0",
            "capability_hash": "sha256:0123456789abcdef",
            "assignee_handle": "term_assignee",
            "process_incarnation": "pid:7:" + _local_path(self.USER, "private/repo"),
            "failure_count": 2,
            "last_failure": self.poisoned("last_failure"),
            "termination_reason": self.poisoned("termination_reason"),
        }

    def poisoned_report(self) -> str:
        """A well-formed report whose FINDING IDS are report-controlled hostile
        tokens: one credential-shaped (ID-shaped, so redaction has to catch it) and
        two that are not id-shaped at all (so the shape check has to)."""
        return (
            "RESULT: PASS\nREVIEW_VERDICT: PASS WITH NOTES\n\n"
            f"ID: {self.CAPABILITY}\nBlocking: YES\nLocation: a.py\n\n"
            f"ID: {self.SECRET}\nBlocking: NO\nLocation: b.py\n\n"
            f"ID: {_local_path(self.USER, 'private/repo')}\n"
            "Blocking: NO\nLocation: c.py\n"
        )

    def write_poisoned_record(self, report: str | None = None, **overrides):
        """One record with a credential and a home path down every covered path."""
        self.write_report(self.poisoned_report() if report is None else report)
        with patch(
            "scripts.run_logging.capture_stored_task_spec",
            return_value=(None, self.poisoned("capture failed")),
        ), patch(
            "scripts.run_logging.capture_delivery_evidence",
            return_value=(self.evidence(), ""),
        ), patch(
            "scripts.run_logging._capture_repository_state", return_value=None
        ):
            kwargs = dict(
                capture=True,
                reviewer_terminal="term_reviewer",
                reviewer_agent_command=self.poisoned("claude --print"),
                reviewer_agent_origin=self.poisoned("resolved_from"),
                provenance_state="voided",
                void_reason="settlement_failure",
                settlement_state="not_settled",
                failure_detail=self.poisoned("agent_prompt_blocked"),
                notes=self.poisoned("see"),
            )
            kwargs.update(overrides)
            return self.write_record(**kwargs)

    def test_no_credential_and_no_home_path_survives_into_record_json(self) -> None:
        published = self.write_poisoned_record()

        raw = (published / "record.json").read_text(encoding="utf-8")
        self.assertNotIn(self.CAPABILITY, raw)
        self.assertNotIn("topsecretvalue", raw)
        self.assertNotIn(self.USER, raw)
        self.assertNotIn(_local_path(self.USER), raw)
        self.assertIn("<REDACTED:orca_dispatch_capability>", raw)
        self.assertIn("<REDACTED:env_secret_pattern>", raw)
        self.assertIn("<REDACTED:absolute_local_path>", raw)

    def test_every_injection_route_is_redacted_field_by_field(self) -> None:
        """One assertion per route the correction names, so a partial fix fails."""
        record = self.record_json(self.write_poisoned_record())

        routes = {
            "reviewer_agent_command": record["reviewer_agent_command"],
            "reviewer_agent_origin": record["reviewer_agent_origin"],
            "failure_detail": record["failure_detail"],
            "notes": record["notes"],
            "stored_task_spec.capture_error": record["stored_task_spec"][
                "capture_error"
            ],
            "delivery_evidence.process_incarnation": record["delivery_evidence"][
                "process_incarnation"
            ],
            "delivery_evidence.last_failure": record["delivery_evidence"][
                "last_failure"
            ],
            "delivery_evidence.termination_reason": record["delivery_evidence"][
                "termination_reason"
            ],
        }
        for field, value in routes.items():
            with self.subTest(field=field):
                self.assertNotIn(self.CAPABILITY, value)
                self.assertNotIn("topsecretvalue", value)
                self.assertNotIn(self.USER, value)
                self.assertIn("<REDACTED:absolute_local_path>", value)

    def test_the_identities_the_record_exists_to_prove_are_not_redacted(self) -> None:
        """The other half of secret-safe: over-redaction destroys the evidence."""
        record = self.record_json(self.write_poisoned_record())

        self.assertEqual(record["reviewer_terminal"], "term_reviewer")
        self.assertEqual(record["task_id"], "task_aaa")
        self.assertEqual(record["dispatch_id"], "ctx_bbb")
        self.assertEqual(record["dispatch_key"], "attempt1__task_aaa__ctx_bbb")
        delivery = record["delivery_evidence"]
        self.assertEqual(delivery["assignee_handle"], "term_assignee")
        self.assertEqual(delivery["capability_hash"], "sha256:0123456789abcdef")
        self.assertEqual(delivery["failure_count"], 2)
        self.assertEqual(record["provenance_state"], "voided")
        self.assertEqual(record["void_reason"], "settlement_failure")

    def test_the_record_states_what_was_covered_and_what_matched(self) -> None:
        record = self.record_json(self.write_poisoned_record())

        block = record["metadata_redaction"]
        self.assertEqual(
            block["redaction_policy_version"],
            run_logging.FINAL_REVIEW_REDACTION_POLICY_VERSION,
        )
        self.assertEqual(
            block["covered_fields"],
            list(run_logging.FINAL_REVIEW_REDACTED_METADATA_FIELDS),
        )
        # C.4's shape: policy order, an entry only for a category that matched, no
        # offset and no removed value anywhere in the block.
        self.assertEqual(
            [entry["category"] for entry in block["redactions"]],
            ["orca_dispatch_capability", "env_secret_pattern", "absolute_local_path"],
        )
        for entry in block["redactions"]:
            self.assertEqual(sorted(entry), ["category", "count"])
            self.assertGreater(entry["count"], 0)

    def test_a_clean_record_records_no_substitution(self) -> None:
        """`[]` unambiguously means nothing was substituted."""
        record = self.record_json(self.write_record(notes="nothing to hide"))

        self.assertEqual(record["metadata_redaction"]["redactions"], [])
        self.assertEqual(record["notes"], "nothing to hide")

    def test_the_report_capture_error_route_is_covered(self) -> None:
        """Exercised directly: this error is built from an already-relative path,
        so the guarantee is the choke point, not the one message it usually holds."""
        record = {"report": {"capture_error": self.poisoned("unreadable")}}

        counts = run_logging._redact_record_metadata(record)

        value = record["report"]["capture_error"]
        self.assertNotIn(self.CAPABILITY, value)
        self.assertNotIn(self.USER, value)
        self.assertEqual(
            [entry["category"] for entry in counts],
            ["orca_dispatch_capability", "env_secret_pattern", "absolute_local_path"],
        )

    def test_the_choke_point_skips_absent_and_non_string_values(self) -> None:
        """The record's shape is decided by its writer, never by the redactor."""
        record = {"notes": None, "delivery_evidence": None, "failure_detail": ""}

        self.assertEqual(run_logging._redact_record_metadata(record), [])
        self.assertEqual(record, {"notes": None, "delivery_evidence": None,
                                  "failure_detail": ""})

    # I-002-R1. The report is the one input NOBODY on the writer side controls, and
    # its parse output lands in the record. Each route below injects a capability
    # token, an env-secret value and a `/Users/<name>/` path through the report and
    # asserts against the bytes that reach disk.

    def assert_record_bytes_are_clean(self, published) -> None:
        raw = (published / "record.json").read_text(encoding="utf-8")
        self.assertNotIn(self.CAPABILITY, raw)
        self.assertNotIn("topsecretvalue", raw)
        self.assertNotIn(self.USER, raw)
        self.assertNotIn(_local_path(self.USER), raw)

    def test_a_malformed_result_never_reaches_the_record_as_raw_text(self) -> None:
        published = self.write_poisoned_record(
            report=f"RESULT: {self.poisoned('nonsense')}\n"
        )

        parsed = self.record_json(published)["report"]["parsed"]
        # The invalid capture is not the field's value; the field stays in its set.
        self.assertEqual(parsed["result"], run_logging.PARSED_ENUM_INVALID)
        self.assertEqual(parsed["parse_status"], "malformed")
        # It is still said, once, in the field that exists to say it -- redacted.
        self.assertIn("RESULT:", parsed["parse_error"])
        self.assertIn("<REDACTED:orca_dispatch_capability>", parsed["parse_error"])
        self.assertIn("<REDACTED:env_secret_pattern>", parsed["parse_error"])
        self.assertIn("<REDACTED:absolute_local_path>", parsed["parse_error"])
        self.assert_record_bytes_are_clean(published)

    def test_a_malformed_review_verdict_never_reaches_the_record_raw(self) -> None:
        published = self.write_poisoned_record(
            report=f"RESULT: PASS\nREVIEW_VERDICT: {self.poisoned('nonsense')}\n"
        )

        parsed = self.record_json(published)["report"]["parsed"]
        self.assertEqual(parsed["result"], "PASS")
        self.assertEqual(parsed["review_verdict"], run_logging.PARSED_ENUM_INVALID)
        self.assertEqual(parsed["parse_status"], "malformed")
        self.assertIn("REVIEW_VERDICT:", parsed["parse_error"])
        self.assertIn("<REDACTED:orca_dispatch_capability>", parsed["parse_error"])
        self.assert_record_bytes_are_clean(published)

    def test_report_controlled_finding_ids_are_constrained_and_redacted(self) -> None:
        published = self.write_poisoned_record()

        parsed = self.record_json(published)["report"]["parsed"]
        self.assertEqual(parsed["parse_status"], "ok")
        # ID-shaped but a credential: the shape check passes it, redaction does not.
        blocking = parsed["blocking_finding_ids"]
        self.assertEqual(len(blocking), 1)
        self.assertNotIn(self.CAPABILITY, blocking[0])
        self.assertIn("<REDACTED:", blocking[0])
        # Not ID-shaped at all: replaced outright, and the count stays honest.
        self.assertEqual(
            parsed["non_blocking_finding_ids"],
            [run_logging.PARSED_FINDING_ID_INVALID] * 2,
        )
        self.assert_record_bytes_are_clean(published)

    def test_a_well_formed_report_keeps_its_ids_and_its_enums(self) -> None:
        """The other half again: constraining must not destroy real evidence."""
        published = self.write_poisoned_record(report=FAILING_REPORT)

        parsed = self.record_json(published)["report"]["parsed"]
        self.assertEqual(parsed["parse_status"], "ok")
        self.assertEqual(parsed["parse_error"], "")
        self.assertEqual(parsed["result"], "FAIL")
        self.assertEqual(parsed["review_verdict"], "FAIL")
        self.assertEqual(parsed["blocking_finding_ids"], ["R1"])
        self.assertEqual(parsed["non_blocking_finding_ids"], ["R2"])

    def test_no_free_form_string_field_escapes_the_covered_list(self) -> None:
        """The durable guard: a new string field added to the record must be either
        declared free-form (and redacted) or declared an identity here."""
        record = self.record_json(self.write_poisoned_record())

        def leaves(node, prefix=""):
            if isinstance(node, dict):
                for name, value in node.items():
                    yield from leaves(value, f"{prefix}{name}.")
            elif isinstance(node, list):
                for value in node:
                    yield from leaves(value, f"{prefix}[].")
            elif isinstance(node, str):
                yield prefix[:-1]

        # Identities, enums, constants, digests, timestamps and paths that are
        # relativized-or-redacted at construction. Anything NOT here must be covered.
        exempt = {
            "schema_version", "record_kind", "run_id", "task_id", "dispatch_id",
            "dispatch_key", "recorded_at", "reviewer_terminal", "provenance_state",
            "void_reason", "settlement_state", "input_altered_across_retry",
            "stored_task_spec.source", "stored_task_spec.capture_status",
            "stored_task_spec.captured_at",
            "stored_task_spec.redaction_policy_version",
            "stored_task_spec.artifact_path",
            "delivery_evidence.source", "delivery_evidence.capture_status",
            "delivery_evidence.captured_at", "delivery_evidence.dispatch_status",
            "delivery_evidence.contract_version", "delivery_evidence.capability_hash",
            "delivery_evidence.assignee_handle",
            "report.contract_path", "report.resolution", "report.capture_status",
            "report.captured_at", "report.redaction_policy_version",
            "report.artifact_path",
            "report.report_digest_pre_redaction",
            "report.artifact_digest_post_redaction",
            "report.redactions.[].category",
            "report.parsed.parse_status",
            "metadata_redaction.redaction_policy_version",
            "metadata_redaction.covered_fields.[]",
            "metadata_redaction.redactions.[].category",
        }
        # I-002-R1. Parser output is NOT exempt by being called an enum: a field is
        # exempt here only if the writer constrains it to a closed set BEFORE it is
        # persisted, and that claim is proved against the bytes on disk rather than
        # asserted by listing the field's name. `parse_error` and the finding-id
        # lists are constrained by nothing, so they are covered instead.
        constrained = {
            "report.parsed.result": {
                "", run_logging.PARSED_ENUM_INVALID, *run_logging.RESULT_VALUES,
            },
            "report.parsed.review_verdict": {
                "",
                run_logging.PARSED_ENUM_INVALID,
                *run_logging.REVIEW_VERDICT_VALUES,
            },
        }
        covered = set(run_logging.FINAL_REVIEW_REDACTED_METADATA_FIELDS)

        for field, allowed in constrained.items():
            with self.subTest(field=field):
                leaf = field.rpartition(".")[2]
                self.assertIn(record["report"]["parsed"][leaf], allowed)

        for field in sorted(set(leaves(record))):
            with self.subTest(field=field):
                # A list of strings is covered by its containing field.
                known = covered | exempt | set(constrained)
                self.assertTrue(
                    field in known or field.removesuffix(".[]") in known,
                    f"{field} is neither covered nor declared constrained/identity",
                )


class AuditCaptureFailureTests(_AuditTestCase):
    """A record that says "the input could not be captured, here is why" is evidence.
    A missing record is not."""

    def test_an_unavailable_capture_still_writes_the_record(self) -> None:
        with patch(
            "scripts.run_logging.capture_stored_task_spec",
            return_value=(None, "orca was not found on PATH"),
        ), patch(
            "scripts.run_logging.capture_delivery_evidence",
            return_value=(None, "orca was not found on PATH"),
        ), patch(
            "scripts.run_logging._capture_repository_state", return_value=None
        ):
            published = self.write_record(
                capture=True,
                provenance_state="voided",
                void_reason="dispatch_input_rejected",
                settlement_state="not_settled",
                failure_detail="agent_prompt_blocked",
                observed_input_bytes=14805,
            )

        record = self.record_json(published)
        self.assertEqual(record["stored_task_spec"]["capture_status"], "unavailable")
        self.assertIn("PATH", record["stored_task_spec"]["capture_error"])
        self.assertIsNone(record["stored_task_spec"]["input_digest_pre_redaction"])
        self.assertEqual(record["stored_task_spec"]["artifact_path"], "")
        self.assertEqual(record["observed_input_bytes"], 14805)
        self.assertEqual(record["failure_detail"], "agent_prompt_blocked")
        self.assertTrue(
            any("final_review_audit_incomplete" in row for row in self.log_rows())
        )

    def test_a_captured_spec_is_redacted_before_it_reaches_disk(self) -> None:
        spec = f"spec with dcap_AAAAAAAAAAAAAAAA and {_local_path('someone', 'work')}"
        with patch(
            "scripts.run_logging.capture_stored_task_spec", return_value=(spec, "")
        ), patch(
            "scripts.run_logging.capture_delivery_evidence",
            return_value=({"status": "failed", "failure_count": 1}, ""),
        ), patch(
            "scripts.run_logging._capture_repository_state", return_value=None
        ):
            published = self.write_record(capture=True)

        retained = (published / "input.md").read_text(encoding="utf-8")
        self.assertNotIn("dcap_AAAAAAAAAAAAAAAA", retained)
        self.assertNotIn("someone", retained)
        record = self.record_json(published)
        stored = record["stored_task_spec"]
        self.assertEqual(stored["capture_status"], "captured")
        self.assertEqual(
            stored["input_digest_pre_redaction"], run_logging.sha256_text(spec)
        )
        self.assertEqual(
            stored["artifact_path"],
            "final_review_audit/attempt1__task_aaa__ctx_bbb/input.md",
        )
        self.assertEqual(record["delivery_evidence"]["dispatch_status"], "failed")
        self.assertEqual(record["delivery_evidence"]["capture_status"], "captured")

    def test_a_missing_report_is_recorded_as_absent(self) -> None:
        record = self.record_json(
            self.write_record(
                provenance_state="voided",
                void_reason="report_missing",
                settlement_state="settled",
            )
        )

        self.assertEqual(record["report"]["capture_status"], "absent")
        self.assertEqual(record["report"]["parsed"]["parse_status"], "not_attempted")
        self.assertEqual(record["void_reason"], "report_missing")

    def test_a_malformed_report_is_still_snapshotted(self) -> None:
        self.write_report("this report has no RESULT line at all\n")

        published = self.write_record(
            provenance_state="voided",
            void_reason="report_malformed",
            settlement_state="settled",
        )

        record = self.record_json(published)
        self.assertEqual(record["report"]["capture_status"], "captured")
        self.assertEqual(record["report"]["parsed"]["parse_status"], "malformed")
        self.assertIn(
            "this report has no RESULT line",
            (published / "report.md").read_text(encoding="utf-8"),
        )


class EvidenceBundleTests(_AuditTestCase):
    """D-F: self-contained, digest-checked, and honest about what is missing."""

    def test_the_bundle_inlines_the_minimum_evidence_subset(self) -> None:
        self.write_report(FAILING_REPORT)
        self.write_record(provenance_state="accepted", settlement_state="settled")

        path = run_logging.export_final_review_evidence(self.RUN_ID, base=self.base)

        bundle = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(path.name, "FINAL_REVIEW_EVIDENCE_BUNDLE.json")
        self.assertEqual(bundle["bundle_kind"], "final_review_evidence_bundle")
        self.assertEqual(
            bundle["component_versions"]["audit_schema"],
            run_logging.FINAL_REVIEW_AUDIT_SCHEMA_VERSION,
        )
        self.assertIn(
            "final_review_audit", bundle["orchestrator_log"]["content"]
        )
        dispatch = bundle["attempts"][0]["dispatches"][0]
        self.assertEqual(dispatch["record_status"], "ok")
        self.assertIn("RESULT: FAIL", dispatch["report"]["content"])
        self.assertTrue(dispatch["report"]["digest_verified"])
        self.assertEqual(
            bundle["attempts"][0]["accepted_dispatch_key"], dispatch["dispatch_key"]
        )

    def test_a_digest_mismatch_is_reported_and_the_bundle_still_ships(self) -> None:
        self.write_report(FAILING_REPORT)
        published = self.write_record(provenance_state="accepted",
                                      settlement_state="settled")
        (published / "report.md").write_text("tampered", encoding="utf-8")

        bundle = json.loads(
            run_logging.export_final_review_evidence(
                self.RUN_ID, base=self.base
            ).read_text(encoding="utf-8")
        )

        self.assertEqual(len(bundle["integrity"]["digest_mismatches"]), 1)
        self.assertFalse(
            bundle["attempts"][0]["dispatches"][0]["report"]["digest_verified"]
        )
        self.assertIn("tampered", bundle["attempts"][0]["dispatches"][0]["report"]["content"])

    def test_incomplete_publications_are_carried_not_hidden(self) -> None:
        staging = self.root / "final_review_audit" / ".staging"
        orphan = staging / "attempt1__task_gone__ctx_gone.7-8"
        orphan.mkdir(parents=True)
        (orphan / "input.md").write_text("partial", encoding="utf-8")

        bundle = json.loads(
            run_logging.export_final_review_evidence(
                self.RUN_ID, base=self.base
            ).read_text(encoding="utf-8")
        )

        self.assertEqual(bundle["integrity"]["records_found"], 0)
        self.assertEqual(
            bundle["integrity"]["incomplete_publications"][0]["dispatch_key"],
            "attempt1__task_gone__ctx_gone",
        )

    def test_two_exports_of_the_same_run_differ_only_in_exported_at(self) -> None:
        self.write_report(PASSING_REPORT)
        self.write_record(provenance_state="accepted", settlement_state="settled")

        first = json.loads(
            run_logging.export_final_review_evidence(
                self.RUN_ID, base=self.base, out=self.base / "a.json"
            ).read_text(encoding="utf-8")
        )
        second = json.loads(
            run_logging.export_final_review_evidence(
                self.RUN_ID, base=self.base, out=self.base / "b.json"
            ).read_text(encoding="utf-8")
        )

        first.pop("exported_at")
        second.pop("exported_at")
        self.assertEqual(first, second)

    def test_this_module_runs_no_write_side_git_command(self) -> None:
        """No IMPLEMENTATION work item may add an automatic `git add`, commit or
        push of run artifacts, in this command or anywhere else. Asserted over the
        parsed argv literals, not over the source text -- the prose that promises it
        also contains the words."""
        module = ast.parse(
            (REPO_ROOT / "scripts" / "run_logging.py").read_text(encoding="utf-8")
        )
        read_only = {"rev-parse", "status", "--porcelain", "HEAD", "--abbrev-ref"}
        seen = 0
        for node in ast.walk(module):
            if not isinstance(node, ast.List) or not node.elts:
                continue
            first = node.elts[0]
            if not (isinstance(first, ast.Constant) and first.value == "git"):
                continue
            seen += 1
            for element in node.elts[1:]:
                if isinstance(element, ast.Starred):
                    continue
                self.assertIsInstance(element, ast.Constant)
                self.assertIn(element.value, read_only)
        self.assertTrue(seen, "the git argv literals were not found at all")


class AuditCliTests(_AuditTestCase):
    """I-5: the three subcommands a live Coordinator actually calls."""

    def run_cli(self, argv: list[str]) -> str:
        buffer = StringIO()
        with redirect_stdout(buffer):
            code = cli_main(argv)
        self.assertEqual(code, 0)
        return buffer.getvalue()

    def test_the_write_subcommand_publishes_a_record(self) -> None:
        self.write_report(PASSING_REPORT)

        output = self.run_cli(
            [
                "final-review-audit-write",
                "--run-id", self.RUN_ID,
                "--base", str(self.base),
                "--attempt", "1",
                "--task-id", "task_cli",
                "--dispatch-id", "ctx_cli",
                "--provenance", "accepted",
                "--settlement", "settled",
                "--no-capture",
            ]
        )

        published = self.root / "final_review_audit" / "attempt1__task_cli__ctx_cli"
        self.assertTrue(published.is_dir())
        self.assertIn("attempt1__task_cli__ctx_cli", output)
        self.assertEqual(self.record_json(published)["provenance_state"], "accepted")

    def test_the_write_subcommand_defaults_to_unknown_provenance(self) -> None:
        self.run_cli(
            [
                "final-review-audit-write",
                "--run-id", self.RUN_ID,
                "--base", str(self.base),
                "--attempt", "1",
                "--task-id", "task_cli",
                "--no-capture",
            ]
        )

        published = (
            self.root / "final_review_audit" / "attempt1__task_cli__nodispatch"
        )
        self.assertEqual(self.record_json(published)["provenance_state"], "unknown")

    def test_the_provenance_subcommand_prints_the_reader_result(self) -> None:
        self.write_record(provenance_state="accepted", settlement_state="settled")

        output = self.run_cli(
            [
                "final-review-audit-provenance",
                "--run-id", self.RUN_ID,
                "--base", str(self.base),
                "--attempt", "1",
            ]
        )

        parsed = json.loads(output)
        self.assertEqual(
            parsed["accepted_dispatch_key"], "attempt1__task_aaa__ctx_bbb"
        )
        self.assertEqual(parsed["violations"], [])

    def test_the_export_subcommand_writes_the_bundle(self) -> None:
        self.write_report(PASSING_REPORT)
        self.write_record(provenance_state="accepted", settlement_state="settled")

        self.run_cli(
            [
                "final-review-audit-export",
                "--run-id", self.RUN_ID,
                "--base", str(self.base),
            ]
        )

        self.assertTrue((self.root / "FINAL_REVIEW_EVIDENCE_BUNDLE.json").is_file())

    def test_no_cli_surface_can_ask_for_accepted_by_default(self) -> None:
        parser = run_logging._build_parser()
        actions = {
            action.dest: action
            for action in parser._subparsers._group_actions[0]
            .choices["final-review-audit-write"]
            ._actions
        }
        self.assertEqual(actions["provenance"].default, "unknown")
        self.assertEqual(tuple(actions["provenance"].choices), run_logging.PROVENANCE_STATES)


class ProvenanceLadderTests(_AuditTestCase):
    """D-B B.2 evaluated once, in one place, so two emission points cannot disagree."""

    def test_the_ladder_takes_the_earliest_matching_cause(self) -> None:
        """Two causes at once resolve to the FIRST -- and the second survives
        verbatim in failure_detail, which is why provenance is two fields and not
        one flat enum."""
        self.assertEqual(
            run_logging.resolve_final_review_provenance(
                input_rejected=True, capability_invalid=True
            ),
            ("voided", "dispatch_input_rejected", "not_settled"),
        )

    def test_every_row_of_the_ladder(self) -> None:
        cases = (
            ({"input_rejected": True}, ("voided", "dispatch_input_rejected", "not_settled")),
            ({"capability_invalid": True}, ("voided", "dispatch_capability_invalid", "not_settled")),
            ({"settled": False}, ("voided", "settlement_failure", "not_settled")),
            (
                {"settled": True, "report_capture_status": "absent"},
                ("voided", "report_missing", "settled"),
            ),
            (
                {"settled": True, "report_capture_status": "unreadable"},
                ("voided", "report_missing", "settled"),
            ),
            (
                {
                    "settled": True,
                    "report_capture_status": "captured",
                    "report_parse_status": "malformed",
                },
                ("voided", "report_malformed", "settled"),
            ),
            (
                {
                    "settled": True,
                    "report_capture_status": "captured",
                    "report_parse_status": "ok",
                    "superseded_by_retry": True,
                },
                ("voided", "superseded_by_retry", "settled"),
            ),
            (
                {
                    "settled": True,
                    "report_capture_status": "captured",
                    "report_parse_status": "ok",
                },
                ("accepted", "", "settled"),
            ),
            ({"determinable": False}, ("unknown", "", "unknown")),
        )
        for kwargs, expected in cases:
            with self.subTest(**kwargs):
                self.assertEqual(
                    run_logging.resolve_final_review_provenance(**kwargs), expected
                )

    def test_the_ladder_never_yields_accepted_without_a_usable_report(self) -> None:
        for capture in ("absent", "unreadable"):
            for parse in ("not_attempted", "malformed", "ok"):
                with self.subTest(capture=capture, parse=parse):
                    state, _reason, _settlement = (
                        run_logging.resolve_final_review_provenance(
                            settled=True,
                            report_capture_status=capture,
                            report_parse_status=parse,
                        )
                    )
                    self.assertNotEqual(state, "accepted")

    def test_every_void_reason_the_ladder_emits_is_in_the_enum(self) -> None:
        for kwargs in (
            {"input_rejected": True},
            {"capability_invalid": True},
            {"settled": False},
            {"settled": True},
            {"settled": True, "report_capture_status": "captured",
             "report_parse_status": "malformed"},
            {"settled": True, "report_capture_status": "captured",
             "report_parse_status": "ok", "superseded_by_retry": True},
        ):
            with self.subTest(**kwargs):
                _state, reason, _settlement = (
                    run_logging.resolve_final_review_provenance(**kwargs)
                )
                self.assertIn(reason, run_logging.VOID_REASONS)

    def test_probe_reports_an_absent_report_without_writing_anything(self) -> None:
        self.assertEqual(
            run_logging.probe_final_review_report(self.RUN_ID, 1, base=self.base),
            ("absent", "not_attempted"),
        )
        self.assertFalse((self.root / "final_review_audit").exists())

    def test_probe_reads_the_laddered_path_and_parses_it(self) -> None:
        self.write_report(PASSING_REPORT, attempt=1)
        self.write_report("garbage\n", attempt=2)

        self.assertEqual(
            run_logging.probe_final_review_report(self.RUN_ID, 1, base=self.base),
            ("captured", "ok"),
        )
        self.assertEqual(
            run_logging.probe_final_review_report(self.RUN_ID, 2, base=self.base),
            ("captured", "malformed"),
        )


if __name__ == "__main__":
    unittest.main()
