#!/usr/bin/env python3
"""
reticast.py - RetiCast 2.0: live weather for NomadNet pages, for any location.

Visitors can search for any place (city, ZIP code, grid square, or lat,lon).
Visitors who identify to the node can save a default location and up to
MAX_FAVORITES favorites, and choose whether the page opens on their default
location or on an overview of all their saved places. Visitors who have not
identified (or have not saved a default) see the server's DEFAULT_LOCATION.

Weather sources (no API keys):
  - US locations:  National Weather Service (api.weather.gov), with alerts
  - Elsewhere:     Open-Meteo (open-meteo.com), no alerts
Place search:      Open-Meteo geocoding, plus Zippopotam.us for US ZIP codes

Python standard library only (Python 3.9+).

Run directly:   ./reticast.py              refresh saved places, print the one-line summary
                ./reticast.py --page       print the server default's full weather view
                ./reticast.py --search Q   test a place search
                ./reticast.py --check      look up DEFAULT_LOCATION and show what it resolved to
                ./reticast.py --debug      also print API timings to stderr
Or import:      from reticast import get_weather_string, get_weather_title, get_weather_micron
The interactive page is pages/reticast.mu, which calls page_main().

License: Unlicense (public domain). See LICENSE.
This software is possible because my parents believed in me and encouraged me to follow my passions.
"""

import os

# ============================ CONFIGURATION ============================

# Where visitors land if they haven't identified and saved a default.
# Any search the page accepts works here: a grid square ("EM20fb"), a city
# ("Houston, TX"), a US ZIP code ("77002"), or "lat,lon" ("29.76,-95.37").
DEFAULT_LOCATION = "EM20fb"
DEFAULT_LOCATION_NAME = ""     # optional display name for it ("" = automatic)

# NWS requires a User-Agent that identifies your app + a contact (email/callsign).
USER_AGENT = "(RetiCast, you@example.com)"

UNITS = "us"                   # "us" (F, mph, inHg, miles) or "metric" (C, km/h, hPa, km)

PAGE_PATH = "/page/reticast.mu"   # this page's path on your node (used in links)

MAX_FAVORITES = 5              # favorites per visitor, not counting the default
MAX_USERS = 1000               # most visitors who can save places on this node
SEARCH_RESULTS = 8             # most results shown for a search

# Alert types shown in the one-line summary. Any event whose name contains one
# of these is included. Add "Advisory" or "Statement" if you want those too.
ALERT_KEYWORDS = ("Warning", "Watch")
MAX_ALERT_CHARS = 600          # trim long alerts on the page (0 = no limit);
                               # trimmed alerts get a "Read full alert" link
FORECAST_PERIODS = 14          # NWS periods on the page (14 = 7 days, day + night)

# How long (minutes) to reuse fetched data before asking the API again.
CACHE_MINUTES = 10             # current conditions and alerts
FORECAST_CACHE_MINUTES = 30    # the forecast changes less often
RETRY_MINUTES = 2              # after a failed update, wait this long before retrying

# The cron run keeps saved places fresh so they load instantly. This caps how
# many places it refreshes per run (the server default is always included).
PREFETCH_LIMIT = 50
PRUNE_DAYS = 7                 # delete cached data for unsaved places after this

# Which time to show after the "@" in the one-line summary:
#   "now"         = time the data was fetched
#   "observation" = time the weather station took its reading
TIMESTAMP_SOURCE = "now"
TIME_FORMAT = "%I%p %m/%d/%Y"      # -> 11AM 09/24/2026   (use "%I:%M%p ..." for minutes)
EXPIRE_FORMAT = "%I%p"             # -> 10PM

MAX_STATIONS = 3               # nearby NWS stations to try if the closest has no data
HTTP_TIMEOUT = 8               # seconds per request

# Alert messages (LXMF). These need the separate notifier service,
# reticast_notify.py; the options only appear on the page while it's running.
TEST_COOLDOWN_MINUTES = 10     # least time between test/verification messages per visitor

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reticast_data")

# =======================================================================

import base64
import contextlib
import fcntl
import hashlib
import http.client
import json
import math
import re
import sys
import tempfile
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

try:
    from zoneinfo import ZoneInfo
except ImportError:          # Python < 3.9: times fall back to UTC
    ZoneInfo = None

VERSION = "2.1"
DEBUG = "--debug" in sys.argv
WX_VERSION = 2               # bump when the cached weather format changes

NWS_API = "https://api.weather.gov"
OM_FORECAST = "https://api.open-meteo.com/v1/forecast"
OM_GEOCODE = "https://geocoding-api.open-meteo.com/v1/search"
ZIP_API = "https://api.zippopotam.us/us/"

CACHE_DIR = os.path.join(DATA_DIR, "cache")
USERS_FILE = os.path.join(DATA_DIR, "users.json")
USERS_LOCK = os.path.join(DATA_DIR, "users.lock")
SERVER_DEFAULT_FILE = os.path.join(DATA_DIR, "server_default.json")
NOTIFY_DIR = os.path.join(DATA_DIR, "notify")
OUTBOX_DIR = os.path.join(NOTIFY_DIR, "outbox")
HEARTBEAT_FILE = os.path.join(NOTIFY_DIR, "heartbeat.json")
STATUS_FILE = os.path.join(NOTIFY_DIR, "status.json")   # written by the notifier
HEARTBEAT_STALE = 300          # seconds; older than this = notifier not running

# Which alerts each choice includes (an alert's rank must be <= the level's)
NOTIFY_LEVELS = {"warning": 0, "watch": 1, "advisory": 2, "all": 3}
NOTIFY_LEVEL_TEXT = {
    "warning": "Warnings only",
    "watch": "Warnings and watches",
    "advisory": "Warnings, watches and advisories",
    "all": "Everything, including statements",
}

META_DAYS = 30               # NWS location lookups (county, stations) are reused this long
GEO_DAYS = 7                 # search results are reused this long

# Countries/territories covered by the National Weather Service
NWS_COUNTRIES = {"US", "PR", "GU", "VI", "AS", "MP", "UM"}

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
    "VI": "U.S. Virgin Islands", "AS": "American Samoa",
    "MP": "Northern Mariana Islands",
}
STATE_ABBR = {name.lower(): abbr for abbr, name in STATE_NAMES.items()}

COMPASS = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
           "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]

# WMO weather codes used by Open-Meteo
WMO = {
    0: "Clear", 1: "Mostly Clear", 2: "Partly Cloudy", 3: "Overcast",
    45: "Fog", 48: "Freezing Fog", 51: "Light Drizzle", 53: "Drizzle",
    55: "Heavy Drizzle", 56: "Light Freezing Drizzle", 57: "Freezing Drizzle",
    61: "Light Rain", 63: "Rain", 65: "Heavy Rain", 66: "Light Freezing Rain",
    67: "Freezing Rain", 71: "Light Snow", 73: "Snow", 75: "Heavy Snow",
    77: "Snow Grains", 80: "Light Showers", 81: "Showers", 82: "Heavy Showers",
    85: "Light Snow Showers", 86: "Snow Showers", 95: "Thunderstorms",
    96: "Thunderstorms With Hail", 99: "Severe Thunderstorms With Hail",
}

GRID_RE = re.compile(r"^[A-Ra-r]{2}[0-9]{2}(?:[A-Xa-x]{2}(?:[0-9]{2})?)?$")
LATLON_RE = re.compile(r"^([+-]?\d{1,3}(?:\.\d+)?)\s*[,\s]\s*([+-]?\d{1,3}(?:\.\d+)?)$")
ZIP_RE = re.compile(r"^(\d{5})(?:-\d{4})?$")     # 77002 or ZIP+4 77002-1234
IDENT_RE = re.compile(r"^[0-9a-f]{32,64}$")
HASH_RE = re.compile(r"^[0-9a-f]{32}$")            # identity hash / LXMF address
TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{8,700}$")
STATION_RE = re.compile(r"^[A-Za-z0-9]{3,10}$")
VAR_SAFE_RE = re.compile(r"[^A-Za-z0-9_.,\-]")
CTRL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")

METRIC = UNITS.lower().startswith("m")


# ============================== basics =================================

def debug(msg):
    if DEBUG:
        print(f"[debug] {msg}", file=sys.stderr)


def warn(msg):
    print(f"[reticast] {msg}", file=sys.stderr)


class FetchError(Exception):
    """Any failure to get usable data from a web API."""


class NotFound(FetchError):
    """The API answered 404 (e.g. NWS has no data for a point outside the US)."""


def http_json(url, accept="application/geo+json"):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": accept})
    start = time.time()
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as e:           # must come before URLError
        debug(f"{time.time() - start:5.2f}s  HTTP {e.code}  {url}")
        if e.code in (400, 404):
            raise NotFound(f"HTTP 404 for {url}") from None
        raise FetchError(f"HTTP {e.code} for {url}") from None
    except (urllib.error.URLError, OSError, http.client.HTTPException, ValueError) as e:
        debug(f"{time.time() - start:5.2f}s  FAILED {type(e).__name__}  {url}")
        raise FetchError(f"{type(e).__name__} for {url}") from None
    debug(f"{time.time() - start:5.2f}s  {url}")
    try:
        return json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise FetchError(f"bad JSON from {url}") from None


def ensure_dirs():
    # makedirs only applies mode to the last folder, so create both explicitly
    os.makedirs(DATA_DIR, mode=0o700, exist_ok=True)
    os.makedirs(CACHE_DIR, mode=0o700, exist_ok=True)


def read_json(path):
    """Parsed JSON, or None if the file is missing or unreadable."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError, UnicodeDecodeError):
        return None


def write_json_atomic(path, data):
    """Write via a temp file + rename, so readers never see a half-written file."""
    folder = os.path.dirname(path)
    os.makedirs(folder, mode=0o700, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=folder)   # created 0600
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, separators=(",", ":"), ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def cache_put(path, data):
    """Save to the cache; a failure here must never break a page."""
    try:
        write_json_atomic(path, data)
    except OSError as e:
        warn(f"WARNING: cannot write {path}: {e}")


def num(v):
    """v as a float, or None if it isn't a real finite number."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    v = float(v)
    return v if math.isfinite(v) else None


# ============================== text / Micron ==========================

def esc(text):
    """Make any text safe to print inside a Micron line."""
    s = CTRL_RE.sub("", "" if text is None else str(text))
    return s.replace("\\", "/").replace("`", "\\`")


def line(text):
    """One Micron line of plain text that can't be mistaken for markup."""
    s = esc(text).replace("\r", " ").replace("\n", " ").replace("\t", " ")
    # At the start of a line these characters mean heading/divider/comment.
    return " " + s if s[:1] in (">", "-", "<", "#") else s


def block(text):
    """Multi-line plain text as a list of safe Micron lines."""
    return [line(ln.rstrip()) for ln in ("" if text is None else str(text)).split("\n")]


def label(text):
    """Text safe to use as a link label."""
    s = CTRL_RE.sub("", "" if text is None else str(text))
    s = re.sub(r"[`\[\]\\|\r\n\t]", " ", s)
    s = " ".join(s.split())
    return s or "?"


def field_default(text):
    """Text safe to use as an input field's pre-filled value."""
    s = CTRL_RE.sub("", "" if text is None else str(text))
    s = re.sub(r"[`<>|\\\r\n\t]", " ", s)
    return " ".join(s.split())[:80]


def mlink(text, fields=(), **variables):
    """A Micron link back to this page, carrying input fields and variables."""
    parts = list(fields)
    for key, value in variables.items():
        parts.append(f"{key}={VAR_SAFE_RE.sub('', str(value))}")
    spec = "|".join(parts)
    if spec:
        return f"`[{label(text)}`:{PAGE_PATH}`{spec}]"
    return f"`[{label(text)}`:{PAGE_PATH}]"


def clean_name(text, limit=80):
    s = CTRL_RE.sub("", "" if text is None else str(text))
    s = re.sub(r"[`\[\]\\|<>\r\n\t]", " ", s)
    return " ".join(s.split())[:limit].strip()


def trim(text, limit):
    """Unwrap NWS hard-wrapped text, keep paragraph breaks, trim to `limit` chars.

    Returns (text, was_trimmed).
    """
    paras = [" ".join(p.split()) for p in re.split(r"\n\s*\n", text or "")]
    paras = [p for p in paras if p]
    out, used = [], 0
    for p in paras:
        if limit and used + len(p) > limit:
            room = limit - used
            out.append(p[:room].rsplit(" ", 1)[0] + " ..." if room > 40 else "...")
            return "\n".join(out), True
        out.append(p)
        used += len(p)
    return "\n".join(out), False


PRODUCT_CODE_RE = re.compile(r"^[A-Z]{3}[A-Z0-9]{3,6}$")    # e.g. AQAHGX


def alert_description(text):
    """NWS alert description without the leading product code line (e.g. 'AQAHGX')."""
    lines = (text or "").strip("\n").split("\n")
    if lines and PRODUCT_CODE_RE.match(lines[0].strip()):
        lines = lines[1:]
    return "\n".join(lines)


# ============================== time / units ===========================

def get_tz(name):
    if name and ZoneInfo is not None:
        try:
            return ZoneInfo(name)
        except Exception:
            pass
    return timezone.utc


def parse_time(s):
    """Aware datetime from an ISO string, or None."""
    if not isinstance(s, str) or not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def fmt_local(dt, fmt, tz):
    return dt.astimezone(tz).strftime(fmt).lstrip("0")


def fmt_epoch(ts, fmt, tz):
    return fmt_local(datetime.fromtimestamp(ts, timezone.utc), fmt, tz)


def temp(c):
    if c is None:
        return "N/A"
    return f"{round(c)}C" if METRIC else f"{round(c * 9 / 5 + 32)}F"


def fc_temp(value, unit):
    """A forecast temperature given in F or C, shown in the configured units."""
    v = num(value)
    if v is None:
        return ""
    unit = (unit or "F").upper()
    c = v if unit == "C" else (v - 32) * 5 / 9
    return temp(c)


def speed(kmh):
    if kmh is None:
        return None
    return f"{round(kmh)} km/h" if METRIC else f"{round(kmh / 1.609344)} mph"


def pressure(hpa):
    if hpa is None:
        return "N/A"
    return f"{hpa:.0f} hPa" if METRIC else f"{hpa * 0.0295300:.2f} inHg"


def distance(km):
    if km is None:
        return "N/A"
    return f"{km:.0f} km" if METRIC else f"{km / 1.609344:.0f} mi"


def compass(deg):
    return None if deg is None else COMPASS[round(deg / 22.5) % 16]


def wind_text(cur):
    kmh = cur.get("wind_kmh")
    if kmh is None:
        return "N/A"
    if round(kmh) == 0:
        return "Calm"
    text = " ".join(x for x in (compass(cur.get("wind_dir")), speed(kmh)) if x)
    if cur.get("gust_kmh"):
        text += f", gusts {speed(cur['gust_kmh'])}"
    return text


# ============================== locations ==============================
# A location is {"name", "lat", "lon", "cc", "grid"}. lat/lon are rounded to
# 4 decimals (about 11 m), which is also what the NWS API expects.

def make_loc(name, lat, lon, cc="", grid=""):
    lat, lon = float(lat), float(lon)          # ValueError/TypeError on junk
    if not (math.isfinite(lat) and math.isfinite(lon)
            and -90 <= lat <= 90 and -180 <= lon <= 180):
        raise ValueError("latitude/longitude out of range")
    lat = round(lat, 4) + 0.0                  # + 0.0 turns -0.0 into 0.0
    lon = round(lon, 4) + 0.0
    cc = cc.upper() if isinstance(cc, str) and re.fullmatch(r"[A-Za-z]{2}", cc) else ""
    grid = grid if isinstance(grid, str) and GRID_RE.match(grid) else ""
    return {"name": clean_name(name) or f"{lat:.4f}, {lon:.4f}",
            "lat": lat, "lon": lon, "cc": cc, "grid": grid}


def loc_from_dict(d):
    if not isinstance(d, dict):
        return None
    try:
        return make_loc(d.get("name", ""), d["lat"], d["lon"], d.get("cc", ""), d.get("grid", ""))
    except (KeyError, TypeError, ValueError):
        return None


def loc_key(loc):
    """Stable id for a location, also used in cache file names."""
    return f"{loc['lat']:.4f}_{loc['lon']:.4f}"


def same_place(a, b):
    return (a is not None and b is not None
            and abs(a["lat"] - b["lat"]) < 0.0015 and abs(a["lon"] - b["lon"]) < 0.0015)


def loc_token(loc):
    """Pack a location into one link-safe variable value."""
    d = {"n": loc["name"], "a": loc["lat"], "o": loc["lon"]}
    if loc.get("cc"):
        d["c"] = loc["cc"]
    if loc.get("grid"):
        d["g"] = loc["grid"]
    raw = json.dumps(d, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def loc_from_token(tok):
    """Unpack loc_token() output. Anything malformed or out of range gives None."""
    if not isinstance(tok, str) or not TOKEN_RE.match(tok):
        return None
    try:
        raw = base64.urlsafe_b64decode(tok + "=" * (-len(tok) % 4))
        d = json.loads(raw.decode("utf-8"))
        if not isinstance(d, dict):
            return None
        return make_loc(d.get("n", ""), d["a"], d["o"], d.get("c", ""), d.get("g", ""))
    except (KeyError, TypeError, ValueError, UnicodeDecodeError):
        return None


def normalize_grid(grid):
    g = grid.strip()
    if not GRID_RE.match(g):
        raise ValueError(f"Invalid grid square: {grid!r}")
    return g[:2].upper() + g[2:4] + g[4:6].lower() + g[6:8]


def grid_to_latlon(grid):
    """Return (lat, lon) of the CENTER of a Maidenhead grid square."""
    g = normalize_grid(grid)
    lon = (ord(g[0]) - 65) * 20 - 180
    lat = (ord(g[1]) - 65) * 10 - 90
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


# ============================== place search ===========================

def split_city_state(query):
    """'Houston TX' / 'Kansas City Missouri' -> ('Houston', 'TX') etc., else (query, None).

    Only used when there's no comma. The state is taken from the end of the
    text, and only if a city name is left over, so 'New York' or 'Washington'
    alone are still searched as place names.
    """
    words = query.split()
    for n in (2, 1):                           # two-word states first ("New Mexico")
        if len(words) <= n:
            continue
        tail = " ".join(words[-n:])
        if tail.upper() in STATE_NAMES and n == 1 and len(tail) == 2:
            return " ".join(words[:-n]), tail.upper()
        abbr = STATE_ABBR.get(tail.lower())
        if abbr:
            return " ".join(words[:-n]), abbr
    return query, None


def _om_place_name(r):
    parts = [r.get("name")]
    admin1 = r.get("admin1")
    if admin1 and admin1 != r.get("name"):
        parts.append(admin1)
    parts.append(r.get("country_code") or r.get("country"))
    return ", ".join(p for p in parts if isinstance(p, str) and p)


def _om_matches(r, quals):
    """Does a geocoder result match every qualifier, e.g. ["tx"] for "Paris, TX"?"""
    hay = [r.get("admin1"), r.get("admin2"), r.get("country"), r.get("country_code")]
    hay = [h.lower() for h in hay if isinstance(h, str) and h]
    if str(r.get("country_code", "")).upper() == "US":
        abbr = STATE_ABBR.get(str(r.get("admin1", "")).lower())
        if abbr:
            hay.append(abbr.lower())
        hay += ["usa", "united states"]
    return all(any(h == q or h.startswith(q) for h in hay) for q in quals)


def _geocode_om_plain(query):
    url = OM_GEOCODE + "?" + urllib.parse.urlencode(
        {"name": query, "count": SEARCH_RESULTS, "language": "en", "format": "json"})
    data = http_json(url, accept="application/json")
    rows = data.get("results") if isinstance(data, dict) else None
    return _rows_to_locs([r for r in (rows or []) if isinstance(r, dict)])


def _is_us_state(q):
    return q.upper() in STATE_NAMES or q.lower() in STATE_ABBR


def _geocode_om(query):
    if "," in query:
        parts = [p.strip() for p in query.split(",")]
        name = parts[0]
        quals = [p.lower() for p in parts[1:] if p]
    else:
        name, state = split_city_state(query)
        quals = [state.lower()] if state else []
    if len(name) < 2:
        return []
    params = {"name": name, "count": 20 if quals else SEARCH_RESULTS,
              "language": "en", "format": "json"}
    if quals and _is_us_state(quals[0]):
        params["countryCode"] = "US"          # keep US towns from being crowded out
    url = OM_GEOCODE + "?" + urllib.parse.urlencode(params)
    data = http_json(url, accept="application/json")
    rows = data.get("results") if isinstance(data, dict) else None
    rows = [r for r in (rows or []) if isinstance(r, dict)]
    if not rows and "," not in query and name != query:
        # The last word looked like a state but nothing matched (e.g. a foreign
        # name ending in "LA"): search the whole text as a plain place name.
        return _geocode_om_plain(query)
    if quals:
        filtered = [r for r in rows if _om_matches(r, quals)]
        rows = filtered or rows       # nothing matched: show the unfiltered hits
    return _rows_to_locs(rows)


def _rows_to_locs(rows):
    names = [_om_place_name(r) for r in rows]
    out = []
    for r, name in zip(rows, names):
        county = r.get("admin2")
        if names.count(name) > 1 and isinstance(county, str) and county:
            head, _, rest = name.partition(", ")
            name = f"{head} ({county}), {rest}" if rest else f"{head} ({county})"
        try:
            out.append(make_loc(name, r.get("latitude"), r.get("longitude"),
                                r.get("country_code") or ""))
        except (TypeError, ValueError):
            continue
    return out


def _geocode_zip(zipcode):
    try:
        data = http_json(ZIP_API + zipcode, accept="application/json")
    except NotFound:
        return []
    out = []
    for p in (data.get("places") or []) if isinstance(data, dict) else []:
        if not isinstance(p, dict):
            continue
        name = f"{p.get('place name', '')}, {p.get('state abbreviation', '')} {zipcode}"
        try:
            out.append(make_loc(name, p.get("latitude"), p.get("longitude"), "US"))
        except (TypeError, ValueError):
            continue
    return out


def _dedupe(locs):
    out = []
    for loc in locs:
        if not any(same_place(loc, o) for o in out):
            out.append(loc)
    return out


def search_locations(query):
    """Return (list of locations, message or None). Never raises."""
    q = " ".join(CTRL_RE.sub("", str(query or "")).split())[:80]
    if len(q) < 2:
        return [], "Type at least 2 characters to search."

    m = LATLON_RE.match(q)
    if m:
        try:
            return [make_loc("", m.group(1), m.group(2))], None
        except ValueError:
            return [], "Latitude must be -90 to 90, and longitude -180 to 180."

    if GRID_RE.match(q):
        g = normalize_grid(q)
        lat, lon = grid_to_latlon(g)
        return [make_loc(f"Grid {g}", lat, lon, grid=g)], None

    ensure_dirs()
    key = hashlib.sha1(q.lower().encode("utf-8")).hexdigest()
    path = os.path.join(CACHE_DIR, f"geo_{key}.json")
    cached = read_json(path)
    if isinstance(cached, dict) and time.time() - (num(cached.get("ts")) or 0) < GEO_DAYS * 86400:
        locs = [x for x in (loc_from_dict(d) for d in cached.get("results") or []) if x]
    else:
        try:
            zm = ZIP_RE.match(q)
            locs = []
            if zm:
                try:
                    locs = _geocode_zip(zm.group(1))
                except FetchError as e:        # ZIP service down: try the geocoder
                    debug(f"ZIP lookup failed: {e}")
            if not locs:
                locs = _geocode_om(zm.group(1) if zm else q)
        except FetchError:
            return [], "Place search isn't reachable right now. Try again in a minute."
        except Exception as e:     # a malformed answer must not break the page
            warn(f"search error for {q!r}: {type(e).__name__}: {e}")
            return [], "Place search had a problem. Try again in a minute."
        locs = _dedupe(locs)[:SEARCH_RESULTS]
        cache_put(path, {"ts": time.time(), "results": locs})
    if not locs:
        return [], f"No places found for \"{q}\". Try adding a state or country, e.g. \"Paris, TX\"."
    return locs, None


# ============================== NWS ====================================

def county_label(name, state_abbr):
    if not name:
        return ""
    suffix = {"LA": "Parish", "AK": "Borough"}.get(state_abbr, "County")
    if name.lower().endswith(("county", "parish", "borough", "census area", "city")):
        return name
    return f"{name} {suffix}"


def _props(data):
    p = data.get("properties") if isinstance(data, dict) else None
    return p if isinstance(p, dict) else {}


def fetch_nws_stations(url):
    data = http_json(url)
    stations = []
    for f in (data.get("features") or []) if isinstance(data, dict) else []:
        p = _props(f)
        sid = p.get("stationIdentifier")
        if isinstance(sid, str) and STATION_RE.match(sid):
            stations.append([sid, clean_name(p.get("name"), 60)])
        if len(stations) >= MAX_STATIONS:
            break
    return stations


def fetch_meta(loc):
    """Which weather source covers this place, plus NWS details that never change."""
    now = time.time()
    if loc["cc"] and loc["cc"] not in NWS_COUNTRIES:
        return {"provider": "om", "ts": now}
    try:
        pts = _props(http_json(f"{NWS_API}/points/{loc['lat']:.4f},{loc['lon']:.4f}"))
    except NotFound:                   # outside NWS coverage
        return {"provider": "om", "ts": now}
    forecast = pts.get("forecast")
    stations_url = pts.get("observationStations")
    rel = _props(pts.get("relativeLocation"))
    meta = {
        "provider": "nws", "ts": now, "complete": True,
        "tz": pts.get("timeZone") if isinstance(pts.get("timeZone"), str) else "",
        "forecast": forecast if isinstance(forecast, str) and forecast.startswith(NWS_API + "/") else "",
        "near": clean_name(", ".join(x for x in (rel.get("city"), rel.get("state"))
                                      if isinstance(x, str) and x), 60),
        "county": "", "state": "", "stations": [],
    }
    state_abbr = rel.get("state") if isinstance(rel.get("state"), str) else ""
    county_url = pts.get("county")

    with ThreadPoolExecutor(max_workers=2) as pool:
        st_job = (pool.submit(fetch_nws_stations, stations_url)
                  if isinstance(stations_url, str) and stations_url.startswith(NWS_API + "/") else None)
        co_job = (pool.submit(http_json, county_url)
                  if isinstance(county_url, str) and county_url.startswith(NWS_API + "/") else None)
        if st_job:
            try:
                meta["stations"] = st_job.result()
            except Exception as e:
                debug(f"stations lookup failed: {e}")
                meta["complete"] = False
        if co_job:
            try:
                cz = _props(co_job.result())
                if isinstance(cz.get("state"), str) and cz["state"]:
                    state_abbr = cz["state"]
                meta["county"] = clean_name(county_label(cz.get("name")
                                            if isinstance(cz.get("name"), str) else "", state_abbr), 60)
            except Exception as e:
                debug(f"county lookup failed: {e}")
                meta["complete"] = False
    meta["state"] = STATE_NAMES.get(state_abbr, state_abbr)
    return meta


def get_meta(loc):
    path = os.path.join(CACHE_DIR, f"meta_{loc_key(loc)}.json")
    old = read_json(path)
    if not (isinstance(old, dict) and old.get("provider") in ("nws", "om")):
        old = None
    if old:
        age = time.time() - (num(old.get("ts")) or 0)
        max_age = META_DAYS * 86400 if old.get("provider") == "om" or old.get("complete") else 3600
        if age < max_age:
            return old
    try:
        meta = fetch_meta(loc)
    except FetchError:
        if old:
            return old       # stale but still correct enough
        raise
    cache_put(path, meta)
    return meta


def _qv(obj):
    """(value, unitCode) from an NWS quantity like {"value": 21.5, "unitCode": "wmoUnit:degC"}."""
    if not isinstance(obj, dict):
        return None, ""
    unit = obj.get("unitCode") if isinstance(obj.get("unitCode"), str) else ""
    return num(obj.get("value")), unit


def _c(obj):
    v, u = _qv(obj)
    if v is None:
        return None
    return (v - 32) * 5 / 9 if u.endswith("degF") else v


def _kmh(obj):
    v, u = _qv(obj)
    if v is None:
        return None
    if "m_s-1" in u:
        return v * 3.6
    if "mi_h-1" in u:
        return v * 1.609344
    if "kn" in u:
        return v * 1.852
    return v


def _hpa(obj):
    v, u = _qv(obj)
    if v is None:
        return None
    return v if "hPa" in u else v / 100


def _km(obj):
    v, u = _qv(obj)
    if v is None:
        return None
    return v if u.endswith(":km") else v / 1000


def _pct(obj):
    return _qv(obj)[0]


def fetch_nws_current(meta):
    stations = meta.get("stations") or []
    fallback = None
    for sid, sname in stations[:MAX_STATIONS]:
        try:
            p = _props(http_json(f"{NWS_API}/stations/{sid}/observations/latest"))
        except FetchError:
            continue
        heat, chill = _c(p.get("heatIndex")), _c(p.get("windChill"))
        cur = {
            "station": sid, "station_name": sname,
            "obs_time": p.get("timestamp") if isinstance(p.get("timestamp"), str) else "",
            "desc": clean_name(p.get("textDescription"), 60),
            "temp_c": _c(p.get("temperature")),
            "feels_c": heat if heat is not None else chill,
            "dew_c": _c(p.get("dewpoint")),
            "rh": _pct(p.get("relativeHumidity")),
            "wind_dir": _qv(p.get("windDirection"))[0],
            "wind_kmh": _kmh(p.get("windSpeed")),
            "gust_kmh": _kmh(p.get("windGust")),
            "pres_hpa": _hpa(p.get("barometricPressure")) or _hpa(p.get("seaLevelPressure")),
            "vis_km": _km(p.get("visibility")),
        }
        if cur["temp_c"] is None:
            fallback = fallback or cur
            continue
        obs = parse_time(cur["obs_time"])
        if obs and (datetime.now(timezone.utc) - obs) > timedelta(hours=3):
            fallback = fallback or cur        # usable, but try a fresher station
            continue
        return cur
    if fallback:
        return fallback
    raise FetchError("no usable station observation")


def fetch_nws_alerts(loc):
    data = http_json(f"{NWS_API}/alerts/active?point={loc['lat']:.4f},{loc['lon']:.4f}")
    alerts = []
    for f in (data.get("features") or []) if isinstance(data, dict) else []:
        p = _props(f)
        if p.get("status") != "Actual" or p.get("messageType") == "Cancel":
            continue
        event = p.get("event")
        if not isinstance(event, str) or not event:
            continue
        s = lambda k: p.get(k) if isinstance(p.get(k), str) else ""
        fid = f.get("id") if isinstance(f, dict) and isinstance(f.get("id"), str) else ""
        refs = []
        for r in p.get("references") or []:
            if isinstance(r, dict) and isinstance(r.get("identifier"), str):
                refs.append(r["identifier"])
        alerts.append({"id": s("id") or fid, "event": event, "severity": s("severity"),
                       "headline": s("headline"), "description": s("description"),
                       "instruction": s("instruction"), "ends": s("ends") or s("expires"),
                       "area": s("areaDesc"), "sender": s("senderName"),
                       "type": s("messageType"), "refs": refs[:20]})
        if len(alerts) >= 20:
            break
    return alerts


def fetch_nws_forecast(meta):
    url = meta.get("forecast")
    if not url:
        return []
    url += ("&" if "?" in url else "?") + ("units=si" if METRIC else "units=us")
    props = _props(http_json(url))
    out = []
    for p in (props.get("periods") or []):
        if not isinstance(p, dict):
            continue
        pop = _pct(p.get("probabilityOfPrecipitation"))
        out.append({"kind": "period",
                    "name": clean_name(p.get("name"), 30),
                    "is_day": bool(p.get("isDaytime")),
                    "temp": num(p.get("temperature")),
                    "unit": p.get("temperatureUnit") if p.get("temperatureUnit") in ("F", "C") else "F",
                    "pop": round(pop) if pop is not None else None,
                    "short": clean_name(p.get("shortForecast"), 160),
                    "detail": p.get("detailedForecast") if isinstance(p.get("detailedForecast"), str) else ""})
    return out[:14]


# ============================== Open-Meteo =============================

def fetch_om(loc):
    params = {
        "latitude": f"{loc['lat']:.4f}", "longitude": f"{loc['lon']:.4f}",
        "current": "temperature_2m,relative_humidity_2m,apparent_temperature,weather_code,"
                   "wind_speed_10m,wind_direction_10m,wind_gusts_10m,pressure_msl",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max",
        "timezone": "auto", "forecast_days": 7,
    }
    data = http_json(OM_FORECAST + "?" + urllib.parse.urlencode(params), accept="application/json")
    if not isinstance(data, dict):
        raise FetchError("unexpected Open-Meteo answer")
    offset = num(data.get("utc_offset_seconds")) or 0
    tz = data.get("timezone") if isinstance(data.get("timezone"), str) else ""

    c = data.get("current") if isinstance(data.get("current"), dict) else {}
    obs_time = ""
    t = c.get("time")
    if isinstance(t, str):
        with contextlib.suppress(ValueError):
            local = datetime.fromisoformat(t)
            if local.tzinfo is None:
                local = local.replace(tzinfo=timezone(timedelta(seconds=offset)))
            obs_time = local.astimezone(timezone.utc).isoformat()
    code = num(c.get("weather_code"))
    cur = {
        "station": "", "station_name": "", "obs_time": obs_time,
        "desc": WMO.get(int(code), "") if code is not None else "",
        "temp_c": num(c.get("temperature_2m")),
        "feels_c": num(c.get("apparent_temperature")),
        "dew_c": None,
        "rh": num(c.get("relative_humidity_2m")),
        "wind_dir": num(c.get("wind_direction_10m")),
        "wind_kmh": num(c.get("wind_speed_10m")),
        "gust_kmh": num(c.get("wind_gusts_10m")),
        "pres_hpa": num(c.get("pressure_msl")),
        "vis_km": None,
    }
    if cur["temp_c"] is None:
        raise FetchError("Open-Meteo returned no current temperature")

    d = data.get("daily") if isinstance(data.get("daily"), dict) else {}
    cols = [d.get(k) if isinstance(d.get(k), list) else [] for k in
            ("time", "weather_code", "temperature_2m_max", "temperature_2m_min",
             "precipitation_probability_max")]
    days = []
    for i, day in enumerate(cols[0]):
        get = lambda col: num(col[i]) if i < len(col) else None
        try:
            name = datetime.strptime(str(day), "%Y-%m-%d").strftime("%A %m/%d")
        except ValueError:
            continue
        code = get(cols[1])
        pop = get(cols[4])
        days.append({"kind": "day", "name": "Today" if i == 0 else name,
                     "hi_c": get(cols[2]), "lo_c": get(cols[3]),
                     "short": WMO.get(int(code), "") if code is not None else "",
                     "pop": round(pop) if pop is not None else None})
    return tz, cur, days


# ============================== weather cache ==========================

def _empty_wx(provider):
    return {"v": WX_VERSION, "provider": provider, "units": "metric" if METRIC else "us",
            "tz": "", "near": "", "county": "", "state": "",
            "cur": None, "cur_ts": 0, "alerts": [], "alerts_ts": 0,
            "fc": [], "fc_ts": 0, "try_ts": 0}


def _valid_wx(wx):
    return (isinstance(wx, dict) and wx.get("v") == WX_VERSION
            and wx.get("provider") in ("nws", "om"))


def _stale_parts(wx, now):
    """Which sections of cached weather need refreshing."""
    if not wx:
        return {"cur", "alerts", "fc"}
    need = set()
    if now - (num(wx.get("cur_ts")) or 0) >= CACHE_MINUTES * 60:
        need.add("cur")
    if wx.get("provider") == "nws":
        if now - (num(wx.get("alerts_ts")) or 0) >= CACHE_MINUTES * 60:
            need.add("alerts")
        fc_units = "metric" if METRIC else "us"
        if (now - (num(wx.get("fc_ts")) or 0) >= FORECAST_CACHE_MINUTES * 60
                or wx.get("units") != fc_units):
            need.add("fc")
    elif now - (num(wx.get("fc_ts")) or 0) >= CACHE_MINUTES * 60:
        need.add("fc")
    return need


def _refresh_nws(loc, meta, wx, need, now):
    wx.update({"tz": meta.get("tz") or "", "near": meta.get("near") or "",
               "county": meta.get("county") or "", "state": meta.get("state") or ""})
    got = False
    with ThreadPoolExecutor(max_workers=3) as pool:
        jobs = {}
        if "cur" in need:
            jobs["cur"] = pool.submit(fetch_nws_current, meta)
        if "alerts" in need:
            jobs["alerts"] = pool.submit(fetch_nws_alerts, loc)
        if "fc" in need:
            if meta.get("forecast"):
                jobs["fc"] = pool.submit(fetch_nws_forecast, meta)
            else:
                wx["fc"], wx["fc_ts"] = [], now
                wx["units"] = "metric" if METRIC else "us"
        for part, job in jobs.items():
            try:
                result = job.result()
            except Exception as e:           # keep the previous data for this part
                debug(f"{part} update failed for {loc['name']}: {type(e).__name__}: {e}")
                continue
            wx[part] = result
            wx[part + "_ts"] = now
            if part == "fc":
                wx["units"] = "metric" if METRIC else "us"
            got = True
    return got


def _refresh_om(loc, wx, now):
    try:
        tz, cur, days = fetch_om(loc)
    except Exception as e:
        debug(f"Open-Meteo update failed for {loc['name']}: {type(e).__name__}: {e}")
        return False
    wx.update({"tz": tz, "cur": cur, "cur_ts": now, "fc": days, "fc_ts": now,
               "alerts": [], "alerts_ts": now})
    return True


def get_weather(loc, fetch="stale"):
    """Weather for a location from the cache, refreshed as needed.

    fetch="stale"   refresh whatever is out of date (page views, cron)
    fetch="missing" only go to the network if nothing is cached (home page line)
    Returns the cached dict (possibly old, possibly with cur=None), or None.
    Never raises for network problems.
    """
    ensure_dirs()
    path = os.path.join(CACHE_DIR, f"wx_{loc_key(loc)}.json")
    wx = read_json(path)
    wx = wx if _valid_wx(wx) else None
    now = time.time()

    if wx and fetch == "missing" and wx.get("cur"):
        return wx
    need = _stale_parts(wx, now)
    if not need:
        return wx
    if wx and now - (num(wx.get("try_ts")) or 0) < RETRY_MINUTES * 60:
        return wx                        # an update just failed; don't hammer the API

    try:
        meta = get_meta(loc)
    except FetchError as e:
        debug(f"location lookup failed for {loc['name']}: {e}")
        meta = None
    except Exception as e:
        warn(f"location lookup error for {loc['name']}: {type(e).__name__}: {e}")
        meta = None

    if meta is None:
        new = dict(wx) if wx else _empty_wx("nws")
        new["try_ts"] = now
        cache_put(path, new)
        return new

    provider = meta["provider"]
    if wx and wx.get("provider") == provider:
        new = dict(wx)
    else:
        new = _empty_wx(provider)
        need = {"cur", "alerts", "fc"}
    new["try_ts"] = now

    if provider == "nws":
        _refresh_nws(loc, meta, new, need, now)
    else:
        _refresh_om(loc, new, now)
    cache_put(path, new)
    return new


def safe_get_weather(loc, fetch="stale"):
    try:
        return get_weather(loc, fetch)
    except Exception as e:
        warn(f"weather error for {loc.get('name')}: {type(e).__name__}: {e}")
        debug(traceback.format_exc())
        return None


def weather_many(locs, fetch="stale"):
    """get_weather for several places at once (in parallel). Same order as locs."""
    if not locs:
        return []
    with ThreadPoolExecutor(max_workers=min(6, len(locs))) as pool:
        return list(pool.map(lambda l: safe_get_weather(l, fetch), locs))


# ============================== server default =========================

def server_default_loc():
    """The node's configured default location. Raises LookupError if it can't be found."""
    spec = DEFAULT_LOCATION.strip()
    saved = read_json(SERVER_DEFAULT_FILE)
    if (isinstance(saved, dict) and saved.get("spec") == spec
            and saved.get("name") == DEFAULT_LOCATION_NAME):
        loc = loc_from_dict(saved.get("loc"))
        if loc:
            return loc
    results, msg = search_locations(spec)
    if not results:
        raise LookupError(msg or f"DEFAULT_LOCATION {spec!r} not found")
    loc = results[0]
    if DEFAULT_LOCATION_NAME:
        loc["name"] = clean_name(DEFAULT_LOCATION_NAME) or loc["name"]
    cache_put(SERVER_DEFAULT_FILE, {"spec": spec, "name": DEFAULT_LOCATION_NAME, "loc": loc})
    return loc


# ============================== visitors ===============================
# users.json maps an identity hash to:
#   {"default": loc or None, "favorites": [loc, ...], "view": "default"|"overview"}

def new_notify():
    return {"on": False, "level": "watch", "severe": False, "addr": "",
            "pending_addr": "", "code": "", "code_ts": 0, "tries": 0, "test_ts": 0}


def new_profile():
    return {"default": None, "favorites": [], "view": "default", "notify": new_notify()}


def normalize_notify(n):
    out = new_notify()
    if not isinstance(n, dict):
        return out
    out["on"] = n.get("on") is True
    out["level"] = n.get("level") if n.get("level") in NOTIFY_LEVELS else "watch"
    out["severe"] = n.get("severe") is True
    for k in ("addr", "pending_addr"):
        v = n.get(k)
        out[k] = v if isinstance(v, str) and HASH_RE.match(v) else ""
    code = n.get("code")
    out["code"] = code if isinstance(code, str) and re.fullmatch(r"\d{6}", code) else ""
    for k in ("code_ts", "tries", "test_ts"):
        out[k] = int(num(n.get(k)) or 0)
    return out


def normalize_profile(p):
    """A clean profile from whatever is stored, or None if nothing usable."""
    if not isinstance(p, dict):
        return None
    out = new_profile()
    out["default"] = loc_from_dict(p.get("default"))
    favs = p.get("favorites") if isinstance(p.get("favorites"), list) else []
    for f in favs:
        loc = loc_from_dict(f)
        if (loc and not same_place(loc, out["default"])
                and not any(same_place(loc, x) for x in out["favorites"])):
            out["favorites"].append(loc)
    out["favorites"] = out["favorites"][:MAX_FAVORITES]
    out["view"] = "overview" if p.get("view") == "overview" else "default"
    out["notify"] = normalize_notify(p.get("notify"))
    out["updated"] = int(num(p.get("updated")) or 0)
    return out


def get_identity():
    """The visitor's identity hash if they identified to the node, else None."""
    ident = os.environ.get("remote_identity", "").strip().lower()
    return ident if IDENT_RE.match(ident) else None


def load_users():
    data = read_json(USERS_FILE)
    return data if isinstance(data, dict) else {}


def get_profile(ident):
    if not ident:
        return None
    return normalize_profile(load_users().get(ident))


@contextlib.contextmanager
def _users_lock():
    ensure_dirs()
    fd = os.open(USERS_LOCK, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def _load_users_for_update():
    """Like load_users(), but a damaged file is set aside instead of silently overwritten."""
    if not os.path.exists(USERS_FILE):
        return {}
    data = read_json(USERS_FILE)
    if isinstance(data, dict):
        return data
    bad = f"{USERS_FILE}.damaged-{int(time.time())}"
    with contextlib.suppress(OSError):
        os.replace(USERS_FILE, bad)
        warn(f"users.json was unreadable; moved it to {bad} and started a new one")
    return {}


def update_profile(ident, change):
    """Apply change(profile) -> (changed, message) under a lock. Returns (changed, message)."""
    try:
        return _update_profile(ident, change)
    except OSError as e:
        warn(f"cannot save visitor data: {e}")
        return False, "Sorry, your change couldn't be saved (storage error on this node)."


def _update_profile(ident, change):
    with _users_lock():
        users = _load_users_for_update()
        prof = normalize_profile(users.get(ident)) or new_profile()
        changed, msg = change(prof)
        if not changed:
            return False, msg
        empty = (not prof["default"] and not prof["favorites"] and prof["view"] == "default"
                 and not prof["notify"]["on"] and not prof["notify"]["addr"])
        if ident not in users and not empty and len(users) >= MAX_USERS:
            return False, "Sorry, this node can't save places for any more visitors."
        if empty:
            users.pop(ident, None)
        else:
            prof["updated"] = int(time.time())
            users[ident] = prof
        write_json_atomic(USERS_FILE, users)
        return True, msg


def _find(locs, loc):
    for i, x in enumerate(locs):
        if same_place(x, loc):
            return i
    return None


def act_set_default(loc):
    def change(p):
        old = p["default"]
        if same_place(old, loc):
            return False, f"{loc['name']} is already your default location."
        i = _find(p["favorites"], loc)
        if i is not None:                       # promote a favorite: swap places
            p["favorites"].pop(i)
            if old:
                p["favorites"].insert(i, old)
            p["default"] = loc
            note = f" {old['name']} moved to your favorites." if old else ""
            return True, f"{loc['name']} is now your default location.{note}"
        p["default"] = loc
        if old and len(p["favorites"]) < MAX_FAVORITES:
            p["favorites"].append(old)
            return True, (f"{loc['name']} is now your default location. "
                          f"{old['name']} moved to your favorites.")
        if old:
            return True, (f"{loc['name']} is now your default location, replacing "
                          f"{old['name']} (your favorites are full).")
        return True, f"{loc['name']} is now your default location."
    return change


def act_add_favorite(loc):
    def change(p):
        if same_place(p["default"], loc):
            return False, f"{loc['name']} is already your default location."
        if _find(p["favorites"], loc) is not None:
            return False, f"{loc['name']} is already in your favorites."
        if len(p["favorites"]) >= MAX_FAVORITES:
            return False, (f"Your favorites are full ({MAX_FAVORITES}). "
                           "Remove one in My Places first.")
        p["favorites"].append(loc)
        return True, f"Added {loc['name']} to your favorites."
    return change


def act_remove_favorite(index, key):
    def change(p):
        if 0 <= index < len(p["favorites"]) and loc_key(p["favorites"][index]) == key:
            gone = p["favorites"].pop(index)
            return True, f"Removed {gone['name']} from your favorites."
        return False, "That favorite was already removed or changed."
    return change


def act_clear_default(key):
    def change(p):
        if p["default"] and loc_key(p["default"]) == key:
            gone = p["default"]
            p["default"] = None
            return True, f"{gone['name']} is no longer your default location."
        return False, "That default was already changed."
    return change


def act_set_view(mode):
    def change(p):
        if mode not in ("default", "overview"):
            return False, "Unknown setting."
        if p["view"] == mode:
            return False, None
        p["view"] = mode
        what = "an overview of all your places" if mode == "overview" else "your default location"
        return True, f"RetiCast will now open on {what}."
    return change


# ============================== alert messages =========================

def lxmf_address(ident_hex):
    """The LXMF address ("lxmf.delivery" destination) of a Reticulum identity hash.

    Same result as RNS.Destination.hash_from_name_and_identity("lxmf.delivery", ...):
    the first 16 bytes of SHA-256(name hash + identity hash), where the name hash is
    the first 10 bytes of SHA-256("lxmf.delivery"). reticast_notify.py checks this
    against RNS itself at startup.
    """
    if not isinstance(ident_hex, str) or not HASH_RE.match(ident_hex):
        return ""
    name_hash = hashlib.sha256(b"lxmf.delivery").digest()[:10]
    return hashlib.sha256(name_hash + bytes.fromhex(ident_hex)).digest()[:16].hex()


def message_address(ident, notify):
    """Where a visitor's alert messages go: their chosen address, or their own."""
    return notify.get("addr") or lxmf_address(ident)


def alert_rank(event):
    """0 warning, 1 watch, 2 advisory (incl. '... Alert' products), 3 anything else."""
    e = (event or "").lower()
    if "warning" in e:
        return 0
    if "watch" in e:
        return 1
    if "advisory" in e or e.endswith(" alert"):
        return 2
    return 3


def alert_wanted(notify, alert):
    """Does this visitor's notification choice include this alert?"""
    if alert_rank(alert.get("event")) > NOTIFY_LEVELS.get(notify.get("level"), 1):
        return False
    if notify.get("severe") and alert.get("severity") not in ("Extreme", "Severe"):
        return False
    return True


def nws_covered(loc):
    """True/False if we know whether NWS (and so alerts) covers loc, None if unknown."""
    if not loc:
        return False
    if loc.get("cc"):
        return loc["cc"] in NWS_COUNTRIES
    meta = read_json(os.path.join(CACHE_DIR, f"meta_{loc_key(loc)}.json"))
    if isinstance(meta, dict) and meta.get("provider") in ("nws", "om"):
        return meta["provider"] == "nws"
    return None


def notifier_status():
    """(installed, running, heartbeat dict) for the notifier service."""
    hb = read_json(HEARTBEAT_FILE)
    if not isinstance(hb, dict):
        return False, False, {}
    return True, time.time() - (num(hb.get("ts")) or 0) < HEARTBEAT_STALE, hb


def notify_watch_list(users):
    """Addresses the notifier should look for: everyone who uses or is setting up messages."""
    addrs = []
    for ident, raw in (users or {}).items():
        if not isinstance(ident, str) or not HASH_RE.match(ident):
            continue
        prof = normalize_profile(raw)
        if not prof:
            continue
        n = prof["notify"]
        if not (n["on"] or n["addr"] or n["pending_addr"] or n["test_ts"]):
            continue
        for a in (lxmf_address(ident), n["addr"], n["pending_addr"]):
            if a and a not in addrs:
                addrs.append(a)
    return addrs


def address_status(running):
    """What the notifier last reported: {"addresses": {...}, "last": {...}}."""
    st = read_json(STATUS_FILE) if running else None
    if not isinstance(st, dict):
        return {"addresses": {}, "last": {}}
    return {"addresses": st.get("addresses") if isinstance(st.get("addresses"), dict) else {},
            "last": st.get("last") if isinstance(st.get("last"), dict) else {}}


def _found_text(entry, running):
    if not running:
        return "`F888Can't check right now (the alert message service isn't running)`f"
    if not isinstance(entry, dict):
        return "`F888Checking the network...`f"
    if entry.get("known"):
        return "`F8f8Found on the network`f"
    return ("`Ffa0Not found on the network yet.`f `F888RetiCast keeps looking. Opening your "
            "messaging app, so it announces itself, usually fixes this.`f")


LAST_STATE_TEXT = {
    "sending": "sending",
    "delivered": "delivered",
    "propagated": "left at the propagation node for your app to pick up",
    "waiting": "waiting until your address is found on the network",
    "failed": "couldn't be delivered",
}


def _last_text(entry, tz):
    if not isinstance(entry, dict) or entry.get("state") not in LAST_STATE_TEXT:
        return None
    when = ""
    if num(entry.get("ts")):
        when = " (" + fmt_epoch(entry["ts"], "%I:%M%p %m/%d", tz) + ")"
    title = clean_name(entry.get("title"), 60) or "message"
    return f"{title}: {LAST_STATE_TEXT[entry['state']]}{when}"


def queue_message(kind, ident, addr, **extra):
    """Hand a one-off message (test / verification) to the notifier."""
    ensure_dirs()
    os.makedirs(OUTBOX_DIR, mode=0o700, exist_ok=True)
    try:
        if len(os.listdir(OUTBOX_DIR)) >= 200:      # notifier stopped; don't pile up
            return False
    except OSError:
        return False
    item = {"kind": kind, "ident": ident, "addr": addr, "ts": time.time()}
    item.update(extra)
    path = os.path.join(OUTBOX_DIR, f"{int(time.time() * 1000)}-{os.urandom(4).hex()}.json")
    try:
        write_json_atomic(path, item)
        return True
    except OSError as e:
        warn(f"cannot queue message: {e}")
        return False


def _cooldown_left(n):
    return int(TEST_COOLDOWN_MINUTES * 60 - (time.time() - n["test_ts"]))


def act_notify_on():
    def change(p):
        if p["notify"]["on"]:
            return False, "Alert messages are already on."
        if not p["default"]:
            return False, "Choose a default location first; alerts are sent for it."
        if nws_covered(p["default"]) is False:
            return False, "Alert messages are only available for US locations."
        p["notify"]["on"] = True
        return True, ("Alert messages are on. Send yourself a test message to make sure "
                      "they reach you.")
    return change


def act_notify_off():
    def change(p):
        if not p["notify"]["on"]:
            return False, "Alert messages are already off."
        p["notify"]["on"] = False
        return True, "Alert messages are off."
    return change


def act_notify_level(level):
    def change(p):
        if level not in NOTIFY_LEVELS:
            return False, "Unknown setting."
        if p["notify"]["level"] == level:
            return False, None
        p["notify"]["level"] = level
        return True, f"You'll get: {NOTIFY_LEVEL_TEXT[level].lower()}."
    return change


def act_notify_severe(on):
    def change(p):
        if p["notify"]["severe"] == on:
            return False, None
        p["notify"]["severe"] = on
        return True, ("Only Severe and Extreme alerts will be sent." if on
                      else "Alerts of any severity will be sent.")
    return change


def act_notify_test():
    """Rate-limited; the page queues the message only if this reports a change."""
    def change(p):
        left = _cooldown_left(p["notify"])
        if left > 0:
            return False, f"Please wait {left // 60 + 1} more minute(s) before sending another."
        p["notify"]["test_ts"] = int(time.time())
        return True, "Test message queued. It can take a few minutes to arrive."
    return change


def act_notify_address(ident, addr, code):
    """Start using a different address: store it as pending and send a code to it."""
    def change(p):
        if not HASH_RE.match(addr):
            return False, "That isn't an LXMF address (32 characters, 0-9 and a-f)."
        if addr == message_address(ident, p["notify"]):
            return False, "Messages already go to that address."
        left = _cooldown_left(p["notify"])
        if left > 0:
            return False, f"Please wait {left // 60 + 1} more minute(s) before trying again."
        n = p["notify"]
        n.update({"pending_addr": addr, "code": code, "code_ts": int(time.time()),
                  "tries": 0, "test_ts": int(time.time())})
        return True, "A 6-digit code is on its way to that address. Enter it below to confirm."
    return change


def act_notify_verify(entered, result=None):
    """result (a dict) gets result["ok"] = False when the code is wrong or expired."""
    result = result if result is not None else {}

    def change(p):
        n = p["notify"]
        if not n["pending_addr"] or not n["code"]:
            return False, "There's no address waiting to be confirmed."
        if time.time() - n["code_ts"] > 24 * 3600 or n["tries"] >= 5:
            n.update({"pending_addr": "", "code": "", "tries": 0})
            result["ok"] = False
            return True, "That code has expired. Please enter the address again."
        if entered.strip() != n["code"]:
            n["tries"] += 1
            result["ok"] = False
            return True, "That code doesn't match. Please check it and try again."
        n.update({"addr": n["pending_addr"], "pending_addr": "", "code": "", "tries": 0})
        return True, "Address confirmed. Alert messages will go there from now on."
    return change


def act_notify_undo_pending(addr):
    """Used when a verification code couldn't be queued: forget the pending address."""
    def change(p):
        n = p["notify"]
        if n["pending_addr"] != addr:
            return False, None
        n.update({"pending_addr": "", "code": "", "tries": 0, "test_ts": 0})
        return True, None
    return change


def act_notify_reset_address():
    def change(p):
        n = p["notify"]
        if not n["addr"] and not n["pending_addr"]:
            return False, "Messages already go to your own address."
        n.update({"addr": "", "pending_addr": "", "code": "", "tries": 0})
        return True, "Alert messages will go to your own LXMF address."
    return change


# ============================== output pieces ==========================

def alert_key(a):
    """Short, stable id for an alert, used in 'Read full alert' links."""
    raw = a.get("id") or f"{a.get('event')}|{a.get('headline')}|{a.get('ends')}"
    return hashlib.sha1(str(raw).encode("utf-8")).hexdigest()[:12]


def alert_color(event):
    e = event.lower()
    return "`Ff00" if "warning" in e else "`Ff80" if "watch" in e else "`Fff0"


def display_name(loc, wx):
    """Grid squares read better by county, like RetiCast 1.x: 'Harris County, Texas (EM20fb)'."""
    if loc.get("grid") and loc["name"] == f"Grid {loc['grid']}" and wx and wx.get("county"):
        area = ", ".join(x for x in (wx["county"], wx.get("state")) if x)
        return f"{area} ({loc['grid']})"
    return loc["name"]


def place_name(loc):
    """display_name() using saved location details (no network), e.g. for grid squares."""
    meta = read_json(os.path.join(CACHE_DIR, f"meta_{loc_key(loc)}.json"))
    return display_name(loc, meta if isinstance(meta, dict) else None)


def place_subtitle(loc, wx):
    bits = []
    if loc.get("grid"):
        bits.append(f"grid {loc['grid']}")
    if wx and wx.get("county"):
        bits.append(", ".join(x for x in (wx["county"], wx.get("state")) if x))
    elif wx and wx.get("near"):
        bits.append(f"near {wx['near']}")
    bits.append(f"{loc['lat']:.4f}, {loc['lon']:.4f}")
    return "  |  ".join(bits)


def source_note(wx):
    if wx and wx.get("provider") == "om":
        return "Data: Open-Meteo (open-meteo.com)"
    return "Data: National Weather Service (weather.gov)"


def is_old(wx, now=None):
    now = now or time.time()
    limit = max(3 * CACHE_MINUTES * 60, 30 * 60)
    return now - (num(wx.get("cur_ts")) or 0) > limit


def summary_text(wx):
    """'88F, Partly Cloudy, humidity 57%, wind S 9 mph' from cached weather."""
    cur = wx.get("cur") if wx else None
    if not cur:
        return None
    bits = [temp(cur.get("temp_c"))]
    if cur.get("desc"):
        bits.append(cur["desc"])
    if cur.get("rh") is not None:
        bits.append(f"humidity {round(cur['rh'])}%")
    bits.append(f"wind {wind_text(cur)}")
    return ", ".join(bits)


def outlook_text(wx):
    """Short look ahead: 'This Afternoon 93F Sunny; Tonight 76F Clear'."""
    fc = (wx or {}).get("fc") or []
    if not fc:
        return None
    first = fc[0]
    if first.get("kind") == "day":
        bits = [f"{first.get('name', '')}:"]
        if first.get("hi_c") is not None:
            bits.append(f"high {temp(first['hi_c'])}")
        if first.get("lo_c") is not None:
            bits.append(f"low {temp(first['lo_c'])}")
        if first.get("short"):
            bits.append(first["short"])
        return " ".join(bits)
    parts = []
    for p in fc[:2]:
        t = fc_temp(p.get("temp"), p.get("unit"))
        parts.append(" ".join(x for x in (p.get("name"), t, p.get("short")) if x))
    return "; ".join(parts)


def current_lines(cur, tz):
    out = [f"`!{esc(cur.get('desc') or 'N/A')}`!"]
    t = temp(cur.get("temp_c"))
    feels = cur.get("feels_c")
    if feels is not None and temp(feels) != t:
        t += f" (feels like {temp(feels)})"
    rows = [("Temperature", t),
            ("Humidity", f"{round(cur['rh'])}%" if cur.get("rh") is not None else "N/A")]
    if cur.get("dew_c") is not None:
        rows.append(("Dew point", temp(cur["dew_c"])))
    rows.append(("Wind", wind_text(cur)))
    rows.append(("Pressure", pressure(cur.get("pres_hpa"))))
    if cur.get("vis_km") is not None:
        rows.append(("Visibility", distance(cur["vis_km"])))
    obs = parse_time(cur.get("obs_time"))
    if obs:
        when = fmt_local(obs, "%I:%M%p %Z", tz)
        where = cur.get("station") or ""
        if cur.get("station_name"):
            where += f" ({cur['station_name']})"
        rows.append(("Observed", when + (f" at {where}" if where else "")))
    for name, value in rows:
        out.append(line(f"{name.ljust(13)}{value}"))
    return out


def forecast_lines(fc):
    out = []
    items = fc[:FORECAST_PERIODS] if fc and fc[0].get("kind") == "period" else fc[:7]
    if not items:
        return out
    width = max(len(p.get("name") or "") for p in items) + 2
    for p in items:
        if p.get("kind") == "day":
            hi = f"High {temp(p['hi_c'])}" if p.get("hi_c") is not None else ""
            lo = f"Low {temp(p['lo_c'])}" if p.get("lo_c") is not None else ""
            t = " / ".join(x for x in (hi, lo) if x)
            width_t = 20
        else:
            t = fc_temp(p.get("temp"), p.get("unit"))
            t = f"{'High' if p.get('is_day') else 'Low'} {t}" if t else ""
            width_t = 10
        pop = f"Precip {p['pop']}%" if p.get("pop") else ""
        out.append(f"`!{esc((p.get('name') or '').ljust(width))}`!"
                   f"{esc(t.ljust(width_t))}{esc(pop.ljust(12))}{esc(p.get('short') or '')}")
    return out


def weather_lines(loc, wx):
    """Alerts, current conditions and forecast for one place, as Micron lines."""
    out = []
    if not wx or not wx.get("cur"):
        out.append("`Ffa0Weather data isn't available for this place right now. "
                   "Try again in a few minutes.`f")
        if wx and wx.get("fc"):
            out.append("")
            out.append(">>Forecast")
            out += forecast_lines(wx["fc"])
        return out

    tz = get_tz(wx.get("tz"))
    note = f"updated {fmt_epoch(wx['cur_ts'], '%I:%M%p %m/%d/%Y %Z', tz)}"
    if is_old(wx):
        note += "  |  (saved data - the weather service didn't answer)"
    out.append(f"`F888{esc(note)}`f")
    out.append("")

    if wx.get("provider") == "nws":
        alerts = wx.get("alerts") or []
        if alerts:
            out.append(">>Active Alerts")
            for a in alerts:
                end = parse_time(a.get("ends"))
                until = f"  until {fmt_local(end, '%I:%M%p %a', tz)}" if end else ""
                out.append(f"{alert_color(a['event'])}`!{esc(a['event'].upper())}`!`f{esc(until)}")
                if a.get("headline"):
                    out.append(line(trim(a["headline"], 0)[0]))
                cut = False
                if a.get("description"):
                    text, c1 = trim(alert_description(a["description"]), MAX_ALERT_CHARS)
                    out += block(text)
                    cut = cut or c1
                if a.get("instruction"):
                    text, c2 = trim(a["instruction"], MAX_ALERT_CHARS)
                    instr = block(text)
                    out.append("`!What to do:`! " + instr[0].lstrip())
                    out += instr[1:]
                    cut = cut or c2
                if cut:
                    out.append("`_" + mlink("Read full alert", action="alert",
                                            loc=loc_token(loc), a=alert_key(a)) + "`_")
                out.append("")
        else:
            out.append("`F8f8No active watches, warnings, or advisories.`f")
            out.append("")

    out.append(">>Current Conditions")
    out += current_lines(wx["cur"], tz)
    out.append("")

    fc = forecast_lines(wx.get("fc") or [])
    if fc:
        out.append(">>Forecast")
        out += fc
        out.append("")
    if wx.get("provider") == "om":
        out.append("`F888Weather alerts are only available for US locations.`f")
    out.append(f"`F888{esc(source_note(wx))}`f")
    return out


# ============================== pages ==================================

def nav_lines(ident, prof):
    items = [mlink("Home"), mlink("Search", action="search")]
    if ident:
        items.append(mlink("Overview", action="overview"))
        items.append(mlink("My Places", action="places"))
    out = ["  |  ".join(items)]
    if not ident:
        out.append("`F888You're browsing as a guest. Identify to this node to save "
                   f"a default location and up to {MAX_FAVORITES} favorites.`f")
    elif not prof or (not prof["default"] and not prof["favorites"]):
        out.append("`F888You're identified. Search for a place, then save it as your "
                   "default or a favorite.`f")
    return out


def search_box(previous=""):
    return ["Find a place: city, city and state, US ZIP code, grid square, or lat,lon",
            f"`B333`<32|q`{field_default(previous)}>`b  " + mlink("Search", fields=["q"], action="search")]


def message_lines(msg, kind="info"):
    if not msg:
        return []
    color = {"info": "`F0ff", "warn": "`Ffa0", "error": "`Ff55"}.get(kind, "`F0ff")
    return [f"{color}{esc(msg)}`f", ""]


def save_actions(loc, ident, prof):
    """The 'make default / add favorite' links for a place."""
    if not ident:
        return []
    prof = prof or new_profile()
    tok = loc_token(loc)
    bits = []
    if same_place(prof["default"], loc):
        bits.append("`F8f8This is your default location.`f")
    else:
        bits.append(mlink("Make this my default", action="setdefault", loc=tok))
    if same_place(prof["default"], loc):
        pass                                   # the default isn't also a favorite
    elif _find(prof["favorites"], loc) is not None:
        bits.append("`F8f8In your favorites.`f")
    elif len(prof["favorites"]) < MAX_FAVORITES:
        bits.append(mlink("Add to favorites", action="addfav", loc=tok))
    else:
        bits.append(f"`F888Favorites full ({MAX_FAVORITES}).`f")
    return ["  |  ".join(bits)]


def page_view(loc, ident, prof, note=None):
    wx = safe_get_weather(loc)
    out = [">" + line(display_name(loc, wx)).lstrip(), f"`F888{esc(place_subtitle(loc, wx))}`f"]
    if note:
        out.append(f"`F888{esc(note)}`f")
    out += save_actions(loc, ident, prof)
    out.append("")
    out += weather_lines(loc, wx)
    return out


def page_alert(loc, key, ident, prof):
    """One alert in full, from the cache (no extra API calls when it's fresh)."""
    wx = safe_get_weather(loc)
    back = mlink(f"Back to weather for {display_name(loc, wx)}", action="view", loc=loc_token(loc))
    alert = None
    for a in (wx or {}).get("alerts") or []:
        if alert_key(a) == key:
            alert = a
            break
    if not alert:
        return [">Alert no longer active", "",
                "That alert has expired or been cancelled by the National Weather Service.",
                "", back]
    tz = get_tz(wx.get("tz"))
    out = [f">{alert_color(alert['event'])}{esc(alert['event'].upper())}`f"]
    end = parse_time(alert.get("ends"))
    info = [display_name(loc, wx)]
    if end:
        info.append(f"until {fmt_local(end, '%I:%M%p %a %m/%d', tz)}")
    out.append(f"`F888{esc('  |  '.join(info))}`f")
    out.append("")
    if alert.get("headline"):
        out.append(f"`!{esc(trim(alert['headline'], 0)[0])}`!")
        out.append("")
    if alert.get("area"):
        out.append("`!Areas:`! " + line(" ".join(alert["area"].split())).lstrip())
        out.append("")
    if alert.get("description"):
        out += block(trim(alert_description(alert["description"]), 0)[0])
        out.append("")
    if alert.get("instruction"):
        out.append(">>What to do")
        out += block(trim(alert["instruction"], 0)[0])
        out.append("")
    if alert.get("sender"):
        out.append(f"`F888Issued by {esc(alert['sender'])}`f")
        out.append("")
    out.append(back)
    return out


def page_overview(ident, prof):
    out = [">My Places - Overview"]
    places = []
    if prof and prof["default"]:
        places.append(("Default", prof["default"]))
    for i, f in enumerate(prof["favorites"] if prof else []):
        places.append((f"Favorite {i + 1}", f))
    if not places:
        out.append("You haven't saved any places yet. Use Search to find one.")
        return out
    out.append("")
    for (tag, loc), wx in zip(places, weather_many([p[1] for p in places])):
        out.append(f"`!{mlink(display_name(loc, wx), action='view', loc=loc_token(loc))}`!  `F888{tag}`f")
        summary = summary_text(wx)
        if summary:
            text = f"  {summary}"
            if is_old(wx):
                text += " (saved data)"
            out.append(line(text))
            outlook = outlook_text(wx)
            if outlook:
                out.append(line(f"  {outlook}"))
            events = []
            for a in wx.get("alerts") or []:
                if a["event"] not in events:
                    events.append(a["event"])
            if events:
                out.append(f"  `Ff55! {esc(', '.join(events))}`f")
        else:
            out.append("  `Ffa0Weather data isn't available right now.`f")
        out.append("")
    return out


def page_places(ident, prof):
    prof = prof or new_profile()
    out = [">My Places", ""]
    out.append(">>Default location")
    d = prof["default"]
    if d:
        out.append(f"{esc(place_name(d))}  " + "  ".join([
            mlink("View", action="view", loc=loc_token(d)),
            mlink("Stop using as default", action="cleardefault", k=loc_key(d))]))
    else:
        out.append("`F888None set. Visitors see the node's default location instead.`f")
    out.append("")
    out.append(f">>Favorites ({len(prof['favorites'])} of {MAX_FAVORITES})")
    if not prof["favorites"]:
        out.append("`F888None yet.`f")
    for i, f in enumerate(prof["favorites"]):
        tok = loc_token(f)
        out.append(f"{i + 1}. {esc(place_name(f))}  " + "  ".join([
            mlink("View", action="view", loc=tok),
            mlink("Make default", action="setdefault", loc=tok),
            mlink("Remove", action="delfav", i=i, k=loc_key(f))]))
    out.append("")
    out.append(">>When I open RetiCast, show")
    for mode, text in (("default", "My default location"),
                       ("overview", "An overview of all my places")):
        if prof["view"] == mode:
            out.append(f"  `F8f8(*) {text}`f")
        else:
            out.append(f"  ( ) {mlink(text, action='setview', mode=mode)}")
    if prof["view"] == "overview" and len(prof["favorites"]) + (1 if d else 0) < 2:
        out.append("`F888The overview is used once you have at least 2 saved places.`f")
    out.append("")
    out += notify_lines(ident, prof)
    out.append(">>Add a place")
    out += search_box()
    return out


def notify_lines(ident, prof):
    """The 'Alert messages' section of My Places."""
    installed, running, hb = notifier_status()
    if not installed or not ident or not HASH_RE.match(ident):
        return []
    n = prof["notify"]
    out = [">>Alert messages"]
    if not running:
        out.append("`Ffa0The alert message service isn't running right now, so nothing "
                   "can be sent until it's back.`f")
    d = prof["default"]
    if not d:
        out.append("Choose a default location to get LXMF messages when the National Weather "
                   "Service issues alerts for it.")
        return out + [""]
    covered = nws_covered(d)
    if covered is False:
        out.append(f"Alert messages are only available for US locations; your default is "
                   f"{esc(place_name(d))}.")
        return out + [""]

    status = "`F8f8On`f" if n["on"] else "`F888Off`f"
    toggle = mlink("Turn off", action="ntf_off") if n["on"] else mlink("Turn on", action="ntf_on")
    out.append(f"Messages for {esc(place_name(d))}: {status}  {toggle}")
    out.append("`F888Alerts are for your default location. To get them for another place, "
               "make it your default.`f")
    out.append("")

    st = address_status(running)
    own = lxmf_address(ident)
    out.append(f"Your LXMF address: {own}")
    out.append("  " + _found_text(st["addresses"].get(own), running))
    out.append("  `F888Worked out from the identity you browse with. If it isn't the address "
               "your messaging app shows, enter that address below.`f")
    if n["addr"]:
        out.append(f"Messages go to: {n['addr']} (an address you confirmed)  " +
                   mlink("Use my own address instead", action="ntf_reset"))
        out.append("  " + _found_text(st["addresses"].get(n["addr"]), running))
    else:
        out.append("Messages go to this address.")
    last = _last_text(st["last"].get(message_address(ident, n)),
                      get_tz((read_json(os.path.join(CACHE_DIR, f"meta_{loc_key(d)}.json")) or {})
                             .get("tz")))
    if last:
        out.append("Last message: " + esc(last))
    if hb.get("address") and HASH_RE.match(str(hb["address"])):
        out.append(f"`F888They come from {hb['address']} ({esc(hb.get('name') or 'RetiCast')}).`f")
    out.append("")

    out.append("Send me:")
    for level in NOTIFY_LEVELS:
        text = NOTIFY_LEVEL_TEXT[level]
        if n["level"] == level:
            out.append(f"  `F8f8(*) {text}`f")
        else:
            out.append(f"  ( ) {mlink(text, action='ntf_level', lv=level)}")
    if n["severe"]:
        out.append("  `F8f8[x] Only Severe and Extreme alerts`f  " +
                   mlink("Change", action="ntf_severe", on=0))
    else:
        out.append("  [ ] " + mlink("Only Severe and Extreme alerts", action="ntf_severe", on=1))
    out.append("")
    out.append(mlink("Send a test message", action="ntf_test") +
               "  `F888Reply STOP to any message to turn them off.`f")
    out.append("")
    if n["pending_addr"]:
        out.append(f"Waiting to confirm {n['pending_addr']}. Enter the code sent to it:")
        out.append("  " + _found_text(st["addresses"].get(n["pending_addr"]), running))
        out.append("`B333`<8|code`>`b  " + mlink("Confirm", fields=["code"], action="ntf_verify") +
                   "  " + mlink("Cancel", action="ntf_reset"))
    else:
        out.append("Use a different LXMF address:")
        out.append("`B333`<34|lxmf`>`b  " + mlink("Use this address", fields=["lxmf"],
                                                   action="ntf_addr"))
    out.append("")
    return out


def page_search(ident, prof, query):
    out = [">Search", ""]
    out += search_box(query or "")
    if not query or not query.strip():
        out.append("")
        out.append("`F888Examples: Austin TX  |  Paris, France  |  77002  |  EM20fb  |  29.76,-95.37`f")
        return out
    results, msg = search_locations(query)
    out.append("")
    if msg:
        out += message_lines(msg, "warn")
    if results:
        out.append(">>Results")
    prof = prof or new_profile()
    for loc in results:
        tok = loc_token(loc)
        row = [mlink(loc["name"], action="view", loc=tok)]
        if ident:
            if same_place(prof["default"], loc):
                row.append("`F8f8(your default)`f")
            elif _find(prof["favorites"], loc) is not None:
                row.append("`F8f8(favorite)`f")
            else:
                row.append(mlink("Default", action="setdefault", loc=tok))
                if len(prof["favorites"]) < MAX_FAVORITES:
                    row.append(mlink("+Favorite", action="addfav", loc=tok))
        out.append("  ".join(row))
        out.append(f"  `F888{loc['lat']:.4f}, {loc['lon']:.4f}`f")
    return out


def page_server_default(ident, prof, extra_note=None):
    try:
        loc = server_default_loc()
    except LookupError as e:
        warn(f"server default location problem: {e}")
        return (message_lines("This node's default location couldn't be looked up right now.",
                              "warn") + search_box())
    note = "This node's default location."
    if ident and prof and not prof["default"]:
        note += " Save your own default to see it here instead."
    if extra_note:
        note = extra_note
    return page_view(loc, ident, prof, note=note)


def page_home(ident, prof):
    if ident and prof:
        places = ([prof["default"]] if prof["default"] else []) + prof["favorites"]
        if prof["view"] == "overview" and len(places) >= 2:
            return page_overview(ident, prof)
        if prof["default"]:
            return page_view(prof["default"], ident, prof)   # save_actions says it's the default
        if len(places) >= 2:
            return page_overview(ident, prof)
        if places:
            return page_view(places[0], ident, prof, note="Your saved place.")
    return page_server_default(ident, prof)


def handle(env):
    """Build the page body for one request. env is os.environ-like."""
    ident = get_identity()
    prof = get_profile(ident)
    action = (env.get("var_action") or "").strip().lower()
    msg_lines = []

    def need_ident():
        return message_lines("Identify to this node first to save places.", "warn")

    if action in ("setdefault", "addfav"):
        loc = loc_from_token(env.get("var_loc"))
        if not loc:
            msg_lines = message_lines("That link was damaged. Please search again.", "error")
        elif not ident:
            return need_ident() + page_view(loc, ident, prof)
        else:
            change = act_set_default(loc) if action == "setdefault" else act_add_favorite(loc)
            changed, msg = update_profile(ident, change)
            prof = get_profile(ident)
            return message_lines(msg, "info" if changed else "warn") + page_view(loc, ident, prof)

    elif action in ("delfav", "cleardefault", "setview"):
        if not ident:
            msg_lines = need_ident()
        else:
            if action == "delfav":
                try:
                    index = int(env.get("var_i", ""))
                except ValueError:
                    index = -1
                change = act_remove_favorite(index, env.get("var_k", ""))
            elif action == "cleardefault":
                change = act_clear_default(env.get("var_k", ""))
            else:
                change = act_set_view(env.get("var_mode", ""))
            changed, msg = update_profile(ident, change)
            prof = get_profile(ident)
            return message_lines(msg, "info" if changed else "warn") + page_places(ident, prof)

    elif action.startswith("ntf_"):
        if not ident or not HASH_RE.match(ident):
            msg_lines = need_ident()
        elif not notifier_status()[0]:
            msg_lines = message_lines("Alert messages aren't set up on this node.", "warn")
        else:
            return handle_notify(action, ident, env)

    elif action == "view":
        loc = loc_from_token(env.get("var_loc"))
        if loc:
            return page_view(loc, ident, prof)
        msg_lines = message_lines("That link was damaged. Please search again.", "error")

    elif action == "alert":
        loc = loc_from_token(env.get("var_loc"))
        key = env.get("var_a", "")
        if loc and re.fullmatch(r"[0-9a-f]{12}", key):
            return page_alert(loc, key, ident, prof)
        msg_lines = message_lines("That link was damaged. Please search again.", "error")

    elif action == "search":
        return page_search(ident, prof, env.get("field_q", ""))

    elif action == "overview":
        if ident:
            return page_overview(ident, prof)
        msg_lines = need_ident()

    elif action == "places":
        if ident:
            return page_places(ident, prof)
        msg_lines = need_ident()

    return msg_lines + page_home(ident, prof)


def handle_notify(action, ident, env):
    extra = None
    result = {"ok": True}
    if action == "ntf_on":
        change = act_notify_on()
    elif action == "ntf_off":
        change = act_notify_off()
    elif action == "ntf_level":
        change = act_notify_level(env.get("var_lv", ""))
    elif action == "ntf_severe":
        change = act_notify_severe(env.get("var_on") == "1")
    elif action == "ntf_test":
        change = act_notify_test()
        extra = "test"
    elif action == "ntf_addr":
        addr = (env.get("field_lxmf") or "").strip().lower().strip("<>")
        code = f"{int.from_bytes(os.urandom(4), 'big') % 1000000:06d}"
        change = act_notify_address(ident, addr, code)
        extra = ("verify", addr, code)
    elif action == "ntf_verify":
        change = act_notify_verify(env.get("field_code") or "", result)
    elif action == "ntf_reset":
        change = act_notify_reset_address()
    else:
        return message_lines("Unknown setting.", "warn") + page_places(ident, get_profile(ident))

    changed, msg = update_profile(ident, change)
    prof = get_profile(ident)
    if changed and extra == "test":
        if not queue_message("test", ident, message_address(ident, prof["notify"])):
            changed, msg = False, "The message couldn't be queued. Please try again later."
    elif changed and isinstance(extra, tuple):
        _, addr, code = extra
        if not queue_message("verify", ident, addr, code=code):
            update_profile(ident, act_notify_undo_pending(addr))
            prof = get_profile(ident)
            changed, msg = False, "The code couldn't be sent. Please try again later."
    ok = changed and result["ok"]
    return message_lines(msg, "info" if ok else "warn") + page_places(ident, prof)


def page_main():
    """Entry point for pages/reticast.mu."""
    with contextlib.suppress(Exception):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print("#!c=0")            # personal page: tell clients not to cache it
    try:
        from site_parts import print_top     # shared site header + menu, if present
        print_top()
    except Exception:
        pass
    sys.stdout.flush()

    ident = None
    try:
        ident = get_identity()
        body = handle(os.environ)
    except Exception as e:
        warn(f"page error: {type(e).__name__}: {e}")
        traceback.print_exc(file=sys.stderr)
        body = message_lines(f"RetiCast hit a problem building this page ({type(e).__name__}).",
                             "error")
    try:
        prof = get_profile(ident)
    except Exception:
        prof = None
    top = nav_lines(ident, prof) + [""]
    print("\n".join(top + body))
    print()
    print(f"`F888RetiCast {VERSION}`f")


# ============================== public (1.x compatible) ================

def _line_alerts(alerts, tz):
    events = {}   # event name -> latest end time (dedupes repeated alerts)
    for a in alerts:
        event = a.get("event", "")
        if not any(k.lower() in event.lower() for k in ALERT_KEYWORDS):
            continue
        end = parse_time(a.get("ends"))
        if event not in events or (end and (events[event] is None or end > events[event])):
            events[event] = end

    def rank(item):
        name, end = item
        order = 0 if "warning" in name.lower() else 1 if "watch" in name.lower() else 2
        return (order, end or datetime.max.replace(tzinfo=timezone.utc))

    today = datetime.now(tz).date()
    out = []
    for event, end in sorted(events.items(), key=rank):
        if end:
            when = fmt_local(end, EXPIRE_FORMAT, tz)
            if end.astimezone(tz).date() != today:
                when += end.astimezone(tz).strftime(" %a")
            out.append(f"{event.upper()}!!! (Expiring {when} local time)")
        else:
            out.append(f"{event.upper()}!!!")
    return out


def get_weather_string():
    """One-line summary of the server default location, for other pages."""
    try:
        loc = server_default_loc()
        wx = safe_get_weather(loc, fetch="missing")
        if not wx or not wx.get("cur"):
            return esc(f"Weather data unavailable for {loc['name']}")
        cur = wx["cur"]
        tz = get_tz(wx.get("tz"))
        shown = parse_time(cur.get("obs_time")) if TIMESTAMP_SOURCE == "observation" else None
        shown = shown or datetime.fromtimestamp(wx["cur_ts"], timezone.utc)
        where = f"gridsquare {loc['grid']}" if loc.get("grid") else loc["name"]
        area = ", ".join(x for x in (wx.get("county"), wx.get("state")) if x)
        if area:
            where += f" ({area})"
        humidity = f"{round(cur['rh'])}%" if cur.get("rh") is not None else "N/A"
        text = (f"Current weather conditions for {where} @ {fmt_local(shown, TIME_FORMAT, tz)}: "
                f"Temp. {temp(cur.get('temp_c'))}, Humidity {humidity}, {cur.get('desc') or 'N/A'}")
        alerts = _line_alerts(wx.get("alerts") or [], tz)
        if alerts:
            text += ", Warning/Watch: " + " ".join(alerts)
        if is_old(wx):
            text += " (cached - update failed)"
        return esc(text)
    except Exception as e:
        return esc(f"Weather data unavailable for {DEFAULT_LOCATION} ({type(e).__name__})")


def get_weather_title():
    """'Harris County, Texas (EM20fb)' style title for the server default location."""
    try:
        loc = server_default_loc()
        wx = safe_get_weather(loc, fetch="missing") or {}
        area = ", ".join(x for x in (wx.get("county"), wx.get("state")) if x)
        if area and loc.get("grid"):
            return esc(f"{area} ({loc['grid']})")
        return esc(loc["name"])
    except Exception:
        return esc(DEFAULT_LOCATION)


def get_weather_micron():
    """Full weather view (alerts, conditions, forecast) of the server default location."""
    try:
        loc = server_default_loc()
        return "\n".join(weather_lines(loc, safe_get_weather(loc)))
    except Exception as e:
        return esc(f"Weather data unavailable for {DEFAULT_LOCATION} ({type(e).__name__})")


# ============================== cron ===================================

def prefetch_list():
    """Server default first, then saved places, most recently updated visitors first."""
    locs = []
    try:
        locs.append(server_default_loc())
    except LookupError as e:
        warn(f"server default location problem: {e}")
    profiles = [normalize_profile(p) for p in load_users().values()]
    profiles = [p for p in profiles if p]
    profiles.sort(key=lambda p: -(num(p.get("updated")) or 0))
    for p in profiles:
        for loc in ([p["default"]] if p["default"] else []) + p["favorites"]:
            if not any(same_place(loc, x) for x in locs):
                locs.append(loc)
    return locs


def prune_cache(keep_keys):
    now = time.time()
    try:
        names = os.listdir(CACHE_DIR)
    except OSError:
        return
    for name in names:
        path = os.path.join(CACHE_DIR, name)
        try:
            age = now - os.path.getmtime(path)
        except OSError:
            continue
        if name.startswith(".tmp-"):
            remove = age > 3600
        elif name.startswith("geo_"):
            remove = age > GEO_DAYS * 86400
        elif name.startswith(("wx_", "meta_")):
            key = name.split("_", 1)[1][:-5] if name.endswith(".json") else ""
            remove = age > PRUNE_DAYS * 86400 and key not in keep_keys
        else:
            remove = False
        if remove:
            with contextlib.suppress(OSError):
                os.unlink(path)
                debug(f"pruned {name}")


def refresh_all():
    ensure_dirs()
    locs = prefetch_list()
    weather_many(locs[:max(1, PREFETCH_LIMIT)])
    prune_cache({loc_key(l) for l in locs})


def main(argv):
    if "--search" in argv:
        i = argv.index("--search")
        query = " ".join(argv[i + 1:]).strip()
        results, msg = search_locations(query)
        if msg:
            print(msg)
        for loc in results:
            print(f"{loc['name']}  ({loc['lat']:.4f}, {loc['lon']:.4f})  cc={loc['cc'] or '-'}")
        return 0
    if "--page" in argv:
        print(get_weather_micron())
        return 0
    if "--check" in argv:
        try:
            loc = server_default_loc()
        except LookupError as e:
            print(f"Default location problem: {e}")
            return 1
        print(f"Default location: {loc['name']} ({loc['lat']:.4f}, {loc['lon']:.4f})")
        return 0
    refresh_all()
    print(get_weather_string())
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
