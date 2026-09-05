#!/usr/bin/env python3
"""Opt-in integration tests against the installed Orca runtime."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
import unittest
from pathlib import Path

try:
    from scripts.orca_runtime_harness import (
        CLEANUP_AUTHORITY_STATES,
        CLOSE_ELIGIBLE_ROLES,
        HISTORICAL_ORCA_APP_VERSION_OBSERVATIONS,
        LATE_DEPENDENT_STATUSES,
        NEVER_CLOSE_ROLES,
        SUPPORTED_ORCA_APP_VERSIONS,
        TERMINAL_ROLE_CLASSES,
        UNSETTLED_WORKER_STATES,
        WORKER_RESOURCE_OUTCOMES,
        UnsupportedOrcaContract,
        run_final_review_runtime_scenario,
        run_quality_profile_runtime_scenario,
        run_risk_runtime_scenario,
        run_runtime_scenarios,
        run_session_reuse_runtime_scenario,
    )
except ModuleNotFoundError:
    from orca_runtime_harness import (
        CLEANUP_AUTHORITY_STATES,
        CLOSE_ELIGIBLE_ROLES,
        HISTORICAL_ORCA_APP_VERSION_OBSERVATIONS,
        LATE_DEPENDENT_STATUSES,
        NEVER_CLOSE_ROLES,
        SUPPORTED_ORCA_APP_VERSIONS,
        TERMINAL_ROLE_CLASSES,
        UNSETTLED_WORKER_STATES,
        WORKER_RESOURCE_OUTCOMES,
        UnsupportedOrcaContract,
        run_final_review_runtime_scenario,
        run_quality_profile_runtime_scenario,
        run_risk_runtime_scenario,
        run_runtime_scenarios,
        run_session_reuse_runtime_scenario,
    )

try:
    from scripts.task_context import (
        AGENT_MODES,
        BOUNDARY_RECEIPT_PREFIX,
        CANONICAL_PHASES,
        TASK_BOUNDARY_KEYS,
        parse_reviewer_context,
        parse_task_boundary,
        phase_artifact_contract,
    )
except ModuleNotFoundError:
    from task_context import (
        AGENT_MODES,
        BOUNDARY_RECEIPT_PREFIX,
        CANONICAL_PHASES,
        TASK_BOUNDARY_KEYS,
        parse_reviewer_context,
        parse_task_boundary,
        phase_artifact_contract,
    )


RUN_ORCA = os.environ.get("ORCA_RUNTIME_TEST") == "1"
ARTIFACT_DIR = os.environ.get("ORCA_RUNTIME_ARTIFACT_DIR")


class OrcaRuntimeIntegrationTests(unittest.TestCase):
    def test_runtime_scenarios(self) -> None:
        if not RUN_ORCA:
            self.skipTest("requires --orca-runtime and a ready Orca runtime")
        if ARTIFACT_DIR:
            artifact_dir = Path(ARTIFACT_DIR)
            try:
                results = run_runtime_scenarios(artifact_dir)
            except UnsupportedOrcaContract as exc:
                self.skipTest(str(exc))
            self.assert_run_scoped_logs_exist(artifact_dir, results)
        else:
            with tempfile.TemporaryDirectory() as directory:
                artifact_dir = Path(directory)
                try:
                    results = run_runtime_scenarios(artifact_dir)
                except UnsupportedOrcaContract as exc:
                    self.skipTest(str(exc))
                # OS-17: inside the `with` block on purpose -- the temp directory
                # this scenario run actually wrote ORCHESTRATOR_LOG.md/TIMING_LOG.md
                # under is gone the moment this block exits.
                self.assert_run_scoped_logs_exist(artifact_dir, results)

        by_name = {result.scenario: result for result in results}
        self.assertEqual(set(by_name), set("ABCDEFGHI"))

        scenario_a = by_name["A"]
        self.assertEqual(scenario_a.status, "COMPLETED")
        self.assertEqual(len(scenario_a.attempts), 2)
        self.assertIn("question", scenario_a.signals)
        self.assertEqual(scenario_a.signals.count("worker_done"), 2)

        scenario_b = by_name["B"]
        self.assertEqual(scenario_b.status, "COMPLETED")
        self.assertEqual(scenario_b.iteration, 2)
        self.assertEqual(len(scenario_b.attempts), 4)
        self.assertIn("R1", scenario_b.attempts[1].body)
        self.assertIn("R1", scenario_b.attempts[2].body)
        self.assertTrue(
            scenario_b.attempts[1].lifecycle_action.startswith(("retain:", "reuse:"))
        )

        scenario_c = by_name["C"]
        self.assertEqual(scenario_c.status, "ESCALATED")
        self.assertEqual(scenario_c.iteration, 3)
        self.assertEqual(len(scenario_c.attempts), 6)

        scenario_d = by_name["D"]
        self.assertEqual(scenario_d.status, "BLOCKED")
        self.assertEqual(len(scenario_d.attempts), 1)
        self.assertEqual(scenario_d.attempts[0].outcome, "failed")
        self.assertEqual(scenario_d.attempts[0].task_status, "failed")
        self.assertIn("escalation", scenario_d.signals)

        scenario_e = by_name["E"]
        self.assertEqual(len(scenario_e.attempts), 1)
        self.assertEqual(scenario_e.attempts[0].worker_done_count, 0)

        scenario_f = by_name["F"]
        self.assertEqual(len(scenario_f.attempts), 2)
        self.assertEqual(scenario_f.attempts[1].worker_done_count, 0)

        self.assert_scenario_g(by_name["G"])
        self.assert_scenario_h(by_name["H"])
        self.assert_scenario_i(by_name["I"])
        self.assert_placement_ladder(results)

        for result in results:
            self.assertTrue(result.run_id.startswith("run_"))
            for attempt in result.attempts:
                self.assertTrue(attempt.task_id.startswith("task_"))
                self.assertTrue(attempt.dispatch_id.startswith("ctx_"))
                if attempt.worker_done_count == 0:
                    self.assertIn(attempt.dispatch_status, {"dispatched", "failed"})
                    # "ready" is the adopted-then-already-exited worker; the
                    # "_external" suffix is the unsupervised branch's own label.
                    self.assertTrue(
                        attempt.worker_state in UNSETTLED_WORKER_STATES
                        or attempt.worker_state.startswith("outcome_unknown"),
                        f"unsettled dispatch reported {attempt.worker_state}",
                    )
                else:
                    self.assertIn(attempt.dispatch_status, {"completed", "failed"})
                self.assertIn(attempt.task_status, {"completed", "failed", "blocked"})

                self.assertEqual(attempt.finalizations, 1)
                self.assertIn(attempt.worker_resource, set(WORKER_RESOURCE_OUTCOMES))
                self.assertIn(attempt.cleanup_authority, CLEANUP_AUTHORITY_STATES)
                self.assertIn(attempt.terminal_role, TERMINAL_ROLE_CLASSES)

                # Domain assertions alone would accept a wrong-but-valid "authorized".
                # Pin the actual relation between role and authority instead.
                if attempt.terminal_role in NEVER_CLOSE_ROLES:
                    self.assertNotEqual(attempt.cleanup_authority, "authorized")
                if attempt.cleanup_authority == "authorized":
                    self.assertIn(attempt.terminal_role, CLOSE_ELIGIBLE_ROLES)

    def assert_placement_ladder(self, results) -> None:
        """Rung 3's middle step really ran against the installed runtime.

        The offline tests pin the order (create -> tui-idle -> worker-start); this is
        the evidence that the real `terminal wait --for tui-idle` command was accepted
        by Orca rather than only being asserted against a stub.
        """
        observed = set()
        for result in results:
            worker_rows = [
                row
                for row in result.ledger
                if row["role"] != "run_owner_fixture" and row["created_by"]
            ]
            self.assertTrue(worker_rows, f"scenario {result.scenario} placed no worker")
            for row in worker_rows:
                self.assertIn(row["tui_idle"], {"idle", "timeout", "unobserved"})
                observed.add(row["tui_idle"])
            self.assertIn("terminal wait", result.commands_used)
        # a run in which the wait was never even attempted would leave only the
        # never-observed default behind
        self.assertNotEqual(observed, {"unobserved"})

    def assert_scenario_g(self, scenario_g) -> None:
        """Graph-first promotion used the pre-created Reviewer Task, with no override."""
        self.assertEqual(scenario_g.status, "COMPLETED")
        self.assertEqual(len(scenario_g.attempts), 2)

        # (1) the pre-created Reviewer Task became ready without a manual override
        self.assertTrue(scenario_g.reviewer_task_id.startswith("task_"))
        self.assertEqual(scenario_g.reviewer_task_status, "ready")

        # (2) the very Task that was promoted is the one that was dispatched
        reviewer_attempt = scenario_g.attempts[1]
        self.assertEqual(reviewer_attempt.role, "reviewer")
        self.assertEqual(reviewer_attempt.task_id, scenario_g.reviewer_task_id)
        self.assertEqual(reviewer_attempt.task_status, "completed")
        self.assertEqual(reviewer_attempt.outcome, "succeeded")
        self.assertEqual(reviewer_attempt.finalizations, 1)
        self.assertNotEqual(
            reviewer_attempt.dispatch_id, scenario_g.attempts[0].dispatch_id
        )

        # (3) no force-ready command ran anywhere in Run G (real command log)
        self.assertNotIn("orchestration task-update", scenario_g.commands_used)
        self.assertNotIn("task-update:ready", scenario_g.recovery)

    def assert_run_scoped_logs_exist(self, artifact_dir: Path, results) -> None:
        """OS-17: every scenario run left ORCHESTRATOR_LOG.md/TIMING_LOG.md behind
        under its own run directory, including a non-COMPLETED one (D=BLOCKED).
        """
        for scenario_name in ("A", "D"):
            result = next(item for item in results if item.scenario == scenario_name)
            run_root = artifact_dir / "artifacts" / "runs" / result.run_id
            orchestrator_log = run_root / "ORCHESTRATOR_LOG.md"
            timing_log = run_root / "TIMING_LOG.md"
            self.assertTrue(
                orchestrator_log.is_file(),
                f"scenario {scenario_name}: {orchestrator_log} was not created",
            )
            self.assertTrue(
                timing_log.is_file(),
                f"scenario {scenario_name}: {timing_log} was not created",
            )
            log_text = orchestrator_log.read_text(encoding="utf-8")
            self.assertIn("run_start", log_text)
            self.assertIn(result.status, log_text)

        # OS-17 review round 4 MAJOR: phase/iteration boundaries must come from
        # the real OrcaRuntimeHarness workflow (run_runtime_scenarios() calling
        # run_attempt()/finish(), exactly as every scenario does), not from a
        # test calling log_phase_start()/log_iteration_start() itself. Scenario
        # B (Worker -> Reviewer FAIL -> correction -> Reviewer PASS, two
        # iterations of the same phase) is the real run that actually crosses a
        # phase and two iteration boundaries, so its own TIMING_LOG.md is the
        # proof this ran against real Orca, not a mock.
        scenario_b = next(item for item in results if item.scenario == "B")
        timing_text = (
            artifact_dir / "artifacts" / "runs" / scenario_b.run_id / "TIMING_LOG.md"
        ).read_text(encoding="utf-8")
        for event in ("phase_start", "phase_end", "iteration_start", "iteration_end"):
            self.assertIn(
                f"| {event} |",
                timing_text,
                f"scenario B: {event} boundary row missing from a real run",
            )
        self.assertEqual(timing_text.count("| iteration_start |"), 2)
        self.assertEqual(timing_text.count("| iteration_end |"), 2)

    def assert_scenario_h(self, scenario_h) -> None:
        """A dependent created after settlement is never dispatched.

        OS-41: the STATUS such a Task reports at creation is runtime-defined and
        differs between the two point verifications -- 1.4.184 leaves it `pending`,
        1.4.196 evaluates the already-satisfied dependency and reports `ready`. It is
        pinned to an allowlist so an unrecognized third value still fails closed,
        while the invariant the scenario actually exists for -- the late dependent is
        never dispatched -- is asserted on its own below rather than being smuggled
        in through one runtime's spelling of "not dispatched yet".
        """
        self.assertIn(scenario_h.late_dependent_status, LATE_DEPENDENT_STATUSES)
        # The harness records one RuntimeAttempt per dispatch it made, so exactly one
        # attempt IS "the late dependent was never dispatched".
        self.assertEqual(len(scenario_h.attempts), 1)
        self.assertEqual(scenario_h.attempts[0].task_status, "completed")

    def assert_scenario_i(self, scenario_i) -> None:
        """Self-created is not the same as closable."""
        rows = {row["handle"]: row for row in scenario_i.ledger}

        # I-1: the run-owner fixture is self-created BUT still not authorized
        owner = rows[scenario_i.run_owner_handle]
        self.assertEqual(owner["role"], "run_owner_fixture")
        self.assertEqual(owner["origin"], "self_created")
        self.assertEqual(owner["cleanup_authority"], "not_authorized")
        self.assertEqual(owner["action"], "retained")
        self.assertNotIn("close", owner["policy_commands"])
        # The ledger snapshot above is taken before teardown, so on its own it cannot
        # say whether the teardown path closed this handle. The receipt can: it lists
        # the lifecycle commands seen for the handle immediately BEFORE the close.
        self.assertEqual(
            scenario_i.fixture_teardown["policyCommandsBeforeTeardown"], []
        )
        self.assertEqual(scenario_i.fixture_teardown["close"], "issued")

        # I-2: a simulated coordinator_session row is never authorized either
        simulated = rows["term_simulated"]
        self.assertEqual(simulated["role"], "coordinator_session")
        self.assertEqual(simulated["cleanup_authority"], "not_authorized")
        self.assertEqual(simulated["action"], "retained")

        # I-3: the self-handle guard refuses rather than closes
        self.assertIn(
            scenario_i.fixture_teardown["selfHandleProbe"], {"refused", "unset"}
        )
        self.assertEqual(scenario_i.fixture_teardown["role"], "run_owner_fixture")
        self.assertIn(
            scenario_i.fixture_teardown["selfHandleGuard"], {"passed", "unset"}
        )


class QualityProfileRuntimeIntegrationTests(unittest.TestCase):
    """Scenario L against a real Orca runtime: phase filtering, one resolution.

    Opt-in like every other class in this file (ORCA_RUNTIME_TEST=1). The scenario
    BODY is not left to this gate for its correctness -- QualityProfileScenarioTests
    in scripts/test_orca_runtime_contract.py drives the same function offline with a
    stubbed process boundary and runs in the default suite. What this class adds is
    the one thing the offline driver cannot check: that a REAL Orca dispatch carries
    the quality-gate block through to the agent unaltered.
    """

    def run_scenario(self):
        if not RUN_ORCA:
            self.skipTest("requires --orca-runtime and a ready Orca runtime")
        if ARTIFACT_DIR:
            try:
                return run_quality_profile_runtime_scenario(Path(ARTIFACT_DIR))
            except UnsupportedOrcaContract as exc:
                self.skipTest(str(exc))
        with tempfile.TemporaryDirectory() as directory:
            try:
                return run_quality_profile_runtime_scenario(Path(directory))
            except UnsupportedOrcaContract as exc:
                self.skipTest(str(exc))

    def test_quality_profile_phase_filtering(self) -> None:
        result = self.run_scenario()

        self.assertEqual(result.scenario, "L")
        self.assertEqual(result.status, "COMPLETED")
        self.assertEqual(result.quality_profile_status, "loaded")
        applicable = result.quality_profile_attributes
        self.assertEqual(
            set(applicable),
            {
                "design:worker",
                "design:reviewer",
                "implementation:worker",
                "implementation:reviewer",
                "final_review:reviewer",
            },
        )
        self.assertIn("DESIGN-001", applicable["design:worker"])
        self.assertNotIn("DOMAIN-001", applicable["design:worker"])
        self.assertIn("DOMAIN-001", applicable["implementation:worker"])
        self.assertNotIn("DESIGN-001", applicable["implementation:worker"])
        self.assertEqual(applicable["design:worker"], applicable["design:reviewer"])
        self.assertEqual(
            applicable["implementation:worker"], applicable["implementation:reviewer"]
        )
        for identifier in ("DESIGN-001", "DOMAIN-001", "TEAM-001"):
            self.assertIn(identifier, applicable["final_review:reviewer"])

    def test_every_dispatch_of_the_run_carries_the_quality_gate(self) -> None:
        """The receipt half: each agent echoed the gate keys back out of its preamble."""
        result = self.run_scenario()

        for attempt in result.attempts:
            with self.subTest(role=attempt.role, iteration=attempt.iteration):
                self.assertTrue(attempt.quality_gate)
                gate = dict(attempt.quality_gate)
                self.assertEqual(gate["profile_status"], "loaded")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--orca-runtime", action="store_true")
    parser.add_argument("--artifact-dir")
    args, unittest_args = parser.parse_known_args()
    if args.orca_runtime:
        os.environ["ORCA_RUNTIME_TEST"] = "1"
        global RUN_ORCA
        RUN_ORCA = True
    if args.artifact_dir:
        os.environ["ORCA_RUNTIME_ARTIFACT_DIR"] = args.artifact_dir
        global ARTIFACT_DIR
        ARTIFACT_DIR = args.artifact_dir
    unittest.main(argv=[__file__, *unittest_args])


class RiskRuntimeIntegrationTests(unittest.TestCase):
    """Opt-in scenario R: the OS-3 section 6 risk-conditional Task graph, real runtime.

    Skipped unless ORCA_RUNTIME_TEST=1, exactly like the other opt-in classes.
    run_risk_runtime_scenario() shipped with no caller at all -- not registered here
    the way the final-review, quality-profile and session-reuse scenarios are -- so
    it could never run even on a matching runtime. This class is that registration;
    the offline RiskGraphContractTests in test_orca_runtime_contract.py remains the
    primary proof surface.
    """

    def test_risk_conditional_phase_graph(self) -> None:
        if not RUN_ORCA:
            self.skipTest("requires --orca-runtime and a ready Orca runtime")
        if ARTIFACT_DIR:
            artifact_dir = Path(ARTIFACT_DIR)
            try:
                results = run_risk_runtime_scenario(artifact_dir)
            except UnsupportedOrcaContract as exc:
                self.skipTest(str(exc))
            self.assert_scenario_r(results)
        else:
            with tempfile.TemporaryDirectory() as directory:
                artifact_dir = Path(directory)
                try:
                    results = run_risk_runtime_scenario(artifact_dir)
                except UnsupportedOrcaContract as exc:
                    self.skipTest(str(exc))
                self.assert_scenario_r(results)

    def assert_scenario_r(self, results) -> None:
        """LOW leaves no phase Reviewer task in the run; MEDIUM creates one and it is
        promoted by the Worker's completion."""
        by_risk = {result.risk: result for result in results}
        self.assertEqual(set(by_risk), {"low", "medium"})

        low = by_risk["low"]
        self.assertEqual(low.risk_source, "explicit")
        self.assertEqual(low.phase_reviewer_task_ids, [])
        self.assertEqual(low.reviewer_gates_skipped, ["implementation"])
        self.assertEqual(low.reviewer_task_status, "")

        medium = by_risk["medium"]
        self.assertEqual(len(medium.phase_reviewer_task_ids), 1)
        self.assertEqual(medium.reviewer_gates_skipped, [])
        # Promoted by dependency completion, never by a manual readiness override.
        self.assertEqual(medium.reviewer_task_status, "ready")


class FinalReviewRuntimeIntegrationTests(unittest.TestCase):
    """Opt-in scenario J: Final Adversarial Review terminal freshness, real runtime.

    Skipped unless ORCA_RUNTIME_TEST=1, exactly like OrcaRuntimeIntegrationTests.
    Scenario J is deliberately outside run_runtime_scenarios(), whose A-I result set
    is pinned by an exact-set assertion above.
    """

    FINAL_REVIEW_WORKER_RESOURCE_OUTCOMES = frozenset(
        {"retain", "release", "unsupervised"}
    )

    def test_final_review_terminal_freshness(self) -> None:
        if not RUN_ORCA:
            self.skipTest("requires --orca-runtime and a ready Orca runtime")
        if ARTIFACT_DIR:
            artifact_dir = Path(ARTIFACT_DIR)
            try:
                result = run_final_review_runtime_scenario(artifact_dir)
            except UnsupportedOrcaContract as exc:
                self.skipTest(str(exc))
            self.assert_scenario_j(result, artifact_dir)
        else:
            with tempfile.TemporaryDirectory() as directory:
                artifact_dir = Path(directory)
                try:
                    result = run_final_review_runtime_scenario(artifact_dir)
                except UnsupportedOrcaContract as exc:
                    self.skipTest(str(exc))
                self.assert_scenario_j(result, artifact_dir)

    def assert_scenario_j(self, result, artifact_dir: Path) -> None:
        # J-1: the scenario ran to completion
        self.assertEqual(result.scenario, "J")
        self.assertEqual(result.status, "COMPLETED")

        # J-2: one brand-new terminal per Final Review attempt
        self.assertEqual(len(result.final_review_terminals), 2)
        self.assertNotEqual(
            result.final_review_terminals[0], result.final_review_terminals[1]
        )

        # J-3: and none of them is a phase Reviewer's terminal
        self.assertTrue(
            set(result.final_review_terminals).isdisjoint(
                result.phase_reviewer_terminals
            )
        )

        # J-4/J-5: axis (b) is never reuse, and each Dispatch finalizes exactly once
        final_review_attempts = [result.attempts[2], result.attempts[4]]
        for attempt in final_review_attempts:
            with self.subTest(dispatch=attempt.dispatch_id):
                self.assertIn(
                    attempt.worker_resource,
                    self.FINAL_REVIEW_WORKER_RESOURCE_OUTCOMES,
                )
                self.assertNotEqual(attempt.worker_resource, "reuse")
                self.assertIn(attempt.worker_resource, set(WORKER_RESOURCE_OUTCOMES))
                self.assertEqual(attempt.finalizations, 1)
                self.assertIn(attempt.terminal_role, TERMINAL_ROLE_CLASSES)

        # J-6: the receipt on disk carries the same two distinct handles
        snapshot_path = artifact_dir / "scenario-j.json"
        self.assertTrue(snapshot_path.is_file(), snapshot_path)
        snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
        recorded = snapshot["result"]["final_review_terminals"]
        self.assertEqual(recorded, result.final_review_terminals)
        self.assertEqual(len(set(recorded)), 2)


# ---- OS-41 / PR #29 review MAJOR-1: scenario K is bound to a RUNTIME POINT ------
#
# Scenario K's expectation is not "whatever answer the gate happened to give"; it is
# what the gate answers ON A NAMED RUNTIME. An assertion that accepts either answer
# cannot fail when a regression flips it, which is exactly the defect this block
# exists to remove. The two runtime points are kept apart below, and only one of them
# is reachable from the current executable support set.

# The runtime point this head is actually verified on. Compared against the identity
# the harness recorded on the far side of validate_orca_contract()
# (RuntimeScenarioResult.orca_app_version) -- never inferred from the observed result.
SCENARIO_K_VERIFIED_RUNTIME = "1.4.196"

# What the gate ANSWERS there: all eight same-role transitions REFUSED, each naming
# exactly these four conditions. The set was read out of the recorded run
# (artifacts/orca-runtime/os41-final/scenario-k.json -- all eight `reuse_decisions`
# entries carry `eligible: false` and exactly this reason set) and cross-checked
# against the condition names reuse_eligible() can append. It is bound as an exact set
# of NAMES: "reasons is non-empty" would still pass if the gate started refusing for a
# different condition, and refusing for a different condition is a different runtime
# answer, not the one this PR claims to have verified.
SCENARIO_K_1_4_196_REFUSAL_REASONS = frozenset(
    {
        "ownership_not_transferable",
        "release_state_missing",
        "terminal_effect_unrecorded",
        "worker_state_not_reusable",
    }
)
# Five phases x two roles = ten dispatches; every refusal returns None, so every
# attempt opens a FRESH session. Ten terminals for ten dispatches, not two.
SCENARIO_K_1_4_196_TERMINAL_CREATIONS = 10

# The SEPARATE, HISTORICAL supervised-path expectation. Orca 1.4.184 adopted the
# deterministic fake agent as a SUPERVISED worker, so the gate had the evidence its
# four conditions above ask for and GRANTED reuse: two terminals, five dispatches
# each, every non-final attempt recording `reuse:ownership-transfer-pending`.
#
# That observation was made against the PRE-OS-41 harness revision and is preserved as
# a historical record (docs/validation/historical/, docs/COMPATIBILITY.md,
# HISTORICAL_ORCA_APP_VERSION_OBSERVATIONS). It is NOT an expectation of this head:
# after PR #29 review MAJOR-2, 1.4.184 is not in the current executable support set,
# so validate_orca_contract() refuses that runtime before scenario K can ever start
# and this expectation is UNREACHABLE FROM A LIVE RUN. It is therefore recorded here
# in a clearly-labelled, non-executing form rather than as a live assertion branch
# that would silently never run, and
# test_the_supervised_reuse_expectation_is_historical_not_current() below asserts that
# unreachability instead of leaving it as a comment. Re-listing 1.4.184 as supported
# requires running THIS head against it; the numbers below are then what to assert.
SCENARIO_K_HISTORICAL_SUPERVISED_RUNTIME = "1.4.184"
SCENARIO_K_HISTORICAL_SUPERVISED_EXPECTATION = {
    "reuse_decisions_granted": 8,
    "reuse_decisions_refused": 0,
    "terminal_creations": 2,
    "distinct_terminals": 2,
    "reuse_lifecycle_action": "reuse:ownership-transfer-pending",
}


class SessionReuseRuntimeIntegrationTests(unittest.TestCase):
    """Opt-in scenario K: what the production reuse gate ANSWERS, against the real
    runtime (E-3).

    Skipped unless ORCA_RUNTIME_TEST=1, exactly like the two integration classes
    above. Scenario K is deliberately outside run_runtime_scenarios(), whose A-I
    result set is pinned by an exact-set assertion; scenario J set that precedent.

    OS-41 -- WHAT THIS COVERS, AND WHAT IT DELIBERATELY NO LONGER COVERS.

    This scenario was written against Orca 1.4.184, where the deterministic fake agent
    was adopted as a SUPERVISED worker and the reuse gate therefore had the evidence it
    requires (`worker.state`, `terminalResource.releaseState`/`ownershipState`). It
    asserted the granted path: two terminals, five dispatches each, every non-final
    attempt recording `reuse:ownership-transfer-pending`.

    On Orca 1.4.196 that supervised adoption is unavailable to any deterministic fake:
    `worker-start` there runs a `dispatch_input` acknowledgement stage that only a real
    recognized agent session completes. Every fake-agent dispatch is therefore TRACKED,
    a tracked dispatch reports `worker.state: "unsupervised"` with no `terminalResource`
    at all, and the gate refuses -- correctly, and by its own documented fail-closed
    design, which forbids widening an allowlist to a value that does not prove what the
    condition asks.

    So on 1.4.196 this scenario verifies **that reuse is correctly REFUSED on the
    tracked path**, with the gate's own named refusal reasons recorded, and that the
    refusal really takes effect (a fresh session per phase). It does NOT verify that
    supervised session reuse works. That claim belongs to the HISTORICAL Orca 1.4.184
    point observation of an older harness revision and to the offline contract suite,
    and `docs/COMPATIBILITY.md` records the boundary.

    PR #29 review MAJOR-1 -- WHY THE ASSERTIONS ARE NO LONGER ANSWER-AGNOSTIC.

    These assertions used to be written to hold on EITHER answer: they partitioned the
    eight decisions into granted and refused and then required only that the terminal
    accounting follow whichever answer came back. That is not a verification of the
    result this PR claims. A regression that incorrectly GRANTED one or all eight
    reuse decisions would have kept the accounting internally consistent and the test
    would still have passed, and the four documented refusal reasons were never
    required at all -- only that the reason list was non-empty.

    So the assertions are now BOUND TO A RUNTIME POINT, identified by
    `result.orca_app_version` (recorded by the harness only after
    validate_orca_contract() accepted the runtime) rather than inferred from the
    outcome observed. On SCENARIO_K_VERIFIED_RUNTIME the run MUST produce eight
    refusals, zero grants, the exact four-name refusal reason set, and ten distinct
    terminals; any grant is a failure. The 1.4.184 supervised expectation is kept
    SEPARATE, above, in a non-executing historical form, because after review MAJOR-2
    that runtime is no longer in the executable support set and a live branch for it
    would silently never run.

    The four fields aggregated below are the ONLY first-order evidence D-4 allows for
    the efficiency numbers: the ledger's own `action` label is an accounting of a
    receipt, not the receipt, and may not stand in for one (ANALYSIS F-3 result 2).
    """

    PHASES = 5

    def test_session_reuse_terminal_accounting(self) -> None:
        if not RUN_ORCA:
            self.skipTest("requires --orca-runtime and a ready Orca runtime")
        if ARTIFACT_DIR:
            artifact_dir = Path(ARTIFACT_DIR)
            try:
                result = run_session_reuse_runtime_scenario(artifact_dir)
            except UnsupportedOrcaContract as exc:
                self.skipTest(str(exc))
            self.assert_scenario_k(result, artifact_dir)
        else:
            with tempfile.TemporaryDirectory() as directory:
                artifact_dir = Path(directory)
                try:
                    result = run_session_reuse_runtime_scenario(artifact_dir)
                except UnsupportedOrcaContract as exc:
                    self.skipTest(str(exc))
                self.assert_scenario_k(result, artifact_dir)

    def test_the_supervised_reuse_expectation_is_historical_not_current(self) -> None:
        """PR #29 review MAJOR-1/MAJOR-2: the two runtime points, kept apart.

        Runs OFFLINE and unconditionally -- it is about what this head CLAIMS, not
        about what a runtime answers, so it must not be gated on ORCA_RUNTIME_TEST.

        The 1.4.184 supervised expectation (granted reuse, two terminals, five
        dispatches each) is a HISTORICAL point observation of an older harness
        revision. This head has not been run against 1.4.184, so 1.4.184 is not in the
        executable support set and scenario K can never execute against it -- which is
        asserted here, rather than left as a live `if` branch that would silently
        never run. The historical numbers stay recorded in
        SCENARIO_K_HISTORICAL_SUPERVISED_EXPECTATION so re-verifying that runtime does
        not have to re-derive them.
        """
        self.assertNotIn(
            SCENARIO_K_HISTORICAL_SUPERVISED_RUNTIME, SUPPORTED_ORCA_APP_VERSIONS
        )
        self.assertIn(
            SCENARIO_K_HISTORICAL_SUPERVISED_RUNTIME,
            HISTORICAL_ORCA_APP_VERSION_OBSERVATIONS,
        )
        # The verified point is in the support set, and it is the ONLY one -- so the
        # runtime identity assertion in assert_scenario_k() cannot be satisfied by a
        # second, unverified entry quietly appearing beside it.
        self.assertEqual(SUPPORTED_ORCA_APP_VERSIONS, (SCENARIO_K_VERIFIED_RUNTIME,))
        # The two records name different runtimes and different answers; a historical
        # entry that leaked into the executable set would collapse that distinction.
        self.assertEqual(
            set(SUPPORTED_ORCA_APP_VERSIONS)
            & set(HISTORICAL_ORCA_APP_VERSION_OBSERVATIONS),
            set(),
        )
        self.assertEqual(
            SCENARIO_K_HISTORICAL_SUPERVISED_EXPECTATION["reuse_decisions_granted"], 8
        )
        self.assertEqual(
            SCENARIO_K_HISTORICAL_SUPERVISED_EXPECTATION["terminal_creations"], 2
        )
        # ...and it is NOT the expectation asserted for the verified point.
        self.assertNotEqual(
            SCENARIO_K_HISTORICAL_SUPERVISED_EXPECTATION["terminal_creations"],
            SCENARIO_K_1_4_196_TERMINAL_CREATIONS,
        )

    @staticmethod
    def receipt_fields(snapshot: dict) -> dict[str, list]:
        """E-3's four fields, read out of the raw command log on disk."""
        terminal_effects: list[str] = []
        release_process_actions: list[str] = []
        retained_reasons: list[str] = []
        ownership_states: list[str] = []
        for row in snapshot["commands"]:
            command = row["command"]
            verb = command[1] if len(command) > 1 else command[0]
            payload = (row.get("response") or {}).get("result") or {}
            if verb == "worker-start":
                for effect in payload.get("effects") or ():
                    if isinstance(effect, dict) and effect.get("kind") == "terminal":
                        terminal_effects.append(str(effect.get("action") or ""))
            elif verb in {"worker-release", "worker-retain"}:
                release_process_actions.append(str(payload.get("processAction") or ""))
            elif verb == "worker-show":
                terminal_resource = payload.get("terminalResource") or {}
                retained_reasons.append(
                    str(terminal_resource.get("retainedReason") or "")
                )
                ownership_states.append(
                    str(terminal_resource.get("ownershipState") or "")
                )
        return {
            "terminal_effects": terminal_effects,
            "release_process_actions": release_process_actions,
            "retained_reasons": retained_reasons,
            "ownership_states": ownership_states,
        }

    def assert_scenario_k(self, result, artifact_dir: Path) -> None:
        # K-1: the scenario ran to completion
        self.assertEqual(result.scenario, "K")
        self.assertEqual(result.status, "COMPLETED")
        self.assertEqual(len(result.attempts), self.PHASES * 2)

        # K-1b (PR #29 review MAJOR-1): WHICH RUNTIME POINT produced this result.
        # Everything from K-2 down is the answer expected on that point specifically,
        # so the identity is asserted FIRST and read off the harness's own record --
        # written by preflight() only after validate_orca_contract() accepted the
        # runtime -- never inferred from the outcome that was observed. A result with
        # no identity ("" = no runtime proven) fails here rather than being matched
        # against whichever expectation happens to fit.
        self.assertEqual(result.orca_app_version, SCENARIO_K_VERIFIED_RUNTIME)
        # And that point is the one this head actually advertises, so the expectations
        # below cannot drift away from the support set without failing.
        self.assertEqual(SUPPORTED_ORCA_APP_VERSIONS, (SCENARIO_K_VERIFIED_RUNTIME,))

        # K-2: the production gate was ASKED at every same-role transition, and it
        # answered. A scenario that never reaches the gate proves nothing about it,
        # so the count is exact rather than "at least one".
        self.assertEqual(len(result.reuse_decisions), (self.PHASES - 1) * 2)
        granted = [d for d in result.reuse_decisions if d["eligible"]]
        refused = [d for d in result.reuse_decisions if not d["eligible"]]
        # THE 1.4.196 RESULT THIS PR CLAIMS TO VERIFY. Not "granted or refused, as long
        # as the books balance": on this runtime every fake-agent dispatch is TRACKED,
        # the gate's supervised evidence does not exist, and the documented answer is
        # eight fail-closed refusals. A regression that GRANTS any reuse here fails on
        # the next line -- which is the whole point of binding to the runtime point.
        self.assertEqual(granted, [])
        self.assertEqual(len(refused), (self.PHASES - 1) * 2)
        for decision in result.reuse_decisions:
            with self.subTest(dispatch=decision["dispatch_id"]):
                self.assertIn(decision["role"], CLOSE_ELIGIBLE_ROLES)
                self.assertTrue(decision["handle"])
                self.assertFalse(decision["eligible"])
                # A refusal must SAY WHICH conditions refused, BY NAME. "reasons is
                # non-empty" was the old check and it accepted any reason at all; the
                # four names below are the documented fail-closed set for the tracked
                # path, and refusing for a different condition is a different runtime
                # answer that must not pass as this one.
                self.assertEqual(
                    set(decision["reasons"]), SCENARIO_K_1_4_196_REFUSAL_REASONS
                )
                # Named once each: a duplicated condition would make the set test
                # above pass while the gate double-counted.
                self.assertEqual(
                    len(decision["reasons"]), len(SCENARIO_K_1_4_196_REFUSAL_REASONS)
                )

        # K-3: the refusal really TAKES EFFECT. Ten dispatches, ten distinct fresh
        # terminals -- pinned to the recorded number, not derived from `granted`, so
        # the count cannot follow a regression that changed the gate's answer.
        self.assertEqual(len(result.attempts), SCENARIO_K_1_4_196_TERMINAL_CREATIONS)
        self.assertEqual(
            result.terminal_creations, SCENARIO_K_1_4_196_TERMINAL_CREATIONS
        )
        self.assertEqual(
            sum(1 for attempt in result.attempts if attempt.terminal_created),
            SCENARIO_K_1_4_196_TERMINAL_CREATIONS,
        )
        self.assertEqual(
            len({attempt.terminal for attempt in result.attempts}),
            SCENARIO_K_1_4_196_TERMINAL_CREATIONS,
        )
        # A chain is recorded only for a terminal that served more than one dispatch.
        # Every decision was refused, so there is no chain at all; a non-empty mapping
        # here means a session WAS carried across dispatches despite the refusals.
        self.assertEqual(result.reuse_chains, {})
        # The same accounting identity the answer-agnostic version asserted, kept so
        # the ledger still has to add up and not merely match the pinned totals.
        chained = sum(len(chain) for chain in result.reuse_chains.values())
        self.assertEqual(
            chained + (result.terminal_creations - len(result.reuse_chains)),
            len(result.attempts),
        )

        # K-4: per-attempt lifecycle invariants.
        reuse_attempts = [
            attempt
            for attempt in result.attempts
            if attempt.lifecycle_action.startswith("reuse:")
        ]
        # Every attempt but the last of each role is handed onward alive, whether or
        # not the gate then accepts the session for the next one.
        self.assertEqual(len(reuse_attempts), (self.PHASES - 1) * 2)
        for attempt in result.attempts:
            with self.subTest(dispatch=attempt.dispatch_id):
                self.assertIn(attempt.worker_resource, set(WORKER_RESOURCE_OUTCOMES))
                self.assertEqual(attempt.finalizations, 1)
                self.assertIn(attempt.terminal_role, TERMINAL_ROLE_CLASSES)
                self.assertNotIn(attempt.worker_state, UNSETTLED_WORKER_STATES)
                self.assertIn(attempt.cleanup_authority, CLEANUP_AUTHORITY_STATES)
        # A reuse issues NO lifecycle mutation on either path; the label records WHICH
        # path produced it, so on the verified runtime point it is pinned to the ONE
        # label the tracked path produces. The supervised spelling
        # ("reuse:ownership-transfer-pending") belongs to the historical 1.4.184
        # observation recorded in SCENARIO_K_HISTORICAL_SUPERVISED_EXPECTATION; seeing
        # it here would mean a supervised adoption this runtime cannot perform.
        for attempt in reuse_attempts:
            with self.subTest(dispatch=attempt.dispatch_id):
                self.assertEqual(attempt.lifecycle_action, "reuse:tracked-external")
                self.assertNotEqual(
                    attempt.lifecycle_action,
                    SCENARIO_K_HISTORICAL_SUPERVISED_EXPECTATION[
                        "reuse_lifecycle_action"
                    ],
                )

        # K-4: identity is new on every attempt even though the session is not
        self.assertEqual(
            len({attempt.task_id for attempt in result.attempts}),
            len(result.attempts),
        )
        self.assertEqual(
            len({attempt.dispatch_id for attempt in result.attempts}),
            len(result.attempts),
        )

        # K-5 (E-3): the four first-order receipt fields, from the log on disk
        snapshot_path = artifact_dir / "scenario-k.json"
        self.assertTrue(snapshot_path.is_file(), snapshot_path)
        snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
        fields = self.receipt_fields(snapshot)
        # OS-41: these four fields are all read out of SUPERVISED receipts
        # (`worker-start` effects, `worker-release`/`worker-retain` process actions,
        # `worker-show`'s terminalResource). On a runtime that adopts the fake agent
        # they are populated; on one where every dispatch is tracked there are no such
        # receipts to read, and inventing a substitute is exactly the "the ledger's own
        # label may not stand in for a receipt" error D-4 forbids. So the shape is
        # tied to the path the run actually took. PR #29 review MAJOR-1: WHICH path
        # that is, is not discovered from the receipts -- it follows from the runtime
        # point asserted at K-1b. On SCENARIO_K_VERIFIED_RUNTIME every dispatch is
        # TRACKED, so there are no supervised receipts to read AT ALL, and a run that
        # produced some would mean a supervised adoption this runtime cannot perform.
        self.assertEqual(fields["terminal_effects"], [])
        # One `worker-show` per same-role transition -- the gate's own observation --
        # and every one of them reports NO terminalResource on the tracked path, which
        # `receipt_fields` reads out as the empty string. Asserted as the exact list so
        # both facts are pinned: how many observations were taken, and that not one of
        # them carried the supervised evidence the gate asks for.
        self.assertEqual(
            fields["retained_reasons"], [""] * ((self.PHASES - 1) * 2)
        )
        self.assertEqual(
            fields["ownership_states"], [""] * ((self.PHASES - 1) * 2)
        )
        # The tracked path issues no worker-start, no release and no retain. Its
        # release receipt is the agent process exiting, which the attempt records
        # as `release:natural-exit` -- and there must be exactly one per role.
        self.assertEqual(fields["release_process_actions"], [])
        self.assertEqual(
            sum(
                1
                for attempt in result.attempts
                if attempt.lifecycle_action == "release:natural-exit"
            ),
            2,
        )
        self.assertEqual(
            snapshot["result"]["terminal_creations"], result.terminal_creations
        )

        # K-6 (FINAL-I1-MAJOR-1): the boundary in the DISPATCHED INPUT and in the
        # agent's answer, read off the real runtime rather than off RuntimeAttempt.
        # The Task spec is what Orca replays into the preamble, so a spec without a
        # boundary is an agent that never received one, however complete the
        # coordinator's own records look.
        specs = [
            row["command"][row["command"].index("--spec") + 1]
            for row in snapshot["commands"]
            if row["command"][:2] == ["orchestration", "task-create"]
        ]
        self.assertEqual(len(specs), self.PHASES * 2)
        identities = {attempt.task_id for attempt in result.attempts} | {
            attempt.dispatch_id for attempt in result.attempts
        }
        for spec in specs:
            boundary = parse_task_boundary(spec)
            self.assertEqual(
                tuple(sorted(boundary)), tuple(sorted(TASK_BOUNDARY_KEYS))
            )
            # TASK_BOUNDARY_NEVER_CARRIED: no attempt's id, this one's included.
            for identity in identities:
                self.assertNotIn(identity, spec)
        self.assertEqual(
            sorted(int(parse_task_boundary(spec)["current_iteration"]) for spec in specs),
            sorted(list(range(1, self.PHASES + 1)) * 2),
        )

        # K-7 (PR #12 MAJOR-1): the iteration axis moving is not evidence that the
        # PHASE axis is right -- a boundary saying current_phase=complete satisfies
        # everything above. So the ten dispatched specs are checked against the exact
        # (role, phase) sequence the run performed, on the runtime path, and against
        # the artifact each pair is contracted to produce.
        boundaries = [parse_task_boundary(spec) for spec in specs]
        self.assertEqual(
            [
                (boundary["current_role"], boundary["current_phase"])
                for boundary in boundaries
            ],
            [
                (role, phase)
                for phase in CANONICAL_PHASES[: self.PHASES]
                for role in ("worker", "reviewer")
            ],
        )
        self.assertEqual(
            [boundary["artifact_contract"] for boundary in boundaries],
            [
                phase_artifact_contract(role=role, phase=phase, run_id=result.run_id)
                for phase in CANONICAL_PHASES[: self.PHASES]
                for role in ("worker", "reviewer")
            ],
        )
        for boundary in boundaries:
            with self.subTest(phase=boundary["current_phase"]):
                # The agent modes scenario K runs its fake agents with.
                self.assertNotIn(boundary["current_phase"], AGENT_MODES)
                # K-9: every artifact this run's own boundary names stays inside this
                # run's own directory, never the shared artifacts/ root.
                self.assertTrue(
                    boundary["artifact_contract"].startswith(
                        f"artifacts/runs/{result.run_id}/"
                    )
                )
        # K-8: the Reviewer's delta-first context references the artifacts this run
        # really produced and approved, not a placeholder shaped like one.
        reviewer_specs = [
            spec for spec in specs if parse_task_boundary(spec)["current_role"] == "reviewer"
        ]
        self.assertEqual(len(reviewer_specs), self.PHASES)
        for index, spec in enumerate(reviewer_specs):
            phase = CANONICAL_PHASES[index]
            context = parse_reviewer_context(spec)
            with self.subTest(phase=phase):
                self.assertEqual(context["current_phase"], phase)
                self.assertEqual(
                    context["current_delta"],
                    phase_artifact_contract(
                        role="worker", phase=phase, run_id=result.run_id
                    ),
                )
                self.assertEqual(
                    context["approved_baseline"],
                    " || ".join(
                        phase_artifact_contract(
                            role="worker", phase=earlier, run_id=result.run_id
                        )
                        for earlier in CANONICAL_PHASES[:index]
                    ),
                )
                self.assertIn("worker outcome=succeeded", context["validation"])
        for attempt in result.attempts:
            with self.subTest(dispatch=attempt.dispatch_id):
                # The phase reached the agent, not just the coordinator's log.
                self.assertIn(
                    f"{BOUNDARY_RECEIPT_PREFIX}current_phase: "
                    f"{dict(attempt.task_boundary)['current_phase']}",
                    attempt.body,
                )
        for attempt in result.attempts:
            with self.subTest(dispatch=attempt.dispatch_id):
                # The agent parsed the preamble it was handed and quoted it back.
                self.assertIn(
                    f"{BOUNDARY_RECEIPT_PREFIX}current_iteration: {attempt.iteration}",
                    attempt.body,
                )


if __name__ == "__main__":
    main()
