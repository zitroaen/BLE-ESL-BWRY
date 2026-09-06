"""Wire protocol for Zhsunyco / Wolink BLE e-ink labels.

This module deliberately contains no Home Assistant imports so the protocol
can be exercised from a plain script against a bleak client.

Reference: vendor document "BLE Display API" rev 1.5.
"""

from __future__ import annotations

import asyncio
import logging
import struct
from dataclasses import dataclass

from bleak import BleakClient
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from .const import (
    ADV_PAYLOAD_LEN,
    AES_KEY,
    CHALLENGE_LEN,
    CMD_CLEAR,
    CMD_IMAGE_REFRESH_COMP,
    CMD_IMAGE_REFRESH_RAW,
    CMD_IMAGE_STORE,
    CMD_MULTI_REFRESH,
    CMD_MULTI_STORE,
    CMD_RGB,
    MULTI_SLOT_MAX,
    MULTI_SLOT_MIN,
    UUID_BATTERY,
    UUID_COMMAND,
    UUID_SECURITY,
    UUID_STATUS,
    UUID_VERSION,
)

_LOGGER = logging.getLogger(__name__)

# Conservative default when the client does not expose an MTU.
_FALLBACK_MTU = 23
_ATT_HEADER = 3


class ESLProtocolError(Exception):
    """Raised when the label rejects or fails a protocol operation."""


@dataclass(slots=True)
class VersionInfo:
    """Section IV, read from the version characteristic."""

    pid: int
    app_version: int
    hw_version: int
    disp_version: int


@dataclass(slots=True)
class StatusInfo:
    """Section VI, read from the status characteristic."""

    busy: bool
    error: int


def _aes_ecb_encrypt(data: bytes) -> bytes:
    """Encrypt one block with AES-128-ECB using the vendor key."""
    encryptor = Cipher(algorithms.AES(AES_KEY), modes.ECB()).encryptor()
    return encryptor.update(data) + encryptor.finalize()


async def unlock(client: BleakClient) -> None:
    """Perform the challenge/response unlock described in section 2.

    The label exposes a 16 byte random number on the security characteristic
    that changes with every connection. It must be read, encrypted with
    AES-128-ECB and written back. Until that happens the label drops the
    connection as soon as any other characteristic is written.
    """
    challenge = bytes(await client.read_gatt_char(UUID_SECURITY))
    if len(challenge) != CHALLENGE_LEN:
        raise ESLProtocolError(
            f"unexpected challenge length {len(challenge)}, expected {CHALLENGE_LEN}"
        )

    token = _aes_ecb_encrypt(challenge)
    await client.write_gatt_char(UUID_SECURITY, token)
    _LOGGER.debug("Unlock response written (challenge %s)", challenge.hex())


async def send_command(
    client: BleakClient, payload: bytes, *, response: bool | None = None
) -> None:
    """Write a command to the command characteristic (section 3).

    ``response`` is left to bleak by default so it picks write-with-response
    or write-without-response from the characteristic's own properties.
    """
    if response is None:
        await client.write_gatt_char(UUID_COMMAND, payload)
    else:
        await client.write_gatt_char(UUID_COMMAND, payload, response=response)


async def read_version(client: BleakClient) -> VersionInfo:
    """Read PID, application, hardware and display version (section IV)."""
    raw = bytes(await client.read_gatt_char(UUID_VERSION))
    if len(raw) < 8:
        raise ESLProtocolError(f"version payload too short: {raw.hex()}")
    pid, app, hw, disp = struct.unpack_from("<HHHH", raw, 0)
    return VersionInfo(pid=pid, app_version=app, hw_version=hw, disp_version=disp)


async def read_battery_mv(client: BleakClient) -> int:
    """Read the battery voltage in millivolts (section V)."""
    raw = bytes(await client.read_gatt_char(UUID_BATTERY))
    if len(raw) < 2:
        raise ESLProtocolError(f"battery payload too short: {raw.hex()}")
    return int(struct.unpack_from("<H", raw, 0)[0])


async def read_status(client: BleakClient) -> StatusInfo:
    """Read the busy flag and error code (section VI)."""
    raw = bytes(await client.read_gatt_char(UUID_STATUS))
    if len(raw) < 2:
        raise ESLProtocolError(f"status payload too short: {raw.hex()}")
    return StatusInfo(busy=bool(raw[0]), error=int(raw[1]))


async def clear_screen(client: BleakClient) -> None:
    """Unbind / clear screen, command 0xA504 (section 3.7)."""
    await send_command(client, CMD_CLEAR)


async def set_rgb(
    client: BleakClient,
    red: int,
    green: int,
    blue: int,
    on_ms: int,
    off_ms: int,
    work_ms: int,
) -> None:
    """Drive the RGB LED, command 0xA508 (section 3.8).

    Layout: A5 08 | R 1B | G 1B | B 1B | on_ms 2B | off_ms 2B | work_ms 4B.

    The document does not state the byte order of the three timing fields.
    Little endian is assumed here, consistent with the 4 byte data pointer
    used by the image commands.
    """
    payload = CMD_RGB + bytes((red & 0xFF, green & 0xFF, blue & 0xFF))
    payload += struct.pack(
        "<HHI", on_ms & 0xFFFF, off_ms & 0xFFFF, work_ms & 0xFFFFFFFF
    )
    await send_command(client, payload)


def _chunk_size(client: BleakClient, header_len: int) -> int:
    """Largest data slice that still fits into a single write."""
    mtu = getattr(client, "mtu_size", None) or _FALLBACK_MTU
    return max(1, mtu - _ATT_HEADER - header_len)


async def _store_blocks(
    client: BleakClient, command: bytes, data: bytes, *, prefix: bytes = b""
) -> None:
    """Upload a payload in pointer-addressed chunks.

    Both 0xA500 (section 3.1) and 0xA503 (section 3.9) use the same shape:
    command + data pointer 4B + data. The pointer is the offset of the chunk
    within the complete payload, including any slot header in ``prefix``.
    """
    body = prefix + data
    # command (2) + pointer (4)
    size = _chunk_size(client, len(command) + 4)
    for offset in range(0, len(body), size):
        chunk = body[offset : offset + size]
        await send_command(client, command + struct.pack("<I", offset) + chunk)
        # Give the label time to write into flash between chunks.
        await asyncio.sleep(0.01)
    _LOGGER.debug("Uploaded %d bytes in %d byte chunks", len(body), size)


async def send_image(
    client: BleakClient, data: bytes, *, compressed: bool = False
) -> None:
    """Store and refresh a single full-screen image (sections 3.1 - 3.3).

    ``data`` must already be packed in the panel's native pixel format. The
    document does not specify that format, so packing lives in ``imaging.py``
    and is considered experimental.
    """
    await _store_blocks(client, CMD_IMAGE_STORE, data)
    command = CMD_IMAGE_REFRESH_COMP if compressed else CMD_IMAGE_REFRESH_RAW
    await send_command(client, command + struct.pack("<I", len(data)))


async def store_multi_image(client: BleakClient, slot: int, data: bytes) -> None:
    r"""Store a block-compressed image into a multi-screen slot (section 3.9).

    The payload is prefixed with the six byte header ``PIC0x\\0`` where x is
    the slot index. Storage is terminated by 0xA503 + picture length 4B.
    """
    if not MULTI_SLOT_MIN <= slot <= MULTI_SLOT_MAX:
        raise ESLProtocolError(
            f"slot {slot} out of range {MULTI_SLOT_MIN}..{MULTI_SLOT_MAX}"
        )
    # The document writes the header as "PIC0x\0" (6 bytes) with x in 0..10,
    # which only fits a single digit. Two digit formatting is the reading that
    # keeps the length at 6 for every slot: PIC00..PIC10.
    header = f"PIC{slot:02d}\0".encode("ascii")
    await _store_blocks(client, CMD_MULTI_STORE, data, prefix=header)
    # End of storage marker.
    total = len(header) + len(data)
    await send_command(client, CMD_MULTI_STORE + struct.pack("<I", total))


async def refresh_multi(client: BleakClient, index_a: int, index_b: int) -> None:
    """Refresh or clear the two multi-screen planes (section 3.10).

    Index -2 clears the screen, -1 leaves it untouched, n >= 0 shows the
    image stored in slot n.
    """
    payload = CMD_MULTI_REFRESH + struct.pack("<bb", index_a, index_b)
    await send_command(client, payload)


def parse_advertisement(payload: bytes) -> tuple[VersionInfo, int] | None:
    """Parse manufacturer specific data from the advertisement (section 1.2).

    Home Assistant strips the two byte company identifier, so ``payload``
    starts at document byte 2::

        PID 2B | AppVer 2B | HwVer 2B | DispVer 2B | BatVoltage_mv 2B

    Returns ``None`` when the payload is too short to be one of ours.
    """
    if len(payload) < ADV_PAYLOAD_LEN:
        return None
    pid, app, hw, disp, battery_mv = struct.unpack_from("<HHHHH", payload, 0)
    return VersionInfo(pid=pid, app_version=app, hw_version=hw, disp_version=disp), int(
        battery_mv
    )


def format_version(value: int) -> str:
    """Render a 16 bit version word as ``major.minor``."""
    return f"{value >> 8}.{value & 0xFF}"
