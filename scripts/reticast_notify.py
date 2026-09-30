#!/usr/bin/env python3
"""
reticast_notify.py - RetiCast alert messages over LXMF.

Sends an LXMF message to each visitor who turned on alert messages (on the
RetiCast My Places page) when the National Weather Service issues an alert
for their default location, filtered by the level they chose.

Run it as a background service, as the same user that runs NomadNet, next to
reticast.py (see reticast-notify.service). Needs the "rns" and "lxmf" Python
packages, which NomadNet already installs.

    ./reticast_notify.py            run the service
    ./reticast_notify.py --address  show this service's LXMF address and settings

How it works:
  - Every CHECK_MINUTES it reads the visitors who turned messages on, fetches
    active NWS alerts for their default locations, and sends each new alert
    that matches their choice once. Updates to an alert they already got
    aren't sent again, unless the alert type changes (e.g. watch -> warning).
  - A visitor's messages go to their own LXMF address (worked out from the
    identity they browse with) unless they confirmed a different address.
  - Test and confirmation-code messages from the page arrive through
    reticast_data/notify/outbox/.
  - If someone's address isn't known on the network yet, the message waits
    (up to MAX_WAIT_HOURS) while the service asks for it. Messages that can't
    be delivered directly go through PROPAGATION_NODE, if one is set.
  - Replying STOP turns messages off.

License: Unlicense (public domain). See LICENSE.
This software is possible because my parents believed in me and encouraged me to follow my passions.
"""

import os

# ============================ CONFIGURATION ============================

# LXMF propagation node for reaching people who are offline when an alert is
# issued (32 characters). "" = direct delivery only. Best: one that's always on,
# ideally on this same machine.
PROPAGATION_NODE = ""

DISPLAY_NAME = "RetiCast Alerts"   # the name people see on these messages

# Your NomadNet node's address, used for the "full alert" link in messages.
# "" = work it out from NOMADNET_IDENTITY.
NODE_ADDRESS = ""
NOMADNET_IDENTITY = "~/.nomadnetwork/storage/identity"

CHECK_MINUTES = 3              # how often to check for new alerts
ANNOUNCE_HOURS = 6             # how often to announce this service on the network
MAX_WAIT_HOURS = 6             # give up on reaching an unknown address after this
MAX_PER_CHECK = 5              # most new alerts sent to one person per check
MAX_DESCRIPTION_CHARS = 300    # keep messages small for LoRa links

RETICULUM_CONFIG_DIR = None    # None = the usual ~/.reticulum

# =======================================================================

import json
import sys
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import reticast as rc                                 # noqa: E402  (shares settings and data)

try:
    import RNS
    import LXMF
except ImportError:                                   # checked in main()
    RNS = LXMF = None

LEDGER_FILE = os.path.join(rc.NOTIFY_DIR, "sent.json")
PENDING_FILE = os.path.join(rc.NOTIFY_DIR, "pending.json")
IDENTITY_FILE = os.path.join(rc.NOTIFY_DIR, "identity")
ROUTER_DIR = os.path.join(rc.NOTIFY_DIR, "lxmf")
STOP_WORDS = ("STOP", "UNSUBSCRIBE", "OFF", "QUIT", "CANCEL")
REPLY_COOLDOWN = 600           # at most one automatic reply per sender per 10 minutes


def log(msg):
    print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}", flush=True)


# ============================== deciding what to send ==================
# (No RNS here, so it can be tested without a network.)

def subscribers(users):
    """[(ident, profile, address)] for visitors with messages on and a default location."""
    out = []
    for ident, raw in (users or {}).items():
        if not isinstance(ident, str) or not rc.HASH_RE.match(ident):
            continue
        prof = rc.normalize_profile(raw)
        if not prof or not prof["notify"]["on"] or not prof["default"]:
            continue
        addr = rc.message_address(ident, prof["notify"])
        if addr:
            out.append((ident, prof, addr))
    return out


def load_ledger():
    data = rc.read_json(LEDGER_FILE)
    sent = data.get("sent") if isinstance(data, dict) else None
    clean = {}
    for ident, alerts in (sent or {}).items():
        if isinstance(alerts, dict):
            clean[ident] = {aid: v for aid, v in alerts.items()
                            if isinstance(v, list) and len(v) == 2}
    return {"sent": clean}


def prune_ledger(ledger, now):
    """Forget alerts that ended more than 2 days ago."""
    for ident in list(ledger["sent"]):
        seen = ledger["sent"][ident]
        for aid in [a for a, (until, _) in seen.items() if (rc.num(until) or 0) + 2 * 86400 < now]:
            del seen[aid]
        if not seen:
            del ledger["sent"][ident]


def plan_messages(subs, alerts_by_key, ledger, now):
    """New alerts to send as [(ident, address, loc, alert)]. Records them in the ledger.

    alerts_by_key maps loc_key -> list of alerts, or None when the fetch failed
    (those places are skipped this time, not treated as "no alerts").
    """
    to_send = []
    for ident, prof, addr in subs:
        loc = prof["default"]
        alerts = alerts_by_key.get(rc.loc_key(loc))
        if not alerts:
            continue
        seen = ledger["sent"].setdefault(ident, {})
        sent_now = 0
        for a in alerts:
            aid = a.get("id")
            if not aid or aid in seen or not rc.alert_wanted(prof["notify"], a):
                continue
            if sent_now >= MAX_PER_CHECK:
                break                    # the rest go out on the next check
            end = rc.parse_time(a.get("ends"))
            until = end.timestamp() if end else now + 86400
            # An update/extension of an alert this person already got: note it quietly.
            if any(r in seen and seen[r][1] == a.get("event") for r in a.get("refs") or []):
                seen[aid] = [until, a.get("event")]
                continue
            seen[aid] = [until, a.get("event")]
            to_send.append((ident, addr, loc, a))
            sent_now += 1
        if not seen:
            del ledger["sent"][ident]
    return to_send


def _plain(text):
    return (text or "").replace("`", "'")


def alert_message(loc, alert, tz, node_addr):
    """(title, body) for one alert, kept short for slow links."""
    event = _plain(alert.get("event")) or "Weather alert"
    title = f"{event} - {_plain(loc['name'])}"[:80]
    lines = [f"{event.upper()} for {_plain(loc['name'])}"]
    end = rc.parse_time(alert.get("ends"))
    if end:
        lines.append(f"Until {rc.fmt_local(end, '%I:%M%p %a %m/%d %Z', tz)}")
    if alert.get("severity") in ("Extreme", "Severe"):
        lines.append(f"Severity: {alert['severity']}")
    if alert.get("headline"):
        lines += ["", _plain(" ".join(alert["headline"].split()))]
    desc, _ = rc.trim(rc.alert_description(alert.get("description")), MAX_DESCRIPTION_CHARS)
    if desc:
        lines += ["", _plain(desc)]
    instr, _ = rc.trim(alert.get("instruction") or "", 200)
    if instr:
        lines += ["", "What to do: " + _plain(instr)]
    if node_addr:
        lines += ["", f"Full alert and forecast: {node_addr}:{rc.PAGE_PATH}"]
    lines += ["", "Reply STOP to turn off these messages."]
    return title, "\n".join(lines)


def test_message(prof):
    n = prof["notify"]
    what = rc.NOTIFY_LEVEL_TEXT.get(n["level"], "alerts").lower()
    if n["severe"]:
        what += " (Severe and Extreme only)"
    where = rc.place_name(prof["default"]) if prof.get("default") else "your default location"
    state = ("You'll get " + what + " for " + where + "." if n["on"]
             else "Alert messages are currently off; turn them on in My Places.")
    return ("RetiCast test message",
            f"Alert messages from RetiCast reach you.\n\n{_plain(state)}\n\n"
            "Reply STOP to turn them off.")


def verify_message(code):
    return ("RetiCast confirmation code",
            f"Your RetiCast confirmation code is {code}.\n\n"
            "Enter it on the My Places page to send weather alert messages to this address. "
            "If you didn't ask for this, you can ignore this message.")


def stop_address(addr):
    """Turn off messages for every visitor whose messages go to addr. Returns how many."""
    count = 0
    for ident, prof, a in subscribers(rc.load_users()):
        if a == addr:
            changed, _ = rc.update_profile(ident, rc.act_notify_off())
            count += bool(changed)
    return count


def read_outbox():
    """[(path, item)] waiting in the outbox, oldest first. Bad files are removed."""
    try:
        names = sorted(n for n in os.listdir(rc.OUTBOX_DIR) if n.endswith(".json"))
    except OSError:
        return []
    out = []
    for name in names:
        path = os.path.join(rc.OUTBOX_DIR, name)
        item = rc.read_json(path)
        ok = (isinstance(item, dict) and item.get("kind") in ("test", "verify")
              and isinstance(item.get("addr"), str) and rc.HASH_RE.match(item["addr"])
              and isinstance(item.get("ident"), str) and rc.HASH_RE.match(item["ident"]))
        if ok and item["kind"] == "verify":
            ok = isinstance(item.get("code"), str) and item["code"].isdigit()
        if ok and time.time() - (rc.num(item.get("ts")) or 0) > 3600:
            ok = False                              # too old to be useful
        if ok:
            out.append((path, item))
        else:
            try:
                os.unlink(path)
            except OSError:
                pass
    return out


def outbox_message(item):
    """(address, title, body) for an outbox item, or None if it no longer applies."""
    if item["kind"] == "verify":
        return item["addr"], *verify_message(item["code"])
    prof = rc.get_profile(item["ident"])
    if not prof:
        return None
    # send the test to wherever messages go *now*, not where they went when queued
    return rc.message_address(item["ident"], prof["notify"]), *test_message(prof)


# ============================== LXMF ===================================

class Messenger:
    """Owns the LXMF router. Messages to unknown addresses wait in self.pending."""

    def __init__(self):
        rc.ensure_dirs()
        os.makedirs(ROUTER_DIR, mode=0o700, exist_ok=True)
        self.reticulum = RNS.Reticulum(configdir=RETICULUM_CONFIG_DIR)
        if os.path.isfile(IDENTITY_FILE):
            self.identity = RNS.Identity.from_file(IDENTITY_FILE)
            if self.identity is None:
                raise SystemExit(f"Could not load the identity in {IDENTITY_FILE}")
        else:
            self.identity = RNS.Identity()
            self.identity.to_file(IDENTITY_FILE)
            os.chmod(IDENTITY_FILE, 0o600)
            log("Created a new identity for alert messages")
        self.router = LXMF.LXMRouter(identity=self.identity, storagepath=ROUTER_DIR)
        self.source = self.router.register_delivery_identity(self.identity,
                                                             display_name=DISPLAY_NAME)
        self.router.register_delivery_callback(self.on_message)
        self.propagation = None
        if PROPAGATION_NODE:
            if rc.HASH_RE.match(PROPAGATION_NODE.lower()):
                self.propagation = bytes.fromhex(PROPAGATION_NODE.lower())
                self.router.set_outbound_propagation_node(self.propagation)
            else:
                log(f"PROPAGATION_NODE {PROPAGATION_NODE!r} isn't a valid address; ignoring it")
        self.lock = threading.RLock()   # callbacks may run while we hold it
        self.status = {"addresses": {}, "last": {}}     # shown on the My Places page
        self.status_changed = False
        self.node_addr = ""                              # set by main(); used in replies
        self.path_asked = {}
        self.pending = self._load_pending()
        self.replied = {}
        self.announce()

    # --- pending messages (addresses not known yet), kept across restarts ---
    def _load_pending(self):
        data = rc.read_json(PENDING_FILE)
        items = []
        for p in data if isinstance(data, list) else []:
            if (isinstance(p, dict) and rc.HASH_RE.match(str(p.get("addr")))
                    and isinstance(p.get("title"), str) and isinstance(p.get("body"), str)):
                p["first"] = rc.num(p.get("first")) or time.time()
                p["next_request"] = 0
                items.append(p)
        return items

    def _save_pending(self):
        rc.cache_put(PENDING_FILE, [{k: p[k] for k in ("addr", "title", "body", "first")}
                                    for p in self.pending])

    def queue(self, addr, title, body):
        with self.lock:
            self.pending.append({"addr": addr, "title": title, "body": body,
                                 "first": time.time(), "next_request": 0})
            self._save_pending()
            self._set_last(addr, title, "waiting")
        self.flush()

    # --- status for the page: is each address known, and how did its last message go ---
    def _set_last(self, addr, title, state):
        self.status["last"][addr] = {"title": title, "state": state, "ts": time.time()}
        self.status_changed = True

    def watch(self, addrs):
        """Check which addresses are known on the network; keep asking for the rest."""
        now = time.time()
        with self.lock:
            known = {}
            for a in addrs:
                h = bytes.fromhex(a)
                found = RNS.Identity.recall(h) is not None
                known[a] = {"known": found, "checked": now}
                if not found and now >= self.path_asked.get(a, 0):
                    RNS.Transport.request_path(h)
                    self.path_asked[a] = now + 300
            self.status["addresses"] = known
            self.status["last"] = {a: v for a, v in self.status["last"].items()
                                   if a in known or now - v["ts"] < 86400}

    def save_status(self):
        with self.lock:
            data = {"ts": time.time(), "addresses": dict(self.status["addresses"]),
                    "last": dict(self.status["last"])}
            self.status_changed = False
        rc.cache_put(rc.STATUS_FILE, data)

    def flush(self):
        """Send pending messages whose recipient is known; ask the network for the rest."""
        now = time.time()
        with self.lock:
            keep, changed = [], False
            for p in self.pending:
                dest_hash = bytes.fromhex(p["addr"])
                ident = RNS.Identity.recall(dest_hash)
                if ident is not None:
                    self._send(ident, p)
                    changed = True
                elif now - p["first"] > MAX_WAIT_HOURS * 3600:
                    log(f"Gave up on {p['addr']} (never heard from it): {p['title']}")
                    self._set_last(p["addr"], p["title"], "failed")
                    changed = True
                else:
                    if now >= p["next_request"]:
                        RNS.Transport.request_path(dest_hash)
                        p["next_request"] = now + 300
                    keep.append(p)
            self.pending = keep
            if changed:
                self._save_pending()

    def _send(self, ident, p):
        dest = RNS.Destination(ident, RNS.Destination.OUT, RNS.Destination.SINGLE,
                               "lxmf", "delivery")
        lxm = LXMF.LXMessage(dest, self.source, p["body"], p["title"],
                             desired_method=LXMF.LXMessage.DIRECT)
        lxm.try_propagation_on_fail = self.propagation is not None
        lxm.reticast_addr, lxm.reticast_title = p["addr"], p["title"]
        lxm.register_delivery_callback(self._delivered)
        lxm.register_failed_callback(self._failed)
        self._set_last(p["addr"], p["title"], "sending")
        self.router.handle_outbound(lxm)
        log(f"Sent to {p['addr']}: {p['title']}")

    def _delivered(self, lxm):
        """Direct: the recipient confirmed it. Propagated: the propagation node took it."""
        propagated = getattr(lxm, "desired_method", None) == LXMF.LXMessage.PROPAGATED
        with self.lock:
            self._set_last(getattr(lxm, "reticast_addr", ""), getattr(lxm, "reticast_title", ""),
                           "propagated" if propagated else "delivered")

    def _failed(self, lxm):
        """Direct delivery failed: hand it to the propagation node once."""
        if getattr(lxm, "try_propagation_on_fail", False):
            lxm.try_propagation_on_fail = False
            lxm.delivery_attempts = 0
            if hasattr(lxm, "next_delivery_attempt"):
                del lxm.next_delivery_attempt
            lxm.packed = None
            lxm.desired_method = LXMF.LXMessage.PROPAGATED
            self.router.handle_outbound(lxm)
            log("Direct delivery failed; sent through the propagation node")
        else:
            with self.lock:
                self._set_last(getattr(lxm, "reticast_addr", ""),
                               getattr(lxm, "reticast_title", ""), "failed")
            log("A message could not be delivered")

    def on_message(self, lxm):
        """Replies: STOP turns messages off; anything else gets a short help reply."""
        try:
            src = lxm.source_hash.hex()
            content = lxm.content if isinstance(lxm.content, bytes) else str(lxm.content).encode()
            text = content.decode("utf-8", "replace").strip()
            word = text.split()[0].upper().strip(".!") if text else ""
            now = time.time()
            if now - self.replied.get(src, 0) < REPLY_COOLDOWN:
                if word not in STOP_WORDS:
                    return                   # never get into a loop with another bot
            self.replied[src] = now
            link = f"{self.node_addr}:{rc.PAGE_PATH}" if self.node_addr else ""
            where = (f"open RetiCast at {link} and go to My Places." if link
                     else "go to My Places on RetiCast.")
            if word in STOP_WORDS:
                n = stop_address(src)
                log(f"STOP from {src}: turned off for {n} visitor(s)")
                body = (f"RetiCast alert messages are off. To turn them on again, {where}"
                        if n else "This address wasn't getting RetiCast alert messages.")
            else:
                body = ("This is an automatic weather alert service. Reply STOP to turn off "
                        f"alert messages. To change what you get, {where}")
            self.queue(src, "RetiCast", body)
        except Exception as e:
            log(f"Error handling a reply: {type(e).__name__}: {e}")

    def announce(self):
        self.router.announce(self.source.hash)
        log(f"Announced {self.source.hash.hex()} ({DISPLAY_NAME})")


# ============================== service ================================

def node_address():
    if NODE_ADDRESS:
        return NODE_ADDRESS.lower() if rc.HASH_RE.match(NODE_ADDRESS.lower()) else ""
    path = os.path.expanduser(NOMADNET_IDENTITY)
    if not os.path.isfile(path):
        return ""
    try:
        ident = RNS.Identity.from_file(path)
        return RNS.Destination.hash_from_name_and_identity("nomadnetwork.node", ident.hash).hex()
    except Exception:
        return ""


def self_check(identity):
    """reticast.py works out LXMF addresses without RNS; make sure it agrees with RNS."""
    ours = rc.lxmf_address(identity.hash.hex())
    theirs = RNS.Destination.hash_from_name_and_identity("lxmf.delivery", identity.hash).hex()
    if ours != theirs:
        raise SystemExit("reticast.lxmf_address() doesn't match RNS "
                         f"({ours} vs {theirs}); not sending anything.")


def fetch_alerts(locs):
    """{loc_key: alerts, or None if the fetch failed} for a list of places."""
    def one(loc):
        key = rc.loc_key(loc)
        try:
            meta = rc.get_meta(loc)
        except Exception:
            return key, None, None
        if meta.get("provider") != "nws":
            return key, [], meta
        try:
            return key, rc.fetch_nws_alerts(loc), meta
        except Exception as e:
            log(f"Alert check failed for {loc['name']}: {type(e).__name__}")
            return key, None, meta
    if not locs:
        return {}, {}
    with ThreadPoolExecutor(max_workers=min(4, len(locs))) as pool:
        results = list(pool.map(one, locs))
    return ({k: a for k, a, _ in results}, {k: m for k, _, m in results if m})


def check_alerts(messenger, node_addr):
    subs = subscribers(rc.load_users())
    locs = []
    for _, prof, _ in subs:
        if not any(rc.same_place(prof["default"], l) for l in locs):
            locs.append(prof["default"])
    alerts, metas = fetch_alerts(locs)
    now = time.time()
    ledger = load_ledger()
    prune_ledger(ledger, now)
    to_send = plan_messages(subs, alerts, ledger, now)
    for ident, addr, loc, alert in to_send:          # queue first: a crash here repeats
        meta = metas.get(rc.loc_key(loc)) or {}                      # rather than loses
        named = dict(loc, name=rc.display_name(loc, meta))            # "Harris County, Texas (EM20fb)"
        messenger.queue(addr, *alert_message(named, alert, rc.get_tz(meta.get("tz")), node_addr))
    rc.cache_put(LEDGER_FILE, ledger)
    if to_send:
        log(f"Queued {len(to_send)} alert message(s)")


def process_outbox(messenger):
    for path, item in read_outbox():
        try:
            msg = outbox_message(item)
            if msg:
                messenger.queue(*msg)
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass


def write_heartbeat(messenger):
    rc.cache_put(rc.HEARTBEAT_FILE, {
        "ts": time.time(), "version": rc.VERSION, "address": messenger.source.hash.hex(),
        "name": DISPLAY_NAME, "propagation": messenger.propagation is not None})


def main(argv):
    if RNS is None or LXMF is None:
        print("reticast_notify.py needs the rns and lxmf packages (pip install rns lxmf).")
        return 1
    if "--address" in argv:
        if not os.path.isfile(IDENTITY_FILE):
            print("No identity yet; it's created the first time the service runs.")
            return 0
        ident = RNS.Identity.from_file(IDENTITY_FILE)
        print("Alert messages come from:",
              RNS.Destination.hash_from_name_and_identity("lxmf.delivery", ident.hash).hex())
        print("Propagation node:", PROPAGATION_NODE or "(none - direct delivery only)")
        return 0

    messenger = Messenger()
    self_check(messenger.identity)
    node_addr = node_address()
    messenger.node_addr = node_addr
    log(f"RetiCast alert messages {rc.VERSION} running. Node link: {node_addr or '(none)'}")
    last_check = last_beat = last_watch = 0.0
    last_announce = time.time()
    while True:
        try:
            now = time.time()
            if now - last_beat >= 60:
                write_heartbeat(messenger)
                last_beat = now
            if now - last_watch >= 30:
                messenger.watch(rc.notify_watch_list(rc.load_users()))
                messenger.save_status()
                last_watch = now
            elif messenger.status_changed:
                messenger.save_status()
            process_outbox(messenger)
            if now - last_check >= CHECK_MINUTES * 60:
                last_check = now
                check_alerts(messenger, node_addr)
            messenger.flush()
            if now - last_announce >= ANNOUNCE_HOURS * 3600:
                messenger.announce()
                last_announce = now
        except Exception as e:           # keep the service alive through surprises
            log(f"Error: {type(e).__name__}: {e}")
            traceback.print_exc()
        time.sleep(5)


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except KeyboardInterrupt:
        sys.exit(0)
