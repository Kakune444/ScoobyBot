import json
import os
import re

import discord
from discord import app_commands
from discord.ext import commands

from cogs.scooby_quotes import scooby_quote

MENUS_PATH = os.path.join("data", "role_menus.json")
AUTOROLE_PATH = os.path.join("data", "autorole.json")

ROLE_MENTION_RE = re.compile(r"<@&(\d+)>")


def _load_menus():
    if not os.path.exists(MENUS_PATH):
        return {}
    with open(MENUS_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_menus(data):
    os.makedirs(os.path.dirname(MENUS_PATH), exist_ok=True)
    with open(MENUS_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def _load_autorole():
    if not os.path.exists(AUTOROLE_PATH):
        return {}
    with open(AUTOROLE_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_autorole(data):
    os.makedirs(os.path.dirname(AUTOROLE_PATH), exist_ok=True)
    with open(AUTOROLE_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def _build_view(roles):
    view = discord.ui.View(timeout=None)
    for role in roles:
        view.add_item(discord.ui.Button(
            style=discord.ButtonStyle.secondary,
            label=role["label"],
            emoji=role["emoji"] or None,
            custom_id=f"rolebtn:{role['role_id']}",
        ))
    return view


class Roles(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.autorole = _load_autorole()

    @app_commands.command(name="rolemenu", description="Créer un menu de rôles à sélection par boutons")
    @app_commands.describe(contenu='Format : "Titre du menu" @Rôle1 | emoji | Label1 ; @Rôle2 | emoji | Label2')
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_roles=True)
    async def rolemenu(self, interaction: discord.Interaction, contenu: str):
        match = re.match(r'^"(.+?)"\s*(.*)$', contenu, re.DOTALL)
        if not match:
            await interaction.response.send_message(
                '❌ Format invalide. Exemple :\n'
                '`/rolemenu contenu:"Choisis ton rôle" @Gamer | 🎮 | Gamer ; @Artiste | 🎨 | Artiste`',
                ephemeral=True,
            )
            return

        titre, roles_part = match.groups()
        segments = [s.strip() for s in roles_part.split(";") if s.strip()]
        if not segments:
            await interaction.response.send_message("❌ Aucun rôle fourni.", ephemeral=True)
            return

        roles_data = []
        for segment in segments:
            parts = [p.strip() for p in segment.split("|")]
            if len(parts) != 3:
                await interaction.response.send_message(f"❌ Segment invalide (attendu `@rôle | emoji | label`) : `{segment}`", ephemeral=True)
                return

            mention_str, emoji, label = parts
            role_match = ROLE_MENTION_RE.search(mention_str)
            if not role_match:
                await interaction.response.send_message(f"❌ Rôle introuvable dans : `{mention_str}` (mentionne le rôle avec @)", ephemeral=True)
                return

            role = interaction.guild.get_role(int(role_match.group(1)))
            if role is None:
                await interaction.response.send_message(f"❌ Rôle introuvable sur ce serveur : `{mention_str}`", ephemeral=True)
                return

            roles_data.append({"role_id": role.id, "emoji": emoji, "label": label})

        description = "\n".join(f"{r['emoji']} — <@&{r['role_id']}> ({r['label']})" for r in roles_data)
        embed = discord.Embed(title=titre, description=description, color=discord.Color.blurple())

        view = _build_view(roles_data)
        await interaction.response.send_message(embed=embed, view=view)
        message = await interaction.original_response()

        menus = _load_menus()
        menus[str(message.id)] = {
            "guild_id": interaction.guild.id,
            "channel_id": interaction.channel.id,
            "title": titre,
            "roles": roles_data,
        }
        _save_menus(menus)

    @app_commands.command(name="rolemenu_delete", description="Supprimer un menu de rôles existant")
    @app_commands.describe(message_id="L'ID du message contenant le menu de rôles")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_roles=True)
    async def rolemenu_delete(self, interaction: discord.Interaction, message_id: str):
        menus = _load_menus()
        entry = menus.get(message_id)
        if entry is None:
            await interaction.response.send_message("❌ Aucun menu de rôles trouvé avec cet ID.", ephemeral=True)
            return

        channel = interaction.guild.get_channel(entry["channel_id"])
        if channel is not None:
            try:
                message = await channel.fetch_message(int(message_id))
                await message.delete()
            except discord.NotFound:
                pass

        del menus[message_id]
        _save_menus(menus)
        await interaction.response.send_message(f"✅ Menu de rôles supprimé.\n💬 *{scooby_quote()}*")

    @app_commands.command(name="autorole", description="Définir (ou désactiver) le rôle donné automatiquement aux nouveaux membres")
    @app_commands.describe(role="Le rôle à attribuer automatiquement (laisser vide pour désactiver)")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_roles=True)
    async def autorole_cmd(self, interaction: discord.Interaction, role: discord.Role = None):
        guild_id = str(interaction.guild.id)

        if role is None:
            self.autorole.pop(guild_id, None)
            _save_autorole(self.autorole)
            await interaction.response.send_message("✅ Rôle automatique désactivé.")
            return

        self.autorole[guild_id] = role.id
        _save_autorole(self.autorole)
        await interaction.response.send_message(f"✅ Les nouveaux membres recevront automatiquement le rôle {role.mention}.")

    @commands.Cog.listener()
    async def on_member_join(self, member):
        role_id = self.autorole.get(str(member.guild.id))
        if role_id is None:
            return

        role = member.guild.get_role(role_id)
        if role is None:
            return

        try:
            await member.add_roles(role, reason="Rôle automatique à l'arrivée")
        except discord.Forbidden:
            pass

    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction):
        if interaction.type != discord.InteractionType.component:
            return

        custom_id = interaction.data.get("custom_id", "")
        if not custom_id.startswith("rolebtn:"):
            return

        role_id = int(custom_id.split(":", 1)[1])
        role = interaction.guild.get_role(role_id)
        if role is None:
            await interaction.response.send_message("❌ Ce rôle n'existe plus.", ephemeral=True)
            return

        member = interaction.user
        if role in member.roles:
            await member.remove_roles(role)
            await interaction.response.send_message(f"➖ Rôle **{role.name}** retiré.\n💬 *{scooby_quote()}*", ephemeral=True)
        else:
            await member.add_roles(role)
            await interaction.response.send_message(f"➕ Rôle **{role.name}** ajouté.\n💬 *{scooby_quote()}*", ephemeral=True)


async def setup(bot):
    await bot.add_cog(Roles(bot))
