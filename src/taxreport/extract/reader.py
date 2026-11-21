"""PDF를 쪽·줄·단어(좌표 포함)로 읽는다. 서식의 뜻은 해석하지 않는다."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

import pdfplumber
import structlog

log = structlog.get_logger(__name__)

ROW_TOLERANCE = 3.0
# 금액을 가린 회색 칸: 밝은 회색(흰색·검정 제외)으로 칠한 낮은 도형.
SHADE_MIN_GRAY = 0.85
SHADE_MAX_GRAY = 0.95
SHADE_MAX_HEIGHT = 14.0
SHADE_MIN_WIDTH = 8.0
SHADE_ROW_OFFSET = 4.0
SHADE_ROW_TOLERANCE = 6.0
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
class Shade:
    """회색으로 칠한 작은 영역. 금액을 가린 칸을 알아보는 데 쓴다."""

    x0: float
    x1: float
    top: float
    bottom: float

    def covers(self, x0: float, x1: float, top: float) -> bool:
        """가로 범위가 겹치고 세로로 같은 줄에 있으면 True."""
        middle = (self.top + self.bottom) / 2
        return self.x0 < x1 and self.x1 > x0 and abs(middle - (top + SHADE_ROW_OFFSET)) <= SHADE_ROW_TOLERANCE


@dataclass(frozen=True)
class PageText:
    number: int  # 1부터 시작
    width: float
    rows: tuple[Row, ...]
    shades: tuple[Shade, ...] = ()

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


_EMBEDDED_AMOUNT_RE = re.compile(r"(-?\d{1,3}(?:,\d{3})+)")


def split_glued(word: Word) -> list[Word]:
    """금액과 글자가 붙어 추출된 단어를 나눈다. 예: `150,934,713II.비유동자산` → 금액 + 항목명.

    글자 간격이 좁은 서식에서는 옆 칸의 내용이 한 단어로 붙어 나온다. 가로 위치는 글자 수에
    비례해 나눈다.
    """
    if "," not in word.text or is_comma_amount(word.text):
        return [word]
    parts = [part for part in _EMBEDDED_AMOUNT_RE.split(word.text) if part]
    if len(parts) < 2:
        return [word]
    total = sum(len(part) for part in parts)
    width = word.x1 - word.x0
    pieces: list[Word] = []
    offset = 0
    for part in parts:
        x0 = word.x0 + width * offset / total
        offset += len(part)
        pieces.append(Word(text=part, x0=x0, x1=word.x0 + width * offset / total, top=word.top))
    return pieces


def _group_rows(words: list[Word]) -> tuple[Row, ...]:
    rows: list[list[Word]] = []
    for word in sorted(words, key=lambda w: (w.top, w.x0)):
        if rows and word.top - rows[-1][-1].top <= ROW_TOLERANCE:
            rows[-1].append(word)
        else:
            rows.append([word])
    return tuple(tuple(sorted(row, key=lambda w: w.x0)) for row in rows)


def _gray_level(color: object) -> float | None:
    """칠 색이 회색이면 밝기(0~1), 아니면 None."""
    if isinstance(color, int | float):
        return float(color)
    if isinstance(color, list | tuple) and color:
        try:
            parts = [float(part) for part in color]
        except (TypeError, ValueError):
            return None
        if len(parts) == 1:
            return parts[0]
        if len(parts) == 3 and max(parts) - min(parts) < 0.02:
            return parts[0]
    return None


def _shades(page: Any) -> tuple[Shade, ...]:
    found: list[Shade] = []
    for shape in [*page.curves, *page.rects]:
        if not shape.get("fill"):
            continue
        level = _gray_level(shape.get("non_stroking_color"))
        if level is None or not SHADE_MIN_GRAY <= level <= SHADE_MAX_GRAY:
            continue
        x0, x1 = float(shape["x0"]), float(shape["x1"])
        top, bottom = float(shape["top"]), float(shape["bottom"])
        if x1 - x0 < SHADE_MIN_WIDTH or bottom - top > SHADE_MAX_HEIGHT:
            continue
        found.append(Shade(x0=x0, x1=x1, top=top, bottom=bottom))
    return tuple(found)


def read_pdf(path: Path) -> PdfDocument:
    if not path.is_file():
        raise PdfReadError(f"PDF 파일을 찾지 못했습니다: {path}")
    pages: list[PageText] = []
    try:
        with pdfplumber.open(path) as pdf:
            for number, page in enumerate(pdf.pages, start=1):
                raw = page.extract_words(x_tolerance=3, y_tolerance=3)
                words = [
                    piece
                    for item in raw
                    for piece in split_glued(
                        Word(
                            text=str(item["text"]),
                            x0=float(item["x0"]),
                            x1=float(item["x1"]),
                            top=float(item["top"]),
                        )
                    )
                ]
                pages.append(
                    PageText(
                        number=number,
                        width=float(page.width),
                        rows=_group_rows(words),
                        shades=_shades(page),
                    )
                )
    except PdfReadError:
        raise
    except Exception as exc:  # pdfplumber/pdfminer가 던지는 예외 종류가 여러 가지다
        raise PdfReadError(f"PDF를 읽지 못했습니다: {path.name} ({exc})") from exc
    if not any(page.rows for page in pages):
        raise PdfReadError(f"PDF에 글자 정보가 없습니다(스캔본일 수 있습니다): {path.name}")
    log.info("pdf_read", file=path.name, pages=len(pages))
    return PdfDocument(file_name=path.name, pages=tuple(pages))
