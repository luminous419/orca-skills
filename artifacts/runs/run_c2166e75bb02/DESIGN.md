# Worker Result

STATUS: COMPLETE

**Run:** run_c2166e75bb02 · **Phase:** DESIGN · **Iteration:** 4 · **Role:** worker · **Round:** CORRECTION
**Repository:** /Users/luminous/aiAssistedProjects/orca-skills · branch `main` · HEAD `c279005d0c2c743cbb6111b802efd7ff3797ac35` · tracked worktree clean (only untracked `artifacts/` run dirs)
**Ticket:** Jira OS-31 — Implement Durable Pause and Resume for Human Decisions
**Approved baselines read, not modified:** `artifacts/runs/run_c2166e75bb02/ANALYSIS.md` (iteration 2, PASS), `artifacts/runs/run_c2166e75bb02/PLAN.md` (iteration 2, PASS).
**Context read, not modified:** `REVIEW_ANALYSIS.md`, `REVIEW_ANALYSIS_iteration2.md`, `REVIEW_PLAN.md`, `REVIEW_PLAN_iteration2.md`, `REVIEW_DESIGN.md`, `REVIEW_DESIGN_iteration2.md`.
**Correction round, iteration 5 — the final iteration.** Iteration 1 was gated `FAIL` with three
blocking findings. Iteration 2 was gated `FAIL` with **F-003 RESOLVED**; iterations 3 and 4 were
gated `FAIL` with **F-001 and F-003 RESOLVED** and **F-002 alone still blocking**. This document is
updated **in place** and this round is scoped to exactly F-002 as it stood after iteration 4: the
changed sections are §3 (vocabulary), §4.2.1, §4.2.1a, §4.4, §10.2, §12, §13.3 (regressions 1 and
17) and §13.4, plus the Review Feedback Resolution and Decision Record. **F-001's and F-003's
resolutions are kept intact and untouched**, and the rest of the design is deliberately unchanged.

**What iteration 5 fixes.** Iteration 4 persisted terminal provenance before the crash window but
recorded the worktree scope as the literal alias `"current"`. `current`/`active` are re-resolved by
whichever process reads them, so a successor Coordinator bound to another worktree would enumerate
the wrong scope, find zero candidates, and land in `TERMINAL_ORPHAN_POSSIBLE` — fail-closed, but not
closed, and the tests could not detect it because they replayed the same alias. §4.2.1 now resolves
the creation worktree **once, before E1**, to the stable selector `id:<repo-id>::<path>` (from
`orca worktree current`, which the harness already executes during contract validation), journals
that string, hands the identical string to E2's `terminal create` and to leg (4)'s `terminal list`,
and **refuses before E1** rather than falling back to the alias if it cannot be resolved. A further
defaulted harness seam carries the selector to E2, whose `--worktree "current"` is today hard-coded. Because an
unresolvable selector was observed to return `ok:true` with an empty array rather than an error,
§4.2.1a additionally proves the scope with `worktree show` before believing any "absent" verdict.
T-08 and T-47 now create under one `current` binding and recover under a **different** one.

One claim from iteration 3 is **withdrawn as factually wrong** and replaced with a verified
operation. Iteration 3 asserted that "the verified grammar has no terminal-listing verb", and on that
ground left the fresh-process recovery holding only `sha256(handle)` with nothing to match it
against. `orca terminal list` exists in the live 1.4.197 runtime, its grammar was read from `--help`
and its response executed and observed at this HEAD, and it returns both `handle` and `title` for
every live terminal. §4.2.1a therefore closes **W-D** properly: the run-unique `terminal_title` the
`PLANNED` row already wrote supplies the enumerable candidate set, and the journalled
`terminal_digest` — previously unusable — becomes the *verifier* that turns a title match into proof.

Two claims remain **withdrawn** rather than defended, and the design still states the resulting
limitations explicitly: a terminal with no nameable owner is **not** "transferred" by writing an actor
id into a field (§9.2 step 6), and **W-C** — a terminal created before its digest was journalled —
is still **not** recoverable, though for a corrected and narrower reason: enumeration now works, but
with no digest there is no verifier, and this design does not close a session on a label. Both fail
closed.
**Off-limits and untouched:** `artifacts/runs/run_8e8f9451ad44/` and every other pre-existing historical run/artifact.

This phase designs the work. **No production code, test, configuration or SKILL document was changed.**
No branch was created, nothing was staged, nothing was pushed. Every file/line citation below was
verified by me at this HEAD in this phase unless it is explicitly carried from ANALYSIS/PLAN.

PLAN's **D1, D2 (two tiers + C1–C4), D3, OD-1 … OD-4** are binding on this design and are not
re-opened. Where PLAN said "DESIGN fixes the API", this document fixes it.

---

## Summary / Requirements

OS-31 turns a decision block from an absorbing terminal into a **durable, explicitly named,
resumable lifecycle state**. This design specifies, to the level where IMPLEMENTATION is mechanical:

1. a `WAITING_FOR_INPUT` lifecycle state with an exact transition table, expressed in the existing
   `ROUTE_TOKENS` / `TERMINAL_STATUSES` / `RUN_STATUS_VALUES` vocabulary rather than a parallel one;
2. Tier-1 authority — an in-repository `BaseCheckpointSaver` (OD-4) installed by default — and
   Tier-2 index/fence/projection, with the C1–C4 fail-closed rules given named reason codes;
3. the exact field set binding a pending clarification to run / phase / checkpoint / head / artifact
   digest / policy digest, and how staleness is detected;
4. a pause procedure that settles every dispatch and records terminal ownership on all four axes,
   including how a **fresh process** recovers the plaintext terminal handle the four-axis accounting
   needs (§4.2.1a) and which windows it refuses instead;
5. discovery and exactly-once takeover by a brand-new Coordinator, with a run-scoped lease fence;
6. exactly-once response application and resume, with fail-closed duplicate / stale / conflicting
   handling;
7. stale-source revalidation that re-enters the responsible phase through the existing correction
   machinery;
8. gate preservation as a structural property of the re-entry path, not a promise;
9. explicit cancel/abandon with append-only audit and timing evidence;
10. the engine/adapter boundary and its ports;
11. the no-LangGraph degraded behaviour and why it never supersedes checkpoint authority;
12. a file-by-file change map including the `scripts/` ↔ skill `tools/` byte-parity mirror and the
    two fenced SKILL.md contract blocks;
13. a test design that constructs every required regression on `FakeAdapter` with no Orca.

**Requirement → design section map** (Scope `S`, Acceptance `AC`, 핵심 요구사항 `REQ`, 필수 검증 `V`,
as numbered in PLAN's traceability matrix):

| Source | Where this design answers it |
|---|---|
| S1, REQ-2 | §1 (state machine), §2.1 (`run_lifecycle`), §2.3 (record) |
| S2, REQ-3, AC-1, V4 | §4 (pause procedure), **§4.2.1a (fresh-process handle recovery)**, §10.2 (`LifecycleSettlementPort`) |
| S3, REQ-4 | §3 (pending-clarification binding) |
| S4, REQ-5, AC-2, AC-3 | §5 (discovery/takeover), §6 (resume) |
| S5, REQ-6, V2, V3 | §6.3–§6.6 (dedupe, stale, conflict), §2.4 (C1–C4) |
| S6, S7, REQ-8, AC-5 | §7 (stale-source revalidation) |
| S8, REQ-7, V1 | §4.4 (crash windows), **§4.2.1a (leg (4), the fresh-Coordinator recovery of the terminal handle)**, §6.4 (two-stage applied set) |
| S9, REQ-10, AC-7, V6 | §9 (cancel/abandon, audit, timing) |
| AC-4, V5 | §6.5 (replay), §9.4 (cancel replay) |
| AC-6, REQ-9 | §8 (gate preservation), §7.3 (phase-pass currency) |
| AC-8 | §13 (test design) |
| REQ-1 | §2.2 (Tier-1 saver), §2.5 (default wiring), §6.2 (reconstruction) |
| REQ-11 | §10 (engine/adapter boundary) |
| REQ-12, V8 | §11 (no-LangGraph), §13.1 (FakeAdapter vehicle) |
| V7 | §13.6 (Orca 1.4.196 offline contract regression, OD-2) |
| V9 | §12 (parity/packaging), §13.7 |

---

## Validation Baseline (ACTUAL observed output at this HEAD)

`python3 scripts/validate_skills.py`:

```
Skill validation PASSED (732 checks)
Validated both skills, shared templates/reviews, routing, and policy gates.
VALIDATE_SKILLS_EXIT=0
```

`python3 -m unittest discover -s scripts -p 'test_*.py'`:

```
Ran 2014 tests in 342.536s

OK (skipped=6)
UNITTEST_EXIT=0
```

The six skips are the pre-existing opt-in `requires --orca-runtime and a ready Orca runtime` cases.
This phase changed no code, so the counts match PLAN's baseline exactly, as expected.

**Iteration 3 re-observation (ACTUAL, at the same HEAD, after this round's edits):**

```
Skill validation PASSED (732 checks)
Validated both skills, shared templates/reviews, routing, and policy gates.
VALIDATE_SKILLS_EXIT=0
```

```
Ran 2014 tests in 341.113s

OK (skipped=6)
UNITTEST_EXIT=0
```

**Iteration 4 re-observation (ACTUAL, at the same HEAD, after this round's edits):**

```
Skill validation PASSED (732 checks)
Validated both skills, shared templates/reviews, routing, and policy gates.
VALIDATE_SKILLS_EXIT=0
```

```
Ran 2014 tests in 335.726s

OK (skipped=6)
UNITTEST_EXIT=0
```

(This is the run against the **final** state of this file. An earlier run this same iteration,
launched while the edits were still in progress, printed `Ran 2014 tests in 338.042s` /
`OK (skipped=6)` / exit 0 — identical counts, as it must be, since the suite reads no artifact under
`artifacts/` and no code changed in this phase. Both are actual observed output; the final-state run
is the one recorded above.)

Wall-clock run time differs between iterations, as it does; the check count (732) and the test count
(2014) are identical across iterations 1, 2, 3 and 4 and identical to the reviewer's independent
rerun. This phase changed no code, so no other outcome was possible — and the commands were still
run, and the output above is what they actually printed, not what was expected.

**Iteration 4 additionally executed the Orca operation this round depends on**, rather than citing
it. Actual observed output, live 1.4.197:

```
$ orca terminal list --help
Usage: orca terminal list [--worktree <selector>] [--limit <n>] [--include-visual-layouts] [--json]
List live Orca-managed terminals

$ orca terminal list --json          # 18 terminals; per-element fields, abridged
{"ok": true, "result": {"terminals": [
  {"handle": "term_0c42c18d-d2bd-4f73-8075-9fea396364dd",
   "title": "✳ OS-31 Durable Pause Resume Analysis",
   "worktreeId": "...", "worktreePath": "...", "orphaned": false, "connected": true, ...},
  {"handle": "term_2ab95b23-1fc1-4ee4-802f-60ab095d7521", "title": "orca-skills", ...},
  ... ]}}
```

and the §4.2.1a normalisation rule was **run** against those 18 real titles: every decorated title's
leading code point is Unicode category `So` and is stripped with its trailing space; the two
undecorated titles observed (`"orca-skills"`, `"Terminal 5"`) pass through unchanged; normalisation
is idempotent on all 18; and against a synthetic run-unique target, the decorated and undecorated
forms matched while four adversarial near-misses (foreign run with the same intent suffix,
alphanumeric prefix, longer intent id, unrelated title) were all rejected. §4.2.1a records this.

**Iteration 5 re-observation (ACTUAL, at the same HEAD, after this round's edits):**

```
Skill validation PASSED (732 checks)
Validated both skills, shared templates/reviews, routing, and policy gates.
VALIDATE_SKILLS_EXIT=0
```

```
Ran 2014 tests in 335.068s

OK (skipped=6)
UNITTEST_EXIT=0
```

The check count (732) and the test count (2014) are identical across iterations 1, 2, 3, 4 and 5 and
identical to the reviewer's independent reruns. This phase changed no code, so no other outcome was
possible — and the commands were still run, and the output above is what they actually printed.

**Iteration 5 executed the worktree-identity operations this round depends on.** Actual observed
output, live Orca 1.4.197, at this HEAD:

```
$ env | grep ORCA_WORKTREE
ORCA_WORKTREE_ID=7b6ee134-05a2-4dbd-be45-91ea1c2d2d7e::/Users/luminous/aiAssistedProjects/orca-skills

$ orca worktree current --json                      # available BEFORE any effect
{"ok": true, "result": {"worktree": {
   "id": "7b6ee134-05a2-4dbd-be45-91ea1c2d2d7e::/Users/luminous/aiAssistedProjects/orca-skills",
   "repoId": "7b6ee134-05a2-4dbd-be45-91ea1c2d2d7e",
   "path": "/Users/luminous/aiAssistedProjects/orca-skills",
   "head": "c279005d0c2c743cbb6111b802efd7ff3797ac35", "branch": "refs/heads/main", ...}}}

$ orca terminal create --help                       # the selector alphabet, verbatim
  --worktree <selector>  Worktree selector such as identity:<identity>, id:<repo-id>::<path>,
                         name:<displayName>, branch:<branch>, issue:<number>, path:<path>,
                         or active/current

$ orca terminal list --worktree id:7b6ee134-...::/Users/luminous/aiAssistedProjects/orca-skills --json
ok= True count= 20     # every element's worktreeId equal to that same id (1 distinct value)

$ orca terminal list --worktree id:00000000-0000-0000-0000-000000000000::/nope --json
{"ok": true, "result": {"terminals": [], "totalCount": 0, "truncated": false, ...}}
                        # NOT an error -- an unresolvable scope is indistinguishable from an
                        # empty one, which is why 4.2.1a adds the worktree-show guard below

$ orca worktree show --help
Usage: orca worktree show --worktree <selector> [--json]

$ orca worktree show --worktree id:7b6ee134-...::/Users/luminous/aiAssistedProjects/orca-skills --json
ok= True  id= 7b6ee134-05a2-4dbd-be45-91ea1c2d2d7e::/Users/luminous/aiAssistedProjects/orca-skills

$ orca worktree show --worktree id:00000000-0000-0000-0000-000000000000::/nope --json
ok= False  err= selector_not_found
```

Three facts follow, and §4.2.1/§4.2.1a are built on exactly these: the stable `<repo-id>::<path>`
identity is obtainable **before** E1 through a call the harness already makes
(`orca_runtime_harness.py:1754-1759` already reads `current["result"]["worktree"]["id"]`);
`active`/`current` are documented **aliases**, so persisting one persists no worktree; and a listing
under an unresolvable selector returns `ok:true` with an empty array, so "absent" must be proved by
`worktree show` rather than inferred from emptiness.

---

## Current Architecture

Only the facts this design binds to. All verified in this phase.

- **Engine (OS-40), `scripts/deterministic_workflow/`** — `contracts.py` (closed vocabularies,
  identities, bindings), `state.py` (closed `WorkflowState` + `validate_state` + typed
  `UPDATE_COMMANDS`), `routing.py` (pure gates + `route()`), `graph_spec.py` (static topology +
  `validate_graph_spec`), `executor.py` (node callables), `graph.py` (`GuardedWorkflowGraph` façade
  + `build_graph`), `launcher.py` (CLI, exit codes, defaults), `ports.py` (four protocols),
  `runtime_state.py` (durable per-intent ledger: flock + atomic replace + closed record + lease
  fence), `lease_keeper.py`, `fake_adapter.py`, `orca_adapter.py`.
- **Decision block is absorbing.** `routing.py:102` and `:33` return `BLOCK`;
  `ROUTE_TARGETS["BLOCK"] == "TERMINAL"` (`graph_spec.py:17`); `terminal_node`
  (`executor.py:447-476`) stamps `terminal_status="BLOCKED"`; `validate_state` refuses a terminal
  state carrying a pending role/intent/event (`state.py:248-249`, `POST_TERMINAL_EVENT`).
- **No durable run state.** `build_graph(adapter, *, checkpointer=None, ...)` (`graph.py:262`);
  `execute_state` sets `configurable.thread_id` only when a checkpointer was passed
  (`launcher.py:121-122`); `run_cli` passes none (`launcher.py:215`).
  `langgraph-checkpoint==2.1.1` ships `base`, `memory`, `serde` only — verified again in this phase:
  `from langgraph.checkpoint.base import BaseCheckpointSaver` imports and exposes
  `get_tuple / list / put / put_writes / get_next_version / delete_thread / serde` plus the async
  four; `BaseCheckpointSaver.get_next_version` defaults to integer versions;
  `JsonPlusSerializer().dumps_typed({...})` returns `("msgpack", b"...")`.
- **Durable per-intent discipline to copy.** `runtime_state.py` — POSIX `fcntl.flock` sidecar
  critical section with a finite injectable timeout (`:600-660`), atomic `os.replace` write with
  `fsync` (`:665-686`), closed `RECORD_KEYS` validated on every read, `CREATED/RESUMED/ALREADY_SETTLED`
  claim outcomes, lease token as a **fence** on every mutating call, `RuntimeStateCorrupt` rather
  than "read as empty", `RuntimeStateLockUnavailable` at construction on non-POSIX.
- **Four-axis accounting already exists on the Orca side.** `OrcaRuntimeHarness.account_axes`
  (`orca_runtime_harness.py:1607-1690`) is pure with respect to the runtime and returns
  `(settlement, worker_resource, process_liveness, cleanup, role)`;
  `cleanup_authority(role, origin, owned_by_this_dispatch)` (`:482`);
  `WORKER_RESOURCE_OUTCOMES = ("reuse","retain","release","unsupervised")` (`:323`);
  `CLEANUP_AUTHORITY_STATES = frozenset({"authorized","not_authorized","unknown"})` (`:319`);
  `UNSETTLED_WORKER_STATES = frozenset({"outcome_unknown","ready"})` (`:360`); the
  `worker-abandon` → `worker-release` recovery with the explicit "No role promotion here" comment
  (`:3546-3571`).
- **OS-30 has a concrete port implementation already.**
  `clarification_protocol.ArtifactHumanApprovalPort` (`:526`) implements exactly the
  `ports.HumanApprovalPort` shape (`publish` `:708`, `show` `:1275`, `ingest` `:927`) plus
  `promote_pending` (`:662`), `resolved_items` (`:673`), `_current_request` (`:853`),
  `_current_item_ids` (`:864`), `_effective_decision` (`:1244`), `_lineage_state` (`:1168`).
  Closed schemas: `ITEM_INPUT_FIELDS`/`REQUEST_FIELDS` (`:398-407`).
- **Run artifact root and mutable control area.** `_ensure_run_artifact_root`
  (`run_logging.py:337`) is `artifacts/runs/<run_id>/` under `base` (else cwd);
  `.timing_state.json` (`run_logging.py:57`, `RunTimingTracker.state_path` `:840`, `load` `:856`,
  `save` `:886`, `close_all` `:829`) is the existing mutable control file precedent and the only
  place a new process can recover `run_started_at`.
- **Closed run-status tuple.** `RUN_STATUS_VALUES` (`run_logging.py:116`), validated at `:570-588`,
  repeated at `orca_runtime_harness.py:2923-2936`, CLI `choices` at `run_logging.py:3269`.
  The `--event` column has no `choices` by design (`run_logging.py:144`).
- **Two fenced SKILL.md blocks, machine-policed.** `workflow-graph-contract` (SKILL.md line 159) and
  `workflow-control-plane` (line 173), checked by `scripts/validate_workflow_graph_docs.py`
  (route-token ownership, decision-set equality with `GRAPH_OWNED_DECISIONS`, the
  `NON_AUTHORITATIVE` demotion marker on graph-owned sections and its absence from
  `skill_owned_safety` sections), invoked from `validate_skills.py`.
- **Byte parity.** `validate_deterministic_workflow_parity` (`validate_skills.py:3002-3021`)
  compares the `scripts/deterministic_workflow` ↔ `orca-worker-reviewer-orchestration/tools/
  deterministic_workflow` file **sets** then every file's bytes;
  `validate_run_logging_tool_parity` (`:2974-3000`) does the same for `run_logging.py`.
  `release_manifest.required_skill_paths` enumerates the installed engine by `rglob("*.py")`
  (`release_manifest.py:88-95`), so a new engine file is manifest-covered automatically once it is
  mirrored. There is **no** `scripts/run_workflow.py`; the only launcher script is
  `orca-worker-reviewer-orchestration/tools/run_workflow.py`, a thin wrapper over
  `launcher.run_cli`, so new CLI verbs need no change there.
- **Policy source for the digest.** `decision_policy.load_decision_policy(skill_path)` (`:498`) →
  `skill_policy.load_policy_contract` (`:109`), which extracts the single ```policy-contract JSON
  block (SKILL.md line 246) and returns `contract["decision_policy"]`.

---

## Proposed Design

### 1. The `WAITING_FOR_INPUT` lifecycle state machine

#### 1.1 New constants and where each is enforced

| Constant | File | New value(s) | Enforced at |
|---|---|---|---|
| `ROUTE_TOKENS` | `contracts.py:21-25` | `+ "PAUSE", "CANCEL", "ABANDON"` | `routing.assert_route_token`; `state._assert_value_domains` (`state.py:114-115`); `graph_spec.validate_graph_spec` route-target coverage; `validate_workflow_graph_docs` token ownership |
| `RouteToken` `Literal` | `contracts.py:45` | same three | typing only |
| `TERMINAL_STATUSES` | `contracts.py:26` | `+ "CANCELLED", "ABANDONED"` (**not** `WAITING_FOR_INPUT`) | `state._assert_value_domains` (`state.py:116-117`); `workflow-graph-contract` block |
| `RUN_LIFECYCLE_STATES` *(new)* | `contracts.py` | `("ACTIVE", "WAITING_FOR_INPUT", "SETTLED")` | `state._assert_value_domains` + `state._assert_lifecycle_coherence` |
| `PAUSE_CAPABILITIES` *(new)* | `contracts.py` | `frozenset({"human_approval", "lifecycle_settlement"})` | `routing.pause_admissible` |
| `CAPABILITIES` | `contracts.py:38-40` | `+ "lifecycle_settlement"` | `validate_state` capability subset check (`state.py:222`) |
| `NODES` | `graph_spec.py:8` | `+ "PAUSE", "DISPOSE"` | `validate_graph_spec` |
| `STATIC_EDGES` | `graph_spec.py:10-16` | `+ ("PAUSE","TERMINAL"), ("DISPOSE","TERMINAL")` | `validate_graph_spec` |
| `ROUTE_TARGETS` | `graph_spec.py:17-22` | `+ "PAUSE":"PAUSE", "CANCEL":"DISPOSE", "ABANDON":"DISPOSE"` | `validate_graph_spec` (coverage must equal `ROUTE_TOKENS`) |
| `GRAPH_OWNED_DECISIONS` | `graph_spec.py:31-32` | `+ "PAUSE_RESUME"` | `validate_workflow_graph_docs.validate_control_plane` |
| `RUN_STATUS_VALUES` | `run_logging.py:116` | `+ "WAITING_FOR_INPUT", "CANCELLED", "ABANDONED"` | `run_logging.log_run_status` (`:585-587`); `OrcaRuntimeHarness.log_run_status` (`:2930-2934`, which reads the same tuple); CLI `choices` (`run_logging.py:3269`) |
| `EXIT_CODES` | `launcher.py:33` | `+ "WAITING_FOR_INPUT":4, "CANCELLED":5, "ABANDONED":6` | `launcher.summarize` / `run_cli` |

`WAITING_FOR_INPUT` is deliberately **absent** from `TERMINAL_STATUSES`: that is D3/S1. It is present
in `RUN_STATUS_VALUES` because AC-2 requires the append-only run log to say why the run stopped, and
`RUN_STATUS_VALUES` is the log's status vocabulary, not the engine's terminal vocabulary. The two
tuples were already different sets (`ERROR` is in one and not the other), so this is not a new
asymmetry.

#### 1.2 The state variable

`WorkflowState` gains exactly two fields (`state.py:23-40`):

```python
run_lifecycle: str                       # RUN_LIFECYCLE_STATES
pause_binding: dict[str, Any] | None     # the closed pause binding block, §3
```

`initial_state` sets `"run_lifecycle": "ACTIVE"`, `"pause_binding": None`.

**Coherence invariant** (`state._assert_lifecycle_coherence`, called from `_assert_value_domains`),
one place, fail-closed:

```
run_lifecycle == "SETTLED"            <=>  terminal_status is not None
run_lifecycle == "WAITING_FOR_INPUT"  ==>  terminal_status is None
                                      and  pause_binding is not None
                                      and  pending_intent is None
                                      and  pending_event is None
                                      and  intent_status == "NONE"
run_lifecycle == "ACTIVE"             ==>  terminal_status is None
pause_binding is not None             ==>  run_lifecycle != "ACTIVE"
```
Refusal code: `MALFORMED_STATE:lifecycle coherence`. The biconditional makes `run_lifecycle`
derivable from `terminal_status` for every value **except** `WAITING_FOR_INPUT` — that is the point:
the field exists to name the one run state `terminal_status` cannot express, and its overlap with
`terminal_status` elsewhere is a cross-check, not a second authority. A disagreement is a refusal,
never a preference.

`pause_binding` survives a disposition (`SETTLED` with `terminal_status` `CANCELLED`/`ABANDONED`)
so the checkpoint alone still explains what the run was waiting for when it was disposed (AC-2).

#### 1.3 The transition table

Owned by `pause_policy.py` as data, so it is exhaustively testable (T-05):

```python
PAUSE_EVENTS = ("ENTER_PAUSE", "RESUME", "CANCEL", "ABANDON", "TERMINATE")
PAUSE_TRANSITIONS: frozenset[tuple[str, str, str]] = frozenset({
    ("ACTIVE",            "ENTER_PAUSE", "WAITING_FOR_INPUT"),
    ("ACTIVE",            "TERMINATE",   "SETTLED"),
    ("WAITING_FOR_INPUT", "RESUME",      "ACTIVE"),
    ("WAITING_FOR_INPUT", "CANCEL",      "SETTLED"),
    ("WAITING_FOR_INPUT", "ABANDON",     "SETTLED"),
})
def transition(current: str, event: str) -> str:      # raises PauseTransitionRefused
```

Everything not in that set is **forbidden** and refused with `PAUSE_TRANSITION_FORBIDDEN`. The
consequential forbidden edges, named because a reviewer must see them refused:

| Forbidden | Why |
|---|---|
| `SETTLED → *` | a disposed or completed run is never re-adopted (R-14, TT-6) |
| `WAITING_FOR_INPUT → WAITING_FOR_INPUT` | a second pause of an already paused run would mint a second record |
| `ACTIVE → RESUME` | resume without a pause is a forged re-entry |
| `ACTIVE → CANCEL / ABANDON` | OS-31 disposes **paused** runs only; disposing a live run is out of scope (R-11) |
| any `terminal_status` clear | not expressible: no event produces it, and §8.2 closes the raw path |

**Full run lifecycle, against the existing vocabulary:**

```
                       decision_state in (NEEDS_INPUT, CONFLICT)
                       and pause_admissible(state)
   ACTIVE ─────────────────── route "PAUSE" ───────────────► PAUSE node ──► TERMINAL node
     │                                                                        (stamps NO
     │  decision_state in (NEEDS_INPUT, CONFLICT)                              terminal_status;
     │  and NOT pause_admissible(state)                                        sets WAITING_FOR_INPUT)
     ├──────────── route "BLOCK" ──► TERMINAL ──► terminal_status = BLOCKED
     │                                            (the pre-OS-31 behaviour, preserved)
     │
     └── every existing edge unchanged ──► COMPLETED / BLOCKED / ESCALATED

  WAITING_FOR_INPUT ── typed RESUME_PAUSE ──► ACTIVE ──► route → PREPARE_WORKER /
     │                                                   PREPARE_PHASE_REVIEWER /
     │                                                   PREPARE_CORRECTION / PREPARE_REVALIDATION
     ├── typed REQUEST_DISPOSITION(CANCEL)  ──► route "CANCEL"  ──► DISPOSE ──► TERMINAL → CANCELLED
     └── typed REQUEST_DISPOSITION(ABANDON) ──► route "ABANDON" ──► DISPOSE ──► TERMINAL → ABANDONED
```

`TERMINAL` is the **graph exit node**, not "the run is over". It is the only node that writes
`terminal_status`, and for `route_token == "PAUSE"` it writes none. That keeps
`graph_spec.validate_graph_spec` intact and unmodified in shape (every node still reaches
`TERMINAL`; `TERMINAL` still has no outgoing edge) while making pause genuinely non-terminal.

#### 1.4 Routing changes (`routing.py`)

```python
def pause_admissible(state) -> bool:
    """A decision block may become a durable pause only when the adapter can both
    ask the question and settle what is running."""
    return (state["decision_state"] in ("NEEDS_INPUT", "CONFLICT")
            and not missing_capabilities(PAUSE_CAPABILITIES,
                                         frozenset(state["adapter_capabilities"])))
```

`phase_gate` (`routing.py:32-43`) line 33 becomes:

```python
if state["decision_state"] in ("NEEDS_INPUT", "CONFLICT"):
    return "PAUSE" if pause_admissible(state) else "BLOCK"
```

`route` (`routing.py:99-131`), in strict fail-closed order, with the two new checks placed
**above** the decision axis so a disposition can leave a paused run and a paused run re-routes to
itself idempotently:

```python
if state["terminal_status"] is not None: ...            # unchanged (line 101)
if state["pending_disposition"] is not None:            # NEW, before pause
    return "CANCEL" if kind == "CANCEL" else "ABANDON"
if state["run_lifecycle"] == "WAITING_FOR_INPUT":       # NEW: re-entering a paused
    return "PAUSE"                                      # checkpoint changes nothing
if state["decision_state"] in ("NEEDS_INPUT", "CONFLICT"):   # line 102, amended
    return "PAUSE" if pause_admissible(state) else "BLOCK"
...                                                     # everything below unchanged
gate = phase_gate(state)
if gate == "PAUSE": return "PAUSE"                      # NEW
if gate == "BLOCK": return "BLOCK"
```

`pending_disposition` is carried inside `pause_binding` (§3) rather than as a third top-level field,
so `route` reads `(state["pause_binding"] or {}).get("disposition")`. That keeps the closed
`WorkflowState` field count at +2.

**Behaviour preservation, and why the three "deliberate test edits" PLAN scheduled turn out to be
unnecessary.** `pause_admissible` requires `human_approval` **and** `lifecycle_settlement` in
`state["adapter_capabilities"]`. Verified in this phase: `test_deterministic_workflow_graph.py:32`
sets `self.capabilities = BASE_CAPABILITIES` and `test_deterministic_workflow_contracts.py:16` uses
`BASE_CAPABILITIES`; neither optional capability is in `BASE_CAPABILITIES` (`contracts.py:28-33`).
So `test_deterministic_workflow_graph.py:101`, `:133` and `test_deterministic_workflow_contracts.py:45`
keep asserting `BLOCK` **unchanged**, because for those states pause is not admissible. PLAN's R-19
concern — that the old assertion is deleted rather than preserved — is answered by construction: the
old assertions stay, and WU-5 *adds* new admissible-branch tests beside them. This is a design
improvement over PLAN's forecast and is called out here so the Reviewer does not read the missing
edits as an omission.

---

### 2. Durable state design (PLAN D2)

#### 2.1 The two tiers, restated as code facts

| | Tier 1 — authority | Tier 2 — index / fence / projection |
|---|---|---|
| module | `checkpoint_store.py` (LangGraph-dependent by design, OD-4) | `pause_store.py` (LangGraph-free, D1) |
| holds | the whole checkpointed `WorkflowState` per `thread_id` | one record per **run** |
| answers | "what was the run's execution state?" | "which runs are paused, who owns the resume, which checkpoint is it?" |
| read on resume | **yes — the sole input to state reconstruction** | only to *find* the run and to *cross-check* (C3) |
| authoritative fields of its own | all execution state | discovery identity, claim/owner/lease/expiry, checkpoint pointer, disposition, applied set |

#### 2.2 Tier 1 — `scripts/deterministic_workflow/checkpoint_store.py`

```python
CHECKPOINT_STORE_SCHEMA_VERSION = "os31.checkpoint_store.v1"

class CheckpointStoreError(ValueError): ...
class CheckpointStoreCorrupt(CheckpointStoreError): ...        # never "read as empty"
class CheckpointStoreLockUnavailable(CheckpointStoreError): ...  # non-POSIX, at construction
class CheckpointStoreLockTimeout(CheckpointStoreError): ...
class CheckpointThreadRetired(CheckpointStoreError): ...        # a disposed run's thread

class FileCheckpointSaver(BaseCheckpointSaver[int]):
    def __init__(self, path: str | os.PathLike[str], *,
                 serde: SerializerProtocol | None = None,
                 clock: LeaseClockPort | None = None,
                 lock_timeout_seconds: float = 10.0) -> None: ...
    # --- BaseCheckpointSaver surface ---
    def get_tuple(self, config) -> CheckpointTuple | None: ...
    def list(self, config, *, filter=None, before=None, limit=None) -> Iterator[CheckpointTuple]: ...
    def put(self, config, checkpoint, metadata, new_versions) -> RunnableConfig: ...
    def put_writes(self, config, writes, task_id, task_path="") -> None: ...
    def delete_thread(self, thread_id) -> None: ...
    async def aget_tuple(...); async def alist(...); async def aput(...); async def aput_writes(...)
    # get_next_version: INHERITED (integer, monotonic, +1) -- documented, not overridden
    # --- OS-31 additions, used by pause_runtime only ---
    def head(self, thread_id: str, *, checkpoint_ns: str = "") -> str | None: ...
    def checkpoint_digest(self, thread_id, checkpoint_id, *, checkpoint_ns="") -> str: ...
    def retire_thread(self, thread_id: str, *, reason: str) -> None: ...
    def is_retired(self, thread_id: str) -> bool: ...
```

**Storage format** — one JSON document per store file, closed key sets, validated on every read:

```json
{
  "schema_version": "os31.checkpoint_store.v1",
  "threads": {
    "<thread_id>": {
      "retired": false,
      "retired_reason": null,
      "namespaces": {
        "<checkpoint_ns>": {
          "head": "<checkpoint_id>",
          "next_sequence": 3,
          "checkpoints": {
            "<checkpoint_id>": {
              "sequence": 2,
              "parent_checkpoint_id": "<checkpoint_id>|null",
              "checkpoint": {"type": "msgpack", "payload_b64": "..."},
              "metadata":   {"type": "msgpack", "payload_b64": "..."},
              "channel_versions": {"<channel>": 7},
              "written_at": "2026-09-05T00:00:00Z"
            }
          },
          "blobs":  {"<channel>": {"7": {"type": "msgpack", "payload_b64": "..."}}},
          "writes": {"<checkpoint_id>": {"<task_id>": {"0": {"channel": "...", "task_path": "",
                                                             "value": {"type": "...", "payload_b64": "..."}}}}}
        }
      }
    }
  }
}
```

- **Serialization.** `self.serde.dumps_typed(value)` returns `(type_str, bytes)` (verified:
  `("msgpack", b"\x83\xa1a\x01...")`). Bytes are stored base64-encoded so the file stays valid,
  diffable JSON, and are restored with `loads_typed((type, b64decode(payload_b64)))`. The default
  serializer is `langgraph.checkpoint.serde.jsonplus.JsonPlusSerializer`, which is already installed
  by the pinned `langgraph-checkpoint==2.1.1`. **No new pinned dependency** (OD-4).
- **`channel_values` handling mirrors `InMemorySaver`:** `put` pops `channel_values` off the
  checkpoint copy, writes each new version into `blobs`, and `get_tuple` reassembles them from the
  checkpoint's own `channel_versions`. This is the shape LangGraph 0.2.76 expects; deviating from it
  is how a checkpointer silently loses channel state.
- **Head.** `head` is an **explicit pointer** written inside the same critical section as the `put`,
  not `max(checkpoint_ids)`. This is what makes C2 answerable without trusting id ordering.
- **`sequence`** is a per-`(thread, ns)` monotonic integer, so "the head is monotonic across writes"
  is a checkable property (T-42) rather than an assumption about uuid6.
- **`checkpoint_digest`** = `sha256` over `contracts.canonical_bytes({"checkpoint_type","checkpoint_b64",
  "metadata_type","metadata_b64","channel_versions"})` for the named checkpoint. It is what the
  Tier-2 pointer pins, so a rewritten checkpoint file is evident.
- **Retirement.** `retire_thread` sets `retired=true` and a reason; `put` on a retired thread raises
  `CheckpointThreadRetired`; `get_tuple`/`list` keep working, because the checkpoint is audit
  evidence after a disposal and the artifacts are immutable (§10h.4 of ANALYSIS: *retire, never
  delete*). `delete_thread` is implemented because the base class declares it, and is **never called
  by any OS-31 path**; a test asserts cancel/abandon calls `retire_thread` and not `delete_thread`.
- **Concurrency and durability** come from `durable_store.FileCriticalSection` (§2.6): flock on a
  sidecar `<path>.lock`, finite injectable timeout, read-after-lock, `fsync` + `os.replace`.
- **Corruption.** Unknown top-level key, unknown schema version, a thread whose `head` names a
  missing checkpoint, a checkpoint whose `channel_versions` names a missing blob → `CheckpointStoreCorrupt`.
  Never silently treated as an empty store (`runtime_state.py:125-130` is the precedent).

#### 2.3 Tier 2 — `scripts/deterministic_workflow/pause_store.py`

One record per run, stored at the run's mutable-control area beside `.timing_state.json`:

```
<artifact_base>/artifacts/runs/<run_id>/.pause_state.json          # the record
<artifact_base>/artifacts/runs/<run_id>/.pause_state.json.lock     # the flock sidecar
```

Exact closed schema (`PAUSE_RECORD_KEYS`; unknown key, missing key or wrong type → `PauseRecordCorrupt`):

```python
PAUSE_RECORD_SCHEMA_VERSION = "os31.pause_record.v2"   # +ac1_discharged, +residual_terminals
                                                       #  (iter 4: residual entries additionally
                                                       #   carry handle_recovery + candidate_handle;
                                                       #   abandon-path only, so no version bump --
                                                       #   the record's own key set is unchanged)
PAUSE_RECORD_STATUSES = ("WAITING_FOR_INPUT", "RESUMED", "CANCELLED", "ABANDONED")
PAUSE_CLAIM_OUTCOMES = ("CREATED", "RESUMED", "ALREADY_RESUMED",
                        "ALREADY_CANCELLED", "ALREADY_ABANDONED")
```

| field | type | authority | meaning |
|---|---|---|---|
| `schema_version` | str | record | `"os31.pause_record.v2"` |
| `run_id` | str | record | discovery identity; must match the directory it was found in |
| `workflow_id` | str | record | `contracts.WORKFLOW_ID` |
| `pause_record_id` | str | record | `pause_policy.pause_record_id(...)`, §6.1 |
| `status` | str | record | `PAUSE_RECORD_STATUSES` |
| `created_at`, `updated_at` | str | record | ISO-8601 UTC |
| `owner_id` | str | record | `runtime_state.default_owner_id()` shape (`host:pidN`) |
| `lease_token` | str | record | the fence; minted only by `claim` |
| `lease_expires_at`, `last_heartbeat_at` | float | record | lease arithmetic on an injected clock |
| `checkpoint_store_path` | str | record | path of the Tier-1 store, relative to the artifact base when inside it, else absolute |
| `thread_id` | str | record | the checkpoint thread |
| `checkpoint_ns` | str | record | `""` in the shipped graph |
| `checkpoint_id` | str | record | the checkpoint that carries `WAITING_FOR_INPUT` (C1) |
| `checkpoint_digest` | str | record | `FileCheckpointSaver.checkpoint_digest(...)` |
| `disposition` | dict \| None | record | §9.2 closed shape |
| `ac1_discharged` | bool | record | `all(row["terminal_disposition"] in AC1_DISCHARGING_DISPOSITIONS for row in settlement_ledger)` (§3, §4.2.2). `True` for every committed pause by construction; may be `False` only on a disposition record written by abandon (§9.2 step 6). It exists so "AC-1 held for this run" is checkable rather than assumed |
| `residual_terminals` | list[dict] | record | one entry per `residual` row — `terminal_title`, `terminal_digest`, `terminal_role`, `terminal_origin`, `provenance_source`, `task_id`, `dispatch_id`, `last_observation`, plus (iteration 4) `handle_recovery` and `candidate_handle`. Empty for every committed pause, so both new fields exist **only** on a disposition record written by abandon — never in a paused run's record, never in the checkpoint, never in the journal. `candidate_handle` is the plaintext handle leg (4) found (§4.2.1a) and is present only when `handle_recovery == "listing_candidate"`; it is `""` for `"unverified"`, because publishing an address the digest disproves is worse than publishing none. This is what the abandon report and the `run_end` reason enumerate |
| `applied` | dict[str, dict] | record | `resume_bundle_id` → **one** closed bundle entry, §6.4; never per-item |
| `projection` | dict | **projection of the checkpoint** | exactly `pause_policy.project_pause(state)` |

Every field except `projection` is authoritative in the record; `projection` is subordinate and is
documented as such in the module docstring, so no later reader mistakes it for authority (D2).

`projection` closed key set `PAUSE_PROJECTION_KEYS` — produced by one pure function so C3 can never
silently omit a field:

```python
def project_pause(state: Mapping[str, Any]) -> dict[str, Any]:
    """The read-only human/auditor view of a paused run, derived ONLY from the checkpoint."""
    binding = state["pause_binding"] or {}
    return {
        "current_phase":           state["current_phase"],
        "current_phase_index":     state["current_phase_index"],
        "phase_iteration":         state["phase_iterations"][state["current_phase"]],
        "final_review_iteration":  state["final_review_iterations"],
        "round_kind":              state["round_kind"],
        "risk":                    state["risk"],
        "requested_phases":        list(state["requested_phases"]),
        "decision_state":          state["decision_state"],
        "decision_reason_code":    state["decision_reason_code"],
        "pending_clarification_id": state["pending_clarification_id"],
        "responsible_phase":       binding.get("responsible_phase"),
        "request_id":              binding.get("request_id"),
        "decision_item_ids":       list(binding.get("decision_item_ids") or ()),
        "source_ledger_keys":      list(binding.get("source_ledger_keys") or ()),
        "repository_binding":      dict(binding.get("repository_binding") or {}),
        "artifact_binding":        dict(binding.get("artifact_binding") or {}),
        "policy_digest":           binding.get("policy_digest"),
        "binding_generation":      state["binding_generation"],
        "settlement_ledger":       [dict(row) for row in binding.get("settlement_ledger") or ()],
    }
```

C3 is then literally `project_pause(reconstructed) == record["projection"]`, with a per-key diff in
the refusal message. A projected field cannot be forgotten, because the function is the definition.

Port (`ports.py`, new protocol):

```python
@runtime_checkable
class RunPauseStatePort(Protocol):
    """Run-scoped durable pause index, coordination fence and projection.

    Deliberately separate from RuntimeStatePort, which is keyed on intent_id and cannot
    answer a run-scoped question (ANALYSIS M5/P2). It is NEVER the authority for execution
    state; that is the OS-40 checkpoint (PLAN D2).
    """
    def read(self, run_id: str) -> Mapping[str, Any] | None: ...
    def create(self, record: Mapping[str, Any]) -> Mapping[str, Any]: ...
    def claim(self, run_id: str) -> Mapping[str, Any]: ...          # + claim_outcome, lease_token
    def heartbeat(self, run_id: str, lease_token: str) -> Mapping[str, Any]: ...
    def release(self, run_id: str, lease_token: str) -> None: ...
    def observe(self, run_id: str, *, timeout_seconds: float,
                poll_seconds: float) -> Mapping[str, Any] | None: ...
    def update_pointer(self, run_id: str, *, checkpoint_id: str, checkpoint_digest: str,
                       projection: Mapping[str, Any], lease_token: str) -> Mapping[str, Any]: ...
    def record_applied(self, run_id: str, entry: Mapping[str, Any],
                       lease_token: str) -> Mapping[str, Any]:
        """Write the ONE bundle-level applied entry, atomically (§6.4).

        ``entry`` is a whole `APPLIED_ENTRY_KEYS` bundle covering every decision item of
        the request -- never one item. One call, one whole-record write under the flock
        critical section, so there is no partial-application window across items. Refuses
        with PAUSE_LIFECYCLE_INCOHERENT when ``entry["items"]`` is not exactly the record's
        ``decision_item_ids``, and with RESPONSE_CONFLICT when a *different*
        ``resume_bundle_id`` is already RECORDED or RESUMED.
        """
    def mark_resumed(self, run_id: str, lease_token: str) -> Mapping[str, Any]: ...
    def settle_disposition(self, run_id: str, disposition: Mapping[str, Any],
                           lease_token: str) -> Mapping[str, Any]: ...
```

`FilePauseRecordStore` implements it, and `FileSettlementJournal` (§4.2.1) lives beside it in the
same module over the same `durable_store` discipline — a second durable file with the same flock +
`os.replace` guarantees, no LangGraph and no Orca import. Semantics mirror `runtime_state` exactly:

- every mutating call is fenced by `lease_token`; a stale or absent token raises
  `PauseClaimLost` / `PauseClaimRequired` (`PAUSE_CLAIM_LOST`). There is deliberately no
  "no token supplied" branch.
- `claim` outcomes: `CREATED` (no live lease, status `WAITING_FOR_INPUT`), `RESUMED` (our own or a
  lapsed lease re-taken), `ALREADY_RESUMED` / `ALREADY_CANCELLED` / `ALREADY_ABANDONED` (terminal
  record; nothing external is needed — the `ALREADY_SETTLED` analogue ANALYSIS §10f asked for).
  Another owner's live lease → `PauseClaimHeld` (`PAUSE_CLAIM_HELD`).
- `observe` takes an explicit, finite timeout and raises `PauseObservationTimeout` at the deadline.
- corrupt record → `PauseRecordCorrupt`, never read as "no pause".
- non-POSIX host → `PauseStoreLockUnavailable` at construction.

Module-level discovery (not a port method — it is a filesystem sweep, not a per-run operation):

```python
def discover_paused_runs(artifact_base: Path) -> tuple[PausedRunListing, ...]:
    """Every run root under <artifact_base>/artifacts/runs/ that holds a pause record.

    A record that fails closed-schema validation is reported with verdict
    PAUSE_RECORD_CORRUPT rather than skipped: an invisible paused run is worse than a
    visible broken one.
    """
```

#### 2.4 The C1–C4 consistency rules — what is compared, and what happens

Implemented once, in `pause_runtime.validate_pause_consistency(record, saver)`, called before every
resume and before every disposition that needs the checkpoint. Named reason codes live in
`pause_policy.PAUSE_REFUSAL_CODES`.

| # | Rule | Compared | Violation → code | Fail-closed behaviour |
|---|---|---|---|---|
| **C1** | The checkpoint commit is the commit point of a pause. The record is written **after** `graph.invoke` returns and references the committed head. | `saver.get_tuple({"configurable": {thread_id, checkpoint_ns, checkpoint_id}})` is not `None` | `PAUSE_CHECKPOINT_MISSING` | Resume refused. Record stays `WAITING_FOR_INPUT`; discovery reports it **unresumable, needs explicit disposition**. Nothing is reconstructed from `projection`. The claim is released so a disposition can still take it. |
| **C2** | The named checkpoint must be the head of its own thread. | `saver.head(thread_id, checkpoint_ns=ns) == record["checkpoint_id"]` **and** `saver.checkpoint_digest(...) == record["checkpoint_digest"]` | `STALE_CHECKPOINT_HEAD` | Resume refused, **no effect performed**, no log row beyond the refusal event. The run stays resumable once the record is refreshed under the run-scoped claim (`update_pointer`). |
| **C3** | The projection must agree with the checkpoint. | `pause_policy.project_pause(reconstructed_state) == record["projection"]`, field by field | `PAUSE_PROJECTION_DIVERGED` | Refused. The engine **must not** prefer either side, must not repair the checkpoint from the projection, and must not proceed on the checkpoint alone. Requires an explicit human disposition: a re-projection under the claim (`update_pointer`, which recomputes the projection *from the checkpoint*) or cancel/abandon. |
| **C4** | A crash between C1's two writes is repaired **forward, in the direction of authority only**. | a checkpoint store whose head carries `run_lifecycle == "WAITING_FOR_INPUT"` with no pause record | `PAUSE_RECORD_MISSING` (**repairable**) | `pause_runtime.reindex()` re-derives the whole record *from the checkpoint* under a fresh claim, idempotently (every derived field is a pure function of the checkpoint, so a second reindex is a byte-identical no-op). The converse — a record naming no reachable checkpoint — is C1 and is **not** repairable. |

The asymmetry between C1 and C4 is the operational statement of "the checkpoint is the authority",
and is asserted directly by T-45.

#### 2.5 Default wiring — the checkpointer is installed, not merely accepted (WU-2(c))

`graph.build_graph` (`graph.py:262`):

```python
def build_graph(adapter, *, checkpointer=None, runtime_state=None,
                interrupt_before=None, interrupt_after=None, require_durable_checkpointer=True):
    ...
    if require_durable_checkpointer and not _is_durable_checkpointer(checkpointer):
        raise DurableCheckpointerRequired(
            "DURABLE_CHECKPOINTER_REQUIRED: pass checkpointer=FileCheckpointSaver(...). "
            "A production graph that can pause must be able to survive the process.")
```

`_is_durable_checkpointer(x)` is `isinstance(x, BaseCheckpointSaver) and not isinstance(x, InMemorySaver)`.
`require_durable_checkpointer=False` is the **test-only** escape hatch that keeps the 24 existing
`MemorySaver` uses valid; it is refused on the `run_cli` path by construction (`run_cli` never passes
it). This mirrors `IdempotencyPortRequired` (`graph.py:278`, `runtime_state.py:158-164`) exactly:
a required port, refused at build time.

`launcher.execute_state` (`launcher.py:96-146`):

- gains `checkpoint_store_path: str | Path | None = None` and `artifact_base: Path | None = None`;
- when `checkpointer is None`, constructs `FileCheckpointSaver(resolve_checkpoint_path(...))`;
- sets `config["configurable"] = {"thread_id": thread_id or raw_state["thread_id"], "checkpoint_ns": ""}`
  **unconditionally** on the graph path (today `launcher.py:121-122` sets it only when a checkpointer
  was passed).

`resolve_checkpoint_path(run_id, thread_id, *, explicit=None, artifact_base=None)`, in resolution
order — the same shape as `default_runtime_state_path` (`launcher.py:57-59`) but with the run
artifact root as the final default, because D2 requires the store to live in the run's
mutable-control area beside `.timing_state.json`:

1. `explicit` (`--checkpoint-store`);
2. `$ORCA_OS40_CHECKPOINT_DIR/<run_id>__<thread_id>.json` (env override, sanitised exactly like
   `default_runtime_state_path`);
3. `<artifact_base or cwd>/artifacts/runs/<run_id>/.workflow_checkpoints.json`.

`run_cli` gains `--artifact-base` (default `.`) and `--checkpoint-store`, and constructs the saver
by default beside the existing default `FileRuntimeStateStore`, so the shipped command line is
checkpoint-durable with no extra flags — exactly as it is already ledger-durable with no extra flags.

**Blast radius, and how it is paid for (R-21).** Six existing test files call
`launcher.execute_state` with no checkpointer — `test_deterministic_workflow_launcher.py`,
`_recovery.py`, `_ownership.py`, `_malformed.py`, `_round2.py`, `_lease_keeper.py`. Each call site is
updated **inside WU-2(c)** to pass `checkpoint_store_path=<tmp>/checkpoints.json` (or an explicit
`MemorySaver` with `require_durable_checkpointer=False` where the test is specifically about the
non-durable path). This is a mechanical, enumerated edit, not a discovery in WU-14. Without it the
default would write `artifacts/runs/<run_id>/` into the repository working tree during the suite,
which is the concrete failure mode this rule prevents.

#### 2.6 `scripts/deterministic_workflow/durable_store.py` (shared discipline, LangGraph-free)

```python
class DurableStoreError(ValueError): ...
class LockUnavailable(DurableStoreError): ...      # fcntl absent (non-POSIX): refuse at construction
class LockTimeout(DurableStoreError): ...

class FileCriticalSection:
    """lock -> read -> validate -> mutate -> persist -> unlock, on a sidecar lock file.

    A faithful re-implementation of the discipline runtime_state.py proves out
    (runtime_state.py:10-38, :600-686): flock on <path>.lock with a finite injectable
    timeout, a per-thread re-entrant depth counter guarded by an RLock, read AFTER the lock
    is held, and fsync + os.replace on write.
    """
    def __init__(self, path, *, clock=None, lock_timeout_seconds=10.0): ...
    @contextmanager
    def locked(self) -> Iterator[None]: ...

def read_json_document(path, *, schema_version, corrupt_exc) -> dict: ...
def write_json_document(path, payload) -> None: ...    # NamedTemporaryFile + fsync + os.replace
```

**`runtime_state.py` is deliberately NOT refactored onto this module.** Its `_locked`/`_flocked`
pair is entangled with the `LeaseKeeper` background renewal thread and the depth counter that thread
depends on (`runtime_state.py:610-660`); extracting it is a concurrency refactor of the single most
safety-critical existing module, and PLAN asked for the *discipline*, not the code. The cost is ~50
duplicated lines; the benefit is zero regression risk on the lease/keeper tests. A test asserts the
new module has the same observable properties (exclusivity between processes, finite timeout, POSIX
refusal at construction, atomic replace), which is what "one durability story to review" actually
requires.

---

### 3. Pending clarification binding (`pause_binding`)

Written **into the checkpointed `WorkflowState`** by the PAUSE node, and only *projected* into the
Tier-2 record (D2). Closed key set `PAUSE_BINDING_KEYS`, validated by `state._assert_pause_binding`:

| field | type | source | why it is here |
|---|---|---|---|
| `pause_record_id` | str | `pause_policy.pause_record_id(...)` | ties the checkpoint to its index record |
| `paused_at` | str | ISO-8601 UTC | audit |
| `request_id` | str | OS-30 `PublishResult.request_ids[0]` | the question being awaited |
| `decision_item_ids` | list[str] | OS-30 `PublishResult.item_ids`, sorted | the items being awaited |
| `source_ledger_keys` | list[str] | OS-29 `ClarificationSource.source_ledger_keys` | binds back to the ledger records that blocked |
| `responsible_phase` | str | §7.2 | which phase re-runs when the source moved |
| `repository_binding` | dict | `contracts.binding_snapshot(...)["repository"]` — `{head_sha, tree_digest, dirty}` | head staleness (F5) |
| `artifact_binding` | dict | `...["artifact"]` — `{artifact_root_id, relative_path, digest, evidence_ids}` | artifact staleness (F5) |
| `policy_digest` | str | `pause_policy.policy_digest(SKILL.md)` | a `SKILL.md` edit silently changes the policy in force (FI-7) |
| `settlement_ledger` | list[dict] | §4.2, **projected from the durable journal of §4.2.1** | the four-axis outcomes and terminal dispositions, carried for the reader; the journal, not this copy, is the storage |
| `disposition` | dict \| None | §9.2 | the typed cancel/abandon request, read by `route()` |

`pending_clarification_id` (`state.py:33`, `:63`, `:103`, `:291`) stops being a reserved slot: the
PAUSE node sets it to `request_id`, and §6 reads it. This closes FI-3.

**`policy_digest`** is defined precisely as:

```python
def policy_digest(skill_path: Path) -> str:
    contract = skill_policy.load_policy_contract(skill_path)      # the ```policy-contract block
    return hashlib.sha256(contracts.canonical_bytes(contract["decision_policy"])).hexdigest()
```

That is exactly the sub-object `decision_policy.load_decision_policy` parses (`decision_policy.py:501-506`),
so the digest changes if and only if the policy actually in force changes. Prose edits elsewhere in
`SKILL.md` do not spuriously invalidate a pending decision.

**`settlement_ledger` row**, closed key set `SETTLEMENT_ROW_KEYS`:

```python
{"intent_id", "task_id", "dispatch_id",
 "terminal_title", "terminal_digest", "terminal_role", "terminal_origin",
 "terminal_owner", "provenance_source", "handle_recovery",
 "settlement", "worker_resource", "process_liveness", "cleanup_authority",
 "terminal_disposition", "recovery", "accounted_at"}
```

with runtime-neutral closed vocabularies declared in `pause_policy.py`:

```python
SETTLEMENT_OUTCOMES   = ("settled", "recovered", "not_settled")
WORKER_RESOURCE_OUTCOMES = ("reuse", "retain", "release", "unsupervised")
PROCESS_LIVENESS_STATES  = ("live", "already exited", "disputed")
CLEANUP_AUTHORITY_STATES = ("authorized", "not_authorized", "unknown")
TERMINAL_DISPOSITIONS    = ("released", "exited",                  # §4.2.2, OS-31-owned
                            "retained_by_named_owner", "residual")
AC1_DISCHARGING_DISPOSITIONS = frozenset({"released", "exited", "retained_by_named_owner"})
PROVENANCE_SOURCES       = ("journal", "absent")                   # §4.2.1, OS-31-owned
HANDLE_RECOVERY_OUTCOMES = ("in_process",          # the handle never left this process's memory
                            "listing_verified",    # §4.2.1a: enumerated, title-matched, digest-PROVED
                            "listing_candidate",   # §4.2.1a: title-matched only -- NOT proof, never acted on
                            "not_listed",          # §4.2.1a: the scoped listing was read and holds no match
                            "unverified",          # §4.2.1a: a match the digest contradicts, or an ambiguity
                            "scope_unresolved",    # §4.2.1a: the recorded worktree selector no longer resolves
                            "not_attempted")       # stage < OPENED, so no terminal was ever requested
```

`TERMINAL_DISPOSITIONS` and `PROVENANCE_SOURCES` are the two vocabularies here with no harness
counterpart: they are OS-31's own exit conditions, not re-exports, so the contract test asserts
equality only for the other three. `residual` is a member of the closed set and **not** a member of
`AC1_DISCHARGING_DISPOSITIONS`: a row can be recorded as un-discharged, and that is a different thing
from being discharged. There is deliberately no `transferred` member — see §4.2.2.

The three re-exported vocabularies are byte-equal to `orca_runtime_harness.py:323`, `:319` and the branches at
`:1629-1650`; a contract test asserts the engine's copies equal the harness's, because the engine
must not import the harness (D1) and two silently drifting vocabularies is exactly the defect
`validate_workflow_graph_docs` exists to prevent for routing.

`terminal_owner`, `provenance_source` and `terminal_disposition` are the three fields §4.2.2
consumes; `terminal_owner` and `provenance_source` are copied from the durable journal row (§4.2.1)
and `terminal_owner` is `""` exactly when the disposition is `released`, `exited` or `residual` —
never a placeholder, never a synthesised actor id. `terminal_title` is carried so a `residual` row
can be reported to a human in terms they can act on.

The pause record itself gains one derived boolean, `ac1_discharged`, defined as
`all(row["terminal_disposition"] in AC1_DISCHARGING_DISPOSITIONS for row in settlement_ledger)`. It
is `True` for every committed pause by construction (the pause refuses otherwise) and may be `False`
only on a disposition record written by abandon (§9.2 step 6). It exists so that "AC-1 held for this
run" is a fact a reader can check rather than an assumption a reader must make.

**`terminal_digest`, not a terminal handle.** `state.FORBIDDEN_KEYS`
(`state.py:42`) matches `terminal_handle` case-insensitively and `_checkpointable` raises
`NON_CHECKPOINTABLE_STATE` on any such key — and the *value* is a live runtime handle, which the
checkpoint must never carry. So a row stores `terminal_digest = sha256(handle)` plus the provenance
that actually decides authority (`terminal_role`, `terminal_origin`), which is what
`cleanup_authority(role, origin, owned_by_this_dispatch)` (`orca_runtime_harness.py:482`) consumes.
The live handle stays in adapter memory, exactly as `OrcaAdapter` already keeps `terminal` out of
the persisted receipt (`orca_adapter.py:141-142`).

**How staleness is detected.** On resume, three independent comparisons, all read from the
**reconstructed checkpoint** (the record's copies are only cross-checked under C3):

| axis | frozen | current | mismatch → |
|---|---|---|---|
| repository | `pause_binding.repository_binding` | the caller-supplied current binding, normalised by `contracts.normalize_repository_binding` | `STALE_SOURCE_BINDING` (revalidate) |
| artifact | `pause_binding.artifact_binding` | current artifact binding, normalised | `STALE_ARTIFACT_BINDING` (revalidate) |
| policy | `pause_binding.policy_digest` | `policy_digest(SKILL.md)` recomputed now | `STALE_POLICY_DIGEST` (revalidate) |

These three are **revalidation triggers**, not refusals — they are in `PAUSE_REVALIDATION_CODES`,
a set deliberately disjoint from `PAUSE_REFUSAL_CODES`. §7 says what they cause.

---

### 4. Pause procedure — settlement and terminal-ownership release

#### 4.1 Where the hook sits

The `BLOCK`-for-decision edge no longer hops straight to `TERMINAL`; when pause is admissible it
routes to the new **PAUSE node**, which is the only place the engine performs lifecycle settlement.
`terminal_node` still performs no external call.

```python
def pause_node(settlement_port, approval_port, *, clock, skill_path):
    def node(state): ...
    return node
```

built in `build_graph` beside `execute_intent_node`. Both ports are capability-gated before use,
the way `EXTERNAL_LOOKUP`/`EXTERNAL_RESUME` already are (`executor.py:121`, `:162`); a missing
capability is not reachable here, because `pause_admissible` already refused the route.

#### 4.2 The four-axis accounting (AC-1, FI-4, FI-5)

For every dispatch the run created and has not yet accounted — `settlement_port.open_dispatches()`,
which returns `intent_id`s for **this run only** and never includes the Coordinator's own terminal
(`SKILL.md:898`, `:2407`):

0. **(the handle, before any axis can be evaluated).** `settlement_port.recover_handle(intent_id)`
   (§4.2.1a). In the same process that created the dispatch this is a memory read and returns
   `handle_recovery = "in_process"`. In a **fresh** process — the case F-002 is about — it enumerates
   the scoped terminal listing, narrows by normalised run-unique title and verifies against the
   journalled digest. It runs first because steps 1 and 4 below cannot execute without a plaintext
   handle: `account_axes` takes one, and so does `register_terminal` when the ledger row is re-seeded
   from the journal. Any outcome other than `in_process`/`listing_verified` refuses the row here,
   before a single mutating verb is considered.
1. **(a) decided, never assumed.** `row = settlement_port.account_dispatch(intent_id)` — read-only,
   issues no mutation (the Orca implementation delegates to `harness.account_axes`, which is
   documented as issuing zero Orca commands). If `row["settlement"] == "not_settled"`, the named
   recovery path runs: `settlement_port.recover_dispatch(intent_id, reason=...)` → `worker-abandon`
   then `worker-release` for a supervised worker in `UNSETTLED_WORKER_STATES`, or
   `task-update --status failed` for an unsupervised low-level Dispatch. The row is then recorded
   `settlement="recovered"`, `recovery="abandon:<state>"` or `"task-update:failed"`, **not**
   `"settled"`, with `worker_done` count 0 and **no role promotion**
   (`orca_runtime_harness.py:3568-3570`, `SKILL.md:869`, `:928-930`).
2. **(b) recorded, never inferred from (a).** `worker_resource` ∈ reuse/retain/release/`unsupervised`.
3. **(c1) observed independently**, with the ~10 s eventual-consistency caveat honoured: a
   liveness/listing mismatch is recorded as `"disputed"` and is **never** resolved by closing
   (`SKILL.md:884`). A `"disputed"` reading is re-observed once, after the documented consistency
   window has elapsed on the injected clock (`ManualLeaseClock` in tests — never a real sleep); a
   reading that survives that single bounded retry stays `"disputed"` and is then handed to the exit
   invariant of §4.2.2, which decides whether it is admissible.
4. **(c2) evaluated with its evidence.** `cleanup_authority` ∈ authorized/not_authorized/unknown.
   `release_terminal` is called **only** when `cleanup_authority == "authorized"` *and* the
   lifecycle intent is `release`; anything else is retain-and-report, and `unknown` is treated as
   `not_authorized` **for closing** and differs only in reporting duty (`SKILL.md:913`).
   `unknown` authority is emphatically **not** by itself a pause outcome: it says only that this
   Coordinator may not close the terminal, and it leaves the ownership question open. §4.2.2 is what
   closes it, and refuses the pause when it cannot.
   On a recovery path the `role`/`origin` that `cleanup_authority` consumes come from the ledger row
   this process re-seeded **from the durable journal** (§4.2.1), not from anything the runtime
   returned — the runtime has never held them.

The row is written to the **durable settlement journal** of §4.2.1 and, at the commit instant,
*projected* into `pause_binding["settlement_ledger"]`, keyed by `intent_id`. The journal is the
storage; the `pause_binding` copy is a projection carried in the checkpoint for the reader's benefit,
exactly as `projection` is subordinate to the checkpoint in §2.3. **Idempotency:** a dispatch whose
`intent_id` already has a journal row at stage `ACCOUNTED` or `DISPOSED` is not re-accounted, so a
crash between dispatch *n* and *n+1* leaves the completed rows standing **on disk** and only the
remainder is processed on retry (V1, T-27).

**Four pause refusals, not one.** Pause is refused — the route falls back to `BLOCK` — if any of:

- any row still reads `settlement == "not_settled"` after recovery → `DISPATCH_UNACCOUNTED`. This is
  FI-5 made structural: because `OrcaAdapter` withholds `external_resume`
  (`orca_adapter.py:31-38`), a dispatch left running at pause time is unrecoverable by any new
  Coordinator (`IDEMPOTENCY_RECOVERY_UNSUPPORTED`, `executor.py:121-125`), so leaving one running is
  not a pause — it is a leak; **or**
- any row fails the terminal disposition exit invariant of §4.2.2 → `TERMINAL_OWNERSHIP_UNKNOWN`.
  An ownership question that is merely *recorded* as open is not answered, and OS-31's AC-1 asks for
  an answer; **or**
- a row sits in the W-C window of §4.2.1 — a Task exists, no Dispatch exists, and the journal never
  reached `INTENDED` — so a terminal may have been created whose identity nothing can now *prove* →
  `TERMINAL_ORPHAN_POSSIBLE`. This is the one window the verified Orca contract cannot close, and it
  is refused rather than assumed away. The same code covers a row at stage `>= INTENDED` whose scoped
  terminal listing returned no match that no independent observation corroborates (§4.2.1a); **or**
- leg (4) of §4.2.1a found title matches for a row at stage `>= INTENDED` that its durable
  `terminal_digest` contradicts, or that it matches twice → `TERMINAL_IDENTITY_UNVERIFIED`. The
  runtime showed us something that disagrees with what we recorded, and choosing among candidates is
  the guess this gate exists to forbid.

All four codes are in `PAUSE_REFUSAL_CODES` and all four leave the run exactly where a pre-OS-31
decision block left it: `BLOCK`/`BLOCKED`, with the refusal code on the terminal reason. Refusing to pause is
always available and always safe; pausing with an unresolved terminal is neither.

#### 4.2.1 The durable settlement journal (Tier 2b) — durable *before* the effect

`pause_binding` lives in the checkpoint, and the PAUSE node's checkpoint does not commit until the
node returns (§4.4). Every settlement row produced inside the node is therefore invisible to a
process that dies before that commit — and worse, the two things a successor needs in order to even
*find* the work are process memory today: `OrcaAdapter._receipts` (`orca_adapter.py:21`) and the
harness terminal ledger `OrcaRuntimeHarness._terminals` (`orca_runtime_harness.py:936`). The durable
`FileRuntimeStateStore` receipt deliberately carries no terminal handle
(`RECEIPT_KEYS = {"task_id", "dispatch_id", "external_id", "intent_id"}`, `runtime_state.py:87`;
`orca_adapter.py:138-140` says so in a comment). Idempotent re-mutation is no help when the
successor cannot *discover* what to re-account. So OS-31 adds one durable, run-scoped, append-then-
promote journal, written **before** every external effect it describes.

```
<artifact_base>/artifacts/runs/<run_id>/.settlement_journal.json        # rows, keyed by intent_id
<artifact_base>/artifacts/runs/<run_id>/.settlement_journal.json.lock   # the flock sidecar
```

`FileSettlementJournal` lives in `pause_store.py` — same module, same `durable_store` discipline
(flock + `os.replace`), same "no LangGraph, no Orca" property of §10.1, so §10.1's table is
unchanged. A row is written whole or not at all; there is no partial-row window.

```python
SETTLEMENT_JOURNAL_SCHEMA_VERSION = "os31.settlement_journal.v3"
JOURNAL_STAGES = ("PLANNED", "OPENED", "INTENDED", "ACCOUNTED", "DISPOSED")
JOURNAL_ROW_KEYS = ("intent_id", "run_id", "payload_digest",
                    "task_id", "dispatch_id", "supervised",
                    "terminal_title", "terminal_worktree",   # the two the §4.2.1a listing needs
                    "terminal_digest",                       # the one that VERIFIES what it finds
                    "terminal_role", "terminal_origin", "terminal_intended_role",
                    "terminal_owner", "owner_dispatch_ids", "created_by",
                    "provenance_source",          # "journal" | "absent" -- never "runtime"
                    "stage",
                    "settlement", "worker_resource", "process_liveness",
                    "cleanup_authority", "terminal_disposition",
                    "recovery", "handle_recovery",
                    "planned_at", "opened_at", "intended_at",
                    "accounted_at", "disposed_at")
PROVENANCE_SOURCES = ("journal", "absent")
```

The schema version moves `v2 -> v3` because two keys are added to a **closed** key set, and
`validate_journal_row` refuses a row that does not match the set it was told to expect. No v2 journal
exists anywhere — OS-31 is unreleased and the file is created by this design — so there is nothing to
migrate; the bump exists so that a future reader can tell the two shapes apart rather than to support
a migration.

**The five write points, each strictly before the externally-visible effect it covers.**

Iteration 2 wrote the terminal-bearing record *after* `run_existing_task(...)` returned — i.e. after
the terminal had already been created **and** adopted. A process that died inside that call left a
terminal whose role and origin no longer existed anywhere, and no later query can restore them. That
is unrecoverable *in principle*, and this design says so rather than proposing a reconstruction that
cannot work:

- `OrcaRuntimeHarness.register_terminal` states it in its own docstring — "role and origin are the
  only axis (c2) evidence that exists, **and the runtime keeps neither, so they are recorded here or
  lost forever**" (`orca_runtime_harness.py:1042-1057`);
- `ledger_terminal` is a read over the **process-local** dict `_terminals` (`:936`) and returns
  `unknown_role` / `unknown` for any handle it has not seen (`:1157-1178`);
- the verified `worker-show` response shape carries no terminal handle and no role at all —
  `{"dispatch": {...}, "worker": {"state": ...}, "terminalResource": {"releaseState": ...}}`
  (`test_orca_runtime_contract.py:239-243`, `:1314-1318`, `:1691-1696`, `:2286`);
- the only `role` the runtime ever returns is inside a `worker-start` effect,
  `{"kind": "terminal", "action": "reused", "id": ..., "role": "agent"}` (`:374-379`) — a
  resource-kind label, not a member of `TERMINAL_ROLE_CLASSES` (`orca_runtime_harness.py:302-320`),
  so it cannot stand in for one;
- and empirically, 12 of 12 `worker-release` calls issued by the Coordinator of this very run
  against the live 1.4.197 runtime returned `state=retained reason=external_terminal
  processAction=none` — a terminal this process created and then adopted is reported back by the
  runtime as **external**. The runtime does not model our ownership at all.

So the fix is not a better reconstruction. It is to move every write **in front of** the effect that
could orphan it. The externally-visible effects of one dispatch, in the order
`OrcaAdapter.start` → `OrcaRuntimeHarness.run_existing_task` actually issues them:

```
E1  orchestration task-create                        -> task_id      (orca_adapter.py:120; harness create_task :1904)
E2  terminal create --worktree <w> --title <t>       -> handle       (harness create_fake_terminal :1960-2010)
E3  orchestration worker-start                       -> dispatch_id  (harness start_worker :2074-2140)
E4  wait_for_done / settle_attempt   -> the mutating lifecycle verbs (:3546-3571)
```

Role and origin are **not** runtime facts waiting to be read back at E2. They are the caller's own
choice, handed to `register_terminal` (`:1042`) by the code that is about to create the terminal.
They are therefore fully known *before* E1 — and that is now when they are written.

| stage | written where | written when | what it makes recoverable |
|---|---|---|---|
| `PLANNED` | `OrcaAdapter.start`, immediately before `create_task` | **before E1** | `intent_id`, `run_id`, `payload_digest`, and the entire *intended* terminal provenance — `terminal_role`, `terminal_origin`, `terminal_intended_role`, `created_by`, `terminal_title = f"os31-{run_id}-{intent_id}"` — the run-unique title E2 will be given — and `terminal_worktree`, the **stable origin-worktree selector** `id:<repo-id>::<path>` resolved at this moment by the rule below — never the alias `current`/`active`. Not one field of this is a *runtime observation of an effect*, so not one field can be lost with the effect. The title and the stable worktree selector are what make §4.2.1a's candidate set enumerable |
| `OPENED` | `OrcaAdapter.start`, at the existing `_record_receipt` site (`orca_adapter.py:117`) | after E1 returns `task_id`, **before E2** | `task_id` — a Task now exists and is bound to this intent and to the already-durable provenance |
| `INTENDED` | inside the dispatch call, through the new `terminal_observer` seam (§10.2) | when `create_fake_terminal` returns the handle, **before E3** | `terminal_digest = sha256(handle)` and `provenance_source = "journal"`: the handle is now bound to provenance that was durable before the handle existed |
| `ACCOUNTED` | the PAUSE node, under the run claim | after `account_dispatch` (read-only, **zero** Orca commands, `:1607`) and **before** any mutating `recover_dispatch` / `release_terminal` | `dispatch_id`, `supervised`, and the full four-axis outcome, per row |
| `DISPOSED` | the PAUSE/DISPOSE node, under the run claim | after the mutating call returns | `recovery`, `terminal_disposition`, `disposed_at` — the proof the row is finished |

**The recorded worktree must be a stable identity, not the alias `current`.** `--worktree` accepts,
per the version-matched `orca terminal create --help` read at this HEAD, the durable selectors
`identity:<identity>`, `id:<repo-id>::<path>`, `name:<displayName>`, `branch:<branch>`,
`issue:<number>` and `path:<path>` — while `active`/`current` are **aliases re-resolved in the
context of whichever process issues the command**. Persisting the literal word `current` would
therefore persist no worktree at all: a successor Coordinator whose own binding differs would
enumerate *its* worktree, see zero candidates, and land in `TERMINAL_ORPHAN_POSSIBLE` — fail-closed,
but not closed. So `terminal_worktree` is **never** the alias. It is the string

```python
ORIGIN_WORKTREE_SELECTOR = f"id:{worktree_id}"     # worktree_id == "<repo-id>::<path>"
```

**Where the stable value comes from, and why it is available before E1.** The harness already
executes `orca worktree current` inside `validate_orca_contract` and already reads exactly
`current["result"]["worktree"]["id"]` into its `worktreeId` capability field
(`orca_runtime_harness.py:1754-1759`). That call is part of contract validation, which runs during
harness construction — strictly **before** any OS-31 dispatch, and therefore strictly before E1. The
value is a property of *where this Coordinator process is*, not an observation of an effect that E1
or E2 produced, which is precisely why it can be journalled before the crash window opens. Executed
against the live 1.4.197 runtime at this HEAD it returned `ok: true` with
`result.worktree.id = "7b6ee134-05a2-4dbd-be45-91ea1c2d2d7e::/Users/luminous/aiAssistedProjects/orca-skills"`
— the full `<repo-id>::<path>` form the orchestration guide requires ("use the exact full
`<repo-id>::<path>` worktree id … a bare repo id cannot target the new worktree"). The same string is
independently observable on every element of `orca terminal list --json` as `worktreeId`, which is
what lets §4.2.1a cross-check it. No new verb is introduced: `worktree current` is already in the
harness's executed set.

**If it cannot be resolved, the design fails closed — it never falls back to the alias.** If
`worktree current` is unreadable (non-zero exit, `ok:false`, unparseable, or an `id` that is absent,
empty, or not of the form `<repo-id>::<path>`), `OrcaAdapter.start` **refuses before E1** with
`DISPATCH_UNACCOUNTED` and creates no Task and no terminal. There is nothing to leak, because nothing
was made. Substituting `current` at this point would produce a row that *looks* recoverable and is
not, which is the exact defect this rule exists to prevent.

**E2 and recovery use the identical persisted string.** The value written to
`row["terminal_worktree"]` is passed verbatim as `--worktree` to E2's `terminal create`, and the same
`row["terminal_worktree"]` is passed verbatim as `--worktree` to leg (4)'s `terminal list`. There is
one string, resolved once, journalled once, replayed byte-for-byte by both the effect and its
recovery. No code path re-resolves it, and no code path may.

`terminal_observer` is one of the three new seams this requires: `run_existing_task` gains a
keyword-only `terminal_observer: Callable[[str], None] | None = None`, invoked with the handle
immediately after `create_fake_terminal` returns (`orca_runtime_harness.py:3196-3205`) and
immediately before `start_worker`. It defaults to `None`, so all nine existing call sites bind
unchanged, and it issues no Orca command. The adapter passes a closure that performs the `INTENDED`
write. There is no other point in the call at which a durable write can sit between E2 and E3, which
is why the seam exists at all.

The second seam is what makes the title on the terminal the same string the `PLANNED` row promised.
`create_fake_terminal` today hard-codes `--title f"fake-{role}-{iteration}"`
(`orca_runtime_harness.py:1998-2003`), which is **not** run-unique — two runs, or two iterations of
two runs, can collide on it. It gains a keyword-only `title: str | None = None` that defaults to
`None` and preserves the existing string exactly; `run_existing_task` gains the matching keyword-only
`terminal_title: str | None = None` and forwards it. Both existing call sites (`:3197`, `:3504`) bind
unchanged. When OS-31 dispatches, the adapter passes `os31-<run_id>-<intent_id>`, and that — not the
`fake-*` string — is what E2 sends and what §4.2.1a matches on. A design that wrote a run-unique title
into the journal but let E2 send a colliding one would be matching on a promise nothing kept.

The third seam carries the stable selector to E2. `create_fake_terminal` today hard-codes
`--worktree "current"` (`orca_runtime_harness.py:2000-2003`) — the alias, which is exactly the defect
above. It gains a keyword-only `worktree: str = "current"` whose default preserves today's behaviour
byte-for-byte, with a matching `terminal_worktree: str | None = None` passthrough on
`run_existing_task`. Both existing call sites (`:3197`, `:3504`) bind unchanged. When OS-31
dispatches, the adapter passes `row["terminal_worktree"]` — the stable `id:<repo-id>::<path>` string
it journalled at `PLANNED` — so the terminal is demonstrably created in the worktree the journal
names, rather than in whatever `current` meant at that instant. Without this seam the `PLANNED` row
would again be recording a scope that E2 did not honour.

**The live terminal handle is still never persisted** — only `sha256(handle)` and the caller-chosen
`terminal_title`. A title is a label, not a capability; it is what lets a *human* identify a residual
terminal in the one window below that no code can close. This keeps the rule `state.FORBIDDEN_KEYS`
enforces for the checkpoint (§3) and the rule `OrcaAdapter` already follows for its receipt
(`orca_adapter.py:138-140`).

#### 4.2.1a Recovering the plaintext handle — leg (4), enumerate then **verify**

Leg (4) is **not** a fourth enumeration leg. Legs (1)–(3) below answer "*which* dispatches must be
accounted"; leg (4) runs afterwards, once per enumerated row, and answers "*which live handle*, if
any, is this row's terminal". A row can be enumerated and still fail leg (4) — that is the whole
point, and §4.2.2 is what decides whether such a row is admissible.

Everything above makes *provenance* durable. It does not by itself make the terminal *addressable*
by a fresh process, and iteration 3 did not close that gap: the journal stores
`terminal_digest = sha256(handle)`, while `harness.register_terminal(handle, ...)`
(`orca_runtime_harness.py:1042`) and `harness.account_axes(..., terminal, ...)` (`:1607`) both take
the **plaintext** handle. A digest is only a verifier; without a candidate set to verify against it
decides nothing. Iteration 3 asserted the candidate set could not exist. That assertion was **wrong**,
and this section replaces it with a verified operation.

**The operation, verified first-hand against the live runtime at this HEAD.** Orca 1.4.197 supports
`orca terminal list`, and its `--help` declares the grammar
`orca terminal list [--worktree <selector>] [--limit <n>] [--include-visual-layouts] [--json]`.
Executed as `orca terminal list --json`, it returned `"ok": true` and a `result.terminals` array
whose every element carried **both** of the fields this design needs:

```json
{"handle": "term_0c42c18d-d2bd-4f73-8075-9fea396364dd",
 "title":  "✳ OS-31 Durable Pause Resume Analysis",
 "worktreeId": "...", "worktreePath": "...", "orphaned": false, "connected": true, ...}
```

OS-31 reads exactly three of those fields — `handle`, `title`, and `orphaned` (the last as *reporting*
evidence only, never as an authority). It reads nothing else, so a future response that adds or
reorders fields cannot break it. `terminal list` is therefore added to the design's verb list, on the
same footing as the verbs §10.2 already commits to: it is a real command in the version-matched CLI,
its grammar was read from `--help` rather than guessed, and its response shape was observed rather
than assumed — which is exactly the discipline `SKILL.md:2396` demands and exactly what iteration 3
failed to do when it declared no such verb existed.

**The candidate set is the run-unique title, inside a stable recorded scope.** The `PLANNED` row
carries `terminal_title = f"os31-{run_id}-{intent_id}"` and `terminal_worktree`, both written before
E1. E2 sends that exact title (the title seam) into that exact worktree (the worktree seam). So a
successor enumerates `terminal list --worktree <row["terminal_worktree"]> --json` and filters on the
title.

The listing is issued with the **recorded stable selector** — `id:<repo-id>::<path>`, resolved once
before E1 per §4.2.1 and replayed byte-for-byte. This is the substantive point, not a turn of phrase:
`current`/`active` are aliases that the *reading* process re-resolves, so a successor Coordinator
bound elsewhere would enumerate its own worktree and see nothing. Because the recorded selector names
a repo id and an absolute path, it denotes the same worktree in every process, and the successor's
own binding is irrelevant to it. "Absent" is only meaningful within a stated scope, and the scope is
now genuinely stated rather than named by a pronoun. Verified at this HEAD:
`orca terminal list --worktree id:7b6ee134-…::/Users/luminous/aiAssistedProjects/orca-skills --json`
returned `ok: true` with 20 terminals, every element's `worktreeId` equal to that same id.

**A zero-length listing is not self-certifying, so the scope is proved before "absent" is believed.**
Executed at this HEAD, an *unresolvable* selector does **not** raise: `terminal list --worktree
id:00000000-0000-0000-0000-000000000000::/nope --json` returned `ok: true`, `result.terminals: []`,
`totalCount: 0` — indistinguishable, on its own, from a real worktree that happens to hold no
terminals. Treating that as "the terminal is gone" would reintroduce the defect one level down. So
before *any* zero-candidate outcome may be reported, leg (4) must independently confirm the recorded
selector still resolves, via `orca worktree show --worktree <row["terminal_worktree"]> --json`, whose
grammar was read from `--help` (`--worktree <selector> [--json]`) and whose behaviour was executed at
this HEAD: it returned `ok: true` with `result.worktree.id` **echoing the recorded id exactly** for
the live selector, and `ok: false` with `error.code == "selector_not_found"` for the bogus one. The
guard passes only when `ok` is true **and** the echoed `result.worktree.id` is byte-identical to the
`<repo-id>::<path>` inside `row["terminal_worktree"]`. Anything else is *unknown*, never *empty*.

**Titles are decorated, so equality is the wrong predicate.** Observed live, in one listing:
`"✳ OS-31 Durable Pause Resume Analysis"`, `"◐ Orca orchestration check run_8e8f9451ad44"`,
and also undecorated titles such as `"orca-skills"` and `"Terminal 5"`. The decoration is a leading
status glyph plus a space, it is applied by the UI, and this design does **not** assume that alphabet
is closed. `normalize_terminal_title` is therefore defined as: NFC-normalise, then left-strip every
code point whose Unicode general category is `So`, `Sk`, `Cf` or `Cn` or which is whitespace, then
strip surrounding whitespace. A row matches iff either

1. `normalize(row["title"]) == terminal_title` — the primary predicate; or
2. `normalize(row["title"]).endswith(terminal_title)` **and** the residual prefix contains no
   character from the title alphabet `[A-Za-z0-9_-]` — the fallback, which absorbs a decoration glyph
   this design has not enumerated.

Predicate 2 cannot collide with another OS-31 terminal, and the argument is short enough to check: an
OS-31 title is drawn entirely from `[a-z0-9_-]`, so if `normalize(X)` ended with title *B* while
belonging to a different intent whose title is *A*, the residual prefix would have to contain the
tail of *A* — characters that are all in the alphabet — and predicate 2 rejects it. Suffix matching is
thus bounded by the alphabet, not by trust.

**This rule was executed, not merely reasoned about.** Applied to the 18 terminals the live listing
returned at this HEAD: every decorated title's leading code point is Unicode category `So` (`✳`
U+2733, `◐` U+25D0) and is stripped together with its trailing space, so
`"✳ OS-31 Durable Pause Resume Analysis"` normalises to `"OS-31 Durable Pause Resume Analysis"`;
undecorated titles (`"orca-skills"`, category `Ll`; `"Terminal 5"`, category `Lu`) pass through
**unchanged**; and normalisation is idempotent on all 18. Against a synthetic target
`os31-run_c2166e75bb02-intent_ab12`, the undecorated and both decorated forms matched, and four
adversarial near-misses were all correctly **rejected**: a foreign run sharing the same intent suffix
(`os31-run_OTHERRUN0000-intent_ab12`), an alphanumeric prefix (`x-os31-…`), a longer intent id
(`…-intent_ab120`), and an unrelated title. Note what this shows about the two predicates: on every
decoration observed so far, predicate 1 *already suffices*, because the glyph and its space are both
stripped. Predicate 2 is a safety net for a decoration this design has not seen — it is not the
mechanism the common case depends on, which is why bounding it by the title alphabet costs nothing.

**The digest is what turns a match into proof.** For any row at stage `INTENDED` or later the journal
holds `terminal_digest`, so a candidate is not accepted because its title matched — it is accepted
because `sha256(candidate["handle"]) == row["terminal_digest"]`. The title narrows; the digest
decides. This is the sentence F-002 was asking for, and it is why the digest was never dead weight:
it lacked an enumerable candidate set, and `terminal list` supplies one.

**Decision table — one outcome is a recovery, every other outcome fails closed.** The rows are
disjoint. The scope guard is not stage-specific: **every** zero-candidate row states it explicitly,
so no "the terminal is absent" verdict can be reached at any stage without the recorded selector
having first been proved to resolve.

| row stage | title matches | digest check | `handle_recovery` | outcome |
|---|---|---|---|---|
| `< OPENED` | not attempted — E2 was never requested | — | `not_attempted` | nothing to recover (W-A) |
| `>= INTENDED` | ≥ 1, and **exactly one** candidate's `sha256(handle)` equals `terminal_digest` | **passes** | `listing_verified` | **handle recovered.** Re-seed the harness ledger from the journal row and run the four-axis accounting. This is the only branch that yields an addressable handle |
| `>= INTENDED` | ≥ 1, but **no** candidate's digest matches | contradicted | `unverified` | **refuse** `TERMINAL_IDENTITY_UNVERIFIED`. A title match the digest contradicts is somebody else's terminal, and acting on it would close the wrong session |
| `>= INTENDED` | ≥ 1, and **two or more** candidates' digests match | ambiguous | `unverified` | **refuse** `TERMINAL_IDENTITY_UNVERIFIED`. Two live handles cannot both hash to one digest unless the listing repeated a row; either way this is an anomaly, not a coin toss |
| `>= INTENDED` | **0**, listing read successfully at the recorded scope **and** the scope guard passed | not applicable | `not_listed` | the terminal is not live in the scope it was created in. Admissible as `exited` **only** if corroborated by an independent runtime observation — `worker-show --dispatch` reporting a terminal worker state, or `dispatch-show --task` proving no Dispatch and the Task already terminal. Uncorroborated → **refuse** `TERMINAL_ORPHAN_POSSIBLE` |
| any | **0**, but the recorded selector does **not** resolve — `worktree show` returns `ok:false`, or its echoed `result.worktree.id` differs from the recorded id | not attempted | `scope_unresolved` | **refuse** `DISPATCH_UNACCOUNTED`. An empty listing under an unresolvable scope is *unknown*, not *empty*; the terminal may be perfectly alive in a worktree this process failed to name. This is the cell that makes the recorded selector's stability load-bearing rather than decorative |
| `OPENED` (W-C) | ≥ 1 | **impossible** — no digest exists yet | `listing_candidate` | **refuse** `TERMINAL_ORPHAN_POSSIBLE`, unchanged. The candidate handle is *reported*, never acted on — see below |
| `OPENED` (W-C) | 0, scope guard passed | impossible | `not_listed` | **refuse** `TERMINAL_ORPHAN_POSSIBLE`, unchanged |
| any | the listing cannot be read (non-zero exit, `ok:false`, unparseable) | — | `unverified` | **refuse** `DISPATCH_UNACCOUNTED`. An unreadable listing is "unknown", never "empty" — the rule `OrcaAdapter.lookup` already states (`orca_adapter.py:44-52`) |

`terminal list` issues no mutation, so leg (4) is safe to repeat and is re-run rather than cached
across a retry, exactly like `account_dispatch`.

**Where the decision lives, and why that matters for D1.** The table above is implemented as a
**pure** function in `pause_policy.py` — `resolve_terminal_handle(row, listing) -> {"handle",
"handle_recovery"}` over an already-fetched listing — while the I/O (`terminal list`) lives in the
harness and the adapter. So the engine never imports the harness (PLAN D1 holds unchanged),
`pause_policy` keeps its "no LangGraph, no Orca" property of §10.1, and every cell of the table —
including all four fail-closed cells — is unit-testable with no Orca, no adapter and no fixture at
all, from a literal Python list. The tests in §13.3 then prove the *wiring* on top of that, rather
than having to prove the logic and the wiring at once.

**What this does and does not change about W-C.** Iteration 3 refused W-C on the ground that "the
verified grammar has no terminal-listing verb". That ground is **withdrawn** — the verb exists, and
leaving the sentence standing would have been a second false claim. The honest restatement is
narrower and it does not move the refusal:

- a W-C row is at stage `OPENED`, so **no digest was ever written**; the predicates above can still
  yield a candidate, but nothing can *verify* it;
- OS-31 will not close, release or otherwise act on a terminal it cannot prove is its own, because
  closing the wrong session is the irreversible harm this whole gate exists to prevent, and a title
  is a label, not proof;
- so W-C keeps its verdict (**NOT closed**), keeps its refusal code (`TERMINAL_ORPHAN_POSSIBLE`),
  keeps `residual` on the abandon path, and keeps AC-1 withheld. No refusal code, disposition,
  vocabulary or acceptance claim changes.

What *does* improve is the report. A `residual` row is now enumerated with its
`handle_recovery = "listing_candidate"` and the candidate handle itself, so the human disposing of it
has an address rather than a search. Persisting that handle is policy-consistent and deliberately
scoped: the skill's redaction policy names the terminal handle as an **identity the record exists to
prove**, never redacted, alongside `run_id`/`task_id`/`dispatch_id` and `reviewer_terminal`
(`SKILL.md:1553-1555`). The handle still never enters the checkpoint — `state.FORBIDDEN_KEYS`
(`state.py:42`) matches `terminal_handle` case-insensitively — and still never enters the journal or
the durable receipt (`RECEIPT_KEYS`, `runtime_state.py:87`). It lives in adapter memory and, for a
residual row only, in the two abandon-path human-facing outputs — the `residual_terminals` entry of
the abandon **disposition** record and the abandon report/`run_end` reason (§2.3). It is never in a
paused run's record, because a committed pause has no residual rows by construction. Those are four
different stores with four different rules, and this design keeps all four.

**Why not simply persist the handle at `INTENDED` and skip all of this?** That was resolution path
(A)'s other branch and it was rejected on the design's own terms: the journal is read by the same
reconstruction path that feeds `pause_binding`, `FORBIDDEN_KEYS` exists precisely to keep live
handles out of reconstructable state, and a stored handle would be *stale* evidence — it says what
was true when it was written, not what is live now. Leg (4) reads what is live and proves it against
a digest that cannot go stale. It also needs no new durable secret, no new file and no new field
outside the two the closed schema already gained.

**Every crash window, with what is and is not recoverable.**

| # | the process dies … | what exists externally | recovered how | verdict |
|---|---|---|---|---|
| W-A | before the `PLANNED` write lands | nothing — `create_task` is not reached | nothing to recover | **closed** |
| W-B | after `PLANNED`, during or after E1 | possibly a Task | leg (3) below: `task-list --run` with parsed-spec `intent_id` matching (`orca_adapter.py:44-89`) decides whether the Task exists. If it does, the `PLANNED` row already carries its provenance; if it does not, the row is finished `DISPOSED` with `recovery = "no_effect"` | **closed** |
| W-C | after `OPENED`; during E2, or after E2 returns but before the `INTENDED` write lands | a Task, and **possibly a terminal whose digest no process ever recorded** | leg (4) enumerates and may return a title **candidate**, but the row never reached `INTENDED`, so no `terminal_digest` exists and nothing can verify the candidate. `dispatch-show --task` proves no Dispatch exists, so `worker-show` (which requires a `dispatch_id`) adds nothing either. The handle is **addressable-but-unproven**, which this design treats as not recoverable | **NOT closed — refused, reported with its candidate; see below** |
| W-D | after `INTENDED`; during or after E3 | Task, terminal, possibly a Dispatch | the journal holds the title, the **stable** `id:<repo-id>::<path>` worktree selector (never the alias `current`, §4.2.1), the digest **and** the provenance. Leg (4) (§4.2.1a) enumerates `terminal list --worktree <recorded> --json`, narrows by normalised title and **proves** the survivor with `sha256(handle) == terminal_digest`, yielding the plaintext handle that `register_terminal` and `account_axes` require. `dispatch-show --task` supplies `dispatch_id` if E3 completed; `worker-show --dispatch` supplies liveness. Provenance is never asked of the runtime, so nothing depends on what the runtime forgot; identity is asked of the runtime and then verified against a durable digest, so nothing depends on trusting what it returned; and the *scope* of the question is a durable repo-id-plus-path, so nothing depends on where the successor process happens to be bound | **closed** |
| W-E | after `ACCOUNTED`, before `DISPOSED` | everything | re-run `account_dispatch` (zero commands, always safe to repeat), then the mutation, fenced by the row's own stage **and** the harness claim gate (`:3508-3517`) | **closed** |

**W-C is a limitation, stated and enforced, not papered over.** OS-31 cannot *prove* the identity of
a terminal that was created but whose digest was never journalled. Note precisely what the limit is
now, because §4.2.1a moved it: enumeration is no longer the missing piece — `terminal list` exists and
leg (4) runs for a W-C row like any other, and it may well return exactly one title candidate. What is
missing is the **verifier**. The row never reached `INTENDED`, so there is no `terminal_digest`, so
the candidate rests on a label alone; and this design does not close, release or adopt a session on a
label. Closing W-C properly would need a `terminal create` that accepts a caller-supplied idempotency
key — so that identity could be fixed *before* the call rather than read back after it — and that is
not in the version-matched contract this design is permitted to assume; inventing it is exactly what
`SKILL.md:2396` forbids. What the design does instead is make the window **detectable, reportable and
fail-closed**:

- the `PLANNED` / `OPENED` row records that a `terminal create` was about to be issued for this
  intent, under a known `terminal_title`;
- on recovery, a row at stage `OPENED` whose Task exists but whose `dispatch-show --task` reports no
  Dispatch is marked `provenance_source = "absent"`, and `terminal_disposition` is refused for it;
- **pause refuses** with `TERMINAL_ORPHAN_POSSIBLE` and routes to `BLOCK` — no pause record, no
  half-paused run;
- **abandon completes** (it is the last-resort disposition) but records the row
  `terminal_disposition = "residual"` (§9.2 step 6) and enumerates it by `terminal_title`, `task_id`
  and provenance in the abandon report and the `run_end` reason, so a human can dispose of it — and,
  new in this round, with `handle_recovery` and, when leg (4) produced one, the **candidate handle
  itself**, so the human is handed an address instead of a search;
- and **OS-31 does not claim AC-1 for any run that ends with a `residual` row.** The record carries
  `ac1_discharged: false` and the reason says why. AC-1 is claimed only when every row reached a
  member of `AC1_DISCHARGING_DISPOSITIONS` (§4.2.2).

The window is narrow — between one command returning and one `os.replace` landing — but narrow is not
closed, and this design says which one it is.

**`open_dispatches()` is a reconstruction, not a memory read.** For the current `run_id`, the union
of three sources, never one:

```
(1) journal rows for this run whose stage != DISPOSED
      -> now includes PLANNED and OPENED rows, i.e. work that had not yet reached a terminal
(2) durable RuntimeStatePort records for this run (FileRuntimeStateStore, keyed by intent_id)
    whose status is EFFECTED or SETTLED and which have no journal row
      -> a receipt written by a process older than this journal schema
(3) the authoritative runtime listing:
      harness.call("orchestration", "task-list", "--run", run_id)  ->  every Task whose parsed
      spec intent_id appears in neither (1) nor (2)
      -> a FOREIGN Task: one this adapter did not create, or one created before OS-31
minus the Coordinator's own terminal, which never appears in any leg (SKILL.md:898, :2407)
```

Legs (2) and (3) reuse machinery that already exists and is already exercised: `task-list --run` at
`orca_runtime_harness.py:1952` and `:3566`, and spec-parsed `intent_id` matching in
`OrcaAdapter.lookup` / `_spec_intent_id` (`orca_adapter.py:44-89`), which deliberately compares the
parsed top-level field rather than searching raw text. **No new Orca grammar is introduced.** A
listing that cannot be read raises `ExternalLookupUnavailable` and the pause refuses with
`DISPATCH_UNACCOUNTED` — an unreadable listing is "unknown", never "empty" (the rule `lookup` already
states at `orca_adapter.py:44-52`).

Because `PLANNED` is now written **before** `create_task`, leg (3) is no longer the leg that rescues
*our own* lost work: if the `PLANNED` write did not land, `create_task` was never called. Leg (3) is
therefore demoted to what it honestly is — a **detector of Tasks this design did not create**. Such a
row has no OS-31 provenance and never will; it is `provenance_source = "absent"`, it fails §4.2.2
closed, and it is reported rather than adopted. That is a stronger property than iteration 2 claimed,
and a narrower one than it implied.

**Provenance is journal-authoritative; the runtime is observation-only.** This is the sentence
iteration 2 was missing, and the table that makes it checkable:

| datum | authoritative source | why not the runtime |
|---|---|---|
| `terminal_role`, `terminal_origin`, `terminal_intended_role`, `created_by` | the journal `PLANNED` row | the runtime stores none of them (`register_terminal` docstring, `:1056`); `ledger_terminal` is process memory (`:936`, `:1157`) |
| `terminal_digest` | the journal `INTENDED` row | `worker-show`'s verified response carries no terminal handle field at all |
| the **plaintext handle** | *neither side alone.* The runtime supplies the candidate set (`terminal list` → `handle`, `title`); the journal supplies the discriminator (`terminal_title`, `terminal_worktree`) and the **verifier** (`terminal_digest`). §4.2.1a | the runtime cannot say which of its terminals is ours, and the journal cannot say which handle is live. Each half is useless alone, which is why iteration 3, holding only the journal half, could not close W-D |
| `terminal_owner`, `owner_dispatch_ids` | the journal (`created_by` + the dispatch that adopted the handle) | same |
| `task_id` | journal `OPENED`; else leg (2)/(3) | the runtime *does* list Tasks, and that leg is used |
| `dispatch_id` | journal `ACCOUNTED`; else `dispatch-show --task` (`:2278`, `:3548`) | runtime state, correctly read from the runtime |
| `process_liveness`, `settlement` | `worker-show --dispatch` / `dispatch-show --task` observation fed to `account_axes` (`:1607`) | runtime state, correctly read from the runtime |

On recovery the successor re-seeds its process-local ledger **from the journal**:

```python
harness.register_terminal(handle,                      # from leg (4): title-narrowed, digest-VERIFIED (§4.2.1a).
                                                       # Never from worker-show, which returns no handle at all.
                          role=row["terminal_role"],
                          origin=row["terminal_origin"],
                          intended_role=row["terminal_intended_role"],
                          owner_dispatch_id=row["dispatch_id"],
                          created_by=row["created_by"])
```

It never asks `worker-show` for role or origin, because `worker-show` does not have them. A row whose
`provenance_source` is `"absent"` is **not** re-registered at all: it reads back `unknown_role` /
`unknown` and §4.2.2 refuses it. That junction is deliberate — F-002's discovery problem and F-001's
ownership problem meet there, and both fail closed.

**What survives process death, field by field** — the question F-002 asks, answered explicitly:

| datum | survives in | recovered by | lost if the process dies in |
|---|---|---|---|
| the *intent* to dispatch | journal `PLANNED` (before E1) | leg (1) | W-A only, where nothing happened |
| `task_id` | journal `OPENED`; `FileRuntimeStateStore` receipt (`RECEIPT_KEYS`, `runtime_state.py:87`); the runtime's Task listing | legs (1), (2), (3) | — |
| terminal **provenance** (`terminal_role`, `terminal_origin`, `terminal_intended_role`, `created_by`, `terminal_title`) | journal `PLANNED` — durable **before** the terminal exists | leg (1) | — |
| terminal **identity** (`terminal_digest`) | journal `INTENDED` — durable **before** the terminal is adopted | leg (1) | — |
| the **plaintext handle** `register_terminal`/`account_axes` need | **nothing persists it, by design** (`FORBIDDEN_KEYS`, `RECEIPT_KEYS`) | leg (4), §4.2.1a: `terminal list --worktree <recorded>` narrowed by normalised `terminal_title`, then **proved** against `terminal_digest` | **W-C**: created before the digest was journalled ⇒ a candidate with no verifier; refused, reported with its candidate, AC-1 not claimed |
| `dispatch_id` | journal `ACCOUNTED`; durable receipt | legs (1), (2); else `dispatch-show --task` | — |
| four-axis outcome | journal `ACCOUNTED` | leg (1); else re-run `account_dispatch`, read-only and always safe to repeat | — |
| per-row completion | journal `DISPOSED` | leg (1); a row not at `DISPOSED` is unfinished, full stop | — |
| the fact that a pause was even attempted | it does **not** need to survive — the pre-checkpoint crash window leaves `run_lifecycle == "ACTIVE"` and no pause record, so the successor re-drives the PAUSE node from the last committed checkpoint (§4.4) | — | — |

Re-driving is safe because the only repeated read is `account_dispatch` (zero commands) and every
mutation is fenced by the harness claim gate (`orca_runtime_harness.py:3508-3517`) *and* by the row's
own stage. Idempotence and discoverability are two separate, separately-provided properties, which is
precisely what iteration 1 conflated — and durability-before-effect is a third, which is what
iteration 2 was still missing.

#### 4.2.2 The terminal disposition exit invariant (AC-1) — fail-closed

OS-31 requires that a pause leave **no ambiguous terminal ownership**. Recording an ambiguity does
not discharge it — and neither does *labelling* one. Iteration 2 got the first half right and the
second half wrong: it let a row call itself `transferred` on the strength of a stored string. A label
is not a transfer. This revision separates the two ideas by name, so the vocabulary cannot make a
claim the mechanism does not perform:

```python
TERMINAL_DISPOSITIONS = ("released", "exited", "retained_by_named_owner", "residual")
# AC-1 is discharged by the first three, and by nothing else.
AC1_DISCHARGING_DISPOSITIONS = frozenset({"released", "exited", "retained_by_named_owner"})
```

There is deliberately **no** `transferred` member. A genuine ownership transfer would mean a concrete,
verifiable adoption of the live terminal by another identified owner — on the verified Orca contract
that is `worker-start --task <new task> --terminal <handle>` returning a ready state, which
`start_worker` then records through `_attach_terminal` as a new `owner_dispatch_id`
(`orca_runtime_harness.py:2074-2140`, `:1101-1125`). OS-31 has no code path that performs that
operation: pausing and abandoning both *stop* dispatching, so there is no receiving Task to adopt
into. Rather than name a disposition it cannot produce, the closed set omits it. If a future ticket
adds an adoption path it adds the member together with the receipt that proves it.

Evaluated in this order; the first that holds wins:

| # | disposition | holds when | who owns the terminal afterwards | AC-1 |
|---|---|---|---|---|
| 1 | `released` | `cleanup_authority == "authorized"` **and** the lifecycle intent is `release` **and** `release_terminal` returned a receipt whose `processAction` is in `PROCESS_TERMINATING_ACTIONS` (`orca_runtime_harness.py:353`, the D-6/R8-iii gate at `:1670-1680`) | nobody — the process is proven ended; `terminal_owner = ""` | discharged |
| 2 | `exited` | `process_liveness == "already exited"`, i.e. the observed `terminalResource.releaseState` / `terminalState` is one of `released` / `closed` / `exited` (`account_axes`, `:1630-1648`) | nobody — proven already exited; `terminal_owner = ""` | discharged |
| 3 | `retained_by_named_owner` | the terminal outlives the pause **and** the journal names a definite owner: `provenance_source == "journal"` **and** `terminal_role != "unknown_role"` **and** `terminal_origin != "unknown"` **and** `terminal_owner != ""`, where `terminal_owner` comes from the durable `PLANNED`/`INTENDED` provenance of §4.2.1 (the still-open owning dispatch when the terminal is being kept for reuse or retain, or the recorded `created_by` / owner class for a terminal this run never owned) | the named owner — unchanged by the pause, and **identified**, which is exactly what "not ambiguous" means | discharged |
| 4 | `residual` | none of the above: no nameable owner, or `provenance_source == "absent"` (a W-C orphan, or a foreign leg-(3) Task) | **unknown — and the design says so** | **not** discharged |

`residual` is not a pass. It is the honest name for the state iteration 2 was labelling
`transferred`, and it is admissible **only on the abandon path** (§9.2 step 6), where the disposition
is last-resort and must be able to complete. On the pause path a `residual` row raises instead:

```python
def terminal_disposition(row) -> str:            # pause_policy.py, pure
    if row["cleanup_authority"] == "authorized" and row["worker_resource"] == "release" \
       and row["recovery"].startswith("released:"):
        return "released"
    if row["process_liveness"] == "already exited":
        return "exited"
    if row["provenance_source"] == "journal" \
       and row["terminal_role"] != "unknown_role" and row["terminal_origin"] != "unknown" \
       and row["terminal_owner"]:
        return "retained_by_named_owner"
    return "residual"


def require_pause_disposition(row) -> str:       # pause_policy.py, the PAUSE-path gate
    disposition = terminal_disposition(row)
    if disposition in AC1_DISCHARGING_DISPOSITIONS:
        return disposition
    if row["provenance_source"] == "absent" and row["stage"] == "OPENED":
        raise PauseRefused("TERMINAL_ORPHAN_POSSIBLE", detail=...)   # W-C, §4.2.1
    raise PauseRefused("TERMINAL_OWNERSHIP_UNKNOWN", detail=...)     # includes intent_id + evidence
```

Read off the cases the two review rounds named:

- **`cleanup_authority == "unknown"`** does not pass on its own. It passes only through case 3, and
  only when the journal actually names an owner. `unknown` authority arising from `unknown_role` or
  `origin == "unknown"` (`cleanup_authority`, `orca_runtime_harness.py:482-497`) is exactly the case
  where no owner can be named — and that refuses the pause.
- **`process_liveness == "disputed"`** is admissible **only** through case 3. Disputed liveness with
  no identified owner refuses. Disputed liveness with an identified owner is not an *ownership*
  ambiguity: the row says who owns it, and the disputed reading is recorded as the reporting duty
  §4.2 step 3 already requires.
- **`not_authorized`** (`active_worker`, `run_owner_fixture`, `setup_terminal` — `NEVER_CLOSE_ROLES`,
  `:310-320`) passes cleanly through case 3, because a never-close role is precisely a role whose
  owner is known. Not closing a terminal was never the ambiguity; not knowing whose it is, was.
- **The Coordinator's own 12/12 empirical result** — `state=retained reason=external_terminal
  processAction=none` for every `worker-release` against live 1.4.197 — means case 1 is *rarely*
  reachable for a created-then-adopted terminal: the runtime keeps the process and D-6/R8-iii
  correctly refuses to call that a release. Case 3 is therefore the normal outcome on this runtime,
  and it is honest precisely because it claims only what it can show: the owner's identity, from our
  own durable journal, with no assertion that the runtime did anything.

Refusal is safe by construction: the run falls back to `BLOCK` / `BLOCKED`, which is the pre-OS-31
behaviour for the same decision, with no pause record written and no half-paused state. A pause that
cannot answer the ownership question does not happen.

#### 4.3 The rest of the PAUSE node, in order

5. **Publish the OS-30 request.** `approval_port.publish(run_id=..., sources=...)`, where `sources`
   are built by `clarification_protocol.terminal_block_sources(...)` from the decision ledger — the
   same call the harness already makes at `orca_runtime_harness.py:3005-3020`. Idempotent by
   construction: `_write_directory` is content-idempotent and raises on divergence
   (`clarification_protocol.py:343-350`), so a replayed publish returns the same `request_id`.
6. **Build `pause_binding`** (§3) and set `pending_clarification_id = request_id`.
7. **Set** `run_lifecycle = "WAITING_FOR_INPUT"`, `route_token = "PAUSE"`, and clear
   `pending_role` / `pending_intent` / `pending_event` / `intent_status`.
8. `PAUSE → TERMINAL`; `terminal_node` sees `route_token == "PAUSE"`, writes **no** `terminal_status`,
   and sets `terminal_reason = {"code": decision_state, "message": "WAITING_FOR_INPUT",
   "phase": current_phase}`.

#### 4.4 The commit point, and the two crash windows (C1 / C4)

The PAUSE node **cannot** write the Tier-2 record: it does not yet know its own `checkpoint_id`,
because LangGraph commits the checkpoint after the node returns. So the record is written by the
driver, immediately after `invoke` returns — which is precisely what makes C1 true by construction:

```python
# pause_runtime.finalize_pause(final_state, *, saver, pause_store, checkpoint_store_path)
if final_state["run_lifecycle"] != "WAITING_FOR_INPUT":
    return None
head = saver.head(final_state["thread_id"])                 # the committed pause checkpoint
record = build_pause_record(final_state, head,
                            saver.checkpoint_digest(final_state["thread_id"], head),
                            checkpoint_store_path)
return pause_store.create(record)                           # AFTER the checkpoint, never before
```

| crash window | resulting on-disk state | disposition |
|---|---|---|
| **before** the pause checkpoint commits | head is the pre-pause checkpoint; `run_lifecycle == "ACTIVE"`; no pause record; the `pause_binding` projection of the settlement rows is lost with the uncommitted state — **but the rows themselves stand in the durable journal of §4.2.1**, each written before the effect it describes | a **fresh process, holding none of the dead one's objects**, reconstructs the dispatch set from the three-legged `open_dispatches()` of §4.2.1 (journal ∪ durable runtime-state receipts ∪ `task-list --run`), re-seeds each terminal's provenance **from the journal** via `register_terminal` (never from `worker-show`, which does not carry it) using the plaintext handle leg (4) of §4.2.1a recovered — `terminal list` scoped by the **stable** `id:<repo-id>::<path>` selector the `PLANNED` row journalled before E1 (never the alias `current`, which the successor would re-resolve to its own worktree), normalised title match, `sha256` verification against the journalled digest, and a `worktree show` guard before any "absent" verdict is believed — re-drives the PAUSE node, and finishes every row to `DISPOSED`. Windows W-A, W-B, W-D and W-E of §4.2.1 are fully recovered with no leak (T-08). Window **W-C** — terminal created, digest not yet journalled — is **not** recoverable: leg (4) may return an unverifiable title candidate, but with no digest nothing can prove it, so the pause refuses with `TERMINAL_ORPHAN_POSSIBLE`, an abandon records the row `residual` and reports the candidate, and AC-1 is not claimed for that run. A row whose listing result is contradicted or ambiguous refuses with `TERMINAL_IDENTITY_UNVERIFIED` on the same principle. Repeated `account_dispatch` is read-only; every mutation is fenced by the row stage **and** the harness claim gate (`orca_runtime_harness.py:3508-3517`), so no dispatch is settled twice. |
| **after** the checkpoint commits, **before** the record write | head carries `WAITING_FOR_INPUT`; no pause record | C4: `reindex()` re-derives the record **from the checkpoint** under a claim, idempotently (T-09, T-45). |
| after both | fully paused | discovery finds it and validates C1–C3 (T-10, T-13). |

A pause record can therefore never name a checkpoint that does not exist (C1), and the only repair
direction is checkpoint → record (C4). The journal is orthogonal to all three windows: it is not an
execution-state authority, it never feeds the reconstructed `WorkflowState` (that stays the
checkpoint's exclusive job, PLAN F-001), and it is written only in the discover-and-finish direction.
Leg (4) is orthogonal in the same way and more weakly still: it persists nothing at all, it reads a
live listing, and its only outputs are a handle held in process memory and one closed-vocabulary
`handle_recovery` value on a row. Neither the journal nor the listing is ever consulted to decide
what the workflow does next.
A journal row that no longer corresponds to any live dispatch is finished to `DISPOSED` by the
recovery path and never resurrects work.

---

### 5. Discovery and exactly-once takeover

`launcher.run_cli` gains exactly **two** verbs (WU-6's scope wall; no run listing, no run
administration, no general Orca-independent orchestration CLI — R-11):

```
run_workflow.py discover [--artifact-base DIR] [--json]
run_workflow.py resume --run-id RUN_ID [--artifact-base DIR]
                       [--head-sha SHA --tree-digest DIGEST --dirty/--clean]
                       [--artifact-digest DIGEST]
                       [--cancel | --abandon] [--actor-id ID --actor-type human|service
                        --submission-id ID --reason TEXT]
                       [--observe-timeout SECONDS] [--json]
```

#### 5.1 Discovery

`discover_paused_runs(artifact_base)` sweeps `<artifact_base>/artifacts/runs/*/.pause_state.json`.
For each record it reports a `PausedRunListing`:

```python
{"run_id", "status", "pause_record_id", "current_phase", "round_kind", "request_id",
 "decision_item_ids", "owner_id", "lease_expires_at", "checkpoint_id",
 "verdict",            # RESUMABLE | <a PAUSE_REFUSAL_CODES value>
 "detail"}
```

`verdict` is computed **without taking the claim and without performing any effect**: schema
validity, `status == "WAITING_FOR_INPUT"`, and — when LangGraph is present — C1 and C2. A corrupt
record is listed with `PAUSE_RECORD_CORRUPT`, never skipped. Discovery is read-only and safe to run
concurrently from any number of processes.

A brand-new process needs nothing but `--artifact-base`: the run root, the record and the record's
`checkpoint_store_path` are the whole chain (AC-2, T-13).

#### 5.2 Takeover — the fence

`pause_runtime.takeover(run_id, *, pause_store, artifact_base) -> Takeover` performs exactly one
`pause_store.claim(run_id)` inside the flock critical section, so `lock → read → validate → claim →
persist` is atomic and two claimants produce exactly one winner.

```
claim_outcome                      | what the caller does
-----------------------------------+---------------------------------------------------------
CREATED / RESUMED                  | holds the lease; proceeds to C1/C2/C3 and then the effect
ALREADY_RESUMED                    | reports the winner's outcome; performs NO effect; exit 0
ALREADY_CANCELLED / ALREADY_ABANDONED | reports RUN_ALREADY_CANCELLED / _ABANDONED; NO effect; exit 0
raise PauseClaimHeld               | becomes an OBSERVER (below)
```

**Losing claimant, modelled on `_observe_then_take_over` (`executor.py:258-279`):**

```python
record = pause_store.observe(run_id, timeout_seconds=observe_timeout, poll_seconds=...)
# returns the settled record when the owner finishes (RESUMED/CANCELLED/ABANDONED),
# or None when the owner's lease lapses -> ONE takeover attempt, then the ladder above.
# raises PauseObservationTimeout at the deadline -> PAUSE_OBSERVATION_TIMEOUT, exit 1.
```

The loser performs **no** effect at any point: it never applies a response, never dispatches, never
writes a log row other than the `pause_takeover_refused` audit event. That is why the concurrent
race creates no duplicate Task/Dispatch (T-15, T-29) — the claim is taken strictly *before* any
external work, exactly as `RuntimeStatePort.claim` is.

The lease is renewed for the duration of a long resume by the existing
`lease_keeper.lease_keeper_factory(...)` over `RunPauseStatePort.heartbeat`, and a lost renewal
fails closed as `PAUSE_CLAIM_LOST` — the same shape as `IDEMPOTENCY_LEASE_LOST`.

#### 5.3 Reconstruction (REQ-1)

Under the claim, and only after C1 and C2 pass:

```python
saver = FileCheckpointSaver(resolve_store_path(record))
tuple_ = saver.get_tuple({"configurable": {"thread_id": record["thread_id"],
                                           "checkpoint_ns": record["checkpoint_ns"],
                                           "checkpoint_id": record["checkpoint_id"]}})
state = validate_state(dict(tuple_.checkpoint["channel_values"]),
                       expected_thread_id=record["thread_id"])
assert_c3(project_pause(state), record["projection"])
```

`record["projection"]` is **never** an input to `state`. The proof obligation is T-43: a second
variant of the test deliberately mutates the projection before reconstruction and asserts the
reconstructed state is unchanged (and that C3 then refuses, rather than the mutation being adopted).

---

### 6. Response application and resume — idempotency and exactly-once

#### 6.1 The three identities (WU-3, `pause_policy.py`)

```python
def pause_record_id(*, run_id, thread_id, request_id, decision_item_ids) -> str:
    return contracts.stable_id("pause", {"run_id": run_id, "thread_id": thread_id,
                                         "request_id": request_id,
                                         "decision_item_ids": sorted(decision_item_ids)})

def resume_bundle_id(*, run_id, request_id, pause_record_id, decisions) -> str:
    """The identity of ONE application of a COMPLETE decision bundle.

    ``decisions`` is the whole answer, not one item: a tuple of
    ``(decision_item_id, decision_id)`` pairs, sorted by ``decision_item_id``, covering
    exactly ``pause_binding["decision_item_ids"]`` (§6.3 already requires every item).
    """
    return contracts.stable_id("resume_bundle", {
        "run_id": run_id, "request_id": request_id, "pause_record_id": pause_record_id,
        "decisions": [list(pair) for pair in sorted(decisions)]})

def cancellation_id(*, run_id, pause_record_id, cancel_submission_id, cancel_kind) -> str:
    return contracts.stable_id("cancel_run", {...the four fields...})
```

**There is no per-item `resume_id`.** Resume is one graph re-entry — one effect — so its identity is
one identity, taken over the complete sorted item/decision set. Two consequences, both wanted:
a byte-identical replay of the same complete answer yields the same `resume_bundle_id` and is caught
as a replay (§6.4); a *different* answer to any single item yields a different id and is therefore
**never** mistaken for a replay of this one — it is caught as a second application attempt and
refused. A partial answer never produces an id at all, because §6.3 refuses before this point.

All three **deliberately exclude** `repository_binding`, `artifact_binding` and `phase_iteration` —
unlike `intent_id` (`contracts.py:225-240`). That exclusion is load-bearing twice over:
revalidation may legitimately move the bindings after the answer arrives (FI-10/I2), and a cancel
must remain possible after the head moved (CC-4). They are therefore stable across a process
restart and insensitive to binding changes, which is exactly what T-07 asserts.

#### 6.2 The application sequence

```
takeover (§5.2)  ->  C1, C2  ->  reconstruct (§5.3)  ->  C3
     -> read the OS-30 decision for EVERY item (§6.3) -- all-or-nothing, no partial bundle
     -> derive ONE resume_bundle_id over the complete sorted set; consult the applied set (§6.4)
     -> stale-source comparison (§3, §7)
     -> record_applied(one bundle entry) = {stage: RECORDED, items: <the whole sorted set>}
                                                            <-- ONE write, atomic, the dedupe key
     -> typed RESUME_PAUSE update + graph re-invoke on the same thread   <-- THE single effect
     -> applied[resume_bundle_id].stage = RESUMED; pause record status = RESUMED
     -> append run_resumed + run_status rows                <-- non-idempotent, written LAST, once
```

#### 6.3 Reading the decision, and the three fail-closed refusals

For each `decision_item_id` in `pause_binding["decision_item_ids"]`, via
`ArtifactHumanApprovalPort` (already implemented — no new OS-30 code):

| observation | code | outcome |
|---|---|---|
| no effective decision yet (`_effective_decision(...) is None`) | `RESPONSE_NOT_FOUND` | **not an error** — the run is simply still waiting. Claim released, record stays `WAITING_FOR_INPUT`, exit code 4. |
| the response record carries `stale: True`, or the item is absent from `_current_item_ids` | `RESPONSE_STALE_REVISION` | refused; no effect; the run stays resumable by a response against the current revision |
| two distinct effective `decision_id`s for one item, or `LineageFork` | `RESPONSE_CONFLICT` | refused; **never arbitrated by recency or timestamp**; requires an explicit disposition or a re-clarification |
| the item's lineage state is `cancelled` | `RESPONSE_ITEM_UNRESOLVED` | refused for resume; the run is disposed through §9, not resumed |
| an effective decision exists for **every** item | — | proceed |

Requiring *every* item is deliberate: a partially answered bundle is not a resumable decision, and
OS-30's own `resolved_items`/`promote` machinery is what asks the remaining ones. The refusals above
are evaluated over the **whole bundle before any entry is written and before any effect**, so a
two- or three-item OS-30 request behaves as one transaction end to end: the `RESPONSE_NOT_FOUND`
row is reached when *any* item lacks an effective decision, and `RESPONSE_CONFLICT` /
`RESPONSE_STALE_REVISION` / `RESPONSE_ITEM_UNRESOLVED` when *any* item is bad. Nothing downstream
ever sees a half-read bundle, so there is no per-item ordering to reason about.

#### 6.4 The applied set — one bundle entry, two stages, with the checkpoint as the tie-breaker

`record["applied"]` maps `resume_bundle_id` → **exactly one** closed entry that carries the whole
bundle:

```python
APPLIED_ENTRY_KEYS = ("resume_bundle_id", "request_id", "items", "stage",
                      "recorded_at", "resumed_at", "resumed_checkpoint_id")
APPLIED_STAGES = ("RECORDED", "RESUMED")
# items: tuple of {"decision_item_id", "decision_id"}, sorted by decision_item_id,
#        covering exactly record["projection"]["decision_item_ids"]
```

**Atomicity is structural, not disciplinary.** One bundle = one entry = one `record_applied` call =
one whole-record write under the `FilePauseRecordStore` flock critical section with `os.replace`
(§2.3, `durable_store` discipline). There is no window in which a subset of a bundle's items is
`RECORDED` and the rest is not, **because per-item entries do not exist**. That removes the partial-
write class of failures rather than defining recovery for it, which is why this is the shape chosen
over an N-entry batch/CAS.

The bundle entry is also the **single unambiguous effect owner**: the typed `RESUME_PAUSE` update
and the one `graph.invoke` re-entry belong to `resume_bundle_id` and to nothing else. No item owns
the effect; the bundle does.

`FilePauseRecordStore.record_applied` enforces both properties and is the only writer of `applied`:

| condition at write time | outcome |
|---|---|
| `entry["items"]` is not exactly the record's `decision_item_ids`, sorted, with one `decision_id` each | `PAUSE_LIFECYCLE_INCOHERENT` — refuses; an incomplete or over-complete bundle can never be recorded |
| `applied` already holds a **different** `resume_bundle_id` at `RECORDED` or `RESUMED` | `RESPONSE_CONFLICT` — a second, differing answer is never applied on top of the first, and never partially |
| the same `resume_bundle_id` already present | returns the stored entry unchanged (idempotent), and §6.4's replay table decides what the caller does |
| otherwise | writes the entry whole, under the lease token, in one atomic record write |

Replay behaviour, evaluated **before** any effect:

| stored stage | action |
|---|---|
| absent | proceed; write the one `RECORDED` bundle entry under the lease, then perform the single effect |
| `RESUMED` | `RESPONSE_ALREADY_APPLIED` — no second Task/Dispatch, no artifact write, **no second pair of `run_end`/`run_resumed` rows**; report the recorded `resumed_checkpoint_id` and exit 0 (AC-4, T-16, T-23) |
| `RECORDED` | the crash window between the dedupe write and the effect. **Ask the authority, not the record:** if `saver.head(thread_id)` still carries `run_lifecycle == "WAITING_FOR_INPUT"` with the same `pause_record_id`, the resume never committed → re-drive it (the typed update is a pure function of `(checkpoint, the complete decision bundle)`, so re-driving is byte-identical, and the bundle is complete by construction). If the head has moved past the pause, the resume did commit → promote the entry to `RESUMED` and report `RESPONSE_ALREADY_APPLIED`. |

**Every partial-write window, enumerated** — there are four, and none of them is "some items
applied":

| crash point | on-disk state | recovery |
|---|---|---|
| before `record_applied` | no entry; head still at the pause | nothing happened; the entry precedes the effect, so re-deriving the bundle and proceeding is safe. If the answer changed meanwhile, the id differs — correctly a different application, caught by the `RESPONSE_CONFLICT` row above |
| mid-`record_applied` | impossible to observe: the record is written whole under flock + `os.replace`, so a reader sees the old record or the new one | — |
| after `RECORDED`, before the graph re-entry commits | entry `RECORDED`; head still `WAITING_FOR_INPUT` with the same `pause_record_id` | re-drive the single effect (the `RECORDED` ladder above) |
| after the graph re-entry commits, before promotion or before the log rows | entry `RECORDED`; head moved past the pause | promote to `RESUMED`, report `RESPONSE_ALREADY_APPLIED`; the `run_resumed`/`run_status` rows are written only on the `RECORDED → RESUMED` transition, so exactly one pair exists |

Writing the dedupe key **before** the effect is the same rule ANALYSIS §10f states for cancel, and
the `RECORDED` reconciliation is the same recovery-ladder shape `executor._recover` already uses:
never re-run an effect you cannot prove is absent — and here the checkpoint *can* prove it, because
it is the authority.

#### 6.5 Why a replay creates no duplicate artifact

Three independent reasons, none of which relies on discipline:
- the applied set short-circuits before any effect (§6.4);
- OS-30 `_write_directory` is content-idempotent and raises on divergence
  (`clarification_protocol.py:343-350`), so even a re-published request is byte-identical;
- `run_logging` artifacts use `rename`-onto-existing-directory, explicitly "NOT `os.replace`"
  (`run_logging.py:1913-1919`), and there is no `force`/`--overwrite` anywhere (`:1046-1049`,
  `:1067-1071`). Nothing in this design adds one.

#### 6.6 Concurrent resume race — the outcome

Two processes, same paused run: exactly one obtains `CREATED`/`RESUMED` from
`pause_store.claim`; the other raises `PauseClaimHeld`, observes, and reports the winner's settled
outcome. Because the claim precedes every effect, the loser creates no Task, no Dispatch, no
artifact and no log row. Should the winner die mid-resume, its lease lapses, the observer takes over
once, and the `RECORDED` reconciliation of §6.4 decides whether the effect landed — never a blind
re-run.

This holds unchanged for a two- or three-item bundle, and that is the point of §6.4's single entry:
the two contenders are not racing per item, they are racing for one lease over one bundle entry that
is written in one atomic record write. Even two contenders carrying *different* answers to the same
items cannot interleave — the loser never reaches `record_applied`, and if it later takes over a
lapsed lease its differing `resume_bundle_id` meets the `RESPONSE_CONFLICT` row of §6.4 rather than
overwriting or partially amending the winner's application (T-46).

---

### 7. Stale-source revalidation

#### 7.1 The comparison and its outcome

Performed on the **reconstructed** state (§3's table). Then:

```python
def resume_reentry(state, *, current_repository, current_artifact, current_policy_digest):
    """Pure. Returns (round_kind, current_phase, correction_queue, floors, generation_delta)."""
```

| observation | re-entry |
|---|---|
| all three unchanged | **redo the paused round**: `round_kind` and `current_phase` unchanged; the next `route()` yields `PREPARE_WORKER` / `PREPARE_PHASE_REVIEWER` / `PREPARE_CORRECTION` / `PREPARE_REVALIDATION` exactly as it would have before the pause. The answer is consumed as context by the re-dispatched agent; it never substitutes for the round. |
| any of the three changed | **correction re-entry**: `round_kind = "CORRECTION"`, `correction_queue = [responsible_phase]`, `correction_index = 0`, `current_phase = responsible_phase`; the next `route()` yields `PREPARE_CORRECTION`. `advance_phase_node` then computes `downstream_revalidation_set(corrected_phases, requested_phases, risk)` (`executor.py:431-437`, `routing.py:14-19`) exactly as it does for any correction — **high risk only**, unchanged. |

This is D3: the run re-enters through the existing, tested correction/revalidation machinery
(`responsible_phases`, `active_correction_phase`, `advance_phase_node`, `PREPARE_CORRECTION` /
`PREPARE_REVALIDATION`), and never by un-setting a terminal status.

#### 7.2 Choosing the responsible phase

`pause_binding["responsible_phase"]` is fixed when the pause is created, not guessed at resume time:

1. each blocked OS-29 record carries `responsible_phase` (`decision_gate.py`'s required field set),
   and each OS-30 item carries `phase` cross-checked against its `source_ledger_key`
   (`clarification_protocol.py:418-419`, `_ledger_parts` `:384-388`);
2. `ClarificationSource.phase` is therefore available for every source at publish time;
3. the binding stores the **earliest** of those phases in `requested_phases` order — the same
   ordering rule `routing.responsible_phases` uses (`routing.py:22-29`) — so revalidation starts as
   early as the change requires and never later;
4. when no source names a phase, it falls back to the paused `current_phase`.

A resume whose `requested_phases` or `risk` differ from the checkpointed ones is **refused**
(`PAUSE_LIFECYCLE_INCOHERENT`), not revalidated: those are launch parameters, not sources.

#### 7.3 Phase-pass currency (FI-9 / R-4 / AC-6), without breaking forward runs

`WorkflowState` gains two more fields — declared here rather than in §1.2 because they exist for
this rule alone:

```python
binding_generation: int                 # 0 in every run that never resumed
phase_pass_floor: dict[str, int]        # phase -> the generation its pass must carry; default {}
```

- `executor._pass_record` (`executor.py:340-358`) gains `"binding_generation": state["binding_generation"]`
  on every pass it writes.
- a resume that detected a changed source increments `binding_generation` by 1 and sets
  `phase_pass_floor[p] = binding_generation` for the responsible phase **and**, at high risk, for
  every phase in `downstream_revalidation_set([responsible_phase], requested_phases, risk)` — that
  is, for exactly the phases the engine will actually re-run.
- `routing.all_phase_passes_current` (`routing.py:53-54`) becomes a real currency check:

```python
def all_phase_passes_current(state) -> bool:
    floors = state.get("phase_pass_floor") or {}
    for phase in state["requested_phases"]:
        record = (state["phase_passes"] or {}).get(phase)
        if record is None:
            return False
        try:
            phase_pass_binding(state, phase)          # FI-9: the dead helper becomes production code
        except ValueError:
            return False                              # a pass with no reviewed_binding is not a pass
        if record.get("binding_generation", 0) < floors.get(phase, 0):
            return False
    return True
```

**Why this shape and not "compare every pass to the current binding".** A pass is legitimately
recorded against the tree that existed when the phase passed; in an ordinary forward run the head
moves with every Worker settlement, so a naive equality check would make `COMPLETE` unreachable and
redden the suite. The floor expresses the property AC-6 actually needs — *no pass predating a change
the engine has not re-run may satisfy completion* — and is a no-op (`floors == {}`, all generations
`0`) for every run that never paused, so the 2014 existing tests are unaffected by construction.

**Stated limitation, not hidden.** At medium and low risk `downstream_revalidation_set` returns `()`
by design (`routing.py:15`), so a post-resume floor is raised only for the responsible phase itself.
That is a pre-existing engine property (downstream revalidation is high-risk-only), not something
OS-31 changes; raising floors for phases the engine will never re-run would make medium-risk resumed
runs uncompletable. The Final Review binding check (`final_review_binding_current`, `routing.py:57-68`)
still applies at every risk level and still gates `COMPLETE`.

`verify_final_review_binding` (`routing.py:71-78`, today test-only) becomes production-reachable:
`terminal_node` calls it when stamping `COMPLETED` and projects a raised `ValueError` onto `BLOCKED`
with the raised code (`NO_FINAL_REVIEW_PASS` / `STALE_FINAL_REVIEW_BINDING`). Behaviour on a green
run is identical, because `route()` already required `final_review_binding_current` before emitting
`COMPLETE` (`routing.py:112-114`); it is a fail-closed cross-check at the stamping point.

---

### 8. Gate preservation — a property of the re-entry path, not a promise

Three structural facts, each with a proving test. None of them is "the implementation will be
careful".

**8.1 There is no operation that clears a terminal status.** `WAITING_FOR_INPUT` is not a member of
`TERMINAL_STATUSES`, so a paused run never had one to clear. `terminal_status` is written by exactly
one function — `terminal_node` — and no pause, resume, cancel or abandon code path calls it with a
value it did not compute from `route_token`. The `SETTLED ⇒ terminal_status is not None`
biconditional (§1.2) makes a state that "was terminal and now is not" unrepresentable: any such
merge fails `validate_state`. (T-34)

**8.2 The raw ingress is closed against the fields that could forge a gate.** `WU-9` adds to
`graph.py`:

```python
PROTECTED_STATE_FIELDS = frozenset({
    "terminal_status", "terminal_reason", "run_lifecycle", "pause_binding",
    "phase_passes", "phase_pass_floor", "binding_generation",
    "processed_command_ids", "processed_event_ids",
})
```

`GuardedWorkflowGraph._guard_update` / `_aguard_update` raise
`StateError("MALFORMED_STATE:protected field:<name>")` when a raw `values` mapping names any of
them. Today the guard checks only that key *names* are inside `CLOSED_STATE_FIELDS`
(`graph.py:143-146`), and `terminal_status` is one of them — the observed steps 6–7 of ANALYSIS.
After WU-9, resume and cancel are expressible **only** through the typed commands, and a forged
`phase_passes` entry or a hand-written `terminal_status = None` is refused at the boundary. (T-33, T-34)

**8.3 The typed commands cannot touch a gate input.** The new entries in `state.UPDATE_COMMANDS`
name their exact field sets, and `typed_update` refuses a missing or extra field
(`state.py:296-317`):

```python
"RESUME_PAUSE":          ("run_lifecycle", "pause_binding", "decision_state",
                          "decision_reason_code", "pending_clarification_id",
                          "round_kind", "current_phase", "correction_queue",
                          "correction_index", "binding_generation", "phase_pass_floor",
                          "repository_binding", "artifact_binding"),
"REQUEST_DISPOSITION":   ("pause_binding",),
```

Neither writes `worker_result`, `reviewer_result`, `final_reviewer_result`, `phase_passes`,
`final_review_iterations` or any budget. `RESUME_PAUSE` may *raise* a floor and *bump* a generation,
never lower either — enforced by a guard inside `typed_update`
(`MALFORMED_UPDATE_COMMAND:RESUME_PAUSE:generation must not decrease`), so it can only make
completion harder, never easier.

**Consequence.** After a resume, `route()` is the same pure function over the same predicates it was
before the pause: `phase_gate` still requires a `reviewer_result` at medium/high risk
(`routing.py:39-43`); `final_gate` still requires a Final Review verdict (`routing.py:46-50`);
`COMPLETE` still requires `all_phase_passes_current(state) and final_review_binding_current(state)`
(`routing.py:112-114`). And `prepare_intent_node` clears `worker_result`/`reviewer_result` on every
Worker round (`executor.py:85`), so a resumed correction cannot inherit a stale Reviewer verdict.
Final Adversarial Review remains mandatory and identical at every risk level (`SKILL.md:2416`) —
resume touches none of those predicates. (T-21, T-22)

---

### 9. Cancel and abandon (X3: TC-1 … TC-4)

#### 9.1 What changes where (the ANALYSIS constraint, answered exactly)

`RUN_STATUS_VALUES` refuses `CANCELLED`/`ABANDONED`/`WAITING_FOR_INPUT` at three enforcement points
plus a CLI `choices`. All four are satisfied by the **single tuple edit** at `run_logging.py:116`:

| point | file:line | change |
|---|---|---|
| the tuple | `run_logging.py:116` | add the three values |
| `log_run_status` validator | `run_logging.py:585-587` | none — reads the tuple. **Re-verified, not loosened**; T-01 asserts an unrecognised value is still refused |
| harness repeat | `orca_runtime_harness.py:2930-2934` | none — reads `run_logging.RUN_STATUS_VALUES`. Re-verified |
| CLI `choices` | `run_logging.py:3269` | none — `choices=RUN_STATUS_VALUES` |

Two additional harness edits are required and are **not** automatic:
- `OrcaRuntimeHarness.log_run_status` publishes clarifications only for `"BLOCKED"`
  (`orca_runtime_harness.py:3004-3005`); it must publish for `status in ("BLOCKED", "WAITING_FOR_INPUT")`,
  because on the Skill-document path `WAITING_FOR_INPUT` is now the status a pausable decision block
  writes;
- the `COMPLETED` pre-completion gate (`:2953-3002`) is untouched: `CANCELLED`/`ABANDONED`/
  `WAITING_FOR_INPUT` are not `COMPLETED` and must stay writable, exactly as `BLOCKED`/`ERROR`/
  `ESCALATED` are ("ONLY COMPLETED is gated", `:2949-2951`).

#### 9.2 The disposition request and the DISPOSE node

`REQUEST_DISPOSITION` writes into `pause_binding["disposition"]`, a closed dict:

```python
DISPOSITION_KEYS = ("kind", "cancellation_id", "actor_id", "actor_type",
                    "submission_id", "reason", "requested_at")
# kind in ("CANCEL", "ABANDON"); actor_type in ("human", "service")
```

Per OD-3 an abandon is invoked only by an **explicit human instruction** arriving through the same
channel any decision arrives through, and the actor identity that authorised it is recorded. There
is no operator role, no privileged actor class, and no automatic or timeout-driven invoker anywhere
in this design.

`route()` sees the disposition (above the pause check) and emits `CANCEL` / `ABANDON` → `DISPOSE`.
The DISPOSE node, under the run-scoped claim, in this order:

1. **X1 — OS-30 request cancellation, for `CANCEL` only.**
   `approval_port.ingest(run_id=..., request_id=..., decision_item_id=None,
   submission=ResponseSubmission(..., cancel=True))`, which fans out over every item with
   deterministic child tokens (`clarification_protocol.py:936-949`), so whole-request replay is
   byte-exact and the lineage carries a real `decision_cancelled` per item (`:1041-1051`).
   `resolved_items` then correctly refuses to promote dependents (`:673-679`).
   **`ABANDON` does not run X1.** TC-2/TC-3 have no human answer to record, and X1 would write a
   response record and a `decision_cancelled` event attributed to a decision nobody made — exactly
   what TT-2 forbids. The two-authorities hazard ANALYSIS §10h.1 warned about is closed instead by
   step 2 plus WU-12 (§9.5), which makes the run-level disposition the fact `promote_pending`
   consults. This is a DESIGN decision, stated so a reviewer can weigh it: fabricating no decision
   is worth more than uniformity between the two dispositions, and the promotion hazard is closed by
   the mechanism that was going to be needed anyway.
2. **Mark the pause record terminal.** `pause_store.settle_disposition(run_id, disposition,
   lease_token)` → `status = "CANCELLED" | "ABANDONED"`. Discovery can never re-adopt it, and
   `claim` returns `ALREADY_CANCELLED`/`ALREADY_ABANDONED` thereafter (R-14, T-33).
3. **Freeze, never re-validate, the bindings.** No head/artifact/policy comparison runs on this
   path. A moved head is not a reason to refuse a cancel (CC-4) — this is the exact opposite of §7's
   rule for resume, and the pair is asserted by T-19 (resume refuses) and T-26 (cancel succeeds).
4. **Retire the checkpoint.** `saver.retire_thread(thread_id, reason=cancellation_id)`. Never
   delete: the artifacts are immutable and the checkpoint is the audit evidence for what was
   disposed.
5. **Close the timing scopes and write the second `run_end`.**
   `tracker = RunTimingTracker.load(run_id, base=artifact_base)` → `tracker.close_all()` →
   `tracker.save()`, then
   `run_logging.log_run_status(run_id, status, base=..., reason=..., run_started_at=tracker.run_started_at, risk=...)`.
   `.timing_state.json` is the only place a new Coordinator can recover `run_started_at`
   (`run_logging.py:570-578`, `:600-601`, `:886-899`), so this is what stops the OS-19 defect of a
   blank `started_at` and a blank `duration_s` (R-17, T-31).
6. **TC-3 only (crash inside pause). Residual dispatches, under the same exit invariant.**
   Residual dispatches are discovered by the durable three-legged `open_dispatches()` of §4.2.1 —
   not from process memory, so a Coordinator that never ran the original dispatch still finds them —
   and are disposed through `settlement_port.recover_dispatch` (`worker-abandon` → `worker-release`,
   or `task-update --status failed` when unsupervised), each accounted **recovered, not settled**,
   `worker_done` count 0, no role promotion. Closing a terminal is still forbidden: a residual
   `active_worker` terminal is in `NEVER_CLOSE_ROLES` and is permanently `not_authorized`.

   **Being un-closable is not being unowned.** §4.2.2's ordered table is then applied to every
   residual row, exactly as at pause, and every row reaches a `TERMINAL_DISPOSITIONS` value:
   `released` when a release receipt proved a terminating `processAction`;
   `exited` when the post-recovery re-observation shows the terminal already gone;
   `retained_by_named_owner` when the durable journal names a definite owner (an `active_worker`
   terminal's owner *is* identified — it is the dispatch recorded in `owner_dispatch_id` /
   `created_by`, which is what makes a never-close role a *known*-owner role, not an ambiguous one).

   **What abandon does with a row that reaches none of them — the honest boundary.** Iteration 2
   wrote such a row `terminal_disposition = "transferred"`, `terminal_owner = "actor:<actor_id>"`
   and called that a handoff. It is not. Writing an actor id into a JSON field and naming the
   terminal in a report is an **audit action**: no adoption, no registration, no capability
   transfer, no acknowledgement, and nothing the named human can act on beyond being told. The
   review is right, and this design withdraws the claim rather than defending it.

   The verified Orca contract offers exactly one operation that genuinely moves ownership of a live
   terminal — `worker-start --task <task> --terminal <handle>` reaching a ready state, which
   `_attach_terminal` then records as a new `owner_dispatch_id` (`orca_runtime_harness.py:2074-2140`,
   `:1101-1125`) — and abandon has no receiving Task to adopt into, because abandon is the
   disposition that *stops* dispatching. Manufacturing one would be dispatching work nobody asked
   for. So no transfer is available here, and none is claimed.

   Such a row is therefore written `terminal_disposition = "residual"`,
   `terminal_owner = ""`, `recovery = "residual:<cancellation_id>"`, plus the evidence a human needs
   to act: `terminal_title`, `terminal_digest` (when the handle was journalled), `task_id`,
   `dispatch_id`, `provenance_source` and the last observation. The abandon **completes** — it is the
   last-resort disposition and must be able to — but:

   - the pause record and the disposition record carry `ac1_discharged: false`;
   - the abandon report and the `run_end` reason **enumerate every residual terminal**, with a count,
     and state plainly that these terminals were neither released, nor proven exited, nor transferred,
     and that a human must dispose of them;
   - a `run_abandoned` ORCHESTRATOR_LOG row carries `residual_terminal_count` in `detail`, so the
     residue is a machine-readable fact and not only prose;
   - **OS-31 does not claim AC-1 for that run.** AC-1 is claimed only when every row reached a member
     of `AC1_DISCHARGING_DISPOSITIONS`.

   "No ambiguous terminal ownership" is therefore satisfied — and *asserted* — only where it is
   actually true: every terminal released, proven exited, or retained by an owner the durable journal
   names. Where the platform cannot give that guarantee, the run says so instead of claiming it
   (CC-5, T-27, T-48). This is the asymmetry with pause, stated as a pair: pause refuses a row it
   cannot discharge (`TERMINAL_OWNERSHIP_UNKNOWN` / `TERMINAL_ORPHAN_POSSIBLE`), abandon records it
   as `residual` and reports it; neither calls it discharged.
7. `DISPOSE → TERMINAL`; `terminal_node` stamps `CANCELLED` / `ABANDONED` and `run_lifecycle =
   "SETTLED"`.

#### 9.3 TC-4 — cancel racing a resume

Both contend for the same run-scoped lease in `pause_store.claim`. Exactly one wins; the loser
observes the settled record and reports `RUN_ALREADY_CANCELLED` or `ALREADY_RESUMED` without
performing any effect. This is the same fence as §5.2 and needs no separate mechanism — which is the
point of making the pause record the single run-scoped arbiter. (T-29)

#### 9.4 Replay safety of the *effects*, not just the record

Ordering inside DISPOSE is chosen so the one non-idempotent step is last and guarded:
X1 is replay-exact by construction; `settle_disposition` is idempotent (a record already carrying
the same `cancellation_id` returns `ALREADY_CANCELLED` and mutates nothing); `retire_thread` is
idempotent; and the **append-only log rows are written only on the transition into the terminal
status**, i.e. only when `settle_disposition` reported a state change. A replayed cancel therefore
produces one byte-identical set of OS-30 cancel artifacts and **no second pair of `run_end` rows**
(T-28). A `cancellation_id` that differs from the stored one on an already-disposed run is refused
with `RUN_ALREADY_CANCELLED`, never applied as a second disposal.

#### 9.5 WU-12 — no post-cancel clarification republication

`ArtifactHumanApprovalPort.promote_pending` (`clarification_protocol.py:662-671`) is run by the CLI
after **every** response including a cancel (`:1336-1338`) and deliberately reads no run status,
because none was available to it. It gains one durable run-level check:

```python
def promote_pending(self, run_id: str) -> PublishResult:
    if run_disposition(self.artifact_base, run_id) is not None:   # CANCELLED or ABANDONED
        return PublishResult((), (), "EXISTING")
    ...unchanged...
```

`run_disposition` is a small, dependency-free reader of `.pause_state.json`'s `status` and
`disposition` fields, implemented **inside `clarification_protocol.py`** (which imports nothing from
`scripts/` — see its own module docstring rule) as a ~15-line closed-schema read. It returns `None`
for any run with no pause record, so behaviour for a live run is bit-for-bit unchanged (T-07's
sibling assertion, TT-7).

#### 9.6 Append-only audit and timing evidence (AC-7)

The `--event` column has no `choices` by design (`run_logging.py:144`), so the event half needs no
schema change. New named constants in `run_logging.py`, mirroring `EVENT_DECISION_BLOCK` (`:150`):

```python
EVENT_RUN_PAUSED              = "run_paused"
EVENT_RUN_RESUMED             = "run_resumed"
EVENT_RUN_RESUME_REFUSED      = "run_resume_refused"
EVENT_RUN_CANCELLED           = "run_cancelled"
EVENT_RUN_ABANDONED           = "run_abandoned"
EVENT_PAUSE_SETTLEMENT        = "pause_settlement_accounted"
EVENT_PAUSE_TAKEOVER          = "pause_takeover_claimed"
EVENT_PAUSE_TAKEOVER_REFUSED  = "pause_takeover_refused"
```

| moment | ORCHESTRATOR_LOG | TIMING_LOG | scopes |
|---|---|---|---|
| pause | `pause_settlement_accounted` (one row per dispatch, with the four axes in `detail`), then `run_paused`, then `run_end` / `result=WAITING_FOR_INPUT` | `run_paused`, `run_end` / `detail=WAITING_FOR_INPUT` with `started_at` from the tracker | phase/iteration scopes stay **open** — the run continues later, and `.timing_state.json` preserves them across the process boundary, which is exactly what it exists for |
| takeover | `pause_takeover_claimed` / `pause_takeover_refused` (reason code in `detail`) | — | — |
| resume | `run_resumed`, then the ordinary rows of the re-entered round | `run_resumed` | the reloaded tracker continues the same open scope |
| refusal | `run_resume_refused` with the named reason code | — | unchanged |
| cancel / abandon | `run_cancelled` / `run_abandoned`, then a **second** `run_end` / `result=CANCELLED|ABANDONED` | `iteration_end`, `phase_end`, second `run_end` with non-blank `started_at` | `close_all()` |

The second `run_end` is contract-legal: "run_end는 terminal이 아니다 … 마지막 `run_status` row를
authoritative status로 삼는다 … 뒤의 run_end가 앞의 것을 대체한다" (`SKILL.md:1566-1572`), anchored
by `validate_skills.py:2881`, `:2925-2932`.

#### 9.7 WU-11 — reader conformance for the second row

Every `run_end` reader is audited against the last-row-wins rule. The one known offender,
`verify_full_workflow_example.py:262-267`, uses
`any(row["event"]=="run_end" and row["result"]=="COMPLETED" ...)` and is rewritten as:

```python
statuses = [row for row in rows if row["event"] == "run_end"]
authoritative = statuses[-1]["result"] if statuses else None
```

Anchor-sentence validation is unchanged; reader conformance is a **test** obligation (T-30).

---

### 10. Engine / adapter boundary (D1, REQ-11)

#### 10.1 Who owns what

| concern | module | imports `langgraph`? | imports Orca? |
|---|---|---|---|
| lifecycle states, transitions, guards, reason codes, identities, `project_pause`, `policy_digest` | `pause_policy.py` | **no** | **no** |
| Tier-2 record, claim/lease fence, discovery sweep | `pause_store.py` | **no** | **no** |
| flock + atomic replace primitives | `durable_store.py` | **no** | **no** |
| Tier-1 durable checkpointer | `checkpoint_store.py` | **yes, by design (OD-4)** | no |
| pause finalisation, C1–C4, takeover, reconstruction, resume/dispose orchestration | `pause_runtime.py` | yes (it opens the saver) | no |
| routing tokens, admissibility | `routing.py` | no | no |
| PAUSE/DISPOSE nodes | `executor.py` | no | no |
| translation of lifecycle **signals** only | `orca_adapter.py` / `fake_adapter.py` | no | adapter-specific |

An import-isolation test (T-35) asserts that importing `pause_policy` and `pause_store` with
`langgraph` absent succeeds **and** that neither transitively imports `checkpoint_store`.

#### 10.2 The two ports the adapter may translate

`ports.py` gains `RunPauseStatePort` (§2.3) and:

```python
@runtime_checkable
class LifecycleSettlementPort(Protocol):
    """Settle a dispatch and account terminal ownership, for pause and disposal.

    AgentExecutionPort.interrupt (ports.py:23 -> `worker-interrupt`) cannot express this
    (ANALYSIS P3): interrupting is not settling, and it says nothing about the four axes.
    Declared by an adapter as the capability "lifecycle_settlement"; an adapter that cannot
    honour it must not declare it, and pause then correctly falls back to BLOCK.
    """
    def open_dispatches(self) -> tuple[str, ...]:
        """Every dispatch of THIS run that is not yet finished, reconstructed durably.

        Must be answerable by a process that holds none of the objects of the process
        that created the dispatches (§4.2.1). An implementation that can only read its
        own memory does not satisfy this method and must not declare the capability.
        A source that cannot be read is "unknown", never "empty": raise rather than
        return a short tuple.
        """
    def recover_handle(self, intent_id: str) -> Mapping[str, Any]:
        """Resolve this row's live terminal handle, or say why it cannot (§4.2.1a).

        Returns {"handle": str | None, "handle_recovery": <HANDLE_RECOVERY_OUTCOMES member>}.
        Read-only: enumerates and verifies, mutates nothing, and is safe to repeat.
        A handle is returned ONLY for "listing_verified" -- i.e. only when a durable
        digest proved it. "listing_candidate" reports a title match with no verifier and
        MUST NOT be acted on; it exists so an abandon report can name the terminal.
        A source that cannot be read raises rather than returning "not_listed":
        unreadable is unknown, never empty.
        """
    def account_dispatch(self, intent_id: str) -> Mapping[str, Any]: ...   # read-only, no mutation
    def recover_dispatch(self, intent_id: str, *, reason: str) -> Mapping[str, Any]: ...
    def release_terminal(self, intent_id: str, *, authority: str) -> Mapping[str, Any]: ...
```

`account_dispatch` returns exactly a `SETTLEMENT_ROW_KEYS` row minus `accounted_at` (the engine
stamps that from its clock, so adapters need no clock).

**`OrcaAdapter` translation, and nothing more:**

| port method | Orca translation |
|---|---|
| `open_dispatches` | the three-legged reconstruction of §4.2.1 — durable journal rows not at `DISPOSED`, ∪ durable `FileRuntimeStateStore` receipts with no journal row, ∪ `task-list --run` Tasks whose parsed spec `intent_id` is in neither. **Never `self._receipts`**, which is process memory (`orca_adapter.py:21`) and is used only as a same-process fast path that the durable legs always subsume |
| journal `PLANNED` write | `OrcaAdapter.start`, **before** `create_task` — the intended `terminal_role` / `terminal_origin` / `terminal_intended_role` / `created_by` / `terminal_title`, none of which is read from the runtime |
| journal `OPENED` write | the existing `_record_receipt` site (`orca_adapter.py:117`), after `create_task` returns and **before** `terminal create` — `task_id` |
| journal `INTENDED` write | the new `terminal_observer` seam on `run_existing_task`, invoked with the handle after `create_fake_terminal` returns and **before** `start_worker` — `terminal_digest`, `provenance_source = "journal"`. The handle itself is still never persisted |
| `recover_handle` | `harness.list_terminals(worktree=row["terminal_worktree"])` → `terminal list --worktree <w> --json`, where `<w>` is the **stable** `id:<repo-id>::<path>` selector journalled before E1 and passed **verbatim** — the identical string E2 was given, never re-resolved and never the alias `current`. Reads only `handle`, `title` and `orphaned` from each element; then `normalize_terminal_title` matching on `row["terminal_title"]` and **verification** against `row["terminal_digest"]`, per the §4.2.1a decision table. Before any zero-candidate outcome is reported, `harness.resolve_worktree(row["terminal_worktree"])` → `worktree show --worktree <w> --json` must return `ok:true` with `result.worktree.id` byte-identical to the recorded id; otherwise the outcome is `scope_unresolved` / `DISPATCH_UNACCOUNTED`, because an empty listing under an unresolvable scope is unknown, not empty. Two new harness methods, two new verbs, zero mutations |
| provenance for a recovered row | **the journal, always.** `harness.register_terminal(handle, role=row["terminal_role"], origin=row["terminal_origin"], intended_role=..., owner_dispatch_id=..., created_by=...)` (`orca_runtime_harness.py:1042`) re-seeds the process-local ledger from the durable row. `dispatch-show --task` (`:2278`, `:3548`) and `worker-show --dispatch --json` (`:1245`, `:3533`) supply **only** `dispatch_id` and liveness — never role or origin, which their verified response shapes do not contain. A row with `provenance_source = "absent"` is not re-registered: it reads back `unknown_role`/`unknown` and §4.2.2 refuses it |
| `account_dispatch` | `harness.account_axes(task_id, dispatch_id, terminal, supervised=..., observation=..., task_status=..., lifecycle=...)` — **already exists** (`orca_runtime_harness.py:1607`) and issues zero Orca commands; the adapter maps its 5-tuple onto the row and hashes the terminal handle into `terminal_digest` |
| `recover_dispatch` | the observed sequence at `orca_runtime_harness.py:3546-3571`: `worker-show` → `worker-abandon` (only when the state is in `UNSETTLED_WORKER_STATES`) → `worker-release`; or `task-update --status failed` when unsupervised |
| `release_terminal` | the harness release path, called **only** when `cleanup_authority == "authorized"` and the lifecycle intent is `release` |

The journal writes at dispatch time are the only change OS-31 makes to the *non*-pause path, and they
are deliberate: dispatch time is the only moment terminal provenance exists anywhere, and F-002 is
unfixable without capturing it **before** each effect rather than after. They add no Orca command.
They require five additive seams in the harness, all defaulted or purely new so every existing call
site binds unchanged: (i) `run_existing_task` gains keyword-only
`terminal_observer: Callable[[str], None] | None = None`, invoked with the handle between
`create_fake_terminal` and `start_worker` (`orca_runtime_harness.py:3196-3205`) — it issues nothing
and observes one string; (ii) `create_fake_terminal` gains keyword-only `title: str | None = None`
(default preserves the existing `fake-{role}-{iteration}` string exactly, `:1998-2003`) with a
matching `terminal_title` passthrough on `run_existing_task`, so E2 actually sends the run-unique
title the `PLANNED` row recorded; and (iii) a new read-only
`list_terminals(*, worktree: str, limit: int | None = None) -> tuple[dict, ...]` that issues
`terminal list --worktree <w> --json` through the existing `self.call` path and returns the array
verbatim; and (iv) a keyword-only `worktree: str = "current"` on `create_fake_terminal` (default
preserves today's hard-coded `--worktree "current"` exactly, `:2000-2003`) with a matching
`terminal_worktree` passthrough on `run_existing_task`, so E2 creates the terminal in the stable
scope the `PLANNED` row journalled. A read-only `resolve_worktree(selector) -> dict | None` issuing
`worktree show --worktree <s> --json` supplies §4.2.1a's scope guard. Only `list_terminals` and
`resolve_worktree` add verbs, and both are reads.

`OrcaAdapter.capabilities()` gains `"human_approval"` and `"lifecycle_settlement"`. It still does
**not** declare `external_resume` — that stays withheld with its existing rationale
(`orca_adapter.py:31-38`), which is precisely why pause must settle first (§4.2).

**Orca grammar discipline.** `SKILL.md:2396` forbids *guessing* Orca CLI grammar — it does not forbid
using a verb whose grammar and response were verified. This design commits to the verbs already
executed against a real runtime in `orca_runtime_harness.py` — `worker-show`, `worker-abandon`,
`worker-release`, `task-update`, `task-list`, `dispatch-show`, `terminal create/send/wait/close` —
plus `worktree current`, which the harness **already** executes in `validate_orca_contract`
(`orca_runtime_harness.py:1754-1759`) and whose `result.worktree.id` this design now also journals,
plus exactly two additions, both **reads**.

`terminal list`: grammar read from `orca terminal list --help`
(`[--worktree <selector>] [--limit <n>] [--include-visual-layouts] [--json]`), response executed and
observed at this HEAD against the live 1.4.197 runtime, returning `ok: true` with
`result.terminals[].handle` and `result.terminals[].title` present on every element. Only three of
its fields are consumed (`handle`, `title`, `orphaned`), so response drift outside those three cannot
affect OS-31.

`worktree show`: grammar read from `orca worktree show --help`
(`--worktree <selector> [--json]`), response executed and observed at this HEAD — `ok: true` with
`result.worktree.id` echoing the requested `<repo-id>::<path>` for a live selector, and `ok: false`
with `error.code == "selector_not_found"` for an unresolvable one. Exactly one field is consumed
(`result.worktree.id`), compared for byte equality against the recorded id. It exists to make
§4.2.1a's "absent" verdict provable rather than assumed.

The same `--help` read that supplied `terminal list`'s grammar is also the authority for the
`--worktree` selector alphabet this design depends on: `identity:<identity>`, `id:<repo-id>::<path>`,
`name:<displayName>`, `branch:<branch>`, `issue:<number>`, `path:<path>`, **or `active`/`current`** —
the last two documented as aliases, which is precisely why §4.2.1 refuses to persist them. Both
additions create nothing, mutate nothing, and are safe to repeat.
IMPLEMENTATION loads the version-matched Orca orchestration and CLI guides before writing any new
invocation; until then the whole path is exercised through `FakeAdapter`.

**`FakeAdapter`** implements `LifecycleSettlementPort` fully over its existing `FileExternalWorld`
(`fake_adapter.py:15-72`), with an injectable scripted axis outcome per dispatch so a test can
construct `not_settled`, `unknown` authority, `disputed` liveness, an unnamed terminal owner and a
W-C orphan deliberately. Its `recover_handle` is backed by a `FileExternalWorld` terminal listing
that supports the same six §4.2.1a outcomes — verified, unverified (digest contradicted), ambiguous
(two digest matches), not-listed, candidate-without-digest, and scope-unresolved — including
decorated titles and a per-selector listing (so a query under a *different* worktree selector returns
an empty array), so the normalisation, the stable-selector requirement and the fail-closed branches
are all exercised on the fake path too. It declares both new capabilities, so every end-to-end pause/resume/cancel
test runs with **no Orca**. Its `open_dispatches` reads the same durable journal and the same durable
runtime-state file the Orca implementation reads, plus its `FileExternalWorld` listing as the
leg-(3) analogue — so the fake exercises the *reconstruction*, not a memory read. Critically, the
fake's observation responses are **restricted to the fields the real runtime actually returns**
(`{"dispatch": {...}, "worker": {"state": ...}, "terminalResource": {"releaseState": ...}}`): it is
forbidden to invent a role, an origin or a terminal handle in an observation, because a fake that
could do so would prove a recovery the real adapter cannot perform. That restriction is asserted by
the shared suite, not left to authorial discipline.

**Why a FakeAdapter-only proof is not enough, and what closes the gap.** F-002 is a property of the
*real* adapter and the *real* harness (their in-memory `_receipts` and `_terminals`), so a fake that
was written to be restart-safe proves nothing about them. The conformance obligation is therefore
run twice over one shared, parameterised suite (§13.2, T-47):

1. against `FakeAdapter` + `FileExternalWorld`;
2. against the **real `OrcaAdapter` over the real `OrcaRuntimeHarness`**, driven offline through the
   existing `OfflineHarnessTestCase` vehicle (`test_orca_runtime_contract.py:986`), which stubs only
   `_exec_orca` — the subprocess boundary — and therefore executes every line of the real adapter,
   the real terminal ledger, the real `account_axes` and the real journal writes over scripted
   `task-list` / `dispatch-show` / `worker-show` JSON. This is the same vehicle the 1.4.196 contract
   regressions already use, so it introduces no new grammar and no new test infrastructure.

**Scripting the listing is not pre-seeding the handle, and the difference is the whole point.**
F-002 requires that T-47 start from a genuinely empty adapter/harness and never be pre-seeded with
the handle under recovery. This design satisfies that, and the distinction is worth stating precisely
because it is exactly where iteration 2 went wrong:

- what is forbidden is seeding the **process state a dead process held** — `_terminals`, `_receipts`,
  or a `register_terminal` call made by the test. Those stay empty and are asserted empty immediately
  before recovery. A successor that could read them would be proving its own fixture;
- what is legitimate is scripting the **subprocess boundary**, which is the only thing
  `OfflineHarnessTestCase` stubs (`_exec_orca`) and is already how `task-list`, `dispatch-show` and
  `worker-show` are supplied. The governing rule is *a fixture may return only what the real response
  actually carries*. Iteration 2 violated it by scripting a `role`/`origin`/handle into `worker-show`,
  whose verified shape contains none of them — so it staged a recovery the real adapter could never
  perform. `terminal list` **does** return `handle` and `title`; that was executed and observed at
  this HEAD, not assumed. Scripting them models verified reality rather than substituting for it;
- and the recovery is not "the fixture handed us a handle" in any case, because the handle alone is
  not accepted. It is accepted only after `sha256(handle)` matches a digest that was written durably,
  before `worker-start`, by a *different* process. The fixture proves this cannot be a coincidence by
  computing the digest **from** the handle it lists, and the negative twins prove the fixture cannot
  rescue a wrong answer: mutate that digest by one byte and the recovery must refuse even though the
  listing still contains the handle. A staged test would still pass under a mutated digest; this one
  must fail.

**Three fixture rules make leg 2 a real proof rather than a staged one** — this is the half
iteration 2 got wrong, and the rules are stated as obligations the test must satisfy:

- **No pre-seeded harness terminal state.** The recovering harness is built through
  `OfflineHarnessTestCase.build()`, which sets only `run_owner` / `run_id` / `requested_phases` and
  leaves `_terminals` and `_receipts` empty (`test_orca_runtime_contract.py:996-1010`). The test may
  not call `register_terminal` before the recovery; if it does, it is proving its own fixture. The
  handle under recovery is therefore **absent from every in-memory structure** and reaches the
  recovering process by exactly one route: the scripted `terminal list` response, narrowed by title
  and proved against the journal's digest. The scripted listing is built by *hashing* — the fixture
  computes `sha256(handle)` for the row it writes at `INTENDED` and emits the same handle in the
  listing — so the test cannot pass by coincidence, and a deliberately corrupted digest must make it
  refuse.
- **Observations mirror the real response shapes exactly, field for field.** `worker-show` returns
  `{"dispatch": {"status", "completed_at"}, "worker": {"state"}, "terminalResource": {"releaseState"}}`
  and `dispatch-show` returns `{"dispatch": {"status", "completed_at"}}` — the shapes already scripted
  at `test_orca_runtime_contract.py:239-249`, `:1314-1318`, `:1691-1700`, `:2286`. The fixture is
  forbidden to add a `role`, an `origin`, an `owner` or a terminal handle to either payload, and a
  guard assertion enumerates the allowed top-level keys so an added field fails the test rather than
  rescuing it. `worker-release` returns `{"state": "retained", "reason": "external_terminal",
  "processAction": "none"}` — the shape actually observed 12/12 against live 1.4.197 — so case 1 of
  §4.2.2 is *not* reachable in the default fixture and case 3 must carry the weight. `terminal list`
  is scripted with the **observed live shape**, `{"ok": true, "result": {"terminals": [...]}}`, whose
  elements carry `handle`, `title`, `worktreeId`, `worktreePath`, `orphaned`, `connected` and the
  rest; the fixture is required to include **decorated** titles (a leading `✳ ` / `◐ ` glyph, as
  observed live) and at least one unrelated terminal, so normalisation and non-collision are actually
  exercised rather than assumed. It is forbidden to add a `role`, an `origin` or an `owner` to a
  listing element — the live response has none, and a fixture that invented one would again be
  proving a recovery the real adapter cannot perform.
- **Provenance comes only from the journal.** The assertion is positive and negative: the recovered
  row's role/origin equal what `PLANNED` wrote, **and** a variant run with the journal file deleted
  recovers `unknown_role`/`unknown` and refuses — proving the journal, not the stub, is the source.

The residual gap — a genuinely live Orca — is exactly the OD-2 situation and is reported the same
way: the live runtime here is 1.4.197 and `validate_orca_contract` refuses it
(`orca_runtime_harness.py:249`, `:458-463`), so TEST reports the live leg as **not produced, with
that reason stated**, and never as passing. Two things therefore remain unproven by any offline
suite and are named rather than implied: (i) that a live 1.4.196 runtime behaves as the scripted
shapes say, and (ii) the W-C window, which no test can close because no verb *verifies* it — `terminal list` can
enumerate a W-C terminal but nothing can prove the candidate is ours without a digest that was never
written, so the suite asserts the **refusal** plus the fact that the candidate was reported, which is
all there is to assert.

**`HumanApprovalPort`** needs no new production implementation:
`clarification_protocol.ArtifactHumanApprovalPort` (`:526`) already matches `ports.py:90-95`
exactly. `FakeAdapter` gains a `FakeHumanApprovalPort` backed by a temp artifact base — i.e. the
real class over a temp directory — so the fake path exercises the real OS-30 semantics rather than a
simplified stand-in. The `"human_approval"` capability (`contracts.py:39`) is checked before use the
way `EXTERNAL_LOOKUP` is (`executor.py:121`), closing the other half of FI-3.

---

### 11. No-LangGraph fallback

**The fallback that must not break, unchanged.** `launcher.require_runtime()`
(`launcher.py:242-256`) still raises `LANGGRAPH_DEPENDENCY_MISSING` / `LANGGRAPH_VERSION_UNSUPPORTED`
and exits 3; `INSTALL.md:254-255` — "it does not use the prompt loop as a fallback" — stays true and
is not edited. Everything that is not the graph keeps working with no LangGraph:
`run_logging.py`, `clarification_protocol.py`, `decision_gate.py`, `decision_policy.py`,
`orca_runtime_harness.py`, `validate_skills.py`.

**Exact degraded behaviour of the new surface, with LangGraph absent:**

| verb / module | behaviour |
|---|---|
| `run_workflow.py discover` | **works**. It reads only Tier-2 records through `pause_store.py`, which imports no LangGraph. C1/C2 cannot be evaluated, so each listing's `verdict` is `CHECKPOINT_UNVERIFIED` with a `detail` naming the missing runtime — never `RESUMABLE`, and never a claim that the pause is fine. |
| `run_workflow.py resume` | **refused**, `LANGGRAPH_DEPENDENCY_MISSING`, exit 3, before any claim is taken. |
| `pause_policy.py`, `pause_store.py`, `durable_store.py` | import and function |
| `checkpoint_store.py`, `pause_runtime.py`, `graph.py` | `ImportError` at module import, exactly like `graph.py:9` today |
| Skill-document Coordinator path (`orca_runtime_harness.py`) | writes the `WAITING_FOR_INPUT` run status and the audit/timing rows and can read the Tier-2 record as a **discovery index and audit record**; it has no checkpoint and therefore no execution state to be authoritative over |

**The explicit statement PLAN requires.** *The no-LangGraph fallback never supersedes checkpoint
authority when LangGraph is present.* It is a strictly smaller capability. This is not only prose:
`pause_runtime.resume` reads `record["projection"]` **only** inside `assert_c3`, never as an input
to `validate_state`, and T-43's second variant proves it by mutating the projection and asserting
the reconstruction is unchanged. No document, docstring or test in this design claims the degraded
mode is equivalent, and `INSTALL.md` gains one sentence saying the shipped command line is now
checkpoint-durable by default, stated **beside** the unchanged no-fallback sentence so the two are
not confused.

---

## Components / Interfaces / Data Flow

### Data flow — pause

```
route() --"PAUSE"--> PAUSE node
   |  0. FileSettlementJournal: PLANNED/OPENED/INTENDED rows already written at dispatch time,
   |     each BEFORE the effect it covers (durable, §4.2.1)
   |  1. LifecycleSettlementPort.open_dispatches (journal U durable receipts U task-list --run)
   |     -> account_dispatch -> journal ACCOUNTED -> recover_dispatch / release_terminal
   |     -> require_pause_disposition(row) per §4.2.2
   |        (refuse TERMINAL_OWNERSHIP_UNKNOWN / TERMINAL_ORPHAN_POSSIBLE) -> DISPOSED
   |  2. HumanApprovalPort.publish  ->  request_id, item_ids
   |  3. pause_binding{...}  ->  WorkflowState        (checkpointed = AUTHORITY)
   v
TERMINAL node (writes NO terminal_status; run_lifecycle = WAITING_FOR_INPUT)
   v
LangGraph commits the checkpoint                       <=== C1: the commit point of the pause
   v
pause_runtime.finalize_pause: saver.head() -> pause_store.create(record)   (index/fence/projection)
   v
run_logging: pause_settlement_accounted*, run_paused, run_end/WAITING_FOR_INPUT  (+ TIMING_LOG)
```

### Data flow — resume by a NEW process

```
discover_paused_runs(artifact_base)                       (record only; no checkpoint needed)
   v
pause_store.claim(run_id)  --flock/lease-->  exactly one winner
   v
C1 checkpoint exists?  C2 pointer == head && digest matches?
   v
FileCheckpointSaver.get_tuple -> validate_state           <=== the ONLY reconstruction input
   v
C3 project_pause(state) == record["projection"] ?
   v
ArtifactHumanApprovalPort: effective decision per item    (NOT_FOUND / STALE / CONFLICT -> refuse)
   v
ONE resume_bundle_id over the COMPLETE sorted item/decision set
   -> record["applied"]  (absent / RECORDED / RESUMED)   <-- one entry, never per item
   v
stale-source comparison  ->  redo-round  |  correction re-entry (+ generation, floors)
   v
applied[resume_bundle_id] = RECORDED    <-- ONE atomic whole-record write, BEFORE the effect
   v
graph.update_state_command(config, "RESUME_PAUSE", ...) ; graph.invoke(None, config)
                                        <-- THE single effect, owned by the bundle entry
   v
applied[resume_bundle_id] = RESUMED ; pause_store.mark_resumed ; run_resumed + run_status (once)
```

### Public API summary (everything IMPLEMENTATION must create)

```python
# pause_policy.py  (pure)
RUN_LIFECYCLE_STATES, PAUSE_EVENTS, PAUSE_TRANSITIONS, PAUSE_REFUSAL_CODES,
PAUSE_REVALIDATION_CODES, SETTLEMENT_OUTCOMES, WORKER_RESOURCE_OUTCOMES,
PROCESS_LIVENESS_STATES, CLEANUP_AUTHORITY_STATES, TERMINAL_DISPOSITIONS, PAUSE_BINDING_KEYS,
PAUSE_PROJECTION_KEYS, SETTLEMENT_ROW_KEYS, APPLIED_ENTRY_KEYS, APPLIED_STAGES,
DISPOSITION_KEYS
class PauseTransitionRefused(ValueError)      # .code
transition(current, event) -> str
pause_record_id(...) -> str ; cancellation_id(...) -> str
resume_bundle_id(*, run_id, request_id, pause_record_id, decisions) -> str   # F-003: bundle-level
TERMINAL_DISPOSITIONS, AC1_DISCHARGING_DISPOSITIONS, PROVENANCE_SOURCES          # F-001
HANDLE_RECOVERY_OUTCOMES                                                        # F-002 iter 4
terminal_disposition(row) -> str              # F-001: total; may return "residual"
require_pause_disposition(row) -> str         # F-001: the PAUSE-path gate; raises PauseRefused
                                              #        (TERMINAL_OWNERSHIP_UNKNOWN |
                                              #         TERMINAL_ORPHAN_POSSIBLE |
                                              #         TERMINAL_IDENTITY_UNVERIFIED)
normalize_terminal_title(raw) -> str          # F-002 iter 4, S4.2.1a: NFC + strip leading
                                              #   So/Sk/Cf/Cn + whitespace. Pure, idempotent.
match_terminal_title(raw, target) -> bool     # F-002 iter 4: predicate 1 (equality) or
                                              #   predicate 2 (suffix, residual prefix must
                                              #   contain no [A-Za-z0-9_-]). Pure.
resolve_terminal_handle(row, listing) -> dict # F-002 iter 4, S4.2.1a decision table. Pure:
                                              #   takes the already-fetched listing, returns
                                              #   {"handle": str|None, "handle_recovery": ...}
                                              #   and raises PauseRefused for every non-
                                              #   recoverable cell. The I/O lives in the
                                              #   adapter; the DECISION lives here, so the
                                              #   fail-closed table is unit-testable with no
                                              #   Orca and no adapter at all.
project_pause(state) -> dict
policy_digest(skill_path) -> str
resume_reentry(state, *, current_repository, current_artifact, current_policy_digest) -> ReEntry
validate_pause_binding(binding) -> dict
validate_settlement_row(row) -> dict

# pause_store.py  (durable Tier 2)
PAUSE_RECORD_SCHEMA_VERSION, PAUSE_RECORD_KEYS, PAUSE_RECORD_STATUSES, PAUSE_CLAIM_OUTCOMES
SETTLEMENT_JOURNAL_SCHEMA_VERSION, JOURNAL_ROW_KEYS, JOURNAL_STAGES        # F-002 (v3)
                                              # stages: PLANNED -> OPENED -> INTENDED
                                              #         -> ACCOUNTED -> DISPOSED
                                              # v3 adds terminal_worktree + handle_recovery
class PauseStoreError / PauseRecordCorrupt / PauseClaimHeld / PauseClaimLost /
      PauseClaimRequired / PauseObservationTimeout / PauseStoreLockUnavailable /
      SettlementJournalCorrupt
class FilePauseRecordStore(RunPauseStatePort)
class FileSettlementJournal                   # F-002: durable-before-effect, same flock discipline
validate_pause_record(run_id, record) -> dict
validate_journal_row(row) -> dict
settlement_journal_path(run_id, *, artifact_base) -> Path
discover_paused_runs(artifact_base) -> tuple[dict, ...]
pause_record_path(run_id, *, artifact_base) -> Path

# checkpoint_store.py  (durable Tier 1, LangGraph-dependent)
CHECKPOINT_STORE_SCHEMA_VERSION
class CheckpointStoreError / CheckpointStoreCorrupt / CheckpointThreadRetired /
      CheckpointStoreLockUnavailable / CheckpointStoreLockTimeout
class FileCheckpointSaver(BaseCheckpointSaver[int])

# durable_store.py  (shared discipline)
class DurableStoreError / LockUnavailable / LockTimeout
class FileCriticalSection ; read_json_document(...) ; write_json_document(...)

# pause_runtime.py  (ties the tiers together)
class PauseRefused(ValueError)                # .code, .detail
finalize_pause(final_state, *, saver, pause_store, checkpoint_store_path) -> dict | None
build_pause_record(state, checkpoint_id, checkpoint_digest, store_path) -> dict
validate_pause_consistency(record, saver) -> WorkflowState        # C1, C2, C3
takeover(run_id, *, pause_store, observe_timeout_seconds) -> Takeover
reindex(artifact_base, *, pause_store) -> tuple[str, ...]          # C4
resume_run(run_id, *, artifact_base, approval_port, adapter, current_bindings) -> ResumeOutcome
dispose_run(run_id, *, artifact_base, kind, actor, submission_id, reason) -> DisposeOutcome
```

---

## Error Handling / Compatibility

### Closed reason-code vocabulary

```python
PAUSE_REFUSAL_CODES = frozenset({
    "PAUSE_NOT_ADMISSIBLE", "DISPATCH_UNACCOUNTED", "TERMINAL_OWNERSHIP_UNKNOWN",
    "TERMINAL_ORPHAN_POSSIBLE", "TERMINAL_IDENTITY_UNVERIFIED",
    "PAUSE_CHECKPOINT_MISSING", "STALE_CHECKPOINT_HEAD", "PAUSE_PROJECTION_DIVERGED",
    "PAUSE_RECORD_MISSING", "PAUSE_RECORD_CORRUPT", "CHECKPOINT_STORE_RETIRED",
    "PAUSE_CLAIM_HELD", "PAUSE_CLAIM_LOST", "PAUSE_OBSERVATION_TIMEOUT",
    "PAUSE_TRANSITION_FORBIDDEN", "PAUSE_LIFECYCLE_INCOHERENT",
    "SETTLEMENT_JOURNAL_CORRUPT",
    "RESPONSE_NOT_FOUND", "RESPONSE_ALREADY_APPLIED", "RESPONSE_STALE_REVISION",
    "RESPONSE_CONFLICT", "RESPONSE_ITEM_UNRESOLVED",
    "RUN_ALREADY_RESUMED", "RUN_ALREADY_CANCELLED", "RUN_ALREADY_ABANDONED",
    "CHECKPOINT_UNVERIFIED",
})
PAUSE_REVALIDATION_CODES = frozenset({
    "STALE_SOURCE_BINDING", "STALE_ARTIFACT_BINDING", "STALE_POLICY_DIGEST",
})
```

Four codes deserve their enforcement conditions named here, because a declared code with no
condition can never fire and is worse than no code at all:

- **`TERMINAL_OWNERSHIP_UNKNOWN`** — raised by `pause_policy.require_pause_disposition(row)` (§4.2.2)
  for any settlement row whose disposition is not in `AC1_DISCHARGING_DISPOSITIONS`, i.e. not
  `released`, not `exited`, and not `retained_by_named_owner`. Concretely: `cleanup_authority ==
  "unknown"` with no nameable owner, or `process_liveness == "disputed"` surviving its one bounded
  re-observation with no nameable owner. Refuses the pause and routes to `BLOCK`. On the abandon path
  the same predicate runs and the row is recorded `residual` — **reported, not discharged**, and the
  run does not claim AC-1 (§9.2 step 6).
- **`TERMINAL_ORPHAN_POSSIBLE`** — raised by the same gate for the narrower W-C case of §4.2.1: a row
  at stage `OPENED` whose Task exists, whose `dispatch-show --task` reports no Dispatch, and whose
  `provenance_source` is `"absent"`. It is a *distinct* code because it names a distinct fact — a
  terminal may have been created that no process can now address — and because the operator action it
  implies is different: look for a terminal titled `os31-<run_id>-<intent_id>`, which §4.2.1a's leg
  (4) will usually have found and reported as a `listing_candidate` carrying its handle. It is also
  the code for a row at stage `>= INTENDED` whose scoped listing returned **no** match and where no
  independent runtime observation corroborates that absence (§4.2.1a). Refuses the pause; on abandon
  the row is `residual` and enumerated by title, `handle_recovery` and candidate handle.
- **`TERMINAL_IDENTITY_UNVERIFIED`** — raised by leg (4) (§4.2.1a) for a row at stage `>= INTENDED`
  where the scoped `terminal list` returned one or more normalised title matches but the durable
  `terminal_digest` **contradicts** every one of them, or matches two or more. Both are anomalies: a
  run-unique title should not belong to a terminal we cannot hash to, and one digest should not have
  two live pre-images. It is a *distinct* code from `TERMINAL_ORPHAN_POSSIBLE` because it names a
  different fact — not "a terminal may be unreachable" but "the runtime showed us something that
  disagrees with what we recorded" — and because choosing between candidates is precisely the guess
  this gate exists to prevent. Refuses the pause; on abandon the row is `residual` with
  `handle_recovery = "unverified"` and **no** candidate handle reported, because publishing an
  address the digest disproves is worse than publishing none.
- **`SETTLEMENT_JOURNAL_CORRUPT`** — raised by `validate_journal_row` / `FileSettlementJournal._read`
  on a row that fails the closed `JOURNAL_ROW_KEYS` schema. Like `PauseRecordCorrupt`, it is never
  read as "no dispatches": a journal that cannot be parsed refuses the pause with
  `DISPATCH_UNACCOUNTED` rather than silently enumerating an empty set.

The two sets are **disjoint**, asserted by a test: a revalidation code must never be mistaken for a
refusal (that would make a legitimately changed source uncompletable) and a refusal must never be
mistaken for a revalidation (that would apply a stale answer). Every code is uppercase and
colon-prefixed in messages, so `runtime_state.runtime_state_error_code`'s convention
(`runtime_state.py:167-174`) — take the head before the first `:` — works unchanged for projecting a
failure onto a terminal reason.

### Failure-mode table

| condition | detected by | outcome |
|---|---|---|
| adapter cannot publish or settle | `pause_admissible` | `BLOCK` / `BLOCKED` — the pre-OS-31 behaviour, preserved and still tested |
| a dispatch is still `dispatched` after recovery | PAUSE node | `DISPATCH_UNACCOUNTED` → `BLOCK`; the run is not allowed to pretend it paused |
| a terminal is neither released, nor proven exited, nor retained by an owner the journal names | §4.2.2 `require_pause_disposition` | `TERMINAL_OWNERSHIP_UNKNOWN` → `BLOCK`; recording the ambiguity is not resolving it, and neither is labelling it |
| a Task exists, no Dispatch exists, and the journal row never reached `INTENDED` (W-C) | §4.2.1 crash-window table + §4.2.2 gate | `TERMINAL_ORPHAN_POSSIBLE` → `BLOCK`; leg (4) may report a `listing_candidate` handle but nothing may act on it; on abandon, `residual` + enumerated by `terminal_title`, `handle_recovery` and candidate handle, and **AC-1 is not claimed** |
| a row at stage `>= INTENDED` whose scoped `terminal list` yields title matches the durable digest contradicts, or two digest matches | §4.2.1a leg (4) | `TERMINAL_IDENTITY_UNVERIFIED` → `BLOCK`; no handle is used, none is reported, nothing is closed |
| a row at stage `>= INTENDED` whose scoped `terminal list` yields **no** match, uncorroborated by `worker-show`/`dispatch-show` | §4.2.1a leg (4) | `TERMINAL_ORPHAN_POSSIBLE` → `BLOCK`. Corroborated, it is admissible as `exited`; absence alone is not a discharge |
| the terminal listing itself cannot be read | §4.2.1a leg (4) | `DISPATCH_UNACCOUNTED` → `BLOCK`. Unreadable is unknown, never empty |
| an abandon ends with one or more `residual` rows | §9.2 step 6 | the abandon **completes**, `ac1_discharged: false` is recorded, the report and `run_end` reason enumerate every residual terminal, and `run_abandoned` carries `residual_terminal_count` |
| `cleanup_authority == "unknown"` with no nameable owner | §4.2.2 case 3 fails | `TERMINAL_OWNERSHIP_UNKNOWN` → `BLOCK` |
| `process_liveness == "disputed"` surviving one bounded re-observation, with no nameable owner | §4.2 step 3 + §4.2.2 | `TERMINAL_OWNERSHIP_UNKNOWN` → `BLOCK`; with a nameable owner it is admissible as `retained_by_named_owner` and the dispute is reported |
| the Coordinator process dies before the pause checkpoint commits | a fresh process's `open_dispatches()` (§4.2.1) | the durable journal ∪ durable receipts ∪ `task-list --run` re-enumerate every dispatch; provenance is re-seeded **from the journal**; each row's live handle is resolved by leg (4) — scoped `terminal list`, normalised title match, digest verification (§4.2.1a); windows W-A/W-B/W-D/W-E finish to `DISPOSED` with no leak; window W-C refuses (`TERMINAL_ORPHAN_POSSIBLE`) and is reported with its unverified candidate |
| a durable enumeration source cannot be read | journal / runtime-state / `task-list` | refuse: `SETTLEMENT_JOURNAL_CORRUPT` or `DISPATCH_UNACCOUNTED`. "Unreadable" is never "empty" |
| a resume answers only some items of a 2–3 item bundle | §6.3, before any write | `RESPONSE_NOT_FOUND`; no applied entry, no effect |
| a second, differing answer to an already-applied bundle | `record_applied` (§6.4) | `RESPONSE_CONFLICT`; never applied, never partially applied |
| corrupt Tier-2 record | `validate_pause_record` | `PauseRecordCorrupt`; listed in discovery, never read as "no pause" |
| corrupt Tier-1 store | `FileCheckpointSaver._read` | `CheckpointStoreCorrupt`; never read as empty |
| non-POSIX host | both stores, at construction | `*LockUnavailable`; refuse rather than run unlocked |
| lock contention beyond the timeout | `FileCriticalSection` | `LockTimeout`, finite and injectable; never an infinite wait |
| two Coordinators | `pause_store.claim` | one winner; the loser observes; no effect |
| lease lost mid-resume | `LeaseKeeper` over `heartbeat` | `PAUSE_CLAIM_LOST`; fail closed and stay closed |
| checkpoint/projection disagree | C3 | `PAUSE_PROJECTION_DIVERGED`; neither side wins |
| answer replayed | applied set | `RESPONSE_ALREADY_APPLIED`; no second effect, no second row |
| answer superseded | OS-30 `stale` / `_current_item_ids` | `RESPONSE_STALE_REVISION` |
| two different answers | OS-30 lineage / effective decision | `RESPONSE_CONFLICT`; never arbitrated by recency |
| head/artifact/policy moved | §3 comparison | **revalidation**, not refusal (§7) |
| resume of a disposed run | `claim` | `RUN_ALREADY_CANCELLED` / `_ABANDONED`; non-destructive |
| LangGraph absent on `resume` | `require_runtime` | `LANGGRAPH_DEPENDENCY_MISSING`, exit 3 |

### Compatibility

- **Additive vocabulary only.** Nothing is removed from `ROUTE_TOKENS`, `TERMINAL_STATUSES` or
  `RUN_STATUS_VALUES`; every existing value keeps its meaning; the tuples stay closed and eagerly
  validated. Routing for `COMPLETED`/`BLOCKED`/`ESCALATED` is untouched.
- **Two intentional behaviour changes, both named.** (1) The decision axis stops going straight to
  `BLOCK` **when pause is admissible**, which requires two capabilities no existing test declares —
  so the three assertions PLAN scheduled for edit stay green unedited (§1.4). (2) The production
  graph requires a durable checkpointer, paid for by the enumerated six-file edit of §2.5.
- **No migration.** A run that never paused has no pause record and no `pause_binding`; every
  existing artifact reads identically before and after. `initial_state` supplies the four new
  `WorkflowState` fields, and a checkpoint written by an older revision is not readable by a newer
  one only if the closed field set changed — which it did, so `FileCheckpointSaver` stores
  `contracts.SCHEMA_VERSION` alongside each checkpoint and refuses a checkpoint whose
  `schema_version` differs, with `CheckpointStoreCorrupt`. There are no pre-existing durable
  checkpoints anywhere (none ship today), so nothing on disk is invalidated.
- **No historical run or artifact under `artifacts/` is modified.**

---

## Expected Changed Files / Implementation Steps

### File-by-file change map

**New engine files** — each byte-mirrored into
`orca-worker-reviewer-orchestration/tools/deterministic_workflow/` **in the same change**
(FI-13/R-10; `validate_deterministic_workflow_parity` compares file sets then bytes):

| file | unit |
|---|---|
| `scripts/deterministic_workflow/durable_store.py` | WU-2 |
| `scripts/deterministic_workflow/pause_policy.py` | WU-3 |
| `scripts/deterministic_workflow/pause_store.py` | WU-2(b) — `FilePauseRecordStore` **and** `FileSettlementJournal` (§4.2.1) |
| `scripts/deterministic_workflow/checkpoint_store.py` | WU-2(a) |
| `scripts/deterministic_workflow/pause_runtime.py` | WU-5/6/7/8/10 |

**Modified engine files** (same mirror obligation):

| file | change | unit |
|---|---|---|
| `contracts.py` | `ROUTE_TOKENS`, `RouteToken`, `TERMINAL_STATUSES`, `RUN_LIFECYCLE_STATES`, `PAUSE_CAPABILITIES`, `CAPABILITIES += "lifecycle_settlement"` | WU-1 |
| `graph_spec.py` | `NODES`, `STATIC_EDGES`, `ROUTE_TARGETS`, `GRAPH_OWNED_DECISIONS += "PAUSE_RESUME"` | WU-1 |
| `state.py` | 4 new fields (`run_lifecycle`, `pause_binding`, `binding_generation`, `phase_pass_floor`), `_assert_lifecycle_coherence`, `_assert_pause_binding`, `initial_state`, `UPDATE_COMMANDS += RESUME_PAUSE, REQUEST_DISPOSITION` + their guards | WU-1/5/8/10 |
| `routing.py` | `pause_admissible`, `phase_gate` line 33, `route` lines 101-102, `all_phase_passes_current` | WU-5/8 |
| `executor.py` | `pause_node`, `dispose_node`, `terminal_node` PAUSE/CANCEL/ABANDON branches, `_pass_record += binding_generation`, `verify_final_review_binding` call | WU-5/8/10 |
| `graph.py` | `PAUSE`/`DISPOSE` nodes + edges, `DurableCheckpointerRequired`, `PROTECTED_STATE_FIELDS` in `_guard_update`/`_aguard_update` | WU-2(c)/5/9/10 |
| `launcher.py` | `EXIT_CODES`, `resolve_checkpoint_path`, unconditional `configurable.thread_id`, default saver in `run_cli`, `discover`/`resume` subcommands, `--artifact-base`/`--checkpoint-store` | WU-2(c)/6/10 |
| `ports.py` | `RunPauseStatePort`, `LifecycleSettlementPort` | WU-2(b)/4 |
| `fake_adapter.py` | `LifecycleSettlementPort` impl, `FakeHumanApprovalPort`, capabilities | WU-4 |
| `orca_adapter.py` | `LifecycleSettlementPort` impl over `account_axes` / `worker-abandon` / `worker-release`; journal `PLANNED` write before `create_task`, `OPENED` at the existing `_record_receipt` site (`:117`), `INTENDED` through the `terminal_observer` closure; `open_dispatches` as the three-legged durable reconstruction of §4.2.1 (**never** `_receipts`); the pre-E1 resolution of the stable origin-worktree selector `id:<repo-id>::<path>` from `harness` capabilities, refusing before E1 rather than falling back to `current`; `recover_handle` as leg (4) of §4.2.1a over `harness.list_terminals` under that same persisted selector (`normalize_terminal_title` + digest verification, fail-closed on zero/contradicted/ambiguous/scope-unresolved, the last guarded by `harness.resolve_worktree`); provenance re-seeded from the journal via `register_terminal` using the leg-(4) handle; capabilities | WU-4 |
| `orca_runtime_harness.py` | five additive seams, each defaulted or purely new so every existing call site binds unchanged: (i) keyword-only `terminal_observer: Callable[[str], None] \| None = None` on `run_existing_task`, invoked between `create_fake_terminal` and `start_worker` (`:3196-3205`), issuing no Orca command; (ii) keyword-only `title: str \| None = None` on `create_fake_terminal` (`:1998-2003`, default preserves `fake-{role}-{iteration}`) plus a `terminal_title` passthrough on `run_existing_task`, so E2 sends the run-unique title the journal recorded; (iii) a new read-only `list_terminals(*, worktree, limit=None)` issuing `terminal list --worktree <w> --json` through the existing `self.call` path (§4.2.1a); (iv) keyword-only `worktree: str = "current"` on `create_fake_terminal` (`:2000-2003`, default preserves today's hard-coded alias exactly) plus a `terminal_worktree` passthrough on `run_existing_task`, so E2 creates the terminal in the stable `id:<repo-id>::<path>` scope the `PLANNED` row journalled; and (v) a new read-only `resolve_worktree(selector)` issuing `worktree show --worktree <s> --json` for §4.2.1a's scope guard. All existing call sites bind unchanged; only (iii) and (v) add verbs, and both are reads | WU-4 |
| `__init__.py` | **unchanged** — adding a pause export must not risk a transitive `checkpoint_store` import | — |

**Modified top-level scripts:**

| file | change | mirror |
|---|---|---|
| `scripts/run_logging.py` | `RUN_STATUS_VALUES` + 8 event constants | → `orca-worker-reviewer-orchestration/tools/run_logging.py` (byte parity, `validate_skills.py:2974-3000`) |
| `scripts/clarification_protocol.py` | `run_disposition` reader + `promote_pending` guard (WU-12) | → `tools/clarification_protocol.py` |
| `scripts/orca_runtime_harness.py` | publish clarifications for `WAITING_FOR_INPUT` too; re-verify the run-status repeat | none |
| `scripts/verify_full_workflow_example.py` | last-`run_end`-wins reader (WU-11) | none |

**Skill document — `orca-worker-reviewer-orchestration/SKILL.md`** (WU-1 sets, WU-13 reconciles):

1. **`workflow-graph-contract` block (line 159)** — `route_tokens` gains `"PAUSE"`, `"CANCEL"`,
   `"ABANDON"`; `terminal_statuses` gains `"CANCELLED"`, `"ABANDONED"`. The block must equal
   `WORKFLOW_ID / SCHEMA_VERSION / PHASES / ROUTE_TOKENS / TERMINAL_STATUSES` exactly
   (`validate_workflow_graph_docs.validate`, lines 93-104).
2. **`workflow-control-plane` block (line 173)** — a sixth entry:
   ```json
   {"decision": "PAUSE_RESUME",
    "route_tokens": ["PAUSE", "CANCEL", "ABANDON"],
    "sections": ["## Durable Pause and Resume (OS-31)"]}
   ```
   The declared decision tuple must equal `GRAPH_OWNED_DECISIONS` **in order**, so `"PAUSE_RESUME"`
   is appended last in both places. Every route token must be owned by some declared decision —
   satisfied by this entry.
3. **New section `## Durable Pause and Resume (OS-31)`**, carrying the exact
   `NON_AUTHORITATIVE (graph-owned)` marker string
   (`validate_workflow_graph_docs.py:23-27`) as its first line and appearing **exactly once** in the
   document (`_section_body` refuses a missing or duplicated heading). It documents the state
   machine, the two tiers, the reason codes and the two CLI verbs as derived prose.
4. **`skill_owned_safety` is unchanged** — in particular
   `"## Structured Human Clarification (OS-30)"` must **not** acquire the marker.
5. **Limitations block (`:2377-2383`)** — L1, L3, L6, L7 become false and are rewritten:
   - L1 → "blocked run은 종료된다; **pause 가능한 decision block은 `WAITING_FOR_INPUT`으로 durable하게
     보존되고 동일 run으로 재개된다**."
   - L3 → the consumption lineage now exists: the pause record's applied set.
   - L6 → a downgrade round is still terminal, but a decision block is now resumable.
   - L7 → a new process **can** adopt a paused run through the run-scoped claim.
   L2, L4, L5, L8 are unchanged; **L4 (no timeout semantics) stays true** and this design adds no
   timeout-driven default anywhere.
6. **Core Invariants (`:2387-2450`)** — the line "A decision block is terminal at every risk level"
   is rewritten to "A decision block is terminal at every risk level **unless it is admissible as a
   durable pause, in which case it is `WAITING_FOR_INPUT` at every risk level**; risk never changes
   the terminal status, decision state or reason code", plus new invariants:
   `WAITING_FOR_INPUT is never a terminal status`;
   `A pause settles every dispatch before the run may wait`;
   `Resume re-enters through the correction path and never clears a terminal status`;
   `Exactly one Coordinator may resume or dispose a paused run`;
   `The OS-40 checkpoint is authoritative for resume state; the pause record is an index`;
   `Cancel and abandon are explicit human instructions, never a timeout`.
   Verified in this phase: this block is free prose with **no** `validate_skills.py` anchor on those
   sentences, so the edit is safe; the anchored sentences (`run_end는 terminal이 아니다`,
   `FINAL_REVIEW_AUDIT_SCHEMA_VERSION = …`) are untouched.

**Docs / packaging:**

| file | change |
|---|---|
| `INSTALL.md` | one sentence: the shipped command line is checkpoint-durable by default, stated beside the unchanged `:254-255` no-fallback sentence; the two new verbs |
| `docs/DETERMINISTIC_WORKFLOW.md` | the pause/dispose nodes and the two tiers |
| `requirements-langgraph.txt` | **no change** — OD-4 adds no pinned dependency |
| `release_manifest.py` | **no change expected** — `required_skill_paths` enumerates the installed engine by `rglob("*.py")` (`:88-95`), so the five new mirrored files are covered automatically. Verified in this phase. `test_release_package.py` is the gate. |

### Implementation order (PLAN's WU-1 … WU-14, unchanged)

```
WU-1 vocabulary + both fenced blocks
  ├─ WU-2 (a) checkpoint_store  (b) pause_store + durable_store  (c) default wiring
  │     └─ WU-3 pause_policy
  ├─ WU-4 LifecycleSettlementPort + adapters + four-axis accounting
  │           └─ WU-5 PAUSE node + binding  ................. first end-to-end pause
  │                 ├─ WU-6 discover/takeover ──► WU-7 response application ──┐
  │                 └─ WU-9 raw update_state seam ──────────────────────────► WU-8 revalidation
  │                                                                            (first end-to-end resume)
  └────────────────────────────────────────────────────────► WU-10 cancel/abandon
                                                                ├─ WU-11 run_end readers
                                                                └─ WU-12 promote suppression
                                                                     └─ WU-13 SKILL.md + parity
                                                                          └─ WU-14 regression suite
```

Every unit runs the full suite plus `validate_skills.py` before it is done, and mirrors its engine
files in the same change.

---

## Testing Strategy

### 13.1 The vehicle

Everything except the opt-in `--orca-runtime` suite runs with **no Orca runtime and no network**.
`FakeAdapter` is the vehicle: it already is the reference implementation of the optional recovery
capabilities and is declared to make a new process able to look up and finish an earlier process's
work (`fake_adapter.py:15-25`), which is exactly the property resume needs. Crash injection is
expressed as "stop driving the graph at step *n*, drop every object, and start a **new** driver over
the same on-disk stores" — the same shape `test_deterministic_workflow_ownership.py` already uses.
Time is never slept: `ManualLeaseClock` (`runtime_state.py:188-201`) is injected into both new
stores, so every lease, observation and lock-timeout test is deterministic.

### 13.2 New and extended test files

| file | covers |
|---|---|
| `scripts/test_deterministic_workflow_checkpoint.py` *(new, gated on `_langgraph_ok()`)* | WU-2(a)/(c): saver round-trip, head, retirement, corruption, default wiring |
| `scripts/test_deterministic_workflow_pause.py` *(new, **not** gated)* | WU-2(b)/WU-3: record schema, lease fence, transition table, identities, projection, import isolation |
| `scripts/test_deterministic_workflow_pause_e2e.py` *(new)* | WU-4…WU-9 end to end on `FakeAdapter`; the multi-item bundle transaction (T-46) |
| `scripts/test_deterministic_workflow_settlement.py` *(new)* | the durable journal schema and stages; the terminal disposition exit invariant as an exhaustive property (T-12); **the §4.2.1a handle-resolution decision table as pure unit tests** — `normalize_terminal_title` (idempotence, inertness on undecorated titles, the live `✳`/`◐` decorations), `match_terminal_title` (both predicates plus the alphabet-bounded rejection of a foreign run's title), and `resolve_terminal_handle` over literal listings covering every cell, with **no Orca, no adapter and no fixture**; then the **shared, parameterised** `LifecycleSettlementPort` restart-conformance suite (T-47) — run once against `FakeAdapter` and once against the real `OrcaAdapter`+`OrcaRuntimeHarness` via `OfflineHarnessTestCase` |
| `scripts/test_deterministic_workflow_cancel.py` *(new)* | WU-10/WU-12: TC-1…TC-4, TT-1…TT-9 |
| `_graph.py`, `_contracts.py`, `_round2.py`, `_recovery.py`, `_ownership.py`, `_malformed.py`, `_launcher.py`, `_lease_keeper.py` *(extended)* | vocabulary, new edges, the `PROTECTED_STATE_FIELDS` refusal, and the six `execute_state` call-site updates of §2.5 |
| `scripts/test_run_logging.py` *(extended)* | the run-status tuple at all enforcement points, the second `run_end` pair, timing-scope closure, non-blank `started_at` |
| `scripts/test_verify_full_workflow_example.py` *(extended)* | WU-11 reader conformance |
| `scripts/test_workflow_control_plane.py` *(extended)* | both fenced blocks against the engine |
| `scripts/test_orca_runtime_contract.py` *(extended)* | harness-side pause/cancel offline; the real-adapter half of the T-47 conformance suite over `OfflineHarnessTestCase`; the new `list_terminals` seam and the scripted `terminal list` response shape; the `create_fake_terminal(title=...)` default-preserving assertion (an omitted `title` still produces `fake-{role}-{iteration}`, so no existing call site changed behaviour); the unchanged 1.4.196 assertions |
| `scripts/test_clarification_protocol.py` *(extended)* | WU-12 suppression |
| `scripts/test_release_package.py`, `scripts/test_validate_skills.py` *(extended)* | parity and packaging |

The gating asymmetry between the first two files is deliberate and is itself V8 evidence: the
authority module is LangGraph-dependent, the index/policy modules are not.

### 13.3 How each required regression is constructed

| # | Required regression | Construction | PLAN test |
|---|---|---|---|
| 1 | **crash immediately before pause** | drive the graph with an adapter whose `account_dispatch` raises on the *n*-th dispatch; then **drop every in-memory object — driver, graph, saver, adapter, harness, pause store, journal handle** — and build a *fresh* adapter+harness pair over the same on-disk checkpoint store, `.runtime_state.json`, `.settlement_journal.json` and artifact base, with **no** reference to the dead objects and with `_terminals`/`_receipts` empty. Assert: head is the pre-pause checkpoint, `run_lifecycle == "ACTIVE"`, no pause record; the fresh `open_dispatches()` enumerates **exactly** the prior dispatch set. **One case per crash window of §4.2.1**, each injected by truncating the run at the named effect: W-A (nothing created → nothing enumerated); W-B (Task only, no journal `OPENED` → found via leg 3, disposed `no_effect`); W-D (journal at `INTENDED`, `worker-start` never issued → provenance read **from the journal**, the plaintext handle recovered by leg (4) from a scripted `terminal list` whose matching element carries a **decorated** title and whose handle hashes to the journalled digest — and, decisively for F-002, **the creating and recovering processes are scripted with different `current` bindings**: the original harness's `worktree current` resolves to worktree **A** (`id:repoA::/wt/a`, the value journalled at `PLANNED` and sent to E2), while the fresh harness's `worktree current` is scripted to resolve to worktree **B** (`id:repoB::/wt/b`), and the scripted `terminal list` returns the matching terminal **only** when invoked with the A selector and returns an empty array for B. The recovery must still succeed, and the assertion is made on the command log `harness._raw`: the `terminal list` leg (4) issued carries `--worktree id:repoA::/wt/a` and the literal strings `current` and `active` appear in **no** `--worktree` argument of any recovery command. A twin in which the design is imagined to have persisted the alias is expressed as the direct assertion that a listing issued under B finds nothing — so the test fails loudly if a future change reintroduces alias replay; `dispatch-show --task` reports no Dispatch, row disposed with an `AC1_DISCHARGING_DISPOSITIONS` value; plus three fail-closed twins on the same fixture — digest contradicted → `TERMINAL_IDENTITY_UNVERIFIED`, two digest matches → `TERMINAL_IDENTITY_UNVERIFIED`, zero matches uncorroborated → `TERMINAL_ORPHAN_POSSIBLE` — each asserting no pause record and that `release_terminal`/`terminal close` was never called); W-E (accounted, not disposed → re-accounted with zero Orca commands, then disposed). Assert re-driving finishes every one of those rows to `DISPOSED`, exactly one settled dispatch per intent, no duplicate `create_task`, and **no leaks**. Then the negative case: **W-C** (journal at `OPENED`, terminal created and **present in the scripted `terminal list`** under its run-unique title, `INTENDED` write dropped) asserts the pause is **REFUSED** with `TERMINAL_ORPHAN_POSSIBLE` *even though leg (4) found a title match* — the point being that an unverifiable candidate is not a recovery — that `handle_recovery == "listing_candidate"`, that no pause record is written, that nothing was closed, and that the abandon path records the row `residual` with `ac1_discharged == false` **and reports it as `candidate_handle`** in `residual_terminals` (§2.3), so the limitation is asserted as behaviour, with its reporting duty, rather than left as prose. Run twice over one parameterised suite: once on `FakeAdapter`, once on the real `OrcaAdapter`+`OrcaRuntimeHarness` through `OfflineHarnessTestCase` (only `_exec_orca` stubbed) | T-08, T-47 |
| 2 | **crash immediately after pause** | monkeypatch `finalize_pause` to raise after `graph.invoke` returns; assert head carries `WAITING_FOR_INPUT` and no record exists; run `reindex()` in a new process, assert the record is derived from the checkpoint; run `reindex()` again and assert byte-identical (idempotent) | T-09, T-45 |
| 3 | **duplicate response** | resume once; then, in a new process, replay the identical `ResponseSubmission`; assert `RESPONSE_ALREADY_APPLIED`, `adapter.effect_count` unchanged, no new Task/Dispatch, `clarification/` byte-identical, and exactly one `run_resumed` + one `run_end` pair | T-16, T-23 |
| 4 | **concurrent resume race** | two `FilePauseRecordStore` instances with **different `owner_id`s** over one record (this is what makes them two Coordinators — `default_owner_id` is host:pid, `runtime_state.py:107-114`); both call `takeover`; assert exactly one `CREATED`, one `PauseClaimHeld`, the loser observes `RESUMED` and its `effect_count` is 0 | T-15 |
| 5 | **stale checkpoint** | after a legal pause, drive one more checkpoint onto the thread so `head != record["checkpoint_id"]`; assert `STALE_CHECKPOINT_HEAD`, no effect, record still `WAITING_FOR_INPUT`, and that `update_pointer` under the claim makes it resumable again | T-17, T-44 |
| 6 | **stale response** | publish revision 1, then a revision-2 request for the same items via `expand_scope`/`_reclarify`; answer revision 1; assert `RESPONSE_STALE_REVISION` and no effect | T-18 |
| 7 | **changed source / policy** | (a) mutate `repository_binding` before resume; (b) rewrite the ```policy-contract `decision_policy` sub-object in a temp `SKILL.md` copy; assert in each case that the answer is **not** applied unconditionally, that `binding_generation` incremented, that `phase_pass_floor` names the responsible phase (+ downstream at high risk) and that route yields `PREPARE_CORRECTION` | T-19, T-20 |
| 8 | **conflicting response** | write two distinct effective decisions for one item into the lineage; assert `RESPONSE_CONFLICT`, that neither is applied, and that the refusal is not resolved by recency (swap the timestamps and assert the same refusal) | part of T-18/T-19; new case in `_pause_e2e` |
| 9 | **orphan task/dispatch** | script `FakeAdapter` so one dispatch reports `settlement="not_settled"`; assert the pause runs `recover_dispatch`, records `settlement="recovered"` with `worker_done` count 0 and no role promotion, and that a dispatch that cannot be recovered refuses the pause with `DISPATCH_UNACCOUNTED`; additionally assert an orphan that exists **only** in the runtime listing (no journal row, no durable receipt) is still enumerated and still finished, and that an unreadable listing refuses rather than enumerating an empty set | T-11, T-27 |
| 10 | **terminal ownership leak** | **the oracle is the exit invariant, not the record.** Four scripted cases, all asserting `release_terminal` was never called on a non-`authorized` row and that the Coordinator's own terminal never appears in `open_dispatches()`: (a) `cleanup_authority="unknown"`, `process_liveness="disputed"`, **no nameable owner** → the pause is **REFUSED** with `TERMINAL_OWNERSHIP_UNKNOWN`, `run_lifecycle` stays `"ACTIVE"`, terminal status is `BLOCKED`, and **no pause record is written** — merely persisting `unknown` explicitly does **not** satisfy AC-1; (b) same, but the journal names a definite owner (`active_worker`, `owner_dispatch_id` set, `provenance_source="journal"`) → admissible as `terminal_disposition="retained_by_named_owner"` with a non-empty `terminal_owner`, disputed liveness recorded as reporting evidence; (c) `process_liveness="already exited"` → `exited`; (d) `authorized` + `release` + a release receipt whose `processAction` is in `PROCESS_TERMINATING_ACTIONS` → `released`, **plus its negative twin**: the same row with the empirically observed live receipt `{"state":"retained","reason":"external_terminal","processAction":"none"}` must **not** reach `released` (D-6/R8-iii), and falls through to case 3 or refuses; (e) `provenance_source="absent"` at stage `OPENED` → **REFUSED** with `TERMINAL_ORPHAN_POSSIBLE`, and its twin at stage `INTENDED` whose listing match the digest contradicts → **REFUSED** with `TERMINAL_IDENTITY_UNVERIFIED`, both asserting that no `release_terminal` and no `terminal close` was issued. Assert additionally that `"transferred"` is not a member of `TERMINAL_DISPOSITIONS` and that `"residual"` is not a member of `AC1_DISCHARGING_DISPOSITIONS`, so a future edit cannot quietly re-admit a label as a discharge. Plus the exhaustive property of §13.4 | T-12 |
| 11 | **artifact duplication / overwrite** | snapshot `sha256` of every file under `artifacts/runs/<run>/clarification/` before a replay and assert equality after; assert `_write_directory` raised on no path; assert exactly one `run_end` pair per real transition | T-23, T-28 |
| 12 | **cancel / abandon** | TT-1…TT-9 in `_cancel.py`: cancel end to end with no Orca; abandon with no response fabricates **no** decision record and **no** `decision_cancelled` (assert the lineage file list is unchanged); cancel succeeds with a moved head; cancel replay yields `ALREADY_CANCELLED` with no second `run_end` pair; TC-4 arbitration; a cancelled run is unresumable and undiscoverable, **including** through the raw `update_state` path; `promote_pending` publishes nothing after a cancel and behaves unchanged for a live run; the second `run_end` is authoritative under last-row-wins; the TIMING_LOG `run_end` has a non-blank `started_at` and closed scopes | T-24…T-33 |
| 13 | **Orca 1.4.196 compatibility** | offline contract regression per OD-2: `SUPPORTED_ORCA_APP_VERSIONS == ("1.4.196",)` (`test_orca_runtime_contract.py:8957`, `:9014`) and the refusal behaviour for any other version, proving OS-31 changes neither | T-36 |
| 14 | **no-LangGraph fallback** | import `pause_policy` and `pause_store` with `langgraph` removed from `sys.modules` and blocked by a meta-path finder; assert both import and function and that `checkpoint_store` is not in `sys.modules`; assert `discover` works and reports `CHECKPOINT_UNVERIFIED`; assert `resume` and `require_runtime` still raise `LANGGRAPH_DEPENDENCY_MISSING` and exit 3 | T-35, T-37 |
| 16 | **multi-item decision bundle (F-003)** | a 3-item OS-30 request. (a) crash after the single `record_applied` `RECORDED` write with the head still at the pause → re-drive → **exactly one** graph re-entry, one `run_resumed`, one `run_end` pair, `adapter.effect_count == 1`; (b) crash after the re-entry commits, before promotion → the head-based ladder promotes to `RESUMED` and reports `RESPONSE_ALREADY_APPLIED` with no second effect; (c) two concurrent resumers on the same 3-item bundle → one `CREATED`, one `PauseClaimHeld`, loser `effect_count == 0`; (d) a resume answering only 2 of 3 items → `RESPONSE_NOT_FOUND`, **no** applied entry written, no effect; (e) a takeover carrying a *different* `decision_id` for one item after the first bundle is `RESUMED` → `RESPONSE_CONFLICT`, record unchanged, **no partial application**; (f) after every case, assert `len(record["applied"]) == 1` and its `items` equal the record's `decision_item_ids` sorted — the structural proof that no per-item partial state can exist | T-46 |
| 17 | **durable settlement reconstruction, real adapter (F-002)** | the parameterised conformance suite of §13.2 run against the real `OrcaAdapter` + `OrcaRuntimeHarness` on `OfflineHarnessTestCase`, under the three fixture rules of §10.2. **Ordering**, asserted from the real command log `harness._raw` and the journal's own timestamps: `PLANNED` precedes `task-create`, `OPENED` precedes `terminal create`, `INTENDED` precedes `worker-start`; a row can never be at a later stage than the effect it covers. **Handle recovery is proved end to end and it is the only route** (F-002): with `harness._terminals == {}` and `adapter._receipts == {}` asserted immediately before recovery, the fresh pair recovers the plaintext handle solely from the scripted `terminal list --worktree <recorded> --json`, matched by `normalize_terminal_title` against the journal's `terminal_title` and **verified** against its `terminal_digest`; **and the recorded scope is proved to be a stable identity rather than a replayed alias** — the creating harness's `worktree current` is scripted to return `id:repoA::/wt/a` and the *recovering* harness's `worktree current` is scripted to return a **different** worktree `id:repoB::/wt/b`, while the scripted `terminal list` yields the matching element **only** under the A selector and an empty array under B and under `current`/`active`. The test asserts from the real command log `harness._raw` that E2's `terminal create` and leg (4)'s `terminal list` were issued with the **byte-identical** `--worktree` string, that this string is the `terminal_worktree` value read back from `.settlement_journal.json`, that it begins with `id:` and contains `::`, and that neither `current` nor `active` appears as a `--worktree` argument anywhere in the run. A negative twin scripts the journal to hold the literal `current` and asserts the recovery **fails** — the guard against a future regression, since a test that replays the same alias would otherwise pass either way. Two further twins cover the scope guard: `worktree show` scripted `ok:false`/`selector_not_found` with an empty listing → `scope_unresolved` / `DISPATCH_UNACCOUNTED` and nothing closed; and `worktree show` returning `ok:true` but echoing a *different* `result.worktree.id` → the same refusal, proving the guard compares the echoed id rather than merely checking `ok`; the test then asserts the recovered handle is the one E2 created, that `register_terminal` and `account_axes` were called with that plaintext handle, and that `handle_recovery == "listing_verified"`. Six fail-closed twins run on the same fixture and must each refuse without closing anything: the digest mutated by one byte → `TERMINAL_IDENTITY_UNVERIFIED`; the same handle listed twice → `TERMINAL_IDENTITY_UNVERIFIED`; the matching element removed from the listing, scope guard passing, with no corroborating observation → `TERMINAL_ORPHAN_POSSIBLE`; the listing command scripted to return `ok:false` → `DISPATCH_UNACCOUNTED`, never an empty enumeration; and the two scope-guard twins above (`selector_not_found`, and a mismatched echoed id) → `scope_unresolved` / `DISPATCH_UNACCOUNTED`, which is the pair that distinguishes "this worktree holds no such terminal" from "this process could not name the worktree at all". **Normalisation is exercised, not assumed**: the matching element's title carries a leading `✳ ` glyph as observed live, an unrelated `"orca-skills"` terminal and a foreign `os31-`-prefixed title from a *different* run sit in the same listing, and the assertion is that exactly the right one matched and the foreign one did not. **No pre-seeding**: the recovering harness is built by `OfflineHarnessTestCase.build()` and the test asserts `harness._terminals == {}` and `adapter._receipts == {}` immediately before recovery; the test never calls `register_terminal` itself. **Response shapes are the real ones**: a guard assertion pins the allowed top-level keys of the scripted `worker-show` (`dispatch`, `worker`, `terminalResource`) and `dispatch-show` (`dispatch`) payloads, so a fixture cannot rescue the recovery by inventing a `role`, an `origin`, an `owner` or a terminal handle the runtime does not return; `worker-release` is scripted with the empirically observed `{"state":"retained","reason":"external_terminal","processAction":"none"}`. **Provenance source is proved both ways**: recovered role/origin equal what `PLANNED` wrote, and a variant with `.settlement_journal.json` deleted recovers `unknown_role`/`unknown` and refuses — so the journal, not the stub, is demonstrably the source. Plus: the handle is never persisted — asserted positively by reading back `.settlement_journal.json`, `.runtime_state.json` and the checkpoint after recovery and requiring the handle string to appear in **none** of them, while `sha256(handle)` appears in the journal; `terminal list` issues zero mutations and `account_dispatch` issues zero Orca commands on the repeat, both asserted from `harness._raw`; the live-runtime leg is reported per OD-2 as not produced, with the 1.4.197 reason | T-47 |
| 18 | **abandon: the transfer mechanism, or the refusal (F-001)** | the oracle is the *mechanism*, never a stored string. Four parts. **(a) The claim is withdrawn:** assert `"transferred" not in TERMINAL_DISPOSITIONS` and that no code path writes a `terminal_owner` matching `^actor:` — a grep-style assertion over `pause_policy` and the DISPOSE node, so the old label cannot return. **(b) The honest outcome:** abandon a run whose residual terminal has `unknown_role`/`unknown` origin and no nameable owner; assert the abandon **completes**, the row is `terminal_disposition="residual"` with `terminal_owner == ""` and `recovery="residual:<cancellation_id>"`, the record carries `ac1_discharged == false`, the abandon report and the `run_end` reason enumerate that terminal by `terminal_title`/`task_id`/`provenance_source`/`handle_recovery` — plus `candidate_handle` when leg (4) produced one, and `candidate_handle == ""` when `handle_recovery == "unverified"` — `run_abandoned` carries `residual_terminal_count == 1`, and **no terminal was closed** (`lifecycle_commands()` contains no `close`). **(c) The discharge that is real:** the same abandon over a row the journal *does* own reaches `retained_by_named_owner` with a non-empty `terminal_owner` taken from the journal, and `ac1_discharged == true`. **(d) The asymmetry, as a pair:** the same input on the **pause** path refuses with `TERMINAL_OWNERSHIP_UNKNOWN` (or `TERMINAL_ORPHAN_POSSIBLE` for a W-C row) and writes no pause record. Together these prove the refusal, since the mechanism a real transfer would need does not exist on this contract | T-48 |
| 15 | **checkpoint authority (REQ-1)** | `run_cli --demo` with no extra flags produces an on-disk checkpoint; `configurable.thread_id` is set unconditionally; `build_graph` refuses without a durable checkpointer; every closed `WorkflowState` field survives a round trip; the head is monotonic; two concurrent writers are serialised; a corrupt store is refused; a brand-new process rebuilds the state **field-for-field** from the checkpoint, with a second variant that mutates `record["projection"]` first and proves the reconstruction ignored it | T-41…T-45 |

### 13.4 Property-style obligations

- the transition table is exhaustive: every `(state, event)` pair in the cross product is either in
  `PAUSE_TRANSITIONS` or refused with `PAUSE_TRANSITION_FORBIDDEN` (T-05);
- **the recorded worktree scope is a stable identity, never an alias** (F-002): every
  `terminal_worktree` value written to the journal matches `^id:[^:]+::/` , and across the whole
  command log of every test in the suite no `--worktree` argument issued by OS-31 equals `current` or
  `active`; and for every row that reaches leg (4), the `--worktree` string of E2's `terminal create`
  is byte-identical to the `--worktree` string of the recovering `terminal list` (T-08, T-47);
- `PAUSE_REFUSAL_CODES ∩ PAUSE_REVALIDATION_CODES == ∅`;
- the engine's `WORKER_RESOURCE_OUTCOMES` / `PROCESS_LIVENESS_STATES` / `CLEANUP_AUTHORITY_STATES`
  equal the harness's (`orca_runtime_harness.py:323`, `:319`, `:1629-1650`);
- **every** settlement row that reaches a committed pause carries a disposition in
  `AC1_DISCHARGING_DISPOSITIONS`, and every row whose disposition is `retained_by_named_owner`
  carries a non-empty `terminal_owner` **and** `provenance_source == "journal"` — asserted as an
  exhaustive property over the cross product of
  `(settlement × worker_resource × process_liveness × cleanup_authority × role × origin × owner? ×
  provenance_source)`, so no combination can slip through un-dispositioned and none can reach a
  committed pause as `residual` (T-12);
- `AC1_DISCHARGING_DISPOSITIONS ⊂ TERMINAL_DISPOSITIONS`, `"residual" ∉ AC1_DISCHARGING_DISPOSITIONS`,
  and `"transferred" ∉ TERMINAL_DISPOSITIONS` — the vocabulary itself is asserted, so no later edit
  can re-admit a label as a discharge (T-12, T-48);
- every journal write precedes the effect it covers, asserted from the real command log: for every
  row, `planned_at < task-create`, `opened_at < terminal create`, `intended_at < worker-start`
  (T-47);
- **a plaintext terminal handle is used only when a durable digest proved it** — over the cross
  product of (candidate count 0/1/2) × (digest match none/one/many) × (stage `OPENED`/`INTENDED`+),
  exactly the one cell (≥1 candidate, exactly one digest match, stage ≥ `INTENDED`) yields
  `handle_recovery == "listing_verified"` and a non-`None` handle; every other cell yields a refusal
  code in `PAUSE_REFUSAL_CODES` and a `None` handle, and in no cell is `release_terminal` or
  `terminal close` reached (T-47, T-08);
- `normalize_terminal_title` is idempotent, never alters a string already free of leading decoration,
  and never matches a title belonging to a different `(run_id, intent_id)` — asserted over the live
  decorations observed at this HEAD (`✳ `, `◐ `), over undecorated titles (`"orca-skills"`,
  `"Terminal 5"`), and over adversarial near-misses that differ only in `intent_id` (T-47);
- the plaintext handle appears in **no** durable artifact OS-31 writes except an abandon report's
  residual enumeration — asserted by scanning the checkpoint, `.settlement_journal.json` and
  `.runtime_state.json` for the literal handle after every test in the suite (T-47, T-48);
- `applied` never holds more than one entry, and that entry's `items` always equal the record's
  `decision_item_ids` sorted — asserted over 1-, 2- and 3-item bundles (T-46);
- `project_pause` covers exactly `PAUSE_PROJECTION_KEYS`, so C3 cannot silently omit a field;
- `resume_bundle_id` / `cancellation_id` are stable across a process restart and **insensitive** to
  `repository_binding` / `artifact_binding` / `phase_iteration` (T-07); `resume_bundle_id` is
  additionally **sensitive** to every `(decision_item_id, decision_id)` pair and to the item set's
  completeness, so a different or partial answer can never collide with an applied bundle (T-46);
- no OS-31 code path calls `delete_thread`, `os.replace` onto a published artifact, or
  `graph.update_state` with a `PROTECTED_STATE_FIELDS` key.

### 13.5 Evidence rules for TEST

- record **actual** output, never predicted; a skipped test is reported as skipped with its reason;
- the six pre-existing opt-in skips must remain exactly six unless OS-31 deliberately adds an opt-in
  test, in which case the new count is stated;
- OD-2 applies verbatim: the 1.4.196 result is reported as an **offline contract regression**, and
  the report says plainly that no live-runtime evidence was produced and why (the live runtime here
  is 1.4.197 and `validate_orca_contract` refuses it — `orca_runtime_harness.py:249`, `:458-463`).

### 13.6 Gates that must be green at the end

`python3 -m unittest discover -s scripts -p 'test_*.py'` · `python3 scripts/validate_skills.py`
(≥ 732 checks) · `python3 scripts/validate_workflow_graph_docs.py` → `PASSED` ·
`test_release_package.py` (package/archive + source-installed parity).

---

## Risks / Open Issues

Carrying PLAN's R-1 … R-21, with what this design does about each. Severity is PLAN's.

| # | Risk | Design answer |
|---|---|---|
| R-1 | fifth `RUN_STATUS` value is a contract change with eager validators | one tuple edit at `run_logging.py:116` satisfies all four points; T-01 proves acceptance **and** continued refusal of an unknown value |
| R-2 | new tokens/statuses trip the topology and documentation validators | §12 lists both fenced blocks, the new demoted section and the exact ordering constraint (`GRAPH_OWNED_DECISIONS` equality is order-sensitive) |
| R-3 | resume as "clear `terminal_status`" | §8.1/8.2: not expressible — `WAITING_FOR_INPUT` is not terminal, and `PROTECTED_STATE_FIELDS` closes the raw path |
| R-4 | phase passes not currency-checked | §7.3's floor mechanism; no-op for runs that never paused |
| R-5 | pausing without settling | §4.2: pause is **refused** with `DISPATCH_UNACCOUNTED` unless every dispatch is accounted, and with `TERMINAL_OWNERSHIP_UNKNOWN` / `TERMINAL_ORPHAN_POSSIBLE` unless every terminal reaches a member of `AC1_DISCHARGING_DISPOSITIONS` (§4.2.2). An abandon may end with `residual` rows, and then explicitly does **not** claim AC-1 |
| R-6 | crash inside pause | §4.4's three windows, each with a determinate disposition; the pre-checkpoint window is recovered by a **fresh process** from the durable journal ∪ durable receipts ∪ `task-list --run` (§4.2.1), not from process memory; repeated accounting is read-only and every mutation is stage- and claim-gated |
| R-7 | two Coordinators resume one run | §5.2's run-scoped flock + lease fence |
| R-8 | in-repository checkpointer is a store we must maintain | §2.2 pins the format, the serializer and the head rule; T-42's obligations (full-field round trip, monotonic head, concurrent writers, corrupt refusal) are the maintenance budget; **no new pinned dependency** |
| R-9 | live 1.4.196 evidence unobtainable here | OD-2 honoured verbatim in §13.5 |
| R-10 | forgetting the `tools/` mirror | mirroring is a completion criterion of every engine-touching unit; §12 enumerates the five new files |
| R-11 | scope creep into a general CLI | §5 caps `run_cli` at two verbs |
| R-12 | scope creep into OS-37 process/PTY ownership | `LifecycleSettlementPort` is confined to settle/recover/release-terminal; `external_resume` stays withheld |
| R-13 | X1 or X2 mistaken for a run cancel | §9.2 treats X1 as **one step of** CANCEL and X2 as the TC-3-only recovery |
| R-14 | cancelled run stays resumable | §9.2 step 2 marks the record terminal under the claim |
| R-15 | cancel given resume's currency check | §7 and §9.2 step 3 state the two opposite rules explicitly; T-19/T-26 are the paired proofs |
| R-16 | `any(...)` reader reports a cancelled run as pre-cancel | §9.7 |
| R-17 | cancel leaves timing scopes open / blank `started_at` | §9.2 step 5 |
| R-18 | post-cancel republication | §9.5 |
| R-19 | the three deliberate test edits mistaken for a regression | §1.4: they are **not needed**; the old assertions stay green because `BASE_CAPABILITIES` makes pause inadmissible |
| R-20 | checkpoint and record drift; resume silently prefers one | §2.4's C1–C4 with named codes; C3 is a refusal, never a preference; T-43/T-44/T-45 |
| R-21 | default checkpointer changes six existing test environments | §2.5 enumerates the six files and fixes them inside WU-2(c), with `require_durable_checkpointer=False` as the named test-only escape |

**Open issues carried to IMPLEMENTATION** (none blocks DESIGN, none requires user authority):

- **OI-1 — Orca CLI grammar.** This design commits only to verbs already executed against a real
  runtime in `orca_runtime_harness.py`. IMPLEMENTATION loads the version-matched Orca orchestration
  and CLI guides before writing any new `worker-*` invocation (`SKILL.md:2396`). Until then the path
  is exercised through `FakeAdapter` only.
- **OI-2 — `checkpoint_ns`.** The shipped graph uses the empty namespace. The store is written for
  the general case (subgraphs would use others) but only `""` is exercised; a non-empty namespace is
  stored and read correctly and is covered by one unit test, not by the e2e path.
- **OI-3 — medium/low-risk downstream revalidation.** Stated in §7.3: `downstream_revalidation_set`
  is high-risk-only by pre-existing design, so a post-resume floor at medium/low risk covers the
  responsible phase alone. This is an inherited engine property, recorded so TEST reports it rather
  than discovering it.
- **OI-4 — checkpoint store growth.** `FileCheckpointSaver` keeps every checkpoint of a thread; a
  long run's store grows monotonically. No pruning is implemented, because pruning could delete the
  checkpoint a pause record points at. Retention is bounded in practice by the iteration budget, and
  a retention policy is explicitly out of scope for OS-31.

---

## Review Feedback Resolution

Gate result for iteration 1: **FAIL**, three blocking findings. Gate result for iteration 2:
**FAIL**, F-003 resolved, F-001 and F-002 still blocking, no non-blocking findings. Iteration 3 is a
correction round scoped to exactly those two. **F-003 is kept as resolved and was not touched**;
PLAN F-001 is untouched — the checkpointed `WorkflowState` remains the sole reconstruction authority
whenever LangGraph is present (§2.2, §2.4, §5.3, §10.1), and the journal is explicitly **not** an
execution-state authority (§4.4). Nothing outside §3, §4.2.1, §4.2.2, §4.4, §9.2 step 6, §10.2, §12,
§13.3 (regressions 1, 10, 17, 18) and §13.4 was rewritten.

The iteration-2 entries below are kept as the record of what that round did. Where iteration 3
supersedes one, it is marked and the superseding entry follows.

### F-001 — CRITICAL / G1 — unresolved terminal ownership accepted as a successful pause

**Accepted without argument.** Recording `cleanup_authority == "unknown"` and
`process_liveness == "disputed"` and calling the run paused does not satisfy OS-31's "no ambiguous
terminal ownership", and a `TERMINAL_OWNERSHIP_UNKNOWN` code with no enforcement condition can never
fire.

| what changed | where |
|---|---|
| **New fail-closed exit invariant.** Every terminal must reach one of `TERMINAL_DISPOSITIONS = ("released", "transferred", "exited")` — definitively released, transferred to an **identified** owner, or proven already exited — before the run may enter `WAITING_FOR_INPUT`. Given as an ordered table **and** as the exact predicate `pause_policy.terminal_disposition(row)` | **§4.2.2** (new) |
| ~~the `"transferred"` member of that set~~ — **PARTIALLY SUPERSEDED by iteration 3**: the invariant, the ordered table and the predicate are kept; `"transferred"` is renamed to `"retained_by_named_owner"` and a non-discharging `"residual"` is added, because the old name claimed an operation nothing performed | see F-001 (iteration 3) |
| **`TERMINAL_OWNERSHIP_UNKNOWN` is bound to a real condition**: raised by that predicate when a row is not `released`, not `exited`, and has no nameable owner — i.e. `cleanup_authority == "unknown"` with no owner, or `disputed` liveness with no owner. Pause refuses and routes to `BLOCK` | §4.2.2; second refusal added to **§4.2**; enforcement conditions stated under **Closed reason-code vocabulary**; four rows added to the **Failure-mode table** |
| **Disputed liveness gets a bounded re-observation** (one retry after the documented ~10 s window, on the injected clock, never a real sleep) and is admissible **only** when ownership is established | **§4.2 step 3** |
| **`unknown` authority is explicitly no longer a pause outcome on its own** — it means only "may not close", and leaves the ownership question for §4.2.2 to close or refuse | **§4.2 step 4** |
| **Row carries the answer**: `SETTLEMENT_ROW_KEYS` and the journal row gain `terminal_owner` and `terminal_disposition`; `TERMINAL_DISPOSITIONS` added to the runtime-neutral vocabularies, flagged as OS-31-owned (no harness counterpart, so excluded from the equality contract test) | **§3** |
| **Abandon fixed the same way.** Step 6 no longer calls a retained `active_worker` terminal "no ambiguous ownership" by fiat: the same invariant runs, `active_worker` passes as `transferred` because a never-close role is a *known-owner* role, and a residue with no nameable owner is discharged by an explicit **handoff to the actor who ordered the abandon** (`terminal_owner = "actor:<actor_id>"`, `recovery = "handoff:<cancellation_id>"`), enumerated by digest and provenance in the abandon report and the `run_end` reason. Available on abandon only; pause refuses instead, and the asymmetry is stated | **§9.2 step 6** |
| ~~**Abandon fixed the same way** (row above)~~ — **SUPERSEDED by iteration 3**: the "handoff to the actor" was an audit action, not a transfer, and is removed | see F-001 (iteration 3) |
| **T-12's oracle changed so persisting `unknown` cannot satisfy AC-1**: four scripted cases; case (a) `unknown` + `disputed` + no owner now asserts the pause is **REFUSED**, `run_lifecycle` stays `"ACTIVE"`, terminal status is `BLOCKED`, and **no pause record is written**. Plus an exhaustive property over the whole axis cross product, and a new paired abandon-handoff test | **§13.3 regressions 10 and 18 (T-48)**, **§13.4** |

### F-002 — CRITICAL / G1 — pre-checkpoint crash cannot reconstruct the dispatch set or terminal ownership

**Accepted without argument, and re-verified in the repository this iteration.** The three facts the
review cited are true at this HEAD: `OrcaAdapter._receipts` is process memory (`orca_adapter.py:21`);
the harness terminal ledger `OrcaRuntimeHarness._terminals` is *also* process memory
(`orca_runtime_harness.py:936`, `ledger_terminal` at `:1157`) — which the review's summary implied
and which makes the gap wider than stated; and the durable receipt is
`RECEIPT_KEYS = {"task_id", "dispatch_id", "external_id", "intent_id"}` with no terminal handle
(`runtime_state.py:87`, and `orca_adapter.py:138-140` says so in a comment).

Resolution: **a durable-before-effect settlement journal**, plus a reconstruction algorithm for the
rows the journal cannot have.

| what changed | where |
|---|---|
| **New durable Tier-2b journal** `.settlement_journal.json` + flock sidecar, `FileSettlementJournal` in `pause_store.py` over the existing `durable_store` discipline (no LangGraph, no Orca — §10.1's table is unchanged). Closed `JOURNAL_ROW_KEYS`, `JOURNAL_STAGES = ("INTENDED", "ACCOUNTED", "DISPOSED")` | **§4.2.1** (new); **§2.3**; Public API summary |
| **Three write points, each strictly before the effect it describes**: `INTENDED` at *dispatch* time in `OrcaAdapter.start` at the two existing `_record_receipt` sites — the only moment terminal identity and provenance exist; `ACCOUNTED` after the zero-command `account_dispatch` and **before** any mutation; `DISPOSED` after the mutation returns | §4.2.1 table; **§10.2**; **§12** file map |
| **`open_dispatches()` redefined as a three-legged reconstruction**, never a memory read: durable journal rows not at `DISPOSED` ∪ durable `FileRuntimeStateStore` receipts with no journal row ∪ `task-list --run` Tasks whose parsed spec `intent_id` is in neither. Leg 3 is the only one that can find a Task whose creating process died before any local write. Unreadable source ⇒ refuse, never "empty" | **§4.2.1**; the port docstring in **§10.2** now makes cross-process answerability a contract condition |
| ~~**Provenance recovery for a row found without it**: `dispatch-show --task` → `worker-show --dispatch --json` → `register_terminal`~~ — **SUPERSEDED by iteration 3**: `worker-show` returns neither role nor origin nor a handle, so this could never work; provenance is now journal-authoritative and written before the effect | see F-002 (iteration 3) |
| **Explicit survival table** for every datum F-002 named: task id, dispatch id, terminal identity, terminal provenance, four-axis outcome, per-row completion — each with its durable home and its recovery leg | §4.2.1 |
| **The two properties are separated.** Iteration 1 conflated idempotent re-mutation with discoverability; §4.2.1 now states that idempotence (harness claim gate + row stage) and discovery (the three legs) are provided separately, because idempotence cannot help you re-account what you cannot enumerate | §4.2.1; **§4.4** pre-checkpoint row rewritten |
| **`pause_binding["settlement_ledger"]` demoted to a projection** of the journal at the commit instant — the journal is the storage — mirroring how `projection` is subordinate to the checkpoint | **§3**, §4.2 |
| **The pre-commit crash test now drops all adapter/harness objects**: a fresh adapter+harness pair over the same on-disk stores must enumerate the prior set (including one row discoverable only via leg 2 and one only via leg 3), re-derive provenance, finish every row to `DISPOSED` with a disposition, create no duplicate Task, and leave **no leaks** | **§13.3 regression 1 (T-08)** |
| **The FakeAdapter-only gap is closed, not waved at.** One shared parameterised conformance suite runs twice: against `FakeAdapter`, and against the **real** `OrcaAdapter` + `OrcaRuntimeHarness` driven through the existing `OfflineHarnessTestCase` vehicle (`test_orca_runtime_contract.py:986`), which stubs only `_exec_orca` — so the real adapter, the real terminal ledger and the real `account_axes` all execute, with no Orca. The residual live-runtime leg is reported per OD-2 as **not produced**, with the 1.4.197 reason, never as passing | **§10.2**; **§13.2** (new `test_deterministic_workflow_settlement.py`); **§13.3 regression 17 (T-47)** |

### F-003 — MAJOR / G5 — exactly-once undefined for a 2–3 item decision bundle

**Accepted without argument.** A per-item `resume_id` with a singular `record_applied` and one graph
effect leaves the subset-`RECORDED` window undefined at exactly the boundary that prevents duplicate
and conflicting application.

Of the two remedies F-003 permitted, this design takes the first — **one atomic bundle-level
application identity over the complete sorted item/decision set** — because it *removes* the
partial-write class rather than defining recovery for it.

| what changed | where |
|---|---|
| **`resume_id` is replaced by `resume_bundle_id(*, run_id, request_id, pause_record_id, decisions)`**, where `decisions` is the complete set of `(decision_item_id, decision_id)` pairs sorted by item id. One resume = one effect = one identity. Stated explicitly that no per-item identity exists | **§6.1**; Public API summary |
| **`applied` holds exactly one bundle entry.** `APPLIED_ENTRY_KEYS` becomes `("resume_bundle_id", "request_id", "items", "stage", "recorded_at", "resumed_at", "resumed_checkpoint_id")`, with `items` the whole sorted set. One entry = one `record_applied` call = one whole-record write under flock + `os.replace`, so **no subset window exists** | **§6.4**; **§2.3** record schema |
| **The single effect owner is named**: the typed `RESUME_PAUSE` update and the one `graph.invoke` belong to `resume_bundle_id` and to nothing else. No item owns the effect | §6.4 |
| **`RunPauseStatePort.record_applied` updated** — signature unchanged (one entry), contract now explicit that the entry is the whole bundle, with two new refusals: `PAUSE_LIFECYCLE_INCOHERENT` when `items` are not exactly the record's `decision_item_ids` sorted, and `RESPONSE_CONFLICT` when a *different* bundle id is already `RECORDED`/`RESUMED` | **§2.3**, §6.4 |
| **Every partial-write window enumerated with its recovery** — four of them, none being "some items applied": before `record_applied`; mid-write (unobservable, atomic); after `RECORDED` before the re-entry commits; after the re-entry before promotion or log rows | §6.4 |
| **All-items reading is now explicitly transactional**: every refusal is evaluated over the whole bundle before any entry is written and before any effect, so nothing downstream sees a half-read bundle | **§6.3** |
| **Concurrent resume covers the multi-item case**, including two contenders carrying *different* answers to the same items | **§6.6** |
| **New multi-item crash/replay/concurrent test (T-46)**, six cases: partial-write crash → exactly one re-entry; post-commit crash → promote, no second effect; concurrent race → loser effect_count 0; 2-of-3 answer → `RESPONSE_NOT_FOUND` with no entry; differing second bundle → `RESPONSE_CONFLICT` with no partial application; and `len(applied) == 1` with matching items after every case | **§13.3 regression 16**; **§13.2**; **§13.4** |

### F-001 (iteration 3) — CRITICAL / G1 — "a label is not a transfer"

**Accepted without argument, and the claim is withdrawn rather than defended.** The review is exactly
right: writing `terminal_owner = "actor:<actor_id>"` and naming the terminal in a report performs no
adoption, no registration, no capability transfer and no acknowledgement. It is an audit action. It
told an identified human that something exists; it did not give them anything, and it did not prove
they can control it. Regression 18 asserted the label, which recreated the very defect iteration 1
was failed for, one path over.

**Verified in the repository this iteration, not assumed.** The only operation on the verified Orca
contract that genuinely moves ownership of a live terminal is `worker-start --task <task> --terminal
<handle>` reaching a ready state, which `start_worker` records through `_attach_terminal` as a new
`owner_dispatch_id` (`orca_runtime_harness.py:2074-2140`, `:1101-1125`). Abandon has no receiving
Task to adopt into — it is the disposition that *stops* dispatching — so that operation is not
available here, and manufacturing a Task to receive the terminal would be dispatching work nobody
asked for. Independently confirmed by this run's own evidence: 12 of 12 `worker-release` calls
against live 1.4.197 returned `state=retained reason=external_terminal processAction=none`, i.e. the
runtime reports a terminal this process created *and adopted* as **external** and refuses to release
it. The platform does not model our ownership, so OS-31 must not claim it does.

| what changed | where |
|---|---|
| **`transferred` is removed from the closed vocabulary.** `TERMINAL_DISPOSITIONS = ("released", "exited", "retained_by_named_owner", "residual")`. The third name says only what is provable — the journal names the owner — and no longer implies an operation. A `transferred` member would require the adoption receipt above, and is added only when a path produces one | **§3**, **§4.2.2** |
| **`AC1_DISCHARGING_DISPOSITIONS = {"released", "exited", "retained_by_named_owner"}`** is separated from the closed set, so a row can be *recorded* without being *discharged* — the distinction iteration 2 collapsed | §3, §4.2.2 |
| **The abandon-only handoff is deleted.** A row with no nameable owner is written `terminal_disposition = "residual"`, `terminal_owner = ""` (never a synthesised actor id), `recovery = "residual:<cancellation_id>"`, carrying `terminal_title`, `terminal_digest`, `task_id`, `dispatch_id`, `provenance_source` and the last observation | **§9.2 step 6** |
| **The abandon still completes — and stops claiming AC-1.** The record carries `ac1_discharged: false`; the abandon report and the `run_end` reason enumerate every residual terminal and state plainly that they were neither released, nor proven exited, nor transferred, and that a human must dispose of them; a `run_abandoned` row carries `residual_terminal_count`. AC-1 is asserted **only** when every row reached `AC1_DISCHARGING_DISPOSITIONS` | §9.2 step 6; §3 (`ac1_discharged`) |
| **The pause predicate is kept exactly as the review asked**, with case 3 renamed and one condition added (`provenance_source == "journal"`), plus the separate `require_pause_disposition` gate that raises | §4.2.2 |
| **T-48 now proves the mechanism or the refusal**, in four parts: (a) `"transferred" not in TERMINAL_DISPOSITIONS` and no code path writes a `^actor:` owner — the old label cannot return; (b) the residual outcome, with `ac1_discharged == false`, the enumeration, and no `close`; (c) the *real* discharge over a journal-owned row, with `ac1_discharged == true`; (d) the pause/abandon asymmetry as a pair. The stored string is no longer an oracle anywhere | **§13.3 regression 18**, regression 10 case (d)'s negative twin, **§13.4** |

**The boundary, stated once and plainly:** on the Orca contract this design is permitted to assume,
a run that abandons with a terminal it cannot name an owner for **has a residual terminal**, and
OS-31 reports it rather than claiming a safe disposition. That is a limitation of the platform, and
this document now says so instead of writing over it.

### F-002 (iteration 3) — CRITICAL / G1 — provenance cannot be reconstructed after the window opens

**Accepted without argument.** The review's premise is correct and iteration 2's fallback was
impossible, not merely weak. Re-verified at this HEAD, first-hand:

- `register_terminal`'s own docstring: role and origin "are the only axis (c2) evidence that exists,
  and the runtime keeps neither, so they are recorded here or lost forever"
  (`orca_runtime_harness.py:1042-1057`);
- `_terminals` is a plain process-local dict (`:936`) and `ledger_terminal` returns
  `unknown_role`/`unknown` for anything it has not seen (`:1157-1178`);
- the verified `worker-show` response shape is
  `{"dispatch": {...}, "worker": {"state": ...}, "terminalResource": {"releaseState": ...}}`
  (`test_orca_runtime_contract.py:239-243`, `:1314-1318`, `:1691-1696`, `:2286`) — **no role, no
  origin, no owner, and no terminal handle**. Iteration 2 asserted this call yields "the terminal
  handle and its role/origin/owner". It yields none of them;
- the only runtime `role` anywhere is `{"kind": "terminal", ..., "role": "agent"}` inside a
  `worker-start` effect (`:374-379`) — a resource-kind label, not a `TERMINAL_ROLE_CLASSES` member.

So no post-hoc reconstruction can work, and the design stops proposing one.

| what changed | where |
|---|---|
| **Persist before the window, not after the call.** Journal stages become `PLANNED → OPENED → INTENDED → ACCOUNTED → DISPOSED`, with the externally-visible effects named explicitly (E1 `task-create`, E2 `terminal create`, E3 `worker-start`, E4 the mutating verbs) and each write placed strictly in front of its effect. `PLANNED` — carrying the whole *intended* provenance plus a run-unique `terminal_title` — is written **before `create_task`**, because role and origin are the caller's own choice and are fully known then | **§4.2.1** (rewritten) |
| **One new seam, defaulted.** `run_existing_task` gains keyword-only `terminal_observer: Callable[[str], None] \| None = None`, invoked with the handle between `create_fake_terminal` and `start_worker`, so the `INTENDED` digest write lands **before** the terminal is adopted. All nine existing call sites bind unchanged; no Orca command is added | §4.2.1; **§10.2**; **§12** |
| **Provenance is journal-authoritative; the runtime is observation-only.** A table names, per datum, which side owns it. On recovery, `register_terminal` is called with the *journal's* role/origin, never with anything `worker-show` returned | §4.2.1; §10.2 |
| ⚠ **SUPERSEDED BY ITERATION 4 — the stated ground was false; see "F-002 (iteration 4)" below.** **Every crash window enumerated with its verdict** — W-A, W-B, W-D, W-E **closed**; **W-C** (terminal created, digest not yet journalled) **NOT closed**, because (*this reason is withdrawn*) the verified grammar has no terminal-listing verb and `worker-show` needs a `dispatch_id` that does not exist. W-C fails closed: pause refuses with the new `TERMINAL_ORPHAN_POSSIBLE`, abandon records `residual`, and AC-1 is not claimed. The design states the limitation and what would close it (a terminal-listing verb, or an idempotency key on `terminal create`) rather than implying leak-free recovery it cannot deliver | **§4.2.1**; **§4.4**; reason-code vocabulary; failure-mode table |
| **Leg (3) demoted, honestly.** Because `PLANNED` precedes `create_task`, leg (3) can no longer be the rescue path for this adapter's own work — it is a **detector of foreign Tasks**, which have no OS-31 provenance, are `provenance_source = "absent"`, and are reported and refused rather than adopted | §4.2.1; §10.2 |
| **The real-adapter fixture is fixed, and its rules are stated as obligations.** No pre-seeded harness terminal state (`_terminals == {}` and `_receipts == {}` asserted immediately before recovery; the test never calls `register_terminal`); observations restricted to the real response shapes with a guard assertion pinning the allowed top-level keys, so no invented `role`/`origin`/`owner`/handle can rescue the recovery; `worker-release` scripted with the empirically observed `retained/external_terminal/none`; and provenance proved both ways — recovered values equal what `PLANNED` wrote, and a variant with the journal deleted recovers `unknown` and refuses | **§10.2**; **§13.3 regression 17 (T-47)** |
| **T-08 becomes one case per crash window**, including the W-C negative case asserting the refusal, so the stated limitation is asserted as behaviour rather than left as prose; plus an ordering property asserted from the real command log (`planned_at < task-create < opened_at-effect …`) | **§13.3 regression 1**, **§13.4** |
| **The FakeAdapter gap is closed where it can be and named where it cannot.** The fake is forbidden to invent provenance in an observation, and that restriction is asserted. Two things remain unproven by any offline suite and are named: live-runtime behaviour (OD-2; 1.4.197 is refused, so the live leg is reported as **not produced**), and W-C, which no test can close because no verb ⚠ *(iteration 4: `terminal list` enumerates it, but no verb)* **verifies** it | §10.2 |

### F-002 (iteration 4) — CRITICAL / G1 — the recovery algorithm needs the plaintext handle, and only the digest was persisted

**Accepted, and the finding was exactly right.** Iteration 3 wrote provenance before the effect —
which was the correct half — and then asserted that the resulting `terminal_digest` could never be
matched, "because the verified grammar has no terminal-listing verb". Both halves of that sentence
mattered and the second half was **false**. I verified it myself at this HEAD rather than taking it
from the coordinator:

- `orca terminal --help` lists a `list` subcommand among `show / read / send / wait / create /
  switch / close / rename / split`;
- `orca terminal list --help` declares the grammar
  `orca terminal list [--worktree <selector>] [--limit <n>] [--include-visual-layouts] [--json]`;
- `orca terminal list --json` executed against the live 1.4.197 runtime returned `"ok": true` with a
  `result.terminals` array whose **every** element carried both `handle` (e.g.
  `term_0c42c18d-d2bd-4f73-8075-9fea396364dd`) and `title`, alongside `worktreeId`, `worktreePath`,
  `orphaned` and `connected`.

Both caveats the coordinator flagged were also reproduced first-hand in that one listing, and both
are handled rather than noted: titles are decorated with a leading status glyph (`"✳ OS-31 Durable
Pause Resume Analysis"`, `"◐ Orca orchestration check run_8e8f9451ad44"`) so exact equality would
indeed fail, and undecorated titles (`"orca-skills"`, `"Terminal 5"`) coexist with them.

So resolution path **(A)** was taken, in its second form — *use a concrete verified Orca operation
that returns the handle* — rather than persisting a handle. No new durable secret, no new file, no
unavailable runtime field.

| what changed | where |
|---|---|
| **New §4.2.1a, leg (4): enumerate, then verify.** A successor issues `terminal list --worktree <recorded> --json`, narrows the array by `normalize_terminal_title` against the `PLANNED` row's run-unique `terminal_title`, and then **proves** the survivor with `sha256(candidate["handle"]) == row["terminal_digest"]`. The title narrows; the digest decides. This is what makes the digest useful instead of inert — it always was a verifier, and it finally has a candidate set. Leg (4) is explicitly **not** a fourth enumeration leg: legs (1)–(3) say *which rows*, leg (4) says *which handle* | **§4.2.1a** (new) |
| **Normalisation is specified exactly, and suffix matching is bounded by an alphabet, not by trust.** NFC, then left-strip code points in Unicode categories `So`/`Sk`/`Cf`/`Cn` and whitespace, then strip. Predicate 1 is equality after normalisation; predicate 2 is `endswith(terminal_title)` **only when the residual prefix contains no character from the title alphabet `[A-Za-z0-9_-]`**. Since an OS-31 title is drawn entirely from that alphabet, predicate 2 provably cannot match another intent's terminal | §4.2.1a |
| **Zero and many both fail closed, explicitly, with a decision table.** Exactly one digest-verified candidate is the *only* branch that yields a handle. No digest match → `TERMINAL_IDENTITY_UNVERIFIED` (new code). Two digest matches → the same. Zero title matches → `not_listed`, admissible as `exited` **only** when corroborated by an independent `worker-show`/`dispatch-show` observation, else `TERMINAL_ORPHAN_POSSIBLE`. An unreadable listing → `DISPATCH_UNACCOUNTED`, because unreadable is unknown and never empty | §4.2.1a; reason-code vocabulary; failure-mode table |
| **W-D is now genuinely closed.** After `INTENDED`, a fresh object recovers task/dispatch identity and provenance from the journal *and* the live plaintext handle from leg (4), so it can seed `register_terminal` and run `account_axes` — the two operations the finding correctly said were unreachable. The crash-window table, the failure-mode table and the "what survives process death" table all say so, and the latter gains an explicit row for the plaintext handle naming the journal and the runtime as two halves that are each useless alone | §4.2.1; §4.2.1a; failure-mode table |
| **ITERATION 5 — the recorded scope becomes an identity.** `terminal_worktree` is no longer the alias `"current"` but the stable `id:<repo-id>::<path>` selector, resolved once before E1 from `orca worktree current` (which the harness already executes at `:1754-1759`), journalled, and handed **byte-identically** to E2's `terminal create` and leg (4)'s `terminal list`. If it cannot be resolved, `start` refuses before E1 rather than falling back to the alias | §4.2.1; §4.2.1a; §10.2; §12 |
| **ITERATION 5 — a fifth seam and a second read verb.** `create_fake_terminal` gains keyword-only `worktree: str = "current"` (default preserves today's hard-coded alias, `:2000-2003`) with a `terminal_worktree` passthrough, so E2 honours the journalled scope; and `worktree show` is added as a **read** because an unresolvable selector was observed to return `ok:true` with an empty array — so "absent" is proved, never inferred, and its failure is the new `scope_unresolved` / `DISPATCH_UNACCOUNTED` cell | §4.2.1a; §10.2; §12 |
| **ITERATION 5 — the tests can now detect the defect.** T-08 and T-47 create under one `current` binding and recover under a **different** one, assert from the real command log that E2 and leg (4) used the byte-identical `id:`-prefixed selector and that neither `current` nor `active` appears in any `--worktree` argument, and add a negative twin that journals the alias and requires the recovery to **fail** | §13.3 regressions 1, 17; §13.4 |
| **The second seam, without which the first would match a promise nothing kept.** `create_fake_terminal` hard-codes `--title f"fake-{role}-{iteration}"` (`orca_runtime_harness.py:1998-2003`), which is **not** run-unique. It gains a defaulted keyword-only `title`, with a `terminal_title` passthrough on `run_existing_task`, so E2 actually sends `os31-<run_id>-<intent_id>`. Both existing call sites (`:3197`, `:3504`) bind unchanged | §4.2.1; §10.2; §12 |
| **Schema v2 → v3, two keys.** `terminal_worktree` (written at `PLANNED`) so the listing is scoped to where the terminal was created — "absent" is only meaningful within a stated scope; and `handle_recovery`, a closed `HANDLE_RECOVERY_OUTCOMES` vocabulary recording *how* the handle was obtained or why it was not. No v2 journal exists, so nothing migrates. **Amended by iteration 5:** `terminal_worktree` is the stable `id:<repo-id>::<path>` selector resolved before E1, never the alias `current`, and the vocabulary gains `scope_unresolved` | §3; §4.2.1; §4.2.1a |
| **W-C keeps its verdict but loses its false rationale.** The old ground ("no listing verb") is withdrawn. The corrected, narrower ground: a W-C row never reached `INTENDED`, so **no digest exists**, so leg (4) can produce a candidate but nothing can verify it — and OS-31 does not close, release or adopt a session on a label, because closing the wrong session is the irreversible harm the gate exists to prevent. So `TERMINAL_ORPHAN_POSSIBLE`, `residual`, the withheld AC-1 and every disposition and vocabulary member are **unchanged**. What improves is reporting: the residual row now carries `handle_recovery` and, when leg (4) found one, `candidate_handle`, so a human gets an address instead of a search | §4.2.1 (W-C paragraph); §9.2 step 6 reporting; §13.3 regressions 1, 18 |
| **The handle still never becomes persisted state.** It is not in the checkpoint (`state.FORBIDDEN_KEYS` matches `terminal_handle`, `state.py:42`), not in the journal, not in the durable receipt (`RECEIPT_KEYS`, `runtime_state.py:87`). It lives in adapter memory and — for a `residual` row only — in the abandon report a human reads, which is policy-consistent: the skill's redaction policy names the terminal handle as an *identity the record exists to prove*, never redacted, alongside `run_id`/`task_id`/`dispatch_id` and `reviewer_terminal` (`SKILL.md:1553-1555`). Asserted positively by a scan of all three stores after every recovery test | §4.2.1a; §13.4 |
| **T-47's fixture proves the recovery instead of staging it.** `_terminals == {}` and `_receipts == {}` are still asserted immediately before recovery and the test still never calls `register_terminal` — so the handle reaches the recovering process by exactly **one** route, the scripted listing. The fixture computes `sha256(handle)` for the row it writes, includes a decorated title, an unrelated terminal and a *foreign* `os31-`-prefixed title from a different run, and adds four fail-closed twins (digest mutated, handle listed twice, element removed, listing `ok:false`). The listing shape is pinned to the observed live shape and forbidden to carry a `role`, `origin` or `owner` | **§10.2**; **§13.3 regression 17** |
| **T-08 gains the W-D positive case and its three refusal twins, and the W-C case now asserts the refusal *despite* a title match** — which is the sharper statement, since it proves the design refuses an unverifiable candidate rather than merely failing to find one | **§13.3 regression 1** |
| **Two new property obligations.** Over the cross product (candidate count 0/1/2) × (digest matches none/one/many) × (stage `OPENED`/`INTENDED`+), exactly one cell yields a handle and every other yields a refusal with `release_terminal`/`terminal close` unreached; and `normalize_terminal_title` is idempotent, inert on undecorated titles, and never matches a different `(run_id, intent_id)` | **§13.4** |

**What was deliberately not done.** Resolution path (A)'s *other* branch — durably persisting the
plaintext handle — was rejected on this design's own terms: `FORBIDDEN_KEYS` exists to keep live
handles out of reconstructable state, and a stored handle is stale evidence about a live resource,
whereas leg (4) reads what is live now and proves it against a digest that cannot go stale. Path (B)
(classify W-D as unrecoverable and withhold AC-1 for it) was not taken **for W-D**, because a
concrete verified operation exists and the reviewer preferred (A) where it genuinely works; it
remains in force **for W-C**, where it is still the honest answer. No positive recovery is claimed
that this design cannot execute.

**Scope.** F-001 and F-003 were not touched. PLAN F-001 is not regressed: the checkpointed
`WorkflowState` remains the sole reconstruction authority, the journal remains explicitly not an
execution-state authority, and leg (4) adds no state of any kind — it is a read whose only output is
a handle held in process memory and a closed-vocabulary outcome recorded on a row.

### F-002 (iteration 5) — CRITICAL / G1 — the persisted worktree scope was an alias, not an identity

**Accepted in full; the finding is correct and the defect was real.** Iteration 4 moved terminal
provenance ahead of the crash window, which was the right move, but it recorded the *scope* of the
recovery query as the literal string `"current"`. The reviewer's evidence is confirmed here
first-hand at this HEAD against live Orca 1.4.197: `orca terminal create --help` documents
`--worktree` as accepting `identity:<identity>`, `id:<repo-id>::<path>`, `name:<displayName>`,
`branch:<branch>`, `issue:<number>`, `path:<path>`, **or `active`/`current`** — the last two named as
aliases; and `orca_runtime_harness.py:2000-2003` does hard-code `--worktree "current"`. An alias is
resolved in the context of the process that *issues* it, so journalling `current` persists no
worktree at all. A successor Coordinator bound elsewhere would enumerate its own worktree, see zero
candidates, and reach `TERMINAL_ORPHAN_POSSIBLE`. That is fail-closed, but it is not closed, and
iteration 4's §4.2.1a sentence claiming the recorded selector was "not whatever `current` happens to
mean in the successor process" was therefore false as written. It is withdrawn and replaced.

**The stable value exists, is a property of *place* rather than of an effect, and is available before
E1.** The harness already executes `orca worktree current` inside `validate_orca_contract` and
already reads exactly `current["result"]["worktree"]["id"]` into its `worktreeId` capability field
(`orca_runtime_harness.py:1754-1759`). Contract validation runs at harness construction — strictly
before any OS-31 dispatch, hence strictly before E1. Executed at this HEAD it returned `ok: true`
with `result.worktree.id = "7b6ee134-05a2-4dbd-be45-91ea1c2d2d7e::/Users/luminous/aiAssistedProjects/orca-skills"`,
the full `<repo-id>::<path>` form the version-matched orchestration guide requires. The same string
appears as `worktreeId` on every element of `orca terminal list --json`, which is what lets §4.2.1a
cross-check it. Crucially, this value is not an *observation of an effect*: it describes where this
process is, so it cannot be lost with the Task or the terminal it precedes. No new verb is needed —
`worktree current` is already in the harness's executed set. (The reviewer also noted
`ORCA_WORKTREE_ID` in the process environment, which I confirmed carries the identical string; the
design does not depend on it, because a CLI call whose grammar and response were verified is a
stronger commitment than an environment variable this design does not own.)

**What changed, concretely.** §4.2.1 now resolves the origin worktree once, before E1, to
`ORIGIN_WORKTREE_SELECTOR = f"id:{worktree_id}"`, journals **that** string as `terminal_worktree`,
and states three things explicitly, as the required action demanded: where the value comes from and
why it is available before the effect; that if it cannot be resolved — unreadable call, `ok:false`,
absent/empty `id`, or an `id` not of the form `<repo-id>::<path>` — `OrcaAdapter.start` **refuses
before E1** with `DISPATCH_UNACCOUNTED`, creating no Task and no terminal and explicitly **not**
falling back to the alias; and that E2 and leg (4) use the identical persisted string, resolved once,
replayed byte-for-byte, re-resolved by nothing. A further defaulted harness seam — item (iv) of
§10.2's five, and the third of §4.2.1's dispatch-side three — carries it to E2:
`create_fake_terminal` gains keyword-only `worktree: str = "current"` (the default preserves today's
behaviour exactly, so both existing call sites bind unchanged) with a `terminal_worktree` passthrough
on `run_existing_task`. Without that seam the `PLANNED` row would again be recording a scope E2 did
not honour — the same class of mistake the title seam existed to prevent.

**A second hole found while fixing the first, and closed with it.** Because leg (4)'s "zero
candidates" branch is load-bearing, I tested what an *unresolvable* selector actually does. It does
not error: `orca terminal list --worktree id:00000000-0000-0000-0000-000000000000::/nope --json`
returned `ok: true`, `result.terminals: []`, `totalCount: 0` — indistinguishable from a real worktree
holding no terminals. Believing that as "the terminal is gone" would reintroduce the defect one level
down. So §4.2.1a now requires the scope to be *proved* before any zero-candidate verdict:
`orca worktree show --worktree <recorded> --json`, grammar read from `--help`
(`--worktree <selector> [--json]`), observed at this HEAD to return `ok:true` with
`result.worktree.id` echoing the recorded id for a live selector and `ok:false` with
`error.code == "selector_not_found"` for a bogus one. The guard passes only on `ok:true` **and** a
byte-identical echoed id. Failure yields the new `handle_recovery` value `scope_unresolved` and
refuses with `DISPATCH_UNACCOUNTED`, on the standing rule that unknown is never empty. This is one
additional read verb, verified the same way as `terminal list`, and it is the cell that makes the
selector's stability load-bearing rather than decorative.

**The tests are fixed the way the finding required, not cosmetically.** The reviewer's precise
objection was that T-08/T-47 "script the listing under the same recorded selector and do not require
a fresh Coordinator whose `current` resolves elsewhere, so they cannot detect the defect." Both tests
now **create under one `current` binding and recover under a different one**: the creating harness's
`worktree current` is scripted to worktree **A** (`id:repoA::/wt/a`, the value journalled and sent to
E2), the recovering harness's to worktree **B** (`id:repoB::/wt/b`), and the scripted `terminal list`
yields the matching element **only** under the A selector, returning an empty array for B and for
`current`/`active`. Recovery must still succeed, and the assertions are made on the real command log
`harness._raw`: E2's `terminal create` and leg (4)'s `terminal list` carry a **byte-identical**
`--worktree` string; that string equals the `terminal_worktree` read back from
`.settlement_journal.json`; it begins with `id:` and contains `::`; and neither `current` nor `active`
appears as a `--worktree` argument anywhere in the run. A negative twin journals the literal
`current` and asserts the recovery **fails** — so a future regression to alias replay cannot pass. Two
scope-guard twins cover `selector_not_found` and a mismatched echoed id. §13.4 carries the same
statement as a suite-wide property, so it is an invariant rather than a single test's assertion.

**Nothing else was reopened.** F-001 and F-003 remain RESOLVED and were not touched. PLAN F-001 is
not regressed: the checkpointed `WorkflowState` is still the sole reconstruction authority, the
journal is still explicitly not an execution-state authority, and the selector resolution persists
one caller-chosen string in a field that already existed. W-C keeps its verdict, its
`TERMINAL_ORPHAN_POSSIBLE` code, its `residual` disposition and its withheld AC-1. The plaintext
handle still never enters the checkpoint, the journal or the durable receipt. No production code,
test, configuration or SKILL document was changed; no branch, no staging, no push.

### F-003 (iteration 3) — RESOLVED in iteration 2, kept unchanged

The gate recorded F-003 as **RESOLVED**. The atomic bundle-level application identity (§6.1, §6.4),
the one-entry `applied` set, the four enumerated partial-write windows and T-46's six cases were not
touched by this round.

### F-001 (iteration 4) and F-003 (iteration 4) — RESOLVED, kept unchanged

The iteration-3 re-review recorded **F-001 RESOLVED** and **F-003 RESOLVED**. Neither was reopened in
this round. `TERMINAL_DISPOSITIONS`, `AC1_DISCHARGING_DISPOSITIONS`, the absence of `transferred`,
the `residual` disposition, `ac1_discharged`, `residual_terminals`, §4.2.2's exit invariant, §9.2
step 6, and the whole of §6's bundle-level application identity are byte-unchanged except where this
round *added* reporting fields to a residual row (`handle_recovery`, the candidate handle) — which
enlarges what an abandon report says and changes no disposition, no vocabulary member and no
acceptance claim.

### Non-blocking findings

The iteration-3 re-review recorded **none**.

### Scope discipline

Nothing outside the three findings was added. Specifically: no process-memory snapshot/restore (the
journal stores durable facts written before their effects, never a memory image); no timeout-based
default decision anywhere (OD-3 and SKILL.md L4 hold — an abandon is triggered by an explicit human
abandon instruction, never by elapsed time, and iteration 3 removed the "handoff to the abandoning
actor" entirely rather than making it automatic); no GUI or notification transport; no
Orca-independent orchestration CLI (`run_cli` still has exactly two verbs, §5); no edits to any
historical run under `artifacts/`. `artifacts/runs/run_8e8f9451ad44/` was not read or written.
`ANALYSIS.md`, `PLAN.md` and `REVIEW_DESIGN.md` were read only.

Iteration 4 held the same line. The only additions are the ones F-002's required action forced: one
verified read verb (`terminal list`), one new subsection describing how its result is narrowed and
verified, two closed-vocabulary journal keys, one new refusal code, three defaulted harness seams,
and the tests that prove each. No new durable file, no new persisted secret, no change to any
disposition, to `AC1_DISCHARGING_DISPOSITIONS`, to the pause/abandon asymmetry, or to F-003's bundle
identity. No production code, test, configuration or SKILL document was changed — this remains a
design phase. `artifacts/runs/run_8e8f9451ad44/` was again neither read nor written.

Iteration 5 held the line more tightly still, because it is the final iteration and a new defect
would be unrecoverable. It changed exactly what F-002's required action named plus the one hole that
fix exposed: the `terminal_worktree` value becomes a stable selector, one defaulted harness seam
carries it to E2, one additional verified read verb (`worktree show`) proves the scope before an
"absent" verdict, one closed-vocabulary value (`scope_unresolved`) records that outcome, and T-08,
T-47 and §13.4 prove the selector is an identity rather than an alias. No new durable file, no new
persisted secret, no new refusal code (`DISPATCH_UNACCOUNTED` already existed), no change to any
disposition, to `AC1_DISCHARGING_DISPOSITIONS`, to the pause/abandon asymmetry, to F-001's mechanism
or to F-003's bundle identity. No production code, test, configuration or SKILL document was changed;
no branch, staging or push; `artifacts/runs/run_8e8f9451ad44/` was neither read nor written, and no
`REVIEW_*.md` was edited.

---

## Decision Record

DECISION_STATE: CLEAR
REASON_CODE: none

Every choice this phase made was decided from the approved ANALYSIS/PLAN, the ticket's explicit
requirements, or facts verified in the repository at this HEAD. Nothing required user authority.
The three judgement calls a reviewer should look at hardest are stated in the open, with their
grounds, and each is reversible inside this run:

1. **Abandon does not run the OS-30 X1 cancellation** (§9.2), because TT-2 forbids fabricating a
   decision for an unanswered item, and the two-authorities hazard is closed by WU-12's run-level
   suppression instead.
2. **`runtime_state.py` is not refactored onto `durable_store.py`** (§2.6), trading ~50 duplicated
   lines for zero regression risk on the lease-keeper concurrency code.
3. **Phase-pass currency uses a generation floor, not binding equality** (§7.3), because equality
   would make `COMPLETE` unreachable in ordinary forward runs and redden the existing suite.

Iteration 2 adds three more, all resolving the gate's blocking findings and all decided from the
ticket's own acceptance criteria and facts verified in the repository — none required user authority:

4. **A pause that cannot name a terminal's owner is refused, not recorded** (§4.2.2). The
   alternative — pausing and reporting the ambiguity — was iteration 1's behaviour and does not meet
   AC-1. Refusal is safe because it falls back to `BLOCK`/`BLOCKED`, the exact pre-OS-31 behaviour
   for the same decision. **Abandon** completes but records such a row `residual` and does **not**
   claim AC-1 (iteration 3; see item 7).
5. **A durable settlement journal, written before every effect, rather than a pure reconstruction
   algorithm** (§4.2.1). A pure reconstruction was the other option F-002 permitted, but terminal
   *identity and provenance* exist only at dispatch time and cannot be re-derived for a terminal the
   runtime no longer lists. The journal is therefore the primary source, with runtime queries kept as
   the leg that finds what no local write ever recorded. Cost: one new durable file and two write
   points inside `OrcaAdapter.start`, which is the smallest surface that makes the property true.
6. **One bundle-level application identity, rather than an atomic batch across per-item entries**
   (§6.1, §6.4). Both were permitted by F-003. The bundle identity *removes* the partial-write class
   instead of defining recovery for it, needs no change to `record_applied`'s one-entry signature,
   and keeps the single effect owner unambiguous by construction. The cost is that a changed answer
   to one item yields a new identity — which is correct behaviour, and is asserted as such.

Iteration 3 adds two more. Both are refusals to claim something the platform cannot give, both were
decided from evidence verified in this repository and from this run's own live-runtime observations,
and neither required user authority:

7. **The abandon "handoff" is deleted rather than strengthened, and AC-1 is conditionally withdrawn**
   (§9.2 step 6, §4.2.2). The reviewer permitted either a concrete ownership transfer or an honest
   refusal. A concrete transfer is not available: the only adoption operation on the verified
   contract is `worker-start --terminal`, abandon has no receiving Task, and the live runtime reports
   a terminal we created *and adopted* as `external_terminal` and refuses to release it (12/12 in
   this run). So `transferred` is removed from the vocabulary entirely, a row with no nameable owner
   becomes `residual`, and a run that ends with one records `ac1_discharged: false` and says so in
   its report. Cost: OS-31 no longer claims AC-1 unconditionally. That cost is the finding.
8. **Terminal provenance is persisted before the effect, and one crash window is declared open**
   (§4.2.1, §4.4). Moving `PLANNED` in front of `create_task` and the digest write in front of
   `worker-start` is possible because role and origin are the caller's own choice, not runtime state.
   That closes W-A, W-B, W-D and W-E. It does **not** close W-C — a terminal created but not yet
   journalled. ⚠ **The reason given here is superseded by item 9:** iteration 3 said "no verified
   Orca verb enumerates terminals", which is false — `terminal list` does. The surviving reason is
   that a W-C row has no `terminal_digest`, so nothing can *verify* what enumeration finds, and
   inventing an identity guarantee `terminal create` does not offer is what `SKILL.md:2396` forbids. The alternative was to keep implying leak-free recovery; this design
   states the window, fails closed inside it, and asserts the refusal in T-08. Cost: one honest
   limitation on the record instead of an unsupported guarantee.

Iteration 4 adds one. It was decided from a capability I verified first-hand against the live
runtime, and it required no user authority:

9. **The plaintext handle is *recovered and verified* rather than *persisted*, and W-C keeps its
   refusal for a corrected reason** (§4.2.1a). F-002 permitted either durably persisting a
   recoverable terminal identifier or using a concrete verified Orca operation, and preferred the
   latter where it genuinely works. It works: `orca terminal list --json` exists in 1.4.197, its
   grammar came from `--help` and its response from execution, and it returns `handle` and `title`
   for every live terminal — so the run-unique `terminal_title` already written at `PLANNED` supplies
   the candidate set and the already-journalled `terminal_digest` supplies the proof. Persisting the
   handle was rejected because `FORBIDDEN_KEYS` exists to keep live handles out of reconstructable
   state and because a stored handle is stale evidence about a live resource. The consequence I want
   a reviewer to look at hardest: this **corrects** iteration 3's claim that no terminal-listing verb
   exists, and that correction removes W-C's stated ground. W-C nonetheless keeps its verdict, its
   refusal code and its withheld AC-1, because the missing thing was never enumeration — it is the
   **verifier**, and a W-C row has no digest. Choosing a candidate on a title alone would be the one
   guess this entire gate exists to forbid. Cost: one verified read verb and three defaulted harness
   seams. Benefit: W-D is closed with proof rather than assumption, and a W-C residual is now
   reported with an address instead of a search.

---

DECISION_GATE_STATE: CLEAR

```decision-gate
{
  "run": "run_c2166e75bb02",
  "phase": "DESIGN",
  "iteration": 5,
  "state": "CLEAR",
  "reason_code": null,
  "evidence": "Iteration 5 correction round -- the final DESIGN iteration -- re-observed at HEAD c279005d0c2c743cbb6111b802efd7ff3797ac35 (branch main, no tracked file modified; only untracked artifacts/ run directories): `python3 scripts/validate_skills.py` -> 'Skill validation PASSED (732 checks) / Validated both skills, shared templates/reviews, routing, and policy gates.' with exit 0; `python3 -m unittest discover -s scripts -p 'test_*.py'` -> 'Ran 2014 tests in 335.068s' / 'OK (skipped=6)' with exit 0. Both counts are identical to iterations 1-4 and to the reviewer's independent reruns, as expected for a phase that changes no code. This round addressed exactly one finding, F-002 as restated after iteration 4, and every decisive fact was executed by me against the live Orca 1.4.197 runtime rather than taken from the coordinator's summary. VERIFIED: `orca terminal create --help` documents --worktree as accepting identity:<identity>, id:<repo-id>::<path>, name:<displayName>, branch:<branch>, issue:<number>, path:<path>, or active/current -- so the finding is correct that active/current are aliases and that persisting the literal word 'current' persists no worktree. VERIFIED: `orca worktree current --json` returned ok=true with result.worktree.id = '7b6ee134-05a2-4dbd-be45-91ea1c2d2d7e::/Users/luminous/aiAssistedProjects/orca-skills', the full <repo-id>::<path> form; ORCA_WORKTREE_ID in the process environment carries the identical string; and `orca terminal list --json` reports that same value as worktreeId on every element. VERIFIED: the harness ALREADY executes `worktree current` in validate_orca_contract and already reads exactly current['result']['worktree']['id'] into its worktreeId capability field (orca_runtime_harness.py:1754-1759), and contract validation runs at harness construction, strictly before any dispatch and therefore strictly before E1 -- which is why the stable value is available before the effect and cannot be lost with it. VERIFIED: `orca terminal list --worktree id:7b6ee134-...::/Users/luminous/aiAssistedProjects/orca-skills --json` returned ok=true with 20 terminals, all carrying that one worktreeId, so the id: selector resolves and is replayable. Resolution: S4.2.1 now resolves the creation worktree ONCE before E1 to ORIGIN_WORKTREE_SELECTOR = f'id:{worktree_id}', persists THAT in the PLANNED row as terminal_worktree, and hands the identical string to BOTH E2's `terminal create` and leg (4)'s `terminal list`; nothing re-resolves it. If it cannot be resolved -- unreadable call, ok:false, absent/empty id, or an id not of the form <repo-id>::<path> -- OrcaAdapter.start REFUSES BEFORE E1 with DISPATCH_UNACCOUNTED and creates no Task and no terminal; it explicitly does NOT fall back to the alias. A further defaulted harness seam was required and is stated: create_fake_terminal hard-codes --worktree 'current' (orca_runtime_harness.py:2000-2003), so it gains keyword-only worktree: str = 'current' whose default preserves today's behaviour exactly, plus a terminal_worktree passthrough on run_existing_task; both existing call sites (:3197, :3504) bind unchanged. Otherwise the PLANNED row would again have recorded a scope E2 did not honour. A SECOND HOLE was found while fixing the first and is closed with it, rather than left for the reviewer: an unresolvable selector does NOT error -- `orca terminal list --worktree id:00000000-0000-0000-0000-000000000000::/nope --json` returned ok=true with terminals:[] and totalCount:0, indistinguishable from a real but empty worktree -- so believing emptiness would reintroduce the defect one level down. S4.2.1a therefore requires the scope to be PROVED before any zero-candidate verdict, via `orca worktree show --worktree <recorded> --json`, whose grammar was read from --help ('--worktree <selector> [--json]') and whose behaviour was executed here: ok=true with result.worktree.id echoing the recorded id for the live selector, and ok=false with error.code='selector_not_found' for the bogus one. The guard passes only on ok=true AND a byte-identical echoed id; failure yields the new closed-vocabulary value scope_unresolved and refuses with DISPATCH_UNACCOUNTED, on the standing rule that unknown is never empty. Tests were fixed exactly as the finding required rather than cosmetically: T-08 and T-47 now create under one 'current' binding (worktree A, id:repoA::/wt/a, the value journalled and sent to E2) and recover with a fresh object whose 'current' resolves ELSEWHERE (worktree B, id:repoB::/wt/b), with the scripted terminal list yielding the matching element only under A and an empty array under B and under current/active; recovery must still succeed, and the assertions are made on the real command log harness._raw -- E2's `terminal create` and leg (4)'s `terminal list` carry a BYTE-IDENTICAL --worktree string, that string equals the terminal_worktree read back from .settlement_journal.json, it begins with 'id:' and contains '::', and neither 'current' nor 'active' appears as a --worktree argument anywhere in the run. A negative twin journals the literal alias and requires the recovery to FAIL, so a future regression to alias replay cannot pass, and two further twins cover the scope guard (selector_not_found, and a mismatched echoed id). S13.4 carries the same statement as a suite-wide property, so it is an invariant rather than one test's assertion. F-001 and F-003 were not reopened and remain RESOLVED. PLAN F-001 is not regressed: the checkpointed WorkflowState remains the sole reconstruction authority and the journal remains explicitly not an execution-state authority. W-C keeps its verdict, its TERMINAL_ORPHAN_POSSIBLE code, its residual disposition and its withheld AC-1. The plaintext handle still never enters the checkpoint, the journal or the durable receipt. No production code, test, configuration or SKILL document was changed in this phase; no branch, no staging, no push; no REVIEW_*.md was edited.",
  "assumption": null,
  "open_item": null,
  "responsible_phase": "DESIGN",
  "role": "worker",
  "verdict": "COMPLETE",
  "source_binding": "branch main, HEAD c279005d0c2c743cbb6111b802efd7ff3797ac35, tracked worktree clean (only untracked artifacts/ run directories)",
  "recorded_at": "2026-09-05T19:20:00Z",
  "boundary": "B2",
  "open_decision_item": false,
  "grounds": "Every choice in this final correction round was determined by the approved ANALYSIS and PLAN, the ticket's acceptance criteria, F-002's own stated required action, and Orca capabilities I executed and observed first-hand at this HEAD. The finding offered two acceptable resolutions and preferred the stable-selector path where it genuinely works; I verified by execution that a stable id:<repo-id>::<path> selector is obtainable before E1 through a call the harness already makes, so the choice was settled by evidence rather than preference and the alternative (classify the window as an enforced limitation and withhold AC-1) was correctly not taken. The one judgement I made beyond the literal required action -- adding a `worktree show` scope guard because I observed that an unresolvable selector returns ok:true with an empty array -- was forced by the same fail-closed principle the finding applied, is strictly more conservative, and refuses rather than proceeds when in doubt, so it needed no user authority. Writing a design document created no irreversible action and no monetary, security, privacy or compliance consequence and no long-term lock-in. This record concerns the authoring of this document; it is not about the pause/resume machinery the document specifies, whose own fail-closed refusals are design content rather than a gate state of mine.",
  "scope": "The DESIGN phase iteration-5 correction deliverable for OS-31: artifacts/runs/run_c2166e75bb02/DESIGN.md, updated in place, resolving the single remaining blocking finding F-002 (the persisted worktree scope was the alias 'current' rather than a stable identity) and leaving F-001's and F-003's resolutions intact. Sections changed: header, Validation Baseline, S3 vocabulary (HANDLE_RECOVERY_OUTCOMES gains scope_unresolved), S4.2.1 (PLANNED row, the new origin-worktree resolution rule and its fail-closed refusal, the worktree seam, the W-D crash-window row), S4.2.1a (candidate enumeration, the zero-length-listing scope guard, the decision table's zero-match cell and the new scope_unresolved cell), S4.4, S10.2 (recover_handle translation, seam list, Orca grammar discipline, FakeAdapter), S12 file map, S13.3 regressions 1 and 17, S13.4, the iteration-4 summary table, Review Feedback Resolution (new F-002 iteration-5 entry and scope discipline) and Decision Record. No production code, test, configuration or SKILL document; no branch, staging or push; no other artifact written; no REVIEW_*.md edited; ANALYSIS.md, PLAN.md and the REVIEW_*.md files read only; run_8e8f9451ad44 neither read nor written.",
  "classification_attempted": true,
  "reversibility": "reversible_in_run",
  "blast_radius": "current_change",
  "monetary_cost": false,
  "security": false,
  "privacy": false,
  "compliance": false,
  "long_term_lock_in": false,
  "impact": "Closes the last gap between what the OS-31 design claims a fresh Coordinator can do and what it can actually execute. Iteration 4 made terminal provenance durable before the crash window but recorded the recovery's SCOPE as an alias that the reading process re-resolves, so a successor bound to another worktree would have enumerated the wrong place, found nothing, and refused -- fail-closed but not closed, and invisible to tests that replayed the same alias. The recorded scope is now a stable repo-id-plus-path identity resolved before E1 and replayed byte-for-byte by both the effect and its recovery, so window W-D is genuinely closed and the orphan/terminal-leak evidence G1 asks for is producible by a process that shares nothing with the one that died. It also hardens the 'absent' verdict itself: because an unresolvable selector was observed to return an empty listing rather than an error, absence must now be proved by `worktree show` before it is believed. Nothing here changes runtime behaviour by itself; it changes what IMPLEMENTATION is instructed to build and what TEST is instructed to prove."
}
```
