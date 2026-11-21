from __future__ import annotations

import json
from pathlib import Path

import pytest

from taxreport import cli
from taxreport.config.loader import FormsConfig, Policy
from taxreport.domain.models import CompanyCase
from taxreport.extract.intake import IntakeError
from taxreport.render.report_pdf import FontNotFoundError, find_font


def test_build_writes_pdf_and_json(
    case: CompanyCase,
    policy: Policy,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    try:
        find_font(policy)
    except FontNotFoundError:
        pytest.skip("한글 글꼴(TTF)이 없는 환경입니다")

    def fake_load_case(first: Path, second: Path, forms: FormsConfig) -> CompanyCase:
        return case

    monkeypatch.setattr(cli, "load_case", fake_load_case)
    code = cli.main(["build", "a.pdf", "b.pdf", "--out", str(tmp_path)])
    assert code == 0
    pdf = tmp_path / "(주)테스트상사_2025_법인세분석보고서.pdf"
    extracted = tmp_path / "(주)테스트상사_2025_추출결과.json"
    assert pdf.is_file() and pdf.stat().st_size > 10_000
    data = json.loads(extracted.read_text(encoding="utf-8"))
    assert data["current"]["identity"]["fiscal_year"] == 2025
    assert data["current"]["income_statement"]["1"]["amount"]["source"]["page"] == 10
    out = capsys.readouterr().out
    assert "확인 필요: 2건" in out
    assert "가정:" in out


def test_intake_failure_returns_nonzero(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    def failing(first: Path, second: Path, forms: FormsConfig) -> CompanyCase:
        raise IntakeError("두 파일의 사업연도가 같습니다")

    monkeypatch.setattr(cli, "load_case", failing)
    code = cli.main(["build", "a.pdf", "a.pdf", "--out", str(tmp_path)])
    assert code == cli.EXIT_INTAKE != 0
    assert "접수 오류" in capsys.readouterr().err
    assert list(tmp_path.iterdir()) == []


def test_missing_pdf_returns_nonzero(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code = cli.main(["inspect", str(tmp_path / "없는파일.pdf")])
    assert code != 0
    assert "찾지 못했습니다" in capsys.readouterr().err
