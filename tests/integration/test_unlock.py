"""The AES unlock, and the status bits that report whether it landed."""

from __future__ import annotations

from unittest.mock import patch

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from custom_components.esl_zhsunyco.const import AES_KEY, UUID_SECURITY, UUID_STATUS
from custom_components.esl_zhsunyco.protocol import unlock

from .conftest import attach_services

CHALLENGE = bytes(range(16))


def _encrypted(challenge: bytes = CHALLENGE) -> bytes:
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    encryptor = Cipher(algorithms.AES(AES_KEY), modes.ECB()).encryptor()
    return encryptor.update(challenge) + encryptor.finalize()


class FakeLabel:
    """A label that unlocks only for AES-ECB(challenge) with the vendor key.

    Mirrors the documented behaviour: a locked label accepts the unlock write,
    then drops the link on the next write to any other characteristic.
    """

    def __init__(self, accepts: bool = True) -> None:
        self.is_connected = True
        self.accepts = accepts
        self.unlocked = False
        self.busy = False
        self.commands: list[bytes] = []
        self.security_writes: list[bytes] = []
        attach_services(self)

    def reconnect(self) -> None:
        self.is_connected = True
        self.unlocked = False

    async def disconnect(self):
        self.is_connected = False

    async def start_notify(self, uuid, cb):
        return None

    async def read_gatt_char(self, uuid):
        if uuid == UUID_SECURITY:
            return CHALLENGE
        if uuid == UUID_STATUS:
            busy, self.busy = self.busy, False
            # Measured on hardware: bit 0 is BUSY, bits 1|2 mean still locked.
            state = (0x01 if busy else 0x00) | (0x00 if self.unlocked else 0x06)
            return bytes((state, 0)) + bytes(30)
        raise AssertionError(f"unexpected read of {uuid}")

    async def write_gatt_char(self, uuid, data, response=None):
        payload = bytes(data)
        if uuid == UUID_SECURITY:
            self.security_writes.append(payload)
            self.unlocked = self.accepts and payload == _encrypted()
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


async def test_unlock_encrypts_the_challenge_with_the_vendor_key() -> None:
    """Verified twice on hardware; nothing else is sent any more.

    Five alternatives used to be tried in turn. They are gone: a wrong one
    does not merely fail, it makes the label revoke authorisation.
    """
    label = FakeLabel()
    await unlock(label)

    assert label.security_writes == [_encrypted()]
    assert label.unlocked is True
    # Not the plaintext, and not the inverse operation.
    assert label.security_writes[0] != CHALLENGE

    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    decryptor = Cipher(algorithms.AES(AES_KEY), modes.ECB()).decryptor()
    decrypted = decryptor.update(CHALLENGE) + decryptor.finalize()
    assert label.security_writes[0] != decrypted


async def test_unlock_rejects_a_challenge_of_the_wrong_length() -> None:
    """A short read is a protocol error, not something to encrypt anyway."""
    from custom_components.esl_zhsunyco.protocol import ESLProtocolError

    class ShortChallenge(FakeLabel):
        async def read_gatt_char(self, uuid):
            if uuid == UUID_SECURITY:
                return bytes(8)
            return await super().read_gatt_char(uuid)

    label = ShortChallenge()
    try:
        await unlock(label)
    except ESLProtocolError as err:
        assert "8" in str(err)
    else:
        raise AssertionError("a short challenge must not be accepted")
    assert label.security_writes == []


async def test_a_rejected_unlock_is_noticed_before_any_command(
    hass: HomeAssistant, config_entry, mock_bluetooth
) -> None:
    """The status byte answers this directly, so a command need not be risked."""
    device = await _setup(hass, config_entry)
    label = FakeLabel(accepts=False)

    async def fake_wait(_self, wait=180):
        return object()

    async def fake_establish(*args, **kwargs):
        label.reconnect()
        return label

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
    ):
        async with device.connection():
            pass

    assert device.state.unlock_verified is False
    assert device.state.last_unlock_status["looks_locked"] is True
    # No command was sent to find this out.
    assert label.commands == []


async def test_an_accepted_unlock_is_recorded_as_verified(
    hass: HomeAssistant, config_entry, mock_bluetooth
) -> None:
    """The counterpart, so the check cannot pass by always reporting failure."""
    device = await _setup(hass, config_entry)
    label = FakeLabel(accepts=True)

    async def fake_wait(_self, wait=180):
        return object()

    async def fake_establish(*args, **kwargs):
        label.reconnect()
        return label

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
    ):
        async with device.connection():
            pass

    assert device.state.unlock_verified is True
    assert device.state.last_unlock_status["looks_locked"] is False


async def test_locked_status_byte_is_not_reported_as_busy(
    hass: HomeAssistant, config_entry, mock_bluetooth
) -> None:
    """0x06 means locked, not busy; reading the whole byte as a flag lied."""
    from custom_components.esl_zhsunyco.device import _read_status

    class Status:
        def __init__(self, state):
            self.state = state

        async def read_gatt_char(self, uuid):
            return bytes((self.state, 0)) + bytes(30)

    locked = await _read_status(Status(0x06))
    assert locked["busy"] is False
    assert locked["looks_locked"] is True

    unlocked = await _read_status(Status(0x00))
    assert unlocked["busy"] is False
    assert unlocked["looks_locked"] is False

    working = await _read_status(Status(0x01))
    assert working["busy"] is True
    assert working["looks_locked"] is False
