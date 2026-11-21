"""추출값 사이의 산식 검증. 어긋나면 경고만 남기고 값은 고치지 않는다."""

from __future__ import annotations

from decimal import Decimal

from taxreport.config.loader import FormsConfig
from taxreport.domain.models import LineItem, Schedules, TaxSummary


def _value(items: dict[str, LineItem], code: str | None) -> Decimal | None:
    if code is None:
        return None
    item = items.get(code)
    return item.amount.value if item is not None else None


def _compare(name: str, expected: Decimal | None, actual: Decimal | None) -> str | None:
    if expected is None or actual is None:
        return None
    if expected == actual:
        return None
    return f"검증 불일치: {name} (계산 {expected:,}원 / 서식 {actual:,}원)"


def run_checks(
    balance_sheet: dict[str, LineItem],
    income_statement: dict[str, LineItem],
    tax: TaxSummary,
    schedules: Schedules,
    forms: FormsConfig,
) -> list[str]:
    bs = forms.standard_forms.balance_sheet
    pl = forms.standard_forms.income_statement
    results: list[str | None] = []

    def b(key: str) -> Decimal | None:
        return _value(balance_sheet, bs.codes.get(key))

    def p(key: str) -> Decimal | None:
        return _value(income_statement, pl.codes.get(key))

    def minus(left: Decimal | None, right: Decimal | None) -> Decimal | None:
        return None if left is None or right is None else left - right

    def plus(left: Decimal | None, right: Decimal | None) -> Decimal | None:
        return None if left is None or right is None else left + right

    results.append(
        _compare(
            "자산총계 = 부채총계 + 자본총계",
            plus(b("total_liabilities"), b("total_equity")),
            b("total_assets"),
        )
    )
    results.append(
        _compare("매출액 - 매출원가 = 매출총손익", minus(p("revenue"), p("cogs")), p("gross_profit"))
    )
    results.append(
        _compare(
            "매출총손익 - 판매비와관리비 = 영업손익",
            minus(p("gross_profit"), p("sga_total")),
            p("operating_income"),
        )
    )
    results.append(
        _compare(
            "법인세비용차감전손익 - 법인세비용 = 당기순손익",
            minus(p("pretax_income"), p("income_tax")),
            p("net_income"),
        )
    )
    sga_code = pl.codes.get("sga_total")
    if sga_code is not None and sga_code in income_statement:
        details = [
            item.amount.value
            for item in income_statement.values()
            if item.parent_code == sga_code and item.amount.value is not None
        ]
        if details:
            results.append(
                _compare("판관비 세부 항목 합 = 판매비와관리비", sum(details, Decimal(0)), p("sga_total"))
            )
    tax_net_income = tax.values.get("net_income")
    if tax_net_income is not None:
        results.append(
            _compare(
                "세액조정계산서의 결산서상 당기순손익 = 표준손익계산서의 당기순손익",
                tax_net_income.value,
                p("net_income"),
            )
        )
    for key, keywords in forms.schedules.accounts.items():
        rows = schedules.for_account(keywords)
        codes = bs.note_codes.get(key, [])
        if not rows or not codes:
            continue
        schedule_sum = sum((r.amount.value or Decimal(0) for r in rows), Decimal(0))
        form_values = [_value(balance_sheet, code) for code in codes]
        form_sum = sum((v for v in form_values if v is not None), Decimal(0))
        results.append(
            _compare(
                f"부속명세서 {'·'.join(keywords)} 거래처별 합계 = 표준재무상태표 코드 {'·'.join(codes)}",
                schedule_sum,
                form_sum,
            )
        )
    return [message for message in results if message is not None]
