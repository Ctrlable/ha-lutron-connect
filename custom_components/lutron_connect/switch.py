"""Support for Lutron Connect Bridge KeypadLED switches."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.switch import SwitchEntity
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
    """Expose keypad LED indicator switches."""
    data: LutronConnectData = hass.data[DOMAIN][config_entry.entry_id]
    bridge = data.bridge
    bridge_devices = bridge.get_devices()

    entities = []
    for device in bridge.get_devices_by_domain("switch"):
        if device.get("type") != "KeypadLED":
            continue
        parent_id = device.get("parent_device")
        parent = bridge_devices.get(parent_id) if parent_id else None
        entities.append(LutronConnectKeypadLED(device, parent, data))

    async_add_entities(entities, True)


class LutronConnectKeypadLED(SwitchEntity):
    """A keypad LED indicator as an on/off switch."""

    _attr_should_poll = False

    def __init__(
        self,
        device: dict[str, Any],
        parent_keypad: dict[str, Any] | None,
        data: LutronConnectData,
    ) -> None:
        self._device = device
        self._bridge = data.bridge
        bridge_unique_id = _serial_to_unique_id(data.bridge_device["serial"])
        self._attr_unique_id = f"led_{bridge_unique_id}_{device['device_id']}"

        if parent_keypad:
            area = _area_name(data.bridge.areas, parent_keypad.get("area"))
            keypad_name = parent_keypad["name"].split("_")[-1]
            led_name = device.get("device_name") or f"LED {device['button_number']}"
            self._attr_name = f"{area} {keypad_name} {led_name}"

            serial = parent_keypad.get("serial") or f"{data.bridge_device['serial']}_{parent_keypad['device_id']}"
            info = DeviceInfo(
                identifiers={(DOMAIN, serial)},
                manufacturer=MANUFACTURER,
                name=f"{area} {keypad_name}",
                model=f"{parent_keypad['model']} ({parent_keypad['type']})",
                via_device=(DOMAIN, data.bridge_device["serial"]),
                configuration_url=CONFIG_URL,
            )
            if area != UNASSIGNED_AREA:
                info["suggested_area"] = area
            self._attr_device_info = info
        else:
            self._attr_name = device.get("name", f"LED {device['device_id']}")

    @property
    def device_id(self):
        return self._device["device_id"]

    async def async_added_to_hass(self) -> None:
        self._bridge.add_subscriber(self.device_id, self._on_state_update)

    def _on_state_update(self, _=None) -> None:
        device = self._bridge.get_device_by_id(self.device_id)
        self._attr_is_on = device.get("current_state", 0) > 0
        self.async_write_ha_state()

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._bridge.turn_on(self.device_id)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._bridge.turn_off(self.device_id)
