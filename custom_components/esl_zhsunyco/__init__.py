"""The Zhsunyco ESL integration."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from .const import DOMAIN
from .device import ESLDevice
from .services import async_setup_services

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.BUTTON,
    Platform.LIGHT,
    Platform.NUMBER,
    Platform.SENSOR,
]

if TYPE_CHECKING:
    ESLConfigEntry = ConfigEntry[ESLDevice]
else:
    ESLConfigEntry = ConfigEntry


async def async_setup_entry(hass: HomeAssistant, entry: ESLConfigEntry) -> bool:
    """Set up one label from a config entry."""
    device = ESLDevice(hass, entry)

    try:
        await device.async_setup()
    except ConfigEntryNotReady:
        await device.async_unload()
        raise
    except Exception as err:
        await device.async_unload()
        raise ConfigEntryNotReady(f"Could not set up {device.address}: {err}") from err

    entry.runtime_data = device
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = device

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    async_setup_services(hass)

    entry.async_on_unload(entry.add_update_listener(async_update_options))
    return True


async def async_update_options(hass: HomeAssistant, entry: ESLConfigEntry) -> None:
    """Apply changed options without a full reload."""
    device: ESLDevice = entry.runtime_data
    device.async_update_interval()
    await device.coordinator.async_request_refresh()


async def async_unload_entry(hass: HomeAssistant, entry: ESLConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        device: ESLDevice = entry.runtime_data
        await device.async_unload()
        hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    return unload_ok
