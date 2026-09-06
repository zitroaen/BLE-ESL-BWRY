"""Generate the HACS/brands assets for this integration.

Kept in the repository so the icon is reproducible instead of being an
opaque binary someone has to redraw by hand. Run it from the repo root:

    python scripts/make_brand_assets.py

Both files are what HACS looks for at
custom_components/esl_zhsunyco/brand/. The drawing is deliberately plain:
the label body, and the panel showing the four colours this hardware can
actually display.
"""

from __future__ import annotations

import pathlib

from PIL import Image, ImageDraw

OUT = pathlib.Path("custom_components/esl_zhsunyco/brand")

# The panel's real palette, in its verified code order.
BLACK = (26, 26, 26)
WHITE = (245, 245, 242)
YELLOW = (240, 190, 40)
RED = (200, 60, 55)
BODY = (252, 252, 250)
EDGE = (150, 152, 155)


def _draw(size: int) -> Image.Image:
    """Draw the label at `size` x `size`, transparent around it."""
    # Supersample, then downscale: PIL has no antialiased rectangle.
    scale = 4
    px = size * scale
    image = Image.new("RGBA", (px, px), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    unit = px / 32
    # A portrait label, the shape of a real ESL.
    x0, x1 = unit * 7, unit * 25
    y0, y1 = unit * 3, unit * 29
    draw.rounded_rectangle(
        (x0, y0, x1, y1),
        radius=unit * 2.4,
        fill=BODY,
        outline=EDGE,
        width=int(unit * 0.5),
    )

    # The e-ink panel inside it.
    inset = unit * 2.2
    px0, py0, px1, py1 = x0 + inset, y0 + inset * 1.15, x1 - inset, y1 - inset * 1.15
    draw.rectangle(
        (px0, py0, px1, py1), fill=WHITE, outline=EDGE, width=int(unit * 0.3)
    )

    # Four colour bands, the palette in code order 00, 01, 10, 11.
    pad = unit * 0.9
    bx0, bx1 = px0 + pad, px1 - pad
    band_top, band_bottom = py0 + pad, py1 - pad
    height = (band_bottom - band_top) / 4
    for index, colour in enumerate((BLACK, WHITE, YELLOW, RED)):
        top = band_top + index * height
        draw.rectangle((bx0, top, bx1, top + height), fill=colour)
    draw.rectangle(
        (bx0, band_top, bx1, band_bottom), outline=EDGE, width=int(unit * 0.25)
    )

    return image.resize((size, size), Image.Resampling.LANCZOS)


def main() -> None:
    """Write both assets and report their size."""
    OUT.mkdir(parents=True, exist_ok=True)
    for name, size in (("icon.png", 256), ("logo.png", 256)):
        image = _draw(size)
        image.save(OUT / name)
        print(f"{OUT / name}: {image.size[0]}x{image.size[1]}")


if __name__ == "__main__":
    main()
