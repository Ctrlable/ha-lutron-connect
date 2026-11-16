"""Find automations still listening for the button event name used before v1.2.0.

Up to v1.1.x Lutron Connect fired core lutron_caseta's event name. It now fires
``lutron_connect_button_event``. Automations written against the old name stop
firing for Connect keypads, so at setup we look for them once and raise a
Repairs notice naming them. We never rewrite an automation.

Where the automations come from: the automation component's own loaded
entities (``hass.data[DATA_COMPONENT].entities``) and each one's
``raw_config``. That covers automations.yaml, packages, ``!include_dir_*``
splits and blueprint automations (raw_config is the blueprint with its inputs
substituted), which reading automations.yaml alone would miss. It needs the
automation component loaded, so the scan runs once the site has started.
"""

from __future__ import annotations

from typing import Any

from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import issue_registry as ir

from .const import DOMAIN, LEGACY_CASETA_BUTTON_EVENT

LEGACY_EVENT_ISSUE = "legacy_caseta_button_event"
_CASETA_DOMAIN = "lutron_caseta"
_TRIGGER_KEYS = ("triggers", "trigger")


def _mentions_legacy_event(node: Any) -> bool:
    """True if a trigger tree listens for the legacy event name.

    Walks nested lists/dicts so it catches ``event_type`` given as a string or
    a list, and triggers nested inside a ``triggers:`` list of a trigger.
    """
    if isinstance(node, dict):
        event_type = node.get("event_type")
        if event_type == LEGACY_CASETA_BUTTON_EVENT or (
            isinstance(event_type, list) and LEGACY_CASETA_BUTTON_EVENT in event_type
        ):
            return True
        return any(_mentions_legacy_event(v) for v in node.values())
    if isinstance(node, list):
        return any(_mentions_legacy_event(v) for v in node)
    return False


@callback
def async_find_legacy_automations(hass: HomeAssistant) -> list[tuple[str, str]]:
    """Return (entity_id, name) of automations triggered by the legacy event."""
    from homeassistant.components.automation.const import DATA_COMPONENT

    component = hass.data.get(DATA_COMPONENT)
    if component is None:
        return []
    found: list[tuple[str, str]] = []
    for entity in component.entities:
        raw = getattr(entity, "raw_config", None) or {}
        triggers = [raw[k] for k in _TRIGGER_KEYS if k in raw]
        if _mentions_legacy_event(triggers):
            state = hass.states.get(entity.entity_id)
            name = (
                (state and state.attributes.get("friendly_name"))
                or raw.get("alias")
                or entity.entity_id
            )
            found.append((entity.entity_id, str(name)))
    return sorted(found)


@callback
def async_check_legacy_automations(hass: HomeAssistant) -> None:
    """Raise or clear the Repairs notice for automations on the old event."""
    found = async_find_legacy_automations(hass)
    # A site with its own Caseta / RA2 Select bridge fires the old name for
    # real, so automations on it are not ours to flag.
    if not found or hass.config_entries.async_entries(_CASETA_DOMAIN):
        ir.async_delete_issue(hass, DOMAIN, LEGACY_EVENT_ISSUE)
        return
    ir.async_create_issue(
        hass,
        DOMAIN,
        LEGACY_EVENT_ISSUE,
        is_fixable=False,
        is_persistent=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key=LEGACY_EVENT_ISSUE,
        translation_placeholders={
            "automations": "\n".join(f"- {name} (`{eid}`)" for eid, name in found),
            "old_event": LEGACY_CASETA_BUTTON_EVENT,
            "new_event": "lutron_connect_button_event",
        },
    )


@callback
def async_check_legacy_automations_later(hass: HomeAssistant) -> None:
    """Run the check once the site is up and its automations are loaded."""
    if hass.is_running:
        async_check_legacy_automations(hass)
        return

    @callback
    def _on_started(_event) -> None:
        async_check_legacy_automations(hass)

    hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, _on_started)
