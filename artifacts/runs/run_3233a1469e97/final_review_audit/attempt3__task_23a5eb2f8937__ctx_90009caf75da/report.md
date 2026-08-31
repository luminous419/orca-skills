RESULT: FAIL
REVIEW_VERDICT: FAIL

## Summary

FR-1~FR-4와 RI3-1 수정은 배포된 계약에서 유지된다. 4×4 전이 값, reason-code/boundary-element 일치, 닫힌 사용자 권한 source allowlist, 실제 entry predicate 평가, 두 precedence cell, enum trigger 일관성을 코드와 테스트에서 확인했다. 정당한 `CLEAR`/`ASSUMPTION_ALLOWED` positive path도 살아 있고 `permitted_states(policy, {}) == frozenset()`은 무증거를 CLEAR로 보지 않는 fail-closed 선택으로서 DESIGN D2-2c와 일치한다.

그러나 final adversarial probe에서 새로운 권한 우회 1건을 재현했다. `permitted_states()`의 `explicit_user_authorization` predicate는 `user_decision.source`의 allowlist membership만 확인하고 계약이 필수로 선언한 `where_recorded`와 `resolves`를 확인하지 않는다. 동일한 불완전 evidence를 `validate_transition()`은 거부하지만 state evaluator는 `CLEAR`로 승인하므로, machine-readable 계약의 두 소비 경로가 사용자 승인 여부에 대해 모순된다.

UD-1~UD-4는 구현에서 뒤집히지 않았고 OS-29 이상 runtime/UI/pause-resume 범위는 추가되지 않았다. M-21b, UD-2 permission 한계, UD-4의 11-element 포괄성 가정, runtime 미구현, V-3/V-4는 기록된 한계로 확인했으며 새 finding으로 세지 않았다. ANALYSIS Reviewer 최초 dispatch가 Codex update prompt로 readiness 실패한 뒤 recovery 목적으로 task status를 수동 복구하고 재dispatch한 provenance도 기록과 일치하며 정상 readiness 증거로 확대 해석하지 않았다.

## Blocking Findings

### FR-5

- ID: FR-5
- Quality Attribute: G1
- Severity: CRITICAL
- Blocking: YES
- Responsible Phase: implementation
- Location: `scripts/decision_policy.py:573-579, 615-692, 767-795`; `scripts/test_decision_policy.py:349-365, 509-523`
- Issue: `permitted_states()`가 필수 필드가 빠진 `user_decision`도 allowlisted `source` 하나만 있으면 explicit user authorization으로 인정하여 고영향 또는 사용자 전용 결정을 `CLEAR`로 허용한다.
- Reason: 계약의 `user_decision_fields`는 `source`, `where_recorded`, `resolves` 세 필드를 필수 evidence로 선언하고 ANALYSIS A5-3/INV-5는 이를 사용자 결정의 증거로 요구한다. `validate_transition()`은 세 필드를 모두 non-empty로 검사하지만 `_evaluate_predicate()`의 authorization 계산은 source membership만 본다. 따라서 `{"explicit_user_authority":"reserved","security":true,"user_decision":{"source":"explicit_user_reply"}}`가 실제 사용자 응답의 위치나 해결 대상을 전혀 증명하지 않아도 `CLEAR`를 얻는다. 이는 단순한 승인 category 주장도 실제 명시적 사용자 권한의 증거로 승격하지 말라는 경계를 위반한다.
- Evidence: 직접 실행한 probe에서 위 불완전 record의 `permitted_states()` 결과는 `['CLEAR']`이었다. 같은 mapping으로 `validate_transition(policy, 'NEEDS_INPUT', 'CLEAR', facts)`를 호출하면 `user_decision requires a non-empty 'where_recorded'`로 거부되어 production API 내부 모순도 확인됐다. 기존 642-check validator와 1404-test suite는 green이며, `test_an_allowlisted_authorization_permits_clear`가 의도적으로 source-only fixture를 positive control로 사용하여 결함을 고정하고 있다.
- Required Action: 사용자 결정의 유효성 판정을 한 helper로 통합하여 source가 닫힌 allowlist에 속하고 `policy.user_decision_fields` 전부가 non-empty일 때만 `explicit_user_authorization` predicate가 true가 되게 하라. reserved authority, 각 conflict clause, irreversible/high-impact cases에 대해 source-only 및 각 필드 누락 mutation이 `CLEAR`를 허용하지 않는 cardinality-guarded negative sweep을 추가하고, 두 완전한 genuine-user fixture가 여전히 `CLEAR`를 허용하는 positive control을 유지하라. `validate_transition()`과 `permitted_states()`가 같은 evidence에 같은 authorization 판정을 내리는 parity test도 추가하라.

## Non-Blocking Recommendations

없음. 이미 정직하게 기록된 residual gaps를 중복 recommendation으로 만들지 않는다.

## Test Review

- `python3 scripts/validate_skills.py` — PASS, 642 checks. 두 Skill의 계약 parity와 값 고정 C15~C31, template/review section, authority vocabulary를 검증했다. `permitted_states()`가 `user_decision_fields` 전체를 소비하는지는 검증하지 않는다.
- `python3 -m unittest discover -s scripts -p 'test_*.py'` — PASS, 1404 tests in 299.305s, skipped=6. FR-1~FR-4/RI3-1 회귀와 positive paths를 포함하지만 source-only authorization을 올바른 positive case로 기대하여 FR-5를 탐지하지 못한다.
- `python3 scripts/verify_package.py` — PASS, 173 source files.
- `python3 scripts/build_release.py` — PASS, reproducible `dist/orca-skills-0.9.0.tar.gz` 생성.
- `python3 scripts/verify_package.py --archive "dist/orca-skills-$(tr -d '\n' < VERSION).tar.gz"` — PASS, 173 source files 및 archive 검증.
- `git diff --check` — PASS, 출력 없음.
- 독립 FR-5 probe — `security=true`, `explicit_user_authority=reserved`, allowlisted source만 있는 불완전 `user_decision`을 전달했다. `permitted_states()`는 `CLEAR`만 반환했고, `validate_transition()`은 같은 evidence에서 누락된 `where_recorded`를 이유로 거부했다.
- 코드/계약 대조 — 11개 boundary element, 18개 reason code, confidence/risk/profile 비권한화, 두-Skill raw equality와 expected constants, 정당한 CLEAR/ASSUMPTION_ALLOWED 경로, D2-2c empty-facts semantics를 확인했다. 실제 LLM의 과잉 escalation, OS-29 runtime enforcement, M-21b/V-3/V-4식 coordinated test-assertion 변경은 이 static suite가 검증하지 않으며 해결되었다고 주장하지 않는다.

## Final Decision

FAIL. 다섯 correction은 보존되었지만 `permitted_states()`가 불완전한 사용자 결정 주장을 실제 명시적 승인으로 승격하는 CRITICAL authority bypass가 남아 있다. IMPLEMENTATION correction에서 authorization evidence 판정을 통합하고 mutation-resistant negative/positive parity tests를 추가한 뒤 downstream TEST 재검증과 fresh Final Adversarial Review가 필요하다.