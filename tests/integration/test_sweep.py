"""Command sweep: sending several encodings in one connection."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from custom_components.esl_zhsunyco.const import UUID_STATUS


class FakeClient:
    """A label that only reacts to one specific payload.

    Models what the hardware does: a rejected command revokes authorisation,
    after which even a plain read fails.
    """

    def __init__(self, reacts_to: bytes | None = None, drops_on: bytes | None = None):
        self.reacts_to = reacts_to
        self.drops_on = drops_on
        self.writes: list[bytes] = []
        self.is_connected = True
        self._busy = False
        self.revoked = False

    def reconnect(self) -> None:
        self.is_connected = True
        self.revoked = False

    async def read_gatt_char(self, uuid):
        assert uuid == UUID_STATUS
        if self.revoked:
            raise OSError("BluetoothGATTErrorResponse: Insufficient authorization (8)")
        return bytes((1 if self._busy else 0, 0)) + bytes(30)

    async def write_gatt_char(self, uuid, data, response=None):
        payload = bytes(data)
        self.writes.append(payload)
        if self.drops_on is not None and payload == self.drops_on:
            self.revoked = True
            self.is_connected = False
            return
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
    """Patch the connection so each candidate gets a fresh, unlocked link."""

    async def enter(_self):
        client.reconnect()
        return client

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


async def test_sweep_identifies_the_payload_the_label_reacts_to(
    hass: HomeAssistant, config_entry, mock_bluetooth
) -> None:
    """A status change is the signal that a command landed."""
    device = await _setup(hass, config_entry)
    client = FakeClient(reacts_to=b"\x04\xa5")

    import contextlib

    with contextlib.ExitStack() as stack:
        for ctx in _connection(client):
            stack.enter_context(ctx)
        report = await device.async_command_sweep([b"\xa5\x04", b"\x04\xa5"], settle=0)

    assert report["working_payload"] == "04 a5"
    by_payload = {item["payload"]: item for item in report["results"]}
    assert by_payload["a5 04"]["label_reacted"] is False
    assert by_payload["04 a5"]["label_reacted"] is True


async def test_sweep_reports_no_reaction_at_all(
    hass: HomeAssistant, config_entry, mock_bluetooth
) -> None:
    """An inert label must be reported as such, not as success."""
    device = await _setup(hass, config_entry)
    client = FakeClient()

    import contextlib

    with contextlib.ExitStack() as stack:
        for ctx in _connection(client):
            stack.enter_context(ctx)
        report = await device.async_command_sweep(
            [b"\xa5\x04", b"\x04\xa5", b"\xa5\x04\x00"], settle=0
        )

    assert report["any_status_changed"] is False
    assert len(report["results"]) == 3
    assert report["tolerated_but_ignored"] == ["a5 04", "04 a5", "a5 04 00"]


async def test_sweep_stops_when_the_label_disconnects(
    hass: HomeAssistant, config_entry, mock_bluetooth
) -> None:
    """A drop right after one payload is itself a finding."""
    device = await _setup(hass, config_entry)
    client = FakeClient(drops_on=b"\x04\xa5")

    import contextlib

    with contextlib.ExitStack() as stack:
        for ctx in _connection(client):
            stack.enter_context(ctx)
        report = await device.async_command_sweep(
            [b"\xa5\x04", b"\x04\xa5", b"\xa5\x04\x00"], settle=0
        )

    # A revoked candidate must not stop the sweep any more: each one gets a
    # fresh connection, so the ones after it are still worth testing.
    assert len(report["results"]) == 3
    rejected = report["results"][1]
    assert rejected["payload"] == "04 a5"
    assert rejected["rejected"] is True
    assert "a5 04 00" in report["tolerated_but_ignored"]


async def test_sweep_result_is_kept_for_diagnostics(
    hass: HomeAssistant, config_entry, mock_bluetooth
) -> None:
    """A button press must stop being a silent event."""
    device = await _setup(hass, config_entry)
    client = FakeClient()

    import contextlib

    with contextlib.ExitStack() as stack:
        for ctx in _connection(client):
            stack.enter_context(ctx)
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


async def test_att_authorisation_error_is_recognised_as_rejection(
    hass: HomeAssistant, config_entry, mock_bluetooth
) -> None:
    """ATT error 0x08 is the label refusing, not a transport glitch.

    Measured on hardware: after writing 04 a5 the label answered the next
    plain read with "Insufficient authorization (8)". Treating that as an
    ordinary error would hide the most informative signal we get.
    """
    import contextlib

    device = await _setup(hass, config_entry)
    client = FakeClient(drops_on=b"\x04\xa5")

    with contextlib.ExitStack() as stack:
        for ctx in _connection(client):
            stack.enter_context(ctx)
        report = await device.async_command_sweep([b"\x04\xa5", b"\xa5\x04"], settle=0)

    rejected = report["results"][0]
    assert rejected["payload"] == "04 a5"
    assert rejected["rejected"] is True

    # And the next candidate still got a real chance.
    assert report["results"][1]["payload"] == "a5 04"
    assert report["results"][1]["rejected"] is False


async def test_tolerated_but_ignored_is_reported_separately(
    hass: HomeAssistant, config_entry, mock_bluetooth
) -> None:
    """A command the label tolerates is a different finding from one it refuses.

    a5 04 is tolerated and ignored; 04 a5 gets access revoked. Only the first
    is evidence that the byte order is right.
    """
    import contextlib

    device = await _setup(hass, config_entry)
    client = FakeClient(drops_on=b"\x04\xa5")

    with contextlib.ExitStack() as stack:
        for ctx in _connection(client):
            stack.enter_context(ctx)
        report = await device.async_command_sweep([b"\xa5\x04", b"\x04\xa5"], settle=0)

    assert report["tolerated_but_ignored"] == ["a5 04"]
    assert "no candidate made the panel react" in report["conclusion"]
