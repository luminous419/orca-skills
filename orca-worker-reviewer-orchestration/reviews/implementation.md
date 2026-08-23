# IMPLEMENTATION Review Policy

Common Review Policy와 함께 적용한다.

## Requirement and Correctness
- 실제 source와 diff를 확인해 original requirement 충족 여부를 판단한다.
- 중요 logic, branch/boundary, failure handling, side effect와 compatibility를 검증한다.

## Minimality and Hygiene
- 변경이 요구사항을 충족하는 최소 범위인지 확인한다.
- unrelated refactoring/formatting/dependency 변경이 없는지 확인한다.
- 테스트 편의를 위한 불필요한 production API exposure나 architecture 왜곡을 확인한다.
- debug code, temporary artifact, secret 또는 의도하지 않은 파일이 diff에 없는지 확인한다.

## Mandatory Unit Test Gate
Production code 변경에는 대응 Unit Test 추가/수정 및 실행이 필수다. 없거나 실패하면 `RESULT: FAIL`이다.

테스트가 다음을 충족하는지 직접 검증한다.

- changed behavior/path를 실제로 실행하는 meaningful assertion
- 요구사항과 regression risk에 비례한 중요 branch/edge/failure coverage
- trivial/항상 성공하는 test가 아님
- 신규/수정 테스트와 관련 기존 테스트가 PASS
- 실행 command와 결과 evidence가 재검증 가능

## Review Feedback
Iteration > 1이면 이전 finding의 `RESOLVED | DISPUTED | BLOCKED` 상태와 실제 해결 여부를 확인한다.

## PASS Conditions
요구사항과 correctness가 충족되고, 변경이 최소이며, blocking finding이 없고,
필수 Unit Test와 실행 evidence가 충분할 때만 `RESULT: PASS`다.
severity만으로 자동 FAIL하지 않는다. Blocking 판정은 Common Review Policy의
`Severity and Blocking`을 따른다.
