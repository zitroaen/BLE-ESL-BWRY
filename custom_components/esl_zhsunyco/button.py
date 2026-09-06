"""Button entities for the Zhsunyco ESL integration."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .device import ESLDevice
from .entity import ESLEntity


async def async_setup_entry(
    hass: HomeAssistant, entry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up the buttons for one label."""
    device: ESLDevice = entry.runtime_data
    async_add_entities([ESLClearScreenButton(device)])


class ESLClearScreenButton(ESLEntity, ButtonEntity):
    """Clears the panel, command 0xA504."""

    def __init__(self, device: ESLDevice) -> None:
        """Initialise the button."""
        super().__init__(device, "clear_screen")

    async def async_press(self) -> None:
        """Send the clear command."""
        await self.device.async_clear_screen()
