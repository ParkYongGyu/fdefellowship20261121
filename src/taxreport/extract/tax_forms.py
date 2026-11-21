"""세무 서식 추출: 세액조정계산서, 최저한세, 소득금액조정합계표, 공제감면, 결손금."""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import date
from decimal import Decimal

import structlog

from taxreport.config.loader import (
    AdjustmentRules,
    CreditRules,
    LossRules,
    MinimumTaxRules,
    TaxAdjustmentRules,
)
from taxreport.domain.models import (
    AdjustmentKind,
    LossRecord,
    Source,
    SourcedValue,
    TaxAdjustment,
    TaxCredit,
    ValueStatus,
)
from taxreport.extract.reader import (
    PageText,
    PdfDocument,
    Row,
    Word,
    despace,
    is_comma_amount,
    nearest_word,
    parse_comma_amount,
    row_text,
)

log = structlog.get_logger(__name__)

CODE_COLUMN_TOLERANCE = 6.0
HEADER_MATCH_DISTANCE = 15.0
CODE_HEADER_DISTANCE = 20.0
AMOUNT_GAP_RATIO = 0.16  # 코드와 금액 사이 간격의 상한(쪽 너비 대비). 넓히면 옆 단의 줄 번호를 금액으로 읽는다
BLANK_NOTE = "빈 칸"


def _confirmed(
    value: Decimal | None, doc: PdfDocument, form: str, page: int, code: str | None, label: str
) -> SourcedValue:
    return SourcedValue(
        value=value if value is not None else Decimal(0),
        status=ValueStatus.CONFIRMED,
        source=Source(file=doc.file_name, form=form, page=page, code=code, label=label),
        note=BLANK_NOTE if value is None else None,
    )


def _is_code_word(word: Word, code: str, anchor: Word) -> bool:
    """코드 열에 있는 코드 단어인가. 산식 괄호와 붙어 `)21`처럼 나온 경우도 받는다."""
    if word.text == code:
        return abs(word.x0 - anchor.x0) <= CODE_COLUMN_TOLERANCE
    if word.text.endswith(")" + code):
        return abs(word.x1 - anchor.x1) <= CODE_COLUMN_TOLERANCE
    return False


def extract_tax_summary(
    doc: PdfDocument, pages: Sequence[int], rules: TaxAdjustmentRules
) -> dict[str, SourcedValue]:
    """세액조정계산서의 왼쪽 단에서 코드 번호 옆 금액을 읽는다."""
    values: dict[str, SourcedValue] = {}
    for number in pages:
        page = doc.page(number)
        anchor = next(
            (w for row in page.rows for w in row if w.text == rules.anchor_code), None
        )
        if anchor is None:
            continue
        for key, code in rules.codes.items():
            if key in values:
                continue
            for row in page.rows:
                index = next(
                    (
                        i
                        for i, w in enumerate(row)
                        if _is_code_word(w, code, anchor)
                    ),
                    None,
                )
                if index is None:
                    continue
                amount: Decimal | None = None
                if index + 1 < len(row):
                    candidate = row[index + 1]
                    near = candidate.x0 - row[index].x1 < page.width * AMOUNT_GAP_RATIO
                    if near:
                        amount = parse_comma_amount(candidate.text)
                values[key] = _confirmed(
                    amount, doc, rules.form, number, code, rules.labels.get(key, key)
                )
                break
    log.info("tax_summary_extracted", file=doc.file_name, found=len(values))
    return values


def _find_header(page: PageText, code_header: str, pattern: re.Pattern[str]) -> Row | None:
    for row in page.rows:
        texts = [w.text for w in row]
        if code_header in texts and any(pattern.search(t) for t in texts):
            return row
    return None


def extract_minimum_tax(
    doc: PdfDocument, pages: Sequence[int], rules: MinimumTaxRules
) -> dict[str, SourcedValue]:
    values: dict[str, SourcedValue] = {}
    wanted_columns = {row.column for row in rules.rows.values()}
    for number in pages:
        page = doc.page(number)
        header = next(
            (
                row
                for row in page.rows
                if rules.code_header in [w.text for w in row]
                and wanted_columns & {w.text for w in row}
            ),
            None,
        )
        if header is None:
            continue
        code_header = next(w for w in header if w.text == rules.code_header)
        columns = [w for w in header if w.x0 > code_header.x1]
        for key, rule in rules.rows.items():
            if key in values:
                continue
            for row in page.rows:
                code_word = next(
                    (
                        w
                        for w in row
                        if w.text == rule.code
                        and abs(w.center - code_header.center) <= CODE_HEADER_DISTANCE
                    ),
                    None,
                )
                if code_word is None:
                    continue
                amount: Decimal | None = None
                for word in row:
                    if word.x0 <= code_word.x1 or not is_comma_amount(word.text):
                        continue
                    column = nearest_word(columns, word, page.width)
                    if column is not None and column.text == rule.column:
                        amount = parse_comma_amount(word.text)
                values[key] = _confirmed(
                    amount, doc, rules.form, number, rule.code, rules.labels.get(key, key)
                )
                break
    return values


_HAS_LETTER_RE = re.compile(r"[가-힣A-Za-z]")


def _parse_adjustment_half(words: Sequence[Word]) -> tuple[str, Decimal, str | None] | None:
    """(과목, 금액, 소득처분). 과목이 다른 줄에 있으면 과목은 빈 문자열로 돌려준다."""
    index = next((i for i, w in enumerate(words) if is_comma_amount(w.text)), None)
    if index is None:
        return None
    amount = parse_comma_amount(words[index].text)
    if amount is None:
        return None
    subject = "".join(w.text for w in words[:index])
    rest = [w.text for w in words[index + 1 :] if not is_comma_amount(w.text)]
    disposition = "".join(rest) or None
    return subject, amount, disposition


def extract_adjustments(
    doc: PdfDocument, pages: Sequence[int], rules: AdjustmentRules
) -> list[TaxAdjustment]:
    """소득금액조정합계표. 왼쪽 절반은 익금산입·손금불산입, 오른쪽 절반은 손금산입·익금불산입."""
    result: list[TaxAdjustment] = []
    for number in pages:
        page = doc.page(number)
        middle = page.width / 2
        in_body = False
        prefixes: dict[AdjustmentKind, str] = {}
        open_items: dict[AdjustmentKind, TaxAdjustment | None] = {}
        for row in page.rows:
            text = despace(row_text(row))
            if not in_body:
                in_body = all(keyword in text for keyword in rules.header_keywords)
                continue
            halves = (
                (AdjustmentKind.ADDITION, [w for w in row if w.x0 < middle]),
                (AdjustmentKind.DEDUCTION, [w for w in row if w.x0 >= middle]),
            )
            for kind, words in halves:
                parsed = _parse_adjustment_half(words)
                if parsed is None:
                    # 금액이 없는 줄: 과목이 여러 줄로 나뉜 경우의 앞·뒤 조각이다.
                    text = "".join(w.text for w in words)
                    if not text:
                        continue
                    waiting = open_items.get(kind)
                    if waiting is not None:
                        waiting.subject += text
                        if waiting.amount.source is not None:
                            waiting.amount.source.label = waiting.subject
                        open_items[kind] = None
                    else:
                        prefixes[kind] = text
                    continue
                subject, amount, disposition = parsed
                wrapped = subject == ""
                if wrapped and disposition is None:
                    # 금액만 있는 줄은 쪽 번호 같은 꼬리말이다. 조정 항목으로 보지 않는다.
                    prefixes[kind] = ""
                    continue
                if wrapped:
                    subject = prefixes.get(kind, "")
                prefixes[kind] = ""
                open_items[kind] = None
                if subject in rules.total_labels or not _HAS_LETTER_RE.search(subject):
                    continue
                result.append(
                    TaxAdjustment(
                        kind=kind,
                        subject=subject,
                        disposition=disposition,
                        amount=SourcedValue(
                            value=amount,
                            status=ValueStatus.CONFIRMED,
                            source=Source(
                                file=doc.file_name,
                                form=rules.form,
                                page=number,
                                code=None,
                                label=subject,
                            ),
                        ),
                    )
                )
                if wrapped:
                    open_items[kind] = result[-1]
    log.info("adjustments_extracted", file=doc.file_name, count=len(result))
    return result


def extract_credits(doc: PdfDocument, pages: Sequence[int], rules: CreditRules) -> list[TaxCredit]:
    """공제감면세액합계표(갑)에서 감면(공제)세액 칸에 금액이 있는 줄만 읽는다."""
    amount_header = re.compile(rules.amount_header_pattern)
    code_pattern = re.compile(rules.code_pattern)
    law_pattern = re.compile(rules.law_pattern)
    result: list[TaxCredit] = []
    header: Row | None = None
    for number in pages:
        page = doc.page(number)
        for row in page.rows:
            if rules.code_header in [w.text for w in row] and any(
                amount_header.search(w.text) for w in row
            ):
                header = row
                continue
            if header is None:
                continue
            code_header = next(w for w in header if w.text == rules.code_header)
            columns = [w for w in header if w.x0 > code_header.x1]
            code_word = next(
                (
                    w
                    for w in row
                    if code_pattern.match(w.text)
                    and abs(w.center - code_header.center) <= CODE_HEADER_DISTANCE
                ),
                None,
            )
            if code_word is None:
                continue
            amount: Decimal | None = None
            for word in row:
                if word.x0 <= code_word.x1 or not is_comma_amount(word.text):
                    continue
                column = nearest_word(columns, word, page.width)
                if column is not None and amount_header.search(column.text):
                    amount = parse_comma_amount(word.text)
            if amount is None:
                continue
            before = [w.text for w in row if w.x1 < code_word.x0]
            law: str | None = None
            if before and law_pattern.search(before[-1]):
                law = before.pop()
            name = "".join(before)
            if rules.row_number_pattern:
                name = re.sub(rules.row_number_pattern, "", name)
            if law is None and rules.glued_law_pattern:
                glued = re.match(rules.glued_law_pattern, name)
                if glued:
                    name, law = glued.group(1), glued.group(2)
            if any(skip in name for skip in rules.skip_labels):
                continue
            if not name:
                name = f"(구분명 확인 필요, 코드 {code_word.text})"
            result.append(
                TaxCredit(
                    name=name,
                    legal_basis=law,
                    code=code_word.text,
                    amount=SourcedValue(
                        value=amount,
                        status=ValueStatus.CONFIRMED,
                        source=Source(
                            file=doc.file_name,
                            form=rules.form,
                            page=number,
                            code=code_word.text,
                            label=name,
                        ),
                    ),
                )
            )
    log.info("credits_extracted", file=doc.file_name, count=len(result))
    return result


def extract_losses(doc: PdfDocument, pages: Sequence[int], rules: LossRules) -> list[LossRecord]:
    """자본금과적립금조정명세서(갑)의 사업연도별 결손금 줄을 읽는다."""
    row_pattern = re.compile(rules.row_pattern)
    result: list[LossRecord] = []
    for number in pages:
        page = doc.page(number)
        for row in page.rows:
            matched = row_pattern.match(row[0].text)
            if matched is None:
                continue
            year, month, day = (int(part) for part in matched.groups())
            amounts = [w for w in row[1:] if is_comma_amount(w.text)]
            above = [w for other in page.rows for w in other if w.top < row[0].top]
            picked: dict[str, SourcedValue] = {}
            for key, marker in rules.columns.items():
                headers = [w for w in above if marker in w.text]
                if not headers:
                    continue
                column = max(headers, key=lambda w: w.top)
                word = nearest_word(amounts, column, HEADER_MATCH_DISTANCE)
                if word is None:
                    continue
                value = parse_comma_amount(word.text)
                picked[key] = SourcedValue(
                    value=value,
                    status=ValueStatus.CONFIRMED,
                    source=Source(
                        file=doc.file_name,
                        form=rules.form,
                        page=number,
                        code=None,
                        label=f"결손금 {key} ({row[0].text})",
                    ),
                )
            incurred = picked.get("incurred")
            if incurred is None:
                continue
            result.append(
                LossRecord(
                    fiscal_year_end=date(year, month, day),
                    incurred=incurred,
                    used_current=picked.get("used_current"),
                    balance=picked.get("balance"),
                )
            )
    log.info("losses_extracted", file=doc.file_name, count=len(result))
    return result
