import discord
from discord.ext import commands
from datetime import datetime, timezone

from database import db
from helpers.embeds import success, error, info
from helpers.utils import is_module_enabled


class RolePersistence(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        """Save roles when a member leaves."""
        if not is_module_enabled(member.guild.id, "role_persistence"):
            return

        # Save all roles except @everyone and managed roles (bot roles)
        role_ids = [str(r.id) for r in member.roles if r != member.guild.default_role and not r.managed]
        if not role_ids:
            return

        settings = db.get_guild_settings(member.guild.id)
        saved_roles = settings.get("saved_roles", {})
        saved_roles[str(member.id)] = {
            "roles": role_ids,
            "left_at": datetime.now(timezone.utc).isoformat(),
        }
        db.update_guild_settings(member.guild.id, {"saved_roles": saved_roles})

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        """Restore roles when a member rejoins."""
        if not is_module_enabled(member.guild.id, "role_persistence"):
            return

        settings = db.get_guild_settings(member.guild.id)
        saved_roles = settings.get("saved_roles", {})
        user_data = saved_roles.get(str(member.id))

        if not user_data:
            return

        roles_to_add = []
        for role_id in user_data["roles"]:
            role = member.guild.get_role(int(role_id))
            if role and not role.managed and role != member.guild.default_role:
                roles_to_add.append(role)

        if roles_to_add:
            try:
                await member.add_roles(*roles_to_add, reason="Role persistence: restoring roles")
            except discord.Forbidden:
                pass
            except Exception as e:
                print(f"Role persistence error: {e}")

        # Clean up saved data
        del saved_roles[str(member.id)]
        db.update_guild_settings(member.guild.id, {"saved_roles": saved_roles})


async def setup(bot):
    await bot.add_cog(RolePersistence(bot))
