# Worker Result

STATUS: COMPLETE

Run: run_35b221ea299d · Phase: design · Iteration: 1 · Role: worker
Branch: os-29-continuous-decision-gates (base main @ b13f191)
Ticket: OS-29 "Add Continuous Decision and Escalation Gates to Every Phase"
Approved input: `ANALYSIS.md` (PASSED at iteration 2) and `PLAN.md` (PASSED at iteration 3,
`REVIEW_PLAN_iteration3.md`). **No approved ANALYSIS or PLAN conclusion is changed by this DESIGN.**

Every `file:line` below was read out of this branch's working tree **during this phase**, not copied
from ANALYSIS or PLAN. Where a number differed from the approved input I say so inline; the only
such case is PLAN D8's premise about `os.rename`, discussed in D8, and it is a *refinement of a
parenthetical*, not a change to any conclusion.

Three runnable prototypes are committed with this artifact under `prototypes/`. Every claim marked
**[executed]** is quoted from their verbatim output, and the Reviewer can re-run them.

---

## Summary / Requirements

DESIGN owes eight decisions (PLAN P10: **D1–D8**), each with grounds and `file:line` evidence, plus
six things the task spec names explicitly: the complete machine-readable record schema; the exact
Markdown↔machine authority relationship and its anti-drift validator; the precise B1/B2/B3 control
flow keyed to existing call sites; the mirrored-vs-orchestration-only partition and how a validator
makes drift a FAILURE; backward compatibility for existing runs, fixtures and the 1496-test suite;
and the limitations that remain while OS-30 and OS-31 do not exist.

**The eight decisions, in one table.** Each row's grounds are developed in *Proposed Design*.

| # | Decision | Grounds in one line |
| --- | --- | --- |
| **D1** | **O1** — `RUN_STATUS: BLOCKED` + a closed OS-29 `reason` constant + two sparse columns. **No new lifecycle value.** | `RUN_STATUS_VALUES` already contains `BLOCKED` (`run_logging.py:105`); four precedent reason constants already exist; O2 takes OS-31's named deliverable; O3 widens the pair `workflow_contract.py:97` asserts. **[executed]** |
| **D2** | **Both halves, in one channel**: a required `DECISION_GATE_STATE:` field line **and** a required ```` ```decision-gate ```` JSON block in the agent's own result body. The **record is the authority**; the field line is its projection; disagreement is its own terminal reason. | The result body is the only channel that provably crosses the live Orca boundary (`orca_runtime_harness.py:2138-2139` already reads `attempt.body`); `parse_unit_test_status` (`e2e_harness.py:327`) is the standing precedent for an added field with its own reader. **[executed]** |
| **D3** | **`gate_attempts()`** (`e2e_harness.py:1501-1507`) — one edit, keyed on `decision_block`. | It is the one closure all three increment sites (`:1521`, `:1635`, `:1712`) already call; the three-site alternative would have to rewind or special-case the dispatch ledgers at `:1631-1634`/`:1708-1711`, which C-9 forbids. **[executed]** |
| **D4** | **A new run-scoped append-only record, AND two sparse columns** — not one or the other. | The thirteen required fields do not fit a log row (five are structured); the columns are the *index*, the record is the *authority*, exactly the split `parse_final_review_report` (`run_logging.py:1549`) already operates. **[executed]** |
| **D5** | Lineage goes exactly as far as **the ledger key**, the **`verifies`** back-reference, and **`prior_open_decision_items`** — and stops there, enforced by a **closed record-field set**. | OS-30 owns supersession; a closed set makes the boundary a check rather than a promise. **[executed]** |
| **D6** | A tenth orchestration-only `#### Decision gate contract` anchor block (18 keys, budget 20 lines) + a three-sentence `MIRRORED_DECISION_SEMANTICS_ANCHORS` tuple checked byte-for-byte in **both** Skills. | Nine existing anchor contracts are the form (orchestration 9, loop 0 — **measured this phase**); `validate_skills.py:744-750` is the rationale. **[executed]** |
| **D7** | The gate is **always armed**; the fake agents **declare `CLEAR` by default**. The OS-3 opt-in precedent (`fake_worker.py:28-32`) is deliberately **inverted**. | An arming flag is a fail-open default, which the *Fail-closed rules* forbid. B1 needs no fixture migration (the harness writes the run-entry declaration), so D7 covers only B2/B3. **[executed]** |
| **D8** | **Reuse the existing directory-rename primitive unchanged**, generalized by one line; a losing writer gets a typed collision, never an overwrite. **Reader-side detection (A2/A4-iv) is implemented regardless.** | `_stage_and_publish_audit_record` renames a *directory* onto a *directory*, which POSIX refuses when the target is non-empty — executed: `OSError:66` (ENOTEMPTY). A **file** rename does overwrite, which is why a file-per-record ledger is rejected. **[executed]** |

**The one boundary the objective reserves was not reached.** D1 resolves with the **existing**
terminal vocabulary. No new `RUN_STATUS`, no new `round_kind`, no new Worker `STATUS` value, and no
new `REVIEW_VERDICT` value is proposed. There is therefore no escalation to manufacture, and this
DESIGN makes the ordinary settled design call the task spec instructs it to make.

---

## Current Architecture

Re-verified this phase against the working tree. Only the facts DESIGN actually depends on are
listed; ANALYSIS §A1–A10 and PLAN P1 carry the full inventory.

| Fact | Evidence (read this phase) |
| --- | --- |
| One correction loop, one `for` header | `scripts/e2e_harness.py:904` inside `run()` (`:898`) |
| Exactly two agent-invoking subprocess sites | `:981` (Worker), `:1158` (Reviewer) |
| Exactly three round-dispatch sites | `:1520`, `:1625`→`:1405`, `:1705` |
| Worker result parsed, then appended, then branched | parse `:997`, append `:1012`, `STATUS: BLOCKED` branch `:1015`, LOW safety floor `:1029-1051`, finding-trace guard `:1053-1066`, LOW gate return `:1070` |
| Reviewer result parsed, appended, branched | parse `:1183`, append `:1194`, PASS `:1198`, FAIL `:1210`, budget exhausted `:1216` |
| The iteration counting closure and its three call sites | `gate_attempts()` `:1501-1507`; `:1521`, `:1635`, `:1712` |
| The three run-open statements (RU-12) | `e2e_harness.py:658`, `:1490`, `orca_runtime_harness.py:1529`. The fourth call at `e2e_harness.py:1362` is a **read** on the Final-Review report path |
| `_parse_choice` strictness and the field regex | `e2e_harness.py:294-305`; `FIELD_LINE = ^(?P<field>[A-Z_]+):\s*(?P<value>[A-Z_]+)\s*$` at `:52` |
| The standing precedent for an **added field with its own reader** | `parse_unit_test_status` `e2e_harness.py:327-355`, whose docstring states why it is not a fourth tuple element |
| A **lowercase**-valued line needs its own regex | `RESPONSIBLE_PHASE_LINE` `e2e_harness.py:79-81` (`[a-z][a-z0-9_]*`) |
| Terminal statuses and the sparse-column precedent | `run_logging.py:105`; OS-3's four columns and their rationale `:74-80` |
| `--event` is an **open** vocabulary by design | `run_logging.py:974-977` — "`--event` has no `choices` by design, so these are new VALUES in an already-open vocabulary" |
| The append-only record machinery | `write_final_review_audit_record` `:2120`, `_REQUIRED_RECORD_FIELDS` `:2358-2372`, `_stage_and_publish_audit_record` `:1779`, `_write_staged_file` `:1770`, `_fsync_directory` `:1755`, `redact_text` `:1129`, "never edited" `:2148-2151` |
| **`run_logging.py` imports NOTHING from `scripts/`, on purpose** | module docstring `run_logging.py:16-27` (OS-17 review round 3 MAJOR-1) |
| …and is **byte-duplicated** inside the Skill | `orca-worker-reviewer-orchestration/tools/run_logging.py`, enforced by `validate_run_logging_tool_parity` (`validate_skills.py:2758-2783`). **Measured this phase: `cmp` reports the two files byte-identical.** |
| The anchor-contract form and its shared parser | `parse_anchor_contract` `validate_skills.py:1568`, `anchor_contract_block_lines` `:1600`, line/token grammar `:111-112`; the OS-4 dict and budget `:751-794`; the division-of-labour comment `:744-750` |
| Anchor-contract asymmetry | **Measured this phase:** orchestration `SKILL.md` has **9** `#### … contract` blocks (`:688, 840, 1034, 1090, 1117, 1544, 1584, 1686, 2170`); the loop `SKILL.md` has **0** |
| The shared `decision_policy` block is at its budget | **Measured this phase with the validator's own regex:** `90 / 90` in *both* Skills against `DECISION_POLICY_MAX_LINES = 90` (`decision_policy.py:130`, C7 at `validate_skills.py:2313-2318`) |
| The narrative Decision Record section already emits `DECISION_STATE:` | `reviews/common.md:190-193`; optionality anchor `validate_skills.py:725-727`, "absent section is not a finding" `reviews/common.md:200` |
| Baseline, re-run this phase | `python3 scripts/validate_skills.py` → `Skill validation PASSED (648 checks)`; `git diff --check` clean; `git status --short` shows untracked `artifacts/` only |

---

## Proposed Design

### D1 — the terminal vocabulary: **O1**

**Decision.** A decision block terminates the run with the **existing** `RUN_STATUS: BLOCKED`, a
`reason` drawn from a **closed OS-29 set**, and two **sparse** `ORCHESTRATOR_LOG` columns that carry
the state and reason code in *columns* rather than in free text.

**The closed reason set** (nine shapes, eight constants plus one grammar):

```text
DECISION_GATE_INPUT_MISSING                      # A1 / F1: no record where one is required
DECISION_GATE_INPUT_MALFORMED                    # A4-i, A4-iii, A4-iv / F2, F4, F5, F6
DECISION_GATE_INPUT_UNBOUND                      # A3 / P6b row 7
DECISION_LEDGER_INCONSISTENT                     # A2
DECISION_LEDGER_SCHEMA_UNSUPPORTED               # A4-ii  (PLAN W-7, settled)
DECISION_GATE_DECLARATION_DISAGREES_WITH_LEDGER  # A6
DECISION_GATE_SUMMARY_DISAGREES_WITH_RECORD      # the Markdown/record drift reason (new here, D2)
DECISION_DOWNGRADE_REJECTED                      # P6b row 6
DECISION_BLOCKED:<STATE>:<reason_code>           # P6b rows 2, 4, 5, 8 -- the grammar
```

The grammar is `^DECISION_BLOCKED:(NEEDS_INPUT|CONFLICT):[a-z][a-z0-9_]*$`.

**Grounds.**

* `BLOCKED` is already a member of `RUN_STATUS_VALUES` (`run_logging.py:105`), so nothing is added
  to a set that is validated eagerly and *raises* (`orca_runtime_harness.py:2298-2301`, C-3).
* A closed-set-looking constant in the free-text `reason` field is an **established** extension:
  `UNIT_TEST_BLOCKED` / `UNIT_TEST_EVIDENCE_MISSING` (`e2e_harness.py:109-110`),
  `MAX_ITERATIONS_REACHED` (`:1225`), `FINAL_REVIEW_MAX_ITERATIONS_REACHED` (`:1576`),
  `OUT_OF_SCOPE_FINAL_REVIEW_FINDING` (`:1605`), and `REASON: PREVIOUS_PHASE_CHANGE_REQUIRED`
  (`SKILL.md:1086-1088`).
* The **free-text objection** is answered by the columns, not by the reason string: the
  ORIGINAL_REQUEST forbids workflow control depending on free-form Markdown, and OS-3's own comment
  (`run_logging.py:74-80`) records sparse columns as the mechanism for exactly this.
* **O2 is contra-indicated and not taken.** `WAITING_FOR_INPUT` is OS-31's named deliverable; taking
  it would imply a resume this ticket must not claim.
* **O3 is rejected.** `workflow_contract.py:97` asserts the Worker value set is *exactly*
  `{COMPLETE, BLOCKED}` and `_find_choice` (`:45-63`) requires every documented occurrence to agree.

**Executed** (`prototypes/d1_d2_d3_transition.py`, cases D1-1…D1-9): `RUN_STATUS_VALUES` is
unchanged and does not contain `WAITING_FOR_INPUT`; `ROUND_KIND_VALUES` stays at four; the reason
grammar accepts every blocking reason code the shipped contract defines and rejects a non-blocking
state; the eight refusal reasons are distinct; and `load_workflow_output_contract` still reports the
worker pair as exactly `{COMPLETE, BLOCKED}`.

**Why this is not a manufactured escalation.** The objective reserves user authority for the case
where the blocked outcome *cannot* be expressed with the existing terminal vocabulary. It can:
`BLOCKED` + a reason constant + two columns adds **no** value to any of the four vocabularies. Per
the task spec, this is an ordinary settled design call and it is made here.

---

### D2 — the decision channel for the agent-emitted half (B2/B3)

**Decision: both halves, in one channel — the agent's own result body.** Two objects:

1. **`DECISION_GATE_STATE: <CLEAR|ASSUMPTION_ALLOWED|NEEDS_INPUT|CONFLICT>`** — a **required**
   field line, parsed with `_parse_choice`'s exact strictness (exactly one occurrence, value inside
   the closed set). This is the **declaration**.
2. **A required fenced ```` ```decision-gate ```` block** holding one JSON object — the ledger
   record. This is the **authority**.

The gate parses (1), parses (2), **reconciles them**, then runs `decision_policy.validate_record()`
and the A4 clauses on (2). Any of: no field line, no block, more than one of either, an unknown
state, unparseable JSON, or `record["state"] != declared` is a **terminal refusal**, never a `CLEAR`.

**Why a distinct field name matters, and why `DECISION_STATE` cannot be reused.**
`reviews/common.md:190` already prescribes `DECISION_STATE:` inside the **optional narrative**
`## Decision Record` section, and `DECISION_STATE: CLEAR` matches `FIELD_LINE` (`e2e_harness.py:52`)
exactly. Reusing that name would make the gate read the optional narrative — collapsing the two
objects and destroying **R-A2-1**. The gate field is therefore `DECISION_GATE_STATE`, a name that
appears nowhere in the shipped templates or reviews.

**Why the result body rather than a file the agent writes.** On the live Orca path the Coordinator
has no guarantee that the Worker's filesystem is its own; the settled **body** is the one channel
the runtime already reads across that boundary (`orca_runtime_harness.py:2138-2139`, where
`_reviewer_gate_result` / `_reviewer_review_verdict` already parse `attempt.body`). Anchoring the
channel anywhere else would work in `e2e_harness` and be unenforceable in the runtime.

**Why a fenced block rather than more field lines.** Four of the thirteen required fields
(`evidence`, `assumption`, `open_item`, `source_binding`) and every OS-28 evidence key are
structured, and OS-28 reason codes are **lowercase** (`blast_radius_beyond_scope`), which
`FIELD_LINE`'s `[A-Z_]+` value class cannot carry. A fenced JSON block is the repository's own
precedent for a machine-readable payload inside Markdown (the ```` ```policy-contract ```` block
parsed by `parse_decision_policy`, `decision_policy.py:200`).

**Both requirements are satisfied, and the satisfaction is separable.**

* **R-A2-1** — the narrative section stays optional. `DECISION_RECORD_OPTIONALITY_ANCHOR`
  (`validate_skills.py:725-727`) and `reviews/common.md:200` are **byte-unchanged**; the gate never
  reads the narrative.
* **R-A2-2** — the gate result is required and explicit. "No decision was needed" is *asserted* as
  `CLEAR` with grounds that satisfy `no_open_decision_item` (`decision_policy.py:867-877`), never
  inferred from an absence.

**Executed** (cases D2-1…D2-13). A valid `CLEAR` admits; an absent field line, an absent block, a
duplicated field line and an unknown state each fail closed with their own reason; a
summary/record disagreement produces `DECISION_GATE_SUMMARY_DISAGREES_WITH_RECORD`; **the ANALYSIS
iteration-1 F-001 defect** (Markdown reading as correct while the record carries
`"reason_code": "(none - CLEAR carries no reason code)"`) is refused; the narrative
`DECISION_STATE: CLEAR` line is **present in the refused case** and stripping the gate field from it
still refuses, which is the non-vacuity half proving the narrative alone never admits; the existing
`STATUS` parse still yields exactly `["COMPLETE"]`; a well-formed `NEEDS_INPUT` is admitted as a
*valid input* and routed by the decision axis rather than treated as an input error.

**The SKILL.md / reviews additions do not break the contract loader — proved on a real copy.**
Adding `DECISION_GATE_STATE: CLEAR | ASSUMPTION_ALLOWED | NEEDS_INPUT | CONFLICT` to both
`SKILL.md` §11 and `reviews/common.md` leaves `load_workflow_output_contract` returning a
**byte-identical** `WorkflowOutputContract` (case D2-12). The **control** (case D2-13) is
load-bearing: a four-value line whose values contain **no underscore** *does* collide with
`REVIEW_VERDICT_LINE` (`workflow_contract.py:19-22`, value class `[A-Z]+`) and raises
`WorkflowContractError`. The underscores in `ASSUMPTION_ALLOWED` / `NEEDS_INPUT` are what keep the
addition safe, and that is now a fact of the evidence rather than an assumption.

**C9 is therefore ~0 lines.** `workflow_contract.py` needs no change: the gate vocabulary is owned
by `decision_gate.py` and the Skill text is documentation of it, the same relationship
`UNIT_TEST_STATUS_VALUES` (`e2e_harness.py:104`) already has.

---

### D3 — the non-consumption site: `gate_attempts()`

**Decision.** One edit, in the closure at `e2e_harness.py:1501-1507`:

```python
def gate_attempts(result: WorkflowResult) -> int:
    """SKILL.md section 13 ... (existing docstring kept)

    OS-29: a decision block is not a quality failure, so it charges no correction
    iteration -- at any risk level. Keyed on `decision_block` and NOT on risk, which
    is what lets ONE rule cover both terminal shapes P6b produces: the LOW round that
    ends at B2 with a Worker attempt appended (:1012) and the MEDIUM/HIGH round that
    ends at B3-V with a Reviewer attempt appended (:1194).
    """
    if result.decision_block is not None:
        return 0
    return len(result.worker_attempts if risk == "low" else result.reviewer_attempts)
```

**Grounds.** All three increment sites (`:1521`, `:1635`, `:1712`) already call this closure, so one
edit covers all three; the three-site alternative is three edits that can drift (R-8) and would have
to reach past the dispatch ledgers written at `:1631-1634` and `:1708-1711` — which **C-9** forbids
("written BEFORE any verdict is applied … never rewound"). Keying on `decision_block` rather than on
risk is settled by PLAN **W-6** and is what makes the single rule correct at LOW and at MEDIUM/HIGH.

**Executed** (cases D3-1…D3-7), with its mandatory NV-2 control in the same run: a decision-blocked
LOW round charges 0; a decision-blocked HIGH round charges 0; **and** a quality-`FAIL` HIGH round
still charges 1, a passing LOW round still charges 1, a passing HIGH round still charges 1. The
blocked HIGH round's `worker_attempts` and `reviewer_attempts` are both still length 1 — the
dispatch ledgers are not rewound (C-9). INV-D1 holds in every cell of the table.

---

### D4 — the ledger's shape: **a new run-scoped append-only record AND two sparse columns**

**Decision.** Both, with a stated division of labour:

* **The record is the authority.** One immutable JSON record per settled boundary, under
  `<ARTIFACT_ROOT>/decision_ledger/<NNNNNN>/record.json`, carrying **all thirteen** required fields
  plus six ledger-mechanics fields.
* **The columns are the index.** `decision_state` and `decision_reason_code` join
  `ORCHESTRATOR_LOG_COLUMNS`, sparse by design, so "which run blocked and why" is a column scan and
  not a directory walk.

**Grounds.** Five of the thirteen fields are entirely missing today and two are partial (ANALYSIS
A7); four of them (`evidence`, `assumption`, `open_item`, `source_binding`) are structured and
cannot live in a `|`-delimited cell whose contract is "every row fills every column"
(`run_logging.py:59-61`). Conversely a record alone would make the log unable to answer the
question the log exists to answer. The repository already runs exactly this split —
`parse_final_review_report` (`:1549-1617`) reconciles a human report against its audit record while
the log rows carry the joinable identifiers. The record's properties are **inherited, not
re-invented**: staged-then-published (`_stage_and_publish_audit_record` `:1779`), a closed required
field set (`_REQUIRED_RECORD_FIELDS` `:2358-2372` is the model), deterministic redaction
(`redact_text` `:1129`), and append-only — "the record is complete when it is written and is never
edited … correcting a record means writing a new record under a new key" (`:2148-2151`).

#### The complete machine-readable record schema

`ledger_schema_version = 1`. Types are JSON types. **Req** = required-ness.

**A. The thirteen fields the ticket requires.**

| # | Field | Type | Req | Meaning / constraint |
| --- | --- | --- | --- | --- |
| 1 | `run` | string | always | the run id; must equal the ledger's own run (A2) |
| 2 | `phase` | string | always | one of `CANONICAL_PHASES` ∪ specialized phases (`e2e_harness.py:60`); `""` is not permitted |
| 3 | `iteration` | integer ≥ 0 | always | `0` on the run-entry declaration; `1..max_iterations` on agent records |
| 4 | `state` | string | always | one of the four OS-28 states (`decision_policy.py:44-49`) |
| 5 | `reason_code` | string \| null | always **present** | `null` iff `state == "CLEAR"`; otherwise a key of the contract's `reason_codes` bound to that state. Enforced by `validate_record()` |
| 6 | `evidence` | object | always | the contract's `required_evidence[state]` keys and their values; `{}` for `CLEAR` (`required_evidence["CLEAR"] == []`) |
| 7 | `assumption` | string \| null | always present | the assumption being made; non-null **required** when `state == "ASSUMPTION_ALLOWED"` |
| 8 | `open_item` | string \| null | always present | the open question or conflict; non-null **required** when `state ∈ {NEEDS_INPUT, CONFLICT}` |
| 9 | `responsible_phase` | string | always | the phase that owns a correction; same vocabulary as `phase` and matched by `RESPONSIBLE_PHASE_LINE` (`e2e_harness.py:79-81`) |
| 10 | `role` | string | always | closed: `coordinator` \| `worker` \| `reviewer` |
| 11 | `verdict` | string | always present | the **quality** axis verdict this record was recorded beside: `""` \| `PASS` \| `FAIL`. `""` on Worker and run-entry records — the two axes stay separate and the field is never used to derive the decision axis |
| 12 | `source_binding` | string | always | the run-relative artifact path this judgement was made from, from `task_context.phase_artifact_contract` (`:284`) |
| 13 | `recorded_at` | string | always | ISO-8601 UTC, `run_logging.now_iso()` (`:165`); same shape as the audit record's `recorded_at` (`:2365`) |

**B. The six ledger-mechanics fields** (PLAN P7 — additional to the thirteen, never a substitute).

| Field | Type | Req | Meaning / constraint |
| --- | --- | --- | --- |
| `ledger_schema_version` | integer (a `bool` is **not** an integer) | always | must be in `decision_gate.SUPPORTED_LEDGER_RECORD_SCHEMA_VERSIONS`. **Distinct** from the policy block's `schema_version` (`decision_policy.py:42`), which `validate_record()` never reads |
| `boundary` | string | always | closed: `B1` \| `B2` \| `B3` |
| `sequence` | integer ≥ 0 | always | gapless `0..n-1` across the ledger (A4-iv); `0` is the run-entry declaration and only it (A2) |
| `source` | string | always | closed: `coordinator:run_entry` \| `worker` \| `reviewer` |
| `prior_open_decision_items` | array of ledger keys | run-entry record only; `[]` elsewhere | A6 **recomputes** it from the on-disk ledger and refuses on disagreement |
| `verifies` | object \| null | always present | `{run, phase, iteration, worker_record_key}` on a **B3-V** record; `null` elsewhere. A reference to a *ledger record*, never a link between two decisions (D5) |

**C. OS-28 contract evidence keys** ride alongside and are validated by `validate_record()`:
`boundary_element`, `blast_radius`, `monetary_cost`, `security`, `privacy`, `compliance`,
`long_term_lock_in`, `reversibility`, `impact`, `policy_source`, `retraction_condition`,
`what_is_missing`, `why_policy_cannot_decide`, `classification_attempted`, `citations`,
`why_they_cannot_both_hold`, `open_decision_item`, `grounds`, `scope`, `user_decision`.

**D. The field set is CLOSED.** `decision_gate.CLOSED_LEDGER_RECORD_FIELDS` is exactly A ∪ B ∪ C. A
record carrying anything else is `DECISION_GATE_INPUT_MALFORMED`. This is what makes D5's boundary a
check rather than a promise.

**Ledger key.** `"{run}/{phase}/{iteration}/{boundary}#{sequence}"` — the same shape the A1–A6
prototype already uses, and the value `verifies.worker_record_key` and
`prior_open_decision_items[]` hold.

**Executed** (`prototypes/d4_d5_d8_ledger.py`, cases D4-1…D4-7): for **each of the four states** a
concrete record carries all thirteen required fields and all six mechanics fields, is **accepted by
`validate_record()` against the shipped contract**, and carries no field outside the closed set.
The two controls are co-located: a `CLEAR` carrying a `reason_code` is rejected, and a
`NEEDS_INPUT` missing one required-evidence field is rejected — so the acceptances are not the
validator ignoring the records. The existing `redact_text` is called and actually redacts.

#### The columns

Two values are appended to `ORCHESTRATOR_LOG_COLUMNS` (`run_logging.py:62-86`), positioned
immediately after `round_kind` and before `result`, which is where OS-3 put its own four:

```text
"decision_state",        # one of the four OS-28 states; blank where the event has none
"decision_reason_code",  # the OS-28 reason code; blank for CLEAR and where the event has none
```

`TIMING_LOG_COLUMNS` is **not** changed: it measures durations, and a decision state is not one.

Three `--event` values are added to the **already-open** event vocabulary, on the precedent stated
at `run_logging.py:974-977`:

```text
EVENT_DECISION_RECORD_WRITTEN = "decision_record_written"
EVENT_DECISION_GATE_REFUSED   = "decision_gate_refused"     # a B1 refusal: no dispatch happened
EVENT_DECISION_BLOCK          = "decision_block"            # a B2/B3 terminal decision block
```

---

### D5 — how far Decision ID / lineage goes

**Decision.** Exactly three things, and then a closed set that stops it:

1. **The ledger key** `run/phase/iteration/boundary#sequence` — a stable identity for one recorded
   judgement. It is an *identity*, not a request/response identity.
2. **`verifies`** — a B3-V record's reference to the B2 record it verifies. The audit needs it
   (otherwise "the Reviewer verified something" is unfalsifiable) and P6b row 7 depends on it.
3. **`prior_open_decision_items`** — the run-entry declaration's re-checkable claim, recomputed by
   A6.

**And it stops there.** `supersedes`, `superseded_by`, `request_id`, `response_id`, `options`,
`recommendation`, `answered_at`, `answered_by` are **outside** `CLOSED_LEDGER_RECORD_FIELDS`, so a
record carrying one is `DECISION_GATE_INPUT_MALFORMED`. **Scenario 8** (downstream expands an
existing user decision) is discharged by requiring a **new decision event or escalation** — a new
ledger record whose `state` is `NEEDS_INPUT`/`CONFLICT` — and never by writing a link from it to the
decision it widens. That is PLAN's L3/R-10 boundary made executable.

**Executed** (cases D5-1…D5-5): `verifies` is inside the closed set; every one of the eight OS-30
fields is outside it; a record carrying `supersedes` is detected by the closed-set check; a B3-V
record's `verifies.worker_record_key` resolves to the Worker's own B2 ledger key; and the control
shows an unresolvable `verifies` is detectable, which is P6b row 7.

---

### D6 — the tenth anchor contract, and the mirrored-vs-not partition

**Decision (orchestration-only half).** One new `#### Decision gate contract` block in
`orca-worker-reviewer-orchestration/SKILL.md`, its dict
`DECISION_GATE_CONTRACT` and budget `DECISION_GATE_CONTRACT_MAX_LINES = 20` in `validate_skills.py`,
validated with the **shared** `parse_anchor_contract` (`:1568`) exactly as OS-4's block is
(`validate_agent_profile_contract` `:2153-2186`).

````text
#### Decision gate contract

```text
DECISION_GATE_BOUNDARIES = before_phase_entry, after_worker_result, after_reviewer_result
DECISION_GATE_INPUT = explicit_machine_readable_record_never_absence
DECISION_GATE_AXIS_ORDER = decision_axis_then_quality_axis
DECISION_GATE_LEDGER = artifact_root_decision_ledger_append_only
DECISION_GATE_LEDGER_ENTRY_SEQUENCE = zero
DECISION_GATE_LEDGER_PRODUCER = coordinator_at_run_open
DECISION_GATE_ADMISSIBILITY = non_empty, single_entry_declaration, schema_supported, bound_head, declaration_recomputed, no_unresolved_open_item
DECISION_GATE_BLOCKING_STATES = needs_input, conflict
DECISION_GATE_TERMINAL_STATUS = blocked
DECISION_GATE_LOW_TERMINAL_BOUNDARY = after_worker_result
DECISION_GATE_MEDIUM_HIGH_TERMINAL_BOUNDARY = after_reviewer_result
DECISION_GATE_REVIEWER_PARTICIPATION = already_scheduled_reviewer_in_verification_mode
DECISION_GATE_NEW_DISPATCH_SITES = none
DECISION_GATE_ITERATION_ACCOUNTING = decision_block_consumes_no_correction_iteration
DECISION_GATE_DOWNGRADE_AUTHORITY = policy_contract_transition_rule_only
DECISION_GATE_RISK_INDEPENDENCE = identical_terminal_outcome_at_every_risk_level
DECISION_GATE_RESUME = not_implemented_terminal_only
DECISION_GATE_AUTHORITY = machine_record_over_markdown_summary
```
````

Every key names an **Orca lifecycle** fact — where the gate runs, what it reads, where the terminal
is recorded, whether a dispatch site is added, how the counter behaves, whether resume exists. None
of them redefines a state's meaning, a reason code, an entry clause or an evidence requirement:
those are the **shared** contract's, which stays untouched at 90/90 lines (C-1/C-2/C-3).

**Decision (mirrored half).** Three sentences, byte-checked in **both** Skills, in the same shape as
`DECISION_POLICY_SKILL_PROSE_ANCHORS` (`validate_skills.py:713-724`, checked as C12 at
`:2474-2478`):

```python
MIRRORED_DECISION_SEMANTICS_ANCHORS = (
    "gate 경계에서 decision 결과는 필수이며 명시적이다. 섹션의 optional 여부와 다른 객체다.",
    "\"결정할 것이 없었다\"는 CLEAR로 단언되어야 하며 기록의 부재로 추정될 수 없다.",
    "기계가 읽는 record가 authority이고 Markdown 요약은 사람을 위한 설명이다.",
)
```

plus the `DECISION_GATE_STATE` result-contract line, which is mirrored into orchestration §10
(`SKILL.md:1481`) / §11 (`:1504`) **and** loop §14 (`:874`) / §16 (`:916`), and into all fourteen
`templates/*.md` and both `reviews/common.md`.

**The exact partition.**

| Mirrored into **both** Skills (decision **semantics**) | Kept **orchestration-only** (Orca **lifecycle**) |
| --- | --- |
| the meaning of the four states and their evidence — already shared via the `decision_policy` block (C4 raw-equality, `validate_skills.py:2481-2486`) | dispatch blocking at B1, and the pre-`start_worker` guard |
| the three `MIRRORED_DECISION_SEMANTICS_ANCHORS` sentences | `RUN_STATUS` and the closed OS-29 reason set |
| the `DECISION_GATE_STATE` line and the ```` ```decision-gate ```` block in §10/§11 ↔ §14/§16 | the two sparse `ORCHESTRATOR_LOG` columns and the three new events |
| the same addition in all 14 `templates/*.md` and both `reviews/common.md` | `round_kind`, terminal/Dispatch provenance, the Final Review audit record |
| the fail-closed source list and `forbidden_authority_sources` | the whole `#### Decision gate contract` block |
| the byte-identical optionality sentence, **unchanged** | the decision ledger's on-disk layout and its producer |

**How a validator makes drift a FAILURE — in all three directions.** `validate_decision_gate_contract()`:

* (a) each mirrored anchor is present in **orchestration**; mutate it in one Skill ⇒ FAIL;
* (b) each mirrored anchor is present in **the loop**; delete it from **both** ⇒ still FAIL — this
  is why an anchor **set** is needed and byte-equality between the two Skills is not enough, the
  reason already recorded at `validate_skills.py:711-712`;
* (c) `"#### Decision gate contract"` is present in orchestration and **absent** from the loop; copy
  it across ⇒ FAIL.

Plus the existing gates, unmodified: C4 raw-equality, `test_policy_smoke.py:21-25` (both
`SKILL_PATHS`), `test_decision_policy.py:1462-1470`.

**Executed** (`prototypes/d6_d7_parity_migration.py`, cases D6-1…D6-15). The proposed block parses
with the **shipped** `parse_anchor_contract`, round-trips key-for-key and value-for-value, fits its
20-line budget, and every value matches the shipped lowercase-snake token grammar
(`validate_skills.py:112`). The two controls prove the parser is not blind: an UPPERCASE value is
rejected, and a duplicate key is rejected. The block redefines **no** decision semantics (a
ten-token semantics scan finds nothing). On real copies of both Skill trees the shipped-pair shape
PASSes, and each of the three drift directions FAILs with its own distinct reason, with the
unmutated pair still PASSing afterwards as the co-located control. Finally: the loop Skill still has
**zero** `#### … contract` blocks and orchestration goes from **nine to ten**.

---

### D7 — the fail-closed migration for existing fixtures

**Decision.** The gate is **always armed**. `fake_worker.py` and `fake_reviewer.py` emit
`DECISION_GATE_STATE: CLEAR` and a matching ```` ```decision-gate ```` block **by default**, with
`--decision-gate-state` (choices: the four states) and an unconstrained
`--decision-gate-record-raw` malformed seam beside the existing `--unit-test-status-raw`
(`fake_worker.py:41`, whose rationale is at `:34-39`).

**This deliberately inverts the OS-3 precedent, and says so.** `fake_worker.py:28-32` records
"Opt-in: the default emits NOTHING, so every existing scenario's output stays byte-identical". That
was right for an *evidence* field whose absence is a legitimate state. It is wrong here: under a
fail-closed gate, a default of silence would either break every scenario or force an arming flag —
and an arming flag **is** the fail-open default the *Fail-closed rules* forbid. The agents therefore
**assert** `CLEAR`, which satisfies R-A2-2 (an agent asserting a state) and is not the engine
presuming one.

**Scope.** PLAN P6a settles that **B1 needs no fixture migration at all** — the harness writes the
run-entry declaration at run open (RU-12), so all ~1496 existing tests obtain their explicit
sequence-0 `CLEAR` with **no fixture edited**. D7 therefore covers only the B2/B3 agent
declarations, and its whole surface is the two fake agents plus the assertion sites that read their
output.

**Measured blast radius, this phase:** `grep -rlE 'fake_worker|fake_reviewer' --include=test_*.py
scripts/` returns exactly one module — `scripts/test_e2e_harness.py`. Every change there is
**additive to the expected output**; no check is relaxed, and the diff for each site is to be
justified in the IMPLEMENTATION artifact.

**Executed** (cases D7-1…D7-7): the migrated default declares `CLEAR` rather than staying silent; a
silent agent is refused at **all three** risk levels, because no opt-in flag exists; the default
does not disturb the existing `STATUS` parse; a blocking declaration travels the same default
channel. The **anti-pattern control** is the important half — the opt-in alternative is constructed
and shown to **fail open** (`ADMITTED:CLEAR`) on exactly the silent input the always-armed design
refuses. That makes "no fail-open default" a demonstrated property rather than a claim.

---

### D8 — the ledger's sequence-collision primitive

**Decision.** Two independent mechanisms, both implemented:

**(a) Writer-side: reuse the existing directory-rename primitive, generalized by one line.** The
published key is the zero-padded sequence; the payload is `{record.json: <text>}`. A second writer
claiming a published sequence gets a typed `DecisionLedgerCollision` — never an overwrite.

**(b) Reader-side: mandatory regardless.** A2 (exactly one `sequence == 0`, and it is the run-entry
declaration) and A4-iv (gapless `0..n-1`) run at **every** boundary, independent of who wrote the
records, with **F12** as the fixture.

**PLAN D8's premise, checked rather than assumed.** PLAN D8 says
`_stage_and_publish_audit_record`'s `os.rename` (`run_logging.py:1779`) "OVERWRITES on POSIX". That
is true of a **file** rename and **false of the directory rename this function actually performs** —
and the function's own docstring already says so at `:1818-1820`: *"NOT os.replace: replace would
overwrite. rename onto an existing non-empty directory fails, which is the immutability guarantee
obtained atomically rather than by a precheck that races."* **Executed on this platform** (Darwin
25.5.0), case D8-2a: renaming a directory onto a non-empty directory raises `OSError:66`
(`ENOTEMPTY`); case D8-2b, the control: a **file** rename silently overwrites. So the hazard PLAN
named is real for a file-per-record ledger and absent for a directory-per-record one — which is
precisely why the directory shape is chosen and no `O_EXCL` scheme is introduced.

This refines a parenthetical premise; it changes **no** PLAN conclusion. D8's actual requirement —
"choose how two writers claiming the same sequence are prevented or detected" — is answered by (a),
and its mandatory clause — "reader-side detection is MANDATORY regardless" — is (b).

**The one residual hazard, and the precondition that closes it.** A directory rename onto an
**empty** directory *does* succeed (case D8-3a). A published ledger key can only come into existence
via a rename of an already-populated staging directory, so it is never empty at any instant — but
that is an argument, and OS-29 turns it into an enforced precondition: the generalized publisher
**raises on an empty `files` mapping** before it stages anything.

**The generalization, in full.** `_stage_and_publish_audit_record` (`:1779-1826`) changes in exactly
two places: the loop `for name in FINAL_REVIEW_AUDIT_FILENAMES:` (`:1812`) becomes
`for name, text in files.items():`, and a `if not files: raise RunLoggingError(...)` precondition is
added at the top. Both existing callers pass exactly those three filenames, so the change is
behaviour-preserving for them (asserted by the existing `test_run_logging.py` suite), and both
record families then share **one** durability scheme rather than two — which is what PLAN C3 asks
for ("rather than a second durability scheme").

**Sequence allocation.** `append_decision_ledger_record()` reads the ledger, computes
`next = max(existing) + 1` (or `0`), and attempts to publish under that key; on
`DecisionLedgerCollision` it re-reads and retries with the next free sequence, bounded at 8
attempts, then raises. In practice a run has one writer — `E2EHarness` is single-threaded and the
Orca path serialises dispatches — so this is a **detection** mechanism, not a contention one; it is
implemented anyway because a shared `run_id` across two processes is reachable (it is what makes
fixture **F10** a real case).

**Executed** (cases D8-1…D8-7): the **real** `_stage_and_publish_audit_record` publishes, then
refuses a second writer under the same key with `FinalReviewAuditCollision`, and the first writer's
bytes survive byte-identical. Three records publish under sequence-named keys, every published name
is a complete record, and publishing a later sequence leaves an earlier record's bytes **identical**
(append-only). Two writers forced onto the same claim receive **different** sequences (4 and 5) and
the ledger stays gapless. Reader-side: the honest ledger reads clean, a hand-planted duplicate
sequence is detected as `DECISION_LEDGER_INCONSISTENT`, a deleted record is detected as
`DECISION_GATE_INPUT_MALFORMED` (A4-iv), and the co-located control shows the unmodified ledger
still reads clean after both mutants — so the detector is not simply rejecting everything.

---

### The relationship between the Markdown summary and the machine-readable authority

**The rule, stated once.** *The machine-readable record is the authority at every boundary. The
Markdown is a human explanation of it. Where the two disagree, the run blocks — it never proceeds on
either.*

There are three Markdown/machine pairs in OS-29 and they are deliberately different:

| Pair | Markdown half | Machine half | If they disagree |
| --- | --- | --- | --- |
| **P-1 the gate result** | `DECISION_GATE_STATE:` in the agent's result | the ```` ```decision-gate ```` record in the same result | `DECISION_GATE_SUMMARY_DISAGREES_WITH_RECORD` — terminal at B2/B3 |
| **P-2 the phase artifact** | the **optional** `## Decision Record` section of `ANALYSIS.md` / `PLAN.md` / … | that phase's ledger record for the same `(phase, iteration, role)` | a Final Review blocking finding. Checked **only when the section exists** — its absence stays a non-finding (`reviews/common.md:200`) |
| **P-3 the run** | `ORCHESTRATOR_LOG.md`'s two sparse columns | the ledger record they index | a validator FAILURE at export time, on the pattern `parse_final_review_report` (`run_logging.py:1549-1617`) already uses |

**Why the optionality survives all three.** The optional object is the *narrative section*. The
required object is the *gate result*. They have different names (`DECISION_STATE` vs
`DECISION_GATE_STATE`), different homes (a `##` section vs a fenced block), and different failure
modes (absence is not a finding vs absence is terminal). `DECISION_RECORD_OPTIONALITY_ANCHOR`
(`validate_skills.py:725-727`) is byte-unchanged, and C13/C14 (`:2488-2505`) keep passing.

**The motivating case, and the fixture it becomes.** This run produced the drift live: ANALYSIS
iteration 1 wrote `REASON_CODE: (none — CLEAR carries no reason code)` — prose that reads as
correct, and a value `validate_record()` rejects for `CLEAR` (`decision_policy.py:1197`,
`:1208-1212`). It was caught by a human Reviewer, which is exactly what OS-29 must not depend on.
It is therefore a **required negative fixture**: a document whose Markdown looks right and whose
machine record is invalid must **fail** the validator. **Executed** (case D2-7): that exact string,
placed in the record of a result whose Markdown declares `CLEAR`, produces
`DECISION_GATE_INPUT_MALFORMED`. Case D2-8 co-locates the non-vacuity half — the narrative
`DECISION_STATE: CLEAR` line is *present* in the refused document, so the refusal is attributable
to the record and not to a missing narrative.

**The validator surface, concretely.**

```python
# decision_gate.py -- runtime, at every boundary
reconcile(declared_state, record)        # P-1; raises GateRefusal(SUMMARY_DISAGREES_WITH_RECORD)

# run_logging.py -- artifact-level, no scripts/ imports (see the note under D4/Components)
parse_decision_record_section(text)      # P-2; returns (state, reason_code) or None when absent
reconcile_decision_record_section(...)   # P-2; modelled on parse_final_review_report(:1549)

# validate_skills.py -- static, at CI time
validate_decision_gate_contract()        # the anchor block, the mirrored anchors, the asymmetry,
                                         # and that the optionality sentence is byte-unchanged
```

---

### The precise B1/B2/B3 control flow, keyed to the existing call sites

**Ordering rules** (PLAN P6b O-1/O-2/O-3, implemented, not restated):
`B1 → Worker dispatch → B2 → [Reviewer dispatch] → B3 → quality routing`; at every boundary the
**decision** axis is evaluated before the **quality** axis; risk selects *where* the terminal is
recorded, never *whether* it is terminal or *what* it says.

#### `run_workflow()` — B1, at four sites

```python
# :1490  the run-open statement becomes the ledger open (RU-12, settled by P6a)
root = run_logging.open_decision_ledger(
    self.run_id, base=self.workspace, phases=scenario.phases, risk=risk,
    ledger_schema_version=decision_gate.LEDGER_RECORD_SCHEMA_VERSION,   # caller-supplied; see below
)
last_settled: tuple[str, str, int] | None = None      # what the harness itself just settled

def b1(site: str) -> str | None:
    """The shared B1 guard. Returns a refusal reason, or None to admit.
    One function, four call sites -- the guard lives where the loop is (R-8)."""
    try:
        decision_gate.admit_head(
            self.policy,
            run_logging.read_decision_ledger(self.run_id, base=self.workspace),
            run_id=self.run_id,
            expected_settled_round=last_settled,
        )
    except decision_gate.GateRefusal as refusal:
        self._log_decision_gate_refused(site, refusal)   # via _safe_log; logging never gates (C-7)
        return refusal.reason
    return None

for phase in scenario.phases:                            # :1509
    refusal = b1("phase_gate")                           # ---- B1 site 1, immediately above :1520
    if refusal:
        return snapshot(self.contract.blocked_status, refusal)
    result = self._phase_harness(phase, self.max_iterations).run(phase_scenario)   # :1520
    phase_iterations[phase] += gate_attempts(result)                               # :1521
    last_settled = (self.run_id, phase, result.current_iteration)
    if result.final_status != self.contract.completed_status:                      # :1524
        ...unchanged...
```

The other three sites are identical two-line guards:

| B1 site | Placement | Neighbour it copies |
| --- | --- | --- |
| phase gate | immediately above `:1520` | the `SCENARIO_PHASE_MISSING` guard at `:1511-1515` |
| Final Review attempt open | immediately above `:1531`'s `while True:` body | — |
| T4 correction | beside the existing budget guard at `:1612` | `if phase_iterations[phase] >= self.max_iterations:` |
| T5a revalidation | beside the existing budget guard at `:1692` | same |

**Why `expected_settled_round` is an argument and not read off the ledger.** A3 is a *binding*
check: the head must be the record of the round the harness itself just settled. Deriving that
expectation from the same file it validates would make A3 a restatement of the file. It is therefore
supplied from in-memory round state — `None` at the run's first B1.

#### `run()` — B2, between `:1012` and `:1015`

```python
worker_attempts.append(AgentAttempt(iteration, worker_status, worker.stdout))   # :1012 unchanged

# ======== B2. ABOVE the STATUS: BLOCKED branch (:1015) and ABOVE the LOW return (:1070),
#              so the check exists at EVERY risk level (ANALYSIS V1 / R-1).
try:
    gate = decision_gate.parse_gate_result(worker.stdout, self.policy)     # P6b rows 1-3
except decision_gate.GateRefusal as refusal:                              # row 3, risk-independent
    return self._decision_blocked(iteration, refusal.reason,
                                  block=("INPUT", refusal.reason),
                                  worker_attempts, reviewer_attempts, finding_traces)
worker_key = self._append_decision_record(gate, phase=self.phase, iteration=iteration,
                                          role="worker", boundary="B2",
                                          verdict="", verifies=None)
verification_only = False
if gate.state in decision_gate.BLOCKING_STATES:                            # row 2
    if self._risk_or_default() == "low":                                   # LOW: terminal HERE
        return self._decision_blocked(iteration, decision_gate.block_reason(gate),
                                      block=(gate.state, gate.reason_code),
                                      worker_attempts, reviewer_attempts, finding_traces)
    verification_only = True          # MEDIUM/HIGH: fall through to the ALREADY-SCHEDULED Reviewer
# ======== end B2

if worker_status == self.contract.worker_blocked:      # :1015 unchanged; reached only when NO
    ... reason="WORKER_BLOCKED" ...                    #        decision block is present
```

`_decision_blocked(...)` is a private constructor for the same `WorkflowResult` the surrounding code
already builds, with `final_status = blocked_status` and the new `decision_block` field set. It adds
no dispatch, no round and no subprocess site.

#### `run()` — B3, after `:1194`, two modes on one code path

```python
reviewer_attempts.append(AgentAttempt(iteration, review_result, reviewer.stdout))   # :1194 unchanged

# ======== B3
try:
    rgate = decision_gate.parse_gate_result(reviewer.stdout, self.policy)
except decision_gate.GateRefusal as refusal:                          # rows 7 and 10
    return self._decision_blocked(iteration, refusal.reason, block=("INPUT", refusal.reason), ...)
self._append_decision_record(rgate, phase=self.phase, iteration=iteration, role="reviewer",
                             boundary="B3", verdict=review_result,
                             verifies=worker_key if verification_only else None)

if verification_only:                                                  # ---- B3-V, rows 4-7
    outcome = decision_gate.evaluate_verification(self.policy, gate, rgate, worker_key=worker_key)
    return self._decision_blocked(iteration, outcome.reason, block=outcome.block, ...)

if rgate.state in decision_gate.BLOCKING_STATES:                       # ---- B3-N, row 8
    for finding_id in parsed_findings:            # the SAME loop as :1209-1214, run BEFORE returning
        finding_traces.setdefault(finding_id, FindingTrace(finding_id, iteration)) \
                      .reviewer_iterations.append(iteration)
    return self._decision_blocked(iteration, decision_gate.block_reason(rgate),
                                  block=(rgate.state, rgate.reason_code), ...)
# ======== end B3

if review_result == self.contract.reviewer_pass:    # :1198 unchanged  -- row 9
    ...
for finding_id in parsed_findings:                  # :1209-1214 unchanged -- row 9 FAIL
    ...
```

Row 8 runs the finding-trace loop **before** returning, so the Jira acceptance criterion "the phase
Reviewer can FAIL it as a blocking finding" is satisfied by a recorded finding while the
ORIGINAL_REQUEST's "does not consume a correction iteration" is satisfied by `decision_block`.

**`evaluate_verification` implements rows 4–7 and nothing else**, and it delegates the downgrade
question entirely to `decision_policy.validate_transition()`. OS-29 writes **no** downgrade rule of
its own; an accepted downgrade is *recorded* and the round is still terminal (**L6**).

#### The live Orca path — B1 before `start_worker`

```python
# orca_runtime_harness.py, inside run_existing_task (:2382), BEFORE start_worker (:2456)
refusal = self._b1_guard(phase=phase, role=role, iteration=iteration)
if refusal is not None:
    self._log_pre_dispatch_failure(phase=phase, role=role, iteration=iteration, error=refusal)
    return self._pre_dispatch_refusal(refusal)     # no Task, no Dispatch, no terminal is created
```

`_log_pre_dispatch_failure` (`:2265`) already exists for exactly this shape of refusal and already
logs. `_log_attempt` (`:2094`) cannot be the gate — it runs **after** settlement by construction
(`:2126-2131`) — but it *is* the single funnel every settled dispatch passes, so it is where the
harness records `_last_settled = (run_id, phase, iteration)` for the next B1's
`expected_settled_round`, and where the two new sparse columns are filled.

**`start_run` (`:1529`)** becomes the third `open_decision_ledger(...)` call site, adjacent to the
ORCHESTRATOR_LOG / TIMING_LOG opens at `:1530-1536` — the same one-statement shape it has today.

---

### One PLAN mechanism refined — disclosed rather than applied silently

**What PLAN says.** C3 and P6a state that `run_logging.open_decision_ledger()` *"imports
`LEDGER_RECORD_SCHEMA_VERSION`"* from `decision_gate.py` to stamp what it writes.

**Why that exact mechanism is not implementable.** `scripts/run_logging.py:16-27` states that the
file has **ZERO imports from elsewhere in `scripts/` — not even `scripts.task_context` — on
purpose**, because `INSTALL.md`'s documented global install never copies `scripts/`, and the file is
**byte-duplicated** at `orca-worker-reviewer-orchestration/tools/run_logging.py` (parity enforced by
`validate_run_logging_tool_parity`, `validate_skills.py:2758-2783`; **`cmp` confirms byte-identity
this phase**). An import of `decision_gate` would make the installed Skill's logging CLI crash on
`ModuleNotFoundError` in any target project.

**The design that preserves every settled conclusion.** `ledger_schema_version` is a **required
keyword argument** of `open_decision_ledger()` and of `append_decision_ledger_record()`, with **no
default**, supplied by the callers — `e2e_harness.py` and `orca_runtime_harness.py`, both of which
already import freely from `scripts/`:

```python
run_logging.open_decision_ledger(..., ledger_schema_version=decision_gate.LEDGER_RECORD_SCHEMA_VERSION)
```

**What stays settled, verbatim.** `decision_gate.py` remains the **sole owner** of
`LEDGER_RECORD_SCHEMA_VERSION` and `SUPPORTED_LEDGER_RECORD_SCHEMA_VERSIONS`; `A4-ii` remains the
gate's check with the terminal reason `DECISION_LEDGER_SCHEMA_UNSUPPORTED`; the import direction
stays acyclic and `decision_gate` still imports nothing from `run_logging`; `open_decision_ledger()`
remains the B1 producer at RU-12's three sites; and the ledger record is still stamped with the
version at write time. This is a change of *how the value reaches the writer*, not of *who owns it*.

**It is strictly stronger, not merely equivalent.** The writer is not trusted: whatever version a
caller passes is re-checked by A4-i/A4-ii at the next boundary, which is the same
"declare-then-recompute" shape A6 already applies to `prior_open_decision_items`. A caller that
passes a wrong or unsupported value produces a **terminal refusal**, never a `CLEAR`.

**Consequential change-surface fact PLAN does not mention:** every `run_logging.py` edit must be
mirrored into `orca-worker-reviewer-orchestration/tools/run_logging.py` in the same commit, or
`validate_skills.py` fails. It is listed in *Expected Changed Files* as **C3b**.

---

## Components / Interfaces / Data Flow

### `scripts/decision_gate.py` (new — PLAN C1)

Imports `decision_policy` and the standard library. Imports **nothing** from `e2e_harness`,
`orca_runtime_harness`, `run_logging` or `task_context` (asserted statically over the module's AST,
PLAN W-1).

```python
LEDGER_RECORD_SCHEMA_VERSION: int = 1
SUPPORTED_LEDGER_RECORD_SCHEMA_VERSIONS: tuple[int, ...] = (1,)

DECISION_STATES  = ("CLEAR", "ASSUMPTION_ALLOWED", "NEEDS_INPUT", "CONFLICT")
BLOCKING_STATES  = ("NEEDS_INPUT", "CONFLICT")
BOUNDARIES       = ("B1", "B2", "B3")
SOURCES          = ("coordinator:run_entry", "worker", "reviewer")
ROLES            = ("coordinator", "worker", "reviewer")

GATE_STATE_FIELD = "DECISION_GATE_STATE"
GATE_RECORD_BLOCK: re.Pattern           # ```decision-gate ... ```
REQUIRED_LEDGER_RECORD_FIELDS: tuple    # the thirteen
LEDGER_MECHANICS_FIELDS: tuple          # the six
CLOSED_LEDGER_RECORD_FIELDS: frozenset  # A u B u C -- D5's boundary
GATE_REFUSAL_REASONS: tuple             # the eight closed constants

class DecisionGateError(ValueError): ...
class GateRefusal(DecisionGateError):   # carries .reason (closed) and .detail (free text)
    ...

@dataclass(frozen=True)
class GateResult:
    declared_state: str
    record: dict
    @property
    def state(self) -> str: ...
    @property
    def reason_code(self) -> str | None: ...

@dataclass(frozen=True)
class GateOutcome:
    reason: str
    block: tuple[str, str | None] | None

def block_reason(gate: GateResult) -> str: ...                  # DECISION_BLOCKED:<STATE>:<code>
def ledger_key(record: dict) -> str: ...                        # run/phase/iteration/boundary#seq
def parse_gate_result(text: str, policy) -> GateResult: ...     # fail-closed; raises GateRefusal
def validate_ledger_record(policy, record) -> None: ...         # A4-i/ii/iii + the closed field set
def admit_head(policy, records, *, run_id, expected_settled_round) -> dict: ...   # A1-A6
def evaluate_verification(policy, worker: GateResult, reviewer: GateResult, *, worker_key) -> GateOutcome:
    ...                                                          # P6b rows 4-7
```

**Risk-independence is structural.** No function above takes a `risk`, `profile` or
quality-profile parameter, asserted with `inspect.signature` exactly as
`test_decision_policy.py:1244-1300` already does for `permitted_states` — whose `:1298` comment
names this as "the anti-vacuity move: it proves risk is INERT, not merely absent". The harness's
*placement* of the terminal is risk-specific (P6b O-3); the terminal's **value** is not, and that
equality is scenario 7's assertion.

### `scripts/run_logging.py` (PLAN C3) — and its byte-identical twin (C3b)

```python
DECISION_LEDGER_DIRNAME         = "decision_ledger"
DECISION_LEDGER_RECORD_FILENAME = "record.json"
DECISION_LEDGER_STAGING_DIRNAME = ".staging"
EVENT_DECISION_RECORD_WRITTEN   = "decision_record_written"
EVENT_DECISION_GATE_REFUSED     = "decision_gate_refused"
EVENT_DECISION_BLOCK            = "decision_block"

class DecisionLedgerError(RunLoggingError): ...
class DecisionLedgerCollision(DecisionLedgerError): ...   # never an overwrite; no force, no update

def open_decision_ledger(run_id, *, base=None, phases, risk, ledger_schema_version) -> Path:
    """Provision the run artifact root AND write ledger sequence 0 in one statement, so a
    root can never exist without a ledger. Idempotent, first-writer-wins, the same way
    _ensure_table(:288) and _ensure_run_artifact_root(:306) already are."""

def append_decision_ledger_record(run_id, record, *, base=None) -> tuple[Path, int]:
    """Allocate the next free sequence and publish. Never edits a published record."""

def read_decision_ledger(run_id, *, base=None) -> list[dict]:
    """Every published record, ordered by `sequence`. It reads and orders; the A1-A6
    judgement is the gate's, not the reader's."""

def parse_decision_record_section(text) -> tuple[str, str | None] | None:   # P-2, drift
```

`_stage_and_publish_audit_record` (`:1779`) is generalized in the two places named under D8.

### `scripts/e2e_harness.py` (PLAN C2)

* `WorkflowResult` (`:160`) gains `decision_block: tuple[str, str | None] | None = None` and
  `decision_state: str = ""` / `decision_reason_code: str = ""`; `WorkflowRunResult` (`:270`) gains
  the same three for reporting.
* `self.policy = decision_policy.load_decision_policy(self._skill_path)`, loaded once beside the
  existing contract load.
* B2 / B3 / B1 as shown above; `gate_attempts()` as shown in D3.
* `:658` and `:1490` become `open_decision_ledger(...)`; `:1362` is a **read** and is unchanged.

### `scripts/orca_runtime_harness.py` (PLAN C4)

* `_b1_guard(...)` before `start_worker` (`:1763`, called from `run_existing_task` `:2382`, guard
  above `:2456`), reusing `_log_pre_dispatch_failure` (`:2265`).
* `start_run` (`:1529`) → `open_decision_ledger(...)`.
* `_log_attempt` (`:2094`) fills the two sparse columns and records `_last_settled`.
* The classification-verification framing rides in the **existing** `new_claims` / `drill_down` keys
  of `build_reviewer_context` (`task_context.py:343`); `REVIEWER_CONTEXT_KEYS` stays the closed
  eight-tuple asserted at `orca_runtime_harness.py:2483-2484` (**RU-6 / R-P4 honoured; no ninth key**).

### Data flow, end to end

```text
run open           open_decision_ledger()  ->  ledger[0] = run-entry declaration (CLEAR, recomputable)
                                                  |
B1 (4 sites)       read_decision_ledger() -> admit_head(A1-A6, expected_settled_round)
                        admit -> dispatch                     refuse -> RUN_STATUS: BLOCKED, no dispatch
                                                  |
Worker dispatch    :981 / start_worker      (unchanged site)
                                                  |
B2 (:1012->:1015)  parse_gate_result(body) -> reconcile -> validate_record -> append ledger record
                        CLEAR/AA -> admit         NEEDS_INPUT/CONFLICT -> LOW: terminal
                                                                          MED/HIGH: verification_only
                                                  |
Reviewer dispatch  :1158                    (unchanged site -- the ALREADY-SCHEDULED one)
                                                  |
B3 (:1194)         parse_gate_result(body) -> append ledger record (verifies = worker key in B3-V)
                        B3-V -> evaluate_verification -> terminal BLOCKED
                        B3-N -> block (row 8) | existing PASS/FAIL routing (row 9)
                                                  |
terminal           RUN_STATUS: BLOCKED + closed reason + two sparse columns + the ledger
```

---

## Error Handling / Compatibility

### Fail-closed behaviour, boundary by boundary

Every rejection already exists in `decision_policy.py`; OS-29 adds the **call** and the boundary.

| Presumption source (ORIGINAL_REQUEST) | B1 | B2 | B3 |
| --- | --- | --- | --- |
| missing record | `DECISION_GATE_INPUT_MISSING` (A1) — no dispatch | terminal | terminal |
| malformed record | `DECISION_GATE_INPUT_MALFORMED` (A4-i/iii/iv) | terminal | terminal |
| unsupported **ledger-record** schema | `DECISION_LEDGER_SCHEMA_UNSUPPORTED` (A4-ii) | same | same |
| unsupported **policy-block** schema | the Skill fails to load — `parse_decision_policy` `:200-214`, **existing and unchanged** | same | same |
| unknown state / unknown reason code | `validate_record()` (`:1189`, `:1198-1207`) | same | same |
| missing safety fact | `_undeclared_safety_facts` (`:803`) via `:1279-1288` | same | same |
| model confidence / agreement / recommended default | `forbidden_authority_sources` (`SKILL.md:322`), `_user_decision_defect` (`:682`) | same | **scenario 10** |
| timeout / non-response | same closed five-item list | same | **scenario 11** — and `decision_block` charges no iteration |
| summary/record disagreement | — | `DECISION_GATE_SUMMARY_DISAGREES_WITH_RECORD` | same |

**No new fail-closed semantics are invented.** OS-29 adds the call, the boundary, and two reasons
that describe *ledger* defects the OS-28 contract has no opinion about.

### Backward compatibility

**Existing runs.** Prior-run artifacts under `artifacts/runs/run_*` are never read as a decision
ledger and never modified. A directory with no `decision_ledger/` is simply not an OS-29 run; the
gate is only reachable from a run the new `open_decision_ledger()` opened.

**Existing fixtures.** B1 needs **none**: the harness writes the run-entry declaration at run open,
so every existing scenario gets its explicit sequence-0 `CLEAR` with no fixture edited (PLAN P6a).
B2/B3 need the two fake agents to declare by default (D7), whose measured blast radius is the single
module `scripts/test_e2e_harness.py`.

**The 1496-test suite.**

* **A fully-`CLEAR` run is a transition no-op.** The same dispatches (`sessions`,
  `worker_attempts`, `reviewer_attempts`), the same `phase_iterations`, the same
  `correction_dispatches` / `revalidation_dispatches`, the same `final_status` and `reason`. That is
  an explicit regression assertion, and it is what makes the whole suite the compatibility proof.
* **It is a transition no-op, not an artifact no-op.** Such a run additionally gains the
  `decision_ledger/` directory (one record per settled boundary), two sparse `ORCHESTRATOR_LOG`
  columns that are blank on existing rows, and up to three new event values. Any test asserting an
  exact run-directory listing or an exact log header is updated **additively**, and each such diff
  is justified in the IMPLEMENTATION artifact.
* **What the suite proves and what it does not.** Because the harness stamps the run-entry
  declaration and all four B1 sites admit it through the complete A1–A6 path *including*
  A4-i/A4-ii, every existing test exercises the record-version check on a real ledger. Its
  **non-vacuity** is F13/F14, not the suite: a green suite is equally consistent with a build that
  never reads a version.
* **One deliberate, ticket-mandated behaviour change.** A run whose agents declare nothing at B2/B3
  no longer proceeds. That is the *Fail-closed rules*, not a regression, and it is the single
  incompatibility IMPLEMENTATION must call out.

**The safety floor and the finding-resolution trace.** B2 sits above both (`:1029-1051`,
`:1053-1066`), so a decision-blocked round returns before them. This weakens nothing: those two
guards exist to stop a round **completing** without evidence, and a decision-blocked round returns
`BLOCKED` and can never reach `COMPLETED`. A Worker cannot "escape" the LOW safety floor by
declaring `NEEDS_INPUT` — it would trade one terminal `BLOCKED` for another, and the phase still
does not pass. Stated here so the interaction is a designed fact rather than an accident.

**Hard constraints re-verified this phase and honoured.** C-1 the shared block is at **90/90**
(measured with the validator's own regex); C-2 `left.raw == right.raw` — the block is not touched;
C-3 `RUN_STATUS_VALUES` unchanged; C-4 `ROUND_KIND_VALUES` unchanged; C-5 the exact worker pair
unchanged; C-6 the six byte-anchored prose sentences survive; C-7 logging never mutates a lifecycle
judgement (every OS-29 log write goes through `_safe_log`, `orca_runtime_harness.py:2076`, or the
`e2e_harness.py:1387-1389` equivalent); C-8 the T2 last-attempt guard stays first on the FAIL edge
(`:1569-1577`) — OS-29 adds nothing to that edge; C-9 the dispatch ledgers keep writing before any
verdict and are never rewound; C-10 the five CI gates.

---

## Expected Changed Files / Implementation Steps

The surface is a **subset** of PLAN P2, with two additions PLAN did not name (C3b, and C9 falling to
zero). Sizes are production lines, excluding tests.

| # | File | Change | Size |
| --- | --- | --- | --- |
| C1 | `scripts/decision_gate.py` **(new)** | the module surface above | ~230–300 |
| C2 | `scripts/e2e_harness.py` | B1×4, B2, B3, `gate_attempts()`, `decision_block` + two report fields, `_decision_blocked`, `_append_decision_record`, the two run-open calls, the policy load | ~170–230 |
| C3 | `scripts/run_logging.py` | the ledger constants, three exceptions/events, `open_decision_ledger`, `append_decision_ledger_record`, `read_decision_ledger`, `parse_decision_record_section`, two sparse columns, the `_stage_and_publish_audit_record` generalization | ~280–380 |
| **C3b** | `orca-worker-reviewer-orchestration/tools/run_logging.py` | **byte-identical copy of C3** — `validate_run_logging_tool_parity` (`validate_skills.py:2758-2783`) fails otherwise | = C3 |
| C4 | `scripts/orca_runtime_harness.py` | `_b1_guard` before `start_worker`, `start_run` → `open_decision_ledger`, `_last_settled` + the two columns in `_log_attempt` | ~90–140 |
| C5 | `scripts/validate_skills.py` | `DECISION_GATE_CONTRACT` + pattern + budget + `validate_decision_gate_contract()`; `MIRRORED_DECISION_SEMANTICS_ANCHORS`; the asymmetry check; the optionality-unchanged check | ~130–190 |
| C6 | `orca-worker-reviewer-orchestration/SKILL.md` | the tenth anchor block; §10 `:1481` / §11 `:1504` result contracts; §12 `:1712`, §13 `:1758`, §17 `:1989`, §18 `:2192`; correction of the stale `:368-369`; L1–L7 | ~90–150 |
| C7 | `orca-worker-reviewer-loop/SKILL.md` | §14 `:874` / §16 `:916` gain the **same** decision field + the three mirrored sentences; `:364-365` **stays**; still **zero** anchor blocks | ~35–65 |
| C8 | `templates/*.md` ×14, `reviews/common.md` ×2 | the gate-result relationship beside the Decision Record section; **the optionality sentence stays byte-identical** | ~10–20 each |
| **C9** | `scripts/workflow_contract.py` | **0 lines** — proved unnecessary by case D2-12 | 0 |
| C10 | `scripts/fake_worker.py`, `scripts/fake_reviewer.py` | declare by default + the two flags | ~20–35 each |
| C11 | `scripts/test_validate_skills.py:49-66` | add `decision_gate.py` to the copied-module list (the trap documented at `:57-64`) | 1–4 |
| C12 | `CHANGELOG.md`, `docs/ROADMAP.md` | one Unreleased entry; OS-29 status | ~10–20 |
| C13 | tests + `scripts/fixtures/decision_gate/{valid,invalid}/` | see *Testing Strategy* | ~1200–1800 |

`scripts/release_manifest.py` needs nothing: `INCLUDED_ROOTS` (`:44`) takes `scripts/` wholesale.

**Implementation order** (PLAN's DAG, unchanged, with C3b bound to C3):
`W-1 → W-2 → {W-2b → {W-5, W-8}}, {W-3 → {W-4 → W-6}}`; then `W-7 → W-9 → W-11`; `W-10` last among
production items; `W-12` alongside and completed after `W-11`; `W-13` last. **C3b is edited in the
same commit as C3, always.**

---

## Testing Strategy

PLAN P4 fixes the fourteen scenarios one-to-one and the three mandatory non-vacuity proofs; DESIGN
does not restate them. What DESIGN adds is the **placement and the shape** its own decisions imply.

| Subject | Module | Cases |
| --- | --- | --- |
| the parser/evaluator, A1–A6, F1–F8, F13/F14, the closed field set, `inspect.signature` risk-independence | `scripts/test_decision_gate.py` **(new)** | D1's reason grammar; D2's parse/reconcile matrix incl. **the ANALYSIS F-001 string as a required negative fixture**; D4's four state shapes; D5's closed-set check with `supersedes` as the negative |
| the producer, the ledger, the collision primitive, the columns | `scripts/test_run_logging.py` | F10, F12, F14's on-disk round trip; **D8's writer-side exclusivity and its ENOTEMPTY grounds**; append-only byte-identity; the empty-`files` precondition; the two-writer allocation retry |
| every transition cell, the counters, the dispatch ledgers | `scripts/test_e2e_harness.py` | P6b B2 rows 1–3 and B3 rows 4–10, each a named case; **the cross-risk equality of rows 2 and 4** on `final_status`/`decision_state`/`reason_code` with its "the three runs differ elsewhere" guard; NV-1, NV-2, NV-3/M-DUP; F9, F11 with its co-located control |
| the live pre-dispatch half | `scripts/test_orca_runtime_contract.py` | `live_red_written_at_start_run`, `live_red_absent_refuses` (**no Dispatch id is created**) |
| the validators and both drift directions | `scripts/test_validate_skills.py` | scenario 14 (a) one-Skill drift, (b) delete-from-both, (c) the Orca-only block leaking into the loop; the copied-module list at `:49-66` |
| the cross-cutting residue | `scripts/test_os29_decision_gate.py` **(new)** | the import-direction AST assertion; INV-D3's static call-site count; the `run_logging`-imports-nothing-from-`scripts/` assertion |

**Two shapes this DESIGN makes mandatory, which PLAN could not state before the mechanism existed:**

1. **The `run_logging` isolation assertion.** A test that parses `scripts/run_logging.py`'s AST and
   asserts it imports nothing from `scripts/` — including `decision_gate`. Without it, the refined
   mechanism can silently regress into the one PLAN wrote and break the installed Skill. Its
   co-located control asserts the AST walker finds the module's *real* imports, on the
   `test_os22_required_tests.py:549-557` rule that "a negative assertion over a walker that finds
   nothing proves nothing".
2. **The empty-`files` precondition test.** D8-3a shows a rename onto an empty directory succeeds,
   so the precondition is the only thing standing between the design and a silent replace. It gets
   its own positive/negative pair.

**Every non-vacuity control is co-located in the same test function**, per the repository's own rule
at `test_decision_policy.py:4-8`. The three prototypes shipped with this artifact already carry
their controls in this shape and are the executable model for the tests.

---

## Risks / Open Issues

Carried from PLAN P8 (R-1…R-11, R-P1…R-P6) — all mitigations remain as planned. New or sharpened by
this DESIGN:

| # | Risk | Trigger | Sev | Mitigation |
| --- | --- | --- | --- | --- |
| **R-D1** | **`run_logging` grows a `scripts/` import**, breaking the installed Skill with `ModuleNotFoundError` in a target project | PLAN C3's literal wording invites it | **High** | Caller-supplied `ledger_schema_version`, plus the AST assertion in `test_os29_decision_gate.py`. The failure is invisible in this repo's CI, which is why the assertion is mandatory |
| **R-D2** | **`tools/run_logging.py` drifts from `scripts/run_logging.py`** | C3 and C3b are two files | Medium | `validate_run_logging_tool_parity` (`validate_skills.py:2758-2783`) already fails on it; the mitigation is to edit both in one commit and let CI be the compiler |
| **R-D3** | **The gate field name collides with the narrative section**, collapsing the two objects and destroying R-A2-1 | `reviews/common.md:190` already emits `DECISION_STATE:`, which matches `FIELD_LINE` | Medium-High | The distinct name `DECISION_GATE_STATE`, with case D2-9 as the executed proof that the narrative alone never admits |
| **R-D4** | **A future four-value vocabulary line breaks `_find_review_verdict_choice`** | `REVIEW_VERDICT_LINE` matches any `FIELD: A \| B \| C \| D` of underscore-free uppercase values | Medium | Case D2-13 makes the hazard a fact of the evidence; the OS-29 state names carry underscores, and a regression test pins that the loader still returns an identical contract |
| **R-D5** | **A published ledger key is replaced** because the target directory was empty | D8-3a | Low-Medium | The empty-`files` precondition, plus reader-side A2/A4-iv which detects the result regardless |
| **R-D6** | **B2 above the safety floor is read as weakening it** | B2 returns before `:1029-1051` | Low | Designed and documented: both paths are `BLOCKED`; a decision block can never produce `COMPLETED`. Asserted as a named case |

### Limitations that remain because OS-30 and OS-31 do not exist

**L1–L6 are carried from the approved PLAN, unchanged:** L1 a blocked run **terminates** and cannot
be resumed — answering means a new run; L2 no question is asked in any structured form; L3 no
supersession lineage, and scenario 8's answer is escalation, not lineage; L4 no timeout semantics
beyond the contract's negative rule (`SKILL.md:322`); L5 at LOW there is no phase Reviewer
(`e2e_harness.py:1070`), so a LOW Worker's *misclassification* is caught only by the Final
Adversarial Review; L6 a decision block is terminal at every risk level even when a downgrade is
validly authorized, because acting on it would be resume.

**L7 is new here, and is a consequence of L1 on the live path.** `OrcaRuntimeHarness` has no
deterministic iteration counter (no `phase_iterations` anywhere in that file), so B1's
`expected_settled_round` comes from in-memory state recorded in `_log_attempt` (`:2094`). A **fresh
Coordinator process** resuming an existing run therefore has no such state; with a non-trivial
ledger its first B1 refuses with `DECISION_GATE_INPUT_UNBOUND`. That is **fail-closed and correct
for OS-29** — cross-session resume is OS-31's — but it must be written down rather than discovered:
in OS-29 a live run is gated only within the Coordinator process that opened it.

**L8, also new and also honest.** Because `run_logging.py` may import nothing from `scripts/`, its
CLI path cannot validate a ledger record against the OS-28 contract; it writes and reads, and the
**gate** judges. A ledger produced entirely through the CLI by an installed Skill is therefore
durable and ordered but not contract-validated until a gate reads it — which is the same
declare-then-recompute shape A6 uses, and never a path to `CLEAR`.

### Open Questions / Conflicts

**None requiring user authority.** Every ambiguity met in this phase was settled by an explicit
requirement in `ORIGINAL_REQUEST.md`, by the approved `ANALYSIS.md` / `PLAN.md`, by the current code,
or by executed evidence. The three items that could look like open decisions, and why none is:

* **D1's terminal vocabulary.** The objective reserves user authority only for the case where the
  blocked outcome *cannot* be expressed with the existing vocabulary. It can — O1 adds no value to
  any of the four sets — so no new lifecycle state is proposed and, per the task spec, this is an
  ordinary settled design call. **Manufacturing an escalation here would be the error.**
* **The `run_logging` import direction.** Not a choice between two acceptable designs: PLAN's
  literal mechanism is contradicted by a stated module invariant (`run_logging.py:16-27`) and by a
  shipped CI gate, so the only implementable option that preserves every settled conclusion is
  caller-supplied injection. It is reversible, touches no security, privacy, compliance, monetary or
  lock-in element, has no blast radius beyond `scripts/` and the Skill's `tools/` copy, and pits no
  two explicit requirements against each other. **Not escalated**, and disclosed in its own section
  rather than applied silently.
* **D8's premise.** The refinement is an executed fact about the platform
  (`OSError:66` / `ENOTEMPTY`), not a judgement call, and it *strengthens* the PLAN's requirement
  rather than relaxing it — reader-side detection is implemented regardless, exactly as D8 mandates.

No `NEEDS_INPUT` and no `CONFLICT` item arose in this phase, so this phase does not stop.

---

## Decision Record

The record is the machine-readable JSON at
`artifacts/runs/run_35b221ea299d/records/design_decision_record.json`; **it is the authority and the
prose here only describes it** — which is the very rule this phase designs. Per the OS-28 contract,
`CLEAR` carries **no** reason code, so the grounds that carry the state are the declared facts.

```json
{
  "state": "CLEAR",
  "reason_code": null,
  "open_decision_item": false,
  "run": "run_35b221ea299d",
  "phase": "design",
  "iteration": 1,
  "responsible_phase": "design",
  "role": "worker",
  "grounds": "...see the file; summarized above...",
  "scope": "Covers the DESIGN phase's own conduct at iteration 1. No new RUN_STATUS, round_kind or Worker STATUS value is proposed, so the one boundary the objective reserves for user authority was not reached."
}
```

**This record was VALIDATED, not merely described.** Command and verbatim output:

```text
$ python3 -c "
import json, sys; sys.path.insert(0, 'scripts')
from pathlib import Path
from decision_policy import load_decision_policy, validate_record, DecisionPolicyError
rec = json.load(open('artifacts/runs/run_35b221ea299d/records/design_decision_record.json'))
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

**NEGATIVE CONTROL** is exactly the ANALYSIS iteration-1 defect this DESIGN adopts as its motivating
drift fixture, and it is rejected — proving the acceptance is not the validator ignoring the field.
**CONTROL-2** flips the single fact that carries the state and is rejected by the `CLEAR` entry
condition (`decision_policy.py:867-877`) — proving the acceptance is not the validator ignoring the
grounds.

---

## Modified Files / Artifacts

Written by this phase (DESIGN produces a design, not code — **no tracked production file was
modified**):

* `artifacts/runs/run_35b221ea299d/DESIGN.md` — this document, written **in place** at the exact
  contracted path, no suffix.
* `artifacts/runs/run_35b221ea299d/records/design_decision_record.json` — **new**. Validated against
  **both** Skills' policies with two negative controls (see `## Decision Record`).
* `artifacts/runs/run_35b221ea299d/prototypes/d1_d2_d3_transition.py` — **new**. D1 terminal
  vocabulary, D2 decision channel, D3 non-consumption, and every cell of PLAN P6b's two tables.
* `artifacts/runs/run_35b221ea299d/prototypes/d4_d5_d8_ledger.py` — **new**. D4 record schema across
  all four states, D5 lineage boundary, D8 collision primitive against the **real**
  `run_logging` machinery.
* `artifacts/runs/run_35b221ea299d/prototypes/d6_d7_parity_migration.py` — **new**. D6 anchor
  contract through the **shipped** `parse_anchor_contract` plus the three drift directions on real
  Skill copies; D7 fail-closed migration with its fail-open anti-pattern control.

**Not written, deliberately:** no `REVIEW_*.md` file (those are the Reviewer's, one path per
iteration); `ANALYSIS.md` and `PLAN.md` are **unmodified**; `prototypes/a1_a6_admissibility.py` is
carried forward unchanged and only re-executed.

The three new prototypes are **run artifacts, not production code**: nothing under `scripts/`
imports them and `artifacts/` is outside `release_manifest.INCLUDED_ROOTS` (`:44`), so they cannot
affect CI or the release.

---

## Validation

| Check | Result |
| --- | --- |
| `python3 scripts/validate_skills.py` | `Skill validation PASSED (648 checks)` — **re-run this phase** |
| `python3 -m unittest discover -s scripts -p 'test_*.py'` | `Ran 1496 tests in 303.447s` → `OK (skipped=6)`, **exit 0** — **re-run this phase**, not inherited |
| `git diff --check` | clean, re-run this phase |
| `git status --short` | untracked `artifacts/` only; **no tracked source file modified** |
| Decision-record validation | positive against **both** Skills at `iteration: 1`, plus two negative controls. Verbatim output in `## Decision Record` |
| **D1 + D2 + D3 + P6b**, executed | `python3 artifacts/runs/run_35b221ea299d/prototypes/d1_d2_d3_transition.py` → **`40/40 cases behaved as specified`**, exit 0 |
| **D4 + D5 + D8**, executed | `python3 artifacts/runs/run_35b221ea299d/prototypes/d4_d5_d8_ledger.py` → **`40/40 cases behaved as specified`**, exit 0 |
| **D6 + D7**, executed | `python3 artifacts/runs/run_35b221ea299d/prototypes/d6_d7_parity_migration.py` → **`23/23 cases behaved as specified`**, exit 0 |
| PLAN's A1–A6 prototype, re-executed unchanged | `python3 artifacts/runs/run_35b221ea299d/prototypes/a1_a6_admissibility.py` → **`17/17 cases behaved as specified`**, exit 0 |
| C-1 measured with the validator's own regex | `DECISION_POLICY_BLOCK_PATTERN` body = **90 / 90** in **both** Skills against `DECISION_POLICY_MAX_LINES = 90` |
| Anchor-contract asymmetry measured | `grep -c '^#### .* contract$'` → orchestration **9**, loop **0** |
| `run_logging` twin parity measured | `cmp scripts/run_logging.py orca-worker-reviewer-orchestration/tools/run_logging.py` → **byte-identical** |
| D7 blast radius measured | `grep -rlE 'fake_worker\|fake_reviewer' --include=test_*.py scripts/` → **one** module, `scripts/test_e2e_harness.py` |
| POSIX rename semantics measured on this platform | directory→non-empty-directory `OSError:66` (ENOTEMPTY); directory→**empty**-directory succeeds; **file**→file overwrites; `open(..., "x")` on an existing file raises `FileExistsError` |

**Totals: 120 executed design cases across four prototypes, all passing, plus the 1496-test
regression suite and the 648-check skill validator, both green.** Every negative case in the three
new prototypes differs from an admitted positive in exactly one field or one fact, and every
non-vacuity control sits in the same run as the claim it protects.

**What the evidence does NOT prove**, stated so it is not over-read: these are prototypes, not the
shipped implementation. They prove the *design is implementable and its rules are non-vacuous*; they
do not prove `scripts/decision_gate.py` will be written correctly. That is IMPLEMENTATION's gate,
and PLAN P4/P10 already fix how it is judged.

---

## Unit Tests / Testing Strategy

DESIGN writes no production code, so no unit test is added by this phase. The testing strategy this
phase is responsible for **specifying** is `## Testing Strategy` above: the module that owns each
subject, the two new mandatory test shapes this DESIGN's decisions imply (the `run_logging`
zero-`scripts/`-imports AST assertion, and the empty-`files` precondition pair), and the requirement
that every non-vacuity control is co-located in the same test function
(`test_decision_policy.py:4-8`). PLAN P4's fourteen one-to-one scenarios, its F1–F14 fixtures and
its three mandatory non-vacuity proofs (NV-1 dispatch blocking, NV-2 iteration non-consumption,
NV-3 non-duplication via M-DUP) are carried forward unchanged and unweakened. **No existing test or
validator is scheduled for deletion or weakening**, and the mandatory
IMPLEMENTATION/BUGFIX/REFACTORING test gates are untouched at every risk level.

The three prototypes shipped with this artifact are the **executable model** for those tests: each
is a positive/negative matrix with its controls co-located, which is the shape
`test_decision_gate.py` and the additions to `test_e2e_harness.py` / `test_run_logging.py` /
`test_validate_skills.py` should take.

---

## Review Feedback Resolution

Not applicable. This is DESIGN **iteration 1**; no `REVIEW_DESIGN*.md` exists yet, so there is no
review feedback to resolve.

The findings closed in the approved upstream phases stay closed and their lessons are carried, not
merely cited:

* **ANALYSIS F-001** (a `CLEAR` record that still supplied a reason code) is applied twice: this
  phase's own decision record is validated against both Skills with that exact defect as its
  negative control, **and** the defect is adopted as the motivating fixture for the P-1 drift
  validator, executed as case D2-7 with its non-vacuity half at D2-8.
* **ANALYSIS F-002** (the round-kind proxy is not the non-duplication proof) is carried: this
  DESIGN adds **no** dispatch site, **no** subprocess site and **no** round — the verification-mode
  Reviewer is the already-scheduled one at `:1158` — and INV-D1 is asserted to hold in every cell of
  the transition table (case D3-7), with M-DUP remaining the required non-vacuity control at TEST.
* **PLAN F-001** (the ledger-record schema version distinct from OS-28's policy-block version) is
  implemented, not re-decided: `decision_gate.py` remains the sole owner of both constants, A4-ii
  keeps the terminal reason `DECISION_LEDGER_SCHEMA_UNSUPPORTED`, and the A1–A6 prototype is
  re-executed unchanged at 17/17.
* **PLAN F-002** (one risk-specific transition table) is implemented as written: LOW terminates at
  B2, MEDIUM/HIGH at B3-V, with the **same** `final_status`, `decision_state` and `reason_code` —
  executed as cases P6b-1…P6b-3, with the "the three runs differ elsewhere" control at P6b-4.

One PLAN **mechanism** — `run_logging` importing `decision_gate` — is refined rather than applied,
for a reason stated in its own section with the contradicting `file:line` evidence and the CI gate
that would catch it. Every settled conclusion it touches is preserved verbatim. It is disclosed
here as well so a Reviewer cannot miss it.
