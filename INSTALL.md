# Installation — Orca Worker-Reviewer Loop

이 문서는 `orca-worker-reviewer-loop` Skill의 **설치, 검증, 업데이트, 삭제**만 다룬다.

사용법, phase, parameter, workflow 설명은 [`README.md`](README.md)를 참고한다.

---

## 1. Prerequisites

다음 환경이 준비되어 있어야 한다.

- Orca
- Claude Code 기반 Worker / Reviewer 실행 command
- 해당 command가 PATH에서 실행 가능
- `~/.claude/skills` 사용 가능 환경

기본 Skill 설정은 다음 agent command를 사용한다.

```text
worker=claude-glm
reviewer=claude-gemma
```

---

## 2. Agent Command 확인

PATH에서 command가 확인되는지 검사한다.

```bash
command -v claude-glm
command -v claude-gemma
```

두 command 모두 경로를 반환해야 한다.

실행 권한 확인:

```bash
test -x "$(command -v claude-glm)" && echo "claude-glm OK"
test -x "$(command -v claude-gemma)" && echo "claude-gemma OK"
```

command가 발견되지 않으면 Skill 설치 전에 PATH 설정을 먼저 수정한다.

---

## 3. Package 압축 해제

배포 파일:

```text
orca-worker-reviewer-loop-skill.tar.gz
```

압축 해제:

```bash
tar -xzf orca-worker-reviewer-loop-skill.tar.gz
```

생성되는 구조:

```text
orca-worker-reviewer-loop-skill/
├── README.md
├── INSTALL.md
└── orca-worker-reviewer-loop/
    ├── SKILL.md
    ├── templates/
    └── reviews/
```

---

## 4. 전역 설치 — 권장

```bash
mkdir -p ~/.claude/skills
cp -R \
  orca-worker-reviewer-loop-skill/orca-worker-reviewer-loop \
  ~/.claude/skills/
```

최종 위치:

```text
~/.claude/skills/orca-worker-reviewer-loop/
```

---

## 5. 설치 결과 확인

```bash
find ~/.claude/skills/orca-worker-reviewer-loop \
  -maxdepth 3 \
  -type f \
  | sort
```

다음 파일이 존재해야 한다.

```text
~/.claude/skills/orca-worker-reviewer-loop/SKILL.md
~/.claude/skills/orca-worker-reviewer-loop/templates/analysis.md
~/.claude/skills/orca-worker-reviewer-loop/templates/plan.md
~/.claude/skills/orca-worker-reviewer-loop/templates/design.md
~/.claude/skills/orca-worker-reviewer-loop/templates/implementation.md
~/.claude/skills/orca-worker-reviewer-loop/templates/test.md
~/.claude/skills/orca-worker-reviewer-loop/templates/bugfix.md
~/.claude/skills/orca-worker-reviewer-loop/templates/refactoring.md
~/.claude/skills/orca-worker-reviewer-loop/reviews/common.md
~/.claude/skills/orca-worker-reviewer-loop/reviews/analysis.md
~/.claude/skills/orca-worker-reviewer-loop/reviews/plan.md
~/.claude/skills/orca-worker-reviewer-loop/reviews/design.md
~/.claude/skills/orca-worker-reviewer-loop/reviews/implementation.md
~/.claude/skills/orca-worker-reviewer-loop/reviews/test.md
~/.claude/skills/orca-worker-reviewer-loop/reviews/refactoring.md
```

---

## 6. Claude Code Session 재시작

Skill이 인식되지 않는 경우 현재 Claude Code session을 종료한 후 다시 실행한다.

```bash
claude
```

---

## 7. Help 동작 확인

```text
/orca-worker-reviewer-loop help
```

실제 Worker / Reviewer orchestration이 시작되지 않고 간단한 usage가 출력되어야 한다.

---

## 8. 프로젝트 로컬 설치 — 선택 사항

```bash
mkdir -p .claude/skills
cp -R \
  orca-worker-reviewer-loop-skill/orca-worker-reviewer-loop \
  .claude/skills/
```

최종 위치:

```text
<project-root>/.claude/skills/orca-worker-reviewer-loop/
```

여러 프로젝트에서 공통으로 사용할 목적이면 전역 설치를 권장한다.

---

## 9. 기존 Skill에서 Migration

전역:

```bash
rm -rf ~/.claude/skills/orca-glm-gemma-loop
```

프로젝트 로컬:

```bash
rm -rf .claude/skills/orca-glm-gemma-loop
```

현재 Skill 이름:

```text
orca-worker-reviewer-loop
```

---

## 10. Update

백업이 필요하면:

```bash
mv \
  ~/.claude/skills/orca-worker-reviewer-loop \
  ~/.claude/skills/orca-worker-reviewer-loop.backup
```

새 버전 설치:

```bash
cp -R \
  orca-worker-reviewer-loop-skill/orca-worker-reviewer-loop \
  ~/.claude/skills/
```

확인 후 백업 제거:

```bash
rm -rf ~/.claude/skills/orca-worker-reviewer-loop.backup
```

---

## 11. Uninstall

전역 설치 제거:

```bash
rm -rf ~/.claude/skills/orca-worker-reviewer-loop
```

프로젝트 로컬 설치 제거:

```bash
rm -rf .claude/skills/orca-worker-reviewer-loop
```

---

## 12. Runtime `run/` 디렉터리

Skill 실행 중 `run/` 또는 기타 runtime artifact 디렉터리가 생성될 수 있다.

배포/설치 대상은 다음뿐이다.

```text
SKILL.md
templates/
reviews/
```

따라서 다른 환경으로 Skill을 복사하거나 패키징할 때 `run/`은 포함하지 않는다.

---

## 13. 설치 파일 역할

- `README.md`: 사용자용 개요, 사용법, phase, parameter, 예시
- `INSTALL.md`: 설치, 검증, 업데이트, 삭제
- `orca-worker-reviewer-loop/SKILL.md`: 실제 Coordinator orchestration 실행 명세
- `templates/`: phase별 Worker 지침
- `reviews/`: phase별 Reviewer 정책

---

## 14. 문제 확인

1. 설치 경로 확인

```bash
ls -la ~/.claude/skills/orca-worker-reviewer-loop
```

2. `SKILL.md` 확인

```bash
ls -l ~/.claude/skills/orca-worker-reviewer-loop/SKILL.md
```

3. Claude Code session 재시작

4. agent command 확인

```bash
command -v claude-glm
command -v claude-gemma
```

5. Help 호출

```text
/orca-worker-reviewer-loop help
```

사용법 자체는 `README.md`를 참고한다.
