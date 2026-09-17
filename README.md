# Hockey Schedule → Google Calendar (.ics)

This project turns a Bond Sports hockey league schedule into an auto-updating
`.ics` calendar feed you can subscribe to in Google Calendar (or any calendar app).

Once subscribed, the calendar stays up to date with:
- Upcoming games (and reschedules)
- Final scores (W / L / T) in the event title
- Correct home/away formatting
- Rink locations
- Head-to-head history, the opponent's results so far, and a standings snapshot in each event

All using free GitHub tooling (GitHub Actions + GitHub Pages).

---

## Feed

| Team | Feed URL |
| --- | --- |
| Alligator Skinners | `https://hmgoalie35.github.io/hockey-ics/alligator-skinners.ics` |

The feed is **season-agnostic**: every season the team plays is merged into the
same file, so you subscribe once and never need to change the URL. The previous
per-season URL (`alligator-skinners-winter-2026-d3.ics`) is still published as
a copy of the same feed so older subscriptions keep working.

Google Calendar → Other calendars → `+` → From URL → paste the feed URL.

---

## How It Works

1. A GitHub Action runs every 6 hours (`.github/workflows/build_ics.yml`)
2. `src/generate_ics.py` lists every season of the configured Bond Sports
   program, loads each season's competition and stages, and keeps the ones
   where a team with the configured name plays (new seasons are picked up
   automatically; results are cached in `docs/_state/<slug>.json`)
3. For each season it fetches `game-scores` and `standings`, finds the team's
   numeric ID by name, and filters the games to that team
4. One `.ics` file per team is written to `docs/` and committed
5. GitHub Pages serves `docs/`, and Google Calendar refreshes on its own
6. If the newest game in a feed is more than three weeks old and nothing newer
   was found, the workflow opens a GitHub issue so the feed never goes stale silently

---

## Configuration (`config.yaml`)

```yaml
output_dir: "docs"
default_timezone: "America/New_York"

teams:
  - name: "Alligator Skinners"        # team name exactly as Bond Sports shows it
    slug: "alligator-skinners"        # -> docs/alligator-skinners.ics
    aliases:                          # optional extra copies of the feed (legacy URLs)
      - "alligator-skinners-winter-2026-d3"
    program_id: 12070                 # Bond Sports program -> seasons are auto-discovered
    seasons:                          # optional explicit seasons, always included
      - league_name: "Winter 2026 Division 3"
        competition_id: "180251ce-9fbc-4153-b7f6-ce3530a2c7f9"
        stage_id: 153
```

### New seasons

Nothing to do. `program_id` is the number in the league's Bond Sports URL
(`bondsports.co/activity/programs/adult-hockey/12070/season/...`), and every
season of that program is checked on each run. If the team ever plays under a
different program, change `program_id` or add the season under `seasons:`
(competition UUID + stage ID from the season's `/competition` page). The team
ID is resolved by matching `name` against the schedule; set `team_id:` on a
season only if the name differs that season.

Optional per-team keys: `team_names` (alternate spellings to match),
`calendar_name`, `opponent_recent_max`, `head_to_head_max`.

---

## Repository Structure

```
.
├── README.md
├── config.yaml
├── src/
│   └── generate_ics.py
├── docs/
│   ├── <slug>.ics            # published feeds
│   └── _state/
│       ├── <slug>.json       # frozen standings snapshots for completed games
│       └── summary.json      # what each feed currently contains
└── .github/
    └── workflows/
        └── build_ics.yml
```

---

## GitHub Pages Setup

1. Repo → Settings → Pages
2. Source: Deploy from a branch
3. Branch: `main`, folder: `/docs`

Feeds are served at `https://<github-username>.github.io/<repo-name>/<slug>.ics`.

> On a forked repository GitHub disables workflows until you open the
> **Actions** tab and enable them; the feed only updates once that's done.

---

## License

MIT
