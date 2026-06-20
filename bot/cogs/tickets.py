import discord
from discord.ext import commands
from discord import app_commands
from datetime import datetime

from database import db
from config import config
from helpers import has_mod_role, has_admin_role, is_module_enabled
from helpers.embeds import success, error, warning, info


PRIORITY_CONFIG = {
    "low": {"color": discord.Color.green(), "emoji": "🟢", "label": "Low"},
    "medium": {"color": discord.Color.gold(), "emoji": "🟡", "label": "Medium"},
    "high": {"color": discord.Color.orange(), "emoji": "🟠", "label": "High"},
    "urgent": {"color": discord.Color.red(), "emoji": "🔴", "label": "Urgent"},
}


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


class TicketPanelView(discord.ui.View):

    def __init__(self, panel_id: str = None):
        super().__init__(timeout=None)
        self.panel_id = panel_id


class TicketTypeButton(discord.ui.Button):

    def __init__(self, panel_id: str, ticket_type: dict, index: int):
        style = BUTTON_STYLES.get(
            ticket_type.get("button_style", "primary"), discord.ButtonStyle.primary
        )
        emoji = ticket_type.get("emoji")
        # Only pass emoji if it's a non-empty string
        emoji = emoji if emoji and emoji.strip() else None
        super().__init__(
            label=ticket_type.get("name", "Create Ticket"),
            style=style,
            emoji=emoji,
            custom_id=f"ticket_panel_{panel_id}_{index}",
        )
        self.panel_id = panel_id
        self.ticket_type = ticket_type
        self.type_index = index

    async def callback(self, interaction: discord.Interaction):
        # Check if modal is enabled for this ticket type
        if self.ticket_type.get("use_modal") and self.ticket_type.get(
            "modal_questions"
        ):
            modal = TicketModal(self.panel_id, self.ticket_type)
            await interaction.response.send_modal(modal)
        else:
            await create_ticket_from_panel(interaction, self.panel_id, self.ticket_type)


class TicketModal(discord.ui.Modal):
    """Modal for collecting ticket information before creating."""

    def __init__(self, panel_id: str, ticket_type: dict):
        title = (
            ticket_type.get("modal_title")
            or f"Create {ticket_type.get('name', 'Ticket')}"
        )
        super().__init__(title=title[:45])
        self.panel_id = panel_id
        self.ticket_type = ticket_type
        self.answers = []

        questions = ticket_type.get("modal_questions", [])[:5]
        for i, q in enumerate(questions):
            style = (
                discord.TextStyle.long
                if q.get("style") == "long"
                else discord.TextStyle.short
            )
            text_input = discord.ui.TextInput(
                label=q.get("label", f"Question {i+1}")[:45],
                style=style,
                placeholder=(
                    q.get("placeholder", "")[:100] if q.get("placeholder") else None
                ),
                required=q.get("required", False),
                max_length=1000 if style == discord.TextStyle.long else 100,
            )
            self.add_item(text_input)

    async def on_submit(self, interaction: discord.Interaction):
        # Collect answers
        answers = []
        for child in self.children:
            if isinstance(child, discord.ui.TextInput):
                answers.append({"question": child.label, "answer": child.value})

        await create_ticket_from_panel(
            interaction, self.panel_id, self.ticket_type, modal_answers=answers
        )


class TicketTypeSelect(discord.ui.Select):
    """Dropdown select for ticket types."""

    def __init__(self, panel_id: str, ticket_types: list):
        self.panel_id = panel_id
        self.ticket_types = ticket_types

        options = []
        for i, ticket_type in enumerate(ticket_types[:25]):
            emoji = ticket_type.get("emoji")
            emoji = emoji if emoji and emoji.strip() else None
            options.append(
                discord.SelectOption(
                    label=ticket_type.get("name", "Create Ticket"),
                    value=str(i),
                    emoji=emoji,
                    description=(
                        ticket_type.get("description", "")[:100]
                        if ticket_type.get("description")
                        else None
                    ),
                )
            )

        super().__init__(
            placeholder="Select a ticket type...",
            options=options,
            custom_id=f"ticket_select_{panel_id}",
        )

    async def callback(self, interaction: discord.Interaction):
        selected_index = int(self.values[0])
        ticket_type = self.ticket_types[selected_index]
        # Check if modal is enabled for this ticket type
        if ticket_type.get("use_modal") and ticket_type.get("modal_questions"):
            modal = TicketModal(self.panel_id, ticket_type)
            await interaction.response.send_modal(modal)
        else:
            await create_ticket_from_panel(interaction, self.panel_id, ticket_type)


class TicketControlView(discord.ui.View):
    def __init__(self, claimed: bool = False):
        super().__init__(timeout=None)
        self.claimed = claimed
        if claimed:
            # Remove claim button if already claimed
            self.remove_item(self.claim_ticket)

    @discord.ui.button(
        label="Close", style=discord.ButtonStyle.danger, custom_id="ticket_close"
    )
    async def close_ticket(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        await close_ticket_channel(interaction)

    @discord.ui.button(
        label="Claim", style=discord.ButtonStyle.success, custom_id="ticket_claim"
    )
    async def claim_ticket(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        await claim_ticket_channel(interaction, self)


class TicketCloseConfirmView(discord.ui.View):
    def __init__(self, reason: str = None):
        super().__init__(timeout=60)
        self.reason = reason

    @discord.ui.button(
        label="Confirm Close",
        style=discord.ButtonStyle.danger,
        custom_id="ticket_close_confirm",
    )
    async def confirm_close(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        await delete_ticket_channel(interaction, self.reason)

    @discord.ui.button(
        label="Cancel",
        style=discord.ButtonStyle.secondary,
        custom_id="ticket_close_cancel",
    )
    async def cancel_close(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        await interaction.response.send_message(
            embed=info("Ticket close cancelled."), ephemeral=True
        )
        self.stop()


class CloseRequestView(discord.ui.View):
    """View for close request buttons - never times out, persistent across restarts"""

    def __init__(self, reason: str = None):
        super().__init__(timeout=None)  # Never times out
        self.reason = reason

    @discord.ui.button(
        label="Accept",
        style=discord.ButtonStyle.success,
        custom_id="close_request_accept",
    )
    async def accept_close(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        ticket = db.get_ticket(interaction.guild.id, interaction.channel.id)
        if not ticket:
            await interaction.response.send_message(
                embed=error("This is not a ticket channel."), ephemeral=True
            )
            return

        # Only ticket opener can accept
        if str(interaction.user.id) != ticket.get("user_id"):
            await interaction.response.send_message(
                embed=error("Only the ticket opener can accept this close request."), ephemeral=True
            )
            return

        # Extract reason from embed if we don't have it (after bot restart)
        reason = self.reason
        if not reason and interaction.message.embeds:
            embed_desc = interaction.message.embeds[0].description or ""
            if "**Reason:**" in embed_desc:
                reason = embed_desc.split("**Reason:**")[1].strip()

        # Disable buttons
        for child in self.children:
            child.disabled = True
        await interaction.message.edit(view=self)

        await interaction.response.send_message(
            embed=success(f"{interaction.user.mention} accepted the close request. Closing ticket...")
        )
        await delete_ticket_channel(interaction, reason)

    @discord.ui.button(
        label="Deny", style=discord.ButtonStyle.danger, custom_id="close_request_deny"
    )
    async def deny_close(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        ticket = db.get_ticket(interaction.guild.id, interaction.channel.id)
        if not ticket:
            await interaction.response.send_message(
                embed=error("This is not a ticket channel."), ephemeral=True
            )
            return

        # Only ticket opener can deny
        if str(interaction.user.id) != ticket.get("user_id"):
            await interaction.response.send_message(
                embed=error("Only the ticket opener can deny this close request."), ephemeral=True
            )
            return

        # Disable buttons
        for child in self.children:
            child.disabled = True
        await interaction.message.edit(view=self)

        await interaction.response.send_message(
            embed=info(f"{interaction.user.mention} denied the close request. The ticket will remain open.")
        )


class FeedbackView(discord.ui.View):
    """View for collecting ticket feedback ratings (1-5 stars) via DM."""

    def __init__(self, guild_id: str, ticket_number: int, user_id: str):
        super().__init__(timeout=300)
        self.guild_id = guild_id
        self.ticket_number = ticket_number
        self.user_id = user_id
        for i in range(1, 6):
            button = FeedbackButton(guild_id, ticket_number, user_id, i)
            self.add_item(button)


class FeedbackButton(discord.ui.Button):

    def __init__(self, guild_id: str, ticket_number: int, user_id: str, rating: int):
        star = "\u2B50" * rating
        super().__init__(
            label=f"{rating}",
            style=discord.ButtonStyle.secondary if rating < 4 else discord.ButtonStyle.success,
            custom_id=f"feedback_{guild_id}_{ticket_number}_{rating}",
        )
        self.guild_id = guild_id
        self.ticket_number = ticket_number
        self.user_id = user_id
        self.rating = rating

    async def callback(self, interaction: discord.Interaction):
        db.save_ticket_feedback(self.guild_id, self.ticket_number, self.user_id, self.rating)
        for child in self.view.children:
            child.disabled = True
        await interaction.response.edit_message(view=self.view)
        stars = "\u2B50" * self.rating
        await interaction.followup.send(
            embed=success(f"Thank you for your feedback! You rated this ticket {stars} ({self.rating}/5)."),
            ephemeral=True,
        )
        self.view.stop()


def create_panel_view(panel: dict) -> TicketPanelView:
    """Create a view with buttons or dropdown for ticket types in the panel."""
    view = TicketPanelView(panel.get("id"))
    ticket_types = panel.get("ticket_types", [])

    if not ticket_types:
        # Default single button if no types defined
        ticket_types = [{"name": "Create Ticket", "button_style": "primary"}]

    # Check if dropdown mode is enabled
    if panel.get("use_dropdown", False):
        select = TicketTypeSelect(panel.get("id"), ticket_types)
        view.add_item(select)
    else:
        for i, ticket_type in enumerate(
            ticket_types[:25]
        ):  # Discord limit is 25 buttons
            button = TicketTypeButton(panel.get("id"), ticket_type, i)
            view.add_item(button)

    return view


async def create_ticket_from_panel(
    interaction: discord.Interaction,
    panel_id: str,
    ticket_type: dict,
    modal_answers: list = None,
):
    panel = db.get_ticket_panel(interaction.guild.id, panel_id)
    if not panel:
        if not interaction.response.is_done():
            await interaction.response.send_message(
                embed=error("This ticket panel no longer exists."), ephemeral=True
            )
        return

    # Check if user is ticket banned
    if db.is_ticket_banned(interaction.guild.id, interaction.user.id):
        if not interaction.response.is_done():
            await interaction.response.send_message(
                embed=error("You are banned from creating tickets."), ephemeral=True
            )
        return

    # Check if user has the ticket blacklist role
    settings = db.get_guild_settings(interaction.guild.id)
    blacklist_role_id = settings.get("ticket_blacklist_role")
    if blacklist_role_id:
        blacklist_role = interaction.guild.get_role(int(blacklist_role_id))
        if blacklist_role and blacklist_role in interaction.user.roles:
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    embed=error("You are not allowed to create tickets."), ephemeral=True
                )
            return

    # Check global ticket limit
    global_ticket_limit = settings.get("ticket_limit", 3)
    all_user_tickets = db.get_user_tickets(interaction.guild.id, interaction.user.id)
    if len(all_user_tickets) >= global_ticket_limit:
        if not interaction.response.is_done():
            await interaction.response.send_message(
                embed=warning(f"You already have {len(all_user_tickets)} open ticket(s). Global limit: {global_ticket_limit}"),
                ephemeral=True,
            )
        return

    # Check max tickets per user for this panel
    max_tickets = panel.get("max_per_user", 1)
    user_tickets = db.get_user_tickets(
        interaction.guild.id, interaction.user.id, panel_id
    )
    if len(user_tickets) >= max_tickets:
        if not interaction.response.is_done():
            await interaction.response.send_message(
                embed=warning(f"You already have {len(user_tickets)} open ticket(s) for this panel. Max allowed: {max_tickets}"),
                ephemeral=True,
            )
        return

    # Defer the response early to prevent timeout
    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=True)

    # Get category (ticket type specific or panel default)
    category_id = ticket_type.get("category") or panel.get("category")
    category = None
    if category_id:
        category = interaction.guild.get_channel(int(category_id))

    # Get support roles (ticket type specific or panel default)
    # Support both legacy support_role and new support_roles
    support_role_ids = (
        ticket_type.get("support_roles") or panel.get("support_roles") or []
    )
    # Fallback to legacy single role if no array
    if not support_role_ids:
        legacy_role = ticket_type.get("support_role") or panel.get("support_role")
        if legacy_role:
            support_role_ids = [legacy_role]
    support_roles = []
    for role_id in support_role_ids:
        role = interaction.guild.get_role(int(role_id))
        if role:
            support_roles.append(role)

    # Create ticket number
    ticket_num = db.get_next_ticket_number(interaction.guild.id)

    # Channel name format
    type_name = ticket_type.get("name", "ticket").lower().replace(" ", "-")
    channel_prefix = ticket_type.get("channel_prefix", "")

    if channel_prefix:
        # Use custom prefix (e.g., "gs-", "ia-")
        channel_name = f"{channel_prefix}{ticket_num}"
    else:
        # Use name format
        name_format = ticket_type.get("name_format") or panel.get(
            "name_format", "{type}-{number}"
        )
        channel_name = (
            name_format.replace("{number}", str(ticket_num))
            .replace("{user}", interaction.user.name.lower()[:20])
            .replace("{type}", type_name[:20])
        )

    # Set permissions
    overwrites = {
        interaction.guild.default_role: discord.PermissionOverwrite(view_channel=False),
        interaction.user: discord.PermissionOverwrite(
            view_channel=True, send_messages=True, attach_files=True, embed_links=True
        ),
        interaction.guild.me: discord.PermissionOverwrite(
            view_channel=True, send_messages=True, manage_channels=True
        ),
    }
    for support_role in support_roles:
        overwrites[support_role] = discord.PermissionOverwrite(
            view_channel=True, send_messages=True, attach_files=True, embed_links=True
        )

    # Create channel
    channel = await interaction.guild.create_text_channel(
        name=channel_name, category=category, overwrites=overwrites
    )

    db.create_ticket(
        interaction.guild.id,
        {
            "channel_id": str(channel.id),
            "user_id": str(interaction.user.id),
            "ticket_number": ticket_num,
            "panel_id": panel_id,
            "ticket_type": ticket_type.get("name", "General"),
            "status": "open",
            "created_at": datetime.utcnow().isoformat(),
            "claimed_by": None,
        },
    )

    # Send welcome message with placeholder support
    welcome_msg = ticket_type.get("welcome_message") or panel.get(
        "welcome_message", "Welcome {user}! Support will be with you shortly."
    )

    # Get user ticket counts for placeholders
    user_ticket_counts = db.get_user_ticket_counts(
        interaction.guild.id, interaction.user.id
    )
    now = datetime.utcnow()

    # Replace placeholders (support both {placeholder} and %placeholder% formats)
    replacements = {
        "user": interaction.user.mention,
        "username": interaction.user.name,
        "user_id": str(interaction.user.id),
        "type": ticket_type.get("name", "General"),
        "ticket_id": str(ticket_num),
        "ticket_number": str(ticket_num),
        "user_open_tickets": str(user_ticket_counts["open"]),
        "user_total_tickets": str(user_ticket_counts["total"]),
        "datetime": now.strftime("%Y-%m-%d %H:%M:%S UTC"),
        "date": now.strftime("%Y-%m-%d"),
        "time": now.strftime("%H:%M:%S UTC"),
        "server": interaction.guild.name,
        "channel": f"<#{channel.id}>",
    }

    for key, value in replacements.items():
        welcome_msg = welcome_msg.replace(f"{{{key}}}", value)  # {placeholder}
        welcome_msg = welcome_msg.replace(f"%{key}%", value)  # %placeholder%

    # Embed color
    embed_color = panel.get("embed_color", "#5865F2")
    try:
        color = discord.Color.from_str(embed_color)
    except:
        color = discord.Color.green()

    embed = discord.Embed(
        title=f"Ticket #{ticket_num} - {ticket_type.get('name', 'General')}",
        description=welcome_msg,
        color=color,
    )
    embed.add_field(name="Created by", value=interaction.user.mention, inline=True)
    embed.add_field(name="Type", value=ticket_type.get("name", "General"), inline=True)
    embed.timestamp = datetime.utcnow()

    await channel.send(embed=embed, view=TicketControlView())

    # Send modal answers if provided
    if modal_answers:
        answers_embed = discord.Embed(title="Ticket Information", color=color)
        for answer in modal_answers:
            question = answer.get("question", "Question")
            response = answer.get("answer", "No response")
            if response:  # Only add if there's an answer
                answers_embed.add_field(
                    name=question,
                    value=response[:1024] or "No response",  # Field value limit
                    inline=False,
                )
        if answers_embed.fields:  # Only send if there are answers
            await channel.send(embed=answers_embed)

    # Ping roles if configured (per ticket type)
    ping_support = ticket_type.get("ping_support", panel.get("ping_support", True))
    if ping_support:
        # Check for dedicated ping_roles on ticket type, fall back to support_roles
        ping_role_ids = ticket_type.get("ping_roles") or []
        if ping_role_ids:
            ping_roles = []
            for role_id in ping_role_ids:
                role = interaction.guild.get_role(int(role_id))
                if role:
                    ping_roles.append(role)
        else:
            # Fall back to support roles if no ping_roles configured
            ping_roles = support_roles

        if ping_roles:
            mentions = " ".join([role.mention for role in ping_roles])
            ping_msg = await channel.send(mentions)
            await ping_msg.delete()

    await interaction.followup.send(
        embed=success(f"Ticket created! {channel.mention}"), ephemeral=True
    )


async def close_ticket_channel(interaction: discord.Interaction):
    ticket = db.get_ticket(interaction.guild.id, interaction.channel.id)
    if not ticket:
        await interaction.response.send_message(
            embed=error("This is not a ticket channel."), ephemeral=True
        )
        return

    embed = discord.Embed(
        title="Close Ticket?",
        description="Are you sure you want to close this ticket? This action will delete the channel.",
        color=discord.Color.orange(),
    )
    await interaction.response.send_message(embed=embed, view=TicketCloseConfirmView())


async def delete_ticket_channel(interaction_or_ctx, reason: str = None, silent: bool = False, no_transcript: bool = False, no_log: bool = False):
    # Normalize interaction vs context
    is_interaction = isinstance(interaction_or_ctx, discord.Interaction)
    if is_interaction:
        interaction = interaction_or_ctx
        guild = interaction.guild
        channel = interaction.channel
        user = interaction.user
        client = interaction.client
    else:
        ctx = interaction_or_ctx
        guild = ctx.guild
        channel = ctx.channel
        user = ctx.author
        client = ctx.bot
        interaction = None

    ticket = db.get_ticket(guild.id, channel.id)
    if not ticket:
        if is_interaction:
            await interaction.response.send_message(
                embed=error("This is not a ticket channel."), ephemeral=True
            )
        else:
            await ctx.send(embed=error("This is not a ticket channel."))
        return

    # Defer response immediately since this takes time
    if is_interaction and not interaction.response.is_done():
        await interaction.response.defer(ephemeral=True)
    elif not is_interaction:
        await ctx.send(embed=info("Closing ticket..."))

    # Get panel settings for log channel
    panel_id = ticket.get("panel_id")
    ticket_type_raw = ticket.get("ticket_type")
    # Handle ticket_type being either a string or a dict
    ticket_type_name = (
        ticket_type_raw.get("name", "General")
        if isinstance(ticket_type_raw, dict)
        else (ticket_type_raw if isinstance(ticket_type_raw, str) else "General")
    )
    settings = db.get_guild_settings(guild.id)

    log_channel_id = None
    ticket_type_transcript = None

    if panel_id:
        panel = db.get_ticket_panel(guild.id, panel_id)
        if panel:
            log_channel_id = panel.get("log_channel")
            # Check for ticket type specific transcript channel
            if ticket_type_name and panel.get("ticket_types"):
                for tt in panel["ticket_types"]:
                    if tt.get("name") == ticket_type_name:
                        ticket_type_transcript = tt.get("transcript_channel")
                        break

    # Fallback to global settings
    if not log_channel_id:
        log_channel_id = settings.get("tickets", {}).get("log_channel")

    # Use ticket type transcript override if set (takes priority)
    if ticket_type_transcript:
        log_channel_id = ticket_type_transcript

    # Get support roles for access control
    support_role_ids = []
    if panel_id:
        panel = db.get_ticket_panel(guild.id, panel_id)
        if panel:
            # Check for ticket type specific support roles
            if ticket_type_name and panel.get("ticket_types"):
                for tt in panel["ticket_types"]:
                    if tt.get("name") == ticket_type_name:
                        support_role_ids = tt.get("support_roles") or []
                        if not support_role_ids and tt.get("support_role"):
                            support_role_ids = [tt.get("support_role")]
                        break
            # Fallback to panel support roles
            if not support_role_ids:
                support_role_ids = panel.get("support_roles") or []
                if not support_role_ids and panel.get("support_role"):
                    support_role_ids = [panel.get("support_role")]

    # Build list of allowed users (will be populated as we collect messages)
    allowed_users = set()
    allowed_users.add(ticket["user_id"])  # Ticket opener always has access

    # Add users with support roles at time of closure
    for role_id in support_role_ids:
        try:
            role = guild.get_role(int(role_id))
            if role:
                for member in role.members:
                    allowed_users.add(str(member.id))
        except:
            pass

    # Collect messages and save transcript
    transcript_id = None
    messages = []

    if not no_transcript:
        async for msg in channel.history(limit=500, oldest_first=True):
            msg_data = {
                "author_id": str(msg.author.id),
                "author_name": msg.author.name,
                "author_avatar": (
                    str(msg.author.display_avatar.url)
                    if msg.author.display_avatar
                    else None
                ),
                "author_bot": msg.author.bot,
                "content": msg.content,
                "timestamp": msg.created_at.isoformat(),
                "attachments": [
                    {"url": a.url, "filename": a.filename} for a in msg.attachments
                ],
                "embeds": len(msg.embeds) > 0,
            }
            messages.append(msg_data)
            if not msg.author.bot:
                allowed_users.add(str(msg.author.id))

        transcript_data = {
            "ticket_number": ticket["ticket_number"],
            "ticket_type": ticket_type_name,
            "panel_id": panel_id,
            "channel_name": channel.name,
            "user_id": ticket["user_id"],
            "closed_by": str(user.id),
            "claimed_by": ticket.get("claimed_by"),
            "opened_at": ticket.get("created_at"),
            "messages": messages,
            "message_count": len(messages),
            "allowed_users": list(allowed_users),
        }
        transcript_id = db.save_transcript(guild.id, transcript_data)

    # Send to log channel
    if log_channel_id and not no_log:
        log_channel = guild.get_channel(int(log_channel_id))
        if log_channel:
            text_lines = []
            for msg in messages:
                if not msg["author_bot"]:
                    text_lines.append(
                        f"[{msg['timestamp'][:16].replace('T', ' ')}] {msg['author_name']}: {msg['content']}"
                    )

            log_embed = discord.Embed(
                title=f"Ticket #{ticket['ticket_number']} Closed",
                color=discord.Color.red(),
            )
            log_embed.add_field(name="Type", value=ticket_type_name, inline=True)
            log_embed.add_field(
                name="Opened by", value=f"<@{ticket['user_id']}>", inline=True
            )
            log_embed.add_field(
                name="Closed by", value=user.mention, inline=True
            )
            if ticket.get("claimed_by"):
                log_embed.add_field(
                    name="Claimed by", value=f"<@{ticket['claimed_by']}>", inline=True
                )
            # Show first response time if tracked
            first_response_at = ticket.get("first_response_at")
            if first_response_at and ticket.get("created_at"):
                try:
                    created = datetime.fromisoformat(ticket["created_at"])
                    responded = datetime.fromisoformat(first_response_at)
                    delta = responded - created
                    total_seconds = int(delta.total_seconds())
                    if total_seconds < 60:
                        response_time = f"{total_seconds}s"
                    elif total_seconds < 3600:
                        response_time = f"{total_seconds // 60}m {total_seconds % 60}s"
                    else:
                        hours = total_seconds // 3600
                        mins = (total_seconds % 3600) // 60
                        response_time = f"{hours}h {mins}m"
                    log_embed.add_field(name="First Response", value=f"{response_time} (by <@{ticket.get('first_response_by', 'Unknown')}>)", inline=True)
                except:
                    pass
            log_embed.add_field(name="Messages", value=str(len(messages)), inline=True)
            if transcript_id:
                transcript_url = f"{config.DASHBOARD_URL}/server/{guild.id}/transcripts/{transcript_id}"
                log_embed.add_field(
                    name="Transcript",
                    value=f"[View Transcript]({transcript_url})",
                    inline=True,
                )
            if reason:
                log_embed.add_field(name="Reason", value=reason, inline=False)
            log_embed.timestamp = datetime.utcnow()

            await log_channel.send(embed=log_embed)

    # DM the ticket opener
    if not silent:
        try:
            ticket_opener = await client.fetch_user(int(ticket["user_id"]))
            if ticket_opener:
                dm_embed = discord.Embed(
                    title=f"Ticket #{ticket['ticket_number']} Closed",
                    description=f"Your ticket in **{guild.name}** has been closed.",
                    color=discord.Color.blue(),
                )
                dm_embed.add_field(name="Type", value=ticket_type_name, inline=True)
                dm_embed.add_field(
                    name="Closed by", value=user.name, inline=True
                )
                if reason:
                    dm_embed.add_field(name="Reason", value=reason, inline=False)
                if transcript_id:
                    transcript_url = f"{config.DASHBOARD_URL}/server/{guild.id}/transcripts/{transcript_id}"
                    dm_embed.add_field(
                        name="Transcript",
                        value=f"[View Transcript]({transcript_url})",
                        inline=False,
                    )
                dm_embed.set_footer(text="Thank you for contacting support!")
                await ticket_opener.send(embed=dm_embed)

                # Send feedback request
                feedback_embed = discord.Embed(
                    title="Rate Your Experience",
                    description="How would you rate the support you received? Click a button below (1-5 stars).",
                    color=discord.Color.gold(),
                )
                feedback_view = FeedbackView(
                    str(guild.id), ticket["ticket_number"], ticket["user_id"]
                )
                await ticket_opener.send(embed=feedback_embed, view=feedback_view)
        except discord.Forbidden:
            pass
        except Exception as e:
            print(f"Failed to DM ticket opener: {e}")

    # Delete ticket from database and channel
    db.delete_ticket(guild.id, channel.id)
    if is_interaction:
        await interaction.followup.send(embed=info("Closing ticket..."), ephemeral=True)
    await channel.delete()


async def claim_ticket_channel(
    interaction: discord.Interaction, view: TicketControlView = None
):
    ticket = db.get_ticket(interaction.guild.id, interaction.channel.id)
    if not ticket:
        await interaction.response.send_message(
            embed=error("This is not a ticket channel."), ephemeral=True
        )
        return

    if str(interaction.user.id) == ticket.get("user_id"):
        await interaction.response.send_message(
            embed=error("You can't claim your own ticket."), ephemeral=True
        )
        return

    if ticket.get("claimed_by"):
        await interaction.response.send_message(
            embed=warning(f"This ticket is already claimed by <@{ticket['claimed_by']}>."),
            ephemeral=True,
        )
        return

    db.update_ticket(
        interaction.guild.id,
        interaction.channel.id,
        {"claimed_by": str(interaction.user.id)},
    )

    # Update the message to remove the claim button
    if view and interaction.message:
        view.remove_item(view.claim_ticket)
        await interaction.response.edit_message(view=view)
        await interaction.followup.send(embed=success(f"Ticket claimed by {interaction.user.mention}"))
    else:
        await interaction.response.send_message(
            embed=success(f"Ticket claimed by {interaction.user.mention}")
        )


async def ticket_type_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    """Autocomplete for ticket type selection from all panels."""
    # Get all panels for this guild
    panels = db.get_all_ticket_panels(interaction.guild.id)
    if not panels:
        return []

    choices = []
    for panel in panels:
        panel_id = panel.get("id")
        panel_name = panel.get("name", "Unknown Panel")
        ticket_types = panel.get("ticket_types", [])

        for i, t in enumerate(ticket_types):
            type_name = t.get("name", f"Type {i+1}")
            # Show as "Type Name (Panel Name)" for clarity
            display_name = f"{type_name} ({panel_name})"
            # Value format: panel_id:type_index
            value = f"{panel_id}:{i}"

            if (
                current.lower() in display_name.lower()
                or current.lower() in type_name.lower()
                or not current
            ):
                choices.append(app_commands.Choice(name=display_name, value=value))
            if len(choices) >= 25:
                break
        if len(choices) >= 25:
            break
    return choices


class Tickets(commands.Cog):
    """Ticket system for support management."""

    _cd = commands.CooldownMapping.from_cooldown(1, 3, commands.BucketType.user)

    async def cog_check(self, ctx: commands.Context) -> bool:
        bucket = self._cd.get_bucket(ctx.message)
        retry_after = bucket.update_rate_limit()
        if retry_after:
            await ctx.send(embed=error(f"Cooldown. Try again in {retry_after:.1f}s."), delete_after=3)
            return False
        return True

    def __init__(self, bot):
        self.bot = bot

    @commands.hybrid_command(
        name="ticketpanel", description="Send a ticket panel (create panels in dashboard)"
    )
    @app_commands.describe(panel_id="ID of the panel to send (from dashboard)")
    async def ticketpanel(self, ctx: commands.Context, panel_id: str = None):
        if not is_module_enabled(ctx.guild.id, "tickets"):
            await ctx.send(embed=error("The tickets module is disabled on this server."))
            return
        if not has_admin_role(ctx.author):
            await ctx.send(embed=error("You don't have permission to use this command."))
            return
        if panel_id:
            # Send specific panel
            panel = db.get_ticket_panel(ctx.guild.id, panel_id)
            if not panel:
                await ctx.send(embed=error("Panel not found. Create panels in the dashboard."))
                return
        else:
            # List available panels or create default
            panels = db.get_all_ticket_panels(ctx.guild.id)
            if not panels:
                # Create a default panel
                panel_id = db.create_ticket_panel(
                    ctx.guild.id,
                    {
                        "name": "Support",
                        "title": "Support Tickets",
                        "description": "Click a button below to create a ticket.",
                        "embed_color": "#5865F2",
                        "ticket_types": [
                            {
                                "name": "General Support",
                                "emoji": "",
                                "button_style": "primary",
                            },
                        ],
                        "max_per_user": 1,
                        "name_format": "ticket-{number}",
                        "welcome_message": "Welcome {user}! Support will be with you shortly.",
                        "ping_support": True,
                    },
                )
                panel = db.get_ticket_panel(ctx.guild.id, panel_id)
                await ctx.send(embed=info("Created a default panel. Customize it in the dashboard."))
            elif len(panels) == 1:
                panel = panels[0]
            else:
                # List available panels
                embed = discord.Embed(
                    title="Available Ticket Panels", color=discord.Color.blurple()
                )
                for p in panels:
                    embed.add_field(
                        name=p.get("name", "Unnamed"),
                        value=f"ID: `{p['id']}`",
                        inline=False,
                    )
                embed.set_footer(text="Use !ticketpanel <id> to send a specific panel")
                await ctx.send(embed=embed)
                return

        # Send the panel
        embed_color = panel.get("embed_color", "#5865F2")
        try:
            color = discord.Color.from_str(embed_color)
        except:
            color = discord.Color.blurple()

        embed = discord.Embed(
            title=panel.get("title", "Support Tickets"),
            description=panel.get(
                "description", "Click a button below to create a ticket."
            ),
            color=color,
        )
        if panel.get("footer"):
            embed.set_footer(text=panel["footer"])
        else:
            embed.set_footer(text=ctx.guild.name)

        view = create_panel_view(panel)
        await ctx.send(embed=embed, view=view)

    @commands.hybrid_command(name="close", description="Close the current ticket")
    @app_commands.describe(reason="Reason for closing the ticket. Use --bypass/-b to skip confirmation.")
    async def close(self, ctx: commands.Context, *, reason: str = None):
        """Close a ticket. Flags: --bypass/-b (skip confirmation)"""
        ticket = db.get_ticket(ctx.guild.id, ctx.channel.id)
        if not ticket:
            # Check if this is a modmail channel
            settings = db.get_guild_settings(ctx.guild.id)
            mm = settings.get("modmail", {})
            if (mm.get("enabled") and mm.get("category_id")
                    and ctx.channel.category
                    and ctx.channel.category_id == int(mm["category_id"])):
                await ctx.send(embed=info("Closing modmail thread..."))
                await ctx.channel.delete()
                return
            await ctx.send(embed=error("This is not a ticket channel."))
            return
        if not is_module_enabled(ctx.guild.id, "tickets"):
            await ctx.send(embed=error("The tickets module is disabled on this server."))
            return

        # Parse flags
        bypass = False
        silent = False
        no_transcript = False
        no_log = False
        if reason:
            parts = reason.split()
            clean_parts = []
            for p in parts:
                low = p.lower()
                if low in ("--bypass", "-b", "--b"):
                    bypass = True
                elif low in ("--silent", "-s"):
                    silent = True
                elif low in ("--no-transcript", "-nt"):
                    no_transcript = True
                elif low in ("--no-log", "-nl"):
                    no_log = True
                else:
                    clean_parts.append(p)
            reason = " ".join(clean_parts) if clean_parts else None

        if bypass:
            await delete_ticket_channel(ctx, reason, silent=silent, no_transcript=no_transcript, no_log=no_log)
            return

        description = "Are you sure you want to close this ticket?"
        if reason:
            description += f"\n\n**Reason:** {reason}"

        embed = discord.Embed(
            title="Close Ticket?", description=description, color=discord.Color.orange()
        )
        await ctx.send(embed=embed, view=TicketCloseConfirmView(reason))

    @commands.hybrid_command(
        name="closerequest",
        description="Request to close the ticket (requires ticket opener approval)",
    )
    @app_commands.describe(reason="Reason for requesting to close the ticket")
    async def closerequest(self, ctx: commands.Context, *, reason: str = None):
        if not is_module_enabled(ctx.guild.id, "tickets"):
            await ctx.send(embed=error("The tickets module is disabled on this server."))
            return
        ticket = db.get_ticket(ctx.guild.id, ctx.channel.id)
        if not ticket:
            await ctx.send(embed=error("This is not a ticket channel."))
            return

        ticket_opener_id = ticket.get("user_id")

        # Check if the requester is the ticket opener (they can just use /close)
        if str(ctx.author.id) == ticket_opener_id:
            await ctx.send(
                embed=info("You are the ticket opener. Use `/close` to close the ticket directly.")
            )
            return

        description = f"{ctx.author.mention} has requested to close this ticket.\n\n<@{ticket_opener_id}>, please accept or deny this request."
        if reason:
            description += f"\n\n**Reason:** {reason}"

        embed = discord.Embed(
            title="Close Request", description=description, color=discord.Color.orange()
        )
        embed.set_footer(text="Only the ticket opener can accept or deny this request.")

        await ctx.send(f"<@{ticket_opener_id}>", embed=embed, view=CloseRequestView(reason))

    @commands.hybrid_command(name="claim", description="Claim the current ticket")
    async def claim(self, ctx: commands.Context):
        if not is_module_enabled(ctx.guild.id, "tickets"):
            await ctx.send(embed=error("The tickets module is disabled on this server."))
            return
        ticket = db.get_ticket(ctx.guild.id, ctx.channel.id)
        if not ticket:
            await ctx.send(embed=error("This is not a ticket channel."))
            return

        if str(ctx.author.id) == ticket.get("user_id"):
            await ctx.send(embed=error("You can't claim your own ticket."))
            return

        if ticket.get("claimed_by"):
            await ctx.send(embed=warning(f"This ticket is already claimed by <@{ticket['claimed_by']}>."))
            return

        db.update_ticket(ctx.guild.id, ctx.channel.id, {"claimed_by": str(ctx.author.id)})

        embed = discord.Embed(
            title="Ticket Claimed",
            description=f"{ctx.author.mention} has claimed this ticket and will be assisting you.",
            color=discord.Color.green(),
        )
        await ctx.send(embed=embed)

    @commands.hybrid_command(
        name="unclaim", description="Release your claim on the current ticket"
    )
    async def unclaim(self, ctx: commands.Context):
        if not is_module_enabled(ctx.guild.id, "tickets"):
            await ctx.send(embed=error("The tickets module is disabled on this server."))
            return
        ticket = db.get_ticket(ctx.guild.id, ctx.channel.id)
        if not ticket:
            await ctx.send(embed=error("This is not a ticket channel."))
            return

        if not ticket.get("claimed_by"):
            await ctx.send(embed=info("This ticket is not claimed."))
            return

        # Allow unclaim if: user is the claimer OR has admin permissions
        if (
            ticket.get("claimed_by") != str(ctx.author.id)
            and not ctx.author.guild_permissions.administrator
        ):
            await ctx.send(
                embed=error("You can only unclaim tickets you have claimed (or be an admin).")
            )
            return

        # Remove claim
        db.update_ticket(ctx.guild.id, ctx.channel.id, {"claimed_by": None})

        embed = discord.Embed(
            title="Ticket Unclaimed",
            description=f"{ctx.author.mention} has released this ticket. It is now available for other staff.",
            color=discord.Color.orange(),
        )
        await ctx.send(embed=embed)

    @commands.hybrid_command(
        name="forceclaim",
        description="Force claim a ticket (takes over from current claimer)",
    )
    async def forceclaim(self, ctx: commands.Context):
        """Force claim a ticket - requires admin role or Manage Server permission."""
        if not is_module_enabled(ctx.guild.id, "tickets"):
            await ctx.send(embed=error("The tickets module is disabled on this server."))
            return

        ticket = db.get_ticket(ctx.guild.id, ctx.channel.id)
        if not ticket:
            await ctx.send(embed=error("This is not a ticket channel."))
            return

        # Check if user has permission (admin role or Manage Server)
        has_permission = ctx.author.guild_permissions.manage_guild or has_admin_role(
            ctx.author
        )
        if not has_permission:
            await ctx.send(
                embed=error("You need the admin role or Manage Server permission to force claim a ticket.")
            )
            return

        previous_claimer = ticket.get("claimed_by")

        # Update ticket with new claimer
        db.update_ticket(ctx.guild.id, ctx.channel.id, {"claimed_by": str(ctx.author.id)})

        if previous_claimer and previous_claimer != str(ctx.author.id):
            embed = discord.Embed(
                title="Ticket Force Claimed",
                description=f"{ctx.author.mention} has taken over this ticket from <@{previous_claimer}>.",
                color=discord.Color.orange(),
            )
        else:
            embed = discord.Embed(
                title="Ticket Claimed",
                description=f"{ctx.author.mention} has claimed this ticket and will be assisting you.",
                color=discord.Color.green(),
            )
        await ctx.send(embed=embed)

    @commands.hybrid_command(
        name="transfer", description="Transfer the ticket to another staff member"
    )
    @app_commands.describe(user="Staff member to transfer the ticket to")
    async def transfer(self, ctx: commands.Context, user: discord.Member):
        if not is_module_enabled(ctx.guild.id, "tickets"):
            await ctx.send(embed=error("The tickets module is disabled on this server."))
            return
        ticket = db.get_ticket(ctx.guild.id, ctx.channel.id)
        if not ticket:
            await ctx.send(embed=error("This is not a ticket channel."))
            return

        if user.bot:
            await ctx.send(embed=error("You cannot transfer a ticket to a bot."))
            return

        if user.id == ctx.author.id:
            await ctx.send(embed=error("You cannot transfer a ticket to yourself."))
            return

        # Update ticket with new claimer
        db.update_ticket(ctx.guild.id, ctx.channel.id, {"claimed_by": str(user.id)})

        # Make sure the new staff member has access
        await ctx.channel.set_permissions(
            user, view_channel=True, send_messages=True, attach_files=True, embed_links=True
        )

        embed = discord.Embed(
            title="Ticket Transferred",
            description=f"{ctx.author.mention} has transferred this ticket to {user.mention}.",
            color=discord.Color.blue(),
        )
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="add", description="Add a user to the ticket")
    @app_commands.describe(user="User to add to the ticket")
    async def add(self, ctx: commands.Context, user: discord.Member):
        if not is_module_enabled(ctx.guild.id, "tickets"):
            await ctx.send(embed=error("The tickets module is disabled on this server."))
            return
        ticket = db.get_ticket(ctx.guild.id, ctx.channel.id)
        if not ticket:
            await ctx.send(embed=error("This is not a ticket channel."))
            return

        await ctx.channel.set_permissions(
            user, view_channel=True, send_messages=True, attach_files=True
        )
        await ctx.send(embed=success(f"{user.mention} has been added to the ticket."))

    @commands.hybrid_command(name="addrole", description="Add a role to the ticket")
    @app_commands.describe(role="Role to add to the ticket")
    async def addrole(self, ctx: commands.Context, role: discord.Role):
        if not is_module_enabled(ctx.guild.id, "tickets"):
            await ctx.send(embed=error("The tickets module is disabled on this server."))
            return
        ticket = db.get_ticket(ctx.guild.id, ctx.channel.id)
        if not ticket:
            await ctx.send(embed=error("This is not a ticket channel."))
            return

        await ctx.channel.set_permissions(
            role, view_channel=True, send_messages=True, read_message_history=True
        )
        embed = discord.Embed(
            description=f"{role.mention} has been added to the ticket.",
            color=discord.Color.green(),
        )
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="removerole", description="Remove a role from the ticket")
    @app_commands.describe(role="Role to remove from the ticket")
    async def removerole(self, ctx: commands.Context, role: discord.Role):
        if not is_module_enabled(ctx.guild.id, "tickets"):
            await ctx.send(embed=error("The tickets module is disabled on this server."))
            return
        ticket = db.get_ticket(ctx.guild.id, ctx.channel.id)
        if not ticket:
            await ctx.send(embed=error("This is not a ticket channel."))
            return

        await ctx.channel.set_permissions(role, overwrite=None)
        embed = discord.Embed(
            description=f"{role.mention} has been removed from the ticket.",
            color=discord.Color.red(),
        )
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="remove", description="Remove a user from the ticket")
    @app_commands.describe(user="User to remove from the ticket")
    async def remove(self, ctx: commands.Context, user: discord.Member):
        if not is_module_enabled(ctx.guild.id, "tickets"):
            await ctx.send(embed=error("The tickets module is disabled on this server."))
            return
        ticket = db.get_ticket(ctx.guild.id, ctx.channel.id)
        if not ticket:
            await ctx.send(embed=error("This is not a ticket channel."))
            return

        # Don't allow removing the ticket creator
        if str(user.id) == ticket["user_id"]:
            await ctx.send(embed=error("Cannot remove the ticket creator."))
            return

        await ctx.channel.set_permissions(user, overwrite=None)
        await ctx.send(embed=success(f"{user.mention} has been removed from the ticket."))

    @commands.hybrid_command(name="rename", description="Rename the ticket channel")
    @app_commands.describe(name="New name for the ticket")
    async def rename(self, ctx: commands.Context, *, name: str):
        ticket = db.get_ticket(ctx.guild.id, ctx.channel.id)
        if not ticket:
            await ctx.send(embed=error("This is not a ticket channel."))
            return

        await ctx.channel.edit(name=name)
        await ctx.send(embed=success(f"Ticket renamed to `{name}`"))

    @commands.hybrid_command(
        name="switchtype", description="Switch this ticket to a different type"
    )
    @app_commands.describe(ticket_type="Ticket type to switch to")
    @app_commands.autocomplete(ticket_type=ticket_type_autocomplete)
    async def switchtype(self, ctx: commands.Context, ticket_type: str):
        await ctx.defer()

        ticket = db.get_ticket(ctx.guild.id, ctx.channel.id)
        if not ticket:
            await ctx.send(embed=error("This is not a ticket channel."))
            return

        if not has_mod_role(ctx.author):
            await ctx.send(embed=error("You don't have permission to switch ticket types."))
            return

        # Parse the ticket_type value (format: panel_id:type_index)
        try:
            new_panel_id, type_index_str = ticket_type.split(":")
            type_index = int(type_index_str)
        except ValueError:
            await ctx.send(embed=error("Invalid ticket type format."))
            return

        # Get the new panel
        new_panel = db.get_ticket_panel(ctx.guild.id, new_panel_id)
        if not new_panel:
            await ctx.send(embed=error("Panel not found."))
            return

        new_ticket_types = new_panel.get("ticket_types", [])
        if type_index < 0 or type_index >= len(new_ticket_types):
            await ctx.send(embed=error("Invalid ticket type."))
            return

        new_type = new_ticket_types[type_index]
        new_type_name = new_type.get("name", "Unknown")

        # Get old panel info
        old_panel_id = ticket.get("panel_id")
        old_panel = (
            db.get_ticket_panel(ctx.guild.id, old_panel_id) if old_panel_id else None
        )
        old_ticket_types = old_panel.get("ticket_types", []) if old_panel else []

        old_type = ticket.get("ticket_type")
        old_type_name = (
            old_type.get("name", "Unknown")
            if isinstance(old_type, dict)
            else (old_type if isinstance(old_type, str) else "Unknown")
        )

        # Check if switching to the same type on the same panel
        if old_panel_id == new_panel_id and old_type_name == new_type_name:
            await ctx.send(embed=warning("This ticket is already that type."))
            return

        # Find the old type's transcript channel from old panel's ticket_types
        old_transcript_channel_id = None
        for tt in old_ticket_types:
            if tt.get("name") == old_type_name:
                old_transcript_channel_id = tt.get("transcript_channel")
                break

        # Fallback to old panel's log channel if no type-specific transcript
        if not old_transcript_channel_id and old_panel:
            old_transcript_channel_id = old_panel.get("log_channel")

        # Send transcript to old type's channel before switching
        if old_transcript_channel_id:
            old_log_channel = ctx.guild.get_channel(int(old_transcript_channel_id))
            if old_log_channel:
                # Get old support roles for access control
                old_support_role_ids_for_transcript = []
                for tt in old_ticket_types:
                    if tt.get("name") == old_type_name:
                        old_support_role_ids_for_transcript = tt.get("support_roles") or []
                        if not old_support_role_ids_for_transcript and tt.get(
                            "support_role"
                        ):
                            old_support_role_ids_for_transcript = [tt.get("support_role")]
                        break
                if not old_support_role_ids_for_transcript and old_panel:
                    old_support_role_ids_for_transcript = (
                        old_panel.get("support_roles") or []
                    )
                    if not old_support_role_ids_for_transcript and old_panel.get(
                        "support_role"
                    ):
                        old_support_role_ids_for_transcript = [
                            old_panel.get("support_role")
                        ]

                # Build allowed users list
                allowed_users = set()
                allowed_users.add(ticket["user_id"])  # Ticket opener

                # Add users with old support roles
                for role_id in old_support_role_ids_for_transcript:
                    try:
                        role = ctx.guild.get_role(int(role_id))
                        if role:
                            for member in role.members:
                                allowed_users.add(str(member.id))
                    except:
                        pass

                # Collect messages for transcript
                messages = []
                async for msg in ctx.channel.history(limit=500, oldest_first=True):
                    msg_data = {
                        "author_id": str(msg.author.id),
                        "author_name": msg.author.name,
                        "author_avatar": (
                            str(msg.author.display_avatar.url)
                            if msg.author.display_avatar
                            else None
                        ),
                        "author_bot": msg.author.bot,
                        "content": msg.content,
                        "timestamp": msg.created_at.isoformat(),
                        "attachments": [
                            {"url": a.url, "filename": a.filename} for a in msg.attachments
                        ],
                        "embeds": len(msg.embeds) > 0,
                    }
                    messages.append(msg_data)
                    # Add message authors to allowed users
                    if not msg.author.bot:
                        allowed_users.add(str(msg.author.id))

                # Save transcript to Firebase
                transcript_data = {
                    "ticket_number": ticket["ticket_number"],
                    "ticket_type": old_type_name,
                    "panel_id": old_panel_id,
                    "channel_name": ctx.channel.name,
                    "user_id": ticket["user_id"],
                    "switched_by": str(ctx.author.id),
                    "switched_to": new_type_name,
                    "switched_to_panel": new_panel_id,
                    "opened_at": ticket.get("created_at"),
                    "messages": messages,
                    "message_count": len(messages),
                    "is_switch_transcript": True,
                    "allowed_users": list(allowed_users),
                }
                transcript_id = db.save_transcript(ctx.guild.id, transcript_data)
                transcript_url = f"{config.DASHBOARD_URL}/server/{ctx.guild.id}/transcripts/{transcript_id}"

                # Send transcript embed to old type's channel
                embed = discord.Embed(
                    title=f"Ticket #{ticket['ticket_number']} Type Changed",
                    description=f"Ticket switched from **{old_type_name}** to **{new_type_name}**",
                    color=discord.Color.orange(),
                )
                embed.add_field(name="Previous Type", value=old_type_name, inline=True)
                embed.add_field(name="New Type", value=new_type_name, inline=True)
                embed.add_field(name="Switched by", value=ctx.author.mention, inline=True)
                embed.add_field(
                    name="Opened by", value=f"<@{ticket['user_id']}>", inline=True
                )
                embed.add_field(name="Messages", value=str(len(messages)), inline=True)
                embed.add_field(
                    name="Transcript",
                    value=f"[View Transcript]({transcript_url})",
                    inline=True,
                )
                embed.timestamp = datetime.utcnow()

                try:
                    await old_log_channel.send(embed=embed)
                except:
                    pass

        # Update the ticket's type and panel in database
        db.update_ticket(
            str(ctx.guild.id),
            str(ctx.channel.id),
            {"ticket_type": new_type_name, "panel_id": new_panel_id},
        )

        # Prepare channel edit kwargs (combine into single API call)
        edit_kwargs = {}

        # Category
        new_category_id = new_type.get("category") or new_panel.get("category")
        if new_category_id:
            new_category = ctx.guild.get_channel(int(new_category_id))
            if new_category and ctx.channel.category_id != new_category.id:
                edit_kwargs["category"] = new_category

        # Channel name
        new_prefix = new_type.get("channel_prefix", "")
        if new_prefix:
            ticket_num = ticket.get("ticket_number")
            if ticket_num:
                edit_kwargs["name"] = f"{new_prefix}{ticket_num}"

        # Apply channel edits in one call
        if edit_kwargs:
            try:
                await ctx.channel.edit(**edit_kwargs)
            except:
                pass

        # Handle support role permissions
        # Find old type's support roles from old panel's ticket_types by name
        old_support_role_ids = []
        for tt in old_ticket_types:
            if tt.get("name") == old_type_name:
                old_support_role_ids = tt.get("support_roles") or []
                if not old_support_role_ids and tt.get("support_role"):
                    old_support_role_ids = [tt.get("support_role")]
                break
        if not old_support_role_ids and old_panel:
            old_support_role_ids = old_panel.get("support_roles") or []
            if not old_support_role_ids and old_panel.get("support_role"):
                old_support_role_ids = [old_panel.get("support_role")]

        new_support_role_ids = (
            new_type.get("support_roles") or new_panel.get("support_roles") or []
        )
        if not new_support_role_ids:
            legacy_role = new_type.get("support_role") or new_panel.get("support_role")
            if legacy_role:
                new_support_role_ids = [legacy_role]

        # Remove permissions for old roles not in new roles
        for old_role_id in old_support_role_ids:
            if old_role_id not in new_support_role_ids:
                try:
                    old_role = ctx.guild.get_role(int(old_role_id))
                    if old_role:
                        await ctx.channel.set_permissions(old_role, overwrite=None)
                except:
                    pass

        # Add permissions for new roles
        for new_role_id in new_support_role_ids:
            try:
                new_role = ctx.guild.get_role(int(new_role_id))
                if new_role:
                    await ctx.channel.set_permissions(
                        new_role, read_messages=True, send_messages=True
                    )
            except:
                pass

        await ctx.send(embed=success(f"Ticket switched from **{old_type_name}** to **{new_type_name}**."))


    @commands.hybrid_command(
        name="ticketstats", description="View ticket statistics for this server"
    )
    async def ticketstats(self, ctx: commands.Context):
        if not is_module_enabled(ctx.guild.id, "tickets"):
            await ctx.send(embed=error("The tickets module is disabled on this server."))
            return
        if not has_mod_role(ctx.author):
            await ctx.send(embed=error("You don't have permission to use this command."))
            return

        stats = db.get_ticket_stats(ctx.guild.id)
        rating_data = db.get_avg_ticket_rating(ctx.guild.id)

        embed = discord.Embed(
            title="Ticket Statistics",
            color=discord.Color.blurple(),
        )
        embed.add_field(name="Open Tickets", value=str(stats["open"]), inline=True)
        embed.add_field(name="Closed Tickets", value=str(stats["closed"]), inline=True)
        embed.add_field(name="Total Tickets", value=str(stats["total"]), inline=True)

        if rating_data["count"] > 0:
            stars = "\u2B50" * round(rating_data["avg_rating"])
            embed.add_field(
                name="Average Rating",
                value=f"{rating_data['avg_rating']}/5 {stars} ({rating_data['count']} ratings)",
                inline=False,
            )
        else:
            embed.add_field(name="Average Rating", value="No ratings yet", inline=False)

        if stats["busiest_staff"]:
            embed.add_field(
                name="Busiest Staff",
                value=f"<@{stats['busiest_staff']}> ({stats['busiest_count']} tickets claimed)",
                inline=False,
            )

        embed.timestamp = datetime.utcnow()
        await ctx.send(embed=embed)


    @commands.hybrid_command(
        name="priority", description="Set the priority of the current ticket"
    )
    @app_commands.describe(level="Priority level: low, medium, high, or urgent")
    @app_commands.choices(level=[
        app_commands.Choice(name="Low", value="low"),
        app_commands.Choice(name="Medium", value="medium"),
        app_commands.Choice(name="High", value="high"),
        app_commands.Choice(name="Urgent", value="urgent"),
    ])
    async def priority(self, ctx: commands.Context, level: str):
        if not is_module_enabled(ctx.guild.id, "tickets"):
            await ctx.send(embed=error("The tickets module is disabled on this server."))
            return
        if not has_mod_role(ctx.author):
            await ctx.send(embed=error("You don't have permission to use this command."))
            return

        ticket = db.get_ticket(ctx.guild.id, ctx.channel.id)
        if not ticket:
            await ctx.send(embed=error("This is not a ticket channel."))
            return

        level = level.lower()
        if level not in PRIORITY_CONFIG:
            await ctx.send(embed=error("Invalid priority. Choose: low, medium, high, or urgent."))
            return

        priority_info = PRIORITY_CONFIG[level]

        # Update ticket extra with priority
        db.update_ticket(ctx.guild.id, ctx.channel.id, {"priority": level})

        # Update channel name with priority emoji prefix
        current_name = ctx.channel.name
        # Remove any existing priority emoji prefix
        for p in PRIORITY_CONFIG.values():
            if current_name.startswith(p["emoji"]):
                current_name = current_name[len(p["emoji"]):].lstrip("-").lstrip()
                break
        new_name = f"{priority_info['emoji']}-{current_name}"
        try:
            await ctx.channel.edit(name=new_name)
        except:
            pass

        embed = discord.Embed(
            title="Priority Updated",
            description=f"Ticket priority has been set to **{priority_info['label']}** {priority_info['emoji']}",
            color=priority_info["color"],
        )
        embed.set_footer(text=f"Updated by {ctx.author.name}")
        embed.timestamp = datetime.utcnow()
        await ctx.send(embed=embed)


    @commands.hybrid_group(
        name="snippet", description="Manage ticket snippets (canned responses)"
    )
    async def snippet(self, ctx: commands.Context):
        if ctx.invoked_subcommand is None:
            await ctx.send(embed=info("Use `snippet create`, `snippet delete`, or `snippet list`."))

    @snippet.command(name="create", description="Create a new snippet")
    @app_commands.describe(name="Snippet name (used to trigger it)", content="Snippet content to send")
    async def snippet_create(self, ctx: commands.Context, name: str, *, content: str):
        if not has_mod_role(ctx.author):
            await ctx.send(embed=error("You don't have permission to use this command."))
            return

        name = name.lower().strip()
        if not name or not content:
            await ctx.send(embed=error("Please provide both a name and content."))
            return

        # Check if snippet already exists
        existing = db.get_snippet(ctx.guild.id, name)
        if existing:
            await ctx.send(embed=warning(f"A snippet named `{name}` already exists. Delete it first to recreate."))
            return

        db.create_snippet(ctx.guild.id, name, content, str(ctx.author.id))
        await ctx.send(embed=success(f"Snippet `{name}` created. Use it in ticket channels with your prefix + `{name}`."))

    @snippet.command(name="delete", description="Delete a snippet")
    @app_commands.describe(name="Name of the snippet to delete")
    async def snippet_delete(self, ctx: commands.Context, name: str):
        if not has_mod_role(ctx.author):
            await ctx.send(embed=error("You don't have permission to use this command."))
            return

        deleted = db.delete_snippet(ctx.guild.id, name.lower().strip())
        if deleted:
            await ctx.send(embed=success(f"Snippet `{name}` deleted."))
        else:
            await ctx.send(embed=error(f"Snippet `{name}` not found."))

    @snippet.command(name="list", description="List all snippets")
    async def snippet_list(self, ctx: commands.Context):
        if not has_mod_role(ctx.author):
            await ctx.send(embed=error("You don't have permission to use this command."))
            return

        snippets = db.get_all_snippets(ctx.guild.id)
        if not snippets:
            await ctx.send(embed=info("No snippets found. Create one with `snippet create <name> <content>`."))
            return

        embed = discord.Embed(title="Ticket Snippets", color=discord.Color.blurple())
        for s in snippets[:25]:
            content_preview = s["content"][:100] + "..." if len(s["content"]) > 100 else s["content"]
            embed.add_field(name=s["name"], value=content_preview, inline=False)

        if len(snippets) > 25:
            embed.set_footer(text=f"Showing 25 of {len(snippets)} snippets")

        await ctx.send(embed=embed)


    @commands.hybrid_command(
        name="ticketlimit", description="Set the global ticket limit per user"
    )
    @app_commands.describe(limit="Maximum number of open tickets per user (across all panels)")
    async def ticketlimit(self, ctx: commands.Context, limit: int):
        if not has_admin_role(ctx.author):
            await ctx.send(embed=error("You don't have permission to use this command."))
            return

        if limit < 1 or limit > 25:
            await ctx.send(embed=error("Ticket limit must be between 1 and 25."))
            return

        db.update_guild_settings(ctx.guild.id, {"ticket_limit": limit})
        await ctx.send(embed=success(f"Global ticket limit set to **{limit}** per user."))


    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        # Ignore bots and DMs
        if message.author.bot or not message.guild:
            return

        # --- Feature 4: First Response Timer ---
        ticket = db.get_ticket(message.guild.id, message.channel.id)
        if ticket:
            # Check if this is a staff message (not the ticket opener) and no first response recorded yet
            if (
                str(message.author.id) != ticket.get("user_id")
                and not ticket.get("first_response_at")
            ):
                db.update_ticket(
                    message.guild.id,
                    message.channel.id,
                    {
                        "first_response_at": datetime.utcnow().isoformat(),
                        "first_response_by": str(message.author.id),
                    },
                )

        # --- Feature 5: Snippet usage in ticket channels ---
        if ticket:
            settings = db.get_guild_settings(message.guild.id)
            prefix = settings.get("prefix", "a!")
            if message.content.startswith(prefix):
                potential_snippet = message.content[len(prefix):].strip().lower()
                # Only check single-word snippet names (no spaces)
                if potential_snippet and " " not in potential_snippet:
                    snippet = db.get_snippet(message.guild.id, potential_snippet)
                    if snippet:
                        embed = discord.Embed(
                            description=snippet["content"],
                            color=discord.Color.blurple(),
                        )
                        embed.set_footer(text=f"Snippet: {snippet['name']}")
                        await message.channel.send(embed=embed)


async def setup(bot):
    await bot.add_cog(Tickets(bot))
