"""RGB LED entity for the Zhsunyco ESL integration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.light import ATTR_RGB_COLOR, ColorMode, LightEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .device import ESLDevice
from .entity import ESLEntity


async def async_setup_entry(
    hass: HomeAssistant, entry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up the RGB LED for one label."""
    device: ESLDevice = entry.runtime_data
    async_add_entities([ESLRGBLight(device)])


class ESLRGBLight(ESLEntity, LightEntity):
    """The label's RGB LED, command 0xA508.

    The LED has no readable state, so on/off and colour are tracked locally
    from whatever we last sent. Blink timings come from the three number
    entities.
    """

    _attr_supported_color_modes = {ColorMode.RGB}
    _attr_color_mode = ColorMode.RGB

    def __init__(self, device: ESLDevice) -> None:
        """Initialise the light."""
        super().__init__(device, "rgb")

    @property
    def is_on(self) -> bool:
        """Whether the LED was last told to light up."""
        return self.device.state.rgb_is_on

    @property
    def rgb_color(self) -> tuple[int, int, int]:
        """Last colour we sent."""
        return self.device.state.rgb_color

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Light the LED with the configured blink pattern."""
        colour = kwargs.get(ATTR_RGB_COLOR) or self.device.state.rgb_color
        if not any(colour):
            # Black would be indistinguishable from off.
            colour = (255, 255, 255)
        await self.device.async_set_rgb(*colour)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Switch the LED off by sending black with a zero work time."""
        remembered = self.device.state.rgb_color
        await self.device.async_set_rgb(0, 0, 0, work_ms=0)
        # Keep the colour so the next turn_on restores it instead of black.
        self.device.state.rgb_color = remembered
        self.device.state.rgb_is_on = False
        self.async_write_ha_state()
