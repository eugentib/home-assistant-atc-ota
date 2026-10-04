"""Config flow for ATC OTA."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.components.bluetooth import BluetoothServiceInfoBleak
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


class AtcOtaConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Configure the singleton ATC OTA manager."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")
        if user_input is not None:
            await self.async_set_unique_id(DOMAIN)
            self._abort_if_unique_id_configured()
            return self.async_create_entry(title="ATC OTA", data={})
        return self.async_show_form(step_id="user")

    async def async_step_bluetooth(self, discovery_info: BluetoothServiceInfoBleak):
        if not _looks_supported(discovery_info):
            return self.async_abort(reason="not_supported")
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()
        self.context["title_placeholders"] = {"name": discovery_info.name or "ATC thermometer"}
        return await self.async_step_bluetooth_confirm()

    async def async_step_bluetooth_confirm(self, user_input: dict[str, Any] | None = None):
        if user_input is not None:
            return self.async_create_entry(title="ATC OTA", data={})
        return self.async_show_form(
            step_id="bluetooth_confirm",
            description_placeholders={"name": self.context.get("title_placeholders", {}).get("name", "ATC thermometer")},
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return AtcOtaOptionsFlow(config_entry)


class AtcOtaOptionsFlow(config_entries.OptionsFlow):
    def __init__(self, config_entry) -> None:
        self.config_entry = config_entry

    async def async_step_init(self, user_input: dict[str, Any] | None = None):
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)
        options = self.config_entry.options
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_LOW_BATTERY_THRESHOLD,
                    default=options.get(CONF_LOW_BATTERY_THRESHOLD, DEFAULT_LOW_BATTERY_THRESHOLD),
                ): vol.All(vol.Coerce(int), vol.Range(min=5, max=80)),
                vol.Required(
                    CONF_AUTO_PROBE_METADATA,
                    default=options.get(CONF_AUTO_PROBE_METADATA, DEFAULT_AUTO_PROBE_METADATA),
                ): bool,
                vol.Required(
                    CONF_AUTO_PROBE_MIN_RSSI,
                    default=options.get(CONF_AUTO_PROBE_MIN_RSSI, DEFAULT_AUTO_PROBE_MIN_RSSI),
                ): vol.All(vol.Coerce(int), vol.Range(min=-110, max=-30)),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
