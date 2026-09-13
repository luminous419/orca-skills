"""OS-37 F5 (consolidated follow-up review of `87f6179`) -- the REAL-CLI, Orca-free
Worker -> Reviewer FAIL -> correction -> fresh Reviewer PASS loop, through the PRODUCTION
GRAPH/ADAPTER PROMPT BOUNDARY.

`test_os37_r10_workflow_e2e.py` drives the same `run_workflow.py --adapter standalone`
graph path but with the deterministic NATIVE FIXTURE agent, which receives the canonical
intent.  This module is the finding-5 answer: it drives a REAL `claude` CLI, and every
prompt the agent sees is the one `launcher.build_standalone_prompt_renderer` renders at
`EXECUTE_INTENT -> StandaloneAdapter.start` -- role, phase, task contract, correction
instruction and the `STATUS:` / `RESULT:` + `decision-gate` review-output contract.

Skip-gated on `ORCA_OS37_E2E=1` (no CI runner sets it) because it needs a real,
authenticated agent CLI and drives several real local agent processes over minutes.
Declared in `scripts/tolerated_skip_manifest.txt` with the exact reason below.
"""
from __future__ import annotations

import ast
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from scripts import os37_r10_graph_prompt_e2e as harness

R10_REASON = ("requires ORCA_OS37_E2E=1; the F5 graph-prompt E2E drives a real agent CLI "
              "through run_workflow.py with orca removed from PATH")
R10_ENABLED = os.environ.get("ORCA_OS37_E2E") == "1"
E2E_CLI = os.environ.get("ORCA_OS37_E2E_CLI", "claude")


@unittest.skipUnless(R10_ENABLED, R10_REASON)
class F5GraphPromptE2ETests(unittest.TestCase):
    """One real Orca-free run of the workflow graph whose prompts are rendered at the
    production boundary, asserted from the settlements the graph consumed."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.out = Path(tempfile.mkdtemp(prefix="os37-f5graph-"))
        cls.record = harness.run_e2e(E2E_CLI, cls.out, timeout_s=1800.0)

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.out, ignore_errors=True)

    def _require_ran(self) -> None:
        if self.record.get("outcome") != "ran":
            self.skipTest(f"the CLI was BLOCKED, not established: "
                          f"{self.record.get('constraint')}: "
                          f"{self.record.get('observed', '')[:400]}")

    def test_orca_was_not_resolvable_in_the_run(self) -> None:
        pre = json.loads((self.out / "preconditions.json").read_text())
        self.assertIsNone(pre["orca_on_child_path"],
                          "`orca` was resolvable; the E2E precondition is NOT established")
        self.assertEqual(pre["orca_env_names"], [])

    def test_the_workflow_reached_completed_through_the_launcher(self) -> None:
        self._require_ran()
        summary = self.record["run_summary"]
        self.assertEqual(summary["terminal_status"], "COMPLETED",
                         (self.out / "run_stderr.txt").read_text()[-2000:])
        self.assertEqual(summary["terminal_reason"]["code"], "WORKFLOW_COMPLETED")
        self.assertGreaterEqual(summary["final_review_iterations"], 1)

    def test_the_loop_was_worker_reviewer_fail_correction_fresh_pass(self) -> None:
        """The finding-5 shape, read from the settlements the GRAPH consumed -- not chosen
        by the harness.  A phase reviewer returned FAIL, a further worker settlement
        followed, and a later phase reviewer returned PASS, all through the rendered
        production prompt."""
        self._require_ran()
        self.assertTrue(harness.loop_is_established(self.record),
                        f"the FAIL -> correction -> PASS loop was not established: "
                        f"{self.record.get('settlements')}")
        verdicts = [row.get("result") for row in self.record["settlements"]]
        first_fail = next(i for i, v in enumerate(verdicts) if v == "FAIL")
        self.assertTrue(any(v == "PASS" for v in verdicts[first_fail + 1:]),
                        f"no fresh PASS followed the reviewer FAIL: {verdicts}")

    def test_the_prompt_was_the_rendered_production_prompt_not_canonical_intent(self) -> None:
        """The delivered prompt carried the task contract and the review-output contract,
        which the canonical `ActionIntent` JSON does not: the design the worker produced
        cites the objective's own section titles rather than envelope fields."""
        self._require_ran()
        design = self.out / "wt" / "DESIGN.md"
        self.assertTrue(design.exists(), "the worker wrote no DESIGN.md")
        text = design.read_text()
        self.assertIn("## Parsing", text)
        self.assertIn("## Examples", text)
        # The correction round filled the third section the reviewer failed on.
        self.assertIn("## Error handling", text)


class HarnessCannotAuthorAVerdictTests(unittest.TestCase):
    """NOT gated: a cheap AST walk that runs everywhere.  The harness's whole claim is that
    it never chooses a PASS/FAIL -- the reviewer does, and the workflow gate reads it out of
    the settlement the graph consumed.  If any `PASS`/`FAIL` string is assigned, returned or
    compared as a verdict in the harness, this fails by name."""

    def test_no_pass_or_fail_literal_is_used_as_a_verdict(self) -> None:
        source = Path(harness.__file__).read_text()
        tree = ast.parse(source)
        offenders: list[str] = []
        for node in ast.walk(tree):
            const = None
            if isinstance(node, ast.Return) and isinstance(node.value, ast.Constant):
                const = node.value.value
            elif isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant):
                const = node.value.value
            if isinstance(const, str) and const.strip() in ("PASS", "FAIL"):
                offenders.append(f"line {getattr(node, 'lineno', '?')}: {const!r}")
        # A `== "PASS"` / `== "FAIL"` comparison is how `loop_is_established` READS the
        # graph's verdict; that is admissible (it consumes, never authors).  Only an
        # assignment or return of a bare verdict literal is forbidden.
        self.assertEqual(offenders, [],
                         f"the harness authored a verdict literal: {offenders}")


if __name__ == "__main__":
    unittest.main()
