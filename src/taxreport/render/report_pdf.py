"""법인세 분석 보고서 PDF. Report를 그리기만 하고 값을 계산하지 않는다."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

import structlog
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import (
    Image,
    KeepTogether,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from taxreport.config.loader import Policy
from taxreport.domain.models import Note, Report, SgaRowKind, ValueStatus
from taxreport.render.charts import Chart, render_charts
from taxreport.render.formatters import (
    format_evidence_value,
    format_metric_change,
    format_metric_value,
    format_number,
    format_percent,
    format_period,
    format_rate,
    render_sentence,
)

log = structlog.get_logger(__name__)

FONT_NAME = "ReportFont"
FONT_DIR = Path("assets") / "fonts"
MARGIN = 16 * mm
CONTENT_WIDTH = A4[0] - 2 * MARGIN
HEADER_BACKGROUND = colors.HexColor("#e8eef5")
GRID_COLOR = colors.HexColor("#9aa5b1")
TOTAL_BACKGROUND = colors.HexColor("#f3f5f7")


class FontNotFoundError(Exception):
    """한글 글꼴을 찾지 못했다. 글자가 깨진 PDF를 만들지 않기 위해 멈춘다."""


def find_font(policy: Policy, base_dir: Path | None = None) -> Path:
    """탐색 순서: assets/fonts/*.ttf → policy.font_path → policy.font_fallbacks."""
    root = base_dir if base_dir is not None else Path.cwd()
    candidates: list[Path] = sorted((root / FONT_DIR).glob("*.ttf"))
    if policy.font_path:
        candidates.append(Path(policy.font_path).expanduser())
    candidates += [Path(path).expanduser() for path in policy.font_fallbacks]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    searched = ", ".join(str(c) for c in candidates) or str(root / FONT_DIR)
    raise FontNotFoundError(
        "한글 글꼴(TTF)을 찾지 못했습니다. assets/fonts/에 글꼴을 넣거나 "
        f"policy.yaml의 font_path를 지정해 주세요. 찾아본 곳: {searched}"
    )


class _Styles:
    def __init__(self) -> None:
        base = ParagraphStyle("base", fontName=FONT_NAME, fontSize=9, leading=13.5)
        self.body = base
        self.title = ParagraphStyle("title", parent=base, fontSize=20, leading=26, alignment=TA_CENTER)
        self.subtitle = ParagraphStyle("subtitle", parent=base, fontSize=12, leading=17, alignment=TA_CENTER)
        self.heading = ParagraphStyle(
            "heading", parent=base, fontSize=12.5, leading=17, spaceBefore=10, spaceAfter=5,
            textColor=colors.HexColor("#1f3b5c"), keepWithNext=True,
        )
        self.subheading = ParagraphStyle(
            "subheading", parent=base, fontSize=10, leading=14, spaceBefore=6, spaceAfter=2, keepWithNext=True
        )
        self.note = ParagraphStyle("note", parent=base, fontSize=8, leading=11, textColor=colors.HexColor("#555555"))
        self.cell = ParagraphStyle("cell", parent=base, fontSize=8.5, leading=11.5)
        self.cell_right = ParagraphStyle("cell_right", parent=self.cell, alignment=TA_RIGHT)
        self.cell_center = ParagraphStyle("cell_center", parent=self.cell, alignment=TA_CENTER)
        self.small = ParagraphStyle("small", parent=base, fontSize=7, leading=9.5)
        self.small_right = ParagraphStyle("small_right", parent=self.small, alignment=TA_RIGHT)
        self.bullet = ParagraphStyle("bullet", parent=base, leftIndent=10, firstLineIndent=-8)


class _Builder:
    def __init__(self, report: Report, policy: Policy, charts: list[Chart], font: TTFont) -> None:
        self.report = report
        self.policy = policy
        self.charts = charts
        self.styles = _Styles()
        self._glyphs: Any = font.face.charToGlyph
        self.missing_glyphs: set[str] = set()

    # ------------------------------------------------------------- 공통 도구

    def p(self, text: str, style: ParagraphStyle | None = None) -> Paragraph:
        self.missing_glyphs.update(
            ch for ch in text if not ch.isspace() and ord(ch) not in self._glyphs
        )
        return Paragraph(escape(text), style or self.styles.body)

    def table(
        self,
        header: list[str],
        rows: list[list[Paragraph]],
        widths: list[float],
        highlight_last: bool = False,
        header_style: ParagraphStyle | None = None,
    ) -> Table:
        head = [self.p(text, header_style or self.styles.cell_center) for text in header]
        table = Table([head, *rows], colWidths=[CONTENT_WIDTH * w for w in widths], repeatRows=1)
        commands: list[tuple[Any, ...]] = [
            ("GRID", (0, 0), (-1, -1), 0.4, GRID_COLOR),
            ("BACKGROUND", (0, 0), (-1, 0), HEADER_BACKGROUND),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ]
        if highlight_last and rows:
            commands.append(("BACKGROUND", (0, -1), (-1, -1), TOTAL_BACKGROUND))
        table.setStyle(TableStyle(commands))
        return table

    def prior_header(self) -> str:
        return f"{self.report.prior_year}년(전기)"

    def current_header(self) -> str:
        return f"{self.report.current_year}년(당기)"

    def section(self, key: str) -> Paragraph:
        return self.p(self.policy.report.sections[key], self.styles.heading)

    # ----------------------------------------------------------------- 구획

    def cover(self) -> list[Any]:
        report, policy, s = self.report, self.policy, self.styles
        info = [
            ("회사명", report.company),
            ("사업자등록번호", report.business_no),
            (f"전기 사업연도({report.prior_year}년)", format_period(*report.prior_period)),
            (f"당기 사업연도({report.current_year}년)", format_period(*report.current_period)),
            ("발행", report.issuer),
            ("작성일", f"{report.issued_on:%Y.%m.%d}"),
        ]
        table = Table(
            [[self.p(k, s.cell_center), self.p(v, s.cell)] for k, v in info],
            colWidths=[CONTENT_WIDTH * 0.32, CONTENT_WIDTH * 0.68],
        )
        table.setStyle(
            TableStyle(
                [
                    ("GRID", (0, 0), (-1, -1), 0.4, GRID_COLOR),
                    ("BACKGROUND", (0, 0), (0, -1), HEADER_BACKGROUND),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ]
            )
        )
        return [
            self.p(policy.report.title, s.title),
            Spacer(1, 2 * mm),
            self.p(f"{report.company} · {report.prior_year}년(전기) / {report.current_year}년(당기)", s.subtitle),
            Spacer(1, 4 * mm),
            table,
        ]

    def key_metrics(self) -> list[Any]:
        s = self.styles
        rows = [
            [
                self.p(row.label, s.cell_center),
                self.p(format_metric_value(row, "prior", self.policy), s.cell_right),
                self.p(format_metric_value(row, "current", self.policy), s.cell_right),
                self.p(format_metric_change(row, self.policy), s.cell_right),
            ]
            for row in self.report.key_metrics
        ]
        table = self.table(
            ["구분", self.prior_header(), self.current_header(), "증감"], rows, [0.22, 0.26, 0.26, 0.26]
        )
        return [self.section("key_metrics"), table, self.p(self.policy.report.unit_note_key, s.note)]

    def chart_grid(self) -> list[Any]:
        if not self.charts:
            return []
        cell_width = CONTENT_WIDTH / 2
        images = [
            Image(BytesIO(chart.png), width=cell_width - 4, height=(cell_width - 4) * 3.7 / 5.4)
            for chart in self.charts
        ]
        grid = Table(
            [images[i : i + 2] for i in range(0, len(images), 2)],
            colWidths=[cell_width, cell_width],
        )
        grid.setStyle(TableStyle([("ALIGN", (0, 0), (-1, -1), "CENTER")]))
        return [KeepTogether([self.section("charts"), grid])]

    def comments(self) -> list[Any]:
        s = self.styles
        flow: list[Any] = []
        lead: list[Any] = [self.section("comments")]  # 구획 제목이 쪽 끝에 혼자 남지 않게 첫 문단과 묶는다
        for block in self.report.comments:
            text = " ".join(render_sentence(sentence, self.policy) for sentence in block.sentences)
            flow.append(KeepTogether([*lead, self.p(block.title, s.subheading), self.p(text)]))
            lead = []
        return flow or lead

    def comparison_table(self, rows_data: list[Any]) -> Table:
        s = self.styles
        rows = [
            [
                self.p(row.label, s.cell_center),
                self.p(format_number(row.prior.value, self.policy), s.cell_right),
                self.p(format_number(row.current.value, self.policy), s.cell_right),
                self.p(format_number(row.change, self.policy), s.cell_right),
                self.p(format_rate(row, self.policy), s.cell_right),
            ]
            for row in rows_data
        ]
        return self.table(
            ["구분", self.prior_header(), self.current_header(), "증감액", "증감률"],
            rows,
            [0.24, 0.21, 0.21, 0.19, 0.15],
        )

    def income_comparison(self) -> list[Any]:
        return [
            self.section("income_comparison"),
            self.p(self.policy.report.unit_note_won, self.styles.note),
            self.comparison_table(self.report.income_comparison),
        ]

    def sga(self) -> list[Any]:
        s, policy, report = self.styles, self.policy, self.report
        decimals = policy.sga.share_decimals
        rows = [
            [
                self.p(row.label, s.cell),
                self.p(format_number(row.prior.value, policy), s.cell_right),
                self.p(format_number(row.current.value, policy), s.cell_right),
                self.p(format_number(row.change, policy), s.cell_right),
                self.p(format_percent(row.prior_share, decimals, policy), s.cell_right),
                self.p(format_percent(row.current_share, decimals, policy), s.cell_right),
            ]
            for row in report.sga_items
        ]
        header = [
            "항목",
            f"{report.prior_year}년(전기) 금액",
            f"{report.current_year}년(당기) 금액",
            "증감액",
            f"{report.prior_year}년 매출액 대비 비중",
            f"{report.current_year}년 매출액 대비 비중",
        ]
        has_total = bool(report.sga_items) and report.sga_items[-1].kind is SgaRowKind.TOTAL
        table = self.table(header, rows, [0.26, 0.17, 0.17, 0.16, 0.12, 0.12], highlight_last=has_total)
        return [self.section("sga"), self.p(policy.report.unit_note_won, s.note), table]

    def notes(self, key: str, notes: list[Note]) -> list[Any]:
        s = self.styles
        flow: list[Any] = [self.section(key)]
        for note in notes:
            title = note.title
            if note.status is not ValueStatus.CONFIRMED:
                title = f"{title} [{note.status.value}]"
            body = [self.p(f"· {render_sentence(sentence, self.policy)}", s.bullet) for sentence in note.sentences]
            flow.append(KeepTogether([self.p(title, s.subheading), *body]))
        return flow

    def financial_notes(self) -> list[Any]:
        flow = self.notes("financial_notes", self.report.financial_notes)
        flow += [
            self.p(f"{self.policy.report.balance_table_title} ({self.policy.report.unit_note_won})", self.styles.subheading),
            self.comparison_table(self.report.balance_comparison),
        ]
        return flow

    def assumptions(self) -> list[Any]:
        s, texts, report = self.styles, self.policy.report, self.report
        flow: list[Any] = [self.section("assumptions"), self.p(texts.assumptions_title, s.subheading)]
        flow += [self.p(f"· {text}", s.bullet) for text in report.assumptions]
        flow.append(self.p(texts.needs_check_title, s.subheading))
        items = report.needs_check or [texts.needs_check_none]
        flow += [self.p(f"· {text}", s.bullet) for text in items]
        return flow

    def evidence(self) -> list[Any]:
        s = self.styles
        rows = []
        for row in self.report.evidence:
            form = f"{row.form} (코드 {row.code})" if row.code else row.form
            rows.append(
                [
                    self.p(row.item, s.small),
                    self.p(f"{row.year}년", s.small),
                    self.p(format_evidence_value(row, self.policy), s.small_right),
                    self.p(row.file, s.small),
                    self.p(form, s.small),
                    self.p(str(row.page) if row.page is not None else "-", s.small_right),
                ]
            )
        header_style = ParagraphStyle("small_center", parent=s.small, alignment=TA_CENTER)
        table = self.table(
            ["항목", "연도", "값", "파일", "서식", "쪽"],
            rows,
            [0.18, 0.09, 0.14, 0.28, 0.25, 0.06],
            header_style=header_style,
        )
        return [self.section("evidence"), table]

    def story(self) -> list[Any]:
        flow: list[Any] = []
        flow += self.cover()
        flow += self.key_metrics()
        flow += self.chart_grid()
        flow += self.comments()
        flow += self.income_comparison()
        flow += self.sga()
        flow += self.notes("tax_notes", self.report.tax_notes)
        flow += self.financial_notes()
        flow += self.assumptions()
        flow += self.evidence()
        return flow


def render_report_pdf(
    report: Report, policy: Policy, out_path: Path, base_dir: Path | None = None
) -> Path:
    """보고서 PDF를 만든다. 한글 글꼴을 찾지 못하면 FontNotFoundError."""
    font_path = find_font(policy, base_dir)
    font = TTFont(FONT_NAME, str(font_path))
    pdfmetrics.registerFont(font)
    charts = render_charts(report, policy, font_path)
    builder = _Builder(report, policy, charts, font)
    footer = f"{policy.issuer.footer}  |  {report.company}"

    def draw_footer(canvas: Canvas, doc: Any) -> None:
        canvas.saveState()
        canvas.setFont(FONT_NAME, 8)
        canvas.setFillColor(colors.HexColor("#555555"))
        canvas.drawString(MARGIN, 9 * mm, footer)
        canvas.drawRightString(A4[0] - MARGIN, 9 * mm, f"{doc.page}쪽")
        canvas.restoreState()

    out_path.parent.mkdir(parents=True, exist_ok=True)
    document = SimpleDocTemplate(
        str(out_path),
        pagesize=A4,
        leftMargin=MARGIN,
        rightMargin=MARGIN,
        topMargin=MARGIN,
        bottomMargin=MARGIN + 4 * mm,
        title=f"{policy.report.title} - {report.company} {report.current_year}",
        author=policy.issuer.name,
    )
    document.build(builder.story(), onFirstPage=draw_footer, onLaterPages=draw_footer)
    if builder.missing_glyphs:
        log.warning(
            "font_missing_glyphs",
            font=font_path.name,
            characters="".join(sorted(builder.missing_glyphs)),
        )
    log.info("report_pdf_written", path=str(out_path), font=font_path.name)
    return out_path
