#!/usr/bin/env python3
"""OS-49 BUGFIX round: one direct regression per confirmed PR #37 review finding.

Every test here is written so that it FAILS on the pre-fix commit (c3d7484) and passes
after. Each class names the finding it locks and states the pre-fix failure mode, because a
regression that passes before and after proves nothing.

  M1  the reference driver's harness import is dual-layout        -> InstalledLayoutTests
  M2  cross-phase resolved-model drift on one reused session      -> SessionModelDriftTests
  M3  driver shape + driver failure normalization                 -> DriverShapeTests
  M4  the dead `model_selector` surface is gone                   -> locked in
                                                                     test_os49_driver_seam.py
                                                                     and
                                                                     test_os49_vocabulary_locks.py
  M5  one session cannot be verified as both roles                -> PairSessionIdentityTests
  M6  the driver seam exists on the normal construction path      -> ConstructionSeamTests
  M7  the launcher-parity tests are host-independent              -> locked in
                                                                     test_os49_launcher_parity.py
  N1  accepted evidence does not outlive a failed delivery        -> EvidenceLifetimeTests
  N2  the redaction policy version denotes the category tuple     -> locked in
                                                                     test_os49_contract_locks.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from os import environ
from pathlib import Path
from unittest.mock import patch

from scripts import agent_profile, release_manifest
from scripts.agent_profile import (
    MODEL_EVIDENCE_VERIFIED,
    MODEL_SELECTION_VERIFIED_CAPABILITY,
    REASON_MODEL_NOT_SUPPORTED,
    REASON_WORKER_REVIEWER_MUST_DIFFER,
)
from scripts.deterministic_workflow import launcher
from scripts.deterministic_workflow.fake_adapter import FakeAdapter, InProcessModelDriver
from scripts.orca_runtime_harness import (
    MODEL_SELECTION_AMBIGUOUS,
    SELF_HANDLE_ENV,
    MODEL_SELECTION_UNSUPPORTED,
    MODEL_SELECTION_UNVERIFIED,
    OrcaRuntimeError,
    OrcaRuntimeHarness,
)
from scripts.test_orca_runtime_contract import RecordingExec, SequentialTerminalExec
from scripts.test_os49_delivery_barrier import (
    BarrierTestCase,
    RecordingDriver,
    SPLIT_PROFILE,
    conforming,
    routing_from,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
INSTALLED_TOOLS = REPO_ROOT / release_manifest.ORCHESTRATION_SKILL_NAME / "tools"

#: A v2 profile whose Worker and Reviewer use DIFFERENT commands, so Gate A's row 1 settles
#: independence on the commands alone and the model axis is the only thing under test. Both
#: phases declare the SAME requested alias for the Worker, which is exactly the shape M2 is
#: about: an equal alias is a declaration, not evidence that it resolved the same way twice.
CROSS_PHASE_PROFILE = (
    "version: 2\n"
    "profiles:\n"
    "  drift:\n"
    "    phases:\n"
    "      implementation:\n"
    "        worker:\n"
    "          command: claude\n"
    "          model: alias-x\n"
    "        reviewer: codex\n"
    "      test:\n"
    "        worker:\n"
    "          command: claude\n"
    "          model: alias-x\n"
    "        reviewer: codex\n"
    "    final_review:\n"
    "      reviewer: codex\n"
)


def _verified_resolver(pick):
    """A `RecordingDriver` reporting `verified` with a resolved model `pick(ticket)` chooses.

    Separate from `InProcessModelDriver`, deliberately: that driver OWNS satisfaction and
    reports `mismatch` whenever the resolved value differs from the requested one, which is
    correct behaviour and would mask the cases under test behind a different refusal. These
    report `verified` for a resolved value the routing did not literally request, which is
    the only way to construct an ALIAS -- the case the whole model axis exists for.
    """

    def evidence(ticket, request_stamp, observe_stamp):
        return conforming(
            ticket, request_stamp, observe_stamp,
            state=MODEL_EVIDENCE_VERIFIED, resolved=pick(ticket),
        )

    return RecordingDriver(evidence)


def resolving_per_session(*models: str):
    """One model per TERMINAL, assigned in first-seen order and stable thereafter.

    What a correct provider looks like: a session's resolved model does not change between
    attempts, so re-verifying the same session resolves to the same value. Used by every
    test whose subject is NOT drift, so a second verification of one session cannot refuse
    for the wrong reason.
    """
    assigned: dict[str, str] = {}

    def pick(ticket):
        if ticket.terminal not in assigned:
            assigned[ticket.terminal] = models[min(len(assigned), len(models) - 1)]
        return assigned[ticket.terminal]

    return _verified_resolver(pick)


def drifting(*models: str):
    """`models[n]` on the n-th CALL, whatever the session. The M2 subject itself."""
    calls: list[int] = []

    def pick(_ticket):
        index = min(len(calls), len(models) - 1)
        calls.append(index)
        return models[index]

    return _verified_resolver(pick)


# ---- M1 -------------------------------------------------------------------------------

class InstalledLayoutTests(unittest.TestCase):
    """M1. `InProcessModelDriver.select_and_verify()` must work in the INSTALLED layout.

    Pre-fix failure mode: the method did `from ..orca_runtime_harness import ModelEvidence`.
    In the repository the package is `scripts.deterministic_workflow`, so `..` is `scripts`
    and it resolved. In the installed flat Skill layout `deterministic_workflow` is
    top-level and the harness is a `tools/` sibling, so `..` points outside every package:

        ImportError: attempted relative import beyond top-level package

    Because the import was LAZY the module still imported cleanly, so a module-level import
    test would have passed on the broken code. This runs in a SEPARATE interpreter whose
    `sys.path` is the installed `tools/` directory, proves `import scripts` fails there, and
    then CALLS the method -- which is the only thing that could have caught it.
    """

    DRIVER = textwrap.dedent(
        '''
        import json, os, sys
        sys.path.insert(0, os.environ["INSTALLED_TOOLS"])
        # The whole point: if a repository module were reachable here, the result below
        # would say nothing about the installed copy.
        try:
            import scripts  # noqa: F401
        except ImportError:
            pass
        else:
            raise SystemExit("REPOSITORY_ON_PATH: not testing the installed copy")

        from deterministic_workflow.fake_adapter import InProcessModelDriver
        import orca_runtime_harness as runtime


        class Ticket:
            run_id, task_id, terminal = "run_i", "task_i", "term_i"
            role, phase, attempt = "worker", "implementation", 1
            command, requested_model = "claude", "glm-5.2"
            token = "run_i:task_i:term_i:worker:implementation:1:1"

            def __init__(self):
                self.drawn = 0

            def stamp(self):
                self.drawn += 1
                return self.drawn


        evidence = InProcessModelDriver().select_and_verify(Ticket())
        print(json.dumps({
            "type": type(evidence).__name__,
            "is_model_evidence": isinstance(evidence, runtime.ModelEvidence),
            "state": evidence.state,
            "resolved_model": evidence.resolved_model,
            "request_stamp": evidence.request_stamp,
            "observe_stamp": evidence.observe_stamp,
            "selection_token": evidence.selection_token,
        }))
        '''
    )

    def test_the_reference_driver_runs_in_the_installed_flat_layout(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            script = Path(directory) / "driver.py"
            script.write_text(self.DRIVER, encoding="utf-8")
            completed = subprocess.run(
                [sys.executable, str(script)],
                cwd=directory,                      # outside the repository
                text=True,
                capture_output=True,
                env={
                    "PATH": os.environ.get("PATH", ""),
                    "HOME": directory,
                    "INSTALLED_TOOLS": str(INSTALLED_TOOLS),
                    # No PYTHONPATH: the driver's own sys.path insert is the only source.
                },
            )
        self.assertEqual(
            completed.returncode, 0,
            f"the installed driver failed:\nstdout={completed.stdout}\n"
            f"stderr={completed.stderr}",
        )
        payload = json.loads(completed.stdout.strip().splitlines()[-1])
        self.assertTrue(payload["is_model_evidence"], payload)
        self.assertEqual(payload["type"], "ModelEvidence")
        self.assertEqual(payload["state"], MODEL_EVIDENCE_VERIFIED)
        self.assertEqual(payload["resolved_model"], "glm-5.2")
        # The two legs really ran in order inside the installed copy, so this is not just
        # an import smoke test.
        self.assertEqual(payload["request_stamp"], 1)
        self.assertEqual(payload["observe_stamp"], 2)
        self.assertEqual(
            payload["selection_token"], "run_i:task_i:term_i:worker:implementation:1:1"
        )

    def test_no_parent_relative_import_remains_in_the_engine_package(self) -> None:
        """The mechanical half, over the AST rather than over the text: a relative import of
        level >= 2 reaches outside this package, which cannot resolve in a layout where the
        package is top-level. Both copies are walked, so the installed one cannot drift.

        AST and not a grep: the shim's own docstring QUOTES the broken `from ..` form to say
        what it replaced, and a text scan would either fail on the explanation or have to be
        taught to ignore comments -- neither of which is the fact under test.
        """
        import ast

        for directory in (
            REPO_ROOT / "scripts" / "deterministic_workflow",
            INSTALLED_TOOLS / "deterministic_workflow",
        ):
            for path in sorted(directory.rglob("*.py")):
                tree = ast.parse(path.read_text(encoding="utf-8"))
                for node in ast.walk(tree):
                    if not isinstance(node, ast.ImportFrom):
                        continue
                    with self.subTest(path=path.name, line=node.lineno):
                        self.assertLessEqual(
                            node.level or 0, 1,
                            f"{path.name}:{node.lineno} imports {node.level} levels up, "
                            "which resolves outside the package in the installed layout",
                        )


# ---- M2 -------------------------------------------------------------------------------

class SessionModelDriftTests(BarrierTestCase):
    """M2. One physical session reused across phases must be refused when the RESOLVED
    model drifts, even though the REQUESTED alias is identical.

    Pre-fix failure mode: leg (i) compared against `self._model_identity[(phase,
    routing_role)]`, so an IMPLEMENTATION record and a TEST record had different keys and
    were never compared; reuse condition 9 compared `recorded_requested !=
    requested_model`, which is equal here by construction. So alias-x -> model-a followed
    by alias-x -> model-b on the SAME terminal was accepted and delivered.
    """

    def drift_harness(self, driver):
        recorder = SequentialTerminalExec()
        harness = self.build(
            recorder,
            routing=routing_from(
                CROSS_PHASE_PROFILE, "drift", phases=("implementation", "test")
            ),
            model_driver=driver,
            phases=("implementation", "test"),
        )
        return recorder, harness

    def test_the_same_alias_resolving_to_a_second_model_is_refused(self) -> None:
        driver = drifting("model-a", "model-b")
        recorder, harness = self.drift_harness(driver)
        handle = harness.create_fake_terminal(
            "worker", "complete", iteration=1, phase="implementation"
        )
        harness.verify_model_identity(
            "task_impl", handle, role="worker", phase="implementation", attempt=1
        )
        recorder.commands.clear()
        with self.assertRaises(OrcaRuntimeError) as caught:
            harness.start_worker(
                "task_test", handle, "spec", role="worker", phase="test", attempt=1
            )
        message = str(caught.exception)
        self.assertTrue(
            message.startswith(MODEL_SELECTION_AMBIGUOUS),
            f"expected {MODEL_SELECTION_AMBIGUOUS}, got: {message}",
        )
        # The refusal names BOTH resolved values, so a reader can see the drift itself
        # rather than only that something was ambiguous.
        self.assertIn("model-a", message)
        self.assertIn(handle, message)
        self.assertNothingDelivered(recorder)

    def test_the_same_alias_resolving_to_the_same_model_still_delivers(self) -> None:
        """The non-drift case must be untouched: this refuses drift, not cross-phase reuse."""
        driver = drifting("model-a", "model-a")
        recorder, harness = self.drift_harness(driver)
        handle = harness.create_fake_terminal(
            "worker", "complete", iteration=1, phase="implementation"
        )
        harness.verify_model_identity(
            "task_impl", handle, role="worker", phase="implementation", attempt=1
        )
        dispatch, supervised = harness.start_worker(
            "task_test", handle, "spec", role="worker", phase="test", attempt=1
        )
        self.assertTrue(supervised)
        self.assertEqual(
            harness.ledger_terminal(handle)["resolved_model"], "model-a"
        )

    def test_the_drift_record_is_run_scoped(self) -> None:
        """A second run on the same harness instance is not refused by the first run's record.

        OS-49 BUGFIX (review B1). This test used to hand-copy the three assignments
        `finish()` made and assert against the copy. The intent was right and the copy was
        the weakness: B2 added two more pieces of run-scoped model state and an imitation of
        the run boundary cannot notice that, so the test went on claiming the boundary was
        clean while asserting nothing about it. It now drives the REAL boundary --
        `start_run()`, which is also where B1 moved the reset to, because that is the one
        point every run passes through whether or not the previous one reached `finish()`.
        """
        driver = drifting("model-a", "model-b")
        recorder, harness = self.drift_harness(driver)
        recorder.results["run-create"] = {"run": {"id": "run_drift_one"}}
        harness.start_run("drift one", requested_phases=("implementation", "test"))
        handle = harness.create_fake_terminal(
            "worker", "complete", iteration=1, phase="implementation"
        )
        harness.verify_model_identity(
            "task_impl", handle, role="worker", phase="implementation", attempt=1
        )
        self.assertIn(handle, harness._model_session_identity)
        # The real run boundary, with run 1 NEVER finishing -- which is the B1 premise.
        recorder.results["run-create"] = {"run": {"id": "run_drift_two"}}
        harness.start_run("drift two", requested_phases=("implementation", "test"))
        harness.verify_model_identity(
            "task_impl2", handle, role="worker", phase="implementation", attempt=2
        )
        self.assertEqual(
            harness._model_session_identity[handle][2].resolved_model, "model-b"
        )

    def test_both_run_boundaries_clear_every_piece_of_run_scoped_model_state(self) -> None:
        """Asserted against the real sources rather than against a copy of what they do.

        OS-49 BUGFIX (review B1/B2). Two changes from the iteration-2 version. It now reads
        `start_run()` as well as `finish()`, because `finish()` alone was the B1 defect: a
        run that fails or is abandoned never reaches it, and the state leaked into the next
        run. And it now requires the two B2 history maps, so the next map added to the run's
        model state cannot be forgotten at either boundary without a test saying so.
        """
        import inspect

        maps = (
            "self._model_identity = {}",
            "self._model_pending_evidence = {}",
            "self._model_session_identity = {}",
            "self._model_role_history = {}",
            "self._model_session_history = {}",
        )
        for boundary in (OrcaRuntimeHarness.start_run, OrcaRuntimeHarness.finish):
            source = inspect.getsource(boundary)
            for reset in maps:
                self.assertIn(
                    reset, source,
                    f"{boundary.__name__}() does not clear {reset!r}; run-scoped model "
                    "state that survives a run boundary can refuse or admit the next run "
                    "on evidence no session in it produced",
                )


# ---- M3 -------------------------------------------------------------------------------

class BrokenDriverShapes:
    """The four shapes a driver can be wrong in, as objects rather than as prose."""

    class Missing:
        """No `select_and_verify` at all. Pre-fix: AttributeError out of the barrier."""

    class NotCallable:
        """A `select_and_verify` ATTRIBUTE. Pre-fix: TypeError out of the barrier."""

        select_and_verify = "not a method"

    class Raising:
        """Pre-fix: the driver's own exception escaped the barrier untouched."""

        def select_and_verify(self, ticket):
            raise ZeroDivisionError("the provider channel exploded")

    class WrongType:
        """Pre-fix: ALREADY refused by name -- the one of the four that was covered."""

        def select_and_verify(self, ticket):
            ticket.stamp()
            ticket.stamp()
            return {"state": "verified", "resolved_model": "model-a"}


class DriverShapeTests(BarrierTestCase):
    """M3. Every way a driver can be invalid or fail must leave through the OS-49 closed
    vocabulary, never as a raw AttributeError, TypeError or arbitrary driver exception.

    `self.refuse()` asserts `OrcaRuntimeError` AND that no delivery command ran, so each of
    the first three cases fails pre-fix on the exception TYPE alone.
    """

    #: Every case here is about a DRIVER's shape or failure, so the pair must be
    #: admissible for the delivery to reach the driver at all (final review R2). The two
    #: cases that refuse ABOVE the driver call -- a missing and a non-callable
    #: `select_and_verify` -- are unaffected either way. See `BarrierTestCase.refuse()`.
    ADMIT_PAIR = True

    def test_a_driver_without_select_and_verify_is_unsupported(self) -> None:
        message = self.assertRefusedWith(
            MODEL_SELECTION_UNSUPPORTED, driver=BrokenDriverShapes.Missing()
        )
        self.assertIn("callable select_and_verify", message)
        self.assertIn("Missing", message)

    def test_a_non_callable_select_and_verify_is_unsupported(self) -> None:
        message = self.assertRefusedWith(
            MODEL_SELECTION_UNSUPPORTED, driver=BrokenDriverShapes.NotCallable()
        )
        self.assertIn("callable select_and_verify", message)

    def test_a_raising_driver_is_unverified_and_names_the_exception(self) -> None:
        message = self.assertRefusedWith(
            MODEL_SELECTION_UNVERIFIED, driver=BrokenDriverShapes.Raising()
        )
        self.assertIn("ZeroDivisionError", message)
        self.assertIn("the provider channel exploded", message)
        # The whole attempt diagnosis is still attached, exactly as for every other
        # refusal: a normalized failure must not be a less informative one.
        self.assertIn("minted_token=", message)
        self.assertIn("expected_stamps=", message)

    def test_a_wrong_return_type_is_unverified(self) -> None:
        """Covered BEFORE this round too -- stated rather than claimed as new. It is kept
        because the other three now share its code path and a future refactor could
        regress it."""
        message = self.assertRefusedWith(
            MODEL_SELECTION_UNVERIFIED, driver=BrokenDriverShapes.WrongType()
        )
        self.assertIn("not ModelEvidence", message)

    def test_a_raising_driver_leaves_no_usable_stamp_behind(self) -> None:
        """The `finally:` revocation still runs ahead of the normalization, so the ordering
        proof is not weakened by catching the exception."""
        stolen: list = []

        class Thief:
            def select_and_verify(self, ticket):
                stolen.append(ticket)
                raise RuntimeError("after stashing the ticket")

        self.assertRefusedWith(MODEL_SELECTION_UNVERIFIED, driver=Thief())
        self.assertEqual(len(stolen), 1)
        with self.assertRaises(OrcaRuntimeError):
            stolen[0].stamp()

    def test_every_broken_shape_is_refused_through_the_reuse_gate_too(self) -> None:
        """Reuse condition 9 asks the SAME capability question, so a driver that cannot be
        called is `model_capability_unsupported` there rather than passing and leaking at
        delivery time."""
        from scripts.orca_runtime_harness import MODEL_CAPABILITY_UNSUPPORTED

        for name in ("Missing", "NotCallable"):
            with self.subTest(driver=name):
                recorder = RecordingExec()
                harness = self.build(
                    recorder,
                    routing=routing_from(SPLIT_PROFILE, "split"),
                    model_driver=getattr(BrokenDriverShapes, name)(),
                )
                reasons = harness._model_identity_reuse_reasons(
                    {"model_state": MODEL_EVIDENCE_VERIFIED,
                     "requested_model": "glm-5.2",
                     "resolved_model": "glm-5.2",
                     "model_request_method": "driver_select_and_verify",
                     "model_request_evidence": "tok:1->2",
                     "model_observed_at_dispatch": "ctx_1"},
                    requested_model="glm-5.2",
                    dispatch_id="ctx_1",
                )
                self.assertIn(MODEL_CAPABILITY_UNSUPPORTED, reasons)

    def test_one_capability_derivation_answers_for_every_reader(self) -> None:
        """Gate A, Gate B, reuse condition 9 and the fake adapter's `capabilities()` must
        agree on every shape, because they now ask one function."""
        table = (
            (None, False),
            (BrokenDriverShapes.Missing(), False),
            (BrokenDriverShapes.NotCallable(), False),
            (BrokenDriverShapes.Raising(), True),      # callable: a RUNTIME failure, not a
            (InProcessModelDriver(), True),            # shape failure -- Gate B's business
        )
        for driver, expected in table:
            with self.subTest(driver=type(driver).__name__):
                derived = MODEL_SELECTION_VERIFIED_CAPABILITY in (
                    agent_profile.model_selection_capabilities(driver)
                )
                self.assertEqual(derived, expected)
                self.assertEqual(
                    MODEL_SELECTION_VERIFIED_CAPABILITY
                    in FakeAdapter([], model_driver=driver).capabilities(),
                    expected,
                )


# ---- M5 -------------------------------------------------------------------------------

class PairSessionIdentityTests(BarrierTestCase):
    """M5. One physical terminal must not be able to play both sides of a pair.

    Pre-fix failure mode: pair admission compared `counterpart.observed_at_run` and
    `counterpart.resolved_model` and never read `counterpart.observed_at_terminal`, which
    `ModelEvidence` has carried since OS-49. So verifying reviewer/model-B and then
    worker/model-A on ONE handle produced two "independent" effective identities and every
    later delivery saw an admitted pair -- while the Skill invariant
    `Worker session != Reviewer session` says there is only one agent.
    """

    def one_session_harness(self, driver):
        recorder = SequentialTerminalExec()
        harness = self.build(
            recorder, routing=routing_from(SPLIT_PROFILE, "split"), model_driver=driver
        )
        return recorder, harness

    # `drifting`, NOT `resolving_per_session`, and the choice is load-bearing: the two
    # verifications on the one terminal must resolve to DIFFERENT models. If they resolved
    # to the same model the pre-existing resolved-value comparison would already refuse the
    # pair, and the test would pass on the broken code for a reason that has nothing to do
    # with session identity. Two DISTINCT resolved models on one session is exactly the case
    # `(command, resolved_model)` cannot see.
    def test_one_terminal_verified_as_both_roles_is_refused(self) -> None:
        driver = drifting("model-b", "model-a")
        recorder, harness = self.one_session_harness(driver)
        handle = harness.create_fake_terminal(
            "reviewer", "pass", iteration=1, phase="implementation"
        )
        harness.verify_model_identity(
            "task_rev", handle, role="reviewer", phase="implementation", attempt=1
        )
        with self.assertRaises(OrcaRuntimeError) as caught:
            harness.verify_model_identity(
                "task_wkr", handle, role="worker", phase="implementation", attempt=1
            )
        message = str(caught.exception)
        self.assertTrue(
            message.startswith(REASON_WORKER_REVIEWER_MUST_DIFFER),
            f"expected {REASON_WORKER_REVIEWER_MUST_DIFFER}, got: {message}",
        )
        self.assertIn(handle, message)
        self.assertIn("one physical session cannot be both sides", message)
        self.assertNothingDelivered(recorder)

    def test_the_refusal_holds_in_the_other_order_too(self) -> None:
        driver = drifting("model-a", "model-b")
        _recorder, harness = self.one_session_harness(driver)
        handle = harness.create_fake_terminal(
            "worker", "complete", iteration=1, phase="implementation"
        )
        harness.verify_model_identity(
            "task_wkr", handle, role="worker", phase="implementation", attempt=1
        )
        with self.assertRaises(OrcaRuntimeError) as caught:
            harness.verify_model_identity(
                "task_rev", handle, role="reviewer", phase="implementation", attempt=1
            )
        self.assertTrue(
            str(caught.exception).startswith(REASON_WORKER_REVIEWER_MUST_DIFFER)
        )

    def test_the_refused_second_role_records_nothing(self) -> None:
        """A refused pre-pass must leave no trace a later delivery could read as earned.

        WHY THIS TEST'S MEANING CHANGED UNDER THE B1 HOIST (final review R3). Read the
        surviving reviewer record below against the two code shapes:

          PRE-HOIST: the session check sat BELOW `select_and_verify()`, so attempt 2
          actually CALLED this `drifting("model-b", "model-a")` driver and it switched
          handle onto model-a. The assertions then said a reviewer/model-b record
          SURVIVES a session that had physically moved to model-a -- i.e. the test
          encoded the B1 defect as correct behaviour.

          POST-HOIST: the conflict is decided from harness state before a ticket is
          minted, so the driver is NEVER CALLED on attempt 2 and the session is never
          switched. The same reviewer/model-b record is now a TRUE description of
          handle, which is the only reason these assertions are sound.

        So what makes the surviving record correct is the HOIST, not retention after a
        switched session. `_stale_model_evidence()` deliberately does not run here,
        because a pre-selection refusal asked nothing of the session.
        """
        driver = drifting("model-b", "model-a")
        _recorder, harness = self.one_session_harness(driver)
        handle = harness.create_fake_terminal(
            "reviewer", "pass", iteration=1, phase="implementation"
        )
        harness.verify_model_identity(
            "task_rev", handle, role="reviewer", phase="implementation", attempt=1
        )
        with self.assertRaises(OrcaRuntimeError):
            harness.verify_model_identity(
                "task_wkr", handle, role="worker", phase="implementation", attempt=1
            )
        self.assertNotIn(("implementation", "worker"), harness._model_identity)
        self.assertEqual(
            harness._model_session_identity[handle][0], "reviewer"
        )

    def test_two_distinct_sessions_are_still_admitted(self) -> None:
        """The positive case OS-49 exists for is untouched: the refusal is about ONE
        session, not about a same-command pair."""
        driver = resolving_per_session("model-a", "model-b")
        recorder, harness = self.one_session_harness(driver)
        handles = {
            role: harness.create_fake_terminal(role, mode, iteration=1,
                                               phase="implementation")
            for role, mode in (("worker", "complete"), ("reviewer", "pass"))
        }
        self.assertNotEqual(handles["worker"], handles["reviewer"])
        for role, handle in handles.items():
            harness.verify_model_identity(
                "task_g", handle, role=role, phase="implementation", attempt=1
            )
        recorder.commands.clear()
        _dispatch, supervised = harness.start_worker(
            "task_g", handles["worker"], "spec", role="worker",
            phase="implementation", attempt=1,
        )
        self.assertTrue(supervised)


# ---- N1 -------------------------------------------------------------------------------

class EvidenceLifetimeTests(BarrierTestCase):
    """N1. Accepted model evidence must not outlive the delivery it was accepted for.

    Pre-fix failure mode: Gate B is `start_worker()`'s first statement and records on
    acceptance, and `start_worker()` had no rollback. So a refusal or failure anywhere after
    the barrier -- the own-handle refusal, a `worker-start` that never reached a ready
    worker, a failing `dispatch` -- left a verified record behind that pair admission and
    reuse condition 9 would later read as if the delivery had happened.

    Two harness shapes, because the exposure is different in each:

      * `solo_harness` routes a model-aware Worker on `claude` against a model-less
        Reviewer on `codex`. Independence holds on the commands alone, so there is no pair
        to admit and Gate B's own acceptance is the FIRST record for that identity -- which
        is the case where a surviving record has nothing legitimate underneath it.
      * `pair_harness` routes the same-command pair, where Gate B can only accept once a
        pre-pass record exists. There the correct rollback is a RESTORE, not a delete: the
        pre-pass succeeded on its own terms and `verify_model_identity()` is documented to
        grant admission without delivering, so destroying it would refuse a retry that
        should be allowed.
    """

    def solo_harness(self, **recorder_kwargs):
        recorder = SequentialTerminalExec(**recorder_kwargs)
        harness = self.build(
            recorder,
            routing=routing_from(CROSS_PHASE_PROFILE, "drift"),
            model_driver=resolving_per_session("model-a", "model-b"),
        )
        return recorder, harness

    def pair_harness(self, **recorder_kwargs):
        recorder = SequentialTerminalExec(**recorder_kwargs)
        harness = self.build(
            recorder,
            routing=routing_from(SPLIT_PROFILE, "split"),
            model_driver=resolving_per_session("model-a", "model-b"),
        )
        return recorder, harness

    @staticmethod
    def pair_handles(harness):
        return {
            role: harness.create_fake_terminal(role, mode, iteration=1,
                                               phase="implementation")
            for role, mode in (("worker", "complete"), ("reviewer", "pass"))
        }

    FAILED_START = {"worker-start": {"dispatchId": "ctx_1", "state": "failed",
                                     "stage": "compose", "failedStage": "compose",
                                     "lastError": "boom"}}

    MODEL_ROW_CELLS = ("resolved_model", "model_state", "model_request_method",
                       "model_request_evidence", "model_observed_at_dispatch")

    def test_a_failed_worker_start_leaves_no_new_evidence(self) -> None:
        recorder, harness = self.solo_harness(results=self.FAILED_START)
        handle = harness.create_fake_terminal(
            "worker", "complete", iteration=1, phase="implementation"
        )
        row_before = dict(harness.ledger_terminal(handle))
        with self.assertRaises(OrcaRuntimeError) as caught:
            harness.start_worker(
                "task_g", handle, "spec", role="worker", phase="implementation",
                attempt=1,
            )
        self.assertIn("did not reach a ready worker", str(caught.exception))
        # Nothing new is recorded anywhere the next decision reads.
        self.assertNotIn(("implementation", "worker"), harness._model_identity)
        self.assertNotIn(handle, harness._model_pending_evidence)
        self.assertNotIn(handle, harness._model_session_identity)
        row_after = harness.ledger_terminal(handle)
        for cell in self.MODEL_ROW_CELLS:
            with self.subTest(cell=cell):
                self.assertEqual(row_after[cell], row_before[cell])

    def test_the_failed_attempt_leaves_the_row_indistinguishable_from_no_attempt(
        self,
    ) -> None:
        """The behavioural consequence on the reuse gate, which reads the ROW rather than
        the maps -- and stated as an EQUIVALENCE rather than as a reason list, so it cannot
        accidentally assert a pre-existing row fact.

        Two terminals of the same model-aware routing: one has a Gate B acceptance followed
        by a failed `worker-start`, the other has had nothing attempted on it at all. Reuse
        condition 9 must say exactly the same thing about both. Pre-fix it could not: the
        failed one's row carried `model_state: verified` and a resolved model, so it reported
        `model_identity_stale` as an internally CONTRADICTORY row while the untouched one
        reported only `model_identity_unverified`.
        """
        from scripts.orca_runtime_harness import MODEL_IDENTITY_UNVERIFIED

        _recorder, harness = self.solo_harness(results=self.FAILED_START)
        failed = harness.create_fake_terminal(
            "worker", "complete", iteration=1, phase="implementation"
        )
        untouched = harness.create_fake_terminal(
            "worker", "complete", iteration=2, phase="implementation"
        )
        with self.assertRaises(OrcaRuntimeError):
            harness.start_worker(
                "task_g", failed, "spec", role="worker", phase="implementation",
                attempt=1,
            )

        def reasons(handle: str):
            return harness._model_identity_reuse_reasons(
                harness.ledger_terminal(handle), requested_model="alias-x",
                dispatch_id="ctx_1",
            )

        self.assertEqual(reasons(failed), reasons(untouched))
        self.assertIn(MODEL_IDENTITY_UNVERIFIED, reasons(failed))
        self.assertEqual(
            harness.ledger_terminal(failed)["model_state"],
            harness.ledger_terminal(untouched)["model_state"],
        )
        self.assertEqual(harness.ledger_terminal(failed)["resolved_model"], "")

    def test_a_failed_delivery_cannot_admit_its_counterpart_afterwards(self) -> None:
        """The reason the lifetime matters, asserted as behaviour rather than as state.

        Only the REVIEWER is pre-passed, so the Worker's own Gate B is the first record for
        `(implementation, worker)`. Its delivery then fails. Pre-fix that record survived and
        the Reviewer -- arriving second on the same command -- was admitted and DELIVERED on
        the strength of a Worker that never ran.
        """
        recorder, harness = self.pair_harness(results=self.FAILED_START)
        handles = self.pair_handles(harness)
        harness.verify_model_identity(
            "task_g", handles["reviewer"], role="reviewer", phase="implementation",
            attempt=1,
        )
        with self.assertRaises(OrcaRuntimeError) as caught:
            harness.start_worker(
                "task_g", handles["worker"], "spec", role="worker",
                phase="implementation", attempt=1,
            )
        self.assertIn("did not reach a ready worker", str(caught.exception))
        self.assertNotIn(("implementation", "worker"), harness._model_identity)
        recorder.commands.clear()
        with self.assertRaises(OrcaRuntimeError) as refused:
            harness.start_worker(
                "task_g", handles["reviewer"], "spec", role="reviewer",
                phase="implementation", attempt=1,
            )
        self.assertTrue(
            str(refused.exception).startswith("model_selection_pair_unadmitted"),
            str(refused.exception),
        )
        self.assertNothingDelivered(recorder)

    def test_a_successful_delivery_keeps_its_evidence(self) -> None:
        """The rollback is scoped to failure: a delivery that happened must still be able
        to admit its counterpart and to be read by reuse condition 9."""
        _recorder, harness = self.pair_harness()
        handles = self.pair_handles(harness)
        for role, handle in handles.items():
            harness.verify_model_identity(
                "task_g", handle, role=role, phase="implementation", attempt=1
            )
        harness.start_worker(
            "task_g", handles["worker"], "spec", role="worker",
            phase="implementation", attempt=1,
        )
        evidence = harness._model_identity[("implementation", "worker")]
        self.assertEqual(evidence.state, MODEL_EVIDENCE_VERIFIED)
        row = harness.ledger_terminal(handles["worker"])
        self.assertEqual(row["model_state"], MODEL_EVIDENCE_VERIFIED)
        self.assertEqual(row["resolved_model"], "model-a")
        self.assertEqual(row["model_observed_at_dispatch"], "ctx_1")

    def test_a_rolled_back_attempt_restores_an_earlier_pre_pass_record(self) -> None:
        """Snapshot/restore rather than delete: a separate, successful pre-pass legitimately
        earned its record, and a later failed DELIVERY must not destroy it -- that would
        refuse a retry that should be allowed. The attempt's own write is what is rolled
        back, and nothing wider."""
        _recorder, harness = self.pair_harness(results=self.FAILED_START)
        handles = self.pair_handles(harness)
        for role, handle in handles.items():
            harness.verify_model_identity(
                "task_g", handle, role=role, phase="implementation", attempt=1
            )
        before = harness._model_identity[("implementation", "worker")]
        with self.assertRaises(OrcaRuntimeError):
            harness.start_worker(
                "task_g", handles["worker"], "spec", role="worker",
                phase="implementation", attempt=1,
            )
        self.assertIs(harness._model_identity[("implementation", "worker")], before)
        self.assertIs(harness._model_session_identity[handles["worker"]][2], before)

    def test_the_own_handle_refusal_also_rolls_back(self) -> None:
        """The refusal that sits BETWEEN the barrier and every delivery command: it was the
        clearest case of an accepted model with nothing delivered."""
        recorder, harness = self.solo_harness()
        handle = harness.create_fake_terminal(
            "worker", "complete", iteration=1, phase="implementation"
        )
        recorder.commands.clear()
        with patch.dict(environ, {SELF_HANDLE_ENV: handle}):
            with self.assertRaises(OrcaRuntimeError) as caught:
                harness.start_worker(
                    "task_g", handle, "spec", role="worker", phase="implementation",
                    attempt=1,
                )
        self.assertIn("caller's own terminal", str(caught.exception))
        self.assertNotIn(("implementation", "worker"), harness._model_identity)
        self.assertNotIn(handle, harness._model_session_identity)
        self.assertNotIn(handle, harness._model_pending_evidence)
        self.assertNothingDelivered(recorder)

    def test_a_model_less_role_is_unaffected_by_the_rollback(self) -> None:
        """A legacy / model-less delivery writes no model evidence at all, so the rollback
        has nothing to restore and the failure path is byte-identical to before."""
        recorder = SequentialTerminalExec(results=self.FAILED_START)
        harness = self.build(recorder)
        handle = harness.create_fake_terminal("worker", "complete", iteration=1)
        with self.assertRaises(OrcaRuntimeError):
            harness.start_worker("task_g", handle, "spec")
        self.assertEqual(harness._model_identity, {})
        self.assertEqual(harness._model_session_identity, {})
        self.assertEqual(recorder.verbs, ["create", "wait", "worker-start"])


# ---- M6 -------------------------------------------------------------------------------

class ConstructionSeamTests(unittest.TestCase):
    """M6. The normal launcher construction path must be able to carry a model driver, and
    must still be fail-closed when it is not given one.

    Pre-fix failure mode: `model_driver` was an `OrcaRuntimeHarness.__init__` keyword and
    nothing else. No production construction passed one and neither `launcher` nor
    `orca_adapter` mentioned it, so the whole `routing -> capability -> Gate A -> harness ->
    Gate B` chain was reachable only when a test instantiated the harness by hand -- and
    Gate A could never admit a declared model through any door.
    """

    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.project = Path(self.temporary_directory.name)
        (self.project / ".orca").mkdir(parents=True)
        (self.project / ".orca" / "agent-profiles.yaml").write_text(
            SPLIT_PROFILE, encoding="utf-8"
        )
        binaries = self.project / "bin"
        binaries.mkdir()
        for command in ("claude", "codex"):
            shim = binaries / command
            shim.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            shim.chmod(0o755)
        patcher = patch.dict(os.environ, {"PATH": str(binaries)})
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def routing(self, *, model_driver=None):
        return launcher.orca_run_routing(
            agent_profile_name="split",
            requested_phases=("implementation",),
            risk="high",
            project_root=self.project,
            model_driver=model_driver,
        )

    def test_the_default_construction_still_refuses_a_declared_model(self) -> None:
        """The hard requirement: production passes no driver and stays fail-closed."""
        with self.assertRaises(launcher.LauncherError) as caught:
            self.routing()
        self.assertIn(REASON_MODEL_NOT_SUPPORTED, str(caught.exception))

    def test_a_reference_driver_admits_the_model_aware_routing_through_gate_a(self) -> None:
        routing = self.routing(model_driver=InProcessModelDriver())
        self.assertTrue(routing.is_model_aware)
        # Pending, not passed: Gate A has observed no resolved model, so the obligation is
        # still outstanding and the barrier is what discharges it.
        self.assertEqual(routing.pending_admission_phases(), ("implementation",))

    def test_a_driver_that_cannot_be_called_is_refused_at_gate_a(self) -> None:
        """Gate A reads the same derivation Gate B does, so an object that merely exists
        does not buy admission."""
        with self.assertRaises(launcher.LauncherError) as caught:
            self.routing(model_driver=BrokenDriverShapes.NotCallable())
        self.assertIn(REASON_MODEL_NOT_SUPPORTED, str(caught.exception))

    def test_build_orca_adapter_hands_one_driver_to_both_gates(self) -> None:
        """End to end through the REAL `build_orca_adapter`: the routing it validates and
        the harness it constructs receive the SAME driver object, and the harness then
        delivers a same-command model-aware pair through Gate B."""
        driver = InProcessModelDriver(
            resolve=lambda requested: requested      # alias == resolved, both verified
        )
        captured: dict = {}
        recorder = SequentialTerminalExec(
            results={
                "status": {"runtime": {"state": "ready", "appVersion": "unused",
                                       "runtimeId": "rt_seam"}},
                "current": {"worktree": {"id": "repo_seam::/p", "repoId": "repo_seam",
                                         "path": "/p"}},
                "run-create": {"run": {"id": "run_seam"}},
            }
        )

        def harness_factory(artifact_base, **kwargs):
            captured.update(kwargs)
            with patch.dict(os.environ, {"ORCA_CLI_COMMAND": "/opt/orca-dev"}):
                harness = OrcaRuntimeHarness(Path(artifact_base), **kwargs)
            harness._exec_orca = recorder
            harness.preflight = lambda: {}           # the raw `orca skills get` boundary
            captured["harness"] = harness
            return harness

        adapter, state = launcher.build_orca_adapter(
            {"thread_id": "seam", "phases": ["IMPLEMENTATION"], "risk": "high"},
            objective="OS-49 M6 construction seam",
            artifact_base=self.project,
            agent_profile_name="split",
            project_root=self.project,
            harness_factory=harness_factory,
            model_driver=driver,
        )
        self.assertEqual(state["run_id"], "run_seam")
        self.assertIs(captured["model_driver"], driver)
        harness = captured["harness"]
        self.assertIs(harness.model_driver, driver)
        self.assertTrue(harness.agent_routing.is_model_aware)
        self.assertIs(adapter.harness, harness)

        # ... and Gate B now runs on that harness, through the real barrier, with the real
        # routing Gate A admitted. Both sessions first, as a same-command pair requires.
        handles = {
            role: harness.create_fake_terminal(role, mode, iteration=1,
                                               phase="implementation")
            for role, mode in (("worker", "complete"), ("reviewer", "pass"))
        }
        for role, handle in handles.items():
            harness.verify_model_identity(
                "task_seam", handle, role=role, phase="implementation", attempt=1
            )
        _dispatch, supervised = harness.start_worker(
            "task_seam", handles["worker"], "spec", role="worker",
            phase="implementation", attempt=1,
        )
        self.assertTrue(supervised)
        self.assertEqual(
            harness.ledger_terminal(handles["worker"])["model_state"],
            MODEL_EVIDENCE_VERIFIED,
        )
        self.assertEqual(
            harness.ledger_terminal(handles["worker"])["resolved_model"], "glm-5.2"
        )

    def test_the_default_construction_passes_no_driver_keyword_at_all(self) -> None:
        """Byte-identical default: the keyword is OMITTED, not passed as None, so an
        existing `harness_factory` does not have to grow a parameter."""
        captured: dict = {}

        def harness_factory(artifact_base, **kwargs):
            captured["kwargs"] = dict(kwargs)
            raise launcher.LauncherError("STOP: construction reached, nothing else needed")

        (self.project / ".orca" / "agent-profiles.yaml").write_text(
            "version: 1\n"
            "profiles:\n"
            "  plain:\n"
            "    phases:\n"
            "      implementation:\n"
            "        worker: claude\n"
            "        reviewer: codex\n"
            "    final_review:\n"
            "      reviewer: codex\n",
            encoding="utf-8",
        )
        with self.assertRaises(launcher.LauncherError):
            launcher.build_orca_adapter(
                {"thread_id": "seam", "phases": ["IMPLEMENTATION"], "risk": "high"},
                objective="OS-49 M6 default construction",
                artifact_base=self.project,
                agent_profile_name="plain",
                project_root=self.project,
                harness_factory=harness_factory,
            )
        self.assertNotIn("model_driver", captured["kwargs"])

    def test_the_orca_adapter_still_declares_no_model_capability(self) -> None:
        """The seam does not make the production Orca adapter model-capable: it can neither
        request a selection nor observe a resolution, and OS-14 owns that."""
        from scripts.deterministic_workflow.orca_adapter import OrcaAdapter

        adapter = OrcaAdapter.__new__(OrcaAdapter)
        adapter.settlement_journal = None
        adapter.approval_port = None
        self.assertNotIn(
            MODEL_SELECTION_VERIFIED_CAPABILITY, adapter.capabilities()
        )


if __name__ == "__main__":
    unittest.main()
