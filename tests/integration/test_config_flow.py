"""Config and options flow tests."""

from __future__ import annotations

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.esl_zhsunyco.const import (
    CONF_ADDRESS,
    CONF_MODEL,
    CONF_SCAN_INTERVAL_MIN,
    DOMAIN,
)

from .conftest import ADDRESS


async def test_user_flow_creates_entry(hass: HomeAssistant, mock_bluetooth) -> None:
    """A valid address must produce a config entry."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_ADDRESS: ADDRESS,
            CONF_MODEL: "BLE-35BWRY",
            CONF_SCAN_INTERVAL_MIN: 30,
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_ADDRESS] == ADDRESS
    assert result["options"][CONF_SCAN_INTERVAL_MIN] == 30


async def test_user_flow_rejects_bad_address(
    hass: HomeAssistant, mock_bluetooth
) -> None:
    """A malformed address must be reported on the address field."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_ADDRESS: "not-a-mac",
            CONF_MODEL: "BLE-35BWRY",
            CONF_SCAN_INTERVAL_MIN: 30,
        },
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_ADDRESS: "invalid_address"}


async def test_user_flow_normalises_address(
    hass: HomeAssistant, mock_bluetooth
) -> None:
    """Lower case and dashes must be accepted and normalised."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_ADDRESS: "66-66-54-20-00-55",
            CONF_MODEL: "BLE-35BWRY",
            CONF_SCAN_INTERVAL_MIN: 30,
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_ADDRESS] == ADDRESS


async def test_duplicate_is_aborted(
    hass: HomeAssistant, config_entry, mock_bluetooth
) -> None:
    """The same label must not be set up twice."""
    config_entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_ADDRESS: ADDRESS,
            CONF_MODEL: "BLE-35BWRY",
            CONF_SCAN_INTERVAL_MIN: 30,
        },
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_options_flow_updates_interval(
    hass: HomeAssistant, config_entry, mock_bluetooth
) -> None:
    """Changing the interval must reach the coordinator."""
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    assert result["type"] is FlowResultType.FORM

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_SCAN_INTERVAL_MIN: 15}
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    device = config_entry.runtime_data
    assert device.coordinator.update_interval is not None
    assert device.coordinator.update_interval.total_seconds() == 15 * 60


async def test_options_zero_disables_polling(
    hass: HomeAssistant, config_entry, mock_bluetooth
) -> None:
    """Interval 0 must switch the connectable poll off entirely."""
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    device = config_entry.runtime_data
    assert device.coordinator.update_interval is None
