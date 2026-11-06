"""DataUpdateCoordinator for the Micro-Air cloud integration.

Owns everything long-lived for one config entry:
- the persisted IoT certificate (HA storage; minted once, then reused)
- Cognito tokens (refreshed, or re-created by sign-in when needed) --
  only used to mint a certificate if none is stored; day-to-day
  polling talks MQTT with the certificate and needs no tokens at all
- one persistent MQTT connection, polled with 'Get Status' every 60 s

Passwords, tokens and private keys are never logged.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import time

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from . import aws_auth, mqtt_client, srp
from .const import (
    CONF_EMAIL,
    CONF_PASSWORD,
    CONF_SERIAL,
    CONF_SIGNIN_LABEL,
    CONF_TOPIC,
    DOMAIN,
    POLL_INTERVAL,
    STATUS_WAIT_SECONDS,
    STORAGE_VERSION,
)

_LOGGER = logging.getLogger(__name__)


def _jwt_expiry(token: str) -> float:
    """Expiry (epoch) of a JWT without verifying it, 0 if unreadable."""
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return float(json.loads(base64.urlsafe_b64decode(payload))["exp"])
    except Exception:
        return 0.0


class MicroAirCloudCoordinator(DataUpdateCoordinator):
    """Coordinate cloud polling for one EasyTouch thermostat."""

    def __init__(self, hass: HomeAssistant, entry) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=POLL_INTERVAL,
            config_entry=entry,
        )
        self.entry = entry
        data = entry.data
        self.email: str = data[CONF_EMAIL]
        self._password: str = data[CONF_PASSWORD]
        self.topic: str = data[CONF_TOPIC]
        self._signin_label: str = data.get(CONF_SIGNIN_LABEL, "current")
        self._store: Store = Store(
            hass, STORAGE_VERSION, f"{DOMAIN}_{data[CONF_SERIAL]}"
        )
        self._stored: dict | None = None
        self._link: mqtt_client.MqttLink | None = None
        self._pem_dir = hass.config.path(".storage", DOMAIN, entry.entry_id)
        self._last_status: dict = {}
        self._status_seq = 0
        self._status_event = asyncio.Event()

    # -- storage --------------------------------------------------------------
    async def _load_stored(self) -> dict:
        if self._stored is None:
            self._stored = await self._store.async_load() or {}
        return self._stored

    async def _save_stored(self) -> None:
        await self._store.async_save(self._stored or {})

    # -- tokens / certificate ---------------------------------------------------
    async def _ensure_tokens(self) -> dict:
        """Return valid Cognito tokens, refreshing or signing in as needed."""
        stored = await self._load_stored()
        tokens = stored.get("tokens")
        if tokens and _jwt_expiry(tokens.get("id_token", "")) > time.time() + 120:
            return tokens
        pool = aws_auth.pool_by_label(self._signin_label)
        if tokens and tokens.get("refresh_token"):
            try:
                tokens = await self.hass.async_add_executor_job(
                    aws_auth.refresh_session,
                    self.email,
                    pool,
                    tokens["refresh_token"],
                )
                stored["tokens"] = tokens
                await self._save_stored()
                return tokens
            except srp.SrpAuthError:
                _LOGGER.debug("Token refresh failed; falling back to sign-in")
        tokens, pool = await self.hass.async_add_executor_job(
            aws_auth.sign_in, self.email, self._password
        )
        stored["tokens"] = tokens
        await self._save_stored()
        return tokens

    async def _ensure_certificate(self) -> dict:
        """Return the stored IoT certificate, minting one if necessary."""
        stored = await self._load_stored()
        if stored.get("certificate"):
            return stored["certificate"]
        tokens = await self._ensure_tokens()
        pool = aws_auth.pool_by_label(self._signin_label)
        creds, fed = await self.hass.async_add_executor_job(
            aws_auth.federate, tokens["id_token"], pool
        )
        _LOGGER.info(
            "Runtime certificate mint via federation combination: %s", fed["label"]
        )
        cert = await self.hass.async_add_executor_job(
            aws_auth.provision_certificate, creds
        )
        stored["certificate"] = cert
        await self._save_stored()
        return cert

    # -- MQTT lifecycle ---------------------------------------------------------
    def _handle_status_threadsafe(self, parsed: dict) -> None:
        self.hass.loop.call_soon_threadsafe(self._handle_status, parsed)

    def _handle_status(self, parsed: dict) -> None:
        self._last_status = parsed
        self._status_seq += 1
        self._status_event.set()

    async def _start_link(self) -> None:
        cert = await self._ensure_certificate()
        cert_path, key_path = await self.hass.async_add_executor_job(
            mqtt_client.write_pem_files,
            self._pem_dir,
            cert["cert_pem"],
            cert["key_pem"],
        )
        link = mqtt_client.MqttLink(self.topic, self._handle_status_threadsafe)
        await self.hass.async_add_executor_job(link.start, cert_path, key_path)
        self._link = link

    @property
    def mqtt_connected(self) -> bool:
        return bool(self._link and self._link.connected)

    # -- controls ----------------------------------------------------------------
    async def _wait_connected(self) -> bool:
        """Ensure the MQTT link exists and has finished connecting."""
        if self._link is None:
            await self._start_link()
        assert self._link is not None
        deadline = time.monotonic() + STATUS_WAIT_SECONDS
        while not self._link.connected and time.monotonic() < deadline:
            await asyncio.sleep(0.5)
        return bool(self._link.connected)

    async def _fresh_status(self, timeout: float = 12.0) -> dict | None:
        """Request a status and wait for the next NEW response."""
        assert self._link is not None
        seq_before = self._status_seq
        self._status_event.clear()
        if not self._link.request_status(self.email):
            return None
        try:
            await asyncio.wait_for(self._status_event.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            return None
        if self._status_seq == seq_before:
            return None
        return self._last_status

    async def async_send_change(self, changes: dict, expect: dict) -> bool:
        """Publish a Change and verify it against fresh thermostat status.

        `expect` maps status-dict keys to the values the thermostat
        should report afterwards (e.g. {"heat_setpoint": 79}). Returns
        True only when a post-change status confirms every expectation;
        the fresh status is pushed to the entities either way, so the
        UI always ends up showing what the thermostat actually says.
        """
        if not await self._wait_connected():
            _LOGGER.warning("Change not sent: MQTT connection is not up")
            return False
        assert self._link is not None
        if not self._link.publish_change(changes):
            _LOGGER.warning("Change publish failed: %s", changes)
            return False
        # Give the thermostat a beat to apply the change before asking
        # (the phone app likewise ignores the first status afterwards).
        await asyncio.sleep(1.2)
        for _attempt in range(2):
            fresh = await self._fresh_status()
            if fresh is None:
                break
            self.async_set_updated_data({**fresh, "connected": True})
            if all(fresh.get(key) == value for key, value in expect.items()):
                return True
            await asyncio.sleep(1.5)
        _LOGGER.warning(
            "Thermostat did not confirm change %s (expected %s)",
            changes,
            expect,
        )
        return False

    # -- polling ------------------------------------------------------------------
    async def _async_update_data(self) -> dict:
        if self._link is None:
            await self._start_link()
        assert self._link is not None
        # The link connects in the background (TLS handshake + broker
        # CONNACK); wait for it before requesting, the way the setup
        # probe does — checking immediately loses the race and the
        # first refresh fails with "MQTT connection is not up".
        deadline = time.monotonic() + STATUS_WAIT_SECONDS
        while not self._link.connected and time.monotonic() < deadline:
            await asyncio.sleep(0.5)
        seq_before = self._status_seq
        self._status_event.clear()
        if not self._link.request_status(self.email):
            raise UpdateFailed("MQTT connection is not up")
        try:
            await asyncio.wait_for(
                self._status_event.wait(), timeout=STATUS_WAIT_SECONDS
            )
        except asyncio.TimeoutError as err:
            raise UpdateFailed("Thermostat did not answer the status poll") from err
        if self._status_seq == seq_before:
            raise UpdateFailed("Thermostat did not answer the status poll")
        return {**self._last_status, "connected": True}

    async def async_shutdown(self) -> None:
        if self._link is not None:
            await self.hass.async_add_executor_job(self._link.stop)
            self._link = None

        def _cleanup() -> None:
            for name in ("cert.pem", "key.pem"):
                try:
                    os.unlink(os.path.join(self._pem_dir, name))
                except OSError:
                    pass

        await self.hass.async_add_executor_job(_cleanup)
