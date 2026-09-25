#!/usr/bin/env bash
# install.sh - demo deployment of RetiCast, a live weather line for NomadNet pages.
#
# Run as the same user that runs NomadNet:   ./install.sh
# Non-interactive:   GRID=FN31pr CONTACT=you@example.com ./install.sh
# Optional overrides: SCRIPTS_DIR, PAGES_DIR, PYTHON
#
# Safe to re-run: existing header/body/about files are never overwritten,
# the old index.mu is backed up, and the cron job is only added once.
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
[ -n "$PYTHON" ] && [ -x "$PYTHON" ] || die "python3 not found. Install Python 3.9+ or set PYTHON=/full/path/to/python3"
"$PYTHON" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' \
    || die "$PYTHON is older than 3.9 ($("$PYTHON" --version 2>&1))."
[ -d "$PAGES_DIR" ] || die "NomadNet pages folder not found at $PAGES_DIR. Set PAGES_DIR=/path/to/pages"

say "Python:        $PYTHON ($("$PYTHON" --version 2>&1))"
say "Scripts folder: $SCRIPTS_DIR"
say "Pages folder:   $PAGES_DIR"
say ""

# --- settings ---------------------------------------------------------
GRID="${GRID:-}"
if [ -z "$GRID" ]; then
    read -rp "Your Maidenhead grid square [FN31pr]: " GRID
    GRID="${GRID:-FN31pr}"
fi
[[ "$GRID" =~ ^[A-Ra-r]{2}([0-9]{2}([A-Xa-x]{2}([0-9]{2})?)?)?$ ]] || die "'$GRID' is not a valid grid square (e.g. FN31 or FN31pr)."

CONTACT="${CONTACT:-}"
while [ -z "$CONTACT" ]; do
    read -rp "Email or callsign for the NWS User-Agent (required): " CONTACT
done

# --- weather script ---------------------------------------------------
mkdir -p "$SCRIPTS_DIR"
if [ -f "$SCRIPTS_DIR/reticast.py" ]; then
    cp "$SCRIPTS_DIR/reticast.py" "$SCRIPTS_DIR/reticast.py.bak.$STAMP"
    say "Backed up existing reticast.py"
fi
cp "$SRC/scripts/reticast.py" "$SCRIPTS_DIR/reticast.py"

"$PYTHON" - "$SCRIPTS_DIR/reticast.py" "$PYTHON" "$GRID" "$CONTACT" <<'PY'
import re, sys
path, python, grid, contact = sys.argv[1:]
contact = contact.replace("\\", "").replace('"', "")
s = open(path, encoding="utf-8").read()
s = re.sub(r"^#!.*", lambda m: "#!" + python, s, count=1)
s = re.sub(r'^GRIDSQUARE = ".*?"', lambda m: f'GRIDSQUARE = "{grid}"', s, count=1, flags=re.M)
s = re.sub(r'^USER_AGENT = ".*?"',
           lambda m: f'USER_AGENT = "(RetiCast, {contact})"', s, count=1, flags=re.M)
open(path, "w", encoding="utf-8").write(s)
PY
chmod +x "$SCRIPTS_DIR/reticast.py"
rm -f "$SCRIPTS_DIR/reticast_cache.json"
say "Installed $SCRIPTS_DIR/reticast.py (grid $GRID)"

# --- page parts (never overwrite your edits) --------------------------
for f in index_header.mu index_body.mu; do
    if [ -e "$SCRIPTS_DIR/$f" ]; then
        say "Kept existing $SCRIPTS_DIR/$f"
    else
        cp "$SRC/scripts/$f" "$SCRIPTS_DIR/$f"
        say "Installed demo $SCRIPTS_DIR/$f"
    fi
done

# --- pages ------------------------------------------------------------
if [ -e "$PAGES_DIR/index.mu" ]; then
    if grep -q "from reticast import\|from nomadweather import" "$PAGES_DIR/index.mu"; then
        say "index.mu is already a RetiCast page script; updating it"
    else
        cp "$PAGES_DIR/index.mu" "$SCRIPTS_DIR/index.mu.backup.$STAMP"
        say "Backed up your old index.mu to $SCRIPTS_DIR/index.mu.backup.$STAMP"
    fi
fi
"$PYTHON" - "$SRC/pages/index.mu" "$PAGES_DIR/index.mu" "$PYTHON" "$SCRIPTS_DIR" <<'PY'
import re, sys
src, dst, python, scripts_dir = sys.argv[1:]
s = open(src, encoding="utf-8").read()
s = re.sub(r"^#!.*", lambda m: "#!" + python, s, count=1)
s = re.sub(r'^PARTS_DIR = .*$', lambda m: f"PARTS_DIR = {scripts_dir!r}", s, count=1, flags=re.M)
open(dst, "w", encoding="utf-8").write(s)
PY
chmod +x "$PAGES_DIR/index.mu"
say "Installed $PAGES_DIR/index.mu"

if [ -e "$PAGES_DIR/about.mu" ]; then
    say "Kept existing $PAGES_DIR/about.mu"
else
    cp "$SRC/pages/about.mu" "$PAGES_DIR/about.mu"
    say "Installed demo $PAGES_DIR/about.mu"
fi

# --- test run ---------------------------------------------------------
say ""
say "Test run:"
"$PYTHON" "$SCRIPTS_DIR/reticast.py" --debug || warn "Test run failed; see output above."
[ -w "$SCRIPTS_DIR" ] || warn "$SCRIPTS_DIR is not writable by $(whoami); the cache file cannot be saved."

# --- cron -------------------------------------------------------------
say ""
JOB="*/5 * * * * $PYTHON $SCRIPTS_DIR/reticast.py > /dev/null 2>&1"
if ! command -v crontab >/dev/null 2>&1; then
    warn "crontab not found. Add this job manually:  $JOB"
elif crontab -l 2>/dev/null | grep -Fq "nomadweather.py"; then
    crontab -l 2>/dev/null | grep -Fv "nomadweather.py" | { cat; echo "$JOB"; } | crontab -
    say "Replaced old nomadweather.py cron job with RetiCast"
elif crontab -l 2>/dev/null | grep -Fq "$SCRIPTS_DIR/reticast.py"; then
    say "Cron job already present (crontab -l to view)"
else
    (crontab -l 2>/dev/null; echo "$JOB") | crontab -
    say "Added cron job: refresh every 5 minutes"
fi

say ""
say "Done. Restart NomadNet so it picks up the new pages, for example:"
say "    sudo systemctl restart nomadnet     (or your own service name / start method)"
say "Then open your node in a NomadNet client to see the weather line."
say "Edit your page in $SCRIPTS_DIR/index_header.mu and $SCRIPTS_DIR/index_body.mu."
