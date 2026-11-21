from __future__ import annotations

from decimal import Decimal

from taxreport.analyze.metrics import build_key_metrics, debt_ratio, display_label
from taxreport.config.loader import FormsConfig, Policy
from taxreport.domain.models import CompanyCase, Filing, MetricKind, ValueStatus
from taxreport.render.formatters import format_metric_change, format_metric_value


def test_debt_ratio(current_filing: Filing, forms: FormsConfig, policy: Policy) -> None:
    value, label = debt_ratio(current_filing, forms, policy)
    assert value.value == Decimal("51.79")
    assert label is None
    assert value.status is ValueStatus.ASSUMED
    assert {source.code for source in value.derived_from} == {"333", "382"}


def test_debt_ratio_negative_equity(prior_filing: Filing, forms: FormsConfig, policy: Policy) -> None:
    value, label = debt_ratio(prior_filing, forms, policy)
    assert value.value == Decimal("-354.88")
    assert label == "자본잠식"


def test_debt_ratio_zero_equity_needs_check(prior_filing: Filing, forms: FormsConfig, policy: Policy) -> None:
    prior_filing.balance_sheet["382"].amount.value = Decimal(0)
    value, _ = debt_ratio(prior_filing, forms, policy)
    assert value.value is None
    assert value.status is ValueStatus.NEEDS_CHECK


def test_key_metrics(case: CompanyCase, forms: FormsConfig, policy: Policy) -> None:
    rows = build_key_metrics(case, forms, policy)
    assert [row.label for row in rows] == ["매출액", "영업이익", "당기순이익", "부채비율"]
    shown = {
        row.label: (
            format_metric_value(row, "prior", policy),
            format_metric_value(row, "current", policy),
            format_metric_change(row, policy),
        )
        for row in rows
    }
    assert shown["매출액"] == ("9,294만원", "1.93억원", "9,979만원")
    assert shown["영업이익"] == ("-1,539만원", "4,925만원", "6,464만원")
    assert shown["당기순이익"] == ("-1,551만원", "4,650만원", "6,201만원")
    assert shown["부채비율"] == ("자본잠식 (-354.88%)", "51.79%", "-")
    assert rows[3].kind is MetricKind.RATIO


def test_debt_ratio_change_when_both_positive(case: CompanyCase, forms: FormsConfig, policy: Policy) -> None:
    case.prior.balance_sheet["382"].amount.value = Decimal(10_000_000)
    case.prior.balance_sheet["333"].amount.value = Decimal(20_000_000)
    ratio = build_key_metrics(case, forms, policy)[3]
    assert ratio.change == Decimal("-148.21")
    assert format_metric_change(ratio, policy) == "-148.21%p"


def test_display_label() -> None:
    assert display_label("Ⅳ.판매비와관리비") == "판매비와관리비"
    assert display_label("1.급여") == "급여"
    assert display_label("가.임원급여") == "임원급여"
    assert display_label("(1)당좌자산") == "당좌자산"
    assert display_label("자산총계(Ⅰ+Ⅱ)") == "자산총계(Ⅰ+Ⅱ)"
    assert display_label("28.수도광열비(전기료제외)") == "수도광열비(전기료제외)"
