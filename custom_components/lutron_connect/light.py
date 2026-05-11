"""Support for Lutron Connect Bridge lights."""

from __future__ import annotations

from datetime import timedelta
import logging
from typing import Any

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_COLOR_TEMP_KELVIN,
    ATTR_HS_COLOR,
    ATTR_TRANSITION,
    ColorMode,
    LightEntity,
    LightEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import DOMAIN, LutronConnectData, LutronConnectDevice

_LOGGER = logging.getLogger(__name__)

# Switched zones in HomeworksQS are non-dimmable lighting circuits; expose them as lights.
_DIMMABLE_TYPES = frozenset({
    "WallDimmer", "PlugInDimmer", "InLineDimmer", "SunnataDimmer",
    "TempInWallPaddleDimmer", "WallDimmerWithPreset", "Dimmed",
    "DivaSmartDimmer", "PowPak0-10V",
})
_COLOR_TUNING_TYPES = frozenset({"SpectrumTune", "WhiteTune", "ColorTune"})
_SWITCHED_LIGHT_TYPES = frozenset({"Switched"})


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    data: LutronConnectData = hass.data[DOMAIN][config_entry.entry_id]
    bridge = data.bridge

    entities = []
    for device in bridge.get_devices().values():
        device_type = device.get("type", "")
        if device_type in _COLOR_TUNING_TYPES:
            entities.append(LutronConnectColorLight(device, data))
        elif device_type in _DIMMABLE_TYPES:
            entities.append(LutronConnectLight(device, data, dimmable=True))
        elif device_type in _SWITCHED_LIGHT_TYPES:
            entities.append(LutronConnectLight(device, data, dimmable=False))

    async_add_entities(entities, True)


def _to_lutron(level: int) -> float:
    return float(level * 100 / 255)


def _to_hass(level: float) -> int:
    return int(level * 255 / 100)


class LutronConnectLight(LutronConnectDevice, LightEntity):
    """A dimmable or switched Lutron zone as a light entity."""

    _attr_is_on: bool | None = None
    _prev_brightness: int | None = None

    def __init__(self, device: dict[str, Any], data: LutronConnectData, *, dimmable: bool) -> None:
        super().__init__(device, data)
        self._dimmable = dimmable
        if dimmable:
            self._attr_color_mode = ColorMode.BRIGHTNESS
            self._attr_supported_color_modes = {ColorMode.BRIGHTNESS}
            self._attr_supported_features = LightEntityFeature.TRANSITION
        else:
            self._attr_color_mode = ColorMode.ONOFF
            self._attr_supported_color_modes = {ColorMode.ONOFF}

    async def async_added_to_hass(self) -> None:
        self._bridge.add_subscriber(self.device_id, self._on_level_update)

    def _on_level_update(self, _=None) -> None:
        device = self._bridge.get_device_by_id(self.device_id)
        level = device.get("current_state", -1)
        if level < 0:
            return
        self._attr_is_on = level > 0
        hass_level = _to_hass(level)
        self._attr_brightness = hass_level
        if self._prev_brightness is None or hass_level != 0:
            self._prev_brightness = hass_level
        if self.entity_id:
            self.async_write_ha_state()

    async def async_turn_on(self, **kwargs: Any) -> None:
        if ATTR_BRIGHTNESS in kwargs and self._dimmable:
            brightness = kwargs[ATTR_BRIGHTNESS]
        elif not self._prev_brightness:
            brightness = 255
        else:
            brightness = self._prev_brightness
        self._prev_brightness = brightness
        level = int(_to_lutron(brightness))
        fade_secs = kwargs.get(ATTR_TRANSITION)
        fade = timedelta(seconds=fade_secs) if fade_secs is not None else None
        await self._bridge.set_value(self.device_id, level, fade_time=fade)

    async def async_turn_off(self, **kwargs: Any) -> None:
        fade_secs = kwargs.get(ATTR_TRANSITION)
        fade = timedelta(seconds=fade_secs) if fade_secs is not None else None
        await self._bridge.set_value(self.device_id, 0, fade_time=fade)

    async def async_update(self) -> None:
        self._on_level_update()


class LutronConnectColorLight(LutronConnectLight):
    """A Ketra/SpectrumTune zone with color temperature (CCT) support."""

    def __init__(self, device: dict[str, Any], data: LutronConnectData) -> None:
        super().__init__(device, data, dimmable=True)
        white_range = (device.get("white_tuning_range") or {})
        self._attr_min_color_temp_kelvin = int(white_range.get("MinColorTemp", 1400))
        self._attr_max_color_temp_kelvin = int(white_range.get("MaxColorTemp", 6500))
        self._attr_color_mode = ColorMode.COLOR_TEMP
        self._attr_supported_color_modes = {ColorMode.COLOR_TEMP, ColorMode.HS}

    def _on_level_update(self, _=None) -> None:
        device = self._bridge.get_device_by_id(self.device_id)
        level = device.get("current_state", -1)
        if level < 0:
            return
        self._attr_is_on = level > 0
        hass_level = _to_hass(level)
        self._attr_brightness = hass_level
        if self._prev_brightness is None or hass_level != 0:
            self._prev_brightness = hass_level
        color_mode = device.get("current_color_mode", "color_temp")
        if color_mode == "hs":
            hs = device.get("current_hs_color")
            if hs:
                self._attr_hs_color = hs
            self._attr_color_mode = ColorMode.HS
        else:
            ct_k = device.get("current_color_temp")
            if ct_k and ct_k > 0:
                self._attr_color_temp_kelvin = int(ct_k)
            self._attr_color_mode = ColorMode.COLOR_TEMP
        if self.entity_id:
            self.async_write_ha_state()

    async def async_turn_on(self, **kwargs: Any) -> None:
        brightness = kwargs.get(ATTR_BRIGHTNESS, self._prev_brightness or 255)
        self._prev_brightness = brightness
        level = int(_to_lutron(brightness))
        fade_secs = kwargs.get(ATTR_TRANSITION)
        fade = timedelta(seconds=fade_secs) if fade_secs is not None else None

        color_value = None
        if ATTR_HS_COLOR in kwargs:
            color_value = {"hs": kwargs[ATTR_HS_COLOR]}
        elif ATTR_COLOR_TEMP_KELVIN in kwargs:
            color_value = {"color_temp": int(kwargs[ATTR_COLOR_TEMP_KELVIN])}

        await self._bridge.set_value(self.device_id, level, fade_time=fade, color_value=color_value)
