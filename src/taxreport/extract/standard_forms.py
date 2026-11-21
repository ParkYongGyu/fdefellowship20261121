"""표준재무상태표·표준손익계산서 추출. 재무 숫자의 유일한 출처다."""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

import structlog

from taxreport.domain.models import LineItem, Source, SourcedValue, ValueStatus
from taxreport.extract.reader import PdfDocument, Shade, Word

log = structlog.get_logger(__name__)

_CODE_RE = re.compile(r"^\d{1,3}$")
_CELL_CHARS = set("-:0123456789")
_ROMAN = "ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩⅪⅫ"
_LEVEL_PATTERNS: tuple[tuple[int, re.Pattern[str]], ...] = (
    (1, re.compile(r"^\(\d+\)")),
    (2, re.compile(r"^\d+\.")),
    (3, re.compile(r"^[가-힣]\.")),
)
_ASCII_ROMAN_RE = re.compile(r"^[IVX]+\.")
_TRAILING_CODE_RE = re.compile(r"^(.*\D)(\d{1,3})$")
CODE_HEADER = "코드"
COLON_LAYOUT_MIN_CELLS = 3
CODE_BAND_LEFT = 20.0
CODE_BAND_RIGHT = 30.0
BLANK_NOTE = "빈 칸"
MASKED_NOTE = "금액 칸이 회색으로 가려져 있어 읽지 못했습니다"
MARKER_NOTE = "금액 대신 «{marker}»라고 적혀 있어 읽지 못했습니다"
INCOMPLETE_NOTE = "금액 칸이 일부만 인쇄되어 있어 읽지 못했습니다"


def parse_amount(text: str) -> Decimal | None:
    """칸 구분 기호 `:`가 섞인 금액을 숫자로 바꾼다. 빈 칸(`:`만 있음)은 None.

    `21:224:676` → 21224676, `:2:021:410` → 2021410, `-15:385:718` → -15385718
    """
    compact = re.sub(r"\s+", "", text)
    digits = re.sub(r"\D", "", compact)
    if not digits:
        return None
    value = Decimal(digits)
    return -value if "-" in compact else value


def _is_cell(text: str) -> bool:
    return ":" in text and set(text) <= _CELL_CHARS


def _level(label: str) -> int | None:
    if label and (label[0] in _ROMAN or _ASCII_ROMAN_RE.match(label)):
        return 0
    for level, pattern in _LEVEL_PATTERNS:
        if pattern.match(label):
            return level
    return None


def split_row(words: Sequence[Word]) -> list[tuple[str, str, str, Word]]:
    """한 줄에서 (항목명, 코드, 금액 원문, 코드 단어)를 차례로 뽑는다. 좌우 두 단이면 둘이 나온다."""
    return [(label, code, raw, code_word) for label, code, raw, code_word, _ in split_row_cells(words)]


def split_row_cells(words: Sequence[Word]) -> list[tuple[str, str, str, Word, tuple[Word, ...]]]:
    """split_row와 같되, 금액 칸을 이루는 단어들도 함께 돌려준다."""
    found: list[tuple[str, str, str, Word, tuple[Word, ...]]] = []
    count = len(words)
    label_start = 0
    i = 0
    while i < count:
        is_code = bool(_CODE_RE.match(words[i].text))
        if is_code and i + 1 < count and _is_cell(words[i + 1].text):
            j = i + 1
            while j < count and _is_cell(words[j].text):
                j += 1
            # 금액이 작으면 마지막 칸의 숫자가 `:` 없이 따로 나온다(예: `: : : : : 14`).
            # 뒤에 금액 칸이 또 이어지면 그 숫자는 다음 단의 코드다.
            trailing_digit = j < count and bool(_CODE_RE.match(words[j].text))
            if trailing_digit and not (j + 1 < count and _is_cell(words[j + 1].text)):
                j += 1
            label = "".join(word.text for word in words[label_start:i])
            raw = "".join(word.text for word in words[i + 1 : j])
            found.append((label, words[i].text, raw, words[i], tuple(words[i + 1 : j])))
            label_start = j
            i = j
        else:
            i += 1
    return found


def code_bands(page_rows: Sequence[Sequence[Word]]) -> list[tuple[float, float]]:
    """표 머리글의 «코드» 위치로 코드 열의 가로 범위를 잡는다(좌우 두 단이면 둘)."""
    for row in page_rows:
        headers = [word for word in row if word.text == CODE_HEADER]
        if headers:
            return [(word.x0 - CODE_BAND_LEFT, word.x1 + CODE_BAND_RIGHT) for word in headers]
    return []


def _rows_below_header(page_rows: Sequence[Sequence[Word]]) -> Sequence[Sequence[Word]]:
    for index, row in enumerate(page_rows):
        if any(word.text == CODE_HEADER for word in row):
            return page_rows[index + 1 :]
    return page_rows


def _in_band(word: Word, bands: Sequence[tuple[float, float]]) -> bool:
    return any(low <= word.x0 and word.x1 <= high for low, high in bands)


def split_row_plain(
    words: Sequence[Word], bands: Sequence[tuple[float, float]]
) -> list[tuple[str, str, str, Word, tuple[Word, ...]]]:
    """금액이 `1,234,567`처럼 쉼표 숫자로 인쇄된 서식의 한 줄을 읽는다.

    «항목명 코드 [금액]»이 좌우로 이어진다. 금액이 없으면 아무것도 인쇄되지 않는다.
    코드는 머리글 «코드» 아래 열에 있는 1~3자리 숫자다. 항목명 끝에 코드가 붙어 나온
    단어(`…처분손실185`)는 나눠서 본다.
    """
    normalized: list[Word] = []
    for word in words:
        glued = _TRAILING_CODE_RE.match(word.text)
        if glued and any(low <= word.x1 - 1 <= high for low, high in bands) and not _in_band(word, bands):
            cut = word.x1 - 6.0 * len(glued.group(2))
            normalized.append(Word(text=glued.group(1), x0=word.x0, x1=cut, top=word.top))
            normalized.append(Word(text=glued.group(2), x0=cut, x1=word.x1, top=word.top))
        else:
            normalized.append(word)
    found: list[tuple[str, str, str, Word, tuple[Word, ...]]] = []
    label_start = 0
    for i, word in enumerate(normalized):
        if not (_CODE_RE.match(word.text) and _in_band(word, bands)):
            continue
        label = "".join(item.text for item in normalized[label_start:i])
        if not label:
            continue
        amount_words: tuple[Word, ...] = ()
        if i + 1 < len(normalized) and parse_plain_amount(normalized[i + 1].text) is not None:
            following = normalized[i + 1]
            if not _in_band(following, bands):
                amount_words = (following,)
        raw = amount_words[0].text if amount_words else ""
        found.append((label, word.text, raw, word, amount_words))
        label_start = i + 1 + len(amount_words)
    return found


def parse_plain_amount(text: str) -> Decimal | None:
    """`1,234,567`, `-6,340`, `950` 형태의 금액. 형식이 다르면 None."""
    if re.fullmatch(r"-?\d{1,3}(,\d{3})*", text):
        return Decimal(text.replace(",", ""))
    return None


@dataclass(frozen=True)
class _Cell:
    label: str
    code: str
    raw: str
    code_word: Word
    cell_words: tuple[Word, ...]
    row: Sequence[Word]


def _unreadable_reasons(
    cells: Sequence[_Cell], shades: Sequence[Shade]
) -> list[tuple[_Cell, str | None]]:
    """한 단(좌 또는 우)의 금액 칸마다 읽지 못한 이유를 붙인다. 읽었거나 온전한 빈 칸이면 None.

    온전한 빈 칸은 칸 구분 기호가 모두 인쇄돼 있다(`: : : : :`). 구분 기호가 모자라고
    숫자도 없으면 금액이 가려졌거나 지워진 칸이다.
    """
    if not cells:
        return []
    # 숫자가 든 칸은 자릿수 구분에도 `:`를 쓰므로, 빈 칸끼리만 비교해 가장 흔한 개수를 기준으로 삼는다.
    blank_counts = Counter(
        cell.raw.count(":") for cell in cells if not any(char.isdigit() for char in cell.raw)
    )
    full = blank_counts.most_common(1)[0][0] if blank_counts else 0
    right_edge = max(word.x1 for cell in cells for word in cell.cell_words)
    results: list[tuple[_Cell, str | None]] = []
    for cell in cells:
        has_digits = any(char.isdigit() for char in cell.raw)
        if has_digits or cell.raw.count(":") >= full:
            results.append((cell, None))
            continue
        cell_left = cell.code_word.x1
        last = cell.cell_words[-1]
        markers = [
            word.text
            for word in cell.row
            if last.x1 <= word.x0 <= right_edge + 2 and not _is_cell(word.text)
        ]
        if any(shade.covers(cell_left, right_edge + 2, cell.code_word.top) for shade in shades):
            reason = MASKED_NOTE
        elif markers:
            reason = MARKER_NOTE.format(marker=" ".join(markers))
        else:
            reason = INCOMPLETE_NOTE
        results.append((cell, reason))
    return results


def _plain_unreadable_reasons(
    cells: Sequence[_Cell], shades: Sequence[Shade], bands: Sequence[tuple[float, float]]
) -> list[tuple[_Cell, str | None]]:
    """쉼표 숫자 서식: 금액이 없는 칸을 회색 도형이 덮고 있으면 가려진 칸으로 본다."""
    results: list[tuple[_Cell, str | None]] = []
    for cell in cells:
        reason: str | None = None
        if not cell.raw:
            later = [low for low, _ in bands if low > cell.code_word.x1]
            right = min(later) if later else cell.code_word.x1 + 130.0
            if any(shade.covers(cell.code_word.x1, right, cell.code_word.top) for shade in shades):
                reason = MASKED_NOTE
        results.append((cell, reason))
    return results


def parse_standard_form(
    doc: PdfDocument, pages: Sequence[int], form_name: str
) -> tuple[dict[str, LineItem], list[str]]:
    """서식의 모든 «항목명·코드·금액» 줄을 읽어 코드 → 항목으로 돌려준다."""
    # 항목명, 코드, 금액 원문, 쪽, 읽지 못한 이유(읽었거나 빈 칸이면 None)
    entries: list[tuple[str, str, str, int, str | None]] = []
    for number in pages:
        page = doc.page(number)
        halves: tuple[list[_Cell], list[_Cell]] = ([], [])
        # 금액 칸을 `:`로 나눠 인쇄하는 서식과 쉼표 숫자로 인쇄하는 서식이 있다.
        # «(단위 : 원)»의 `:` 하나는 금액 칸이 아니므로 개수로 판단한다.
        cell_count = sum(1 for row in page.rows for word in row if _is_cell(word.text))
        colon_layout = cell_count >= COLON_LAYOUT_MIN_CELLS
        bands = [] if colon_layout else code_bands(page.rows)
        # 쉼표 숫자 서식은 «코드» 머리글 아래 줄만 읽는다(위쪽 제목의 숫자를 코드로 보지 않기 위해).
        body_rows = page.rows if colon_layout else _rows_below_header(page.rows)
        for row in body_rows:
            pieces = split_row_cells(row) if colon_layout else split_row_plain(row, bands)
            for label, code, raw, code_word, cell_words in pieces:
                half = halves[0] if code_word.x0 < page.width / 2 else halves[1]
                half.append(_Cell(label, code, raw, code_word, cell_words, row))
        for half in halves:
            reasons = (
                _unreadable_reasons(half, page.shades)
                if colon_layout
                else _plain_unreadable_reasons(half, page.shades, bands)
            )
            entries.extend((cell.label, cell.code, cell.raw, number, reason) for cell, reason in reasons)

    items: dict[str, LineItem] = {}
    warnings: list[str] = []
    stack: list[tuple[int, str]] = []
    for label, code, raw, number, unreadable in entries:
        if code in items:
            warnings.append(f"{form_name} {number}쪽: 코드 {code}가 두 번 나와 처음 값만 썼습니다")
            continue
        level = _level(label)
        if level is None:
            parent = stack[-1][1] if stack else None
        else:
            while stack and stack[-1][0] >= level:
                stack.pop()
            parent = stack[-1][1] if stack else None
            stack.append((level, code))
        value = parse_amount(raw)
        source = Source(file=doc.file_name, form=form_name, page=number, code=code, label=label)
        if unreadable is not None:
            # 가려졌거나 일부만 인쇄된 칸은 0이 아니다. 값 없이 «확인 필요»로 둔다.
            amount = SourcedValue(
                value=None, status=ValueStatus.NEEDS_CHECK, source=source, note=unreadable
            )
            warnings.append(
                f"{doc.file_name} «{form_name}» {number}쪽 {label}(코드 {code}): {unreadable} "
                "→ 보고서에는 «확인 필요»로 표시하고 이 값을 쓰는 계산은 보류했습니다"
            )
        else:
            amount = SourcedValue(
                value=value if value is not None else Decimal(0),
                status=ValueStatus.CONFIRMED,
                source=source,
                note=BLANK_NOTE if value is None else None,
            )
        items[code] = LineItem(code=code, label=label, parent_code=parent, amount=amount)
    log.info("standard_form_parsed", file=doc.file_name, form=form_name, items=len(items))
    return items, warnings
