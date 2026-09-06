"""A stale GATT cache must not make a command impossible."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util

from .conftest import attach_services


class FakeClient:
    """A connection whose GATT table may or may not carry our characteristics."""

    def __init__(self, present: bool) -> None:
        self.is_connected = True
        self.disconnects = 0
        self.services = attach_services(self, present)

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


def _patches(clients: list[FakeClient]):
    made: list[FakeClient] = []

    async def fake_wait(_self, wait=180):
        return object()

    async def fake_establish(*args, **kwargs):
        client = clients[len(made)] if len(made) < len(clients) else clients[-1]
        made.append(client)
        return client

    async def fake_unlock(_client):
        return None

    return (
        made,
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


async def test_stale_cache_is_cleared_and_the_connection_retried(
    hass: HomeAssistant, config_entry, mock_bluetooth
) -> None:
    """The reported failure: a characteristic the probe has seen goes missing.

    "Characteristic 31323032-... was not found" came from a connection whose
    cached GATT table did not carry the command characteristic. Clearing the
    cache and rediscovering is the only way out.
    """
    device = await _setup(hass, config_entry)
    stale = FakeClient(present=False)
    good = FakeClient(present=True)

    made, a, b, c = _patches([stale, good])
    with a, b, c:
        async with device.connection() as client:
            assert client is good

    assert stale.services.cleared == 1, "the stale cache was not cleared"
    assert stale.disconnects == 1, "the unusable connection was not closed"
    assert len(made) == 2, "no second attempt was made"


async def test_persistently_missing_characteristics_are_reported(
    hass: HomeAssistant, config_entry, mock_bluetooth
) -> None:
    """If clearing does not help, say so instead of failing obscurely."""
    device = await _setup(hass, config_entry)
    broken = FakeClient(present=False)

    # clear_cache must not silently repair this fake.
    async def no_repair():
        broken.services.cleared += 1
        return True

    broken.clear_cache = no_repair

    made, a, b, c = _patches([broken])
    with a, b, c, pytest.raises(HomeAssistantError, match="command"):
        async with device.connection():
            pass

    assert broken.services.cleared == 2, "both attempts must clear the cache"
    assert device._lock.locked() is False
    assert device._client is None


async def test_held_connection_with_a_broken_table_is_replaced(
    hass: HomeAssistant, config_entry, mock_bluetooth
) -> None:
    """A reused connection must be checked, not trusted."""
    device = await _setup(hass, config_entry)
    good = FakeClient(present=True)

    made, a, b, c = _patches([good])
    with a, b, c:
        async with device.connection():
            pass

        assert device.connected is True

        # The held connection loses its table, as after a firmware hiccup.
        device._client.services.present = False

        async with device.connection() as client:
            assert client is not None
            assert client.services.get_characteristic("anything") is not None


async def test_command_failure_is_recorded(
    hass: HomeAssistant, config_entry, mock_bluetooth
) -> None:
    """A failed button press must leave a trace in diagnostics."""
    device = await _setup(hass, config_entry)
    good = FakeClient(present=True)

    made, a, b, c = _patches([good])
    with (
        a,
        b,
        c,
        patch(
            "custom_components.esl_zhsunyco.device.protocol.clear_screen",
            side_effect=OSError("Characteristic ... was not found!"),
        ),
        pytest.raises(OSError),
    ):
        await device.async_clear_screen()

    assert device.state.last_command is not None
    assert device.state.last_command["kind"] == "clear_screen"
    assert device.state.last_command["result"] == "failed"
    assert "was not found" in device.state.last_command["detail"]


async def test_command_success_is_recorded(
    hass: HomeAssistant, config_entry, mock_bluetooth
) -> None:
    """A working command must be visible too, not only failures."""
    from unittest.mock import AsyncMock

    device = await _setup(hass, config_entry)
    good = FakeClient(present=True)

    made, a, b, c = _patches([good])
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

    assert device.state.last_command["kind"] == "clear_screen"
    assert device.state.last_command["result"] == "ok"
