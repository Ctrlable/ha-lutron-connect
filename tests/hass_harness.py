"""A real (minimal) Ctrlable Pro core for tests: bus, registries, config entries.

No pytest plugin needed — boots homeassistant the way bootstrap does, in a
throwaway config dir.
"""
import contextlib, tempfile
from types import MappingProxyType

from homeassistant import bootstrap, config_entries, loader
from homeassistant.core import HomeAssistant


@contextlib.asynccontextmanager
async def running_hass():
    with tempfile.TemporaryDirectory() as config_dir:
        hass = HomeAssistant(config_dir)
        hass.config.skip_pip = True
        loader.async_setup(hass)
        hass.config_entries = config_entries.ConfigEntries(hass, {})
        await bootstrap.async_load_base_functionality(hass)
        try:
            yield hass
        finally:
            await hass.async_stop(force=True)


def add_entry(hass, domain, title="test"):
    """Register a config entry without setting the integration up."""
    entry = config_entries.ConfigEntry(
        data={}, discovery_keys=MappingProxyType({}), domain=domain, minor_version=1,
        options={}, source="user", subentries_data=None, title=title,
        unique_id=None, version=1,
    )
    hass.config_entries._entries[entry.entry_id] = entry
    return entry
