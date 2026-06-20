import discord
from discord.ext import commands
from datetime import datetime, timedelta, timezone
import asyncio

from helpers import send_log, send_mod_log, log_member_join
from helpers.utils import is_module_enabled
from database import db
class Logging(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_message_edit(self, before: discord.Message, after: discord.Message):
        if before.author.bot or not before.guild:
            return
        if before.content == after.content:
            return

        embed = discord.Embed(title="Message Edited", color=discord.Color.blue())
        embed.add_field(
            name="Author", value=f"{before.author.mention} ({before.author})", inline=True
        )
        embed.add_field(name="Channel", value=before.channel.mention, inline=True)
        embed.add_field(
            name="Before", value=before.content[:1024] or "No content", inline=False
        )
        embed.add_field(
            name="After", value=after.content[:1024] or "No content", inline=False
        )
        embed.set_footer(text=f"Message ID: {before.id}")

        await send_log(before.guild, "message", embed)

    @commands.Cog.listener()
    async def on_message_delete(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return

        embed = discord.Embed(title="Message Deleted", color=discord.Color.red())
        embed.add_field(
            name="Author", value=f"{message.author.mention} ({message.author})", inline=True
        )
        embed.add_field(name="Channel", value=message.channel.mention, inline=True)
        embed.add_field(
            name="Content", value=message.content[:1024] or "No content/embed", inline=False
        )
        if message.attachments:
            embed.add_field(
                name="Attachments",
                value="\n".join(a.filename for a in message.attachments),
                inline=False,
            )
        embed.set_footer(text=f"Message ID: {message.id}")

        await send_log(message.guild, "message", embed)

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        # Role changes
        if before.roles != after.roles:
            added_roles = [r for r in after.roles if r not in before.roles]
            removed_roles = [r for r in before.roles if r not in after.roles]

            if added_roles or removed_roles:
                embed = discord.Embed(
                    title="Member Roles Updated", color=discord.Color.blue()
                )
                embed.add_field(
                    name="Member", value=f"{after.mention} ({after})", inline=True
                )
                if added_roles:
                    embed.add_field(
                        name="Added",
                        value=", ".join(r.mention for r in added_roles),
                        inline=False,
                    )
                if removed_roles:
                    embed.add_field(
                        name="Removed",
                        value=", ".join(r.mention for r in removed_roles),
                        inline=False,
                    )
                embed.set_thumbnail(url=after.display_avatar.url)

                await send_log(after.guild, "member", embed)

        # Nickname changes
        if before.nick != after.nick:
            embed = discord.Embed(title="Nickname Changed", color=discord.Color.blue())
            embed.add_field(name="Member", value=f"{after.mention} ({after})", inline=True)
            embed.add_field(name="Before", value=before.nick or "None", inline=True)
            embed.add_field(name="After", value=after.nick or "None", inline=True)
            embed.set_thumbnail(url=after.display_avatar.url)

            await send_log(after.guild, "member", embed)

    @commands.Cog.listener()
    async def on_voice_state_update(
        self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState
    ):
    

        # Check if user joined a generator channel (Join to Create)
        if after.channel is not None:
            generator = db.get_voice_generator_by_channel(str(after.channel.id))
            if generator:
                # Check if voice_channels module is enabled
                if is_module_enabled(member.guild.id, "voice_channels"):
                    try:
                        # Get the name template and create channel name
                        name_template = generator.get("name_template", "{user}'s Channel")
                        channel_name = name_template.replace(
                            "{user}", member.display_name
                        ).replace("{username}", member.name)

                        # Determine category - use generator's category or the generator channel's category
                        category = None
                        if generator.get("category_id"):
                            category = member.guild.get_channel(
                                int(generator["category_id"])
                            )
                        if not category and after.channel.category:
                            category = after.channel.category

                        # Create the temporary voice channel
                        overwrites = {
                            member.guild.default_role: discord.PermissionOverwrite(
                                connect=True, speak=True
                            ),
                            member: discord.PermissionOverwrite(
                                connect=True,
                                speak=True,
                                mute_members=True,
                                deafen_members=True,
                                move_members=True,
                                manage_channels=True,
                            ),
                            member.guild.me: discord.PermissionOverwrite(
                                connect=True,
                                speak=True,
                                manage_channels=True,
                                move_members=True,
                            ),
                        }

                        new_channel = await member.guild.create_voice_channel(
                            name=channel_name,
                            category=category,
                            user_limit=generator.get("user_limit", 0),
                            bitrate=min(
                                generator.get("bitrate", 64000), member.guild.bitrate_limit
                            ),
                            overwrites=overwrites,
                            reason=f"Join to Create: {member} created a voice channel",
                        )

                        # Move the member to the new channel
                        await member.move_to(new_channel)

                        # Track the temporary channel
                        db.create_temp_voice_channel(
                            member.guild.id, new_channel.id, member.id, generator.get("id")
                        )

                    except discord.Forbidden:
                        pass  # Bot doesn't have permission
                    except Exception as e:
                        print(f"Error creating temp voice channel: {e}")

        # Check if user left a temporary voice channel (delete if empty)
        if before.channel is not None:
            temp_channel = db.get_temp_voice_channel(str(before.channel.id))
            if temp_channel:
                # Check if channel is empty
                channel = member.guild.get_channel(int(before.channel.id))
                if channel and len(channel.members) == 0:
                    try:
                        await channel.delete(
                            reason="Temporary voice channel: All users left"
                        )
                        db.delete_temp_voice_channel(str(before.channel.id))
                    except discord.Forbidden:
                        pass
                    except discord.NotFound:
                        db.delete_temp_voice_channel(str(before.channel.id))
                    except Exception as e:
                        print(f"Error deleting temp voice channel: {e}")

    
        # Join voice channel
        if before.channel is None and after.channel is not None:
            embed = discord.Embed(title="Voice Channel Joined", color=discord.Color.green())
            embed.add_field(
                name="Member", value=f"{member.mention} ({member})", inline=True
            )
            embed.add_field(name="Channel", value=after.channel.mention, inline=True)
            embed.set_thumbnail(url=member.display_avatar.url)
            await send_log(member.guild, "voice", embed)

        # Leave voice channel
        elif before.channel is not None and after.channel is None:
            embed = discord.Embed(title="Voice Channel Left", color=discord.Color.red())
            embed.add_field(
                name="Member", value=f"{member.mention} ({member})", inline=True
            )
            embed.add_field(name="Channel", value=before.channel.mention, inline=True)
            embed.set_thumbnail(url=member.display_avatar.url)
            await send_log(member.guild, "voice", embed)

        # Switch voice channel
        elif before.channel != after.channel:
            embed = discord.Embed(
                title="Voice Channel Switched", color=discord.Color.blue()
            )
            embed.add_field(
                name="Member", value=f"{member.mention} ({member})", inline=True
            )
            embed.add_field(
                name="From",
                value=before.channel.mention if before.channel else "None",
                inline=True,
            )
            embed.add_field(
                name="To",
                value=after.channel.mention if after.channel else "None",
                inline=True,
            )
            embed.set_thumbnail(url=member.display_avatar.url)
            await send_log(member.guild, "voice", embed)

        # Mute/Deafen changes
        if before.self_mute != after.self_mute:
            action = "muted" if after.self_mute else "unmuted"
            embed = discord.Embed(
                title=f"Member Self-{action.title()}", color=discord.Color.orange()
            )
            embed.add_field(
                name="Member", value=f"{member.mention} ({member})", inline=True
            )
            embed.add_field(
                name="Channel",
                value=after.channel.mention if after.channel else "None",
                inline=True,
            )
            embed.set_thumbnail(url=member.display_avatar.url)
            await send_log(member.guild, "voice", embed)

        if before.self_deaf != after.self_deaf:
            action = "deafened" if after.self_deaf else "undeafened"
            embed = discord.Embed(
                title=f"Member Self-{action.title()}", color=discord.Color.orange()
            )
            embed.add_field(
                name="Member", value=f"{member.mention} ({member})", inline=True
            )
            embed.add_field(
                name="Channel",
                value=after.channel.mention if after.channel else "None",
                inline=True,
            )
            embed.set_thumbnail(url=member.display_avatar.url)
            await send_log(member.guild, "voice", embed)

        # Server mute/deafen (by moderator)
        if before.mute != after.mute:
            action = "Server Muted" if after.mute else "Server Unmuted"
            embed = discord.Embed(
                title=action,
                color=discord.Color.red() if after.mute else discord.Color.green(),
            )
            embed.add_field(
                name="Member", value=f"{member.mention} ({member})", inline=True
            )
            embed.add_field(
                name="Channel",
                value=after.channel.mention if after.channel else "None",
                inline=True,
            )
            embed.set_thumbnail(url=member.display_avatar.url)
            await send_log(member.guild, "voice", embed)

        if before.deaf != after.deaf:
            action = "Server Deafened" if after.deaf else "Server Undeafened"
            embed = discord.Embed(
                title=action,
                color=discord.Color.red() if after.deaf else discord.Color.green(),
            )
            embed.add_field(
                name="Member", value=f"{member.mention} ({member})", inline=True
            )
            embed.add_field(
                name="Channel",
                value=after.channel.mention if after.channel else "None",
                inline=True,
            )
            embed.set_thumbnail(url=member.display_avatar.url)
            await send_log(member.guild, "voice", embed)

    @commands.Cog.listener()
    async def on_guild_channel_create(self, channel: discord.abc.GuildChannel):
        embed = discord.Embed(title="Channel Created", color=discord.Color.green())
        embed.add_field(
            name="Channel", value=f"{channel.mention} ({channel.name})", inline=True
        )
        embed.add_field(
            name="Type", value=str(channel.type).replace("_", " ").title(), inline=True
        )
        embed.add_field(name="ID", value=channel.id, inline=True)
        if hasattr(channel, "category") and channel.category:
            embed.add_field(name="Category", value=channel.category.name, inline=True)
        await send_log(channel.guild, "server", embed)

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel):
        embed = discord.Embed(title="Channel Deleted", color=discord.Color.red())
        embed.add_field(name="Channel", value=f"#{channel.name}", inline=True)
        embed.add_field(
            name="Type", value=str(channel.type).replace("_", " ").title(), inline=True
        )
        embed.add_field(name="ID", value=channel.id, inline=True)
        if hasattr(channel, "category") and channel.category:
            embed.add_field(name="Category", value=channel.category.name, inline=True)
        await send_log(channel.guild, "server", embed)

    @commands.Cog.listener()
    async def on_guild_channel_update(
        self, before: discord.abc.GuildChannel, after: discord.abc.GuildChannel
    ):
        changes = []

        if before.name != after.name:
            changes.append(f"**Name:** {before.name} -> {after.name}")

        if (
            hasattr(before, "topic")
            and hasattr(after, "topic")
            and before.topic != after.topic
        ):
            changes.append(f"**Topic:** {before.topic or 'None'} -> {after.topic or 'None'}")

        if (
            hasattr(before, "slowmode_delay")
            and hasattr(after, "slowmode_delay")
            and before.slowmode_delay != after.slowmode_delay
        ):
            changes.append(
                f"**Slowmode:** {before.slowmode_delay}s -> {after.slowmode_delay}s"
            )

        if hasattr(before, "nsfw") and hasattr(after, "nsfw") and before.nsfw != after.nsfw:
            changes.append(f"**NSFW:** {before.nsfw} -> {after.nsfw}")

        if (
            hasattr(before, "category")
            and hasattr(after, "category")
            and before.category != after.category
        ):
            changes.append(
                f"**Category:** {before.category.name if before.category else 'None'} -> {after.category.name if after.category else 'None'}"
            )

        if (
            hasattr(before, "bitrate")
            and hasattr(after, "bitrate")
            and before.bitrate != after.bitrate
        ):
            changes.append(
                f"**Bitrate:** {before.bitrate//1000}kbps -> {after.bitrate//1000}kbps"
            )

        if (
            hasattr(before, "user_limit")
            and hasattr(after, "user_limit")
            and before.user_limit != after.user_limit
        ):
            changes.append(
                f"**User Limit:** {before.user_limit or 'Unlimited'} -> {after.user_limit or 'Unlimited'}"
            )

        # Check permission overwrites
        if before.overwrites != after.overwrites:
            changes.append("**Permissions:** Updated")

        if changes:
            embed = discord.Embed(title="Channel Updated", color=discord.Color.blue())
            embed.add_field(
                name="Channel", value=f"{after.mention} ({after.name})", inline=True
            )
            embed.add_field(
                name="Type", value=str(after.type).replace("_", " ").title(), inline=True
            )
            embed.add_field(name="Changes", value="\n".join(changes)[:1024], inline=False)
            await send_log(after.guild, "server", embed)

    @commands.Cog.listener()
    async def on_guild_role_create(self, role: discord.Role):
        embed = discord.Embed(title="Role Created", color=discord.Color.green())
        embed.add_field(name="Role", value=f"{role.mention} ({role.name})", inline=True)
        embed.add_field(name="ID", value=role.id, inline=True)
        embed.add_field(name="Color", value=str(role.color), inline=True)
        embed.add_field(
            name="Mentionable", value="Yes" if role.mentionable else "No", inline=True
        )
        embed.add_field(name="Hoisted", value="Yes" if role.hoist else "No", inline=True)
        await send_log(role.guild, "server", embed)

    @commands.Cog.listener()
    async def on_guild_role_delete(self, role: discord.Role):
        embed = discord.Embed(title="Role Deleted", color=discord.Color.red())
        embed.add_field(name="Role", value=role.name, inline=True)
        embed.add_field(name="ID", value=role.id, inline=True)
        embed.add_field(name="Color", value=str(role.color), inline=True)
        await send_log(role.guild, "server", embed)

    @commands.Cog.listener()
    async def on_guild_role_update(self, before: discord.Role, after: discord.Role):
        changes = []

        if before.name != after.name:
            changes.append(f"**Name:** {before.name} -> {after.name}")

        if before.color != after.color:
            changes.append(f"**Color:** {before.color} -> {after.color}")

        if before.hoist != after.hoist:
            changes.append(f"**Hoisted:** {before.hoist} -> {after.hoist}")

        if before.mentionable != after.mentionable:
            changes.append(f"**Mentionable:** {before.mentionable} -> {after.mentionable}")

        if before.permissions != after.permissions:
            added_perms = [
                p[0]
                for p in after.permissions
                if p[1] and not dict(before.permissions).get(p[0], False)
            ]
            removed_perms = [
                p[0]
                for p in before.permissions
                if p[1] and not dict(after.permissions).get(p[0], False)
            ]
            if added_perms:
                changes.append(f"**Permissions Added:** {', '.join(added_perms[:5])}")
            if removed_perms:
                changes.append(f"**Permissions Removed:** {', '.join(removed_perms[:5])}")

        if before.position != after.position:
            changes.append(f"**Position:** {before.position} -> {after.position}")

        if changes:
            embed = discord.Embed(title="Role Updated", color=after.color)
            embed.add_field(
                name="Role", value=f"{after.mention} ({after.name})", inline=True
            )
            embed.add_field(name="ID", value=after.id, inline=True)
            embed.add_field(name="Changes", value="\n".join(changes)[:1024], inline=False)
            await send_log(after.guild, "server", embed)

    @commands.Cog.listener()
    async def on_guild_emojis_update(self, guild: discord.Guild, before: list, after: list):
        before_ids = {e.id for e in before}
        after_ids = {e.id for e in after}

        # Added emojis
        added = [e for e in after if e.id not in before_ids]
        for emoji in added:
            embed = discord.Embed(title="Emoji Added", color=discord.Color.green())
            embed.add_field(name="Emoji", value=f"{emoji} `:{emoji.name}:`", inline=True)
            embed.add_field(name="ID", value=emoji.id, inline=True)
            embed.add_field(
                name="Animated", value="Yes" if emoji.animated else "No", inline=True
            )
            embed.set_thumbnail(url=emoji.url)
            await send_log(guild, "server", embed)

        # Removed emojis
        removed = [e for e in before if e.id not in after_ids]
        for emoji in removed:
            embed = discord.Embed(title="Emoji Removed", color=discord.Color.red())
            embed.add_field(name="Emoji", value=f"`:{emoji.name}:`", inline=True)
            embed.add_field(name="ID", value=emoji.id, inline=True)
            embed.add_field(
                name="Animated", value="Yes" if emoji.animated else "No", inline=True
            )
            await send_log(guild, "server", embed)

        # Updated emojis (name change)
        before_dict = {e.id: e for e in before}
        for emoji in after:
            if emoji.id in before_dict and emoji.name != before_dict[emoji.id].name:
                embed = discord.Embed(title="Emoji Renamed", color=discord.Color.blue())
                embed.add_field(
                    name="Before", value=f"`:{before_dict[emoji.id].name}:`", inline=True
                )
                embed.add_field(
                    name="After", value=f"{emoji} `:{emoji.name}:`", inline=True
                )
                embed.add_field(name="ID", value=emoji.id, inline=True)
                embed.set_thumbnail(url=emoji.url)
                await send_log(guild, "server", embed)

    @commands.Cog.listener()
    async def on_guild_stickers_update(self, guild: discord.Guild, before: list, after: list):
        before_ids = {s.id for s in before}
        after_ids = {s.id for s in after}

        # Added stickers
        added = [s for s in after if s.id not in before_ids]
        for sticker in added:
            embed = discord.Embed(title="Sticker Added", color=discord.Color.green())
            embed.add_field(name="Name", value=sticker.name, inline=True)
            embed.add_field(name="ID", value=sticker.id, inline=True)
            embed.add_field(
                name="Description", value=sticker.description or "None", inline=False
            )
            embed.set_thumbnail(url=sticker.url)
            await send_log(guild, "server", embed)

        # Removed stickers
        removed = [s for s in before if s.id not in after_ids]
        for sticker in removed:
            embed = discord.Embed(title="Sticker Removed", color=discord.Color.red())
            embed.add_field(name="Name", value=sticker.name, inline=True)
            embed.add_field(name="ID", value=sticker.id, inline=True)
            await send_log(guild, "server", embed)

    @commands.Cog.listener()
    async def on_guild_update(self, before: discord.Guild, after: discord.Guild):
        changes = []

        if before.name != after.name:
            changes.append(f"**Name:** {before.name} -> {after.name}")

        if before.icon != after.icon:
            changes.append("**Icon:** Updated")

        if before.banner != after.banner:
            changes.append("**Banner:** Updated")

        if before.description != after.description:
            changes.append(
                f"**Description:** {before.description or 'None'} -> {after.description or 'None'}"
            )

        if before.afk_channel != after.afk_channel:
            changes.append(
                f"**AFK Channel:** {before.afk_channel.name if before.afk_channel else 'None'} -> {after.afk_channel.name if after.afk_channel else 'None'}"
            )

        if before.afk_timeout != after.afk_timeout:
            changes.append(
                f"**AFK Timeout:** {before.afk_timeout//60}min -> {after.afk_timeout//60}min"
            )

        if before.system_channel != after.system_channel:
            changes.append(
                f"**System Channel:** {before.system_channel.name if before.system_channel else 'None'} -> {after.system_channel.name if after.system_channel else 'None'}"
            )

        if before.verification_level != after.verification_level:
            changes.append(
                f"**Verification Level:** {before.verification_level.name} -> {after.verification_level.name}"
            )

        if before.explicit_content_filter != after.explicit_content_filter:
            changes.append(
                f"**Content Filter:** {before.explicit_content_filter.name} -> {after.explicit_content_filter.name}"
            )

        if before.default_notifications != after.default_notifications:
            changes.append(
                f"**Default Notifications:** {before.default_notifications.name} -> {after.default_notifications.name}"
            )

        if before.premium_tier != after.premium_tier:
            changes.append(f"**Boost Level:** {before.premium_tier} -> {after.premium_tier}")

        if before.vanity_url_code != after.vanity_url_code:
            changes.append(
                f"**Vanity URL:** {before.vanity_url_code or 'None'} -> {after.vanity_url_code or 'None'}"
            )

        if changes:
            embed = discord.Embed(title="Server Updated", color=discord.Color.blue())
            embed.add_field(name="Changes", value="\n".join(changes)[:1024], inline=False)
            if after.icon:
                embed.set_thumbnail(url=after.icon.url)
            await send_log(after, "server", embed)

    @commands.Cog.listener()
    async def on_invite_create(self, invite: discord.Invite):
        embed = discord.Embed(title="Invite Created", color=discord.Color.green())
        embed.add_field(name="Code", value=invite.code, inline=True)
        embed.add_field(
            name="Channel",
            value=invite.channel.mention if invite.channel else "Unknown",
            inline=True,
        )
        embed.add_field(
            name="Creator",
            value=(
                f"{invite.inviter.mention} ({invite.inviter})"
                if invite.inviter
                else "Unknown"
            ),
            inline=True,
        )
        embed.add_field(name="Max Uses", value=invite.max_uses or "Unlimited", inline=True)
        embed.add_field(
            name="Expires",
            value=(
                f"<t:{int((datetime.utcnow() + timedelta(seconds=invite.max_age)).timestamp())}:R>"
                if invite.max_age
                else "Never"
            ),
            inline=True,
        )
        embed.add_field(
            name="Temporary", value="Yes" if invite.temporary else "No", inline=True
        )
        await send_log(invite.guild, "server", embed)

    @commands.Cog.listener()
    async def on_invite_delete(self, invite: discord.Invite):
        embed = discord.Embed(title="Invite Deleted", color=discord.Color.red())
        embed.add_field(name="Code", value=invite.code, inline=True)
        embed.add_field(
            name="Channel",
            value=invite.channel.mention if invite.channel else "Unknown",
            inline=True,
        )
        await send_log(invite.guild, "server", embed)

    @commands.Cog.listener()
    async def on_bulk_message_delete(self, messages: list):
        if not messages:
            return

        guild = messages[0].guild
        if not guild:
            return

        channel = messages[0].channel

        embed = discord.Embed(title="Bulk Messages Deleted", color=discord.Color.red())
        embed.add_field(name="Channel", value=channel.mention, inline=True)
        embed.add_field(name="Count", value=len(messages), inline=True)

        # Show sample of deleted messages
        sample = []
        for msg in messages[:5]:
            content = msg.content[:50] + "..." if len(msg.content) > 50 else msg.content
            sample.append(f"**{msg.author}:** {content or '[embed/attachment]'}")

        if sample:
            embed.add_field(name="Sample", value="\n".join(sample), inline=False)

        await send_log(guild, "message", embed)

    @commands.Cog.listener()
    async def on_thread_create(self, thread: discord.Thread):
        embed = discord.Embed(title="Thread Created", color=discord.Color.green())
        embed.add_field(
            name="Thread", value=f"{thread.mention} ({thread.name})", inline=True
        )
        embed.add_field(
            name="Parent",
            value=thread.parent.mention if thread.parent else "Unknown",
            inline=True,
        )
        embed.add_field(name="Owner", value=f"<@{thread.owner_id}>", inline=True)
        embed.add_field(name="ID", value=thread.id, inline=True)
        await send_log(thread.guild, "server", embed)

    @commands.Cog.listener()
    async def on_thread_delete(self, thread: discord.Thread):
        embed = discord.Embed(title="Thread Deleted", color=discord.Color.red())
        embed.add_field(name="Thread", value=thread.name, inline=True)
        embed.add_field(
            name="Parent",
            value=thread.parent.mention if thread.parent else "Unknown",
            inline=True,
        )
        embed.add_field(name="ID", value=thread.id, inline=True)
        await send_log(thread.guild, "server", embed)

    @commands.Cog.listener()
    async def on_thread_update(self, before: discord.Thread, after: discord.Thread):
        changes = []

        if before.name != after.name:
            changes.append(f"**Name:** {before.name} -> {after.name}")

        if before.archived != after.archived:
            changes.append(f"**Archived:** {before.archived} -> {after.archived}")

        if before.locked != after.locked:
            changes.append(f"**Locked:** {before.locked} -> {after.locked}")

        if before.slowmode_delay != after.slowmode_delay:
            changes.append(
                f"**Slowmode:** {before.slowmode_delay}s -> {after.slowmode_delay}s"
            )

        if changes:
            embed = discord.Embed(title="Thread Updated", color=discord.Color.blue())
            embed.add_field(
                name="Thread", value=f"{after.mention} ({after.name})", inline=True
            )
            embed.add_field(name="Changes", value="\n".join(changes), inline=False)
            await send_log(after.guild, "server", embed)

    @commands.Cog.listener()
    async def on_webhooks_update(self, channel: discord.abc.GuildChannel):
        embed = discord.Embed(title="Webhooks Updated", color=discord.Color.blue())
        embed.add_field(
            name="Channel", value=f"{channel.mention} ({channel.name})", inline=True
        )
        embed.add_field(
            name="Note", value="A webhook was created, deleted, or modified", inline=False
        )
        await send_log(channel.guild, "server", embed)

    @commands.Cog.listener()
    async def on_scheduled_event_create(self, event: discord.ScheduledEvent):
        embed = discord.Embed(title="Event Created", color=discord.Color.green())
        embed.add_field(name="Event", value=event.name, inline=True)
        embed.add_field(
            name="Creator",
            value=f"{event.creator.mention}" if event.creator else "Unknown",
            inline=True,
        )
        embed.add_field(
            name="Start", value=f"<t:{int(event.start_time.timestamp())}:F>", inline=True
        )
        if event.end_time:
            embed.add_field(
                name="End", value=f"<t:{int(event.end_time.timestamp())}:F>", inline=True
            )
        embed.add_field(
            name="Location",
            value=event.location or event.channel.mention if event.channel else "Unknown",
            inline=True,
        )
        if event.description:
            embed.add_field(
                name="Description", value=event.description[:1024], inline=False
            )
        await send_log(event.guild, "server", embed)

    @commands.Cog.listener()
    async def on_scheduled_event_delete(self, event: discord.ScheduledEvent):
        embed = discord.Embed(title="Event Deleted", color=discord.Color.red())
        embed.add_field(name="Event", value=event.name, inline=True)
        embed.add_field(name="ID", value=event.id, inline=True)
        await send_log(event.guild, "server", embed)

    @commands.Cog.listener()
    async def on_scheduled_event_update(
        self, before: discord.ScheduledEvent, after: discord.ScheduledEvent
    ):
        changes = []

        if before.name != after.name:
            changes.append(f"**Name:** {before.name} -> {after.name}")

        if before.description != after.description:
            changes.append("**Description:** Updated")

        if before.start_time != after.start_time:
            changes.append(f"**Start Time:** Updated")

        if before.status != after.status:
            changes.append(f"**Status:** {before.status.name} -> {after.status.name}")

        if changes:
            embed = discord.Embed(title="Event Updated", color=discord.Color.blue())
            embed.add_field(name="Event", value=after.name, inline=True)
            embed.add_field(name="Changes", value="\n".join(changes), inline=False)
            await send_log(after.guild, "server", embed)

    @commands.Cog.listener()
    async def on_member_ban(self, guild: discord.Guild, user: discord.User):
        embed = discord.Embed(title="Member Banned", color=discord.Color.red())
        embed.add_field(name="User", value=f"{user.mention} ({user})", inline=True)
        embed.add_field(name="ID", value=user.id, inline=True)
        embed.set_thumbnail(url=user.display_avatar.url)
        await send_log(guild, "mod", embed)

    @commands.Cog.listener()
    async def on_member_unban(self, guild: discord.Guild, user: discord.User):
        embed = discord.Embed(title="Member Unbanned", color=discord.Color.green())
        embed.add_field(name="User", value=f"{user.mention} ({user})", inline=True)
        embed.add_field(name="ID", value=user.id, inline=True)
        embed.set_thumbnail(url=user.display_avatar.url)
        await send_log(guild, "mod", embed)
async def setup(bot: commands.Bot):
    await bot.add_cog(Logging(bot))
