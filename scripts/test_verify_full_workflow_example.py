#!/usr/bin/env python3
"""Tests for the full-workflow best-practice example verifier."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts.run_logging import log_orchestrator_event
from scripts.verify_full_workflow_example import (
    ExampleVerificationError,
    PHASES,
    verify_run,
)


PASS_REPORT = "# Review Result\n\nRESULT: PASS\nREVIEW_VERDICT: PASS\n"
FAIL_REPORT = "# Review Result\n\nRESULT: FAIL\nREVIEW_VERDICT: FAIL\n"


class FullWorkflowExampleVerifierTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary_directory.name)
        self.run_id = "run_example"
        self.run_dir = self.base / "artifacts" / "runs" / self.run_id
        self.run_dir.mkdir(parents=True)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def write_successful_example(self, *, include_design_fail: bool = True) -> None:
        for phase in PHASES:
            (self.run_dir / f"{phase.upper()}.md").write_text(
                f"# {phase.upper()} Worker Result\n", encoding="utf-8"
            )
            first_result = (
                FAIL_REPORT if phase == "design" and include_design_fail else PASS_REPORT
            )
            (self.run_dir / f"REVIEW_{phase.upper()}.md").write_text(
                first_result, encoding="utf-8"
            )
            if phase == "design" and include_design_fail:
                (self.run_dir / "REVIEW_DESIGN_iteration2.md").write_text(
                    PASS_REPORT, encoding="utf-8"
                )

        (self.run_dir / "FINAL_REVIEW.md").write_text(PASS_REPORT, encoding="utf-8")
        (self.run_dir / "TIMING_LOG.md").write_text(
            "| timestamp | event |\n| --- | --- |\n", encoding="utf-8"
        )

        log_orchestrator_event(
            self.run_id,
            base=self.base,
            event="run_start",
            risk="medium",
            risk_source="explicit",
            requested_phases=",".join(PHASES),
        )
        for phase in PHASES:
            if phase == "design" and include_design_fail:
                log_orchestrator_event(
                    self.run_id,
                    base=self.base,
                    event="dispatch_settled",
                    phase=phase,
                    role="reviewer",
                    iteration=1,
                    gate_result="FAIL",
                    review_verdict="FAIL",
                    risk="medium",
                    round_kind="phase_gate",
                    detail="seeded requirement contradiction | verified",
                )
                iteration = 2
                round_kind = "correction"
            else:
                iteration = 1
                round_kind = "phase_gate"
            log_orchestrator_event(
                self.run_id,
                base=self.base,
                event="dispatch_settled",
                phase=phase,
                role="reviewer",
                iteration=iteration,
                gate_result="PASS",
                review_verdict="PASS",
                risk="medium",
                round_kind=round_kind,
            )
        log_orchestrator_event(
            self.run_id,
            base=self.base,
            event="dispatch_settled",
            phase="final_review",
            role="reviewer",
            iteration=1,
            gate_result="PASS",
            review_verdict="PASS",
            risk="medium",
            round_kind="final_review",
        )
        log_orchestrator_event(
            self.run_id,
            base=self.base,
            event="run_end",
            risk="medium",
            risk_source="explicit",
            result="COMPLETED",
        )

    def test_accepts_the_documented_fail_correction_pass_lifecycle(self) -> None:
        self.write_successful_example()

        summary = verify_run(self.run_dir)

        self.assertEqual(summary.design_fail_iteration, 1)
        self.assertEqual(summary.design_pass_iteration, 2)
        self.assertEqual(summary.final_review_iteration, 1)

    def test_rejects_a_clean_first_pass_run_that_did_not_exercise_correction(self) -> None:
        self.write_successful_example(include_design_fail=False)

        with self.assertRaisesRegex(ExampleVerificationError, "DESIGN has no Reviewer FAIL"):
            verify_run(self.run_dir)

    def test_rejects_a_report_only_claim_without_log_provenance(self) -> None:
        self.write_successful_example()
        log_path = self.run_dir / "ORCHESTRATOR_LOG.md"
        text = log_path.read_text(encoding="utf-8")
        log_path.write_text(
            text.replace("| FAIL | FAIL |", "| PASS | PASS |", 1),
            encoding="utf-8",
        )

        with self.assertRaisesRegex(
            ExampleVerificationError,
            "ORCHESTRATOR_LOG.md has no settled DESIGN Reviewer FAIL",
        ):
            verify_run(self.run_dir)

    def test_rejects_an_inconsistent_report_verdict(self) -> None:
        self.write_successful_example()
        report = self.run_dir / "REVIEW_DESIGN.md"
        report.write_text(
            "# Review Result\n\nRESULT: FAIL\nREVIEW_VERDICT: PASS\n",
            encoding="utf-8",
        )

        with self.assertRaisesRegex(
            ExampleVerificationError,
            r"inconsistent RESULT \(FAIL\) and REVIEW_VERDICT \(PASS\)",
        ):
            verify_run(self.run_dir)


if __name__ == "__main__":
    unittest.main()
