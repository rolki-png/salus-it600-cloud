from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import wire
from .coordinator import SalusCloudCoordinator
from .devices import is_switch_device
from .entity import SalusEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: SalusCloudCoordinator = entry.runtime_data
    async_add_entities(
        [
            SalusCloudSwitch(coordinator, device_id, device)
            for device_id, device in coordinator.data.items()
            if is_switch_device(device.listing)
        ]
    )


class SalusCloudSwitch(SalusEntity, SwitchEntity):
    @property
    def is_on(self) -> bool | None:
        value = self.shadow_properties.get(wire.ON_OFF)
        return value == 1 if value is not None else None

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_switch(self._device_id, True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_switch(self._device_id, False)
