"""출력 PDF에서 필요한 글자가 추출되는지 본다. 한글 글꼴이 없는 환경에서는 건너뛴다."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pdfplumber
import pytest

from taxreport.analyze.builder import build_report
from taxreport.config.loader import FormsConfig, Policy
from taxreport.domain.models import CompanyCase
from taxreport.render.report_pdf import FontNotFoundError, find_font, render_report_pdf


@pytest.fixture()
def pdf_pages(case: CompanyCase, forms: FormsConfig, policy: Policy, tmp_path: Path) -> list[str]:
    try:
        find_font(policy)
    except FontNotFoundError:
        pytest.skip("한글 글꼴(TTF)이 없는 환경입니다")
    report = build_report(case, forms, policy, issued_on=date(2026, 11, 21))
    path = render_report_pdf(report, policy, tmp_path / "report.pdf")
    with pdfplumber.open(path) as pdf:
        return [page.extract_text() or "" for page in pdf.pages]


def test_required_text_is_extractable(pdf_pages: list[str]) -> None:
    text = "\n".join(pdf_pages)
    required = [
        "법인세 분석 보고서", "(주)테스트상사", "123-45-67890",
        "2024년(전기)", "2025년(당기)", "2024.01.01 ~ 2024.12.31", "2025.01.01 ~ 2025.12.31",
        "매출액", "영업이익", "당기순이익", "부채비율",
        "1.93억원", "4,925만원", "-1,539만원", "51.79%", "자본잠식", "-354.88%", "흑자전환",
        "단위: 1억원 이상은 억원, 1억원 미만은 만원", "단위: 원",
        "192,734,983", "143,166,567", "판매비와관리비 합계", "급여", "기타",
        "요약 코멘트", "세무조정 특이사항", "재무제표 주요 특이사항", "근거 목록", "확인 필요",
        "표준손익계산서", "표준재무상태표", "test_2025.pdf", "test_2024.pdf",
        "자산총계", "부채총계", "자본총계",
    ]
    missing = [item for item in required if item not in text]
    assert not missing


def test_footer_on_every_page(pdf_pages: list[str], policy: Policy) -> None:
    assert len(pdf_pages) >= 2
    for number, text in enumerate(pdf_pages, start=1):
        assert policy.issuer.footer in text, f"{number}쪽에 발행 표기가 없습니다"
        assert "(주)테스트상사" in text
        assert f"{number}쪽" in text


def test_excluded_content_and_forbidden_words(pdf_pages: list[str]) -> None:
    text = "\n".join(pdf_pages)
    for word in ("적자전환", "적자지속", "부가가치세", "원천징수", "대폭"):
        assert word not in text


def test_font_search_fails_loudly(policy: Policy, tmp_path: Path) -> None:
    no_font = policy.model_copy(update={"font_path": None, "font_fallbacks": []})
    with pytest.raises(FontNotFoundError):
        find_font(no_font, base_dir=tmp_path)


def test_font_search_order(policy: Policy, tmp_path: Path) -> None:
    configured = tmp_path / "configured.ttf"
    configured.write_bytes(b"")
    with_path = policy.model_copy(update={"font_path": str(configured), "font_fallbacks": []})
    assert find_font(with_path, base_dir=tmp_path) == configured
    bundled = tmp_path / "assets" / "fonts"
    bundled.mkdir(parents=True)
    (bundled / "a.ttf").write_bytes(b"")
    assert find_font(with_path, base_dir=tmp_path) == bundled / "a.ttf"
