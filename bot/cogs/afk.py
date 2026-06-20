import discord
from discord.ext import commands
from discord import app_commands

from database import db
from helpers.embeds import success, error


# Default AFK settings
DEFAULT_AFK = {"enabled": True, "max_reason_length": 100}


def get_afk_settings(guild_id: int) -> dict:
    settings = db.get_guild_settings(guild_id)
    afk_settings = settings.get("afk_settings", {})
    return {**DEFAULT_AFK, **afk_settings}


class AFKCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.hybrid_command(name="afk", description="Set your AFK status")
    @app_commands.describe(reason="Reason for being AFK (optional)")
    async def afk_cmd(self, ctx: commands.Context, *, reason: str = "AFK"):
        afk_settings = get_afk_settings(ctx.guild.id)
        if not afk_settings["enabled"]:
            await ctx.send(embed=error("AFK is disabled on this server."))
            return

        # Truncate reason if too long
        max_len = afk_settings["max_reason_length"]
        if len(reason) > max_len:
            reason = reason[:max_len] + "..."

        db.set_afk(ctx.guild.id, ctx.author.id, reason)
        await ctx.send(embed=success(f"{ctx.author.mention}, I've set your AFK: {reason}"))


# AFK handling in on_message is added below


async def setup(bot):
    await bot.add_cog(AFKCog(bot))
