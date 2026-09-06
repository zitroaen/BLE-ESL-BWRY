"""Shared entity base for the Zhsunyco ESL integration."""

from __future__ import annotations

from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MODELS
from .device import ESLDevice, ESLState
from .protocol import format_version


class ESLEntity(CoordinatorEntity[ESLState]):
    """Base class wiring an entity to one label."""

    _attr_has_entity_name = True

    def __init__(self, device: ESLDevice, key: str) -> None:
        """Initialise the entity for ``device`` with a unique suffix."""
        super().__init__(device.coordinator)
        self.device = device
        self._attr_unique_id = f"{device.address}_{key}"
        self._attr_translation_key = key

    @property
    def device_info(self) -> DeviceInfo:
        """Group all entities of one label under a single device."""
        state = self.device.state
        panel = MODELS.get(self.device.model, {})
        return DeviceInfo(
            identifiers={(DOMAIN, self.device.address)},
            connections={(CONNECTION_BLUETOOTH, self.device.address)},
            name=f"ESL {self.device.address}",
            manufacturer="Zhsunyco",
            model=str(panel.get("desc", self.device.model)),
            model_id=self.device.model,
            sw_version=(
                format_version(state.app_version)
                if state.app_version is not None
                else None
            ),
            hw_version=(
                format_version(state.hw_version)
                if state.hw_version is not None
                else None
            ),
        )

    @property
    def available(self) -> bool:
        """Availability follows the label, not just the last poll."""
        return self.device.available
