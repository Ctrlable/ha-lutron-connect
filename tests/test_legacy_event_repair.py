"""Repairs notice for automations still triggered by lutron_caseta_button_event."""
import asyncio, json, pathlib, re, sys
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "custom_components"))
sys.path.insert(0, str(ROOT / "tests"))
from homeassistant.helpers import issue_registry as ir
from homeassistant.setup import async_setup_component
from hass_harness import add_entry, running_hass
from lutron_connect.legacy_event import (LEGACY_EVENT_ISSUE, async_check_legacy_automations,
                                         async_check_legacy_automations_later)

OLD = "lutron_caseta_button_event"
AUTOMATIONS = [
    {"id": "a1", "alias": "Kitchen scene from keypad",
     "triggers": [{"trigger": "event", "event_type": OLD, "event_data": {"leap_button_number": 1}}],
     "actions": []},
    {"id": "a2", "alias": "Old-style trigger key",  # pre-2024.10 spelling
     "trigger": {"platform": "event", "event_type": [OLD, "something_else"]}, "action": []},
    {"id": "a3", "alias": "Already moved",
     "triggers": [{"trigger": "event", "event_type": "lutron_connect_button_event"}], "actions": []},
    {"id": "a4", "alias": "Mentions old name only in an action",
     "triggers": [{"trigger": "time", "at": "07:00:00"}],
     "actions": [{"event": OLD, "event_data": {}}]},
]


def issue(hass):
    return ir.async_get(hass).async_get_issue("lutron_connect", LEGACY_EVENT_ISSUE)


async def site(automations, caseta=False, start_first=True):
    hass_cm = running_hass()
    hass = await hass_cm.__aenter__()
    add_entry(hass, "lutron_connect")
    if caseta:
        add_entry(hass, "lutron_caseta")
    if not start_first:  # setup before the site has started: check deferred to STARTED
        async_check_legacy_automations_later(hass)
    assert await async_setup_component(hass, "automation", {"automation": automations})
    await hass.async_start()
    await hass.async_block_till_done()
    if start_first:
        async_check_legacy_automations_later(hass)
    return hass, hass_cm


async def main():
    hass, cm = await site(AUTOMATIONS, start_first=False)
    i = issue(hass)
    assert i is not None, "no issue raised"
    assert i.severity == ir.IssueSeverity.WARNING and not i.is_fixable and i.translation_key == LEGACY_EVENT_ISSUE
    listed = i.translation_placeholders["automations"]
    assert "automation.kitchen_scene_from_keypad" in listed and "automation.old_style_trigger_key" in listed, listed
    assert "already_moved" not in listed and "mentions_old_name" not in listed, listed
    print("1 issue raised once the site starts, naming only automations TRIGGERED by the old event")
    print("   ", listed.replace("\n", " | "))

    strings = json.loads((ROOT / "custom_components/lutron_connect/strings.json").read_text())
    en = json.loads((ROOT / "custom_components/lutron_connect/translations/en.json").read_text())
    assert strings["issues"][LEGACY_EVENT_ISSUE] == en["issues"][LEGACY_EVENT_ISSUE]
    text = json.dumps(strings["issues"][LEGACY_EVENT_ISSUE])
    assert set(re.findall(r"\{(\w+)\}", text)) <= set(i.translation_placeholders), text
    assert "Home Assistant" not in text and "Ctrlable Pro" in text
    assert not re.search(r"\b(home|rooms?)\b", text, re.I), text
    print("2 strings.json == en.json; every placeholder supplied; Ctrlable Pro wording")

    await cm.__aexit__(None, None, None)

    hass, cm = await site(AUTOMATIONS[2:])
    assert issue(hass) is None
    print("3 no automation on the old event -> no issue")
    await cm.__aexit__(None, None, None)

    hass, cm = await site(AUTOMATIONS, caseta=True)
    assert issue(hass) is None
    print("4 site has its own lutron_caseta entry -> no issue")
    await cm.__aexit__(None, None, None)

    hass, cm = await site(AUTOMATIONS)
    assert issue(hass) is not None
    from homeassistant.components.automation.const import DATA_COMPONENT  # user fixes them
    for ent in hass.data[DATA_COMPONENT].entities:
        ent.raw_config = {"triggers": [{"trigger": "event", "event_type": "lutron_connect_button_event"}]}
    async_check_legacy_automations(hass)
    assert issue(hass) is None
    print("5 issue clears once no automation uses the old event")
    await cm.__aexit__(None, None, None)

asyncio.run(main())
print("ALL PASS")
