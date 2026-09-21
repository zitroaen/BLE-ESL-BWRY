"""Draw an OpenEPaperLink style "drawcustom" payload onto a panel canvas.

The ESPHome Designer can export a layout as an OpenEPaperLink service call,
which is a list of drawing elements: text, shapes, icons, QR codes. This
module takes that list and paints it onto an RGB image the size of the
panel. Everything after that - quantising to the panel palette, packing,
compressing, sending - is the ordinary image path in `imaging.py`.

No Home Assistant imports here on purpose: this is pure drawing and runs in
an executor. Anything that needs the network (a `dlimg` element) is fetched
by the caller beforehand and handed in through `resources`.

The element and property names follow OpenEPaperLink's documented payload so
a Designer export can be pasted across unchanged. Where a property has no
sensible meaning on a four colour label it is accepted and ignored rather
than rejected, because rejecting it would break an otherwise fine export.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from functools import lru_cache
from io import BytesIO
from pathlib import Path
from typing import Any

_LOGGER = logging.getLogger(__name__)

ASSETS = Path(__file__).parent / "assets"
MDI_FONT = ASSETS / "materialdesignicons-webfont.ttf"
MDI_CODEPOINTS = ASSETS / "mdi-codepoints.json"
TEXT_FONT = ASSETS / "Roboto-Regular.ttf"
TEXT_FONT_BOLD = ASSETS / "Roboto-Bold.ttf"


class DrawError(ValueError):
    """The payload cannot be drawn, and it is the payload's fault."""


# Colour names as OpenEPaperLink spells them, plus its one letter
# shortcuts. The halftones have no palette entry of their own: they are
# handed to the renderer as the midpoint colour and the Floyd-Steinberg
# dithering in imaging.py turns them into the mixed pattern the name
# promises, which is exactly how a halftone reaches an e-ink panel anyway.
COLORS: dict[str, tuple[int, int, int]] = {
    "black": (0, 0, 0),
    "b": (0, 0, 0),
    "white": (255, 255, 255),
    "w": (255, 255, 255),
    "red": (255, 0, 0),
    "r": (255, 0, 0),
    "yellow": (255, 255, 0),
    "y": (255, 255, 0),
    # The accent colour is whatever the label prints besides black. Ours
    # print both; red is the one OpenEPaperLink calls accent by default.
    "accent": (255, 0, 0),
    "a": (255, 0, 0),
    "half_black": (128, 128, 128),
    "hb": (128, 128, 128),
    "gray": (128, 128, 128),
    "grey": (128, 128, 128),
    "half_red": (255, 128, 128),
    "hr": (255, 128, 128),
    "half_yellow": (255, 255, 128),
    "hy": (255, 255, 128),
}

# Font names that an OpenEPaperLink payload or an ESPHome Designer export
# uses. None of them are on a Home Assistant box, so looking for the file
# would only waste a stat call per string drawn: they go straight to the
# bundled Roboto, in the weight the name asks for.
_KNOWN_FONT_NAMES = frozenset(
    {
        "ppb.ttf",
        "rbm.ttf",
        "bahnschrift.ttf",
        "segoeui.ttf",
        "arial.ttf",
        "roboto",
        "roboto-regular",
        "roboto-regular.ttf",
        "roboto-bold",
        "roboto-bold.ttf",
        "roboto-medium",
        "roboto-medium.ttf",
    }
)

# Substrings that mean "the heavy weight, please".
_BOLD_HINTS = ("bold", "black", "heavy", "b.ttf", "-b")

_COLOR_TAG = re.compile(r"\[(/?)([a-z_]+)\]")

# Which keys an element cannot do without. Everything else has a default.
REQUIRED_KEYS: dict[str, tuple[str, ...]] = {
    "debug_grid": (),
    "text": ("value",),
    "multiline": ("value",),
    "line": ("x_start", "y_start", "x_end", "y_end"),
    "rectangle": ("x_start", "y_start", "x_end", "y_end"),
    "rectangle_pattern": (
        "x_start",
        "y_start",
        "x_size",
        "y_size",
        "x_repeat",
        "y_repeat",
    ),
    "polygon": ("points",),
    "circle": ("x", "y", "radius"),
    "ellipse": ("x_start", "y_start", "x_end", "y_end"),
    "arc": ("x_start", "y_start", "x_end", "y_end", "start", "end"),
    "progress_bar": ("x_start", "y_start", "x_end", "y_end", "progress"),
    "icon": ("value",),
    "icon_sequence": ("icons",),
    "dlimg": ("url",),
    "qrcode": ("data",),
}

SUPPORTED_TYPES = frozenset(REQUIRED_KEYS)

# Known to OpenEPaperLink, deliberately not implemented here, with the
# reason the error message should give.
UNSUPPORTED_TYPES: dict[str, str] = {
    "plot": (
        "plot draws a chart from Home Assistant's recorder history, which "
        "this integration does not read. Render the chart elsewhere and "
        "place it with a dlimg element instead"
    ),
}


def _fail(where: str, message: str) -> DrawError:
    return DrawError(f"{where}: {message}")


def _no_payload_key(payload: dict[str, Any]) -> str:
    """Say what arrived instead of a payload, and what to do about it.

    The ESPHome Designer has two exports that both look like "the JSON":
    the project file, which describes the device for a firmware build, and
    the Home Assistant service call, which is the one with the drawing
    elements in it. Handing over the first is an easy mistake and the
    difference is not obvious from either file, so name it.
    """
    if isinstance(payload.get("pages"), list) and any(
        isinstance(page, dict) and "widgets" in page for page in payload["pages"]
    ):
        return (
            "this is an ESPHome Designer project file - it describes a "
            "device (pages, widgets, pins), not a drawing. In the Designer, "
            "export the layout as 'Home Assistant Service Call (JSON)' "
            "instead; that export has a 'payload' list of elements"
        )
    keys = ", ".join(sorted(payload)[:8]) or "nothing"
    return f"payload object has no 'payload' key with the elements (it has: {keys})"


def normalise(payload: Any) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Accept the shapes a Designer export can arrive in.

    Returns the element list and the top level options. A bare list is a
    payload with no options; a dict is the whole service call data block,
    where `payload` holds the elements and the rest are options. A string
    is parsed as JSON first, which is what a template renders to.
    """
    if isinstance(payload, str):
        text = payload.strip()
        if not text:
            raise DrawError("payload is empty")
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as err:
            raise DrawError(f"payload is not valid JSON: {err}") from err

    options: dict[str, Any] = {}
    if isinstance(payload, dict):
        # A full Designer export nests the real call under "data".
        if "payload" not in payload and isinstance(payload.get("data"), dict):
            payload = payload["data"]
        if "payload" not in payload:
            raise DrawError(_no_payload_key(payload))
        options = {k: v for k, v in payload.items() if k != "payload"}
        payload = payload["payload"]

    if not isinstance(payload, list):
        raise DrawError(
            f"payload must be a list of elements, got {type(payload).__name__}"
        )
    for index, element in enumerate(payload):
        if not isinstance(element, dict):
            raise DrawError(
                f"element {index} must be an object, got {type(element).__name__}"
            )
    return payload, options


def validate(elements: list[dict[str, Any]]) -> None:
    """Check a payload without drawing it.

    Worth doing before the service goes anywhere near the radio: a typo in
    an element type should come back immediately, not after a minute of
    connect attempts.
    """
    for index, element in enumerate(elements):
        where = f"element {index}"
        kind = element.get("type")
        if not kind:
            raise _fail(where, "has no 'type'")
        if not isinstance(kind, str):
            raise _fail(where, f"'type' must be a string, got {type(kind).__name__}")
        kind = kind.lower()
        where = f"element {index} ({kind})"
        if reason := UNSUPPORTED_TYPES.get(kind):
            raise _fail(where, reason)
        if kind not in SUPPORTED_TYPES:
            known = ", ".join(sorted(SUPPORTED_TYPES))
            raise _fail(where, f"unknown type. Supported types are: {known}")
        for key in REQUIRED_KEYS[kind]:
            if element.get(key) is None:
                raise _fail(where, f"needs '{key}'")
        for key, value in element.items():
            if isinstance(value, str) and key.endswith(
                ("color", "fill", "outline", "bgcolor", "background")
            ):
                parse_color(value, where)


def collect_urls(elements: list[dict[str, Any]]) -> list[str]:
    """Every image URL the payload needs, so the caller can fetch them."""
    urls: list[str] = []
    for element in elements:
        if str(element.get("type", "")).lower() == "dlimg":
            url = element.get("url")
            if isinstance(url, str) and url and url not in urls:
                urls.append(url)
    return urls


def parse_color(value: Any, where: str = "payload") -> tuple[int, int, int]:
    """Turn a payload colour into RGB."""
    if isinstance(value, (list, tuple)) and len(value) == 3:
        return tuple(int(component) for component in value)  # type: ignore[return-value]
    if not isinstance(value, str):
        raise _fail(where, f"{value!r} is not a colour")
    name = value.strip().lower()
    if name in COLORS:
        return COLORS[name]
    if name.startswith("#"):
        from PIL import ImageColor

        try:
            red, green, blue = ImageColor.getrgb(name)[:3]
        except ValueError as err:
            raise _fail(where, f"{value!r} is not a colour: {err}") from err
        return (red, green, blue)
    known = ", ".join(
        sorted(
            {
                "black",
                "white",
                "red",
                "yellow",
                "accent",
                "half_black",
                "half_red",
                "half_yellow",
            }
        )
    )
    raise _fail(where, f"unknown colour {value!r}. Known names: {known}, or #rrggbb")


@dataclass(slots=True)
class _Context:
    """Everything a single draw needs to know."""

    width: int
    height: int
    resources: dict[str, bytes] = field(default_factory=dict)
    cursor_y: float = 0.0
    where: str = "payload"


def _number(value: Any, span: int, ctx: _Context, name: str) -> float:
    """Resolve a coordinate, which may be a percentage of the panel."""
    if isinstance(value, str):
        text = value.strip()
        if text.endswith("%"):
            try:
                return float(text[:-1]) / 100.0 * span
            except ValueError as err:
                raise _fail(ctx.where, f"{name}={value!r} is not a percentage") from err
        value = text
    try:
        return float(value)
    except (TypeError, ValueError) as err:
        raise _fail(ctx.where, f"{name}={value!r} is not a number") from err


def _x(element: dict, ctx: _Context, key: str = "x", default: Any = 0) -> float:
    return _number(element.get(key, default), ctx.width, ctx, key)


def _y(element: dict, ctx: _Context, key: str = "y", default: Any = 0) -> float:
    return _number(element.get(key, default), ctx.height, ctx, key)


def _color(
    element: dict, ctx: _Context, key: str, default: Any
) -> tuple[int, int, int] | None:
    value = element.get(key, default)
    if value is None:
        return None
    return parse_color(value, ctx.where)


def _int(element: dict, key: str, default: int, ctx: _Context) -> int:
    value = element.get(key, default)
    try:
        return int(value)
    except (TypeError, ValueError) as err:
        raise _fail(ctx.where, f"{key}={value!r} is not a whole number") from err


@lru_cache(maxsize=64)
def _font_file(path: str, size: int):
    """Open a font file once per size; a layout draws many strings."""
    from PIL import ImageFont

    return ImageFont.truetype(path, size)


def _bundled_font(size: int, *, bold: bool):
    """Roboto, or Pillow's own font if the assets are not there.

    The fallback keeps a missing asset from taking the whole render down,
    but it cannot draw an umlaut, so it is a last resort and says so.
    """
    from PIL import ImageFont

    path = TEXT_FONT_BOLD if bold else TEXT_FONT
    if path.is_file():
        return _font_file(str(path), size)
    _LOGGER.warning(
        "%s is missing, falling back to the built in font - it has no "
        "umlauts and no accents. Run scripts/fetch_assets.py",
        path.name,
    )
    return ImageFont.load_default(size=size)


def _load_font(name: Any, size: int):
    """Pick a font for a text element.

    A name the payload gives is tried as a file first, so a `.ttf` in the
    config directory works. The names the drawing tools emit are not files
    on this machine, so those resolve to the bundled Roboto - which is
    also what the ESPHome Designer previews with.
    """
    if not isinstance(name, str) or not name.strip():
        return _bundled_font(size, bold=False)

    key = name.strip()
    lower = key.lower()
    bold = any(hint in lower for hint in _BOLD_HINTS)
    if lower not in _KNOWN_FONT_NAMES:
        try:
            return _font_file(key, size)
        except OSError:
            _LOGGER.debug("font %s not available, using the bundled font", key)
    return _bundled_font(size, bold=bold)


def _line_height(font) -> float:
    ascent, descent = font.getmetrics()
    return ascent + descent


def _split_colored(value: str, default: tuple[int, int, int], ctx: _Context):
    """Split "plain [red]hot[/red]" into coloured runs."""
    runs: list[tuple[str, tuple[int, int, int]]] = []
    stack = [default]
    position = 0
    for match in _COLOR_TAG.finditer(value):
        if match.start() > position:
            runs.append((value[position : match.start()], stack[-1]))
        closing, name = match.group(1), match.group(2)
        if closing:
            if len(stack) > 1:
                stack.pop()
        else:
            stack.append(parse_color(name, ctx.where))
        position = match.end()
    if position < len(value):
        runs.append((value[position:], stack[-1]))
    return [run for run in runs if run[0]]


def _wrap(draw, text: str, font, max_width: float) -> list[str]:
    """Break a line so no piece is wider than max_width."""
    lines: list[str] = []
    for paragraph in text.split("\n"):
        words = paragraph.split(" ")
        current = ""
        for word in words:
            candidate = f"{current} {word}".strip()
            if current and draw.textlength(candidate, font=font) > max_width:
                lines.append(current)
                current = word
            else:
                current = candidate
        lines.append(current)
    return lines


def _anchor_offset(draw, text: str, font, anchor: str) -> tuple[float, float]:
    """How far to shift a left/top drawn string for a given anchor."""
    horizontal, vertical = (anchor + "lt")[:2]
    width = draw.textlength(text, font=font)
    ascent, descent = font.getmetrics()
    dx = {"l": 0.0, "m": -width / 2, "r": -width}.get(horizontal, 0.0)
    dy = {
        "t": 0.0,
        "a": 0.0,
        "m": -(ascent + descent) / 2,
        "s": -float(ascent),
        "b": -(ascent + descent),
        "d": -(ascent + descent),
    }.get(vertical, 0.0)
    return dx, dy


def _draw_line_of_text(
    draw,
    x: float,
    y: float,
    text: str,
    font,
    color: tuple[int, int, int],
    anchor: str,
    ctx: _Context,
    *,
    parse_colors: bool = False,
    stroke_width: int = 0,
    stroke_fill: tuple[int, int, int] | None = None,
) -> None:
    """Draw one line at a top-left origin, honouring the anchor ourselves.

    Pillow can do anchors, but not per-run colours, so the placement maths
    lives here and every run is then drawn left to right from the same
    resolved origin.
    """
    runs = _split_colored(text, color, ctx) if parse_colors else [(text, color)]
    plain = "".join(run[0] for run in runs)
    dx, dy = _anchor_offset(draw, plain, font, anchor)
    pen = x + dx
    top = y + dy
    for piece, run_color in runs:
        draw.text(
            (pen, top),
            piece,
            font=font,
            fill=run_color,
            anchor="la",
            stroke_width=stroke_width,
            stroke_fill=stroke_fill,
        )
        pen += draw.textlength(piece, font=font)


def _element_y(element: dict, ctx: _Context, padding: float) -> float:
    """Resolve the y to draw at: the given one, or below the last element."""
    if element.get("y") is None:
        return ctx.cursor_y + padding
    return _y(element, ctx)


# --- element renderers ----------------------------------------------------


def _render_text(draw, element: dict, ctx: _Context) -> None:
    value = element.get("value")
    if not isinstance(value, str):
        value = "" if value is None else str(value)
    size = _int(element, "size", 20, ctx)
    font = _load_font(element.get("font"), size)
    color = _color(element, ctx, "color", "black") or (0, 0, 0)
    anchor = str(element.get("anchor", "lt")).lower()
    spacing = _number(element.get("spacing", 5), ctx.height, ctx, "spacing")
    padding = _number(element.get("y_padding", 10), ctx.height, ctx, "y_padding")
    stroke_width = _int(element, "stroke_width", 0, ctx)
    stroke_fill = _color(element, ctx, "stroke_fill", None) if stroke_width else None
    parse_colors = bool(element.get("parse_colors", False))

    x = _x(element, ctx)
    y = _element_y(element, ctx, padding)

    lines = [value]
    if max_width := element.get("max_width"):
        limit = _number(max_width, ctx.width, ctx, "max_width")
        plain = _COLOR_TAG.sub("", value) if parse_colors else value
        if element.get("truncate"):
            while len(plain) > 1 and draw.textlength(plain, font=font) > limit:
                plain = plain[:-1]
            lines = [plain]
            parse_colors = False
        else:
            lines = _wrap(draw, plain, font, limit)
            if parse_colors and len(lines) > 1:
                # Wrapping rewrites the string, so the tags no longer line
                # up with the runs. Colour the whole block instead.
                parse_colors = False
    elif "\n" in value:
        lines = value.split("\n")

    height = _line_height(font)
    for index, line in enumerate(lines):
        _draw_line_of_text(
            draw,
            x,
            y + index * (height + spacing),
            line,
            font,
            color,
            anchor,
            ctx,
            parse_colors=parse_colors,
            stroke_width=stroke_width,
            stroke_fill=stroke_fill,
        )
    _, dy = _anchor_offset(draw, lines[0] if lines else "", font, anchor)
    ctx.cursor_y = y + dy + len(lines) * height + max(0, len(lines) - 1) * spacing


def _render_multiline(draw, element: dict, ctx: _Context) -> None:
    value = element.get("value")
    delimiter = element.get("delimiter", "\n") or "\n"
    text = value if isinstance(value, str) else str(value)
    lines = text.split(delimiter)
    offset = element.get("offset_y")
    spacing = _number(element.get("spacing", 5), ctx.height, ctx, "spacing")
    size = _int(element, "size", 20, ctx)
    font = _load_font(element.get("font"), size)
    height = (
        _number(offset, ctx.height, ctx, "offset_y")
        if offset is not None
        else _line_height(font) + spacing
    )
    color = _color(element, ctx, "color", "black") or (0, 0, 0)
    anchor = str(element.get("anchor", "lt")).lower()
    padding = _number(element.get("y_padding", 10), ctx.height, ctx, "y_padding")
    parse_colors = bool(element.get("parse_colors", False))
    x = _x(element, ctx)
    y = _element_y(element, ctx, padding)

    for index, line in enumerate(lines):
        _draw_line_of_text(
            draw,
            x,
            y + index * height,
            line,
            font,
            color,
            anchor,
            ctx,
            parse_colors=parse_colors,
        )
    _, dy = _anchor_offset(draw, lines[0] if lines else "", font, anchor)
    ctx.cursor_y = y + dy + len(lines) * height


def _render_line(draw, element: dict, ctx: _Context) -> None:
    draw.line(
        (
            _x(element, ctx, "x_start"),
            _y(element, ctx, "y_start"),
            _x(element, ctx, "x_end"),
            _y(element, ctx, "y_end"),
        ),
        fill=_color(element, ctx, "fill", "black"),
        width=_int(element, "width", 1, ctx),
    )


def _corners(element: dict) -> tuple[bool, bool, bool, bool]:
    corners = element.get("corners", "all")
    if isinstance(corners, str):
        corners = [part.strip() for part in corners.replace(",", " ").split()]
    if not corners or "all" in corners:
        return (True, True, True, True)
    names = {str(corner).lower() for corner in corners}
    return (
        "topleft" in names or "top_left" in names,
        "topright" in names or "top_right" in names,
        "bottomright" in names or "bottom_right" in names,
        "bottomleft" in names or "bottom_left" in names,
    )


def _box(element: dict, ctx: _Context) -> tuple[float, float, float, float]:
    x0 = _x(element, ctx, "x_start")
    y0 = _y(element, ctx, "y_start")
    x1 = _x(element, ctx, "x_end")
    y1 = _y(element, ctx, "y_end")
    return (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))


def _rectangle(draw, box, element: dict, ctx: _Context) -> None:
    radius = _int(element, "radius", 0, ctx)
    kwargs = {
        "fill": _color(element, ctx, "fill", None),
        "outline": _color(element, ctx, "outline", "black"),
        "width": _int(element, "width", 1, ctx),
    }
    if radius > 0:
        draw.rounded_rectangle(box, radius=radius, corners=_corners(element), **kwargs)
    else:
        draw.rectangle(box, **kwargs)


def _render_rectangle(draw, element: dict, ctx: _Context) -> None:
    _rectangle(draw, _box(element, ctx), element, ctx)


def _render_rectangle_pattern(draw, element: dict, ctx: _Context) -> None:
    x_start = _x(element, ctx, "x_start")
    y_start = _y(element, ctx, "y_start")
    x_size = _x(element, ctx, "x_size")
    y_size = _y(element, ctx, "y_size")
    x_repeat = _int(element, "x_repeat", 1, ctx)
    y_repeat = _int(element, "y_repeat", 1, ctx)
    x_offset = _x(element, ctx, "x_offset", 0)
    y_offset = _y(element, ctx, "y_offset", 0)
    for row in range(y_repeat):
        for column in range(x_repeat):
            left = x_start + column * (x_size + x_offset)
            top = y_start + row * (y_size + y_offset)
            _rectangle(draw, (left, top, left + x_size, top + y_size), element, ctx)


def _render_polygon(draw, element: dict, ctx: _Context) -> None:
    points = element.get("points")
    if not isinstance(points, list) or len(points) < 3:
        raise _fail(ctx.where, "needs at least three points")
    resolved: list[tuple[float, float]] = []
    for point in points:
        if isinstance(point, dict):
            pair = (point.get("x"), point.get("y"))
        elif isinstance(point, (list, tuple)) and len(point) == 2:
            pair = (point[0], point[1])
        else:
            raise _fail(ctx.where, f"point {point!r} must be [x, y]")
        resolved.append(
            (
                _number(pair[0], ctx.width, ctx, "x"),
                _number(pair[1], ctx.height, ctx, "y"),
            )
        )
    draw.polygon(
        resolved,
        fill=_color(element, ctx, "fill", None),
        outline=_color(element, ctx, "outline", "black"),
        width=_int(element, "width", 1, ctx),
    )


def _render_circle(draw, element: dict, ctx: _Context) -> None:
    x = _x(element, ctx)
    y = _y(element, ctx)
    radius = _number(element.get("radius"), ctx.width, ctx, "radius")
    draw.ellipse(
        (x - radius, y - radius, x + radius, y + radius),
        fill=_color(element, ctx, "fill", None),
        outline=_color(element, ctx, "outline", "black"),
        width=_int(element, "width", 1, ctx),
    )


def _render_ellipse(draw, element: dict, ctx: _Context) -> None:
    draw.ellipse(
        _box(element, ctx),
        fill=_color(element, ctx, "fill", None),
        outline=_color(element, ctx, "outline", "black"),
        width=_int(element, "width", 1, ctx),
    )


def _render_arc(draw, element: dict, ctx: _Context) -> None:
    draw.arc(
        _box(element, ctx),
        start=_number(element.get("start", 0), 360, ctx, "start"),
        end=_number(element.get("end", 360), 360, ctx, "end"),
        fill=_color(element, ctx, "fill", "black"),
        width=_int(element, "width", 1, ctx),
    )


def _render_progress_bar(draw, element: dict, ctx: _Context) -> None:
    x0, y0, x1, y1 = _box(element, ctx)
    progress = max(
        0.0, min(100.0, _number(element.get("progress", 0), 100, ctx, "progress"))
    )
    direction = str(element.get("direction", "right")).lower()
    background = _color(element, ctx, "background", "white")
    fill = _color(element, ctx, "fill", "red")
    outline = _color(element, ctx, "outline", "black")
    width = _int(element, "width", 1, ctx)

    draw.rectangle((x0, y0, x1, y1), fill=background, outline=outline, width=width)

    fraction = progress / 100.0
    inset = width
    left, top, right, bottom = x0 + inset, y0 + inset, x1 - inset, y1 - inset
    if right <= left or bottom <= top:
        return
    if direction == "left":
        bar = (right - (right - left) * fraction, top, right, bottom)
    elif direction == "up":
        bar = (left, bottom - (bottom - top) * fraction, right, bottom)
    elif direction == "down":
        bar = (left, top, right, top + (bottom - top) * fraction)
    else:
        bar = (left, top, left + (right - left) * fraction, bottom)
    if bar[2] > bar[0] and bar[3] > bar[1]:
        draw.rectangle(bar, fill=fill)

    if element.get("show_percentage"):
        size = _int(element, "size", max(8, int((bottom - top) * 0.7)), ctx)
        font = _load_font(element.get("font"), size)
        label = f"{int(round(progress))}%"
        _draw_line_of_text(
            draw,
            (x0 + x1) / 2,
            (y0 + y1) / 2,
            label,
            font,
            _color(element, ctx, "color", "black") or (0, 0, 0),
            "mm",
            ctx,
        )


_ICON_TABLE: dict[str, str] | None = None


def _icon_char(name: Any, ctx: _Context) -> str:
    """Look up an icon name in the bundled Material Design Icons table."""
    global _ICON_TABLE  # noqa: PLW0603

    if not isinstance(name, str) or not name.strip():
        raise _fail(ctx.where, f"{name!r} is not an icon name")
    key = name.strip().lower()
    if key.startswith("mdi:"):
        key = key[4:]
    key = key.replace("_", "-")

    if _ICON_TABLE is None:
        if not MDI_CODEPOINTS.is_file():
            raise _fail(ctx.where, f"the icon table is missing at {MDI_CODEPOINTS}")
        _ICON_TABLE = json.loads(MDI_CODEPOINTS.read_text(encoding="utf-8"))["icons"]
    codepoint = _ICON_TABLE.get(key)
    if codepoint is None:
        raise _fail(ctx.where, f"no Material Design Icon called {name!r}")
    return chr(int(codepoint, 16))


def _icon_font(size: int, ctx: _Context):
    if not MDI_FONT.is_file():
        raise _fail(ctx.where, f"the icon font is missing at {MDI_FONT}")
    return _font_file(str(MDI_FONT), size)


def _render_icon(draw, element: dict, ctx: _Context) -> None:
    size = _int(element, "size", 20, ctx)
    font = _icon_font(size, ctx)
    color = _color(element, ctx, "color", None) or _color(element, ctx, "fill", "black")
    anchor = str(element.get("anchor", "la")).lower()
    padding = _number(element.get("y_padding", 10), ctx.height, ctx, "y_padding")
    x = _x(element, ctx)
    y = _element_y(element, ctx, padding)
    glyph = _icon_char(element.get("value"), ctx)
    _draw_line_of_text(draw, x, y, glyph, font, color, anchor, ctx)
    ctx.cursor_y = y + _line_height(font)


def _render_icon_sequence(draw, element: dict, ctx: _Context) -> None:
    icons = element.get("icons")
    if not isinstance(icons, list) or not icons:
        raise _fail(ctx.where, "needs a non empty 'icons' list")
    size = _int(element, "size", 20, ctx)
    font = _icon_font(size, ctx)
    color = _color(element, ctx, "color", None) or _color(element, ctx, "fill", "black")
    spacing = _number(element.get("spacing", 5), ctx.width, ctx, "spacing")
    direction = str(element.get("direction", "right")).lower()
    padding = _number(element.get("y_padding", 10), ctx.height, ctx, "y_padding")
    x = _x(element, ctx)
    y = _element_y(element, ctx, padding)

    step = size + spacing
    for index, icon in enumerate(icons):
        name = icon.get("value") if isinstance(icon, dict) else icon
        glyph = _icon_char(name, ctx)
        piece_color = color
        if isinstance(icon, dict) and icon.get("color") is not None:
            piece_color = parse_color(icon["color"], ctx.where)
        if direction == "left":
            spot = (x - index * step, y)
        elif direction == "up":
            spot = (x, y - index * step)
        elif direction == "down":
            spot = (x, y + index * step)
        else:
            spot = (x + index * step, y)
        _draw_line_of_text(draw, spot[0], spot[1], glyph, font, piece_color, "la", ctx)
    ctx.cursor_y = y + _line_height(font)


def _render_dlimg(canvas, element: dict, ctx: _Context) -> None:
    from PIL import Image

    url = element.get("url")
    data = ctx.resources.get(url)
    if data is None:
        raise _fail(ctx.where, f"nothing was downloaded for {url!r}")
    try:
        source = Image.open(BytesIO(data))
    except Exception as err:  # noqa: BLE001 - Pillow raises several kinds
        raise _fail(ctx.where, f"{url} is not a readable image: {err}") from err
    source = source.convert("RGBA")

    x_size = element.get("xsize", element.get("x_size"))
    y_size = element.get("ysize", element.get("y_size"))
    if x_size is not None or y_size is not None:
        target_w = (
            int(_number(x_size, ctx.width, ctx, "xsize")) if x_size else source.width
        )
        target_h = (
            int(_number(y_size, ctx.height, ctx, "ysize")) if y_size else source.height
        )
        source = source.resize((max(1, target_w), max(1, target_h)), Image.LANCZOS)
    if rotate := element.get("rotate"):
        source = source.rotate(
            -_number(rotate, 360, ctx, "rotate"), expand=True, fillcolor=(0, 0, 0, 0)
        )

    x = int(_x(element, ctx))
    y = int(_y(element, ctx))
    canvas.paste(source, (x, y), source)
    ctx.cursor_y = y + source.height


def _render_qrcode(canvas, element: dict, ctx: _Context) -> None:
    try:
        import qrcode as qrcode_lib
    except ImportError as err:  # pragma: no cover - the manifest requires it
        raise _fail(ctx.where, "the qrcode library is not installed") from err

    data = element.get("data")
    code = qrcode_lib.QRCode(
        box_size=_int(element, "boxsize", 2, ctx),
        border=_int(element, "border", 1, ctx),
    )
    code.add_data("" if data is None else str(data))
    code.make(fit=True)
    image = code.make_image(
        fill_color=_color(element, ctx, "color", "black"),
        back_color=_color(element, ctx, "bgcolor", "white"),
    ).convert("RGB")
    canvas.paste(image, (int(_x(element, ctx)), int(_y(element, ctx))))
    ctx.cursor_y = _y(element, ctx) + image.height


def _render_debug_grid(draw, element: dict, ctx: _Context) -> None:
    spacing = _int(element, "spacing", 50, ctx)
    if spacing < 2:
        raise _fail(ctx.where, "spacing must be at least 2")
    color = _color(element, ctx, "color", "red")
    font = _load_font(None, max(8, min(12, spacing // 3)))
    for x in range(0, ctx.width, spacing):
        draw.line((x, 0, x, ctx.height), fill=color, width=1)
    for y in range(0, ctx.height, spacing):
        draw.line((0, y, ctx.width, y), fill=color, width=1)
    for x in range(0, ctx.width, spacing):
        for y in range(0, ctx.height, spacing):
            draw.text((x + 1, y + 1), f"{x},{y}", font=font, fill=color, anchor="la")


# Renderers that paint through ImageDraw, and those that need the image
# itself because they paste pixels onto it.
_DRAW_RENDERERS = {
    "debug_grid": _render_debug_grid,
    "text": _render_text,
    "multiline": _render_multiline,
    "line": _render_line,
    "rectangle": _render_rectangle,
    "rectangle_pattern": _render_rectangle_pattern,
    "polygon": _render_polygon,
    "circle": _render_circle,
    "ellipse": _render_ellipse,
    "arc": _render_arc,
    "progress_bar": _render_progress_bar,
    "icon": _render_icon,
    "icon_sequence": _render_icon_sequence,
}
_CANVAS_RENDERERS = {
    "dlimg": _render_dlimg,
    "qrcode": _render_qrcode,
}


def render_payload(
    elements: list[dict[str, Any]],
    width: int,
    height: int,
    *,
    background: Any = "white",
    rotate: int = 0,
    resources: dict[str, bytes] | None = None,
    antialias: bool = True,
):
    """Draw the payload and return an RGB image of exactly width x height.

    A rotation of 90 or 270 is taken to mean the layout was designed for the
    other orientation, so the elements are drawn on a swapped canvas and the
    result is turned to fit the panel. That way percentages and coordinates
    in the export mean what the designer saw.

    With `antialias` off, glyphs are rendered bi-level: every pixel is
    either on or off, so text and icons land exactly on the pixel grid.
    That is usually what you want here. A panel with four colours has no
    grey to put a soft edge in, so an antialiased edge is quantised to
    whatever is nearest - ragged with dithering off, speckled with it on.
    """
    from PIL import Image, ImageDraw

    rotate = int(rotate or 0) % 360
    if rotate not in (0, 90, 180, 270):
        raise DrawError(f"rotate must be 0, 90, 180 or 270, got {rotate}")

    canvas_w, canvas_h = (height, width) if rotate in (90, 270) else (width, height)
    canvas = Image.new("RGB", (canvas_w, canvas_h), parse_color(background))
    draw = ImageDraw.Draw(canvas)
    if not antialias:
        # Pillow's own switch for bi-level glyph rendering.
        draw.fontmode = "1"
    ctx = _Context(width=canvas_w, height=canvas_h, resources=resources or {})

    for index, element in enumerate(elements):
        kind = str(element.get("type", "")).lower()
        ctx.where = f"element {index} ({kind})" if kind else f"element {index}"
        if "visible" in element and not element["visible"]:
            continue
        if renderer := _DRAW_RENDERERS.get(kind):
            renderer(draw, element, ctx)
        elif canvas_renderer := _CANVAS_RENDERERS.get(kind):
            canvas_renderer(canvas, element, ctx)
        elif reason := UNSUPPORTED_TYPES.get(kind):
            raise _fail(ctx.where, reason)
        else:
            known = ", ".join(sorted(SUPPORTED_TYPES))
            raise _fail(ctx.where, f"unknown type. Supported types are: {known}")

    if rotate:
        canvas = canvas.rotate(rotate, expand=True)
    if canvas.size != (width, height):  # pragma: no cover - guards the maths
        canvas = canvas.resize((width, height), Image.LANCZOS)
    return canvas
