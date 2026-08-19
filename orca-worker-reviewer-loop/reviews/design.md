# DESIGN Review Policy

Common Review Policy와 함께 적용한다.

검토:
- requirements가 빠짐없이 반영되었는가?
- 실제 repository architecture/convention에 맞는 최소 설계인가?
- 책임, interface, data flow와 변경 범위가 구현 가능하게 정의되었는가?
- error handling, compatibility와 주요 failure mode가 다뤄졌는가?
- testing strategy가 중요한 behavior와 위험을 검증하는가?

FAIL 예:
- 핵심 requirement 또는 failure handling 누락
- repository 구조와 불일치하거나 구현 불가능한 설계
- 불필요한 abstraction/범위 확대
- 중요한 compatibility 또는 testing strategy 누락
