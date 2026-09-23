"""What happens when the service is pointed at the wrong thing."""

from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import device_registry as dr

from custom_components.esl_zhsunyco.const import DOMAIN, SERVICE_CLEAR_SCREEN


async def _setup(hass: HomeAssistant, entry):
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)[0]


async def test_the_label_name_is_answered_with_its_id(
    hass: HomeAssistant, config_entry, mock_bluetooth
) -> None:
    """The name is on every screen; the id is on none. Expect the mistake."""
    device = await _setup(hass, config_entry)

    with pytest.raises(ServiceValidationError) as caught:
        await hass.services.async_call(
            DOMAIN, SERVICE_CLEAR_SCREEN, {"device_id": device.name}, blocking=True
        )

    message = str(caught.value)
    assert "name, not its device id" in message
    assert device.id in message, "the message has to hand over the right id"
    assert "device_id(" in message, "and the template that avoids looking it up"


async def test_an_id_that_is_nothing_at_all_is_still_refused(
    hass: HomeAssistant, config_entry, mock_bluetooth
) -> None:
    await _setup(hass, config_entry)

    with pytest.raises(ServiceValidationError, match="Unknown device id"):
        await hass.services.async_call(
            DOMAIN, SERVICE_CLEAR_SCREEN, {"device_id": "not-a-device"}, blocking=True
        )
