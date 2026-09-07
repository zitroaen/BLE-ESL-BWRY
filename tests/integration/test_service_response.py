"""What a service call reports back, so an automation can react to it."""

from __future__ import annotations

import contextlib
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.util import dt as dt_util

from custom_components.esl_zhsunyco.const import (
    DOMAIN,
    SERVICE_CLEAR_SCREEN,
    SERVICE_SEND_TEST_PATTERN,
    UUID_STATUS,
)


class FakeLabel:
    """A label that can be told whether to act on a command."""

    def __init__(self, reacts: bool = True) -> None:
        self.is_connected = True
        self.reacts = reacts
        self._busy = False
        self.writes: list[bytes] = []

    async def read_gatt_char(self, uuid):
        assert uuid == UUID_STATUS
        busy, self._busy = self._busy, False
        return bytes((1 if busy else 0, 0)) + bytes(30)

    async def write_gatt_char(self, uuid, data, response=None):
        self.writes.append(bytes(data))
        if self.reacts:
            self._busy = True


async def _setup(hass: HomeAssistant, entry):
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    device = entry.runtime_data
    device.state.last_advert = dt_util.utcnow()
    device.coordinator.async_update_listeners()
    return device


def _device_id(hass: HomeAssistant, entry) -> str:
    return dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)[0].id


def _connected(label, device=None):
    """Patch the connection, and make the device look genuinely connected.

    Without the _client assignment `device.connected` stays False and every
    command looks as if the label hung up, which is a different finding.
    """

    async def enter(_self):
        if device is not None:
            device._client = label
        return label

    return (
        patch(
            "custom_components.esl_zhsunyco.device._ESLConnection.__aenter__",
            new=enter,
        ),
        patch(
            "custom_components.esl_zhsunyco.device._ESLConnection.__aexit__",
            new=AsyncMock(return_value=False),
        ),
        patch("custom_components.esl_zhsunyco.device.STATUS_POLL_S", 0),
        patch("custom_components.esl_zhsunyco.device.STATUS_WATCH_S", 0.01),
    )


async def _call(hass, service, data, label, device=None):
    with contextlib.ExitStack() as stack:
        for ctx in _connected(label, device):
            stack.enter_context(ctx)
        return await hass.services.async_call(
            DOMAIN, service, data, blocking=True, return_response=True
        )


async def test_a_reacting_label_reports_success(
    hass: HomeAssistant, config_entry, mock_bluetooth
) -> None:
    """The panel went busy, so the command really landed."""
    await _setup(hass, config_entry)
    label = FakeLabel(reacts=True)

    response = await _call(
        hass,
        SERVICE_CLEAR_SCREEN,
        {"device_id": _device_id(hass, config_entry)},
        label,
    )

    result = response["results"][0]
    assert result["ok"] is True
    assert result["label_reacted"] is True
    assert result["address"] == "66:66:54:20:00:55"


async def test_an_ignored_command_is_reported_as_not_reacted(
    hass: HomeAssistant, config_entry, mock_bluetooth
) -> None:
    """The case an exception cannot express, and the reason this exists.

    The write is accepted and the label does nothing. Nothing raises, so an
    automation that only watches for errors would call this a success.
    """
    device = await _setup(hass, config_entry)
    label = FakeLabel(reacts=False)

    response = await _call(
        hass,
        SERVICE_CLEAR_SCREEN,
        {"device_id": _device_id(hass, config_entry)},
        label,
        device,
    )

    result = response["results"][0]
    assert result["ok"] is True, "the write itself did succeed"
    assert result["label_reacted"] is False
    assert "not understood" in result["detail"]


async def test_an_image_send_reports_its_size(
    hass: HomeAssistant, config_entry, mock_bluetooth
) -> None:
    """Enough to tell a full upload from a truncated one in an automation."""
    await _setup(hass, config_entry)
    label = FakeLabel(reacts=True)

    with patch(
        "custom_components.esl_zhsunyco.device.protocol.send_prepared_image",
        new=AsyncMock(),
    ):
        response = await _call(
            hass,
            SERVICE_SEND_TEST_PATTERN,
            {"device_id": _device_id(hass, config_entry)},
            label,
        )

    result = response["results"][0]
    assert result["ok"] is True
    assert result["bytes"] == 184 * 384 // 4
    assert result["encoding"] == "bwry_packed"


async def test_the_response_is_optional(
    hass: HomeAssistant, config_entry, mock_bluetooth
) -> None:
    """Automations written before this existed must keep working unchanged."""
    await _setup(hass, config_entry)
    label = FakeLabel(reacts=True)

    with contextlib.ExitStack() as stack:
        for ctx in _connected(label):
            stack.enter_context(ctx)
        result = await hass.services.async_call(
            DOMAIN,
            SERVICE_CLEAR_SCREEN,
            {"device_id": _device_id(hass, config_entry)},
            blocking=True,
        )

    assert result is None
    assert label.writes == [b"\x04\xa5"]


async def test_a_failed_write_still_raises(
    hass: HomeAssistant, config_entry, mock_bluetooth
) -> None:
    """The response must not turn a real failure into a quiet success."""
    await _setup(hass, config_entry)
    label = FakeLabel(reacts=True)

    async def boom(uuid, data, response=None):
        raise OSError("the label hung up")

    label.write_gatt_char = boom

    raised = False
    try:
        await _call(
            hass,
            SERVICE_CLEAR_SCREEN,
            {"device_id": _device_id(hass, config_entry)},
            label,
        )
    except Exception as err:  # noqa: BLE001 - any error type is fine here
        raised = "hung up" in str(err)

    assert raised, "a failing write has to surface as an error"


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (TimeoutError(), "TimeoutError"),
        (OSError(), "OSError"),
        (ValueError("something specific"), "something specific"),
    ],
    ids=["bare timeout", "bare oserror", "with a message"],
)
async def test_a_failure_says_what_and_when(
    hass: HomeAssistant, config_entry, mock_bluetooth, error, expected
) -> None:
    """Home Assistant shows whatever escapes, so it has to be legible.

    A bare TimeoutError reaches the frontend as the single word "Timeout",
    and a bare OSError as "Unknown error" - neither says which step failed
    or how long it took.
    """
    device = await _setup(hass, config_entry)
    label = FakeLabel(reacts=True)

    async def boom(uuid, data, response=None):
        raise error

    label.write_gatt_char = boom

    with pytest.raises(HomeAssistantError) as caught:
        await _call(
            hass,
            SERVICE_CLEAR_SCREEN,
            {"device_id": _device_id(hass, config_entry)},
            label,
            device,
        )

    message = str(caught.value)
    assert device.address in message
    assert "clear_screen" in message
    assert "writing the command" in message, "which step failed"
    assert expected in message
    assert message != "Timeout"


async def test_the_record_keeps_the_step_and_the_duration(
    hass: HomeAssistant, config_entry, mock_bluetooth
) -> None:
    """Diagnostics has to carry the same context as the error message."""
    device = await _setup(hass, config_entry)
    label = FakeLabel(reacts=True)

    async def boom(uuid, data, response=None):
        raise TimeoutError

    label.write_gatt_char = boom

    with contextlib.suppress(HomeAssistantError):
        await _call(
            hass,
            SERVICE_CLEAR_SCREEN,
            {"device_id": _device_id(hass, config_entry)},
            label,
            device,
        )

    record = device.state.last_command
    assert record["result"] == "failed"
    assert record["phase"] == "writing the command"
    assert isinstance(record["elapsed_s"], float)


async def test_a_failed_connect_reports_what_the_stack_sees(
    hass: HomeAssistant, config_entry, mock_bluetooth
) -> None:
    """"Cannot connect" is the one failure that needs the surroundings.

    Advertisements carry further than a usable connection does, so a label
    that is plainly visible and still will not connect looks like a
    contradiction. The numbers that resolve it are how many scanners could
    connect at all and which one heard it last.
    """
    from bleak_retry_connector import BleakNotFoundError

    await _setup(hass, config_entry)

    async def fake_wait(_self, wait=300):
        return object()

    with (
        patch(
            "custom_components.esl_zhsunyco.device.ESLDevice."
            "_async_wait_for_connectable",
            new=fake_wait,
        ),
        patch(
            "custom_components.esl_zhsunyco.device.establish_connection",
            side_effect=BleakNotFoundError("Failed to connect after 7 attempt(s)"),
        ),
        pytest.raises(HomeAssistantError) as caught,
    ):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_CLEAR_SCREEN,
            {"device_id": _device_id(hass, config_entry)},
            blocking=True,
        )

    message = str(caught.value)
    assert "while connecting" in message
    assert "connectable scanner" in message, "how many could connect"
    assert "power cycle" in message, "and what to try"
