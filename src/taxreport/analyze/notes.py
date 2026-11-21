"""세무조정 특이사항과 재무제표 특이사항(규칙 기반). 판단 표현 없이 수치만 적는다."""

from __future__ import annotations

from decimal import Decimal

from taxreport.analyze.metrics import (
    arg_count,
    arg_metric,
    arg_percent,
    arg_text,
    arg_won,
    arg_year,
    percent_of,
    statement_value,
    subtract,
    sum_note_codes,
)
from taxreport.config.loader import FormsConfig, Policy
from taxreport.domain.models import (
    CompanyCase,
    Filing,
    MetricKind,
    MetricRow,
    Note,
    Sentence,
    TextArg,
    ValueStatus,
)

NO_BASIS = "근거 법조항 미기재"
NO_DISPOSITION = "소득처분 미기재"


def _sentence(policy: Policy, key: str, **args: TextArg) -> Sentence:
    return Sentence(template=policy.template(key), args=args)


def _adjustment_sentences(filing: Filing, forms: FormsConfig, policy: Policy, detailed: bool) -> list[Sentence]:
    year = arg_year(filing.identity.fiscal_year)
    if filing.adjustments:
        if not detailed:
            return [_sentence(policy, "adjustment_count", year=year, count=arg_count(len(filing.adjustments)))]
        sentences: list[Sentence] = []
        for adjustment in filing.adjustments:
            key = "adjustment_item" if adjustment.disposition else "adjustment_item_no_disposition"
            sentences.append(
                _sentence(
                    policy,
                    key,
                    year=year,
                    subject=arg_text(adjustment.subject),
                    amount=arg_won(adjustment.amount.value),
                    kind=arg_text(adjustment.kind.value),
                    disposition=arg_text(adjustment.disposition or NO_DISPOSITION),
                )
            )
        return sentences
    added = filing.tax.values.get("income_addition")
    deducted = filing.tax.values.get("income_deduction")
    has_amount = any(v is not None and (v.value or Decimal(0)) != 0 for v in (added, deducted))
    if has_amount and not filing.has_form(forms.adjustments.form):
        return [_sentence(policy, "adjustment_unread", year=year)]
    return [_sentence(policy, "adjustment_none", year=year)]


def build_tax_notes(case: CompanyCase, forms: FormsConfig, policy: Policy) -> tuple[list[Note], list[str]]:
    """세무조정 특이사항과, 확인 필요 목록에 올릴 문장."""
    titles = policy.notes.titles
    current, prior = case.current, case.prior
    cy, py = arg_year(current.identity.fiscal_year), arg_year(prior.identity.fiscal_year)
    notes: list[Note] = []
    needs_check: list[str] = []

    current_adjustments = _adjustment_sentences(current, forms, policy, detailed=True)
    prior_adjustments = _adjustment_sentences(prior, forms, policy, detailed=False)
    unread = policy.template("adjustment_unread")
    adjustment_status = (
        ValueStatus.NEEDS_CHECK
        if any(s.template == unread for s in (*current_adjustments, *prior_adjustments))
        else ValueStatus.CONFIRMED
    )
    notes.append(
        Note(
            title=titles["adjustments"],
            sentences=[*current_adjustments, *prior_adjustments],
            status=adjustment_status,
        )
    )
    if adjustment_status is ValueStatus.NEEDS_CHECK:
        needs_check.append("소득금액조정합계표를 찾지 못한 사업연도가 있습니다(세무조정 특이사항 참고)")

    loss_sentences: list[Sentence] = []
    loss_status = ValueStatus.CONFIRMED
    used = current.tax.values.get("loss_carryforward_used")
    if used is not None and (used.value or Decimal(0)) != 0:
        loss_sentences.append(_sentence(policy, "loss_used", year=cy, amount=arg_won(used.value)))
        for origin in current.losses:
            if origin.used_current is None or (origin.used_current.value or Decimal(0)) == 0:
                continue
            loss_sentences.append(
                _sentence(
                    policy,
                    "loss_origin",
                    year_end=arg_text(origin.fiscal_year_end.isoformat()),
                    incurred=arg_won(origin.incurred.value),
                )
            )
    prior_base = prior.tax.values.get("tax_base")
    if prior_base is not None and prior_base.value is not None and prior_base.value < 0:
        for filing in (current, prior):
            record = next(
                (r for r in filing.losses if r.fiscal_year_end == prior.identity.period_end), None
            )
            if record is None or record.incurred.value is None:
                continue
            deficit = -prior_base.value
            if record.incurred.value != deficit:
                loss_status = ValueStatus.NEEDS_CHECK
                loss_sentences.append(
                    _sentence(
                        policy,
                        "loss_mismatch",
                        py=py,
                        tax_base=arg_won(deficit),
                        incurred=arg_won(record.incurred.value),
                        diff=arg_won(abs(record.incurred.value - deficit)),
                    )
                )
                needs_check.append(
                    f"{prior.identity.fiscal_year}년 과세표준 결손액과 자본금과적립금조정명세서(갑)의 "
                    "결손금 발생액이 서로 다릅니다(세무조정 특이사항의 이월결손금 참고)"
                )
            break
    if loss_sentences:
        notes.append(Note(title=titles["loss"], sentences=loss_sentences, status=loss_status))

    credit_sentences = [
        _sentence(
            policy,
            "credit_note",
            year=cy,
            name=arg_text(credit.name),
            basis=arg_text(credit.legal_basis or NO_BASIS),
            amount=arg_won(credit.amount.value),
        )
        for credit in current.credits
    ]
    if credit_sentences:
        notes.append(Note(title=titles["credits"], sentences=credit_sentences))

    minimum = current.tax.values.get("minimum_tax")
    after = current.tax.values.get("tax_after_credits")
    if minimum is not None and after is not None:
        notes.append(
            Note(
                title=titles["minimum_tax"],
                sentences=[
                    _sentence(
                        policy,
                        "minimum_tax",
                        year=cy,
                        minimum=arg_won(minimum.value),
                        after=arg_won(after.value),
                    )
                ],
            )
        )

    summary: list[Sentence] = []
    summary_status = ValueStatus.CONFIRMED
    for filing in (current, prior):
        values = [filing.tax.get(key) for key in ("tax_base", "calculated_tax", "total_tax")]
        if any(v.value is None for v in values):
            summary_status = ValueStatus.NEEDS_CHECK
            needs_check.append(
                f"{filing.identity.fiscal_year}년 세액조정계산서에서 과세표준·세액을 읽지 못했습니다"
            )
        summary.append(
            _sentence(
                policy,
                "tax_summary",
                year=arg_year(filing.identity.fiscal_year),
                tax_base=arg_won(values[0].value),
                calculated=arg_won(values[1].value),
                total=arg_won(values[2].value),
            )
        )
    notes.append(Note(title=titles["tax_summary"], sentences=summary, status=summary_status))
    return notes, needs_check


def build_financial_notes(
    case: CompanyCase, forms: FormsConfig, policy: Policy, key_metrics: list[MetricRow]
) -> list[Note]:
    titles = policy.notes.titles
    current, prior = case.current, case.prior
    cy, py = arg_year(current.identity.fiscal_year), arg_year(prior.identity.fiscal_year)
    notes: list[Note] = []

    prior_equity = statement_value(prior, forms, "balance", "total_equity").value
    current_equity = statement_value(current, forms, "balance", "total_equity").value
    equity_sentences: list[Sentence] = []
    prior_negative = prior_equity is not None and prior_equity < 0
    current_negative = current_equity is not None and current_equity < 0
    if prior_negative and current_equity is not None and not current_negative:
        equity_sentences.append(
            _sentence(
                policy,
                "negative_equity_resolved",
                py=py,
                cy=cy,
                prior=arg_won(prior_equity),
                current=arg_won(current_equity),
            )
        )
    else:
        if prior_negative:
            equity_sentences.append(_sentence(policy, "negative_equity", year=py, equity=arg_won(prior_equity)))
        if current_negative:
            equity_sentences.append(
                _sentence(policy, "negative_equity", year=cy, equity=arg_won(current_equity))
            )
    if equity_sentences:
        notes.append(Note(title=titles["negative_equity"], sentences=equity_sentences))

    prior_borrow = sum_note_codes(prior, forms, "officer_borrowings").value
    current_borrow = sum_note_codes(current, forms, "officer_borrowings").value
    if (prior_borrow or Decimal(0)) > 0 or (current_borrow or Decimal(0)) > 0:
        sentences = [
            _sentence(
                policy,
                "officer_borrowings",
                py=py,
                cy=cy,
                prior=arg_won(prior_borrow),
                current=arg_won(current_borrow),
                change=arg_won(subtract(current_borrow, prior_borrow)),
            )
        ]
        status = ValueStatus.CONFIRMED
        if current_negative and current_equity is not None:
            status = ValueStatus.ASSUMED
            sentences.append(
                _sentence(
                    policy,
                    "officer_borrowings_settle",
                    cy=cy,
                    equity=arg_won(current_equity),
                    needed=arg_won(abs(current_equity)),
                )
            )
        notes.append(Note(title=titles["officer_borrowings"], sentences=sentences, status=status))

    current_revenue = statement_value(current, forms, "income", "revenue").value
    current_loans = sum_note_codes(current, forms, "officer_loans").value
    if (current_loans or Decimal(0)) > 0:
        notes.append(
            Note(
                title=titles["officer_loans"],
                sentences=[
                    _sentence(
                        policy,
                        "officer_loans",
                        cy=cy,
                        current=arg_won(current_loans),
                        ratio=arg_percent(percent_of(current_loans, current_revenue, 2), 2),
                    )
                ],
            )
        )

    prior_receivable = sum_note_codes(prior, forms, "receivables").value
    current_receivable = sum_note_codes(current, forms, "receivables").value
    if (prior_receivable or Decimal(0)) > 0 or (current_receivable or Decimal(0)) > 0:
        sentences = [
            _sentence(
                policy,
                "receivables",
                py=py,
                cy=cy,
                prior=arg_won(prior_receivable),
                current=arg_won(current_receivable),
                ratio=arg_percent(percent_of(current_receivable, current_revenue, 2), 2),
            )
        ]
        partners = [
            b
            for b in current.schedules.for_account(forms.schedules.accounts.get("receivables", []))
            if (b.amount.value or Decimal(0)) != 0
        ]
        if partners:
            sentences.append(_sentence(policy, "receivables_partners", cy=cy, count=arg_count(len(partners))))
        notes.append(Note(title=titles["receivables"], sentences=sentences))

    ratio_row = next((row for row in key_metrics if row.kind is MetricKind.RATIO), None)
    if ratio_row is not None:
        decimals = policy.debt_ratio.decimals
        notes.append(
            Note(
                title=titles["debt_ratio"],
                sentences=[
                    _sentence(
                        policy,
                        "debt_ratio_note",
                        py=py,
                        cy=cy,
                        prior=arg_metric(ratio_row, "prior", decimals),
                        current=arg_metric(ratio_row, "current", decimals),
                    )
                ],
                status=ValueStatus.ASSUMED,
            )
        )
    return notes
