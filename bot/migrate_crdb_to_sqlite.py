"""
Migrate all data from CockroachDB to SQLite.
Run on VPS: python migrate_crdb_to_sqlite.py
"""
import os
import sys
import sqlite3
import psycopg2
import psycopg2.extras

COCKROACH_DSN = os.environ.get("COCKROACH_DSN")
SQLITE_PATH = os.path.join(os.path.dirname(__file__), "apex.db")

if not COCKROACH_DSN:
    print("Set COCKROACH_DSN env var first")
    sys.exit(1)

TABLES = [
    "guilds", "mod_logs", "warnings", "ticket_panels", "tickets",
    "transcripts", "bot_guilds", "custom_commands", "reaction_role_panels",
    "blacklist", "knowledge_base", "bot_status", "error_logs",
    "support_templates", "user_notes", "audit_logs", "server_health",
    "suggestions", "auto_responders", "giveaways", "giveaway_entries",
    "afk", "economy", "invite_tracking", "invite_cache", "leveling",
    "level_roles", "voice_generators", "temp_voice_channels",
    "dev_auth_codes", "incidents", "tempbans", "starboard_posts",
    "ticket_feedback", "loa_requests", "snippets", "ticket_bans",
]


def migrate():
    print(f"Connecting to CockroachDB...")
    crdb = psycopg2.connect(COCKROACH_DSN)
    crdb_cur = crdb.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    # Init SQLite with existing schema
    print(f"Initializing SQLite at {SQLITE_PATH}...")
    from database_sqlite import Database
    sqlite_db = Database()

    sq = sqlite3.connect(SQLITE_PATH)
    sq.row_factory = sqlite3.Row
    sq_cur = sq.cursor()

    total = 0
    for table in TABLES:
        try:
            crdb_cur.execute(f"SELECT * FROM {table}")
            rows = crdb_cur.fetchall()
        except Exception as e:
            print(f"  {table}: skip ({e})")
            crdb.rollback()
            continue

        if not rows:
            print(f"  {table}: 0 rows")
            continue

        cols = list(rows[0].keys())
        placeholders = ",".join(["?" for _ in cols])
        col_names = ",".join(cols)

        # Clear existing SQLite data for this table
        try:
            sq_cur.execute(f"DELETE FROM {table}")
        except:
            print(f"  {table}: table doesn't exist in SQLite, skipping")
            continue

        count = 0
        for row in rows:
            values = []
            for c in cols:
                v = row[c]
                if isinstance(v, (dict, list)):
                    import json
                    v = json.dumps(v)
                values.append(v)
            try:
                sq_cur.execute(f"INSERT OR IGNORE INTO {table} ({col_names}) VALUES ({placeholders})", values)
                count += 1
            except Exception as e:
                print(f"  {table}: row error: {e}")

        sq.commit()
        print(f"  {table}: {count} rows migrated")
        total += count

    sq.close()
    crdb.close()
    print(f"\nDone. {total} total rows migrated to {SQLITE_PATH}")


if __name__ == "__main__":
    migrate()
