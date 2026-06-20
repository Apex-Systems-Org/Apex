import os
import sys
from dotenv import load_dotenv

# Support loading a different env file with --env flag
env_file = ".env"
for i, arg in enumerate(sys.argv):
    if arg == "--env" and i + 1 < len(sys.argv):
        env_file = f".env.{sys.argv[i + 1]}"
        break

load_dotenv(env_file)

class Config:
    ENVIRONMENT = os.getenv("ENVIRONMENT", "development")
    IS_DEV = ENVIRONMENT == "development"
    DISCORD_TOKEN = os.getenv("DEV_DISCORD_TOKEN") if IS_DEV and os.getenv("DEV_DISCORD_TOKEN") else os.getenv("DISCORD_TOKEN")
    DASHBOARD_URL = os.getenv("DASHBOARD_URL", "http://localhost:3000")
    DB_BACKEND = os.getenv("DB_BACKEND", "sqlite")
    COCKROACH_DSN = os.getenv("COCKROACH_DSN")
    BOT_API_PORT = int(os.getenv("BOT_API_PORT", "5050"))

config = Config()
