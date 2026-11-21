from __future__ import annotations

from decimal import Decimal

import pytest

from taxreport.analyze.comparison import build_income_comparison, change_rate
from taxreport.config.loader import FormsConfig, Policy
from taxreport.domain.models import CompanyCase
from taxreport.render.formatters import format_rate


@pytest.mark.parametrize(
    ("prior", "current", "expected"),
    [
        (-15_385_718, 49_254_390, (None, "흑자전환")),  # 전기 적자 → 당기 흑자
        (0, 2_719_623, (None, "-")),  # 전기 0
        (0, 0, (None, "-")),
        (-100, -50, (None, "-")),  # 양쪽 적자
        (100, -50, (None, "-")),  # 흑자 → 적자
        (-100, 0, (None, "-")),
        (92_944_479, 192_734_983, (Decimal("107.4"), None)),
        (129_610, 45_480, (Decimal("-64.9"), None)),
        (200, 100, (Decimal("-50.0"), None)),
    ],
)
def test_change_rate(policy: Policy, prior: int, current: int, expected: tuple[Decimal | None, str | None]) -> None:
    assert change_rate(Decimal(prior), Decimal(current), policy) == expected


def test_change_rate_missing(policy: Policy) -> None:
    assert change_rate(None, Decimal(1), policy) == (None, None)


def test_income_comparison_rows(case: CompanyCase, forms: FormsConfig, policy: Policy) -> None:
    rows = {row.label: row for row in build_income_comparison(case, forms, policy)}
    assert list(rows) == [
        "매출액", "매출총이익", "영업손익", "영업외수익", "영업외비용",
        "법인세차감전손익", "법인세비용", "당기순이익",
    ]
    assert rows["매출액"].change == Decimal(99_790_504)
    assert format_rate(rows["매출액"], policy) == "107.4%"
    assert format_rate(rows["영업손익"], policy) == "흑자전환"
    assert rows["영업손익"].change == Decimal(64_640_108)
    assert format_rate(rows["법인세비용"], policy) == "-"
    assert rows["매출액"].prior.source is not None
    assert rows["매출액"].prior.source.file == "test_2024.pdf"


def test_balance_rows_do_not_use_turnaround_label(policy: Policy) -> None:
    """자본총계가 음수에서 양수로 바뀌어도 «흑자전환»이라고 쓰지 않는다."""
    rate, text = change_rate(Decimal("-5510353"), Decimal("40985771"), policy, allow_turnaround=False)
    assert rate is None
    assert text == policy.comparison.not_applicable
