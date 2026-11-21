"""도메인 모델. 파일·PDF·설정을 읽지 않는다."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, Field


class Source(BaseModel):
    """값이 적혀 있던 자리."""

    file: str
    form: str
    page: int
    code: str | None = None
    label: str | None = None


class ValueStatus(StrEnum):
    CONFIRMED = "확정"
    ASSUMED = "가정"
    NEEDS_CHECK = "확인 필요"


class SourcedValue(BaseModel):
    """값 + 상태 + 출처. 계산값은 derived_from에 원천 출처를 담는다."""

    value: Decimal | None
    status: ValueStatus
    source: Source | None = None
    derived_from: list[Source] = Field(default_factory=list)
    note: str | None = None

    @classmethod
    def missing(cls, note: str) -> SourcedValue:
        return cls(value=None, status=ValueStatus.NEEDS_CHECK, note=note)

    def sources(self) -> list[Source]:
        if self.source is not None:
            return [self.source]
        return list(self.derived_from)


class CompanyIdentity(BaseModel):
    name: str
    business_no: str
    period_start: date
    period_end: date
    fiscal_year: int


class LineItem(BaseModel):
    """표준서식의 한 줄."""

    code: str
    label: str
    amount: SourcedValue
    parent_code: str | None = None


class TaxSummary(BaseModel):
    """세액조정계산서·최저한세조정계산서에서 읽은 값. 키는 forms.yaml의 항목 키."""

    values: dict[str, SourcedValue] = Field(default_factory=dict)

    def get(self, key: str) -> SourcedValue:
        found = self.values.get(key)
        if found is None:
            return SourcedValue.missing(f"세무 서식에서 {key} 값을 찾지 못했습니다")
        return found


class AdjustmentKind(StrEnum):
    ADDITION = "익금산입·손금불산입"
    DEDUCTION = "손금산입·익금불산입"


class TaxAdjustment(BaseModel):
    kind: AdjustmentKind
    subject: str
    amount: SourcedValue
    disposition: str | None = None


class TaxCredit(BaseModel):
    name: str
    legal_basis: str | None = None
    code: str | None = None
    amount: SourcedValue


class LossRecord(BaseModel):
    """자본금과적립금조정명세서(갑)의 사업연도별 결손금."""

    fiscal_year_end: date
    incurred: SourcedValue
    used_current: SourcedValue | None = None
    balance: SourcedValue | None = None


class ScheduleBalance(BaseModel):
    """부속명세서의 거래처별 기말 잔액."""

    schedule: str
    account: str
    partner: str | None = None
    amount: SourcedValue


class Schedules(BaseModel):
    balances: list[ScheduleBalance] = Field(default_factory=list)

    def for_account(self, keywords: list[str]) -> list[ScheduleBalance]:
        return [b for b in self.balances if any(k in b.account for k in keywords)]


class FormRef(BaseModel):
    name: str
    pages: list[int]


class Filing(BaseModel):
    """한 사업연도의 추출 결과."""

    source_file: str
    identity: CompanyIdentity
    balance_sheet: dict[str, LineItem] = Field(default_factory=dict)
    income_statement: dict[str, LineItem] = Field(default_factory=dict)
    tax: TaxSummary = Field(default_factory=TaxSummary)
    adjustments: list[TaxAdjustment] = Field(default_factory=list)
    credits: list[TaxCredit] = Field(default_factory=list)
    losses: list[LossRecord] = Field(default_factory=list)
    schedules: Schedules = Field(default_factory=Schedules)
    forms_found: list[FormRef] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    def has_form(self, name: str) -> bool:
        return any(ref.name == name for ref in self.forms_found)


class CompanyCase(BaseModel):
    prior: Filing
    current: Filing


# ---------------------------------------------------------------- 표시용 모델


class NumberStyle(StrEnum):
    WON = "won"  # 1,234원
    KEY_AMOUNT = "key_amount"  # 1.93억원 / 4,925만원
    PERCENT = "percent"  # 51.79%
    COUNT = "count"  # 3
    TEXT = "text"  # 글자 그대로


class TextArg(BaseModel):
    """문장 틀에 끼워 넣을 값. 표기 형식은 render가 적용한다."""

    style: NumberStyle
    value: Decimal | None = None
    text: str | None = None
    decimals: int = 2


class Sentence(BaseModel):
    template: str
    args: dict[str, TextArg] = Field(default_factory=dict)


class CommentBlock(BaseModel):
    title: str
    sentences: list[Sentence]


class Note(BaseModel):
    title: str
    sentences: list[Sentence]
    status: ValueStatus = ValueStatus.CONFIRMED


class MetricKind(StrEnum):
    AMOUNT = "amount"
    RATIO = "ratio"


class MetricRow(BaseModel):
    label: str
    kind: MetricKind
    prior: SourcedValue
    current: SourcedValue
    change: Decimal | None = None
    prior_label: str | None = None  # 예: 자본잠식
    current_label: str | None = None
    change_text: str | None = None  # 계산하지 않을 때 대신 쓰는 글자


class ComparisonRow(BaseModel):
    label: str
    prior: SourcedValue
    current: SourcedValue
    change: Decimal | None = None
    change_rate: Decimal | None = None
    rate_text: str | None = None  # 흑자전환 / -


class SgaRowKind(StrEnum):
    ITEM = "item"
    OTHER = "other"
    TOTAL = "total"


class SgaRow(BaseModel):
    label: str
    kind: SgaRowKind
    code: str | None = None
    prior: SourcedValue
    current: SourcedValue
    change: Decimal | None = None
    prior_share: Decimal | None = None  # 전기 매출액 대비 비중(%)
    current_share: Decimal | None = None
    composition: Decimal | None = None  # 당기 판관비 합계 대비 구성비(%)


class EvidenceRow(BaseModel):
    item: str
    year: int
    value: Decimal | None
    style: NumberStyle = NumberStyle.WON
    status: ValueStatus
    file: str
    form: str
    page: int | None = None
    code: str | None = None


class Report(BaseModel):
    """보고서 한 건의 표시용 데이터."""

    company: str
    business_no: str
    prior_year: int
    current_year: int
    prior_period: tuple[date, date]
    current_period: tuple[date, date]
    issuer: str
    issued_on: date
    key_metrics: list[MetricRow]
    income_comparison: list[ComparisonRow]
    balance_comparison: list[ComparisonRow]
    sga_items: list[SgaRow]
    comments: list[CommentBlock]
    tax_notes: list[Note]
    financial_notes: list[Note]
    assumptions: list[str]
    needs_check: list[str]
    evidence: list[EvidenceRow]
