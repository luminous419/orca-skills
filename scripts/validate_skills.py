#!/usr/bin/env python3
"""Validate the structure and shared policy of the Orca skills in this repo."""

from __future__ import annotations

import re
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit

import run_logging
from decision_gate import (
    BLOCKING_STATES,
    BOUNDARIES,
    DECISION_STATES,
    GATE_STATE_FIELD,
)
from decision_policy import (
    AXIS_TOKENS,
    CANONICAL_INDEPENDENT_AXES,
    DECISION_POLICY_MAX_LINES,
    DECLARATIVE_KEYS,
    STATE_SELECTION_INPUTS,
    TRANSITION_VALUES,
    WORKFLOW_VALUES,
    DecisionPolicyError,
    load_decision_policy,
)
from skill_policy import PolicyContractError, load_policy_contract, load_risk_contract
from workflow_contract import WorkflowContractError, load_workflow_output_contract


REPO_ROOT = Path(__file__).resolve().parents[1]
SKILL_DIRS = (
    REPO_ROOT / "orca-worker-reviewer-loop",
    REPO_ROOT / "orca-worker-reviewer-orchestration",
)

PHASE_ROUTES = {
    "ANALYSIS": (
        "templates/analysis.md",
        "reviews/common.md",
        "reviews/analysis.md",
    ),
    "PLAN": ("templates/plan.md", "reviews/common.md", "reviews/plan.md"),
    "DESIGN": ("templates/design.md", "reviews/common.md", "reviews/design.md"),
    "IMPLEMENTATION": (
        "templates/implementation.md",
        "reviews/common.md",
        "reviews/implementation.md",
    ),
    "TEST": ("templates/test.md", "reviews/common.md", "reviews/test.md"),
    "BUGFIX": (
        "templates/bugfix.md",
        "reviews/common.md",
        "reviews/bugfix.md",
    ),
    "REFACTORING": (
        "templates/refactoring.md",
        "reviews/common.md",
        "reviews/refactoring.md",
    ),
}

REQUIRED_ERROR_CODES = (
    "AGENT_NOT_ALLOWED",
    "INVALID_AGENT_COMMAND",
    "WORKER_REVIEWER_MUST_DIFFER",
    "AGENT_COMMAND_NOT_FOUND",
    "INVALID_PHASE",
    "INVALID_PHASE_ORDER",
    "UNSUPPORTED_PHASE_COMBINATION",
    "PHASE_CONFLICT",
    "PREVIOUS_PHASE_CHANGE_REQUIRED",
    "INVALID_MAX_ITERATIONS",
    # OS-4. These three live in the SHARED policy contract rather than the
    # orchestration-only anchor block: unlike the risk axis, both skills select a
    # profile and both fail closed on one, so an anchor block would leave
    # orca-worker-reviewer-loop with no contract for reporting its own failures.
    "INVALID_AGENT_PROFILE",
    "UNKNOWN_AGENT_PROFILE",
    "AGENT_ROLE_UNRESOLVED",
)

USER_ABSOLUTE_PATH_PATTERNS = (
    re.compile(r"/Users/(?!<|\{)[^/\s`]+"),
    re.compile(r"/home/(?!<|\{)[^/\s`]+"),
    re.compile(r"[A-Za-z]:\\Users\\(?!<|\{)[^\\\s`]+"),
)
REPOSITORY_DOCS = (
    "README.md",
    "INSTALL.md",
    "CHANGELOG.md",
    "docs/ROADMAP.md",
    "docs/COMPATIBILITY.md",
    "docs/RELEASING.md",
    "docs/LICENSE-DECISION.md",
    "docs/validation/GLM_GEMMA_SMOKE_PROCEDURE.md",
    "docs/validation/historical/GLM_GEMMA_SMOKE_REPORT_2026-08-20.md",
)
MARKDOWN_LINK_PATTERN = re.compile(r"\[[^\]]+\]\(([^)]+)\)")
MARKDOWN_FENCE_PATTERN = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
SEMVER_PATTERN = re.compile(
    r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"
)

LIFECYCLE_SKILL_DIR = REPO_ROOT / "orca-worker-reviewer-orchestration"

# Re-exported so anchor_contract_block_lines() can measure the same block
# load_risk_contract() parses -- one pattern, not two.
from skill_policy import RISK_CONTRACT_BLOCK_PATTERN  # noqa: E402

LIFECYCLE_CONTRACT_BLOCK_PATTERN = re.compile(
    r"####\s*Lifecycle accounting contract\s*\n(?P<body>.*?)```text\n(?P<values>.*?)\n```",
    re.DOTALL,
)
LIFECYCLE_CONTRACT_LINE_PATTERN = re.compile(r"([A-Z][A-Z0-9_]*) = (.+)")
LIFECYCLE_CONTRACT_TOKEN_PATTERN = re.compile(r"[a-z][a-z0-9_]*")

LIFECYCLE_CONTRACT: dict[str, tuple[str, ...]] = {
    "AXIS_A_SETTLEMENT": ("dispatch_and_task_provenance",),
    "AXIS_B_WORKER_RESOURCE": ("supervised_worker_registry",),
    "AXIS_C1_PROCESS_LIVENESS": ("terminal_inspection",),
    "AXIS_C2_CLEANUP_AUTHORITY": ("launch_provenance_and_ownership",),
    "LIFECYCLE_OUTCOMES": ("reuse", "retain", "release", "unsupervised"),
    "CLEANUP_AUTHORITY_STATES": ("authorized", "not_authorized", "unknown"),
    "TERMINAL_ROLE_CLASSES": (
        "coordinator_session",
        "setup_terminal",
        "active_worker",
        "external_or_adopted",
        "phase_worker",
        "phase_reviewer",
        "unknown_role",
    ),
    "NEVER_CLOSE_TERMINAL_ROLES": (
        "coordinator_session",
        "setup_terminal",
        "active_worker",
        "external_or_adopted",
        "unknown_role",
    ),
    "CLOSE_ELIGIBLE_TERMINAL_ROLES": ("phase_worker", "phase_reviewer"),
    "CLOSE_ALLOWED_ONLY_WHEN": ("authorized_and_close_eligible_role",),
    "DEFAULT_WHEN_NOT_AUTHORIZED": ("retain_and_report",),
    "FINALIZATION_PER_DISPATCH": (
        "exactly_once",
        "gate_before_lifecycle_action",
        "settlement_verified_before_lifecycle_action",
    ),
    "TASK_GRAPH_ORDERING": ("create_graph_before_worker_dispatch",),
    "FORCE_READY_USE": ("recovery_only",),
    "CUSTOM_COMMAND_PLACEMENT_ORDER": (
        "worker_start_agent",
        "terminal_create_then_tui_idle_then_worker_start_terminal",
        "dispatch_inject",
    ),
}

LIFECYCLE_AXIS_LABELS = ("(a)", "(b)", "(c1)", "(c2)")
LIFECYCLE_CONTRACT_MAX_LINES = 15

# ---- three new anchor contracts, one per topic (PLAN D-3) ----------------------
# Each block anchors on its own `#### <heading>` and takes the FIRST ```text fence
# after it, non-greedily, so the three patterns and the two existing ones never
# interfere -- not even where a new block sits earlier in the file than an old one.

REUSE_CONTRACT_BLOCK_PATTERN = re.compile(
    r"####\s*Session reuse contract\s*\n(?P<body>.*?)```text\n(?P<values>.*?)\n```",
    re.DOTALL,
)
REUSE_CONTRACT: dict[str, tuple[str, ...]] = {
    "REUSE_SCOPE": ("same_role_across_phases_and_iterations",),
    "REUSE_ELIGIBILITY": (
        "same_role",
        "same_agent_command",
        "live_process",
        "previous_dispatch_settled",
        "ownership_transferable",
        "not_explicitly_retained",
        "not_coordinator_or_adopted",
        "not_in_lifecycle_recovery",
    ),
    "REUSE_TERMINATION": ("zero_lifecycle_commands", "finalize_exactly_once"),
    "REUSE_ORDER": (
        "verify_settlement",
        "finalize_previous_dispatch",
        "start_next_task_on_same_terminal",
    ),
}
REUSE_CONTRACT_MAX_LINES = 4
# The reuse block lives in section 6, so it reuses that section's boundaries.
REUSE_SECTION_HEADING = "## 6. Orca-native Worker Placement"
REUSE_SECTION_END = "\n## 7."
# The prose sentence that makes REUSE_TERMINATION's zero_lifecycle_commands checkable.
REUSE_ZERO_COMMAND_SENTENCE = (
    "reuse는 이전 Dispatch에 어떤 lifecycle mutation 명령도 보내지 않는다."
)
# PLAN D-1: the role table must no longer call a reused terminal external_or_adopted.
REUSE_ROLE_TABLE_DRIFT = "| `external_or_adopted` | reused"

TASK_BOUNDARY_CONTRACT_BLOCK_PATTERN = re.compile(
    r"####\s*Task boundary contract\s*\n(?P<body>.*?)```text\n(?P<values>.*?)\n```",
    re.DOTALL,
)
TASK_BOUNDARY_CONTRACT: dict[str, tuple[str, ...]] = {
    "TASK_BOUNDARY_KEYS": (
        "current_role",
        "current_phase",
        "current_iteration",
        "artifact_contract",
        "relevant_previous_findings",
    ),
    "DISPATCH_INJECTED_IDENTITY": (
        "task_id",
        "dispatch_id",
        "dispatch_capability",
        "coordinator_handle",
    ),
    "DISPATCH_IDENTITY_RULE": (
        "injected_by_orca_at_dispatch",
        "new_value_every_attempt",
        "never_written_by_coordinator",
    ),
    "TASK_BOUNDARY_NEVER_CARRIED": (
        "previous_task_id",
        "previous_dispatch_id",
        "unfinished_instruction",
    ),
}
TASK_BOUNDARY_CONTRACT_MAX_LINES = 4
TASK_BOUNDARY_SECTION_HEADING = "## 9. Approved Phase Output"
TASK_BOUNDARY_SECTION_END = "\n## 10."
TASK_BOUNDARY_WRITE_ONCE_SENTENCE = "Task spec 본문은"

REVIEWER_CONTEXT_CONTRACT_BLOCK_PATTERN = re.compile(
    r"####\s*Reviewer context contract\s*\n(?P<body>.*?)```text\n(?P<values>.*?)\n```",
    re.DOTALL,
)
REVIEWER_CONTEXT_CONTRACT: dict[str, tuple[str, ...]] = {
    "REVIEWER_CONTEXT_KEYS": (
        "original_objective",
        "current_phase",
        "approved_baseline",
        "current_delta",
        "new_claims",
        "previous_findings",
        "validation",
        "drill_down",
    ),
    "REVIEWER_CONTEXT_MODE": ("delta_first",),
    "REVIEWER_DRILL_DOWN": ("mandatory_and_unrestricted",),
    "REVIEWER_CONTEXT_EXCLUDES": ("final_adversarial_review",),
}
REVIEWER_CONTEXT_CONTRACT_MAX_LINES = 4
REVIEWER_CONTEXT_SECTION_HEADING = "## 11. Reviewer Contract"
REVIEWER_CONTEXT_SECTION_END = "\n## 12."
RUN_LOGGING_SECTION_HEADING = "#### Run-scoped orchestration and timing logs"
RUN_LOGGING_SECTION_END = "\n## 10."
# R-4 anti-weakening: delta-first must not be able to shrink the direct-verification
# duty. This is the ONE place that sentence may appear, which is why the new
# subsection references it instead of quoting it.
REVIEWER_DIRECT_VERIFICATION_SENTENCE = "Worker 설명을 사실로 가정하지 않고"
REVIEWER_DRILL_DOWN_SENTENCE = "delta는 시작점이지 경계가 아니다"

FINAL_REVIEW_CONTRACT_BLOCK_PATTERN = re.compile(
    r"####\s*Final review contract\s*\n(?P<body>.*?)```text\n(?P<values>.*?)\n```",
    re.DOTALL,
)

FINAL_REVIEW_CONTRACT: dict[str, tuple[str, ...]] = {
    "FINAL_REVIEW_TRIGGER": ("after_every_requested_phase_set",),
    "FINAL_REVIEW_STRUCTURE": ("orchestration_only_implicit_gate",),
    "FINAL_REVIEW_ROLE": ("phase_reviewer",),
    "FINAL_REVIEW_TERMINAL_FRESHNESS": ("new_terminal_per_attempt",),
    "FINAL_REVIEW_WORKER_RESOURCE_OUTCOMES": ("retain", "release", "unsupervised"),
    "FINAL_REVIEW_TASK_GRAPH": ("single_node_no_dependencies",),
    "FINAL_REVIEW_COUNTER_DOMAINS": ("phase_iterations", "final_review_iterations"),
    "FINAL_REVIEW_ITERATION_BOUND": ("max_iterations",),
    "FINAL_REVIEW_LAST_ATTEMPT_FAIL": ("escalate_before_correction_routing",),
    "FINAL_REVIEW_EXHAUSTION_REASON": ("final_review_max_iterations_reached",),
    "FINAL_REVIEW_OUT_OF_SCOPE_REASON": ("out_of_scope_final_review_finding",),
    # [T5a - iteration 3] the machine-checkable form of MAJOR 1's answer
    "FINAL_REVIEW_DOWNSTREAM_REVALIDATION": (
        "all_requested_phases_after_earliest_corrected_phase",
    ),
    "FINAL_REVIEW_COMPLETION_GATE": ("requested_phases_pass_and_final_review_pass",),
    # OS-3: the final gate does not vary with risk.
    # OS-4: which command the Final Reviewer runs. The chain's first entry differs
    # from the phase reviewer's on purpose -- a profile that names a final reviewer
    # outranks an explicit `reviewer=`, which is about the phase reviewers.
    "FINAL_REVIEW_AGENT_RESOLUTION": (
        "agent_profile_final_review_then_explicit_then_defaults",
    ),
    "FINAL_REVIEW_RISK_INDEPENDENCE": ("mandatory_and_identical_at_every_risk_level",),
    # OS-22: where the per-dispatch audit record for an attempt lives, and what a
    # reader that cannot determine provenance must conclude. Both are policy
    # statements in the block's existing style (lowercase snake, no paths), not
    # paths -- the path rule itself lives in section 9.
    "FINAL_REVIEW_AUDIT_RECORD": ("artifact_root_final_review_audit_per_dispatch",),
    "FINAL_REVIEW_PROVENANCE_DEFAULT": ("unknown",),
}

# was 15; the 16th and 17th keys are OS-22's audit record location and its
# fail-closed provenance default.
FINAL_REVIEW_CONTRACT_MAX_LINES = 17
FINAL_REVIEW_SECTION_HEADING = "## 17. Final Adversarial Review"
FINAL_REVIEW_SECTION_END = "\n## 18."
# Rewritten by D-1.7 S-5: the pre-OS-3 wording anchored the anti-anchoring rule on a
# previous *Reviewer* PASS, which LOW never produces -- so the checklist's defence
# against inheriting an upstream mistake evaporated at exactly the level where the
# upstream evidence is an unreviewed Worker self-report. The premise is now stated
# over the phase gate. These two stay the OPENING clause of each language's version,
# so the sentence can be extended without churn but not deleted.
FINAL_REVIEW_ANTI_ANCHORING_SENTENCES = (
    "앞선 phase gate가 PASS였다는 사실을 옳다고 가정하지 않는다.",
    "Do NOT assume any previous phase gate PASS is correct:",
)
FINAL_REVIEW_CHECKLIST_ANCHORS = (
    "A objective alignment",
    "B cross-phase consistency",
    "C contract vs implementation",
    "D implementation vs tests",
    "E docs vs behavior",
    "F lifecycle state machine",
    "G security destructive",
    "H over-engineering",
    "I hidden coupling",
)
FINAL_REVIEW_BARE_CHOICE_LINE = re.compile(
    r"(?m)^FINAL_REVIEW:\s*[A-Z_]+\s*\|\s*[A-Z_]+\s*$"
)

QUALITY_PROFILE_CONTRACT_BLOCK_PATTERN = re.compile(
    r"####\s*Quality profile contract\s*\n(?P<body>.*?)```text\n"
    r"(?P<values>QUALITY_PROFILE_STATUS.*?)\n```",
    re.DOTALL,
)
QUALITY_PROFILE_CONTRACT: dict[str, tuple[str, ...]] = {
    "QUALITY_PROFILE_STATUS": ("loaded", "absent", "invalid"),
    "QUALITY_PROFILE_ABSENT_CONDITION": ("path_does_not_exist",),
    "QUALITY_PROFILE_RESOLUTION_SCOPE": ("resolved_once_per_run_never_per_attempt",),
    "QUALITY_PROFILE_INVALID_HANDLING": ("validation_failure_before_dispatch",),
    "QUALITY_PROFILE_ABSENT_BASIS": (
        "explicit_requirements",
        "current_phase_contract",
        "minimal_general_gate",
    ),
    "QUALITY_ATTRIBUTE_APPLIES_TO_DEFAULT": ("all_applicable_workflow_phases",),
    "QUALITY_GATE_DECISION_PRIORITY": (
        "explicit_requirements",
        "project_quality_attributes",
        "current_phase_contract",
        "minimal_general_gate",
    ),
    "QUALITY_GATE_GENERAL_IDS": ("g1", "g2", "g3", "g4", "g5"),
    "QUALITY_GATE_SEVERITY_RULE": ("severity_is_not_blocking",),
    "QUALITY_GATE_BLOCKING_SOURCES": (
        "blocking_quality_attribute",
        "minimal_general_gate",
    ),
    "QUALITY_GATE_VERDICTS": ("pass", "pass_with_notes", "fail", "blocked"),
    "QUALITY_GATE_WORKFLOW_VALUES": ("pass", "fail"),
    "QUALITY_GATE_CONTEXT_KEYS": (
        "profile_status",
        "profile_path",
        "applicable_quality_attributes",
        "blocking_quality_attributes",
        "general_gate",
        "decision_priority",
        "non_blocking_by_default",
        "verdict_semantics",
    ),
    "QUALITY_GATE_CONTEXT_ROLES": ("worker", "reviewer", "final_reviewer"),
}
QUALITY_PROFILE_CONTRACT_MAX_LINES = 14  # was 12; +absent condition, +resolution scope
# The prose the block is only an index into. Each of these is a sentence the review
# policy would still be broad-generic without, which is why they are checked here
# rather than left to read as documentation flavour.
QUALITY_PROFILE_PROSE_ANCHORS = (
    ".orca/quality-profile.yaml",
    "REASON: INVALID_QUALITY_PROFILE",
    "broad generic checklist를 복구하지 않는다",
    # IMPL-I1 F-001/F-002: the two sentences the machine keys only index.
    "정확히 한 번",
    "regular file이",
)
# The shared review policy is what the phase Reviewer actually reads. A machine
# contract in SKILL.md that the routed policy file never mentions would be exactly
# the documentation-only change the requirement forbids.
QUALITY_GATE_REVIEW_POLICY_ANCHORS = (
    "## Quality Model",
    "### Decision Priority",
    "### Minimal General Gate",
    "## Severity and Blocking",
    "PASS WITH NOTES",
    "Quality Attribute:",
    "Blocking: YES | NO",
)
# OS-17 review follow-up: the dispatch_settled example is the one place a Coordinator
# copies its `orchestrator-event` invocation from. `--action` (Coordinator's own
# created/reused decision), `--reuse` (Orca's own reported effects[].action),
# `--gate-result` (the settled review's own two-valued PASS/FAIL, distinct from
# dispatch outcome) and `--review-verdict` (OS-1's separate four-valued report
# annotation) answer four different questions; losing any of them from the example
# silently loses that column from every ORCHESTRATOR_LOG.md a live Coordinator ever
# writes.
# ---- OS-3: the seventh orchestration-only anchor contract -----------------------
# Same shape as the six above: read from LIFECYCLE_SKILL_DIR only, asserted absent
# from the loop skill, and parsed by ONE function -- skill_policy.load_risk_contract,
# imported rather than re-implemented, so the runtime evaluator and this validator
# cannot disagree about what the block says.
RISK_CONTRACT: dict[str, tuple[str, ...]] = {
    "RISK_PARAMETER": ("risk",),
    "RISK_LEVELS": ("low", "medium", "high"),
    "RISK_DEFAULT": ("high",),
    "RISK_SELECTION_SOURCES": ("explicit", "default"),
    "RISK_NATURAL_LANGUAGE": ("deterministic_explicit_parameter_only",),
    "RISK_INVALID_ERROR": ("invalid_risk",),
    "RISK_INVALID_HANDLING": ("validation_failure_before_dispatch",),
    "RISK_EMPTY_VALUE": ("explicit_invalid_never_omission",),
    "RISK_RESOLUTION_SCOPE": ("resolved_once_per_run_never_per_attempt",),
    "RISK_PHASE_AXIS": ("never_expands_or_contracts_requested_phases",),
    "RISK_QUALITY_PROFILE_AXIS": ("independent_never_read_or_gate_on_each_other",),
    "RISK_LOW_PHASE_GATE": ("worker_only",),
    "RISK_MEDIUM_PHASE_GATE": ("worker_then_phase_reviewer",),
    "RISK_HIGH_PHASE_GATE": ("worker_then_phase_reviewer",),
    "RISK_LOW_TASK_GRAPH": ("worker_node_only",),
    "RISK_MEDIUM_TASK_GRAPH": ("worker_and_dependent_reviewer",),
    "RISK_HIGH_TASK_GRAPH": ("worker_and_dependent_reviewer",),
    "RISK_DOWNSTREAM_REVALIDATION": ("high_only",),
    "RISK_FINAL_REVIEW": ("mandatory_at_every_level",),
    "RISK_SAFETY_FLOOR": ("mandatory_test_gates_apply_at_every_level",),
}
RISK_CONTRACT_MAX_LINES = 20
# ---- OS-28: the decision policy contract --------------------------------------
# The expected constant exists for the SIMULTANEOUS-DELETION blind spot: the shared
# policy-contract JSON is asserted deep-equal between the Skills, which proves they
# AGREE but not that they agree on something correct. Delete a reason code from both
# blocks and deep-equality still passes. Same idiom as RISK_CONTRACT above.
DECISION_POLICY_REASON_CODES: dict[str, tuple[str, str | None, str | None]] = {
    "repository_policy": ("ASSUMPTION_ALLOWED", None, None),
    "explicit_requirement": ("ASSUMPTION_ALLOWED", None, None),
    "phase_contract": ("ASSUMPTION_ALLOWED", None, None),
    "quality_profile_attribute": ("ASSUMPTION_ALLOWED", None, None),
    "ambiguous_requirement": ("NEEDS_INPUT", "N-1", "ambiguity"),
    "missing_user_intent": ("NEEDS_INPUT", "N-2", "ambiguity"),
    "irreversible_action": ("NEEDS_INPUT", "N-1", "reversibility"),
    "blast_radius_beyond_scope": ("NEEDS_INPUT", "N-1", "blast_radius"),
    "monetary_cost": ("NEEDS_INPUT", "N-1", "monetary_cost"),
    "security_impact": ("NEEDS_INPUT", "N-1", "security"),
    "privacy_impact": ("NEEDS_INPUT", "N-1", "privacy"),
    "compliance_impact": ("NEEDS_INPUT", "N-1", "compliance"),
    "long_term_lock_in": ("NEEDS_INPUT", "N-1", "long_term_lock_in"),
    "authority_reserved_to_user": ("NEEDS_INPUT", "N-1", "explicit_user_authority"),
    "unclassifiable_decision": ("NEEDS_INPUT", "N-3", None),
    "requirement_contradiction": ("CONFLICT", "C-1", None),
    "requirement_vs_accepted_decision": ("CONFLICT", "C-2", None),
    "requirement_vs_safety_floor": ("CONFLICT", "C-3", None),
}
DECISION_POLICY_CODE_COUNT = 18  # UD-4
# TEST phase: the constant above pinned only the reason codes, which left the
# semantic core unpinned. Five surgical mutations passed every check --
# NEEDS_INPUT's workflow flipped to "continue" (a legal member of the closed set,
# so C11c was satisfied while bounded autonomy was defeated), the two other state
# flags, INV-4's blast-radius clause emptied, and aggregate_order inverted so CLEAR
# would dominate CONFLICT. Closed-set membership is not the same as correct value;
# these pin the values.
DECISION_POLICY_STATES = {
    "CLEAR": ("continue", False, False),
    "ASSUMPTION_ALLOWED": ("continue_and_review", False, True),
    "NEEDS_INPUT": ("pause_and_ask", True, True),
    "CONFLICT": ("pause_and_request_resolution", True, True),
}
# FR-1: the full 4x4 matrix, pinned BY VALUE. C8 compared only the set of cells whose
# value is "forbidden" and C11c only closed-set membership, so both Skills'
# NEEDS_INPUT -> CLEAR could be relaxed from requires_user_decision to the equally
# legal "allowed" and the validator stayed green at 626 checks. Reproduced on a
# disposable `git archive HEAD` copy before this constant existed. Membership in a
# closed set is not the same as a correct value -- the same lesson C15-C23 applied to
# the state semantics, now applied to the edges.
DECISION_POLICY_TRANSITIONS = {
    ("CLEAR", "CLEAR"): "allowed",
    ("CLEAR", "ASSUMPTION_ALLOWED"): "allowed",
    ("CLEAR", "NEEDS_INPUT"): "allowed",
    ("CLEAR", "CONFLICT"): "allowed",
    ("ASSUMPTION_ALLOWED", "CLEAR"): "requires_retraction",
    ("ASSUMPTION_ALLOWED", "ASSUMPTION_ALLOWED"): "allowed",
    ("ASSUMPTION_ALLOWED", "NEEDS_INPUT"): "allowed",
    ("ASSUMPTION_ALLOWED", "CONFLICT"): "allowed",
    ("NEEDS_INPUT", "CLEAR"): "requires_user_decision",
    ("NEEDS_INPUT", "ASSUMPTION_ALLOWED"): "forbidden",
    ("NEEDS_INPUT", "NEEDS_INPUT"): "allowed",
    ("NEEDS_INPUT", "CONFLICT"): "allowed",
    ("CONFLICT", "CLEAR"): "requires_user_decision",
    ("CONFLICT", "ASSUMPTION_ALLOWED"): "forbidden",
    ("CONFLICT", "NEEDS_INPUT"): "allowed",
    ("CONFLICT", "CONFLICT"): "allowed",
}
# The two edges that carry the authority boundary. Named separately so a failure says
# which promise broke, not merely that a table drifted.
DECISION_POLICY_AUTHORITY_EDGES = {
    ("NEEDS_INPUT", "CLEAR"),
    ("CONFLICT", "CLEAR"),
}
# Found by the same-shape sweep FR-1 prompted: these four keys were also checked only
# for names or membership, never for value. Each mutation below passed every check.
# The fourth tuple slot is `triggering` -- which value(s) make the element TRUE in
# A3-1's sense. FR-4 made these load-bearing for permitted_states, so they are pinned
# by value like everything else; leaving them unpinned would be the FR-1 gap again.
DECISION_POLICY_BOUNDARY_ELEMENT_SPECS = {
    "ambiguity": ("declared", (), None, True),
    "explicit_requirement_conflict": ("citations", (), 2, "at_minimum"),
    "reversibility": (
        "enum",
        ("reversible_in_run", "reversible_with_effort", "irreversible"),
        None,
        ("irreversible",),
    ),
    "blast_radius": (
        "enum",
        ("current_change", "module", "repository", "external_system"),
        None,
        ("repository", "external_system"),
    ),
    "monetary_cost": ("boolean", (), None, True),
    "security": ("boolean", (), None, True),
    "privacy": ("boolean", (), None, True),
    "compliance": ("boolean", (), None, True),
    "long_term_lock_in": ("boolean", (), None, True),
    "repository_project_policy": ("policy_source", (), None, None),
    "explicit_user_authority": ("user_decision", (), None, ("reserved",)),
}
# FR-4: A3-1's entry conditions, made machine-evaluable. permitted_states() evaluates
# these rather than assuming a fixed starting set, so they are the contract's most
# authority-relevant data and are pinned cell by cell.
# RI3-1: the two cells A4-0 marks as things a determining policy source CANNOT
# resolve -- "a policy source cannot un-reserve it" and "a policy source cannot
# arbitrate two explicit requirements". Pinned by value like every other authority
# datum, because widening this list is how the precedence would quietly come undone.
DECISION_POLICY_CANNOT_RESOLVE = (
    "explicit_user_authority",
    "explicit_requirement_conflict",
)
DECISION_POLICY_ENTRY_CONDITIONS = {
    "CLEAR": (
        "any_of",
        (
            "no_open_decision_item",
            "determining_policy_source",
            "explicit_user_authorization",
        ),
    ),
    "ASSUMPTION_ALLOWED": (
        "all_of",
        (
            "all_safety_facts_declared",
            "reversible_in_run",
            "blast_radius_within_scope",
            "no_high_impact_element",
            "supporting_policy_source",
            "no_reserved_user_authority",
        ),
    ),
    "NEEDS_INPUT": (
        "any_of",
        ("undetermined_boundary_element", "absent_user_intent", "unclassifiable_item"),
    ),
    "CONFLICT": ("any_of", ("declared_contradiction",)),
}
DECISION_POLICY_SOURCE_ROLES = ("determines", "supports")
DECISION_POLICY_SOURCE_KINDS = (
    "file_path",
    "requirement_id",
    "quality_attribute_id",
    "phase_contract_section",
)
DECISION_POLICY_STATE_SCOPE = "per_decision_item_with_derived_check_aggregate"
DECISION_POLICY_AGGREGATE_ORDER = (
    "CONFLICT",
    "NEEDS_INPUT",
    "ASSUMPTION_ALLOWED",
    "CLEAR",
)
DECISION_POLICY_FORBIDDEN_WHEN = {
    "reversibility_in": ["irreversible"],
    "blast_radius_in_with_irreversible": ["repository", "external_system"],
    "any_true_of": [
        "monetary_cost",
        "security",
        "privacy",
        "compliance",
        "long_term_lock_in",
    ],
    "explicit_user_authority_reserved": True,
    "exception_allowed": False,
}
DECISION_POLICY_ASSUMPTION_REQUIRES = {
    "policy_source_role": "supports",
    "all_required_evidence_non_empty": True,
    # F-001. The facts an ASSUMPTION_ALLOWED record must DECLARE, pinned by value for
    # the reason every other authority datum here is: shortening this list is how the
    # fail-open would come back, and it would come back silently -- a shorter list
    # rejects nothing new, so every test and fixture would stay green while an
    # undeclared blast radius or security flag again read as safe.
    "declared_safety_facts": [
        "blast_radius",
        "monetary_cost",
        "security",
        "privacy",
        "compliance",
        "long_term_lock_in",
    ],
    # Pinned because it is a RULE, not a default: flipping it to "reserved" changes
    # what an unstated user authority means, and that must not happen unnoticed.
    "absent_explicit_user_authority": "not_reserved",
}
# F-002. Which predicate PROVES each entry clause. Pinned by value: repointing N-2 at
# `undetermined_boundary_element` would restore exactly the defect the review found --
# `missing_user_intent` satisfied by evidence that says nothing about user intent --
# and closed-set membership alone would not notice.
DECISION_POLICY_CLAUSE_PREDICATES = {
    "N-1": "undetermined_boundary_element",
    "N-2": "absent_user_intent",
    "N-3": "unclassifiable_item",
    "C-1": "declared_contradiction",
    "C-2": "declared_contradiction",
    "C-3": "declared_contradiction",
}
DECISION_POLICY_USER_DECISION_FIELDS = ("source", "where_recorded", "resolves")
# FR-2: the closed POSITIVE vocabulary for user authority. The denylist below no
# longer enforces anything -- enforcement is membership in this set, and an
# unrecognised source is rejected. These two values are the only shapes ANALYSIS
# A4-0 identifies: an in-run answer to a structured question, and a standing
# authorization carried from the original request.
DECISION_POLICY_USER_DECISION_SOURCES = (
    "explicit_user_reply",
    "prior_explicit_user_authorization",
)
DECISION_POLICY_CITATION_MINIMUM = {"CONFLICT": 2}
DECISION_POLICY_REQUIRED_EVIDENCE = {
    "CLEAR": (),
    "ASSUMPTION_ALLOWED": (
        "reason_code",
        "policy_source",
        "reversibility",
        "impact",
        "retraction_condition",
    ),
    "NEEDS_INPUT": (
        "reason_code",
        "boundary_element",
        "what_is_missing",
        "why_policy_cannot_decide",
    ),
    "CONFLICT": ("reason_code", "citations", "why_they_cannot_both_hold"),
}
# Entry-clause prose and the downstream rule are pinned by VALUE. DESIGN F-5 records
# that a coordinated edit of both Skills AND this constant still passes every static
# check -- that remains true and is still only caught by human diff review. What this
# does close is the two-file variant: editing the Skills alone now fails here.
DECISION_POLICY_ENTRY_CLAUSES = {
    "NEEDS_INPUT": {
        "N-1": (
            "a boundary element is true, is not determined by a policy source, and "
            "is not decided by an explicit authorization"
        ),
        "N-2": "required user intent is absent",
        "N-3": (
            "the item crosses the autonomy boundary but cannot be classified under "
            "these closed vocabularies"
        ),
    },
    "CONFLICT": {
        "C-1": "two or more explicit requirements are contradictory",
        "C-2": (
            "an explicit requirement contradicts an already-accepted decision of "
            "this run"
        ),
        "C-3": (
            "an explicit requirement contradicts a non-overridable project invariant"
        ),
    },
}
DECISION_POLICY_DOWNSTREAM_RULE = (
    "an unresolved NEEDS_INPUT or CONFLICT item may not be reported CLEAR by a "
    "later phase"
)
DECISION_POLICY_PER_STATE = {"ASSUMPTION_ALLOWED": 4, "NEEDS_INPUT": 11, "CONFLICT": 3}
DECISION_POLICY_BOUNDARY_ELEMENTS = (
    "ambiguity",
    "explicit_requirement_conflict",
    "reversibility",
    "blast_radius",
    "monetary_cost",
    "security",
    "privacy",
    "compliance",
    "long_term_lock_in",
    "repository_project_policy",
    "explicit_user_authority",
)
DECISION_POLICY_FORBIDDEN_CELLS = {
    ("NEEDS_INPUT", "ASSUMPTION_ALLOWED"),
    ("CONFLICT", "ASSUMPTION_ALLOWED"),
}
DECISION_POLICY_REJECT_LIST = (
    "model_confidence",
    "timeout",
    "no_response",
    "worker_reviewer_agreement",
    "recommended_default",
)
DECISION_POLICY_BLOCK_PATTERN = re.compile(
    r'\n  "decision_policy": \{\n(?P<body>.*?)\n  \}\n\}', re.DOTALL
)
# Sentences the machine block only INDEXES. Byte-equality catches divergence between
# the Skills; it cannot catch a sentence deleted from BOTH copies. These anchors can.
DECISION_POLICY_SKILL_PROSE_ANCHORS = (
    "decision state는 RUN_STATUS / Worker STATUS / REVIEW_VERDICT와 별개의 축이다",
    "NEEDS_INPUT은 정보가 없는 것이고 CONFLICT는 정보가 모순되는 것이다",
    "답변을 받은 항목은 CLEAR가 되며 ASSUMPTION_ALLOWED가 되지 않는다",
    "INV-4에는 예외가 없다",
    # F-001 / F-002: the two sentences the section would be WRONG without after this
    # fix, held to the same standard as the four above. A reader who takes "not
    # declared" for "false", or a code's clause for a proof of that clause, has the
    # contract backwards -- and both readings were true of the shipped code.
    "선언하지 않은 fact는 거짓이 아니라 미상이며",
    "reason code가 rest하는 clause는 record에서 실제로",
)
DECISION_RECORD_OPTIONALITY_ANCHOR = (
    "optional section이다. 없어도 계약 위반이 아니다."
)
DECISION_RECORD_TEMPLATE_ANCHOR = "## Decision Record (optional)"

# ---- OS-29: the tenth orchestration-only anchor contract -------------------------
# The SHARED `decision_policy` block owns every decision SEMANTIC -- the four states,
# their entry clauses, the closed reason codes and the required evidence -- and it is
# at its documented line budget, which is one reason this block exists at all. What
# this block owns is strictly the Orca LIFECYCLE half: where the gate runs, what it
# reads, where the terminal is recorded, whether a dispatch site is added, how the
# correction counter behaves, and whether resume exists. None of its keys redefines a
# state's meaning, so the two skills' decision semantics cannot drift through it.
DECISION_GATE_CONTRACT_HEADING = "#### Decision gate contract"
DECISION_GATE_CONTRACT_BLOCK_PATTERN = re.compile(
    r"####\s*Decision gate contract\s*\n(?P<body>.*?)```text\n(?P<values>.*?)\n```",
    re.DOTALL,
)
DECISION_GATE_CONTRACT: dict[str, tuple[str, ...]] = {
    "DECISION_GATE_BOUNDARIES": (
        "before_phase_entry",
        "after_worker_result",
        "after_reviewer_result",
    ),
    "DECISION_GATE_INPUT": ("explicit_machine_readable_record_never_absence",),
    "DECISION_GATE_AXIS_ORDER": ("decision_axis_then_quality_axis",),
    "DECISION_GATE_LEDGER": ("artifact_root_decision_ledger_append_only",),
    "DECISION_GATE_LEDGER_ENTRY_SEQUENCE": ("zero",),
    "DECISION_GATE_LEDGER_PRODUCER": ("coordinator_at_run_open",),
    "DECISION_GATE_ADMISSIBILITY": (
        "non_empty",
        "single_entry_declaration",
        "schema_supported",
        "bound_head",
        "declaration_recomputed",
        "no_unresolved_open_item",
    ),
    "DECISION_GATE_BLOCKING_STATES": ("needs_input", "conflict"),
    "DECISION_GATE_TERMINAL_STATUS": ("blocked",),
    "DECISION_GATE_LOW_TERMINAL_BOUNDARY": ("after_worker_result",),
    "DECISION_GATE_MEDIUM_HIGH_TERMINAL_BOUNDARY": ("after_reviewer_result",),
    "DECISION_GATE_REVIEWER_PARTICIPATION": (
        "already_scheduled_reviewer_in_verification_mode",
    ),
    "DECISION_GATE_NEW_DISPATCH_SITES": ("none",),
    "DECISION_GATE_ITERATION_ACCOUNTING": (
        "decision_block_consumes_no_correction_iteration",
    ),
    "DECISION_GATE_DOWNGRADE_AUTHORITY": ("policy_contract_transition_rule_only",),
    "DECISION_GATE_RISK_INDEPENDENCE": (
        "identical_terminal_outcome_at_every_risk_level",
    ),
    "DECISION_GATE_RESUME": ("not_implemented_terminal_only",),
    "DECISION_GATE_AUTHORITY": ("machine_record_over_markdown_summary",),
}
DECISION_GATE_CONTRACT_MAX_LINES = 20
# The decision SEMANTICS both skills must state identically. Byte-equality between
# the two files would catch a sentence changed in ONE of them; it cannot catch a
# sentence deleted from BOTH. These anchors can -- the same reason
# DECISION_POLICY_SKILL_PROSE_ANCHORS exists.
MIRRORED_DECISION_SEMANTICS_ANCHORS = (
    "gate 경계에서 decision 결과는 필수이며 명시적이다.",
    "CLEAR로 단언되어야 하며 기록의 부재로 추정될",
    "기계가 읽는 record가 authority이고 Markdown 요약은 사람을 위한",
)
# The result-contract line itself, mirrored into both SKILL.md files and into every
# templates/*.md and reviews/common.md. Built from decision_gate's own constants, so
# renaming the field in code without editing the documents is a validation failure
# rather than a silent divergence between the contract and its implementation.
DECISION_GATE_RESULT_CONTRACT_ANCHOR = (
    f"{GATE_STATE_FIELD}: " + " | ".join(DECISION_STATES)
)

RISK_SECTION_HEADING = "## 8. Phase Sequence Contract"
RISK_SECTION_END = "\n## 9."
# The prose the block is only an index into. Each is a sentence the section would be
# WRONG without, the same criterion QUALITY_PROFILE_PROSE_ANCHORS already uses.
RISK_PROSE_ANCHORS = (
    "REASON: INVALID_RISK",
    "mid-phase Reviewer gate를 건너뛴다",
    "phase-local bounded correction loop",
    "downstream revalidation(§17 T5a)",
    "문서화된 예외",
    "BUGFIX / REFACTORING × risk",
)
RISK_PARAMETER_DOC_ANCHOR = "risk=<low|medium|high>"

# ---- OS-4: the eighth orchestration-only anchor contract ------------------------
# The risk block's rationale applies here only in part. The SHARED policy contract
# owns everything both skills need (the parameter, the two source paths, the schema
# version, the three error codes); this block owns what only the orchestration
# runtime has -- Final Reviewer routing, the risk-aware required-role table, the
# pre-Run gate order, and the evidence obligation. The value grammar is lowercase
# snake tokens, which is why no path or error-code string can live here.
AGENT_PROFILE_CONTRACT_BLOCK_PATTERN = re.compile(
    r"####\s*Agent profile contract\s*\n(?P<body>.*?)```text\n(?P<values>.*?)\n```",
    re.DOTALL,
)
AGENT_PROFILE_CONTRACT: dict[str, tuple[str, ...]] = {
    "AGENT_PROFILE_PARAMETER": ("profile",),
    "AGENT_PROFILE_SELECTION_STATES": ("omitted", "selected", "invalid"),
    "AGENT_PROFILE_RESOLUTION_SCOPE": ("resolved_once_before_run_never_per_attempt",),
    "AGENT_PROFILE_GATE_ORDER": (
        "materialize",
        "validate_commands",
        "validate_required_roles",
        "create_run",
    ),
    "AGENT_PROFILE_PHASE_WORKER_PRECEDENCE": ("explicit", "phase", "defaults"),
    "AGENT_PROFILE_PHASE_REVIEWER_PRECEDENCE": ("explicit", "phase", "defaults"),
    "AGENT_PROFILE_FINAL_REVIEWER_PRECEDENCE": ("final_review", "explicit", "defaults"),
    "AGENT_PROFILE_SOURCE_PRECEDENCE": ("project_local", "user_global"),
    "AGENT_PROFILE_MERGE": ("whole_definition_never_field_level",),
    "AGENT_PROFILE_REQUIRED_ROLES_LOW": ("phase_worker", "final_reviewer"),
    "AGENT_PROFILE_REQUIRED_ROLES_MEDIUM": (
        "phase_worker",
        "phase_reviewer",
        "final_reviewer",
    ),
    "AGENT_PROFILE_REQUIRED_ROLES_HIGH": (
        "phase_worker",
        "phase_reviewer",
        "final_reviewer",
    ),
    "AGENT_PROFILE_PATH_CHECK_SCOPE": ("required_roles_only",),
    "AGENT_PROFILE_EVIDENCE": (
        "profile_name",
        "profile_source",
        "requested_phases",
        "resolved_commands",
        "resolution_sources",
    ),
    "AGENT_PROFILE_SECRETS": ("never_recorded",),
    "AGENT_PROFILE_RISK_DEPENDENCY": ("reads_settled_risk_never_modifies",),
    "AGENT_PROFILE_QUALITY_AXIS": ("independent",),
    "AGENT_PROFILE_LEGACY": ("omitted_profile_preserves_existing_behavior",),
}
AGENT_PROFILE_CONTRACT_MAX_LINES = 18
AGENT_PROFILE_PARAMETER_DOC_ANCHOR = "profile=<name>"
# The sentences that keep the two runtime differences from quietly disappearing from
# the loop skill. Prose rather than an anchor block: orca-worker-reviewer-loop has no
# anchor contracts at all, and adding its first one for two facts would be a heavier
# structure than the facts need.
LOOP_AGENT_PROFILE_PROSE_ANCHORS = (
    "이 Skill에는 risk 축이 없으므로 모든 phase에서 Reviewer가 required다",
    "이 Skill은 그 값을 읽지 않고 무시한다",
)
# Both skills must say that omitting the parameter changes nothing.
AGENT_PROFILE_LEGACY_PROSE_ANCHOR = (
    "`profile`을 생략하면 기존 동작을 그대로 유지한다"
)
# The two-gate split, in the prose a reader actually meets: token/allowlist over
# the WHOLE selected profile definition (every phase, requested or not, plus
# participating explicit values), PATH narrowed to required roles only.
AGENT_PROFILE_SAFETY_ALL_ENTRIES_PROSE_ANCHOR = (
    "selected profile이 선언한 모든 command**다"
)
AGENT_PROFILE_PATH_REQUIRED_ONLY_PROSE_ANCHOR = (
    "PATH 검사만 **required role로 좁힌다**"
)
# Section 6 must say that risk chooses which graph NODES exist, never WHEN they are
# created -- the sentence that keeps LOW from leaving an orphan ready Reviewer Task.
RISK_TASK_GRAPH_PROSE_ANCHOR = "LOW에서는 Worker Task 하나만 만들고"

# ---- OS-3 Final Review R1: risk-neutral phase-gate predicates -------------------
# The phase gate is risk-dependent (section 6 step 8, section 8, section 16, section
# 18), so a phase transition or the Final Review trigger must never be expressed as
# requiring a Reviewer PASS -- LOW creates no phase Reviewer and produces no verdict,
# which would make the mandatory final gate unreachable in the specification even
# though the code handles it correctly.
#
# Stale predicates that must never reappear ANYWHERE in the document. Each is the
# exact text Final Review R1 found, so a revert or a copy-paste from an older
# revision fails loudly.
PHASE_GATE_STALE_PREDICATES = (
    "Reviewer PASS를 받을 때까지",                      # frontmatter
    "모든 requested phase가 Reviewer PASS를 받은",       # section 17 trigger
    "마지막 requested phase의 Reviewer 판정이 PASS",     # section 17 procedure step 1
    "trigger가 마지막 Reviewer Task의",                  # section 17 dependency justification
    # --- extended by D-1.7 (Final Review R1, attempt 2) ---------------------
    # The four entries above are the four sentences the SECOND review round
    # happened to quote. R1 recurred a third time because nothing swept for
    # siblings, so D-1.7 swept the whole document and these seven are what it
    # found. Each is the exact text as it stood before the sweep, so a revert or
    # a copy-paste from an older revision fails loudly and by name.
    #
    # The `아니면 ` prefix is load-bearing: corrected section 17 T4 still
    # contains `correction Worker → p의 Reviewer 재검토 (§12 FAIL Loop 그대로)`
    # on its MEDIUM/HIGH branch line, where it is correct. Only the
    # unconditional form began with `아니면 `. Dropping the prefix would reject
    # the corrected file.
    "아니면 correction Worker → p의 Reviewer 재검토",        # S-1, section 17 T4
    # S-2, section 6 `### Task graph ordering`. The WHOLE BULLET LINE,
    # deliberately: the bare sentence `Reviewer Task는 Worker Task를 dependency로
    # 선언한다` occurs a second time, at section 6 step 2, where it is already
    # correctly risk-scoped (`MEDIUM/HIGH에서는 ... LOW에서는 Worker Task 하나만
    # 만들고 ...`). A sentence anchor would reject the corrected document -- the
    # same trap the section 12 headings below hit with `Reviewer FAIL`. Verified
    # against the corrected file: this full line occurs 0 times, the sentence 1.
    "- Coordinator는 Worker를 dispatch하기 전에 그 phase/iteration의 Task graph 전체를 "
    "생성한다. Reviewer Task는 Worker Task를 dependency로 선언한다.",
    "FAIL loop의 correction/re-review Task도 동일 규칙",     # S-2, section 6, same bullet list
    # S-3, section 9 OS-17 timing call point 3. The parenthesis is part of the
    # anchor: `(phase) Reviewer` in that bracketed form appears nowhere else, and
    # the replacement reads `이 iteration의 phase gate 판정이`.
    "이 iteration의 (phase) Reviewer",
    "각 phase별 Reviewer attempt를 iteration으로 센다",       # S-4, section 13
    # S-5, section 17 review checklist. The FULL sentence, because the
    # replacement legitimately contains `이전 phase Reviewer의 PASS 판정이고` --
    # a shorter prefix anchor would reject the corrected file.
    "이전 phase Reviewer의 PASS 판정을 옳다고 가정하지 않는다",
    "이 FAIL Loop와 정확히 같은 모양이다",                    # S-6, section 12
)
# Section 12's two transition HEADINGS. They need their own list because they are
# stale only in one section and only as whole lines. BOTH refinements are
# load-bearing:
#
#   - SCOPED, because `Reviewer PASS` / `Reviewer FAIL` occur legitimately elsewhere
#     in the document -- section 17's anti-anchoring sentence ("Do NOT assume any
#     previous Reviewer PASS decision is correct.") and section 18's invariant
#     ("Reviewer FAIL -> new Worker correction dispatch"). A document-wide check
#     would reject the corrected file.
#
#   - LINE-EXACT, because section 12 ITSELF legitimately contains the substring
#     `Reviewer FAIL`, in "LOW에는 in-phase Reviewer FAIL이 없으므로 이 loop는 §17 T4를
#     통해서만 진입한다" -- the very sentence that states the LOW rule correctly. A
#     substring check scoped to section 12 would therefore fail on the CORRECTED
#     document. The stale forms are whole lines ending in a colon; that prose is not.
#
# D-1.7 note: after S-5 rewrote section 17's anti-anchoring line in terms of the
# phase gate, the bare substring `Reviewer PASS` no longer occurs anywhere in the
# document -- so half the SCOPED justification above now reads as historical. Do
# NOT "simplify" `Reviewer PASS:` into a document-wide negative anchor on that
# basis. Section 11 and section 17 may legitimately regain the phrase, and
# `Reviewer FAIL` still occurs twice for exactly the reasons stated (section 12's
# own LOW sentence and section 18's invariant), so the pair must keep the same
# shape. The LINE-EXACT half is unaffected and remains load-bearing.
PHASE_GATE_STALE_SECTION_HEADINGS = (
    ("## 12. FAIL Loop", "\n## 13.", "Reviewer PASS:"),
    ("## 12. FAIL Loop", "\n## 13.", "Reviewer FAIL:"),
)
# The risk-neutral replacements, each checked in the section that must carry it.
PHASE_GATE_NEUTRAL_ANCHORS = (
    ("## 12. FAIL Loop", "\n## 13.", "phase gate PASS:"),
    ("## 12. FAIL Loop", "\n## 13.", "phase gate FAIL:"),
    # TEST-phase revalidation: the two headings above are risk-NEUTRAL but say
    # nothing about what the gate IS. Deleting the sentence that defines it left the
    # validator green while the document went back to not telling a Coordinator that
    # LOW's gate is the Worker result -- R1's failure mode in a quieter form. These
    # two anchor the definition itself, one per section that carries it.
    (
        "## 12. FAIL Loop",
        "\n## 13.",
        "LOW에서는 Worker 자신의 결과(§6 8단계, §14)이고",
    ),
    ("## 17. Final Adversarial Review", "\n## 18.", "자신의 **phase gate**를 PASS한 직후"),
    ("## 17. Final Adversarial Review", "\n## 18.", "마지막 requested phase의 phase gate가 PASS"),
    ("## 17. Final Adversarial Review", "\n## 18.", "LOW에서는 Worker 자신의 결과이고"),
    # --- extended by D-1.7 (Final Review R1, attempt 2) ---------------------
    # One per S-finding location, checked in the section that must carry it. The
    # negative list above forbids the stale text; these forbid its silent
    # DELETION, which would leave a section saying nothing about risk at all --
    # R1's failure mode in a quieter form.
    ("## 1. Purpose", "\n## 2.", "위 그림은 MEDIUM/HIGH의 모양이다"),
    ("## 6. Orca-native Worker Placement", "\n## 7.", "LOW에는 없다(§6 2단계)"),
    ("## 9. Approved Phase Output", "\n## 10.", "이 iteration의 phase gate 판정이 나온"),
    ("## 12. FAIL Loop", "\n## 13.", "위 문단의 LOW 예외를 뒤집지 않는다"),
    ("## 13. Iteration", "\n## 14.", "각 phase별 gate attempt를 iteration으로 센다"),
    ("## 17. Final Adversarial Review", "\n## 18.", "아니면 correction round를 연다"),
    ("## 17. Final Adversarial Review", "\n## 18.", "T5a HIGH에서만 실행된다"),
    (
        "## 17. Final Adversarial Review",
        "\n## 18.",
        "Do NOT assume any previous phase gate PASS is correct",
    ),
)
PHASE_GATE_FRONTMATTER_ANCHOR = "phase gate는 risk가 정한다"

RUN_LOGGING_DISPATCH_SETTLED_ANCHORS = (
    "--action created|reused --reuse",
    "--gate-result <role가 reviewer일 때",
    "--review-verdict <role가 reviewer일 때",
    # OS-3: same reasoning -- prose alone silently drops a column.
    # Indented, so these anchor on the dispatch_settled CALL POINT rather than on
    # the usage block above it -- both live inside this section, and only the call
    # point is what a Coordinator copies its invocation from.
    "\n      --risk <이 run의 risk>",
    "\n      --round-kind phase_gate|correction|downstream_revalidation|final_review",
)
# TEST-phase revalidation 2: the same failure mode, one log over. TIMING_LOG.md's
# `risk` column shipped populated at two of six call sites in the Python path and at
# NONE of them in the CLI path documented here, so a Coordinator driving Orca by hand
# wrote a wholly blank column while OrcaRuntimeHarness wrote a partly filled one --
# two different logs for the same run. The behavioural half is pinned by T-36 (every
# TIMING row carries the run's risk); these four keep the CALL POINTS, which are what
# a live Coordinator copies its invocation from, saying the same thing. Separate from
# the tuple above because the failure message must name the right example.
RUN_LOGGING_TIMING_RISK_ANCHORS = (
    "--event run_start --started-at <지금 시각> --risk <이 run의 risk>",
    # OS-19: the boundary call points no longer take a timestamp -- the scope's
    # start is whatever timing-dispatch-start captured -- but they still name
    # --risk, which is what these anchors exist to hold.
    "--event phase_end --phase <phase>\n      --risk <이 run의 risk>",
    # Scoped to the iteration_end call point: the bare `--iteration <n> --risk ...`
    # line now occurs three times in this section (iteration_end, the
    # timing-dispatch-start clock call, and dispatch_settled), so an unscoped
    # anchor would stay satisfied by a sibling after this one lost its flag.
    "--event iteration_end --phase <phase>\n      --iteration <n> --risk <이 run의 risk>",
    "--event dispatch_settled --phase <phase> --role <role> --iteration <n>\n      --risk <이 run의 risk>",
)
# OS-19: the authoritative dispatch clock. The five negative duration_s rows in
# PR #16's real OS-3 TIMING_LOG.md all came from a Coordinator reconstructing
# --started-at from an earlier row, because this section asked it to. If the
# pre-dispatch call point ever drops back out of the documented procedure, the
# CLI path silently returns to guessing, so it is anchored the same way the
# dispatch_settled flags are.
RUN_LOGGING_DISPATCH_CLOCK_ANCHORS = (
    "timing-dispatch-start --run-id <run-id>",
    "(worker-start 직전) timing-dispatch-start --phase <phase> --role <role>",
    "timing_invalid=",
)



class Validation:
    def __init__(self) -> None:
        self.checks = 0
        self.errors: list[str] = []

    def check(self, condition: bool, message: str) -> None:
        self.checks += 1
        if not condition:
            self.errors.append(message)


def parse_frontmatter(path: Path) -> dict[str, str]:
    """Parse the flat YAML subset used by SKILL.md frontmatter.

    The repository intentionally has no third-party runtime dependency. This
    parser supports scalar keys and YAML folded/literal block scalars, rejects
    duplicate keys, and fails on unsupported nested YAML instead of accepting it
    ambiguously.
    """

    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    if not lines or lines[0] != "---":
        raise ValueError("missing opening '---'")

    try:
        closing = lines.index("---", 1)
    except ValueError as exc:
        raise ValueError("missing closing '---'") from exc

    data: dict[str, str] = {}
    frontmatter = lines[1:closing]
    index = 0
    while index < len(frontmatter):
        line = frontmatter[index]
        if not line.strip() or line.lstrip().startswith("#"):
            index += 1
            continue
        if line[:1].isspace() or ":" not in line:
            raise ValueError(f"unsupported YAML at frontmatter line {index + 2}")

        key, raw_value = line.split(":", 1)
        key = key.strip()
        raw_value = raw_value.strip()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]*", key):
            raise ValueError(f"invalid YAML key {key!r}")
        if key in data:
            raise ValueError(f"duplicate YAML key {key!r}")

        if raw_value in {">", ">-", ">+", "|", "|-", "|+"}:
            block: list[str] = []
            index += 1
            while index < len(frontmatter):
                block_line = frontmatter[index]
                if block_line and not block_line[:1].isspace():
                    break
                block.append(block_line.strip())
                index += 1
            separator = " " if raw_value.startswith(">") else "\n"
            value = separator.join(block).strip()
        else:
            if not raw_value:
                raise ValueError(f"empty or nested YAML value for {key!r}")
            if (raw_value.startswith("[") or raw_value.startswith("{")):
                raise ValueError(f"unsupported collection value for {key!r}")
            if len(raw_value) >= 2 and raw_value[0] == raw_value[-1] and raw_value[0] in "\"'":
                raw_value = raw_value[1:-1]
            value = raw_value
            index += 1

        data[key] = value

    return data


def extract_phase_routes(skill_text: str) -> dict[str, tuple[str, ...]]:
    lines = skill_text.splitlines()
    routes: dict[str, tuple[str, ...]] = {}
    phase_pattern = re.compile(
        rf"^({'|'.join(PHASE_ROUTES)})(?::|\s+→)", re.ASCII
    )
    path_pattern = re.compile(r"(?:templates|reviews)/[a-z-]+\.md")

    for index, line in enumerate(lines):
        match = phase_pattern.match(line.strip())
        if not match:
            continue
        phase = match.group(1)
        context = line if "→" in line else "\n".join(lines[index : index + 4])
        paths = tuple(dict.fromkeys(path_pattern.findall(context)))
        if paths:
            routes[phase] = paths
    return routes


def validate_frontmatter(validation: Validation, skill_dir: Path) -> None:
    path = skill_dir / "SKILL.md"
    try:
        metadata = parse_frontmatter(path)
    except (OSError, ValueError) as exc:
        validation.check(False, f"{path.relative_to(REPO_ROOT)}: invalid YAML frontmatter: {exc}")
        return

    validation.check(bool(metadata.get("name")), f"{path}: frontmatter name is required")
    validation.check(
        metadata.get("name") == skill_dir.name,
        f"{path}: frontmatter name must match directory name",
    )
    validation.check(
        bool(metadata.get("description")), f"{path}: frontmatter description is required"
    )


def validate_routes_and_files(validation: Validation, skill_dir: Path) -> None:
    skill_path = skill_dir / "SKILL.md"
    text = skill_path.read_text(encoding="utf-8")
    routes = extract_phase_routes(text)

    for phase, expected_paths in PHASE_ROUTES.items():
        for relative_path in expected_paths:
            validation.check(
                (skill_dir / relative_path).is_file(),
                f"{skill_dir.name}: missing {relative_path} required by {phase}",
            )
        validation.check(
            routes.get(phase) == expected_paths,
            f"{skill_dir.name}: {phase} routing is {routes.get(phase)!r}, expected {expected_paths!r}",
        )


def validate_shared_directories(validation: Validation) -> None:
    left, right = SKILL_DIRS
    for subdir in ("templates", "reviews"):
        left_files = {
            path.relative_to(left / subdir): path
            for path in (left / subdir).rglob("*")
            if path.is_file()
        }
        right_files = {
            path.relative_to(right / subdir): path
            for path in (right / subdir).rglob("*")
            if path.is_file()
        }
        validation.check(
            left_files.keys() == right_files.keys(),
            f"{subdir}/ file sets differ between skills",
        )
        for relative_path in sorted(left_files.keys() & right_files.keys()):
            validation.check(
                left_files[relative_path].read_bytes() == right_files[relative_path].read_bytes(),
                f"{subdir}/{relative_path} differs between skills",
            )


def validate_no_user_absolute_paths(validation: Validation) -> None:
    paths = [REPO_ROOT / name for name in REPOSITORY_DOCS]
    for skill_dir in SKILL_DIRS:
        paths.extend(skill_dir.rglob("*.md"))

    for path in sorted(paths):
        text = path.read_text(encoding="utf-8")
        for pattern in USER_ABSOLUTE_PATH_PATTERNS:
            match = pattern.search(text)
            validation.check(
                match is None,
                f"{path.relative_to(REPO_ROOT)}: user-specific absolute path {match.group(0)!r}"
                if match
                else "",
            )


def validate_repository_links(validation: Validation) -> None:
    """Reject stale relative links in the repository's maintained documents."""

    for relative in REPOSITORY_DOCS:
        document = REPO_ROOT / relative
        validation.check(document.is_file(), f"missing repository document: {relative}")
        if not document.is_file():
            continue
        text = markdown_prose_without_block_code(document.read_text(encoding="utf-8"))
        for raw_target in MARKDOWN_LINK_PATTERN.findall(text):
            target = markdown_link_destination(raw_target)
            parsed = urlsplit(target)
            if parsed.scheme or parsed.netloc or not parsed.path:
                continue
            linked = document.parent / unquote(parsed.path)
            validation.check(
                linked.exists(),
                f"{relative}: broken relative link {raw_target!r}",
            )


def markdown_prose_without_block_code(text: str) -> str:
    """Return Markdown prose while omitting fenced and indented code blocks."""

    prose: list[str] = []
    active_fence: tuple[str, int] | None = None
    in_indented_code = False
    previous_line_blank = True

    for line in text.splitlines():
        fence = MARKDOWN_FENCE_PATTERN.match(line)
        if active_fence is not None:
            if fence:
                marker, trailing = fence.groups()
                if (
                    marker[0] == active_fence[0]
                    and len(marker) >= active_fence[1]
                    and not trailing.strip()
                ):
                    active_fence = None
            continue

        if fence:
            marker = fence.group(1)
            active_fence = (marker[0], len(marker))
            in_indented_code = False
            continue

        if not line.strip():
            if not in_indented_code:
                prose.append(line)
            previous_line_blank = True
            continue

        indented = line.startswith("    ") or line.startswith("\t")
        if indented and (previous_line_blank or in_indented_code):
            in_indented_code = True
            previous_line_blank = False
            continue

        in_indented_code = False
        previous_line_blank = False
        prose.append(line)

    return "\n".join(prose)


def markdown_link_destination(raw_target: str) -> str:
    """Extract a Markdown link destination without discarding bracketed spaces."""

    stripped = raw_target.strip()
    if stripped.startswith("<"):
        closing_bracket = stripped.find(">", 1)
        if closing_bracket != -1:
            return stripped[1:closing_bracket]
    return stripped.split(maxsplit=1)[0]


def validate_version(validation: Validation) -> None:
    version_path = REPO_ROOT / "VERSION"
    validation.check(version_path.is_file(), "missing VERSION source of truth")
    if not version_path.is_file():
        return
    raw = version_path.read_text(encoding="utf-8")
    version = raw.strip()
    validation.check(
        raw == f"{version}\n" and bool(SEMVER_PATTERN.fullmatch(version)),
        "VERSION must contain one SemVer MAJOR.MINOR.PATCH line",
    )


def validate_policy_contracts(validation: Validation, skill_dir: Path) -> None:
    text = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
    for error_code in REQUIRED_ERROR_CODES:
        validation.check(
            error_code in text,
            f"{skill_dir.name}: missing required error code {error_code}",
        )

    validation.check(
        bool(
            re.search(
                r"IMPLEMENTATION[\s\S]{0,600}(?:Unit Test|Unit Tests)[\s\S]{0,300}(?:PASS|required|필수)",
                text,
                re.IGNORECASE,
            )
        ),
        f"{skill_dir.name}: missing IMPLEMENTATION Unit Test gate",
    )
    validation.check(
        bool(
            re.search(
                r"BUGFIX[\s\S]{0,400}(?:Regression Test|regression test)[\s\S]{0,200}(?:PASS|required|필수)",
                text,
                re.IGNORECASE,
            )
        ),
        f"{skill_dir.name}: missing BUGFIX Regression Test gate",
    )
    validation.check(
        bool(re.search(r"1\s*<=\s*max-iterations\s*<=\s*10", text)),
        f"{skill_dir.name}: missing max-iterations range 1 <= max-iterations <= 10",
    )


def validate_machine_readable_contracts(validation: Validation) -> None:
    contracts: list[tuple[Path, dict[str, object]]] = []
    for skill_dir in SKILL_DIRS:
        skill_path = skill_dir / "SKILL.md"
        skill_text = skill_path.read_text(encoding="utf-8")
        try:
            contract = load_policy_contract(skill_path)
        except (OSError, PolicyContractError) as exc:
            validation.check(False, str(exc))
            continue
        contracts.append((skill_dir, contract))

        validation.check(
            contract.get("schema_version") == 1,
            f"{skill_dir.name}: unsupported policy contract schema",
        )
        validation.check(
            contract.get("sequential_phases")
            == [phase.casefold() for phase in PHASE_ROUTES if phase not in {"BUGFIX", "REFACTORING"}],
            f"{skill_dir.name}: contract sequential phases do not match phase routing",
        )
        validation.check(
            contract.get("specialized_phases") == ["bugfix", "refactoring"],
            f"{skill_dir.name}: contract specialized phases are invalid",
        )
        validation.check(
            contract.get("supported_specialized_combinations")
            == [["bugfix"], ["refactoring"]],
            f"{skill_dir.name}: contract specialized combinations are invalid",
        )
        validation.check(
            contract.get("natural_language_automation")
            == {
                "deterministic_representative_terms_for": ["phases"],
                "llm_interpretation_required_for": [
                    "worker",
                    "reviewer",
                    "max-iterations",
                    "free-form phase requests",
                ],
            },
            f"{skill_dir.name}: natural-language automation scope is invalid",
        )

        raw_defaults = contract.get("defaults", {})
        defaults = raw_defaults if isinstance(raw_defaults, dict) else {}
        raw_known_commands = contract.get("known_agent_commands", [])
        known_commands = (
            raw_known_commands if isinstance(raw_known_commands, list) else []
        )
        # OS-4 R11: this is about the LEGACY default PAIR only -- the two commands a
        # profile-less invocation falls back to. It says nothing about a `defaults`
        # block inside an agent profile, where worker and reviewer are allowed to be
        # the same command (session separation is a different invariant, owned by the
        # reuse gate's role condition).
        validation.check(
            defaults.get("worker") in known_commands
            and defaults.get("reviewer") in known_commands
            and defaults.get("worker") != defaults.get("reviewer"),
            f"{skill_dir.name}: contract agent defaults/known commands are inconsistent",
        )
        validation.check(
            contract.get("agent_command_pattern") == "[A-Za-z0-9._-]+",
            f"{skill_dir.name}: contract agent command pattern is invalid",
        )
        validation.check(
            contract.get("custom_agent_command_pattern")
            == "(?:claude|codex)-[A-Za-z0-9._-]+",
            f"{skill_dir.name}: contract custom agent trust pattern is invalid",
        )
        validation.check(
            contract.get("agent_launch_arguments") == [],
            f"{skill_dir.name}: agent launch arguments must be empty",
        )
        validation.check(
            "--dangerously-skip-permissions" not in skill_text,
            f"{skill_dir.name}: contains a vendor-specific agent launch argument",
        )
        validation.check(
            f"DEFAULT_WORKER = {defaults.get('worker')}" in skill_text
            and f"DEFAULT_REVIEWER = {defaults.get('reviewer')}" in skill_text
            and f"DEFAULT_MAX_ITERATIONS = {defaults.get('max_iterations')}"
            in skill_text,
            f"{skill_dir.name}: human-readable defaults differ from contract",
        )

        known_commands_match = re.search(
            r"기본 known commands:\s*```text\s*(?P<values>.*?)\s*```",
            skill_text,
            re.DOTALL,
        )
        documented_known_commands = (
            [
                line.strip()
                for line in known_commands_match.group("values").splitlines()
                if line.strip()
            ]
            if known_commands_match
            else []
        )
        validation.check(
            documented_known_commands == known_commands,
            f"{skill_dir.name}: human-readable known commands differ from contract",
        )
        raw_iteration_range = contract.get("max_iterations", {})
        iteration_range = (
            raw_iteration_range if isinstance(raw_iteration_range, dict) else {}
        )
        validation.check(
            iteration_range.get("min") == 1
            and iteration_range.get("max") == 10
            and iteration_range["min"]
            <= defaults.get("max_iterations", 0)
            <= iteration_range["max"],
            f"{skill_dir.name}: contract max-iterations values are inconsistent",
        )
        validation.check(
            bool(
                re.search(
                    rf"{iteration_range.get('min')}\s*<=\s*max-iterations\s*<=\s*{iteration_range.get('max')}",
                    skill_text,
                )
            ),
            f"{skill_dir.name}: human-readable max-iterations range differs from contract",
        )

        raw_errors = contract.get("errors", {})
        errors = raw_errors if isinstance(raw_errors, dict) else {}
        required_error_keys = {
            "agent_not_allowed",
            "invalid_agent_command",
            "agent_command_not_found",
            "worker_reviewer_must_differ",
            "invalid_max_iterations",
            "invalid_phase",
            "invalid_phase_order",
            "phase_conflict",
            "unsupported_phase_combination",
            "invalid_agent_profile",
            "unknown_agent_profile",
            "agent_role_unresolved",
        }
        validation.check(
            set(errors) == required_error_keys
            and set(errors.values()).issubset(REQUIRED_ERROR_CODES),
            f"{skill_dir.name}: contract error mapping is incomplete or invalid",
        )
        validation.check(
            all(f"REASON: {error_code}" in skill_text for error_code in errors.values()),
            f"{skill_dir.name}: human-readable error mapping differs from contract",
        )

    if len(contracts) == len(SKILL_DIRS):
        validation.check(
            contracts[0][1] == contracts[1][1],
            "machine-readable policy contracts differ between skills",
        )


def parse_lifecycle_contract(skill_text: str) -> dict[str, tuple[str, ...]] | None:
    """Parse the section 6 anchor block into {KEY: (value, ...)}.

    Returns None when the block is absent or violates the format rules; the caller
    turns that single condition into one diagnostic instead of many derived ones.
    """
    match = LIFECYCLE_CONTRACT_BLOCK_PATTERN.search(skill_text)
    if match is None:
        return None
    parsed: dict[str, tuple[str, ...]] = {}
    for line in match.group("values").splitlines():
        line_match = LIFECYCLE_CONTRACT_LINE_PATTERN.fullmatch(line)
        if line_match is None:
            return None
        key, raw = line_match.group(1), line_match.group(2)
        if key in parsed:
            return None
        values = tuple(value.strip() for value in raw.split(","))
        if not all(
            LIFECYCLE_CONTRACT_TOKEN_PATTERN.fullmatch(value) for value in values
        ):
            return None
        parsed[key] = values
    return parsed


def lifecycle_contract_block_lines(skill_text: str) -> int:
    match = LIFECYCLE_CONTRACT_BLOCK_PATTERN.search(skill_text)
    if match is None:
        return 0
    return len(match.group("values").splitlines())


def validate_lifecycle_accounting_contract(validation: Validation) -> None:
    """Orchestration-only section 6 lifecycle contract. Not shared with the loop skill."""
    skill_path = LIFECYCLE_SKILL_DIR / "SKILL.md"
    try:
        skill_text = skill_path.read_text(encoding="utf-8")
    except OSError as exc:
        validation.check(False, f"{LIFECYCLE_SKILL_DIR.name}: {exc}")
        skill_text = ""

    parsed = parse_lifecycle_contract(skill_text)
    validation.check(
        parsed is not None,
        f"{LIFECYCLE_SKILL_DIR.name}: missing or malformed lifecycle accounting "
        "contract block",
    )
    parsed = parsed or {}

    validation.check(
        set(parsed) == set(LIFECYCLE_CONTRACT),
        f"{LIFECYCLE_SKILL_DIR.name}: lifecycle contract keys differ from the "
        "validator source of truth",
    )
    validation.check(
        parsed == LIFECYCLE_CONTRACT,
        f"{LIFECYCLE_SKILL_DIR.name}: lifecycle contract values differ from the "
        "validator source of truth",
    )
    validation.check(
        0 < lifecycle_contract_block_lines(skill_text) <= LIFECYCLE_CONTRACT_MAX_LINES,
        f"{LIFECYCLE_SKILL_DIR.name}: lifecycle contract block exceeds "
        f"{LIFECYCLE_CONTRACT_MAX_LINES} lines",
    )

    section = extract_lifecycle_section(skill_text)
    for label in LIFECYCLE_AXIS_LABELS:
        validation.check(
            label in section,
            f"{LIFECYCLE_SKILL_DIR.name}: lifecycle prose is missing axis label "
            f"{label}",
        )
    missing_outcomes = [
        outcome
        for outcome in LIFECYCLE_CONTRACT["LIFECYCLE_OUTCOMES"]
        if outcome not in section
    ]
    validation.check(
        not missing_outcomes,
        f"{LIFECYCLE_SKILL_DIR.name}: lifecycle prose is missing outcome "
        + ", ".join(missing_outcomes or ["-"]),
    )

    loop_skill = REPO_ROOT / "orca-worker-reviewer-loop" / "SKILL.md"
    loop_text = loop_skill.read_text(encoding="utf-8") if loop_skill.is_file() else ""
    validation.check(
        parse_lifecycle_contract(loop_text) is None,
        "orca-worker-reviewer-loop: must not contain the orchestration lifecycle "
        "contract",
    )

    never_close = set(parsed.get("NEVER_CLOSE_TERMINAL_ROLES", ()))
    close_eligible = set(parsed.get("CLOSE_ELIGIBLE_TERMINAL_ROLES", ()))
    all_roles = set(parsed.get("TERMINAL_ROLE_CLASSES", ()))
    expected_never_close = set(LIFECYCLE_CONTRACT["NEVER_CLOSE_TERMINAL_ROLES"])
    validation.check(
        never_close == expected_never_close,
        "NEVER_CLOSE_TERMINAL_ROLES must contain exactly "
        + ", ".join(sorted(expected_never_close)),
    )
    validation.check(
        close_eligible == set(LIFECYCLE_CONTRACT["CLOSE_ELIGIBLE_TERMINAL_ROLES"]),
        "CLOSE_ELIGIBLE_TERMINAL_ROLES must be exactly phase_worker, phase_reviewer",
    )
    validation.check(
        bool(all_roles)
        and not (never_close & close_eligible)
        and (never_close | close_eligible) == all_roles,
        "terminal role classes must partition into never-close and close-eligible",
    )
    validation.check(
        parsed.get("CLOSE_ALLOWED_ONLY_WHEN")
        == LIFECYCLE_CONTRACT["CLOSE_ALLOWED_ONLY_WHEN"],
        "CLOSE_ALLOWED_ONLY_WHEN must require a close eligible terminal role",
    )
    validation.check(
        "coordinator_session" in section,
        f"{LIFECYCLE_SKILL_DIR.name}: lifecycle prose must name coordinator_session",
    )


def extract_lifecycle_section(skill_text: str) -> str:
    """Return the body of section 6, where the lifecycle prose must live."""
    start = skill_text.find("## 6. Orca-native Worker Placement")
    if start == -1:
        return ""
    end = skill_text.find("\n## 7.", start)
    return skill_text[start:] if end == -1 else skill_text[start:end]



def parse_anchor_contract(
    skill_text: str, pattern: re.Pattern[str]
) -> dict[str, tuple[str, ...]] | None:
    """Parse any `#### <heading>` + ```text anchor block into {KEY: (value, ...)}.

    Shared by the three contracts added in this change. The two existing parsers stay
    separate on purpose: parse_final_review_contract's own docstring records that its
    separation exists because twelve regression tests bind to the lifecycle parser --
    a test-coupling reason, not a rule that every block needs its own parser. The new
    blocks carry no such coupling, so they share one implementation and only reuse the
    same two regexes the existing parsers already share.
    """
    match = pattern.search(skill_text)
    if match is None:
        return None
    parsed: dict[str, tuple[str, ...]] = {}
    for line in match.group("values").splitlines():
        line_match = LIFECYCLE_CONTRACT_LINE_PATTERN.fullmatch(line)
        if line_match is None:
            return None
        key, raw = line_match.group(1), line_match.group(2)
        if key in parsed:
            return None
        values = tuple(value.strip() for value in raw.split(","))
        if not all(
            LIFECYCLE_CONTRACT_TOKEN_PATTERN.fullmatch(value) for value in values
        ):
            return None
        parsed[key] = values
    return parsed


def anchor_contract_block_lines(skill_text: str, pattern: re.Pattern[str]) -> int:
    """0 when the block is absent, else its line count."""
    match = pattern.search(skill_text)
    return 0 if match is None else len(match.group("values").splitlines())


def extract_section(skill_text: str, heading: str, end_marker: str) -> str:
    """The body between `heading` and the next `end_marker`, or "" when absent."""
    start = skill_text.find(heading)
    if start == -1:
        return ""
    end = skill_text.find(end_marker, start)
    return skill_text[start:] if end == -1 else skill_text[start:end]


def parse_final_review_contract(skill_text: str) -> dict[str, tuple[str, ...]] | None:
    """Parse the section 17 anchor block into {KEY: (value, ...)}.

    Deliberately a separate implementation from parse_lifecycle_contract rather than a
    shared extraction: the two blocks have different key prefixes, different line caps
    and different structural cross-checks, and twelve existing regression tests bind to
    the lifecycle parser. Only the two shared regexes are reused.
    """
    match = FINAL_REVIEW_CONTRACT_BLOCK_PATTERN.search(skill_text)
    if match is None:
        return None
    parsed: dict[str, tuple[str, ...]] = {}
    for line in match.group("values").splitlines():
        line_match = LIFECYCLE_CONTRACT_LINE_PATTERN.fullmatch(line)
        if line_match is None:
            return None
        key, raw = line_match.group(1), line_match.group(2)
        if key in parsed:
            return None
        values = tuple(value.strip() for value in raw.split(","))
        if not all(
            LIFECYCLE_CONTRACT_TOKEN_PATTERN.fullmatch(value) for value in values
        ):
            return None
        parsed[key] = values
    return parsed


def final_review_contract_block_lines(skill_text: str) -> int:
    """0 when the block is absent, else its line count."""
    match = FINAL_REVIEW_CONTRACT_BLOCK_PATTERN.search(skill_text)
    if match is None:
        return 0
    return len(match.group("values").splitlines())


def extract_final_review_section(skill_text: str) -> str:
    """Return the body of section 17, where the final-review prose must live.

    Unlike extract_lifecycle_section this DOES return "" on a missing anchor, but the
    caller turns that into a dedicated diagnostic rather than letting the content
    checks fail with derived messages.
    """
    start = skill_text.find(FINAL_REVIEW_SECTION_HEADING)
    if start == -1:
        return ""
    end = skill_text.find(FINAL_REVIEW_SECTION_END, start)
    return skill_text[start:] if end == -1 else skill_text[start:end]


def validate_final_review_contract(validation: Validation) -> None:
    """Orchestration-only section 17 final adversarial review gate contract."""
    skill_path = LIFECYCLE_SKILL_DIR / "SKILL.md"
    try:
        skill_text = skill_path.read_text(encoding="utf-8")
    except OSError as exc:
        validation.check(False, f"{LIFECYCLE_SKILL_DIR.name}: {exc}")
        skill_text = ""

    parsed = parse_final_review_contract(skill_text)
    validation.check(
        parsed is not None,
        f"{LIFECYCLE_SKILL_DIR.name}: missing or malformed final review contract "
        "block",
    )
    parsed = parsed or {}

    validation.check(
        set(parsed) == set(FINAL_REVIEW_CONTRACT),
        f"{LIFECYCLE_SKILL_DIR.name}: final review contract keys differ from the "
        "validator source of truth",
    )
    validation.check(
        parsed == FINAL_REVIEW_CONTRACT,
        f"{LIFECYCLE_SKILL_DIR.name}: final review contract values differ from the "
        "validator source of truth",
    )
    validation.check(
        0 < final_review_contract_block_lines(skill_text)
        <= FINAL_REVIEW_CONTRACT_MAX_LINES,
        f"{LIFECYCLE_SKILL_DIR.name}: final review contract block exceeds "
        f"{FINAL_REVIEW_CONTRACT_MAX_LINES} lines",
    )

    section = extract_final_review_section(skill_text)
    validation.check(
        section != "",
        f"{LIFECYCLE_SKILL_DIR.name}: section 17 Final Adversarial Review is missing "
        "or renumbered",
    )
    validation.check(
        FINAL_REVIEW_ANTI_ANCHORING_SENTENCES[0] in section,
        f"{LIFECYCLE_SKILL_DIR.name}: final review prose is missing the "
        "anti-anchoring premise (ko)",
    )
    validation.check(
        FINAL_REVIEW_ANTI_ANCHORING_SENTENCES[1] in section,
        f"{LIFECYCLE_SKILL_DIR.name}: final review prose is missing the "
        "anti-anchoring premise (en)",
    )
    validation.check(
        "Responsible Phase" in section,
        f"{LIFECYCLE_SKILL_DIR.name}: final review prose is missing the Responsible "
        "Phase field",
    )
    validation.check(
        "FINAL_REVIEW_MAX_ITERATIONS_REACHED" in section,
        f"{LIFECYCLE_SKILL_DIR.name}: final review prose is missing the exhaustion "
        "reason",
    )
    validation.check(
        "OUT_OF_SCOPE_FINAL_REVIEW_FINDING" in section,
        f"{LIFECYCLE_SKILL_DIR.name}: final review prose is missing the out-of-scope "
        "reason",
    )
    missing_anchors = [
        anchor for anchor in FINAL_REVIEW_CHECKLIST_ANCHORS if anchor not in section
    ]
    validation.check(
        not missing_anchors,
        f"{LIFECYCLE_SKILL_DIR.name}: final review checklist is missing "
        + ", ".join(missing_anchors or ["-"]),
    )

    outcomes = set(parsed.get("FINAL_REVIEW_WORKER_RESOURCE_OUTCOMES", ()))
    lifecycle_outcomes = set(LIFECYCLE_CONTRACT["LIFECYCLE_OUTCOMES"])
    validation.check(
        "reuse" not in outcomes,
        "FINAL_REVIEW_WORKER_RESOURCE_OUTCOMES must never contain reuse",
    )
    validation.check(
        bool(outcomes) and outcomes < lifecycle_outcomes,
        "FINAL_REVIEW_WORKER_RESOURCE_OUTCOMES must be a strict subset of "
        "LIFECYCLE_OUTCOMES",
    )
    role = parsed.get("FINAL_REVIEW_ROLE", ())
    validation.check(
        bool(role) and role[0] in LIFECYCLE_CONTRACT["CLOSE_ELIGIBLE_TERMINAL_ROLES"],
        "FINAL_REVIEW_ROLE must be a close eligible terminal role",
    )

    loop_skill = REPO_ROOT / "orca-worker-reviewer-loop" / "SKILL.md"
    loop_text = loop_skill.read_text(encoding="utf-8") if loop_skill.is_file() else ""
    validation.check(
        parse_final_review_contract(loop_text) is None,
        "orca-worker-reviewer-loop: must not contain the orchestration final review "
        "contract",
    )
    validation.check(
        "Final Adversarial Review" not in loop_text,
        "orca-worker-reviewer-loop: must not describe the final adversarial review "
        "gate",
    )

    validation.check(
        FINAL_REVIEW_BARE_CHOICE_LINE.search(skill_text) is None,
        f"{LIFECYCLE_SKILL_DIR.name}: FINAL_REVIEW must not be written as a "
        "PASS | FAIL choice line",
    )


def validate_reuse_contract(validation: Validation) -> None:
    """The section 6 session reuse anchor block, and the prose that must back it."""
    skill_path = LIFECYCLE_SKILL_DIR / "SKILL.md"
    if not skill_path.is_file():
        return
    skill_text = skill_path.read_text(encoding="utf-8")

    parsed = parse_anchor_contract(skill_text, REUSE_CONTRACT_BLOCK_PATTERN)
    validation.check(
        parsed is not None,
        f"{LIFECYCLE_SKILL_DIR.name}: session reuse contract block is missing or "
        "malformed",
    )
    if parsed is None:
        return
    validation.check(
        set(parsed) == set(REUSE_CONTRACT),
        f"{LIFECYCLE_SKILL_DIR.name}: session reuse contract keys drifted",
    )
    validation.check(
        parsed == REUSE_CONTRACT,
        f"{LIFECYCLE_SKILL_DIR.name}: session reuse contract values drifted",
    )
    validation.check(
        0
        < anchor_contract_block_lines(skill_text, REUSE_CONTRACT_BLOCK_PATTERN)
        <= REUSE_CONTRACT_MAX_LINES,
        f"{LIFECYCLE_SKILL_DIR.name}: session reuse contract block exceeds "
        f"{REUSE_CONTRACT_MAX_LINES} lines",
    )
    eligibility = parsed.get("REUSE_ELIGIBILITY", ())
    validation.check(
        len(eligibility) == 8,
        "REUSE_ELIGIBILITY must list exactly eight conditions",
    )
    validation.check(
        "zero_lifecycle_commands" in parsed.get("REUSE_TERMINATION", ()),
        "REUSE_TERMINATION must keep zero_lifecycle_commands",
    )

    section = extract_section(skill_text, REUSE_SECTION_HEADING, REUSE_SECTION_END)
    missing = [token for token in eligibility if token not in section]
    validation.check(
        not missing,
        f"{LIFECYCLE_SKILL_DIR.name}: section 6 prose is missing reuse conditions "
        + ", ".join(missing or ["-"]),
    )
    validation.check(
        REUSE_ZERO_COMMAND_SENTENCE in section,
        f"{LIFECYCLE_SKILL_DIR.name}: section 6 prose is missing the zero lifecycle "
        "command sentence",
    )
    validation.check(
        REUSE_ROLE_TABLE_DRIFT not in skill_text,
        f"{LIFECYCLE_SKILL_DIR.name}: the terminal role table must not call a reused "
        "terminal external_or_adopted",
    )

    loop_skill = REPO_ROOT / "orca-worker-reviewer-loop" / "SKILL.md"
    loop_text = loop_skill.read_text(encoding="utf-8") if loop_skill.is_file() else ""
    validation.check(
        parse_anchor_contract(loop_text, REUSE_CONTRACT_BLOCK_PATTERN) is None,
        "orca-worker-reviewer-loop: must not contain the session reuse contract",
    )


def validate_task_boundary_contract(validation: Validation) -> None:
    """The section 9 task boundary anchor block: two layers, and neither id in layer 1."""
    skill_path = LIFECYCLE_SKILL_DIR / "SKILL.md"
    if not skill_path.is_file():
        return
    skill_text = skill_path.read_text(encoding="utf-8")

    parsed = parse_anchor_contract(skill_text, TASK_BOUNDARY_CONTRACT_BLOCK_PATTERN)
    validation.check(
        parsed is not None,
        f"{LIFECYCLE_SKILL_DIR.name}: task boundary contract block is missing or "
        "malformed",
    )
    if parsed is None:
        return
    validation.check(
        set(parsed) == set(TASK_BOUNDARY_CONTRACT),
        f"{LIFECYCLE_SKILL_DIR.name}: task boundary contract keys drifted",
    )
    validation.check(
        parsed == TASK_BOUNDARY_CONTRACT,
        f"{LIFECYCLE_SKILL_DIR.name}: task boundary contract values drifted",
    )
    validation.check(
        0
        < anchor_contract_block_lines(
            skill_text, TASK_BOUNDARY_CONTRACT_BLOCK_PATTERN
        )
        <= TASK_BOUNDARY_CONTRACT_MAX_LINES,
        f"{LIFECYCLE_SKILL_DIR.name}: task boundary contract block exceeds "
        f"{TASK_BOUNDARY_CONTRACT_MAX_LINES} lines",
    )
    boundary_keys = set(parsed.get("TASK_BOUNDARY_KEYS", ()))
    validation.check(
        "task_id" not in boundary_keys and "dispatch_id" not in boundary_keys,
        "TASK_BOUNDARY_KEYS must not carry an id that does not exist when the Task "
        "spec body is written",
    )
    validation.check(
        {"task_id", "dispatch_id"}
        <= set(parsed.get("DISPATCH_INJECTED_IDENTITY", ())),
        "DISPATCH_INJECTED_IDENTITY must name both task_id and dispatch_id",
    )
    validation.check(
        "new_value_every_attempt" in parsed.get("DISPATCH_IDENTITY_RULE", ()),
        "DISPATCH_IDENTITY_RULE must forbid identity carry-over",
    )
    validation.check(
        {"previous_task_id", "previous_dispatch_id"}
        <= set(parsed.get("TASK_BOUNDARY_NEVER_CARRIED", ())),
        "TASK_BOUNDARY_NEVER_CARRIED must keep both previous ids",
    )

    section = extract_section(
        skill_text, TASK_BOUNDARY_SECTION_HEADING, TASK_BOUNDARY_SECTION_END
    )
    validation.check(
        TASK_BOUNDARY_WRITE_ONCE_SENTENCE in section,
        f"{LIFECYCLE_SKILL_DIR.name}: section 9 prose is missing the write-once "
        "task spec premise",
    )

    loop_skill = REPO_ROOT / "orca-worker-reviewer-loop" / "SKILL.md"
    loop_text = loop_skill.read_text(encoding="utf-8") if loop_skill.is_file() else ""
    validation.check(
        parse_anchor_contract(loop_text, TASK_BOUNDARY_CONTRACT_BLOCK_PATTERN) is None,
        "orca-worker-reviewer-loop: must not contain the task boundary contract",
    )


def validate_reviewer_context_contract(validation: Validation) -> None:
    """The section 11 delta-first Reviewer context block, and its anti-weakening guard."""
    skill_path = LIFECYCLE_SKILL_DIR / "SKILL.md"
    if not skill_path.is_file():
        return
    skill_text = skill_path.read_text(encoding="utf-8")

    parsed = parse_anchor_contract(skill_text, REVIEWER_CONTEXT_CONTRACT_BLOCK_PATTERN)
    validation.check(
        parsed is not None,
        f"{LIFECYCLE_SKILL_DIR.name}: reviewer context contract block is missing or "
        "malformed",
    )
    if parsed is None:
        return
    validation.check(
        set(parsed) == set(REVIEWER_CONTEXT_CONTRACT),
        f"{LIFECYCLE_SKILL_DIR.name}: reviewer context contract keys drifted",
    )
    validation.check(
        parsed == REVIEWER_CONTEXT_CONTRACT,
        f"{LIFECYCLE_SKILL_DIR.name}: reviewer context contract values drifted",
    )
    validation.check(
        0
        < anchor_contract_block_lines(
            skill_text, REVIEWER_CONTEXT_CONTRACT_BLOCK_PATTERN
        )
        <= REVIEWER_CONTEXT_CONTRACT_MAX_LINES,
        f"{LIFECYCLE_SKILL_DIR.name}: reviewer context contract block exceeds "
        f"{REVIEWER_CONTEXT_CONTRACT_MAX_LINES} lines",
    )
    context_keys = parsed.get("REVIEWER_CONTEXT_KEYS", ())
    validation.check(
        "drill_down" in context_keys and len(context_keys) == 8,
        "REVIEWER_CONTEXT_KEYS must keep all eight keys including drill_down",
    )
    validation.check(
        parsed.get("REVIEWER_CONTEXT_EXCLUDES") == ("final_adversarial_review",),
        "REVIEWER_CONTEXT_EXCLUDES must keep the final adversarial review carve-out",
    )

    section = extract_section(
        skill_text, REVIEWER_CONTEXT_SECTION_HEADING, REVIEWER_CONTEXT_SECTION_END
    )
    validation.check(
        REVIEWER_DIRECT_VERIFICATION_SENTENCE in section,
        f"{LIFECYCLE_SKILL_DIR.name}: delta-first context must not remove the "
        "reviewer's direct verification duty",
    )
    validation.check(
        REVIEWER_DRILL_DOWN_SENTENCE in section,
        f"{LIFECYCLE_SKILL_DIR.name}: section 11 prose is missing the "
        "delta-is-not-a-boundary sentence",
    )

    loop_skill = REPO_ROOT / "orca-worker-reviewer-loop" / "SKILL.md"
    loop_text = loop_skill.read_text(encoding="utf-8") if loop_skill.is_file() else ""
    validation.check(
        parse_anchor_contract(loop_text, REVIEWER_CONTEXT_CONTRACT_BLOCK_PATTERN)
        is None,
        "orca-worker-reviewer-loop: must not contain the reviewer context contract",
    )


def validate_quality_profile_contract(validation: Validation) -> None:
    """The section 11 quality profile block, and the review policy that must back it.

    Two halves on purpose. The anchor block is the machine-readable claim; the
    reviews/common.md anchors are the check that the claim reached the text a phase
    Reviewer is actually routed to. A SKILL.md-only change would satisfy the first
    and leave the review gate exactly as broad and generic as it was.
    """
    skill_path = LIFECYCLE_SKILL_DIR / "SKILL.md"
    if not skill_path.is_file():
        return
    skill_text = skill_path.read_text(encoding="utf-8")

    parsed = parse_anchor_contract(skill_text, QUALITY_PROFILE_CONTRACT_BLOCK_PATTERN)
    validation.check(
        parsed is not None,
        f"{LIFECYCLE_SKILL_DIR.name}: quality profile contract block is missing or "
        "malformed",
    )
    if parsed is None:
        return
    validation.check(
        set(parsed) == set(QUALITY_PROFILE_CONTRACT),
        f"{LIFECYCLE_SKILL_DIR.name}: quality profile contract keys drifted",
    )
    validation.check(
        parsed == QUALITY_PROFILE_CONTRACT,
        f"{LIFECYCLE_SKILL_DIR.name}: quality profile contract values drifted",
    )
    validation.check(
        0
        < anchor_contract_block_lines(
            skill_text, QUALITY_PROFILE_CONTRACT_BLOCK_PATTERN
        )
        <= QUALITY_PROFILE_CONTRACT_MAX_LINES,
        f"{LIFECYCLE_SKILL_DIR.name}: quality profile contract block exceeds "
        f"{QUALITY_PROFILE_CONTRACT_MAX_LINES} lines",
    )
    validation.check(
        parsed.get("QUALITY_GATE_WORKFLOW_VALUES") == ("pass", "fail"),
        "QUALITY_GATE_WORKFLOW_VALUES must stay two-valued so PASS WITH NOTES and "
        "BLOCKED never become lifecycle states",
    )
    validation.check(
        set(parsed.get("QUALITY_GATE_WORKFLOW_VALUES", ()))
        < set(parsed.get("QUALITY_GATE_VERDICTS", ())),
        "QUALITY_GATE_WORKFLOW_VALUES must be a strict subset of QUALITY_GATE_VERDICTS",
    )
    validation.check(
        len(parsed.get("QUALITY_GATE_GENERAL_IDS", ())) == 5,
        "the minimal general gate must stay five ids",
    )
    validation.check(
        set(parsed.get("QUALITY_GATE_CONTEXT_ROLES", ())) >= {"worker", "reviewer"},
        "QUALITY_GATE_CONTEXT_ROLES must reach the Worker as well as the Reviewer",
    )

    section = extract_section(
        skill_text, REVIEWER_CONTEXT_SECTION_HEADING, REVIEWER_CONTEXT_SECTION_END
    )
    for anchor in QUALITY_PROFILE_PROSE_ANCHORS:
        validation.check(
            anchor in section,
            f"{LIFECYCLE_SKILL_DIR.name}: section 11 quality profile prose is missing "
            f"{anchor!r}",
        )

    for skill_dir in SKILL_DIRS:
        policy_path = skill_dir / "reviews" / "common.md"
        if not policy_path.is_file():
            continue
        policy_text = policy_path.read_text(encoding="utf-8")
        for anchor in QUALITY_GATE_REVIEW_POLICY_ANCHORS:
            validation.check(
                anchor in policy_text,
                f"{skill_dir.name}: reviews/common.md is missing the profile-first "
                f"anchor {anchor!r}",
            )

    loop_skill = REPO_ROOT / "orca-worker-reviewer-loop" / "SKILL.md"
    loop_text = loop_skill.read_text(encoding="utf-8") if loop_skill.is_file() else ""
    validation.check(
        parse_anchor_contract(loop_text, QUALITY_PROFILE_CONTRACT_BLOCK_PATTERN)
        is None,
        "orca-worker-reviewer-loop: must not contain the quality profile contract",
    )


def validate_risk_profile_contract(validation: Validation) -> None:
    """The section 8 risk block, and the section 4/6/8 prose it is only an index into.

    Imports load_risk_contract from skill_policy rather than re-implementing the
    parse, extending the dependency direction this module already has. One parser
    means the runtime evaluator and this validator cannot disagree about the block.
    """
    skill_path = LIFECYCLE_SKILL_DIR / "SKILL.md"
    if not skill_path.is_file():
        return
    skill_text = skill_path.read_text(encoding="utf-8")

    parsed = load_risk_contract(skill_path)
    validation.check(
        parsed is not None,
        f"{LIFECYCLE_SKILL_DIR.name}: risk profile contract block is missing or "
        "malformed",
    )
    if parsed is None:
        return
    validation.check(
        set(parsed) == set(RISK_CONTRACT),
        f"{LIFECYCLE_SKILL_DIR.name}: risk profile contract keys drifted",
    )
    validation.check(
        parsed == RISK_CONTRACT,
        f"{LIFECYCLE_SKILL_DIR.name}: risk profile contract values drifted",
    )
    validation.check(
        0
        < anchor_contract_block_lines(skill_text, RISK_CONTRACT_BLOCK_PATTERN)
        <= RISK_CONTRACT_MAX_LINES,
        f"{LIFECYCLE_SKILL_DIR.name}: risk profile contract block exceeds "
        f"{RISK_CONTRACT_MAX_LINES} lines",
    )

    # Internal consistency: the three facts the runtime actually branches on.
    validation.check(
        parsed.get("RISK_DEFAULT", ("",))[0] in parsed.get("RISK_LEVELS", ()),
        "RISK_DEFAULT must be one of RISK_LEVELS",
    )
    validation.check(
        parsed.get("RISK_SELECTION_SOURCES") == ("explicit", "default"),
        "RISK_SELECTION_SOURCES must be exactly explicit, default",
    )
    validation.check(
        parsed.get("RISK_LOW_TASK_GRAPH") != parsed.get("RISK_MEDIUM_TASK_GRAPH")
        and parsed.get("RISK_MEDIUM_TASK_GRAPH")
        == parsed.get("RISK_HIGH_TASK_GRAPH"),
        "LOW must differ from MEDIUM/HIGH in task graph shape, and MEDIUM must equal "
        "HIGH",
    )
    validation.check(
        parsed.get("RISK_DOWNSTREAM_REVALIDATION") == ("high_only",),
        "downstream revalidation must be HIGH-only",
    )

    section = extract_section(skill_text, RISK_SECTION_HEADING, RISK_SECTION_END)
    for anchor in RISK_PROSE_ANCHORS:
        validation.check(
            anchor in section,
            f"{LIFECYCLE_SKILL_DIR.name}: section 8 risk prose is missing "
            f"{anchor!r}",
        )
    for anchor in (RISK_PARAMETER_DOC_ANCHOR, "DEFAULT_RISK = high"):
        validation.check(
            anchor in skill_text,
            f"{LIFECYCLE_SKILL_DIR.name}: section 4 does not document {anchor!r}",
        )
    validation.check(
        RISK_TASK_GRAPH_PROSE_ANCHOR in extract_lifecycle_section(skill_text),
        f"{LIFECYCLE_SKILL_DIR.name}: section 6 does not make the task graph "
        "risk-conditional",
    )

    loop_skill = REPO_ROOT / "orca-worker-reviewer-loop" / "SKILL.md"
    validation.check(
        not loop_skill.is_file() or load_risk_contract(loop_skill) is None,
        "orca-worker-reviewer-loop: must not contain the risk profile contract",
    )
    loop_text = loop_skill.read_text(encoding="utf-8") if loop_skill.is_file() else ""
    validation.check(
        "INVALID_RISK" not in loop_text,
        "orca-worker-reviewer-loop: must not carry the orchestration-only "
        "INVALID_RISK error code",
    )


def validate_agent_profile_contract(validation: Validation) -> None:
    """The OS-4 anchor block, and the prose in both skills it indexes.

    Reuses parse_anchor_contract rather than adding a ninth parser: this block has
    no test coupling of its own, which is the condition that function's docstring
    names for sharing one implementation.
    """
    skill_path = LIFECYCLE_SKILL_DIR / "SKILL.md"
    if not skill_path.is_file():
        return
    skill_text = skill_path.read_text(encoding="utf-8")

    parsed = parse_anchor_contract(skill_text, AGENT_PROFILE_CONTRACT_BLOCK_PATTERN)
    validation.check(
        parsed is not None,
        f"{LIFECYCLE_SKILL_DIR.name}: agent profile contract block is missing or "
        "malformed",
    )
    if parsed is None:
        return
    validation.check(
        set(parsed) == set(AGENT_PROFILE_CONTRACT),
        f"{LIFECYCLE_SKILL_DIR.name}: agent profile contract keys drifted",
    )
    validation.check(
        parsed == AGENT_PROFILE_CONTRACT,
        f"{LIFECYCLE_SKILL_DIR.name}: agent profile contract values drifted",
    )
    validation.check(
        0
        < anchor_contract_block_lines(skill_text, AGENT_PROFILE_CONTRACT_BLOCK_PATTERN)
        <= AGENT_PROFILE_CONTRACT_MAX_LINES,
        f"{LIFECYCLE_SKILL_DIR.name}: agent profile contract block exceeds "
        f"{AGENT_PROFILE_CONTRACT_MAX_LINES} lines",
    )

    # Internal consistency: the two facts the runtime branches on. LOW is the level
    # that makes a phase Reviewer optional, and it is the only one.
    validation.check(
        "phase_reviewer" not in parsed.get("AGENT_PROFILE_REQUIRED_ROLES_LOW", ()),
        "AGENT_PROFILE_REQUIRED_ROLES_LOW must not require a phase reviewer",
    )
    validation.check(
        all(
            "phase_reviewer" in parsed.get(key, ())
            for key in (
                "AGENT_PROFILE_REQUIRED_ROLES_MEDIUM",
                "AGENT_PROFILE_REQUIRED_ROLES_HIGH",
            )
        ),
        "AGENT_PROFILE_REQUIRED_ROLES_MEDIUM/HIGH must require a phase reviewer",
    )
    validation.check(
        all(
            "final_reviewer" in parsed.get(key, ())
            for key in (
                "AGENT_PROFILE_REQUIRED_ROLES_LOW",
                "AGENT_PROFILE_REQUIRED_ROLES_MEDIUM",
                "AGENT_PROFILE_REQUIRED_ROLES_HIGH",
            )
        ),
        "the final reviewer must be required at every risk level",
    )
    # The two chains disagree about their first entry on purpose. If they ever match,
    # one of them has been "corrected" into the other.
    validation.check(
        parsed.get("AGENT_PROFILE_PHASE_REVIEWER_PRECEDENCE")
        != parsed.get("AGENT_PROFILE_FINAL_REVIEWER_PRECEDENCE"),
        "the phase reviewer and final reviewer precedence chains must differ",
    )

    for skill_dir in SKILL_DIRS:
        text = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
        validation.check(
            AGENT_PROFILE_PARAMETER_DOC_ANCHOR in text,
            f"{skill_dir.name}: missing the profile=<name> runtime parameter",
        )
        validation.check(
            AGENT_PROFILE_LEGACY_PROSE_ANCHOR in text,
            f"{skill_dir.name}: missing the omitted-profile legacy guarantee",
        )
        validation.check(
            AGENT_PROFILE_SAFETY_ALL_ENTRIES_PROSE_ANCHOR in text,
            f"{skill_dir.name}: missing the all-entries token/allowlist safety scope",
        )
        validation.check(
            AGENT_PROFILE_PATH_REQUIRED_ONLY_PROSE_ANCHOR in text,
            f"{skill_dir.name}: missing the required-role-only PATH gate scope",
        )

    loop_text = (
        REPO_ROOT / "orca-worker-reviewer-loop" / "SKILL.md"
    ).read_text(encoding="utf-8")
    for anchor in LOOP_AGENT_PROFILE_PROSE_ANCHORS:
        validation.check(
            anchor in loop_text,
            f"orca-worker-reviewer-loop: missing agent profile prose anchor {anchor!r}",
        )


def validate_decision_gate_contract(validation: Validation) -> None:
    """OS-29: the orchestration-only gate contract, and the semantics both skills share.

    Three drift directions, all of which must FAIL, and each of which the other two
    cannot catch:

    (a) a mirrored semantics sentence changed or deleted in ONE skill;
    (b) the same sentence deleted from BOTH -- which byte-equality between the two
        files would report as agreement;
    (c) the orchestration-only lifecycle block copied INTO the loop skill, which is
        the drift that would make the loop claim an Orca lifecycle it does not have.

    Reuses parse_anchor_contract rather than adding an eleventh parser, on the
    condition that function's own docstring names: this block has no test coupling of
    its own.
    """
    skill_path = LIFECYCLE_SKILL_DIR / "SKILL.md"
    loop_dir = next(d for d in SKILL_DIRS if d != LIFECYCLE_SKILL_DIR)
    loop_path = loop_dir / "SKILL.md"
    if not skill_path.is_file() or not loop_path.is_file():
        return
    skill_text = skill_path.read_text(encoding="utf-8")
    loop_text = loop_path.read_text(encoding="utf-8")

    parsed = parse_anchor_contract(skill_text, DECISION_GATE_CONTRACT_BLOCK_PATTERN)
    validation.check(
        parsed is not None,
        f"{LIFECYCLE_SKILL_DIR.name}: decision gate contract block is missing or "
        "malformed",
    )
    if parsed is not None:
        validation.check(
            set(parsed) == set(DECISION_GATE_CONTRACT),
            f"{LIFECYCLE_SKILL_DIR.name}: decision gate contract keys drifted",
        )
        validation.check(
            parsed == DECISION_GATE_CONTRACT,
            f"{LIFECYCLE_SKILL_DIR.name}: decision gate contract values drifted",
        )
        validation.check(
            0
            < anchor_contract_block_lines(
                skill_text, DECISION_GATE_CONTRACT_BLOCK_PATTERN
            )
            <= DECISION_GATE_CONTRACT_MAX_LINES,
            f"{LIFECYCLE_SKILL_DIR.name}: decision gate contract block exceeds "
            f"{DECISION_GATE_CONTRACT_MAX_LINES} lines",
        )
        # Internal consistency: the block's own vocabulary must be the code's. A
        # block that named a boundary or a blocking state the gate does not
        # implement would be a contract for something else.
        validation.check(
            tuple(
                value.upper() for value in parsed["DECISION_GATE_BLOCKING_STATES"]
            )
            == tuple(BLOCKING_STATES),
            "DECISION_GATE_BLOCKING_STATES must name exactly the blocking states",
        )
        validation.check(
            len(parsed["DECISION_GATE_BOUNDARIES"]) == len(BOUNDARIES),
            "DECISION_GATE_BOUNDARIES must name one value per gate boundary",
        )

    # (a) + (b): the mirrored semantics, checked in BOTH skills by anchor.
    for anchor in MIRRORED_DECISION_SEMANTICS_ANCHORS:
        for skill_dir, text in (
            (LIFECYCLE_SKILL_DIR, skill_text),
            (loop_dir, loop_text),
        ):
            validation.check(
                anchor in text,
                f"{skill_dir.name}: missing mirrored decision semantics anchor "
                f"{anchor!r}",
            )

    # (c): the lifecycle block is orchestration-only and must stay that way.
    validation.check(
        DECISION_GATE_CONTRACT_HEADING in skill_text,
        f"{LIFECYCLE_SKILL_DIR.name}: missing {DECISION_GATE_CONTRACT_HEADING!r}",
    )
    validation.check(
        DECISION_GATE_CONTRACT_HEADING not in loop_text,
        f"{loop_dir.name}: carries the orchestration-only "
        f"{DECISION_GATE_CONTRACT_HEADING!r}; decision SEMANTICS are mirrored, Orca "
        "lifecycle is not",
    )
    validation.check(
        parse_anchor_contract(loop_text, DECISION_GATE_CONTRACT_BLOCK_PATTERN) is None,
        f"{loop_dir.name}: must carry no decision gate anchor contract block",
    )

    # The result contract itself, in both skills and in every routed document.
    for skill_dir, text in ((LIFECYCLE_SKILL_DIR, skill_text), (loop_dir, loop_text)):
        validation.check(
            text.count(DECISION_GATE_RESULT_CONTRACT_ANCHOR) >= 2,
            f"{skill_dir.name}: the Worker and Reviewer result contracts must both "
            f"declare {DECISION_GATE_RESULT_CONTRACT_ANCHOR!r}",
        )
    for skill_dir in SKILL_DIRS:
        for relative in (
            *(f"templates/{phase.casefold()}.md" for phase in PHASE_ROUTES),
            "reviews/common.md",
        ):
            path = skill_dir / relative
            if not path.is_file():
                continue
            document = path.read_text(encoding="utf-8")
            validation.check(
                DECISION_GATE_RESULT_CONTRACT_ANCHOR in document,
                f"{skill_dir.name}: {relative} is missing the decision gate result "
                "contract",
            )
            # The gate result is REQUIRED and the narrative section stays OPTIONAL.
            # Checked together, in one place, because the whole risk of adding the
            # first is that it quietly makes the second mandatory.
            validation.check(
                DECISION_RECORD_OPTIONALITY_ANCHOR in document,
                f"{skill_dir.name}: {relative} lost the decision record optionality "
                "sentence while gaining the gate result contract",
            )


def validate_decision_policy_contract(validation: Validation) -> None:
    """OS-28 checks C1-C14. Imports the loader rather than re-parsing the block, the
    same dependency direction validate_risk_profile_contract has toward
    skill_policy.load_risk_contract -- so the runtime evaluator and this validator
    cannot disagree about what the contract says."""

    policies = {}
    for skill_dir in SKILL_DIRS:
        skill_path = skill_dir / "SKILL.md"
        if not skill_path.is_file():
            continue
        try:  # C1
            policies[skill_dir.name] = load_decision_policy(skill_path)
        except (OSError, PolicyContractError, DecisionPolicyError) as exc:
            validation.check(
                False, f"{skill_dir.name}: decision policy contract is missing or malformed: {exc}"
            )

    if len(policies) != len(SKILL_DIRS):
        return

    for name, policy in policies.items():
        observed = {
            code: (spec.state, spec.clause, spec.boundary_element)
            for code, spec in policy.reason_codes.items()
        }
        validation.check(  # C2
            set(observed) == set(DECISION_POLICY_REASON_CODES),
            f"{name}: decision policy contract keys drifted",
        )
        validation.check(  # C3
            observed == DECISION_POLICY_REASON_CODES,
            f"{name}: decision policy contract values drifted",
        )
        validation.check(  # C5
            len(policy.reason_codes) == DECISION_POLICY_CODE_COUNT,
            f"{name}: decision policy reason-code cardinality drifted "
            f"(expected {DECISION_POLICY_CODE_COUNT})",
        )
        per_state = {
            state: sum(1 for c in policy.reason_codes.values() if c.state == state)
            for state in DECISION_POLICY_PER_STATE
        }
        validation.check(
            per_state == DECISION_POLICY_PER_STATE,
            f"{name}: decision policy per-state reason-code split drifted",
        )
        validation.check(
            tuple(policy.boundary_elements) == DECISION_POLICY_BOUNDARY_ELEMENTS,
            f"{name}: decision policy boundary elements drifted",
        )
        for code, spec in sorted(policy.reason_codes.items()):  # C6
            if spec.state in policy.entry_clauses:
                ok = spec.clause in policy.entry_clauses[spec.state]
            else:
                ok = spec.clause is None
            validation.check(
                ok, f"{name}: reason code {code} has no valid entry clause"
            )
        skill_text = (REPO_ROOT / name / "SKILL.md").read_text(encoding="utf-8")
        match = DECISION_POLICY_BLOCK_PATTERN.search(skill_text)
        validation.check(  # C7
            match is not None
            and 0 < len(match.group("body").splitlines()) <= DECISION_POLICY_MAX_LINES,
            f"{name}: decision policy contract block exceeds "
            f"{DECISION_POLICY_MAX_LINES} lines",
        )
        forbidden = {
            pair for pair, rule in policy.transitions.items() if rule == "forbidden"
        }
        validation.check(  # C8
            forbidden == DECISION_POLICY_FORBIDDEN_CELLS,
            f"{name}: NEEDS_INPUT/CONFLICT -> ASSUMPTION_ALLOWED must be forbidden",
        )
        validation.check(  # C9
            policy.assumption_allowed_forbidden_when.get("exception_allowed") is False,
            f"{name}: INV-4 must have no exception",
        )
        observed_states = {
            state: (spec.workflow, spec.user_decision_required, spec.reason_code_required)
            for state, spec in policy.states.items()
        }
        validation.check(  # C15
            observed_states == DECISION_POLICY_STATES,
            f"{name}: decision policy state semantics drifted "
            "(workflow / user_decision_required / reason_code_required)",
        )
        validation.check(  # C16
            policy.aggregate_order == DECISION_POLICY_AGGREGATE_ORDER,
            f"{name}: decision policy aggregate order drifted",
        )
        validation.check(  # C26
            dict(policy.transitions) == DECISION_POLICY_TRANSITIONS,
            f"{name}: the transition matrix drifted -- every cell is pinned by value, "
            "not merely by closed-set membership",
        )
        for edge in sorted(DECISION_POLICY_AUTHORITY_EDGES):  # C26a
            validation.check(
                policy.transitions.get(edge) == "requires_user_decision",
                f"{name}: {edge[0]} -> {edge[1]} must require a user decision; "
                f"found {policy.transitions.get(edge)!r}",
            )
        observed_elements = {
            element: (
                spec.kind,
                tuple(spec.values),
                spec.minimum,
                tuple(spec.triggering)
                if isinstance(spec.triggering, list)
                else spec.triggering,
            )
            for element, spec in policy.boundary_elements.items()
        }
        validation.check(  # C27
            observed_elements == DECISION_POLICY_BOUNDARY_ELEMENT_SPECS,
            f"{name}: boundary element specifications drifted "
            "(kind / enum values / minimum)",
        )
        validation.check(  # C28
            policy.policy_source_roles == DECISION_POLICY_SOURCE_ROLES
            and policy.policy_source_kinds == DECISION_POLICY_SOURCE_KINDS,
            f"{name}: policy source roles or kinds drifted",
        )
        validation.check(  # C29
            policy.state_scope == DECISION_POLICY_STATE_SCOPE,
            f"{name}: decision state scope drifted",
        )
        observed_conditions = {
            state: next(
                (combinator, tuple(predicates))
                for combinator, predicates in condition.items()
            )
            for state, condition in policy.entry_conditions.items()
        }
        validation.check(  # C31
            tuple(sorted(policy.policy_source_cannot_resolve))
            == tuple(sorted(DECISION_POLICY_CANNOT_RESOLVE)),
            f"{name}: authority precedence drifted -- a policy source must not "
            "resolve reserved user authority or an explicit requirement conflict",
        )
        validation.check(  # C30
            observed_conditions == DECISION_POLICY_ENTRY_CONDITIONS,
            f"{name}: state entry conditions drifted -- permitted_states evaluates "
            "these, so a change here moves the authority boundary",
        )
        validation.check(  # C17
            dict(policy.assumption_allowed_forbidden_when)
            == DECISION_POLICY_FORBIDDEN_WHEN,
            f"{name}: INV-4 forbidden-when conditions drifted",
        )
        validation.check(  # C18
            dict(policy.assumption_allowed_requires)
            == DECISION_POLICY_ASSUMPTION_REQUIRES,
            f"{name}: INV-3 assumption requirements drifted",
        )
        validation.check(  # C19
            policy.user_decision_fields == DECISION_POLICY_USER_DECISION_FIELDS,
            f"{name}: user_decision required fields drifted",
        )
        validation.check(  # C24
            tuple(sorted(policy.user_decision_sources))
            == tuple(sorted(DECISION_POLICY_USER_DECISION_SOURCES)),
            f"{name}: the user-authority positive vocabulary drifted",
        )
        validation.check(  # C25
            not (policy.user_decision_sources & policy.forbidden_authority_sources),
            f"{name}: the user-authority vocabulary admits a forbidden source",
        )
        validation.check(  # C20
            dict(policy.citation_minimum) == DECISION_POLICY_CITATION_MINIMUM,
            f"{name}: CONFLICT citation minimum drifted",
        )
        validation.check(  # C21
            {s: tuple(f) for s, f in policy.required_evidence.items()}
            == DECISION_POLICY_REQUIRED_EVIDENCE,
            f"{name}: per-state required evidence drifted",
        )
        validation.check(  # C22
            {s: dict(c) for s, c in policy.entry_clauses.items()}
            == DECISION_POLICY_ENTRY_CLAUSES,
            f"{name}: entry clause text drifted",
        )
        validation.check(  # C32
            dict(policy.clause_predicates) == DECISION_POLICY_CLAUSE_PREDICATES,
            f"{name}: clause->predicate binding drifted -- validate_record proves a "
            "reason code's clause through these, so a change here lets a record be "
            "filed under a clause its evidence does not establish",
        )
        validation.check(  # C23
            policy.downstream_rule == DECISION_POLICY_DOWNSTREAM_RULE,
            f"{name}: downstream rule drifted",
        )
        validation.check(  # C10
            tuple(sorted(policy.forbidden_authority_sources))
            == tuple(sorted(DECISION_POLICY_REJECT_LIST)),
            f"{name}: forbidden-authority reject list drifted",
        )

        block = load_policy_contract(REPO_ROOT / name / "SKILL.md")["decision_policy"]
        validation.check(  # C11a
            set(block) == STATE_SELECTION_INPUTS | DECLARATIVE_KEYS,
            f"{name}: decision policy key "
            f"{sorted(set(block) ^ (STATE_SELECTION_INPUTS | DECLARATIVE_KEYS))} "
            "is not classified as a selection input or declarative",
        )
        hits = _axis_token_hits(block)
        validation.check(  # C11b
            not hits,
            f"{name}: decision policy references axis token at {hits[:1]}, "
            "which is a state-selection input",
        )
        closed = _closed_value_violations(block)
        validation.check(  # C11c
            not closed,
            f"{name}: decision policy value {closed[:1]} is outside its closed set",
        )
        validation.check(  # C11d
            policy.independent_axes == CANONICAL_INDEPENDENT_AXES,
            f"{name}: independent_axes must name exactly the three canonical axes",
        )

        for anchor in DECISION_POLICY_SKILL_PROSE_ANCHORS:  # C12
            validation.check(
                anchor in skill_text,
                f"{name}: missing decision policy prose anchor {anchor!r}",
            )

    left, right = (policies[d.name] for d in SKILL_DIRS)
    validation.check(  # C4
        left.reason_codes == right.reason_codes
        and left.transitions == right.transitions
        and left.raw == right.raw,
        "decision policy contracts differ between skills",
    )

    for skill_dir in SKILL_DIRS:  # C13 / C14
        for relative in (
            *(f"templates/{phase.casefold()}.md" for phase in PHASE_ROUTES),
            "reviews/common.md",
        ):
            path = skill_dir / relative
            if not path.is_file():
                continue
            text = path.read_text(encoding="utf-8")
            validation.check(
                DECISION_RECORD_TEMPLATE_ANCHOR in text,
                f"{skill_dir.name}: {relative} is missing the decision record section",
            )
            validation.check(
                DECISION_RECORD_OPTIONALITY_ANCHOR in text,
                f"{skill_dir.name}: {relative} is missing the decision record "
                "optionality sentence",
            )


def _axis_token_hits(block: dict) -> list[str]:
    """Exact-token axis references inside a STATE_SELECTION_INPUTS subtree (C11b)."""
    hits: list[str] = []

    def walk(node: object, path: str) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key in AXIS_TOKENS:
                    hits.append(f"{path}/{key} (key)")
                walk(value, f"{path}/{key}")
        elif isinstance(node, list):
            for index, value in enumerate(node):
                walk(value, f"{path}[{index}]")
        elif isinstance(node, str) and node in AXIS_TOKENS:
            hits.append(f"{path} (value)")

    for key in sorted(STATE_SELECTION_INPUTS & set(block)):
        walk(block[key], key)
    return hits


def _closed_value_violations(block: dict) -> list[str]:
    """Enumerated positions carrying a value outside their closed set (C11c)."""
    bad: list[str] = []
    for source, row in block.get("transitions", {}).items():
        if not isinstance(row, dict):
            continue
        for target, rule in row.items():
            if rule not in TRANSITION_VALUES:
                bad.append(f"transitions[{source}][{target}]={rule!r}")
    for name, spec in block.get("states", {}).items():
        if isinstance(spec, dict) and spec.get("workflow") not in WORKFLOW_VALUES:
            bad.append(f"states[{name}].workflow={spec.get('workflow')!r}")
    return bad


def validate_phase_gate_neutrality(validation: Validation) -> None:
    """Phase transitions and the Final Review trigger must be risk-neutral.

    A separate function from validate_risk_profile_contract() so a failure names the
    actual concern: a transition expressed in terms of a Reviewer that LOW does not
    create. Its load-bearing half is the NEGATIVE anchors -- a neutral phrase can sit
    happily beside a stale one in the same section, which is exactly how this
    regressed past three review rounds.
    """
    skill_path = LIFECYCLE_SKILL_DIR / "SKILL.md"
    if not skill_path.is_file():
        return
    skill_text = skill_path.read_text(encoding="utf-8")

    for stale in PHASE_GATE_STALE_PREDICATES:
        validation.check(
            stale not in skill_text,
            f"{LIFECYCLE_SKILL_DIR.name}: phase gate predicate is not risk-neutral -- "
            f"{stale!r} requires a Reviewer PASS, which LOW cannot produce",
        )

    for heading, end, stale in PHASE_GATE_STALE_SECTION_HEADINGS:
        section = extract_section(skill_text, heading, end)
        validation.check(
            stale not in [line.strip() for line in section.splitlines()],
            f"{LIFECYCLE_SKILL_DIR.name}: section 12 still uses the Reviewer-scoped "
            f"transition heading {stale!r}; the phase gate predicate is risk-neutral "
            "(LOW = the Worker result, MEDIUM/HIGH = the phase Reviewer verdict)",
        )

    for heading, end, anchor in PHASE_GATE_NEUTRAL_ANCHORS:
        section = extract_section(skill_text, heading, end)
        validation.check(
            anchor in section,
            f"{LIFECYCLE_SKILL_DIR.name}: {heading} is missing the risk-neutral "
            f"phase gate anchor {anchor!r}",
        )

    try:
        description = parse_frontmatter(skill_path).get("description", "")
    except (OSError, ValueError):
        description = ""
    validation.check(
        PHASE_GATE_FRONTMATTER_ANCHOR in description,
        f"{LIFECYCLE_SKILL_DIR.name}: the frontmatter description does not state "
        f"that the phase gate is risk-determined ({PHASE_GATE_FRONTMATTER_ANCHOR!r})",
    )


def validate_run_logging_contract(validation: Validation) -> None:
    """The dispatch_settled CLI example in the OS-17 run-logging subsection.

    Regression guard for a review finding on PR #15: the example showed `--action`
    (the Coordinator's own created/reused decision) but omitted `--reuse` (Orca's own
    reported effects[].action). Both are real ORCHESTRATOR_LOG.md columns; only prose
    told a live Coordinator which flags to pass, so a missing flag here silently drops
    a column from every real run's log with nothing to catch it.
    """
    skill_path = LIFECYCLE_SKILL_DIR / "SKILL.md"
    if not skill_path.is_file():
        return
    skill_text = skill_path.read_text(encoding="utf-8")

    section = extract_section(
        skill_text, RUN_LOGGING_SECTION_HEADING, RUN_LOGGING_SECTION_END
    )
    validation.check(
        bool(section),
        f"{LIFECYCLE_SKILL_DIR.name}: run-scoped orchestration/timing log section is "
        "missing",
    )
    for anchor in RUN_LOGGING_DISPATCH_SETTLED_ANCHORS:
        validation.check(
            anchor in section,
            f"{LIFECYCLE_SKILL_DIR.name}: dispatch_settled orchestrator-event example "
            f"is missing {anchor!r}",
        )
    for anchor in RUN_LOGGING_TIMING_RISK_ANCHORS:
        validation.check(
            anchor in section,
            f"{LIFECYCLE_SKILL_DIR.name}: a timing-event call point is missing "
            f"{anchor!r}; every TIMING_LOG.md row names the risk it was produced "
            "under, and the CLI path must write the same column the Python path does",
        )
    for anchor in RUN_LOGGING_DISPATCH_CLOCK_ANCHORS:
        validation.check(
            anchor in section,
            f"{LIFECYCLE_SKILL_DIR.name}: the run-logging section is missing "
            f"{anchor!r}; without the authoritative pre-dispatch clock (OS-19) a "
            "Coordinator reconstructs --started-at from an earlier row and "
            "TIMING_LOG.md gets negative durations again",
        )


# The full heading, including the ticket tag: sections 16 and 17 both REFERENCE
# "#### Final Review audit artifacts", and a prefix anchor would silently start
# extracting from one of those references if the real subsection were renamed.
FINAL_REVIEW_AUDIT_SECTION_HEADING = "#### Final Review audit artifacts (OS-22)"
FINAL_REVIEW_AUDIT_SECTION_END = "\n## 10."
# Each anchor is a claim the SKILL text has to make in its own words, not a
# paraphrase this validator would accept a weaker version of.
FINAL_REVIEW_AUDIT_ANCHORS = (
    "final_review_audit/",
    "FINAL_REVIEW_AUDIT_SCHEMA_VERSION = 1.0",
    "FINAL_REVIEW_REDACTION_POLICY_VERSION = redaction/1.1",
    "FINAL_REVIEW_EVIDENCE_BUNDLE.json",
    "final-review-audit-write",
    "final-review-audit-provenance",
    "final-review-audit-export",
    "final_review_audit_incomplete_publication",
    "dispatch_input_rejected",
    "superseded_by_retry",
)
# The .staging/-is-never-a-record reader rule, the three authorities, and the
# run_end-is-not-terminal rule, each anchored on the sentence that carries it.
FINAL_REVIEW_AUDIT_STAGING_ANCHORS = (".staging/", "record가 아니다")
FINAL_REVIEW_AUDIT_AUTHORITY_ANCHORS = (
    "ORCHESTRATOR_LOG.md",
    "per-dispatch audit records",
    "FINAL_RESULT.md",
)
FINAL_REVIEW_AUDIT_RUN_END_ANCHOR = "run_end는 terminal이 아니다"
FINAL_REVIEW_STEP8_ARTIFACT_ROOT_ANCHOR = "`<ARTIFACT_ROOT>FINAL_REVIEW*`"
FINAL_REVIEW_STEP8_STALE_PATH = "artifacts/FINAL_REVIEW_"


def validate_final_review_audit_contract(validation: Validation) -> None:
    """OS-22 section 9's audit-artifact subsection, and section 16's path fix.

    The schema version is stated in two places by necessity -- the constant in
    run_logging.py and the prose a live Coordinator reads -- so this validator is
    what keeps them one value instead of two that drift.
    """
    skill_path = LIFECYCLE_SKILL_DIR / "SKILL.md"
    if not skill_path.is_file():
        return
    skill_text = skill_path.read_text(encoding="utf-8")

    start = skill_text.find(FINAL_REVIEW_AUDIT_SECTION_HEADING)
    end = skill_text.find(FINAL_REVIEW_AUDIT_SECTION_END, start) if start != -1 else -1
    section = skill_text[start:end] if start != -1 and end != -1 else ""
    validation.check(
        bool(section),
        f"{LIFECYCLE_SKILL_DIR.name}: section 9's Final Review audit artifact "
        "subsection is missing, renamed, or has escaped section 9",
    )
    for anchor in FINAL_REVIEW_AUDIT_ANCHORS:
        validation.check(
            anchor in section,
            f"{LIFECYCLE_SKILL_DIR.name}: the Final Review audit subsection is "
            f"missing {anchor!r}",
        )
    for anchor in FINAL_REVIEW_AUDIT_STAGING_ANCHORS:
        validation.check(
            anchor in section,
            f"{LIFECYCLE_SKILL_DIR.name}: the Final Review audit subsection does not "
            f"state the .staging/-is-never-a-record reader rule ({anchor!r} absent); "
            "a reader that parses a half-written staging directory would read an "
            "incomplete record as a record",
        )
    for anchor in FINAL_REVIEW_AUDIT_AUTHORITY_ANCHORS:
        validation.check(
            anchor in section,
            f"{LIFECYCLE_SKILL_DIR.name}: the three-authority statement is missing "
            f"{anchor!r}",
        )
    validation.check(
        FINAL_REVIEW_AUDIT_RUN_END_ANCHOR in section,
        f"{LIFECYCLE_SKILL_DIR.name}: the audit subsection does not state that "
        "run_end is not terminal; a reader that stops at the first run_end reports a "
        "run that continued as finished",
    )
    validation.check(
        f"FINAL_REVIEW_AUDIT_SCHEMA_VERSION = {run_logging.FINAL_REVIEW_AUDIT_SCHEMA_VERSION}"
        in section,
        f"{LIFECYCLE_SKILL_DIR.name}: the audit subsection's schema version differs "
        f"from run_logging.FINAL_REVIEW_AUDIT_SCHEMA_VERSION "
        f"({run_logging.FINAL_REVIEW_AUDIT_SCHEMA_VERSION!r})",
    )
    validation.check(
        f"FINAL_REVIEW_REDACTION_POLICY_VERSION = {run_logging.FINAL_REVIEW_REDACTION_POLICY_VERSION}"
        in section,
        f"{LIFECYCLE_SKILL_DIR.name}: the audit subsection's redaction policy version "
        f"differs from run_logging.FINAL_REVIEW_REDACTION_POLICY_VERSION",
    )

    final_verification = extract_section(skill_text, "## 16. Final Verification", "\n## 17.")
    validation.check(
        FINAL_REVIEW_STEP8_ARTIFACT_ROOT_ANCHOR in final_verification,
        f"{LIFECYCLE_SKILL_DIR.name}: section 16 step 8 does not name "
        f"{FINAL_REVIEW_STEP8_ARTIFACT_ROOT_ANCHOR}; the run-scoped path is section "
        "9's contract and a run-external path contradicts it",
    )
    validation.check(
        FINAL_REVIEW_STEP8_STALE_PATH not in final_verification,
        f"{LIFECYCLE_SKILL_DIR.name}: section 16 still names the stale "
        f"{FINAL_REVIEW_STEP8_STALE_PATH!r} path",
    )
    validation.check(
        "FINAL_REVIEW_AUDIT:" in final_verification,
        f"{LIFECYCLE_SKILL_DIR.name}: section 16's Final Adversarial Review block "
        "does not cite the per-dispatch audit record",
    )
    validation.check(
        # The Final Result template's own line, not the prose that references it:
        # trimming the serialization while leaving the prose behind is exactly the
        # shape DEC-5 refuses.
        "## Orca Orchestration State\n## Final Adversarial Review" in final_verification,
        f"{LIFECYCLE_SKILL_DIR.name}: the Final Result template lost its four-axis "
        "## Orca Orchestration State ledger; OS-22 adds an authority, it does not "
        "trim the existing one",
    )


def validate_run_logging_tool_parity(validation: Validation) -> None:
    """scripts/run_logging.py and the copy installed inside the Skill must match.

    OS-17 review round 3 MAJOR-1: INSTALL.md's documented global install
    (`cp -R orca-worker-reviewer-orchestration ~/.claude/skills/`) never copies
    scripts/, so a live Coordinator's logging commands only work if the Skill
    directory ships its own copy of run_logging.py (orca-worker-reviewer-
    orchestration/tools/run_logging.py). Two copies of the same file is a drift
    risk with no compiler to catch it, so this validator is the compiler: same
    exact-byte-equality pattern validate_shared_directories() above already uses
    for templates/ and reviews/, applied to this one file pair instead of two
    directories.
    """
    canonical_path = REPO_ROOT / "scripts" / "run_logging.py"
    installed_path = LIFECYCLE_SKILL_DIR / "tools" / "run_logging.py"
    validation.check(
        installed_path.is_file(),
        f"{LIFECYCLE_SKILL_DIR.name}: tools/run_logging.py is missing -- the "
        "installed Skill would have no working logging CLI",
    )
    if not (canonical_path.is_file() and installed_path.is_file()):
        return
    validation.check(
        canonical_path.read_bytes() == installed_path.read_bytes(),
        f"{LIFECYCLE_SKILL_DIR.name}: tools/run_logging.py differs from "
        "scripts/run_logging.py",
    )


def validate_workflow_output_contracts(validation: Validation) -> None:
    contracts = []
    for skill_dir in SKILL_DIRS:
        skill_path = skill_dir / "SKILL.md"
        try:
            contract = load_workflow_output_contract(skill_path)
        except (OSError, WorkflowContractError) as exc:
            validation.check(False, str(exc))
            continue
        contracts.append(contract)
        validation.check(
            contract.finding_resolution_values
            == ("RESOLVED", "DISPUTED", "BLOCKED"),
            f"{skill_dir.name}: invalid finding resolution contract",
        )

    if len(contracts) == len(SKILL_DIRS):
        validation.check(
            contracts[0] == contracts[1],
            "Worker/Reviewer output contracts differ between skills",
        )


def main() -> int:
    validation = Validation()

    discovered_skill_dirs = tuple(
        sorted(path.parent for path in REPO_ROOT.glob("*/SKILL.md"))
    )
    validation.check(bool(discovered_skill_dirs), "no SKILL.md files found")
    for skill_dir in discovered_skill_dirs:
        validate_frontmatter(validation, skill_dir)

    for skill_dir in SKILL_DIRS:
        validation.check(skill_dir.is_dir(), f"missing skill directory: {skill_dir}")
        validation.check(
            (skill_dir / "SKILL.md").is_file(),
            f"{skill_dir.name}: missing SKILL.md",
        )
        if not (skill_dir / "SKILL.md").is_file():
            continue
        validate_routes_and_files(validation, skill_dir)
        validate_policy_contracts(validation, skill_dir)

    validate_shared_directories(validation)
    validate_machine_readable_contracts(validation)
    validate_workflow_output_contracts(validation)
    validate_lifecycle_accounting_contract(validation)
    validate_final_review_contract(validation)
    validate_final_review_audit_contract(validation)
    validate_reuse_contract(validation)
    validate_task_boundary_contract(validation)
    validate_reviewer_context_contract(validation)
    validate_quality_profile_contract(validation)
    validate_risk_profile_contract(validation)
    validate_agent_profile_contract(validation)
    validate_decision_policy_contract(validation)
    validate_decision_gate_contract(validation)
    validate_phase_gate_neutrality(validation)
    validate_run_logging_contract(validation)
    validate_run_logging_tool_parity(validation)
    validate_version(validation)
    validate_repository_links(validation)
    validate_no_user_absolute_paths(validation)

    if validation.errors:
        print(f"Skill validation FAILED ({len(validation.errors)} errors, {validation.checks} checks)")
        for error in validation.errors:
            print(f"- {error}")
        return 1

    print(f"Skill validation PASSED ({validation.checks} checks)")
    print("Validated both skills, shared templates/reviews, routing, and policy gates.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
