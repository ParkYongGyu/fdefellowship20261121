from __future__ import annotations

from decimal import Decimal

import pytest

from taxreport.config.loader import Policy
from taxreport.domain.models import NumberStyle, Sentence, TextArg
from taxreport.render.formatters import (
    format_key_amount,
    format_labeled_percent,
    format_number,
    format_percent,
    format_won,
    render_sentence,
)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (192_734_983, "1.93억원"),
        (49_254_390, "4,925만원"),
        (46_496_124, "4,650만원"),
        (92_944_479, "9,294만원"),
        (-15_385_718, "-1,539만원"),
        (100_000_000, "1.00억원"),
        (99_999_999, "10,000만원"),  # 1억 미만은 만원 단위(반올림)라는 규칙을 그대로 적용한 결과
        (-250_000_000, "-2.50억원"),
        (12_345_678_901, "123.46억원"),
        (4_999, "0만원"),
        (-3_000, "0만원"),
        (5_000, "1만원"),
        (0, "0만원"),
    ],
)
def test_format_key_amount(policy: Policy, value: int, expected: str) -> None:
    assert format_key_amount(Decimal(value), policy) == expected


def test_format_key_amount_rounding_down(policy: Policy) -> None:
    down = policy.model_copy(update={"units": policy.units.model_copy(update={"rounding": "down"})})
    assert format_key_amount(Decimal(49_259_999), down) == "4,925만원"
    assert format_key_amount(Decimal(199_999_999), down) == "1.99억원"
    assert format_key_amount(Decimal(49_259_999), policy) == "4,926만원"


def test_missing_values_are_marked(policy: Policy) -> None:
    assert format_key_amount(None, policy) == "확인 필요"
    assert format_won(None, policy) == "확인 필요"
    assert format_percent(None, 2, policy) == "확인 필요"


def test_format_percent_and_negative_equity_label(policy: Policy) -> None:
    assert format_percent(Decimal("51.79"), 2, policy) == "51.79%"
    assert format_labeled_percent(Decimal("-354.88"), 2, "자본잠식", policy) == "자본잠식 (-354.88%)"
    assert format_labeled_percent(Decimal("51.79"), 2, None, policy) == "51.79%"


def test_format_won_and_number(policy: Policy) -> None:
    assert format_won(Decimal(-15_385_718), policy) == "-15,385,718원"
    assert format_number(Decimal(192_734_983), policy) == "192,734,983"


def test_render_sentence(policy: Policy) -> None:
    sentence = Sentence(
        template="{year}년 매출액은 {amount}이고 비율은 {ratio}입니다.",
        args={
            "year": TextArg(style=NumberStyle.TEXT, text="2025"),
            "amount": TextArg(style=NumberStyle.WON, value=Decimal(1_234_567)),
            "ratio": TextArg(style=NumberStyle.PERCENT, value=Decimal("12.3"), decimals=1),
        },
    )
    assert render_sentence(sentence, policy) == "2025년 매출액은 1,234,567원이고 비율은 12.3%입니다."
