"""Config flow for ATC OTA."""

from __future__ import annotations

from typing import Any, override

import voluptuous as vol

from homeassistant.components.bluetooth import BluetoothServiceInfoBleak
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback

from .const import (
    CONF_AUTO_PROBE_METADATA,
    CONF_AUTO_PROBE_MIN_RSSI,
    CONF_LOW_BATTERY_THRESHOLD,
    DEFAULT_AUTO_PROBE_METADATA,
    DEFAULT_AUTO_PROBE_MIN_RSSI,
    DEFAULT_LOW_BATTERY_THRESHOLD,
    DOMAIN,
)


def _looks_supported(discovery_info: BluetoothServiceInfoBleak) -> bool:
    address = discovery_info.address.upper()
    name = (discovery_info.name or "").upper()
    return address.startswith("A4:C1:38:") or name.startswith(
        ("ATC_", "LYWSD", "MHO-", "MJWSD", "CGG1", "CGDK")
    )


class AtcOtaConfigFlow(ConfigFlow, domain=DOMAIN):
    """Configure the singleton ATC OTA manager."""

    VERSION = 1

    @override
    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            await self.async_set_unique_id(DOMAIN)
            self._abort_if_unique_id_configured()
            return self.async_create_entry(title="ATC OTA", data={})
        return self.async_show_form(step_id="user", data_schema=vol.Schema({}))

    @override
    async def async_step_bluetooth(
        self, discovery_info: BluetoothServiceInfoBleak
    ) -> ConfigFlowResult:
        if not _looks_supported(discovery_info):
            return self.async_abort(reason="not_supported")
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()
        self.context["title_placeholders"] = {
            "name": discovery_info.name or "ATC thermometer"
        }
        return await self.async_step_bluetooth_confirm()

    async def async_step_bluetooth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(title="ATC OTA", data={})
        return self.async_show_form(
            step_id="bluetooth_confirm",
            data_schema=vol.Schema({}),
            description_placeholders={
                "name": self.context.get("title_placeholders", {}).get(
                    "name", "ATC thermometer"
                )
            },
        )

    @staticmethod
    @callback
    @override
    def async_get_options_flow(config_entry: ConfigEntry) -> "AtcOtaOptionsFlow":
        return AtcOtaOptionsFlow()


class AtcOtaOptionsFlow(OptionsFlow):
    """ATC OTA options applied live without reloading the config entry."""

    @override
    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)
        options = self.config_entry.options
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_LOW_BATTERY_THRESHOLD,
                    default=options.get(
                        CONF_LOW_BATTERY_THRESHOLD, DEFAULT_LOW_BATTERY_THRESHOLD
                    ),
                ): vol.All(vol.Coerce(int), vol.Range(min=5, max=80)),

            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
