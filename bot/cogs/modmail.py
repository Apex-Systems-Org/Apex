import discord
from discord.ext import commands
from discord import app_commands
from datetime import datetime, timezone

from database import db
from helpers import has_mod_role, has_admin_role
from helpers.embeds import success, error, warning, info, mod_embed


class Modmail(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    def _get_modmail_settings(self, guild_id):
        settings = db.get_guild_settings(guild_id)
        return settings.get("modmail", {})

    async def _find_modmail_guild(self, user: discord.User):
        """Find which guild has modmail enabled and the user is a member of."""
        for guild in self.bot.guilds:
            member = guild.get_member(user.id)
            if not member:
                continue
            mm = self._get_modmail_settings(guild.id)
            if mm.get("enabled") and mm.get("category_id"):
                return guild, mm
        return None, None

    async def _find_existing_thread(self, guild: discord.Guild, user_id: int, category_id: int):
        """Find an existing modmail channel for a user."""
        category = guild.get_channel(category_id)
        if not category:
            return None
        for channel in category.text_channels:
            if channel.topic and str(user_id) in channel.topic:
                return channel
        return None

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        # Only handle DMs from non-bots
        if message.author.bot:
            return
        if message.guild is not None:
            return

        # Check if this is a reply in a modmail channel
        # (handled separately - this is for incoming DMs)
        guild, mm = await self._find_modmail_guild(message.author)
        if not guild or not mm:
            return

        category_id = int(mm["category_id"])
        category = guild.get_channel(category_id)
        if not category:
            return

        # Find or create modmail thread
        channel = await self._find_existing_thread(guild, message.author.id, category_id)

        if not channel:
            # Create new modmail channel
            channel_name = f"mail-{message.author.name}"[:100]
            try:
                # Get support roles for permissions
                overwrites = {
                    guild.default_role: discord.PermissionOverwrite(read_messages=False),
                    guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True),
                }

                # Add mod roles
                settings = db.get_guild_settings(guild.id)
                for role_id in settings.get("mod_roles", []):
                    role = guild.get_role(int(role_id))
                    if role:
                        overwrites[role] = discord.PermissionOverwrite(read_messages=True, send_messages=True)
                for role_id in settings.get("admin_roles", []):
                    role = guild.get_role(int(role_id))
                    if role:
                        overwrites[role] = discord.PermissionOverwrite(read_messages=True, send_messages=True)

                channel = await category.create_text_channel(
                    name=channel_name,
                    topic=f"Modmail thread for {message.author} ({message.author.id})",
                    overwrites=overwrites,
                )

                # Send opener embed
                member = guild.get_member(message.author.id)
                open_embed = discord.Embed(
                    title="New Modmail Thread",
                    color=discord.Color.blurple(),
                    timestamp=datetime.now(timezone.utc),
                )
                open_embed.add_field(name="User", value=f"{message.author.mention} ({message.author.id})", inline=True)
                if member:
                    open_embed.add_field(name="Joined", value=f"<t:{int(member.joined_at.timestamp())}:R>", inline=True)
                    open_embed.add_field(name="Roles", value=", ".join(r.mention for r in member.roles[1:][:5]) or "None", inline=False)
                open_embed.set_thumbnail(url=message.author.display_avatar.url)
                open_embed.set_footer(text="Apex Modmail")
                await channel.send(embed=open_embed)

                # Notify user
                await message.author.send(embed=info("Your message has been sent to the staff team. They will reply here."))

            except discord.Forbidden:
                return
            except Exception as e:
                print(f"Modmail error creating channel: {e}")
                return

        # Forward the message to the modmail channel
        fwd_embed = discord.Embed(
            description=message.content or "*No text content*",
            color=discord.Color.blurple(),
            timestamp=datetime.now(timezone.utc),
        )
        fwd_embed.set_author(name=str(message.author), icon_url=message.author.display_avatar.url)

        files = []
        for att in message.attachments:
            files.append(await att.to_file())

        await channel.send(embed=fwd_embed, files=files if files else None)
        await message.add_reaction("\u2709")

    @commands.Cog.listener("on_message")
    async def on_staff_reply(self, message: discord.Message):
        """Forward staff replies to the user."""
        if message.author.bot or not message.guild:
            return

        # Check if this channel is in a modmail category
        if not message.channel.category:
            return

        mm = self._get_modmail_settings(message.guild.id)
        if not mm.get("enabled") or not mm.get("category_id"):
            return

        if message.channel.category_id != int(mm["category_id"]):
            return

        # This is a modmail channel - get the user ID from topic
        if not message.channel.topic:
            return

        # Extract user ID from topic
        import re
        match = re.search(r"\((\d{17,20})\)", message.channel.topic)
        if not match:
            return

        user_id = int(match.group(1))

        # Don't forward commands
        settings = db.get_guild_settings(message.guild.id)
        prefix = settings.get("prefix", "a!")
        if message.content.startswith(prefix) or message.content.startswith("/"):
            return

        # Forward to user
        try:
            user = await self.bot.fetch_user(user_id)
            reply_embed = discord.Embed(
                description=message.content or "*No text content*",
                color=discord.Color.green(),
                timestamp=datetime.now(timezone.utc),
            )
            reply_embed.set_author(name=f"{message.author.display_name} (Staff)", icon_url=message.author.display_avatar.url)
            reply_embed.set_footer(text=message.guild.name, icon_url=message.guild.icon.url if message.guild.icon else None)

            files = []
            for att in message.attachments:
                files.append(await att.to_file())

            await user.send(embed=reply_embed, files=files if files else None)
            await message.add_reaction("\u2705")
        except discord.Forbidden:
            await message.channel.send(embed=error("Could not DM this user. They may have DMs disabled."), delete_after=5)
        except discord.NotFound:
            await message.channel.send(embed=error("User not found."), delete_after=5)

    @commands.hybrid_command(name="modmail", description="Set up modmail")
    @app_commands.describe(action="Action: setup, disable, or status")
    async def modmail_cmd(self, ctx: commands.Context, action: str = "status"):
        """Manage modmail. Usage: a!modmail setup/disable/status"""
        if not has_admin_role(ctx.author):
            await ctx.send(embed=error("You need admin permissions."))
            return

        action = action.lower()

        if action == "status":
            mm = self._get_modmail_settings(ctx.guild.id)
            if mm.get("enabled") and mm.get("category_id"):
                cat = ctx.guild.get_channel(int(mm["category_id"]))
                cat_name = cat.name if cat else "Unknown"
                await ctx.send(embed=info(f"Modmail is **enabled**.\nCategory: **{cat_name}**\n\nUsers can DM the bot to contact staff."))
            else:
                await ctx.send(embed=info("Modmail is **disabled**.\nUse `a!modmail setup` to enable it."))

        elif action == "setup":
            # Create or find modmail category
            mm = self._get_modmail_settings(ctx.guild.id)
            if mm.get("enabled") and mm.get("category_id"):
                cat = ctx.guild.get_channel(int(mm["category_id"]))
                if cat:
                    await ctx.send(embed=info(f"Modmail is already set up in **{cat.name}**."))
                    return

            # Create category
            try:
                category = await ctx.guild.create_category(
                    name="Modmail",
                    overwrites={
                        ctx.guild.default_role: discord.PermissionOverwrite(read_messages=False),
                        ctx.guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True, manage_channels=True),
                    },
                )
                db.update_guild_settings(ctx.guild.id, {
                    "modmail": {"enabled": True, "category_id": str(category.id)}
                })
                await ctx.send(embed=success(f"Modmail enabled. Category **{category.name}** created.\n\nUsers can now DM the bot to contact staff."))
            except discord.Forbidden:
                await ctx.send(embed=error("I don't have permission to create categories."))

        elif action in ("disable", "off"):
            db.update_guild_settings(ctx.guild.id, {
                "modmail": {"enabled": False, "category_id": None}
            })
            await ctx.send(embed=success("Modmail disabled."))

        else:
            await ctx.send(embed=info("**Usage:**\n`a!modmail setup` - Enable modmail\n`a!modmail disable` - Disable modmail\n`a!modmail status` - Check status"))

    @commands.hybrid_command(name="reply", description="Reply to a modmail thread")
    @app_commands.describe(message="Message to send to the user")
    async def reply(self, ctx: commands.Context, *, message: str):
        """Reply to a modmail thread (anonymous)."""
        if not has_mod_role(ctx.author):
            await ctx.send(embed=error("You don't have permission."))
            return

        if not ctx.channel.category:
            await ctx.send(embed=error("This is not a modmail channel."))
            return

        mm = self._get_modmail_settings(ctx.guild.id)
        if not mm.get("category_id") or ctx.channel.category_id != int(mm["category_id"]):
            await ctx.send(embed=error("This is not a modmail channel."))
            return

        if not ctx.channel.topic:
            await ctx.send(embed=error("Could not find user ID in channel topic."))
            return

        import re
        match = re.search(r"\((\d{17,20})\)", ctx.channel.topic)
        if not match:
            await ctx.send(embed=error("Could not find user ID in channel topic."))
            return

        user_id = int(match.group(1))
        try:
            user = await self.bot.fetch_user(user_id)
            reply_embed = discord.Embed(
                description=message,
                color=discord.Color.green(),
                timestamp=datetime.now(timezone.utc),
            )
            reply_embed.set_author(name=f"Staff Response", icon_url=ctx.guild.icon.url if ctx.guild.icon else None)
            reply_embed.set_footer(text=ctx.guild.name)
            await user.send(embed=reply_embed)

            # Confirm in channel
            confirm_embed = discord.Embed(
                description=message,
                color=discord.Color.green(),
                timestamp=datetime.now(timezone.utc),
            )
            confirm_embed.set_author(name=f"{ctx.author.display_name} (Anonymous Reply)", icon_url=ctx.author.display_avatar.url)
            await ctx.send(embed=confirm_embed)

            # Delete the command message
            try:
                await ctx.message.delete()
            except:
                pass

        except discord.Forbidden:
            await ctx.send(embed=error("Could not DM this user."))
        except discord.NotFound:
            await ctx.send(embed=error("User not found."))


async def setup(bot):
    await bot.add_cog(Modmail(bot))
