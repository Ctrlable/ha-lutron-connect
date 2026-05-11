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

Button cluster detection:
  On HWQS systems consecutive buttons within the same physical keypad have LEAP IDs
  that differ by 2–8.  Buttons that belong to a DIFFERENT physical keypad (one that
  does not appear in /device) appear in the same numeric "gap" but are separated by
  IDs in the hundreds or thousands.  _CLUSTER_GAP_THRESHOLD (30) is well above the
  normal intra-keypad gap (≤ 4) and well below the smallest observed inter-keypad gap
  (38, confirmed on firmware 08.01.09f000).
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from datetime import timedelta
from typing import Callable, Optional

from pylutron_caseta import BridgeResponseError, BUTTON_STATUS_RELEASED
from pylutron_caseta.leap import id_from_href
from pylutron_caseta.smartbridge import Smartbridge

_LOGGER = logging.getLogger(__name__)

_CLUSTER_GAP_THRESHOLD = 30


def _leap_duration(td: timedelta) -> str:
    total = int(td.total_seconds())
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def _split_button_clusters(buttons: list) -> list[list]:
    """Group a sorted button list into clusters; a gap > _CLUSTER_GAP_THRESHOLD signals a new physical device."""
    if not buttons:
        return []
    clusters: list[list] = [[buttons[0]]]
    for b in buttons[1:]:
        prev_id = int(id_from_href(clusters[-1][-1]["href"]))
        curr_id = int(id_from_href(b["href"]))
        if curr_id - prev_id > _CLUSTER_GAP_THRESHOLD:
            clusters.append([])
        clusters[-1].append(b)
    return clusters


class ConnectSmartbridge(Smartbridge):
    """Smartbridge variant for the Lutron Connect Bridge (HomeworksQS)."""

    async def _login(self):
        """Connect and initialise the bridge using flat LEAP endpoints."""
        self.led_devices: dict[str, dict] = {}
        self._led_subscribers: dict[str, Callable] = {}

        try:
            _LOGGER.debug("ConnectSmartbridge: negotiating admin role")
            await self._negotiate_admin_role()

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

            _LOGGER.debug("ConnectSmartbridge: subscribing to LED status")
            await self._subscribe_to_connect_leds()

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

        _HANDLED = {"Dimmed", "Switched", "Shade", "SpectrumTune", "WhiteTune", "ColorTune"}
        if not zone_id or zone_type not in _HANDLED:
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

        elif zone_type in ("SpectrumTune", "WhiteTune", "ColorTune"):
            params: dict = {}
            if value is not None:
                params["Level"] = value
            if fade_time is not None:
                params["FadeTime"] = _leap_duration(fade_time)
            if color_value and "hs" in color_value:
                h, s = color_value["hs"]
                params["HSVTuningLevel"] = {"Hue": round(h), "Saturation": round(s)}
                self.devices[device_id]["current_hs_color"] = (h, s)
                self.devices[device_id]["current_color_mode"] = "hs"
                self.devices[device_id]["color_command_time"] = asyncio.get_event_loop().time()
            elif color_value and "color_temp" in color_value:
                params["ColorTemperature"] = color_value["color_temp"]
                self.devices[device_id]["current_color_temp"] = color_value["color_temp"]
                self.devices[device_id]["current_color_mode"] = "color_temp"
                self.devices[device_id]["color_command_time"] = asyncio.get_event_loop().time()
            elif color_value and "xy" in color_value:
                x, y = color_value["xy"]
                params["XYTuningLevel"] = {"X": x, "Y": y}
                self.devices[device_id]["color_command_time"] = asyncio.get_event_loop().time()
            await self._request(
                "CreateRequest",
                f"/zone/{zone_id}/commandprocessor",
                {"Command": {
                    "CommandType": "GoToSpectrumTuningLevel",
                    "SpectrumTuningLevelParameters": params,
                }},
            )
            if value is not None:
                self.devices[device_id]["current_state"] = value
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

    async def tap_button(self, button_id: str) -> None:
        """Simulate a button press on the Connect Bridge via /button/{id}/commandprocessor."""
        if button_id not in self.buttons:
            return
        try:
            await self._request(
                "CreateRequest",
                f"/button/{button_id}/commandprocessor",
                {"Command": {"CommandType": "PressAndRelease"}},
            )
        except BridgeResponseError as exc:
            _LOGGER.warning("tap_button %s failed: %s", button_id, exc)

    def _handle_zone_status(self, status: dict) -> None:
        """Translate SwitchedLevel into a numeric Level and cache color tuning state."""
        if "SwitchedLevel" in status and "Level" not in status:
            status = {**status, "Level": 100 if status["SwitchedLevel"] == "On" else 0}

        color_tuning = status.get("ColorTuningStatus")
        if color_tuning:
            zone_href = (status.get("Zone") or {}).get("href", "")
            zone_id = id_from_href(zone_href) if zone_href else None
            if zone_id and zone_id in self.devices:
                # After we send a color command, the bridge echoes back the Lutron app's
                # last-known state (the old color) as a subscription push.  Guard against
                # that echo overwriting our optimistic state for 3 seconds.
                cmd_time = self.devices[zone_id].get("color_command_time", 0)
                if asyncio.get_event_loop().time() - cmd_time >= 3.0:
                    ct_k = status.get("ColorTemperature")
                    if ct_k:
                        self.devices[zone_id]["current_color_temp"] = ct_k
                    hsv = color_tuning.get("HSVTuningLevel") or {}
                    h, s = hsv.get("Hue", 0), hsv.get("Saturation", 0)
                    if s > 0:
                        self.devices[zone_id]["current_hs_color"] = (h, s)
                        self.devices[zone_id]["current_color_mode"] = "hs"
                    xy = color_tuning.get("XYTuningLevel") or {}
                    x, y = xy.get("X", 0), xy.get("Y", 0)
                    if x > 0 and y > 0:
                        self.devices[zone_id]["current_xy"] = (x, y)

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
        """Load keypad devices and buttons for the Connect Bridge.

        The Connect Bridge does not expose DeviceType, ButtonGroups, or
        /device/{id}/buttongroup/expanded.  Instead:
          - All physical keypad devices appear in /device?where=IsThisDevice:false
          - All buttons appear in /button with ButtonNumber and no parent reference
          - Button IDs cluster numerically after their parent device ID.

        When multiple physical keypads lack individual /device entries (e.g. WPMs and
        shade-only controllers), their buttons appear in the ID range of the nearest
        listed device.  _split_button_clusters() separates them using the large ID gap
        between clusters; each extra cluster becomes a virtual device entry.
        """
        try:
            resp = await self._request("ReadRequest", "/device?where=IsThisDevice:false")
        except BridgeResponseError as exc:
            _LOGGER.warning("Could not load devices from /device: %s", exc)
            return

        if resp.Body is None:
            return

        devices_raw = resp.Body.get("Devices", [])
        _LOGGER.debug("ConnectSmartbridge: found %d non-bridge devices", len(devices_raw))

        try:
            btn_resp = await self._request("ReadRequest", "/button")
        except BridgeResponseError as exc:
            _LOGGER.warning("Could not load buttons from /button: %s", exc)
            btn_resp = None

        buttons_raw = (
            btn_resp.Body.get("Buttons", []) if (btn_resp and btn_resp.Body) else []
        )
        _LOGGER.debug("ConnectSmartbridge: found %d buttons", len(buttons_raw))

        devices_sorted = sorted(devices_raw, key=lambda d: int(id_from_href(d["href"])))
        buttons_sorted = sorted(buttons_raw, key=lambda b: int(id_from_href(b["href"])))

        # cluster_info collects (first_button_id_int, keypad_device_id, cluster_buttons)
        # for LED discovery after all keypads are loaded.
        cluster_info: list[tuple[int, str, list]] = []

        for i, device in enumerate(devices_sorted):
            device_id = id_from_href(device["href"])
            device_id_int = int(device_id)
            next_id_int = (
                int(id_from_href(devices_sorted[i + 1]["href"]))
                if i + 1 < len(devices_sorted)
                else 10 ** 9
            )

            my_buttons = [
                b for b in buttons_sorted
                if device_id_int < int(id_from_href(b["href"])) < next_id_int
            ]

            if not my_buttons:
                continue  # WPMs, phantom keypads, or devices with no buttons

            device_name = device.get("Name", f"Device {device_id}")
            device_serial = device.get("SerialNumber")

            area_id = None
            if "AssociatedArea" in device:
                area_id = id_from_href(device["AssociatedArea"]["href"])

            area_name = self.areas.get(area_id, {}).get("name", "") if area_id else ""

            clusters = _split_button_clusters(my_buttons)

            for cluster_idx, cluster_buttons in enumerate(clusters):
                if cluster_idx == 0:
                    eff_device_id = device_id
                    eff_serial = device_serial
                    eff_name = device_name
                else:
                    eff_device_id = f"{device_id}_x{cluster_idx}"
                    eff_serial = None
                    eff_name = f"{device_name} ({cluster_idx})"

                combined_name = f"{area_name}_{eff_name}" if area_name else eff_name

                self.devices.setdefault(
                    eff_device_id,
                    {"device_id": eff_device_id, "current_state": -1, "fan_speed": None},
                ).update(
                    zone=None,
                    name=combined_name,
                    button_groups=[],
                    type="CSD",
                    model=None,
                    serial=eff_serial,
                    device_name=eff_name,
                    area=area_id,
                )

                _LOGGER.debug(
                    "Loaded%s keypad: %s in area %s with %d button(s)",
                    " virtual" if cluster_idx > 0 else "",
                    eff_name,
                    area_name or "unassigned",
                    len(cluster_buttons),
                )

                for button_json in cluster_buttons:
                    await self._load_connect_button(button_json, self.devices[eff_device_id])

                first_btn_id = int(id_from_href(cluster_buttons[0]["href"]))
                cluster_info.append((first_btn_id, eff_device_id, list(cluster_buttons)))

        _LOGGER.debug(
            "ConnectSmartbridge: loaded %d buttons total", len(self.buttons)
        )

        await self._discover_connect_leds(cluster_info)

    async def _load_connect_button(self, button_json: dict, keypad_device: dict) -> None:
        """Populate self.buttons for a single Connect Bridge button."""
        button_id = id_from_href(button_json["href"])
        button_number = button_json.get("ButtonNumber", 0)
        button_name = f"Button {button_number}"

        button_led = None
        associated_led = button_json.get("AssociatedLED")
        if associated_led is not None:
            button_led = id_from_href(associated_led["href"])

        self.buttons.setdefault(
            button_id,
            {
                "device_id": button_id,
                "current_state": BUTTON_STATUS_RELEASED,
                "button_number": button_number,
                "button_group": None,
            },
        ).update(
            name=keypad_device["name"],
            type=keypad_device["type"],
            model=keypad_device["model"],
            serial=keypad_device["serial"],
            button_name=button_name,
            button_led=button_led,
            device_name=button_name,
            parent_device=keypad_device["device_id"],
        )

        if button_led is not None:
            await self._load_ra3_button_led(button_led, button_id, keypad_device)

    async def _negotiate_admin_role(self) -> None:
        """Negotiate Admin role via /clientsetting to unlock LED endpoints."""
        try:
            await self._request(
                "UpdateRequest",
                "/clientsetting",
                {"ClientSetting": {"ClientMajorVersion": 1}},
            )
            _LOGGER.debug("ConnectSmartbridge: Admin role negotiated")
        except BridgeResponseError as exc:
            _LOGGER.warning("ConnectSmartbridge: clientsetting negotiation failed: %s", exc)

    async def _discover_connect_leds(
        self, cluster_info: list[tuple[int, str, list]]
    ) -> None:
        """Probe IDs just before each cluster's first button to find LED resources.

        On Connect Bridge firmware, LED IDs are allocated as a block immediately
        before the first button ID of each physical keypad cluster.  They are only
        accessible after Admin role negotiation (clientsetting).  SET is not
        supported (405); LEDs are read-only indicators of scene/zone state.
        """
        sem = asyncio.Semaphore(20)

        async def try_led(lid: int) -> tuple[int, str] | None:
            async with sem:
                try:
                    resp = await asyncio.wait_for(
                        self._request("ReadRequest", f"/led/{lid}/status"),
                        timeout=3,
                    )
                    if resp and resp.Body:
                        state = resp.Body.get("LEDStatus", {}).get("State", "Off")
                        return (lid, state)
                except Exception:
                    pass
                return None

        # Build tasks for all clusters simultaneously, limited by semaphore.
        tasks_meta: list[tuple[int, str, list]] = []  # (led_id_int, keypad_device_id, buttons_sorted)
        for first_btn_id, keypad_device_id, cluster_buttons in cluster_info:
            buttons_sorted = sorted(
                cluster_buttons,
                key=lambda b: b.get("ButtonNumber", int(id_from_href(b["href"]))),
            )
            for offset in range(1, 9):
                tasks_meta.append((first_btn_id - offset, keypad_device_id, buttons_sorted))

        results = await asyncio.gather(*[try_led(lid) for lid, _, _ in tasks_meta])

        # Group found LEDs by keypad, preserving order (sorted by led_id).
        grouped: dict[str, list[tuple[int, str, list]]] = defaultdict(list)
        for (led_id, keypad_device_id, buttons_sorted), result in zip(tasks_meta, results):
            if result is not None:
                grouped[keypad_device_id].append((led_id, result[1], buttons_sorted))

        for keypad_device_id, entries in grouped.items():
            found = sorted(entries, key=lambda x: x[0])  # sort by led_id ascending
            buttons_sorted = found[0][2]
            for i, (led_id, state, _) in enumerate(found):
                btn_number = (
                    buttons_sorted[i].get("ButtonNumber", i + 1)
                    if i < len(buttons_sorted)
                    else i + 1
                )
                self.led_devices[str(led_id)] = {
                    "led_id": str(led_id),
                    "keypad_device_id": keypad_device_id,
                    "button_number": btn_number,
                    "state": state,
                }

        _LOGGER.debug("ConnectSmartbridge: discovered %d LED devices", len(self.led_devices))

    async def _subscribe_to_connect_leds(self) -> None:
        """Subscribe to /led/{id}/status for every discovered LED."""
        if not self.led_devices:
            _LOGGER.debug("ConnectSmartbridge: no LED devices to subscribe")
            return

        sem = asyncio.Semaphore(10)

        async def sub_one(led_id: str) -> None:
            async with sem:
                try:
                    await self._subscribe(
                        f"/led/{led_id}/status",
                        self._handle_led_status,
                    )
                except Exception as exc:
                    _LOGGER.debug("LED %s subscribe failed: %s", led_id, exc)

        await asyncio.gather(*[sub_one(lid) for lid in self.led_devices])
        _LOGGER.debug("ConnectSmartbridge: subscribed to %d LEDs", len(self.led_devices))

    def _handle_led_status(self, response) -> None:
        """Handle LEDStatus update from a subscribed /led/{id}/status endpoint."""
        if response.Body is None:
            return
        led_status = response.Body.get("LEDStatus")
        if not led_status:
            return
        led_href = (led_status.get("LED") or {}).get("href", "")
        if not led_href:
            led_href = led_status.get("href", "").replace("/status", "")
        if not led_href:
            return
        led_id = id_from_href(led_href)
        state = led_status.get("State", "Off")
        if led_id not in self.led_devices:
            return
        self.led_devices[led_id]["state"] = state
        cb = self._led_subscribers.get(led_id)
        if cb:
            try:
                cb(state)
            except Exception:
                _LOGGER.exception("Error in LED subscriber for LED %s", led_id)

    def add_led_subscriber(self, led_id: str, callback: Callable[[str], None]) -> None:
        """Register a callback invoked with the new State ('On'/'Off') on LED changes."""
        self._led_subscribers[led_id] = callback

    async def _subscribe_to_button_status(self) -> None:
        """Override parent's per-button subscription with the flat endpoint.

        The Connect Bridge does not support /button/{id}/status/event (returns 405).
        The flat /button/status/event endpoint works and sends MultipleButtonStatusEvent
        messages for every button press on any keypad.
        """
        _LOGGER.debug("ConnectSmartbridge: subscribing to /button/status/event")
        try:
            await self._subscribe(
                "/button/status/event",
                self._handle_connect_button_status,
            )
            _LOGGER.debug("ConnectSmartbridge: subscribed to /button/status/event")
        except BridgeResponseError as ex:
            _LOGGER.warning(
                "ConnectSmartbridge: failed to subscribe to button status: %s", ex
            )

    def _handle_connect_button_status(self, response) -> None:
        """Handle MultipleButtonStatusEvent from the flat button subscription."""
        if response.Body is None:
            return
        statuses = response.Body.get("ButtonStatuses") or []
        if not statuses:
            single = response.Body.get("ButtonStatus")
            if single:
                statuses = [single]
        for status in statuses:
            try:
                button_id = id_from_href(status["Button"]["href"])
                button_event = status["ButtonEvent"]["EventType"]
            except (KeyError, TypeError) as exc:
                _LOGGER.warning("Malformed button status payload %s: %s", status, exc)
                continue
            _LOGGER.debug(
                "Button event: id=%s event=%s known=%s subscribed=%s",
                button_id, button_event,
                button_id in self.buttons,
                button_id in self._button_subscribers,
            )
            if button_id in self.buttons:
                self.buttons[button_id]["current_state"] = button_event
                if button_id in self._button_subscribers:
                    try:
                        self._button_subscribers[button_id](button_event)
                    except Exception:
                        _LOGGER.exception(
                            "Error in button subscriber for button %s", button_id
                        )

    def _handle_unsolicited(self, response) -> None:
        """Extend parent to catch untagged button events from the Connect Bridge."""
        super()._handle_unsolicited(response)
        mbt = response.Header.MessageBodyType
        if mbt in ("MultipleButtonStatusEvent", "OneButtonStatusEvent"):
            self._handle_connect_button_status(response)

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
