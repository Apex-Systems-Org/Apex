import discord
from discord.ext import commands
from discord import app_commands
from datetime import datetime
import re

from database import db
from helpers.embeds import success, error, warning, info
from helpers.utils import is_module_enabled


# Default invite tracking settings
DEFAULT_INVITES = {"enabled": True, "show_in_welcome": False}


def get_invite_settings(guild_id: int) -> dict:
    """Get invite tracking settings for a guild."""
    settings = db.get_guild_settings(guild_id)
    invite_settings = settings.get("invite_tracking", {})
    return {**DEFAULT_INVITES, **invite_settings}


async def cache_guild_invites(guild: discord.Guild):
    """Cache all invites for a guild."""
    invite_settings = get_invite_settings(guild.id)
    if not invite_settings["enabled"]:
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
        pass  # Bot doesn't have permission to view invites


class InvitesCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_invite_create(self, invite: discord.Invite):
        """Cache new invites."""
        invite_settings = get_invite_settings(invite.guild.id)
        if not invite_settings["enabled"]:
            return
        db.update_invite_cache(
            invite.guild.id,
            invite.code,
            invite.uses,
            str(invite.inviter.id) if invite.inviter else None,
        )

    @commands.Cog.listener()
    async def on_invite_delete(self, invite: discord.Invite):
        """Remove deleted invites from cache."""
        db.remove_invite_cache(invite.guild.id, invite.code)

    @commands.hybrid_command(name="invites", description="Check invite stats")
    @app_commands.describe(user="User to check invites for (optional)")
    async def invites_cmd(self, ctx: commands.Context, user: discord.Member = None):
        """Check your or another user's invite stats."""
        invite_settings = get_invite_settings(ctx.guild.id)
        if not invite_settings["enabled"]:
            await ctx.send(embed=error("Invite tracking is disabled on this server."))
            return

        target = user or ctx.author
        stats = db.get_invite_stats(ctx.guild.id, target.id)

        embed = discord.Embed(
            title=f"{target.display_name}'s Invites", color=discord.Color.blue()
        )
        embed.add_field(name="Total", value=stats["total"], inline=True)
        embed.add_field(name="Stayed", value=stats["current"], inline=True)
        embed.add_field(name="Left", value=stats["left"], inline=True)
        embed.set_thumbnail(url=target.display_avatar.url)

        await ctx.send(embed=embed)

    @commands.hybrid_command(name="inviteleaderboard", description="View invite leaderboard")
    @app_commands.describe(page="Page number")
    async def inviteleaderboard(self, ctx: commands.Context, page: int = 1):
        """View the server's top inviters."""
        invite_settings = get_invite_settings(ctx.guild.id)
        if not invite_settings["enabled"]:
            await ctx.send(embed=error("Invite tracking is disabled on this server."))
            return

        lb = db.get_invite_leaderboard(ctx.guild.id, limit=100)

        if not lb:
            await ctx.send(embed=info("No invites tracked yet!"))
            return

        per_page = 10
        pages = (len(lb) + per_page - 1) // per_page
        page = max(1, min(page, pages))
        start = (page - 1) * per_page
        end = start + per_page

        embed = discord.Embed(
            title=f"Invite Leaderboard - {ctx.guild.name}", color=discord.Color.blue()
        )

        description = []
        for i, entry in enumerate(lb[start:end], start=start + 1):
            member = ctx.guild.get_member(int(entry["inviter_id"]))
            name = member.display_name if member else f"User {entry['inviter_id']}"
            medal = f"**{i}.**"
            description.append(
                f"{medal} {name} - {entry['current']} invites ({entry['left_count']} left)"
            )

        embed.description = "\n".join(description)
        embed.set_footer(text=f"Page {page}/{pages}")

        await ctx.send(embed=embed)

    @commands.hybrid_command(name="whoinvited", description="Check who invited a user")
    @app_commands.describe(user="User to check")
    async def whoinvited(self, ctx: commands.Context, user: discord.Member):
        """Check who invited a user."""
        invite_settings = get_invite_settings(ctx.guild.id)
        if not invite_settings["enabled"]:
            await ctx.send(embed=error("Invite tracking is disabled on this server."))
            return

        data = db.get_user_inviter(ctx.guild.id, user.id)

        if not data or not data.get("inviter_id"):
            await ctx.send(
                embed=info(f"I don't have invite data for {user.mention}. They may have joined before invite tracking was enabled.")
            )
            return

        inviter = ctx.guild.get_member(int(data["inviter_id"]))
        inviter_name = inviter.mention if inviter else f"<@{data['inviter_id']}>"

        embed = discord.Embed(
            title=f"Who Invited {user.display_name}?",
            description=f"{user.mention} was invited by {inviter_name}",
            color=discord.Color.blue(),
        )
        if data.get("invite_code"):
            embed.add_field(name="Invite Code", value=data["invite_code"], inline=True)
        if data.get("joined_at"):
            embed.add_field(
                name="Joined",
                value=f"<t:{int(datetime.fromisoformat(data['joined_at']).timestamp())}:R>",
                inline=True,
            )

        await ctx.send(embed=embed)

    @commands.command(name="import-circle", hidden=True)
    @commands.is_owner()
    async def import_circle(
        self, ctx: commands.Context, channel: discord.TextChannel, limit: int = 500
    ):
        """Import punishment history from Circle bot. Owner only."""

        await ctx.defer()

        # Circle bot ID
        CIRCLE_BOT_ID = 497196352866877441

        imported = {
            "warn": 0,
            "ban": 0,
            "kick": 0,
            "mute": 0,
            "unmute": 0,
            "timeout": 0,
            "other": 0,
        }
        skipped = 0
        errors_count = 0
        circle_messages = 0
        debug_titles = []  # Track first few embed titles for debugging

        status_msg = await ctx.send(embed=info(f"Scanning {channel.mention} for Circle punishments..."))

        try:
            async for message in channel.history(limit=limit):
                # Only process Circle bot messages
                if message.author.id != CIRCLE_BOT_ID:
                    continue

                circle_messages += 1

                if not message.embeds:
                    skipped += 1
                    continue

                for embed in message.embeds:
                    try:
                        # Track embed titles for debugging
                        if len(debug_titles) < 5 and embed.title:
                            debug_titles.append(embed.title[:50])

                        # Parse the title: "Action | Case #123"
                        if not embed.title:
                            skipped += 1
                            continue

                        if "|" not in embed.title:
                            skipped += 1
                            continue

                        title_parts = embed.title.split("|")
                        if len(title_parts) != 2:
                            continue

                        action_raw = title_parts[0].strip().lower()
                        case_part = title_parts[1].strip()

                        # Extract case number
                        case_match = re.search(r"#(\d+)", case_part)
                        case_number = int(case_match.group(1)) if case_match else None

                        # Map Circle actions to Apex actions
                        action_map = {
                            "warn": "warn",
                            "warning": "warn",
                            "ban": "ban",
                            "kick": "kick",
                            "mute": "mute",
                            "timeout": "timeout",
                            "unmute": "unmute",
                            "manual unmute": "unmute",
                            "unban": "unban",
                            "softban": "softban",
                        }

                        action = None
                        for key, value in action_map.items():
                            if key in action_raw:
                                action = value
                                break

                        if not action:
                            action = "other"

                        # Parse fields
                        user_id = None
                        moderator_id = None
                        reason = "Imported from Circle"
                        duration = None

                        for field in embed.fields:
                            field_name = field.name.lower().strip()
                            field_value = field.value

                            if "member" in field_name:
                                # Extract user ID - handle various formats with spaces/newlines
                                # Try parentheses format: (123456789) or ( 123456789 )
                                id_match = re.search(r"\(\s*(\d{17,20})\s*\)", field_value)
                                if id_match:
                                    user_id = id_match.group(1)
                                else:
                                    # Try mention format: <@123456789>
                                    id_match = re.search(r"<@!?(\d{17,20})>", field_value)
                                    if id_match:
                                        user_id = id_match.group(1)
                                    else:
                                        # Try just finding any long number
                                        id_match = re.search(r"(\d{17,20})", field_value)
                                        if id_match:
                                            user_id = id_match.group(1)

                            elif "moderator" in field_name:
                                # Extract moderator ID
                                id_match = re.search(r"\(\s*(\d{17,20})\s*\)", field_value)
                                if id_match:
                                    moderator_id = id_match.group(1)
                                else:
                                    id_match = re.search(r"<@!?(\d{17,20})>", field_value)
                                    if id_match:
                                        moderator_id = id_match.group(1)
                                    else:
                                        id_match = re.search(r"(\d{17,20})", field_value)
                                        if id_match:
                                            moderator_id = id_match.group(1)

                            elif "reason" in field_name:
                                reason = (
                                    field_value[:500]
                                    if field_value
                                    else "No reason provided"
                                )

                            elif "duration" in field_name:
                                duration = field_value

                        if not user_id:
                            skipped += 1
                            continue

                        # Get timestamp from message
                        timestamp = message.created_at.isoformat()

                        # Create mod log entry
                        db.log_action(
                            ctx.guild.id,
                            {
                                "action": action,
                                "user_id": user_id,
                                "moderator_id": moderator_id or str(ctx.author.id),
                                "reason": reason
                                + (f" (Duration: {duration})" if duration else ""),
                                "case": case_number,
                                "timestamp": timestamp,
                            },
                        )

                        if action in imported:
                            imported[action] += 1
                        else:
                            imported["other"] += 1

                    except Exception as e:
                        errors_count += 1
                        if len(debug_titles) < 10:
                            debug_titles.append(f"ERROR: {str(e)[:40]}")
                        continue

            # Update case counter to highest case number in database
            highest_case = db.get_highest_case_number(ctx.guild.id)
            if highest_case > 0:
                db.update_guild_settings(ctx.guild.id, {"case_number": highest_case})

            # Summary
            total = sum(imported.values())
            summary_embed = discord.Embed(
                title="Circle Import Complete", color=discord.Color.green()
            )
            summary_embed.add_field(name="Total Imported", value=str(total), inline=True)
            summary_embed.add_field(name="Skipped", value=str(skipped), inline=True)
            summary_embed.add_field(name="Errors", value=str(errors_count), inline=True)

            breakdown = []
            if imported["warn"]:
                breakdown.append(f"{imported['warn']} warnings")
            if imported["ban"]:
                breakdown.append(f"{imported['ban']} bans")
            if imported["kick"]:
                breakdown.append(f"{imported['kick']} kicks")
            if imported["mute"]:
                breakdown.append(f"{imported['mute']} mutes")
            if imported["timeout"]:
                breakdown.append(f"{imported['timeout']} timeouts")
            if imported["unmute"]:
                breakdown.append(f"{imported['unmute']} unmutes")
            if imported["other"]:
                breakdown.append(f"{imported['other']} other")

            if breakdown:
                summary_embed.add_field(
                    name="Breakdown", value="\n".join(breakdown), inline=False
                )

            if debug_titles:
                summary_embed.add_field(
                    name="Sample Titles Found",
                    value="\n".join(debug_titles[:5]),
                    inline=False,
                )

            summary_embed.add_field(
                name="Case Counter", value=f"Set to #{highest_case}", inline=True
            )
            summary_embed.set_footer(
                text=f"Scanned {limit} messages, found {circle_messages} Circle messages in #{channel.name}"
            )

            await status_msg.edit(content=None, embed=summary_embed)

        except discord.Forbidden:
            await status_msg.edit(content=None, embed=error("I don't have permission to read that channel."))
        except Exception as e:
            await status_msg.edit(content=None, embed=error(f"Error during import: {e}"))


async def setup(bot):
    await bot.add_cog(InvitesCog(bot))
