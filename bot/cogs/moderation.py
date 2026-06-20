import discord
from discord.ext import commands, tasks
from discord import app_commands
from datetime import datetime, timedelta, timezone
import asyncio
import re

from helpers import (
    has_mod_role,
    has_admin_role,
    can_moderate,
    send_mod_log,
    dm_user,
    check_warning_actions,
    parse_duration,
    is_staff,
    is_module_enabled,
    fuzzy_match,
)
from helpers.embeds import success, error, warning, info
from helpers.flags import mod_flag_parser, ban_flag_parser, purge_flag_parser
from database import db
from config import config


def resolve_user_id(user_str: str, guild: discord.Guild):
    """Resolve a user string to (member_or_none, user_id_or_none)."""
    mention_match = re.search(r"<@!?(\d{17,20})>", user_str)
    if mention_match:
        uid = int(mention_match.group(1))
        return guild.get_member(uid), uid
    if user_str.isdigit() and len(user_str) >= 17:
        uid = int(user_str)
        return guild.get_member(uid), uid
    member = discord.utils.find(
        lambda m: m.name.lower() == user_str.lower()
        or (m.nick and m.nick.lower() == user_str.lower())
        or str(m).lower() == user_str.lower(),
        guild.members,
    )
    if member:
        return member, member.id
    return None, None


class LeaderboardView(discord.ui.View):
    def __init__(self, leaderboard: list, guild: discord.Guild, per_page: int = 10):
        super().__init__(timeout=120)
        self.leaderboard = leaderboard
        self.guild = guild
        self.per_page = per_page
        self.page = 0
        self.max_page = (len(leaderboard) - 1) // per_page

    def get_embed(self) -> discord.Embed:
        embed = discord.Embed(
            title="Moderation Leaderboard",
            color=discord.Color.gold(),
            timestamp=datetime.utcnow(),
        )

        start = self.page * self.per_page
        end = start + self.per_page
        page_data = self.leaderboard[start:end]

        lines = []
        medals = ["1.", "2.", "3."]
        for i, (mod_id, count) in enumerate(page_data):
            rank = start + i
            prefix = medals[rank] if rank < 3 else f"**{rank + 1}.**"
            lines.append(f"{prefix} <@{mod_id}> - **{count}** actions")

        embed.description = "\n".join(lines)
        embed.set_footer(
            text=f"Page {self.page + 1}/{self.max_page + 1} • Total moderators: {len(self.leaderboard)}"
        )

        return embed

    @discord.ui.button(label="◀", style=discord.ButtonStyle.secondary)
    async def prev_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        if self.page > 0:
            self.page -= 1
        await interaction.response.edit_message(embed=self.get_embed(), view=self)

    @discord.ui.button(label="▶", style=discord.ButtonStyle.secondary)
    async def next_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        if self.page < self.max_page:
            self.page += 1
        await interaction.response.edit_message(embed=self.get_embed(), view=self)


class Moderation(commands.Cog):
    # 3 second cooldown per user on all mod commands
    async def cog_check(self, ctx: commands.Context) -> bool:
        bucket = self._cd.get_bucket(ctx.message)
        retry_after = bucket.update_rate_limit()
        if retry_after:
            await ctx.send(embed=error(f"Cooldown. Try again in {retry_after:.1f}s."), delete_after=3)
            return False
        return True

    _cd = commands.CooldownMapping.from_cooldown(1, 3, commands.BucketType.user)

    def __init__(self, bot):
        self.bot = bot
        self.check_tempbans.start()

    def cog_unload(self):
        self.check_tempbans.cancel()

    @tasks.loop(seconds=30)
    async def check_tempbans(self):
        """Check for expired tempbans and unban users."""
        try:
            expired = db.get_expired_tempbans()
            for tempban in expired:
                try:
                    guild = self.bot.get_guild(int(tempban["guild_id"]))
                    if guild:
                        await guild.unban(
                            discord.Object(id=int(tempban["user_id"])),
                            reason="Temp-ban expired"
                        )
                except discord.NotFound:
                    pass  # Already unbanned
                except Exception:
                    pass
                db.remove_tempban(tempban["id"])
        except Exception as e:
            print(f"Error checking tempbans: {e}")

    @check_tempbans.before_loop
    async def before_check_tempbans(self):
        await self.bot.wait_until_ready()

    @commands.hybrid_command(name="prefix", description="Change the bot prefix for this server")
    async def prefix(self, ctx: commands.Context, new_prefix: str):
        if not has_admin_role(ctx.author):
            await ctx.send(embed=error("You don't have permission to use this command."))
            return
        if len(new_prefix) > 5:
            await ctx.send(embed=error("Prefix must be 5 characters or less."))
            return

        db.set_prefix(ctx.guild.id, new_prefix)
        await ctx.send(embed=success(f"Prefix changed to `{new_prefix}`"))

    @commands.hybrid_command(
        name="dashboard", description="Get the link to this server's dashboard"
    )
    async def dashboard(self, ctx: commands.Context):
        # Check for Manage Server permission or admin role
        has_manage_server = ctx.author.guild_permissions.manage_guild
        has_admin = has_admin_role(ctx.author)

        if not has_manage_server and not has_admin:
            await ctx.send(
                embed=error("You need Manage Server permission or an admin role to access the dashboard.")
            )
            return

        url = f"{config.DASHBOARD_URL}/server/{ctx.guild.id}"
        embed = discord.Embed(
            title="Server Dashboard",
            description=f"[Click here to open the dashboard]({url})",
            color=discord.Color.blurple(),
        )
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="warn", description="Warn a user")
    @app_commands.describe(
        user="User to warn (mention, username, or ID)", reason="Reason for warning"
    )
    async def warn(self, ctx: commands.Context, user: str, *, reason: str):
        """Warn a user. Flags: --silent/-s (no DM), --notify/-n (force DM)"""
        if not is_module_enabled(ctx.guild.id, "moderation"):
            await ctx.send(embed=error("The moderation module is disabled on this server."))
            return
        if not has_mod_role(ctx.author):
            await ctx.send(embed=error("You don't have permission to use this command."))
            return

        # Parse flags from reason
        parser = mod_flag_parser()
        flags, remaining = parser.parse(reason)
        reason = flags["reason"] or remaining or "No reason provided"

        target_member, user_id = resolve_user_id(user, ctx.guild)

        if not user_id:
            await ctx.send(embed=error(f"Could not find user: {user}"))
            return

        if target_member:
            can_do, error_msg = can_moderate(ctx.author, target_member, "warn")
            if not can_do:
                await ctx.send(embed=error(error_msg))
                return
            user_display = f"{target_member.mention} ({target_member.id})"
            user_name = target_member.name
            if not flags["silent"]:
                await dm_user(target_member, ctx.guild, "warn", reason)
        else:
            try:
                target_user = await self.bot.fetch_user(user_id)
                user_display = f"{target_user.mention} ({target_user.id})"
                user_name = target_user.name
            except:
                user_display = f"<@{user_id}> ({user_id})"
                user_name = f"User {user_id}"

        case_num = db.get_next_case_number(ctx.guild.id)

        warning_data = {
            "case": case_num,
            "reason": reason,
            "moderator_id": str(ctx.author.id),
            "timestamp": datetime.utcnow().isoformat(),
        }
        db.add_warning(ctx.guild.id, user_id, warning_data)
        db.log_action(
            ctx.guild.id,
            {
                "action": "warn",
                "case": case_num,
                "user_id": str(user_id),
                "moderator_id": str(ctx.author.id),
                "reason": reason,
                "timestamp": datetime.utcnow().isoformat(),
            },
        )

        response = f"**Case #{case_num}:** {user_name} has been warned for {reason}."
        if flags["silent"]:
            response += " (silent)"
        await ctx.send(embed=success(response))

        embed = discord.Embed(
            title=f"Case #{case_num} | Warn", color=discord.Color.yellow()
        )
        embed.add_field(name="User", value=user_display, inline=True)
        embed.add_field(name="Moderator", value=f"{ctx.author.mention}", inline=True)
        embed.add_field(name="Reason", value=reason, inline=False)
        if flags["silent"]:
            embed.set_footer(text="DM not sent (silent)")
        embed.timestamp = datetime.utcnow()
        await send_mod_log(ctx.guild, embed)

        if target_member:
            await check_warning_actions(ctx.guild, target_member)

    @commands.hybrid_command(name="warnings", description="View warnings for a user")
    @app_commands.describe(user="User to check (ID, mention, or username)")
    async def warnings(self, ctx: commands.Context, user: str):
        if not is_module_enabled(ctx.guild.id, "moderation"):
            await ctx.send(embed=error("The moderation module is disabled on this server."))
            return

        # Parse user input - can be ID, mention, or username
        user_id = None
        user_display = None

        # Try to extract ID from mention <@123> or <@!123>
        mention_match = re.search(r"<@!?(\d{17,20})>", user)
        if mention_match:
            user_id = mention_match.group(1)
        # Check if it's a raw ID
        elif user.isdigit() and len(user) >= 17:
            user_id = user
        else:
            # Try to find member by name
            member = discord.utils.find(
                lambda m: m.name.lower() == user.lower()
                or (m.nick and m.nick.lower() == user.lower())
                or str(m).lower() == user.lower(),
                ctx.guild.members,
            )
            if member:
                user_id = str(member.id)
                user_display = member.display_name

        if not user_id:
            await ctx.send(embed=error(f"Could not find user: {user}"))
            return

        if not user_display:
            member = ctx.guild.get_member(int(user_id))
            user_display = member.display_name if member else f"User {user_id}"

        warns = db.get_user_warnings(ctx.guild.id, user_id)
        if not warns:
            await ctx.send(embed=info(f"{user_display} has no warnings."))
            return

        embed = discord.Embed(
            title=f"Warnings for {user_display}", color=discord.Color.orange()
        )
        for i, w in enumerate(warns, 1):
            # Convert ISO timestamp to Unix
            ts = int(
                datetime.fromisoformat(w["timestamp"])
                .replace(tzinfo=timezone.utc)
                .timestamp()
            )
            embed.add_field(
                name=f"Warning {i}",
                value=f"**Reason:** {w['reason']}\n**Date:** <t:{ts}:f>",
                inline=False,
            )
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="kick", description="Kick a user from the server")
    @app_commands.describe(
        user="User to kick (mention, username, or ID)", reason="Reason for kick"
    )
    async def kick(self, ctx: commands.Context, user: str, *, reason: str = "No reason provided"):
        """Kick a user. Flags: --silent/-s (no DM), --reason/-r <text>"""
        if not is_module_enabled(ctx.guild.id, "moderation"):
            await ctx.send(embed=error("The moderation module is disabled on this server."))
            return
        if not has_mod_role(ctx.author):
            await ctx.send(embed=error("You don't have permission to use this command."))
            return

        parser = mod_flag_parser()
        flags, remaining = parser.parse(reason)
        reason = flags["reason"] or remaining or "No reason provided"

        target_member, user_id = resolve_user_id(user, ctx.guild)

        if not user_id:
            await ctx.send(embed=error(f"Could not find user: {user}"))
            return

        if not target_member:
            await ctx.send(
                embed=error("That user is not in this server. Use `/ban` to ban users not in the server.")
            )
            return

        can_do, error_msg = can_moderate(ctx.author, target_member, "kick")
        if not can_do:
            await ctx.send(embed=error(error_msg))
            return

        if not flags["silent"]:
            await dm_user(target_member, ctx.guild, "kick", reason)
        await target_member.kick(reason=reason)
        case_num = db.log_action(
            ctx.guild.id,
            {
                "action": "kick",
                "user_id": str(target_member.id),
                "moderator_id": str(ctx.author.id),
                "reason": reason,
                "timestamp": datetime.utcnow().isoformat(),
            },
        )

        response = f"**Case #{case_num}:** {target_member.mention} has been kicked. Reason: {reason}"
        if flags["silent"]:
            response += " (silent)"
        await ctx.send(embed=success(response))

        mod_embed = discord.Embed(title=f"Case #{case_num} | Kick", color=discord.Color.orange())
        mod_embed.add_field(
            name="User", value=f"{target_member.mention} ({target_member.id})", inline=True
        )
        mod_embed.add_field(name="Moderator", value=f"{ctx.author.mention}", inline=True)
        mod_embed.add_field(name="Reason", value=reason, inline=False)
        if flags["silent"]:
            mod_embed.set_footer(text="DM not sent (silent)")
        mod_embed.timestamp = datetime.utcnow()
        await send_mod_log(ctx.guild, mod_embed)

    @commands.hybrid_command(name="ban", description="Ban a user from the server")
    @app_commands.describe(
        user="User to ban (mention, username, or ID)", reason="Reason for ban"
    )
    async def ban(self, ctx: commands.Context, user: str, *, reason: str = "No reason provided"):
        """Ban a user. Flags: --silent/-s, --reason/-r, --delete/-d <1d/7d>, --duration/-t <time> (tempban)"""
        if not is_module_enabled(ctx.guild.id, "moderation"):
            await ctx.send(embed=error("The moderation module is disabled on this server."))
            return
        if not has_admin_role(ctx.author):
            await ctx.send(embed=error("You don't have permission to use this command."))
            return

        parser = ban_flag_parser()
        flags, remaining = parser.parse(reason)
        reason = flags["reason"] or remaining or "No reason provided"

        # Parse delete days from --delete flag
        delete_days = 0
        if flags["delete"]:
            dur = parse_duration(flags["delete"])
            if dur:
                delete_days = min(7, max(0, int(dur[0].total_seconds() / 86400)))

        # Parse tempban duration
        tempban_duration = None
        tempban_text = None
        if flags["duration"]:
            dur = parse_duration(flags["duration"])
            if dur:
                tempban_duration, tempban_text = dur

        target_member, user_id = resolve_user_id(user, ctx.guild)

        if not user_id:
            await ctx.send(embed=error(f"Could not find user: {user}"))
            return

        if target_member:
            can_do, error_msg = can_moderate(ctx.author, target_member, "ban")
            if not can_do:
                await ctx.send(embed=error(error_msg))
                return
            if not flags["silent"]:
                duration_text = tempban_text if tempban_text else None
                await dm_user(target_member, ctx.guild, "ban", reason, duration_text)
            await target_member.ban(reason=reason, delete_message_days=delete_days)
            user_display = f"{target_member.mention} ({target_member.id})"
        else:
            try:
                target_user = await self.bot.fetch_user(user_id)
                user_display = f"{target_user.mention} ({target_user.id})"
            except:
                user_display = f"<@{user_id}> ({user_id})"

            try:
                await ctx.guild.ban(discord.Object(id=user_id), reason=reason, delete_message_days=delete_days)
            except discord.NotFound:
                await ctx.send(embed=error(f"Could not find user with ID: {user_id}"))
                return
            except discord.Forbidden:
                await ctx.send(embed=error("I don't have permission to ban this user."))
                return

        case_num = db.log_action(
            ctx.guild.id,
            {
                "action": "tempban" if tempban_duration else "ban",
                "user_id": str(user_id),
                "moderator_id": str(ctx.author.id),
                "reason": reason,
                "duration": tempban_text,
                "timestamp": datetime.utcnow().isoformat(),
            },
        )

        response = f"**Case #{case_num}:** <@{user_id}> has been banned. Reason: {reason}"
        if tempban_text:
            response = f"**Case #{case_num}:** <@{user_id}> has been temp-banned for {tempban_text}. Reason: {reason}"
        if flags["silent"]:
            response += " (silent)"
        if delete_days:
            response += f" ({delete_days}d of messages deleted)"
        await ctx.send(embed=success(response))

        embed = discord.Embed(title=f"Case #{case_num} | {"Temp-Ban" if tempban_duration else "Ban"}", color=discord.Color.red())
        embed.add_field(name="User", value=user_display, inline=True)
        embed.add_field(name="Moderator", value=f"{ctx.author.mention}", inline=True)
        embed.add_field(name="Reason", value=reason, inline=False)
        if tempban_text:
            embed.add_field(name="Duration", value=tempban_text, inline=True)
        if delete_days:
            embed.add_field(name="Messages Deleted", value=f"{delete_days} day(s)", inline=True)
        if flags["silent"]:
            embed.set_footer(text="DM not sent (silent)")
        embed.timestamp = datetime.utcnow()
        await send_mod_log(ctx.guild, embed)

        # Store tempban in database for persistence across restarts
        if tempban_duration:
            expires_at = (datetime.utcnow() + tempban_duration).isoformat()
            db.add_tempban(ctx.guild.id, user_id, ctx.author.id, reason, expires_at)

    @commands.hybrid_command(name="timeout", description="Timeout a user")
    @app_commands.describe(
        user="User to timeout (mention, username, or ID)",
        duration="Duration (e.g. 1s, 5m, 2h, 1d)",
        reason="Reason for timeout",
    )
    async def timeout(
        self,
        ctx: commands.Context,
        user: str,
        duration: str,
        *,
        reason: str = "No reason provided",
    ):
        if not is_module_enabled(ctx.guild.id, "moderation"):
            await ctx.send(embed=error("The moderation module is disabled on this server."))
            return
        if not has_mod_role(ctx.author):
            await ctx.send(embed=error("You don't have permission to use this command."))
            return

        # Resolve user - can be member, ID, or mention
        target_member = None
        user_id = None

        # Try to extract ID from mention <@123> or <@!123>
        mention_match = re.search(r"<@!?(\d{17,20})>", user)
        if mention_match:
            user_id = int(mention_match.group(1))
        # Check if it's a raw ID
        elif user.isdigit() and len(user) >= 17:
            user_id = int(user)
        else:
            # Try to find member by name
            target_member = discord.utils.find(
                lambda m: m.name.lower() == user.lower()
                or (m.nick and m.nick.lower() == user.lower())
                or str(m).lower() == user.lower(),
                ctx.guild.members,
            )
            if target_member:
                user_id = target_member.id

        if not user_id:
            await ctx.send(embed=error(f"Could not find user: {user}"))
            return

        # Try to get member from guild
        if not target_member:
            target_member = ctx.guild.get_member(user_id)

        # Timeout requires member to be in server
        if not target_member:
            await ctx.send(
                embed=error("That user is not in this server. Timeout only works for members in the server.")
            )
            return

        can_do, error_msg = can_moderate(ctx.author, target_member, "timeout")
        if not can_do:
            await ctx.send(embed=error(error_msg))
            return

        parsed = parse_duration(duration)
        if not parsed:
            await ctx.send(embed=error("Invalid duration. Use formats like: `10s`, `5m`, `2h`, `1d`"))
            return

        duration_td, duration_str = parsed
        await dm_user(target_member, ctx.guild, "timeout", reason, duration_str)
        await target_member.timeout(duration_td, reason=reason)
        case_num = db.log_action(
            ctx.guild.id,
            {
                "action": "timeout",
                "user_id": str(target_member.id),
                "moderator_id": str(ctx.author.id),
                "reason": reason,
                "duration": duration_str,
                "timestamp": datetime.utcnow().isoformat(),
            },
        )
        await ctx.send(
            embed=success(f"**Case #{case_num}:** {target_member.mention} has been timed out for {duration_str}. Reason: {reason}")
        )

        embed = discord.Embed(title=f"Case #{case_num} | Timeout", color=discord.Color.blue())
        embed.add_field(
            name="User", value=f"{target_member.mention} ({target_member.id})", inline=True
        )
        embed.add_field(name="Moderator", value=f"{ctx.author.mention}", inline=True)
        embed.add_field(name="Duration", value=duration_str, inline=True)
        embed.add_field(name="Reason", value=reason, inline=False)
        embed.timestamp = datetime.utcnow()
        await send_mod_log(ctx.guild, embed)

    @commands.hybrid_command(name="void", description="Void/delete a case from the logs")
    @app_commands.describe(case="Case number to void")
    async def void(self, ctx: commands.Context, case: int):
        if not is_module_enabled(ctx.guild.id, "moderation"):
            await ctx.send(embed=error("The moderation module is disabled on this server."))
            return
        if not has_admin_role(ctx.author):
            await ctx.send(embed=error("You don't have permission to use this command."))
            return
        deleted = db.void_case(ctx.guild.id, case)
        if deleted:
            await ctx.send(embed=success(f"**Case #{case}** has been voided."))
        else:
            await ctx.send(embed=error(f"Case #{case} not found."))

    @commands.hybrid_command(name="case", description="View details of a specific case")
    @app_commands.describe(case_num="Case number to view")
    async def case_cmd(self, ctx: commands.Context, case_num: int):
        """View details of a specific moderation case."""
        if not is_module_enabled(ctx.guild.id, "moderation"):
            await ctx.send(embed=error("The moderation module is disabled on this server."))
            return
        if not has_mod_role(ctx.author):
            await ctx.send(embed=error("You don't have permission to use this command."))
            return

        case_data = db.get_case(ctx.guild.id, case_num)
        if not case_data:
            await ctx.send(embed=error(f"Case #{case_num} not found."))
            return

        # Get user and moderator info
        user_id = case_data.get("user_id")
        mod_id = case_data.get("moderator_id")

        try:
            user = await self.bot.fetch_user(int(user_id)) if user_id else None
        except:
            user = None

        try:
            moderator = await self.bot.fetch_user(int(mod_id)) if mod_id else None
        except:
            moderator = None

        action = case_data.get("action", "unknown")
        embed = discord.Embed(
            title=f"Case #{case_num}",
            color=discord.Color.blue(),
            timestamp=(
                datetime.fromisoformat(case_data["timestamp"])
                if case_data.get("timestamp")
                else None
            ),
        )

        embed.add_field(name="Action", value=action.title(), inline=True)
        embed.add_field(
            name="User",
            value=f"{user.mention if user else 'Unknown'} (`{user_id}`)",
            inline=True,
        )
        embed.add_field(
            name="Moderator",
            value=f"{moderator.mention if moderator else 'Unknown'} (`{mod_id}`)",
            inline=True,
        )
        embed.add_field(
            name="Reason", value=case_data.get("reason", "No reason provided"), inline=False
        )

        if case_data.get("duration"):
            embed.add_field(name="Duration", value=case_data["duration"], inline=True)

        await ctx.send(embed=embed)

    @commands.hybrid_command(name="modlogs", description="View recent moderation logs")
    @app_commands.describe(
        user="User to filter logs for (ID, mention, or username)",
        limit="Number of logs to show",
    )
    async def modlogs(self, ctx: commands.Context, user: str = None, limit: int = 10):
        if not is_module_enabled(ctx.guild.id, "moderation"):
            await ctx.send(embed=error("The moderation module is disabled on this server."))
            return
        if not has_mod_role(ctx.author):
            await ctx.send(embed=error("You don't have permission to use this command."))
            return

        # Parse user input - can be ID, mention, or username
        user_id = None
        user_display = None
        if user:
            # Try to extract ID from mention <@123> or <@!123>
            mention_match = re.search(r"<@!?(\d{17,20})>", user)
            if mention_match:
                user_id = mention_match.group(1)
            # Check if it's a raw ID
            elif user.isdigit() and len(user) >= 17:
                user_id = user
            else:
                # Try to find member by name
                member = discord.utils.find(
                    lambda m: m.name.lower() == user.lower()
                    or (m.nick and m.nick.lower() == user.lower())
                    or str(m).lower() == user.lower(),
                    ctx.guild.members,
                )
                if member:
                    user_id = str(member.id)
                    user_display = member.display_name
                else:
                    # Just use the input as-is for searching logs
                    user_id = user

            if not user_display:
                member = (
                    ctx.guild.get_member(int(user_id))
                    if user_id and user_id.isdigit()
                    else None
                )
                user_display = member.display_name if member else f"User {user_id}"

        logs = db.get_mod_logs(ctx.guild.id, min(limit, 25))

        if user_id:
            logs = [log for log in logs if log.get("user_id") == str(user_id)]

        if not logs:
            if user:
                await ctx.send(embed=info(f"No moderation logs found for {user_display or user}."))
            else:
                await ctx.send(embed=info("No moderation logs found."))
            return

        title = f"Moderation Logs for {user_display}" if user else "Recent Moderation Logs"

        # Color based on most severe action
        action_colors = {
            "ban": discord.Color.red(),
            "softban": discord.Color.red(),
            "kick": discord.Color.orange(),
            "timeout": discord.Color.blue(),
            "mute": discord.Color.blue(),
            "warn": discord.Color.yellow(),
            "unban": discord.Color.green(),
            "unmute": discord.Color.green(),
        }

        embed = discord.Embed(title=title, color=discord.Color.blue())
        for log in logs:
            action = log.get("action", "unknown").lower()
            case_str = f"Case #{log['case']} | " if "case" in log else ""
            duration_str = f"\nDuration: {log['duration']}" if log.get("duration") else ""

            # Convert ISO timestamp to Unix
            ts = int(datetime.fromisoformat(log["timestamp"]).timestamp())
            embed.add_field(
                name=f"{case_str}{action.upper()} - <t:{ts}:f>",
                value=f"**User:** <@{log['user_id']}>\n**Mod:** <@{log['moderator_id']}>\n**Reason:** {log['reason']}{duration_str}",
                inline=False,
            )

        embed.set_footer(text=f"Showing {len(logs)} log(s)")
        await ctx.send(embed=embed)

    @commands.hybrid_command(
        name="modstats", description="View moderation statistics for a moderator"
    )
    @app_commands.describe(moderator="Moderator to view stats for (defaults to yourself)")
    async def modstats(self, ctx: commands.Context, moderator: discord.Member = None):
        if not is_module_enabled(ctx.guild.id, "moderation"):
            await ctx.send(embed=error("The moderation module is disabled on this server."))
            return
        if not has_mod_role(ctx.author):
            await ctx.send(embed=error("You don't have permission to use this command."))
            return

        target = moderator or ctx.author
        stats = db.get_mod_stats(ctx.guild.id, target.id)

        embed = discord.Embed(
            title=f"Moderation Stats for {target.display_name}",
            color=discord.Color.blue(),
            timestamp=datetime.utcnow(),
        )
        embed.set_thumbnail(url=target.display_avatar.url)
        embed.add_field(name="Total Actions", value=str(stats["total"]), inline=False)
        embed.add_field(name="Warns", value=str(stats["warn"]), inline=True)
        embed.add_field(name="Kicks", value=str(stats["kick"]), inline=True)
        embed.add_field(name="Bans", value=str(stats["ban"]), inline=True)
        embed.add_field(name="Timeouts", value=str(stats["timeout"]), inline=True)
        embed.add_field(name="Mutes", value=str(stats["mute"]), inline=True)
        embed.add_field(name="Softbans", value=str(stats["softban"]), inline=True)
        embed.add_field(name="Unbans", value=str(stats["unban"]), inline=True)

        await ctx.send(embed=embed)

    @commands.hybrid_command(name="lb", description="View the moderation leaderboard")
    async def lb(self, ctx: commands.Context):
        if not is_module_enabled(ctx.guild.id, "moderation"):
            await ctx.send(embed=error("The moderation module is disabled on this server."))
            return
        if not has_mod_role(ctx.author):
            await ctx.send(embed=error("You don't have permission to use this command."))
            return

        leaderboard = db.get_mod_leaderboard(ctx.guild.id)

        if not leaderboard:
            await ctx.send(embed=info("No moderation actions recorded yet."))
            return

        view = LeaderboardView(leaderboard, ctx.guild)
        await ctx.send(embed=view.get_embed(), view=view)

    @commands.hybrid_command(
        name="transferlogs", description="Transfer mod logs from one user to another"
    )
    @app_commands.describe(
        from_user="User to transfer logs from", to_user="User to transfer logs to"
    )
    async def transferlogs(
        self, ctx: commands.Context, from_user: discord.Member, to_user: discord.Member
    ):
        if not is_module_enabled(ctx.guild.id, "moderation"):
            await ctx.send(embed=error("The moderation module is disabled on this server."))
            return
        if not has_admin_role(ctx.author):
            await ctx.send(embed=error("You need admin permissions to use this command."))
            return

        if from_user.id == to_user.id:
            await ctx.send(embed=warning("Cannot transfer logs to the same user."))
            return

        count = db.transfer_mod_logs(ctx.guild.id, from_user.id, to_user.id)

        if count == 0:
            await ctx.send(embed=info(f"No mod logs found for {from_user.mention}."))
            return

        embed = discord.Embed(
            title="Mod Logs Transferred",
            color=discord.Color.green(),
            timestamp=datetime.utcnow(),
        )
        embed.add_field(name="From", value=from_user.mention, inline=True)
        embed.add_field(name="To", value=to_user.mention, inline=True)
        embed.add_field(name="Logs Transferred", value=str(count), inline=False)
        embed.set_footer(text=f"Transferred by {ctx.author}")

        await ctx.send(embed=embed)
        await send_mod_log(ctx.guild, embed)

    @commands.hybrid_command(
        name="transferstats", description="Transfer mod stats (same as transferlogs)"
    )
    @app_commands.describe(
        from_user="User to transfer stats from", to_user="User to transfer stats to"
    )
    async def transferstats(
        self, ctx: commands.Context, from_user: discord.Member, to_user: discord.Member
    ):
        if not is_module_enabled(ctx.guild.id, "moderation"):
            await ctx.send(embed=error("The moderation module is disabled on this server."))
            return
        if not has_admin_role(ctx.author):
            await ctx.send(embed=error("You need admin permissions to use this command."))
            return

        if from_user.id == to_user.id:
            await ctx.send(embed=warning("Cannot transfer stats to the same user."))
            return

        # Get stats before transfer to show what was transferred
        old_stats = db.get_mod_stats(ctx.guild.id, from_user.id)

        if old_stats["total"] == 0:
            await ctx.send(embed=info(f"No mod stats found for {from_user.mention}."))
            return

        count = db.transfer_mod_logs(ctx.guild.id, from_user.id, to_user.id)

        embed = discord.Embed(
            title="Mod Stats Transferred",
            color=discord.Color.green(),
            timestamp=datetime.utcnow(),
        )
        embed.add_field(name="From", value=from_user.mention, inline=True)
        embed.add_field(name="To", value=to_user.mention, inline=True)
        embed.add_field(name="Total Actions Transferred", value=str(count), inline=False)

        stats_summary = []
        if old_stats["warn"] > 0:
            stats_summary.append(f"{old_stats['warn']} warns")
        if old_stats["kick"] > 0:
            stats_summary.append(f"{old_stats['kick']} kicks")
        if old_stats["ban"] > 0:
            stats_summary.append(f"{old_stats['ban']} bans")
        if old_stats["timeout"] > 0:
            stats_summary.append(f"{old_stats['timeout']} timeouts")
        if old_stats["mute"] > 0:
            stats_summary.append(f"{old_stats['mute']} mutes")

        if stats_summary:
            embed.add_field(name="Breakdown", value=", ".join(stats_summary), inline=False)

        embed.set_footer(text=f"Transferred by {ctx.author}")

        await ctx.send(embed=embed)
        await send_mod_log(ctx.guild, embed)

    @commands.hybrid_command(name="purge", description="Delete multiple messages")
    @app_commands.describe(
        amount="Number of messages to delete (1-100)",
        flags="Filters: --user @user, --contains text, --bots, --embeds, --images, --links",
    )
    async def purge(self, ctx: commands.Context, amount: int, *, flags: str = ""):
        if not is_module_enabled(ctx.guild.id, "moderation"):
            await ctx.send(embed=error("The moderation module is disabled on this server."))
            return
        if not has_mod_role(ctx.author):
            await ctx.send(embed=error("You don't have permission to use this command."))
            return

        if amount < 1 or amount > 100:
            await ctx.send(embed=error("Amount must be between 1 and 100."))
            return

        parser = purge_flag_parser()
        parsed, _ = parser.parse(flags)

        # Resolve user filter
        filter_user = None
        if parsed["user"]:
            member, uid = resolve_user_id(parsed["user"], ctx.guild)
            if member:
                filter_user = member
            elif uid:
                filter_user = uid

        await ctx.defer(ephemeral=True)

        def check(msg):
            if msg.id == ctx.message.id:
                return True
            if filter_user:
                target_id = filter_user.id if isinstance(filter_user, discord.Member) else filter_user
                if msg.author.id != target_id:
                    return False
            if parsed["bots"] and not msg.author.bot:
                return False
            if parsed["contains"] and parsed["contains"].lower() not in msg.content.lower():
                return False
            if parsed["embeds"] and not msg.embeds:
                return False
            if parsed["images"] and not msg.attachments:
                return False
            if parsed["links"] and not re.search(r"https?://\S+", msg.content):
                return False
            return True

        deleted = await ctx.channel.purge(limit=amount + 1, check=check)

        filters_used = []
        if filter_user:
            name = filter_user.mention if isinstance(filter_user, discord.Member) else f"<@{filter_user}>"
            filters_used.append(f"user: {name}")
        if parsed["bots"]:
            filters_used.append("bots only")
        if parsed["contains"]:
            filters_used.append(f"containing: \"{parsed['contains']}\"")
        if parsed["embeds"]:
            filters_used.append("with embeds")
        if parsed["images"]:
            filters_used.append("with images")
        if parsed["links"]:
            filters_used.append("with links")

        response = f"Deleted {len(deleted) - 1} messages."
        if filters_used:
            response += f" ({', '.join(filters_used)})"

        await ctx.send(embed=success(response), ephemeral=True)

    @commands.hybrid_command(name="slowmode", description="Set channel slowmode")
    @app_commands.describe(seconds="Slowmode delay in seconds (0 to disable)")
    async def slowmode(self, ctx: commands.Context, seconds: int):
        if not is_module_enabled(ctx.guild.id, "moderation"):
            await ctx.send(embed=error("The moderation module is disabled on this server."))
            return
        if not has_mod_role(ctx.author):
            await ctx.send(embed=error("You don't have permission to use this command."))
            return

        if seconds < 0 or seconds > 21600:
            await ctx.send(embed=error("Slowmode must be between 0 and 21600 seconds (6 hours)."))
            return

        await ctx.channel.edit(slowmode_delay=seconds)

        if seconds == 0:
            await ctx.send(embed=success("Slowmode disabled."))
        else:
            await ctx.send(embed=success(f"Slowmode set to {seconds} seconds."))

    @commands.hybrid_command(name="lock", description="Lock a channel")
    @app_commands.describe(channel="Channel to lock (defaults to current)")
    async def lock(self, ctx: commands.Context, channel: discord.TextChannel = None):
        if not is_module_enabled(ctx.guild.id, "moderation"):
            await ctx.send(embed=error("The moderation module is disabled on this server."))
            return
        if not has_mod_role(ctx.author):
            await ctx.send(embed=error("You don't have permission to use this command."))
            return

        channel = channel or ctx.channel
        await channel.set_permissions(ctx.guild.default_role, send_messages=False)
        await ctx.send(embed=success(f"{channel.mention} has been locked."))

    @commands.hybrid_command(name="unlock", description="Unlock a channel")
    @app_commands.describe(channel="Channel to unlock (defaults to current)")
    async def unlock(self, ctx: commands.Context, channel: discord.TextChannel = None):
        if not is_module_enabled(ctx.guild.id, "moderation"):
            await ctx.send(embed=error("The moderation module is disabled on this server."))
            return
        if not has_mod_role(ctx.author):
            await ctx.send(embed=error("You don't have permission to use this command."))
            return

        channel = channel or ctx.channel
        await channel.set_permissions(ctx.guild.default_role, send_messages=None)
        await ctx.send(embed=success(f"{channel.mention} has been unlocked."))

    @commands.hybrid_command(
        name="softban",
        description="Ban and immediately unban a user (clears their messages)",
    )
    @app_commands.describe(user="User to softban", reason="Reason for softban")
    async def softban(
        self, ctx: commands.Context, user: discord.Member, *, reason: str = "No reason provided"
    ):
        if not is_module_enabled(ctx.guild.id, "moderation"):
            await ctx.send(embed=error("The moderation module is disabled on this server."))
            return
        if not has_mod_role(ctx.author):
            await ctx.send(embed=error("You don't have permission to use this command."))
            return

        can_do, error_msg = can_moderate(ctx.author, user, "softban")
        if not can_do:
            await ctx.send(embed=error(error_msg))
            return

        # DM before softban (they won't be in server during ban)
        await dm_user(user, ctx.guild, "softban", reason)
        await ctx.guild.ban(user, reason=f"Softban: {reason}", delete_message_days=7)
        await ctx.guild.unban(user, reason="Softban unban")

        case_num = db.log_action(
            ctx.guild.id,
            {
                "action": "softban",
                "user_id": str(user.id),
                "moderator_id": str(ctx.author.id),
                "reason": reason,
                "timestamp": datetime.utcnow().isoformat(),
            },
        )

        await ctx.send(embed=success(f"**Case #{case_num}:** {user.mention} has been softbanned. Reason: {reason}"))

        softban_embed = discord.Embed(title=f"Case #{case_num} | Softban", color=discord.Color.orange())
        softban_embed.add_field(name="User", value=f"{user.mention} ({user.id})", inline=True)
        softban_embed.add_field(name="Moderator", value=f"{ctx.author.mention}", inline=True)
        softban_embed.add_field(name="Reason", value=reason, inline=False)
        softban_embed.timestamp = datetime.utcnow()
        await send_mod_log(ctx.guild, softban_embed)

    @commands.hybrid_command(name="unban", description="Unban a user")
    @app_commands.describe(user_id="User ID to unban", reason="Reason for unban")
    async def unban(
        self, ctx: commands.Context, user_id: str, *, reason: str = "No reason provided"
    ):
        if not is_module_enabled(ctx.guild.id, "moderation"):
            await ctx.send(embed=error("The moderation module is disabled on this server."))
            return
        if not has_admin_role(ctx.author):
            await ctx.send(embed=error("You don't have permission to use this command."))
            return

        try:
            user = await self.bot.fetch_user(int(user_id))
            await ctx.guild.unban(user, reason=reason)

            case_num = db.log_action(
                ctx.guild.id,
                {
                    "action": "unban",
                    "user_id": str(user.id),
                    "moderator_id": str(ctx.author.id),
                    "reason": reason,
                    "timestamp": datetime.utcnow().isoformat(),
                },
            )

            await ctx.send(embed=success(f"**Case #{case_num}:** {user.name} ({user.id}) has been unbanned. Reason: {reason}"))

            embed = discord.Embed(title=f"Case #{case_num} | Unban", color=discord.Color.green())
            embed.add_field(name="User", value=f"{user.mention} ({user.id})", inline=True)
            embed.add_field(name="Moderator", value=f"{ctx.author.mention}", inline=True)
            embed.add_field(name="Reason", value=reason, inline=False)
            embed.timestamp = datetime.utcnow()
            await send_mod_log(ctx.guild, embed)
        except discord.NotFound:
            await ctx.send(embed=error("User not found or not banned."))
        except ValueError:
            await ctx.send(embed=error("Invalid user ID."))

    @commands.hybrid_command(name="clearwarnings", description="Clear all warnings for a user")
    @app_commands.describe(user="User to clear warnings for")
    async def clearwarnings(self, ctx: commands.Context, user: discord.Member):
        if not is_module_enabled(ctx.guild.id, "moderation"):
            await ctx.send(embed=error("The moderation module is disabled on this server."))
            return
        if not has_admin_role(ctx.author):
            await ctx.send(embed=error("You don't have permission to use this command."))
            return

        count = db.clear_warnings(ctx.guild.id, user.id)
        await ctx.send(embed=success(f"Cleared {count} warning(s) for {user.mention}."))

    @commands.hybrid_command(name="reason", description="Edit the reason for a case")
    @app_commands.describe(case="Case number to edit", new_reason="New reason for the case")
    async def reason(self, ctx: commands.Context, case: int, *, new_reason: str):
        if not is_module_enabled(ctx.guild.id, "moderation"):
            await ctx.send(embed=error("The moderation module is disabled on this server."))
            return
        if not has_mod_role(ctx.author):
            await ctx.send(embed=error("You don't have permission to use this command."))
            return

        updated = db.update_case_reason(ctx.guild.id, case, new_reason)
        if updated:
            await ctx.send(embed=success(f"Updated reason for **Case #{case}**."))
        else:
            await ctx.send(embed=error(f"Case #{case} not found."))

    @commands.hybrid_command(name="mute", description="Mute a user (timeout)")
    @app_commands.describe(
        user="User to mute",
        duration="Duration (e.g. 1s, 5m, 2h, 1d)",
        reason="Reason for mute",
    )
    async def mute(
        self,
        ctx: commands.Context,
        user: discord.Member,
        duration: str,
        *,
        reason: str = "No reason provided",
    ):
        if not is_module_enabled(ctx.guild.id, "moderation"):
            await ctx.send(embed=error("The moderation module is disabled on this server."))
            return
        if not has_mod_role(ctx.author):
            await ctx.send(embed=error("You don't have permission to use this command."))
            return

        can_do, error_msg = can_moderate(ctx.author, user, "mute")
        if not can_do:
            await ctx.send(embed=error(error_msg))
            return

        parsed = parse_duration(duration)
        if not parsed:
            await ctx.send(embed=error("Invalid duration. Use formats like: `10s`, `5m`, `2h`, `1d`"))
            return

        duration_td, duration_str = parsed
        await dm_user(user, ctx.guild, "mute", reason, duration_str)
        try:
            await user.timeout(duration_td, reason=reason)
        except discord.Forbidden:
            await ctx.send(
                embed=error(f"I don't have permission to mute {user.mention}. Make sure my role is above theirs and I have the 'Moderate Members' permission.")
            )
            return
        case_num = db.log_action(
            ctx.guild.id,
            {
                "action": "mute",
                "user_id": str(user.id),
                "moderator_id": str(ctx.author.id),
                "reason": reason,
                "duration": duration_str,
                "timestamp": datetime.utcnow().isoformat(),
            },
        )
        await ctx.send(
            embed=success(f"**Case #{case_num}:** {user.mention} has been muted for {duration_str}. Reason: {reason}")
        )

        mute_embed = discord.Embed(title=f"Case #{case_num} | Mute", color=discord.Color.blue())
        mute_embed.add_field(name="User", value=f"{user.mention} ({user.id})", inline=True)
        mute_embed.add_field(name="Moderator", value=f"{ctx.author.mention}", inline=True)
        mute_embed.add_field(name="Duration", value=duration_str, inline=True)
        mute_embed.add_field(name="Reason", value=reason, inline=False)
        mute_embed.timestamp = datetime.utcnow()
        await send_mod_log(ctx.guild, mute_embed)

    @commands.hybrid_command(name="unmute", description="Unmute a user (remove timeout)")
    @app_commands.describe(user="User to unmute", reason="Reason for unmute")
    async def unmute(
        self, ctx: commands.Context, user: discord.Member, *, reason: str = "No reason provided"
    ):
        if not is_module_enabled(ctx.guild.id, "moderation"):
            await ctx.send(embed=error("The moderation module is disabled on this server."))
            return
        if not has_mod_role(ctx.author):
            await ctx.send(embed=error("You don't have permission to use this command."))
            return

        can_do, error_msg = can_moderate(ctx.author, user, "unmute")
        if not can_do:
            await ctx.send(embed=error(error_msg))
            return

        if not user.is_timed_out():
            await ctx.send(embed=info(f"{user.mention} is not muted."))
            return

        try:
            await user.timeout(None, reason=reason)
        except discord.Forbidden:
            await ctx.send(
                embed=error(f"I don't have permission to unmute {user.mention}. Make sure my role is above theirs and I have the 'Moderate Members' permission.")
            )
            return
        case_num = db.log_action(
            ctx.guild.id,
            {
                "action": "unmute",
                "user_id": str(user.id),
                "moderator_id": str(ctx.author.id),
                "reason": reason,
                "timestamp": datetime.utcnow().isoformat(),
            },
        )
        await ctx.send(embed=success(f"**Case #{case_num}:** {user.mention} has been unmuted. Reason: {reason}"))

        unmute_embed = discord.Embed(title=f"Case #{case_num} | Unmute", color=discord.Color.green())
        unmute_embed.add_field(name="User", value=f"{user.mention} ({user.id})", inline=True)
        unmute_embed.add_field(name="Moderator", value=f"{ctx.author.mention}", inline=True)
        unmute_embed.add_field(name="Reason", value=reason, inline=False)
        unmute_embed.timestamp = datetime.utcnow()
        await send_mod_log(ctx.guild, unmute_embed)

    @commands.hybrid_command(name="nick", description="Change a user's nickname")
    @app_commands.describe(
        user="User to change nickname for", nickname="New nickname (leave empty to reset)"
    )
    async def nick(self, ctx: commands.Context, user: discord.Member, *, nickname: str = None):
        if not is_module_enabled(ctx.guild.id, "moderation"):
            await ctx.send(embed=error("The moderation module is disabled on this server."))
            return
        if not has_mod_role(ctx.author):
            await ctx.send(embed=error("You don't have permission to use this command."))
            return

        can_do, error_msg = can_moderate(ctx.author, user, "change nickname for")
        if not can_do:
            await ctx.send(embed=error(error_msg))
            return

        old_nick = user.display_name
        try:
            await user.edit(nick=nickname)
            if nickname:
                await ctx.send(
                    embed=success(f"Changed {user.mention}'s nickname from `{old_nick}` to `{nickname}`")
                )
            else:
                await ctx.send(embed=success(f"Reset {user.mention}'s nickname (was `{old_nick}`)"))
        except discord.Forbidden:
            await ctx.send(embed=error("I don't have permission to change that user's nickname."))

    @commands.hybrid_command(name="role", description="Add or remove a role from a user")
    @app_commands.describe(user="User to modify", role="Role to add/remove")
    async def role(self, ctx: commands.Context, user: discord.Member, *, role: str):
        is_embedded = getattr(ctx, "_embedded_command", False)

        if not is_module_enabled(ctx.guild.id, "moderation"):
            await ctx.send(embed=error("The moderation module is disabled on this server."))
            return
        # Skip permission check for embedded commands (custom commands)
        if not is_embedded and not has_mod_role(ctx.author):
            await ctx.send(embed=error("You don't have permission to use this command."))
            return

        # Find the role with fuzzy matching
        found_role = find_role(ctx.guild, role)
        if not found_role:
            await ctx.send(embed=error(f"Could not find a role matching `{role}`"))
            return

        # Check if bot can manage this role
        if found_role >= ctx.guild.me.top_role:
            await ctx.send(
                embed=error("I can't manage that role (it's higher than or equal to my highest role).")
            )
            return

        # Skip user hierarchy check for embedded commands
        if not is_embedded:
            if found_role >= ctx.author.top_role and ctx.author != ctx.guild.owner:
                await ctx.send(
                    embed=error("You can't manage that role (it's higher than or equal to your highest role).")
                )
                return

        try:
            if found_role in user.roles:
                await user.remove_roles(found_role)
                embed = discord.Embed(
                    description=f"Removed {found_role.mention} from {user.mention}",
                    color=discord.Color.red(),
                )
                await ctx.send(embed=embed)
            else:
                await user.add_roles(found_role)
                embed = discord.Embed(
                    description=f"Added {found_role.mention} to {user.mention}",
                    color=discord.Color.green(),
                )
                await ctx.send(embed=embed)
        except discord.Forbidden:
            await ctx.send(embed=error("I don't have permission to manage that role."))

    @commands.hybrid_command(
        name="clearmodlogs", description="Clear all mod logs for this server (Dev only)"
    )
    async def clearmodlogs(self, ctx: commands.Context):
        """Clear all moderation logs for this server. Dev only."""
        _, is_dev = await is_staff(self.bot, ctx.author.id)
        if not is_dev:
            await ctx.send(embed=error("This command is restricted to developers."))
            return

        # Confirmation message
        confirm_embed = discord.Embed(
            title="Clear All Mod Logs",
            description=f"Are you sure you want to delete **all** moderation logs for **{ctx.guild.name}**?\n\nThis action cannot be undone!",
            color=discord.Color.red(),
        )
        confirm_msg = await ctx.send(embed=confirm_embed)
        await confirm_msg.add_reaction("\u2705")
        await confirm_msg.add_reaction("\u274c")

        def check(reaction, user):
            return (
                user == ctx.author
                and str(reaction.emoji) in ["\u2705", "\u274c"]
                and reaction.message.id == confirm_msg.id
            )

        try:
            reaction, _ = await self.bot.wait_for("reaction_add", timeout=30.0, check=check)
            if str(reaction.emoji) == "\u2705":
                deleted = db.clear_all_mod_logs(ctx.guild.id)
                result_embed = discord.Embed(
                    title="Mod Logs Cleared",
                    description=f"Deleted **{deleted}** mod log entries and reset case counter.",
                    color=discord.Color.green(),
                )
                await confirm_msg.edit(embed=result_embed)
            else:
                cancel_embed = discord.Embed(
                    title="Cancelled",
                    description="Mod logs were not cleared.",
                    color=discord.Color.grey(),
                )
                await confirm_msg.edit(embed=cancel_embed)
            await confirm_msg.clear_reactions()
        except asyncio.TimeoutError:
            timeout_embed = discord.Embed(
                title="Timed Out",
                description="No response received. Mod logs were not cleared.",
                color=discord.Color.grey(),
            )
            await confirm_msg.edit(embed=timeout_embed)
            await confirm_msg.clear_reactions()


    @commands.hybrid_command(name="clean", description="Remove all of a user's messages across the server")
    @app_commands.describe(
        user="User whose messages to remove",
        timeframe="How far back to look (e.g. 1h, 6h, 1d, 3d). Max 14d.",
    )
    async def clean(self, ctx, user: discord.Member, timeframe: str = "1h"):
        if not is_module_enabled(ctx.guild.id, "moderation"):
            return await ctx.send(embed=error("The moderation module is disabled on this server."))
        if not has_mod_role(ctx.author):
            return await ctx.send(embed=error("No permission."))

        parsed = parse_duration(timeframe)
        if not parsed:
            return await ctx.send(embed=error("Invalid timeframe. Use `1h`, `6h`, `1d`, `3d`, etc."))

        delta, dur_text = parsed
        if delta.total_seconds() > 14 * 86400:
            return await ctx.send(embed=error("Max timeframe is 14 days (Discord limitation)."))

        after = datetime.now(timezone.utc) - delta
        await ctx.defer()

        total = 0
        failed = 0
        channels_cleaned = 0

        for channel in ctx.guild.text_channels:
            try:
                perms = channel.permissions_for(ctx.guild.me)
                if not perms.read_messages or not perms.manage_messages:
                    continue
                deleted = await channel.purge(
                    limit=500,
                    check=lambda m: m.author.id == user.id,
                    after=after,
                    reason=f"Clean by {ctx.author}",
                )
                if deleted:
                    total += len(deleted)
                    channels_cleaned += 1
            except (discord.Forbidden, discord.HTTPException):
                failed += 1

        result = f"Removed **{total}** messages from {user.mention} across **{channels_cleaned}** channels.\nTimeframe: last {dur_text}"
        if failed:
            result += f"\nSkipped {failed} channels (no permission)."

        await ctx.send(embed=success(result))

        await send_mod_log(ctx.guild, mod_embed("Messages Cleaned")
            .add_field(name="User", value=f"{user.mention} ({user.id})", inline=True)
            .add_field(name="Moderator", value=ctx.author.mention, inline=True)
            .add_field(name="Messages", value=str(total), inline=True)
            .add_field(name="Timeframe", value=dur_text, inline=True)
            .add_field(name="Channels", value=str(channels_cleaned), inline=True)
        )


def find_role(guild: discord.Guild, query: str) -> discord.Role:
    # Check if it's a mention
    match = re.match(r"<@&(\d+)>", query)
    if match:
        return guild.get_role(int(match.group(1)))
    # Check if it's an ID
    if query.isdigit():
        return guild.get_role(int(query))
    # Fuzzy match by name
    return fuzzy_match(query, guild.roles, lambda r: r.name)


def find_member(guild: discord.Guild, query: str) -> discord.Member:
    # Check if it's a mention
    match = re.match(r"<@!?(\d+)>", query)
    if match:
        return guild.get_member(int(match.group(1)))
    # Check if it's an ID
    if query.isdigit():
        return guild.get_member(int(query))
    # Fuzzy match by display name then username
    member = fuzzy_match(query, guild.members, lambda m: m.display_name)
    if not member:
        member = fuzzy_match(query, guild.members, lambda m: m.name)
    return member


async def setup(bot):
    await bot.add_cog(Moderation(bot))
