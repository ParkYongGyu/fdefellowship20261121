---
name: claude_harness_python
description: |
  OpenAI "Harness Engineering" 원칙을 Claude Code에 적용한 agent-first 개발 워크플로우 (Python 프로젝트 버전).
  5단계(Clarify→Context→Plan→Generate→Evaluate)를 sub-agent로 순차 실행하며,
  각 단계 결과를 .harness/ artifact 파일로 저장하고 GitHub PR까지 자동 처리한다.

  핵심 철학 (출처: openai.com/index/harness-engineering/):
  - "Give agents a map, not a 1,000-page manual" → AGENTS.md는 ~100줄 table of contents
  - "Diagnose before retrying" → 실패 시 누락된 capability를 찾아 보강
  - "Enforce invariants, not implementations" → 아키텍처 규칙은 linter/CI로 강제
  - "Repository is the system of record" → 모든 context는 repo-local 파일로 관리
  - "Progressive disclosure" → agent는 entry point → docs/ 순으로 탐색

allowed-tools: Agent, Bash, Read, Edit, Write, TodoWrite
argument-hint: "<구현 요청을 자연어로 입력>"
user-invocable: true
---

당신은 **Claude Code Harness 오케스트레이터**입니다.
OpenAI의 "Harness Engineering" 원칙에 따라, 각 Phase를 독립 sub-agent에게 위임하고
결과를 `.harness/` 디렉토리의 artifact 파일로 저장합니다.

> **핵심 원칙**: 에이전트가 막혔을 때 "더 세게 시도"하지 마세요.
> 누락된 capability를 진단하고, 그것을 repo에 인코딩하세요.

사용자 요청: $ARGUMENTS

---

## 시작 전 준비

```bash
mkdir -p .harness
```

AGENTS.md가 없으면 생성하세요 (table of contents 형식, 100줄 이내):

```bash
test -f AGENTS.md || cat > AGENTS.md << 'EOF'
# AGENTS.md — Repository Map

> This file is the entry point for all coding agents.
> Keep it under 100 lines. Point to docs/ for details.

## Quick Start
- Tech stack: [프로젝트 기술 스택]
- Entry point: [메인 파일 경로]
- Run tests: [테스트 커맨드]
- Lint: [린트 커맨드]

## Repository Structure
See ARCHITECTURE.md for domain/layer map.

## Key Docs
- docs/DESIGN.md — Core design decisions
- docs/QUALITY_SCORE.md — Quality grades per domain
- docs/PLANS.md — Active execution plans

## Architecture Invariants (enforced by CI)
- [레이어 의존성 방향]
- [금지된 패턴]

## Agent Operating Principles
1. Parse, don't validate — validate data shapes at boundaries
2. Prefer shared utilities over hand-rolled helpers
3. Structured logging only — no ad-hoc print/console.log
4. Tests are required for all new public interfaces
5. Update docs/ when behavior changes
EOF
```

---

## Phase 0: Execution Plan 생성 & 브랜치 준비

오케스트레이터가 **직접** 실행하세요.

### 0-1. 작업 규모 판단

요청을 읽고 다음 기준으로 분류하세요:

| 규모 | 기준 | Plan 위치 |
|------|------|-----------|
| **S** (소규모) | 단일 파일, 명확한 범위, 1~2시간 이내 | `.harness/plan.md` (경량) |
| **M** (중규모) | 복수 도메인, 설계 결정 필요, 반나절~하루 | `docs/exec-plans/active/YYYYMMDD-<slug>.md` |
| **L** (대규모) | 아키텍처 변경, 여러 PR 예상, 며칠 이상 | `docs/exec-plans/active/YYYYMMDD-<slug>.md` + sub-plan 분기 |

### 0-2. Execution Plan 생성

**S 규모 — 경량 plan** (`.harness/plan.md`):

```markdown
---
size: S
request: "<요청 1줄 요약>"
created: <ISO 날짜>
---

## 목표
<1~2문장>

## 변경 파일
- `path/to/file.py` — 이유

## 완료 기준
- [ ] <체크 1>
- [ ] <체크 2>
```

**M/L 규모 — Full Execution Plan** (`docs/exec-plans/active/YYYYMMDD-<slug>.md`):

```markdown
---
title: "<작업 제목>"
slug: YYYYMMDD-<kebab-case-title>
size: M | L
status: active          # active | blocked | completed | cancelled
created: <ISO 날짜>
updated: <ISO 날짜>
branch: harness/YYYYMMDD-<slug>
pr: ""                  # PR URL — PR 생성 후 채움
---

## 목표 (Goal)
<무엇을 달성하려는가 — 2~4문장>

## 배경 (Background)
<왜 이 작업이 필요한가, 관련 이슈/요청 링크>

## 접근법 (Approach)
<어떻게 달성할 것인가 — 단계별 서술>

## 범위 (Scope)
**포함:**
- ...

**제외 (명시적 out-of-scope):**
- ...

## 체크포인트 (Checkpoints)
<!-- 오케스트레이터가 각 Phase 완료 시 [x]로 업데이트 -->
- [ ] Phase 1 Clarify — 모호한 점 해소
- [ ] Phase 2 Context — 관련 파일 및 패턴 파악
- [ ] Phase 3 Plan — run_phases.py 생성, 사용자 승인
- [ ] Phase 4 Generate — 코드 생성 완료
- [ ] Phase 5 Evaluate — 검증 통과
- [ ] Phase 6 PR — PR 생성 및 링크 기록

## 설계 결정 로그 (Decision Log)
<!-- Phase 진행 중 중요한 결정이 있을 때 추가 -->
| 날짜 | 결정 | 이유 | 대안 |
|------|------|------|------|
|      |      |      |      |

## 위험 요소 (Risks)
| 위험 | 가능성 | 영향 | 완화 전략 |
|------|--------|------|-----------|
|      |        |      |           |

## 블록 로그 (Block Log)
<!-- status가 blocked일 때 기록 -->
| 날짜 | 블록 사유 | 필요한 것 | 해소 날짜 |
|------|-----------|-----------|-----------|

## 기술 부채 (Tech Debt Discovered)
<!-- Phase 진행 중 발견한 부채 — Phase 5 완료 후 tech-debt-tracker.md에 이관 -->
- ...

## 완료 노트 (Completion Notes)
<!-- Phase 6 완료 후 작성 -->
- 실제 소요 시간:
- 예상과 달랐던 점:
- 다음 작업에 반영할 교훈:
```

### 0-3. Git 연결 확인 및 브랜치 생성

```bash
# Git 상태 확인
git status && git remote -v
git branch --show-current && git diff --stat
git fetch origin

# 브랜치 생성 (규칙: harness/YYYYMMDD-<kebab-slug>)
BRANCH_NAME="harness/$(date +%Y%m%d)-<요청-키워드>"
echo "$BRANCH_NAME" > .harness/branch_name.txt
git checkout -b "$BRANCH_NAME"

# M/L 규모면 plan 파일의 branch 필드 업데이트
sed -i "s/^branch: .*/branch: $BRANCH_NAME/" docs/exec-plans/active/*.md 2>/dev/null || true
```

사용자에게 알리세요: `"작업 브랜치 <브랜치명> 생성, Execution Plan: <plan 경로>"`

---

## Phase 1: Clarify

다음 프롬프트로 **Explore sub-agent**를 실행하세요:

```
사용자 요청: "$ARGUMENTS"

[임무]
요청을 분석하여 구현 전 명확히 해야 할 모호한 점을 파악하세요.
결과를 .harness/phase1_clarify.md로 저장하세요.

[저장 형식 — .harness/phase1_clarify.md]
---
phase: 1-clarify
request: |
  $ARGUMENTS
status: SKIP | QUESTIONS
questions:
  - (질문이 있을 경우만)
---

검토 항목:
- 기능 범위: 포함/제외 기준이 명확한가?
- 입출력: 받는 데이터와 결과물이 명확한가?
- 아키텍처 제약: 기존 레이어 의존성 규칙과 충돌하는가?
- 완료 기준: 언제 완성인지 명확한가?
- Agent legibility: agent가 repo만 보고 컨텍스트를 파악할 수 있는가?

질문 없으면 SKIP, 있으면 QUESTIONS.
저장 후 "artifact: .harness/phase1_clarify.md 저장 완료" 출력.
```

**오케스트레이터 처리:**
- `SKIP` → Phase 2 즉시 실행
- `QUESTIONS` → 질문을 사용자에게 보여주고 답변 대기 후 Phase 2 실행
- **Execution Plan 업데이트**: Checkpoints의 `Phase 1 Clarify` 항목을 `[x]`로 표시
  ```bash
  # M/L 규모인 경우
  sed -i 's/- \[ \] Phase 1 Clarify/- [x] Phase 1 Clarify/' docs/exec-plans/active/<plan 파일>
  ```

---

## Phase 2: Context

다음 프롬프트로 **Explore sub-agent**를 실행하세요:

```
사용자 요청: "$ARGUMENTS"
Phase 1 artifact: .harness/phase1_clarify.md

[임무]
phase1 artifact를 읽고, 프로젝트를 탐색하여 구현에 필요한 컨텍스트를 수집하세요.
결과를 .harness/phase2_context.md로 저장하세요.

[탐색 순서 — OpenAI "progressive disclosure" 원칙]
1. AGENTS.md 읽기 (entry point — table of contents)
2. AGENTS.md가 가리키는 docs/ 파일들 읽기
3. find . -type f -name "*.py" | grep -v ".venv\|node_modules" | head -30
4. 설정 파일 읽기 (pyproject.toml / package.json 등)
5. 요청과 관련된 도메인 파일들 읽기

[저장 형식 — .harness/phase2_context.md]
---
phase: 2-context
request: |
  $ARGUMENTS
---

## 프로젝트 구조
(find 결과 요약 + 도메인/레이어 구조)

## 아키텍처 불변량
(CI로 강제되는 규칙 목록)

## 관련 파일 목록
(수정 필요 파일 + 이유)

## 코드 패턴
(import 방식, 에러 처리, 명명 규칙, 로깅 패턴)

## Agent Legibility 평가
(agent가 repo만 보고 파악 가능한 컨텍스트 vs 외부에 있는 컨텍스트)

## 주의사항
(의존성 충돌, 아키텍처 위반 가능성, stale 문서 등)

## 핵심 코드 스니펫
(각 관련 파일의 핵심 부분 발췌)

저장 후 "artifact: .harness/phase2_context.md 저장 완료" 출력.
```

**오케스트레이터 처리:**
- context 요약을 사용자에게 보여주기
- Agent Legibility 이슈가 있으면 사용자에게 알리기
- **Execution Plan 업데이트**: `Phase 2 Context` 체크 + 발견한 위험을 Risks 표에 추가
  ```bash
  sed -i 's/- \[ \] Phase 2 Context/- [x] Phase 2 Context/' docs/exec-plans/active/<plan 파일>
  ```

---

## Phase 3: Plan

다음 프롬프트로 **general-purpose sub-agent**를 실행하세요:

```
Phase 2 artifact: .harness/phase2_context.md

[임무]
phase2 artifact를 읽고, run_phases.py를 현재 작업 디렉토리에 생성하세요.
계획을 .harness/phase3_plan.md로 저장하세요.

[run_phases.py 작성 규칙]
- 각 phase_N_[단계명]() 함수는 실제 파일을 생성/수정합니다
- Path.write_text() 또는 Path.read_text() + 문자열 치환 사용
- 각 함수는 독립 실행 가능해야 합니다
- run_all()에서 모든 phase를 순서대로 실행합니다
- 각 함수 완료 시 "✅ [설명] 완료" print

[아키텍처 검증 체크리스트 — 각 변경이 다음을 만족하는지 확인]
- [ ] 레이어 의존성 방향 준수
- [ ] 데이터 경계에서 파싱 (parse, don't validate)
- [ ] 공유 utility 패키지 활용 (hand-rolled 지양)
- [ ] structured logging 사용
- [ ] 새 public interface에 테스트 포함

[저장 형식 — .harness/phase3_plan.md]
---
phase: 3-plan
---

## 구현 계획

| Phase | 함수명 | 대상 파일 | 변경 내용 |
|-------|--------|----------|----------|
| 1     | phase_1_xxx | path/to/file | 설명 |

## 아키텍처 영향 분석
(새로운 의존성, 레이어 변경, cross-cutting concern 여부)

## 필요한 docs/ 업데이트
(behavior 변경 시 업데이트할 문서 목록)

## run_phases.py 생성 완료 여부

## 예상 위험 요소

저장 후 "artifact: .harness/phase3_plan.md 저장 완료" 출력.
```

**오케스트레이터 처리:**
- 구현 계획을 사용자에게 표시
- **"이 계획으로 진행할까요?"** 확인 후 Phase 4 실행
- **Execution Plan 업데이트** (사용자 승인 후):
  - `Phase 3 Plan` 체크
  - 중요한 설계 결정이 있으면 Decision Log에 행 추가
  ```bash
  sed -i 's/- \[ \] Phase 3 Plan/- [x] Phase 3 Plan/' docs/exec-plans/active/<plan 파일>
  ```
  - `updated` 필드를 현재 날짜로 갱신
- **블록 발생 시**: plan의 `status: blocked` 변경 + Block Log에 사유 기록

---

## Phase 4: Generate

다음 프롬프트로 **general-purpose sub-agent**를 실행하세요:

```
Phase 3 artifact: .harness/phase3_plan.md

[임무]
phase3 artifact를 읽고, run_phases.py를 실행하여 코드를 생성하세요.
결과를 .harness/phase4_generate.md로 저장하세요.

[실행 순서]
1. .harness/phase3_plan.md 읽기
2. uv run python run_phases.py (uv 없으면 python run_phases.py)
3. 오류 발생 시 run_phases.py 수정 후 재실행 (최대 3회)
4. 생성/수정된 각 파일의 핵심 내용 확인

[실패 처리 — "Diagnose before retrying" 원칙]
오류 발생 시 다음 순서로 진단:
1. 누락된 capability 파악 (missing tool, abstraction, context)
2. 해당 capability를 repo에 추가할 수 있는지 확인
3. 추가 가능하면 추가 후 재시도
4. 3회 실패 시 FAILED로 기록하고 누락 capability 상세 보고

[저장 형식 — .harness/phase4_generate.md]
---
phase: 4-generate
status: SUCCESS | PARTIAL | FAILED
---

## 실행 결과
(run_phases.py 실행 로그 요약)

## 생성/수정된 파일
- path/to/file1: 변경 내용 요약

## 발생한 오류 및 수정 내용
(없으면 "없음")

## 누락 Capability (실패 시)
(어떤 capability가 없어서 실패했는지 명확히 기술)

## 검증 대상 파일 목록
validate_files: file1.py file2.py ...

저장 후 "artifact: .harness/phase4_generate.md 저장 완료" 출력.
```

**오케스트레이터 처리:**
- `FAILED` → 누락 capability 사용자에게 보고하고 중단
  - Execution Plan: `status: blocked` + Block Log 기록
- 그 외 → Phase 5 실행
- **Execution Plan 업데이트**: `Phase 4 Generate` 체크
  ```bash
  sed -i 's/- \[ \] Phase 4 Generate/- [x] Phase 4 Generate/' docs/exec-plans/active/<plan 파일>
  ```

---

## Phase 5: Evaluate

다음 프롬프트로 **general-purpose sub-agent**를 실행하세요:

```
Phase 4 artifact: .harness/phase4_generate.md

[임무]
phase4 artifact에서 validate_files 목록을 확인하고 코드 품질을 검증하세요.
발견된 문제는 즉시 수정하세요.
결과를 .harness/phase5_evaluate.md로 저장하세요.

[검증 순서]
1. validate_files 목록 추출
2. 문법 검사: uv run python -m py_compile [파일들] && echo "PASS" || echo "FAIL"
3. 린팅: uv run ruff check [파일들] || uv run flake8 [파일들] --max-line-length=100 || echo "린터 없음"
4. Type checking: uv run mypy [파일들] --strict 2>/dev/null || echo "mypy 없음"
5. 테스트: uv run pytest -x -q 2>/dev/null || echo "테스트 없음"
6. import 검증: uv run python -c "import <메인 모듈>" 2>/dev/null || echo "건너뜀"

[아키텍처 불변량 검증]
7. 레이어 의존성 검사: 커스텀 linter 실행 (있는 경우)
8. 금지 패턴 검사: grep -rn "print(" [파일들] (structured logging 위반 여부)
9. 문제 발견 시 해당 파일 수정 후 재검증

[docs/ 업데이트 확인]
10. phase3_plan.md에서 업데이트 필요한 문서 목록 확인
11. behavior 변경이 있으면 해당 docs/ 파일 업데이트

[저장 형식 — .harness/phase5_evaluate.md]
---
phase: 5-evaluate
overall: PASS | PASS_WITH_WARNINGS | FAIL
---

## 검증 결과
✓/✗ 문법 검사: ...
✓/✗ 린팅 (ruff): ...
✓/✗ Type checking (mypy): ...
✓/✗ 테스트 (pytest): ...
✓/✗ Import 검증: ...
✓/✗ 레이어 의존성: ...
✓/✗ Logging 패턴: ...

## 수정된 문제
(없으면 "없음")

## 업데이트된 문서
(docs/ 변경 사항)

## 최종 생성 파일
- ...

## 다음 단계 제안
- 수동 확인 필요 항목: ...
- docs/ 추가 업데이트 권장: ...
- 기술 부채 등록 필요: ...

저장 후 "artifact: .harness/phase5_evaluate.md 저장 완료" 출력.
```

**오케스트레이터 처리:**
- 검증 결과를 사용자에게 보여주기
- **Execution Plan 업데이트**: `Phase 5 Evaluate` 체크
  ```bash
  sed -i 's/- \[ \] Phase 5 Evaluate/- [x] Phase 5 Evaluate/' docs/exec-plans/active/<plan 파일>
  ```
- Phase 5 artifact의 "기술 부채 등록 필요" 항목을 `docs/exec-plans/tech-debt-tracker.md`에 추가
- Artifact 파일 경로 안내

---

## Phase 6: Git Commit & PR 생성

Phase 5 결과가 `PASS` 또는 `PASS_WITH_WARNINGS`이면 **오케스트레이터가 직접** 실행하세요:

```bash
# 1. 변경 파일 확인
git status && git diff --stat

# 2. 스테이징 (.harness/, run_phases.py 제외, docs/ 포함)
git add <변경된 소스 파일들>
git add docs/  # docs 업데이트 포함

# 3. 커밋
git commit -m "$(cat <<'EOF'
<커밋 메시지 — 사용자 요청 핵심 내용 1줄 요약>

- <변경 내용 bullet 1>
- <변경 내용 bullet 2>

Harness: Claude Code
Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
EOF
)"

# 4. 푸시
git push -u origin <브랜치명>

# 5. PR 생성
PR_URL=$(gh pr create \
  --title "<PR 제목>" \
  --body "$(cat <<'EOF'
## Summary
<변경 내용 요약>

## Changes
<변경된 파일 및 내용 bullet list>

## Architecture Impact
- Layer dependency changes: <none/describe>
- New cross-cutting concerns: <none/describe>
- Docs updated: <list>

## Execution Plan
<plan 파일 경로> (repo 내 링크)

## Test Plan
- [ ] 문법 검사 통과
- [ ] 린팅 (ruff) 통과
- [ ] Type checking (mypy) 통과
- [ ] 수동 동작 확인 필요 항목

## Artifacts
이 PR은 Claude Code Harness 워크플로우로 생성되었습니다.

🤖 Generated with [Claude Code](https://claude.ai/code)
EOF
)" --print-url)

echo "PR: $PR_URL"
```

**Execution Plan 완료 처리** (PR 생성 후):

```bash
PLAN_FILE=$(ls docs/exec-plans/active/*.md 2>/dev/null | head -1)

if [ -n "$PLAN_FILE" ]; then
  # 1. PR URL 기록
  sed -i "s|^pr: \"\"|pr: \"$PR_URL\"|" "$PLAN_FILE"

  # 2. Phase 6 체크
  sed -i 's/- \[ \] Phase 6 PR/- [x] Phase 6 PR/' "$PLAN_FILE"

  # 3. Completion Notes 작성
  cat >> "$PLAN_FILE" << EOF

## Completion Notes
- 완료 시각: $(date -u +%Y-%m-%dT%H:%M:%SZ)
- PR: $PR_URL
- 실제 소요: <작성>
- 예상과 달랐던 점: <작성>
- 다음 작업 교훈: <작성>
EOF

  # 4. status: completed 로 변경
  sed -i 's/^status: active/status: completed/' "$PLAN_FILE"

  # 5. active → completed 이동
  COMPLETED_FILE="docs/exec-plans/completed/$(basename $PLAN_FILE)"
  mv "$PLAN_FILE" "$COMPLETED_FILE"

  # 6. tech-debt-tracker.md에 부채 이관 (있는 경우)
  # Phase 5 artifact의 "기술 부채 등록 필요" 섹션 내용을 수동 확인

  git add docs/exec-plans/
  git commit -m "chore: close execution plan $(basename $PLAN_FILE .md)"
  git push
fi
```

---

## 운영 원칙

### Execution Plan 라이프사이클 요약

```
[요청 수신]
    │
    ▼
Phase 0: Plan 생성 (S/M/L 판단)
    │
    ▼
Phase 1~5: 각 완료 시 Checkpoints [x] 체크 + updated 갱신
    │
    ├─ 블록 발생 → status: blocked + Block Log 기록
    │              → 해소 후 status: active 복원
    │
    ▼
Phase 6: PR 생성 → pr 필드 기록 → status: completed
    │
    ▼
active/ → completed/ 이동 + git commit
```

### tech-debt-tracker.md 형식

`docs/exec-plans/tech-debt-tracker.md` 에 누적 관리:

```markdown
# Tech Debt Tracker

## 미해결 (Open)

| ID | 발견일 | 출처 Plan | 내용 | 우선순위 | 담당 |
|----|--------|-----------|------|----------|------|
| TD-001 | 2026-05-10 | 20260510-auth-layer | 로그인 로직 중복 — 공유 util로 추출 필요 | High | - |

## 해결됨 (Resolved)

| ID | 해결일 | PR | 내용 |
|----|--------|----|------|
```

### Agent Legibility 관리
에이전트가 막힐 때마다 다음을 확인하세요:
- 해당 컨텍스트가 repo에 인코딩되어 있는가?
- AGENTS.md → docs/ 경로로 발견 가능한가?
- 아키텍처 불변량이 linter로 강제되는가?

### 문서 가비지 컬렉션
주기적으로 실행:
```bash
# stale 문서 탐지 (최근 30일간 코드 변경 있었지만 docs 미업데이트)
git log --since="30 days ago" --name-only -- "*.py" | sort -u | \
  xargs -I{} sh -c 'echo "Changed: {}"'
```

### 골든 원칙 (docs/QUALITY_SCORE.md에 기록)
1. 공유 utility 패키지 > hand-rolled helper
2. 경계에서 파싱 (Zod/Pydantic), 내부에서는 타입만
3. Structured logging only
4. 100% test coverage for shared utilities
5. AGENTS.md는 항상 100줄 이하 유지
