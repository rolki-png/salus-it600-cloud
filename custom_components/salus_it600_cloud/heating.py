from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Any

from . import wire


class Demand(Enum):
    UNREPORTED = "unreported"
    UNREADABLE = "unreadable"
    IDLE = "idle"
    HEATING = "heating"

    def is_on(self) -> bool | None:
        if self is Demand.HEATING:
            return True
        if self is Demand.IDLE:
            return False
        if self is Demand.UNREPORTED or self is Demand.UNREADABLE:
            return None
        raise RuntimeError(self)

    def attribute_label(self) -> str | None:
        if self is Demand.HEATING:
            return "Heating"
        if self is Demand.IDLE:
            return "Idle"
        if self is Demand.UNREPORTED or self is Demand.UNREADABLE:
            return None
        raise RuntimeError(self)


class HeatingAction(Enum):
    UNKNOWN = "unknown"
    OFF = "off"
    IDLE = "idle"
    HEATING = "heating"


@dataclass(frozen=True)
class CentiCelsius:
    hundredths: int

    def __post_init__(self) -> None:
        if type(self.hundredths) is not int:
            raise TypeError("hundredths must be an int")

    def celsius(self) -> float:
        return self.hundredths / 100


def centi_celsius_from_degrees(degrees: float) -> int:
    return round(degrees * 100)


@dataclass(frozen=True)
class CentiPercent:
    hundredths: int

    def __post_init__(self) -> None:
        if type(self.hundredths) is not int:
            raise TypeError("hundredths must be an int")

    def percent(self) -> float:
        return self.hundredths / 100


@dataclass(frozen=True)
class DeciVolt:
    tenths: int

    def __post_init__(self) -> None:
        if type(self.tenths) is not int:
            raise TypeError("tenths must be an int")

    def volts(self) -> float:
        return self.tenths / 10


class HoldCell(Enum):
    UNREPORTED = "unreported"
    UNREADABLE = "unreadable"

    @property
    def is_standby(self) -> bool:
        return False


@dataclass(frozen=True)
class HoldReported:
    raw: int

    def __post_init__(self) -> None:
        if type(self.raw) is not int:
            raise TypeError("hold raw must be an int")

    @property
    def is_standby(self) -> bool:
        return self.raw == _PRESET_RAW["away"]


Hold = HoldCell | HoldReported


@dataclass(frozen=True)
class SystemMode:
    raw: int | None

    def __post_init__(self) -> None:
        if self.raw is not None and type(self.raw) is not int:
            raise TypeError("system mode raw must be an int")

    @property
    def is_off(self) -> bool:
        return self.raw == SYSTEM_MODE_OFF


@dataclass(frozen=True)
class ThermostatObservation:
    demand: Demand
    local_temperature: CentiCelsius | None
    heating_setpoint: CentiCelsius | None
    hold: Hold
    system_mode: SystemMode
    battery: DeciVolt | None
    cooling_setpoint: CentiCelsius | None = None
    ambient_temperature: CentiCelsius | None = None
    humidity: CentiPercent | None = None
    battery_level: int | None = None

    def __post_init__(self) -> None:
        if type(self.demand) is not Demand:
            raise TypeError("demand must be Demand")
        for sample in (self.local_temperature, self.heating_setpoint, self.cooling_setpoint, self.ambient_temperature):
            if sample is not None and type(sample) is not CentiCelsius:
                raise TypeError("temperatures must be CentiCelsius")
        if type(self.hold) not in (HoldCell, HoldReported) or type(self.system_mode) is not SystemMode:
            raise TypeError("hold and system_mode must be their domain types")
        if self.battery is not None and type(self.battery) is not DeciVolt:
            raise TypeError("battery must be DeciVolt")
        if self.humidity is not None and type(self.humidity) is not CentiPercent:
            raise TypeError("humidity must be CentiPercent")
        if self.battery_level is not None and not (type(self.battery_level) is int and 0 <= self.battery_level <= 100):
            raise TypeError("battery_level must be an int percentage")

    def hold_label(self) -> str | None:
        match self.hold:
            case HoldCell.UNREPORTED:
                return None
            case HoldCell.UNREADABLE:
                return "Unknown"
            case HoldReported(raw):
                return _HOLD_LABELS.get(raw, f"Unknown ({raw})")

    def system_mode_label(self) -> str | None:
        if self.system_mode.raw is None:
            return None
        return _SYSTEM_LABELS.get(self.system_mode.raw, f"Unknown ({self.system_mode.raw})")

    def preset_slug(self) -> str | None:
        match self.hold:
            case HoldCell.UNREPORTED:
                return None
            case HoldCell.UNREADABLE:
                return "manual"
            case HoldReported(raw):
                return next((slug for slug, preset in _PRESET_RAW.items() if preset == raw), "manual")


@dataclass(frozen=True)
class TemperatureMetric:
    slug: str
    name: str
    read: Callable[[ThermostatObservation], CentiCelsius | None]
    only_when_reported: bool = False


@dataclass(frozen=True)
class DemandMetric:
    slug: str
    name: str


def _read_local(observation: ThermostatObservation) -> CentiCelsius | None:
    return observation.local_temperature


def _read_setpoint(observation: ThermostatObservation) -> CentiCelsius | None:
    return observation.heating_setpoint


def _read_cooling_setpoint(observation: ThermostatObservation) -> CentiCelsius | None:
    return observation.cooling_setpoint


def _read_ambient(observation: ThermostatObservation) -> CentiCelsius | None:
    return observation.ambient_temperature


TEMPERATURE_METRICS: tuple[TemperatureMetric, ...] = (
    TemperatureMetric("local_temperature", "Temperature", _read_local),
    TemperatureMetric("heating_setpoint", "Heating setpoint", _read_setpoint),
    TemperatureMetric("cooling_setpoint", "Cooling setpoint", _read_cooling_setpoint, only_when_reported=True),
    TemperatureMetric("ambient_temperature", "Ambient temperature", _read_ambient, only_when_reported=True),
)
RUNNING_STATE_METRIC = DemandMetric("running_state", "Running state")

_PRESET_RAW: dict[str, int] = {"schedule": 0, "manual": 2, "away": 7}
PRESET_SLUGS: tuple[str, ...] = tuple(_PRESET_RAW)

SYSTEM_MODE_OFF = 0
SYSTEM_MODE_HEAT = 4
_SYSTEM_LABELS: dict[int, str] = {SYSTEM_MODE_OFF: "Off", 1: "Auto", SYSTEM_MODE_HEAT: "Heat"}
_HOLD_LABELS: dict[int, str] = {
    _PRESET_RAW["schedule"]: "Schedule",
    _PRESET_RAW["manual"]: "Manual Hold",
    _PRESET_RAW["away"]: "Frost Protection",
}


class ThermostatCommand:
    HOLD = wire.desired(wire.HOLD_TYPE)
    SETPOINT = wire.desired(wire.HEATING_SETPOINT_X100)
    COOLING_SETPOINT = wire.desired(wire.COOLING_SETPOINT_X100)
    SYSTEM_MODE = wire.desired(wire.SYSTEM_MODE)


def hold_raw_for_preset(slug: str) -> int:
    return _PRESET_RAW[slug]


def decode_running_cell(value: Any, *, present: bool) -> Demand:
    """bool is a subclass of int and True == 1, so a bool cell must not count as heating."""
    if not present:
        return Demand.UNREPORTED
    if type(value) is not int:
        return Demand.UNREADABLE
    if value == 1:
        return Demand.HEATING
    return Demand.IDLE


def decode_x100_cell(value: Any, *, present: bool) -> CentiCelsius | None:
    if not present or type(value) is not int:
        return None
    return CentiCelsius(value)


def observation_from_cells(
    *,
    running: Any,
    running_present: bool,
    local_x100: Any,
    local_present: bool,
    setpoint_x100: Any,
    setpoint_present: bool,
    hold: Any,
    hold_present: bool,
    system_mode: Any,
    system_mode_present: bool,
    battery_tenths: Any,
    battery_present: bool,
    cooling_x100: Any = None,
    ambient_x100: Any = None,
    humidity_x100: Any = None,
    battery_level: Any = None,
) -> ThermostatObservation:
    """Pass None for an absent cell after battery_present. Absent and unreadable cells both read as unknown."""
    tenths = _optional_int(battery_present, battery_tenths)
    return ThermostatObservation(
        demand=decode_running_cell(running, present=running_present),
        local_temperature=decode_x100_cell(local_x100, present=local_present),
        heating_setpoint=decode_x100_cell(setpoint_x100, present=setpoint_present),
        hold=_hold(hold_present, hold),
        system_mode=SystemMode(_optional_int(system_mode_present, system_mode)),
        battery=None if tenths is None else DeciVolt(tenths),
        cooling_setpoint=decode_x100_cell(cooling_x100, present=True),
        ambient_temperature=decode_x100_cell(ambient_x100, present=True),
        humidity=CentiPercent(humidity_x100) if type(humidity_x100) is int else None,
        battery_level=battery_level if type(battery_level) is int and 0 <= battery_level <= 100 else None,
    )


def observe(properties: Mapping[str, Any]) -> ThermostatObservation | None:
    if not properties:
        return None
    running_present, running = _cell(properties, wire.RUNNING_STATE)
    local_present, local = _cell(properties, wire.LOCAL_TEMPERATURE_X100)
    setpoint_present, setpoint = _cell(properties, wire.HEATING_SETPOINT_X100)
    hold_present, hold = _cell(properties, wire.HOLD_TYPE)
    system_present, system_mode = _cell(properties, wire.SYSTEM_MODE)
    battery_present, battery = _cell(properties, wire.BATTERY_VOLTAGE)
    return observation_from_cells(
        running=running,
        running_present=running_present,
        local_x100=local,
        local_present=local_present,
        setpoint_x100=setpoint,
        setpoint_present=setpoint_present,
        hold=hold,
        hold_present=hold_present,
        system_mode=system_mode,
        system_mode_present=system_present,
        battery_tenths=battery,
        battery_present=battery_present,
        cooling_x100=properties.get(wire.COOLING_SETPOINT_X100),
        ambient_x100=_any_endpoint_value(properties, wire.AMBIENT_TEMPERATURE_X100),
        humidity_x100=_any_endpoint_value(properties, wire.HUMIDITY_X100),
        battery_level=properties.get(wire.BATTERY_LEVEL),
    )


def heating_action(observation: ThermostatObservation | None) -> HeatingAction:
    if observation is None:
        return HeatingAction.UNKNOWN
    demand = observation.demand
    if demand is Demand.UNREPORTED or demand is Demand.UNREADABLE:
        return HeatingAction.UNKNOWN
    if observation.hold.is_standby or observation.system_mode.is_off:
        return HeatingAction.OFF
    if demand is Demand.HEATING:
        return HeatingAction.HEATING
    if demand is Demand.IDLE:
        return HeatingAction.IDLE
    raise RuntimeError(demand)


def _cell(properties: Mapping[str, Any], key: str) -> tuple[bool, Any]:
    if key not in properties:
        return False, None
    return True, properties[key]


def _any_endpoint_value(properties: Mapping[str, Any], service_property: str) -> Any:
    found = wire.find_reported(properties, service_property)
    return None if found is None else found[1]


def _optional_int(present: bool, value: Any) -> int | None:
    if present and type(value) is int:
        return value
    return None


def _hold(present: bool, value: Any) -> Hold:
    if not present:
        return HoldCell.UNREPORTED
    if type(value) is int:
        return HoldReported(value)
    return HoldCell.UNREADABLE
