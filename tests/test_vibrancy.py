"""Vibrancy on the Connect Bridge: command shapes verified live 2026-10-06."""
import asyncio, pathlib, sys, time
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "custom_components"))
from lutron_connect.smartbridge import ConnectSmartbridge
import lutron_connect.number  # noqa: F401 — platform imports cleanly


async def main():
    b = ConnectSmartbridge(lambda: None)
    sent = []

    async def fake_request(kind, url, body=None):
        sent.append(body["Command"]["SpectrumTuningLevelParameters"] if body else None)
    b._request = fake_request
    b.devices = {"9650": {"device_id": "9650", "zone": "9650", "type": "SpectrumTune", "name": "Display Boxes",
                          "current_state": 80, "current_color_mode": "hs", "current_hs_color": (0.0, 100.0)}}
    light_cb, vib_cb = [], []
    b.add_subscriber("9650", lambda: light_cb.append(1))
    b.add_vibrancy_subscriber("9650", lambda: vib_cb.append(b.devices["9650"]["vibrancy"]))

    await b.set_vibrancy("9650", 50)
    assert sent[-1] == {"Level": 80, "Vibrancy": 50, "ColorTuningStatus": {"HSVTuningLevel": {"Hue": 0, "Saturation": 100}}}, sent[-1]
    print("1 colour mode: Level + colour + Vibrancy sent together")

    b.devices["9650"].update(current_color_mode="color_temp", current_color_temp=3000)
    await b.set_vibrancy("9650", 100)
    assert sent[-1] == {"Level": 80, "Vibrancy": 100, "ColorTuningStatus": {"WhiteTuningLevel": {"Kelvin": 3000}}}, sent[-1]
    print("2 white mode: Kelvin sent with Vibrancy")

    b.devices["9650"]["current_state"] = 0
    n = len(sent)
    await b.set_vibrancy("9650", 30)
    assert len(sent) == n and b.devices["9650"]["pending_vibrancy"] == 30 and vib_cb[-1] == 30
    print("3 light off: nothing sent, value held + shown")

    await b.set_value("9650", 60)
    assert sent[-1]["Level"] == 60 and sent[-1]["Vibrancy"] == 30 and sent[-1]["ColorTuningStatus"] == {"WhiteTuningLevel": {"Kelvin": 3000}}, sent[-1]
    assert "pending_vibrancy" not in b.devices["9650"]
    print("4 turn-on carries held Vibrancy + current colour")

    b.devices["9650"]["vibrancy_command_time"] = time.monotonic()
    b._handle_zone_status({"Zone": {"href": "/zone/9650"}, "Level": 60, "Vibrancy": 99})
    assert b.devices["9650"]["vibrancy"] == 30, "stale echo should be ignored"
    b.devices["9650"]["vibrancy_command_time"] = -100
    b.devices["9650"]["color_command_time"] = -100
    light_cb.clear()
    b._handle_zone_status({"Zone": {"href": "/zone/9650"}, "Level": 60, "Vibrancy": 75,
                           "ColorTuningStatus": {"WhiteTuningLevel": {"Kelvin": 3000}}})
    assert b.devices["9650"]["vibrancy"] == 75 and vib_cb[-1] == 75 and light_cb, (b.devices["9650"], light_cb)
    print("5 keypad/app push updates Vibrancy; echo ignored; light still notified")

asyncio.run(main())
print("ALL PASS")
