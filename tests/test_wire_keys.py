import ast
import re
from pathlib import Path

import pytest

from custom_components.salus_it600_cloud import wire

PACKAGE = Path(__file__).resolve().parents[1] / "custom_components" / "salus_it600_cloud"
WIRE = "ep9:sIT600TH:"
WIRE_LITERAL = re.compile(r"ep_?\d+:")


def test_shadow_property_names_are_spelt_only_in_wire_py() -> None:
    offences = [
        f"{path.relative_to(PACKAGE)}:{node.lineno} {node.value!r}"
        for path in sorted(PACKAGE.rglob("*.py"))
        if path != PACKAGE / "wire.py"
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


def test_catalog_rejects_the_v1_0_2_running_state_typo() -> None:
    with pytest.raises(ValueError):
        wire._reported("ep9:sIT600_TH:RunningState")


def test_find_reported_reads_the_lowest_endpoint_and_keeps_null_apart_from_absent() -> None:
    properties = {
        "ep9:sHumiS:MeasuredValue_x100": 5100,
        "ep1:sHumiS:MeasuredValue_x100": 4550,
        "ep1:sHumiS:SetMeasuredValue_x100": 9999,
        "ep_1:sHumiS:MeasuredValue_x100": 1,
        "ep2:sOccupS:Occupancy": None,
    }

    assert wire.find_reported(properties, wire.HUMIDITY_X100) == ("ep1:sHumiS:MeasuredValue_x100", 4550)
    assert wire.find_reported(properties, wire.OCCUPANCY) == ("ep2:sOccupS:Occupancy", None)
    assert wire.find_reported(properties, wire.ZONE_ALARM) is None


def test_any_endpoint_names_follow_the_reported_grammar() -> None:
    with pytest.raises(ValueError):
        wire._any_endpoint("sHumiS:SetMeasuredValue_x100")
    with pytest.raises(ValueError):
        wire._any_endpoint("ep1:sHumiS:MeasuredValue_x100")
