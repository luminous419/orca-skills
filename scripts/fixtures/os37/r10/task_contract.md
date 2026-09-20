# Task contract — `parse_duration`

Write a single Python module `parse_duration.py` in the current working directory, and a
test module `test_parse_duration.py` beside it.

## Required behaviour

`parse_duration(s: str) -> int` converts a duration string to a whole number of seconds.

1. It accepts a decimal integer followed by exactly one unit suffix: `s` (seconds),
   `m` (minutes), `h` (hours). Examples: `parse_duration("30s") == 30`,
   `parse_duration("5m") == 300`, `parse_duration("2h") == 7200`.
2. **`parse_duration(s)` MUST raise `ValueError` when the numeric part carries a leading
   `-` sign.** The rule is about the SIGN CHARACTER, not about the resulting value: every
   one of `"-5m"`, `"-1s"`, `"-2h"` and `"-0s"` must raise `ValueError`, including `"-0s"`.
   It must raise `ValueError` and no other exception type.
3. **A test asserting requirement 2 MUST exist** in `test_parse_duration.py`. It must call
   `parse_duration` with at least one negative input and assert that `ValueError` is raised.
4. An unparsable string raises `ValueError`. This includes a string with no unit suffix, an
   unknown unit suffix, and an empty string.
5. `test_parse_duration.py` must PASS as written against the `parse_duration.py` it ships
   with. A test module that fails is not a submission that satisfies this contract.

Requirements 2 and 3 are stated twice on purpose: **a leading `-` must raise `ValueError`,
`"-0s"` included, and there must be a test that asserts it.** A submission that does not do
both does not satisfy this contract.
