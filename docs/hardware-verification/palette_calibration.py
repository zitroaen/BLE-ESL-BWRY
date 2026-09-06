"""
Palette calibration for the BLE-35BWRY (2 bits per pixel, 4 colours).

Puts four equally tall bands across the whole display, each filled with one
of the four possible 2 bit codes. A photograph of the result gives the
code -> colour mapping directly.

Measured result (2026-09-06), top to bottom:

    Code 00 (0x00) -> black
    Code 01 (0x55) -> white
    Code 10 (0xAA) -> yellow
    Code 11 (0xFF) -> red

That matches BWRY_PALETTE in imaging.py index for index.

Usage:
    python palette_calibration.py --mac 66:66:17:40:27:77
"""

import argparse
import asyncio
import sys

from bleak import BleakClient

import ble35bwry_reference as esl

# One byte holds 4 pixels. A constant colour code gives these byte values:
CODE_BYTES = {
    0b00: 0x00,
    0b01: 0x55,
    0b10: 0xAA,
    0b11: 0xFF,
}


def build_palette_bitmap() -> bytes:
    """Four horizontal bands, each a constant 2 bit code."""
    out = bytearray()
    band_height = esl.HEIGHT // 4
    codes = [0b00, 0b01, 0b10, 0b11]
    for y in range(esl.HEIGHT):
        code = codes[min(y // band_height, 3)]
        out.extend(bytes([CODE_BYTES[code]]) * esl.BYTES_PER_ROW)
    return bytes(out)


async def run(args: argparse.Namespace) -> None:
    bitmap = build_palette_bitmap()
    print(f"Palette image: {len(bitmap)} bytes "
          f"({esl.WIDTH}x{esl.HEIGHT} @ {esl.BITS_PER_PIXEL}bpp, "
          f"{esl.BYTES_PER_ROW} bytes/row)")

    device = await esl.find_device(args.mac, args.scan_timeout, args.scan_retries)
    if device is None:
        raise RuntimeError(f"Device {args.mac} not found.")

    async with BleakClient(device, timeout=40.0) as client:
        print("Connected.")
        await esl.unlock(client)
        print("Unlocked.")

        busy, err = await esl.read_status(client)
        battery = await esl.read_battery_mv(client)
        print(f"Status: BUSY={busy} ERR={err} ({esl.ERR_TEXT.get(err, '?')}), "
              f"battery: {battery} mV")

        print("Sending ...")
        await esl.send_image_bytes(client, bitmap)
        print("Sent. The display needs a few seconds now.")

        try:
            await asyncio.sleep(6.0)
            busy, err = await esl.read_status(client)
            print(f"Status afterwards: BUSY={busy} ERR={err} "
                  f"({esl.ERR_TEXT.get(err, '?')})")
        except Exception as err:
            # The label routinely disconnects during the e-paper refresh.
            print(f"(No status after the refresh - the tag disconnected: {err})")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="BLE-35BWRY palette calibration")
    p.add_argument("--mac", required=True)
    p.add_argument("--scan-timeout", type=float, default=45.0)
    p.add_argument("--scan-retries", type=int, default=6)
    return p.parse_args()


if __name__ == "__main__":
    try:
        asyncio.run(run(parse_args()))
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
