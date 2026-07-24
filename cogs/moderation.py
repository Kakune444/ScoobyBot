import datetime
import json
import os

import discord
from discord.ext import commands

from cogs.scooby_quotes import scooby_quote

WARNINGS_PATH = os.path.join("data", "warnings.json")

MUTE_AT_WARNINGS = 3
MUTE_DURATION_MINUTES = 60
KICK_AT_WARNINGS = 5


def _load_warnings():
    if not os.path.exists(WARNINGS_PATH):
        return {}
    with open(WARNINGS_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_warnings(data):
    os.makedirs(os.path.dirname(WARNINGS_PATH), exist_ok=True)
    with open(WARNINGS_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


class Moderation(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="kick")
    @commands.has_permissions(kick_members=True)
    @commands.bot_has_permissions(kick_members=True)
    async def kick(self, ctx, member: discord.Member, *, raison: str = "Aucune raison fournie"):
        await member.kick(reason=raison)
        await ctx.send(f"👢 {member.mention} a été expulsé. Raison : {raison}\n💬 *{scooby_quote()}*")

    @commands.command(name="ban")
    @commands.has_permissions(ban_members=True)
    @commands.bot_has_permissions(ban_members=True)
    async def ban(self, ctx, member: discord.Member, *, raison: str = "Aucune raison fournie"):
        await member.ban(reason=raison)
        await ctx.send(f"🔨 {member.mention} a été banni. Raison : {raison}\n💬 *{scooby_quote()}*")

    @commands.command(name="unban")
    @commands.has_permissions(ban_members=True)
    @commands.bot_has_permissions(ban_members=True)
    async def unban(self, ctx, user_id: int):
        user = discord.Object(id=user_id)
        await ctx.guild.unban(user)
        await ctx.send(f"✅ Utilisateur `{user_id}` débanni.\n💬 *{scooby_quote()}*")

    @commands.command(name="mute")
    @commands.has_permissions(moderate_members=True)
    @commands.bot_has_permissions(moderate_members=True)
    async def mute(self, ctx, member: discord.Member, minutes: int = 10, *, raison: str = "Aucune raison fournie"):
        duration = datetime.timedelta(minutes=minutes)
        await member.timeout(duration, reason=raison)
        await ctx.send(f"🔇 {member.mention} mute pour {minutes} min. Raison : {raison}\n💬 *{scooby_quote()}*")

    @commands.command(name="unmute")
    @commands.has_permissions(moderate_members=True)
    @commands.bot_has_permissions(moderate_members=True)
    async def unmute(self, ctx, member: discord.Member):
        await member.timeout(None)
        await ctx.send(f"🔊 {member.mention} n'est plus mute.\n💬 *{scooby_quote()}*")

    @commands.command(name="warn")
    @commands.has_permissions(manage_messages=True)
    async def warn(self, ctx, member: discord.Member, *, raison: str = "Aucune raison fournie"):
        data = _load_warnings()
        guild_id = str(ctx.guild.id)
        member_id = str(member.id)
        data.setdefault(guild_id, {}).setdefault(member_id, [])
        data[guild_id][member_id].append({
            "raison": raison,
            "date": datetime.datetime.utcnow().isoformat(),
            "par": str(ctx.author.id),
        })
        count = len(data[guild_id][member_id])
        _save_warnings(data)

        await ctx.send(f"⚠️ {member.mention} averti ({count}). Raison : {raison}\n💬 *{scooby_quote()}*")

        if count == KICK_AT_WARNINGS:
            try:
                await member.kick(reason=f"{KICK_AT_WARNINGS} avertissements atteints")
                await ctx.send(f"👢 {member.mention} a été expulsé automatiquement ({KICK_AT_WARNINGS} avertissements).\n💬 *{scooby_quote()}*")
            except discord.Forbidden:
                await ctx.send("⚠️ Impossible d'expulser automatiquement (permissions insuffisantes).")
        elif count == MUTE_AT_WARNINGS:
            try:
                await member.timeout(datetime.timedelta(minutes=MUTE_DURATION_MINUTES), reason=f"{MUTE_AT_WARNINGS} avertissements atteints")
                await ctx.send(f"🔇 {member.mention} a été mute automatiquement {MUTE_DURATION_MINUTES} min ({MUTE_AT_WARNINGS} avertissements).\n💬 *{scooby_quote()}*")
            except discord.Forbidden:
                await ctx.send("⚠️ Impossible de mute automatiquement (permissions insuffisantes).")

    @commands.command(name="warnings")
    @commands.has_permissions(manage_messages=True)
    async def warnings_cmd(self, ctx, member: discord.Member):
        data = _load_warnings()
        entries = data.get(str(ctx.guild.id), {}).get(str(member.id), [])
        if not entries:
            await ctx.send(f"{member.mention} n'a aucun avertissement.")
            return

        lines = [f"{i + 1}. {e['raison']} — <t:{int(datetime.datetime.fromisoformat(e['date']).timestamp())}:R>" for i, e in enumerate(entries)]
        embed = discord.Embed(
            title=f"Avertissements de {member.display_name}",
            description="\n".join(lines),
            color=discord.Color.orange(),
        )
        await ctx.send(embed=embed)

    @commands.command(name="clearwarnings")
    @commands.has_permissions(manage_messages=True)
    async def clearwarnings(self, ctx, member: discord.Member):
        data = _load_warnings()
        guild_id = str(ctx.guild.id)
        member_id = str(member.id)
        if guild_id in data and member_id in data[guild_id]:
            del data[guild_id][member_id]
            _save_warnings(data)
        await ctx.send(f"✅ Avertissements de {member.mention} réinitialisés.\n💬 *{scooby_quote()}*")

    @commands.command(name="purge")
    @commands.has_permissions(manage_messages=True)
    @commands.bot_has_permissions(manage_messages=True)
    async def purge(self, ctx, nombre: int):
        nombre = max(1, min(nombre, 100))
        deleted = await ctx.channel.purge(limit=nombre + 1)
        msg = await ctx.send(f"🧹 {len(deleted) - 1} messages supprimés.\n💬 *{scooby_quote()}*")
        await msg.delete(delay=3)

    @commands.command(name="slowmode")
    @commands.has_permissions(manage_channels=True)
    @commands.bot_has_permissions(manage_channels=True)
    async def slowmode(self, ctx, secondes: int):
        secondes = max(0, min(secondes, 21600))
        await ctx.channel.edit(slowmode_delay=secondes)
        if secondes == 0:
            await ctx.send(f"✅ Slowmode désactivé.\n💬 *{scooby_quote()}*")
        else:
            await ctx.send(f"🐌 Slowmode réglé sur {secondes}s.\n💬 *{scooby_quote()}*")


async def setup(bot):
    await bot.add_cog(Moderation(bot))
