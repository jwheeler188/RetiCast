#!/usr/bin/python3
# reticast.mu - RetiCast full weather page for NomadNet.
# The first line must be the full path to your Python (run: which python3).
import os
import sys

SCRIPTS_DIR = os.path.expanduser("~/scripts")
sys.path.insert(0, SCRIPTS_DIR)

# A "Read full alert" link opens this same page with an alert id attached.
alert = os.environ.get("var_alert", "").strip()

try:
    import reticast
    title = reticast.get_weather_title()
    if alert:
        heading = ">Weather Alert for " + title
        body = reticast.get_alert_micron(alert)
    else:
        heading = ">Weather for " + title
        body = reticast.get_weather_micron()
except Exception as e:
    heading = ">RetiCast"
    body = f"Weather unavailable ({type(e).__name__})"

# Match the rest of your site: if SCRIPTS_DIR has a site_parts.py with a
# print_top() function, it prints your own header and menu above this page.
try:
    from site_parts import print_top
    print_top()
except ImportError:
    pass
except Exception as e:
    print(f"`Ff00[site header error: {type(e).__name__}]`f")

print(heading)
print()
print(body)
print()
if not alert:
    print("`[Back to home`:/page/index.mu]")
