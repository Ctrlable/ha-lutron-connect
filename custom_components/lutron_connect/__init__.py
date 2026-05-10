"""Lutron Connect Bridge integration — LEAP-based support for HomeworksQS."""

from __future__ import annotations

import asyncio
import logging
import ssl
from typing import Any, cast

from pylutron_caseta import BUTTON_STATUS_PRESSED
from pylutron_caseta.smartbridge import Smartbridge

from homeassistant import config_entries
from homeassistant.const import ATTR_DEVICE_ID, ATTR_SUGGESTED_AREA, CONF_HOST, Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceInfo

from .const import (
    ACTION_PRESS,
    ACTION_RELEASE,
    ATTR_ACTION,
    ATTR_AREA_NAME,
    ATTR_BUTTON_NUMBER,
    ATTR_BUTTON_TYPE,
    ATTR_DEVICE_NAME,
    ATTR_LEAP_BUTTON_NUMBER,
    ATTR_SERIAL,
    ATTR_TYPE,
    BRIDGE_DEVICE_ID,
    BRIDGE_TIMEOUT,
    CONF_CA_CERTS,
    CONF_CERTFILE,
    CONF_KEYFILE,
    CONFIG_URL,
    DOMAIN,
    LEAP_PORT,
    LUTRON_CASETA_BUTTON_EVENT,
    MANUFACTURER,
    UNASSIGNED_AREA,
)

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.COVER,
    Platform.FAN,
    Platform.LIGHT,
    Platform.SCENE,
    Platform.SWITCH,
]


async def async_setup_entry(
    hass: HomeAssistant, config_entry: config_entries.ConfigEntry
) -> bool:
    """Set up a Connect Bridge from a config entry."""
    host = config_entry.data[CONF_HOST]
    keyfile = hass.config.path(config_entry.data[CONF_KEYFILE])
    certfile = hass.config.path(config_entry.data[CONF_CERTFILE])
    ca_certs = hass.config.path(config_entry.data[CONF_CA_CERTS])

    try:
        bridge = Smartbridge.create_tls(
            hostname=host,
            keyfile=keyfile,
            certfile=certfile,
            ca_certs=ca_certs,
            port=LEAP_PORT,
        )
    except ssl.SSLError:
        _LOGGER.error("Invalid certificate for Connect Bridge at %s", host)
        return False

    timed_out = True
    try:
        async with asyncio.timeout(BRIDGE_TIMEOUT):
            await bridge.connect()
            timed_out = False
    except TimeoutError:
        pass
    except (OSError, ssl.SSLError) as exc:
        await bridge.close()
        raise ConfigEntryNotReady(f"Cannot connect to {host}: {exc}") from exc

    if timed_out or not bridge.is_connected():
        await bridge.close()
        if timed_out:
            raise ConfigEntryNotReady(f"Timed out connecting to {host}")
        raise ConfigEntryNotReady(f"Cannot connect to {host}")

    _LOGGER.debug("Connected to Lutron Connect Bridge at %s via LEAP", host)

    bridge_devices = bridge.get_devices()
    bridge_device = bridge_devices[BRIDGE_DEVICE_ID]

    entry_id = config_entry.entry_id

    if not config_entry.unique_id:
        hass.config_entries.async_update_entry(
            config_entry, unique_id=_serial_to_unique_id(bridge_device["serial"])
        )

    _async_register_bridge_device(hass, entry_id, bridge_device, bridge)
    keypad_data = _async_setup_keypads(hass, entry_id, bridge, bridge_device)

    hass.data.setdefault(DOMAIN, {})[entry_id] = LutronConnectData(
        bridge=bridge,
        bridge_device=bridge_device,
        keypads=keypad_data["keypads"],
        keypad_buttons=keypad_data["keypad_buttons"],
    )

    await hass.config_entries.async_forward_entry_setups(config_entry, PLATFORMS)
    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: config_entries.ConfigEntry
) -> bool:
    """Unload a config entry."""
    data: LutronConnectData = hass.data[DOMAIN][entry.entry_id]
    await data.bridge.close()
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok


class LutronConnectData:
    """Runtime data stored per config entry."""

    def __init__(self, bridge, bridge_device, keypads, keypad_buttons):
        self.bridge: Smartbridge = bridge
        self.bridge_device: dict[str, Any] = bridge_device
        self.keypads: dict[int, dict] = keypads
        self.keypad_buttons: dict[int, dict] = keypad_buttons


@callback
def _async_register_bridge_device(
    hass: HomeAssistant,
    config_entry_id: str,
    bridge_device: dict,
    bridge: Smartbridge,
) -> None:
    device_registry = dr.async_get(hass)
    area = _area_name(bridge.areas, bridge_device.get("area"))
    info = DeviceInfo(
        name=bridge_device["name"],
        manufacturer=MANUFACTURER,
        identifiers={(DOMAIN, bridge_device["serial"])},
        model=f"{bridge_device['model']} ({bridge_device['type']})",
        configuration_url=CONFIG_URL,
    )
    if area != UNASSIGNED_AREA:
        info[ATTR_SUGGESTED_AREA] = area
    device_registry.async_get_or_create(**info, config_entry_id=config_entry_id)


@callback
def _async_setup_keypads(
    hass: HomeAssistant,
    config_entry_id: str,
    bridge: Smartbridge,
    bridge_device: dict,
) -> dict:
    device_registry = dr.async_get(hass)
    bridge_devices = bridge.get_devices()
    bridge_buttons = bridge.buttons

    keypads: dict[int, dict] = {}
    keypad_buttons: dict[int, dict] = {}

    for bridge_button in bridge_buttons.values():
        parent_id = cast(str, bridge_button["parent_device"])
        bridge_keypad = bridge_devices[parent_id]
        keypad_device_id = cast(int, bridge_keypad["device_id"])
        button_device_id = cast(int, bridge_button["device_id"])
        leap_button_number = cast(int, bridge_button["button_number"])

        if not (keypad := keypads.get(keypad_device_id)):
            area_name = _area_name(bridge.areas, bridge_keypad.get("area"))
            keypad_name = bridge_keypad["name"].split("_")[-1]
            serial = bridge_keypad.get("serial") or f"{bridge_device['serial']}_{keypad_device_id}"
            info = DeviceInfo(
                name=f"{area_name} {keypad_name}",
                manufacturer=MANUFACTURER,
                identifiers={(DOMAIN, serial)},
                model=f"{bridge_keypad['model']} ({bridge_keypad['type']})",
                via_device=(DOMAIN, bridge_device["serial"]),
            )
            if area_name != UNASSIGNED_AREA:
                info[ATTR_SUGGESTED_AREA] = area_name

            dr_device = device_registry.async_get_or_create(
                **info, config_entry_id=config_entry_id
            )
            keypad = keypads[keypad_device_id] = {
                "lutron_device_id": keypad_device_id,
                "dr_device_id": dr_device.id,
                "area_name": area_name,
                "name": keypad_name,
                "serial": serial,
                "type": bridge_keypad["type"],
                "buttons": [],
            }

        button_name = bridge_button.get("device_name") or f"button {leap_button_number}"
        keypad_buttons[button_device_id] = {
            "lutron_device_id": button_device_id,
            "leap_button_number": leap_button_number,
            "button_name": button_name,
            "parent_keypad": keypad_device_id,
        }
        keypad["buttons"].append(button_device_id)

    _async_subscribe_button_events(hass, bridge, keypads, keypad_buttons)

    return {"keypads": keypads, "keypad_buttons": keypad_buttons}


@callback
def _async_subscribe_button_events(
    hass: HomeAssistant,
    bridge: Smartbridge,
    keypads: dict[int, dict],
    keypad_buttons: dict[int, dict],
) -> None:
    @callback
    def _on_button_event(button_id: int, event_type):
        button = keypad_buttons.get(button_id)
        if not button:
            return
        keypad = keypads.get(button["parent_keypad"])
        if not keypad:
            return

        action = ACTION_PRESS if event_type == BUTTON_STATUS_PRESSED else ACTION_RELEASE
        hass.bus.async_fire(
            LUTRON_CASETA_BUTTON_EVENT,
            {
                ATTR_SERIAL: keypad["serial"],
                ATTR_TYPE: keypad["type"],
                ATTR_BUTTON_NUMBER: None,
                ATTR_LEAP_BUTTON_NUMBER: button["leap_button_number"],
                ATTR_DEVICE_NAME: keypad["name"],
                ATTR_DEVICE_ID: keypad["dr_device_id"],
                ATTR_AREA_NAME: keypad["area_name"],
                ATTR_BUTTON_TYPE: button["button_name"],
                ATTR_ACTION: action,
            },
        )

    for button_id in keypad_buttons:
        bridge.add_button_subscriber(
            str(button_id),
            lambda event_type, bid=button_id: _on_button_event(bid, event_type),
        )


def _area_name(areas: dict, area_id: str | None) -> str:
    if area_id is None:
        return UNASSIGNED_AREA
    return _build_area_name(areas, area_id, [])


def _build_area_name(areas: dict, area_id: str, labels: list[str]) -> str:
    area = areas[area_id]
    parent_id = area.get("parent_id")
    if parent_id is None:
        return " ".join(labels)
    labels.insert(0, area["name"])
    return _build_area_name(areas, parent_id, labels)


def _serial_to_unique_id(serial: int | str | None) -> str:
    if serial is None:
        return "unknown"
    return hex(int(serial))[2:].zfill(8)


class LutronConnectDevice:
    """Base mixin for a Connect Bridge device entity."""

    _attr_should_poll = False

    def __init__(self, device: dict[str, Any], data: LutronConnectData) -> None:
        self._device = device
        self._bridge = data.bridge
        self._bridge_device = data.bridge_device
        self._bridge_unique_id = _serial_to_unique_id(data.bridge_device["serial"])

        if "serial" not in device or "parent_device" in device:
            return

        area = _area_name(self._bridge.areas, device.get("area"))
        name = device["name"].split("_")[-1]
        self._attr_name = full_name = f"{area} {name}"
        serial = self._handle_none_serial(device["serial"])
        info = DeviceInfo(
            identifiers={(DOMAIN, serial)},
            manufacturer=MANUFACTURER,
            model=f"{device['model']} ({device['type']})",
            name=full_name,
            via_device=(DOMAIN, self._bridge_device["serial"]),
            configuration_url=CONFIG_URL,
        )
        if area != UNASSIGNED_AREA:
            info[ATTR_SUGGESTED_AREA] = area
        self._attr_device_info = info

    def _handle_none_serial(self, serial):
        if serial is None:
            return f"{self._bridge_unique_id}_{self.device_id}"
        return serial

    async def async_added_to_hass(self):
        self._bridge.add_subscriber(self.device_id, self.async_write_ha_state)

    @property
    def device_id(self):
        return self._device["device_id"]

    @property
    def unique_id(self) -> str:
        return str(self._handle_none_serial(self._device.get("serial")))

    @property
    def extra_state_attributes(self):
        attrs = {"device_id": self.device_id}
        if zone := self._device.get("zone"):
            attrs["zone_id"] = zone
        return attrs
