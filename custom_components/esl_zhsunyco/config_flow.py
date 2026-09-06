"""Config flow for the Zhsunyco ESL integration."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.components.bluetooth import BluetoothServiceInfoBleak
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    CONF_ADDRESS,
    CONF_LINGER_S,
    CONF_MODEL,
    CONF_SCAN_INTERVAL_MIN,
    CONF_UNLOCK_VARIANT,
    CONF_WRITE_MODE,
    DEFAULT_LINGER_S,
    DEFAULT_MODEL,
    DEFAULT_SCAN_INTERVAL_MIN,
    DEFAULT_UNLOCK_VARIANT,
    DEFAULT_WRITE_MODE,
    DOMAIN,
    MODELS,
    WRITE_MODES,
)
from .protocol import UNLOCK_VARIANTS

MODEL_SELECTOR = selector.SelectSelector(
    selector.SelectSelectorConfig(
        options=[
            selector.SelectOptionDict(value=key, label=f"{key} - {info['desc']}")
            for key, info in MODELS.items()
        ],
        mode=selector.SelectSelectorMode.DROPDOWN,
    )
)

UNLOCK_SELECTOR = selector.SelectSelector(
    selector.SelectSelectorConfig(
        options=list(UNLOCK_VARIANTS),
        mode=selector.SelectSelectorMode.DROPDOWN,
    )
)

WRITE_MODE_SELECTOR = selector.SelectSelector(
    selector.SelectSelectorConfig(
        options=list(WRITE_MODES),
        mode=selector.SelectSelectorMode.DROPDOWN,
        translation_key="write_mode",
    )
)

LINGER_SELECTOR = selector.NumberSelector(
    selector.NumberSelectorConfig(
        min=0,
        max=600,
        step=5,
        unit_of_measurement="s",
        mode=selector.NumberSelectorMode.BOX,
    )
)

INTERVAL_SELECTOR = selector.NumberSelector(
    selector.NumberSelectorConfig(
        min=0,
        max=1440,
        step=1,
        unit_of_measurement="min",
        mode=selector.NumberSelectorMode.BOX,
    )
)


class ESLConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle discovery and manual setup of a label."""

    VERSION = 2

    def __init__(self) -> None:
        """Initialise the flow."""
        self._discovered_address: str | None = None
        self._discovered_name: str | None = None

    async def async_step_bluetooth(
        self, discovery_info: BluetoothServiceInfoBleak
    ) -> ConfigFlowResult:
        """Handle a label found by the Bluetooth integration."""
        await self.async_set_unique_id(discovery_info.address)
        self._abort_if_unique_id_configured()

        self._discovered_address = discovery_info.address
        self._discovered_name = discovery_info.name or discovery_info.address
        self.context["title_placeholders"] = {"name": self._discovered_name}
        return await self.async_step_confirm()

    async def async_step_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the panel model of a discovered label."""
        assert self._discovered_address is not None

        if user_input is not None:
            return self.async_create_entry(
                title=f"ESL {self._discovered_address}",
                data={
                    CONF_ADDRESS: self._discovered_address,
                    CONF_MODEL: user_input[CONF_MODEL],
                },
                options={
                    CONF_SCAN_INTERVAL_MIN: int(user_input[CONF_SCAN_INTERVAL_MIN]),
                },
            )

        return self.async_show_form(
            step_id="confirm",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_MODEL, default=DEFAULT_MODEL): MODEL_SELECTOR,
                    vol.Required(
                        CONF_SCAN_INTERVAL_MIN, default=DEFAULT_SCAN_INTERVAL_MIN
                    ): INTERVAL_SELECTOR,
                }
            ),
            description_placeholders={"name": self._discovered_name or ""},
        )

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle manual entry of a label address."""
        errors: dict[str, str] = {}

        if user_input is not None:
            address = user_input[CONF_ADDRESS].strip().upper().replace("-", ":")
            if not _is_valid_address(address):
                errors[CONF_ADDRESS] = "invalid_address"
            else:
                await self.async_set_unique_id(address)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=f"ESL {address}",
                    data={
                        CONF_ADDRESS: address,
                        CONF_MODEL: user_input[CONF_MODEL],
                    },
                    options={
                        CONF_SCAN_INTERVAL_MIN: int(user_input[CONF_SCAN_INTERVAL_MIN]),
                    },
                )

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_ADDRESS, default=(user_input or {}).get(CONF_ADDRESS, "")
                    ): selector.TextSelector(),
                    vol.Required(CONF_MODEL, default=DEFAULT_MODEL): MODEL_SELECTOR,
                    vol.Required(
                        CONF_SCAN_INTERVAL_MIN, default=DEFAULT_SCAN_INTERVAL_MIN
                    ): INTERVAL_SELECTOR,
                }
            ),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry) -> OptionsFlow:
        """Return the options flow."""
        return ESLOptionsFlow()


class ESLOptionsFlow(OptionsFlow):
    """Let the user change the poll interval."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show and store the options."""
        if user_input is not None:
            return self.async_create_entry(
                data={
                    CONF_SCAN_INTERVAL_MIN: int(user_input[CONF_SCAN_INTERVAL_MIN]),
                    CONF_LINGER_S: int(user_input[CONF_LINGER_S]),
                    CONF_WRITE_MODE: user_input[CONF_WRITE_MODE],
                    CONF_UNLOCK_VARIANT: user_input[CONF_UNLOCK_VARIANT],
                }
            )

        options = self.config_entry.options
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_SCAN_INTERVAL_MIN,
                        default=options.get(
                            CONF_SCAN_INTERVAL_MIN, DEFAULT_SCAN_INTERVAL_MIN
                        ),
                    ): INTERVAL_SELECTOR,
                    vol.Required(
                        CONF_LINGER_S,
                        default=options.get(CONF_LINGER_S, DEFAULT_LINGER_S),
                    ): LINGER_SELECTOR,
                    vol.Required(
                        CONF_WRITE_MODE,
                        default=options.get(CONF_WRITE_MODE, DEFAULT_WRITE_MODE),
                    ): WRITE_MODE_SELECTOR,
                    vol.Required(
                        CONF_UNLOCK_VARIANT,
                        default=options.get(
                            CONF_UNLOCK_VARIANT, DEFAULT_UNLOCK_VARIANT
                        ),
                    ): UNLOCK_SELECTOR,
                }
            ),
        )


def _is_valid_address(address: str) -> bool:
    """Check for an ``AA:BB:CC:DD:EE:FF`` style address."""
    parts = address.split(":")
    if len(parts) != 6:
        return False
    return all(
        len(part) == 2 and all(c in "0123456789ABCDEF" for c in part) for part in parts
    )
