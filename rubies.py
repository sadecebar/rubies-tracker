#!/usr/bin/env python3
"""Rubies tracker: saves the Pets Universe Rubies leaderboard every hour and posts a picture
with your placement, your rubies per hour over the last 4 h and what you made today.

Data: the site's public API (r=boards), the same data as https://petsuniverse.org/players.
"""
import html
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Stockholm")
API = "https://petsuniverse.org/api/public.php?r=boards"
BOARD = os.environ.get("RUBIES_BOARD", "Rubies")          # board id in the API
ME = os.environ.get("TRACK_USER", "imnotsadece")
WEBHOOK = os.environ.get("DISCORD_WEBHOOK_URL", "")
STATE = os.environ.get("RUBIES_STATE", "data/rubies.json")
WINDOW_H = 4        # "last 4 hours" rate
KEEP_DAYS = 8
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0 Safari/537.36")
SUF = ["", "K", "M", "B", "T", "Qa", "Qi", "Sx", "Sp", "Oc", "No", "Dc"]


# ------------------------------------------------------------------ data
def fetch():
    """-> (board time as unix seconds, [(uid, name, value), ...] in rank order)."""
    req = urllib.request.Request(API, headers={"User-Agent": UA, "Referer": "https://petsuniverse.org/players",
                                               "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        d = json.load(r)
    b = next((x for x in d.get("boards") or [] if x.get("id") == BOARD), None)
    if not d.get("ok") or not b or not b.get("rows"):
        raise RuntimeError(f"board {BOARD!r} not in the API answer")
    return int(b.get("at") or time.time()), [(r.get("u"), r.get("n"), float(r.get("v") or 0)) for r in b["rows"]]


def load():
    try:
        with open(STATE, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, ValueError):
        return {"snaps": []}


def save(st):
    os.makedirs(os.path.dirname(STATE) or ".", exist_ok=True)
    cut = time.time() - KEEP_DAYS * 86400
    st["snaps"] = [s for s in st["snaps"] if s["at"] >= cut]
    with open(STATE, "w", encoding="utf-8") as f:
        json.dump(st, f, separators=(",", ":"))


def snapshot():
    """Fetch the board and store it (once per board update). Returns the state."""
    st = load()
    at, rows = fetch()
    if not st["snaps"] or st["snaps"][-1]["at"] != at:
        st["snaps"].append({"at": at, "rows": [list(r) for r in rows]})
        save(st)
        print(f"rubies snapshot {datetime.fromtimestamp(at, TZ):%H:%M} ({len(rows)} rows)")
    else:
        print("rubies board not updated since last snapshot")
    return st


def me_in(snap):
    """(rank, uid, name, value) for ME in a snapshot, or None."""
    for i, (uid, name, v) in enumerate(snap["rows"], 1):
        if (name or "").lower() == ME.lower():
            return i, uid, name, v
    return None


def value_of(snap, uid):
    return next((v for u, n, v in snap["rows"] if u == uid), None)


def closest(snaps, ts, max_off):
    """Snapshot closest to time ts (within max_off seconds), or None."""
    best = min(snaps, key=lambda s: abs(s["at"] - ts), default=None)
    return best if best and abs(best["at"] - ts) <= max_off else None


# ------------------------------------------------------------------ maths
def short(v, signed=False):
    if v is None:
        return "?"
    sign = ("+" if v >= 0 else "-") if signed else ("-" if v < 0 else "")
    a, e = abs(v), 0
    while a >= 1000 and e < len(SUF) - 1:
        a, e = a / 1000, e + 1
    t = f"{a:.2f}" if a < 10 else f"{a:.1f}" if a < 100 else f"{a:.0f}"
    if e and "." in t:
        t = t.rstrip("0").rstrip(".")
    return f"{sign}{t}{SUF[e]}"


def dur(h):
    m = int(round(h * 60))
    if m < 60:
        return f"{m}m"
    return f"{m // 60}h" if m % 60 == 0 else f"{m // 60}h {m % 60:02d}m"


def report(st, now=None):
    """Everything the picture shows. Never guesses: missing history is reported as missing."""
    snaps = st["snaps"]
    cur = snaps[-1]
    at = cur["at"]
    now_l = datetime.fromtimestamp(at, TZ)
    me = me_in(cur)
    out = {"time": now_l.strftime("%a %d %b · %H:%M"), "me": None, "rows": cur["rows"]}
    if not me:
        return out
    rank, uid, name, v = me
    out["me"] = {"rank": rank, "uid": uid, "name": name, "value": v}
    # last 4 hours
    old = closest(snaps[:-1], at - WINDOW_H * 3600, 75 * 60)
    if old is None and snaps[:-1] and at - snaps[0]["at"] >= 3600:  # less history: use the oldest we have
        old = snaps[0]
    if old is not None:
        hrs = (at - old["at"]) / 3600
        ov = value_of(old, uid)
        orank = me_in(old)[0] if me_in(old) else None
        if ov is not None and hrs > 0:
            out["win"] = {"hours": hrs, "gain": v - ov, "rate": (v - ov) / hrs,
                          "moved": (orank - rank) if orank else None}
    # today (since 00:00 Stockholm)
    midnight = now_l.replace(hour=0, minute=0, second=0, microsecond=0)
    base = [s for s in snaps if s["at"] <= midnight.timestamp() + 15 * 60]
    start = base[-1] if base and midnight.timestamp() - base[-1]["at"] <= 75 * 60 else None
    label = "since 00:00"
    if start is None:  # tracker started today: count from the first snapshot of today
        todays = [s for s in snaps if s["at"] >= midnight.timestamp()]
        start = todays[0] if todays and todays[0] is not cur else None
        if start is not None:
            label = f"since {datetime.fromtimestamp(start['at'], TZ):%H:%M} (tracking started)"
    if start is not None and value_of(start, uid) is not None:
        hrs = (at - start["at"]) / 3600
        g = v - value_of(start, uid)
        out["today"] = {"gain": g, "hours": hrs, "rate": g / hrs if hrs > 0 else None, "label": label}
    # hourly gains, last 12 snapshots ~ hours
    pts = [(s["at"], value_of(s, uid)) for s in snaps if s["at"] >= at - 12.5 * 3600]
    pts = [p for p in pts if p[1] is not None]
    out["bars"] = [(datetime.fromtimestamp(b[0], TZ).strftime("%H"), (b[1] - a[1]) / max((b[0] - a[0]) / 3600, 1e-9))
                   for a, b in zip(pts, pts[1:]) if b[0] - a[0] >= 20 * 60]
    # neighbours, with their own 4h rate when known
    rows = cur["rows"]

    def nb(i):
        if not 0 <= i < len(rows):
            return None
        u, n, val = rows[i]
        r = None
        if "win" in out and old is not None and value_of(old, u) is not None:
            r = (val - value_of(old, u)) / out["win"]["hours"]
        return {"rank": i + 1, "uid": u, "name": n, "value": val, "rate": r}
    out["above"], out["below"] = nb(rank - 2), nb(rank)
    return out


# ------------------------------------------------------------------ picture
def headshots(uids):
    uids = [u for u in uids if u]
    if not uids:
        return {}
    try:
        url = ("https://thumbnails.roblox.com/v1/users/avatar-headshot?userIds="
               + ",".join(str(u) for u in uids) + "&size=150x150&format=Png&isCircular=false")
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=15) as r:
            return {t["targetId"]: t.get("imageUrl") for t in json.load(r)["data"]}
    except Exception as ex:
        print("headshots failed:", str(ex)[:100])
        return {}


CSS = """
@import url('https://fonts.googleapis.com/css2?family=Outfit:wght@400;600;800&display=block');
*{box-sizing:border-box;margin:0;padding:0}
body{margin:0;background:transparent;font-family:'Outfit','Noto Sans','DejaVu Sans',sans-serif;color:#f4f4f7}
.card{width:600px;border-radius:30px;padding:26px 24px 20px;position:relative;overflow:hidden;
 background:radial-gradient(120% 60% at 50% 0%,rgba(244,63,94,.32) 0%,rgba(0,0,0,0) 60%),linear-gradient(180deg,#1a1216 0%,#0d0a0c 100%);
 border:1.5px solid rgba(244,63,94,.45)}
.card:before{content:"";position:absolute;inset:0;pointer-events:none;background-image:radial-gradient(rgba(255,255,255,.55) 1px,transparent 1.5px);background-size:46px 46px;opacity:.08}
.top{display:flex;justify-content:space-between;align-items:center;position:relative}
.tag{font-size:13px;font-weight:800;letter-spacing:.18em;color:#fb7185;padding:7px 14px;border-radius:99px;background:rgba(244,63,94,.12);border:1px solid rgba(244,63,94,.45)}
.when{font-size:14px;color:#b5a1a8;font-weight:600}
.hero{display:flex;align-items:center;gap:18px;margin:22px 0 18px;position:relative}
.av{width:96px;height:96px;border-radius:50%;object-fit:cover;background:#2a2024;flex:none;
 box-shadow:0 0 0 3px rgba(244,63,94,.6),0 10px 30px rgba(0,0,0,.5)}
.who{flex:1;min-width:0}
.nm{font-size:26px;font-weight:800}
.tot{font-size:40px;font-weight:800;color:#fb7185;line-height:1.1}
.tot small{font-size:16px;color:#b5a1a8;font-weight:600;margin-left:6px}
.rk{text-align:center;flex:none}
.rk .n{font-size:46px;font-weight:800;line-height:1}
.rk .l{font-size:12px;color:#b5a1a8;letter-spacing:.12em;font-weight:700}
.rk .mv{font-size:14px;font-weight:800;margin-top:4px}
.up{color:#3ddc97}.dn{color:#f87171}.eq{color:#8a7a80}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:10px;position:relative}
.tile{background:#ffffff0b;border:1px solid #ffffff12;border-radius:18px;padding:13px 16px}
.tile.main{background:rgba(244,63,94,.12);border-color:rgba(244,63,94,.4)}
.tile .k{font-size:12px;color:#a8969c;font-weight:700;letter-spacing:.08em;text-transform:uppercase}
.tile .v{font-size:26px;font-weight:800;margin-top:3px;white-space:nowrap}
.tile.main .v{color:#fb7185}
.tile .s{font-size:12px;color:#8a7a80;margin-top:1px}
h2{font-size:13px;font-weight:800;letter-spacing:.14em;color:#c9a7b1;text-transform:uppercase;margin:18px 0 8px;position:relative}
.chart{display:flex;align-items:flex-end;gap:6px;height:90px;padding:10px 12px 0;background:#ffffff0a;border:1px solid #ffffff12;border-radius:18px;position:relative}
.col{flex:1;display:flex;flex-direction:column;align-items:center;justify-content:flex-end;height:100%}
.col i{display:block;width:100%;border-radius:6px 6px 2px 2px;background:linear-gradient(180deg,#fb7185,#be123c);min-height:3px}
.col span{font-size:10px;color:#8a7a80;margin:4px 0 6px}
.nbs{background:#ffffff0a;border:1px solid #ffffff12;border-radius:18px;padding:4px 14px;position:relative}
.nb{display:flex;align-items:center;gap:10px;padding:9px 0;border-top:1px solid #ffffff0d}.nb:first-child{border-top:0}
.nb img,.nb .ph{width:36px;height:36px;border-radius:50%;object-fit:cover;background:#2a2024;flex:none}
.nb .t{flex:1;min-width:0;font-size:15px}.nb .t b{font-weight:800}.nb .t small{display:block;font-size:12px;color:#a8969c}
.nb .r{text-align:right;font-size:14px;font-weight:700;white-space:nowrap}
.note{color:#a8969c;font-size:13px;margin-top:10px;position:relative}
.foot{display:flex;justify-content:space-between;margin-top:14px;font-size:12px;color:#7a6a70;position:relative}
"""


def build_html(r):
    e = lambda x: html.escape(str(x))
    m = r["me"]
    heads = headshots([m["uid"]] + [x["uid"] for x in (r.get("above"), r.get("below")) if x])
    pic = lambda uid, cls="": (f'<img class="{cls}" src="{e(heads[uid])}">' if heads.get(uid)
                               else f'<div class="{cls or "ph"}"></div>')
    win, today = r.get("win"), r.get("today")
    mv = ""
    if win and win.get("moved") is not None:
        d = win["moved"]
        mv = (f'<div class="mv up">▲{d} in {dur(win["hours"])}</div>' if d > 0 else
              f'<div class="mv dn">▼{-d} in {dur(win["hours"])}</div>' if d < 0 else
              f'<div class="mv eq">same place</div>')
    tiles = []
    if win:
        tiles.append(("main", f"Last {dur(win['hours'])}", short(win["gain"], True), "rubies made"))
        tiles.append(("main", "Per hour", short(win["rate"]) + "/h", f"average over {dur(win['hours'])}"))
    else:
        tiles.append(("", f"Last {WINDOW_H}h", "…", "needs a few hours of history"))
        tiles.append(("", "Per hour", "…", "needs a few hours of history"))
    if today:
        tiles.append(("", "Today", short(today["gain"], True), today["label"]))
        tiles.append(("", "Today per hour", (short(today["rate"]) + "/h") if today["rate"] is not None else "…",
                      f"over {dur(today['hours'])}"))
    else:
        tiles.append(("", "Today", "…", "starts after the next check"))
        tiles.append(("", "Today per hour", "…", "starts after the next check"))
    grid = "".join(f'<div class="tile {c}"><div class="k">{e(k)}</div><div class="v">{e(v)}</div><div class="s">{e(s)}</div></div>'
                   for c, k, v, s in tiles)
    chart = ""
    bars = r.get("bars") or []
    if len(bars) >= 2:
        top = max(max(g for _, g in bars), 1)
        chart = ('<h2>\U0001F4CA Rubies per hour · last 12h</h2><div class="chart">' +
                 "".join(f'<div class="col"><i style="height:{max(3, int(max(g, 0) / top * 62))}px"></i><span>{h}</span></div>'
                         for h, g in bars[-12:]) + "</div>")
    nbs = []
    for lbl, x in (("⬆️ Next up", r.get("above")), ("⬇️ Behind you", r.get("below"))):
        if not x:
            continue
        gap = abs(x["value"] - m["value"])
        rate = f'{short(x["rate"])}/h' if x.get("rate") is not None else "speed: no data yet"
        catch = ""
        if win and x.get("rate") is not None:
            if lbl.startswith("⬆") and win["rate"] > x["rate"]:
                catch = f" · you pass them in ~{dur(gap / (win['rate'] - x['rate']))}"
            elif lbl.endswith("you") and x["rate"] > win["rate"]:
                catch = f" · they pass you in ~{dur(gap / (x['rate'] - win['rate']))}"
        nbs.append(f'<div class="nb">{pic(x["uid"])}<div class="t"><b>#{x["rank"]} {e(x["name"])}</b>'
                   f'<small>{e(lbl)} · {short(gap)} {"ahead" if lbl.startswith(chr(0x2B06)) else "behind"}{e(catch)}</small></div>'
                   f'<div class="r">{e(rate)}</div></div>')
    nb_html = f'<h2>\U0001F465 Around you</h2><div class="nbs">{"".join(nbs)}</div>' if nbs else ""
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>{CSS}</style></head><body><div class="card">
<div class="top"><div class="tag">RUBIES TRACKER</div><div class="when">{e(r['time'])}</div></div>
<div class="hero">{pic(m['uid'], 'av')}<div class="who"><div class="nm">{e(m['name'])}</div>
<div class="tot">{short(m['value'])}<small>\U0001F48E rubies</small></div></div>
<div class="rk"><div class="l">PLACE</div><div class="n">#{m['rank']}</div>{mv}</div></div>
<div class="grid">{grid}</div>{chart}{nb_html}
<div class="foot"><span>Rubies leaderboard · checked every hour</span><span>Pets Universe · Rubies Tracker</span></div>
</div></body></html>"""


def render(page):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_context(viewport={"width": 640, "height": 1400}, device_scale_factor=2).new_page()
        try:
            pg.set_content(page, wait_until="networkidle", timeout=25000)
            try:  # never wait forever for web fonts
                pg.wait_for_function("document.fonts.status === 'loaded'", timeout=10000)
            except Exception:
                print("fonts slow - drawing with fallback fonts")
            ok = pg.evaluate("() => { const c = document.querySelector('.card');"
                             " return !!c && getComputedStyle(c).borderTopLeftRadius === '30px' }")
            return pg.locator(".card").screenshot(omit_background=True, timeout=10000) if ok else None
        finally:
            b.close()


# ------------------------------------------------------------------ discord
def post(content, png=None, color=0xF43F5E):
    if not WEBHOOK:
        print("[no DISCORD_WEBHOOK_URL - not sent]", content)
        return None
    payload = {"content": content}
    if png:
        payload = {"embeds": [{"color": color, "image": {"url": "attachment://rubies.png"}}]}
        bnd = "----ru" + os.urandom(8).hex()
        body = (f"--{bnd}\r\nContent-Disposition: form-data; name=\"payload_json\"\r\n"
                f"Content-Type: application/json\r\n\r\n{json.dumps(payload)}\r\n"
                f"--{bnd}\r\nContent-Disposition: form-data; name=\"files[0]\"; filename=\"rubies.png\"\r\n"
                f"Content-Type: image/png\r\n\r\n").encode() + png + f"\r\n--{bnd}--\r\n".encode()
        ctype = f"multipart/form-data; boundary={bnd}"
    else:
        body, ctype = json.dumps(payload).encode(), "application/json"
    req = urllib.request.Request(WEBHOOK, data=body, headers={"Content-Type": ctype, "User-Agent": "pu-rubies/1.0"})
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                print("discord status", resp.status)
                return resp.status
        except urllib.error.HTTPError as ex:
            if attempt == 3 or not (ex.code == 429 or ex.code >= 500):
                raise
            time.sleep(3 + attempt * 3)


def send_report(st):
    r = report(st)
    if not r["me"]:
        post(f"\U0001F48E **Rubies tracker:** {ME} isn't in the top {len(r['rows'])} of the Rubies board right now.")
        return 0
    print(json.dumps({k: r.get(k) for k in ("me", "win", "today")}, default=str))
    png = None
    try:
        png = render(build_html(r))
    except Exception as ex:
        print("rubies card failed:", str(ex)[:150])
    if png:
        post("", png)
    else:  # text fallback
        m, w, t = r["me"], r.get("win"), r.get("today")
        post(f"\U0001F48E **Rubies** · #{m['rank']} · {short(m['value'])}"
             + (f"\nLast {dur(w['hours'])}: {short(w['gain'], True)} ({short(w['rate'])}/h)" if w else "")
             + (f"\nToday: {short(t['gain'], True)} {t['label']}" if t else ""))
    return 0


def main():
    return send_report(snapshot())


if __name__ == "__main__":
    sys.exit(main())
