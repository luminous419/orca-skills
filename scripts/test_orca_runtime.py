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
        NEVER_CLOSE_ROLES,
        TERMINAL_ROLE_CLASSES,
        UNSETTLED_WORKER_STATES,
        WORKER_RESOURCE_OUTCOMES,
        UnsupportedOrcaContract,
        run_final_review_runtime_scenario,
        run_quality_profile_runtime_scenario,
        run_runtime_scenarios,
        run_session_reuse_runtime_scenario,
    )
except ModuleNotFoundError:
    from orca_runtime_harness import (
        CLEANUP_AUTHORITY_STATES,
        CLOSE_ELIGIBLE_ROLES,
        NEVER_CLOSE_ROLES,
        TERMINAL_ROLE_CLASSES,
        UNSETTLED_WORKER_STATES,
        WORKER_RESOURCE_OUTCOMES,
        UnsupportedOrcaContract,
        run_final_review_runtime_scenario,
        run_quality_profile_runtime_scenario,
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
        else:
            with tempfile.TemporaryDirectory() as directory:
                try:
                    results = run_runtime_scenarios(Path(directory))
                except UnsupportedOrcaContract as exc:
                    self.skipTest(str(exc))

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

    def assert_scenario_h(self, scenario_h) -> None:
        """A dependent created after settlement stays pending; it is never dispatched."""
        self.assertEqual(scenario_h.late_dependent_status, "pending")
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


class SessionReuseRuntimeIntegrationTests(unittest.TestCase):
    """Opt-in scenario K: same-role session reuse against the real runtime (E-3).

    Skipped unless ORCA_RUNTIME_TEST=1, exactly like the two integration classes
    above. Scenario K is deliberately outside run_runtime_scenarios(), whose A-I
    result set is pinned by an exact-set assertion; scenario J set that precedent.

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

        # K-2: ten dispatches, two terminals -- one chain per role
        self.assertEqual(result.terminal_creations, 2)
        self.assertEqual(
            sum(1 for attempt in result.attempts if attempt.terminal_created), 2
        )
        self.assertEqual(len({attempt.terminal for attempt in result.attempts}), 2)
        self.assertEqual(len(result.reuse_chains), 2)
        for handle, chain in result.reuse_chains.items():
            with self.subTest(handle=handle):
                self.assertEqual(len(chain), self.PHASES)
                self.assertEqual(len(set(chain)), self.PHASES)

        # K-3: every attempt but the last of each role is a reuse, and a reuse never
        # sends a lifecycle mutation, so axis (b) is the only place it is recorded.
        reuse_attempts = [
            attempt for attempt in result.attempts if attempt.worker_resource == "reuse"
        ]
        self.assertEqual(len(reuse_attempts), (self.PHASES - 1) * 2)
        for attempt in result.attempts:
            with self.subTest(dispatch=attempt.dispatch_id):
                self.assertIn(attempt.worker_resource, set(WORKER_RESOURCE_OUTCOMES))
                self.assertEqual(attempt.finalizations, 1)
                self.assertIn(attempt.terminal_role, TERMINAL_ROLE_CLASSES)
                self.assertNotIn(attempt.worker_state, UNSETTLED_WORKER_STATES)
                self.assertIn(attempt.cleanup_authority, CLEANUP_AUTHORITY_STATES)
        for attempt in reuse_attempts:
            with self.subTest(dispatch=attempt.dispatch_id):
                self.assertEqual(
                    attempt.lifecycle_action, "reuse:ownership-transfer-pending"
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
        self.assertEqual(len(fields["terminal_effects"]), self.PHASES * 2)
        # Two releases only: the last attempt of each role. Every other attempt is a
        # reuse and issues nothing at all.
        self.assertEqual(len(fields["release_process_actions"]), 2)
        self.assertTrue(fields["retained_reasons"])
        self.assertTrue(fields["ownership_states"])
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
