# Worker Result

STATUS: COMPLETE

Phase: ANALYSIS · **Iteration 5 (correction after Reviewer FAIL)** · Run `run_3233a1469e97` · risk `high`
Ticket: **OS-28 "Define Bounded Autonomy Decision Policy Contract"** (P0/High)
Branch: `feat/os-28-bounded-autonomy-policy` (base `main` @ `c264e79`)
Scope of this document: **diagnosis only. No repository source file was created or modified.**
The only file this Task wrote is this artifact.

Baseline captured before analysis, from the repository's own CI commands:

| command | result |
|---|---|
| `python3 scripts/validate_skills.py` | **PASSED (501 checks)** |
| `python3 -m unittest discover -s scripts -p 'test_*.py'` | **Ran 1269 tests in 298.5s — OK (skipped=6)** |

Every claim below marked with a file/line reference was read directly in this repository at
`c264e79` + working tree. Claims I could not verify are labelled **확인하지 않음**.

---

## Summary

OS-28 asks for a machine-readable, all-phase decision policy contract with four states
(`CLEAR` / `ASSUMPTION_ALLOWED` / `NEEDS_INPUT` / `CONFLICT`), an 11-element decision boundary,
a reason-code system, and ten validation properties — **without** implementing the gate execution
(OS-29), the clarification protocol (OS-30), or durable pause/resume (OS-31).

Four findings drive the recommendation:

1. **The repository has four distinct contract mechanisms, and only two of them are shared across
   both Skills.** The shared ` ```policy-contract ` JSON block is asserted *deep-equal* between the
   two skills (`scripts/validate_skills.py:1122-1126`), and `templates/**` + `reviews/**` are
   asserted *byte-equal* (`validate_shared_directories`, `scripts/validate_skills.py:800-822`). The
   `#### … contract` anchor-block pattern is orchestration-only **by convention**: six existing
   anchor contracts are each individually asserted absent from the loop skill by their own
   name-specific pattern, and that file has zero `####` headings — but **no validator forbids adding
   a new shared anchor block**, and iteration 1's "by construction / impossible" claim is withdrawn
   (A1-2a). The anchor host is a live option; the JSON block is recommended on **cost and grammar
   fit** — the anchor grammar is flat `KEY = token, token`, and OS-28's contract is nested (A1-6a).
2. **`docs/ROADMAP.md` and the OS-28 requirements do not contradict each other anywhere I could
   find.** The ROADMAP defines the four states, their Continue/Pause action, the boundary element
   list, confidence-is-not-authority, and axis independence. It does **not** define entry
   conditions, transitions, reason codes, evidence fields, or Reviewer misclassification judgment —
   those are gaps OS-28 fills, not conflicts it must resolve.
3. **Neither SKILL.md contains any bounded-autonomy decision vocabulary today.** `grep` for
   `NEEDS_INPUT`, `ASSUMPTION_ALLOWED`, and a decision-state `CONFLICT` returns nothing in either
   Skill, in `templates/**`, or in `reviews/**`. OS-28 is a greenfield addition, not a refactor.
   But two *existing* tokens are near-collisions and must be disambiguated in the contract:
   `PHASE_CONFLICT` (an invocation-validation error code) and `ESCALATED` (a terminal run status
   meaning "budget exhausted", **not** "waiting for a user").
4. **The repository's existing schema-version handling is split.** The YAML loaders fail closed on
   an unknown version (`scripts/quality_profile.py:521-528`, `scripts/agent_profile.py:462-467`),
   but `evaluate_invocation()` in `scripts/skill_policy.py` **never reads `schema_version`** — that
   key is asserted only by the repository validator. And `load_risk_contract()` returns `None` for a
   *malformed* block, which the runtime reads as "this skill has no risk axis" (fail-open at
   runtime, caught only in CI). OS-28's requirement 9 ("malformed·unknown schema version fail
   closed") means OS-28 must follow the YAML-loader precedent, **not** the risk-contract precedent.

Recommended shape: a **three-surface split** — machine vocabulary in the shared
` ```policy-contract ` JSON (parity for free), Worker-facing classification prose in shared
`templates/**`, Reviewer-facing misclassification judgment in shared `reviews/common.md` (byte
parity for free) — plus a new `scripts/decision_policy.py` loader with an explicit
`SUPPORTED_SCHEMA_VERSIONS` gate, imported by `validate_skills.py` rather than re-implemented.

**Eight open questions were raised (OQ-1…OQ-8); three are now decided and five remain open.** The
Coordinator put OQ-2, OQ-6, and OQ-7 to the repository owner as structured questions, and the
owner's answers are recorded as **UD-1, UD-2, and UD-3** in
`artifacts/runs/run_3233a1469e97/USER_DECISIONS.md`: an **optional** decision record section in the
Result Contract templates; validation requirement 5 proven to the **permission** level only, with
that limit stated and never reported as solved; and the existing `evaluate_invocation()`
`schema_version` gap left **out of scope** as a pre-existing defect. Those three are the user's
decisions, recorded here, not made here. **OQ-1, OQ-3, OQ-4, OQ-5, OQ-8 and the newly registered
OQ-9 remain open** and are presented with options, impact, and a recommendation rather than silently
chosen. OQ-9 exists because iteration 4 drew a conclusion wider than its citation supported; rather
than keep the conclusion, the choice it rested on was returned to the open set (A3-1a, RA-6).

---

## Analysis

### A1. Existing contract patterns

Four mechanisms exist. All four were read in full.

#### A1-1. Shared ` ```policy-contract ` JSON block

| aspect | detail |
|---|---|
| grammar | ` ```policy-contract ` fence containing one JSON object |
| parser | `scripts/skill_policy.py:load_policy_contract()`, regex `CONTRACT_BLOCK_PATTERN` (line ~40) |
| failure mode | raises `PolicyContractError` on missing block / invalid JSON / non-object |
| runtime consumer | `evaluate_invocation()` — the pre-dispatch parameter gate |
| parity | `validate_machine_readable_contracts()` ends with `contracts[0][1] == contracts[1][1]` — the **entire dict** must match between the two Skills |
| human↔machine parity | also asserts `DEFAULT_WORKER = …`, the `기본 known commands:` ` ```text ` list, `1 <= max-iterations <= 10`, and `REASON: <CODE>` presence in the prose |
| present in loop skill | **yes** — `orca-worker-reviewer-loop/SKILL.md:156` under `## Machine-Readable Policy Contract` |

Strengths for OS-28: shared by construction, already carries `schema_version`, one loader, one
parity assertion, and the prose-parity idiom is already established.

Weakness: the block today holds *invocation-time* parameter policy (things `evaluate_invocation`
parses out of a `/skill …` string). OS-28's decision policy is *phase-time* policy that no current
runtime function consumes. Putting both in one block is a cohesion cost — real, but bounded, since
`agent_profile` config already sits there as a sub-object without being parsed by the same code
path.

Verified gap: `grep -n schema_version scripts/skill_policy.py` returns **nothing**.
`evaluate_invocation()` reads `contract["help"]`, `contract["defaults"]`, etc. directly and never
checks the version. So today an unknown schema version fails in CI but **not** at runtime.

#### A1-2. Orchestration-only `#### <heading>` + ` ```text ` anchor blocks

| aspect | detail |
|---|---|
| grammar | `#### <Heading>\n … ```text\nKEY = value, value\n``` ` |
| line pattern | `LIFECYCLE_CONTRACT_LINE_PATTERN = ([A-Z][A-Z0-9_]*) = (.+)`; token pattern `[a-z][a-z0-9_]*`; comma-separated; duplicate key ⇒ `None` |
| parsers | `parse_lifecycle_contract`, `parse_final_review_contract`, `parse_anchor_contract` (shared), and `skill_policy.load_risk_contract` (imported by the validator, not duplicated) |
| drift protection | the expected dict is duplicated as a Python constant (`RISK_CONTRACT`, `AGENT_PROFILE_CONTRACT`, `QUALITY_PROFILE_CONTRACT`, …); the validator asserts key-set equality, value equality, a `*_MAX_LINES` budget, internal-consistency rules, and named prose anchors |
| count today | 9 `#### … contract` headings in `orca-worker-reviewer-orchestration/SKILL.md` (lines 552, 704, 898, 954, 981, 1408, 1448, 1550, 2034) |
| present in loop skill | **no, for the six existing blocks — each individually asserted absent.** `grep '^#### ' orca-worker-reviewer-loop/SKILL.md` returns **zero** matches; that file uses only `#`/`##`/`###`. See A1-2a for exactly what the validators do and do not forbid. |

Strengths: the strongest drift/anti-weakening machinery in the repo — value equality *plus* a line
budget *plus* internal-consistency assertions *plus* prose anchors that fail when a sentence the
block only indexes is deleted.

##### A1-2a. What the loop-skill exclusions actually enforce (RA-3 correction)

Iteration 1 claimed a `#### … contract` host is impossible in the loop skill "by construction" and
that existing validators exclude such blocks generally. **That claim was wider than the evidence and
is withdrawn.** Command run: `grep -n 'loop_skill\|loop_text\|orca-worker-reviewer-loop'
scripts/validate_skills.py`. Every loop-skill assertion it returns, read at its cited lines:

| validator | lines | what it asserts about the loop skill |
|---|---|---|
| `validate_lifecycle_accounting_contract` | 1209-1214 | `parse_lifecycle_contract(loop_text) is None` |
| `validate_final_review_contract` | 1445-1455 | `parse_final_review_contract(loop_text) is None`; `"Final Adversarial Review" not in loop_text` |
| `validate_reuse_contract` | 1523-1527 | `parse_anchor_contract(loop_text, REUSE_CONTRACT_BLOCK_PATTERN) is None` |
| `validate_task_boundary_contract` | 1593-1597 | `parse_anchor_contract(loop_text, TASK_BOUNDARY_CONTRACT_BLOCK_PATTERN) is None` |
| `validate_reviewer_context_contract` | 1657-1662 | `parse_anchor_contract(loop_text, REVIEWER_CONTEXT_CONTRACT_BLOCK_PATTERN) is None` |
| `validate_quality_profile_contract` | 1745-1750 | `parse_anchor_contract(loop_text, QUALITY_PROFILE_CONTRACT_BLOCK_PATTERN) is None` |
| `validate_risk_profile_contract` | 1829-1838 | `load_risk_contract(loop_skill) is None`; `"INVALID_RISK" not in loop_text` |
| `validate_agent_profile_contract` | 1932-1939 | the **opposite** direction — two named prose anchors must be **present** in the loop skill |

So the accurate statement is narrower than iteration 1's, and narrower in one direction than the
Reviewer's citation in another: **six existing anchor contracts are each individually asserted absent
from the loop skill, by their own name-specific block pattern** (the Reviewer cited only the risk
one at 1831-1838; five more exist). But every one of those checks is **pattern-specific**. Verified
by reading them: there is no generic "the loop skill must contain no `####` heading" check, no
"no anchor contract" check, and nothing that matches a *new* heading. `validate_frontmatter`
(name/description) and `validate_routes_and_files` (`extract_phase_routes`, which matches
`^(PHASE)(?::|\s+→)` lines) are the only other structural checks over `SKILL.md`, and neither
constrains heading levels.

**Therefore: a new shared decision anchor block in both Skills is NOT forbidden by any validator.**
The closest thing to a prohibition is a *code comment*, not a check — `scripts/validate_skills.py:485-488`:

> `# ... Prose rather than an anchor block: orca-worker-reviewer-loop has no`
> `# anchor contracts at all, and adding its first one for two facts would be a heavier`
> `# structure than the facts need.`

That records the convention and its cost reasoning. It does not enforce it. The anchor host is
therefore evaluated in A1-6 as a **possible alternative** on cost, not excluded on impossibility.

Fail-open found: `load_risk_contract()`'s own docstring states it returns `None` for a malformed
block "following `parse_lifecycle_contract`'s one-condition-one-diagnostic convention rather than
raising", and `evaluate_invocation()` treats `None` as *"this skill has no risk axis"*. A malformed
risk block therefore silently removes the risk axis at runtime. CI catches it; a runtime caller does
not. **OS-28 must not copy this convention** (requirement 9).

#### A1-3. `scripts/workflow_contract.py` — regex over the prose vocabulary itself

| aspect | detail |
|---|---|
| grammar | `CHOICE_LINE = ^FIELD: LEFT \| RIGHT$`; `REVIEW_VERDICT_LINE` for the exactly-four-value `REVIEW_VERDICT` line whose values may contain spaces |
| sources read | `SKILL.md`, `templates/implementation.md` (finding-resolution vocabulary), `reviews/common.md` (`REVIEW_VERDICT`) |
| parity | `validate_workflow_output_contracts()` asserts `contracts[0] == contracts[1]` over the `WorkflowOutputContract` dataclass |

Strength: the vocabulary is validated **where a human actually reads it** — inside the Result
Contract block — so there is no second copy to drift.

Weakness: the parser *discovers* field names rather than asserting them, and matches on an exact
value set. It can prove `STATUS: COMPLETE | BLOCKED` exists; it cannot detect that a *new* state was
added elsewhere in the document. For a four-state vocabulary that must reject a fifth value, this
mechanism alone is insufficient.

#### A1-4. Shared-directory byte equality

`validate_shared_directories()` (`scripts/validate_skills.py:800-822`) asserts that
`templates/` and `reviews/` have identical file-name sets **and identical bytes** in both Skills.
Verified: both skills carry the same 7 templates and 8 review files.

This is the cheapest and strongest cross-skill drift protection in the repository: it needs no
parser, no duplicated Python constant, and no new validator function.

#### A1-5. How Skill drift is prevented today — and where it is not

Prevented:

```text
templates/** and reviews/**        byte-equal
policy-contract JSON block         deep-equal (whole dict)
WorkflowOutputContract             deep-equal (dataclass)
REQUIRED_ERROR_CODES               present in both SKILL.md
named agent-profile prose anchors  present in both SKILL.md
the six existing anchor contracts  each individually asserted ABSENT from the loop skill,
                                   by its own name-specific block pattern (A1-2a)
```

Not prevented:

- any prose in either `SKILL.md` that no anchor constant names;
- runtime schema-version drift (`evaluate_invocation` never checks it);
- runtime malformed-anchor-block drift (`load_risk_contract` returns `None`, read as "no axis").

#### A1-6. Judgment: which pattern fits OS-28

| candidate | fits? | reason |
|---|---|---|
| shared ` ```policy-contract ` JSON | **yes, for the machine vocabulary** | already deep-equality-asserted across both Skills; already versioned; one loader; extending it is exactly the pattern `docs/deterministic_flow_idea.review_by_opus.md:106-108` names as this repository's existing "code enforces / prompt explains" instance |
| shared `templates/**` + `reviews/**` | **yes, for the prose** | byte-equality is free; and `reviews/common.md` is what the phase Reviewer actually reads, which is where the misclassification rules must live to have any effect |
| a **new shared** `#### … contract` anchor block in both Skills | **possible — not excluded** | no validator forbids it (A1-2a). It is judged on cost below, not on impossibility |
| standalone `.orca/decision-policy.yaml` | **no** | `.orca/**` is *project* configuration (per-project, absent by default — only `*.example.yaml` files exist here). OS-28 is *lifecycle* policy, which ROADMAP Architecture Principle 5 keeps separate from project configuration |
| new `scripts/decision_policy.py` loader | **yes, required regardless** | requirement 9 needs a fail-closed `SUPPORTED_SCHEMA_VERSIONS` gate; follow `quality_profile.py:521-528` / `agent_profile.py:462-467`, and have `validate_skills.py` **import** it rather than re-implement the parse (the precedent `load_risk_contract` set) |

##### A1-6a. JSON block vs a new shared anchor block — cost comparison

Both are available (A1-2a). The comparison is therefore on parity cost, document-change cost, and
grammar fit — no impossibility claim is made for either.

| axis | shared ` ```policy-contract ` JSON | new shared `#### Decision policy contract` anchor block in both Skills |
|---|---|---|
| **cross-skill parity cost** | **zero.** `contracts[0][1] == contracts[1][1]` already compares the whole dict (`validate_skills.py:1122-1126`) | **one new validator function.** Every existing anchor validator reads `LIFECYCLE_SKILL_DIR` **only** and compares to a Python constant; none parses both Skills. A shared block needs a new both-Skills parse-and-compare. Note this yields *stronger* protection than deep-equality — each Skill is compared to the same constant, so drift in either fails — but it is new code, not reuse |
| **document-change cost** | zero — both Skills already carry the block | the loop skill gains its **first** `####` heading and its first anchor contract. No validator objects (A1-2a); the cost is convention, recorded at `validate_skills.py:485-488` |
| **grammar fit** | **native.** JSON nests | **poor, and this is the decisive axis.** `LIFECYCLE_CONTRACT_LINE_PATTERN` is `([A-Z][A-Z0-9_]*) = (.+)` with values split on `,` and each value matched against `LIFECYCLE_CONTRACT_TOKEN_PATTERN = [a-z][a-z0-9_]*` — a **flat** `KEY = token, token` grammar with no nesting. OS-28 needs a transition matrix keyed by `(from, to)`, per-state required-field sets, and the A4-0 per-element truth table. Flattening those produces one key per cell (a 4×4 matrix alone is up to 16 keys) against existing line budgets of 4/14/17/18/20 (R-4) |
| **versioning** | `schema_version` already present in the block | none — no anchor block carries a version today; one would have to be added |
| **line-budget idiom** | none today (JSON is not line-budgeted) | `*_MAX_LINES` machinery already exists and is the direct answer to R-4 |

Recommendation, on cost rather than exclusion: **three-surface split** — JSON block (vocabulary,
transitions, reason codes, required evidence fields) + `templates/**` (Worker: how to classify, why)
+ `reviews/common.md` (Reviewer: how to judge misclassification) + `scripts/decision_policy.py`
(fail-closed loader). The deciding factor is **grammar fit**, not availability: the anchor grammar is
flat and OS-28's contract is nested. Parity cost reinforces it; document-change cost is minor either
way. If a future decision prefers the anchor host, the trade is accepted deliberately — one new
parity validator and a flattened key set in exchange for the `*_MAX_LINES` budget idiom.

Final choice on the machine-block host remains **OQ-4** and is not settled here.

---

### A2. `docs/ROADMAP.md` Bounded Autonomy Model — alignment table

Source read: `docs/ROADMAP.md` "Bounded Autonomy Model" section, plus Vision, Architecture
Principles 2/3/4/8, Non-Goals, and the Milestone 1 list.

What the ROADMAP already fixes:

- decision checks apply continuously across ANALYSIS, PLAN, DESIGN, IMPLEMENTATION, TEST, and Final
  Review; **"A check is not itself a user pause."**
- the four states with a one-line meaning and a workflow action each;
- the boundary factor list;
- "Model confidence alone is not authority.";
- "Risk, Quality Profile, and Agent Profile remain separate axes and cannot silently expand what the
  workflow is allowed to decide.";
- Principle 3: `NEEDS_INPUT` and `CONFLICT` are **durable states, not failures to disguise or
  invitations to guess**; a response resumes the responsible phase without bypassing review;
- Non-Goals: Worker/Reviewer agreement, model confidence, and a recommended default are **not**
  evidence of user authorization;
- Milestone 1 dependency flow OS-28 → OS-29 → OS-30 → OS-31 → OS-32.

| OS-28 requirement | ROADMAP position | verdict |
|---|---|---|
| four-state vocabulary | defined verbatim in the state table | **일치** |
| exact meaning of each state | one-line meaning only | **공백** — usable as a definition, insufficient as a contract |
| entry conditions | not defined | **공백** |
| allowed next transitions | not defined | **공백** |
| workflow continue/pause per state | defined: Continue / Continue and review / Pause and ask / Pause and request resolution | **일치** |
| user decision required per state | implied by "Pause and ask" / "Pause and request resolution" | **일치** |
| required evidence + reason code | not defined; "decision-provenance completeness" appears only as a success **metric** | **공백** |
| Reviewer misclassification judgment | not defined anywhere | **공백** |
| 11 boundary elements | all 11 present, 3 worded differently (see below) | **일치 (용어 정렬 필요)** |
| confidence ≠ authority | stated | **일치** |
| record policy/reversibility/impact/retraction for auto decisions | not stated in ROADMAP | **공백** (ticket-only; no contradiction) |
| requirement contradiction / irreversible high-impact never auto-approved | Principle 2 states it | **일치** |
| "everything = NEEDS_INPUT" is also wrong | Vision "eliminate unnecessary intervention"; "unnecessary-question rate" metric; "Success is not measured by maximizing question-free runs" cuts both ways | **일치** |
| Worker+Reviewer agreement ≠ user approval | Non-Goals, verbatim | **일치** |
| recommended default ≠ user approval | Non-Goals, verbatim | **일치** |
| timeout / no response ≠ approval | not stated in ROADMAP | **공백** (ticket-only; no contradiction) |
| Risk/QP/AP independent of decision authority | stated | **일치** |
| LOW/MEDIUM/HIGH must not expand decision scope | stated ("cannot silently expand what the workflow is allowed to decide") | **일치** |

**불일치: none found.** I looked specifically for a ROADMAP sentence that OS-28 would have to
contradict and did not find one.

Terminology deltas to reconcile (cosmetic, but the contract must pick one and the ROADMAP is the
published document):

| ticket wording | ROADMAP wording |
|---|---|
| explicit requirement conflict | contradiction |
| monetary cost | cost |
| repository/project policy | existing project policy |

One substantive ROADMAP detail that is easy to lose: `ASSUMPTION_ALLOWED`'s workflow action is
**"Continue and review"**, not plain "Continue". So `ASSUMPTION_ALLOWED` must **not** be defined as
behaviourally identical to `CLEAR` — the recorded assumption is an input to review.

Derived consequence, and a place OS-28 must be explicit: at `risk=low` there **is no phase
Reviewer** (`RISK_LOW_PHASE_GATE = worker_only`, verified in the Risk profile contract block,
`orca-worker-reviewer-orchestration/SKILL.md:898`). But the Final Adversarial Review is mandatory at
every level (`RISK_FINAL_REVIEW = mandatory_at_every_level`,
`FINAL_REVIEW_RISK_INDEPENDENCE = mandatory_and_identical_at_every_risk_level`). So "Continue and
review" resolves as: *a recorded assumption is reviewed by the phase Reviewer where one exists, and
by the Final Adversarial Reviewer always.* That is what keeps "risk does not expand decision
authority" true at LOW — the review is later, not absent.

#### A2-1. Namespace collisions with existing repository vocabulary (verified)

| existing token | where | today's meaning | collision risk |
|---|---|---|---|
| `PHASE_CONFLICT` | `errors["phase_conflict"]` in both skills' policy contract; `skill_policy.evaluate_invocation` | explicit `phases=` disagrees with natural-language phase terms — a **pre-dispatch invocation validation** failure | same word, different layer. The contract must state that decision-state `CONFLICT` is not `PHASE_CONFLICT` |
| `ESCALATED` | `run_logging.py:105` `RUN_STATUS_VALUES = ("COMPLETED","BLOCKED","ERROR","ESCALATED")`; `orca-worker-reviewer-orchestration/SKILL.md:1662-1668` | **terminal** run status meaning "iteration budget exhausted" or "out-of-scope final review finding" — an unresolved-failure end state | OS-28's `NEEDS_INPUT`/`CONFLICT` are **mid-run pauses that resume**. Mapping them onto `ESCALATED` would collapse "gave up" and "waiting for a decision" into one status |
| `BLOCKED` | Worker result `STATUS: COMPLETE \| BLOCKED`; also a `REVIEW_VERDICT` value; also a `RUN_STATUS` value | Worker cannot proceed / Reviewer lacks evidence | `NEEDS_INPUT` ≠ `BLOCKED`. Must be stated, or a Worker will report `BLOCKED` where the contract wants `NEEDS_INPUT` |

Minimum obligation on OS-28: state that the four decision states are a **separate axis** from
`RUN_STATUS_VALUES`, from Worker `STATUS`, and from `REVIEW_VERDICT`, and that OS-28 changes none of
them. See **OQ-3** for the forward-looking half.

---

### A3. State semantics, entry conditions, transitions (draft)

#### A3-1. Draft definitions

| state | draft meaning | draft entry condition | continue? | user decision? |
|---|---|---|---|---|
| `CLEAR` | no open decision on this phase's work crosses the decision boundary | **any** of: no decision item is open; a locatable policy source **determines** the choice; an explicit user authorization (prior or in-run) **decides** it | yes | no |
| `ASSUMPTION_ALLOWED` | a safe, reversible, policy-supported assumption was made and recorded | **all** of: reversible within this run's change scope; blast radius confined to the requested scope; **none** of {monetary cost, security, privacy, compliance, long-term lock-in} is true — **no authorization exception exists** (A4-0); a locatable policy source **supports but does not determine** the choice; no explicit user authority is reserved over it | yes, **and the record is a review input** | no |
| `NEEDS_INPUT` | correctness depends on user intent or authority that cannot be derived | **any** boundary element is true and is neither determined by policy nor decided by an explicit authorization, or required intent is simply **absent** | no — pause and ask | yes |
| `CONFLICT` | requirements or accepted decisions cannot all be satisfied | **any** of the three clauses C-1/C-2/C-3 below is **contradictory** | no — pause and request resolution | yes |

The `NEEDS_INPUT` / `CONFLICT` line, stated once so it is testable: **`NEEDS_INPUT` is *missing*
information; `CONFLICT` is *contradictory* information.** The distinction matters for two concrete
reasons: (a) the structured question shape differs (OS-30 — "which do you want?" vs "these two
cannot both hold, which wins?"), and (b) `CONFLICT` can be **detected from two artifacts without
asking anything**, so it is machine-observable in a way `NEEDS_INPUT` is not.

##### A3-1a. The three `CONFLICT` clauses (RA-5 resolution)

Iteration 3's entry condition named only two kinds of contradiction, but A5-4 offered fixtures
citing four. Two of those fixtures cited things that were neither an explicit requirement nor an
already-accepted decision. The entry condition is therefore stated in full, as three clauses, and
one reason code is removed rather than stretched to fit:

```text
C-1  >=2 explicit requirements are contradictory.
C-2  An explicit requirement contradicts an ALREADY-ACCEPTED DECISION.
C-3  An explicit requirement contradicts a NON-OVERRIDABLE PROJECT INVARIANT.
```

**`already-accepted decision` (C-2) — normative definition.** Exactly two things qualify, and both
are locatable artifacts of *this* run, not repository background:

```text
- a user_decision record produced earlier in this run (A5-3), including a
  prior_explicit_user_authorization carried from the original request; or
- an approved output of an earlier phase in this run -- one that passed its phase gate.
```

Repository conventions, project configuration, and code structure are **not** accepted decisions.
They are `policy_source` — a boundary **input** — which is exactly how A4-0 already classifies them.

**`non-overridable project invariant` (C-3) — normative definition.** A rule the workflow has **no
authority to lower at any risk level**. In this repository that is the safety floor:
`RISK_SAFETY_FLOOR = mandatory_test_gates_apply_at_every_level`
(`orca-worker-reviewer-orchestration/SKILL.md:923`), backed by ROADMAP Architecture Principle 8
(`docs/ROADMAP.md:80-82`): *"Risk settings may change review strength, but never remove mandatory
test gates or authorize unsafe lifecycle cleanup."*

Why C-3 needs **user resolution** rather than silent refusal or silent compliance. Both horns are
closed to the workflow: it cannot lower the floor (no authority), and it cannot satisfy the
requirement as written. Proceeding either way would decide, on the user's behalf, which of the two
they meant — the exact thing the decision boundary exists to prevent. The only actor who can
withdraw or restate the requirement is the user, so the item pauses. Note the asymmetry from C-1:
in C-1 either requirement could win and the user picks; in C-3 the invariant always wins, and what
the user is being asked is *how to restate the requirement*, not *which side to take*.

**Requirement vs. repository policy — what the evidence supports, and what it does not (RA-6).**

Iteration 4 cited a real precedence and then drew a conclusion wider than that citation carries.
The citation is kept; the conclusion is withdrawn.

*What the citation establishes, stated at its actual width.* `reviews/common.md:28-35` and
`QUALITY_GATE_DECISION_PRIORITY` (`orca-worker-reviewer-orchestration/SKILL.md:1540`) both order
four tiers:

```text
1 explicit user/project requirements
2 applicable project quality profile attributes
3 current phase contract
4 minimal general gate
```

> **Narrow claim, and the only one made here:** *when judging a quality finding*, an explicit
> requirement outranks an applicable project quality-profile attribute, which outranks the current
> phase contract, which outranks the minimal general gate.

*What it does not establish.* Nothing about repository conventions in general, project
configuration, code structure, or security / privacy / compliance / tooling policy; and nothing
about **decision-boundary classification**, which is a different axis from quality-finding
adjudication. Iteration 4 generalized the four-tier ordering to "this repository already resolves
that class by precedence", which the citation does not support. That sentence is **withdrawn**.

*Consequence for the reason code.* `requirement_vs_repository_policy` was removed in iteration 4 on
the strength of the withdrawn generalization. With that basis gone, the removal is **not confirmed**
— treating it as settled would leave a decision standing on a reason that no longer holds. Its
disposition is registered as **OQ-9** (A8) with options, impacts, and a recommendation, and is
**not decided here**. This is the behaviour the ticket exists to define: when the basis for a
choice does not survive scrutiny, the choice returns to the open set rather than being kept for
tidiness.

*Unaffected.* C-1, C-2, C-3 and the `requirement_vs_safety_floor` handling above stand as the
Reviewer confirmed them. RA-6 concerns only the repository-policy axis. A repository policy that
records an earlier *user* decision is still reached by C-2 through `requirement_vs_accepted_decision`
regardless of how OQ-9 resolves.

The `CLEAR` / `ASSUMPTION_ALLOWED` line, which the same table now fixes and which A4-0 and A5-2
depend on: **a policy source that *determines* the choice yields `CLEAR`; a policy source that
*supports but does not determine* it yields `ASSUMPTION_ALLOWED`.** This follows from the ROADMAP's
own wording — `ASSUMPTION_ALLOWED` is "a safe, reversible, policy-supported **assumption**", and
there is no assumption left once a policy or a user has settled the choice.

#### A3-2. Draft transition matrix

A decision state is the *result* of a check, not a mutable variable. "Transition" therefore means:
the state of the **same decision item** at a later check (same phase re-evaluated after new
evidence, or a later phase that touches the same item).

| from ↓ / to → | `CLEAR` | `ASSUMPTION_ALLOWED` | `NEEDS_INPUT` | `CONFLICT` |
|---|---|---|---|---|
| `CLEAR` | ✓ | ✓ (a new decision appeared) | ✓ | ✓ |
| `ASSUMPTION_ALLOWED` | ✓ **only with a recorded retraction + the evidence that superseded it** | ✓ | ✓ (assumption invalidated / boundary re-crossed) | ✓ |
| `NEEDS_INPUT` | ✓ **only with a valid `user_decision` record** | ✗ **forbidden unconditionally** — a `user_decision` does **not** enable it | ✓ | ✓ |
| `CONFLICT` | ✓ **only with a valid `user_decision` record** | ✗ **forbidden unconditionally** — a `user_decision` does **not** enable it | ✓ | ✓ |

**The rule the matrix encodes, stated once (RA-1 resolution).** An answered question produces a
**decision**, not an assumption. So a user response never routes an item to `ASSUMPTION_ALLOWED`; it
routes the item to `CLEAR`, and `NEEDS_INPUT|CONFLICT → ASSUMPTION_ALLOWED` is forbidden **with or
without** a `user_decision`.

Why `CLEAR` and not `ASSUMPTION_ALLOWED` — three reasons, in decreasing order of weight:

1. **The ROADMAP's own definition.** `ASSUMPTION_ALLOWED` is "a safe, reversible, policy-supported
   **assumption** is recorded". Once the user has decided, nothing is being assumed. Filing a user
   decision as an assumption would misreport authority as inference, and would make OS-32's
   `silent-assumption defects` metric count answered questions as assumptions.
2. **It removes a contradiction rather than creating one.** INV-4 forbids `ASSUMPTION_ALLOWED`
   whenever the item is irreversible or touches monetary/security/privacy/compliance/lock-in — which
   is the typical reason an item was `NEEDS_INPUT` in the first place. A permitted
   `NEEDS_INPUT → ASSUMPTION_ALLOWED` edge would immediately have to re-litigate INV-4 for the same
   item. Routing to `CLEAR` never does.
3. **One check point.** The forbidden-authority reject list then has to be enforced on exactly one
   edge (`→ CLEAR`), not two.

The ticket's own position is satisfied: "요구사항 모순이나 비가역적 고영향 결정은 명시적 권한
없이 자동 승인하지 않는다" — with explicit authority the work **does** proceed. The rule above says
it proceeds as `CLEAR`, carrying the `user_decision` record. It does not block authorized work.

**Reclassification after a user response — fixed, not left open:**

```text
answer fully resolves the item        -> item becomes CLEAR and carries user_decision permanently
answer resolves it only in part       -> item STAYS NEEDS_INPUT / CONFLICT
                                         (a partial answer is not a decision)
answer raises a further decision      -> that is a NEW decision item at its own state;
                                         it is not a transition of the old item
answer's source is in the reject list -> not a user_decision at all; item stays NEEDS_INPUT /
                                         CONFLICT and the record is rejected by INV-5
```

Forbidden transitions, each with its reason:

| # | forbidden | reason |
|---|---|---|
| T-F1 | `NEEDS_INPUT → CLEAR` without a `user_decision` record | this is "guess and continue" — ROADMAP Principle 3 ("not … invitations to guess") |
| T-F2 | `NEEDS_INPUT → ASSUMPTION_ALLOWED`, **unconditionally** — a recorded `user_decision` does not enable it | **the highest-value rule in the contract.** This is the escape hatch a fluent model finds first: downgrade the question into a "safe assumption" and avoid pausing. Making it conditional on a `user_decision` would reopen exactly that hatch, since the record is self-produced (R-2). An answered question routes to `CLEAR` instead |
| T-F3 | `CONFLICT → ASSUMPTION_ALLOWED`, **unconditionally** | an assumption cannot arbitrate between two contradictory *explicit* requirements — that is precisely the authority the user reserved. A user resolution routes the item to `CLEAR`, naming which requirement wins |
| T-F4 | `CONFLICT → CLEAR` without a recorded resolution | same as T-F1 |
| T-F5 | **any** transition whose sole justification is model confidence, timeout, absence of a response, Worker/Reviewer agreement, or a recommended default | **three of these five** are ROADMAP Non-Goals verbatim; the other two come from the ticket. Per-item sourcing in the table below |
| T-F6 | a later phase reporting `CLEAR` **on a decision item** that an earlier phase left `NEEDS_INPUT`/`CONFLICT` unresolved | otherwise an unresolved escalation evaporates by moving forward one phase. Must be scoped **per decision item**, not per phase, or it becomes unusable (see OQ-1) |

Allowed-but-must-be-recorded: `ASSUMPTION_ALLOWED → CLEAR` requires a retraction record. Without
that rule an assumption can be laundered into a fact by re-running the check.

**T-F5 per-item sourcing.** `docs/ROADMAP.md:298-299` reads, verbatim and in full: *"Treat
Worker/Reviewer agreement, model confidence, or a recommended default as evidence of user
authorization."* That is **three** items. Commands run: `grep -n 'Non-Goals' -A 16 docs/ROADMAP.md`
and `grep -n -i 'timeout\|elapsed\|no response\|absence' docs/ROADMAP.md` — the second returns **no
matches**, so the ROADMAP does not mention timeout, elapsed time, or absence of a response anywhere.

| reject-list entry | source | exact basis |
|---|---|---|
| `worker_reviewer_agreement` | **ROADMAP Non-Goals, verbatim** | `docs/ROADMAP.md:298-299` |
| `model_confidence` | **ROADMAP Non-Goals, verbatim** | `docs/ROADMAP.md:298-299`; also stated in the Bounded Autonomy Model section ("Model confidence alone is not authority") |
| `recommended_default` | **ROADMAP Non-Goals, verbatim** | `docs/ROADMAP.md:298-299` |
| `timeout` | **ticket requirement, not ROADMAP** | OS-28 정책 원칙: "timeout이나 응답 부재를 암묵적 승인으로 취급하지 않는다" |
| `no_response` | **ticket requirement, not ROADMAP** | same sentence ("응답 부재") |

*elapsed time* appeared in iteration 1's T-F5 list and has been **removed**. It has no independent
source in either document, and it is a generalization of `timeout` rather than a separate rule.
Removing it also repairs an internal mismatch iteration 1 carried: T-F5 listed six entries while the
A5-2 reject list defined five. Both are now the same five.

#### A3-3. The modelling choice this rests on

T-F6 and the evidence fields only make sense **per decision item**, but the ROADMAP says
"A check … produces one of four states" (singular). Both can be true at once:

> a decision state is carried **per decision item**, and a check's reported state is the **derived
> aggregate** = the most restrictive open item, ordered
> `CONFLICT > NEEDS_INPUT > ASSUMPTION_ALLOWED > CLEAR`.

This keeps the ROADMAP sentence literally true while making transitions and evidence well-defined.
It is a real design decision with a cost (a per-item list is a bigger contract than a single enum),
so it is recorded as **OQ-1**, not silently adopted.

---

### A4. The 11 boundary elements — machine-checkable vs judgment

#### A4-0. Authorization truth table (RA-2 resolution)

Iteration 1 left two incompatible readings of what a cited authorization does. It is settled here as
**one** rule, and it is the same rule A3-2 settled for transitions:

> **Nothing lifts INV-4.** Neither a determining policy source nor an explicit user authorization
> unlocks `ASSUMPTION_ALLOWED` for a high-impact element. They **relocate** the item to `CLEAR`.

The reason is the ROADMAP's own definition of the state, and it is the same reason `NEEDS_INPUT →
ASSUMPTION_ALLOWED` is forbidden: `ASSUMPTION_ALLOWED` records an **assumption**, and a settled
choice is not an assumption. So an authorized monetary or security choice is not a "permitted
assumption" — it is a decided item, i.e. `CLEAR`. RA-1 and RA-2 therefore close with a single cut
rather than two independent exceptions, which is the strongest evidence available that the cut is in
the right place.

**Two things named separately, because iteration 1 conflated them:**

| | `policy_source` — a **boundary input** | `user_decision` — **transition evidence** |
|---|---|---|
| what it is | an existing repository/project artifact: a file path, requirement id, quality-attribute id, or phase-contract section | a user-supplied authorization recorded in this run, or supplied in the original request before the item arose |
| when it is read | during boundary evaluation, to decide which state the item enters | only on the `NEEDS_INPUT\|CONFLICT → CLEAR` edge, to decide whether that edge is legal |
| effect if it **determines** the choice | item is `CLEAR` | item is `CLEAR`, carrying the record |
| effect if it only **supports** the choice | item may be `ASSUMPTION_ALLOWED`, **if and only if** no high-impact element is true | n/a — a user decision is never partial support (a partial answer leaves the item `NEEDS_INPUT`) |
| can it satisfy INV-5? | **no.** A repository file cannot grant authority the user reserved | **yes** — this is the only thing that can |
| can it lift INV-4? | **no** | **no** |

**When a policy source and an explicit requirement disagree (added for RA-5, narrowed for RA-6).**
Iteration 4 stated flatly that "the requirement wins" and the item is `CLEAR`. That was wider than
its citation and is **withdrawn**. What A3-1a's narrow claim supports is only this:

```text
in scope   an explicit requirement outranks an applicable project QUALITY-PROFILE ATTRIBUTE
           when a quality finding is being judged.
NOT settled whether an explicit requirement overrides any OTHER kind of policy_source --
           a repository convention, project configuration, code structure, or a security /
           privacy / compliance / tooling policy -- and therefore whether such an item is
           CLEAR by the requirement.  Registered as OQ-9; A4-0 does not classify it.
```

So this table classifies a `policy_source`/requirement disagreement **only** inside the quality-gate
tiers. Outside them, the disposition is open. What is *not* affected either way: C-2 (the other side
is an accepted decision of this run) and C-3 (the other side is a non-overridable invariant) remain
`CONFLICT`, and neither of those is "repository policy". Nor does OQ-9 touch INV-4 — no resolution
of it can unlock `ASSUMPTION_ALLOWED` for a high-impact element, because that prohibition has no
exception (this section's opening rule).

A **prior standing authorization** (e.g. the original request says "you may modify the production
config" or "you may spend up to $X") is a `user_decision` with
`source: prior_explicit_user_authorization` and `where_recorded` pointing at the request text. Its
effect is that the item never enters `NEEDS_INPUT` at all — it enters `CLEAR` directly, because the
authority question is already answered. It never produces `ASSUMPTION_ALLOWED`.

Per-element truth table. Column 2 is INV-4; columns 3-4 show where the item goes instead:

| element (true / triggering value) | forbids `ASSUMPTION_ALLOWED`? | with a **determining** policy source | with an **explicit user authorization** |
|---|---|---|---|
| reversibility == `irreversible` | **yes** | still forbidden → `CLEAR` if the policy decides it, else `NEEDS_INPUT` | still forbidden → `CLEAR` |
| blast radius ∈ {`repository`, `external_system`} **and** irreversible | **yes** | same | same |
| monetary cost | **yes — no exception** | → `CLEAR` | → `CLEAR` |
| security | **yes — no exception** | → `CLEAR` | → `CLEAR` |
| privacy | **yes — no exception** | → `CLEAR` | → `CLEAR` |
| compliance | **yes — no exception** | → `CLEAR` | → `CLEAR` |
| long-term lock-in | **yes — no exception** | → `CLEAR` | → `CLEAR` |
| explicit user authority reserved | **yes** | a policy source cannot un-reserve it → `NEEDS_INPUT` | → `CLEAR` |
| ambiguity (unresolved) | **yes** | → `CLEAR` (the policy disambiguates) | → `CLEAR` |
| explicit requirement conflict | **yes** | a policy source cannot arbitrate two explicit requirements → `CONFLICT` | → `CLEAR` |
| none of the above true | no | → `CLEAR` | → `CLEAR` |
| none of the above true, policy only **supports** | no | → **`ASSUMPTION_ALLOWED`** (the only route into that state) | n/a |

The last two rows are the load-bearing ones for R-1: `ASSUMPTION_ALLOWED` remains genuinely
reachable, so the contract does not collapse into "everything is `NEEDS_INPUT`".

#### A4-1. Element-by-element split

The honest framing first: **none of the 11 is fully machine-decidable from a request.** What *is*
machine-checkable is (i) whether the element was **declared**, (ii) whether the declared combination
is **permitted** to yield the claimed state, (iii) whether the reason code is in the closed set, and
(iv) whether the required evidence fields are **present and locatable**. The judgment half is which
value applies.

| # | element | machine-checkable part | judgment part |
|---|---|---|---|
| 1 | ambiguity | item declared with a reason code + a citation into the requirement text | whether the text is genuinely ambiguous |
| 2 | explicit requirement conflict | ≥2 requirement citations recorded; state **must** be `CONFLICT` when both are cited as contradictory | whether they actually contradict |
| 3 | reversibility | value drawn from a closed set `{reversible_in_run, reversible_with_effort, irreversible}`; `irreversible` ⇒ `ASSUMPTION_ALLOWED` forbidden | which value applies |
| 4 | blast radius | closed set `{current_change, module, repository, external_system}`; `{repository, external_system}` + `irreversible` ⇒ state ∈ {`NEEDS_INPUT`,`CONFLICT`} | which value applies |
| 5 | monetary cost | boolean; `true` ⇒ `ASSUMPTION_ALLOWED` forbidden, **no exception** — a cited authorization routes the item to `CLEAR` instead (A4-0) | whether the action costs money |
| 6 | security | boolean; same rule, same no-exception | whether it is security-relevant |
| 7 | privacy | boolean; same rule, same no-exception | whether personal data is involved |
| 8 | compliance | boolean; same rule, same no-exception | whether a compliance regime applies |
| 9 | long-term lock-in | boolean; same rule, same no-exception | whether the choice is durable |
| 10 | repository/project policy | **strongest machine half**: an `ASSUMPTION_ALLOWED` item must cite a **locatable** policy source (file path, requirement id, quality-attribute id, or phase-contract section) — *existence of the cited path is checkable*. The record must also declare whether the source **determines** or only **supports** the choice, since that selects `CLEAR` vs `ASSUMPTION_ALLOWED` (A4-0) | whether the cited policy actually determines — or merely supports — *this* choice |
| 11 | explicit user authority | the decision record must cite a user-supplied authorization **with a source**, and the source must not be in the forbidden-authority reject list. An authorization is `user_decision` (transition evidence), never `policy_source` (boundary input) — A4-0 keeps the two apart | whether the user really said it |

Structural invariants that fall out (this is the code half in full):

```text
INV-1  state ∈ {CLEAR, ASSUMPTION_ALLOWED, NEEDS_INPUT, CONFLICT}; anything else rejected
INV-2  ASSUMPTION_ALLOWED / NEEDS_INPUT / CONFLICT require a reason_code from the closed set
INV-3  ASSUMPTION_ALLOWED requires all four provenance fields non-empty
       (policy_source, reversibility, impact, retraction_condition), and policy_source must
       be declared as "supports" -- a policy_source declared "determines" yields CLEAR
INV-4  ASSUMPTION_ALLOWED forbidden when reversibility == irreversible, OR blast_radius in
       {repository, external_system} together with irreversible, OR any of
       {monetary, security, privacy, compliance, lock_in} is true, OR authority is reserved.
       NO EXCEPTION: neither a determining policy_source nor a user_decision lifts any of
       these; they route the item to CLEAR instead (A4-0).
INV-5  NEEDS_INPUT|CONFLICT -> ASSUMPTION_ALLOWED is forbidden UNCONDITIONALLY -- a
       user_decision does not enable it (A3-2).
       NEEDS_INPUT|CONFLICT -> CLEAR requires a user_decision record whose source is NOT in
       {model_confidence, timeout, no_response, worker_reviewer_agreement, recommended_default}.
       These are the only two edges out of NEEDS_INPUT|CONFLICT that reach a continuing
       state; every other edge stays within {NEEDS_INPUT, CONFLICT}.
INV-6  the state / reason / boundary vocabularies are identical in both Skills
INV-7  unknown or malformed schema version fails closed before any dispatch
INV-8  risk, quality profile and agent profile are not inputs to state selection
```

INV-8 is checkable two ways, and both are worth having: statically, a validator asserting no
decision-policy key names a risk level or a profile name; behaviourally, a parametrized test that
runs the same fixture at `risk ∈ {low, medium, high}` and asserts an **identical permitted-state
set**. The second is the real test — it mirrors the existing
`RISK_QUALITY_PROFILE_AXIS = independent_never_read_or_gate_on_each_other` precedent.

#### A4-2. Relation to OS-27's "코드가 강제하고, 프롬프트는 그 이유를 설명한다"

Verified quote, `docs/deterministic_flow_idea.review_by_opus.md:106-108`:

> 따라서 현실적인 형태는 **"코드가 강제하고, 프롬프트는 그 이유를 설명한다"**의 이중 구조다.
> 이 저장소는 이미 그 방향이다 — SKILL.md의 `policy-contract` JSON 블록을
> `scripts/test_policy_smoke.py`가 검증하는 구조가 정확히 그것이다.

and, immediately above it (lines 102-105), the reason the prose half cannot be deleted: a rule moved
to code alone is enforced, but at a boundary case the LLM **doesn't know why it was blocked and
routes around it**.

The A4 split *is* that dual structure, and the mapping is exact:

| half | owns | lives in |
|---|---|---|
| code | closed vocabularies, required-field presence, forbidden combinations, transition legality, parity, fail-closed versioning | `scripts/decision_policy.py`, `scripts/validate_skills.py`, `scripts/test_*.py` |
| prompt | *why* a state exists, how to tell `NEEDS_INPUT` from `CONFLICT`, why an assumption is not authority, what the Reviewer looks for | shared `templates/**` (Worker) and `reviews/common.md` (Reviewer) |

This matters for OS-28 specifically because the failure mode T-F2 describes — downgrading a question
into an assumption — is exactly a "routes around the block" failure. The code can only check the
*shape* of the record; the prose has to make the Worker not want to write it.

Note that OS-27 is Milestone 4 and exploratory. OS-28 adopting its dual-structure framing is
**consistent with** OS-27, not dependent on it, and adds no dependency on OS-27's engine/adapter
proposal.

---

### A5. Reason-code system requirements

#### A5-1. Closed or open?

**Closed.** Four evidence-backed reasons:

1. **Every existing machine vocabulary in this repository is closed.** Verified:
   `RISK_LEVELS = low, medium, high`; `LIFECYCLE_OUTCOMES`; `CLEANUP_AUTHORITY_STATES`;
   `QUALITY_GATE_VERDICTS`; `QUALITY_PROFILE_STATUS`; `REQUIRED_ERROR_CODES`;
   `RUN_STATUS_VALUES`. An open set would be the first exception and would need to justify itself.
2. **OS-32 cannot compute its metrics over an open set.** The ROADMAP names required-escalation
   recall, unnecessary-question rate, and silent-assumption defects. All three are ratios over
   categories.
3. **An open set makes requirement 3 vacuous.** "Reason present" degenerates to "any non-empty
   string", which a fluent model always satisfies. Rejecting reason-less use requires a set to
   check membership against.
4. **The Reviewer needs a per-code rule.** Misclassification judgment is "what would have to be true
   for *this code* to be correct". Open sets have no such rule.

Cost of closing: a genuinely novel reason has no code. The repository's own convention answers this
— `RISK_LEVELS` and `QUALITY_GATE_VERDICTS` are extended by a contract change with a validator and
test diff, and that friction is the point. **No `OTHER` escape hatch**: an `OTHER` code becomes the
default within one run. A safer alternative is presented as **OQ-5(c)**.

#### A5-2. Draft code set

Derived from the 11 boundary elements so that coverage is arguable rather than invented.

`ASSUMPTION_ALLOWED` — *why the assumption was permitted*:

Per A4-0, every code here describes a source that **supports** the choice without determining it. A
source that *determines* the choice yields `CLEAR`, which takes no reason code.

```text
repository_policy          an existing repository convention/config supports it
explicit_requirement       the requirement text supports it
phase_contract             the current phase template contract supports it
quality_profile_attribute  an applicable quality attribute supports it
```

**`reversible_local_default` is REMOVED (RA-4 resolution).** Iteration 2 defined it as *"no policy
source, but reversible + blast_radius=current_change + no boundary element true"*, which cannot
coexist with the rule the RA-2 correction hardened one section earlier: A3-1 requires a locatable
supporting policy source to enter `ASSUMPTION_ALLOWED`, A4-0's last row calls a supporting policy
the *only* route into that state, and INV-3 / A5-3 require a non-empty `policy_source` declared
`supports`. No valid record can be built for the code, so it was a dead entry in a closed set.

Removal rather than redefinition, and the burden is where the correction brief put it: the ROADMAP
defines the state as *"a safe, reversible, **policy-supported** assumption is recorded"*. **I cannot
show that an assumption with no policy source satisfies that definition** — the phrase names the
policy support as constitutive of the state, not as one optional route into it. Redefining the code
to require a locatable supporting policy would have made it a duplicate of `repository_policy`,
distinguished only by the absence of something the schema requires. So the code is deleted and none
of A3-1 / A4-0 / INV-3 / A5-3 changes.

**Where the removed case goes — this leaves no gap.** A choice that is reversible, scope-local, has
no boundary element true, and has *no policy bearing on it at all* is not a decision that crosses
the autonomy boundary; it is an ordinary implementation choice. It is therefore **`CLEAR`** under
A3-1's first entry condition ("no decision item is open"), and `CLEAR` takes no reason code and no
record. The contract loses no expressible case — it stops offering a second, unbuildable way to
express one it already handles.

`NEEDS_INPUT` — one per boundary element that can be missing rather than contradictory:

```text
ambiguous_requirement      irreversible_action        security_impact      long_term_lock_in
missing_user_intent        blast_radius_beyond_scope  privacy_impact       authority_reserved_to_user
                           monetary_cost              compliance_impact
```

`CONFLICT`:

One code per `CONFLICT` clause in A3-1a, so the mapping is 1:1 and checkable:

```text
requirement_contradiction          C-1  two explicit requirements
requirement_vs_accepted_decision   C-2  a user_decision or an approved earlier-phase output
                                        of THIS run
requirement_vs_safety_floor        C-3  a non-overridable project invariant -- a requirement that
                                        would remove a mandatory test gate
                                        (RISK_SAFETY_FLOOR = mandatory_test_gates_apply_at_every_level)
```

**`requirement_vs_repository_policy` is SUSPENDED, pending OQ-9 — not confirmed removed (RA-6).**

What survives from RA-5, because it is a fact about the draft rather than an inference: its fixture
cited a requirement plus a repository policy path, and repository policy is **none of** C-1, C-2, or
C-3 as those clauses are written. So the code has no entry clause to satisfy **under the entry
condition as it currently stands**, and it is therefore not part of the confirmed live set. A5-2
listing it *separately* from `requirement_vs_accepted_decision` also shows the draft never treated
the two as equivalent.

What does **not** survive is iteration 4's reason for concluding the class is settled. That reason
was "an explicit requirement outranks project policy, so the item is `CLEAR`", generalized from a
citation that only covers quality-finding adjudication (A3-1a). With the generalization withdrawn,
the class has **no** established destination, so the code's fate is genuinely open:

```text
not established  that a requirement always overrides repository policy  -> so NOT necessarily CLEAR
not established  that this class needs user resolution                  -> so NOT necessarily
                                                                            CONFLICT or NEEDS_INPUT
established      it satisfies no clause of the CONFLICT entry condition AS WRITTEN
```

Three ways to close that are set out as **OQ-9** (A8) — restore it under `CONFLICT`, restore it
under `NEEDS_INPUT`, or keep the removal and define where out-of-scope policy conflicts go — with
impacts and a recommendation. **OQ-9 is registered, not decided.** Until it is decided the closed
set stands at **17 confirmed live codes plus 1 suspended**, and A5-4 reports it that way rather than
counting the suspension as a settled removal.

One thing is unaffected by every option: a policy that encodes an earlier *user* decision is reached
by C-2 through the existing `requirement_vs_accepted_decision`.

Forbidden-authority reject list (the INV-5 input) — each entry maps 1:1 to a stated principle, so
this list is derived, not invented:

```text
model_confidence            "Model confidence alone is not authority" (ROADMAP)
timeout                     "timeout이나 응답 부재를 암묵적 승인으로 취급하지 않는다" (ticket)
no_response                 same
worker_reviewer_agreement   ROADMAP Non-Goals, verbatim
recommended_default         ROADMAP Non-Goals, verbatim
```

#### A5-3. Required evidence fields per state

```text
CLEAR                 no reason_code. One of:
                      - nothing was open: no record
                      - a policy source DETERMINED it: policy_source { locatable, "determines" }
                      - an authorization DECIDED it:   user_decision { source, where_recorded }
ASSUMPTION_ALLOWED    reason_code, policy_source { locatable, "supports" }, reversibility,
                      impact, retraction_condition
NEEDS_INPUT           reason_code, boundary_element, what_is_missing, why_policy_cannot_decide
CONFLICT              reason_code, >=2 citations, why_they_cannot_both_hold

CLEAR reached FROM    user_decision { source, where_recorded, resolves } — and `source` must NOT
NEEDS_INPUT|CONFLICT  be in the forbidden-authority reject list (INV-5)

ASSUMPTION_ALLOWED    unreachable from NEEDS_INPUT|CONFLICT (INV-5). No evidence shape exists
                      for that edge because the edge does not exist.
```

The iteration-1 phrasing "any state reached FROM `NEEDS_INPUT`/`CONFLICT`" is gone: it implied
`ASSUMPTION_ALLOWED` was reachable with the right record. Only `CLEAR` is, and the table above now
says so in the same words as the A3-2 matrix, T-F2/T-F3, and INV-5.

The four `ASSUMPTION_ALLOWED` fields are exactly the ticket's "적용 정책, 가역성, 영향, 철회 조건".

#### A5-4. Minimal valid fixture per reason code — three-check audit

RA-4 and RA-5 were the **same defect twice**: a fixture row that did not actually satisfy the state
it was filed under. RA-4 failed on *evidence* (the code forbade the field the schema required);
RA-5 failed on *entry condition* (the fixture cited something the entry clause never named). Fixing
only the two flagged rows would leave the audit method unchanged and invite a third instance, so
this iteration re-derives **every** row against three separate checks.

```text
C1  ENTRY   Does the basis the fixture cites actually fall under the WORDING of that
            state's entry condition?                          <- this is what RA-5 caught
C2  EVIDENCE Can the fixture fill EVERY required field in A5-3, not just the headline one?
                                                              <- this is what RA-4 caught
C3  INVARIANT Does the fixture violate INV-3, INV-4, or INV-5?
```

Method: for each code, take A3-1's entry condition (now including A3-1a's three `CONFLICT` clauses),
plus A5-3's required evidence fields, plus the invariants, and construct the smallest record that
satisfies all three at once. "Fixture" here means a test input, not a claim about this repository's
current state.

Two method changes this iteration, both made because the looseness they remove is what let RA-4 and
RA-5 through:

1. **Every row states every evidence field.** Iteration 3 let three rows say "other three fields as
   above". A row that inherits its fields cannot be checked independently, and C2 is exactly a
   per-field check.
2. **Every `NEEDS_INPUT` row states the negative conditions.** The entry clause requires the element
   to be "neither determined by policy nor decided by an explicit authorization". A fixture that
   omits those makes C1 unverifiable — the same shape of omission as RA-5.

**Audit scope, stated exactly:** all 17 live codes were checked against C1/C2/C3 — 4
`ASSUMPTION_ALLOWED`, 10 `NEEDS_INPUT`, 3 `CONFLICT`. No row was skipped. The two removed codes
(`reversible_local_default`, `requirement_vs_repository_policy`) are shown struck through with the
check they fail.

**`ASSUMPTION_ALLOWED`** — entry: reversible in run; blast radius confined to the requested scope;
none of {monetary, security, privacy, compliance, lock-in} true; a locatable policy source
**supports but does not determine**; no user authority reserved. Evidence: `reason_code`,
`policy_source {locatable, "supports"}`, `reversibility`, `impact`, `retraction_condition`.

`policy_source` is admissible when it is one of the four artifact kinds A4-0 names: a file path, a
requirement id, a quality-attribute id, or a phase-contract section. Each row below states which.

| reason code | minimal valid fixture (all five evidence fields) | C1 entry | C2 evidence | C3 invariants |
|---|---|---|---|---|
| `repository_policy` | a new helper's file placement, where an existing repository convention **supports** putting it beside its siblings without mandating it. `policy_source` = that directory path (**file path** kind); `reversibility` = `reversible_in_run`; `impact` = `current_change`; `retraction_condition` = "a reviewer names a different location" | **PASS** — a locatable policy source that supports-not-determines | **PASS** — 5/5 fields | **PASS** — INV-3 all non-empty + `supports`; INV-4 no high-impact element, reversible, scope-local; INV-5 n/a |
| `explicit_requirement` | a requirement that fixes *what* to record but not the field order; its own example **supports** one order. `policy_source` = requirement id + cited line (**requirement id** kind); `reversibility` = `reversible_in_run`; `impact` = `current_change`; `retraction_condition` = "the user states a different order" | **PASS** — the requirement *supports*; had it *determined* the order, A3-1 sends the item to `CLEAR` instead | **PASS** — 5/5 | **PASS** — as above |
| `phase_contract` | the phase template lists Result Contract sections but not their subsection depth; the contract **supports** matching the sibling phases' depth. `policy_source` = the template file + heading (**phase-contract section** kind); `reversibility` = `reversible_in_run`; `impact` = `current_change`; `retraction_condition` = "a reviewer names a different depth" | **PASS** | **PASS** — 5/5, fields now stated rather than inherited | **PASS** — as above |
| `quality_profile_attribute` | two implementations both conform to an applicable attribute; the attribute **supports** the one already used elsewhere. `policy_source` = the attribute id (**quality-attribute id** kind); `reversibility` = `reversible_in_run`; `impact` = `current_change`; `retraction_condition` = "the profile changes or the attribute is withdrawn" | **PASS**, *with a documented precondition* — see the note below | **PASS** — 5/5 | **PASS** — as above |
| ~~`reversible_local_default`~~ **— REMOVED** | none exists | n/a | **FAIL (RA-4)** — entry required "no policy source"; evidence required a non-empty `policy_source` declared `supports`. Irreconcilable | n/a |

> Note on `quality_profile_attribute`: this code is only usable when `profile_status` is `loaded`.
> This repository currently has **no** `.orca/quality-profile.yaml` — only
> `.orca/quality-profile.example.yaml` and `.orca/agent-profiles.example.yaml` exist (verified in
> iteration 1 via `ls -la .orca/`), which is why this run's Task spec reports
> `profile_status: absent`. A fixture must therefore construct a loaded profile. That is a normal
> fixture precondition, not a contradiction: the code is unbuildable *in a run with no profile*,
> which is correct behaviour, not a dead entry. `QUALITY_PROFILE_STATUS = loaded, absent, invalid`
> already makes that state distinction machine-readable.

**`NEEDS_INPUT`** — entry: a boundary element is true and is neither determined by policy nor decided
by an authorization, or required intent is absent. Evidence: `reason_code`, `boundary_element`,
`what_is_missing`, `why_policy_cannot_decide`.

Every row below states the **negative conditions** the entry clause requires — no determining policy,
no explicit authorization — because a fixture that omits them cannot be checked against C1.
`what_is_missing` and `why_policy_cannot_decide` are stated per row, not inherited.

| reason code | `boundary_element` | minimal valid fixture, with `what_is_missing` / `why_policy_cannot_decide` | C1 entry | C2 evidence | C3 invariants |
|---|---|---|---|---|---|
| `ambiguous_requirement` | ambiguity | a requirement admitting two readings; **no policy selects either and no authorization exists**. missing = which reading is intended; why = no policy source addresses the choice | **PASS** — clause 1 (element true, undetermined, unauthorized) | **PASS** — 4/4 | **PASS** — INV-3/4 are `ASSUMPTION_ALLOWED`-only; INV-5 governs exits, not this entry |
| `missing_user_intent` | ambiguity (silence) | the requirement is **silent** on a choice the work cannot avoid; no policy covers it, no authorization exists. missing = the intent itself; why = there is no text to interpret and no policy substitute | **PASS** — via the entry condition's **second** clause ("required intent is simply absent"), the only row that enters that way | **PASS** — 4/4 | **PASS** |
| `irreversible_action` | reversibility | a migration that cannot be undone within the run; no policy determines it, no authorization exists. missing = authority to act irreversibly; why = A4-0 — a policy source cannot grant authority the user reserved | **PASS** | **PASS** — 4/4 | **PASS** |
| `blast_radius_beyond_scope` | blast radius | a change reaching an external system; undetermined, unauthorized. missing = authority to act outside the requested scope; why = policy cannot widen the scope the user set | **PASS** | **PASS** — 4/4 | **PASS** |
| `monetary_cost` | monetary cost | an action that spends money; no policy determines the spend, **no prior authorization**. missing = spending authority; why = A4-0 — a repository file cannot authorize spending | **PASS** | **PASS** — 4/4 | **PASS** |
| `security_impact` | security | a change to an authentication path; undetermined, unauthorized. missing = approval of the security-relevant change; why = policy cannot accept security risk on the user's behalf | **PASS** | **PASS** — 4/4 | **PASS** |
| `privacy_impact` | privacy | a change that would log personal data; undetermined, unauthorized. missing = approval to process personal data; why = same | **PASS** | **PASS** — 4/4 | **PASS** |
| `compliance_impact` | compliance | a change touching a retention rule; undetermined, unauthorized. missing = confirmation the change is compliant; why = the repository holds no compliance determination | **PASS** | **PASS** — 4/4 | **PASS** |
| `long_term_lock_in` | long-term lock-in | adopting a dependency costly to leave; undetermined, unauthorized. missing = acceptance of the long-term commitment; why = policy cannot commit the project's future on its own | **PASS** | **PASS** — 4/4 | **PASS** |
| `authority_reserved_to_user` | explicit user authority | a choice the user explicitly reserved. missing = the user's decision on that choice; why = A4-0 — *"a policy source cannot un-reserve it"* | **PASS** — and it cannot be "decided by an explicit authorization", since reserving is the opposite of authorizing | **PASS** — 4/4 | **PASS** |

> Silence note: `missing_user_intent` maps to `ambiguity` as its **degenerate case** — the
> requirement determines no reading because it says nothing, rather than determining several. This
> keeps the boundary-element set closed at the ticket's and the ROADMAP's eleven; no twelfth element
> is invented. `ambiguous_requirement` and `missing_user_intent` share the element and are separated
> by whether the requirement text is under-determined or absent.

**`CONFLICT`** — entry: clause **C-1**, **C-2**, or **C-3** of A3-1a. Evidence: `reason_code`, ≥2
citations, `why_they_cannot_both_hold`. Each code maps to exactly one clause, which is what makes C1
checkable here at all — the pre-RA-5 table had four codes against two clauses.

| reason code | the two citations, and `why_they_cannot_both_hold` | C1 entry | C2 evidence | C3 invariants |
|---|---|---|---|---|
| `requirement_contradiction` | two requirement ids whose texts cannot both be satisfied. why = satisfying either falsifies the other | **PASS** — **C-1** | **PASS** — 3/3 | **PASS** — INV-3/4 n/a; INV-5 governs the exit, which requires a `user_decision` |
| `requirement_vs_accepted_decision` | a requirement id + an accepted decision **of this run** — a `user_decision` record (A5-3) or an approved earlier-phase output. why = the new requirement reverses a decision already made and relied on | **PASS** — **C-2**, using its normative definition in A3-1a | **PASS** — 3/3 | **PASS** |
| `requirement_vs_safety_floor` | a requirement id + `RISK_SAFETY_FLOOR = mandatory_test_gates_apply_at_every_level` (`orca-worker-reviewer-orchestration/SKILL.md:923`, in the `#### Risk profile contract` block headed at line 898), with ROADMAP Principle 8 (`docs/ROADMAP.md:80-82`) as the non-overridability basis. why = the floor cannot be lowered at any risk level, so the requirement cannot be satisfied as written | **PASS** — **C-3**, added this iteration precisely so this fixture has an entry clause to satisfy | **PASS** — 3/3 | **PASS** |
| ~~`requirement_vs_repository_policy`~~ **— SUSPENDED (OQ-9)** | a requirement id + a repository policy path | **FAIL under the entry condition as written** — repository policy is none of C-1/C-2/C-3. *Whether the entry condition is right as written for this class is **open** (OQ-9); iteration 4's "resolved by precedence to `CLEAR`" basis is withdrawn (RA-6).* | n/a while suspended | n/a while suspended |

**Result: 17 confirmed live codes pass C1, C2, and C3; 1 further code is suspended pending OQ-9.**
The three-check audit result for those 17 is unaffected by OQ-9 — no option in OQ-9 changes any of
their entry clauses, evidence fields, or invariants. Of the 19 drafted:

```text
codes drafted                 19
removed for C2 (RA-4)          1   reversible_local_default          evidence unsatisfiable;
                                                                     removal CONFIRMED
suspended pending OQ-9 (RA-6)  1   requirement_vs_repository_policy  no entry clause AS WRITTEN;
                                                                     disposition UNDECIDED
confirmed live, fully checked 17   4 ASSUMPTION_ALLOWED, 10 NEEDS_INPUT, 3 CONFLICT
rows skipped                   0
```

So the closed set is **17 confirmed, with a floor of 17 and a ceiling of 18** depending on OQ-9. No
confirmed live code requires an invariant to be broken, and none is reachable only by stretching an
entry clause. **No new code was added at any point**; the one still-open question is whether a code
already drafted comes back, and under which state.

*Why the suspended row is reported this way rather than as a removal.* The RA-4 removal rests on an
irreconcilable schema contradiction — a fact about the draft that no later decision can undo. The
RA-5 removal rested on an inference about precedence that RA-6 showed the evidence does not carry.
Two removals with different evidential strength should not be reported with one word.

Two accounting checks this table also settles:

- **Boundary-element coverage.** The 10 `NEEDS_INPUT` codes cover 9 of the 11 elements. The two not
  covered are *explicit requirement conflict* — the `CONFLICT` state's own domain, covered by its
  **3** confirmed codes, one per A3-1a clause — and *repository/project policy*, which A4-0
  classifies as a boundary **input**. That classification is unchanged and is not what RA-6
  disturbed; what RA-6 withdrew is the further claim that a requirement therefore always overrides
  such an input and the item is `CLEAR`. So this element's coverage is **conditionally complete**:
  complete under OQ-9 option (c), and gaining a dedicated code under (a) or (b). Every element is
  accounted for either way, and the coverage is derived from the element list rather than asserted.
- **`CLEAR` needs no fixture row** because it carries no reason code. Its three record shapes are in
  A5-3, and all three are trivially constructible (empty record; a `determines` policy source; a
  `user_decision`). The forbidden-authority reject list likewise needs no fixtures — its five
  entries are the **negative** cases for INV-5, tested as rejections under A6 requirement 6.

This table is a static contract check, not evidence about model behaviour. It shows the vocabulary
is *implementable*; it says nothing about whether a Worker picks the right code.

---

### A6. The ten validation requirements — how each is satisfied

| # | requirement | mechanism | where it lives | what fails when violated |
|---|---|---|---|---|
| 1 | reject any state outside the four | closed `DECISION_STATES` tuple in the loader; validator asserts the SKILL.md/template vocabulary equals it | `scripts/decision_policy.py` + `validate_skills.py` | loader raises; validator emits one named check failure |
| 2 | reject invalid transitions | transition matrix as **data**; loader validates a `(from, to, provenance)` triple; one unit test per forbidden cell (≥ T-F1…T-F6) | loader + `scripts/test_*.py` | table-driven, so every forbidden cell is covered by construction |
| 3 | reject reason-less use of the three non-`CLEAR` states | required-field map keyed by state (INV-2/INV-3) | loader | one unit test per state, **plus a three-check liveness test per reason code**: A5-4's minimal fixture for each of the **17 live codes** must be *accepted*, and the assertion must cover entry condition (C1), every evidence field (C2), and the invariants (C3) — not just acceptance. A rejection-only suite passes happily with a dead code in the set, which is how RA-4 (C2) and RA-5 (C1) each survived a round |
| 4 | a high-impact irreversible fixture is not weakened | fixture declaring `irreversible` + `blast_radius=repository`; assert INV-4 forbids `ASSUMPTION_ALLOWED` with **no state permitted to be `ASSUMPTION_ALLOWED`** — absent an authorization only `{NEEDS_INPUT, CONFLICT}`, and *with* one, `CLEAR` (never `ASSUMPTION_ALLOWED`). Third case is A4-0's anti-weakening test: the same fixture **plus** a cited `policy_source{determines}` and **plus** a `user_decision`, asserting `ASSUMPTION_ALLOWED` stays forbidden in both | fixture + unit test | plus requirement 7's cross-risk parametrization, so it cannot be weakened by changing risk |
| 5 | a safe fixture is not unconditionally forced to `NEEDS_INPUT` | fixture that is reversible, scope-local, no boundary element true, citing a policy source declared **`supports`** (a `determines` source would correctly yield `CLEAR`, not `ASSUMPTION_ALLOWED` — A4-0); assert `ASSUMPTION_ALLOWED` is **permitted** and that the contract does **not** require `NEEDS_INPUT` there | fixture + unit test | **permission level only — decided by the user as UD-2**, not chosen here. The limitation must be stated and never reported as solved: a contract-level test cannot detect a real model's over-escalation. This is the test that proves A4-0's last row is reachable and the contract has not collapsed into all-`NEEDS_INPUT` |
| 6 | confidence is never authority | forbidden-authority reject list; test that a `NEEDS_INPUT → CLEAR` transition citing `model_confidence` (and each other entry) is rejected | loader + unit test | one test per reject-list entry |
| 7 | changing risk does not change authority | parametrize the same fixture over `risk ∈ {low, medium, high}`, assert an identical permitted-state set; plus a validator check that no decision-policy key names a risk level or profile | unit test + validator | mirrors `RISK_QUALITY_PROFILE_AXIS` precedent |
| 8 | drift between the two Skills fails | machine block in the shared ` ```policy-contract ` JSON (already deep-equal asserted, `validate_skills.py:1122-1126`); prose in `templates/**` + `reviews/**` (byte-equal asserted, `validate_skills.py:800-822`) | existing validators — **no new parity machinery needed under OQ-4(a)**; under OQ-4(b) this row costs one new both-Skills parity validator (A1-6a) | plus a `test_validate_skills.py` regression test that mutates one Skill's copy and asserts a named failure, following `test_workflow_output_contract_drift_fails` |
| 9 | malformed / unknown schema version fails closed | `SUPPORTED_SCHEMA_VERSIONS` in `scripts/decision_policy.py`, **raising** — follow `quality_profile.py:521-528` and `agent_profile.py:462-467`, **not** `load_risk_contract`'s return-`None` convention | loader | the pre-existing `evaluate_invocation` gap is **out of scope by user decision UD-3** — recorded as a pre-existing defect and a follow-up ticket candidate. OS-28 must not report it as fixed, and does not worsen it |
| 10 | no lifecycle / package regression | baseline recorded (validator 501 checks PASSED); `release_manifest.py:INCLUDED_ROOTS` already contains `scripts`, so a new module is packaged with no manifest edit; no new top-level doc ⇒ `REQUIRED_DOCS` untouched; `RUN_STATUS_VALUES` untouched | re-run `validate_skills.py`, the unittest suite, `verify_package.py`, `build_release.py` | CI already runs all four (`.github/workflows/*.yml`) |

**What these ten do not prove.** All ten are *contract-level*. None of them can demonstrate that an
LLM classifies a real decision correctly — that requires the gate to actually run (OS-29) and an
evaluation harness (OS-32). OS-28's completion report must state this explicitly rather than let
"10/10 validation requirements satisfied" read as "the workflow now escalates correctly".

---

### A7. Scope boundary

**In scope for OS-28:**

- the four-state vocabulary as a shared, machine-readable contract;
- entry conditions, allowed/forbidden transitions, continue/pause semantics — expressed as data;
- the decision-boundary element vocabulary with closed value sets;
- the closed reason-code set and the forbidden-authority reject list;
- required evidence fields per state;
- Reviewer misclassification-judgment rules as prose in the shared `reviews/`;
- a loader with fail-closed schema versioning;
- validator parity checks and unit tests for the ten requirements.

**Out of scope, explicitly:**

| ticket | what it owns — and OS-28 must not pre-build it |
|---|---|
| **OS-29** | running the check at each phase gate; wiring a state into the phase transition; where in the dispatch flow the check fires |
| **OS-30** | the structured question format, option presentation, the `orca orchestration ask` interaction, any user-facing UI |
| **OS-31** | a `WAITING_FOR_INPUT` state, durable pause records, resume-from-responsible-phase, `HumanApprovalPort`, notification adapters, post-answer resumption |
| **OS-32** | metric definitions and the evaluation harness |

Also out of scope: adding a fifth `RUN_STATUS` value or touching `RUN_STATUS_VALUES`; changing risk,
quality-profile, agent-profile, Final Review, or Orca lifecycle semantics; changing the meaning of
the existing `ESCALATED` status or `PHASE_CONFLICT` error code; `VERSION`/`LICENSE`; other Jira
tickets; past run/artifact history.

**The boundary line, stated once:**

> OS-28 defines **what a decision state means and when it is legal**.
> It does not define **when the check runs, how the question is asked, or how the run waits.**
> Anything that requires the workflow to stop and wait is OS-29/30/31.

Grey area that needed a decision, not a guess: recording is in scope ("자동 결정에는 … 기록한다"), but
a *place to write the record* means touching the Worker/Reviewer Result Contract, which is parsed by
`workflow_contract.py` and duplicated across 7 templates × 2 Skills. Without a place to write it,
the contract is unobservable and the requirement-4/5/6 fixtures have nothing to bind to. Raised as
**OQ-2** in iteration 1; **decided by the user as UD-1** — an **optional** decision record section is
added to `templates/**` and `reviews/common.md`. Its absence is not a contract violation; only when
the section is present are the state, reason code, and evidence format validated. Recording is
therefore in scope for OS-28, as an additive optional section.

---

### A8. Risks and open questions

#### Decided by the user (UD-1…UD-3)

Three of the eight open questions have since been **decided by the repository owner**, not by this
Worker and not by Worker/Reviewer agreement. The Coordinator put each to the user as a structured
question with options, impact, and a recommendation; the answers are recorded in
`artifacts/runs/run_3233a1469e97/USER_DECISIONS.md`. That file states its own admissibility rule —
Worker/Reviewer agreement, a recommended default, and non-response/timeout **cannot** be recorded
there — which matches this contract's own forbidden-authority reject list (A5-2).

| was | now | decision | applied where |
|---|---|---|---|
| OQ-2 | **UD-1** | Add an **optional** decision record section to the Result Contract templates and `reviews/common.md`. Absence of the section is **not** a contract violation; the state / reason code / evidence format is validated **only when the section is present** | A7 grey-area paragraph; the OQ-2 row below |
| OQ-6 | **UD-2** | Validation requirement 5 is proven to the **permission** level only. The limitation — a contract-level test cannot detect a real model's over-escalation — must be stated explicitly and **must not** be reported as solved | A6 requirement 5; R-1; the OQ-6 row below; Unit Tests / Testing Strategy |
| OQ-7 | **UD-3** | The missing `schema_version` gate in the existing `evaluate_invocation()` is **out of scope**. Record it as a pre-existing defect and a follow-up ticket candidate. OS-28 must **not** claim to have fixed it | A6 requirement 9; the OQ-7 row below |

These three are the user's decisions. This analysis records them; it did not make them.

#### Open questions — **still not decided here**

OQ-1, OQ-3, OQ-4, OQ-5, OQ-8 and **OQ-9** remain open and are **not** settled by this iteration.
OQ-9 is new in iteration 5: RA-6 withdrew the basis on which iteration 4 removed
`requirement_vs_repository_policy`, so that disposition returns here rather than standing on a
reason that no longer holds. The three decided rows are retained below, marked, so the options and
impact that were put to the user stay auditable.

| id | question | options | impact | recommendation / **decision** |
|---|---|---|---|---|
| **OQ-1** | Is a decision state a property of the **phase check** (one state per phase per iteration) or of an individual **decision item** (n per phase)? | (a) per-check enum only; (b) per-item + derived per-check aggregate; (c) per-item only | (a) is smaller but makes T-F6 and per-item evidence undefinable; (b) satisfies the ROADMAP's "a check produces one of four states" *and* the ticket's evidence requirements; (c) contradicts the ROADMAP sentence | **(b)**, aggregate ordered `CONFLICT > NEEDS_INPUT > ASSUMPTION_ALLOWED > CLEAR` |
| **OQ-2** ✅ | Does OS-28 add the decision record to the Worker/Reviewer Result Contract? | (a) no — contract only, nothing writes a record; (b) additive **optional** section in templates + `reviews/common.md`; (c) required section | (a) leaves the contract unobservable and requirements 4/5/6 with nothing to bind to; (b) touches 7 templates × 2 Skills but they are byte-shared so it is one edit set; (c) forces every phase to emit the section even when `CLEAR`, and is a breaking change to existing artifacts | **DECIDED BY THE USER — UD-1: (b).** Optional section; its absence is not a violation, and the format is validated only when present |
| **OQ-3** | How do decision states relate to `RUN_STATUS_VALUES` / Worker `STATUS` / `REVIEW_VERDICT`? | (a) separate axis, OS-28 changes none of them; (b) reuse `ESCALATED` for a pause; (c) add a run status now | (b) collapses "budget exhausted, gave up" and "waiting for a decision, will resume" into one token; (c) is OS-31's scope | **(a)** — and state the separation explicitly so OS-31 is not foreclosed |
| **OQ-4** | Where does the machine block live? | (a) shared ` ```policy-contract ` JSON; (b) **new shared** `####` anchor block in **both** Skills; (c) new `scripts/`-side constant only; (d) `.orca/` file | (a) parity + versioning for free, native nesting, but mixes invocation-time and phase-time policy in one block; (b) **available — no validator forbids it (A1-2a)** — costs one new both-Skills parity validator, a first `####` heading in the loop skill, and a flattened key set against the anchor grammar, in exchange for the `*_MAX_LINES` budget idiom (A1-6a); (c) has no document to review; (d) confuses lifecycle policy with project config | **(a)**, on grammar fit and parity cost — **not** on (b) being impossible |
| **OQ-5** | Closed reason-code set with no escape hatch? | (a) closed, no escape; (b) closed + `OTHER`; (c) closed + `unclassifiable_decision`, which itself **forces `NEEDS_INPUT`** | (a) a genuinely novel reason blocks a live run; (b) `OTHER` becomes the default within one run; (c) preserves closure and fails safe — an unclassifiable decision is exactly one the user should decide | **(c)** |
| **OQ-6** ✅ | Requirement 5 ("a safe fixture must not be unconditionally `NEEDS_INPUT`") — is *permission* enough? | (a) yes: prove the contract **permits** `ASSUMPTION_ALLOWED`; (b) no: prove a run **produces** it | (b) needs an LLM in the loop and an evaluation harness — that is OS-32, and is not achievable in a contract-only ticket | **DECIDED BY THE USER — UD-2: (a).** Permission level only, **and** the limitation must be stated explicitly and never reported as solved |
| **OQ-7** ✅ | `evaluate_invocation()` has **no `schema_version` gate** today (verified). Fix it here? | (a) out of scope — OS-28's own loader is fail-closed, the existing path is unchanged; (b) in scope — add the gate to the existing path too | (b) is a behaviour change to a shipped path: a contract with an unexpected version currently runs, and would start failing closed. Real improvement, real regression surface | **DECIDED BY THE USER — UD-3: (a).** Out of scope; recorded as a **pre-existing** defect and a follow-up ticket candidate. OS-28 neither fixes nor worsens it |
| **OQ-8** | If OQ-4 resolves to an anchor block, `orca-worker-reviewer-loop/SKILL.md` gains its first `####` heading and its first anchor contract. Accept? | (a) avoid by choosing OQ-4(a); (b) accept the document change | **this is a convention cost, not a validator conflict** — verified: no check constrains heading levels in that file (A1-2a), and the only recorded objection is a code comment at `validate_skills.py:485-488`. (b) is a structural change to a Skill this ticket otherwise does not touch, and adds a both-Skills parity validator | **(a)**, but the cost is small enough that OQ-4(b) is not blocked by it |
| **OQ-9** *(new, RA-6)* | What is the decision state when an explicit requirement contradicts a repository policy that is **not** a quality-profile attribute — a convention, project configuration, code structure, or a security / privacy / compliance / tooling policy — and what happens to the suspended `requirement_vs_repository_policy` code? | **(a)** restore the code under `CONFLICT` — this class is resolved by the user; **(b)** restore the code under `NEEDS_INPUT` — the user supplies the missing authority; **(c)** keep the removal: only the quality-gate-tier case is `CLEAR` by the requirement, and everything outside that tier is routed by the existing boundary elements rather than by a policy-specific code | See the impact analysis below the table — including the machine-checkability question the correction brief requires, and what state applies when the distinction cannot be made | **(c)**, with the reasoning and its residual risk stated below. **Registered, not decided.** |

##### OQ-9 impact analysis

**The machine-checkability question first, because every option depends on its answer.** Can code
distinguish an **overridable** policy from a **non-overridable invariant**?

| | evidence |
|---|---|
| **For the one invariant C-3 already names — yes.** | `RISK_SAFETY_FLOOR = mandatory_test_gates_apply_at_every_level` is a named key in a parsed contract block (`orca-worker-reviewer-orchestration/SKILL.md:923`), so a loader can read it. |
| **For repository policy in general — no, not today.** | I searched for an overridability marker and found none. `.orca/quality-profile.example.yaml:24-26` documents its only near-neighbour field, and it means something different: *"`blocking: true` means a violation FAILS the workflow gate"* — i.e. whether a violation fails the gate, **not** whether a user requirement may override the attribute. `QUALITY_GATE_SEVERITY_RULE = severity_is_not_blocking` and `QUALITY_GATE_BLOCKING_SOURCES` (`SKILL.md:1542-1543`) are likewise about gate outcomes. A repository convention, a config file, or a security policy document carries **no** machine-readable overridability flag at all. |

So: **overridability is machine-checkable only where a policy is already expressed as a named key in
a parsed contract, which today means the safety floor alone.** Everywhere else it is a judgment.
This is the same code/judgment split as A4-1, and it is why the option comparison below turns on
what happens in the *undistinguishable* case rather than on which state reads best.

| option | what it does | impact, including the undistinguishable case |
|---|---|---|
| **(a)** restore under `CONFLICT` | this class is contradictory information the user resolves | Requires **widening the C-1/C-2/C-3 entry condition** the Reviewer just confirmed — the cost the correction brief specifically warns against. It is also a poor semantic fit: `CONFLICT` means *cannot all be satisfied*, but an overridable convention **can** be satisfied by following the requirement. Undistinguishable case → `CONFLICT`, i.e. the workflow pauses on every convention disagreement, including trivial ones. Highest over-escalation cost (R-1). |
| **(b)** restore under `NEEDS_INPUT` | missing authority, not contradiction | Semantically closer than (a) — the missing thing is *authority to override*, which is what `NEEDS_INPUT` is for — and it needs no change to the confirmed entry condition. Fail-closed, matching ROADMAP Principle 4 (*"Fail closed when provenance is uncertain"*). But since overridability is undistinguishable in general, the undistinguishable case is the **normal** case, so this pauses on most policy disagreements. Also duplicative: a security/privacy/compliance policy conflict **already** trips `security_impact` / `privacy_impact` / `compliance_impact`, so those items reach `NEEDS_INPUT` with or without this code. |
| **(c)** keep the removal, route by boundary element | the quality-gate-tier case is `CLEAR` by the narrow claim; outside that tier the **eleven boundary elements** decide, with no policy-specific code | Adds nothing to the closed set and touches no confirmed text. The undistinguishable case is resolved by asking the question the boundary already asks: *is any of the eleven elements true?* A security/privacy/compliance policy conflict → `NEEDS_INPUT` via the matching element. A conflict with a policy carrying a machine-readable non-overridable marker → `CONFLICT` via C-3. A plain convention with **no** element true → `CLEAR` by the requirement, which is the case the user explicitly asked for. Residual risk, stated plainly: this **assumes the eleven elements catch every policy class that matters**. If a policy class exists that matters and trips no element, (c) silently resolves it to `CLEAR`. I have not enumerated repository policy classes to test that assumption — **확인하지 않음** — and OS-32's silent-assumption-defect metric is where it would show up. |

**Recommendation: (c).** It adds no reason code, requires no change to the confirmed C-1/C-2/C-3
entry condition, and needs no new authority rule — and avoiding a new authority rule is the entire
point of RA-6. Its weakness is a stated assumption rather than a stated fact, which is the honest
trade. **This is a recommendation only; OQ-9 is registered and not decided**, and a PLAN phase should
put it to the user the way UD-1/2/3 were put.

#### Risks

| id | risk | why it is real here | mitigation |
|---|---|---|---|
| **R-1** | **Over-blocking.** The cheapest way to satisfy INV-4 is to classify everything `NEEDS_INPUT`. | The ticket names this as a wrong implementation, and requirement 5 is the only counterweight — and it is *permission-only* by **user decision UD-2**. Contract-level tests cannot detect a model that over-escalates | requirement 5 as the floor; real detection deferred to OS-32's unnecessary-question rate. **UD-2 requires this limit to be stated explicitly and not reported as solved** |
| **R-2** | **T-F2 is the highest-value rule and the hardest to verify after the fact.** | The `ASSUMPTION_ALLOWED` record is **self-produced**. Code can check its shape, never its truth | make T-F2 an outright forbidden transition (not merely "requires a record"); put the *why* in `templates/**` per OS-27's dual structure; give the Reviewer an explicit misclassification rule per reason code |
| **R-3** | **Contract-only ticket, execution-shaped expectations.** Nothing executes this contract until OS-29. | The ROADMAP already says the bounded-autonomy contract "is not current behavior until [its] own Jira issues and acceptance criteria are implemented and validated" | OS-28's completion report must not read as "the workflow now escalates correctly". Repository history (per the task brief) has already been burned by claims wider than the evidence |
| **R-4** | **Prompt budget.** `orca-worker-reviewer-orchestration/SKILL.md` is 131 KB / 2110 lines. | The anchor-block `*_MAX_LINES` budgets (4, 14, 17, 20) exist precisely to bound this | adopt an explicit line budget for the decision contract and assert it, whichever host OQ-4 picks |
| **R-5** | **Inheriting a fail-open.** `load_risk_contract()` returns `None` on a malformed block and the runtime reads `None` as "no risk axis". | Copying the nearest precedent would import that fail-open straight into the decision contract, directly violating requirement 9 | follow `quality_profile.py` / `agent_profile.py` (raise), not `load_risk_contract` (return `None`). Call this out in the PLAN so a reviewer does not "fix" it toward the wrong precedent |
| **R-6** | **Vocabulary collision.** `CONFLICT` vs `PHASE_CONFLICT`; `NEEDS_INPUT` vs `BLOCKED`; pause vs `ESCALATED`. | All three tokens already exist with different meanings (verified, A2-1) | disambiguate explicitly in the contract prose; do not reuse any of the three existing tokens |
| **R-7** | **Terminology drift against a published document.** The ticket and `docs/ROADMAP.md` word 3 of the 11 boundary elements differently. | The ROADMAP is the published artifact; a mismatch invites a later "which is authoritative?" round | pick the ROADMAP's wording for the published contract, or update the ROADMAP in the same change — decide once, in PLAN |

---

## Changes

None. This phase produced analysis only; no repository source file, Skill, script, test, or document
was created or modified.

## Modified Files / Artifacts

| path | change |
|---|---|
| `artifacts/runs/run_3233a1469e97/ANALYSIS.md` | **updated in place** (this file) — created in iteration 1, corrected in iterations 2, 3, 4, and 5 |

`artifacts/runs/run_3233a1469e97/USER_DECISIONS.md` was **read, not written** — it is the
Coordinator's record of the user's answers, and this Worker does not edit it.

No other file was written in any iteration. Verified for iterations 2 and 3 by
`git status --porcelain -- scripts orca-worker-reviewer-orchestration orca-worker-reviewer-loop docs`
and `git diff --stat` over the same paths; both returned empty output.

## Validation

Read directly and in full or in the cited ranges:

| file | what was checked |
|---|---|
| `docs/ROADMAP.md` (312 lines) | Bounded Autonomy Model section, Vision, Architecture Principles, Milestones, Non-Goals |
| `scripts/skill_policy.py` (615 lines) | `CONTRACT_BLOCK_PATTERN`, `RISK_CONTRACT_BLOCK_PATTERN`, `load_policy_contract`, `load_risk_contract`, `evaluate_invocation`, `_resolve_agent_routing`, `finalize_routing` |
| `scripts/workflow_contract.py` (146 lines) | full file |
| `scripts/validate_skills.py` (2260 lines) | constants (1-420), `validate_shared_directories`, `validate_policy_contracts`, `validate_machine_readable_contracts`, `parse_anchor_contract`, `validate_risk_profile_contract`, `validate_agent_profile_contract`, `validate_phase_gate_neutrality`, `validate_workflow_output_contracts`, `main` |
| `orca-worker-reviewer-orchestration/SKILL.md` (2110 lines) | `policy-contract` block, all 9 `#### … contract` headings, §13 escalation, §14 test gates, full `## ` heading map |
| `orca-worker-reviewer-loop/SKILL.md` (1096 lines) | full heading map, `policy-contract` block, Agent Profile section; confirmed **zero** `####` headings |
| `orca-worker-reviewer-orchestration/reviews/common.md` | full file |
| `orca-worker-reviewer-orchestration/templates/analysis.md`, `templates/implementation.md` | full files |
| `scripts/run_logging.py` | `RUN_STATUS_VALUES` and its fail-closed check |
| `scripts/quality_profile.py`, `scripts/agent_profile.py` | schema-version handling |
| `scripts/release_manifest.py` | `INCLUDED_ROOTS`, `REQUIRED_DOCS`, `FORBIDDEN_PARTS` |
| `scripts/test_validate_skills.py`, `scripts/test_policy_smoke.py` | harness shape and existing drift-regression test pattern |
| `docs/deterministic_flow_idea.review_by_opus.md` | lines 95-120, the "코드가 강제하고 프롬프트가 이유를 설명한다" passage |
| `.github/workflows/*.yml` | the four CI steps |

Commands executed:

| command | result |
|---|---|
| `python3 scripts/validate_skills.py` | **PASSED (501 checks)** |
| `grep -c 'decision' orca-worker-reviewer-loop/SKILL.md` | `0` |
| `grep '^#### ' orca-worker-reviewer-loop/SKILL.md` | no matches |
| `grep -n schema_version scripts/skill_policy.py` | no matches |
| `python3 -m unittest discover -s scripts -p 'test_*.py'` | **Ran 1269 tests in 298.5s — OK (skipped=6)**, exit 0 |

Added in iteration 2, for the RA-3 correction and the RA-N1 sourcing:

| command | result |
|---|---|
| `grep -n 'loop_skill\|loop_text\|orca-worker-reviewer-loop' scripts/validate_skills.py` | 8 validator sites, each read at its lines and tabulated in A1-2a |
| `sed -n '1820,1845p' scripts/validate_skills.py` | `validate_risk_profile_contract`'s two loop-skill checks, read verbatim (the lines the Reviewer cited) |
| `sed -n '480,492p' scripts/validate_skills.py` | the `LOOP_AGENT_PROFILE_PROSE_ANCHORS` comment at 485-488 — the convention statement quoted in A1-2a |
| `sed -n '745,800p' scripts/validate_skills.py` | `extract_phase_routes`, `validate_frontmatter`, `validate_routes_and_files` — confirmed **none** constrains heading levels |
| `grep -n 'Non-Goals' -A 16 docs/ROADMAP.md` | the Non-Goals list; the authorization item at lines 298-299 names **three** things |
| `grep -n -i 'timeout\|elapsed\|no response\|absence' docs/ROADMAP.md` | **no matches** — the ROADMAP nowhere mentions timeout, elapsed time, or absence of a response |
| `git status --porcelain -- scripts orca-worker-reviewer-orchestration orca-worker-reviewer-loop docs` | **empty** — iteration 2 changed no tracked source file either |
| `git diff --stat -- scripts orca-worker-reviewer-orchestration orca-worker-reviewer-loop docs` | **empty** |

Added in iteration 3, for the RA-4 correction and the UD-1/2/3 records:

| command | result |
|---|---|
| `cat artifacts/runs/run_3233a1469e97/USER_DECISIONS.md` | three explicit user decisions (UD-1/2/3) with question, decision, rationale, and application method; the file states that Worker/Reviewer agreement, a recommended default, and non-response/timeout cannot be recorded in it |
| `ls -la .orca/` | only `agent-profiles.example.yaml` and `quality-profile.example.yaml` — **no** `quality-profile.yaml`, confirming the `profile_status: absent` precondition noted against `quality_profile_attribute` in A5-4 |
| `sed -n '898p' orca-worker-reviewer-orchestration/SKILL.md` | `#### Risk profile contract` — the block heading cited in A5-4 |
| `grep -n 'RISK_SAFETY_FLOOR' orca-worker-reviewer-orchestration/SKILL.md` | line **923**: `RISK_SAFETY_FLOOR = mandatory_test_gates_apply_at_every_level` — the citation used by the `requirement_vs_safety_floor` fixture |
| `git status --porcelain -- scripts orca-worker-reviewer-orchestration orca-worker-reviewer-loop docs VERSION` | **empty** |
| `git diff --stat -- scripts orca-worker-reviewer-orchestration orca-worker-reviewer-loop docs` | **empty** |

Added in iteration 4, for the RA-5 correction:

| command | result |
|---|---|
| `grep -n 'Safety is independent' -A 3 docs/ROADMAP.md` | Architecture Principle 8 at **lines 80-82**: *"Risk settings may change review strength, but never remove mandatory test gates or authorize unsafe lifecycle cleanup."* — the non-overridability basis for clause C-3 |
| `grep -n 'QUALITY_GATE_DECISION_PRIORITY' orca-worker-reviewer-orchestration/SKILL.md` | line **1540**: `explicit_requirements, project_quality_attributes, current_phase_contract, minimal_general_gate` |
| `grep -n '### Decision Priority' -A 10 orca-worker-reviewer-orchestration/reviews/common.md` | **lines 28-35**, the four-tier list in the shared review policy — the precedence A3-1a cites for removing `requirement_vs_repository_policy` |
| `git status --porcelain -- scripts orca-worker-reviewer-orchestration orca-worker-reviewer-loop docs VERSION` | **empty** |
| `git diff --stat -- scripts orca-worker-reviewer-orchestration orca-worker-reviewer-loop docs` | **empty** |

Added in iteration 5, for the RA-6 correction — specifically to test whether overridability is
machine-checkable, which OQ-9's options turn on:

| command | result |
|---|---|
| `grep -n 'blocking' .orca/quality-profile.example.yaml` | the only near-neighbour field; **lines 24-26** define it as *"`blocking: true` means a violation FAILS the workflow gate"* — a gate-outcome flag, **not** an override permission |
| `grep -n 'QUALITY_GATE_SEVERITY_RULE\|QUALITY_GATE_BLOCKING_SOURCES' orca-worker-reviewer-orchestration/SKILL.md` | **lines 1542-1543** — also about gate outcomes, not overridability |
| `grep -rn 'overridable\|override' --include=*.py --include=*.md --include=*.yaml scripts/ orca-worker-reviewer-orchestration/ .orca/` | **no overridability marker exists** — every hit is an unrelated use of "override" (parameter defaults, test helpers, profile phase overrides). This is the evidence for OQ-9's "not machine-checkable in general" finding |
| `git status --porcelain -- scripts orca-worker-reviewer-orchestration orca-worker-reviewer-loop docs VERSION .orca` | **empty** |

The iteration-1 baseline (501 checks / 1269 tests) is **not** re-run in iterations 2, 3, 4, or 5 and
is not re-claimed as fresh: all four correction iterations edited only this artifact, and the `git`
commands above are the evidence that nothing under `scripts/`, either Skill, `docs/`, `.orca/`, or
`VERSION` changed.

Explicitly **확인하지 않음**:

- the Jira OS-28 issue body itself (only the task brief and `docs/ROADMAP.md` were available);
- Jira OS-29/30/31/32 issue bodies — their scope is taken from `docs/ROADMAP.md` and the task brief,
  not from Jira;
- whether `orca-worker-reviewer-orchestration/tools/` contains anything relevant (directory noted,
  contents not read).

## Unit Tests / Testing Strategy

No test was added or modified in this phase — this is ANALYSIS, and the task explicitly forbids
writing code.

Testing strategy proposed for the implementation phases (the ten requirements map 1:1 onto it; see
the A6 table for the full mapping):

1. **Loader unit tests** (`scripts/test_decision_policy.py`, new): closed-vocabulary rejection
   (req. 1), required-field rejection (req. 3), fail-closed schema versioning (req. 9).
2. **Transition table tests**: one case per forbidden cell T-F1…T-F6, table-driven so adding a
   forbidden cell without a test is impossible (req. 2).
3. **Fixture tests**: a high-impact irreversible fixture that cannot become `ASSUMPTION_ALLOWED`
   (req. 4), and a safe fixture that is not *forced* to `NEEDS_INPUT` (req. 5, permission-only by
   **user decision UD-2**).
3b. **Reason-code liveness tests, three checks per code**: A5-4's minimal valid fixture for each of
   the **17 live codes** must be **accepted**, asserting all three of C1 (the cited basis falls under
   the state's entry-condition wording), C2 (every required evidence field is fillable), and C3 (no
   invariant is violated). This is the direct guard against the defect that produced both RA-4 (a C2
   failure) and RA-5 (a C1 failure): a code that cannot satisfy entry and evidence at once is
   invisible to a suite that only tests rejections. Asserting acceptance alone would have caught
   RA-4 but **not** RA-5 — a fixture can be accepted by a loader while citing a basis the entry
   clause never named — which is why C1 is a separate assertion rather than folded into the others.
4. **Authority tests**: one case per forbidden-authority reject-list entry (req. 6).
5. **Axis-independence test**: the same fixture parametrized over `risk ∈ {low, medium, high}`,
   asserting an identical permitted-state set (req. 7).
6. **Validator regression tests** (`scripts/test_validate_skills.py`, extended): mutate one Skill's
   copy of the vocabulary and assert a *named* failure — following the existing
   `test_workflow_output_contract_drift_fails` / `test_shared_template_drift_fails` pattern
   (req. 8).
7. **Regression baseline**: `validate_skills.py`, the full unittest suite, `verify_package.py`, and
   `build_release.py` + archive verification — the four steps CI already runs (req. 10).

Three limits to state up front, so they are not discovered at review time. The first two are
**required to be stated** by user decisions UD-2 and UD-3:

- **(UD-2)** these tests validate the **contract**, never an LLM's classification of a real decision.
  Requirement 5 is provable only as *permission* (the contract does not force `NEEDS_INPUT`), not as
  *production*, until OS-32 exists. **A contract-level test cannot detect a real model's
  over-escalation, and OS-28 must not report that problem as solved.**
- **(UD-3)** the missing `schema_version` gate in the existing `evaluate_invocation()` is a
  **pre-existing** defect that OS-28 leaves in place. The new loader is fail-closed; the shipped
  path is unchanged. OS-28 neither fixes nor worsens it, and must not claim otherwise. It is
  recorded as a follow-up ticket candidate.
- A5-4's fixture table proves the reason-code vocabulary is *implementable*. It says nothing about
  whether a Worker selects the right code — that is OS-29/OS-32 territory.

## Review Feedback Resolution

Iteration 5, correcting the one new blocking finding (RA-6) in the current
`artifacts/runs/run_3233a1469e97/REVIEW_ANALYSIS.md` (`RESULT: FAIL`). The Reviewer confirmed RA-5
and the 17-code fixture audit as **RESOLVED**; those areas were not rewritten.

```text
FINDING RA-6:  RESOLVED   (new in iteration 4's review; fixed in iteration 5)
FINDING RA-N3: NOTED      (non-blocking; forward action for PLAN — see below)
FINDING RA-5:  RESOLVED   (confirmed by the Reviewer; untouched in iteration 5)
FINDING RA-4:  RESOLVED   (confirmed in iteration 3's review; untouched since)
FINDING RA-N2: VERIFIED   (UD-1/2/3 records confirmed; untouched since)
FINDING RA-1:  RESOLVED   (confirmed in iteration 2's review; untouched since)
FINDING RA-2:  RESOLVED   (confirmed in iteration 2's review; untouched since)
FINDING RA-3:  RESOLVED   (confirmed in iteration 2's review; untouched since)
FINDING RA-N1: RESOLVED   (confirmed in iteration 2's review; untouched since)
```

| finding | resolution | where |
|---|---|---|
| **RA-6** (blocking, G5) — the citation was narrowed in iteration 4 but the **conclusion was still generalized**: a quality-gate precedence was extended to repository conventions, project configuration, code structure, and security / compliance / tooling policy, making all of them auto-`CLEAR` | **RESOLVED by narrowing the conclusion to the citation and returning the decision to the open set — no new authority rule was created, which is the point of the finding.** The claim now reads, at its actual width: *"when judging a quality finding, an explicit requirement outranks an applicable project quality-profile attribute, which outranks the current phase contract, which outranks the minimal general gate"* — and the text states explicitly what that does **not** establish (repository conventions in general, project configuration, code structure, security/privacy/compliance/tooling policy, and decision-boundary classification as an axis). Iteration 4's sentence *"This repository already resolves that class by documented precedence"* is **withdrawn** verbatim. A4-0's "the requirement wins" paragraph is narrowed the same way and now says A4-0 does not classify the out-of-tier case at all. | A3-1a "Requirement vs. repository policy" (rewritten); A4-0 "When a policy source and an explicit requirement disagree" (narrowed) |
| **RA-6** — the `requirement_vs_repository_policy` removal | **NOT confirmed; changed to SUSPENDED.** Iteration 4 removed the code on the strength of the now-withdrawn generalization, so keeping the removal would leave a decision standing on a reason that no longer holds. The distinction is stated: RA-4's removal rests on an irreconcilable schema contradiction — a fact no later decision can undo — while this one rested on an inference the evidence does not carry, and two removals of different evidential strength should not be reported with one word. What survives is only the narrow fact that the code satisfies no clause of the entry condition **as written**; whether that entry condition is right for this class is now **OQ-9**. | A5-2 (removal → suspension, with an explicit "not established / established" block); A5-4 `CONFLICT` row and count block |
| **RA-6** — register OQ-9 | **DONE, registered and explicitly not decided.** Options (a) restore under `CONFLICT`, (b) restore under `NEEDS_INPUT`, (c) keep the removal and route out-of-tier conflicts by the existing boundary elements. The machine-checkability question the brief requires is answered with evidence: overridability **is** machine-checkable where a policy is a named key in a parsed contract (today, only `RISK_SAFETY_FLOOR`), and **is not** in general — verified by `grep -rn 'overridable\|override'` across `scripts/`, the orchestration Skill, and `.orca/`, which returns **no** overridability marker, and by `.orca/quality-profile.example.yaml:24-26` showing that `blocking` means "a violation fails the gate", not "may not be overridden". Each option states what happens in the **undistinguishable** case, which is the normal case. Recommendation **(c)**, with its residual assumption stated as an assumption — that the eleven boundary elements catch every policy class that matters, which I have **not** enumerated (**확인하지 않음**). | A8 OQ table (new OQ-9 row); new "OQ-9 impact analysis" subsection; Summary; A8 intro |
| **RA-6** — set-size honesty | **DONE.** A5-4 now reports **17 confirmed live codes + 1 suspended**, with a floor of 17 and a ceiling of 18 depending on OQ-9, rather than counting the suspension as a settled removal. The three-check audit result for the 17 is explicitly unaffected by OQ-9, since no option changes any of their entry clauses, evidence fields, or invariants. Boundary-element coverage for *repository/project policy* is restated as **conditionally complete** — complete under (c), gaining a dedicated code under (a) or (b). | A5-4 result paragraph, count block, and coverage bullet |
| **RA-N3** (non-blocking) — the 17-code fixture table is a document check, not runtime acceptance evidence; PLAN should track positive liveness plus reject-list negative tests | **NOTED; no change needed.** The limit is already stated in A5-4 (*"a static contract check, not evidence about model behaviour"*) and the tracking the finding asks for is already specified in A6 requirement 3 and Testing Strategy step 3b, both of which name the three-check liveness assertion and the per-entry reject-list tests. Recorded here so the forward action is not lost. | no edit — A5-4 closing paragraph; A6 requirement 3; Testing Strategy 3b |

**OQ-1, OQ-3, OQ-4, OQ-5, OQ-8 and the new OQ-9 remain open** and were not settled in this
iteration. **A3-1a's C-1/C-2/C-3 definitions and the `requirement_vs_safety_floor` handling are
byte-unchanged**, as the correction brief required — RA-6 concerns only the repository-policy axis.

Iteration 4's resolutions, retained for audit:

| finding | resolution | where |
|---|---|---|
| **RA-5** (blocking, G3) — `requirement_vs_repository_policy` and `requirement_vs_safety_floor` cited bases that the `CONFLICT` entry condition never named *(the `requirement_vs_repository_policy` half of the row below is **superseded by iteration 5**: RA-6 withdrew the precedence generalization it rests on, the code is now **suspended pending OQ-9** rather than removed, and the live count reads **17 confirmed + 1 suspended**. The `requirement_vs_safety_floor` / C-1/C-2/C-3 half stands as the Reviewer confirmed it. The original text is kept unrewritten, per the repository's rule against rewriting historical evidence.)* | **RESOLVED with a split answer, because the two codes are different cases and one label for both would be wrong.** `requirement_vs_safety_floor` → **option (a)**: the entry condition now states three explicit clauses (A3-1a), the third being *an explicit requirement contradicts a **non-overridable project invariant*** — one the workflow has no authority to lower at any risk level, i.e. `RISK_SAFETY_FLOOR` (`SKILL.md:923`) backed by ROADMAP Principle 8 (`docs/ROADMAP.md:80-82`). Why it needs **user** resolution is stated: both horns are closed — the floor cannot be lowered and the requirement cannot be satisfied as written — so proceeding either way would decide on the user's behalf which they meant; and the asymmetry from C-1 is named (in C-1 either side may win; in C-3 the invariant always wins and the question is how to restate the requirement). `requirement_vs_repository_policy` → **option (c), removed**: this repository already resolves that class by **precedence**, not escalation — `reviews/common.md:28-35` and `QUALITY_GATE_DECISION_PRIORITY` (`SKILL.md:1540`) both put explicit requirements above project policy, so the requirement determines the choice and the item is `CLEAR` with nothing for a user to resolve. The citation's scope is stated honestly: that ordering is documented as the *quality-gate* priority, and OS-28 **adopts** it rather than inheriting a rule that already covered decision states. No gap: a policy encoding an earlier user decision is reached by C-2 via the existing `requirement_vs_accepted_decision`. **The closed set shrank; no code was added.** | **A3-1a** (new, with normative definitions of *already-accepted decision* and *non-overridable project invariant*); A3-1 `CONFLICT` row; A4-0 (new paragraph on requirement-vs-policy precedence); A5-2 `CONFLICT` list; A5-4 `CONFLICT` table |
| **RA-5 companion requirement** — audit **every** A5-4 row, not just the two flagged | **DONE, and the audit method itself was changed.** RA-4 and RA-5 were the same defect twice: a fixture filed under a state it did not satisfy — RA-4 on *evidence*, RA-5 on *entry condition*. A5-4 now runs three explicit checks per row (**C1** entry wording, **C2** every evidence field, **C3** invariants) with a column each, and two sources of looseness were removed because they are what let the defects through: three `ASSUMPTION_ALLOWED` rows previously said "other three fields as above" (a row that inherits fields cannot be checked per-field), and the `NEEDS_INPUT` rows previously omitted the entry clause's negative conditions (no determining policy, no authorization), leaving C1 unverifiable. **Result: all 17 live codes pass C1/C2/C3; 0 rows skipped; 2 of 19 drafted removed — one per defect class.** Each row now also names which of A4-0's four `policy_source` artifact kinds it uses, and each `CONFLICT` row maps to exactly one A3-1a clause. | **A5-4** rewritten (method, scope statement, three check columns on all three tables, count block); A6 requirement 3; Testing Strategy step 3b — both now demand a **three-check** liveness assertion over 17 codes, with the note that asserting acceptance alone would have caught RA-4 but **not** RA-5 |

**OQ-1, OQ-3, OQ-4, OQ-5, and OQ-8 remained open** at the end of iteration 4; OQ-9 did not exist yet.

Iteration 3's resolutions, retained for audit:

| finding | resolution | where |
|---|---|---|
| **RA-4** (blocking, G3) — `reversible_local_default` defined as "no policy source" while A3-1 / A4-0 / INV-3 / A5-3 all require a supporting `policy_source`, so the code could produce no valid record | **RESOLVED by REMOVING the code**, not by redefining it and not by relaxing the evidence rule. The burden the brief set was to reconcile a policy-source-less assumption with the ROADMAP's `"a safe, reversible, **policy-supported** assumption is recorded"` — **I could not**: that phrase makes policy support constitutive of the state, not one optional route in. Redefining the code to require a locatable supporting policy would have made it a duplicate of `repository_policy` distinguished only by the absence of a required field. So the code is deleted and **A3-1, A4-0, INV-3, and A5-3 are unchanged** — the RA-2-resolved surfaces stay exactly as the Reviewer confirmed them. No gap is created: a reversible, scope-local choice with *no policy bearing on it at all* does not cross the autonomy boundary and is `CLEAR` under A3-1's first entry condition, which takes no reason code and no record. | A5-2 (code removed + the removal rationale); **A5-4** (new) |
| **RA-4** required action 3 — per-code fixture verification *(counts below **superseded by iteration 4**: RA-5 showed this pass checked evidence but not entry-condition wording, so `CONFLICT` then had 4 codes and the total read 18 of 19. The current figures are **17 live of 19 drafted**, all three-check verified — see A5-4. The original text is kept unrewritten, per the repository's rule against rewriting historical evidence.)* | **New section A5-4**, that iteration's core deliverable. For each of the 19 draft codes it constructs the minimal record satisfying the state's entry condition **and** its required evidence simultaneously. **Result: 18 of 19 are buildable; the one that is not is `reversible_local_default`, now removed.** Two accounting checks fall out: the 10 `NEEDS_INPUT` codes cover 9 of the 11 boundary elements, with *explicit requirement conflict* owned by `CONFLICT`'s 4 codes and *repository/project policy* classified by A4-0 as a boundary **input** rather than a trigger; and `CLEAR` needs no fixture row because it carries no reason code. Two honest caveats are recorded rather than smoothed over — `quality_profile_attribute` requires `profile_status: loaded` (this repository has no `.orca/quality-profile.yaml`, only the two `*.example.yaml` files, verified by `ls -la .orca/`), and `missing_user_intent` maps to `ambiguity` as its **degenerate silence case** so the eleven boundary elements stay closed and no twelfth is invented. | **A5-4** (new); A6 requirement 3 (adds a per-code **liveness** test — a rejection-only suite cannot see a dead code); Testing Strategy step 3b |
| **UD-1** (user decision, was OQ-2) | **RECORDED, not decided by me.** An **optional** decision record section is added to `templates/**` and `reviews/common.md`; its absence is not a contract violation, and the state / reason code / evidence format is validated only when the section is present. | A7 grey-area paragraph; new "Decided by the user" subsection in A8; OQ-2 row marked ✅ with the decision |
| **UD-2** (user decision, was OQ-6) | **RECORDED, not decided by me.** Validation requirement 5 is proven to the **permission** level only, and the limitation — a contract-level test cannot detect a real model's over-escalation — must be stated and never reported as solved. | A6 requirement 5; R-1; A8 subsection + OQ-6 row ✅; Testing Strategy limits (now three, two of them UD-mandated) |
| **UD-3** (user decision, was OQ-7) | **RECORDED, not decided by me.** The missing `schema_version` gate in the existing `evaluate_invocation()` is out of scope; it is a **pre-existing** defect and a follow-up ticket candidate. OS-28 neither fixes nor worsens it and must not claim to. | A6 requirement 9; A8 subsection + OQ-7 row ✅; Testing Strategy limits |

Iteration 2's resolutions, retained for audit:

| finding | resolution | where |
|---|---|---|
| **RA-1** (blocking, G3) — `NEEDS_INPUT`/`CONFLICT → ASSUMPTION_ALLOWED` readable as both absolutely forbidden and conditionally allowed | **RESOLVED by choosing absolute prohibition.** Both edges are now **forbidden unconditionally** — a recorded `user_decision` does *not* enable them; an answered question routes the item to **`CLEAR`**. Rationale given in three ranked reasons, the first being the ROADMAP's own wording (`ASSUMPTION_ALLOWED` records an *assumption*; a decided item is not an assumption, and filing it as one would make OS-32's `silent-assumption defects` metric count answered questions). The ticket's "명시적 권한이 있으면 진행 가능" is satisfied — the work proceeds, as `CLEAR`. Post-answer reclassification is now fixed in four explicit cases (full resolution → `CLEAR`; partial → stays; new decision → new item; reject-listed source → not a `user_decision` at all). All five surfaces aligned. | A3-2 matrix + the new rule paragraph + the reclassification block; T-F2; T-F3; INV-5; A5-3 |
| **RA-2** (blocking, G3) — A4 rows 5-8 allowed an authorization exception that INV-4 forbade absolutely; A3-1 left prior authorization undefined | **RESOLVED by choosing no exception, via the same cut as RA-1.** New **A4-0 truth table**: *nothing lifts INV-4* — neither a determining `policy_source` nor a `user_decision` unlocks `ASSUMPTION_ALLOWED`; they **relocate** the item to `CLEAR`. `policy_source` (boundary input) and `user_decision` (transition evidence) are now separated in their own table with six contrasting rows, including which can satisfy INV-5 (only `user_decision`) and which can lift INV-4 (neither). A prior standing authorization is classified as `user_decision` with `source: prior_explicit_user_authorization`, and it prevents entry to `NEEDS_INPUT` rather than unlocking an assumption. A3-1 entry conditions, the 11-element table, the reason codes ("determines" → "supports"), INV-3, and INV-4 all restated to match. | **A4-0** (new); A3-1 `CLEAR`/`ASSUMPTION_ALLOWED` rows + the determines/supports paragraph; A4-1 rows 5-11; INV-3; INV-4; A5-2; A5-3 |
| **RA-3** (blocking, G5) — the "anchor host impossible by construction / validators exclude such blocks" claim exceeded the evidence | **RESOLVED by withdrawing the claim and re-grounding the recommendation on cost.** New **A1-2a** enumerates every loop-skill assertion from `grep -n 'loop_skill\|loop_text\|orca-worker-reviewer-loop' scripts/validate_skills.py`, read at its lines: six anchor contracts are each asserted absent by **name-specific pattern** (the review cited one at 1831-1838; five more exist at 1209-1214, 1445-1455, 1523-1527, 1593-1597, 1657-1662, 1745-1750), and the agent-profile validator at 1932-1939 runs the **opposite** direction. Verified that **no** validator constrains heading levels; the only objection is a *code comment* at `validate_skills.py:485-488`, quoted. Conclusion stated plainly: **a new shared anchor block is not forbidden.** New **A1-6a** compares the two hosts on parity cost, document-change cost, grammar fit, versioning, and line budget only. The recommendation still favours the JSON block, but now on **grammar fit** — `LIFECYCLE_CONTRACT_LINE_PATTERN` is flat `KEY = token, token` and OS-28's contract is nested (a 4×4 matrix alone flattens to up to 16 keys against budgets of 4/14/17/18/20). | **A1-2a**, **A1-6a** (both new); A1-2 "present in loop skill" row; A1-5; A1-6 candidate table; Summary finding 1; OQ-4; OQ-8 |
| **RA-N1** (non-blocking) — "four of these six are ROADMAP Non-Goals verbatim" was inaccurate | **RESOLVED.** Corrected to **three of five**, with a per-item source table: `worker_reviewer_agreement`, `model_confidence`, `recommended_default` → `docs/ROADMAP.md:298-299` verbatim; `timeout`, `no_response` → ticket requirement only. Commands recorded. *elapsed time* is **removed** — it has no independent source in either document and is a generalization of `timeout`; removing it also repairs an internal mismatch iteration 1 carried (T-F5 listed six entries, the A5-2 reject list five). Both are now the same five. | T-F5 row + the new per-item sourcing table below it; A5-2 reject list unchanged (it was already correct) |

Not touched in iteration 2, per that correction brief: the repository-pattern survey
(A1-1/A1-3/A1-4), the ROADMAP alignment table (A2), A3-3, A4-2, A5-1, A6, A7, OQ-1/2/3/5/6/7, and
R-1…R-7 kept iteration 1's text except where a finding required a specific line to change.

Not touched in **iteration 3**: every RA-1 / RA-2 / RA-3 / RA-N1 surface the Reviewer confirmed as
RESOLVED — the A3-2 matrix and its rule paragraph, T-F2/T-F3/T-F5, INV-4, INV-5, A4-0's truth
tables, A1-2a, A1-6a, and the A3-1 entry conditions — is byte-unchanged. RA-4 was fixable **entirely
by deleting one reason code**, which is why none of those surfaces needed to move. The five open OQs
are unchanged. The Reviewer's own confirmations (repository citations accurate, no ROADMAP conflict,
zero tracked-source diff) were not rebuilt.

Not touched in **iteration 4**: the RA-4 resolution (the `reversible_local_default` removal and its
rationale in A5-2) and the whole UD-1/2/3 record set — A8's "Decided by the user" subsection, the
✅-marked OQ-2/6/7 rows, A6 requirements 5 and 9, R-1, and the UD-mandated Testing Strategy limits —
are unchanged, as are every RA-1/RA-2/RA-3/RA-N1 surface. The A3-2 transition matrix, T-F1…T-F6,
INV-4 and INV-5 are also unchanged: RA-5 lives entirely in `CONFLICT`'s **entry** condition, which is
a different axis from the transition rules. What did change is scoped to five places, all listed in
the RA-5 rows above. **A4-1's element table row for *explicit requirement conflict* was re-read and
needs no edit** — it already routes a policy-vs-requirement disagreement away from `CONFLICT`
("a policy source cannot arbitrate two explicit requirements"), which is consistent with the removal
rather than contradicted by it.

Not touched in **iteration 5**: A3-1a's C-1/C-2/C-3 clauses and their normative definitions, the
`requirement_vs_safety_floor` handling, the whole 17-code C1/C2/C3 audit and its three fixture
tables, and every RA-1…RA-5 and UD-1/2/3 surface. RA-6 is confined to the repository-policy axis, so
the edits are scoped to four places, all listed in the RA-6 rows above, plus the OQ table and its new
impact subsection. Two neighbouring passages were **re-read and confirmed to need no edit**: A4-0's
element-table row for *explicit requirement conflict* is about two requirements, not policy, and
A4-1 row 10 concerns whether a policy source *determines* or *supports* an item that is not in
conflict with a requirement at all — neither carries the generalization RA-6 withdrew.

A final self-check was run over this iteration's edits against the rule the finding names — *every
conclusion must sit inside the range its own citation supports*. The three claims that carry a
citation now read: the four-tier ordering supports only the quality-finding precedence sentence; the
`grep` for an overridability marker supports only "no such marker exists today"; and
`.orca/quality-profile.example.yaml:24-26` supports only "`blocking` is a gate-outcome flag". The
recommendation in OQ-9 is labelled a recommendation and its load-bearing premise is labelled an
assumption I did not test.

STATUS: COMPLETE
