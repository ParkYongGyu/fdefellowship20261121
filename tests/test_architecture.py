"""레이어 의존 방향과 print() 사용을 소스에서 직접 검사한다."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src" / "taxreport"
# 숫자가 클수록 위 레이어. 위에서 아래로만 import할 수 있다.
RANK = {"domain": 0, "extract": 1, "analyze": 2, "render": 3, "cli": 4}
CONFIG_ALLOWED = {"config", "domain"}


def _layer(path: Path) -> str:
    relative = path.relative_to(SRC)
    return relative.parts[0] if len(relative.parts) > 1 else relative.stem


def _imported_layers(tree: ast.AST) -> set[str]:
    layers: set[str] = set()
    for node in ast.walk(tree):
        names: list[str] = []
        if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.append(node.module)
        elif isinstance(node, ast.ImportFrom) and node.level > 0:
            pytest.fail("상대 import는 쓰지 않습니다(레이어 검사를 위해 절대 경로로 적습니다)")
        elif isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        for name in names:
            parts = name.split(".")
            if parts[0] == "taxreport" and len(parts) > 1:
                layers.add(parts[1])
    return layers


SOURCES = sorted(p for p in SRC.rglob("*.py") if p.name != "__init__.py")


def test_sources_exist() -> None:
    assert SOURCES
    assert {_layer(p) for p in SOURCES} == {*RANK, "config"}


@pytest.mark.parametrize("path", SOURCES, ids=lambda p: str(p.relative_to(SRC)))
def test_layer_direction(path: Path) -> None:
    layer = _layer(path)
    imported = _imported_layers(ast.parse(path.read_text(encoding="utf-8")))
    if layer == "config":
        assert imported <= CONFIG_ALLOWED, f"config는 domain만 import할 수 있습니다: {imported}"
        return
    for other in imported - {"config"}:
        assert other in RANK, f"알 수 없는 레이어: {other}"
        assert RANK[other] <= RANK[layer], f"{layer} → {other} import는 역방향입니다"


@pytest.mark.parametrize("path", SOURCES, ids=lambda p: str(p.relative_to(SRC)))
def test_print_only_in_cli(path: Path) -> None:
    if _layer(path) == "cli":
        return
    tree = ast.parse(path.read_text(encoding="utf-8"))
    calls = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "print"
    ]
    assert not calls, f"print()는 cli.py에서만 씁니다: {path.name} {calls}"


def test_domain_does_no_io() -> None:
    forbidden = {"pdfplumber", "yaml", "reportlab", "matplotlib", "pathlib", "os", "structlog"}
    for path in (SRC / "domain").glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        roots: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                roots.add(node.module.split(".")[0])
            elif isinstance(node, ast.Import):
                roots.update(alias.name.split(".")[0] for alias in node.names)
        assert not roots & forbidden, f"domain은 파일·PDF·설정을 읽지 않습니다: {roots & forbidden}"


def test_no_company_specific_values_in_source() -> None:
    """회사명과 표준서식 쪽 번호를 코드·설정에 박지 않는다(발행자 문구는 policy.yaml)."""
    for path in [*SOURCES, *SRC.rglob("*.yaml")]:
        text = path.read_text(encoding="utf-8")
        assert "샘플컴퍼니" not in text, path.name
        if path.suffix == ".py":
            assert "샘플세무법인" not in text, path.name
