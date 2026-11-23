"""Source lints. Each failure message names the fix."""

import ast
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1] / "custom_components" / "salus_it600_cloud"


def _sources() -> list[tuple[Path, ast.Module]]:
    return [(path, ast.parse(path.read_text(), str(path))) for path in sorted(PACKAGE.glob("*.py"))]


def _is_dict_get_with_default(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "get"
        and len(node.args) == 2
    )


def test_no_method_call_on_a_defaulted_get() -> None:
    offences = [
        f"{path.name}:{node.lineno}"
        for path, tree in _sources()
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and _is_dict_get_with_default(node.value)
    ]
    assert not offences, (
        f"{offences}: .get(key, default) returns None when the cloud sends the key as null, "
        "so a method called on it crashes (upstream issue #10). Write (x.get(key) or default).method(), "
        "or read a device field that api.parse_gateway_items has already made a string."
    )
