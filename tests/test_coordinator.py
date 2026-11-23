from datetime import timedelta
from unittest.mock import patch

import pytest
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.salus_it600_cloud.api import (
    SalusCloudAuthenticationError,
    SalusCloudConnectionError,
)

from . import fixtures
from .helpers import CALL_LATER, FakeApi, FakeConnection, create_coordinator

HOLD = "ep9:sIT600TH:HoldType"
TEMPERATURE = "ep9:sIT600TH:LocalTemperature_x100"


async def test_refresh_combines_device_list_with_reported_states() -> None:
    coordinator, _, _ = await create_coordinator()

    assert list(coordinator.data) == [
        fixtures.THERMOSTAT_ID,
        fixtures.THERMOSTAT_2_ID,
        fixtures.RELAY_ID,
        fixtures.GATEWAY_ID,
    ]
    thermostat = coordinator.data[fixtures.THERMOSTAT_ID]
    assert thermostat["_shadow_properties"][HOLD] == 0
    assert thermostat["_shadow_properties"][TEMPERATURE] == 2200
    assert thermostat["_shadow_device_index"] == "11"
    assert thermostat["_fresh"] is True
    assert coordinator.data[fixtures.GATEWAY_ID]["_fresh"] is False


async def test_connection_uses_gateway_client_id_and_all_devices() -> None:
    api = FakeApi()
    _, connection, _ = await create_coordinator(api)

    assert connection.kwargs["client_id_prefix"] == fixtures.GATEWAY_CODE
    assert connection.kwargs["credentials_provider"] == api.get_iot_credentials
    assert connection.started_with == [
        fixtures.THERMOSTAT_CODE,
        fixtures.THERMOSTAT_2_CODE,
        fixtures.RELAY_CODE,
        fixtures.GATEWAY_CODE,
    ]


async def test_device_list_is_refreshed_only_every_fifteen_minutes() -> None:
    api = FakeApi()
    coordinator, _, clock = await create_coordinator(api)

    clock[0] = 899
    await coordinator._async_update_data()
    assert api.metadata_calls == 1

    clock[0] = 900
    await coordinator._async_update_data()
    assert api.metadata_calls == 2


async def test_device_missing_in_poll_keeps_last_known_state() -> None:
    api = FakeApi()
    coordinator, _, clock = await create_coordinator(api)
    api.shadows = {fixtures.RELAY_CODE: fixtures.relay_shadow(1)}
    clock[0] = 30

    data = await coordinator._async_update_data()

    assert data[fixtures.THERMOSTAT_ID]["_shadow_properties"][HOLD] == 0
    assert data[fixtures.THERMOSTAT_ID]["_shadow_properties"][TEMPERATURE] == 2200
    assert data[fixtures.THERMOSTAT_ID]["_fresh"] is True
    assert data[fixtures.RELAY_ID]["_shadow_properties"]["ep9:sOnOffS:OnOff"] == 1


async def test_failed_poll_keeps_recent_states() -> None:
    api = FakeApi()
    coordinator, _, clock = await create_coordinator(api)
    api.shadows = SalusCloudConnectionError("timeout")
    clock[0] = 30

    data = await coordinator._async_update_data()

    assert data[fixtures.THERMOSTAT_ID]["_shadow_properties"][HOLD] == 0
    assert data[fixtures.THERMOSTAT_ID]["_fresh"] is True


async def test_poll_failing_for_ten_minutes_fails_update() -> None:
    api = FakeApi()
    coordinator, _, clock = await create_coordinator(api)
    api.shadows = SalusCloudConnectionError("timeout")
    clock[0] = 601

    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()


async def test_failed_device_list_refresh_uses_previous_list() -> None:
    api = FakeApi()
    coordinator, _, clock = await create_coordinator(api)
    api.metadata_error = SalusCloudConnectionError("timeout")
    api.shadows = {fixtures.THERMOSTAT_CODE: fixtures.thermostat_shadow(hold_type=2)}
    clock[0] = 900

    data = await coordinator._async_update_data()

    assert data[fixtures.THERMOSTAT_ID]["_shadow_properties"][HOLD] == 2


async def test_rejected_credentials_start_reauthentication() -> None:
    api = FakeApi()
    coordinator, _, _ = await create_coordinator(api)
    api.shadows = SalusCloudAuthenticationError("rejected")

    with pytest.raises(ConfigEntryAuthFailed):
        await coordinator._async_update_data()


async def test_mode_change_without_sync_targets_selected_thermostat() -> None:
    coordinator, connection, _ = await create_coordinator(sync=False)

    with patch(CALL_LATER):
        await coordinator.async_set_hold_type(fixtures.THERMOSTAT_ID, 7)

    assert connection.published == [(fixtures.THERMOSTAT_CODE, "11", {"ep9:sIT600TH:SetHoldType": 7})]
    assert coordinator.data[fixtures.THERMOSTAT_ID]["_shadow_properties"][HOLD] == 7
    assert coordinator.data[fixtures.THERMOSTAT_2_ID]["_shadow_properties"][HOLD] == 0


async def test_mode_change_with_sync_targets_all_thermostats_of_gateway() -> None:
    coordinator, connection, _ = await create_coordinator(sync=True)

    with patch(CALL_LATER):
        await coordinator.async_set_hold_type(fixtures.THERMOSTAT_2_ID, 7)

    assert sorted(connection.published) == [
        (fixtures.THERMOSTAT_2_CODE, "11", {"ep9:sIT600TH:SetHoldType": 7}),
        (fixtures.THERMOSTAT_CODE, "11", {"ep9:sIT600TH:SetHoldType": 7}),
    ]
    assert coordinator.data[fixtures.THERMOSTAT_ID]["_shadow_properties"][HOLD] == 7
    assert coordinator.data[fixtures.THERMOSTAT_2_ID]["_shadow_properties"][HOLD] == 7


async def test_temperature_change_is_not_synchronized() -> None:
    coordinator, connection, _ = await create_coordinator(sync=True)

    with patch(CALL_LATER):
        await coordinator.async_set_temperature(fixtures.THERMOSTAT_ID, 22.5)

    assert connection.published == [
        (
            fixtures.THERMOSTAT_CODE,
            "11",
            {
                "ep9:sIT600TH:SetHeatingSetpoint_x100": 2250,
                "ep9:sIT600TH:SetHoldType": 2,
                "ep9:sIT600TH:SetSystemMode": 4,
            },
        )
    ]
    assert coordinator.data[fixtures.THERMOSTAT_ID]["_shadow_properties"]["ep9:sIT600TH:HeatingSetpoint_x100"] == 2250


async def test_partially_failed_sync_reports_failed_thermostat() -> None:
    connection = FakeConnection(fail_codes={fixtures.THERMOSTAT_2_CODE})
    coordinator, _, _ = await create_coordinator(sync=True, connection=connection)

    with patch(CALL_LATER), pytest.raises(HomeAssistantError, match="Termostat koupelna"):
        await coordinator.async_set_hold_type(fixtures.THERMOSTAT_ID, 7)

    assert coordinator.data[fixtures.THERMOSTAT_ID]["_shadow_properties"][HOLD] == 7
    assert coordinator.data[fixtures.THERMOSTAT_2_ID]["_shadow_properties"][HOLD] == 0


async def test_poll_before_device_applies_command_does_not_revert_state() -> None:
    api = FakeApi()
    coordinator, _, clock = await create_coordinator(api)
    with patch(CALL_LATER):
        await coordinator.async_set_hold_type(fixtures.THERMOSTAT_ID, 7)
    clock[0] = 10

    data = await coordinator._async_update_data()

    assert data[fixtures.THERMOSTAT_ID]["_shadow_properties"][HOLD] == 7


async def test_command_schedules_confirmation_refresh_only_without_push() -> None:
    coordinator, connection, _ = await create_coordinator()

    with patch(CALL_LATER) as call_later:
        await coordinator.async_set_hold_type(fixtures.THERMOSTAT_ID, 7)
    assert call_later.call_args.args[1] == 10

    connection.kwargs["on_push_available"](True)
    with patch(CALL_LATER) as call_later:
        await coordinator.async_set_hold_type(fixtures.THERMOSTAT_ID, 0)
    call_later.assert_not_called()


async def test_pushed_shadow_document_updates_state() -> None:
    coordinator, connection, clock = await create_coordinator()
    clock[0] = 700

    connection.kwargs["on_shadow_document"](
        fixtures.THERMOSTAT_CODE, fixtures.thermostat_shadow(hold_type=2, temperature_x100=2350)
    )

    thermostat = coordinator.data[fixtures.THERMOSTAT_ID]
    assert thermostat["_shadow_properties"][HOLD] == 2
    assert thermostat["_shadow_properties"][TEMPERATURE] == 2350
    assert thermostat["_fresh"] is True


async def test_push_availability_adjusts_polling_interval() -> None:
    coordinator, connection, _ = await create_coordinator()

    connection.kwargs["on_push_available"](True)
    assert coordinator.update_interval == timedelta(minutes=5)

    connection.kwargs["on_push_available"](False)
    assert coordinator.update_interval == timedelta(seconds=30)


async def test_onetouch_rule_is_triggered_through_gateway_shadow() -> None:
    coordinator, connection, _ = await create_coordinator()

    await coordinator.async_trigger_rule(fixtures.GATEWAY_ID, fixtures.RULE_TRIGGER_KEY)

    assert connection.published == [
        (fixtures.GATEWAY_CODE, "000000000001", {"ep0:sRule:SetTriggerRule": fixtures.RULE_TRIGGER_KEY})
    ]


async def test_shutdown_stops_connection() -> None:
    coordinator, connection, _ = await create_coordinator()

    await coordinator.async_shutdown()

    assert connection.stopped is True
