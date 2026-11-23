from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

_GRAMMAR = re.compile(r"ep(\d+):(s[A-Za-z0-9]+:[A-Z][A-Za-z0-9_]*)")


def _reported(name: str) -> str:
    if not _GRAMMAR.fullmatch(name) or name.rpartition(":")[2].startswith("Set"):
        raise ValueError(f"{name!r} is not a reported shadow property name")
    return name


def _any_endpoint(service_property: str) -> str:
    _reported(f"ep0:{service_property}")
    return service_property


HOLD_TYPE = _reported("ep9:sIT600TH:HoldType")
HEATING_SETPOINT_X100 = _reported("ep9:sIT600TH:HeatingSetpoint_x100")
COOLING_SETPOINT_X100 = _reported("ep9:sIT600TH:CoolingSetpoint_x100")
SYSTEM_MODE = _reported("ep9:sIT600TH:SystemMode")
RUNNING_STATE = _reported("ep9:sIT600TH:RunningState")
LOCAL_TEMPERATURE_X100 = _reported("ep9:sIT600TH:LocalTemperature_x100")
BATTERY_LEVEL = _reported("ep9:sIT600TH:BatteryLevel")
BATTERY_VOLTAGE = _reported("ep9:sBasicS:BatteryVoltage")
ON_OFF = _reported("ep9:sOnOffS:OnOff")
TRIGGER_RULE = _reported("ep0:sRule:TriggerRule")

AMBIENT_TEMPERATURE_X100 = _any_endpoint("sTempS:MeasuredValue_x100")
HUMIDITY_X100 = _any_endpoint("sHumiS:MeasuredValue_x100")
OCCUPANCY = _any_endpoint("sOccupS:Occupancy")
ZONE_ALARM = _any_endpoint("sIASZS:ErrorIASZSAlarmed1")


def find_reported(properties: Mapping[str, Any], service_property: str) -> tuple[str, Any] | None:
    """Return the key and value of service_property on the lowest endpoint that reports it."""
    endpoints = sorted(
        (int(match[1]), key)
        for key in properties
        if (match := _GRAMMAR.fullmatch(key)) and match[2] == service_property
    )
    if not endpoints:
        return None
    key = endpoints[0][1]
    return key, properties[key]


def desired(reported: str) -> str:
    service, _, prop = reported.rpartition(":")
    return f"{service}:Set{prop}"


def reported(desired_name: str) -> str:
    service, _, prop = desired_name.rpartition(":")
    return f"{service}:{prop.removeprefix('Set')}"
