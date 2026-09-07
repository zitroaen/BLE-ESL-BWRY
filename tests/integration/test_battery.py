"""Battery level in percent, derived from the reported voltage."""

from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from custom_components.esl_zhsunyco.const import (
    CONF_BATTERY_EMPTY_MV,
    CONF_BATTERY_FULL_MV,
)

ENTITY = "sensor.esl_66_66_54_20_00_55_battery"


async def _setup(hass: HomeAssistant, entry):
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    device = entry.runtime_data
    device.state.last_advert = dt_util.utcnow()
    device.coordinator.async_update_listeners()
    return device


@pytest.mark.parametrize(
    ("millivolts", "expected"),
    [
        (3000, 100),  # the configured full voltage
        (2200, 0),  # the configured empty voltage
        (2600, 50),  # halfway
        (3300, 100),  # above full, clamped rather than reported as 137 %
        (1800, 0),  # below empty, clamped rather than negative
        (2947, 93),  # a real reading from the measured unit
    ],
    ids=["full", "empty", "half", "above", "below", "measured"],
)
async def test_percentage_from_voltage(
    hass: HomeAssistant, config_entry, mock_bluetooth, millivolts, expected
) -> None:
    """Linear between the configured ends, clamped outside them."""
    device = await _setup(hass, config_entry)
    device.state.battery_mv = millivolts
    assert device.battery_percent == expected


async def test_no_reading_yields_no_percentage(
    hass: HomeAssistant, config_entry, mock_bluetooth
) -> None:
    """Before the first advertisement there is nothing to derive from."""
    device = await _setup(hass, config_entry)
    device.state.battery_mv = None
    assert device.battery_percent is None


async def test_the_range_is_configurable(
    hass: HomeAssistant, config_entry, mock_bluetooth
) -> None:
    """A different cell needs different ends, so both are options."""
    device = await _setup(hass, config_entry)
    device.state.battery_mv = 2400

    hass.config_entries.async_update_entry(
        config_entry,
        options={
            **config_entry.options,
            CONF_BATTERY_FULL_MV: 2600,
            CONF_BATTERY_EMPTY_MV: 2200,
        },
    )
    await hass.async_block_till_done()

    assert device.battery_percent == 50


async def test_an_inverted_range_reports_nothing(
    hass: HomeAssistant, config_entry, mock_bluetooth
) -> None:
    """A stored range that cannot work must not produce a wrong number.

    The options flow refuses this, but an entry could still carry it, and
    an inverted range would otherwise read as a confidently wrong value.
    """
    device = await _setup(hass, config_entry)
    device.state.battery_mv = 2500

    hass.config_entries.async_update_entry(
        config_entry,
        options={
            **config_entry.options,
            CONF_BATTERY_FULL_MV: 2200,
            CONF_BATTERY_EMPTY_MV: 3000,
        },
    )
    await hass.async_block_till_done()

    assert device.battery_percent is None


async def test_the_sensor_reports_it_as_a_battery(
    hass: HomeAssistant, config_entry, mock_bluetooth
) -> None:
    """device_class battery is what puts it in the device's battery slot."""
    device = await _setup(hass, config_entry)
    device.state.battery_mv = 2800
    device.coordinator.async_update_listeners()
    await hass.async_block_till_done()

    state = hass.states.get(ENTITY)
    assert state is not None
    assert state.state == "75"
    assert state.attributes["device_class"] == "battery"
    assert state.attributes["unit_of_measurement"] == "%"


async def test_the_voltage_sensor_still_exists(
    hass: HomeAssistant, config_entry, mock_bluetooth
) -> None:
    """The percentage is an estimate; the volts are the measurement."""
    device = await _setup(hass, config_entry)
    device.state.battery_mv = 2947
    device.coordinator.async_update_listeners()
    await hass.async_block_till_done()

    volts = hass.states.get("sensor.esl_66_66_54_20_00_55_battery_voltage")
    assert volts is not None
    assert float(volts.state) == pytest.approx(2.947)
