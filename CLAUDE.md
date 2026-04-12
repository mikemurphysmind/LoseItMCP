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
