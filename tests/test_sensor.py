from dataclasses import replace
from unittest.mock import MagicMock, patch

from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.const import UnitOfTemperature

from custom_components.salus_it600_cloud import sensor
from custom_components.salus_it600_cloud.api import parse_gateway_items
from custom_components.salus_it600_cloud.coordinator import SalusDevice
from custom_components.salus_it600_cloud.heating import TEMPERATURE_METRICS
from custom_components.salus_it600_cloud.sensor import SalusHeatingTemperature

from . import fixtures
from .helpers import CALL_LATER, FakeApi, create_coordinator


def _metric(slug: str):
    return next(metric for metric in TEMPERATURE_METRICS if metric.slug == slug)


def _entity(coordinator: object, slug: str, device_id: str = fixtures.THERMOSTAT_ID) -> SalusHeatingTemperature:
    entity = SalusHeatingTemperature(coordinator, device_id, coordinator.data[device_id], _metric(slug))
    entity.hass = MagicMock()
    return entity


def _uid(device_id: str, slug: str) -> str:
    return f"salus_it600_cloud_{fixtures.GATEWAY_ID}_{device_id}_{slug}"


async def test_setup_creates_measurement_sensors_for_thermostats_only() -> None:
    coordinator, _, _ = await create_coordinator()
    entry = MagicMock()
    entry.runtime_data = coordinator
    add_entities = MagicMock()

    await sensor.async_setup_entry(MagicMock(), entry, add_entities)

    entities = add_entities.call_args.args[0]
    assert [entity.unique_id for entity in entities] == [
        _uid(fixtures.THERMOSTAT_ID, "local_temperature"),
        _uid(fixtures.THERMOSTAT_ID, "heating_setpoint"),
        _uid(fixtures.THERMOSTAT_2_ID, "local_temperature"),
        _uid(fixtures.THERMOSTAT_2_ID, "heating_setpoint"),
    ]
    assert all(entity.state_class is SensorStateClass.MEASUREMENT for entity in entities)
    assert all(entity.device_class is SensorDeviceClass.TEMPERATURE for entity in entities)
    assert all(entity.native_unit_of_measurement == UnitOfTemperature.CELSIUS for entity in entities)
    assert all(entity.suggested_display_precision == 1 for entity in entities)


async def test_native_values_match_reported_temperatures() -> None:
    coordinator, _, _ = await create_coordinator()

    assert _entity(coordinator, "local_temperature").native_value == 22.0
    assert _entity(coordinator, "heating_setpoint").native_value == 21.0
    assert _entity(coordinator, "local_temperature").name == "Apartmán A Termostat pokoj Temperature"
    assert _entity(coordinator, "heating_setpoint").name == "Apartmán A Termostat pokoj Heating setpoint"


async def test_missing_temperature_is_unknown_and_the_entity_still_exists() -> None:
    api = FakeApi()
    shadow = fixtures.thermostat_shadow(hold_type=0)
    del shadow["state"]["reported"]["11"]["properties"]["ep9:sIT600TH:LocalTemperature_x100"]
    api.shadows[fixtures.THERMOSTAT_CODE] = shadow
    coordinator, _, _ = await create_coordinator(api)
    entity = _entity(coordinator, "local_temperature")

    assert entity.native_value is None
    assert entity.available is True


async def test_set_temperature_shows_on_the_setpoint_sensor() -> None:
    coordinator, _, _ = await create_coordinator()

    with patch(CALL_LATER):
        await coordinator.async_set_temperature(fixtures.THERMOSTAT_ID, 21.5)

    assert _entity(coordinator, "heating_setpoint").native_value == 21.5
    assert _entity(coordinator, "local_temperature").native_value == 22.0


async def test_climate_device_skips_the_legacy_temperature_sensor() -> None:
    coordinator, _, _ = await create_coordinator()
    thermostat = coordinator.data[fixtures.THERMOSTAT_ID]
    coordinator.data[fixtures.THERMOSTAT_ID] = replace(
        thermostat, listing=replace(thermostat.listing, fields={**thermostat.listing.fields, "temperature": 2200})
    )
    (hall,), _ = parse_gateway_items(
        fixtures.GATEWAY_ID,
        [{"id": "hall", "device_code": "hall-code", "type": "temperature_sensor", "name": "Hall", "temperature": 19.5}],
    )
    coordinator.data["hall"] = SalusDevice(listing=hall, properties={}, shadow_index=None, fresh=True)
    entry = MagicMock()
    entry.runtime_data = coordinator
    add_entities = MagicMock()

    await sensor.async_setup_entry(MagicMock(), entry, add_entities)

    entities = {entity.unique_id: entity for entity in add_entities.call_args.args[0]}
    assert _uid(fixtures.THERMOSTAT_ID, "temperature") not in entities
    assert _uid(fixtures.THERMOSTAT_ID, "local_temperature") in entities
    assert entities[_uid("hall", "temperature")].native_value == 19.5


def test_listing_readings_in_hundredths_read_as_degrees_and_percent() -> None:
    temperature = ("temperature", "current_temperature", "LocalTemperature")
    assert sensor._value({"temperature": 2150}, temperature, True) == 21.5
    assert sensor._value({"status": {"LocalTemperature": 2150}}, temperature, True) == 21.5
    assert sensor._value({"temperature": 21.5}, temperature, True) == 21.5
    assert sensor._value({"humidity": 4500}, ("humidity",), True) == 45.0
    assert sensor._value({"battery": 100}, ("battery",), False) == 100.0
