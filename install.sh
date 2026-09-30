#!/usr/bin/env bash
# install.sh - install or upgrade RetiCast 2.1 on a NomadNet node.
#
# Run as the same user that runs NomadNet (no sudo).
#
#   ./install.sh                  walks you through each setting, one at a time
#   ./install.sh --location EM20fb --contact W1AW
#                                 uses the settings you give, shows everything
#                                 it will use, and asks before installing
#   ./install.sh --silent         no questions: uses the settings you give,
#                                 and defaults for the rest
#
# "Defaults" are your current settings when upgrading, so a silent upgrade
# never resets them; on a new install they're the built-in defaults.
# Run ./install.sh --help for all options.
#
# Safe to re-run. Upgrading backs up the old scripts, keeps visitors' saved
# places, and adds the cron job once.
#
# License: Unlicense (public domain).
set -euo pipefail

SRC="$(cd "$(dirname "$0")" && pwd)"
STAMP="$(date +%Y%m%d-%H%M%S)"

say()  { printf '%s\n' "$*"; }
warn() { printf 'WARNING: %s\n' "$*" >&2; }
die()  { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

usage() {
    cat <<'EOF'
Usage: ./install.sh [options]

With no options, the installer walks you through each setting. With options,
it shows the settings it will use and asks before installing.

Settings:
  --location PLACE          default location for visitors: grid square,
                            "City, ST", US ZIP code, or "lat,lon"
  --contact EMAIL_OR_CALL   sent to the weather services so they can reach you
  --units us|metric         units shown on the page (default: us)
  --display-name NAME       name on alert messages (default: RetiCast Alerts)
  --propagation-node HASH   LXMF propagation node for alert messages to people
                            who are offline, or "none" (default: none)

Alert message service:
  --start-after UNIT        systemd unit the alert service waits for, e.g.
                            reticulum-ready.service, or "network" for none
                            (default: detected; see below)

Paths (rarely needed):
  --scripts-dir DIR         default: ~/scripts
  --pages-dir DIR           default: ~/.nomadnetwork/storage/pages
  --python PATH             default: output of "which python3"

Other:
  -s, --silent              no questions; use the options given, and defaults
                            (or your current settings, when upgrading) for the rest
  -h, --help                show this help

The installer looks for how Reticulum is started on this machine: a
reticulum-ready.service (reticulum-readygate), whatever NomadNet's own service
waits for, or a service that runs rnsd. The alert service is set to wait for
the same thing, so it doesn't start before Reticulum is ready.

Options can also be written --name=value. The environment variables LOCATION,
GRID, CONTACT, UNITS, DISPLAY_NAME, PROPAGATION_NODE, SCRIPTS_DIR, PAGES_DIR
and PYTHON still work and count as options.
EOF
}

# --- options -----------------------------------------------------------
SILENT=0
GIVEN=0                       # were any settings given as options?
F_LOCATION=""; F_CONTACT=""; F_UNITS=""; F_DNAME=""; F_PROP=""
F_SCRIPTS=""; F_PAGES=""; F_PYTHON=""; F_AFTER=""

set_opt() {
    GIVEN=1
    case "$1" in
        --location)          F_LOCATION="$2" ;;
        --contact)           F_CONTACT="$2" ;;
        --units)             F_UNITS="$2" ;;
        --display-name)      F_DNAME="$2" ;;
        --propagation-node)  F_PROP="$2" ;;
        --scripts-dir)       F_SCRIPTS="$2" ;;
        --pages-dir)         F_PAGES="$2" ;;
        --python)            F_PYTHON="$2" ;;
        --start-after)       F_AFTER="$2" ;;
    esac
}

# environment variables (from RetiCast 2.0) count as options
[ -n "${LOCATION:-}" ] && set_opt --location "$LOCATION"
[ -z "${LOCATION:-}" ] && [ -n "${GRID:-}" ] && set_opt --location "$GRID"
[ -n "${CONTACT:-}" ] && set_opt --contact "$CONTACT"
[ -n "${UNITS:-}" ] && set_opt --units "$UNITS"
[ -n "${DISPLAY_NAME:-}" ] && set_opt --display-name "$DISPLAY_NAME"
[ -n "${PROPAGATION_NODE:-}" ] && set_opt --propagation-node "$PROPAGATION_NODE"
[ -n "${SCRIPTS_DIR:-}" ] && set_opt --scripts-dir "$SCRIPTS_DIR"
[ -n "${PAGES_DIR:-}" ] && set_opt --pages-dir "$PAGES_DIR"
[ -n "${PYTHON:-}" ] && set_opt --python "$PYTHON"

while [ $# -gt 0 ]; do
    case "$1" in
        -s|--silent) SILENT=1 ;;
        -h|--help) usage; exit 0 ;;
        --location|--contact|--units|--display-name|--propagation-node|--scripts-dir|--pages-dir|--python|--start-after)
            [ $# -ge 2 ] || die "$1 needs a value (see ./install.sh --help)"
            set_opt "$1" "$2"; shift ;;
        --location=*|--contact=*|--units=*|--display-name=*|--propagation-node=*|--scripts-dir=*|--pages-dir=*|--python=*|--start-after=*)
            set_opt "${1%%=*}" "${1#*=}" ;;
        *) die "Unknown option: $1 (see ./install.sh --help)" ;;
    esac
    shift
done

INTERACTIVE=0; [ "$SILENT" = 0 ] && [ "$GIVEN" = 0 ] && INTERACTIVE=1

# ask VAR "question" "default" [required]  (returns the trimmed answer)
ask() {
    local __var="$1" __q="$2" __def="$3" __req="${4:-}" __ans
    while :; do
        if [ -n "$__def" ]; then
            read -rp "$__q [$__def]: " __ans || die "No answer (input closed). Use options or --silent; see --help."
            __ans="${__ans:-$__def}"
        else
            read -rp "$__q: " __ans || die "No answer (input closed). Use options or --silent; see --help."
        fi
        __ans="$(printf '%s' "$__ans" | sed 's/^[[:space:]]*//; s/[[:space:]]*$//')"
        if [ -z "$__ans" ] && [ -n "$__req" ]; then
            say "  This one is needed."
            continue
        fi
        printf -v "$__var" '%s' "$__ans"
        return 0
    done
}

say "RetiCast 2.1 installer"
say ""

# --- paths and Python ----------------------------------------------------
SCRIPTS_DIR="${F_SCRIPTS:-$HOME/scripts}"
PAGES_DIR="${F_PAGES:-$HOME/.nomadnetwork/storage/pages}"
PYTHON="${F_PYTHON:-$(command -v python3 || true)}"
SCRIPTS_DIR="${SCRIPTS_DIR/#\~/$HOME}"; PAGES_DIR="${PAGES_DIR/#\~/$HOME}"

[ -f "$SRC/scripts/reticast.py" ] && [ -f "$SRC/scripts/reticast_notify.py" ] && [ -f "$SRC/pages/reticast.mu" ] \
    || die "Run this from the RetiCast folder (scripts/reticast.py and pages/reticast.mu are missing)."
[ -n "$PYTHON" ] && [ -x "$PYTHON" ] || die "python3 not found. Install Python 3.9+ or use --python /full/path/to/python3"
"$PYTHON" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' \
    || die "$PYTHON is older than 3.9 ($("$PYTHON" --version 2>&1))."
if [ ! -d "$PAGES_DIR" ]; then
    [ "$SILENT" = 1 ] && die "NomadNet pages folder not found at $PAGES_DIR. Use --pages-dir /path/to/pages"
    say "NomadNet's pages folder wasn't found at $PAGES_DIR."
    while [ ! -d "$PAGES_DIR" ]; do
        ask PAGES_DIR "Path to your NomadNet pages folder" "" required
        PAGES_DIR="${PAGES_DIR/#\~/$HOME}"
        [ -d "$PAGES_DIR" ] || say "  That folder doesn't exist."
    done
    say ""
fi

# --- current settings (when upgrading) -----------------------------------
OLD_LOCATION=""; OLD_CONTACT=""; OLD_UNITS=""; OLD_PROP=""; OLD_DNAME=""
if [ -f "$SCRIPTS_DIR/reticast.py" ] || [ -f "$SCRIPTS_DIR/reticast_notify.py" ]; then
    {
        IFS= read -r OLD_LOCATION || true
        IFS= read -r OLD_CONTACT || true
        IFS= read -r OLD_UNITS || true
        IFS= read -r OLD_PROP || true
        IFS= read -r OLD_DNAME || true
    } < <("$PYTHON" - "$SCRIPTS_DIR/reticast.py" "$SCRIPTS_DIR/reticast_notify.py" <<'PY'
import json, re, sys
def read(path):
    try:
        return open(path, encoding="utf-8").read()
    except OSError:
        return ""
main, notify = read(sys.argv[1]), read(sys.argv[2])
def find(pattern, s):
    m = re.search(pattern, s, re.M)
    return m.group(1).replace("\n", " ").strip() if m else ""
def literal(name, s):                     # a "..." setting, escapes and all
    m = re.search(rf'^{name} = ("(?:[^"\\]|\\.)*")', s, re.M)
    try:
        return str(json.loads(m.group(1))).replace("\n", " ").strip() if m else ""
    except ValueError:
        return ""
loc = literal("DEFAULT_LOCATION", main) or literal("GRIDSQUARE", main)   # 2.x or 1.x
contact = find(r'^USER_AGENT = "\([^,]*,\s*(.*?)\)"', main)
if contact == "you@example.com":
    contact = ""
units = find(r'^UNITS = "(.*?)"', main)
prop = find(r'^PROPAGATION_NODE = "([0-9a-fA-F]{32})"', notify).lower()
dname = literal("DISPLAY_NAME", notify)
for v in (loc, contact, units, prop, dname):
    print(v)
PY
)
fi
UPGRADE=0; [ -n "$OLD_LOCATION$OLD_CONTACT" ] && UPGRADE=1

# --- settings ---------------------------------------------------------
# Each setting: the option given, else the current setting, else the default.
src_of() {   # where a value came from, for the summary
    if [ -n "$1" ]; then echo "option"; elif [ -n "$2" ]; then echo "current setting"; else echo "default"; fi
}
S_LOCATION="$(src_of "$F_LOCATION" "$OLD_LOCATION")"
S_CONTACT="$(src_of "$F_CONTACT" "$OLD_CONTACT")"
S_UNITS="$(src_of "$F_UNITS" "$OLD_UNITS")"
S_DNAME="$(src_of "$F_DNAME" "$OLD_DNAME")"
S_PROP="$(src_of "$F_PROP" "$OLD_PROP")"
LOCATION="${F_LOCATION:-$OLD_LOCATION}"
CONTACT="${F_CONTACT:-$OLD_CONTACT}"
UNITS="${F_UNITS:-${OLD_UNITS:-us}}"
DNAME="${F_DNAME:-${OLD_DNAME:-RetiCast Alerts}}"
PROPAGATION_NODE="${F_PROP:-$OLD_PROP}"

valid_units() { case "$1" in us|US|metric|METRIC) return 0 ;; *) return 1 ;; esac; }
clean_prop()  { printf '%s' "$1" | tr 'A-F' 'a-f' | tr -d '<> '; }
valid_prop()  { [ -z "$1" ] || [ "$1" = "none" ] || [[ "$1" =~ ^[0-9a-f]{32}$ ]]; }

if [ "$INTERACTIVE" = 1 ]; then
    [ "$UPGRADE" = 1 ] && say "Found an existing RetiCast install. Press Enter to keep each current setting." && say ""
    say "1. Default location"
    say "   What visitors see before they save their own place."
    say "   Examples: EM20fb   Houston, TX   77002   29.76,-95.37"
    ask LOCATION "   Default location" "$LOCATION" required
    say ""
    say "2. Contact"
    say "   An email address or callsign. The weather services ask every app for a"
    say "   way to reach its operator. It's never shown to visitors."
    ask CONTACT "   Email or callsign" "$CONTACT" required
    say ""
    say "3. Units"
    say "   us (F, mph, inHg, miles) or metric (C, km/h, hPa, km)."
    while :; do
        ask UNITS "   Units" "$UNITS" required
        valid_units "$UNITS" && break
        say "  Please enter us or metric."
    done
    say ""
    say "4. Alert messages: display name"
    say "   The name people see on RetiCast's alert messages, e.g. \"W5PL - RetiCast Alerts\"."
    ask DNAME "   Display name" "$DNAME" required
    say ""
    say "5. Alert messages: propagation node"
    say "   An LXMF propagation node lets alerts reach people who are offline when"
    say "   they're issued. Enter its 32-character address, or \"none\" to deliver"
    say "   directly only."
    while :; do
        ask PROPAGATION_NODE "   Propagation node" "${PROPAGATION_NODE:-none}" required
        PROPAGATION_NODE="$(clean_prop "$PROPAGATION_NODE")"
        valid_prop "$PROPAGATION_NODE" && break
        say "  That isn't a 32-character address (0-9, a-f). Enter it again, or \"none\"."
    done
    say ""
    S_LOCATION=""; S_CONTACT=""; S_UNITS=""; S_DNAME=""; S_PROP=""   # all chosen just now
elif [ "$SILENT" = 1 ]; then
    if [ -z "$LOCATION" ]; then
        LOCATION="EM20fb"; S_LOCATION="default"
        warn "No location given; using the built-in default, $LOCATION. Change it with --location."
    fi
    if [ -z "$CONTACT" ]; then
        CONTACT="you@example.com"; S_CONTACT="default"
        warn "No contact given. The weather services ask for one; add it with --contact."
    fi
else
    # options were given: only ask for anything required that's still missing
    if [ -z "$LOCATION" ]; then
        say "No default location was given."
        ask LOCATION "Default location (e.g. EM20fb, Houston, TX, 77002)" "" required; S_LOCATION="entered"
    fi
    if [ -z "$CONTACT" ]; then
        say "No contact was given."
        ask CONTACT "Email or callsign for the weather services" "" required; S_CONTACT="entered"
    fi
fi

# validate (options and silent mode stop here on a bad value)
valid_units "$UNITS" || die "--units must be us or metric (got \"$UNITS\")."
case "$UNITS" in US) UNITS="us" ;; METRIC) UNITS="metric" ;; esac
PROPAGATION_NODE="$(clean_prop "$PROPAGATION_NODE")"
valid_prop "$PROPAGATION_NODE" || die "--propagation-node must be a 32-character LXMF address or none (got \"$PROPAGATION_NODE\")."
[ "$PROPAGATION_NODE" = "none" ] && PROPAGATION_NODE=""
DNAME="$(printf '%s' "$DNAME" | tr -d '"\\' | tr -d '\000-\037' | tr -s ' ' | cut -c1-64)"
[ -n "$DNAME" ] || DNAME="RetiCast Alerts"

# --- how Reticulum is started (for the alert service) ------------------------
# Services that use Reticulum fail if they start before it's ready, so the
# alert service waits for the same thing the rest of the stack waits for.
UNIT_DIRS="${RETICAST_UNIT_DIRS:-/etc/systemd/system /lib/systemd/system /usr/lib/systemd/system}"
unit_exists() {
    local d
    for d in $UNIT_DIRS; do [ -f "$d/$1" ] && return 0; done
    command -v systemctl >/dev/null 2>&1 && systemctl cat "$1" >/dev/null 2>&1
}
unit_files_running() {       # service files whose ExecStart runs $1
    local d f
    for d in $UNIT_DIRS; do
        for f in "$d"/*.service; do
            [ -f "$f" ] || continue
            case "$(basename "$f")" in reticast-notify.service) continue ;; esac
            grep -qE "^[[:space:]]*ExecStart=.*[/[:space:]=]$1([[:space:]]|$)" "$f" && echo "$f"
        done
    done
    return 0
}
RNS_UNIT=""; RNS_HOW=""; RNS_REQUIRE=0
detect_rns_unit() {
    if unit_exists reticulum-ready.service; then
        RNS_UNIT="reticulum-ready.service"; RNS_REQUIRE=1
        RNS_HOW="found reticulum-ready.service (a Reticulum readiness gate)"; return
    fi
    local f line u best score top
    for f in $(unit_files_running nomadnet); do            # copy NomadNet's own dependency
        declare -A required=() seen=()
        while IFS= read -r line; do
            case "$line" in Requires=*|After=*|BindsTo=*|Wants=*) ;; *) continue ;; esac
            for u in ${line#*=}; do
                case "$u" in *.service) ;; *) continue ;; esac
                echo "$u" | grep -qiE 'reticul|rnsd|rns' || continue
                seen["$u"]=1
                case "$line" in Requires=*|BindsTo=*) required["$u"]=1 ;; esac
            done
        done < <(sed 's/^[[:space:]]*//' "$f")
        # best: a unit NomadNet requires (a gate), then one named like a "ready" check
        best=""; top=-1
        for u in "${!seen[@]}"; do
            score=0
            [ -n "${required[$u]:-}" ] && score=$((score + 2))
            echo "$u" | grep -qi ready && score=$((score + 1))
            if [ "$score" -gt "$top" ] || { [ "$score" -eq "$top" ] && [[ "$u" < "$best" ]]; }; then
                best="$u"; top=$score
            fi
        done
        if [ -n "$best" ]; then
            RNS_UNIT="$best"; RNS_REQUIRE=0; [ -n "${required[$best]:-}" ] && RNS_REQUIRE=1
            RNS_HOW="NomadNet's service ($(basename "$f")) waits for it"; return
        fi
        unset required seen
    done
    for f in $(unit_files_running rnsd); do                 # a service that runs rnsd
        RNS_UNIT="$(basename "$f")"; RNS_REQUIRE=0
        RNS_HOW="it runs rnsd; consider a readiness gate (see README)"; return
    done
    RNS_HOW="no Reticulum service found; waits for the network only"
}
if [ -n "$F_AFTER" ]; then
    case "$F_AFTER" in
        network|none) RNS_UNIT=""; RNS_HOW="as given (network only)" ;;
        *.service|*.target)
            [[ "$F_AFTER" =~ ^[A-Za-z0-9@._:-]+$ ]] || die "--start-after must be a systemd unit name (got \"$F_AFTER\")."
            RNS_UNIT="$F_AFTER"; RNS_REQUIRE=1; RNS_HOW="as given" ;;
        *) die "--start-after must be a unit name like reticulum-ready.service, or network (got \"$F_AFTER\")." ;;
    esac
else
    detect_rns_unit
fi

# --- which Python runs the alert service ------------------------------------
# The alert service needs the rns and lxmf packages. They're often installed
# with pip --user, pipx or a virtual environment, so plain python3 can't always
# import them. If it can't, use the Python that NomadNet or rnsd runs with.
py_has_rns() { [ -x "$1" ] && "$1" -c 'import RNS, LXMF' >/dev/null 2>&1; }
exec_paths_of() {            # the programs service files run for $1
    local f
    for f in $(unit_files_running "$1"); do
        sed -n 's/^[[:space:]]*ExecStart=[-@+!:]*\([^[:space:]]*\).*/\1/p' "$f"
    done
    return 0
}
NOTIFY_PYTHON="$PYTHON"; NOTIFY_PY_HOW=""
if ! py_has_rns "$PYTHON"; then
    for exe in $(exec_paths_of nomadnet) $(exec_paths_of rnsd) \
               $(command -v nomadnet rnsd lxmd 2>/dev/null || true) \
               "$HOME/.local/bin/nomadnet" "$HOME/.local/bin/rnsd"; do
        [ -f "$exe" ] || continue
        first="$(head -n 1 "$exe" 2>/dev/null || true)"
        case "$first" in "#!"*) ;; *) continue ;; esac
        read -r interp arg _ <<< "${first#\#!}"
        case "$interp" in */env) interp="$(command -v "$arg" 2>/dev/null || true)" ;; esac
        if [ -n "$interp" ] && py_has_rns "$interp"; then
            NOTIFY_PYTHON="$interp"; NOTIFY_PY_HOW="the Python that runs $(basename "$exe")"
            break
        fi
    done
fi

# --- summary and confirmation --------------------------------------------
label() { [ -n "$1" ] && printf '  (%s)' "$1"; return 0; }
say "Settings:"
say "  Default location:  $LOCATION$(label "$S_LOCATION")"
say "  Contact:           $CONTACT$(label "$S_CONTACT")"
say "  Units:             $UNITS$(label "$S_UNITS")"
say "  Alert name:        $DNAME$(label "$S_DNAME")"
say "  Propagation node:  ${PROPAGATION_NODE:-none}$(label "$S_PROP")"
say "  Alert service:     starts after ${RNS_UNIT:-network-online.target}  ($RNS_HOW)"
[ -n "$NOTIFY_PY_HOW" ] && say "  Alert Python:      $NOTIFY_PYTHON  ($NOTIFY_PY_HOW)"
say "  Python:            $PYTHON ($("$PYTHON" --version 2>&1))"
say "  Scripts folder:    $SCRIPTS_DIR"
say "  Pages folder:      $PAGES_DIR"
say ""
if [ "$SILENT" = 0 ]; then
    read -rp "Ready to install with these settings? [Y/n]: " REPLY || die "No answer (input closed). Use --silent to install without questions."
    case "$REPLY" in
        ""|y|Y|yes|YES|Yes) ;;
        *) say "Nothing was changed."; exit 0 ;;
    esac
    say ""
fi

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
"$PYTHON" - "$SCRIPTS_DIR/reticast_notify.py.new" "$NOTIFY_PYTHON" "$PROPAGATION_NODE" "$DNAME" <<'PY'
import json, re, sys
path, python, prop, dname = sys.argv[1:]
s = open(path, encoding="utf-8").read()
s = re.sub(r"^#!.*", lambda m: "#!" + python, s, count=1)
for name, value in (("PROPAGATION_NODE", prop), ("DISPLAY_NAME", dname)):
    s, n = re.subn(rf"^{name} = .*?(\s+#.*)?$",
                   lambda m: f"{name} = {json.dumps(value, ensure_ascii=False)}" + (m.group(1) or ""),
                   s, count=1, flags=re.M)
    if n != 1:
        sys.exit(f"could not set {name}")
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

# --- stack control scripts ----------------------------------------------
# Scripts like reticulum-readygate's reloadstack.sh list the services that
# depend on Reticulum in a DEPENDENTS=(...) line. Offer to add ours.
add_to_stack_scripts() {
    local f real found=()
    declare -A seen_scripts=()
    for f in "$HOME"/*.sh "$HOME"/*/*stack*.sh "$HOME"/bin/*.sh "$HOME"/.local/bin/*.sh \
             "$SCRIPTS_DIR"/*.sh /usr/local/bin/*.sh; do
        [ -f "$f" ] || continue
        real="$(readlink -f "$f")"
        [ -z "${seen_scripts[$real]:-}" ] || continue
        seen_scripts["$real"]=1
        f="$real"                     # show, back up and edit the real file, not a link to it
        grep -qE '^[[:space:]]*DEPENDENTS=\(' "$f" || continue
        grep -q 'reticast-notify.service' "$f" && continue
        found+=("$f")
    done
    [ ${#found[@]} -gt 0 ] || return 0
    say ""
    say "These scripts restart or check the services that depend on Reticulum,"
    say "but don't include the alert service yet:"
    for f in "${found[@]}"; do say "    $f"; done
    local answer="n"
    if [ "$SILENT" = 0 ]; then
        read -rp "Add reticast-notify.service to their DEPENDENTS list? [Y/n]: " answer || answer="n"
        answer="${answer:-y}"
    fi
    case "$answer" in
        y|Y|yes|Yes|YES) ;;
        *) say "To add it yourself, put reticast-notify.service in the DEPENDENTS=(...) line of each."
           return 0 ;;
    esac
    for f in "${found[@]}"; do
        if [ ! -w "$f" ]; then
            say "  $f isn't writable by $(id -un); add reticast-notify.service there with sudo."
            continue
        fi
        cp "$f" "$f.bak.$STAMP"
        if "$PYTHON" - "$f" <<'PY'
import re, sys
path = sys.argv[1]
s = open(path, encoding="utf-8").read()
new, n = re.subn(r"^(\s*DEPENDENTS=\()([^)\n]*)\)",
                 lambda m: m.group(1) + (m.group(2).rstrip() + " " if m.group(2).strip() else "")
                           + "reticast-notify.service)", s, count=1, flags=re.M)
if n != 1:
    sys.exit(1)
open(path, "w", encoding="utf-8").write(new)
PY
        then
            say "  Added to $f (backup: $(basename "$f").bak.$STAMP)"
        else
            rm -f "$f.bak.$STAMP"
            say "  Couldn't edit $f automatically; add reticast-notify.service to its DEPENDENTS line."
        fi
    done
}

# --- alert messages service (optional) ----------------------------------
say ""
SERVICE="$SCRIPTS_DIR/reticast-notify.service"
if [ -n "$RNS_UNIT" ] && [ "$RNS_REQUIRE" = 1 ]; then
    DEPS="After=$RNS_UNIT
Requires=$RNS_UNIT"
elif [ -n "$RNS_UNIT" ]; then
    DEPS="After=network-online.target $RNS_UNIT
Wants=network-online.target $RNS_UNIT"
else
    DEPS="After=network-online.target
Wants=network-online.target"
fi
cat > "$SERVICE" <<UNIT
[Unit]
Description=RetiCast weather alert messages (LXMF)
$DEPS

[Service]
Type=simple
User=$(id -un)
ExecStart=$NOTIFY_PYTHON $SCRIPTS_DIR/reticast_notify.py
Restart=on-failure
RestartSec=10
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
UNIT
if py_has_rns "$NOTIFY_PYTHON"; then
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
    [ -z "$PROPAGATION_NODE" ] && say "Tip: use --propagation-node so people who are offline still get alerts (see README)."
    INSTALLED_UNIT=""
    for d in $UNIT_DIRS; do [ -f "$d/reticast-notify.service" ] && INSTALLED_UNIT="$d/reticast-notify.service" && break; done
    if [ -n "$INSTALLED_UNIT" ] && ! cmp -s "$SERVICE" "$INSTALLED_UNIT"; then
        say ""
        say "The installed service file ($INSTALLED_UNIT) differs from the new one"
        say "(for example, what it waits for before starting). To update it:"
        say "    sudo cp $SERVICE $INSTALLED_UNIT"
        say "    sudo systemctl daemon-reload"
        say "    sudo systemctl restart reticast-notify"
    fi
    add_to_stack_scripts
else
    say "Alert messages need the rns and lxmf Python packages. They weren't found for $PYTHON,"
    say "or for the Python NomadNet or rnsd runs with. Use --python to point at the right one."
    say "The weather page works without them."
fi

say ""
say "Done. Open /page/reticast.mu on your node. If this is a new install,"
say "restart NomadNet so it sees the new page."
say "To link to it from your own pages:"
say '    `[Weather`:/page/reticast.mu]'
exit 0
