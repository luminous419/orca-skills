# Worker Result

STATUS: COMPLETE

## Request Summary

Jira OS-21 asks whether Orca's internal Final Adversarial Review (SKILL.md §17) actually
catches the same class of blocking (CRITICAL/MAJOR) defects that an external ChatGPT/human
PR review catches, and — where it does not — why. This is a MEASURE / DIAGNOSE / EXPLAIN
phase. No lifecycle, Risk, Quality Profile, Agent Profile, or Final Review contract change
is made here; root-caused improvement items are named for a separate backlog and their
design is deferred to PLAN.

Corpus: PRs #10–#18 of `luminous419/orca-skills`, their GitHub review threads (`gh api
pulls/<n>/reviews`), the local run artifacts under `artifacts/runs/*` and `artifacts/*.md`,
and `git show` of the exact heads each reviewer read.

## Current State

### What §17 specifies today

§17 (193 lines, `SKILL.md:1698-1890`) makes the Final Adversarial Review a mandatory,
non-disableable global gate that runs after every requested phase passes its phase gate. It
is not a third role: it is a §11 Reviewer instance in a **fresh terminal per attempt**, with
one extra finding field (`Responsible Phase`) and a T0–T5a state machine. Its search axes are
nine lines:

```text
A objective alignment   B cross-phase consistency   C contract vs implementation
D implementation vs tests   E docs vs behavior   F lifecycle state machine
G security destructive   H over-engineering   I hidden coupling
```

Three structural properties of the current contract matter to this analysis and are facts
about the repository, not inferences:

1. **No Final Review policy artifact exists.** Every phase has `reviews/<phase>.md`
   (analysis/plan/design/implementation/test/bugfix/refactoring). The one mandatory global
   gate has none. Its entire normative content is the A–I block plus one paragraph inside
   §17; the rest of §17 is lifecycle/state machine.
2. **The strongest anti-anchoring machinery in the skill is explicitly scoped away from the
   Final Reviewer.** §11's `#### Reviewer context contract` carries rule 1 ("delta는 시작점이지
   경계가 아니다"), rule 3 ("`approved_baseline`은 immutable truth가 아니다"), and
   `REVIEWER_DRILL_DOWN = mandatory_and_unrestricted`. Rule 6 and
   `REVIEWER_CONTEXT_EXCLUDES = final_adversarial_review` (`SKILL.md:1300,1310`) exclude the
   Final Reviewer from all of it. What §17 keeps is a prohibition — "앞선 phase gate가 PASS였다는
   사실을 옳다고 가정하지 않는다" — not a positive obligation to re-derive.
3. **The stopping rule is the first PASS.** `T1 attempt PASS → STATUS: COMPLETED`. Nothing
   requires a PASS to be reproducible, to have inspected at least as much as a prior FAIL
   attempt, or to explain a disagreement with a prior attempt.

Items 1, 2 and 3 are contract facts, established by reading the skill. Whether any of them
*caused* an observed miss is a separate question that the retained run evidence does not settle;
those causal readings are carried below as hypotheses H-4, H-1 and H-2 respectively, not as
findings.

### What actually happened in the field

All 9 PRs (#10–#18) received an external review. Every external review author is
`luminous419` posting an external (ChatGPT/human) review as a GitHub PR review — a channel
distinct from the internal `FINAL_REVIEW*.md` artifacts. Across 23 external review rounds:
**16 MAJOR findings, 0 CRITICAL.** 8 of 9 PRs required at least one external MAJOR fix; only
PR #17 was clean on the first external round.

## Findings

**Evidence tiers used below.** OS-21 requires misses to be root-caused *with evidence*, so every
causal claim in this section carries a tier:

- **DEMONSTRATED** — the retained record establishes the claim directly: an artifact says it, a
  `git show` of the exact reviewed head shows it, or a log row records it.
- **HYPOTHESIS** — an explanation *consistent with* the demonstrated evidence but not established
  by it, because the retained record does not contain the reviewer's input or reasoning. These
  are candidate causes requiring controlled validation before any lifecycle change is justified
  by them.

Hypotheses are numbered H-1 … H-4 and introduced at the point they first arise (H-4 in F3, H-1 in
F4, H-2 and H-3 in F5). A contract *gap* being demonstrated never by itself promotes the claim
that the gap caused an observed miss — that claim is always a separate, tiered statement.

The distinction is load-bearing here. No Final Review *input* spec is retained for any run in the
corpus (§9 has no artifact contract for it), and for the run where attempt-to-attempt
disagreement is sharpest (PR #18) the accepted PASS attempt left no report file at all. Reviewer
*decisions* are therefore observable throughout; reviewer *reasoning* mostly is not.

### F1 — Yes. There are real, reproducible cases where Internal Final Review PASSed and the external review then found a new blocking finding. (Core Q1)

Five runs have a recoverable internal Final Review record *and* an external review of the
same head. Timeline anchors below are UTC, from `gh pr view --json mergedAt`,
`artifacts/runs/*/ORCHESTRATOR_LOG.md` rows, and artifact mtimes.

| PR | Ticket | Internal FR record | Internal verdict on the head the external reviewer read | External MAJOR on that head | FR caught it? |
|---|---|---|---|---|---|
| #12 | orchestration efficiency | `artifacts/ORCHESTRATION_EFFICIENCY_ORCHESTRATOR_LOG.md` §"Final Adversarial Review (2 attempts)": attempt 1 FAIL, attempt 2 PASS | **PASS** (head `dfe5eed`) | 1 | **No** (caught an adjacent structural defect — see F3) |
| #14 | OS-1 quality profiles | `artifacts/runs/run_bf55f06dd7fc/FINAL_REVIEW.md`, PASS, written 07:25Z, head `c6a5503` | **PASS** | 1 | **No** |
| #16 | OS-3 risk workflows | `artifacts/runs/run_e0cdf1afae58/FINAL_REVIEW{,_iteration2,_iteration3}.md`: FAIL, FAIL, **PASS** 04:37Z, head `27690cc` | **PASS** | 1 | **No** |
| #17 | OS-19 timing log | `artifacts/runs/run_ec18ea04bc22/ORCHESTRATOR_LOG.md`: attempt 1 FAIL, attempt 2 PASS 09:17Z, head `087c836` | **PASS** | 0 | **n/a — clean** |
| #18 | OS-4 agent profiles | `artifacts/runs/run_c854db299e7a/ORCHESTRATOR_LOG.md`: 3 dispatches, PASS 14:32Z, head `0287271` | **PASS** | 2 | **No** |

Three further PRs bound the picture:

- **#11** — the PR that *introduced* §17 — shipped with no Final Review run at all.
  `artifacts/ORCHESTRATOR_LOG_run_1955ca9863a7.md` contains zero final-review rows, and the
  external MINOR-1 on that PR says exactly this ("the attached timing/orchestrator logs do
  not contain an actual Final Adversarial Review Task/Dispatch for the PR-creation run").
  External review then found 2 MAJOR.
- **#13** and **#15** have no recoverable internal Final Review artifact. #15 needed **five**
  external rounds and 6 MAJOR findings before MERGE-READY.
- **#10** predates the gate entirely.

**Aggregate on the measurable subset (#12, #14, #16, #17, #18):**

```text
external blocking findings present in the head the Final Review PASSed : 5
independently found by the Final Reviewer                              : 0
external CRITICAL/MAJOR recall                                         : 0 / 5  = 0%
runs where an internal PASS was followed by >=1 external blocking      : 4 / 5  = 80%
```

### F2 — The Final Reviewer is precise, not noisy. The failure is recall, not discipline.

Internal Final Reviewers issued **6** blocking findings in the recoverable corpus. Of the 4
that entered the lifecycle, **0** were later disputed or withdrawn, and all 4 were real
defects that were fixed:

| Finding | Run | Defect | Outcome |
|---|---|---|---|
| FINAL-I1-MAJOR-1 | #12 attempt 1 | Task boundary built *after* `start_worker()` — the advertised safety boundary was never delivered to any agent | corrected + downstream revalidation |
| R1 | #16 attempt 1 | §12/§17 phase transitions expressed as "Reviewer PASS", impossible at LOW | corrected in DESIGN |
| R1 | #16 attempt 2 | §17 T4 still routes every Final Review correction through a phase Reviewer | corrected in DESIGN |
| R1 | #17 attempt 1 | NaN duration reachable in TIMING_LOG | corrected in BUGFIX |

Non-blocking findings issued across the whole corpus: 3 (all in PR #12's Final Review, all
accurate and correctly non-blocking). Evidence grounding is consistently good: every internal
Final Review artifact cites `file:line` locations, quotes the diff range it read, and re-runs
and reports `validate_skills.py` / `unittest discover` / `verify_package.py` /
`git diff --check` totals itself rather than trusting the Worker's numbers.

```text
blocking false-positive rate      : 0 / 4   = 0%
findings citing concrete evidence : 9 / 9   = 100%
independent re-run of validation  : 5 / 5 runs
```

This matters for the recommendation: the gate is **conservative**, and the fix direction is
*not* "raise strictness" or "widen the checklist" — that would break the deliberate
`Not Blocking by Default` minimalism in `reviews/common.md`. The gap is where it looks.

### F3 — All five missed findings share one archetype (negative-space defects), and §17 states no falsification obligation. Both are DEMONSTRATED; that the missing obligation *caused* the misses is a HYPOTHESIS (H-4). (Core Q2, Q3)

**DEMONSTRATED 1 — the archetype.** All five missed external MAJORs are **negative-space**
defects — a fallback branch, a losing precedence path, an equality case of an inequality claim,
a value inside a structure verified as present, a scope boundary of a validation. Each was verified present at the internally-
PASSed head by `git show`.

| # | PR | Missed finding | What the Final Reviewer did instead | Verified at |
|---|---|---|---|---|
| M1 | #12 | `dispatch_context()` sets `current_phase=mode`, so the delivered Task boundary says `current_phase: complete` / `pass`, not the real phase | Attempt 2 confirmed the boundary is now "rendered before dispatch and present in the actual `--spec`" — presence, not value | `git show dfe5eed:scripts/orca_runtime_harness.py:388,401` → `current_phase=mode`; fixed only at `33bff91:435` |
| M2 | #14 | `run_attempt()` never carries the run's requested phase set, so Final Review silently falls back to `ALL_APPLICABLE_PHASES` | FR confirmed `build_quality_gate_context()` *supports* `requested_phases` and that rendered specs carry the profile block — the supporting function, not the omitting call site | `git show c6a55038:scripts/orca_runtime_harness.py:531-532` → `scope = requested_phases or (ALL_APPLICABLE_PHASES if phase == FINAL_REVIEW_PHASE else ())` |
| M3 | #16 | The documented churn invariant (strict `MEDIUM<HIGH` whenever any correction occurs) is false: phase-local correction, and a Final Review correction on the last requested phase, both give `MEDIUM==HIGH` | FR attempt 3 checklist B read that exact claim and **blessed** it: "The documented clean-first-pass and all-specialized `MEDIUM == HIGH` exceptions are preserved while `LOW < MEDIUM` remains strict" | `git show 12c60a4^:.../SKILL.md:718-719` — the false text was live at the PASSed head |
| M4 | #18 | `discover_agent_profiles()` parses *both* sources eagerly, so a malformed `~/.orca/agent-profiles.yaml` rejects a valid project-local profile despite documented whole-definition precedence | Both retained FR reports confirm the **precedence order** as implemented, plus its passing tests — the affirmative winner path. Neither retained report examines what happens when the *losing* source fails to parse | `git show 0287271:scripts/agent_profile.py` — loop iterates all candidates before returning |
| M5 | #18 | Static command safety applied only to `required_entries()`, while `evidence_rows()` writes every routing entry's raw command verbatim into run-scoped audit logs | Both retained FR reports affirm the narrow scope as correct ("required commands still pass token, allowlist, and PATH gates") — a confirmation of the required path, never a check of the non-required one. Neither is the accepted PASS attempt's report (F5) | `git show 0287271:scripts/agent_profile.py` — `evidence_rows()` writes every entry's raw command; only `required_entries()` is gated |

**Provenance note on the two #18 rows.** The accepted PASS attempt for PR #18 left no report
file, so its reasoning is unrecoverable (F5). The "what the Final Reviewer did instead" column
for M4 and M5 therefore quotes the two *retained* Final Review reports from that run
(`artifacts/FINAL_REVIEW_agent_profile_separation.md`,
`artifacts/runs/run_c854db299e7a/FINAL_REVIEW.md`), whose dispatches failed at `dispatch_input`
and whose verdicts were correctly voided on provenance grounds. Those reports are used here only
as retained *reasoning traces of Final Reviewer instances reviewing the same head* — the
archetype claim — never as accepted verdicts. The miss itself (M4/M5 present at the PASSed head,
absent from `FINAL_FINDINGS`) is established independently of them by `git show` and the run's
`FINAL_RESULT.md`. M1–M3 quote their runs' accepted FR artifacts directly.

**DEMONSTRATED 2 — the contract coverage gap.** §17's A–I entries are *topic labels*
("objective alignment", "cross-phase consistency", "contract vs implementation", …), not search
procedures. Read literally, the nine lines impose **no** obligation to look for a counterexample,
to enumerate call sites, to walk a negative path, or to test an equality case, and §17 contains no
explicit falsification or search-depth requirement anywhere else either. This is a fact about the
contract text, verifiable by reading `SKILL.md:1698-1890`, and it pairs with Current State item 1
(no `reviews/final_review.md` policy artifact exists to carry such an obligation). What §17 *does*
state on adjacent ground — a fresh adversarial review per attempt, "do not assume the prior phase
gate's PASS was correct", and unrestricted direct verification of the final repository state — is
orthogonal to search depth: none of it tells a reviewer when a search may stop, in either
direction.

**Consistent-with observation, not itself evidence of a cause.** The FR artifacts' own
`## Evidence Checked` sections read as lists of confirmations ("Confirmed the rendered Task model
orders decisions as…", "Verified `round_kind` is constrained to…"), never as a record of a failed
search for a violation. That describes what the reports *recorded*, not what the reviewers *did*:
no contract requires a reviewer to record a search that found nothing, so a confirmation-only
report is equally consistent with a reviewer who did hunt for counterexamples and found none in
the places looked.

**Evidence availability check (Core Q: was the evidence there?)** — 5/5 yes. M2 and M4 sit in
files the FR artifact explicitly lists as inspected. M3 sits in the same `SKILL.md` section
the FR quoted. M1 is in the function the FR named. M5 is in the code path the FR blessed.
**Scope check** — 5/5 in scope under axes A/C/D/E. No miss is attributable to missing
evidence or an out-of-scope finding.

**H-4 (HYPOTHESIS — the best-supported one in this report; needs a controlled test) — the
affirmative-only search contract.** The explanation joining DEMONSTRATED 1 to DEMONSTRATED 2 is
that the absence of an explicit falsification obligation is what let each reviewer stop after one
confirming instance, producing the 5/5 negative-space archetype. This analysis considers it the
most likely explanation and its most directly actionable one: it is consistent with all five
misses, it names an editable contract surface, and the competing explanations are individually
weaker on this corpus (capability is present — the same reviewer channel produced 4 real,
well-evidenced MAJORs in the same runs, F2; visibility and scope are excluded — 5/5 misses had
their evidence available and in scope, immediately above). It is nonetheless **not demonstrated**:

- No Final Review *input* spec and no reviewer search procedure is retained for any run in the
  corpus, so nothing in the record shows that any reviewer stopped *because* the checklist asked
  for no more. "The contract permitted stopping" is demonstrated; "the contract caused stopping"
  is not.
- Model / per-attempt variance is not ruled out. Five misses across five runs is also consistent
  with a search-depth lottery that rewording the checklist would not change.
- For M4 and M5 specifically, the only quoted reasoning traces come from the two dispatches that
  failed at `dispatch_input` and were voided (provenance note above); the accepted PASS attempt
  produced no report at all, so its search behaviour is unrecoverable.

Separating the two readings requires the controlled comparison this report proposes rather than
performs — a seeded-defect fixture reviewed with and without an explicit falsification obligation
(Recommended Next Step item 1, enabled by item 2). Until that runs, H-4 is an evidence-supported
inference, not a settled root cause, and the effectiveness of the intervention it motivates must
be **measured**, not assumed.

### F4 — The Final Review did not independently re-derive an accepted DESIGN-Reviewer scope decision, and a cross-phase defect survived it. Why it survived is UNKNOWN. (Core Q4)

The contract's language is a prohibition on assuming, and §11's operational anti-anchoring
machinery is excluded from the Final Reviewer (Current State, item 2). Three things are
DEMONSTRATED below; the mechanism behind them is not.

**DEMONSTRATED 1 — genuine independence does occur.** PR #11's and PR #12's post-correction
Final Reviews state it explicitly ("이전 PASS 판정은 근거로 간주하지 않고 최종 파일과
재대조했다") and re-ran all validation themselves. PR #16 attempt 3 is described in its own log
row as a "fresh independent A-I audit".

**DEMONSTRATED 2 — a cross-phase defect (M5) passed through a phase Reviewer, the Final Review,
and out to the external reviewer.** The requirement chain is fully recorded:

1. `artifacts/runs/run_c854db299e7a/REVIEW_DESIGN_iteration2.md`, finding **D-002-R1**
   (G1/MAJOR/blocking), *required* that token/allowlist validation be narrowed to materialized
   required routing only, and *required* a negative test proving an out-of-request `bash` value
   does not block the run.
2. The Worker complied (log row `design worker 4 … resolved D-002-R2`), and the accepted
   implementation encodes exactly that scope (`git show 0287271:scripts/agent_profile.py`:
   `required_entries()` is gated, `evidence_rows()` is not).
3. Both retained Final Review reports for that run *affirm* the narrow scope as correct rather
   than raising it as a question — `artifacts/FINAL_REVIEW_agent_profile_separation.md:42`
   (checklist G: "required commands still pass token, allowlist, and PATH gates") and
   `artifacts/runs/run_c854db299e7a/FINAL_REVIEW.md:40` ("Required routing만 safe-token,
   allowlist, PATH 순서로 검증되고").
4. The run completed on a PASS with `FINAL_FINDINGS: none`
   (`artifacts/runs/run_c854db299e7a/FINAL_RESULT.md:159`).
5. The external reviewer then raised exactly this MAJOR and named the regression test the design
   reviewer had ordered: *"The new regression `test_an_out_of_request_phase_command_is_still
   _untouched` explicitly locks this behavior in."*

The distinction the internal chain missed — static token/allowlist safety belongs to the whole
selected profile definition, PATH availability belongs to required roles only — was never drawn
until the external round. That the defect *originated in a prior phase Reviewer's own accepted
blocking finding* is a fact about the requirement chain and holds independently of any claim
about how the Final Reviewer reasoned.

**DEMONSTRATED 3 — a responsible-phase mapping gap.** §17's ladder maps "production code 동작
결함 / 계약 위반 → IMPLEMENTATION". M5's true owner is a DESIGN Reviewer's accepted finding. The
ladder has no rung for "a prior phase Reviewer's blocking finding was itself wrong." This is a
contract-text gap, verifiable by reading §17, and is independent of why M5 was missed.

**UNKNOWN — the mechanism.** Nothing in the retained record shows *why* the Final Reviewer
affirmed the narrow scope instead of re-deriving it from the ticket's fail-closed contract:

- No retained Final Review artifact cites D-002-R1, the DESIGN review, or any prior phase verdict
  as a reason. Negative check performed:
  `grep -c "D-002" artifacts/FINAL_REVIEW_agent_profile_separation.md artifacts/runs/run_c854db299e7a/FINAL_REVIEW.md`
  → `0`, `0`. `D-002` appears only in `REVIEW_DESIGN_iteration2.md` and `ORCHESTRATOR_LOG.md`.
- The accepted PASS attempt (`task_d3f49c042d5a`) left **no report file at all**; only its verdict
  row survives. The Coordinator's second-hand summary of it in `FINAL_RESULT.md` names four
  things that attempt verified, and the token/allowlist scope is not among them.
- No Final Review *input* spec was retained for any run, so what any reviewer was actually shown
  about D-002-R1 is unrecoverable.

Inheriting an upstream scope decision and independently arriving at the same narrow reading are
observationally identical in this record.

**H-1 (HYPOTHESIS — consistent with the evidence, not demonstrated) — prior-decision anchoring.**
§11's rule 1 / rule 3 / `REVIEWER_DRILL_DOWN = mandatory_and_unrestricted` are excluded from the
Final Reviewer by `REVIEWER_CONTEXT_EXCLUDES = final_adversarial_review` (`SKILL.md:1300,1310`),
leaving a prohibition on assuming and no positive obligation to re-derive. M5 is consistent with
the Final Reviewer treating an accepted upstream scope decision as settled. It is equally
consistent with a reviewer independently making the same narrow reading unaided — as the phase
Reviewer and the Worker both did. Separating the two requires evidence this corpus does not
contain (a retained input spec plus a retained reasoning trace), so H-1 must be validated before
any lifecycle change is justified by it. The §17/§11 exclusion is itself a *structural* asymmetry
worth examining on contract-hygiene grounds; that argument does not depend on H-1 being true.

### F5 — Three dispatches of one gate on one head produced three different verdicts, and the accepted one is unauditable. DEMONSTRATED. Whether the first-PASS stopping rule caused the miss is a HYPOTHESIS. (Core Q5)

`artifacts/runs/run_c854db299e7a/FINAL_RESULT.md` and the run's `ORCHESTRATOR_LOG.md` record
three Final Review dispatches for one attempt:

```text
14:22:25Z  task_2d0a6f4fc5a4 / ctx_8251971fb59e  term_33295587…  FAILED at dispatch_input
                                                 (agent_prompt_blocked, spec ~14.8KB)
14:24:25Z  task_6b7d7a0cdd95 / ctx_a2ed3c36e1b9  term_113d023d…  FAILED at dispatch_input
                                                 (spec ~5.5KB)
14:32:36Z  task_d3f49c042d5a / ctx_71f59c521292  term_c164994d…  PASS (spec ~2.3KB)
```

Both blocked agents nevertheless kept working and produced complete review reports, still on
disk:

- `artifacts/FINAL_REVIEW_agent_profile_separation.md` (14:31Z) — **RESULT: FAIL**, R1
  MAJOR/G1: a selected profile can resolve a phase Worker and its required Reviewer to the
  same command, bypassing `WORKER_REVIEWER_MUST_DIFFER`.
- `artifacts/runs/run_c854db299e7a/FINAL_REVIEW.md` (14:32Z) — **RESULT: FAIL**, R1 MAJOR/G1:
  `E2EHarness.allocate_session()` reuses a same-role session across phases even when the
  profile changes that role's resolved command, and records a fixed fake-agent command as
  audit evidence.

**What the lifecycle did, and what that rules out.** Both dispatches failed at `dispatch_input`;
their capabilities were revoked and Orca rejected their `worker_done`
(`dispatch_capability_invalid`). Their reports were therefore **correctly voided on provenance
grounds**, and the Coordinator was right not to use them as review evidence. This bounds the
causal reading: **the lifecycle never held a valid prior FAIL that the PASS overrode.**
`T1 attempt PASS → COMPLETED` did not terminate a sequence of *accepted* contradictory verdicts,
and nothing in this run shows the stopping rule suppressing a verdict the lifecycle had accepted.

**DEMONSTRATED — verdict instability.** Three dispatches of the same gate against the same head
produced three different outcomes: two distinct MAJOR findings and one clean PASS. Whether either
voided finding is a true defect was never adjudicated, so this is not evidence of two further
missed defects — but it is direct evidence that the gate's verdict was **not reproducible** on
this head.

**DEMONSTRATED — the accepted PASS is unauditable.** The PASS ran on a spec roughly one sixth the
size of the first attempt's, shrunk purely to work around a dispatch-layer `agent_prompt_blocked`
threshold. That spec was not retained (§9 has no artifact contract for the Final Review input),
and the PASS attempt produced no report file — only a verdict row plus the Coordinator's
second-hand summary. `FINAL_RESULT.md` records `FINAL_FINDINGS: none` and asserts the *opposite*
of one voided finding (that `WORKER_REVIEWER_MUST_DIFFER` is *session*-scoped, not
command-scoped) with no retained reviewer artifact behind it. **Whether that attempt's
disagreement was a considered correctness judgment or a context artifact cannot be determined
from the evidence**, and no claim about it is made here.

**H-2 (HYPOTHESIS — needs validation) — the first-PASS stopping rule is a binding constraint.**
`T1 attempt PASS → STATUS: COMPLETED` requires no PASS to be reproducible, to have inspected at
least as much as any other attempt, or to explain a disagreement with one — that much is a
contract fact (Current State item 3). PR #18 shows a run completing on a verdict that was
demonstrably not reproducible and whose input was smaller and unrecorded, which is *consistent
with* the rule letting the least-resourced attempt settle the gate. But because no valid FAIL was
ever accepted, this corpus contains no instance of the rule actually suppressing an accepted
contradiction. H-2 needs a controlled test — e.g. two independent attempts on a seeded-defect
fixture — before a lifecycle change is justified by it.

**H-3 (HYPOTHESIS — weak, n=2) — attempt depth correlates with external outcome.** PR #16 spent 3
attempts with two real FAILs in between; PR #17, the only externally-clean PR, ran 2 attempts with
a real FAIL in between. The two PRs where the gate did more than one substantive attempt are the
two with the best external outcomes. No controlled variation — suggestive, not established.

**Factor assessment against the ticket's candidate list** (tier in the verdict column):

| Candidate cause | Verdict | Evidence |
|---|---|---|
| Prompt/checklist coverage | **HYPOTHESIS (H-4) — best-supported; needs validation** | DEMONSTRATED: 5/5 misses are negative-space defects, each verified present at the PASSed head by `git show`, and §17's A–I entries are topic labels stating no falsification/search-depth obligation (nor does any `reviews/final_review.md` exist to carry one). NOT DEMONSTRATED: that the missing obligation is what made reviewers stop — no input spec or search procedure is retained, and model/per-attempt variance is not excluded (F3) |
| Repo/diff inspection depth | **DEMONSTRATED (depth, not breadth)** | Breadth is good — diffs, file lists, validation all re-run. All 5 misses lie inside files the FR listed as inspected (F3) |
| Test-evidence inspection | **DEMONSTRATED** | In M5 the *test suite itself encoded the defect*; the retained FR reports read "773 tests OK" as evidence of correctness rather than asking which tests bless which behaviour |
| Verdict reproducibility / input auditability | **DEMONSTRATED (not on the ticket's candidate list)** | PR #18: three verdicts on one head; the accepted PASS's input spec *and* report are both unretained, so its reasoning cannot be audited now or later (F5) |
| Responsible-phase ladder coverage | **DEMONSTRATED (contract gap)** | §17's ladder has no rung for "a prior phase Reviewer's accepted finding was itself wrong", which is M5's true owner (F4) |
| Prior-decision anchoring | **HYPOTHESIS (H-1) — needs validation** | Consistent with M5 and with the §11 exclusion, but no retained FR artifact cites D-002-R1 and no input spec survives; independent same-mistake is observationally identical (F4) |
| Review budget / stopping rule | **HYPOTHESIS (H-2) — needs validation** | `T1 = first PASS completes` is a contract fact and PR #18 completed on a non-reproducible PASS — but no valid FAIL was ever accepted, so no suppression of an accepted contradiction is demonstrated (F5) |
| Context / spec budget | **HYPOTHESIS — mechanism unknown** | The ~6x shrink and the undocumented `agent_prompt_blocked` limit are demonstrated; because the spec was not retained, whether content loss changed the verdict is undeterminable (F5) |
| Model capability / agent assignment | **Not supported as primary** | The same reviewer channel produced 4 real, well-evidenced MAJORs elsewhere in the same runs, including two SKILL.md contradictions the external reviewer never found. Capability is present |
| Artifact visibility | **Not supported** | 5/5 misses had the evidence available and in scope |
| Task scope | **Not supported** | 5/5 misses were within §17's stated axes |

### F6 — After an *external* correction, a fresh Final Review almost never re-runs. (Contract verification)

| PR | fix commits driven by external review | fresh internal Final Review after them? |
|---|---|---|
| #11 | (post-review correction to `0bf13fb`) | **Yes** — `artifacts/FINAL_REVIEW_final_adversarial_review.md`, PASS |
| #12 | `33bff91` | **Yes** — `artifacts/FINAL_REVIEW_orchestration_efficiency_pr12.md`, PASS |
| #14 | `b3a1ff3b` | **No** |
| #15 | `2b8853aa`, `581f1f3d`, `3a548c98`, `5f9e9368`, `1b51634f` | **No** |
| #16 | `12c60a4` | **No** — `run_end COMPLETED` at 04:41Z, fix committed 05:55Z |
| #18 | `773d8b7`, `5d70aed`, `80f604c` | **No** — `run_end COMPLETED` at 14:32Z, PR created 15:18Z |

The two PRs where a fresh Final Review *did* re-run after external correction are the two
where the external re-review then agreed with no further blocking finding. In the four where
it did not, external review needed 1, 5, 1 and 3 further rounds respectively. This is
correlation on a small sample, not proof of causation, but it is the only configuration in
the corpus with a clean record.

### F7 — §17 contract verification against observed runs

| §17 claim | Observed | Evidence |
|---|---|---|
| Fresh terminal per attempt; no reuse of any prior attempt's or phase Reviewer's terminal | **HOLDS** | #16: attempts 1/2/3 on three distinct `created` terminals — independently confirmed by the external reviewer from the supplied log; #18: `term_33295587` / `term_113d023d` / `term_c164994d`, all distinct from the phase terminals `term_da6ae9eb` / `term_15620afd` |
| Does not share a session with the prior phase Reviewer | **HOLDS** | same rows; `action=created` on every final-review row |
| Does not assume prior PASS was correct | **STATED; COMPLIANCE NOT VERIFIABLE** | Explicit re-verification is evidenced in #11/#12. For #18/M5 the *outcome* is demonstrated — an accepted upstream scope decision was never re-derived and the defect shipped — but no retained artifact shows whether the reviewer relied on that decision (H-1, F4). The retained record cannot confirm or refute compliance |
| Directly verifies the final repository state | **HOLDS** | every FR artifact re-runs and reports validator/unittest/package/`git diff --check` totals itself |
| (Unstated) a PASS verdict is reproducible / its input is recorded | **NOT REQUIRED; NOT OBSERVED** | §17 requires neither. PR #18: three dispatches, three verdicts; the accepted PASS's input spec and report are both unretained (F5) |
| Directly inspects diff / source / test / artifact | **HOLDS in breadth, FAILS in depth** | diffs and file:line are cited; all 5 missed defects lie inside the cited files |
| Routes blocking defects to the responsible requested phase | **HOLDS mechanically** | #12 → implementation then T5a → TEST; #16 → design; #17 → bugfix; all logged with `Responsible Phase`. Ladder has no rung for a wrong prior-Reviewer finding (F4) |
| Fresh Final Review re-runs after correction | **HOLDS in-run; FAILS after external correction** | #16 attempt 3, #12 attempt 2 vs. F6 |
| Identical at every risk level | **UNKNOWN** | every recoverable run is `risk=high` (#16, #17, #18 log rows). No LOW or MEDIUM Final Review exists in the corpus — and LOW is precisely where §17 is the *only* gate |

## Impact Scope

- **Correctness of the COMPLETED claim.** A run reaching `STATUS: COMPLETED` currently
  asserts that a global adversarial gate found no blocking defect. On this corpus that claim
  carried 0% recall against an external reviewer reading the same head. `COMPLETED` is
  therefore not evidence of merge-readiness, which is how the final report reads today
  (`FINAL_REVIEW: PASS` / `FINAL_FINDINGS: none`).
- **Highest exposure at LOW risk.** At LOW there is no phase Reviewer; §17 is the only
  verification gate in the entire run. The corpus contains no LOW Final Review at all, so the
  gate's weakest-evidenced configuration is also its most load-bearing one.
- **Files/contracts implicated (analysis only, unchanged here):**
  `orca-worker-reviewer-orchestration/SKILL.md` §17 (`:1698-1890`), §11 rule 6 /
  `REVIEWER_CONTEXT_EXCLUDES` (`:1300,1310`), §16 final report template (`:1660-1690`),
  §9 artifact path contract; the absent `orca-worker-reviewer-orchestration/reviews/final_review.md`.
- **Measurement infrastructure.** `artifacts/` is **untracked** (`git status` → `?? artifacts/`;
  `.gitignore` excludes only some subpaths). PR #13's and PR #15's internal Final Review
  artifacts are already unrecoverable. The whole evidence base for this ticket is unversioned
  local state.
- **No production code path is implicated.** Every defect discussed here was found and fixed;
  the current `main` contains the corrected forms (verified by `git show` on `12c60a4`,
  `33bff91`, `b3a1ff3b`, `5d70aed`, `80f604c`).

## Dependencies / Constraints

- **Structural invariants held (ticket requirement):** no phase lifecycle, Risk semantics,
  Quality Profile semantics, Agent Profile semantics, or Final Review correction/revalidation
  lifecycle was modified. No prompt or contract file was edited. This phase produced findings
  only.
- **Quality gate:** `.orca/quality-profile.yaml` is absent (only
  `.orca/quality-profile.example.yaml` exists), so only Explicit Requirements + the ANALYSIS
  phase contract + G1–G5 apply.
- **`reviews/common.md` minimalism is a hard constraint on any future fix.** The
  `Not Blocking by Default` list and the four-tier decision priority are deliberate and are
  working (F2: 0% false-positive rate). Any improvement must add *search depth*, not new
  blocking criteria.
- **External review is a single reviewer channel**, not an independent panel; it is a
  reference standard, not ground truth. Defects neither channel found are invisible to this
  measurement, so all recall figures are upper bounds on the true miss count, never lower.

## Risks

- **R-A (high) — Over-correcting into noise.** The obvious reaction to 0% recall is a longer
  checklist. `reviews/common.md`'s minimalism and the observed 0% false-positive rate say the
  opposite: broadening blocking criteria would trade a working property for a speculative one.
- **R-B (high, DEMONSTRATED) — Non-reproducible verdicts on an unrecorded input.** PR #18
  produced three different verdicts on one head, and the accepted PASS's input spec and report
  are both unretained. The demonstrated consequence is already visible in this ticket: that PASS
  cannot be root-caused now, and on the current contract the next OS-21 will hit the same wall.
  A checklist fix alone leaves the gate's verdict unreproducible and its reasoning unrecorded.
  *(Whether the first-PASS stopping rule is what let the least-resourced attempt settle the gate
  is H-2 — consistent with the evidence, not established. See F5.)*
- **R-C (medium) — Cost.** Every candidate direction (mandatory falsification pass, a second
  independent attempt, re-running FR after external correction) increases Final Review
  dispatches per run. PR #16 already spent 3 attempts and PR #18 5 dispatches.
- **R-D (medium) — The dispatch-layer size limit is unresolved and undocumented.** PR #18 hit
  `agent_prompt_blocked` twice; the Coordinator's only mitigation was to shrink the spec. Any
  fix that enriches the Final Review input will hit the same wall.
- **R-E (medium) — Evidence loss.** With `artifacts/` untracked, the corpus this ticket rests
  on can be deleted at any time, and future OS-21-style validation would have to start over.

## Assumptions / Unknowns

- **Assumption (well-supported):** the GitHub PR reviews authored by `luminous419` are the
  external ChatGPT/human channel and are distinct from the internal `FINAL_REVIEW*.md`
  artifacts. Supported by their form (`External review — NOT MERGE-READY`, CRITICAL/MAJOR/MINOR
  counts, MERGE-READY verdicts), by their post-hoc timing relative to `run_end`, and by fix
  commits that name them ("Address PR #15 third review round", "PR #18 re-review").
- **Unknown — what the PASSing PR #18 attempt was actually given, and what it concluded.** The
  ~2.3KB spec was not retained; §9 has no artifact contract for the Final Review *input*. The
  attempt also produced **no report file** — only a verdict row and the Coordinator's
  second-hand summary. Whether its disagreement with the two voided attempts was a considered
  correctness judgment or a context artifact **cannot be determined from the evidence** and is
  not asserted here.
- **Unknown — the reviewer-decision mechanism behind every miss.** No Final Review input spec is
  retained for any run in the corpus. Reviewer *decisions* are observable; reviewer *reasoning*
  is only partly so, and not at all for the one accepted PASS most in question. H-1 (anchoring)
  and H-2 (first-PASS stopping rule) are therefore hypotheses consistent with the evidence, not
  demonstrated causes, and are labelled as such throughout F4, F5, and the recommendations. The
  same limit applies to H-4 (the affirmative-only search contract, F3): the negative-space
  archetype and §17's missing falsification obligation are both demonstrated, but no retained
  record shows that the second produced the first, and H-4 is labelled a hypothesis throughout
  F3, the factor table, and the recommendations.
- **Negative check performed, and it settles nothing either way.** `D-002` appears in no Final
  Review artifact (only in `REVIEW_DESIGN_iteration2.md` and `ORCHESTRATOR_LOG.md`). Absence of a
  citation is consistent with both anchoring and independent agreement; it is recorded so a
  future validation does not have to redo it.
- **Unknown — whether the two voided PR #18 findings are true defects.** They were correctly
  discarded on provenance grounds and never independently adjudicated. They are reported as
  *attempts disagreeing*, not as confirmed defects.
- **Unknown — LOW and MEDIUM behaviour.** Zero coverage in the corpus (F7). Any claim about
  §17's `RISK_INDEPENDENCE` at LOW is currently unfalsifiable from field data.
- **Unknown — PR #13 and PR #15 internal verdicts.** Artifacts unrecoverable. Excluded from
  all rates rather than assumed; including them could only lower recall further, since both
  drew external MAJORs (1 and 6 respectively).
- **Not measured — defects both channels missed.** Out of reach without a seeded-defect
  fixture; noted as the reason every figure here is an upper bound on recall.

## Recommended Next Step

PLAN should scope, prioritise, and split the following into a separate Jira backlog; it
should **not** design fixes for all of them, and per the ticket only minimal
fixture/harness work needed to make this validation repeatable may be implemented in this run.

Items are ranked by **evidential support and the directness of the gap they close**, and every
item carries its evidence tier. Item 1 is ranked first because the gap it closes is demonstrated
and is the most directly editable surface in the contract — but its *causal* premise is H-4 and
must be measured, not assumed. Items 2–3 follow from demonstrated evidence alone. Items 4–6 are
hypothesis-driven and must be validated before they justify a lifecycle change (Core Q6).

### Ranked first — central intervention target (gap DEMONSTRATED, causal premise H-4)

1. **P1 (H-4) — Give the Final Review a falsification obligation.** Two things are demonstrated:
   5/5 misses are negative-space defects, each verified present at the internally-PASSed head,
   and §17's A–I entries are topic labels that state no falsification or search-depth obligation
   (F3). That the second *caused* the first is **H-4** — the best-supported explanation in this
   report, but an inference, not an established fact: no reviewer input spec or search procedure
   is retained and model/per-attempt variance is not excluded. This remains the change with the
   broadest evidential support and the first PLAN should scope. In scope for PLAN: whether it
   lands as a new `reviews/final_review.md` policy artifact (closing the asymmetry in Current
   State item 1) or as §17 text, and how to add depth without touching `Not Blocking by Default`
   (R-A). **PLAN must pair the change with the controlled measurement that would settle H-4 and
   size the change's real effect** — a seeded-defect fixture reviewed with and without the
   obligation, enabled by item 2 — and must state its expected impact as a hypothesis to be
   measured, not as a proven fix.

### Backed by demonstrated evidence

2. **P1 — Make the Final Review input and every attempt's verdict auditable.** Add an artifact
   contract (§9) for the FR input spec *and* for each attempt's report, and record/handle the
   `agent_prompt_blocked` size limit rather than silently shrinking the spec (R-D). Demonstrated
   need: PR #18 produced three verdicts on one head with the accepted one's input and report both
   unretained (F5, R-B). This is also the precondition for validating H-1, H-2 and H-4 at all — on
   the current record the sharpest causal questions in this ticket are structurally unanswerable,
   and will stay so.
3. **P2 — Add a `Responsible Phase` ladder rung for "a prior phase Reviewer's accepted finding
   was itself wrong."** M5's true owner is D-002-R1, a DESIGN Reviewer's own accepted blocking
   finding, and §17's ladder maps only to IMPLEMENTATION (F4, DEMONSTRATED 3). This is a
   contract-text gap and does not depend on H-1.

### Hypothesis-driven — validate before changing the lifecycle

4. **P2 (H-2) — Revisit the stopping rule.** `T1 = first PASS → COMPLETED` requires no PASS to be
   reproducible, to match another attempt's depth, or to explain a disagreement. PR #18 completed
   on a non-reproducible PASS, which is consistent with — but does not prove — the rule binding
   here, since no valid FAIL was ever accepted (F5). PLAN should treat this as a candidate
   requiring a controlled test (two independent attempts on a seeded-defect fixture, enabled by
   item 2) before evaluating options (reproducibility requirement, minimum-evidence floor,
   obligation to reconcile with prior attempts) against cost (R-C). Must not alter T2/T3/T4/T5a
   semantics.
5. **P2 (H-1) — Reconsider the Final Reviewer's exclusion from §11's anti-anchoring machinery.**
   `REVIEWER_CONTEXT_EXCLUDES = final_adversarial_review` withholds rule 1 / rule 3 /
   `REVIEWER_DRILL_DOWN = mandatory_and_unrestricted` from the one mandatory global gate. The
   *structural asymmetry* is demonstrated by reading the contract; that it *caused* M5 is H-1 and
   is not established (F4). PLAN may treat the asymmetry as a contract-hygiene question on its
   own merits, but must not present anchoring as a proven cause.
6. **P3 — Re-run the Final Review after an external correction.** The only two PRs that did
   (#11, #12) are the only two whose external re-review then found nothing. Correlation on a
   small sample (F6) — PLAN should treat this as a cheap policy change, not a proven mechanism.

### Enabling / coverage

7. **P3 — Preserve and version the evidence base.** `artifacts/` is untracked and two runs'
   Final Review artifacts are already gone (R-E). This is the one item that plausibly
   qualifies as the ticket's allowed "minimal evaluation fixture/harness change", and PLAN
   should decide whether it belongs in this run or the backlog. Pairs directly with item 2.
8. **P3 — Close the LOW/MEDIUM evidence gap.** §17 claims risk-independence; the corpus is
   entirely HIGH. LOW is where §17 is the sole gate.

No implementation, prompt change, or lifecycle change should be made until PLAN has scoped
these and the evidence for each item has been accepted — and item 1's causal premise (H-4) along
with items 4-6 should not be treated as settled root causes at any point in that scoping.
