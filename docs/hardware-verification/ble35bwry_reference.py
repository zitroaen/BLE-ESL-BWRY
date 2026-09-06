"""
Library for the BLE-35BWRY ESL (Zhsunyco), 184x384, 4 colours (black/white/red/yellow).

Protocol (vendor PDF "BLE Display API" plus our own verification on the device):

  A single GATT service 30323032-4C53-4545-4C42-4B4E494C4F57 with 5 characteristics:
    33323032...  Security   (read/write)  - AES-128-ECB challenge/response
    31323032...  Command    (read/write)  - every command
    32323032...  Version    (read/notify)
    35323032...  Battery    (read/notify)
    34323032...  Status     (read/notify) - byte 0 BUSY, byte 1 ERR

  Image format (verified on the device):
    2 bits per pixel, MSB first, row-major, 184x384 = 17664 bytes
    Colour codes: 00=black, 01=white, 10=yellow, 11=red
"""

from __future__ import annotations

import asyncio
from typing import Iterable

from bleak import BleakClient, BleakScanner
from Crypto.Cipher import AES
from PIL import Image

# ---------------------------------------------------------------------------
# Protocol constants
# ---------------------------------------------------------------------------

UUID_SEC_CHAR = "33323032-4C53-4545-4C42-4B4E494C4F57"
UUID_CMD_CHAR = "31323032-4C53-4545-4C42-4B4E494C4F57"
UUID_VER_CHAR = "32323032-4C53-4545-4C42-4B4E494C4F57"
UUID_BAT_CHAR = "35323032-4C53-4545-4C42-4B4E494C4F57"
UUID_STATUS_CHAR = "34323032-4C53-4545-4C42-4B4E494C4F57"

AES_KEY = bytes([
    0x9B, 0x60, 0x9F, 0x28, 0xBC, 0x49, 0xE2, 0x57,
    0x29, 0xBD, 0x7B, 0x8D, 0xF2, 0x2B, 0x44, 0x20,
])

CMD_STORE_IMAGE = 0xA500      # + Pointer 4B + Data
CMD_REFRESH_RAW = 0xA501      # + Picture data size 4B
CMD_REFRESH_COMPRESSED = 0xA502
CMD_CLEAR_SCREEN = 0xA504
CMD_RGB = 0xA508              # + R G B on_ms(2B) off_ms(2B) work_ms(4B)
CMD_MULTI_REFRESH = 0xA509    # + Screen A Index 1B + Screen B Index 1B

WIDTH = 184
HEIGHT = 384
BITS_PER_PIXEL = 2
BYTES_PER_ROW = WIDTH * BITS_PER_PIXEL // 8      # 46
IMAGE_BYTES = BYTES_PER_ROW * HEIGHT             # 17664

CHUNK_SIZE = 180

# Colour codes -> reference RGB for nearest-colour matching
BLACK, WHITE, YELLOW, RED = 0b00, 0b01, 0b10, 0b11

PALETTE = {
    BLACK: (0, 0, 0),
    WHITE: (255, 255, 255),
    YELLOW: (255, 200, 40),
    RED: (190, 45, 55),
}

ERR_TEXT = {
    0: "no error",
    1: "EPD initialisation error",
    2: "EPD write error",
    3: "decompression error",
    4: "OTA error",
    5: "unlock failed",
}


# ---------------------------------------------------------------------------
# BLE
# ---------------------------------------------------------------------------

async def find_device(mac: str, scan_timeout: float = 30.0, retries: int = 4):
    """The tag advertises only sporadically, hence several scan attempts."""
    for attempt in range(1, retries + 1):
        print(f"Looking for {mac} (attempt {attempt}/{retries}, up to {scan_timeout:.0f}s) ...")
        device = await BleakScanner.find_device_by_address(mac, timeout=scan_timeout)
        if device is not None:
            print(f"  found: {device.address} ({device.name})")
            return device
    return None


async def unlock(client: BleakClient) -> None:
    """Read the 16 byte random challenge, encrypt with AES-128-ECB, write back."""
    challenge = await client.read_gatt_char(UUID_SEC_CHAR)
    if len(challenge) != 16:
        raise RuntimeError(f"Unexpected challenge length: {len(challenge)}")
    cipher = AES.new(AES_KEY, AES.MODE_ECB)
    await client.write_gatt_char(UUID_SEC_CHAR, cipher.encrypt(challenge), response=True)


async def send_cmd(client: BleakClient, payload: bytes) -> None:
    await client.write_gatt_char(UUID_CMD_CHAR, payload, response=True)


async def read_status(client: BleakClient) -> tuple[int, int]:
    data = await client.read_gatt_char(UUID_STATUS_CHAR)
    return data[0], data[1]


async def read_battery_mv(client: BleakClient) -> int:
    data = await client.read_gatt_char(UUID_BAT_CHAR)
    return int.from_bytes(data[0:2], "little")


async def read_version(client: BleakClient) -> dict:
    data = await client.read_gatt_char(UUID_VER_CHAR)
    return {
        "pid": int.from_bytes(data[0:2], "little"),
        "app_ver": int.from_bytes(data[2:4], "little"),
        "hw_ver": int.from_bytes(data[4:6], "little"),
        "disp_ver": int.from_bytes(data[6:8], "little"),
    }


async def clear_screen(client: BleakClient) -> None:
    await send_cmd(client, CMD_CLEAR_SCREEN.to_bytes(2, "little"))


async def set_rgb(client: BleakClient, r: int, g: int, b: int,
                  on_ms: int = 500, off_ms: int = 500, work_ms: int = 5000) -> None:
    payload = (
        CMD_RGB.to_bytes(2, "little")
        + bytes([r & 0xFF, g & 0xFF, b & 0xFF])
        + on_ms.to_bytes(2, "little")
        + off_ms.to_bytes(2, "little")
        + work_ms.to_bytes(4, "little")
    )
    await send_cmd(client, payload)


async def send_image_bytes(client: BleakClient, bitmap: bytes, progress: bool = True) -> None:
    """CMD 0xA500 (data in chunks) + CMD 0xA501 (refresh)."""
    if len(bitmap) != IMAGE_BYTES:
        raise ValueError(f"Bitmap must be {IMAGE_BYTES} bytes, is {len(bitmap)}")

    offset = 0
    while offset < len(bitmap):
        chunk = bitmap[offset:offset + CHUNK_SIZE]
        payload = (CMD_STORE_IMAGE.to_bytes(2, "little")
                   + offset.to_bytes(4, "little")
                   + chunk)
        await send_cmd(client, payload)
        offset += len(chunk)
        if progress and (offset % 3600 == 0 or offset == len(bitmap)):
            print(f"  ... {offset}/{len(bitmap)} Bytes")

    await send_cmd(client, CMD_REFRESH_RAW.to_bytes(2, "little")
                   + len(bitmap).to_bytes(4, "little"))


# ---------------------------------------------------------------------------
# Image encoding
# ---------------------------------------------------------------------------

def _nearest_code(r: int, g: int, b: int) -> int:
    best_code, best_dist = BLACK, None
    for code, (pr, pg, pb) in PALETTE.items():
        dist = (r - pr) ** 2 + (g - pg) ** 2 + (b - pb) ** 2
        if best_dist is None or dist < best_dist:
            best_code, best_dist = code, dist
    return best_code


def quantize(img: Image.Image, dither: bool = True) -> list[int]:
    """RGB image -> list of colour codes (0..3), row-major. Optional Floyd-Steinberg."""
    img = img.convert("RGB")
    if img.size != (WIDTH, HEIGHT):
        img = img.resize((WIDTH, HEIGHT), Image.Resampling.LANCZOS)

    px = img.load()
    codes: list[int] = []

    if not dither:
        for y in range(HEIGHT):
            for x in range(WIDTH):
                codes.append(_nearest_code(*px[x, y]))
        return codes

    for y in range(HEIGHT):
        for x in range(WIDTH):
            old_r, old_g, old_b = px[x, y]
            code = _nearest_code(old_r, old_g, old_b)
            new_r, new_g, new_b = PALETTE[code]
            px[x, y] = (new_r, new_g, new_b)

            err = (old_r - new_r, old_g - new_g, old_b - new_b)
            for nx, ny, factor in ((x + 1, y, 7 / 16), (x - 1, y + 1, 3 / 16),
                                   (x, y + 1, 5 / 16), (x + 1, y + 1, 1 / 16)):
                if 0 <= nx < WIDTH and 0 <= ny < HEIGHT:
                    cr, cg, cb = px[nx, ny]
                    px[nx, ny] = (
                        min(255, max(0, int(cr + err[0] * factor))),
                        min(255, max(0, int(cg + err[1] * factor))),
                        min(255, max(0, int(cb + err[2] * factor))),
                    )

    for y in range(HEIGHT):
        for x in range(WIDTH):
            codes.append(_nearest_code(*px[x, y]))
    return codes


def pack_codes(codes: Iterable[int]) -> bytes:
    """Colour codes (2 bit) -> packed bytes, MSB first, 4 pixels per byte."""
    out = bytearray()
    byte = 0
    count = 0
    for code in codes:
        byte = (byte << 2) | (code & 0b11)
        count += 1
        if count == 4:
            out.append(byte)
            byte = 0
            count = 0
    if count:
        byte <<= (4 - count) * 2
        out.append(byte)
    return bytes(out)


def encode_image(img: Image.Image, dither: bool = True) -> bytes:
    return pack_codes(quantize(img, dither=dither))
