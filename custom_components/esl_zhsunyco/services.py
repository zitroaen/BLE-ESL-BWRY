"""Integration wide services for the Zhsunyco ESL integration."""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from functools import partial

import voluptuous as vol
from homeassistant.components import persistent_notification
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

from .const import (
    DOMAIN,
    RGB_OFF_MS_MAX,
    RGB_OFF_MS_MIN,
    RGB_ON_MS_MAX,
    RGB_ON_MS_MIN,
    RGB_WORK_MS_MAX,
    RGB_WORK_MS_MIN,
    SERVICE_CLEAR_SCREEN,
    SERVICE_COMMAND_SWEEP,
    SERVICE_DEBUG_COMMAND,
    SERVICE_DEBUG_PROBE,
    SERVICE_SEND_TEST_PATTERN,
    SERVICE_SET_IMAGE,
    SERVICE_SET_RGB,
)
from .device import ESLDevice
from .imaging import BIT_ORDERS, ENCODINGS, ImageRequest
from .patterns import DEFAULT_PATTERN, PATTERNS
from .probe import async_probe_and_notify
from .protocol import clear_screen_candidates, command_variants

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

COMMAND_SWEEP_SCHEMA = _DEVICE_SELECTOR.extend(
    {
        vol.Optional("preset", default="clear_screen"): vol.In(
            ["clear_screen", "opcode"]
        ),
        vol.Optional("opcode", default="A504"): cv.string,
        vol.Optional("payloads"): vol.All(cv.ensure_list, [cv.string]),
        vol.Optional("settle", default=1.5): vol.All(
            vol.Coerce(float), vol.Range(0.1, 10.0)
        ),
    }
)

DEBUG_COMMAND_SCHEMA = _DEVICE_SELECTOR.extend(
    {
        vol.Required("payload"): cv.string,
        vol.Optional("expect_response", default=True): cv.boolean,
    }
)

# Shared knobs: the pixel format is undocumented, so every plausible variant
# has to be reachable without a new release.
_ENCODING_FIELDS = {
    vol.Optional("encoding", default="auto"): vol.In(ENCODINGS),
    vol.Optional("bit_order", default="msb"): vol.In(BIT_ORDERS),
    vol.Optional("rotate", default=0): vol.All(
        vol.Coerce(int), vol.In([0, 90, 180, 270])
    ),
    vol.Optional("mirror", default=False): cv.boolean,
    vol.Optional("invert", default=False): cv.boolean,
    vol.Optional("dither", default=True): cv.boolean,
}

SET_IMAGE_SCHEMA = _DEVICE_SELECTOR.extend(
    {vol.Required("path"): cv.string, **_ENCODING_FIELDS}
)

SEND_TEST_PATTERN_SCHEMA = _DEVICE_SELECTOR.extend(
    {
        vol.Optional("pattern", default=DEFAULT_PATTERN): vol.In(PATTERNS),
        **_ENCODING_FIELDS,
    }
)


def _notify(
    hass: HomeAssistant, *, title: str, notification_id: str, body: str
) -> None:
    """Post a persistent notification, the only channel a sweep can report on."""
    persistent_notification.async_create(
        hass, body, title=title, notification_id=notification_id
    )


def _run_detached(
    hass: HomeAssistant,
    device: ESLDevice,
    *,
    label: str,
    notification_id: str,
    factory: Callable[[], Awaitable[dict]],
) -> None:
    """Run a sweep in the background and report through notifications.

    A sweep wakes the label once per candidate and a sleeping label can take
    three minutes to advertise, so a full sweep runs for many minutes. Awaiting
    that inside the service call means the caller - the frontend, a script,
    an automation - sits on an open call for the whole time and gives up long
    before the label does; the error it then shows has no text at all, which
    is where the bare "undefined" came from. The work itself was fine, nobody
    was left to receive it.
    """
    title = f"ESL {label} {device.address}"
    _notify(
        hass,
        title=title,
        notification_id=notification_id,
        body=(
            "Running. Waking a sleeping label can take three minutes per "
            "reconnect, so this may run for a while. The result replaces "
            "this notification."
        ),
    )

    async def _runner() -> None:
        try:
            report = await factory()
        except Exception as err:  # noqa: BLE001 - the report IS the result
            # Never let this surface as an empty message: some BLE timeouts
            # stringify to "" and the frontend renders that as "undefined".
            _LOGGER.exception("ESL %s on %s failed", label, device.address)
            body = f"Failed: {type(err).__name__}: {err or 'no detail'}"
        else:
            body = "```json\n" + json.dumps(report, indent=2, default=str) + "\n```"
        _notify(hass, title=title, notification_id=notification_id, body=body)

    hass.async_create_background_task(
        _runner(), f"{DOMAIN} {label} {device.address}", eager_start=False
    )


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
        **{k: record[k] for k in ("bytes", "encoding") if k in record},
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

    async def _debug_command(call: ServiceCall) -> None:
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

        for device in _resolve_devices(hass, call):
            await device.async_send_raw_command(
                payload, expect_response=call.data["expect_response"]
            )

    def _request_from(call: ServiceCall, **source) -> ImageRequest:
        return ImageRequest(
            encoding=call.data["encoding"],
            bit_order=call.data["bit_order"],
            rotate=call.data["rotate"],
            mirror=call.data["mirror"],
            invert=call.data["invert"],
            dither=call.data["dither"],
            **source,
        )

    async def _command_sweep(call: ServiceCall) -> None:
        """Try several command encodings in one wake-up and report the deltas."""
        if raw_payloads := call.data.get("payloads"):
            try:
                payloads = [
                    bytes.fromhex(item.replace(" ", "").replace(":", ""))
                    for item in raw_payloads
                ]
            except ValueError as err:
                raise ServiceValidationError(f"payloads must be hex: {err}") from err
        elif call.data["preset"] == "clear_screen":
            # Both documented clear paths rather than byte permutations of one.
            payloads = clear_screen_candidates()
        else:
            try:
                opcode = int(call.data["opcode"].replace("0x", ""), 16)
            except ValueError as err:
                raise ServiceValidationError(
                    f"opcode must be hex, got {call.data['opcode']!r}"
                ) from err
            payloads = command_variants(opcode)

        if not payloads:
            raise ServiceValidationError("nothing to send")

        for device in _resolve_devices(hass, call):
            _run_detached(
                hass,
                device,
                label="command sweep",
                notification_id=f"{DOMAIN}_sweep_{device.address}",
                factory=partial(
                    device.async_command_sweep, payloads, settle=call.data["settle"]
                ),
            )

    async def _set_image(call: ServiceCall) -> ServiceResponse:
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
        DOMAIN, SERVICE_DEBUG_COMMAND, _debug_command, schema=DEBUG_COMMAND_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_COMMAND_SWEEP, _command_sweep, schema=COMMAND_SWEEP_SCHEMA
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_SEND_TEST_PATTERN,
        _send_test_pattern,
        schema=SEND_TEST_PATTERN_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
