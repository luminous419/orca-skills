RESULT: PASS
REVIEW_VERDICT: PASS

## Summary

The complete run satisfies G1–G7 without widening the sandbox or weakening answer-key isolation. The preflight still checks git, selects the same executable the launched agent's effective PATH selects, classifies that admitted candidate before launch, and resolves Apple's tool shim to the real developer-tool implementation without executing the shim. The phase reports, current Draft PR #20 body, exact-head Linux CI, repository scope, and retained endorsed limits are mutually consistent.

## Blocking Findings

None.

## Non-Blocking Findings

None.

## Test Review

- Independently ran all 57 focused `ProbeInterpreterShimTests`, `ProbeLaunchWiringTests`, and `PreflightGitPathSelectionTests`; all passed. This included the live Darwin assertions that `/usr/bin/git` and `/usr/bin/python3` are the same shim file, that both resolve away from it, and that the resolved git executes successfully.
- Independently ran the complete `test_review_isolation.py` module: 189 tests passed in 231.745 seconds with no skips or failures in this checkout. The focused suite covers the actual `preflight_probe()` launch command, PATH-selected `--agent-path` and inherited-PATH candidates, fixed-path regression, fail-closed resolution, admission-before-read through `builtins.open`, `io.open`, and `os.open`, default-admits-nothing behavior, exact readable-set wiring, and relative/empty component refusal.
- The raw launch spelling cannot return unnoticed: `test_the_preflight_git_check_execs_the_resolved_git` inspects the command handed to `/bin/sh`, requires the substituted resolved spelling, and rejects both bare `git --version` and `SYSTEM_GIT`. `test_the_preflight_launch_line_checks_the_agent_path_git` separately drives the real resolver and requires the selected agent-PATH candidate on that launch line.
- PATH fidelity cannot silently revert to fixed `/usr/bin/git`: the suite asserts admitted agent-path precedence, inherited-PATH selection, first-match ordering, literal equality between the searched and launched PATH, and the end-to-end launch command. Positive cases prevent the refusal tests from passing vacuously with the resolver or git check removed.
- G4 remains intact. The run changes neither the generated Seatbelt profile policy nor the recursive immutability and NEG-5 mechanisms; `DEFAULT_IMM_CANDIDATES` and `NEVER_ADMITTED` remain pinned by value, with no new root. `select_launch_path_tool()` decides realpath admission before calling the byte-reading shim classifier, an empty `admitted_roots` admits nothing, `isolate()` passes exactly its computed readable entry paths, failed immutability remains fatal with cleanup, and NEG-5 remains mandatory.
- Exact-head Actions run 33195424212 targets `8ecb408c74d8c2a54ffafcca80db24561021184a`; all Python 3.11, 3.12, and 3.13 jobs and every listed step succeeded. Independent local package gates also passed: 463 skill checks, 109 package source files, and `git diff --check` with no diagnostics.

## Final Decision

PASS. The run removes the git shim from the executing preflight call site while preserving verification of the agent's actual PATH-selected git and failing closed whenever that cannot be done safely. The diff from `fc4f4a8` changes only this run's `IMPLEMENTATION.md` and `TEST.md` plus the two intended production/test scripts; VERSION, LICENSE, other run histories, sandbox admission constants, and isolation machinery are untouched. PR #20 remains open and Draft, is not merged, accurately records the Linux behavior change and all three endorsed limits, and cites the successful exact-head CI run.
