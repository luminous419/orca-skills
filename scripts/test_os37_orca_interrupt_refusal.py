"""OS-37 WI-02 / DECISION D-1 (W-1-C).  ``OrcaAdapter.interrupt`` refuses BY NAME.

The old body called ``orca orchestration worker-interrupt``.  No such verb exists at the
pinned revision -- the spec defines exactly eight ``worker-*`` verbs and none is an
interrupt -- so the only thing that call could ever produce was an unnamed CLI failure.

Whether such a verb existed in an EARLIER release was never investigated.  That is U8, and
this test asserts the refusal, not the history: the claim under test is "absent at the
pinned revision", never "never existed".

Two assertions, because the returned member alone would not be enough: the outcome must be
the contract's own ``unsupported`` member, AND no CLI verb may be invoked -- proven through
the harness's single subprocess seam, which records every call.
"""
from __future__ import annotations

import unittest

from scripts.deterministic_workflow import orca_adapter as adapter_mod
from scripts.deterministic_workflow.standalone_lifecycle import INTERRUPT_OUTCOMES


class _RecordingHarness:
    """The single subprocess seam.  Every ``call`` is recorded and none is executed.

    A spy rather than a mock: the test's claim is that ZERO verbs were invoked, and only
    something that would have recorded a call can establish that.
    """

    run_id = "run_os37"

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def call(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        raise AssertionError(
            "OrcaAdapter.interrupt invoked a CLI verb; no Orca primitive expresses a "
            f"non-settling interrupt at the pinned revision, so {args!r} cannot exist")

    def task_status(self, task_id):
        self.calls.append((("task_status", task_id), {}))
        return {"status": "RUNNING"}


class OrcaInterruptRefusalTests(unittest.TestCase):

    def setUp(self) -> None:
        self.harness = _RecordingHarness()
        self.adapter = adapter_mod.OrcaAdapter(self.harness)
        self.adapter._receipts["intent-1"] = {
            "intent_id": "intent-1", "task_id": "task-1", "dispatch_id": "dispatch-1",
            "terminal": "term-1", "payload_digest": "d"}

    def test_interrupt_returns_the_contracts_named_unsupported_member(self) -> None:
        result = self.adapter.interrupt("intent-1", "operator asked")
        self.assertEqual(result["interrupt_outcome"], "unsupported")
        self.assertIn(
            result["interrupt_outcome"], INTERRUPT_OUTCOMES,
            "the refusal must be a member of the closed interrupt_outcome vocabulary, so a "
            "caller has to handle it rather than reading it as a silent no-op")
        self.assertEqual(result["refusal"], adapter_mod.ORCA_INTERRUPT_PRIMITIVE_ABSENT)
        self.assertEqual(result["intent_id"], "intent-1")
        self.assertEqual(result["reason"], "operator asked")
        self.assertEqual(result["dispatch_id"], "dispatch-1")

    def test_interrupt_invokes_no_orca_cli_verb(self) -> None:
        """ZERO calls through the subprocess seam.  This is the half a return value cannot prove."""
        self.adapter.interrupt("intent-1", "operator asked")
        self.assertEqual(
            self.harness.calls, [],
            "interrupt reached the Orca CLI; it must refuse without invoking any verb, "
            "because the verb it used to invoke does not exist")

    def test_interrupt_settles_nothing(self) -> None:
        """Interrupting is not settling.  The refusal must leave the ledger untouched."""
        before = dict(self.adapter._events)
        self.adapter.interrupt("intent-1", "operator asked")
        self.assertEqual(self.adapter._events, before)
        self.assertIsNone(self.adapter.settlement("intent-1"))

    def test_primitive_map_no_longer_asserts_a_nonexistent_primitive(self) -> None:
        """``ORCA_PRIMITIVE_MAP["interrupt"]`` is EMPTY.

        The map was the second place in the module asserting the verb existed.  Correcting
        the body and leaving the map would have left the claim in the repository.
        """
        self.assertEqual(adapter_mod.ORCA_PRIMITIVE_MAP["interrupt"], ())
        for method in ("start", "send", "status"):
            self.assertTrue(
                adapter_mod.ORCA_PRIMITIVE_MAP[method],
                f"{method}'s primitives were not touched by this change")

    def test_agent_interrupt_is_still_declared_and_base_capabilities_unchanged(self) -> None:
        """D-1: ``agent_interrupt`` stays DECLARED, and ``BASE_CAPABILITIES`` is unedited.

        The residual honesty gap -- ``OrcaAdapter`` declares ``agent_interrupt`` while its
        ``interrupt()`` refuses -- is NOT claimed closed.  It is bounded: ``agent_interrupt``
        has no engine call site at all, and the refusal is now named and tested.  Removing
        the declaration instead would trip ``validate_node``'s ``BASE_CAPABILITIES`` gate and
        BLOCK every existing Orca run, which is a far worse outcome than a named residual.
        """
        from scripts.deterministic_workflow.contracts import BASE_CAPABILITIES
        self.assertIn("agent_interrupt", BASE_CAPABILITIES)
        self.assertIn("agent_interrupt", self.adapter.capabilities())
        self.assertEqual(
            BASE_CAPABILITIES,
            frozenset({"agent_start", "agent_command", "agent_status", "agent_interrupt",
                       "settlement", "idempotent_intent", "artifact_immutable",
                       "checkpoint"}),
            "BASE_CAPABILITIES must not change: test_deterministic_workflow_graph builds "
            "BASE_CAPABILITIES - {'agent_interrupt'} to prove the validate gate fires")

    def test_external_resume_is_still_not_declared_by_the_orca_adapter(self) -> None:
        """CONFLICT-1: the Orca ``external_resume`` reasoning is untouched.

        Four existing test modules depend on the Orca adapter NOT declaring it.  OS-37 adds
        a standalone adapter that does; it does not change what the Orca one honestly can.
        """
        from scripts.deterministic_workflow.contracts import EXTERNAL_RESUME
        self.assertNotIn(EXTERNAL_RESUME, self.adapter.capabilities())


if __name__ == "__main__":
    unittest.main()
