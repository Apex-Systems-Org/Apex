import discord
from datetime import datetime, timezone

# Consistent brand colors
BRAND_COLOR = discord.Color.from_str("#1a6aff")

COLORS = {
    "success": discord.Color.from_str("#57F287"),
    "error": discord.Color.from_str("#ED4245"),
    "warning": discord.Color.from_str("#FEE75C"),
    "info": BRAND_COLOR,
    "mod": discord.Color.from_str("#EB459E"),
}

FOOTER_TEXT = "Apex"


def _base(description: str, color: discord.Color, title: str = None) -> discord.Embed:
    embed = discord.Embed(description=description, color=color, timestamp=datetime.now(timezone.utc))
    if title:
        embed.title = title
    embed.set_footer(text=FOOTER_TEXT)
    return embed


def success(description: str, title: str = None) -> discord.Embed:
    return _base(description, COLORS["success"], title)


def error(description: str, title: str = None) -> discord.Embed:
    return _base(description, COLORS["error"], title)


def warning(description: str, title: str = None) -> discord.Embed:
    return _base(description, COLORS["warning"], title)


def info(description: str, title: str = None) -> discord.Embed:
    return _base(description, COLORS["info"], title)


def mod_embed(title: str, color: discord.Color = None) -> discord.Embed:
    """Create a branded moderation embed for mod log entries."""
    embed = discord.Embed(title=title, color=color or COLORS["mod"], timestamp=datetime.now(timezone.utc))
    embed.set_footer(text=FOOTER_TEXT)
    return embed
