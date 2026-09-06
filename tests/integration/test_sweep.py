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


async def test_command_sweep_service_runs(
    hass: HomeAssistant, config_entry, mock_bluetooth
) -> None:
    """The service must reach the device and report, without blocking on it.

    A sweep can run for many minutes. Awaiting it inside the service call
    left the caller holding an open call for the whole time; the frontend
    gave up first and showed an error with no text at all - "undefined".
    So the call returns at once and the work continues in the background.
    """
    import contextlib

    from homeassistant.components import persistent_notification
    from homeassistant.helpers import device_registry as dr

    from custom_components.esl_zhsunyco.const import DOMAIN, SERVICE_COMMAND_SWEEP

    device = await _setup(hass, config_entry)
    device_entry = dr.async_entries_for_config_entry(
        dr.async_get(hass), config_entry.entry_id
    )[0]
    client = FakeClient(reacts_to=b"\xa5\x09\xfe\xfe")

    with contextlib.ExitStack() as stack:
        for ctx in _connection(client):
            stack.enter_context(ctx)
        await hass.services.async_call(
            DOMAIN,
            SERVICE_COMMAND_SWEEP,
            {"device_id": device_entry.id, "settle": 0.1},
            blocking=True,
        )
        # The call is back before the sweep is; that is the point.
        assert device.state.last_command is None
        await hass.async_block_till_done(wait_background_tasks=True)

    assert device.state.last_command is not None
    assert device.state.last_command["working_payload"] == "a5 09 fe fe"

    notifications = persistent_notification._async_get_or_create_notifications(hass)
    body = notifications[f"{DOMAIN}_sweep_{device.address}"]["message"]
    assert "a5 09 fe fe" in body


async def test_command_sweep_failure_is_reported_with_text(
    hass: HomeAssistant, config_entry, mock_bluetooth
) -> None:
    """A sweep that blows up must say what happened.

    Several BLE timeouts stringify to the empty string. Surfacing one of
    those verbatim is how the user ended up staring at "undefined", so the
    notification always carries the exception type as well.
    """
    from homeassistant.components import persistent_notification
    from homeassistant.helpers import device_registry as dr

    from custom_components.esl_zhsunyco.const import DOMAIN, SERVICE_COMMAND_SWEEP

    device = await _setup(hass, config_entry)
    device_entry = dr.async_entries_for_config_entry(
        dr.async_get(hass), config_entry.entry_id
    )[0]

    with patch.object(
        type(device), "async_command_sweep", side_effect=TimeoutError()
    ):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_COMMAND_SWEEP,
            {"device_id": device_entry.id},
            blocking=True,
        )
        await hass.async_block_till_done(wait_background_tasks=True)

    notifications = persistent_notification._async_get_or_create_notifications(hass)
    body = notifications[f"{DOMAIN}_sweep_{device.address}"]["message"]
    assert "TimeoutError" in body
    assert "undefined" not in body


async def test_tolerated_candidate_keeps_the_connection(
    hass: HomeAssistant, config_entry, mock_bluetooth
) -> None:
    """Only a rejection is worth another wake-up.

    Reconnecting per candidate is what made a full sweep take twenty
    minutes: waiting for a sleeping label to advertise costs up to three
    minutes each time. A candidate the label merely ignores leaves the link
    unlocked and usable, so it is kept.
    """
    import contextlib

    device = await _setup(hass, config_entry)
    client = FakeClient(drops_on=b"\x04\xa5")
    disconnects = 0
    real_disconnect = device._async_disconnect

    async def counting_disconnect():
        nonlocal disconnects
        disconnects += 1
        device._client = None
        await real_disconnect()

    device._async_disconnect = counting_disconnect

    async def enter(_self):
        client.reconnect()
        # Mirror what the real connection does, so device.connected is true.
        device._client = client
        return client

    with contextlib.ExitStack() as stack:
        stack.enter_context(
            patch(
                "custom_components.esl_zhsunyco.device._ESLConnection.__aenter__",
                new=enter,
            )
        )
        stack.enter_context(
            patch(
                "custom_components.esl_zhsunyco.device._ESLConnection.__aexit__",
                new=AsyncMock(return_value=False),
            )
        )
        stack.enter_context(
            patch("custom_components.esl_zhsunyco.device.STATUS_POLL_S", 0)
        )
        stack.enter_context(
            patch("custom_components.esl_zhsunyco.device.STATUS_WATCH_S", 0.01)
        )
        report = await device.async_command_sweep(
            [b"\xa5\x04", b"\xa5\x04\x00", b"\x04\xa5", b"\xa5\x09"], settle=0
        )

    # Four candidates, but only the rejection is worth dropping the link for.
    assert disconnects == 1
    fresh = [item["fresh_connection"] for item in report["results"]]
    assert fresh == [True, False, False, True]


async def test_little_endian_candidates_come_first() -> None:
    """Ordering follows the evidence, and every rejection costs a reconnect.

    Little endian is what the hardware accepted for 0xA500 and 0xA501, so
    04 a5 leads. It used to be last, marked "known rejected" - a verdict
    from a sweep that had already written a5 04 on the same connection and
    so judged it on a link the label had cut.
    """
    from custom_components.esl_zhsunyco.protocol import clear_screen_candidates

    candidates = clear_screen_candidates()
    assert candidates[0] == b"\x04\xa5"
    assert candidates[1] == b"\x09\xa5\xfe\xfe"
    assert b"\xa5\x04" in candidates, "big endian stays in, it is untested"
    assert candidates.index(b"\x04\xa5") < candidates.index(b"\xa5\x04")
    assert len(set(candidates)) == len(candidates)


async def test_command_variants_lead_with_little_endian() -> None:
    """Same reasoning for an arbitrary opcode."""
    from custom_components.esl_zhsunyco.protocol import command_variants

    variants = command_variants(0xA504)
    assert variants[0] == b"\x04\xa5"
    assert b"\xa5\x04" in variants
    assert len(set(variants)) == len(variants), "no duplicate candidates"
