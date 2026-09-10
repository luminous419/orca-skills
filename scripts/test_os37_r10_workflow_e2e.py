"""OS-37 R10 / WI-17 / required implementation 14 -- the WORKFLOW-level Orca-free E2E.

`test_os37_standalone_e2e.py` proves the standalone RUNTIME end to end: one dispatch, spawn
to validated settlement, against a real spawn.  It does not drive the deterministic workflow
GRAPH, so it cannot show a Worker -> Reviewer -> correction -> fresh Final Review loop.
This module does exactly that, and it does it by RUNNING the repository's real
`run_workflow.py --adapter standalone` in a process whose `PATH` cannot resolve `orca` and
whose environment carries no `ORCA_*` marker.

The harness is `artifacts/runs/run_54d90086bd75/evidence/r10_standalone_e2e.sh`, kept as a
run artefact rather than inlined here so the evidence a reader inspects is the same script
CI executes.  It refuses to start (exit 90) if `orca` is still resolvable, so a run that
produced the right artefacts while quietly reaching an Orca binary cannot be mistaken for a
pass.

Skip-gated: it spawns nine real local agent processes and takes minutes.  Declared in
`scripts/tolerated_skip_manifest.txt` with this exact reason.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from scripts import os37_r10_fixture as r10_fixture

REPO = Path(__file__).resolve().parent.parent
HARNESS = (REPO / "artifacts" / "runs" / "run_54d90086bd75" / "evidence"
           / "r10_standalone_e2e.sh")

#: Gated on an env var nothing sets, and declared in `scripts/tolerated_skip_manifest.txt`
#: with this exact reason.
R10_REASON = "requires ORCA_OS37_E2E=1; the R10 workflow E2E drives real local agent processes"
R10_ENABLED = os.environ.get("ORCA_OS37_E2E") == "1"


@unittest.skipUnless(R10_ENABLED, R10_REASON)
class R10WorkflowE2ETests(unittest.TestCase):
    """One real Orca-free run of the workflow graph, asserted from its own evidence."""

    @classmethod
    def setUpClass(cls) -> None:
        if r10_fixture.native_agent_dir() is None:
            raise unittest.SkipTest(r10_fixture.NO_COMPILER_REASON)
        cls.out = Path(tempfile.mkdtemp(prefix="os37-r10-"))
        cls.completed = subprocess.run(
            ["/bin/bash", str(HARNESS), str(cls.out)],
            capture_output=True, text=True, check=False, timeout=1800)

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.out, ignore_errors=True)

    # -- the precondition, first.  A failure here is "not established", never a pass. ----
    def test_orca_was_not_resolvable_in_the_run(self) -> None:
        text = (self.out / "preconditions.txt").read_text()
        self.assertIn("command -v orca   : ''", text,
                      "`orca` was still resolvable; the E2E precondition is NOT established")
        self.assertIn("ORCA_* in env     : '0'", text)
        self.assertNotEqual(self.completed.returncode, 90,
                            "the harness aborted on its own precondition gate")

    def test_the_agent_binary_was_a_real_native_executable(self) -> None:
        """R-A leg 4 is an executable-IMAGE identity; a script fixture cannot stand in."""
        text = (self.out / "preconditions.txt").read_text()
        self.assertRegex(text, r"file\(1\).*: .*(Mach-O|ELF).* executable")

    # -- the run itself ------------------------------------------------------------------
    def test_the_workflow_reached_completed(self) -> None:
        self.assertEqual(self.completed.returncode, 0,
                         (self.out / "run_stderr.txt").read_text()[-3000:])
        summary = json.loads((self.out / "run_stdout.json").read_text())
        self.assertEqual(summary["terminal_status"], "COMPLETED")
        self.assertEqual(summary["terminal_reason"]["code"], "WORKFLOW_COMPLETED")
        self.assertEqual(summary["run_lifecycle"], "SETTLED")
        self.assertGreaterEqual(summary["final_review_iterations"], 1,
                                "no Final Review round ran")

    def test_the_loop_really_was_worker_reviewer_correction_final_review(self) -> None:
        """The journal, not the summary, is the evidence: it records every settlement.

        A run that merely COMPLETED could have passed every review on the first look.  The
        correction leg is what R10 asks for, so it is asserted as a SHAPE: at least one
        phase Reviewer returned FAIL, a further Worker settlement followed it in the same
        phase, a later Reviewer returned PASS, and a Final Review round settled after that.
        """
        settlements = self._settlements()
        roles = [(row["role"], row["phase"], row["verdict"]) for row in settlements]

        failures = [i for i, (role, phase, verdict) in enumerate(roles)
                    if role == "reviewer" and phase != "final_review" and verdict == "FAIL"]
        self.assertTrue(failures, f"no phase Reviewer ever returned FAIL: {roles}")
        first_fail = failures[0]

        corrections = [i for i, (role, _, _) in enumerate(roles[first_fail + 1:],
                                                          start=first_fail + 1)
                       if role == "worker"]
        self.assertTrue(corrections,
                        f"a Reviewer returned FAIL and no correction Worker followed: {roles}")

        later_pass = [i for i, (role, phase, verdict) in enumerate(roles)
                      if role == "reviewer" and phase != "final_review"
                      and verdict == "PASS" and i > corrections[0]]
        self.assertTrue(later_pass, f"the correction was never accepted: {roles}")

        finals = [i for i, (_, phase, verdict) in enumerate(roles)
                  if phase == "final_review" and verdict == "PASS"]
        self.assertTrue(finals, f"no Final Review settled PASS: {roles}")
        self.assertGreater(finals[-1], later_pass[-1],
                           "the Final Review did not run AFTER the corrected phase passed")

    def test_every_dispatch_walked_the_whole_lifecycle_in_order(self) -> None:
        """Per intent: spawned -> identity_bound -> readiness -> delivery -> settlement.

        Nothing here is a description of the runtime; it is read out of the append-only
        journal the run actually wrote.
        """
        rows = self._journal_rows()
        by_intent: dict[str, list[tuple[str, str]]] = {}
        for row in rows:
            by_intent.setdefault(row["intent_id"], []).append((row["kind"], row["event"]))
        self.assertTrue(by_intent, "the run wrote no journal at all")
        for intent_id, entries in by_intent.items():
            with self.subTest(intent=intent_id):
                # `DELIVERY_INTENT` is FIRST and carries NO event, and both facts are
                # required rather than incidental.  D4.3a: the spawn request and the prompt
                # digest are journalled as ONE record BEFORE the process exists, so its
                # absence proves no `fork` happened.  It carries no event because it is an
                # OBSERVATION and not a lifecycle transition -- it is not a claim, grants no
                # exclusivity and settles nothing (AC-37-20).
                self.assertEqual(
                    entries,
                    [("DELIVERY_INTENT", ""),
                     ("EVENT", "spawned"),
                     ("SPAWN_OBSERVED", "identity_bound"),
                     ("RECEIPT_OBSERVED", "identity_bound"),
                     ("EVENT", "readiness_observed"),
                     ("EVENT", "delivery_proof_observed"),
                     ("SETTLEMENT_OBSERVED", "settlement_confirmed")],
                    "a dispatch did not walk the lifecycle in order")
                intent_rows = [r for r in rows
                               if r["intent_id"] == intent_id
                               and r["kind"] == "DELIVERY_INTENT"]
                self.assertEqual(len(intent_rows), 1,
                                 "a dispatch journalled more than one delivery intent")
                vocabulary = intent_rows[0]["source_vocabulary"]
                self.assertTrue(vocabulary["prompt_digest"],
                                "the delivery intent carries no prompt digest")
                self.assertTrue(vocabulary["argv_digest"],
                                "the delivery intent carries no argv digest")
                self.assertIn(vocabulary["delivery_mode"],
                              ("launch_with_prompt", "post_ready_delivery"))

    def test_the_readiness_quorum_closed_on_all_three_legs_every_time(self) -> None:
        """READY never rested on text: R-A, R-B and R-C are each recorded true."""
        rows = [r for r in self._journal_rows() if r["event"] == "readiness_observed"]
        self.assertTrue(rows, "no readiness was ever observed")
        for row in rows:
            quorum = row["source_vocabulary"]["quorum"]
            self.assertEqual(quorum, {"R-A": True, "R-B": True, "R-C": True})

    def test_the_run_is_requeryable_from_a_stranger_process(self) -> None:
        """R6.  The Coordinator's turn ended when the harness exited; the state survives."""
        journal = self._journal_path()
        probe = subprocess.run(
            [os.sys.executable, "-c",
             "import json,sys;"
             "rows=[json.loads(l) for l in open(sys.argv[1]) if l.strip()];"
             "print(len(rows), sum(1 for r in rows "
             "if r['event']=='settlement_confirmed'))",
             str(journal)],
            capture_output=True, text=True, check=False, cwd=tempfile.gettempdir())
        self.assertEqual(probe.returncode, 0, probe.stderr)
        total, settled = (int(part) for part in probe.stdout.split())
        self.assertGreater(total, 0)
        self.assertGreaterEqual(settled, 5,
                                "a stranger process cannot read the run's settlements")

    # -- helpers -------------------------------------------------------------------------
    def _journal_path(self) -> Path:
        matches = sorted((self.out / "artifact_base").rglob("journal.ndjson"))
        self.assertEqual(len(matches), 1, f"expected one journal, found {matches}")
        return matches[0]

    def _journal_rows(self) -> list[dict]:
        return [json.loads(line) for line in
                self._journal_path().read_text().splitlines() if line.strip()]

    def _settlements(self) -> list[dict]:
        out = []
        for row in self._journal_rows():
            if row["event"] != "settlement_confirmed":
                continue
            result = row["source_vocabulary"]["event"].get("result", {})
            record = (result.get("gate") or {}).get("record") or {}
            out.append({"role": record.get("role"), "phase": record.get("phase"),
                        "verdict": result.get("result") or result.get("status"),
                        "seq": row["seq"]})
        return out


if __name__ == "__main__":
    unittest.main()
