#!/usr/bin/env python3
"""Fetch the fonts the drawcustom renderer draws with.

Two sets, both committed so a Home Assistant install never has to reach
the network to draw a label:

  * Roboto, regular and bold, for text. Pillow's built in font has no
    umlauts - it draws "Müller" as "M box ller" - which rules it out for
    anything but English. Roboto is also what the ESPHome Designer shows
    while you lay a screen out, so what you design is what you get.
  * Material Design Icons, plus a table saying which glyph each name
    lives at, so an "icon" element can say "mdi:battery-50".

Run it again to pick up newer versions:

    python scripts/fetch_assets.py

Both are licensed Apache 2.0; see assets/NOTICE.
"""

from __future__ import annotations

import json
import re
import sys
import urllib.request
from pathlib import Path

REPO = "https://raw.githubusercontent.com/Templarian/MaterialDesign-Webfont/master"
FONT_URL = f"{REPO}/fonts/materialdesignicons-webfont.ttf"
VARIABLES_URL = f"{REPO}/scss/_variables.scss"

ROBOTO = "https://raw.githubusercontent.com/googlefonts/roboto/main/src/hinted"
TEXT_FONTS = {
    "Roboto-Regular.ttf": f"{ROBOTO}/Roboto-Regular.ttf",
    "Roboto-Bold.ttf": f"{ROBOTO}/Roboto-Bold.ttf",
}

TRUETYPE_MAGIC = (b"\x00\x01\x00\x00", b"true", b"ttcf", b"OTTO")

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "custom_components/esl_zhsunyco/assets"

_ENTRY = re.compile(r'^\s*"([a-z0-9-]+)":\s*([0-9A-Fa-f]{4,6}),?\s*$')
_VERSION = re.compile(r'\$mdi-version:\s*"([^"]+)"')


def _get(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=60) as response:  # noqa: S310
        return response.read()


def main() -> int:
    """Download both files and write them into the integration."""
    ASSETS.mkdir(parents=True, exist_ok=True)

    for name, url in TEXT_FONTS.items():
        data = _get(url)
        if data[:4] not in TRUETYPE_MAGIC:
            print(f"{name} does not look like a TrueType file", file=sys.stderr)
            return 1
        (ASSETS / name).write_bytes(data)
        print(f"{name}: {len(data)} bytes")

    font = _get(FONT_URL)
    if font[:4] not in TRUETYPE_MAGIC:
        print("the icon font does not look like a TrueType file", file=sys.stderr)
        return 1
    (ASSETS / "materialdesignicons-webfont.ttf").write_bytes(font)

    scss = _get(VARIABLES_URL).decode("utf-8")
    version_match = _VERSION.search(scss)
    icons: dict[str, str] = {}
    for line in scss.splitlines():
        if match := _ENTRY.match(line):
            icons[match.group(1)] = match.group(2).upper()
    if len(icons) < 1000:
        print(f"only parsed {len(icons)} icons, refusing to write", file=sys.stderr)
        return 1

    table = {
        "version": version_match.group(1) if version_match else "unknown",
        "icons": dict(sorted(icons.items())),
    }
    (ASSETS / "mdi-codepoints.json").write_text(
        json.dumps(table, separators=(",", ":")) + "\n", encoding="utf-8"
    )

    print(
        f"icon font: {len(font)} bytes, "
        f"icons: {len(icons)}, version {table['version']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
