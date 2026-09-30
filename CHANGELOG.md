# Changelog

## 2.1

- Alert messages: visitors can get an LXMF message when the National Weather Service issues an alert for their default location. They choose warnings only, warnings and watches, warnings/watches/advisories, or everything, and can limit it to Severe and Extreme alerts.
- Messages go to the visitor's own LXMF address, worked out from the identity they browse with, or to another address they confirm with a code.
- Replying STOP turns messages off.
- My Places shows each visitor's LXMF address, whether it has been found on the network, and what happened to their last message (delivered, left at the propagation node, or waiting).
- Alerts follow the visitor's default location, and the section says so. The place search on My Places has its own "Add a place" heading.
- New optional service, `reticast_notify.py`, with a ready-made systemd service file from the installer.
- The alert service's systemd file waits for Reticulum the same way the rest of your stack does: a readiness gate such as reticulum-readygate's `reticulum-ready.service`, whatever NomadNet's service waits for, or the service that runs `rnsd`. Override with `--start-after`. The installer also offers to add the alert service to stack control scripts' `DEPENDENTS` lists, and says when an installed service file is out of date.
- Long forecast descriptions are no longer cut off mid-word.
- If the system's `python3` can't import `rns` and `lxmf` (for example, Reticulum is in a virtual environment), the installer runs the alert service with the Python that NomadNet or `rnsd` uses.
- The installer walks you through each setting, including the alert messages display name and propagation node. Given options (`--location`, `--contact`, `--units`, `--display-name`, `--propagation-node`, and paths), it shows the settings it will use and asks before installing. `--silent` installs without any questions. Upgrades keep all current settings, including the display name and propagation node.

## 2.0

- Visitors can look up the weather for any place: city, city and state, US ZIP code (including ZIP+4), grid square, or latitude/longitude.
- Places outside the US are supported through Open-Meteo. US places still use the National Weather Service, with alerts.
- Visitors who identify to the node can save a default location and up to 5 favorites, and choose whether RetiCast opens on their default or on an overview of all their saved places.
- New Overview and My Places pages.
- `GRIDSQUARE` is replaced by `DEFAULT_LOCATION`, which accepts any location. It's what guests see.
- The cron job now also keeps visitors' saved places fresh, and cleans out old data.
- The page tells clients not to cache it, since it's personal to each visitor.
- "Read full alert" now works for any US place, not just the node's default.
- The NWS product code line (e.g. `AQAHGX`) is no longer shown in alerts.
- New `--check` and `--search` commands.
- The installer keeps your settings when upgrading, including from 1.x.
- `get_weather_string()`, `get_weather_title()`, and `get_weather_micron()` still work, for the node's default location.

## 1.x

- Weather for one grid square from the National Weather Service: alerts, current conditions, and a 7-day forecast, plus a one-line summary for the home page.
