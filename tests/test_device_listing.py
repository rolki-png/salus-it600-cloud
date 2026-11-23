"""Accounts with location groups and null device fields, replayed from upstream issues #1 and #10."""

from unittest.mock import MagicMock

from custom_components.salus_it600_cloud import binary_sensor, button, climate, sensor, switch
from custom_components.salus_it600_cloud.api import SalusMetadata

from . import fixtures
from .helpers import FakeApi, create_coordinator
from .test_api import _api, _Response


async def _grouped_metadata() -> SalusMetadata:
    api, _, _ = _api(
        {
            "/occupants/slider_list": lambda: _Response(200, fixtures.slider_list()),
            "/occupants/slider_details": lambda: _Response(200, fixtures.grouped_slider_details()),
        }
    )
    await api.authenticate()
    return await api.fetch_metadata()


async def test_devices_nested_in_groups_are_listed() -> None:
    metadata = await _grouped_metadata()

    assert [device["id"] for device in metadata.devices] == [
        fixtures.THERMOSTAT_ID,
        fixtures.THERMOSTAT_2_ID,
        fixtures.RELAY_ID,
        fixtures.GATEWAY_ID,
    ]
    assert [rule["id"] for rule in metadata.rules] == [fixtures.RULE_ID, fixtures.AUTO_RULE_ID]


async def test_every_platform_sets_up_a_grouped_account_with_null_types() -> None:
    metadata = await _grouped_metadata()
    api = FakeApi()
    api.fetch_metadata = lambda: _resolved(metadata)
    coordinator, _, _ = await create_coordinator(api)
    entry = MagicMock()
    entry.runtime_data = coordinator

    created: dict[str, list[str]] = {}
    for platform in (climate, switch, sensor, binary_sensor, button):
        add_entities = MagicMock()
        await platform.async_setup_entry(MagicMock(), entry, add_entities)
        created[platform.__name__.rsplit(".", 1)[1]] = [entity.unique_id for entity in add_entities.call_args.args[0]]

    prefix = f"salus_it600_cloud_{fixtures.GATEWAY_ID}_"
    assert created["climate"] == [prefix + fixtures.THERMOSTAT_ID, prefix + fixtures.THERMOSTAT_2_ID]
    assert created["switch"] == [prefix + fixtures.RELAY_ID]


async def _resolved(metadata: SalusMetadata) -> SalusMetadata:
    return metadata
