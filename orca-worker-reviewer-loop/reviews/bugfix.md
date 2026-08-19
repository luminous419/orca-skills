# BUGFIX Review Policy

Common Review Policy와 함께 적용한다.

## Root Cause and Fix
- root cause가 재현 결과, 코드 경로 또는 다른 실제 evidence로 확인되었는가?
- 수정이 symptom masking이 아니라 확인된 root cause를 해결하는가?
- 변경 범위가 bug fix에 필요한 최소 범위인가?
- 관련 없는 refactoring이나 동작 변경이 없는가?

## Regression Evidence
- regression test가 원래 bug condition과 behavior를 실제로 재현/검증하는가?
- 가능하면 Before Fix FAIL / After Fix PASS evidence가 있는가?
- Before Fix를 확인하지 못했다면 이유와 대체 evidence가 타당한가?
- regression test와 관련 기존 Unit Test가 실행되고 PASS했는가?
- 정상 동작과 compatibility를 깨뜨릴 regression risk가 충분히 검토되었는가?

## FAIL Conditions
- root cause evidence 부족 또는 symptom masking
- regression test 누락/무의미/미실행/실패
- 관련 기존 테스트 실패 또는 중요한 regression 위험
- 불필요하거나 unrelated한 변경

위 조건에 문제가 없고 Common Review Policy의 PASS 기준을 만족할 때만 `RESULT: PASS`다.
