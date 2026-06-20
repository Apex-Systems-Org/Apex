"""
Migration script: SQLite -> CockroachDB
Reads all data from apex.db and inserts into CockroachDB.

Usage:
    python migrate_to_crdb.py

Requires COCKROACH_DSN in .env
"""
import sqlite3
import json
import os
import sys
from dotenv import load_dotenv

load_dotenv()

DSN = os.getenv("COCKROACH_DSN")
if not DSN:
    print("ERROR: Set COCKROACH_DSN in .env")
    sys.exit(1)

try:
    import psycopg2
    import psycopg2.extras
except ImportError:
    print("ERROR: pip install psycopg2-binary")
    sys.exit(1)

DB_PATH = os.path.join(os.path.dirname(__file__), "apex.db")

TABLES = [
    "guilds",
    "mod_logs",
    "warnings",
    "ticket_panels",
    "tickets",
    "transcripts",
    "bot_guilds",
    "custom_commands",
    "reaction_role_panels",
    "blacklist",
    "knowledge_base",
    "bot_status",
    "error_logs",
    "support_templates",
    "user_notes",
    "audit_logs",
    "server_health",
    "suggestions",
    "auto_responders",
    "giveaways",
    "giveaway_entries",
    "afk",
    "economy",
    "invite_tracking",
    "invite_cache",
    "leveling",
    "level_roles",
    "voice_generators",
    "temp_voice_channels",
    "dev_auth_codes",
    "incidents",
]


def migrate():
    print(f"Connecting to SQLite: {DB_PATH}")
    sqlite_conn = sqlite3.connect(DB_PATH)
    sqlite_conn.row_factory = sqlite3.Row

    print(f"Connecting to CockroachDB...")
    pg_conn = psycopg2.connect(DSN)
    pg_conn.autocommit = False
    pg_cur = pg_conn.cursor()

    # First, initialize schema by importing the crdb module
    print("Initializing CockroachDB schema...")
    from database_crdb import Database as CRDBDatabase
    crdb = CRDBDatabase()
    print("Schema created.")

    total_rows = 0

    for table in TABLES:
        try:
            sqlite_cur = sqlite_conn.cursor()
            sqlite_cur.execute(f"SELECT * FROM {table}")
            rows = sqlite_cur.fetchall()

            if not rows:
                print(f"  {table}: 0 rows (skipping)")
                continue

            columns = [desc[0] for desc in sqlite_cur.description]

            # Get columns that exist in the CockroachDB table
            pg_cur.execute(f"SELECT column_name FROM information_schema.columns WHERE table_name = %s", (table,))
            crdb_columns = {r[0] for r in pg_cur.fetchall()}

            # Filter to only columns that exist in both
            valid_columns = [c for c in columns if c in crdb_columns]
            if not valid_columns:
                print(f"  {table}: no matching columns (skipping)")
                continue

            # Quote reserved words
            RESERVED = {"left", "right", "order", "group", "user", "default", "check", "index", "key"}
            quoted_columns = [f'"{c}"' if c.lower() in RESERVED else c for c in valid_columns]

            placeholders = ", ".join(["%s"] * len(valid_columns))
            col_names = ", ".join(quoted_columns)

            insert_sql = f"INSERT INTO {table} ({col_names}) VALUES ({placeholders}) ON CONFLICT DO NOTHING"

            batch = []
            for row in rows:
                values = tuple(row[col] for col in valid_columns)
                batch.append(values)

            psycopg2.extras.execute_batch(pg_cur, insert_sql, batch, page_size=100)
            pg_conn.commit()

            print(f"  {table}: {len(rows)} rows migrated")
            total_rows += len(rows)

        except sqlite3.OperationalError as e:
            print(f"  {table}: SQLite error - {e}")
        except psycopg2.Error as e:
            pg_conn.rollback()
            print(f"  {table}: CockroachDB error - {e}")

    sqlite_conn.close()
    pg_conn.close()

    print(f"\nMigration complete! {total_rows} total rows migrated.")


if __name__ == "__main__":
    migrate()
