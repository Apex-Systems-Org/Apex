# Apex

A Discord moderation bot built with discord.py.

**[Website](https://apexconsole.net)** · **[Discord](https://discord.gg/xZsfgNHnnE)** · **[Invite Bot](https://discord.com/oauth2/authorize?client_id=1461965918228713472&permissions=8&scope=bot+applications.commands)**

## Features

**Moderation** — ban, kick, warn, timeout, mute, purge, slowmode, lock/unlock, case system with mod logs, auto-mod (spam, links, invites, word filter, raid protection), warning escalation

**Tickets** — multi-panel system with buttons/dropdown, custom ticket types, modal forms, transcripts, claim/transfer, priority levels, snippets, Components V2 support

**Utility** — userinfo, serverinfo, avatar, roleinfo, roles list, editrole, members with exclusions, polls, reminders, custom commands, suggestions, AFK system

**Leveling** — XP per message, level roles, leaderboard, configurable cooldowns and XP ranges

**Voice** — join-to-create channels, owner controls (lock, rename, limit, kick, transfer)

**Other** — reaction roles, giveaways, invite tracking, starboard, modmail, LOA system, sticky messages, role persistence, auto-responders

## Setup

```bash
git clone https://github.com/Apex-Systems-Org/Apex.git
cd Apex/bot
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Edit `.env` with your bot token, then:

```bash
python bot.py
```

### With PM2

```bash
pm2 start venv/bin/python --name apex -- bot.py
```

## Configuration

All settings are configurable per-server through commands or the web dashboard at [apexconsole.net](https://apexconsole.net).

```
a!config prefix !
a!config mod_role @Moderator
a!config admin_role @Admin
a!plugin enable tickets
a!plugin enable leveling
```

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

[MIT](LICENSE)
