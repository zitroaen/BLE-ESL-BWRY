"""Number entities holding the RGB timing parameters."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.number import (
    NumberEntityDescription,
    NumberMode,
    RestoreNumber,
)
from homeassistant.const import EntityCategory, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    DEFAULT_RGB_OFF_MS,
    DEFAULT_RGB_ON_MS,
    DEFAULT_RGB_WORK_MS,
    RGB_OFF_MS_MAX,
    RGB_OFF_MS_MIN,
    RGB_ON_MS_MAX,
    RGB_ON_MS_MIN,
    RGB_WORK_MS_MAX,
    RGB_WORK_MS_MIN,
)
from .device import ESLDevice, ESLState
from .entity import ESLEntity


@dataclass(frozen=True, kw_only=True)
class ESLNumberDescription(NumberEntityDescription):
    """Number description with getter and setter on the shared state."""

    default: int
    value_fn: Callable[[ESLState], int]
    set_fn: Callable[[ESLState, int], None]


def _set_on(state: ESLState, value: int) -> None:
    state.rgb_on_ms = value


def _set_off(state: ESLState, value: int) -> None:
    state.rgb_off_ms = value


def _set_work(state: ESLState, value: int) -> None:
    state.rgb_work_ms = value


NUMBERS: tuple[ESLNumberDescription, ...] = (
    ESLNumberDescription(
        key="rgb_on_ms",
        translation_key="rgb_on_ms",
        native_min_value=RGB_ON_MS_MIN,
        native_max_value=RGB_ON_MS_MAX,
        native_step=50,
        native_unit_of_measurement=UnitOfTime.MILLISECONDS,
        mode=NumberMode.BOX,
        entity_category=EntityCategory.CONFIG,
        default=DEFAULT_RGB_ON_MS,
        value_fn=lambda state: state.rgb_on_ms,
        set_fn=_set_on,
    ),
    ESLNumberDescription(
        key="rgb_off_ms",
        translation_key="rgb_off_ms",
        native_min_value=RGB_OFF_MS_MIN,
        native_max_value=RGB_OFF_MS_MAX,
        native_step=50,
        native_unit_of_measurement=UnitOfTime.MILLISECONDS,
        mode=NumberMode.BOX,
        entity_category=EntityCategory.CONFIG,
        default=DEFAULT_RGB_OFF_MS,
        value_fn=lambda state: state.rgb_off_ms,
        set_fn=_set_off,
    ),
    ESLNumberDescription(
        key="rgb_work_ms",
        translation_key="rgb_work_ms",
        native_min_value=RGB_WORK_MS_MIN,
        native_max_value=RGB_WORK_MS_MAX,
        native_step=100,
        native_unit_of_measurement=UnitOfTime.MILLISECONDS,
        mode=NumberMode.BOX,
        entity_category=EntityCategory.CONFIG,
        default=DEFAULT_RGB_WORK_MS,
        value_fn=lambda state: state.rgb_work_ms,
        set_fn=_set_work,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up the number entities for one label."""
    device: ESLDevice = entry.runtime_data
    async_add_entities(ESLNumber(device, description) for description in NUMBERS)


class ESLNumber(ESLEntity, RestoreNumber):
    """A timing parameter used by the RGB light and the set_rgb service."""

    entity_description: ESLNumberDescription

    def __init__(self, device: ESLDevice, description: ESLNumberDescription) -> None:
        """Initialise the number."""
        super().__init__(device, description.key)
        self.entity_description = description

    async def async_added_to_hass(self) -> None:
        """Restore the last value so the setting survives a restart."""
        await super().async_added_to_hass()
        last = await self.async_get_last_number_data()
        if last is not None and last.native_value is not None:
            self.entity_description.set_fn(self.device.state, int(last.native_value))

    @property
    def available(self) -> bool:
        """These are local settings, always editable."""
        return True

    @property
    def native_value(self) -> float:
        """Current value from the shared state."""
        return self.entity_description.value_fn(self.device.state)

    async def async_set_native_value(self, value: float) -> None:
        """Store the new value."""
        self.entity_description.set_fn(self.device.state, int(value))
        self.async_write_ha_state()
