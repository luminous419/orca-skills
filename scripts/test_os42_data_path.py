"""OS-42: the end-to-end data path, ingress and egress.

The generated contract has to reach the engine Worker through the REAL path --
`OrcaAdapter.start` -> `run_existing_task` -> `dispatch_context` -> `render_task_spec` ->
`start_worker` -- and the gate output has to come back through the closed
`result["gate"]` envelope. These tests assert on the strings and dictionaries those
functions actually produce, not on a detached helper.
"""
from __future__ import annotations

import ast
import json
import unittest
from pathlib import Path

from scripts import decision_contract, task_context
from scripts.deterministic_workflow import orca_adapter
from scripts.deterministic_workflow.contracts import (BASE_CAPABILITIES,
                                                      EventValidationError,
                                                      GATE_ENVELOPE_KEYS, make_intent,
                                                      make_settlement_event,
                                                      settlement_digest, validate_event)
from scripts.deterministic_workflow.state import initial_state
from scripts.orca_runtime_harness import dispatch_context

REPO_ROOT = Path(__file__).resolve().parents[1]

OS42_DEFECT_BODY = """# Worker Result

STATUS: COMPLETE
DECISION_GATE_STATE: CLEAR

```decision-gate
{"ledger_schema_version": 1, "boundary": "B2", "source": "worker", "role": "worker",
 "run": "run_os42", "phase": "ANALYSIS", "iteration": 1,
 "responsible_phase": "ANALYSIS", "state": "CLEAR", "reason_code": null,
 "open_decision_item": false, "open_item": null, "assumption": null, "evidence": {},
 "verdict": "", "source_binding": "artifacts/runs/run_os42/",
 "recorded_at": "2026-01-01T00:00:00+00:00", "prior_open_decision_items": [],
 "reversibility": "Fully reversible: this phase wrote exactly one new artifact"}
```
"""


class _Attempt:
    def __init__(self, body: str) -> None:
        self.body = body
        self.dispatch_id = "ctx_stub"


class _StubHarness:
    """Records what `OrcaAdapter.start` actually hands to each primitive."""

    def __init__(self, body: str = "{}") -> None:
        self.created_specs: list[str] = []
        self.run_calls: list[dict] = []
        self.body = body

    def create_task(self, spec: str, *, deps=()) -> str:
        self.created_specs.append(spec)
        return "task_stub"

    def run_existing_task(self, role, iteration, mode, task_id, **kwargs):
        self.run_calls.append({"role": role, "iteration": iteration, "mode": mode,
                               "task_id": task_id, **kwargs})
        return _Attempt(self.body), "term_stub"


def an_intent(role="WORKER", **overrides):
    state = dict(initial_state(run_id="run_os42", thread_id="t", phases=("ANALYSIS",),
                               capabilities=frozenset(BASE_CAPABILITIES), risk="high"))
    state.update(overrides)
    return make_intent(state, role, "PHASE_GATE")


class IngressTests(unittest.TestCase):
    """The generated contract must be in the string the AGENT reads."""

    def setUp(self) -> None:
        self.projection = decision_contract.contract_projection(
            decision_contract.resolve_policy())

    def test_dispatched_spec_contains_the_generated_enum_values(self) -> None:
        """On the real render site. Catches an ingress that renders the block into some
        other string, or a stale copy."""
        spec, boundary, _ = dispatch_context(
            "phase_worker", 1, "complete", phase="analysis", base_spec="do the thing",
            run_id="run_os42")
        self.assertIn(task_context.DECISION_GATE_SPEC_HEADER, spec)
        for token in self.projection.enum_tokens():
            self.assertIn(token, spec, f"the dispatched spec omits {token!r}")
        self.assertEqual(boundary["artifact_contract"],
                         "artifacts/runs/run_os42/ANALYSIS.md")

    def test_an_ordinary_dispatch_carries_no_repair_block(self) -> None:
        spec, _, _ = dispatch_context(
            "phase_worker", 1, "complete", phase="analysis", base_spec="x",
            run_id="run_os42")
        self.assertNotIn(task_context.VALIDATION_REPAIR_SPEC_HEADER, spec)

    def test_a_repair_dispatch_carries_the_exact_defect_payload(self) -> None:
        """The pair the finding asks for: the repair prompt contains the error, the field
        path, the actual value and the COMPLETE allowed set; the ordinary one does not."""
        instruction = {
            "attempt": 1, "max_attempts": 2,
            "defects": [{"code": "DECISION_GATE_INPUT_MALFORMED", "kind": "FORM",
                         "field_path": "reversibility",
                         "expected": ["reversible_in_run", "reversible_with_effort",
                                      "irreversible"],
                         "actual": "'Fully reversible: ...'",
                         "message": "boundary element 'reversibility' declares ..."}],
        }
        spec, _, _ = dispatch_context(
            "phase_worker", 1, "complete", phase="analysis", base_spec="x",
            run_id="run_os42", repair_instruction=instruction)
        self.assertIn(task_context.VALIDATION_REPAIR_SPEC_HEADER, spec)
        self.assertIn("reversibility", spec)
        self.assertIn("Fully reversible", spec)
        for token in ("reversible_in_run", "reversible_with_effort", "irreversible"):
            self.assertIn(token, spec)

    def test_render_task_spec_omitting_the_new_blocks_is_byte_identical(self) -> None:
        """Catches a change that makes an existing caller render differently."""
        boundary = task_context.build_task_boundary(
            current_role="worker", current_phase="analysis", current_iteration=1,
            artifact_contract="artifacts/runs/run_os42/ANALYSIS.md")
        without = task_context.render_task_spec("base", boundary)
        explicit_none = task_context.render_task_spec(
            "base", boundary, None, None, None, None, None, None)
        self.assertEqual(without, explicit_none)


class AdapterTests(unittest.TestCase):
    def test_create_task_spec_stays_canonical_intent_json(self) -> None:
        """The other half of the ingress. Catches injecting the contract into the LOOKUP
        spec, which would break `_intent_id_of_spec` and the external_lookup rung."""
        harness = _StubHarness()
        adapter = orca_adapter.OrcaAdapter(harness)
        intent = an_intent()
        adapter.start(intent)
        spec = harness.created_specs[0]
        self.assertEqual(json.loads(spec)["intent_id"], intent["intent_id"])
        self.assertNotIn(task_context.DECISION_GATE_SPEC_HEADER, spec)

    def test_the_adapter_reads_the_derived_gate_iteration(self) -> None:
        """Catches recomputing `phase_iteration + 1` beside the field."""
        harness = _StubHarness()
        adapter = orca_adapter.OrcaAdapter(harness)
        intent = an_intent()
        adapter.start(intent)
        self.assertEqual(harness.run_calls[0]["iteration"], intent["gate_iteration"])
        self.assertEqual(intent["gate_iteration"], 1)

    def test_the_adapter_forwards_the_repair_instruction(self) -> None:
        harness = _StubHarness()
        adapter = orca_adapter.OrcaAdapter(harness)
        adapter.start(an_intent())
        self.assertIn("repair_instruction", harness.run_calls[0])

    def test_adapter_settlement_reaches_the_classifier(self) -> None:
        """The verbatim OS-42 body, all the way through the adapter's default parser
        into the classifier. Catches a parser that raises instead of transporting."""
        harness = _StubHarness(body=OS42_DEFECT_BODY)
        adapter = orca_adapter.OrcaAdapter(harness)
        intent = an_intent()
        adapter.start(intent)
        event = adapter.settlement(intent["intent_id"])
        gate = event["result"]["gate"]
        self.assertEqual(set(gate), set(GATE_ENVELOPE_KEYS))
        self.assertIn("Fully reversible", gate["record"]["reversibility"])
        policy = decision_contract.resolve_policy()
        defects = decision_contract.classify_gate(policy, gate, role="WORKER")
        self.assertEqual(len(defects), 1)
        self.assertEqual(defects[0].kind, "FORM")
        self.assertEqual(defects[0].field_path, "reversibility")


class DigestTests(unittest.TestCase):
    def test_gate_field_is_covered_by_the_settlement_digest(self) -> None:
        """Mutating one character inside the gate changes the settlement digest and the
        event id, while intent identity is untouched. Catches a `gate` smuggled outside
        `result` (which would leave it unsigned) and any leak of result content into
        intent identity."""
        intent = an_intent()
        base = {"status": "COMPLETE", "gate": {"declared_state": "CLEAR",
                                               "declaration_count": 1, "fence_count": 1,
                                               "record": {"a": 1}, "record_text": None,
                                               "truncated": False}}
        mutated = json.loads(json.dumps(base))
        mutated["gate"]["record"]["a"] = 2
        self.assertNotEqual(settlement_digest(intent, base),
                            settlement_digest(intent, mutated))
        first = make_settlement_event(intent, base, occurred_at="1970-01-01T00:00:00Z")
        second = make_settlement_event(intent, mutated, occurred_at="1970-01-01T00:00:00Z")
        self.assertNotEqual(first["event_id"], second["event_id"])
        # intent identity is a function of the INTENT only
        self.assertEqual(intent["intent_id"], an_intent()["intent_id"])


class EnvelopeShapeTests(unittest.TestCase):
    def make_event(self, gate):
        intent = an_intent()
        result = {"status": "COMPLETE", "gate": gate}
        return intent, make_settlement_event(intent, result,
                                             occurred_at="1970-01-01T00:00:00Z")

    def test_a_malformed_envelope_is_an_event_integrity_failure(self) -> None:
        """The envelope is the ADAPTER's product, so a broken one is never repairable."""
        intent, event = self.make_event({"declared_state": "CLEAR"})
        with self.assertRaises(EventValidationError) as caught:
            validate_event(intent, event)
        self.assertEqual(caught.exception.code, "MALFORMED_EVENT")

    def test_a_wrong_typed_envelope_field_is_rejected(self) -> None:
        gate = {"declared_state": "CLEAR", "declaration_count": "1", "fence_count": 1,
                "record": None, "record_text": None, "truncated": False}
        intent, event = self.make_event(gate)
        with self.assertRaises(EventValidationError):
            validate_event(intent, event)

    def test_an_absent_gate_is_legal_at_the_event_layer(self) -> None:
        """"The agent said nothing" must reach the classifier as a repairable FORM
        defect, never be disguised as an integrity failure."""
        intent = an_intent()
        event = make_settlement_event(intent, {"status": "COMPLETE"},
                                      occurred_at="1970-01-01T00:00:00Z")
        validate_event(intent, event)     # must not raise

    def test_the_two_envelope_key_tuples_agree(self) -> None:
        """`contracts.py` duplicates the tuple to stay free of tools/ imports; this is
        what keeps the two copies equal."""
        self.assertEqual(GATE_ENVELOPE_KEYS, decision_contract.GATE_ENVELOPE_KEYS)


class ImportDirectionTests(unittest.TestCase):
    def test_executor_module_imports_no_tools_sibling(self) -> None:
        """`executor.py` is inside the shipped engine package; a module-scope import of a
        tools/ sibling would make the whole package unimportable when it is absent."""
        source = (REPO_ROOT / "scripts" / "deterministic_workflow" / "executor.py"
                  ).read_text(encoding="utf-8")
        tree = ast.parse(source)
        for node in tree.body:                      # module scope only
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self.assertNotIn("decision_contract", alias.name)
            elif isinstance(node, ast.ImportFrom):
                self.assertNotIn("decision_contract", node.module or "")

    def test_decision_gate_still_imports_only_the_policy(self) -> None:
        """OS-42 must not have grown `decision_gate` an outward edge."""
        source = (REPO_ROOT / "scripts" / "decision_gate.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        local = {p.stem for p in (REPO_ROOT / "scripts").glob("*.py")}
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.replace("scripts.", "").split(".")[0])
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    imported.add(alias.name.replace("scripts.", "").split(".")[0])
        self.assertEqual(imported & local, {"decision_policy"})


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
