"""Diagnostics output and the command write mode option."""

from __future__ import annotations

import struct
from types import SimpleNamespace

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.components.diagnostics import (
    get_diagnostics_for_config_entry,
)

from custom_components.esl_zhsunyco.const import (
    CONF_WRITE_MODE,
    MANUFACTURER_ID,
    WRITE_MODE_NO_RESPONSE,
    WRITE_MODE_RESPONSE,
)


async def _setup(hass: HomeAssistant, entry):
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry.runtime_data


async def test_diagnostics_include_raw_advertisement(
    hass: HomeAssistant, hass_client, config_entry, mock_bluetooth
) -> None:
    """The raw bytes must be downloadable so a wrong layout can be spotted."""
    device = await _setup(hass, config_entry)

    payload = struct.pack("<HHHHH", 0xAB01, 0x0102, 0x0203, 0x0304, 29200)
    device._advert_received(
        SimpleNamespace(rssi=-55, manufacturer_data={MANUFACTURER_ID: payload}), None
    )
    await hass.async_block_till_done()

    result = await get_diagnostics_for_config_entry(hass, hass_client, config_entry)

    advert = result["advertisement"]
    assert advert["raw_by_company_id"]["0xBBAA"] == payload.hex(" ")
    # The implausible documented reading and both alternatives must be visible.
    candidates = advert["decoded"]["documented"]["battery_candidates_v"]
    assert candidates["le_mv"] == 29.2
    assert candidates["be_mv"] == 4.21
    assert candidates["le_tenth_mv"] == 2.92
    assert "battery_candidate_meanings" in result


async def test_diagnostics_redact_address(
    hass: HomeAssistant, hass_client, config_entry, mock_bluetooth
) -> None:
    """The Bluetooth address must not be exposed verbatim in a shared file."""
    await _setup(hass, config_entry)
    result = await get_diagnostics_for_config_entry(hass, hass_client, config_entry)
    assert result["entry"]["data"]["address"] == "**REDACTED**"


async def test_write_mode_defaults_to_auto(
    hass: HomeAssistant, config_entry, mock_bluetooth
) -> None:
    """Without an explicit option bleak picks the write type."""
    device = await _setup(hass, config_entry)
    assert device.write_response is None


async def test_write_mode_option_is_applied(
    hass: HomeAssistant, config_entry, mock_bluetooth
) -> None:
    """Both explicit write types must reach the protocol layer."""
    device = await _setup(hass, config_entry)

    hass.config_entries.async_update_entry(
        config_entry,
        options={**config_entry.options, CONF_WRITE_MODE: WRITE_MODE_NO_RESPONSE},
    )
    await hass.async_block_till_done()
    assert device.write_response is False

    hass.config_entries.async_update_entry(
        config_entry,
        options={**config_entry.options, CONF_WRITE_MODE: WRITE_MODE_RESPONSE},
    )
    await hass.async_block_till_done()
    assert device.write_response is True
