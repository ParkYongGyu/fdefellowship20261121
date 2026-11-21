"""진입점. extract → analyze → render를 차례로 부른다."""

from __future__ import annotations

import argparse
import logging
import re
import sys
from collections.abc import Sequence
from datetime import date
from pathlib import Path

import structlog

from taxreport.analyze.builder import build_report
from taxreport.analyze.metrics import display_label
from taxreport.config.loader import ConfigError, FormsConfig, load_forms, load_policy
from taxreport.domain.models import Filing, SourcedValue, ValueStatus
from taxreport.extract.intake import IntakeError, extract_filing, load_case
from taxreport.extract.reader import PdfReadError
from taxreport.render.report_pdf import FontNotFoundError, render_report_pdf

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_INTAKE = 2
EXIT_FONT = 3


def _configure_logging(verbose: bool) -> None:
    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.dev.ConsoleRenderer(colors=False),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.INFO if verbose else logging.WARNING
        ),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
    )


def _safe_name(text: str) -> str:
    return re.sub(r'[\\/:*?"<>|\s]+', "_", text).strip("_")


def _describe(value: SourcedValue) -> str:
    amount = "(값 없음)" if value.value is None else f"{value.value:,}"
    if value.source is None:
        return f"{amount} [{value.status.value}]"
    source = value.source
    code = f" 코드 {source.code}" if source.code else ""
    return f"{amount} [{value.status.value}] {source.form} {source.page}쪽{code}"


def _print_filing(filing: Filing, forms: FormsConfig) -> None:
    identity = filing.identity
    print(f"파일: {filing.source_file}")
    print(f"법인명: {identity.name} / 사업자등록번호: {identity.business_no}")
    print(f"사업연도: {identity.period_start} ~ {identity.period_end} ({identity.fiscal_year}년)")
    print("\n[서식 색인]")
    for ref in filing.forms_found:
        pages = ", ".join(str(page) for page in ref.pages)
        print(f"  {ref.name}: {pages}쪽")
    sections = (
        (forms.standard_forms.income_statement, filing.income_statement),
        (forms.standard_forms.balance_sheet, filing.balance_sheet),
    )
    for rules, items in sections:
        print(f"\n[{rules.form}] 금액이 있는 항목")
        for item in items.values():
            if item.amount.value:
                print(f"  {item.code:>4} {display_label(item.label)}: {_describe(item.amount)}")
    print(f"\n[{forms.tax_adjustment.form} · {forms.minimum_tax.form}]")
    labels = {**forms.tax_adjustment.labels, **forms.minimum_tax.labels}
    for key, value in filing.tax.values.items():
        print(f"  {labels.get(key, key)}: {_describe(value)}")
    print(f"\n[{forms.adjustments.form}] {len(filing.adjustments)}건")
    for adjustment in filing.adjustments:
        print(
            f"  {adjustment.kind.value} {adjustment.subject} "
            f"({adjustment.disposition or '-'}): {_describe(adjustment.amount)}"
        )
    print(f"\n[{forms.credits.form}] {len(filing.credits)}건")
    for credit in filing.credits:
        print(f"  {credit.name} ({credit.legal_basis or '-'}): {_describe(credit.amount)}")
    print(f"\n[{forms.losses.form}] 결손금 {len(filing.losses)}건")
    for record in filing.losses:
        print(f"  {record.fiscal_year_end} 발생액: {_describe(record.incurred)}")
    print(f"\n[부속명세서] 거래처별 잔액 {len(filing.schedules.balances)}건")
    keywords = [k for names in forms.schedules.accounts.values() for k in names]
    for balance in filing.schedules.for_account(keywords):
        print(f"  {balance.account} {balance.partner or ''}: {_describe(balance.amount)}")
    print(f"\n[경고] {len(filing.warnings)}건")
    for warning in filing.warnings:
        print(f"  {warning}")


def _run_build(args: argparse.Namespace) -> int:
    forms = load_forms()
    policy = load_policy(args.policy)
    case = load_case(args.pdf1, args.pdf2, forms)
    report = build_report(case, forms, policy, issued_on=date.today())
    out_dir: Path = args.out
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{_safe_name(report.company)}_{report.current_year}"
    pdf_path = render_report_pdf(report, policy, out_dir / f"{stem}_법인세분석보고서.pdf")
    json_path = out_dir / f"{stem}_추출결과.json"
    json_path.write_text(case.model_dump_json(indent=2), encoding="utf-8")

    assumed = sum(1 for row in report.evidence if row.status is ValueStatus.ASSUMED)
    print(f"회사: {report.company} ({report.business_no})")
    print(f"전기: {report.prior_year}년 ({case.prior.source_file})")
    print(f"당기: {report.current_year}년 ({case.current.source_file})")
    print(f"보고서 PDF: {pdf_path}")
    print(f"추출결과 JSON: {json_path}")
    print(f"가정: {len(report.assumptions)}건 (가정으로 계산한 값 {assumed}개)")
    print(f"확인 필요: {len(report.needs_check)}건")
    for text in report.needs_check:
        print(f"  - {text}")
    return EXIT_OK


def _run_inspect(args: argparse.Namespace) -> int:
    forms = load_forms()
    _print_filing(extract_filing(args.pdf, forms), forms)
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="taxreport",
        description="세무조정계산서 PDF 2개(전기·당기)로 법인세 분석 보고서를 만듭니다.",
    )
    parser.add_argument("--verbose", action="store_true", help="진행 로그를 표준 오류로 출력")
    commands = parser.add_subparsers(dest="command", required=True)

    build = commands.add_parser("build", help="보고서 PDF와 추출결과 JSON 생성")
    build.add_argument("pdf1", type=Path, help="세무조정계산서 PDF (전기·당기 순서 무관)")
    build.add_argument("pdf2", type=Path, help="세무조정계산서 PDF (전기·당기 순서 무관)")
    build.add_argument("--out", type=Path, default=Path("output"), help="출력 폴더 (기본: output/)")
    build.add_argument("--policy", type=Path, default=None, help="policy.yaml 경로 (기본: 내장 설정)")
    build.set_defaults(handler=_run_build)

    inspect = commands.add_parser("inspect", help="서식 색인과 추출값·출처 출력 (검토용)")
    inspect.add_argument("pdf", type=Path, help="세무조정계산서 PDF")
    inspect.set_defaults(handler=_run_inspect)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    _configure_logging(args.verbose)
    try:
        return int(args.handler(args))
    except IntakeError as exc:
        print(f"접수 오류: {exc}", file=sys.stderr)
        return EXIT_INTAKE
    except FontNotFoundError as exc:
        print(f"글꼴 오류: {exc}", file=sys.stderr)
        return EXIT_FONT
    except (PdfReadError, ConfigError) as exc:
        print(f"오류: {exc}", file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
