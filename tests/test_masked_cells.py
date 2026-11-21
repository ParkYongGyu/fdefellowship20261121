"""가려졌거나 일부만 인쇄된 금액 칸은 0이 아니라 «확인 필요»로 읽는다."""

from __future__ import annotations

from decimal import Decimal

from taxreport.domain.models import ValueStatus
from taxreport.extract.reader import PageText, PdfDocument, Shade, Word
from taxreport.extract.standard_forms import (
    INCOMPLETE_NOTE,
    MASKED_NOTE,
    parse_standard_form,
)


def _row(top: float, *cells: tuple[str, float]) -> tuple[Word, ...]:
    return tuple(Word(text=text, x0=x0, x1=x0 + 6 * len(text), top=top) for text, x0 in cells)


def _blank(top: float, label: str, code: str) -> tuple[Word, ...]:
    return _row(top, (label, 10), (code, 100), (":", 120), (":", 130), (":", 140), (":", 150), (":", 160))


def _doc(rows: list[tuple[Word, ...]], shades: tuple[Shade, ...] = ()) -> PdfDocument:
    page = PageText(number=1, width=600.0, rows=tuple(rows), shades=shades)
    return PdfDocument(file_name="sample.pdf", pages=(page,))


def test_blank_cell_is_zero_and_confirmed() -> None:
    items, warnings = parse_standard_form(_doc([_blank(10, "가.항목", "4"), _blank(20, "나.항목", "5")]), [1], "표준손익계산서")
    assert items["4"].amount.value == Decimal(0)
    assert items["4"].amount.status is ValueStatus.CONFIRMED
    assert warnings == []


def test_amount_is_read() -> None:
    rows = [
        _blank(10, "가.항목", "4"),
        _row(20, ("Ⅰ.매출액", 10), ("1", 100), (":", 120), (":", 130), (":", 140), ("253:568:829", 150)),
    ]
    items, _ = parse_standard_form(_doc(rows), [1], "표준손익계산서")
    assert items["1"].amount.value == Decimal("253568829")


def test_partially_printed_cell_is_not_zero() -> None:
    rows = [
        _blank(10, "가.항목", "4"),
        _blank(20, "나.항목", "5"),
        _row(30, ("Ⅰ.매출액", 10), ("1", 100), (":", 120), (":", 130), (":", 140)),
    ]
    items, warnings = parse_standard_form(_doc(rows), [1], "표준손익계산서")
    amount = items["1"].amount
    assert amount.value is None
    assert amount.status is ValueStatus.NEEDS_CHECK
    assert amount.note == INCOMPLETE_NOTE
    assert amount.source is not None and amount.source.page == 1
    assert len(warnings) == 1 and "sample.pdf" in warnings[0] and "코드 1" in warnings[0]


def test_gray_masked_cell_is_reported_as_masked() -> None:
    rows = [
        _blank(10, "가.항목", "4"),
        _blank(20, "나.항목", "5"),
        _row(30, ("Ⅲ.매출총손익", 10), ("66", 100), (":", 120), (":", 130), (":", 140)),
    ]
    shade = Shade(x0=145, x1=170, top=29, bottom=38)
    items, _ = parse_standard_form(_doc(rows, (shade,)), [1], "표준손익계산서")
    assert items["66"].amount.value is None
    assert items["66"].amount.note == MASKED_NOTE


def test_text_instead_of_amount_is_reported() -> None:
    rows = [
        _blank(10, "가.항목", "4"),
        _blank(20, "나.항목", "5"),
        _row(30, ("Ⅳ.판매비와관리비", 10), ("67", 120), (":", 135), (":", 140), (":", 145), ("확인중", 155)),
    ]
    items, _ = parse_standard_form(_doc(rows), [1], "표준손익계산서")
    amount = items["67"].amount
    assert amount.value is None
    assert amount.note is not None and "확인중" in amount.note
