from __future__ import annotations

import re

_GRAMMAR = re.compile(r"ep\d+:s[A-Za-z0-9]+:[A-Z][A-Za-z0-9_]*")


def _reported(name: str) -> str:
    if not _GRAMMAR.fullmatch(name) or name.rpartition(":")[2].startswith("Set"):
        raise ValueError(f"{name!r} is not a reported shadow property name")
    return name


HOLD_TYPE = _reported("ep9:sIT600TH:HoldType")
HEATING_SETPOINT_X100 = _reported("ep9:sIT600TH:HeatingSetpoint_x100")
SYSTEM_MODE = _reported("ep9:sIT600TH:SystemMode")
RUNNING_STATE = _reported("ep9:sIT600TH:RunningState")
LOCAL_TEMPERATURE_X100 = _reported("ep9:sIT600TH:LocalTemperature_x100")
BATTERY_VOLTAGE = _reported("ep9:sBasicS:BatteryVoltage")
ON_OFF = _reported("ep9:sOnOffS:OnOff")
TRIGGER_RULE = _reported("ep0:sRule:TriggerRule")


def desired(reported: str) -> str:
    service, _, prop = reported.rpartition(":")
    return f"{service}:Set{prop}"


def reported(desired_name: str) -> str:
    service, _, prop = desired_name.rpartition(":")
    return f"{service}:{prop.removeprefix('Set')}"
