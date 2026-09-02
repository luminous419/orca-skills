# Deterministic workflow trace examples

The normalized trace compares `sequence,node,route,phase,phase_iteration,
final_review_iteration,role,round_kind,intent_id,event_id,gate,terminal_status,reason_code`.
Adapter-specific task, dispatch, terminal, timestamp, and absolute path values are excluded.

```text
happy: ANALYSIS Worker -> Reviewer PASS -> ... -> TEST Reviewer PASS
       -> Final Reviewer PASS -> COMPLETED
correction: Reviewer FAIL -> same-phase correction Worker -> fresh Reviewer PASS
            -> downstream revalidation (HIGH) -> fresh Final Reviewer
blocking: NEEDS_INPUT or CONFLICT -> BLOCKED before another intent
budget: phase/final guard exhausted -> ESCALATED without another dispatch
```
