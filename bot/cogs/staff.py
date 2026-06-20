import discord
from discord.ext import commands
from discord import app_commands
from datetime import datetime
from database import db
from helpers.embeds import success, error, warning, info
from helpers import is_staff, is_module_enabled

MAIN_SERVER_ID = 1459426097283334147


class Staff(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.hybrid_command(
        name="staffinfo", description="View information about this server (Staff only)"
    )
    async def staffinfo(self, ctx: commands.Context):
        """View server settings and stats - available to support and dev staff."""
        is_support, is_dev = await is_staff(self.bot, ctx.author.id)
        if not is_support:
            await ctx.send(embed=error("You don't have permission to use this command."))
            return

        guild = ctx.guild
        settings = db.get_guild_settings(guild.id)

        # Get moderation stats
        mod_logs = db.get_mod_logs(guild.id, limit=1000)
        warnings = db.get_all_warnings(guild.id)

        embed = discord.Embed(
            title=f"Staff Info - {guild.name}", color=discord.Color.blue()
        )
        if guild.icon:
            embed.set_thumbnail(url=guild.icon.url)

        # Server info
        embed.add_field(name="Server ID", value=guild.id, inline=True)
        embed.add_field(
            name="Owner",
            value=f"{guild.owner.mention if guild.owner else 'Unknown'}",
            inline=True,
        )
        embed.add_field(name="Members", value=guild.member_count, inline=True)

        # Settings info
        prefix = settings.get("prefix", "a!")
        mod_log = settings.get("mod_log_channel")
        embed.add_field(name="Prefix", value=f"`{prefix}`", inline=True)
        embed.add_field(
            name="Mod Log", value=f"<#{mod_log}>" if mod_log else "Not set", inline=True
        )

        # Moderation stats
        embed.add_field(name="Total Mod Actions", value=len(mod_logs), inline=True)
        embed.add_field(name="Active Warnings", value=len(warnings), inline=True)

        # Enabled modules
        modules = []
        for module in [
            "moderation",
            "tickets",
            "utility",
            "welcome",
            "auto_mod",
            "reaction_roles",
        ]:
            if is_module_enabled(guild.id, module):
                modules.append(module)
        embed.add_field(
            name="Enabled Modules",
            value=", ".join(modules) if modules else "None",
            inline=False,
        )

        embed.set_footer(text=f"Requested by {ctx.author}")
        embed.timestamp = datetime.utcnow()

        await ctx.send(embed=embed)

    @commands.hybrid_command(
        name="stafflookup",
        description="Look up a user's history across servers (Staff only)",
    )
    @app_commands.describe(user="User ID or mention to look up")
    async def stafflookup(self, ctx: commands.Context, user: str):
        """Look up user's moderation history across all servers - available to support and dev staff."""
        is_support, is_dev = await is_staff(self.bot, ctx.author.id)
        if not is_support:
            await ctx.send(embed=error("You don't have permission to use this command."))
            return

        # Parse user ID
        user_id = user.strip("<@!>")
        try:
            user_id = int(user_id)
        except ValueError:
            await ctx.send(embed=error("Invalid user ID."))
            return

        # Try to get user info
        try:
            discord_user = await self.bot.fetch_user(user_id)
        except discord.NotFound:
            discord_user = None

        embed = discord.Embed(title=f"Staff Lookup", color=discord.Color.blue())

        if discord_user:
            embed.set_thumbnail(url=discord_user.display_avatar.url)
            embed.add_field(
                name="User", value=f"{discord_user} ({discord_user.id})", inline=False
            )
            embed.add_field(
                name="Account Created",
                value=f"<t:{int(discord_user.created_at.timestamp())}:R>",
                inline=True,
            )
        else:
            embed.add_field(name="User ID", value=user_id, inline=False)
            embed.add_field(
                name="Note",
                value="Could not fetch user info (may be deleted)",
                inline=False,
            )

        # Check blacklist status
        blacklist_data = db.is_blacklisted(user_id)
        if blacklist_data:
            embed.add_field(
                name="Blacklisted",
                value=f"Reason: {blacklist_data.get('reason', 'No reason')}",
                inline=False,
            )
        else:
            embed.add_field(name="Blacklist Status", value="Not blacklisted", inline=True)

        # Look up across all guilds the bot is in
        total_warnings = 0
        total_actions = 0
        servers_with_history = []

        for guild in self.bot.guilds:
            warnings = db.get_warnings(guild.id, user_id)
            mod_logs = [
                log
                for log in db.get_mod_logs(guild.id, limit=100)
                if log.get("user_id") == str(user_id)
            ]

            if warnings or mod_logs:
                servers_with_history.append(
                    f"**{guild.name}**: {len(warnings)} warnings, {len(mod_logs)} actions"
                )
                total_warnings += len(warnings)
                total_actions += len(mod_logs)

        embed.add_field(name="Total Warnings", value=total_warnings, inline=True)
        embed.add_field(name="Total Mod Actions", value=total_actions, inline=True)

        if servers_with_history:
            history_text = "\n".join(servers_with_history[:10])
            if len(servers_with_history) > 10:
                history_text += f"\n... and {len(servers_with_history) - 10} more servers"
            embed.add_field(name="History by Server", value=history_text, inline=False)

        embed.set_footer(text=f"Requested by {ctx.author}")
        embed.timestamp = datetime.utcnow()

        await ctx.send(embed=embed)

    @commands.hybrid_command(name="botstats", description="View bot statistics (Staff only)")
    async def botstats(self, ctx: commands.Context):
        """View bot statistics - available to support and dev staff."""
        is_support, is_dev = await is_staff(self.bot, ctx.author.id)
        if not is_support:
            await ctx.send(embed=error("You don't have permission to use this command."))
            return

        embed = discord.Embed(title="Apex Bot Statistics", color=discord.Color.blurple())
        embed.set_thumbnail(url=self.bot.user.display_avatar.url)

        # Bot info
        embed.add_field(name="Servers", value=len(self.bot.guilds), inline=True)
        embed.add_field(
            name="Users", value=sum(g.member_count for g in self.bot.guilds), inline=True
        )
        embed.add_field(name="Latency", value=f"{round(self.bot.latency * 1000)}ms", inline=True)

        # Blacklist stats
        blacklist = db.get_blacklist()
        embed.add_field(name="Blacklisted Users", value=len(blacklist), inline=True)

        # Uptime (from status)
        status = db.get_bot_status()
        if status and status.get("started_at"):
            started_at = status["started_at"]
            if hasattr(started_at, "timestamp"):
                embed.add_field(
                    name="Started",
                    value=f"<t:{int(started_at.timestamp())}:R>",
                    inline=True,
                )

        # Top servers by member count
        top_servers = sorted(self.bot.guilds, key=lambda g: g.member_count, reverse=True)[:5]
        top_list = "\n".join(
            [f"- {g.name} ({g.member_count} members)" for g in top_servers]
        )
        embed.add_field(name="Top Servers", value=top_list, inline=False)

        embed.set_footer(text=f"Requested by {ctx.author}")
        embed.timestamp = datetime.utcnow()

        await ctx.send(embed=embed)

    @commands.hybrid_command(
        name="blacklist", description="Blacklist a user from using the bot (Dev only)"
    )
    @app_commands.describe(
        user="User ID or mention to blacklist", reason="Reason for blacklisting"
    )
    async def blacklist_user(
        self, ctx: commands.Context, user: str, *, reason: str = "No reason provided"
    ):
        """Blacklist a user from using the bot - dev only."""
        is_support, is_dev = await is_staff(self.bot, ctx.author.id)
        if not is_dev:
            await ctx.send(
                embed=error("You don't have permission to use this command. Dev role required.")
            )
            return

        # Parse user ID
        user_id = user.strip("<@!>")
        try:
            user_id = int(user_id)
        except ValueError:
            await ctx.send(embed=error("Invalid user ID."))
            return

        # Check if already blacklisted
        if db.is_blacklisted(user_id):
            await ctx.send(embed=warning(f"User `{user_id}` is already blacklisted."))
            return

        # Add to blacklist
        db.add_to_blacklist(user_id, reason, ctx.author.id)

        # Try to get user info
        try:
            discord_user = await self.bot.fetch_user(user_id)
            user_display = f"{discord_user} ({user_id})"
        except:
            user_display = str(user_id)

        embed = discord.Embed(
            title="User Blacklisted",
            description=f"**User:** {user_display}\n**Reason:** {reason}",
            color=discord.Color.red(),
        )
        embed.set_footer(text=f"Blacklisted by {ctx.author}")
        embed.timestamp = datetime.utcnow()

        await ctx.send(embed=embed)

    @commands.hybrid_command(
        name="unblacklist", description="Remove a user from the blacklist (Dev only)"
    )
    @app_commands.describe(user="User ID or mention to unblacklist")
    async def unblacklist_user(self, ctx: commands.Context, user: str):
        """Remove a user from the blacklist - dev only."""
        is_support, is_dev = await is_staff(self.bot, ctx.author.id)
        if not is_dev:
            await ctx.send(
                embed=error("You don't have permission to use this command. Dev role required.")
            )
            return

        # Parse user ID
        user_id = user.strip("<@!>")
        try:
            user_id = int(user_id)
        except ValueError:
            await ctx.send(embed=error("Invalid user ID."))
            return

        # Check if blacklisted
        if not db.is_blacklisted(user_id):
            await ctx.send(embed=error(f"User `{user_id}` is not blacklisted."))
            return

        # Remove from blacklist
        db.remove_from_blacklist(user_id)

        embed = discord.Embed(
            title="User Unblacklisted",
            description=f"**User ID:** {user_id}",
            color=discord.Color.green(),
        )
        embed.set_footer(text=f"Unblacklisted by {ctx.author}")
        embed.timestamp = datetime.utcnow()

        await ctx.send(embed=embed)

    @commands.hybrid_command(
        name="forceleave", description="Force the bot to leave a server (Dev only)"
    )
    @app_commands.describe(guild_id="Server ID to leave (default: current server)")
    async def forceleave(self, ctx: commands.Context, guild_id: str = None):
        """Force the bot to leave a server - dev only."""
        is_support, is_dev = await is_staff(self.bot, ctx.author.id)
        if not is_dev:
            await ctx.send(
                embed=error("You don't have permission to use this command. Dev role required.")
            )
            return

        # Use current guild if no ID provided
        if guild_id:
            try:
                target_guild = self.bot.get_guild(int(guild_id))
            except ValueError:
                await ctx.send(embed=error("Invalid guild ID."))
                return
        else:
            target_guild = ctx.guild

        if not target_guild:
            await ctx.send(embed=error("Could not find that server."))
            return

        # Don't allow leaving the main server
        if target_guild.id == MAIN_SERVER_ID:
            await ctx.send(embed=error("Cannot force leave the main server."))
            return

        guild_name = target_guild.name
        await target_guild.leave()

        embed = discord.Embed(
            title="Force Left Server",
            description=f"**Server:** {guild_name}\n**ID:** {guild_id or ctx.guild.id}",
            color=discord.Color.orange(),
        )
        embed.set_footer(text=f"Executed by {ctx.author}")
        embed.timestamp = datetime.utcnow()

        # If we left the current server, we can't send a response
        if guild_id and guild_id != str(ctx.guild.id):
            await ctx.send(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(Staff(bot))
