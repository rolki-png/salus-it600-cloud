from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
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
from .heating import TEMPERATURE_METRICS, TemperatureMetric, ThermostatObservation, observe


def _has_temperature(device: DeviceListing) -> bool:
    device_type = device.type.lower()
    return device_type == "temperature_sensor" or (device_type == "sensor" and "temperature" in device.fields)


def _has_humidity(device: DeviceListing) -> bool:
    fields = device.fields
    return "humidity" in fields or ("status" in fields and "humidity" in fields["status"])


def _has_battery(device: DeviceListing) -> bool:
    fields = device.fields
    return "battery" in fields or "battery_level" in fields or ("status" in fields and "battery" in fields["status"])


def _reported_ambient(observation: ThermostatObservation) -> float | None:
    sample = observation.ambient_temperature
    return None if sample is None else sample.celsius()


def _reported_humidity(observation: ThermostatObservation) -> float | None:
    sample = observation.humidity
    return None if sample is None else sample.percent()


def _reported_battery(observation: ThermostatObservation) -> float | None:
    return observation.battery_level


@dataclass(frozen=True)
class DeviceReading:
    """A reading the shadow reports or, failing that, the device listing carries."""

    kind: str
    device_class: SensorDeviceClass
    unit: str
    listed: Callable[[DeviceListing], bool]
    reported: Callable[[ThermostatObservation], float | None]
    listing_fields: tuple[str, ...]
    scale_above_100: bool
    on_thermostats: bool = True


DEVICE_READINGS: tuple[DeviceReading, ...] = (
    DeviceReading(
        "temperature",
        SensorDeviceClass.TEMPERATURE,
        UnitOfTemperature.CELSIUS,
        _has_temperature,
        _reported_ambient,
        ("temperature", "current_temperature", "LocalTemperature"),
        scale_above_100=True,
        on_thermostats=False,
    ),
    DeviceReading(
        "humidity",
        SensorDeviceClass.HUMIDITY,
        PERCENTAGE,
        _has_humidity,
        _reported_humidity,
        ("humidity", "current_humidity"),
        scale_above_100=True,
    ),
    DeviceReading(
        "battery",
        SensorDeviceClass.BATTERY,
        PERCENTAGE,
        _has_battery,
        _reported_battery,
        ("battery", "battery_level", "battery_percentage"),
        scale_above_100=False,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: SalusCloudCoordinator = entry.runtime_data
    entities: list[SensorEntity] = []
    for device_id, device in coordinator.data.items():
        observation = observe(device.properties)
        thermostat = is_climate_device(device.listing)
        if thermostat:
            for metric in TEMPERATURE_METRICS:
                if not metric.only_when_reported or (observation is not None and metric.read(observation) is not None):
                    entities.append(SalusHeatingTemperature(coordinator, device_id, device, metric))
        for reading in DEVICE_READINGS:
            if thermostat and not reading.on_thermostats:
                continue
            if reading.listed(device.listing) or _shadow_value(reading, observation) is not None:
                entities.append(SalusCloudSensor(coordinator, device_id, device, reading))
    async_add_entities(entities)


def _shadow_value(reading: DeviceReading, observation: ThermostatObservation | None) -> float | None:
    return None if observation is None else reading.reported(observation)


def _value(data: Mapping[str, Any], fields: tuple[str, ...], scale_above_100: bool) -> float | None:
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
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self, coordinator: SalusCloudCoordinator, device_id: str, device: SalusDevice, reading: DeviceReading
    ) -> None:
        super().__init__(coordinator, device_id, device)
        self._reading = reading
        self._attr_device_class = reading.device_class
        self._attr_native_unit_of_measurement = reading.unit
        self._attr_name = f"{self._attr_name} {reading.kind.title()}"
        self._attr_unique_id = f"{self._attr_unique_id}_{reading.kind}"

    @property
    def native_value(self) -> float | None:
        if (value := _shadow_value(self._reading, observe(self.shadow_properties))) is not None:
            return value
        return _value(self.listing_fields, self._reading.listing_fields, self._reading.scale_above_100)
