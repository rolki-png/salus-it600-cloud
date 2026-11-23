from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import wire
from .api import DeviceListing
from .coordinator import SalusCloudCoordinator, SalusDevice
from .devices import is_climate_device
from .entity import SalusEntity
from .heating import RUNNING_STATE_METRIC, observe

BINARY_SENSOR_TYPES = ("door_sensor", "window_sensor", "motion_sensor", "binary_sensor")
MODEL_CLASSES: dict[str, BinarySensorDeviceClass] = {
    "WLS": BinarySensorDeviceClass.DOOR,
    "OS600": BinarySensorDeviceClass.DOOR,
    "MS600": BinarySensorDeviceClass.MOTION,
}
SHADOW_FLAGS: dict[str, BinarySensorDeviceClass | None] = {
    wire.ZONE_ALARM: None,
    wire.OCCUPANCY: BinarySensorDeviceClass.OCCUPANCY,
}
TRUE_VALUES = ("on", "true", "1", "open", "active")


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: SalusCloudCoordinator = entry.runtime_data
    entities: list[BinarySensorEntity] = []
    for device_id, device in coordinator.data.items():
        flag = _shadow_flag(device.properties)
        if _is_binary_sensor_device(device.listing) or flag is not None:
            device_class = _get_device_class(device.listing) or (None if flag is None else SHADOW_FLAGS[flag[0]])
            entities.append(SalusCloudBinarySensor(coordinator, device_id, device, device_class))
        if is_climate_device(device.listing):
            entities.append(SalusRunningState(coordinator, device_id, device))
    async_add_entities(entities)


def _is_binary_sensor_device(device: DeviceListing) -> bool:
    return device.type.lower() in BINARY_SENSOR_TYPES or device.model.upper().startswith(tuple(MODEL_CLASSES))


def _get_device_class(device: DeviceListing) -> BinarySensorDeviceClass | None:
    device_type = device.type.lower()
    if "door" in device_type or "window" in device_type:
        return BinarySensorDeviceClass.DOOR
    if "motion" in device_type:
        return BinarySensorDeviceClass.MOTION
    if "occupancy" in device_type:
        return BinarySensorDeviceClass.OCCUPANCY
    model = device.model.upper()
    return next((device_class for prefix, device_class in MODEL_CLASSES.items() if model.startswith(prefix)), None)


def _shadow_flag(properties: Mapping[str, Any]) -> tuple[str, Any] | None:
    """Return the first flag in SHADOW_FLAGS the shadow reports, with its raw value."""
    for flag in SHADOW_FLAGS:
        if (found := wire.find_reported(properties, flag)) is not None:
            return flag, found[1]
    return None


def _as_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.lower() in TRUE_VALUES
    if isinstance(value, int):
        return value == 1
    return None


class SalusRunningState(SalusEntity, BinarySensorEntity):
    _attr_device_class = BinarySensorDeviceClass.HEAT

    def __init__(self, coordinator: SalusCloudCoordinator, device_id: str, device: SalusDevice) -> None:
        super().__init__(coordinator, device_id, device)
        self._attr_name = f"{self._attr_name} {RUNNING_STATE_METRIC.name}"
        self._attr_unique_id = f"{self._attr_unique_id}_{RUNNING_STATE_METRIC.slug}"

    @property
    def is_on(self) -> bool | None:
        observation = observe(self.shadow_properties)
        return None if observation is None else observation.demand.is_on()


class SalusCloudBinarySensor(SalusEntity, BinarySensorEntity):
    def __init__(
        self,
        coordinator: SalusCloudCoordinator,
        device_id: str,
        device: SalusDevice,
        device_class: BinarySensorDeviceClass | None,
    ) -> None:
        super().__init__(coordinator, device_id, device)
        self._attr_device_class = device_class

    @property
    def is_on(self) -> bool | None:
        if (flag := _shadow_flag(self.shadow_properties)) is not None:
            return flag[1] == 1 if type(flag[1]) is int else None
        data = self.listing_fields
        for field in ("is_on", "state", "active", "triggered", "open"):
            if field in data and (value := _as_bool(data[field])) is not None:
                return value
        status = data.get("status")
        if isinstance(status, dict):
            for field in ("state", "active", "triggered"):
                if field in status and (value := _as_bool(status[field])) is not None:
                    return value
        return False
