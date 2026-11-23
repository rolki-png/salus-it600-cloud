import pytest

from custom_components.salus_it600_cloud.state import DeviceStateStore

from custom_components.salus_it600_cloud.heating import (
    CentiCelsius,
    Demand,
    HeatingAction,
    ThermostatCommand,
    ThermostatObservation,
    centi_celsius_from_degrees,
    decode_running_cell,
    decode_x100_cell,
    heating_action,
    hold_raw_for_preset,
    observation_from_cells,
    observe,
)

RUNNING = "ep9:sIT600TH:RunningState"
LOCAL = "ep9:sIT600TH:LocalTemperature_x100"
SETPOINT = "ep9:sIT600TH:HeatingSetpoint_x100"
HOLD = "ep9:sIT600TH:HoldType"
SYSTEM = "ep9:sIT600TH:SystemMode"
BATTERY = "ep9:sBasicS:BatteryVoltage"
COOLING = "ep9:sIT600TH:CoolingSetpoint_x100"
BATTERY_LEVEL = "ep9:sIT600TH:BatteryLevel"
_DELETE = object()


def _properties(**overrides: object) -> dict[str, object]:
    properties: dict[str, object] = {
        HOLD: 2,
        SYSTEM: 4,
        RUNNING: 0,
        LOCAL: 2200,
        SETPOINT: 2100,
        BATTERY: 30,
    }
    for key, value in overrides.items():
        if value is _DELETE:
            del properties[key]
        else:
            properties[key] = value
    return properties


def test_empty_property_map_is_not_an_observation() -> None:
    assert observe({}) is None


def test_non_empty_map_with_no_thermostat_keys_is_unreported() -> None:
    observation = observe({"ep9:sOnOffS:OnOff": 1})

    assert observation is not None
    assert observation.demand is Demand.UNREPORTED
    assert observation.demand.is_on() is None
    assert observation.local_temperature is None
    assert observation.heating_setpoint is None
    assert observation.preset_slug() is None
    assert heating_action(observation) is HeatingAction.UNKNOWN


@pytest.mark.parametrize(
    ("present", "value", "demand"),
    [
        (False, None, Demand.UNREPORTED),
        (True, None, Demand.UNREADABLE),
        (True, True, Demand.UNREADABLE),
        (True, False, Demand.UNREADABLE),
        (True, 1.0, Demand.UNREADABLE),
        (True, "1", Demand.UNREADABLE),
        (True, 0, Demand.IDLE),
        (True, 2, Demand.IDLE),
        (True, 1, Demand.HEATING),
    ],
)
def test_running_cell_keeps_missing_idle_and_garbage_apart(present: bool, value: object, demand: Demand) -> None:
    assert decode_running_cell(value, present=present) is demand


def test_observe_uses_the_running_cell_decoder() -> None:
    assert observe(_properties(**{RUNNING: True})).demand is Demand.UNREADABLE
    assert observe(_properties(**{RUNNING: _DELETE})).demand is Demand.UNREPORTED
    assert observe(_properties(**{RUNNING: 0})).demand is Demand.IDLE
    assert observe(_properties(**{RUNNING: 1})).demand is Demand.HEATING


@pytest.mark.parametrize(
    ("demand", "is_on"),
    [
        (Demand.UNREPORTED, None),
        (Demand.UNREADABLE, None),
        (Demand.IDLE, False),
        (Demand.HEATING, True),
    ],
)
def test_demand_is_on_is_unknown_unless_the_cell_was_an_int(demand: Demand, is_on: bool | None) -> None:
    assert demand.is_on() is is_on


def test_unreported_and_unreadable_demand_stay_unknown_in_standby() -> None:
    missing = observe(_properties(**{HOLD: 7, RUNNING: _DELETE}))
    garbage = observe(_properties(**{HOLD: 7, SYSTEM: 0, RUNNING: True}))
    null_cell = observe(_properties(**{RUNNING: None}))

    assert heating_action(missing) is HeatingAction.UNKNOWN
    assert heating_action(garbage) is HeatingAction.UNKNOWN
    assert heating_action(null_cell) is HeatingAction.UNKNOWN
    assert missing.demand.is_on() is None
    assert garbage.demand.attribute_label() is None


def test_known_demand_in_standby_or_system_off_is_off_even_while_calling() -> None:
    standby_idle = observe(_properties(**{HOLD: 7, RUNNING: 0}))
    standby_calling = observe(_properties(**{HOLD: 7, RUNNING: 1}))
    system_off_calling = observe(_properties(**{HOLD: 2, SYSTEM: 0, RUNNING: 1}))

    assert heating_action(standby_idle) is HeatingAction.OFF
    assert heating_action(standby_calling) is HeatingAction.OFF
    assert heating_action(system_off_calling) is HeatingAction.OFF
    assert standby_calling.demand.is_on() is True
    assert standby_idle.demand.attribute_label() == "Idle"
    assert standby_calling.demand.attribute_label() == "Heating"


def test_heat_mode_action_follows_integer_demand() -> None:
    assert heating_action(observe(_properties(**{RUNNING: 1}))) is HeatingAction.HEATING
    assert heating_action(observe(_properties(**{RUNNING: 0}))) is HeatingAction.IDLE
    assert heating_action(observe(_properties(**{RUNNING: 2}))) is HeatingAction.IDLE
    assert heating_action(None) is HeatingAction.UNKNOWN


def test_zero_temperature_is_a_real_sample_and_garbage_is_not() -> None:
    assert decode_x100_cell(0, present=True).celsius() == 0.0
    assert decode_x100_cell(2250, present=True).celsius() == 22.5
    assert decode_x100_cell(0, present=False) is None
    assert decode_x100_cell(None, present=True) is None
    assert decode_x100_cell(True, present=True) is None
    assert decode_x100_cell(22.5, present=True) is None

    zero = observe(_properties(**{LOCAL: 0, SETPOINT: _DELETE}))
    assert zero.local_temperature.celsius() == 0.0
    assert zero.heating_setpoint is None
    assert observe(_properties(**{LOCAL: True})).local_temperature is None


def test_scale_pair_round_trips_setpoints() -> None:
    for degrees in (0.0, 5.0, 21.5, 22.5, 35.0):
        assert CentiCelsius(centi_celsius_from_degrees(degrees)).celsius() == degrees


def test_commands_show_up_in_the_observation() -> None:
    store = DeviceStateStore(pending_timeout=60, max_age=600)
    store.update_reported("t", "11", {}, now=0)
    store.set_pending(
        "t",
        {
            ThermostatCommand.SETPOINT: centi_celsius_from_degrees(21.5),
            ThermostatCommand.HOLD: hold_raw_for_preset("away"),
            ThermostatCommand.SYSTEM_MODE: 0,
        },
        now=0,
    )
    observation = observe(store.properties("t", now=1))

    assert observation.heating_setpoint.celsius() == 21.5
    assert observation.preset_slug() == "away"
    assert observation.hold.is_standby is True
    assert observation.hold_label() == "Frost Protection"
    assert observation.system_mode.is_off is True
    assert observation.system_mode_label() == "Off"


@pytest.mark.parametrize(
    ("raw", "slug", "label", "standby"),
    [
        (0, "schedule", "Schedule", False),
        (2, "manual", "Manual Hold", False),
        (7, "away", "Frost Protection", True),
        (3, "manual", "Unknown (3)", False),
    ],
)
def test_hold_presets(raw: int, slug: str, label: str, standby: bool) -> None:
    observation = observe(_properties(**{HOLD: raw}))

    assert observation.preset_slug() == slug
    assert observation.hold_label() == label
    assert observation.hold.is_standby is standby


def test_preset_slugs_are_the_hold_command_values() -> None:
    assert [hold_raw_for_preset(slug) for slug in ("schedule", "manual", "away")] == [0, 2, 7]


def test_absent_hold_has_no_preset_and_present_non_int_is_manual() -> None:
    absent = observe(_properties(**{HOLD: _DELETE}))
    garbage = observe(_properties(**{HOLD: True}))

    assert absent.preset_slug() is None
    assert absent.hold_label() is None
    assert absent.hold.is_standby is False
    assert garbage.preset_slug() == "manual"
    assert garbage.hold_label() == "Unknown"
    assert garbage.hold.is_standby is False


def test_system_mode_labels() -> None:
    assert observe(_properties(**{SYSTEM: 4})).system_mode_label() == "Heat"
    assert observe(_properties(**{SYSTEM: 1})).system_mode_label() == "Auto"
    assert observe(_properties(**{SYSTEM: 9})).system_mode_label() == "Unknown (9)"
    assert observe(_properties(**{SYSTEM: _DELETE})).system_mode_label() is None
    assert observe(_properties(**{SYSTEM: False})).system_mode.is_off is False


def test_battery_tenths_become_volts() -> None:
    assert observe(_properties()).battery.volts() == 3.0
    assert observe(_properties(**{BATTERY: _DELETE})).battery is None
    assert observe(_properties(**{BATTERY: True})).battery is None


def test_history_cells_do_not_need_wire_keys() -> None:
    observation = observation_from_cells(
        running=1,
        running_present=True,
        local_x100=0,
        local_present=True,
        setpoint_x100=None,
        setpoint_present=False,
        hold=2,
        hold_present=True,
        system_mode=4,
        system_mode_present=True,
        battery_tenths=30,
        battery_present=True,
    )

    assert observation.demand is Demand.HEATING
    assert observation.local_temperature.celsius() == 0.0
    assert observation.heating_setpoint is None
    assert observation.preset_slug() == "manual"
    assert observation.battery.volts() == 3.0


def test_bool_cannot_pose_as_hundredths() -> None:
    with pytest.raises(TypeError):
        CentiCelsius(True)


def test_observation_rejects_a_raw_float_temperature() -> None:
    with pytest.raises(TypeError):
        ThermostatObservation(
            demand=Demand.IDLE,
            local_temperature=22.5,
            heating_setpoint=None,
            hold=observe(_properties()).hold,
            system_mode=observe(_properties()).system_mode,
            battery=None,
        )


def test_unknown_preset_slug_is_rejected() -> None:
    with pytest.raises(KeyError):
        hold_raw_for_preset("boost")


def test_apk_cells_are_unknown_until_reported() -> None:
    observation = observe(_properties())

    assert observation.cooling_setpoint is None
    assert observation.ambient_temperature is None
    assert observation.humidity is None
    assert observation.battery_level is None


def test_cooling_setpoint_reads_like_the_heating_setpoint() -> None:
    assert observe(_properties(**{COOLING: 2450})).cooling_setpoint.celsius() == 24.5
    assert observe(_properties(**{COOLING: 0})).cooling_setpoint.celsius() == 0.0
    assert observe(_properties(**{COOLING: True})).cooling_setpoint is None
    assert observe(_properties(**{COOLING: None})).cooling_setpoint is None


def test_humidity_and_ambient_temperature_come_from_any_endpoint() -> None:
    observation = observe(
        _properties(**{"ep1:sHumiS:MeasuredValue_x100": 4550, "ep9:sTempS:MeasuredValue_x100": 2125})
    )

    assert observation.humidity.percent() == 45.5
    assert observation.ambient_temperature.celsius() == 21.25
    assert observe(_properties(**{"ep1:sHumiS:MeasuredValue_x100": 45.5})).humidity is None


@pytest.mark.parametrize(("raw", "level"), [(0, 0), (87, 87), (100, 100), (101, None), (255, None), (-1, None), (True, None)])
def test_battery_level_is_a_percentage_beside_the_voltage(raw: object, level: int | None) -> None:
    observation = observe(_properties(**{BATTERY_LEVEL: raw}))

    assert observation.battery_level == level
    assert observation.battery.volts() == 3.0


def test_cooling_command_shows_up_in_the_observation() -> None:
    store = DeviceStateStore(pending_timeout=60, max_age=600)
    store.update_reported("t", "11", _properties(**{COOLING: 2400}), now=0)
    store.set_pending("t", {ThermostatCommand.COOLING_SETPOINT: centi_celsius_from_degrees(25.5)}, now=0)

    assert ThermostatCommand.COOLING_SETPOINT == "ep9:sIT600TH:SetCoolingSetpoint_x100"
    assert observe(store.properties("t", now=1)).cooling_setpoint.celsius() == 25.5


def test_observation_rejects_raw_humidity_and_out_of_range_battery() -> None:
    reference = observe(_properties())
    common = dict(
        demand=Demand.IDLE,
        local_temperature=None,
        heating_setpoint=None,
        hold=reference.hold,
        system_mode=reference.system_mode,
        battery=None,
    )
    with pytest.raises(TypeError):
        ThermostatObservation(**common, humidity=45.5)
    with pytest.raises(TypeError):
        ThermostatObservation(**common, battery_level=101)
    with pytest.raises(TypeError):
        ThermostatObservation(**common, cooling_setpoint=24.5)
