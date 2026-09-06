"""Fixtures for the Home Assistant integration tests."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.esl_zhsunyco.const import (
    CONF_ADDRESS,
    CONF_MODEL,
    CONF_SCAN_INTERVAL_MIN,
    DOMAIN,
)

ADDRESS = "66:66:54:20:00:55"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Make Home Assistant load custom_components in tests."""
    return enable_custom_integrations


@pytest.fixture
def config_entry() -> MockConfigEntry:
    """Return a configured label with polling disabled."""
    return MockConfigEntry(
        domain=DOMAIN,
        version=2,
        unique_id=ADDRESS,
        title=f"ESL {ADDRESS}",
        data={CONF_ADDRESS: ADDRESS, CONF_MODEL: "BLE-35BWRY"},
        options={CONF_SCAN_INTERVAL_MIN: 0},
    )


async def _never_advertises(*args, **kwargs):
    """Stand in for a label that stays asleep, without the real wait."""
    raise TimeoutError


@pytest.fixture
def mock_bluetooth():
    """Stub out the Bluetooth stack so no adapter is required."""
    with (
        patch(
            "custom_components.esl_zhsunyco.device.bluetooth.async_register_callback",
            return_value=lambda: None,
        ),
        patch(
            "custom_components.esl_zhsunyco.device.bluetooth.async_last_service_info",
            return_value=None,
        ),
        patch(
            "custom_components.esl_zhsunyco.device.bluetooth.async_ble_device_from_address",
            return_value=None,
        ),
        # Without this the tests would sit through the real 180 s wait for an
        # advertisement that never arrives.
        patch(
            "custom_components.esl_zhsunyco.device.bluetooth.async_process_advertisements",
            side_effect=_never_advertises,
        ),
    ):
        yield
