# REFACTORING Review Policy

Common Review Policy와 함께 적용한다.

검토:
- 요청된 구조 개선이 실제로 달성되었는가?
- 외부 behavior가 유지되는가?
- public contract/API가 의도 없이 바뀌지 않았는가?
- 불필요한 abstraction이나 complexity가 추가되지 않았는가?
- 관련 Unit Test가 충분히 존재하고 PASS하는가?
- behavior preservation 근거가 있는가?
- scope가 과도하게 확장되지 않았는가?

FAIL 조건:
- behavior regression 가능성
- public contract의 의도치 않은 변경
- 관련 테스트 미실행/실패
- behavior preservation 검증 부족
- refactoring과 무관한 기능 변경
- complexity를 오히려 증가시키는 구조 변경
