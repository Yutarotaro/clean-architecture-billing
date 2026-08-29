"""依存の向きが守られていることを機械的に検査する。

クリーンアーキテクチャの唯一のルールは「依存は内側にしか向かない」であり、それは
レビューで守るものではなく、テストで守るものである。図をいくら描いても、締切前に
ドメイン層から SQLAlchemy を import する誰かは必ず現れる。

このテストが落ちるときは、たいてい設計上の判断が必要になっている。import を消して
通せば済む場合もあれば、インターフェースを 1 つ足すべき場合もある。
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "src"
PACKAGE = SRC / "billing"

#: 各層が import してよい内部パッケージ。自分自身は常に許可される。
ALLOWED_INTERNAL: dict[str, set[str]] = {
    "domain": set(),
    "application": {"domain"},
    "infrastructure": {"domain", "application"},
    "presentation": {"domain", "application", "infrastructure"},
}

#: 各層が import してよい外部ライブラリ。ここに書かれていないものは禁止。
ALLOWED_EXTERNAL: dict[str, set[str]] = {
    # 標準ライブラリだけ。ドメインは「ただの Python」でなければならない。
    "domain": set(),
    # ユースケース層も同じ。ポート越しにしか外の世界に触れない。
    "application": set(),
    "infrastructure": {"sqlalchemy", "boto3", "botocore"},
    "presentation": {"fastapi", "pydantic", "starlette"},
}

_STDLIB = set(sys.stdlib_module_names)


def _layer_of(path: Path) -> str:
    return path.relative_to(PACKAGE).parts[0]


def _modules_in(layer: str) -> list[Path]:
    return sorted((PACKAGE / layer).rglob("*.py"))


def _imported_roots(path: Path) -> set[str]:
    """そのファイルが import しているトップレベルのモジュール名を集める。"""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:  # 相対 import はこのプロジェクトでは使っていない
                raise AssertionError(f"{path}: relative import is not used in this project")
            if node.module:
                roots.add(node.module)
    return roots


@pytest.mark.parametrize("layer", sorted(ALLOWED_INTERNAL))
def test_layer_does_not_import_outer_layers(layer: str) -> None:
    allowed = ALLOWED_INTERNAL[layer] | {layer}
    violations: list[str] = []

    for path in _modules_in(layer):
        for module in _imported_roots(path):
            if not module.startswith("billing."):
                continue
            imported_layer = module.split(".")[1]
            if imported_layer not in allowed:
                violations.append(f"{path.relative_to(SRC)} imports {module}")

    assert not violations, "依存が外側を向いている:\n" + "\n".join(violations)


@pytest.mark.parametrize("layer", sorted(ALLOWED_EXTERNAL))
def test_layer_does_not_import_unexpected_third_party(layer: str) -> None:
    allowed = ALLOWED_EXTERNAL[layer]
    violations: list[str] = []

    for path in _modules_in(layer):
        for module in _imported_roots(path):
            root = module.split(".")[0]
            if root == "billing" or root in _STDLIB:
                continue
            if root not in allowed:
                violations.append(f"{path.relative_to(SRC)} imports {module}")

    assert not violations, (
        f"{layer} 層が許可されていない外部ライブラリに依存している:\n" + "\n".join(violations)
    )


def test_domain_is_pure_python() -> None:
    """ドメイン層は標準ライブラリしか使っていない。

    この 1 つのテストが通るかぎり、ドメインのビジネスルールは DB もフレームワークも
    ネットワークもない場所で実行でき、テストは常にミリ秒で終わる。
    """
    third_party: set[str] = set()
    for path in _modules_in("domain"):
        for module in _imported_roots(path):
            root = module.split(".")[0]
            if root != "billing" and root not in _STDLIB:
                third_party.add(root)

    assert not third_party, f"domain 層が外部ライブラリに依存している: {sorted(third_party)}"
