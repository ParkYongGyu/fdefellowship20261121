from __future__ import annotations

from decimal import Decimal

import pytest

from taxreport.extract.reader import Word, despace, parse_comma_amount
from taxreport.extract.standard_forms import parse_amount, split_row


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("21:224:676", Decimal(21224676)),
        (":2:021:410", Decimal(2021410)),
        ("-15:385:718", Decimal(-15385718)),
        (":::62:045:447", Decimal(62045447)),
        (": : : :165:000", Decimal(165000)),
        (":::::14", Decimal(14)),
        (":", None),
        (": : : : :", None),
    ],
)
def test_parse_amount(text: str, expected: Decimal | None) -> None:
    assert parse_amount(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("2,719,623", Decimal(2719623)),
        ("-15,510,353", Decimal(-15510353)),
        ("9", Decimal(9)),
        ("2025.01.01", None),
        ("999-88-00000", None),
        ("11O", None),
    ],
)
def test_parse_comma_amount(text: str, expected: Decimal | None) -> None:
    assert parse_comma_amount(text) == expected


def _row(text: str) -> list[Word]:
    return [Word(text=t, x0=float(i * 10), x1=float(i * 10 + 8), top=0.0) for i, t in enumerate(text.split())]


def test_split_row_reads_both_columns() -> None:
    row = _row("Ⅰ. 유동자산 1 : : : 62:045:447 (3) 기타유동자산 67 : : : : :")
    parsed = [(label, code, parse_amount(raw)) for label, code, raw, _ in split_row(row)]
    assert parsed == [("Ⅰ.유동자산", "1", Decimal(62045447)), ("(3)기타유동자산", "67", None)]


def test_split_row_negative_and_small_trailing_amount() -> None:
    row = _row("Ⅴ.영업손익 129 : : : -15:385:718 28.기타잡이익 174 : : : : : 14")
    parsed = [(code, parse_amount(raw)) for _, code, raw, _ in split_row(row)]
    assert parsed == [("129", Decimal(-15385718)), ("174", Decimal(14))]


def test_split_row_blank_label_in_right_column_is_a_code_not_an_amount() -> None:
    row = _row("8. 39 : : : : : 75 : : : :1:000")
    parsed = [(label, code, parse_amount(raw)) for label, code, raw, _ in split_row(row)]
    assert parsed == [("8.", "39", None), ("", "75", Decimal(1000))]


def test_split_row_ignores_lines_without_amount_cells() -> None:
    assert split_row(_row("사업자등록번호 999-88-00000")) == []
    assert split_row(_row("계 정 과 목 코드 금 액")) == []
    assert split_row(_row("32")) == []


def test_despace() -> None:
    assert despace("매   출   액") == "매출액"
