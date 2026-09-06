"""Integration wide services for the Zhsunyco ESL integration."""

from __future__ import annotations

import logging

import voluptuous as vol
from homeassistant.core import HomeAssistant, ServiceCall, callback
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
    SERVICE_DEBUG_PROBE,
    SERVICE_SET_IMAGE,
    SERVICE_SET_RGB,
)
from .device import ESLDevice
from .imaging import ImageRequest

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

SET_IMAGE_SCHEMA = _DEVICE_SELECTOR.extend(
    {
        vol.Required("path"): cv.string,
        vol.Optional("rotate", default=0): vol.All(
            vol.Coerce(int), vol.In([0, 90, 180, 270])
        ),
        vol.Optional("invert", default=False): cv.boolean,
        vol.Optional("dither", default=True): cv.boolean,
    }
)


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

    async def _set_rgb(call: ServiceCall) -> None:
        red, green, blue = call.data["rgb_color"]
        for device in _resolve_devices(hass, call):
            await device.async_set_rgb(
                red,
                green,
                blue,
                call.data.get("on_ms"),
                call.data.get("off_ms"),
                call.data.get("work_ms"),
            )

    async def _clear_screen(call: ServiceCall) -> None:
        for device in _resolve_devices(hass, call):
            await device.async_clear_screen()

    async def _debug_probe(call: ServiceCall) -> None:
        """Collect a GATT report and surface it as a persistent notification."""
        import json

        from homeassistant.components import persistent_notification

        for device in _resolve_devices(hass, call):
            report = await device.async_probe()
            pretty = json.dumps(report, indent=2, default=str)
            _LOGGER.warning("ESL probe %s:\n%s", device.address, pretty)
            persistent_notification.async_create(
                hass,
                f"```json\n{pretty}\n```",
                title=f"ESL probe {device.address}",
                notification_id=f"{DOMAIN}_probe_{device.address}",
            )

    async def _set_image(call: ServiceCall) -> None:
        path = call.data["path"]
        if not hass.config.is_allowed_path(path):
            raise ServiceValidationError(
                f"Path {path} is not allowed, add it to allowlist_external_dirs"
            )
        request = ImageRequest(
            path=path,
            rotate=call.data["rotate"],
            invert=call.data["invert"],
            dither=call.data["dither"],
        )
        for device in _resolve_devices(hass, call):
            await device.async_send_image(request)

    hass.services.async_register(
        DOMAIN, SERVICE_SET_RGB, _set_rgb, schema=SET_RGB_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_CLEAR_SCREEN, _clear_screen, schema=CLEAR_SCREEN_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_SET_IMAGE, _set_image, schema=SET_IMAGE_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_DEBUG_PROBE, _debug_probe, schema=DEBUG_PROBE_SCHEMA
    )
