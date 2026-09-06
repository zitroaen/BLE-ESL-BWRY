"""Diagnostics support for the Zhsunyco ESL integration."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .device import ESLDevice
from .protocol import BATTERY_CANDIDATES

_LOGGER = logging.getLogger(__name__)

TO_REDACT = {"address"}

# A probe needs a connection; keep it well inside any frontend timeout.
PROBE_TIMEOUT = 25.0


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return everything needed to debug a label without shell access."""
    device: ESLDevice = entry.runtime_data
    state = device.state

    # Run a probe if none is cached. Downloading diagnostics is the step a
    # user actually performs, so it should not silently omit the one section
    # that answers which protocol the label speaks.
    if state.last_probe is None:
        try:
            async with asyncio.timeout(PROBE_TIMEOUT):
                await device.async_probe()
        except TimeoutError:
            _LOGGER.warning("Probe for diagnostics timed out after %ss", PROBE_TIMEOUT)
            state.last_probe = {"error": f"probe timed out after {PROBE_TIMEOUT}s"}

    return {
        "entry": {
            "version": entry.version,
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": dict(entry.options),
        },
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
        "advertisement": {
            "raw_by_company_id": dict(state.advert_raw),
            "decoded": dict(state.advert_decoded),
        },
        "battery_candidate_meanings": BATTERY_CANDIDATES,
        "last_probe": state.last_probe,
    }
