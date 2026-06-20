import discord
from discord.ext import commands
from discord import app_commands
from datetime import datetime, timezone
import traceback

from database import db
from helpers.embeds import error, warning


class ErrorHandler(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        bot.tree.on_error = self.on_app_command_error

    async def _send_error_notification(self, destination, error_obj, command_name: str, error_id: str):
        try:
            embed = discord.Embed(
                title="Something went wrong",
                description=(
                    "Apex ran into an unexpected error while processing your command. "
                    "This has been logged and our team has been notified.\n\n"
                    "If this keeps happening, feel free to report it with the error ID below.\n\n"
                    f"**Error ID**\n`{error_id}`"
                ),
                color=discord.Color.dark_red(),
                timestamp=datetime.now(timezone.utc),
            )
            embed.set_footer(text="Apex")

            view = discord.ui.View()
            view.add_item(
                discord.ui.Button(
                    label="Report this issue",
                    url="https://apex-systems.vercel.app/discord",
                    style=discord.ButtonStyle.link,
                )
            )

            if isinstance(destination, discord.Interaction):
                if destination.response.is_done():
                    await destination.followup.send(embed=embed, view=view, ephemeral=True)
                else:
                    await destination.response.send_message(embed=embed, view=view, ephemeral=True)
            else:
                await destination.send(embed=embed, view=view)
        except Exception:
            pass

    async def _log_unhandled(self, err, command_name: str, guild_id: int | None, user_id: int | None):
        tb = "".join(traceback.format_exception(type(err), err, err.__traceback__))
        print(f"[ErrorHandler] Unhandled error in {command_name}:\n{tb}", flush=True)

        error_id = db.log_error({
            "error": f"{type(err).__name__}: {err}",
            "command": command_name,
            "guild_id": str(guild_id) if guild_id else None,
            "user_id": str(user_id) if user_id else None,
        })

        error_channel_id = None
        if guild_id:
            settings = db.get_guild_settings(guild_id)
            error_channel_id = settings.get("error_log_channel")

        if error_channel_id:
            try:
                channel = self.bot.get_channel(int(error_channel_id))
                if channel:
                    log_embed = discord.Embed(
                        title="Unhandled Error",
                        color=discord.Color.dark_red(),
                        timestamp=datetime.now(timezone.utc),
                    )
                    log_embed.add_field(name="Command", value=f"`{command_name}`", inline=True)
                    log_embed.add_field(name="Error ID", value=f"`{error_id}`", inline=True)
                    log_embed.add_field(name="Error", value=f"```\n{str(err)[:1000]}\n```", inline=False)
                    log_embed.set_footer(text="Apex")
                    await channel.send(embed=log_embed)
            except Exception:
                pass

        return error_id

    @commands.Cog.listener()
    async def on_command_error(self, ctx: commands.Context, err: commands.CommandError):
        try:
            if isinstance(err, commands.CommandInvokeError):
                err = err.original

            if isinstance(err, commands.CommandNotFound):
                return

            if isinstance(err, commands.NotOwner):
                return

            if isinstance(err, commands.MissingPermissions):
                missing = ", ".join(err.missing_permissions)
                await ctx.send(
                    embed=error(f"You need the following permissions: `{missing}`"),
                    delete_after=10,
                )
                return

            if isinstance(err, commands.BotMissingPermissions):
                missing = ", ".join(err.missing_permissions)
                await ctx.send(
                    embed=error(f"I need the following permissions: `{missing}`"),
                    delete_after=10,
                )
                return

            if isinstance(err, commands.MissingRequiredArgument):
                prefix = db.get_prefix(ctx.guild.id) if ctx.guild else "a!"
                usage = f"{prefix}{ctx.command.name}"
                if ctx.command.signature:
                    usage += f" {ctx.command.signature}"
                await ctx.send(
                    embed=error(f"Missing required argument: **{err.param.name}**\n\nUsage: `{usage}`"),
                    delete_after=15,
                )
                return

            if isinstance(err, commands.BadArgument):
                prefix = db.get_prefix(ctx.guild.id) if ctx.guild else "a!"
                usage = f"{prefix}{ctx.command.name}"
                if ctx.command.signature:
                    usage += f" {ctx.command.signature}"
                await ctx.send(
                    embed=error(f"Invalid argument: {err}\n\nUsage: `{usage}`"),
                    delete_after=15,
                )
                return

            if isinstance(err, commands.CommandOnCooldown):
                await ctx.send(
                    embed=warning(f"Cooldown. Try again in **{err.retry_after:.1f}s**."),
                    delete_after=5,
                )
                return

            if isinstance(err, commands.CheckFailure):
                return

            if isinstance(err, discord.Forbidden):
                await ctx.send(
                    embed=error("I don't have permission to do that. Check my role and permissions."),
                    delete_after=10,
                )
                return

            command_name = ctx.command.name if ctx.command else "Unknown"
            error_id = await self._log_unhandled(
                err, command_name,
                ctx.guild.id if ctx.guild else None,
                ctx.author.id,
            )
            await self._send_error_notification(ctx, err, command_name, error_id)

        except discord.NotFound:
            pass
        except discord.HTTPException:
            pass

    async def on_app_command_error(self, interaction: discord.Interaction, err: app_commands.AppCommandError):
        try:
            original = err.__cause__ if isinstance(err, app_commands.CommandInvokeError) else err

            if isinstance(original, app_commands.MissingPermissions):
                missing = ", ".join(original.missing_permissions)
                embed = error(f"You need the following permissions: `{missing}`")
                if interaction.response.is_done():
                    await interaction.followup.send(embed=embed, ephemeral=True)
                else:
                    await interaction.response.send_message(embed=embed, ephemeral=True)
                return

            if isinstance(original, app_commands.BotMissingPermissions):
                missing = ", ".join(original.missing_permissions)
                embed = error(f"I need the following permissions: `{missing}`")
                if interaction.response.is_done():
                    await interaction.followup.send(embed=embed, ephemeral=True)
                else:
                    await interaction.response.send_message(embed=embed, ephemeral=True)
                return

            if isinstance(original, app_commands.CommandOnCooldown):
                embed = warning(f"Cooldown. Try again in **{original.retry_after:.1f}s**.")
                if interaction.response.is_done():
                    await interaction.followup.send(embed=embed, ephemeral=True)
                else:
                    await interaction.response.send_message(embed=embed, ephemeral=True)
                return

            if isinstance(original, app_commands.CheckFailure):
                embed = error("You do not have permission to use this command.")
                if interaction.response.is_done():
                    await interaction.followup.send(embed=embed, ephemeral=True)
                else:
                    await interaction.response.send_message(embed=embed, ephemeral=True)
                return

            if isinstance(original, discord.Forbidden):
                embed = error("I don't have permission to do that. Check my role and permissions.")
                if interaction.response.is_done():
                    await interaction.followup.send(embed=embed, ephemeral=True)
                else:
                    await interaction.response.send_message(embed=embed, ephemeral=True)
                return

            command_name = interaction.command.name if interaction.command else "Unknown"
            error_id = await self._log_unhandled(
                original, command_name,
                interaction.guild_id,
                interaction.user.id,
            )
            await self._send_error_notification(interaction, original, command_name, error_id)

        except discord.NotFound:
            pass
        except discord.HTTPException:
            pass


async def setup(bot: commands.Bot):
    await bot.add_cog(ErrorHandler(bot))
