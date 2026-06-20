import discord
from discord.ext import commands
from discord import app_commands
from datetime import datetime
from database import db
from helpers.embeds import success, error, warning, info
from helpers import has_mod_role, has_admin_role

STATUS_COLORS = {
    "pending": 0xF5C542,
    "approved": 0x2ECC71,
    "denied": 0xE74C3C,
    "considered": 0x3498DB,
    "implemented": 0x9B59B6,
}

STATUS_LABELS = {
    "approved": "Approved",
    "denied": "Denied",
    "considered": "Under Consideration",
    "implemented": "Implemented",
}


def _build_embed(text, author, upvotes=0, downvotes=0, status="pending",
                 staff_response=None, reviewed_by=None):
    color = STATUS_COLORS.get(status, STATUS_COLORS["pending"])
    embed = discord.Embed(color=discord.Colour(color), timestamp=datetime.utcnow())
    embed.set_author(name=str(author), icon_url=author.display_avatar.url)
    embed.description = f"**Suggestion**\n> {text}"

    if status != "pending" and status in STATUS_LABELS:
        embed.add_field(name="Status", value=STATUS_LABELS[status], inline=True)

    if staff_response:
        by = f" — {reviewed_by.display_name}" if reviewed_by else ""
        embed.add_field(name="Response", value=f"{staff_response}{by}", inline=False)

    return embed


class SuggestionView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    async def _vote(self, interaction, direction):
        suggestion = db.get_suggestion_by_message(str(interaction.message.id))
        if not suggestion:
            return await interaction.response.send_message(
                embed=error("This suggestion no longer exists."), ephemeral=True)
        if suggestion["status"] != "pending":
            return await interaction.response.send_message(
                embed=warning("This suggestion has already been reviewed."), ephemeral=True)

        result = db.vote_suggestion(str(interaction.message.id), str(interaction.user.id), direction)
        if result == "error":
            return await interaction.response.send_message(
                embed=error("An error occurred."), ephemeral=True)

        suggestion = db.get_suggestion_by_message(str(interaction.message.id))
        self.children[0].label = str(suggestion["upvotes"])
        self.children[1].label = str(suggestion["downvotes"])

        msgs = {"added": "Vote recorded!", "removed": "Vote removed!", "changed": "Vote changed!"}
        await interaction.response.edit_message(view=self)

    @discord.ui.button(label="0", emoji="👍", style=discord.ButtonStyle.success, custom_id="suggestion_upvote")
    async def upvote(self, interaction, button):
        await self._vote(interaction, "up")

    @discord.ui.button(label="0", emoji="👎", style=discord.ButtonStyle.danger, custom_id="suggestion_downvote")
    async def downvote(self, interaction, button):
        await self._vote(interaction, "down")

    @discord.ui.button(label="Votes", style=discord.ButtonStyle.secondary, custom_id="suggestion_voters")
    async def voters(self, interaction, button):
        suggestion = db.get_suggestion_by_message(str(interaction.message.id))
        if not suggestion:
            return await interaction.response.send_message(
                embed=error("This suggestion no longer exists."), ephemeral=True)

        voters_list = suggestion.get("voters", [])
        if not voters_list:
            return await interaction.response.send_message(
                embed=info("No one has voted yet."), ephemeral=True)

        up = [v["user_id"] for v in voters_list if v.get("vote") == "up"]
        down = [v["user_id"] for v in voters_list if v.get("vote") == "down"]

        embed = discord.Embed(color=discord.Colour(0x3498DB))
        embed.add_field(name=f"👍 Upvotes ({len(up)})", value="\n".join(f"<@{u}>" for u in up) or "*None*", inline=True)
        embed.add_field(name=f"👎 Downvotes ({len(down)})", value="\n".join(f"<@{u}>" for u in down) or "*None*", inline=True)
        await interaction.response.send_message(embed=embed, ephemeral=True)


class Suggestions(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.hybrid_command(name="suggest", description="Submit a suggestion")
    @app_commands.describe(suggestion="Your suggestion")
    async def suggest(self, ctx, *, suggestion: str):
        settings = db.get_guild_settings(ctx.guild.id)
        cfg = settings.get("suggestions", {})

        if not cfg.get("enabled", False):
            return await ctx.send(embed=error("Suggestions are not enabled on this server."), ephemeral=True)

        channel_id = cfg.get("channel")
        if not channel_id:
            return await ctx.send(embed=error("No suggestions channel configured."), ephemeral=True)

        channel = ctx.guild.get_channel(int(channel_id))
        if not channel:
            return await ctx.send(embed=error("Suggestions channel not found."), ephemeral=True)

        embed = _build_embed(suggestion, ctx.author)
        view = SuggestionView()

        msg = await channel.send(embed=embed, view=view)

        # Ping roles
        ping_roles = cfg.get("ping_roles", [])
        if ping_roles:
            mentions = [ctx.guild.get_role(int(r)).mention for r in ping_roles if ctx.guild.get_role(int(r))]
            if mentions:
                ping_msg = await channel.send(" ".join(mentions))
                await ping_msg.delete()

        try:
            thread_name = f"Suggestion by @{ctx.author.name}"
            thread = await msg.create_thread(name=thread_name)
        except (discord.Forbidden, discord.HTTPException):
            pass

        db.create_suggestion(ctx.guild.id, {
            "user_id": str(ctx.author.id),
            "message_id": str(msg.id),
            "channel_id": str(channel.id),
            "content": suggestion,
            "status": "pending",
            "upvotes": 0,
            "downvotes": 0,
            "voters": [],
        })

        await ctx.send(embed=success(f"Suggestion submitted in {channel.mention}"), ephemeral=True)

    @commands.hybrid_command(name="suggestion", description="Manage a suggestion (staff)")
    @app_commands.describe(action="Action", message_id="Suggestion message ID", response="Staff response")
    @app_commands.choices(action=[
        app_commands.Choice(name="Approve", value="approved"),
        app_commands.Choice(name="Deny", value="denied"),
        app_commands.Choice(name="Consider", value="considered"),
        app_commands.Choice(name="Mark Implemented", value="implemented"),
    ])
    async def manage_suggestion(self, ctx, action: str, message_id: str, response: str = None):
        if not has_mod_role(ctx.author) and not has_admin_role(ctx.author):
            return await ctx.send(embed=error("No permission."), ephemeral=True)

        suggestion = db.get_suggestion_by_message(message_id)
        if not suggestion:
            return await ctx.send(embed=error("Suggestion not found."), ephemeral=True)

        updates = {"status": action, "responded_by": str(ctx.author.id)}
        if response:
            updates["staff_response"] = response
        db.update_suggestion(ctx.guild.id, suggestion["id"], updates)

        try:
            ch = ctx.guild.get_channel(int(suggestion["channel_id"]))
            if ch:
                msg = await ch.fetch_message(int(message_id))
                if msg:
                    submitter = ctx.guild.get_member(int(suggestion["user_id"]))
                    embed = _build_embed(
                        suggestion["content"], submitter or ctx.author,
                        suggestion["upvotes"], suggestion["downvotes"],
                        action, response, ctx.author,
                    )
                    await msg.edit(embed=embed, view=None)
        except Exception as e:
            print(f"[Suggestions] Error updating message: {e}")

        label = STATUS_LABELS.get(action, action)
        await ctx.send(embed=success(f"Suggestion marked as **{label}**."), ephemeral=True)


async def setup(bot):
    await bot.add_cog(Suggestions(bot))
