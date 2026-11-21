"""접수: PDF에서 회사·사업연도를 읽고, 두 파일이 같은 회사의 연속 연도인지 검증한다."""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import structlog

from taxreport.config.loader import FormsConfig, IdentityRules
from taxreport.domain.models import (
    CompanyCase,
    CompanyIdentity,
    Filing,
    LineItem,
    TaxCredit,
    TaxSummary,
)
from taxreport.extract.checks import run_checks
from taxreport.extract.form_index import FormIndex, build_index
from taxreport.extract.reader import PdfDocument, parse_comma_amount, read_pdf, row_text
from taxreport.extract.schedules import extract_schedules
from taxreport.extract.standard_forms import parse_standard_form
from taxreport.extract.tax_forms import (
    extract_adjustments,
    extract_credits,
    extract_losses,
    extract_minimum_tax,
    extract_tax_summary,
)

log = structlog.get_logger(__name__)


class IntakeError(Exception):
    """접수 검증 실패. 메시지에 이유를 적는다."""


def read_identity(doc: PdfDocument, index: FormIndex, rules: IdentityRules) -> CompanyIdentity:
    pages = index.pages(rules.form)
    if not pages:
        raise IntakeError(f"{doc.file_name}: «{rules.form}»를 찾지 못했습니다")
    business_no: str | None = None
    name: str | None = None
    period: tuple[date, date] | None = None
    for number in pages:
        for line in doc.page(number).lines:
            if business_no is None:
                found = re.search(rules.business_no_pattern, line)
                if found:
                    business_no = found.group(1)
            if name is None:
                found = re.search(rules.name_pattern, line)
                if found:
                    name = found.group(1).strip()
            if period is None:
                found = re.search(rules.period_pattern, line)
                if found:
                    y1, m1, d1, y2, m2, d2 = (int(part) for part in found.groups())
                    period = (date(y1, m1, d1), date(y2, m2, d2))
    missing = [
        label
        for label, value in (("사업자등록번호", business_no), ("법인명", name), ("사업연도", period))
        if value is None
    ]
    if business_no is None or name is None or period is None:
        raise IntakeError(
            f"{doc.file_name}: «{rules.form}»에서 {', '.join(missing)}을(를) 읽지 못했습니다"
        )
    return CompanyIdentity(
        name=name,
        business_no=business_no,
        period_start=period[0],
        period_end=period[1],
        fiscal_year=period[1].year,
    )


def extract_filing(path: Path, forms: FormsConfig) -> Filing:
    """PDF 하나에서 한 사업연도의 추출 결과를 만든다."""
    doc = read_pdf(path)
    index = build_index(doc, forms)
    identity = read_identity(doc, index, forms.identity)
    warnings: list[str] = []

    def standard(form_name: str) -> dict[str, LineItem]:
        pages = index.pages(form_name)
        if not pages:
            warnings.append(f"«{form_name}»를 찾지 못했습니다")
            return {}
        items, form_warnings = parse_standard_form(doc, pages, form_name)
        warnings.extend(form_warnings)
        return items

    balance_sheet = standard(forms.standard_forms.balance_sheet.form)
    income_statement = standard(forms.standard_forms.income_statement.form)
    for rules, items in (
        (forms.standard_forms.balance_sheet, balance_sheet),
        (forms.standard_forms.income_statement, income_statement),
    ):
        if not items:
            continue
        absent = [code for code in rules.codes.values() if code not in items]
        if absent:
            warnings.append(f"«{rules.form}»에서 코드 {', '.join(absent)}를 찾지 못했습니다")

    tax_values = extract_tax_summary(
        doc, index.pages(forms.tax_adjustment.form), forms.tax_adjustment
    )
    if not index.has(forms.tax_adjustment.form):
        warnings.append(f"«{forms.tax_adjustment.form}»를 찾지 못했습니다")
    tax_values.update(
        extract_minimum_tax(doc, index.pages(forms.minimum_tax.form), forms.minimum_tax)
    )
    tax = TaxSummary(values=tax_values)
    schedules = extract_schedules(doc, forms.schedules)
    warnings.extend(run_checks(balance_sheet, income_statement, tax, schedules, forms))
    warnings.extend(
        check_gross_receipts(
            doc, index.pages(forms.identity.form), forms.identity.form, income_statement, forms
        )
    )

    credits = extract_credits(doc, index.pages(forms.credits.form), forms.credits)
    warnings.extend(check_credit_total(tax, credits, forms.credits.form))

    filing = Filing(
        source_file=doc.file_name,
        identity=identity,
        balance_sheet=balance_sheet,
        income_statement=income_statement,
        tax=tax,
        adjustments=extract_adjustments(
            doc, index.pages(forms.adjustments.form), forms.adjustments
        ),
        credits=credits,
        losses=extract_losses(doc, index.pages(forms.losses.form), forms.losses),
        schedules=schedules,
        forms_found=index.refs(),
        warnings=warnings,
    )
    log.info(
        "filing_extracted",
        file=doc.file_name,
        fiscal_year=identity.fiscal_year,
        warnings=len(warnings),
    )
    return filing


_TRAILING_TAG_RE = re.compile(r"\s*\([^()]*\)\s*$")
_RECEIPTS_RE = re.compile(r"수\s*입\s*금\s*액\s*\(\s*(-?[\d,]*)\s*\)")


def check_gross_receipts(
    doc: PdfDocument,
    pages: Sequence[int],
    form_name: str,
    income_statement: dict[str, LineItem],
    forms: FormsConfig,
) -> list[str]:
    """신고서의 수입금액과 표준손익계산서의 매출액을 견준다.

    두 값은 범위가 다를 수 있다(수입금액에는 영업외수익이 들어가기도 한다). 그래서 한쪽으로
    덮어쓰지 않고, 다르면 차이만 경고로 남긴다. 보고서의 매출액은 표준손익계산서 값이다.
    """
    rules = forms.standard_forms.income_statement
    code = rules.codes.get("revenue")
    item = income_statement.get(code) if code is not None else None
    for number in pages:
        for row in doc.page(number).rows:
            match = _RECEIPTS_RE.search(row_text(row))
            if match is None:
                continue
            where = f"{doc.file_name} «{form_name}» {number}쪽 수입금액"
            receipts = parse_comma_amount(match.group(1))
            if receipts is None:
                return [f"{where}: 금액 칸이 비어 있어 읽지 못했습니다(0으로 보지 않음)"]
            if item is None or item.amount.value is None or item.amount.value == receipts:
                return []
            source = item.amount.source
            page = source.page if source is not None else None
            return [
                f"서식 간 차이: {where} {receipts:,}원 / «{rules.form}» {page}쪽 "
                f"{item.label}(코드 {item.code}) {item.amount.value:,}원 "
                f"(차이 {receipts - item.amount.value:,}원). 수입금액은 영업외수익 등이 포함되어 "
                "범위가 다를 수 있습니다. 보고서의 매출액은 표준손익계산서 값을 썼고 다른 값으로 바꾸지 않았습니다"
            ]
    return []


def check_credit_total(tax: TaxSummary, credits: Sequence[TaxCredit], form_name: str) -> list[str]:
    """읽은 공제·감면세액의 합을 «산출세액 - 총부담세액»과 견준다.

    공제·감면이 있는데 합계표에서 읽지 못하면 보고서가 «공제·감면 없음»이라고 잘못 쓰게 된다.
    가산세 등으로 두 값이 다를 수 있으므로 값은 고치지 않고 차이만 알린다.
    """
    calculated = tax.values.get("calculated_tax")
    total = tax.values.get("total_tax")
    if calculated is None or total is None or calculated.value is None or total.value is None:
        return []
    expected = calculated.value - total.value
    found = sum((credit.amount.value or Decimal(0) for credit in credits), Decimal(0))
    if expected == found:
        return []
    return [
        f"세액조정계산서의 산출세액 - 총부담세액은 {expected:,}원인데 «{form_name}»에서 읽은 "
        f"공제·감면세액은 {len(credits)}건 합계 {found:,}원입니다(차이 {expected - found:,}원). "
        "읽지 못한 공제·감면 항목이나 가산세가 있는지 원문 확인이 필요합니다"
    ]


def pair_filings(first: Filing, second: Filing) -> CompanyCase:
    """두 추출 결과를 사업연도로 전기·당기에 배정한다. 인자 순서와 파일명은 보지 않는다."""
    a, b = first.identity, second.identity
    if a.business_no != b.business_no:
        raise IntakeError(
            "두 파일의 회사가 다릅니다: "
            f"{first.source_file}은 {a.name}({a.business_no}), "
            f"{second.source_file}은 {b.name}({b.business_no})입니다"
        )
    if a.period_start == b.period_start and a.period_end == b.period_end:
        raise IntakeError(
            f"두 파일의 사업연도가 같습니다({a.period_start}~{a.period_end}). "
            "전기와 당기 파일을 하나씩 넣어 주세요"
        )
    prior, current = (first, second) if a.period_end < b.period_end else (second, first)
    expected_start = prior.identity.period_end + timedelta(days=1)
    if current.identity.period_start != expected_start:
        raise IntakeError(
            "두 파일의 사업연도가 이어지지 않습니다: "
            f"전기 {prior.identity.period_start}~{prior.identity.period_end}, "
            f"당기 {current.identity.period_start}~{current.identity.period_end}"
        )
    if prior.identity.name != current.identity.name:
        # 프로그램이 법인명 뒤에 붙인 표시(예: «(10기)», «(최종)»)만 다르면 공통 이름을 쓴다.
        bases = {_TRAILING_TAG_RE.sub("", filing.identity.name).strip() for filing in (prior, current)}
        original = f"{prior.identity.name} / {current.identity.name}"
        if len(bases) == 1 and (base := bases.pop()):
            prior.identity.name = base
            current.identity.name = base
            current.warnings.append(
                f"법인명 표기가 연도마다 다릅니다({original}). 끝의 괄호 표시는 법인명이 아닌 것으로 보고 "
                f"«{base}»로 적었습니다(가정)"
            )
        else:
            current.warnings.append(f"법인명 표기가 연도마다 다릅니다: {original}")
    return CompanyCase(prior=prior, current=current)


def load_case(first: Path, second: Path, forms: FormsConfig) -> CompanyCase:
    return pair_filings(extract_filing(first, forms), extract_filing(second, forms))
