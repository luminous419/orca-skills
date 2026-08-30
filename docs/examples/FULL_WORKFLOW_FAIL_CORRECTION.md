# Full Workflow Best-Practice Example

This example lets a Skill user exercise the complete
`orca-worker-reviewer-orchestration` lifecycle:

```text
ANALYSIS → PLAN → DESIGN → IMPLEMENTATION → TEST → Final Review
```

It also creates one deliberate, bounded DESIGN defect so the run demonstrates a real
phase-gate transition:

```text
DESIGN attempt 1 → Reviewer FAIL → Worker correction → Reviewer PASS
```

The seeded defect is an orchestration exercise, not a Reviewer-quality benchmark. The
example tells both roles what the exercise is testing so that the lifecycle is
reproducible instead of depending on a model to make an accidental mistake.

## 1. Prerequisites

- Orca and the `orca-worker-reviewer-orchestration` Skill are installed.
- Two distinct allowed agent commands resolve on `PATH`.
- Run the exercise in a disposable repository, not in a working project.
- Allow enough time for five phase gates and the mandatory Final Adversarial Review.

The copy-paste invocation below uses `claude` as Worker and `codex` as Reviewer. Replace
those two command tokens if needed. For example, a company environment may use
`worker=claude-glm reviewer=claude-gemma`.

## 2. Create a disposable repository

```bash
mkdir orca-full-workflow-demo
cd orca-full-workflow-demo
git init
printf '# Orca full-workflow demo\n' > README.md
git add README.md
git commit -m 'Initialize workflow demo'
```

If Git has no user identity configured, configure a local identity before the commit.

## 3. Run this prompt

Paste the complete block into the Orca Coordinator session:

```text
/orca-worker-reviewer-orchestration worker=claude reviewer=codex max-iterations=3 risk=medium phases=analysis,plan,design,implementation,test

Create a tiny, standard-library-only Python retry utility in this repository and take it
through every requested phase in the declared order. Do not skip, merge, reorder, or add
phases.

Functional requirements:

1. Add `retry_demo.py` with:
   - `class TransientError(Exception)`.
   - `run_with_retry(operation, max_attempts)` where `operation` is a zero-argument
     callable.
2. `max_attempts` is valid only when `type(max_attempts) is int` and its value is at
   least 1. Otherwise raise `ValueError` before calling `operation`.
3. Return the first successful result.
4. Retry only `TransientError` and make at most `max_attempts` total calls.
5. Re-raise the last `TransientError` after the attempt budget is exhausted.
6. Propagate every non-`TransientError` immediately without retrying it.
7. Do not sleep, use the network, add dependencies, or use nondeterministic timing.
8. Add meaningful `unittest` coverage in `test_retry_demo.py`, including success,
   transient recovery, exhaustion, non-transient propagation, and invalid values
   `0`, `-1`, `True`, and `1.5`.
9. Run `python3 -m unittest -v` during IMPLEMENTATION and TEST and report the exact
   result.

Training-only seeded review exercise:

1. ANALYSIS and PLAN must accurately preserve all functional requirements above.
2. On DESIGN Worker attempt 1 only, deliberately specify that `max_attempts=0` is valid
   and returns `None` without calling `operation`. Keep every other part of the design
   consistent with the requirements.
3. The DESIGN Reviewer must independently compare that design with the original
   functional requirements. The training instruction is not an acceptance exception:
   report the contradiction as a blocking G1 explicit-requirement violation and return
   `RESULT: FAIL` / `REVIEW_VERDICT: FAIL`.
4. In the DESIGN correction attempt, remove the seeded contradiction, require
   `type(max_attempts) is int and max_attempts >= 1`, and record how the blocking finding
   was resolved. The next Reviewer must verify the corrected artifact rather than merely
   trusting the resolution statement.
5. Do not introduce any other deliberate defect. Additional legitimate findings from a
   Reviewer are handled by the normal bounded correction loop.

Completion requirements:

- Every requested phase reaches its phase gate.
- At least the seeded DESIGN review follows FAIL -> correction -> PASS.
- IMPLEMENTATION and TEST have passing unit-test evidence.
- A fresh Final Adversarial Reviewer verifies the final repository state.
- Report the run id and artifact directory in the final result.
```

Why `risk=medium` is used:

- every requested phase gets a Worker and an independent phase Reviewer;
- correction-loop behavior is exercised;
- the mandatory fresh Final Adversarial Review still runs;
- HIGH-only downstream revalidation is not needed for this small lifecycle exercise.

`max-iterations=3` leaves room for the seeded correction and one additional legitimate
finding while keeping the exercise bounded.

## 4. Expected minimum trace

Additional legitimate FAIL rounds are allowed, but a successful exercise has at least
this sequence:

| Phase | Expected gate sequence |
| --- | --- |
| ANALYSIS | Worker → Reviewer PASS |
| PLAN | Worker → Reviewer PASS |
| DESIGN | Worker attempt 1 → Reviewer FAIL → Worker correction → Reviewer PASS |
| IMPLEMENTATION | Worker + unit tests → Reviewer PASS |
| TEST | Worker + test evidence → Reviewer PASS |
| Final Review | fresh Reviewer PASS |

The DESIGN Worker updates `DESIGN.md` in place. The two review decisions remain separate
evidence:

```text
REVIEW_DESIGN.md
REVIEW_DESIGN_iteration2.md
```

## 5. Verify the run

From the demo repository, locate the reported run directory and execute the verifier from
an `orca-skills` checkout or release archive:

```bash
python3 /path/to/orca-skills/scripts/verify_full_workflow_example.py \
  artifacts/runs/<run-id>
```

The verifier checks:

- all five Worker artifacts exist;
- every phase eventually has a Reviewer PASS;
- DESIGN has a Reviewer FAIL followed by a later PASS;
- Final Adversarial Review ends in PASS;
- review `RESULT` and `REVIEW_VERDICT` values agree;
- `ORCHESTRATOR_LOG.md` records the declared risk, ordered gate sequence, correction
  round, and `COMPLETED` run state;
- `TIMING_LOG.md` exists.

Expected output begins with:

```text
PASS: full workflow example completed
```

Do not treat terminal prose alone as proof. The run-scoped artifacts and append-only
orchestration log are the evidence that the Skill actually followed the lifecycle.

## 6. What this example proves

- explicit phase order is honored;
- phase gates prevent advancement before PASS;
- a blocking finding produces a correction attempt;
- correction evidence is reviewed independently;
- implementation and test safety gates run;
- Final Adversarial Review remains mandatory;
- the lifecycle is visible in durable run-scoped evidence.

It does not prove that one model pair is better than another, that every real project will
finish within three iterations, or that a deliberately seeded finding measures natural
defect-detection recall.
