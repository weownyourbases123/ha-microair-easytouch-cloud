"""Climate entity for Micro-Air EasyTouch (Cloud).

One climate entity per thermostat: HVAC mode (Off / Fan Only / Cool /
Heat / Dry / Auto), target temperature (both setpoints in Auto), and
fan mode. Every write goes through the coordinator, which publishes
the same "Change" JSON the phone app sends and then verifies the
thermostat's own next status report before calling it done.

Micro-Air has several heat flavours (Heat, Furnace, Heat Pump, Heat
Strip, Electric Heat) and auto flavours that all collapse into Home
Assistant's single HEAT / HEAT_COOL modes. When you set HEAT, the
entity re-uses the flavour the thermostat is already in when it is a
heat flavour, and otherwise picks Furnace (the RV's heat source);
HEAT_COOL likewise preserves the auto flavour, defaulting to Auto
Furnace.
"""

from __future__ import annotations

import logging

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import protocol
from .const import (
    CONF_CONTROL_TYPE,
    CONF_DEVICE_NAME,
    CONF_SERIAL,
    DOMAIN,
)
from .coordinator import MicroAirCloudCoordinator

_LOGGER = logging.getLogger(__name__)

# Micro-Air mode int -> Home Assistant mode (U_Change_Temperature enum).
_MODE_TO_HVAC = {
    0: HVACMode.OFF,
    1: HVACMode.FAN_ONLY,
    2: HVACMode.COOL,
    3: HVACMode.HEAT,
    4: HVACMode.HEAT,
    5: HVACMode.HEAT,
    6: HVACMode.DRY,
    7: HVACMode.HEAT,
    8: HVACMode.HEAT_COOL,
    9: HVACMode.HEAT_COOL,
    10: HVACMode.HEAT_COOL,
    11: HVACMode.HEAT_COOL,
    12: HVACMode.HEAT,
}
_HEAT_FAMILY = {3, 4, 5, 7, 12}
_AUTO_FAMILY = {8, 9, 10, 11}
_DEFAULT_HEAT_MODE = 4  # Furnace
_DEFAULT_AUTO_MODE = 11  # Auto Furnace

# Setpoint Change key + status key per single-temperature HVAC mode.
_SETPOINT_KEYS = {
    HVACMode.COOL: ("cool_sp", "cool_setpoint"),
    HVACMode.HEAT: ("heat_sp", "heat_setpoint"),
    HVACMode.DRY: ("dry_sp", "dry_setpoint"),
}

FAN_MODES = ["off", "low", "high", "auto"]


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: MicroAirCloudCoordinator = entry.runtime_data
    async_add_entities([MicroAirClimate(coordinator, entry)])


class MicroAirClimate(CoordinatorEntity, ClimateEntity):
    """The thermostat itself, as a Home Assistant climate entity."""

    _attr_has_entity_name = True
    _attr_name = None  # primary entity: takes the device name
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.TARGET_TEMPERATURE_RANGE
        | ClimateEntityFeature.FAN_MODE
    )
    _attr_temperature_unit = UnitOfTemperature.FAHRENHEIT
    _attr_min_temp = 40
    _attr_max_temp = 90
    _attr_target_temperature_step = 1
    _attr_hvac_modes = [
        HVACMode.OFF,
        HVACMode.FAN_ONLY,
        HVACMode.COOL,
        HVACMode.HEAT,
        HVACMode.DRY,
        HVACMode.HEAT_COOL,
    ]
    _attr_fan_modes = FAN_MODES

    def __init__(self, coordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.data[CONF_SERIAL]}_climate"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.data[CONF_SERIAL])},
            manufacturer="Micro-Air",
            model=f"EasyTouch {entry.data.get(CONF_CONTROL_TYPE, '')}".strip(),
            name=entry.data.get(CONF_DEVICE_NAME) or entry.title,
            serial_number=entry.data[CONF_SERIAL],
        )

    # -- state ---------------------------------------------------------------
    @property
    def _data(self) -> dict:
        return self.coordinator.data or {}

    @property
    def current_temperature(self):
        return self._data.get("inside_temp")

    @property
    def hvac_mode(self) -> HVACMode | None:
        if self._data.get("system_power") is False:
            return HVACMode.OFF
        return _MODE_TO_HVAC.get(self._data.get("mode"))

    @property
    def hvac_action(self) -> HVACAction | None:
        data = self._data
        mode = self.hvac_mode
        if mode == HVACMode.OFF:
            return HVACAction.OFF
        if data.get("cooling_active"):
            return HVACAction.COOLING
        if data.get("heating_active"):
            return HVACAction.HEATING
        if mode == HVACMode.FAN_ONLY:
            return HVACAction.FAN
        if mode == HVACMode.DRY:
            return HVACAction.DRYING
        return HVACAction.IDLE

    @property
    def target_temperature(self):
        keys = _SETPOINT_KEYS.get(self.hvac_mode)
        if keys is None:
            return None
        return self._data.get(keys[1])

    @property
    def target_temperature_high(self):
        return self._data.get("auto_cool_setpoint")

    @property
    def target_temperature_low(self):
        return self._data.get("auto_heat_setpoint")

    @property
    def fan_mode(self) -> str | None:
        field = protocol.FAN_STATUS_FIELD.get(self._data.get("mode"))
        if field is None:
            return None
        return protocol.decode_fan_mode(self._data.get(field))

    @property
    def extra_state_attributes(self):
        data = self._data
        return {
            "microair_mode": data.get("mode_name"),
            "outside_temperature": data.get("outside_temp"),
            "fault_code": data.get("fault_code"),
            "firmware": data.get("firmware"),
        }

    # -- controls --------------------------------------------------------------
    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        current_raw = self._data.get("mode")
        if hvac_mode == HVACMode.OFF:
            raw = 0
        elif hvac_mode == HVACMode.FAN_ONLY:
            raw = 1
        elif hvac_mode == HVACMode.COOL:
            raw = 2
        elif hvac_mode == HVACMode.DRY:
            raw = 6
        elif hvac_mode == HVACMode.HEAT:
            raw = current_raw if current_raw in _HEAT_FAMILY else _DEFAULT_HEAT_MODE
        elif hvac_mode == HVACMode.HEAT_COOL:
            raw = current_raw if current_raw in _AUTO_FAMILY else _DEFAULT_AUTO_MODE
        else:
            return
        await self.coordinator.async_send_change({"mode": raw}, {"mode": raw})

    async def async_set_temperature(self, **kwargs) -> None:
        if kwargs.get("hvac_mode") is not None:
            await self.async_set_hvac_mode(kwargs["hvac_mode"])
        changes: dict = {}
        expect: dict = {}
        temp = kwargs.get(ATTR_TEMPERATURE)
        high = kwargs.get("target_temp_high")
        low = kwargs.get("target_temp_low")
        if high is not None:
            changes["autoCool_sp"] = round(float(high))
            expect["auto_cool_setpoint"] = round(float(high))
        if low is not None:
            changes["autoHeat_sp"] = round(float(low))
            expect["auto_heat_setpoint"] = round(float(low))
        if temp is not None:
            keys = _SETPOINT_KEYS.get(self.hvac_mode)
            if keys is not None:
                changes[keys[0]] = round(float(temp))
                expect[keys[1]] = round(float(temp))
        if changes:
            await self.coordinator.async_send_change(changes, expect)

    async def async_set_fan_mode(self, fan_mode: str) -> None:
        raw_mode = self._data.get("mode")
        key = protocol.FAN_CHANGE_KEY.get(raw_mode)
        if key is None:
            _LOGGER.warning(
                "Fan control is not offered by the app in mode %s", raw_mode
            )
            return
        field = protocol.FAN_STATUS_FIELD[raw_mode]
        value = protocol.encode_fan_mode(fan_mode, self._data.get(field))
        if value is None:
            return
        await self.coordinator.async_send_change({key: value}, {field: value})
