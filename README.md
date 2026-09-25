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

## Install options

There are two ways to install RetiCast. Both give the same result:

- **Quick install:** run `install.sh`, which asks two questions and does everything for you. Best for most people.
- **Manual install:** copy the files and set things up by hand. Use this if you want to see every step before it happens, or if your setup is unusual.

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

### Restart NomadNet

NomadNet registers its pages when it starts, so restart it after installing to make sure the new and updated pages are picked up. Later edits to your header and body files show up without a restart. If NomadNet runs as a systemd service:

```
sudo systemctl restart nomadnet
```

Use your own unit name if it's different (check with `systemctl list-units | grep -i nomad`), or `systemctl --user restart nomadnet` for a user service. If you start NomadNet by hand, stop it and start it again.

Then open your node in a NomadNet client. The demo home page shows a header, the weather line, and a small menu with an About page.

### What the installer does

1. Checks for Python 3.9+ and your NomadNet pages folder (`~/.nomadnetwork/storage/pages`).
2. Installs `reticast.py` in `~/scripts` with your grid square, contact, and full Python path.
3. Installs the demo header and body in `~/scripts`. Existing files are kept, never overwritten.
4. Backs up your current `index.mu` to `~/scripts/index.mu.backup.<date>`, then installs the RetiCast page script.
5. Adds a demo `about.mu` if you don't already have one.
6. Runs a test fetch with timing output.
7. Adds a cron job that refreshes the weather every 5 minutes (only once, even if you rerun it).

### Installer options

Set any of these before `./install.sh` to change how it runs:

| Option | Default | What it does |
| --- | --- | --- |
| `GRID` | asks | Your grid square; skips the question |
| `CONTACT` | asks | Your email or callsign for NWS; skips the question |
| `SCRIPTS_DIR` | `~/scripts` | Where `reticast.py` and the header and body files go |
| `PAGES_DIR` | `~/.nomadnetwork/storage/pages` | Your NomadNet pages folder |
| `PYTHON` | output of `which python3` | Python to use (3.9 or newer) |

For example, to use a specific Python and a different scripts folder:

```
PYTHON=/usr/bin/python3 SCRIPTS_DIR=~/reticast-files ./install.sh
```

It's safe to run the installer again, for example to change your grid square. It keeps your own header, body, and About page, and it won't add a second cron job.

## Manual install

If you'd rather not run `install.sh`, do the same steps by hand from the repo folder, as the NomadNet user.

1. Copy the script and demo files. If you already have your own `index_header.mu` or `index_body.mu` in `~/scripts`, copy only `reticast.py` so they aren't overwritten.
   ```
   mkdir -p ~/scripts
   cp scripts/reticast.py scripts/index_header.mu scripts/index_body.mu ~/scripts/
   chmod +x ~/scripts/reticast.py
   ```
2. Edit the settings at the top of `~/scripts/reticast.py`: set `GRIDSQUARE` to your grid square and put your email or callsign in `USER_AGENT`.
3. Find your full Python path. It must be version 3.9 or newer:
   ```
   which python3
   python3 --version
   ```
   NomadNet runs page scripts without your login shell, so use this full path (for example `/usr/bin/python3`) in the first line of `pages/index.mu`, in the form `#!/usr/bin/python3`.
4. Back up your current home page, then install the page script and demo About page:
   ```
   cp ~/.nomadnetwork/storage/pages/index.mu ~/scripts/index.mu.backup
   cp pages/index.mu pages/about.mu ~/.nomadnetwork/storage/pages/
   chmod +x ~/.nomadnetwork/storage/pages/index.mu
   ```
5. Test it. You should see `[debug]` timing lines, then the weather line:
   ```
   ~/scripts/reticast.py --debug
   ```
6. Add the cron job, using your Python path from step 3. Then confirm it with `crontab -l`:
   ```
   (crontab -l 2>/dev/null; echo "*/5 * * * * /usr/bin/python3 $HOME/scripts/reticast.py > /dev/null 2>&1") | crontab -
   ```
7. Restart NomadNet so it picks up the new pages, as described under [Restart NomadNet](#restart-nomadnet).

To use your existing page instead of the demo, split your old `index.mu` into two files in `~/scripts`: everything above where the weather should appear goes in `index_header.mu`, and everything below goes in `index_body.mu`.

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
| New page doesn't show up, or the old home page still appears | Restart NomadNet (see [Restart NomadNet](#restart-nomadnet)) |
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
