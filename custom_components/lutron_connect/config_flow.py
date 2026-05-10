"""Config flow for Lutron Connect Bridge."""

from __future__ import annotations

import logging
import os
import ssl

import voluptuous as vol

from homeassistant.components import zeroconf
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_HOST, CONF_NAME

from .const import (
    CONF_CA_CERTS,
    CONF_CERTFILE,
    CONF_KEYFILE,
    DOMAIN,
)
from .pairing import PAIR_CA, PAIR_CERT, PAIR_KEY, async_pair

_LOGGER = logging.getLogger(__name__)

TLS_ASSET_TEMPLATE = "lutron_connect-{}-{}.pem"
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

        # Check once (without a network call) whether valid certs already exist.
        if not self._tls_attempted:
            self._tls_validated = await self.hass.async_add_executor_job(
                self._tls_assets_valid
            )
            self._tls_attempted = True

        if user_input is not None:
            # Certs are already good — skip re-pairing.
            if self._tls_validated:
                return self.async_create_entry(title=self._bridge_id, data=self.data)

            try:
                assets = await async_pair(self.data[CONF_HOST])
            except (TimeoutError, OSError, RuntimeError) as exc:
                _LOGGER.error("Pairing failed for %s: %s", self.data[CONF_HOST], exc)
                errors["base"] = "cannot_connect"
            else:
                # async_pair already verified connectivity via ping on port 8090.
                # Write certs and create the entry; async_setup_entry will connect
                # and set the unique_id from the bridge serial.
                await self.hass.async_add_executor_job(self._write_tls_assets, assets)
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

    def _tls_assets_valid(self) -> bool:
        """Return True if cert files exist and can be loaded as a valid SSL context."""
        for conf_key in FILE_MAPPING.values():
            if not os.path.exists(self.hass.config.path(self.data[conf_key])):
                return False
        try:
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            ctx.check_hostname = False
            ctx.load_verify_locations(self.hass.config.path(self.data[CONF_CA_CERTS]))
            ctx.load_cert_chain(
                self.hass.config.path(self.data[CONF_CERTFILE]),
                self.hass.config.path(self.data[CONF_KEYFILE]),
            )
            return True
        except ssl.SSLError:
            return False

    def _write_tls_assets(self, assets: dict) -> None:
        for asset_key, conf_key in FILE_MAPPING.items():
            with open(
                self.hass.config.path(self.data[conf_key]), "w", encoding="utf8"
            ) as fh:
                fh.write(assets[asset_key])
