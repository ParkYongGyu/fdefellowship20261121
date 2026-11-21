"""샘플컴퍼니 PDF에서 읽은 값이 기준값·출처 쪽과 맞는지 본다. PDF가 없으면 건너뛴다."""

from __future__ import annotations

from decimal import Decimal

import pytest

from taxreport.config.loader import FormsConfig, Policy
from taxreport.domain.models import CompanyCase, Filing
from taxreport.extract.intake import IntakeError, pair_filings

# (서식, 코드) → {연도: (값, 쪽)}
BASELINE: dict[tuple[str, str], dict[int, tuple[int, int]]] = {
    ("income", "1"): {2024: (92_944_479, 30), 2025: (192_734_983, 41)},
    ("income", "67"): {2024: (108_330_197, 30), 2025: (143_166_567, 41)},
    ("income", "129"): {2024: (-15_385_718, 31), 2025: (49_254_390, 42)},
    ("income", "219"): {2024: (-15_510_353, 31), 2025: (46_496_124, 42)},
    ("balance", "228"): {2024: (14_044_981, 22), 2025: (62_210_447, 33)},
    ("balance", "333"): {2024: (19_555_334, 23), 2025: (21_224_676, 34)},
    ("balance", "382"): {2024: (-5_510_353, 23), 2025: (40_985_771, 34)},
}
PAGE_RANGES = {
    ("income", 2024): range(30, 32),
    ("income", 2025): range(41, 43),
    ("balance", 2024): range(21, 24),
    ("balance", 2025): range(32, 35),
}


@pytest.mark.parametrize("key", list(BASELINE))
def test_baseline_values_and_sources(sample_case: CompanyCase, key: tuple[str, str]) -> None:
    statement, code = key
    for filing in (sample_case.prior, sample_case.current):
        year = filing.identity.fiscal_year
        items = filing.income_statement if statement == "income" else filing.balance_sheet
        expected_value, expected_page = BASELINE[key][year]
        item = items[code]
        assert item.amount.value == Decimal(expected_value)
        source = item.amount.source
        assert source is not None
        assert source.page == expected_page
        assert source.page in PAGE_RANGES[(statement, year)]
        assert source.file == filing.source_file
        assert source.form == ("표준손익계산서" if statement == "income" else "표준재무상태표")
        assert source.code == code


def test_identity_is_read_from_pdf_body(sample_case: CompanyCase) -> None:
    assert sample_case.prior.identity.fiscal_year == 2024
    assert sample_case.current.identity.fiscal_year == 2025
    for filing in (sample_case.prior, sample_case.current):
        assert filing.identity.name == "(주)샘플컴퍼니"
        assert filing.identity.business_no == "999-88-00000"


def test_same_pdf_twice_and_swapped_order(sample_filings: tuple[Filing, Filing]) -> None:
    prior, current = sample_filings
    with pytest.raises(IntakeError):
        pair_filings(current, current)
    assert pair_filings(prior, current).current.identity.fiscal_year == 2025
    assert pair_filings(current, prior).current.identity.fiscal_year == 2025


def test_form_index_finds_forms_by_title(sample_case: CompanyCase) -> None:
    current = {ref.name: ref.pages for ref in sample_case.current.forms_found}
    prior = {ref.name: ref.pages for ref in sample_case.prior.forms_found}
    assert current["표준재무상태표"] == [32, 33, 34]
    assert current["표준손익계산서"] == [41, 42]
    assert prior["표준재무상태표"] == [21, 22, 23]
    assert prior["표준손익계산서"] == [30, 31]
    assert "소득금액조정합계표" in current
    assert "소득금액조정합계표" not in prior
    assert "최저한세조정계산서" not in prior


def test_sga_details_2025(sample_case: CompanyCase) -> None:
    items = sample_case.current.income_statement
    expected = {
        "68": 50_319_710, "78": 2_021_410, "79": 19_528_517, "80": 18_311_175, "81": 439_727,
        "85": 951_981, "90": 1_558_580, "104": 3_463_979, "108": 23_996_690, "109": 337_628,
        "114": 693_690, "116": 92_450, "124": 21_451_030,
    }
    for code, amount in expected.items():
        assert items[code].amount.value == Decimal(amount)
        assert items[code].parent_code == "67"
    assert items["70"].parent_code == "68"
    assert sum(expected.values()) == 143_166_567


def test_tax_forms_2025(sample_case: CompanyCase) -> None:
    tax = sample_case.current.tax
    expected = {
        "net_income": 46_496_124,
        "income_addition": 2_719_623,
        "taxable_income": 49_215_747,
        "loss_carryforward_used": 15_640_153,
        "tax_base": 33_575_594,
        "tax_rate": 9,
        "calculated_tax": 3_021_803,
        "credits_subject_to_minimum_tax": 604_360,
        "total_tax": 2_417_443,
        "minimum_tax": 2_350_291,
        "tax_after_credits": 2_417_443,
    }
    for key, amount in expected.items():
        assert tax.get(key).value == Decimal(amount), key
    adjustments = sample_case.current.adjustments
    assert [(a.subject, a.amount.value, a.disposition) for a in adjustments] == [
        ("법인세등", Decimal(2_719_623), "기타사외유출")
    ]
    credits = sample_case.current.credits
    assert [(c.name, c.legal_basis, c.amount.value) for c in credits] == [
        ("중소기업에대한특별세액감면", "조세특례제한법제7조", Decimal(604_360))
    ]


def test_tax_forms_2024(sample_case: CompanyCase) -> None:
    prior = sample_case.prior
    assert prior.tax.get("tax_base").value == Decimal(-15_510_353)
    assert prior.tax.get("calculated_tax").value == Decimal(0)
    assert prior.adjustments == []
    assert prior.credits == []
    assert prior.losses[0].incurred.value == Decimal(15_640_153)
    assert prior.losses[0].balance is not None
    assert prior.losses[0].balance.value == Decimal(15_640_153)


def test_schedule_balances_use_ending_column(sample_case: CompanyCase, forms: FormsConfig) -> None:
    """2025 외상매출금명세서에는 증가·감소만 있고 잔액이 빈 거래처가 있다. 잔액 열만 읽어야 한다."""
    for filing, receivable, borrowing in (
        (sample_case.prior, 7_324_268, 8_135_140),
        (sample_case.current, 27_109_977, 1_088_470),
    ):
        rows = filing.schedules.for_account(forms.schedules.accounts["receivables"])
        assert sum(r.amount.value or 0 for r in rows) == receivable
        assert filing.balance_sheet["10"].amount.value == Decimal(receivable)
        rows = filing.schedules.for_account(forms.schedules.accounts["officer_borrowings"])
        assert sum(r.amount.value or 0 for r in rows) == borrowing
        assert filing.balance_sheet["236"].amount.value == Decimal(borrowing)
    current_rows = sample_case.current.schedules.for_account(["외상매출금"])
    assert len(current_rows) == 4


def test_checks_pass_on_sample(sample_case: CompanyCase) -> None:
    assert sample_case.prior.warnings == []
    assert sample_case.current.warnings == []


def test_report_from_sample(sample_case: CompanyCase, forms: FormsConfig, policy: Policy) -> None:
    from datetime import date

    from taxreport.analyze.builder import build_report
    from taxreport.render.formatters import format_metric_value

    report = build_report(sample_case, forms, policy, issued_on=date(2026, 11, 21))
    shown = {
        row.label: (format_metric_value(row, "prior", policy), format_metric_value(row, "current", policy))
        for row in report.key_metrics
    }
    assert shown["매출액"] == ("9,294만원", "1.93억원")
    assert shown["부채비율"] == ("자본잠식 (-354.88%)", "51.79%")
    assert [row.code for row in report.sga_items[:5]] == ["68", "108", "124", "79", "80"]
    assert len(report.needs_check) == 2  # 결손금 차이 + «37.기타» 항목의 뜻 확인
    pages = {(row.item, row.year): row.page for row in report.evidence}
    assert pages[("매출액", 2025)] == 41
    assert pages[("자본총계(Ⅲ+~Ⅵ)", 2024)] == 23
