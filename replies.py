"""Automatic replies under the pinned "what are your playoff odds?" post.

Someone replies with their Sleeper username -> we find their in-season league and team on Sleeper, open that league's
Playoff Odds page on fflens.app in headless Chrome (the same 6,000-season simulation the site runs), press
"Share playoff picture", and reply with that image plus the one-line summary.

Only replies inside the pinned post's conversation are answered (they asked for it), at most 3 a day per person
(a follow-up with just a league name reuses the username they gave before).
Usage counters on the site are blocked for these visits so they don't count as traffic.

Test locally:  .venv/bin/python replies.py <sleeper username> [league name words]   (writes reply-test.png)
"""
import base64, json, os, re, ssl, sys, urllib.parse, urllib.request

SITE = "https://fflens.app"
PER_RUN, PER_DAY = 6, 60
CTX = ssl.create_default_context(cafile="/etc/ssl/cert.pem") if sys.platform == "darwin" and os.path.exists("/etc/ssl/cert.pem") else None
STOP = {"my", "is", "the", "and", "for", "username", "user", "name", "sleeper", "league", "team", "please", "pls", "thanks", "here", "its", "it's", "me", "do", "mine", "hey", "hi", "yo", "what", "about"}


def sget(path):
    req = urllib.request.Request("https://api.sleeper.app/v1" + path, headers={"User-Agent": "LeagueLens-newsbot/1.0 (+https://fflens.app)"})
    try:
        with urllib.request.urlopen(req, timeout=60, context=CTX) as r:
            return json.loads(r.read().decode() or "null")
    except Exception:
        return None


def candidates(text):
    """likely Sleeper usernames in a reply, best guess first"""
    t = re.sub(r"https?://\S+|@\w+", " ", text)
    m = re.search(r"(?:username|user|sleeper)\s*(?:is|:|=|-)?\s*@?([A-Za-z0-9_]{2,30})", t, re.I)
    words = [w for w in re.findall(r"[A-Za-z0-9_]{2,30}", t) if w.lower() not in STOP]
    out = [m.group(1)] if m and m.group(1).lower() not in STOP else []
    out += sorted(words, key=lambda w: -(bool(re.search(r"\d|_", w)) * 2 + (w != w.lower() and w != w.capitalize())))
    seen = []
    for w in out:
        if w.lower() not in [s.lower() for s in seen]:
            seen.append(w)
    return seen[:3]


def find_team(text, known=None):
    """-> (dict with lid, league, rid, user, n) or (None, error reply text); known = username this person gave us before"""
    st = sget("/state/nfl") or {}
    season = st.get("league_season") or st.get("season")
    names = candidates(text) + ([known] if known else [])
    for name in names:
        u = sget(f"/user/{urllib.parse.quote(name)}")
        if not u or not u.get("user_id"):
            continue
        lgs = [l for l in (sget(f"/user/{u['user_id']}/leagues/nfl/{season}") or []) if l.get("status") == "in_season"]
        if not lgs:
            return None, f"Found {u.get('display_name') or name} on Sleeper, but no leagues in season right now."
        low = text.lower()
        pick = next((l for l in lgs if (l.get("name") or "").strip().lower() and (l.get("name") or "").strip().lower() in low), None)
        pick = pick or max(lgs, key=lambda l: l.get("total_rosters") or 0)
        rosters = sget(f"/league/{pick['league_id']}/rosters") or []
        mine = next((r for r in rosters if r.get("owner_id") == u["user_id"] or u["user_id"] in (r.get("co_owners") or [])), None)
        if not mine:
            continue
        return {"lid": pick["league_id"], "league": (pick.get("name") or "your league").strip(), "rid": mine["roster_id"], "n": len(lgs), "user": name}, None
    return None, ("Couldn't find that Sleeper username. Reply with your exact Sleeper username (not your team name) and I'll run your league."
                  if names else "Reply with your Sleeper username and I'll run your league's playoff odds.")


def render(lid, rid):
    """-> (png bytes, summary text) from fflens.app's own Playoff Odds page, or (None, reason)"""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        try:
            b = p.chromium.launch(channel="chrome", headless=True)
        except Exception:
            b = p.chromium.launch(headless=True)
        try:
            pg = b.new_page(viewport={"width": 1100, "height": 900})
            pg.route("**/api/e", lambda r: r.abort())  # don't count bot visits as traffic
            pg.goto(SITE + "/", wait_until="domcontentloaded")
            pg.evaluate("""([lid, rid]) => { localStorage.setItem('ll_myteam', JSON.stringify({[lid]: rid})); for (const k of ['ll_rp_off', 'll_tour', 'll_who_off']) localStorage.setItem(k, '1'); }""", [lid, rid])
            pg.goto(f"{SITE}/#/l/{lid}/playoffs", wait_until="domcontentloaded")
            try:
                pg.wait_for_selector("#shScen", timeout=120000)
            except Exception:
                txt = pg.inner_text("#panel") if pg.query_selector("#panel") else ""
                return None, "playoffs over" if "Final Standings" in txt else "no scenarios"
            pg.evaluate("""() => { window.__img = null; shDeliver = async (c, f, t, text) => { window.__img = c.toDataURL('image/png'); window.__txt = text; return 'shared'; }; }""")
            pg.click("#shScen")
            pg.wait_for_function("() => window.__img", timeout=60000)
            img, txt = pg.evaluate("() => [window.__img, window.__txt]")
            return base64.b64decode(img.split(",", 1)[1]), txt.split("\n")[0]
        finally:
            b.close()


def run(rep, pin, me_id, live, now, x):
    """rep: persisted state {since, done: {tweet_id: 1}, users: {author: day}, day, n}; x: dict of api helpers"""
    if not pin:
        return
    day = now.date().isoformat()
    if rep.get("day") != day:
        rep["day"], rep["n"], rep["users"] = day, 0, {}
    tweets = x["search"](f"conversation_id:{pin} -is:retweet", rep.get("since"))
    if tweets is None:
        return
    done = rep.setdefault("done", {})
    sent = 0
    for t in sorted(tweets, key=lambda t: int(t["id"])):
        rep["since"] = max(rep.get("since") or "0", t["id"], key=int)
        if t["id"] in done or t.get("author_id") == me_id:
            continue
        if sent >= PER_RUN or rep["n"] >= PER_DAY:
            rep["since"] = str(int(t["id"]) - 1)  # come back for it next run
            break
        done[t["id"]] = 1
        a = t.get("author_id")
        if rep["users"].get(a, 0) >= 3:
            continue
        who = rep.setdefault("who", {})
        team, err = find_team(t.get("text", ""), who.get(a))
        if team:
            who[a] = team["user"]
        png, text = None, err
        if team:
            try:
                png, summary = render(team["lid"], team["rid"])
            except Exception as e:
                print("reply render failed:", repr(e)[:200]); done.pop(t["id"], None); continue
            if not png:
                text = ("The playoffs have started in " + team["league"] + ", so there are no odds left to run. Good luck!") if summary == "playoffs over" else None
            else:
                more = f"\n\nYou're in {team['n']} leagues: reply with another league's name to run that one." if team["n"] > 1 else ""
                text = f"{team['league']}: {summary}{more}"
        if not text:
            continue
        text = text if len(text) <= 280 else text[:277] + "…"
        if live:
            media = x["upload"](png) if png else None
            code, res = x["post"](text, media=media, reply_to=t["id"])
            print(f"[{code}] reply to {t['id']} -> {res}{' (with image)' if media else ''}")
            if code not in (200, 201):
                continue
        else:
            print(f"--- DRY REPLY to {t['id']} ---\n{text}\n" + ("[image]\n" if png else ""))
        rep["users"][a] = rep["users"].get(a, 0) + 1
        rep["n"] += 1
        sent += 1
    if len(rep.get("who", {})) > 3000:
        rep["who"] = dict(list(rep["who"].items())[-2000:])
    if len(done) > 3000:
        rep["done"] = dict(list(done.items())[-2000:])


if __name__ == "__main__":
    team, err = find_team(" ".join(sys.argv[1:]))
    print(team or err)
    if team:
        png, txt = render(team["lid"], team["rid"])
        print(txt)
        if png:
            open("reply-test.png", "wb").write(png)
