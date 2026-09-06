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
    ERROR_CODES,
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
    or write-without-response from the characteristic's own properties. A
    label that only implements one of the two ignores the other silently,
    which is why this can be forced from the options flow.
    """
    _LOGGER.debug("TX %s -> %s", payload.hex(" "), UUID_COMMAND)
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


async def clear_screen(client: BleakClient, *, response: bool | None = None) -> None:
    """Unbind / clear screen, command 0xA504 (section 3.7)."""
    await send_command(client, CMD_CLEAR, response=response)


async def set_rgb(
    client: BleakClient,
    red: int,
    green: int,
    blue: int,
    on_ms: int,
    off_ms: int,
    work_ms: int,
    *,
    response: bool | None = None,
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
    await send_command(client, payload, response=response)


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


# --- diagnostics ----------------------------------------------------------

# Candidate decodings for the battery field. The document says "mv", but the
# observed values did not match, so every plausible rule is reported side by
# side and the right one can be picked from real hardware output.
BATTERY_CANDIDATES: dict[str, str] = {
    "le_mv": "little endian, millivolts (as documented)",
    "be_mv": "big endian, millivolts",
    "le_tenth_mv": "little endian, 0.1 mV steps",
    "be_tenth_mv": "big endian, 0.1 mV steps",
}


def decode_battery_candidates(raw: bytes, offset: int = 0) -> dict[str, float | None]:
    """Decode two bytes under every candidate rule, in volts."""
    if len(raw) < offset + 2:
        return dict.fromkeys(BATTERY_CANDIDATES)
    little = int(struct.unpack_from("<H", raw, offset)[0])
    big = int(struct.unpack_from(">H", raw, offset)[0])
    return {
        "le_mv": round(little / 1000, 4),
        "be_mv": round(big / 1000, 4),
        "le_tenth_mv": round(little / 10000, 4),
        "be_tenth_mv": round(big / 10000, 4),
    }


def describe_advertisement(payload: bytes) -> dict[str, object]:
    """Break an advertisement payload down field by field.

    Reports the documented layout plus the battery value at every two byte
    offset, so a layout that differs from the document can be spotted.
    """
    fields: dict[str, object] = {
        "raw": payload.hex(" "),
        "length": len(payload),
        "documented_layout_valid": len(payload) >= ADV_PAYLOAD_LEN,
    }

    if len(payload) >= ADV_PAYLOAD_LEN:
        pid, app, hw, disp, battery = struct.unpack_from("<HHHHH", payload, 0)
        fields["documented"] = {
            "pid": f"0x{pid:04X}",
            "app_version": format_version(app),
            "hw_version": format_version(hw),
            "disp_version": format_version(disp),
            "battery_raw_le": battery,
            "battery_candidates_v": decode_battery_candidates(payload, 8),
        }

    fields["battery_by_offset_v"] = {
        f"offset_{offset}": decode_battery_candidates(payload, offset)
        for offset in range(0, max(0, len(payload) - 1), 2)
    }
    return fields


async def probe_device(client: BleakClient) -> dict[str, object]:
    """Enumerate the GATT table and read every characteristic we know.

    This answers the questions the vendor document leaves open: whether our
    UUIDs exist at all, which ATT write types the label accepts, and what the
    status characteristic reports right after an unlock attempt.
    """
    report: dict[str, object] = {}

    services = []
    for service in client.services:
        characteristics = []
        for char in service.characteristics:
            characteristics.append(
                {
                    "uuid": str(char.uuid),
                    "handle": char.handle,
                    "properties": sorted(char.properties),
                }
            )
        services.append({"uuid": str(service.uuid), "characteristics": characteristics})
    report["services"] = services

    known = {
        "security": UUID_SECURITY,
        "command": UUID_COMMAND,
        "version": UUID_VERSION,
        "status": UUID_STATUS,
        "battery": UUID_BATTERY,
    }
    present = {}
    for name, uuid in known.items():
        char = client.services.get_characteristic(uuid)
        present[name] = (
            {"found": True, "properties": sorted(char.properties)}
            if char is not None
            else {"found": False}
        )
    report["known_characteristics"] = present

    # Unlock, then read everything. The status error code is the direct
    # answer to whether the unlock was accepted (5 means it was not).
    unlock: dict[str, object] = {}
    try:
        challenge = bytes(await client.read_gatt_char(UUID_SECURITY))
        unlock["challenge"] = challenge.hex(" ")
        unlock["challenge_length"] = len(challenge)
        token = _aes_ecb_encrypt(challenge.ljust(CHALLENGE_LEN, b"\0")[:CHALLENGE_LEN])
        unlock["response"] = token.hex(" ")
        await client.write_gatt_char(UUID_SECURITY, token)
        unlock["write_ok"] = True
    except Exception as err:  # noqa: BLE001 - a probe must not abort here
        unlock["error"] = f"{type(err).__name__}: {err}"
        unlock["write_ok"] = False
    report["unlock"] = unlock

    reads: dict[str, object] = {}
    for name, uuid in (
        ("version", UUID_VERSION),
        ("battery", UUID_BATTERY),
        ("status", UUID_STATUS),
    ):
        try:
            raw = bytes(await client.read_gatt_char(uuid))
            entry: dict[str, object] = {"raw": raw.hex(" "), "length": len(raw)}
            if name == "battery":
                entry["candidates_v"] = decode_battery_candidates(raw, 0)
            if name == "status" and len(raw) >= 2:
                entry["busy"] = bool(raw[0])
                entry["error_code"] = raw[1]
                entry["error_meaning"] = ERROR_CODES.get(raw[1], "undocumented")
            if name == "version" and len(raw) >= 8:
                pid, app, hw, disp = struct.unpack_from("<HHHH", raw, 0)
                entry["pid"] = f"0x{pid:04X}"
                entry["app_version"] = format_version(app)
                entry["hw_version"] = format_version(hw)
                entry["disp_version"] = format_version(disp)
            reads[name] = entry
        except Exception as err:  # noqa: BLE001 - report, do not abort
            reads[name] = {"error": f"{type(err).__name__}: {err}"}
    report["reads"] = reads

    report["mtu_size"] = getattr(client, "mtu_size", None)
    return report
