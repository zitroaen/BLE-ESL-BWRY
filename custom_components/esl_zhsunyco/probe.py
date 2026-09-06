"""Shared helper that runs a diagnostic probe and surfaces the result."""

from __future__ import annotations

import json
import logging
from typing import Any

from homeassistant.components import persistent_notification
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .device import ESLDevice

_LOGGER = logging.getLogger(__name__)

# Notifications are rendered as markdown; a very long report would be
# unreadable there, so it is truncated and the full copy goes to the log and
# the diagnostics download.
MAX_NOTIFICATION_CHARS = 12000


async def async_probe_and_notify(
    hass: HomeAssistant, device: ESLDevice
) -> dict[str, Any]:
    """Run the probe and present it as a notification and a log entry."""
    report = await device.async_probe()
    pretty = json.dumps(report, indent=2, default=str)

    # Warning level on purpose: this is user requested diagnostics and has to
    # show up in a default log download without enabling debug logging first.
    _LOGGER.warning("ESL probe %s:\n%s", device.address, pretty)

    body = pretty
    if len(body) > MAX_NOTIFICATION_CHARS:
        body = (
            body[:MAX_NOTIFICATION_CHARS]
            + "\n... truncated, see the log or the diagnostics download"
        )

    persistent_notification.async_create(
        hass,
        f"```json\n{body}\n```",
        title=f"ESL probe {device.address}",
        notification_id=f"{DOMAIN}_probe_{device.address}",
    )
    return report
