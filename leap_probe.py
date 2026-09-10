#!/usr/bin/env python3
"""
Standalone LEAP diagnostic script for the Lutron Connect Bridge.

Emulates every LEAP call made by ConnectSmartbridge._login() and reports
exactly what the bridge returns for each endpoint.

Usage:
    python3 leap_probe.py <host> <keyfile> <certfile> <ca_certs>

Example:
    python3 leap_probe.py 10.1.8.17 client.key client.pem ca.pem

To get the cert files from your HA instance, run on the HA host:
    grep -A 10 "lutron_connect" /config/.storage/core.config_entries
  That shows the keyfile/certfile/ca_certs paths (relative to /config/).
  Copy those three files to this machine.
"""

import asyncio
import json
import sys
import ssl
import logging
import argparse

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
# Quiet the noisy protocol-level loggers
logging.getLogger("pylutron_caseta.leap").setLevel(logging.WARNING)

from pylutron_caseta.leap import LeapProtocol, open_connection, id_from_href
from pylutron_caseta import BridgeResponseError

LEAP_PORT = 8090
REQUEST_TIMEOUT = 10.0  # generous timeout so we can detect hangs quickly


async def request(leap: LeapProtocol, method: str, url: str, body: dict | None = None) -> dict | None:
    """Send a LEAP request and return the response body (or None on 204/error)."""
    print(f"\n  --> {method} {url}")
    try:
        async with asyncio.timeout(REQUEST_TIMEOUT):
            resp = await leap.request(method, url, body)
    except TimeoutError:
        print(f"  *** TIMED OUT after {REQUEST_TIMEOUT}s — endpoint hangs!")
        return "TIMEOUT"
    except Exception as exc:
        print(f"  *** ERROR: {exc}")
        return "ERROR"

    status = resp.Header.StatusCode
    print(f"  <-- {status}")
    if resp.Body:
        body_preview = json.dumps(resp.Body, indent=2)
        lines = body_preview.splitlines()
        if len(lines) > 30:
            preview = "\n".join(lines[:30]) + f"\n  ... ({len(lines)-30} more lines)"
        else:
            preview = body_preview
        print("  " + preview.replace("\n", "\n  "))
        return resp.Body
    return None


async def probe(host: str, keyfile: str, certfile: str, ca_certs: str):
    print(f"\n{'='*60}")
    print(f"Connecting to {host}:{LEAP_PORT} ...")
    print(f"{'='*60}")

    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.load_verify_locations(ca_certs)
    ctx.load_cert_chain(certfile, keyfile)
    ctx.check_hostname = False

    try:
        async with asyncio.timeout(15):
            leap = await open_connection(host, LEAP_PORT, ssl=ctx)
    except Exception as exc:
        print(f"FAILED to connect: {exc}")
        return

    # leap.run() reads responses and resolves in-flight request futures.
    # It must run as a background task or every request() will hang forever.
    run_task = asyncio.get_event_loop().create_task(leap.run())

    print("Connected.\n")

    # ── 1. /project ───────────────────────────────────────────────────────────
    print("\n[1] /project — product type detection")
    project = await request(leap, "ReadRequest", "/project")

    # ── 2. /area ───────────────────────────────────────────────────────────────
    print("\n[2] /area — area hierarchy")
    areas_body = await request(leap, "ReadRequest", "/area")
    areas = {}
    if isinstance(areas_body, dict):
        for a in areas_body.get("Areas", []):
            aid = id_from_href(a["href"])
            areas[aid] = a.get("Name", aid)
        print(f"  => {len(areas)} areas loaded")

    # ── 3. /device?where=IsThisDevice:true ────────────────────────────────────
    print("\n[3] /device?where=IsThisDevice:true — bridge device")
    await request(leap, "ReadRequest", "/device?where=IsThisDevice:true")

    # ── 4. /zone — ALL zones (new flat endpoint) ───────────────────────────────
    print("\n[4] /zone — flat zone list (key new endpoint)")
    zones_body = await request(leap, "ReadRequest", "/zone")
    zone_ids = []
    if isinstance(zones_body, dict):
        zone_ids = [id_from_href(z["href"]) for z in zones_body.get("Zones", [])]
        print(f"  => {len(zone_ids)} zones found")

    # ── 5. /zone/status — zone states subscription target ─────────────────────
    print("\n[5] /zone/status — all zone current states")
    await request(leap, "ReadRequest", "/zone/status")

    # ── 6. /device?where=IsThisDevice:false — all non-bridge devices ──────────
    print("\n[6] /device?where=IsThisDevice:false — keypads and sensors")
    devices_body = await request(leap, "ReadRequest", "/device?where=IsThisDevice:false")
    keypad_ids = []
    if isinstance(devices_body, dict):
        for d in devices_body.get("Devices", []):
            dtype = d.get("DeviceType", "")
            has_bg = "ButtonGroups" in d
            did = id_from_href(d["href"])
            print(f"     device {did}: type={dtype!r}  ButtonGroups={'yes' if has_bg else 'no'}")
            if has_bg or "Keypad" in dtype or "Pico" in dtype:
                keypad_ids.append(did)
        print(f"  => {len(keypad_ids)} potential keypad devices")

    # ── 7. /device/{id}/buttongroup/expanded for first keypad ─────────────────
    if keypad_ids:
        did = keypad_ids[0]
        print(f"\n[7] /device/{did}/buttongroup/expanded — buttons for first keypad")
        await request(leap, "ReadRequest", f"/device/{did}/buttongroup/expanded")
    else:
        print("\n[7] (no keypad devices to probe)")

    # ── 8. Per-area sub-resources — confirm they're 405 ───────────────────────
    if areas:
        first_area = next(iter(areas))
        print(f"\n[8] Per-area endpoints on area {first_area} — expect 405")
        await request(leap, "ReadRequest", f"/area/{first_area}/associatedzone")
        await request(leap, "ReadRequest", f"/area/{first_area}/associatedcontrolstation")

    # ── 9. Other potentially useful endpoints ─────────────────────────────────
    print("\n[9] Other endpoints")
    for ep in ["/occupancygroup", "/virtualbutton", "/area/status"]:
        await request(leap, "ReadRequest", ep)

    # ── 10. Zone type census ───────────────────────────────────────────────────
    print(f"\n[10] Zone ControlType census")
    if isinstance(zones_body, dict):
        from collections import Counter
        type_counts = Counter(z.get("ControlType", "?") for z in zones_body.get("Zones", []))
        for t, n in sorted(type_counts.items()):
            print(f"     {t}: {n}")

    # ── 11. Zone status subscription ──────────────────────────────────────────
    print(f"\n[11] SubscribeRequest /zone/status — subscribe to ALL zone statuses")
    sub_body = await request(leap, "SubscribeRequest", "/zone/status")
    if isinstance(sub_body, dict):
        statuses = sub_body.get("ZoneStatuses", [])
        zone_ids_in_status = {id_from_href(s["Zone"]["href"]) for s in statuses}
        zone_ids_loaded = set(zone_ids)
        missing = zone_ids_in_status - zone_ids_loaded
        print(f"  => status zones: {len(zone_ids_in_status)},  loaded zones: {len(zone_ids_loaded)},  in-status-but-not-loaded: {len(missing)}")
        if missing:
            print(f"  *** MISSING zone IDs (would cause KeyError in _handle_zone_status): {sorted(missing)[:10]}...")

    # ── 12. Command processor — try GoToLevel on a Shade zone ─────────────────
    shade_ids = [id_from_href(z["href"]) for z in zones_body.get("Zones", []) if z.get("ControlType") == "Shade"] if isinstance(zones_body, dict) else []
    dimmed_ids = [id_from_href(z["href"]) for z in zones_body.get("Zones", []) if z.get("ControlType") == "Dimmed"] if isinstance(zones_body, dict) else []

    if shade_ids:
        zid = shade_ids[0]
        print(f"\n[12a] CreateRequest /zone/{zid}/commandprocessor — GoToLevel 50 (Shade)")
        await request(leap, "CreateRequest", f"/zone/{zid}/commandprocessor", {
            "Command": {"CommandType": "GoToLevel", "Parameter": [{"Type": "Level", "Value": 50}]}
        })
        print(f"\n[12b] CreateRequest /zone/{zid}/commandprocessor — GoToDimmedLevel")
        await request(leap, "CreateRequest", f"/zone/{zid}/commandprocessor", {
            "Command": {"CommandType": "GoToDimmedLevel", "DimmedLevelParameters": {"Level": 50}}
        })
        print(f"\n[12c] CreateRequest /zone/{zid}/commandprocessor — Raise")
        await request(leap, "CreateRequest", f"/zone/{zid}/commandprocessor", {
            "Command": {"CommandType": "Raise"}
        })

    if dimmed_ids:
        zid = dimmed_ids[0]
        print(f"\n[12d] CreateRequest /zone/{zid}/commandprocessor — GoToLevel 50 (Dimmed)")
        await request(leap, "CreateRequest", f"/zone/{zid}/commandprocessor", {
            "Command": {"CommandType": "GoToLevel", "Parameter": [{"Type": "Level", "Value": 50}]}
        })

    # ── 13. Flat button/buttongroup endpoints ─────────────────────────────────
    print(f"\n[13a] ReadRequest /button — flat button list")
    await request(leap, "ReadRequest", "/button")
    print(f"\n[13b] ReadRequest /buttongroup — flat button group list")
    await request(leap, "ReadRequest", "/buttongroup")
    print(f"\n[13c] ReadRequest /buttongroup/expanded — all button groups expanded")
    await request(leap, "ReadRequest", "/buttongroup/expanded")

    # ── 14. Per-area endpoints ─────────────────────────────────────────────────
    if areas:
        first_area = next(iter(areas))
        print(f"\n[14a] ReadRequest /area/{first_area}/associatedzone — expect 405")
        await request(leap, "ReadRequest", f"/area/{first_area}/associatedzone")
        print(f"\n[14b] ReadRequest /area/{first_area} — full area detail")
        await request(leap, "ReadRequest", f"/area/{first_area}")

    print(f"\n{'='*60}")
    print("Probe complete.")
    print(f"{'='*60}\n")

    run_task.cancel()
    try:
        await run_task
    except (asyncio.CancelledError, Exception):
        pass


def main():
    parser = argparse.ArgumentParser(description="LEAP endpoint probe for Lutron Connect Bridge")
    parser.add_argument("host", help="Bridge IP address (e.g. 10.1.8.17)")
    parser.add_argument("keyfile", help="Path to client private key (.pem)")
    parser.add_argument("certfile", help="Path to client certificate (.pem)")
    parser.add_argument("ca_certs", help="Path to CA certificate (.pem)")
    args = parser.parse_args()

    asyncio.run(probe(args.host, args.keyfile, args.certfile, args.ca_certs))


if __name__ == "__main__":
    main()
