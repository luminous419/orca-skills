# REFACTORING Review Policy

Common Review Policy와 함께 적용한다.

## Structure and Scope
- 요청한 구조 개선이 달성되었는가?
- 변경이 최소 범위이며 unrelated 기능 변경이나 불필요한 abstraction/complexity가 없는가?
- public contract/API와 외부 behavior가 의도 없이 바뀌지 않았는가?

## Behavior Preservation Evidence
- behavior invariant가 명확하고 실제 diff와 일치하는가?
- relevant existing Unit Test를 반드시 실행했고 PASS했는가?
- 기존 테스트만으로 preservation evidence가 충분한가?
- evidence가 부족한 경우에만 필요한 테스트를 추가/수정했고 PASS했는가?

## FAIL Conditions
- behavior regression 또는 public contract의 의도치 않은 변경
- 관련 기존 테스트 미실행/실패
- preservation evidence가 부족한데 필요한 테스트를 보강하지 않음
- refactoring과 무관한 기능 변경 또는 complexity 증가

위 조건에 문제가 없고 Common Review Policy의 PASS 기준을 만족할 때만 `RESULT: PASS`다.
