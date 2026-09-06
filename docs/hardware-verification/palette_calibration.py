"""
Paletten-Kalibrierung fuer das BLE-35BWRY (2 Bit pro Pixel, 4 Farben).

Legt vier gleich hohe Baender ueber das gesamte Display, jedes gefuellt mit
einem der vier moeglichen 2-Bit-Codes. Aus einem Foto des Ergebnisses laesst
sich die Zuordnung Code -> Farbe direkt ablesen.

Gemessenes Ergebnis (2026-09-06), von oben nach unten:

    Code 00 (0x00) -> Schwarz
    Code 01 (0x55) -> Weiss
    Code 10 (0xAA) -> Gelb
    Code 11 (0xFF) -> Rot

Das entspricht BWRY_PALETTE in imaging.py Index fuer Index.

Nutzung:
    python palette_calibration.py --mac 66:66:17:40:27:77
"""

import argparse
import asyncio
import sys

from bleak import BleakClient

import ble35bwry_reference as esl

# Ein Byte fasst 4 Pixel. Ein durchgehender Farbcode ergibt diese Bytewerte:
CODE_BYTES = {
    0b00: 0x00,
    0b01: 0x55,
    0b10: 0xAA,
    0b11: 0xFF,
}


def build_palette_bitmap() -> bytes:
    """Vier horizontale Baender, je ein konstanter 2-Bit-Code."""
    out = bytearray()
    band_height = esl.HEIGHT // 4
    codes = [0b00, 0b01, 0b10, 0b11]
    for y in range(esl.HEIGHT):
        code = codes[min(y // band_height, 3)]
        out.extend(bytes([CODE_BYTES[code]]) * esl.BYTES_PER_ROW)
    return bytes(out)


async def run(args: argparse.Namespace) -> None:
    bitmap = build_palette_bitmap()
    print(f"Palettenbild: {len(bitmap)} Bytes "
          f"({esl.WIDTH}x{esl.HEIGHT} @ {esl.BITS_PER_PIXEL}bpp, "
          f"{esl.BYTES_PER_ROW} Bytes/Zeile)")

    device = await esl.find_device(args.mac, args.scan_timeout, args.scan_retries)
    if device is None:
        raise RuntimeError(f"Geraet {args.mac} nicht gefunden.")

    async with BleakClient(device, timeout=40.0) as client:
        print("Verbunden.")
        await esl.unlock(client)
        print("Entsperrt.")

        busy, err = await esl.read_status(client)
        battery = await esl.read_battery_mv(client)
        print(f"Status: BUSY={busy} ERR={err} ({esl.ERR_TEXT.get(err, '?')}), "
              f"Batterie: {battery} mV")

        print("Sende ...")
        await esl.send_image_bytes(client, bitmap)
        print("Gesendet. Das Display braucht jetzt einige Sekunden.")

        try:
            await asyncio.sleep(6.0)
            busy, err = await esl.read_status(client)
            print(f"Status nachher: BUSY={busy} ERR={err} "
                  f"({esl.ERR_TEXT.get(err, '?')})")
        except Exception as err:
            # Das Label trennt waehrend des E-Paper-Refreshs regelmaessig.
            print(f"(Kein Status nach dem Refresh - Tag hat getrennt: {err})")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="BLE-35BWRY Palettenkalibrierung")
    p.add_argument("--mac", required=True)
    p.add_argument("--scan-timeout", type=float, default=45.0)
    p.add_argument("--scan-retries", type=int, default=6)
    return p.parse_args()


if __name__ == "__main__":
    try:
        asyncio.run(run(parse_args()))
    except Exception as exc:
        print(f"Fehler: {exc}", file=sys.stderr)
        sys.exit(1)
