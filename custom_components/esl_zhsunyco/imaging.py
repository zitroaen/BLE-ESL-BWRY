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
# bit code written to the panel. The order is VERIFIED: a calibration image of
# four equal bands, each filled with one constant 2 bit code, came back off a
# physical BLE-35BWRY as black, white, yellow, red top to bottom - byte values
# 0x00, 0x55, 0xAA, 0xFF. See docs/hardware-verified-findings.md section 4.
BWRY_PALETTE: list[tuple[int, int, int]] = [
    (0, 0, 0),  # 0 black
    (255, 255, 255),  # 1 white
    (255, 255, 0),  # 2 yellow
    (255, 0, 0),  # 3 red
]

# For 1 bit panels: which bit value means black.
MONO_BLACK_BIT = 0


# The panel decides the encoding. Both of these were measured on a
# BLE-35BWRY: 2 bits per pixel MSB first for four colour panels, and the
# separate bit planes some controllers use were ruled out. 1 bit panels are
# still unverified, which is what "mono" is for.
# See docs/hardware-verified-findings.md section 4.


@dataclass(slots=True)
class ImageRequest:
    """What to render, independent of how it is packed."""

    path: str | None = None
    data: bytes | None = None
    pattern: str | None = None
    pixel_format: str = "mono"
    rotate: int = 0
    mirror: bool = False
    invert: bool = False
    dither: bool = True
    stretch: bool = False
    background: tuple[int, int, int] = field(default=(255, 255, 255))
    # What to call this image in the UI. Needed because a downloaded image
    # arrives as raw bytes with no path to name it by.
    source_name: str | None = None


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


def _pack_bits(bits: list[int], width: int, height: int) -> bytes:
    """Pack single bit pixels, eight per byte, rows padded to whole bytes."""
    out = bytearray()
    for y in range(height):
        row = bits[y * width : (y + 1) * width]
        for start in range(0, width, 8):
            chunk = row[start : start + 8]
            byte = 0
            for index in range(8):
                bit = chunk[index] if index < len(chunk) else 0
                byte |= bit << (7 - index)
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


def _pack_codes(codes: list[int], width: int, height: int) -> bytes:
    """Pack two bit codes, four per byte, rows padded to whole bytes."""
    out = bytearray()
    for y in range(height):
        row = codes[y * width : (y + 1) * width]
        for start in range(0, width, 4):
            chunk = row[start : start + 4]
            byte = 0
            for index in range(4):
                code = chunk[index] if index < len(chunk) else 0
                byte |= code << (3 - index) * 2
            out.append(byte)
    return bytes(out)


def _resolve_encoding(request: ImageRequest) -> str:
    """Return the packing this panel uses; the panel decides, not the caller."""
    return "bwry_packed" if request.pixel_format == "bwry" else "mono"


def _preview_png(codes: list[int], width: int, height: int, mono: bool) -> bytes:
    """Draw what the panel will show, from the pixels that were packed.

    Built from the quantised codes rather than from the source image, so
    the preview cannot drift from what actually goes on the wire: dithering
    noise, palette snapping and letterboxing are all already baked in. If
    the preview looks wrong, the label looks wrong the same way.
    """
    from PIL import Image as PILImage

    if mono:
        palette = [(0, 0, 0), (255, 255, 255)]
        if MONO_BLACK_BIT == 1:
            palette.reverse()
    else:
        palette = BWRY_PALETTE

    image = PILImage.new("RGB", (width, height))
    image.putdata([palette[code] for code in codes])
    buffer = BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


@dataclass(slots=True)
class RenderedImage:
    """The bytes for the panel, plus a picture of what they will look like."""

    payload: bytes
    preview_png: bytes
    encoding: str
    width: int
    height: int


def render_image(request: ImageRequest, width: int, height: int) -> RenderedImage:
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
        codes = _bwry_codes(image, request.dither)
        packed = _pack_codes(codes, width, height)
    else:
        codes = _mono_bits(image, request.invert)
        packed = _pack_bits(codes, width, height)

    _LOGGER.debug(
        "Rendered %dx%d as %s: %d bytes", width, height, encoding, len(packed)
    )
    return RenderedImage(
        payload=packed,
        preview_png=_preview_png(codes, width, height, mono=encoding == "mono"),
        encoding=encoding,
        width=width,
        height=height,
    )
