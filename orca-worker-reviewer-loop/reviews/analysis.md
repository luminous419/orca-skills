# ANALYSIS Review Policy

Common Review Policy와 함께 적용한다.

검토:
- 사용자 요청을 정확히 분석했는가?
- 실제 repository 근거가 있는가?
- 현재 동작/문제/영향 범위가 구분되어 있는가?
- 중요한 dependency/constraint/risk가 누락되지 않았는가?
- 사실과 가정/추론이 구분되어 있는가?
- 다음 PLAN/DESIGN 단계가 잘못된 전제로 시작할 위험이 없는가?

FAIL 예:
- 핵심 문제를 잘못 이해함
- 실제 코드 구조와 불일치
- 중요한 영향 범위 누락
- 검증되지 않은 가정을 사실처럼 사용
- 다음 단계 진행을 막는 핵심 unknown을 숨김
