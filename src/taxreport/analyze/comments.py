"""요약 코멘트. 정해 둔 문장 틀에 수치를 끼워 넣는다(LLM 호출 없음)."""

from __future__ import annotations

from decimal import Decimal

from taxreport.analyze.metrics import (
    arg_metric,
    arg_percent,
    arg_text,
    arg_won,
    arg_year,
    display_label,
    percent_of,
    statement_value,
)
from taxreport.config.loader import FormsConfig, Policy
from taxreport.domain.models import (
    CommentBlock,
    CompanyCase,
    ComparisonRow,
    Filing,
    LineItem,
    MetricKind,
    MetricRow,
    Sentence,
    SgaRow,
    SgaRowKind,
    TextArg,
)

NO_BASIS = "근거 법조항 미기재"


def _sentence(policy: Policy, key: str, **args: TextArg) -> Sentence:
    return Sentence(template=policy.template(key), args=args)


def _rate_arg(row: ComparisonRow, policy: Policy) -> TextArg:
    if row.rate_text is not None:
        return arg_text(row.rate_text)
    return arg_percent(row.change_rate, policy.comparison.rate_decimals)


def _children_with_amount(filing: Filing, parent_code: str | None) -> list[LineItem]:
    if parent_code is None:
        return []
    return [
        item
        for item in filing.income_statement.values()
        if item.parent_code == parent_code and (item.amount.value or Decimal(0)) != 0
    ]


def _largest_change[T: (ComparisonRow, SgaRow)](rows: list[T]) -> T | None:
    changed = [row for row in rows if row.change is not None and row.change != 0]
    if not changed:
        return None
    return max(changed, key=lambda row: abs(row.change or Decimal(0)))


def build_comments(
    case: CompanyCase,
    forms: FormsConfig,
    policy: Policy,
    key_metrics: list[MetricRow],
    income_rows: list[ComparisonRow],
    sga_rows: list[SgaRow],
) -> list[CommentBlock]:
    titles = policy.comments.titles
    current, prior = case.current, case.prior
    cy, py = arg_year(current.identity.fiscal_year), arg_year(prior.identity.fiscal_year)
    codes = forms.standard_forms.income_statement.codes
    by_label = {row.label: row for row in income_rows}
    labels = {row.key: row.label for row in policy.labels.income_comparison}
    blocks: list[CommentBlock] = []

    def income(key: str, filing: Filing) -> Decimal | None:
        return statement_value(filing, forms, "income", key).value

    # ① 매출 및 수익성
    sentences: list[Sentence] = []
    revenue_row = by_label.get(labels.get("revenue", ""))
    if revenue_row is not None:
        sentences.append(
            _sentence(
                policy,
                "revenue",
                cy=cy,
                py=py,
                current=arg_won(revenue_row.current.value),
                prior=arg_won(revenue_row.prior.value),
                change=arg_won(revenue_row.change),
                rate=_rate_arg(revenue_row, policy),
            )
        )
    sentences.append(
        _sentence(
            policy,
            "profit",
            cy=cy,
            py=py,
            prior_op=arg_won(income("operating_income", prior)),
            current_op=arg_won(income("operating_income", current)),
            prior_ni=arg_won(income("net_income", prior)),
            current_ni=arg_won(income("net_income", current)),
        )
    )
    current_revenue = income("revenue", current)
    sentences.append(
        _sentence(
            policy,
            "margins",
            cy=cy,
            op_margin=arg_percent(percent_of(income("operating_income", current), current_revenue, 2), 2),
            ni_margin=arg_percent(percent_of(income("net_income", current), current_revenue, 2), 2),
        )
    )
    blocks.append(CommentBlock(title=titles["profitability"], sentences=sentences))

    # ② 판관비 주요 항목
    sentences = []
    decimals = policy.sga.share_decimals
    total = next((row for row in sga_rows if row.kind is SgaRowKind.TOTAL), None)
    items = [row for row in sga_rows if row.kind is SgaRowKind.ITEM]
    if total is not None:
        sentences.append(
            _sentence(
                policy,
                "sga_total",
                cy=cy,
                py=py,
                current=arg_won(total.current.value),
                prior=arg_won(total.prior.value),
                change=arg_won(total.change),
                prior_share=arg_percent(total.prior_share, decimals),
                current_share=arg_percent(total.current_share, decimals),
            )
        )
    if items:
        first = items[0]
        sentences.append(
            _sentence(
                policy,
                "sga_top",
                label=arg_text(first.label),
                amount=arg_won(first.current.value),
                share=arg_percent(first.current_share, decimals),
            )
        )
    if len(items) >= 3:
        sentences.append(
            _sentence(
                policy,
                "sga_next",
                label2=arg_text(items[1].label),
                amount2=arg_won(items[1].current.value),
                label3=arg_text(items[2].label),
                amount3=arg_won(items[2].current.value),
            )
        )
    blocks.append(CommentBlock(title=titles["sga"], sentences=sentences))

    # ③ 영업외손익: 금액이 있는 계정명과 금액만 적는다(PDF에 사유가 없다)
    sentences = [
        _sentence(
            policy,
            "non_operating_total",
            cy=cy,
            py=py,
            income=arg_won(income("non_operating_income", current)),
            expense=arg_won(income("non_operating_expense", current)),
            prior_income=arg_won(income("non_operating_income", prior)),
            prior_expense=arg_won(income("non_operating_expense", prior)),
        )
    ]
    detail_count = 0
    for key in ("non_operating_income", "non_operating_expense"):
        for item in _children_with_amount(current, codes.get(key)):
            detail_count += 1
            sentences.append(
                _sentence(
                    policy,
                    "non_operating_item",
                    cy=cy,
                    category=arg_text(labels.get(key, key)),
                    label=arg_text(display_label(item.label)),
                    amount=arg_won(item.amount.value),
                )
            )
    if detail_count == 0:
        sentences.append(_sentence(policy, "non_operating_none", cy=cy))
    blocks.append(CommentBlock(title=titles["non_operating"], sentences=sentences))

    # ④ 전년 대비 주요 변동: 증감액 절댓값이 가장 큰 손익 항목과 판관비 항목
    sentences = []
    top_income = _largest_change(income_rows)
    if top_income is not None:
        sentences.append(
            _sentence(
                policy,
                "change_income",
                cy=cy,
                py=py,
                label=arg_text(top_income.label),
                change=arg_won(top_income.change),
                prior=arg_won(top_income.prior.value),
                current=arg_won(top_income.current.value),
            )
        )
    top_sga = _largest_change(items)
    if top_sga is not None:
        sentences.append(
            _sentence(
                policy,
                "change_sga",
                cy=cy,
                py=py,
                label=arg_text(top_sga.label),
                change=arg_won(top_sga.change),
                prior=arg_won(top_sga.prior.value),
                current=arg_won(top_sga.current.value),
            )
        )
    if sentences:
        blocks.append(CommentBlock(title=titles["changes"], sentences=sentences))

    # ⑤ 재무비율(부채비율)
    ratio_row = next((row for row in key_metrics if row.kind is MetricKind.RATIO), None)
    if ratio_row is not None:
        ratio_decimals = policy.debt_ratio.decimals
        blocks.append(
            CommentBlock(
                title=titles["ratios"],
                sentences=[
                    _sentence(
                        policy,
                        "debt_ratio",
                        cy=cy,
                        py=py,
                        prior=arg_metric(ratio_row, "prior", ratio_decimals),
                        current=arg_metric(ratio_row, "current", ratio_decimals),
                    ),
                    _sentence(
                        policy,
                        "debt_ratio_basis",
                        cy=cy,
                        liabilities=arg_won(statement_value(current, forms, "balance", "total_liabilities").value),
                        equity=arg_won(statement_value(current, forms, "balance", "total_equity").value),
                    ),
                ],
            )
        )

    # ⑥ 공제·감면
    sentences = []
    if current.credits:
        for credit in current.credits:
            sentences.append(
                _sentence(
                    policy,
                    "credit_item",
                    cy=cy,
                    name=arg_text(credit.name),
                    basis=arg_text(credit.legal_basis or NO_BASIS),
                    amount=arg_won(credit.amount.value),
                )
            )
        sentences.append(
            _sentence(
                policy,
                "credit_summary",
                calculated=arg_won(current.tax.get("calculated_tax").value),
                total=arg_won(current.tax.get("total_tax").value),
            )
        )
    elif current.has_form(forms.credits.form):
        sentences.append(_sentence(policy, "credit_none", cy=cy))
    else:
        sentences.append(_sentence(policy, "credit_no_form", cy=cy))
    blocks.append(CommentBlock(title=titles["credits"], sentences=sentences))
    return blocks
