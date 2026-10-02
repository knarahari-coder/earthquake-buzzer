#!/usr/bin/env python3
"""
Devan's Earthquake Buzzer
=========================
Every few minutes this program asks the U.S. Geological Survey (USGS):
"Were there any earthquakes near Seattle lately?"

If it finds a new one that is big enough, it sends a buzz to Dad's phone
through the free ntfy app. Bigger quakes get louder, more urgent buzzes.

The tiny quakes that happen all the time are skipped.
"""

import json
import math
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

# ---------------------------------------------------------------------------
# SETTINGS: Devan, these are the knobs you can turn!
# ---------------------------------------------------------------------------

MIN_MAGNITUDE = 1.5          # Skip anything smaller than this
RADIUS_MILES = 60            # How far from Seattle to look
CENTER_NAME = "Seattle"
CENTER_LAT = 47.6062         # Downtown Seattle
CENTER_LON = -122.3321

# Alert levels, biggest first. Each one has its own title and buzz strength.
# ntfy priority: 3 = normal buzz, 4 = strong buzz, 5 = URGENT (loud + long)
ALERT_LEVELS = [
    {"name": "feel",   "min_mag": 3.0, "priority": 5, "emoji": "🔴",
     "title": "You might FEEL this one!",  "tags": ["rotating_light"]},
    {"name": "maybe",  "min_mag": 2.5, "priority": 4, "emoji": "🟠",
     "title": "Some people may have felt it", "tags": ["warning"]},
    {"name": "secret", "min_mag": MIN_MAGNITUDE, "priority": 3, "emoji": "🔵",
     "title": "Secret quake",              "tags": ["ocean"]},
]

# ---------------------------------------------------------------------------
# Behind-the-scenes settings (you probably don't need to change these)
# ---------------------------------------------------------------------------

LOOKBACK_HOURS = 6           # How far back to check each time
FORGET_AFTER_DAYS = 3        # Stop remembering old quakes after this long
SEEN_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "seen.json")
USGS_URL = "https://earthquake.usgs.gov/fdsnws/event/1/query"
NTFY_SERVER = os.environ.get("NTFY_SERVER", "https://ntfy.sh")
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "").strip()
TEST_MODE = os.environ.get("TEST_MODE", "").lower() == "true"
DRY_RUN = os.environ.get("DRY_RUN", "").lower() == "true"   # print instead of buzzing
FIXTURE = os.environ.get("QUAKE_FIXTURE")                     # fake data for testing
LOCAL_TZ = ZoneInfo("America/Los_Angeles")
KM_PER_MILE = 1.609344


def level_for(mag):
    """Pick the alert level for a magnitude (or None if it's too small)."""
    for i, level in enumerate(ALERT_LEVELS):
        if mag >= level["min_mag"]:
            return i, level
    return None, None


def miles_between(lat1, lon1, lat2, lon2):
    """Distance over the Earth's curved surface (the 'haversine' formula)."""
    r = 3958.8  # Earth's radius in miles
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def energy_in_cars(mag):
    """
    Fun fact: how many cars zooming at 65 mph have the same energy?
    Scientists estimate quake energy as 10^(1.5 x magnitude + 4.8) joules.
    A 3,300 lb car at 65 mph has about 630,000 joules of motion energy.
    Every +1 magnitude is about 32 TIMES more energy!
    """
    quake_joules = 10 ** (1.5 * mag + 4.8)
    car_joules = 0.5 * 1500 * (29.06 ** 2)
    cars = quake_joules / car_joules
    if cars < 10:
        return f"{cars:.0f}"
    if cars < 1000:
        return f"{round(cars, -1):,.0f}"
    return f"{round(cars, -2):,.0f}"


def fetch_quakes():
    """Ask USGS for recent earthquakes near Seattle."""
    if FIXTURE:
        with open(FIXTURE) as f:
            return json.load(f)["features"]
    start = datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_HOURS)
    params = {
        "format": "geojson",
        "starttime": start.strftime("%Y-%m-%dT%H:%M:%S"),
        "latitude": CENTER_LAT,
        "longitude": CENTER_LON,
        "maxradiuskm": round(RADIUS_MILES * KM_PER_MILE, 1),
        "minmagnitude": MIN_MAGNITUDE,
        "eventtype": "earthquake",     # skip quarry blasts and explosions
        "orderby": "time-asc",
    }
    url = USGS_URL + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "devan-earthquake-buzzer"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)["features"]


def describe(quake):
    """Turn USGS data into a friendly notification."""
    p = quake["properties"]
    lon, lat, depth_km = quake["geometry"]["coordinates"]
    mag = p["mag"]
    _, level = level_for(mag)
    miles = miles_between(CENTER_LAT, CENTER_LON, lat, lon)
    when = datetime.fromtimestamp(p["time"] / 1000, LOCAL_TZ)
    when_text = when.strftime("%a %b %-d, %-I:%M %p")
    depth_mi = max(depth_km or 0, 0) / KM_PER_MILE

    lines = [
        f"📍 {p.get('place') or 'Near Seattle'}",
        f"📏 {miles:.0f} miles from {CENTER_NAME}, {depth_mi:.0f} miles underground",
        f"🕒 {when_text}",
        f"🚗 Energy: like {energy_in_cars(mag)} cars going 65 mph",
    ]
    if p.get("felt"):
        lines.append(f"🙋 {p['felt']:,} people reported feeling it")
    return {
        "title": f"{level['emoji']} M{mag:.1f} · {level['title']}",
        "message": "\n".join(lines),
        "priority": level["priority"],
        "tags": level["tags"],
        "click": p.get("url") or "https://earthquake.usgs.gov",
    }


def buzz(note):
    """Send a notification to the phone through ntfy."""
    if DRY_RUN:
        print("---- WOULD BUZZ ----")
        print(note["title"], f"(priority {note['priority']})")
        print(note["message"])
        return
    body = dict(note, topic=NTFY_TOPIC)
    req = urllib.request.Request(
        NTFY_SERVER,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        resp.read()
    print("Buzzed:", note["title"])


def load_seen():
    try:
        with open(SEEN_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_seen(seen):
    cutoff_ms = (time.time() - FORGET_AFTER_DAYS * 86400) * 1000
    kept = {k: v for k, v in seen.items() if v.get("time", 0) >= cutoff_ms}
    with open(SEEN_FILE, "w") as f:
        json.dump(kept, f, indent=2, sort_keys=True)
        f.write("\n")


def send_test_buzzes():
    """Send one example of each alert level so you can hear the difference."""
    print("TEST MODE: sending one example of each alert level")
    now_ms = int(time.time() * 1000)
    examples = [(1.8, "TEST: 5 km NE of Woodinville, WA"),
                (2.7, "TEST: 3 km W of Bremerton, WA"),
                (3.4, "TEST: 8 km SE of Kent, WA")]
    for mag, place in examples:
        fake = {"properties": {"mag": mag, "place": place, "time": now_ms,
                               "url": "https://earthquake.usgs.gov/earthquakes/map/"},
                "geometry": {"coordinates": [-122.2, 47.7, 20.0]}}
        note = describe(fake)
        note["title"] = "TEST " + note["title"]
        buzz(note)
        time.sleep(2)


def main():
    if not NTFY_TOPIC and not DRY_RUN:
        sys.exit("ERROR: NTFY_TOPIC is missing. Add it as a GitHub secret.")

    if TEST_MODE:
        send_test_buzzes()

    try:
        quakes = fetch_quakes()
    except (urllib.error.URLError, TimeoutError, ValueError) as e:
        # USGS hiccup. No big deal, we'll try again in 5 minutes.
        print("Couldn't reach USGS this time:", e)
        return

    print(f"USGS found {len(quakes)} quake(s) of M{MIN_MAGNITUDE}+ in the last {LOOKBACK_HOURS} hours")
    seen = load_seen()
    changed = False

    for q in quakes:
        p = q["properties"]
        if p.get("mag") is None:
            continue
        level_index, level = level_for(p["mag"])
        if level is None:
            continue
        # A quake can have several ID codes (one per seismic network). Check them all.
        ids = [i for i in (p.get("ids") or "").split(",") if i] or [q["id"]]
        if q["id"] not in ids:
            ids.append(q["id"])
        previous = next((seen[i] for i in ids if i in seen), None)

        if previous is None:
            note = describe(q)
        elif level_index < previous["level"]:
            # Scientists re-measured it and it got bigger!
            note = describe(q)
            note["title"] = "⬆️ UPGRADED " + note["title"]
        else:
            continue  # Already buzzed about this one

        try:
            buzz(note)
        except (urllib.error.URLError, TimeoutError) as e:
            print("Couldn't send the buzz, will retry next time:", e)
            continue
        record = {"level": level_index, "mag": p["mag"], "time": p["time"]}
        for i in ids:
            seen[i] = record
        changed = True

    if changed or not os.path.exists(SEEN_FILE):
        save_seen(seen)


if __name__ == "__main__":
    main()
