"""전기·당기 비교: 증감액, 증감률, 흑자전환 판정."""

from __future__ import annotations

from decimal import Decimal

from taxreport.analyze.metrics import Statement, quantize, statement_value, subtract
from taxreport.config.loader import FormsConfig, Policy, RowLabel
from taxreport.domain.models import CompanyCase, ComparisonRow, SourcedValue


def change_rate(
    prior: Decimal | None,
    current: Decimal | None,
    policy: Policy,
    *,
    allow_turnaround: bool = True,
) -> tuple[Decimal | None, str | None]:
    """증감률(%)과, 계산하지 않을 때 대신 쓰는 글자. 예외는 TRD 7.4의 순서대로 본다.

    «흑자전환»은 손익 항목에만 쓰는 말이다. 자본총계 같은 재무상태 항목은
    allow_turnaround=False로 불러 «-»로 둔다.
    """
    rules = policy.comparison
    if prior is None or current is None:
        return None, None
    if allow_turnaround and prior < 0 and current > 0:
        return None, rules.turnaround_label
    if prior == 0:
        return None, rules.not_applicable
    if prior < 0 or current < 0:
        return None, rules.not_applicable
    rate = (current - prior) / abs(prior) * 100
    return quantize(rate, rules.rate_decimals), None


def compare(
    label: str,
    prior: SourcedValue,
    current: SourcedValue,
    policy: Policy,
    *,
    allow_turnaround: bool = True,
) -> ComparisonRow:
    rate, text = change_rate(prior.value, current.value, policy, allow_turnaround=allow_turnaround)
    return ComparisonRow(
        label=label,
        prior=prior,
        current=current,
        change=subtract(current.value, prior.value),
        change_rate=rate,
        rate_text=text,
    )


def _rows(
    case: CompanyCase,
    forms: FormsConfig,
    policy: Policy,
    statement: Statement,
    labels: list[RowLabel],
    *,
    allow_turnaround: bool = True,
) -> list[ComparisonRow]:
    return [
        compare(
            row.label,
            statement_value(case.prior, forms, statement, row.key),
            statement_value(case.current, forms, statement, row.key),
            policy,
            allow_turnaround=allow_turnaround,
        )
        for row in labels
    ]


def build_income_comparison(case: CompanyCase, forms: FormsConfig, policy: Policy) -> list[ComparisonRow]:
    return _rows(case, forms, policy, "income", policy.labels.income_comparison)


def build_balance_comparison(case: CompanyCase, forms: FormsConfig, policy: Policy) -> list[ComparisonRow]:
    return _rows(
        case, forms, policy, "balance", policy.labels.balance_comparison, allow_turnaround=False
    )
