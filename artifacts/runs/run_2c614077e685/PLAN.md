# Worker Result

STATUS: COMPLETE

## Goal

Deliver Jira **OS-21**'s required artifact: an evidence-backed **Final Adversarial Review
Effectiveness Report** answering whether Orca's internal Final Adversarial Review (SKILL.md
§17) catches the same class of blocking (CRITICAL/MAJOR) defects an external ChatGPT/human
review catches, plus a **prioritized, root-caused follow-up Jira backlog** — one ticket per
root cause, none bundled.

The report is the deliverable. It is built strictly on
`artifacts/runs/run_2c614077e685/ANALYSIS.md` (approved, PASSed at iteration 3, confirmed by
`artifacts/runs/run_2c614077e685/REVIEW_ANALYSIS_iteration3.md`). ANALYSIS's
**DEMONSTRATED / HYPOTHESIS** evidence tiers are binding: the Verdict rests on DEMONSTRATED
findings only; H-4 (the affirmative-only search contract), H-1 (anchoring) and H-2 (first-PASS
stopping rule) are hypotheses and are never stated as settled causes. H-4 is the best-supported
of the three and motivates a P1 intervention — but that intervention closes a *demonstrated
contract gap* while its *causal effectiveness* must be **measured** against a seeded-defect
fixture, not assumed. H-1 and H-2 drive only lower-priority, **validate-before-changing**
backlog items.

Per the ticket's Improvement Decision Rule, the verdict below is **B (PARTIALLY EFFECTIVE)**, so
this ticket defines improvements and splits them into follow-up tickets; it does **not** start
large improvement implementation.

## Scope / Out of Scope

**In scope (this PLAN phase):**
- The OS-21 report in full, with all ten required sections, reproducible against concrete PR
  numbers, commit SHAs, artifact paths, and finding IDs.
- Root-cause classification with evidence tier per root cause.
- Per-root-cause improvement proposals carrying expected impact, regression risk,
  implementation scope, and priority.
- A recommended follow-up ticket split, with a stated ticket count and rationale.
- An explicit determination of whether a harness/fixture gap blocks the follow-up tickets, and
  if so, a minimal explicitly-bounded work item for it (W-3).

**Out of scope (ticket Structural Invariants — unchanged here and not proposed for change
*inside OS-21*):**
- Phase lifecycle semantics, Risk semantics, Quality Profile semantics, Agent Profile semantics.
- Any edit to `orca-worker-reviewer-orchestration/SKILL.md`, `reviews/*.md`, or any prompt,
  contract, or runtime script. **No repository file outside
  `artifacts/runs/run_2c614077e685/PLAN.md` is created or modified by this phase.**
- Redoing ANALYSIS's investigation. Spot-checks were performed only to confirm internal
  consistency (results in W-0 §7 footnote).
- Design or implementation of any proposed improvement. Every proposal below stops at scope,
  impact, risk, and priority.

**Deliberately not proposed at all**, at any priority, in any ticket: broadening §17's
*blocking* criteria. ANALYSIS F2 measures a 0% blocking false-positive rate and
`reviews/common.md`'s `Not Blocking by Default` minimalism is working. Every proposal below adds
**search depth or auditability**, never new blocking criteria (see Risk R-A).

## Work Items

- **W-0 — Produce the OS-21 Effectiveness Report** (the deliverable; body follows in full).
- **W-1 — Define the follow-up Jira backlog** (W-0 §10; 8 tickets, one per root cause).
- **W-2 — Answer the ticket's harness/fixture-gap question explicitly.**
- **W-3 — Scope the minimal, bounded measurement-infrastructure work item.**

---

# W-0 — OS-21 Final Adversarial Review Effectiveness Report

## 1. Executive Summary

**Verdict: B — PARTIALLY EFFECTIVE.**

Orca's Final Adversarial Review is a **precise but shallow** gate. On the five runs where an
internal Final Review verdict and an external review of the *same head* both survive in the
record, the gate PASSed a head that the external reviewer then found blocking defects in, and
found **none of them independently**:

```text
external blocking (CRITICAL/MAJOR) findings present in the head the Final Review PASSed : 5
independently found by the Final Reviewer                                               : 0
external CRITICAL/MAJOR recall                                                          : 0 / 5 = 0%
runs where an internal PASS was followed by >= 1 external blocking finding              : 4 / 5 = 80%
```

It is nevertheless **not** ineffective. On the same corpus the gate issued **6 blocking findings
of its own, 4 of which entered the lifecycle, 0 disputed or withdrawn, 0 false positives** —
including two `SKILL.md` self-contradictions the external channel never found. Every internal
finding cited `file:line` evidence and every run re-ran the validators itself rather than
trusting the Worker's numbers.

So the two channels are **complementary, not redundant**, and the internal gate's failure is
narrow and specific rather than general:

> **All 5/5 missed defects are the same archetype — negative-space defects.** A fallback branch,
> a losing precedence path, the equality case of an inequality claim, the *value* inside a
> structure verified as *present*, and the *un*-covered scope of a validation. In every case the
> Final Reviewer's retained artifact records one confirming instance and no search for a
> counterexample. In every case the evidence was present, in scope, and inside a file the
> reviewer's own artifact listed as inspected.

A second thing is demonstrated alongside the archetype, and it is a fact about the contract text
rather than about any reviewer: **§17 states no falsification or search-depth obligation.** Its
nine search axes (A–I) are topic labels — *objective alignment*, *contract vs implementation*, … —
read as questions of the form *is X consistent / aligned / matching?*, which a single confirming
instance satisfies. None asks for a counterexample, an enumeration of call sites, a negative
path, or an equality case, and nothing elsewhere in §17 tells a reviewer when a search may stop.
The one mandatory global gate in the skill is also the only reviewer role with **no policy
artifact** (`orca-worker-reviewer-orchestration/reviews/` holds seven phase files and no
`final_review.md`).

**The link between those two demonstrated facts is a hypothesis, not a finding.** That the
missing obligation is *what caused* each reviewer to stop after one confirming instance is
**H-4** — the best-supported explanation in this report (capability, visibility and scope are
each ruled out below, and it names an editable contract surface), but an inference rather than an
established cause: no Final Review input spec or search procedure is retained for any run, and
model / per-attempt search variance is not excluded. "The contract permitted stopping" is
demonstrated; "the contract caused stopping" is not. Separating the two requires the controlled
seeded-fixture comparison this report proposes rather than performs — which is why the P1
intervention H-4 motivates is scoped as an experiment whose recall effect must be **measured**,
not as a proven fix.

A separate demonstrated defect compounds this: the gate's verdict was **not reproducible and its
accepted verdict is not auditable**. On PR #18, three dispatches of the same gate against the
same head produced three different outcomes; the accepted PASS ran on a spec shrunk ~6x to dodge
an undocumented `agent_prompt_blocked` limit, and **neither its input spec nor any report file
was retained**. That is why *every* causal question in this ticket — H-4 (the affirmative-only
search contract), H-1 (anchoring) and H-2 (first-PASS stopping rule) — is structurally
unanswerable on the current record, and will remain so for the next OS-21 unless the audit
contract is fixed first.

**Consequence for the product claim.** A run reaching `STATUS: COMPLETED` today asserts that a
global adversarial gate found no blocking defect (`FINAL_REVIEW: PASS` / `FINAL_FINDINGS: none`).
On this corpus that claim carried 0% recall against an external reviewer reading the same head.
`COMPLETED` is not currently evidence of merge-readiness. Exposure is highest at **LOW risk**,
where §17 is the *only* verification gate in the entire run — and where the corpus contains
**zero** observations.

**Recommendation:** 8 follow-up tickets, one per root cause, in 3 waves. Two P1 tickets close
demonstrated contract gaps: the missing falsification search contract (whose recall effect is
H-4 and must be measured) and the missing input/verdict audit contract (a demonstrated defect
needing no causal premise). Two further hypothesis-validation tickets are explicitly gated behind
an evaluation fixture and must not ship lifecycle changes before their experiment reports. No
blocking-criteria expansion anywhere.

## 2. Validation Corpus

**Repository:** `luminous419/orca-skills`. **Population:** PRs #10–#18 (all 9 PRs in the
repository's Final-Review era and its immediate predecessor).

**External reference channel.** Every external review is a GitHub PR review authored by
`luminous419` carrying an external (ChatGPT/human) review, retrieved with
`gh api pulls/<n>/reviews` — a channel distinct from the internal `FINAL_REVIEW*.md` artifacts.
Across **23 external review rounds: 16 MAJOR, 0 CRITICAL.** 8 of 9 PRs required at least one
external MAJOR fix; only PR #17 was clean on its first external round.

**Internal channel.** `artifacts/runs/<run_id>/FINAL_REVIEW*.md`,
`artifacts/FINAL_REVIEW_*.md`, `artifacts/runs/<run_id>/ORCHESTRATOR_LOG.md`,
`artifacts/runs/<run_id>/FINAL_RESULT.md`, plus `git show` of the exact head each reviewer read.

| PR | Ticket | Internal FR record | Head reviewed | In measurable subset? | Why |
|---|---|---|---|---|---|
| #10 | — | none | — | No | predates the §17 gate entirely |
| #11 | Final Adversarial Review | none for the PR-creation run (`artifacts/ORCHESTRATOR_LOG_run_1955ca9863a7.md` has zero final-review rows) | — | No | the PR that *introduced* §17 shipped without running it; external found 2 MAJOR, and external MINOR-1 states this explicitly |
| #12 | orchestration efficiency | `artifacts/ORCHESTRATION_EFFICIENCY_ORCHESTRATOR_LOG.md` — attempt 1 FAIL, attempt 2 PASS | `dfe5eed` | **Yes** | |
| #13 | — | unrecoverable | — | No | artifact lost (`artifacts/` untracked) |
| #14 | OS-1 quality profiles | `artifacts/runs/run_bf55f06dd7fc/FINAL_REVIEW.md`, PASS @07:25Z | `c6a5503` | **Yes** | |
| #15 | — | unrecoverable | — | No | artifact lost; needed 5 external rounds / 6 MAJOR |
| #16 | OS-3 risk workflows | `artifacts/runs/run_e0cdf1afae58/FINAL_REVIEW{,_iteration2,_iteration3}.md` — FAIL, FAIL, PASS @04:37Z | `27690cc` | **Yes** | |
| #17 | OS-19 timing log | `artifacts/runs/run_ec18ea04bc22/ORCHESTRATOR_LOG.md` — attempt 1 FAIL, attempt 2 PASS @09:17Z | `087c836` | **Yes** | |
| #18 | OS-4 agent profiles | `artifacts/runs/run_c854db299e7a/ORCHESTRATOR_LOG.md` — 3 dispatches, PASS @14:32Z | `0287271` | **Yes** | |

**Measurable subset: n = 5** (#12, #14, #16, #17, #18) — runs with both a recoverable internal
verdict and an external review of the same head.

**Corpus limits, stated up front.**
- The external channel is **a single reviewer channel, not an independent panel**. It is a
  reference standard, not ground truth.
- Defects **neither** channel found are invisible here. Every recall figure below is therefore
  an **upper bound** on true recall / lower bound on true miss count.
- #13 and #15 are excluded rather than assumed. Including them could only *lower* recall, since
  both drew external MAJORs (1 and 6).
- Every recoverable run is `risk=high`. **No LOW or MEDIUM Final Review exists in the corpus.**
- `artifacts/` is **untracked** (`git status` → `?? artifacts/`; `.gitignore` excludes only
  `artifacts/orca-runtime/`, `artifacts/orca-agent-smoke/`, `artifacts/**/.timing_state.json`).
  The entire evidence base for this ticket is unversioned local state.

## 3. Internal vs External Finding Comparison

### 3.1 Head-to-head on the measurable subset

| PR | Internal verdict on the reviewed head | External blocking on that head | FR caught it? | Internal blocking findings the external channel did *not* raise |
|---|---|---|---|---|
| #12 | **PASS** (`dfe5eed`) | 1 MAJOR | **No** — caught an adjacent structural defect instead (see M1) | FINAL-I1-MAJOR-1 (attempt 1) |
| #14 | **PASS** (`c6a5503`) | 1 MAJOR | **No** | — |
| #16 | **PASS** (`27690cc`, attempt 3) | 1 MAJOR | **No** | R1 attempt 1, R1 attempt 2 (two §12/§17 lifecycle contradictions) |
| #17 | **PASS** (`087c836`, attempt 2) | 0 | n/a — externally clean | R1 attempt 1 (NaN duration in TIMING_LOG) |
| #18 | **PASS** (`0287271`) | 2 MAJOR | **No** (both) | — (accepted attempt left no report; two voided attempts' findings excluded, §4.3) |

### 3.2 Directional counts

```text
INTERNAL -> EXTERNAL (recall)
  external blocking findings on internally-PASSed heads        5
  independently found by the Final Reviewer                    0     recall = 0%

EXTERNAL -> INTERNAL (complementarity)
  internal blocking findings issued (recoverable corpus)       6
  of which entered the lifecycle                               4
  of those 4, also raised by the external channel              0     -> 4 findings unique to internal
  internal blocking findings later disputed or withdrawn       0     false-positive rate = 0%
  internal non-blocking findings issued                        3     (all in PR #12, all accurate)

DISCIPLINE
  findings citing concrete file:line / diff-range evidence     9 / 9  = 100%
  runs where the FR independently re-ran validation            5 / 5
       (validate_skills.py, unittest discover, verify_package.py, git diff --check)
```

The two channels overlap on **zero** findings in either direction. The internal gate is not a
weaker copy of the external one; it finds a **different class** of defect (lifecycle/contract
self-contradiction inside `SKILL.md`) and misses the class the external reviewer specialises in
(negative-space code paths). This is the single most decision-relevant fact in the report and it
is what separates verdict **B** from verdict **C**.

## 4. Miss Analysis

### 4.1 The five misses

Every row was verified present at the internally-PASSed head with `git show`.

| # | PR | Missed external MAJOR | What the Final Reviewer verified instead | Verified at (reproducible) |
|---|---|---|---|---|
| **M1** | #12 | `dispatch_context()` sets `current_phase=mode`, so the delivered Task boundary says `current_phase: complete` / `pass` instead of the real phase — the advertised safety boundary carried a wrong *value* | attempt 2 confirmed the boundary is "rendered before dispatch and present in the actual `--spec`" — **presence, not value** | `git show dfe5eed:scripts/orca_runtime_harness.py` :388,401 → `current_phase=mode`; fixed only at `33bff91:435` |
| **M2** | #14 | `run_attempt()` never carries the run's requested phase set, so Final Review silently falls back to `ALL_APPLICABLE_PHASES` | FR confirmed `build_quality_gate_context()` *supports* `requested_phases` and that rendered specs carry the profile block — **the supporting function, not the omitting call site** | `git show c6a55038:scripts/orca_runtime_harness.py` :531-532 → `scope = requested_phases or (ALL_APPLICABLE_PHASES if phase == FINAL_REVIEW_PHASE else ())` |
| **M3** | #16 | The documented churn invariant (strict `MEDIUM < HIGH` whenever any correction occurs) is **false**: phase-local correction, and a Final Review correction on the last requested phase, both yield `MEDIUM == HIGH` | FR attempt 3 checklist B read that exact claim and **blessed it**: "The documented clean-first-pass and all-specialized `MEDIUM == HIGH` exceptions are preserved while `LOW < MEDIUM` remains strict" — **the stated exceptions, never the unstated equality case** | `git show 12c60a4^:orca-worker-reviewer-orchestration/SKILL.md` :718-719 — the false text was live at the PASSed head |
| **M4** | #18 | `discover_agent_profiles()` parses *both* sources eagerly, so a malformed `~/.orca/agent-profiles.yaml` rejects a valid project-local profile despite documented whole-definition precedence | Retained FR reports confirm the **precedence order** as implemented plus its passing tests — **the affirmative winner path**; neither examines what happens when the *losing* source fails to parse | `git show 0287271:scripts/agent_profile.py` — loop iterates all candidates before returning |
| **M5** | #18 | Static command safety applied only to `required_entries()`, while `evidence_rows()` writes every routing entry's raw command verbatim into run-scoped audit logs | Retained FR reports **affirm the narrow scope as correct** ("required commands still pass token, allowlist, and PATH gates") — a confirmation of the required path, never a check of the non-required one | `git show 0287271:scripts/agent_profile.py` — `evidence_rows()` writes every entry's raw command; only `required_entries()` is gated |

### 4.2 The archetype, stated precisely

All five are **negative-space defects**: the defect lives in the branch, path, case, or value the
affirmative check does not reach.

```text
M1  a verified-present structure carrying a wrong VALUE
M2  a supporting function verified, the OMITTING CALL SITE not enumerated
M3  the stated exceptions verified, the unstated EQUALITY CASE not tested
M4  the winning precedence path verified, the LOSING PATH's failure mode not probed
M5  the in-scope path verified, the OUT-OF-SCOPE path not checked
```

The archetype has a directly editable candidate explanation **in the search contract itself**.
§17's nine axes
(`SKILL.md:1698-1890`) read:

```text
A objective alignment   B cross-phase consistency   C contract vs implementation
D implementation vs tests   E docs vs behavior   F lifecycle state machine
G security destructive   H over-engineering   I hidden coupling
```

Every one is a topic label, read as a question of the form *is X consistent / aligned /
matching?* — **satisfied by exhibiting one confirming instance.** None asks for a counterexample,
an enumeration of call sites, a negative path, or an equality case, and §17 states no explicit
falsification or search-depth obligation anywhere else either. That is a **DEMONSTRATED** fact
about the contract text, verifiable by reading `SKILL.md:1698-1890`. Correspondingly, the FR
artifacts' own `## Evidence Checked` sections read as lists of confirmations ("Confirmed the
rendered Task model orders decisions as…", "Verified `round_kind` is constrained to…") and
**never** as a record of a failed search for a violation.

**Consistent-with, not evidence of a cause.** That last observation describes what the reports
*recorded*, not what the reviewers *did*: no contract requires recording a search that found
nothing, so a confirmation-only report is equally consistent with a reviewer who did hunt for
counterexamples and found none in the places looked. The archetype and the contract gap are both
demonstrated; that the gap *produced* the archetype is **H-4** (§5, RC-1), and is not asserted
here.

**Two control checks rule out the easy explanations:**

```text
Was the evidence available at the PASSed head?   5 / 5  YES
Was the defect in scope under axes A/C/D/E?      5 / 5  YES
```

M2 and M4 sit in files the FR artifact explicitly lists as inspected. M3 sits in the same
`SKILL.md` section the FR quoted. M1 is in the function the FR named. M5 is in the code path the
FR blessed. **No miss is attributable to missing evidence, artifact invisibility, out-of-scope
findings, or reviewer capability.** What the misses *are* attributable to is the open question:
the leading candidate is the contract gap above (H-4), which the record supports but does not
establish.

### 4.3 The cross-phase miss (M5) and what is *not* known about it

M5's requirement chain is fully recorded, and it is the most instructive miss in the corpus:

1. `artifacts/runs/run_c854db299e7a/REVIEW_DESIGN_iteration2.md`, finding **D-002-R1**
   (G1/MAJOR/blocking) *required* that token/allowlist validation be narrowed to materialized
   required routing only, and *required* a negative test proving an out-of-request `bash` value
   does not block the run.
2. The Worker complied (log row `design worker 4 … resolved D-002-R2`); the accepted
   implementation encodes exactly that scope (`git show 0287271:scripts/agent_profile.py`).
3. Both retained Final Review reports **affirm** the narrow scope rather than question it —
   `artifacts/FINAL_REVIEW_agent_profile_separation.md:42`,
   `artifacts/runs/run_c854db299e7a/FINAL_REVIEW.md:40`.
4. The run completed on PASS with `FINAL_FINDINGS: none`
   (`artifacts/runs/run_c854db299e7a/FINAL_RESULT.md:159`).
5. The external reviewer raised exactly this MAJOR and named the regression test the design
   reviewer had ordered.

So **a defect originating in a prior phase Reviewer's own accepted blocking finding passed
through that Reviewer, through the Final Review, and out to the external channel.** The
distinction the internal chain never drew — static token/allowlist safety belongs to the whole
selected profile definition; PATH availability belongs to required roles only — was first drawn
externally.

**Provenance caveat (binding).** The accepted PASS attempt for PR #18 left **no report file**.
The "what the FR verified instead" column for M4/M5 therefore quotes the two *retained* FR
reports from that run, whose dispatches failed at `dispatch_input` and whose verdicts were
**correctly voided on provenance grounds**. They are used only as retained *reasoning traces of
Final Reviewer instances reviewing the same head* (the archetype claim), **never as accepted
verdicts**. The miss itself — M4/M5 present at the PASSed head, absent from `FINAL_FINDINGS` — is
established independently by `git show` plus `FINAL_RESULT.md`. M1–M3 quote their runs' accepted
FR artifacts directly.

**What is NOT known, and must not be asserted.** Nothing in the retained record shows *why* the
Final Reviewer affirmed the narrow scope instead of re-deriving it:
- No retained FR artifact cites D-002-R1 or any prior phase verdict as a reason. Negative check
  performed: `grep -c "D-002"` over both retained FR reports → `0`, `0`. `D-002` appears only in
  `REVIEW_DESIGN_iteration2.md` and `ORCHESTRATOR_LOG.md`.
- No Final Review **input spec** is retained for any run in the corpus (§9 has no artifact
  contract for it).

**Inheriting an upstream scope decision and independently making the same narrow reading are
observationally identical in this record.** That is exactly why anchoring is carried below as
**H-1 (hypothesis)** and not as a cause.

### 4.4 Verdict instability (PR #18)

`artifacts/runs/run_c854db299e7a/ORCHESTRATOR_LOG.md` records three Final Review dispatches for
one attempt on one head:

```text
14:22:25Z  task_2d0a6f4fc5a4 / ctx_8251971fb59e  term_33295587…  FAILED at dispatch_input
                                                 (agent_prompt_blocked, spec ~14.8KB)
14:24:25Z  task_6b7d7a0cdd95 / ctx_a2ed3c36e1b9  term_113d023d…  FAILED at dispatch_input
                                                 (spec ~5.5KB)
14:32:36Z  task_d3f49c042d5a / ctx_71f59c521292  term_c164994d…  PASS (spec ~2.3KB)
```

Both blocked agents nevertheless completed full review reports (still on disk), each **FAIL**
with a distinct MAJOR/G1 finding — one on `WORKER_REVIEWER_MUST_DIFFER` bypass via a profile
resolving Worker and Reviewer to the same command, one on `E2EHarness.allocate_session()` reuse.

- **DEMONSTRATED — the gate's verdict was not reproducible on this head.** Three dispatches,
  three outcomes.
- **DEMONSTRATED — the accepted PASS is unauditable.** It ran on a spec roughly **one sixth** the
  size of attempt 1's, shrunk purely to work around an undocumented dispatch-layer
  `agent_prompt_blocked` threshold. That spec was not retained, and the attempt produced no
  report — only a verdict row plus the Coordinator's second-hand summary. `FINAL_RESULT.md`
  records `FINAL_FINDINGS: none` and asserts the *opposite* of one voided finding with no
  retained reviewer artifact behind it.
- **What this rules out.** Both failed dispatches had their capabilities revoked and their
  `worker_done` rejected (`dispatch_capability_invalid`). Their reports were correctly voided,
  and the Coordinator was right not to use them. **The lifecycle therefore never held a valid
  prior FAIL that the PASS overrode.** `T1 attempt PASS → COMPLETED` did not terminate a sequence
  of *accepted* contradictory verdicts. Whether the two voided findings are true defects was
  never adjudicated and is **not** counted as two further misses.

## 5. Root Cause Classification

Eight root causes, each with its evidence tier. **RC-2 through RC-5 are DEMONSTRATED and carry
the verdict. RC-1's contract gap is DEMONSTRATED, but its causal link to the five misses is
HYPOTHESIS H-4** — the best-supported explanation in this report and the target of its
highest-priority intervention, whose effect must therefore be measured rather than assumed.
**RC-6 and RC-7 are likewise demonstrated contract facts whose causal links (H-1, H-2) are
hypotheses; they gate their own lifecycle proposals behind validation. RC-8 is a demonstrated
*coverage* gap, not a defect.**

| ID | Root cause | Tier | Evidence | Explains |
|---|---|---|---|---|
| **RC-1** | **No falsification obligation in the search contract.** All nine A–I axes are topic labels, read as *is X consistent?* — satisfiable by one confirming instance. §17 states no falsification, enumeration, negative-path, or equality-case obligation, there or anywhere else. The one mandatory global gate is also the only reviewer role with **no policy artifact** — `orca-worker-reviewer-orchestration/reviews/` contains `analysis, plan, design, implementation, test, bugfix, refactoring, common` and **no `final_review.md`**; §17's entire normative content is the A–I block plus one paragraph. | **Contract gap: DEMONSTRATED. Causal link to the misses: HYPOTHESIS H-4 (best-supported)** | **DEMONSTRATED:** F3 — 5/5 misses negative-space, each verified present at the PASSed head by `git show`; evidence-availability 5/5, in-scope 5/5; A–I are topic labels stating no falsification/search-depth obligation (`SKILL.md:1698-1890`); `ls orca-worker-reviewer-orchestration/reviews/` shows no `final_review.md`. **NOT DEMONSTRATED:** that this omission is what made the reviewers stop — no input spec or search procedure is retained for any run, and model / per-attempt search variance is not excluded | Candidate mechanism for **M1, M2, M3, M4, M5** (H-4). **Not asserted as their proven cause.** |
| **RC-2** | **The gate is unauditable and its verdict unreproducible.** §9 defines no artifact contract for the Final Review *input spec* nor for each *attempt's* report. The dispatch-layer `agent_prompt_blocked` size limit is undocumented and was handled by silently shrinking the spec ~6x. | **DEMONSTRATED** | F5 / §4.4: PR #18's three verdicts on one head; accepted PASS's input **and** report both unretained; `FINAL_RESULT.md:159` | Why the PASS on `0287271` cannot be root-caused; why H-4, H-1 and H-2 are all structurally unanswerable on this record |
| **RC-3** | **`Responsible Phase` ladder has no rung for "a prior phase Reviewer's accepted blocking finding was itself wrong."** The ladder (`SKILL.md:1790-1802`) routes by rung 1 (artifact path `<ARTIFACT_ROOT><PHASE>.md`) then by defect *nature*. A review artifact (`REVIEW_DESIGN_iteration2.md`) is **not** in the rung-1 path contract, and M5's surface nature ("production code 동작 결함 / 계약 위반") routes to **IMPLEMENTATION** — not to the DESIGN review finding D-002-R1 that actually authored the defect. | **DEMONSTRATED (contract-text gap)** | F4 DEMONSTRATED 3; ladder text at `SKILL.md:1790-1802`; requirement chain in §4.3 | Correct routing of M5-class cross-phase defects |
| **RC-4** | **No revalidation after external correction.** §17 mandates a fresh Final Review after an *internal* correction, but nothing re-runs it after a fix driven by an *external* review. | **DEMONSTRATED (policy gap); effect is correlational** | F6: re-ran in #11, #12 only; not in #14, #15, #16, #18. #16 `run_end COMPLETED` 04:41Z vs fix `12c60a4` 05:55Z; #18 `run_end` 14:32Z vs PR created 15:18Z | The two PRs that *did* re-run are the only two whose external re-review then found nothing; the four that did not needed 1, 5, 1, 3 further rounds |
| **RC-5** | **Measurement infrastructure is fragile and there is no controlled fixture.** `artifacts/` is untracked; #13's and #15's FR artifacts are **already lost**. No seeded-defect fixture exists, so no controlled internal-vs-external comparison can be run. | **DEMONSTRATED** | `git status` → `?? artifacts/`; `.gitignore` excludes only 3 subpaths; corpus limits in §2 | Why every figure here is an upper bound; why RC-6/RC-7 cannot be tested today |
| **RC-6** | **Reviewer-context asymmetry.** §11's anti-anchoring machinery — rule 1 ("delta는 시작점이지 경계가 아니다"), rule 3 ("`approved_baseline`은 immutable truth가 아니다"), `REVIEWER_DRILL_DOWN = mandatory_and_unrestricted` — is withheld from the one mandatory global gate by rule 6 and `REVIEWER_CONTEXT_EXCLUDES = final_adversarial_review` (`SKILL.md:1300,1310`). What §17 retains is a *prohibition* ("앞선 phase gate가 PASS였다는 사실을 옳다고 가정하지 않는다"), not a positive obligation to re-derive. | **Asymmetry: DEMONSTRATED (contract-hygiene). Causal link to any miss: HYPOTHESIS H-1** | `sed -n '1296,1310p' SKILL.md` confirms rule 6 + the `EXCLUDES` key. But: no FR artifact cites D-002-R1 (`grep -c` → 0,0), no input spec retained, and independent same-mistake is observationally identical (§4.3) | Possibly M5. **Not asserted.** |
| **RC-7** | **The stopping rule is the first PASS.** `T1 attempt PASS → STATUS: COMPLETED` requires no PASS to be reproducible, to have inspected at least as much as another attempt, or to explain a disagreement with one. | **Contract fact: DEMONSTRATED. Causal link to any miss: HYPOTHESIS H-2** | `SKILL.md` T1; PR #18 completed on a demonstrably non-reproducible PASS whose input was smaller and unrecorded. **But** no valid FAIL was ever *accepted* (§4.4), so this corpus contains **no instance of the rule suppressing an accepted contradiction** | Possibly the PR #18 outcome. **Not asserted.** |
| **RC-8** | **Zero LOW/MEDIUM evidence.** §17 claims risk-independence; every recoverable run is `risk=high`. At LOW there is no phase Reviewer at all, so §17 is the **sole** verification gate — the gate's weakest-evidenced configuration is its most load-bearing one. | **DEMONSTRATED (coverage gap)** | F7 last row; `risk=high` log rows for #16, #17, #18 | Nothing yet; makes the risk-independence claim currently unfalsifiable from field data |

### 5.1 Causes explicitly ruled out

| Candidate (from the ticket's list) | Assessment | Evidence |
|---|---|---|
| Model capability / agent assignment | **Not supported as primary** | The same reviewer channel produced 4 real, well-evidenced MAJORs in the same runs, including two `SKILL.md` contradictions the external reviewer never found. Capability is present |
| Artifact visibility | **Not supported** | 5/5 misses had the evidence available at the PASSed head |
| Task scope | **Not supported** | 5/5 misses were in scope under §17's own axes A/C/D/E |
| Repo/diff inspection **breadth** | **Not supported** | Diffs, file lists, and validators are all re-run independently, 5/5 runs |
| Repo/diff inspection **depth** | **Demonstrated as the failing dimension → RC-1 (mechanism = H-4)** | All 5 misses lie inside files the FR listed as inspected; *why* each search stopped there is not in the record |
| Test-evidence inspection | **Supported → RC-1 (mechanism = H-4)** | In M5 the *test suite itself encoded the defect*; retained FR reports read "773 tests OK" as evidence of correctness rather than asking which tests bless which behaviour |
| Context / spec budget | **Mechanism unknown → folded into RC-2** | The ~6x shrink and the undocumented limit are demonstrated; because the spec was not retained, whether content loss changed the verdict is undeterminable |
| Prior-decision anchoring | **HYPOTHESIS H-1 → RC-6** | §4.3 |
| Review budget / stopping rule | **HYPOTHESIS H-2 → RC-7** | §4.4 |

### 5.2 One further correlation, recorded but not relied upon

**H-3 (weak, n=2).** PR #16 spent 3 attempts with two real FAILs between them; PR #17 — the only
externally-clean PR — ran 2 attempts with a real FAIL between them. The two PRs where the gate
did more than one substantive attempt are the two with the best external outcomes. No controlled
variation. Suggestive only; it informs the *design space* of OS-28 but justifies nothing on its
own.

## 6. Quantitative / Qualitative Evaluation

### 6.1 Quantitative

```text
RECALL (the ticket's core question)
  measurable subset n                                              5 runs   (#12,#14,#16,#17,#18)
  external blocking findings on internally-PASSed heads            5
  independently found by the Final Review                          0
  external CRITICAL/MAJOR recall                                   0 / 5  =   0%
  runs where internal PASS was followed by >=1 external blocking   4 / 5  =  80%

PRECISION (the gate's counter-evidence)
  internal blocking findings issued                                6
  entered the lifecycle                                            4
  later disputed or withdrawn                                      0
  blocking false-positive rate                                     0 / 4  =   0%
  internal non-blocking findings, all accurate                     3

UNIQUE CONTRIBUTION
  internal blocking findings the external channel never raised     4 / 4  = 100%
  external blocking findings the internal gate also raised         0 / 5  =   0%
  channel overlap                                                  0 findings, both directions

DISCIPLINE
  findings citing concrete file:line / diff-range evidence         9 / 9  = 100%
  runs independently re-running validators                         5 / 5

MISS CHARACTER
  misses of the negative-space archetype                           5 / 5  = 100%
  misses where evidence was available at the PASSed head           5 / 5  = 100%
  misses in scope under §17's own axes                             5 / 5  = 100%

REPRODUCIBILITY / AUDITABILITY
  heads with >1 dispatched verdict                                 1  (#18: 3 dispatches, 3 outcomes)
  accepted PASS attempts with a retained report                    #18: 0
  runs with a retained Final Review INPUT spec                     0 / 5  =   0%

CONTRACT COVERAGE
  PRs in the §17 era that ran the gate at all                      6 / 8  (#11 and #13 did not / unrecoverable)
  fresh FR re-run after an EXTERNAL correction                     2 / 6  =  33%
  Final Reviews observed at risk=LOW or MEDIUM                     0
```

**Population context (not part of recall):** 23 external review rounds across PRs #10–#18
produced **16 MAJOR, 0 CRITICAL**; 8 of 9 PRs required at least one external MAJOR fix.

**Statistical honesty.** n = 5 is small and every figure is an upper bound on true recall (§2).
But the recall result is not a marginal call: it is **0 of 5, with a single shared archetype and
5/5 evidence-availability**. The direction and the mechanism are consistent across every
observation; the *magnitude* is what n = 5 cannot pin down.

### 6.2 Qualitative

**What is working, and must not be broken.**
- **Evidence discipline.** Every finding cites `file:line` and quotes the diff range read. No
  finding in the corpus is unsupported.
- **Independent validation.** 5/5 runs re-ran `validate_skills.py`, `unittest discover`,
  `verify_package.py`, and `git diff --check` themselves rather than trusting Worker-reported
  numbers.
- **Conservatism.** 0% blocking false-positive rate. `reviews/common.md`'s
  `Not Blocking by Default` list and four-tier decision priority are functioning exactly as
  designed.
- **Genuine independence does occur.** PR #11's and PR #12's post-correction Final Reviews state
  it explicitly ("이전 PASS 판정은 근거로 간주하지 않고 최종 파일과 재대조했다"); PR #16 attempt 3
  is logged as a "fresh independent A-I audit".
- **Complementary coverage.** The gate found two `SKILL.md` self-contradictions (#16 R1 attempt 1
  and attempt 2) that the external reviewer never found. Removing or weakening the gate would
  *lose* real defect detection.

**What is not working.**
- The gate **records confirmations, never falsification attempts** — and §17 never asks it to.
  Its `## Evidence Checked` sections are lists of confirmations; not one records a failed search
  for a violation. Whether the reviewers also *searched* only affirmatively is H-4: unrecorded
  either way.
- The gate's **own verdict is not a durable artifact.** The one attempt whose reasoning matters
  most in this entire ticket left nothing behind.
- The gate's **strongest claim is its least verifiable.** "Does not assume the prior PASS was
  correct" is stated in §17, evidenced in two runs, and **unverifiable in the run where it
  matters** (§4.3).
- The gate is **untested where it is load-bearing.** At LOW risk it is the only gate in the run,
  and the corpus has zero LOW observations.

## 7. Final Review Contract Verification

Each §17 claim checked against observed runs. **HOLDS / FAILS / NOT VERIFIABLE / UNKNOWN.**

| # | §17 claim | Result | Evidence |
|---|---|---|---|
| C1 | Fresh terminal per attempt; no reuse of any prior attempt's or phase Reviewer's terminal | **HOLDS** | #16: attempts 1/2/3 on three distinct `created` terminals — independently confirmed by the external reviewer from the supplied log. #18: `term_33295587` / `term_113d023d` / `term_c164994d`, all distinct from phase terminals `term_da6ae9eb` / `term_15620afd` |
| C2 | Does not share a session with the prior phase Reviewer | **HOLDS** | same rows; `action=created` on every final-review row |
| C3 | Does not assume the prior PASS was correct | **STATED; COMPLIANCE NOT VERIFIABLE** | Explicit re-verification evidenced in #11/#12; #16 attempt 3 logged as a fresh independent audit. For #18/M5 the *outcome* is demonstrated (an accepted upstream scope decision was never re-derived and the defect shipped) but **no retained artifact shows whether the reviewer relied on it** (§4.3). The record can neither confirm nor refute compliance → **RC-2, and the reason H-1 stays a hypothesis** |
| C4 | Directly verifies the final repository state | **HOLDS** | every FR artifact re-runs and reports validator / unittest / package / `git diff --check` totals itself |
| C5 | Directly inspects diff / source / test / artifact | **HOLDS in breadth, FAILS in depth** | diffs and `file:line` are cited; **all 5 missed defects lie inside the cited files** → RC-1 |
| C6 | Routes blocking defects to the responsible requested phase | **HOLDS mechanically; ladder incomplete** | #12 → implementation then T5a → TEST; #16 → design; #17 → bugfix; all logged with `Responsible Phase`. Ladder (`SKILL.md:1790-1802`) has no rung for a wrong prior-Reviewer finding → RC-3 |
| C7 | A fresh Final Review re-runs after correction | **HOLDS in-run; FAILS after external correction** | #16 attempt 3, #12 attempt 2 vs. the 4/6 gap in §6.1 → RC-4 |
| C8 | Identical at every risk level (`RISK_INDEPENDENCE`) | **UNKNOWN** | every recoverable run is `risk=high`; **no LOW or MEDIUM Final Review exists in the corpus** → RC-8 |
| C9 | *(unstated)* a PASS verdict is reproducible / its input is recorded | **NOT REQUIRED BY §17; NOT OBSERVED** | §17 requires neither. #18: three dispatches, three verdicts; accepted PASS's input spec **and** report both unretained → RC-2, RC-7 |
| C10 | The gate actually runs on every PR that requests phases | **FAILS once** | PR #11 — the PR that *introduced* §17 — shipped with **zero** final-review rows in `artifacts/ORCHESTRATOR_LOG_run_1955ca9863a7.md`; the external MINOR-1 on that PR says exactly this |

**Score: 4 HOLDS (C1, C2, C4, C6-mechanical) / 2 partial (C5, C7) / 1 not verifiable (C3) /
1 unknown (C8) / 1 unrequired-and-unobserved (C9) / 1 outright failure (C10).**

*Spot-checks performed by this PLAN phase against the repository (internal-consistency only, not
a re-investigation):* `orca-worker-reviewer-orchestration/reviews/` contains 8 files and **no**
`final_review.md`; `SKILL.md:1301-1302` carries rule 6 and `:1310` carries
`REVIEWER_CONTEXT_EXCLUDES = final_adversarial_review`; §17 begins at `SKILL.md:1698` and the
`Responsible Phase` ladder occupies `:1790-1802`; `.gitignore` excludes only
`artifacts/orca-runtime/`, `artifacts/orca-agent-smoke/`, `artifacts/**/.timing_state.json`, so
`artifacts/` is untracked. **All ANALYSIS structural claims re-checked here were confirmed. One
refinement:** the ladder *does* contain a DESIGN rung ("코드는 명세를 따르는데 명세가 틀림 →
DESIGN"), so RC-3 is stated precisely as *the review artifact is absent from rung 1's path
contract and M5's surface nature routes it to IMPLEMENTATION*, rather than as "no DESIGN rung
exists".

## 8. Verdict

> # PARTIALLY EFFECTIVE (B)

**Why not A (EFFECTIVE).** The gate PASSed five heads that an external reviewer, reading the same
commits, found blocking defects in — and found **zero** of them (§6.1). It missed a
requirement-chain defect (M5) that had passed through its own prior phase Reviewer, and it
completed one run on a verdict that was demonstrably **not reproducible** and whose input and
reasoning were **not retained** (§4.4). A `STATUS: COMPLETED` claim cannot today be read as
evidence of merge-readiness.

**Why not C (INEFFECTIVE).** In the same corpus the gate independently produced **4 real blocking
findings, 0 disputed, 0 false positives**, all with concrete `file:line` evidence, and
independently re-ran every validator in 5/5 runs. **All 4 are findings the external channel never
raised**, including two `SKILL.md` lifecycle self-contradictions. Channel overlap is zero in both
directions: the gate is not a weaker copy of the external reviewer, it covers a **different
defect class**. Deleting or weakening it would lose real detections. Six of ten contract claims
verify cleanly.

**Therefore.** The gate is **precise but shallow, and unauditable at its decision point.** Its
failure is a **recall failure in one identifiable defect class** (negative-space paths),
compounded by an **evidence-retention failure** that prevents its own decisions from being
audited (RC-2 — demonstrated, and structural in the contract). The defect class is demonstrated,
and so is the contract gap that is the leading candidate for producing it: §17 imposes no
falsification or search-depth obligation (RC-1). That this gap is *what caused* the misses is
**H-4** and is not carried by the verdict — capability, scope and visibility are ruled out by the
control checks whatever the true mechanism turns out to be.

**Evidence carrying the verdict — DEMONSTRATED tier only:**

```text
0 / 5   external blocking recall                                      §6.1, F1
5 / 5   misses share one archetype (negative-space)                   §4.2, F3
5 / 5   misses had evidence available and in scope at the PASSed head §4.2, F3
0       falsification / search-depth obligations stated in §17 A–I    §4.2, F3
0 / 4   internal blocking false positives                             §6.1, F2
4 / 4   internal blocking findings unique to the internal channel     §6.1, F2
9 / 9   internal findings citing concrete evidence                    §6.1, F2
3       verdicts from 3 dispatches on one head (#18)                  §4.4, F5
0 / 5   runs retaining the Final Review input spec                    §4.4, F5, RC-2
0       Final Reviews observed at LOW or MEDIUM risk                  §7 C8, RC-8
```

**Explicitly excluded from the verdict basis:** H-4 (the missing falsification obligation as the
*cause* of the misses), H-1 (anchoring) and H-2 (first-PASS stopping rule). All three are
consistent with the evidence; none is established. The verdict does not change if any of them
turns out false — it rests on the measured recall, the archetype, the contract text, and the
control checks, all of which hold whatever mechanism produced the misses.

**Improvement Decision Rule applies.** Verdict B ⇒ **no large improvement implementation in
OS-21.** What follows is proposals and a ticket split only.

## 9. Recommended Improvements

Grouped by root cause. Each carries **expected impact / regression risk / implementation scope /
priority**. Ordered so that **items closing a demonstrated gap come first**; every item whose
*causal* premise is a hypothesis carries that tier explicitly. I-1 is ranked first because the gap
it closes is demonstrated and is the most directly editable surface in the contract — but its
causal premise is **H-4** and its effect must be **measured, not assumed** (its expected impact is
stated below as a hypothesis to be tested). I-6 and I-7, whose hypotheses would justify a
*lifecycle* change, are additionally marked *validate before changing the lifecycle*.

**A constraint binding on all of them:** every proposal adds **search depth or auditability**.
**None** adds a new blocking criterion, and none may weaken `reviews/common.md`'s
`Not Blocking by Default` list or its four-tier decision priority (measured 0% false-positive
rate, §6.1). Any proposal that would raise the false-positive rate is out of bounds by
construction.

---

### I-1 — Give the Final Review a falsification obligation  *(RC-1 · P1 · gap DEMONSTRATED, causal premise = H-4, MEASURE THE EFFECT)*

**Proposal.** Convert §17's affirmative A–I axes into a search contract that requires the
reviewer to *attempt to falsify* each affirmative claim, and give the gate the policy artifact it
is the only reviewer role to lack. Two candidate landing sites, to be decided in the follow-up
ticket's own design step:
 (a) a new `orca-worker-reviewer-orchestration/reviews/final_review.md`, which also closes the
     structural asymmetry that seven phase reviewers have a policy file and the one mandatory
     global gate does not; or
 (b) §17 text only.
Recommendation: **(a)** — it is where the other seven contracts already live, and it keeps §17's
lifecycle/state-machine text separable from its search policy.

**Content direction (derived from the five misses, not invented):** for each axis, require the
reviewer to name, per claim it affirms, (i) the branch/case/path **not** exercised by its
confirming instance, (ii) the **enumeration** of call sites for any claim about a function's
behaviour, (iii) the **equality/boundary case** of any inequality or precedence claim, and (iv)
the **losing/failing** side of any precedence or fallback structure. Require `## Evidence Checked`
to record **searches that found nothing**, not only confirmations.

**Expected impact — a hypothesis to be measured, not a projected fix.** The obligations above are
derived so that each observed miss maps to one of (i)–(iv): M1→(i) value-vs-presence, M2→(ii)
call-site enumeration, M3→(iii) equality case, M4→(iv) losing path, M5→(i)/(iv) out-of-scope path.
That mapping is demonstrated — it is a property of the five defects and of the proposed text. What
is **not** demonstrated is that the obligation would have changed any of the five verdicts: that
step is **H-4**, and the retained record contains no reviewer input spec or search procedure that
could confirm it, nor excludes per-attempt search variance. This proposal has the broadest
evidential support in the backlog and closes a contract gap that is real whether or not H-4 holds
— but its recall effect is a **claim to be tested**, not an established one.

**Mandatory pairing (binding on the follow-up ticket).** The change must be paired with the
controlled measurement that would settle H-4 and size its real effect: the seeded-defect fixture
of **I-5** reviewed **with and without** the falsification obligation, scored against a known
answer key, with the blocking false-positive rate reported alongside recall. The ticket's success
criterion is that measurement, not the presence of the new text, and its description must state
H-4 as a hypothesis rather than present the change as addressing a proven cause.

**Regression risk — MEDIUM, and the highest-risk item in this backlog.**
 - *Noise.* Requiring more searching could tempt reviewers into promoting speculative concerns to
   blocking, destroying the measured 0% false-positive rate. **Mitigation (mandatory in the
   ticket): the falsification obligation changes what must be SEARCHED and RECORDED, never what
   is BLOCKING.** The four-tier decision priority and `Not Blocking by Default` are unchanged
   text and must be restated as binding inside the new artifact.
 - *Cost.* Deeper search lengthens each attempt (see R-C).
 - *Prompt size.* A longer contract collides with the `agent_prompt_blocked` limit — which is
   why **I-2 should land first or together** (see Execution Order).

**Implementation scope.** 1 new file (~1 policy artifact) + a §17 reference to it; no lifecycle,
Risk, Quality Profile, or Agent Profile semantics touched; no runtime/script change. Small in
diff, high in review care.

**Priority: P1.**

---

### I-2 — Make the Final Review's input and every attempt's verdict auditable  *(RC-2 · P1 · DEMONSTRATED)*

**Proposal.** Three bounded parts:
 1. Add a §9 artifact-path contract for the Final Review **input spec** (one file per attempt),
    so what a reviewer was actually shown is recoverable.
 2. Add a §9 artifact-path contract for **every attempt's report**, including attempts whose
    dispatch later fails — retained with an explicit provenance status (`accepted` / `voided:
    dispatch_input`), so a voided report stays readable as a reasoning trace without ever
    counting as a verdict. (PR #18 shows both needs at once: two voided reports survived by
    accident, the accepted one left nothing.)
 3. **Record and handle** the dispatch-layer `agent_prompt_blocked` size limit explicitly —
    document the threshold and define what a Coordinator may drop when a spec exceeds it —
    instead of silently shrinking the spec ~6x with no record of what was lost.

**Expected impact.** Does not by itself catch a single defect — and that is the correct
expectation. Its impact is that **the next OS-21 can be answered.** Today, every causal question
in this ticket (H-4, H-1, H-2) is structurally unanswerable, and PR #18's accepted PASS cannot be
root-caused **now or ever**. I-2 is the **precondition for validating H-4, H-1 and H-2 at all** —
retained input specs are exactly what a future run would need to tell "the reviewer searched only
affirmatively" from "the reviewer searched and missed" — and the precondition for measuring
whether I-1 worked.

**Regression risk — LOW.** Additive artifact contracts; no verdict semantics change. Two real
risks: artifact volume growth per run, and (part 3) a documented size limit becoming stale if the
dispatch layer changes it — the ticket should record it as an observed operational limit with its
observation date, not as a guaranteed constant.

**Implementation scope.** §9 artifact-path table additions; Coordinator-side retention of the
rendered spec and of each attempt's report; one documented operational note on the size limit. No
lifecycle-semantics change. Medium diff, low conceptual risk.

**Priority: P1.**

---

### I-3 — Add a `Responsible Phase` ladder rung for "a prior phase Reviewer's accepted finding was itself wrong"  *(RC-3 · P2 · DEMONSTRATED)*

**Proposal.** Extend the ladder at `SKILL.md:1790-1802` so that when a Final Review finding's
origin is traceable to an accepted **blocking finding in a phase Review artifact**
(`REVIEW_<PHASE>*.md`), the `Responsible Phase` resolves to **that review's phase**, not to the
phase whose code exhibits the symptom. Concretely, either extend rung 1's path contract to cover
review artifacts, or add a rung above the nature-based mapping.

**Expected impact.** Narrow but exact. M5 is the demonstrated case: its true owner is D-002-R1, a
DESIGN Reviewer's own accepted blocking finding, while its surface nature ("production code 동작
결함 / 계약 위반") routes it to IMPLEMENTATION. Misrouting means the correction is applied to the
symptom while the wrong requirement survives in the design record. Frequency in the corpus: 1/5
misses — real, not common.

**Regression risk — LOW-MEDIUM.** The ladder is "first match wins", so a new rung changes routing
for defects that previously routed elsewhere; rung 3's demotion rule (`결과가 requested phase
집합에 없으면 …`) and rung 4's `OUT_OF_SCOPE_FINAL_REVIEW_FINDING` escalation must both still
behave, especially for runs that did **not** request DESIGN. The ticket must include a routing
table walk-through for each requested-phase subset. **This is a `Responsible Phase` mapping
change only — T0–T5a verdict semantics are not touched.**

**Implementation scope.** One ladder edit in §17 plus routing examples. Small.

**Priority: P2.**

---

### I-4 — Re-run the Final Review after an external correction  *(RC-4 · P3 · policy gap DEMONSTRATED, effect correlational)*

**Proposal.** Define a policy — likely a documented Coordinator procedure rather than a lifecycle
transition — for re-running a fresh Final Review over the corrected head after fixes driven by an
**external** review, closing the asymmetry with the existing internal-correction revalidation
requirement.

**Expected impact.** Cheap and plausibly useful, **but the evidence is correlational and the
sample is small**: the only two PRs that re-ran (#11, #12) are the only two whose external
re-review then found nothing; the four that did not needed 1, 5, 1, and 3 further rounds. This is
the strongest configuration in the corpus, not a demonstrated mechanism, and the ticket must say
so.

**Regression risk — LOW-MEDIUM.** The real hazard is *scope creep into lifecycle semantics*: a
run has already reached `COMPLETED` when an external review arrives. **The ticket must not
re-open a completed run's state machine.** Prefer a new post-completion revalidation procedure
that produces its own artifact over any change to T0–T5a. Cost: +1 Final Review dispatch per
external correction round.

**Implementation scope.** Documented procedure + artifact naming. Small. **Must be sequenced
after I-1**, or it re-runs the same shallow gate and buys little.

**Priority: P3.**

---

### I-5 — Preserve, version, and make the evaluation repeatable  *(RC-5 · P2, enabling · DEMONSTRATED)*

**Proposal.** Two parts under one root cause (validation is not repeatable):
 1. **Preserve the evidence base.** `artifacts/` is untracked and PRs #13's and #15's Final Review
    artifacts are **already lost**. Decide and implement a retention policy (version the run
    artifacts, or export the effectiveness-relevant subset) so the corpus a future OS-21 depends
    on cannot silently disappear.
 2. **Build a seeded-defect evaluation fixture.** A small fixture repository/run where known
    defects of the negative-space archetype are deliberately planted, so the Final Review can be
    dispatched against a **known** answer key. This is the only way to (a) settle **H-4** and
    measure whether I-1 actually improved recall, (b) run the controlled two-attempt experiments
    that I-6 and I-7 require, and (c) detect defects **both** channels currently miss — which
    today are invisible.

**Expected impact.** Enabling. Converts every figure in §6.1 from an upper bound measured on
found-in-the-field data into something reproducible. Without it, I-1's effect cannot be measured
and I-6/I-7 cannot be executed at all.

**Regression risk — LOW.** Additive. Part 1 touches repository hygiene (`.gitignore` / retention),
not the skill contract; the only real question is artifact volume and whether any run artifact
contains content unsuitable for versioning — the ticket must check before committing anything.
Part 2 touches no production contract at all.

**Implementation scope.** Part 1: small (retention decision + `.gitignore`/export mechanics).
Part 2: medium (fixture + answer key + a repeatable dispatch procedure). **Depends on I-2 part 1**
for the fixture to capture what each attempt was shown.

**Priority: P2 (enabling — must precede I-6 and I-7, and gates I-1's effectiveness claim).**

---

### I-6 — Reconsider the Final Reviewer's exclusion from §11's anti-anchoring machinery  *(RC-6 · P2 · asymmetry DEMONSTRATED, causation = H-1, VALIDATE FIRST)*

**Proposal.** Two clearly separated steps, **in this order**:
 1. **Validate H-1.** Using the I-5 fixture and I-2's retained input specs, run controlled
    attempts that vary whether the Final Reviewer is shown a prior accepted scope decision, and
    measure whether affirmation of that decision changes. This is the only way to separate
    "inherited an upstream decision" from "independently made the same reading" — which are
    **observationally identical** in the current record (§4.3).
 2. **Only then** decide whether `REVIEWER_CONTEXT_EXCLUDES = final_adversarial_review`
    (`SKILL.md:1310`) and rule 6 should continue to withhold rule 1, rule 3, and
    `REVIEWER_DRILL_DOWN = mandatory_and_unrestricted` from the one mandatory global gate.

**Expected impact.** Unknown by construction — that is the point of step 1. The *contract-hygiene*
argument stands on its own regardless of H-1: the gate that is meant to assume nothing is the one
role denied the operational machinery for not assuming, and what it keeps is a prohibition rather
than a positive obligation to re-derive. **The ticket must state H-1 as a hypothesis in its own
description and must not present anchoring as a proven cause of M5.**

**Regression risk — MEDIUM.** Step 2 touches §11's Reviewer context contract, which is
load-bearing for all seven phase reviewers; removing an exclusion changes what the Final Reviewer
is handed and interacts directly with the `agent_prompt_blocked` size limit (R-D). Also overlaps
I-1: if I-1 already imposes a re-derivation obligation, step 2 may become redundant — the ticket
must check for double-counting before proposing any edit.

**Implementation scope.** Step 1: experiment only, no contract change. Step 2: potentially a
one-key contract change, deferred and conditional. **Nothing ships from this ticket before step 1
reports.**

**Priority: P2 — validation first; lifecycle change gated.**

---

### I-7 — Revisit the first-PASS stopping rule  *(RC-7 · P2 · contract fact DEMONSTRATED, causation = H-2, VALIDATE FIRST)*

**Proposal.** Two clearly separated steps, **in this order**:
 1. **Validate H-2** with a controlled test on the I-5 fixture: dispatch two *independent, valid*
    Final Review attempts against a seeded head and measure verdict agreement. The current corpus
    **cannot** support this — PR #18's contradictory attempts were all voided at `dispatch_input`,
    so **no valid FAIL was ever accepted** and no suppression of an accepted contradiction is
    demonstrated (§4.4).
 2. **Only then** evaluate options against cost: a reproducibility requirement, a minimum-evidence
    floor per attempt, or an obligation to reconcile a PASS with a prior attempt's disagreement.

**Expected impact.** Unknown by construction. What *is* demonstrated is the contract fact
(`T1 attempt PASS → COMPLETED` requires no PASS to be reproducible, to match another attempt's
depth, or to explain a disagreement) and the observed instability (3 verdicts on one head, the
accepted one unauditable). H-3 (§5.2) weakly suggests attempt depth correlates with external
outcome and can inform the option space — it justifies nothing on its own.

**Regression risk — HIGH if step 2 is rushed.** This is the item closest to the ticket's
Structural Invariants: any change here touches the Final Review verdict state machine. **T2/T3/T4/
T5a semantics must not be altered**, and the follow-up ticket must be scoped so that only T1's
completion condition is even a candidate. Cost is the binding constraint: requiring two
independent attempts roughly doubles Final Review dispatches per run, on top of I-1's deeper
attempts (R-C). PR #16 already spent 3 attempts and PR #18 spent 5 dispatches.

**Implementation scope.** Step 1: experiment only. Step 2: a §17 verdict-condition change,
conditional and deferred. **Nothing ships before step 1 reports.**

**Priority: P2 — validation first; lifecycle change gated.**

---

### I-8 — Close the LOW/MEDIUM risk evidence gap  *(RC-8 · P3 · DEMONSTRATED coverage gap)*

**Proposal.** Observe and record Final Review behaviour at `risk=LOW` and `risk=MEDIUM` — either
by dispatching the I-5 fixture at all three risk levels, or by instrumenting the next real LOW/
MEDIUM runs — and check whether §17's `RISK_INDEPENDENCE` claim actually holds.

**Expected impact.** Converts an unfalsifiable claim into a measured one. Strategically important
out of proportion to its priority: **at LOW there is no phase Reviewer, so §17 is the only
verification gate in the entire run** — the gate's weakest-evidenced configuration is its most
load-bearing one. Everything in this report is measured on HIGH.

**Regression risk — NONE (measurement only).** No contract change is proposed by this ticket. If
the measurement shows behaviour differs by risk level, that becomes a *new* ticket, not a change
inside this one.

**Implementation scope.** Small once I-5 exists; essentially a dispatch matrix plus a report.
**Depends on I-5.**

**Priority: P3.**

---

### 9.1 Priority summary

| ID | Root cause | Tier | Priority | Depends on | Regression risk |
|---|---|---|---|---|---|
| I-1 | RC-1 affirmative-only search contract | gap DEMONSTRATED / cause **H-4** (measure effect) | **P1** | I-2 (recommended); I-5 for the effect measurement | MEDIUM |
| I-2 | RC-2 unauditable gate / unreproducible verdict | DEMONSTRATED | **P1** | — | LOW |
| I-3 | RC-3 responsible-phase ladder gap | DEMONSTRATED | P2 | — | LOW-MEDIUM |
| I-5 | RC-5 measurement infrastructure | DEMONSTRATED | P2 (enabling) | I-2 part 1 | LOW |
| I-6 | RC-6 reviewer-context asymmetry | **H-1** | P2 (validate first) | I-5, I-2 | MEDIUM |
| I-7 | RC-7 first-PASS stopping rule | **H-2** | P2 (validate first) | I-5, I-2 | HIGH if rushed |
| I-4 | RC-4 no post-external-correction re-run | DEMONSTRATED gap / correlational effect | P3 | I-1 | LOW-MEDIUM |
| I-8 | RC-8 no LOW/MEDIUM coverage | DEMONSTRATED gap | P3 | I-5 | none |

## 10. Proposed Follow-up Jira Tickets

### 10.1 Recommended split: **8 tickets**

**Rationale for the number.** The ticket forbids bundling unrelated root causes. There are 8
distinct root causes (RC-1…RC-8), each with a different owner-question, a different evidence
tier, and a different regression-risk profile — so 8 is the minimum split that does not bundle.

Three specific merges were considered and **rejected**:
- **I-1 + I-2** (both P1, both about the Final Review contract) — rejected: one changes *what the
  reviewer must search*, the other changes *what the system must retain*. They have different
  regression risks (MEDIUM vs LOW) and I-2 must be able to ship even if I-1's design stalls.
- **I-6 + I-7** (both hypothesis-validation) — rejected: different hypotheses, different contract
  surfaces (§11 context contract vs §17 verdict state machine), and very different regression
  risk. Bundling would make one hypothesis's negative result block the other's ticket.
- **I-8 into I-5** (both measurement) — rejected: I-5 asks *can we measure repeatably*, I-8 asks
  *does the gate behave the same at LOW*. I-8 is a finding about `RISK_INDEPENDENCE`, not
  infrastructure.

One merge was **accepted**: I-5's two parts (version the evidence base; build the seeded-defect
fixture) share the single root cause "validation is not repeatable" and are sequenced together.

### 10.2 The tickets

Ticket numbers below are **proposals** (`OS-22`…`OS-29`); actual numbering is assigned at filing.

| Ticket | Title | Root cause | Tier | Priority | Wave | Blocks / Depends |
|---|---|---|---|---|---|---|
| **OS-22** | Final Review falsification search contract (`reviews/final_review.md`) | RC-1 | gap DEMONSTRATED / cause **H-4** | **P1** | 1 | depends on OS-23 (prompt-size headroom); **effect measurement depends on OS-26**; blocks OS-25 |
| **OS-23** | Final Review input & per-attempt verdict audit contract | RC-2 | DEMONSTRATED | **P1** | 1 | blocks OS-26, OS-27, OS-28 |
| **OS-24** | `Responsible Phase` ladder rung for a wrong upstream Reviewer finding | RC-3 | DEMONSTRATED | P2 | 2 | independent |
| **OS-26** | Evaluation fixture + versioned evidence base | RC-5 | DEMONSTRATED | P2 (enabling) | 2 | depends on OS-23; blocks OS-27, OS-28, OS-29 |
| **OS-27** | Validate H-1 (reviewer-context asymmetry), then decide on the §11 exclusion | RC-6 | **H-1** | P2 (gated) | 3 | depends on OS-26, OS-23 |
| **OS-28** | Validate H-2 (first-PASS stopping rule), then evaluate a reproducibility floor | RC-7 | **H-2** | P2 (gated) | 3 | depends on OS-26, OS-23 |
| **OS-25** | Post-external-correction Final Review re-run policy | RC-4 | DEMONSTRATED gap | P3 | 3 | depends on OS-22 |
| **OS-29** | Measure §17 behaviour at LOW and MEDIUM risk | RC-8 | DEMONSTRATED gap | P3 | 3 | depends on OS-26 |

---

**OS-22 — Give the Final Adversarial Review a falsification search contract.**
*Root cause: RC-1 (no falsification / search-depth obligation in §17's affirmative A–I axes; no
`final_review.md` policy artifact). Tier: **contract gap DEMONSTRATED** (§4.1–4.2, F3 — 5/5 misses
are negative-space defects, and A–I are topic labels stating no search-depth obligation). **Causal
link to the misses: HYPOTHESIS H-4**, the best-supported explanation in this report. This ticket
is therefore a P1 **intervention whose effect must be measured**, not the application of a proven
fix.*
Scope: author `orca-worker-reviewer-orchestration/reviews/final_review.md` (recommended landing
site) requiring, per affirmed claim, the unexercised branch/case, the call-site enumeration, the
equality/boundary case, and the losing/failing path; require `## Evidence Checked` to record
searches that found nothing. Add a §17 reference to it.
**Hard constraint:** changes what must be SEARCHED and RECORDED, never what is BLOCKING.
`Not Blocking by Default` and the four-tier decision priority are restated as binding and
unchanged (measured 0% false-positive rate must be preserved).
**Out of scope:** phase lifecycle, Risk, Quality Profile, Agent Profile semantics; T0–T5a.
**Binding:** the ticket description must state H-4 as a hypothesis and must not present the change
as addressing a demonstrated cause.
**Acceptance:** (1) the five archetypes M1–M5 are each traceable to a specific required obligation
in the new artifact — satisfiable at authoring time; (2) the change's recall effect is
**measured** on the OS-26 seeded fixture, reviewed with and without the obligation against a known
answer key, with the blocking false-positive rate reported alongside recall as a guardrail. (2) is
what settles H-4 and is required before the change may be claimed effective.

**OS-23 — Retain and document the Final Review's input and every attempt's verdict.**
*Root cause: RC-2. Evidence: DEMONSTRATED — §4.4, F5; 0/5 runs retained an input spec; PR #18's
accepted PASS left no report.*
Scope: (1) §9 artifact-path contract for the per-attempt Final Review **input spec**; (2) §9
artifact-path contract for **every attempt's report**, including dispatch-failed attempts,
carrying an explicit provenance status (`accepted` / `voided: dispatch_input`) so a voided report
is readable as a reasoning trace and never as a verdict; (3) document the observed
`agent_prompt_blocked` size threshold (with observation date, as an operational limit not a
constant) and define what a Coordinator may drop when a spec exceeds it, replacing silent
shrinking.
**Out of scope:** any verdict-semantics change.
**Acceptance:** a completed run permits reconstructing, for each attempt, what the reviewer was
shown and what it concluded. Explicitly: **a future OS-21 can root-cause a PASS.**

**OS-24 — Add a `Responsible Phase` ladder rung for a wrong upstream Reviewer finding.**
*Root cause: RC-3. Evidence: DEMONSTRATED — §4.3, F4 DEMONSTRATED 3; ladder at
`SKILL.md:1790-1802`; M5's true owner is D-002-R1 in `REVIEW_DESIGN_iteration2.md`.*
Scope: route a Final Review finding whose origin is an accepted blocking finding in a
`REVIEW_<PHASE>*.md` artifact to **that review's phase**, not to the symptom-bearing phase.
Include a routing walk-through per requested-phase subset, preserving rung 3's demotion and rung
4's `OUT_OF_SCOPE_FINAL_REVIEW_FINDING` escalation.
**Out of scope:** T0–T5a verdict semantics — this is a mapping change only.
**Acceptance:** replaying M5 through the revised ladder yields DESIGN, not IMPLEMENTATION, and
every existing corpus routing (#12→implementation/TEST, #16→design, #17→bugfix) is unchanged.

**OS-26 — Build the evaluation fixture and version the evidence base.**
*Root cause: RC-5. Evidence: DEMONSTRATED — `artifacts/` untracked, #13/#15 artifacts already
lost, no fixture exists.*
Scope: (1) decide and implement a retention policy for effectiveness-relevant run artifacts,
after checking that nothing unsuitable for versioning is included; (2) build a seeded-defect
fixture with a known answer key covering the five negative-space archetypes, plus a repeatable
dispatch procedure.
**Acceptance:** the Final Review can be dispatched against a known answer key and a recall number
computed reproducibly; OS-22's effect becomes measurable; OS-27/OS-28's experiments become
executable.
**Note:** this is the ticket the ANALYSIS/PLAN chain identified as the remaining *harness gap*
(see W-2).

**OS-27 — Validate H-1, then decide whether the Final Reviewer should keep §11's anti-anchoring
machinery.**
*Root cause: RC-6. Tier: asymmetry DEMONSTRATED (`SKILL.md:1301-1302,1310`); causation
**HYPOTHESIS H-1**.*
Scope, strictly ordered: **step 1** — a controlled experiment on the OS-26 fixture varying whether
the reviewer is shown a prior accepted scope decision, using OS-23's retained inputs; **step 2 —
conditional and deferred** — decide on `REVIEWER_CONTEXT_EXCLUDES = final_adversarial_review` and
rule 6, and check for redundancy with OS-22's re-derivation obligation before proposing any edit.
**Binding:** the ticket description must state H-1 as a hypothesis. **Anchoring must not be
presented as a proven cause of M5** — inheriting an upstream decision and independently making the
same reading are observationally identical in the current record (§4.3). **No contract change
ships before step 1 reports.**

**OS-28 — Validate H-2, then evaluate a reproducibility floor for the Final Review verdict.**
*Root cause: RC-7. Tier: contract fact DEMONSTRATED (`T1 attempt PASS → COMPLETED`); causation
**HYPOTHESIS H-2**.*
Scope, strictly ordered: **step 1** — dispatch two independent, *valid* attempts against seeded
heads on the OS-26 fixture and measure verdict agreement (the current corpus cannot support this:
PR #18's contradictory attempts were all voided at `dispatch_input`, so no valid FAIL was ever
accepted, §4.4); **step 2 — conditional and deferred** — evaluate a reproducibility requirement,
a minimum-evidence floor, or an obligation to reconcile with a prior attempt's disagreement,
against dispatch cost.
**Binding:** **T2/T3/T4/T5a semantics must not be altered**; only T1's completion condition is
even a candidate. **No lifecycle change ships before step 1 reports.**

**OS-25 — Re-run the Final Review after an external correction.**
*Root cause: RC-4. Evidence: policy gap DEMONSTRATED (F6, §6.1 2/6); effect is correlational on
n=6.*
Scope: a documented post-completion revalidation procedure producing its own artifact.
**Out of scope:** re-opening a completed run's state machine — the run has already reached
`COMPLETED` when the external review arrives. Prefer a new procedure over any T0–T5a change.
**Sequencing:** after OS-22, or it re-runs the same shallow gate.
**Acceptance:** the ticket states plainly that the supporting evidence is correlational.

**OS-29 — Measure §17 behaviour at LOW and MEDIUM risk.**
*Root cause: RC-8. Evidence: DEMONSTRATED coverage gap — 0 LOW/MEDIUM Final Reviews in the
corpus; §7 C8.*
Scope: dispatch the OS-26 fixture at LOW, MEDIUM, and HIGH and report whether
`RISK_INDEPENDENCE` holds. **Measurement only** — if behaviour differs by risk level, that becomes
a new ticket, not a change inside this one.
**Strategic note for the filing:** at LOW there is no phase Reviewer, so §17 is the **only**
verification gate in the run; everything measured in this report was measured at HIGH.

### 10.3 What is deliberately NOT proposed

- **No expansion of blocking criteria** in any ticket. F2's 0% false-positive rate and
  `reviews/common.md`'s `Not Blocking by Default` minimalism are working properties; trading them
  for a speculative recall gain is the single most likely way to make the gate worse (R-A).
- **No change to phase lifecycle, Risk, Quality Profile, or Agent Profile semantics** anywhere in
  this backlog — per OS-21's Structural Invariants, and restated as an out-of-scope line inside
  each ticket that touches §17.
- **No lifecycle change justified by H-1 or H-2** before OS-27/OS-28 step 1 reports.
- **No removal or weakening of the Final Review gate.** It contributes 4 unique real findings the
  external channel never raised (§6.1); the verdict is B precisely because it is *complementary*.

---

# W-1 — Follow-up backlog definition

Delivered in W-0 §10: **8 tickets (OS-22…OS-29), one per root cause, 3 waves**, each with scope,
out-of-scope, evidence tier, priority, dependencies, and acceptance. The rejected-merge rationale
is recorded in §10.1 so the split can be audited rather than re-argued.

# W-2 — Harness / fixture gap: explicit answer

The ticket asks whether any harness/fixture gap remains before the follow-up implementation
tickets could even be executed.

**Answer: YES — one gap. It hard-blocks three of the eight tickets, and blocks the *effect
measurement* (not the authoring) of a fourth, OS-22.**

| Question | Answer |
|---|---|
| Did OS-21's own investigation need a new fixture? | **No.** ANALYSIS used only existing repository, artifact, and GitHub evidence. Nothing in this ticket was blocked. |
| Do the gap-demonstrated tickets need one? | **Not in order to be authored.** OS-22, OS-23, OS-24, OS-25 are contract/policy work executable today. **But OS-22's *effect* cannot be established without it:** its causal premise is H-4, and settling H-4 — and sizing the change's real recall effect — requires the OS-26 fixture. Authoring is unblocked; the effectiveness claim is not. |
| Do the hypothesis tickets need one? | **Yes — hard blocker.** OS-27 (H-1) and OS-28 (H-2) both require controlled attempts against a known answer key. The current corpus **cannot** supply this: no Final Review input spec is retained for any run, and PR #18's contradictory attempts were all voided at `dispatch_input`, so no valid accepted FAIL exists to compare against (§4.3, §4.4). |
| Does the risk-coverage ticket need one? | **Yes.** OS-29 needs a dispatchable fixture to observe LOW/MEDIUM without waiting on real runs. |
| Is the gap scoped as its own work item? | **Yes — OS-26**, plus W-3 below for the one part with an OS-21-local decision. |

**The gap, stated concretely:** (1) no seeded-defect fixture with an answer key; (2) no retained
Final Review input spec (fixed by OS-23, which OS-26 depends on); (3) the evidence base itself is
unversioned and two runs' artifacts are already lost.

**Per OS-21's Structural Invariants**, OS-26 is scoped as *evaluation infrastructure only* — it
touches no Final Review prompt, no §17 lifecycle, and no Risk / Quality Profile / Agent Profile
semantics.

# W-3 — Minimal, explicitly-bounded measurement-infrastructure work item

OS-21 permits "minimal evaluation fixture/harness changes needed to make validation possible"
inside this ticket. Two facts determine what that means here:

1. **No such change was needed for OS-21's own deliverable.** ANALYSIS completed on existing
   evidence. The fixture gap blocks *future* tickets (OS-27/OS-28/OS-29), not this one.
2. **This run's phase set is `analysis, plan` only.** No design, implementation, or test phase
   follows, so this run has no phase in which to write, review, or validate code or repository
   changes. Making a repository change from a PLAN phase would ship an unreviewed, untested change
   under a gate that this very ticket has just measured at 0% external recall.

**Decision: no repository change is made inside OS-21.** The bounded work item is defined here,
left immediately executable, and handed to OS-26.

**W-3 bounded scope (if the Coordinator or user chooses to authorize it separately):**
- **Included:** decide a retention mechanism for effectiveness-relevant run artifacts under
  `artifacts/runs/` (version them, or export the relevant subset), after reviewing their content
  for anything unsuitable for versioning; record the decision.
- **Explicitly excluded:** the seeded-defect fixture (larger; belongs in OS-26 with a review
  phase), any `SKILL.md` edit, any `reviews/*.md` file, any runtime script, and anything touching
  the Final Review prompt or lifecycle.
- **Size:** one retention decision plus `.gitignore`/export mechanics.
- **Why it is the only candidate:** it is the sole part of the gap that is (a) reversible, (b)
  contract-free, and (c) already losing data every day it waits — PRs #13 and #15 are gone.

## Dependencies / Execution Order

**This PLAN phase has no execution dependencies** — the deliverable is the report, and it is
complete. What follows is the recommended order for the *backlog*, not work performed here.

```text
WAVE 1  (P1, demonstrated contract gaps — start here)
  OS-23  input & per-attempt verdict audit contract        ── no dependencies
     │   (also resolves the agent_prompt_blocked handling that OS-22 will collide with)
     ▼
  OS-22  falsification search contract                     ── after/with OS-23

WAVE 2  (P2, demonstrated + enabling)
  OS-24  responsible-phase ladder rung                     ── independent, may run in parallel
  OS-26  fixture + versioned evidence base                 ── after OS-23

WAVE 3  (gated / P3)
  OS-27  validate H-1, then decide §11 exclusion           ── after OS-26, OS-23
  OS-28  validate H-2, then evaluate reproducibility floor ── after OS-26, OS-23
  OS-25  post-external-correction re-run policy            ── after OS-22
  OS-29  LOW/MEDIUM risk measurement                       ── after OS-26
```

**Ordering rationale.**
1. **OS-23 first.** It is the cheapest, lowest-risk ticket; it unblocks three others; and it
   removes the prompt-size hazard OS-22 would otherwise hit. Every day it is deferred, another
   run's reasoning becomes permanently unrecoverable.
2. **OS-22 second.** It closes the demonstrated contract gap that is the leading candidate cause
   (H-4) and carries the largest *hypothesised* recall effect — but that effect is only
   *measurable* once OS-26 exists, so it must not be judged by feel or reported as a proven
   improvement before the fixture measures it.
3. **Waves 2 and 3 are parallelizable** except where the arrows bind.
4. **OS-27 and OS-28 cannot start their step 2 at all** until their step 1 experiments report.

## Validation / Test Plan

**No code is produced by this phase, so there is no executable test to run.** Validation of a
report is evidentiary. The following checks were performed on this artifact:

| # | Check | Method | Result |
|---|---|---|---|
| V1 | All ten ticket-required sections present | §1 Executive Summary, §2 Validation Corpus, §3 Internal vs External Comparison, §4 Miss Analysis, §5 Root Cause Classification, §6 Quantitative/Qualitative Evaluation, §7 Final Review Contract Verification, §8 Verdict, §9 Recommended Improvements, §10 Proposed Follow-up Tickets | **PASS** |
| V2 | Verdict stated explicitly with a classification | §8: **PARTIALLY EFFECTIVE (B)**, with an explicit why-not-A / why-not-C and a DEMONSTRATED-only evidence block | **PASS** |
| V3 | Verdict rests only on DEMONSTRATED evidence | §8's evidence block cites F1–F5/§4/§6 only (measured recall, the archetype, the §17 contract text, the control checks); **H-4**, H-1 and H-2 are all explicitly excluded and the verdict is stated to be invariant to their truth | **PASS** |
| V4 | Hypothesis tier preserved, never upgraded | **H-4** appears only as RC-1's causal tier, I-1's expected impact, and OS-22's premise — labelled HYPOTHESIS everywhere, with the demonstrated part (archetype + contract gap) stated separately and the intervention's effect gated behind measurement; H-1 appears only in RC-6/I-6/OS-27, H-2 only in RC-7/I-7/OS-28, H-3 only in §5.2 — each labelled HYPOTHESIS with a stated validation precondition and a "no change ships first" clause | **PASS** |
| V5 | Reproducible identities throughout | PRs #10–#18; SHAs `dfe5eed`, `c6a5503`/`c6a55038`, `27690cc`, `087c836`, `0287271`, `33bff91`, `12c60a4`, `b3a1ff3b`, `5d70aed`, `80f604c`, `2b8853aa`, `581f1f3d`, `3a548c98`, `5f9e9368`, `1b51634f`, `0bf13fb`; run ids `run_bf55f06dd7fc`, `run_e0cdf1afae58`, `run_ec18ea04bc22`, `run_c854db299e7a`, `run_1955ca9863a7`; task/ctx ids `task_2d0a6f4fc5a4`/`ctx_8251971fb59e`, `task_6b7d7a0cdd95`/`ctx_a2ed3c36e1b9`, `task_d3f49c042d5a`/`ctx_71f59c521292`; findings D-002-R1, FINAL-I1-MAJOR-1, M1–M5, F1–F7, RC-1–RC-8, I-1–I-8 | **PASS** |
| V6 | Improvement Decision Rule honoured (verdict B ⇒ no large implementation) | No file outside `artifacts/runs/run_2c614077e685/PLAN.md` created or modified; verified with `git status` | **PASS** |
| V7 | Structural Invariants honoured | No proposal changes phase lifecycle, Risk, Quality Profile, or Agent Profile semantics *inside OS-21*; every §17-touching proposal is deferred to its own ticket with an explicit out-of-scope line | **PASS** |
| V8 | One root cause per ticket, no bundling | §10.1 records the split rationale and three rejected merges | **PASS** |
| V9 | ANALYSIS internal-consistency spot-checks | `ls reviews/` (8 files, no `final_review.md`); `SKILL.md:1301-1302,1310` (rule 6 + `REVIEWER_CONTEXT_EXCLUDES`); §17 at `:1698`; ladder at `:1790-1802`; `.gitignore` (3 excluded subpaths only) | **PASS**, with one refinement recorded in §7 (the ladder *does* have a DESIGN rung; RC-3 restated precisely) |
| V10 | Every improvement adds search depth or auditability, never blocking criteria | §9 preamble states the constraint; I-1 carries it as a mandatory ticket constraint | **PASS** |

**How the backlog itself gets validated later** (not executable now, recorded so it is not lost):
once **OS-26** exists, re-run this measurement against the fixture and against post-OS-22 real
runs; the effectiveness metric is the same `external blocking recall` figure in §6.1, and the
guardrail metric is the `blocking false-positive rate`, which must remain at 0%. **A recall
improvement bought with a false-positive increase is a failure, not a success.**

## Risks

| ID | Risk | Sev | Mitigation |
|---|---|---|---|
| **R-A** | **Over-correcting into noise.** The instinctive reaction to 0% recall is a longer checklist; `reviews/common.md`'s minimalism and the measured 0% false-positive rate say the opposite. | **HIGH** | Every proposal is constrained to search depth / auditability. OS-22 carries an explicit hard constraint. The false-positive rate is named as a guardrail metric in the Validation Plan. |
| **R-B** | **Non-reproducible verdicts on an unrecorded input.** DEMONSTRATED and already biting: PR #18's accepted PASS cannot be root-caused now or ever. Fixing only the checklist leaves the gate's verdict unreproducible and its reasoning unrecorded. | **HIGH** | OS-23 is P1 and Wave 1. *(Whether the first-PASS rule let the least-resourced attempt settle the gate is H-2 — not established.)* |
| **R-C** | **Cost.** Every direction (falsification pass, second independent attempt, post-external re-run) increases Final Review dispatches per run. #16 already spent 3 attempts, #18 spent 5 dispatches. | MEDIUM | Cost is a first-class evaluation criterion inside OS-22, OS-25, OS-28. OS-28's second-attempt option is gated behind a measured benefit. |
| **R-D** | **The `agent_prompt_blocked` limit is real, undocumented, and unresolved.** Any fix that enriches the Final Review input hits the same wall that shrank PR #18's spec ~6x. | MEDIUM | OS-23 part 3 documents and handles it, sequenced **before** OS-22 enriches the contract. |
| **R-E** | **Evidence loss.** `artifacts/` is untracked; #13's and #15's FR artifacts are already gone. A future OS-21 would start from a smaller corpus than this one. | MEDIUM | W-3 / OS-26 part 1; flagged as the only part of the gap losing data while it waits. |
| **R-F** | **Small n.** n=5 for recall, n=6 for the re-run correlation, n=2 for H-3. Direction and mechanism are consistent; magnitude is not pinned down. | MEDIUM | Every figure is stated as an upper bound (§2, §6.1); OS-26 makes the measurement repeatable. No proposal's justification depends on a precise magnitude. |
| **R-G** | **Hypothesis creep.** H-4, H-1 and H-2 are attractive explanations and may get treated as settled during backlog grooming — **H-4 most of all**, because it is the best-supported and already drives a P1 ticket. Treating it as proven would let OS-22 be reported as a fix for a demonstrated cause and skip its measurement; for H-1/H-2 it would produce an unjustified lifecycle change. | MEDIUM | Tier labels are carried into the ticket descriptions (OS-22, OS-27, OS-28), each with a "must not present as proven cause" instruction. OS-27/OS-28 additionally carry a "step 1 must report first" clause; OS-22 carries a mandatory effect-measurement acceptance criterion against the OS-26 fixture. |
| **R-H** | **Reference-standard limits.** The external channel is one reviewer, not a panel. Defects both channels missed are invisible; recall figures are upper bounds. | LOW-MEDIUM | Stated in §2 and §6.1. OS-26's seeded fixture is the only proposed route to measuring the both-missed class. |

## Completion Criteria

This PLAN phase is complete when **all** of the following hold. Each is met as written.

1. **The OS-21 deliverable exists in full** at `artifacts/runs/run_2c614077e685/PLAN.md`, with all
   ten required sections (V1). ✔
2. **The Verdict is stated explicitly** as EFFECTIVE / PARTIALLY EFFECTIVE / INEFFECTIVE and
   justified from ANALYSIS's demonstrated evidence: **PARTIALLY EFFECTIVE (B)** (§8, V2, V3). ✔
3. **Every miss is root-caused with evidence**, and each root cause carries an explicit evidence
   tier: RC-1…RC-8, with M1–M5 each mapped to RC-1 as their **candidate** mechanism (H-4 — the
   archetype and the contract gap are demonstrated, the causal link is not) and M5 additionally to
   RC-3 (§4, §5). ✔
4. **The DEMONSTRATED / HYPOTHESIS distinction is preserved end to end.** H-4, H-1 and H-2 appear
   only as hypothesis-tier drivers, never as settled causes — H-4 gates OS-22's *effectiveness
   claim* behind measurement without blocking its authoring — and the Verdict is stated to be
   invariant to all three (V4). ✔
5. **Improvements are grouped by root cause**, each with expected impact, regression risk,
   implementation scope, and priority (§9, I-1…I-8). ✔
6. **A follow-up ticket split is recommended with a stated count and rationale**: 8 tickets
   (OS-22…OS-29), one root cause each, with the three rejected merges recorded (§10.1). ✔
7. **No unrelated root causes are bundled** into any single ticket (V8). ✔
8. **The Improvement Decision Rule is honoured**: no large improvement implementation started; no
   repository file outside this artifact created or modified (V6). ✔
9. **Structural Invariants are honoured**: no phase lifecycle, Risk, Quality Profile, or Agent
   Profile semantics change is proposed *inside OS-21*; every §17-touching change is deferred to a
   scoped follow-up ticket (V7). ✔
10. **The harness/fixture-gap question is answered explicitly** — yes, one gap remains; it blocks
    OS-27/OS-28/OS-29 and not the P1 tickets; it is scoped as OS-26 plus the bounded W-3 (W-2,
    W-3). ✔
11. **The report is reproducible** — every claim traceable to a PR number, commit SHA, artifact
    path, run/task/dispatch id, or finding id (V5). ✔
12. **Quality gate satisfied.** `.orca/quality-profile.yaml` is absent (`profile_status: absent`),
    so only Explicit Requirements + the PLAN phase contract + G1–G5 apply. G1: every ticket
    requirement is addressed (criteria 1–11). G2: the deliverable is a report and it is complete
    and internally consistent. G3: no change made, so no regression possible. G4: no data loss,
    security, or irreversible side effect — this phase wrote exactly one new artifact file. G5:
    validation evidence is recorded in the Validation / Test Plan (V1–V10), including the
    spot-checks and the one refinement they produced. ✔
