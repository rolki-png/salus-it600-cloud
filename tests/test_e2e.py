"""The integration in a real Home Assistant, faked only at the Salus HTTP API, Cognito and the AWS IoT broker."""

import json
import time
from collections.abc import Callable
from typing import Any
from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker, AiohttpClientMockResponse

from custom_components.salus_it600_cloud import wire
from custom_components.salus_it600_cloud.api import COGNITO_IDENTITY_URL, SalusCloudApi
from custom_components.salus_it600_cloud.const import DOMAIN, SERVICE_API_BASE_URL

from . import fixtures
from .test_api import _token
from .test_mqtt import _Broker

SHADOWS = {
    fixtures.THERMOSTAT_CODE: fixtures.thermostat_shadow(hold_type=0, temperature_x100=2200, setpoint_x100=2100),
    fixtures.THERMOSTAT_2_CODE: fixtures.thermostat_shadow(hold_type=2, temperature_x100=2300),
    fixtures.RELAY_CODE: fixtures.relay_shadow(0),
}


def _login(api: SalusCloudApi) -> None:
    api._access_token = "access"
    api._id_token = _token(int(time.time()) + 3600)
    api._refresh_token = "refresh"


def _cloud(aioclient_mock: AiohttpClientMocker, slider_details: dict[str, Any]) -> None:
    aioclient_mock.get(f"{SERVICE_API_BASE_URL}/occupants/slider_list", json=fixtures.slider_list())
    aioclient_mock.get(f"{SERVICE_API_BASE_URL}/occupants/slider_details", json=slider_details)
    aioclient_mock.post(f"{SERVICE_API_BASE_URL}/devices/device_shadows", json=fixtures.device_shadows(SHADOWS))

    async def cognito(method: str, url: Any, data: dict[str, Any]) -> AiohttpClientMockResponse:
        body = (
            {"IdentityId": "eu-central-1:1111"}
            if "IdentityPoolId" in data
            else {
                "IdentityId": "eu-central-1:1111",
                "Credentials": {
                    "AccessKeyId": "ASIAEXAMPLE",
                    "SecretKey": "secret",
                    "SessionToken": "session-token",
                    "Expiration": int(time.time()) + 3600,
                },
            }
        )
        return AiohttpClientMockResponse(method, url, json=body)

    aioclient_mock.post(COGNITO_IDENTITY_URL, side_effect=cognito)


@pytest.fixture
async def broker(hass: HomeAssistant) -> _Broker:
    broker = _Broker()
    with (
        patch.object(SalusCloudApi, "_sync_authenticate", _login),
        patch("paho.mqtt.client.Client", lambda **kwargs: broker.create_client(kwargs["client_id"])),
    ):
        yield broker


async def _set_up(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, slider_details: dict[str, Any]) -> str:
    _cloud(aioclient_mock, slider_details)
    entry = MockConfigEntry(domain=DOMAIN, data={"email": "owner@example.com", "password": "password"})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry.entry_id


def _entity_id(hass: HomeAssistant, platform: str, device_id: str) -> str:
    entity_id = er.async_get(hass).async_get_entity_id(platform, DOMAIN, f"{DOMAIN}_{fixtures.GATEWAY_ID}_{device_id}")
    assert entity_id, f"no {platform} entity for {device_id}"
    return entity_id


async def _until(hass: HomeAssistant, predicate: Callable[[], bool]) -> None:
    for _ in range(100):
        if predicate():
            return
        await hass.async_block_till_done()
    assert predicate()


async def test_thermostat_state_reaches_home_assistant(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, broker: _Broker
) -> None:
    await _set_up(hass, aioclient_mock, fixtures.slider_details())

    state = hass.states.get(_entity_id(hass, "climate", fixtures.THERMOSTAT_ID))
    assert state.state == "heat"
    assert state.attributes["current_temperature"] == 22.0
    assert state.attributes["temperature"] == 21.0
    assert state.attributes["preset_mode"] == "schedule"
    assert state.attributes["hvac_action"] == "idle"
    assert hass.states.get(_entity_id(hass, "switch", fixtures.RELAY_ID)).state == "off"


async def test_set_temperature_publishes_the_command_and_shows_it(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, broker: _Broker
) -> None:
    await _set_up(hass, aioclient_mock, fixtures.slider_details())
    entity_id = _entity_id(hass, "climate", fixtures.THERMOSTAT_ID)

    await hass.services.async_call(
        "climate", "set_temperature", {"entity_id": entity_id, "temperature": 22.5}, blocking=True
    )

    (topic, payload, qos) = broker.clients[-1].published[-1]
    assert topic == f"$aws/things/{fixtures.THERMOSTAT_CODE}/shadow/update"
    assert payload["state"]["desired"]["11"]["properties"][wire.desired(wire.HEATING_SETPOINT_X100)] == 2250
    assert qos == 1
    state = hass.states.get(entity_id)
    assert state.attributes["temperature"] == 22.5
    assert state.attributes["preset_mode"] == "manual"


async def test_pushed_shadow_document_updates_the_state(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, broker: _Broker
) -> None:
    await _set_up(hass, aioclient_mock, fixtures.slider_details())
    entity_id = _entity_id(hass, "switch", fixtures.RELAY_ID)

    broker.clients[-1].deliver(
        f"$aws/things/{fixtures.RELAY_CODE}/shadow/update/documents",
        {"current": fixtures.relay_shadow(1)},
    )

    await _until(hass, lambda: hass.states.get(entity_id).state == "on")


async def test_grouped_account_with_null_types_gets_its_thermostats(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, broker: _Broker
) -> None:
    await _set_up(hass, aioclient_mock, fixtures.grouped_slider_details())

    assert hass.states.get(_entity_id(hass, "climate", fixtures.THERMOSTAT_ID)).attributes["current_temperature"] == 22.0
    assert hass.states.get(_entity_id(hass, "climate", fixtures.THERMOSTAT_2_ID)) is not None
    assert hass.states.get(_entity_id(hass, "switch", fixtures.RELAY_ID)) is not None


async def test_devices_hang_off_their_gateway_in_the_device_registry(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, broker: _Broker
) -> None:
    entry_id = await _set_up(hass, aioclient_mock, fixtures.slider_details())
    devices = dr.async_get(hass)

    gateway = devices.async_get_device_by_identifier((DOMAIN, fixtures.GATEWAY_ID), entry_id)
    thermostat = devices.async_get_device_by_identifier((DOMAIN, fixtures.THERMOSTAT_ID), entry_id)
    assert thermostat.via_device_id == gateway.id


async def test_thermostat_listing_with_humidity_and_battery_gets_those_sensors(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, broker: _Broker
) -> None:
    details = fixtures.slider_details()
    details["data"]["items"][0].update({"humidity": 45, "battery": 80, "temperature": 2200})
    await _set_up(hass, aioclient_mock, details)
    registry = er.async_get(hass)

    def sensor(suffix: str) -> str | None:
        unique_id = f"{DOMAIN}_{fixtures.GATEWAY_ID}_{fixtures.THERMOSTAT_ID}_{suffix}"
        return registry.async_get_entity_id("sensor", DOMAIN, unique_id)

    assert hass.states.get(sensor("humidity")).state == "45.0"
    assert hass.states.get(sensor("battery")).state == "80.0"
    assert sensor("temperature") is None
    assert hass.states.get(sensor("local_temperature")).state == "22.0"


async def test_cooling_thermostat_takes_a_target_range_through_home_assistant(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, broker: _Broker, monkeypatch: pytest.MonkeyPatch
) -> None:
    shadow = fixtures.thermostat_shadow(hold_type=0)
    shadow["state"]["reported"]["11"]["properties"].update(
        {"ep9:sIT600TH:CoolingSetpoint_x100": 2450, "ep1:sHumiS:MeasuredValue_x100": 5200}
    )
    monkeypatch.setitem(SHADOWS, fixtures.THERMOSTAT_CODE, shadow)
    await _set_up(hass, aioclient_mock, fixtures.slider_details())
    entity_id = _entity_id(hass, "climate", fixtures.THERMOSTAT_ID)
    state = hass.states.get(entity_id)
    assert (state.attributes["target_temp_low"], state.attributes["target_temp_high"]) == (21.0, 24.5)
    assert state.attributes["current_humidity"] == 52.0
    assert "target_temp_high" not in hass.states.get(_entity_id(hass, "climate", fixtures.THERMOSTAT_2_ID)).attributes

    await hass.services.async_call(
        "climate",
        "set_temperature",
        {"entity_id": entity_id, "target_temp_low": 20.0, "target_temp_high": 25.5},
        blocking=True,
    )

    (topic, payload, _) = broker.clients[-1].published[-1]
    assert topic == f"$aws/things/{fixtures.THERMOSTAT_CODE}/shadow/update"
    assert payload["state"]["desired"]["11"]["properties"] == {
        wire.desired(wire.HEATING_SETPOINT_X100): 2000,
        wire.desired(wire.COOLING_SETPOINT_X100): 2550,
        wire.desired(wire.HOLD_TYPE): 2,
    }
    state = hass.states.get(entity_id)
    assert (state.attributes["target_temp_low"], state.attributes["target_temp_high"]) == (20.0, 25.5)


async def test_shadow_only_readings_reach_home_assistant_as_entities(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, broker: _Broker, monkeypatch: pytest.MonkeyPatch
) -> None:
    shadow = fixtures.thermostat_shadow(hold_type=0)
    shadow["state"]["reported"]["11"]["properties"].update(
        {"ep9:sIT600TH:BatteryLevel": 87, "ep1:sOccupS:Occupancy": 1}
    )
    monkeypatch.setitem(SHADOWS, fixtures.THERMOSTAT_CODE, shadow)
    await _set_up(hass, aioclient_mock, fixtures.slider_details())
    registry = er.async_get(hass)
    base = f"{DOMAIN}_{fixtures.GATEWAY_ID}_{fixtures.THERMOSTAT_ID}"

    battery = hass.states.get(registry.async_get_entity_id("sensor", DOMAIN, f"{base}_battery"))
    occupancy = hass.states.get(registry.async_get_entity_id("binary_sensor", DOMAIN, base))
    assert (battery.state, battery.attributes["device_class"]) == ("87", "battery")
    assert (occupancy.state, occupancy.attributes["device_class"]) == ("on", "occupancy")
