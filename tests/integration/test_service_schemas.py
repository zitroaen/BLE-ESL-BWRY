"""What the Home Assistant UI submits has to pass the service schemas.

A schema rejection does not reach the frontend as a readable message: it
comes out of `hass.services.async_call` as a voluptuous error, which is not
a HomeAssistantError, and the UI renders that as a bare "Unknown error".
So a schema that is stricter than the form is a dead end for the user, and
that is worth a test rather than a bug report.
"""

from __future__ import annotations

import pathlib

import pytest
import voluptuous as vol
import yaml

from custom_components.esl_zhsunyco import services as svc

SPEC = yaml.safe_load(
    pathlib.Path("custom_components/esl_zhsunyco/services.yaml").read_text()
)

SCHEMAS = {
    "set_rgb": svc.SET_RGB_SCHEMA,
    "clear_screen": svc.CLEAR_SCREEN_SCHEMA,
    "set_image": svc.SET_IMAGE_SCHEMA,
    "debug_probe": svc.DEBUG_PROBE_SCHEMA,
    "debug_command": svc.DEBUG_COMMAND_SCHEMA,
    "send_test_pattern": svc.SEND_TEST_PATTERN_SCHEMA,
}


def _text_fields(service: str) -> list[str]:
    """Return the optional fields the UI renders as a text box.

    Only these arrive empty: the UI omits an untouched number, boolean or
    select, but it submits a text box the user left alone as "".
    """
    fields = (SPEC[service] or {}).get("fields") or {}
    return [
        name
        for name, cfg in fields.items()
        if name != "device_id"
        and not cfg.get("required")
        and "text" in (cfg.get("selector") or {})
    ]


def test_every_service_is_covered() -> None:
    """A new service must not slip past this file unnoticed."""
    assert set(SCHEMAS) == set(SPEC), "services.yaml and SCHEMAS disagree"


@pytest.mark.parametrize("service", sorted(SCHEMAS))
def test_blank_optional_text_fields_are_accepted(service: str) -> None:
    """Leaving an optional text box empty must not be an error."""
    blanks = _text_fields(service)
    if not blanks:
        pytest.skip(f"{service} has no optional text fields")

    fields = (SPEC[service] or {}).get("fields") or {}
    for blank in ("", "   ", None):
        data: dict = {"device_id": ["abc"]}
        for name, cfg in fields.items():
            if name == "device_id":
                continue
            if cfg.get("required"):
                data[name] = cfg.get("example", "a")
            elif name in blanks:
                data[name] = blank
        try:
            SCHEMAS[service](data)
        except vol.Invalid as err:
            # set_image needs one of two sources; saying so is the point.
            if "either path or url" in str(err):
                continue
            raise AssertionError(
                f"{service} rejects a blank optional text field ({blank!r}): {err}"
            ) from err


def test_set_image_accepts_either_source_with_the_other_left_blank() -> None:
    """The specific shape that made set_image unusable from the UI.

    An exclusion group rejects the key being present at all, and cv.url
    rejects an empty string, so filling in one field and leaving the other
    alone failed twice over.
    """
    base = {"device_id": ["abc"]}

    assert SCHEMAS["set_image"]({**base, "path": "/config/a.png", "url": ""})
    assert SCHEMAS["set_image"]({**base, "path": "", "url": "https://e.com/a.png"})

    with pytest.raises(vol.Invalid, match="either path or url"):
        SCHEMAS["set_image"]({**base, "path": "", "url": ""})
    with pytest.raises(vol.Invalid, match="either path or url"):
        SCHEMAS["set_image"](
            {**base, "path": "/config/a.png", "url": "https://e.com/a.png"}
        )


def test_a_blank_source_does_not_reach_the_handler() -> None:
    """The validator strips it, so the handler sees one source, not two."""
    result = SCHEMAS["set_image"](
        {"device_id": ["abc"], "path": "/config/a.png", "url": None}
    )
    assert "url" not in result
    assert result["path"] == "/config/a.png"
