# AGENTS.md — Repository Map

> 모든 코딩 에이전트의 진입점이다. 100줄 이내로 유지하고, 자세한 내용은 아래 문서를 가리킨다.

## 무엇을 만드는가

같은 회사의 전기·당기 세무조정계산서 PDF 2개 → 법인세 분석 보고서(PDF·Excel) + 조정료청구서.
고객은 샘플세무법인, 문서와 대화는 한국어.

## Quick Start

- Tech stack: Python ≥ 3.12, uv, pdfplumber, pydantic v2, reportlab, matplotlib, openpyxl, structlog
- Entry point: `src/taxreport/cli.py`
- Run: `uv run taxreport build <PDF1> <PDF2> --out output/`
- Run tests: `uv run pytest -q` (단일 테스트: `uv run pytest tests/test_x.py::test_name`)
- Lint: `uv run ruff check .`
- Type check: `uv run mypy src --strict`

코드는 아직 없다. 구현 순서는 `docs/TRD.md` 12장을 따른다.

## Key Docs

- `CLAUDE.md` — 실습 규칙, 고객이 확정한 기준, PDF 구조와 추출 시 주의점, 검증용 기준값
- `docs/TRD.md` — 설계: 범위, 가정, 아키텍처, 데이터 모델, 서식별 추출 규칙, 보고서 규칙
- `docs/exec-plans/active/` — 진행 중인 실행 계획
- `claudedocs/1차_질문지.md` — 고객 질의·답변 원문 (답변은 지우지 않고 누적)

## Repository Structure

```
src/taxreport/
  cli.py        진입점
  config/       policy.yaml(정책·문구), forms.yaml(서식 인식 규칙)
  render/       보고서 PDF·Excel, 조정료청구서, 그래프, 표기 형식
  analyze/      지표, 비교, 판관비, 특이사항, 코멘트
  extract/      PDF 읽기, 접수 검증, 서식 색인, 서식별 추출
  domain/       모델
tests/
```

## Architecture Invariants (tests/test_architecture.py로 강제)

- 의존 방향: `cli → render → analyze → extract → domain`. 역방향 import 금지.
- `domain`은 파일·PDF·설정을 읽지 않는다.
- `render`는 값을 계산하지 않는다. 표기 형식만 적용한다.
- 모든 숫자는 `SourcedValue`(값 + 상태 + 출처)로 다닌다.
- `print()`는 `cli.py`의 최종 결과 출력에만 쓴다. 그 외에는 structlog.

## Agent Operating Principles

1. 회사명, 페이지 번호, 계정 목록을 코드에 박지 않는다. PDF에서 읽거나 설정 파일에 둔다.
2. 서식은 페이지 번호가 아니라 제목으로 찾는다.
3. 재무 숫자는 표준재무상태표·표준손익계산서에서만 읽는다.
4. 고객이 정하지 않은 기준은 `policy.yaml`의 값으로 두고 보고서에 가정임을 적는다.
5. 값을 읽지 못하면 추측하지 말고 «확인 필요»로 둔다.
6. 경계에서 파싱한다(pydantic). 새 public 인터페이스에는 테스트를 붙인다.
7. 동작이 바뀌면 `docs/TRD.md`를 함께 고친다.

## 저장소에 올리지 않는 것

고객에게 받은 Excel·PDF·안내 자료, `output/`, `.harness/`, `run_phases.py`, 글꼴 파일. `.gitignore` 참고.
