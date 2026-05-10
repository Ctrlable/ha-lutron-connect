"""Describe lutron_connect events in the HA logbook / device activity log."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from homeassistant.core import Event, HomeAssistant, callback

from .const import (
    ATTR_ACTION,
    ATTR_AREA_NAME,
    ATTR_DEVICE_NAME,
    ATTR_LEAP_BUTTON_NUMBER,
    DOMAIN,
    LUTRON_CASETA_BUTTON_EVENT,
)


@callback
def async_describe_events(
    hass: HomeAssistant,
    async_describe_event: Callable[[str, str, Callable[[Event], dict[str, Any]]], None],
) -> None:
    """Register a description for lutron_caseta_button_event in the logbook."""

    @callback
    def _describe(event: Event) -> dict[str, Any]:
        data = event.data
        area = data.get(ATTR_AREA_NAME, "Unknown area")
        device = data.get(ATTR_DEVICE_NAME, "keypad")
        button = data.get(ATTR_LEAP_BUTTON_NUMBER, "?")
        action = data.get(ATTR_ACTION, "?")
        return {
            "name": f"{area} {device}",
            "message": f"button {button} {action}",
        }

    async_describe_event(DOMAIN, LUTRON_CASETA_BUTTON_EVENT, _describe)
