"""Sending a picture the panel already shows.

Every transfer keeps the label connected, which makes it invisible to
Bluetooth for the duration, and ends in a full colour refresh. Doing that
to arrive at the picture already on the panel is pure cost, so it is
skipped - which is what lets an automation run often without thinking
about it.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.util import dt as dt_util

from custom_components.esl_zhsunyco.const import DOMAIN, SERVICE_DRAWCUSTOM

PAYLOAD = [{"type": "text", "value": "Mittwoch", "x": 10, "y": 10, "size": 30}]
OTHER = [{"type": "text", "value": "Donnerstag", "x": 10, "y": 10, "size": 30}]


async def _setup(hass: HomeAssistant, entry):
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    device = entry.runtime_data
    device.state.last_advert = dt_util.utcnow()
    return device


def _device_id(hass: HomeAssistant, entry) -> str:
    return dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)[0].id


async def _send(hass, entry, uploaded: list[bytes], **extra):
    async def fake_send_image(client, payload, *, compressed=False):
        uploaded.append(bytes(payload))

    with (
        patch(
            "custom_components.esl_zhsunyco.device.protocol.send_prepared_image",
            new=fake_send_image,
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
        response = await hass.services.async_call(
            DOMAIN,
            SERVICE_DRAWCUSTOM,
            {"device_id": _device_id(hass, entry), "payload": PAYLOAD, **extra},
            blocking=True,
            return_response=True,
        )
    return response["results"][0]


async def test_the_same_picture_twice_only_goes_over_the_air_once(
    hass: HomeAssistant, config_entry, mock_bluetooth
) -> None:
    await _setup(hass, config_entry)
    uploaded: list[bytes] = []

    first = await _send(hass, config_entry, uploaded)
    second = await _send(hass, config_entry, uploaded)

    assert first["sent"] is True
    assert second["sent"] is False
    assert second["ok"] is True, "nothing failed; there was nothing to do"
    assert len(uploaded) == 1


async def test_a_different_picture_is_sent(
    hass: HomeAssistant, config_entry, mock_bluetooth
) -> None:
    await _setup(hass, config_entry)
    uploaded: list[bytes] = []

    await _send(hass, config_entry, uploaded)
    with (
        patch(
            "custom_components.esl_zhsunyco.device.protocol.send_prepared_image",
            new=AsyncMock(),
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
        response = await hass.services.async_call(
            DOMAIN,
            SERVICE_DRAWCUSTOM,
            {"device_id": _device_id(hass, config_entry), "payload": OTHER},
            blocking=True,
            return_response=True,
        )
    assert response["results"][0]["sent"] is True


async def test_force_sends_it_anyway(
    hass: HomeAssistant, config_entry, mock_bluetooth
) -> None:
    await _setup(hass, config_entry)
    uploaded: list[bytes] = []

    await _send(hass, config_entry, uploaded)
    again = await _send(hass, config_entry, uploaded, force=True)

    assert again["sent"] is True
    assert len(uploaded) == 2


async def test_clearing_the_screen_makes_the_next_send_go_out(
    hass: HomeAssistant, config_entry, mock_bluetooth
) -> None:
    """A cleared panel shows nothing, so the same picture is now a change."""
    device = await _setup(hass, config_entry)
    uploaded: list[bytes] = []

    await _send(hass, config_entry, uploaded)
    with (
        patch(
            "custom_components.esl_zhsunyco.device.protocol.clear_screen",
            new=AsyncMock(),
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
        await device.async_clear_screen()

    after = await _send(hass, config_entry, uploaded)
    assert after["sent"] is True
    assert len(uploaded) == 2


async def test_the_panel_is_remembered_across_a_restart(
    hass: HomeAssistant, config_entry, mock_bluetooth
) -> None:
    """The picture survives a restart, so the digest has to as well."""
    await _setup(hass, config_entry)
    uploaded: list[bytes] = []
    await _send(hass, config_entry, uploaded)

    assert await hass.config_entries.async_unload(config_entry.entry_id)
    await hass.async_block_till_done()
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    config_entry.runtime_data.state.last_advert = dt_util.utcnow()

    again = await _send(hass, config_entry, uploaded)
    assert again["sent"] is False
    assert len(uploaded) == 1
