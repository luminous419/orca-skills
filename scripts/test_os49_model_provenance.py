#!/usr/bin/env python3
"""OS-49: durable model provenance, and non-drift across every round kind.

It must be possible to reconstruct which effective agent identity produced each phase
result and each review -- from the artifacts alone, with no running code. These tests
assert that, and the equally important converse: historical evidence is untouched, the
column tuple did not move, and a legacy run's logs stay byte-identical.
"""
from __future__ import annotations

import re
import tempfile
import unittest
from os import environ
from pathlib import Path
from unittest.mock import patch

from scripts import decision_gate, run_logging
from scripts.agent_profile import (
    EVENT_AGENT_IDENTITY_BOUND,
    EVENT_PROFILE_SELECTED,
    EVENT_ROUTING_RESOLVED,
    MODEL_EVIDENCE_NONE,
    MODEL_EVIDENCE_VERIFIED,
    RUNTIME_ORCHESTRATION,
    SELECTION_SELECTED,
    AgentProfileSelection,
    load_agent_profiles_text,
    materialize_run_routing,
)
from scripts.deterministic_workflow.fake_adapter import InProcessModelDriver
from scripts.orca_runtime_harness import (
    MODEL_SELECTION_REQUEST_METHODS,
    OrcaRuntimeHarness,
)
from scripts.task_context import (
    AGENT_ROUTING_KEYS,
    ROUTING_NO_MODEL,
    ROUTING_NOT_APPLICABLE,
    build_agent_routing_context,
)
from scripts.test_orca_runtime_contract import DECLARED_DONE_BODY, RecordingExec
from scripts.test_os49_delivery_barrier import (
    LEGACY_PROFILE,
    SPLIT_PROFILE,
    routing_from,
)

ROUND_KINDS = ("phase_gate", "correction", "downstream_revalidation", "final_review")


def log_rows(artifact_dir: Path, run_id: str) -> list[dict[str, str]]:
    """Every parseable ORCHESTRATOR_LOG row, read the way production readers read it."""
    path = run_logging.orchestrator_log_path(run_id, base=artifact_dir)
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines()[2:]:
        if not line.startswith("|"):
            continue
        cells = re.split(r"(?<!\\)\|", line)[1:-1]
        cells = [c.strip().replace(r"\|", "|").replace(r"\\", "\\") for c in cells]
        if len(cells) != len(run_logging.ORCHESTRATOR_LOG_COLUMNS):
            continue
        rows.append(dict(zip(run_logging.ORCHESTRATOR_LOG_COLUMNS, cells)))
    return rows


def detail_pairs(detail: str) -> dict[str, str]:
    return dict(
        part.split("=", 1) for part in detail.split(" ") if "=" in part
    )


class ProvenanceTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.artifact_dir = Path(self.temporary_directory.name)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def build(self, recorder, *, routing=None, model_driver=None,
              run_id="run_prov", phases=("implementation",)):
        with patch.dict(environ, {"ORCA_CLI_COMMAND": "/opt/orca-dev"}):
            harness = OrcaRuntimeHarness(
                self.artifact_dir, agent_routing=routing, model_driver=model_driver
            )
        harness._exec_orca = recorder
        harness.run_owner, harness.run_id = "term_owner", run_id
        harness.requested_phases = phases
        run_logging.open_orchestrator_log(
            run_id, base=self.artifact_dir, objective="os49 provenance"
        ) if hasattr(run_logging, "open_orchestrator_log") else None
        run_logging.open_decision_ledger(
            run_id,
            base=self.artifact_dir,
            phases=phases,
            risk=harness.risk or "",
            ledger_schema_version=decision_gate.LEDGER_RECORD_SCHEMA_VERSION,
        )
        self.admit_pending_pairs(harness, recorder)
        return harness

    def admit_pending_pairs(self, harness, recorder) -> None:
        """Run the OS-49 admission PRE-PASS for every same-command pair of this run.

        Needed from iteration 2 on, for review finding F-001: a same-command
        model-aware pair may not be delivered at all -- for EITHER role -- until both
        effective identities are positively verified and distinct, so a provenance test
        that drives real dispatches on `SPLIT_PROFILE` has to admit the pair before the
        first one. It is done here rather than at fourteen call sites because it is a
        precondition of the lifecycle, not a subject of these tests.

        It changes nothing these tests assert: `verify_model_identity()` issues no
        delivery command, settles no dispatch and writes no orchestrator row, so every
        provenance row below still comes entirely from the dispatches themselves. The two
        admission sessions get their own handles so they cannot share a ledger row with
        the dispatch terminals the base recorder pins; the pinned handle is restored
        afterwards, which is why existing join-key assertions are unaffected.

        A no-op without a driver, without routing, and on a model-less or
        distinct-command profile -- which is what keeps the legacy and
        `model_driver=None` tests byte-identical.
        """
        routing = harness.agent_routing
        if harness.model_driver is None or routing is None:
            return
        pinned = recorder.results.get("create")
        try:
            for phase in routing.pending_admission_phases():
                for role, mode in (("worker", "complete"), ("reviewer", "pass")):
                    recorder.results["create"] = {
                        "terminal": {"handle": f"term_admit_{phase}_{role}"}
                    }
                    handle = harness.create_fake_terminal(
                        role, mode, iteration=1, phase=phase
                    )
                    harness.verify_model_identity(
                        "task_admit", handle, role=role, phase=phase, attempt=1
                    )
        finally:
            if pinned is not None:
                recorder.results["create"] = pinned

    def arm(self, recorder, dispatch_id: str, task_id: str) -> None:
        """Point the stub at the NEXT (task, dispatch, delivery) triple.

        RecordingExec pins one of each, so without this a second round would settle the
        same ledger row and read back as a replay rather than as its own dispatch -- which
        is precisely what a non-drift test across rounds has to be able to distinguish.
        """
        import json

        recorder.results["task-create"] = {"task": {"id": task_id}}
        recorder.results["task-list"] = {
            "tasks": [{"id": task_id, "status": "completed"}]
        }
        recorder.results["worker-start"] = {
            "dispatchId": dispatch_id, "state": "ready",
        }
        recorder.results["check"] = {
            "deliveryId": f"dlv_{dispatch_id}",
            "timedOut": False,
            "messages": [
                {
                    "id": f"msg_{dispatch_id}",
                    "type": "worker_done",
                    "payload": json.dumps(
                        {
                            "taskId": task_id,
                            "dispatchId": dispatch_id,
                            "outcome": "succeeded",
                        }
                    ),
                    "body": DECLARED_DONE_BODY,
                }
            ],
        }

    def dispatch(self, harness, recorder, *, role="worker", mode="complete",
                phase="implementation", iteration=1, round_kind="phase_gate",
                task_id="task_g"):
        """One real dispatch through the production initiator.

        The spec is rendered by `dispatch_context` rather than being a placeholder string:
        it is what carries the decision-gate contract the B1 guard binds each round's
        boundary record to, so a multi-round chain settles its rounds instead of refusing
        the second one as unbound.
        """
        from scripts.orca_runtime_harness import dispatch_context

        self.arm(recorder, f"ctx_{task_id}_{iteration}", task_id)
        spec, _, _ = dispatch_context(
            role,
            iteration,
            mode,
            phase=phase,
            base_spec=f"{role} iteration {iteration}: {phase}",
            run_id=harness.run_id or "",
            requested_phases=harness.requested_phases,
            risk=harness.risk,
            risk_source=harness.risk_source,
        )
        # Created through the harness so the recorder captures this round's gate
        # mechanics off the real `task-create --spec`, which is what binds the settled
        # body's decision-gate record to this (phase, iteration).
        created = harness.create_task(spec)
        return harness.run_existing_task(
            role, iteration, mode, created, phase=phase, spec=spec,
            round_kind=round_kind,
        )


class IdentityRowTests(ProvenanceTestCase):
    def test_identity_row_emitted_once_per_settled_dispatch(self) -> None:
        for round_kind in ROUND_KINDS:
            with self.subTest(round_kind=round_kind):
                recorder = RecordingExec(
                    results={"check": RecordingExec.ACCEPTED_DONE}
                )
                run_id = f"run_{round_kind}"
                harness = self.build(
                    recorder,
                    routing=routing_from(SPLIT_PROFILE, "split"),
                    model_driver=InProcessModelDriver(),
                    run_id=run_id,
                )
                role = "reviewer" if round_kind == "final_review" else "worker"
                phase = (
                    "final_review" if round_kind == "final_review"
                    else "implementation"
                )
                if round_kind == "final_review":
                    harness.requested_phases = ("implementation",)
                self.dispatch(
                    harness, recorder, role=role,
                    mode="pass" if role == "reviewer" else "complete",
                    phase=phase, round_kind=round_kind,
                )
                identity = [
                    row for row in log_rows(self.artifact_dir, run_id)
                    if row["event"] == EVENT_AGENT_IDENTITY_BOUND
                ]
                self.assertEqual(len(identity), 1, identity)
                self.assertEqual(identity[0]["round_kind"], round_kind)

    def test_identity_row_result_column_carries_exactly_one_state(self) -> None:
        recorder = RecordingExec(results={"check": RecordingExec.ACCEPTED_DONE})
        harness = self.build(
            recorder, routing=routing_from(SPLIT_PROFILE, "split"),
            model_driver=InProcessModelDriver(),
        )
        self.dispatch(harness, recorder)
        row = next(
            r for r in log_rows(self.artifact_dir, "run_prov")
            if r["event"] == EVENT_AGENT_IDENTITY_BOUND
        )
        self.assertEqual(row["result"], f"model_state={MODEL_EVIDENCE_VERIFIED}")

    def test_identity_row_detail_carries_both_legs(self) -> None:
        recorder = RecordingExec(results={"check": RecordingExec.ACCEPTED_DONE})
        harness = self.build(
            recorder, routing=routing_from(SPLIT_PROFILE, "split"),
            model_driver=InProcessModelDriver(),
        )
        self.dispatch(harness, recorder)
        row = next(
            r for r in log_rows(self.artifact_dir, "run_prov")
            if r["event"] == EVENT_AGENT_IDENTITY_BOUND
        )
        pairs = detail_pairs(row["detail"])
        for key in (
            "command", "requested_model", "resolved_model",
            "request_method", "selection_token", "request_stamp", "observe_stamp",
            "observation_method", "selection_capability", "selection_verified",
            "profile", "profile_source", "schema",
        ):
            with self.subTest(key=key):
                self.assertIn(key, pairs)
        self.assertEqual(pairs["command"], "claude")
        self.assertEqual(pairs["requested_model"], "glm-5.2")
        self.assertEqual(pairs["resolved_model"], "glm-5.2")
        self.assertEqual(pairs["request_method"], MODEL_SELECTION_REQUEST_METHODS[0])
        self.assertEqual(pairs["selection_verified"], "true")
        self.assertEqual(pairs["schema"], "2")

    def test_request_ordinals_prove_request_before_verify_in_the_durable_row(self) -> None:
        """The ordering is reconstructible from the ARTIFACT alone, by arithmetic, with no
        running code and no appeal to a label."""
        recorder = RecordingExec(results={"check": RecordingExec.ACCEPTED_DONE})
        harness = self.build(
            recorder, routing=routing_from(SPLIT_PROFILE, "split"),
            model_driver=InProcessModelDriver(),
        )
        self.dispatch(harness, recorder)
        row = next(
            r for r in log_rows(self.artifact_dir, "run_prov")
            if r["event"] == EVENT_AGENT_IDENTITY_BOUND
        )
        pairs = detail_pairs(row["detail"])
        request_stamp = int(pairs["request_stamp"])
        observe_stamp = int(pairs["observe_stamp"])
        self.assertGreater(request_stamp, 0)
        self.assertLess(request_stamp, observe_stamp)
        self.assertEqual(observe_stamp, request_stamp + 1)
        self.assertTrue(pairs["selection_token"].startswith("run_prov:"))

    def test_identity_row_join_keys_match_the_sibling_settled_row(self) -> None:
        recorder = RecordingExec(results={"check": RecordingExec.ACCEPTED_DONE})
        harness = self.build(
            recorder, routing=routing_from(SPLIT_PROFILE, "split"),
            model_driver=InProcessModelDriver(),
        )
        self.dispatch(harness, recorder)
        rows = log_rows(self.artifact_dir, "run_prov")
        identity = next(r for r in rows if r["event"] == EVENT_AGENT_IDENTITY_BOUND)
        settled = next(r for r in rows if r["event"] == "dispatch_settled")
        for key in ("dispatch_id", "task_id", "terminal", "phase", "role", "iteration"):
            with self.subTest(key=key):
                self.assertEqual(identity[key], settled[key])

    def test_the_identity_row_precedes_its_settled_row(self) -> None:
        recorder = RecordingExec(results={"check": RecordingExec.ACCEPTED_DONE})
        harness = self.build(
            recorder, routing=routing_from(SPLIT_PROFILE, "split"),
            model_driver=InProcessModelDriver(),
        )
        self.dispatch(harness, recorder)
        events = [r["event"] for r in log_rows(self.artifact_dir, "run_prov")]
        self.assertLess(
            events.index(EVENT_AGENT_IDENTITY_BOUND), events.index("dispatch_settled")
        )

    def test_a_model_less_profile_run_emits_no_identity_row(self) -> None:
        recorder = RecordingExec(results={"check": RecordingExec.ACCEPTED_DONE})
        harness = self.build(
            recorder, routing=routing_from(LEGACY_PROFILE, "plain"),
            model_driver=InProcessModelDriver(),
        )
        self.dispatch(harness, recorder)
        rows = log_rows(self.artifact_dir, "run_prov")
        self.assertEqual(
            [r for r in rows if r["event"] == EVENT_AGENT_IDENTITY_BOUND], []
        )
        self.assertTrue([r for r in rows if r["event"] == "dispatch_settled"])

    def test_a_legacy_run_emits_no_identity_row(self) -> None:
        recorder = RecordingExec(results={"check": RecordingExec.ACCEPTED_DONE})
        harness = self.build(recorder)
        self.dispatch(harness, recorder)
        self.assertEqual(
            [r for r in log_rows(self.artifact_dir, "run_prov")
             if r["event"] == EVENT_AGENT_IDENTITY_BOUND],
            [],
        )

    def test_a_pre_dispatch_failure_emits_no_identity_row(self) -> None:
        """No dispatch, nothing delivered, so no identity to bind."""
        recorder = RecordingExec(results={"check": RecordingExec.ACCEPTED_DONE})
        harness = self.build(
            recorder, routing=routing_from(SPLIT_PROFILE, "split"), model_driver=None
        )
        with self.assertRaises(Exception):
            self.dispatch(harness, recorder)
        self.assertEqual(
            [r for r in log_rows(self.artifact_dir, "run_prov")
             if r["event"] == EVENT_AGENT_IDENTITY_BOUND],
            [],
        )


class HistoryIsUntouchedTests(ProvenanceTestCase):
    def test_columns_unchanged(self) -> None:
        """A new COLUMN would leave historical rows on disk and make every one of them
        INVISIBLE, because every reader skips a row whose cell count differs. OS-49 adds
        an event NAME instead."""
        self.assertEqual(
            run_logging.ORCHESTRATOR_LOG_COLUMNS,
            (
                "timestamp", "event", "phase", "role", "iteration", "task_id",
                "dispatch_id", "terminal", "action", "reuse", "gate_result",
                "review_verdict", "risk", "risk_source", "requested_phases",
                "round_kind", "decision_state", "decision_reason_code",
                "result", "detail",
            ),
        )

    def test_a_historical_row_still_parses_under_the_same_cell_count(self) -> None:
        recorder = RecordingExec(results={"check": RecordingExec.ACCEPTED_DONE})
        harness = self.build(
            recorder, routing=routing_from(SPLIT_PROFILE, "split"),
            model_driver=InProcessModelDriver(),
        )
        self.dispatch(harness, recorder)
        rows = log_rows(self.artifact_dir, "run_prov")
        self.assertTrue(rows)
        for row in rows:
            with self.subTest(event=row["event"]):
                self.assertEqual(
                    len(row), len(run_logging.ORCHESTRATOR_LOG_COLUMNS)
                )

    def test_an_unknown_event_is_skipped_not_misread(self) -> None:
        """Readers select by `event`, so a reader that does not know
        `agent_identity_bound` ignores it rather than mis-reading it as a settlement."""
        recorder = RecordingExec(results={"check": RecordingExec.ACCEPTED_DONE})
        harness = self.build(
            recorder, routing=routing_from(SPLIT_PROFILE, "split"),
            model_driver=InProcessModelDriver(),
        )
        self.dispatch(harness, recorder)
        rows = log_rows(self.artifact_dir, "run_prov")
        identity = [r for r in rows if r["event"] == EVENT_AGENT_IDENTITY_BOUND]
        self.assertEqual(len(identity), 1)
        # A pre-OS-49 reader looking for settlements finds exactly the settlements.
        self.assertEqual(
            len([r for r in rows if r["event"] == "dispatch_settled"]), 1
        )

    def test_provenance_failure_never_unwinds_a_settled_dispatch(self) -> None:
        """A logging failure lands in self._logging_errors; enforcement lives in the two
        gates, so a missing identity row can never be what PERMITTED a delivery."""
        recorder = RecordingExec(results={"check": RecordingExec.ACCEPTED_DONE})
        harness = self.build(
            recorder, routing=routing_from(SPLIT_PROFILE, "split"),
            model_driver=InProcessModelDriver(),
        )
        original = run_logging.log_orchestrator_event

        def exploding(*args, **kwargs):
            if kwargs.get("event") == EVENT_AGENT_IDENTITY_BOUND:
                raise OSError("disk full")
            return original(*args, **kwargs)

        with patch.object(run_logging, "log_orchestrator_event", exploding):
            attempt, _handle = self.dispatch(harness, recorder)
        self.assertEqual(attempt.outcome, "succeeded")
        self.assertTrue(harness._logging_errors)


class NonDriftTests(ProvenanceTestCase):
    """Correction, re-review, downstream revalidation and the Final Review must preserve
    the MATERIALIZED routing identity rather than silently changing models."""

    def test_the_materialized_routing_answers_identically_every_round(self) -> None:
        routing = routing_from(SPLIT_PROFILE, "split")
        first = routing.effective_identity("implementation", "worker")
        for _round in range(5):
            self.assertEqual(
                routing.effective_identity("implementation", "worker"), first
            )

    def test_every_round_preserves_the_identity_in_the_durable_rows(self) -> None:
        recorder = RecordingExec(results={"check": RecordingExec.ACCEPTED_DONE})
        harness = self.build(
            recorder, routing=routing_from(SPLIT_PROFILE, "split"),
            model_driver=InProcessModelDriver(),
        )
        for iteration, round_kind in enumerate(
            ("phase_gate", "correction", "downstream_revalidation"), start=1
        ):
            self.dispatch(
                harness, recorder, iteration=iteration, round_kind=round_kind,
                task_id=f"task_{iteration}",
            )
        rows = [
            r for r in log_rows(self.artifact_dir, "run_prov")
            if r["event"] == EVENT_AGENT_IDENTITY_BOUND
        ]
        self.assertEqual(len(rows), 3)
        resolved = {
            detail_pairs(r["detail"])["resolved_model"] for r in rows
        }
        self.assertEqual(
            resolved, {"glm-5.2"},
            "a same-(phase, role) pair of rows with different resolved models is a drift",
        )

    def test_a_correction_round_cannot_reuse_the_previous_rounds_request_evidence(self) -> None:
        """A round cannot INHERIT a verification: each mints its own ticket, and replaying
        an earlier round's token is refused before delivery."""
        recorder = RecordingExec()
        driver = InProcessModelDriver()
        harness = self.build(
            recorder, routing=routing_from(SPLIT_PROFILE, "split"),
            model_driver=driver,
        )
        # The admission pre-pass this run required before its first delivery drew its
        # own legs; what this test counts is the legs the ROUNDS draw.
        driver.requests.clear()
        self.dispatch(harness, recorder, iteration=1, round_kind="phase_gate")
        first_token = driver.tickets[-1].token
        self.dispatch(
            harness, recorder, iteration=2, round_kind="correction", task_id="task_2"
        )
        self.assertNotEqual(driver.tickets[-1].token, first_token)
        self.assertEqual(len(driver.requests), 4)   # both legs, both rounds

    def test_drift_between_rounds_is_refused_not_merely_logged(self) -> None:
        """Covered in full by the barrier suite's ambiguity test; asserted here on the
        PROVENANCE side -- a drifted round writes no identity row because it never
        dispatches."""
        from scripts.test_os49_delivery_barrier import conforming, RecordingDriver

        # Keyed by ROLE, and the Worker's first answer belongs to the admission pre-pass
        # that a same-command pair now requires before any delivery: admission and round
        # 1 agree, and round 2 is the drift this test is about.
        worker_answers = iter(("glm-5.2", "glm-5.2", "glm-9.9"))
        recorder = RecordingExec(results={"check": RecordingExec.ACCEPTED_DONE})
        harness = self.build(
            recorder, routing=routing_from(SPLIT_PROFILE, "split"),
            model_driver=RecordingDriver(
                lambda t, rq, ob: conforming(
                    t, rq, ob,
                    resolved=(
                        next(worker_answers) if t.role == "worker"
                        else "glm-5.3-flash"
                    ),
                )
            ),
        )
        self.dispatch(harness, recorder, iteration=1)
        with self.assertRaises(Exception):
            self.dispatch(harness, recorder, iteration=2, task_id="task_2")
        rows = [
            r for r in log_rows(self.artifact_dir, "run_prov")
            if r["event"] == EVENT_AGENT_IDENTITY_BOUND
        ]
        self.assertEqual(len(rows), 1, "the refused round must write no identity row")


class RoutingEvidenceAndSpecTests(ProvenanceTestCase):
    def test_the_run_scoped_evidence_rows_carry_schema_and_model(self) -> None:
        routing = routing_from(SPLIT_PROFILE, "split")
        rows = routing.evidence_rows()
        selected = next(r for r in rows if r["event"] == EVENT_PROFILE_SELECTED)
        self.assertIn("schema=2", selected["detail"])
        resolved = [r for r in rows if r["event"] == EVENT_ROUTING_RESOLVED]
        worker = next(
            r for r in resolved if r["phase"] == "implementation" and r["role"] == "worker"
        )
        self.assertIn("model=glm-5.2", worker["detail"])

    def test_a_legacy_routing_still_emits_nothing(self) -> None:
        legacy = materialize_run_routing(
            runtime=RUNTIME_ORCHESTRATION,
            selection=AgentProfileSelection(status="omitted"),
            requested_phases=("implementation",),
            risk="high",
        )
        self.assertEqual(legacy.evidence_rows(), ())

    def test_the_task_spec_carries_the_three_model_keys(self) -> None:
        context = build_agent_routing_context(
            routing=routing_from(SPLIT_PROFILE, "split"),
            current_phase="implementation",
        )
        self.assertEqual(set(context), set(AGENT_ROUTING_KEYS))
        self.assertEqual(context["phase_worker_model"], "glm-5.2")
        self.assertEqual(context["phase_reviewer_model"], "glm-5.3-flash")
        self.assertEqual(context["final_reviewer_model"], "glm-5.2")

    def test_task_spec_model_keys_render_none_without_a_model(self) -> None:
        context = build_agent_routing_context(
            routing=routing_from(LEGACY_PROFILE, "plain"),
            current_phase="implementation",
        )
        self.assertEqual(context["phase_worker_model"], ROUTING_NO_MODEL)
        self.assertEqual(context["phase_reviewer_model"], ROUTING_NO_MODEL)
        self.assertEqual(context["final_reviewer_model"], ROUTING_NO_MODEL)

    def test_a_role_this_runtime_does_not_have_reads_not_applicable(self) -> None:
        from scripts.agent_profile import RUNTIME_LOOP

        profiles = dict(
            load_agent_profiles_text(
                LEGACY_PROFILE, path="t.yaml", source="project_local"
            )
        )
        loop = materialize_run_routing(
            runtime=RUNTIME_LOOP,
            selection=AgentProfileSelection(
                status=SELECTION_SELECTED, name="plain", profile=profiles["plain"]
            ),
            requested_phases=("implementation",),
            risk=None,
        )
        context = build_agent_routing_context(
            routing=loop, current_phase="implementation"
        )
        self.assertEqual(context["final_reviewer_model"], ROUTING_NOT_APPLICABLE)
        self.assertEqual(context["final_reviewer"], ROUTING_NOT_APPLICABLE)

    def test_the_three_model_keys_collide_with_no_other_axis(self) -> None:
        from scripts.task_context import (
            QUALITY_GATE_KEYS,
            RISK_CONTEXT_KEYS,
        )

        model_keys = {
            "phase_worker_model", "phase_reviewer_model", "final_reviewer_model"
        }
        self.assertEqual(model_keys & set(RISK_CONTEXT_KEYS), set())
        self.assertEqual(model_keys & set(QUALITY_GATE_KEYS), set())


class FinalReviewAuditRecordTests(ProvenanceTestCase):
    def test_the_audit_record_takes_a_minor_bump_only(self) -> None:
        """A MAJOR bump would make every historical 1.0 record read as unknown_major --
        the records this family exists to preserve."""
        major, minor = run_logging.FINAL_REVIEW_AUDIT_SCHEMA_VERSION.split(".")[:2]
        self.assertEqual(major, "1")
        self.assertEqual(run_logging.FINAL_REVIEW_AUDIT_SCHEMA_VERSION, "1.1")

    def test_a_historical_one_point_zero_record_still_reads(self) -> None:
        import json

        record_dir = (
            self.artifact_dir / "artifacts" / "runs" / "run_old"
            / run_logging.FINAL_REVIEW_AUDIT_DIRNAME / "attempt1__task_a__ctx_a"
        )
        record_dir.mkdir(parents=True)
        (record_dir / run_logging.FINAL_REVIEW_AUDIT_RECORD_FILENAME).write_text(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "record_kind": run_logging.FINAL_REVIEW_AUDIT_RECORD_KIND,
                    "run_id": "run_old",
                    "final_review_attempt": 1,
                    "task_id": "task_a",
                    "dispatch_id": "ctx_a",
                    "dispatch_key": "attempt1__task_a__ctx_a",
                    "recorded_at": "1970-01-01T00:00:00Z",
                    "stored_task_spec": {"captured": False},
                    "delivery_evidence": {"captured": False},
                    "report": {"captured": False},
                    "provenance_state": "accepted",
                    "void_reason": "",
                    "settlement_state": "settled",
                }
            ),
            encoding="utf-8",
        )
        verdict = run_logging.read_final_review_attempt_provenance(
            "run_old", 1, base=self.artifact_dir
        )
        self.assertNotIn("unknown_major", str(verdict))
        self.assertEqual(verdict["violations"], [])
        self.assertEqual(verdict["accepted_dispatch_key"], "attempt1__task_a__ctx_a")

    def test_the_five_model_fields_are_redacted(self) -> None:
        for field in (
            "reviewer_requested_model", "reviewer_resolved_model",
            "reviewer_model_state", "reviewer_model_request_method",
            "reviewer_model_request_evidence",
        ):
            with self.subTest(field=field):
                self.assertIn(
                    field, run_logging.FINAL_REVIEW_REDACTED_METADATA_FIELDS
                )

    def test_the_audit_record_carries_the_final_reviewers_request_evidence(self) -> None:
        import json

        record_dir = run_logging.write_final_review_audit_record(
            "run_audit",
            base=self.artifact_dir,
            final_review_attempt=1,
            task_id="task_a",
            dispatch_id="ctx_a",
            provenance_state="accepted",
            settlement_state="settled",
            reviewer_terminal="term_fr",
            reviewer_agent_command="claude",
            reviewer_agent_origin="phase",
            reviewer_requested_model="glm-5.2",
            reviewer_resolved_model="glm-5.2",
            reviewer_model_state=MODEL_EVIDENCE_VERIFIED,
            reviewer_model_request_method=MODEL_SELECTION_REQUEST_METHODS[0],
            reviewer_model_request_evidence="run_audit:task_a:term_fr:x:y:1:1:2->3",
            capture=False,
        )
        record = json.loads(
            (record_dir / run_logging.FINAL_REVIEW_AUDIT_RECORD_FILENAME).read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(record["schema_version"], "1.1")
        self.assertEqual(record["reviewer_resolved_model"], "glm-5.2")
        self.assertEqual(record["reviewer_model_state"], MODEL_EVIDENCE_VERIFIED)
        evidence = record["reviewer_model_request_evidence"]
        request_stamp, observe_stamp = evidence.rsplit(":", 1)[1].split("->")
        self.assertLess(int(request_stamp), int(observe_stamp))

    def test_a_model_less_final_review_record_carries_empty_model_fields(self) -> None:
        import json

        record_dir = run_logging.write_final_review_audit_record(
            "run_plain",
            base=self.artifact_dir,
            final_review_attempt=1,
            task_id="task_a",
            dispatch_id="ctx_a",
            provenance_state="accepted",
            settlement_state="settled",
            reviewer_agent_command="codex",
            capture=False,
        )
        record = json.loads(
            (record_dir / run_logging.FINAL_REVIEW_AUDIT_RECORD_FILENAME).read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(record["reviewer_requested_model"], "")
        self.assertEqual(record["reviewer_model_request_evidence"], "")


class StandaloneJournalUntouchedTests(unittest.TestCase):
    def test_the_journal_record_keys_are_unchanged(self) -> None:
        """OS-49 adds no JournalRecord field: `write` rejects unknown fields and the digest
        covers the record, so a field is not free there and a schema break is not
        authorised."""
        from scripts.deterministic_workflow import standalone_journal

        keys = standalone_journal._RECORD_KEYS
        self.assertNotIn("model", " ".join(keys))
        self.assertEqual(
            keys, tuple(standalone_journal.JournalRecord.__annotations__)
        )


if __name__ == "__main__":
    unittest.main()
