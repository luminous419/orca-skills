# 전체 워크플로 Best Practice 예제

[English](FULL_WORKFLOW_FAIL_CORRECTION.md)

이 예제를 사용하면 `orca-worker-reviewer-orchestration` Skill의 전체 생명주기를
직접 실행해 볼 수 있습니다.

```text
ANALYSIS → PLAN → DESIGN → IMPLEMENTATION → TEST → Final Review
```

또한 DESIGN에 의도적이고 제한된 결함 하나를 넣어 실제 phase gate 전환을
경험하도록 구성했습니다.

```text
DESIGN 1차 시도 → Reviewer FAIL → Worker 수정 → Reviewer PASS
```

의도적으로 넣은 결함은 orchestration 흐름을 연습하기 위한 장치이며 Reviewer 품질을
측정하기 위한 benchmark가 아닙니다. 모델이 우연히 실수하기를 기다리지 않고 생명주기를
재현할 수 있도록, 이 예제가 검증하려는 동작을 두 역할 모두에게 명시합니다.

## 1. 사전 준비

- Orca와 `orca-worker-reviewer-orchestration` Skill이 설치되어 있어야 합니다.
- 서로 다른 두 개의 허용된 agent command가 `PATH`에서 확인되어야 합니다.
- 실제 작업 프로젝트가 아닌 일회용 repository에서 실행합니다.
- 다섯 단계의 phase gate와 필수 Final Adversarial Review를 완료할 시간을 확보합니다.

아래의 복사·붙여넣기용 호출은 Worker로 `claude`, Reviewer로 `codex`를 사용합니다.
필요하면 두 command token을 환경에 맞게 바꾸세요. 예를 들어 회사 환경에서는
`worker=claude-glm reviewer=claude-gemma`를 사용할 수 있습니다.

## 2. 일회용 repository 생성

```bash
mkdir orca-full-workflow-demo
cd orca-full-workflow-demo
git init
printf '# Orca full-workflow demo\n' > README.md
git add README.md
git commit -m 'Initialize workflow demo'
```

Git 사용자 정보가 설정되어 있지 않다면 commit 전에 이 repository의 local identity를
설정하세요.

## 3. 예제 prompt 실행

아래 block 전체를 Orca Coordinator session에 붙여 넣으세요.

```text
/orca-worker-reviewer-orchestration worker=claude reviewer=codex max-iterations=3 risk=medium phases=analysis,plan,design,implementation,test

이 repository에 Python 표준 라이브러리만 사용하는 작은 retry utility를 만들고,
선언된 순서대로 요청된 모든 단계를 진행해줘. 단계를 건너뛰거나, 합치거나,
순서를 바꾸거나, 추가하지 마.

기능 요구사항:

1. 다음 내용을 포함하는 `retry_demo.py`를 추가해.
   - `class TransientError(Exception)`.
   - `operation`을 인자 없는 callable로 받는
     `run_with_retry(operation, max_attempts)`.
2. `max_attempts`는 `type(max_attempts) is int`이고 값이 1 이상인 경우에만
   유효해. 그 외에는 `operation`을 호출하기 전에 `ValueError`를 발생시켜.
3. 첫 번째 성공 결과를 반환해.
4. `TransientError`만 retry하고, 전체 호출 횟수는 최대 `max_attempts`회로 제한해.
5. 시도 횟수를 모두 소진하면 마지막 `TransientError`를 다시 발생시켜.
6. `TransientError`가 아닌 모든 예외는 retry하지 말고 즉시 전파해.
7. sleep, network, 추가 dependency, 비결정론적인 timing을 사용하지 마.
8. `test_retry_demo.py`에 의미 있는 `unittest` coverage를 추가해. 첫 시도 성공,
   일시적 실패 후 성공, 시도 횟수 소진, 비일시적 예외 전파, 잘못된 값 `0`, `-1`,
   `True`, `1.5`를 모두 포함해.
9. IMPLEMENTATION과 TEST에서 `python3 -m unittest -v`를 실행하고 정확한 결과를
   보고해.

학습용 의도적 review exercise:

1. ANALYSIS와 PLAN은 위의 모든 기능 요구사항을 정확히 보존해야 해.
2. DESIGN Worker의 1차 시도에서만 `max_attempts=0`이 유효하며 `operation`을
   호출하지 않고 `None`을 반환한다고 의도적으로 명시해. Design의 나머지 부분은
   모든 요구사항과 일치시켜.
3. DESIGN Reviewer는 해당 design을 원래 기능 요구사항과 독립적으로 비교해야 해.
   이 학습 지시는 acceptance 예외가 아니야. 해당 모순을 blocking G1 명시적 요구사항
   위반으로 보고하고 `RESULT: FAIL` / `REVIEW_VERDICT: FAIL`을 반환해.
4. DESIGN 수정 시도에서는 의도적 모순을 제거하고,
   `type(max_attempts) is int and max_attempts >= 1`을 요구하며, blocking finding을
   어떻게 해결했는지 기록해. 다음 Reviewer는 해결 설명을 그대로 믿지 말고 수정된
   artifact를 직접 검증해야 해.
5. 다른 의도적 결함은 넣지 마. Reviewer가 추가로 발견한 실제 finding은 정상적인
   bounded correction loop로 처리해.

완료 조건:

- 요청된 모든 단계가 각각의 phase gate에 도달해야 해.
- 적어도 의도적으로 넣은 DESIGN review가 FAIL -> 수정 -> PASS를 따라야 해.
- IMPLEMENTATION과 TEST에 성공한 unit test 근거가 있어야 해.
- 새로운 Final Adversarial Reviewer가 최종 repository 상태를 검증해야 해.
- 최종 결과에 run id와 artifact directory를 보고해.
```

`risk=medium`을 사용하는 이유:

- 요청된 모든 단계에 Worker와 독립 phase Reviewer가 배정됩니다.
- correction loop 동작을 경험할 수 있습니다.
- 필수인 새로운 Final Adversarial Review도 그대로 실행됩니다.
- 이 작은 생명주기 예제에는 HIGH 전용 downstream revalidation이 필요하지 않습니다.

`max-iterations=3`은 실행을 제한된 범위로 유지하면서 의도적 수정 한 번과 추가로 발생할
수 있는 실제 finding 한 번을 처리할 여유를 줍니다.

## 4. 예상되는 최소 실행 흐름

추가적인 실제 FAIL round가 발생해도 괜찮지만, 성공한 예제에는 최소한 다음 흐름이
있어야 합니다.

| 단계 | 예상 gate 흐름 |
| --- | --- |
| ANALYSIS | Worker → Reviewer PASS |
| PLAN | Worker → Reviewer PASS |
| DESIGN | Worker 1차 시도 → Reviewer FAIL → Worker 수정 → Reviewer PASS |
| IMPLEMENTATION | Worker + unit test → Reviewer PASS |
| TEST | Worker + test 근거 → Reviewer PASS |
| Final Review | 새로운 Reviewer PASS |

DESIGN Worker는 `DESIGN.md`를 제자리에서 갱신합니다. 두 번의 review 판단은 서로 다른
근거 파일로 남습니다.

```text
REVIEW_DESIGN.md
REVIEW_DESIGN_iteration2.md
```

## 5. 실행 결과 검증

예제 repository에서 보고된 run directory를 찾고, `orca-skills` checkout 또는 release
archive에 포함된 검증기를 실행하세요.

```bash
python3 /path/to/orca-skills/scripts/verify_full_workflow_example.py \
  artifacts/runs/<run-id>
```

검증기는 다음 사항을 확인합니다.

- 다섯 개의 Worker artifact가 모두 존재합니다.
- 모든 단계가 최종적으로 Reviewer PASS에 도달합니다.
- DESIGN에서 Reviewer FAIL 후 나중에 PASS가 발생합니다.
- Final Adversarial Review가 PASS로 끝납니다.
- review의 `RESULT`와 `REVIEW_VERDICT` 값이 일치합니다.
- `ORCHESTRATOR_LOG.md`에 선언된 risk, 순서대로 실행된 gate, correction round,
  `COMPLETED` run 상태가 기록되어 있습니다.
- `TIMING_LOG.md`가 존재합니다.

성공 시 출력은 다음 문장으로 시작합니다.

```text
PASS: full workflow example completed
```

terminal의 설명만으로 성공을 판단하지 마세요. run-scoped artifact와 append-only
orchestration log가 Skill이 실제 생명주기를 따랐다는 근거입니다.

## 6. 이 예제로 검증할 수 있는 것

- 명시된 단계 순서가 지켜집니다.
- PASS 전에는 phase gate가 다음 단계로의 진행을 막습니다.
- blocking finding이 수정 시도를 발생시킵니다.
- 수정 근거가 독립적으로 review됩니다.
- implementation과 test safety gate가 실행됩니다.
- Final Adversarial Review가 필수로 유지됩니다.
- 전체 생명주기가 지속성 있는 run-scoped 근거로 남습니다.

이 예제는 특정 model 조합이 더 우수하다는 것, 모든 실제 프로젝트가 세 번의 iteration
안에 끝난다는 것, 의도적으로 넣은 finding이 자연스러운 결함 탐지율을 측정한다는 것을
증명하지는 않습니다.
