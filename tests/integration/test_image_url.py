"""Sending an image the integration fetches over HTTP."""

from __future__ import annotations

from io import BytesIO
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import device_registry as dr
from homeassistant.util import dt as dt_util

from custom_components.esl_zhsunyco.const import DOMAIN, SERVICE_SET_IMAGE

from .conftest import raw_payload

URL = "https://example.com/calendar.png"


def _png(width: int = 40, height: int = 60) -> bytes:
    from PIL import Image

    image = Image.new("RGB", (width, height), (255, 0, 0))
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


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
            DOMAIN, SERVICE_SET_IMAGE, data, blocking=True, return_response=True
        )


async def test_an_image_is_downloaded_and_sent(
    hass: HomeAssistant, config_entry, mock_bluetooth, aioclient_mock
) -> None:
    """The whole point: point the service at a URL and the panel gets it."""
    device = await _setup(hass, config_entry)
    aioclient_mock.get(URL, content=_png())
    uploaded: list[bytes] = []

    response = await _call(
        hass, {"device_id": _device_id(hass, config_entry), "url": URL}, uploaded
    )

    # Scaled to the panel and packed, exactly as a local file would be.
    assert len(raw_payload(uploaded[0])) == 184 * 384 // 4
    assert response["results"][0]["ok"] is True
    # The URL names the image, since downloaded bytes have no path.
    assert device.state.last_image_source == URL


async def test_a_failed_download_never_reaches_the_label(
    hass: HomeAssistant, config_entry, mock_bluetooth, aioclient_mock
) -> None:
    """A 404 must be a clear error, not a blank panel."""
    await _setup(hass, config_entry)
    aioclient_mock.get(URL, status=404)
    uploaded: list[bytes] = []

    with pytest.raises(ServiceValidationError, match="404"):
        await _call(
            hass, {"device_id": _device_id(hass, config_entry), "url": URL}, uploaded
        )

    assert uploaded == []


async def test_an_oversized_download_is_refused(
    hass: HomeAssistant, config_entry, mock_bluetooth, aioclient_mock
) -> None:
    """A wrong URL must not pull an unbounded body into memory."""
    from custom_components.esl_zhsunyco.services import MAX_DOWNLOAD_BYTES

    await _setup(hass, config_entry)
    aioclient_mock.get(URL, content=b"\x00" * (MAX_DOWNLOAD_BYTES + 10))
    uploaded: list[bytes] = []

    with pytest.raises(ServiceValidationError, match="larger than"):
        await _call(
            hass, {"device_id": _device_id(hass, config_entry), "url": URL}, uploaded
        )

    assert uploaded == []


async def test_a_non_image_body_is_refused(
    hass: HomeAssistant, config_entry, mock_bluetooth, aioclient_mock
) -> None:
    """An HTML error page returned with HTTP 200 must not look like success."""
    await _setup(hass, config_entry)
    aioclient_mock.get(URL, content=b"<html>not an image</html>")
    uploaded: list[bytes] = []

    with pytest.raises(Exception):  # noqa: B017 - Pillow decides the type
        await _call(
            hass, {"device_id": _device_id(hass, config_entry), "url": URL}, uploaded
        )

    assert uploaded == []


@pytest.mark.parametrize(
    "data",
    [
        {},
        {"path": "/config/a.png", "url": URL},
        {"url": "ftp://example.com/a.png"},
    ],
    ids=["neither", "both", "wrong scheme"],
)
async def test_the_source_must_be_exactly_one_usable_thing(
    hass: HomeAssistant, config_entry, mock_bluetooth, data
) -> None:
    """Ambiguous or unusable input has to be refused before any connection."""
    await _setup(hass, config_entry)
    uploaded: list[bytes] = []

    with pytest.raises(Exception):  # noqa: B017 - vol.Invalid or ServiceValidationError
        await _call(
            hass, {"device_id": _device_id(hass, config_entry), **data}, uploaded
        )

    assert uploaded == []
