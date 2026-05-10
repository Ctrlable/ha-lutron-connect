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
