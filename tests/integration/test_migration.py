"""Migration of config entries written by the pre-HACS prototype."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.esl_zhsunyco.const import (
    CONF_ADDRESS,
    CONF_MODEL,
    CONF_SCAN_INTERVAL_MIN,
    DEFAULT_MODEL,
    DEFAULT_SCAN_INTERVAL_MIN,
    DOMAIN,
)


def _legacy_entry(**overrides) -> MockConfigEntry:
    """Recreate exactly what the old integration wrote to .storage."""
    data = {
        "mac_address": "66:66:17:40:27:77",
        # Deliberately the name the old version wrote, not the current one.
        "model": "BLE-35BWRY",
        "battery_scan_interval": "12:00:00",
    }
    data.update(overrides)
    return MockConfigEntry(
        domain=DOMAIN,
        version=1,
        unique_id="66:66:17:40:27:77",
        title="ESL 66:66:17:40:27:77 (BLE-35BWRY)",
        data=data,
    )


async def test_legacy_entry_sets_up(hass: HomeAssistant, mock_bluetooth) -> None:
    """The entry from the bug report must load instead of raising KeyError."""
    entry = _legacy_entry()
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    assert entry.version == 2
    assert entry.data[CONF_ADDRESS] == "66:66:17:40:27:77"
    assert entry.unique_id == "66:66:17:40:27:77"
    assert entry.title == "ESL 66:66:17:40:27:77"


async def test_legacy_interval_becomes_minutes(
    hass: HomeAssistant, mock_bluetooth
) -> None:
    """12:00:00 must become 720 minutes, not a crash or a default."""
    entry = _legacy_entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.options[CONF_SCAN_INTERVAL_MIN] == 720
    device = entry.runtime_data
    assert device.coordinator.update_interval.total_seconds() == 720 * 60


async def test_legacy_lowercase_address_is_normalised(
    hass: HomeAssistant, mock_bluetooth
) -> None:
    """The prototype stored the address in lower case."""
    entry = _legacy_entry(mac_address="66:66:17:40:27:77".lower())
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.data[CONF_ADDRESS] == "66:66:17:40:27:77"
    assert entry.runtime_data.address == "66:66:17:40:27:77"


async def test_legacy_unknown_model_falls_back(
    hass: HomeAssistant, mock_bluetooth
) -> None:
    """A model that no longer exists must not break setup."""
    entry = _legacy_entry(model="SOMETHING-ELSE")
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.data[CONF_MODEL] == DEFAULT_MODEL


async def test_legacy_missing_interval_uses_default(
    hass: HomeAssistant, mock_bluetooth
) -> None:
    """An entry without the interval key must still migrate."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=1,
        unique_id="66:66:17:40:27:77",
        data={"mac_address": "66:66:17:40:27:77", "model": "BLE-35BWRY"},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.options[CONF_SCAN_INTERVAL_MIN] == DEFAULT_SCAN_INTERVAL_MIN


async def test_entry_without_address_fails_cleanly(
    hass: HomeAssistant, mock_bluetooth
) -> None:
    """A corrupt entry must report a migration error, not a KeyError."""
    entry = MockConfigEntry(
        domain=DOMAIN, version=1, unique_id="broken", data={"model": "BLE-35BWRY"}
    )
    entry.add_to_hass(hass)

    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.MIGRATION_ERROR


async def test_current_entry_is_untouched(
    hass: HomeAssistant, config_entry, mock_bluetooth
) -> None:
    """A version 2 entry must not be rewritten by the migration."""
    assert config_entry.version == 2
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    assert config_entry.state is ConfigEntryState.LOADED
    assert config_entry.options[CONF_SCAN_INTERVAL_MIN] == 0
