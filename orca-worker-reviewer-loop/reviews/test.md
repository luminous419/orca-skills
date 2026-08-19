# TEST Review Policy

Common Review Policy와 함께 적용한다.

검토:
- 테스트가 original requirement와 changed behavior를 실제로 실행하고 검증하는가?
- assertion이 meaningful하며 중요 branch/edge/failure risk를 필요한 수준으로 다루는가?
- 추가/수정 및 관련 기존 테스트의 command/result evidence가 명확한가?
- production defect를 테스트 변경으로 숨기지 않았는가?
- correctness failure와 flaky/environment failure를 근거로 구분했는가?
- 남은 coverage gap과 regression risk가 정확히 보고되었는가?

FAIL 조건:
- 핵심 behavior 미검증 또는 trivial test
- 신규/관련 테스트 실패
- production defect를 숨기거나 PASS 처리
- 실행 결과를 재현/검증할 evidence 부족
