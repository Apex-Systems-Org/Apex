import discord
from discord.ext import commands
from discord import app_commands
from datetime import datetime
from collections import defaultdict
import random

from database import db
from helpers.embeds import success, error, info
from helpers.utils import is_module_enabled
from helpers.checks import has_admin_role


# Cooldown tracker for XP: {guild_id: {user_id: last_xp_timestamp}}
leveling_cooldowns: dict = defaultdict(dict)
LEVELING_XP_COOLDOWN = 60  # 60 seconds between XP gains
LEVELING_XP_MIN = 15
LEVELING_XP_MAX = 25


async def process_leveling_xp(message: discord.Message):
    """Process XP gain for a message."""
    guild_id = message.guild.id
    user_id = message.author.id

    # Check cooldown
    now = datetime.utcnow().timestamp()
    last_xp = leveling_cooldowns[guild_id].get(user_id, 0)
    if now - last_xp < LEVELING_XP_COOLDOWN:
        return

    # Update cooldown
    leveling_cooldowns[guild_id][user_id] = now

    # Get leveling settings
    settings = db.get_guild_settings(guild_id)
    leveling_settings = settings.get("leveling", {})

    # Check if channel is ignored
    ignored_channels = leveling_settings.get("ignored_channels", [])
    if str(message.channel.id) in ignored_channels:
        return

    # Check if user has ignored role
    ignored_roles = leveling_settings.get("ignored_roles", [])
    if any(str(role.id) in ignored_roles for role in message.author.roles):
        return

    # Give random XP
    xp_min = leveling_settings.get("xp_min", LEVELING_XP_MIN)
    xp_max = leveling_settings.get("xp_max", LEVELING_XP_MAX)
    xp_amount = random.randint(xp_min, xp_max)

    # Apply XP multiplier if set
    xp_multiplier = leveling_settings.get("xp_multiplier", 1.0)
    xp_amount = int(xp_amount * xp_multiplier)

    # Add XP
    result = db.add_xp(guild_id, user_id, xp_amount)

    # Check for level up
    if result["level_up"]:
        await handle_level_up(message, result)


async def handle_level_up(message: discord.Message, result: dict):
    """Handle a user leveling up."""
    guild_id = message.guild.id
    user = message.author
    new_level = result["level"]

    settings = db.get_guild_settings(guild_id)
    leveling_settings = settings.get("leveling", {})

    # Send level up message
    announce_channel_id = leveling_settings.get("announce_channel")
    announce_message = leveling_settings.get(
        "level_up_message", "{user} reached **Level {level}**!"
    )

    # Replace placeholders
    announce_text = announce_message.replace("{user}", user.mention)
    announce_text = announce_text.replace("{username}", user.name)
    announce_text = announce_text.replace("{level}", str(new_level))
    announce_text = announce_text.replace("{server}", message.guild.name)

    try:
        if announce_channel_id:
            channel = message.guild.get_channel(int(announce_channel_id))
            if channel:
                await channel.send(embed=info(announce_text))
        else:
            # Send in same channel
            await message.channel.send(embed=info(announce_text), delete_after=10)
    except:
        pass

    # Check for level roles
    level_roles = db.get_level_roles(guild_id)
    for lr in level_roles:
        if lr["level"] <= new_level:
            role = message.guild.get_role(int(lr["role_id"]))
            if role and role not in user.roles:
                try:
                    await user.add_roles(role, reason=f"Reached level {new_level}")
                except:
                    pass


# Leveling command group
class LevelingCommands(
    commands.GroupCog, name="level", description="Leveling commands"
):
    def __init__(self, bot):
        self.bot = bot
        super().__init__()
        # Store reference on bot so it can be accessed from on_message
        self.bot.process_leveling_xp = process_leveling_xp

    @app_commands.command(name="rank", description="Check your level and XP")
    @app_commands.describe(user="User to check (leave empty for yourself)")
    async def rank(self, interaction: discord.Interaction, user: discord.Member = None):
        if not is_module_enabled(interaction.guild.id, "leveling"):
            await interaction.response.send_message(
                embed=error("The leveling module is disabled on this server.")
            )
            return
        target = user or interaction.user
        data = db.get_user_level(interaction.guild.id, target.id)
        rank_num = db.get_user_rank(interaction.guild.id, target.id)
        current_level = data["level"]
        current_xp = data["xp"]
        xp_for_current = db._xp_for_level(current_level)
        xp_for_next = db._xp_for_level(current_level + 1)
        xp_progress = current_xp - xp_for_current
        xp_needed = xp_for_next - xp_for_current
        progress_percent = min(xp_progress / xp_needed, 1.0) if xp_needed > 0 else 0
        bar_length = 20
        filled = int(bar_length * progress_percent)
        bar = "\u2588" * filled + "\u2591" * (bar_length - filled)
        embed = discord.Embed(
            title=f"{target.display_name}'s Rank",
            color=(
                target.color
                if target.color != discord.Color.default()
                else discord.Color.blurple()
            ),
        )
        embed.add_field(name="Rank", value=f"#{rank_num}", inline=True)
        embed.add_field(name="Level", value=str(current_level), inline=True)
        embed.add_field(name="Total XP", value=f"{current_xp:,}", inline=True)
        embed.add_field(
            name="Progress",
            value=f"{bar}\n{xp_progress:,} / {xp_needed:,} XP",
            inline=False,
        )
        embed.add_field(
            name="Messages", value=f"{data['total_messages']:,}", inline=True
        )
        embed.set_thumbnail(url=target.display_avatar.url)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(
        name="leaderboard", description="View the leveling leaderboard"
    )
    @app_commands.describe(page="Page number")
    async def levels(self, interaction: discord.Interaction, page: int = 1):
        if not is_module_enabled(interaction.guild.id, "leveling"):
            await interaction.response.send_message(
                embed=error("The leveling module is disabled on this server.")
            )
            return
        per_page = 10
        leaderboard = db.get_level_leaderboard(interaction.guild.id, limit=100)
        if not leaderboard:
            await interaction.response.send_message(
                embed=info("No leveling data yet. Start chatting to earn XP!")
            )
            return
        total_pages = (len(leaderboard) + per_page - 1) // per_page
        page = max(1, min(page, total_pages))
        offset = (page - 1) * per_page
        embed = discord.Embed(
            title=f"{interaction.guild.name} Leaderboard", color=discord.Color.gold()
        )
        lines = []
        for i, entry in enumerate(
            leaderboard[offset : offset + per_page], start=offset + 1
        ):
            user = interaction.guild.get_member(int(entry["user_id"]))
            name = user.display_name if user else f"User {entry['user_id']}"
            medal = f"**{i}.**"
            lines.append(
                f"{medal} {name} - Level {entry['level']} ({entry['xp']:,} XP)"
            )
        embed.description = "\n".join(lines) if lines else "No data"
        embed.set_footer(text=f"Page {page}/{total_pages}")
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="set", description="Set a user's level (admin)")
    @app_commands.describe(user="User to set level for", level="New level")
    async def setlevel(
        self, interaction: discord.Interaction, user: discord.Member, level: int
    ):
        if not is_module_enabled(interaction.guild.id, "leveling"):
            await interaction.response.send_message(
                embed=error("The leveling module is disabled on this server.")
            )
            return
        if not has_admin_role(interaction.user):
            await interaction.response.send_message(
                embed=error("You need admin permissions to use this command.")
            )
            return
        if level < 0 or level > 1000:
            await interaction.response.send_message(embed=error("Level must be between 0 and 1000."))
            return
        db.set_user_level(interaction.guild.id, user.id, level)
        if interaction.guild.me.guild_permissions.manage_roles:
            for lr in db.get_level_roles(interaction.guild.id):
                role = interaction.guild.get_role(int(lr["role_id"]))
                if role and lr["level"] <= level and role not in user.roles and role < interaction.guild.me.top_role:
                    await user.add_roles(role, reason=f"Level manually set to {level}")
        await interaction.response.send_message(
            embed=success(f"Set {user.mention}'s level to **{level}**.")
        )

    @app_commands.command(
        name="reset", description="Reset a user's leveling data (admin)"
    )
    @app_commands.describe(user="User to reset")
    async def resetlevel(self, interaction: discord.Interaction, user: discord.Member):
        if not is_module_enabled(interaction.guild.id, "leveling"):
            await interaction.response.send_message(
                embed=error("The leveling module is disabled on this server.")
            )
            return
        if not has_admin_role(interaction.user):
            await interaction.response.send_message(
                embed=error("You need admin permissions to use this command.")
            )
            return
        db.reset_user_level(interaction.guild.id, user.id)
        await interaction.response.send_message(
            embed=success(f"Reset {user.mention}'s leveling data.")
        )

    @app_commands.command(
        name="addrole", description="Add a role reward for reaching a level (admin)"
    )
    @app_commands.describe(level="Level required", role="Role to give")
    async def addlevelrole(
        self, interaction: discord.Interaction, level: int, role: discord.Role
    ):
        if not is_module_enabled(interaction.guild.id, "leveling"):
            await interaction.response.send_message(
                embed=error("The leveling module is disabled on this server.")
            )
            return
        if not has_admin_role(interaction.user):
            await interaction.response.send_message(
                embed=error("You need admin permissions to use this command.")
            )
            return
        if level < 1 or level > 1000:
            await interaction.response.send_message(embed=error("Level must be between 1 and 1000."))
            return
        db.set_level_role(interaction.guild.id, level, role.id)
        await interaction.response.send_message(
            embed=success(f"Users will now receive {role.mention} when they reach level **{level}**.")
        )

    @app_commands.command(
        name="removerole", description="Remove a level role reward (admin)"
    )
    @app_commands.describe(level="Level to remove role from")
    async def removelevelrole(self, interaction: discord.Interaction, level: int):
        if not is_module_enabled(interaction.guild.id, "leveling"):
            await interaction.response.send_message(
                embed=error("The leveling module is disabled on this server.")
            )
            return
        if not has_admin_role(interaction.user):
            await interaction.response.send_message(
                embed=error("You need admin permissions to use this command.")
            )
            return
        db.remove_level_role(interaction.guild.id, level)
        await interaction.response.send_message(
            embed=success(f"Removed level role for level **{level}**.")
        )

    @app_commands.command(name="roles", description="View all level role rewards")
    async def levelroles(self, interaction: discord.Interaction):
        if not is_module_enabled(interaction.guild.id, "leveling"):
            await interaction.response.send_message(
                embed=error("The leveling module is disabled on this server.")
            )
            return
        roles = db.get_level_roles(interaction.guild.id)
        if not roles:
            await interaction.response.send_message(
                embed=info("No level roles configured. Admins can use `/level addrole` to add some.")
            )
            return
        embed = discord.Embed(title="Level Role Rewards", color=discord.Color.blurple())
        lines = []
        for lr in roles:
            role = interaction.guild.get_role(int(lr["role_id"]))
            role_text = role.mention if role else f"Unknown Role ({lr['role_id']})"
            lines.append(f"Level **{lr['level']}** -> {role_text}")
        embed.description = "\n".join(lines)
        await interaction.response.send_message(embed=embed)


async def setup(bot):
    await bot.add_cog(LevelingCommands(bot))
