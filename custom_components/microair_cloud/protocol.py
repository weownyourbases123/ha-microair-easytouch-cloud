"""Thermostat JSON protocol helpers.

The EasyTouch speaks the same JSON over AWS IoT MQTT as it does over
Bluetooth. `build_status_request` builds the "Get Status" messages;
`build_change` builds the same "Change" messages the phone app sends
for mode / setpoint / fan edits.
"""

from __future__ import annotations

import time

from .const import MODE_NAMES

# Change-message fan key for each operating mode, from the app's fan
# dispatch (U_Thermostat.fan_button_pressed). Off (0) and Dry (6) have
# no fan key — the app sends nothing for them.
FAN_CHANGE_KEY = {
    1: "fanOnly",
    2: "coolFan",
    8: "autoFan",
    9: "autoFan",
    10: "autoFan",
    11: "autoFan",
    3: "eleFan",
    5: "eleFan",
    7: "eleFan",
    12: "eleFan",
    4: "gasFan",
}

# Status dict field holding the fan speed relevant to each mode
# (U_Thermostat.getFanSpeed / setFanState). Dry falls back to the cool
# fan field for display; the app offers no fan control in Dry.
FAN_STATUS_FIELD = {
    0: "cool_fan_speed",
    1: "fan_only_speed",
    2: "cool_fan_speed",
    8: "auto_fan_speed",
    9: "auto_fan_speed",
    10: "auto_fan_speed",
    11: "auto_fan_speed",
    3: "electric_fan_speed",
    5: "electric_fan_speed",
    7: "electric_fan_speed",
    12: "electric_fan_speed",
    4: "gas_fan_speed",
    6: "cool_fan_speed",
}


def build_status_request(email: str) -> dict:
    """Build a 'Get Status' request exactly as the app sends it (Wi-Fi mode)."""
    return {
        "Type": "Get Status",
        "Zone": 0,
        "TM": int(time.time()),
        "EM": email,
    }


def build_change(changes: dict) -> dict:
    """Build a 'Change' request exactly as the app sends it.

    `changes` uses the app's keys (U_Thermostat.sendCommand): "mode",
    "power", the setpoints "cool_sp" / "heat_sp" / "dry_sp" /
    "autoHeat_sp" / "autoCool_sp", and the fan keys in FAN_CHANGE_KEY.
    Example — Furnace mode, 78 F: {"mode": 4, "heat_sp": 78}.
    """
    return {"Type": "Change", "Changes": {"zone": 0, **changes}}


def decode_fan_mode(raw) -> str | None:
    """Home Assistant fan-mode label for a raw fan speed value.

    Raw encoding (from the app's setFanState): 0 = off; the low bits
    are the manual speed (1 = low, 2+ = high); bit 6 or bit 7 set
    means an automatic fan mode.
    """
    if raw is None:
        return None
    if raw == 0:
        return "off"
    if raw & 0xC0:
        return "auto"
    return "low" if (raw & 0x0F) <= 1 else "high"


def encode_fan_mode(label: str, current_raw) -> int | None:
    """Raw fan speed value for a Home Assistant fan-mode label.

    "auto" keeps the current speed bits under the auto flag (64),
    defaulting to speed 1 — the app's canonical auto value is 65.
    """
    if label == "off":
        return 0
    if label == "low":
        return 1
    if label == "high":
        return 2
    if label == "auto":
        speed = (current_raw or 0) & 0x0F
        return 0x40 | (speed if speed else 1)
    return None


def is_status_response(payload: dict) -> bool:
    """True if a topic payload is a thermostat status response."""
    return (
        isinstance(payload, dict)
        and payload.get("Type") == "Response"
        and payload.get("RT") == "Status"
    )


def _zone_array(payload: dict) -> list:
    """Return the positional Z_sts array for zone 0, or []."""
    z_sts = payload.get("Z_sts") or {}
    zone = z_sts.get("0", z_sts.get(0, []))
    return zone if isinstance(zone, list) else []


def _at(values: list, index: int):
    return values[index] if len(values) > index else None


def parse_status(payload: dict) -> dict:
    """Decode a status response into a flat dict for the coordinator.

    Z_sts positional layout:
    [0] autoHeatSP [1] autoCoolSP [2] coolSP [3] heatSP [4] drySP
    [5] humiditySP [6] fanOnly [7] coolFan [8] electricFan [9] autoFan
    [10] mode [11] gasFan [12] ambient indoor temp
    [13] outside temp (255 = none) [14] fault code [15] status bits
    Status bits: bit0 cycle active, bit1 cooling, bit2 heating.
    PRM[1] is a flags bitfield; bit3 = system power.
    """
    zone = _zone_array(payload)
    prm = payload.get("PRM") or []
    mode = _at(zone, 10)
    outside = _at(zone, 13)
    bits = _at(zone, 15) or 0
    prm_flags = prm[1] if isinstance(prm, list) and len(prm) > 1 else None
    return {
        "inside_temp": _at(zone, 12),
        "mode": mode,
        "mode_name": MODE_NAMES.get(mode, "Unknown"),
        "cool_setpoint": _at(zone, 2),
        "heat_setpoint": _at(zone, 3),
        "auto_heat_setpoint": _at(zone, 0),
        "auto_cool_setpoint": _at(zone, 1),
        "dry_setpoint": _at(zone, 4),
        "humidity_setpoint": _at(zone, 5),
        "fan_only_speed": _at(zone, 6),
        "cool_fan_speed": _at(zone, 7),
        "electric_fan_speed": _at(zone, 8),
        "auto_fan_speed": _at(zone, 9),
        "gas_fan_speed": _at(zone, 11),
        "outside_temp": None if outside in (None, 255) else outside,
        "fault_code": _at(zone, 14),
        "status_bits": bits,
        "cycle_active": bool(bits & 1),
        "cooling_active": bool(bits & 2),
        "heating_active": bool(bits & 4),
        "system_power": (
            bool(prm_flags & 8) if isinstance(prm_flags, int) else None
        ),
        "firmware": payload.get("REV"),
        "faceplate_temp": _at(prm, 3) if isinstance(prm, list) else None,
    }
