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
    if label and label[0] in _ROMAN:
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


def parse_standard_form(
    doc: PdfDocument, pages: Sequence[int], form_name: str
) -> tuple[dict[str, LineItem], list[str]]:
    """서식의 모든 «항목명·코드·금액» 줄을 읽어 코드 → 항목으로 돌려준다."""
    # 항목명, 코드, 금액 원문, 쪽, 읽지 못한 이유(읽었거나 빈 칸이면 None)
    entries: list[tuple[str, str, str, int, str | None]] = []
    for number in pages:
        page = doc.page(number)
        halves: tuple[list[_Cell], list[_Cell]] = ([], [])
        for row in page.rows:
            for label, code, raw, code_word, cell_words in split_row_cells(row):
                half = halves[0] if code_word.x0 < page.width / 2 else halves[1]
                half.append(_Cell(label, code, raw, code_word, cell_words, row))
        for half in halves:
            entries.extend(
                (cell.label, cell.code, cell.raw, number, reason)
                for cell, reason in _unreadable_reasons(half, page.shades)
            )

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
