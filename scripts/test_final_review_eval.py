#!/usr/bin/env python3
"""Tests for scripts/final_review_eval.py: the fixture, the leak scan, the scorer."""

from __future__ import annotations

import argparse
import ast
import inspect
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import run_logging
from scripts import final_review_eval as evaluator

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE = REPO_ROOT / "scripts" / "fixtures" / "final_review_eval"
KEY_PATH = FIXTURE / "key" / "answer_key.json"
SCORER = REPO_ROOT / "scripts" / "final_review_eval.py"


def run_cli(*argv: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCORER), *argv],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def finding(
    identifier: str,
    location: str,
    issue: str,
    reason: str = "",
    action: str = "",
    *,
    blocking: bool = True,
) -> str:
    return (
        f"\nID: {identifier}\n"
        "Quality Attribute: G1\n"
        "Severity: MAJOR\n"
        f"Blocking: {'YES' if blocking else 'NO'}\n"
        "Responsible Phase: implementation\n"
        f"Location: {location}\n"
        f"Issue: {issue}\n"
        f"Reason / Evidence: {reason}\n"
        f"Required Action: {action}\n"
    )


# One finding per entry, phrased in words the key never uses verbatim -- the point of
# claim GROUPS is that the reviewer does not have to guess the key's wording.
PERFECT_REPORT = (
    "# Final Adversarial Review\n\nRESULT: FAIL\nREVIEW_VERDICT: FAIL\n\n"
    + finding(
        "R1",
        "src/policy.py:8",
        "resolve_tier only checks the presence of the retention_tier key.",
        "CONTRACT.md 2 requires the value to name a tier that exists; an unknown tier "
        "is accepted and the limit lookup then reports none.",
    )
    + finding(
        "R2",
        "src/pipeline.py:25",
        "publish_batch resolves a tier for the write but the quota call omits the tier.",
        "the batch path silently evaluates the default tier, so a destination tier is "
        "not propagated.",
    )
    + finding(
        "R3",
        "src/quota.py:19",
        "enforce_quota uses a strict comparison at the boundary.",
        "a store sitting exactly at the limit is refused, and the contract allows that "
        "case.",
    )
    + finding(
        "R4",
        "src/config.py:17",
        "resolve_settings inverts the documented precedence.",
        "the dict splat is last wins, so the built-in defaults override an explicit "
        "override.",
    )
    + finding(
        "R5",
        "src/pipeline.py:33",
        "republish never calls validate_record.",
        "the retry path bypasses validation entirely.",
    )
)

NOISE_FINDING = finding(
    "R9",
    "somewhere in the project",
    "the module docstrings could be longer.",
    "style preference.",
    blocking=False,
)

# The closed-world case needs a noise finding whose location RESOLVES and that still
# matches no key entry: that is the only shape an exhaustive attestation can speak
# about. NOISE_FINDING above is deliberately unresolvable, which is a different case
# (the matcher could not finish evaluating it) and must stay REFUSED.
RESOLVED_NOISE_FINDING = finding(
    "R9",
    "src/validation.py:10",
    "the required field list could be a frozenset.",
    "micro-optimization, not a contract violation.",
    blocking=False,
)


def attestation(**overrides) -> dict:
    document = {
        "schema_version": "1.0",
        "adjudicator": "a human",
        "adjudicated_at": "2026-08-26T10:00:00+00:00",
        "closed_world": True,
        "exhaustive_attestation": {
            "scope": "final_review_eval/v1",
            "statement": "the key enumerates every true defect in this scope",
            "attested_by": "a human",
            "attested_at": "2026-08-26T10:00:00+00:00",
        },
        "verdicts": [],
    }
    document.update(overrides)
    return document


class FixtureIntegrityTests(unittest.TestCase):
    """The fixture is what the key says it is -- demonstrated, not asserted."""

    def test_verify_fixture_passes_on_the_shipped_fixture(self) -> None:
        self.assertEqual(evaluator.verify_fixture(FIXTURE, KEY_PATH), [])

    def test_both_subject_suites_pass(self) -> None:
        """A green head suite is the point: a failing test would localize an entry for
        free, and the fixture would stop measuring search at all."""
        for tree in ("base", "head"):
            with self.subTest(tree=tree):
                passed, output = evaluator._run_suite(
                    evaluator.read_tree(FIXTURE / "subject" / tree)
                )
                self.assertTrue(passed, output)

    def test_every_key_entry_names_a_real_symbol_in_a_changed_range(self) -> None:
        key = evaluator.load_key(KEY_PATH)
        base = evaluator.read_tree(FIXTURE / "subject" / "base")
        head = evaluator.read_tree(FIXTURE / "subject" / "head")
        for entry in key["seeded_defects"]:
            location = entry["location"]
            start, end = location["line_range"]
            with self.subTest(entry=entry["id"]):
                text = head[location["file"]]
                lines = text.splitlines()
                self.assertTrue(
                    any(
                        lines[number - 1].lstrip().startswith(
                            f"def {location['symbol']}"
                        )
                        for number in range(start, end + 1)
                    )
                )
                touched = evaluator.changed_head_lines(
                    base.get(location["file"], ""), text
                )
                self.assertTrue(touched & set(range(start, end + 1)))

    def test_each_entry_is_demonstrated_by_running_the_head_tree(self) -> None:
        """Not "the key says so": each behaviour is exercised against head/ itself."""
        head = FIXTURE / "subject" / "head"
        program = (
            "import sys; sys.path.insert(0, '.')\n"
            "from src.policy import resolve_tier, tier_limits\n"
            "from src.quota import enforce_quota\n"
            "from src.config import resolve_settings\n"
            "from src.pipeline import publish_batch, republish\n"
            "out = {}\n"
            "out['unknown_tier_accepted'] = resolve_tier({'retention_tier': 'typo'}, {})\n"
            "out['unknown_tier_limit'] = tier_limits('typo')['max_items']\n"
            "out['unlimited'] = enforce_quota([{}] * 9999, {}, tier='typo')\n"
            "store = [{} for _ in range(120)]\n"
            "try:\n"
            "    publish_batch(store, [{'id': 'r1', 'payload': 'x', 'created_at': 't'}],"
            " {'max_items': 100}, {'retention_tier': 'archival'})\n"
            "    out['batch_used_destination_tier'] = True\n"
            "except Exception:\n"
            "    out['batch_used_destination_tier'] = False\n"
            "out['exactly_at_limit_accepted'] = enforce_quota([{}] * 100, {})\n"
            "out['explicit_override'] = resolve_settings({'max_items': 7}, {}, {})"
            "['max_items']\n"
            "written = []\n"
            "republish(written, {'id': 'r1'}, {}, {'name': 'd'})\n"
            "out['unvalidated_write'] = len(written)\n"
            "import json; print(json.dumps(out))\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name, text in evaluator.read_tree(head).items():
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text, encoding="utf-8")
            completed = subprocess.run(
                [sys.executable, "-c", program],
                cwd=root,
                capture_output=True,
                text=True,
                check=False,
            )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        observed = json.loads(completed.stdout)

        # SD-1: a value that is not a tier is accepted, and the limit vanishes with it.
        self.assertEqual(observed["unknown_tier_accepted"], "typo")
        self.assertIsNone(observed["unknown_tier_limit"])
        self.assertTrue(observed["unlimited"])
        # SD-2: the batch path did not use the destination's tier.
        self.assertFalse(observed["batch_used_destination_tier"])
        # SD-3: the contract accepts exactly the limit; the code does not.
        self.assertFalse(observed["exactly_at_limit_accepted"])
        # SD-4: the explicit override never takes effect.
        self.assertEqual(observed["explicit_override"], 100)
        # SD-5: the retry path wrote a record no validator saw.
        self.assertEqual(observed["unvalidated_write"], 1)


class LeakScanTests(unittest.TestCase):
    def test_the_subject_tree_carries_no_key_material(self) -> None:
        key = evaluator.load_key(KEY_PATH)
        self.assertEqual(evaluator.scan_leak(key, [FIXTURE / "subject"]), [])

    def test_the_scan_catches_an_entry_id_and_an_archetype_name(self) -> None:
        key = evaluator.load_key(KEY_PATH)
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "leaky.py"
            target.write_text(
                "# SD-3 here, archetype equality_boundary\n", encoding="utf-8"
            )

            hits = evaluator.scan_leak(key, [target])

        tokens = {hit.get("token") for hit in hits}
        self.assertIn("sd-3", tokens)
        self.assertIn("equality_boundary", tokens)

    def test_the_scan_catches_a_verbatim_run_of_the_key_prose(self) -> None:
        key = evaluator.load_key(KEY_PATH)
        excerpt = " ".join(key["seeded_defects"][0]["summary"].split()[:8])
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "leaky.md"
            target.write_text(excerpt, encoding="utf-8")

            self.assertTrue(evaluator.scan_leak(key, [target]))

    def test_the_scan_catches_an_expected_count_statement(self) -> None:
        key = evaluator.load_key(KEY_PATH)
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "leaky.md"
            target.write_text(
                "You should find five defects in this project.\n", encoding="utf-8"
            )

            hits = evaluator.scan_leak(key, [target])

        self.assertTrue(any("expected_count_statement" in hit for hit in hits))

    def test_the_scan_does_not_flag_a_real_symbol_the_subject_must_contain(
        self,
    ) -> None:
        """"Every string in the key" would be wrong: the key names symbols and files
        that MUST appear in the subject tree."""
        key = evaluator.load_key(KEY_PATH)
        tokens = evaluator.key_leak_tokens(key)
        for allowed in ("resolve_tier", "src/policy.py", "enforce_quota", "republish"):
            with self.subTest(token=allowed):
                self.assertNotIn(allowed, tokens)


class ScanLeakRefactorTests(unittest.TestCase):
    """T-8.4g: extracting `scan_leak_text()` changed no record `scan_leak()` returns.

    This is what licenses "behaviour unchanged" in DESIGN iteration 5's Components
    table, rather than leaving it as an assertion. The reference below is the
    pre-refactor per-file body, verbatim.
    """

    @staticmethod
    def reference_scan_leak(key: dict, targets: list[Path]) -> list[dict]:
        tokens = evaluator.key_leak_tokens(key)
        hits: list[dict] = []
        for target in targets:
            files = (
                [path for path in sorted(target.rglob("*")) if path.is_file()]
                if target.is_dir()
                else [target]
            )
            for path in files:
                if "__pycache__" in path.parts:
                    continue
                try:
                    raw = path.read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError):
                    continue
                haystack = " ".join(raw.split()).casefold()
                for token in sorted(tokens):
                    if token in haystack:
                        hits.append({"path": str(path), "token": token})
                for pattern in (
                    evaluator._EXPECTED_COUNT,
                    evaluator._EXPECTED_COUNT_REVERSE,
                ):
                    match = pattern.search(haystack)
                    if match is not None:
                        hits.append(
                            {
                                "path": str(path),
                                "expected_count_statement": match.group(0)[:120],
                            }
                        )
                        break
        return hits

    def test_t84g_the_records_are_identical_over_every_hit_shape(self) -> None:
        key = evaluator.load_key(KEY_PATH)
        defect = key["seeded_defects"][0]
        with tempfile.TemporaryDirectory() as directory:
            tree = Path(directory) / "tree"
            (tree / "__pycache__").mkdir(parents=True)
            (tree / "token.py").write_text(
                f"# {defect['id']} here, archetype {defect['archetype']}\n",
                encoding="utf-8",
            )
            (tree / "counted.md").write_text(
                "You should find five defects in this project.\n", encoding="utf-8"
            )
            (tree / "prose.md").write_text(defect["summary"], encoding="utf-8")
            (tree / "clean.txt").write_text("nothing here\n", encoding="utf-8")
            (tree / "undecodable.bin").write_bytes(b"\xff\xfe\x00\x01binary")
            (tree / "__pycache__" / "skipped.py").write_text(
                f"# {defect['id']}\n", encoding="utf-8"
            )

            observed = evaluator.scan_leak(key, [tree])
            expected = self.reference_scan_leak(key, [tree])

        self.assertEqual(observed, expected)
        self.assertTrue(any("expected_count_statement" in hit for hit in observed))
        self.assertTrue(any(hit.get("token") == defect["archetype"] for hit in observed))
        self.assertNotIn(
            "__pycache__", " ".join(hit["path"] for hit in observed)
        )
        for path in {hit["path"] for hit in observed}:
            self.assertLessEqual(
                len([h for h in observed if h["path"] == path
                     and "expected_count_statement" in h]),
                1,
                "at most one expected-count record per file, as before",
            )

    def test_t84g_the_shipped_fixture_is_unchanged_too(self) -> None:
        key = evaluator.load_key(KEY_PATH)
        subject = FIXTURE / "subject"
        self.assertEqual(
            evaluator.scan_leak(key, [subject]),
            self.reference_scan_leak(key, [subject]),
        )


class MaterializeTests(unittest.TestCase):
    def workspace(self, directory: str) -> Path:
        destination = Path(directory) / "ws"
        evaluator.materialize(destination, FIXTURE)
        return destination

    def test_a_workspace_carries_the_head_tree_a_diff_and_a_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            destination = self.workspace(directory)

            self.assertTrue((destination / "CONTRACT.md").is_file())
            self.assertTrue((destination / "src" / "policy.py").is_file())
            self.assertTrue((destination / "tests" / "test_quota.py").is_file())
            diff = (destination / "DIFF.patch").read_text(encoding="utf-8")
            self.assertIn("--- a/src/config.py", diff)
            self.assertIn("+++ b/src/pipeline.py", diff)
            manifest = json.loads(
                (destination / "MANIFEST.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                manifest["fixture_digest"], evaluator.key_fixture_digest(KEY_PATH)
            )

    def test_no_git_directory_is_created_or_copied(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            destination = self.workspace(directory)

            self.assertFalse((destination / ".git").exists())
            self.assertEqual(list(destination.rglob(".git")), [])

    def test_the_key_and_the_adjudications_never_reach_the_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            destination = self.workspace(directory)

            for path in destination.rglob("*"):
                for part in path.relative_to(destination).parts:
                    with self.subTest(path=str(path)):
                        self.assertNotIn(part, ("key", "adjudications"))
            self.assertEqual(list(destination.rglob("answer_key.json")), [])

    def test_the_workspace_the_reviewer_reads_is_clean(self) -> None:
        """D.5 rule 4, with no exemption: every file, MANIFEST.json included."""
        key = evaluator.load_key(KEY_PATH)
        with tempfile.TemporaryDirectory() as directory:
            destination = self.workspace(directory)

            scanned = sorted(
                path.relative_to(destination).as_posix()
                for path in destination.rglob("*")
                if path.is_file()
            )
            self.assertIn("MANIFEST.json", scanned)
            self.assertIn("CONTRACT.md", scanned)
            self.assertIn("DIFF.patch", scanned)
            self.assertEqual(evaluator.scan_leak(key, [destination]), [])

    def test_the_manifest_names_the_fixture_opaquely(self) -> None:
        """The fixture id is a leak token, so the workspace carries a digest of it."""
        key = evaluator.load_key(KEY_PATH)
        fixture_id = key["fixture_id"]
        with tempfile.TemporaryDirectory() as directory:
            destination = self.workspace(directory)

            raw = (destination / "MANIFEST.json").read_text(encoding="utf-8")
            self.assertNotIn(fixture_id, raw)
            manifest = json.loads(raw)
            self.assertEqual(
                manifest["fixture_id"], evaluator.workspace_fixture_ref(fixture_id)
            )
            self.assertEqual(
                manifest["fixture_id_form"], evaluator.WORKSPACE_FIXTURE_REF_FORM
            )

    def test_the_scanner_takes_no_exclusion_argument(self) -> None:
        """A scanner that can be told to skip reviewer-visible content proves nothing."""
        self.assertNotIn(
            "exclude_names",
            inspect.signature(evaluator.scan_leak).parameters,
        )

    def test_a_non_empty_destination_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "ws"
            destination.mkdir()
            (destination / "already-here").write_text("x", encoding="utf-8")

            with self.assertRaises(evaluator.FixtureError):
                evaluator.materialize(destination, FIXTURE)

            self.assertEqual(
                sorted(path.name for path in destination.iterdir()), ["already-here"]
            )

    def test_a_stale_expected_digest_fails_and_leaves_nothing_behind(self) -> None:
        """There is deliberately no flag that rewrites the value it checks against."""
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "ws"
            with patch.object(
                evaluator, "key_fixture_digest", return_value="sha256:stale"
            ):
                with self.assertRaises(evaluator.EvalContractError):
                    evaluator.materialize(destination, FIXTURE)

            self.assertFalse(destination.exists() and any(destination.iterdir()))

    def test_two_materializations_are_byte_identical(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            first = self.workspace(directory)
            second = Path(directory) / "ws2"
            evaluator.materialize(second, FIXTURE)

            self.assertEqual(
                evaluator.read_tree(first), evaluator.read_tree(second)
            )


class MatchingTests(unittest.TestCase):
    def parse(self, report: str, workspace: Path | None = None) -> dict:
        return {
            "schema_version": "1.0",
            "source_report": "report.md",
            "source_report_digest": "sha256:" + evaluator.sha256_text(report),
            "findings": evaluator.parse_report(report, workspace),
        }

    def score(self, report: str, **kwargs) -> dict:
        return evaluator.score(
            self.parse(report), evaluator.load_key(KEY_PATH), **kwargs
        )

    def test_a_report_that_finds_everything_scores_full_recall(self) -> None:
        metrics = self.score(PERFECT_REPORT)

        self.assertEqual(metrics["detected_seeded_defects"], 5)
        self.assertEqual(
            metrics["seeded_recall"],
            {
                "value": 1.0,
                "numerator": 5,
                "denominator": 5,
                "population": "seeded_defects_only",
            },
        )
        self.assertEqual(metrics["missed_defect_ids"], [])

    def test_two_entries_in_one_file_are_separated(self) -> None:
        """SD-2 and SD-5 both live in src/pipeline.py, which is exactly why the matcher
        needs symbol and line disambiguation."""
        metrics = self.score(PERFECT_REPORT)

        pairs = {
            item["finding_id"]: item["seeded_defect_id"]
            for item in metrics["matched_findings"]
        }
        self.assertEqual(pairs["R2"], "SD-2")
        self.assertEqual(pairs["R5"], "SD-5")

    def test_the_assignment_is_one_to_one(self) -> None:
        duplicated = PERFECT_REPORT + finding(
            "R6",
            "src/policy.py:8",
            "resolve_tier only checks the presence of the key.",
            "an unknown tier is accepted.",
        )

        metrics = self.score(duplicated)

        assigned = [item["seeded_defect_id"] for item in metrics["matched_findings"]]
        self.assertEqual(len(assigned), len(set(assigned)))
        self.assertIn("R6", [item["finding_id"] for item in metrics["unmatched_findings"]])

    def test_a_missed_entry_is_reported_with_an_explicit_denominator(self) -> None:
        partial = (
            "RESULT: FAIL\n"
            + finding(
                "R1",
                "src/policy.py:8",
                "resolve_tier only checks the presence of the retention_tier key.",
                "an unknown tier is accepted.",
            )
        )

        metrics = self.score(partial)

        self.assertEqual(metrics["seeded_recall"]["numerator"], 1)
        self.assertEqual(metrics["seeded_recall"]["denominator"], 5)
        self.assertEqual(metrics["seeded_recall"]["population"], "seeded_defects_only")
        self.assertEqual(metrics["miss_count"], 4)
        self.assertEqual(
            metrics["missed_defect_ids"], ["SD-2", "SD-3", "SD-4", "SD-5"]
        )

    def test_an_unmatched_finding_is_unadjudicated_and_never_an_auto_false_positive(
        self,
    ) -> None:
        metrics = self.score(PERFECT_REPORT + NOISE_FINDING)

        unmatched = metrics["unmatched_findings"]
        self.assertEqual(len(unmatched), 1)
        self.assertEqual(unmatched[0]["finding_id"], "R9")
        self.assertEqual(unmatched[0]["classification"], "UNADJUDICATED")
        self.assertEqual(metrics["adjudicated_false_positives"], 0)
        self.assertEqual(metrics["unadjudicated_count"], 1)

    def test_a_finding_whose_location_does_not_resolve_says_so(self) -> None:
        metrics = self.score(PERFECT_REPORT + NOISE_FINDING)

        self.assertEqual(
            metrics["unmatched_findings"][0]["reason"], "unresolvable_location"
        )

    def test_a_finding_with_no_key_match_is_labelled_no_key_match(self) -> None:
        off_topic = "RESULT: FAIL\n" + finding(
            "R7",
            "src/validation.py:10",
            "the required field list could be a frozenset.",
            "micro-optimization.",
        )

        metrics = self.score(off_topic)

        self.assertEqual(metrics["unmatched_findings"][0]["reason"], "no_key_match")

    def test_evidence_grounding_uses_the_materialized_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory) / "ws"
            evaluator.materialize(workspace, FIXTURE)

            metrics = evaluator.score(
                self.parse(PERFECT_REPORT + NOISE_FINDING, workspace),
                evaluator.load_key(KEY_PATH),
                workspace=workspace,
            )

        self.assertEqual(metrics["evidence_grounding"]["numerator"], 5)
        self.assertEqual(metrics["evidence_grounding"]["denominator"], 6)
        self.assertEqual(
            metrics["evidence_grounding"]["ungrounded_finding_ids"], ["R9"]
        )

    def test_a_workspace_that_is_not_the_keys_tree_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory) / "ws"
            evaluator.materialize(workspace, FIXTURE)
            manifest = json.loads(
                (workspace / "MANIFEST.json").read_text(encoding="utf-8")
            )
            manifest["fixture_digest"] = "sha256:something-else"
            (workspace / "MANIFEST.json").write_text(
                json.dumps(manifest), encoding="utf-8"
            )

            with self.assertRaises(evaluator.EvalContractError):
                evaluator.score(
                    self.parse(PERFECT_REPORT),
                    evaluator.load_key(KEY_PATH),
                    workspace=workspace,
                )


class PrecisionRefusalTests(unittest.TestCase):
    def parse(self, report: str) -> dict:
        return {
            "schema_version": "1.0",
            "source_report": "report.md",
            "source_report_digest": "sha256:" + evaluator.sha256_text(report),
            "findings": evaluator.parse_report(report, None),
        }

    def score(self, report: str, adjudications=None) -> dict:
        return evaluator.score(
            self.parse(report),
            evaluator.load_key(KEY_PATH),
            adjudications=adjudications,
        )

    def test_precision_is_refused_without_adjudication(self) -> None:
        metrics = self.score(PERFECT_REPORT + NOISE_FINDING)

        self.assertIsNone(metrics["precision"])
        self.assertEqual(metrics["precision_status"], "REFUSED")
        self.assertIn("adjudication_incomplete", metrics["precision_refusal_reason"])
        self.assertIsNone(metrics["false_positive_rate"])
        self.assertEqual(metrics["false_positive_rate_status"], "REFUSED")
        self.assertEqual(metrics["adjudication_status"], "none")

    def test_the_refused_keys_are_present_never_omitted(self) -> None:
        metrics = self.score(PERFECT_REPORT + NOISE_FINDING)

        for key in (
            "precision",
            "precision_status",
            "precision_refusal_reason",
            "false_positive_rate",
            "false_positive_rate_status",
            "false_positive_rate_refusal_reason",
            "unadjudicated_count",
            "adjudication_status",
        ):
            with self.subTest(key=key):
                self.assertIn(key, metrics)

    def test_a_complete_adjudication_computes_precision(self) -> None:
        adjudications = {
            "schema_version": "1.0",
            "adjudicator": "a human",
            "adjudicated_at": "2026-08-26T10:00:00+00:00",
            "closed_world": False,
            "exhaustive_attestation": None,
            "verdicts": [
                {
                    "finding_id": "R9",
                    "verdict": "false_positive",
                    "rationale": "a style preference, not a defect",
                }
            ],
        }

        metrics = self.score(PERFECT_REPORT + NOISE_FINDING, adjudications)

        self.assertEqual(metrics["precision_status"], "COMPUTED")
        self.assertAlmostEqual(metrics["precision"], 5 / 6)
        self.assertAlmostEqual(metrics["false_positive_rate"], 1 / 6)
        self.assertEqual(metrics["adjudication_status"], "complete")
        self.assertEqual(metrics["unadjudicated_count"], 0)

    def test_a_partial_adjudication_still_refuses(self) -> None:
        report = PERFECT_REPORT + NOISE_FINDING + finding(
            "R10", "src/validation.py:5", "unrelated", "unrelated"
        )
        adjudications = {
            "schema_version": "1.0",
            "adjudicator": "a human",
            "adjudicated_at": "2026-08-26T10:00:00+00:00",
            "verdicts": [
                {
                    "finding_id": "R9",
                    "verdict": "false_positive",
                    "rationale": "style only",
                }
            ],
        }

        metrics = self.score(report, adjudications)

        self.assertEqual(metrics["precision_status"], "REFUSED")
        self.assertEqual(metrics["adjudication_status"], "partial")
        self.assertEqual(metrics["unadjudicated_count"], 1)

    def test_a_closed_world_attestation_computes_precision(self) -> None:
        metrics = self.score(PERFECT_REPORT + RESOLVED_NOISE_FINDING, attestation())

        self.assertEqual(metrics["precision_status"], "COMPUTED")
        self.assertTrue(metrics["closed_world"])

    def test_a_closed_world_run_refuses_an_unresolvable_noise_finding(self) -> None:
        """The attestation covers the KEY's completeness, not a match the matcher
        could not finish evaluating. Auto-FP-ing one would manufacture precision out
        of a matcher limitation."""
        metrics = self.score(PERFECT_REPORT + NOISE_FINDING, attestation())

        self.assertEqual(metrics["precision_status"], "REFUSED")
        self.assertIn(
            "closed_world_incomplete_match_evaluation",
            metrics["precision_refusal_reason"],
        )
        self.assertEqual(metrics["unmatched_findings"][0]["classification"], "UNADJUDICATED")
        self.assertEqual(metrics["attested_false_positives"], 0)

    def test_recall_is_computable_even_when_precision_is_refused(self) -> None:
        metrics = self.score(PERFECT_REPORT + NOISE_FINDING)

        self.assertEqual(metrics["seeded_recall"]["value"], 1.0)
        self.assertEqual(metrics["precision_status"], "REFUSED")

    def test_verdict_reproducibility_is_never_asserted_from_one_run(self) -> None:
        document = self.parse(PERFECT_REPORT)
        key = evaluator.load_key(KEY_PATH)

        single = evaluator.score(document, key, run_verdicts=["FAIL"])
        self.assertEqual(
            single["verdict_reproducibility"]["status"], "SINGLE_RUN_NOT_ASSERTED"
        )
        self.assertIsNone(single["verdict_reproducibility"]["agreement"])

        several = evaluator.score(
            document, key, run_verdicts=["FAIL", "FAIL", "PASS"]
        )
        self.assertEqual(several["verdict_reproducibility"]["status"], "OBSERVED")
        self.assertEqual(several["verdict_reproducibility"]["run_count"], 3)
        self.assertAlmostEqual(several["verdict_reproducibility"]["agreement"], 2 / 3)


class ClosedWorldFalsePositiveRateTests(unittest.TestCase):
    """The R3 regression guard, asserted by EXACT VALUE, not by "computation occurred".

    Before the D-E correction this exact input reported false_positive_rate 0.0 with
    unadjudicated_count 1: precision penalised the unmatched finding while the
    false-positive rate ignored it. Four of the assertions below fail against that
    behaviour, which is what makes this a regression guard rather than a formality.
    """

    def parse(self, report: str) -> dict:
        return {
            "schema_version": "1.0",
            "source_report": "report.md",
            "source_report_digest": "sha256:" + evaluator.sha256_text(report),
            "findings": evaluator.parse_report(report, None),
        }

    def score(self, report: str, adjudications=None) -> dict:
        return evaluator.score(
            self.parse(report),
            evaluator.load_key(KEY_PATH),
            adjudications=adjudications,
        )

    def test_an_unmatched_finding_under_attestation_is_an_attested_false_positive(
        self,
    ) -> None:
        metrics = self.score(PERFECT_REPORT + RESOLVED_NOISE_FINDING, attestation())

        self.assertEqual(metrics["findings_total"], 6)
        self.assertEqual(len(metrics["matched_findings"]), 5)
        unmatched = metrics["unmatched_findings"]
        self.assertEqual(len(unmatched), 1)
        self.assertEqual(unmatched[0]["finding_id"], "R9")
        self.assertEqual(unmatched[0]["reason"], "no_key_match")
        self.assertEqual(unmatched[0]["classification"], "ATTESTED_FALSE_POSITIVE")
        self.assertEqual(metrics["attested_false_positives"], 1)
        self.assertEqual(metrics["adjudicated_false_positives"], 0)
        self.assertEqual(metrics["unadjudicated_count"], 0)
        self.assertEqual(metrics["adjudication_status"], "complete_by_attestation")
        self.assertEqual(metrics["precision_status"], "COMPUTED")
        self.assertAlmostEqual(metrics["precision"], 5 / 6)
        # The R3 defect reported 0.0 here.
        self.assertAlmostEqual(metrics["false_positive_rate"], 1 / 6)
        self.assertAlmostEqual(
            metrics["precision"] + metrics["false_positive_rate"], 1.0
        )

    def test_an_explicit_verdict_beats_the_attestation(self) -> None:
        adjudications = attestation(
            verdicts=[
                {
                    "finding_id": "R9",
                    "verdict": "true_positive",
                    "rationale": "a real defect the key does not enumerate",
                }
            ]
        )

        metrics = self.score(PERFECT_REPORT + RESOLVED_NOISE_FINDING, adjudications)

        self.assertEqual(
            metrics["unmatched_findings"][0]["classification"],
            "ADJUDICATED_TRUE_POSITIVE",
        )
        self.assertEqual(metrics["attested_false_positives"], 0)
        self.assertEqual(metrics["adjudication_status"], "complete")
        self.assertAlmostEqual(metrics["precision"], 1.0)
        self.assertAlmostEqual(metrics["false_positive_rate"], 0.0)

    def test_closed_world_refuses_an_incompletely_evaluated_match(self) -> None:
        """Both reasons, and the fix available to the adjudicator.

        The reason is FORCED rather than provoked: what is under test is the
        classification rule keyed on E.4 step 6's reason, not the matcher's ability to
        produce that reason from a particular report.
        """
        real = evaluator.match_findings

        def forced(reason: str):
            def _match(findings, key):
                matched, unmatched = real(findings, key)
                return matched, [{**item, "reason": reason} for item in unmatched]

            return _match

        for reason in evaluator.INCOMPLETE_MATCH_REASONS:
            with self.subTest(reason=reason):
                with patch.object(evaluator, "match_findings", forced(reason)):
                    metrics = self.score(
                        PERFECT_REPORT + RESOLVED_NOISE_FINDING, attestation()
                    )

                self.assertEqual(metrics["unmatched_findings"][0]["reason"], reason)
                self.assertEqual(
                    metrics["unmatched_findings"][0]["classification"], "UNADJUDICATED"
                )
                self.assertEqual(metrics["precision_status"], "REFUSED")
                self.assertEqual(metrics["false_positive_rate_status"], "REFUSED")
                self.assertIsNone(metrics["precision"])
                self.assertIsNone(metrics["false_positive_rate"])
                self.assertIn(
                    "closed_world_incomplete_match_evaluation",
                    metrics["precision_refusal_reason"],
                )
                self.assertEqual(metrics["adjudication_status"], "partial")
                self.assertEqual(metrics["attested_false_positives"], 0)

                # The fix available to the adjudicator: a verdict for exactly that id
                # flips the same input to COMPUTED.
                with patch.object(evaluator, "match_findings", forced(reason)):
                    supplied = self.score(
                        PERFECT_REPORT + RESOLVED_NOISE_FINDING,
                        attestation(
                            verdicts=[
                                {
                                    "finding_id": "R9",
                                    "verdict": "false_positive",
                                    "rationale": "a style preference, not a defect",
                                }
                            ]
                        ),
                    )
                self.assertEqual(supplied["precision_status"], "COMPUTED")
                self.assertEqual(supplied["adjudication_status"], "complete")
                self.assertAlmostEqual(supplied["false_positive_rate"], 1 / 6)

        supplied = self.score(
            PERFECT_REPORT + NOISE_FINDING,
            attestation(
                verdicts=[
                    {
                        "finding_id": "R9",
                        "verdict": "false_positive",
                        "rationale": "a style preference the review should not have "
                        "reported",
                    }
                ]
            ),
        )
        self.assertEqual(supplied["precision_status"], "COMPUTED")
        self.assertEqual(supplied["adjudication_status"], "complete")
        self.assertAlmostEqual(supplied["false_positive_rate"], 1 / 6)

    def test_the_open_world_path_never_auto_false_positives(self) -> None:
        """Path B is unchanged by the closed-world exception."""
        for report in (
            PERFECT_REPORT + RESOLVED_NOISE_FINDING,
            PERFECT_REPORT + NOISE_FINDING,
        ):
            with self.subTest(report=report[-40:]):
                metrics = self.score(report)
                self.assertEqual(
                    metrics["unmatched_findings"][0]["classification"],
                    "UNADJUDICATED",
                )
                self.assertEqual(metrics["attested_false_positives"], 0)
                self.assertEqual(metrics["precision_status"], "REFUSED")
                self.assertIn(
                    "adjudication_incomplete", metrics["precision_refusal_reason"]
                )

    def test_the_gate_is_a_single_decision_across_every_case(self) -> None:
        cases = (
            (PERFECT_REPORT + RESOLVED_NOISE_FINDING, attestation()),
            (PERFECT_REPORT + NOISE_FINDING, attestation()),
            (PERFECT_REPORT + RESOLVED_NOISE_FINDING, None),
            (PERFECT_REPORT, None),
            (PERFECT_REPORT, attestation()),
        )
        for report, adjudications in cases:
            with self.subTest(adjudicated=adjudications is not None):
                metrics = self.score(report, adjudications)
                self.assertEqual(
                    metrics["precision_status"], metrics["false_positive_rate_status"]
                )
                if metrics["precision_status"] == "COMPUTED":
                    self.assertEqual(metrics["unadjudicated_count"], 0)
                    self.assertAlmostEqual(
                        metrics["precision"] + metrics["false_positive_rate"], 1.0
                    )

    def test_no_findings_refuses_both_metrics_on_both_paths(self) -> None:
        for adjudications in (None, attestation()):
            with self.subTest(closed_world=adjudications is not None):
                metrics = self.score("RESULT: PASS\n", adjudications)

                self.assertEqual(metrics["findings_total"], 0)
                self.assertIsNone(metrics["precision"])
                self.assertIsNone(metrics["false_positive_rate"])
                self.assertEqual(metrics["precision_status"], "REFUSED")
                self.assertIn("no_findings", metrics["precision_refusal_reason"])


class ScorerPathFieldTests(unittest.TestCase):
    """C.7 P-PATH: the scorer's own path fields, so a metrics document produced from a
    scratch workspace cannot embed that workspace's absolute path."""

    def metrics_for(self, source_report: str) -> dict:
        document = {
            "schema_version": "1.0",
            "source_report": source_report,
            "source_report_digest": "sha256:" + evaluator.sha256_text(PERFECT_REPORT),
            "findings": evaluator.parse_report(PERFECT_REPORT, None),
        }
        return evaluator.score(document, evaluator.load_key(KEY_PATH))

    def test_an_absolute_source_report_outside_the_repository_is_replaced(self) -> None:
        for value in (
            "/private/tmp/claude-501/-Users-luminous-orca-skills/9b57/scratch/REPORT.md",
            "/luminous",
        ):
            with self.subTest(value=value):
                metrics = self.metrics_for(value)

                self.assertEqual(
                    metrics["findings_source"], "<REDACTED:foreign_absolute_path>"
                )
                self.assertNotIn("luminous", json.dumps(metrics))

    def test_a_repository_path_stays_readable(self) -> None:
        metrics = self.metrics_for(
            str(REPO_ROOT / "artifacts" / "runs" / "run_x" / "report.md")
        )

        self.assertEqual(metrics["findings_source"], "artifacts/runs/run_x/report.md")

    def test_every_string_in_the_metrics_document_is_path_safe(self) -> None:
        metrics = self.metrics_for("/private/tmp/x/y/report.md")

        def walk(node):
            if isinstance(node, str):
                yield node
            elif isinstance(node, dict):
                for value in node.values():
                    yield from walk(value)
            elif isinstance(node, list):
                for value in node:
                    yield from walk(value)

        for value in walk(metrics):
            with self.subTest(value=value):
                self.assertFalse(value.startswith("/"))
        self.assertEqual(
            run_logging.normalize_retained_path_field(metrics["findings_source"]),
            metrics["findings_source"],
        )


class AdjudicationContractTests(unittest.TestCase):
    def load(self, document: dict):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "adjudications.json"
            path.write_text(json.dumps(document), encoding="utf-8")
            return evaluator.load_adjudications(path)

    def base(self, **overrides) -> dict:
        document = {
            "schema_version": "1.0",
            "adjudicator": "a human",
            "adjudicated_at": "2026-08-26T10:00:00+00:00",
            "verdicts": [
                {"finding_id": "R9", "verdict": "false_positive", "rationale": "style"}
            ],
        }
        document.update(overrides)
        return document

    def test_a_well_formed_adjudication_loads(self) -> None:
        self.assertEqual(len(self.load(self.base())["verdicts"]), 1)

    def test_a_historical_corpus_field_is_a_hard_error(self) -> None:
        """DEC-8 rule 4 made structural: the forbidden inference is unrepresentable,
        not merely discouraged."""
        document = self.base()
        document["verdicts"][0]["was_corrected_in_a_previous_run"] = True

        with self.assertRaises(evaluator.EvalContractError):
            self.load(document)

    def test_an_unknown_top_level_key_is_a_hard_error(self) -> None:
        with self.assertRaises(evaluator.EvalContractError):
            self.load(self.base(historical_agreement_rate=0.9))

    def test_an_empty_rationale_is_refused(self) -> None:
        document = self.base()
        document["verdicts"][0]["rationale"] = "   "

        with self.assertRaises(evaluator.EvalContractError):
            self.load(document)

    def test_a_third_verdict_value_is_refused(self) -> None:
        document = self.base()
        document["verdicts"][0]["verdict"] = "probably_fine"

        with self.assertRaises(evaluator.EvalContractError):
            self.load(document)

    def test_a_duplicate_finding_id_is_refused(self) -> None:
        document = self.base()
        document["verdicts"].append(dict(document["verdicts"][0]))

        with self.assertRaises(evaluator.EvalContractError):
            self.load(document)

    def test_an_attestation_without_a_closed_world_claim_is_refused(self) -> None:
        """The other half of E.3's coupling: an attestation no computation path reads
        is a half-state, not a document."""
        with self.assertRaises(evaluator.EvalContractError):
            self.load(attestation(closed_world=False))

    def test_closed_world_requires_a_complete_attestation(self) -> None:
        with self.assertRaises(evaluator.EvalContractError):
            self.load(self.base(closed_world=True))
        with self.assertRaises(evaluator.EvalContractError):
            self.load(
                self.base(
                    closed_world=True,
                    exhaustive_attestation={
                        "scope": "x",
                        "statement": "",
                        "attested_by": "y",
                        "attested_at": "z",
                    },
                )
            )


class DeterminismTests(unittest.TestCase):
    """B5: identical inputs, byte-identical metrics -- for the WHOLE file."""

    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.report = self.root / "report.md"
        self.report.write_text(PERFECT_REPORT + NOISE_FINDING, encoding="utf-8")
        self.findings = self.root / "findings.json"
        self.assertEqual(
            run_cli(
                "parse-report", "--report", str(self.report), "--out", str(self.findings)
            ).returncode,
            0,
        )

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def score_to(self, name: str, *extra: str) -> tuple[Path, int]:
        out = self.root / name
        completed = run_cli(
            "score",
            "--findings",
            str(self.findings),
            "--key",
            str(KEY_PATH),
            "--out",
            str(out),
            *extra,
        )
        return out, completed.returncode

    def test_two_runs_produce_byte_identical_metrics(self) -> None:
        first, code_a = self.score_to("a.json")
        second, code_b = self.score_to("b.json")

        self.assertEqual((code_a, code_b), (0, 0))
        self.assertEqual(first.read_bytes(), second.read_bytes())

    def test_the_metrics_document_reads_no_clock(self) -> None:
        """Structural, not reviewed: every clock module reachable from this module is
        replaced by an object that raises on ANY attribute access, and `score` still
        returns. A future contributor cannot reintroduce a timestamp into the metrics
        document without this failing."""
        document = json.loads(self.findings.read_text(encoding="utf-8"))
        key = evaluator.load_key(KEY_PATH)

        class Exploding:
            def __getattr__(self, name):
                raise AssertionError(f"the scorer reached a clock: {name}")

        with patch.dict(
            sys.modules, {"time": Exploding(), "datetime": Exploding()}
        ):
            metrics = evaluator.score(document, key)

        self.assertNotIn("generated_at", metrics)
        for value in metrics:
            with self.subTest(key=value):
                self.assertNotIn("generated", value)

    def test_the_only_clock_read_lives_in_the_sidecar_writer(self) -> None:
        """The import that reads a clock is function-local, in one function, so the
        proof above is about the whole module rather than about one call path."""
        module = ast.parse(SCORER.read_text(encoding="utf-8"))
        clock_importers = set()
        for node in ast.walk(module):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for inner in ast.walk(node):
                    names = []
                    if isinstance(inner, ast.Import):
                        names = [alias.name for alias in inner.names]
                    elif isinstance(inner, ast.ImportFrom):
                        names = [inner.module or ""]
                    if any(name in ("time", "datetime") for name in names):
                        clock_importers.add(node.name)
        self.assertEqual(clock_importers, {"_write_provenance"})
        top_level = {
            alias.name
            for node in module.body
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        self.assertNotIn("time", top_level)
        self.assertNotIn("datetime", top_level)

    def test_the_provenance_sidecar_is_a_separate_file(self) -> None:
        out = self.root / "metrics.json"
        sidecar = self.root / "provenance.json"
        completed = run_cli(
            "score",
            "--findings",
            str(self.findings),
            "--key",
            str(KEY_PATH),
            "--out",
            str(out),
            "--provenance-out",
            str(sidecar),
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        metrics = json.loads(out.read_text(encoding="utf-8"))
        provenance = json.loads(sidecar.read_text(encoding="utf-8"))
        self.assertNotIn("generated_at", metrics)
        self.assertIn("generated_at", provenance)
        self.assertEqual(
            provenance["metrics_digest"],
            "sha256:" + evaluator.sha256_text(out.read_text(encoding="utf-8")),
        )

    def test_the_default_invocation_writes_no_sidecar(self) -> None:
        out, code = self.score_to("metrics.json")

        self.assertEqual(code, 0)
        self.assertEqual(list(self.root.glob("provenance*.json")), [])
        self.assertTrue(out.is_file())


class ExitCodeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        report = self.root / "report.md"
        report.write_text(PERFECT_REPORT + NOISE_FINDING, encoding="utf-8")
        self.findings = self.root / "findings.json"
        run_cli("parse-report", "--report", str(report), "--out", str(self.findings))

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_zero_when_precision_is_refused_and_not_required(self) -> None:
        completed = run_cli(
            "score", "--findings", str(self.findings), "--key", str(KEY_PATH)
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("REFUSED", completed.stdout)

    def test_three_when_precision_is_refused_and_required(self) -> None:
        completed = run_cli(
            "score",
            "--findings",
            str(self.findings),
            "--key",
            str(KEY_PATH),
            "--require-precision",
        )

        self.assertEqual(completed.returncode, 3)
        self.assertIn("precision refused", completed.stderr)

    def test_one_for_a_missing_input(self) -> None:
        completed = run_cli(
            "score",
            "--findings",
            str(self.root / "nope.json"),
            "--key",
            str(KEY_PATH),
        )

        self.assertEqual(completed.returncode, 1)

    def test_one_for_an_unknown_schema_major(self) -> None:
        document = json.loads(self.findings.read_text(encoding="utf-8"))
        document["schema_version"] = "9.0"
        path = self.root / "future.json"
        path.write_text(json.dumps(document), encoding="utf-8")

        completed = run_cli("score", "--findings", str(path), "--key", str(KEY_PATH))

        self.assertEqual(completed.returncode, 1)

    def test_two_for_a_contract_violation_in_an_adjudication(self) -> None:
        path = self.root / "adjudications.json"
        path.write_text(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "adjudicator": "a human",
                    "adjudicated_at": "2026-08-26T10:00:00+00:00",
                    "verdicts": [
                        {
                            "finding_id": "R9",
                            "verdict": "false_positive",
                            "rationale": "style",
                            "was_not_disputed": True,
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )

        completed = run_cli(
            "score",
            "--findings",
            str(self.findings),
            "--key",
            str(KEY_PATH),
            "--adjudications",
            str(path),
        )

        self.assertEqual(completed.returncode, 2)

    def test_three_when_a_closed_world_run_cannot_finish_a_match(self) -> None:
        """The findings file's noise finding is unresolvable, so the attestation says
        nothing about it and --require-precision must still exit 3."""
        path = self.root / "attestation.json"
        path.write_text(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "adjudicator": "a human",
                    "adjudicated_at": "2026-08-26T10:00:00+00:00",
                    "closed_world": True,
                    "exhaustive_attestation": {
                        "scope": "final_review_eval/v1",
                        "statement": "the key enumerates every true defect in scope",
                        "attested_by": "a human",
                        "attested_at": "2026-08-26T10:00:00+00:00",
                    },
                    "verdicts": [],
                }
            ),
            encoding="utf-8",
        )

        completed = run_cli(
            "score",
            "--findings",
            str(self.findings),
            "--key",
            str(KEY_PATH),
            "--adjudications",
            str(path),
            "--require-precision",
        )

        self.assertEqual(completed.returncode, 3)
        self.assertIn("closed_world_incomplete_match_evaluation", completed.stderr)

    def test_two_when_closed_world_and_the_attestation_disagree(self) -> None:
        """E.3's coupling, in both directions, at the CLI boundary."""
        signed = {
            "scope": "final_review_eval/v1",
            "statement": "the key enumerates every true defect in scope",
            "attested_by": "a human",
            "attested_at": "2026-08-26T10:00:00+00:00",
        }
        for label, closed_world, attestation_value in (
            ("unsigned closed world", True, None),
            ("unread attestation", False, signed),
        ):
            with self.subTest(case=label):
                path = self.root / f"adjudications-{closed_world}.json"
                path.write_text(
                    json.dumps(
                        {
                            "schema_version": "1.0",
                            "adjudicator": "a human",
                            "adjudicated_at": "2026-08-26T10:00:00+00:00",
                            "closed_world": closed_world,
                            "exhaustive_attestation": attestation_value,
                            "verdicts": [],
                        }
                    ),
                    encoding="utf-8",
                )

                completed = run_cli(
                    "score",
                    "--findings",
                    str(self.findings),
                    "--key",
                    str(KEY_PATH),
                    "--adjudications",
                    str(path),
                )

                self.assertEqual(completed.returncode, 2, completed.stderr)

    def test_four_for_a_leak_and_for_a_non_empty_destination(self) -> None:
        leaky = self.root / "leaky.md"
        leaky.write_text("SD-1 lives here\n", encoding="utf-8")
        leak = run_cli(
            "scan-leak", "--key", str(KEY_PATH), "--target", str(leaky)
        )
        self.assertEqual(leak.returncode, 4)

        destination = self.root / "ws"
        destination.mkdir()
        (destination / "x").write_text("x", encoding="utf-8")
        occupied = run_cli("materialize", "--dest", str(destination))
        self.assertEqual(occupied.returncode, 4)

    def test_verify_fixture_exits_zero_on_the_shipped_fixture(self) -> None:
        completed = run_cli("verify-fixture")

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("PASSED", completed.stdout)


class NoTargetCountTests(unittest.TestCase):
    def test_nothing_reads_a_target_finding_count(self) -> None:
        """Section 5's "expected finding count" cannot leak through the key even by
        accident, because no code path reads one.

        Asserted over the parsed source: the phrase occurs exactly twice -- once as a
        token the leak scanner looks FOR, and once as the name of the literal guard the
        key must carry -- and never as a value anything reads.
        """
        module = ast.parse(SCORER.read_text(encoding="utf-8"))
        read = [
            node.args[0].value
            for node in ast.walk(module)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ]
        subscripts = [
            node.slice.value
            for node in ast.walk(module)
            if isinstance(node, ast.Subscript)
            and isinstance(node.slice, ast.Constant)
            and isinstance(node.slice.value, str)
        ]
        # The ONLY name carrying the phrase that anything reads is the literal guard,
        # and it is read to assert its presence -- never for a number.
        for name in read + subscripts:
            with self.subTest(name=name):
                if "expected_finding_count" in name:
                    self.assertEqual(
                        name, "expected_finding_count_is_not_a_contract"
                    )
                self.assertNotIn("target_count", name)
                self.assertNotIn("finding_count", name.replace(
                    "expected_finding_count_is_not_a_contract", ""
                ))

    def test_the_key_carries_the_literal_guard(self) -> None:
        key = json.loads(KEY_PATH.read_text(encoding="utf-8"))
        self.assertIs(key["expected_finding_count_is_not_a_contract"], True)

    def test_a_key_without_the_guard_is_refused(self) -> None:
        key = json.loads(KEY_PATH.read_text(encoding="utf-8"))
        del key["expected_finding_count_is_not_a_contract"]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "key.json"
            path.write_text(json.dumps(key), encoding="utf-8")

            with self.assertRaises(evaluator.EvalContractError):
                evaluator.load_key(path)


class IsolateCliWiringTests(unittest.TestCase):
    """T-10: the subcommand family's wiring, exit codes and mutual exclusions."""

    def test_the_docstring_subcommand_count_matches_the_parser(self) -> None:
        parser = evaluator.build_parser()
        subparsers = [
            action for action in parser._actions
            if isinstance(action, argparse._SubParsersAction)
        ][0]
        self.assertEqual(sorted(subparsers.choices), [
            "isolate", "materialize", "parse-report", "scan-leak", "score",
            "verify-fixture",
        ])
        # The module docstring's count is part of the contract: a sixth subcommand added
        # without updating it leaves the file lying about itself.
        self.assertIn("Six subcommands", evaluator.__doc__)
        self.assertEqual(len(subparsers.choices), 6)

    def test_repatriate_and_teardown_are_mutually_exclusive(self) -> None:
        completed = run_cli(
            "isolate", "--run-id", "r", "--repatriate", "/a", "--teardown", "/b"
        )
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("not allowed with", completed.stderr)

    def test_teardown_refuses_a_stranger_with_a_contract_exit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            completed = run_cli("isolate", "--run-id", "r", "--teardown", directory)
            self.assertEqual(
                completed.returncode, evaluator.EXIT_CONTRACT_VIOLATION
            )
            self.assertTrue(Path(directory).is_dir())

    def test_repatriate_without_a_report_is_a_contract_exit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            completed = run_cli("isolate", "--run-id", "r", "--repatriate", directory)
            self.assertEqual(
                completed.returncode, evaluator.EXIT_CONTRACT_VIOLATION
            )

    def test_a_session_base_inside_the_repository_is_a_contract_exit(self) -> None:
        inside = REPO_ROOT / "artifacts" / "_t10_probe"
        inside.mkdir(parents=True, exist_ok=True)
        try:
            completed = run_cli(
                "isolate", "--run-id", "r", "--session-base", str(inside),
                "--enforcement", "none", "--no-plant",
            )
            self.assertEqual(
                completed.returncode, evaluator.EXIT_CONTRACT_VIOLATION
            )
            self.assertEqual(list(inside.iterdir()), [])
        finally:
            import shutil

            shutil.rmtree(inside, ignore_errors=True)

    def test_an_unknown_enforcement_backend_is_rejected_by_the_parser(self) -> None:
        completed = run_cli("isolate", "--run-id", "r", "--enforcement", "bwrap")
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("invalid choice", completed.stderr)

    def test_a_leak_in_an_allowed_root_is_exit_four(self) -> None:
        import shutil

        with tempfile.TemporaryDirectory() as directory:
            planted = Path(directory) / "extra"
            planted.mkdir()
            shutil.copy2(str(KEY_PATH), str(planted / "harmless.json"))
            completed = run_cli(
                "isolate", "--run-id", "r", "--enforcement", "none", "--no-plant",
                "--allow-read", str(planted), "--session-base", directory,
            )
            self.assertEqual(completed.returncode, evaluator.EXIT_LEAK_OR_FIXTURE)
            self.assertIn("key material is reachable", completed.stderr)

    def test_the_imm_candidate_flag_replaces_the_default_list_and_defaults_to_it(
        self,
    ) -> None:
        """T-10: `--imm-candidate` wiring, asserted in-process so it runs on every host.

        The flag exists so a caller can supply FIXTURE-CONTROLLED Class IMM roots instead
        of inheriting one host's real `/dev`. Its behaviour end-to-end is a Seatbelt
        property and is darwin-only; its WIRING -- repeatable, replaces rather than
        extends, and falls back to the built-in default when absent -- is not, so it is
        pinned here rather than left to a platform-gated integration run.
        """
        import review_isolation

        for argv, expected in (
            (["--imm-candidate", "/bin", "--imm-candidate", "/sbin"], ("/bin", "/sbin")),
            ([], tuple(review_isolation.DEFAULT_IMM_CANDIDATES)),
        ):
            with self.subTest(argv=argv):
                with patch.object(review_isolation, "isolate") as isolate:
                    isolate.return_value = {
                        "session": "/s", "review_root": "/s/review_root",
                        "attestation": "/s/control/ISOLATION.json",
                        "launch_command": "AGENT", "scope_enforcement": "unenforced",
                        "properties": {"S1": "PASS", "S2": "FAIL", "S3": "FAIL"},
                    }
                    with patch("sys.stdout"), patch("sys.stderr"):
                        code = evaluator.main(
                            ["isolate", "--run-id", "r", "--enforcement", "none",
                             "--no-plant", *argv]
                        )
                self.assertEqual(code, evaluator.EXIT_OK)
                self.assertEqual(
                    isolate.call_args.kwargs["imm_candidates"], expected
                )

    def test_a_missing_policy_file_is_a_contract_exit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            completed = run_cli(
                "isolate", "--run-id", "r", "--enforcement", "none", "--no-plant",
                "--policy-file", "no/such/file.md", "--session-base", directory,
            )
            self.assertEqual(
                completed.returncode, evaluator.EXIT_CONTRACT_VIOLATION
            )


class AttemptDomainCliTests(unittest.TestCase):
    """T-13.2 -- DESIGN D-A.7.4 GATE 3, the shared `--attempt` CLI door.

    Asserting the EXIT CODE and not only the message is the point: it pins the `except`
    clause in `_dispatch_isolate()`. An `IsolationAttemptDomainError` that escaped to
    `main()` would traceback instead of printing `input error: ...`, because this file
    runs as `__main__` while `review_isolation` does its own `import final_review_eval`
    -- two `EvalInputError` classes, no subclass relationship across them. That is
    IMPLEMENTATION Finding F-503's defect, in this file, for this reason.
    """

    def assertRefused(self, completed, attempt: str) -> None:
        self.assertEqual(
            completed.returncode, evaluator.EXIT_INPUT_ERROR, completed.stderr
        )
        self.assertIn(
            f"input error: --attempt must be >= 1, got {int(attempt)}", completed.stderr
        )
        self.assertNotIn("Traceback", completed.stderr)

    def test_t132_the_repatriate_form_refuses_zero_and_negatives(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            for attempt in ("0", "-1"):
                with self.subTest(attempt=attempt):
                    self.assertRefused(
                        run_cli(
                            "isolate", "--run-id", "r", "--repatriate", directory,
                            "--attempt", attempt,
                        ),
                        attempt,
                    )

    def test_t132_the_teardown_form_refuses_them_too(self) -> None:
        # `--teardown` ignores `attempt` today, so this is what proves GATE 3 precedes
        # the branch rather than sitting inside one arm of it. A door that accepts a
        # nonsense value on one form is an open door.
        with tempfile.TemporaryDirectory() as directory:
            for attempt in ("0", "-1"):
                with self.subTest(attempt=attempt):
                    completed = run_cli(
                        "isolate", "--run-id", "r", "--teardown", directory,
                        "--attempt", attempt,
                    )
                    self.assertRefused(completed, attempt)
                    self.assertTrue(
                        Path(directory).is_dir(),
                        "a refused attempt must tear nothing down",
                    )

    def test_t132_a_valid_attempt_still_reaches_the_handler(self) -> None:
        # The regression half: gate 3 refuses the domain and nothing else. A valid
        # attempt still reaches `repatriate()`, which refuses this directory for its own
        # reason (no report) with the CONTRACT exit, not the input-error exit.
        with tempfile.TemporaryDirectory() as directory:
            completed = run_cli(
                "isolate", "--run-id", "r", "--repatriate", directory, "--attempt", "2"
            )
            self.assertEqual(
                completed.returncode, evaluator.EXIT_CONTRACT_VIOLATION, completed.stderr
            )


if __name__ == "__main__":
    unittest.main()
