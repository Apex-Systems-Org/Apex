# Contributing to Apex

Thanks for your interest in contributing to Apex!

## Getting Started

1. Fork the repository
2. Clone your fork: `git clone https://github.com/your-username/Apex.git`
3. Create a branch: `git checkout -b feature/your-feature`
4. Make your changes
5. Push and open a Pull Request

## Setup

```bash
cd bot
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Fill in your bot token in .env
python bot.py
```

## Guidelines

- Keep PRs focused on a single change
- Test your changes before submitting
- Follow the existing code style
- Don't commit `.env`, `apex.db`, or `__pycache__/`
- Add your command to the help categories in `cogs/utility.py` if applicable

## Project Structure

```
bot/
├── bot.py              # Entry point, event loop, background tasks
├── api.py              # Flask API for the web dashboard
├── config.py           # Environment config loader
├── database.py         # Database backend switcher
├── database_sqlite.py  # SQLite implementation
├── cogs/               # Command modules
│   ├── moderation.py   # Ban, kick, warn, timeout, purge, etc.
│   ├── tickets.py      # Ticket panel system
│   ├── utility.py      # Info commands, help, polls, reminders
│   ├── events.py       # Message events, auto-mod, AFK, custom commands
│   ├── logging.py      # Audit logging for all server events
│   ├── leveling.py     # XP and level system
│   ├── voice.py        # Join-to-create voice channels
│   └── ...             # Other feature cogs
└── helpers/            # Shared utilities
    ├── embeds.py       # Branded embed builders
    ├── utils.py        # Common functions
    ├── cache.py        # In-memory cache
    ├── flags.py        # Command flag parser
    └── components.py   # Discord Components V2 helpers
```

## Adding a New Cog

1. Create `cogs/your_feature.py`
2. Add a class extending `commands.Cog`
3. Add the `async def setup(bot)` function at the bottom
4. Register it in `bot.py` in the `EXTENSIONS` list
5. Add commands to the help menu in `cogs/utility.py`

## Questions?

Open an issue or join our [Discord server](https://discord.gg/xZsfgNHnnE).
