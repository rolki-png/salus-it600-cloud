"""Shadow property names are spelt only in wire.py."""

import ast
import re
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1] / "custom_components" / "salus_it600_cloud"
WIRE = "ep9:sIT600TH:"
WIRE_LITERAL = re.compile(r"ep_?\d+:")


def test_shadow_property_names_are_spelt_only_in_wire_py() -> None:
    offences = [
        f"{path.name}:{node.lineno} {node.value!r}"
        for path in sorted(PACKAGE.glob("*.py"))
        if path.name != "wire.py"
        for node in ast.walk(ast.parse(path.read_text()))
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and WIRE_LITERAL.search(node.value)
    ]
    assert not offences, (
        f"{offences}: spell a shadow property once in wire.py as its reported name and use "
        "wire.desired() for the command, so a typo cannot hide in a second spelling."
    )


def test_cube_names_no_host_and_no_wire_keys() -> None:
    text = (PACKAGE / "cube.py").read_text()
    lowered = text.lower()
    assert WIRE not in text
    assert "http://" not in lowered
    assert "https://" not in lowered
    assert "salusconnect" not in lowered


def test_live_poll_does_not_import_cube() -> None:
    for name in ("coordinator.py", "__init__.py"):
        text = (PACKAGE / name).read_text()
        assert "from .cube" not in text
        assert "import cube" not in text
