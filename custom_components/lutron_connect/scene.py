"""Support for Lutron Connect Bridge scenes."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.scene import Scene
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import DOMAIN, LutronConnectData
from .const import CONFIG_URL, MANUFACTURER

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    data: LutronConnectData = hass.data[DOMAIN][config_entry.entry_id]
    bridge = data.bridge

    entities = [
        LutronConnectScene(scene_id, scene, data)
        for scene_id, scene in bridge.get_scenes().items()
    ]
    async_add_entities(entities, True)


class LutronConnectScene(Scene):
    """A Lutron virtual button / scene."""

    _attr_should_poll = False

    def __init__(self, scene_id: str, scene: dict[str, Any], data: LutronConnectData) -> None:
        self._scene_id = scene_id
        self._scene = scene
        self._bridge = data.bridge
        self._bridge_device = data.bridge_device
        self._attr_name = scene.get("name", f"Scene {scene_id}")
        self._attr_unique_id = f"scene_{scene_id}"

    async def async_activate(self, **kwargs: Any) -> None:
        await self._bridge.activate_scene(self._scene_id)
