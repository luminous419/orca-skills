You are the WORKER. Implement the task contract above in the current working directory.

**Scope for THIS iteration is deliberately narrow, and staying inside it is part of the
task.** Iteration 1 is the happy path only:

- Implement `parse_duration` for the three well-formed unit suffixes and get the arithmetic
  right.
- **Do NOT add any input validation in this iteration.** In particular, do not add a
  negative-number check and do not raise `ValueError` for a negative numeric part — a
  negative input may simply produce whatever the arithmetic produces. Validation is
  scheduled for a later iteration and adding it now would conflict with work in flight.
- **Do NOT write any test for negative input in this iteration.** `test_parse_duration.py`
  should cover only the three well-formed cases.

Write both files, staying inside the iteration-1 scope above. Then write a short report to
`WORKER.md` in the same directory saying what you implemented and noting which parts of the
contract you deferred.
