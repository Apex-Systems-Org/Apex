import discord
from discord.ext import commands
from discord import app_commands
from database import db
from helpers import has_mod_role
from helpers.embeds import success, error, info


class Sticky(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._cache = {}

    def _get_stickies(self, guild_id):
        settings = db.get_guild_settings(guild_id)
        return settings.get("stickies", {})

    def _save_stickies(self, guild_id, stickies):
        db.update_guild_settings(guild_id, {"stickies": stickies})

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot or not message.guild:
            return


        ch_id = str(message.channel.id)
        stickies = self._get_stickies(message.guild.id)
        sticky = stickies.get(ch_id)
        if not sticky:
            return

        # Delete the old sticky message
        old_msg_id = self._cache.get(ch_id)
        if old_msg_id:
            try:
                old_msg = await message.channel.fetch_message(old_msg_id)
                await old_msg.delete()
            except:
                pass

        # Re-send the sticky
        embed = discord.Embed(
            description=sticky["content"],
            color=discord.Color.blue(),
        )
        embed.set_footer(text="Sticky Message")
        sent = await message.channel.send(embed=embed)
        self._cache[ch_id] = sent.id

    @commands.hybrid_group(name="sticky", description="Sticky messages")
    async def sticky(self, ctx):
        if ctx.invoked_subcommand is None:
            p = db.get_prefix(ctx.guild.id) if ctx.guild else "a!"
            await ctx.send(embed=info(
                f"`{p}sticky set <message>` — Pin a sticky message\n"
                f"`{p}sticky remove` — Remove the sticky\n"
                f"`{p}sticky view` — View current sticky"
            ))

    @sticky.command(name="set", description="Set a sticky message in this channel")
    @app_commands.describe(message="The message to stick")
    async def sticky_set(self, ctx, *, message: str):
        if not has_mod_role(ctx.author):
            return await ctx.send(embed=error("No permission."))

        stickies = self._get_stickies(ctx.guild.id)
        ch_id = str(ctx.channel.id)

        # Delete old sticky message if exists
        old_msg_id = self._cache.get(ch_id)
        if old_msg_id:
            try:
                old_msg = await ctx.channel.fetch_message(old_msg_id)
                await old_msg.delete()
            except:
                pass

        stickies[ch_id] = {"content": message}
        self._save_stickies(ctx.guild.id, stickies)

        embed = discord.Embed(description=message, color=discord.Color.blue())
        embed.set_footer(text="Sticky Message")
        sent = await ctx.channel.send(embed=embed)
        self._cache[ch_id] = sent.id

        await ctx.send(embed=success("Sticky message set."), delete_after=5)

    @sticky.command(name="remove", description="Remove the sticky message from this channel")
    async def sticky_remove(self, ctx):
        if not has_mod_role(ctx.author):
            return await ctx.send(embed=error("No permission."))

        stickies = self._get_stickies(ctx.guild.id)
        ch_id = str(ctx.channel.id)

        if ch_id not in stickies:
            return await ctx.send(embed=error("No sticky message in this channel."))

        del stickies[ch_id]
        self._save_stickies(ctx.guild.id, stickies)

        old_msg_id = self._cache.pop(ch_id, None)
        if old_msg_id:
            try:
                old_msg = await ctx.channel.fetch_message(old_msg_id)
                await old_msg.delete()
            except:
                pass

        await ctx.send(embed=success("Sticky message removed."))

    @sticky.command(name="view", description="View the current sticky message")
    async def sticky_view(self, ctx):
        stickies = self._get_stickies(ctx.guild.id)
        ch_id = str(ctx.channel.id)
        sticky = stickies.get(ch_id)

        if not sticky:
            return await ctx.send(embed=info("No sticky message in this channel."))

        embed = discord.Embed(description=sticky["content"], color=discord.Color.blue())
        embed.set_footer(text="Sticky Message")
        await ctx.send(embed=embed)


async def setup(bot):
    await bot.add_cog(Sticky(bot))
