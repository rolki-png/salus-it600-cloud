from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import SalusCloudCoordinator, SalusDevice
from .devices import is_climate_device
from .entity import SalusEntity
from .heating import TEMPERATURE_METRICS, TemperatureMetric, observe


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: SalusCloudCoordinator = entry.runtime_data
    entities: list[SensorEntity] = []
    for device_id, device in coordinator.data.items():
        if is_climate_device(device):
            for metric in TEMPERATURE_METRICS:
                entities.append(SalusHeatingTemperature(coordinator, device_id, device, metric))
            continue
        if _has_temperature(device):
            entities.append(SalusCloudTemperatureSensor(coordinator, device_id, device))
        if _has_humidity(device):
            entities.append(SalusCloudHumiditySensor(coordinator, device_id, device))
        if _has_battery(device):
            entities.append(SalusCloudBatterySensor(coordinator, device_id, device))
    async_add_entities(entities)


def _has_temperature(device: dict[str, Any]) -> bool:
    device_type = device["type"].lower()
    return device_type == "temperature_sensor" or (device_type == "sensor" and "temperature" in device)


def _has_humidity(device: dict[str, Any]) -> bool:
    return "humidity" in device or ("status" in device and "humidity" in device["status"])


def _has_battery(device: dict[str, Any]) -> bool:
    return "battery" in device or "battery_level" in device or ("status" in device and "battery" in device["status"])


def _value(data: dict[str, Any], fields: tuple[str, ...], scale_above_100: bool) -> float | None:
    for source in (data, data.get("status") if isinstance(data.get("status"), dict) else {}):
        for field in fields:
            if field in source:
                value = source[field]
                if scale_above_100 and isinstance(value, int) and value > 100:
                    return value / 100.0
                return float(value)
    return None


class SalusHeatingTemperature(SalusEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_suggested_display_precision = 1

    def __init__(
        self,
        coordinator: SalusCloudCoordinator,
        device_id: str,
        device: SalusDevice,
        metric: TemperatureMetric,
    ) -> None:
        super().__init__(coordinator, device_id, device)
        self._metric = metric
        self._attr_name = f"{self._attr_name} {metric.name}"
        self._attr_unique_id = f"{self._attr_unique_id}_{metric.slug}"

    @property
    def native_value(self) -> float | None:
        observation = observe(self.shadow_properties)
        sample = None if observation is None else self._metric.read(observation)
        return None if sample is None else sample.celsius()


class SalusCloudSensor(SalusEntity, SensorEntity):
    def __init__(
        self, coordinator: SalusCloudCoordinator, device_id: str, device: SalusDevice, sensor_type: str
    ) -> None:
        super().__init__(coordinator, device_id, device)
        self._attr_name = f"{self._attr_name} {sensor_type.title()}"
        self._attr_unique_id = f"{self._attr_unique_id}_{sensor_type}"


class SalusCloudTemperatureSensor(SalusCloudSensor):
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS

    def __init__(self, coordinator: SalusCloudCoordinator, device_id: str, device: SalusDevice) -> None:
        super().__init__(coordinator, device_id, device, "temperature")

    @property
    def native_value(self) -> float | None:
        return _value(self.device_data, ("temperature", "current_temperature", "LocalTemperature"), True)


class SalusCloudHumiditySensor(SalusCloudSensor):
    _attr_device_class = SensorDeviceClass.HUMIDITY
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = PERCENTAGE

    def __init__(self, coordinator: SalusCloudCoordinator, device_id: str, device: SalusDevice) -> None:
        super().__init__(coordinator, device_id, device, "humidity")

    @property
    def native_value(self) -> float | None:
        return _value(self.device_data, ("humidity", "current_humidity"), True)


class SalusCloudBatterySensor(SalusCloudSensor):
    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = PERCENTAGE

    def __init__(self, coordinator: SalusCloudCoordinator, device_id: str, device: SalusDevice) -> None:
        super().__init__(coordinator, device_id, device, "battery")

    @property
    def native_value(self) -> float | None:
        return _value(self.device_data, ("battery", "battery_level", "battery_percentage"), False)
