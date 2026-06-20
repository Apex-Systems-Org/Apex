import discord
from discord.ext import commands
from datetime import datetime, timedelta, timezone
import re
import aiohttp
from database import db
from helpers.embeds import success, error, warning, info
from helpers.cache import settings_cache, prefix_cache


def get_cached_settings(guild_id) -> dict:
    """Get guild settings with caching."""
    key = str(guild_id)
    cached = settings_cache.get(key)
    if cached is not None:
        return cached
    settings = db.get_guild_settings(guild_id)
    settings_cache.set(key, settings)
    return settings


def get_cached_prefix(guild_id) -> str:
    """Get guild prefix with caching."""
    key = str(guild_id)
    cached = prefix_cache.get(key)
    if cached is not None:
        return cached
    prefix = db.get_prefix(guild_id)
    prefix_cache.set(key, prefix)
    return prefix


def invalidate_settings_cache(guild_id):
    """Clear cache when settings are updated."""
    settings_cache.delete(str(guild_id))
    prefix_cache.delete(str(guild_id))


def is_module_enabled(guild_id: int, module: str) -> bool:
    """Check if a module is enabled for the guild."""
    settings = get_cached_settings(guild_id)
    modules = settings.get("modules", {})
    default_off_modules = {"giveaways", "leveling", "voice_channels", "role_persistence", "starboard", "loa"}
    default_value = module not in default_off_modules
    return modules.get(module, default_value)


def parse_duration(duration_str: str) -> tuple[timedelta, str] | None:
    """Parse duration string like '1s', '5m', '2h', '1d' into timedelta and human-readable format."""
    duration_str = duration_str.lower().strip()
    match = re.match(
        r"^(\d+)\s*(s|sec|second|seconds|m|min|minute|minutes|h|hr|hour|hours|d|day|days)$",
        duration_str,
    )
    if not match:
        try:
            minutes = int(duration_str)
            return (
                timedelta(minutes=minutes),
                f"{minutes} minute{'s' if minutes != 1 else ''}",
            )
        except ValueError:
            return None

    value = int(match.group(1))
    unit = match.group(2)

    if unit in ("s", "sec", "second", "seconds"):
        return timedelta(seconds=value), f"{value} second{'s' if value != 1 else ''}"
    elif unit in ("m", "min", "minute", "minutes"):
        return timedelta(minutes=value), f"{value} minute{'s' if value != 1 else ''}"
    elif unit in ("h", "hr", "hour", "hours"):
        return timedelta(hours=value), f"{value} hour{'s' if value != 1 else ''}"
    elif unit in ("d", "day", "days"):
        return timedelta(days=value), f"{value} day{'s' if value != 1 else ''}"
    return None


async def send_mod_log(guild: discord.Guild, embed: discord.Embed):
    """Send an embed to the mod log channel if configured."""
    settings = db.get_guild_settings(guild.id)
    channel_id = settings.get("mod_log_channel")
    if channel_id:
        try:
            channel = guild.get_channel(int(channel_id))
            if channel:
                await channel.send(embed=embed)
        except Exception as e:
            print(f"Failed to send mod log: {e}")


async def send_log(guild: discord.Guild, log_type: str, embed: discord.Embed):
    """Send a log message to the appropriate log channel."""
    settings = db.get_guild_settings(guild.id)
    logging_config = settings.get("logging", {})

    channel_id = logging_config.get(f"{log_type}_log_channel")
    if not channel_id:
        return

    try:
        channel = guild.get_channel(int(channel_id))
        if channel:
            embed.timestamp = datetime.utcnow()
            await channel.send(embed=embed)
    except Exception as e:
        print(f"Failed to send {log_type} log: {e}")


async def dm_user(
    user: discord.Member | discord.User,
    guild: discord.Guild,
    action: str,
    reason: str,
    duration: str = None,
):
    """Send a DM to the user about a moderation action if enabled."""
    settings = db.get_guild_settings(guild.id)
    if not settings.get("dm_on_moderation", True):
        return

    action_text = {
        "warn": "warned in",
        "mute": "muted in",
        "timeout": "timed out in",
        "kick": "kicked from",
        "ban": "banned from",
        "softban": "softbanned from",
    }.get(action, f"{action} in")

    embed = discord.Embed(
        title=f"You have been {action_text} {guild.name}",
        color=discord.Color.red(),
        timestamp=datetime.utcnow(),
    )
    embed.add_field(name="Reason", value=reason, inline=False)
    if duration:
        embed.add_field(name="Duration", value=duration, inline=False)
    embed.set_footer(text=f"Server: {guild.name}")

    try:
        await user.send(embed=embed)
    except discord.Forbidden:
        pass
    except Exception as e:
        print(f"Failed to DM user: {e}")


async def log_member_join(member: discord.Member):
    """Log member join to member log channel."""
    embed = discord.Embed(title="Member Joined", color=discord.Color.green())
    embed.add_field(name="Member", value=f"{member.mention} ({member})", inline=True)
    embed.add_field(name="ID", value=member.id, inline=True)
    embed.add_field(
        name="Account Created",
        value=f"<t:{int(member.created_at.timestamp())}:R>",
        inline=True,
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    await send_log(member.guild, "member", embed)


async def check_warning_actions(guild: discord.Guild, user: discord.Member):
    """Check if user should be auto-kicked/banned based on warning count."""
    settings = db.get_guild_settings(guild.id)
    warning_actions = settings.get("warning_actions", {})
    kick_at = warning_actions.get("kick_at", 0)
    ban_at = warning_actions.get("ban_at", 0)

    warnings = db.get_user_warnings(guild.id, user.id)
    warning_count = len(warnings)

    if ban_at > 0 and warning_count >= ban_at:
        try:
            await user.ban(reason=f"Auto-ban: Reached {ban_at} warnings")
            embed = discord.Embed(title="Auto-Ban", color=discord.Color.red())
            embed.add_field(
                name="User", value=f"{user.mention} ({user.id})", inline=True
            )
            embed.add_field(
                name="Reason", value=f"Reached {ban_at} warnings", inline=False
            )
            embed.timestamp = datetime.utcnow()
            await send_mod_log(guild, embed)
        except:
            pass
    elif kick_at > 0 and warning_count >= kick_at:
        try:
            await user.kick(reason=f"Auto-kick: Reached {kick_at} warnings")
            embed = discord.Embed(title="Auto-Kick", color=discord.Color.orange())
            embed.add_field(
                name="User", value=f"{user.mention} ({user.id})", inline=True
            )
            embed.add_field(
                name="Reason", value=f"Reached {kick_at} warnings", inline=False
            )
            embed.timestamp = datetime.utcnow()
            await send_mod_log(guild, embed)
        except:
            pass


async def apply_bot_profile(guild_id: int, bot_instance=None) -> dict:
    """Apply bot profile settings (nickname, avatar) for a guild."""
    import sys

    def log(msg):
        print(msg, file=sys.stderr, flush=True)

    _bot = bot_instance

    try:
        log(f"[Bot Profile] Applying profile for guild {guild_id}")
        log(f"[Bot Profile] Bot has {len(_bot.guilds)} guilds in cache")
        guild = _bot.get_guild(guild_id)
        if not guild:
            log(f"[Bot Profile] Guild {guild_id} not in cache, fetching from API")
            try:
                guild = await _bot.fetch_guild(guild_id)
            except Exception as e:
                log(f"[Bot Profile] Failed to fetch guild: {e}")
                return {"success": False, "error": "Guild not found"}

        settings = db.get_guild_settings(guild_id)
        bot_profile = settings.get("bot_profile", {})
        log(f"[Bot Profile] Settings: {bot_profile}")

        me = guild.me
        if not me:
            log(f"[Bot Profile] Bot member not in cache, fetching")
            try:
                me = await guild.fetch_member(_bot.user.id)
            except Exception as e:
                log(f"[Bot Profile] Failed to fetch bot member: {e}")
                return {"success": False, "error": "Bot member not found in guild"}

        new_nick = bot_profile.get("nickname") or None
        log(f"[Bot Profile] Applying nickname: '{new_nick}' (current: '{me.nick}')")
        if me.nick != new_nick:
            try:
                await me.edit(nick=new_nick)
                log(f"[Bot Profile] Nickname applied successfully")
            except discord.Forbidden as e:
                log(f"[Bot Profile] Forbidden to change nickname: {e}")
                return {
                    "success": False,
                    "error": "Missing permissions to change nickname",
                }
            except Exception as e:
                log(f"[Bot Profile] Error changing nickname: {e}")
                return {
                    "success": False,
                    "error": f"Failed to change nickname: {str(e)}",
                }

        avatar_url = bot_profile.get("avatar_url")
        log(f"[Bot Profile] Avatar URL: {avatar_url}")
        if avatar_url:
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(avatar_url) as resp:
                        log(f"[Bot Profile] Avatar fetch status: {resp.status}")
                        if resp.status == 200:
                            avatar_data = await resp.read()
                            log(
                                f"[Bot Profile] Avatar data size: {len(avatar_data)} bytes"
                            )

                            import base64

                            content_type = resp.content_type or "image/png"
                            b64_data = base64.b64encode(avatar_data).decode("utf-8")
                            avatar_uri = f"data:{content_type};base64,{b64_data}"

                            await _bot.http.request(
                                discord.http.Route(
                                    "PATCH",
                                    "/guilds/{guild_id}/members/@me",
                                    guild_id=guild.id,
                                ),
                                json={"avatar": avatar_uri},
                            )
                            log(f"[Bot Profile] Avatar applied successfully")
                        else:
                            return {
                                "success": False,
                                "error": f"Failed to fetch avatar: HTTP {resp.status}",
                            }
            except discord.Forbidden as e:
                log(f"[Bot Profile] Forbidden to change avatar: {e}")
                return {
                    "success": False,
                    "error": "Missing permissions to change avatar",
                }
            except Exception as e:
                log(f"[Bot Profile] Error applying avatar: {e}")
                return {"success": False, "error": f"Failed to apply avatar: {str(e)}"}
        else:
            try:
                await _bot.http.request(
                    discord.http.Route(
                        "PATCH", "/guilds/{guild_id}/members/@me", guild_id=guild.id
                    ),
                    json={"avatar": None},
                )
            except discord.Forbidden:
                pass
            except Exception:
                pass

        return {"success": True}
    except Exception as e:
        log(f"[Bot Profile] Exception: {e}")
        return {"success": False, "error": str(e)}


def extract_id(value: str) -> str:
    """Extract an ID from a mention or return the value if it's already an ID."""
    match = re.match(r"<@!?(\d+)>", value)
    if match:
        return match.group(1)
    match = re.match(r"<@&(\d+)>", value)
    if match:
        return match.group(1)
    match = re.match(r"<#(\d+)>", value)
    if match:
        return match.group(1)
    if value.isdigit():
        return value
    return value


def fuzzy_match(query: str, items: list, key_func) -> any:
    """Find the best matching item by name (case-insensitive, partial match)."""
    query_lower = query.lower()
    for item in items:
        if key_func(item).lower() == query_lower:
            return item
    for item in items:
        if key_func(item).lower().startswith(query_lower):
            return item
    for item in items:
        if query_lower in key_func(item).lower():
            return item
    return None


def format_as_mention(value: str, mention_type: str, guild: discord.Guild) -> str:
    """Format a value as a Discord mention with fuzzy matching."""
    if mention_type == "user" and re.match(r"<@!?\d+>", value):
        return value
    if mention_type == "role" and re.match(r"<@&\d+>", value):
        return value
    if mention_type == "channel" and re.match(r"<#\d+>", value):
        return value

    id_value = extract_id(value)
    if id_value.isdigit():
        if mention_type == "user":
            return f"<@{id_value}>"
        elif mention_type == "role":
            return f"<@&{id_value}>"
        elif mention_type == "channel":
            return f"<#{id_value}>"

    if mention_type == "role":
        role = fuzzy_match(value, guild.roles, lambda r: r.name)
        if role:
            return role.mention
    elif mention_type == "channel":
        channel = fuzzy_match(value, guild.text_channels, lambda c: c.name)
        if channel:
            return channel.mention
    elif mention_type == "user":
        member = fuzzy_match(value, guild.members, lambda m: m.display_name)
        if not member:
            member = fuzzy_match(value, guild.members, lambda m: m.name)
        if member:
            return member.mention

    return value


def replace_placeholders(text: str, message: discord.Message, args: list = None) -> str:
    """Replace all placeholders in text with actual values."""
    if not text:
        return text

    args = args or []
    raw_args = " ".join(args)

    text = (
        text.replace("{user}", message.author.mention)
        .replace("{username}", message.author.name)
        .replace("{server}", message.guild.name)
        .replace("{channel}", message.channel.mention)
        .replace("{membercount}", str(message.guild.member_count))
    )

    text = text.replace("{args}", raw_args)

    if "|" in raw_args:
        parts = raw_args.split("|", 1)
        before_delim = parts[0].strip()
        after_delim = parts[1].strip() if len(parts) > 1 else ""

        text = text.replace("{before|}", before_delim)
        text = text.replace("{after|}", after_delim)

        all_parts = [p.strip() for p in raw_args.split("|")]
        for i in range(1, 10):
            if i <= len(all_parts):
                text = text.replace(f"{{{i}|}}", all_parts[i - 1])
            else:
                text = text.replace(f"{{{i}|}}", "")
    else:
        text = text.replace("{before|}", raw_args)
        text = text.replace("{after|}", "")
        for i in range(1, 10):
            text = text.replace(f"{{{i}|}}", raw_args if i == 1 else "")

    for match in re.findall(r"\{(\d+):(user|role|channel)\}", text):
        num, mention_type = int(match[0]), match[1]
        if num <= len(args):
            formatted = format_as_mention(args[num - 1], mention_type, message.guild)
            text = text.replace(f"{{{num}:{mention_type}}}", formatted)
        else:
            text = text.replace(f"{{{num}:{mention_type}}}", "")

    for i in range(1, 10):
        if i <= len(args):
            text = text.replace(f"{{{i}}}", args[i - 1])
        else:
            text = text.replace(f"{{{i}}}", "")

    for match in re.findall(r"\{args:(\d+)\}", text):
        n = int(match)
        if n <= len(args):
            text = text.replace(f"{{args:{match}}}", " ".join(args[n - 1 :]))
        else:
            text = text.replace(f"{{args:{match}}}", "")

    for match in re.findall(r"\{args:(\d+)\|(\d+)\}", text):
        start_pos, part_num = int(match[0]), int(match[1])
        if start_pos <= len(args):
            rest_args = " ".join(args[start_pos - 1 :])
            parts = [p.strip() for p in rest_args.split("|")]
            if part_num <= len(parts):
                text = text.replace(
                    f"{{args:{match[0]}|{match[1]}}}", parts[part_num - 1]
                )
            else:
                text = text.replace(f"{{args:{match[0]}|{match[1]}}}", "")
        else:
            text = text.replace(f"{{args:{match[0]}|{match[1]}}}", "")

    return text


async def handle_custom_command(bot, message: discord.Message, cmd: dict, args: list = None):
    """Handle execution of a custom command."""
    args = args or []
    guild_id = str(message.guild.id)
    user_id = str(message.author.id)
    cmd_name = cmd.get("name", "")

    # We store cooldowns on the bot instance
    if not hasattr(bot, '_custom_command_cooldowns'):
        bot._custom_command_cooldowns = {}
    custom_command_cooldowns = bot._custom_command_cooldowns

    allowed_roles = cmd.get("allowed_roles", [])
    if allowed_roles:
        user_role_ids = [str(r.id) for r in message.author.roles]
        if not any(role_id in user_role_ids for role_id in allowed_roles):
            return

    allowed_channels = cmd.get("allowed_channels", [])
    if allowed_channels and str(message.channel.id) not in allowed_channels:
        return

    cooldown_seconds = cmd.get("cooldown", 0)
    if cooldown_seconds > 0:
        if guild_id not in custom_command_cooldowns:
            custom_command_cooldowns[guild_id] = {}
        if cmd_name not in custom_command_cooldowns[guild_id]:
            custom_command_cooldowns[guild_id][cmd_name] = {}

        last_used = custom_command_cooldowns[guild_id][cmd_name].get(user_id, 0)
        now = datetime.utcnow().timestamp()

        if now - last_used < cooldown_seconds:
            remaining = int(cooldown_seconds - (now - last_used))
            try:
                await message.channel.send(
                    embed=warning(f"Command on cooldown. Try again in {remaining}s."),
                    delete_after=3,
                )
            except:
                pass
            return

        custom_command_cooldowns[guild_id][cmd_name][user_id] = now

    if cmd.get("delete_trigger", False):
        try:
            await message.delete()
        except:
            pass

    target_channels = []
    response_channel_id = cmd.get("response_channel")

    if response_channel_id:
        target_channel = message.guild.get_channel(int(response_channel_id))
        if target_channel:
            target_channels.append(target_channel)
    else:
        target_channels.append(message.channel)

    response_text = replace_placeholders(cmd.get("response", ""), message, args)

    reactions_to_add = re.findall(r"\{react:([^}]+)\}", response_text)
    for reaction in reactions_to_add:
        response_text = response_text.replace("{react:" + reaction + "}", "").strip()

    embedded_commands = re.findall(r"\{\{(.+?)\}\}", response_text, re.DOTALL)
    for embedded_cmd in embedded_commands:
        response_text = response_text.replace("{{" + embedded_cmd + "}}", "").strip()

    embed = None
    embed_config = cmd.get("embed")
    if embed_config:
        try:
            color = discord.Color.from_str(embed_config.get("color", "#5865F2"))
        except:
            color = discord.Color.blurple()

        embed = discord.Embed(color=color)

        if embed_config.get("title"):
            embed.title = replace_placeholders(embed_config["title"], message, args)

        if embed_config.get("description"):
            embed.description = replace_placeholders(
                embed_config["description"], message, args
            )

        if embed_config.get("thumbnail"):
            if embed_config["thumbnail"] == "{user_avatar}":
                embed.set_thumbnail(url=message.author.display_avatar.url)
            elif embed_config["thumbnail"] == "{server_icon}":
                if message.guild.icon:
                    embed.set_thumbnail(url=message.guild.icon.url)
            else:
                embed.set_thumbnail(url=embed_config["thumbnail"])

        if embed_config.get("image"):
            embed.set_image(url=embed_config["image"])

        if embed_config.get("footer"):
            embed.set_footer(
                text=replace_placeholders(embed_config["footer"], message, args)
            )

        if embed_config.get("timestamp"):
            embed.timestamp = datetime.utcnow()

        for field in embed_config.get("fields", []):
            embed.add_field(
                name=replace_placeholders(field.get("name", "Field"), message, args),
                value=replace_placeholders(field.get("value", "Value"), message, args),
                inline=field.get("inline", True),
            )

        if not (embed.title or embed.description or embed.fields):
            embed = None

    sent_messages = []
    for channel in target_channels:
        try:
            if embed:
                sent_msg = await channel.send(
                    content=response_text if response_text else None, embed=embed
                )
            elif response_text:
                sent_msg = await channel.send(response_text)
            else:
                sent_msg = None
            if sent_msg:
                sent_messages.append(sent_msg)
        except Exception as e:
            print(f"Failed to send custom command response to {channel}: {e}")

    for sent_msg in sent_messages:
        for reaction in reactions_to_add:
            try:
                if reaction.isdigit():
                    emoji = bot.get_emoji(int(reaction))
                    if emoji:
                        await sent_msg.add_reaction(emoji)
                elif ":" in reaction and not reaction.startswith("<"):
                    parts = reaction.split(":")
                    if len(parts) >= 2 and parts[-1].isdigit():
                        emoji = bot.get_emoji(int(parts[-1]))
                        if emoji:
                            await sent_msg.add_reaction(emoji)
                else:
                    await sent_msg.add_reaction(reaction.strip())
            except Exception as e:
                print(f"Failed to add reaction {reaction}: {e}")

    if cmd.get("dm_response", False):
        try:
            if embed:
                await message.author.send(
                    content=response_text if response_text else None, embed=embed
                )
            elif response_text:
                await message.author.send(response_text)
        except discord.Forbidden:
            pass

    for embedded_cmd in embedded_commands:
        processed_cmd = replace_placeholders(embedded_cmd.strip(), message, args)
        try:
            original_content = message.content
            message.content = processed_cmd
            ctx = await bot.get_context(message)
            message.content = original_content
            if ctx.valid and ctx.command:
                ctx._embedded_command = True
                await bot.invoke(ctx)
        except Exception as e:
            print(f"Failed to execute embedded command '{embedded_cmd}': {e}")


async def cache_guild_invites(guild: discord.Guild):
    """Cache all invites for a guild."""
    settings = db.get_guild_settings(guild.id)
    invite_settings = settings.get("invite_tracking", {})
    if not invite_settings.get("enabled", True):
        return
    try:
        invites = await guild.invites()
        invite_list = []
        for inv in invites:
            invite_list.append(
                {
                    "code": inv.code,
                    "uses": inv.uses,
                    "inviter_id": str(inv.inviter.id) if inv.inviter else None,
                }
            )
        db.cache_invites(guild.id, invite_list)
    except discord.Forbidden:
        pass
