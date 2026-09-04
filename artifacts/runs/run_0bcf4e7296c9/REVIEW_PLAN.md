# Review Result

RESULT: FAIL
REVIEW_VERDICT: FAIL
DECISION_GATE_STATE: CLEAR

## Summary

PLAN 의 구조·범위·순서는 대체로 견고하다. AC 1~16 이 전부 named test 로 매핑되었고, 사용자
지정 11개 검증 항목도 11개 모두 담당 test 와 **구체적인 mutation 감도**(`>=`→`>`, guard reorder,
FAIL edge→next-phase, dedupe 제거, idempotency lookup bypass 등)와 함께 배정되었다. ANALYSIS 가
열어둔 `e2e_harness.py` 처분도 회피하지 않고 확정했고(pure logic 추출 + `run_workflow` 는
test-only parity oracle 로 존치 + production import 금지 static test), packaging 계획은
`release_manifest.py` / `verify_package.py` 의 실제 구조와 정확히 맞는다 — 직접 확인했다.
범위 확대나 OS-31/37/38 침범도 없다.

그러나 **Validation / Test Plan 이 이 저장소가 쓰지 않는 test runner 위에 세워져 있다.**

- 저장소의 test 20개 파일은 **전부 `unittest`** 를 쓰고 `pytest` 를 쓰는 파일은 **0개**다.
- 실제 CI(`.github/workflows/ci.yml`)의 test step 은
  `python3 -m unittest discover -s scripts -p 'test_*.py'` 다.
- `pytest` 라는 문자열은 `scripts/*.py`, `docs/*.md`, `README.md`, `INSTALL.md`,
  `.github/workflows/*.yml` **어디에도 없다.**
- 그리고 PLAN 이 TEST gate 명령으로 지정한 `python3 -m pytest scripts/ -q` 를 실제로 실행하면
  **테스트가 하나도 돌지 않는다** — collection 단계에서 5개 error 로 중단된다.

반면 저장소의 실제 명령은 정상 동작한다: `python3 -m unittest discover -s scripts -p 'test_*.py'`
→ **Ran 1725 tests … OK (skipped=6)**. `validate_skills.py`(714 checks PASSED),
`verify_package.py`(195 source files PASSED), `git diff --check`(clean)도 전부 정상이다.
즉 문제는 저장소가 아니라 **PLAN 이 고른 runner** 다.

이 선택은 세 갈래로 파급된다: (1) AC 16 과 사용자 요구 “기존 테스트 회귀 없음” 의 증거를
지정된 방식으로는 얻을 수 없고, (2) PLAN 이 설계한 dependency-absent lane 의 skip 기제
`pytest.importorskip(...)` 은 CI 의 `unittest discover` 아래에서 skip 이 아니라 **error** 가 되며,
(3) 그 호출은 langgraph 가 **설치되어 있어도** skip 된다(측정 확인). 세 번째는 PLAN 자신의
“dependency-present lane 은 skip 0건” gate 를 스스로 무너뜨린다. 이는 G1/G2 위반이며,
승인된 ANALYSIS 가 명시한 standard-library-only 전제(`ANALYSIS.md:77`)와도 정면으로 어긋난다.

**공정성 기록:** dispatch 의 ENVIRONMENT FACTS 절이 “저장소 테스트는 `python3 -m pytest scripts/ -q`
로 실행한다” 고 단언하며 재도출을 금지했다. Worker 는 그 지시를 따랐다. 그 사실은 이 defect 의
출처를 설명하지만 defect 자체를 없애지는 않는다 — DESIGN/IMPLEMENTATION/TEST 가 이 계획대로
진행하면 새 test module 이 CI 에서 실행되지 않고 AC 16 증거가 공백이 된다. 또한 skip 기제 선택과
stdlib-only 전제 위반은 environment facts 가 지시한 바가 아니라 PLAN 자신의 결정이다.
**Coordinator 는 ENVIRONMENT FACTS 의 해당 항목을 정정하는 것이 좋다.**

수정 범위는 좁다. runner 를 저장소의 실제 runner 로 바꾸고 skip 기제를 stdlib 로 교체하면 되며,
AC 매핑·mutation 설계·work item·순서·packaging 은 그대로 살릴 수 있다.

## Blocking Findings

### F-001

```text
ID: F-001
Quality Attribute: G1, G2
Severity: MAJOR
Blocking: YES
```

**Location**: `PLAN.md:48` (skip 기제), `PLAN.md:139` (AC 16 행), `PLAN.md:161-175`
(실행 명령과 두 lane), `PLAN.md:164,168,169` (pytest 명령)

**Issue**: Validation / Test Plan 전체가 `pytest` 를 전제하지만, 이 저장소는 `unittest` 기반이고
`pytest` 에 의존하지 않으며, PLAN 이 지정한 pytest 명령은 현재 상태에서 테스트를 한 개도
실행하지 못한다.

**Reason (전부 직접 실행/확인)**:

1. **runner 불일치**
   - `scripts/test_*.py` 20개 전부 `import unittest`; `import pytest` 는 0개.
   - `.github/workflows/ci.yml` 의 “Run deterministic tests” step:
     `python3 -m unittest discover -s scripts -p 'test_*.py'` (Python 3.11/3.12/3.13 matrix).
   - `pytest` 문자열은 `scripts/*.py`, `docs/*.md`, `README.md`, `INSTALL.md`,
     `.github/workflows/*.yml` 어디에도 없다.

2. **지정된 명령이 실제로 실패한다**
   ```text
   $ python3 -m pytest scripts/ -q
   ERROR scripts/fixtures/final_review_eval/subject/head/tests/test_config.py
   ERROR scripts/fixtures/final_review_eval/subject/head/tests/test_pipeline.py
   ERROR scripts/fixtures/final_review_eval/subject/head/tests/test_policy.py
   ERROR scripts/fixtures/final_review_eval/subject/head/tests/test_quota.py
   ERROR scripts/fixtures/final_review_eval/subject/head/tests/test_validation.py
   !!!!! Interrupted: 5 errors during collection !!!!!
   1 warning, 5 errors in 0.51s
   ```
   원인은 `import file mismatch` 다 —
   `scripts/fixtures/final_review_eval/subject/base/tests/test_config.py` 와
   `.../head/tests/test_config.py` 가 둘 다 `__init__.py` 를 가진 `tests` package 안에 있어
   module 이름 `tests.test_config` 로 충돌한다(5개 파일 모두 동일). 저장소 어디에도
   `conftest.py` 나 pytest 설정 파일이 없어 collection 범위를 좁히는 장치가 없다.
   **repo test 는 단 하나도 실행되지 않는다.**

3. **저장소의 실제 명령은 정상이다 (대조군)**
   ```text
   $ python3 -m unittest discover -s scripts -p 'test_*.py'
   Ran 1725 tests in 324.013s
   OK (skipped=6)
   ```

4. **skip 기제가 세 가지 방식으로 깨진다** (`PLAN.md:48`
   `pytest.importorskip("langgraph", minversion="0.2.76")`)
   - CI 의 `unittest discover` 아래에서 module-level `pytest.importorskip` 은 `Skipped` 예외를
     import 시점에 던진다. unittest 는 이를 skip 으로 해석하지 않고 **ERROR** 로 보고한다 —
     PLAN 이 설계한 dependency-absent legacy lane 이 바로 그 lane 에서 깨진다.
   - `pytest` 를 test 의존성으로 들여오는 것은 승인된 ANALYSIS 가 명시한 standard-library-only
     전제(`ANALYSIS.md:77`, `validate_skills.py:1086-1092`, `docs/COMPATIBILITY.md:93-100`)와
     어긋난다. reviewer context 상 승인 baseline 과의 명백한 모순은 넘어갈 사항이 아니다.
   - **langgraph 가 설치되어 있어도 skip 된다.** `langgraph` 는 `__version__` 을 노출하지 않아
     `minversion` 비교가 항상 실패한다. 측정 결과:
     ```text
     >>> pytest.importorskip('langgraph', minversion='0.2.76')
     Skipped: module 'langgraph' has __version__ None, required is: '0.2.76'
     >>> hasattr(langgraph, '__version__')      -> False
     >>> importlib.metadata.version('langgraph') -> '0.2.76'
     ```
     이는 PLAN 자신이 세운 “dependency-present lane 은 skip 0건” gate(`PLAN.md:48`)를
     달성 불가능하게 만든다 — 계획이 내부적으로 모순이다.

**영향**: AC 16(“full unit tests … 통과”)과 사용자 요구 “기존 테스트, skill validation 및
package verification 회귀 없음” 의 증거를 지정된 방식으로 얻을 수 없고(G1), 새 test module 을
pytest 양식으로 작성하면 저장소 CI 에서 실행조차 되지 않는다(G2). 이 결정은 W1~W7 전부의
“tests” 작성 방식을 좌우하므로 DESIGN 이전에 고쳐야 한다.

**Required Action**:
1. 회귀/TEST gate 와 신규 test module 을 저장소의 실제 runner 에 맞춘다:
   `python3 -m unittest discover -s scripts -p 'test_*.py'`.
   pytest 를 **의도적으로 도입**하려면 그것 자체를 work item 으로 세우고(의존성 선언, CI step
   추가, `scripts/fixtures/` collection 제외 설정), stdlib-only 전제 변경을 명시적으로 다뤄야
   한다. 어느 쪽이든 “현재 명령이 그대로 동작한다” 는 전제는 성립하지 않는다.
2. langgraph skip 기제를 unittest 호환 stdlib 방식으로 교체한다
   (예: `importlib.util.find_spec("langgraph")` + `unittest.skipUnless` / `raise unittest.SkipTest`),
   버전 확인은 `__version__` 이 아니라 `importlib.metadata.version("langgraph")` 로 한다.
3. 두 lane 의 통과 조건을 선택한 runner 의 실제 출력 형식으로 다시 쓴다
   (현재 baseline 은 `OK (skipped=6)` 이므로 “skip 0건” 이 아니라 **기대 skip allowlist** 로
   표현해야 한다 — dependency-absent lane 뿐 아니라 present lane 에서도 기존 6개 skip 이 남는다).

## Non-Blocking Findings

### N-001

```text
ID: N-001
Quality Attribute: NONE
Severity: MINOR
Blocking: NO
```

**Location**: `PLAN.md:139` (AC 16 행), `PLAN.md:163-173` (명령 블록)

**Issue**: AC 16 행이 증거로 “dependency/license validator” 를 명시하지만, 그 validator 는
파일 목록(`PLAN.md:18-38`)에도, 실행 명령 블록에도 존재하지 않는다. 실제 산출물은
`docs/LANGGRAPH_DEPENDENCIES.md` 의 license inventory(문서 표)로 보인다.

**Reason**: Jira AC 16 의 문구는 “dependency/license 검증” 이고 문서 기반 검증도 이를 충족할 수
있으므로 요구사항 위반은 아니다. 다만 매핑 표가 존재하지 않는 자동화 도구를 지목하고 있어,
이번 PLAN 의 리뷰 질문 1(“손이 많이 가는 항목이 실체 없는 한 줄로 처리되지 않았는가”)에
정확히 답하지 못한다.

**Required Action**: optional — 해당 셀을 실제 기제(문서 표 + 수동 대조인지, 아니면
스크립트인지)로 정정하거나, 스크립트를 만들 것이면 파일 목록과 명령 블록에 추가한다.

### N-002

```text
ID: N-002
Quality Attribute: NONE
Severity: MINOR
Blocking: NO
```

**Location**: `PLAN.md:175` (“Dependency-absent lane 은 isolated Python environment 에서…”)

**Issue**: dependency-absent lane 을 **어떻게 만드는지** 가 지정되어 있지 않다. 현재 개발
환경에는 langgraph 가 이미 설치되어 있고, repository policy 는 외부 network 접근과 package
download 를 금지한다. 따라서 TEST phase 가 “langgraph 가 없는 환경” 을 실제로 어떻게 구성할지가
열려 있다.

**Reason**: 실행 불가능하지는 않다(예: `python3 -m venv` 로 빈 환경을 만들거나, import 차단
stub 을 쓰는 방법이 네트워크 없이 가능하다). 요구사항 위반도 아니다. 다만 TEST 가 이 lane 을
집행해야 하므로 기제가 지정되지 않으면 그 단계에서 즉흥적으로 정해진다.

**Required Action**: optional — network 없이 재현 가능한 구성 방법 하나를 PLAN 또는 DESIGN 에서
지정한다.

## Test Review

이 phase 는 production code 를 변경하지 않으므로 validation 은 (1) 인용/전제의 정확성,
(2) 계획의 실행 가능성, (3) AC/검증항목 커버리지다.

**코드 변경 없음 확인**: `git status --short` 에 tracked 수정 0건(전부 `??` untracked artifact),
branch `feat/os-40-langgraph-engine`, HEAD `7bc228a`. delta 는 `PLAN.md` 신규 생성뿐이다.

**PLAN 이 전제한 명령·API·구조를 직접 실행/확인한 결과**

| PLAN 의 전제 | 확인 결과 |
| --- | --- |
| `python3 -m pytest scripts/ -q` (`:164`) | **실패** — collection 5 errors, 실행된 test 0개 (F-001) |
| `pytest.importorskip("langgraph", minversion="0.2.76")` (`:48`) | **오작동** — langgraph 설치 상태에서도 Skipped (F-001) |
| `python3 scripts/validate_skills.py` (`:165`) | OK — `Skill validation PASSED (714 checks)` |
| `python3 scripts/verify_package.py` (`:166`) | OK — `Package verification PASSED (195 source files)` |
| `python3 scripts/build_release.py --output <path>` (`:170`) | OK — `--output` 인자 존재 (`build_release.py:41,44`), 임시 경로 사용 가능하므로 `dist/` 미접촉 계획도 성립 |
| `python3 scripts/verify_package.py --archive <path>` (`:171`) | OK — 지원되는 형식 |
| `git diff --check` (`:172`) | OK — clean |
| “`scripts/`는 이미 release root” (`:14`, `release_manifest.py:41`) | 정확 — `INCLUDED_ROOTS = (".github", "docs", "scripts", *SKILL_NAMES)`; `release_files()` 가 rglob 하므로 `scripts/deterministic_workflow/` 는 자동 포함 |
| installed Skill 의 unexpected-file guard 갱신 필요 (`:42,100`) | 정확 — `release_manifest.verify_source_tree` 가 `packaged_skill_files - required` 를 `unexpected Skill package files` 로 거부(`:113-133`), `required_skill_paths`(`:76-88`)에 추가해야 함 |
| “installed Skill 은 repository `scripts/`를 복사하지 않는다” (`:14`) | 정확 — `INSTALL.md` 는 `cp -R <skill-dir> ~/.claude/skills/` 만 안내 |
| source/installed exact parity 갱신 필요 (`:42`) | 정확 — 현재는 단일 파일 pair 만 강제(`validate_skills.py:2974`), tree parity validator 없음 |
| AC 15 가 지목한 기존 test 파일들 | 전부 존재 — `test_decision_gate.py`, `test_decision_policy.py`, `test_os29_decision_gate.py`, `test_clarification_protocol.py`, `test_run_logging.py`, `test_validate_skills.py` |
| exact pins (`:46`) | ENVIRONMENT FACTS 및 설치본과 일치 — 0.2.76 / 2.1.1 / 0.1.74 / 0.3.80 / 0.3.45 |
| `validate_skills.py` 의 stdlib-only “전제” 와 충돌 여부 (리뷰 질문 4) | 강제하는 검사는 **없다**(1089행 docstring 진술뿐). 따라서 **optional runtime** langgraph 는 충돌하지 않는다. 그러나 pytest 를 test 의존성으로 들이는 것은 그 전제와 어긋난다(F-001) |

**저장소 baseline (직접 실행)**
```text
python3 -m unittest discover -s scripts -p 'test_*.py'  ->  Ran 1725 tests, OK (skipped=6)
python3 scripts/validate_skills.py                      ->  PASSED (714 checks)
python3 scripts/verify_package.py                       ->  PASSED (195 source files)
git diff --check                                        ->  clean
```
즉 OS-40 이전 baseline 은 건강하다. F-001 은 baseline 문제가 아니라 PLAN 의 runner 선택 문제다.

**커버리지 판정 (리뷰 질문 1~2)**
- AC 1~16 전부 deliverable + named test 로 매핑되었고, orphan AC 도 orphan deliverable 도 없다
  (`PLAN.md:141` 의 자기 주장도 표와 대조해 확인했다). 손이 많이 가는 항목도 실체가 있다:
  AC 11 은 compile 검사 + 자체 lint 두 test 로, AC 13 은 normalized 필드 목록과 comparator
  민감도 검증으로, AC 14 는 validator + `test_validate_skills.py` test 로 분해되어 있다.
  AC 16 만 한 셀이 느슨하다(N-001).
- 사용자 지정 11개 검증 항목이 11개 모두 배정되었고, 각 행에 **어떤 mutation 이 어떤 assertion 을
  깨뜨려야 하는지** 가 적혀 있다(예: “idempotency lookup bypass mutation 시 call count 2로 실패”,
  “`>=`→`>`/guard reorder mutation 검출”). 선언에 그치지 않는다.

**리뷰 질문 3·5~8**
- 질문 3(e2e_harness 처분): **확정했다**(`PLAN.md:40`) — pure logic 추출 + compatibility
  re-export, `run_workflow` 는 test-only parity oracle 로 한 release 존치, production import 금지
  static test, 신규 routing 은 graph spec 에만. DESIGN 회피가 아니다.
- 질문 5(packaging): 위 표대로 실제 구조와 일치하며, ANALYSIS §Risks 의 “설치본에서 engine 이
  사라지는” 시나리오를 required paths + tree parity + archive content test 로 막는다.
- 질문 6(우회 loop 금지): 구조적 장치가 있다 — `W2.4` graph 외부 loop 금지, `migration.py`
  “routing 규칙 없음”, legacy loop production import 금지 static test, Completion Criteria 3번째 항목.
- 질문 7(범위): OS-31 은 port/필드만, OS-37 은 guide/conformance test 만, OS-38 은 미접촉.
  과확대 없음.
- 질문 8(순서): Jira implementation order 1~7 과 W1~W7 이 선행조건과 함께 대응하고
  DESIGN/IMPLEMENTATION/TEST 경계가 행마다 분리되어 있다(`PLAN.md:106-116`). 타당하다.

**ANALYSIS(승인 baseline)와의 정합성**: N-004(인용 범위)·N-005(§Current State 서술)는 PLAN 의
전제에 영향을 주지 않는다. 다만 PLAN 은 ANALYSIS 가 명시한 standard-library-only 전제와
어긋나는 test 의존성을 도입하며, 이는 F-001 에 포함했다.

**decision gate 형식 검증**: `DECISION_GATE_STATE:` 선언 1개, ```` ```decision-gate ```` record
1개, `STATUS:` 1개. record 는 CLEAR 에 허용된 5개 key 만 사용하고 선언과 일치한다. PLAN 은
열린 선택지를 남기지 않고 전부 확정했으므로 `open_decision_item: false` 는 문서 상태와 부합하며,
확정된 항목 중 되돌릴 수 없거나 blast radius/monetary/security/privacy/compliance/lock-in 이
참인 것은 없다. **오분류 없음.**

## Final Decision

이 phase gate 판정은 **FAIL** 이다 — blocking finding 1건(F-001, MAJOR, G1/G2), non-blocking 2건.

FAIL 의 근거는 하나뿐이다. PLAN 의 Validation / Test Plan 이 이 저장소가 쓰지 않는 test runner
위에 세워져 있고, 지정된 회귀 명령은 실제로 실행하면 테스트를 하나도 돌리지 못하며, 설계된
skip 기제는 CI 의 실제 runner 에서 error 가 되고 langgraph 가 설치된 상태에서도 skip 되어
계획 자신의 gate 를 무너뜨린다. AC 16 과 사용자 요구인 “기존 테스트 회귀 없음” 의 증거가
지정된 방식으로는 얻어지지 않으므로 G1/G2 이며, 이 결정은 W1~W7 모든 test 의 작성 방식을
좌우하므로 DESIGN 으로 넘길 수 없다.

이 defect 의 출처가 dispatch 의 ENVIRONMENT FACTS 오기재라는 점은 Summary 에 기록했다.
Coordinator 는 해당 항목을 `python3 -m unittest discover -s scripts -p 'test_*.py'` 로 정정하는
것이 좋다. 그러나 skip 기제 선택과 stdlib-only 전제 위반은 PLAN 자신의 결정이며, 어느 쪽이든
계획이 그대로 실행되면 증거가 비게 된다.

나머지는 좋다. AC 매핑, mutation 감도 설계, work item 분해, 실행 순서, packaging 정합성,
`e2e_harness.py` 처분 확정, 범위 통제는 모두 이 phase 가 요구하는 수준을 충족한다.
**수정 대상은 §Validation / Test Plan 의 runner 와 skip 기제, 그리고 두 lane 의 통과 조건
서술뿐이며, 나머지 절은 손대지 않아도 된다.**

```decision-gate
{
  "state": "CLEAR",
  "reason_code": null,
  "open_decision_item": false,
  "grounds": "This review's verdict follows from explicit OS-40 requirements and the PLAN phase contract applied to directly executed repository evidence; the blocking finding is a mechanical defect I reproduced with the repository's own commands, and its correction is fully determined by existing repository convention, so no item at this boundary needs user authority.",
  "scope": "This phase's own conduct at this iteration."
}
```
