"""Run-length encoding used by Zhsunyco e-ink labels.

The BLE Display API document refers to a "Block Compressed Picture" for
commands 0xA502 and 0xA503 but never specifies the scheme. This is the RLE
that the easyTag firmware from the same vendor uses, reverse engineered and
documented at https://github.com/roxburghm/zhsunyco-esl.

It is therefore a well founded CANDIDATE for what the WOLINK firmware means
by block compression, not a confirmed match. The encoder is paired with a
decoder so the implementation can be verified by round trip without
hardware.

Wire format, operating on a flat row-major array of single bit pixels:

    literal        1PPPPPPP          seven pixels, MSB first
    short repeat   0CLLLLLL          7..31 pixels of colour C
    medium repeat  0C000001 LL       32..255 pixels
    long repeat    0C000000 LO HI    256..65535 pixels, little endian
"""

from __future__ import annotations

LITERAL_FLAG = 0x80
LITERAL_PIXELS = 7
SHORT_MIN = 7
SHORT_MAX = 31
MEDIUM_MAX = 255
LONG_MAX = 0xFFFF


class RLEError(ValueError):
    """Raised when a compressed stream cannot be decoded."""


def compress(pixels: list[int]) -> bytes:
    """Compress a flat list of 0/1 pixels."""
    out = bytearray()
    index = 0
    total = len(pixels)

    while index < total:
        value = pixels[index]
        run = 0
        while index + run < total and pixels[index + run] == value and run < LONG_MAX:
            run += 1

        if run < SHORT_MIN:
            # Literal always occupies seven slots; missing pixels pad with 0.
            byte = LITERAL_FLAG
            taken = min(LITERAL_PIXELS, total - index)
            for offset in range(taken):
                byte |= (pixels[index + offset] & 1) << (6 - offset)
            out.append(byte)
            index += taken
        elif run <= SHORT_MAX:
            out.append((value << 6) | run)
            index += run
        elif run <= MEDIUM_MAX:
            out.append((value << 6) | 0x01)
            out.append(run)
            index += run
        else:
            out.append((value << 6) | 0x00)
            out.append(run & 0xFF)
            out.append((run >> 8) & 0xFF)
            index += run

    return bytes(out)


def decompress(data: bytes, expected: int | None = None) -> list[int]:
    """Decode a compressed stream back into pixels.

    ``expected`` trims the literal padding of the final byte, which the
    format cannot express on its own.
    """
    pixels: list[int] = []
    index = 0
    size = len(data)

    while index < size:
        byte = data[index]
        index += 1

        if byte & LITERAL_FLAG:
            for offset in range(LITERAL_PIXELS):
                pixels.append((byte >> (6 - offset)) & 1)
            continue

        colour = (byte >> 6) & 1
        field = byte & 0x3F

        if field >= 2:
            run = field
        elif field == 1:
            if index >= size:
                raise RLEError("truncated medium repeat")
            run = data[index]
            index += 1
        else:
            if index + 1 >= size:
                raise RLEError("truncated long repeat")
            run = data[index] | (data[index + 1] << 8)
            index += 2

        pixels.extend([colour] * run)

    if expected is not None:
        if len(pixels) < expected:
            raise RLEError(f"decoded {len(pixels)} pixels, expected {expected}")
        del pixels[expected:]

    return pixels
