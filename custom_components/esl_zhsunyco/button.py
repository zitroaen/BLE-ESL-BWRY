"""Button entities for the Zhsunyco ESL integration."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .device import ESLDevice
from .entity import ESLEntity
from .probe import async_probe_and_notify


async def async_setup_entry(
    hass: HomeAssistant, entry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up the buttons for one label."""
    device: ESLDevice = entry.runtime_data
    async_add_entities(
        [
            ESLClearScreenButton(device),
            ESLTestPatternButton(device),
            ESLProbeButton(device),
        ]
    )


class ESLClearScreenButton(ESLEntity, ButtonEntity):
    """Clears the panel, command 0xA504."""

    def __init__(self, device: ESLDevice) -> None:
        """Initialise the button."""
        super().__init__(device, "clear_screen")

    async def async_press(self) -> None:
        """Send the clear command."""
        await self.device.async_clear_screen()


class ESLTestPatternButton(ESLEntity, ButtonEntity):
    """Uploads the built-in diagnostic pattern so the panel can be verified."""

    def __init__(self, device: ESLDevice) -> None:
        """Initialise the button."""
        super().__init__(device, "test_pattern")

    async def async_press(self) -> None:
        """Render and upload the diagnostic pattern."""
        from .imaging import ImageRequest
        from .patterns import DEFAULT_PATTERN

        await self.device.async_send_image(
            ImageRequest(pattern=DEFAULT_PATTERN, stretch=True)
        )


class ESLProbeButton(ESLEntity, ButtonEntity):
    """Runs a diagnostic probe and posts the result as a notification."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, device: ESLDevice) -> None:
        """Initialise the button."""
        super().__init__(device, "debug_probe")

    @property
    def available(self) -> bool:
        """Always pressable: it exists for when the label misbehaves."""
        return True

    async def async_press(self) -> None:
        """Collect and publish the report."""
        await async_probe_and_notify(self.hass, self.device)
