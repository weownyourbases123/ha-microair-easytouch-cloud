"""AWS IoT MQTT link via paho-mqtt (mutual TLS, port 8883).

One persistent connection per config entry. The link publishes
'Get Status' requests and 'Change' requests, and
delivers parsed status responses to a callback. Certificate/key
arrive as PEM strings
(persisted in HA storage by the coordinator) and are materialised to
short-lived files because paho's TLS stack reads paths; the caller
owns those files and deletes them on shutdown.

TLS note: no ca_certs argument is passed, so paho falls back to the
certifi bundle shipped with Home Assistant; the AWS IoT ATS endpoint
chains to Amazon Root CA 1, which is in that bundle. No certificate
pinning exists anywhere in this path.
"""

from __future__ import annotations

import json
import logging
import os
import ssl
import threading
import time
import uuid

import paho.mqtt.client as mqtt

from . import protocol
from .const import IOT_ENDPOINT, IOT_PORT

_LOGGER = logging.getLogger(__name__)


def write_pem_files(directory: str, cert_pem: str, key_pem: str) -> tuple[str, str]:
    """Write cert/key PEM strings to files (key is 0600). Returns paths."""
    os.makedirs(directory, exist_ok=True)
    cert_path = os.path.join(directory, "cert.pem")
    key_path = os.path.join(directory, "key.pem")
    with open(cert_path, "w", encoding="utf-8") as handle:
        handle.write(cert_pem)
    fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(key_pem)
    return cert_path, key_path


def _new_client() -> mqtt.Client:
    return mqtt.Client(
        mqtt.CallbackAPIVersion.VERSION2,
        client_id=f"microair-ha-{uuid.uuid4().hex[:12]}",
        clean_session=True,
    )


class MqttLink:
    """Persistent MQTT connection to one thermostat topic."""

    def __init__(self, topic: str, on_status) -> None:
        self._topic = topic
        self._on_status = on_status  # called with parsed status dict
        self._client: mqtt.Client | None = None
        self.connected = False

    # -- paho callbacks (network thread) ------------------------------------
    def _on_connect(self, client, userdata, flags, reason_code, properties):
        self.connected = not reason_code.is_failure
        if self.connected:
            client.subscribe(self._topic, qos=0)
            _LOGGER.debug("MQTT connected; subscribed to thermostat topic")
        else:
            _LOGGER.warning("MQTT connect failed: %s", reason_code)

    def _on_disconnect(self, client, userdata, flags, reason_code, properties):
        self.connected = False
        _LOGGER.debug("MQTT disconnected: %s", reason_code)

    def _on_message(self, client, userdata, message):
        try:
            payload = json.loads(message.payload.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return
        if protocol.is_status_response(payload):
            self._on_status(protocol.parse_status(payload))

    # -- public API -----------------------------------------------------------
    def start(self, cert_path: str, key_path: str) -> None:
        """Connect (blocking briefly; paho then runs its own loop thread)."""
        client = _new_client()
        client.tls_set(
            certfile=cert_path,
            keyfile=key_path,
            tls_version=ssl.PROTOCOL_TLS_CLIENT,
        )
        client.on_connect = self._on_connect
        client.on_disconnect = self._on_disconnect
        client.on_message = self._on_message
        client.reconnect_delay_set(min_delay=2, max_delay=60)
        client.connect(IOT_ENDPOINT, IOT_PORT, keepalive=60)
        client.loop_start()
        self._client = client

    def request_status(self, email: str) -> bool:
        """Publish one Get Status request. False if not connected."""
        if self._client is None or not self.connected:
            return False
        payload = json.dumps(protocol.build_status_request(email))
        result = self._client.publish(self._topic, payload, qos=0)
        return result.rc == mqtt.MQTT_ERR_SUCCESS

    def publish_change(self, changes: dict) -> bool:
        """Publish one Change request. False if not connected."""
        if self._client is None or not self.connected:
            return False
        payload = json.dumps(protocol.build_change(changes))
        result = self._client.publish(self._topic, payload, qos=0)
        return result.rc == mqtt.MQTT_ERR_SUCCESS

    def stop(self) -> None:
        if self._client is not None:
            try:
                self._client.loop_stop()
                self._client.disconnect()
            except Exception:  # shutdown best-effort
                pass
            self._client = None
        self.connected = False


def probe_status(cert_pem: str, key_pem: str, topic: str, email: str,
                 work_dir: str, timeout: int = 30) -> dict | None:
    """One-shot setup probe: connect, ask once, wait for the answer.

    Returns the parsed status dict, or None if the thermostat did not
    answer within `timeout` seconds. Raises on connection/TLS failure
    so the config flow can tell the two failure modes apart.
    """
    cert_path, key_path = write_pem_files(work_dir, cert_pem, key_pem)
    received: dict = {}
    answered = threading.Event()

    def _on_status(parsed: dict) -> None:
        received.update(parsed)
        answered.set()

    link = MqttLink(topic, _on_status)
    link.start(cert_path, key_path)
    try:
        deadline = time.time() + timeout
        while time.time() < deadline and not answered.is_set():
            if link.connected:
                link.request_status(email)
                answered.wait(timeout=6)
            else:
                answered.wait(timeout=1)
        return dict(received) if answered.is_set() else None
    finally:
        link.stop()
        for path in (cert_path, key_path):
            try:
                os.unlink(path)
            except OSError:
                pass
