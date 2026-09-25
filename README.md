# RetiCast

Live US weather and NWS watches/warnings for a Maidenhead grid square, printed as one line of text on your NomadNet pages.

```
Current weather conditions for gridsquare FN31pr (Hartford County, Connecticut) @ 3PM 09/24/2026: Temp. 72F, Humidity 55%, Partly Cloudy
```

When watches or warnings are active for your area, they are added to the end of the line:

```
..., Warning/Watch: FLASH FLOOD WARNING!!! (Expiring 10PM local time) TORNADO WATCH!!! (Expiring 8PM local time)
```

## Features

- Works from a grid square alone. RetiCast finds the county, state, time zone, and nearest weather stations for you.
- Shows active watches and warnings with their local expiration time, and leaves them out when there are none.
- Python standard library only. Nothing to `pip install`, and no API key.
- Fast page loads. A cron job refreshes the line every 5 minutes and saves it, so visitors never wait on the weather service.
- Fails gracefully. If the weather service is unreachable, the page shows the last good line, or "Weather unavailable", and still loads.

## Requirements

- A NomadNet node, installed and running
- Python 3.9 or newer
- Internet access from the node
- A location in the US or its territories (RetiCast uses the National Weather Service API)
- An email address or callsign, which NWS requires every app to send

## Quick install

Run as the same user that runs NomadNet:

```
git clone <this repo URL>
cd reticast
./install.sh
```

The installer asks for your grid square and your email or callsign. To skip the questions:

```
GRID=FN31pr CONTACT=you@example.com ./install.sh
```

Then open your node in a NomadNet client. The demo home page shows a header, the weather line, and a small menu with an About page.

### What the installer does

1. Checks for Python 3.9+ and your NomadNet pages folder (`~/.nomadnetwork/storage/pages`).
2. Installs `reticast.py` in `~/scripts` with your grid square, contact, and full Python path.
3. Installs the demo header and body in `~/scripts`. Existing files are kept, never overwritten.
4. Backs up your current `index.mu` to `~/scripts/index.mu.backup.<date>`, then installs the RetiCast page script.
5. Adds a demo `about.mu` if you don't already have one.
6. Runs a test fetch with timing output.
7. Adds a cron job that refreshes the weather every 5 minutes (only once, even if you rerun it).

You can change the install locations with `SCRIPTS_DIR`, `PAGES_DIR`, and `PYTHON`, for example `PYTHON=/usr/bin/python3 ./install.sh`.

## Files

| Path in repo | Installed to | What it is |
| --- | --- | --- |
| `scripts/reticast.py` | `~/scripts/` | Fetches the weather and builds the line |
| `scripts/index_header.mu` | `~/scripts/` | Demo site header, shown above the weather |
| `scripts/index_body.mu` | `~/scripts/` | Demo menu and content, shown below the weather |
| `pages/index.mu` | `~/.nomadnetwork/storage/pages/` | Page script: header, then weather, then body |
| `pages/about.mu` | `~/.nomadnetwork/storage/pages/` | Demo About page |

`reticast_cache.json` is created next to `reticast.py` on the first run.

## How it works

Micron can't run code or include other files, so the home page is a small Python script. It prints three parts in order:

```
index_header.mu   ->   weather line (from the cache)   ->   index_body.mu
```

Behind that, `reticast.py`:

1. Converts the grid square to the latitude and longitude at its center.
2. Looks up the county, state, time zone, and nearby stations from the NWS API. These never change, so they're saved and looked up only once.
3. Fetches the latest observation and the active alerts at the same time.
4. Builds the line and saves it to the cache for 10 minutes.

Cron runs it every 5 minutes, so the saved line is always fresh when a visitor arrives.

## Make it yours

Replace the demo files in `~/scripts` with your own Micron:

- `index_header.mu`: everything above the weather line, such as your title or banner
- `index_body.mu`: everything below it, such as your menu and page content

Changes show up on the next page load. You don't need to edit `index.mu`.

To show the weather on a page that is already a Python script, add:

```python
import os
import sys
sys.path.insert(0, os.path.expanduser("~/scripts"))
from reticast import get_weather_string

print("`F0ff`!Weather:`! " + get_weather_string() + "`f")
```

## Configuration

Settings are at the top of `reticast.py`. The installer sets the first two for you.

| Setting | Default | What it does |
| --- | --- | --- |
| `GRIDSQUARE` | `"FN31pr"` | Your 4, 6, or 8 character grid square |
| `USER_AGENT` | `"(RetiCast, you@example.com)"` | Your contact for NWS |
| `ALERT_KEYWORDS` | `("Warning", "Watch")` | Alert types shown; add `"Advisory"` for more |
| `CACHE_MINUTES` | `10` | How long a saved line is reused |
| `TIMESTAMP_SOURCE` | `"now"` | `"now"` = fetch time; `"observation"` = station reading time |
| `TIME_FORMAT` | `"%I%p %m/%d/%Y"` | Time after the "@"; use `"%I:%M%p %m/%d/%Y"` to show minutes |

If you change the cron interval to longer than 10 minutes, raise `CACHE_MINUTES` to a little more than the new interval.

## Troubleshooting

Run the script by hand to see what it's doing:

```
~/scripts/reticast.py --debug
```

This prints a `[debug]` line with the time each API call took. If you see no `[debug]` lines, the line came from the cache; delete `~/scripts/reticast_cache.json` and run it again.

| Symptom | Fix |
| --- | --- |
| Page shows the script's code | `chmod +x ~/.nomadnetwork/storage/pages/index.mu` |
| Page shows "Weather unavailable" | Check that `~/scripts/reticast.py` exists, and that the first line of `index.mu` is your Python path (`which python3`) |
| `WARNING: cannot write cache file` | Make `~/scripts` writable by the NomadNet user |
| Line ends with `(cached - update failed)` | The weather service was unreachable; it clears on the next good update |
| Weather never updates | Check `crontab -l` for the RetiCast job |

## Uninstall

```
crontab -l | grep -v reticast.py | crontab -
cp ~/scripts/index.mu.backup.<date> ~/.nomadnetwork/storage/pages/index.mu
rm ~/scripts/reticast.py ~/scripts/reticast_cache.json
```

## Credits

Weather data comes from the [National Weather Service API](https://www.weather.gov/documentation/services-web-api). RetiCast is not affiliated with or endorsed by the National Weather Service.
