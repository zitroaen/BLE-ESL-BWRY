"""Test patterns and the pixel packers."""

from __future__ import annotations

import sys
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
    return imaging.render_image(request, WIDTH, HEIGHT)


def test_encoding_byte_counts():
    """Each encoding must produce exactly the bytes the panel needs."""
    assert len(_render(encoding="mono")) == PIXELS // 8
    assert len(_render(encoding="bwry_packed")) == PIXELS // 4
    # Two 1 bit planes carry the same information as 2 bits per pixel.
    assert len(_render(encoding="bwry_planes")) == 2 * (PIXELS // 8)


def test_bit_order_actually_changes_the_bytes():
    """The knob has to do something, otherwise sweeping it proves nothing."""
    for encoding in ("mono", "bwry_packed", "bwry_planes"):
        msb = _render(encoding=encoding, bit_order="msb")
        lsb = _render(encoding=encoding, bit_order="lsb")
        assert len(msb) == len(lsb), encoding
        assert msb != lsb, f"{encoding}: bit_order had no effect"


def test_mono_bit_order_is_a_per_byte_reversal():
    """MSB and LSB packing must differ exactly by reversing bits in a byte."""
    solid = imaging.ImageRequest(pattern="stripes_v", stretch=True, encoding="mono")
    solid.bit_order = "msb"
    msb = imaging.render_image(solid, 16, 2)
    solid.bit_order = "lsb"
    lsb = imaging.render_image(solid, 16, 2)

    def reverse(byte: int) -> int:
        return int(f"{byte:08b}"[::-1], 2)

    assert bytes(reverse(b) for b in msb) == lsb


def test_planes_are_the_two_bitplanes_of_the_packed_form():
    """bwry_planes must carry the same pixels, only laid out differently."""
    packed = _render(encoding="bwry_packed", bit_order="msb")
    planes = _render(encoding="bwry_planes", bit_order="msb")
    assert len(packed) == len(planes)

    half = len(planes) // 2
    high_plane, low_plane = planes[:half], planes[half:]

    # Rebuild the codes from the packed form and from the planes, compare.
    def codes_from_packed(data: bytes) -> list[int]:
        out = []
        for byte in data:
            for shift in (6, 4, 2, 0):
                out.append((byte >> shift) & 0x03)
        return out

    def codes_from_planes(high: bytes, low: bytes) -> list[int]:
        out = []
        for hi, lo in zip(high, low, strict=True):
            for shift in (7, 6, 5, 4, 3, 2, 1, 0):
                out.append((((hi >> shift) & 1) << 1) | ((lo >> shift) & 1))
        return out

    # Rows are byte aligned in both forms for this width, so they line up.
    assert codes_from_packed(packed) == codes_from_planes(high_plane, low_plane)


def test_solid_black_packs_to_a_constant():
    """The simplest possible check that the packer is not scrambling."""
    request = imaging.ImageRequest(
        pattern="solid_black", stretch=True, encoding="bwry_packed", dither=False
    )
    data = imaging.render_image(request, WIDTH, HEIGHT)
    assert len(set(data)) == 1, "a solid image must pack to one repeated byte"


def test_rotation_and_mirror_change_the_output():
    """Orientation knobs must be effective for sweeping a garbled panel."""
    base = _render()
    assert _render(rotate=180) != base
    assert _render(mirror=True) != base


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
