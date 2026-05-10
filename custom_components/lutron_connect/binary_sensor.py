"""Support for Lutron Connect Bridge occupancy sensors."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
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
    data: LutronConnectData = hass.data[DOMAIN][config_entry.entry_id]
    bridge = data.bridge
    bridge_unique_id = _serial_to_unique_id(data.bridge_device["serial"])

    entities = [
        LutronConnectOccupancySensor(group_id, group, data, bridge_unique_id)
        for group_id, group in bridge.occupancy_groups.items()
    ]
    async_add_entities(entities, True)


class LutronConnectOccupancySensor(BinarySensorEntity):
    """Lutron occupancy group as a presence binary sensor."""

    _attr_should_poll = False
    _attr_device_class = BinarySensorDeviceClass.OCCUPANCY

    def __init__(
        self,
        group_id: str,
        group: dict[str, Any],
        data: LutronConnectData,
        bridge_unique_id: str,
    ) -> None:
        self._group_id = group_id
        self._bridge = data.bridge
        sensor_id = group.get("occupancy_group_id", group_id)
        unique_id = f"occupancygroup_{bridge_unique_id}_{sensor_id}"
        self._attr_unique_id = unique_id

        area = _area_name(data.bridge.areas, group.get("area"))
        self._attr_name = f"{area} Occupancy" if area != UNASSIGNED_AREA else "Occupancy"

        info = DeviceInfo(
            identifiers={(DOMAIN, unique_id)},
            manufacturer=MANUFACTURER,
            name=self._attr_name,
            via_device=(DOMAIN, data.bridge_device["serial"]),
            configuration_url=CONFIG_URL,
        )
        if area != UNASSIGNED_AREA:
            info["suggested_area"] = area
        self._attr_device_info = info

    async def async_added_to_hass(self) -> None:
        self._bridge.add_occupancy_subscriber(self._group_id, self._on_occupancy_change)

    def _on_occupancy_change(self, group_id: str) -> None:
        group = self._bridge.occupancy_groups.get(group_id, {})
        status = group.get("status", "")
        self._attr_is_on = status == "Occupied"
        self.async_write_ha_state()
