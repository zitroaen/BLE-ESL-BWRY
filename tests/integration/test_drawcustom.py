"""The drawcustom service: an exported layout in, panel bytes out."""

from __future__ import annotations

from io import BytesIO
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import device_registry as dr
from homeassistant.util import dt as dt_util

from custom_components.esl_zhsunyco.const import DOMAIN, SERVICE_DRAWCUSTOM

from .conftest import raw_payload

PAYLOAD = [
    {
        "type": "rectangle",
        "x_start": 0,
        "y_start": 0,
        "x_end": "100%",
        "y_end": 30,
        "fill": "red",
    },
    {
        "type": "text",
        "value": "Geburtstage",
        "x": 4,
        "y": 4,
        "size": 18,
        "color": "white",
    },
]


async def _setup(hass: HomeAssistant, entry):
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    device = entry.runtime_data
    device.state.last_advert = dt_util.utcnow()
    device.coordinator.async_update_listeners()
    return device


def _device_id(hass: HomeAssistant, entry) -> str:
    return dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)[0].id


async def _call(hass, data, uploaded: list[bytes]):
    async def fake_send_image(client, payload, *, compressed=False):
        uploaded.append(bytes(payload))

    with (
        patch(
            "custom_components.esl_zhsunyco.device.protocol.send_prepared_image",
            new=fake_send_image,
        ),
        patch(
            "custom_components.esl_zhsunyco.device._ESLConnection.__aenter__",
            new=AsyncMock(return_value=object()),
        ),
        patch(
            "custom_components.esl_zhsunyco.device._ESLConnection.__aexit__",
            new=AsyncMock(return_value=False),
        ),
    ):
        return await hass.services.async_call(
            DOMAIN, SERVICE_DRAWCUSTOM, data, blocking=True, return_response=True
        )


async def test_a_payload_is_drawn_and_sent(
    hass: HomeAssistant, config_entry, mock_bluetooth
) -> None:
    """The whole point: elements in an automation reach the panel."""
    await _setup(hass, config_entry)
    uploaded: list[bytes] = []

    response = await _call(
        hass,
        {"device_id": _device_id(hass, config_entry), "payload": PAYLOAD},
        uploaded,
    )

    # A full panel of two bit pixels, same as any other image.
    assert len(raw_payload(uploaded[0])) == 184 * 384 // 4
    result = response["results"][0]
    assert result["ok"] is True
    assert result["encoding"] == "bwry_packed"


async def test_the_preview_survives_as_the_panel_image(
    hass: HomeAssistant, config_entry, mock_bluetooth
) -> None:
    """The image entity has to show what was just drawn."""
    device = await _setup(hass, config_entry)
    await _call(
        hass,
        {"device_id": _device_id(hass, config_entry), "payload": PAYLOAD},
        [],
    )

    from PIL import Image

    preview = Image.open(BytesIO(device.state.last_image_png))
    assert preview.size == (184, 384)
    assert preview.getpixel((90, 10)) == (255, 0, 0)
    assert device.state.last_image_source == "drawcustom (2 elements)"


async def test_a_whole_exported_block_is_accepted(
    hass: HomeAssistant, config_entry, mock_bluetooth
) -> None:
    """A Designer export carries its options alongside the elements."""
    device = await _setup(hass, config_entry)
    await _call(
        hass,
        {
            "device_id": _device_id(hass, config_entry),
            "payload": {"background": "yellow", "rotate": 0, "payload": []},
        },
        [],
    )

    from PIL import Image

    preview = Image.open(BytesIO(device.state.last_image_png))
    assert preview.getpixel((10, 10)) == (255, 255, 0)


async def test_an_explicit_background_beats_the_one_in_the_payload(
    hass: HomeAssistant, config_entry, mock_bluetooth
) -> None:
    device = await _setup(hass, config_entry)
    await _call(
        hass,
        {
            "device_id": _device_id(hass, config_entry),
            "background": "red",
            "payload": {"background": "yellow", "payload": []},
        },
        [],
    )

    from PIL import Image

    assert Image.open(BytesIO(device.state.last_image_png)).getpixel((10, 10)) == (
        255,
        0,
        0,
    )


async def test_json_text_from_a_template_is_accepted(
    hass: HomeAssistant, config_entry, mock_bluetooth
) -> None:
    """A template renders to a string, so a string has to work."""
    await _setup(hass, config_entry)
    uploaded: list[bytes] = []

    await _call(
        hass,
        {
            "device_id": _device_id(hass, config_entry),
            "payload": '[{"type": "text", "value": "hi", "x": 0, "y": 0}]',
        },
        uploaded,
    )

    assert len(raw_payload(uploaded[0])) == 184 * 384 // 4


async def test_a_bad_element_is_refused_before_any_radio_work(
    hass: HomeAssistant, config_entry, mock_bluetooth
) -> None:
    """A typo must come back at once, not after a minute of connecting."""
    await _setup(hass, config_entry)
    uploaded: list[bytes] = []

    with pytest.raises(ServiceValidationError, match="unknown type"):
        await _call(
            hass,
            {
                "device_id": _device_id(hass, config_entry),
                "payload": [{"type": "sparkline"}],
            },
            uploaded,
        )
    assert uploaded == []


async def test_plot_says_what_to_do_instead(
    hass: HomeAssistant, config_entry, mock_bluetooth
) -> None:
    await _setup(hass, config_entry)
    with pytest.raises(ServiceValidationError, match="dlimg"):
        await _call(
            hass,
            {
                "device_id": _device_id(hass, config_entry),
                "payload": [{"type": "plot", "x_start": 0}],
            },
            [],
        )


async def test_a_dlimg_element_is_downloaded_first(
    hass: HomeAssistant, config_entry, mock_bluetooth, aioclient_mock
) -> None:
    """The renderer never touches the network, so the service must."""
    from PIL import Image

    device = await _setup(hass, config_entry)
    buffer = BytesIO()
    Image.new("RGB", (20, 20), (255, 0, 0)).save(buffer, format="PNG")
    url = "https://example.com/cake.png"
    aioclient_mock.get(url, content=buffer.getvalue())

    await _call(
        hass,
        {
            "device_id": _device_id(hass, config_entry),
            "payload": [{"type": "dlimg", "url": url, "x": 0, "y": 0}],
        },
        [],
    )

    assert aioclient_mock.call_count == 1
    preview = Image.open(BytesIO(device.state.last_image_png))
    assert preview.getpixel((10, 10)) == (255, 0, 0)


async def test_a_relative_url_is_resolved_against_home_assistant(
    hass: HomeAssistant, config_entry, mock_bluetooth, aioclient_mock
) -> None:
    """So a payload can point at /local/... the way a dashboard does."""
    from PIL import Image

    await hass.config.async_update(internal_url="http://homeassistant.local:8123")
    await _setup(hass, config_entry)
    buffer = BytesIO()
    Image.new("RGB", (8, 8), (0, 0, 0)).save(buffer, format="PNG")
    aioclient_mock.get(
        "http://homeassistant.local:8123/local/cake.png", content=buffer.getvalue()
    )

    await _call(
        hass,
        {
            "device_id": _device_id(hass, config_entry),
            "payload": [{"type": "dlimg", "url": "/local/cake.png", "x": 0, "y": 0}],
        },
        [],
    )

    assert aioclient_mock.call_count == 1


async def test_rotation_is_validated(
    hass: HomeAssistant, config_entry, mock_bluetooth
) -> None:
    await _setup(hass, config_entry)
    with pytest.raises(Exception, match="rotate"):
        await _call(
            hass,
            {
                "device_id": _device_id(hass, config_entry),
                "rotate": 45,
                "payload": [],
            },
            [],
        )
