import pytest

from custom_components.salus_it600_cloud.api import DeviceListing
from custom_components.salus_it600_cloud.devices import is_climate_device, is_switch_device


def _listing(model: str, device_type: str = "") -> DeviceListing:
    return DeviceListing(
        id="d", device_code="c", gateway_id="g", type=device_type, model=model, name="Device", fields={}
    )


@pytest.mark.parametrize("model", ["C900T", "TRV3RF", "SQ610RFNH", "SQ610RF"])
def test_apk_thermostat_models_are_climate_devices(model: str) -> None:
    assert is_climate_device(_listing(model))
    assert not is_switch_device(_listing(model))


@pytest.mark.parametrize(("model", "device_type"), [("SP600", ""), ("", "Smart_Relay"), ("SR600", "")])
def test_smart_plug_and_smart_relay_type_are_switches(model: str, device_type: str) -> None:
    assert is_switch_device(_listing(model, device_type))
    assert not is_climate_device(_listing(model, device_type))


@pytest.mark.parametrize("model", ["MS600", "OS600", "WLS600"])
def test_sensor_models_are_neither_climate_nor_switch(model: str) -> None:
    assert not is_climate_device(_listing(model))
    assert not is_switch_device(_listing(model))
