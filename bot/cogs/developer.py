import discord
import os
import sys
from discord.ext import commands
from discord import app_commands
from datetime import datetime, timezone
from database import db
from helpers.embeds import success, error, info
from helpers import is_staff

MAIN_SERVER_ID = 1459426097283334147
DEV_ROLE_ID = 1459459647906779147
DEV_AUTH_LOG_CHANNEL_ID = 1466998394210746634


class DevAuthCodeModal(discord.ui.Modal, title="Generate Dev Auth Code"):

    max_uses = discord.ui.TextInput(
        label="Max Uses",
        placeholder="How many times can this code be used? (1-10)",
        default="1",
        max_length=2,
        required=True,
    )

    expiry_minutes = discord.ui.TextInput(
        label="Expiry (minutes)",
        placeholder="How long until the code expires? (1-60)",
        default="10",
        max_length=2,
        required=True,
    )

    def __init__(self, bot: commands.Bot, guild: discord.Guild, user: discord.User):
        super().__init__()
        self.bot = bot
        self.guild = guild
        self.user = user

    async def on_submit(self, interaction: discord.Interaction):
        import secrets
        from datetime import timedelta

        # Validate inputs
        try:
            max_uses_val = int(self.max_uses.value)
            if max_uses_val < 1 or max_uses_val > 10:
                await interaction.response.send_message(
                    embed=error("Max uses must be between 1 and 10."), ephemeral=True
                )
                return
        except ValueError:
            await interaction.response.send_message(
                embed=error("Max uses must be a number."), ephemeral=True
            )
            return

        try:
            expiry_val = int(self.expiry_minutes.value)
            if expiry_val < 1 or expiry_val > 60:
                await interaction.response.send_message(
                    embed=error("Expiry must be between 1 and 60 minutes."), ephemeral=True
                )
                return
        except ValueError:
            await interaction.response.send_message(
                embed=error("Expiry must be a number."), ephemeral=True
            )
            return

        # Generate a secure random code
        code = secrets.token_urlsafe(16)

        # Set expiration
        expires_at = (
            datetime.now(timezone.utc) + timedelta(minutes=expiry_val)
        ).isoformat()

        # Store the code
        db.create_dev_auth_code(
            code, str(self.guild.id), str(self.user.id), expires_at, max_uses_val
        )

        # Try to DM the user
        try:
            embed = discord.Embed(
                title="Dev Auth Code Generated",
                description="Use this code in the developer dashboard to access this server's settings.",
                color=discord.Color.blurple(),
            )
            embed.add_field(name="Code", value=f"```{code}```", inline=False)
            embed.add_field(name="Server", value=self.guild.name, inline=True)
            embed.add_field(name="Server ID", value=str(self.guild.id), inline=True)
            embed.add_field(name="Expires", value=f"{expiry_val} minutes", inline=True)
            embed.add_field(name="Max Uses", value=str(max_uses_val), inline=True)
            if max_uses_val == 1:
                embed.set_footer(text="This code can only be used once.")
            else:
                embed.set_footer(
                    text=f"This code can be used up to {max_uses_val} times."
                )
            await self.user.send(embed=embed)
            await interaction.response.send_message(
                embed=success("Dev auth code has been sent to your DMs."), ephemeral=True
            )
        except discord.Forbidden:
            await interaction.response.send_message(
                embed=error("I couldn't DM you. Please enable DMs from server members and try again."),
                ephemeral=True,
            )
            return

        # Log this action to the main server
        try:
            main_guild = self.bot.get_guild(MAIN_SERVER_ID)
            if main_guild:
                log_channel = main_guild.get_channel(DEV_AUTH_LOG_CHANNEL_ID)
                if log_channel:
                    log_embed = discord.Embed(
                        title="Dev Auth Code Generated",
                        color=discord.Color.orange(),
                        timestamp=datetime.now(timezone.utc),
                    )
                    log_embed.add_field(
                        name="User", value=f"{self.user} ({self.user.id})", inline=True
                    )
                    log_embed.add_field(
                        name="Server",
                        value=f"{self.guild.name} ({self.guild.id})",
                        inline=True,
                    )
                    log_embed.add_field(
                        name="Max Uses", value=str(max_uses_val), inline=True
                    )
                    log_embed.add_field(
                        name="Expires", value=f"{expiry_val} minutes", inline=True
                    )
                    await log_channel.send(embed=log_embed)
        except Exception as e:
            print(f"Failed to log dev auth code generation: {e}")


class Developer(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(name="devcodeauth", hidden=True)
    async def devcodeauth(self, ctx: commands.Context):
        # Check if user has dev role in main server
        is_support, is_dev = await is_staff(self.bot, ctx.author.id)
        if not is_dev:
            await ctx.send(
                embed=error("You need to be a developer to use this command."), delete_after=10
            )
            return

        # Delete the command message for security
        try:
            await ctx.message.delete()
        except discord.HTTPException:
            pass

        # Show the modal
        modal = DevAuthCodeModal(self.bot, ctx.guild, ctx.author)
        bot_ref = self.bot

        # We need to use a button to trigger the modal since prefix commands can't directly show modals
        class ModalTriggerView(discord.ui.View):
            def __init__(self):
                super().__init__(timeout=60)

            @discord.ui.button(
                label="Configure Code Options", style=discord.ButtonStyle.primary
            )
            async def open_modal(
                self, interaction: discord.Interaction, button: discord.ui.Button
            ):
                if interaction.user.id != ctx.author.id:
                    await interaction.response.send_message(
                        embed=error("This is not for you."), ephemeral=True
                    )
                    return
                await interaction.response.send_modal(modal)
                self.stop()

            @discord.ui.button(
                label="Quick Generate (1 use, 10 min)", style=discord.ButtonStyle.secondary
            )
            async def quick_generate(
                self, interaction: discord.Interaction, button: discord.ui.Button
            ):
                if interaction.user.id != ctx.author.id:
                    await interaction.response.send_message(
                        embed=error("This is not for you."), ephemeral=True
                    )
                    return

                import secrets
                from datetime import timedelta

                code = secrets.token_urlsafe(16)
                expires_at = (
                    datetime.now(timezone.utc) + timedelta(minutes=10)
                ).isoformat()
                db.create_dev_auth_code(
                    code, str(ctx.guild.id), str(ctx.author.id), expires_at, 1
                )

                try:
                    embed = discord.Embed(
                        title="Dev Auth Code Generated",
                        description="Use this code in the developer dashboard to access this server's settings.",
                        color=discord.Color.blurple(),
                    )
                    embed.add_field(name="Code", value=f"```{code}```", inline=False)
                    embed.add_field(name="Server", value=ctx.guild.name, inline=True)
                    embed.add_field(name="Server ID", value=str(ctx.guild.id), inline=True)
                    embed.add_field(name="Expires", value="10 minutes", inline=True)
                    embed.set_footer(text="This code can only be used once.")
                    await ctx.author.send(embed=embed)
                    await interaction.response.send_message(
                        embed=success("Dev auth code has been sent to your DMs."), ephemeral=True
                    )
                except discord.Forbidden:
                    await interaction.response.send_message(
                        embed=error("I couldn't DM you. Please enable DMs from server members and try again."),
                        ephemeral=True,
                    )
                    return

                # Log this action
                try:
                    main_guild = bot_ref.get_guild(MAIN_SERVER_ID)
                    if main_guild:
                        log_channel = main_guild.get_channel(DEV_AUTH_LOG_CHANNEL_ID)
                        if log_channel:
                            log_embed = discord.Embed(
                                title="Dev Auth Code Generated",
                                color=discord.Color.orange(),
                                timestamp=datetime.now(timezone.utc),
                            )
                            log_embed.add_field(
                                name="User",
                                value=f"{ctx.author} ({ctx.author.id})",
                                inline=True,
                            )
                            log_embed.add_field(
                                name="Server",
                                value=f"{ctx.guild.name} ({ctx.guild.id})",
                                inline=True,
                            )
                            log_embed.add_field(name="Max Uses", value="1", inline=True)
                            log_embed.add_field(
                                name="Expires", value="10 minutes", inline=True
                            )
                            await log_channel.send(embed=log_embed)
                except Exception as e:
                    print(f"Failed to log dev auth code generation: {e}")

                self.stop()

        view = ModalTriggerView()
        msg = await ctx.send(
            embed=info("Generate a dev auth code for this server:"), view=view, delete_after=65
        )

    @commands.command(name="sentry", hidden=True)
    async def sentry_lookup(self, ctx: commands.Context, error_id: str = None):
        # Check if user has Dev role in main server
        main_guild = self.bot.get_guild(MAIN_SERVER_ID)
        if not main_guild:
            return
        try:
            member = main_guild.get_member(ctx.author.id) or await main_guild.fetch_member(
                ctx.author.id
            )
        except discord.NotFound:
            return
        if not any(role.id == DEV_ROLE_ID for role in member.roles):
            await ctx.send(embed=error("You don't have permission to use this command."))
            return

        if not error_id:
            await ctx.send(embed=info("Usage: `!sentry <error_id>`"))
            return

        error_log = db.get_error_log(error_id)
        if not error_log:
            await ctx.send(embed=error(f"No error found with ID `{error_id}`."))
            return

        error_str = error_log.get("error", "Unknown")
        if len(error_str) > 1000:
            error_str = error_str[:1000] + "..."

        embed = discord.Embed(
            title=f"Error `{error_id}`",
            description="This is the information regarding this error.",
            color=discord.Color.red(),
            timestamp=datetime.now(timezone.utc),
        )
        embed.add_field(
            name="Error Details", value=f"**Full Error:** {error_str}", inline=False
        )
        embed.add_field(
            name="Command", value=f"`{error_log.get('command', 'Unknown')}`", inline=True
        )
        embed.add_field(
            name="In Guild", value=f"`{error_log.get('guild_id', 'N/A')}`", inline=True
        )
        embed.add_field(
            name="User", value=f"`{error_log.get('user_id', 'N/A')}`", inline=True
        )
        embed.add_field(name="At", value=error_log.get("timestamp", "Unknown"), inline=True)

        embed.set_footer(text="Apex Sentry")

        await ctx.send(embed=embed)

    @commands.hybrid_command(
        name="synccommands", description="Sync slash commands globally (Dev only)"
    )
    async def synccommands(self, ctx: commands.Context):
        is_support, is_dev = await is_staff(self.bot, ctx.author.id)
        if not is_dev:
            await ctx.send(
                embed=error("You don't have permission to use this command. Dev role required.")
            )
            return

        await ctx.send(embed=info("Syncing commands globally... This may take a moment."))

        try:
            synced = await self.bot.tree.sync()
            await ctx.send(embed=success(f"Successfully synced {len(synced)} commands globally."))
        except Exception as e:
            await ctx.send(embed=error(f"Failed to sync commands: {e}"))

    @commands.command(name="backup", hidden=True)
    @commands.is_owner()
    async def backup_db(self, ctx):
        import subprocess
        result = subprocess.run(
            [sys.executable, "backup.py"],
            capture_output=True, text=True, cwd=os.path.dirname(__file__) or "."
        )
        output = result.stdout.strip() or result.stderr.strip() or "No output"
        if result.returncode == 0:
            await ctx.send(embed=success(f"```\n{output}\n```"))
        else:
            await ctx.send(embed=error(f"Backup failed:\n```\n{output}\n```"))


async def setup(bot: commands.Bot):
    await bot.add_cog(Developer(bot))
