#!/usr/bin/env python3
"""
reticast.py - RetiCast: live weather for NomadNet pages.

Turns a Maidenhead grid square into current conditions, active NWS alerts,
and the forecast, using the free US National Weather Service API
(api.weather.gov, no key). Uses only the Python standard library
(Python 3.9+ for zoneinfo).

Run directly:   ./reticast.py            (refresh data, print the one-line summary)
                ./reticast.py --page     (print the full Micron weather page body)
                ./reticast.py --debug    (show API timing)
Or import:      from reticast import get_weather_string, get_weather_micron
"""

import os

# ============================ CONFIGURATION ============================

GRIDSQUARE = "FN31pr"   # 4, 6 or 8 character Maidenhead locator (example: set your own)

# NWS requires a User-Agent that identifies your app + a contact (email/callsign).
USER_AGENT = "(RetiCast, you@example.com)"

UNITS = "us"            # "us" (F, mph, inHg, miles) or "metric" (C, km/h, hPa, km)

# Alert types shown in the one-line summary. Any event whose name contains one
# of these is included. Add "Advisory" or "Statement" if you want those too.
ALERT_KEYWORDS = ("Warning", "Watch")
PAGE_ALL_ALERTS = True  # the full page shows every active alert, advisories included
MAX_ALERT_CHARS = 600   # trim long alert descriptions on the page (0 = no limit)

FORECAST_PERIODS = 14   # forecast periods on the page (14 = 7 days, day + night)

# How long (minutes) to reuse fetched data before hitting the API again.
CACHE_MINUTES = 10            # current conditions and alerts
FORECAST_CACHE_MINUTES = 30   # the forecast changes less often
CACHE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "reticast_cache.json")

# Which time to show after the "@" in the one-line summary:
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
META_VERSION = 2                # bump when the saved location format changes

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

COMPASS = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
           "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]


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


def esc(text):
    text = "" if text is None else str(text)
    return text.replace("`", "\\`") if ESCAPE_MICRON else text


def county_label(name, state_abbr):
    if not name:
        return "Unknown County"
    suffix = {"LA": "Parish", "AK": "Borough"}.get(state_abbr, "County")
    if name.lower().endswith(("county", "parish", "borough", "census area", "city")):
        return name
    return f"{name} {suffix}"


def qv(prop):
    """Value from an NWS quantity object like {"value": 21.5, "unitCode": ...}."""
    return (prop or {}).get("value")


def speed_kmh(prop):
    v = qv(prop)
    if v is None:
        return None
    return v * 3.6 if "m_s-1" in (prop or {}).get("unitCode", "") else v


# ---------------------------- unit output ------------------------------

METRIC = UNITS.lower().startswith("m")


def temp(c):
    if c is None:
        return "N/A"
    return f"{round(c)}C" if METRIC else f"{round(c * 9 / 5 + 32)}F"


def speed(kmh):
    if kmh is None:
        return None
    return f"{round(kmh)} km/h" if METRIC else f"{round(kmh / 1.609344)} mph"


def pressure(pa):
    if pa is None:
        return "N/A"
    return f"{pa / 100:.0f} hPa" if METRIC else f"{pa / 3386.389:.2f} inHg"


def distance(m):
    if m is None:
        return "N/A"
    return f"{m / 1000:.0f} km" if METRIC else f"{m / 1609.344:.0f} mi"


def compass(deg):
    return None if deg is None else COMPASS[round(deg / 22.5) % 16]


# --------------------------- NWS lookups -------------------------------

def get_location_meta(grid, lat, lon, cache):
    """County/state/timezone/stations/forecast URL. Static, so cached until grid changes."""
    meta = cache.get("meta")
    if meta and meta.get("grid") == grid and meta.get("version") == META_VERSION:
        return meta

    pts = http_get_json(f"{API}/points/{lat:.4f},{lon:.4f}")["properties"]
    state_abbr = pts.get("relativeLocation", {}).get("properties", {}).get("state", "")
    county_name = None
    if pts.get("county"):
        cz = http_get_json(pts["county"])["properties"]
        county_name = cz.get("name")
        state_abbr = cz.get("state") or state_abbr

    stations = http_get_json(pts["observationStations"]).get("features", [])[:MAX_STATIONS]
    meta = {
        "version": META_VERSION,
        "grid": grid,
        "county": county_label(county_name, state_abbr),
        "state": STATE_NAMES.get(state_abbr, state_abbr or "Unknown"),
        "timezone": pts.get("timeZone") or "UTC",
        "station_ids": [st["properties"]["stationIdentifier"] for st in stations],
        "station_names": {st["properties"]["stationIdentifier"]: st["properties"].get("name", "")
                          for st in stations},
        "forecast_url": pts.get("forecast"),
    }
    cache["meta"] = meta
    cache.pop("data", None)   # location changed: drop data for the old one
    return meta


def fetch_observation(station_ids):
    """Latest observation from the nearest station that reports a temperature."""
    fallback = None
    for sid in station_ids:
        try:
            p = http_get_json(f"{API}/stations/{sid}/observations/latest")["properties"]
        except Exception:
            continue
        obs = {
            "station": sid,
            "timestamp": p.get("timestamp"),
            "desc": p.get("textDescription"),
            "temp_c": qv(p.get("temperature")),
            "dew_c": qv(p.get("dewpoint")),
            "rh": qv(p.get("relativeHumidity")),
            "heat_c": qv(p.get("heatIndex")),
            "chill_c": qv(p.get("windChill")),
            "wind_dir": qv(p.get("windDirection")),
            "wind_kmh": speed_kmh(p.get("windSpeed")),
            "gust_kmh": speed_kmh(p.get("windGust")),
            "pressure_pa": qv(p.get("barometricPressure")) or qv(p.get("seaLevelPressure")),
            "vis_m": qv(p.get("visibility")),
        }
        if obs["temp_c"] is not None:
            return obs
        fallback = fallback or obs
    return fallback


def fetch_alerts(lat, lon):
    data = http_get_json(f"{API}/alerts/active?point={lat:.4f},{lon:.4f}")
    alerts = []
    for feat in data.get("features", []):
        p = feat.get("properties", {})
        if p.get("status") != "Actual" or p.get("messageType") == "Cancel":
            continue
        alerts.append({
            "event": p.get("event", ""),
            "severity": p.get("severity"),
            "headline": p.get("headline"),
            "description": p.get("description"),
            "instruction": p.get("instruction"),
            "ends": p.get("ends") or p.get("expires"),
        })
    return alerts


def fetch_forecast(url):
    if not url:
        return []
    if METRIC:
        url += ("&" if "?" in url else "?") + "units=si"
    periods = http_get_json(url).get("properties", {}).get("periods", [])
    return [{
        "name": p.get("name"),
        "is_day": p.get("isDaytime"),
        "temp": p.get("temperature"),
        "unit": p.get("temperatureUnit"),
        "pop": qv(p.get("probabilityOfPrecipitation")),
        "wind": " ".join(x for x in (p.get("windDirection"), p.get("windSpeed")) if x),
        "short": p.get("shortForecast"),
    } for p in periods]


# ------------------------------ updating -------------------------------

def refresh(cache, grid):
    """Bring the cached data up to date. Returns (meta, data, stale)."""
    lat, lon = grid_to_latlon(grid)
    meta = get_location_meta(grid, lat, lon, cache)
    data = cache.get("data") or {}
    now = time.time()

    need_now = now - data.get("fetched", 0) >= CACHE_MINUTES * 60
    need_fc = now - data.get("forecast_fetched", 0) >= FORECAST_CACHE_MINUTES * 60
    if not (need_now or need_fc):
        return meta, data, False

    stale = False
    with ThreadPoolExecutor(max_workers=3) as pool:
        jobs = {}
        if need_now:
            jobs["obs"] = pool.submit(fetch_observation, meta["station_ids"])
            jobs["alerts"] = pool.submit(fetch_alerts, lat, lon)
        if need_fc:
            jobs["forecast"] = pool.submit(fetch_forecast, meta.get("forecast_url"))

        if need_now:
            try:
                obs = jobs["obs"].result()
                if obs is None:
                    raise RuntimeError("no station data")
                data["obs"] = obs
                data["fetched"] = now
            except Exception:
                stale = True          # keep the previous observation, if any
            try:
                data["alerts"] = jobs["alerts"].result()
            except Exception:
                data.setdefault("alerts", [])
        if need_fc:
            try:
                data["forecast"] = jobs["forecast"].result()
                data["forecast_fetched"] = now
            except Exception:
                data.setdefault("forecast", [])

    cache["data"] = data
    save_cache(cache)
    if not data.get("obs"):
        raise RuntimeError("no weather data available yet")
    return meta, data, stale


_RESULT = None   # one refresh per run, even when several functions are called


def get_data():
    global _RESULT
    if _RESULT is None:
        try:
            _RESULT = refresh(load_cache(), normalize_grid(GRIDSQUARE))
        except Exception as e:
            _RESULT = e
    if isinstance(_RESULT, Exception):
        raise _RESULT
    return _RESULT


# ----------------------------- one line --------------------------------

def line_alerts(alerts, tz):
    events = {}   # event name -> latest end time (dedupes repeated alerts)
    for a in alerts:
        event = a["event"]
        if not any(k.lower() in event.lower() for k in ALERT_KEYWORDS):
            continue
        end = parse_time(a.get("ends"))
        if event not in events or (end and (events[event] is None or end > events[event])):
            events[event] = end

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
                when += end.astimezone(ZoneInfo(tz)).strftime(" %a")
            out.append(f"{event.upper()}!!! (Expiring {when} local time)")
        else:
            out.append(f"{event.upper()}!!!")
    return out


def build_line(meta, data, stale=False):
    tz = meta["timezone"]
    obs = data["obs"]
    if TIMESTAMP_SOURCE == "observation" and obs.get("timestamp"):
        shown = parse_time(obs["timestamp"])
    else:
        shown = datetime.fromtimestamp(data.get("fetched", time.time()), timezone.utc)
    humidity = f"{round(obs['rh'])}%" if obs.get("rh") is not None else "N/A"
    text = (f"Current weather conditions for gridsquare {meta['grid']} "
            f"({meta['county']}, {meta['state']}) @ {fmt_local(shown, TIME_FORMAT, tz)}: "
            f"Temp. {temp(obs.get('temp_c'))}, Humidity {humidity}, {obs.get('desc') or 'N/A'}")
    alerts = line_alerts(data.get("alerts", []), tz)
    if alerts:
        text += ", Warning/Watch: " + " ".join(alerts)
    if stale:
        text += " (cached - update failed)"
    return esc(text)


# ----------------------------- full page -------------------------------

def alert_color(event):
    e = event.lower()
    return "`Ff00" if "warning" in e else "`Ff80" if "watch" in e else "`Fff0"


def trim(text, limit):
    """Unwrap NWS hard-wrapped text, keep paragraph breaks, trim to `limit` chars."""
    paras = [" ".join(p.split()) for p in re.split(r"\n\s*\n", text or "")]
    paras = [p for p in paras if p]
    out, used = [], 0
    for p in paras:
        if limit and used + len(p) > limit:
            room = limit - used
            if room > 40:
                out.append(p[:room].rsplit(" ", 1)[0] + " ...")
            else:
                out.append("...")
            break
        out.append(p)
        used += len(p)
    return "\n".join(out)


def build_page(meta, data, stale=False):
    tz = meta["timezone"]
    obs = data["obs"]
    out = []

    updated = datetime.fromtimestamp(data.get("fetched", time.time()), timezone.utc)
    station = obs.get("station", "")
    station_name = meta.get("station_names", {}).get(station, "")
    note = f"updated {fmt_local(updated, '%I:%M%p %m/%d/%Y', tz)}"
    if station:
        note += f"  |  station {station}" + (f" ({station_name})" if station_name else "")
    if stale:
        note += "  |  (cached - update failed)"
    out.append(f"`F888{esc(note)}`f")
    out.append("")

    # Alerts
    alerts = data.get("alerts", [])
    if not PAGE_ALL_ALERTS:
        alerts = [a for a in alerts
                  if any(k.lower() in a["event"].lower() for k in ALERT_KEYWORDS)]
    if alerts:
        out.append(">>Active Alerts")
        for a in alerts:
            end = parse_time(a.get("ends"))
            until = f"  until {fmt_local(end, '%I:%M%p %a', tz)}" if end else ""
            out.append(f"{alert_color(a['event'])}`!{esc(a['event'].upper())}`!`f{esc(until)}")
            if a.get("headline"):
                out.append(esc(trim(a["headline"], 0)))
            if a.get("description"):
                out.append(esc(trim(a["description"], MAX_ALERT_CHARS)))
            if a.get("instruction"):
                out.append("`!What to do:`! " + esc(trim(a["instruction"], MAX_ALERT_CHARS)))
            out.append("")

    # Current conditions
    out.append(">>Current Conditions")
    out.append(f"`!{esc(obs.get('desc') or 'N/A')}`!")
    feels = obs.get("heat_c") if obs.get("heat_c") is not None else obs.get("chill_c")
    t = temp(obs.get("temp_c"))
    if feels is not None and temp(feels) != t:
        t += f" (feels like {temp(feels)})"
    rows = [("Temperature", t),
            ("Humidity", f"{round(obs['rh'])}%" if obs.get("rh") is not None else "N/A"),
            ("Dew point", temp(obs.get("dew_c")))]
    wind_speed = speed(obs.get("wind_kmh"))
    if obs.get("wind_kmh") == 0:
        wind = "Calm"
    elif wind_speed:
        wind = " ".join(x for x in (compass(obs.get("wind_dir")), wind_speed) if x)
        if obs.get("gust_kmh"):
            wind += f", gusts {speed(obs['gust_kmh'])}"
    else:
        wind = "N/A"
    rows += [("Wind", wind),
             ("Pressure", pressure(obs.get("pressure_pa"))),
             ("Visibility", distance(obs.get("vis_m")))]
    for label, value in rows:
        out.append(f"{label.ljust(13)}{esc(value)}")
    out.append("")

    # Forecast
    periods = data.get("forecast", [])[:FORECAST_PERIODS]
    if periods:
        out.append(">>Forecast")
        width = max(len(p["name"] or "") for p in periods) + 2
        for p in periods:
            label = "High" if p.get("is_day") else "Low"
            t = f"{label} {p['temp']}{p.get('unit') or ''}" if p.get("temp") is not None else ""
            pop = f"Precip {p['pop']}%" if p.get("pop") else ""
            out.append(f"`!{esc((p['name'] or '').ljust(width))}`!"
                       f"{esc(t.ljust(10))}{esc(pop.ljust(12))}{esc(p.get('short') or '')}")
        out.append("")

    out.append("`F888Data: National Weather Service (weather.gov)`f")
    return "\n".join(out)


# ------------------------------ public ---------------------------------

def get_weather_string():
    """One-line summary for embedding in other pages."""
    try:
        return build_line(*get_data())
    except Exception as e:
        return f"Weather data unavailable for gridsquare {GRIDSQUARE} ({type(e).__name__})"


def get_weather_title():
    """'Hartford County, Connecticut (FN31pr)' for page headings."""
    try:
        meta = get_data()[0]
        return esc(f"{meta['county']}, {meta['state']} ({meta['grid']})")
    except Exception:
        return esc(GRIDSQUARE)


def get_weather_micron():
    """Full weather page body: alerts, current conditions, forecast."""
    try:
        return build_page(*get_data())
    except Exception as e:
        return f"Weather data unavailable for gridsquare {GRIDSQUARE} ({type(e).__name__}: {e})"


if __name__ == "__main__":
    print(get_weather_micron() if "--page" in sys.argv else get_weather_string())
