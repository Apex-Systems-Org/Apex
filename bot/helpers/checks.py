import discord
from database import db
from helpers.cache import settings_cache

MAIN_SERVER_ID = 1459426097283334147
SUPPORT_ROLE_ID = 1459460092968570991
DEV_ROLE_ID = 1459459647906779147


def _get_settings(guild_id) -> dict:
    key = str(guild_id)
    cached = settings_cache.get(key)
    if cached is not None:
        return cached
    settings = db.get_guild_settings(guild_id)
    settings_cache.set(key, settings)
    return settings


def has_mod_role(member: discord.Member) -> bool:
    """Check if member has a configured mod role or Discord mod permissions."""
    if (
        member.guild_permissions.kick_members
        or member.guild_permissions.ban_members
        or member.guild_permissions.administrator
    ):
        return True
    settings = _get_settings(member.guild.id)
    mod_roles = settings.get("mod_roles", [])
    return any(str(role.id) in mod_roles for role in member.roles)


def has_admin_role(member: discord.Member) -> bool:
    """Check if member has a configured admin role or Discord admin permissions."""
    if member.guild_permissions.administrator:
        return True
    settings = _get_settings(member.guild.id)
    admin_roles = settings.get("admin_roles", [])
    return any(str(role.id) in admin_roles for role in member.roles)


async def is_staff(bot, user_id: int) -> tuple[bool, bool]:
    """Check if user is staff. Returns (is_support, is_dev)."""
    try:
        main_guild = bot.get_guild(MAIN_SERVER_ID)
        if not main_guild:
            return False, False
        member = main_guild.get_member(user_id)
        if not member:
            try:
                member = await main_guild.fetch_member(user_id)
            except discord.NotFound:
                return False, False
        is_dev = any(role.id == DEV_ROLE_ID for role in member.roles)
        is_support = any(role.id == SUPPORT_ROLE_ID for role in member.roles)
        return is_support or is_dev, is_dev
    except Exception:
        return False, False


def can_moderate(
    moderator: discord.Member, target: discord.Member, action: str = "moderate"
) -> tuple[bool, str]:
    """Check if moderator can perform action on target. Returns (can_do, error_message)."""
    guild = moderator.guild
    bot_member = guild.me

    if moderator.id == target.id:
        return False, f"You cannot {action} yourself."

    if target.id == guild.owner_id:
        return False, f"You cannot {action} the server owner."

    if target.id == bot_member.id:
        return False, f"I cannot {action} myself."

    if bot_member.top_role <= target.top_role:
        return (
            False,
            f"I cannot {action} {target.mention} because their role is equal to or higher than mine.",
        )

    if moderator.id != guild.owner_id and moderator.top_role <= target.top_role:
        return (
            False,
            f"You cannot {action} {target.mention} because their role is equal to or higher than yours.",
        )

    return True, ""
