"""Advertisement handling and RGB light behaviour."""

from __future__ import annotations

import struct
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from custom_components.esl_zhsunyco.const import MANUFACTURER_ID, MANUFACTURER_ID_ALT

BATTERY_SENSOR = "sensor.esl_66_66_54_20_00_55_battery_voltage"
RGB_LIGHT = "light.esl_66_66_54_20_00_55_rgb_led"


def _advert(company_id: int, battery_mv: int = 2950, rssi: int = -60):
    """Build a fake service info carrying our manufacturer payload."""
    payload = struct.pack("<HHHH", 0xAB01, 0x0102, 0x0203, 0x0304) + struct.pack(
        ">H", battery_mv
    )
    return SimpleNamespace(rssi=rssi, manufacturer_data={company_id: payload})


async def _setup(hass: HomeAssistant, entry):
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry.runtime_data


async def test_advertisement_populates_battery(
    hass: HomeAssistant, config_entry, mock_bluetooth
) -> None:
    """Battery must come from the advertisement without any connection."""
    device = await _setup(hass, config_entry)

    device._advert_received(_advert(MANUFACTURER_ID), None)
    await hass.async_block_till_done()

    state = hass.states.get(BATTERY_SENSOR)
    assert state is not None
    assert float(state.state) == 2.95


async def test_advertisement_accepts_swapped_company_id(
    hass: HomeAssistant, config_entry, mock_bluetooth
) -> None:
    """The document's byte order for 0xbbaa is ambiguous, accept both."""
    device = await _setup(hass, config_entry)

    device._advert_received(_advert(MANUFACTURER_ID_ALT, battery_mv=3100), None)
    await hass.async_block_till_done()

    assert float(hass.states.get(BATTERY_SENSOR).state) == 3.1


async def test_foreign_advertisement_is_ignored(
    hass: HomeAssistant, config_entry, mock_bluetooth
) -> None:
    """Another vendor's payload must not be parsed as battery data."""
    device = await _setup(hass, config_entry)

    device._advert_received(
        SimpleNamespace(rssi=-70, manufacturer_data={0x004C: b"\x01\x02\x03"}), None
    )
    await hass.async_block_till_done()

    assert device.state.battery_mv is None


async def test_advertisement_does_not_reschedule_poll(
    hass: HomeAssistant, config_entry, mock_bluetooth
) -> None:
    """Frequent advertisements must not keep postponing the status poll."""
    device = await _setup(hass, config_entry)
    device.coordinator.update_interval = timedelta(minutes=60)

    before = device.coordinator._unsub_refresh
    device._advert_received(_advert(MANUFACTURER_ID), None)
    await hass.async_block_till_done()

    assert device.coordinator._unsub_refresh is before


async def test_light_remembers_colour_across_off(
    hass: HomeAssistant, config_entry, mock_bluetooth
) -> None:
    """Turning off must not overwrite the colour with black."""
    device = await _setup(hass, config_entry)
    device.state.last_advert = dt_util.utcnow()
    device.coordinator.async_update_listeners()

    sent: list[tuple] = []

    async def fake_set_rgb(client, r, g, b, on_ms, off_ms, work_ms, response=None):
        sent.append((r, g, b, work_ms))

    with (
        patch(
            "custom_components.esl_zhsunyco.device.protocol.set_rgb", new=fake_set_rgb
        ),
        patch(
            "custom_components.esl_zhsunyco.device._ESLConnection.__aenter__",
            new=AsyncMock(return_value=object()),
        ),
        patch(
            "custom_components.esl_zhsunyco.device._ESLConnection.__aexit__",
            new=AsyncMock(return_value=False),
        ),
    ):
        await hass.services.async_call(
            "light",
            "turn_on",
            {"entity_id": RGB_LIGHT, "rgb_color": [0, 255, 0]},
            blocking=True,
        )
        await hass.services.async_call(
            "light", "turn_off", {"entity_id": RGB_LIGHT}, blocking=True
        )
        await hass.services.async_call(
            "light", "turn_on", {"entity_id": RGB_LIGHT}, blocking=True
        )

    assert sent[0] == (0, 255, 0, 30000)
    assert sent[1] == (0, 0, 0, 0)
    # The third call must restore green, not send black again.
    assert sent[2] == (0, 255, 0, 30000)
    assert hass.states.get(RGB_LIGHT).state == "on"
