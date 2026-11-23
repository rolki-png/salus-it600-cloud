"""Accounts with location groups and null device fields, replayed from upstream issues #1 and #10.

test_e2e covers the same account through every platform in a real Home Assistant.
"""

from custom_components.salus_it600_cloud.api import SalusMetadata

from . import fixtures
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

    assert [device.id for device in metadata.devices] == [
        fixtures.THERMOSTAT_ID,
        fixtures.THERMOSTAT_2_ID,
        fixtures.RELAY_ID,
        fixtures.GATEWAY_ID,
    ]
    assert [rule.id for rule in metadata.rules] == [fixtures.RULE_ID]
