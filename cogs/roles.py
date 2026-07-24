import json
import os
import re

import discord
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

    @commands.command(name="rolemenu")
    @commands.has_permissions(manage_roles=True)
    async def rolemenu(self, ctx, *, contenu: str):
        match = re.match(r'^"(.+?)"\s*(.*)$', contenu, re.DOTALL)
        if not match:
            await ctx.send(
                '❌ Format invalide. Exemple :\n'
                '`!rolemenu "Choisis ton rôle" @Gamer | 🎮 | Gamer ; @Artiste | 🎨 | Artiste`'
            )
            return

        titre, roles_part = match.groups()
        segments = [s.strip() for s in roles_part.split(";") if s.strip()]
        if not segments:
            await ctx.send("❌ Aucun rôle fourni.")
            return

        roles_data = []
        for segment in segments:
            parts = [p.strip() for p in segment.split("|")]
            if len(parts) != 3:
                await ctx.send(f"❌ Segment invalide (attendu `@rôle | emoji | label`) : `{segment}`")
                return

            mention_str, emoji, label = parts
            role_match = ROLE_MENTION_RE.search(mention_str)
            if not role_match:
                await ctx.send(f"❌ Rôle introuvable dans : `{mention_str}` (mentionne le rôle avec @)")
                return

            role = ctx.guild.get_role(int(role_match.group(1)))
            if role is None:
                await ctx.send(f"❌ Rôle introuvable sur ce serveur : `{mention_str}`")
                return

            roles_data.append({"role_id": role.id, "emoji": emoji, "label": label})

        description = "\n".join(f"{r['emoji']} — <@&{r['role_id']}> ({r['label']})" for r in roles_data)
        embed = discord.Embed(title=titre, description=description, color=discord.Color.blurple())

        view = _build_view(roles_data)
        message = await ctx.send(embed=embed, view=view)

        menus = _load_menus()
        menus[str(message.id)] = {
            "guild_id": ctx.guild.id,
            "channel_id": ctx.channel.id,
            "title": titre,
            "roles": roles_data,
        }
        _save_menus(menus)

    @commands.command(name="rolemenu_delete")
    @commands.has_permissions(manage_roles=True)
    async def rolemenu_delete(self, ctx, message_id: int):
        menus = _load_menus()
        entry = menus.get(str(message_id))
        if entry is None:
            await ctx.send("❌ Aucun menu de rôles trouvé avec cet ID.")
            return

        channel = ctx.guild.get_channel(entry["channel_id"])
        if channel is not None:
            try:
                message = await channel.fetch_message(message_id)
                await message.delete()
            except discord.NotFound:
                pass

        del menus[str(message_id)]
        _save_menus(menus)
        await ctx.send(f"✅ Menu de rôles supprimé.\n💬 *{scooby_quote()}*")

    @commands.command(name="autorole")
    @commands.has_permissions(manage_roles=True)
    async def autorole_cmd(self, ctx, role: discord.Role = None):
        guild_id = str(ctx.guild.id)

        if role is None:
            self.autorole.pop(guild_id, None)
            _save_autorole(self.autorole)
            await ctx.send("✅ Rôle automatique désactivé.")
            return

        self.autorole[guild_id] = role.id
        _save_autorole(self.autorole)
        await ctx.send(f"✅ Les nouveaux membres recevront automatiquement le rôle {role.mention}.")

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
