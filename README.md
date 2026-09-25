# RetiCast

Live US weather for your NomadNet node, from nothing but a Maidenhead grid square. RetiCast gives you two things:

- **A full weather page** at `/page/reticast.mu` with active alerts, current conditions, and a 7-day forecast.
- **A one-line summary** you can put on your home page or any other page.

Both come from the National Weather Service, refresh automatically, and load instantly because visitors are always served saved data.

## What it looks like

The weather page (in a NomadNet client, warnings are red, watches orange, and advisories yellow):

```
Weather for Hartford County, Connecticut (FN31pr)

updated 3:05PM 09/24/2026  |  station KHFD (Hartford-Brainard Airport)

  Active Alerts
  SEVERE THUNDERSTORM WARNING  until 3:30PM Thu
  Severe Thunderstorm Warning issued September 24 at 2:41PM EDT until 3:30PM EDT by NWS Boston
  At 241 PM EDT, a severe thunderstorm was located near West Hartford, moving east at 25 mph.
  What to do: For your protection move to an interior room on the lowest floor of a building.
  Read full alert

  Current Conditions
  Partly Cloudy
  Temperature  88F (feels like 94F)
  Humidity     57%
  Dew point    71F
  Wind         SW 9 mph, gusts 18 mph
  Pressure     30.02 inHg
  Visibility   10 mi

  Forecast
  This Afternoon  High 88F  Precip 60%  Showers And Thunderstorms
  Tonight         Low 71F   Precip 20%  Partly Cloudy
  Friday          High 82F              Sunny
  ...

Data: National Weather Service (weather.gov)
```

The one-line summary:

```
Current weather conditions for gridsquare FN31pr (Hartford County, Connecticut) @ 3PM 09/24/2026: Temp. 88F, Humidity 57%, Partly Cloudy, Warning/Watch: SEVERE THUNDERSTORM WARNING!!! (Expiring 3PM local time)
```

Watches and warnings are only added to the line when they're active.

## Features

- Works from a grid square alone. RetiCast finds the county, state, time zone, nearest weather stations, and forecast area for you.
- Full weather page: every active alert with its details and safety instructions (long alerts are trimmed, with a "Read full alert" link to the complete text), current conditions including feels-like temperature, wind, pressure, and visibility, and a 7-day forecast.
- US or metric units.
- Fast page loads. A cron job refreshes the data every 5 minutes and saves it, so visitors never wait on the weather service.
- Fails gracefully. If the weather service is unreachable, pages show the last good data, marked as cached, and still load.
- Python standard library only. Nothing to `pip install`, and no API key.

## Requirements

- A NomadNet node, installed and running
- Python 3.9 or newer
- Internet access from the node
- A location in the US or its territories (RetiCast uses the National Weather Service API)
- An email address or callsign, which NWS requires every app to send

## Install options

There are two ways to install RetiCast. Both give the same result:

- **Quick install:** run `install.sh`, which asks two questions and does everything for you. Best for most people.
- **Manual install:** copy the files and set things up by hand. Use this if you want to see every step before it happens, or if your setup is unusual.

The installer can set up just the weather page, or the weather page plus a demo home page that shows the one-line summary. See `HOME_PAGE` under [Installer options](#installer-options).

Either way, install as the same user that runs NomadNet. Installing doesn't need `sudo`, though restarting a system-wide NomadNet service afterward may.

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

To install only the weather page and leave your existing home page alone:

```
HOME_PAGE=no ./install.sh
```

### What the installer does

1. Checks for Python 3.9+ and your NomadNet pages folder (`~/.nomadnetwork/storage/pages`).
2. Installs `reticast.py` in `~/scripts` with your grid square, contact, and full Python path.
3. Installs the weather page as `reticast.mu`. If you already have an unrelated page with that name, it's backed up to `~/scripts` first.
4. Unless you set `HOME_PAGE=no`:
    - installs the demo header and body in `~/scripts` (existing files are kept, never overwritten)
    - backs up your current `index.mu` to `~/scripts/index.mu.backup.<date>`, then installs the demo home page
    - adds a demo `about.mu` if you don't already have one
5. Runs a test fetch with timing output.
6. Adds a cron job that refreshes the weather every 5 minutes (only once, even if you rerun it).

### Installer options

Set any of these before `./install.sh` to change how it runs:

| Option | Default | What it does |
| --- | --- | --- |
| `GRID` | asks | Your grid square; skips the question |
| `CONTACT` | asks | Your email or callsign for NWS; skips the question |
| `HOME_PAGE` | `yes` | `no` installs only the weather page and leaves `index.mu` alone |
| `SCRIPTS_DIR` | `~/scripts` | Where `reticast.py` and the header and body files go |
| `PAGES_DIR` | `~/.nomadnetwork/storage/pages` | Your NomadNet pages folder |
| `PYTHON` | output of `which python3` | Python to use (3.9 or newer) |

For example, to use a specific Python and a different scripts folder:

```
PYTHON=/usr/bin/python3 SCRIPTS_DIR=~/reticast-files ./install.sh
```

It's safe to run the installer again, for example to change your grid square. It keeps your own header, body, and About page, and it won't add a second cron job.

### Restart NomadNet

NomadNet registers its pages when it starts, so restart it after installing to make sure the new and updated pages are picked up. Later edits to your header and body files show up without a restart. If NomadNet runs as a systemd service:

```
sudo systemctl restart nomadnet
```

Use your own unit name if it's different (check with `systemctl list-units | grep -i nomad`), or `systemctl --user restart nomadnet` for a user service. If you start NomadNet by hand, stop it and start it again.

### Link to the weather page

Open `/page/reticast.mu` on your node to check it, then link to it from your home page or menu:

```
`[Weather`:/page/reticast.mu]
```

The demo home page already links to it twice: from its menu, and from the word "Weather" at the start of the one-line summary.

## Manual install

To do the same steps by hand from the repo folder, as the NomadNet user:

1. Copy the script:
   ```
   mkdir -p ~/scripts
   cp scripts/reticast.py ~/scripts/
   chmod +x ~/scripts/reticast.py
   ```
2. Edit the settings at the top of `~/scripts/reticast.py`: set `GRIDSQUARE` to your grid square and put your email or callsign in `USER_AGENT`.
3. Find your full Python path. It must be version 3.9 or newer:
   ```
   which python3
   python3 --version
   ```
   NomadNet runs page scripts without your login shell, so put this full path on the first line of each page you install from `pages/`, in the form `#!/usr/bin/python3`.
4. Install the weather page:
   ```
   cp pages/reticast.mu ~/.nomadnetwork/storage/pages/
   chmod +x ~/.nomadnetwork/storage/pages/reticast.mu
   ```
5. Optional: install the demo home page. Skip this to keep your own home page. If you already have your own `index_header.mu` or `index_body.mu` in `~/scripts`, don't copy those two.
   ```
   cp scripts/index_header.mu scripts/index_body.mu ~/scripts/
   cp ~/.nomadnetwork/storage/pages/index.mu ~/scripts/index.mu.backup
   cp pages/index.mu pages/about.mu ~/.nomadnetwork/storage/pages/
   chmod +x ~/.nomadnetwork/storage/pages/index.mu
   ```
6. Test it. You should see `[debug]` timing lines, then the weather line:
   ```
   ~/scripts/reticast.py --debug
   ```
7. Add the cron job, using your Python path from step 3. Then confirm it with `crontab -l`:
   ```
   (crontab -l 2>/dev/null; echo "*/5 * * * * /usr/bin/python3 $HOME/scripts/reticast.py > /dev/null 2>&1") | crontab -
   ```
8. Restart NomadNet, as described under [Restart NomadNet](#restart-nomadnet).

If you put the script somewhere other than `~/scripts`, also change the `SCRIPTS_DIR` line in `reticast.mu` and the `PARTS_DIR` line in `index.mu` to match.

## Files

| Path in repo | Installed to | What it is |
| --- | --- | --- |
| `scripts/reticast.py` | `~/scripts/` | Fetches the weather, caches it, and formats it |
| `pages/reticast.mu` | `~/.nomadnetwork/storage/pages/` | The full weather page |
| `pages/index.mu` | `~/.nomadnetwork/storage/pages/` | Demo home page: header, weather line, body |
| `scripts/index_header.mu` | `~/scripts/` | Demo site header, shown above the weather line |
| `scripts/index_body.mu` | `~/scripts/` | Demo menu and content, shown below the weather line |
| `pages/about.mu` | `~/.nomadnetwork/storage/pages/` | Demo About page |

`reticast_cache.json` is created next to `reticast.py` on the first run.

## How it works

Cron runs `reticast.py` every 5 minutes. It:

1. Converts the grid square to the latitude and longitude at its center.
2. Looks up the county, state, time zone, nearby stations, and forecast area from the NWS API. These never change, so they're saved and looked up only once.
3. Fetches the latest observation and the active alerts every 10 minutes, and the forecast every 30 minutes, all at the same time.
4. Saves everything to `reticast_cache.json`.

When someone opens a page, it builds the output from that saved data, so pages never wait on the weather service.

## Show the weather on your own pages

Micron can't run code or include other files, so a page that shows live data has to be a small Python script. The demo home page works this way, printing three parts in order:

```
index_header.mu   ->   weather line   ->   index_body.mu
```

To use your existing home page with it, split your old `index.mu` into two files in `~/scripts`: everything above where the weather should appear goes in `index_header.mu`, and everything below goes in `index_body.mu`. Later changes to those files show up on the next page load, without editing `index.mu`.

To add weather to a page that is already a Python script, add this with no leading spaces:

```python
import os
import sys
sys.path.insert(0, os.path.expanduser("~/scripts"))

try:
    from reticast import get_weather_string
    weather = get_weather_string()
except Exception as e:
    weather = f"Weather unavailable ({type(e).__name__})"
print("`F0ff`!`_`[Weather`:/page/reticast.mu]`_`!: " + weather + "`f")
```

The word "Weather" links to the full weather page. For the full page content instead of one line, use `get_weather_micron()`. The `try` block makes sure a weather problem can never stop the rest of the page from loading.

## Match your site's header and menu

If your pages share a common header and menu, the RetiCast page can show them too. Create `site_parts.py` in your scripts folder (`~/scripts` by default) with a `print_top()` function that prints them:

```python
# ~/scripts/site_parts.py
import os

def print_top():
    for name in ("my_header.mu", "my_menu.mu"):
        with open(os.path.expanduser("~/scripts/" + name), encoding="utf-8") as f:
            print(f.read(), end="")
```

The RetiCast page looks for `site_parts.py` every time it loads and, if it's there, prints your header and menu above its own content. Because the hook lives in your own file, it keeps working when you rerun the installer or update RetiCast. If `site_parts.py` doesn't exist, the page shows its own content only.

## Configuration

Settings are at the top of `reticast.py`. The installer sets the first two for you.

| Setting | Default | What it does |
| --- | --- | --- |
| `GRIDSQUARE` | `"FN31pr"` | Your 4, 6, or 8 character grid square |
| `USER_AGENT` | `"(RetiCast, you@example.com)"` | Your contact for NWS |
| `UNITS` | `"us"` | `"us"` (F, mph, inHg, miles) or `"metric"` (C, km/h, hPa, km) |
| `ALERT_KEYWORDS` | `("Warning", "Watch")` | Alert types in the one-line summary; add `"Advisory"` for more |
| `PAGE_ALL_ALERTS` | `True` | Show every alert on the weather page, advisories included |
| `MAX_ALERT_CHARS` | `600` | Trim long alerts on the page and add a "Read full alert" link (`0` = never trim) |
| `PAGE_PATH` | `"/page/reticast.mu"` | Where the weather page is served; change it if you rename the page |
| `FORECAST_PERIODS` | `14` | Forecast periods on the page (14 = 7 days, day and night) |
| `CACHE_MINUTES` | `10` | How long current conditions and alerts are reused |
| `FORECAST_CACHE_MINUTES` | `30` | How long the forecast is reused |
| `TIMESTAMP_SOURCE` | `"now"` | Time in the one-line summary: `"now"` = fetch time, `"observation"` = station reading time |
| `TIME_FORMAT` | `"%I%p %m/%d/%Y"` | Time format in the one-line summary; `"%I:%M%p %m/%d/%Y"` shows minutes |

If you change the cron interval to longer than 10 minutes, raise `CACHE_MINUTES` to a little more than the new interval.

## Troubleshooting

Run a page by hand, the same way NomadNet does. Errors print here but not in the client:

```
~/.nomadnetwork/storage/pages/reticast.mu
```

To check the data fetch itself, run `~/scripts/reticast.py --debug`. It prints a `[debug]` line with the time each API call took. If you see no `[debug]` lines, the data came from the cache; delete `~/scripts/reticast_cache.json` and run it again.

| Symptom | Fix |
| --- | --- |
| Client says "No content available" | Run the page by hand (above) to see the error |
| New page doesn't show up, or the old home page still appears | Restart NomadNet (see [Restart NomadNet](#restart-nomadnet)) |
| Page shows the script's code | Make it executable, for example `chmod +x ~/.nomadnetwork/storage/pages/reticast.mu` |
| Page fails after editing on another computer | The file may have Windows line endings; fix with `sed -i 's/\r$//' <file>` |
| Page shows "Weather unavailable" | Check that `~/scripts/reticast.py` exists, and that the page's first line is your Python path (`which python3`) |
| `WARNING: cannot write cache file` | Make `~/scripts` writable by the NomadNet user |
| "(cached - update failed)" appears | The weather service was unreachable; it clears on the next good update |
| Weather never updates | Check `crontab -l` for the RetiCast job |

## Uninstall

```
crontab -l | grep -v reticast.py | crontab -
rm ~/.nomadnetwork/storage/pages/reticast.mu
rm ~/scripts/reticast.py ~/scripts/reticast_cache.json
```

If you installed the demo home page, restore your original:

```
cp ~/scripts/index.mu.backup.<date> ~/.nomadnetwork/storage/pages/index.mu
```

Then remove any links to `/page/reticast.mu` from your other pages, and restart NomadNet.

## Credits

Weather data comes from the [National Weather Service API](https://www.weather.gov/documentation/services-web-api). RetiCast is not affiliated with or endorsed by the National Weather Service.

## See also

[RetiSkip](<RetiSkip repo URL>): HF band conditions and solar data as a ready-to-install NomadNet page.
