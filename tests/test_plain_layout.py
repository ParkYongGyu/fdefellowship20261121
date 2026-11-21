"""금액을 쉼표 숫자로 인쇄하는 서식(세무사랑)의 표준서식 읽기."""

from __future__ import annotations

from decimal import Decimal

from taxreport.domain.models import ValueStatus
from taxreport.extract.reader import PageText, PdfDocument, Shade, Word, split_glued
from taxreport.extract.standard_forms import MASKED_NOTE, parse_standard_form


def _row(top: float, *cells: tuple[str, float]) -> tuple[Word, ...]:
    return tuple(Word(text=text, x0=x0, x1=x0 + 6 * len(text), top=top) for text, x0 in cells)


HEADER = _row(10, ("계정과목", 60), ("코드", 178), ("금액", 240), ("계정과목", 340), ("코드", 439), ("금액", 500))


def _doc(rows: list[tuple[Word, ...]], shades: tuple[Shade, ...] = ()) -> PdfDocument:
    page = PageText(number=1, width=595.0, rows=(HEADER, *rows), shades=shades)
    return PdfDocument(file_name="plain.pdf", pages=(page,))


def test_reads_both_columns_and_blank_is_zero() -> None:
    rows = [
        _row(20, ("Ⅰ.매출액", 41), ("1", 185), ("2,801,777,825", 233), ("(7)지분법손실", 308), ("60", 443)),
        _row(30, ("1.상품매출", 47), ("2", 185), ("1,946,422,355", 233), ("Ⅲ.매출총손익", 302), ("66", 443), ("1,794,853,942", 494)),
    ]
    items, warnings = parse_standard_form(_doc(rows), [1], "표준손익계산서")
    assert items["1"].amount.value == Decimal("2801777825")
    assert items["66"].amount.value == Decimal("1794853942")
    assert items["60"].amount.value == Decimal(0)
    assert items["60"].amount.status is ValueStatus.CONFIRMED
    assert items["2"].parent_code == "1"
    assert warnings == []


def test_numbers_above_the_header_are_not_codes() -> None:
    title = _row(2, ("제3호의", 135), ("3", 180), ("서식", 195))
    page = PageText(number=1, width=595.0, rows=(title, HEADER, _row(20, ("Ⅰ.매출액", 41), ("1", 185), ("100,000", 233))))
    items, warnings = parse_standard_form(PdfDocument(file_name="p.pdf", pages=(page,)), [1], "표준손익계산서")
    assert list(items) == ["1"]
    assert warnings == []


def test_code_glued_to_label_is_split() -> None:
    rows = [_row(20, ("1.이자수익", 47), ("131", 180), ("65,993", 266), ("다.지분법적용투자주식처분손실185", 352))]
    items, _ = parse_standard_form(_doc(rows), [1], "표준손익계산서")
    assert "185" in items
    assert items["185"].label == "다.지분법적용투자주식처분손실"
    assert items["131"].amount.value == Decimal("65993")


def test_ascii_roman_heading_is_top_level() -> None:
    rows = [
        _row(20, ("가.외상매출금", 59), ("10", 168), ("150,934,713", 242)),
        _row(30, ("II.비유동자산", 41), ("80", 168), ("1,743,241,616", 233)),
    ]
    items, _ = parse_standard_form(_doc(rows), [1], "표준재무상태표")
    assert items["80"].parent_code is None


def test_masked_amount_in_plain_layout_needs_check() -> None:
    rows = [_row(20, ("Ⅰ.매출액", 41), ("1", 185))]
    shade = Shade(x0=230, x1=300, top=19, bottom=28)
    items, warnings = parse_standard_form(_doc(rows, (shade,)), [1], "표준손익계산서")
    assert items["1"].amount.value is None
    assert items["1"].amount.note == MASKED_NOTE
    assert len(warnings) == 1


def test_split_glued_amount_and_label() -> None:
    word = Word(text="150,934,713II.비유동자산", x0=242, x1=362, top=10)
    parts = split_glued(word)
    assert [part.text for part in parts] == ["150,934,713", "II.비유동자산"]
    assert parts[0].x0 == 242 and parts[1].x1 == 362
    assert split_glued(Word(text="1,234", x0=0, x1=10, top=0))[0].text == "1,234"
