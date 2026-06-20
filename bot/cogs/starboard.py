import discord
from discord.ext import commands
from datetime import datetime, timezone

from database import db
from helpers.embeds import success, error, info
from helpers.utils import is_module_enabled


class Starboard(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    def _get_starboard_settings(self, guild_id):
        settings = db.get_guild_settings(guild_id)
        return settings.get("starboard", {})

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        if not payload.guild_id:
            return

        if not is_module_enabled(payload.guild_id, "starboard"):
            return

        sb = self._get_starboard_settings(payload.guild_id)
        if not sb.get("channel_id"):
            return

        emoji = sb.get("emoji", "\u2b50")
        threshold = sb.get("threshold", 3)

        if str(payload.emoji) != emoji and payload.emoji.name != emoji:
            return

        guild = self.bot.get_guild(payload.guild_id)
        if not guild:
            return

        channel = guild.get_channel(payload.channel_id)
        if not channel:
            return

        if payload.channel_id == int(sb["channel_id"]):
            return

        try:
            message = await channel.fetch_message(payload.message_id)
        except:
            return

        count = 0
        for reaction in message.reactions:
            if str(reaction.emoji) == emoji or getattr(reaction.emoji, 'name', None) == emoji:
                count = reaction.count
                break

        if count < threshold:
            return

        starboard_channel = guild.get_channel(int(sb["channel_id"]))
        if not starboard_channel:
            return

        # Check if already posted
        existing = db.get_starboard_post(str(guild.id), str(message.id))
        if existing:
            try:
                sb_msg = await starboard_channel.fetch_message(int(existing["starboard_message_id"]))
                await sb_msg.edit(content=f"{emoji} **{count}** | {channel.mention}")
            except discord.NotFound:
                db.delete_starboard_post(str(guild.id), str(message.id))
            except:
                pass
            return

        embed = discord.Embed(
            description=message.content[:2048] if message.content else None,
            color=discord.Color.gold(),
            timestamp=message.created_at,
        )
        embed.set_author(name=str(message.author), icon_url=message.author.display_avatar.url)

        if message.attachments:
            for att in message.attachments:
                if att.content_type and att.content_type.startswith("image/"):
                    embed.set_image(url=att.url)
                    break

        if message.embeds:
            for msg_embed in message.embeds:
                if msg_embed.image:
                    embed.set_image(url=msg_embed.image.url)
                    break

        embed.add_field(name="Source", value=f"[Jump to message]({message.jump_url})", inline=False)
        embed.set_footer(text=f"#{channel.name}")

        try:
            sb_msg = await starboard_channel.send(
                content=f"{emoji} **{count}** | {channel.mention}",
                embed=embed,
            )
            db.save_starboard_post(str(guild.id), str(message.id), str(sb_msg.id), str(channel.id))
        except discord.Forbidden:
            pass

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent):
        if not payload.guild_id:
            return

        if not is_module_enabled(payload.guild_id, "starboard"):
            return

        sb = self._get_starboard_settings(payload.guild_id)
        if not sb.get("channel_id"):
            return

        emoji = sb.get("emoji", "\u2b50")
        if str(payload.emoji) != emoji and payload.emoji.name != emoji:
            return

        existing = db.get_starboard_post(str(payload.guild_id), str(payload.message_id))
        if not existing:
            return

        guild = self.bot.get_guild(payload.guild_id)
        if not guild:
            return

        channel = guild.get_channel(payload.channel_id)
        if not channel:
            return

        try:
            message = await channel.fetch_message(payload.message_id)
        except:
            return

        count = 0
        for reaction in message.reactions:
            if str(reaction.emoji) == emoji or getattr(reaction.emoji, 'name', None) == emoji:
                count = reaction.count
                break

        starboard_channel = guild.get_channel(int(sb["channel_id"]))
        if not starboard_channel:
            return

        threshold = sb.get("threshold", 3)

        if count < threshold:
            try:
                sb_msg = await starboard_channel.fetch_message(int(existing["starboard_message_id"]))
                await sb_msg.delete()
            except:
                pass
            db.delete_starboard_post(str(payload.guild_id), str(payload.message_id))
        else:
            try:
                sb_msg = await starboard_channel.fetch_message(int(existing["starboard_message_id"]))
                await sb_msg.edit(content=f"{emoji} **{count}** | {channel.mention}")
            except:
                pass


async def setup(bot):
    await bot.add_cog(Starboard(bot))
