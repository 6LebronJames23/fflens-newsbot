#!/usr/bin/env python3
"""@fflensapp news bot: posts fantasy impact when a relevant player's injury status changes on Sleeper,
or when someone becomes the most-added player in the last 6 hours.

Runs on GitHub Actions: each run checks every 5 minutes for ~5.5 hours, then starts the next run (.github/workflows/newsbot.yml). State (last seen statuses,
what was already posted) lives in newsbot-state.json, carried between runs with the Actions cache.
Without X credentials it only prints what it would post (dry run).
Posts carry NO links on purpose: on X's pay-per-use API a post with a URL costs ~$0.20 vs ~$0.015 without (2026). Credentials (OAuth 1.0a, user context
of @fflensapp): X_API_KEY, X_API_SECRET, X_ACCESS_TOKEN, X_ACCESS_SECRET.

Local dry run:  python3 newsbot.py --dry
"""
import base64, datetime, hashlib, hmac, json, os, ssl, sys, time, urllib.parse, urllib.request, uuid

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "newsbot-state.json")
SITE = "https://fflens.app"
POS4 = ["QB", "RB", "WR", "TE"]
BAD = {"Out", "IR", "Doubtful", "PUP", "Sus"}
OK_BEFORE = {None, "", "Questionable", "Probable"}
# fantasy-relevant = inside these position ranks (redraft) or the dynasty Superflex top 150
REL = {"QB": 24, "RB": 48, "WR": 60, "TE": 20}
PER_RUN, PER_DAY = 3, 8
STATUS_TXT = {"Out": "ruled OUT", "IR": "placed on IR", "Doubtful": "listed as DOUBTFUL", "PUP": "placed on PUP", "Sus": "suspended"}
CTX = ssl.create_default_context(cafile="/etc/ssl/cert.pem") if sys.platform == "darwin" and os.path.exists("/etc/ssl/cert.pem") else None


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "LeagueLens-newsbot/1.0 (+https://fflens.app)"})
    with urllib.request.urlopen(req, timeout=120, context=CTX) as r:
        return json.loads(r.read().decode())


def slug(name):
    import re, unicodedata
    s = unicodedata.normalize("NFD", name.replace(".", "")).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", s.replace("'", "")).strip("-")


def ranks(dyn):
    rows = fetch(f"https://api.fantasycalc.com/values/current?isDynasty={'true' if dyn else 'false'}&numQbs={2 if dyn else 1}&numTeams=12&ppr=1")
    out, pc = {}, {}
    for r in sorted(rows, key=lambda r: -r["value"]):
        P = r.get("player") or {}
        sid, pos = P.get("sleeperId"), P.get("position")
        if not sid or pos not in POS4:
            continue
        pc[pos] = pc.get(pos, 0) + 1
        out[sid] = {"prank": pc[pos], "rank": len(out) + 1, "v": r["value"], "trend": r.get("trend30Day") or 0}
    return out


# ---------- X API (OAuth 1.0a user context) ----------
def oauth_header(method, url, query=None):
    k, ks, t, ts = (os.environ.get(x) for x in ("X_API_KEY", "X_API_SECRET", "X_ACCESS_TOKEN", "X_ACCESS_SECRET"))
    q = lambda s: urllib.parse.quote(str(s), safe="")
    oauth = {"oauth_consumer_key": k, "oauth_nonce": uuid.uuid4().hex, "oauth_signature_method": "HMAC-SHA1",
             "oauth_timestamp": str(int(time.time())), "oauth_token": t, "oauth_version": "1.0"}
    params = {**oauth, **(query or {})}
    base = "&".join([method, q(url), q("&".join(f"{q(a)}={q(b)}" for a, b in sorted(params.items())))])
    oauth["oauth_signature"] = base64.b64encode(hmac.new(f"{q(ks)}&{q(ts)}".encode(), base.encode(), hashlib.sha1).digest()).decode()
    return "OAuth " + ", ".join(f'{q(a)}="{q(b)}"' for a, b in sorted(oauth.items()))


def check_x():
    url = "https://api.x.com/2/users/me"
    req = urllib.request.Request(url, headers={"Authorization": oauth_header("GET", url), "User-Agent": "LeagueLens-newsbot/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=60, context=CTX) as r:
            d = json.loads(r.read().decode()).get("data", {})
            print("X credentials OK: @" + d.get("username", "?"))
    except urllib.error.HTTPError as e:
        print("X credentials check failed:", e.code, e.read().decode()[:300])


def post_x(text):
    import re
    if re.search(r"https?://|\b[\w-]+\.(app|com|io|net|org|ly|co)\b", text):
        return 0, "refused: post contains a link (~13x the API price)"
    k, ks, t, ts = (os.environ.get(x) for x in ("X_API_KEY", "X_API_SECRET", "X_ACCESS_TOKEN", "X_ACCESS_SECRET"))
    url = "https://api.x.com/2/tweets"
    q = lambda s: urllib.parse.quote(str(s), safe="")
    oauth = {"oauth_consumer_key": k, "oauth_nonce": uuid.uuid4().hex, "oauth_signature_method": "HMAC-SHA1",
             "oauth_timestamp": str(int(time.time())), "oauth_token": t, "oauth_version": "1.0"}
    base = "&".join(["POST", q(url), q("&".join(f"{q(a)}={q(b)}" for a, b in sorted(oauth.items())))])
    oauth["oauth_signature"] = base64.b64encode(hmac.new(f"{q(ks)}&{q(ts)}".encode(), base.encode(), hashlib.sha1).digest()).decode()
    auth = "OAuth " + ", ".join(f'{q(a)}="{q(b)}"' for a, b in sorted(oauth.items()))
    req = urllib.request.Request(url, data=json.dumps({"text": text}).encode(), method="POST",
                                 headers={"Authorization": auth, "Content-Type": "application/json", "User-Agent": "LeagueLens-newsbot/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=60, context=CTX) as r:
            return r.status, json.loads(r.read().decode()).get("data", {}).get("id")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:300]


def main(dry):
    live = not dry and all(os.environ.get(x) for x in ("X_API_KEY", "X_API_SECRET", "X_ACCESS_TOKEN", "X_ACCESS_SECRET"))
    now = datetime.datetime.now(datetime.timezone.utc)
    today = now.date().isoformat()
    state = json.load(open(STATE)) if os.path.exists(STATE) else {}
    seed = os.path.join(HERE, "posted-seed.json")
    if not state and os.path.exists(seed):
        state = {"posted": json.load(open(seed))}
    prev = state.get("status", {})
    posted = state.get("posted", {})          # key -> iso time, so the same news is never posted twice
    daily = state.get("daily", {}) if state.get("day") == today else {}
    sent_today = daily.get("n", 0)

    # Sleeper asks that /players/nfl (5 MB) be fetched at most once a day: keep a slim copy in state,
    # and read live injury statuses from the much smaller weekly projections feed instead
    pmap = state.get("pmap")
    if not pmap or state.get("pmap_day") != today:
        raw = fetch("https://api.sleeper.app/v1/players/nfl")
        pmap = {pid: {"full_name": p.get("full_name") or f"{p.get('first_name') or ''} {p.get('last_name') or ''}".strip(), "position": p.get("position"), "team": p.get("team")}
                for pid, p in raw.items() if p.get("position") in POS4 and p.get("team")}
    nfl = fetch("https://api.sleeper.app/v1/state/nfl")
    q = "&".join(f"position[]={x}" for x in POS4)
    proj = fetch(f"https://api.sleeper.app/projections/nfl/{nfl.get('season')}/{max(1, int(nfl.get('week') or 1))}?season_type=regular&{q}") or []
    inj = {r["player_id"]: (r.get("player") or {}) for r in proj if r.get("player_id")}
    players = {pid: {**v, "injury_status": inj.get(pid, {}).get("injury_status"), "injury_body_part": inj.get(pid, {}).get("injury_body_part")} for pid, v in pmap.items()}
    # trade values change slowly: refresh hourly
    if not state.get("ranks") or now.timestamp() - state.get("ranks_ts", 0) > 3600:
        state["ranks"], state["ranks_ts"] = {"red": ranks(False), "dyn": ranks(True)}, now.timestamp()
    red, dyn = state["ranks"]["red"], state["ranks"]["dyn"]
    relevant = lambda pid, pos: (pid in red and red[pid]["prank"] <= REL.get(pos, 0)) or (pid in dyn and dyn[pid]["rank"] <= 150)
    adds24 = {t["player_id"]: t["count"] for t in fetch("https://api.sleeper.app/v1/players/nfl/trending/add?lookback_hours=24&limit=200")}
    adds6 = fetch("https://api.sleeper.app/v1/players/nfl/trending/add?lookback_hours=6&limit=25")

    def info(pid):
        p = players.get(pid) or {}
        name = p.get("full_name") or f"{p.get('first_name', '')} {p.get('last_name', '')}".strip()
        return name, p.get("position"), p.get("team") or "FA"

    def ctx_line(pid, pos):
        bits = []
        if pid in red:
            bits.append(f"{pos}{red[pid]['prank']} rest of season")
        if pid in dyn:
            bits.append(f"{pos}{dyn[pid]['prank']} in dynasty SF")
        return " · ".join(bits)

    def link(pid):
        name = info(pid)[0]
        return f"{SITE}/players/{slug(name)}" if pid in red or pid in dyn else f"{SITE}/waiver-wire"

    events = []
    status_now = {}
    for pid, p in players.items():
        pos = p.get("position")
        if pos not in POS4 or not p.get("team") or pid not in inj:
            continue
        st = p.get("injury_status")
        status_now[pid] = st
        if not prev or not relevant(pid, pos):
            continue
        before = prev.get(pid, "__new__")
        if before == "__new__" or before == st:
            continue
        name, _, team = info(pid)
        if st in BAD and before in OK_BEFORE:
            body = p.get("injury_body_part")
            mates = sorted((m for m in adds24 if players.get(m, {}).get("team") == team and players.get(m, {}).get("position") == pos and m != pid), key=lambda m: -adds24[m])
            nxt = f"\nNext man up on Sleeper waivers: {info(mates[0])[0]} ({adds24[mates[0]]:,} adds in 24h)" if mates else ""
            text = (f"🚨 {name} ({pos}, {team}) {STATUS_TXT[st]}{f' ({body.lower()})' if body else ''}.\n\n"
                    f"Fantasy impact: {ctx_line(pid, pos)}{nxt}\n\nFull breakdown + trade values: link in bio")
            score = 1000 - min(red.get(pid, {}).get("prank", 99), dyn.get(pid, {}).get("prank", 99))
            events.append((score, f"inj:{pid}:{st}:{today}", text))
        elif before in BAD and st in (None, "") and (red.get(pid, {}).get("prank", 99) <= REL[pos] // 2):
            text = f"✅ {name} ({pos}, {team}) no longer carries an injury designation on Sleeper.\n\n{ctx_line(pid, pos)}. Back in lineups?"
            events.append((500 - red[pid]["prank"], f"back:{pid}:{today}", text))

    # waiver spikes: top of the 6-hour adds list
    for i, t in enumerate(adds6[:2]):
        pid, n = t["player_id"], t["count"]
        name, pos, team = info(pid)
        if pos not in POS4 or n < 25000:
            continue
        key = f"spike:{pid}"
        last = posted.get(key)
        if last and (now - datetime.datetime.fromisoformat(last)).days < 4:
            continue
        why = ""
        for m, p in players.items():  # injured starter at the same spot explains most spikes
            if p.get("team") == team and p.get("position") == pos and m != pid and p.get("injury_status") in BAD and m in red and red[m]["prank"] <= REL[pos]:
                why = f" with {info(m)[0]} {STATUS_TXT.get(p['injury_status'], p['injury_status'])}"
                break
        text = (f"📈 {name} ({pos}, {team}) is the {'most' if i == 0 else 'second-most'}-added player on Sleeper right now: {n:,} adds in the last 6 hours{why}.\n\n"
                f"Top adds + FAAB help for your league: link in bio")
        events.append((300 - i, key, text))

    # weekly roundups (once per ISO week): Tuesday = top waiver adds, Thursday = dynasty market movers
    wk = "%d-%02d" % now.isocalendar()[:2]
    if now.weekday() == 1 and now.hour >= 15:
        adds48 = fetch("https://api.sleeper.app/v1/players/nfl/trending/add?lookback_hours=48&limit=40")
        top = [(t["player_id"], t["count"]) for t in adds48 if info(t["player_id"])[1] in POS4][:5]
        if len(top) == 5:
            lines = [f"{i + 1}. {info(pid)[0]} ({info(pid)[1]}, {info(pid)[2]}): {n:,} adds" for i, (pid, n) in enumerate(top)]
            events.append((2000, f"weekly:waivers:{wk}", "🧾 Top waiver adds across Sleeper leagues this week:\n\n" + "\n".join(lines) + "\n\nWho are you spending FAAB on? Bid help for your league: link in bio"))
    if now.weekday() == 3 and now.hour >= 15:
        mv = [(pid, x) for pid, x in dyn.items() if x["trend"] and x["v"] - x["trend"] > 0 and x["v"] >= 1500]
        pct = lambda x: round(100 * x["trend"] / (x["v"] - x["trend"]))
        up = sorted(mv, key=lambda t: -t[1]["trend"])[:3]
        dn = sorted(mv, key=lambda t: t[1]["trend"])[:3]
        if len(up) == 3 and len(dn) == 3 and up[0][1]["trend"] > 0 and dn[0][1]["trend"] < 0:
            fmt = lambda t: f"{info(t[0])[0]} ({info(t[0])[1]}) {'+' if t[1]['trend'] > 0 else ''}{pct(t[1])}%"
            events.append((1900, f"weekly:movers:{wk}", "📈 Dynasty market risers, last 30 days (Superflex):\n" + "\n".join("▲ " + fmt(t) for t in up)
                           + "\n\n📉 Fallers:\n" + "\n".join("▼ " + fmt(t) for t in dn) + "\n\nBuy low or sell high? Full list: link in bio"))

    out = []
    for score, key, text in sorted(events, key=lambda e: -e[0]):
        if key in posted or len(out) >= PER_RUN or sent_today + len(out) >= PER_DAY:
            continue
        out.append((key, text))

    if not prev:
        out = []  # first run: just learn everyone's current status
    for key, text in out:
        if live:
            code, res = post_x(text)
            print(f"[{code}] {key} -> {res}")
            if code not in (200, 201):
                continue
        else:
            print(f"--- DRY RUN ({key}) ---\n{text}\n")
        posted[key] = now.isoformat()
    sent_today += len(out) if not live else sum(1 for k, _ in out if k in posted)

    cutoff = now - datetime.timedelta(days=14)
    posted = {k: v for k, v in posted.items() if datetime.datetime.fromisoformat(v) > cutoff}
    json.dump({"status": status_now, "posted": posted, "day": today, "daily": {"n": sent_today}, "pmap": pmap, "pmap_day": today,
               "ranks": state["ranks"], "ranks_ts": state["ranks_ts"]}, open(STATE, "w"))
    verb = "posted" if live else "would post"
    print(f"checked {len(status_now)} players; {len(events)} candidate events; {len(out)} {verb}{' (first run: snapshot saved)' if not prev else ''}")


if __name__ == "__main__":
    if "--check" in sys.argv:
        check_x()
    elif "--loop" in sys.argv:
        # stay up for N minutes, checking every 5 (GitHub's own cron is too unreliable on small repos)
        end = time.time() + 60 * int(sys.argv[sys.argv.index("--loop") + 1])
        while True:
            try:
                main(dry="--dry" in sys.argv)
            except Exception as e:
                print("run failed:", repr(e)[:300])
            sys.stdout.flush()
            if time.time() + 300 > end:
                break
            time.sleep(300)
    else:
        main(dry="--dry" in sys.argv)
