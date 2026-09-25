#!/usr/bin/python3
# index.mu (RetiCast) - prints the site header, the live weather line, then the body.
# Edit the page itself in ~/scripts/index_header.mu and ~/scripts/index_body.mu.
# The first line must be the full path to your Python (run: which python3).
import os
import sys

PARTS_DIR = os.path.expanduser("~/scripts")
sys.path.insert(0, PARTS_DIR)

def read_part(name):
    with open(os.path.join(PARTS_DIR, name), encoding="utf-8") as f:
        return f.read()

try:
    from reticast import get_weather_string
    weather = get_weather_string()
except Exception as e:
    weather = f"Weather unavailable ({type(e).__name__})"

print(read_part("index_header.mu"), end="")
print("`c")
print("`F0ff`!Weather:`!`Fddd " + weather)
print("`a")
print(read_part("index_body.mu"), end="")
