"""Config flow for Lutron Connect Bridge."""

from __future__ import annotations

import asyncio
import logging
import os
import ssl

import voluptuous as vol

from homeassistant.components import zeroconf
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_HOST, CONF_NAME

from .const import (
    BRIDGE_TIMEOUT,
    CONF_CA_CERTS,
    CONF_CERTFILE,
    CONF_KEYFILE,
    DOMAIN,
    LEAP_PORT,
)
from .pairing import PAIR_CA, PAIR_CERT, PAIR_KEY, async_pair

_LOGGER = logging.getLogger(__name__)

TLS_ASSET_TEMPLATE = "lutron_connect-{}-{}.pem"
ENTRY_DEFAULT_TITLE = "Connect Bridge"
DATA_SCHEMA_USER = vol.Schema({vol.Required(CONF_HOST): str})

FILE_MAPPING = {
    PAIR_KEY: CONF_KEYFILE,
    PAIR_CERT: CONF_CERTFILE,
    PAIR_CA: CONF_CA_CERTS,
}


class LutronConnectFlowHandler(ConfigFlow, domain=DOMAIN):
    """Handle a Lutron Connect Bridge config flow."""

    VERSION = 1

    def __init__(self):
        self.data: dict = {}
        self._lutron_id: str | None = None
        self._tls_validated = False
        self._tls_attempted = False

    async def async_step_user(self, user_input=None) -> ConfigFlowResult:
        """Handle a manual (user-initiated) flow."""
        if user_input is not None:
            self.data[CONF_HOST] = user_input[CONF_HOST]
            return await self.async_step_link()
        return self.async_show_form(step_id="user", data_schema=DATA_SCHEMA_USER)

    async def async_step_zeroconf(
        self, discovery_info: zeroconf.ZeroconfServiceInfo
    ) -> ConfigFlowResult:
        """Handle zeroconf-discovered bridge."""
        hostname = discovery_info.hostname or ""
        if not hostname.lower().startswith("lutron-"):
            return self.async_abort(reason="not_lutron_device")

        self._lutron_id = hostname.split("-")[1].replace(".local.", "")
        await self.async_set_unique_id(self._lutron_id)
        host = discovery_info.host
        self._abort_if_unique_id_configured({CONF_HOST: host})

        self.data[CONF_HOST] = host
        self.context["title_placeholders"] = {
            CONF_NAME: self._bridge_id,
            CONF_HOST: host,
        }
        return await self.async_step_link()

    async def async_step_link(self, user_input=None) -> ConfigFlowResult:
        """Handle pairing step — shows button-press prompt."""
        errors: dict = {}

        self._async_abort_entries_match({CONF_HOST: self.data[CONF_HOST]})
        self._configure_tls_assets()

        if not self._tls_attempted:
            if await self.hass.async_add_executor_job(
                self._tls_assets_exist
            ) and await self._async_get_bridge_id():
                self._tls_validated = True
            self._tls_attempted = True

        if user_input is not None:
            if self._tls_validated:
                return self.async_create_entry(title=self._bridge_id, data=self.data)

            try:
                assets = await async_pair(self.data[CONF_HOST])
            except (TimeoutError, OSError, RuntimeError) as exc:
                _LOGGER.error("Pairing failed: %s", exc)
                errors["base"] = "cannot_connect"
            else:
                await self.hass.async_add_executor_job(self._write_tls_assets, assets)
                if bridge_id := await self._async_get_bridge_id():
                    await self.async_set_unique_id(bridge_id, raise_on_progress=False)
                    self._abort_if_unique_id_configured()
                return self.async_create_entry(title=self._bridge_id, data=self.data)

        return self.async_show_form(
            step_id="link",
            errors=errors,
            description_placeholders={
                CONF_NAME: self._bridge_id,
                CONF_HOST: self.data[CONF_HOST],
            },
        )

    @property
    def _bridge_id(self) -> str:
        return self._lutron_id or self.data[CONF_HOST]

    def _configure_tls_assets(self) -> None:
        for asset_key, conf_key in FILE_MAPPING.items():
            self.data[conf_key] = TLS_ASSET_TEMPLATE.format(self._bridge_id, asset_key)

    def _tls_assets_exist(self) -> bool:
        return all(
            os.path.exists(self.hass.config.path(self.data[conf_key]))
            for conf_key in FILE_MAPPING.values()
        )

    def _write_tls_assets(self, assets: dict) -> None:
        for asset_key, conf_key in FILE_MAPPING.items():
            with open(
                self.hass.config.path(self.data[conf_key]), "w", encoding="utf8"
            ) as fh:
                fh.write(assets[asset_key])

    async def _async_get_bridge_id(self) -> str | None:
        """Connect to bridge and return its serial-based ID."""
        from pylutron_caseta.smartbridge import Smartbridge

        try:
            bridge = Smartbridge.create_tls(
                hostname=self.data[CONF_HOST],
                keyfile=self.hass.config.path(self.data[CONF_KEYFILE]),
                certfile=self.hass.config.path(self.data[CONF_CERTFILE]),
                ca_certs=self.hass.config.path(self.data[CONF_CA_CERTS]),
                port=LEAP_PORT,
            )
        except ssl.SSLError:
            return None

        try:
            async with asyncio.timeout(BRIDGE_TIMEOUT):
                await bridge.connect()
        except TimeoutError:
            _LOGGER.error("Timeout connecting to %s", self.data[CONF_HOST])
        else:
            if bridge.is_connected():
                devices = bridge.get_devices()
                from .const import BRIDGE_DEVICE_ID
                bridge_device = devices[BRIDGE_DEVICE_ID]
                serial = bridge_device.get("serial")
                if serial is not None:
                    return hex(int(serial))[2:].zfill(8)
        finally:
            await bridge.close()

        return None
