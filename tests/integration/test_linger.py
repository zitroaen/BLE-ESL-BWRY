"""Holding the connection open between commands."""

from __future__ import annotations

import contextlib
from unittest.mock import AsyncMock, patch

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from custom_components.esl_zhsunyco.const import CONF_LINGER_S


class FakeClient:
    """Counts connects and disconnects."""

    def __init__(self) -> None:
        self.is_connected = True
        self.disconnects = 0

    async def disconnect(self):
        self.disconnects += 1
        self.is_connected = False


async def _setup(hass: HomeAssistant, entry):
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    device = entry.runtime_data
    device.state.last_advert = dt_util.utcnow()
    return device


def _patched_connect(client, waits: list):
    async def fake_wait(_self, wait=180):
        waits.append(wait)
        return object()

    async def fake_establish(*args, **kwargs):
        return client

    async def fake_unlock(_client):
        return None

    return (
        patch(
            "custom_components.esl_zhsunyco.device.ESLDevice."
            "_async_wait_for_connectable",
            new=fake_wait,
        ),
        patch(
            "custom_components.esl_zhsunyco.device.establish_connection",
            new=fake_establish,
        ),
        patch("custom_components.esl_zhsunyco.device.protocol.unlock", new=fake_unlock),
    )


async def test_second_command_reuses_the_open_connection(
    hass: HomeAssistant, config_entry, mock_bluetooth
) -> None:
    """The wait for an advertisement must happen once, not per command.

    Reconnecting means waiting minutes for the label to wake, so a burst of
    commands has to share one connection.
    """
    device = await _setup(hass, config_entry)
    client = FakeClient()
    waits: list[int] = []

    a, b, c = _patched_connect(client, waits)
    with (
        a,
        b,
        c,
        patch(
            "custom_components.esl_zhsunyco.device.protocol.clear_screen",
            new=AsyncMock(),
        ),
    ):
        await device.async_clear_screen()
        await device.async_clear_screen()
        await device.async_clear_screen()

    assert len(waits) == 1, f"connected {len(waits)} times instead of once"
    assert client.disconnects == 0
    assert device.connected is True


async def test_linger_zero_disconnects_immediately(
    hass: HomeAssistant, config_entry, mock_bluetooth
) -> None:
    """Opting out must restore the previous behaviour."""
    config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        config_entry, options={**config_entry.options, CONF_LINGER_S: 0}
    )
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    device = config_entry.runtime_data
    device.state.last_advert = dt_util.utcnow()

    client = FakeClient()
    waits: list[int] = []

    a, b, c = _patched_connect(client, waits)
    with (
        a,
        b,
        c,
        patch(
            "custom_components.esl_zhsunyco.device.protocol.clear_screen",
            new=AsyncMock(),
        ),
    ):
        await device.async_clear_screen()
        await hass.async_block_till_done()

    assert client.disconnects == 1
    assert device.connected is False


async def test_failed_command_drops_the_connection(
    hass: HomeAssistant, config_entry, mock_bluetooth
) -> None:
    """A broken link must not be handed to the next command."""
    device = await _setup(hass, config_entry)
    client = FakeClient()
    waits: list[int] = []

    a, b, c = _patched_connect(client, waits)
    with (
        a,
        b,
        c,
        patch(
            "custom_components.esl_zhsunyco.device.protocol.clear_screen",
            side_effect=OSError("link lost"),
        ),
        contextlib.suppress(OSError),
    ):
        await device.async_clear_screen()

    assert client.disconnects == 1
    assert device.connected is False


async def test_disconnect_callback_clears_the_cached_client(
    hass: HomeAssistant, config_entry, mock_bluetooth
) -> None:
    """A drop initiated by the label must not leave a stale connection."""
    device = await _setup(hass, config_entry)
    client = FakeClient()
    waits: list[int] = []

    a, b, c = _patched_connect(client, waits)
    with (
        a,
        b,
        c,
        patch(
            "custom_components.esl_zhsunyco.device.protocol.clear_screen",
            new=AsyncMock(),
        ),
    ):
        await device.async_clear_screen()
        assert device.connected is True

        device._on_disconnected(client)
        assert device.connected is False

        await device.async_clear_screen()

    assert len(waits) == 2, "a dropped link must be re-established"


async def test_unload_closes_the_held_connection(
    hass: HomeAssistant, config_entry, mock_bluetooth
) -> None:
    """Unloading must not leak a proxy connection slot."""
    device = await _setup(hass, config_entry)
    client = FakeClient()
    waits: list[int] = []

    a, b, c = _patched_connect(client, waits)
    with (
        a,
        b,
        c,
        patch(
            "custom_components.esl_zhsunyco.device.protocol.clear_screen",
            new=AsyncMock(),
        ),
    ):
        await device.async_clear_screen()

    assert device.connected is True
    assert await hass.config_entries.async_unload(config_entry.entry_id)
    await hass.async_block_till_done()

    assert client.disconnects == 1
