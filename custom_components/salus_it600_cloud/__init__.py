from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import SalusCloudApi, SalusCloudAuthenticationError, SalusCloudConnectionError
from .coordinator import SalusCloudCoordinator

PLATFORMS: list[Platform] = [
    Platform.CLIMATE,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
]

type SalusConfigEntry = ConfigEntry[SalusCloudCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: SalusConfigEntry) -> bool:
    api = SalusCloudApi(entry.data[CONF_EMAIL], entry.data[CONF_PASSWORD], session=async_get_clientsession(hass))
    try:
        await api.authenticate()
    except SalusCloudAuthenticationError as err:
        raise ConfigEntryAuthFailed(str(err)) from err
    except SalusCloudConnectionError as err:
        raise ConfigEntryNotReady(str(err)) from err

    coordinator = SalusCloudCoordinator(hass, entry, api)
    await coordinator.async_config_entry_first_refresh()

    await coordinator.async_start_connection()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: SalusConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
