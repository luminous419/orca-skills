#!/usr/bin/env python3
"""OS-49: the shipped contract blocks, and what must NOT have moved.

Two kinds of assertion live here. The first pins what OS-49 deliberately changed, at its
new exact value, so a later drift fails. The second pins what OS-49 must not have touched
-- the security boundary, the three independent axes, and the historical compatibility
evidence -- so "nothing else moved" is a gate rather than a claim.
"""
from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

from scripts import validate_skills
from scripts.skill_policy import CONTRACT_BLOCK_PATTERN, load_policy_contract

REPO_ROOT = Path(__file__).resolve().parents[1]
ORCHESTRATION = REPO_ROOT / "orca-worker-reviewer-orchestration" / "SKILL.md"
LOOP = REPO_ROOT / "orca-worker-reviewer-loop" / "SKILL.md"
COMPATIBILITY = REPO_ROOT / "docs" / "COMPATIBILITY.md"


def policy_contract(path: Path) -> dict:
    match = CONTRACT_BLOCK_PATTERN.search(path.read_text(encoding="utf-8"))
    assert match is not None, f"{path} carries no policy-contract block"
    return json.loads(match.group("contract"))


def anchor_block(path: Path, heading: str) -> list[str]:
    text = path.read_text(encoding="utf-8")
    start = text.index(heading)
    block = text[start:]
    opened = block.index("```text") + len("```text")
    closed = block.index("```", opened)
    return [
        line for line in block[opened:closed].strip().splitlines() if line.strip()
    ]


class SharedPolicyContractTests(unittest.TestCase):
    def test_the_two_contracts_are_still_deeply_equal(self) -> None:
        """The one change that can break both skills at once."""
        self.assertEqual(policy_contract(ORCHESTRATION), policy_contract(LOOP))

    def test_schema_versions_is_one_and_two_in_both(self) -> None:
        for path in (ORCHESTRATION, LOOP):
            with self.subTest(skill=path.parent.name):
                self.assertEqual(
                    policy_contract(path)["agent_profile"]["schema_versions"], [1, 2]
                )

    def test_the_two_model_error_codes_are_in_both_contracts(self) -> None:
        for path in (ORCHESTRATION, LOOP):
            with self.subTest(skill=path.parent.name):
                errors = policy_contract(path)["errors"]
                self.assertEqual(errors["invalid_agent_model"], "INVALID_AGENT_MODEL")
                self.assertEqual(
                    errors["agent_model_not_supported"], "AGENT_MODEL_NOT_SUPPORTED"
                )

    def test_both_codes_have_a_reason_line_in_both_skill_texts(self) -> None:
        for path in (ORCHESTRATION, LOOP):
            text = path.read_text(encoding="utf-8")
            for code in ("INVALID_AGENT_MODEL", "AGENT_MODEL_NOT_SUPPORTED"):
                with self.subTest(skill=path.parent.name, code=code):
                    self.assertIn(f"REASON: {code}", text)

    def test_the_validator_requires_both_codes(self) -> None:
        for code in ("INVALID_AGENT_MODEL", "AGENT_MODEL_NOT_SUPPORTED"):
            with self.subTest(code=code):
                self.assertIn(code, validate_skills.REQUIRED_ERROR_CODES)

    def test_there_is_no_second_name_for_the_independence_rule(self) -> None:
        """WORKER_REVIEWER_MUST_DIFFER is REUSED, never duplicated: only its KEY changed,
        from a command string to an effective (command, resolved model) identity."""
        for path in (ORCHESTRATION, LOOP):
            with self.subTest(skill=path.parent.name):
                codes = set(policy_contract(path)["errors"].values())
                self.assertIn("WORKER_REVIEWER_MUST_DIFFER", codes)
                self.assertNotIn("WORKER_REVIEWER_MODEL_MUST_DIFFER", codes)

    def test_no_dead_screaming_snake_twin_was_created(self) -> None:
        """A name with no producer is a constant a future reader has to reconcile against
        the live ones. Every case is named exactly once per layer instead."""
        for path in (ORCHESTRATION, LOOP):
            codes = set(policy_contract(path)["errors"].values())
            for absent in ("AGENT_MODEL_UNVERIFIED", "AGENT_MODEL_MISMATCH"):
                with self.subTest(skill=path.parent.name, code=absent):
                    self.assertNotIn(absent, codes)


class AgentProfileAnchorBlockTests(unittest.TestCase):
    """Orchestration-only: only this runtime has a pre-delivery verification barrier."""

    HEADING = "#### Agent profile contract"

    def test_the_block_holds_exactly_twenty_five_keys(self) -> None:
        lines = anchor_block(ORCHESTRATION, self.HEADING)
        self.assertEqual(len(lines), 25)
        self.assertEqual(validate_skills.AGENT_PROFILE_CONTRACT_MAX_LINES, 25)

    def test_the_block_and_the_validator_agree_on_keys_and_values(self) -> None:
        parsed = {}
        for line in anchor_block(ORCHESTRATION, self.HEADING):
            key, _, value = line.partition(" = ")
            parsed[key] = tuple(part.strip() for part in value.split(","))
        self.assertEqual(set(parsed), set(validate_skills.AGENT_PROFILE_CONTRACT))
        self.assertEqual(parsed, validate_skills.AGENT_PROFILE_CONTRACT)

    def test_the_seven_model_keys_are_present(self) -> None:
        keys = set(validate_skills.AGENT_PROFILE_CONTRACT)
        for key in (
            "AGENT_PROFILE_ROLE_VALUE",
            "AGENT_PROFILE_SCHEMA_V1_MEANING",
            "AGENT_PROFILE_MODEL_EVIDENCE_STATES",
            "AGENT_PROFILE_EFFECTIVE_IDENTITY",
            "AGENT_PROFILE_MODEL_GATES",
            "AGENT_PROFILE_MODEL_LIFECYCLE",
            "AGENT_PROFILE_MODEL_SELECTION",
        ):
            with self.subTest(key=key):
                self.assertIn(key, keys)

    def test_the_model_lifecycle_is_the_five_ordered_steps(self) -> None:
        """The ordering is a declared contract VALUE the validator compares, not prose a
        reader has to infer.

        CHANGED in OS-49 iteration 2 for review finding F-001, and the lock is not
        weakened by it: it is still an exact, ordered tuple equality. What changed is the
        lifecycle itself -- a same-command pair cannot be admitted role-by-role, so both
        sessions are attached up front and the PAIR is admitted before the first delivery
        of either role.
        """
        self.assertEqual(
            validate_skills.AGENT_PROFILE_CONTRACT["AGENT_PROFILE_MODEL_LIFECYCLE"],
            ("attach_both_sessions", "request_selection", "verify_resolved",
             "admit_pair", "deliver_task"),
        )

    def test_the_model_gates_name_pair_admission_before_the_first_delivery(self) -> None:
        """The third gate. The first two are per-ROLE and provably cannot establish
        Worker/Reviewer independence on one command, which is review finding F-001."""
        gates = validate_skills.AGENT_PROFILE_CONTRACT["AGENT_PROFILE_MODEL_GATES"]
        self.assertEqual(
            gates,
            ("declaration_before_run", "verification_before_delivery",
             "pair_admission_before_first_delivery"),
        )

    def test_the_gate_order_names_the_identity_gate_before_create_run(self) -> None:
        order = validate_skills.AGENT_PROFILE_CONTRACT["AGENT_PROFILE_GATE_ORDER"]
        self.assertIn("validate_effective_identity", order)
        self.assertLess(
            order.index("validate_effective_identity"), order.index("create_run")
        )

    def test_the_evidence_states_match_the_module(self) -> None:
        from scripts.agent_profile import MODEL_EVIDENCE_STATES

        self.assertEqual(
            validate_skills.AGENT_PROFILE_CONTRACT[
                "AGENT_PROFILE_MODEL_EVIDENCE_STATES"
            ],
            MODEL_EVIDENCE_STATES,
        )

    def test_no_error_code_string_appears_in_the_block(self) -> None:
        """The block's own rule: the shared contract owns error-code strings."""
        for line in anchor_block(ORCHESTRATION, self.HEADING):
            with self.subTest(line=line):
                self.assertNotIn("INVALID_AGENT", line)
                self.assertNotIn("AGENT_MODEL_NOT_SUPPORTED", line)

    def test_the_loop_skill_gains_no_agent_profile_anchor_block(self) -> None:
        self.assertNotIn(self.HEADING, LOOP.read_text(encoding="utf-8"))


class SessionReuseContractTests(unittest.TestCase):
    def test_nine_eligibility_members_in_both_the_text_and_the_validator(self) -> None:
        expected = (
            "same_role", "same_agent_command", "live_process",
            "previous_dispatch_settled", "ownership_transferable",
            "not_explicitly_retained", "not_coordinator_or_adopted",
            "not_in_lifecycle_recovery", "compatible_model_identity",
        )
        self.assertEqual(
            validate_skills.REUSE_CONTRACT["REUSE_ELIGIBILITY"], expected
        )
        text = ORCHESTRATION.read_text(encoding="utf-8")
        line = next(
            l for l in text.splitlines() if l.startswith("REUSE_ELIGIBILITY = ")
        )
        self.assertEqual(
            tuple(part.strip() for part in line.split(" = ", 1)[1].split(",")),
            expected,
        )

    def test_the_new_condition_is_last_so_the_eight_keep_their_order(self) -> None:
        self.assertEqual(
            validate_skills.REUSE_CONTRACT["REUSE_ELIGIBILITY"][-1],
            "compatible_model_identity",
        )

    def test_the_reuse_block_still_fits_its_cap(self) -> None:
        lines = anchor_block(ORCHESTRATION, "#### Session reuse contract")
        self.assertLessEqual(len(lines), validate_skills.REUSE_CONTRACT_MAX_LINES)


class TheSecurityBoundaryDidNotMoveTests(unittest.TestCase):
    def test_both_command_patterns_are_unchanged(self) -> None:
        for path in (ORCHESTRATION, LOOP):
            contract = policy_contract(path)
            with self.subTest(skill=path.parent.name):
                self.assertEqual(
                    contract["agent_command_pattern"], "[A-Za-z0-9._-]+"
                )
                self.assertEqual(
                    contract["custom_agent_command_pattern"],
                    "(?:claude|codex)-[A-Za-z0-9._-]+",
                )

    def test_agent_launch_arguments_is_still_empty(self) -> None:
        """`--model` never goes here. Adding it would invert a "this skill passes no vendor
        launch arguments" invariant for BOTH skills at once."""
        for path in (ORCHESTRATION, LOOP):
            with self.subTest(skill=path.parent.name):
                self.assertEqual(policy_contract(path)["agent_launch_arguments"], [])

    def test_no_vendor_launch_argument_appears_in_either_skill(self) -> None:
        for path in (ORCHESTRATION, LOOP):
            with self.subTest(skill=path.parent.name):
                self.assertNotIn(
                    "--dangerously-skip-permissions",
                    path.read_text(encoding="utf-8"),
                )

    def test_known_agent_commands_is_unchanged(self) -> None:
        """Removing `claude-gemma` is an OS-14 decision with its own compatibility
        consequences, and the contract ties `defaults` to this list."""
        for path in (ORCHESTRATION, LOOP):
            with self.subTest(skill=path.parent.name):
                self.assertEqual(
                    policy_contract(path)["known_agent_commands"],
                    ["claude", "codex", "claude-glm", "claude-gemma"],
                )

    def test_no_model_flag_reaches_a_launch_line(self) -> None:
        """The model never reaches argv at all in this release: the only real adapter
        declares no model capability, so a model-aware routing is refused before any
        process exists."""
        for path in (ORCHESTRATION, LOOP):
            text = path.read_text(encoding="utf-8")
            with self.subTest(skill=path.parent.name):
                self.assertNotIn("--model", text)
                self.assertNotIn("--effort", text)


class NoFourthAxisTests(unittest.TestCase):
    def test_canonical_independent_axes_unchanged(self) -> None:
        """Model identity lives INSIDE the agent_profile axis. It is not a fourth axis, and
        it never reads phases, risk, the quality profile or the decision policy."""
        from scripts import decision_policy

        self.assertEqual(
            decision_policy.CANONICAL_INDEPENDENT_AXES,
            ("risk", "quality_profile", "agent_profile"),
        )

    def test_the_validator_asserts_against_the_one_shared_tuple(self) -> None:
        """The validator IMPORTS the constant rather than transcribing it, so there is one
        source of truth and OS-49 could not have widened it in one place only."""
        source = (REPO_ROOT / "scripts" / "validate_skills.py").read_text("utf-8")
        self.assertIn("CANONICAL_INDEPENDENT_AXES,", source)
        self.assertIn(
            "policy.independent_axes == CANONICAL_INDEPENDENT_AXES", source
        )

    def test_no_model_axis_token_was_added_to_the_axis_vocabulary(self) -> None:
        from scripts import decision_policy

        self.assertNotIn("model", " ".join(decision_policy.AXIS_TOKENS).lower())
        self.assertEqual(len(decision_policy.CANONICAL_INDEPENDENT_AXES), 3)


class RedactionPolicyVersionTests(unittest.TestCase):
    """OS-49 BUGFIX (review N2). Which version axis moves when, locked.

    The finding's OBSERVATION is true: OS-49 grew
    `FINAL_REVIEW_REDACTED_METADATA_FIELDS` by five fields and left
    `FINAL_REVIEW_REDACTION_POLICY_VERSION` at `redaction/1.1`. Its CONCLUSION -- that the
    policy version must therefore be bumped -- is not what the contract says, and these
    tests are the evidence, so the question cannot be re-litigated from memory:

      * `redaction/MAJOR.MINOR` denotes the TEXT TRANSFORMATION. `redact_text()` is a pure
        function of (text, policy_version) and admits exactly ONE version, because a digest
        is only comparable to a digest produced under the same policy.
        docs/COMPATIBILITY.md states the version's meaning as its five ordered categories
        and names ADDING A CATEGORY as the MINOR bump. OS-49 added none.
      * Bumping it would make every historical `*_digest_post_redaction` unverifiable
        through the single-version function -- the same reason a MAJOR bump of the audit
        schema is explicitly FORBIDDEN.
      * FIELD COVERAGE is versioned by `FINAL_REVIEW_AUDIT_SCHEMA_VERSION` (bumped 1.0 ->
        1.1 by OS-49 for exactly those five fields) and is RECORDED per record as
        `metadata_redaction.covered_fields`, so no reader infers coverage from the policy
        string.
    """

    #: The five ordered categories `redaction/1.1` IS. A change here without a policy bump
    #: is what this lock exists to fail.
    CATEGORIES_AT_1_1 = (
        "orca_dispatch_capability",
        "url_credential",
        "env_secret_pattern",
        "absolute_local_path",
        "foreign_absolute_path",
    )

    #: The five fields OS-49 added to the COVERAGE set -- the other axis entirely.
    OS49_COVERED_FIELDS = (
        "reviewer_requested_model",
        "reviewer_resolved_model",
        "reviewer_model_state",
        "reviewer_model_request_method",
        "reviewer_model_request_evidence",
    )

    def test_the_policy_version_denotes_the_category_tuple(self) -> None:
        from scripts import run_logging

        self.assertEqual(
            run_logging.FINAL_REVIEW_REDACTION_POLICY_VERSION, "redaction/1.1"
        )
        self.assertEqual(
            tuple(name for name, _pattern, _replacement in run_logging.REDACTION_CATEGORIES),
            self.CATEGORIES_AT_1_1,
        )

    def test_the_compatibility_record_states_the_version_in_terms_of_categories(self) -> None:
        """The contract reading, read from the document rather than asserted."""
        text = COMPATIBILITY.read_text(encoding="utf-8")
        self.assertIn(
            "**Redaction policy `redaction/1.1` covers POSIX paths only.**", text
        )
        self.assertIn("The policy has five ordered", text)
        self.assertIn("is a MINOR policy bump", text)

    def test_the_coverage_set_moved_the_audit_schema_version_instead(self) -> None:
        from scripts import run_logging

        for field in self.OS49_COVERED_FIELDS:
            with self.subTest(field=field):
                self.assertIn(field, run_logging.FINAL_REVIEW_REDACTED_METADATA_FIELDS)
        self.assertEqual(run_logging.FINAL_REVIEW_AUDIT_SCHEMA_VERSION, "1.1")
        # MAJOR unchanged, which is the half that keeps historical 1.0 records readable.
        self.assertEqual(
            run_logging.FINAL_REVIEW_AUDIT_SCHEMA_VERSION.split(".")[0], "1"
        )

    def test_coverage_is_recorded_in_every_record_not_inferred_from_the_version(self) -> None:
        """Why one policy version cannot identify two coverage sets: it identifies none.
        Each record carries its own `covered_fields`, so a reader compares sets rather than
        deducing them from a string."""
        from scripts import run_logging

        source = Path(run_logging.__file__).read_text(encoding="utf-8")
        self.assertIn(
            '"covered_fields": list(FINAL_REVIEW_REDACTED_METADATA_FIELDS),', source
        )
        self.assertIn(
            '"redaction_policy_version": FINAL_REVIEW_REDACTION_POLICY_VERSION,', source
        )

    def test_a_digest_under_the_recorded_policy_is_still_reproducible(self) -> None:
        """The property a bump would have broken, demonstrated rather than argued: the
        policy admits exactly one version, so a historical digest is re-derivable only while
        this string is the one those records name."""
        from scripts import run_logging

        # A FOREIGN absolute path, never a home-rooted literal: `release_manifest`'s source
        # scan refuses a user-name spelling in any packaged file, which is the same posture
        # the policy under test implements.
        sample = "see /opt/evidence/report.md and dcap_" + "A" * 48
        first, counts = run_logging.redact_text(sample)
        second, again = run_logging.redact_text(
            sample, policy_version=run_logging.FINAL_REVIEW_REDACTION_POLICY_VERSION
        )
        self.assertEqual(first, second)
        self.assertEqual(counts, again)
        with self.assertRaises(run_logging.RunLoggingError):
            run_logging.redact_text(sample, policy_version="redaction/1.2")

    def test_the_two_axes_are_documented_at_the_constant(self) -> None:
        """A future reader must find the distinction where the constant is, not only here."""
        from scripts import run_logging

        source = Path(run_logging.__file__).read_text(encoding="utf-8")
        head = source[: source.index('FINAL_REVIEW_REDACTION_POLICY_VERSION = "redaction/1.1"')]
        tail = head[head.index("FINAL_REVIEW_AUDIT_SCHEMA_VERSION = \"1.1\""):]
        self.assertIn("TWO version axes", tail)
        self.assertIn("REDACTION_CATEGORIES", tail)
        self.assertIn("covered_fields", tail)


class HistoricalEvidenceIsUntouchedTests(unittest.TestCase):
    #: The three historical validation rows, byte for byte as they shipped. OS-49 appends
    #: only; it rewrites no historical compatibility evidence.
    HISTORICAL_LINES = (
        "claude-glm",
        "claude-gemma",
    )

    def test_the_historical_glm_and_gemma_rows_are_still_present(self) -> None:
        text = COMPATIBILITY.read_text(encoding="utf-8")
        for needle in self.HISTORICAL_LINES:
            with self.subTest(needle=needle):
                self.assertIn(needle, text)

    def test_no_document_claims_glm_5_validation(self) -> None:
        """Company-environment validation and the GLM-5.2 vs GLM-5.3-flash Worker/Reviewer
        assignment are OS-14's. No doc may claim either here."""
        claim = re.compile(r"(?i)\b(glm-5\.2|glm-5\.3-flash)\b[^\n]*\b(verified|validated)\b")
        for path in sorted((REPO_ROOT / "docs").rglob("*.md")):
            text = path.read_text(encoding="utf-8")
            with self.subTest(path=path.relative_to(REPO_ROOT)):
                self.assertIsNone(claim.search(text), path)

    def test_no_verified_row_was_added_for_a_glm_5_model(self) -> None:
        text = COMPATIBILITY.read_text(encoding="utf-8")
        for line in text.splitlines():
            if "glm-5.2" in line.lower() or "glm-5.3" in line.lower():
                with self.subTest(line=line.strip()[:80]):
                    self.assertNotIn("VERIFIED", line)


if __name__ == "__main__":
    unittest.main()
