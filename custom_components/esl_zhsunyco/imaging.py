"""Image rendering for ESL panels.

EXPERIMENTAL. The vendor document specifies how image data is transported
(sections 3.1 - 3.3) but never says how pixels are encoded. Everything in
this module is therefore a documented guess that is easy to adjust once the
real format is confirmed against hardware.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from io import BytesIO

_LOGGER = logging.getLogger(__name__)

# Palette for four colour black/white/red/yellow panels. The index is the two
# bit code written to the panel; the order is unverified.
BWRY_PALETTE: list[tuple[int, int, int]] = [
    (0, 0, 0),  # 0 black
    (255, 255, 255),  # 1 white
    (255, 255, 0),  # 2 yellow
    (255, 0, 0),  # 3 red
]

# For 1 bit panels: which bit value means black.
MONO_BLACK_BIT = 0


# Encodings to try. The document never specifies the pixel format, so these
# are the plausible shapes an e-ink controller uses and each is one service
# call away rather than one release away.
ENCODINGS = ("auto", "mono", "bwry_packed", "bwry_planes")
BIT_ORDERS = ("msb", "lsb")


@dataclass(slots=True)
class ImageRequest:
    """What to render, independent of how it is packed."""

    path: str | None = None
    data: bytes | None = None
    pattern: str | None = None
    pixel_format: str = "mono"
    encoding: str = "auto"
    bit_order: str = "msb"
    rotate: int = 0
    mirror: bool = False
    invert: bool = False
    dither: bool = True
    stretch: bool = False
    background: tuple[int, int, int] = field(default=(255, 255, 255))


def _open_source(request: ImageRequest, width: int, height: int):
    """Load the source image and flatten transparency onto the background."""
    from PIL import Image

    if request.pattern is not None:
        from . import patterns

        return patterns.build(request.pattern, width, height)

    if request.data is not None:
        source = Image.open(BytesIO(request.data))
    elif request.path:
        source = Image.open(request.path)
    else:
        raise ValueError("image request needs either a path or raw data")

    source = source.convert("RGBA")
    flat = Image.new("RGB", source.size, request.background)
    flat.paste(source, mask=source.split()[3])
    return flat


def _fit(image, width: int, height: int, background: tuple[int, int, int]):
    """Scale to fit inside the panel, centred, without distorting."""
    from PIL import Image

    if image.size == (width, height):
        return image

    scale = min(width / image.width, height / image.height)
    new_size = (max(1, round(image.width * scale)), max(1, round(image.height * scale)))
    resized = image.resize(new_size, Image.LANCZOS)

    canvas = Image.new("RGB", (width, height), background)
    canvas.paste(resized, ((width - new_size[0]) // 2, (height - new_size[1]) // 2))
    return canvas


def _pack_bits(bits: list[int], width: int, height: int, bit_order: str) -> bytes:
    """Pack single bit pixels, eight per byte, rows padded to whole bytes."""
    out = bytearray()
    msb = bit_order == "msb"
    for y in range(height):
        row = bits[y * width : (y + 1) * width]
        for start in range(0, width, 8):
            chunk = row[start : start + 8]
            byte = 0
            for index in range(8):
                bit = chunk[index] if index < len(chunk) else 0
                byte |= bit << (7 - index) if msb else bit << index
            out.append(byte)
    return bytes(out)


def _mono_bits(image, invert: bool) -> list[int]:
    """Reduce to one bit per pixel."""
    from PIL import Image

    bw = image.convert("1", dither=Image.FLOYDSTEINBERG)
    width, height = bw.size
    pixels = bw.load()

    bits: list[int] = []
    for y in range(height):
        for x in range(width):
            # PIL: 0 is black, 255 is white.
            black = pixels[x, y] == 0
            bit = MONO_BLACK_BIT if black else 1 - MONO_BLACK_BIT
            bits.append(bit ^ 1 if invert else bit)
    return bits


def _pack_mono(image, invert: bool, bit_order: str = "msb") -> bytes:
    """1 bit per pixel, rows padded to whole bytes."""
    width, height = image.size
    return _pack_bits(_mono_bits(image, invert), width, height, bit_order)


def _bwry_codes(image, dither: bool) -> list[int]:
    """Quantise to the four colour palette, returning 0..3 per pixel."""
    from PIL import Image

    palette_image = Image.new("P", (1, 1))
    flat: list[int] = []
    for colour in BWRY_PALETTE:
        flat.extend(colour)
    flat.extend([0, 0, 0] * (256 - len(BWRY_PALETTE)))
    palette_image.putpalette(flat)

    quantised = image.quantize(
        palette=palette_image,
        dither=Image.FLOYDSTEINBERG if dither else Image.NONE,
    )
    width, height = quantised.size
    pixels = quantised.load()
    return [pixels[x, y] & 0x03 for y in range(height) for x in range(width)]


def _pack_bwry(image, dither: bool, bit_order: str = "msb") -> bytes:
    """2 bits per pixel, four pixels per byte, rows padded to whole bytes."""
    width, height = image.size
    codes = _bwry_codes(image, dither)
    msb = bit_order == "msb"

    out = bytearray()
    for y in range(height):
        row = codes[y * width : (y + 1) * width]
        for start in range(0, width, 4):
            chunk = row[start : start + 4]
            byte = 0
            for index in range(4):
                code = chunk[index] if index < len(chunk) else 0
                shift = (3 - index) * 2 if msb else index * 2
                byte |= code << shift
            out.append(byte)
    return bytes(out)


def _pack_bwry_planes(image, dither: bool, bit_order: str = "msb") -> bytes:
    """Pack the four colours as two separate 1 bit planes, high plane first.

    E-ink controllers commonly address one plane at a time rather than
    interleaving the bits, and the easyTag firmware from the same vendor does
    exactly that. Same information as bwry_packed, different layout.
    """
    width, height = image.size
    codes = _bwry_codes(image, dither)
    high = [(code >> 1) & 1 for code in codes]
    low = [code & 1 for code in codes]
    return _pack_bits(high, width, height, bit_order) + _pack_bits(
        low, width, height, bit_order
    )


def _resolve_encoding(request: ImageRequest) -> str:
    """Turn 'auto' into the encoding implied by the panel."""
    if request.encoding != "auto":
        return request.encoding
    return "bwry_packed" if request.pixel_format == "bwry" else "mono"


def render_image(request: ImageRequest, width: int, height: int) -> bytes:
    """Render a request into panel-native bytes. Runs in an executor."""
    from PIL import Image as PILImage

    image = _open_source(request, width, height)

    if request.rotate:
        image = image.rotate(request.rotate, expand=True, fillcolor=request.background)
    if request.mirror:
        image = image.transpose(PILImage.Transpose.FLIP_LEFT_RIGHT)

    if request.stretch:
        image = image.convert("RGB").resize((width, height), PILImage.LANCZOS)
    else:
        image = _fit(image, width, height, request.background)

    encoding = _resolve_encoding(request)
    if encoding == "bwry_packed":
        packed = _pack_bwry(image, request.dither, request.bit_order)
    elif encoding == "bwry_planes":
        packed = _pack_bwry_planes(image, request.dither, request.bit_order)
    else:
        packed = _pack_mono(image, request.invert, request.bit_order)

    _LOGGER.debug(
        "Rendered %dx%d as %s/%s: %d bytes",
        width,
        height,
        encoding,
        request.bit_order,
        len(packed),
    )
    return packed
