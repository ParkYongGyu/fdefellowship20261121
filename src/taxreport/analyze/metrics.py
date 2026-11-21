"""주요지표와 공통 계산 도구."""

from __future__ import annotations

import re
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

from taxreport.config.loader import FormsConfig, Policy
from taxreport.domain.models import (
    CompanyCase,
    Filing,
    LineItem,
    MetricKind,
    MetricRow,
    NumberStyle,
    SourcedValue,
    TextArg,
    ValueStatus,
)

Statement = Literal["income", "balance"]
_NUMBERING_RE = re.compile(r"^(?:[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩⅪⅫ]+\.|\(\d+\)|\d+\.|[가-힣]\.)\s*")
DEBT_RATIO_NOTE = "부채총계 ÷ 자본총계 × 100"


def display_label(label: str) -> str:
    """`Ⅳ.판매비와관리비`, `1.급여`, `가.임원급여`에서 앞 번호를 뗀다."""
    return _NUMBERING_RE.sub("", label) or label


def quantize(value: Decimal, decimals: int) -> Decimal:
    return value.quantize(Decimal(1).scaleb(-decimals), rounding=ROUND_HALF_UP)


def percent_of(numerator: Decimal | None, denominator: Decimal | None, decimals: int) -> Decimal | None:
    if numerator is None or denominator is None or denominator == 0:
        return None
    return quantize(numerator / denominator * 100, decimals)


def subtract(current: Decimal | None, prior: Decimal | None) -> Decimal | None:
    if current is None or prior is None:
        return None
    return current - prior


def item_value(items: dict[str, LineItem], code: str | None, what: str) -> SourcedValue:
    if code is None or code not in items:
        return SourcedValue.missing(f"{what} 값을 서식에서 찾지 못했습니다")
    return items[code].amount


def statement_value(filing: Filing, forms: FormsConfig, statement: Statement, key: str) -> SourcedValue:
    """forms.yaml의 항목 키로 표준서식 값을 찾는다."""
    if statement == "income":
        rules, items = forms.standard_forms.income_statement, filing.income_statement
    else:
        rules, items = forms.standard_forms.balance_sheet, filing.balance_sheet
    return item_value(items, rules.codes.get(key), f"{rules.form} {key}")


def sum_note_codes(filing: Filing, forms: FormsConfig, key: str) -> SourcedValue:
    """특이사항용 계정(note_codes)의 합. 서식에 코드가 하나도 없으면 값 없음."""
    rules = forms.standard_forms.balance_sheet
    found = [filing.balance_sheet[c] for c in rules.note_codes.get(key, []) if c in filing.balance_sheet]
    if not found:
        return SourcedValue.missing(f"{rules.form}에서 {key} 계정을 찾지 못했습니다")
    if len(found) == 1:
        return found[0].amount
    total = sum((item.amount.value or Decimal(0) for item in found), Decimal(0))
    sources = [item.amount.source for item in found if item.amount.source is not None]
    return SourcedValue(value=total, status=ValueStatus.CONFIRMED, derived_from=sources)


def debt_ratio(filing: Filing, forms: FormsConfig, policy: Policy) -> tuple[SourcedValue, str | None]:
    """부채비율(가정 A2)과 자본잠식 표기(가정 A3)."""
    liabilities = statement_value(filing, forms, "balance", "total_liabilities")
    equity = statement_value(filing, forms, "balance", "total_equity")
    ratio = percent_of(liabilities.value, equity.value, policy.debt_ratio.decimals)
    if ratio is None:
        return SourcedValue.missing("부채비율을 계산할 수 없습니다(부채총계·자본총계 확인)"), None
    label = policy.debt_ratio.negative_equity_label if (equity.value or 0) < 0 else None
    value = SourcedValue(
        value=ratio,
        status=ValueStatus.ASSUMED,
        derived_from=[*liabilities.sources(), *equity.sources()],
        note=DEBT_RATIO_NOTE,
    )
    return value, label


def build_key_metrics(case: CompanyCase, forms: FormsConfig, policy: Policy) -> list[MetricRow]:
    labels = policy.labels.key_metrics
    rows: list[MetricRow] = []
    for key in ("revenue", "operating_income", "net_income"):
        prior = statement_value(case.prior, forms, "income", key)
        current = statement_value(case.current, forms, "income", key)
        rows.append(
            MetricRow(
                label=labels[key],
                kind=MetricKind.AMOUNT,
                prior=prior,
                current=current,
                change=subtract(current.value, prior.value),
            )
        )
    prior_ratio, prior_label = debt_ratio(case.prior, forms, policy)
    current_ratio, current_label = debt_ratio(case.current, forms, policy)
    comparable = prior_label is None and current_label is None
    change = subtract(current_ratio.value, prior_ratio.value) if comparable else None
    rows.append(
        MetricRow(
            label=labels["debt_ratio"],
            kind=MetricKind.RATIO,
            prior=prior_ratio,
            current=current_ratio,
            change=change,
            prior_label=prior_label,
            current_label=current_label,
            change_text=None if comparable else policy.comparison.not_applicable,
        )
    )
    return rows


# ------------------------------------------------------------ 문장 틀에 넣는 값


def arg_won(value: Decimal | None) -> TextArg:
    return TextArg(style=NumberStyle.WON, value=value)


def arg_text(text: str) -> TextArg:
    return TextArg(style=NumberStyle.TEXT, text=text)


def arg_year(year: int) -> TextArg:
    return TextArg(style=NumberStyle.TEXT, text=str(year))


def arg_percent(value: Decimal | None, decimals: int, label: str | None = None) -> TextArg:
    return TextArg(style=NumberStyle.PERCENT, value=value, decimals=decimals, text=label)


def arg_count(count: int) -> TextArg:
    return TextArg(style=NumberStyle.COUNT, value=Decimal(count))


def arg_metric(row: MetricRow, which: Literal["prior", "current"], decimals: int) -> TextArg:
    """부채비율 행의 한쪽 값을 «자본잠식 (-354.88%)» 꼴로 쓸 수 있게 넘긴다."""
    if which == "prior":
        return arg_percent(row.prior.value, decimals, row.prior_label)
    return arg_percent(row.current.value, decimals, row.current_label)
