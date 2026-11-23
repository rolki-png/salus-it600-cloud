from __future__ import annotations

from .api import DeviceListing

CLIMATE_MODELS = ("HTRP-RF", "TS600", "VS10", "VS20", "SQ610", "FC600")
SWITCH_MODELS = ("RS600", "SPE600", "SR600")


def is_climate_device(device: DeviceListing) -> bool:
    return (
        device.type.lower() in ("thermostat", "climate")
        or device.model.upper().startswith(CLIMATE_MODELS)
        or "thermostat" in device.name.lower()
    )


def is_switch_device(device: DeviceListing) -> bool:
    return device.type.lower() in ("switch", "relay") or device.model.upper().startswith(SWITCH_MODELS)
