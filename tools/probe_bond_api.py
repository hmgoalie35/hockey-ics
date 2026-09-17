#!/usr/bin/env python3
"""
One-off helper: discover how Bond Sports maps a program season to its competition.

Usage:
    python3 tools/probe_bond_api.py "<bondsports.co URL of the current season>"

The URL is the page you'd open to see the team's schedule/standings, e.g.
    https://bondsports.co/activity/programs/<program-name>/<programId>/season/<season-name>/<seasonId>/competition
(the trailing /competition is optional).

Paste the whole output back. Only public, unauthenticated GET requests are made.
Needs only the Python standard library.
"""
import json, re, sys, urllib.request, urllib.error
from urllib.parse import urljoin

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/128 Safari/537.36"
API = "https://api.bondsports.co/"


def http_get(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json, text/html, */*",
                                               "Origin": "https://bondsports.co", "Referer": "https://bondsports.co/"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:
        return "ERR", str(e)


def shrink(x):
    if isinstance(x, list):
        return [shrink(i) for i in x[:3]] + ([f"...(+{len(x)-3} more)"] if len(x) > 3 else [])
    if isinstance(x, dict):
        return {k: shrink(v) for k, v in x.items()}
    if isinstance(x, str) and len(x) > 160:
        return x[:160] + "..."
    return x


def get_json(url, limit=2500):
    print("\n" + "=" * 90 + f"\nGET {url}")
    status, body = http_get(url)
    print("STATUS", status, "LEN", len(body))
    try:
        d = json.loads(body)
    except Exception:
        print(body[:400])
        return None
    print(json.dumps(shrink(d), indent=1)[:limit])
    return d if status == 200 else None


def find_all(obj, key, out=None):
    out = [] if out is None else out
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == key:
                out.append(v)
            find_all(v, key, out)
    elif isinstance(obj, list):
        for i in obj:
            find_all(i, key, out)
    return out


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    page_url = sys.argv[1].strip()
    m = re.search(r"/programs/[^/]+/(\d+)/season/[^/]+/(\d+)", page_url)
    program_id, season_id = (m.group(1), m.group(2)) if m else (None, None)
    print("PROGRAM ID:", program_id, "SEASON ID:", season_id)

    # 1) The page itself (server-rendered data + chunk list)
    print("\n" + "#" * 90 + "\n# 1) Page HTML")
    status, html = http_get(page_url)
    print("STATUS", status, "LEN", len(html))
    nd = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    if nd:
        try:
            data = json.loads(nd.group(1))
            print("__NEXT_DATA__ page:", data.get("page"), "query:", data.get("query"))
            print(json.dumps(shrink(data.get("props", {}).get("pageProps", {})), indent=1)[:6000])
        except Exception as e:
            print("NEXT_DATA parse error", e)
    build = re.search(r'/_next/static/([^/"]+)/_buildManifest\.js', html)
    build = build.group(1) if build else None
    print("BUILD ID:", build)

    # 2) Find the competition page chunk and print how it fetches the competition
    print("\n" + "#" * 90 + "\n# 2) Competition page JS: API route templates")
    chunks = set(re.findall(r'<script[^>]+src="([^"]+)"', html))
    if build:
        _, bm = http_get(f"https://bondsports.co/_next/static/{build}/_buildManifest.js")
        for c in re.findall(r'"(static/chunks/[^"]+\.js)"', bm):
            if "competition" in c or "my-teams" in c or c.startswith("static/chunks/3392") or "season" in c:
                chunks.add("/_next/" + c)
    for c in sorted(chunks):
        if not re.search(r"competition|my-teams|3392|season|_app", c):
            continue
        _, js = http_get(urljoin("https://bondsports.co/", c), timeout=60)
        hits = set(re.findall(r'`([^`]{0,60}(?:competition|program-season|programs-season|/season)[^`]{0,120})`', js))
        if not hits and "competitions/" not in js:
            continue
        print(f"\n--- {c} ({len(js)} chars)")
        for h in sorted(hits):
            print("   TEMPLATE:", h)
        for kw in ("startsOn?new Date(e.startsOn)", "competitionUuid", 'queryFn:()=>(0,'):
            for mm in list(re.finditer(re.escape(kw), js))[:2]:
                s, e = max(0, mm.start() - 900), min(len(js), mm.end() + 300)
                print(f"\n   CONTEXT[{kw}]: ...{js[s:e]}...")
        # base-url variables used with competitions/ templates
        for mm in re.finditer(r'const (\w)=(["`][^"`]{0,80}["`]|[\w.]+\+?["`][^"`]{0,60}["`]);', js):
            if "api" in mm.group(2) or "v4" in mm.group(2):
                print("   BASE:", mm.group(0)[:160])

    # 3) Season / program API guesses
    print("\n" + "#" * 90 + "\n# 3) Program / season API")
    comp = None
    if season_id:
        for u in [f"v4/programs-seasons/{season_id}", f"v1/programs/season/{season_id}",
                  f"v4/competitions/season/{season_id}", f"v4/competitions/program-season/{season_id}",
                  f"v4/competitions/programs-seasons/{season_id}", f"v4/program-seasons/{season_id}/competition",
                  f"v4/programs-seasons/{season_id}/competition", f"v4/competitions/by-season/{season_id}",
                  f"v4/competitions?programSeasonId={season_id}", f"v4/competitions?seasonId={season_id}"]:
            d = get_json(API + u)
            if d and find_all(d, "uuid") and find_all(d, "stages"):
                comp = comp or d
    if program_id:
        for u in [f"v4/programs-seasons/program/{program_id}", f"v1/programs/{program_id}/seasons",
                  f"v1/programs/{program_id}", f"v4/programs/{program_id}",
                  f"v1/programs/program/{program_id}/sessions/landing-page"]:
            get_json(API + u, limit=4000)

    # 4) If we found a competition, show each stage's schedule summary
    print("\n" + "#" * 90 + "\n# 4) Competition -> stages -> game-scores")
    if comp:
        uuids = find_all(comp, "uuid")
        stages = [s for lst in find_all(comp, "stages") for s in (lst if isinstance(lst, list) else [])]
        for uuid in uuids[:2]:
            for s in stages:
                sid = s.get("id") if isinstance(s, dict) else s
                status, body = http_get(f"{API}v4/competitions/{uuid}/stages/{sid}/game-scores")
                try:
                    games = json.loads(body)
                    if isinstance(games, dict):
                        games = games.get("data") or []
                    teams = sorted({t["name"] for g in games for t in (g["homeTeam"], g["awayTeam"])})
                    starts = sorted(g["startDateTime"] for g in games)
                    print(f"uuid={uuid} stage={sid} ({s.get('name') if isinstance(s, dict) else ''}): {status} games={len(games)} "
                          f"span={starts[0] if starts else None}..{starts[-1] if starts else None}\n   teams={teams}")
                except Exception:
                    print(f"uuid={uuid} stage={sid}: {status} {body[:200]}")
    else:
        print("No competition object found above; the templates in section 2 should show the right route.")


if __name__ == "__main__":
    main()
