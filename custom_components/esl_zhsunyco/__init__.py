"""The Zhsunyco ESL integration."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from .const import (
    CONF_ADDRESS,
    CONF_MODEL,
    CONF_SCAN_INTERVAL_MIN,
    DEFAULT_MODEL,
    DEFAULT_SCAN_INTERVAL_MIN,
    DOMAIN,
    LEGACY_CONF_BATTERY_INTERVAL,
    LEGACY_CONF_MAC,
    MODELS,
)
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


def _legacy_interval_minutes(value: object) -> int:
    """Convert the prototype's ``HH:MM:SS`` battery interval into minutes."""
    if isinstance(value, int | float):
        return max(0, int(value))
    if isinstance(value, str) and value:
        parts = value.split(":")
        try:
            numbers = [int(part) for part in parts]
        except ValueError:
            return DEFAULT_SCAN_INTERVAL_MIN
        if len(numbers) == 3:
            hours, minutes, seconds = numbers
            return max(0, hours * 60 + minutes + seconds // 60)
        if len(numbers) == 1:
            return max(0, numbers[0])
    return DEFAULT_SCAN_INTERVAL_MIN


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate entries written by the pre-HACS prototype.

    Version 1 stored the address under ``mac_address`` in lower case and the
    poll interval as an ``HH:MM:SS`` string called ``battery_scan_interval``.
    """
    if entry.version > 2:
        # Downgrade from a future version is not something we can handle.
        return False

    if entry.version == 1:
        data = dict(entry.data)
        options = dict(entry.options)

        raw_address = data.pop(LEGACY_CONF_MAC, None) or data.get(CONF_ADDRESS)
        if not raw_address:
            _LOGGER.error(
                "Cannot migrate %s: no Bluetooth address in the config entry",
                entry.title,
            )
            return False

        address = str(raw_address).upper().replace("-", ":")
        data[CONF_ADDRESS] = address

        model = data.get(CONF_MODEL, DEFAULT_MODEL)
        data[CONF_MODEL] = model if model in MODELS else DEFAULT_MODEL

        legacy_interval = data.pop(LEGACY_CONF_BATTERY_INTERVAL, None)
        if legacy_interval is None:
            legacy_interval = options.pop(LEGACY_CONF_BATTERY_INTERVAL, None)
        if CONF_SCAN_INTERVAL_MIN not in options:
            options[CONF_SCAN_INTERVAL_MIN] = _legacy_interval_minutes(legacy_interval)

        hass.config_entries.async_update_entry(
            entry,
            data=data,
            options=options,
            unique_id=address,
            title=f"ESL {address}",
            version=2,
        )
        _LOGGER.info("Migrated %s to version 2", address)

    return True


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
