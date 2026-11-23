from __future__ import annotations

from .api import DeviceListing

CLIMATE_MODELS = ("HTRP-RF", "TS600", "VS10", "VS20", "SQ610", "FC600", "C900T", "TRV3RF")
SWITCH_MODELS = ("RS600", "SPE600", "SR600", "SP600")
SWITCH_TYPES = ("switch", "relay", "smart_relay")


def is_climate_device(device: DeviceListing) -> bool:
    return (
        device.type.lower() in ("thermostat", "climate")
        or device.model.upper().startswith(CLIMATE_MODELS)
        or "thermostat" in device.name.lower()
    )


def is_switch_device(device: DeviceListing) -> bool:
    return device.type.lower() in SWITCH_TYPES or device.model.upper().startswith(SWITCH_MODELS)
