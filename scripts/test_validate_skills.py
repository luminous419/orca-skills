#!/usr/bin/env python3
"""Regression tests for validate_skills.py using disposable repository copies."""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import run_logging


SOURCE_ROOT = Path(__file__).resolve().parents[1]
SKILL_NAMES = (
    "orca-worker-reviewer-loop",
    "orca-worker-reviewer-orchestration",
)


class ValidatorRegressionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.repo_root = Path(self.temporary_directory.name) / "repo"
        self.repo_root.mkdir()

        for filename in (
            "README.md",
            "INSTALL.md",
            "VERSION",
            "CHANGELOG.md",
            "docs/ROADMAP.md",
            "docs/COMPATIBILITY.md",
            "docs/RELEASING.md",
            "docs/LICENSE-DECISION.md",
            "docs/examples/FULL_WORKFLOW_FAIL_CORRECTION.md",
            "docs/examples/FULL_WORKFLOW_FAIL_CORRECTION.ko.md",
            "docs/validation/GLM_GEMMA_SMOKE_PROCEDURE.md",
            "docs/validation/historical/GLM_GEMMA_SMOKE_REPORT_2026-08-20.md",
        ):
            destination = self.repo_root / filename
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(SOURCE_ROOT / filename, destination)
        for skill_name in SKILL_NAMES:
            shutil.copytree(SOURCE_ROOT / skill_name, self.repo_root / skill_name)

        scripts_dir = self.repo_root / "scripts"
        scripts_dir.mkdir()
        for filename in (
            "validate_skills.py",
            "skill_policy.py",
            "workflow_contract.py",
            "run_logging.py",
            # OS-4: skill_policy imports agent_profile, which imports the YAML
            # reader in quality_profile. The validator runs as a subprocess in this
            # copied tree, so a missing dependency here is an import crash with an
            # empty stdout rather than the named failure a test is asserting on.
            "agent_profile.py",
            "quality_profile.py",
            # OS-28: validate_skills imports the decision-policy loader. Same
            # trap as the OS-4 note above -- omitting it here turns every
            # validator regression test into an import crash with empty stdout.
            "decision_policy.py",
        ):
            shutil.copy2(SOURCE_ROOT / "scripts" / filename, scripts_dir)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def run_validator(self) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "scripts/validate_skills.py"],
            cwd=self.repo_root,
            text=True,
            capture_output=True,
            check=False,
        )

    # ---- OS-28 decision policy contract (DESIGN D4-E) ------------------------
    # These are the permanent form of the DESIGN D5-1 mutations: each one applies a
    # weakening and asserts the validator fails with its NAMED message, so a check
    # that stops working is a red test rather than a quiet gap.

    DECISION_SKILLS = (
        "orca-worker-reviewer-orchestration",
        "orca-worker-reviewer-loop",
    )

    def edit_skills(self, old: str, new: str, *skills: str) -> None:
        """Replace `old` with `new` in each named SKILL.md, asserting it was there."""
        for name in skills or self.DECISION_SKILLS:
            path = self.repo_root / name / "SKILL.md"
            text = path.read_text(encoding="utf-8")
            self.assertIn(old, text, f"{name}: mutation target not found")
            path.write_text(text.replace(old, new, 1), encoding="utf-8")

    def assert_validator_fails_with(self, fragment: str) -> None:
        result = self.run_validator()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn(fragment, result.stdout)

    def test_decision_policy_contract_removed_fails(self) -> None:
        self.edit_skills('"decision_policy": {', '"decision_policy_removed": {')
        self.assert_validator_fails_with("decision policy contract is missing or malformed")

    def test_decision_policy_single_skill_drift_fails(self) -> None:
        """M-2: one Skill only. The existing whole-dict deep-equality catches this."""
        self.edit_skills(
            '"citation_minimum": {"CONFLICT": 2}',
            '"citation_minimum": {"CONFLICT": 3}',
            "orca-worker-reviewer-loop",
        )
        self.assert_validator_fails_with(
            "machine-readable policy contracts differ between skills"
        )

    def test_decision_policy_reason_code_removed_from_both_skills_fails(self) -> None:
        """M-3: the simultaneous-deletion blind spot. Deep-equality still passes here,
        so this is the mutation that justifies the expected Python constant."""
        self.edit_skills(
            '      "privacy_impact": {"state": "NEEDS_INPUT", "clause": "N-1", '
            '"boundary_element": "privacy"},\n',
            "",
        )
        self.assert_validator_fails_with("decision policy reason-code cardinality drifted")

    def test_decision_policy_value_drift_fails(self) -> None:
        self.edit_skills('"clause": "N-1", "boundary_element": "privacy"',
                         '"clause": "N-2", "boundary_element": "privacy"')
        self.assert_validator_fails_with("decision policy contract values drifted")

    def test_decision_policy_forbidden_transition_relaxed_fails(self) -> None:
        """M-4: the T-F2 cell. Relaxing it must not be silently acceptable."""
        self.edit_skills(
            '"NEEDS_INPUT": {"CLEAR": "requires_user_decision", '
            '"ASSUMPTION_ALLOWED": "forbidden"',
            '"NEEDS_INPUT": {"CLEAR": "requires_user_decision", '
            '"ASSUMPTION_ALLOWED": "allowed"',
        )
        self.assert_validator_fails_with(
            "NEEDS_INPUT/CONFLICT -> ASSUMPTION_ALLOWED must be forbidden"
        )

    def test_decision_policy_inv4_exception_fails(self) -> None:
        """M-6: INV-4 has no exception (A4-0)."""
        self.edit_skills('"exception_allowed": false', '"exception_allowed": true')
        self.assert_validator_fails_with("INV-4 must have no exception")

    def test_decision_policy_reject_list_trimmed_fails(self) -> None:
        """M-7: model confidence is never authority."""
        self.edit_skills('"model_confidence", ', "")
        self.assert_validator_fails_with("forbidden-authority reject list drifted")

    def test_decision_policy_unclassified_key_fails(self) -> None:
        """M-18 / C11a: a new top-level key must be classified, or the enumeration
        would silently become incomplete."""
        self.edit_skills(
            '"citation_minimum": {"CONFLICT": 2}',
            '"risk_overrides": {}, "citation_minimum": {"CONFLICT": 2}',
        )
        self.assert_validator_fails_with("decision policy contract is missing or malformed")

    def test_decision_policy_axis_token_in_a_selection_input_fails(self) -> None:
        """M-16 / C11b: an exact axis token inside a state-selection input."""
        self.edit_skills(
            '"explicit_user_authority_reserved": true',
            '"risk_in": ["high"],\n      "explicit_user_authority_reserved": true',
        )
        self.assert_validator_fails_with(
            "references axis token at ['assumption_allowed_forbidden_when/risk_in[0] "
            "(value)'], which is a state-selection input"
        )

    def test_decision_policy_transition_value_outside_closed_set_fails(self) -> None:
        """M-17 / C11c: a risk-conditional value carrying NO exact axis token. This is
        the mutation the token rule misses, and the reason C11c exists."""
        self.edit_skills(
            '"NEEDS_INPUT": {"CLEAR": "requires_user_decision"',
            '"NEEDS_INPUT": {"CLEAR": "requires_user_decision_unless_risk_low"',
        )
        self.assert_validator_fails_with("is outside the closed set")

    def test_decision_policy_independent_axes_drift_fails(self) -> None:
        """M-19 / C11d: checked by positive equality, so the declarative position
        cannot forbid itself."""
        self.edit_skills(
            '"independent_axes": ["risk", "quality_profile", "agent_profile"]',
            '"independent_axes": ["risk"]',
        )
        self.assert_validator_fails_with("decision policy contract is missing or malformed")

    def test_decision_policy_prose_anchor_removed_from_both_skills_fails(self) -> None:
        """M-11: byte-equality cannot see a sentence deleted from BOTH copies."""
        self.edit_skills("INV-4에는 예외가 없다", "INV-4는 중요하다")
        self.assert_validator_fails_with("missing decision policy prose anchor")

    def test_decision_record_optionality_sentence_removed_fails(self) -> None:
        """M-13 / UD-1: making the section required would violate a user decision."""
        for name in self.DECISION_SKILLS:
            for path in sorted((self.repo_root / name / "templates").glob("*.md")):
                text = path.read_text(encoding="utf-8")
                path.write_text(
                    text.replace(
                        "optional section이다. 없어도 계약 위반이 아니다.",
                        "필수 section이다.",
                    ),
                    encoding="utf-8",
                )
        self.assert_validator_fails_with("missing the decision record optionality sentence")

    # ---- TEST phase: the semantic core, found unpinned by mutation ------------
    # Each of these five mutations passed EVERY check before C15-C23 existed. They
    # are the reason closed-set membership is not sufficient: "continue" is a legal
    # workflow value, so C11c was satisfied while the state that must pause declared
    # that it continues.

    def test_decision_policy_needs_input_workflow_flipped_to_continue_fails(self) -> None:
        """N-1: the most severe. NEEDS_INPUT declaring "continue" defeats bounded
        autonomy outright, and "continue" is inside the closed workflow set."""
        self.edit_skills(
            '"NEEDS_INPUT": {"workflow": "pause_and_ask"',
            '"NEEDS_INPUT": {"workflow": "continue"',
        )
        self.assert_validator_fails_with("decision policy state semantics drifted")

    def test_decision_policy_assumption_allowed_review_dropped_fails(self) -> None:
        """N-13: continue_and_review -> continue drops the review the ROADMAP requires
        of a recorded assumption."""
        self.edit_skills(
            '"ASSUMPTION_ALLOWED": {"workflow": "continue_and_review"',
            '"ASSUMPTION_ALLOWED": {"workflow": "continue"',
        )
        self.assert_validator_fails_with("decision policy state semantics drifted")

    def test_decision_policy_user_decision_requirement_dropped_fails(self) -> None:
        """N-14: NEEDS_INPUT no longer declaring that it needs a user decision."""
        self.edit_skills(
            '"NEEDS_INPUT": {"workflow": "pause_and_ask", "user_decision_required": true',
            '"NEEDS_INPUT": {"workflow": "pause_and_ask", "user_decision_required": false',
        )
        self.assert_validator_fails_with("decision policy state semantics drifted")

    def test_decision_policy_inv4_blast_radius_clause_emptied_fails(self) -> None:
        """N-5: INV-4's blast-radius half silently removed."""
        self.edit_skills(
            '"blast_radius_in_with_irreversible": ["repository", "external_system"],',
            '"blast_radius_in_with_irreversible": [],',
        )
        self.assert_validator_fails_with("INV-4 forbidden-when conditions drifted")

    def test_decision_policy_aggregate_order_inverted_fails(self) -> None:
        """N-8: inverting the order would let a check report CLEAR while an item is
        in CONFLICT."""
        self.edit_skills(
            '"aggregate_order": ["CONFLICT", "NEEDS_INPUT", "ASSUMPTION_ALLOWED", "CLEAR"]',
            '"aggregate_order": ["CLEAR", "ASSUMPTION_ALLOWED", "NEEDS_INPUT", "CONFLICT"]',
        )
        self.assert_validator_fails_with("decision policy aggregate order drifted")

    def test_decision_policy_assumption_requires_determining_source_fails(self) -> None:
        """N-4: INV-3 weakened to accept a determining policy source."""
        self.edit_skills(
            '"assumption_allowed_requires": {"policy_source_role": "supports"',
            '"assumption_allowed_requires": {"policy_source_role": "determines"',
        )
        self.assert_validator_fails_with("INV-3 assumption requirements drifted")

    def test_decision_policy_user_decision_fields_trimmed_fails(self) -> None:
        """N-6: INV-5's evidence requirement weakened."""
        self.edit_skills(
            '"user_decision_fields": ["source", "where_recorded", "resolves"]',
            '"user_decision_fields": ["source"]',
        )
        self.assert_validator_fails_with("user_decision required fields drifted")

    def test_decision_policy_citation_minimum_lowered_fails(self) -> None:
        """N-3: CONFLICT needs two citations by definition -- one side is not a
        contradiction."""
        self.edit_skills(
            '"citation_minimum": {"CONFLICT": 2}', '"citation_minimum": {"CONFLICT": 1}'
        )
        self.assert_validator_fails_with("CONFLICT citation minimum drifted")

    def test_decision_policy_downstream_rule_emptied_fails(self) -> None:
        """N-12: T-F6 silently removed."""
        self.edit_skills(
            '"downstream_rule": "an unresolved NEEDS_INPUT or CONFLICT item may not '
            'be reported CLEAR by a later phase"',
            '"downstream_rule": ""',
        )
        self.assert_validator_fails_with("downstream rule drifted")

    def test_decision_policy_entry_clause_prose_edited_in_both_skills_fails(self) -> None:
        """M-21, two-file variant. DESIGN F-5 records the THREE-file coordinated
        variant -- both Skills plus this expected constant -- as a residual gap that
        only human diff review catches. That remains true; this closes the cheaper
        variant, where the Skills are edited and the constant is not."""
        self.edit_skills(
            '"N-1": "a boundary element is true, is not determined by a policy source, '
            'and is not decided by an explicit authorization"',
            '"N-1": "a boundary element is true, unless the run risk is low"',
        )
        self.assert_validator_fails_with("entry clause text drifted")

    # ---- FR-2: user authority is an allowlist ---------------------------------

    def test_decision_policy_user_authority_vocabulary_drift_fails(self) -> None:
        """C24. Widening the positive vocabulary is how a denylist bypass would come
        back -- adding one permissive spelling reopens the whole hole."""
        self.edit_skills(
            '"user_decision_sources": ["explicit_user_reply", '
            '"prior_explicit_user_authorization"]',
            '"user_decision_sources": ["explicit_user_reply", '
            '"prior_explicit_user_authorization", "high_confidence"]',
        )
        self.assert_validator_fails_with("user-authority positive vocabulary drifted")

    def test_decision_policy_user_authority_vocabulary_emptied_fails(self) -> None:
        """Over-blocking guard: an empty allowlist makes every user decision
        unrepresentable, which the ticket also calls a wrong implementation."""
        self.edit_skills(
            '"user_decision_sources": ["explicit_user_reply", '
            '"prior_explicit_user_authorization"]',
            '"user_decision_sources": []',
        )
        self.assert_validator_fails_with("decision policy contract is missing or malformed")

    def test_decision_policy_forbidden_source_admitted_as_authority_fails(self) -> None:
        """C25. The retained denylist's remaining job: the two sets must stay
        disjoint, so a forbidden category cannot be promoted into the allowlist."""
        self.edit_skills(
            '"user_decision_sources": ["explicit_user_reply", '
            '"prior_explicit_user_authorization"]',
            '"user_decision_sources": ["explicit_user_reply", "recommended_default"]',
        )
        self.assert_validator_fails_with("decision policy contract is missing or malformed")

    # ---- FR-1: the transition matrix is pinned BY VALUE ----------------------
    # C8 compared only the set of `forbidden` cells and C11c only closed-set
    # membership, so relaxing an authority-requiring edge to the equally legal
    # "allowed" left the validator green. These two edges carry the contract's
    # central promise: an unresolved NEEDS_INPUT or CONFLICT cannot continue, and
    # reaching CLEAR takes a real user decision.

    def test_needs_input_to_clear_relaxed_to_allowed_fails(self) -> None:
        """FR-1, first required edge. Before C26/C26a this mutation left
        `Skill validation PASSED` at exit 0."""
        self.edit_skills(
            '"NEEDS_INPUT": {"CLEAR": "requires_user_decision",',
            '"NEEDS_INPUT": {"CLEAR": "allowed",',
        )
        self.assert_validator_fails_with(
            "NEEDS_INPUT -> CLEAR must require a user decision"
        )

    def test_conflict_to_clear_relaxed_to_allowed_fails(self) -> None:
        """FR-1, second required edge."""
        self.edit_skills(
            '"CONFLICT": {"CLEAR": "requires_user_decision",',
            '"CONFLICT": {"CLEAR": "allowed",',
        )
        self.assert_validator_fails_with(
            "CONFLICT -> CLEAR must require a user decision"
        )

    def test_authority_edge_relaxed_to_a_retraction_also_fails(self) -> None:
        """A subtler relaxation: still not `allowed`, still not a user decision.
        The named edge check must reject any value that is not
        `requires_user_decision`, not merely the `allowed` spelling."""
        self.edit_skills(
            '"NEEDS_INPUT": {"CLEAR": "requires_user_decision",',
            '"NEEDS_INPUT": {"CLEAR": "requires_retraction",',
        )
        self.assert_validator_fails_with(
            "NEEDS_INPUT -> CLEAR must require a user decision"
        )

    def test_assumption_allowed_to_clear_relaxed_fails(self) -> None:
        """The retraction edge is pinned by the full-matrix check even though it is
        not an authority edge."""
        self.edit_skills(
            '"ASSUMPTION_ALLOWED": {"CLEAR": "requires_retraction",',
            '"ASSUMPTION_ALLOWED": {"CLEAR": "allowed",',
        )
        self.assert_validator_fails_with("the transition matrix drifted")

    def test_a_permissive_cell_tightened_to_forbidden_also_fails(self) -> None:
        """The matrix is pinned in both directions. Silently forbidding a legal edge
        is drift too -- over-restriction is the mirror defect of relaxation."""
        self.edit_skills(
            '"CLEAR": {"CLEAR": "allowed", "ASSUMPTION_ALLOWED": "allowed",',
            '"CLEAR": {"CLEAR": "allowed", "ASSUMPTION_ALLOWED": "forbidden",',
        )
        self.assert_validator_fails_with("the transition matrix drifted")

    # ---- FR-1 sweep: four more keys checked only by name or membership --------

    def test_boundary_element_enum_values_emptied_fails(self) -> None:
        """`reversibility` kept its name while losing every value it can take."""
        self.edit_skills(
            '"values": ["reversible_in_run", "reversible_with_effort", "irreversible"]',
            '"values": []',
        )
        # The loader's triggering-vs-values consistency check now fires FIRST and
        # names the exact inconsistency, so the mutation is caught earlier and more
        # precisely than by C27's value pin. Still caught; the message moved.
        self.assert_validator_fails_with(
            "names triggering value(s) ['irreversible'] that are not in its own "
            "value set []"
        )

    def test_blast_radius_dropping_the_out_of_scope_values_fails(self) -> None:
        """INV-4's blast-radius clause names `repository` and `external_system`;
        removing them from the element would make that clause unreachable."""
        self.edit_skills(
            '"values": ["current_change", "module", "repository", "external_system"]',
            '"values": ["current_change", "module"]',
        )
        # Same: removing the two values INV-4's clause names now also orphans this
        # element's triggering list, which the loader reports directly.
        self.assert_validator_fails_with(
            "names triggering value(s) ['repository', 'external_system'] that are "
            "not in its own value set ['current_change', 'module']"
        )

    def test_conflict_citation_minimum_on_the_element_lowered_fails(self) -> None:
        """A second citation minimum lives on the boundary element; lowering it there
        was invisible even though C20 pins the one in `citation_minimum`."""
        self.edit_skills(
            '"explicit_requirement_conflict": {"kind": "citations", "minimum": 2, '
            '"triggering": "at_minimum"}',
            '"explicit_requirement_conflict": {"kind": "citations", "minimum": 1, '
            '"triggering": "at_minimum"}',
        )
        self.assert_validator_fails_with("boundary element specifications drifted")

    def test_policy_source_roles_dropping_supports_fails(self) -> None:
        """INV-3 requires the role `supports`; removing it from the contract would
        make ASSUMPTION_ALLOWED unreachable."""
        self.edit_skills(
            '"policy_source_roles": ["determines", "supports"]',
            '"policy_source_roles": ["determines"]',
        )
        self.assert_validator_fails_with("policy source roles or kinds drifted")

    def test_policy_source_kinds_widened_fails(self) -> None:
        """Admitting a new evidence kind is a contract change, not a free extension."""
        self.edit_skills(
            '"policy_source_kinds": ["file_path"',
            '"policy_source_kinds": ["model_hunch", "file_path"',
        )
        self.assert_validator_fails_with("policy source roles or kinds drifted")

    def test_state_scope_reversed_fails(self) -> None:
        """OQ-1 settled on per-item states with a derived aggregate; reverting to
        per-check would make the downstream rule undefinable."""
        self.edit_skills(
            '"state_scope": "per_decision_item_with_derived_check_aggregate"',
            '"state_scope": "per_check_only"',
        )
        self.assert_validator_fails_with("decision state scope drifted")

    # ---- FR-4: entry conditions and triggering values are pinned --------------

    def test_clear_entry_condition_widened_fails(self) -> None:
        """permitted_states() evaluates these, so widening CLEAR's condition moves the
        authority boundary itself."""
        self.edit_skills(
            '"CLEAR": {"any_of": ["no_open_decision_item", "determining_policy_source", '
            '"explicit_user_authorization"]}',
            '"CLEAR": {"any_of": ["no_open_decision_item", "determining_policy_source", '
            '"explicit_user_authorization", "unclassifiable_item"]}',
        )
        self.assert_validator_fails_with("state entry conditions drifted")

    def test_assumption_allowed_entry_condition_weakened_fails(self) -> None:
        """Dropping a conjunct from an all_of condition is a relaxation."""
        self.edit_skills(
            '"all_of": ["reversible_in_run", "blast_radius_within_scope", '
            '"no_high_impact_element", "supporting_policy_source", '
            '"no_reserved_user_authority"]',
            '"all_of": ["reversible_in_run", "supporting_policy_source", '
            '"no_reserved_user_authority"]',
        )
        self.assert_validator_fails_with("state entry conditions drifted")

    def test_needs_input_entry_condition_narrowed_fails(self) -> None:
        """Narrowing the pausing state's condition is the FR-4 defect's shape."""
        self.edit_skills(
            '"NEEDS_INPUT": {"any_of": ["undetermined_boundary_element", '
            '"absent_user_intent", "unclassifiable_item"]}',
            '"NEEDS_INPUT": {"any_of": ["absent_user_intent", "unclassifiable_item"]}',
        )
        self.assert_validator_fails_with("state entry conditions drifted")

    def test_a_triggering_value_removed_fails(self) -> None:
        """`irreversible` no longer triggering would silently stop escalating
        irreversible items."""
        self.edit_skills(
            '"triggering": ["irreversible"]', '"triggering": []'
        )
        self.assert_validator_fails_with("boundary element specifications drifted")

    def test_blast_radius_triggering_narrowed_fails(self) -> None:
        self.edit_skills(
            '"triggering": ["repository", "external_system"]',
            '"triggering": ["external_system"]',
        )
        self.assert_validator_fails_with("boundary element specifications drifted")

    def test_a_boolean_element_made_non_triggering_fails(self) -> None:
        self.edit_skills(
            '"security": {"kind": "boolean", "triggering": true}',
            '"security": {"kind": "boolean", "triggering": null}',
        )
        self.assert_validator_fails_with("boundary element specifications drifted")

    # ---- RI3-1: authority precedence -----------------------------------------

    def test_authority_precedence_emptied_fails(self) -> None:
        """Emptying the list restores the RI3-1 defect: a determining policy source
        would again un-reserve user authority and arbitrate a contradiction."""
        self.edit_skills(
            '"policy_source_cannot_resolve": ["explicit_user_authority", '
            '"explicit_requirement_conflict"]',
            '"policy_source_cannot_resolve": []',
        )
        self.assert_validator_fails_with("authority precedence drifted")

    def test_authority_precedence_losing_the_reserved_authority_cell_fails(self) -> None:
        self.edit_skills(
            '"policy_source_cannot_resolve": ["explicit_user_authority", '
            '"explicit_requirement_conflict"]',
            '"policy_source_cannot_resolve": ["explicit_requirement_conflict"]',
        )
        self.assert_validator_fails_with("authority precedence drifted")

    def test_authority_precedence_losing_the_conflict_cell_fails(self) -> None:
        self.edit_skills(
            '"policy_source_cannot_resolve": ["explicit_user_authority", '
            '"explicit_requirement_conflict"]',
            '"policy_source_cannot_resolve": ["explicit_user_authority"]',
        )
        self.assert_validator_fails_with("authority precedence drifted")

    def test_valid_repository_passes(self) -> None:
        result = self.run_validator()

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Skill validation PASSED", result.stdout)

    def test_missing_required_error_code_fails(self) -> None:
        skill_path = (
            self.repo_root / "orca-worker-reviewer-orchestration" / "SKILL.md"
        )
        text = skill_path.read_text(encoding="utf-8")
        skill_path.write_text(
            text.replace("INVALID_MAX_ITERATIONS", "REMOVED_MAX_ITERATIONS_ERROR"),
            encoding="utf-8",
        )

        result = self.run_validator()

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("missing required error code INVALID_MAX_ITERATIONS", result.stdout)

    def test_shared_template_drift_fails(self) -> None:
        template_path = (
            self.repo_root
            / "orca-worker-reviewer-orchestration"
            / "templates"
            / "analysis.md"
        )
        template_path.write_text(
            template_path.read_text(encoding="utf-8") + "\nDrift.\n",
            encoding="utf-8",
        )

        result = self.run_validator()

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("templates/analysis.md differs between skills", result.stdout)

    def test_installed_run_logging_tool_drift_fails(self) -> None:
        """OS-17 review round 3 MAJOR-1: the installed Skill's own copy of
        run_logging.py must stay byte-identical to scripts/run_logging.py, or a
        Coordinator using the installed copy silently runs different logic than
        this repository's own tests exercise."""
        installed_path = (
            self.repo_root
            / "orca-worker-reviewer-orchestration"
            / "tools"
            / "run_logging.py"
        )
        installed_path.write_text(
            installed_path.read_text(encoding="utf-8") + "\n# drift\n",
            encoding="utf-8",
        )

        result = self.run_validator()

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn(
            "tools/run_logging.py differs from scripts/run_logging.py",
            result.stdout,
        )

    def test_missing_installed_run_logging_tool_fails(self) -> None:
        installed_path = (
            self.repo_root
            / "orca-worker-reviewer-orchestration"
            / "tools"
            / "run_logging.py"
        )
        installed_path.unlink()

        result = self.run_validator()

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("tools/run_logging.py is missing", result.stdout)

    def test_human_readable_default_drift_fails(self) -> None:
        skill_path = (
            self.repo_root / "orca-worker-reviewer-orchestration" / "SKILL.md"
        )
        text = skill_path.read_text(encoding="utf-8")
        skill_path.write_text(
            text.replace("DEFAULT_MAX_ITERATIONS = 5", "DEFAULT_MAX_ITERATIONS = 6", 1),
            encoding="utf-8",
        )

        result = self.run_validator()

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("human-readable defaults differ from contract", result.stdout)

    def test_human_readable_known_command_drift_fails(self) -> None:
        skill_path = (
            self.repo_root / "orca-worker-reviewer-orchestration" / "SKILL.md"
        )
        text = skill_path.read_text(encoding="utf-8")
        skill_path.write_text(
            text.replace("claude\ncodex\nclaude-glm", "claude\nclaude-glm", 1),
            encoding="utf-8",
        )

        result = self.run_validator()

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("human-readable known commands differ", result.stdout)

    def test_vendor_specific_agent_argument_fails(self) -> None:
        skill_path = (
            self.repo_root / "orca-worker-reviewer-orchestration" / "SKILL.md"
        )
        text = skill_path.read_text(encoding="utf-8")
        skill_path.write_text(
            text.replace("<agent-command>", "<agent-command> --dangerously-skip-permissions", 1),
            encoding="utf-8",
        )

        result = self.run_validator()

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("vendor-specific agent launch argument", result.stdout)

    def test_custom_agent_trust_pattern_drift_fails(self) -> None:
        skill_path = (
            self.repo_root / "orca-worker-reviewer-orchestration" / "SKILL.md"
        )
        text = skill_path.read_text(encoding="utf-8")
        skill_path.write_text(
            text.replace(
                '"custom_agent_command_pattern": "(?:claude|codex)-[A-Za-z0-9._-]+"',
                '"custom_agent_command_pattern": "[A-Za-z0-9._-]+"',
                1,
            ),
            encoding="utf-8",
        )

        result = self.run_validator()

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("custom agent trust pattern is invalid", result.stdout)

    def test_workflow_output_contract_drift_fails(self) -> None:
        skill_path = (
            self.repo_root / "orca-worker-reviewer-orchestration" / "SKILL.md"
        )
        text = skill_path.read_text(encoding="utf-8")
        skill_path.write_text(
            text.replace("RESULT: PASS | FAIL", "RESULT: ACCEPT | REJECT"),
            encoding="utf-8",
        )

        result = self.run_validator()

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("missing documented choice ['FAIL', 'PASS']", result.stdout)

    def test_invalid_version_fails(self) -> None:
        (self.repo_root / "VERSION").write_text("v1\n", encoding="utf-8")

        result = self.run_validator()

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("VERSION must contain one SemVer", result.stdout)

    def test_user_specific_path_in_step5_report_fails(self) -> None:
        report = (
            self.repo_root
            / "docs/validation/historical/GLM_GEMMA_SMOKE_REPORT_2026-08-20.md"
        )
        report.write_text(
            report.read_text(encoding="utf-8").replace(
                "/Users/<user>/", "/Users/" + "private-user/", 1
            ),
            encoding="utf-8",
        )

        result = self.run_validator()

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("user-specific absolute path", result.stdout)

    def test_broken_repository_link_fails(self) -> None:
        roadmap = self.repo_root / "docs/ROADMAP.md"
        roadmap.write_text(
            roadmap.read_text(encoding="utf-8").replace(
                "(COMPATIBILITY.md)", "(MISSING-COMPATIBILITY.md)", 1
            ),
            encoding="utf-8",
        )

        result = self.run_validator()

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("broken relative link", result.stdout)

    def test_angle_bracket_repository_link_with_space_passes(self) -> None:
        linked = self.repo_root / "docs/my file.md"
        linked.write_text("# Linked document\n", encoding="utf-8")
        roadmap = self.repo_root / "docs/ROADMAP.md"
        roadmap.write_text(
            roadmap.read_text(encoding="utf-8") + "\n[Linked](<my file.md>)\n",
            encoding="utf-8",
        )

        result = self.run_validator()

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_link_in_fenced_code_block_is_ignored(self) -> None:
        roadmap = self.repo_root / "docs/ROADMAP.md"
        roadmap.write_text(
            roadmap.read_text(encoding="utf-8")
            + "\n```text\n[Example](missing-fenced-example.md)\n```\n",
            encoding="utf-8",
        )

        result = self.run_validator()

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_link_in_indented_code_block_is_ignored(self) -> None:
        roadmap = self.repo_root / "docs/ROADMAP.md"
        roadmap.write_text(
            roadmap.read_text(encoding="utf-8")
            + "\n    [Example](missing-indented-example.md)\n",
            encoding="utf-8",
        )

        result = self.run_validator()

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def mutate_orchestration_skill(self, old: str, new: str) -> None:
        skill_path = (
            self.repo_root / "orca-worker-reviewer-orchestration" / "SKILL.md"
        )
        text = skill_path.read_text(encoding="utf-8")
        self.assertIn(old, text)
        skill_path.write_text(text.replace(old, new, 1), encoding="utf-8")

    def orchestration_section(self, heading: str, end: str) -> str:
        """The body of one section of the orchestration SKILL.md, as the validator
        extracts it -- so a test can assert on exactly the text a scoped check sees."""
        text = (
            self.repo_root / "orca-worker-reviewer-orchestration" / "SKILL.md"
        ).read_text(encoding="utf-8")
        start = text.find(heading)
        self.assertNotEqual(start, -1, heading)
        stop = text.find(end, start)
        return text[start:] if stop == -1 else text[start:stop]

    def assert_lifecycle_contract_rejected(self, expected: str) -> None:
        result = self.run_validator()

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn(expected, result.stdout)

    # ---- OS-3 Final Review R1: phase-gate neutrality (T-30) ---------------------
    # Two shapes, deliberately. REPLACEMENT tests swap a corrected sentence back for
    # the stale one. COEXISTENCE tests ADD a stale section-12 heading while leaving
    # the neutral one in place -- a replacement there would trip the neutral-anchor
    # check instead and prove nothing about the section-scoped negative check, which
    # is the blind spot the iteration-7 review found.

    def test_frontmatter_reverting_to_reviewer_pass_fails(self) -> None:
        self.mutate_orchestration_skill(
            "자신의 phase gate를 PASS할 때까지 Worker 수정과 재검토를 반복하는",
            "Reviewer PASS를 받을 때까지 Worker 수정과 Reviewer 재검토를 반복하는",
        )

        self.assert_lifecycle_contract_rejected(
            "phase gate predicate is not risk-neutral"
        )

    def test_section_17_trigger_reverting_to_reviewer_pass_fails(self) -> None:
        self.mutate_orchestration_skill(
            "모든 requested phase가 자신의 **phase gate**를 PASS한 직후",
            "모든 requested phase가 Reviewer PASS를 받은 직후",
        )

        self.assert_lifecycle_contract_rejected(
            "phase gate predicate is not risk-neutral"
        )

    def test_section_17_procedure_reverting_to_reviewer_verdict_fails(self) -> None:
        self.mutate_orchestration_skill(
            "1. 마지막 requested phase의 phase gate가 PASS이고",
            "1. 마지막 requested phase의 Reviewer 판정이 PASS이고",
        )

        self.assert_lifecycle_contract_rejected(
            "phase gate predicate is not risk-neutral"
        )

    def test_section_17_dependency_justification_reverting_fails(self) -> None:
        self.mutate_orchestration_skill(
            "trigger가 마지막 requested phase의 **완료된** phase gate 판정",
            "trigger가 마지막 Reviewer Task의 **완료된** 판정",
        )

        self.assert_lifecycle_contract_rejected(
            "phase gate predicate is not risk-neutral"
        )

    def test_section_12_regaining_the_reviewer_pass_heading_fails(self) -> None:
        # The stale heading ADDED next to the neutral one: every other check still
        # passes, so only the section-scoped negative anchor can reject this.
        self.mutate_orchestration_skill(
            "phase gate PASS:",
            "Reviewer PASS:\n\nphase gate PASS:",
        )

        self.assert_lifecycle_contract_rejected(
            "section 12 still uses the Reviewer-scoped transition heading"
        )

    def test_section_12_regaining_the_reviewer_fail_heading_fails(self) -> None:
        self.mutate_orchestration_skill(
            "phase gate FAIL:",
            "Reviewer FAIL:\n\nphase gate FAIL:",
        )

        self.assert_lifecycle_contract_rejected(
            "section 12 still uses the Reviewer-scoped transition heading"
        )

    def test_section_12_losing_the_neutral_heading_fails(self) -> None:
        """The positive-anchor half, so the two checks are proven independently."""
        self.mutate_orchestration_skill("phase gate PASS:", "gate PASS:")

        self.assert_lifecycle_contract_rejected(
            "is missing the risk-neutral phase gate anchor"
        )

    def test_the_unmutated_document_passes_phase_gate_neutrality(self) -> None:
        """The assertion that pins the LINE-EXACT matching.

        Section 12 legitimately contains the substring `Reviewer FAIL`, in the very
        sentence that states the LOW rule correctly ("LOW에는 in-phase Reviewer
        FAIL이 없으므로 ..."). A substring-based guard scoped to section 12 would
        reject the CORRECTED document, so this test is what stops the whole-line
        match from being "simplified" into a substring check later.
        """
        section = self.orchestration_section("## 12. FAIL Loop", "\n## 13.")
        self.assertIn("in-phase Reviewer FAIL이 없으므로", section)
        self.assertNotIn("Reviewer FAIL:", [line.strip() for line in section.splitlines()])

        result = self.run_validator()

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    # ---- TEST-phase revalidation (T-31): the DEFINITION, not just the heading ----
    # The heading anchors above prove section 12 and section 17 use risk-neutral
    # WORDS. These prove they still say what the phase gate IS -- deleting the
    # defining sentence left the validator green while the document went back to not
    # telling a Coordinator that LOW's gate is the Worker result, which is R1's
    # failure mode in a quieter form.

    def test_section_12_losing_the_phase_gate_definition_fails(self) -> None:
        self.mutate_orchestration_skill(
            "LOW에서는 Worker 자신의 결과(§6 8단계, §14)이고, MEDIUM/HIGH에서는 phase",
            "MEDIUM/HIGH에서는 phase",
        )

        self.assert_lifecycle_contract_rejected(
            "## 12. FAIL Loop is missing the risk-neutral phase gate anchor"
        )

    def test_section_17_losing_the_phase_gate_definition_fails(self) -> None:
        self.mutate_orchestration_skill(
            "phase gate는 risk가 정한다(§8) — LOW에서는 Worker 자신의 결과이고",
            "phase gate는 risk가 정한다(§8) —",
        )

        self.assert_lifecycle_contract_rejected(
            "## 17. Final Adversarial Review is missing the risk-neutral phase gate anchor"
        )

    # ---- D-1.7 full-document sweep (T-32 negative, T-33 positive) ---------------
    # R1 recurred a third time because the guard anchored only the four sentences
    # the second round happened to quote. D-1.7 swept the whole document; these are
    # its seven findings and the eight anchors that keep their replacements alive.
    # T-32 is REPLACEMENT-shaped throughout: unlike section 12's headings, each
    # S-finding's stale text and its corrected text are mutually exclusive, so the
    # document-wide negative check is the one under test.

    def test_section_17_t4_reverting_to_an_unconditional_reviewer_fails(self) -> None:
        """S-1, the cited finding. The `아니면 ` prefix is what makes this a
        distinct anchor from T4's still-legitimate MEDIUM/HIGH branch line."""
        self.mutate_orchestration_skill(
            "    아니면 correction round를 연다. round의 모양은 risk가 정한다(§8 Risk Axis, §12).",
            "    아니면 correction Worker → p의 Reviewer 재검토 (§12 FAIL Loop 그대로)",
        )

        self.assert_lifecycle_contract_rejected(
            "phase gate predicate is not risk-neutral"
        )

    def test_section_6_graph_ordering_reverting_the_dependency_bullet_fails(
        self,
    ) -> None:
        """S-2, first bullet. Anchored as a WHOLE LINE, because the sentence it
        ends with legitimately recurs at section 6 step 2."""
        self.mutate_orchestration_skill(
            "- Coordinator는 Worker를 dispatch하기 전에 그 phase/iteration의 Task graph "
            "전체를 생성한다. 그 graph에 dependent node가 있으면 — MEDIUM/HIGH의 Reviewer "
            "Task가 그것이고, LOW에는 없다(§6 2단계) — dependent는 Worker Task를 dependency로 "
            "선언한다.",
            "- Coordinator는 Worker를 dispatch하기 전에 그 phase/iteration의 Task graph "
            "전체를 생성한다. Reviewer Task는 Worker Task를 dependency로 선언한다.",
        )

        self.assert_lifecycle_contract_rejected(
            "phase gate predicate is not risk-neutral"
        )

    def test_section_6_graph_ordering_reverting_the_fail_loop_bullet_fails(
        self,
    ) -> None:
        """S-2, the correction/re-review bullet from the same list."""
        self.mutate_orchestration_skill(
            "- FAIL loop의 correction Task도, 그 risk level에 re-review Task가 존재한다면 "
            "re-review Task도 동일 규칙을 따른다.",
            "- FAIL loop의 correction/re-review Task도 동일 규칙을 따른다.",
        )

        self.assert_lifecycle_contract_rejected(
            "phase gate predicate is not risk-neutral"
        )

    def test_section_9_iteration_end_reverting_to_a_reviewer_verdict_fails(
        self,
    ) -> None:
        """S-3. The stale wording gave iteration_end one firing point LOW never
        reaches, which would drop every LOW iteration boundary from TIMING_LOG.md.
        The parenthesis in `(phase) Reviewer` is part of the anchor."""
        self.mutate_orchestration_skill(
            "3. 이 iteration의 phase gate 판정이 나온",
            "3. 이 iteration의 (phase) Reviewer 판정이 나온",
        )

        self.assert_lifecycle_contract_rejected(
            "phase gate predicate is not risk-neutral"
        )

    def test_section_13_reverting_the_iteration_definition_fails(self) -> None:
        """S-4. The stale sentence contradicted the PHASE_ITERATIONS block six
        lines below it, so a reader who stopped at the prose got the pre-OS-3 rule."""
        self.mutate_orchestration_skill(
            "각 phase별 gate attempt를 iteration으로 센다",
            "각 phase별 Reviewer attempt를 iteration으로 센다",
        )

        self.assert_lifecycle_contract_rejected(
            "phase gate predicate is not risk-neutral"
        )

    def test_section_17_checklist_reverting_the_anti_anchoring_premise_fails(
        self,
    ) -> None:
        """S-5. Anchored as the FULL sentence: the corrected text legitimately
        contains `이전 phase Reviewer의 PASS 판정이고`, so a shorter prefix anchor
        would reject the corrected document instead of the stale one."""
        self.mutate_orchestration_skill(
            "앞선 phase gate가 PASS였다는 사실을 옳다고 가정하지 않는다.",
            "이전 phase Reviewer의 PASS 판정을 옳다고 가정하지 않는다.",
        )

        self.assert_lifecycle_contract_rejected(
            "phase gate predicate is not risk-neutral"
        )

    def test_section_12_reverting_the_same_shape_sentence_fails(self) -> None:
        """S-6. "정확히 같은 모양이다" sat immediately after the LOW exception and
        could be read as re-imposing the two-node round at every level."""
        self.mutate_orchestration_skill(
            "Final Adversarial Review FAIL이 유발한 correction도 그 risk level의 FAIL Loop와 같은 모양이다.",
            "Final Adversarial Review FAIL이 유발한 correction도 이 FAIL Loop와 정확히 같은 모양이다.",
        )

        self.assert_lifecycle_contract_rejected(
            "phase gate predicate is not risk-neutral"
        )

    # T-33: the positive half. A negative anchor stops the stale text coming back;
    # only these stop the replacement being DELETED, which would leave the section
    # silent about risk -- R1's failure mode in a quieter form. Each mutation
    # removes the anchor WITHOUT reintroducing stale text, so the positive check is
    # the only one that can fire.

    def test_section_1_losing_the_diagram_risk_rider_fails(self) -> None:
        self.mutate_orchestration_skill(
            "위 그림은 MEDIUM/HIGH의 모양이다. LOW에서는", "LOW에서는"
        )

        self.assert_lifecycle_contract_rejected(
            "## 1. Purpose is missing the risk-neutral phase gate anchor"
        )

    def test_section_6_losing_the_low_single_node_qualifier_fails(self) -> None:
        self.mutate_orchestration_skill(
            "Task가 그것이고, LOW에는 없다(§6 2단계) — dependent는",
            "Task가 그것이다 — dependent는",
        )

        self.assert_lifecycle_contract_rejected(
            "## 6. Orca-native Worker Placement is missing the risk-neutral phase gate anchor"
        )

    def test_section_9_losing_the_phase_gate_call_point_fails(self) -> None:
        self.mutate_orchestration_skill(
            "이 iteration의 phase gate 판정이 나온", "이 iteration의 gate 판정이 나온"
        )

        self.assert_lifecycle_contract_rejected(
            "## 9. Approved Phase Output is missing the risk-neutral phase gate anchor"
        )

    def test_section_12_losing_the_low_exception_reminder_fails(self) -> None:
        self.mutate_orchestration_skill(
            "위 문단의 LOW 예외를 뒤집지 않는다", "위 문단의 예외를 뒤집지 않는다"
        )

        self.assert_lifecycle_contract_rejected(
            "## 12. FAIL Loop is missing the risk-neutral phase gate anchor"
        )

    def test_section_13_losing_the_gate_attempt_definition_fails(self) -> None:
        self.mutate_orchestration_skill(
            "각 phase별 gate attempt를 iteration으로 센다", "각 phase별 attempt를 iteration으로 센다"
        )

        self.assert_lifecycle_contract_rejected(
            "## 13. Iteration is missing the risk-neutral phase gate anchor"
        )

    def test_section_17_losing_the_t4_correction_round_anchor_fails(self) -> None:
        self.mutate_orchestration_skill(
            "아니면 correction round를 연다", "아니면 correction round를 시작한다"
        )

        self.assert_lifecycle_contract_rejected(
            "## 17. Final Adversarial Review is missing the risk-neutral phase gate anchor"
        )

    def test_section_17_losing_the_t5a_high_only_scoping_fails(self) -> None:
        self.mutate_orchestration_skill(
            "T5a HIGH에서만 실행된다", "T5a 실행된다"
        )

        self.assert_lifecycle_contract_rejected(
            "## 17. Final Adversarial Review is missing the risk-neutral phase gate anchor"
        )

    def test_section_17_losing_the_english_anti_anchoring_premise_fails(self) -> None:
        self.mutate_orchestration_skill(
            "Do NOT assume any previous phase gate PASS is correct",
            "Do NOT assume any previous gate PASS is correct",
        )

        self.assert_lifecycle_contract_rejected(
            "## 17. Final Adversarial Review is missing the risk-neutral phase gate anchor"
        )

    def test_a_timing_event_call_point_losing_risk_fails(self) -> None:
        """TEST-phase revalidation 2: the CLI path must write the same TIMING_LOG
        column the Python path does. Dropping `--risk` from one call point is how
        this shipped in the first place -- the flag existed, the example omitted it,
        and the column silently went blank for every hand-driven run."""
        # OS-19 moved the timestamps out of this call point -- the scope's start
        # is whatever timing-dispatch-start captured -- so the anchor is now the
        # shorter line. The concern is unchanged: --risk must stay on it.
        self.mutate_orchestration_skill(
            "--iteration <n> --risk <이 run의 risk>",
            "--iteration <n>",
        )

        self.assert_lifecycle_contract_rejected(
            "a timing-event call point is missing"
        )

    # ---- OS-19 authoritative dispatch clock ------------------------------------

    def test_dropping_the_pre_dispatch_clock_call_point_fails(self) -> None:
        """OS-19: without `timing-dispatch-start` in the documented procedure, a
        Coordinator is back to reconstructing `--started-at` from an earlier row
        -- which is exactly how PR #16's real OS-3 run produced five negative
        `duration_s` values."""
        self.mutate_orchestration_skill(
            "(worker-start 직전) timing-dispatch-start --phase <phase> --role <role>",
            "(worker-start 직전) 시작 시각을 기억해 둔다 --phase <phase> --role <role>",
        )

        self.assert_lifecycle_contract_rejected(
            "the run-logging section is missing"
        )

    def test_dropping_the_fail_safe_marker_contract_fails(self) -> None:
        """The other half of OS-19: an unusable timestamp pair must be recorded as
        a blank duration that says why, not clamped into a plausible number."""
        self.mutate_orchestration_skill("timing_invalid=", "duration_note=")

        self.assert_lifecycle_contract_rejected(
            "the run-logging section is missing"
        )

    # ---- OS-3 risk profile contract (T-18) -------------------------------------

    def mutate_loop_skill(self, old: str, new: str) -> None:
        skill_path = self.repo_root / "orca-worker-reviewer-loop" / "SKILL.md"
        text = skill_path.read_text(encoding="utf-8")
        self.assertIn(old, text)
        skill_path.write_text(text.replace(old, new, 1), encoding="utf-8")

    def test_risk_contract_block_removed_fails(self) -> None:
        self.mutate_orchestration_skill(
            "#### Risk profile contract", "#### Risk profile notes"
        )

        self.assert_lifecycle_contract_rejected(
            "risk profile contract block is missing or malformed"
        )

    def test_risk_contract_key_drift_fails(self) -> None:
        self.mutate_orchestration_skill(
            "RISK_PARAMETER = risk\n", "RISK_PARAMETER_NAME = risk\n"
        )

        self.assert_lifecycle_contract_rejected(
            "risk profile contract keys drifted"
        )

    def test_risk_contract_value_drift_fails(self) -> None:
        self.mutate_orchestration_skill(
            "RISK_DEFAULT = high\n", "RISK_DEFAULT = low\n"
        )

        self.assert_lifecycle_contract_rejected(
            "risk profile contract values drifted"
        )

    # ---- OS-4: the agent profile anchor contract and its prose anchors ----------
    # The eighth anchor contract shipped with no negative regression test at all, so
    # the validator that locks it was itself unverified: a silently deleted block or
    # a drifted value would have been caught by nothing.

    def test_agent_profile_contract_block_removed_fails(self) -> None:
        self.mutate_orchestration_skill(
            "#### Agent profile contract", "#### Agent profile contract REMOVED"
        )

        self.assert_lifecycle_contract_rejected(
            "agent profile contract block is missing or malformed"
        )

    def test_agent_profile_contract_key_drift_fails(self) -> None:
        self.mutate_orchestration_skill(
            "AGENT_PROFILE_PARAMETER = profile\n",
            "AGENT_PROFILE_PARAMETER_RENAMED = profile\n",
        )

        self.assert_lifecycle_contract_rejected(
            "agent profile contract keys drifted"
        )

    def test_agent_profile_contract_value_drift_fails(self) -> None:
        self.mutate_orchestration_skill(
            "AGENT_PROFILE_MERGE = whole_definition_never_field_level\n",
            "AGENT_PROFILE_MERGE = field_level\n",
        )

        self.assert_lifecycle_contract_rejected(
            "agent profile contract values drifted"
        )

    def test_low_risk_requiring_a_phase_reviewer_fails(self) -> None:
        """The risk-aware required-role table is the one thing in this block the
        runtime actually branches on."""
        self.mutate_orchestration_skill(
            "AGENT_PROFILE_REQUIRED_ROLES_LOW = phase_worker, final_reviewer\n",
            "AGENT_PROFILE_REQUIRED_ROLES_LOW = phase_worker, phase_reviewer, "
            "final_reviewer\n",
        )

        self.assert_lifecycle_contract_rejected(
            "AGENT_PROFILE_REQUIRED_ROLES_LOW must not require a phase reviewer"
        )

    def test_the_two_precedence_chains_becoming_identical_fails(self) -> None:
        """They disagree about their first entry on purpose; if one is ever
        'corrected' into the other, that is a silent behaviour change."""
        self.mutate_orchestration_skill(
            "AGENT_PROFILE_FINAL_REVIEWER_PRECEDENCE = final_review, explicit, "
            "defaults\n",
            "AGENT_PROFILE_FINAL_REVIEWER_PRECEDENCE = explicit, phase, defaults\n",
        )

        self.assert_lifecycle_contract_rejected(
            "the phase reviewer and final reviewer precedence chains must differ"
        )

    def test_a_skill_losing_the_profile_parameter_fails(self) -> None:
        """The anchor is checked against the whole document -- the same caveat
        test_risk_parameter_undocumented_in_section_4_fails records -- so it trips
        only when BOTH the section 4 fence and the Agent Profile prose lose it."""
        skill_path = self.repo_root / "orca-worker-reviewer-orchestration" / "SKILL.md"
        text = skill_path.read_text(encoding="utf-8")
        self.assertEqual(text.count("profile=<name>"), 2)
        skill_path.write_text(
            text.replace("profile=<name>", "profile-parameter-removed"),
            encoding="utf-8",
        )

        self.assert_lifecycle_contract_rejected(
            "missing the profile=<name> runtime parameter"
        )

    def test_a_skill_losing_the_all_entries_safety_scope_fails(self) -> None:
        """The prose that keeps token/allowlist from narrowing back to
        required-only or requested-phase-only."""
        self.mutate_orchestration_skill(
            "selected profile이 선언한 모든 command**다",
            "selected profile이 선언한 required command만",
        )

        self.assert_lifecycle_contract_rejected(
            "missing the all-entries token/allowlist safety scope"
        )

    def test_a_skill_losing_the_required_only_path_scope_fails(self) -> None:
        """The prose that keeps the PATH check from widening back out to every role."""
        self.mutate_orchestration_skill(
            "**required role로 좁힌다**", "**모든 role에 적용한다**"
        )

        self.assert_lifecycle_contract_rejected(
            "missing the required-role-only PATH gate scope"
        )

    def test_risk_downstream_revalidation_widened_fails(self) -> None:
        """HIGH-only T5a is the DQ-1 decision; widening it must break a check."""
        self.mutate_orchestration_skill(
            "RISK_DOWNSTREAM_REVALIDATION = high_only",
            "RISK_DOWNSTREAM_REVALIDATION = every_level",
        )

        self.assert_lifecycle_contract_rejected(
            "risk profile contract values drifted"
        )

    def test_risk_low_task_graph_matching_medium_fails(self) -> None:
        """LOW must differ from MEDIUM/HIGH in graph shape -- that difference IS the
        section 6 change."""
        self.mutate_orchestration_skill(
            "RISK_LOW_TASK_GRAPH = worker_node_only",
            "RISK_LOW_TASK_GRAPH = worker_and_dependent_reviewer",
        )

        self.assert_lifecycle_contract_rejected(
            "risk profile contract values drifted"
        )

    def test_risk_error_code_missing_from_section_8_fails(self) -> None:
        self.mutate_orchestration_skill(
            "REASON: INVALID_RISK", "REASON: BAD_RISK"
        )

        self.assert_lifecycle_contract_rejected(
            "section 8 risk prose is missing 'REASON: INVALID_RISK'"
        )

    def test_risk_parameter_undocumented_in_section_4_fails(self) -> None:
        """The anchor is checked against the whole document, so it only trips when
        BOTH the section 3 usage line and the section 4 fence lose it."""
        self.mutate_orchestration_skill(
            "[risk=<low|medium|high>] <request>", "<request>"
        )
        self.mutate_orchestration_skill(
            "risk=<low|medium|high>\n```", "risk=<anything>\n```"
        )

        self.assert_lifecycle_contract_rejected(
            "section 4 does not document 'risk=<low|medium|high>'"
        )

    def test_section_6_losing_the_risk_conditional_graph_fails(self) -> None:
        self.mutate_orchestration_skill(
            "LOW에서는 Worker Task 하나만 만들고",
            "LOW에서도 Reviewer Task를 만들고",
        )

        self.assert_lifecycle_contract_rejected(
            "section 6 does not make the task graph risk-conditional"
        )

    def test_the_loop_skill_gaining_the_risk_contract_fails(self) -> None:
        """The out-of-scope guarantee, enforced from the other direction."""
        self.mutate_loop_skill(
            "## 1. Purpose",
            "#### Risk profile contract\n\n```text\nRISK_PARAMETER = risk\n```\n\n"
            "## 1. Purpose",
        )

        self.assert_lifecycle_contract_rejected(
            "orca-worker-reviewer-loop: must not contain the risk profile contract"
        )

    def test_the_loop_skill_gaining_the_risk_error_code_fails(self) -> None:
        self.mutate_loop_skill(
            "## 1. Purpose", "REASON: INVALID_RISK\n\n## 1. Purpose"
        )

        self.assert_lifecycle_contract_rejected(
            "must not carry the orchestration-only INVALID_RISK error code"
        )

    def test_dispatch_settled_example_losing_risk_flags_fails(self) -> None:
        """--round-kind is unique to the dispatch_settled example; --risk also
        appears on the run_start call point, so this mutates the discriminating one."""
        self.mutate_orchestration_skill(
            "      --round-kind phase_gate|correction|downstream_revalidation|final_review\n",
            "",
        )

        self.assert_lifecycle_contract_rejected(
            "dispatch_settled orchestrator-event example is missing"
        )

    def test_lifecycle_contract_missing_unsupervised_outcome_fails(self) -> None:
        self.mutate_orchestration_skill(
            "LIFECYCLE_OUTCOMES = reuse, retain, release, unsupervised",
            "LIFECYCLE_OUTCOMES = reuse, retain, release",
        )

        self.assert_lifecycle_contract_rejected(
            "lifecycle contract values differ from the validator source of truth"
        )

    def test_lifecycle_contract_force_ready_drift_fails(self) -> None:
        self.mutate_orchestration_skill(
            "FORCE_READY_USE = recovery_only",
            "FORCE_READY_USE = routine",
        )

        self.assert_lifecycle_contract_rejected(
            "lifecycle contract values differ from the validator source of truth"
        )

    def test_lifecycle_contract_missing_worker_start_terminal_step_fails(self) -> None:
        self.mutate_orchestration_skill(
            "worker_start_agent, "
            "terminal_create_then_tui_idle_then_worker_start_terminal, dispatch_inject",
            "worker_start_agent, dispatch_inject",
        )

        self.assert_lifecycle_contract_rejected(
            "lifecycle contract values differ from the validator source of truth"
        )

    def test_lifecycle_contract_missing_cleanup_authority_axis_fails(self) -> None:
        self.mutate_orchestration_skill(
            "AXIS_C2_CLEANUP_AUTHORITY = launch_provenance_and_ownership\n",
            "",
        )

        self.assert_lifecycle_contract_rejected(
            "lifecycle contract keys differ from the validator source of truth"
        )

    def test_lifecycle_contract_close_gate_drift_fails(self) -> None:
        self.mutate_orchestration_skill(
            "CLOSE_ALLOWED_ONLY_WHEN = authorized_and_close_eligible_role",
            "CLOSE_ALLOWED_ONLY_WHEN = connected",
        )

        self.assert_lifecycle_contract_rejected(
            "CLOSE_ALLOWED_ONLY_WHEN must require a close eligible terminal role"
        )

    def test_lifecycle_contract_coordinator_session_removed_from_never_close_fails(
        self,
    ) -> None:
        self.mutate_orchestration_skill(
            "NEVER_CLOSE_TERMINAL_ROLES = coordinator_session, ",
            "NEVER_CLOSE_TERMINAL_ROLES = ",
        )

        self.assert_lifecycle_contract_rejected(
            "NEVER_CLOSE_TERMINAL_ROLES must contain exactly"
        )

    def test_lifecycle_contract_close_gate_without_role_condition_fails(self) -> None:
        self.mutate_orchestration_skill(
            "CLOSE_ALLOWED_ONLY_WHEN = authorized_and_close_eligible_role",
            "CLOSE_ALLOWED_ONLY_WHEN = authorized",
        )

        self.assert_lifecycle_contract_rejected(
            "CLOSE_ALLOWED_ONLY_WHEN must require a close eligible terminal role"
        )

    def test_lifecycle_contract_finalization_gate_order_drift_fails(self) -> None:
        self.mutate_orchestration_skill(
            "FINALIZATION_PER_DISPATCH = exactly_once, gate_before_lifecycle_action",
            "FINALIZATION_PER_DISPATCH = exactly_once",
        )

        self.assert_lifecycle_contract_rejected(
            "lifecycle contract values differ from the validator source of truth"
        )

    def test_lifecycle_contract_settlement_verification_drift_fails(self) -> None:
        """Dropping the pre-mutation settlement check from the contract must fail.

        The human review of PR #10 found the harness mutating lifecycle state before
        axis (a) was proven; the anchor block is where that ordering requirement is
        locked, so removing the token has to be rejected here.
        """
        self.mutate_orchestration_skill(
            ", settlement_verified_before_lifecycle_action",
            "",
        )

        self.assert_lifecycle_contract_rejected(
            "lifecycle contract values differ from the validator source of truth"
        )

    def test_lifecycle_contract_copied_into_loop_skill_fails(self) -> None:
        orchestration = (
            self.repo_root / "orca-worker-reviewer-orchestration" / "SKILL.md"
        ).read_text(encoding="utf-8")
        start = orchestration.index("#### Lifecycle accounting contract")
        fence = orchestration.index("```text", start)
        end = orchestration.index("```\n", fence + 1) + 4
        block = orchestration[start:end]
        loop_path = self.repo_root / "orca-worker-reviewer-loop" / "SKILL.md"
        loop_path.write_text(
            loop_path.read_text(encoding="utf-8") + "\n" + block,
            encoding="utf-8",
        )

        self.assert_lifecycle_contract_rejected(
            "orca-worker-reviewer-loop: must not contain the orchestration lifecycle "
            "contract"
        )

    # ---- DESIGN section 7.4: the section 17 final review contract ----------------

    def final_review_contract_block(self) -> str:
        """The section 17 anchor block, sliced out of the copied orchestration skill."""
        orchestration = (
            self.repo_root / "orca-worker-reviewer-orchestration" / "SKILL.md"
        ).read_text(encoding="utf-8")
        start = orchestration.index("#### Final review contract")
        fence = orchestration.index("```text", start)
        end = orchestration.index("```\n", fence + 1) + 4
        return orchestration[start:end]

    def test_bare_final_review_choice_line_fails(self) -> None:
        """Blocker-1 lock: the result template's FINAL_REVIEW must stay a single value.

        Written as `PASS | FAIL` it reads as a workflow choice line, and the shared
        output-contract extractor then sees two skills declaring different fields.
        """
        self.mutate_orchestration_skill(
            "FINAL_REVIEW: PASS",
            "FINAL_REVIEW: PASS | FAIL",
        )

        self.assert_lifecycle_contract_rejected(
            "inconsistent fields for ['FAIL', 'PASS']"
        )

    def test_final_review_contract_missing_fails(self) -> None:
        self.mutate_orchestration_skill(self.final_review_contract_block(), "")

        self.assert_lifecycle_contract_rejected(
            "missing or malformed final review contract"
        )

    def test_final_review_contract_allows_reuse_outcome_fails(self) -> None:
        self.mutate_orchestration_skill(
            "FINAL_REVIEW_WORKER_RESOURCE_OUTCOMES = retain, release, unsupervised",
            "FINAL_REVIEW_WORKER_RESOURCE_OUTCOMES = reuse, retain, release, "
            "unsupervised",
        )

        self.assert_lifecycle_contract_rejected(
            "FINAL_REVIEW_WORKER_RESOURCE_OUTCOMES must never contain reuse"
        )

    def test_final_review_contract_iteration_bound_drift_fails(self) -> None:
        self.mutate_orchestration_skill(
            "FINAL_REVIEW_ITERATION_BOUND = max_iterations",
            "FINAL_REVIEW_ITERATION_BOUND = three",
        )

        self.assert_lifecycle_contract_rejected(
            "final review contract values differ from the validator source of truth"
        )

    def test_final_review_contract_last_attempt_guard_drift_fails(self) -> None:
        self.mutate_orchestration_skill(
            "FINAL_REVIEW_LAST_ATTEMPT_FAIL = escalate_before_correction_routing",
            "FINAL_REVIEW_LAST_ATTEMPT_FAIL = correct_then_escalate",
        )

        self.assert_lifecycle_contract_rejected(
            "final review contract values differ from the validator source of truth"
        )

    def test_final_review_contract_task_graph_drift_fails(self) -> None:
        self.mutate_orchestration_skill(
            "FINAL_REVIEW_TASK_GRAPH = single_node_no_dependencies",
            "FINAL_REVIEW_TASK_GRAPH = depends_on_last_reviewer_task",
        )

        self.assert_lifecycle_contract_rejected(
            "final review contract values differ from the validator source of truth"
        )

    def test_final_review_anti_anchoring_sentence_removed_fails(self) -> None:
        self.mutate_orchestration_skill(
            "앞선 phase gate가 PASS였다는 사실을 옳다고 가정하지 않는다.",
            "",
        )

        self.assert_lifecycle_contract_rejected(
            "final review prose is missing the anti-anchoring premise (ko)"
        )

    def test_final_review_contract_copied_into_loop_skill_fails(self) -> None:
        block = self.final_review_contract_block()
        loop_path = self.repo_root / "orca-worker-reviewer-loop" / "SKILL.md"
        loop_path.write_text(
            loop_path.read_text(encoding="utf-8") + "\n" + block,
            encoding="utf-8",
        )

        self.assert_lifecycle_contract_rejected(
            "orca-worker-reviewer-loop: must not contain the orchestration final "
            "review contract"
        )

    def test_final_review_downstream_revalidation_drift_fails(self) -> None:
        """The permanent lock on the AMENDED DECISION P1 (PR #11 human review, MAJOR 1).

        `delegated_to_next_final_review` is the superseded reading: it says a fresh
        Final Review attempt is a substitute for re-running the downstream phases. That
        reading cannot be re-introduced into section 17 without the validator rejecting
        it, whatever any future prose around the block says.
        """
        self.mutate_orchestration_skill(
            "FINAL_REVIEW_DOWNSTREAM_REVALIDATION = "
            "all_requested_phases_after_earliest_corrected_phase",
            "FINAL_REVIEW_DOWNSTREAM_REVALIDATION = delegated_to_next_final_review",
        )

        self.assert_lifecycle_contract_rejected(
            "final review contract values differ from the validator source of truth"
        )

    def test_final_review_role_outside_close_eligible_roles_fails(self) -> None:
        self.mutate_orchestration_skill(
            "FINAL_REVIEW_ROLE = phase_reviewer",
            "FINAL_REVIEW_ROLE = coordinator_session",
        )

        self.assert_lifecycle_contract_rejected(
            "FINAL_REVIEW_ROLE must be a close eligible terminal role"
        )

    # ---- the three anchor contracts added for session reuse ---------------------
    # test_valid_repository_passes above is the positive case for all three: it runs
    # the validator over an unmutated copy, so every new check has to pass there.

    def test_reuse_contract_missing_fails(self) -> None:
        self.mutate_orchestration_skill(
            "REUSE_SCOPE = same_role_across_phases_and_iterations\n", ""
        )

        self.assert_lifecycle_contract_rejected(
            "session reuse contract keys drifted"
        )

    def test_reuse_eligibility_shorter_than_eight_conditions_fails(self) -> None:
        self.mutate_orchestration_skill(
            ", not_coordinator_or_adopted, not_in_lifecycle_recovery\n",
            "\n",
        )

        self.assert_lifecycle_contract_rejected(
            "REUSE_ELIGIBILITY must list exactly eight conditions"
        )

    def test_reuse_zero_command_sentence_removal_fails(self) -> None:
        self.mutate_orchestration_skill(
            "reuse는 이전 Dispatch에 어떤 lifecycle mutation 명령도 보내지 않는다.",
            "reuse는 이전 Dispatch에 retain 명령을 보낸다.",
        )

        self.assert_lifecycle_contract_rejected(
            "section 6 prose is missing the zero lifecycle command sentence"
        )

    def test_role_table_calling_a_reused_terminal_adopted_fails(self) -> None:
        """PLAN D-1: the drift this change removed must not be reintroduced."""
        self.mutate_orchestration_skill(
            "| `external_or_adopted` | **Coordinator가 만들지 않은** terminal.",
            "| `external_or_adopted` | reused / pre-existing.",
        )

        self.assert_lifecycle_contract_rejected(
            "must not call a reused terminal external_or_adopted"
        )

    def test_task_boundary_keys_carrying_an_id_fails(self) -> None:
        """Layer 1 is assembled before either id exists, so neither may appear."""
        self.mutate_orchestration_skill(
            "TASK_BOUNDARY_KEYS = current_role,",
            "TASK_BOUNDARY_KEYS = task_id, current_role,",
        )

        self.assert_lifecycle_contract_rejected(
            "TASK_BOUNDARY_KEYS must not carry an id"
        )

    def test_dispatch_identity_rule_without_new_value_every_attempt_fails(self) -> None:
        self.mutate_orchestration_skill(
            "new_value_every_attempt, ",
            "",
        )

        self.assert_lifecycle_contract_rejected(
            "DISPATCH_IDENTITY_RULE must forbid identity carry-over"
        )

    def test_reviewer_context_losing_drill_down_fails(self) -> None:
        self.mutate_orchestration_skill(
            ", validation, drill_down\n",
            ", validation\n",
        )

        self.assert_lifecycle_contract_rejected(
            "REVIEWER_CONTEXT_KEYS must keep all eight keys including drill_down"
        )

    def test_delta_first_removing_direct_verification_duty_fails(self) -> None:
        """R-4 anti-weakening: delta-first may not shrink section 11's own duty."""
        self.mutate_orchestration_skill(
            "Worker 설명을 사실로 가정하지 않고 실제 repository/artifact/diff/test result를 확인한다.\n",
            "",
        )

        self.assert_lifecycle_contract_rejected(
            "must not remove the reviewer's direct verification duty"
        )

    def test_reviewer_context_carve_out_removal_fails(self) -> None:
        self.mutate_orchestration_skill(
            "REVIEWER_CONTEXT_EXCLUDES = final_adversarial_review",
            "REVIEWER_CONTEXT_EXCLUDES = none",
        )

        self.assert_lifecycle_contract_rejected(
            "must keep the final adversarial review carve-out"
        )

    # ---- DESIGN section 7.1 D: block-level negatives for the three new contracts --
    # Each block gets the same three: removed entirely, one value drifted, and copied
    # into the loop skill. The three failure modes hit three different checks -- the
    # `parsed is not None` guard, the exact-dict comparison, and the containment
    # guard -- so a validator that lost any one of them still fails here.

    def anchor_block(self, heading: str) -> str:
        """The `#### <heading>` subsection down to the end of its ```text fence."""
        orchestration = (
            self.repo_root / "orca-worker-reviewer-orchestration" / "SKILL.md"
        ).read_text(encoding="utf-8")
        start = orchestration.index(heading)
        fence = orchestration.index("```text", start)
        end = orchestration.index("```\n", fence + 1) + 4
        return orchestration[start:end]

    def copy_into_loop_skill(self, heading: str) -> None:
        loop_path = self.repo_root / "orca-worker-reviewer-loop" / "SKILL.md"
        loop_path.write_text(
            loop_path.read_text(encoding="utf-8")
            + "\n"
            + self.anchor_block(heading),
            encoding="utf-8",
        )

    def test_session_reuse_contract_missing_fails(self) -> None:
        """The whole block, not one key: the `parsed is not None` guard."""
        self.mutate_orchestration_skill(self.anchor_block("#### Session reuse contract"), "")

        self.assert_lifecycle_contract_rejected(
            "session reuse contract block is missing or malformed"
        )

    def test_session_reuse_contract_value_drift_fails(self) -> None:
        """The exact-dict comparison itself, independent of the semantic checks."""
        self.mutate_orchestration_skill(
            "REUSE_SCOPE = same_role_across_phases_and_iterations",
            "REUSE_SCOPE = any_role_across_phases_and_iterations",
        )

        self.assert_lifecycle_contract_rejected(
            "session reuse contract values drifted"
        )

    def test_session_reuse_contract_copied_into_loop_skill_fails(self) -> None:
        self.copy_into_loop_skill("#### Session reuse contract")

        self.assert_lifecycle_contract_rejected(
            "orca-worker-reviewer-loop: must not contain the session reuse contract"
        )

    def test_task_boundary_contract_missing_fails(self) -> None:
        self.mutate_orchestration_skill(self.anchor_block("#### Task boundary contract"), "")

        self.assert_lifecycle_contract_rejected(
            "task boundary contract block is missing or malformed"
        )

    def test_task_boundary_contract_value_drift_fails(self) -> None:
        self.mutate_orchestration_skill(
            "DISPATCH_INJECTED_IDENTITY = task_id, dispatch_id, dispatch_capability, "
            "coordinator_handle",
            "DISPATCH_INJECTED_IDENTITY = task_id, dispatch_id, coordinator_handle",
        )

        self.assert_lifecycle_contract_rejected(
            "task boundary contract values drifted"
        )

    def test_task_boundary_contract_copied_into_loop_skill_fails(self) -> None:
        self.copy_into_loop_skill("#### Task boundary contract")

        self.assert_lifecycle_contract_rejected(
            "orca-worker-reviewer-loop: must not contain the task boundary contract"
        )

    def test_reviewer_context_contract_missing_fails(self) -> None:
        self.mutate_orchestration_skill(
            self.anchor_block("#### Reviewer context contract"), ""
        )

        self.assert_lifecycle_contract_rejected(
            "reviewer context contract block is missing or malformed"
        )

    def test_reviewer_context_contract_value_drift_fails(self) -> None:
        self.mutate_orchestration_skill(
            "REVIEWER_CONTEXT_MODE = delta_first",
            "REVIEWER_CONTEXT_MODE = whole_history",
        )

        self.assert_lifecycle_contract_rejected(
            "reviewer context contract values drifted"
        )

    def test_reviewer_context_contract_copied_into_loop_skill_fails(self) -> None:
        self.copy_into_loop_skill("#### Reviewer context contract")

        self.assert_lifecycle_contract_rejected(
            "orca-worker-reviewer-loop: must not contain the reviewer context contract"
        )

    # ---- the quality profile block, and the review policy it only indexes ---------
    # The block gets the same three negatives as the others. The fourth test is the
    # one this contract needs that the others do not: a SKILL.md that still declares
    # the model while reviews/common.md -- the file a phase Reviewer is actually
    # routed to -- has lost it is exactly the documentation-only change OS-1 forbids.

    def quality_profile_block(self) -> str:
        """The machine-readable fence, not the first ```text in the subsection."""
        orchestration = (
            self.repo_root / "orca-worker-reviewer-orchestration" / "SKILL.md"
        ).read_text(encoding="utf-8")
        start = orchestration.index("```text\nQUALITY_PROFILE_STATUS")
        return orchestration[start : orchestration.index("```\n", start + 1) + 4]

    def test_quality_profile_contract_missing_fails(self) -> None:
        self.mutate_orchestration_skill(self.quality_profile_block(), "")

        self.assert_lifecycle_contract_rejected(
            "quality profile contract block is missing or malformed"
        )

    def test_quality_profile_contract_value_drift_fails(self) -> None:
        self.mutate_orchestration_skill(
            "QUALITY_GATE_SEVERITY_RULE = severity_is_not_blocking",
            "QUALITY_GATE_SEVERITY_RULE = severity_is_blocking",
        )

        self.assert_lifecycle_contract_rejected(
            "quality profile contract values drifted"
        )

    def test_quality_gate_gaining_a_third_workflow_value_fails(self) -> None:
        """PASS WITH NOTES becoming a gate value is the regression this guards."""
        self.mutate_orchestration_skill(
            "QUALITY_GATE_WORKFLOW_VALUES = pass, fail",
            "QUALITY_GATE_WORKFLOW_VALUES = pass, pass_with_notes, fail",
        )

        self.assert_lifecycle_contract_rejected(
            "QUALITY_GATE_WORKFLOW_VALUES must stay two-valued"
        )

    def test_general_gate_growing_past_five_ids_fails(self) -> None:
        self.mutate_orchestration_skill(
            "QUALITY_GATE_GENERAL_IDS = g1, g2, g3, g4, g5",
            "QUALITY_GATE_GENERAL_IDS = g1, g2, g3, g4, g5, g6",
        )

        self.assert_lifecycle_contract_rejected(
            "the minimal general gate must stay five ids"
        )

    def test_quality_gate_dropping_the_worker_role_fails(self) -> None:
        self.mutate_orchestration_skill(
            "QUALITY_GATE_CONTEXT_ROLES = worker, reviewer, final_reviewer",
            "QUALITY_GATE_CONTEXT_ROLES = reviewer, final_reviewer",
        )

        self.assert_lifecycle_contract_rejected(
            "QUALITY_GATE_CONTEXT_ROLES must reach the Worker"
        )

    def test_review_policy_losing_the_profile_first_model_fails(self) -> None:
        """A documentation-only SKILL.md change must not pass validation."""
        policy_path = (
            self.repo_root
            / "orca-worker-reviewer-orchestration"
            / "reviews"
            / "common.md"
        )
        text = policy_path.read_text(encoding="utf-8")
        policy_path.write_text(
            text.replace("### Minimal General Gate", "### Removed", 1), encoding="utf-8"
        )

        self.assert_lifecycle_contract_rejected(
            "reviews/common.md is missing the profile-first anchor"
        )

    def test_quality_profile_contract_copied_into_loop_skill_fails(self) -> None:
        loop_path = self.repo_root / "orca-worker-reviewer-loop" / "SKILL.md"
        loop_path.write_text(
            loop_path.read_text(encoding="utf-8")
            + "\n#### Quality profile contract\n\n"
            + self.quality_profile_block(),
            encoding="utf-8",
        )

        self.assert_lifecycle_contract_rejected(
            "orca-worker-reviewer-loop: must not contain the quality profile contract"
        )

    def test_dispatch_settled_example_losing_reuse_fails(self) -> None:
        """PR #15 review finding: --reuse silently dropped from the CLI example."""
        self.mutate_orchestration_skill(
            "--terminal <handle> --action created|reused --reuse <worker-start/dispatch\n"
            "      응답의 effects[].action> [--gate-result",
            "--terminal <handle> --action created|reused [--gate-result",
        )

        self.assert_lifecycle_contract_rejected(
            "dispatch_settled orchestrator-event example is missing "
            "'--action created|reused --reuse'"
        )

    def test_dispatch_settled_example_losing_gate_result_fails(self) -> None:
        """PR #15 second review round: --gate-result must stay in the example --
        it is the only place the log distinguishes a settled-but-FAILed review from
        a settled-and-PASSed one."""
        self.mutate_orchestration_skill(
            "응답의 effects[].action> [--gate-result <role가 reviewer일 때, 응답 본문이\n"
            "      실제로 적어 보낸 RESULT: PASS|FAIL>] [--review-verdict",
            "응답의 effects[].action> [--review-verdict",
        )

        self.assert_lifecycle_contract_rejected(
            "dispatch_settled orchestrator-event example is missing "
            "'--gate-result <role가 reviewer일 때'"
        )

    def test_dispatch_settled_example_losing_review_verdict_fails(self) -> None:
        """PR #15 third review round MAJOR-2: --review-verdict must also stay in
        the example -- it is the only place the log preserves PASS WITH NOTES and
        BLOCKED instead of collapsing them into the two-valued gate result."""
        self.mutate_orchestration_skill(
            "실제로 적어 보낸 RESULT: PASS|FAIL>] [--review-verdict <role가 reviewer일 때,\n"
            "      응답 본문이 실제로 적어 보낸 REVIEW_VERDICT: PASS|PASS WITH NOTES|FAIL|BLOCKED>]\n"
            # OS-3 added two flags between the verdict and the result in this example.
            "      --risk <이 run의 risk>\n"
            "      --round-kind phase_gate|correction|downstream_revalidation|final_review\n"
            "      --result",
            "실제로 적어 보낸 RESULT: PASS|FAIL>] --result",
        )

        self.assert_lifecycle_contract_rejected(
            "dispatch_settled orchestrator-event example is missing "
            "'--review-verdict <role가 reviewer일 때'"
        )

    def test_run_logging_section_missing_fails(self) -> None:
        self.mutate_orchestration_skill(
            "#### Run-scoped orchestration and timing logs (OS-17)",
            "#### Removed",
        )

        self.assert_lifecycle_contract_rejected(
            "run-scoped orchestration/timing log section is missing"
        )

    # ---- OS-22 Final Review audit contract (I-9) --------------------------------

    def test_dropping_the_audit_subsection_fails(self) -> None:
        """Section 9's audit-artifact subsection is what a live Coordinator reads to
        find out that per-dispatch records exist at all. Without it the feature is
        code with no contract."""
        self.mutate_orchestration_skill(
            "#### Final Review audit artifacts (OS-22)",
            "#### Some other subsection",
        )

        self.assert_lifecycle_contract_rejected(
            "section 9's Final Review audit artifact subsection is missing, renamed, "
            "or has escaped section 9"
        )

    def test_a_schema_version_that_drifts_from_the_writer_fails(self) -> None:
        """The version is stated in two places by necessity -- the constant and the
        prose. This validator is what keeps them one value instead of two."""
        self.mutate_orchestration_skill(
            "FINAL_REVIEW_AUDIT_SCHEMA_VERSION = 1.0",
            "FINAL_REVIEW_AUDIT_SCHEMA_VERSION = 2.0",
        )

        self.assert_lifecycle_contract_rejected(
            "schema version differs from run_logging.FINAL_REVIEW_AUDIT_SCHEMA_VERSION"
        )

    def test_a_redaction_policy_version_that_drifts_fails(self) -> None:
        self.mutate_orchestration_skill(
            f"FINAL_REVIEW_REDACTION_POLICY_VERSION = "
            f"{run_logging.FINAL_REVIEW_REDACTION_POLICY_VERSION}",
            "FINAL_REVIEW_REDACTION_POLICY_VERSION = redaction/9.9",
        )

        self.assert_lifecycle_contract_rejected(
            "redaction policy version differs"
        )

    def test_dropping_the_staging_is_never_a_record_rule_fails(self) -> None:
        """Without it a reader parses a half-written staging directory and reports an
        incomplete record as a record."""
        self.mutate_orchestration_skill(
            ".staging/                       record가 아니다. reader는 전부 무시한다",
            ".staging/                       임시 디렉터리",
        )

        self.assert_lifecycle_contract_rejected(
            "does not state the .staging/-is-never-a-record reader rule"
        )

    def test_dropping_the_run_end_is_not_terminal_rule_fails(self) -> None:
        """ANALYSIS F5b: a reader that stops at the first run_end reports a run that
        continued as finished."""
        self.mutate_orchestration_skill(
            "run_end는 terminal이 아니다",
            "run_end가 마지막 row다",
        )

        self.assert_lifecycle_contract_rejected(
            "does not state that run_end is not terminal"
        )

    def test_dropping_a_cli_call_point_fails(self) -> None:
        self.mutate_orchestration_skill(
            "final-review-audit-export", "final-review-audit-dump"
        )

        self.assert_lifecycle_contract_rejected(
            "the Final Review audit subsection is missing"
        )

    def test_section_16_reverting_to_the_stale_artifacts_path_fails(self) -> None:
        """DEC-10(i): `artifacts/FINAL_REVIEW_*` contradicts section 9's run-scoped
        ladder -- it names a shared root outside any run."""
        self.mutate_orchestration_skill(
            "attempt마다 `<ARTIFACT_ROOT>FINAL_REVIEW*`가 있는지",
            "attempt마다 artifacts/FINAL_REVIEW_*가 있는지",
        )

        self.assert_lifecycle_contract_rejected(
            "section 16 still names the stale"
        )

    def test_section_16_losing_the_audit_record_citation_fails(self) -> None:
        self.mutate_orchestration_skill(
            "FINAL_REVIEW_AUDIT: attempt <n> task_<id>",
            "AUDIT_NOTE: attempt <n> task_<id>",
        )

        self.assert_lifecycle_contract_rejected(
            "does not cite the per-dispatch audit record"
        )

    def test_section_16_losing_the_four_axis_ledger_fails(self) -> None:
        """DEC-5: OS-22 adds an authority, it does not trim the existing one."""
        self.mutate_orchestration_skill(
            "## Orca Orchestration State\n## Final Adversarial Review",
            "## Orchestration Notes\n## Final Adversarial Review",
        )

        self.assert_lifecycle_contract_rejected(
            "lost its four-axis ## Orca Orchestration State ledger"
        )

    def test_the_final_review_contract_block_carries_the_two_new_keys(self) -> None:
        for key in ("FINAL_REVIEW_AUDIT_RECORD", "FINAL_REVIEW_PROVENANCE_DEFAULT"):
            with self.subTest(key=key):
                self.assertIn(
                    key,
                    self.orchestration_section(
                        "#### Final review contract", "\n## 18."
                    ),
                )

    def test_dropping_a_new_final_review_contract_key_fails(self) -> None:
        self.mutate_orchestration_skill(
            "FINAL_REVIEW_PROVENANCE_DEFAULT = unknown\n", ""
        )

        self.assert_lifecycle_contract_rejected(
            "final review contract keys differ from the validator source of truth"
        )

    def test_flipping_the_provenance_default_to_accepted_fails(self) -> None:
        """No default anywhere, on any surface, may be `accepted`."""
        self.mutate_orchestration_skill(
            "FINAL_REVIEW_AUDIT_RECORD = artifact_root_final_review_audit_per_dispatch\n"
            "FINAL_REVIEW_PROVENANCE_DEFAULT = unknown",
            "FINAL_REVIEW_AUDIT_RECORD = artifact_root_final_review_audit_per_dispatch\n"
            "FINAL_REVIEW_PROVENANCE_DEFAULT = accepted",
        )

        self.assert_lifecycle_contract_rejected(
            "final review contract values differ from the validator source of truth"
        )

if __name__ == "__main__":
    unittest.main()
