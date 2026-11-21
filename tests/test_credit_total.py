"""읽은 공제·감면세액의 합이 «산출세액 - 총부담세액»과 다르면 알린다."""

from __future__ import annotations

from decimal import Decimal

from taxreport.domain.models import SourcedValue, TaxCredit, TaxSummary, ValueStatus
from taxreport.extract.intake import check_credit_total


def _value(amount: int) -> SourcedValue:
    return SourcedValue(value=Decimal(amount), status=ValueStatus.CONFIRMED)


def _tax(calculated: int, total: int) -> TaxSummary:
    return TaxSummary(values={"calculated_tax": _value(calculated), "total_tax": _value(total)})


def _credit(amount: int) -> TaxCredit:
    return TaxCredit(name="세액감면", legal_basis=None, code="112", amount=_value(amount))


def test_no_warning_when_credits_explain_the_gap() -> None:
    assert check_credit_total(_tax(25_032_689, 12_448_304), [_credit(8_272_060), _credit(4_312_325)], "합계표") == []


def test_warns_when_credits_were_not_read() -> None:
    warnings = check_credit_total(_tax(25_032_689, 12_448_304), [], "합계표")
    assert len(warnings) == 1
    assert "12,584,385" in warnings[0]


def test_no_warning_without_tax_values() -> None:
    assert check_credit_total(TaxSummary(values={}), [_credit(100)], "합계표") == []
