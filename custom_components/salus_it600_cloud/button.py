from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import OneTouchRule
from .const import DOMAIN
from .coordinator import SalusCloudCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: SalusCloudCoordinator = entry.runtime_data
    async_add_entities([SalusOneTouchButton(coordinator, rule) for rule in coordinator.rules])


class SalusOneTouchButton(ButtonEntity):
    _attr_has_entity_name = False

    def __init__(self, coordinator: SalusCloudCoordinator, rule: OneTouchRule) -> None:
        self._coordinator = coordinator
        self._rule = rule
        gateway = coordinator.get_gateway(rule.gateway_id)
        self._attr_name = f"{gateway.name if gateway else 'Salus iT600'} {rule.name}"
        self._attr_unique_id = f"{DOMAIN}_{rule.gateway_id}_onetouch_{rule.id}"
        device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{rule.gateway_id}_onetouch_rules")},
            name="OneTouch Rules",
            manufacturer="Salus",
            model="OneTouch Rules",
        )
        if via_device_id := coordinator.gateway_device_id(rule.gateway_id):
            device_info["via_device_id"] = via_device_id
        self._attr_device_info = device_info

    async def async_press(self) -> None:
        await self._coordinator.async_trigger_rule(self._rule.gateway_id, self._rule.trigger_key)
