import discord
from discord.ext import commands
from discord import app_commands
from database import db
from helpers.embeds import success, error, info
from helpers import has_admin_role


class AutoResponder(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.hybrid_command(name="autoresponder", description="Manage auto-responders")
    @app_commands.describe(
        action="Action to perform",
        trigger="The trigger word/phrase",
        response="The response to send",
        match_type="How to match the trigger",
    )
    @app_commands.choices(
        action=[
            app_commands.Choice(name="Add", value="add"),
            app_commands.Choice(name="Remove", value="remove"),
            app_commands.Choice(name="List", value="list"),
        ]
    )
    @app_commands.choices(
        match_type=[
            app_commands.Choice(name="Contains", value="contains"),
            app_commands.Choice(name="Exact Match", value="exact"),
            app_commands.Choice(name="Starts With", value="startswith"),
            app_commands.Choice(name="Ends With", value="endswith"),
            app_commands.Choice(name="Whole Word", value="word"),
        ]
    )
    async def autoresponder(
        self,
        ctx: commands.Context,
        action: str,
        trigger: str = None,
        response: str = None,
        match_type: str = "contains",
    ):
        if not has_admin_role(ctx.author):
            await ctx.send(
                embed=error("You don't have permission to manage auto-responders."), ephemeral=True
            )
            return

        if action == "list":
            responders = db.get_auto_responders(ctx.guild.id)
            if not responders:
                await ctx.send(embed=info("No auto-responders configured."), ephemeral=True)
                return

            embed = discord.Embed(title="Auto-Responders", color=discord.Color.blue())
            for r in responders[:25]:  # Limit to 25 for embed
                status = "Enabled" if r.get("enabled", True) else "Disabled"
                embed.add_field(
                    name=f"{status} `{r['trigger_word']}`",
                    value=f"**Match:** {r.get('match_type', 'contains')}\n**Response:** {r['response'][:50]}{'...' if len(r['response']) > 50 else ''}\n**ID:** `{r['id'][:8]}`",
                    inline=True,
                )
            await ctx.send(embed=embed)

        elif action == "add":
            if not trigger or not response:
                await ctx.send(
                    embed=error("Please provide both a trigger and response."), ephemeral=True
                )
                return

            responder_id = db.create_auto_responder(
                ctx.guild.id,
                {
                    "trigger_word": trigger,
                    "response": response,
                    "match_type": match_type,
                    "ignore_case": True,
                    "enabled": True,
                    "created_by": str(ctx.author.id),
                },
            )

            await ctx.send(
                embed=success(f"Auto-responder created!\n**Trigger:** `{trigger}`\n**Match Type:** {match_type}\n**Response:** {response[:100]}{'...' if len(response) > 100 else ''}")
            )

        elif action == "remove":
            if not trigger:
                await ctx.send(
                    embed=error("Please provide the trigger word or ID to remove."), ephemeral=True
                )
                return

            responders = db.get_auto_responders(ctx.guild.id)
            to_delete = None
            for r in responders:
                if r["trigger_word"].lower() == trigger.lower() or r["id"].startswith(
                    trigger
                ):
                    to_delete = r
                    break

            if not to_delete:
                await ctx.send(embed=error("Auto-responder not found."), ephemeral=True)
                return

            db.delete_auto_responder(ctx.guild.id, to_delete["id"])
            await ctx.send(
                embed=success(f"Auto-responder `{to_delete['trigger_word']}` has been removed.")
            )

    @commands.hybrid_command(
        name="toggleresponder", description="Enable/disable an auto-responder"
    )
    @app_commands.describe(trigger="The trigger word or ID")
    async def toggleresponder(self, ctx: commands.Context, trigger: str):
        if not has_admin_role(ctx.author):
            await ctx.send(
                embed=error("You don't have permission to manage auto-responders."), ephemeral=True
            )
            return

        responders = db.get_auto_responders(ctx.guild.id)
        target = None
        for r in responders:
            if r["trigger_word"].lower() == trigger.lower() or r["id"].startswith(trigger):
                target = r
                break

        if not target:
            await ctx.send(embed=error("Auto-responder not found."), ephemeral=True)
            return

        new_status = not target.get("enabled", True)
        db.update_auto_responder(ctx.guild.id, target["id"], {"enabled": new_status})

        status_text = "enabled" if new_status else "disabled"
        await ctx.send(
            embed=success(f"Auto-responder `{target['trigger_word']}` has been **{status_text}**.")
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(AutoResponder(bot))
