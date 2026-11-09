<h1 align="center">Micro-Air EasyTouch (Cloud)</h1>

<p align="center"><em>Home Assistant integration for Micro-Air EasyTouch thermostats — through the cloud path the official app uses, not Bluetooth.</em></p>

<p align="center"><img src="https://img.shields.io/badge/-UNOFFICIAL%20%E2%80%94%20community%20integration-0000AA?style=for-the-badge" alt="Unofficial — community integration"></p>

<p align="center"><a href="https://github.com/custom-components/hacs"><img src="https://img.shields.io/badge/HACS-Custom-orange.svg?style=for-the-badge" alt="HACS Custom"></a> <a href="https://github.com/weownyourbases123/ha-microair-easytouch-cloud/releases"><img src="https://img.shields.io/github/v/release/weownyourbases123/ha-microair-easytouch-cloud?style=for-the-badge" alt="GitHub Release"></a></p>

> **Unofficial.** This is a community integration. It is not made by,
> endorsed by, or affiliated with Micro-Air. Use at your own risk.

A Home Assistant custom integration that talks to a Micro-Air
EasyTouch thermostat (developed and tested on an **EasyTouch 351**)
through the **cloud path the official app uses**, instead of
Bluetooth.

Why cloud instead of Bluetooth? On hosts with a single Bluetooth
adapter, a Bluetooth integration that holds a connection to the
thermostat can starve other BLE devices (tank sensors, temperature
sensors, …) that share the same radio. This integration never touches
Bluetooth at all.

## Features

- **Climate entity** with controls: HVAC mode (Off / Fan Only / Cool /
  Heat / Dry / Auto), target temperature (both setpoints in Auto),
  and fan mode (Off / Low / High / Auto).
- **Sensors:** inside temperature, current mode (with all setpoints
  as attributes), and connection status.
- Every control change is **verified**: after sending a change, the
  integration asks the thermostat for a fresh status and only trusts
  the change if the thermostat's own report reflects it.
- Setup performs the full connection test — sign-in, device lookup,
  certificate provisioning, and a live status request — and only
  creates the entry if the thermostat actually answers.
- **Settings page** (the entry's **Configure** button): status check
  interval (30–600 seconds, default 60) and a debug-logging switch,
  both applied live.
- **Diagnostics:** Home Assistant's built-in *Download diagnostics*
  on the integration page shows connection state and the thermostat's
  last status (credentials redacted).

## Requirements

- A Micro-Air account (the same email/password as the **EasyTouch RV**
  app) with at least one thermostat added.
- Home Assistant with internet access (the thermostat is reached via
  AWS IoT; nothing is exposed on your LAN).
- Developed and tested against: EasyTouch 351 (firmware 1.0.6.10),
  Home Assistant 2026.10. Other EasyTouch models that use the same
  app are expected to work but are untested — please file an issue
  with your model and firmware either way.

## Installation

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=weownyourbases123&repository=ha-microair-easytouch-cloud&category=integration)

**HACS (custom repository):** add this repository as a custom
repository (category: Integration), install "Micro-Air EasyTouch
(Cloud)", and restart Home Assistant.

**Manual:** copy `custom_components/microair_cloud/` into your Home
Assistant `config/custom_components/` folder and restart.

Then: **Settings → Devices & services → Add Integration → "Micro-Air
EasyTouch (Cloud)"**, and sign in with your Micro-Air account.

## Settings

After setup, the entry's **Configure** button opens its settings:

- **Status check interval** — how often Home Assistant asks the
  thermostat for a fresh status (30–600 seconds, default 60 seconds). Lower
  values react faster but talk to the cloud more often.
- **Debug logging** — detailed logging for this integration only,
  visible under **Settings → System → Logs**. Handy when reporting a
  problem; turn it back off when you're done. Home Assistant also has
  a built-in one-shot route — ⋮ menu → Enable debug logging on this
  integration's page, which downloads the log when you turn it back
  off. This switch instead keeps debug logging on until you turn it
  off.

Both apply the moment you save — no restart. For a one-shot snapshot
of the connection and the thermostat's last reported status, use
**Download diagnostics** in the integration page's ⋮ menu (account
details are redacted automatically).

## How it works

The phone app doesn't talk to a proprietary Micro-Air API — it is
built directly on Amazon Web Services, and this integration does the
same thing the app does:

1. **Sign-in** to Amazon Cognito with SRP (the password itself is
   never sent over the wire).
2. **Device list** read from the account's record in a DynamoDB table
   to learn the thermostat's serial number. (The record is keyed by
   the email address exactly as registered — including its case —
   which the integration takes from the sign-in token.)
3. **A client certificate** is requested from AWS IoT and the app's
   policy is attached — once. It is stored by Home Assistant and
   reused; tokens are refreshed as needed.
4. **Live channel:** one MQTT/TLS connection to AWS IoT on the
   thermostat's private topic. Polls "Get Status" at your status check interval (every 60 seconds by default) and
   publishes "Change" messages for controls — the same JSON the app
   sends over Bluetooth.

## Privacy & credentials

- Your email and password are entered only in Home Assistant's setup
  screen and stored by Home Assistant in the config entry, like any
  other integration. Your password, sign-in tokens and private keys
  are never logged, and none of your credentials are written into any
  file in this repository.
- The IoT certificate and sign-in tokens live in Home Assistant's
  `.storage/` area; the private key file used at runtime is
  owner-only (0600) and removed when the integration unloads.
- The AWS identifiers in `const.py` (Cognito pool IDs, client IDs
  and client secrets) are the vendor app's own values, published
  inside the public app and required for its SRP sign-in; they are
  not your credentials.

## Known limitations

- **Zone 0 only** (covers most RV installs).
- Setpoints are native **Fahrenheit**, as the thermostat reports them.
- Micro-Air's heat flavours (Furnace, Heat Pump, Heat Strip, …) map
  to Home Assistant's single "Heat" mode; setting Heat re-uses the
  flavour already in use, defaulting to Furnace. The exact mode is
  always visible in the Current Mode sensor and the climate entity's
  `microair_mode` attribute. Auto flavours behave the same way.
- Accounts belong to the **EasyTouch RV** app. The marine "Easy
  Connect" app uses a different account system and is not supported.
- Depends on Micro-Air's AWS backend staying as it is; a future app
  update could change it.

## Removing it

Delete the config entry in Home Assistant (Settings → Devices &
services → Micro-Air EasyTouch (Cloud) → ⋮ → Delete), then remove
the `custom_components/microair_cloud/` folder and restart. The
thermostat and the phone app are unaffected throughout.

## License

MIT — see `LICENSE`.

## Changelog

- **0.3.0** — Added a settings (options) page: status check interval and debug logging, applied live. Added Download diagnostics support. Refreshed the README presentation.
- **0.2.1** — Fixed mode changes doing nothing after the thermostat was powered off at the unit or in the app: the thermostat tracks power separately from mode, so mode changes now send power along with the mode, and Off sends power alone (mirroring the app's power button).
