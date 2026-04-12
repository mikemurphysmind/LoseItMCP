"""LoseIt MCP Server — syncs Lose It! nutrition data into SQLite and exposes query tools."""

import io
import os
import csv
import sqlite3
import zipfile
import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests
from mcp.server.fastmcp import FastMCP

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
DB_PATH = os.environ.get("LOSEIT_DB", os.path.join(os.path.dirname(__file__), "..", "loseit.db"))
LOGIN_URL = "https://api.loseit.com/account/login"
EXPORT_URL = "https://loseit.com/export/data"
HOST = os.environ.get("LOSEIT_HOST", "0.0.0.0")
PORT = int(os.environ.get("LOSEIT_PORT", "8000"))
TRANSPORT = os.environ.get("LOSEIT_TRANSPORT", "sse")  # "stdio" for local, "sse" for remote
TIMEZONE = os.environ.get("LOSEIT_TIMEZONE", "America/New_York")
TZ = ZoneInfo(TIMEZONE)


def _local_today() -> str:
    return datetime.now(TZ).strftime("%Y-%m-%d")


def _local_date_offset(days: int) -> str:
    return (datetime.now(TZ) + timedelta(days=days)).strftime("%Y-%m-%d")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("loseit-mcp")

mcp = FastMCP("loseit", host=HOST, port=PORT)

# ---------------------------------------------------------------------------
# CSV → SQLite table definitions
#
# Each entry maps a CSV filename (inside the zip) to:
#   table  – SQLite table name
#   cols   – ordered list of (csv_header, sql_col, sql_type)
#
# We import every CSV that ships in the export zip.  Photos and nested zips
# are skipped.
# ---------------------------------------------------------------------------

TABLE_DEFS: dict[str, dict] = {
    "food-logs.csv": {
        "table": "food_logs",
        "cols": [
            ("Date", "date", "TEXT"),
            ("Name", "name", "TEXT"),
            ("Icon", "icon", "TEXT"),
            ("Meal", "meal", "TEXT"),
            ("Quantity", "quantity", "REAL"),
            ("Units", "units", "TEXT"),
            ("Calories", "calories", "REAL"),
            ("Deleted", "deleted", "INTEGER"),
            ("Fat (g)", "fat_g", "REAL"),
            ("Protein (g)", "protein_g", "REAL"),
            ("Carbohydrates (g)", "carbs_g", "REAL"),
            ("Saturated Fat (g)", "sat_fat_g", "REAL"),
            ("Sugars (g)", "sugars_g", "REAL"),
            ("Fiber (g)", "fiber_g", "REAL"),
            ("Cholesterol (mg)", "cholesterol_mg", "REAL"),
            ("Sodium (mg)", "sodium_mg", "REAL"),
        ],
    },
    "daily-calorie-summary.csv": {
        "table": "daily_calorie_summary",
        "cols": [
            ("Date", "date", "TEXT"),
            ("Food cals", "food_cals", "REAL"),
            ("Exercise cals", "exercise_cals", "REAL"),
            ("Budget cals", "budget_cals", "REAL"),
            ("EER", "eer", "REAL"),
        ],
    },
    "weights.csv": {
        "table": "weights",
        "cols": [
            ("Date", "date", "TEXT"),
            ("Weight", "weight", "REAL"),
            ("Last Updated", "last_updated", "TEXT"),
            ("Deleted", "deleted", "TEXT"),
        ],
    },
    "exercise-logs.csv": {
        "table": "exercise_logs",
        "cols": [
            ("Date", "date", "TEXT"),
            ("Name", "name", "TEXT"),
            ("Icon", "icon", "TEXT"),
            ("Type", "type", "TEXT"),
            ("Quantity", "quantity", "REAL"),
            ("Units", "units", "TEXT"),
            ("Calories", "calories", "REAL"),
            ("Deleted", "deleted", "INTEGER"),
        ],
    },
    "custom-foods.csv": {
        "table": "custom_foods",
        "cols": [
            ("Name", "name", "TEXT"),
            ("UniqueId", "unique_id", "TEXT"),
            ("Brand", "brand", "TEXT"),
            ("Image", "image", "TEXT"),
            ("Quantity", "quantity", "REAL"),
            ("Measure", "measure", "TEXT"),
            ("Calories", "calories", "REAL"),
            ("Fat (g)", "fat_g", "REAL"),
            ("Protein (g)", "protein_g", "REAL"),
            ("Carbohydrates (g)", "carbs_g", "REAL"),
            ("Saturated Fat (g)", "sat_fat_g", "REAL"),
            ("Sugars (g)", "sugars_g", "REAL"),
            ("Fiber (g)", "fiber_g", "REAL"),
            ("Cholesterol (mg)", "cholesterol_mg", "REAL"),
            ("Sodium (mg)", "sodium_mg", "REAL"),
        ],
    },
    "recipes.csv": {
        "table": "recipes",
        "cols": [
            ("Name", "name", "TEXT"),
            ("UniqueId", "unique_id", "TEXT"),
            ("Quantity", "quantity", "REAL"),
            ("Measure", "measure", "TEXT"),
            ("Author", "author", "TEXT"),
            ("Image Name", "image_name", "TEXT"),
            ("Calories", "calories", "REAL"),
            ("Fat (g)", "fat_g", "REAL"),
            ("Protein (g)", "protein_g", "REAL"),
            ("Carbohydrates (g)", "carbs_g", "REAL"),
            ("Saturated Fat (g)", "sat_fat_g", "REAL"),
            ("Sugars (g)", "sugars_g", "REAL"),
            ("Fiber (g)", "fiber_g", "REAL"),
            ("Cholesterol (mg)", "cholesterol_mg", "REAL"),
            ("Sodium (mg)", "sodium_mg", "REAL"),
        ],
    },
    "profile.csv": {
        "table": "profile",
        "cols": [
            ("Name", "name", "TEXT"),
            ("Value", "value", "TEXT"),
        ],
    },
    "notes.csv": {
        "table": "notes",
        "cols": [
            ("Date", "date", "TEXT"),
            ("Title", "title", "TEXT"),
            ("Body", "body", "TEXT"),
        ],
    },
    "achievements.csv": {
        "table": "achievements",
        "cols": [
            ("Tag", "tag", "TEXT"),
            ("Level", "level", "INTEGER"),
            ("Earned On", "earned_on", "TEXT"),
            ("Deleted", "deleted", "TEXT"),
        ],
    },
    "daily-values.csv": {
        "table": "daily_values",
        "cols": [
            ("Date", "date", "TEXT"),
            ("Name", "name", "TEXT"),
            ("Value", "value", "REAL"),
        ],
    },
}

# Metric CSVs that all share (Date, Value, Secondary Value, Last Updated)
_METRIC_CSVS = {
    "carbohydrates.csv": "carbohydrates",
    "protein.csv": "protein",
    "fat.csv": "fat",
    "fiber.csv": "fiber",
    "sugar.csv": "sugar",
    "sodium.csv": "sodium_daily",
    "cholesterol.csv": "cholesterol_daily",
    "saturated-fat.csv": "saturated_fat",
    "net-carbohydrates.csv": "net_carbohydrates",
    "water-intake.csv": "water_intake",
    "blood-glucose.csv": "blood_glucose",
    "blood-pressure.csv": "blood_pressure",
    "body-mass-index.csv": "body_mass_index",
    "body-fat.csv": "body_fat",
    "sleep.csv": "sleep",
    "steps.csv": "steps",
}

_METRIC_COLS = [
    ("Date", "date", "TEXT"),
    ("Value", "value", "REAL"),
    ("Secondary Value", "secondary_value", "REAL"),
    ("Last Updated", "last_updated", "TEXT"),
]

for csv_name, tbl_name in _METRIC_CSVS.items():
    TABLE_DEFS[csv_name] = {"table": tbl_name, "cols": list(_METRIC_COLS)}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _normalize_date(val: str) -> str:
    """Convert MM/DD/YYYY → YYYY-MM-DD so dates sort/compare correctly."""
    if not val:
        return val
    for fmt in ("%m/%d/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(val.split("T")[0], fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return val


def _safe_real(val: str) -> float | None:
    if not val or val.strip().lower() in ("", "n/a"):
        return None
    try:
        return float(val)
    except ValueError:
        return None


def _get_db() -> sqlite3.Connection:
    db = sqlite3.connect(DB_PATH)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA foreign_keys=ON")
    return db


def _ensure_tables(db: sqlite3.Connection) -> None:
    for defn in TABLE_DEFS.values():
        col_defs = ", ".join(f"{c[1]} {c[2]}" for c in defn["cols"])
        db.execute(f"CREATE TABLE IF NOT EXISTS {defn['table']} ({col_defs})")
    db.execute(
        "CREATE TABLE IF NOT EXISTS sync_meta (key TEXT PRIMARY KEY, value TEXT)"
    )
    db.commit()


def _login(email: str, password: str) -> requests.Session:
    session = requests.Session()
    session.headers.update({
        "User-Agent": "LoseItMCP/1.0",
        "Accept": "application/json",
    })
    r = session.post(
        LOGIN_URL,
        data={"username": email, "password": password, "grant_type": "password"},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    r.raise_for_status()
    body = r.json()
    if "user_id" not in body:
        raise RuntimeError(f"Login failed: {body}")
    log.info("Logged in as user %s", body["user_id"])
    return session


def _download_export(session: requests.Session) -> zipfile.ZipFile:
    r = session.get(EXPORT_URL, allow_redirects=True)
    r.raise_for_status()
    ct = r.headers.get("Content-Type", "")
    if "zip" not in ct and "octet" not in ct:
        raise RuntimeError(f"Expected zip, got Content-Type={ct}")
    return zipfile.ZipFile(io.BytesIO(r.content))


def _import_csv(db: sqlite3.Connection, zf: zipfile.ZipFile, csv_name: str, defn: dict) -> int:
    """Import a single CSV from the zip into the corresponding table.

    Uses DELETE + INSERT (full replace) to guarantee idempotent imports
    without duplicates.
    """
    try:
        raw = zf.open(csv_name).read().decode("utf-8")
    except KeyError:
        log.warning("CSV %s not found in zip — skipping", csv_name)
        return 0

    reader = csv.DictReader(io.StringIO(raw))
    table = defn["table"]
    cols = defn["cols"]
    sql_cols = [c[1] for c in cols]
    placeholders = ", ".join("?" for _ in sql_cols)
    insert_sql = f"INSERT INTO {table} ({', '.join(sql_cols)}) VALUES ({placeholders})"

    # Full replace: delete then insert
    db.execute(f"DELETE FROM {table}")

    rows: list[tuple] = []
    for row in reader:
        values: list = []
        for csv_hdr, sql_col, sql_type in cols:
            val = row.get(csv_hdr, "")
            if sql_col == "date":
                val = _normalize_date(val)
            elif sql_type == "REAL":
                val = _safe_real(val)
            elif sql_type == "INTEGER":
                try:
                    val = int(float(val)) if val else None
                except (ValueError, TypeError):
                    val = None
            values.append(val)
        rows.append(tuple(values))

    if rows:
        db.executemany(insert_sql, rows)
    return len(rows)


# ---------------------------------------------------------------------------
# MCP Tools
# ---------------------------------------------------------------------------

@mcp.tool()
def sync_data() -> str:
    """Log in to Lose It!, download the full export zip, and import all CSVs into SQLite.

    Requires LOSEIT_EMAIL and LOSEIT_PASSWORD environment variables.
    Returns a summary of rows imported per table.
    """
    email = os.environ.get("LOSEIT_EMAIL")
    password = os.environ.get("LOSEIT_PASSWORD")
    if not email or not password:
        return "Error: set LOSEIT_EMAIL and LOSEIT_PASSWORD environment variables."

    session = _login(email, password)
    zf = _download_export(session)
    db = _get_db()
    _ensure_tables(db)

    summary: list[str] = []
    total = 0
    for csv_name, defn in TABLE_DEFS.items():
        count = _import_csv(db, zf, csv_name, defn)
        if count:
            summary.append(f"  {defn['table']}: {count:,} rows")
            total += count

    db.execute(
        "INSERT OR REPLACE INTO sync_meta (key, value) VALUES ('last_sync', ?)",
        (datetime.utcnow().isoformat(),),
    )
    db.commit()
    db.close()
    zf.close()

    return f"Sync complete — {total:,} rows across {len(summary)} tables:\n" + "\n".join(summary)


@mcp.tool()
def query(sql: str) -> str:
    """Run a read-only SQL query against the Lose It! SQLite database.

    Only SELECT statements are allowed.  Returns results as a formatted table
    (max 100 rows).
    """
    stripped = sql.strip().rstrip(";")
    if not stripped.upper().startswith("SELECT"):
        return "Error: only SELECT queries are allowed."

    db = _get_db()
    _ensure_tables(db)
    try:
        cur = db.execute(stripped)
        columns = [desc[0] for desc in cur.description]
        rows = cur.fetchmany(100)
    except sqlite3.Error as e:
        return f"SQL error: {e}"
    finally:
        db.close()

    if not rows:
        return "(no results)"

    # Format as aligned text table
    widths = [len(c) for c in columns]
    for row in rows:
        for i, val in enumerate(row):
            widths[i] = max(widths[i], len(str(val) if val is not None else "NULL"))

    header = " | ".join(c.ljust(widths[i]) for i, c in enumerate(columns))
    sep = "-+-".join("-" * w for w in widths)
    lines = [header, sep]
    for row in rows:
        lines.append(
            " | ".join(
                (str(v) if v is not None else "NULL").ljust(widths[i])
                for i, v in enumerate(row)
            )
        )
    return "\n".join(lines)


@mcp.tool()
def list_tables() -> str:
    """List all tables and their row counts in the Lose It! database."""
    db = _get_db()
    _ensure_tables(db)
    tables = db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()
    lines = []
    for (name,) in tables:
        count = db.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
        lines.append(f"  {name}: {count:,} rows")
    db.close()
    return "\n".join(lines) if lines else "(no tables)"


@mcp.tool()
def current_date() -> str:
    """Return today's date and yesterday's date in the user's local timezone.

    Lose It! stores all dates in the user's local timezone, so callers should
    use these values (not UTC) when filtering food_logs / daily_calorie_summary
    / etc. by 'today' or 'yesterday'.
    """
    today = _local_today()
    yesterday = _local_date_offset(-1)
    now = datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S %Z")
    return f"timezone: {TIMEZONE}\nnow: {now}\ntoday: {today}\nyesterday: {yesterday}"


@mcp.tool()
def last_sync() -> str:
    """Show when the data was last synced from Lose It!."""
    db = _get_db()
    _ensure_tables(db)
    row = db.execute(
        "SELECT value FROM sync_meta WHERE key = 'last_sync'"
    ).fetchone()
    db.close()
    if row:
        return f"Last sync: {row[0]}"
    return "Data has not been synced yet. Run sync_data first."


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    log.info("Starting LoseIt MCP server on %s:%d (transport=%s)", HOST, PORT, TRANSPORT)
    mcp.run(transport=TRANSPORT)
