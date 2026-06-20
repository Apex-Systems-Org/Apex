"""
Automated SQLite database backup.
Run via cron: 0 */6 * * * cd /root/Apex/bot && /root/Apex/bot/venv/bin/python backup.py

Keeps last 7 days of backups. Stores in ./backups/ directory.
"""
import shutil
import os
import sys
from datetime import datetime, timedelta

DB_PATH = os.path.join(os.path.dirname(__file__), "apex.db")
BACKUP_DIR = os.path.join(os.path.dirname(__file__), "backups")
KEEP_DAYS = 7

def backup():
    if not os.path.exists(DB_PATH):
        print(f"Database not found: {DB_PATH}")
        sys.exit(1)

    os.makedirs(BACKUP_DIR, exist_ok=True)

    timestamp = datetime.utcnow().strftime("%Y-%m-%d_%H%M")
    backup_name = f"apex_{timestamp}.db"
    backup_path = os.path.join(BACKUP_DIR, backup_name)

    shutil.copy2(DB_PATH, backup_path)

    size_mb = os.path.getsize(backup_path) / (1024 * 1024)
    print(f"Backup created: {backup_name} ({size_mb:.1f} MB)")

    cutoff = datetime.utcnow() - timedelta(days=KEEP_DAYS)
    removed = 0
    for f in os.listdir(BACKUP_DIR):
        if not f.startswith("apex_") or not f.endswith(".db"):
            continue
        fpath = os.path.join(BACKUP_DIR, f)
        mtime = datetime.utcfromtimestamp(os.path.getmtime(fpath))
        if mtime < cutoff:
            os.remove(fpath)
            removed += 1

    if removed:
        print(f"Cleaned up {removed} old backup(s)")

    remaining = len([f for f in os.listdir(BACKUP_DIR) if f.endswith(".db")])
    print(f"Total backups: {remaining}")

if __name__ == "__main__":
    backup()
