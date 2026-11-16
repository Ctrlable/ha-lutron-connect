"""Keypad press/release fires lutron_connect_button_event (not Caseta's name), same payload keys."""
import asyncio, pathlib, sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "custom_components"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from pylutron_caseta.messages import Response, ResponseHeader
from hass_harness import add_entry, running_hass
import lutron_connect
from lutron_connect.smartbridge import ConnectSmartbridge

# Same keys as core lutron_caseta's button event — Buttons Machine reads these.
CASETA_KEYS = {"serial", "type", "button_number", "leap_button_number", "device_name",
               "device_id", "area_name", "button_type", "action"}


async def main():
    async with running_hass() as hass:
        entry = add_entry(hass, "lutron_connect")
        b = ConnectSmartbridge(lambda: None)
        b.areas = {"1": {"id": "1", "name": "Site", "parent_id": None},
                   "7": {"id": "7", "name": "Kitchen", "parent_id": "1"}}
        b.devices = {
            "1": {"device_id": "1", "name": "Connect Bridge", "type": "ConnectBridge",
                  "model": "CONNECT-BDG2", "serial": 90001234, "area": None},
            "512": {"device_id": "512", "name": "Kitchen_Entry Keypad", "type": "SeeTouchKeypad",
                    "model": "HQWT-U-P2W", "serial": 51200001, "area": "7"},
        }
        # Built by the real loader, from LEAP /button JSON.
        await b._load_connect_button({"href": "/button/601", "ButtonNumber": 3}, b.devices["512"])

        seen = {"lutron_connect_button_event": [], "lutron_caseta_button_event": []}
        for name, bucket in seen.items():
            hass.bus.async_listen(name, lambda e, bucket=bucket: bucket.append(e.data))

        data = lutron_connect._async_setup_keypads(hass, entry.entry_id, b, b.devices["1"])
        (keypad,) = data["keypads"].values()

        def push(event_type):  # LEAP MultipleButtonStatusEvent, as the bridge sends it
            b._handle_connect_button_status(Response(
                Header=ResponseHeader(Url="/button/status/event", MessageBodyType="MultipleButtonStatusEvent"),
                CommuniqueType="ReadResponse",
                Body={"ButtonStatuses": [{"Button": {"href": "/button/601"},
                                          "ButtonEvent": {"EventType": event_type}}]}))

        push("Press"); push("Release")
        await hass.async_block_till_done()

        assert seen["lutron_caseta_button_event"] == [], seen["lutron_caseta_button_event"]
        print("1 Caseta's event name is no longer fired")
        fired = seen["lutron_connect_button_event"]
        assert len(fired) == 2, seen
        assert all(set(d) == CASETA_KEYS for d in fired), [set(d) ^ CASETA_KEYS for d in fired]
        print("2 lutron_connect_button_event fired with exactly the Caseta payload keys")
        assert fired[0] == {
            "serial": 51200001, "type": "SeeTouchKeypad", "button_number": None,
            "leap_button_number": 3, "device_name": "Entry Keypad", "device_id": keypad["dr_device_id"],
            "area_name": "Kitchen", "button_type": "Button 3", "action": "press",
        }, fired[0]
        assert fired[1]["action"] == "release"
        print("3 values unchanged: press then release")

asyncio.run(main())
print("ALL PASS")
