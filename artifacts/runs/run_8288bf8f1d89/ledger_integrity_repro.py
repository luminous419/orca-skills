"""C2-001 correction round: the six ledger-integrity holes, reproduced end to end.

Builds a *valid* v2 ledger holding exactly one record, tampers with that single record,
then asks ``FileRuntimeStateStore.claim()`` for the matching intent.  Before the fix every
case is ACCEPTED with ``claim_outcome=RESUMED``; after the fix every case must fail closed
with a ``RuntimeStateCorrupt`` / ``RuntimeStateConflict``.

    python3 artifacts/runs/run_8288bf8f1d89/ledger_integrity_repro.py
"""
import json
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))

from scripts.deterministic_workflow.contracts import BASE_CAPABILITIES, make_intent  # noqa: E402
from scripts.deterministic_workflow.state import initial_state  # noqa: E402
from scripts.deterministic_workflow import runtime_state as rs  # noqa: E402


def intent(run_id="run_repro", phase="ANALYSIS", role="WORKER"):
    state = dict(initial_state(run_id=run_id, thread_id="t", phases=(phase,),
                               capabilities=BASE_CAPABILITIES))
    state["pending_role"] = role
    return make_intent(state, role, "PHASE")


def record_for(it, **overrides):
    record = {
        "intent_id": it["intent_id"], "command_id": it["command_id"],
        "payload_digest": it["payload_digest"], "run_id": it["run_id"],
        "phase": it["phase"], "role": it["role"], "round_kind": it["round_kind"],
        "status": "EFFECTED", "receipt": {"task_id": "task_1"}, "settlement": None,
        "owner_id": "coordinator_A", "lease_token": "tok", "lease_expires_at": 0.0,
        "last_heartbeat_at": 0.0,
    }
    record.update(overrides)
    return record


CASES = [
    ("empty receipt {}", dict(receipt={})),
    ("unknown receipt key", dict(receipt={"task_id": "task_1", "smuggled": "x"})),
    ("conflicting run_id", dict(run_id="run_other")),
    ("conflicting phase", dict(phase="IMPLEMENTATION")),
    ("conflicting role", dict(role="PHASE_REVIEWER")),
    ("conflicting command_id", dict(command_id="cmd_other")),
]


def main():
    it = intent()
    worst = 0
    for label, overrides in CASES:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ledger.json"
            record = record_for(it, **overrides)
            path.write_text(json.dumps({
                "schema_version": rs.RUNTIME_STATE_SCHEMA_VERSION,
                "records": {it["intent_id"]: record},
            }), encoding="utf-8")
            try:
                out = rs.FileRuntimeStateStore(path).claim(it)
            except rs.RuntimeStateConflict as exc:
                print(f"{label:<26} -> REFUSED   {type(exc).__name__}: {exc}")
            else:
                worst = 1
                print(f"{label:<26} -> ACCEPTED  outcome={out['claim_outcome']}")
    return worst


if __name__ == "__main__":
    sys.exit(main())
