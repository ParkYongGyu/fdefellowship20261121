"""설정 로드·검증. domain 외의 레이어를 import하지 않는다."""

from __future__ import annotations

from decimal import Decimal
from importlib import resources
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field

# ------------------------------------------------------------------ forms.yaml


class IndexRules(BaseModel):
    head_lines: int = 8
    toc_pattern: str
    generic_start_patterns: list[str] = Field(default_factory=list)
    unknown_form_name: str = "(미등록 서식)"


class FormRule(BaseModel):
    name: str
    patterns: list[str]


class IdentityRules(BaseModel):
    form: str
    business_no_pattern: str
    name_pattern: str
    period_pattern: str


class StandardFormRules(BaseModel):
    form: str
    codes: dict[str, str]
    note_codes: dict[str, list[str]] = Field(default_factory=dict)


class StandardFormsRules(BaseModel):
    balance_sheet: StandardFormRules
    income_statement: StandardFormRules


class TaxAdjustmentRules(BaseModel):
    form: str
    anchor_code: str
    codes: dict[str, str]
    labels: dict[str, str]


class MinimumTaxRow(BaseModel):
    code: str
    column: str


class MinimumTaxRules(BaseModel):
    form: str
    code_header: str
    rows: dict[str, MinimumTaxRow]
    labels: dict[str, str]


class AdjustmentRules(BaseModel):
    form: str
    header_keywords: list[str]
    total_labels: list[str]


class CreditRules(BaseModel):
    form: str
    code_header: str
    code_pattern: str
    amount_header_pattern: str
    row_number_pattern: str = ""
    glued_law_pattern: str = ""
    law_pattern: str
    skip_labels: list[str]


class LossRules(BaseModel):
    form: str
    row_pattern: str
    columns: dict[str, str]


class ScheduleRules(BaseModel):
    title_pattern: str
    asof_pattern: str
    header_account: str
    ending_header: str
    total_labels: list[str]
    accounts: dict[str, list[str]]


class FormsConfig(BaseModel):
    index: IndexRules
    forms: list[FormRule]
    identity: IdentityRules
    standard_forms: StandardFormsRules
    tax_adjustment: TaxAdjustmentRules
    minimum_tax: MinimumTaxRules
    adjustments: AdjustmentRules
    credits: CreditRules
    losses: LossRules
    schedules: ScheduleRules


# ----------------------------------------------------------------- policy.yaml


class Issuer(BaseModel):
    name: str
    footer: str


class ReportTexts(BaseModel):
    title: str
    sections: dict[str, str]
    chart_titles: dict[str, str]
    chart_unit_label: str
    balance_table_title: str
    assumptions_title: str
    needs_check_title: str
    needs_check_none: str
    unit_note_key: str
    unit_note_won: str
    missing_text: str


class Units(BaseModel):
    eok_threshold: Decimal
    eok_decimals: int
    rounding: Literal["half_up", "down"]


class DebtRatio(BaseModel):
    decimals: int
    negative_equity_label: str


class SgaPolicy(BaseModel):
    top_n: int
    other_label: str
    total_label: str
    selection: Literal["current_amount_desc"]
    share_decimals: int
    same_name_template: str


class ComparisonPolicy(BaseModel):
    turnaround_label: str
    not_applicable: str
    rate_decimals: int


class RowLabel(BaseModel):
    key: str
    label: str


class Labels(BaseModel):
    key_metrics: dict[str, str]
    income_comparison: list[RowLabel]
    balance_comparison: list[RowLabel]
    sga_total: str
    cogs: str


class Titles(BaseModel):
    titles: dict[str, str]


class InvoicePolicy(BaseModel):
    fee_table: list[Any] = Field(default_factory=list)
    credit_surcharge: list[Any] = Field(default_factory=list)
    cms_withdrawal_day: int | None = None
    feedback_due_day: int | None = None


REQUIRED_TEMPLATES: tuple[str, ...] = (
    "revenue",
    "profit",
    "margins",
    "sga_total",
    "sga_top",
    "sga_next",
    "non_operating_total",
    "non_operating_item",
    "non_operating_none",
    "change_income",
    "change_sga",
    "debt_ratio",
    "debt_ratio_basis",
    "credit_item",
    "credit_summary",
    "credit_none",
    "credit_no_form",
    "adjustment_item",
    "adjustment_item_no_disposition",
    "adjustment_none",
    "adjustment_count",
    "adjustment_unread",
    "loss_used",
    "loss_origin",
    "loss_mismatch",
    "credit_note",
    "minimum_tax",
    "tax_summary",
    "negative_equity",
    "negative_equity_resolved",
    "officer_borrowings",
    "officer_borrowings_settle",
    "officer_loans",
    "receivables",
    "receivables_partners",
    "debt_ratio_note",
)


class Policy(BaseModel):
    issuer: Issuer
    report: ReportTexts
    units: Units
    debt_ratio: DebtRatio
    sga: SgaPolicy
    comparison: ComparisonPolicy
    font_path: str | None = None
    font_fallbacks: list[str] = Field(default_factory=list)
    labels: Labels
    assumptions: list[str]
    comments: Titles
    templates: dict[str, str]
    notes: Titles
    invoice: InvoicePolicy = Field(default_factory=InvoicePolicy)

    def template(self, key: str) -> str:
        return self.templates[key]


class ConfigError(Exception):
    """설정 파일이 없거나 형식이 맞지 않는다."""


def _read_yaml(path: Path | None, packaged_name: str) -> Any:
    if path is None:
        text = resources.files("taxreport.config").joinpath(packaged_name).read_text("utf-8")
    else:
        if not path.is_file():
            raise ConfigError(f"설정 파일을 찾지 못했습니다: {path}")
        text = path.read_text(encoding="utf-8")
    return yaml.safe_load(text)


def load_forms(path: Path | None = None) -> FormsConfig:
    return FormsConfig.model_validate(_read_yaml(path, "forms.yaml"))


def load_policy(path: Path | None = None) -> Policy:
    policy = Policy.model_validate(_read_yaml(path, "policy.yaml"))
    missing = [key for key in REQUIRED_TEMPLATES if key not in policy.templates]
    if missing:
        raise ConfigError(f"policy.yaml의 templates에 빠진 문장 틀이 있습니다: {', '.join(missing)}")
    return policy
