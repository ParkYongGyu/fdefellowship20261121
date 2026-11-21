from __future__ import annotations

from datetime import date

import pytest

from taxreport.domain.models import Filing
from taxreport.extract.intake import IntakeError, pair_filings


def test_order_of_arguments_does_not_matter(prior_filing: Filing, current_filing: Filing) -> None:
    forward = pair_filings(prior_filing, current_filing)
    backward = pair_filings(current_filing, prior_filing)
    for paired in (forward, backward):
        assert paired.prior.identity.fiscal_year == 2024
        assert paired.current.identity.fiscal_year == 2025


def test_same_filing_twice_is_rejected(current_filing: Filing) -> None:
    with pytest.raises(IntakeError, match="사업연도가 같습니다"):
        pair_filings(current_filing, current_filing.model_copy(deep=True))


def test_different_company_is_rejected(prior_filing: Filing, current_filing: Filing) -> None:
    prior_filing.identity.business_no = "999-99-99999"
    with pytest.raises(IntakeError, match="회사가 다릅니다"):
        pair_filings(prior_filing, current_filing)


def test_non_consecutive_years_are_rejected(prior_filing: Filing, current_filing: Filing) -> None:
    prior_filing.identity.period_start = date(2022, 1, 1)
    prior_filing.identity.period_end = date(2022, 12, 31)
    prior_filing.identity.fiscal_year = 2022
    with pytest.raises(IntakeError, match="이어지지 않습니다"):
        pair_filings(prior_filing, current_filing)


def test_short_first_period_is_accepted(prior_filing: Filing, current_filing: Filing) -> None:
    """설립 첫해처럼 전기가 1년보다 짧아도 종료일 다음 날이 당기 시작일이면 연속이다."""
    prior_filing.identity.period_start = date(2024, 5, 15)
    assert pair_filings(current_filing, prior_filing).prior.identity.fiscal_year == 2024
