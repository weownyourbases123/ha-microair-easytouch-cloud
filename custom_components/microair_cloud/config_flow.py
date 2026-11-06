"""Config flow for Micro-Air EasyTouch (Cloud).

Setup validates the FULL chain before an entry is created:
sign-in -> federation -> device list -> (device pick if several) ->
certificate mint -> MQTT connect -> one Get Status poll. The entry is
created only if the thermostat actually answers. Errors are distinct
and diagnosable: auth_failed / no_devices / iot_connect_failed /
no_status_response.

The password entered here goes only to Amazon Cognito (SRP) from
this flow; it is stored by Home Assistant in the config entry, like
any other integration credential, and is never logged.
"""

from __future__ import annotations

import logging
import os
import tempfile
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.helpers.selector import (
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)
from homeassistant.helpers.storage import Store

from . import aws_auth, mqtt_client
from .const import (
    CONF_CONTROL_TYPE,
    CONF_DEVICE_NAME,
    CONF_EMAIL,
    CONF_FEDERATION_LABEL,
    CONF_PASSWORD,
    CONF_SERIAL,
    CONF_SIGNIN_LABEL,
    CONF_TOPIC,
    DOMAIN,
    STORAGE_VERSION,
)

_LOGGER = logging.getLogger(__name__)

_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_EMAIL): TextSelector(
            TextSelectorConfig(type=TextSelectorType.EMAIL, autocomplete="email")
        ),
        vol.Required(CONF_PASSWORD): TextSelector(
            TextSelectorConfig(type=TextSelectorType.PASSWORD)
        ),
    }
)


def _probe(cert: dict, topic: str, email: str) -> dict | None:
    """Blocking one-shot MQTT status probe in a throwaway directory."""
    work_dir = tempfile.mkdtemp(prefix="microair_probe_")
    try:
        return mqtt_client.probe_status(
            cert["cert_pem"], cert["key_pem"], topic, email, work_dir, timeout=30
        )
    finally:
        try:
            os.rmdir(work_dir)
        except OSError:
            pass


class MicroAirCloudConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Micro-Air EasyTouch (Cloud)."""

    VERSION = 1

    def __init__(self) -> None:
        self._pending: dict[str, Any] = {}
        self._devices: list[dict] = []

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            email = user_input[CONF_EMAIL].strip()
            password = user_input[CONF_PASSWORD]
            try:
                validated = await self.hass.async_add_executor_job(
                    aws_auth.validate_account, email, password
                )
            except aws_auth.AuthFailed:
                errors["base"] = "auth_failed"
            except aws_auth.NoDevices:
                errors["base"] = "no_devices"
            except aws_auth.ChainFailed as err:
                _LOGGER.warning("Micro-Air setup chain failed: %s", err)
                errors["base"] = "iot_connect_failed"
            else:
                _LOGGER.info(
                    "Micro-Air setup: sign-in config=%s, federation config=%s, "
                    "%d thermostat(s) on the account",
                    validated["pool"]["label"],
                    validated["federation"]["label"],
                    len(validated["devices"]),
                )
                self._pending = {
                    CONF_EMAIL: email,
                    CONF_PASSWORD: password,
                    "tokens": validated["tokens"],
                    "credentials": validated["credentials"],
                    "account_email": validated["account_email"],
                    CONF_SIGNIN_LABEL: validated["pool"]["label"],
                    CONF_FEDERATION_LABEL: validated["federation"]["label"],
                }
                self._devices = validated["devices"]
                if len(self._devices) == 1:
                    return await self._async_finish(self._devices[0])
                return await self.async_step_device()
        return self.async_show_form(
            step_id="user", data_schema=_USER_SCHEMA, errors=errors
        )

    async def async_step_device(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            serial = user_input[CONF_SERIAL]
            device = next(d for d in self._devices if d["serial"] == serial)
            return await self._async_finish(device)
        options = {
            d["serial"]: f"{d['name']} (EasyTouch {d['control_type']}, {d['serial']})"
            for d in self._devices
        }
        return self.async_show_form(
            step_id="device",
            data_schema=vol.Schema({vol.Required(CONF_SERIAL): vol.In(options)}),
        )

    async def _async_finish(self, device: dict) -> ConfigFlowResult:
        serial = device["serial"]
        await self.async_set_unique_id(serial)
        self._abort_if_unique_id_configured()
        topic = f"{device['control_type']} {serial}"
        # The canonical (registered-case) email is what the thermostat
        # expects in status requests and what the entry stores; the
        # as-typed email was only needed for the first sign-in.
        email = self._pending.get("account_email") or self._pending[CONF_EMAIL]

        try:
            cert = await self.hass.async_add_executor_job(
                aws_auth.provision_certificate, self._pending["credentials"]
            )
        except aws_auth.ChainFailed as err:
            _LOGGER.warning("Micro-Air certificate mint failed: %s", err)
            return self.async_show_form(
                step_id="user",
                data_schema=_USER_SCHEMA,
                errors={"base": "iot_connect_failed"},
            )

        try:
            status = await self.hass.async_add_executor_job(
                _probe, cert, topic, email
            )
        except Exception as err:  # TLS / connect failure during the probe
            _LOGGER.warning("Micro-Air MQTT probe failed to connect: %s", err)
            return self.async_show_form(
                step_id="user",
                data_schema=_USER_SCHEMA,
                errors={"base": "iot_connect_failed"},
            )
        if status is None:
            return self.async_show_form(
                step_id="user",
                data_schema=_USER_SCHEMA,
                errors={"base": "no_status_response"},
            )
        _LOGGER.info(
            "Micro-Air setup probe: thermostat %s answered (inside temp %s)",
            serial,
            status.get("inside_temp"),
        )

        store = Store(self.hass, STORAGE_VERSION, f"{DOMAIN}_{serial}")
        await store.async_save(
            {"certificate": cert, "tokens": self._pending["tokens"]}
        )

        title = device["name"] or f"EasyTouch {device['control_type']}"
        return self.async_create_entry(
            title=title,
            data={
                CONF_EMAIL: email,
                CONF_PASSWORD: self._pending[CONF_PASSWORD],
                CONF_SERIAL: serial,
                CONF_CONTROL_TYPE: device["control_type"],
                CONF_DEVICE_NAME: device["name"],
                CONF_TOPIC: topic,
                CONF_SIGNIN_LABEL: self._pending[CONF_SIGNIN_LABEL],
                CONF_FEDERATION_LABEL: self._pending[CONF_FEDERATION_LABEL],
            },
        )
