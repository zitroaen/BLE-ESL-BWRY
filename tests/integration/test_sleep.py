"""Connecting to a label that sleeps between advertisements."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError


async def _setup(hass: HomeAssistant, entry):
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry.runtime_data


async def test_setup_does_not_block_on_a_sleeping_label(
    hass: HomeAssistant, config_entry, mock_bluetooth
) -> None:
    """Startup must not wait for a connection that can take minutes.

    A blocking first refresh held up Home Assistant startup for 96 seconds.
    """
    slow_call = AsyncMock()

    async def never_returns(*args, **kwargs):
        await slow_call()
        raise AssertionError("should not be awaited during setup")

    with patch(
        "custom_components.esl_zhsunyco.device.ESLDevice._async_poll",
        side_effect=never_returns,
    ):
        entry = config_entry
        entry.add_to_hass(hass)
        assert await hass.config_entries.async_setup(entry.entry_id)

    # Setup returned without the poll having completed.
    assert entry.runtime_data is not None


async def test_connect_waits_for_the_label_to_wake(
    hass: HomeAssistant, config_entry, mock_bluetooth
) -> None:
    """A sleeping label must be waited for, not reported as unreachable."""
    device = await _setup(hass, config_entry)

    ble_device = object()
    resolved: list[object] = []

    def fake_from_address(hass_, address, connectable=True):
        # Unreachable on the first look, reachable once it has advertised.
        return ble_device if resolved else None

    async def fake_process(hass_, callback, matcher, mode, timeout):
        assert matcher["address"] == device.address
        assert matcher["connectable"] is True
        resolved.append(True)
        return SimpleNamespace(address=device.address)

    with (
        patch(
            "custom_components.esl_zhsunyco.device.bluetooth."
            "async_ble_device_from_address",
            side_effect=fake_from_address,
        ),
        patch(
            "custom_components.esl_zhsunyco.device.bluetooth."
            "async_process_advertisements",
            side_effect=fake_process,
        ),
    ):
        result = await device._async_wait_for_connectable(timeout=5)

    assert result is ble_device
    assert resolved, "the integration must wait for an advertisement"


async def test_connect_uses_the_device_directly_when_awake(
    hass: HomeAssistant, config_entry, mock_bluetooth
) -> None:
    """An awake label must not incur a wait."""
    device = await _setup(hass, config_entry)
    ble_device = object()

    waited = False

    async def fake_process(*args, **kwargs):
        nonlocal waited
        waited = True

    with (
        patch(
            "custom_components.esl_zhsunyco.device.bluetooth."
            "async_ble_device_from_address",
            return_value=ble_device,
        ),
        patch(
            "custom_components.esl_zhsunyco.device.bluetooth."
            "async_process_advertisements",
            side_effect=fake_process,
        ),
    ):
        assert await device._async_wait_for_connectable(timeout=5) is ble_device

    assert waited is False


async def test_timeout_explains_the_cause(
    hass: HomeAssistant, config_entry, mock_bluetooth
) -> None:
    """A label that never wakes must produce an actionable message."""
    device = await _setup(hass, config_entry)

    async def fake_process(*args, **kwargs):
        raise TimeoutError

    with (
        patch(
            "custom_components.esl_zhsunyco.device.bluetooth."
            "async_ble_device_from_address",
            return_value=None,
        ),
        patch(
            "custom_components.esl_zhsunyco.device.bluetooth."
            "async_process_advertisements",
            side_effect=fake_process,
        ),
        pytest.raises(HomeAssistantError, match="did not advertise within 5s"),
    ):
        await device._async_wait_for_connectable(timeout=5)


async def test_probe_reports_the_sleep_failure(
    hass: HomeAssistant, config_entry, mock_bluetooth
) -> None:
    """The probe must name the reason rather than raising."""
    device = await _setup(hass, config_entry)

    async def fake_process(*args, **kwargs):
        raise TimeoutError

    with (
        patch(
            "custom_components.esl_zhsunyco.device.bluetooth."
            "async_ble_device_from_address",
            return_value=None,
        ),
        patch(
            "custom_components.esl_zhsunyco.device.bluetooth."
            "async_process_advertisements",
            side_effect=fake_process,
        ),
    ):
        report = await device.async_probe()

    assert report["connection"] == "failed"
    assert "did not advertise" in report["connection_error"]

    # A failed probe cannot answer the GATT questions; say so rather than
    # leaving the reader to notice the sections are absent.
    missing = report["sections_missing"]["sections"]
    assert "protocol_family" in missing
    assert "known_characteristics" in missing
    assert "reads" in missing
    for section in missing:
        assert section not in report, f"{section} present despite no connection"


async def test_failed_probe_still_reports_the_battery(
    hass: HomeAssistant, config_entry, mock_bluetooth
) -> None:
    """Advertisement data stays readable when the connection fails."""
    import struct

    device = await _setup(hass, config_entry)
    device._advert_received(
        SimpleNamespace(
            rssi=-38,
            manufacturer_data={
                0xBBAA: struct.pack("<HHHH", 0x0030, 0x000E, 0x0330, 0x0201)
                + struct.pack(">H", 2978)
            },
        ),
        None,
    )
    await hass.async_block_till_done()

    async def fake_process(*args, **kwargs):
        raise TimeoutError

    with (
        patch(
            "custom_components.esl_zhsunyco.device.bluetooth."
            "async_ble_device_from_address",
            return_value=None,
        ),
        patch(
            "custom_components.esl_zhsunyco.device.bluetooth."
            "async_process_advertisements",
            side_effect=fake_process,
        ),
    ):
        report = await device.async_probe()

    assert report["advertisement"]["battery_v"] == 2.978
