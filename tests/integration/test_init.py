"""Setup, entity and service tests running inside Home Assistant."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

from custom_components.esl_zhsunyco.const import (
    DOMAIN,
    SERVICE_CLEAR_SCREEN,
    SERVICE_SET_RGB,
)

from .conftest import ADDRESS


async def _setup(hass: HomeAssistant, entry) -> None:
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


def _mark_seen(hass: HomeAssistant, entry) -> None:
    """Pretend an advertisement just arrived so entities become available."""
    device = entry.runtime_data
    device.state.last_advert = dt_util.utcnow()
    device.coordinator.async_update_listeners()


async def test_setup_survives_unreachable_label(
    hass: HomeAssistant, config_entry, mock_bluetooth
) -> None:
    """A label that is out of range must still produce a loaded entry."""
    await _setup(hass, config_entry)
    assert config_entry.state is ConfigEntryState.LOADED


async def test_entities_are_created(
    hass: HomeAssistant, config_entry, mock_bluetooth
) -> None:
    """All four platforms must register their entities."""
    await _setup(hass, config_entry)

    registry = er.async_get(hass)
    entities = er.async_entries_for_config_entry(registry, config_entry.entry_id)
    by_domain: dict[str, int] = {}
    for entity in entities:
        by_domain[entity.domain] = by_domain.get(entity.domain, 0) + 1

    assert by_domain.get("sensor") == 5
    assert by_domain.get("button") == 2
    assert by_domain.get("light") == 1
    assert by_domain.get("number") == 3


async def test_single_device_entry(
    hass: HomeAssistant, config_entry, mock_bluetooth
) -> None:
    """Every entity must be grouped under one device."""
    await _setup(hass, config_entry)

    registry = dr.async_get(hass)
    devices = dr.async_entries_for_config_entry(registry, config_entry.entry_id)
    assert len(devices) == 1
    assert (DOMAIN, ADDRESS) in devices[0].identifiers


async def test_services_registered(
    hass: HomeAssistant, config_entry, mock_bluetooth
) -> None:
    """The integration services must exist after setup."""
    await _setup(hass, config_entry)
    assert hass.services.has_service(DOMAIN, SERVICE_SET_RGB)
    assert hass.services.has_service(DOMAIN, SERVICE_CLEAR_SCREEN)


async def test_button_unavailable_until_label_is_heard(
    hass: HomeAssistant, config_entry, mock_bluetooth
) -> None:
    """A label that was never seen must not offer actionable entities."""
    await _setup(hass, config_entry)
    state = hass.states.get("button.esl_66_66_54_20_00_55_clear_screen")
    assert state is not None
    assert state.state == "unavailable"


async def test_button_press_sends_clear(
    hass: HomeAssistant, config_entry, mock_bluetooth
) -> None:
    """Pressing the button must reach the clear screen command."""
    await _setup(hass, config_entry)
    _mark_seen(hass, config_entry)

    with patch(
        "custom_components.esl_zhsunyco.device.ESLDevice.async_clear_screen",
        new=AsyncMock(),
    ) as clear:
        await hass.services.async_call(
            "button",
            "press",
            {"entity_id": "button.esl_66_66_54_20_00_55_clear_screen"},
            blocking=True,
        )
    assert clear.call_count == 1


async def test_set_rgb_service_uses_number_defaults(
    hass: HomeAssistant, config_entry, mock_bluetooth
) -> None:
    """Omitted timings must fall back to the number entity values."""
    await _setup(hass, config_entry)
    _mark_seen(hass, config_entry)

    registry = dr.async_get(hass)
    device_entry = dr.async_entries_for_config_entry(registry, config_entry.entry_id)[0]

    sent: list[tuple] = []

    async def fake_set_rgb(client, r, g, b, on_ms, off_ms, work_ms, response=None):
        sent.append((r, g, b, on_ms, off_ms, work_ms))

    with (
        patch(
            "custom_components.esl_zhsunyco.device.protocol.set_rgb",
            new=fake_set_rgb,
        ),
        patch(
            "custom_components.esl_zhsunyco.device._ESLConnection.__aenter__",
            new=AsyncMock(return_value=object()),
        ),
        patch(
            "custom_components.esl_zhsunyco.device._ESLConnection.__aexit__",
            new=AsyncMock(return_value=False),
        ),
    ):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_SET_RGB,
            {"device_id": device_entry.id, "rgb_color": [10, 20, 30]},
            blocking=True,
        )

    assert sent == [(10, 20, 30, 500, 500, 30000)]


async def test_unload(hass: HomeAssistant, config_entry, mock_bluetooth) -> None:
    """Unloading must succeed and clean up."""
    await _setup(hass, config_entry)
    assert await hass.config_entries.async_unload(config_entry.entry_id)
    await hass.async_block_till_done()
    assert config_entry.state is ConfigEntryState.NOT_LOADED
