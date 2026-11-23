from __future__ import annotations

from typing import Any

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import SalusCloudCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: SalusCloudCoordinator = entry.runtime_data
    async_add_entities(
        [
            SalusOneTouchButton(coordinator, rule)
            for rule in coordinator.rules
            if rule.get("rule_trigger_key") and not ((rule.get("rule") or {}).get("name") or "").startswith("_")
        ]
    )


class SalusOneTouchButton(ButtonEntity):
    _attr_has_entity_name = False

    def __init__(self, coordinator: SalusCloudCoordinator, rule: dict[str, Any]) -> None:
        self._coordinator = coordinator
        self._gateway_id = rule.get("_gateway_id", "")
        self._rule_trigger_key = rule["rule_trigger_key"]
        gateway = coordinator.get_gateway(self._gateway_id)
        rule_name = (rule.get("rule") or {}).get("name") or "Unknown Rule"
        self._attr_name = f"{gateway.name if gateway else 'Salus iT600'} {rule_name}"
        self._attr_unique_id = f"{DOMAIN}_{self._gateway_id}_onetouch_{rule.get('id', '')}"
        device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{self._gateway_id}_onetouch_rules")},
            name="OneTouch Rules",
            manufacturer="Salus",
            model="OneTouch Rules",
        )
        if via_device_id := coordinator.gateway_device_id(self._gateway_id):
            device_info["via_device_id"] = via_device_id
        self._attr_device_info = device_info

    async def async_press(self) -> None:
        await self._coordinator.async_trigger_rule(self._gateway_id, self._rule_trigger_key)
