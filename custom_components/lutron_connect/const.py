"""Constants for the Lutron Connect Bridge integration."""

DOMAIN = "lutron_connect"

CONF_KEYFILE = "keyfile"
CONF_CERTFILE = "certfile"
CONF_CA_CERTS = "ca_certs"

# Fire the same event name as lutron_caseta so ha-lutron-keypad-controller works unchanged.
LUTRON_CASETA_BUTTON_EVENT = "lutron_caseta_button_event"

BRIDGE_DEVICE_ID = "1"

MANUFACTURER = "Lutron Electronics Co., Inc"
CONFIG_URL = "https://device-login.lutron.com"

ATTR_SERIAL = "serial"
ATTR_TYPE = "type"
ATTR_BUTTON_TYPE = "button_type"
ATTR_LEAP_BUTTON_NUMBER = "leap_button_number"
ATTR_BUTTON_NUMBER = "button_number"
ATTR_DEVICE_NAME = "device_name"
ATTR_AREA_NAME = "area_name"
ATTR_ACTION = "action"

ACTION_PRESS = "press"
ACTION_RELEASE = "release"

UNASSIGNED_AREA = "Unassigned"

BRIDGE_TIMEOUT = 120

# Connect Bridge uses port 8090 for LEAP operations.
LEAP_PORT = 8090
# Pairing happens on port 8083 (same port name as Caseta).
PAIRING_PORT = 8083


# ── Parenting entities to the bridge device ─────────────────────────────────
# Ctrlable Pro 2026.9 dropped DeviceInfo's `via_device` (a parent *identifier*
# tuple) for `via_device_id` (the parent's device-registry id), and the old
# spelling now raises instead of warning. A `via_device` riding inside an
# entity's DeviceInfo is the exposed case: by the time core calls
# async_get_or_create no frame of ours is left on the stack, so HA cannot
# attribute the call to a custom integration and applies core behaviour —
# ReportBehavior.ERROR — which aborts the entity at add time.
#
# Probe the DeviceInfo annotations rather than pinning a core version, so one
# build serves cores on either side of the change.
from homeassistant.helpers.device_registry import DeviceInfo as _DeviceInfo

_SUPPORTS_VIA_DEVICE_ID = "via_device_id" in getattr(
    _DeviceInfo, "__annotations__", {}
)


def link_to_bridge(info, bridge_serial, bridge_ha_device_id=None):
    """Parent a DeviceInfo to the bridge device, however this core spells it."""
    if _SUPPORTS_VIA_DEVICE_ID:
        if bridge_ha_device_id:
            info["via_device_id"] = bridge_ha_device_id
    else:
        info["via_device"] = (DOMAIN, bridge_serial)
    return info
