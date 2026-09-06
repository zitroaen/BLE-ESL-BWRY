"""Protocol tests checking the wire bytes against the vendor document."""

from __future__ import annotations

import asyncio
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from conftest import load_modules  # noqa: E402

const, protocol, imaging = load_modules()


class FakeClient:
    """Records every GATT interaction so tests can assert on the bytes."""

    def __init__(self, reads: dict[str, bytes] | None = None, mtu: int = 23) -> None:
        self.reads = reads or {}
        self.writes: list[tuple[str, bytes, bool]] = []
        self.mtu_size = mtu

    async def read_gatt_char(self, uuid: str) -> bytes:
        return self.reads[uuid]

    async def write_gatt_char(
        self, uuid: str, data: bytes, response: bool | None = None
    ):
        self.writes.append((uuid, bytes(data), response))


def run(coro):
    """Run a coroutine to completion."""
    return asyncio.run(coro)


# --- section 2: security -------------------------------------------------


def test_unlock_is_challenge_response():
    """The label's random challenge must be read, encrypted and written back."""
    challenge = bytes(range(16))
    client = FakeClient({const.UUID_SECURITY: challenge})

    run(protocol.unlock(client))

    assert len(client.writes) == 1
    uuid, payload, _ = client.writes[0]
    assert uuid == const.UUID_SECURITY

    # Independently derived expectation via a second AES implementation path.
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    enc = Cipher(algorithms.AES(const.AES_KEY), modes.ECB()).encryptor()
    assert payload == enc.update(challenge) + enc.finalize()
    # A constant payload would mean the challenge was ignored.
    assert payload != protocol._aes_ecb_encrypt(b"\x00" * 16)


def test_unlock_rejects_short_challenge():
    """A truncated challenge must not be silently padded."""
    client = FakeClient({const.UUID_SECURITY: b"\x01\x02"})
    try:
        run(protocol.unlock(client))
    except protocol.ESLProtocolError:
        return
    raise AssertionError("expected ESLProtocolError")


# --- section 3.8: RGB ----------------------------------------------------


def test_set_rgb_layout():
    """A5 08 | R | G | B | on 2B | off 2B | work 4B, 13 bytes total."""
    client = FakeClient()
    run(protocol.set_rgb(client, 0x11, 0x22, 0x33, 500, 250, 30000))

    uuid, payload, _ = client.writes[0]
    assert uuid == const.UUID_COMMAND
    assert len(payload) == 13
    assert payload[:2] == b"\xa5\x08"
    assert payload[2:5] == b"\x11\x22\x33"
    assert struct.unpack("<HHI", payload[5:]) == (500, 250, 30000)


# --- section 3.7: clear --------------------------------------------------


def test_clear_screen_has_no_payload():
    """0xA504 carries no data; a trailing byte is not part of the command."""
    client = FakeClient()
    run(protocol.clear_screen(client))
    assert client.writes[0][1] == b"\xa5\x04"


# --- sections 3.1 - 3.2: image upload ------------------------------------


def test_send_image_chunks_and_pointers():
    """Chunks must tile the payload and carry their own offset."""
    data = bytes(range(256)) * 2  # 512 bytes
    client = FakeClient(mtu=100)
    run(protocol.send_image(client, data))

    stores = [w for w in client.writes if w[1][:2] == b"\xa5\x00"]
    refresh = [w for w in client.writes if w[1][:2] == b"\xa5\x01"]

    assert len(refresh) == 1
    assert struct.unpack("<I", refresh[0][1][2:]) == (len(data),)

    rebuilt = bytearray(len(data))
    seen = 0
    for _, payload, _ in stores:
        offset = struct.unpack_from("<I", payload, 2)[0]
        chunk = payload[6:]
        # Every chunk must fit a single ATT write.
        assert len(payload) <= client.mtu_size - 3
        rebuilt[offset : offset + len(chunk)] = chunk
        seen += len(chunk)

    assert seen == len(data)
    assert bytes(rebuilt) == data


def test_send_image_compressed_uses_a502():
    """Block compressed payloads refresh via 0xA502."""
    client = FakeClient(mtu=100)
    run(protocol.send_image(client, b"\x00" * 32, compressed=True))
    assert any(w[1][:2] == b"\xa5\x02" for w in client.writes)


# --- section 3.9 / 3.10: multi screen ------------------------------------


def test_multi_store_header_and_terminator():
    r"""Slot uploads are prefixed with PIC0x\0 and closed by a length write."""
    client = FakeClient(mtu=200)
    run(protocol.store_multi_image(client, 3, b"\xaa" * 20))

    first = client.writes[0][1]
    assert first[:2] == b"\xa5\x03"
    assert struct.unpack_from("<I", first, 2)[0] == 0
    assert first[6:12] == b"PIC03\x00"

    last = client.writes[-1][1]
    assert last[:2] == b"\xa5\x03"
    assert struct.unpack("<I", last[2:]) == (26,)  # 6 byte header + 20 byte data


def test_multi_refresh_signed_indices():
    """-2 clears, -1 leaves untouched; both must survive as signed bytes."""
    client = FakeClient()
    run(
        protocol.refresh_multi(
            client, const.MULTI_INDEX_CLEAR, const.MULTI_INDEX_NO_REFRESH
        )
    )
    payload = client.writes[0][1]
    assert payload == b"\xa5\x09\xfe\xff"


# --- sections IV, V, VI: read characteristics ----------------------------


def test_read_version():
    """PID, AppVer, HwVer, DispVer are four little endian words."""
    raw = struct.pack("<HHHH", 0x1234, 0x0102, 0x0203, 0x0304)
    client = FakeClient({const.UUID_VERSION: raw})
    version = run(protocol.read_version(client))
    assert (version.pid, version.app_version) == (0x1234, 0x0102)
    assert (version.hw_version, version.disp_version) == (0x0203, 0x0304)


def test_read_battery_uses_dedicated_characteristic():
    """Battery voltage comes from its own characteristic, not the command one."""
    client = FakeClient({const.UUID_BATTERY: struct.pack("<H", 3021)})
    assert run(protocol.read_battery_mv(client)) == 3021


def test_read_status_busy_and_error():
    """Byte 0 is BUSY, byte 1 is the error code."""
    client = FakeClient({const.UUID_STATUS: bytes((1, 5))})
    status = run(protocol.read_status(client))
    assert status.busy is True
    assert const.ERROR_CODES[status.error] == "unlock_failed"


# --- section 1.2: advertisement ------------------------------------------


def test_parse_advertisement():
    """Manufacturer data after the company id: PID, versions, battery."""
    payload = struct.pack(">HHHHH", 0xAB01, 0x0100, 0x0200, 0x0300, 2950)
    parsed = protocol.parse_advertisement(payload)
    assert parsed is not None
    version, battery_mv = parsed
    assert version.pid == 0xAB01
    assert battery_mv == 2950


def test_parse_advertisement_ignores_short_payload():
    """Foreign advertisements must not be misparsed."""
    assert protocol.parse_advertisement(b"\x01\x02\x03") is None


# --- imaging -------------------------------------------------------------


def test_pack_sizes():
    """Mono packs 8 pixels per byte, BWRY packs 4."""
    from PIL import Image

    image = Image.new("RGB", (16, 4), (255, 255, 255))
    assert len(imaging._pack_mono(image, invert=False)) == 16 // 8 * 4
    assert len(imaging._pack_bwry(image, dither=False)) == 16 // 4 * 4


def test_render_fits_panel():
    """A source of any size renders to the panel's byte count."""
    from PIL import Image

    path = Path("/tmp/esl_test_source.png")
    Image.new("RGB", (50, 90), (255, 0, 0)).save(path)

    request = imaging.ImageRequest(path=str(path), pixel_format="bwry")
    data = imaging.render_image(request, 184, 384)
    assert len(data) == (184 // 4) * 384


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except Exception as exc:
                failures += 1
                print(f"FAIL {name}: {type(exc).__name__}: {exc}")
    print("---")
    print("FAILURES:", failures)
    sys.exit(1 if failures else 0)
