from __future__ import annotations

from datetime import date

from taxreport.analyze.builder import build_report
from taxreport.config.loader import FormsConfig, Policy
from taxreport.domain.models import CompanyCase, Report, ValueStatus
from taxreport.render.formatters import render_sentence

FORBIDDEN = ("적자전환", "적자지속", "대폭", "급증", "급감", "부가가치세", "원천징수")


def _texts(report: Report, policy: Policy) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for block in report.comments:
        result[block.title] = [render_sentence(s, policy) for s in block.sentences]
    for note in (*report.tax_notes, *report.financial_notes):
        result[note.title] = [render_sentence(s, policy) for s in note.sentences]
    return result


def _report(case: CompanyCase, forms: FormsConfig, policy: Policy) -> Report:
    return build_report(case, forms, policy, issued_on=date(2026, 11, 21))


def test_comment_blocks(case: CompanyCase, forms: FormsConfig, policy: Policy) -> None:
    report = _report(case, forms, policy)
    assert [block.title for block in report.comments] == [
        "매출 및 수익성", "판관비 주요 항목", "영업외손익", "전년 대비 주요 변동",
        "재무비율(부채비율)", "공제·감면",
    ]
    texts = _texts(report, policy)
    assert "192,734,983원" in texts["매출 및 수익성"][0]
    assert "107.4%" in texts["매출 및 수익성"][0]
    assert "자본잠식 (-354.88%)" in texts["재무비율(부채비율)"][0]
    assert "51.79%" in texts["재무비율(부채비율)"][0]
    assert any("604,360원" in line for line in texts["공제·감면"])
    for block in report.comments:
        assert 1 <= len(block.sentences) <= 5


def test_tax_notes(case: CompanyCase, forms: FormsConfig, policy: Policy) -> None:
    report = _report(case, forms, policy)
    texts = _texts(report, policy)
    adjustments = texts["소득금액조정"]
    assert "법인세등 2,719,623원" in adjustments[0]
    assert "기타사외유출" in adjustments[0]
    assert adjustments[1] == "2024년은 세무조정 사항이 없습니다."
    loss = next(note for note in report.tax_notes if note.title == "이월결손금")
    assert loss.status is ValueStatus.NEEDS_CHECK
    assert any("129,800원" in line for line in texts["이월결손금"])
    assert any("33,575,594원" in line for line in texts["과세표준과 세액"])
    assert any("결손금 발생액이 서로 다릅니다" in text for text in report.needs_check)


def test_financial_notes(case: CompanyCase, forms: FormsConfig, policy: Policy) -> None:
    report = _report(case, forms, policy)
    texts = _texts(report, policy)
    assert "자본잠식이 해소" in texts["자본잠식"][0]
    assert "8,135,140원" in texts["가수금"][0]
    assert "1,088,470원" in texts["가수금"][0]
    assert "27,109,977원" in texts["외상매출금"][0]
    assert "1곳" in texts["외상매출금"][1]
    assert "가지급금" not in texts  # 해당 계정 잔액이 없으면 문장을 만들지 않는다
    assert "부채비율" in texts


def test_negative_equity_in_current_year(case: CompanyCase, forms: FormsConfig, policy: Policy) -> None:
    case.current.balance_sheet["382"].amount.value = case.prior.balance_sheet["382"].amount.value
    texts = _texts(_report(case, forms, policy), policy)
    assert len(texts["자본잠식"]) == 2
    assert any("정리해야 할 금액은 5,510,353원" in line for line in texts["가수금"])


def test_no_judgement_or_forbidden_words(case: CompanyCase, forms: FormsConfig, policy: Policy) -> None:
    report = _report(case, forms, policy)
    everything = [line for lines in _texts(report, policy).values() for line in lines]
    everything += report.assumptions + report.needs_check + list(policy.templates.values())
    for word in FORBIDDEN:
        assert not any(word in line for line in everything), word


def test_evidence_rows_have_sources(case: CompanyCase, forms: FormsConfig, policy: Policy) -> None:
    report = _report(case, forms, policy)
    by_key = {(row.item, row.year): row for row in report.evidence}
    revenue = by_key[("매출액", 2025)]
    assert (revenue.file, revenue.form, revenue.page, revenue.code) == (
        "test_2025.pdf", "표준손익계산서", 10, "1",
    )
    assert by_key[("부채비율", 2024)].status is ValueStatus.ASSUMED
    assert {row.year for row in report.evidence} == {2024, 2025}
