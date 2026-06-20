import discord
from discord.ext import commands
from discord import app_commands

from database import db
from helpers.embeds import success, error, warning, info
from helpers.utils import is_module_enabled


class VoiceChannelControlView(discord.ui.View):
    """Controls for temporary voice channels."""

    def __init__(self, channel_id: str):
        super().__init__(timeout=None)
        self.channel_id = channel_id

    @discord.ui.button(
        label="Lock", style=discord.ButtonStyle.danger, custom_id="vc_lock"
    )
    async def lock_channel(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        temp_channel = db.get_temp_voice_channel(self.channel_id)
        if not temp_channel or str(interaction.user.id) != temp_channel["owner_id"]:
            await interaction.response.send_message(
                embed=error("You don't own this voice channel."), ephemeral=True
            )
            return
        channel = interaction.guild.get_channel(int(self.channel_id))
        if channel:
            await channel.set_permissions(interaction.guild.default_role, connect=False)
            await interaction.response.send_message(embed=success("Channel locked."), ephemeral=True)

    @discord.ui.button(
        label="Unlock", style=discord.ButtonStyle.success, custom_id="vc_unlock"
    )
    async def unlock_channel(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        temp_channel = db.get_temp_voice_channel(self.channel_id)
        if not temp_channel or str(interaction.user.id) != temp_channel["owner_id"]:
            await interaction.response.send_message(
                embed=error("You don't own this voice channel."), ephemeral=True
            )
            return
        channel = interaction.guild.get_channel(int(self.channel_id))
        if channel:
            await channel.set_permissions(interaction.guild.default_role, connect=True)
            await interaction.response.send_message(embed=success("Channel unlocked."), ephemeral=True)

    @discord.ui.button(
        label="Rename", style=discord.ButtonStyle.primary, custom_id="vc_rename"
    )
    async def rename_channel(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        temp_channel = db.get_temp_voice_channel(self.channel_id)
        if not temp_channel or str(interaction.user.id) != temp_channel["owner_id"]:
            await interaction.response.send_message(
                embed=error("You don't own this voice channel."), ephemeral=True
            )
            return
        modal = VoiceChannelRenameModal(self.channel_id)
        await interaction.response.send_modal(modal)

    @discord.ui.button(
        label="Limit", style=discord.ButtonStyle.secondary, custom_id="vc_limit"
    )
    async def set_limit(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        temp_channel = db.get_temp_voice_channel(self.channel_id)
        if not temp_channel or str(interaction.user.id) != temp_channel["owner_id"]:
            await interaction.response.send_message(
                embed=error("You don't own this voice channel."), ephemeral=True
            )
            return
        modal = VoiceChannelLimitModal(self.channel_id)
        await interaction.response.send_modal(modal)


class VoiceChannelRenameModal(discord.ui.Modal, title="Rename Voice Channel"):
    def __init__(self, channel_id: str):
        super().__init__()
        self.channel_id = channel_id

    new_name = discord.ui.TextInput(
        label="New Channel Name",
        placeholder="Enter a new name for your channel",
        min_length=1,
        max_length=100,
    )

    async def on_submit(self, interaction: discord.Interaction):
        channel = interaction.guild.get_channel(int(self.channel_id))
        if channel:
            await channel.edit(name=self.new_name.value)
            await interaction.response.send_message(
                embed=success(f"Channel renamed to **{self.new_name.value}**"), ephemeral=True
            )
        else:
            await interaction.response.send_message(
                embed=error("Channel not found."), ephemeral=True
            )


class VoiceChannelLimitModal(discord.ui.Modal, title="Set User Limit"):
    def __init__(self, channel_id: str):
        super().__init__()
        self.channel_id = channel_id

    user_limit = discord.ui.TextInput(
        label="User Limit (0 for unlimited)",
        placeholder="Enter a number (0-99)",
        min_length=1,
        max_length=2,
        default="0",
    )

    async def on_submit(self, interaction: discord.Interaction):
        try:
            limit = int(self.user_limit.value)
            if limit < 0 or limit > 99:
                await interaction.response.send_message(
                    embed=error("User limit must be between 0 and 99."), ephemeral=True
                )
                return
            channel = interaction.guild.get_channel(int(self.channel_id))
            if channel:
                await channel.edit(user_limit=limit)
                limit_text = "unlimited" if limit == 0 else str(limit)
                await interaction.response.send_message(
                    embed=success(f"User limit set to **{limit_text}**"), ephemeral=True
                )
            else:
                await interaction.response.send_message(
                    embed=error("Channel not found."), ephemeral=True
                )
        except ValueError:
            await interaction.response.send_message(
                embed=error("Please enter a valid number."), ephemeral=True
            )


# Voice command group - uses subcommands to save slash command slots
class VoiceCommands(commands.GroupCog, name="vc", description="Voice channel commands"):
    def __init__(self, bot):
        self.bot = bot
        super().__init__()

    @app_commands.command(
        name="control", description="Manage your temporary voice channel"
    )
    async def voice_control(self, interaction: discord.Interaction):
        """Open voice channel controls for your temporary channel."""
        if not is_module_enabled(interaction.guild.id, "voice_channels"):
            await interaction.response.send_message(
                embed=error("Voice channels module is not enabled on this server."), ephemeral=True
            )
            return

        if not interaction.user.voice or not interaction.user.voice.channel:
            await interaction.response.send_message(
                embed=error("You must be in a voice channel to use this command."), ephemeral=True
            )
            return

        channel_id = str(interaction.user.voice.channel.id)
        temp_channel = db.get_temp_voice_channel(channel_id)

        if not temp_channel:
            await interaction.response.send_message(
                embed=error("You're not in a temporary voice channel."), ephemeral=True
            )
            return

        if temp_channel["owner_id"] != str(interaction.user.id):
            await interaction.response.send_message(
                embed=error("You don't own this voice channel."), ephemeral=True
            )
            return

        embed = discord.Embed(
            title="Voice Channel Controls",
            description=f"Manage your channel: **{interaction.user.voice.channel.name}**",
            color=discord.Color.blue(),
        )
        embed.add_field(name="Lock", value="Prevent others from joining", inline=True)
        embed.add_field(name="Unlock", value="Allow others to join", inline=True)
        embed.add_field(name="Rename", value="Change channel name", inline=True)
        embed.add_field(name="Limit", value="Set user limit", inline=True)

        view = VoiceChannelControlView(channel_id)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    @app_commands.command(
        name="kick", description="Kick a user from your voice channel"
    )
    async def voice_kick(
        self, interaction: discord.Interaction, member: discord.Member
    ):
        """Kick a user from your temporary voice channel."""
        if not is_module_enabled(interaction.guild.id, "voice_channels"):
            await interaction.response.send_message(
                embed=error("Voice channels module is not enabled on this server."), ephemeral=True
            )
            return

        if not interaction.user.voice or not interaction.user.voice.channel:
            await interaction.response.send_message(
                embed=error("You must be in a voice channel to use this command."), ephemeral=True
            )
            return

        channel_id = str(interaction.user.voice.channel.id)
        temp_channel = db.get_temp_voice_channel(channel_id)

        if not temp_channel:
            await interaction.response.send_message(
                embed=error("You're not in a temporary voice channel."), ephemeral=True
            )
            return

        if temp_channel["owner_id"] != str(interaction.user.id):
            await interaction.response.send_message(
                embed=error("You don't own this voice channel."), ephemeral=True
            )
            return

        if member not in interaction.user.voice.channel.members:
            await interaction.response.send_message(
                f"{member.mention} is not in your voice channel.", ephemeral=True
            )
            return

        if member.id == interaction.user.id:
            await interaction.response.send_message(
                "You can't kick yourself.", ephemeral=True
            )
            return

        await member.move_to(
            None, reason=f"Kicked from voice channel by {interaction.user}"
        )
        await interaction.response.send_message(
            embed=success(f"{member.mention} has been kicked from the voice channel."), ephemeral=True
        )

    @app_commands.command(
        name="transfer", description="Transfer ownership of your voice channel"
    )
    async def voice_transfer(
        self, interaction: discord.Interaction, member: discord.Member
    ):
        """Transfer ownership of your temporary voice channel to another user."""
        if not is_module_enabled(interaction.guild.id, "voice_channels"):
            await interaction.response.send_message(
                embed=error("Voice channels module is not enabled on this server."), ephemeral=True
            )
            return

        if not interaction.user.voice or not interaction.user.voice.channel:
            await interaction.response.send_message(
                embed=error("You must be in a voice channel to use this command."), ephemeral=True
            )
            return

        channel_id = str(interaction.user.voice.channel.id)
        temp_channel = db.get_temp_voice_channel(channel_id)

        if not temp_channel:
            await interaction.response.send_message(
                embed=error("You're not in a temporary voice channel."), ephemeral=True
            )
            return

        if temp_channel["owner_id"] != str(interaction.user.id):
            await interaction.response.send_message(
                embed=error("You don't own this voice channel."), ephemeral=True
            )
            return

        if member not in interaction.user.voice.channel.members:
            await interaction.response.send_message(
                f"{member.mention} must be in the voice channel to transfer ownership.",
                ephemeral=True,
            )
            return

        if member.id == interaction.user.id:
            await interaction.response.send_message(
                "You already own this channel.", ephemeral=True
            )
            return

        # Update ownership in database
        db.delete_temp_voice_channel(channel_id)
        db.create_temp_voice_channel(
            interaction.guild.id,
            channel_id,
            member.id,
            temp_channel.get("generator_id"),
        )

        # Update channel permissions
        channel = interaction.user.voice.channel
        await channel.set_permissions(interaction.user, overwrite=None)
        await channel.set_permissions(
            member,
            connect=True,
            speak=True,
            mute_members=True,
            deafen_members=True,
            move_members=True,
            manage_channels=True,
        )

        await interaction.response.send_message(
            embed=success(f"Ownership transferred to {member.mention}."), ephemeral=True
        )

    @app_commands.command(
        name="setup", description="Set up a Join to Create voice channel"
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def setup_voice(
        self,
        interaction: discord.Interaction,
        channel: discord.VoiceChannel,
        name_template: str = "{user}'s Channel",
    ):
        """Set up a voice channel as a Join to Create trigger."""
        if not is_module_enabled(interaction.guild.id, "voice_channels"):
            await interaction.response.send_message(
                embed=error("Voice channels module is not enabled. Enable it in the dashboard first."),
                ephemeral=True,
            )
            return

        # Check if channel is already a generator
        existing = db.get_voice_generator_by_channel(str(channel.id))
        if existing:
            await interaction.response.send_message(
                embed=warning(f"{channel.mention} is already a Join to Create channel."),
                ephemeral=True,
            )
            return

        # Create the generator
        generator_id = db.create_voice_generator(
            interaction.guild.id,
            {
                "channel_id": str(channel.id),
                "category_id": str(channel.category.id) if channel.category else None,
                "name_template": name_template,
                "user_limit": 0,
                "bitrate": 64000,
                "created_by": str(interaction.user.id),
            },
        )

        embed = discord.Embed(
            title="Join to Create Setup",
            description=f"{channel.mention} is now a Join to Create channel!",
            color=discord.Color.green(),
        )
        embed.add_field(name="Name Template", value=f"`{name_template}`", inline=True)
        embed.add_field(
            name="Category",
            value=channel.category.name if channel.category else "None",
            inline=True,
        )
        embed.add_field(
            name="Usage",
            value="Users who join this channel will automatically get their own voice channel.",
            inline=False,
        )
        embed.set_footer(text=f"Generator ID: {generator_id}")

        await interaction.response.send_message(embed=embed)

    @app_commands.command(
        name="remove", description="Remove a Join to Create voice channel"
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def remove_voice(
        self, interaction: discord.Interaction, channel: discord.VoiceChannel
    ):
        """Remove a Join to Create trigger from a voice channel."""
        generator = db.get_voice_generator_by_channel(str(channel.id))
        if not generator:
            await interaction.response.send_message(
                embed=error(f"{channel.mention} is not a Join to Create channel."), ephemeral=True
            )
            return

        db.delete_voice_generator(interaction.guild.id, generator["id"])
        await interaction.response.send_message(
            embed=success(f"{channel.mention} is no longer a Join to Create channel.")
        )

    @app_commands.command(name="list", description="List all Join to Create channels")
    @app_commands.checks.has_permissions(administrator=True)
    async def list_voice_generators(self, interaction: discord.Interaction):
        """List all Join to Create voice channels in this server."""
        generators = db.get_all_voice_generators(interaction.guild.id)

        if not generators:
            await interaction.response.send_message(
                embed=info("No Join to Create channels set up. Use `/vc setup` to create one.")
            )
            return

        embed = discord.Embed(
            title="Join to Create Channels", color=discord.Color.blue()
        )

        for gen in generators:
            channel = interaction.guild.get_channel(int(gen["channel_id"]))
            channel_text = (
                channel.mention if channel else f"Unknown ({gen['channel_id']})"
            )
            embed.add_field(
                name=channel_text,
                value=f"Template: `{gen['name_template']}`\nUser Limit: {gen['user_limit'] or 'None'}",
                inline=False,
            )

        await interaction.response.send_message(embed=embed)


async def setup(bot):
    await bot.add_cog(VoiceCommands(bot))
