"""표준재무상태표·표준손익계산서 추출. 재무 숫자의 유일한 출처다."""

from __future__ import annotations

import re
from collections.abc import Sequence
from decimal import Decimal

import structlog

from taxreport.domain.models import LineItem, Source, SourcedValue, ValueStatus
from taxreport.extract.reader import PdfDocument, Word

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
    found: list[tuple[str, str, str, Word]] = []
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
            found.append((label, words[i].text, raw, words[i]))
            label_start = j
            i = j
        else:
            i += 1
    return found


def parse_standard_form(
    doc: PdfDocument, pages: Sequence[int], form_name: str
) -> tuple[dict[str, LineItem], list[str]]:
    """서식의 모든 «항목명·코드·금액» 줄을 읽어 코드 → 항목으로 돌려준다."""
    entries: list[tuple[str, str, str, int]] = []  # 항목명, 코드, 금액 원문, 쪽
    for number in pages:
        page = doc.page(number)
        left: list[tuple[str, str, str, int]] = []
        right: list[tuple[str, str, str, int]] = []
        for row in page.rows:
            for label, code, raw, code_word in split_row(row):
                target = left if code_word.x0 < page.width / 2 else right
                target.append((label, code, raw, number))
        entries.extend(left)
        entries.extend(right)

    items: dict[str, LineItem] = {}
    warnings: list[str] = []
    stack: list[tuple[int, str]] = []
    for label, code, raw, number in entries:
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
        items[code] = LineItem(
            code=code,
            label=label,
            parent_code=parent,
            amount=SourcedValue(
                value=value if value is not None else Decimal(0),
                status=ValueStatus.CONFIRMED,
                source=Source(
                    file=doc.file_name, form=form_name, page=number, code=code, label=label
                ),
                note=BLANK_NOTE if value is None else None,
            ),
        )
    log.info("standard_form_parsed", file=doc.file_name, form=form_name, items=len(items))
    return items, warnings
