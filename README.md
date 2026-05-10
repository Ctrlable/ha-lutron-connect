# Lutron Connect Bridge — Home Assistant Integration

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)

A Home Assistant custom integration for the **Lutron Connect Bridge** (used with HomeworksQS systems) using the LEAP protocol over port 8090.

---

## What it does

- **Lights** — all dimmed zones (full brightness control + transitions) and switched zones (on/off)
- **Covers / Shades** — open, close, set position
- **Fans** — Low / Medium / MediumHigh / High speed
- **Scenes** — all LEAP virtual buttons / scenes
- **Occupancy sensors** — motion/occupancy groups as binary sensors
- **Keypad button events** — fires `lutron_caseta_button_event` on every keypad press/release, compatible with [ha-lutron-keypad-controller](https://github.com/Ctrlable/ha-lutron-keypad-controller)

---

## Requirements

- Home Assistant 2024.1 or later
- A Lutron Connect Bridge on your local network (HomeworksQS system)
- **Physical access** to press the small black button on the back of the bridge during initial setup

> **Note on Ketra color:** The Connect Bridge exposes Ketra/CSD zones only as standard dimmed zones over LEAP. Color temperature and hue control are not available through this protocol.

---

## Installation via HACS

1. In Home Assistant, open **HACS → Integrations**
2. Click the **⋮ menu → Custom repositories**
3. Add `https://github.com/Ctrlable/ha-lutron-connect` with category **Integration**
4. Click **Download** on the *Lutron Connect Bridge* card
5. Restart Home Assistant

---

## Setup

1. Go to **Settings → Devices & Services → Add Integration**
2. Search for **Lutron Connect Bridge**
3. Enter the IP address of your Connect Bridge
4. When prompted, **press the small black button on the back of the bridge**, then click **Submit**
5. The integration pairs using a bundled certificate, generates a device-signed certificate, and connects on port 8090

Certificates are stored in your HA config directory as `lutron_connect-{bridge_id}-{key|cert|ca}.pem`.

---

## Keypad Controller compatibility

This integration fires the standard `lutron_caseta_button_event` HA event on every keypad button press and release, with the same payload as the built-in `lutron_caseta` integration:

| Field | Description |
|---|---|
| `serial` | Keypad serial number |
| `type` | Keypad device type |
| `leap_button_number` | LEAP button number |
| `button_number` | LIP button number (None for Connect Bridge) |
| `device_name` | Keypad name |
| `device_id` | HA device registry ID |
| `area_name` | Area name |
| `button_type` | Button label/name |
| `action` | `press` or `release` |

[ha-lutron-keypad-controller](https://github.com/Ctrlable/ha-lutron-keypad-controller) works with this integration without any changes — it listens for the same event regardless of which Lutron integration fires it.

---

## Supported device types

| LEAP type | HA platform |
|---|---|
| `Dimmed`, `SpectrumTune`, `WhiteTune`, `ColorTune` | Light (brightness) |
| `Switched` | Light (on/off) |
| `Shade`, `SerenaRollerShade`, `QsWirelessShade`, etc. | Cover |
| `FanSpeed`, `CeilingFan` | Fan |
| `KeypadLED` | Switch |
| Occupancy groups | Binary sensor |
| Virtual buttons | Scene |
| SeeTouch, Sunnata, Pico keypads | Button events |

---

## Known limitations

- **Ketra / CSD color control** is not supported — the Connect Bridge LEAP API does not expose color commands for these zone types
- LIP button numbers are reported as `None` (the Connect Bridge does not use the LIP protocol)
- Cloud pairing (generating new certificates via the Lutron cloud API) is not currently possible due to AWS SigV4 auth changes on Lutron's side — the integration ships with the bundled Connect LAP certificate required to initiate local pairing

---

## License

MIT
