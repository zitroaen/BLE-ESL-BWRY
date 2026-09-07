"""Sensor entities for the Zhsunyco ESL integration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    EntityCategory,
    UnitOfElectricPotential,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import ERROR_CODES
from .device import ESLDevice, ESLState
from .entity import ESLEntity
from .protocol import format_version


@dataclass(frozen=True, kw_only=True)
class ESLSensorDescription(SensorEntityDescription):
    """Sensor description with a value getter."""

    value_fn: Callable[[ESLDevice], float | str | None]


SENSORS: tuple[ESLSensorDescription, ...] = (
    ESLSensorDescription(
        key="battery_level",
        translation_key="battery_level",
        device_class=SensorDeviceClass.BATTERY,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda device: device.battery_percent,
    ),
    ESLSensorDescription(
        key="battery_voltage",
        translation_key="battery_voltage",
        device_class=SensorDeviceClass.VOLTAGE,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        value_fn=lambda device: device.battery_v,
    ),
    ESLSensorDescription(
        key="status",
        translation_key="status",
        device_class=SensorDeviceClass.ENUM,
        options=sorted(set(ERROR_CODES.values()) | {"busy", "unknown"}),
        value_fn=lambda device: _status_value(device.state),
    ),
    ESLSensorDescription(
        key="signal_strength",
        translation_key="signal_strength",
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda device: device.state.rssi,
    ),
    ESLSensorDescription(
        key="display_version",
        translation_key="display_version",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda device: (
            format_version(device.state.disp_version)
            if device.state.disp_version is not None
            else None
        ),
    ),
    ESLSensorDescription(
        key="product_id",
        translation_key="product_id",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda device: (
            f"0x{device.state.pid:04X}" if device.state.pid is not None else None
        ),
    ),
)


def _status_value(state: ESLState) -> str | None:
    """Map busy flag and error code onto a single enum value."""
    if state.busy:
        return "busy"
    if state.error is None:
        return None if state.last_advert is None else "unknown"
    text = state.error_text
    # An undocumented code must still land on a declared enum option.
    return text if text in ERROR_CODES.values() else "unknown"


async def async_setup_entry(
    hass: HomeAssistant, entry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up the sensors for one label."""
    device: ESLDevice = entry.runtime_data
    async_add_entities(ESLSensor(device, description) for description in SENSORS)


class ESLSensor(ESLEntity, SensorEntity):
    """A single readout of the label."""

    entity_description: ESLSensorDescription

    def __init__(self, device: ESLDevice, description: ESLSensorDescription) -> None:
        """Initialise the sensor."""
        super().__init__(device, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> float | str | None:
        """Current value."""
        return self.entity_description.value_fn(self.device)
