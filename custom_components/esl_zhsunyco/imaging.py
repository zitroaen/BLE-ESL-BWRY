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


@dataclass(slots=True)
class ImageRequest:
    """What to render, independent of how it is packed."""

    path: str | None = None
    data: bytes | None = None
    pixel_format: str = "mono"
    rotate: int = 0
    invert: bool = False
    dither: bool = True
    background: tuple[int, int, int] = field(default=(255, 255, 255))


def _open_source(request: ImageRequest):
    """Load the source image and flatten transparency onto the background."""
    from PIL import Image

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


def _pack_mono(image, invert: bool) -> bytes:
    """1 bit per pixel, MSB first, rows padded to whole bytes."""
    from PIL import Image

    bw = image.convert("1", dither=Image.FLOYDSTEINBERG)
    width, height = bw.size
    pixels = bw.load()

    out = bytearray()
    for y in range(height):
        acc = 0
        bits = 0
        for x in range(width):
            # PIL: 0 is black, 255 is white.
            black = pixels[x, y] == 0
            bit = MONO_BLACK_BIT if black else 1 - MONO_BLACK_BIT
            if invert:
                bit ^= 1
            acc = (acc << 1) | bit
            bits += 1
            if bits == 8:
                out.append(acc)
                acc = 0
                bits = 0
        if bits:
            out.append(acc << (8 - bits))
    return bytes(out)


def _pack_bwry(image, dither: bool) -> bytes:
    """2 bits per pixel, four pixels per byte, MSB first."""
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

    out = bytearray()
    for y in range(height):
        acc = 0
        count = 0
        for x in range(width):
            acc = (acc << 2) | (pixels[x, y] & 0x03)
            count += 1
            if count == 4:
                out.append(acc)
                acc = 0
                count = 0
        if count:
            out.append(acc << (2 * (4 - count)))
    return bytes(out)


def render_image(request: ImageRequest, width: int, height: int) -> bytes:
    """Render a request into panel-native bytes. Runs in an executor."""
    image = _open_source(request)

    if request.rotate:
        image = image.rotate(request.rotate, expand=True, fillcolor=request.background)

    image = _fit(image, width, height, request.background)

    if request.pixel_format == "bwry":
        packed = _pack_bwry(image, request.dither)
    else:
        packed = _pack_mono(image, request.invert)

    _LOGGER.debug(
        "Rendered %dx%d as %s: %d bytes",
        width,
        height,
        request.pixel_format,
        len(packed),
    )
    return packed
