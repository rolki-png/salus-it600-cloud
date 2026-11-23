from __future__ import annotations

from collections.abc import Mapping
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

from .api import DeviceListing
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
        if is_climate_device(device.listing):
            for metric in TEMPERATURE_METRICS:
                entities.append(SalusHeatingTemperature(coordinator, device_id, device, metric))
        elif _has_temperature(device.listing):
            entities.append(SalusCloudTemperatureSensor(coordinator, device_id, device))
        if _has_humidity(device.listing):
            entities.append(SalusCloudHumiditySensor(coordinator, device_id, device))
        if _has_battery(device.listing):
            entities.append(SalusCloudBatterySensor(coordinator, device_id, device))
    async_add_entities(entities)


def _has_temperature(device: DeviceListing) -> bool:
    device_type = device.type.lower()
    return device_type == "temperature_sensor" or (device_type == "sensor" and "temperature" in device.fields)


def _has_humidity(device: DeviceListing) -> bool:
    fields = device.fields
    return "humidity" in fields or ("status" in fields and "humidity" in fields["status"])


def _has_battery(device: DeviceListing) -> bool:
    fields = device.fields
    return "battery" in fields or "battery_level" in fields or ("status" in fields and "battery" in fields["status"])


_TEMPERATURE_DIVISORS: Mapping[str, int] = {"temperature": 1, "current_temperature": 1, "LocalTemperature": 1}
_HUMIDITY_DIVISORS: Mapping[str, int] = {"humidity": 1, "current_humidity": 1}
_BATTERY_DIVISORS: Mapping[str, int] = {"battery": 1, "battery_level": 1, "battery_percentage": 1}


def _value(data: Mapping[str, Any], divisors: Mapping[str, int]) -> float | None:
    for source in (data, data.get("status") if isinstance(data.get("status"), dict) else {}):
        for field, divisor in divisors.items():
            if field in source:
                return source[field] / divisor
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
        return _value(self.listing_fields, _TEMPERATURE_DIVISORS)


class SalusCloudHumiditySensor(SalusCloudSensor):
    _attr_device_class = SensorDeviceClass.HUMIDITY
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = PERCENTAGE

    def __init__(self, coordinator: SalusCloudCoordinator, device_id: str, device: SalusDevice) -> None:
        super().__init__(coordinator, device_id, device, "humidity")

    @property
    def native_value(self) -> float | None:
        return _value(self.listing_fields, _HUMIDITY_DIVISORS)


class SalusCloudBatterySensor(SalusCloudSensor):
    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = PERCENTAGE

    def __init__(self, coordinator: SalusCloudCoordinator, device_id: str, device: SalusDevice) -> None:
        super().__init__(coordinator, device_id, device, "battery")

    @property
    def native_value(self) -> float | None:
        return _value(self.listing_fields, _BATTERY_DIVISORS)
