import datetime
import json
import os
import time
from zoneinfo import ZoneInfo

import discord
from discord.ext import commands, tasks

from cogs.scooby_quotes import scooby_quote

STATS_PATH = os.path.join("data", "stats.json")
INIT_CHANNELS_PATH = os.path.join("data", "initialized_channels.json")

TIMEZONE = ZoneInfo("Europe/Paris")
REDUCED_SAVE_WINDOW = (4, 14)  # entre 4h et 14h inclus : sauvegarde toutes les heures
DEFAULT_SAVE_INTERVAL = 5 * 60  # sinon, toutes les 5 minutes
REDUCED_SAVE_INTERVAL = 60 * 60


def _load_stats():
    if not os.path.exists(STATS_PATH):
        return {}
    with open(STATS_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_stats(data):
    os.makedirs(os.path.dirname(STATS_PATH), exist_ok=True)
    with open(STATS_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def _load_initialized_channels():
    if not os.path.exists(INIT_CHANNELS_PATH):
        return {}
    with open(INIT_CHANNELS_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_initialized_channels(data):
    os.makedirs(os.path.dirname(INIT_CHANNELS_PATH), exist_ok=True)
    with open(INIT_CHANNELS_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def _format_duration(seconds):
    seconds = int(seconds)
    hours, remainder = divmod(seconds, 3600)
    minutes = remainder // 60
    if hours:
        return f"{hours}h{minutes:02d}"
    return f"{minutes}min"


class Stats(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.data = _load_stats()
        self.initialized_channels = _load_initialized_channels()
        self.voice_sessions = {}
        self.initializing_guilds = set()
        self._last_save = time.time()
        self.autosave.start()

    def cog_unload(self):
        self.autosave.cancel()
        _save_stats(self.data)

    def _entry(self, guild_id, user_id):
        guild_data = self.data.setdefault(str(guild_id), {})
        return guild_data.setdefault(str(user_id), {"messages": 0, "voice_seconds": 0})

    def _voice_seconds_live(self, guild_id, user_id, entry):
        total = entry["voice_seconds"]
        key = (guild_id, int(user_id))
        if key in self.voice_sessions:
            total += time.time() - self.voice_sessions[key]
        return total

    def add_voc_time(self, membre, salon, temps_ajouter):
        guild_id = membre.guild.id
        entry = self._entry(guild_id, membre.id)
        entry["voice_seconds"] += temps_ajouter

        channel_key = str(salon.id)
        voice_channels = entry.setdefault("voice_channels", {})
        voice_channels[channel_key] = voice_channels.get(channel_key, 0) + temps_ajouter

        guild_data = self.data.get(str(guild_id), {})
        total_serveur = sum(e["voice_seconds"] for e in guild_data.values())
        total_salon = sum(e.get("voice_channels", {}).get(channel_key, 0) for e in guild_data.values())
        total_membre = entry["voice_seconds"]
        total_membre_salon = voice_channels[channel_key]

        return total_serveur, total_salon, total_membre, total_membre_salon

    @tasks.loop(minutes=5)
    async def autosave(self):
        hour = datetime.datetime.now(TIMEZONE).hour
        window_start, window_end = REDUCED_SAVE_WINDOW
        interval = REDUCED_SAVE_INTERVAL if window_start <= hour <= window_end else DEFAULT_SAVE_INTERVAL

        now = time.time()
        if now - self._last_save >= interval:
            _save_stats(self.data)
            self._last_save = now

    @autosave.before_loop
    async def before_autosave(self):
        await self.bot.wait_until_ready()

    @commands.Cog.listener()
    async def on_ready(self):
        now = time.time()
        for guild in self.bot.guilds:
            for channel in guild.voice_channels:
                for member in channel.members:
                    if member.bot:
                        continue
                    self.voice_sessions.setdefault((guild.id, member.id), now)

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot or message.guild is None:
            return
        entry = self._entry(message.guild.id, message.author.id)
        entry["messages"] += 1

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        if member.bot or before.channel == after.channel:
            return

        key = (member.guild.id, member.id)
        now = time.time()

        if before.channel is not None:
            start = self.voice_sessions.pop(key, None)
            if start is not None:
                self.add_voc_time(member, before.channel, now - start)

        if after.channel is not None:
            self.voice_sessions[key] = now

    @commands.command(name="stats")
    async def stats_cmd(self, ctx, member: discord.Member = None):
        member = member or ctx.author
        entry = self._entry(ctx.guild.id, member.id)

        embed = discord.Embed(
            title=f"📊 Statistiques de {member.display_name}",
            color=discord.Color.teal(),
        )
        embed.add_field(name="💬 Messages", value=str(entry["messages"]), inline=True)
        embed.add_field(
            name="🎙️ Temps en vocal",
            value=_format_duration(self._voice_seconds_live(ctx.guild.id, member.id, entry)),
            inline=True,
        )
        await ctx.send(embed=embed)

    @commands.command(name="topmessages")
    async def top_messages(self, ctx):
        guild_data = self.data.get(str(ctx.guild.id), {})
        ranking = sorted(guild_data.items(), key=lambda kv: kv[1]["messages"], reverse=True)[:10]
        if not ranking:
            await ctx.send("Aucune donnée pour l'instant.")
            return

        lines = []
        for i, (user_id, entry) in enumerate(ranking, start=1):
            member = ctx.guild.get_member(int(user_id))
            name = member.display_name if member else f"Utilisateur {user_id}"
            lines.append(f"{i}. **{name}** — {entry['messages']} messages")

        embed = discord.Embed(title="🏆 Classement des messages", description="\n".join(lines), color=discord.Color.teal())
        await ctx.send(embed=embed)

    @commands.command(name="topvoice")
    async def top_voice(self, ctx):
        guild_data = self.data.get(str(ctx.guild.id), {})
        ranking = sorted(
            guild_data.items(),
            key=lambda kv: self._voice_seconds_live(ctx.guild.id, kv[0], kv[1]),
            reverse=True,
        )[:10]
        if not ranking:
            await ctx.send("Aucune donnée pour l'instant.")
            return

        lines = []
        for i, (user_id, entry) in enumerate(ranking, start=1):
            member = ctx.guild.get_member(int(user_id))
            name = member.display_name if member else f"Utilisateur {user_id}"
            duration = _format_duration(self._voice_seconds_live(ctx.guild.id, user_id, entry))
            lines.append(f"{i}. **{name}** — {duration}")

        embed = discord.Embed(title="🏆 Classement vocal", description="\n".join(lines), color=discord.Color.teal())
        await ctx.send(embed=embed)

    @commands.command(name="serverstats")
    async def server_stats(self, ctx):
        guild_data = self.data.get(str(ctx.guild.id), {})
        total_messages = sum(entry["messages"] for entry in guild_data.values())
        total_voice_seconds = sum(
            self._voice_seconds_live(ctx.guild.id, user_id, entry)
            for user_id, entry in guild_data.items()
        )
        created_ts = int(ctx.guild.created_at.timestamp())

        embed = discord.Embed(title=f"📊 Statistiques de {ctx.guild.name}", color=discord.Color.teal())
        embed.add_field(name="📅 Créé le", value=f"<t:{created_ts}:D> (<t:{created_ts}:R>)", inline=False)
        embed.add_field(name="👥 Membres", value=str(ctx.guild.member_count), inline=True)
        embed.add_field(name="💬 Messages suivis", value=str(total_messages), inline=True)
        embed.add_field(name="🎙️ Temps vocal total", value=_format_duration(total_voice_seconds), inline=True)
        await ctx.send(embed=embed)

    @commands.command(name="initialize")
    @commands.has_permissions(administrator=True)
    @commands.guild_only()
    async def initialize(self, ctx, channel: discord.TextChannel = None):
        if ctx.guild.id in self.initializing_guilds:
            await ctx.send("⏳ Un calcul est déjà en cours sur ce serveur.")
            return

        guild_key = str(ctx.guild.id)
        already_done = set(self.initialized_channels.get(guild_key, []))
        if channel is not None and str(channel.id) in already_done:
            await ctx.send(
                f"❌ Le salon {channel.mention} a déjà été comptabilisé par `!initialize`. "
                "Relancer la commande créerait un doublon."
            )
            return

        self.initializing_guilds.add(ctx.guild.id)
        cutoff = discord.utils.utcnow()
        channels = [channel] if channel is not None else ctx.guild.text_channels
        scope_label = f"le salon {channel.mention}" if channel is not None else f"{len(channels)} salon(s)"
        status = await ctx.send(
            f"🔎 Calcul de tous les messages envoyés depuis la création du serveur sur "
            f"{scope_label}... Ça peut prendre plusieurs minutes, merci de patienter."
        )

        try:
            counts = {}
            scanned_ids = []
            channels_done = 0

            for ch in channels:
                perms = ch.permissions_for(ctx.guild.me)
                if perms.view_channel and perms.read_message_history:
                    try:
                        async for message in ch.history(limit=None, before=cutoff, oldest_first=True):
                            if not message.author.bot:
                                counts[message.author.id] = counts.get(message.author.id, 0) + 1
                        scanned_ids.append(str(ch.id))
                    except (discord.Forbidden, discord.HTTPException):
                        pass

                channels_done += 1
                if channel is None and (channels_done % 3 == 0 or channels_done == len(channels)):
                    await status.edit(content=f"🔎 Analyse en cours... {channels_done}/{len(channels)} salons traités.")

            if channel is not None and str(channel.id) not in scanned_ids:
                await status.edit(content=f"❌ Impossible de lire l'historique de {channel.mention} (permissions insuffisantes).")
                return

            if channel is None:
                # Recalcul complet : on repart de zéro pour éviter le double comptage sur tous les salons.
                for entry in self.data.get(guild_key, {}).values():
                    entry["messages"] = 0
                for user_id, count in counts.items():
                    self._entry(ctx.guild.id, user_id)["messages"] = count
                self.initialized_channels[guild_key] = scanned_ids
            else:
                # Un seul salon : on ajoute au total existant sans toucher aux autres salons.
                for user_id, count in counts.items():
                    self._entry(ctx.guild.id, user_id)["messages"] += count
                self.initialized_channels.setdefault(guild_key, [])
                self.initialized_channels[guild_key].append(str(channel.id))

            _save_stats(self.data)
            _save_initialized_channels(self.initialized_channels)

            await status.edit(
                content=(
                    f"✅ Calcul terminé : {sum(counts.values())} messages comptabilisés sur "
                    f"{scope_label}.\n💬 *{scooby_quote()}*"
                )
            )
        finally:
            self.initializing_guilds.discard(ctx.guild.id)


async def setup(bot):
    await bot.add_cog(Stats(bot))
