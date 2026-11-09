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

from .const import debug_logging_enabled
from .coordinator import MicroAirCloudCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.SENSOR, Platform.CLIMATE]

_PACKAGE_LOGGER = logging.getLogger(__package__)


def _apply_debug_logging(entry: ConfigEntry) -> None:
    """Set this integration's log level from the debug option.

    Every module logger in the package is a child of the package
    logger, so setting the level here covers all of them. NOTSET hands
    control back to Home Assistant's own logging configuration.
    Passwords, tokens and private keys are never logged at any level.
    """
    _PACKAGE_LOGGER.setLevel(
        logging.DEBUG if debug_logging_enabled(entry.options) else logging.NOTSET
    )


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up one thermostat from its config entry."""
    _apply_debug_logging(entry)
    coordinator = MicroAirCloudCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def _async_options_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Apply saved options live (poll interval, debug logging).

    Registered as the entry's update listener. It can fire while the
    entry is not fully loaded (options saved during SETUP_RETRY or
    before the first refresh finished), so the coordinator access is
    defensive: applying the log level is always safe, and the poll
    interval is picked up at setup from the same options if the
    coordinator does not exist yet.
    """
    _apply_debug_logging(entry)
    coordinator: MicroAirCloudCoordinator | None = getattr(
        entry, "runtime_data", None
    )
    if coordinator is not None:
        coordinator.apply_options()


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry: stop MQTT and remove runtime cert files."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        coordinator: MicroAirCloudCoordinator = entry.runtime_data
        await coordinator.async_shutdown()
    return unloaded
