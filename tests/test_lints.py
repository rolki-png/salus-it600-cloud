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


def paho_thread_offences(source: str) -> tuple[set[str], list[str]]:
    tree = ast.parse(source)
    wired = {
        target.attr: node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Attribute) and target.attr.startswith("on_")
    }
    callback_names = {
        value.attr if isinstance(value, ast.Attribute) else value.id
        for value in wired.values()
        if isinstance(value, (ast.Attribute, ast.Name))
    }
    offences = [
        f"line {node.lineno}: asyncio.get_event_loop()"
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and node.attr == "get_event_loop"
    ]
    for function in ast.walk(tree):
        if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)) or function.name not in callback_names:
            continue
        for node in ast.walk(function):
            if (
                isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)
                and node.value.id == "self"
                and node.attr != "_loop"
                and not node.attr.startswith("_handle_")
            ):
                offences.append(f"line {node.lineno}: {function.name} reads self.{node.attr}")
    return callback_names, offences


def test_paho_callbacks_only_hand_off_to_the_event_loop() -> None:
    callbacks, offences = paho_thread_offences((PACKAGE / "mqtt.py").read_text())
    assert {"_on_connect", "_on_disconnect", "_on_subscribe", "_on_message"} <= callbacks
    assert not offences, (
        f"mqtt.py {offences}: paho calls on_* callbacks from its network thread, where there is no event loop "
        "(the Python 3.13 MQTT timeout fixed in v1.0.3). A callback may only call "
        "self._loop.call_soon_threadsafe(self._handle_<event>, ...), with self._loop captured by "
        "asyncio.get_running_loop() in async_start. Do the work in the _handle_<event> method."
    )


def _called_name(call: ast.Call) -> str | None:
    if isinstance(call.func, ast.Name):
        return call.func.id
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    return None


def observation_construction_offences(tree: ast.Module) -> list[int]:
    allowed = {
        id(node)
        for function in ast.walk(tree)
        if isinstance(function, ast.FunctionDef) and function.name == "observation_from_cells"
        for node in ast.walk(function)
    }
    return [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and _called_name(node) == "ThermostatObservation" and id(node) not in allowed
    ]


def test_only_observation_from_cells_builds_an_observation() -> None:
    offences = [f"{path.name}:{line}" for path, tree in _sources() for line in observation_construction_offences(tree)]
    assert not offences, (
        f"{offences}: build a ThermostatObservation only through heating.observation_from_cells (or observe), "
        "which decodes each raw cell once, so a bool cannot pose as heating and an absent key stays UNREPORTED."
    )


def test_observation_lint_catches_a_direct_construction() -> None:
    source = (
        "def observation_from_cells():\n    return ThermostatObservation()\n"
        "def shortcut():\n    return heating.ThermostatObservation(demand=Demand.IDLE)\n"
    )
    assert observation_construction_offences(ast.parse(source)) == [4]
