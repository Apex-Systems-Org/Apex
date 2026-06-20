import discord
from discord.ext import commands, tasks
from discord import app_commands
from datetime import datetime, timedelta
import random

from database import db
from helpers.embeds import success, error, warning, info
from helpers.utils import is_module_enabled, parse_duration
from helpers.checks import has_mod_role


class GiveawayView(discord.ui.View):
    def __init__(self, giveaway_id: str):
        super().__init__(timeout=None)
        self.giveaway_id = giveaway_id

    @discord.ui.button(
        label="Enter", style=discord.ButtonStyle.primary, custom_id="giveaway_enter"
    )
    async def enter_giveaway(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        if not is_module_enabled(interaction.guild.id, "giveaways"):
            await interaction.response.send_message(
                embed=error("Giveaways are disabled on this server."), ephemeral=True
            )
            return
        giveaway = db.get_giveaway(self.giveaway_id)
        if not giveaway or giveaway["ended"]:
            await interaction.response.send_message(
                embed=error("This giveaway has ended!"), ephemeral=True
            )
            return

        # Check required role
        if giveaway.get("required_role"):
            role = interaction.guild.get_role(int(giveaway["required_role"]))
            if role and role not in interaction.user.roles:
                await interaction.response.send_message(
                    embed=error(f"You need the {role.mention} role to enter!"), ephemeral=True
                )
                return

        # Try to add entry
        if db.add_giveaway_entry(self.giveaway_id, interaction.user.id):
            entries = db.get_giveaway_entry_count(self.giveaway_id)
            await interaction.response.send_message(
                embed=success(f"You've entered the giveaway! ({entries} entries)"), ephemeral=True
            )
        else:
            await interaction.response.send_message(
                embed=warning("You've already entered this giveaway!"), ephemeral=True
            )


class GiveawaysCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.check_giveaways.start()

    def cog_unload(self):
        self.check_giveaways.cancel()

    @tasks.loop(seconds=30)
    async def check_giveaways(self):
        active_giveaways = db.get_active_giveaways()
        now = datetime.utcnow()

        for giveaway in active_giveaways:
            ends_at = datetime.fromisoformat(giveaway["ends_at"])
            if now >= ends_at:
                await self.end_giveaway_and_pick_winners(giveaway)

    @check_giveaways.before_loop
    async def before_check_giveaways(self):
        await self.bot.wait_until_ready()

    async def end_giveaway_and_pick_winners(self, giveaway: dict):
        db.end_giveaway(giveaway["id"])

        try:
            channel = self.bot.get_channel(int(giveaway["channel_id"]))
            if not channel:
                return

            message = await channel.fetch_message(int(giveaway["message_id"]))
            entries = db.get_giveaway_entries(giveaway["id"])

            winners_count = min(giveaway["winners_count"], len(entries))

            if winners_count == 0:
                # No entries
                embed = discord.Embed(
                    title="Giveaway Ended",
                    description=f"**Prize:** {giveaway['prize']}\n\nNo valid entries.",
                    color=discord.Color.red(),
                )
                embed.set_footer(text="No winners")
                await message.edit(embed=embed, view=None)
                return

            # Pick random winners
            winner_ids = random.sample(entries, winners_count)
            winners = []
            for wid in winner_ids:
                member = channel.guild.get_member(int(wid))
                if member:
                    winners.append(member.mention)
                else:
                    winners.append(f"<@{wid}>")

            embed = discord.Embed(
                title="Giveaway Ended",
                description=f"**Prize:** {giveaway['prize']}\n\n**Winners:** {', '.join(winners)}",
                color=discord.Color.gold(),
            )
            embed.set_footer(text=f"{len(entries)} total entries")
            await message.edit(embed=embed, view=None)

            # Announce winners
            await channel.send(
                embed=success(f"Congratulations {', '.join(winners)}! You won **{giveaway['prize']}**!")
            )

        except Exception as e:
            print(f"Error ending giveaway: {e}")

    @commands.hybrid_command(name="giveaway", description="Start a giveaway")
    @app_commands.describe(
        duration="How long the giveaway lasts (e.g., 1h, 30m, 1d)",
        winners="Number of winners",
        prize="What you're giving away",
        role="Required role to enter (optional)",
    )
    async def giveaway_cmd(
        self,
        ctx: commands.Context,
        duration: str,
        winners: int,
        prize: str,
        role: discord.Role = None,
    ):
        if not is_module_enabled(ctx.guild.id, "giveaways"):
            await ctx.send(embed=error("The giveaways module is disabled on this server."))
            return
        if not has_mod_role(ctx.author):
            await ctx.send(embed=error("You need moderator permissions to start giveaways."))
            return

        # Parse duration
        duration_seconds = parse_duration(duration)
        if not duration_seconds:
            await ctx.send(embed=error("Invalid duration. Use format like: 1h, 30m, 1d, 2h30m"))
            return

        if winners < 1 or winners > 20:
            await ctx.send(embed=error("Winners must be between 1 and 20."))
            return

        ends_at = datetime.utcnow() + timedelta(seconds=duration_seconds)

        role_text = f"\n**Required Role:** {role.mention}" if role else ""
        embed = discord.Embed(
            title="GIVEAWAY",
            description=f"**Prize:** {prize}\n\n**Winners:** {winners}\n**Ends:** <t:{int(ends_at.timestamp())}:R>\n**Hosted by:** {ctx.author.mention}{role_text}\n\nClick the button below to enter!",
            color=discord.Color.purple(),
        )
        embed.set_footer(text="Click to enter!")

        # Create giveaway in database first
        giveaway_id = db.create_giveaway(
            ctx.guild.id,
            {
                "channel_id": str(ctx.channel.id),
                "prize": prize,
                "winners_count": winners,
                "host_id": str(ctx.author.id),
                "required_role": str(role.id) if role else None,
                "ends_at": ends_at.isoformat(),
            },
        )

        view = GiveawayView(giveaway_id)
        msg = await ctx.send(embed=embed, view=view)

        # Update with message ID
        db.update_giveaway(giveaway_id, {"message_id": str(msg.id)})

    @commands.hybrid_command(name="gend", description="End a giveaway early")
    @app_commands.describe(message_id="The message ID of the giveaway")
    async def gend(self, ctx: commands.Context, message_id: str):
        if not is_module_enabled(ctx.guild.id, "giveaways"):
            await ctx.send(embed=error("The giveaways module is disabled on this server."))
            return
        if not has_mod_role(ctx.author):
            await ctx.send(embed=error("You need moderator permissions to end giveaways."))
            return

        giveaway = db.get_giveaway_by_message(message_id)
        if not giveaway:
            await ctx.send(embed=error("Giveaway not found."))
            return

        if giveaway["ended"]:
            await ctx.send(embed=error("This giveaway has already ended."))
            return

        await self.end_giveaway_and_pick_winners(giveaway)
        await ctx.send(embed=success("Giveaway ended!"))

    @commands.hybrid_command(name="greroll", description="Reroll giveaway winners")
    @app_commands.describe(
        message_id="The message ID of the giveaway", winners="Number of new winners to pick"
    )
    async def greroll(self, ctx: commands.Context, message_id: str, winners: int = 1):
        """Reroll giveaway winners."""
        if not is_module_enabled(ctx.guild.id, "giveaways"):
            await ctx.send(embed=error("The giveaways module is disabled on this server."))
            return
        if not has_mod_role(ctx.author):
            await ctx.send(embed=error("You need moderator permissions to reroll giveaways."))
            return

        giveaway = db.get_giveaway_by_message(message_id)
        if not giveaway:
            await ctx.send(embed=error("Giveaway not found."))
            return

        if not giveaway["ended"]:
            await ctx.send(embed=error("This giveaway hasn't ended yet."))
            return

        entries = db.get_giveaway_entries(giveaway["id"])
        if len(entries) == 0:
            await ctx.send(embed=info("No entries in this giveaway."))
            return

        winners_count = min(winners, len(entries))
        winner_ids = random.sample(entries, winners_count)
        winners_list = []
        for wid in winner_ids:
            member = ctx.guild.get_member(int(wid))
            if member:
                winners_list.append(member.mention)
            else:
                winners_list.append(f"<@{wid}>")

        await ctx.send(
            embed=success(f"New winner(s): {', '.join(winners_list)}! Congratulations on winning **{giveaway['prize']}**!")
        )


async def setup(bot):
    await bot.add_cog(GiveawaysCog(bot))
