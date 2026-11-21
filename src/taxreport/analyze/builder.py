"""분석 결과를 모아 Report를 만든다."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import structlog

from taxreport.analyze.comments import build_comments
from taxreport.analyze.comparison import build_balance_comparison, build_income_comparison
from taxreport.analyze.metrics import build_key_metrics, display_label
from taxreport.analyze.notes import build_financial_notes, build_tax_notes
from taxreport.analyze.sga import build_sga_rows
from taxreport.config.loader import FormsConfig, Policy
from taxreport.domain.models import (
    CompanyCase,
    EvidenceRow,
    Filing,
    LineItem,
    MetricKind,
    MetricRow,
    NumberStyle,
    Report,
    SgaRow,
    SgaRowKind,
    SourcedValue,
    ValueStatus,
)

log = structlog.get_logger(__name__)

CALCULATED_FORM = "계산값"


def _evidence(item: str, year: int, value: SourcedValue, style: NumberStyle = NumberStyle.WON) -> EvidenceRow | None:
    source = value.source
    if source is None:
        return None
    return EvidenceRow(
        item=item,
        year=year,
        value=value.value,
        style=style,
        status=value.status,
        file=source.file,
        form=source.form,
        page=source.page,
        code=source.code,
    )


def _line_items(filing: Filing, items: dict[str, LineItem], codes: list[str]) -> list[EvidenceRow]:
    rows: list[EvidenceRow] = []
    for code in codes:
        item = items.get(code)
        if item is None:
            continue
        row = _evidence(display_label(item.label), filing.identity.fiscal_year, item.amount)
        if row is not None:
            rows.append(row)
    return rows


def _filing_evidence(filing: Filing, forms: FormsConfig, sga_rows: list[SgaRow]) -> list[EvidenceRow]:
    year = filing.identity.fiscal_year
    pl = forms.standard_forms.income_statement
    bs = forms.standard_forms.balance_sheet
    rows: list[EvidenceRow] = []

    income_codes = list(pl.codes.values())
    income_codes += [r.code for r in sga_rows if r.kind is SgaRowKind.ITEM and r.code is not None]
    for key in ("non_operating_income", "non_operating_expense"):
        parent = pl.codes.get(key)
        income_codes += [
            item.code
            for item in filing.income_statement.values()
            if item.parent_code == parent and (item.amount.value or Decimal(0)) != 0
        ]
    rows += _line_items(filing, filing.income_statement, list(dict.fromkeys(income_codes)))

    balance_codes = list(bs.codes.values())
    for codes in bs.note_codes.values():
        balance_codes += [
            c for c in codes if c in filing.balance_sheet and (filing.balance_sheet[c].amount.value or 0) != 0
        ]
    rows += _line_items(filing, filing.balance_sheet, list(dict.fromkeys(balance_codes)))

    labels = {**forms.tax_adjustment.labels, **forms.minimum_tax.labels}
    for key, value in filing.tax.values.items():
        if value.note is not None and (value.value or Decimal(0)) == 0:
            continue  # 빈 칸은 근거 목록에 올리지 않는다
        style = NumberStyle.COUNT if key == "tax_rate" else NumberStyle.WON
        row = _evidence(labels.get(key, key), year, value, style)
        if row is not None:
            rows.append(row)
    for adjustment in filing.adjustments:
        row = _evidence(f"{adjustment.kind.value} {adjustment.subject}", year, adjustment.amount)
        if row is not None:
            rows.append(row)
    for credit in filing.credits:
        row = _evidence(credit.name, year, credit.amount)
        if row is not None:
            rows.append(row)
    for record in filing.losses:
        named = (
            ("결손금 발생액", record.incurred),
            ("결손금 당기 공제액", record.used_current),
            ("결손금 잔액", record.balance),
        )
        for name, loss_value in named:
            if loss_value is None:
                continue
            row = _evidence(f"{name}({record.fiscal_year_end.isoformat()})", year, loss_value)
            if row is not None:
                rows.append(row)
    keywords = [k for names in forms.schedules.accounts.values() for k in names]
    for balance in filing.schedules.for_account(keywords):
        name = f"{balance.account} {balance.partner}" if balance.partner else balance.account
        row = _evidence(name, year, balance.amount)
        if row is not None:
            rows.append(row)
    return rows


def _ratio_evidence(case: CompanyCase, key_metrics: list[MetricRow]) -> list[EvidenceRow]:
    rows: list[EvidenceRow] = []
    for metric in key_metrics:
        if metric.kind is not MetricKind.RATIO:
            continue
        for filing, value in ((case.prior, metric.prior), (case.current, metric.current)):
            sources = value.sources()
            rows.append(
                EvidenceRow(
                    item=metric.label,
                    year=filing.identity.fiscal_year,
                    value=value.value,
                    style=NumberStyle.PERCENT,
                    status=value.status,
                    file=filing.source_file,
                    form=f"{CALCULATED_FORM}({value.note})" if value.note else CALCULATED_FORM,
                    page=None,
                    code="·".join(s.code for s in sources if s.code) or None,
                )
            )
    return rows


def build_report(case: CompanyCase, forms: FormsConfig, policy: Policy, issued_on: date) -> Report:
    key_metrics = build_key_metrics(case, forms, policy)
    income_rows = build_income_comparison(case, forms, policy)
    balance_rows = build_balance_comparison(case, forms, policy)
    sga_rows = build_sga_rows(case, forms, policy)
    comments = build_comments(case, forms, policy, key_metrics, income_rows, sga_rows)
    tax_notes, tax_needs_check = build_tax_notes(case, forms, policy)
    financial_notes = build_financial_notes(case, forms, policy, key_metrics)

    evidence: list[EvidenceRow] = []
    for filing in (case.prior, case.current):
        evidence += _filing_evidence(filing, forms, sga_rows)
    evidence += _ratio_evidence(case, key_metrics)

    needs_check: list[str] = []
    for filing in (case.prior, case.current):
        needs_check += [f"{filing.identity.fiscal_year}년: {warning}" for warning in filing.warnings]
    for metric in key_metrics:
        if metric.prior.value is None or metric.current.value is None:
            needs_check.append(f"주요 지표 «{metric.label}» 값을 읽지 못했습니다")
    for comparison in (*income_rows, *balance_rows):
        if comparison.prior.value is None or comparison.current.value is None:
            needs_check.append(f"«{comparison.label}» 값을 읽지 못했습니다")
    held = [row.label for row in sga_rows if row.kind is SgaRowKind.ITEM and row.current.value is None]
    if held:
        needs_check.append(
            f"판관비 «{', '.join(held)}»의 당기 금액을 읽지 못해 주요 {policy.sga.top_n}개 선정과 "
            f"«{policy.sga.other_label}» 금액을 확정하지 못했습니다(읽은 항목만으로 고른 순서입니다)"
        )
    needs_check += tax_needs_check
    needs_check += [
        f"{row.year}년 {row.item}: {row.form}" for row in evidence if row.status is ValueStatus.NEEDS_CHECK
    ]

    current, prior = case.current.identity, case.prior.identity
    report = Report(
        company=current.name,
        business_no=current.business_no,
        prior_year=prior.fiscal_year,
        current_year=current.fiscal_year,
        prior_period=(prior.period_start, prior.period_end),
        current_period=(current.period_start, current.period_end),
        issuer=policy.issuer.name,
        issued_on=issued_on,
        key_metrics=key_metrics,
        income_comparison=income_rows,
        balance_comparison=balance_rows,
        sga_items=sga_rows,
        comments=comments,
        tax_notes=tax_notes,
        financial_notes=financial_notes,
        assumptions=list(policy.assumptions),
        needs_check=list(dict.fromkeys(needs_check)),
        evidence=evidence,
    )
    log.info(
        "report_built",
        company=report.company,
        current_year=report.current_year,
        evidence=len(evidence),
        needs_check=len(report.needs_check),
    )
    return report
