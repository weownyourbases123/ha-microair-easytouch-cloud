"""Diagnostics for the Micro-Air EasyTouch (Cloud) integration.

Powers Home Assistant's built-in "Download diagnostics" on the
integration and device pages: connection state, the thermostat's last
reported status, and the entry's effective options — with the account
email, thermostat serial, MQTT topic and any credential material
redacted. This is the user-facing debug view for the integration;
detailed protocol logs come from the debug-logging option instead.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import (
    CONF_DEVICE_NAME,
    CONF_EMAIL,
    CONF_PASSWORD,
    CONF_SERIAL,
    CONF_TOPIC,
    debug_logging_enabled,
    poll_interval_seconds,
)
from .coordinator import MicroAirCloudCoordinator

# "title" and the device name are free-text the user set in their
# Micro-Air account; redact them like the other account identifiers
# (Round 3 comparison: names are on HA's keep-out list for diagnostics).
TO_REDACT = {CONF_EMAIL, CONF_PASSWORD, CONF_SERIAL, CONF_TOPIC, CONF_DEVICE_NAME, "title"}

# Thermostat status keys safe (and useful) to show in diagnostics.
_STATUS_KEYS = (
    "mode",
    "mode_name",
    "system_power",
    "inside_temp",
    "outside_temp",
    "faceplate_temp",
    "cool_setpoint",
    "heat_setpoint",
    "auto_cool_setpoint",
    "auto_heat_setpoint",
    "dry_setpoint",
    "cycle_active",
    "cooling_active",
    "heating_active",
    "fault_code",
    "firmware",
    "fan_only_speed",
    "cool_fan_speed",
    "electric_fan_speed",
    "auto_fan_speed",
    "gas_fan_speed",
    "humidity_setpoint",
)


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for one config entry."""
    coordinator: MicroAirCloudCoordinator | None = getattr(
        entry, "runtime_data", None
    )
    if coordinator is None:
        # Entry not currently loaded (e.g. setup retry): report what
        # is known rather than failing the diagnostics download.
        return async_redact_data(
            {
                "entry": {
                    "title": entry.title,
                    "data": dict(entry.data),
                    "options": {
                        "poll_interval_seconds": poll_interval_seconds(
                            entry.options
                        ),
                        "debug_logging": debug_logging_enabled(entry.options),
                    },
                },
                "coordinator": {"loaded": False},
            },
            TO_REDACT,
        )
    status = coordinator.data or {}
    # HA 2026.10.2's DataUpdateCoordinator has last_update_success and
    # last_exception but no last_update_success_time (verified live on
    # the Pi: direct access raised AttributeError and the diagnostics
    # download 500'd). Read it defensively; the bool + exception type
    # below carry the diagnostic value on builds without the timestamp.
    last_success = getattr(coordinator, "last_update_success_time", None)
    diagnostics = {
        "entry": {
            "title": entry.title,
            "data": dict(entry.data),
            "options": {
                "poll_interval_seconds": coordinator.poll_seconds,
                "debug_logging": debug_logging_enabled(entry.options),
            },
        },
        "coordinator": {
            "mqtt_connected": coordinator.mqtt_connected,
            "poll_interval_seconds": coordinator.poll_seconds,
            "last_update_success": coordinator.last_update_success,
            "last_update_success_time": (
                last_success.isoformat() if last_success else None
            ),
            # Exception class name only — never str(exception), whose
            # message could embed account/cloud detail.
            "last_exception_type": (
                type(coordinator.last_exception).__name__
                if getattr(coordinator, "last_exception", None) is not None
                else None
            ),
        },
        "thermostat_last_status": {
            key: status.get(key) for key in _STATUS_KEYS if key in status
        },
    }
    return async_redact_data(diagnostics, TO_REDACT)
