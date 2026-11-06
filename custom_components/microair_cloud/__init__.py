"""Micro-Air EasyTouch (Cloud) integration.

Connects a Micro-Air EasyTouch thermostat to Home Assistant through
Micro-Air's own AWS cloud path (the same one the phone app uses),
instead of Bluetooth. Provides status sensors plus a climate entity
with verified mode / setpoint / fan controls.
See README.md in this folder for the full story.
"""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .coordinator import MicroAirCloudCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.SENSOR, Platform.CLIMATE]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up one thermostat from its config entry."""
    coordinator = MicroAirCloudCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry: stop MQTT and remove runtime cert files."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        coordinator: MicroAirCloudCoordinator = entry.runtime_data
        await coordinator.async_shutdown()
    return unloaded
