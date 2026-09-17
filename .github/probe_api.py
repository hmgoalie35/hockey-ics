"""TEMPORARY probe: explore the Bond Sports API from a GitHub runner."""
import json, os, re, requests
from urllib.parse import urljoin

H = {"Accept": "application/json, text/plain, */*",
     "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/128 Safari/537.36",
     "Origin": "https://bondsports.co", "Referer": "https://bondsports.co/"}
API = "https://api.bondsports.co/"
UUID = "180251ce-9fbc-4153-b7f6-ce3530a2c7f9"
STAGE = 153
TEAM = 1254

def shrink(x):
    if isinstance(x, list):
        return [shrink(i) for i in x[:2]] + ([f"...(+{len(x)-2} more)"] if len(x) > 2 else [])
    if isinstance(x, dict):
        return {k: shrink(v) for k, v in x.items()}
    if isinstance(x, str) and len(x) > 200:
        return x[:200] + "..."
    return x

def get(u, limit=5000):
    print("\n" + "=" * 100 + f"\nGET {u}")
    try:
        r = requests.get(u, headers=H, timeout=30)
        print("STATUS", r.status_code, "CT", r.headers.get("content-type"), "LEN", len(r.content))
        try:
            d = r.json()
        except Exception:
            print(r.text[:800]); return None
        top = f"list[{len(d)}]" if isinstance(d, list) else f"dict keys={list(d.keys())}"
        print("TOP", top)
        print(json.dumps(shrink(d), indent=1)[:limit])
        return d if r.ok else None
    except Exception as e:
        print("ERR", e); return None

def find_all(obj, key, out=None):
    out = out if out is not None else []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == key: out.append(v)
            find_all(v, key, out)
    elif isinstance(obj, list):
        for i in obj: find_all(i, key, out)
    return out

# --- Competition-level (uuid known) ---
comp = get(f"{API}v4/competitions/{UUID}")
get(f"{API}v4/competitions/{UUID}/stages")
get(f"{API}v4/competitions/{UUID}/stages/{STAGE}")
games = get(f"{API}v4/competitions/{UUID}/stages/{STAGE}/game-scores?teamId={TEAM}", limit=2500)
get(f"{API}v4/competitions/{UUID}/stages/{STAGE}/standings", limit=2500)
for tail in ["public-settings", "teams", "divisions", "rosters", f"stages/{STAGE}/rosters", f"stages/{STAGE}/teams", "program-season", "season"]:
    get(f"{API}v4/competitions/{UUID}/{tail}", limit=1500)

# --- Organization-level ---
org_ids = set(str(x) for x in find_all(comp, "organizationId") + find_all(games, "organizationId") if x is not None)
org_ids |= set(x.strip() for x in os.environ.get("ORG_IDS", "").split(",") if x.strip())
print("\n\nORG IDS:", org_ids)
program_ids, season_ids = set(), set()
for org in sorted(org_ids):
    get(f"{API}v1/organizations/{org}", limit=1500)
    get(f"{API}v4/organizations/{org}", limit=1500)
    get(f"{API}v1/organizations/{org}/leagues", limit=3000)
    for pt in (0, 10):
        d = get(f"{API}v1/programs/organization/{org}/{pt}", limit=3000)
        for pid in find_all(d, "id")[:0]: pass
    d = get(f"{API}v4/organizations/{org}/programs?programTypes=league&includePast=true&itemsPerPage=50", limit=4000)
    d2 = get(f"{API}v4/organizations/{org}/programs?includePast=true&expand=sessions&itemsPerPage=50", limit=6000)
    get(f"{API}v4/organizations/{org}/competitions", limit=4000)
    get(f"{API}v4/organizations/{org}/sessions?includePast=true&itemsPerPage=50", limit=4000)
    get(f"{API}v4/organizations/{org}/program-seasons?includePast=true", limit=3000)
    get(f"{API}v4/competitions?organizationId={org}", limit=3000)
    for d_ in (d, d2):
        if isinstance(d_, dict):
            for p in (d_.get("data") or []):
                if isinstance(p, dict) and p.get("id") is not None:
                    program_ids.add(p["id"])
                    for s in ((p.get("sessions") or {}).get("data") if isinstance(p.get("sessions"), dict) else (p.get("sessions") or [])) or []:
                        if isinstance(s, dict) and s.get("id") is not None: season_ids.add(s["id"])

print("\n\nPROGRAM IDS:", sorted(program_ids)[:20], "SEASON IDS:", sorted(season_ids)[:40])
for pid in sorted(program_ids)[:3]:
    get(f"{API}v1/programs/{pid}", limit=2000)
    d = get(f"{API}v1/programs/{pid}/seasons", limit=4000)
    for sid in find_all(d, "id")[:0]: pass
    get(f"{API}v1/programs/program/{pid}/sessions/landing-page", limit=3000)
    get(f"{API}v4/programs/{pid}/sessions?includePast=true&itemsPerPage=50", limit=4000)
    for s in ((d or {}).get("data") if isinstance(d, dict) else (d or [])) or []:
        if isinstance(s, dict) and s.get("id") is not None: season_ids.add(s["id"])
for sid in sorted(season_ids)[:6]:
    for u in [f"v4/program-seasons/{sid}", f"v4/program-seasons/{sid}/competition", f"v4/competitions/program-season/{sid}",
              f"v4/competitions/by-program-season/{sid}", f"v4/sessions/{sid}", f"v4/competitions/sessions/{sid}",
              f"v1/programs/program/0/session/{sid}/landing-page"]:
        get(f"{API}{u}", limit=2000)

# --- Scrape the consumer web app JS bundles for API route templates ---
for page in ["https://bondsports.co/", "https://bondsports.co/activity/leagues/adult-hockey-league/658"]:
    print("\n" + "#" * 100 + f"\nPAGE {page}")
    try:
        r = requests.get(page, headers={"User-Agent": H["User-Agent"]}, timeout=30)
        html = r.text
        print("STATUS", r.status_code, "LEN", len(html))
        print(html[:1500])
        scripts = re.findall(r'<script[^>]+src="([^"]+)"', html)
        print("SCRIPTS", scripts[:30])
        found = set()
        for s in scripts[:40]:
            su = urljoin(page, s)
            try:
                js = requests.get(su, headers={"User-Agent": H["User-Agent"]}, timeout=60).text
            except Exception as e:
                print("JS ERR", su, e); continue
            for m in re.findall(r'["`\']([^"`\']{0,80}(?:v[1-4]/|api\.bondsports)[^"`\']{0,160})["`\']', js):
                found.add(m)
            for m in re.findall(r'["`\'][^"`\']{0,80}(?:competitions|standings|game-scores|program-seasons|my-teams)[^"`\']{0,120}["`\']', js):
                found.add(m)
        print("ROUTES FOUND:", len(found))
        for f in sorted(found): print("   ", f)
    except Exception as e:
        print("ERR", e)
