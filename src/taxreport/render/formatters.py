"""표기 형식. 억원·만원·% 표기는 여기서만 한다. 값을 새로 계산하지 않는다."""

from __future__ import annotations

from datetime import date
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal
from typing import Literal

from taxreport.config.loader import Policy
from taxreport.domain.models import (
    ComparisonRow,
    EvidenceRow,
    MetricKind,
    MetricRow,
    NumberStyle,
    Sentence,
    TextArg,
)

MAN = Decimal(10_000)
EOK = Decimal(100_000_000)


def _rounding(policy: Policy) -> str:
    return ROUND_HALF_UP if policy.units.rounding == "half_up" else ROUND_DOWN


def format_number(value: Decimal | None, policy: Policy) -> str:
    """원 단위 금액을 천 단위 구분 기호와 함께. 단위 글자는 붙이지 않는다(표에서 씀)."""
    if value is None:
        return policy.report.missing_text
    return f"{value:,.0f}"


def format_won(value: Decimal | None, policy: Policy) -> str:
    if value is None:
        return policy.report.missing_text
    return f"{value:,.0f}원"


def format_key_amount(value: Decimal | None, policy: Policy) -> str:
    """주요지표 표기: 절댓값 1억 이상은 `1.93억원`, 1억 미만은 `4,925만원`."""
    if value is None:
        return policy.report.missing_text
    units = policy.units
    if abs(value) >= units.eok_threshold:
        step = Decimal(1).scaleb(-units.eok_decimals)
        eok = (value / EOK).quantize(step, rounding=_rounding(policy))
        return f"{eok:,.{units.eok_decimals}f}억원"
    man = (value / MAN).quantize(Decimal(1), rounding=_rounding(policy))
    if man == 0:
        man = Decimal(0)  # -0만원을 피한다
    return f"{man:,.0f}만원"


def format_percent(value: Decimal | None, decimals: int, policy: Policy) -> str:
    if value is None:
        return policy.report.missing_text
    return f"{value:,.{decimals}f}%"


def format_labeled_percent(value: Decimal | None, decimals: int, label: str | None, policy: Policy) -> str:
    """`51.79%` 또는 `자본잠식 (-354.88%)`."""
    text = format_percent(value, decimals, policy)
    return f"{label} ({text})" if label else text


def format_metric_value(row: MetricRow, which: Literal["prior", "current"], policy: Policy) -> str:
    value = row.prior if which == "prior" else row.current
    label = row.prior_label if which == "prior" else row.current_label
    if row.kind is MetricKind.RATIO:
        return format_labeled_percent(value.value, policy.debt_ratio.decimals, label, policy)
    return format_key_amount(value.value, policy)


def format_metric_change(row: MetricRow, policy: Policy) -> str:
    if row.change_text is not None:
        return row.change_text
    if row.change is None:
        return policy.report.missing_text
    if row.kind is MetricKind.RATIO:
        return f"{row.change:+,.{policy.debt_ratio.decimals}f}%p"
    return format_key_amount(row.change, policy)


def format_rate(row: ComparisonRow, policy: Policy) -> str:
    if row.rate_text is not None:
        return row.rate_text
    if row.change_rate is None:
        return policy.report.missing_text
    return format_percent(row.change_rate, policy.comparison.rate_decimals, policy)


def format_arg(arg: TextArg, policy: Policy) -> str:
    if arg.style is NumberStyle.TEXT:
        return arg.text if arg.text is not None else policy.report.missing_text
    if arg.style is NumberStyle.WON:
        return format_won(arg.value, policy)
    if arg.style is NumberStyle.KEY_AMOUNT:
        return format_key_amount(arg.value, policy)
    if arg.style is NumberStyle.PERCENT:
        return format_labeled_percent(arg.value, arg.decimals, arg.text, policy)
    return format_number(arg.value, policy)


def render_sentence(sentence: Sentence, policy: Policy) -> str:
    values = {name: format_arg(arg, policy) for name, arg in sentence.args.items()}
    return sentence.template.format(**values)


def format_evidence_value(row: EvidenceRow, policy: Policy) -> str:
    if row.style is NumberStyle.PERCENT:
        return format_percent(row.value, policy.debt_ratio.decimals, policy)
    if row.style is NumberStyle.WON:
        return format_won(row.value, policy)
    return format_number(row.value, policy)


def format_period(start: date, end: date) -> str:
    return f"{start:%Y.%m.%d} ~ {end:%Y.%m.%d}"
