"""Support for Lutron Connect Bridge fans."""

from __future__ import annotations

import logging
from typing import Any

from pylutron_caseta import FAN_HIGH, FAN_LOW, FAN_MEDIUM, FAN_MEDIUM_HIGH, FAN_OFF

from homeassistant.components.fan import FanEntity, FanEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util.percentage import (
    ordered_list_item_to_percentage,
    percentage_to_ordered_list_item,
)

from . import DOMAIN, LutronConnectData, LutronConnectDevice

_LOGGER = logging.getLogger(__name__)

ORDERED_SPEEDS = [FAN_LOW, FAN_MEDIUM, FAN_MEDIUM_HIGH, FAN_HIGH]
FAN_DEVICE_TYPES = {"FanSpeed", "CeilingFan"}


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    data: LutronConnectData = hass.data[DOMAIN][config_entry.entry_id]
    entities = [
        LutronConnectFan(device, data)
        for device in data.bridge.get_devices().values()
        if device.get("type") in FAN_DEVICE_TYPES
    ]
    async_add_entities(entities, True)


class LutronConnectFan(LutronConnectDevice, FanEntity):
    """Lutron fan zone."""

    _attr_supported_features = FanEntityFeature.SET_SPEED
    _attr_speed_count = len(ORDERED_SPEEDS)

    async def async_added_to_hass(self) -> None:
        self._bridge.add_subscriber(self.device_id, self._on_state_update)

    def _on_state_update(self, _=None) -> None:
        device = self._bridge.get_device_by_id(self.device_id)
        fan_speed = device.get("fan_speed")
        if fan_speed is None or fan_speed == FAN_OFF:
            self._attr_is_on = False
            self._attr_percentage = 0
        else:
            self._attr_is_on = True
            self._attr_percentage = ordered_list_item_to_percentage(ORDERED_SPEEDS, fan_speed)
        self.async_write_ha_state()

    async def async_turn_on(self, percentage: int | None = None, **kwargs: Any) -> None:
        if percentage is None or percentage == 0:
            percentage = 100
        named_speed = percentage_to_ordered_list_item(ORDERED_SPEEDS, percentage)
        await self._bridge.set_fan(self.device_id, named_speed)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._bridge.set_fan(self.device_id, FAN_OFF)

    async def async_set_percentage(self, percentage: int) -> None:
        if percentage == 0:
            await self.async_turn_off()
        else:
            named_speed = percentage_to_ordered_list_item(ORDERED_SPEEDS, percentage)
            await self._bridge.set_fan(self.device_id, named_speed)

    async def async_update(self) -> None:
        self._on_state_update()
