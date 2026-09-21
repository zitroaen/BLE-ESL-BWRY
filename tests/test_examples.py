"""The examples have to keep working, or they are worse than nothing.

The calendar example is a Jinja template that builds a payload from
calendar events. Nothing in the integration would notice if it stopped
producing valid elements, so it is rendered here against stand-ins for
Home Assistant's own template helpers and then drawn.

Run directly (python tests/test_examples.py) or under pytest.
"""

from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

import yaml

# Importable both under pytest from the repository root and as a script.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from jinja2 import Environment  # noqa: E402

from custom_components.esl_zhsunyco import drawcustom
from custom_components.esl_zhsunyco.imaging import ImageRequest, render_image

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
WIDTH, HEIGHT = 800, 480

# A Monday, so the weekend falls inside the seven day view rather than at
# its edge - that is where a wrong column index would hide.
TODAY = dt.datetime(2026, 9, 21, 0, 0)


def _event(day: int, summary: str, hour: str | None = None) -> dict:
    start = f"2026-09-{day:02d}" + (f"T{hour}:00+02:00" if hour else "")
    return {"start": start, "end": start, "summary": summary}


AGENDA = {
    "calendar.mein_kalender": {
        "events": [
            _event(21, "Zahnarzt Müller", "08:30"),
            _event(21, "Training Lena", "17:15"),
            _event(22, "Standup", "09:00"),
            _event(25, "Werkstatt Termin für den Bus", "10:00"),
        ]
    },
    "calendar.familienkalender": {
        "events": [
            _event(21, "Kino mit Papa", "19:30"),
            _event(22, "Elternabend Klasse 3b", "16:00"),
            _event(26, "Brunch bei Oma", "09:00"),
        ]
    },
    "calendar.geburtstage": {
        "events": [_event(21, "Oma Grünschnitt wird 79"), _event(27, "Lena wird 9")]
    },
    "calendar.muellkalender": {
        "events": [_event(21, "Restmüll rausstellen"), _event(26, "Biotonne")]
    },
}


def _render_script_payload() -> list[dict]:
    """Run the example's template the way Home Assistant would."""
    document = yaml.safe_load((EXAMPLES / "week_calendar.yaml").read_text("utf-8"))
    steps = document["sequence"]
    chrome = next(s["variables"]["chrome"] for s in steps if "variables" in s)
    template = next(
        s["data"]["payload"]
        for s in steps
        if s.get("action") == "esl_zhsunyco.drawcustom"
    )

    environment = Environment()  # noqa: S701 - not rendering HTML
    environment.filters["to_json"] = lambda value: json.dumps(value, ensure_ascii=False)
    rendered = environment.from_string(template).render(
        chrome=chrome,
        agenda=AGENDA,
        today_at=lambda *_: TODAY,
        timedelta=dt.timedelta,
        none=None,
    )
    return json.loads(rendered)


def _draw(elements: list[dict]):
    request = ImageRequest(
        payload=elements,
        payload_options={"background": "white", "rotate": 0, "antialias": False},
        dither=False,
        stretch=True,
        pixel_format="bwry",
    )
    return render_image(request, WIDTH, HEIGHT)


def test_the_calendar_script_builds_a_payload_that_draws() -> None:
    elements = _render_script_payload()
    drawcustom.validate(elements)
    rendered = _draw(elements)

    assert rendered.width, rendered.height == (WIDTH, HEIGHT)
    assert len(rendered.payload) == WIDTH * HEIGHT // 4


def test_every_event_in_the_agenda_reaches_the_screen() -> None:
    """Silently dropping an appointment is the worst failure this can have."""
    texts = {
        str(element.get("value"))
        for element in _render_script_payload()
        if element["type"] == "text"
    }
    for calendar in AGENDA.values():
        for event in calendar["events"]:
            assert any(event["summary"] in text for text in texts), event["summary"]


def test_the_weekend_columns_are_highlighted_where_the_weekend_falls() -> None:
    """The columns start at today, so the weekend moves through the week."""
    yellow = [
        element
        for element in _render_script_payload()
        if element["type"] == "rectangle" and element.get("fill") == "yellow"
        and element.get("y_start") == 78
    ]
    # Monday the 21st: Saturday and Sunday are the sixth and seventh column.
    assert sorted(element["x_start"] for element in yellow) == [600, 700]


def test_the_designer_layout_still_draws() -> None:
    block = json.loads((EXAMPLES / "week_calendar_layout.json").read_text("utf-8"))
    elements, options = drawcustom.normalise(block)
    drawcustom.validate(elements)
    assert options["background"] == "white"
    assert _draw(elements).width == WIDTH


if __name__ == "__main__":
    for name, function in sorted(globals().items()):
        if name.startswith("test_"):
            function()
            print(f"ok  {name}")
    print("examples: all checks passed")
