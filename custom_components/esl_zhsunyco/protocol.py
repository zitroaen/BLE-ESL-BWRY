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
    CMD_RGB,
    EASYTAG_NOTIFY,
    EASYTAG_SERVICE,
    EASYTAG_WRITE,
    ERROR_CODES,
    PROTOCOL_EASYTAG,
    PROTOCOL_UNKNOWN,
    PROTOCOL_WOLINK,
    UUID_BATTERY,
    UUID_COMMAND,
    UUID_SECURITY,
    UUID_STATUS,
    UUID_VERSION,
)

_LOGGER = logging.getLogger(__name__)

_ATT_HEADER = 3

# Data bytes per write. Measured on a physical BLE-35BWRY: 180 byte slices
# (186 byte frames) with response=True carried three full 17664 byte images
# without a single failed write. See docs/hardware-verified-findings.md
# section 7.4.
#
# This is both the cap and the assumption when the client reports no MTU.
# The previous fallback of 23 - the ATT minimum - left 14 usable bytes and
# turned one image into ~1262 writes, which over a proxy takes minutes and
# fails long before it finishes. A client that genuinely negotiated a small
# MTU still reports it, and the min() below respects that.
VERIFIED_CHUNK_BYTES = 180

# Timing for bulk uploads. These are the values the easyTag driver from the
# same vendor needed to avoid overrunning the label's buffer
# (https://github.com/roxburghm/zhsunyco-esl); the BLE Display API document
# gives no guidance at all, so the conservative numbers are used here too.
CHUNK_DELAY_S = 0.020
CHUNK_DELAY_EVERY_5TH_S = 0.003
PRE_DATA_DELAY_S = 0.5
PRE_REFRESH_DELAY_S = 0.5


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


# The unlock is settled. The document's wording ("using the ECB mode AES128
# encryption") is a translation, so five alternatives used to be kept around
# in case the firmware meant the inverse operation or another byte order.
# Two independent hardware runs killed them off: a sweep over an ESPHome
# proxy in which only "encrypt" left the status byte unlocked and survived a
# command write, and a direct-adapter run that read ERR=0 after the unlock
# and then pushed 98 consecutive command writes without a drop.
# See docs/hardware-verified-findings.md section 2.
DEFAULT_UNLOCK_VARIANT = "encrypt"


async def read_challenge(client: BleakClient) -> bytes:
    """Read the 16 byte random number the unlock is computed from."""
    return bytes(await client.read_gatt_char(UUID_SECURITY))


async def unlock(client: BleakClient) -> None:
    """Perform the challenge/response unlock described in section 2.

    The label exposes a 16 byte random number on the security characteristic
    that changes with every connection. It must be read, encrypted with
    AES-128-ECB and written back. Until that happens the label drops the
    connection as soon as any other characteristic is written.
    """
    challenge = await read_challenge(client)
    if len(challenge) != CHALLENGE_LEN:
        raise ESLProtocolError(
            f"unexpected challenge length {len(challenge)}, expected {CHALLENGE_LEN}"
        )

    token = _aes_ecb_encrypt(challenge)
    await client.write_gatt_char(UUID_SECURITY, token)
    _LOGGER.debug(
        "Unlock written: challenge %s -> %s", challenge.hex(" "), token.hex(" ")
    )


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

    Layout: 08 A5 | R 1B | G 1B | B 1B | on_ms 2B | off_ms 2B | work_ms 4B.

    The document does not state the byte order of the three timing fields.
    Little endian, consistent with the opcode and the 4 byte data pointer:
    verified on a BLE-35BWRY through Home Assistant, the LED lit on this
    exact 13 byte frame.
    """
    payload = CMD_RGB + bytes((red & 0xFF, green & 0xFF, blue & 0xFF))
    payload += struct.pack(
        "<HHI", on_ms & 0xFFFF, off_ms & 0xFFFF, work_ms & 0xFFFFFFFF
    )
    await send_command(client, payload, response=response)


def _chunk_size(client: BleakClient, header_len: int) -> int:
    """Largest data slice that still fits into a single write.

    Capped at the slice size that is known to work rather than at whatever
    the negotiated MTU would allow: a larger frame has never been tried on
    this hardware, and a bulk upload is the worst place to find out.
    """
    mtu = getattr(client, "mtu_size", None) or 0
    room = mtu - _ATT_HEADER - header_len
    if room <= 0:
        # No usable MTU reported; go with the slice size that was measured.
        return VERIFIED_CHUNK_BYTES
    return max(1, min(room, VERIFIED_CHUNK_BYTES))


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

    # Let the label settle after the unlock before the first bulk write.
    await asyncio.sleep(PRE_DATA_DELAY_S)

    for number, offset in enumerate(range(0, len(body), size), start=1):
        chunk = body[offset : offset + size]
        await send_command(client, command + struct.pack("<I", offset) + chunk)
        # Give the label time to write into flash between chunks.
        await asyncio.sleep(CHUNK_DELAY_S)
        if number % 5 == 0:
            await asyncio.sleep(CHUNK_DELAY_EVERY_5TH_S)

    _LOGGER.debug("Uploaded %d bytes in %d byte chunks", len(body), size)


async def send_image(
    client: BleakClient, data: bytes, *, compressed: bool = False
) -> None:
    """Store and refresh a single full-screen image (sections 3.1 - 3.3).

    ``data`` must already be packed in the panel's native pixel format. The
    document does not specify that format; for BWRY panels ``imaging.py``
    has been verified against hardware, for 1 bit panels it has not.
    """
    await _store_blocks(client, CMD_IMAGE_STORE, data)
    await asyncio.sleep(PRE_REFRESH_DELAY_S)
    command = CMD_IMAGE_REFRESH_COMP if compressed else CMD_IMAGE_REFRESH_RAW
    await send_command(client, command + struct.pack("<I", len(data)))


def parse_advertisement(payload: bytes) -> tuple[VersionInfo, int] | None:
    """Parse manufacturer specific data from the advertisement (section 1.2).

    Home Assistant strips the two byte company identifier, so ``payload``
    starts at document byte 2::

        PID 2B | AppVer 2B | HwVer 2B | DispVer 2B | BatVoltage_mv 2B

    The byte order is MIXED, which the document does not mention. A full
    probe captured all three sources at once::

        advertisement:           30 00 00 0e 03 30 02 01 0b 99
        version characteristic:  30 00 00 0e 03 30 02 01
        battery characteristic:  99 0b            (little endian, 2969 mV)

    The first eight bytes are byte identical to the version characteristic,
    so the version fields must be decoded the same way in both: little
    endian. The last two are the byte reverse of the battery
    characteristic, so the battery alone is big endian here. Decoding the
    whole payload one way or the other is wrong either way.

    Returns ``None`` when the payload is too short to be one of ours.
    """
    if len(payload) < ADV_PAYLOAD_LEN:
        return None
    pid, app, hw, disp = struct.unpack_from("<HHHH", payload, 0)
    (battery_mv,) = struct.unpack_from(">H", payload, 8)
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
        pid, app, hw, disp = struct.unpack_from("<HHHH", payload, 0)
        (battery,) = struct.unpack_from(">H", payload, 8)
        fields["parsed"] = {
            "byte_order": (
                "version fields little endian (byte identical to the version "
                "characteristic), battery big endian (byte reverse of the "
                "battery characteristic)"
            ),
            "version_bytes": payload[:8].hex(" "),
            "pid": f"0x{pid:04X}",
            "app_version": format_version(app),
            "hw_version": format_version(hw),
            "disp_version": format_version(disp),
            "battery_mv": battery,
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

    # Which protocol family is this actually? Labels from the same vendor
    # ship two incompatible stacks, and pointing this driver at the other one
    # produces exactly the symptoms of a broken command encoding.
    report["protocol_family"] = detect_protocol_family(client)

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
        # Readable per the GATT table, and never looked at so far. If the
        # firmware echoes the last frame or a template here, that settles the
        # command encoding outright.
        ("command", UUID_COMMAND),
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


def detect_protocol_family(client: BleakClient) -> dict[str, object]:
    """Work out which of the two vendor stacks this label actually speaks.

    Both are sold as "Zhsunyco". This integration implements the WOLINK stack
    from the BLE Display API document (AES unlock, 0xA5xx commands). The other
    is the easyTag stack (Nordic style UUIDs, XOR keyed off the MAC, framed
    20/204 byte packets), which needs a completely different driver.
    """

    def present(uuid: str) -> bool:
        return client.services.get_characteristic(uuid) is not None

    wolink = present(UUID_SECURITY) and present(UUID_COMMAND)
    easytag = present(EASYTAG_WRITE) and present(EASYTAG_NOTIFY)

    if wolink:
        family = PROTOCOL_WOLINK
    elif easytag:
        family = PROTOCOL_EASYTAG
    else:
        family = PROTOCOL_UNKNOWN

    return {
        "detected": family,
        "supported_by_this_integration": family == PROTOCOL_WOLINK,
        "wolink_characteristics_present": wolink,
        "easytag_characteristics_present": easytag,
        "easytag_service_uuid": EASYTAG_SERVICE,
        "note": (
            "easytag_xor needs a different driver, see "
            "https://github.com/roxburghm/zhsunyco-esl"
        )
        if family == PROTOCOL_EASYTAG
        else "",
    }
