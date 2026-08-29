# 결정론적 flow 실행 아이디어 — 리뷰

> 검토자: Claude Opus 5
> 검토일: 2026-08-30
> 검토 기준 커밋: `21fe5f6` (OS-20 머지 직후)
> 성격: **discovery / 의견 문서.** 구현 결정이나 승인된 설계가 아니며, Jira 티켓을 대체하지 않는다.

## 제안 요약

현재 개발 흐름 — 각 phase(PLAN, IMPLEMENTATION 등) 내부의 논리와 phase 간 전이 — 은
SKILL.md 안에 프롬프트로 정의되어 있다. 이를 LLM이 매번 읽고 해석하는데, 이 처리에는 두
가지 성질이 있다.

- 비결정론적이다.
- LLM을 거치므로 토큰을 소모한다.

따라서 **이미 정해진 로직을 코드로 구현해 결정론적으로 실행하면 더 예측 가능하고 비용
효율적이지 않겠는가**가 제안의 핵심이다.

## 결론

방향은 타당하다. 다만 제안을 그대로 적용하기 전에 두 가지를 정정할 필요가 있다.

1. **이 저장소는 이미 상당 부분 코드화되어 있다.** 문제는 "코드가 없다"가 아니라 "있는
   코드가 실행 경로가 아니다"이다.
2. **전면 코드화는 답이 아니다.** 코드로 옮겨야 할 것과 LLM에 남겨야 할 것 사이에 실제
   경계가 있다.

## 1. 현재 상태 실측

```text
scripts/*.py (테스트 제외)   약 19,000줄
SKILL.md × 2                약  3,200줄
```

이미 코드 비중이 훨씬 크다. 그리고 `scripts/orca_runtime_harness.py`(3,469줄)에는
`OrcaRuntimeHarness` 클래스와 함께 다음이 **이미 결정론적 코드로 구현되어 있다.**

- `cleanup_authority(role, origin, owned_by_this_dispatch)`
- `close_allowed(...)`
- `dispatch_context(...)`
- `_reviewer_gate_result(...)`, `_reviewer_review_verdict(...)`
- lifecycle 시나리오 러너 다수

그런데 호출자를 추적하면 **전부 `scripts/test_*.py`이다.**

```text
scripts/test_e2e_harness.py
scripts/test_orca_runtime_contract.py
scripts/test_os22_required_tests.py
scripts/test_run_logging.py
scripts/test_agent_profile.py
```

즉 이 결정론적 엔진은 프롬프트가 정의한 흐름이 **맞는지 검증하는 용도**이고, 실제 프로덕션
실행 경로는 여전히 LLM이 SKILL.md를 읽고 수행한다.

### 그래서 제안을 다시 쓰면

> "flow를 코드로 만들자"가 아니라
> **"이미 있는 결정론적 harness를 검증기에서 실행기로 승격하자"**

이 편이 범위가 좁고 즉시 실행 가능하며, 새로 만들 코드도 훨씬 적다.

## 2. 코드와 LLM의 경계

전면 코드화는 권장하지 않는다. 구분선은 **"이미 정해진 로직"과 "판단"** 사이에 있다.

| 코드로 옮기면 확실히 이득 | LLM에 남겨야 함 |
| --- | --- |
| phase 순서 · 전이 규칙 | 산출물이 옳은지 판단 |
| iteration counter, `max-iterations` 소진 | finding의 severity / blocking 판정 |
| 필수 섹션 · 필드 존재 검사 | "이 주장이 증거보다 넓은가" |
| artifact 경로 규칙 (§9) | 설계가 요구사항을 충족하는가 |
| lifecycle 4축 회계 (§6) | correction이 실제로 문제를 고쳤는가 |
| gate 통과 여부 계산 | 코드가 계약과 일치하는가 |

왼쪽을 프롬프트에 둘 이유는 없다. 비결정론적이고, 토큰을 쓰고, 무엇보다 **틀릴 수 있다.**

### 이 저장소에서 관측된 근거

추상적 주장이 아니라 PR #20 / #21 작업 중 실제로 발생한 일이다.

- **Reviewer가 "CI가 red"라고 판정 → 실제로는 green.**
  `gh run view` 한 번이면 결정론적으로 확정되는 사실이었고, 이 오판으로 IMPLEMENTATION
  gate가 한 사이클 낭비됐다.
- **heartbeat 처리가 매번 LLM을 거쳤다.**
  수십 회 반복된 순수 기계적 판정으로, 토큰 소모의 전형적 사례다.
- **반대 방향의 사례도 있다.** F-002(보안 가드가 "읽기 전에 거부"하는지)는 LLM 판단이
  필요했고 실제로 코드 리뷰가 잡아냈다. 이건 코드로 대체할 수 없다.

즉 왼쪽 열을 코드로 옮겼다면 이 작업은 눈에 띄게 짧고 저렴하고 정확했을 것이며, 오른쪽
열은 여전히 LLM이 필요했다.

## 3. 주의할 점 — 프롬프트 산문이 전부 낭비는 아니다

SKILL.md의 상당 부분은 규칙 자체가 아니라 **왜 그런 규칙인지를 설명해 LLM의 판단 품질을
높이는** 역할을 한다. 예를 들어:

> release가 `retained` + 외부 terminal 사유 + process action 없음을 반환하는 것은 정상
> 결과이며, 그 응답은 뒤이어 terminal을 직접 close해도 된다는 허가가 아니다.

이건 규칙이자 오판 방지 설명이다. 규칙만 코드로 옮기면 강제는 되지만, 경계 사례에서 LLM이
**왜 막혔는지 몰라 엉뚱하게 우회**할 수 있다.

따라서 현실적인 형태는 **"코드가 강제하고, 프롬프트는 그 이유를 설명한다"**의 이중 구조다.
이 저장소는 이미 그 방향이다 — SKILL.md의 `policy-contract` JSON 블록을
`scripts/test_policy_smoke.py`가 검증하는 구조가 정확히 그것이다.

## 4. Orca 의존성은 어떻게 달라지는가

### 현재 결합 실측

| | orchestration 스킬 | loop 스킬 |
| --- | --- | --- |
| 총 줄 수 | 2,110 | 1,096 |
| `Dispatch` 언급 | 81회 | 2회 |
| 구체적 `orca` 명령어 | 0회 | 0회 |

두 가지가 중요하다.

**첫째, 동일 정책이 이미 Orca 있이/없이 둘 다 돌고 있다.** `orca-worker-reviewer-loop`은
direct-session이고 `orca-worker-reviewer-orchestration`은 Orca-native인데, Worker/Reviewer,
correction loop, quality gate, agent profile 정책을 공유한다. loop의 `Dispatch` 언급 2회는
공유 문구 잔재다. **정책이 Orca와 분리 가능하다는 것은 이미 경험적으로 증명되어 있다.**

**둘째, SKILL.md에 구체적 orca 명령어가 0회다.** 우연이 아니라 설계다 — SKILL.md는
version-matched guide를 런타임에 로드해 그 문법을 따르라고만 하고 명령어를 하드코딩하지
않는다.

### 결정론화 이후

**총량은 줄지 않는다. 위치와 성격이 바뀐다.**

지금은 Orca 개념이 프롬프트 산문 전체에 흩어져 있다 — Dispatch 81회, 4축 lifecycle 회계,
terminal role enum 7종, reuse eligibility 8조건. LLM이 매 run마다 이를 해석한다.

코드로 옮기면 한 곳의 인터페이스로 응축된다.

```text
정책 코어 (Orca 무관)
  phase 전이 / counter / gate 계산 / artifact 경로 / quality gate
        │
        ▼  추상 인터페이스
실행 어댑터
  orca/     Run · Task · Dispatch · terminal lifecycle
  direct/   (loop이 이미 수행)
  기타/     미검증
```

### 실질적 변화

| 축 | 변화 |
| --- | --- |
| 정책 코어의 Orca 의존 | 사실상 0 (loop이 이미 증명) |
| 어댑터의 Orca 의존 | 오히려 **더 명시적이고 강해짐** |
| LLM이 Orca를 이해할 필요 | **크게 감소** ← 실질 이득 |
| Orca 교체 난이도 | 낮아짐, 단 동등 보증 대체재가 있을 때만 |

의존성은 줄어든다기보다 **"LLM 의존"에서 "코드 의존"으로 이동**한다. 그게 진짜 이득이다.
Orca에 덜 묶이는 것이 아니라, **Orca를 이해하는 주체가 비결정론적 LLM에서 결정론적 코드로
바뀌는 것**이다.

### 과대평가하지 말아야 할 부분

"의존성이 줄어든다"는 기대는 조심해야 한다. 실제로 사용된 Orca 기능 — 4축 lifecycle 회계,
dependency edge 기반 Reviewer 승격, `check --wait` 타입 필터, dispatch provenance — 은 얇은
어댑터로 감싸기 어렵다. 특히 **axis (a) settlement 판정은 Orca의 provenance가 곧 진실의
원천**이라, 추상화하면 그 보증이 약해진다. 이건 줄일 대상이 아니라 지켜야 할 것이다.

또한 **어댑터를 만든다고 Orca를 대체할 수 있게 되는 것도 아니다.** 대체하려면 상대가
Run/Task/Dispatch 수준의 provenance를 제공해야 하는데, 그런 대체재가 존재하는지 자체가
아직 미검증이다(OS-16이 `할 일`).

## 5. 기존 백로그와의 관계

이 아이디어는 완전히 새롭지 않다. 다음과 겹친다.

| 티켓 | 관계 |
| --- | --- |
| **OS-26** (discovery) | P2 후보 *Formal State Machine — lifecycle machine-readable화*가 사실상 동일 발상. "Markdown 설명만으로 lifecycle을 정의하지 말고 machine-readable state transition을 먼저 정의한다"고 명시 |
| **OS-26** | P1 후보 *Deterministic Sensors* — Reviewer에게 필수 섹션 존재 여부를 묻지 말고 validator가 처리 |
| **OS-9** | Reviewer Efficiency Phase 2 — 토큰 절감 목표가 직접 겹침 |
| **OS-11** | Skill Structure Refactoring — 코어/어댑터 분리의 실제 판단 기준이 될 수 있음 |
| **OS-16** | non-Orca 대안 비교 — 어댑터 분리의 전제 검증 |

다만 이 제안은 OS-26 discovery 전체보다 **범위가 좁고 즉시 실행 가능**하다는 점에서 별도
티켓으로 떼어낼 가치가 있다.

## 6. 제안하는 다음 단계

> **Promote the deterministic runtime harness from validation-only to the production
> execution path**

- 현재 `OrcaRuntimeHarness`가 test에서만 쓰이는 상태를 정리한다.
- phase 전이 · counter · gate 계산 · lifecycle 4축을 코드가 소유한다.
- SKILL.md는 판단 기준과 그 근거 설명만 남긴다.
- 코어/어댑터 경계를 validator로 강제한다 — 예: **정책 코어에서 Orca 심볼 참조 0건**을
  기계적으로 검사.

### 측정 가능한 성공 기준

의견이 아니라 수치로 판정할 수 있어야 한다.

- run당 토큰 소모 before / after
- 결정론적으로 확정 가능한 사실에 대한 LLM 오판 건수 (예: "CI가 red" 유형)
- 동일 입력에 대한 run 간 결과 분산
- 정책 코어의 Orca 심볼 참조 수

## 7. 이 문서의 한계

- **구현 결정이 아니다.** Adopt / Adapt / Reject 분석, 기존 백로그 중복 제거, 신규 티켓
  분해가 선행되어야 한다.
- **비용 절감 효과는 추정이며 측정되지 않았다.** 위 성공 기준으로 실측이 필요하다.
- OS-14 최신 회사환경 검증, OS-16 대안 비교 결과에 따라 결론이 바뀔 수 있다.
- Skill lifecycle, runtime semantics, Risk / Quality / Agent Profile 동작에 대한 변경을
  제안하지 않는다. 그 변경은 각자의 티켓과 acceptance criteria를 거쳐야 한다.
