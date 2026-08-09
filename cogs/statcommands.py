from collections import Counter, defaultdict
from datetime import datetime, time, timedelta, timezone
from io import BytesIO
from typing import Optional, Union
from zoneinfo import ZoneInfo

import discord
from discord import app_commands
from discord.ext import commands
from PIL import Image

from cardkit import (
    CardData,
    collect_emojis,
    fetch_emoji_images,
    render_card,
    to_discord_file,
)
from supabase_client import (
    get_messages,
    get_voice_seconds_breakdown,
    get_voice_sessions_overlapping,
)

PARIS = ZoneInfo("Europe/Paris")
EPOCH = datetime(2015, 1, 1, tzinfo=timezone.utc)
MONTHS_FR = [
    "janvier", "février", "mars", "avril", "mai", "juin",
    "juillet", "août", "septembre", "octobre", "novembre", "décembre",
]

PERIOD_CHOICES = [
    app_commands.Choice(name="7 jours", value="7d"),
    app_commands.Choice(name="14 jours", value="14d"),
    app_commands.Choice(name="30 jours", value="30d"),
    app_commands.Choice(name="Tout", value="all"),
]
DEFAULT_PERIOD = "14d"
PERIOD_DAYS = {"7d": 7, "14d": 14, "30d": 30}
FOOTER_LABELS = {
    "7d": "7 derniers jours",
    "14d": "14 derniers jours",
    "30d": "30 derniers jours",
    "all": "Tout l'historique",
}
# Les trois sous-fenêtres affichées dans les blocs Messages / Activité vocale,
# par période (la dernière est toujours la période elle-même).
SUB_WINDOWS = {
    "7d": [("1j", 1), ("3j", 3), ("7j", 7)],
    "14d": [("1j", 1), ("7j", 7), ("14j", 14)],
    "30d": [("1j", 1), ("7j", 7), ("30j", 30)],
    "all": [("1j", 1), ("7j", 7), ("Tout", None)],
}


def _window(period: str, now: datetime) -> tuple[datetime, datetime]:
    if period == "all":
        return EPOCH, now
    return now - timedelta(days=PERIOD_DAYS[period]), now


def _format_hours(seconds: float) -> str:
    hours = seconds / 3600
    text = f"{hours:.2f}".rstrip("0").rstrip(".")
    return text or "0"


def _format_date_fr(dt: datetime) -> str:
    local = dt.astimezone(PARIS)
    return f"{local.day} {MONTHS_FR[local.month - 1]} {local.year}"


def _parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _channel_name(guild: discord.Guild, channel_id: int) -> str:
    channel = guild.get_channel(channel_id)
    return channel.name if channel else "salon-supprimé"


def _member_name(guild: discord.Guild, user_id: int) -> str:
    member = guild.get_member(user_id)
    return member.display_name if member else f"Utilisateur {user_id}"


async def _read_asset(asset: Optional[discord.Asset]) -> Optional[Image.Image]:
    if asset is None:
        return None
    try:
        data = await asset.read()
        return Image.open(BytesIO(data)).convert("RGBA")
    except Exception:
        return None


def _count_since(rows: list[dict], now: datetime, days) -> int:
    if days is None:
        return len(rows)
    cutoff = now - timedelta(days=days)
    return sum(1 for m in rows if _parse_ts(m["created_at"]) >= cutoff)


def _sum_by(rows: list[dict], key: str) -> Counter:
    totals = Counter()
    for row in rows:
        totals[row[key]] += row["seconds"]
    return totals


def _rank_label(counter: Counter, target_id) -> str:
    for i, (key, value) in enumerate(counter.most_common(), start=1):
        if key == target_id:
            return f"#{i}" if value > 0 else "#—"
    return "#—"


def _top_row(icon: str, pairs: list, name_fn, value_fn, unit: str) -> tuple:
    if not pairs or pairs[0][1] <= 0:
        return (icon, "", "", "")
    key, value = pairs[0]
    return (icon, name_fn(key), value_fn(value), unit)


def _bucket_messages(rows: list[dict]) -> dict:
    by_channel = Counter()
    by_user = Counter()
    by_day = Counter()
    for row in rows:
        local = _parse_ts(row["created_at"]).astimezone(PARIS)
        by_channel[row["channel_id"]] += 1
        by_user[row["user_id"]] += 1
        by_day[local.date()] += 1
    return {"by_channel": by_channel, "by_user": by_user, "by_day": by_day}


def _bucket_voice_daily(rows: list[dict], window_start: datetime, window_end: datetime) -> dict:
    daily = defaultdict(float)
    for row in rows:
        joined = _parse_ts(row["joined_at"])
        left = _parse_ts(row["left_at"]) if row["left_at"] else discord.utils.utcnow()
        start = max(joined, window_start)
        end = min(left, window_end)
        if start >= end:
            continue
        cursor = start.astimezone(PARIS)
        end_local = end.astimezone(PARIS)
        while cursor.date() < end_local.date():
            next_midnight = datetime.combine(cursor.date() + timedelta(days=1), time.min, tzinfo=PARIS)
            daily[cursor.date()] += (next_midnight - cursor).total_seconds()
            cursor = next_midnight
        daily[cursor.date()] += (end_local - cursor).total_seconds()
    return daily


def _daily_series(by_day: Counter, voice_daily: dict, period: str, now: datetime) -> tuple[list, list]:
    """Deux listes alignées (messages/jour, heures vocales/jour) sur la période."""
    today = now.astimezone(PARIS).date()
    if period == "all":
        all_days = set(by_day) | set(voice_daily)
        start_day = min(all_days) if all_days else today
    else:
        start_day = today - timedelta(days=PERIOD_DAYS[period] - 1)
    n = (today - start_day).days + 1
    days = [start_day + timedelta(days=i) for i in range(n)]
    messages = [by_day.get(d, 0) for d in days]
    voice = [voice_daily.get(d, 0.0) / 3600 for d in days]
    return messages, voice


class StatCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def _voice_subtotals(self, guild_id: int, now: datetime, period: str, *, user_id=None, channel_id=None):
        """[(label, rows_breakdown)] pour chaque sous-fenêtre de la période.
        La dernière sous-fenêtre est la période elle-même — ses rows servent de focus."""
        out = []
        for label, days in SUB_WINDOWS[period]:
            start = EPOCH if days is None else now - timedelta(days=days)
            rows = await get_voice_seconds_breakdown(
                guild_id=guild_id, window_start=start, window_end=now, user_id=user_id, channel_id=channel_id,
            )
            out.append((label, rows))
        return out

    async def _brand_icon(self, interaction: discord.Interaction) -> Optional[Image.Image]:
        user = interaction.client.user
        return await _read_asset(user.display_avatar) if user else None

    # -- /serverstat ----------------------------------------------------------

    @app_commands.command(name="serverstat", description="Statistiques du serveur : messages, vocal, tops, activité")
    @app_commands.describe(periode="Période d'analyse (14 jours par défaut)")
    @app_commands.choices(periode=PERIOD_CHOICES)
    @app_commands.guild_only()
    async def serverstat(self, interaction: discord.Interaction, periode: app_commands.Choice[str] = None):
        period = periode.value if periode else DEFAULT_PERIOD
        await interaction.response.defer()

        guild = interaction.guild
        now = discord.utils.utcnow()
        focus_start, _ = _window(period, now)

        all_messages = await get_messages(guild_id=guild.id, window_start=EPOCH, window_end=now)
        focus_messages = [m for m in all_messages if _parse_ts(m["created_at"]) >= focus_start]
        buckets = _bucket_messages(focus_messages)

        messages_rows = [
            (label, str(_count_since(all_messages, now, days)), "messages")
            for label, days in SUB_WINDOWS[period]
        ]
        voice_sub = await self._voice_subtotals(guild.id, now, period)
        voice_rows = [
            (label, _format_hours(sum(r["seconds"] for r in rows)), "heures")
            for label, rows in voice_sub
        ]
        voice_focus = voice_sub[-1][1]

        top_msg = buckets["by_user"].most_common(1)
        top_voc = _sum_by(voice_focus, "user_id").most_common(1)
        rank_rows = [
            ("Message", _member_name(guild, top_msg[0][0]) if top_msg else "—"),
            ("Vocale", _member_name(guild, top_voc[0][0]) if top_voc and top_voc[0][1] > 0 else "—"),
        ]

        top_rows = [
            _top_row("text", buckets["by_channel"].most_common(1),
                     lambda cid: f"#{_channel_name(guild, cid)}", str, "messages"),
            _top_row("voice", _sum_by(voice_focus, "channel_id").most_common(1),
                     lambda cid: _channel_name(guild, cid), _format_hours, "heures"),
            ("game", "", "", ""),
        ]

        voice_sessions = await get_voice_sessions_overlapping(guild_id=guild.id, window_start=focus_start, window_end=now)
        voice_daily = _bucket_voice_daily(voice_sessions, focus_start, now)
        graph_messages, graph_voice = _daily_series(buckets["by_day"], voice_daily, period, now)

        joined = guild.me.joined_at
        data = CardData(
            avatar=await _read_asset(guild.icon),
            placeholder_glyph=(guild.name[:1] or "?").upper(),
            title=guild.name,
            title_suffix="",
            subtitle=f"{guild.member_count} membres",
            badges=[
                ("Créé le", _format_date_fr(guild.created_at)),
                ("Rejoint le", _format_date_fr(joined) if joined else "—"),
            ],
            rank_title="Top membres",
            rank_rows=rank_rows,
            messages_rows=messages_rows,
            voice_rows=voice_rows,
            top_title="Top des salons et applications",
            top_rows=top_rows,
            graph_messages=graph_messages,
            graph_voice=graph_voice,
            period_label=FOOTER_LABELS[period],
            brand_icon=await self._brand_icon(interaction),
        )
        emoji_map = await fetch_emoji_images(collect_emojis(
            data.title, data.subtitle, rank_rows[0][1], rank_rows[1][1], top_rows[0][1], top_rows[1][1],
        ))
        card = render_card(data, emoji_map)
        await interaction.followup.send(file=to_discord_file(card, "serverstat.png"))

    # -- /userstat --------------------------------------------------------------

    @app_commands.command(name="userstat", description="Statistiques d'un membre : messages, vocal, classement, activité")
    @app_commands.describe(membre="Le membre à consulter (toi par défaut)", periode="Période d'analyse (14 jours par défaut)")
    @app_commands.choices(periode=PERIOD_CHOICES)
    @app_commands.guild_only()
    async def userstat(self, interaction: discord.Interaction, membre: discord.Member = None, periode: app_commands.Choice[str] = None):
        member = membre or interaction.user
        period = periode.value if periode else DEFAULT_PERIOD
        await interaction.response.defer()

        guild = interaction.guild
        now = discord.utils.utcnow()
        focus_start, _ = _window(period, now)

        all_messages = await get_messages(guild_id=guild.id, window_start=EPOCH, window_end=now)
        guild_focus_messages = [m for m in all_messages if _parse_ts(m["created_at"]) >= focus_start]
        user_messages = [m for m in all_messages if m["user_id"] == member.id]
        user_focus_messages = [m for m in guild_focus_messages if m["user_id"] == member.id]
        buckets = _bucket_messages(user_focus_messages)

        messages_rows = [
            (label, str(_count_since(user_messages, now, days)), "messages")
            for label, days in SUB_WINDOWS[period]
        ]
        voice_sub = await self._voice_subtotals(guild.id, now, period, user_id=member.id)
        voice_rows = [
            (label, _format_hours(sum(r["seconds"] for r in rows)), "heures")
            for label, rows in voice_sub
        ]
        voice_focus = voice_sub[-1][1]

        # Classement sur la période, parmi tout le serveur
        guild_voice_focus = await get_voice_seconds_breakdown(guild_id=guild.id, window_start=focus_start, window_end=now)
        rank_rows = [
            ("Message", _rank_label(Counter(m["user_id"] for m in guild_focus_messages), member.id)),
            ("Vocale", _rank_label(_sum_by(guild_voice_focus, "user_id"), member.id)),
        ]

        top_rows = [
            _top_row("text", buckets["by_channel"].most_common(1),
                     lambda cid: f"#{_channel_name(guild, cid)}", str, "messages"),
            _top_row("voice", _sum_by(voice_focus, "channel_id").most_common(1),
                     lambda cid: _channel_name(guild, cid), _format_hours, "heures"),
            ("game", "", "", ""),
        ]

        voice_sessions = await get_voice_sessions_overlapping(
            guild_id=guild.id, window_start=focus_start, window_end=now, user_id=member.id,
        )
        voice_daily = _bucket_voice_daily(voice_sessions, focus_start, now)
        graph_messages, graph_voice = _daily_series(buckets["by_day"], voice_daily, period, now)

        data = CardData(
            avatar=await _read_asset(member.display_avatar),
            placeholder_glyph=(member.display_name[:1] or "?").upper(),
            title=member.display_name,
            title_suffix=member.name,
            subtitle=guild.name,
            badges=[
                ("Créé le", _format_date_fr(member.created_at)),
                ("Rejoint le", _format_date_fr(member.joined_at) if member.joined_at else "—"),
            ],
            rank_title="Classement serveur",
            rank_rows=rank_rows,
            messages_rows=messages_rows,
            voice_rows=voice_rows,
            top_title="Top des salons et applications",
            top_rows=top_rows,
            graph_messages=graph_messages,
            graph_voice=graph_voice,
            period_label=FOOTER_LABELS[period],
            brand_icon=await self._brand_icon(interaction),
        )
        emoji_map = await fetch_emoji_images(collect_emojis(
            data.title, data.title_suffix, data.subtitle, top_rows[0][1], top_rows[1][1],
        ))
        card = render_card(data, emoji_map)
        await interaction.followup.send(file=to_discord_file(card, "userstat.png"))

    # -- /channelstat -----------------------------------------------------------

    @app_commands.command(name="channelstat", description="Statistiques d'un salon : messages, vocal, top membres, activité")
    @app_commands.describe(salon="Le salon à consulter (salon courant par défaut)", periode="Période d'analyse (14 jours par défaut)")
    @app_commands.choices(periode=PERIOD_CHOICES)
    @app_commands.guild_only()
    async def channelstat(
        self,
        interaction: discord.Interaction,
        salon: Union[discord.TextChannel, discord.VoiceChannel] = None,
        periode: app_commands.Choice[str] = None,
    ):
        channel = salon or interaction.channel
        period = periode.value if periode else DEFAULT_PERIOD
        await interaction.response.defer()

        guild = interaction.guild
        now = discord.utils.utcnow()
        focus_start, _ = _window(period, now)
        is_voice = isinstance(channel, discord.VoiceChannel)

        all_messages = await get_messages(guild_id=guild.id, window_start=EPOCH, window_end=now)
        guild_focus_messages = [m for m in all_messages if _parse_ts(m["created_at"]) >= focus_start]
        channel_messages = [m for m in all_messages if m["channel_id"] == channel.id]
        channel_focus_messages = [m for m in guild_focus_messages if m["channel_id"] == channel.id]
        buckets = _bucket_messages(channel_focus_messages)

        messages_rows = [
            (label, str(_count_since(channel_messages, now, days)), "messages")
            for label, days in SUB_WINDOWS[period]
        ]
        voice_sub = await self._voice_subtotals(guild.id, now, period, channel_id=channel.id)
        voice_rows = [
            (label, _format_hours(sum(r["seconds"] for r in rows)), "heures")
            for label, rows in voice_sub
        ]
        voice_focus = voice_sub[-1][1]

        # Classement du salon parmi les salons du serveur, sur la période
        guild_voice_focus = await get_voice_seconds_breakdown(guild_id=guild.id, window_start=focus_start, window_end=now)
        rank_rows = [
            ("Message", _rank_label(Counter(m["channel_id"] for m in guild_focus_messages), channel.id)),
            ("Vocale", _rank_label(_sum_by(guild_voice_focus, "channel_id"), channel.id)),
        ]

        top_rows = [
            _top_row("text", buckets["by_user"].most_common(1),
                     lambda uid: _member_name(guild, uid), str, "messages"),
            _top_row("voice", _sum_by(voice_focus, "user_id").most_common(1),
                     lambda uid: _member_name(guild, uid), _format_hours, "heures"),
            ("game", "", "", ""),
        ]

        voice_sessions = await get_voice_sessions_overlapping(
            guild_id=guild.id, window_start=focus_start, window_end=now, channel_id=channel.id,
        )
        voice_daily = _bucket_voice_daily(voice_sessions, focus_start, now)
        graph_messages, graph_voice = _daily_series(buckets["by_day"], voice_daily, period, now)

        created = getattr(channel, "created_at", None)
        data = CardData(
            avatar=None,
            placeholder_glyph="♪" if is_voice else "#",
            title=channel.name if is_voice else f"#{channel.name}",
            title_suffix="",
            subtitle=guild.name,
            badges=[
                ("Créé le", _format_date_fr(created) if created else "—"),
                ("Type", "Vocal" if is_voice else "Texte"),
            ],
            rank_title="Classement serveur",
            rank_rows=rank_rows,
            messages_rows=messages_rows,
            voice_rows=voice_rows,
            top_title="Top des membres",
            top_rows=top_rows,
            graph_messages=graph_messages,
            graph_voice=graph_voice,
            period_label=FOOTER_LABELS[period],
            brand_icon=await self._brand_icon(interaction),
        )
        emoji_map = await fetch_emoji_images(collect_emojis(
            data.title, data.subtitle, top_rows[0][1], top_rows[1][1],
        ))
        card = render_card(data, emoji_map)
        await interaction.followup.send(file=to_discord_file(card, "channelstat.png"))


async def setup(bot):
    await bot.add_cog(StatCommands(bot))
