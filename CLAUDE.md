# LoseItMCP

MCP server that syncs nutrition/health data from [Lose It!](https://loseit.com) into a local SQLite database.

## Prerequisites

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install pandas mcp requests
```

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `LOSEIT_EMAIL` | Yes | Your Lose It! account email |
| `LOSEIT_PASSWORD` | Yes | Your Lose It! account password |
| `LOSEIT_DB` | No | Path to SQLite database (default: `./loseit.db`) |
| `LOSEIT_BEARER_TOKENS` | No | Comma-separated bearer tokens. When set, all SSE/HTTP requests must include `Authorization: Bearer <token>`. **Strongly recommended for any remote deployment.** |
| `LOSEIT_TIMEZONE` | No | IANA timezone for `current_date` (default: `America/New_York`) |
| `LOSEIT_TRANSPORT` | No | `stdio` (local), `sse`, or `streamable-http` (default: `sse`) |
| `LOSEIT_HOST` | No | Bind host (default: `0.0.0.0`) |
| `LOSEIT_PORT` | No | Bind port (default: `8000`) |

## Running the Server

```bash
source .venv/bin/activate
python src/server.py
```

Or register as a Claude Code MCP tool:

```bash
claude mcp add loseit -- python src/server.py
```

## MCP Tools

| Tool | Description |
|---|---|
| `sync_data` | Log in, download the full export zip, import all CSVs into SQLite |
| `query` | Run a read-only SELECT query against the database |
| `list_tables` | Show all tables and row counts |
| `last_sync` | Show when the last sync occurred |

## Authentication

For local stdio use, no auth is needed. For any remote deployment, set
`LOSEIT_BEARER_TOKENS` to one or more random tokens:

```bash
TOKEN=$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')
LOSEIT_BEARER_TOKENS="$TOKEN" python src/server.py
```

Then register clients with the token as a header:

```bash
claude mcp add --transport http loseit https://your-url/mcp \
  --header "Authorization: Bearer $TOKEN"
```

Without a token, the server logs a warning and runs **open** — anyone with the
URL can read your data and trigger syncs.

## How Sync Works

1. Authenticates via `POST https://api.loseit.com/account/login` (form-encoded, grant_type=password)
2. Downloads full export zip from `GET https://loseit.com/export/data`
3. Parses each CSV in the zip and replaces (DELETE + INSERT) the corresponding SQLite table
4. Dates are normalized from `MM/DD/YYYY` → `YYYY-MM-DD` for proper sorting

Each sync is a full replace — no duplicates are possible.

## Database Schema

### Core Tables

**food_logs** — Individual food entries
`date, name, icon, meal, quantity, units, calories, deleted, fat_g, protein_g, carbs_g, sat_fat_g, sugars_g, fiber_g, cholesterol_mg, sodium_mg`

**daily_calorie_summary** — Daily totals
`date, food_cals, exercise_cals, budget_cals, eer`

**exercise_logs** — Exercise entries
`date, name, icon, type, quantity, units, calories, deleted`

**weights** — Weight measurements
`date, weight, last_updated, deleted`

**custom_foods** — User-created food definitions
`name, unique_id, brand, image, quantity, measure, calories, fat_g, protein_g, carbs_g, sat_fat_g, sugars_g, fiber_g, cholesterol_mg, sodium_mg`

**recipes** — Saved recipes
`name, unique_id, quantity, measure, author, image_name, calories, fat_g, protein_g, carbs_g, sat_fat_g, sugars_g, fiber_g, cholesterol_mg, sodium_mg`

**profile** — User profile key/value pairs
`name, value`

**notes** — Daily notes
`date, title, body`

**achievements** — Earned achievements
`tag, level, earned_on, deleted`

**daily_values** — Daily goal completion flags
`date, name, value`

### Metric Tables (all share: `date, value, secondary_value, last_updated`)

`carbohydrates`, `protein`, `fat`, `fiber`, `sugar`, `sodium_daily`, `cholesterol_daily`, `saturated_fat`, `net_carbohydrates`, `water_intake`, `blood_glucose`, `blood_pressure`, `body_mass_index`, `body_fat`, `sleep`, `steps`

### Utility Tables

**sync_meta** — Tracks last sync time
`key, value`

## Example Queries

```sql
-- Last 7 days of calorie data
SELECT date, food_cals, exercise_cals, budget_cals
FROM daily_calorie_summary
ORDER BY date DESC LIMIT 7;

-- Average daily protein this month
SELECT AVG(value) as avg_protein
FROM protein
WHERE date >= date('now', 'start of month');

-- Top 10 most logged foods
SELECT name, COUNT(*) as times_logged, ROUND(AVG(calories),0) as avg_cals
FROM food_logs WHERE deleted = 0
GROUP BY name ORDER BY times_logged DESC LIMIT 10;

-- Weight trend (last 30 entries)
SELECT date, weight FROM weights
WHERE deleted = 'false' ORDER BY date DESC LIMIT 30;
```

## Coaching Memory — TDEE Estimation

When estimating the user's TDEE, ALWAYS compute all **four** methods and report their **average** as the working number:

1. **Lose It EER** — from the `profile` table (`Current EER`).
2. **Mifflin-St Jeor** — BMR = 10·kg + 6.25·cm − 5·age + 5, then × activity multiplier.
3. **Katch-McArdle** — BMR = 370 + 21.6·LBM(kg), then × activity multiplier. LBM = weight × (1 − body_fat%); pull body fat from the `body_fat` table.
4. **Back-calculated (real-world)** — avg logged `food_cals` + (weight change in lb × 3500 ÷ days) over a multi-week window. This is the ground-truth method.

For the two formula methods, use the activity multiplier that best matches the back-calculated value — currently **~1.55** ("daily / intense 3–4×/week"), i.e. one level BELOW raw activity, because the higher multipliers over-credit very active people (activity is compensated and already priced into maintenance).

Report the **average of the four**. As of 2026-07 (age 57, 5'11", ~30% BF): Lose It 2,972 · Mifflin 2,860 · Katch-McArdle 2,912 · real-data 2,870 → **average ≈ 2,900 cal/day** (working number). Recompute whenever weight, body fat, or activity change materially, and re-anchor the multiplier to the latest back-calculated value. NOTE: short-window (<3 wk) back-calcs read high when scale weight includes water clearing after a blowout — prefer long-window data and treat ~2,900 as the anchor until a clean multi-week deficit confirms otherwise.

## Coaching Memory — Body Composition & Current Status

- **CUT START DATE: 2026-07-06** (not the Lose It profile's 04/01/2026). Apr 1 – Jul 5 was nominally a "cut" but intake ~2,770 vs ~2,900 maintenance = ~130 cal/day deficit, i.e. effectively maintenance; weight was flat (Apr 220.7, May 220.1, Jun 224.1). The real deficit began 2026-07-06. **Measure cut duration, diet fatigue, and diet-break timing from July 6.**
- **Current weight (as of 2026-08-02): ~217.4 lb 7-day avg** (single-day low 215.9); down ~5 lb from the ~222.6 plateau. **Goal 200 lb**, on track ~mid-Oct to early Nov at ~1.3–1.8 lb/wk.
- **Track the 7-day weight average, not single days** — daily weight swings ±2–3 lb with sodium/carbs/alcohol (esp. after weekend/Friday overages). Water clears over 2–3 days; a post-blowout spike is not fat.
- **BIA body fat is unreliable short-term — trust the weight trend over it.** It's hydration-sensitive and can swing 2–3 pts on measurement conditions. Example: 2026-07-26 BF reading jumped 29.7% → 32.5% *while* weight dropped ~5 lb — physiologically impossible (can't lose scale weight and gain fat% on high protein + lifting), i.e. a pure artifact. Use BIA BF only as a slow multi-week trend, never a single reading.

## Coaching Memory — Training Balance (recurring flag)

- **Persistent imbalance: shoulders over-trained, rear delts + quads under-trained.** Across multiple reviews shoulders sit ~14–16 work sets/wk (his #1, already maximized) while rear delts ~3/wk (minimal) and quads ~6–8/wk (maintenance). Standing recommendation: **redirect ~4–5 weekly sets from shoulders → rear delts + quads.** He tends to want *more* shoulder work — steer him to rear delts/face-pulls instead. Volume target: ~10–20 hard sets/muscle/wk, 2×/wk frequency.

## Coaching Memory — Metrics Tracked in Reviews

- **Daily water intake** (`water_intake` table, fluid ounces). User committed 2026-07 to logging one daily total. Target **~100 oz/day**, +20–30 oz on heavy cardio (long hikes/rides) or hot days. Historically logged only sparsely (5 days in 2026) — flag if logging lapses again, and read it as habit adherence, not calorie math (water doesn't affect the deficit). Relevant to fiber efficacy, regularity, cardio recovery, and blood pressure. **As of 2026-08-02: habit established — logging daily, averaging ~95 oz.**
- **Fiber** — target 25–30 g/day. Was chronically ~13 g/day through mid-2026; **as of 2026-08-02 averaging ~33 g/day (target hit)** via ground flax in the morning shake + raspberries with Oikos Greek yogurt. NOTE: ramp fiber slowly — a fast jump (12 → 29 g) combined with MiraLAX caused a bad diarrhea/dehydration episode on 2026-07-23. **Psyllium (Metamucil powder) does not work for him — it bulks and makes it harder to go.** Prefer food fiber (flax, raspberries, veg) or PHGG/Sunfiber; avoid psyllium and osmotic laxatives.
- **Protein** — target **180 g/day** (~1.8 g/kg; both total-weight and lean-mass methods agree at ~30% BF). Consistently exceeding since 2026-07-18, averaging ~200–210 g.
- **Weekly deficit trend** (report this as a 4-week table): W27 411 → W28 479 → W29 513 → W30 651 cal/day. Steady improvement; ~1.3 lb/wk as of W30.
