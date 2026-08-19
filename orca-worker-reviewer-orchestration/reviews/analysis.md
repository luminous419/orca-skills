# ANALYSIS Review Policy

Common Review Policy와 함께 적용한다.

검토:
- 요청과 실제 repository evidence가 정확히 연결되는가?
- 현재 동작, 기대 동작, 문제와 영향 범위가 구분되는가?
- 중요한 dependency, constraint, risk와 unknown이 드러나는가?
- 사실과 가정/추론이 구분되어 다음 단계가 안전하게 진행될 수 있는가?

FAIL 예:
- 핵심 문제를 잘못 이해하거나 repository 구조와 불일치
- 중요한 영향 범위/제약 누락
- 검증되지 않은 가정을 사실로 사용
- 다음 단계를 막는 핵심 unknown 또는 evidence 누락
