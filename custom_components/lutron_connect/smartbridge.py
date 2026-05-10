"""Subclass of pylutron_caseta Smartbridge that handles the Connect Bridge ProductType.

The Connect Bridge reports ProductType "Lutron Connect Bridge Project", which is
neither "Lutron RadioRA 3 Project" nor "Lutron HWQS Project", so pylutron_caseta
falls into the Caseta branch and crashes on the missing DeviceType key.

The Connect Bridge also does not support per-area LEAP sub-resources
(/area/{id}/associatedzone, /area/{id}/associatedcontrolstation), returning 405
for each.

This subclass implements a flat loading strategy:
  - /zone          → all zone definitions (replaces per-area /associatedzone)
  - /device?...    → all keypad devices   (replaces per-area /associatedcontrolstation)
  - /zone/status   → zone state subscription (same as RA3, confirmed working)

Correct command formats confirmed on Connect Bridge firmware 08.01.09f000:
  - Dimmed zones:   GoToDimmedLevel  + DimmedLevelParameters:  {"Level": 0-100}
  - Switched zones: GoToSwitchedLevel + SwitchedLevelParameters: {"SwitchedLevel": "On"/"Off"}
  - Shade zones:    GoToShadeLevel   + ShadeLevelParameters:   {"Level": 0-100}
  - Raise/Lower/Stop work on all shade zones
  - GoToLevel returns 405 on all zone types (not supported)
"""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import Optional

from pylutron_caseta import BridgeResponseError, _LEAP_DEVICE_TYPES, BUTTON_STATUS_RELEASED
from pylutron_caseta.leap import id_from_href
from pylutron_caseta.smartbridge import Smartbridge

_LOGGER = logging.getLogger(__name__)

_SENSOR_TYPES = set(_LEAP_DEVICE_TYPES.get("sensor", []))


def _leap_duration(td: timedelta) -> str:
    total = int(td.total_seconds())
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


class ConnectSmartbridge(Smartbridge):
    """Smartbridge variant for the Lutron Connect Bridge (HomeworksQS)."""

    async def _login(self):
        """Connect and initialise the bridge using flat LEAP endpoints."""
        try:
            _LOGGER.debug("ConnectSmartbridge: loading areas")
            await self._load_areas()

            _LOGGER.debug("ConnectSmartbridge: loading bridge device")
            await self._load_connect_bridge_device()

            _LOGGER.debug("ConnectSmartbridge: loading zones via /zone")
            await self._load_connect_zones()

            _LOGGER.debug("ConnectSmartbridge: loading keypads via /device")
            await self._load_connect_keypads()

            _LOGGER.debug("ConnectSmartbridge: subscribing to zone status")
            await self._subscribe_to_multi_zone_status()

            _LOGGER.debug("ConnectSmartbridge: subscribing to button status")
            await self._subscribe_to_button_status()

            _LOGGER.debug("ConnectSmartbridge: loading occupancy groups")
            await self._load_ra3_occupancy_groups()

            _LOGGER.debug("ConnectSmartbridge: subscribing to occupancy groups")
            await self._subscribe_to_ra3_occupancy_groups()

            _LOGGER.debug("ConnectSmartbridge: login complete")

            if not self._login_completed.done():
                self._login_completed.set_result(None)
        except asyncio.CancelledError:
            pass
        except Exception as ex:
            if not self._login_completed.done():
                self._login_completed.set_exception(ex)
            raise

    async def set_value(
        self,
        device_id: str,
        value: Optional[int] = None,
        fade_time: Optional[timedelta] = None,
        color_value=None,
    ) -> None:
        """Send the correct LEAP command for the Connect Bridge zone type."""
        device = self.devices.get(device_id)
        if device is None:
            return

        zone_type = device.get("type", "")
        zone_id = device.get("zone")

        if not zone_id or zone_type not in ("Dimmed", "Switched", "Shade"):
            return await super().set_value(device_id, value, fade_time, color_value)

        if zone_type == "Dimmed":
            params: dict = {"Level": value if value is not None else 0}
            if fade_time is not None:
                params["FadeTime"] = _leap_duration(fade_time)
            await self._request(
                "CreateRequest",
                f"/zone/{zone_id}/commandprocessor",
                {"Command": {"CommandType": "GoToDimmedLevel", "DimmedLevelParameters": params}},
            )

        elif zone_type == "Switched":
            await self._request(
                "CreateRequest",
                f"/zone/{zone_id}/commandprocessor",
                {
                    "Command": {
                        "CommandType": "GoToSwitchedLevel",
                        "SwitchedLevelParameters": {
                            "SwitchedLevel": "On" if value and value > 0 else "Off"
                        },
                    }
                },
            )

        elif zone_type == "Shade":
            level = value if value is not None else 0
            await self._request(
                "CreateRequest",
                f"/zone/{zone_id}/commandprocessor",
                {"Command": {"CommandType": "GoToShadeLevel", "ShadeLevelParameters": {"Level": level}}},
            )
            self.devices[device_id]["current_state"] = level
            if device_id in self._subscribers:
                self._subscribers[device_id]()

    async def raise_cover(self, device_id: str) -> None:
        """Raise a shade and notify subscribers of the optimistic state."""
        await super().raise_cover(device_id)
        if device_id in self._subscribers:
            self._subscribers[device_id]()

    async def lower_cover(self, device_id: str) -> None:
        """Lower a shade and notify subscribers of the optimistic state."""
        await super().lower_cover(device_id)
        if device_id in self._subscribers:
            self._subscribers[device_id]()

    def _handle_zone_status(self, status: dict) -> None:
        """Translate SwitchedLevel into a numeric Level before delegating."""
        if "SwitchedLevel" in status and "Level" not in status:
            status = {**status, "Level": 100 if status["SwitchedLevel"] == "On" else 0}
        super()._handle_zone_status(status)

    async def _load_connect_bridge_device(self):
        """Load the bridge itself as devices['1'] without requiring AssociatedArea."""
        try:
            resp = await self._request("ReadRequest", "/device?where=IsThisDevice:true")
        except BridgeResponseError as exc:
            _LOGGER.warning("Could not load Connect Bridge device info: %s", exc)
            resp = None

        if resp is None or resp.Body is None:
            _LOGGER.warning("Connect Bridge device info unavailable; using placeholder")
            self.devices.setdefault(
                "1",
                {
                    "device_id": "1",
                    "current_state": -1,
                    "fan_speed": None,
                    "zone": "1",
                    "name": "Connect Bridge",
                    "button_groups": None,
                    "type": "ConnectBridge",
                    "model": "CONNECT-BDG2",
                    "serial": None,
                    "area": None,
                    "device_name": "Connect Bridge",
                },
            )
            return

        device = resp.Body["Devices"][0]
        self.devices.setdefault(
            "1",
            {"device_id": "1", "current_state": -1, "fan_speed": None},
        ).update(
            zone="1",
            name=device["Name"],
            button_groups=None,
            type=device["DeviceType"],
            model=device.get("ModelNumber", ""),
            serial=device.get("SerialNumber"),
            area=None,
            device_name=device["Name"],
        )
        _LOGGER.debug(
            "Loaded Connect Bridge device: %s (serial %s)",
            device["Name"],
            device.get("SerialNumber"),
        )

    async def _load_connect_zones(self):
        """Load all zones from the flat /zone endpoint."""
        try:
            resp = await self._request("ReadRequest", "/zone")
        except BridgeResponseError as exc:
            _LOGGER.warning("Could not load zones from /zone: %s", exc)
            return

        if resp.Body is None:
            _LOGGER.warning("Empty response from /zone")
            return

        zones = resp.Body.get("Zones", [])
        _LOGGER.debug("ConnectSmartbridge: found %d zones", len(zones))

        for zone in zones:
            zone_id = id_from_href(zone["href"])
            zone_name = zone.get("Name", f"Zone {zone_id}")
            zone_type = zone.get("ControlType", "Dimmed")
            level = zone.get("Level", -1)
            fan_speed = zone.get("FanSpeed", None)

            area_id = None
            if "AssociatedArea" in zone:
                area_id = id_from_href(zone["AssociatedArea"]["href"])

            area_name = self.areas.get(area_id, {}).get("name", "") if area_id else ""

            color_tuning = zone.get("ColorTuningProperties")
            white_tuning_range = color_tuning.get("WhiteTuningLevelRange") if color_tuning else None

            self.devices.setdefault(
                zone_id,
                {"device_id": zone_id, "current_state": level, "fan_speed": fan_speed},
            ).update(
                zone=zone_id,
                name=f"{area_name}_{zone_name}" if area_name else zone_name,
                button_groups=None,
                type=zone_type,
                model=None,
                serial=None,
                area=area_id,
                device_name=zone_name,
                white_tuning_range=white_tuning_range,
            )

        _LOGGER.debug("ConnectSmartbridge: loaded %d zone devices", len(zones))

    async def _load_connect_keypads(self):
        """Load all keypad/control-station devices from the flat /device endpoint."""
        try:
            resp = await self._request("ReadRequest", "/device?where=IsThisDevice:false")
        except BridgeResponseError as exc:
            _LOGGER.warning("Could not load devices from /device: %s", exc)
            return

        if resp.Body is None:
            return

        devices = resp.Body.get("Devices", [])
        _LOGGER.debug("ConnectSmartbridge: found %d non-bridge devices", len(devices))

        for device in devices:
            device_type = device.get("DeviceType", "")
            has_button_groups = "ButtonGroups" in device

            # Only process devices that have buttons
            if device_type not in _SENSOR_TYPES and not has_button_groups:
                continue

            device_id = id_from_href(device["href"])

            try:
                bg_resp = await self._request(
                    "ReadRequest", f"/device/{device_id}/buttongroup/expanded"
                )
            except BridgeResponseError as exc:
                _LOGGER.debug("No button groups for device %s: %s", device_id, exc)
                continue

            if bg_resp.Body is None:
                continue

            button_groups_expanded = bg_resp.Body.get("ButtonGroupsExpanded", [])
            if not button_groups_expanded:
                continue

            device_name = device.get("Name", f"Device {device_id}")
            device_model = device.get("ModelNumber", "")
            device_serial = device.get("SerialNumber", None)

            area_id = None
            if "AssociatedArea" in device:
                area_id = id_from_href(device["AssociatedArea"]["href"])

            area_name = self.areas.get(area_id, {}).get("name", "") if area_id else ""

            device_type_friendly = device_type
            if "Pico" in device_type:
                device_type_friendly = "Pico"
            elif "Keypad" in device_type:
                device_type_friendly = "Keypad"

            button_group_ids = [
                id_from_href(group["href"])
                for group in button_groups_expanded
            ]

            combined_name = (
                f"{area_name}_{device_name} {device_type_friendly}"
                if area_name
                else f"{device_name} {device_type_friendly}"
            )

            self.devices.setdefault(
                device_id,
                {"device_id": device_id, "current_state": -1, "fan_speed": None},
            ).update(
                zone=None,
                name=combined_name,
                button_groups=button_group_ids,
                type=device_type,
                model=device_model,
                serial=device_serial,
                device_name=device_name,
                area=area_id,
            )

            _LOGGER.debug(
                "Loaded keypad: %s (%s) in area %s with %d button group(s)",
                device_name,
                device_type,
                area_name or "unassigned",
                len(button_group_ids),
            )

            for bg_expanded in button_groups_expanded:
                for button_json in bg_expanded.get("Buttons", []):
                    await self._load_ra3_button(button_json, self.devices[device_id])

        _LOGGER.debug(
            "ConnectSmartbridge: loaded %d buttons total", len(self.buttons)
        )

    async def _load_ra3_occupancy_groups(self):
        """Override to guard against missing DeviceType on Connect Bridge devices."""
        from pylutron_caseta import RA3_OCCUPANCY_SENSOR_DEVICE_TYPES

        try:
            resp = await self._request("ReadRequest", "/device?where=IsThisDevice:false")
        except BridgeResponseError as exc:
            _LOGGER.warning("Could not load occupancy devices: %s", exc)
            return

        if resp.Body is None:
            return

        for device in resp.Body.get("Devices", []):
            device_type = device.get("DeviceType")
            if device_type and device_type in RA3_OCCUPANCY_SENSOR_DEVICE_TYPES:
                self._process_ra3_occupancy_group(device)
