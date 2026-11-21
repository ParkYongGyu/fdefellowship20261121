"""결산 부속명세서의 거래처별 기말 잔액. 특이사항에만 쓴다."""

from __future__ import annotations

import re

import structlog

from taxreport.config.loader import ScheduleRules
from taxreport.domain.models import ScheduleBalance, Schedules, Source, SourcedValue, ValueStatus
from taxreport.extract.reader import (
    PdfDocument,
    Row,
    despace,
    is_comma_amount,
    nearest_word,
    parse_comma_amount,
    row_text,
)

log = structlog.get_logger(__name__)

_ACCOUNT_CODE_RE = re.compile(r"^\d{3,4}$")
_PARTNER_CODE_RE = re.compile(r"^\d{4,6}$")


def extract_schedules(doc: PdfDocument, rules: ScheduleRules) -> Schedules:
    """«○○명세서» 제목 아래의 줄에서 «금액» 열(기말 잔액)에 있는 값만 읽는다.

    연도마다 열 구성이 다르다(금액 한 열 / 기초·증가·감소·금액). 증가·감소만 있고
    잔액이 빈 줄이 있으므로, 맨 오른쪽 숫자가 아니라 열 제목 위치로 잔액을 고른다.
    """
    title_pattern = re.compile(rules.title_pattern)
    asof_pattern = re.compile(rules.asof_pattern)
    balances: list[ScheduleBalance] = []
    title: str | None = None
    header: Row | None = None
    for page in doc.pages:
        rows = page.rows
        for position, row in enumerate(rows):
            text = despace(row_text(row))
            matched = title_pattern.match(text)
            following = despace(row_text(rows[position + 1])) if position + 1 < len(rows) else ""
            if matched and asof_pattern.match(following):
                title = matched.group(1)
                header = None
                continue
            if title is None:
                continue
            texts = [w.text for w in row]
            if rules.header_account in texts and rules.ending_header in texts:
                header = row
                continue
            if header is None or len(row) < 3 or not _ACCOUNT_CODE_RE.match(row[0].text):
                continue
            account_header = next(w for w in header if w.text == rules.header_account)
            columns = [w for w in header if w.x0 > account_header.x1]
            amounts = [w for w in row[2:] if is_comma_amount(w.text)]
            ending = None
            for word in amounts:
                column = nearest_word(columns, word, page.width)
                if column is not None and column.text == rules.ending_header:
                    ending = word
            if ending is None:
                continue
            names = [
                w.text
                for w in row[2:]
                if not is_comma_amount(w.text) and not _PARTNER_CODE_RE.match(w.text)
            ]
            partner = " ".join(names) or None
            account = row[1].text
            balances.append(
                ScheduleBalance(
                    schedule=title,
                    account=account,
                    partner=partner,
                    amount=SourcedValue(
                        value=parse_comma_amount(ending.text),
                        status=ValueStatus.CONFIRMED,
                        source=Source(
                            file=doc.file_name,
                            form=title,
                            page=page.number,
                            code=row[0].text,
                            label=f"{account} {partner}" if partner else account,
                        ),
                    ),
                )
            )
    log.info("schedules_extracted", file=doc.file_name, balances=len(balances))
    return Schedules(balances=balances)
