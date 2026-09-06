"""The advertisement callback must not be the only source of state."""

from __future__ import annotations

import struct
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.esl_zhsunyco.const import MANUFACTURER_ID

BATTERY_SENSOR = "sensor.esl_66_66_54_20_00_55_battery_voltage"

# The advertisement captured from real hardware.
REAL_ADVERT = bytes.fromhex("3000000e03300201") + struct.pack(">H", 2969)


def _service_info():
    return SimpleNamespace(
        name="ESL",
        rssi=-38,
        source="proxy",
        manufacturer_data={MANUFACTURER_ID: REAL_ADVERT},
    )


async def test_callback_matcher_does_not_require_a_connectable_scanner(
    hass: HomeAssistant, config_entry
) -> None:
    """A matcher without connectable defaults to requiring one.

    Advertisements seen only by a passive scanner would then never reach us,
    and the passive data needs no connection at all.
    """
    captured: list[dict] = []

    def fake_register(hass_, callback_, matcher, mode):
        captured.append(dict(matcher))
        return lambda: None

    with (
        patch(
            "custom_components.esl_zhsunyco.device.bluetooth.async_register_callback",
            side_effect=fake_register,
        ),
        patch(
            "custom_components.esl_zhsunyco.device.bluetooth.async_last_service_info",
            return_value=None,
        ),
        patch(
            "custom_components.esl_zhsunyco.device.bluetooth."
            "async_ble_device_from_address",
            return_value=None,
        ),
        patch(
            "custom_components.esl_zhsunyco.device.bluetooth."
            "async_process_advertisements",
            side_effect=TimeoutError,
        ),
    ):
        config_entry.add_to_hass(hass)
        assert await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()

    assert captured, "no callback was registered"
    assert captured[0]["connectable"] is False


async def test_state_is_seeded_from_the_stack_at_setup(
    hass: HomeAssistant, config_entry
) -> None:
    """Whatever Home Assistant already saw must populate the entities."""
    with (
        patch(
            "custom_components.esl_zhsunyco.device.bluetooth.async_register_callback",
            return_value=lambda: None,
        ),
        patch(
            "custom_components.esl_zhsunyco.device.bluetooth.async_last_service_info",
            return_value=_service_info(),
        ),
        patch(
            "custom_components.esl_zhsunyco.device.bluetooth."
            "async_ble_device_from_address",
            return_value=None,
        ),
        patch(
            "custom_components.esl_zhsunyco.device.bluetooth."
            "async_process_advertisements",
            side_effect=TimeoutError,
        ),
    ):
        config_entry.add_to_hass(hass)
        assert await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()

        assert hass.states.get(BATTERY_SENSOR).state == "2.969"


async def test_periodic_seed_recovers_a_silent_callback(
    hass: HomeAssistant, config_entry, freezer: FrozenDateTimeFactory
) -> None:
    """If the callback never fires, the timer must still fill the entities."""
    service_info = None

    def last_service_info(*args, **kwargs):
        return service_info

    with (
        patch(
            "custom_components.esl_zhsunyco.device.bluetooth.async_register_callback",
            return_value=lambda: None,
        ),
        patch(
            "custom_components.esl_zhsunyco.device.bluetooth.async_last_service_info",
            side_effect=last_service_info,
        ),
        patch(
            "custom_components.esl_zhsunyco.device.bluetooth."
            "async_ble_device_from_address",
            return_value=None,
        ),
        patch(
            "custom_components.esl_zhsunyco.device.bluetooth."
            "async_process_advertisements",
            side_effect=TimeoutError,
        ),
    ):
        config_entry.add_to_hass(hass)
        assert await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()

        # Nothing known yet: the callback never fired and the stack was empty.
        assert hass.states.get(BATTERY_SENSOR).state == "unavailable"

        # The stack learns about the label, but our callback stays silent.
        service_info = _service_info()
        freezer.tick(timedelta(minutes=6))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()

        assert hass.states.get(BATTERY_SENSOR).state == "2.969"
