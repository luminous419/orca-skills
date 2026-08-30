# Worker Result

STATUS: COMPLETE

Phase: DESIGN · **Iteration 2 (correction after Reviewer FAIL)** · Run `run_3233a1469e97` · risk `high`
Ticket: **Jira OS-28 "Define Bounded Autonomy Decision Policy Contract"** (P0/High)
Branch: `feat/os-28-bounded-autonomy-policy` (base `main` @ `c264e79`)
Scope of this document: **design only. No repository source file was created or modified.**
The only file this Task wrote is this artifact.

Inputs: `ANALYSIS.md` (gate PASS), `PLAN.md` (gate PASS), `REVIEW_PLAN.md` (PASS, one non-blocking
`RP-N1`), `USER_DECISIONS.md` (**UD-1…UD-4**). Everything ANALYSIS and PLAN settled is carried
forward. Two places where this design *adds* something are marked **[DESIGN ADDITION]** with the
reason; nothing is overturned.

`RP-N1` is closed by this document: it asked that the reason-code cardinality be materialized once
UD-4 landed. **D1 fixes it at 18** and every downstream count in D3/D4/D5 reads that one number.

---

## Summary

The design is complete enough to implement from. Its load-bearing parts:

- **D1** gives the full `decision_policy` JSON, **machine-verified rather than eyeballed**: 18 codes
  (4 / 11 / 3), 11 boundary elements, a complete 4×4 transition matrix with exactly two forbidden
  cells, and **zero row defects** under a structural check that every code's state, entry clause,
  boundary element and required-evidence set actually resolve. Body is **82 lines**; budget set at
  **90**.
- **[DESIGN ADDITION 1] `unclassifiable_decision` needed an entry clause that did not exist.**
  PLAN confirmed OQ-5(c) — a closed set plus this code, forcing `NEEDS_INPUT` — but never gave it a
  clause to satisfy. Checking it against `NEEDS_INPUT`'s entry wording, it fits **neither** existing
  clause. That is exactly the RA-5 defect shape, caught here before it reached a table. D1 adds
  clause **N-3**; the safety argument is in D1-3.
- **[DESIGN ADDITION 2] its evidence shape also differs** — `boundary_element` is unavailable by
  definition, and inventing a twelfth element is forbidden. D1 uses a per-code
  `required_evidence` override instead, plus a mandatory `classification_attempted` field so the
  escape hatch cannot be used lazily.
- **D6's parser safety is measured, not argued.** Every line of the new optional section was run
  against the real `CHOICE_LINE` and `REVIEW_VERDICT_LINE` patterns; all five are inert, and the
  four existing lines those parsers must keep finding still match.
- **UD-4 is honoured exactly**: no `requirement_vs_repository_policy`, C-1/C-2/C-3 untouched, 18
  codes. Its unverified assumption is recorded as an assumption in D9, not as a fact.

No new question needs user authority. One finding worth the Reviewer's attention is recorded in D9.

---

## Analysis

### D1. The contract data — complete `decision_policy` JSON

#### D1-1. The block, ready to paste

Goes inside the existing ` ```policy-contract ` fence in **both** SKILL.md files, as a new top-level
key of that JSON object, after `"errors"`. Byte-identical in both Skills; deep-equality is the guard
(D3-1), not the author.

```json
"decision_policy": {
  "schema_version": 1,
  "state_scope": "per_decision_item_with_derived_check_aggregate",
  "aggregate_order": ["CONFLICT", "NEEDS_INPUT", "ASSUMPTION_ALLOWED", "CLEAR"],
  "states": {
    "CLEAR": {"workflow": "continue", "user_decision_required": false, "reason_code_required": false},
    "ASSUMPTION_ALLOWED": {"workflow": "continue_and_review", "user_decision_required": false, "reason_code_required": true},
    "NEEDS_INPUT": {"workflow": "pause_and_ask", "user_decision_required": true, "reason_code_required": true},
    "CONFLICT": {"workflow": "pause_and_request_resolution", "user_decision_required": true, "reason_code_required": true}
  },
  "transitions": {
    "CLEAR": {"CLEAR": "allowed", "ASSUMPTION_ALLOWED": "allowed", "NEEDS_INPUT": "allowed", "CONFLICT": "allowed"},
    "ASSUMPTION_ALLOWED": {"CLEAR": "requires_retraction", "ASSUMPTION_ALLOWED": "allowed", "NEEDS_INPUT": "allowed", "CONFLICT": "allowed"},
    "NEEDS_INPUT": {"CLEAR": "requires_user_decision", "ASSUMPTION_ALLOWED": "forbidden", "NEEDS_INPUT": "allowed", "CONFLICT": "allowed"},
    "CONFLICT": {"CLEAR": "requires_user_decision", "ASSUMPTION_ALLOWED": "forbidden", "NEEDS_INPUT": "allowed", "CONFLICT": "allowed"}
  },
  "downstream_rule": "an unresolved NEEDS_INPUT or CONFLICT item may not be reported CLEAR by a later phase",
  "entry_clauses": {
    "NEEDS_INPUT": {
      "N-1": "a boundary element is true, is not determined by a policy source, and is not decided by an explicit authorization",
      "N-2": "required user intent is absent",
      "N-3": "the item crosses the autonomy boundary but cannot be classified under these closed vocabularies"
    },
    "CONFLICT": {
      "C-1": "two or more explicit requirements are contradictory",
      "C-2": "an explicit requirement contradicts an already-accepted decision of this run",
      "C-3": "an explicit requirement contradicts a non-overridable project invariant"
    }
  },
  "reason_codes": {
    "repository_policy": {"state": "ASSUMPTION_ALLOWED"},
    "explicit_requirement": {"state": "ASSUMPTION_ALLOWED"},
    "phase_contract": {"state": "ASSUMPTION_ALLOWED"},
    "quality_profile_attribute": {"state": "ASSUMPTION_ALLOWED"},
    "ambiguous_requirement": {"state": "NEEDS_INPUT", "clause": "N-1", "boundary_element": "ambiguity"},
    "missing_user_intent": {"state": "NEEDS_INPUT", "clause": "N-2", "boundary_element": "ambiguity"},
    "irreversible_action": {"state": "NEEDS_INPUT", "clause": "N-1", "boundary_element": "reversibility"},
    "blast_radius_beyond_scope": {"state": "NEEDS_INPUT", "clause": "N-1", "boundary_element": "blast_radius"},
    "monetary_cost": {"state": "NEEDS_INPUT", "clause": "N-1", "boundary_element": "monetary_cost"},
    "security_impact": {"state": "NEEDS_INPUT", "clause": "N-1", "boundary_element": "security"},
    "privacy_impact": {"state": "NEEDS_INPUT", "clause": "N-1", "boundary_element": "privacy"},
    "compliance_impact": {"state": "NEEDS_INPUT", "clause": "N-1", "boundary_element": "compliance"},
    "long_term_lock_in": {"state": "NEEDS_INPUT", "clause": "N-1", "boundary_element": "long_term_lock_in"},
    "authority_reserved_to_user": {"state": "NEEDS_INPUT", "clause": "N-1", "boundary_element": "explicit_user_authority"},
    "unclassifiable_decision": {"state": "NEEDS_INPUT", "clause": "N-3", "required_evidence": ["reason_code", "what_is_missing", "why_policy_cannot_decide", "classification_attempted"]},
    "requirement_contradiction": {"state": "CONFLICT", "clause": "C-1"},
    "requirement_vs_accepted_decision": {"state": "CONFLICT", "clause": "C-2"},
    "requirement_vs_safety_floor": {"state": "CONFLICT", "clause": "C-3"}
  },
  "entry_conditions": {
    "CLEAR": {"any_of": ["no_open_decision_item", "determining_policy_source", "explicit_user_authorization"]},
    "ASSUMPTION_ALLOWED": {"all_of": ["reversible_in_run", "blast_radius_within_scope", "no_high_impact_element", "supporting_policy_source", "no_reserved_user_authority"]},
    "NEEDS_INPUT": {"any_of": ["undetermined_boundary_element", "absent_user_intent", "unclassifiable_item"]},
    "CONFLICT": {"any_of": ["declared_contradiction"]}
  },
  "boundary_elements": {
    "ambiguity": {"kind": "declared", "triggering": true},
    "explicit_requirement_conflict": {"kind": "citations", "minimum": 2},
    "reversibility": {"kind": "enum", "values": ["reversible_in_run", "reversible_with_effort", "irreversible"]},
    "blast_radius": {"kind": "enum", "values": ["current_change", "module", "repository", "external_system"]},
    "monetary_cost": {"kind": "boolean"},
    "security": {"kind": "boolean"},
    "privacy": {"kind": "boolean"},
    "compliance": {"kind": "boolean"},
    "long_term_lock_in": {"kind": "boolean"},
    "repository_project_policy": {"kind": "policy_source"},
    "explicit_user_authority": {"kind": "user_decision"}
  },
  "policy_source_roles": ["determines", "supports"],
  "policy_source_kinds": ["file_path", "requirement_id", "quality_attribute_id", "phase_contract_section"],
  "required_evidence": {
    "CLEAR": [],
    "ASSUMPTION_ALLOWED": ["reason_code", "policy_source", "reversibility", "impact", "retraction_condition"],
    "NEEDS_INPUT": ["reason_code", "boundary_element", "what_is_missing", "why_policy_cannot_decide"],
    "CONFLICT": ["reason_code", "citations", "why_they_cannot_both_hold"]
  },
  "assumption_allowed_requires": {"policy_source_role": "supports", "all_required_evidence_non_empty": true},
  "assumption_allowed_forbidden_when": {
    "reversibility_in": ["irreversible"],
    "blast_radius_in_with_irreversible": ["repository", "external_system"],
    "any_true_of": ["monetary_cost", "security", "privacy", "compliance", "long_term_lock_in"],
    "explicit_user_authority_reserved": true,
    "exception_allowed": false
  },
  "user_decision_fields": ["source", "where_recorded", "resolves"],
  "user_decision_sources": ["explicit_user_reply", "prior_explicit_user_authorization"],
  "forbidden_authority_sources": ["model_confidence", "timeout", "no_response", "worker_reviewer_agreement", "recommended_default"],
  "citation_minimum": {"CONFLICT": 2},
  "independent_axes": ["risk", "quality_profile", "agent_profile"]
}
```

#### D1-2. Where each key comes from, and what it encodes

| key | encodes | source |
|---|---|---|
| `schema_version` | requirement 9's fail-closed target | **its own**, not the block's top-level key. PLAN's reason: the top-level key governs the invocation-time contract, and UD-3 puts that path out of scope; a shared version would make requirement 9 collide with UD-3 |
| `state_scope`, `aggregate_order` | OQ-1(b) as PLAN confirmed it — per decision item, with a derived per-check aggregate | ANALYSIS A3-3; PLAN P7 |
| `states[*].workflow` | the ROADMAP's own action column: Continue / Continue and review / Pause and ask / Pause and request resolution | ANALYSIS A3-1 |
| `transitions` | ANALYSIS A3-2's matrix verbatim. `"forbidden"` appears in exactly two cells — T-F2 and T-F3 | A3-2 |
| `downstream_rule` | T-F6, scoped per decision item | A3-2 |
| `entry_clauses.CONFLICT` | C-1/C-2/C-3 **verbatim in meaning, untouched** per UD-4 | A3-1a |
| `entry_clauses.NEEDS_INPUT` | N-1 and N-2 restate A3-1's existing two clauses. **N-3 is new — see D1-3** | A3-1 + [DESIGN ADDITION 1] |
| `reason_codes` | all 18, each carrying its state, its entry clause where the state has clauses, its boundary element where one applies, and an evidence override where the default does not fit | A5-2 + UD-4 + OQ-5(c) |
| `boundary_elements` | the 11, with the closed value sets A4-1 names | A4-1 |
| `required_evidence` | A5-3's per-state field lists | A5-3 |
| `assumption_allowed_requires` / `..._forbidden_when` | INV-3 and INV-4, including `"exception_allowed": false` — the A4-0 rule that nothing lifts INV-4 | INV-3, INV-4, A4-0 |
| `user_decision_fields`, `user_decision_sources` | INV-5's enforcement. **`user_decision_sources` is the closed POSITIVE vocabulary for user authority** — the two shapes A4-0 identifies. Membership is the gate; an unrecognised source is rejected (FR-2) | INV-5, A4-0 |
| `forbidden_authority_sources` | **retained, but no longer the enforcement.** It names the five excluded categories for documentation; its one remaining machine job is staying disjoint from `user_decision_sources` (C25) | A5-2, FR-2 |
| `independent_axes` | INV-8, as a **declarative** key — never a state-selection input. Checked by positive equality (C11d / R-B), not by a prohibition, so it cannot forbid itself (D3-2) | INV-8 |

**Why `reason_codes` is keyed by code rather than by state.** The first draft used three parallel
maps — codes-by-state, clause-by-code, element-by-code. A structural check found four codes present
in one map and absent from another. Keying by code makes that class of drift unrepresentable: a code
is one object carrying its own state, clause and element. It is also 3 lines shorter. The loader
derives the by-state view in one comprehension.

#### D1-3. [DESIGN ADDITION 1] Clause N-3, and why it is required rather than optional

PLAN confirmed OQ-5(c): a closed set **plus** `unclassifiable_decision`, which itself forces
`NEEDS_INPUT`. PLAN did not work out what entry condition that code satisfies. Checking it against
`NEEDS_INPUT`'s entry wording as ANALYSIS A3-1 states it:

```text
clause 1  "any boundary element is true and is neither determined by policy nor decided
           by an explicit authorization"
              -> does NOT apply. Reason codes map 1:1 onto elements, so if an element is
                 known to be true, a specific code already exists for it. An unclassifiable
                 item is precisely one where no element has been determined.
clause 2  "required intent is simply absent"
              -> does NOT apply. That is `missing_user_intent`'s clause. Reusing it would make
                 the two codes indistinguishable at the entry level, which is the defect
                 RA-5 raised about `requirement_vs_repository_policy`.
```

So the code as PLAN confirmed it **had no entry clause** — the RA-5 shape exactly, caught before it
was drawn into a fixture table. Two ways out, and only one is available:

| option | verdict |
|---|---|
| drop the code, revert to OQ-5(a) | **not available.** PLAN confirmed (c) and UD-4 states the confirmed set is 18. |
| add a third `NEEDS_INPUT` entry clause | **adopted** |

**N-3:** *"the item crosses the autonomy boundary but cannot be classified under these closed
vocabularies."*

Why this is safe to add, stated as the argument a Reviewer should check:

1. **It is monotone toward pausing.** N-3 adds a route **into** `NEEDS_INPUT` only. No item that
   was `NEEDS_INPUT` or `CONFLICT` becomes `CLEAR` or `ASSUMPTION_ALLOWED` because of it. It cannot
   weaken the boundary in any direction; the worst it can do is escalate more (R-1, bounded by the
   mandatory `classification_attempted` evidence below).
2. **It touches nothing UD-4 protects.** `CONFLICT`'s C-1/C-2/C-3 are unchanged. No reason code is
   added or revived beyond the 18 UD-4 fixed. No boundary element is invented.
3. **It is a consequence of an already-confirmed decision, not a new one.** OQ-5(c) is confirmed;
   without N-3 it is simply not implementable. DESIGN is where a confirmed decision's mechanics get
   settled.

#### D1-4. [DESIGN ADDITION 2] The evidence override, and why not a twelfth element

`NEEDS_INPUT`'s default evidence set requires `boundary_element`. `unclassifiable_decision` cannot
supply one — not knowing which element applies is the code's whole meaning. Options:

| option | verdict |
|---|---|
| add an `"unknown"` boundary element value | **rejected.** ANALYSIS's silence note states the element set stays closed at eleven and "no twelfth element is invented". |
| let `boundary_element` be empty for this code | **rejected.** It would make the field optional in practice for every code, since a validator cannot tell a deliberate omission from a lazy one. |
| a per-code `required_evidence` override | **adopted** |

The override drops `boundary_element` and **adds a required `classification_attempted`** — a
non-empty record of the states and codes considered and why each was ruled out. That field is the
anti-laziness device: the escape hatch cannot be reached without showing the work, and the Reviewer
gets something concrete to judge (D7).

Constructibility, checked the way A5-4 checks every other code:

```text
C1  entry     N-3 names exactly this situation                              PASS
C2  evidence  reason_code / what_is_missing ("which treatment the user wants for this item")
              / why_policy_cannot_decide ("no policy source addresses it and no closed-set
              code matches") / classification_attempted (the ruled-out list)  PASS 4/4
C3  invariant INV-3 and INV-4 are ASSUMPTION_ALLOWED-only; INV-5 governs exits, not this
              entry. No violation.                                          PASS
```

#### D1-5. Machine verification of this JSON — what was actually run

The block above was written to a scratch file, parsed, and checked structurally. Command: a Python
snippet that loads the JSON and asserts each property below. Results:

| property | result |
|---|---|
| valid JSON | parses |
| body line count (excluding the outer wrapper braces) | **82** |
| reason codes total / unique | **18 / 18** — `ASSUMPTION_ALLOWED` 4, `NEEDS_INPUT` 11, `CONFLICT` 3 |
| boundary elements | **11** |
| transition matrix complete over all four states | **true** |
| cells marked `forbidden` | exactly **2** — `(NEEDS_INPUT, ASSUMPTION_ALLOWED)` and `(CONFLICT, ASSUMPTION_ALLOWED)` |
| **row defects** — every code's `state` is a real state; every `clause` exists in `entry_clauses` for that state; no `clause` on a state that has no clause set; every `boundary_element` exists; every code whose effective required-evidence includes `boundary_element` actually has one | **NONE** |
| clauses defined vs. clauses used | identical both ways — `N-1/N-2/N-3` and `C-1/C-2/C-3`, no orphans in either direction |

The last two rows are the point. ANALYSIS's recurring failure was "the table was drawn but the row
does not hold"; this check is that failure made mechanical, and it is the same check D4-B turns into
a test.

One structural fact the check surfaced and the validator must encode correctly: **the four
`ASSUMPTION_ALLOWED` codes carry no `clause`, and that is correct.** `ASSUMPTION_ALLOWED`'s entry
condition is a conjunction ("all of…"), not a set of alternatives, so it has no clause set. A naive
validator rule "every code has a clause" would be wrong; D3's check C6 states the right rule.

#### D1-6. Line budget

`DECISION_POLICY_MAX_LINES = 90`, against a measured 82 — about 10% headroom, matching how tightly
the existing `*_MAX_LINES` budgets sit against their blocks. Asserted by D3 check C7.

Honest note on R-4: the ` ```policy-contract ` block in each SKILL.md is currently ~72 lines; this
adds ~82, roughly doubling it. That is a real prompt-budget cost, accepted because the alternative
hosts were compared and rejected in ANALYSIS A1-6a, and because the *explanatory* prose — the bulk
of the text a model reads — goes into `templates/**` and `reviews/**` where it is loaded per phase,
not into SKILL.md (D7).

---

### D2. Loader API — `scripts/decision_policy.py`

#### D2-1. Public surface

```python
class DecisionPolicyError(ValueError):
    """Raised when the decision-policy contract is missing, malformed, or unsupported."""

SUPPORTED_SCHEMA_VERSIONS: tuple[int, ...] = (1,)

@dataclass(frozen=True)
class ReasonCode:
    name: str
    state: str
    clause: str | None
    boundary_element: str | None
    required_evidence: tuple[str, ...]      # the effective set: per-code override, else per-state

@dataclass(frozen=True)
class DecisionPolicy:
    schema_version: int
    states: Mapping[str, StateSpec]                    # workflow / user_decision_required / reason_code_required
    transitions: Mapping[tuple[str, str], str]         # (from, to) -> allowed|forbidden|requires_*
    entry_clauses: Mapping[str, Mapping[str, str]]
    reason_codes: Mapping[str, ReasonCode]             # all 18, keyed by name
    boundary_elements: Mapping[str, BoundaryElement]
    required_evidence: Mapping[str, tuple[str, ...]]
    forbidden_authority_sources: frozenset[str]
    assumption_allowed_forbidden_when: Mapping[str, object]
    max_lines: int

def load_decision_policy(skill_path: Path) -> DecisionPolicy: ...
def codes_for_state(policy: DecisionPolicy, state: str) -> tuple[str, ...]: ...
def transition_rule(policy: DecisionPolicy, source: str, target: str) -> str: ...
def validate_record(policy: DecisionPolicy, record: Mapping[str, object]) -> None: ...
def validate_transition(policy: DecisionPolicy, source: str, target: str,
                        record: Mapping[str, object]) -> None: ...

def permitted_states(policy: DecisionPolicy,
                     facts: Mapping[str, object]) -> frozenset[str]: ...
```

`load_decision_policy` reuses `skill_policy.load_policy_contract()` to get the JSON object rather
than re-parsing the fence — one parser for the block, as the repository already does.

**`permitted_states` — added for RD-2, and it was already missing.** Iteration 1's API had no way to
ask "which states does the contract permit for these declared facts", yet D4-A's requirement 4 says
*"assert `ASSUMPTION_ALLOWED` is **not permitted**"* and requirement 5 says *"assert it **is**
permitted"*. Both needed this function and neither had it; requirement 7's vacuous test was the
symptom the Reviewer caught, not the whole defect.

```text
input   policy  : the loaded contract
        facts   : the DECLARED boundary-element values for one decision item, e.g.
                  {"reversibility": "irreversible", "blast_radius": "repository",
                   "security": false, ..., "policy_source_role": "supports",
                   "explicit_user_authority": "reserved"}
output  the set of states the contract permits for those facts
purpose pure function of (contract, declared facts). No I/O, no dispatch, no phase, no gate,
        no wait. This is contract evaluation, NOT the OS-29 runtime check.
```

**Why risk cannot reach it — structural, not a blacklist.** `permitted_states` iterates
`policy.boundary_elements` and reads only those keys out of `facts`. A key that is not a declared
boundary element is therefore **unreachable by construction**, not merely unused. Since `risk`,
`quality_profile` and `agent_profile` are excluded from `boundary_elements` by rule R-A3 (D3-2),
there is no path by which a `facts["risk"]` value can affect the result. That structural property is
what D4-G asserts and what mutation M-15 attacks.

The signature carries **no `risk` parameter**, and D4-G asserts that too via `inspect.signature`, so
adding one later fails a test rather than passing silently.

#### D2-2. Fail-closed behaviour — exhaustive

Every condition below **raises `DecisionPolicyError`**. None returns `None`, and none returns a
partial object.

| condition | when |
|---|---|
| the `decision_policy` key is absent from the contract | `load_decision_policy` |
| `decision_policy` is not a JSON object | `load_decision_policy` |
| `schema_version` missing, not an `int`, or a `bool` | `load_decision_policy` |
| `schema_version not in SUPPORTED_SCHEMA_VERSIONS` | `load_decision_policy` |
| any required top-level key of `decision_policy` missing, or an unknown key present | `load_decision_policy` |
| a state name outside the four | `load_decision_policy` |
| the transition matrix is not total over the four states | `load_decision_policy` |
| a reason code names an unknown state, an unknown clause, or an unknown boundary element | `load_decision_policy` |
| a code's effective required-evidence names `boundary_element` while the code has none | `load_decision_policy` |
| a record uses a state outside the four | `validate_record` |
| a record in `ASSUMPTION_ALLOWED`/`NEEDS_INPUT`/`CONFLICT` has no `reason_code`, or one outside the closed set | `validate_record` |
| a required evidence field for the record's state (or its per-code override) is missing or empty | `validate_record` |
| an `ASSUMPTION_ALLOWED` record trips any `assumption_allowed_forbidden_when` condition | `validate_record` |
| an `ASSUMPTION_ALLOWED` record's `policy_source.role` is not `"supports"` | `validate_record` |
| a transition marked `forbidden` | `validate_transition` |
| a `requires_user_decision` transition with no `user_decision`, or whose `source` is **not in `user_decision_sources`** — an unknown or alias source is rejected, not merely an explicitly forbidden one (FR-2) | `validate_transition` |
| `user_decision_sources` is empty, or overlaps `forbidden_authority_sources` | `load_decision_policy` |
| a `requires_retraction` transition with no retraction record | `validate_transition` |

**The precedent this follows, and the one it does not (PLAN V3).** `quality_profile.py:521-528` and
`agent_profile.py:462-467` both **raise** on an unsupported version. `skill_policy.load_risk_contract`
returns `None` for a malformed block, and its caller reads `None` as "this Skill has no risk axis" —
a runtime fail-open. This loader must not copy that shape. The IMPLEMENTATION docstring should say
so, so a later reviewer does not "correct" it toward the wrong neighbour. Mutation M-9 is the guard.

#### D2-2a. User authority is an allowlist, not a denylist (FR-2)

Final Adversarial Review found that authority was implemented as an open-ended source string minus
five exact tokens, so `high_confidence`, `worker_reviewer_consensus` and `automated_default` — the
same categories as the listed `model_confidence` and `worker_reviewer_agreement`, differently spelled
— satisfied a `requires_user_decision` transition. Reproduced before changing anything: only the
exact string `model_confidence` was rejected.

**A denylist of spellings cannot enforce a categorical rule.** The categories the ticket names are
"model confidence", "Worker+Reviewer agreement" and "a recommended default"; a list of five
spellings admits every synonym nobody enumerated, and expanding it is chasing an infinite set.

The contract therefore carries a **closed positive vocabulary**:

```text
user_decision_sources = ["explicit_user_reply", "prior_explicit_user_authorization"]
```

These are not invented: they are the only two shapes ANALYSIS A4-0 identifies — an answer to a
structured question put during the run, and a standing authorization carried from the original
request. Enforcement is membership; **an unrecognised source is rejected rather than assumed valid.**

`forbidden_authority_sources` is **retained and demoted**. It no longer enforces anything. Its value
now is documentary — it names the five excluded categories where a reader meets them — plus one
machine job: `load_decision_policy` requires the two sets to be **disjoint**, so a forbidden category
can never be promoted into the allowlist (validator check C25). Enforcement belongs to the positive
vocabulary alone, and the code comment at the check says so.

**Width, checked rather than assumed.** A vocabulary too narrow to express a real user decision would
be the opposite defect — the ticket calls classifying everything `NEEDS_INPUT` a wrong implementation
too. The empirical test is the four decisions this run actually recorded: UD-1 through UD-4 were each
an answer to a structured question the Coordinator put to the repository owner, so each is
`explicit_user_reply` with a locator into `USER_DECISIONS.md`.
`test_the_four_recorded_user_decisions_are_expressible` asserts all four validate, on both the
`NEEDS_INPUT → CLEAR` and `CONFLICT → CLEAR` edges.

**What this does not prove.** Membership in the vocabulary is still a *claim the Worker writes*. This
change makes an unknown or aliased source impossible to pass off as authority; it does not prove a
human actually replied. Establishing that a real user answered is OS-30's protocol and OS-31's
durable record, neither of which is in scope here.

#### D3-3. Every contract key is pinned by value, not by membership (FR-1)

Final Adversarial Review FR-1 found that the transition matrix's *values* were never fixed. C8
compared only the **set** of cells whose value is `forbidden`, and C11c only closed-set membership,
so both Skills' `NEEDS_INPUT → CLEAR` could be relaxed from `requires_user_decision` to the equally
legal `allowed` and the validator stayed green. Reproduced on a disposable `git archive HEAD` copy:
`Skill validation PASSED (626 checks)`, exit 0. That accepts a change which removes two promises at
once — an unresolved `NEEDS_INPUT` or `CONFLICT` cannot continue, and reaching `CLEAR` takes a real
user decision.

This is the same lesson C15-C23 already applied to the state semantics — **membership in a closed set
is not the same as a correct value** — which had simply not been applied to the edges.

**Sweeping the rest of the contract for the same shape found four more keys**, each verified by
mutation rather than by reading. Before this correction, every one of these passed *every* check:

| key | mutation that passed | now pinned by |
|---|---|---|
| `transitions` | `NEEDS_INPUT → CLEAR` and `CONFLICT → CLEAR` relaxed to `allowed` | C26, C26a |
| `boundary_elements` *(payloads, not names)* | `reversibility.values` emptied; `blast_radius` losing `repository`/`external_system` — the two values INV-4's clause names; `explicit_requirement_conflict.minimum` lowered 2 → 1 | C27 |
| `policy_source_roles` | dropping `supports`, the role INV-3 requires | C28 |
| `policy_source_kinds` | widened with a bogus kind | C28 |
| `state_scope` | reversed to `per_check_only`, undoing OQ-1 | C29 |

`independent_axes` was already pinned by C11d's positive equality, and `schema_version` is correctly
checked by *membership* in `SUPPORTED_SCHEMA_VERSIONS` — a supported version legitimately varies.
Those two are not gaps.

**Both halves are demonstrated.** Each mutation makes the validator fail with its named message, and
the unmodified tree stays green — the unmutated control is run in the same sweep so a validator that
merely failed on everything would be visible.

#### D2-2b. `permitted_states` evaluates entry conditions (FR-4), and codes bind their element (FR-3)

Final Adversarial Review attempt 2 raised two blocking findings, both reproduced before any change.

**FR-4 (CRITICAL).** `permitted_states()` fixed its result to `{CLEAR, NEEDS_INPUT, CONFLICT}` and
computed only whether to add `ASSUMPTION_ALLOWED`. So facts declaring an **irreversible,
external-system, security-relevant** item with no policy source and no authorization returned
`['CLEAR', 'CONFLICT', 'NEEDS_INPUT']` — automatic approval of an irreversible high-impact decision
without explicit authority, which the ticket forbids outright. The requirement-4 test asserted only
the **absence of `ASSUMPTION_ALLOWED`**, a narrower property than the one being claimed.

A3-1's entry conditions are now **contract data rather than prose**, transcribed from the approved
wording rather than newly designed:

```text
CLEAR              any_of  no_open_decision_item / determining_policy_source /
                           explicit_user_authorization
ASSUMPTION_ALLOWED all_of  reversible_in_run / blast_radius_within_scope /
                           no_high_impact_element / supporting_policy_source /
                           no_reserved_user_authority
NEEDS_INPUT        any_of  undetermined_boundary_element / absent_user_intent /
                           unclassifiable_item
CONFLICT           any_of  declared_contradiction
```

Predicate names come from a **closed twelve-entry vocabulary** (`ENTRY_PREDICATES`), so a typo fails
at load rather than silently making a condition unsatisfiable. Each boundary element gains a
`triggering` value saying which of its values make it true in A3-1's sense — those values are A4-1's,
not new: `irreversible`; blast radius in `{repository, external_system}`; the five booleans `true`;
authority `reserved`; and `null` for `repository_project_policy`, which A4-0 classifies as a boundary
**input** rather than a trigger.

Verified before and after, same facts:

```text
before:  ['CLEAR', 'CONFLICT', 'NEEDS_INPUT']      <- CLEAR with no authority
after :  ['NEEDS_INPUT']                            <- only the pausing state
after + determining policy source:  ['CLEAR']
after + allowlisted authorization:  ['CLEAR']
after + FORBIDDEN authorization  :  ['NEEDS_INPUT'] <- FR-2's allowlist gates this route too
```

**FR-3.** `validate_record()` checked that the evidence field was non-empty but never that it
**matched the element the reason code binds**, so `security_impact` could be filed with
`boundary_element: privacy`. Misclassification — precisely what a Reviewer is required to be able to
judge — was not machine-checkable, and the liveness test compared the two values in *test* code,
which proves the fixture is self-consistent rather than that production rejects an inconsistent one.
Exact equality is now enforced. `unclassifiable_decision`'s deliberate absence of a bound element is
kept as a **separate positive control** that also rejects smuggling one in.

**The two axes, swept further.** FR-4's shape is *"forbids but never permits"*; FR-3's is *"checks
presence but never consistency"*. Sweeping both found **three more** on the FR-3 axis, each verified
by probe: `reversibility` and `blast_radius` accepted values outside their own declared enums, and
`policy_source.kind` accepted a kind outside the closed set. An unrecognised enum value did not
raise — it matched no triggering value, so `permitted_states` returned an **empty set**: degenerate
rather than fail-closed. Declared values are now checked for membership, while **omitting** an
element stays legal, so the fix does not over-block.

**Over-blocking is the mirror defect, so every negative check has a positive control.** Legitimate
`CLEAR` (nothing open / determining policy / allowlisted authorization), `ASSUMPTION_ALLOWED` (safe,
reversible, supporting policy), and `CONFLICT` (declared contradiction) all remain reachable and are
each asserted.

#### D2-3. What the loader deliberately does not do

No import from `orca_runtime_harness`, `run_logging`, `review_isolation`, `e2e_harness`, or
`task_context`. No dispatch, gate, phase, pause, wait, or question logic. Nothing calls it except
`validate_skills.py` and its own tests — which is what D8 proof 1 checks.

---

### D3. Validator checks in `validate_skills.py`

One new function, `validate_decision_policy_contract(validation)`, registered in `main()` alongside
the other contract validators. It **imports the loader** rather than re-parsing — the same
dependency direction `validate_risk_profile_contract` already has toward
`skill_policy.load_risk_contract`.

| id | check | failure message |
|---|---|---|
| C1 | the block parses in **both** Skills (loader raises → one named failure per Skill) | `<skill>: decision policy contract is missing or malformed` |
| C2 | parsed keys equal `set(DECISION_POLICY_CONTRACT)` | `<skill>: decision policy contract keys drifted` |
| C3 | parsed value equals `DECISION_POLICY_CONTRACT` | `<skill>: decision policy contract values drifted` |
| C4 | the two Skills' `decision_policy` objects are equal *(belt-and-braces alongside the existing whole-dict deep-equality)* | `decision policy contracts differ between skills` |
| C5 | exactly **18** reason codes, and the per-state split is 4 / 11 / 3 | `<skill>: decision policy reason-code cardinality drifted (expected 18)` |
| C6 | every code whose **state has an entry-clause set** names a clause in it; codes for `ASSUMPTION_ALLOWED` (a conjunctive entry condition, no clause set) name none | `<skill>: reason code <name> has no valid entry clause` |
| C7 | `0 < block lines <= DECISION_POLICY_MAX_LINES` | `<skill>: decision policy contract block exceeds 90 lines` |
| C8 | the transition matrix is total, and `(NEEDS_INPUT\|CONFLICT) -> ASSUMPTION_ALLOWED` is `forbidden` | `NEEDS_INPUT/CONFLICT -> ASSUMPTION_ALLOWED must be forbidden` |
| C9 | `assumption_allowed_forbidden_when.exception_allowed` is `false` | `INV-4 must have no exception` |
| C10 | `forbidden_authority_sources` equals the five-entry reject list | `forbidden-authority reject list drifted` |
| C24 | `user_decision_sources` equals the closed positive vocabulary (FR-2) | `<skill>: the user-authority positive vocabulary drifted` |
| C26 | **the full 4×4 transition matrix equals the expected mapping, cell by cell** (FR-1) | `<skill>: the transition matrix drifted -- every cell is pinned by value, not merely by closed-set membership` |
| C26a | `NEEDS_INPUT → CLEAR` and `CONFLICT → CLEAR` each equal `requires_user_decision`, named separately so a failure says which promise broke | `<skill>: <from> -> <to> must require a user decision; found <value>` |
| C27 | each boundary element's `{kind, values, minimum}` payload equals the expected spec — not just the element names | `<skill>: boundary element specifications drifted (kind / enum values / minimum)` |
| C28 | `policy_source_roles` and `policy_source_kinds` equal their expected tuples | `<skill>: policy source roles or kinds drifted` |
| C29 | `state_scope` equals OQ-1's settled value | `<skill>: decision state scope drifted` |
| C30 | `entry_conditions` equals the expected combinator + predicate tuple per state (FR-4) — `permitted_states` evaluates these, so a change here moves the authority boundary | `<skill>: state entry conditions drifted` |
| C27 *(extended)* | each boundary element's `triggering` value is pinned alongside `{kind, values, minimum}` (FR-4) | `<skill>: boundary element specifications drifted` |
| C25 | `user_decision_sources` and `forbidden_authority_sources` are disjoint | `<skill>: the user-authority vocabulary admits a forbidden source` |
| C11a | **partition completeness** — every key of `decision_policy` is in `STATE_SELECTION_INPUTS` or `DECLARATIVE_KEYS`, and their union equals the key set exactly (R-A2) | `decision policy key <name> is not classified as a selection input or declarative` |
| C11b | **no axis token in a selection input** — no key name and no string value inside any `STATE_SELECTION_INPUTS` subtree **exactly equals** a member of `AXIS_TOKENS` (R-A3) | `decision policy references axis token <token> at <path>, which is a state-selection input` |
| C11c | **closed value enumeration** — every transition cell is in `{allowed, forbidden, requires_user_decision, requires_retraction}`; every `states[*].workflow` is in the four workflow values; every `reason_codes[*]` `state`/`clause`/`boundary_element` resolves (R-A4) | `decision policy value <path>=<value> is outside its closed set` |
| C11d | **`independent_axes` positive equality** — equals exactly `["risk", "quality_profile", "agent_profile"]` (R-B) | `independent_axes must name exactly the three canonical axes` |
| C12 | the named prose anchors are present in **both** SKILL.md files | `<skill>: missing decision policy prose anchor <anchor>` |
| C13 | the named prose anchors are present in the shared `templates/*.md` and `reviews/common.md` | `<skill>: missing decision record prose anchor <anchor>` |
| C14 | the UD-1 **optionality sentence** is present (D6-3) | `<skill>: the decision record optionality sentence is missing` |

#### D3-1. How the four drift layers sit together

```text
layer 1  deep-equality        contracts[0][1] == contracts[1][1]   validate_skills.py:1118-1122
                              catches: the two Skills DIVERGE
layer 2  byte-equality        validate_shared_directories()        validate_skills.py:800-822
                              catches: templates/** or reviews/** DIVERGE
layer 3  DECISION_POLICY_      C2 / C3 above
         CONTRACT constant    catches: BOTH Skills changed together  <-- the blind spot
layer 4  prose anchors        C12 / C13 / C14
                              catches: a sentence deleted from BOTH copies  <-- same blind spot
```

Layers 1 and 2 prove the Skills **agree**; they cannot prove the Skills agree on something correct.
Delete a reason code from both blocks and layer 1 still passes. Layers 3 and 4 exist only to close
that, following the `RISK_CONTRACT` / `AGENT_PROFILE_CONTRACT` idiom already in the file. Mutation
M-3 is the dedicated proof that layer 3 earns its place.

#### D3-2. The axis-independence rule set (RD-1 resolution)

Iteration 1's C11 said "no key or string value names a risk level or a profile". Applied literally
it **fails the correct contract**, because `independent_axes` intentionally names all three axes.
Exempting those two strings ad hoc would leave "names a profile" undefined, so two implementers
would build two different detectors. The rule is therefore restated as a **partition plus four
checks**, with one definition that C11a-d, the expected constant, the mutations, and the tests all
read.

**The partition — two module constants in `scripts/decision_policy.py`,** imported by
`validate_skills.py` and by the tests, so there is exactly one definition:

```text
STATE_SELECTION_INPUTS = {          # keys whose CONTENT may participate in selecting or
  "states", "transitions",          # constraining a state
  "entry_clauses", "reason_codes",
  "boundary_elements", "required_evidence",
  "assumption_allowed_requires", "assumption_allowed_forbidden_when",
  "user_decision_fields", "user_decision_sources", "forbidden_authority_sources",
  "entry_conditions",
  "citation_minimum", "downstream_rule", "aggregate_order",
  "policy_source_roles", "policy_source_kinds", "state_scope"
}
DECLARATIVE_KEYS = {"schema_version", "independent_axes"}   # metadata; never consulted to select
AXIS_TOKENS      = {"risk", "quality_profile", "agent_profile", "profile",
                    "low", "medium", "high"}
```

**The four rules:**

```text
R-A2  partition completeness    every decision_policy key is in exactly one of the two sets,
      (check C11a)              and their union EQUALS the key set. An unclassified new key
                                fails. This is what keeps the enumeration from silently
                                becoming incomplete -- the failure mode the correction brief
                                names for option (a).
R-A3  token exclusion           no key name and no string value inside a STATE_SELECTION_INPUTS
      (check C11b)              subtree EXACTLY EQUALS an AXIS_TOKENS member.
R-A4  closed value enumeration  every value in an enumerated position comes from its declared
      (check C11c)              closed set (transition cells, workflows, and each reason code's
                                state / clause / boundary_element resolving).
R-B   declarative equality      independent_axes EQUALS the three canonical names. A positive
      (check C11d)              equality, so the declarative position cannot forbid itself.
```

**Exact-token matching, not substring, and why it is load-bearing.** A substring rule would reject
`quality_attribute_id` (a legitimate `policy_source_kinds` member — A4-0 admits a quality attribute
as an evidence *source*) and `long_term_lock_in` (which contains `lo`, not the token `low`). Citing
a quality attribute as a policy source is an *input the Worker supplies*; the axis being a
*selector the contract branches on* is what requirement 7 forbids. Exact-token matching is what
keeps those two apart mechanically.

**Verified against the real contract** — command: a Python snippet applying the partition and the
token walk to the D1-1 block.

| property | result |
|---|---|
| unclassified keys | **none** |
| keys declared but absent | **none** |
| partition size vs contract key count | **18 == 18** |
| `AXIS_TOKENS` occurrences inside `STATE_SELECTION_INPUTS` | **none** |
| `AXIS_TOKENS` occurrences inside `DECLARATIVE_KEYS` | exactly the three values of `independent_axes` — which R-B permits by equality |

---

### D4. Test design — `scripts/test_decision_policy.py`

#### D4-A. Requirements 1-10, at function level

| req | test function | asserts | fixture |
|---|---|---|---|
| 1 | `test_state_outside_the_four_is_rejected` | `validate_record` raises for a fifth state | `invalid/schema/fifth_state.json` |
| 1 | `test_contract_states_are_exactly_the_four` | parsed state set equals the four | contract |
| 2 | `test_forbidden_transitions_are_rejected` *(subTest per cell)* | `validate_transition` raises for every cell marked `forbidden` | `invalid/transition/*.json` |
| 2 | `test_needs_input_to_assumption_allowed_is_forbidden_even_with_a_user_decision` | the T-F2 cell stays forbidden **with a valid `user_decision` present** — iteration 2's original error | `invalid/transition/tf2_with_user_decision.json` |
| 2 | `test_conflict_to_assumption_allowed_is_forbidden_even_with_a_user_decision` | T-F3, same shape | `invalid/transition/tf3_with_user_decision.json` |
| 2 | `test_needs_input_to_clear_requires_a_user_decision` | T-F1 | `invalid/transition/tf1_no_decision.json` |
| 2 | `test_conflict_to_clear_requires_a_user_decision` | T-F4 | `invalid/transition/tf4_no_decision.json` |
| 2 | `test_assumption_allowed_to_clear_requires_a_retraction` | the `requires_retraction` cell | `invalid/transition/aa_to_clear_no_retraction.json` |
| 2 | `test_unresolved_item_may_not_be_reported_clear_by_a_later_phase` | T-F6 via `downstream_rule` | `invalid/transition/tf6_downstream.json` |
| 3 | `test_reason_code_is_required_for_each_non_clear_state` *(subTest ×3)* | raises when `reason_code` is absent | `invalid/evidence/no_reason_<state>.json` |
| 3 | `test_reason_code_outside_the_closed_set_is_rejected` | raises for an invented code | `invalid/evidence/unknown_code.json` |
| 3 | `test_each_required_evidence_field_is_enforced` *(subTest per state×field)* | raises when any one field is missing or empty | `invalid/evidence/*` |
| 3 | **D4-B liveness suite** | see below | `valid/*` |
| 4 | `test_high_impact_irreversible_cannot_be_assumption_allowed` | `ASSUMPTION_ALLOWED` rejected for `irreversible` + `blast_radius=repository` | `invalid/inv4/irreversible_repository.json` |
| 4 | `test_inv4_is_not_lifted_by_a_determining_policy_source` | same fixture **plus** `policy_source{determines}` — still rejected | `invalid/inv4/..._with_determining_policy.json` |
| 4 | `test_inv4_is_not_lifted_by_a_user_decision` | same fixture **plus** a valid `user_decision` — still rejected | `invalid/inv4/..._with_user_decision.json` |
| 4 | `test_each_high_impact_element_alone_forbids_assumption_allowed` *(subTest ×5)* | monetary / security / privacy / compliance / lock-in each independently | `invalid/inv4/<element>.json` |
| 5 | `test_a_safe_reversible_item_is_permitted_to_be_assumption_allowed` | see D4-C for the UD-2 wording | `valid/repository_policy.json` |
| 5 | `test_the_contract_does_not_require_needs_input_for_a_safe_item` | the same fixture is **not** forced to `NEEDS_INPUT` | `valid/repository_policy.json` |
| 6 | `test_forbidden_authority_source_cannot_justify_a_transition` *(subTest ×5)* | `NEEDS_INPUT → CLEAR` citing each reject-list entry is rejected | `invalid/authority/<source>.json` |
| 6 | `test_forbidden_authority_list_is_exactly_five_entries` | the list has not been quietly trimmed | contract |
| 7 | **five functions — see D4-G**, which replaces iteration 1's single vacuous test | requirement 7 is proven structurally (partition, tokens, closed values) **and** behaviourally (a `permitted_states` result that is provably inert to a risk fact) | contract + `valid/*` + `invalid/inv4/*` |
| 9 | `test_unknown_schema_version_raises` | see D4-D | `invalid/schema/version_99.json` |
| 9 | `test_missing_schema_version_raises` | raises, does not default | `invalid/schema/no_version.json` |
| 9 | `test_malformed_contract_raises_and_does_not_return_none` | **explicitly asserts a raise, not a `None` return** — the R-5 / PLAN-V3 guard | `invalid/schema/malformed.json` |
| 8, 10 | in `test_validate_skills.py` and the S8 command runs — see D3 and D4-E | | |

#### D4-B. The 18-code liveness suite — the RA-4 / RA-5 recurrence guard

```python
def test_every_reason_code_has_a_constructible_record(self):
    """C1/C2/C3 positive assertion for all 18 codes. A rejection-only suite passes
    happily with a dead code in a closed set -- that is how RA-4 (evidence) and RA-5
    (entry clause) each survived a review round."""
    for name, code in policy.reason_codes.items():
        with self.subTest(reason_code=name):
            record = load_fixture(f"valid/{name}.json")
            # C1 entry: the record's clause is the one the contract assigns this code,
            #           and it exists in that state's entry_clauses
            # C2 evidence: every field in code.required_evidence is present and non-empty
            # C3 invariants: validate_record accepts it -- no INV-3/4/5 violation
```

Plus two cardinality guards, so a code cannot be added or a fixture dropped without a failure:

```python
def test_reason_code_count_is_eighteen(self)          # contract vs the literal 18 (UD-4)
def test_every_reason_code_has_exactly_one_fixture(self)   # fixture dir vs contract, both ways
```

`test_every_reason_code_has_exactly_one_fixture` compares in **both** directions — a fixture with no
code fails too, so a stale fixture cannot linger after a code is removed.

#### D4-C. Requirement 5 and UD-2 — the limit stated in three places

The test name says `is_permitted_to_be`, not `is`. Its docstring:

```python
"""UD-2: permission level only. This asserts the contract PERMITS ASSUMPTION_ALLOWED for a
safe, reversible, scope-local item and does not REQUIRE NEEDS_INPUT there. It does NOT and
cannot show that a real model produces that state -- a contract-level test cannot detect a
model's over-escalation. That belongs to OS-32 and is not claimed here."""
```

And the PR text repeats it as a known limit. Three places, per UD-2's "must be stated explicitly and
never reported as solved".

#### D4-D. Requirement 9 and UD-3 — scope fenced in the test itself

```python
"""UD-3: scope is THIS loader only. scripts/skill_policy.py's evaluate_invocation() has no
schema_version gate; that is a PRE-EXISTING defect, out of scope, recorded as a follow-up
candidate. This change neither fixes nor worsens it, and must not be described as addressing
it."""
```

A companion test pins the fence: `test_this_change_does_not_alter_evaluate_invocation_behaviour`
asserts `skill_policy.evaluate_invocation` still accepts a contract carrying an unexpected
**top-level** `schema_version`, i.e. the pre-existing behaviour is unchanged in both directions.

#### D4-F. Anti-vacuity rule for every data-driven loop — the sweep RD-2 demanded

RD-2's defect is one instance of a family: **a test whose assertion never runs, or whose input never
varies, passes green while guarding nothing.** I swept every test in D4-A and D4-B for both shapes.
Result below, stated per test rather than as a blanket claim.

**Shape 1 — "the loop can be empty."** Six tests iterate a collection *derived from the contract*.
Empty that collection and the loop body never executes, so the test passes. All six are listed;
none was assumed safe.

| test | collection it iterates | co-located guard added |
|---|---|---|
| `test_forbidden_transitions_are_rejected` | cells marked `forbidden` | assert the forbidden-cell set **equals** `{(NEEDS_INPUT, ASSUMPTION_ALLOWED), (CONFLICT, ASSUMPTION_ALLOWED)}` before iterating |
| `test_reason_code_is_required_for_each_non_clear_state` | the three non-`CLEAR` states | assert the iteration ran exactly 3 times |
| `test_each_required_evidence_field_is_enforced` | `required_evidence[state]` | assert the per-state field counts are 5 / 4 / 3 before iterating |
| `test_each_high_impact_element_alone_forbids_assumption_allowed` | `assumption_allowed_forbidden_when.any_true_of` | assert that list is exactly the five elements before iterating |
| `test_forbidden_authority_source_cannot_justify_a_transition` | `forbidden_authority_sources` | assert the list is exactly the five entries before iterating |
| `test_alias_and_unknown_sources_are_rejected` (FR-2) | the adversarial source list | assert it is exactly the six entries before iterating |
| `test_genuine_user_evidence_is_accepted` (FR-2) | the `authority_valid/` fixtures | assert exactly 2 fixtures before iterating, and that they prove both vocabulary entries |
| `test_every_reason_code_has_a_constructible_record` (D4-B) | `policy.reason_codes` | assert the count is 18 before iterating |

**Design rule, so this is not six ad-hoc patches:** every data-driven loop asserts its collection's
expected cardinality **inside the same test function**, before the loop. A guard in a *separate*
test can be deleted or skipped independently of the loop it protects; a co-located guard cannot.

**Shape 2 — "the input never varies."** Exactly one test had this, and it is the RD-2 finding:
iteration 1's `test_permitted_states_are_identical_across_risk_levels` iterated risk strings that
reached no function argument. It is replaced by D4-G. **No other test in D4-A or D4-B asserts
sameness across a varying label without varying an actual input** — every other test supplies a
distinct record or contract per case and asserts a distinct outcome.

Two tests deserve a note rather than a change, because their green is honestly narrow:

- `test_contract_states_are_exactly_the_four` and `test_forbidden_authority_list_is_exactly_five_entries`
  compare the contract against a literal. They cannot catch a *coordinated* change that edits the
  contract and the literal together — only the Reviewer reading the diff catches that. Recorded here
  rather than left implied.

#### D4-G. Requirement 7 — the replacement proof (RD-2 resolution)

**Option (a) is adopted**, per the correction brief's recommendation, and completed with the
enumeration-completeness guarantee it asks for. Option (b) is rejected: making the evaluator take a
runtime risk value is exactly the OS-29 wiring both the brief and A7 place out of scope.

But (a) alone would still be a static-only claim, so it is paired with a **behavioural** test that
varies a real input without adding runtime wiring — the missing `permitted_states` function (D2-1)
makes that possible.

| # | test | input that varies | assertion | why it cannot pass vacuously |
|---|---|---|---|---|
| 7.1 | `test_every_contract_key_is_classified_as_input_or_declarative` | — (contract) | R-A2: partition union **equals** the key set | an unclassified key fails; this is what stops the enumeration from silently becoming incomplete |
| 7.2 | `test_no_axis_token_appears_in_a_state_selection_input` | walks every key and string in the input subtrees | R-A3: no exact `AXIS_TOKENS` match | walks the real tree, so a token added anywhere in an input position fails |
| 7.3 | `test_enumerated_positions_use_only_closed_values` | every transition cell, workflow, and code reference | R-A4 | catches a risk-conditional value string that contains no exact token (mutation M-17) |
| 7.4 | `test_permitted_states_signature_has_no_risk_parameter` | — (`inspect.signature`) | no parameter named `risk`/`profile` | adding a risk parameter later fails a test instead of passing silently |
| 7.5 | `test_a_risk_fact_does_not_change_permitted_states` | **the `facts` mapping itself** — four distinct calls: no `risk` key, then `risk="low"`, `"medium"`, `"high"` | all four results are **equal**, *and* each equals the no-`risk`-key baseline | the input genuinely differs across the four calls. The baseline comparison is the anti-vacuity move: it proves risk is **inert**, not merely *consistently consulted*. If `permitted_states` branched on `facts["risk"]` at all, at least one of the four diverges and the test fails |

7.5's structural backing (D2-1): `permitted_states` iterates `policy.boundary_elements` and reads
only those keys, so a non-element key is unreachable. 7.2/7.3 keep `risk` out of
`boundary_elements`. The two halves close on each other.

**What this proves, and what it does not.** It proves the contract has no risk/profile input in any
enumerated or structural position, and that the evaluator's result is inert to a risk fact. It does
**not** prove anything about a future OS-29 runtime that has not been written. Mutations M-15…M-20
(D5) are what demonstrate the checks have teeth; **M-21 records the one shape that is not caught**.

#### D4-E. Validator regression tests in `test_validate_skills.py`

| test | mutation applied to the disposable tree | asserts |
|---|---|---|
| `test_decision_policy_contract_removed_fails` | delete the block from one Skill | named C1 failure |
| `test_decision_policy_key_drift_fails` | add a key to both Skills | named C2 failure |
| `test_decision_policy_value_drift_fails` | change a value in both Skills | named C3 failure |
| `test_decision_policy_single_skill_drift_fails` | change one Skill only | the existing deep-equality message |
| `test_decision_policy_reason_code_count_drift_fails` | delete a code from both | named C5 failure |
| `test_decision_policy_forbidden_transition_relaxed_fails` | flip a `forbidden` cell | named C8 failure |
| `test_decision_record_optionality_sentence_removed_fails` | delete the sentence from both | named C14 failure |
| `test_decision_policy_axis_token_in_a_selection_input_fails` | add `"high"` inside `assumption_allowed_forbidden_when` (mutation M-16 shape) | named **C11b** failure |
| `test_decision_policy_unclassified_key_fails` | add a top-level `risk_overrides` key (M-18) | named **C11a** failure |
| `test_decision_policy_transition_value_outside_closed_set_fails` | set a transition cell to `"requires_user_decision_unless_risk_low"` (M-17) | named **C11c** failure |
| `test_decision_policy_independent_axes_drift_fails` | trim `independent_axes` (M-19) | named **C11d** failure |

**Required in the same commit as the validator import:** add `"decision_policy.py"` to the copy
tuple at `scripts/test_validate_skills.py:49-63`. That tuple's own comment records this exact
failure happening during OS-4 — *"a missing dependency here is an import crash with an empty stdout
rather than the named failure a test is asserting on."* Without it, every validator regression test
fails opaquely.

---

### D5. Fixture design — `scripts/fixtures/decision_policy/`

```text
scripts/fixtures/decision_policy/
  README.md                       what these are, and that they are contract inputs, not run evidence
  valid/                          18 files, one per reason code -- named exactly <reason_code>.json
    repository_policy.json        ASSUMPTION_ALLOWED, policy_source{file_path, supports},
                                  reversibility=reversible_in_run, impact=current_change,
                                  retraction_condition non-empty
    explicit_requirement.json     as above, policy_source kind = requirement_id
    phase_contract.json           as above, kind = phase_contract_section
    quality_profile_attribute.json  as above, kind = quality_attribute_id
                                  + note: requires profile_status "loaded"; the fixture
                                    constructs one (ANALYSIS A5-4's documented precondition)
    ambiguous_requirement.json    NEEDS_INPUT, clause N-1, boundary_element=ambiguity,
                                  what_is_missing, why_policy_cannot_decide, and the explicit
                                  negatives: no determining policy, no authorization
    missing_user_intent.json      NEEDS_INPUT, clause N-2
    irreversible_action.json      \
    blast_radius_beyond_scope.json |
    monetary_cost.json             |  NEEDS_INPUT, clause N-1, one per boundary element,
    security_impact.json           |  each stating the two negative conditions
    privacy_impact.json            |
    compliance_impact.json         |
    long_term_lock_in.json         |
    authority_reserved_to_user.json/
    unclassifiable_decision.json  NEEDS_INPUT, clause N-3, override evidence set:
                                  reason_code / what_is_missing / why_policy_cannot_decide /
                                  classification_attempted (non-empty ruled-out list)
    requirement_contradiction.json      CONFLICT, C-1, 2 citations, why_they_cannot_both_hold
    requirement_vs_accepted_decision.json CONFLICT, C-2, requirement id + a user_decision record
    requirement_vs_safety_floor.json     CONFLICT, C-3, requirement id + RISK_SAFETY_FLOOR
  invalid/
    transition/   tf1_no_decision, tf2_with_user_decision, tf3_with_user_decision,
                  tf4_no_decision, aa_to_clear_no_retraction, tf6_downstream
    evidence/     no_reason_<state> x3, unknown_code, missing_<field> per state/field
    inv4/         irreversible_repository, ..._with_determining_policy, ..._with_user_decision,
                  monetary, security, privacy, compliance, long_term_lock_in
    authority/    model_confidence, timeout, no_response, worker_reviewer_agreement,
                  recommended_default
    schema/       fifth_state, version_99, no_version, malformed
  clear/          absence_is_valid.json -- a Result Contract record with NO decision section,
                  which must validate clean (UD-1's optionality, mutation M-13)
```

Naming rule that makes D4-B's bidirectional cardinality test possible: **every file in `valid/` is
named exactly `<reason_code>.json`.** JSON rather than Python literals so OS-29 and OS-32 can reuse
them without importing this ticket's test module.

#### D5-1. Mutation additions — extends PLAN P5's M-1…M-14

PLAN P5 holds the mutation list; these seven are added by this correction. Six attack requirement 7
directly, and RD-N1 asks for the seventh. **Each was executed against the D1-1 block during this
task, so the CAUGHT/MISSED column is a measurement, not a prediction.**

| id | mutation | expected detector | measured |
|---|---|---|---|
| **M-15** | add `risk` as a twelfth boundary element with values `low/medium/high` | C11b / test 7.2 (exact token as a key inside `boundary_elements`) | **CAUGHT** — `boundary_elements/risk` |
| **M-16** | add `"risk_in": ["high"]` to `assumption_allowed_forbidden_when` — a risk gate on INV-4 | C11b / test 7.2 (exact token as a value) | **CAUGHT** — `assumption_allowed_forbidden_when/risk_in[0]` |
| **M-17** | change a transition cell to `"requires_user_decision_unless_risk_low"` — a risk dependency with **no exact token** | C11c / test 7.3 (closed value enumeration) | **CAUGHT** — and **MISSED by C11b**, which is precisely why R-A4 exists rather than a token blacklist alone |
| **M-18** | add a new top-level `risk_overrides` key | C11a / test 7.1 (partition completeness) | **CAUGHT** — unclassified key |
| **M-19** | trim `independent_axes` to `["risk"]` | C11d / R-B | **CAUGHT** |
| **M-20** | change a `states[*].workflow` to `"continue_if_risk_low"` | C11c / test 7.3 | **CAUGHT** |
| **M-22** *(RD-N1)* | drop `classification_attempted`, or set it empty, in `valid/unclassifiable_decision.json` | D4-B liveness C2 + `test_each_required_evidence_field_is_enforced` | to be run at IMPLEMENTATION; the per-code override makes the field required, so C2 must fail |

**And the mutation that is NOT caught, recorded because leaving it implied is the failure mode this
run keeps hitting:**

| id | mutation | status |
|---|---|---|
| **M-21** | smuggle a risk dependency into a **prose** position — e.g. rewrite entry clause `N-1` to end "…unless the run risk is low" | **MISSED by every static check (C11a-d).** Prose values cannot be closed-enumerated, so R-A4 does not reach them and R-A3 finds no exact token inside a sentence. |

What *does* catch M-21, and what does not:

```text
CAUGHT   when the change is ACCIDENTAL or one-sided -- C3 pins every value against
         DECISION_POLICY_CONTRACT, so any prose edit that is not mirrored in the constant fails,
         and layer 1 deep-equality fails if only one Skill is edited.
NOT      when the change is DELIBERATE and COORDINATED -- the author edits both Skills and the
CAUGHT   Python constant together. Then every static check passes.
MITIGATED only by a human reading the diff. The constant change is visible in it, and the prose
         anchors (C12/C13) make the affected sentences ones a Reviewer is already looking at.
         This is a real residual gap, not a covered case.
```

The same honesty applies to `test_contract_states_are_exactly_the_four` and
`test_forbidden_authority_list_is_exactly_five_entries` (D4-F): a literal compared against the
contract cannot survive a coordinated edit of both.

---

### D6. UD-1 — the optional decision record section

#### D6-1. Exact template text

Appended to the **Result Contract** section of each of the 7 shared templates. Two parts: one line
inside the existing fence, and a short block after it.

Inside the fence, as the last line of the section list:

```text
## Decision Record (optional)
```

Immediately after the fence (the block below is the literal Markdown to insert; its outer
four-backtick fence is this document's quoting only and is **not** part of the text to copy):

````markdown
### Decision Record (optional)

`## Decision Record`는 **optional section이다. 없어도 계약 위반이 아니다.** 이번 phase에서
자동으로 내린 결정이나 사용자 결정이 필요한 항목이 있을 때만 적는다. 적을 때는 SKILL.md의
`decision_policy` 계약이 정한 형식을 따른다.

```text
DECISION_STATE: CLEAR | ASSUMPTION_ALLOWED | NEEDS_INPUT | CONFLICT
REASON_CODE: <closed set; none for CLEAR>
EVIDENCE: fields required by the state
```

- `CLEAR` 외 세 state는 `REASON_CODE` 없이 쓸 수 없다.
- `NEEDS_INPUT` / `CONFLICT`는 진행하지 않고 멈춘다.
- 답변을 받은 항목은 `CLEAR`가 되며 `ASSUMPTION_ALLOWED`가 되지 않는다.
- 모델 확신, Worker/Reviewer 합의, 권고 default, timeout, 무응답은 사용자 권한의 근거가 아니다.
````

Identical in all 7 templates and in both Skills — one edit set, applied by copy (D6-4).

#### D6-2. Parser safety — measured, not argued

PLAN P6-4 established the hazard; this design verifies the **exact** lines it will write. Every line
above was run against the real `CHOICE_LINE` and `REVIEW_VERDICT_LINE` imported from
`scripts/workflow_contract.py`:

| line | `CHOICE_LINE` | `REVIEW_VERDICT_LINE` |
|---|---|---|
| `DECISION_STATE: CLEAR \| ASSUMPTION_ALLOWED \| NEEDS_INPUT \| CONFLICT` | no | no |
| `REASON_CODE: <closed set; none for CLEAR>` | no | no |
| `EVIDENCE: fields required by the state` | no | no |
| `## Decision Record (optional)` | no | no |
| `CLASSIFICATION_ATTEMPTED: states and codes ruled out` | no | no |

And the four lines those parsers **must keep finding**, re-checked as a regression guard:
`STATUS: COMPLETE | BLOCKED` → `CHOICE_LINE` matches; `RESULT: PASS | FAIL` → matches;
`REVIEW_VERDICT: PASS | PASS WITH NOTES | FAIL | BLOCKED` → `REVIEW_VERDICT_LINE` matches;
`Result: PASS | FAIL` → neither (lower-case field, as today).

The four-state line is safe because `REVIEW_VERDICT_LINE`'s value pattern is `[A-Z]+`, which
excludes the underscore in `ASSUMPTION_ALLOWED`. **Standing constraint, unchanged from PLAN: never
write a new two-valued all-caps `FIELD: A | B` line into these shared files.**

#### D6-3. How the validator expresses "optional" — the three rules

```text
DOES check    the OPTIONALITY SENTENCE is present (check C14). The anchor string is
              "optional section이다. 없어도 계약 위반이 아니다."
              -> "optional" cannot be silently deleted, nor upgraded to required.
DOES check    IF a record is present, its state / reason code / evidence match the
              decision_policy contract (validate_record).
DOES NOT      require that any artifact, template output, or Result Contract instance
              CONTAINS the section. Absence is valid and is proven so by
              fixtures/decision_policy/clear/absence_is_valid.json.
```

Mutation M-13 (flip the validator to treat the section as required) must be caught by the
absence-valid fixture test.

#### D6-4. Keeping byte-equality

16 files, **8 distinct contents**. Procedure: edit the `orca-worker-reviewer-orchestration` copy,
then `cp` it verbatim to `orca-worker-reviewer-loop`. Never hand-edit both.
`validate_shared_directories` (`validate_skills.py:800-822`) is the proof, and mutation M-14 (edit
one copy only) is the test that it works.

---

### D7. SKILL.md prose

**Not byte-shared** — the two SKILL.md files legitimately differ, so this text is written into each
and held by anchors checked in both (`SKILL_DIRS` iteration, the pattern
`validate_agent_profile_contract` already uses). Kept short deliberately: the explanatory bulk lives
in `templates/**` and `reviews/**`, which are loaded per phase (R-4, D1-6).

**Placement.** A new `## Decision Policy` section immediately after the `## Machine-Readable Policy
Contract` section in both files, so the prose sits beside the block it explains.

**Content — the four anchored sentences.** These are the ones the machine block only *indexes*, so
each becomes a named prose-anchor constant (checks C12/C13):

| anchor | sentence, in substance |
|---|---|
| `DECISION_STATE_SEPARATION_ANCHOR` | the four decision states are a **separate axis** from `RUN_STATUS_VALUES`, Worker `STATUS`, and `REVIEW_VERDICT`; OS-28 changes none of them, and decision-state `CONFLICT` is not the `PHASE_CONFLICT` error code (OQ-3, R-6) |
| `DECISION_NEEDS_INPUT_VS_CONFLICT_ANCHOR` | `NEEDS_INPUT` is **missing** information; `CONFLICT` is **contradictory** information |
| `DECISION_ANSWER_IS_NOT_AN_ASSUMPTION_ANCHOR` | an answered question yields a **decision**, so the item becomes `CLEAR`, never `ASSUMPTION_ALLOWED` — with or without a `user_decision` (T-F2/T-F3) |
| `DECISION_INV4_NO_EXCEPTION_ANCHOR` | nothing lifts INV-4; a determining policy source or an explicit authorization **relocates** the item to `CLEAR` rather than unlocking `ASSUMPTION_ALLOWED` |

Each anchor exists because deleting the sentence from **both** Skills would otherwise pass every
equality check (D3-1, layer 4). The *why* matters as much as the rule: per ANALYSIS A4-2's reading
of OS-27, a Worker that is blocked without knowing why routes around the block, and the T-F2
downgrade is exactly that failure mode.

**Loop-Skill note.** One extra sentence in `orca-worker-reviewer-loop/SKILL.md` only: this contract
is **risk-independent**, so it reads identically in a Skill with no risk axis — mirroring the
existing `LOOP_AGENT_PROFILE_PROSE_ANCHORS` idiom of stating the loop Skill's own difference.

---

### D8. Proving OS-29 / OS-30 / OS-31 were not built

Four proofs, each a command whose **output** goes into the PR body rather than a claim.

| # | proof | command | expected |
|---|---|---|---|
| 1 | nothing executes the contract | `grep -rn 'decision_policy' scripts/ orca-worker-reviewer-*/` | hits only in `decision_policy.py`, `test_decision_policy.py`, `validate_skills.py`, `test_validate_skills.py`, the two SKILL.md blocks, and the shared prose. **No runtime module.** |
| 2 | no OS-30/31 vocabulary entered | `grep -rniE 'waiting_for_input\|humanapprovalport\|durable pause\|resume from\|orchestration ask\|slack\|approval adapter' <changed files>` | every hit is inside an out-of-scope statement. The PR lists each hit with file and line so a reader can check rather than trust |
| 3 | no lifecycle surface moved | `git diff --stat -- scripts/run_logging.py scripts/orca_runtime_harness.py scripts/review_isolation.py scripts/e2e_harness.py scripts/task_context.py` | **empty** — covers `RUN_STATUS_VALUES` specifically |
| 4 | no gate was wired | `git diff --stat` over the whole tree | the changed set equals PLAN P1's list exactly; no phase-dispatch, task-graph, or gate-evaluation call site |

Proof 1 is the strongest and is worth naming as such: it is the mechanical form of "this ticket
defines what a state means, not when the check runs".

---

### D9. Design findings, risks, and what stays unverified

**No new question requires user authority.** UD-1…UD-4 answer everything this design needed. The two
`[DESIGN ADDITION]` items are mechanics of an already-confirmed decision (OQ-5(c)), not new
authority choices, and D1-3 gives the monotone-toward-pausing argument for why.

| id | finding | disposition |
|---|---|---|
| **F-1** | `unclassifiable_decision` as PLAN confirmed it had **no entry clause and an unsatisfiable evidence set** — the RA-4 and RA-5 defects combined, in a code added *after* ANALYSIS's audit ran. | Fixed here by clause **N-3** and a per-code evidence override (D1-3, D1-4). Worth the Reviewer's attention because it shows the A5-4 audit must be re-run whenever a code is added, which D4-B now automates. |
| **F-2** | A naive validator rule "every reason code names an entry clause" would be **wrong**: the four `ASSUMPTION_ALLOWED` codes correctly have none, since that state's entry condition is conjunctive. | Encoded correctly as check C6. Found by the D1-5 structural run, not by reading. |
| **F-3** *(iteration 2)* | Iteration 1's API had **no `permitted_states` function**, yet D4-A's requirements 4 and 5 both assert that a state *is* or *is not* "permitted". RD-2 surfaced this through requirement 7's vacuous test, but the gap was wider than that one test. | `permitted_states(policy, facts)` added in D2-1. Requirements 4, 5 and 7.5 all bind to it. |
| **F-4** *(iteration 2)* | **Six** data-driven test loops in D4-A/D4-B would pass green if their contract-derived collection were emptied — the same "green but guards nothing" family as RD-2, in a different shape. | D4-F: each loop asserts its expected cardinality **inside the same test function**. Co-located, because a guard in a separate test can be deleted independently of the loop it protects. |
| **F-5** *(iteration 2, honest gap)* | A **deliberate, coordinated** risk dependency smuggled into a **prose** position (an entry-clause sentence, `downstream_rule`) passes every static check once the Python constant is updated to match. | **Not closed.** Recorded as mutation M-21 with exactly what does and does not catch it (D5-1). Mitigation is human review of the diff, aided by the prose anchors. Stated as a residual gap rather than covered. |
| **R-4 (carried)** | The block roughly doubles, ~72 → ~154 lines per SKILL.md. | Accepted; alternatives were compared in ANALYSIS A1-6a. Bounded by `DECISION_POLICY_MAX_LINES = 90` and by putting explanatory prose in per-phase files. |
| **R-1 (carried)** | Over-escalation. N-3 adds a route into `NEEDS_INPUT`. | Bounded by the mandatory `classification_attempted` evidence; real detection is OS-32's unnecessary-question rate, not claimed here. |
| **UD-4's stated limit** | (c) relies on **"the eleven boundary elements catch every policy class that matters"** — an assumption ANALYSIS explicitly did not verify by enumerating policy classes. | Carried forward **as an assumption, not a fact**, and to be repeated in the PR's known-limits section per UD-4's application instruction. This design does nothing to make it more or less true. |
| **UD-2's limit** | Requirement 5 is permission-level only. | Stated in the test name, the docstring, and the PR (D4-C). |
| **UD-3's limit** | The `evaluate_invocation` schema gap is pre-existing. | Fenced by D4-D, including a test that pins the unchanged behaviour. |

---

## Changes

None. This phase produced a design only; no repository source file, Skill, script, test, template,
or document was created or modified.

## Modified Files / Artifacts

| path | change |
|---|---|
| `artifacts/runs/run_3233a1469e97/DESIGN.md` | **updated in place** (this file) — created in iteration 1, corrected in iteration 2 |

No other file was written. Verified by `git status --porcelain` and `git diff --stat` over `scripts`,
both Skills, `docs`, `.orca`, `VERSION` and `CHANGELOG.md` — both returned empty output.

## Validation

Design-time verification actually executed by this task:

| what | command | result |
|---|---|---|
| the D1 JSON parses and is structurally sound | a Python snippet loading the drafted block and asserting each property | **82 body lines; 18/18 unique codes (4/11/3); 11 elements; matrix total; exactly 2 forbidden cells; row defects NONE; clauses defined == clauses used** |
| the D6 section lines are inert to both parsers | a Python snippet importing `CHOICE_LINE` and `REVIEW_VERDICT_LINE` from `scripts/workflow_contract.py` | all 5 new lines match neither; the 4 existing lines still match as before |

Added in iteration 2, for RD-1 and RD-2:

| what | command | result |
|---|---|---|
| the D3-2 partition is complete and exact against the real block | a Python snippet applying `STATE_SELECTION_INPUTS` / `DECLARATIVE_KEYS` to the D1-1 JSON | **18 == 18 keys; no unclassified key; no key declared but absent** |
| no axis token sits in a state-selection input | the same snippet walking every key and string in the input subtrees for exact `AXIS_TOKENS` matches | **zero hits in selection inputs**; the only hits are the three `independent_axes` values, which R-B permits |
| the requirement-7 checks have teeth | six risk-injection mutations (M-15…M-20) applied to a copy of the block and re-checked | **6 / 6 CAUGHT.** M-17 was **missed by the token rule and caught only by R-A4** — the measurement that justifies closed value enumeration |
| what the checks do **not** catch | M-21, a risk dependency written into a prose entry-clause sentence | **MISSED by every static check**, as designed and recorded (F-5, D5-1). Caught by C3 only while the change is accidental or one-sided |

Repository facts re-read at their cited lines during this task:

| claim | how verified |
|---|---|
| deep-equality assertion at `validate_skills.py:1118-1122` | read in the PLAN task and re-cited here unchanged |
| `validate_shared_directories` at `validate_skills.py:800-822` | same |
| fail-closed precedent — `quality_profile.py:521-528`, `agent_profile.py:462-467` both raise | read directly |
| `test_validate_skills.py:49-63` fixed copy tuple, with its OS-4 comment | read directly |
| the 7 shared templates and `reviews/common.md` exist in both Skills | `ls orca-worker-reviewer-loop/templates/ orca-worker-reviewer-loop/reviews/` |
| the Result Contract fence and its current section list | `tail` of `templates/analysis.md` and `templates/implementation.md` |
| `RISK_SAFETY_FLOOR` at `orca-worker-reviewer-orchestration/SKILL.md:923` | carried from ANALYSIS, verified there |

Explicitly **확인하지 않음**:

- **no runtime evidence exists for any designed component** — nothing was implemented, so `N1`,
  `N2` and `N3` have no execution results. The D1 verification is a check of the *design artifact*,
  not of shipped code;
- the baseline suites were **not** re-run in this phase. PLAN measured 501 checks / 1269 tests on an
  unchanged tree and this phase changed no tracked file, so the numbers stand as PLAN recorded them
  rather than being re-claimed as fresh here;
- the Jira OS-28 / OS-29 / OS-30 / OS-31 / OS-32 issue bodies;
- whether any repository policy class exists that trips none of the eleven boundary elements —
  UD-4's stated assumption, carried forward unverified.

## Unit Tests / Testing Strategy

No test was added or modified in this phase — this is DESIGN, and the task brief forbids writing
code. D4 is the test design at function level: per-requirement functions (D4-A), the 18-code
C1/C2/C3 liveness suite with bidirectional fixture-cardinality guards (D4-B), the UD-2 and UD-3
scope fences written into the test docstrings themselves (D4-C, D4-D), and the validator regression
set (D4-E).

The regression baseline for later phases remains PLAN's measurement: **501 validator checks** and
**1269 unittest tests, OK (skipped=6)** — floors, not targets; both counts must rise and neither
suite may fail.

## Review Feedback Resolution

Iteration 2 of DESIGN, correcting the two blocking findings in
`artifacts/runs/run_3233a1469e97/REVIEW_DESIGN.md` (`RESULT: FAIL`). The Reviewer confirmed the
contract data itself — the D1 JSON, 18 codes, transition matrix, entry clauses and evidence — as
aligned and constructible; **none of it was rewritten.** Both findings concern requirement 7's proof
method only, and the diff is confined to D2-1, D3, D4 and D5-1.

```text
FINDING RD-1:  RESOLVED   (blocking; C11 failed the correct contract)
FINDING RD-2:  RESOLVED   (blocking; the requirement-7 dynamic test was vacuous)
FINDING RD-N1: RESOLVED   (non-blocking; classification_attempted mutation added as M-22)
FINDING RP-N1: RESOLVED   (PLAN's only finding; closed in iteration 1)
```

| finding | resolution | where |
|---|---|---|
| **RD-1** (blocking, G3) — C11 would fail the correct contract, because the same JSON deliberately carries `independent_axes: [risk, quality_profile, agent_profile]`; and "names a profile" was undefined | **RESOLVED by replacing one vague prohibition with a partition and four rules that share a single definition.** `STATE_SELECTION_INPUTS` / `DECLARATIVE_KEYS` / `AXIS_TOKENS` live as constants in `scripts/decision_policy.py`, imported by `validate_skills.py` and the tests, so C11a-d, the expected constant, the mutation inputs and the test assertions all read the same source. **R-A2** partition completeness (an unclassified key fails — this is the enumeration-completeness guarantee the brief asks for); **R-A3** exact-token exclusion inside selection inputs; **R-A4** closed value enumeration; **R-B** `independent_axes` positive equality, so the declarative position cannot forbid itself. Exact-token — not substring — matching is load-bearing: it keeps `quality_attribute_id` (a legitimate `policy_source_kinds` member) and `long_term_lock_in` valid. Verified against the real D1-1 block: 18 == 18 keys, no unclassified key, zero axis tokens in any selection input, and the only token occurrences are the three `independent_axes` values R-B permits. | **D3-2** (new); D3 checks C11a-d replacing C11; D1-2's `independent_axes` row; D4-E's four validator regression tests |
| **RD-2** (blocking, G3) — the requirement-7 dynamic test iterated risk strings that reached no function argument, so the input never varied and the assertion passed vacuously | **RESOLVED by option (a), completed, plus a behavioural test that varies a real input.** (b) is rejected: giving the evaluator a runtime risk value is the OS-29 wiring the brief and A7 both exclude. Root cause was wider than the test — iteration 1's API had no `permitted_states` function at all, yet requirements 4 and 5 already assert what is "permitted" (F-3). D2-1 adds `permitted_states(policy, facts)`, a pure function of contract + declared facts with no risk parameter. **Why it cannot pass vacuously:** test 7.5 makes four calls with genuinely different `facts` mappings — no `risk` key, then `low`, `medium`, `high` — and asserts all four are equal **and** equal to the no-`risk`-key baseline. That baseline comparison proves risk is *inert*, not merely *consistently consulted*; if the function branched on `facts["risk"]` at all, at least one call diverges and the test fails. The structural backing is that `permitted_states` iterates `policy.boundary_elements` and reads only those keys, so a non-element key is unreachable **by construction** — and R-A3 keeps `risk` out of `boundary_elements`. Test 7.4 additionally asserts via `inspect.signature` that no risk parameter exists. | **D4-G** (new, 5 tests replacing 1); **D2-1** `permitted_states`; D4-A's requirement-7 row |
| **RD-2 companion** — add a mutation proving the check has teeth | **DONE, and executed rather than predicted.** Six risk-injection mutations were run against the D1-1 block during this task: M-15 (risk as a boundary element), M-16 (risk gate in INV-4), M-17 (risk-conditional transition value with **no exact token**), M-18 (new top-level risk key), M-19 (`independent_axes` trimmed), M-20 (risk-conditional workflow). **All six CAUGHT.** M-17 is the instructive one: it is **missed by the token rule and caught only by R-A4**, which is why the design has closed value enumeration and not a blacklist alone. | **D5-1** (new) |
| **RD-N1** (non-blocking) — verify `classification_attempted` missing/empty fails | **Added as M-22**, bound to the D4-B liveness C2 check and `test_each_required_evidence_field_is_enforced`. To be executed at IMPLEMENTATION, since it needs the loader. | D5-1 |

**Sweep result, as the brief required — swept, not assumed.** Every test in D4-A and D4-B was
examined for both vacuity shapes. **Shape 2 ("input never varies"): exactly one instance, the RD-2
finding itself; no other test asserts sameness across a varying label without varying an input.**
**Shape 1 ("the loop can be empty"): six instances found**, all listed by name in D4-F and each given
a co-located cardinality guard. Two literal-comparison tests are noted there as honestly narrow
rather than silently counted as coverage.

**One residual gap is recorded rather than closed** (F-5 / M-21): a deliberate, coordinated risk
dependency placed in a **prose** value passes every static check once the pinned constant is updated
to match. Prose cannot be closed-enumerated. C3 and layer-1 deep-equality catch the accidental and
one-sided cases; the coordinated case is caught only by a human reading the diff.

| finding | resolution | where |
|---|---|---|
| **RP-N1** (non-blocking) — reason-code cardinality must be materialized once OQ-9 is decided, and the contract constant, fixture count and liveness parameterization must all read the same number | **RESOLVED.** UD-4 fixed the answer at (c) / 18 codes. D1 materializes the set once in the contract; D3 check C5 asserts 18 and the 4/11/3 split; D4-B asserts the count against the contract **and** compares the `valid/` fixture directory to the code list in both directions; D5 names every fixture file `<reason_code>.json` so the comparison is mechanical. One number, four readers, all derived from the contract. | D1-1, D3 C5, D4-B, D5 |

UD-1…UD-4 are applied, not re-litigated: UD-1 in D6, UD-2 in D4-C, UD-3 in D4-D, UD-4 in D1
(18 codes, no `requirement_vs_repository_policy`, C-1/C-2/C-3 untouched) with its unverified
assumption recorded as an assumption in D9.

STATUS: COMPLETE
