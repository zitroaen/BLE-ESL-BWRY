"""Integration wide services for the Zhsunyco ESL integration."""

from __future__ import annotations

import logging

import aiohttp
import voluptuous as vol
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
    callback,
)
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    DOMAIN,
    RGB_OFF_MS_MAX,
    RGB_OFF_MS_MIN,
    RGB_ON_MS_MAX,
    RGB_ON_MS_MIN,
    RGB_WORK_MS_MAX,
    RGB_WORK_MS_MIN,
    SERVICE_CLEAR_SCREEN,
    SERVICE_DEBUG_COMMAND,
    SERVICE_DEBUG_PROBE,
    SERVICE_SEND_TEST_PATTERN,
    SERVICE_SET_IMAGE,
    SERVICE_SET_RGB,
)
from .device import ESLDevice
from .imaging import ImageRequest
from .patterns import DEFAULT_PATTERN, PATTERNS
from .probe import async_probe_and_notify

_LOGGER = logging.getLogger(__name__)

ATTR_DEVICE_ID = "device_id"

_DEVICE_SELECTOR = vol.Schema(
    {vol.Required(ATTR_DEVICE_ID): vol.All(cv.ensure_list, [cv.string])},
    extra=vol.ALLOW_EXTRA,
)

SET_RGB_SCHEMA = _DEVICE_SELECTOR.extend(
    {
        vol.Required("rgb_color"): vol.All(
            cv.ensure_list,
            [vol.All(vol.Coerce(int), vol.Range(0, 255))],
            vol.Length(3, 3),
        ),
        vol.Optional("on_ms"): vol.All(
            vol.Coerce(int), vol.Range(RGB_ON_MS_MIN, RGB_ON_MS_MAX)
        ),
        vol.Optional("off_ms"): vol.All(
            vol.Coerce(int), vol.Range(RGB_OFF_MS_MIN, RGB_OFF_MS_MAX)
        ),
        vol.Optional("work_ms"): vol.All(
            vol.Coerce(int), vol.Range(RGB_WORK_MS_MIN, RGB_WORK_MS_MAX)
        ),
    }
)

CLEAR_SCREEN_SCHEMA = _DEVICE_SELECTOR
DEBUG_PROBE_SCHEMA = _DEVICE_SELECTOR

DEBUG_COMMAND_SCHEMA = _DEVICE_SELECTOR.extend(
    {
        vol.Required("payload"): cv.string,
        vol.Optional("expect_response", default=True): cv.boolean,
    }
)

# Shared knobs. The pixel format itself is settled and no longer adjustable;
# what is left is how the source picture should be fitted onto the panel.
_ENCODING_FIELDS = {
    vol.Optional("rotate", default=0): vol.All(
        vol.Coerce(int), vol.In([0, 90, 180, 270])
    ),
    vol.Optional("mirror", default=False): cv.boolean,
    vol.Optional("invert", default=False): cv.boolean,
    vol.Optional("dither", default=True): cv.boolean,
}


def _drop_blank_sources(data: dict) -> dict:
    """Treat an empty path or url as not given at all.

    The Home Assistant UI submits every field it renders, blank ones
    included, so filling in one of the two arrives as the other being
    present and empty. Removing them here, before the field validators
    run, is what lets an empty url stay out of the way of cv.url.
    """
    if not isinstance(data, dict):
        return data
    cleaned = dict(data)
    for key in ("path", "url"):
        value = cleaned.get(key)
        if value is None or (isinstance(value, str) and not value.strip()):
            cleaned.pop(key, None)
    return cleaned


def _exactly_one_source(data: dict) -> dict:
    """Require a path or a URL, and refuse both at once."""
    if ("path" in data) == ("url" in data):
        raise vol.Invalid("give either path or url, not both and not neither")
    return data


SET_IMAGE_SCHEMA = vol.All(
    _drop_blank_sources,
    _DEVICE_SELECTOR.extend(
        {
            vol.Optional("path"): cv.string,
            vol.Optional("url"): vol.All(cv.string, cv.url),
            **_ENCODING_FIELDS,
        }
    ),
    _exactly_one_source,
)

SEND_TEST_PATTERN_SCHEMA = _DEVICE_SELECTOR.extend(
    {
        vol.Optional("pattern", default=DEFAULT_PATTERN): vol.In(PATTERNS),
        **_ENCODING_FIELDS,
    }
)


# A full panel is 17664 bytes, so anything remotely reasonable fits easily.
# The cap is only here so a wrong URL cannot pull an arbitrarily large body
# into memory before Pillow ever sees it.
MAX_DOWNLOAD_BYTES = 8 * 1024 * 1024
DOWNLOAD_TIMEOUT_S = 30
_READ_CHUNK = 64 * 1024


async def _fetch_image(hass: HomeAssistant, url: str) -> bytes:
    """Download an image, with the failure modes spelled out.

    Home Assistant does not allowlist outgoing URLs the way it allowlists
    file paths, and neither do its own image entities, so this does not
    either - the caller is already an authenticated user. What it does do
    is refuse anything that is not http(s), give up after a timeout instead
    of hanging a service call, and stop reading past the size cap.
    """
    if not url.lower().startswith(("http://", "https://")):
        raise ServiceValidationError(f"url must be http or https, got {url!r}")

    session = async_get_clientsession(hass)
    try:
        response = await session.get(
            url, timeout=aiohttp.ClientTimeout(total=DOWNLOAD_TIMEOUT_S)
        )
        async with response:
            if response.status != 200:
                raise ServiceValidationError(f"{url} returned HTTP {response.status}")
            # Read to the end, in pieces, capping as we go. Not read(n):
            # that returns whatever happens to be buffered, so a body that
            # arrives in several TCP segments - anything of size, over a
            # real network - comes back truncated with no error at all,
            # and a half a PNG then fails much later as a broken image.
            buffer = bytearray()
            async for chunk in response.content.iter_chunked(_READ_CHUNK):
                buffer.extend(chunk)
                if len(buffer) > MAX_DOWNLOAD_BYTES:
                    raise ServiceValidationError(
                        f"{url} is larger than the {MAX_DOWNLOAD_BYTES} byte limit"
                    )
            data = bytes(buffer)
    except TimeoutError as err:
        raise ServiceValidationError(
            f"{url} did not respond within {DOWNLOAD_TIMEOUT_S}s"
        ) from err
    except aiohttp.ClientError as err:
        raise ServiceValidationError(f"Could not fetch {url}: {err}") from err

    if not data:
        raise ServiceValidationError(f"{url} returned an empty body")
    return data


def _outcome(device: ESLDevice, record: dict) -> dict:
    """Boil one command record down to what an automation needs.

    A failed write raises, so an automation already notices that. This
    carries the case an exception cannot express: the label accepted the
    write and the panel never went busy, which means it did not act on it.
    """
    status = record.get("status_after") or {}
    return {
        "address": device.address,
        "ok": record.get("result") == "ok",
        # The panel reported busy, so it really started drawing.
        "label_reacted": bool(record.get("label_reacted")),
        "connection_dropped": bool(record.get("connection_dropped")),
        "error_code": (status.get("final") or {}).get("error_code"),
        "detail": record.get("detail") or record.get("interpretation"),
        "at": record.get("at"),
        # Image sends add these; nothing else in the record carries them.
        **{
            key: record[key]
            for key in ("bytes", "sent_bytes", "compressed", "encoding")
            if key in record
        },
    }


def _resolve_devices(hass: HomeAssistant, call: ServiceCall) -> list[ESLDevice]:
    """Map the service target onto our device objects."""
    registry = dr.async_get(hass)
    devices: list[ESLDevice] = []

    for device_id in call.data[ATTR_DEVICE_ID]:
        entry_device = registry.async_get(device_id)
        if entry_device is None:
            raise ServiceValidationError(f"Unknown device id {device_id}")

        for entry_id in entry_device.config_entries:
            device = hass.data.get(DOMAIN, {}).get(entry_id)
            if device is not None:
                devices.append(device)
                break
        else:
            raise ServiceValidationError(
                f"Device {device_id} does not belong to {DOMAIN}"
            )

    if not devices:
        raise ServiceValidationError("No ESL device selected")
    return devices


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register the integration services once."""
    if hass.services.has_service(DOMAIN, SERVICE_SET_RGB):
        return

    async def _set_rgb(call: ServiceCall) -> ServiceResponse:
        red, green, blue = call.data["rgb_color"]
        results = []
        for device in _resolve_devices(hass, call):
            record = await device.async_set_rgb(
                red,
                green,
                blue,
                call.data.get("on_ms"),
                call.data.get("off_ms"),
                call.data.get("work_ms"),
            )
            results.append(_outcome(device, record))
        return {"results": results}

    async def _clear_screen(call: ServiceCall) -> ServiceResponse:
        results = []
        for device in _resolve_devices(hass, call):
            results.append(_outcome(device, await device.async_clear_screen()))
        return {"results": results}

    async def _debug_probe(call: ServiceCall) -> None:
        """Collect a GATT report and surface it as a persistent notification."""
        for device in _resolve_devices(hass, call):
            await async_probe_and_notify(hass, device)

    async def _debug_command(call: ServiceCall) -> ServiceResponse:
        """Write raw bytes to the command characteristic.

        The document leaves the exact command encoding open in places, so
        this makes trying a variant a one line service call instead of a
        release. Example payload: "a5 04" for clear screen.
        """
        text = call.data["payload"].replace(" ", "").replace(":", "")
        try:
            payload = bytes.fromhex(text)
        except ValueError as err:
            raise ServiceValidationError(
                f"payload must be hex, got {call.data['payload']!r}"
            ) from err
        if not payload:
            raise ServiceValidationError("payload must not be empty")

        results = []
        for device in _resolve_devices(hass, call):
            record = await device.async_send_raw_command(
                payload, expect_response=call.data["expect_response"]
            )
            results.append(_outcome(device, record))
        return {"results": results}

    def _request_from(call: ServiceCall, **source) -> ImageRequest:
        return ImageRequest(
            rotate=call.data["rotate"],
            mirror=call.data["mirror"],
            invert=call.data["invert"],
            dither=call.data["dither"],
            **source,
        )

    async def _set_image(call: ServiceCall) -> ServiceResponse:
        if url := call.data.get("url"):
            request = _request_from(call, data=await _fetch_image(hass, url))
            # So the image entity and the response can say where it came from.
            request.source_name = url
        else:
            path = call.data["path"]
            if not hass.config.is_allowed_path(path):
                raise ServiceValidationError(
                    f"Path {path} is not allowed, add it to allowlist_external_dirs"
                )
            request = _request_from(call, path=path)
        results = []
        for device in _resolve_devices(hass, call):
            results.append(_outcome(device, await device.async_send_image(request)))
        return {"results": results}

    async def _send_test_pattern(call: ServiceCall) -> ServiceResponse:
        """Send a built-in pattern, no file and no allowlist needed."""
        request = _request_from(call, pattern=call.data["pattern"])
        # A test pattern is drawn at panel resolution already; fitting it
        # would letterbox and hide exactly the edges being tested.
        request.stretch = True
        results = []
        for device in _resolve_devices(hass, call):
            results.append(_outcome(device, await device.async_send_image(request)))
        return {"results": results}

    hass.services.async_register(
        DOMAIN,
        SERVICE_SET_RGB,
        _set_rgb,
        schema=SET_RGB_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_CLEAR_SCREEN,
        _clear_screen,
        schema=CLEAR_SCREEN_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_SET_IMAGE,
        _set_image,
        schema=SET_IMAGE_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN, SERVICE_DEBUG_PROBE, _debug_probe, schema=DEBUG_PROBE_SCHEMA
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_DEBUG_COMMAND,
        _debug_command,
        schema=DEBUG_COMMAND_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_SEND_TEST_PATTERN,
        _send_test_pattern,
        schema=SEND_TEST_PATTERN_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
