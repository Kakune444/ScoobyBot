import asyncio
import datetime
import io
import json
import os
import time
from zoneinfo import ZoneInfo

import discord
from discord.ext import commands, tasks
from PIL import Image, ImageDraw, ImageFont

from cogs.scooby_quotes import scooby_quote

STATS_PATH = os.path.join("data", "stats.json")
INIT_CHANNELS_PATH = os.path.join("data", "initialized_channels.json")

TIMEZONE = ZoneInfo("Europe/Paris")
REDUCED_SAVE_WINDOW = (4, 14)  # entre 4h et 14h inclus : sauvegarde toutes les heures
DEFAULT_SAVE_INTERVAL = 5 * 60  # sinon, toutes les 5 minutes
REDUCED_SAVE_INTERVAL = 60 * 60

CARD_SIZE = (900, 280)
CARD_BG_COLOR = (23, 43, 43)
CARD_ACCENT_COLOR = (230, 126, 34)
CARD_TEXT_COLOR = (255, 255, 255)
CARD_MUTED_COLOR = (170, 200, 200)
CARD_AVATAR_SIZE = 200


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


def _circular_avatar(avatar_bytes, size):
    avatar = Image.open(io.BytesIO(avatar_bytes)).convert("RGBA").resize((size, size))
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size, size), fill=255)
    circular = Image.new("RGBA", (size, size))
    circular.paste(avatar, (0, 0), mask)
    return circular


def _circular_placeholder(letter, size):
    placeholder = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(placeholder)
    draw.ellipse((0, 0, size, size), fill=CARD_ACCENT_COLOR)
    font = ImageFont.load_default(size=int(size * 0.5))
    left, top, right, bottom = draw.textbbox((0, 0), letter, font=font)
    width, height = right - left, bottom - top
    draw.text(((size - width) / 2 - left, (size - height) / 2 - top), letter, font=font, fill=CARD_TEXT_COLOR)
    return placeholder


def _wrap_text(draw, text, font, max_width):
    words = text.split()
    lines = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if draw.textlength(candidate, font=font) <= max_width:
            current = candidate
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def _render_card(name, messages, voice_seconds, quote, avatar_bytes):
    card = Image.new("RGB", CARD_SIZE, CARD_BG_COLOR)
    draw = ImageDraw.Draw(card)
    draw.rectangle((0, 0, 14, CARD_SIZE[1]), fill=CARD_ACCENT_COLOR)

    avatar = _circular_avatar(avatar_bytes, CARD_AVATAR_SIZE)
    card.paste(avatar, (50, 40), avatar)

    text_x = 50 + CARD_AVATAR_SIZE + 30
    name_font = ImageFont.load_default(size=40)
    label_font = ImageFont.load_default(size=26)
    quote_font = ImageFont.load_default(size=22)

    draw.text((text_x, 40), name, font=name_font, fill=CARD_TEXT_COLOR)
    draw.text((text_x, 100), f"Messages : {messages}", font=label_font, fill=CARD_MUTED_COLOR)
    draw.text((text_x, 135), f"Temps vocal : {_format_duration(voice_seconds)}", font=label_font, fill=CARD_MUTED_COLOR)

    quote_lines = _wrap_text(draw, f"« {quote} »", quote_font, CARD_SIZE[0] - text_x - 40)
    y = 185
    for line in quote_lines[:3]:
        draw.text((text_x, y), line, font=quote_font, fill=CARD_TEXT_COLOR)
        y += 30

    buffer = io.BytesIO()
    card.save(buffer, format="PNG")
    buffer.seek(0)
    return buffer


def _render_server_card(name, member_count, total_messages, total_voice_seconds, created_str, icon_bytes):
    card = Image.new("RGB", CARD_SIZE, CARD_BG_COLOR)
    draw = ImageDraw.Draw(card)
    draw.rectangle((0, 0, 14, CARD_SIZE[1]), fill=CARD_ACCENT_COLOR)

    if icon_bytes is not None:
        icon = _circular_avatar(icon_bytes, CARD_AVATAR_SIZE)
    else:
        icon = _circular_placeholder((name[:1] or "?").upper(), CARD_AVATAR_SIZE)
    card.paste(icon, (50, 40), icon)

    text_x = 50 + CARD_AVATAR_SIZE + 30
    name_font = ImageFont.load_default(size=40)
    label_font = ImageFont.load_default(size=26)

    draw.text((text_x, 30), name, font=name_font, fill=CARD_TEXT_COLOR)
    draw.text((text_x, 90), f"Créé le : {created_str}", font=label_font, fill=CARD_MUTED_COLOR)
    draw.text((text_x, 125), f"Membres : {member_count}", font=label_font, fill=CARD_MUTED_COLOR)
    draw.text((text_x, 160), f"Messages suivis : {total_messages}", font=label_font, fill=CARD_MUTED_COLOR)
    draw.text((text_x, 195), f"Temps vocal total : {_format_duration(total_voice_seconds)}", font=label_font, fill=CARD_MUTED_COLOR)

    buffer = io.BytesIO()
    card.save(buffer, format="PNG")
    buffer.seek(0)
    return buffer


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

    @commands.command(name="card")
    async def card_cmd(self, ctx, member: discord.Member = None):
        member = member or ctx.author
        entry = self._entry(ctx.guild.id, member.id)
        voice_seconds = self._voice_seconds_live(ctx.guild.id, member.id, entry)
        quote = entry.get("quote") or scooby_quote()

        avatar_bytes = await member.display_avatar.with_size(256).read()
        buffer = await asyncio.to_thread(
            _render_card, member.display_name, entry["messages"], voice_seconds, quote, avatar_bytes
        )
        await ctx.send(file=discord.File(buffer, filename="card.png"))

    @commands.command(name="setquote")
    @commands.has_permissions(administrator=True)
    async def set_quote(self, ctx, member: discord.Member, *, quote: str):
        entry = self._entry(ctx.guild.id, member.id)
        entry["quote"] = quote
        _save_stats(self.data)
        await ctx.send(f"✅ Citation de {member.mention} mise à jour : « {quote} »\n💬 *{scooby_quote()}*")

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

    @commands.command(name="servercard")
    async def server_card_cmd(self, ctx):
        guild_data = self.data.get(str(ctx.guild.id), {})
        total_messages = sum(entry["messages"] for entry in guild_data.values())
        total_voice_seconds = sum(
            self._voice_seconds_live(ctx.guild.id, user_id, entry)
            for user_id, entry in guild_data.items()
        )
        created_str = ctx.guild.created_at.strftime("%d/%m/%Y")
        icon_bytes = await ctx.guild.icon.read() if ctx.guild.icon else None

        buffer = await asyncio.to_thread(
            _render_server_card,
            ctx.guild.name,
            ctx.guild.member_count,
            total_messages,
            total_voice_seconds,
            created_str,
            icon_bytes,
        )
        await ctx.send(file=discord.File(buffer, filename="server_card.png"))

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
