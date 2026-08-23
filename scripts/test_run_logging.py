#!/usr/bin/env python3
"""Tests for scripts/run_logging.py: the ORCHESTRATOR_LOG.md/TIMING_LOG.md writer."""

from __future__ import annotations

import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from scripts.run_logging import (
    ORCHESTRATOR_LOG_COLUMNS,
    ORCHESTRATOR_LOG_FILENAME,
    RUN_STATUS_VALUES,
    TIMING_LOG_COLUMNS,
    TIMING_LOG_FILENAME,
    RunLoggingError,
    elapsed_seconds,
    log_orchestrator_event,
    log_run_status,
    log_timing_event,
    main as cli_main,
    orchestrator_log_path,
    timing_log_path,
)


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


if __name__ == "__main__":
    unittest.main()
