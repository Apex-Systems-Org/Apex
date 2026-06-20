import discord
from discord.ext import commands, tasks
from discord import app_commands
from datetime import datetime, timedelta

from database import db
from helpers import has_mod_role
from helpers.embeds import success, error, warning, info
from helpers.utils import parse_duration


def _ts(date_str):
    try:
        return int(datetime.strptime(date_str, "%Y-%m-%d").timestamp())
    except:
        return int(datetime.utcnow().timestamp())


class LOARequestModal(discord.ui.Modal, title="Create LOA"):
    duration = discord.ui.TextInput(label="Duration", placeholder="e.g. 3d, 1w, 14d", max_length=10)
    reason = discord.ui.TextInput(label="Reason", placeholder="Why are you going on leave?", style=discord.TextStyle.paragraph, max_length=500)

    def __init__(self, guild_id, target_user_id):
        super().__init__()
        self.guild_id = guild_id
        self.target_user_id = target_user_id

    async def on_submit(self, interaction):
        parsed = parse_duration(self.duration.value)
        if not parsed:
            return await interaction.response.send_message(embed=error("Invalid duration."), ephemeral=True)

        delta, dur_text = parsed
        days = max(1, int(delta.total_seconds() / 86400))
        cfg = db.get_guild_settings(self.guild_id).get("loa", {})
        if days > cfg.get("max_days", 30):
            return await interaction.response.send_message(embed=error(f"Max is {cfg.get('max_days', 30)} days."), ephemeral=True)

        if db.get_user_active_loa(self.guild_id, self.target_user_id):
            return await interaction.response.send_message(embed=warning("Already has an active LOA."), ephemeral=True)

        now = datetime.utcnow()
        end_dt = now + delta
        loa_id = db.create_loa(self.guild_id, self.target_user_id, self.reason.value, now.strftime("%Y-%m-%d"), end_dt.strftime("%Y-%m-%d"))
        db.update_loa_status(self.guild_id, loa_id, "approved", str(interaction.user.id))

        role_id = cfg.get("role")
        if role_id:
            member = interaction.guild.get_member(int(self.target_user_id))
            role = interaction.guild.get_role(int(role_id))
            if member and role:
                try:
                    await member.add_roles(role, reason="LOA created by admin")
                except:
                    pass

        embed = discord.Embed(
            description=f"LOA created for <@{self.target_user_id}>\n> **Duration:** {dur_text}\n> **Ends:** <t:{int(end_dt.timestamp())}:D>",
            color=discord.Color.green(),
        )
        embed.set_footer(text="Apex")
        await interaction.response.send_message(embed=embed, ephemeral=True)


class LOAExtendModal(discord.ui.Modal, title="Extend LOA"):
    duration = discord.ui.TextInput(label="Extend by", placeholder="e.g. 3d, 1w", max_length=10)

    def __init__(self, guild_id, loa_data):
        super().__init__()
        self.guild_id = guild_id
        self.loa = loa_data

    async def on_submit(self, interaction):
        parsed = parse_duration(self.duration.value)
        if not parsed:
            return await interaction.response.send_message(embed=error("Invalid duration."), ephemeral=True)

        delta, dur_text = parsed
        try:
            old_end = datetime.strptime(self.loa["end_date"], "%Y-%m-%d")
            new_end = old_end + delta

            inner = db._inner if hasattr(db, '_inner') else db
            conn = inner._get_connection()
            cursor = conn.cursor()
            if hasattr(inner, '_return_connection'):
                cursor.execute("UPDATE loa_requests SET end_date = %s WHERE id = %s", (new_end.strftime("%Y-%m-%d"), self.loa["id"]))
                conn.commit()
                inner._return_connection(conn)
            else:
                cursor.execute("UPDATE loa_requests SET end_date = ? WHERE id = ?", (new_end.strftime("%Y-%m-%d"), self.loa["id"]))
                conn.commit()
                conn.close()

            embed = discord.Embed(
                description=f"Extended by {dur_text}.\n> **New end:** <t:{int(new_end.timestamp())}:D>",
                color=discord.Color.green(),
            )
            embed.set_footer(text="Apex")
            await interaction.response.send_message(embed=embed, ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(embed=error(str(e)), ephemeral=True)


class LOAApproveView(discord.ui.View):
    def __init__(self, loa_id, guild_id):
        super().__init__(timeout=None)
        self.loa_id = loa_id
        self.guild_id = guild_id

    def _can_manage(self, member):
        approver_id = db.get_guild_settings(member.guild.id).get("loa", {}).get("approver_role")
        if not approver_id:
            return has_mod_role(member)
        role = member.guild.get_role(int(approver_id))
        return (role and role in member.roles) or member.guild_permissions.administrator

    @discord.ui.button(label="Approve", style=discord.ButtonStyle.success, custom_id="loa_approve")
    async def approve(self, interaction, button):
        if not self._can_manage(interaction.user):
            return await interaction.response.send_message(embed=error("No permission."), ephemeral=True)
        loa = db.get_loa(self.guild_id, self.loa_id)
        if not loa or loa["status"] != "pending":
            return await interaction.response.send_message(embed=warning("Already handled."), ephemeral=True)

        db.update_loa_status(self.guild_id, self.loa_id, "approved", str(interaction.user.id))
        role_id = db.get_guild_settings(interaction.guild.id).get("loa", {}).get("role")
        if role_id:
            member = interaction.guild.get_member(int(loa["user_id"]))
            role = interaction.guild.get_role(int(role_id))
            if member and role:
                try:
                    await member.add_roles(role, reason="LOA approved")
                except:
                    pass

        embed = interaction.message.embeds[0] if interaction.message.embeds else discord.Embed()
        embed.color = discord.Color.green()
        embed.description = (embed.description or "") + f"\n\n**Approved** by {interaction.user.mention}"
        for c in self.children:
            c.disabled = True
        await interaction.message.edit(embed=embed, view=self)

        try:
            user = await interaction.client.fetch_user(int(loa["user_id"]))
            dm = discord.Embed(description=f"Your LOA in **{interaction.guild.name}** was approved.", color=discord.Color.green())
            dm.set_footer(text="Apex")
            await user.send(embed=dm)
        except:
            pass
        await interaction.response.send_message(embed=success("Approved."), ephemeral=True)

    @discord.ui.button(label="Deny", style=discord.ButtonStyle.danger, custom_id="loa_deny")
    async def deny(self, interaction, button):
        if not self._can_manage(interaction.user):
            return await interaction.response.send_message(embed=error("No permission."), ephemeral=True)
        loa = db.get_loa(self.guild_id, self.loa_id)
        if not loa or loa["status"] != "pending":
            return await interaction.response.send_message(embed=warning("Already handled."), ephemeral=True)

        db.update_loa_status(self.guild_id, self.loa_id, "denied", str(interaction.user.id))
        embed = interaction.message.embeds[0] if interaction.message.embeds else discord.Embed()
        embed.color = discord.Color.red()
        embed.description = (embed.description or "") + f"\n\n**Denied** by {interaction.user.mention}"
        for c in self.children:
            c.disabled = True
        await interaction.message.edit(embed=embed, view=self)

        try:
            user = await interaction.client.fetch_user(int(loa["user_id"]))
            dm = discord.Embed(description=f"Your LOA in **{interaction.guild.name}** was denied.", color=discord.Color.red())
            dm.set_footer(text="Apex")
            await user.send(embed=dm)
        except:
            pass
        await interaction.response.send_message(embed=success("Denied."), ephemeral=True)


class LOAAdminView(discord.ui.View):
    def __init__(self, guild_id, user_id, loa_data=None):
        super().__init__(timeout=120)
        self.guild_id = guild_id
        self.target_user_id = user_id
        self.loa = loa_data

        if not loa_data:
            self.delete_btn.disabled = True
            self.end_btn.disabled = True
            self.extend_btn.disabled = True

    @discord.ui.button(label="Create", style=discord.ButtonStyle.primary)
    async def create_btn(self, interaction, button):
        if db.get_user_active_loa(self.guild_id, self.target_user_id):
            return await interaction.response.send_message(embed=warning("Already has an active LOA."), ephemeral=True)
        await interaction.response.send_modal(LOARequestModal(self.guild_id, self.target_user_id))

    @discord.ui.button(label="List", style=discord.ButtonStyle.secondary)
    async def list_btn(self, interaction, button):
        user_loas = [l for l in db.get_all_loa(self.guild_id) if l["user_id"] == self.target_user_id]
        if not user_loas:
            return await interaction.response.send_message(embed=info("No LOA history."), ephemeral=True)

        lines = []
        for l in user_loas[:10]:
            lines.append(f"**{l['status'].title()}** — <t:{_ts(l['start_date'])}:d> → <t:{_ts(l['end_date'])}:d> — {l['reason'][:40]}")

        embed = discord.Embed(description="\n".join(lines), color=discord.Color.blurple())
        embed.set_author(name=f"LOA History ({len(user_loas)})")
        embed.set_footer(text="Apex")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(label="Delete", style=discord.ButtonStyle.danger)
    async def delete_btn(self, interaction, button):
        if not self.loa:
            return
        db.delete_loa(self.guild_id, self.loa["id"])
        role_id = db.get_guild_settings(interaction.guild.id).get("loa", {}).get("role")
        if role_id:
            member = interaction.guild.get_member(int(self.target_user_id))
            role = interaction.guild.get_role(int(role_id))
            if member and role and role in member.roles:
                try:
                    await member.remove_roles(role, reason="LOA deleted")
                except:
                    pass
        for c in self.children:
            c.disabled = True
        await interaction.message.edit(view=self)
        await interaction.response.send_message(embed=success("LOA deleted."), ephemeral=True)

    @discord.ui.button(label="End", style=discord.ButtonStyle.secondary)
    async def end_btn(self, interaction, button):
        if not self.loa:
            return
        db.update_loa_status(self.guild_id, self.loa["id"], "ended")
        role_id = db.get_guild_settings(interaction.guild.id).get("loa", {}).get("role")
        if role_id:
            member = interaction.guild.get_member(int(self.target_user_id))
            role = interaction.guild.get_role(int(role_id))
            if member and role and role in member.roles:
                try:
                    await member.remove_roles(role, reason="LOA ended")
                except:
                    pass
        for c in self.children:
            c.disabled = True
        await interaction.message.edit(view=self)
        await interaction.response.send_message(embed=success("LOA ended."), ephemeral=True)

    @discord.ui.button(label="Extend", style=discord.ButtonStyle.primary)
    async def extend_btn(self, interaction, button):
        if not self.loa:
            return
        await interaction.response.send_modal(LOAExtendModal(self.guild_id, self.loa))


class LOA(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._expire_loop.start()

    def cog_unload(self):
        self._expire_loop.cancel()

    @tasks.loop(minutes=30)
    async def _expire_loop(self):
        try:
            for loa in db.get_expired_loas():
                guild = self.bot.get_guild(int(loa["guild_id"]))
                if not guild:
                    continue
                cfg = db.get_guild_settings(guild.id).get("loa", {})
                role_id = cfg.get("role")
                if role_id:
                    member = guild.get_member(int(loa["user_id"]))
                    role = guild.get_role(int(role_id))
                    if member and role:
                        try:
                            await member.remove_roles(role, reason="LOA expired")
                        except:
                            pass
                db.update_loa_status(str(guild.id), loa["id"], "expired")
                ch_id = cfg.get("log_channel")
                if ch_id:
                    ch = guild.get_channel(int(ch_id))
                    if ch:
                        embed = discord.Embed(
                            description=f"<@{loa['user_id']}>'s LOA has expired.\n> Ended <t:{_ts(loa['end_date'])}:R>",
                            color=discord.Color.orange(),
                        )
                        embed.set_footer(text="Apex")
                        await ch.send(embed=embed)
        except Exception as e:
            print(f"LOA expire error: {e}")

    @_expire_loop.before_loop
    async def _before_expire(self):
        await self.bot.wait_until_ready()

    def _past_count(self, guild_id, user_id):
        return len([l for l in db.get_all_loa(str(guild_id)) if l["user_id"] == str(user_id)])

    @commands.hybrid_group(name="loa", description="Leave of Absence")
    async def loa(self, ctx):
        if ctx.invoked_subcommand is None:
            p = db.get_prefix(ctx.guild.id) if ctx.guild else "a!"
            await ctx.send(embed=info(
                f"`{p}loa request <duration> <reason>`\n"
                f"`{p}loa status [user]`\n"
                f"`{p}loa active`\n"
                f"`{p}loa admin @user`"
            ))

    @loa.command(name="request", description="Request a leave of absence")
    @app_commands.describe(duration="Duration (e.g. 3d, 1w)", reason="Reason")
    async def loa_request(self, ctx, duration: str, *, reason: str):
        cfg = db.get_guild_settings(ctx.guild.id).get("loa", {})
        if not cfg.get("enabled"):
            return await ctx.send(embed=error("LOA is not enabled."))

        parsed = parse_duration(duration)
        if not parsed:
            return await ctx.send(embed=error("Invalid duration. Try `3d`, `1w`, `14d`."))

        delta, dur_text = parsed
        days = max(1, int(delta.total_seconds() / 86400))
        if days > cfg.get("max_days", 30):
            return await ctx.send(embed=error(f"Max LOA is {cfg.get('max_days', 30)} days."))

        if db.get_user_active_loa(str(ctx.guild.id), str(ctx.author.id)):
            return await ctx.send(embed=warning("You already have an active LOA."))

        now = datetime.utcnow()
        end_dt = now + delta
        loa_id = db.create_loa(str(ctx.guild.id), str(ctx.author.id), reason, now.strftime("%Y-%m-%d"), end_dt.strftime("%Y-%m-%d"))
        past = max(0, self._past_count(ctx.guild.id, ctx.author.id) - 1)

        embed = discord.Embed(color=discord.Color.gold(), timestamp=now)
        embed.set_author(name=ctx.guild.name, icon_url=ctx.guild.icon.url if ctx.guild.icon else None)
        embed.description = (
            f"**Staff Information**\n"
            f"> **Staff Member:** {ctx.author.mention}\n"
            f"> **Top Role:** {ctx.author.top_role.name}\n"
            f"> **Past LOAs:** {past}\n\n"
            f"**Request Information**\n"
            f"> **Reason:** {reason}\n"
            f"> **Starts At:** <t:{int(now.timestamp())}>\n"
            f"> **Ends At:** <t:{int(end_dt.timestamp())}>"
        )
        embed.set_footer(text=loa_id[:8])

        ch_id = cfg.get("log_channel")
        if ch_id:
            ch = ctx.guild.get_channel(int(ch_id))
            if ch:
                await ch.send(embed=embed, view=LOAApproveView(loa_id, str(ctx.guild.id)))

        await ctx.send(embed=success(f"LOA submitted ({dur_text})."))

    @loa.command(name="status", description="Check LOA status")
    @app_commands.describe(user="User to check")
    async def loa_status(self, ctx, user: discord.Member = None):
        target = user or ctx.author
        loa = db.get_user_active_loa(str(ctx.guild.id), str(target.id))
        if not loa:
            return await ctx.send(embed=info(f"{target.display_name} is not on LOA."))

        status = "Pending" if loa["status"] == "pending" else "On leave"
        color = discord.Color.gold() if loa["status"] == "pending" else discord.Color.green()

        embed = discord.Embed(color=color)
        embed.set_author(name=target.display_name, icon_url=target.display_avatar.url)
        embed.description = (
            f"**{status}**\n\n"
            f"> **Period:** <t:{_ts(loa['start_date'])}:D> → <t:{_ts(loa['end_date'])}:D>\n"
            f"> **Ends:** <t:{_ts(loa['end_date'])}:R>\n"
            f"> **Reason:** {loa['reason']}"
        )
        embed.set_footer(text="Apex")
        await ctx.send(embed=embed)

    @loa.command(name="active", description="View all active LOAs")
    async def loa_active(self, ctx):
        active = [l for l in db.get_all_loa(str(ctx.guild.id)) if l["status"] in ("pending", "approved")]
        if not active:
            return await ctx.send(embed=info("No active LOAs."))

        lines = []
        for l in active[:15]:
            tag = "pending" if l["status"] == "pending" else "active"
            lines.append(f"<@{l['user_id']}> — {tag} — ends <t:{_ts(l['end_date'])}:R>")

        embed = discord.Embed(description="\n".join(lines), color=discord.Color.blurple())
        embed.set_author(name=f"Active LOAs ({len(active)})")
        embed.set_footer(text="Apex")
        await ctx.send(embed=embed)

    @loa.command(name="admin", description="Manage a user's LOA")
    @app_commands.describe(user="User to manage")
    async def loa_admin(self, ctx, user: discord.Member):
        if not has_mod_role(ctx.author):
            return await ctx.send(embed=error("No permission."))

        loa = db.get_user_active_loa(str(ctx.guild.id), str(user.id))
        past = self._past_count(ctx.guild.id, user.id)

        embed = discord.Embed(color=discord.Color.blurple())
        embed.set_author(name=ctx.guild.name, icon_url=ctx.guild.icon.url if ctx.guild.icon else None)

        desc = (
            f"**Staff Information**\n"
            f"> **Staff Member:** {user.mention}\n"
            f"> **Top Role:** {user.top_role.name}\n"
            f"> **Past LOAs:** {past}"
        )

        if loa:
            desc += (
                f"\n\n**Current LOA**\n"
                f"> **Status:** {loa['status'].title()}\n"
                f"> **Reason:** {loa['reason']}\n"
                f"> **Starts At:** <t:{_ts(loa['start_date'])}>\n"
                f"> **Ends At:** <t:{_ts(loa['end_date'])}>"
            )

        embed.description = desc
        await ctx.send(embed=embed, view=LOAAdminView(str(ctx.guild.id), str(user.id), loa))


async def setup(bot):
    await bot.add_cog(LOA(bot))
