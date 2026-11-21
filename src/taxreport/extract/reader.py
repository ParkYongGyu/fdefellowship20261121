"""PDF를 쪽·줄·단어(좌표 포함)로 읽는다. 서식의 뜻은 해석하지 않는다."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

import pdfplumber
import structlog

log = structlog.get_logger(__name__)

ROW_TOLERANCE = 3.0
_SPACE_RE = re.compile(r"\s+")
_COMMA_AMOUNT_RE = re.compile(r"^-?\d{1,3}(,\d{3})*$")


class PdfReadError(Exception):
    """PDF를 열 수 없거나 글자를 읽을 수 없다."""


@dataclass(frozen=True)
class Word:
    text: str
    x0: float
    x1: float
    top: float

    @property
    def center(self) -> float:
        return (self.x0 + self.x1) / 2


Row = tuple[Word, ...]


@dataclass(frozen=True)
class PageText:
    number: int  # 1부터 시작
    width: float
    rows: tuple[Row, ...]

    @property
    def lines(self) -> list[str]:
        return [row_text(row) for row in self.rows]

    def head(self, line_count: int) -> str:
        """쪽 앞부분 글자를 공백 없이 이어 붙인 문자열."""
        return despace("".join(self.lines[:line_count]))


@dataclass(frozen=True)
class PdfDocument:
    file_name: str
    pages: tuple[PageText, ...]

    def page(self, number: int) -> PageText:
        return self.pages[number - 1]


def despace(text: str) -> str:
    return _SPACE_RE.sub("", text)


def row_text(row: Sequence[Word]) -> str:
    return " ".join(word.text for word in row)


def is_comma_amount(text: str) -> bool:
    return bool(_COMMA_AMOUNT_RE.match(text))


def parse_comma_amount(text: str) -> Decimal | None:
    """`1,234,567` / `-15,510,353` 형태를 숫자로 바꾼다. 형식이 다르면 None."""
    if not is_comma_amount(text):
        return None
    return Decimal(text.replace(",", ""))


def nearest_word(candidates: Sequence[Word], target: Word, max_distance: float) -> Word | None:
    """가로 중심이 target과 가장 가까운 단어. max_distance보다 멀면 None."""
    best: Word | None = None
    best_distance = max_distance
    for candidate in candidates:
        distance = abs(candidate.center - target.center)
        if distance <= best_distance:
            best = candidate
            best_distance = distance
    return best


def _group_rows(words: list[Word]) -> tuple[Row, ...]:
    rows: list[list[Word]] = []
    for word in sorted(words, key=lambda w: (w.top, w.x0)):
        if rows and word.top - rows[-1][-1].top <= ROW_TOLERANCE:
            rows[-1].append(word)
        else:
            rows.append([word])
    return tuple(tuple(sorted(row, key=lambda w: w.x0)) for row in rows)


def read_pdf(path: Path) -> PdfDocument:
    if not path.is_file():
        raise PdfReadError(f"PDF 파일을 찾지 못했습니다: {path}")
    pages: list[PageText] = []
    try:
        with pdfplumber.open(path) as pdf:
            for number, page in enumerate(pdf.pages, start=1):
                raw = page.extract_words(x_tolerance=3, y_tolerance=3)
                words = [
                    Word(
                        text=str(item["text"]),
                        x0=float(item["x0"]),
                        x1=float(item["x1"]),
                        top=float(item["top"]),
                    )
                    for item in raw
                ]
                pages.append(
                    PageText(number=number, width=float(page.width), rows=_group_rows(words))
                )
    except PdfReadError:
        raise
    except Exception as exc:  # pdfplumber/pdfminer가 던지는 예외 종류가 여러 가지다
        raise PdfReadError(f"PDF를 읽지 못했습니다: {path.name} ({exc})") from exc
    if not any(page.rows for page in pages):
        raise PdfReadError(f"PDF에 글자 정보가 없습니다(스캔본일 수 있습니다): {path.name}")
    log.info("pdf_read", file=path.name, pages=len(pages))
    return PdfDocument(file_name=path.name, pages=tuple(pages))
