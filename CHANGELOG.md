# Changelog

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
