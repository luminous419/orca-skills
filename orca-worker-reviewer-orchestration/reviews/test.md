# TEST Review Policy

Common Review Policy와 함께 적용한다.

검토:
- 테스트가 original requirement와 changed behavior를 실제로 검증하는가?
- 중요한 happy path/branch/edge/error case가 필요한 수준으로 포함되었는가?
- assertion이 의미 있는가?
- 테스트 실행 결과가 명확한가?
- 관련 기존 테스트가 깨지지 않았는가?
- TEST phase에서 발견된 production defect를 테스트 코드로 숨기지 않았는가?
- flaky/environment failure와 correctness failure를 구분했는가?

FAIL 조건:
- 필요한 핵심 테스트 누락
- 테스트가 실제 변경 경로를 실행하지 않음
- 의미 없는 assertion
- 신규/관련 테스트 실패
- production defect가 발견되었는데 PASS 처리
- 결과를 재현/검증할 수 없음
