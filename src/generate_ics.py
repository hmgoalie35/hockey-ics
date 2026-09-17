#!/usr/bin/env python3
"""
hockey-ics: generate .ics feeds from Bond Sports API.

Feature 1) Opponent games before this matchup:
  - Include ALL opponent games whose start < this matchup start (even future games between now and matchup).
  - If scores exist: include W/L/T X-Y
  - If scores missing: include the game line but omit the result.

Feature 2) Standings snapshot per event:
  - Future events: standings updated each run (as-of timestamp included)
  - Past events (game start passed): standings frozen (kept as it was first time it crossed into the past)
  - Snapshots persisted under: docs/_state/<calendar-namespace>.json

Formatting improvements:
  - ASCII separators for sections
  - Compact opponent list lines with local (config) timezone display
  - Head-to-head section vs opponent

Config expected:
output_dir: "docs"
default_timezone: "America/New_York"
teams:
  - name: "Alligator Skinners"        # team name as it appears in Bond Sports
    slug: "alligator-skinners"        # feed file: docs/<slug>.ics (season-agnostic)
    aliases: ["old-feed-name"]        # optional: extra copies of the feed (legacy URLs)
    team_names: ["Alligator Skinners"]  # optional: alternate spellings used to find the team
    calendar_name: "..."              # optional
    opponent_recent_max: 20           # optional
    head_to_head_max: 20              # optional
    program_id: 12070                 # optional: Bond Sports program -> seasons auto-discovered
    seasons:                          # optional: explicit seasons (always included)
      - league_name: "Winter 2026 Division 3"
        competition_id: "<bond sports competition uuid>"
        stage_id: 153
        team_id: 1254                 # optional: resolved by name when omitted
        # or, instead of competition_id/stage_id:
        # api_url: ... game-scores
        # standings_api_url: ... standings

All seasons of a team are merged into one .ics so the subscription URL never
changes between seasons. With `program_id`, every season of that Bond Sports
program is checked for a team matching `name`, so new seasons are picked up
automatically (see discover_team_seasons). Discovered seasons are cached in the
state file so the feed survives a discovery outage. Standings snapshots are persisted under
docs/_state/<slug>.json (keyed by Bond Sports event id, which is global).
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests
import yaml

try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover
    ZoneInfo = None  # type: ignore


# -------------------------
# Helpers
# -------------------------

def utc_now() -> datetime:
    return datetime.now(timezone.utc)

def parse_iso_z(dt_str: str) -> datetime:
    # "2026-01-20T01:30:00.000Z" or without ms
    if dt_str.endswith("Z"):
        dt_str = dt_str[:-1] + "+00:00"
    return datetime.fromisoformat(dt_str)

def fmt_dt_utc_for_ics(dt: datetime) -> str:
    dt = dt.astimezone(timezone.utc)
    return dt.strftime("%Y%m%dT%H%M%SZ")

def ics_escape(text: str) -> str:
    text = text.replace("\\", "\\\\")
    text = text.replace("\n", "\\n")
    text = text.replace(";", r"\;")
    text = text.replace(",", r"\,")
    return text

def stable_uid(namespace: str, event_id: Any) -> str:
    raw = f"{namespace}:{event_id}".encode("utf-8")
    h = hashlib.sha256(raw).hexdigest()[:24]
    return f"{h}@hockey-ics"

def slugify(s: str) -> str:
    s = s.strip().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = re.sub(r"-{2,}", "-", s).strip("-")
    return s or "calendar"

def fetch_json(url: str, timeout: int = 30) -> Any:
    r = requests.get(url, timeout=timeout)
    r.raise_for_status()
    return r.json()

def ascii_rule(title: str, width: int = 40) -> List[str]:
    line = "-" * width
    return [line, title, line]

def ordinal(n: int) -> str:
    # 1st/2nd/3rd/4th...
    if 10 <= (n % 100) <= 20:
        suf = "th"
    else:
        suf = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suf}"

def fmt_month_day_local(dt: datetime, tz: timezone) -> str:
    # e.g. "Jan 20th"
    d = dt.astimezone(tz)
    mon = d.strftime("%b")
    day = int(d.strftime("%d"))
    return f"{mon} {ordinal(day)}"

def fmt_start_local(dt: datetime, tz: timezone) -> str:
    # e.g. "2026-01-19 20:30 EST"
    return dt.astimezone(tz).strftime("%Y-%m-%d %H:%M %Z")


# -------------------------
# Models
# -------------------------

@dataclass
class TeamRef:
    id: int
    name: str
    score: Optional[int]
    division_name: Optional[str] = None

@dataclass
class SpaceRef:
    name: Optional[str]

@dataclass
class Game:
    event_id: Any
    game_id: Any
    stage_name: Optional[str]
    status: str
    start: datetime
    end: datetime
    home: TeamRef
    away: TeamRef
    space: SpaceRef

    def involves_team_id(self, team_id: int) -> bool:
        return self.home.id == team_id or self.away.id == team_id

    @property
    def has_result(self) -> bool:
        return self.home.score is not None and self.away.score is not None

    @property
    def is_final(self) -> bool:
        return (self.status or "").lower() == "final" and self.has_result


def parse_games(raw_games: Any) -> List[Game]:
    # Bond Sports returns a bare list from the consumer endpoint, but the
    # paginated variant wraps it as {"data": [...], "meta": {...}}.
    if isinstance(raw_games, dict):
        raw_games = raw_games.get("data") or []
    games: List[Game] = []
    for g in raw_games:
        start = parse_iso_z(g["startDateTime"])
        end = parse_iso_z(g["endDateTime"])
        if end <= start:
            end = start + timedelta(minutes=80)

        games.append(
            Game(
                event_id=g.get("eventId"),
                game_id=g.get("gameId"),
                stage_name=g.get("stageName"),
                status=g.get("status") or "scheduled",
                start=start,
                end=end,
                home=TeamRef(
                    id=int(g["homeTeam"]["id"]),
                    name=str(g["homeTeam"]["name"]),
                    score=g["homeTeam"].get("score"),
                    division_name=g["homeTeam"].get("divisionName"),
                ),
                away=TeamRef(
                    id=int(g["awayTeam"]["id"]),
                    name=str(g["awayTeam"]["name"]),
                    score=g["awayTeam"].get("score"),
                    division_name=g["awayTeam"].get("divisionName"),
                ),
                space=SpaceRef(name=(g.get("space") or {}).get("name")),
            )
        )
    return games


# -------------------------
# Feature: results helpers
# -------------------------

def result_for_team(game: Game, team_id: int) -> Optional[str]:
    """Return 'W 5-3' / 'L 2-6' / 'T 3-3' if scores exist, else None."""
    if not game.has_result:
        return None

    if game.home.id == team_id:
        gf, ga = game.home.score, game.away.score
    elif game.away.id == team_id:
        gf, ga = game.away.score, game.home.score
    else:
        return None

    if gf is None or ga is None:
        return None

    if gf > ga:
        prefix = "W"
    elif gf < ga:
        prefix = "L"
    else:
        prefix = "T"
    return f"{prefix} {gf}-{ga}"


# -------------------------
# Feature 1: opponent games (compact, local dates)
# -------------------------

def opponent_games_lines_compact(
    all_games: List[Game],
    opponent_id: int,
    cutoff_start: datetime,
    tz: timezone,
    max_lines: int = 20,
) -> List[str]:
    """
    Include ALL opponent games with start < cutoff_start (regardless of status).

    Compact format (no repeating opponent name):
        "    Jan 20th vs Backdoor Bandits (W 10-5)"
        "    Feb 3rd @ Dirty Mike and the Boys"
    """
    prior = [g for g in all_games if g.involves_team_id(opponent_id) and g.start < cutoff_start]
    prior.sort(key=lambda g: g.start)

    lines: List[str] = []
    for g in prior[-max_lines:]:
        opp_is_home = (g.home.id == opponent_id)
        other = g.away if opp_is_home else g.home
        at_vs = "vs" if opp_is_home else "@"

        res = result_for_team(g, opponent_id)
        date_str = fmt_month_day_local(g.start, tz)

        if res:
            lines.append(f"    {date_str} {at_vs} {other.name} ({res})")
        else:
            lines.append(f"    {date_str} {at_vs} {other.name}")
    return lines


# -------------------------
# Feature: head-to-head vs opponent (compact, local dates)
# -------------------------

def head_to_head_lines(
    all_games: List[Game],
    my_team_id: int,
    opponent_id: int,
    cutoff_start: datetime,
    tz: timezone,
    max_lines: int = 20,
) -> List[str]:
    """
    Prior matchups between my team and opponent, before cutoff_start.
    Compact formatting from my team perspective.
    """
    h2h = [
        g for g in all_games
        if g.start < cutoff_start
        and g.involves_team_id(my_team_id)
        and g.involves_team_id(opponent_id)
    ]
    h2h.sort(key=lambda g: g.start)

    lines: List[str] = []
    for g in h2h[-max_lines:]:
        my_is_home = (g.home.id == my_team_id)
        at_vs = "vs" if my_is_home else "@"
        opp_name = g.away.name if my_is_home else g.home.name

        res = result_for_team(g, my_team_id)
        date_str = fmt_month_day_local(g.start, tz)

        if res:
            lines.append(f"    {date_str} {at_vs} {opp_name} ({res})")
        else:
            lines.append(f"    {date_str} {at_vs} {opp_name}")
    return lines


# -------------------------
# Feature 2: standings parsing & formatting
# -------------------------

def pick_division_standings(raw: Any, my_team_id: int) -> List[Dict[str, Any]]:
    """
    Raw standings example:
    [
      { divisionId, divisionName, standings: [ {team:{id,name}, rank, points, wins, losses, ...}, ... ] },
      ...
    ]

    We pick the division that contains my team id if possible.
    If not found, fallback to first division's standings.
    """
    if not isinstance(raw, list) or not raw:
        return []

    for div in raw:
        if not isinstance(div, dict):
            continue
        rows = div.get("standings")
        if not isinstance(rows, list):
            continue
        for r in rows:
            team = (r or {}).get("team") if isinstance(r, dict) else None
            if isinstance(team, dict) and int(team.get("id", -1)) == my_team_id:
                return rows

    for div in raw:
        if isinstance(div, dict) and isinstance(div.get("standings"), list):
            return div["standings"]

    return []

def format_standings_lines(rows: List[Dict[str, Any]], max_rows: int = 20) -> List[str]:
    def key(r: Dict[str, Any]) -> Tuple[int, str]:
        try:
            rk = int(r.get("rank", 9999))
        except Exception:
            rk = 9999
        team = r.get("team") if isinstance(r, dict) else None
        name = team.get("name") if isinstance(team, dict) else ""
        return (rk, str(name))

    rows_sorted = sorted(rows, key=key)[:max_rows]
    out: List[str] = []
    for r in rows_sorted:
        team = r.get("team") if isinstance(r, dict) else None
        name = team.get("name") if isinstance(team, dict) else "Unknown"
        rank = r.get("rank")
        wins = r.get("wins")
        losses = r.get("losses")
        points = r.get("points")

        bits = []
        if rank is not None:
            bits.append(f"{rank}.")
        bits.append(str(name))

        rec = []
        if wins is not None and losses is not None:
            rec.append(f"{wins}-{losses}")
        if points is not None:
            rec.append(f"{points} pts")

        if rec:
            bits.append("(" + ", ".join(map(str, rec)) + ")")

        out.append(" ".join(bits))
    return out


# -------------------------
# ICS building
# -------------------------

def build_ics_calendar(cal_name: str, events: List[str]) -> str:
    header = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//hockey-ics//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{ics_escape(cal_name)}",
    ]
    footer = ["END:VCALENDAR"]
    return "\r\n".join(header + events + footer) + "\r\n"

def build_vevent(
    uid: str,
    summary: str,
    dtstart: datetime,
    dtend: datetime,
    description: str,
    location: str,
    last_modified: datetime,
) -> str:
    lines = [
        "BEGIN:VEVENT",
        f"UID:{ics_escape(uid)}",
        f"DTSTAMP:{fmt_dt_utc_for_ics(utc_now())}",
        f"LAST-MODIFIED:{fmt_dt_utc_for_ics(last_modified)}",
        f"DTSTART:{fmt_dt_utc_for_ics(dtstart)}",
        f"DTEND:{fmt_dt_utc_for_ics(dtend)}",
        f"SUMMARY:{ics_escape(summary)}",
    ]
    if location:
        lines.append(f"LOCATION:{ics_escape(location)}")
    if description:
        lines.append(f"DESCRIPTION:{ics_escape(description)}")
    lines.append("END:VEVENT")
    return "\r\n".join(lines)

def my_title(my_team_id: int, my_team_name: str, game: Game) -> Tuple[str, Optional[str], int, str]:
    """Return (title, my_result, opp_id, opp_name). Title always lists my team first."""
    if game.home.id == my_team_id:
        opp_id, opp_name = game.away.id, game.away.name
        core = f"{my_team_name} vs {opp_name}"
    else:
        opp_id, opp_name = game.home.id, game.home.name
        core = f"{my_team_name} @ {opp_name}"

    res = result_for_team(game, my_team_id)
    if res:
        return f"{core} ({res})", res, opp_id, opp_name
    return core, None, opp_id, opp_name


# -------------------------
# State: standings snapshots
# -------------------------

def load_state(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {"events": {}}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"events": {}}

def save_state(path: Path, state: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")

def freeze_for_game(game: Game, now: datetime) -> bool:
    """
    Freeze standings only when the game is truly complete.

    This prevents a moved/rescheduled game from staying "frozen" just because its
    original start time passed, and avoids freezing games where the league is late
    posting results.
    """
    if game.is_final:
        return True
    if game.has_result:
        return True
    return False


# -------------------------
# Seasons & team resolution
# -------------------------

BOND_API = "https://api.bondsports.co/v4"


def season_urls(season: Dict[str, Any]) -> Tuple[str, Optional[str]]:
    """Return (game_scores_url, standings_url) for a season config entry."""
    if season.get("api_url"):
        return str(season["api_url"]), (str(season["standings_api_url"]) if season.get("standings_api_url") else None)
    comp = season.get("competition_id")
    stage = season.get("stage_id")
    if not comp or stage is None:
        raise SystemExit(f"Season {season.get('league_name')!r}: need competition_id + stage_id (or api_url).")
    base = f"{BOND_API}/competitions/{comp}/stages/{int(stage)}"
    return f"{base}/game-scores", f"{base}/standings"


def resolve_team_id(all_games: List[Game], names: List[str]) -> Optional[int]:
    """Find the numeric team id whose name matches one of `names` (case/space-insensitive)."""
    wanted = {re.sub(r"\s+", " ", n).strip().lower() for n in names if n}
    for g in all_games:
        for t in (g.home, g.away):
            if re.sub(r"\s+", " ", t.name).strip().lower() in wanted:
                return t.id
    return None


def team_names_in(all_games: List[Game]) -> List[str]:
    return sorted({t.name for g in all_games for t in (g.home, g.away)})


# -------------------------
# Season auto-discovery (Bond Sports program -> seasons -> competition -> stages)
# -------------------------

def _list_items(raw: Any) -> List[Dict[str, Any]]:
    """Accept a bare list or a paginated {data: [...]} envelope."""
    if isinstance(raw, dict):
        raw = raw.get("data") or raw.get("items") or []
    return [x for x in (raw or []) if isinstance(x, dict)]


def _parse_date(value: Any) -> Optional[datetime]:
    if not value or not isinstance(value, str):
        return None
    try:
        dt = parse_iso_z(value[:19] + ("Z" if len(value) <= 19 else value[19:]))
    except Exception:
        try:
            dt = datetime.fromisoformat(value[:10])
        except Exception:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def fetch_program_seasons(program_id: int) -> List[Dict[str, Any]]:
    """Seasons ("sessions") of a Bond Sports program: [{id, name, start, end}, ...]."""
    raw = fetch_json(f"{BOND_API}/programs-seasons/program/{int(program_id)}")
    out: List[Dict[str, Any]] = []
    for s in _list_items(raw):
        if s.get("id") is None:
            continue
        out.append({
            "id": int(s["id"]),
            "name": str(s.get("name") or s["id"]),
            "start": _parse_date(s.get("startDate")),
            "end": _parse_date(s.get("endDate")),
        })
    return out


def fetch_season_competition(season_id: int) -> Optional[Dict[str, Any]]:
    """Competition attached to a program season (None if it has none)."""
    try:
        raw = fetch_json(f"{BOND_API}/program_seasons/{int(season_id)}/competition")
    except requests.HTTPError as e:
        if e.response is not None and e.response.status_code in (400, 404):
            return None
        raise
    if not isinstance(raw, dict) or not raw.get("uuid"):
        return None
    stages = []
    for st in raw.get("stages") or []:
        if isinstance(st, dict) and st.get("id") is not None:
            stages.append({"id": int(st["id"]), "name": st.get("name"), "type": st.get("stageType")})
    stages.sort(key=lambda s: s["id"])
    return {"uuid": str(raw["uuid"]), "name": raw.get("name"), "stages": stages}


def discover_team_seasons(
    program_id: int,
    match_names: List[str],
    cache: Dict[str, Any],
    now: datetime,
) -> Tuple[List[Dict[str, Any]], Dict[str, List[Game]]]:
    """
    Walk every season of the program and return the (competition, stage) pairs in
    which a team matching `match_names` plays, plus the games already fetched.

    `cache` (persisted in the state file) remembers stages already checked:
      cache["stages"][f"{uuid}:{stage_id}"] = {"team": bool, "league_name": str, "season_name": str}
    Positive entries are kept forever (old seasons stay in the feed even if the
    program stops listing them). Negative entries are re-checked while the
    season is still running, since schedules can be published late.
    """
    stage_cache: Dict[str, Any] = cache.setdefault("stages", {})
    seasons_out: List[Dict[str, Any]] = []
    prefetched: Dict[str, List[Game]] = {}

    def add_from_cache(key: str, entry: Dict[str, Any]) -> None:
        uuid, stage_id = key.split(":")
        seasons_out.append({
            "league_name": entry.get("league_name") or entry.get("season_name") or "League",
            "competition_id": uuid,
            "stage_id": int(stage_id),
            "discovered": True,
        })

    seen_keys = set()
    for season in fetch_program_seasons(program_id):
        comp = fetch_season_competition(season["id"])
        if not comp:
            continue
        for st in comp["stages"]:
            key = f"{comp['uuid']}:{st['id']}"
            seen_keys.add(key)
            cached = stage_cache.get(key)
            season_over = season["end"] is not None and season["end"] < now - timedelta(days=7)
            if cached and cached.get("team"):
                add_from_cache(key, cached)
                continue
            if cached and not cached.get("team") and season_over:
                continue

            games = parse_games(fetch_json(f"{BOND_API}/competitions/{comp['uuid']}/stages/{st['id']}/game-scores"))
            team_id = resolve_team_id(games, match_names)
            entry = {"team": team_id is not None, "season_name": season["name"], "stage_name": st.get("name")}
            if team_id is not None:
                mine = next((t for g in games for t in (g.home, g.away) if t.id == team_id), None)
                division = (mine.division_name if mine else None) or ""
                # e.g. "Fall 2026: Division 3"; fall back to the season name
                league_name = division.replace(":", "") if division else season["name"]
                if (st.get("type") or "").lower() == "playoffs" or (st.get("name") or "").lower() == "playoffs":
                    league_name = f"{league_name} Playoffs"
                entry["league_name"] = league_name
                prefetched[key] = games
                print(f"  discovered: {league_name} (competition {comp['uuid']}, stage {st['id']}, team id {team_id})")
            stage_cache[key] = entry
            if entry["team"]:
                add_from_cache(key, entry)

    # Seasons the program no longer lists but that we know the team played in.
    for key, entry in stage_cache.items():
        if entry.get("team") and key not in seen_keys:
            add_from_cache(key, entry)

    return seasons_out, prefetched


# -------------------------
# Main
# -------------------------

def main() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    config_path = repo_root / "config.yaml"

    cfg = yaml.safe_load(config_path.read_text(encoding="utf-8"))

    output_dir = repo_root / str(cfg.get("output_dir", "docs"))
    output_dir.mkdir(parents=True, exist_ok=True)

    state_dir = output_dir / "_state"
    state_dir.mkdir(parents=True, exist_ok=True)

    tz_name = str(cfg.get("default_timezone", "America/New_York"))
    if ZoneInfo is not None:
        try:
            local_tz = ZoneInfo(tz_name)
        except Exception:
            local_tz = timezone.utc
            tz_name = "UTC"
    else:
        local_tz = timezone.utc
        tz_name = "UTC"

    teams = cfg.get("teams", [])
    if not teams:
        raise SystemExit("No teams found in config.yaml under 'teams'.")

    now = utc_now()
    run_asof = now.strftime("%Y-%m-%d %H:%M UTC")

    # Deterministic summary of what each feed contains (used by the workflow to
    # detect a season that has ended with nothing new configured).
    summary: Dict[str, Any] = {}

    for team_entry in teams:
        team_name = str(team_entry["name"])
        slug = slugify(str(team_entry.get("slug") or team_name))
        aliases = [slugify(str(a)) for a in (team_entry.get("aliases") or [])]
        match_names = [team_name] + [str(x) for x in (team_entry.get("team_names") or [])]
        cal_name = str(team_entry.get("calendar_name") or f"{team_name} — Hockey")
        max_recent = int(team_entry.get("opponent_recent_max", 20))
        h2h_max = int(team_entry.get("head_to_head_max", 20))

        seasons: List[Dict[str, Any]] = list(team_entry.get("seasons") or [])
        if team_entry.get("api_url"):  # legacy single-season entry
            seasons.append({
                "league_name": team_entry.get("league_name", team_name),
                "api_url": team_entry["api_url"],
                "standings_api_url": team_entry.get("standings_api_url"),
                "team_id": (team_entry.get("my_team_ids") or [None])[0],
            })
        namespace = slug
        state_path = state_dir / f"{namespace}.json"
        state = load_state(state_path)
        state_events: Dict[str, Any] = state.setdefault("events", {})

        # Auto-discovery of seasons via the Bond Sports program.
        prefetched: Dict[str, List[Game]] = {}
        program_id = team_entry.get("program_id")
        if program_id:
            discovery_cache: Dict[str, Any] = state.setdefault("discovery", {})
            try:
                discovered, prefetched = discover_team_seasons(int(program_id), match_names, discovery_cache, now)
            except Exception as e:  # network / API change: fall back to what we already know
                print(f"WARN {slug}: season discovery failed ({e}); using cached seasons.")
                discovered = []
                for key, entry in (discovery_cache.get("stages") or {}).items():
                    if entry.get("team"):
                        uuid, stage_id = key.split(":")
                        discovered.append({
                            "league_name": entry.get("league_name") or entry.get("season_name") or "League",
                            "competition_id": uuid, "stage_id": int(stage_id), "discovered": True,
                        })
            known = {(str(s.get("competition_id")), int(s["stage_id"])) for s in seasons if s.get("competition_id")}
            for d in discovered:
                if (d["competition_id"], d["stage_id"]) not in known:
                    seasons.append(d)
                    known.add((d["competition_id"], d["stage_id"]))
        if not seasons:
            raise SystemExit(f"Config error for {slug}: no seasons configured or discovered.")

        # (season, my_team_id, my_games, all_games, standings_lines) per season
        loaded: List[Tuple[Dict[str, Any], int, List[Game], List[Game], List[str]]] = []
        for season in seasons:
            league_name = str(season.get("league_name") or team_name)
            games_url, standings_url = season_urls(season)

            key = f"{season.get('competition_id')}:{season.get('stage_id')}"
            all_games = prefetched.get(key) or parse_games(fetch_json(games_url))

            my_team_id: Optional[int] = int(season["team_id"]) if season.get("team_id") is not None else None
            if my_team_id is None:
                my_team_id = resolve_team_id(all_games, match_names)
            if my_team_id is None:
                raise SystemExit(
                    f"{slug} / {league_name}: could not find a team named {match_names} in the schedule. "
                    f"Teams present: {team_names_in(all_games)}"
                )

            standings_lines_current: List[str] = []
            if standings_url:
                try:
                    standings_raw = fetch_json(standings_url)
                    rows = pick_division_standings(standings_raw, my_team_id=my_team_id)
                    standings_lines_current = format_standings_lines(rows)
                except requests.RequestException as e:
                    print(f"WARN {slug} / {league_name}: standings unavailable ({e}); continuing without.")

            my_games = [g for g in all_games if g.involves_team_id(my_team_id)]
            my_games.sort(key=lambda g: g.start)
            loaded.append(({**season, "league_name": league_name}, my_team_id, my_games, all_games, standings_lines_current))

        # Oldest season first so the calendar reads chronologically.
        loaded.sort(key=lambda item: item[2][0].start if item[2] else now)

        vevents: List[str] = []
        for season, my_team_id, my_games, all_games, standings_lines_current in loaded:
            league_name = str(season["league_name"])
            for g in my_games:
                title, my_res, opp_id, opp_name = my_title(my_team_id, team_name, g)
                uid = stable_uid(namespace, g.event_id)

                desc: List[str] = []
                desc.extend(ascii_rule("GAME INFO"))
                desc.append(f"League: {league_name}")
                if g.stage_name:
                    desc.append(f"Stage: {g.stage_name}")
                desc.append(f"Status: {g.status}")
                desc.append(f"Start ({tz_name}): {fmt_start_local(g.start, local_tz)}")
                if g.space.name:
                    desc.append(f"Rink: {g.space.name}")
                if my_res:
                    desc.append(f"Result: {my_res}")

                # Head-to-head (prior matchups vs opponent, this season)
                h2h_lines = head_to_head_lines(
                    all_games=all_games,
                    my_team_id=my_team_id,
                    opponent_id=opp_id,
                    cutoff_start=g.start,
                    tz=local_tz,
                    max_lines=h2h_max,
                )
                if h2h_lines:
                    desc.append("")
                    desc.extend(ascii_rule(f"HEAD-TO-HEAD vs {opp_name}"))
                    desc.extend(h2h_lines)

                # Feature 1: opponent games before this matchup (compact)
                opp_lines = opponent_games_lines_compact(
                    all_games=all_games,
                    opponent_id=opp_id,
                    cutoff_start=g.start,
                    tz=local_tz,
                    max_lines=max_recent,
                )
                if opp_lines:
                    desc.append("")
                    desc.extend(ascii_rule(f"{opp_name.upper()} GAMES-TO-DATE"))
                    desc.extend(opp_lines)

                # Feature 2: standings snapshot (frozen for completed games)
                key = str(g.event_id)
                if standings_lines_current:
                    if freeze_for_game(g, now):
                        if key not in state_events:
                            state_events[key] = {"as_of": run_asof, "lines": standings_lines_current}
                    else:
                        state_events[key] = {"as_of": run_asof, "lines": standings_lines_current}
                snap = state_events.get(key) or {}
                snap_lines = snap.get("lines", [])
                if snap_lines:
                    desc.append("")
                    desc.extend(ascii_rule(f"STANDINGS (as of {snap.get('as_of', run_asof)})"))
                    desc.extend([str(x) for x in snap_lines])

                vevents.append(
                    build_vevent(
                        uid=uid,
                        summary=title,
                        dtstart=g.start,
                        dtend=g.end,
                        description="\n".join(desc),
                        location=(g.space.name or ""),
                        last_modified=now,
                    )
                )

        ics_text = build_ics_calendar(cal_name=cal_name, events=vevents)
        for out_name in [slug] + aliases:
            (output_dir / f"{out_name}.ics").write_text(ics_text, encoding="utf-8")
        save_state(state_path, state)

        all_my_games = [g for _, _, my_games, _, _ in loaded for g in my_games]
        summary[slug] = {
            "seasons": [str(s["league_name"]) for s, *_ in loaded],
            "games": len(all_my_games),
            "first_game_start": fmt_dt_utc_for_ics(min(g.start for g in all_my_games)) if all_my_games else None,
            "last_game_start": fmt_dt_utc_for_ics(max(g.start for g in all_my_games)) if all_my_games else None,
        }
        print(f"{slug}: {len(all_my_games)} games across {len(loaded)} season(s) -> {slug}.ics"
              + (f" (+ aliases: {', '.join(aliases)})" if aliases else ""))

    (state_dir / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print("Done. Calendars updated.")


if __name__ == "__main__":
    main()
