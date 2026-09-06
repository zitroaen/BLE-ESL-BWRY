"""Every command must say whether the label actually reacted."""

from __future__ import annotations

import contextlib
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .conftest import attach_services

STATUS_UUID = "34323032-4c53-4545-4c42-4b4e494c4f57"


class FakeClient:
    """A label whose status characteristic may or may not go busy."""

    def __init__(self, goes_busy: bool) -> None:
        self.is_connected = True
        self.disconnects = 0
        self.goes_busy = goes_busy
        self.command_sent = False
        attach_services(self)

    async def disconnect(self):
        self.disconnects += 1
        self.is_connected = False

    async def read_gatt_char(self, uuid):
        assert uuid == STATUS_UUID
        busy = self.goes_busy and self.command_sent
        # Busy for the first read after the command, then done.
        if busy:
            self.command_sent = False
            return bytes((1, 0)) + bytes(30)
        return bytes((0, 0)) + bytes(30)


async def _setup(hass: HomeAssistant, entry):
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    device = entry.runtime_data
    device.state.last_advert = dt_util.utcnow()
    return device


def _patches(client):
    async def fake_wait(_self, wait=180):
        return object()

    async def fake_establish(*args, **kwargs):
        return client

    async def fake_unlock(_client):
        return None

    async def fake_clear(_client, response=None):
        client.command_sent = True

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
        patch(
            "custom_components.esl_zhsunyco.device.protocol.clear_screen",
            new=fake_clear,
        ),
        patch("custom_components.esl_zhsunyco.device.STATUS_POLL_S", 0),
        patch("custom_components.esl_zhsunyco.device.STATUS_WATCH_S", 0.05),
    )


async def test_a_reacting_label_is_reported_as_such(
    hass: HomeAssistant, config_entry, mock_bluetooth
) -> None:
    """Busy rising is the proof that the command landed."""
    device = await _setup(hass, config_entry)
    client = FakeClient(goes_busy=True)

    with contextlib.ExitStack() as stack:
        for ctx in _patches(client):
            stack.enter_context(ctx)
        await device.async_clear_screen()

    record = device.state.last_command
    assert record["result"] == "ok"
    assert record["label_reacted"] is True
    assert "acted on the command" in record["interpretation"]


async def test_an_ignored_command_is_not_reported_as_success(
    hass: HomeAssistant, config_entry, mock_bluetooth
) -> None:
    """A write the label swallows must not look like it worked.

    This is the gap a plain "ok" leaves: the write is accepted either way.
    """
    device = await _setup(hass, config_entry)
    client = FakeClient(goes_busy=False)

    with contextlib.ExitStack() as stack:
        for ctx in _patches(client):
            stack.enter_context(ctx)
        await device.async_clear_screen()

    record = device.state.last_command
    assert record["result"] == "ok", "the write itself did succeed"
    assert record["label_reacted"] is False
    assert "not understood" in record["interpretation"]


async def test_missing_characteristic_clears_the_cache_and_retries(
    hass: HomeAssistant, config_entry, mock_bluetooth
) -> None:
    """The reported failure must be retried once, not surfaced immediately."""
    from bleak.exc import BleakCharacteristicNotFoundError

    device = await _setup(hass, config_entry)
    client = FakeClient(goes_busy=True)
    attempts: list[int] = []

    async def flaky_clear(_client, response=None):
        attempts.append(1)
        if len(attempts) == 1:
            raise BleakCharacteristicNotFoundError("31323032")
        client.command_sent = True

    async def fake_wait(_self, wait=180):
        return object()

    async def fake_establish(*args, **kwargs):
        client.is_connected = True
        return client

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
        patch("custom_components.esl_zhsunyco.device.protocol.unlock", new=AsyncMock()),
        patch(
            "custom_components.esl_zhsunyco.device.protocol.clear_screen",
            new=flaky_clear,
        ),
        patch("custom_components.esl_zhsunyco.device.STATUS_POLL_S", 0),
        patch("custom_components.esl_zhsunyco.device.STATUS_WATCH_S", 0.05),
    ):
        await device.async_clear_screen()

    assert len(attempts) == 2, "the command was not retried"
    assert client.services.cleared >= 1, "the service cache was not cleared"
    assert device.state.last_command["result"] == "ok"


async def test_a_persistently_missing_characteristic_still_fails(
    hass: HomeAssistant, config_entry, mock_bluetooth
) -> None:
    """One retry, not an endless loop."""
    from bleak.exc import BleakCharacteristicNotFoundError

    device = await _setup(hass, config_entry)
    client = FakeClient(goes_busy=True)
    attempts: list[int] = []

    async def always_missing(_client, response=None):
        attempts.append(1)
        raise BleakCharacteristicNotFoundError("31323032")

    async def fake_wait(_self, wait=180):
        return object()

    async def fake_establish(*args, **kwargs):
        client.is_connected = True
        return client

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
        patch("custom_components.esl_zhsunyco.device.protocol.unlock", new=AsyncMock()),
        patch(
            "custom_components.esl_zhsunyco.device.protocol.clear_screen",
            new=always_missing,
        ),
        patch("custom_components.esl_zhsunyco.device.STATUS_POLL_S", 0),
        patch("custom_components.esl_zhsunyco.device.STATUS_WATCH_S", 0.05),
        pytest.raises(BleakCharacteristicNotFoundError),
    ):
        await device.async_clear_screen()

    assert len(attempts) == 2
    assert device.state.last_command["result"] == "failed"
    assert "31323032" in device.state.last_command["detail"]
