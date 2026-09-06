"""Test patterns and the pixel packers."""

from __future__ import annotations

import sys
from io import BytesIO
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from conftest import load_imaging  # noqa: E402

patterns, imaging = load_imaging()

WIDTH, HEIGHT = 184, 384
PIXELS = WIDTH * HEIGHT


def test_every_pattern_renders_at_panel_size():
    """A pattern must fill the panel exactly, with no scaling needed."""
    for name in patterns.PATTERNS:
        image = patterns.build(name, WIDTH, HEIGHT)
        assert image.size == (WIDTH, HEIGHT), name


def test_patterns_use_only_exact_palette_colours():
    """Dithering must not blur a test pattern; that would mask packing bugs."""
    for name in patterns.PATTERNS:
        image = patterns.build(name, WIDTH, HEIGHT)
        colours = set(image.convert("RGB").getdata())
        assert colours <= set(patterns.PALETTE), (name, colours - set(patterns.PALETTE))


def test_unknown_pattern_is_rejected():
    """A typo must not silently produce a blank screen."""
    try:
        patterns.build("nope", WIDTH, HEIGHT)
    except ValueError:
        return
    raise AssertionError("expected ValueError")


def test_diagnostic_pattern_uses_all_four_colours():
    """The colour blocks are the point; all four must be present."""
    image = patterns.build("diagnostic", WIDTH, HEIGHT)
    assert set(image.convert("RGB").getdata()) == set(patterns.PALETTE)


def test_diagnostic_pattern_is_asymmetric():
    """The corner wedge must make mirroring and rotation unambiguous."""
    from PIL import Image

    image = patterns.build("diagnostic", WIDTH, HEIGHT)
    mirrored = image.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
    assert image.tobytes() != mirrored.tobytes()
    assert image.tobytes() != image.rotate(180).tobytes()


def _render(**kwargs) -> bytes:
    request = imaging.ImageRequest(pattern="diagnostic", stretch=True, **kwargs)
    return imaging.render_image(request, WIDTH, HEIGHT).payload



def test_solid_black_packs_to_a_constant():
    """The simplest possible check that the packer is not scrambling."""
    request = imaging.ImageRequest(
        pattern="solid_black", stretch=True, pixel_format="bwry", dither=False
    )
    data = imaging.render_image(request, WIDTH, HEIGHT).payload
    assert len(set(data)) == 1, "a solid image must pack to one repeated byte"


def test_rotation_and_mirror_change_the_output():
    """Orientation knobs must be effective for sweeping a garbled panel."""
    base = _render()
    assert _render(rotate=180) != base
    assert _render(mirror=True) != base


def test_preview_shows_exactly_what_was_packed():
    """The preview must be decodable back into the bytes that were sent.

    It claims to show the panel, so it has to come from the packed pixels
    and not from a second, independent rendering of the source. Unpacking
    the payload and re-reading the preview has to give the same picture.
    """
    from PIL import Image

    request = imaging.ImageRequest(pattern="diagnostic", pixel_format="bwry")
    request.stretch = True
    rendered = imaging.render_image(request, WIDTH, HEIGHT)

    preview = Image.open(BytesIO(rendered.preview_png)).convert("RGB")
    assert preview.size == (WIDTH, HEIGHT)

    # Unpack the payload: four 2 bit codes per byte, MSB first.
    codes = []
    for byte in rendered.payload:
        for shift in (6, 4, 2, 0):
            codes.append((byte >> shift) & 0b11)

    expected = [imaging.BWRY_PALETTE[code] for code in codes]
    assert list(preview.getdata()) == expected


def test_preview_uses_only_panel_colours():
    """A preview in colours the panel cannot show would be a lie."""
    from PIL import Image

    gradient = Image.new("RGB", (WIDTH, HEIGHT))
    gradient.putdata(
        [
            (x * 255 // WIDTH, y * 255 // HEIGHT, 128)
            for y in range(HEIGHT)
            for x in range(WIDTH)
        ]
    )
    buffer = BytesIO()
    gradient.save(buffer, format="PNG")
    rendered = imaging.render_image(
        imaging.ImageRequest(data=buffer.getvalue(), pixel_format="bwry"),
        WIDTH,
        HEIGHT,
    )
    preview = Image.open(BytesIO(rendered.preview_png)).convert("RGB")
    assert set(preview.getdata()) <= set(imaging.BWRY_PALETTE)


def test_preview_is_a_png():
    """The image entity declares image/png, so this has to be one."""
    rendered = imaging.render_image(
        imaging.ImageRequest(pattern="checkerboard", pixel_format="bwry"),
        WIDTH,
        HEIGHT,
    )
    assert rendered.preview_png.startswith(b"\x89PNG\r\n\x1a\n")
    assert rendered.encoding == "bwry_packed"


def test_mono_preview_is_black_and_white():
    """A 1 bit panel must not get a four colour preview."""
    from PIL import Image

    rendered = imaging.render_image(
        imaging.ImageRequest(pattern="checkerboard", pixel_format="mono"), 64, 32
    )
    preview = Image.open(BytesIO(rendered.preview_png)).convert("RGB")
    assert set(preview.getdata()) <= {(0, 0, 0), (255, 255, 255)}


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except Exception as exc:
                failures += 1
                print(f"FAIL {name}: {type(exc).__name__}: {exc}")
    print("---")
    print("FAILURES:", failures)
    sys.exit(1 if failures else 0)
