# OS-28 User Decisions

이 파일은 Coordinator가 사용자에게 직접 질문하고 받은 **명시적 결정**을 기록한다.
Worker/Reviewer 합의, 권고 default, 무응답/timeout은 여기에 기록될 수 없다.

기록 시각: 2026-08-30
결정 주체: 저장소 소유자 (사용자)
질문 방식: 선택지 + 영향 + 권고를 제시한 구조화된 질문

## UD-1 — Result Contract에 decision record 기록 여부 (ANALYSIS OQ-2)

**질문:** OS-28이 Worker/Reviewer Result Contract 템플릿에 decision record 섹션을 추가해야 하는가?

**결정:** **선택적(optional) 섹션 추가.**

`templates/` 와 `reviews/common.md` 에 optional decision record 섹션을 추가한다.
두 Skill이 byte-공유하므로 한 번의 편집셋이지만 7개 템플릿 × 2 Skill을 건드린다.

**근거(사용자에게 제시된 영향):** 계약만 만들고 아무것도 기록하지 않으면 계약이 관측
불가능해지고 검증 요구사항 4·5·6이 bind할 대상 없이 남는다. 필수 섹션으로 만들면 CLEAR일
때조차 출력을 강제하게 되어 기존 artifact 형식에 breaking change가 된다.

**적용 방법:** 섹션은 **optional**이다. 존재하지 않는 것이 계약 위반이 아니며, 존재할 경우에만
상태·reason code·evidence 형식을 검증한다.

## UD-2 — 검증 요구사항 5의 증명 수준 (ANALYSIS OQ-6)

**질문:** "안전·가역 fixture를 무조건 `NEEDS_INPUT`으로 만들지 않는다"를 어디까지 증명하는가?

**결정:** **허용(permission) 증명까지.**

계약이 해당 fixture에 대해 `ASSUMPTION_ALLOWED`/`CLEAR`를 **허용**하고 `NEEDS_INPUT`을
**요구하지 않음**을 테스트로 증명한다.

**근거(사용자에게 제시된 영향):** 실제 run이 그 상태를 **산출**함을 증명하려면 LLM을 루프에
넣은 평가 harness가 필요하며 이는 계약 전용 티켓의 범위를 벗어난다(OS-32 성격).

**적용 방법:** 이 한계 — 계약 수준 테스트는 실제 모델의 과잉 escalation을 탐지하지 못한다 —
를 완료 보고와 PR 설명의 알려진 한계에 **명시**한다. 해결되었다고 주장하지 않는다.

## UD-3 — 기존 `evaluate_invocation()` schema_version 게이트 부재 (ANALYSIS OQ-7)

**질문:** 기존 shipped 경로에 schema_version 게이트가 없어 예상치 못한 버전도 실행되는 결함을
이번 티켓에서 함께 고칠 것인가?

**결정:** **범위 밖. 별도 티켓으로 올린다.**

OS-28은 자기 loader만 fail-closed로 만들고 기존 경로는 건드리지 않는다.

**근거(사용자에게 제시된 영향):** 기존 경로에 게이트를 추가하면 지금 통과하던 계약이 앞으로
실패하게 되는 동작 변경이라 회귀 표면이 생긴다. "기존 결함과 이번 작업이 도입한 변경을
구분하라"는 지시에도 부합한다.

**적용 방법:** 사전 결함으로 기록하고 후속 티켓 후보로 완료 보고에 남긴다. 이번 변경이
그 결함을 고쳤다고 주장하지 않으며, 악화시키지도 않는다.

## UD-4 — quality-gate tier 밖 repository policy 충돌의 decision state (ANALYSIS OQ-9 / PLAN P7)

**질문:** explicit requirement가 quality-profile attribute가 **아닌** repository policy(코딩 관례,
project configuration, 코드 구조, security/privacy/compliance/tooling 정책)와 충돌할 때 decision
state는 무엇이며, suspended된 `requirement_vs_repository_policy` reason code는 어떻게 되는가?

**결정:** **(c) 전용 reason code 없이 기존 11개 boundary element가 라우팅한다.**

suspended된 `requirement_vs_repository_policy`는 되살리지 않는다. 계약에 아무것도 더하지 않고,
ANALYSIS가 확정한 `CONFLICT` entry condition(C-1/C-2/C-3) 문언도 건드리지 않는다.
확정 reason code 집합은 18개다.

**근거(사용자에게 제시된 영향):** 중요한 충돌은 기존 11개 boundary element(security, privacy,
compliance, 비가역성, blast radius 등)가 이미 `NEEDS_INPUT`으로 보낸다. (a) `CONFLICT`로 되살리면
방금 확정한 entry condition을 다시 넓혀야 하고 "둘 다 충족 불가"라는 의미에도 맞지 않으며 모든
관례 불일치에서 멈춘다. (b) `NEEDS_INPUT`으로 되살리면 의미는 가깝지만 사소한 policy 불일치마다
멈춰 과잉 escalation이 되고, security/privacy/compliance는 이미 해당 element로 도달하므로 중복이다.

**적용 방법 및 남는 한계:** 이 선택은 **"11개 element가 중요한 policy class를 전부 잡는다"는
검증되지 않은 가정에 의존한다.** ANALYSIS Worker가 그 목록을 열거해 검증하지 않았다고 명시했다.
이 가정을 사실로 서술하지 말고, 완료 보고와 PR의 알려진 한계에 가정으로 기록한다.

**overridability의 machine-checkability(참고):** policy가 파싱되는 contract의 named key로 표현된
경우에만 판정 가능하며 현재는 `RISK_SAFETY_FLOOR` 하나뿐이다. 저장소 어디에도 overridability
marker가 없으므로 "구분 불가능한 경우"가 정상 케이스다. 이 선택지는 그 경우를 "11개 element 중
참인 것이 있는가"로 해결한다.

## UD-5 — IMPLEMENTATION iteration 예산 증액 (TR4-1/2/3 수정 승인)

**질문:** `max-iterations=5`로 IMPLEMENTATION 예산이 5/5 소진된 상태에서 TEST revalidation이
계약 결함 3건(MAJOR 2, MINOR 1)을 발견해 보고했다. 어떻게 진행할 것인가?

선택지로 (a) 예산을 7회로 늘려 수정, (b) 현재 상태로 Final Review attempt 4를 돌리고
ESCALATED 가능성을 감수, (c) 지금 중단하고 알려진 한계로 기록해 PR 생성을 제시했다.

**결정:** **(a) IMPLEMENTATION 예산을 7회로 늘려 TR4-1/2/3을 수정한다.**

수정 후 TEST downstream revalidation을 거쳐 fresh Final Review attempt 4를 실행한다.

**근거(사용자에게 제시된 영향):** 세 결함 모두 범위가 좁고 수정 방향이 명확하다
(공유 helper로 통합 + parity 테스트) — FR-5와 같은 패턴이므로 이미 검증된 방법이다.
비용은 사이클 2~3회 추가. (b)는 ESCALATED로 끝나 세 결함이 미해결로 남고,
(c)는 Skill 계약상 COMPLETED가 아니다.

**적용 방법:** `max-iterations`의 다른 축(FINAL_REVIEW 5, TEST 5)은 그대로 둔다.
이 증액은 IMPLEMENTATION phase에만 적용되며, 이 run의 기록에 명시적 사용자 결정으로 남는다.
증액 자체가 결정 권한을 넓히지는 않는다 — 여전히 모든 phase gate와 Final Review가 그대로 걸린다.

## UD-6 — 세 축 iteration 예산 재증액 (FR-6/FR-7 수정 승인)

**질문:** Final Review attempt 4가 FR-6(CRITICAL, implementation)과 FR-7(MAJOR, test)을 냈다.
TEST 예산은 5/5 소진, implementation 1회 남음, FINAL_REVIEW 1회 남음. 어떻게 진행할 것인가?

선택지로 (a) 세 축 모두 증액, (b) FR-6만 고치고 FR-7은 후속 티켓으로,
(c) 지금 중단하고 알려진 한계로 기록해 PR 생성을 제시했다.

**결정:** **(a) 세 축 모두 증액한다.**

  TEST            5 -> 7
  IMPLEMENTATION  7 -> 9
  FINAL_REVIEW    5 -> 7

FR-6과 FR-7을 고치고 fresh Final Review attempt 5를 실행한다.

**근거(사용자에게 제시된 영향):** FR-6은 실질적 결함이다 — `validate_record()`가 reason code의
boundary **이름만** 확인하고 그 boundary의 **값이 진입 조건을 만족하는지**는 확인하지 않아,
실제로 발동하지 않은 boundary로 `pause_and_ask` 기록을 정당화할 수 있고 Reviewer가 계약으로
오분류를 거부할 수 없다. 이는 티켓이 명시적으로 요구한 "Reviewer의 오분류 판정 가능성"을
충족하지 못한다는 뜻이다. Coordinator가 8/8 재현해 확인했다.
FR-7은 그 결함을 테스트가 놓친 이유이며 둘은 같은 수정의 양면이다.
수정 방향도 명확하다 — 공유 판정 경로를 `CLEAR`/`NEEDS_INPUT`/`CONFLICT`에도 적용한다.

**적용 방법 및 유의:** 이번이 **두 번째 증액**이다(UD-5에 이어). 증액 자체는 결정 권한을
넓히지 않으며 모든 phase gate와 Final Review가 그대로 걸린다. 다만 무한히 늘릴 수는 없으므로,
attempt 5 이후에도 같은 계열의 결함이 계속 나오면 Coordinator는 증액을 반복 제안하지 말고
**중단 여부를 사용자에게 다시 묻는다.**

## UD-7 — attempt 5 이후 계속 진행 여부

**질문:** Final Review attempt 5가 FR-8(CRITICAL, `kind=boolean` boundary의 타입 미검사로
fail-open)과 FR-9(MAJOR, 그것을 놓친 테스트)를 냈다. UD-6에 기록한 대로, 같은 계열의 결함이
계속 나오면 증액을 반복 제안하지 않고 중단 여부를 다시 묻기로 했다.
이번에는 예산이 남아 있다(FINAL_REVIEW 5/7, implementation 7/9, test 6/7).

**결정:** **남은 예산으로 한 사이클 더 진행한다. 증액하지 않는다.**

implementation 1회 + test 1회 + Final Review attempt 6.

**근거(사용자에게 제시된 영향):** FR-8은 범위가 좁고 수정 방향이 명확하다 — enum element의
멤버십 검사와 같은 자리에 boolean 타입 검사를 추가하는 것이다. Coordinator가
`security='yes'` / `1` / `{'a':1}` / `None` 이 모두 `ASSUMPTION_ALLOWED`를 내는 것을 재현했다.
다만 **test 예산이 1회만 남으므로 그 이후에도 같은 계열이 나오면 다시 여쭙는다.**

**적용 방법:** 예산을 늘리지 않는다. 이 사이클 안에서 닫지 못하면 그것 자체가 정보이며,
Coordinator는 임의로 연장하지 않고 사용자에게 보고한다.

## UD-8 — 세 번째 예산 벽에서 한 사이클 더 (RI9-1 / FR-9)

**질문:** IMPLEMENTATION Reviewer가 RI9-1을 냈다 — `policy_source`의 locator가 없거나
비어 있거나 공백·숫자·null이어도 `ASSUMPTION_ALLOWED`가 허용되어, "정책이 뒷받침한다"고
주장하면서 아무 곳도 가리키지 않을 수 있다. IMPLEMENTATION 예산이 9/9로 소진됐다(세 번째 벽).

**결정:** **한 사이클 더 진행한다.** IMPLEMENTATION +2 (9 -> 11), TEST +1 (7 -> 8).
RI9-1과 대기 중인 FR-9를 고치고 Final Review attempt 6을 실행한다.

**근거(사용자에게 제시된 영향):** RI9-1은 이 run에서 나온 것 중 **가장 좁은 결함**이다 —
한 필드의 non-empty text 모양 검사이며 I/O가 필요 없다. 실제 존재 확인만 I/O 계층의 한계로
남긴다. FR-9(테스트 보강)는 이미 대기 중이다.
Coordinator가 사용자에게 이 run의 추이를 정직하게 제시했다: 매 사이클이 이 계열의 갭 하나를
닫으면 다음 리뷰가 인접한 갭을 찾으며, 다만 **범위가 계속 좁아지고 있다**
(계약 semantics -> API parity -> record 경로 -> 값 도메인 -> 한 필드의 모양).
수렴 신호이지만 끝났다고 보장할 수는 없다는 점을 함께 알렸다.

**적용 방법:** 이번에도 **증액이 결정 권한을 넓히지 않는다.** 모든 gate가 그대로 걸린다.
이 사이클 이후에도 같은 계열이 나오면 Coordinator는 다시 여쭙는다.
