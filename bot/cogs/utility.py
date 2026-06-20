import discord
from discord.ext import commands
from discord import app_commands
from datetime import datetime, timedelta, timezone
import asyncio
import aiohttp
import re

from database import db
from helpers import has_mod_role, has_admin_role, is_staff
from helpers.embeds import success, error, warning, info
from helpers.utils import is_module_enabled, fuzzy_match, handle_custom_command, parse_duration


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


class HelpSelect(discord.ui.Select):
    def __init__(self, categories: dict, prefix: str, author_id: int):
        self.categories = categories
        self.prefix = prefix
        self.author_id = author_id
        options = [
            discord.SelectOption(label="Overview", value="overview", description="All command categories")
        ]
        for key, cat in categories.items():
            options.append(discord.SelectOption(label=cat["name"], value=key, description=f"{len(cat['commands'])} commands"))
        super().__init__(placeholder="Select a category...", options=options, custom_id="help_select")

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(embed=error("This isn't your help menu."), ephemeral=True)
            return
        value = self.values[0]
        if value == "overview":
            embed = self._build_overview()
        else:
            embed = self._build_category(value)
        await interaction.response.edit_message(embed=embed)

    def _build_overview(self) -> discord.Embed:
        embed = discord.Embed(
            title="Apex Commands",
            description=f"Select a category from the dropdown or use `{self.prefix}help <command>` for details.",
            color=discord.Color.from_str("#5865F2"),
        )
        for key, cat in self.categories.items():
            cmd_names = ", ".join(f"`{c[0]}`" for c in cat["commands"][:6])
            if len(cat["commands"]) > 6:
                cmd_names += f" +{len(cat['commands']) - 6} more"
            embed.add_field(name=cat["name"], value=cmd_names, inline=False)
        embed.set_footer(text="Apex")
        return embed

    def _build_category(self, key: str) -> discord.Embed:
        cat = self.categories[key]
        lines = []
        for name, desc in cat["commands"]:
            lines.append(f"`{self.prefix}{name}` \u2014 {desc}")
        embed = discord.Embed(
            title=cat["name"],
            description="\n".join(lines),
            color=discord.Color.from_str("#5865F2"),
        )
        embed.set_footer(text=f"Apex \u2022 {len(cat['commands'])} commands")
        return embed


class HelpView(discord.ui.View):
    def __init__(self, categories: dict, prefix: str, author_id: int):
        super().__init__(timeout=120)
        self.add_item(HelpSelect(categories, prefix, author_id))


class PollView(discord.ui.View):
    def __init__(self, options: list, end_time: datetime = None):
        super().__init__(timeout=None)
        self.votes = {i: set() for i in range(len(options))}
        self.options = options
        self.end_time = end_time
        self.ended = False

        for i, option in enumerate(options[:5]):  # Max 5 options
            button = discord.ui.Button(
                label=f"{option} (0)",
                style=discord.ButtonStyle.primary,
                custom_id=f"poll_option_{i}",
            )
            button.callback = self.create_callback(i)
            self.add_item(button)

    def create_callback(self, index: int):
        async def callback(interaction: discord.Interaction):
            if self.ended:
                await interaction.response.send_message(
                    embed=info("This poll has ended."), ephemeral=True
                )
                return

            user_id = interaction.user.id

            # Remove vote from other options
            for i in self.votes:
                self.votes[i].discard(user_id)

            # Add vote to selected option
            self.votes[index].add(user_id)

            # Update button labels
            for i, child in enumerate(self.children):
                if isinstance(child, discord.ui.Button):
                    child.label = f"{self.options[i]} ({len(self.votes[i])})"

            await interaction.response.edit_message(view=self)

        return callback

    def get_results(self) -> str:
        total = sum(len(v) for v in self.votes.values())
        results = []
        for i, option in enumerate(self.options):
            count = len(self.votes[i])
            pct = (count / total * 100) if total > 0 else 0
            bar = "\u2588" * int(pct / 10) + "\u2591" * (10 - int(pct / 10))
            results.append(f"**{option}**: {count} votes ({pct:.1f}%)\n{bar}")
        return "\n\n".join(results)


# Store active polls for ending
active_polls = {}


async def custom_command_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    """Autocomplete for custom command selection."""
    try:
        custom_cmds = db.get_all_custom_commands(interaction.guild.id)
    except Exception:
        return []
    if not custom_cmds:
        return []

    choices = []
    for cmd in custom_cmds:
        if not cmd.get("enabled", True):
            continue
        name = cmd.get("name", "")
        if current.lower() in name.lower():
            choices.append(app_commands.Choice(name=name, value=name))
        if len(choices) >= 25:
            break

    return choices


class Utility(commands.Cog):
    _cd = commands.CooldownMapping.from_cooldown(1, 3, commands.BucketType.user)

    async def cog_check(self, ctx: commands.Context) -> bool:
        bucket = self._cd.get_bucket(ctx.message)
        retry_after = bucket.update_rate_limit()
        if retry_after:
            await ctx.send(embed=error(f"Cooldown. Try again in {retry_after:.1f}s."), delete_after=3)
            return False
        return True

    def __init__(self, bot):
        self.bot = bot


    @commands.hybrid_command(name="userinfo", description="Get information about a user")
    @app_commands.describe(user="User to get info about")
    async def userinfo(self, ctx: commands.Context, user: discord.Member = None):
        if not is_module_enabled(ctx.guild.id, "utility"):
            await ctx.send(embed=error("The utility module is disabled on this server."))
            return

        user = user or ctx.author

        # Get user's roles (excluding @everyone)
        roles = [r.mention for r in reversed(user.roles) if r != ctx.guild.default_role]
        roles_str = ", ".join(roles[:10]) if roles else "None"
        if len(roles) > 10:
            roles_str += f" (+{len(roles) - 10} more)"

        # Calculate account age
        account_age = (datetime.utcnow() - user.created_at.replace(tzinfo=None)).days
        join_age = (
            (datetime.utcnow() - user.joined_at.replace(tzinfo=None)).days
            if user.joined_at
            else 0
        )

        embed = discord.Embed(
            title=f"User Info - {user}", color=user.color or discord.Color.blurple()
        )
        embed.set_thumbnail(url=user.display_avatar.url)

        embed.add_field(name="ID", value=user.id, inline=True)
        embed.add_field(name="Nickname", value=user.nick or "None", inline=True)
        embed.add_field(name="Bot", value="Yes" if user.bot else "No", inline=True)

        embed.add_field(
            name="Account Created",
            value=f"<t:{int(user.created_at.timestamp())}:R> ({account_age} days ago)",
            inline=True,
        )
        embed.add_field(
            name="Joined Server",
            value=(
                f"<t:{int(user.joined_at.timestamp())}:R> ({join_age} days ago)"
                if user.joined_at
                else "Unknown"
            ),
            inline=True,
        )
        embed.add_field(
            name="Boosting Since",
            value=(
                f"<t:{int(user.premium_since.timestamp())}:R>"
                if user.premium_since
                else "Not boosting"
            ),
            inline=True,
        )

        embed.add_field(name=f"Roles [{len(roles)}]", value=roles_str, inline=False)

        # Status and activity
        status_emoji = {
            "online": "Online",
            "idle": "Idle",
            "dnd": "DND",
            "offline": "Offline",
        }
        embed.add_field(
            name="Status",
            value=f"{status_emoji.get(str(user.status), 'Offline')}",
            inline=True,
        )

        if user.activity:
            embed.add_field(
                name="Activity",
                value=str(user.activity.name)[:100] if user.activity.name else "None",
                inline=True,
            )

        embed.set_footer(text=f"Requested by {ctx.author}")
        embed.timestamp = datetime.utcnow()

        await ctx.send(embed=embed)

    @commands.hybrid_command(name="ping", description="Check the bot's latency")
    async def ping(self, ctx: commands.Context):
        """Check the bot's latency and response time."""
        import time

        # Measure message latency
        start = time.perf_counter()
        msg = await ctx.send(embed=info("Pinging..."))
        end = time.perf_counter()
        message_latency = (end - start) * 1000

        # WebSocket latency
        ws_latency = self.bot.latency * 1000

        # Database latency
        db_start = time.perf_counter()
        try:
            db.get_guild_settings(ctx.guild.id)
            db_latency = (time.perf_counter() - db_start) * 1000
            db_status = f"{db_latency:.1f}ms"
            db_ok = True
        except Exception:
            db_status = "Error"
            db_ok = False

        # Calculate uptime
        bot_start_time = getattr(self.bot, "bot_start_time", None)
        if bot_start_time:
            now = datetime.now(timezone.utc)
            start_time = (
                bot_start_time.replace(tzinfo=timezone.utc)
                if bot_start_time.tzinfo is None
                else bot_start_time
            )
            uptime_delta = now - start_time
            days = uptime_delta.days
            hours, remainder = divmod(uptime_delta.seconds, 3600)
            minutes, seconds = divmod(remainder, 60)
            if days > 0:
                uptime_str = f"{days}d {hours}h {minutes}m"
            elif hours > 0:
                uptime_str = f"{hours}h {minutes}m {seconds}s"
            else:
                uptime_str = f"{minutes}m {seconds}s"
        else:
            uptime_str = "Unknown"

        # Determine status for each metric
        def get_status(value, good, warning_threshold):
            if value <= good:
                return "Good"
            elif value <= warning_threshold:
                return "OK"
            return "Poor"

        ws_status = get_status(ws_latency, 150, 300)
        msg_status = get_status(message_latency, 300, 600)
        db_status_emoji = (
            "Good"
            if db_ok and float(db_status.replace("ms", "")) < 100
            else "OK" if db_ok else "Poor"
        )

        # Overall status (more lenient thresholds)
        if ws_latency < 150 and message_latency < 300 and db_ok:
            status_text = "Excellent"
            color = discord.Color.green()
        elif ws_latency < 300 and message_latency < 600 and db_ok:
            status_text = "Good"
            color = discord.Color.gold()
        else:
            status_text = "Degraded"
            color = discord.Color.red()

        embed = discord.Embed(title="Pong!", color=color)
        embed.add_field(
            name="WebSocket", value=f"{ws_status} `{ws_latency:.1f}ms`", inline=True
        )
        embed.add_field(
            name="Message", value=f"{msg_status} `{message_latency:.1f}ms`", inline=True
        )
        embed.add_field(
            name="Database", value=f"{db_status_emoji} `{db_status}`", inline=True
        )
        embed.add_field(name="Uptime", value=f"`{uptime_str}`", inline=True)
        embed.add_field(name="Servers", value=f"`{len(self.bot.guilds)}`", inline=True)
        embed.add_field(name="Status", value=f"**{status_text}**", inline=True)
        embed.set_footer(text=f"Shard: {ctx.guild.shard_id if ctx.guild else 0}")

        await msg.edit(content=None, embed=embed)

    @commands.hybrid_command(name="serverinfo", description="Get information about the server")
    async def serverinfo(self, ctx: commands.Context):
        if not is_module_enabled(ctx.guild.id, "utility"):
            await ctx.send(embed=error("The utility module is disabled on this server."))
            return

        guild = ctx.guild

        # Count channels by type
        text_channels = len(guild.text_channels)
        voice_channels = len(guild.voice_channels)
        categories = len(guild.categories)

        # Count members by status
        online = sum(1 for m in guild.members if m.status == discord.Status.online)
        idle = sum(1 for m in guild.members if m.status == discord.Status.idle)
        dnd = sum(1 for m in guild.members if m.status == discord.Status.dnd)
        offline = sum(1 for m in guild.members if m.status == discord.Status.offline)
        bots = sum(1 for m in guild.members if m.bot)

        embed = discord.Embed(
            title=f"Server Info - {guild.name}", color=discord.Color.blurple()
        )

        if guild.icon:
            embed.set_thumbnail(url=guild.icon.url)

        embed.add_field(
            name="Owner",
            value=guild.owner.mention if guild.owner else "Unknown",
            inline=True,
        )
        embed.add_field(name="ID", value=guild.id, inline=True)
        embed.add_field(
            name="Created", value=f"<t:{int(guild.created_at.timestamp())}:R>", inline=True
        )

        embed.add_field(
            name=f"Members [{guild.member_count}]",
            value=f"{online} online, {idle} idle, {dnd} dnd, {offline} offline\n{bots} bots",
            inline=True,
        )
        embed.add_field(
            name=f"Channels [{text_channels + voice_channels}]",
            value=f"{text_channels} text\n{voice_channels} voice\n{categories} categories",
            inline=True,
        )
        embed.add_field(name="Roles", value=len(guild.roles), inline=True)

        embed.add_field(
            name="Verification", value=str(guild.verification_level).title(), inline=True
        )
        embed.add_field(
            name="Boosts",
            value=f"Level {guild.premium_tier} ({guild.premium_subscription_count} boosts)",
            inline=True,
        )
        embed.add_field(
            name="Emojis", value=f"{len(guild.emojis)}/{guild.emoji_limit}", inline=True
        )

        if guild.banner:
            embed.set_image(url=guild.banner.url)

        embed.set_footer(text=f"Requested by {ctx.author}")
        embed.timestamp = datetime.utcnow()

        await ctx.send(embed=embed)

    @commands.hybrid_command(name="staff", description="View the server's admin and mod roles")
    async def staff_cmd(self, ctx: commands.Context):
        """View configured admin and mod roles for this server."""
        settings = db.get_guild_settings(ctx.guild.id)
        admin_role_ids = settings.get("admin_roles", [])
        mod_role_ids = settings.get("mod_roles", [])

        embed = discord.Embed(
            title=f"Staff Roles - {ctx.guild.name}", color=discord.Color.blurple()
        )

        if ctx.guild.icon:
            embed.set_thumbnail(url=ctx.guild.icon.url)

        # Admin roles (dashboard access - Manage Server perm or configured admin roles)
        admin_roles = []
        for role_id in admin_role_ids:
            role = ctx.guild.get_role(int(role_id))
            if role:
                admin_roles.append(role.mention)

        admin_text = "\n".join(admin_roles) if admin_roles else "No admin roles configured"
        admin_text += (
            "\n\n*Members with `Manage Server` permission also have admin access.*"
        )
        embed.add_field(
            name="Admin Roles (Dashboard Access)", value=admin_text, inline=False
        )

        # Mod roles (moderation access)
        mod_roles = []
        for role_id in mod_role_ids:
            role = ctx.guild.get_role(int(role_id))
            if role:
                mod_roles.append(role.mention)

        mod_text = "\n".join(mod_roles) if mod_roles else "No mod roles configured"
        mod_text += "\n\n*Members with `Kick/Ban Members` permission also have mod access.*"
        embed.add_field(name="Mod Roles (Moderation Access)", value=mod_text, inline=False)

        embed.set_footer(text=f"Requested by {ctx.author} \u2022 Configure in dashboard")
        embed.timestamp = datetime.utcnow()

        await ctx.send(embed=embed)


    @commands.hybrid_command(name="serverid", description="Get the server's ID")
    async def serverid(self, ctx: commands.Context):
        """Get the current server's ID."""
        embed = discord.Embed(
            title="Server ID",
            description=f"```{ctx.guild.id}```",
            color=discord.Color.blurple(),
        )
        embed.set_footer(
            text=ctx.guild.name, icon_url=ctx.guild.icon.url if ctx.guild.icon else None
        )
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="userid", description="Get a user's ID")
    @app_commands.describe(user="The user to get the ID of (leave empty for yourself)")
    async def userid(self, ctx: commands.Context, user: discord.User = None):
        """Get a user's ID."""
        target = user or ctx.author
        embed = discord.Embed(
            title="User ID", description=f"```{target.id}```", color=discord.Color.blurple()
        )
        embed.set_footer(text=str(target), icon_url=target.display_avatar.url)
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="roleid", description="Get a role's ID")
    @app_commands.describe(role="The role to get the ID of")
    async def roleid(self, ctx: commands.Context, *, role: discord.Role):
        """Get a role's ID."""
        embed = discord.Embed(
            title="Role ID",
            description=f"```{role.id}```",
            color=(
                role.color
                if role.color != discord.Color.default()
                else discord.Color.blurple()
            ),
        )
        embed.set_footer(text=f"@{role.name}")
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="channelid", description="Get a channel's ID")
    @app_commands.describe(
        channel="The channel to get the ID of (leave empty for current channel)"
    )
    async def channelid(self, ctx: commands.Context, channel: discord.abc.GuildChannel = None):
        """Get a channel's ID."""
        target = channel or ctx.channel
        embed = discord.Embed(
            title="Channel ID",
            description=f"```{target.id}```",
            color=discord.Color.blurple(),
        )
        embed.set_footer(text=f"#{target.name}")
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="roleinfo", description="Get information about a role")
    @app_commands.describe(role="Role to get info about")
    async def roleinfo(self, ctx: commands.Context, *, role: str):
        if not is_module_enabled(ctx.guild.id, "utility"):
            await ctx.send(embed=error("The utility module is disabled on this server."))
            return

        # Find the role with fuzzy matching
        found_role = find_role(ctx.guild, role)
        if not found_role:
            await ctx.send(embed=error(f"Could not find a role matching `{role}`"))
            return
        role = found_role

        # Get members with this role
        members_with_role = len(role.members)

        # Key permissions
        key_perms = []
        if role.permissions.administrator:
            key_perms.append("Administrator")
        if role.permissions.manage_guild:
            key_perms.append("Manage Server")
        if role.permissions.manage_channels:
            key_perms.append("Manage Channels")
        if role.permissions.manage_roles:
            key_perms.append("Manage Roles")
        if role.permissions.manage_messages:
            key_perms.append("Manage Messages")
        if role.permissions.kick_members:
            key_perms.append("Kick Members")
        if role.permissions.ban_members:
            key_perms.append("Ban Members")
        if role.permissions.moderate_members:
            key_perms.append("Timeout Members")
        if role.permissions.mention_everyone:
            key_perms.append("Mention Everyone")

        embed = discord.Embed(
            title=f"Role Info - {role.name}", color=role.color or discord.Color.blurple()
        )

        embed.add_field(name="ID", value=role.id, inline=True)
        embed.add_field(name="Color", value=str(role.color), inline=True)
        embed.add_field(name="Position", value=role.position, inline=True)

        embed.add_field(name="Members", value=members_with_role, inline=True)
        embed.add_field(
            name="Mentionable", value="Yes" if role.mentionable else "No", inline=True
        )
        embed.add_field(name="Hoisted", value="Yes" if role.hoist else "No", inline=True)

        embed.add_field(
            name="Created", value=f"<t:{int(role.created_at.timestamp())}:R>", inline=True
        )
        embed.add_field(name="Mention", value=role.mention, inline=True)
        embed.add_field(
            name="Managed", value="Yes (integration)" if role.managed else "No", inline=True
        )

        if key_perms:
            embed.add_field(
                name="Key Permissions", value=", ".join(key_perms), inline=False
            )
        else:
            embed.add_field(name="Key Permissions", value="None", inline=False)

        embed.set_footer(text=f"Requested by {ctx.author}")
        embed.timestamp = datetime.utcnow()

        await ctx.send(embed=embed)


    @commands.hybrid_command(name="members", description="Show all members with a specific role")
    @app_commands.describe(query="Role name, or 'role not excluded_role'")
    async def members(self, ctx: commands.Context, *, query: str):
        # Parse "role not excluded_role" syntax
        exclude_names = []
        if " not " in query.lower():
            parts = query.lower().split(" not ", 1)
            role_part = query[:len(parts[0])]
            exclude_part = query[len(parts[0]) + 5:]
            exclude_names = [e.strip() for e in exclude_part.split(",") if e.strip()]
        else:
            role_part = query

        # Support multiple include roles with comma
        include_names = [r.strip() for r in role_part.split(",") if r.strip()]
        if not include_names:
            return await ctx.send(embed=error("Provide a role name."))

        # Find first include role
        found_role = find_role(ctx.guild, include_names[0])
        if not found_role:
            return await ctx.send(embed=error(f"Could not find role: `{include_names[0]}`"))

        result = set(found_role.members)

        # Intersect with additional include roles
        for name in include_names[1:]:
            r = find_role(ctx.guild, name)
            if r:
                result = result & set(r.members)

        # Exclude roles
        for name in exclude_names:
            r = find_role(ctx.guild, name)
            if r:
                result = {m for m in result if r not in m.roles}

        result = sorted(result, key=lambda m: m.display_name.lower())

        if not result:
            return await ctx.send(embed=info("No members match that filter."))

        member_list = [f"{m.mention} (`{m.name}`)" for m in list(result)[:50]]
        total = len(result)

        # Build title with all roles
        all_include = [found_role.name] + [find_role(ctx.guild, n).name for n in include_names[1:] if find_role(ctx.guild, n)]
        title = "Members with " + ", ".join(all_include)
        if exclude_names:
            excluded = [find_role(ctx.guild, n).name for n in exclude_names if find_role(ctx.guild, n)]
            if excluded:
                title += " not " + ", ".join(excluded)

        embed = discord.Embed(
            title=title,
            description="\n".join(member_list),
            color=found_role.color if found_role.color.value else discord.Color.blurple(),
        )
        embed.set_footer(text=f"{total} member{'s' if total != 1 else ''}" + (" (first 50)" if total > 50 else ""))
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="roles", description="List all roles in the server")
    @app_commands.describe(
        filter="Filter: user mention, 'bots', 'mentionable', 'hoisted', or search query",
    )
    async def roles_cmd(self, ctx: commands.Context, *, filter: str = None):
        roles = sorted(ctx.guild.roles[1:], key=lambda r: r.position, reverse=True)

        if filter:
            f = filter.lower().strip()
            if f == "bots":
                roles = [r for r in roles if r.is_bot_managed()]
            elif f == "mentionable":
                roles = [r for r in roles if r.mentionable]
            elif f == "unmentionable":
                roles = [r for r in roles if not r.mentionable]
            elif f == "hoisted":
                roles = [r for r in roles if r.hoist]
            elif f == "unhoisted":
                roles = [r for r in roles if not r.hoist]
            else:
                member = ctx.guild.get_member_named(filter)
                if not member:
                    try:
                        member = ctx.guild.get_member(int(filter.strip("<@!>")))
                    except ValueError:
                        member = None
                if member:
                    roles = sorted(member.roles[1:], key=lambda r: r.position, reverse=True)
                else:
                    roles = [r for r in roles if f in r.name.lower()]

        if not roles:
            return await ctx.send(embed=info("No roles found."))

        lines = []
        for r in roles[:40]:
            color = f"#{r.color.value:06x}" if r.color.value else "none"
            lines.append(f"{r.mention} — {len(r.members)} members — `{color}`")

        embed = discord.Embed(
            description="\n".join(lines),
            color=discord.Color.blurple(),
        )
        title = "Server Roles"
        if filter:
            title += f" — {filter}"
        embed.set_author(name=title)
        embed.set_footer(text=f"{len(roles)} role{'s' if len(roles) != 1 else ''}" + (" (first 40)" if len(roles) > 40 else ""))
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="editrole", description="Edit a role")
    @app_commands.describe(query="<role> <action> [value]")
    async def editrole(self, ctx: commands.Context, *, query: str = None):
        if not has_admin_role(ctx.author):
            return await ctx.send(embed=error("You need admin permissions."))

        if not query:
            prefix = db.get_prefix(ctx.guild.id) if ctx.guild else "a!"
            return await ctx.send(embed=info(
                f"`{prefix}editrole <role> name <new_name>`\n"
                f"`{prefix}editrole <role> color <hex|random|clear>`\n"
                f"`{prefix}editrole <role> hoist`\n"
                f"`{prefix}editrole <role> mentionable`\n"
                f"`{prefix}editrole <role> position <num|above|below> <role>`\n"
                f"`{prefix}editrole <role> permissions <perm1, perm2|clear>`"
            ))

        # Parse: find the action keyword in the query, everything before it is the role name
        actions = ["name", "color", "colour", "hoist", "mentionable", "mention", "position", "permissions", "perms"]
        found_role = None
        action = None
        value = None

        words = query.split()
        for i in range(len(words)):
            if words[i].lower() in actions:
                role_name = " ".join(words[:i])
                action = words[i].lower()
                value = " ".join(words[i+1:]) if i + 1 < len(words) else None
                found_role = find_role(ctx.guild, role_name)
                if found_role:
                    break

        if not found_role or not action:
            return await ctx.send(embed=error(f"Could not parse command. Use: `a!editrole <role> <action> [value]`"))

        if found_role >= ctx.guild.me.top_role:
            return await ctx.send(embed=error("That role is above my highest role."))
        if found_role >= ctx.author.top_role and ctx.author != ctx.guild.owner:
            return await ctx.send(embed=error("That role is above your highest role."))

        action = action.lower()

        try:
            if action == "name":
                if not value:
                    return await ctx.send(embed=error("Provide a new name."))
                old = found_role.name
                await found_role.edit(name=value, reason=f"Edited by {ctx.author}")
                await ctx.send(embed=success(f"Renamed **{old}** to **{value}**."))

            elif action == "color" or action == "colour":
                if not value:
                    return await ctx.send(embed=error("Provide a hex color (e.g. #FF0000) or 'clear'."))
                if value.lower() == "clear":
                    await found_role.edit(color=discord.Color.default(), reason=f"Edited by {ctx.author}")
                    await ctx.send(embed=success(f"Cleared color for **{found_role.name}**."))
                elif value.lower() == "random":
                    await found_role.edit(color=discord.Color.random(), reason=f"Edited by {ctx.author}")
                    await ctx.send(embed=success(f"Set random color for **{found_role.name}**."))
                else:
                    hex_val = value.strip("#")
                    color = discord.Color(int(hex_val, 16))
                    await found_role.edit(color=color, reason=f"Edited by {ctx.author}")
                    await ctx.send(embed=success(f"Set color for **{found_role.name}** to `#{hex_val}`."))

            elif action == "hoist":
                new_val = not found_role.hoist
                await found_role.edit(hoist=new_val, reason=f"Edited by {ctx.author}")
                await ctx.send(embed=success(f"**{found_role.name}** is {'now' if new_val else 'no longer'} hoisted."))

            elif action == "mentionable" or action == "mention":
                new_val = not found_role.mentionable
                await found_role.edit(mentionable=new_val, reason=f"Edited by {ctx.author}")
                await ctx.send(embed=success(f"**{found_role.name}** is {'now' if new_val else 'no longer'} mentionable."))

            elif action == "position":
                if not value:
                    return await ctx.send(embed=error("Provide a position or 'above/below <role>'."))
                parts = value.split(None, 1)
                if parts[0].lower() == "above" and len(parts) > 1:
                    target = find_role(ctx.guild, parts[1])
                    if not target:
                        return await ctx.send(embed=error(f"Could not find role: `{parts[1]}`"))
                    await found_role.edit(position=target.position + 1, reason=f"Edited by {ctx.author}")
                    await ctx.send(embed=success(f"Moved **{found_role.name}** above **{target.name}**."))
                elif parts[0].lower() == "below" and len(parts) > 1:
                    target = find_role(ctx.guild, parts[1])
                    if not target:
                        return await ctx.send(embed=error(f"Could not find role: `{parts[1]}`"))
                    pos = max(1, target.position - 1)
                    await found_role.edit(position=pos, reason=f"Edited by {ctx.author}")
                    await ctx.send(embed=success(f"Moved **{found_role.name}** below **{target.name}**."))
                else:
                    try:
                        pos = int(parts[0])
                        await found_role.edit(position=pos, reason=f"Edited by {ctx.author}")
                        await ctx.send(embed=success(f"Set **{found_role.name}** position to {pos}."))
                    except ValueError:
                        await ctx.send(embed=error("Invalid position."))

            elif action == "permissions" or action == "perms":
                if not value:
                    return await ctx.send(embed=error("Provide permission names or 'clear'."))
                if value.lower() == "clear":
                    await found_role.edit(permissions=discord.Permissions.none(), reason=f"Edited by {ctx.author}")
                    await ctx.send(embed=success(f"Cleared all permissions for **{found_role.name}**."))
                else:
                    perms = found_role.permissions
                    for perm_name in value.split(","):
                        perm_name = perm_name.strip().lower().replace(" ", "_")
                        if hasattr(perms, perm_name):
                            current = getattr(perms, perm_name)
                            setattr(perms, perm_name, not current)
                        else:
                            return await ctx.send(embed=error(f"Unknown permission: `{perm_name}`"))
                    await found_role.edit(permissions=perms, reason=f"Edited by {ctx.author}")
                    await ctx.send(embed=success(f"Updated permissions for **{found_role.name}**."))

            else:
                prefix = db.get_prefix(ctx.guild.id) if ctx.guild else "a!"
                await ctx.send(embed=info(
                    f"`{prefix}editrole <role> name <new_name>`\n"
                    f"`{prefix}editrole <role> color <hex|random|clear>`\n"
                    f"`{prefix}editrole <role> hoist`\n"
                    f"`{prefix}editrole <role> mentionable`\n"
                    f"`{prefix}editrole <role> position <num|above|below> <role>`\n"
                    f"`{prefix}editrole <role> permissions <perm1, perm2|clear>`"
                ))

        except discord.Forbidden:
            await ctx.send(embed=error("I don't have permission to edit that role."))
        except Exception as e:
            await ctx.send(embed=error(f"Failed: {e}"))

    @commands.hybrid_command(name="copyemoji", description="Copy an emoji to this server")
    @app_commands.describe(
        emoji="The emoji to copy (or URL)", name="Custom name for the emoji (optional)"
    )
    async def copyemoji(self, ctx: commands.Context, emoji: str, name: str = None):
        if not is_module_enabled(ctx.guild.id, "utility"):
            await ctx.send(embed=error("The utility module is disabled on this server."))
            return

        # Check if user has manage emojis permission
        if not ctx.author.guild_permissions.manage_emojis:
            await ctx.send(embed=error("You need the Manage Emojis permission to use this command."))
            return

        # Try to parse as a custom emoji
        emoji_match = re.match(r"<(a)?:(\w+):(\d+)>", emoji)

        if emoji_match:
            # It's a custom emoji
            animated = emoji_match.group(1) is not None
            emoji_name = name or emoji_match.group(2)
            emoji_id = emoji_match.group(3)
            ext = "gif" if animated else "png"
            url = f"https://cdn.discordapp.com/emojis/{emoji_id}.{ext}"
        elif emoji.startswith("http"):
            # It's a URL
            url = emoji
            emoji_name = name or "emoji"
        else:
            await ctx.send(embed=error("Please provide a valid custom emoji or image URL."))
            return

        # Download the image
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as resp:
                    if resp.status != 200:
                        await ctx.send(embed=error("Failed to download the emoji image."))
                        return
                    image_data = await resp.read()

                    # Check file size (Discord limit is 256KB)
                    if len(image_data) > 256 * 1024:
                        await ctx.send(embed=error("The image is too large (max 256KB)."))
                        return
        except Exception as e:
            await ctx.send(embed=error(f"Failed to download the image: {e}"))
            return

        # Create the emoji
        try:
            new_emoji = await ctx.guild.create_custom_emoji(
                name=emoji_name, image=image_data
            )
            await ctx.send(embed=success(f"Successfully added {new_emoji} as `:{new_emoji.name}:`"))
        except discord.HTTPException as e:
            if "Maximum number of emojis reached" in str(e):
                await ctx.send(embed=error("This server has reached the maximum number of emojis."))
            else:
                await ctx.send(embed=error(f"Failed to create emoji: {e}"))


    @commands.hybrid_command(name="avatar", description="Get a user's avatar")
    @app_commands.describe(user="The user to get the avatar of (defaults to yourself)")
    async def avatar(self, ctx: commands.Context, user: discord.Member = None):
        if not is_module_enabled(ctx.guild.id, "utility"):
            await ctx.send(embed=error("The utility module is disabled on this server."))
            return
        user = user or ctx.author

        embed = discord.Embed(
            title=f"{user.display_name}'s Avatar",
            color=user.color if user.color.value else discord.Color.blurple(),
        )

        # Get both global and server avatar if different
        avatar_url = user.display_avatar.url
        embed.set_image(url=avatar_url)

        # Add links to different formats
        links = []
        for fmt in ["png", "jpg", "webp"]:
            links.append(
                f"[{fmt.upper()}]({user.display_avatar.replace(format=fmt, size=1024)})"
            )
        if user.display_avatar.is_animated():
            links.append(f"[GIF]({user.display_avatar.replace(format='gif', size=1024)})")

        embed.description = " | ".join(links)

        # If user has a server avatar different from global
        if user.guild_avatar and user.avatar:
            embed.add_field(
                name="Global Avatar", value=f"[View]({user.avatar.url})", inline=True
            )

        await ctx.send(embed=embed)

    @commands.hybrid_command(name="servericon", description="Get the server's icon")
    async def servericon(self, ctx: commands.Context):
        if not is_module_enabled(ctx.guild.id, "utility"):
            await ctx.send(embed=error("The utility module is disabled on this server."))
            return

        if not ctx.guild.icon:
            await ctx.send(embed=error("This server doesn't have an icon."))
            return

        embed = discord.Embed(
            title=f"{ctx.guild.name}'s Icon", color=discord.Color.blurple()
        )
        embed.set_image(url=ctx.guild.icon.url)

        # Add links to different formats
        links = []
        for fmt in ["png", "jpg", "webp"]:
            links.append(
                f"[{fmt.upper()}]({ctx.guild.icon.replace(format=fmt, size=1024)})"
            )
        if ctx.guild.icon.is_animated():
            links.append(f"[GIF]({ctx.guild.icon.replace(format='gif', size=1024)})")

        embed.description = " | ".join(links)
        await ctx.send(embed=embed)


    @commands.hybrid_command(name="cc", description="Run a custom command")
    @app_commands.describe(
        name="The custom command to run",
        arguments="Optional arguments to pass to the command",
    )
    @app_commands.autocomplete(name=custom_command_autocomplete)
    async def cc_command(self, ctx: commands.Context, name: str, *, arguments: str = ""):
        custom_cmd = db.get_custom_command(ctx.guild.id, name)
        if not custom_cmd:
            await ctx.send(embed=error(f"Custom command `{name}` not found."))
            return

        if not custom_cmd.get("enabled", True):
            await ctx.send(embed=error(f"Custom command `{name}` is disabled."))
            return

        args = arguments.split() if arguments else []
        await ctx.send(embed=success(f"Running `{name}`..."), ephemeral=True)
        await handle_custom_command(self.bot, ctx.message, custom_cmd, args)


    @commands.hybrid_command(name="help", description="Show all available commands")
    @app_commands.describe(command="Get detailed help for a specific command")
    async def help_command(self, ctx: commands.Context, command: str = None):
        prefix = db.get_prefix(ctx.guild.id) if ctx.guild else "a!"

        if command:
            # Special case: show custom commands
            if command.lower() == "cc" or command.lower() == "customcommands":
                custom_cmds = db.get_all_custom_commands(ctx.guild.id)
                if not custom_cmds:
                    await ctx.send(embed=info("This server has no custom commands."))
                    return

                enabled_cmds = [c for c in custom_cmds if c.get("enabled", True)]
                if not enabled_cmds:
                    await ctx.send(embed=info("This server has no enabled custom commands."))
                    return

                embed = discord.Embed(
                    title="Custom Commands",
                    description=f"This server has **{len(enabled_cmds)}** custom command{'s' if len(enabled_cmds) != 1 else ''}:",
                    color=discord.Color.purple(),
                )

                for c in enabled_cmds[:25]:  # Limit to 25 to avoid embed limits
                    desc = c.get("description") or "No description"
                    embed.add_field(name=f"`{prefix}{c['name']}`", value=desc, inline=False)

                if len(enabled_cmds) > 25:
                    embed.set_footer(text=f"Showing 25 of {len(enabled_cmds)} commands")

                await ctx.send(embed=embed)
                return

            # Detailed help data for specific commands
            DETAILED_HELP = {
                "roles": {
                    "description": "List all roles in the server or filter by criteria.",
                    "usages": [
                        f"{prefix}roles",
                        f"{prefix}roles [user]",
                        f"{prefix}roles bots",
                        f"{prefix}roles mentionable",
                        f"{prefix}roles unmentionable",
                        f"{prefix}roles hoisted",
                        f"{prefix}roles unhoisted",
                        f"{prefix}roles [search]",
                    ],
                    "examples": [
                        f"{prefix}roles",
                        f"{prefix}roles @HolsterJr10",
                        f"{prefix}roles bots",
                        f"{prefix}roles mod",
                    ],
                },
                "editrole": {
                    "description": "Edit a role's properties.",
                    "usages": [
                        f"{prefix}editrole [role] name [new_name]",
                        f"{prefix}editrole [role] color [hex|random|clear]",
                        f"{prefix}editrole [role] hoist",
                        f"{prefix}editrole [role] mentionable",
                        f"{prefix}editrole [role] position above [other_role]",
                        f"{prefix}editrole [role] position below [other_role]",
                        f"{prefix}editrole [role] position [number]",
                        f"{prefix}editrole [role] permissions [perm1, perm2|clear]",
                    ],
                    "examples": [
                        f"{prefix}editrole Member name Verified Member",
                        f"{prefix}editrole Member color #66CCFF",
                        f"{prefix}editrole Member color random",
                        f"{prefix}editrole Staff hoist",
                        f"{prefix}editrole Member mentionable",
                        f"{prefix}editrole Member position above Unverified",
                        f"{prefix}editrole Unverified permissions send_messages, attach_files",
                    ],
                },
                "members": {
                    "description": "View members who have one or more roles, optionally excluding others.",
                    "usages": [
                        f"{prefix}members [role]",
                        f"{prefix}members [role], [role2]",
                        f"{prefix}members [role] not [excluded_role]",
                        f"{prefix}members [role], [role2] not [excluded], [excluded2]",
                    ],
                    "examples": [
                        f"{prefix}members Support",
                        f"{prefix}members Support, Senior Support",
                        f"{prefix}members Support not Core Team",
                        f"{prefix}members Support, Senior Support not Core Team, Admin",
                    ],
                },
                "clean": {
                    "description": "Remove all of a user's messages across every channel in the server within a timeframe.",
                    "usages": [
                        f"{prefix}clean [user] [timeframe]",
                    ],
                    "examples": [
                        f"{prefix}clean @user 1h",
                        f"{prefix}clean @user 6h",
                        f"{prefix}clean @user 1d",
                        f"{prefix}clean @user 3d",
                    ],
                },
                "sticky": {
                    "description": "Pin a message that stays at the bottom of a channel.",
                    "usages": [
                        f"{prefix}sticky set [message]",
                        f"{prefix}sticky remove",
                        f"{prefix}sticky view",
                    ],
                    "examples": [
                        f"{prefix}sticky set Read the rules before posting!",
                        f"{prefix}sticky remove",
                    ],
                },
                "reminder": {
                    "description": "Set reminders that DM you when they expire.",
                    "usages": [
                        f"{prefix}reminder set [time] [message]",
                        f"{prefix}reminder list",
                        f"{prefix}reminder cancel [id]",
                    ],
                    "examples": [
                        f"{prefix}reminder set 1h Check on ticket",
                        f"{prefix}reminder set 2d Submit report",
                        f"{prefix}reminder list",
                    ],
                },
                "loa": {
                    "description": "Leave of Absence system for staff.",
                    "usages": [
                        f"{prefix}loa request [duration] [reason]",
                        f"{prefix}loa status [user]",
                        f"{prefix}loa active",
                        f"{prefix}loa admin @user",
                    ],
                    "examples": [
                        f"{prefix}loa request 3d Going on vacation",
                        f"{prefix}loa status",
                        f"{prefix}loa admin @HolsterJr10",
                    ],
                },
            }

            cmd_name = command.lower()
            cmd = self.bot.get_command(cmd_name)
            if not cmd:
                await ctx.send(embed=error(f"Command `{command}` not found."))
                return

            detailed = DETAILED_HELP.get(cmd_name)

            if detailed:
                embed = discord.Embed(
                    title=f"Command: {prefix}{cmd.name}",
                    description=detailed["description"],
                    color=discord.Color.blurple(),
                )
                embed.add_field(
                    name="Usages",
                    value="\n".join(detailed["usages"]),
                    inline=False,
                )
                embed.add_field(
                    name="Examples",
                    value="\n".join(detailed["examples"]),
                    inline=False,
                )
            else:
                embed = discord.Embed(
                    title=f"Command: {prefix}{cmd.name}",
                    description=cmd.description or "No description available.",
                    color=discord.Color.blurple(),
                )
                usage = f"{prefix}{cmd.name}"
                if cmd.signature:
                    usage += f" {cmd.signature}"
                embed.add_field(name="Usage", value=f"`{usage}`", inline=False)

            if cmd.aliases:
                embed.add_field(
                    name="Aliases",
                    value=", ".join(f"`{a}`" for a in cmd.aliases),
                    inline=False,
                )

            embed.set_footer(text="Apex")
            await ctx.send(embed=embed)
            return

        categories = {
            "moderation": {
                "name": "Moderation",
                "commands": [
                    ("warn <user> <reason>", "Warn a user"),
                    ("warnings <user>", "View warnings"),
                    ("clearwarnings <user>", "Clear all warnings"),
                    ("kick <user> [reason]", "Kick a user"),
                    ("ban <user> [reason]", "Ban a user (flags: -s, -d, -t)"),
                    ("unban <user>", "Unban a user"),
                    ("softban <user> [reason]", "Ban + unban (clears messages)"),
                    ("timeout <user> <dur> [reason]", "Timeout a user"),
                    ("mute <user> <dur> [reason]", "Mute a user (timeout)"),
                    ("unmute <user>", "Remove timeout"),
                    ("purge <amount> [flags]", "Delete messages (-u, -c, -b)"),
                    ("slowmode <seconds>", "Set slowmode"),
                    ("lock", "Lock channel"),
                    ("unlock", "Unlock channel"),
                    ("nick <user> <name>", "Change nickname"),
                    ("role <user> <role>", "Add/remove role"),
                    ("modlogs [user]", "View mod logs"),
                    ("modstats <user>", "View mod stats"),
                    ("case <number>", "View a case"),
                    ("reason <case> <reason>", "Edit case reason"),
                    ("void <case>", "Delete a case"),
                    ("lb", "Mod leaderboard"),
                    ("transferlogs <from> <to>", "Transfer mod logs"),
                    ("clean <user> <time>", "Remove user msgs across server"),
                    ("editrole <role> <action>", "Edit role name/color/hoist/perms"),
                ],
            },
            "tickets": {
                "name": "Tickets",
                "commands": [
                    ("ticketpanel <id>", "Send a ticket panel"),
                    ("close [flags]", "Close ticket (-b, -s, -nt, -nl)"),
                    ("closerequest [reason]", "Request ticket close"),
                    ("claim", "Claim ticket"),
                    ("unclaim", "Release claim"),
                    ("transfer <user>", "Transfer to staff"),
                    ("add <user>", "Add user to ticket"),
                    ("remove <user>", "Remove user"),
                    ("addrole <role>", "Add role to ticket"),
                    ("removerole <role>", "Remove role from ticket"),
                    ("rename <name>", "Rename ticket"),
                    ("switchtype <type>", "Switch ticket type"),
                    ("priority <low/med/high/urgent>", "Set ticket priority"),
                    ("ticketstats", "View ticket statistics"),
                    ("ticketlimit <number>", "Set max tickets per user"),
                    ("snippet create/delete/list", "Manage canned responses"),
                ],
            },
            "utility": {
                "name": "Utility",
                "commands": [
                    ("userinfo [user]", "User information"),
                    ("serverinfo", "Server information"),
                    ("avatar [user]", "User avatar"),
                    ("servericon", "Server icon"),
                    ("ping", "Bot latency"),
                    ("roleinfo <role>", "Role details"),
                    ("roles [filter]", "List roles (bots/hoisted/search)"),
                    ("members <role> [not role]", "Members with/without roles"),
                    ("serverid", "Server ID"),
                    ("userid [user]", "User ID"),
                    ("roleid <role>", "Role ID"),
                    ("channelid [channel]", "Channel ID"),
                    ("copyemoji <emoji>", "Copy emoji to server"),
                    ("poll <question>", "Create a poll"),
                    ("endpoll <msg_id>", "End a poll early"),
                    ("say <message>", "Bot sends a message"),
                    ("embed <json>", "Send a custom embed"),
                    ("reminder set <time> <msg>", "Set a reminder"),
                    ("reminder list", "View your reminders"),
                    ("reminder cancel <id>", "Cancel a reminder"),
                    ("sticky set/remove/view", "Sticky messages"),
                    ("cc <name>", "Run a custom command"),
                ],
            },
            "community": {
                "name": "Community",
                "commands": [
                    ("suggest <text>", "Submit suggestion"),
                    ("suggestion <action> <id>", "Manage suggestion (staff)"),
                    ("afk [reason]", "Set AFK status"),
                    ("giveaway <dur> <win> <prize>", "Start giveaway"),
                    ("gend <message_id>", "End giveaway early"),
                    ("greroll <message_id>", "Reroll winners"),
                    ("invites [user]", "Check invite stats"),
                    ("inviteleaderboard", "Top inviters"),
                    ("whoinvited <user>", "Who invited someone"),
                ],
            },
            "leveling": {
                "name": "Leveling",
                "commands": [
                    ("level rank [user]", "Check level and XP"),
                    ("level leaderboard", "XP leaderboard"),
                    ("level set <user> <level>", "Set user level (admin)"),
                    ("level reset <user>", "Reset user XP (admin)"),
                    ("level addrole <level> <role>", "Add level role reward"),
                    ("level removerole <level>", "Remove level role"),
                    ("level roles", "View level roles"),
                ],
            },
            "voice": {
                "name": "Voice Channels",
                "commands": [
                    ("vc setup [channel]", "Create generator channel"),
                    ("vc remove <channel>", "Remove generator"),
                    ("vc list", "View generators"),
                    ("vc control", "Manage your temp channel"),
                    ("vc kick <user>", "Kick from your channel"),
                    ("vc transfer <user>", "Transfer ownership"),
                ],
            },
            "settings": {
                "name": "Settings",
                "commands": [
                    ("config [setting] [value]", "Quick server config"),
                    ("plugin list/enable/disable", "Manage plugins"),
                    ("prefix <new>", "Change prefix"),
                    ("modmail setup/disable", "Manage modmail"),
                    ("reply <message>", "Reply to modmail (anon)"),
                    ("dashboard", "Dashboard link"),
                    ("invite", "Bot invite link"),
                    ("autoresponder", "Manage auto-responders"),
                    ("toggleresponder <id>", "Toggle auto-responder"),
                    ("staff", "View admin/mod roles"),
                    ("help [command]", "Show commands"),
                    ("reactionrole", "Manage reaction roles"),
                ],
            },
        }

        view = HelpView(categories, prefix, ctx.author.id)
        overview = view.children[0]._build_overview()
        await ctx.send(embed=overview, view=view)

    # Dev help command (owner only, not hybrid/slash)
    @commands.command(name="devhelp", hidden=True)
    @commands.is_owner()
    async def devhelp(self, ctx: commands.Context):
        prefix = db.get_prefix(ctx.guild.id) if ctx.guild else "a!"

        embed = discord.Embed(
            title="Developer Commands",
            color=discord.Color.dark_red(),
        )

        embed.add_field(
            name="Jishaku",
            value="\n".join(f"`{prefix}{c}` — {d}" for c, d in [
                ("jsk py <code>", "Execute Python"),
                ("jsk sh <command>", "Shell command"),
                ("jsk git <command>", "Git commands"),
                ("jsk sync", "Sync slash commands"),
                ("jsk shutdown", "Shutdown bot"),
                ("jsk load/unload/reload <ext>", "Manage extensions"),
                ("jsk debug <command>", "Debug command"),
                ("jsk source <command>", "View source"),
                ("jsk tasks", "View running tasks"),
            ]),
            inline=False,
        )

        embed.add_field(
            name="Bot Management",
            value="\n".join(f"`{prefix}{c}` — {d}" for c, d in [
                ("blacklist <user> [reason]", "Blacklist user globally"),
                ("unblacklist <user>", "Remove from blacklist"),
                ("forceleave <guild_id>", "Leave a server"),
                ("synccommands", "Sync commands globally"),
                ("clearmodlogs", "Clear all mod logs (this server)"),
            ]),
            inline=False,
        )

        embed.add_field(
            name="Staff Commands",
            value="\n".join(f"`{prefix}{c}` — {d}" for c, d in [
                ("staffinfo", "View server info (staff)"),
                ("stafflookup <user>", "Lookup user across servers"),
                ("botstats", "Bot statistics"),
            ]),
            inline=False,
        )

        embed.add_field(
            name="Developer Tools",
            value="\n".join(f"`{prefix}{c}` — {d}" for c, d in [
                ("sentry <error_id>", "Look up error details"),
                ("devcodeauth", "Generate dev auth code"),
                ("import-circle <channel> [limit]", "Import Circle punishments"),
            ]),
            inline=False,
        )

        embed.add_field(
            name="Dashboards",
            value=f"[Dev Portal](https://apex-systems.vercel.app/dev) — Full admin panel\n"
                  f"[Support Portal](https://apex-systems.vercel.app/support) — Staff tools\n"
                  f"[Status](https://apex-systems.vercel.app/status) — System status",
            inline=False,
        )

        embed.set_footer(text="Hidden from regular users")
        await ctx.send(embed=embed)


    @commands.hybrid_command(name="poll", description="Create a poll with up to 5 options")
    @app_commands.describe(
        question="The poll question",
        option1="First option",
        option2="Second option",
        option3="Third option (optional)",
        option4="Fourth option (optional)",
        option5="Fifth option (optional)",
        duration="Duration in minutes (optional, default: no limit)",
    )
    async def poll(
        self,
        ctx: commands.Context,
        question: str,
        option1: str,
        option2: str,
        option3: str = None,
        option4: str = None,
        option5: str = None,
        duration: int = None,
    ):
        if not is_module_enabled(ctx.guild.id, "utility"):
            await ctx.send(embed=error("The utility module is disabled on this server."))
            return
        options = [opt for opt in [option1, option2, option3, option4, option5] if opt]

        if len(options) < 2:
            await ctx.send(embed=error("You need at least 2 options for a poll."))
            return

        end_time = None
        if duration:
            end_time = datetime.utcnow() + timedelta(minutes=duration)

        view = PollView(options, end_time)

        embed = discord.Embed(
            title=question,
            description="Click a button to vote!",
            color=discord.Color.blue(),
        )
        embed.add_field(
            name="Options", value="\n".join(f"\u2022 {opt}" for opt in options), inline=False
        )
        if duration:
            embed.set_footer(text=f"Poll ends in {duration} minutes")
        embed.timestamp = datetime.utcnow()

        msg = await ctx.send(embed=embed, view=view)

        if duration:
            active_polls[msg.id] = (view, msg, question)
            await asyncio.sleep(duration * 60)
            if msg.id in active_polls:
                view.ended = True
                for child in view.children:
                    child.disabled = True

                results_embed = discord.Embed(
                    title="Poll Ended: " + question,
                    description=view.get_results(),
                    color=discord.Color.green(),
                )
                results_embed.timestamp = datetime.utcnow()
                await msg.edit(embed=results_embed, view=view)
                del active_polls[msg.id]

    @commands.hybrid_command(name="endpoll", description="End a poll early and show results")
    @app_commands.describe(message_id="The message ID of the poll to end")
    async def endpoll(self, ctx: commands.Context, message_id: str):
        if not is_module_enabled(ctx.guild.id, "utility"):
            await ctx.send(embed=error("The utility module is disabled on this server."))
            return
        try:
            msg_id = int(message_id)
        except ValueError:
            await ctx.send(embed=error("Invalid message ID."))
            return

        if msg_id not in active_polls:
            await ctx.send(embed=error("Poll not found or already ended."))
            return

        view, msg, question = active_polls[msg_id]
        view.ended = True
        for child in view.children:
            child.disabled = True

        results_embed = discord.Embed(
            title="Poll Ended: " + question,
            description=view.get_results(),
            color=discord.Color.green(),
        )
        results_embed.set_footer(text=f"Ended by {ctx.author}")
        results_embed.timestamp = datetime.utcnow()
        await msg.edit(embed=results_embed, view=view)
        del active_polls[msg_id]
        await ctx.send(embed=success("Poll ended!"), ephemeral=True)


    @commands.hybrid_command(name="say", description="Make the bot send a message")
    @app_commands.describe(
        channel="Channel to send the message in (defaults to current)",
        message="The message to send",
    )
    async def say(
        self, ctx: commands.Context, channel: discord.TextChannel = None, *, message: str
    ):
        if not is_module_enabled(ctx.guild.id, "utility"):
            await ctx.send(embed=error("The utility module is disabled on this server."))
            return
        if not has_mod_role(ctx.author):
            await ctx.send(embed=error("You don't have permission to use this command."))
            return

        target_channel = channel or ctx.channel

        # Check bot permissions in target channel
        if not target_channel.permissions_for(ctx.guild.me).send_messages:
            await ctx.send(
                embed=error(f"I don't have permission to send messages in {target_channel.mention}.")
            )
            return

        # For slash commands, respond first then send the message
        if ctx.interaction is not None:
            await ctx.send(embed=success(f"Message sent to {target_channel.mention}!"), ephemeral=True)
            await target_channel.send(message)
        else:
            # For text commands, send message then delete the command
            await target_channel.send(message)
            try:
                await ctx.message.delete()
            except discord.Forbidden:
                pass

    @commands.hybrid_command(name="embed", description="Send a customizable embed message")
    @app_commands.describe(
        title="The title of the embed",
        description="The description/main content of the embed",
        channel="Channel to send the embed in (defaults to current)",
        color="Hex color code (e.g., #ff0000 or ff0000)",
        footer="Footer text",
        thumbnail="URL for thumbnail image (small, top-right)",
        image="URL for main image (large, bottom)",
        author="Author name shown at the top",
    )
    async def embed_cmd(
        self,
        ctx: commands.Context,
        title: str = None,
        description: str = None,
        channel: discord.TextChannel = None,
        color: str = None,
        footer: str = None,
        thumbnail: str = None,
        image: str = None,
        author: str = None,
    ):
        if not is_module_enabled(ctx.guild.id, "utility"):
            await ctx.send(embed=error("The utility module is disabled on this server."))
            return
        if not has_mod_role(ctx.author):
            await ctx.send(embed=error("You don't have permission to use this command."))
            return

        # Require at least title or description
        if not title and not description:
            await ctx.send(
                embed=error("You must provide at least a title or description for the embed."),
                ephemeral=True,
            )
            return

        target_channel = channel or ctx.channel

        # Check bot permissions in target channel
        if not target_channel.permissions_for(ctx.guild.me).send_messages:
            await ctx.send(
                embed=error(f"I don't have permission to send messages in {target_channel.mention}.")
            )
            return
        if not target_channel.permissions_for(ctx.guild.me).embed_links:
            await ctx.send(
                embed=error(f"I don't have permission to send embeds in {target_channel.mention}.")
            )
            return

        # Parse color
        embed_color = discord.Color.blue()  # Default color
        if color:
            # Remove # if present
            color = color.lstrip("#")
            try:
                embed_color = discord.Color(int(color, 16))
            except ValueError:
                await ctx.send(
                    embed=error("Invalid color format. Use hex code like `#ff0000` or `ff0000`."),
                    ephemeral=True,
                )
                return

        # Build the embed
        embed = discord.Embed(color=embed_color)

        if title:
            embed.title = title
        if description:
            embed.description = description
        if footer:
            embed.set_footer(text=footer)
        if author:
            embed.set_author(name=author)
        if thumbnail:
            embed.set_thumbnail(url=thumbnail)
        if image:
            embed.set_image(url=image)

        # For slash commands, respond first then send the embed
        if ctx.interaction is not None:
            await ctx.send(embed=success(f"Embed sent to {target_channel.mention}!"), ephemeral=True)
            await target_channel.send(embed=embed)
        else:
            # For text commands, send embed then delete the command
            await target_channel.send(embed=embed)
            try:
                await ctx.message.delete()
            except discord.Forbidden:
                pass

    # Reminder system
    @commands.hybrid_group(name="reminder", description="Set and manage reminders")
    async def reminder(self, ctx):
        if ctx.invoked_subcommand is None:
            p = db.get_prefix(ctx.guild.id) if ctx.guild else "a!"
            await ctx.send(embed=info(
                f"`{p}reminder set <time> <message>` — Set a reminder\n"
                f"`{p}reminder list` — View your reminders\n"
                f"`{p}reminder cancel <id>` — Cancel a reminder"
            ))

    @reminder.command(name="set", description="Set a reminder")
    @app_commands.describe(time="Duration (e.g. 10m, 1h, 1d)", message="What to remind you about")
    async def reminder_set(self, ctx, time: str, *, message: str):
        parsed = parse_duration(time)
        if not parsed:
            return await ctx.send(embed=error("Invalid time. Use `10m`, `1h`, `1d`, etc."))

        delta, dur_text = parsed
        if delta.total_seconds() > 30 * 86400:
            return await ctx.send(embed=error("Max reminder is 30 days."))
        if delta.total_seconds() < 30:
            return await ctx.send(embed=error("Min reminder is 30 seconds."))

        remind_at = (datetime.now(timezone.utc) + delta).isoformat()
        rid = db.create_reminder(ctx.author.id, ctx.guild.id if ctx.guild else None, ctx.channel.id, message, remind_at)

        await ctx.send(embed=success(f"I'll DM you in **{dur_text}**.\n> {message}\n\n-# ID: `{rid[:8]}`"))

    @reminder.command(name="list", description="View your reminders")
    async def reminder_list(self, ctx):
        reminders = db.get_user_reminders(ctx.author.id)
        if not reminders:
            return await ctx.send(embed=info("You have no reminders."))

        lines = []
        for r in reminders[:10]:
            try:
                dt = datetime.fromisoformat(r["remind_at"])
                ts = f"<t:{int(dt.timestamp())}:R>"
            except:
                ts = r["remind_at"]
            lines.append(f"`{r['id'][:8]}` — {ts} — {r['message'][:50]}")

        embed = discord.Embed(description="\n".join(lines), color=discord.Color.blurple())
        embed.set_author(name=f"Your Reminders ({len(reminders)})")
        embed.set_footer(text="Apex")
        await ctx.send(embed=embed)

    @reminder.command(name="cancel", description="Cancel a reminder")
    @app_commands.describe(reminder_id="Reminder ID (first 8 characters)")
    async def reminder_cancel(self, ctx, reminder_id: str):
        reminders = db.get_user_reminders(ctx.author.id)
        match = next((r for r in reminders if r["id"].startswith(reminder_id)), None)
        if not match:
            return await ctx.send(embed=error("Reminder not found."))

        db.delete_reminder(match["id"])
        await ctx.send(embed=success("Reminder cancelled."))


    @commands.hybrid_command(name="invite", description="Get the bot invite link")
    async def invite(self, ctx: commands.Context):
        embed = discord.Embed(
            title="Invite Apex",
            description="Click the link below to invite Apex to your server.",
            color=discord.Color.blurple(),
        )
        embed.add_field(
            name="Invite Link",
            value="[Click here to invite](https://discord.com/oauth2/authorize?client_id=1461965918228713472&permissions=8&integration_type=0&scope=bot)",
            inline=False,
        )

        await ctx.send(embed=embed)


    PLUGINS = {
        "moderation": {
            "name": "Moderation",
            "description": "Ban, kick, warn, timeout, purge, and mod logging.",
            "default": True,
        },
        "tickets": {
            "name": "Tickets",
            "description": "Multi-panel ticket system with transcripts and staff tools.",
            "default": True,
        },
        "reaction_roles": {
            "name": "Reaction Roles",
            "description": "Button and dropdown role menus.",
            "default": True,
        },
        "utility": {
            "name": "Utility",
            "description": "User info, server info, polls, reminders, and more.",
            "default": True,
        },
        "leveling": {
            "name": "Leveling",
            "description": "XP-based leveling with role rewards and leaderboards.",
            "default": False,
        },
        "giveaways": {
            "name": "Giveaways",
            "description": "Host giveaways with button entries and role requirements.",
            "default": False,
        },
        "voice_channels": {
            "name": "Voice Channels",
            "description": "Join-to-create temporary voice channels.",
            "default": False,
        },
        "role_persistence": {
            "name": "Role Persistence",
            "description": "Re-assign roles when members leave and rejoin.",
            "default": False,
        },
        "starboard": {
            "name": "Starboard",
            "description": "Pin popular messages to a starboard channel.",
            "default": False,
        },
        "loa": {
            "name": "Leave of Absence",
            "description": "Staff LOA request and approval system.",
            "default": False,
        },
    }

    @commands.hybrid_command(name="plugin", description="Manage bot plugins")
    @app_commands.describe(action="Action: list, enable, disable, or info", plugin="Plugin name")
    async def plugin(self, ctx: commands.Context, action: str = "list", *, plugin: str = None):
        """Manage bot plugins. Usage: a!plugin list/enable/disable/info [plugin]"""
        action = action.lower()

        if action == "list":
            settings = db.get_guild_settings(ctx.guild.id)
            modules = settings.get("modules", {})

            lines = []
            for key, meta in self.PLUGINS.items():
                enabled = modules.get(key, meta["default"])
                status = "on" if enabled else "off"
                indicator = "\u2713" if enabled else "\u2717"
                lines.append(f"{indicator}  **{meta['name']}** \u2014 {status}")

            embed = discord.Embed(
                description="\n".join(lines),
                color=discord.Color.blurple(),
            )
            embed.set_author(name="Plugins")
            embed.set_footer(text="a!plugin enable/disable <name>")
            await ctx.send(embed=embed)

        elif action in ("enable", "on"):
            if not has_admin_role(ctx.author):
                await ctx.send(embed=error("You need admin permissions to manage plugins."))
                return
            if not plugin:
                await ctx.send(embed=error("Specify a plugin. Use `a!plugin list` to see all."))
                return
            key = plugin.lower().replace(" ", "_").replace("-", "_")
            if key not in self.PLUGINS:
                matches = [k for k in self.PLUGINS if key in k or key in self.PLUGINS[k]["name"].lower()]
                if len(matches) == 1:
                    key = matches[0]
                else:
                    await ctx.send(embed=error(f"Unknown plugin: `{plugin}`. Use `a!plugin list` to see all."))
                    return

            settings = db.get_guild_settings(ctx.guild.id)
            modules = settings.get("modules", {})
            modules[key] = True
            db.update_guild_settings(ctx.guild.id, {"modules": modules})

            meta = self.PLUGINS[key]
            await ctx.send(embed=success(f"**{meta['name']}** has been enabled."))

        elif action in ("disable", "off"):
            if not has_admin_role(ctx.author):
                await ctx.send(embed=error("You need admin permissions to manage plugins."))
                return
            if not plugin:
                await ctx.send(embed=error("Specify a plugin. Use `a!plugin list` to see all."))
                return
            key = plugin.lower().replace(" ", "_").replace("-", "_")
            if key not in self.PLUGINS:
                matches = [k for k in self.PLUGINS if key in k or key in self.PLUGINS[k]["name"].lower()]
                if len(matches) == 1:
                    key = matches[0]
                else:
                    await ctx.send(embed=error(f"Unknown plugin: `{plugin}`. Use `a!plugin list` to see all."))
                    return

            settings = db.get_guild_settings(ctx.guild.id)
            modules = settings.get("modules", {})
            modules[key] = False
            db.update_guild_settings(ctx.guild.id, {"modules": modules})

            meta = self.PLUGINS[key]
            await ctx.send(embed=success(f"**{meta['name']}** has been disabled."))

        elif action == "info":
            if not plugin:
                await ctx.send(embed=error("Specify a plugin. Use `a!plugin list` to see all."))
                return
            key = plugin.lower().replace(" ", "_").replace("-", "_")
            if key not in self.PLUGINS:
                matches = [k for k in self.PLUGINS if key in k or key in self.PLUGINS[k]["name"].lower()]
                if len(matches) == 1:
                    key = matches[0]
                else:
                    await ctx.send(embed=error(f"Unknown plugin: `{plugin}`. Use `a!plugin list` to see all."))
                    return

            meta = self.PLUGINS[key]
            settings = db.get_guild_settings(ctx.guild.id)
            modules = settings.get("modules", {})
            enabled = modules.get(key, meta["default"])

            embed = discord.Embed(
                title=meta["name"],
                description=meta["description"],
                color=discord.Color.green() if enabled else discord.Color.red(),
            )
            embed.add_field(name="Status", value="Enabled" if enabled else "Disabled", inline=True)
            embed.add_field(name="Default", value="On" if meta["default"] else "Off", inline=True)
            embed.add_field(name="ID", value=f"`{key}`", inline=True)
            await ctx.send(embed=embed)

        else:
            await ctx.send(embed=info("**Usage:**\n`a!plugin list` - View all plugins\n`a!plugin enable <name>` - Enable a plugin\n`a!plugin disable <name>` - Disable a plugin\n`a!plugin info <name>` - View plugin details"))


    @commands.hybrid_command(name="config", description="Quick server configuration")
    @app_commands.describe(setting="Setting to view or change", value="New value (leave empty to view current)")
    async def config(self, ctx: commands.Context, setting: str = None, *, value: str = None):
        """Quick server config. Usage: a!config <setting> [value]"""
        if not setting:
            settings = db.get_guild_settings(ctx.guild.id)
            prefix = settings.get("prefix", "a!")
            modlog = settings.get("mod_log_channel")
            welcome_ch = settings.get("welcome_channel")
            goodbye_ch = settings.get("goodbye_channel")
            autorole = settings.get("auto_role")
            dm_on_mod = settings.get("dm_on_moderation", True)
            sb = settings.get("starboard", {})
            sb_channel = sb.get("channel_id")
            sb_threshold = sb.get("threshold", 3)

            lines = [
                f"**prefix** \u2014 `{prefix}`",
                f"**modlog** \u2014 {f'<#{modlog}>' if modlog else 'Not set'}",
                f"**welcome** \u2014 {f'<#{welcome_ch}>' if welcome_ch else 'Not set'}",
                f"**goodbye** \u2014 {f'<#{goodbye_ch}>' if goodbye_ch else 'Not set'}",
                f"**autorole** \u2014 {f'<@&{autorole}>' if autorole else 'Not set'}",
                f"**dm_on_mod** \u2014 {'on' if dm_on_mod else 'off'}",
                f"**starboard** \u2014 {f'<#{sb_channel}>' if sb_channel else 'Not set'}",
                f"**starboard_threshold** \u2014 {sb_threshold}",
            ]

            embed = discord.Embed(description="\n".join(lines), color=discord.Color.blurple())
            embed.set_author(name="Server Config")
            embed.set_footer(text=f"a!config <setting> <value> to change")
            await ctx.send(embed=embed)
            return

        setting = setting.lower().replace("-", "_")

        # View current value if no value provided
        if value is None:
            settings = db.get_guild_settings(ctx.guild.id)
            current = None

            if setting == "prefix":
                current = f"`{settings.get('prefix', 'a!')}`"
            elif setting in ("modlog", "mod_log", "mod_log_channel"):
                ch = settings.get("mod_log_channel")
                current = f"<#{ch}>" if ch else "Not set"
            elif setting in ("welcome", "welcome_channel"):
                ch = settings.get("welcome_channel")
                current = f"<#{ch}>" if ch else "Not set"
            elif setting in ("welcome_message", "welcome_msg"):
                current = settings.get("welcome_message") or "Not set"
            elif setting in ("goodbye", "goodbye_channel"):
                ch = settings.get("goodbye_channel")
                current = f"<#{ch}>" if ch else "Not set"
            elif setting in ("goodbye_message", "goodbye_msg"):
                current = settings.get("goodbye_message") or "Not set"
            elif setting in ("autorole", "auto_role"):
                role = settings.get("auto_role")
                current = f"<@&{role}>" if role else "Not set"
            elif setting in ("dm_on_mod", "dm"):
                current = "on" if settings.get("dm_on_moderation", True) else "off"
            else:
                await ctx.send(embed=error(f"Unknown setting: `{setting}`\nRun `a!config` to see all settings."))
                return

            await ctx.send(embed=info(f"**{setting}** is currently set to {current}"))
            return

        # Set value - requires admin
        if not has_admin_role(ctx.author):
            await ctx.send(embed=error("You need admin permissions to change settings."))
            return

        if setting == "prefix":
            if len(value) > 5:
                await ctx.send(embed=error("Prefix must be 5 characters or less."))
                return
            db.update_guild_settings(ctx.guild.id, {"prefix": value})
            await ctx.send(embed=success(f"Prefix set to `{value}`"))

        elif setting in ("modlog", "mod_log", "mod_log_channel"):
            channel = self._resolve_channel(ctx, value)
            if not channel:
                await ctx.send(embed=error("Channel not found."))
                return
            db.update_guild_settings(ctx.guild.id, {"mod_log_channel": str(channel.id)})
            await ctx.send(embed=success(f"Mod log channel set to {channel.mention}"))

        elif setting in ("welcome", "welcome_channel"):
            if value.lower() in ("none", "off", "disable", "reset"):
                db.update_guild_settings(ctx.guild.id, {"welcome_channel": None})
                await ctx.send(embed=success("Welcome channel cleared."))
                return
            channel = self._resolve_channel(ctx, value)
            if not channel:
                await ctx.send(embed=error("Channel not found."))
                return
            db.update_guild_settings(ctx.guild.id, {"welcome_channel": str(channel.id)})
            await ctx.send(embed=success(f"Welcome channel set to {channel.mention}"))

        elif setting in ("welcome_message", "welcome_msg"):
            if value.lower() in ("none", "off", "disable", "reset"):
                db.update_guild_settings(ctx.guild.id, {"welcome_message": None})
                await ctx.send(embed=success("Welcome message cleared."))
                return
            db.update_guild_settings(ctx.guild.id, {"welcome_message": value})
            preview = value.replace("{user}", ctx.author.mention).replace("{username}", ctx.author.name).replace("{server}", ctx.guild.name).replace("{membercount}", str(ctx.guild.member_count))
            await ctx.send(embed=success(f"Welcome message set.\n\n**Preview:** {preview}"))

        elif setting in ("goodbye", "goodbye_channel"):
            if value.lower() in ("none", "off", "disable", "reset"):
                db.update_guild_settings(ctx.guild.id, {"goodbye_channel": None})
                await ctx.send(embed=success("Goodbye channel cleared."))
                return
            channel = self._resolve_channel(ctx, value)
            if not channel:
                await ctx.send(embed=error("Channel not found."))
                return
            db.update_guild_settings(ctx.guild.id, {"goodbye_channel": str(channel.id)})
            await ctx.send(embed=success(f"Goodbye channel set to {channel.mention}"))

        elif setting in ("goodbye_message", "goodbye_msg"):
            if value.lower() in ("none", "off", "disable", "reset"):
                db.update_guild_settings(ctx.guild.id, {"goodbye_message": None})
                await ctx.send(embed=success("Goodbye message cleared."))
                return
            db.update_guild_settings(ctx.guild.id, {"goodbye_message": value})
            preview = value.replace("{user}", ctx.author.mention).replace("{username}", ctx.author.name).replace("{server}", ctx.guild.name).replace("{membercount}", str(ctx.guild.member_count))
            await ctx.send(embed=success(f"Goodbye message set.\n\n**Preview:** {preview}"))

        elif setting in ("autorole", "auto_role"):
            if value.lower() in ("none", "off", "disable", "reset"):
                db.update_guild_settings(ctx.guild.id, {"auto_role": None})
                await ctx.send(embed=success("Auto-role cleared."))
                return
            role = self._resolve_role(ctx, value)
            if not role:
                await ctx.send(embed=error("Role not found."))
                return
            db.update_guild_settings(ctx.guild.id, {"auto_role": str(role.id)})
            await ctx.send(embed=success(f"Auto-role set to {role.mention}"))

        elif setting in ("dm_on_mod", "dm"):
            if value.lower() in ("on", "true", "yes", "enable"):
                db.update_guild_settings(ctx.guild.id, {"dm_on_moderation": True})
                await ctx.send(embed=success("DM on moderation **enabled**."))
            elif value.lower() in ("off", "false", "no", "disable"):
                db.update_guild_settings(ctx.guild.id, {"dm_on_moderation": False})
                await ctx.send(embed=success("DM on moderation **disabled**."))
            else:
                await ctx.send(embed=error("Use `on` or `off`."))

        elif setting in ("starboard", "starboard_channel"):
            if value.lower() in ("none", "off", "disable", "reset"):
                settings = db.get_guild_settings(ctx.guild.id)
                sb = settings.get("starboard", {})
                sb["channel_id"] = None
                db.update_guild_settings(ctx.guild.id, {"starboard": sb})
                await ctx.send(embed=success("Starboard channel cleared."))
                return
            channel = self._resolve_channel(ctx, value)
            if not channel:
                await ctx.send(embed=error("Channel not found."))
                return
            settings = db.get_guild_settings(ctx.guild.id)
            sb = settings.get("starboard", {})
            sb["channel_id"] = str(channel.id)
            db.update_guild_settings(ctx.guild.id, {"starboard": sb})
            await ctx.send(embed=success(f"Starboard channel set to {channel.mention}"))

        elif setting in ("starboard_threshold", "star_threshold", "stars"):
            try:
                threshold = int(value)
                if threshold < 1 or threshold > 25:
                    await ctx.send(embed=error("Threshold must be between 1 and 25."))
                    return
                settings = db.get_guild_settings(ctx.guild.id)
                sb = settings.get("starboard", {})
                sb["threshold"] = threshold
                db.update_guild_settings(ctx.guild.id, {"starboard": sb})
                await ctx.send(embed=success(f"Starboard threshold set to **{threshold}** stars."))
            except ValueError:
                await ctx.send(embed=error("Threshold must be a number."))

        elif setting in ("loa_channel", "loa_log"):
            if value.lower() in ("none", "off", "disable", "reset"):
                settings = db.get_guild_settings(ctx.guild.id)
                loa = settings.get("loa", {})
                loa["log_channel"] = None
                db.update_guild_settings(ctx.guild.id, {"loa": loa})
                await ctx.send(embed=success("LOA log channel cleared."))
                return
            channel = self._resolve_channel(ctx, value)
            if not channel:
                await ctx.send(embed=error("Channel not found."))
                return
            settings = db.get_guild_settings(ctx.guild.id)
            loa = settings.get("loa", {})
            loa["log_channel"] = str(channel.id)
            loa["enabled"] = True
            db.update_guild_settings(ctx.guild.id, {"loa": loa})
            await ctx.send(embed=success(f"LOA log channel set to {channel.mention}"))

        elif setting in ("loa_role",):
            if value.lower() in ("none", "off", "disable", "reset"):
                settings = db.get_guild_settings(ctx.guild.id)
                loa = settings.get("loa", {})
                loa["role"] = None
                db.update_guild_settings(ctx.guild.id, {"loa": loa})
                await ctx.send(embed=success("LOA role cleared."))
                return
            role = self._resolve_role(ctx, value)
            if not role:
                await ctx.send(embed=error("Role not found."))
                return
            settings = db.get_guild_settings(ctx.guild.id)
            loa = settings.get("loa", {})
            loa["role"] = str(role.id)
            db.update_guild_settings(ctx.guild.id, {"loa": loa})
            await ctx.send(embed=success(f"LOA role set to {role.mention}"))

        elif setting in ("loa_approver", "loa_approver_role"):
            if value.lower() in ("none", "off", "disable", "reset"):
                settings = db.get_guild_settings(ctx.guild.id)
                loa = settings.get("loa", {})
                loa["approver_role"] = None
                db.update_guild_settings(ctx.guild.id, {"loa": loa})
                await ctx.send(embed=success("LOA approver role cleared."))
                return
            role = self._resolve_role(ctx, value)
            if not role:
                await ctx.send(embed=error("Role not found."))
                return
            settings = db.get_guild_settings(ctx.guild.id)
            loa = settings.get("loa", {})
            loa["approver_role"] = str(role.id)
            db.update_guild_settings(ctx.guild.id, {"loa": loa})
            await ctx.send(embed=success(f"LOA approver role set to {role.mention}"))

        elif setting in ("loa_max_days",):
            try:
                days = int(value)
                if days < 1 or days > 365:
                    await ctx.send(embed=error("Must be between 1 and 365."))
                    return
                settings = db.get_guild_settings(ctx.guild.id)
                loa = settings.get("loa", {})
                loa["max_days"] = days
                db.update_guild_settings(ctx.guild.id, {"loa": loa})
                await ctx.send(embed=success(f"LOA max days set to **{days}**."))
            except ValueError:
                await ctx.send(embed=error("Must be a number."))

        else:
            await ctx.send(embed=error(f"Unknown setting: `{setting}`\nRun `a!config` to see all settings."))

    def _resolve_channel(self, ctx: commands.Context, value: str):
        """Resolve a channel from mention, ID, or name."""
        # Mention
        match = re.match(r"<#(\d+)>", value)
        if match:
            return ctx.guild.get_channel(int(match.group(1)))
        # ID
        if value.isdigit():
            return ctx.guild.get_channel(int(value))
        # Name
        return discord.utils.find(lambda c: c.name.lower() == value.lower(), ctx.guild.text_channels)

    def _resolve_role(self, ctx: commands.Context, value: str):
        """Resolve a role from mention, ID, or name."""
        match = re.match(r"<@&(\d+)>", value)
        if match:
            return ctx.guild.get_role(int(match.group(1)))
        if value.isdigit():
            return ctx.guild.get_role(int(value))
        return discord.utils.find(lambda r: r.name.lower() == value.lower(), ctx.guild.roles)


async def setup(bot):
    await bot.add_cog(Utility(bot))
