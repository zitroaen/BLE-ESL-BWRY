"""Round trip verification of the candidate block compression."""

from __future__ import annotations

import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from conftest import load_rle  # noqa: E402

rle = load_rle()


def _roundtrip(pixels):
    data = rle.compress(pixels)
    return rle.decompress(data, expected=len(pixels)), data


def test_all_zero_uses_long_repeat():
    """A full white plane must collapse to three bytes per 65535 pixels."""
    pixels = [0] * 1000
    decoded, data = _roundtrip(pixels)
    assert decoded == pixels
    assert data == bytes((0x00, 1000 & 0xFF, 1000 >> 8))


def test_short_repeat_boundaries():
    """Runs of 7 and 31 must both use the single byte form."""
    for run in (7, 31):
        pixels = [1] * run
        decoded, data = _roundtrip(pixels)
        assert decoded == pixels
        assert len(data) == 1
        assert data[0] == (1 << 6) | run


def test_medium_repeat_boundaries():
    """Runs of 32 and 255 must use the two byte form."""
    for run in (32, 255):
        pixels = [0] * run
        decoded, data = _roundtrip(pixels)
        assert decoded == pixels
        assert len(data) == 2
        assert data[0] == 0x01
        assert data[1] == run


def test_long_repeat_is_little_endian():
    """The length of a long repeat is low byte first."""
    pixels = [1] * 300
    _, data = _roundtrip(pixels)
    assert len(data) == 3
    assert data[0] == 0x40
    assert data[1] == 300 & 0xFF
    assert data[2] == 300 >> 8


def test_literal_packs_seven_pixels():
    """A mixed run shorter than seven must become one literal byte."""
    pixels = [1, 0, 1, 1, 0, 0, 1]
    decoded, data = _roundtrip(pixels)
    assert decoded == pixels
    assert len(data) == 1
    assert data[0] == 0b1_1011001


def test_alternating_pattern_roundtrips():
    """The worst case for RLE must still be lossless."""
    pixels = [index % 2 for index in range(1001)]
    decoded, _ = _roundtrip(pixels)
    assert decoded == pixels


def test_random_planes_roundtrip():
    """Random and clustered data must survive a round trip."""
    rng = random.Random(20260906)
    for _ in range(40):
        length = rng.randint(1, 4000)
        if rng.random() < 0.5:
            pixels = [rng.randint(0, 1) for _ in range(length)]
        else:
            pixels = []
            while len(pixels) < length:
                pixels.extend([rng.randint(0, 1)] * rng.randint(1, 400))
            pixels = pixels[:length]
        decoded, _ = _roundtrip(pixels)
        assert decoded == pixels, f"failed for length {length}"


def test_realistic_panel_compresses_well():
    """A mostly blank 184x384 panel must compress by orders of magnitude."""
    width, height = 184, 384
    pixels = [0] * (width * height)
    for y in range(100, 140):
        for x in range(20, 160):
            pixels[y * width + x] = 1

    decoded, data = _roundtrip(pixels)
    assert decoded == pixels
    assert len(data) < (width * height) // 100


def test_truncated_stream_is_rejected():
    """A cut off length field must raise rather than return junk."""
    for broken in (bytes((0x01,)), bytes((0x00, 0x10))):
        try:
            rle.decompress(broken)
        except rle.RLEError:
            continue
        raise AssertionError(f"expected RLEError for {broken.hex()}")


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
