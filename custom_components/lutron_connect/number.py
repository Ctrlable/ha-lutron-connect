"""Ketra Vibrancy (0-100%) for SpectrumTune zones on the Lutron Connect Bridge.

One slider per Ketra fixture, on the same device as its light. The value is read
from the bridge's zone status and stays live through keypad / Lutron app changes.
Setting it keeps brightness and colour (see ConnectSmartbridge.set_vibrancy); while
the light is off the value is held and applied on the next turn-on.
"""
from __future__ import annotations

from typing import Any

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import LutronConnectData, LutronConnectDevice
from .const import DOMAIN


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    data: LutronConnectData = hass.data[DOMAIN][config_entry.entry_id]
    bridge = data.bridge
    await bridge.async_read_vibrancy()
    async_add_entities(
        LutronConnectVibrancy(device, data)
        for device in bridge.get_devices().values()
        if device.get("type") == "SpectrumTune" and device.get("zone")
    )


class LutronConnectVibrancy(LutronConnectDevice, NumberEntity):
    """Vibrancy slider for one Ketra zone."""

    _attr_native_min_value = 0
    _attr_native_max_value = 100
    _attr_native_step = 1
    _attr_native_unit_of_measurement = "%"
    _attr_mode = NumberMode.SLIDER
    _attr_icon = "mdi:palette-outline"

    def __init__(self, device: dict[str, Any], data: LutronConnectData) -> None:
        super().__init__(device, data)
        if getattr(self, "_attr_name", None):
            self._attr_name = f"{self._attr_name} Vibrancy"

    @property
    def unique_id(self) -> str:
        return f"{super().unique_id}_vibrancy"

    async def async_added_to_hass(self) -> None:
        # NOT add_subscriber(): it holds one callback per device and the light owns it.
        self._bridge.add_vibrancy_subscriber(self.device_id, self.async_write_ha_state)

    @property
    def available(self) -> bool:
        return self._bridge.is_connected()

    @property
    def native_value(self) -> int | None:
        return self._bridge.get_device_by_id(self.device_id).get("vibrancy")

    async def async_set_native_value(self, value: float) -> None:
        await self._bridge.set_vibrancy(self.device_id, round(value))
