import datetime
import json
import os

import discord
from discord import app_commands
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

    @app_commands.command(name="kick", description="Expulser un membre du serveur")
    @app_commands.describe(member="Le membre à expulser", raison="Raison de l'expulsion")
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.bot_has_permissions(kick_members=True)
    async def kick(self, interaction: discord.Interaction, member: discord.Member, raison: str = "Aucune raison fournie"):
        await member.kick(reason=raison)
        await interaction.response.send_message(f"👢 {member.mention} a été expulsé. Raison : {raison}\n💬 *{scooby_quote()}*")

    @app_commands.command(name="ban", description="Bannir un membre du serveur")
    @app_commands.describe(member="Le membre à bannir", raison="Raison du bannissement")
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.bot_has_permissions(ban_members=True)
    async def ban(self, interaction: discord.Interaction, member: discord.Member, raison: str = "Aucune raison fournie"):
        await member.ban(reason=raison)
        await interaction.response.send_message(f"🔨 {member.mention} a été banni. Raison : {raison}\n💬 *{scooby_quote()}*")

    @app_commands.command(name="unban", description="Débannir un utilisateur via son ID Discord")
    @app_commands.describe(user_id="L'ID Discord de l'utilisateur à débannir")
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.bot_has_permissions(ban_members=True)
    async def unban(self, interaction: discord.Interaction, user_id: str):
        user = discord.Object(id=int(user_id))
        await interaction.guild.unban(user)
        await interaction.response.send_message(f"✅ Utilisateur `{user_id}` débanni.\n💬 *{scooby_quote()}*")

    @app_commands.command(name="mute", description="Mute temporairement un membre (timeout)")
    @app_commands.describe(member="Le membre à mute", minutes="Durée du mute en minutes", raison="Raison du mute")
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.bot_has_permissions(moderate_members=True)
    async def mute(self, interaction: discord.Interaction, member: discord.Member, minutes: int = 10, raison: str = "Aucune raison fournie"):
        duration = datetime.timedelta(minutes=minutes)
        await member.timeout(duration, reason=raison)
        await interaction.response.send_message(f"🔇 Tiens {minutes} minutes de mute dans ta mère {member.mention} ! Raison : {raison}\n💬 *{scooby_quote()}*")

    @app_commands.command(name="unmute", description="Retirer le mute (timeout) d'un membre")
    @app_commands.describe(member="Le membre à démute")
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.bot_has_permissions(moderate_members=True)
    async def unmute(self, interaction: discord.Interaction, member: discord.Member):
        await member.timeout(None)
        await interaction.response.send_message(f"🔊 {member.mention} n'est plus mute.\n💬 *{scooby_quote()}*")

    @app_commands.command(name="warn", description="Avertir un membre (mute/kick automatique à certains seuils)")
    @app_commands.describe(member="Le membre à avertir", raison="Raison de l'avertissement")
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    async def warn(self, interaction: discord.Interaction, member: discord.Member, raison: str = "Aucune raison fournie"):
        data = _load_warnings()
        guild_id = str(interaction.guild.id)
        member_id = str(member.id)
        data.setdefault(guild_id, {}).setdefault(member_id, [])
        data[guild_id][member_id].append({
            "raison": raison,
            "date": datetime.datetime.utcnow().isoformat(),
            "par": str(interaction.user.id),
        })
        count = len(data[guild_id][member_id])
        _save_warnings(data)

        await interaction.response.send_message(f"⚠️ {member.mention} averti ({count}). Raison : {raison}\n💬 *{scooby_quote()}*")

        if count == KICK_AT_WARNINGS:
            try:
                await member.kick(reason=f"{KICK_AT_WARNINGS} avertissements atteints")
                await interaction.followup.send(f"👢 {member.mention} a été expulsé automatiquement ({KICK_AT_WARNINGS} avertissements).\n💬 *{scooby_quote()}*")
            except discord.Forbidden:
                await interaction.followup.send("⚠️ Impossible d'expulser automatiquement (permissions insuffisantes).")
        elif count == MUTE_AT_WARNINGS:
            try:
                await member.timeout(datetime.timedelta(minutes=MUTE_DURATION_MINUTES), reason=f"{MUTE_AT_WARNINGS} avertissements atteints")
                await interaction.followup.send(f"🔇 {member.mention} a été mute automatiquement {MUTE_DURATION_MINUTES} min ({MUTE_AT_WARNINGS} avertissements).\n💬 *{scooby_quote()}*")
            except discord.Forbidden:
                await interaction.followup.send("⚠️ Impossible de mute automatiquement (permissions insuffisantes).")

    @app_commands.command(name="warnings", description="Afficher les avertissements d'un membre")
    @app_commands.describe(member="Le membre dont on veut voir les avertissements")
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    async def warnings_cmd(self, interaction: discord.Interaction, member: discord.Member):
        data = _load_warnings()
        entries = data.get(str(interaction.guild.id), {}).get(str(member.id), [])
        if not entries:
            await interaction.response.send_message(f"{member.mention} n'a aucun avertissement.")
            return

        lines = [f"{i + 1}. {e['raison']} — <t:{int(datetime.datetime.fromisoformat(e['date']).timestamp())}:R>" for i, e in enumerate(entries)]
        embed = discord.Embed(
            title=f"Avertissements de {member.display_name}",
            description="\n".join(lines),
            color=discord.Color.orange(),
        )
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="clearwarnings", description="Réinitialiser les avertissements d'un membre")
    @app_commands.describe(member="Le membre dont on efface les avertissements")
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    async def clearwarnings(self, interaction: discord.Interaction, member: discord.Member):
        data = _load_warnings()
        guild_id = str(interaction.guild.id)
        member_id = str(member.id)
        if guild_id in data and member_id in data[guild_id]:
            del data[guild_id][member_id]
            _save_warnings(data)
        await interaction.response.send_message(f"✅ Avertissements de {member.mention} réinitialisés.\n💬 *{scooby_quote()}*")

    @app_commands.command(name="purge", description="Supprimer plusieurs messages d'un coup dans ce salon")
    @app_commands.describe(nombre="Nombre de messages à supprimer (1-100)")
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.bot_has_permissions(manage_messages=True)
    async def purge(self, interaction: discord.Interaction, nombre: app_commands.Range[int, 1, 100]):
        await interaction.response.defer(ephemeral=True)
        deleted = await interaction.channel.purge(limit=nombre)
        await interaction.followup.send(f"🧹 {len(deleted)} messages supprimés.\n💬 *{scooby_quote()}*", ephemeral=True)

    @app_commands.command(name="slowmode", description="Régler le slowmode du salon courant")
    @app_commands.describe(secondes="Délai du slowmode en secondes (0-21600)")
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.bot_has_permissions(manage_channels=True)
    async def slowmode(self, interaction: discord.Interaction, secondes: app_commands.Range[int, 0, 21600]):
        await interaction.channel.edit(slowmode_delay=secondes)
        if secondes == 0:
            await interaction.response.send_message(f"✅ Slowmode désactivé.\n💬 *{scooby_quote()}*")
        else:
            await interaction.response.send_message(f"🐌 Slowmode réglé sur {secondes}s.\n💬 *{scooby_quote()}*")


async def setup(bot):
    await bot.add_cog(Moderation(bot))
