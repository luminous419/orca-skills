#!/usr/bin/env python3
"""Verify the documented five-phase FAIL -> correction -> PASS example run."""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path


PHASES = ("analysis", "plan", "design", "implementation", "test")
RESULT_PATTERN = re.compile(r"^RESULT:\s*(PASS|FAIL)\s*$", re.MULTILINE)
REVIEW_VERDICT_PATTERN = re.compile(
    r"^REVIEW_VERDICT:\s*(PASS|PASS WITH NOTES|FAIL|BLOCKED)\s*$", re.MULTILINE
)
ITERATION_PATTERN = re.compile(r"_iteration([1-9][0-9]*)\.md$")


class ExampleVerificationError(ValueError):
    """Raised when a run does not demonstrate the documented lifecycle."""


@dataclass(frozen=True)
class VerificationSummary:
    run_dir: Path
    design_fail_iteration: int
    design_pass_iteration: int
    final_review_iteration: int


def _report_iteration(path: Path) -> int:
    match = ITERATION_PATTERN.search(path.name)
    return int(match.group(1)) if match else 1


def _result(path: Path) -> str:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ExampleVerificationError(f"cannot read {path}: {exc}") from exc
    matches = RESULT_PATTERN.findall(text)
    if len(matches) != 1:
        raise ExampleVerificationError(
            f"{path.name} must contain exactly one RESULT: PASS|FAIL line"
        )
    verdicts = REVIEW_VERDICT_PATTERN.findall(text)
    if len(verdicts) != 1:
        raise ExampleVerificationError(
            f"{path.name} must contain exactly one valid REVIEW_VERDICT line"
        )
    result = matches[0]
    verdict = verdicts[0]
    if verdict != result:
        raise ExampleVerificationError(
            f"{path.name} has inconsistent RESULT ({result}) and "
            f"REVIEW_VERDICT ({verdict})"
        )
    return result


def _review_reports(run_dir: Path, phase: str) -> list[Path]:
    prefix = f"REVIEW_{phase.upper()}"
    reports = [
        path
        for path in run_dir.glob(f"{prefix}*.md")
        if path.name == f"{prefix}.md" or ITERATION_PATTERN.search(path.name)
    ]
    return sorted(reports, key=_report_iteration)


def _final_reports(run_dir: Path) -> list[Path]:
    reports = [
        path
        for path in run_dir.glob("FINAL_REVIEW*.md")
        if path.name == "FINAL_REVIEW.md" or ITERATION_PATTERN.search(path.name)
    ]
    return sorted(reports, key=_report_iteration)


def _split_markdown_row(line: str) -> list[str]:
    content = line.strip()
    if not content.startswith("|") or not content.endswith("|"):
        return []
    content = content[1:-1]
    cells = re.split(r"(?<!\\)\|", content)
    return [cell.strip().replace(r"\|", "|") for cell in cells]


def _orchestrator_rows(path: Path) -> list[dict[str, str]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ExampleVerificationError(f"cannot read {path}: {exc}") from exc
    if len(lines) < 2:
        raise ExampleVerificationError("ORCHESTRATOR_LOG.md has no table rows")
    header = _split_markdown_row(lines[0])
    required = {
        "event",
        "phase",
        "role",
        "iteration",
        "gate_result",
        "review_verdict",
        "risk",
        "requested_phases",
        "round_kind",
        "result",
    }
    if not required.issubset(header):
        missing = ", ".join(sorted(required - set(header)))
        raise ExampleVerificationError(
            f"ORCHESTRATOR_LOG.md is missing required columns: {missing}"
        )
    rows: list[dict[str, str]] = []
    for line in lines[2:]:
        cells = _split_markdown_row(line)
        if len(cells) == len(header):
            rows.append(dict(zip(header, cells)))
    if not rows:
        raise ExampleVerificationError("ORCHESTRATOR_LOG.md has no parseable events")
    return rows


def verify_run(run_dir: Path) -> VerificationSummary:
    run_dir = run_dir.resolve()
    if not run_dir.is_dir():
        raise ExampleVerificationError(f"run directory does not exist: {run_dir}")

    missing_workers = [
        f"{phase.upper()}.md"
        for phase in PHASES
        if not (run_dir / f"{phase.upper()}.md").is_file()
    ]
    if missing_workers:
        raise ExampleVerificationError(
            "missing Worker artifacts: " + ", ".join(missing_workers)
        )

    phase_results: dict[str, list[tuple[int, str]]] = {}
    for phase in PHASES:
        reports = _review_reports(run_dir, phase)
        if not reports:
            raise ExampleVerificationError(
                f"no phase Reviewer artifact found for {phase.upper()}"
            )
        outcomes = [(_report_iteration(path), _result(path)) for path in reports]
        phase_results[phase] = outcomes
        if not any(result == "PASS" for _, result in outcomes):
            raise ExampleVerificationError(
                f"{phase.upper()} never reached a Reviewer PASS"
            )

    design_failures = [
        iteration
        for iteration, result in phase_results["design"]
        if result == "FAIL"
    ]
    design_passes = [
        iteration
        for iteration, result in phase_results["design"]
        if result == "PASS"
    ]
    if not design_failures:
        raise ExampleVerificationError("DESIGN has no Reviewer FAIL")
    first_fail = min(design_failures)
    later_passes = [iteration for iteration in design_passes if iteration > first_fail]
    if not later_passes:
        raise ExampleVerificationError(
            "DESIGN has no later Reviewer PASS after its FAIL"
        )
    first_later_pass = min(later_passes)

    final_reports = _final_reports(run_dir)
    if not final_reports:
        raise ExampleVerificationError("no Final Adversarial Review artifact found")
    final_report = final_reports[-1]
    if _result(final_report) != "PASS":
        raise ExampleVerificationError("the last Final Adversarial Review is not PASS")

    timing_log = run_dir / "TIMING_LOG.md"
    if not timing_log.is_file():
        raise ExampleVerificationError("TIMING_LOG.md is missing")

    rows = _orchestrator_rows(run_dir / "ORCHESTRATOR_LOG.md")
    settled_reviews = [
        row
        for row in rows
        if row["event"] == "dispatch_settled" and row["role"].lower() == "reviewer"
    ]
    pass_positions: dict[str, int] = {}
    for phase in PHASES:
        matching_positions = [
            index
            for index, row in enumerate(settled_reviews)
            if row["phase"].lower() == phase
            and row["gate_result"] == "PASS"
            and row["review_verdict"] == "PASS"
        ]
        if not matching_positions:
            raise ExampleVerificationError(
                f"ORCHESTRATOR_LOG.md has no settled Reviewer PASS for {phase.upper()}"
            )
        pass_positions[phase] = matching_positions[0]

    ordered_pass_positions = [pass_positions[phase] for phase in PHASES]
    if ordered_pass_positions != sorted(ordered_pass_positions):
        raise ExampleVerificationError(
            "ORCHESTRATOR_LOG.md does not record phase-gate PASS events in "
            "ANALYSIS -> PLAN -> DESIGN -> IMPLEMENTATION -> TEST order"
        )

    design_rows = [
        row for row in settled_reviews if row["phase"].lower() == "design"
    ]
    fail_positions = [
        index
        for index, row in enumerate(design_rows)
        if row["gate_result"] == "FAIL" and row["review_verdict"] == "FAIL"
    ]
    if not fail_positions:
        raise ExampleVerificationError(
            "ORCHESTRATOR_LOG.md has no settled DESIGN Reviewer FAIL"
        )
    first_fail_position = fail_positions[0]
    if not any(
        row["gate_result"] == "PASS"
        and row["review_verdict"] == "PASS"
        and row["round_kind"] == "correction"
        for row in design_rows[first_fail_position + 1 :]
    ):
        raise ExampleVerificationError(
            "ORCHESTRATOR_LOG.md has no DESIGN correction PASS after FAIL"
        )

    final_positions = [
        index
        for index, row in enumerate(settled_reviews)
        if row["phase"].lower() == "final_review"
        and row["gate_result"] == "PASS"
        and row["review_verdict"] == "PASS"
    ]
    if not final_positions:
        raise ExampleVerificationError(
            "ORCHESTRATOR_LOG.md has no settled Final Reviewer PASS"
        )
    if final_positions[-1] <= pass_positions["test"]:
        raise ExampleVerificationError(
            "ORCHESTRATOR_LOG.md records Final Review before the TEST gate"
        )

    run_starts = [row for row in rows if row["event"] == "run_start"]
    expected_phases = ",".join(PHASES)
    if not any(
        row["risk"] == "medium" and row["requested_phases"] == expected_phases
        for row in run_starts
    ):
        raise ExampleVerificationError(
            "ORCHESTRATOR_LOG.md has no medium-risk run_start with all five phases"
        )
    # OS-31 WU-11. `run_end` is not terminal: a cancel/abandon appends a SECOND one, and
    # the contract says the LAST row wins. An `any(...)` reader would report a cancelled
    # run as still COMPLETED, so the authoritative status is the last run_end's result.
    run_ends = [row for row in rows if row["event"] == "run_end"]
    authoritative = run_ends[-1]["result"] if run_ends else None
    if authoritative != "COMPLETED":
        raise ExampleVerificationError(
            "ORCHESTRATOR_LOG.md has no COMPLETED run_end event"
        )

    return VerificationSummary(
        run_dir=run_dir,
        design_fail_iteration=first_fail,
        design_pass_iteration=first_later_pass,
        final_review_iteration=_report_iteration(final_report),
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Verify the full-workflow best-practice example artifacts."
    )
    parser.add_argument(
        "run_dir",
        type=Path,
        help="artifacts/runs/<run-id> directory produced by the example",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        summary = verify_run(args.run_dir)
    except ExampleVerificationError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 2
    print("PASS: full workflow example completed")
    print(f"run_dir: {summary.run_dir}")
    print(
        "design_gate: "
        f"iteration {summary.design_fail_iteration} FAIL -> "
        f"iteration {summary.design_pass_iteration} PASS"
    )
    print(f"final_review: iteration {summary.final_review_iteration} PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
