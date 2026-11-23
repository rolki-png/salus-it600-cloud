from __future__ import annotations

from typing import Any

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import SalusCloudCoordinator
from .devices import is_climate_device
from .entity import SalusEntity
from .heating import PRESET_SLUGS, HeatingAction, heating_action, hold_raw_for_preset, observe

_HVAC_ACTION: dict[HeatingAction, HVACAction | None] = {
    HeatingAction.HEATING: HVACAction.HEATING,
    HeatingAction.IDLE: HVACAction.IDLE,
    HeatingAction.OFF: HVACAction.OFF,
    HeatingAction.UNKNOWN: None,
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: SalusCloudCoordinator = entry.runtime_data
    async_add_entities(
        [
            SalusCloudClimate(coordinator, device_id, device)
            for device_id, device in coordinator.data.items()
            if is_climate_device(device)
        ]
    )


class SalusCloudClimate(SalusEntity, ClimateEntity):
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.PRESET_MODE
        | ClimateEntityFeature.TURN_ON
        | ClimateEntityFeature.TURN_OFF
    )
    _attr_hvac_modes = [HVACMode.HEAT, HVACMode.OFF]
    _attr_preset_modes = list(PRESET_SLUGS)
    _attr_target_temperature_step = 0.5
    _attr_min_temp = 5.0
    _attr_max_temp = 35.0

    @property
    def current_temperature(self) -> float | None:
        observation = observe(self.shadow_properties)
        sample = None if observation is None else observation.local_temperature
        return None if sample is None else sample.celsius()

    @property
    def target_temperature(self) -> float | None:
        observation = observe(self.shadow_properties)
        sample = None if observation is None else observation.heating_setpoint
        return None if sample is None else sample.celsius()

    @property
    def hvac_mode(self) -> HVACMode | None:
        observation = observe(self.shadow_properties)
        if observation is None:
            return None
        if observation.hold.is_standby or observation.system_mode.is_off:
            return HVACMode.OFF
        return HVACMode.HEAT

    @property
    def hvac_action(self) -> HVACAction | None:
        return _HVAC_ACTION[heating_action(observe(self.shadow_properties))]

    @property
    def preset_mode(self) -> str | None:
        observation = observe(self.shadow_properties)
        return None if observation is None else observation.preset_slug()

    async def async_set_temperature(self, **kwargs: Any) -> None:
        if (temperature := kwargs.get(ATTR_TEMPERATURE)) is not None:
            await self.coordinator.async_set_temperature(self._device_id, temperature)

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        await self.coordinator.async_set_hold_type(self._device_id, hold_raw_for_preset(preset_mode))

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        slug = "away" if hvac_mode == HVACMode.OFF else "schedule"
        await self.coordinator.async_set_hold_type(self._device_id, hold_raw_for_preset(slug))

    async def async_turn_on(self) -> None:
        await self.async_set_hvac_mode(HVACMode.HEAT)

    async def async_turn_off(self) -> None:
        await self.async_set_hvac_mode(HVACMode.OFF)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        observation = observe(self.shadow_properties)
        if observation is None:
            return {}
        attributes: dict[str, Any] = {}
        if (hold_type := observation.hold_label()) is not None:
            attributes["hold_type"] = hold_type
        if (system_mode := observation.system_mode_label()) is not None:
            attributes["system_mode"] = system_mode
        if observation.battery is not None:
            attributes["battery_voltage"] = f"{observation.battery.volts():.1f}V"
        if (running_state := observation.demand.attribute_label()) is not None:
            attributes["running_state"] = running_state
        return attributes
