"""공용 fixture. 샘플 PDF가 프로젝트 루트에 없으면 PDF가 필요한 테스트는 건너뛴다."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from taxreport.config.loader import FormsConfig, Policy, load_forms, load_policy
from taxreport.domain.models import (
    AdjustmentKind,
    CompanyCase,
    CompanyIdentity,
    Filing,
    FormRef,
    LineItem,
    LossRecord,
    ScheduleBalance,
    Schedules,
    Source,
    SourcedValue,
    TaxAdjustment,
    TaxCredit,
    TaxSummary,
    ValueStatus,
)
from taxreport.extract.intake import extract_filing, pair_filings

ROOT = Path(__file__).resolve().parents[1]
SAMPLE_PRIOR = ROOT / "샘플컴퍼니_2024_세무조정계산서.pdf"
SAMPLE_CURRENT = ROOT / "샘플컴퍼니_2025_세무조정계산서.pdf"

# (코드, 항목명, 상위 코드)
INCOME_LAYOUT: tuple[tuple[str, str, str | None], ...] = (
    ("1", "Ⅰ.매출액", None),
    ("35", "Ⅱ.매출원가", None),
    ("66", "Ⅲ.매출총손익", None),
    ("67", "Ⅳ.판매비와관리비", None),
    ("68", "1.급여", "67"),
    ("70", "나.직원급여", "68"),
    ("78", "3.보험료", "67"),
    ("79", "4.복리후생비", "67"),
    ("80", "5.여비교통비", "67"),
    ("104", "20.지급수수료", "67"),
    ("108", "22.소모품비", "67"),
    ("124", "37.기타", "67"),
    ("129", "Ⅴ.영업손익", None),
    ("130", "Ⅵ.영업외수익", None),
    ("131", "1.이자수익", "130"),
    ("179", "Ⅶ.영업외비용", None),
    ("212", "21.기타잡손실", "179"),
    ("217", "Ⅷ.법인세비용차감전손익", None),
    ("218", "Ⅸ.법인세비용", None),
    ("219", "Ⅹ.당기순손익", None),
)
BALANCE_LAYOUT: tuple[tuple[str, str, str | None], ...] = (
    ("1", "Ⅰ.유동자산", None),
    ("10", "가.외상매출금", "1"),
    ("80", "Ⅱ.비유동자산", None),
    ("228", "자산총계(Ⅰ+Ⅱ)", None),
    ("229", "Ⅰ.유동부채", None),
    ("236", "나.주주·임원·직원단기차입금", "229"),
    ("284", "Ⅱ.비유동부채", None),
    ("333", "부채총계(Ⅰ+Ⅱ)", None),
    ("334", "Ⅲ.자본금", None),
    ("372", "Ⅶ.이익잉여금", None),
    ("382", "자본총계(Ⅲ+~Ⅵ)", None),
)


def sourced(value: int, file: str, form: str, page: int, code: str | None, label: str) -> SourcedValue:
    return SourcedValue(
        value=Decimal(value),
        status=ValueStatus.CONFIRMED,
        source=Source(file=file, form=form, page=page, code=code, label=label),
    )


def make_items(
    layout: tuple[tuple[str, str, str | None], ...], amounts: dict[str, int], file: str, form: str
) -> dict[str, LineItem]:
    return {
        code: LineItem(
            code=code,
            label=label,
            parent_code=parent,
            amount=sourced(amounts.get(code, 0), file, form, 10, code, label),
        )
        for code, label, parent in layout
    }


def make_filing(
    year: int,
    income: dict[str, int],
    balance: dict[str, int],
    tax: dict[str, int] | None = None,
    business_no: str = "123-45-67890",
    name: str = "(주)테스트상사",
    forms_found: tuple[str, ...] = ("표준재무상태표", "표준손익계산서"),
) -> Filing:
    file = f"test_{year}.pdf"
    tax_values = {
        key: sourced(value, file, "법인세과세표준및세액조정계산서", 5, None, key)
        for key, value in (tax or {}).items()
    }
    return Filing(
        source_file=file,
        identity=CompanyIdentity(
            name=name,
            business_no=business_no,
            period_start=date(year, 1, 1),
            period_end=date(year, 12, 31),
            fiscal_year=year,
        ),
        balance_sheet=make_items(BALANCE_LAYOUT, balance, file, "표준재무상태표"),
        income_statement=make_items(INCOME_LAYOUT, income, file, "표준손익계산서"),
        tax=TaxSummary(values=tax_values),
        forms_found=[FormRef(name=form, pages=[10]) for form in forms_found],
    )


@pytest.fixture(scope="session")
def forms() -> FormsConfig:
    return load_forms()


@pytest.fixture(scope="session")
def policy() -> Policy:
    return load_policy()


@pytest.fixture()
def prior_filing() -> Filing:
    """전기: 적자, 자본잠식."""
    return make_filing(
        2024,
        income={
            "1": 92_944_479, "66": 92_944_479, "67": 108_330_197, "68": 37_760_740, "70": 37_760_740,
            "79": 10_281_038, "80": 10_843_879, "104": 5_667_859, "108": 20_648_759, "124": 19_317_000,
            "78": 3_810_922, "129": -15_385_718, "130": 4_975, "131": 4_975, "179": 129_610,
            "212": 129_610, "217": -15_510_353, "219": -15_510_353,
        },
        balance={
            "1": 13_879_981, "10": 7_324_268, "80": 165_000, "228": 14_044_981, "229": 19_555_334,
            "236": 8_135_140, "333": 19_555_334, "334": 10_000_000, "372": -15_510_353, "382": -5_510_353,
        },
        tax={"net_income": -15_510_353, "tax_base": -15_510_353, "calculated_tax": 0, "total_tax": 0},
    )


@pytest.fixture()
def current_filing() -> Filing:
    """당기: 흑자, 자본잠식 해소, 감면 있음."""
    filing = make_filing(
        2025,
        income={
            "1": 192_734_983, "35": 314_026, "66": 192_420_957, "67": 143_166_567, "68": 50_319_710,
            "70": 50_319_710, "78": 2_021_410, "79": 19_528_517, "80": 18_311_175, "104": 3_463_979,
            "108": 23_996_690, "124": 25_525_086, "129": 49_254_390, "130": 6_837, "131": 6_837,
            "179": 45_480, "212": 45_480, "217": 49_215_747, "218": 2_719_623, "219": 46_496_124,
        },
        balance={
            "1": 62_045_447, "10": 27_109_977, "80": 165_000, "228": 62_210_447, "229": 21_224_676,
            "236": 1_088_470, "333": 21_224_676, "334": 10_000_000, "372": 30_985_771, "382": 40_985_771,
        },
        tax={
            "net_income": 46_496_124, "income_addition": 2_719_623, "loss_carryforward_used": 15_640_153,
            "tax_base": 33_575_594, "calculated_tax": 3_021_803, "total_tax": 2_417_443,
        },
        forms_found=("표준재무상태표", "표준손익계산서", "소득금액조정합계표"),
    )
    file = filing.source_file
    filing.adjustments = [
        TaxAdjustment(
            kind=AdjustmentKind.ADDITION,
            subject="법인세등",
            disposition="기타사외유출",
            amount=sourced(2_719_623, file, "소득금액조정합계표", 16, None, "법인세등"),
        )
    ]
    filing.credits = [
        TaxCredit(
            name="중소기업에대한특별세액감면",
            legal_basis="조세특례제한법제7조",
            code="112",
            amount=sourced(604_360, file, "공제감면세액및추가납부세액합계표(갑)", 9, "112", "감면"),
        )
    ]
    filing.losses = [
        LossRecord(
            fiscal_year_end=date(2024, 12, 31),
            incurred=sourced(15_640_153, file, "자본금과적립금조정명세서(갑)", 24, None, "발생액"),
            used_current=sourced(15_640_153, file, "자본금과적립금조정명세서(갑)", 24, None, "공제액"),
        )
    ]
    filing.schedules = Schedules(
        balances=[
            ScheduleBalance(
                schedule="외상매출금명세서",
                account="외상매출금",
                partner="거래처A",
                amount=sourced(27_109_977, file, "외상매출금명세서", 60, "108", "외상매출금 거래처A"),
            )
        ]
    )
    return filing


@pytest.fixture()
def case(prior_filing: Filing, current_filing: Filing) -> CompanyCase:
    return CompanyCase(prior=prior_filing, current=current_filing)


@pytest.fixture(scope="session")
def sample_filings(forms: FormsConfig) -> tuple[Filing, Filing]:
    """(2024, 2025) 샘플 PDF의 추출 결과. PDF가 없으면 건너뛴다."""
    if not (SAMPLE_PRIOR.is_file() and SAMPLE_CURRENT.is_file()):
        pytest.skip("샘플 PDF가 프로젝트 루트에 없습니다")
    return extract_filing(SAMPLE_PRIOR, forms), extract_filing(SAMPLE_CURRENT, forms)


@pytest.fixture(scope="session")
def sample_case(sample_filings: tuple[Filing, Filing]) -> CompanyCase:
    # 일부러 당기 → 전기 순서로 넘긴다(인자 순서로 판정하지 않는지 확인).
    return pair_filings(sample_filings[1], sample_filings[0])
