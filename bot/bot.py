import discord
from discord.ext import commands, tasks
from datetime import datetime, timedelta, timezone
import asyncio
import os
import threading
from config import config
from database import db
from api import run_api, set_bot_reference
from helpers.utils import cache_guild_invites

os.environ["JISHAKU_NO_UNDERSCORE"] = "True"
os.environ["JISHAKU_NO_DM_TRACEBACK"] = "True"

def get_prefix(bot, message):
    if not message.guild:
        return "a!"
    from helpers.cache import prefix_cache
    key = str(message.guild.id)
    cached = prefix_cache.get(key)
    if cached is not None:
        return cached
    prefix = db.get_prefix(message.guild.id)
    prefix_cache.set(key, prefix)
    return prefix


intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.moderation = True

bot = commands.Bot(
    command_prefix=get_prefix,
    intents=intents,
    help_command=None,
    owner_ids={1329197305538150442, 776208075009818636},
)

bot.bot_start_time = None
bot.commands_run = 0

EXTENSIONS = [
    "cogs.logging",
    "cogs.events",
    "cogs.moderation",
    "cogs.tickets",
    "cogs.utility",
    "cogs.leveling",
    "cogs.giveaways",
    "cogs.afk",
    "cogs.invites",
    "cogs.voice",
    "cogs.developer",
    "cogs.staff",
    "cogs.reaction_roles",
    "cogs.suggestions",
    "cogs.autoresponder",
    "cogs.modmail",
    "cogs.role_persistence",
    "cogs.starboard",
    "cogs.loa",
    "cogs.error_handler",
    "cogs.sticky",
]


# Override default on_message to prevent double process_commands.
# The Events cog listener handles process_commands itself.
@bot.event
async def on_message(message):
    pass


async def _setup_hook():
    """Load extensions before the bot connects. Runs once."""
    import traceback
    for ext in EXTENSIONS:
        try:
            await bot.load_extension(ext)
            print(f"Loaded {ext}", flush=True)
        except Exception as e:
            print(f"Failed to load {ext}: {e}", flush=True)
            traceback.print_exc()

    try:
        await bot.load_extension("jishaku")
        print("Jishaku loaded", flush=True)
    except Exception as e:
        print(f"Failed to load Jishaku: {e}", flush=True)

bot.setup_hook = _setup_hook


@bot.event
async def setup_hook():
    """Load extensions before the bot connects. Runs once."""
    for ext in EXTENSIONS:
        try:
            await bot.load_extension(ext)
            print(f"Loaded {ext}", flush=True)
        except Exception as e:
            print(f"Failed to load {ext}: {e}", flush=True)

    try:
        await bot.load_extension("jishaku")
        print("Jishaku loaded", flush=True)
    except Exception as e:
        print(f"Failed to load Jishaku: {e}", flush=True)


@bot.event
async def on_ready():
    bot.bot_start_time = datetime.utcnow()

    # Set bot reference for API
    set_bot_reference(bot)

    # Register persistent views
    from cogs.tickets import TicketControlView, CloseRequestView, create_panel_view
    from cogs.suggestions import SuggestionView
    from cogs.reaction_roles import create_reaction_role_view
    from cogs.giveaways import GiveawayView

    bot.add_view(TicketControlView())
    bot.add_view(CloseRequestView())
    bot.add_view(SuggestionView())

    for guild in bot.guilds:
        # Ticket panel views
        panels = db.get_all_ticket_panels(guild.id)
        for panel in panels:
            view = create_panel_view(panel)
            bot.add_view(view)

        # Reaction role panel views
        rr_panels = db.get_all_reaction_role_panels(guild.id)
        for panel in rr_panels:
            view = create_reaction_role_view(panel)
            if view:
                bot.add_view(view)

    # Sync bot guilds
    db.sync_bot_guilds(bot.guilds)

    # Update bot status
    total_users = sum(g.member_count or 0 for g in bot.guilds)
    db.update_bot_status(
        {
            "online": True,
            "started_at": bot.bot_start_time.isoformat(),
            "last_heartbeat": datetime.utcnow().isoformat(),
            "total_users": total_users,
            "total_servers": len(bot.guilds),
            "commands_run": bot.commands_run,
        }
    )

    # Start background tasks
    if not heartbeat_task.is_running():
        heartbeat_task.start()
    if not command_execution_task.is_running():
        command_execution_task.start()
    if not server_health_task.is_running():
        server_health_task.start()
    if not reminder_task.is_running():
        reminder_task.start()

    # Register persistent giveaway views
    active_giveaways = db.get_active_giveaways()
    for giveaway in active_giveaways:
        bot.add_view(GiveawayView(giveaway["id"]))

    # Cache invites for all guilds
    for guild in bot.guilds:
        await cache_guild_invites(guild)

    # Cleanup stale temp voice channels
    temp_channels = db.get_all_temp_voice_channels()
    for temp_ch in temp_channels:
        guild = bot.get_guild(int(temp_ch["guild_id"]))
        if guild:
            channel = guild.get_channel(int(temp_ch["channel_id"]))
            if not channel:
                db.delete_temp_voice_channel(temp_ch["channel_id"])
            elif len(channel.members) == 0:
                try:
                    await channel.delete(
                        reason="Cleanup: Empty temp voice channel on startup"
                    )
                    db.delete_temp_voice_channel(temp_ch["channel_id"])
                except:
                    pass
        else:
            db.delete_temp_voice_channel(temp_ch["channel_id"])

    label = "DEV" if config.IS_DEV else "PROD"
    print(f"Apex [{label}] logged in as {bot.user}")

    await bot.change_presence(activity=discord.Activity(type=discord.ActivityType.watching, name="apexconsole.net"))
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} commands")
    except Exception as e:
        print(f"Failed to sync commands: {e}")


@bot.event
async def on_command_completion(ctx: commands.Context):
    bot.commands_run += 1
    if bot.commands_run % 10 == 0:
        total_users = sum(g.member_count or 0 for g in bot.guilds)
        db.update_bot_status(
            {
                "last_heartbeat": datetime.utcnow().isoformat(),
                "total_users": total_users,
                "total_servers": len(bot.guilds),
                "commands_run": bot.commands_run,
            }
        )


@tasks.loop(seconds=60)
async def heartbeat_task():
    if bot.is_ready():
        total_users = sum(g.member_count or 0 for g in bot.guilds)
        db.update_bot_status(
            {
                "online": True,
                "last_heartbeat": datetime.utcnow().isoformat(),
                "total_users": total_users,
                "total_servers": len(bot.guilds),
                "commands_run": bot.commands_run,
            }
        )


@heartbeat_task.before_loop
async def before_heartbeat():
    await bot.wait_until_ready()


@tasks.loop(seconds=30)
async def reminder_task():
    if not bot.is_ready():
        return
    try:
        due = db.get_due_reminders()
        for r in due:
            try:
                user = await bot.fetch_user(int(r["user_id"]))
                embed = discord.Embed(
                    title="Reminder",
                    description=r["message"],
                    color=discord.Color.blue(),
                )
                embed.set_footer(text="Apex")
                await user.send(embed=embed)
            except:
                pass
            db.delete_reminder(r["id"])
    except Exception as e:
        print(f"Reminder task error: {e}")


@reminder_task.before_loop
async def before_reminder():
    await bot.wait_until_ready()


@tasks.loop(seconds=5)
async def command_execution_task():
    pass  # Disabled - Firebase removed


@command_execution_task.before_loop
async def before_command_execution():
    await bot.wait_until_ready()


@tasks.loop(minutes=5)
async def server_health_task():
    if not bot.is_ready():
        return

    try:
        for guild in bot.guilds:
            issues = []

            bot_member = guild.me
            perms = bot_member.guild_permissions

            if not perms.manage_roles:
                issues.append("Missing 'Manage Roles' permission")
            if not perms.kick_members:
                issues.append("Missing 'Kick Members' permission")
            if not perms.ban_members:
                issues.append("Missing 'Ban Members' permission")
            if not perms.manage_channels:
                issues.append("Missing 'Manage Channels' permission")
            if not perms.manage_messages:
                issues.append("Missing 'Manage Messages' permission")
            if not perms.moderate_members:
                issues.append("Missing 'Timeout Members' permission")

            if bot_member.top_role.position <= 1:
                issues.append("Bot role is too low in hierarchy")

            settings = db.get_guild_settings(guild.id)

            mod_log_id = settings.get("mod_log_channel")
            if mod_log_id:
                channel = guild.get_channel(int(mod_log_id))
                if not channel:
                    issues.append("Mod log channel not found (deleted?)")
                elif not channel.permissions_for(bot_member).send_messages:
                    issues.append("Cannot send messages in mod log channel")

            mute_role_id = settings.get("mute_role")
            if mute_role_id:
                role = guild.get_role(int(mute_role_id))
                if not role:
                    issues.append("Mute role not found (deleted?)")
                elif role.position >= bot_member.top_role.position:
                    issues.append("Mute role is higher than bot's role")

            panels = db.get_all_ticket_panels(guild.id)
            for panel in panels:
                cat_id = panel.get("category")
                if cat_id:
                    category = guild.get_channel(int(cat_id))
                    if not category:
                        issues.append(
                            f"Ticket panel '{panel.get('name')}' category not found"
                        )

    except Exception as e:
        print(f"Error in server health task: {e}")


@server_health_task.before_loop
async def before_server_health():
    await bot.wait_until_ready()


if __name__ == "__main__":
    # Start API server in a separate thread
    api_port = int(os.getenv("BOT_API_PORT", 5050))
    api_thread = threading.Thread(target=run_api, args=(api_port,), daemon=True)
    api_thread.start()
    print(f"API server started on port {api_port}")

    # Run the bot
    bot.run(config.DISCORD_TOKEN)
