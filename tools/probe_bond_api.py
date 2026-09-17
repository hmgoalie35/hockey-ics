"""TEMPORARY probe v3: explore the Bond Sports API from a GitHub runner."""
import json, os, re, requests
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urljoin

H = {"Accept": "application/json, text/plain, */*",
     "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/128 Safari/537.36",
     "Origin": "https://bondsports.co", "Referer": "https://bondsports.co/"}
API = "https://api.bondsports.co/"
A = "180251ce-9fbc-4153-b7f6-ce3530a2c7f9"   # Alligator Skinners Winter 2026 D3, stage 153
B = "4fd5e832-5d86-400a-af3f-7a01c20d1e4e"   # Orcas Winter 2026 6A, stage 160
C = "00d2c916-8370-463c-8d83-01c9ce2de443"   # Grocery Sticks Winter 2026 5A, stage 158

def shrink(x):
    if isinstance(x, list):
        return [shrink(i) for i in x[:2]] + ([f"...(+{len(x)-2} more)"] if len(x) > 2 else [])
    if isinstance(x, dict):
        return {k: shrink(v) for k, v in x.items()}
    if isinstance(x, str) and len(x) > 200:
        return x[:200] + "..."
    return x

def get(u, limit=3000):
    print("\n" + "=" * 100 + f"\nGET {u}")
    try:
        r = requests.get(u, headers=H, timeout=30)
        print("STATUS", r.status_code, "LEN", len(r.content))
        try:
            d = r.json()
        except Exception:
            print(r.text[:800]); return None
        print(json.dumps(shrink(d), indent=1)[:limit])
        return d if r.ok else None
    except Exception as e:
        print("ERR", e); return None

def games_summary(d):
    if not isinstance(d, list):
        return str(d)[:200]
    teams = sorted({t["name"] for g in d for t in (g["homeTeam"], g["awayTeam"])})
    divs = sorted({g["homeTeam"].get("divisionName") or "" for g in d})
    starts = sorted(g["startDateTime"] for g in d)
    return f"games={len(d)} stages={sorted({g.get('stageName') for g in d})} divs={divs} span={starts[0] if starts else None}..{starts[-1] if starts else None} teams={teams}"

print("#" * 100 + "\n# 1) Is the competition UUID enforced per stage?")
for uuid, stage in [(A, 160), (A, 158), (B, 153), (A, 153)]:
    r = requests.get(f"{API}v4/competitions/{uuid}/stages/{stage}/game-scores", headers=H, timeout=30)
    body = r.json() if r.headers.get("content-type", "").startswith("application/json") else r.text[:300]
    print(f"\n{uuid[:8]}/stages/{stage}: STATUS {r.status_code} -> {games_summary(body) if r.ok else str(body)[:300]}")

print("\n" + "#" * 100 + "\n# 2) Enumerate stage ids under competition A")
def probe_stage(stage):
    try:
        r = requests.get(f"{API}v4/competitions/{A}/stages/{stage}/game-scores", headers=H, timeout=30)
        if r.ok:
            return stage, r.status_code, games_summary(r.json())
        return stage, r.status_code, (r.json().get("message") if "json" in r.headers.get("content-type", "") else r.text[:120])
    except Exception as e:
        return stage, "ERR", str(e)[:120]
with ThreadPoolExecutor(8) as ex:
    for stage, status, info in ex.map(probe_stage, range(100, 420)):
        if status == 200 or stage in (150, 153, 160, 200, 300):
            print(f"stage {stage}: {status} {info}")

print("\n" + "#" * 100 + "\n# 3) More competition-level guesses")
for u in [f"v4/standings-integration/competitions/{A}", f"v4/consumer/competitions/{A}", f"v4/competitions/{A}/info",
          f"v4/competitions/{A}/details", f"v4/competitions/{A}/summary", f"v4/competitions/{A}/stages/153/games",
          f"v4/competitions/{A}/stages/153/schedule", f"v4/competitions/{A}/stages/153/divisions",
          f"v4/competitions/{A}/game-scores", f"v4/competitions/{A}/standings", f"v4/competitions/uuid/{A}",
          f"v4/league-standings/competitions/{A}", f"v4/leagues/competitions/{A}"]:
    get(f"{API}{u}", limit=1200)

print("\n" + "#" * 100 + "\n# 4) Next.js bundles of bondsports.co (consumer app) -> API route templates")
UA = {"User-Agent": H["User-Agent"]}
html = requests.get("https://bondsports.co/some-page-that-does-not-exist", headers=UA, timeout=30).text
m = re.search(r'/_next/static/([^/"]+)/_buildManifest\.js', html)
build = m.group(1) if m else None
print("BUILD ID:", build)
chunks = set(re.findall(r'<script[^>]+src="([^"]+)"', html))
if build:
    bm = requests.get(f"https://bondsports.co/_next/static/{build}/_buildManifest.js", headers=UA, timeout=30).text
    print("BUILD MANIFEST (first 6000 chars):\n", bm[:6000])
    for c in re.findall(r'"(static/chunks/[^"]+\.js)"', bm):
        chunks.add("/_next/" + c)
    pages = re.findall(r'"(/[^"]*)":\[', bm)
    print("PAGES:", pages)
print("CHUNKS:", len(chunks))
found, ctx = set(), []
def fetch_chunk(c):
    try:
        return c, requests.get(urljoin("https://bondsports.co/", c), headers=UA, timeout=60).text
    except Exception as e:
        return c, ""
with ThreadPoolExecutor(8) as ex:
    for c, js in ex.map(fetch_chunk, sorted(chunks)):
        for m in re.findall(r'[`"\']([^`"\']{0,80}(?:v[1-4]/|api\.bondsports)[^`"\']{0,200})[`"\']', js):
            found.add(m)
        for kw in ("game-scores", "standingsAndScoresLink", "standingsFor", "/standings", "competitionId", "competitionUuid", "stageId"):
            for mm in re.finditer(re.escape(kw), js):
                s = max(0, mm.start() - 250); e = min(len(js), mm.end() + 250)
                ctx.append(f"[{c.split('/')[-1]}] ...{js[s:e]}...")
print("\nROUTE TEMPLATES:", len(found))
for f in sorted(found): print("   ", f)
print("\nCONTEXT SNIPPETS:", len(ctx))
seen = set()
for s in ctx[:80]:
    k = s[:120]
    if k in seen: continue
    seen.add(k); print("\n" + s.replace("\n", " "))
