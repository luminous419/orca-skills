# IMPLEMENTATION Review Policy

Common Review Policy와 함께 적용한다.

## Requirement
구현이 original requirement를 실제로 충족하는지 확인한다.

## Actual Diff
Worker summary만 읽지 말고 실제 source와 diff를 확인한다.

## Correctness
- logic
- conditions
- boundaries
- null / empty
- exception handling
- side effects
- compatibility

## Scope
불필요한 변경이 포함되지 않았는지 확인한다.

## Mandatory Unit Test Gate
Production code가 변경되었다면 대응 Unit Test가 반드시 존재해야 한다.
없으면 `RESULT: FAIL`.

## Unit Test Quality
- meaningful assertion
- changed path 실행
- relevant branches
- edge cases
- exception behavior

`assertTrue(true)` 같은 테스트는 인정하지 않는다.

## Unit Test Execution
Worker가 테스트를 실행했는지 확인한다.
새 테스트 및 관련 기존 테스트는 PASS여야 한다.
실패하면 `RESULT: FAIL`.

## BUGFIX
Task Type이 BUGFIX라면 regression test가 반드시 존재해야 한다.
없으면 `RESULT: FAIL`.
Regression Test는 bug condition을 직접 검증해야 한다.

## Review Feedback
이전 finding 해결 여부를 실제 code/artifact로 재검증한다.

## PASS Conditions
- requirement satisfied
- implementation correct
- no CRITICAL
- no MAJOR
- required Unit Test exists
- test validates changed behavior
- new Unit Test PASS
- relevant existing test PASS
- BUGFIX이면 regression test 존재

모두 충족하면 `RESULT: PASS`, 아니면 Blocking 사유로 `RESULT: FAIL`.
