"""판관비 주요 항목: 당기 상위 N개 + 기타 + 합계."""

from __future__ import annotations

from decimal import Decimal

from taxreport.analyze.metrics import display_label, percent_of, statement_value, subtract
from taxreport.config.loader import FormsConfig, Policy
from taxreport.domain.models import (
    CompanyCase,
    LineItem,
    SgaRow,
    SgaRowKind,
    SourcedValue,
    ValueStatus,
)

OTHER_NOTE = "판매비와관리비 합계 - 주요 항목"
ABSENT_PRIOR_NOTE = "전기 서식에 같은 코드가 없어 0으로 둠"


def sga_candidates(income_statement: dict[str, LineItem], total_code: str) -> list[LineItem]:
    """판관비 바로 아래 항목만 후보다. 그 아래 세부 항목은 이중 계산이 되므로 뺀다."""
    return [item for item in income_statement.values() if item.parent_code == total_code]


def _row_label(item: LineItem, form_name: str, policy: Policy) -> str:
    name = display_label(item.label)
    if name == policy.sga.other_label:
        return policy.sga.same_name_template.format(name=name, form=form_name, label=item.label)
    return name


def build_sga_rows(case: CompanyCase, forms: FormsConfig, policy: Policy) -> list[SgaRow]:
    rules = forms.standard_forms.income_statement
    decimals = policy.sga.share_decimals
    total_code = rules.codes.get("sga_total", "")
    prior_total = statement_value(case.prior, forms, "income", "sga_total")
    current_total = statement_value(case.current, forms, "income", "sga_total")
    prior_revenue = statement_value(case.prior, forms, "income", "revenue").value
    current_revenue = statement_value(case.current, forms, "income", "revenue").value

    def row(label: str, kind: SgaRowKind, code: str | None, prior: SourcedValue, current: SourcedValue) -> SgaRow:
        return SgaRow(
            label=label,
            kind=kind,
            code=code,
            prior=prior,
            current=current,
            change=subtract(current.value, prior.value),
            prior_share=percent_of(prior.value, prior_revenue, decimals),
            current_share=percent_of(current.value, current_revenue, decimals),
            composition=percent_of(current.value, current_total.value, decimals),
        )

    everything = sga_candidates(case.current.income_statement, total_code)
    # 금액을 읽지 못한 항목은 순위에 넣을 수 없다. 표에 «확인 필요»로 따로 보여 준다.
    unreadable = [item for item in everything if item.amount.value is None]
    candidates = [item for item in everything if (item.amount.value or Decimal(0)) > 0]
    candidates.sort(key=lambda item: (-(item.amount.value or Decimal(0)), int(item.code)))
    top = candidates[: policy.sga.top_n]

    rows: list[SgaRow] = []
    for item in top:
        prior_item = case.prior.income_statement.get(item.code)
        prior = (
            prior_item.amount
            if prior_item is not None
            else SourcedValue(value=Decimal(0), status=ValueStatus.ASSUMED, note=ABSENT_PRIOR_NOTE)
        )
        rows.append(row(_row_label(item, rules.form, policy), SgaRowKind.ITEM, item.code, prior, item.amount))

    for item in unreadable:
        prior_item = case.prior.income_statement.get(item.code)
        prior = (
            prior_item.amount
            if prior_item is not None
            else SourcedValue.missing("전기 서식에 같은 코드가 없습니다")
        )
        rows.append(row(_row_label(item, rules.form, policy), SgaRowKind.ITEM, item.code, prior, item.amount))

    def other(total: SourcedValue, parts: list[Decimal | None]) -> SourcedValue:
        if total.value is None or any(part is None for part in parts):
            return SourcedValue.missing("판관비 기타 금액을 계산할 수 없습니다")
        used = sum((part for part in parts if part is not None), Decimal(0))
        return SourcedValue(
            value=total.value - used,
            status=ValueStatus.CONFIRMED,
            derived_from=total.sources(),
            note=OTHER_NOTE,
        )

    rows.append(
        row(
            policy.sga.other_label,
            SgaRowKind.OTHER,
            None,
            other(prior_total, [r.prior.value for r in rows]),
            other(current_total, [r.current.value for r in rows]),
        )
    )
    rows.append(row(policy.sga.total_label, SgaRowKind.TOTAL, total_code or None, prior_total, current_total))
    return rows
