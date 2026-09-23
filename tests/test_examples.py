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
        if element["type"] == "rectangle"
        and element.get("fill") == "yellow"
        and element.get("y_start") == 78
    ]
    # Monday the 21st: Saturday and Sunday are the sixth and seventh column.
    assert sorted(element["x_start"] for element in yellow) == [600, 700]


def test_running_only_the_action_says_so_on_the_panel() -> None:
    """Half the script is an easy mistake; it must not be a stack trace.

    The events come from the step before, so running the drawcustom action
    on its own used to end in UndefinedError, which reaches the user as
    one unhelpful line. Now it draws a sentence saying what to do.
    """
    document = yaml.safe_load((EXAMPLES / "week_calendar.yaml").read_text("utf-8"))
    template = next(
        s["data"]["payload"]
        for s in document["sequence"]
        if s.get("action") == "esl_zhsunyco.drawcustom"
    )
    environment = Environment()  # noqa: S701 - not rendering HTML
    environment.filters["to_json"] = lambda value: json.dumps(value, ensure_ascii=False)

    # Neither chrome nor agenda: exactly what a standalone run has.
    elements = json.loads(
        environment.from_string(template).render(
            today_at=lambda *_: TODAY, timedelta=dt.timedelta, none=None
        )
    )
    drawcustom.validate(elements)
    assert any("Skript" in str(element.get("value", "")) for element in elements), (
        "the hint is missing"
    )
    assert _draw(elements).width == WIDTH


def test_the_modern_draft_layout_still_draws() -> None:
    block = json.loads((EXAMPLES / "calendar_modern_layout.json").read_text("utf-8"))
    elements, _ = drawcustom.normalise(block)
    drawcustom.validate(elements)
    assert _draw(elements).width == WIDTH


# --- the two calendar script -------------------------------------------


def _event(start: str, summary: str, end: str = "", hour: str = "") -> dict:
    if hour:
        stamp = f"2026-{start}T{hour}:00+02:00"
        return {"start": stamp, "end": stamp, "summary": summary}
    return {"start": f"2026-{start}", "end": f"2026-{end or start}", "summary": summary}


FAMILIE = {
    "calendar.familie": {
        "events": [
            _event("09-21", "Zahnarzt Lena", hour="08:30"),
            _event("09-21", "Kino mit Papa", hour="19:30"),
            # Four days of holiday, and it has to show on every one of them.
            _event("09-21", "Herbstferien", end="09-25"),
            _event("09-22", "Elternabend Klasse 3b", hour="16:00"),
            _event("09-22", "Sportfest der Schule", hour="09:00"),
            _event("09-22", "Zahnarzt Papa", hour="11:00"),
            _event("09-27", "Lenas Geburtstagsfeier", hour="14:00"),
        ]
    }
}

ABFALL = {
    "calendar.abfallkalender": {
        "events": [
            _event("09-22", "Restmuellbehaelter"),
            _event("09-24", "Gelbe Tonne"),
            _event("09-28", "Biomuellbehaelter"),
            _event("10-06", "Papierbehaelter"),
            _event("10-08", "Glas"),
            _event("10-15", "Schadstoffmobil"),
        ]
    }
}


def _render_family(familie=None, abfall=None, with_chrome=True) -> list[dict]:
    document = yaml.safe_load((EXAMPLES / "family_calendar.yaml").read_text("utf-8"))
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
        **({"chrome": chrome} if with_chrome else {}),
        familie=FAMILIE if familie is None else familie,
        abfall=ABFALL if abfall is None else abfall,
        today_at=lambda *_: TODAY,
        timedelta=dt.timedelta,
        now=lambda: TODAY.replace(hour=7, minute=15),
        none=None,
    )
    return json.loads(rendered)


def _values(elements: list[dict]) -> list[str]:
    return [str(e.get("value")) for e in elements if e["type"] == "text"]


def _mentions(elements: list[dict], text: str) -> int:
    """How many drawn strings carry this title.

    A timed entry is drawn as "14:00  Title" in the week column, so an
    exact match would miss it.
    """
    return sum(1 for value in _values(elements) if text in value)


def test_the_family_script_draws() -> None:
    elements = _render_family()
    drawcustom.validate(elements)
    assert len(_draw(elements).payload) == WIDTH * HEIGHT // 4


def test_a_multi_day_event_appears_on_every_day_it_covers() -> None:
    """The holiday runs 21st to 24th, so it belongs on four days."""
    assert _mentions(_render_family(), "Herbstferien") == 4


def test_a_timed_event_appears_once() -> None:
    assert _mentions(_render_family(), "Lenas Geburtstagsfeier") == 1


def test_the_bin_names_are_shortened_and_given_icons() -> None:
    """The lookup has to fix two things at once.

    The calendar writes Restmuellbehaelter: too wide for a chip, and
    missing its umlaut.
    """
    elements = _render_family()
    values = _values(elements)
    assert "Restmüll" in values
    assert "Restmuellbehaelter" not in values
    assert {"Bio", "Gelbe Tonne", "Papier", "Glas"} <= set(values)

    icons = {e["value"] for e in elements if e["type"] == "icon"}
    assert "mdi:recycle" in icons
    assert "mdi:leaf" in icons


def test_only_five_collections_are_shown() -> None:
    """Six are in range; the strip has room for five."""
    assert "Schadstoffe" not in _values(_render_family())


def test_red_marks_tomorrow_and_nothing_else() -> None:
    """The collection to put out tonight is the only red fill there is."""
    elements = _render_family()
    assert "MORGEN" in _values(elements)
    red = [e for e in elements if e["type"] == "rectangle" and e.get("fill") == "red"]
    assert len(red) == 1


def test_a_week_without_a_collection_tomorrow_stays_black() -> None:
    """Red has to mean act, which only works if it is usually absent."""
    later = {
        "calendar.abfallkalender": {"events": [_event("09-28", "Biomuellbehaelter")]}
    }
    elements = _render_family(abfall=later)
    assert not [
        e for e in elements if e["type"] == "rectangle" and e.get("fill") == "red"
    ]
    assert "MORGEN" not in _values(elements)


def test_an_empty_day_and_an_empty_calendar_still_draw() -> None:
    elements = _render_family(familie={}, abfall={})
    drawcustom.validate(elements)
    assert "Nichts eingetragen" in _values(elements)
    assert _draw(elements).width == WIDTH


def test_the_family_script_says_when_only_the_action_ran() -> None:
    elements = _render_family(with_chrome=False)
    drawcustom.validate(elements)
    assert any("Skript" in value for value in _values(elements))


def test_the_examples_quote_the_y_key() -> None:
    """A bare `y:` in YAML can be read as a boolean and lose the position.

    This bit once: two headings arrived without their y and stacked at the
    top edge of a real panel.
    """
    for name in ("family_calendar.yaml", "week_calendar.yaml"):
        text = (EXAMPLES / name).read_text("utf-8")
        assert "\n          y: " not in text, f"{name} has an unquoted y key"


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
