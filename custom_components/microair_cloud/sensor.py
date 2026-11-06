"""Sensors for Micro-Air EasyTouch (Cloud).

Three sensors per thermostat:
- Inside temperature (the number you actually care about)
- Current mode (Off / Heat / Cool / ...), with every setpoint exposed
  as attributes so automations and dashboards can read them
- Connection status (whether the cloud link + thermostat answered)
"""

from __future__ import annotations

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    CONF_CONTROL_TYPE,
    CONF_DEVICE_NAME,
    CONF_SERIAL,
    DOMAIN,
    MODE_NAMES,
)
from .coordinator import MicroAirCloudCoordinator


def _device_info(entry: ConfigEntry) -> DeviceInfo:
    return DeviceInfo(
        identifiers={(DOMAIN, entry.data[CONF_SERIAL])},
        manufacturer="Micro-Air",
        model=f"EasyTouch {entry.data.get(CONF_CONTROL_TYPE, '')}".strip(),
        name=entry.data.get(CONF_DEVICE_NAME) or entry.title,
        serial_number=entry.data[CONF_SERIAL],
    )


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: MicroAirCloudCoordinator = entry.runtime_data
    async_add_entities(
        [
            InsideTemperatureSensor(coordinator, entry),
            CurrentModeSensor(coordinator, entry),
            ConnectionStatusSensor(coordinator, entry),
        ]
    )


class _BaseSensor(CoordinatorEntity, SensorEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator, entry: ConfigEntry, key: str) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.data[CONF_SERIAL]}_{key}"
        self._attr_device_info = _device_info(entry)


class InsideTemperatureSensor(_BaseSensor):
    _attr_name = "Inside Temperature"
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = "°F"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, "inside_temperature")

    @property
    def native_value(self):
        data = self.coordinator.data or {}
        return data.get("inside_temp")


class CurrentModeSensor(_BaseSensor):
    _attr_name = "Current Mode"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = list(MODE_NAMES.values()) + ["Unknown"]

    def __init__(self, coordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, "current_mode")

    @property
    def native_value(self):
        data = self.coordinator.data or {}
        return data.get("mode_name")

    @property
    def extra_state_attributes(self):
        data = self.coordinator.data or {}
        return {
            "cool_setpoint": data.get("cool_setpoint"),
            "heat_setpoint": data.get("heat_setpoint"),
            "auto_heat_setpoint": data.get("auto_heat_setpoint"),
            "auto_cool_setpoint": data.get("auto_cool_setpoint"),
            "dry_setpoint": data.get("dry_setpoint"),
            "outside_temperature": data.get("outside_temp"),
            "fault_code": data.get("fault_code"),
            "firmware": data.get("firmware"),
        }


class ConnectionStatusSensor(_BaseSensor):
    _attr_name = "Connection Status"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = ["connected", "disconnected"]

    def __init__(self, coordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, "connection_status")

    @property
    def available(self) -> bool:
        # This sensor's whole job is reporting the link state, so it
        # stays available (showing "disconnected") when polls fail.
        return True

    @property
    def native_value(self):
        coordinator: MicroAirCloudCoordinator = self.coordinator
        if coordinator.mqtt_connected and coordinator.last_update_success:
            return "connected"
        return "disconnected"
