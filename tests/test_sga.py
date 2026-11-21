from __future__ import annotations

from decimal import Decimal

from taxreport.analyze.sga import build_sga_rows, sga_candidates
from taxreport.config.loader import FormsConfig, Policy
from taxreport.domain.models import CompanyCase, SgaRowKind


def test_top_five_plus_other_equals_total(case: CompanyCase, forms: FormsConfig, policy: Policy) -> None:
    rows = build_sga_rows(case, forms, policy)
    items = [row for row in rows if row.kind is SgaRowKind.ITEM]
    other = next(row for row in rows if row.kind is SgaRowKind.OTHER)
    total = rows[-1]
    assert total.kind is SgaRowKind.TOTAL
    assert len(items) == 5
    assert len(rows) == 7
    for side in ("prior", "current"):
        parts = sum((getattr(row, side).value for row in [*items, other]), Decimal(0))
        assert parts == getattr(total, side).value
    assert total.current.value == Decimal(143_166_567)
    assert other.current.value == Decimal(5_485_389)


def test_items_are_sorted_by_current_amount(case: CompanyCase, forms: FormsConfig, policy: Policy) -> None:
    items = [row for row in build_sga_rows(case, forms, policy) if row.kind is SgaRowKind.ITEM]
    assert [row.code for row in items] == ["68", "124", "108", "79", "80"]
    amounts = [row.current.value for row in items]
    assert amounts == sorted(amounts, reverse=True)


def test_child_items_are_not_double_counted(case: CompanyCase, forms: FormsConfig, policy: Policy) -> None:
    """1.급여(68)와 그 아래 나.직원급여(70)가 같은 금액이어도 상위 항목만 후보다."""
    candidates = {item.code for item in sga_candidates(case.current.income_statement, "67")}
    assert "68" in candidates
    assert "70" not in candidates
    codes = [row.code for row in build_sga_rows(case, forms, policy)]
    assert "70" not in codes


def test_form_item_named_other_is_distinguished(case: CompanyCase, forms: FormsConfig, policy: Policy) -> None:
    rows = build_sga_rows(case, forms, policy)
    labels = [row.label for row in rows]
    assert "기타(표준손익계산서 37.기타)" in labels
    assert labels.count("기타") == 1
    assert labels[-1] == "판매비와관리비 합계"


def test_shares_and_changes(case: CompanyCase, forms: FormsConfig, policy: Policy) -> None:
    rows = {row.code: row for row in build_sga_rows(case, forms, policy)}
    salary = rows["68"]
    assert salary.change == Decimal(12_558_970)
    assert salary.prior_share == Decimal("40.63")
    assert salary.current_share == Decimal("26.11")
    assert rows["67"].current_share == Decimal("74.28")
    assert rows["67"].composition == Decimal("100.00")


def test_prior_amount_is_zero_when_code_absent(case: CompanyCase, forms: FormsConfig, policy: Policy) -> None:
    del case.prior.income_statement["108"]
    rows = {row.code: row for row in build_sga_rows(case, forms, policy)}
    assert rows["108"].prior.value == Decimal(0)
    assert rows["108"].change == Decimal(23_996_690)
