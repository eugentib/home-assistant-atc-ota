"""ATC OTA integration."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

if TYPE_CHECKING:
    from .manager import AtcManager

PLATFORMS = [Platform.SENSOR, Platform.BINARY_SENSOR, Platform.UPDATE, Platform.BUTTON]

type AtcOtaConfigEntry = ConfigEntry["AtcManager"]


async def async_setup_entry(hass: HomeAssistant, entry: AtcOtaConfigEntry) -> bool:
    """Set up ATC OTA from a config entry.

    Keep the Bluetooth/runtime imports out of module import time so the config
    flow can always be loaded independently.
    """
    from .manager import AtcManager  # noqa: PLC0415

    manager = AtcManager(hass, entry)
    entry.runtime_data = manager
    await manager.async_setup()
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: AtcOtaConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
