"""그래프 4종을 PNG로 만든다. 값은 Report에 있는 것을 그대로 그린다."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from io import BytesIO
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt  # noqa: E402
from matplotlib import font_manager  # noqa: E402
from matplotlib.axes import Axes  # noqa: E402
from matplotlib.figure import Figure  # noqa: E402

from taxreport.config.loader import Policy  # noqa: E402
from taxreport.domain.models import MetricKind, Report, SgaRowKind  # noqa: E402
from taxreport.render.formatters import format_key_amount, format_percent  # noqa: E402

FIGURE_SIZE = (5.4, 3.7)
DPI = 160
PRIOR_COLOR = "#9aa5b1"
CURRENT_COLOR = "#2f6fb0"
PIE_COLORS = ["#2f6fb0", "#5b8fc7", "#86aedb", "#b3cdea", "#d6e3f3", "#9aa5b1"]
MAN = Decimal(10_000)


@dataclass(frozen=True)
class Chart:
    title: str
    png: bytes


def register_font(font_path: Path) -> str:
    """matplotlib에 보고서와 같은 한글 글꼴을 등록하고 글꼴 이름을 돌려준다."""
    font_manager.fontManager.addfont(str(font_path))
    name = str(font_manager.FontProperties(fname=str(font_path)).get_name())
    plt.rcParams["font.family"] = name
    plt.rcParams["axes.unicode_minus"] = False
    return name


def _to_man(value: Decimal | None) -> float:
    return float((value or Decimal(0)) / MAN)


def _png(figure: Figure) -> bytes:
    buffer = BytesIO()
    figure.tight_layout()
    figure.savefig(buffer, format="png", dpi=DPI)
    plt.close(figure)
    return buffer.getvalue()


def _grouped_bars(
    title: str,
    labels: list[str],
    prior: list[Decimal | None],
    current: list[Decimal | None],
    report: Report,
    policy: Policy,
) -> Chart:
    figure, axes = plt.subplots(figsize=FIGURE_SIZE)
    assert isinstance(axes, Axes)
    positions = list(range(len(labels)))
    width = 0.38
    series = (
        (prior, -width / 2, PRIOR_COLOR, f"{report.prior_year}년"),
        (current, width / 2, CURRENT_COLOR, f"{report.current_year}년"),
    )
    for values, offset, color, legend in series:
        bars = axes.bar(
            [p + offset for p in positions],
            [_to_man(v) for v in values],
            width=width,
            color=color,
            label=legend,
        )
        axes.bar_label(bars, labels=[format_key_amount(v, policy) for v in values], fontsize=6, padding=2)
    axes.set_xticks(positions)
    axes.set_xticklabels(labels, fontsize=7)
    axes.set_ylabel(policy.report.chart_unit_label, fontsize=8)
    axes.tick_params(axis="y", labelsize=7)
    axes.axhline(0, color="#444444", linewidth=0.6)
    axes.set_title(title, fontsize=10)
    axes.legend(fontsize=7)
    axes.margins(y=0.15)
    return Chart(title=title, png=_png(figure))


def _pie(title: str, report: Report, policy: Policy) -> Chart:
    rows = [
        row
        for row in report.sga_items
        if row.kind is not SgaRowKind.TOTAL and (row.current.value or Decimal(0)) > 0
    ]
    figure, axes = plt.subplots(figsize=FIGURE_SIZE)
    assert isinstance(axes, Axes)
    if rows:
        axes.pie(
            [float(row.current.value or 0) for row in rows],
            labels=[
                f"{row.label}\n{format_percent(row.composition, policy.sga.share_decimals, policy)}"
                for row in rows
            ],
            colors=PIE_COLORS[: len(rows)],
            startangle=90,
            counterclock=False,
            textprops={"fontsize": 6.5},
            wedgeprops={"linewidth": 0.6, "edgecolor": "white"},
        )
    axes.set_title(f"{title} ({report.current_year}년, %)", fontsize=10)
    return Chart(title=title, png=_png(figure))


def render_charts(report: Report, policy: Policy, font_path: Path) -> list[Chart]:
    register_font(font_path)
    titles = policy.report.chart_titles
    amounts = [row for row in report.key_metrics if row.kind is MetricKind.AMOUNT]
    items = [row for row in report.sga_items if row.kind is SgaRowKind.ITEM]
    return [
        _grouped_bars(
            titles["income"],
            [row.label for row in amounts],
            [row.prior.value for row in amounts],
            [row.current.value for row in amounts],
            report,
            policy,
        ),
        _pie(titles["sga_pie"], report, policy),
        _grouped_bars(
            titles["sga_bars"],
            [row.label.replace("(", "\n(", 1) for row in items],
            [row.prior.value for row in items],
            [row.current.value for row in items],
            report,
            policy,
        ),
        _grouped_bars(
            titles["balance"],
            [row.label for row in report.balance_comparison],
            [row.prior.value for row in report.balance_comparison],
            [row.current.value for row in report.balance_comparison],
            report,
            policy,
        ),
    ]
