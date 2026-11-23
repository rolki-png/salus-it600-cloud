from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import SalusCloudCoordinator, SalusDevice


class SalusEntity(CoordinatorEntity[SalusCloudCoordinator]):
    _attr_has_entity_name = False

    def __init__(self, coordinator: SalusCloudCoordinator, device_id: str, device: SalusDevice) -> None:
        super().__init__(coordinator)
        self._device_id = device_id
        listing = device.listing
        gateway = coordinator.get_gateway(listing.gateway_id)
        self._attr_name = f"{gateway.name if gateway else 'Salus iT600'} {listing.name or device_id}"
        self._attr_unique_id = f"{DOMAIN}_{listing.gateway_id}_{device_id}"
        self._attr_device_info = coordinator.device_info(listing)

    @property
    def _device(self) -> SalusDevice | None:
        return self.coordinator.get_device(self._device_id)

    @property
    def listing_fields(self) -> Mapping[str, Any]:
        return device.listing.fields if (device := self._device) else {}

    @property
    def shadow_properties(self) -> Mapping[str, Any]:
        return device.properties if (device := self._device) else {}

    @property
    def available(self) -> bool:
        return super().available and (device := self._device) is not None and device.fresh
