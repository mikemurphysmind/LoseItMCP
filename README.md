# LoseItMCP

An [MCP](https://modelcontextprotocol.io) server that pulls your nutrition, fitness, and body metrics from [Lose It!](https://loseit.com) into a local SQLite database — so you (or any LLM) can query years of food logs, weight, macros, exercise, sleep, and more with plain SQL.

## Features

- **Full-history sync** — downloads your complete Lose It! data export (food logs, exercise, custom foods, recipes, weights, BMI, body fat, blood pressure, blood glucose, sleep, steps, water, achievements, plus daily totals for protein, carbs, fat, fiber, sugar, sodium, cholesterol)
- **Idempotent** — re-running `sync_data` replaces tables in place; no duplicates
- **Timezone-aware** — date filters use your local timezone, not the server's
- **Multiple transports** — `stdio` for local Claude Desktop / Code, `sse` and `streamable-http` for remote clients (Claude on iOS, OpenAI apps, etc.)
- **Read-only SQL** — exposes a `query` tool that runs SELECT statements only

## Quick Start

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install pandas mcp requests

export LOSEIT_EMAIL="you@example.com"
export LOSEIT_PASSWORD="your-password"

python src/server.py
```

To register with Claude Code locally:

```bash
claude mcp add loseit -- python src/server.py
```

For remote access, run with `LOSEIT_TRANSPORT=streamable-http` (or `sse`) and expose port 8000 via your tunnel of choice (Cloudflare Tunnel, ngrok, Codespaces port forwarding, etc.), then:

```bash
claude mcp add --transport http loseit https://your-public-url/mcp
```

## MCP Tools

| Tool | Description |
|---|---|
| `sync_data` | Log in to Lose It!, download the export zip, and import all CSVs into SQLite |
| `query` | Run a read-only SELECT query against the database |
| `list_tables` | Show all tables and row counts |
| `last_sync` | Show when data was last synced |
| `current_date` | Return today/yesterday in the user's local timezone (use these for "today" / "yesterday" queries) |

## Configuration

| Variable | Default | Description |
|---|---|---|
| `LOSEIT_EMAIL` | (required) | Your Lose It! account email |
| `LOSEIT_PASSWORD` | (required) | Your Lose It! account password |
| `LOSEIT_DB` | `./loseit.db` | SQLite database path |
| `LOSEIT_TRANSPORT` | `sse` | Transport: `stdio`, `sse`, or `streamable-http` |
| `LOSEIT_HOST` | `0.0.0.0` | Bind host (for SSE/HTTP) |
| `LOSEIT_PORT` | `8000` | Bind port (for SSE/HTTP) |
| `LOSEIT_TIMEZONE` | `America/New_York` | IANA timezone for `current_date` |

## How Sync Works

1. POST credentials to `https://api.loseit.com/account/login` (form-encoded, `grant_type=password`)
2. GET `https://loseit.com/export/data` — returns a zip with ~30 CSVs
3. Each CSV is parsed and replaces (DELETE + INSERT) its corresponding SQLite table
4. Dates are normalized from `MM/DD/YYYY` → `YYYY-MM-DD`

See [CLAUDE.md](./CLAUDE.md) for the full database schema and example queries.

## Example Queries

```sql
-- Last 7 days of calorie data
SELECT date, food_cals, exercise_cals, budget_cals
FROM daily_calorie_summary ORDER BY date DESC LIMIT 7;

-- Average daily protein this month
SELECT AVG(value) FROM protein
WHERE date >= date('now', 'start of month');

-- Top 10 most logged foods
SELECT name, COUNT(*) as times_logged, ROUND(AVG(calories)) as avg_cals
FROM food_logs WHERE deleted = 0
GROUP BY name ORDER BY times_logged DESC LIMIT 10;
```

## Security Notes

- Credentials are read from environment variables only, never written to disk
- The `query` tool only allows SELECT (no writes, no DDL)
- For remote deployments, set `LOSEIT_BEARER_TOKENS` (comma-separated) — every SSE / streamable-HTTP request must then include `Authorization: Bearer <token>` or it gets 401. Generate one with `python3 -c 'import secrets; print(secrets.token_urlsafe(32))'`
- When `LOSEIT_BEARER_TOKENS` is unset the server logs a warning and runs **open** — only safe for local stdio or a trusted local network
- Register clients with the token: `claude mcp add --transport http loseit https://your-url/mcp --header "Authorization: Bearer $TOKEN"`
