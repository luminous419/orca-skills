# Pre-OS-4 legacy baseline

`pre_os4_artifacts.json` is a golden capture of the three artifacts a run leaves
behind, for two skills across three representative workflows (single canonical
phase, multi canonical phase, and a specialized `bugfix` phase). Each surface is
taken from the runtime that actually produces it:

| surface | producer | shape |
|---|---|---|
| `task_specs` | `render_task_spec`, wrapped for the run | full text of every dispatched spec |
| `orchestrator_log` | `OrcaRuntimeHarness`, the log-writing runtime, replaying **this fixture's** phase sequence | the real file, normalized |
| `final_report` | `scripts/final_report.py`, rendering **that skill's own** SKILL.md template | the renderer's whole output text |

None of these is a summary, and none is shared across fixtures:

- Each orchestration log replays the phases of its own workflow, so the three
  orchestration fixtures hold three different logs. An earlier revision pasted one
  fixed single-phase run onto all six, which made a `bugfix` fixture's "log" record
  `phase=implementation`.
- The loop fixtures hold **no** log, and that is the contract rather than a gap: that
  runtime has no run-scoped log at all, and its evidence medium is the final report.
- The report follows each skill's own template — the loop one carries no risk axis and
  no Final Adversarial Review block. `FinalReportContractTests` parses both templates
  out of the SKILL.md fences and checks the renderer field by field in both
  directions, including that it emits no key the template does not declare.

The spec and log capture need no production hook, so the identical capture function
runs against the pre-OS-4 commit and against the current tree. The report renderer is
new in OS-4, so `scripts/final_report.py` is **copied** into the pre-OS-4 checkout —
the same file, not a re-typed twin — and applied to that commit's workflow data.

It was generated from commit `8f3cfa3` ("Fix TIMING_LOG timestamp and duration
correctness (#17)"), the last commit **before** Agent Profile (OS-4) existed, by
running `scripts/test_e2e_harness.py`'s `capture_legacy_artifacts()` inside a
`git archive HEAD` checkout of that commit.

`LegacyByteIdentityTests` compares the current code's **omitted-profile** output
against this file character for character. That is the claim OS-4 has to keep:
a run that does not select a profile produces exactly what it produced before the
feature was written. Regenerating this file to make a failing test pass would
destroy the only evidence for that claim — if it fails, the current code changed
legacy behaviour and the code is what needs fixing.

Timestamps, orca-assigned ids and the workspace path are normalized out; they vary
per run and were never part of the contract.
