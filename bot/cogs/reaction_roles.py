import discord
from discord.ext import commands
from discord import app_commands
from database import db
from helpers.embeds import success, error, info
from helpers import has_admin_role, is_module_enabled

BUTTON_STYLES = {
    "primary": discord.ButtonStyle.primary,
    "secondary": discord.ButtonStyle.secondary,
    "success": discord.ButtonStyle.success,
    "danger": discord.ButtonStyle.danger,
    "blurple": discord.ButtonStyle.primary,
    "gray": discord.ButtonStyle.secondary,
    "green": discord.ButtonStyle.success,
    "red": discord.ButtonStyle.danger,
}


class ReactionRolePanelView(discord.ui.View):
    """Dynamic view for reaction role panels with buttons."""

    def __init__(self, panel_id: str = None):
        super().__init__(timeout=None)
        self.panel_id = panel_id


class ReactionRoleButton(discord.ui.Button):
    """Button for a specific reaction role."""

    def __init__(self, panel_id: str, role_config: dict, index: int):
        style = BUTTON_STYLES.get(
            role_config.get("button_style", "primary"), discord.ButtonStyle.primary
        )
        emoji = role_config.get("emoji")
        emoji = emoji if emoji and emoji.strip() else None
        label = role_config.get("label", "Get Role")
        super().__init__(
            label=label,
            style=style,
            emoji=emoji,
            custom_id=f"reaction_role_{panel_id}_{index}",
        )
        self.panel_id = panel_id
        self.role_config = role_config
        self.role_index = index

    async def callback(self, interaction: discord.Interaction):
        if not is_module_enabled(interaction.guild.id, "reaction_roles"):
            await interaction.response.send_message(
                embed=error("Reaction roles are disabled on this server."), ephemeral=True
            )
            return

        role_id = self.role_config.get("role_id")
        if not role_id:
            await interaction.response.send_message(
                embed=error("This role is not configured properly."), ephemeral=True
            )
            return

        role = interaction.guild.get_role(int(role_id))
        if not role:
            await interaction.response.send_message(
                embed=error("The configured role no longer exists."), ephemeral=True
            )
            return

        # Check if bot can manage this role
        if role >= interaction.guild.me.top_role:
            await interaction.response.send_message(
                embed=error("I cannot manage this role (it's higher than my role)."), ephemeral=True
            )
            return

        member = interaction.user
        panel = db.get_reaction_role_panel(interaction.guild.id, self.panel_id)
        mode = panel.get("mode", "toggle") if panel else "toggle"

        if role in member.roles:
            # Remove role
            try:
                await member.remove_roles(role, reason="Reaction role")
                await interaction.response.send_message(
                    embed=success(f"Removed the **{role.name}** role."), ephemeral=True
                )
            except discord.Forbidden:
                await interaction.response.send_message(
                    embed=error("I don't have permission to remove this role."), ephemeral=True
                )
        else:
            # Check if single-select mode (only one role from panel at a time)
            if mode == "single" and panel:
                roles_in_panel = [rc.get("role_id") for rc in panel.get("roles", [])]
                for r_id in roles_in_panel:
                    existing_role = (
                        interaction.guild.get_role(int(r_id)) if r_id else None
                    )
                    if (
                        existing_role
                        and existing_role in member.roles
                        and existing_role.id != role.id
                    ):
                        try:
                            await member.remove_roles(
                                existing_role,
                                reason="Reaction role - single select mode",
                            )
                        except:
                            pass

            # Add role
            try:
                await member.add_roles(role, reason="Reaction role")
                await interaction.response.send_message(
                    embed=success(f"Gave you the **{role.name}** role!"), ephemeral=True
                )
            except discord.Forbidden:
                await interaction.response.send_message(
                    embed=error("I don't have permission to give you this role."), ephemeral=True
                )


class ReactionRoleSelect(discord.ui.Select):
    """Dropdown select for reaction roles."""

    def __init__(self, panel_id: str, roles: list):
        self.panel_id = panel_id
        self.roles_config = roles

        options = []
        for i, role_config in enumerate(roles[:25]):
            emoji = role_config.get("emoji")
            emoji = emoji if emoji and emoji.strip() else None
            options.append(
                discord.SelectOption(
                    label=role_config.get("label", "Get Role"),
                    value=str(i),
                    emoji=emoji,
                    description=(
                        role_config.get("description", "")[:100]
                        if role_config.get("description")
                        else None
                    ),
                )
            )

        super().__init__(
            placeholder="Select a role...",
            options=options,
            custom_id=f"reaction_role_select_{panel_id}",
        )

    async def callback(self, interaction: discord.Interaction):
        if not is_module_enabled(interaction.guild.id, "reaction_roles"):
            await interaction.response.send_message(
                embed=error("Reaction roles are disabled on this server."), ephemeral=True
            )
            return

        selected_index = int(self.values[0])
        role_config = self.roles_config[selected_index]
        role_id = role_config.get("role_id")

        if not role_id:
            await interaction.response.send_message(
                embed=error("This role is not configured properly."), ephemeral=True
            )
            return

        role = interaction.guild.get_role(int(role_id))
        if not role:
            await interaction.response.send_message(
                embed=error("The configured role no longer exists."), ephemeral=True
            )
            return

        if role >= interaction.guild.me.top_role:
            await interaction.response.send_message(
                embed=error("I cannot manage this role (it's higher than my role)."), ephemeral=True
            )
            return

        member = interaction.user
        panel = db.get_reaction_role_panel(interaction.guild.id, self.panel_id)
        mode = panel.get("mode", "toggle") if panel else "toggle"

        if role in member.roles:
            try:
                await member.remove_roles(role, reason="Reaction role")
                await interaction.response.send_message(
                    embed=success(f"Removed the **{role.name}** role."), ephemeral=True
                )
            except discord.Forbidden:
                await interaction.response.send_message(
                    embed=error("I don't have permission to remove this role."), ephemeral=True
                )
        else:
            # Single select mode - remove other roles from this panel first
            if mode == "single" and panel:
                roles_in_panel = [rc.get("role_id") for rc in panel.get("roles", [])]
                for r_id in roles_in_panel:
                    existing_role = (
                        interaction.guild.get_role(int(r_id)) if r_id else None
                    )
                    if (
                        existing_role
                        and existing_role in member.roles
                        and existing_role.id != role.id
                    ):
                        try:
                            await member.remove_roles(
                                existing_role,
                                reason="Reaction role - single select mode",
                            )
                        except:
                            pass

            try:
                await member.add_roles(role, reason="Reaction role")
                await interaction.response.send_message(
                    embed=success(f"Gave you the **{role.name}** role!"), ephemeral=True
                )
            except discord.Forbidden:
                await interaction.response.send_message(
                    embed=error("I don't have permission to give you this role."), ephemeral=True
                )


def create_reaction_role_view(panel: dict) -> ReactionRolePanelView | None:
    """Create a view with buttons or dropdown for reaction roles in the panel.
    Returns None if using reactions mode (emoji-based)."""
    interaction_type = panel.get("interaction_type", "buttons")

    # Legacy support: use_dropdown overrides if interaction_type not set
    if not panel.get("interaction_type") and panel.get("use_dropdown"):
        interaction_type = "dropdown"

    # Reactions mode doesn't use a view
    if interaction_type == "reactions":
        return None

    view = ReactionRolePanelView(panel.get("id"))
    roles = panel.get("roles", [])

    if not roles:
        return view

    # Check if dropdown mode is enabled
    if interaction_type == "dropdown":
        select = ReactionRoleSelect(panel.get("id"), roles)
        view.add_item(select)
    else:
        for i, role_config in enumerate(roles[:25]):
            button = ReactionRoleButton(panel.get("id"), role_config, i)
            view.add_item(button)

    return view


class ReactionRoles(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.hybrid_command(
        name="reactionrole",
        description="Send a reaction role panel (create panels in dashboard)",
    )
    @app_commands.describe(panel_id="ID of the panel to send (from dashboard)")
    async def reactionrole(self, ctx: commands.Context, panel_id: str = None):
        if not is_module_enabled(ctx.guild.id, "reaction_roles"):
            await ctx.send(embed=error("The reaction roles module is disabled on this server."))
            return
        if not has_admin_role(ctx.author):
            await ctx.send(embed=error("You don't have permission to use this command."))
            return

        if panel_id:
            panel = db.get_reaction_role_panel(ctx.guild.id, panel_id)
            if not panel:
                await ctx.send(embed=error("Panel not found. Create panels in the dashboard."))
                return
        else:
            panels = db.get_all_reaction_role_panels(ctx.guild.id)
            if not panels:
                await ctx.send(
                    embed=info("No reaction role panels found. Create panels in the dashboard first.")
                )
                return
            elif len(panels) == 1:
                panel = panels[0]
            else:
                embed = discord.Embed(
                    title="Available Reaction Role Panels", color=discord.Color.blurple()
                )
                for p in panels:
                    embed.add_field(
                        name=p.get("name", "Unnamed"),
                        value=f"ID: `{p['id']}`",
                        inline=False,
                    )
                embed.set_footer(text="Use !reactionrole <id> to send a specific panel")
                await ctx.send(embed=embed)
                return

        # Send the panel
        embed_color = panel.get("embed_color", "#5865F2")
        try:
            color = discord.Color.from_str(embed_color)
        except:
            color = discord.Color.blurple()

        interaction_type = panel.get("interaction_type", "buttons")
        if not panel.get("interaction_type") and panel.get("use_dropdown"):
            interaction_type = "dropdown"

        # Adjust description based on interaction type
        default_desc = "Click a button below to get a role."
        if interaction_type == "dropdown":
            default_desc = "Select a role from the dropdown below."
        elif interaction_type == "reactions":
            default_desc = "React below to get a role."

        embed = discord.Embed(
            title=panel.get("title", "Get Roles"),
            description=panel.get("description", default_desc),
            color=color,
        )

        if panel.get("footer"):
            embed.set_footer(text=panel["footer"])
        else:
            embed.set_footer(text=ctx.guild.name)

        view = create_reaction_role_view(panel)
        msg = await ctx.send(embed=embed, view=view)

        # For reactions mode, add the emoji reactions to the message
        if interaction_type == "reactions":
            roles_list = panel.get("roles", [])
            for role_config in roles_list:
                emoji = role_config.get("emoji", "").strip()
                if emoji:
                    try:
                        await msg.add_reaction(emoji)
                    except discord.HTTPException:
                        pass  # Invalid emoji or can't react

        # Save message ID to panel for persistence
        db.update_reaction_role_panel(
            ctx.guild.id,
            panel["id"],
            {"message_id": str(msg.id), "channel_id": str(ctx.channel.id)},
        )

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        """Handle reaction add for reaction-based role panels."""
        if payload.user_id == self.bot.user.id:
            return  # Ignore bot's own reactions

        if not payload.guild_id:
            return  # Ignore DMs

        guild = self.bot.get_guild(payload.guild_id)
        if not guild:
            return

        if not is_module_enabled(guild.id, "reaction_roles"):
            return

        # Find the panel this reaction belongs to
        panels = db.get_all_reaction_role_panels(guild.id)
        for panel in panels:
            if panel.get("message_id") == str(payload.message_id):
                interaction_type = panel.get("interaction_type", "buttons")
                if not panel.get("interaction_type") and panel.get("use_dropdown"):
                    interaction_type = "dropdown"

                if interaction_type != "reactions":
                    return  # This panel uses buttons/dropdown, not reactions

                # Find the role for this emoji
                emoji_str = str(payload.emoji)
                for role_config in panel.get("roles", []):
                    config_emoji = role_config.get("emoji", "").strip()
                    if config_emoji == emoji_str or config_emoji == payload.emoji.name:
                        role_id = role_config.get("role_id")
                        if not role_id:
                            return

                        role = guild.get_role(int(role_id))
                        if not role:
                            return

                        member = guild.get_member(payload.user_id)
                        if not member:
                            try:
                                member = await guild.fetch_member(payload.user_id)
                            except:
                                return

                        # Check if bot can manage this role
                        if role >= guild.me.top_role:
                            return

                        mode = panel.get("mode", "toggle")

                        # Single select mode - remove other roles first
                        if mode == "single":
                            roles_in_panel = [
                                rc.get("role_id") for rc in panel.get("roles", [])
                            ]
                            for r_id in roles_in_panel:
                                existing_role = guild.get_role(int(r_id)) if r_id else None
                                if (
                                    existing_role
                                    and existing_role in member.roles
                                    and existing_role.id != role.id
                                ):
                                    try:
                                        await member.remove_roles(
                                            existing_role,
                                            reason="Reaction role - single select mode",
                                        )
                                    except:
                                        pass

                        try:
                            await member.add_roles(role, reason="Reaction role")
                        except discord.Forbidden:
                            pass
                        return
                return

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent):
        """Handle reaction remove for reaction-based role panels."""
        if payload.user_id == self.bot.user.id:
            return  # Ignore bot's own reactions

        if not payload.guild_id:
            return  # Ignore DMs

        guild = self.bot.get_guild(payload.guild_id)
        if not guild:
            return

        if not is_module_enabled(guild.id, "reaction_roles"):
            return

        # Find the panel this reaction belongs to
        panels = db.get_all_reaction_role_panels(guild.id)
        for panel in panels:
            if panel.get("message_id") == str(payload.message_id):
                interaction_type = panel.get("interaction_type", "buttons")
                if not panel.get("interaction_type") and panel.get("use_dropdown"):
                    interaction_type = "dropdown"

                if interaction_type != "reactions":
                    return  # This panel uses buttons/dropdown, not reactions

                # Find the role for this emoji
                emoji_str = str(payload.emoji)
                for role_config in panel.get("roles", []):
                    config_emoji = role_config.get("emoji", "").strip()
                    if config_emoji == emoji_str or config_emoji == payload.emoji.name:
                        role_id = role_config.get("role_id")
                        if not role_id:
                            return

                        role = guild.get_role(int(role_id))
                        if not role:
                            return

                        member = guild.get_member(payload.user_id)
                        if not member:
                            try:
                                member = await guild.fetch_member(payload.user_id)
                            except:
                                return

                        # Check if bot can manage this role
                        if role >= guild.me.top_role:
                            return

                        try:
                            await member.remove_roles(role, reason="Reaction role removed")
                        except discord.Forbidden:
                            pass
                        return
                return


async def setup(bot: commands.Bot):
    await bot.add_cog(ReactionRoles(bot))
