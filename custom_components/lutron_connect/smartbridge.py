"""Subclass of pylutron_caseta Smartbridge that handles the Connect Bridge ProductType.

The Connect Bridge reports ProductType "Lutron Connect Bridge Project", which is
neither "Lutron RadioRA 3 Project" nor "Lutron HWQS Project", so pylutron_caseta
falls into the Caseta branch and crashes on the missing DeviceType key.

This subclass routes the Connect Bridge to the RA3 loading path (zones + areas),
which matches the bridge's actual LEAP schema, and replaces _load_ra3_processor
because the Connect Bridge device has no AssociatedArea field.
"""

from __future__ import annotations

import logging

from pylutron_caseta.smartbridge import Smartbridge

_LOGGER = logging.getLogger(__name__)


class ConnectSmartbridge(Smartbridge):
    """Smartbridge variant for the Lutron Connect Bridge (HomeworksQS)."""

    async def _login(self):
        """Connect and initialise the bridge using the RA3 device loading path."""
        _LOGGER.debug("ConnectSmartbridge: loading areas")
        await self._load_areas()

        # Populate devices["1"] for the bridge itself.  We can't call the stock
        # _load_ra3_processor() because that method requires processor["AssociatedArea"]
        # which the Connect Bridge device does not include.
        _LOGGER.debug("ConnectSmartbridge: loading bridge device")
        await self._load_connect_bridge_device()

        # Zones and control stations use the same RA3-style LEAP endpoints.
        _LOGGER.debug("ConnectSmartbridge: loading RA3 devices (zones + keypads)")
        await self._load_ra3_devices()

        _LOGGER.debug("ConnectSmartbridge: subscribing to button status")
        await self._subscribe_to_button_status()

        # Occupancy groups — the Connect Bridge may return an empty list; both
        # methods handle that gracefully.
        _LOGGER.debug("ConnectSmartbridge: loading occupancy groups")
        await self._load_ra3_occupancy_groups()

        _LOGGER.debug("ConnectSmartbridge: subscribing to occupancy groups")
        await self._subscribe_to_ra3_occupancy_groups()

        _LOGGER.debug("ConnectSmartbridge: login complete")

    async def _load_connect_bridge_device(self):
        """Load the bridge itself as devices['1'] without requiring AssociatedArea."""
        resp = await self._request("ReadRequest", "/device?where=IsThisDevice:true")
        if resp.Body is None:
            _LOGGER.warning("Could not load Connect Bridge device info")
            # Provide a minimal placeholder so devices['1'] always exists.
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
