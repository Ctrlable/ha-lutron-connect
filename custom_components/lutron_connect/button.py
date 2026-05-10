"""Support for Lutron Connect Bridge keypad LED buttons."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import DOMAIN, LutronConnectData, _area_name, _serial_to_unique_id
from .const import CONFIG_URL, MANUFACTURER, UNASSIGNED_AREA

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Expose keypad LED buttons as press-able HA button entities."""
    data: LutronConnectData = hass.data[DOMAIN][config_entry.entry_id]
    bridge = data.bridge
    bridge_devices = bridge.get_devices()
    bridge_unique_id = _serial_to_unique_id(data.bridge_device["serial"])

    entities = []
    for button in bridge.buttons.values():
        parent_id = button.get("parent_device")
        if parent_id is None:
            continue
        parent = bridge_devices.get(parent_id)
        if parent is None:
            continue
        if "button_led" not in button:
            continue
        entities.append(LutronConnectLedButton(button, parent, data, bridge_unique_id))

    async_add_entities(entities, True)


class LutronConnectLedButton(ButtonEntity):
    """A keypad LED addressable as a button entity."""

    _attr_should_poll = False

    def __init__(
        self,
        button: dict[str, Any],
        keypad: dict[str, Any],
        data: LutronConnectData,
        bridge_unique_id: str,
    ) -> None:
        self._button = button
        self._bridge = data.bridge
        button_id = button["device_id"]
        self._attr_unique_id = f"button_{bridge_unique_id}_{button_id}"

        area = _area_name(data.bridge.areas, keypad.get("area"))
        keypad_name = keypad["name"].split("_")[-1]
        button_name = button.get("device_name") or f"button {button['button_number']}"
        self._attr_name = f"{area} {keypad_name} {button_name}"

        serial = keypad.get("serial") or f"{data.bridge_device['serial']}_{keypad['device_id']}"
        info = DeviceInfo(
            identifiers={(DOMAIN, serial)},
            manufacturer=MANUFACTURER,
            name=f"{area} {keypad_name}",
            model=f"{keypad['model']} ({keypad['type']})",
            via_device=(DOMAIN, data.bridge_device["serial"]),
            configuration_url=CONFIG_URL,
        )
        if area != UNASSIGNED_AREA:
            info["suggested_area"] = area
        self._attr_device_info = info

    async def async_press(self) -> None:
        """Simulate a button press."""
        await self._bridge.tap_button(str(self._button["device_id"]))
