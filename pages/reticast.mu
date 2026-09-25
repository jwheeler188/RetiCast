#!/usr/bin/python3
# reticast.mu - RetiCast full weather page for NomadNet.
# The first line must be the full path to your Python (run: which python3).
import os
import sys

SCRIPTS_DIR = os.path.expanduser("~/scripts")
sys.path.insert(0, SCRIPTS_DIR)

try:
    from reticast import get_weather_title, get_weather_micron
    title = get_weather_title()
    body = get_weather_micron()
except Exception as e:
    title = "RetiCast"
    body = f"Weather unavailable ({type(e).__name__})"

print(">Weather for " + title)
print()
print(body)
print()
print("`[Back to home`:/page/index.mu]")
