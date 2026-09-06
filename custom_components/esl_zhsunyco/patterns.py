"""Built-in test patterns for verifying the image path.

The panel's pixel format is not documented, so the first uploads are likely
to come out wrong. These patterns are designed so that a photo of the result
says *how* it is wrong rather than just that it is: orientation, colour
mapping and bit order each fail in a visually distinct way.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from PIL.Image import Image

BLACK = (0, 0, 0)
WHITE = (255, 255, 255)
RED = (255, 0, 0)
YELLOW = (255, 255, 0)

PATTERNS = (
    "diagnostic",
    "solid_black",
    "solid_white",
    "solid_red",
    "solid_yellow",
    "stripes_h",
    "stripes_v",
    "checkerboard",
    "quadrants",
)

DEFAULT_PATTERN = "diagnostic"


def _new(width: int, height: int, colour=WHITE) -> Image:
    from PIL import Image as PILImage

    return PILImage.new("RGB", (width, height), colour)


def _diagnostic(width: int, height: int) -> Image:
    """Draw a pattern that identifies which part of the encoding is wrong.

    Reading a photo of the result:

    * the 1 px frame proves the full panel is addressed, and a missing or
      doubled edge means width and height are swapped
    * the filled corner wedge is asymmetric, so mirroring and rotation are
      unambiguous
    * the four colour blocks show the palette mapping
    * the 1 px stripe blocks smear or shift when the bit order is wrong,
      because a single pixel error is visible there and nowhere else
    """
    from PIL import ImageDraw

    image = _new(width, height)
    draw = ImageDraw.Draw(image)

    draw.rectangle((0, 0, width - 1, height - 1), outline=BLACK, width=1)

    # Asymmetric corner wedge, top left only.
    wedge = max(8, min(width, height) // 8)
    draw.polygon([(1, 1), (wedge, 1), (1, wedge)], fill=BLACK)

    inner_x0, inner_x1 = 4, width - 5
    usable = height - 8
    band = max(6, usable // 8)
    y = wedge + 4

    def block(colour, label_height):
        nonlocal y
        draw.rectangle((inner_x0, y, inner_x1, y + label_height), fill=colour)
        draw.rectangle((inner_x0, y, inner_x1, y + label_height), outline=BLACK)
        y += label_height + 3

    block(BLACK, band)
    block(RED, band)
    block(YELLOW, band)
    block(WHITE, band)

    # One pixel horizontal stripes: wrong row stride turns these into blur.
    for row in range(y, min(y + band, height - 5), 2):
        draw.line((inner_x0, row, inner_x1, row), fill=BLACK)
    y += band + 3

    # One pixel vertical stripes: wrong bit order inside a byte shifts these.
    for column in range(inner_x0, inner_x1, 2):
        draw.line((column, y, column, min(y + band, height - 5)), fill=BLACK)
    y += band + 3

    if y < height - 12:
        draw.text((inner_x0 + 2, y), f"ESL {width}x{height}", fill=BLACK)

    return image


def _stripes(width: int, height: int, *, horizontal: bool, period: int = 8) -> Image:
    from PIL import ImageDraw

    image = _new(width, height)
    draw = ImageDraw.Draw(image)
    if horizontal:
        for row in range(0, height, period * 2):
            draw.rectangle((0, row, width - 1, row + period - 1), fill=BLACK)
    else:
        for column in range(0, width, period * 2):
            draw.rectangle((column, 0, column + period - 1, height - 1), fill=BLACK)
    return image


def _checkerboard(width: int, height: int, size: int = 16) -> Image:
    from PIL import ImageDraw

    image = _new(width, height)
    draw = ImageDraw.Draw(image)
    for row in range(0, height, size):
        for column in range(0, width, size):
            if (row // size + column // size) % 2 == 0:
                draw.rectangle(
                    (column, row, column + size - 1, row + size - 1), fill=BLACK
                )
    return image


def _quadrants(width: int, height: int) -> Image:
    from PIL import ImageDraw

    image = _new(width, height)
    draw = ImageDraw.Draw(image)
    mid_x, mid_y = width // 2, height // 2
    draw.rectangle((0, 0, mid_x - 1, mid_y - 1), fill=BLACK)
    draw.rectangle((mid_x, 0, width - 1, mid_y - 1), fill=RED)
    draw.rectangle((0, mid_y, mid_x - 1, height - 1), fill=YELLOW)
    draw.rectangle((mid_x, mid_y, width - 1, height - 1), fill=WHITE)
    return image


PALETTE = (BLACK, WHITE, RED, YELLOW)


def snap_to_palette(image: Image) -> Image:
    """Force every pixel onto an exact palette colour.

    Text rendering antialiases, and a test pattern must not depend on the
    dithering step: any difference on the panel should come from packing,
    not from quantisation choices made on the way there.
    """
    from PIL import Image as PILImage

    reference = PILImage.new("P", (1, 1))
    flat: list[int] = []
    for colour in PALETTE:
        flat.extend(colour)
    flat.extend([0, 0, 0] * (256 - len(PALETTE)))
    reference.putpalette(flat)

    return (
        image.convert("RGB")
        .quantize(palette=reference, dither=PILImage.Dither.NONE)
        .convert("RGB")
    )


def build(name: str, width: int, height: int) -> Image:
    """Render one of the built-in patterns at panel resolution."""
    return snap_to_palette(_build(name, width, height))


def _build(name: str, width: int, height: int) -> Image:
    if name == "diagnostic":
        return _diagnostic(width, height)
    if name == "solid_black":
        return _new(width, height, BLACK)
    if name == "solid_white":
        return _new(width, height, WHITE)
    if name == "solid_red":
        return _new(width, height, RED)
    if name == "solid_yellow":
        return _new(width, height, YELLOW)
    if name == "stripes_h":
        return _stripes(width, height, horizontal=True)
    if name == "stripes_v":
        return _stripes(width, height, horizontal=False)
    if name == "checkerboard":
        return _checkerboard(width, height)
    if name == "quadrants":
        return _quadrants(width, height)
    raise ValueError(f"unknown pattern {name!r}, expected one of {PATTERNS}")
