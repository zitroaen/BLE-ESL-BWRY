"""Persistence for the picture of what a label is displaying.

An e-ink panel keeps its image with no power and cannot be read back, so
after a Home Assistant restart the only record of what a label shows is the
one we kept. That makes it worth storing: the panel is still displaying it.

The stored copy is only as true as its premise - that nothing else writes
to this label. Clearing the screen from somewhere else, or a factory reset,
would leave it stale, which is why a clear through this integration drops
it rather than leaving a picture of a screen that is now blank.
"""

from __future__ import annotations

import base64
import binascii
import logging
from datetime import datetime
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1

# A four colour panel compresses well: about 1 kB for graphics with flat
# areas, around 14 kB for a fully dithered photo. Small enough to keep in
# .storage as base64 rather than managing a second file next to it.
_KEY_PNG = "png_base64"
_KEY_AT = "at"
_KEY_SOURCE = "source"


class PanelImageStore:
    """Holds one label's last uploaded image across restarts."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        """Create a store scoped to one config entry."""
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, f"{DOMAIN}.{entry_id}.panel"
        )

    async def async_load(self) -> tuple[bytes, datetime, str | None] | None:
        """Return the stored image, or None if there is nothing usable.

        Never raises. A store that cannot be read is a reason to show no
        picture, not a reason to stop the integration from starting.
        """
        try:
            data = await self._store.async_load()
        except Exception as err:  # noqa: BLE001 - a bad store must not block setup
            _LOGGER.warning("Could not read the stored panel image: %s", err)
            return None

        if not data:
            return None

        try:
            png = base64.b64decode(data[_KEY_PNG], validate=True)
            at = dt_util.parse_datetime(data[_KEY_AT])
        except (KeyError, TypeError, ValueError, binascii.Error) as err:
            _LOGGER.warning("Stored panel image is unusable, ignoring it: %s", err)
            return None

        if not png or at is None:
            _LOGGER.warning("Stored panel image is incomplete, ignoring it")
            return None

        return png, at, data.get(_KEY_SOURCE)

    async def async_save(self, png: bytes, at: datetime, source: str | None) -> None:
        """Write the image that is now on the panel."""
        await self._store.async_save(
            {
                _KEY_PNG: base64.b64encode(png).decode("ascii"),
                _KEY_AT: at.isoformat(),
                _KEY_SOURCE: source,
            }
        )

    async def async_clear(self) -> None:
        """Forget the image, because the panel is no longer showing it."""
        await self._store.async_remove()
