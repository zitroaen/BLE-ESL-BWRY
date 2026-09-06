"""Image entity showing what was last put on the panel."""

from __future__ import annotations

from datetime import datetime

from homeassistant.components.image import ImageEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .device import ESLDevice
from .entity import ESLEntity


async def async_setup_entry(
    hass: HomeAssistant, entry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up the panel preview for one label."""
    device: ESLDevice = entry.runtime_data
    async_add_entities([ESLPanelImage(hass, device)])


class ESLPanelImage(ESLEntity, ImageEntity):
    """A picture of the label's current screen.

    An e-ink panel gives no way to read back what it is showing, so this is
    not a photo of the label - it is the image that was sent, drawn from the
    quantised pixels that were actually packed. Dithering and palette
    snapping are therefore visible here exactly as on the panel.
    """

    _attr_content_type = "image/png"
    _attr_entity_registry_enabled_default = True

    def __init__(self, hass: HomeAssistant, device: ESLDevice) -> None:
        """Initialise the preview entity."""
        ESLEntity.__init__(self, device, "panel")
        ImageEntity.__init__(self, hass)

    @property
    def available(self) -> bool:
        """Always available: this is local state, not a live read.

        The other entities go unavailable when the label drops out of range.
        This one must not - what the panel shows does not stop being true
        because the label is asleep, and that is the normal state for an ESL.
        """
        return True

    @property
    def image_last_updated(self) -> datetime | None:
        """When the panel was last written, or None if not in this session."""
        return self.device.state.last_image_at

    @property
    def extra_state_attributes(self) -> dict[str, str] | None:
        """Name the source, so a dashboard can say where the picture is from."""
        source = self.device.state.last_image_source
        return {"source": source} if source else None

    def image(self) -> bytes | None:
        """Return the PNG of the last uploaded image."""
        return self.device.state.last_image_png
