#!/usr/bin/env python3
"""
reticast.py - RetiCast: current conditions + active watches/warnings for a
Maidenhead grid square, output as a single line of text for NomadNet.

Data source: US National Weather Service API (api.weather.gov) - free, no key.
Uses only the Python standard library (Python 3.9+ for zoneinfo).

Run directly:   ./reticast.py
Or import:      from reticast import get_weather_string
"""

import os

# ============================ CONFIGURATION ============================

GRIDSQUARE = "FN31pr"   # 4, 6 or 8 character Maidenhead locator (example: set your own)

# NWS requires a User-Agent that identifies your app + a contact (email/callsign).
USER_AGENT = "(RetiCast, you@example.com)"

# Alert types to show. Any event whose name contains one of these is included.
# Add "Advisory" or "Statement" if you want those too.
ALERT_KEYWORDS = ("Warning", "Watch")

# How long (minutes) to reuse the last result before hitting the API again.
# Keeps things fast and polite if many people load the page. 0 = no caching.
CACHE_MINUTES = 10
CACHE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "reticast_cache.json")

# Which time to show after the "@":
#   "now"         = time the data was fetched (matches your system clock)
#   "observation" = time the weather station took its reading (often up to
#                   an hour old, since most stations report once an hour)
TIMESTAMP_SOURCE = "now"

# strftime formats (leading zero on the hour is stripped automatically)
TIME_FORMAT = "%I%p %m/%d/%Y"      # -> 11AM 09/24/2026   (use "%I:%M%p ..." for minutes)
EXPIRE_FORMAT = "%I%p"             # -> 10PM

ESCAPE_MICRON = True   # escape backticks so text can't break Micron formatting
MAX_STATIONS = 3       # nearby stations to try if the closest has no data
HTTP_TIMEOUT = 10      # seconds

# =======================================================================

import json
import re
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

API = "https://api.weather.gov"
DEBUG = "--debug" in sys.argv   # run with --debug to print API timings to stderr

STATE_NAMES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas",
    "CA": "California", "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware",
    "DC": "District of Columbia", "FL": "Florida", "GA": "Georgia", "HI": "Hawaii",
    "ID": "Idaho", "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas",
    "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland",
    "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi",
    "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada",
    "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York",
    "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma",
    "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina",
    "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas", "UT": "Utah",
    "VT": "Vermont", "VA": "Virginia", "WA": "Washington", "WV": "West Virginia",
    "WI": "Wisconsin", "WY": "Wyoming", "PR": "Puerto Rico", "GU": "Guam",
    "VI": "U.S. Virgin Islands", "AS": "American Samoa", "MP": "Northern Mariana Islands",
}


# ----------------------------- helpers ---------------------------------

def normalize_grid(grid):
    g = grid.strip()
    if not re.fullmatch(r"[A-Ra-r]{2}(\d{2}([A-Xa-x]{2}(\d{2})?)?)?", g):
        raise ValueError(f"Invalid grid square: {grid!r}")
    out = g[:2].upper() + g[2:4]
    if len(g) >= 6:
        out += g[4:6].lower()
    return out + g[6:8]


def grid_to_latlon(grid):
    """Return (lat, lon) of the CENTER of a Maidenhead grid square."""
    g = normalize_grid(grid)
    lon = (ord(g[0]) - 65) * 20 - 180
    lat = (ord(g[1]) - 65) * 10 - 90
    lon_size, lat_size = 20.0, 10.0
    if len(g) >= 4:
        lon += int(g[2]) * 2
        lat += int(g[3]) * 1
        lon_size, lat_size = 2.0, 1.0
    if len(g) >= 6:
        lon += (ord(g[4]) - 97) * (5 / 60)
        lat += (ord(g[5]) - 97) * (2.5 / 60)
        lon_size, lat_size = 5 / 60, 2.5 / 60
    if len(g) >= 8:
        lon += int(g[6]) * (0.5 / 60)
        lat += int(g[7]) * (0.25 / 60)
        lon_size, lat_size = 0.5 / 60, 0.25 / 60
    return round(lat + lat_size / 2, 4), round(lon + lon_size / 2, 4)


def http_get_json(url):
    req = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "application/geo+json",
    })
    start = time.time()
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
        data = json.load(resp)
    if DEBUG:
        print(f"[debug] {time.time() - start:5.2f}s  {url}", file=sys.stderr)
    return data


def load_cache():
    try:
        with open(CACHE_FILE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_cache(cache):
    try:
        with open(CACHE_FILE, "w") as f:
            json.dump(cache, f)
    except OSError as e:
        # Never fail the page over this, but say so: without a writable cache
        # file, every run repeats the location lookup and is much slower.
        print(f"[reticast] WARNING: cannot write cache file {CACHE_FILE}: {e}",
              file=sys.stderr)


def parse_time(s):
    return datetime.fromisoformat(s) if s else None


def fmt_local(dt, fmt, tz):
    return dt.astimezone(ZoneInfo(tz)).strftime(fmt).lstrip("0")


def county_label(name, state_abbr):
    if not name:
        return "Unknown County"
    suffix = {"LA": "Parish", "AK": "Borough"}.get(state_abbr, "County")
    if name.lower().endswith(("county", "parish", "borough", "census area", "city")):
        return name
    return f"{name} {suffix}"


# --------------------------- NWS lookups -------------------------------

def get_location_meta(grid, lat, lon, cache):
    """County/state/timezone/station list. Static, so cached until grid changes."""
    meta = cache.get("meta")
    if meta and meta.get("grid") == grid and meta.get("station_ids"):
        return meta

    pts = http_get_json(f"{API}/points/{lat:.4f},{lon:.4f}")["properties"]
    state_abbr = pts.get("relativeLocation", {}).get("properties", {}).get("state", "")
    county_name = None
    if pts.get("county"):
        cz = http_get_json(pts["county"])["properties"]
        county_name = cz.get("name")
        state_abbr = cz.get("state") or state_abbr

    # The nearby-station list doesn't change either, so save it too
    stations = http_get_json(pts["observationStations"]).get("features", [])
    station_ids = [st["properties"]["stationIdentifier"] for st in stations[:MAX_STATIONS]]

    meta = {
        "grid": grid,
        "county": county_label(county_name, state_abbr),
        "state": STATE_NAMES.get(state_abbr, state_abbr or "Unknown"),
        "timezone": pts.get("timeZone") or "UTC",
        "station_ids": station_ids,
    }
    cache["meta"] = meta
    return meta


def get_observation(station_ids):
    """Latest observation from the nearest station that actually reports a temp."""
    fallback = None
    for sid in station_ids:
        try:
            obs = http_get_json(f"{API}/stations/{sid}/observations/latest")["properties"]
        except Exception:
            continue
        if (obs.get("temperature") or {}).get("value") is not None:
            return obs
        fallback = fallback or obs
    return fallback


def get_alerts(lat, lon, tz):
    data = http_get_json(f"{API}/alerts/active?point={lat:.4f},{lon:.4f}")
    events = {}  # event name -> latest end time (dedupes repeated alerts)
    for feat in data.get("features", []):
        p = feat.get("properties", {})
        event = p.get("event", "")
        if p.get("status") != "Actual" or p.get("messageType") == "Cancel":
            continue
        if not any(k.lower() in event.lower() for k in ALERT_KEYWORDS):
            continue
        end = parse_time(p.get("ends") or p.get("expires"))
        if event not in events or (end and (events[event] is None or end > events[event])):
            events[event] = end

    # Warnings first, then watches, then anything else; soonest-expiring first
    def rank(item):
        name, end = item
        order = 0 if "warning" in name.lower() else 1 if "watch" in name.lower() else 2
        return (order, end or datetime.max.replace(tzinfo=timezone.utc))

    today = datetime.now(ZoneInfo(tz)).date()
    out = []
    for event, end in sorted(events.items(), key=rank):
        if end:
            when = fmt_local(end, EXPIRE_FORMAT, tz)
            if end.astimezone(ZoneInfo(tz)).date() != today:
                when += end.astimezone(ZoneInfo(tz)).strftime(" %a")  # e.g. "10PM Fri"
            out.append(f"{event.upper()}!!! (Expiring {when} local time)")
        else:
            out.append(f"{event.upper()}!!!")
    return out


# ----------------------------- main ------------------------------------

def get_weather_string():
    cache = load_cache()
    try:
        grid = normalize_grid(GRIDSQUARE)
    except ValueError as e:
        return str(e)

    cached = cache.get("output")
    if (CACHE_MINUTES > 0 and cached and cached.get("grid") == grid
            and time.time() - cached.get("time", 0) < CACHE_MINUTES * 60):
        return cached["text"]

    try:
        lat, lon = grid_to_latlon(grid)
        meta = get_location_meta(grid, lat, lon, cache)
        tz = meta["timezone"]

        # Fetch conditions and alerts at the same time instead of one after another
        with ThreadPoolExecutor(max_workers=2) as pool:
            obs_job = pool.submit(get_observation, meta["station_ids"])
            alert_job = pool.submit(get_alerts, lat, lon, tz)
            obs = obs_job.result() or {}
            try:
                alerts = alert_job.result()
            except Exception:
                alerts = []  # don't lose the conditions line if the alerts call fails

        temp_c = (obs.get("temperature") or {}).get("value")
        hum = (obs.get("relativeHumidity") or {}).get("value")
        temp = f"{round(temp_c * 9 / 5 + 32)}F" if temp_c is not None else "N/A"
        humidity = f"{round(hum)}%" if hum is not None else "N/A"
        sky = obs.get("textDescription") or "N/A"
        if TIMESTAMP_SOURCE == "observation":
            shown_time = parse_time(obs.get("timestamp")) or datetime.now(timezone.utc)
        else:
            shown_time = datetime.now(timezone.utc)

        text = (f"Current weather conditions for gridsquare {grid} "
                f"({meta['county']}, {meta['state']}) @ {fmt_local(shown_time, TIME_FORMAT, tz)}: "
                f"Temp. {temp}, Humidity {humidity}, {sky}")

        if alerts:
            text += ", Warning/Watch: " + " ".join(alerts)

        if ESCAPE_MICRON:
            text = text.replace("`", "\\`")

        cache["output"] = {"grid": grid, "time": time.time(), "text": text}
        save_cache(cache)
        return text

    except Exception as e:
        # Serve the last good result rather than an error if we have one
        if cached and cached.get("grid") == grid:
            return cached["text"] + " (cached - update failed)"
        return f"Weather data unavailable for gridsquare {grid} ({type(e).__name__}: {e})"


if __name__ == "__main__":
    print(get_weather_string())
