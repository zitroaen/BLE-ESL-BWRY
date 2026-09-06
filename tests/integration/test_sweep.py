"""Command sweep: sending several encodings in one connection."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from custom_components.esl_zhsunyco.const import UUID_STATUS


class FakeClient:
    """A label that only reacts to one specific payload."""

    def __init__(self, reacts_to: bytes | None = None, drops_on: bytes | None = None):
        self.reacts_to = reacts_to
        self.drops_on = drops_on
        self.writes: list[bytes] = []
        self.is_connected = True
        self._busy = False

    async def read_gatt_char(self, uuid):
        assert uuid == UUID_STATUS
        return bytes((1 if self._busy else 0, 0)) + bytes(30)

    async def write_gatt_char(self, uuid, data, response=None):
        payload = bytes(data)
        self.writes.append(payload)
        if self.drops_on is not None and payload == self.drops_on:
            self.is_connected = False
            raise OSError("device disconnected")
        if self.reacts_to is not None and payload == self.reacts_to:
            self._busy = True


async def _setup(hass: HomeAssistant, entry):
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    device = entry.runtime_data
    device.state.last_advert = dt_util.utcnow()
    return device


def _connection(client):
    return (
        patch(
            "custom_components.esl_zhsunyco.device._ESLConnection.__aenter__",
            new=AsyncMock(return_value=client),
        ),
        patch(
            "custom_components.esl_zhsunyco.device._ESLConnection.__aexit__",
            new=AsyncMock(return_value=False),
        ),
    )


async def test_sweep_identifies_the_payload_the_label_reacts_to(
    hass: HomeAssistant, config_entry, mock_bluetooth
) -> None:
    """A status change is the signal that a command landed."""
    device = await _setup(hass, config_entry)
    client = FakeClient(reacts_to=b"\x04\xa5")

    enter, exit_ = _connection(client)
    with enter, exit_:
        report = await device.async_command_sweep([b"\xa5\x04", b"\x04\xa5"], settle=0)

    assert report["any_status_changed"] is True
    by_payload = {item["payload"]: item for item in report["results"]}
    assert by_payload["a5 04"]["status_changed"] is False
    assert by_payload["04 a5"]["status_changed"] is True


async def test_sweep_reports_no_reaction_at_all(
    hass: HomeAssistant, config_entry, mock_bluetooth
) -> None:
    """An inert label must be reported as such, not as success."""
    device = await _setup(hass, config_entry)
    client = FakeClient()

    enter, exit_ = _connection(client)
    with enter, exit_:
        report = await device.async_command_sweep(
            [b"\xa5\x04", b"\x04\xa5", b"\xa5\x04\x00"], settle=0
        )

    assert report["any_status_changed"] is False
    assert len(report["results"]) == 3
    assert all(item["write"] == "ok" for item in report["results"])


async def test_sweep_stops_when_the_label_disconnects(
    hass: HomeAssistant, config_entry, mock_bluetooth
) -> None:
    """A drop right after one payload is itself a finding."""
    device = await _setup(hass, config_entry)
    client = FakeClient(drops_on=b"\x04\xa5")

    enter, exit_ = _connection(client)
    with enter, exit_:
        report = await device.async_command_sweep(
            [b"\xa5\x04", b"\x04\xa5", b"\xa5\x04\x00"], settle=0
        )

    # The third payload must not be attempted after the connection is gone.
    assert len(report["results"]) == 2
    last = report["results"][-1]
    assert last["payload"] == "04 a5"
    assert last["connected_after"] is False
    assert "dropped the connection" in last["note"]


async def test_sweep_result_is_kept_for_diagnostics(
    hass: HomeAssistant, config_entry, mock_bluetooth
) -> None:
    """A button press must stop being a silent event."""
    device = await _setup(hass, config_entry)
    client = FakeClient()

    enter, exit_ = _connection(client)
    with enter, exit_:
        await device.async_command_sweep([b"\xa5\x04"], settle=0)

    assert device.state.last_command is not None
    assert device.state.last_command["results"][0]["payload"] == "a5 04"


async def test_variants_cover_both_byte_orders() -> None:
    """The default candidate set must contain the obvious alternatives."""
    from custom_components.esl_zhsunyco.protocol import command_variants

    variants = command_variants(0xA504)
    assert b"\xa5\x04" in variants
    assert b"\x04\xa5" in variants
    assert len(set(variants)) == len(variants), "no duplicate candidates"
