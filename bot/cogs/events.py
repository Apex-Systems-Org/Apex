import discord
from discord.ext import commands
from datetime import datetime, timedelta, timezone
import re
from collections import defaultdict

from database import db
from helpers import (
    send_mod_log,
    send_log,
    log_member_join,
    is_module_enabled,
    handle_custom_command,
    cache_guild_invites,
)
from helpers.embeds import success, warning, info, mod_embed


class Events(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.spam_tracker = defaultdict(lambda: defaultdict(list))
        self.raid_tracker = defaultdict(list)
        # Automod violation tracker: {guild_id: {user_id: [timestamps]}}
        self.violation_tracker = defaultdict(lambda: defaultdict(list))

    async def _handle_automod_violation(self, message: discord.Message, auto_mod: dict, reason: str):
        guild_id = message.guild.id
        user_id = message.author.id
        now = datetime.utcnow().timestamp()

        # Track this violation (keep last 24h)
        self.violation_tracker[guild_id][user_id].append(now)
        self.violation_tracker[guild_id][user_id] = [
            t for t in self.violation_tracker[guild_id][user_id] if now - t < 86400
        ]
        count = len(self.violation_tracker[guild_id][user_id])

        # Get automod action thresholds
        actions = auto_mod.get("automod_actions", {})
        warn_at = actions.get("warn_at", 3)
        mute_at = actions.get("mute_at", 5)
        mute_duration = actions.get("mute_duration", 600)  # 10 min default
        kick_at = actions.get("kick_at", 0)  # 0 = disabled
        ban_at = actions.get("ban_at", 0)  # 0 = disabled

        member = message.author

        # Escalate based on violation count
        if ban_at > 0 and count >= ban_at:
            try:
                await send_mod_log(message.guild, mod_embed(
                    "Auto-Ban", discord.Color.red()
                ).add_field(name="User", value=f"{member.mention} ({member.id})", inline=True)
                .add_field(name="Reason", value=f"Reached {ban_at} automod violations", inline=False))
                await member.ban(reason=f"Automod: {count} violations - {reason}")
                self.violation_tracker[guild_id].pop(user_id, None)
            except:
                pass
        if kick_at > 0 and count >= kick_at:
            try:
                await send_mod_log(message.guild, mod_embed(
                    "Auto-Kick", discord.Color.orange()
                ).add_field(name="User", value=f"{member.mention} ({member.id})", inline=True)
                .add_field(name="Reason", value=f"Reached {kick_at} automod violations", inline=False))
                await member.kick(reason=f"Automod: {count} violations - {reason}")
                self.violation_tracker[guild_id].pop(user_id, None)
            except:
                pass
        if mute_at > 0 and count >= mute_at:
            try:
                duration = timedelta(seconds=mute_duration)
                await member.timeout(duration, reason=f"Automod: {count} violations - {reason}")
                await message.channel.send(
                    embed=warning(f"{member.mention} has been muted for {mute_duration // 60}m. Reason: {reason} ({count} violations)"),
                    delete_after=10,
                )
                await send_mod_log(message.guild, mod_embed(
                    "Auto-Mute", discord.Color.orange()
                ).add_field(name="User", value=f"{member.mention} ({member.id})", inline=True)
                .add_field(name="Duration", value=f"{mute_duration // 60} minutes", inline=True)
                .add_field(name="Reason", value=f"{count} automod violations", inline=False))
            except:
                pass
        if warn_at > 0 and count >= warn_at and count % warn_at == 0:
            # Warn every X violations
            try:
                case_num = db.get_next_case_number(guild_id)
                db.add_warning(guild_id, user_id, {
                    "case": case_num,
                    "reason": f"Automod: {reason} ({count} violations)",
                    "moderator_id": str(self.bot.user.id),
                    "timestamp": datetime.utcnow().isoformat(),
                })
                db.log_action(guild_id, {
                    "action": "warn",
                    "case": case_num,
                    "user_id": str(user_id),
                    "moderator_id": str(self.bot.user.id),
                    "reason": f"Automod: {reason} ({count} violations)",
                    "timestamp": datetime.utcnow().isoformat(),
                })
                await message.channel.send(
                    embed=warning(f"{member.mention} **Warning #{case_num}** \u2014 {reason} ({count} violations)"),
                    delete_after=10,
                )
            except:
                pass
        # Below thresholds - just notify
        await message.channel.send(
            embed=warning(f"{member.mention} {reason}"),
            delete_after=5,
        )

    # on_member_join: welcome, auto-role, anti-raid, invite tracking
    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        settings = db.get_guild_settings(member.guild.id)
        auto_mod = settings.get("auto_mod", {})

        # Anti-raid detection
        if auto_mod.get("anti_raid"):
            now = datetime.utcnow().timestamp()
            self.raid_tracker[member.guild.id].append(now)
            # Keep only joins from last 10 seconds
            self.raid_tracker[member.guild.id] = [
                t for t in self.raid_tracker[member.guild.id] if now - t < 10
            ]

            raid_threshold = auto_mod.get(
                "raid_threshold", 5
            )  # Default 5 joins in 10 seconds
            if len(self.raid_tracker[member.guild.id]) >= raid_threshold:
                # Raid detected - kick the member
                try:
                    await member.kick(reason="Anti-raid: Too many joins in short time")
                    embed = discord.Embed(title="Anti-Raid Kick", color=discord.Color.red())
                    embed.add_field(
                        name="User", value=f"{member} ({member.id})", inline=True
                    )
                    embed.add_field(
                        name="Reason",
                        value=f"Raid detected ({len(self.raid_tracker[member.guild.id])} joins in 10s)",
                        inline=False,
                    )
                    embed.timestamp = datetime.utcnow()
                    await send_mod_log(member.guild, embed)
                except:
                    pass
                return  # Don't process welcome for kicked raiders

        # Auto-role
        auto_role_id = settings.get("auto_role")
        if auto_role_id:
            try:
                role = member.guild.get_role(int(auto_role_id))
                if role:
                    await member.add_roles(role)
            except Exception as e:
                print(f"Failed to add auto-role: {e}")

        # Welcome message
        welcome_channel_id = settings.get("welcome_channel")
        welcome_message = settings.get("welcome_message")
        if welcome_channel_id and welcome_message:
            try:
                channel = member.guild.get_channel(int(welcome_channel_id))
                if channel:
                    msg = (
                        welcome_message.replace("{user}", member.mention)
                        .replace("{username}", member.name)
                        .replace("{server}", member.guild.name)
                        .replace("{membercount}", str(member.guild.member_count))
                    )
                    await channel.send(msg)
            except Exception as e:
                print(f"Failed to send welcome message: {e}")

        # Invite tracking
        try:
            cached_invites = db.get_cached_invites(member.guild.id)
            current_invites = await member.guild.invites()

            # Find which invite was used
            used_invite = None
            for invite in current_invites:
                cached = next((c for c in cached_invites if c["code"] == invite.code), None)
                if cached and invite.uses > cached["uses"]:
                    used_invite = invite
                    break

            if used_invite and used_invite.inviter:
                db.track_invite(
                    member.guild.id, member.id, used_invite.inviter.id, used_invite.code
                )

            # Update cache
            await cache_guild_invites(member.guild)
        except discord.Forbidden:
            pass  # No permission to view invites
        except Exception as e:
            print(f"Error tracking invite: {e}")

        # Log member join
        await log_member_join(member)

    # on_member_remove: goodbye, invite tracking, member leave log
    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        settings = db.get_guild_settings(member.guild.id)

        # Mark user as left for invite tracking
        db.mark_user_left(member.guild.id, member.id)

        goodbye_channel_id = settings.get("goodbye_channel")
        goodbye_message = settings.get("goodbye_message")
        if goodbye_channel_id and goodbye_message:
            try:
                channel = member.guild.get_channel(int(goodbye_channel_id))
                if channel:
                    msg = (
                        goodbye_message.replace("{user}", member.mention)
                        .replace("{username}", member.name)
                        .replace("{server}", member.guild.name)
                        .replace("{membercount}", str(member.guild.member_count))
                    )
                    await channel.send(msg)
            except Exception as e:
                print(f"Failed to send goodbye message: {e}")

        # Member leave log
        await send_log(
            member.guild,
            "member",
            discord.Embed(
                title="Member Left",
                description=f"{member.mention} ({member})",
                color=discord.Color.red(),
            )
            .add_field(name="ID", value=member.id, inline=True)
            .add_field(
                name="Account Created",
                value=f"<t:{int(member.created_at.timestamp())}:R>",
                inline=True,
            )
            .set_thumbnail(url=member.display_avatar.url),
        )

    # on_message: bot mention, blacklist, auto-mod, auto-responders,
    #             delete triggers, AFK, leveling, TTS, custom commands
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            await self.bot.process_commands(message)
            return
        # Check if message is only a bot mention
        bot_mention = f"<@{self.bot.user.id}>"
        bot_mention_nick = f"<@!{self.bot.user.id}>"
        content_stripped = message.content.strip()
        if content_stripped == bot_mention or content_stripped == bot_mention_nick:
            prefix = db.get_prefix(message.guild.id)
            embed = discord.Embed(
                title="Apex",
                description="A powerful Discord moderation bot with tickets, auto-mod, leveling, and more!",
                color=discord.Color.blue(),
            )
            embed.set_thumbnail(url=self.bot.user.display_avatar.url)
            embed.add_field(name="Prefix", value=f"`{prefix}` or `/`", inline=True)
            embed.add_field(name="Commands", value="`/help`", inline=True)
            embed.add_field(name="Servers", value=str(len(self.bot.guilds)), inline=True)
            embed.add_field(
                name="Links",
                value="[Dashboard](https://apex-systems.vercel.app) • [Support Server](https://apex-systems.vercel.app/discord) • [Documentation](https://apex-systems.vercel.app/docs)",
                inline=False,
            )
            embed.set_footer(text="Thank you for using Apex!")
            await message.channel.send(embed=embed)
        # Check if user is blacklisted
        blacklist_data = db.is_blacklisted(message.author.id)
        if blacklist_data:
            # Check if message looks like a command attempt
            prefix = db.get_prefix(message.guild.id)
            if message.content.startswith(prefix) or message.content.startswith("/"):
                reason = blacklist_data.get("reason", "No reason provided")
                embed = discord.Embed(
                    title="You Are Blacklisted",
                    description=f"You have been blacklisted from using Apex.",
                    color=discord.Color.red(),
                )
                embed.add_field(name="Reason", value=reason, inline=False)
                embed.add_field(
                    name="Appeal",
                    value="If you believe this is a mistake, please contact support.",
                    inline=False,
                )

                # Try to DM the user first
                try:
                    await message.author.send(embed=embed)
                except discord.Forbidden:
                    # If DMs are closed, send in the channel
                    await message.channel.send(
                        f"{message.author.mention}", embed=embed, delete_after=10
                    )
        # Check if user is mod (skip auto-mod for mods)
        is_mod = message.author.guild_permissions.manage_messages

        settings = db.get_guild_settings(message.guild.id)
        auto_mod = settings.get("auto_mod", {})

        violated = False
        violation_reason = None

        if not is_mod:
            # Anti-spam (5 messages in 5 seconds)
            if auto_mod.get("anti_spam"):
                now = datetime.utcnow().timestamp()
                user_messages = self.spam_tracker[message.guild.id][message.author.id]
                user_messages.append(now)
                # Keep only messages from last 5 seconds
                self.spam_tracker[message.guild.id][message.author.id] = [
                    t for t in user_messages if now - t < 5
                ]
                if len(self.spam_tracker[message.guild.id][message.author.id]) >= 5:
                    violated = True
                    violation_reason = "Spam detected"
                    self.spam_tracker[message.guild.id][message.author.id] = []

            # Block links
            if auto_mod.get("block_links") and not violated:
                url_pattern = r"https?://\S+"
                if re.search(url_pattern, message.content):
                    violated = True
                    violation_reason = "Links not allowed"

            # Block Discord invites
            if auto_mod.get("block_invites") and not violated:
                invite_pattern = (
                    r"(discord\.gg|discord\.com/invite|discordapp\.com/invite)/\S+"
                )
                if re.search(invite_pattern, message.content, re.IGNORECASE):
                    violated = True
                    violation_reason = "Discord invites not allowed"

            # Banned words
            banned_words = auto_mod.get("banned_words", [])
            if banned_words and not violated:
                content_lower = message.content.lower()
                for word in banned_words:
                    if word.lower() in content_lower:
                        violated = True
                        violation_reason = f"Banned word detected"
                        break

            # Max mentions
            max_mentions = auto_mod.get("max_mentions", 0)
            if max_mentions > 0 and not violated:
                total_mentions = len(message.mentions) + len(message.role_mentions)
                if total_mentions > max_mentions:
                    violated = True
                    violation_reason = (
                        f"Too many mentions ({total_mentions}/{max_mentions})"
                    )

            # Max caps percentage
            max_caps = auto_mod.get("max_caps", 0)
            if max_caps > 0 and not violated and len(message.content) >= 10:
                letters = [c for c in message.content if c.isalpha()]
                if letters:
                    caps_percent = (
                        sum(1 for c in letters if c.isupper()) / len(letters) * 100
                    )
                    if caps_percent > max_caps:
                        violated = True
                        violation_reason = f"Too many caps ({int(caps_percent)}%)"

        if violated:
            try:
                await message.delete()
            except:
                pass

            # Track violations and apply automod actions
            await self._handle_automod_violation(message, auto_mod, violation_reason)
            await self.bot.process_commands(message)
        # Auto-responders
        auto_responders = db.get_auto_responders(message.guild.id)
        for responder in auto_responders:
            if not responder.get("enabled", True):
                continue
            trigger = responder.get("trigger_word", "")
            response = responder.get("response", "")
            match_type = responder.get("match_type", "contains")
            ignore_case = responder.get("ignore_case", True)

            content = message.content.lower() if ignore_case else message.content
            trigger_check = trigger.lower() if ignore_case else trigger

            matched = False
            if match_type == "exact":
                matched = content == trigger_check
            elif match_type == "startswith":
                matched = content.startswith(trigger_check)
            elif match_type == "endswith":
                matched = content.endswith(trigger_check)
            elif match_type == "word":
                # Match whole word only
                pattern = r"\b" + re.escape(trigger_check) + r"\b"
                flags = re.IGNORECASE if ignore_case else 0
                matched = bool(re.search(pattern, message.content, flags))
            else:  # contains
                matched = trigger_check in content

            if matched:
                # Replace placeholders
                response_text = response.replace("{user}", message.author.mention)
                response_text = response_text.replace("{username}", message.author.name)
                response_text = response_text.replace("{server}", message.guild.name)
                response_text = response_text.replace("{channel}", message.channel.mention)
                try:
                    await message.channel.send(response_text)
                except:
                    pass
                break  # Only trigger one auto-response per message

        # Delete command triggers if enabled
        if settings.get("delete_triggers"):
            ctx = await self.bot.get_context(message)
            if ctx.valid:
                try:
                    await message.delete()
                except:
                    pass

        # AFK System handling
        # Check if author is AFK and remove their status
        author_afk = db.get_afk(message.guild.id, message.author.id)
        if author_afk:
            db.remove_afk(message.guild.id, message.author.id)
            try:
                await message.channel.send(
                    embed=info(f"Welcome back {message.author.mention}! I've removed your AFK status."),
                    delete_after=5,
                )
            except:
                pass

        # Check if any mentioned users are AFK
        if message.mentions:
            mentioned_ids = [m.id for m in message.mentions if not m.bot]
            afk_users = db.get_afk_users(message.guild.id, mentioned_ids)
            for afk_data in afk_users:
                user = message.guild.get_member(int(afk_data["user_id"]))
                if user:
                    set_at = datetime.fromisoformat(afk_data["set_at"]).replace(
                        tzinfo=timezone.utc
                    )
                    reason = afk_data.get("reason", "AFK")
                    afk_embed = discord.Embed(
                        description=f"**{user.display_name}** is currently AFK.\n> **Reason:** {reason}\n> **Since:** <t:{int(set_at.timestamp())}:R>",
                        color=discord.Color.orange(),
                    )
                    afk_embed.set_thumbnail(url=user.display_avatar.url)
                    afk_embed.set_footer(text="Apex")
                    try:
                        await message.channel.send(
                            embed=afk_embed,
                            delete_after=10,
                        )
                    except:
                        pass

        # Leveling system - give XP for messages
        if is_module_enabled(message.guild.id, "leveling"):
            if hasattr(self.bot, 'process_leveling_xp'):
                await self.bot.process_leveling_xp(message)
            else:
                # Fallback: try to call the standalone function if it exists in bot module
                from bot import process_leveling_xp
                await process_leveling_xp(message)

        # Check for custom commands
        prefix = db.get_prefix(message.guild.id)
        if message.content.startswith(prefix):
            parts = message.content[len(prefix):].split()
            cmd_name = parts[0].lower() if parts else ""
            args = parts[1:] if len(parts) > 1 else []
            custom_cmd = db.get_custom_command(message.guild.id, cmd_name)
            if custom_cmd and custom_cmd.get("enabled", True):
                await handle_custom_command(self.bot, message, custom_cmd, args)
                return

        await self.bot.process_commands(message)

    # on_guild_join / on_guild_remove
    @commands.Cog.listener()
    async def on_guild_join(self, guild: discord.Guild):
        db.add_bot_guild(guild.id, guild.name)

    @commands.Cog.listener()
    async def on_guild_remove(self, guild: discord.Guild):
        db.remove_bot_guild(guild.id)


async def setup(bot: commands.Bot):
    await bot.add_cog(Events(bot))
