from unittest.mock import MagicMock

import pytest
from homeassistant.components.binary_sensor import BinarySensorDeviceClass
from homeassistant.components.climate import HVACAction

from custom_components.salus_it600_cloud import binary_sensor
from custom_components.salus_it600_cloud.binary_sensor import SalusCloudBinarySensor, SalusRunningState
from custom_components.salus_it600_cloud.api import parse_gateway_items
from custom_components.salus_it600_cloud.climate import SalusCloudClimate
from custom_components.salus_it600_cloud.coordinator import SalusDevice

from . import fixtures
from .helpers import FakeApi, create_coordinator


def _entity(coordinator: object, device_id: str = fixtures.THERMOSTAT_ID) -> SalusRunningState:
    entity = SalusRunningState(coordinator, device_id, coordinator.data[device_id])
    entity.hass = MagicMock()
    return entity


def _uid(device_id: str) -> str:
    return f"salus_it600_cloud_{fixtures.GATEWAY_ID}_{device_id}_running_state"


async def test_setup_creates_one_heat_sensor_per_thermostat() -> None:
    coordinator, _, _ = await create_coordinator()
    entry = MagicMock()
    entry.runtime_data = coordinator
    add_entities = MagicMock()

    await binary_sensor.async_setup_entry(MagicMock(), entry, add_entities)

    entities = add_entities.call_args.args[0]
    assert [entity.unique_id for entity in entities] == [
        _uid(fixtures.THERMOSTAT_ID),
        _uid(fixtures.THERMOSTAT_2_ID),
    ]
    assert all(type(entity) is SalusRunningState for entity in entities)
    assert all(not isinstance(entity, SalusCloudBinarySensor) for entity in entities)
    assert all(entity.device_class is BinarySensorDeviceClass.HEAT for entity in entities)


async def test_idle_demand_is_off() -> None:
    coordinator, _, _ = await create_coordinator()
    entity = _entity(coordinator)

    assert entity.is_on is False
    assert entity.available is True
    assert entity.name == "Apartmán A Termostat pokoj Running state"


async def test_missing_running_state_is_unknown_not_off() -> None:
    api = FakeApi()
    shadow = fixtures.thermostat_shadow(hold_type=0)
    del shadow["state"]["reported"]["11"]["properties"]["ep9:sIT600TH:RunningState"]
    api.shadows[fixtures.THERMOSTAT_CODE] = shadow
    coordinator, _, _ = await create_coordinator(api)
    entity = _entity(coordinator)

    assert entity.is_on is None
    assert entity.available is True


async def test_integer_one_is_on() -> None:
    api = FakeApi()
    shadow = fixtures.thermostat_shadow(hold_type=2)
    shadow["state"]["reported"]["11"]["properties"]["ep9:sIT600TH:RunningState"] = 1
    api.shadows[fixtures.THERMOSTAT_CODE] = shadow
    coordinator, _, _ = await create_coordinator(api)

    assert _entity(coordinator).is_on is True


async def test_bool_running_state_is_unknown() -> None:
    api = FakeApi()
    shadow = fixtures.thermostat_shadow(hold_type=2)
    shadow["state"]["reported"]["11"]["properties"]["ep9:sIT600TH:RunningState"] = True
    api.shadows[fixtures.THERMOSTAT_CODE] = shadow
    coordinator, _, _ = await create_coordinator(api)

    assert _entity(coordinator).is_on is None


async def test_unavailable_follows_freshness_not_the_key() -> None:
    api = FakeApi()
    del api.shadows[fixtures.THERMOSTAT_CODE]
    coordinator, _, _ = await create_coordinator(api)
    entity = _entity(coordinator)

    assert entity.available is False
    assert entity.is_on is None


async def test_calling_during_standby_is_on_while_climate_action_is_off() -> None:
    api = FakeApi()
    shadow = fixtures.thermostat_shadow(hold_type=7)
    shadow["state"]["reported"]["11"]["properties"]["ep9:sIT600TH:RunningState"] = 1
    api.shadows[fixtures.THERMOSTAT_CODE] = shadow
    coordinator, _, _ = await create_coordinator(api)
    climate = SalusCloudClimate(coordinator, fixtures.THERMOSTAT_ID, coordinator.data[fixtures.THERMOSTAT_ID])
    climate.hass = MagicMock()

    assert _entity(coordinator).is_on is True
    assert climate.hvac_action is HVACAction.OFF


async def _with_device(listing: dict[str, object], properties: dict[str, object]) -> dict[str, SalusCloudBinarySensor]:
    coordinator, _, _ = await create_coordinator()
    (device,), _ = parse_gateway_items(fixtures.GATEWAY_ID, [{"device_code": f"{listing['id']}-code", **listing}])
    coordinator.data[device.id] = SalusDevice(listing=device, properties=properties, shadow_index="1", fresh=True)
    entry = MagicMock()
    entry.runtime_data = coordinator
    add_entities = MagicMock()
    await binary_sensor.async_setup_entry(MagicMock(), entry, add_entities)
    return {
        entity.unique_id: entity
        for entity in add_entities.call_args.args[0]
        if type(entity) is SalusCloudBinarySensor
    }


def _base_uid(device_id: str) -> str:
    return f"salus_it600_cloud_{fixtures.GATEWAY_ID}_{device_id}"


@pytest.mark.parametrize(("raw", "is_on"), [(1, True), (0, False), (True, None), (None, None)])
async def test_zone_alarm_drives_the_door_sensor(raw: object, is_on: bool | None) -> None:
    entities = await _with_device(
        {"id": "door", "name": "Door", "model": "OS600"}, {"ep1:sIASZS:ErrorIASZSAlarmed1": raw}
    )

    door = entities[_base_uid("door")]
    assert (door.device_class, door.is_on) == (BinarySensorDeviceClass.DOOR, is_on)


async def test_unlisted_device_reporting_occupancy_gets_an_occupancy_sensor() -> None:
    entities = await _with_device({"id": "hall", "name": "Hall", "model": "XX1"}, {"ep1:sOccupS:Occupancy": 1})

    hall = entities[_base_uid("hall")]
    assert (hall.device_class, hall.is_on) == (BinarySensorDeviceClass.OCCUPANCY, True)


async def test_motion_model_is_a_motion_sensor_before_its_shadow_arrives() -> None:
    entities = await _with_device({"id": "pir", "name": "PIR", "model": "MS600"}, {})

    pir = entities[_base_uid("pir")]
    assert (pir.device_class, pir.is_on) == (BinarySensorDeviceClass.MOTION, False)


async def test_listed_leak_sensor_reads_the_zone_alarm_not_the_listing() -> None:
    entities = await _with_device(
        {"id": "leak", "name": "Leak", "model": "WLS600", "type": "binary_sensor", "state": "off"},
        {"ep1:sIASZS:ErrorIASZSAlarmed1": 1},
    )

    assert entities[_base_uid("leak")].is_on is True
