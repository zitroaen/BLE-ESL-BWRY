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


@pytest.fixture(autouse=True)
def no_connect_settle():
    """Skip the post-connect settle delay; production uses one second."""
    with patch("custom_components.esl_zhsunyco.device.POST_CONNECT_SETTLE_S", 0):
        yield


@pytest.fixture
def mock_bluetooth(mock_bleak_scanner_start, mock_bluetooth_adapters):
    """Stub out the Bluetooth stack so no adapter is required.

    The two requested fixtures come from pytest-homeassistant-custom-component
    and keep the bluetooth component itself from touching D-Bus during setup;
    the patches below then replace the calls our own code makes.
    """
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


class FakeServices:
    """A GATT table offering the characteristics the integration requires."""

    def __init__(self, present: bool = True) -> None:
        self.present = present
        self.cleared = 0

    def get_characteristic(self, uuid):
        return object() if self.present else None


def attach_services(client, present: bool = True) -> FakeServices:
    """Give a fake client a GATT table and a clear_cache implementation."""
    services = FakeServices(present)
    client.services = services

    async def clear_cache():
        services.cleared += 1
        services.present = True
        return True

    client.clear_cache = clear_cache
    return services


def raw_payload(sent: bytes) -> bytes:
    """Return the image bytes behind whatever went on the wire.

    Images are compressed by default, so a test that cares about the pixel
    data has to look through that. Decompressing here rather than turning
    compression off keeps the tests on the path the integration actually
    takes.
    """
    from custom_components.esl_zhsunyco.protocol import (
        COMPRESSION_HEADER,
        decompress_image,
    )

    if sent.startswith(COMPRESSION_HEADER):
        return decompress_image(sent)
    return sent
