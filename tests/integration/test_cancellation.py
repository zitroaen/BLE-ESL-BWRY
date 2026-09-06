"""A cancelled connection attempt must not wedge the integration."""

from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util


async def _setup(hass: HomeAssistant, entry):
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    device = entry.runtime_data
    device.state.last_advert = dt_util.utcnow()
    return device


async def test_timeout_during_the_wait_releases_the_lock(
    hass: HomeAssistant, config_entry, mock_bluetooth
) -> None:
    """A cancelled wait must leave the device usable.

    asyncio.CancelledError is a BaseException, so an `except Exception` around
    the connect path let the lock stay held for good and every later command
    hung forever.
    """
    device = await _setup(hass, config_entry)

    async def never_wakes(_self, wait=180):
        await asyncio.sleep(3600)

    with (
        patch(
            "custom_components.esl_zhsunyco.device.ESLDevice._async_wait_for_connectable",
            new=never_wakes,
        ),
        pytest.raises(TimeoutError),
    ):
        async with asyncio.timeout(0.05):
            async with device.connection():
                pass

    assert device._lock.locked() is False, "the lock was leaked"

    # And the device must still be usable afterwards.
    async with asyncio.timeout(1):
        await device._lock.acquire()
    device._lock.release()


async def test_timeout_during_unlock_closes_the_connection(
    hass: HomeAssistant, config_entry, mock_bluetooth
) -> None:
    """A connection opened just before a cancellation must not stay open.

    A connected label stops advertising, so a leaked connection makes the
    whole integration go dark.
    """
    device = await _setup(hass, config_entry)

    class FakeClient:
        def __init__(self):
            self.is_connected = True
            self.disconnects = 0

        async def disconnect(self):
            self.disconnects += 1
            self.is_connected = False

    client = FakeClient()

    async def fake_wait(_self, wait=180):
        return object()

    async def fake_establish(*args, **kwargs):
        return client

    async def hangs(_client):
        await asyncio.sleep(3600)

    with (
        patch(
            "custom_components.esl_zhsunyco.device.ESLDevice."
            "_async_wait_for_connectable",
            new=fake_wait,
        ),
        patch(
            "custom_components.esl_zhsunyco.device.establish_connection",
            new=fake_establish,
        ),
        patch("custom_components.esl_zhsunyco.device.protocol.unlock", new=hangs),
        pytest.raises(TimeoutError),
    ):
        async with asyncio.timeout(0.05):
            async with device.connection():
                pass

    await hass.async_block_till_done()

    assert device._lock.locked() is False
    assert device._client is None
    assert client.disconnects == 1, "the open connection was leaked"


async def test_available_while_a_connection_is_held(
    hass: HomeAssistant, config_entry, mock_bluetooth
) -> None:
    """A connected label stops advertising; entities must not drop out."""
    device = await _setup(hass, config_entry)

    class FakeClient:
        is_connected = True

    device.state.last_advert = None
    device.state.last_connect_ok = False
    assert device.available is False

    device._client = FakeClient()
    assert device.available is True


async def test_diagnostics_never_connects(
    hass: HomeAssistant, hass_client, config_entry, mock_bluetooth
) -> None:
    """Downloading diagnostics must stay a passive operation."""
    from pytest_homeassistant_custom_component.components.diagnostics import (
        get_diagnostics_for_config_entry,
    )

    device = await _setup(hass, config_entry)
    calls: list[int] = []

    async def record(_self, wait=180):
        calls.append(wait)
        raise TimeoutError

    with patch(
        "custom_components.esl_zhsunyco.device.ESLDevice._async_wait_for_connectable",
        new=record,
    ):
        result = await get_diagnostics_for_config_entry(hass, hass_client, config_entry)

    assert calls == [], "diagnostics tried to open a connection"
    assert "press the Debug probe button" in result["last_probe"]
    assert device._lock.locked() is False
