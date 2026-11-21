"""쪽마다 어떤 서식인지 제목으로 식별한다."""

from __future__ import annotations

import re
from dataclasses import dataclass

import structlog

from taxreport.config.loader import FormsConfig
from taxreport.domain.models import FormRef
from taxreport.extract.reader import PdfDocument

log = structlog.get_logger(__name__)


@dataclass(frozen=True)
class FormIndex:
    page_forms: dict[int, str]  # 쪽 → 서식명 (식별하지 못한 쪽은 없음)
    unknown_name: str

    def pages(self, form_name: str) -> list[int]:
        return [page for page, name in sorted(self.page_forms.items()) if name == form_name]

    def has(self, form_name: str) -> bool:
        return bool(self.pages(form_name))

    def refs(self) -> list[FormRef]:
        """발견한 서식 목록(등록한 서식만, 처음 나온 쪽 순서)."""
        grouped: dict[str, list[int]] = {}
        for page, name in sorted(self.page_forms.items()):
            if name == self.unknown_name:
                continue
            grouped.setdefault(name, []).append(page)
        return [FormRef(name=name, pages=pages) for name, pages in grouped.items()]


def build_index(doc: PdfDocument, forms: FormsConfig) -> FormIndex:
    rules = forms.index
    toc = re.compile(rules.toc_pattern)
    generic = [re.compile(p) for p in rules.generic_start_patterns]
    compiled = [(rule.name, [re.compile(p) for p in rule.patterns]) for rule in forms.forms]

    page_forms: dict[int, str] = {}
    previous: str | None = None
    for page in doc.pages:
        head = page.head(rules.head_lines)
        current: str | None
        if not head:
            current = None
        elif toc.search(head):
            current = None  # 목차에는 모든 서식 제목이 나오므로 건너뛴다
        else:
            current = next(
                (name for name, patterns in compiled if any(p.search(head) for p in patterns)),
                None,
            )
            if current is None:
                if any(p.search(head) for p in generic):
                    current = rules.unknown_form_name
                else:
                    current = previous  # 제목이 없는 쪽은 앞 서식의 이어지는 쪽이다
        if current is not None:
            page_forms[page.number] = current
        previous = current
    index = FormIndex(page_forms=page_forms, unknown_name=rules.unknown_form_name)
    log.info("form_index_built", file=doc.file_name, forms=len(index.refs()))
    return index
