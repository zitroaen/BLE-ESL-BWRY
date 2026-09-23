"""Diagnostics support for the Zhsunyco ESL integration."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .device import ESLDevice
from .drawcustom import ASSETS
from .protocol import BATTERY_CANDIDATES

_LOGGER = logging.getLogger(__name__)

TO_REDACT = {"address"}

# Downloading diagnostics deliberately does NOT connect. It used to run a
# probe automatically, which was an active operation behind a passive looking
# action: it occupied a proxy connection slot, kept the label connected so it
# stopped advertising, and its timeout cancelled the attempt mid-connect. Use
# the "Debug probe" button when a probe is wanted; the result is cached here.
NO_PROBE_HINT = (
    "no probe cached; press the Debug probe button on the device to collect one"
)


def _assets() -> dict[str, Any]:
    """Which bundled files are on disk, and how big.

    Worth reporting because a partial install fails quietly: a missing
    icon font is an error, but a missing text font only falls back to
    Pillow's own, which draws every umlaut as an empty box.
    """
    wanted = (
        "Roboto-Regular.ttf",
        "Roboto-Bold.ttf",
        "materialdesignicons-webfont.ttf",
        "mdi-codepoints.json",
    )
    found = {}
    for name in wanted:
        path = ASSETS / name
        found[name] = path.stat().st_size if path.is_file() else "MISSING"
    return found


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return everything needed to debug a label without shell access."""
    device: ESLDevice = entry.runtime_data
    state = device.state

    return {
        "entry": {
            "version": entry.version,
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": dict(entry.options),
        },
        "assets": _assets(),
        "panel": {
            "model": device.model,
            "width": device.width,
            "height": device.height,
            "pixel_format": device.pixel_format,
        },
        "state": {
            "available": device.available,
            "battery_mv": state.battery_mv,
            "battery_v": device.battery_v,
            "pid": state.pid,
            "app_version": state.app_version,
            "hw_version": state.hw_version,
            "disp_version": state.disp_version,
            "busy": state.busy,
            "error": state.error,
            "error_text": state.error_text,
            "rssi": state.rssi,
            "last_advert": state.last_advert.isoformat() if state.last_advert else None,
            "last_connect_ok": state.last_connect_ok,
            "connection_held_open": device.connected,
            "linger_seconds": device.linger_seconds,
            "unlock_verified": state.unlock_verified,
            "last_unlock_status": state.last_unlock_status,
            "notifications": list(state.notifications),
            "last_error_message": state.last_error_message,
        },
        "coordinator": {
            "last_update_success": device.coordinator.last_update_success,
            "update_interval_s": (
                device.coordinator.update_interval.total_seconds()
                if device.coordinator.update_interval
                else None
            ),
        },
        "bluetooth": device.bluetooth_report(),
        "advertisement": {
            "raw_by_company_id": dict(state.advert_raw),
            "decoded": dict(state.advert_decoded),
        },
        "battery_candidate_meanings": BATTERY_CANDIDATES,
        "last_command": state.last_command,
        "last_probe": state.last_probe if state.last_probe else NO_PROBE_HINT,
    }
