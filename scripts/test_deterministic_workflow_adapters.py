from __future__ import annotations

import ast
import importlib
import importlib.metadata
import sys
import unittest
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from scripts.deterministic_workflow.contracts import BASE_CAPABILITIES, make_intent
from scripts.deterministic_workflow.fake_adapter import FakeAdapter, FakeArtifactStore, IdempotencyConflict
from scripts.deterministic_workflow.migration import normalize_trace
from scripts.deterministic_workflow.orca_adapter import OrcaAdapter
from scripts.deterministic_workflow.state import initial_state


def _langgraph_ok() -> bool:
    try:
        import langgraph  # noqa: F401
        import langgraph.graph  # noqa: F401
    except ImportError:
        return False
    try:
        return importlib.metadata.version("langgraph") == "0.2.76"
    except importlib.metadata.PackageNotFoundError:
        return False


class AdapterTests(unittest.TestCase):
    def test_replayed_intent_does_not_duplicate_effect(self):
        adapter=FakeAdapter([{"status":"COMPLETE","unit_test_status":"NOT_APPLICABLE"}])
        state=initial_state(run_id="run_replay",thread_id="t",phases=("ANALYSIS",),capabilities=BASE_CAPABILITIES)
        intent=make_intent(state,"WORKER","PHASE_GATE")
        first=adapter.start(intent); second=adapter.start(intent)
        self.assertEqual(first,second); self.assertEqual(adapter.effect_count,1); self.assertEqual(adapter.settlement(intent["intent_id"]),adapter.settlement(intent["intent_id"]))

    def test_same_identity_different_payload_is_conflict(self):
        adapter=FakeAdapter([{}]); state=initial_state(run_id="run_conflict",thread_id="t",phases=("ANALYSIS",),capabilities=BASE_CAPABILITIES); intent=make_intent(state,"WORKER","PHASE_GATE"); adapter.start(intent)
        changed=dict(intent); changed["payload_digest"]="different"
        with self.assertRaises(IdempotencyConflict): adapter.start(changed)

    def test_artifact_replay_is_idempotent_and_conflict_is_rejected(self):
        state=initial_state(run_id="run_artifact",thread_id="t",phases=("ANALYSIS",),capabilities=BASE_CAPABILITIES)
        intent=make_intent(state,"WORKER","PHASE_GATE"); store=FakeArtifactStore()
        first=store.put(intent,b"evidence"); second=store.put(intent,b"evidence")
        self.assertEqual(first,second); self.assertEqual(len(store.items),1)
        with self.assertRaises(IdempotencyConflict): store.put(intent,b"different")

    def test_core_checkpoint_modules_have_no_runtime_specific_imports_or_fields(self):
        root=Path(__file__).parent/"deterministic_workflow"
        for name in ("contracts.py","state.py","routing.py","graph_spec.py","ports.py","executor.py","graph.py"):
            tree=ast.parse((root/name).read_text())
            imports=[n for n in ast.walk(tree) if isinstance(n,(ast.Import,ast.ImportFrom))]
            text=" ".join((getattr(n,"module","") or "")+" "+" ".join(a.name for a in n.names) for n in imports)
            self.assertNotIn("orca",text.lower()); self.assertNotIn("subprocess",text.lower())

    def test_trace_comparator_detects_mutation(self):
        trace=[{"sequence":0,"node":"ROUTE","route":"COMPLETE","phase":"ANALYSIS"}]
        mutated=[dict(trace[0],route="BLOCK")]
        self.assertNotEqual(normalize_trace(trace),normalize_trace(mutated))


@unittest.skipUnless(_langgraph_ok(), "requires pinned langgraph 0.2.76")
class LangGraphAdapterParityTests(unittest.TestCase):
    def test_fake_and_orca_adapter_have_identical_graph_trace(self):
        from scripts.deterministic_workflow.graph import build_graph
        class OfflineHarness:
            def __init__(self, results): self.results=list(results); self.calls=[]
            def create_task(self, spec, *, deps=()):
                self.calls.append(("create_task",spec,deps)); return f"task_{len(self.calls)}"
            def run_existing_task(self, role, iteration, mode, task_id, *, phase=None,
                                  spec=None, round_kind="phase_gate", **kwargs):
                self.calls.append(("run_existing_task",role,iteration,mode,task_id,phase,round_kind))
                return SimpleNamespace(body=json.dumps(self.results.pop(0)),dispatch_id=f"dispatch_{len(self.calls)}"), "term_offline"
            def task_status(self, task_id): return "completed"
            def call(self, *args, **kwargs): return {"ok":True,"args":args}
        results=[{"status":"COMPLETE","unit_test_status":"NOT_APPLICABLE"},
                 {"result":"PASS","review_verdict":"PASS","findings":[]},
                 {"result":"PASS","review_verdict":"PASS","findings":[]}]
        state=initial_state(run_id="run_parity",thread_id="t",phases=("ANALYSIS",),capabilities=BASE_CAPABILITIES)
        fake=FakeAdapter(results); fake_out=build_graph(fake).invoke(state)
        harness=OfflineHarness(results); orca=OrcaAdapter(harness)
        orca_out=build_graph(orca).invoke(state)
        self.assertEqual(normalize_trace(fake_out["logical_trace"]),normalize_trace(orca_out["logical_trace"]))
        self.assertTrue(harness.calls)
        self.assertEqual({call[0] for call in harness.calls},{"create_task","run_existing_task"})


class LangGraphGuardTests(unittest.TestCase):
    def test_guard_is_import_based_for_blocked_import(self):
        module=importlib.import_module("scripts.test_deterministic_workflow_graph")
        real_import=__import__
        def blocked(name,*args,**kwargs):
            if name=="langgraph" or name.startswith("langgraph."): raise ImportError("blocked")
            return real_import(name,*args,**kwargs)
        with patch("builtins.__import__",side_effect=blocked): self.assertFalse(module._langgraph_ok())


if __name__ == "__main__": unittest.main()
