#!/usr/bin/env bash
# install.sh - install or upgrade RetiCast 2.1 on a NomadNet node.
#
# Run as the same user that runs NomadNet (no sudo):   ./install.sh
#
# Non-interactive:
#   LOCATION=EM20fb CONTACT=you@example.com ./install.sh
#
# Options (all optional):
#   LOCATION     default location for visitors: grid square, "City, ST", ZIP, or "lat,lon"
#   GRID         same as LOCATION (kept for RetiCast 1.x installs)
#   CONTACT      email or callsign sent to the weather services
#   UNITS        "us" or "metric"
#   PROPAGATION_NODE  LXMF propagation node for alert messages to offline people
#   SCRIPTS_DIR  default ~/scripts
#   PAGES_DIR    default ~/.nomadnetwork/storage/pages
#   PYTHON       default: output of "which python3"
#
# Safe to re-run. Upgrading keeps your location, contact and units, backs up
# the old reticast.py, keeps visitors' saved places, and adds the cron job once.
#
# License: Unlicense (public domain).
set -euo pipefail

SRC="$(cd "$(dirname "$0")" && pwd)"
SCRIPTS_DIR="${SCRIPTS_DIR:-$HOME/scripts}"
PAGES_DIR="${PAGES_DIR:-$HOME/.nomadnetwork/storage/pages}"
PYTHON="${PYTHON:-$(command -v python3 || true)}"
STAMP="$(date +%Y%m%d-%H%M%S)"

say()  { printf '%s\n' "$*"; }
warn() { printf 'WARNING: %s\n' "$*" >&2; }
die()  { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

# --- checks -----------------------------------------------------------
[ -f "$SRC/scripts/reticast.py" ] && [ -f "$SRC/scripts/reticast_notify.py" ] && [ -f "$SRC/pages/reticast.mu" ] \
    || die "Run this from the RetiCast folder (scripts/reticast.py and pages/reticast.mu are missing)."
[ -n "$PYTHON" ] && [ -x "$PYTHON" ] || die "python3 not found. Install Python 3.9+ or set PYTHON=/full/path/to/python3"
"$PYTHON" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' \
    || die "$PYTHON is older than 3.9 ($("$PYTHON" --version 2>&1))."
[ -d "$PAGES_DIR" ] || die "NomadNet pages folder not found at $PAGES_DIR. Set PAGES_DIR=/path/to/pages"

say "RetiCast 2.1 installer"
say "Python:         $PYTHON ($("$PYTHON" --version 2>&1))"
say "Scripts folder: $SCRIPTS_DIR"
say "Pages folder:   $PAGES_DIR"
say ""

# --- current settings (when upgrading) --------------------------------
OLD_LOCATION=""; OLD_CONTACT=""; OLD_UNITS=""
if [ -f "$SCRIPTS_DIR/reticast.py" ]; then
    {
        IFS= read -r OLD_LOCATION || true
        IFS= read -r OLD_CONTACT || true
        IFS= read -r OLD_UNITS || true
    } < <("$PYTHON" - "$SCRIPTS_DIR/reticast.py" <<'PY'
import re, sys
try:
    s = open(sys.argv[1], encoding="utf-8").read()
except OSError:
    s = ""
def find(pattern):
    m = re.search(pattern, s, re.M)
    return m.group(1).replace("\n", " ").strip() if m else ""
loc = find(r'^DEFAULT_LOCATION = "(.*?)"') or find(r'^GRIDSQUARE = "(.*?)"')   # 2.x or 1.x
contact = find(r'^USER_AGENT = "\([^,]*,\s*(.*?)\)"')
if contact == "you@example.com":
    contact = ""
units = find(r'^UNITS = "(.*?)"')
print(loc); print(contact); print(units)
PY
)
    [ -n "$OLD_LOCATION$OLD_CONTACT" ] && say "Found an existing RetiCast install; its settings are offered as defaults."
fi

OLD_PROP=""
if [ -f "$SCRIPTS_DIR/reticast_notify.py" ]; then
    OLD_PROP="$("$PYTHON" - "$SCRIPTS_DIR/reticast_notify.py" <<'PY'
import re, sys
try:
    s = open(sys.argv[1], encoding="utf-8").read()
except OSError:
    s = ""
m = re.search(r'^PROPAGATION_NODE = "([0-9a-fA-F]{32})"', s, re.M)
print(m.group(1).lower() if m else "")
PY
)"
fi

# --- settings ---------------------------------------------------------
LOCATION="${LOCATION:-${GRID:-}}"
if [ -z "$LOCATION" ]; then
    say "Default location: what visitors see before they save their own."
    say "  Examples: EM20fb   Houston, TX   77002   29.76,-95.37"
    while [ -z "$LOCATION" ]; do
        if [ -n "$OLD_LOCATION" ]; then
            read -rp "Default location [$OLD_LOCATION]: " LOCATION
            LOCATION="${LOCATION:-$OLD_LOCATION}"
        else
            read -rp "Default location (required): " LOCATION
        fi
    done
fi

CONTACT="${CONTACT:-}"
while [ -z "$CONTACT" ]; do
    if [ -n "$OLD_CONTACT" ]; then
        read -rp "Email or callsign sent to the weather services [$OLD_CONTACT]: " CONTACT
        CONTACT="${CONTACT:-$OLD_CONTACT}"
    else
        read -rp "Email or callsign sent to the weather services (required): " CONTACT
    fi
done

PROPAGATION_NODE="${PROPAGATION_NODE:-$OLD_PROP}"
PROPAGATION_NODE="$(printf '%s' "$PROPAGATION_NODE" | tr 'A-F' 'a-f' | tr -d '<> ')"
if [ -n "$PROPAGATION_NODE" ] && ! [[ "$PROPAGATION_NODE" =~ ^[0-9a-f]{32}$ ]]; then
    die "PROPAGATION_NODE must be a 32-character LXMF address (got \"$PROPAGATION_NODE\")."
fi

UNITS="${UNITS:-${OLD_UNITS:-us}}"
case "$UNITS" in
    us|US) UNITS="us" ;;
    metric|METRIC) UNITS="metric" ;;
    *) die "UNITS must be \"us\" or \"metric\" (got \"$UNITS\")." ;;
esac

# --- script -----------------------------------------------------------
mkdir -p "$SCRIPTS_DIR"
if [ -f "$SCRIPTS_DIR/reticast.py" ]; then
    cp "$SCRIPTS_DIR/reticast.py" "$SCRIPTS_DIR/reticast.py.bak.$STAMP"
    say "Backed up the existing reticast.py to reticast.py.bak.$STAMP"
fi
cp "$SRC/scripts/reticast.py" "$SCRIPTS_DIR/reticast.py.new"
"$PYTHON" - "$SCRIPTS_DIR/reticast.py.new" "$PYTHON" "$LOCATION" "$CONTACT" "$UNITS" <<'PY'
import json, re, sys
path, python, location, contact, units = sys.argv[1:]
clean = lambda v: re.sub(r'[\\"\x00-\x1f]', "", v).strip()
location, contact = clean(location), clean(contact)
s = open(path, encoding="utf-8").read()
def setting(name, value):
    global s
    s, n = re.subn(rf"^{name} = .*?(\s+#.*)?$",
                   lambda m: f"{name} = {json.dumps(value, ensure_ascii=False)}" + (m.group(1) or ""),
                   s, count=1, flags=re.M)
    if n != 1:
        sys.exit(f"could not set {name}")
s = re.sub(r"^#!.*", lambda m: "#!" + python, s, count=1)
setting("DEFAULT_LOCATION", location)
setting("USER_AGENT", f"(RetiCast, {contact})")
setting("UNITS", units)
open(path, "w", encoding="utf-8").write(s)
PY
chmod +x "$SCRIPTS_DIR/reticast.py.new"
mv "$SCRIPTS_DIR/reticast.py.new" "$SCRIPTS_DIR/reticast.py"
say "Installed $SCRIPTS_DIR/reticast.py"

if [ -f "$SCRIPTS_DIR/reticast_notify.py" ]; then
    cp "$SCRIPTS_DIR/reticast_notify.py" "$SCRIPTS_DIR/reticast_notify.py.bak.$STAMP"
fi
cp "$SRC/scripts/reticast_notify.py" "$SCRIPTS_DIR/reticast_notify.py.new"
"$PYTHON" - "$SCRIPTS_DIR/reticast_notify.py.new" "$PYTHON" "$PROPAGATION_NODE" <<'PY'
import re, sys
path, python, prop = sys.argv[1:]
s = open(path, encoding="utf-8").read()
s = re.sub(r"^#!.*", lambda m: "#!" + python, s, count=1)
s, n = re.subn(r'^PROPAGATION_NODE = ".*?"', lambda m: f'PROPAGATION_NODE = "{prop}"', s, count=1, flags=re.M)
if n != 1:
    sys.exit("could not set PROPAGATION_NODE")
open(path, "w", encoding="utf-8").write(s)
PY
chmod +x "$SCRIPTS_DIR/reticast_notify.py.new"
mv "$SCRIPTS_DIR/reticast_notify.py.new" "$SCRIPTS_DIR/reticast_notify.py"
say "Installed $SCRIPTS_DIR/reticast_notify.py"

# RetiCast 1.x cache (2.0 keeps its data in reticast_data/)
if [ -f "$SCRIPTS_DIR/reticast_cache.json" ]; then
    rm -f "$SCRIPTS_DIR/reticast_cache.json"
    say "Removed the old RetiCast 1.x cache"
fi
# a changed DEFAULT_LOCATION is looked up again automatically; nothing else to clear

# --- page -------------------------------------------------------------
if [ -e "$PAGES_DIR/reticast.mu" ] && ! grep -qE "import reticast|from reticast import" "$PAGES_DIR/reticast.mu"; then
    cp "$PAGES_DIR/reticast.mu" "$SCRIPTS_DIR/reticast.mu.backup.$STAMP"
    say "Backed up an unrelated reticast.mu to $SCRIPTS_DIR/reticast.mu.backup.$STAMP"
fi
"$PYTHON" - "$SRC/pages/reticast.mu" "$PAGES_DIR/reticast.mu" "$PYTHON" "$SCRIPTS_DIR" <<'PY'
import re, sys
src, dst, python, scripts_dir = sys.argv[1:]
s = open(src, encoding="utf-8").read()
s = re.sub(r"^#!.*", lambda m: "#!" + python, s, count=1)
s, n = re.subn(r"^SCRIPTS_DIR = .*$", lambda m: f"SCRIPTS_DIR = {scripts_dir!r}", s, count=1, flags=re.M)
if n != 1:
    sys.exit("could not set SCRIPTS_DIR in reticast.mu")
open(dst, "w", encoding="utf-8").write(s)
PY
chmod +x "$PAGES_DIR/reticast.mu"
say "Installed $PAGES_DIR/reticast.mu"

# --- test run ---------------------------------------------------------
say ""
[ -w "$SCRIPTS_DIR" ] || warn "$SCRIPTS_DIR is not writable by $(whoami); RetiCast can't save its data."
say "Checking the default location:"
if ! "$PYTHON" "$SCRIPTS_DIR/reticast.py" --check; then
    warn "Couldn't look up \"$LOCATION\". Check the spelling (try adding a state, e.g. \"Paris, TX\"),"
    warn "or your internet connection, then run the installer again."
fi
say ""
say "Test run:"
"$PYTHON" "$SCRIPTS_DIR/reticast.py" --debug || warn "Test run failed; see output above."

# --- cron: refresh every 5 minutes so pages load instantly --------------
say ""
JOB="*/5 * * * * $PYTHON $SCRIPTS_DIR/reticast.py > /dev/null 2>&1"
if ! command -v crontab >/dev/null 2>&1; then
    warn "crontab not found. Add this line to your scheduler yourself:"
    say "    $JOB"
elif crontab -l 2>/dev/null | grep -Fxq "$JOB"; then
    say "Cron job already present."
else
    # replace any older RetiCast / NomadWeather job instead of adding a second one
    { crontab -l 2>/dev/null | grep -Fv "reticast.py" | grep -Fv "nomadweather.py" || true; echo "$JOB"; } | crontab -
    say "Cron job installed (every 5 minutes)."
fi

# --- alert messages service (optional) ----------------------------------
say ""
SERVICE="$SCRIPTS_DIR/reticast-notify.service"
cat > "$SERVICE" <<UNIT
[Unit]
Description=RetiCast weather alert messages (LXMF)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$(id -un)
ExecStart=$PYTHON $SCRIPTS_DIR/reticast_notify.py
Restart=on-failure
RestartSec=10
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
UNIT
if "$PYTHON" -c 'import RNS, LXMF' >/dev/null 2>&1; then
    say "Alert messages (optional): a service file is ready at $SERVICE"
    if command -v systemctl >/dev/null 2>&1 && systemctl is-active --quiet reticast-notify 2>/dev/null; then
        say "The alert message service is running; restart it to use the new version:"
        say "    sudo systemctl restart reticast-notify"
    else
        say "To turn on alert messages, run:"
        say "    sudo cp $SERVICE /etc/systemd/system/"
        say "    sudo systemctl daemon-reload"
        say "    sudo systemctl enable --now reticast-notify"
    fi
    [ -z "$PROPAGATION_NODE" ] && say "Tip: set PROPAGATION_NODE so people who are offline still get alerts (see README)."
else
    say "Alert messages need the rns and lxmf Python packages, which weren't found for $PYTHON."
    say "The weather page works without them."
fi

say ""
say "Done. Open /page/reticast.mu on your node. If this is a new install,"
say "restart NomadNet so it sees the new page."
say "To link to it from your own pages:"
say '    `[Weather`:/page/reticast.mu]'
exit 0
