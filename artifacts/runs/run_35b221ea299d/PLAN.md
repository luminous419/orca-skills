# Worker Result

STATUS: COMPLETE

Run: run_35b221ea299d · Phase: plan · Iteration: 3 · Role: worker
Branch: os-29-continuous-decision-gates (base main @ b13f191)
Ticket: OS-29 "Add Continuous Decision and Escalation Gates to Every Phase"
Approved input: `artifacts/runs/run_35b221ea299d/ANALYSIS.md` (PASSED its gate at iteration 2,
`REVIEW_ANALYSIS_iteration2.md`). No approved ANALYSIS conclusion is changed by this PLAN.

Every `file:line` below was read out of this branch's working tree during this phase, not copied
from ANALYSIS. Where my read disagreed with an ANALYSIS line number I state my own; the only
divergences found were `scripts/workflow_contract.py` `_find_choice` (**`:45`**, ANALYSIS said
`:41-60`) and `_find_review_verdict_choice` (**`:66`**, ANALYSIS said `:63-88`). Both are
range-vs-definition-line differences, not factual errors, and neither is load-bearing.

### What iteration 3 changed (and what it deliberately did not)

The iteration-2 gate closed **F-002** and left **one** blocking finding. This revision fixes
exactly it and nothing else:

* **F-001** (the remaining half): the run-entry declaration omitted the schema version that the
  plan's own admissibility rule **A4** required, and the supplied evidence exercised only
  `decision_policy.validate_record()`, which does not read a record-level version at all. Resolved
  by defining the **ledger-RECORD schema version** as an object distinct from OS-28's
  policy-contract-block version — new subsection **"The ledger-record schema version, and why it is
  not OS-28's"** in P6a, the field `ledger_schema_version` on the RED **and** on the agent ledger
  records, **A4** split into four explicit clauses A4-i…A4-iv with their own closed reasons, a new
  terminal reason `DECISION_LEDGER_SCHEMA_UNSUPPORTED`, two new negative fixtures **F13/F14**, and
  the whole **A1–A6** path executed over the exact RED with fifteen controls plus an on-disk round
  trip (`## Decision Record`, "The complete A1–A6 path was executed").

The consequential edits it forced, in full: **C1** and **C3** (P2), **W-2b** (P3), the **F3** row of
P6's fail-closed table, P6a's record shape / A4 / evaluation order / validation table, **P7**'s
ledger-mechanics field list, scenario row **13** (P4), and the backward-compatibility paragraph in
*Rollback / no-op guarantees*. Nothing else was touched: P6b, W-3, W-4, W-6, the reuse inventory,
the parity plan, the provenance plan, the scope boundary and the other thirteen scenario rows are
carried forward unchanged, as instructed.

### What iteration 2 changed (and what it deliberately did not)

The iteration-1 gate FAILed on two blocking transition-contract findings. This revision fixes
exactly those and their consequences:

* **F-001** is resolved by a new subsection **P6a**, which names the producer, the machine-readable
  input, the binding and the admissibility rule for **every** B1 check including the first phase of
  a new run, plus a new work item **W-2b** and four new negative fixtures (F9–F12).
* **F-002** is resolved by a new subsection **P6b**, which replaces the contradictory W-3/W-4 prose
  with **one** risk-specific transition table and one boundary/axis ordering rule.

Everything the Reviewer called strong — the P1 reuse inventory, the P5 parity plan, the P7
provenance plan, the P9 scope boundary and the fourteen-scenario matrix — is kept. The
**consequential edits F-001/F-002 forced** are flagged inline with the marker **[F-001]** or
**[F-002]** and are, in full: W-3, W-4 and W-6 rewritten (P3); new W-2b (P3); C2/C3/C4 change
surface and sizes (P2); new RU-12 (P1); the execution-order DAG; scenario rows 3, 4, 5, 6, 10, 12
and 13 (P4); risks R-1, R-2 and the new R-P5/R-P6 (P8); limitation **L6**; DESIGN items D2/D7 and
the new D8 (P10); and the "behavioural no-op" claim in *Rollback / no-op guarantees*, which was
overstated and is now correct.

---

## Goal

Make `scripts/decision_policy.py` the **authority at three boundaries the existing correction loop
already has**, so that a quality failure and a user-decision block become two distinguishable
outcomes on two separate axes — adding no second loop, no third role, and no duplicate gate.

Concretely, when a phase produces `NEEDS_INPUT` or `CONFLICT`:

* the correction Worker is not dispatched and the next phase is not dispatched (**in code**);
* the pause consumes **no** correction iteration at any risk level;
* the judgement, its grounds and its provenance survive as machine-readable run evidence;
* the run terminates with an explicit blocked outcome — not a pause, not a resume (OS-31), not a
  question protocol (OS-30).

---

## Scope / Out of Scope

### In scope

| # | Deliverable |
| --- | --- |
| S1 | A gate evaluator that calls the OS-28 contract at boundaries **B1 / B2 / B3** (ANALYSIS A3) |
| S2 | A machine-readable decision channel out of Worker and Reviewer results, fail-closed on absence |
| S3 | Dispatch blocking in `E2EHarness.run_workflow()` and a pre-`start_worker` guard on the live Orca path |
| S4 | Iteration non-consumption for a decision block, at every risk level, with quality `FAIL` accounting untouched |
| S5 | An explicit terminal blocked outcome expressible in the existing lifecycle vocabulary |
| S6 | Provenance: the thirteen required machine-readable fields, reusing the existing columns and the append-only per-dispatch record machinery |
| S7 | Cross-Skill parity of decision **semantics**, with drift as a validator FAILURE |
| S8 | The fourteen ORIGINAL_REQUEST scenarios as positive/negative fixtures plus the non-vacuity proofs |
| S9 | Documented limitations L1–L5 for the absent OS-30/OS-31 |

### Out of scope (P9 — restated concretely, with the boundary made operational)

| Excluded | Owner | The concrete line OS-29 must not cross |
| --- | --- | --- |
| Question composition, options, recommendation, default/timeout behaviour, reply normalization, response actor/provenance, **supersession lineage**, cancel/scope-change protocol, the `HumanApprovalPort` request/response contract | **OS-30** | OS-29 may emit *that* an item is undecided and *why*; it may not emit a question object, an option set, or a link from a new decision to the one it supersedes. Scenario 8 is discharged by **requiring a new decision event or escalation**, never by recording lineage. |
| `WAITING_FOR_INPUT` durable run state and its transitions; settling Task/Dispatch and terminal ownership at pause; binding a pending request to run/head/artifact digest; discovery/resume from a new Coordinator; duplicate/stale response handling; stale-decision revalidation; crash/replay idempotency | **OS-31** | OS-29 must **not** add a fifth `RUN_STATUS` value (`run_logging.py:105`), must not claim resume, and must not write any "pending request" object a future Coordinator is expected to pick up. A decision-blocked run **terminates**. |
| Evaluation benchmarks / success metrics | OS-32 | No scorer, no metric document. |
| Deterministic production engine + Orca adapter separation | OS-27 follow-up | `e2e_harness.py` stays the deterministic reference; no extraction. |
| A separate Reviewer, a duplicate gate loop, a real-time monitoring agent, new phase vocabulary, modifying past run artifacts, unrelated refactoring | — | Enforced by INV-D1/INV-D2/INV-D3 + mutation **M-DUP** (P4). |
| Weakening lifecycle / Risk / Quality Profile / Agent Profile / Final Review guarantees | — | Enumerated as the "deliberately not changed" list in P2. |

### Limitations that must be written down because OS-30/OS-31 do not exist (ANALYSIS A8)

**L1** a blocked run terminates and cannot be resumed; answering means a new run. **L2** no question
is asked in any structured form. **L3** no supersession lineage; scenario 8's answer is escalation,
not lineage. **L4** no timeout semantics beyond the negative rule already in the contract
(`orca-worker-reviewer-orchestration/SKILL.md:322`, `forbidden_authority_sources`). **L5** at LOW
there is no phase Reviewer (`scripts/e2e_harness.py:1070`), so a LOW Worker's decision
*misclassification* is caught only by the Final Adversarial Review. **L6** **[F-002]** a decision
block is **terminal in OS-29 at every risk level**, even when a downgrade is validly authorized:
`validate_transition()` can in principle accept `NEEDS_INPUT -> CLEAR` when a conforming
`user_decision` is present, but acting on that acceptance mid-run *is* resume, which is OS-31's.
OS-29 therefore **records** an accepted downgrade and still terminates. Today the case is
unreachable anyway — no in-run channel exists to supply a `user_decision` (that channel is OS-30).

---

## Work Items

### P1. Reuse inventory — what already exists, and what OS-29 adds to it

| # | Existing component | File:line | What it already does | What OS-29 adds |
| --- | --- | --- | --- | --- |
| **RU-1** | **The transition engine, single-phase half** — `E2EHarness.run()` | `scripts/e2e_harness.py:898`, loop header `:904` | One `for` loop, two agent-invoking `subprocess.run` sites (Worker `:981`, Reviewer `:1158`), six terminating branches: Worker-BLOCKED `:1015`, LOW safety floor `:1029-1051`, finding-trace `:1053-1066`, LOW gate `:1070`, Reviewer PASS `:1198`, Reviewer FAIL `:1210-1215`, budget exhausted `:1216-1226` | **Boundary B2** as a new guard between the `worker_attempts.append(...)` at `:1012` and the existing BLOCKED branch at `:1015`, and **B3** at `:1198`/`:1210`. Guards go **inside `run()`**, never in `run_workflow()`, because all three round callers funnel here (R-8) |
| **RU-2** | **The transition engine, whole-run half** — `E2EHarness.run_workflow()` | `:1424`; three round-dispatch sites `:1520` (phase gate), `:1625` → `_run_correction_round` `:1391` → `run()` `:1405`, `:1705` (T5a) | Sequential phase gates, Final Review `while True:` at `:1531` with T1 `:1565`, T2 `:1573`, T3 `:1584-1609`, T4 correction `:1611-1668`, T5a revalidation `:1691-1721` | **Boundary B1** as a `return snapshot(...)` guard placed beside the *existing* budget guards at `:1612` and `:1692`, at the phase-gate site `:1520`, and at the Final-Review attempt open `:1531` |
| **RU-3** | **The decision policy loader/evaluator** | `scripts/decision_policy.py`: `load_decision_policy` `:498`, `parse_decision_policy` `:200`, `permitted_states` `:982`, `record_facts` `:1015`, `validate_record` `:1189`, `validate_transition` `:1300`, `DecisionPolicyError` `:133` | A complete, tested, **fail-closed** contract evaluator with **zero production consumers** (only importer: `scripts/validate_skills.py:12-21`) | **The call.** OS-29 adds no new fail-closed semantics — every rejection it needs already exists here. It adds an importer, and it must not add gate logic *into* this module (its docstring at `:22-26` forbids exactly that; the dependency direction stays gate → policy) |
| **RU-4** | **The run-scoped logging writer** | `scripts/run_logging.py`: `ORCHESTRATOR_LOG_COLUMNS` `:62-86` (OS-3 sparse-column note `:74-80`), `TIMING_LOG_COLUMNS` `:87-103`, `log_orchestrator_event` `:346`, `log_timing_event` `:413`, `log_run_status` `:514`, `RUN_STATUS_VALUES` `:105`, `ROUND_KIND_VALUES` `:111-116` | Append-only tables where "the columns are the whole schema" (`:59-61`); four terminal statuses validated eagerly | Two **sparse** columns (`decision_state`, `decision_reason_code`) on the OS-3 precedent, and one closed `reason` constant. **No new `RUN_STATUS` value, no new `round_kind`** |
| **RU-5** | **The append-only per-dispatch record machinery** | `run_logging.py`: `write_final_review_audit_record` `:2120`, `_REQUIRED_RECORD_FIELDS` `:2358-2372`, `_stage_and_publish_audit_record` `:1779`, `redact_text` `:1129`, `FINAL_REVIEW_AUDIT_DIRNAME` `:871`, events `:978-980`, `export_final_review_evidence` `:2549` | Immutable, staged-then-published-by-`os.rename`, redacted, never edited, "correcting a record means writing a new record under a new key" | The **decision/assumption ledger** the live Jira ticket asks for, built from this pattern — *not* re-invented |
| **RU-6** | **The reviewer-context machinery** | `scripts/task_context.py`: `REVIEWER_CONTEXT_KEYS` `:84-93`, `build_reviewer_context` `:343`, `REVIEWER_DRILL_DOWN_MANDATE` `:97`, `REVIEWER_PRIOR_PASS_IS_NOT_EVIDENCE` `:104`; consumed by `orca_runtime_harness.dispatch_context` `:446` (`:535`) | Eight delta-first keys reach the Reviewer as part of the dispatched spec, with a mandatory non-empty `drill_down` | The Reviewer's decision-classification verification framing rides in the **existing** `new_claims` / `drill_down` keys. **No ninth key unless DESIGN proves the eight cannot carry it** — `REVIEWER_CONTEXT_KEYS` is a closed tuple asserted at `orca_runtime_harness.py:2483-2484` |
| **RU-7** | **The cross-Skill parity validator** | `scripts/validate_skills.py`: `validate_decision_policy_contract` `:2253`, C4 raw-equality `:2481-2486`, C7 size budget `:2313-2318`, C11a `:2451-2457`, C11b `:2458-2463`, C11c `:2465`, C11d `:2469-2472`, C12 prose anchors `:2474-2478` (list `:713-724`), C13/C14 `:2488-2505` (anchors `:725-728`); the nine orchestration-only anchor contracts and the "division of labour" comment `:744-750` | ~25 checks per Skill + cross-Skill equality; 648 checks total today | A **tenth orchestration-only anchor contract** for the gate lifecycle, a mirrored-semantics anchor set, the Markdown↔machine drift check, and their negative regression tests |
| **RU-8** | **Task-spec rendering** | `task_context.py`: `TASK_BOUNDARY_KEYS` `:38-44`, `build_task_boundary` `:311`, `phase_artifact_contract` `:284`, `run_artifact_root` `:234`, `ensure_run_artifact_root` `:263`; assembled in `orca_runtime_harness.dispatch_context` `:446`, called before dispatch at `:2415` | The five-key layer-1 boundary, the per-(role, phase) artifact path, the run artifact root created before the first dispatch | Nothing structural. The decision record's path is derived from `run_artifact_root()`; **`TASK_BOUNDARY_KEYS` stays five** |
| **RU-9** | **The workflow output-contract parser** | `scripts/workflow_contract.py`: `_find_choice` `:45`, `_find_review_verdict_choice` `:66`, `load_workflow_output_contract` `:92`, the exact worker pair `{COMPLETE, BLOCKED}` `:97`, reviewer pair `{PASS, FAIL}` `:100`, statuses `:117` | Reads the four vocabularies out of SKILL.md so code and contract cannot drift | A **new field**, never a new *value* in `STATUS:`. `_find_choice` asserts the worker value set is *exactly* the pair (`:97`); widening it is the highest-blast-radius option (ANALYSIS A4 O3) and is out of scope |
| **RU-10** | **The live Orca dispatch path** | `scripts/orca_runtime_harness.py`: `run_attempt` `:2498` → `run_existing_task` `:2382` → `dispatch_context` `:2415` → `_open_phase_iteration_boundary` `:2354` → `start_worker` `:1763`; settlement `wait_for_done` `:1864`, `settle_attempt` `:1898`; the one logging funnel `_log_attempt` `:2094` (round_kind check `:2121-2124`, RUN_STATUS check `:2298-2301`); pre-dispatch failure path `_log_pre_dispatch_failure` `:2265`; `_safe_log` `:2076` | Real Orca lifecycle; **no phase loop and no iteration counter** — `iteration` is a caller-supplied parameter | A B1 guard **before `start_worker`**, reusing the existing pre-dispatch failure path, plus decision fields flowing into `_log_attempt`. `_log_attempt` runs *after* settlement (`:2126-2131`), so it can **record** but never **gate** |
| **RU-11** | **The deterministic fake agents** | `scripts/fake_worker.py:1-71`, `scripts/fake_reviewer.py:1-124` | Emit the result contract for the harness; OS-3's `--unit-test-status` flag documents the opt-in precedent at `fake_worker.py:28-32` ("the default emits NOTHING, so every existing scenario's output stays byte-identical") | An explicit decision declaration. **The OS-3 opt-in precedent must be inverted here** — see W-6 and Risk R-P1: fail-closed means silence cannot be the default |
| **RU-12** **[F-001]** | **The run-root provisioning sites** — the three statements that open a run's artifact directory | `scripts/e2e_harness.py:658` (`__init__`, "before the first Worker/Reviewer subprocess"), `:1490` (`run_workflow`, re-provisioned because `run_id` may differ from the constructor's), `scripts/orca_runtime_harness.py:1529` (`start_run`, "once, immediately after the run id is known — and BEFORE any caller can create a Task"). The fourth call at `e2e_harness.py:1362` is a **read** of an already-open root on the Final-Review report path, not a run open | Each already runs strictly before the run's first dispatch and each already carries a comment saying so — they are the only points in the codebase that are "run entry" | **The B1 producer (P6a).** These three sites call `open_decision_ledger()` instead, which provisions the root **and** writes the sequence-0 run-entry declaration in one statement, so a root can never exist without a ledger. No new call site, no new ordering to get wrong |

### P2. Minimal change surface

Every row states the nature, rough size, and why it is minimal. "Rough size" is lines of production
change, excluding tests.

| # | File | Nature of change | Rough size | Why this is the minimum |
| --- | --- | --- | --- | --- |
| C1 | **`scripts/decision_gate.py`** (new) | The gate evaluator: parse a decision result out of an agent result, evaluate it against the loaded policy, and answer one question — *may this boundary dispatch?* Imports `decision_policy`; imports **nothing** from `e2e_harness` / `orca_runtime_harness` / `run_logging` | ~150–250 | A **new module rather than an edit to `decision_policy.py`**, because that module's docstring (`:22-26`) makes contract-only isolation a stated invariant and `permitted_states`' docstring repeats it (`:995-996`). Putting the gate there would break the one property OS-28 shipped. One module means one place for the risk-independence signature assertion (P1 in ANALYSIS A9). **[F-001]** It is also the single owner of `LEDGER_RECORD_SCHEMA_VERSION` / `SUPPORTED_LEDGER_RECORD_SCHEMA_VERSIONS` and of the A1–A6 admissibility function, for the reason given in P6a's *"why it is not OS-28's"*: the record version is a **gate-input** contract, and the gate is the only thing that may decide admissibility. `run_logging` imports the constant from here (acyclic — C1 forbids the reverse edge, and the W-1 AST assertion pins it) |
| C2 | `scripts/e2e_harness.py` | B2 guard after `:1012`/**before** `:1015`; B3 guards at `:1198` and `:1210`; B1 guards at `:1520`, `:1612`, `:1692`, `:1531`; **[F-001]** the two run-open calls at `:658` and `:1490` become `open_decision_ledger(...)` (same one-statement shape, returns the same root); **[F-002]** a `decision_block` field on `WorkflowResult` `:161` carrying the round's terminal decision outcome, and the verification-mode flag threaded from B2 to the Reviewer branch through the existing local `run()` state; `gate_attempts()` `:1501-1507` non-consumption keyed on `decision_block`, not on risk; new terminal `reason` constants beside the four existing ones (`UNIT_TEST_*` `:109-110`, `MAX_ITERATIONS_REACHED` `:1225`, `FINAL_REVIEW_MAX_ITERATIONS_REACHED` `:1576`, `OUT_OF_SCOPE_FINAL_REVIEW_FINDING` `:1605`); decision fields on `WorkflowResult` `:161` / `WorkflowRunResult` `:270` | ~150–220 | Every guard is a **condition added to a branch that already exists**; no new function on the round-dispatch path, no new round. B2 sits **above** the LOW early return at `:1070` so the check exists at LOW (closes V1/R-1). Non-consumption is **one** edit in the closure all three increment sites already call (`:1521`, `:1635`, `:1712`) rather than three edits |
| C3 | `scripts/run_logging.py` | Two sparse columns in `ORCHESTRATOR_LOG_COLUMNS` `:62-86`; the decision ledger writer + reader modelled on `write_final_review_audit_record` `:2120`; **[F-001]** `open_decision_ledger()` (the B1 producer — provisions the root via `_ensure_run_artifact_root` `:306` and writes the sequence-0 run-entry declaration, **idempotently**, first-writer-wins, on the same "idempotent the same way `ensure_run_artifact_root()` is" precedent already stated at `:290`, **stamping every record it writes with `decision_gate.LEDGER_RECORD_SCHEMA_VERSION`**) and `read_decision_ledger()` (the ordered reader B1/B2/B3 all share; it *reads and orders*, and the A1–A6 judgement — including the version check — is the gate's, not the reader's); new `--event` values | ~260–380 | Columns follow the OS-3 sparse precedent whose rationale is written at `:74-80`; the ledger reuses `_stage_and_publish_audit_record` `:1779` and `redact_text` `:1129` rather than a second durability scheme. `RUN_STATUS_VALUES` `:105` and `ROUND_KIND_VALUES` `:111-116` are **not** touched |
| C4 | `scripts/orca_runtime_harness.py` | Pre-`start_worker` B1 guard on the `run_existing_task` path (`:2382`, guard before `:2456`), reusing `_log_pre_dispatch_failure` `:2265`; **[F-001]** the run-open call inside `start_run` at `:1529` becomes `open_decision_ledger(...)`, i.e. the live path gets its run-entry declaration at the same point it already gets its run root, its ORCHESTRATOR_LOG and its TIMING_LOG (`:1530-1536`); decision fields into `_log_attempt` `:2094` | ~80–130 | The pre-dispatch failure path already exists and already logs; the alternative (gating inside `_log_attempt`) is impossible because it runs after settlement (`:2126-2131`) |
| C5 | `scripts/validate_skills.py` | A tenth `#### … contract` anchor block + its dict and checks; the mirrored-semantics anchor set (both directions); the Markdown↔machine drift check | ~120–180 | Follows the nine existing anchor-contract dicts (`:114, 166, 200, 234, 265, 333, 407, 755` and the risk block) and the division-of-labour comment at `:744-750`. Adds no key to the shared block |
| C6 | `orca-worker-reviewer-orchestration/SKILL.md` | New `#### Decision gate contract` anchor block; §10 `:1481` and §11 `:1504` result contracts gain the decision field; §12 `:1712`, §13 `:1758`, §17 `:1989`, §18 `:2192` gain the decision-axis rules; **the stale sentence at `:368-369`** ("각 phase gate에서 검사를 실행하는 것(OS-29) … 아직 구현되어 있지 않다") is corrected; L1–L5 documented | ~80–140 | `:368-369` is **not** in `DECISION_POLICY_SKILL_PROSE_ANCHORS` (`validate_skills.py:713-724` — verified), so correcting it is legal; leaving it would make the shipped Skill assert something false, which is Final Review axis E |
| C7 | `orca-worker-reviewer-loop/SKILL.md` | §14 `:874` and §16 `:916` result contracts gain the **same** decision field; the sentence at `:364-365` **stays as-is** (the loop Skill still does not gate) | ~30–60 | Decision *semantics* mirror; Orca lifecycle does not. The loop Skill has **zero** `#### … contract` anchor blocks today (verified by grep) and must still have zero |
| C8 | `orca-worker-reviewer-*/templates/*.md` (7 + 7), `reviews/common.md` (2) | The Decision Record section gains the gate-result relationship; the **optionality sentence stays byte-identical** | ~10–20 per file | R-A2-1: `DECISION_RECORD_OPTIONALITY_ANCHOR` (`validate_skills.py:725-727`) and `reviews/common.md:200` ("섹션이 없는 것은 finding이 아니다") must both survive — removing them weakens an OS-28 guarantee, which the ORIGINAL_REQUEST's *Out of scope* forbids |
| C9 | `scripts/workflow_contract.py` | **Only if** DESIGN puts the decision field's vocabulary in SKILL.md: a loader for it | 0–40 | `_find_choice` `:45` asserts the worker value set is exactly `{COMPLETE, BLOCKED}` (`:97`); a new *field* does not touch that assertion, a new *value* would |
| C10 | `scripts/fake_worker.py`, `scripts/fake_reviewer.py` | An explicit decision declaration, emitted by default | ~15–30 each | See Risk **R-P1**: under a fail-closed gate the default cannot be silence |
| C11 | `scripts/test_validate_skills.py:49-66` | Add `decision_gate.py` (and any new module `validate_skills` imports) to the copied-file list | 1–4 | The comments at `:57-64` record that omitting a dependency here turns every validator regression test into an import crash with empty stdout. Cheap, and invisible until it bites |
| C12 | `CHANGELOG.md` (Unreleased/Added), `docs/ROADMAP.md` | One entry; OS-29 status | ~10–20 | Release hygiene the repository already enforces via `verify_package.py` / `build_release.py` in CI |
| C13 | Tests: `scripts/test_decision_gate.py` (new), `scripts/test_os29_decision_gate.py` (new residue module), plus additions to `test_e2e_harness.py`, `test_run_logging.py`, `test_orca_runtime_contract.py`, `test_validate_skills.py`; fixtures `scripts/fixtures/decision_gate/{valid,invalid}/` | ~1200–1800 | Every case is required by the ticket's *Required validation scenarios* or by a non-vacuity rule | See P4 |

**No file needs to be added to `release_manifest.py`**: `INCLUDED_ROOTS` (`scripts/release_manifest.py:44`) already takes `scripts/` wholesale.

#### Deliberately NOT changed (and why)

| Not changed | Evidence | Reason |
| --- | --- | --- |
| The shared ```` ```policy-contract ```` `decision_policy` block | orchestration `SKILL.md:232`, loop `SKILL.md:228`; **measured this phase: exactly 90 body lines in both**, against `DECISION_POLICY_MAX_LINES = 90` (`decision_policy.py:130`) checked as C7 (`validate_skills.py:2313-2318`) | It is at 90/90. Any addition fails CI immediately, and C11a (`:2451-2457`) would additionally require a key-partition edit in `decision_policy.py:58-82`, and C4 (`:2481-2486`) requires byte-equal JSON in both Skills. The anchor-contract pattern exists precisely for this |
| `RUN_STATUS_VALUES` | `run_logging.py:105`, eagerly validated at `orca_runtime_harness.py:2298-2301` | A fifth value is a lifecycle-contract change, and `WAITING_FOR_INPUT` is **OS-31's** named deliverable |
| `ROUND_KIND_VALUES` | `run_logging.py:111-116`, validated at `orca_runtime_harness.py:2121-2124` | "No new phase vocabulary" is an explicit out-of-scope item. Note: this staying at four is **evidence for that item only** — it is *not* the non-duplication proof (ANALYSIS F-002) |
| Worker `STATUS:` / Reviewer `RESULT:` / `REVIEW_VERDICT:` value sets | `workflow_contract.py:97, 100`; `SKILL.md:1490, 1515`; `reviews/common.md` | `REVIEW_VERDICT: BLOCKED` already means "insufficient information for a trustworthy verdict" and maps to `RESULT: FAIL`, which routes into the correction loop and consumes an iteration — the exact opposite of what OS-29 needs (R-4). Invariants `SKILL.md:2240`, `:2244` |
| The four-axis lifecycle accounting | `SKILL.md:840` `#### Lifecycle accounting contract` | Untouched: OS-29 adds a *pre-dispatch* guard, never a settlement change |
| `final_review_iterations` and its bound | `e2e_harness.py:1533`, `:1573`; the T2 last-attempt guard's position as the **first** statement on the FAIL edge (`:1569-1577`, whose comment records that moving it was a real past defect) | Two-domain counter rule (`SKILL.md:1801`, pinned by `FINAL_REVIEW_COUNTER_DOMAINS`, `validate_skills.py:272`) |
| Risk / Quality Profile / Agent Profile axes | `SKILL.md:1034`, `:1584`, `:1686`; `scripts/quality_profile.py`, `scripts/agent_profile.py` | Independent axes; risk never expands decision authority (`SKILL.md:322` `independent_axes`, `decision_policy.py:93`) |
| Final Review guarantees | `SKILL.md:1989`, `:2170` `#### Final review contract`; `e2e_harness.py:1251`, `:1344` | OS-29 **adds** checks to the Final Review checklist; it removes none, and the audit-record write at `:1562` keeps its position |
| `scripts/review_isolation.py`, `scripts/final_review_eval.py`, `scripts/quality_profile.py`, `scripts/agent_profile.py` | — | OS-22 evaluation machinery and independent axes; unrelated to the decision axis |
| Prior runs' artifacts under `artifacts/runs/run_*` | ORIGINAL_REQUEST *Out of scope* | Never modified |

### P3. Work breakdown (dependency order)

Each item states **DONE =** (the completion condition) and **VERIFIED BY** (the mechanism that
proves it). IMPLEMENTATION executes them in this order.

| # | Work item | DONE = | VERIFIED BY |
| --- | --- | --- | --- |
| **W-1** | `scripts/decision_gate.py`: the fail-closed parser + evaluator. Public surface: a `GateResult` value object, a `parse_gate_result(text)` that raises on absent/malformed/unknown, an `evaluate(policy, result)` returning an allow/block outcome plus a closed reason, and a `DecisionGateError` | The module imports `decision_policy` and nothing from `e2e_harness`/`orca_runtime_harness`/`run_logging`; every one of the eight fail-closed sources raises; `evaluate` has **no** `risk`, `profile` or quality-profile parameter | `test_decision_gate.py` unit tests + an `inspect.signature` assertion mirroring `test_decision_policy.py:1244-1300`; a static import-direction assertion over the module's AST |
| **W-2** | The decision channel in the result contract: the field(s) an agent emits, and the run-scoped JSON record they bind to. **DESIGN chooses the mechanism (ANALYSIS U-1); PLAN fixes the requirement**: it is a distinct object from the optional `## Decision Record` section, it is REQUIRED at a gate boundary, and "no decision was needed" is *asserted* as `CLEAR` with grounds satisfying `no_open_decision_item` (`decision_policy.py:867-877`) | A Worker/Reviewer result with no decision channel is a **validation failure**, not a `CLEAR` | `test_decision_gate.py` negative fixtures; scenario 13 |
| **W-2b** **[F-001]** | **The B1 producer and the ledger reader** (full contract in **P6a**): `run_logging.open_decision_ledger()` writes the **run-entry declaration** as ledger sequence 0 at the three run-open sites (RU-12), and `read_decision_ledger()` returns the run's records ordered by sequence. **[F-001, iteration 3]** Every record is then run through the gate's **A4**: it must declare a `ledger_schema_version` in `decision_gate.SUPPORTED_LEDGER_RECORD_SCHEMA_VERSIONS` (A4-i/A4-ii) **and** pass `decision_policy.validate_record()` (A4-iii) **and** sit in a gapless `0..n-1` sequence (A4-iv). B1's input is always the ledger **head** selected by P6a's admissibility rule A1–A6 — never an agent result, never an absence | A run root cannot exist without a sequence-0 declaration; B1 at the **first** phase has an explicit, validated, bound `CLEAR` input; the declaration is **inadmissible** at any boundary after the first, so a later missing record cannot fall back to it | `test_run_logging.py` (producer + idempotence), `test_decision_gate.py` (A1–A6), `test_e2e_harness.py` (F9/F11 with its co-located control), `test_orca_runtime_contract.py` (the live half) |
| **W-3** **[F-002]** | **B2 guard** in `E2EHarness.run()`, between `:1012` and `:1015`, i.e. **above** both the `STATUS: BLOCKED` branch at `:1015` and the LOW early return at `:1070`. It implements **exactly rows 1–3 of P6b's B2 table and nothing else**: it *terminates* the round only at LOW and only on the fail-closed rows; at MEDIUM/HIGH a valid `NEEDS_INPUT`/`CONFLICT` sets the round's `verification_only` flag and **falls through** to the already-scheduled Reviewer | At LOW a valid `NEEDS_INPUT`/`CONFLICT` Worker result terminates **at B2** with `DECISION_BLOCKED:<state>:<code>`; at MEDIUM/HIGH the same result reaches the Reviewer dispatch at `:1158` with `verification_only` set and terminates at **B3-V** with the **same** `final_status`, `decision_state` and `reason_code`; a missing/malformed/unbound Worker gate result terminates at B2 at **every** risk level and never enters verification mode; the Worker-declared `STATUS: BLOCKED` path at `:1015` keeps its own distinct `WORKER_BLOCKED` reason (`:1024`) for the case where **no** decision block is present | Scenarios 3, 4, 5, 11, 13; ANALYSIS P3 ("the check executes on the LOW path"); the cross-risk equality assertion of scenario 7 |
| **W-4** **[F-002]** | **B3 guards** at `:1198` (PASS) and `:1210` (FAIL) plus Final Review T1 `:1565`, implementing **exactly P6b's B3 table**. Two modes on one code path: **B3-V** (`verification_only` set by W-3) and **B3-N** (normal). B3-V terminates the round from the Reviewer branch; B3-N terminates only when the *Reviewer's own* gate result is `NEEDS_INPUT`/`CONFLICT`, and otherwise leaves the existing PASS/FAIL routing byte-identical | In B3-V: the Reviewer's verification record must be **bound** to the Worker's B2 ledger key or the round fails closed; a proposed downgrade is decided **solely** by `decision_policy.validate_transition()` — OS-29 adds no downgrade rule of its own — and the block is terminal either way (L6). In B3-N: a Reviewer-discovered `NEEDS_INPUT`/`CONFLICT` terminates on the decision axis and charges **no** iteration, while its `RESULT: FAIL` and blocking finding are still recorded. **No new dispatch site, no new subprocess site and no new round exists in either mode** | Scenarios 4, 6, 8, 9, 10; **INV-D1/INV-D2/INV-D3 + mutation M-DUP**; the P6b determinism test (every cell of the two tables is a named case) |
| **W-5** **[F-001]** | **B1 guards** at the three round-dispatch sites `:1520`, `:1612` (beside the existing budget guard), `:1692` (same), and the Final-Review attempt open `:1531`. **Every one of them takes its input from W-2b's ledger head under P6a's rule A1–A6** — including the very first execution of `:1520`, which is the boundary F-001 named | With an unresolved blocking decision, `correction_dispatches` and `revalidation_dispatches` gain **no** entry and `sessions` gains no event ordered after the settling event | Scenario 12; INV-D2; the mutation "remove the guard → the same scenario dispatches" |
| **W-6** **[F-002]** | **Iteration non-consumption**: one edit in `gate_attempts()` (`:1501-1507`) — `return 0 if result.decision_block is not None else <the existing expression>`. Keying it on `decision_block` rather than on risk is what makes **one** edit cover **both** terminating shapes P6b produces: the LOW round that ends at B2 with a Worker attempt appended (`:1012`), and the MEDIUM/HIGH round that ends at B3-V with a Reviewer attempt appended (`:1194`). The two dispatch ledgers at `:1631-1634` and `:1708-1711` keep writing — a dispatch that physically happened is never rewound (C-9) | `phase_iterations[p]` is unchanged across a decision-blocked round at **all three risk levels and in both P6b terminal shapes**, while a quality-`FAIL` round still increments it | Scenario 3 run at low/medium/high + the co-located control that quality `FAIL` increments; the LOW-vs-MEDIUM asymmetry in ANALYSIS A5 is closed, not inherited |
| **W-7** | Terminal outcome: `RUN_STATUS: BLOCKED` (`run_logging.py:105`) + a **closed** reason constant (the set is P6a's A1–A6 reasons plus `DECISION_BLOCKED:<state>:<code>`; **[F-001, iteration 3]** it gains `DECISION_LEDGER_SCHEMA_UNSUPPORTED` for A4-ii, which is distinct from `DECISION_GATE_INPUT_MALFORMED` so that "this build is too old for this ledger" is never confused with "this record is broken") + the sparse `decision_state` / `decision_reason_code` columns. **DESIGN chooses among ANALYSIS A4's O1/O2/O3; PLAN scopes O1 as the budgeted shape** and records that O2 is contra-indicated by OS-31's ownership of `WAITING_FOR_INPUT` | A blocked run's terminal row states the state and reason code **in columns**, not in free text | `test_run_logging.py`; scenario 3/4 end-to-end |
| **W-8** | Live-path B1 guard before `start_worker` (`:1763`, called from `run_existing_task` `:2382`), reusing `_log_pre_dispatch_failure` `:2265`; decision fields through `_log_attempt` `:2094` | No Dispatch is created when a blocking decision is unresolved; the refusal is logged through the existing pre-dispatch path | `test_orca_runtime_contract.py`; scenario 12's live half |
| **W-9** | The decision/assumption **ledger**: append-only, staged-then-published, redacted, never edited, under the run artifact root (`task_context.py:234`, `:263`) | The thirteen required fields are all present and machine-readable (P7); a correction is a **new** record, never an edit | `test_run_logging.py` + the P7 drift validator |
| **W-10** | Skill text: the tenth anchor contract in orchestration `SKILL.md`; §10/§11 and loop §14/§16 result contracts; §12/§13/§17/§18 decision rules; correction of `:368-369`; L1–L5 | `validate_skills.py` passes with its new checks; the loop Skill still has **zero** `#### … contract` blocks | `validate_skills.py` (count rises above 648); `test_validate_skills.py` regressions |
| **W-11** | Validators: the anchor-contract dict + checks, the mirrored-semantics anchor set (both directions), the Markdown↔machine drift check; `test_validate_skills.py:49-66` copied-module list updated | Mutating one Skill only ⇒ FAIL; deleting a mirrored sentence from **both** ⇒ FAIL; a Markdown summary contradicting its machine record ⇒ FAIL | Scenario 14 + the P7 drift regression |
| **W-12** | The fourteen scenarios, their negative halves, and the three non-vacuity proofs | P4's table is fully green | Full CI |
| **W-13** | `CHANGELOG.md` Unreleased entry; `docs/ROADMAP.md` status; L1–L5 in the Skill | Release verification passes | `verify_package.py`, `build_release.py` |

### P5. Parity plan

| Direction | What | Enforced by | Regression test |
| --- | --- | --- | --- |
| **MUST be mirrored** (decision semantics) | The meaning of the four states; what evidence each requires; **the decision result a Worker/Reviewer must emit** — orchestration §10 `SKILL.md:1481` (lines at `:1490`) / §11 `:1504` (`:1515`) ↔ loop §14 `SKILL.md:874` (`:879`) / §16 `:916` (`:921`); all 14 `templates/*.md`; both `reviews/common.md`; the fail-closed source list; `forbidden_authority_sources` | Existing **C4** raw-equality of the shared JSON block (`validate_skills.py:2481-2486`) + a **new** `MIRRORED_DECISION_SEMANTICS_ANCHORS` tuple checked byte-for-byte in **both** Skills, in the same shape as `DECISION_POLICY_SKILL_PROSE_ANCHORS` (`:713-724`, checked as C12 at `:2474-2478`). The anchor form is required because byte-equality between the two Skills cannot catch a sentence deleted from **both** — the reason recorded at `validate_skills.py:711-712` | `test_validate_skills.py`: (a) mutate the sentence in **one** Skill → FAIL; (b) delete it from **both** → FAIL. Both halves, on the `test_decision_policy_prose_anchor_removed_from_both_skills_fails` pattern (`test_validate_skills.py:194`) |
| **MUST NOT be mirrored** (Orca-only lifecycle) | Dispatch blocking, `RUN_STATUS` + the reason constant, ORCHESTRATOR/TIMING columns, `round_kind`, terminal/Dispatch provenance, the Final Review audit record, the B1 pre-`start_worker` guard, the whole tenth anchor contract | A **new** check asserting the `#### Decision gate contract` heading is present in orchestration `SKILL.md` and **absent** from the loop `SKILL.md` — the same asymmetry the nine existing anchor contracts already have (verified this phase: 9 in orchestration at `:688, 840, 1034, 1090, 1117, 1544, 1584, 1686, 2170`; **0** in the loop). The division-of-labour rationale is already written at `validate_skills.py:744-750` | `test_validate_skills.py`: copy the orchestration-only block into the loop Skill → FAIL |
| **Legitimately divergent** | orchestration `SKILL.md:368-369` becomes "OS-29 is implemented here"; loop `SKILL.md:364-365` keeps "not implemented" | Neither sentence is in the C12 anchor list (verified), so the divergence is legal — but it must be **asserted**, not merely allowed: one check that the orchestration copy no longer claims OS-29 is unimplemented, and one that the loop copy still does | `test_validate_skills.py`, one test per direction |
| **Also parity-checked today** | `scripts/test_policy_smoke.py:21-25` runs every deterministic invocation assertion against **both** `SKILL_PATHS`; `test_decision_policy.py:1462-1470` asserts the two blocks are identical | Existing | Must stay green unmodified |

**Drift between the two Skills' decision semantics becomes a validation FAILURE, in both
directions** — that is scenario 14, and it is discharged by the two anchor sets above plus C4.

### P6. Fail-closed plan

The rule: **a gate boundary requires an explicit machine-readable decision result, and none of the
eight sources below may be presumed `CLEAR`.** Every rejection already exists in
`decision_policy.py` — OS-29 adds the *call*, and the boundary at which it happens.

| # | Presumption source | What must happen | Existing machinery | At B1 (`e2e:1520/1612/1692/1531`; `orca:before 1763`) — **input and admissibility per P6a** | At B2 (`e2e:1012→1015`) — **routing per P6b** | At B3 (`e2e:1198/1210`, FR `:1565`) — **routing per P6b** |
| --- | --- | --- | --- | --- | --- | --- |
| F1 | **Missing** decision record | validation failure | `OutputContractError` shape from `_parse_choice` (`e2e_harness.py:294-305`) | refuse dispatch, `snapshot(BLOCKED, …)` | terminate round, decision reason | terminate run, decision reason |
| F2 | **Malformed** record | validation failure | same + `DecisionPolicyError` (`decision_policy.py:133`) | same | same | same |
| F3 **[F-001]** | **Unsupported schema.** **Two distinct objects, two distinct constants** — see P6a *"why it is not OS-28's"*. (a) the **policy contract block** a Skill ships; (b) the **ledger record** an OS-29 producer writes | validation failure in both cases | (a) `SUPPORTED_SCHEMA_VERSIONS` `decision_policy.py:42`, enforced by `parse_decision_policy` `:200-214`, which raises rather than returning `None` — **existing, unchanged**. (b) `SUPPORTED_LEDGER_RECORD_SCHEMA_VERSIONS` in **`decision_gate.py`** (new, C1), enforced by **A4-i/A4-ii** on **every** record the ledger holds — `validate_record()` does **not** perform this check and was never written to | (a) the Skill fails to load ⇒ refuse dispatch; (b) A4-ii ⇒ refuse dispatch with `DECISION_LEDGER_SCHEMA_UNSUPPORTED`; a missing or non-integer version is `DECISION_GATE_INPUT_MALFORMED` (A4-i). **Neither can produce a `CLEAR`** | same, on the agent's B2 ledger record | same, on the agent's B3 ledger record |
| F4 | **Unknown state** | validation failure | `validate_record` `:1189` (unknown-state branch) | same | same | same |
| F5 | **Unknown / foreign reason code** | validation failure | `validate_record` `:1189` (code-vs-state binding) | same | same | same |
| F6 | **Missing required safety fact** | blocked, never `ASSUMPTION_ALLOWED` | `_undeclared_safety_facts` `:803`, applied from `validate_record` | same | same | same |
| F7 | **Model confidence / Worker-Reviewer agreement / recommended default** | never authority | `forbidden_authority_sources` (`SKILL.md:322`), `_user_decision_defect` `:682`, pinned by `DECISION_POLICY_REJECT_LIST` (`validate_skills.py:701-707`, checked at C10) | same | same | **scenario 10**: agreement between the two roles changes nothing |
| F8 | **Timeout / non-response** | never approval **and** never an iteration charge | same closed five-item list | same | same | **scenario 11** |

Three structural rules that make the above real rather than nominal:

* **The check is on the Worker-result boundary first (B2).** It is the only boundary that executes
  at every risk level; the LOW early return at `e2e_harness.py:1070` means a Reviewer-only check
  silently does not exist at LOW (ANALYSIS V1 / R-1).
* **The guard lives inside `run()`, not `run_workflow()`.** `run()` is entered from `:1520`,
  `:1405` and `:1705`; a guard in `run_workflow()` must be repeated three times and will drift
  (R-8).
* **A `CLEAR` must be asserted, not inferred.** Absence of the optional `## Decision Record`
  narrative section stays a non-finding (`reviews/common.md:200`); absence of a **gate result** is
  a failure. These are two objects, exactly as the requester states.
* **[F-001] "Nothing to check yet" is a claim, not a silence.** The table above says B1 refuses
  dispatch when its record is missing. That rule is only implementable if B1 has a *producer* even
  at the first phase of a new run, where no agent has run. **P6a** supplies it, and states in full
  why the run-entry declaration is an assertion the engine can re-check rather than a presumption
  the engine makes.
* **[F-002] The decision axis is evaluated before the quality axis at every boundary, and the
  risk-specific routing after a Worker block is a single table.** **P6b** is that table; W-3, W-4,
  W-6, the execution order and the scenario matrix all defer to it.

### P6a. B1 gate input: producer, binding and admissibility  **[resolves F-001]**

#### The defect this fixes

W-2 makes the decision channel something a **Worker or Reviewer emits**; W-5 puts **B1 before the
first Worker dispatch**. At the first execution of `e2e_harness.py:1520` (and at the live path's
first `run_existing_task`, `orca_runtime_harness.py:2382`) no agent has run and no prior boundary
has settled, so B1 had **no input at all**. With P6's rule honored the first phase always blocks;
with the boundary exempted the rule is fail-open at exactly the point the ticket cares about.
Making the fake agents emit `CLEAR` by default (C10/R-P1) cannot help: that output does not exist
until *after* the dispatch B1 is supposed to authorize.

#### The distinction, designed rather than assumed

| Claim | Is it admissible? | Why |
| --- | --- | --- |
| "This run's append-only decision ledger contains no open blocking item at run entry." | **Yes** — it is a statement about a *ledger*, it is produced explicitly by a named producer, it is bound to `run`, and the gate **recomputes** it (A6). | Nobody's judgement is being presumed. The first agent judgement still has to arrive at B2 and is still fail-closed there. |
| "No record is present, therefore `CLEAR`." | **Never** — this is the ticket's *Fail-closed rules*, item 1. | It presumes a judgement nobody made. |

OS-29 makes the first claim an explicit, validated, machine-readable record and gives the second no
code path anywhere.

#### The producer

**`run_logging.open_decision_ledger(run_id, *, base, phases, risk)`** (C3/W-2b). It provisions the
run artifact root **and** writes ledger sequence 0 — the **run-entry declaration (RED)** — in one
statement, so a run root can never exist without a ledger. It is **idempotent, first-writer-wins**,
on the precedent already written at `run_logging.py:290` ("Idempotent the same way
`ensure_run_artifact_root()` is"); re-opening a run whose ledger already has a sequence-0 record is
a no-op and never writes a second one.

**Its call sites are exactly RU-12's three run-open statements, and nothing else:**

| Site | Today | After |
| --- | --- | --- |
| `e2e_harness.py:658` (`E2EHarness.__init__`) | `ensure_run_artifact_root(self.run_id, base=self.workspace)` — "before the first Worker/Reviewer subprocess" | `open_decision_ledger(...)`, same one statement, same returned root |
| `e2e_harness.py:1490` (`run_workflow`) | same call, re-provisioned because `run_id` may differ from the constructor's | same |
| `orca_runtime_harness.py:1529` (`start_run`) | same call, "once, immediately after the run id is known — and BEFORE any caller can create a Task" | same, adjacent to the ORCHESTRATOR_LOG/TIMING_LOG opens at `:1530-1536` |
| `e2e_harness.py:1362` (Final-Review report write) | a **read** of an already-open root | **unchanged** — this is not a run open |

**Ordering is therefore not a new thing to get right.** Each of the three sites already runs
strictly before that run's first dispatch and each already carries a comment saying so. The static
guarantee is one AST assertion (same technique as INV-D3): *no run-open site provisions a root
without opening a ledger* — i.e. outside `open_decision_ledger` itself, `ensure_run_artifact_root`
is called in `e2e_harness.py` only from the Final-Review report path and in
`orca_runtime_harness.py` not at all.

#### The record

The RED is an ordinary OS-28 decision record and is validated by `decision_policy.validate_record()`
like every other one. It carries the OS-29 ledger fields (P7) in addition:

```json
{
  "ledger_schema_version": 1,
  "state": "CLEAR",
  "reason_code": null,
  "open_decision_item": false,
  "run": "<run_id>",
  "phase": "<the first requested phase>",
  "iteration": 0,
  "responsible_phase": "<the first requested phase>",
  "role": "coordinator",
  "boundary": "B1",
  "sequence": 0,
  "source": "coordinator:run_entry",
  "prior_open_decision_items": [],
  "grounds": "This run's append-only decision ledger was empty when the run root was provisioned, so no decision item is open at run entry. This declares the ledger's state; it declares nothing about any phase's judgement, which is produced at B2/B3 and is fail-closed there.",
  "scope": "Run entry only. It authorizes the first phase-entry transition and nothing after it."
}
```

* `state: CLEAR` with `reason_code: null` satisfies the contract (`decision_policy.py:1209-1212`),
  and the `CLEAR` entry condition is met through the **`no_open_decision_item`** predicate
  (`decision_policy.py:867`, entry clause `{"any_of": [...]}`) — the same predicate this run's own
  `plan_decision_record.json` already validates against.
* `iteration: 0` is **not new phase vocabulary**: it sits outside the `1..max_iterations` correction
  domain, `gate_attempts()` never sees it, and no `RUN_STATUS`/`round_kind`/`STATUS` value is added.
* `boundary ∈ {"B1","B2","B3"}` and `source ∈ {"coordinator:run_entry","worker","reviewer"}` are
  **closed sets** on OS-29's own ledger record — they are not phase, round or lifecycle vocabulary.
* **[F-001, iteration 3]** `ledger_schema_version: 1` is the record's **own** version, checked by
  **A4-i/A4-ii** against `decision_gate.SUPPORTED_LEDGER_RECORD_SCHEMA_VERSIONS`. It is the field
  iteration 2 omitted while A4 already demanded one; the next subsection says why it is a new object
  rather than a reuse of OS-28's constant.

**The B2/B3 agent ledger records carry the same field.** The declaration an agent emits (C10/R-P1)
becomes a ledger record of exactly this family, so A4 is a property of *every* record, not a special
property of the RED:

```json
{
  "ledger_schema_version": 1,
  "state": "CLEAR",
  "reason_code": null,
  "open_decision_item": false,
  "run": "<run_id>",
  "phase": "<phase>",
  "iteration": "<1..max_iterations>",
  "responsible_phase": "<phase>",
  "role": "worker",
  "boundary": "B2",
  "sequence": "<n>",
  "source": "worker",
  "grounds": "…",
  "scope": "…"
}
```

#### The ledger-record schema version, and why it is **not** OS-28's

**These are two different objects and they get two different constants.**

| | OS-28 `decision_policy.SUPPORTED_SCHEMA_VERSIONS` | OS-29 `decision_gate.SUPPORTED_LEDGER_RECORD_SCHEMA_VERSIONS` |
| --- | --- | --- |
| **Versions what** | the ```` ```policy-contract ```` **`decision_policy` block** a Skill ships — the states, reason codes, boundary elements and transition rules | one **decision ledger record** an OS-29 producer writes under the run artifact root |
| **Field it reads** | `schema_version`, a key of the policy block | `ledger_schema_version`, a key of the record |
| **Who enforces it** | `parse_decision_policy()` (`decision_policy.py:200-214`), at Skill load | **A4-i/A4-ii**, in `decision_gate.py`, at every gate boundary |
| **Declared where** | `decision_policy.py:42` | `decision_gate.py` (new, C1) |
| **Changes when** | the *contract* gains a state, code, element or rule | the *record* gains, renames or re-types a field |

**Why the OS-28 constant is deliberately not reused.** Three independent reasons, each verified in
this branch's working tree this phase:

1. **`validate_record()` never reads it.** `SUPPORTED_SCHEMA_VERSIONS` is referenced three times in
   the module — its definition at `decision_policy.py:42` and two uses at `:212` and `:214`, **both
   inside `parse_decision_policy()`**, i.e. exactly one consuming function. `validate_record()`
   (`decision_policy.py:1189-1292`) reads `state`, `reason_code`, the evidence sets, the declared
   facts and the grounds, and **never** a version of any kind. Iteration 2's evidence therefore
   proved the OS-28 state/evidence shape and **not** A4 — which is precisely the finding.
2. **The policy block cannot carry a record field even if that were wanted.** `parse_decision_policy`
   asserts the block's key set is **exactly** `STATE_SELECTION_INPUTS | DECLARATIVE_KEYS`
   (`decision_policy.py:216-222`), and the block is measured at **90/90** lines against
   `DECISION_POLICY_MAX_LINES` (P2, *Deliberately NOT changed*). Adding anything there fails CI on
   the next line.
3. **They must be free to move independently.** A ledger record can gain a field without the
   decision *contract* changing at all, and the contract can gain a reason code without any existing
   record becoming unreadable. One shared integer couples two release cadences that have no reason
   to be coupled, and would silently make every prior run's record "unsupported" the first time
   OS-30 adds a state.

**Where it lives, and the import direction.** Both constants and the A1–A6 function live in
**`decision_gate.py`** (C1). `run_logging.open_decision_ledger()` imports
`LEDGER_RECORD_SCHEMA_VERSION` from it to stamp what it writes. That edge is acyclic: C1 already
forbids `decision_gate` from importing `run_logging`, and W-1's static AST assertion enforces it.
The alternative — putting the constant in `decision_policy.py` — is rejected for the reason C1
already records: that module's docstring (`:22-26`) makes contract-only isolation a stated
invariant, and a *gate-input* version is not part of that contract.

**Compatibility rule, stated once.** A record whose `ledger_schema_version` this build does not
support is **refused** — never coerced, never treated as absent: a *newer* version means a newer
build wrote it and this build cannot be sure it understands the record; an *older* version, once one
exists, is handled by an explicit migration or refused. Both directions fail closed, and neither can
yield a `CLEAR`. This is demonstrated rather than asserted — case **D2** in `## Decision Record`
writes a version-2 ledger to disk and shows this build refuse it.

#### The admissibility rule — one function, used by all four B1 sites

`read_decision_ledger(run_id)` returns the run's records ordered by `sequence`. B1's input is the
**head** of that list, admitted only under **all six** of A1–A6. Each failure has its own closed
reason; none of them can produce a `CLEAR`.

| # | Rule | Violation ⇒ terminal reason |
| --- | --- | --- |
| **A1** | The ledger is **non-empty**. | `DECISION_GATE_INPUT_MISSING` — the producer did not run. Absence is a refusal, never a `CLEAR`. |
| **A2** | Exactly one record has `sequence == 0`, and it is the RED (`source == "coordinator:run_entry"`, `boundary == "B1"`, `run == run_id`). | `DECISION_LEDGER_INCONSISTENT` |
| **A3** | **Head selection.** If `len(L) == 1` the head is the RED, and it is admissible **only** when this is the run's first B1 (no settled agent record exists for this run). If `len(L) > 1` the head must have `source ∈ {"worker","reviewer"}`, `boundary ∈ {"B2","B3"}`, and its `(run, phase, iteration)` must equal the round that just settled. That expected round is an **argument supplied by the B1 caller** from the harness's own in-memory round state (`None` at the run's first B1) — it is never read back off the ledger, which is what makes A3 a binding check rather than a restatement of the file. | `DECISION_GATE_INPUT_UNBOUND` |
| **A4** **[F-001]** | **Every** record in `L`, in four explicit clauses. **A4-i** it declares `ledger_schema_version` and the value is an `int` (a `bool` is not an `int`). **A4-ii** that value is in `decision_gate.SUPPORTED_LEDGER_RECORD_SCHEMA_VERSIONS` — the **record** constant, *not* `decision_policy.SUPPORTED_SCHEMA_VERSIONS`, which versions the policy block. **A4-iii** it passes `decision_policy.validate_record()`. **A4-iv** the sequences are a gapless `0..n-1`. | A4-i → `DECISION_GATE_INPUT_MALFORMED`; **A4-ii → `DECISION_LEDGER_SCHEMA_UNSUPPORTED`** (new closed reason, W-7); A4-iii → `DECISION_GATE_INPUT_MALFORMED`; A4-iv → `DECISION_GATE_INPUT_MALFORMED` |
| **A5** | No record in `L` carries `state ∈ {NEEDS_INPUT, CONFLICT}` with `open_decision_item: true` that a later record has not resolved. A "resolution" is only a record whose transition passes `validate_transition()` — unreachable today (L1/L6). | `DECISION_BLOCKED:<state>:<reason_code>`, naming the open record's ledger key |
| **A6** | The RED's `prior_open_decision_items` **equals** the set A5 recomputes from the on-disk ledger. The declaration is re-checked, never trusted. | `DECISION_GATE_DECLARATION_DISAGREES_WITH_LEDGER` |

**Evaluation order, so the reported reason is deterministic [F-001].** All six must hold; when more
than one fails at once the reason reported is the first in this order:
**A1 → A2 → A4 → A3 → A6 → A5.**
A4 precedes A3 because an unreadable ledger cannot be head-selected from. **A6 precedes A5** because
a declaration that disagrees with the ledger is a defect of the *producer*, and naming it
`DECISION_BLOCKED` instead would hide a lying RED behind the block it failed to declare — this is
what makes F10's expected reason reproducible rather than order-dependent. No ordering changes any
*outcome*: every one of the six refusals is terminal and none can yield a `CLEAR`.

**A3 is the clause that stops this becoming a hole.** The RED is admissible at exactly one position:
`len(L) == 1`, before any agent record exists. At every later boundary the head must be the settled
`B2`/`B3` record of the round that just finished. So when a later record is missing, the head is the
*previous* boundary's record, its `(run, phase, iteration)` does not match the round that just
settled, and B1 blocks with `DECISION_GATE_INPUT_UNBOUND`. **There is no fallback to the RED and no
code path that reaches `CLEAR` from a shorter ledger.** F11 below is the fixture that proves it.

#### How existing fully-`CLEAR` runs obtain an explicit `CLEAR`

The RED is written **by the harness at run open**, not by an agent, so every one of the ~1496
existing tests gets its explicit sequence-0 `CLEAR` with **no fixture edited**. The agent-emitted
declarations (C10 / R-P1) cover B2 and B3 only. No existing check is relaxed anywhere: the single
behaviour change remains the one already recorded under *Rollback / no-op guarantees* — a run that
declares nothing at B2/B3 no longer proceeds.

#### Validation — positive, negative, and non-vacuous

| Fixture | Shape | Asserts | Module |
| --- | --- | --- | --- |
| `red_present_first_phase_dispatches` | **positive** | the first phase dispatches; `sessions` is non-empty; `phase_iterations` advances normally. Doubles as **NV-1's control** | `test_e2e_harness.py` |
| **F9** `red_absent` | negative | delete the sequence-0 record before `run_workflow` ⇒ `final_status == BLOCKED`, `reason == DECISION_GATE_INPUT_MISSING`, `sessions == ()`, every `phase_iterations[p] == 0`, `correction_dispatches == []`. **Co-located control**: the same scenario with the RED present *does* dispatch | `test_e2e_harness.py` |
| **F10** `red_disagrees_with_ledger` | negative | reuse a `run_id` whose directory already holds an open `NEEDS_INPUT` record, hand-write a RED claiming `prior_open_decision_items: []` ⇒ `DECISION_GATE_DECLARATION_DISAGREES_WITH_LEDGER`. Makes A6 non-vacuous — a real, reachable case, because `run_id` is scenario-supplied and the root is re-provisioned at `:1490` | `test_e2e_harness.py` + `test_run_logging.py` |
| **F11** `red_offered_at_later_boundary` | negative — **the hole proof** | run one phase to a settled `B2`/`B3` record, delete that record so the RED is the head again, then hit the next B1 ⇒ `DECISION_GATE_INPUT_UNBOUND`, **not** admitted. **Co-located control**: with the settled record present the same run proceeds | `test_e2e_harness.py` |
| **F12** `two_sequence_zero_records` | negative | a second RED appended ⇒ `DECISION_LEDGER_INCONSISTENT`; and `open_decision_ledger()` called twice writes **one** record | `test_run_logging.py` |
| **F13** **[F-001]** `ledger_record_schema_version_missing` | negative — **A4-i** | the exact RED with `ledger_schema_version` deleted ⇒ `DECISION_GATE_INPUT_MALFORMED`, first phase does not dispatch. Three shape variants in the same parametrized case: absent, `"1"` as text, and `True` (a `bool` must not pass as an `int`). Plus the **cross-object control**: a record carrying only the policy-block key `schema_version` is still `DECISION_GATE_INPUT_MALFORMED` — the two fields are not interchangeable | `test_decision_gate.py` (A4) + `test_e2e_harness.py` (the end-to-end refusal) |
| **F14** **[F-001]** `ledger_record_schema_version_unsupported` | negative — **A4-ii** | `ledger_schema_version` set to `max(SUPPORTED_LEDGER_RECORD_SCHEMA_VERSIONS) + 1` ⇒ `DECISION_LEDGER_SCHEMA_UNSUPPORTED`, terminal, **never** `CLEAR`. Run **twice**: once on the RED, once on a **B2 agent record** while the RED is valid, so A4 is proved to be a property of every record and not only of sequence 0. **Co-located control**: the same ledger with the supported version *does* admit | `test_decision_gate.py` + `test_run_logging.py` (the on-disk round trip) |
| `live_red_written_at_start_run` / `live_red_absent_refuses` | positive + negative | `start_run` (`:1529`) writes the RED; deleting it before `run_existing_task` refuses **before `start_worker`** — no Dispatch id is created — through the existing `_log_pre_dispatch_failure` `:2265` | `test_orca_runtime_contract.py` |

**F13/F14 are the fixtures iteration 2 lacked.** They are what make A4 non-vacuous: without them,
"the RED is admissible" is also true of a build that never reads a record version at all —
which is exactly the state `decision_policy.validate_record()` is in today.

These extend, and do not replace, scenario 13's F1–F8 table: F1–F8 are the ticket's eight forbidden
presumption sources at all three boundaries; **F9–F14** are B1's **input-admissibility** negatives.
**F13 and F14 are the F3 ("unsupported schema") half that applies to the ledger record**; F3's other
half — the Skill's policy contract block — is already enforced by `parse_decision_policy()` and is
unchanged by OS-29.

---

### P6b. The single decision transition table  **[resolves F-002]**

#### The defect this fixes

Iteration 1's W-3 said the B2 guard terminates the round at low, medium **and** high; W-4 said the
same `NEEDS_INPUT`/`CONFLICT` Worker result at MEDIUM/HIGH continues into the already-scheduled
Reviewer and terminates from the Reviewer branch. Both cannot hold at one code location. This
section replaces both with one rule; W-3, W-4, W-6, the execution order and scenarios 3, 4, 5, 6,
10 and 12 are aligned to it and state nothing on their own.

#### How the rule is derived from the objective's own words

The objective says the current-phase Reviewer **"MAY verify the classification and its grounds"**;
"MAY" is a permission, so the determinism has to come from *this* document. Three of its other
sentences fix the answer:

1. *"Only a Reviewer dispatch that verifies the CURRENT phase's decision classification may be
   permitted"* — the classification-verifying Reviewer is the **one** dispatch allowed after a
   block, so where one is **already scheduled in the same round** the cheapest correct design is to
   let it run in verification mode rather than to add anything.
2. At LOW there **is no phase Reviewer**: `e2e_harness.py:1070` returns before the Reviewer half.
   LOW therefore has no choice and must terminate at B2.
3. *"Risk NEVER expands decision authority"* — so whichever path a round takes, **the terminal
   outcome must be identical**. It is: same `final_status`, same `decision_state`, same
   `reason_code`; only the attached evidence differs. That equality is scenario 7's assertion, not
   a claim in prose.

#### Ordering rules (the whole ordering, in three lines)

* **O-1 — boundary order.** `B1 → Worker dispatch → B2 → [Reviewer dispatch] → B3 → quality routing`.
  Unchanged from P1/RU-1; no new dispatch site, no new subprocess site, no new round.
* **O-2 — axis order, at every boundary.** Parse and validate the gate result (fail-closed) →
  evaluate the **decision** axis → only if the decision axis admits, evaluate the existing
  **quality** axis. The decision axis can only *admit the existing routing* or *terminate*; it never
  creates a PASS. This is what makes "separate axes" operational, and it is why the B2 guard sits
  **above** the `STATUS: BLOCKED` branch at `:1015`: a Worker that discovers a blocking decision
  mid-work and reports it must be accounted on the decision axis (no iteration), not swallowed as a
  generic `WORKER_BLOCKED` (which at LOW would charge one).
* **O-3 — risk-specific Reviewer participation.** The two tables below. Risk selects *where the
  terminal block is recorded and what evidence it carries*, never *whether* it is terminal.

#### B2 — after the Worker result (`e2e_harness.py`, between `:1012` and `:1015`)

| # | Worker gate result | LOW | MEDIUM / HIGH |
| --- | --- | --- | --- |
| 1 | `CLEAR` / `ASSUMPTION_ALLOWED`, valid | **admit** → the existing LOW gate return at `:1070`, unchanged | **admit** → the existing Reviewer dispatch at `:1158` in **normal** mode → B3-N |
| 2 | `NEEDS_INPUT` / `CONFLICT`, valid | **TERMINAL at B2.** `final_status = BLOCKED`, `reason = DECISION_BLOCKED:<state>:<code>`. No Reviewer exists at LOW (`:1070`) | **not terminal here.** Set `verification_only`; append the B2 ledger record with `verdict: block_pending_verification`; **fall through** to the *already-scheduled* Reviewer at `:1158` → **B3-V** |
| 3 | missing / malformed / unsupported schema / unknown state / unknown reason code / undeclared safety fact | **TERMINAL at B2**, fail-closed reason from P6's F1–F6 | **TERMINAL at B2**, same reason. **No Reviewer is dispatched**: verification mode requires a *valid* classification to verify, and there is none |

Row 3 is deliberately risk-independent: it is the one case where MEDIUM/HIGH must **not** spend the
Reviewer, because there is nothing for it to bind to.

**What the verification-mode Reviewer receives.** The classification-verification framing rides in
the **existing** `new_claims` / `drill_down` keys of `build_reviewer_context` (RU-6,
`task_context.py:343`); `REVIEWER_CONTEXT_KEYS` stays the closed eight-tuple asserted at
`orca_runtime_harness.py:2483-2484`.

#### B3 — after the Reviewer result (`e2e_harness.py:1198` PASS / `:1210` FAIL; Final Review T1 `:1565`)

| # | Mode | Reviewer gate result | Outcome |
| --- | --- | --- | --- |
| 4 | **B3-V** | valid, **bound**, and it **confirms** the Worker's classification | **TERMINAL BLOCKED**, `reason = DECISION_BLOCKED:<worker state>:<worker code>` — byte-identical to row 2's LOW terminal |
| 5 | **B3-V** | valid, bound, and **stricter** (e.g. Worker `NEEDS_INPUT` → Reviewer `CONFLICT`, or the Worker's grounds are rejected) | **TERMINAL BLOCKED** with the **Reviewer's** state and code. A verification may only move *toward* more blocking, never away |
| 6 | **B3-V** | valid, bound, and proposes a **downgrade** to `CLEAR` / `ASSUMPTION_ALLOWED` | Decided **only** by `decision_policy.validate_transition(policy, worker_state, reviewer_state, reviewer_record)`. OS-29 writes no downgrade rule of its own. `NEEDS_INPUT`/`CONFLICT` → `ASSUMPTION_ALLOWED` is **`forbidden`** unconditionally in the shipped contract; `→ CLEAR` is **`requires_user_decision`**, which no in-run channel can satisfy while OS-30 is absent. Rejected ⇒ **TERMINAL BLOCKED**, `reason = DECISION_DOWNGRADE_REJECTED`. Accepted (only reachable if a future OS-30 supplies a conforming `user_decision`) ⇒ the acceptance is **recorded** and the round is **still TERMINAL BLOCKED** — acting on it would be resume, which is OS-31 (**L6**) |
| 7 | **B3-V** | missing / malformed / **not bound** to the Worker's B2 ledger key | **TERMINAL BLOCKED**, `DECISION_GATE_INPUT_MISSING` / `..._MALFORMED` / `..._UNBOUND`. Deliberately *not* a silent fall-back to the Worker's classification: both outcomes block, but only this one makes the defect visible |
| 8 | **B3-N** | `NEEDS_INPUT` / `CONFLICT` discovered by the **Reviewer** | **TERMINAL BLOCKED** on the decision axis, **no iteration charged**. The Reviewer's `RESULT: FAIL` and its blocking finding are still parsed, still recorded in `finding_traces` and still written to the ledger — the Jira AC "the phase Reviewer can FAIL it as a blocking finding" is satisfied by the recorded finding, and the ORIGINAL_REQUEST's "`NEEDS_INPUT`/`CONFLICT` … do NOT consume a correction iteration" is satisfied by the accounting. **This is scenario 6, and it is a consequential change from iteration 1**, which had scenario 6 routing into the correction loop |
| 9 | **B3-N** | `CLEAR` / `ASSUMPTION_ALLOWED`, valid | **The existing routing, untouched**: PASS at `:1198` → next phase; FAIL at `:1210` → correction loop, iteration charged as today |
| 10 | **B3-N** | missing / malformed | **TERMINAL BLOCKED**, fail-closed reason (P6 F1–F5) |

**Binding (rows 4–7).** The Reviewer's verification record carries
`verifies: {run, phase, iteration, worker_record_key}`, where `worker_record_key` is the ledger key
of the Worker's B2 record. That is a reference to a *ledger record*, which the audit needs; it is
**not** a supersession link between two decisions, which is OS-30's (**L3**, **R-10**). A record
whose `verifies` does not resolve to this round's B2 record is row 7.

**A `PASS` that carries `NEEDS_INPUT` is self-contradictory, and O-2 already decides it**: the
decision axis is read first, so the round blocks (row 8). No extra rule is needed and none is added.

#### What this rule guarantees, restated against the objective

| Requirement | Where it is satisfied |
| --- | --- |
| the correction Worker is not dispatched | Rows 2/4–8 return a `final_status != completed_status`, so `run_workflow`'s phase-gate branch at `:1524` returns `snapshot(...)` immediately. Correction rounds are only reachable from the Final-Review T4 path (`:1611-1668`), which is never entered. **In code**, not by convention |
| the next phase is not dispatched | Same return; the `for phase in scenario.phases` loop at `:1509` exits |
| only a current-phase classification-verifying Reviewer may run | It is the **already-scheduled** Reviewer at `:1158`, in the same round, for the same `(phase, iteration)`. INV-D1 (≤ 1 reviewer event per iteration), INV-D2 and INV-D3 (exactly 2 subprocess sites, 3 round-dispatch sites) all still hold, and **M-DUP still fails them** |
| the blocked outcome is terminal | Every row 2/4–8 outcome is a terminal `BLOCKED`; L6 makes even a validly-authorized downgrade terminal in OS-29 |
| no correction iteration is consumed | W-6's single `gate_attempts()` edit keyed on `decision_block`, which is set by rows 2, 4–8 alike — covering the LOW shape (Worker attempt appended at `:1012`) and the MEDIUM/HIGH shape (Reviewer attempt appended at `:1194`) with one rule |
| risk does not expand decision authority | Rows 2 and 4 produce the **same** `final_status`, `decision_state` and `reason_code`. Asserted as an equality across low/medium/high in scenario 7, with the co-located guard that the three runs differ elsewhere |

---

### P7. Provenance plan

**Where each of the thirteen required fields lands** (⬤ = already carried, ◐ = partial today,
○ = new):

| Field | Today | Where OS-29 puts it |
| --- | --- | --- |
| run | ⬤ | run-id directory identity (`run_logging.py:306-345`), `requested_phases` column `:77` |
| phase | ⬤ | `ORCHESTRATOR_LOG_COLUMNS` `phase` `:65`; TIMING `:90` |
| iteration | ⬤ | `iteration` column `:67`; TIMING `:92` |
| Worker/Reviewer role | ⬤ | `role` column `:66` |
| verdict | ⬤ | `gate_result` `:72`, `review_verdict` `:73`, parsed at `orca_runtime_harness.py:595` / `:626` |
| timestamp | ⬤ | `timestamp` column `:64`; `recorded_at` in the record |
| responsible phase | ◐ | parsed today (`e2e_harness.py:79` `RESPONSIBLE_PHASE_LINE`, `:496` `parse_final_review_output`, routed `:1592-1605`) but **not a column** → becomes a **record field** |
| source binding | ◐ | `task_id`/`dispatch_id`/`terminal` columns `:68-70` → the record additionally binds the decision item to its **artifact path and phase** (`task_context.phase_artifact_contract:284`) |
| **decision state** | ○ | **sparse column** + record field |
| **reason code** | ○ | **sparse column** + record field |
| **evidence** | ○ | record field (the contract's `required_evidence` set) |
| **assumption** | ○ | record field (the six `declared_safety_facts`) |
| **open question / conflict** | ○ | record field |

**[F-001]/[F-002] The ledger-mechanics fields are additional to the thirteen, never a substitute
for them.** P6a and P6b add `ledger_schema_version` (the **record's own** version, closed by
`decision_gate.SUPPORTED_LEDGER_RECORD_SCHEMA_VERSIONS` and checked by A4-i/A4-ii — **not** the
policy block's `schema_version`, which OS-28 owns and `parse_decision_policy()` alone enforces),
`boundary` (closed: `B1`/`B2`/`B3`), `sequence` (the gapless `0..n-1` order the admissibility rule
A2–A4 reads), `source` (closed: `coordinator:run_entry`/`worker`/`reviewer`),
`prior_open_decision_items` (the run-entry declaration's re-checkable claim, A6) and `verifies` (the
B3-V binding to the Worker's B2 record key). All six are *mechanics of the ledger*, and every one of the thirteen required fields above
is still carried on every record. `verifies` is a reference to a ledger record, not a link between
two decisions: supersession lineage stays OS-30's (**L3**).

**Two channels, one authority.** The machine-readable record is the authority; the Markdown
`## Decision Record` section is the human explanation. This mirrors the existing precedent where
`run_logging.parse_final_review_report` (`:1549`) reconciles a human report against its audit
record.

**The anti-drift validator, and why this run is its best fixture.** A validator must reject a
Markdown summary that contradicts its machine record. **This run already produced exactly that
failure**: the iteration-1 ANALYSIS wrote `REASON_CODE: (none — CLEAR carries no reason code)` in
Markdown — prose that reads as correct — while the same value is rejected by `validate_record()`
for state `CLEAR` (finding F-001, resolved in `ANALYSIS.md` "Review Feedback Resolution"). That
string is therefore a **required negative fixture**: a document whose Markdown looks right and
whose machine record is invalid must fail the validator, not pass it.

**Ledger properties, inherited from `write_final_review_audit_record` (`run_logging.py:2120`), not
re-invented:** a closed required-field set (`_REQUIRED_RECORD_FIELDS` `:2358-2372` is the model),
staged-then-published by a single `os.rename` (`_stage_and_publish_audit_record` `:1779`) so a
published name is a complete record, deterministic redaction (`redact_text` `:1129`), and
**append-only**: "the record is complete when it is written and is never edited … correcting a
record means writing a new record under a new key" (`:2148-2151`). **Decision ID and lineage go
exactly this far and no further** — a superseding decision is a *new record*, never a link back to
the old one, because supersession lineage is OS-30's (limitation L3).

---

## Dependencies / Execution Order

**Order of work that minimizes rework** (P8's second half). The ordering principle is: settle the
things that constrain everything downstream *first*, and touch the shared, CI-load-bearing text
*last*.

```text
W-1  decision_gate module            (no dependencies; fixes the vocabulary everything else uses)
 └─ W-2  decision channel + record shape        (needs W-1's parser surface)
     ├─ W-2b B1 producer + ledger reader        [F-001] (needs W-2's record shape; must precede
     │        open_decision_ledger/read_...      W-5 and W-8 -- neither B1 guard has an input
     │                                           until the producer and reader exist)
     │   ├─ W-5  B1 guards at the 3 dispatch sites  (needs W-2b, not just W-2)
     │   └─ W-8  live-path pre-start_worker guard   (needs W-2b; parallel to W-5)
     └─ W-3  B2 guard in run()                  (needs W-2; implements P6b rows 1-3 only)
         ├─ W-4  B3 guards, modes B3-V and B3-N (needs W-3: verification_only is set at B2)
         └─ W-6  gate_attempts() non-consumption (needs W-3 AND W-4: both P6b terminal shapes
                  must exist before the counter rule can be written once and be right)
W-7  terminal outcome + sparse columns          (needs W-3/W-4/W-5: the reason constant is only
                                                 knowable once all three block shapes exist)
 └─ W-9  decision ledger                        (needs W-7's field set)
     └─ W-11 validators incl. Markdown↔machine  (needs W-9's record shape)
W-10 Skill text + tenth anchor contract         (LAST of the production items: the anchor block
                                                 must describe behaviour that already exists)
W-12 tests                                      (written alongside W-3..W-9; completed after W-11)
W-13 CHANGELOG / ROADMAP / limitations          (last)
```

**Why this order minimizes rework.** Two sequencing decisions are load-bearing.

**W-2b before W-5/W-8** **[F-001]**: iteration 1 made W-5 depend on W-2 alone, which is how a B1
guard ended up with no input at the first boundary. The producer and the ledger reader are now an
explicit prerequisite of every B1 guard, so a B1 site cannot be written before the thing it reads
exists.

**W-6 after W-3 *and* W-4** **[F-002]**: the counter rule has to cover *both* P6b terminal shapes —
the LOW round that ends at B2 with a Worker attempt appended (`:1012`), and the MEDIUM/HIGH round
that ends at B3-V after the verification Reviewer attempt was appended (`:1194`). Writing it after
only one of them produces a rule that is silently different at LOW — the exact V3/R-2 defect. This
dependency was stated in iteration 1 but was not *true* of iteration 1's W-3/W-4, which each
claimed the same terminal; under P6b the two shapes genuinely exist and the dependency is real.

W-10 last because an anchor-contract block is a *description* of runtime behaviour; writing it
first guarantees a second edit.

**Hard constraints carried from ANALYSIS** (all re-verified this phase): C-1 the shared block is at
90/90; C-2 `left.raw == right.raw`; C-3 `RUN_STATUS_VALUES` raises, not logs; C-4 `ROUND_KIND_VALUES`
validated per settled dispatch; C-5 `_find_choice` asserts the exact worker pair; C-6 six
byte-anchored prose sentences survive; C-7 logging never mutates a lifecycle judgement (`_safe_log`
`orca_runtime_harness.py:2076`; `e2e_harness.py:1387-1389`); C-8 the T2 last-attempt guard stays
first on the FAIL edge; C-9 dispatch ledgers write before any verdict and are never rewound;
C-10 the five CI gates.

---

## Validation / Test Plan

### P4. The fourteen required scenarios, one-to-one

**Placement principle, taken from the repository's own precedent** (`test_os22_required_tests.py:1-15`):
each case goes in the module that **owns its subject**, and only the cross-cutting residue goes in a
dedicated module. Fixtures follow `scripts/fixtures/decision_policy/{valid,invalid}` (used from
`test_decision_policy.py:45`) as `scripts/fixtures/decision_gate/{valid,invalid}`.

**Rows 3, 4, 5, 6, 10 and 12 are stated against P6b's tables and rows 1, 12 and 13 against P6a's
admissibility rule; where a row changed in iteration 2 it carries [F-001] or [F-002].** No row was
weakened and none was removed.

| # | Scenario | Positive fixture | Negative fixture (the paired half) | Test module | Why there |
| --- | --- | --- | --- | --- | --- |
| 1 **[F-001]** | `CLEAR` + Review PASS → next phase | `clear_pass_proceeds` — B1 admits on the RED at the first phase (P6a A1–A6) and on the settled B3 record at every later phase | `clear_pass_but_absent_record` (no B2/B3 gate result ⇒ must NOT proceed) **and** `red_absent` (F9: no run-entry declaration ⇒ the *first* phase must not dispatch at all) | `test_e2e_harness.py` | The subject is a **transition**; `WorkflowRunResult` (`e2e_harness.py:270`) is where "did the next phase dispatch" is a list-length assertion |
| 2 | `ASSUMPTION_ALLOWED` + valid grounds → record, then proceed | `assumption_allowed_six_facts_declared` | `assumption_allowed_one_fact_undeclared` (⇒ blocked, via `_undeclared_safety_facts` `decision_policy.py:803`) | `test_e2e_harness.py` (transition) + `test_run_logging.py` (the record was written) | Two subjects, two owners |
| 3 **[F-002]** | `NEEDS_INPUT` → correction Worker **and** next phase blocked, **iteration NOT consumed** | `needs_input_blocks`, run at **low** (P6b row 2, terminal at B2) and at **medium/high** (P6b row 2 → row 4, terminal at B3-V). A single parametrized assertion that all three produce the **same** `final_status`, `decision_state` and `reason_code` | `quality_fail_consumes_iteration` — the **co-located control**: a FAIL round must still increment `phase_iterations` | `test_e2e_harness.py` | Assert `correction_dispatches == []` (`:275`), `revalidation_dispatches == []` (`:280`), `phase_iterations[p]` unchanged (`:272`) — all on the returned value, not a log grep. At medium/high additionally assert `len(reviewer_attempts) == 1` (INV-D1: the verification is the already-scheduled Reviewer, not a second one) and at low `reviewer_attempts == []` |
| 4 **[F-002]** | `CONFLICT` → correction Worker and next phase blocked | `conflict_requirement_contradiction` (P6b row 2 → row 4) | `conflict_downgraded_without_grounds` — P6b **row 6**: a Reviewer proposing `CONFLICT → ASSUMPTION_ALLOWED` is rejected by `validate_transition()` as **`forbidden`** unconditionally, and one proposing `CONFLICT → CLEAR` is rejected as `requires_user_decision` with no conforming `user_decision`; both ⇒ `DECISION_DOWNGRADE_REJECTED`, still terminal. Plus **row 7**: an unbound verification record ⇒ `DECISION_GATE_INPUT_UNBOUND` | `test_e2e_harness.py` + `test_decision_gate.py` | Same ledgers as 3. The transition verdict is asserted to come from `validate_transition()` (OS-29 adds no downgrade rule of its own) |
| 5 **[F-002]** | High-impact decision found **during IMPLEMENTATION** → blocked outcome, not completion | `implementation_midwork_block` (phases=`(implementation,)`), asserted at HIGH through P6b row 2 → row 4, and at LOW through row 2's B2 terminal | `implementation_same_item_declared_clear` (⇒ completes) | `test_e2e_harness.py` | The claim is "the phase did not complete", which is `final_status` on `WorkflowRunResult`. Co-located: the Worker reported `STATUS: BLOCKED` **and** a decision block, and O-2 routes it to the decision axis — `reason` must be `DECISION_BLOCKED:…`, **not** `WORKER_BLOCKED`, and `phase_iterations` must be unchanged (under `WORKER_BLOCKED` at LOW it would be charged) |
| 6 **[F-002]** | Worker auto-approves a high-impact decision without authority → Reviewer FAILs it as **blocking** | `worker_unauthorized_high_impact`: the Worker's gate result is `CLEAR`, so B2 **admits** (row 1) and the Reviewer runs in **normal** mode; the Reviewer emits `RESULT: FAIL` with a blocking finding **and** a gate result of `NEEDS_INPUT`/`CONFLICT` ⇒ **P6b row 8**: terminal `BLOCKED`, **no iteration charged**, and the blocking finding still recorded in `finding_traces` | `worker_high_impact_with_explicit_authorization` (⇒ CLEAR, proceeds) **and** the axis-separation control: a Reviewer `RESULT: FAIL` whose own gate result is `CLEAR` ⇒ **row 9**, the existing correction routing, iteration **charged** | `test_e2e_harness.py` (the routing, both rows) + `test_decision_gate.py` (the INV-4 classification defect) | INV-4 has no exception (`SKILL.md` Decision Policy section, anchor "INV-4에는 예외가 없다", `validate_skills.py:716`). **Changed in iteration 2**: under P6b row 8 this scenario terminates on the decision axis instead of entering the correction loop. Both AC sentences still hold — the Reviewer *did* FAIL it as a blocking finding, and a user-decision block *does not* consume a correction iteration |
| 7 | LOW risk → decision authority **not** expanded | `same_record_at_low_medium_high` | co-located non-vacuity guard: the three runs must **differ elsewhere** (e.g. `reviewer_gates_skipped` `:286` is non-empty only at LOW) | `test_decision_gate.py` (**P1**: `inspect.signature` has no `risk`/`profile` parameter; **P2**: equality across levels) + `test_e2e_harness.py` (**P3**: a LOW `NEEDS_INPUT` run actually blocks) | P1 mirrors `test_decision_policy.py:1244-1300`, whose `:1298` comment states the anti-vacuity move: proving risk is **inert**, not merely absent |
| 8 | Downstream expands an existing user decision → a **new decision event or escalation** is required | `downstream_expands_decision` | `downstream_within_original_decision` (⇒ no new event required) | `test_e2e_harness.py` (T5a path `:1691-1721`) + `test_decision_gate.py` (the drift rule) | **Must assert the escalation, never a lineage link** (L3 / R-10) |
| 9 | Final Review with an unresolved decision → completion **forbidden** | `final_review_unresolved_decision` | `final_review_all_decisions_resolved` (⇒ `COMPLETED`) | `test_e2e_harness.py` (T1 branch `:1565`) | The Final Review is where "no unresolved decision" becomes a completion condition |
| 10 **[F-002]** | Worker and Reviewer agree on the same unauthorized assumption → **no** automatic approval | `worker_reviewer_agree_unauthorized` — exercised through **P6b row 6**, where the agreement is the *only* thing offered as grounds for the downgrade and `validate_transition()` rejects it; asserted at medium and high (at LOW there is no Reviewer to agree with, which is L5) | precheck that the injected value **really is** `worker_reviewer_agreement` from the closed list, on the `test_decision_policy.py:2157-2178` pattern | `test_decision_gate.py` + `test_e2e_harness.py` end-to-end | `forbidden_authority_sources` is a closed five-item set, so the loop over it carries `assertEqual(len(...), 5)` as a co-located cardinality guard |
| 11 | Timeout / non-response → no approval **and** no iteration consumption | `timeout_no_response` | `quality_fail_consumes_iteration` (reused control) | `test_decision_gate.py` (no approval) + `test_e2e_harness.py` (no iteration charge) | Two distinct claims, two owners |
| 12 **[F-001]** | Illegal dispatch attempt after a blocking decision → **fail closed** | `illegal_dispatch_after_block` exercised at **all four** B1 sites (`:1520`, `:1612`, `:1692`, `:1531`), each blocking under P6a **A5** on the open ledger record the blocked round wrote | `guard_removed_mutant` — with the guard removed, the same scenario **does** dispatch — **plus F11** `red_offered_at_later_boundary`, which proves A3 refuses to fall back to the run-entry declaration when a later record is missing | `test_e2e_harness.py` + `test_orca_runtime_contract.py` (the live pre-`start_worker` half) | The mutant is the non-vacuity half: without it, "nothing was dispatched" also passes when nothing is ever dispatched (R-9) |
| 13 **[F-001]** | Missing or malformed decision result → **no `CLEAR` presumption** | one passing fixture | **eight** negative fixtures F1–F8 (the ticket's eight forbidden presumption sources, at all three boundaries), each differing from the passing one in exactly one source, each with a precheck that the injected value genuinely is that forbidden source, **plus P6a's six B1 input-admissibility negatives F9–F14**. **[F-001, iteration 3]** F3 ("unsupported schema") is exercised on **both** of its objects: the policy contract block (`parse_decision_policy`, existing) **and** the ledger record (**F14**, A4-ii, `DECISION_LEDGER_SCHEMA_UNSUPPORTED`), with **F13** covering the missing/mistyped-version half (A4-i). The passing fixture is the **exact RED of P6a**, admitted through the **complete A1–A6 path** — not through `validate_record()` alone | `test_decision_gate.py` (F1–F8, A1–A6, F13/F14), `test_e2e_harness.py` (F9–F11, F13 end-to-end), `test_run_logging.py` (F10/F12 producer half, F14 on-disk round trip) | The parser/evaluator owns F1–F8 and A1–A6; fixtures live under `scripts/fixtures/decision_gate/invalid/`. F9–F14 are placed with the boundary they defend, per the same placement principle |
| 14 | Decision-semantics drift between the two Skills → **validation failure** | validator passes on the shipped pair | (a) mutate one Skill only ⇒ FAIL; (b) delete a mirrored sentence from **both** ⇒ FAIL; (c) copy the orchestration-only gate block into the loop Skill ⇒ FAIL | `test_validate_skills.py` | It already owns `edit_skills(...)`-style mutation regressions (`:104-262`), and its copied-module list at `:49-66` must gain `decision_gate.py` |

### The three non-vacuity proofs (mandatory, not bonus)

| Proof | Construction | The co-located control that makes it non-vacuous |
| --- | --- | --- |
| **NV-1 dispatch blocking** | Remove the B1 guard (or force the state to `CLEAR`) and assert the previously-blocked scenario now appends to `correction_dispatches` / `revalidation_dispatches` | In the `CLEAR` control run **the same ledgers must be non-empty** — otherwise "empty" proves only that nothing ever dispatches. **[F-001]** The control is `red_present_first_phase_dispatches`: it is the run whose only difference from F9 is the presence of the run-entry declaration, so "nothing dispatched" in F9 is attributable to the guard and to nothing else |
| **NV-2 iteration non-consumption** | Assert `phase_iterations[p]` unchanged across the blocked round | Co-locate the assertion that a quality-`FAIL` round **increments** it — otherwise "unchanged" also passes with a globally broken counter |
| **NV-3 non-duplication — mutation M-DUP** | Subclass `E2EHarness` and, after the Reviewer PASS branch at `:1198`, dispatch the phase Reviewer a **second** time for the same `(phase, iteration)` **while reusing the existing `phase_gate` label**. Then, in **one** test function and in this order: (1) **anti-vacuity precheck** — `assertGreater` on the mutant's `sessions` length and its `role == "reviewer"` count versus the unmutated control ("the mutation must not be a no-op", the `test_e2e_harness.py:4762-4810` pattern); (2) **proof the proxy is blind** — `assertEqual` that the observed `round_kind` set is *still* exactly the scenario's subset of `run_logging.ROUND_KIND_VALUES`, i.e. the four-value assertion **still passes on the mutant**; (3) **the invariant must fail** — INV-D1 and INV-D3 reject the mutant | The unmutated run, on which the same invariant checker must return **clean** — otherwise "it rejects the mutant" also holds for a checker that rejects everything |

**The invariants NV-3 asserts** (from approved ANALYSIS A1; ledger locations re-verified this phase):

* **INV-D1 — per-iteration role cardinality.** Within one `E2EHarness.run()` execution the
  `sessions` ledger (`_record_session` `:835`, appended at `:973` for the Worker and `:1148` for the
  Reviewer) holds exactly one `role="worker"` event per iteration and at most one `role="reviewer"`
  event per iteration; equivalently `len(reviewer_attempts) <= len(worker_attempts)` (`:1012`,
  `:1194`).
* **INV-D2 — no dispatch after a blocking decision.** No `sessions` event is ordered after the
  settling event, and `correction_dispatches` (`:1632`) / `revalidation_dispatches` (`:1709`) gain
  no entry for the blocked phase. Control: the matching `CLEAR` run, where both are non-empty.
* **INV-D3 — dispatch-site cardinality.** The round-dispatch call sites stay exactly three (`:1520`,
  `:1625`→`:1405`, `:1705`) and the agent-invoking `subprocess.run` sites inside `run()` stay
  exactly two (Worker `:981`, Reviewer `:1158`) — a static assertion over the module's AST.

`ROUND_KIND_VALUES` staying at four (`run_logging.py:111-116`) is retained **only** as supplementary
evidence for the separate out-of-scope item "no new phase vocabulary". It is explicitly **not** the
non-duplication proof — M-DUP step (2) makes that demotion a fact of the test suite rather than a
claim in prose.

### Regression and CI

* Targeted: `python3 -m unittest scripts.test_decision_gate scripts.test_os29_decision_gate scripts.test_e2e_harness scripts.test_validate_skills`.
* Full CI, exactly as `.github/workflows/*.yml:33-48`: `validate_skills.py`; `python3 -m unittest
  discover -s scripts -p 'test_*.py'`; `verify_package.py`; `build_release.py` + archive
  verification; `git diff --check`.
* **No existing test or validator may be deleted or weakened.** Where an existing test must change
  because the fake agents now emit a decision declaration (Risk R-P1), the change is *additive to
  the expected output*, and the diff for each such site must be justified in the IMPLEMENTATION
  artifact.

---

## Risks

### P8. Risk register

Carried from the approved ANALYSIS (R-1…R-11), plus the risks this PLAN itself introduces (R-P*).

| # | Risk | Trigger / evidence | Severity | Mitigation, owned by a work item |
| --- | --- | --- | --- | --- |
| R-1 **[F-002]** | Check placed only on the Reviewer branch ⇒ **no check at all at LOW**, widening authority by risk level | `e2e_harness.py:1070` returns before the Reviewer half | High | **W-3**: B2 sits above `:1070` and P6b row 2 gives LOW its own terminal there. The risk-independence claim is now an *equality assertion* — rows 2 and 4 must produce the same `final_status`/`decision_state`/`reason_code` — rather than a claim that the same code runs. Verified by scenario 7's P3 half and scenario 3's cross-risk parametrization |
| R-2 **[F-002]** | The classification-verifying Reviewer attempt **consumes** a correction iteration | `gate_attempts()` `:1501-1507` counts `reviewer_attempts`, appended at `:1194` — and under P6b row 4 that attempt is exactly what a MEDIUM/HIGH decision block now produces, so this risk is *realized by design* unless W-6 is right | High | **W-6**: one edit in `gate_attempts()`, keyed on `decision_block` rather than on risk, so it covers P6b's LOW (row 2) and MEDIUM/HIGH (row 4) shapes together; NV-2 control |
| R-3 | Any addition to the shared `decision_policy` block breaks CI immediately | C7 at **90/90** (measured), C11a key partition, C4 byte-parity | High | **P2 "deliberately not changed"**: the block is not touched; the tenth anchor contract carries the Orca-only half |
| R-4 | Reusing `REVIEW_VERDICT: BLOCKED` collapses into `RESULT: FAIL` and routes into the correction loop | invariants `SKILL.md:2240`, `:2244` | High | **W-7**: `RUN_STATUS: BLOCKED` + a closed reason constant; the review vocabularies are untouched |
| R-5 | Taking `WAITING_FOR_INPUT` encroaches on OS-31 and implies a resume that does not exist | live OS-31 scope | Med-High | **W-7 / P9**: O2 is contra-indicated and recorded as such; DESIGN must not pick it without new user authority |
| R-6 | Free-form Markdown becomes the control input | five of thirteen fields have no machine channel today | Med-High | **W-9 + P7 drift validator**, with the F-001 defect of this very run as the negative fixture |
| R-7 | A duplicated Reviewer dispatch or a second loop is introduced — **and stays invisible to the `round_kind` check** because it reuses a label | `e2e_harness.py:1158`, `:1520`, `:1625`, `:1705` | Medium | **NV-3 / M-DUP** with INV-D1/INV-D2/INV-D3 |
| R-8 | Guards added in `run_workflow()` get duplicated three times and drift | `:1520`, `:1405`, `:1705` all enter `run()` | Medium | **W-3/W-4** place B2/B3 inside `run()`; only B1 is per-site, beside guards that already exist there |
| R-9 | Vacuous tests: "the next phase was not dispatched" also passes if nothing is ever dispatched | the repository's own rule, `test_decision_policy.py:4-8` | Medium | **NV-1/NV-2/NV-3**, each with its co-located control |
| R-10 | Scenario 8 drifts into OS-30's supersession lineage | live OS-30 scope | Medium | Scenario 8 asserts **escalation**, not lineage; the ledger writes a new record and never a back-link (L3) |
| R-11 | The live runtime path has no deterministic iteration counter, so parts of OS-29 are code-enforced in `e2e_harness` but only contractually in SKILL.md on the Orca path | no `phase_iterations` in `orca_runtime_harness.py` | Medium | **W-8** enforces the one thing that *is* enforceable there (no Dispatch is created); the rest is the tenth anchor contract's Coordinator obligation, and this asymmetry is documented, not papered over |
| **R-P1** | **Fail-closed default vs. 1496 existing tests.** A gate that requires an explicit result makes every existing scenario's silent agent output a *failure*. The tempting fix — arming the gate only when a run opts in — is **fail-open by default and forbidden by the ticket** | `fake_worker.py:28-32` records the opposite (opt-in) precedent for OS-3's `--unit-test-status`; the ticket's *Fail-closed rules* forbid it here | High | **W-2 + C10**: the gate is armed always; the fake agents declare `CLEAR` **by default**, which is an agent *asserting* the state (satisfying R-A2-2), not the engine presuming it. Sizing measured this phase: only ~14 assertion sites in `scripts/test_*.py` reference fake-agent output shape, so the blast radius is small and each change is additive |
| **R-P2** | The tenth anchor contract is written before the behaviour exists and then has to be rewritten | ordering | Low | **W-10 is last** among production items |
| **R-P3** | `validate_skills.py` gains an import of `decision_gate` and every validator regression test becomes an import crash with empty stdout | the trap is documented at `test_validate_skills.py:57-64` for exactly this reason | Low-Med | **C11/W-11**: update the copied-module list at `test_validate_skills.py:49-66` in the same commit |
| **R-P4** | `REVIEWER_CONTEXT_KEYS` is a closed eight-tuple asserted at `orca_runtime_harness.py:2483-2484`; adding a ninth key for decision context ripples through the reviewer-context contract | `task_context.py:84-93` | Low-Med | **RU-6**: carry the classification framing in the existing `new_claims` / `drill_down` keys; a ninth key requires DESIGN to prove the eight cannot carry it |
| **R-P5** **[F-001]** | **The run-entry declaration degenerates into a rubber stamp** — a producer that always writes `CLEAR` is, in effect, the fail-open default the ticket forbids, just relocated | P6a's producer runs unconditionally at run open | **High** | The RED asserts **one machine-recomputable fact** about the ledger, and **A6 recomputes it** rather than trusting it. Its non-vacuity is proved by a *reachable* fixture, not by argument: **F10** reuses a `run_id` whose directory already holds an open `NEEDS_INPUT` (reachable because `run_id` is scenario-supplied and the root is re-provisioned at `:1490`) and the declaration is rejected. Separately, **A3** confines the RED to ledger position 0, so it can never stand in for an agent's judgement — **F11** is that proof |
| **R-P6** **[F-002]** | **The verification-mode Reviewer is mistaken for — or becomes — a second loop**, which is the ticket's first out-of-scope item | P6b rows 4–7 run the Reviewer *after* a block | Medium | It is the **already-scheduled** Reviewer at the **existing** dispatch site `:1158`, in the same round, for the same `(phase, iteration)`. No new dispatch site, no new subprocess site, no new `round_kind`, no second round. INV-D1/INV-D2/INV-D3 are asserted unchanged **and M-DUP must still fail them** — that mutation is what distinguishes "the same Reviewer ran once, in a different mode" from "a Reviewer ran twice" |

### Rollback / no-op guarantees

* **Behavioural no-op for a fully-`CLEAR` run — stated precisely [F-001].** A run in which every
  boundary declares `CLEAR` must produce **byte-identical transitions**: the same dispatches
  (`sessions`, `worker_attempts`, `reviewer_attempts`), the same `phase_iterations`, the same
  `correction_dispatches` / `revalidation_dispatches`, the same `final_status` and `reason`. This
  is an explicit regression assertion, not an aspiration — it is what makes the whole existing
  1496-test suite the backward-compatibility proof.
  **[F-001, iteration 3] What that suite proves, stated exactly.** The harness writes the RED at run
  open stamped with `decision_gate.LEDGER_RECORD_SCHEMA_VERSION`, and each of the four B1 sites
  admits it through the **complete A1–A6 path including A4-i/A4-ii**. So every one of the ~1496
  existing tests exercises the record-version check on a real ledger, on every run, with **no
  fixture edited** — the check is load-bearing across the whole suite rather than covered by one
  unit test. Its **non-vacuity** is F13/F14, not the suite: a passing suite is also consistent with
  a build that never reads a version, which is why the two negative fixtures are mandatory. And
  because refusal is the only other outcome (`DECISION_GATE_INPUT_MALFORMED` /
  `DECISION_LEDGER_SCHEMA_UNSUPPORTED`, both terminal), a future version bump cannot degrade an old
  run into a silent `CLEAR`; it can only make it block.
  **It is a transition no-op, not an artifact no-op**, and iteration 1 overstated it by not saying
  so. Such a run additionally gains: the sequence-0 run-entry declaration plus one ledger record per
  settled boundary (W-2b/W-9), and two **sparse** `ORCHESTRATOR_LOG` columns (W-7) that are empty on
  the existing rows. All three are *additions under the run artifact root*; none changes a
  transition, a counter, a status or a `reason`, and none edits an existing row or record. Any test
  asserting an exact run-directory listing is updated **additively**, and the diff for each such
  site is justified in the IMPLEMENTATION artifact.
* **A run that declares nothing does NOT silently proceed.** That is a deliberate, ticket-mandated
  behaviour change (*Fail-closed rules*), not a regression, and it is the single incompatibility
  IMPLEMENTATION must call out in its artifact. Every affected fixture is updated by *adding an
  explicit declaration*, never by relaxing a check.
* **Rollback unit.** Each of W-3, W-4, W-5, W-6, W-8 is independently revertible: each is a guard
  at a named line plus its tests. Reverting the whole feature is reverting C1–C13; nothing in
  OS-29 mutates existing rows, records, or prior-run artifacts, so there is no data migration to
  undo.
* **Append-only, never rewritten.** The decision ledger inherits "written once, never edited"
  (`run_logging.py:2148-2151`); a correction is a new record. No OS-29 code path edits an existing
  log row or record.

---

## Completion Criteria

### P10. Exit criteria for each remaining phase

Each row is what that phase's gate will be judged against, tied to the requester's completion
conditions.

**DESIGN — exits when all of the following are decided, with grounds and evidence:**

| # | Decision DESIGN owes | Bounded by |
| --- | --- | --- |
| D1 | The terminal-vocabulary shape: A4's **O1 / O2 / O3** | O1 is the budgeted, lowest-cost candidate; **O2 is contra-indicated** (OS-31 owns `WAITING_FOR_INPUT`); O3 has the highest blast radius (`workflow_contract.py:97`) |
| D2 **[F-001]** | The decision channel's mechanism for the **agent-emitted** half at B2/B3 (ANALYSIS **U-1**): a parsed field in the result contract, a Coordinator-side derivation from the record, or both | Must satisfy R-A2-1 (the narrative section stays optional) **and** R-A2-2 (the gate result is required and explicit). **The B1 half is no longer open**: P6a fixes the producer (`open_decision_ledger` at RU-12's three sites), the record shape, the binding and the admissibility rule A1–A6. DESIGN implements P6a; it does not re-decide it |
| D3 | The non-consumption site (**U-2**) | PLAN budgets `gate_attempts()` (one edit); a three-site alternative must justify itself against C-9 |
| D4 | The ledger's shape (**U-3**): new run-scoped record, columns, or both | Must reuse `write_final_review_audit_record`'s properties; must carry all thirteen fields |
| D5 | How far Decision ID / lineage goes (**U-4**) | Stops before OS-30's supersession protocol (L3, R-10) |
| D6 | The exact contents of the tenth anchor contract, and the exact mirrored-vs-not partition | Nine existing anchor contracts are the form; `validate_skills.py:744-750` is the rationale |
| D7 | The fail-closed migration for existing fixtures (R-P1) | Must not introduce a fail-open default. Scoped by P6a: the **B1** half needs no fixture migration at all (the harness writes the RED), so D7 covers only the B2/B3 agent declarations |
| D8 **[F-001]** | The ledger's sequence-collision primitive: `_stage_and_publish_audit_record`'s `os.rename` (`run_logging.py:1779`) overwrites on POSIX, so DESIGN must choose how two writers claiming the same sequence are prevented or detected (exclusive create, a rename onto a sequence-named path guarded by `O_EXCL`, or reader-side detection only) | Must preserve "staged then published so a published name is a complete record" and append-only "never edited" (`:2148-2151`). Reader-side detection is mandatory **regardless** — P6a **A2**/**A4** reject a duplicate or gapped sequence, and **F12** is its fixture |
| — | **Gate:** every decision above has grounds and file:line evidence; no approved ANALYSIS conclusion is changed; the shared `decision_policy` block is untouched; no new `RUN_STATUS`/`round_kind`/`STATUS` value is proposed without explicit user authority | |

**IMPLEMENTATION — exits when:**

* W-1…W-11 are implemented as designed, and the change surface is a subset of P2's table (anything
  outside it is justified in the artifact or is a scope violation);
* the existing review/correction loop is **not** duplicated: INV-D1/INV-D2/INV-D3 hold and M-DUP
  fails them while the control run passes;
* `NEEDS_INPUT`/`CONFLICT` block the correction Worker **and** the next phase **in code**, at low,
  medium and high, by P6b's single table — LOW terminal at B2, MEDIUM/HIGH terminal at B3-V, same
  `final_status`/`decision_state`/`reason_code`;
* **[F-001]** every B1 check, including the first phase of a new run, consumes an explicit,
  validated, bound ledger head under P6a A1–A6, and no code path reaches `CLEAR` from an absent,
  malformed or unbound record;
* a decision block consumes **no** correction iteration, while quality `FAIL` still consumes one;
* a missing or malformed gate result fails closed at all three boundaries;
* the mandatory IMPLEMENTATION test gate is satisfied with affirmative evidence (`SKILL.md:1809`
  §14; the LOW safety floor at `e2e_harness.py:1029-1051` requires it positively);
* full CI is green: `validate_skills.py` (check count **> 648**), the full unittest discovery
  (**≥ 1496** tests, no deletions), `verify_package.py`, `build_release.py`, `git diff --check`.

**TEST — exits when:**

* all fourteen scenarios have a **named positive and a named negative** fixture, and each is mapped
  to the module stated in P4;
* **[F-001]** F9–F12 are present, F11 (`red_offered_at_later_boundary`) passes **with** its
  co-located control, and F10 proves A6 on a reachable reused-`run_id` case — i.e. the run-entry
  declaration is demonstrably not a rubber stamp (R-P5);
* **[F-002]** every cell of P6b's two tables (B2 rows 1–3, B3 rows 4–10) has a named case, and the
  cross-risk equality of rows 2 and 4 is asserted on `final_status`, `decision_state` and
  `reason_code`;
* NV-1, NV-2 and NV-3 are present, each with its co-located control, and each control is asserted
  to be non-vacuous **inside the same test function** (`test_decision_policy.py:4-8`);
* scenario 7's risk-independence is asserted structurally (`inspect.signature`) **and**
  behaviourally (equality across low/medium/high with a guard that the three runs differ elsewhere);
* scenario 14 fails the validator in **both** drift directions, plus the delete-from-both direction;
* the P7 Markdown↔machine drift validator rejects the F-001 string from this run's iteration 1;
* the full regression suite passes and no existing test or validator was deleted or weakened.

**FINAL ADVERSARIAL REVIEW — the run completes only when:**

the existing loop was not duplicated; Quality verdict and Decision State are separate axes; the four
OS-28 states are integrated into the existing transitions; `NEEDS_INPUT`/`CONFLICT` block the
correction Worker and the next phase; current-phase Reviewer classification verification and the
forbidden next dispatch are distinguishable; user-waiting consumes no iteration; a missing or
malformed result does not fail open; Reviewer and Final Review block unauthorized assumptions and
unresolved decisions; the two Skills' decision semantics match; positive/negative and non-vacuity
verification plus full CI pass; and L1–L5 are documented accurately.

---

## Open Questions / Conflicts

**None requiring user authority.** Every ambiguity met in this phase was settled by an explicit
requirement in `ORIGINAL_REQUEST.md`, by the approved `ANALYSIS.md`, by the current code, or by the
PLAN phase contract. The items that could look like open decisions, and why none is:

* **The five DESIGN decisions D1–D5** are `ANALYSIS.md`'s U-1…U-4 plus the terminal-vocabulary
  choice. They are **phase-scope handoffs the task spec explicitly assigns to DESIGN** ("Do NOT make
  the detailed design decisions that DESIGN owns"), not open decision items crossing the autonomy
  boundary.
* **R-P1 (fail-closed default vs. existing fixtures)** looks like a trade-off requiring authority,
  but it is settled by an explicit requirement: "A missing or malformed gate result must become an
  explicit validation failure or blocked result, never automatic progression." The only remaining
  question is *how* the fixtures declare their state, which is D7 — a mechanism, not an authority
  question.
* **Correcting orchestration `SKILL.md:368-369`** is not a contract weakening: the sentence is not
  in `DECISION_POLICY_SKILL_PROSE_ANCHORS` (verified at `validate_skills.py:713-724`), and leaving
  a shipped Skill asserting something false is itself a Final Review finding (axis E, docs vs.
  behavior).

* **[iteration 3] The remaining F-001 half is also a settled technical design question.** Defining
  a ledger-**record** schema version distinct from OS-28's policy-block version is *determined*, not
  discretionary: the plan's own A4 already required a record version, and
  `decision_policy.validate_record()` demonstrably reads none, so the only two options were to add
  the check or to delete the rule — and deleting it is forbidden by the ORIGINAL_REQUEST's
  *Fail-closed rules*. Which module owns the constant is fixed by C1's already-recorded
  contract-only-isolation invariant plus the import direction W-1 asserts. No boundary element is
  triggering: it is reversible, touches no security, privacy, compliance, monetary or lock-in
  element, has no blast radius beyond `scripts/`, and pits no two explicit requirements against each
  other. The task spec independently states it is not a user-decision item. **Not escalated.**

* **The two iteration-2 findings are settled judgement calls, not user-decision items.**
  **F-001** is settled by an explicit requirement — the ORIGINAL_REQUEST's *Fail-closed rules*
  ("A gate boundary requires an explicit machine-readable decision result … missing decision record
  [may not be presumed `CLEAR`]") — which forces a producer to exist; P6a picks the minimal shape
  that satisfies it out of the repository's own precedents (RU-5's append-only record machinery and
  the three existing run-open statements), and adds no authority anywhere. **F-002** is settled by
  the objective's own text: `e2e_harness.py:1070` removes the choice at LOW, "only a Reviewer
  dispatch that verifies the CURRENT phase's decision classification may be permitted" identifies
  the one dispatch allowed after a block, and "risk NEVER expands decision authority" fixes the
  terminal outcome as identical across levels. Neither is an ambiguity no policy source settles,
  neither is irreversible or security/privacy/compliance/monetary/lock-in relevant, and neither
  pits two explicit requirements against each other — so neither is escalated.

No `NEEDS_INPUT` and no `CONFLICT` item arose in this phase, so this phase does not stop.

---

## Decision Record

The record is the machine-readable JSON at
`artifacts/runs/run_35b221ea299d/records/plan_decision_record.json`; it is the authority and the
prose here only describes it. Per the OS-28 contract, `CLEAR` carries **no** reason code —
`validate_record()` rejects any non-null `reason_code` for `CLEAR` (`scripts/decision_policy.py:1189`)
— so the grounds that carry the state are the declared facts, not a code.

```json
{
  "state": "CLEAR",
  "reason_code": null,
  "open_decision_item": false,
  "run": "run_35b221ea299d",
  "phase": "plan",
  "iteration": 3,
  "responsible_phase": "plan",
  "role": "worker",
  "grounds": "No boundary element declared by this phase is triggering, and no contradiction between two explicit requirements was found. The remaining review finding F-001 is a settled technical design question determined by the plan's own A4 rule plus the observed fact that decision_policy.validate_record() reads no version, so defining a distinct ledger-record schema version is determined rather than discretionary; the task spec states explicitly that it is not a user-decision item. D1-D8 are phase-scope handoffs to DESIGN mandated by the task spec; all are recorded under 'Open Questions / Conflicts' with their determining sources.",
  "scope": "Covers the PLAN phase's own conduct at iteration 3, including the resolution of review finding F-001. Design decisions deferred to DESIGN are not open decision items crossing the autonomy boundary."
}
```

**This record was VALIDATED, not merely described.** Command and verbatim output:

```text
$ python3 -c "
import json, sys; sys.path.insert(0, 'scripts')
from pathlib import Path
from decision_policy import load_decision_policy, validate_record, DecisionPolicyError
rec = json.load(open('artifacts/runs/run_35b221ea299d/records/plan_decision_record.json'))
for skill in ('orca-worker-reviewer-orchestration', 'orca-worker-reviewer-loop'):
    p = load_decision_policy(Path(skill + '/SKILL.md'))
    validate_record(p, rec)
    print('POSITIVE (' + skill + '): accepted.')
p = load_decision_policy(Path('orca-worker-reviewer-orchestration/SKILL.md'))
mut = dict(rec); mut['reason_code'] = '(none - CLEAR carries no reason code)'
try:
    validate_record(p, mut); print('NEGATIVE CONTROL FAILED: mutant accepted')
except DecisionPolicyError as e:
    print('NEGATIVE CONTROL: rejected ->', e)
mut2 = dict(rec); mut2['open_decision_item'] = True
try:
    validate_record(p, mut2); print('CONTROL-2 FAILED: accepted')
except DecisionPolicyError as e:
    print('CONTROL-2: rejected ->', e)
"
POSITIVE (orca-worker-reviewer-orchestration): accepted.
POSITIVE (orca-worker-reviewer-loop): accepted.
NEGATIVE CONTROL: rejected -> state CLEAR must not carry a reason_code
CONTROL-2: rejected -> CLEAR declares ['open_decision_item'] as grounds, but they do not
satisfy the CLEAR entry condition -- state CLEAR requires any_of of
['determining_policy_source', 'explicit_user_authorization', 'no_open_decision_item'];
unsatisfied: ['determining_policy_source', 'explicit_user_authorization',
'no_open_decision_item']
```

**NEGATIVE CONTROL** is exactly the iteration-1 ANALYSIS F-001 defect — a parenthetical string
supplied as `reason_code` — and it is rejected, proving the acceptance above is not the validator
ignoring the field. **CONTROL-2** flips the single fact that carries the state and is rejected by
the CLEAR entry condition (`decision_policy.py:867-877`), proving the acceptance is not the
validator ignoring the grounds.

### The complete A1–A6 path was executed over the exact RED  **[F-001, iteration 3]**

Iteration 2's evidence ran the RED through `decision_policy.validate_record()` **only**, which — as
the Reviewer showed — cannot exercise A4 at all, because that function reads no version of any kind.
This iteration executes the **whole** admissibility path: A1 → A2 → A4 → A3 → A6 → A5, over the
exact RED printed in P6a, with positive controls, the A4 negatives the finding named, and an on-disk
round trip through a real ledger directory.

The runnable prototype is committed with this artifact at
`artifacts/runs/run_35b221ea299d/prototypes/a1_a6_admissibility.py`; it imports `decision_policy`
and nothing else (the C1/W-1 import direction), defines `LEDGER_RECORD_SCHEMA_VERSION = 1` and
`SUPPORTED_LEDGER_RECORD_SCHEMA_VERSIONS = (1,)`, and implements A1–A6 exactly as P6a specifies.
Command and **verbatim** output:

```text
$ python3 artifacts/runs/run_35b221ea299d/prototypes/a1_a6_admissibility.py; echo "exit=$?"
policy block schema_version = 1  (decision_policy.SUPPORTED_SCHEMA_VERSIONS -- the POLICY BLOCK)
ledger record schema version = 1  (SUPPORTED_LEDGER_RECORD_SCHEMA_VERSIONS -- the RECORD)

[PASS] P1  exact RED, first B1, complete A1-A6
        expect=ADMITTED
        got   =ADMITTED head=run_35b221ea299d/plan/0/B1#0 state=CLEAR
[PASS] P2  settled B2 head at a later boundary (A3 second branch)
        expect=ADMITTED
        got   =ADMITTED head=run_35b221ea299d/plan/1/B2#1 state=CLEAR
[PASS] N1  RED with ledger_schema_version MISSING (A4-i)
        expect=DECISION_GATE_INPUT_MALFORMED
        got   =REFUSED DECISION_GATE_INPUT_MALFORMED -- run_35b221ea299d/plan/0/B1#0 has no ledger_schema_version (A4-i)
[PASS] N2  RED with UNSUPPORTED ledger_schema_version 2 (A4-ii)
        expect=DECISION_LEDGER_SCHEMA_UNSUPPORTED
        got   =REFUSED DECISION_LEDGER_SCHEMA_UNSUPPORTED -- run_35b221ea299d/plan/0/B1#0 declares ledger_schema_version 2; this build supports [1] (A4-ii)
[PASS] N3  RED with ledger_schema_version '1' as text (A4-i)
        expect=DECISION_GATE_INPUT_MALFORMED
        got   =REFUSED DECISION_GATE_INPUT_MALFORMED -- run_35b221ea299d/plan/0/B1#0 ledger_schema_version must be an integer, got '1' (A4-i)
[PASS] N4  RED with ledger_schema_version True (bool is not int) (A4-i)
        expect=DECISION_GATE_INPUT_MALFORMED
        got   =REFUSED DECISION_GATE_INPUT_MALFORMED -- run_35b221ea299d/plan/0/B1#0 ledger_schema_version must be an integer, got True (A4-i)
[PASS] N5  AGENT record with unsupported version, RED fine (A4-ii, every record)
        expect=DECISION_LEDGER_SCHEMA_UNSUPPORTED
        got   =REFUSED DECISION_LEDGER_SCHEMA_UNSUPPORTED -- run_35b221ea299d/plan/1/B2#1 declares ledger_schema_version 99; this build supports [1] (A4-ii)
[PASS] N6  policy-block version smuggled in as `schema_version` only (A4-i)
        expect=DECISION_GATE_INPUT_MALFORMED
        got   =REFUSED DECISION_GATE_INPUT_MALFORMED -- run_35b221ea299d/plan/0/B1#0 has no ledger_schema_version (A4-i)
[PASS] N7  empty ledger (A1)
        expect=DECISION_GATE_INPUT_MISSING
        got   =REFUSED DECISION_GATE_INPUT_MISSING -- the producer did not run
[PASS] N8  two sequence-0 records (A2)
        expect=DECISION_LEDGER_INCONSISTENT
        got   =REFUSED DECISION_LEDGER_INCONSISTENT -- 2 records carry sequence 0
[PASS] N9  RED offered at a LATER boundary -- F11 hole proof (A3)
        expect=DECISION_GATE_INPUT_UNBOUND
        got   =REFUSED DECISION_GATE_INPUT_UNBOUND -- the RED is the head but this is not the run's first B1; the settled record for ('run_35b221ea299d', 'plan', 1) is absent
[PASS] N10 RED carrying a reason_code -- the iteration-1 defect (A4-iii)
        expect=DECISION_GATE_INPUT_MALFORMED
        got   =REFUSED DECISION_GATE_INPUT_MALFORMED -- run_35b221ea299d/plan/0/B1#0: state CLEAR must not carry a reason_code (A4-iii)
[PASS] N11 sequence gap 0,2 (A4-iv)
        expect=DECISION_GATE_INPUT_MALFORMED
        got   =REFUSED DECISION_GATE_INPUT_MALFORMED -- sequences [0, 2] are not a gapless 0..n-1 (A4-iv)
[PASS] N12 unresolved open NEEDS_INPUT in the ledger (A5)
        expect=DECISION_BLOCKED:NEEDS_INPUT:blast_radius_beyond_scope
        got   =REFUSED DECISION_BLOCKED:NEEDS_INPUT:blast_radius_beyond_scope -- open at run_35b221ea299d/plan/1/B2#1
[PASS] N13 RED declares [] while an open item exists -- F10 (A6)
        expect=DECISION_GATE_DECLARATION_DISAGREES_WITH_LEDGER
        got   =REFUSED DECISION_GATE_DECLARATION_DISAGREES_WITH_LEDGER -- declared=[] recomputed=['run_35b221ea299d/plan/1/B2#1']

-- on-disk round trip --
   published keys: ['000000.json']
   ledger_schema_version read back from disk: 1
[PASS] D1  RED read from disk, complete A1-A6
        expect=ADMITTED
        got   =ADMITTED head=run_35b221ea299d/plan/0/B1#0 state=CLEAR
[PASS] D2  a FUTURE-version ledger written by a newer build fails closed
        expect=DECISION_LEDGER_SCHEMA_UNSUPPORTED
        got   =REFUSED DECISION_LEDGER_SCHEMA_UNSUPPORTED -- run_35b221ea299d/plan/0/B1#0 declares ledger_schema_version 2; this build supports [1] (A4-ii)

17/17 cases behaved as specified
exit=0
```

**What each group proves.**

* **P1** — the exact RED of P6a, *with* `ledger_schema_version`, is **admitted** through all six
  rules at the first B1. This is the claim iteration 2 asserted and did not demonstrate.
* **P2** — the A3 second branch: at a later boundary the settled B2 record is the head and admits.
  It is the **co-located control** that makes every refusal below attributable to its own rule
  rather than to a checker that refuses everything.
* **N1–N6** — the finding, directly. Missing version (**A4-i**), unsupported version (**A4-ii**,
  reported as the new closed reason `DECISION_LEDGER_SCHEMA_UNSUPPORTED`), a version supplied as
  text, a version supplied as `True` (a `bool` must not pass as an `int`), an **agent** record with
  an unsupported version while the RED is valid (A4 is a property of *every* record), and the
  cross-object control **N6**: a record carrying only the policy block's `schema_version` key is
  still malformed. The two fields are not interchangeable, and the prototype proves it rather than
  claiming it.
* **N7–N13** — the other five rules, each with its own closed reason, so A4 is shown to be an
  *added* clause and not a replacement for anything: A1 missing ledger, A2 duplicate sequence 0,
  **A3** the F11 hole proof, A4-iii the iteration-1 `reason_code` defect, A4-iv a sequence gap,
  **A5** an unresolved open `NEEDS_INPUT` (reported as `DECISION_BLOCKED:NEEDS_INPUT:
  blast_radius_beyond_scope`), and **A6** the F10 lying declaration.
* **D1/D2** — the producer→file→reader→gate round trip. `D1` writes the RED to a real ledger
  directory, reads it back off disk and admits it; `D2` bumps the stored record to version 2 and the
  same build **refuses** it with `DECISION_LEDGER_SCHEMA_UNSUPPORTED`. That is the compatibility
  rule of P6a demonstrated end-to-end: a future-version ledger blocks, it never degrades to `CLEAR`.

**Non-vacuity.** Seventeen cases, two of them positive; every negative differs from an admitted
positive in **exactly one** field or one ledger fact. So "refused" is attributable to the rule under
test, and "admitted" is not the checker being blind.

**Independent confirmation that the OS-28 constant could not have served.** Run this phase against
the working tree:

```text
$ grep -n "SUPPORTED_SCHEMA_VERSIONS" scripts/decision_policy.py
42:SUPPORTED_SCHEMA_VERSIONS: tuple[int, ...] = (1,)
212:        version in SUPPORTED_SCHEMA_VERSIONS,
214:        f"{list(SUPPORTED_SCHEMA_VERSIONS)}",
```

Both consumers are inside `parse_decision_policy()` (`:200-214`), which validates the **policy
block**. `validate_record()` (`:1189-1292`) contains no reference to it. Reusing it for records
would therefore have required adding the check anyway — at which point the two objects would share
one integer for no benefit and with the coupling cost described in P6a.

### The two iteration-2 designs were executed against the contract, not merely asserted

**P6a's run-entry declaration is a valid OS-28 record.** The prototype in P6a was run through
`validate_record()` this phase, with a control:

```text
P6a RED PROTOTYPE: accepted by validate_record().
RED CONTROL: rejected -> state CLEAR must not carry a reason_code
```

The acceptance is therefore the contract accepting the shape, not the validator ignoring it. It
also confirms the shape needs **no** change to `decision_policy.py`: the RED is an ordinary `CLEAR`
record satisfying the `no_open_decision_item` predicate, and the OS-29 ledger fields (`boundary`,
`sequence`, `source`, `prior_open_decision_items`) ride alongside without disturbing validation.

**P6b row 6 delegates the downgrade decision entirely to `validate_transition()`, and all four
downgrade edges are closed today.** Run this phase with a record carrying **no** `user_decision`:

```text
NEEDS_INPUT -> ASSUMPTION_ALLOWED : rejected -> transition NEEDS_INPUT -> ASSUMPTION_ALLOWED is
                                    forbidden unconditionally; a user_decision does not enable it
NEEDS_INPUT -> CLEAR              : rejected -> transition NEEDS_INPUT -> CLEAR requires a
                                    user_decision record
CONFLICT    -> ASSUMPTION_ALLOWED : rejected -> transition CONFLICT -> ASSUMPTION_ALLOWED is
                                    forbidden unconditionally; a user_decision does not enable it
CONFLICT    -> CLEAR              : rejected -> transition CONFLICT -> CLEAR requires a
                                    user_decision record
```

with the co-located control that the checker is not simply rejecting everything:

```text
CLEAR       -> NEEDS_INPUT : accepted.
NEEDS_INPUT -> CONFLICT    : accepted.
```

This is why P6b row 6 needs **no OS-29 downgrade rule of its own**, and why the "Reviewer may not
downgrade without grounds" requirement is discharged by existing, tested machinery: the
`ASSUMPTION_ALLOWED` edges are forbidden unconditionally, and the `CLEAR` edges require a
`user_decision` that no in-run channel can supply while OS-30 is absent (**L1/L6**).

---

## Modified Files / Artifacts

* `artifacts/runs/run_35b221ea299d/PLAN.md` (this document) — **updated in place at iteration 3**
  (same path, no suffix), per the artifact contract.
* `artifacts/runs/run_35b221ea299d/records/plan_decision_record.json` — updated in place
  (`iteration` 2 → 3, grounds and scope restated for the one remaining finding) and re-validated
  against **both** Skills' policies with two negative controls.
* `artifacts/runs/run_35b221ea299d/prototypes/a1_a6_admissibility.py` — **new at iteration 3**. The
  runnable A1–A6 prototype whose verbatim output is quoted in `## Decision Record`, committed so the
  Reviewer can re-execute the evidence rather than take it on trust. It is a **run artifact**, not
  production code: nothing under `scripts/` imports it and it is outside `release_manifest.py`'s
  `INCLUDED_ROOTS` (`scripts/release_manifest.py:44`), so it cannot affect CI or the release.
* No `REVIEW_*.md` file was written by this phase — those belong to the Reviewer, and each
  iteration owns its own path.

**No tracked production file was modified by this phase.** PLAN produces a plan, not code.

---

## Validation

| Check | Result |
| --- | --- |
| `python3 scripts/validate_skills.py` | `Skill validation PASSED (648 checks)` — the baseline this PLAN's exit criteria are measured against. **Re-run at iteration 3**, same result |
| `python3 -m unittest discover -s scripts -p 'test_*.py'` | `Ran 1496 tests in 300.497s` -> `OK (skipped=6)`, exit 0 -- **re-run at iteration 3**, not inherited from iteration 2 |
| `git diff --check` | clean, re-run at iteration 3 |
| `git log --oneline -1` | `b13f191` — no commit made by this phase |
| `git status --short` | untracked `artifacts/` only; no tracked source modified |
| Decision-record validation | See `## Decision Record` — positive against **both** Skills' policies at `iteration: 3`, plus two negative controls. **Re-run at iteration 3** |
| **[F-001, iteration 3]** The **complete A1–A6 path** over the **exact** RED | `python3 artifacts/runs/run_35b221ea299d/prototypes/a1_a6_admissibility.py` → **`17/17 cases behaved as specified`**, exit 0. 2 positives (P1 the exact RED at the first B1; P2 the A3 later-boundary control), 13 negatives covering **every** one of A1–A6 including the six A4 cases the finding named, and 2 on-disk round-trip cases (D1 admit from disk, D2 a version-2 ledger **refused**). Verbatim output in `## Decision Record` |
| **[F-001, iteration 3]** The two schema versions are distinct objects | `grep -n "SUPPORTED_SCHEMA_VERSIONS" scripts/decision_policy.py` → 3 hits: the module-level definition at `:42` and its only two uses at `:212` and `:214`, **both inside `parse_decision_policy()`**; **zero** inside `validate_record()` (`:1189-1292`). So the OS-28 constant could not have validated a record even in principle. Verbatim output in `## Decision Record` |
| **[F-001, iteration 2]** P6a run-entry-declaration prototype | Run through `validate_record()` at iteration 2: **accepted**, with a `reason_code` mutant **rejected** as its control. **Superseded as evidence by the A1–A6 row above**, which is the complete path; kept because A4-iii still relies on it |
| **[F-002]** P6b row 6 downgrade edges | All four `NEEDS_INPUT`/`CONFLICT` → `ASSUMPTION_ALLOWED`/`CLEAR` edges run through `validate_transition()` this phase: **all four rejected**, with two legal edges **accepted** as the non-vacuity control. Verbatim output in `## Decision Record` |
| Measured this phase (not inherited) | `decision_policy` block body = **90 lines** in both Skills against `DECISION_POLICY_MAX_LINES = 90`; `#### … contract` anchor blocks = **9** in orchestration, **0** in the loop; fake-agent output is referenced at ~**14** assertion sites in `scripts/test_*.py` |

---

## Unit Tests / Testing Strategy

PLAN writes no production code, so no unit test is added by this phase. The testing strategy this
phase is responsible for **specifying** is `## Validation / Test Plan` above: fourteen
scenario pairs mapped one-to-one to named fixtures and owning modules, three mandatory non-vacuity
proofs (NV-1 dispatch blocking, NV-2 iteration non-consumption, NV-3 non-duplication via M-DUP),
each with a co-located control, and the mandatory IMPLEMENTATION/TEST gates restated as exit
criteria in P10. No existing test or validator is scheduled for deletion or weakening.

---

## Review Feedback Resolution

### Iteration 3 — the one remaining finding

Finding from `artifacts/runs/run_35b221ea299d/REVIEW_PLAN_iteration2.md` (PLAN gate, iteration 2).
It is accepted as correct and is **not** disputed. F-002 was verified RESOLVED at iteration 2 and is
neither re-opened nor re-litigated here; its iteration-2 trace is retained below unchanged.

**FINDING F-001: RESOLVED**

The Reviewer was right, and the defect was real in both halves it named:

1. **The specified NORMAL output was incomplete under the stated reader contract.** P6a presented
   the RED as B1's machine-readable input and showed its complete JSON **without** a schema version,
   while A4 already required "the schema version is in `SUPPORTED_SCHEMA_VERSIONS`". Implementing A4
   literally would have rejected the very record P6a shows, blocking the first phase; not
   implementing it would have silently dropped the plan's own unsupported-schema fail-closed rule.
2. **The evidence could not have proved A4.** Verified in the working tree this phase:
   `SUPPORTED_SCHEMA_VERSIONS` (`decision_policy.py:42`) has exactly two consumers, both inside
   `parse_decision_policy()` (`:212`, `:214`), which validates the Skill's **policy contract block**.
   `validate_record()` (`:1189-1292`) reads no version at all. Iteration 2's acceptance therefore
   proved the OS-28 state/evidence shape and nothing about A4.

**The distinction the Reviewer drew is honoured: two objects, two constants, and OS-28's is not
reused.** What changed, and where:

* **New subsection `#### The ledger-record schema version, and why it is not OS-28's`** (in P6a) —
  a side-by-side table of the two objects (what each versions, which field it reads, who enforces
  it, where it is declared, when it changes) and three verified reasons the OS-28 constant is not
  reused: `validate_record()` never reads it; the policy block's key set is closed
  (`decision_policy.py:216-222`) and the block is at 90/90 lines so a record field cannot be added
  there; and the two objects must be free to version independently. The subsection also fixes the
  **owner and import direction** — `LEDGER_RECORD_SCHEMA_VERSION` /
  `SUPPORTED_LEDGER_RECORD_SCHEMA_VERSIONS` live in the new `decision_gate.py` (C1),
  `run_logging.open_decision_ledger()` imports the constant to stamp what it writes, and the reverse
  edge stays forbidden by C1 and pinned by W-1's AST assertion — plus the **compatibility rule**:
  an unsupported version is refused, never coerced and never treated as absent, in both directions.
* **The field is in the RED and in the agent ledger records.** `"ledger_schema_version": 1` is now
  the first key of the exact RED in *"The record"*, and that subsection additionally shows the
  **B2/B3 agent ledger-record shape** carrying the same field — so A4 is a property of *every*
  record, not a special property of sequence 0.
* **A4 is now four explicit clauses with their own closed reasons** — **A4-i** the field is present
  and an `int` (a `bool` is not); **A4-ii** the value is in
  `decision_gate.SUPPORTED_LEDGER_RECORD_SCHEMA_VERSIONS`, violation ⇒ the **new** terminal reason
  `DECISION_LEDGER_SCHEMA_UNSUPPORTED` (added to **W-7**'s closed set, deliberately distinct from
  `DECISION_GATE_INPUT_MALFORMED`); **A4-iii** `validate_record()` passes; **A4-iv** the sequences
  are gapless. The A1–A6 table no longer *uses* `SUPPORTED_SCHEMA_VERSIONS`; it now names it only
  to say explicitly that it is **not** the constant A4 checks against.
* **The evaluation order is now stated**, so the reported reason is deterministic when several rules
  fail at once: **A1 → A2 → A4 → A3 → A6 → A5**. A6 precedes A5 so that F10's expected
  `DECISION_GATE_DECLARATION_DISAGREES_WITH_LEDGER` is reproducible rather than hidden behind the
  block the lying RED failed to declare. No ordering changes any outcome — all six refusals are
  terminal and none can yield a `CLEAR`.
* **F3 and scenario 13 are updated to exercise the complete path.** P6's **F3** row now names both
  objects, both constants and both enforcers, and states explicitly that `validate_record()` does
  **not** perform the record check. Scenario **13**'s passing fixture is the **exact RED admitted
  through the complete A1–A6 path** rather than through `validate_record()` alone, and its negative
  set grows from F9–F12 to **F9–F14** with the two new fixtures: **F13**
  `ledger_record_schema_version_missing` (A4-i, three shape variants plus the cross-object control
  that a bare policy-block `schema_version` key does not satisfy it) and **F14**
  `ledger_record_schema_version_unsupported` (A4-ii, run on both the RED and a B2 agent record, with
  a co-located supported-version control).
* **P7** lists `ledger_schema_version` first among the ledger-mechanics fields, with the explicit
  note that it is not the policy block's `schema_version`; the count is corrected from five to six.
* **The backward-compatibility proof now exercises the complete path.** *Rollback / no-op
  guarantees* states exactly what the 1496-test suite proves: the harness stamps the RED with
  `LEDGER_RECORD_SCHEMA_VERSION` at run open and all four B1 sites admit it through A1–A6 including
  A4-i/A4-ii, so every existing test exercises the record-version check on a real ledger with no
  fixture edited — while **non-vacuity is F13/F14, not the suite**, because a green suite is equally
  consistent with a build that never reads a version. It also records that a future version bump can
  only make an old run **block**, never degrade it to a silent `CLEAR`.

**Verified by EXECUTION, not assertion.** `## Decision Record` → *"The complete A1–A6 path was
executed"* carries the verbatim output of
`artifacts/runs/run_35b221ea299d/prototypes/a1_a6_admissibility.py`, committed with this artifact so
the Reviewer can re-run it: **`17/17 cases behaved as specified`**, exit 0. It runs the **exact**
RED of P6a through **A1 → A2 → A4 → A3 → A6 → A5** — not `validate_record()` alone — with two
positive controls, the six A4 negatives the finding named (missing, unsupported, text, `bool`, an
**agent** record with an unsupported version, and a record carrying only the policy-block
`schema_version` key), the seven negatives for the other five rules, and a producer→file→reader→gate
on-disk round trip whose second half writes a **version-2** ledger and shows this build refuse it
with `DECISION_LEDGER_SCHEMA_UNSUPPORTED`. Every negative differs from an admitted positive in
exactly one field or one ledger fact.

**Not escalated.** Per the task spec this is a settled technical design question, and it was
resolved in-phase. This iteration's decision record is `CLEAR` with `reason_code: null` at
`iteration: 3`, re-validated against both Skills' policies with two negative controls.

**F-002: previously resolved.** Verified RESOLVED by the iteration-2 review. Not re-opened, not
re-litigated, and untouched by this iteration — P6b, W-3, W-4, W-6, the execution order and
scenarios 3, 4, 5, 6, 10 and 12 are carried forward byte-identical.

---

### Iteration 2 — retained trace

Findings from `artifacts/runs/run_35b221ea299d/REVIEW_PLAN.md` (PLAN gate, iteration 1). Both are
accepted as correct; neither is disputed. **F-001's iteration-2 resolution below was judged
incomplete at the iteration-2 gate; the completion is the iteration-3 entry above.**

**FINDING F-001: PARTIALLY RESOLVED AT ITERATION 2 — completed at iteration 3**

The Reviewer was right on every claim, including that the current harness dispatches the Worker
before any Worker result exists — re-verified this phase at `scripts/e2e_harness.py:1509-1524`
(the phase loop calls `_phase_harness(...).run(...)` with no prior boundary) and at
`scripts/orca_runtime_harness.py:2382-2456` (`run_existing_task` renders the spec and calls
`start_worker` with no prior boundary).

What changed, and where:

* **New `### P6a. B1 gate input: producer, binding and admissibility`** — the whole resolution.
  It names the **producer** (`run_logging.open_decision_ledger()`, C3/W-2b), its **exact call
  sites** (the three existing run-open statements — `e2e_harness.py:658`, `:1490`,
  `orca_runtime_harness.py:1529` — which provision the root and the ledger in **one** statement, so
  a root cannot exist without a ledger), the **machine-readable input** (the run-entry declaration,
  ledger sequence 0, a `CLEAR` record bound to `run` / `phase` / `iteration: 0` /
  `source: "coordinator:run_entry"` / `boundary: "B1"`, validated by `validate_record()`), and the
  **admissibility rule A1–A6** that every one of the four B1 sites shares.
* **The "nothing to check yet" vs. "absence means CLEAR" distinction is designed, not assumed** —
  P6a's second table. The declaration asserts one **machine-recomputable fact about the ledger**,
  and **A6 recomputes it** rather than trusting it; it asserts nothing about any agent's judgement,
  which still arrives at B2 and is still fail-closed there.
* **It cannot become a hole.** **A3** admits the run-entry declaration at exactly one ledger
  position (`len(L) == 1`, before any agent record exists); at every later boundary the head must be
  the settled `B2`/`B3` record of the round that just finished, so a later missing record yields
  `DECISION_GATE_INPUT_UNBOUND` and can never fall back to the declaration.
* **Proved by a negative fixture** — P6a's validation table: **F9** `red_absent` (no declaration ⇒
  the first phase does not dispatch, `sessions == ()`), **F10** `red_disagrees_with_ledger` (a
  reachable reused-`run_id` case that makes A6 non-vacuous), **F11**
  `red_offered_at_later_boundary` (**the hole proof**, with its co-located control), **F12**
  `two_sequence_zero_records`, and the live pair on `test_orca_runtime_contract.py`.
* **How existing runs obtain an explicit `CLEAR` without absence being `CLEAR`** — P6a's
  "How existing fully-`CLEAR` runs obtain an explicit `CLEAR`": the harness writes the declaration
  at run open, so all ~1496 existing tests get it with **no fixture edited** and **no check
  relaxed**.
* **Change surface, ordering and dependencies added to the plan** — new **RU-12** (P1); **C2**,
  **C3**, **C4** rewritten with revised sizes (P2); new work item **W-2b** and **W-5** re-pointed at
  it (P3); the execution-order DAG now makes W-2b a hard prerequisite of W-5 and W-8, with the
  rework rationale stated; new risk **R-P5** (the declaration degenerating into a rubber stamp) with
  its mitigation; new DESIGN item **D8** (sequence-collision primitive) and **D2/D7** narrowed
  because the B1 half is no longer open; scenario rows **1**, **12** and **13** extended.
* **An overstated claim was corrected**: *Rollback / no-op guarantees* now says a fully-`CLEAR` run
  is a **transition** no-op, not an artifact no-op, and enumerates the three additions under the run
  artifact root.
* **Executed evidence, not assertion**: the P6a record prototype was run through
  `validate_record()` at iteration 2 and accepted, with a mutant control rejected — verbatim output
  in `## Decision Record`. **This was the incomplete half.** `validate_record()` cannot exercise A4,
  so the iteration-2 gate correctly held F-001 open; the complete A1–A6 execution is the iteration-3
  entry above.

**FINDING F-002: RESOLVED**

The contradiction was real: iteration-1 W-3 and W-4 prescribed different terminals for the same
`NEEDS_INPUT`/`CONFLICT` Worker result at MEDIUM/HIGH. Resolved deterministically from the
objective's own text, as a settled judgement call — **not** escalated.

What changed, and where:

* **New `### P6b. The single decision transition table`** — one B2 table (rows 1–3), one B3 table
  (rows 4–10), and three ordering rules **O-1** (boundary order), **O-2** (the decision axis is
  evaluated before the quality axis at *every* boundary) and **O-3** (risk selects where the
  terminal is recorded, never whether it is terminal).
* **The single rule.** **LOW blocks at B2** (row 2) — forced, because `e2e_harness.py:1070` returns
  before the Reviewer half. **MEDIUM/HIGH** continue into the **already-scheduled** Reviewer at
  `:1158` in `verification_only` mode and take the terminal block at **B3-V** (rows 4–7) — chosen
  because the objective permits exactly one dispatch after a block, "a Reviewer dispatch that
  verifies the CURRENT phase's decision classification", and that dispatch already exists in the
  round. **Row 3 is risk-independent**: a missing/malformed/unbound Worker gate result terminates at
  B2 at every level and never enters verification mode, because there is no valid classification to
  bind to.
* **Binding of the Reviewer's output to the Worker's classification** — `verifies:
  {run, phase, iteration, worker_record_key}`, a reference to a *ledger record*. An unresolvable
  `verifies` is row 7 (`DECISION_GATE_INPUT_UNBOUND`). It is explicitly **not** supersession
  lineage, which stays OS-30's (L3/R-10).
* **No downgrade without grounds, with no new OS-29 rule** — row 6 delegates entirely to
  `decision_policy.validate_transition()`. Verified by execution this phase: all four
  `NEEDS_INPUT`/`CONFLICT` → `ASSUMPTION_ALLOWED`/`CLEAR` edges are rejected (the
  `ASSUMPTION_ALLOWED` pair **unconditionally forbidden**, the `CLEAR` pair requiring a
  `user_decision` no in-run channel can supply), with two legal edges accepted as the control —
  verbatim output in `## Decision Record`.
* **The blocked outcome stays terminal** — every outcome in rows 2 and 4–8 is a terminal `BLOCKED`,
  and new limitation **L6** makes even a validly-authorized downgrade terminal in OS-29, because
  acting on it would be resume (OS-31).
* **No correction iteration is charged, and no correction loop is entered** — **W-6** rewritten:
  one `gate_attempts()` edit keyed on `decision_block` rather than on risk, which is what lets a
  single rule cover both P6b terminal shapes (LOW's Worker attempt at `:1012`, MEDIUM/HIGH's
  Reviewer attempt at `:1194`). The correction Worker is unreachable because a non-`completed`
  round returns at `:1524` and correction rounds are only entered from the Final-Review T4 path.
* **Risk does not expand authority** — rows 2 and 4 produce the **same** `final_status`,
  `decision_state` and `reason_code`; scenario 3 now asserts that equality across low/medium/high
  rather than asserting that the same code runs.
* **W-3, W-4, W-6, P6, the execution order and the scenario matrix are aligned to P6b and state
  nothing on their own** — W-3/W-4/W-6 rewritten (P3); P6's structural-rules list gains the axis-
  ordering bullet; the execution-order rationale corrected (the W-6-after-W-3-and-W-4 dependency was
  *claimed* in iteration 1 but was not true of iteration 1's W-3/W-4, which each claimed the same
  terminal); scenario rows **3, 4, 5, 6, 10** and **12** restated against the tables; risks **R-1**
  and **R-2** restated and new risk **R-P6** added (the verification-mode Reviewer being mistaken
  for, or becoming, a second loop — mitigated by INV-D1/INV-D2/INV-D3 and M-DUP, which must still
  fail the mutant).
* **One consequential behaviour change is called out rather than hidden**: under row 8, scenario 6
  (Reviewer detects an unauthorized high-impact auto-approval) now terminates on the decision axis
  instead of entering the correction loop. Both Jira acceptance sentences still hold — the Reviewer
  *does* FAIL it as a blocking finding, which is parsed and recorded, and a user-decision block does
  *not* consume a correction iteration. The co-located control (a Reviewer `RESULT: FAIL` whose own
  gate result is `CLEAR` ⇒ row 9, the existing correction routing, iteration charged) keeps the two
  axes visibly separate.

**Not reopened.** The ANALYSIS-phase findings F-001 and F-002 were closed in the approved input and
remain closed: the ANALYSIS F-001 lesson is still applied by validating this phase's own decision
record with two controls and by keeping that exact defect as a required negative fixture for the P7
drift validator; the ANALYSIS F-002 conclusion is still carried as INV-D1/INV-D2/INV-D3 + M-DUP in
P4, with `ROUND_KIND_VALUES` kept only as supplementary evidence.

**Untouched by iteration 2**, as instructed: the P1 reuse inventory (apart from the added RU-12),
the P5 parity plan, the P7 provenance plan, the P9 scope boundary, and the fourteen-scenario matrix
apart from the six rows F-001/F-002 forced.

**Untouched by iteration 3**, as instructed: the whole of P6b, W-3, W-4 and W-6; the reuse
inventory; the parity plan; the scope boundary; the three non-vacuity proofs; and thirteen of the
fourteen scenario rows. P7 gained one field name and scenario 13 gained two fixtures, both forced by
F-001 and both flagged inline.
