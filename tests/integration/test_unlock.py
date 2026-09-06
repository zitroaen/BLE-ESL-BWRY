"""Finding out which AES unlock computation the label actually accepts."""

from __future__ import annotations

from unittest.mock import patch

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from custom_components.esl_zhsunyco.const import AES_KEY, UUID_SECURITY, UUID_STATUS
from custom_components.esl_zhsunyco.protocol import UNLOCK_VARIANTS

from .conftest import attach_services

CHALLENGE = bytes(range(16))


def _expected(variant: str) -> bytes:
    return UNLOCK_VARIANTS[variant](CHALLENGE)


class FakeLabel:
    """A label that only unlocks for one specific computation.

    Mirrors the documented behaviour: a locked label accepts the unlock write,
    then drops the link on the next write to any other characteristic.
    """

    def __init__(self, accepts: str | None) -> None:
        self.is_connected = True
        self.accepts = accepts
        self.unlocked = False
        self.busy = False
        self.commands: list[bytes] = []
        self.disconnects = 0
        attach_services(self)

    def reconnect(self) -> None:
        self.is_connected = True
        self.unlocked = False

    async def disconnect(self):
        self.disconnects += 1
        self.is_connected = False

    async def start_notify(self, uuid, cb):
        return None

    async def read_gatt_char(self, uuid):
        if uuid == UUID_SECURITY:
            return CHALLENGE
        if uuid == UUID_STATUS:
            busy, self.busy = self.busy, False
            return bytes((1 if busy else 0, 0)) + bytes(30)
        raise AssertionError(f"unexpected read of {uuid}")

    async def write_gatt_char(self, uuid, data, response=None):
        payload = bytes(data)
        if uuid == UUID_SECURITY:
            self.unlocked = self.accepts is not None and payload == _expected(
                self.accepts
            )
            return
        self.commands.append(payload)
        if self.unlocked:
            self.busy = True
        else:
            # "writing other services will be disconnected immediately"
            self.is_connected = False


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
        client.reconnect()
        return client

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
        patch("custom_components.esl_zhsunyco.device.STATUS_POLL_S", 0),
        patch("custom_components.esl_zhsunyco.device.STATUS_WATCH_S", 0.01),
    )


async def _sweep(hass, device, client):
    import contextlib

    with contextlib.ExitStack() as stack:
        for ctx in _patches(client):
            stack.enter_context(ctx)
        return await device.async_unlock_sweep(settle=0)


async def test_sweep_finds_the_documented_encrypt_variant(
    hass: HomeAssistant, config_entry, mock_bluetooth
) -> None:
    """A label behaving as documented must be identified as such."""
    device = await _setup(hass, config_entry)
    report = await _sweep(hass, device, FakeLabel(accepts="encrypt"))

    assert report["working_variant"] == "encrypt"
    assert "reacted to the 'encrypt' unlock" in report["conclusion"]


async def test_sweep_finds_decrypt_when_the_document_is_wrong(
    hass: HomeAssistant, config_entry, mock_bluetooth
) -> None:
    """The whole point: the document says encryption, it may mean the inverse."""
    device = await _setup(hass, config_entry)
    report = await _sweep(hass, device, FakeLabel(accepts="decrypt"))

    assert report["working_variant"] == "decrypt"
    by_variant = {item["variant"]: item for item in report["results"]}
    assert by_variant["encrypt"]["label_reacted"] is False
    assert by_variant["decrypt"]["label_reacted"] is True


async def test_sweep_reports_when_nothing_unlocks(
    hass: HomeAssistant, config_entry, mock_bluetooth
) -> None:
    """No variant working is a finding too, not a silent pass."""
    device = await _setup(hass, config_entry)
    report = await _sweep(hass, device, FakeLabel(accepts=None))

    assert report["working_variant"] is None
    assert "no unlock variant" in report["conclusion"]
    assert len(report["results"]) == len(UNLOCK_VARIANTS)


async def test_every_variant_gets_its_own_connection(
    hass: HomeAssistant, config_entry, mock_bluetooth
) -> None:
    """Each variant must get a connection of its own.

    A rejected unlock drops the link, so sharing one connection would only
    ever test the first variant.
    """
    device = await _setup(hass, config_entry)
    label = FakeLabel(accepts="encrypt_reversed")
    report = await _sweep(hass, device, label)

    # encrypt and decrypt come first and are rejected; each must still have
    # been given a working connection of its own.
    tried = [item["variant"] for item in report["results"]]
    assert tried[:3] == ["encrypt", "decrypt", "encrypt_reversed"]
    assert report["working_variant"] == "encrypt_reversed"

    by_variant = {item["variant"]: item for item in report["results"]}
    assert by_variant["encrypt"]["connected_after_command"] is False
    assert "dropped the link" in by_variant["encrypt"]["note"]


async def test_sweep_stops_at_the_first_working_variant(
    hass: HomeAssistant, config_entry, mock_bluetooth
) -> None:
    """No point reconnecting for the rest once one works."""
    device = await _setup(hass, config_entry)
    report = await _sweep(hass, device, FakeLabel(accepts="encrypt"))

    assert [item["variant"] for item in report["results"]] == ["encrypt"]


async def test_sweep_records_the_challenge_and_every_response(
    hass: HomeAssistant, config_entry, mock_bluetooth
) -> None:
    """The report must be checkable by hand afterwards."""
    device = await _setup(hass, config_entry)
    report = await _sweep(hass, device, FakeLabel(accepts=None))

    # Each variant now has its own connection, so the challenge is recorded
    # per attempt rather than once for the sweep.
    for item in report["results"]:
        assert item["challenge"] == CHALLENGE.hex(" ")
        assert item["response"] == _expected(item["variant"]).hex(" ")


def test_variants_are_distinct_and_use_the_vendor_key():
    """Sweeping identical candidates would prove nothing."""
    responses = {name: fn(CHALLENGE) for name, fn in UNLOCK_VARIANTS.items()}
    assert len(set(responses.values())) == len(responses)
    assert all(len(value) == 16 for value in responses.values())

    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    enc = Cipher(algorithms.AES(AES_KEY), modes.ECB()).encryptor()
    assert responses["encrypt"] == enc.update(CHALLENGE) + enc.finalize()

    dec = Cipher(algorithms.AES(AES_KEY), modes.ECB()).decryptor()
    assert responses["decrypt"] == dec.update(CHALLENGE) + dec.finalize()
    assert responses["encrypt"] != responses["decrypt"]
