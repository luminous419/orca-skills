RESULT: PASS

DECISION_GATE_STATE: CLEAR

```decision-gate
{
  "state": "CLEAR",
  "reason_code": null,
  "open_decision_item": false,
  "grounds": "The iteration-3 BUGFIX re-review was an evidence-based verification of the prescribed fencing correction and introduced no decision requiring user authority.",
  "scope": "This phase's own conduct at this iteration."
}
```

UNIT_TEST_STATUS: PASS

## Verdict

The Final Adversarial Review finding F-01 is resolved. `record_receipt` and `settle` now require a non-empty lease token and both transitions use the same `_fenced` check, which compares the persisted token and owner before any state write. `heartbeat` retains equivalent token/owner fencing; `release` can only shorten a matching token's lease and cannot advance effect state. I found no migration or alternate mutation path around these transitions.

The executor takes the token directly from the record returned by `runtime_state.claim(intent)` and passes that exact value through `_settle_now`, `_recover`, `_collect`, and `AgentExecutionPort.start`. Neither the executor nor either adapter re-reads, re-mints, or substitutes the token. The port addition remains runtime-neutral: it is an opaque string capability, and adapters without a runtime-state ledger can still be used without one; once a ledger is wired, a missing token fails closed.

Normal-owner and re-entry behavior remains live. The positive fencing control records and settles under B after takeover, existing same-owner `RESUMED` tests pass, and the focused ownership/recovery regression set completes successfully. A lease expiry alone does not invalidate an uncontested owner's token; takeover rotates the token and is the fencing event.

## Direct mutation evidence

I independently applied each mutation to the production source, ran its targeted test, reversed the patch, and checked the original MD5 values.

- Optional fence restoration: `LeaseFencingTests` failed with 2 failures and 1 error; missing `None` tokens were accepted and the empty token raised the wrong exception.
- Quoted-substring lookup restoration: `test_the_orca_lookup_matches_the_parsed_intent_id_not_a_substring` failed because `task_mentions_us` was falsely returned.
- Executor propagation removal: `test_the_executor_hands_the_claim_token_to_the_adapter` failed with `None is not true`. The run terminated at the explicit 30-second observer bound (`Ran 1 test in 30.022s`), rather than waiting indefinitely.
- OrcaAdapter propagation removal: `test_a_slow_orca_start_cannot_land_its_task_after_takeover` failed because the observed exception was `RuntimeStateLeaseRequired`, proving the test distinguishes absent propagation from a correctly propagated stale token rejected as `LEASE_LOST`.

Restored hashes:

```text
06c78d2ff53a79696cb59f47f619544f  scripts/deterministic_workflow/runtime_state.py
3d2aed7b2a35505a59fcbcde6c90cced  scripts/deterministic_workflow/orca_adapter.py
3a37242e77a0e69df5fec3721c1ee58a  scripts/deterministic_workflow/executor.py
```

## Lookup and regression evidence

`OrcaAdapter.lookup` parses each string spec with `json.loads`, requires an object, and compares its top-level string `intent_id`. The positive canonical-spec test returns `task_mine`; the absence test returns `None`; non-JSON, scalar JSON, and foreign specs that merely mention the target do not match. Thus the substring false positive is removed without introducing a normal-spec false negative.

Focused command:

```text
python3 -m unittest scripts.test_deterministic_workflow_ownership \
  scripts.test_deterministic_workflow_round2 \
  scripts.test_deterministic_workflow_recovery
```

Actual result:

```text
Ran 139 tests in 7.736s

OK
```

This set rechecks the C2-001 claim race, ledger validation and fencing cases together with M2-001 complete-state ingress validation, M2-002 budget invariants, M2-003 recovery/fresh-process continuation, and M2-004 repository/artifact binding behavior. The coordinator's independently reported full 1946-test and packaging gates were not redundantly rerun. `git diff --check` remains clean after all mutation reversals.

## Blocking findings

None.

## Non-blocking notes

- The executor-propagation mutation takes the configured finite observation timeout before reaching its assertion. It is mutation-sensitive and bounded, though slower than the other targeted mutation checks.
