"""Support for Lutron Connect Bridge shades/covers."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.cover import (
    ATTR_POSITION,
    CoverDeviceClass,
    CoverEntity,
    CoverEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import DOMAIN, LutronConnectData, LutronConnectDevice

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    data: LutronConnectData = hass.data[DOMAIN][config_entry.entry_id]
    entities = [
        LutronConnectCover(device, data)
        for device in data.bridge.get_devices_by_domain("cover")
    ]
    async_add_entities(entities, True)


class LutronConnectCover(LutronConnectDevice, CoverEntity):
    """Lutron shade as a HA cover."""

    _attr_device_class = CoverDeviceClass.SHADE
    _attr_supported_features = (
        CoverEntityFeature.OPEN
        | CoverEntityFeature.CLOSE
        | CoverEntityFeature.SET_POSITION
    )
    _attr_current_cover_position: int | None = None

    async def async_added_to_hass(self) -> None:
        self._bridge.add_subscriber(self.device_id, self._on_level_update)

    def _on_level_update(self, _=None) -> None:
        device = self._bridge.get_device_by_id(self.device_id)
        level = device.get("current_state", -1)
        if level < 0:
            return
        self._attr_current_cover_position = int(level)
        self.async_write_ha_state()

    @property
    def is_closed(self) -> bool | None:
        if self._attr_current_cover_position is None:
            return None
        return self._attr_current_cover_position == 0

    async def async_open_cover(self, **kwargs: Any) -> None:
        await self._bridge.raise_cover(self.device_id)

    async def async_close_cover(self, **kwargs: Any) -> None:
        await self._bridge.lower_cover(self.device_id)

    async def async_set_cover_position(self, **kwargs: Any) -> None:
        await self._bridge.set_value(self.device_id, int(kwargs[ATTR_POSITION]))

    async def async_update(self) -> None:
        self._on_level_update()
