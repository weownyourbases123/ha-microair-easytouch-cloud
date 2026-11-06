# Micro-Air EasyTouch (Cloud) — integration files

This folder is the integration itself. For installation, setup, and
the full explanation of how it works, see the **README at the
repository root**.

Short version: it connects Home Assistant to a Micro-Air EasyTouch
thermostat through the same AWS cloud path the official EasyTouch RV
app uses (Cognito sign-in → DynamoDB device list → AWS IoT MQTT),
instead of Bluetooth. It provides status sensors and a climate entity
with verified controls.

| File | What it does |
|---|---|
| `manifest.json` | The integration's ID card: name, version, libraries it needs. |
| `__init__.py` | Starts/stops the integration when Home Assistant does. |
| `config_flow.py` | The setup screens (email/password, thermostat picker) and the full connection test that runs before setup is allowed to finish. |
| `aws_auth.py` | The Amazon sign-in chain: sign-in → temporary credentials → thermostat list → minting the certificate. |
| `srp.py` | The Cognito SRP sign-in math. Small and self-contained so it can be audited. |
| `mqtt_client.py` | The encrypted connection to the thermostat; sends "Get Status" and "Change", receives answers. |
| `protocol.py` | Translates between the thermostat's JSON and plain values, in both directions (status decoding and Change building). |
| `coordinator.py` | The conductor: keeps the certificate and sign-in tokens fresh, polls every 60 seconds, feeds the entities, and verifies every control change against the thermostat's reply. |
| `sensor.py` | The three sensors: Inside Temperature, Current Mode (setpoints as attributes), Connection Status. |
| `climate.py` | The thermostat entity itself: mode buttons, target temperature, fan mode — the control surface dashboards and automations use. |
| `const.py` | Addresses and settings (Amazon endpoints, poll interval, mode names). The Cognito pool IDs, client IDs and client secrets here are the app's own identifiers, printed inside the public app and required for its SRP sign-in — not user credentials. |
| `strings.json`, `translations/` | The wording of the setup screens and error messages. |

Credentials are entered only in Home Assistant's own setup screen
and stored by Home Assistant — never in any file here. Passwords,
tokens, and keys are never written to the log.
