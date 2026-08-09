from collections import Counter, defaultdict
from datetime import datetime, time, timedelta, timezone
from io import BytesIO
from typing import Optional, Union
from zoneinfo import ZoneInfo

import discord
import emoji
from discord import app_commands
from discord.ext import commands
from PIL import Image

from cardkit import (
    CONTENT_WIDTH,
    GRIDLINE_HEX,
    INK_PRIMARY_HEX,
    INK_SECONDARY_HEX,
    SERIES_MESSAGES_HEX,
    SERIES_VOICE_HEX,
    StatTile,
    compose_card,
    hrow,
    render_chip_row,
    render_empty_panel,
    render_header,
    render_ranking_card,
    render_stat_row,
    split_width,
    to_discord_file,
    wrap_chart,
)
from supabase_client import (
    get_distinct_message_days,
    get_emoji_events,
    get_messages,
    get_voice_seconds_breakdown,
    get_voice_sessions_overlapping,
)

PARIS = ZoneInfo("Europe/Paris")
EPOCH = datetime(2015, 1, 1, tzinfo=timezone.utc)
WEEKDAY_NAMES = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"]
MONTHS_FR = [
    "janvier", "février", "mars", "avril", "mai", "juin",
    "juillet", "août", "septembre", "octobre", "novembre", "décembre",
]

PERIOD_CHOICES = [
    app_commands.Choice(name="7 jours", value="7d"),
    app_commands.Choice(name="30 jours", value="30d"),
    app_commands.Choice(name="Tout", value="all"),
]
PERIOD_LABELS = {"7d": "7 jours", "30d": "30 jours", "all": "Tout"}


def _window(period: str) -> tuple[datetime, datetime]:
    end = discord.utils.utcnow()
    if period == "7d":
        return end - timedelta(days=7), end
    if period == "30d":
        return end - timedelta(days=30), end
    return EPOCH, end


def _format_duration(seconds: float) -> str:
    seconds = int(seconds)
    hours, remainder = divmod(seconds, 3600)
    minutes = remainder // 60
    if hours:
        return f"{hours}h{minutes:02d}"
    return f"{minutes}min"


def _format_date_fr(dt: datetime) -> str:
    local = dt.astimezone(PARIS)
    return f"{local.day} {MONTHS_FR[local.month - 1]} {local.year}"


def _parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _text_channel_label(guild: discord.Guild, channel_id: int) -> str:
    channel = guild.get_channel(channel_id)
    return f"#{channel.name}" if channel else "#salon-supprimé"


def _voice_channel_label(guild: discord.Guild, channel_id: int) -> str:
    channel = guild.get_channel(channel_id)
    return channel.name if channel else "Salon supprimé"


def _member_label(guild: discord.Guild, user_id: int) -> str:
    member = guild.get_member(user_id)
    return member.display_name if member else f"Utilisateur {user_id}"


def _emoji_chip_key(row: dict) -> str:
    # Aucun glyphe emoji dans l'image (DejaVu Sans n'a pas les caractères emoji modernes) :
    # on affiche un nom sûr en ASCII — le nom custom Discord, ou le shortcode démojisé.
    if row["is_custom"]:
        return f":{row['emoji_name']}:"
    return emoji.demojize(row["emoji_name"])


async def _read_asset(asset: Optional[discord.Asset]) -> Optional[Image.Image]:
    if asset is None:
        return None
    try:
        data = await asset.read()
        return Image.open(BytesIO(data)).convert("RGBA")
    except Exception:
        return None


def _bucket_messages(rows: list[dict]) -> dict:
    by_channel = Counter()
    by_user = Counter()
    by_hour = Counter()
    by_weekday = Counter()
    by_day = Counter()
    for row in rows:
        created = _parse_ts(row["created_at"])
        local = created.astimezone(PARIS)
        by_channel[row["channel_id"]] += 1
        by_user[row["user_id"]] += 1
        by_hour[local.hour] += 1
        by_weekday[local.weekday()] += 1
        by_day[local.date()] += 1
    return {
        "total": len(rows),
        "by_channel": by_channel,
        "by_user": by_user,
        "by_hour": by_hour,
        "by_weekday": by_weekday,
        "by_day": by_day,
        "active_users": {row["user_id"] for row in rows},
    }


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


def _compute_streak(days: list) -> int:
    if not days:
        return 0
    days_set = set(days)
    today = datetime.now(PARIS).date()
    if today in days_set:
        cursor = today
    elif (today - timedelta(days=1)) in days_set:
        cursor = today - timedelta(days=1)
    else:
        return 0
    streak = 0
    while cursor in days_set:
        streak += 1
        cursor -= timedelta(days=1)
    return streak


def _date_range(days: list) -> list:
    ordered = sorted(days)
    return [ordered[0] + timedelta(days=i) for i in range((ordered[-1] - ordered[0]).days + 1)]


# ---------------------------------------------------------------------------
# Graphiques (matplotlib via cardkit.wrap_chart — jamais de double axe Y :
# deux mesures d'échelles différentes = deux sous-graphiques empilés)
# ---------------------------------------------------------------------------

def _render_hour_panel(width: float, height: float, hour_counts: Counter, title: str, color: str = SERIES_MESSAGES_HEX):
    if sum(hour_counts.values()) == 0:
        return render_empty_panel(width, height)

    def draw(fig, axes):
        ax = axes[0]
        hours = list(range(24))
        values = [hour_counts.get(h, 0) for h in hours]
        ax.bar(hours, values, color=color, width=0.7)
        ax.set_xticks(range(0, 24, 6))
        ax.set_xticklabels([f"{h}h" for h in range(0, 24, 6)])
        ax.yaxis.grid(True, color=GRIDLINE_HEX, linewidth=1)
        ax.set_title(title, fontsize=13, loc="left", color=INK_PRIMARY_HEX, pad=12)

    return wrap_chart(width, height, draw)


def _render_trend_panel(width: float, height: float, dates: list, values: list, title: str, color: str = SERIES_MESSAGES_HEX):
    def draw(fig, axes):
        ax = axes[0]
        ax.plot(dates, values, color=color, linewidth=2, solid_capstyle="round")
        ax.fill_between(dates, values, color=color, alpha=0.10)
        ax.yaxis.grid(True, color=GRIDLINE_HEX, linewidth=1)
        for tick_label in ax.get_xticklabels():
            tick_label.set_rotation(30)
            tick_label.set_ha("right")
        ax.set_title(title, fontsize=13, loc="left", color=INK_PRIMARY_HEX, pad=12)

    return wrap_chart(width, height, draw)


def _render_dual_trend_panel(width: float, height: float, dates: list, messages_values: list, voice_hours_values: list, title: str):
    def draw(fig, axes):
        ax1, ax2 = axes
        for ax, values, color, label in (
            (ax1, messages_values, SERIES_MESSAGES_HEX, "Messages"),
            (ax2, voice_hours_values, SERIES_VOICE_HEX, "Heures vocales"),
        ):
            ax.plot(dates, values, color=color, linewidth=2, solid_capstyle="round")
            ax.fill_between(dates, values, color=color, alpha=0.10)
            ax.yaxis.grid(True, color=GRIDLINE_HEX, linewidth=1)
            ax.set_ylabel(label, color=INK_SECONDARY_HEX, fontsize=9)
        for tick_label in ax2.get_xticklabels():
            tick_label.set_rotation(30)
            tick_label.set_ha("right")
        ax1.set_title(title, fontsize=13, loc="left", color=INK_PRIMARY_HEX, pad=12)

    return wrap_chart(width, height, draw, nrows=2, sharex=True)


class StatCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # -- /serverstat ----------------------------------------------------------

    @app_commands.command(name="serverstat", description="Statistiques du serveur : messages, vocal, classements, activité")
    @app_commands.describe(periode="Fenêtre pour les classements et graphiques (les totaux 7j/30j/Tout restent toujours affichés)")
    @app_commands.choices(periode=PERIOD_CHOICES)
    @app_commands.guild_only()
    async def serverstat(self, interaction: discord.Interaction, periode: app_commands.Choice[str] = None):
        period = periode.value if periode else "30d"
        period_label = PERIOD_LABELS[period]
        await interaction.response.defer()

        guild = interaction.guild
        now = discord.utils.utcnow()

        all_messages = await get_messages(guild_id=guild.id, window_start=EPOCH, window_end=now)
        counts_7d = sum(1 for m in all_messages if _parse_ts(m["created_at"]) >= now - timedelta(days=7))
        counts_30d = sum(1 for m in all_messages if _parse_ts(m["created_at"]) >= now - timedelta(days=30))
        focus_start, focus_end = _window(period)
        focus_messages = [m for m in all_messages if focus_start <= _parse_ts(m["created_at"]) < focus_end]
        buckets = _bucket_messages(focus_messages)
        messages_period_value = {"7d": counts_7d, "30d": counts_30d, "all": len(all_messages)}[period]

        voice_7d = await get_voice_seconds_breakdown(guild_id=guild.id, window_start=now - timedelta(days=7), window_end=now)
        voice_30d = await get_voice_seconds_breakdown(guild_id=guild.id, window_start=now - timedelta(days=30), window_end=now)
        voice_all = await get_voice_seconds_breakdown(guild_id=guild.id, window_start=EPOCH, window_end=now)
        voice_focus = {"7d": voice_7d, "30d": voice_30d, "all": voice_all}[period]
        voice_period_seconds = sum(r["seconds"] for r in voice_focus)

        icon_image = await _read_asset(guild.icon)
        subtitle = [f"Créé le {_format_date_fr(guild.created_at)}"]
        if guild.me.joined_at:
            subtitle.append(f"Bot ajouté le {_format_date_fr(guild.me.joined_at)}")
        header = render_header(CONTENT_WIDTH, icon_image, guild.name, subtitle)

        weekday_label = WEEKDAY_NAMES[buckets["by_weekday"].most_common(1)[0][0]] if buckets["by_weekday"] else "—"
        tiles = [
            StatTile(
                "Messages", str(messages_period_value), f"({period_label})",
                f"7j {counts_7d} · 30j {counts_30d} · Tout {len(all_messages)}",
            ),
            StatTile(
                "Heures vocales", _format_duration(voice_period_seconds), f"({period_label})",
                f"7j {_format_duration(sum(r['seconds'] for r in voice_7d))} · "
                f"30j {_format_duration(sum(r['seconds'] for r in voice_30d))} · "
                f"Tout {_format_duration(sum(r['seconds'] for r in voice_all))}",
            ),
            StatTile(
                "Serveur", str(len(buckets["active_users"])), "contributeurs actifs",
                f"Jour le + actif : {weekday_label}",
            ),
        ]
        stat_row = render_stat_row(CONTENT_WIDTH, tiles)

        text_ranking = [(_text_channel_label(guild, cid), f"{n} messages") for cid, n in buckets["by_channel"].most_common(10)]
        voice_by_channel = Counter()
        for row in voice_focus:
            voice_by_channel[row["channel_id"]] += row["seconds"]
        voice_ranking = [(_voice_channel_label(guild, cid), _format_duration(s)) for cid, s in voice_by_channel.most_common(10)]
        channel_row = hrow([
            render_ranking_card(split_width(CONTENT_WIDTH, 2), "Top salons texte", text_ranking),
            render_ranking_card(split_width(CONTENT_WIDTH, 2), "Top salons vocaux", voice_ranking),
        ])

        members_msg_ranking = [(_member_label(guild, uid), f"{n} messages") for uid, n in buckets["by_user"].most_common(10)]
        voice_by_user = Counter()
        for row in voice_focus:
            voice_by_user[row["user_id"]] += row["seconds"]
        members_voice_ranking = [(_member_label(guild, uid), _format_duration(s)) for uid, s in voice_by_user.most_common(10)]
        members_row = hrow([
            render_ranking_card(split_width(CONTENT_WIDTH, 2), "Top membres (messages)", members_msg_ranking),
            render_ranking_card(split_width(CONTENT_WIDTH, 2), "Top membres (vocal)", members_voice_ranking),
        ])

        reactions = await get_emoji_events(guild_id=guild.id, window_start=focus_start, window_end=focus_end, source="reaction")
        reactive_users = Counter(r["user_id"] for r in reactions)
        reactive_ranking = [(_member_label(guild, uid), f"{n} réactions") for uid, n in reactive_users.most_common(10)]
        reactive_card = render_ranking_card(CONTENT_WIDTH, "Membres les plus réactifs", reactive_ranking)

        hour_chart = _render_hour_panel(CONTENT_WIDTH, 340, buckets["by_hour"], "Activité par heure (messages)")

        if buckets["by_day"]:
            date_range = _date_range(buckets["by_day"].keys())
            voice_rows = await get_voice_sessions_overlapping(guild_id=guild.id, window_start=focus_start, window_end=focus_end)
            voice_daily = _bucket_voice_daily(voice_rows, focus_start, focus_end)
            trend_chart = _render_dual_trend_panel(
                CONTENT_WIDTH, 480, date_range,
                [buckets["by_day"].get(d, 0) for d in date_range],
                [voice_daily.get(d, 0.0) / 3600 for d in date_range],
                "Activité dans le temps",
            )
        else:
            trend_chart = render_empty_panel(CONTENT_WIDTH, 480)

        card = compose_card(
            header,
            [stat_row, channel_row, members_row, reactive_card, hour_chart, trend_chart],
            f"Généré le {_format_date_fr(now)} · Période : {period_label} · ScoobyBot",
        )
        await interaction.followup.send(
            content=f"📊 **Statistiques de {guild.name}**",
            file=to_discord_file(card, "serverstat.png"),
        )

    # -- /userstat --------------------------------------------------------------

    @app_commands.command(name="userstat", description="Statistiques d'un membre : messages, vocal, classement, streak")
    @app_commands.describe(membre="Le membre à consulter (toi par défaut)", periode="Fenêtre pour les classements/salons/graphique")
    @app_commands.choices(periode=PERIOD_CHOICES)
    @app_commands.guild_only()
    async def userstat(self, interaction: discord.Interaction, membre: discord.Member = None, periode: app_commands.Choice[str] = None):
        member = membre or interaction.user
        period = periode.value if periode else "30d"
        period_label = PERIOD_LABELS[period]
        await interaction.response.defer()

        guild = interaction.guild
        now = discord.utils.utcnow()
        focus_start, focus_end = _window(period)

        all_messages = await get_messages(guild_id=guild.id, window_start=EPOCH, window_end=now)
        buckets_all = _bucket_messages(all_messages)
        user_messages_7d = sum(1 for m in all_messages if m["user_id"] == member.id and _parse_ts(m["created_at"]) >= now - timedelta(days=7))
        user_messages_30d = sum(1 for m in all_messages if m["user_id"] == member.id and _parse_ts(m["created_at"]) >= now - timedelta(days=30))
        user_messages_all = buckets_all["by_user"].get(member.id, 0)
        focus_messages = [m for m in all_messages if focus_start <= _parse_ts(m["created_at"]) < focus_end and m["user_id"] == member.id]
        user_buckets = _bucket_messages(focus_messages)
        messages_period_value = {"7d": user_messages_7d, "30d": user_messages_30d, "all": user_messages_all}[period]

        voice_7d = await get_voice_seconds_breakdown(guild_id=guild.id, window_start=now - timedelta(days=7), window_end=now, user_id=member.id)
        voice_30d = await get_voice_seconds_breakdown(guild_id=guild.id, window_start=now - timedelta(days=30), window_end=now, user_id=member.id)
        voice_all = await get_voice_seconds_breakdown(guild_id=guild.id, window_start=EPOCH, window_end=now, user_id=member.id)
        voice_focus = {"7d": voice_7d, "30d": voice_30d, "all": voice_all}[period]
        voice_period_seconds = sum(r["seconds"] for r in voice_focus)

        ranking = buckets_all["by_user"].most_common()
        messages_rank = next((i for i, (uid, _n) in enumerate(ranking, start=1) if uid == member.id), None)

        voice_all_guild = await get_voice_seconds_breakdown(guild_id=guild.id, window_start=EPOCH, window_end=now)
        voice_totals_by_user = Counter()
        for row in voice_all_guild:
            voice_totals_by_user[row["user_id"]] += row["seconds"]
        voice_rank = next((i for i, (uid, _s) in enumerate(voice_totals_by_user.most_common(), start=1) if uid == member.id), None)

        icon_image = await _read_asset(member.display_avatar)
        subtitle = []
        if member.joined_at:
            subtitle.append(f"Arrivé le {_format_date_fr(member.joined_at)}")
        subtitle.append(f"Compte créé le {_format_date_fr(member.created_at)}")
        header = render_header(CONTENT_WIDTH, icon_image, member.display_name, subtitle)

        member_days = await get_distinct_message_days(guild_id=guild.id, user_id=member.id)
        streak = _compute_streak(member_days)

        tiles = [
            StatTile(
                "Messages", str(messages_period_value), f"({period_label})",
                f"7j {user_messages_7d} · 30j {user_messages_30d} · Tout {user_messages_all}",
            ),
            StatTile(
                "Heures vocales", _format_duration(voice_period_seconds), f"({period_label})",
                f"7j {_format_duration(sum(r['seconds'] for r in voice_7d))} · "
                f"30j {_format_duration(sum(r['seconds'] for r in voice_30d))} · "
                f"Tout {_format_duration(sum(r['seconds'] for r in voice_all))}",
            ),
            StatTile("Classement", f"#{messages_rank or '—'}", "messages", f"Vocal : #{voice_rank or '—'}"),
            StatTile("Streak", str(streak), "jour(s) consécutif(s)"),
        ]
        stat_row = render_stat_row(CONTENT_WIDTH, tiles)

        top_text_channels = [(_text_channel_label(guild, cid), f"{n} messages") for cid, n in user_buckets["by_channel"].most_common(5)]
        user_voice_channels = Counter()
        for row in voice_focus:
            user_voice_channels[row["channel_id"]] += row["seconds"]
        top_voice_channels = [(_voice_channel_label(guild, cid), _format_duration(s)) for cid, s in user_voice_channels.most_common(5)]
        channel_row = hrow([
            render_ranking_card(split_width(CONTENT_WIDTH, 2), "Top salons texte", top_text_channels, max_rows=5),
            render_ranking_card(split_width(CONTENT_WIDTH, 2), "Top salons vocaux", top_voice_channels, max_rows=5),
        ])

        user_emojis = await get_emoji_events(guild_id=guild.id, window_start=focus_start, window_end=focus_end, user_id=member.id)
        emoji_counts = Counter(_emoji_chip_key(row) for row in user_emojis)
        chips = [f"{name} ×{n}" for name, n in emoji_counts.most_common(15)]
        chip_row = render_chip_row(CONTENT_WIDTH, "Emojis les plus utilisés (messages + réactions)", chips)

        hour_chart = _render_hour_panel(CONTENT_WIDTH, 340, user_buckets["by_hour"], f"Activité par heure — {member.display_name}")
        if user_buckets["by_day"]:
            date_range = _date_range(user_buckets["by_day"].keys())
            trend_chart = _render_trend_panel(
                CONTENT_WIDTH, 340, date_range,
                [user_buckets["by_day"].get(d, 0) for d in date_range],
                f"Messages dans le temps — {member.display_name}",
            )
        else:
            trend_chart = render_empty_panel(CONTENT_WIDTH, 340)

        card = compose_card(
            header,
            [stat_row, channel_row, chip_row, hour_chart, trend_chart],
            f"Généré le {_format_date_fr(now)} · Période : {period_label} · ScoobyBot",
        )
        await interaction.followup.send(
            content=f"📊 **Statistiques de {member.display_name}**",
            file=to_discord_file(card, "userstat.png"),
        )

    # -- /channelstat -----------------------------------------------------------

    @app_commands.command(name="channelstat", description="Statistiques d'un salon : messages ou vocal, top membres, activité")
    @app_commands.describe(salon="Le salon à consulter (salon courant par défaut)", periode="Fenêtre pour les classements et le graphique")
    @app_commands.choices(periode=PERIOD_CHOICES)
    @app_commands.guild_only()
    async def channelstat(
        self,
        interaction: discord.Interaction,
        salon: Union[discord.TextChannel, discord.VoiceChannel] = None,
        periode: app_commands.Choice[str] = None,
    ):
        channel = salon or interaction.channel
        period = periode.value if periode else "30d"
        period_label = PERIOD_LABELS[period]
        await interaction.response.defer()

        guild = interaction.guild
        now = discord.utils.utcnow()
        focus_start, focus_end = _window(period)
        is_voice = isinstance(channel, discord.VoiceChannel)

        title = channel.name if is_voice else f"#{channel.name}"
        header = render_header(CONTENT_WIDTH, None, title, ["Salon vocal" if is_voice else "Salon texte"])

        if is_voice:
            voice_7d = await get_voice_seconds_breakdown(guild_id=guild.id, window_start=now - timedelta(days=7), window_end=now, channel_id=channel.id)
            voice_30d = await get_voice_seconds_breakdown(guild_id=guild.id, window_start=now - timedelta(days=30), window_end=now, channel_id=channel.id)
            voice_all = await get_voice_seconds_breakdown(guild_id=guild.id, window_start=EPOCH, window_end=now, channel_id=channel.id)
            voice_focus = {"7d": voice_7d, "30d": voice_30d, "all": voice_all}[period]

            tiles = [StatTile(
                "Heures vocales", _format_duration(sum(r["seconds"] for r in voice_focus)), f"({period_label})",
                f"7j {_format_duration(sum(r['seconds'] for r in voice_7d))} · "
                f"30j {_format_duration(sum(r['seconds'] for r in voice_30d))} · "
                f"Tout {_format_duration(sum(r['seconds'] for r in voice_all))}",
            )]
            stat_row = render_stat_row(CONTENT_WIDTH, tiles)

            by_user = Counter()
            for row in voice_focus:
                by_user[row["user_id"]] += row["seconds"]
            ranking = [(_member_label(guild, uid), _format_duration(s)) for uid, s in by_user.most_common(10)]
            ranking_card = render_ranking_card(CONTENT_WIDTH, "Top membres", ranking)

            voice_rows = await get_voice_sessions_overlapping(guild_id=guild.id, window_start=focus_start, window_end=focus_end, channel_id=channel.id)
            voice_daily = _bucket_voice_daily(voice_rows, focus_start, focus_end)
            if voice_daily:
                date_range = _date_range(voice_daily.keys())
                chart = _render_trend_panel(
                    CONTENT_WIDTH, 340, date_range, [voice_daily.get(d, 0.0) / 3600 for d in date_range],
                    f"Heures vocales dans le temps — {channel.name}", color=SERIES_VOICE_HEX,
                )
            else:
                chart = render_empty_panel(CONTENT_WIDTH, 340)

            body_blocks = [stat_row, ranking_card, chart]
        else:
            all_messages = await get_messages(guild_id=guild.id, window_start=EPOCH, window_end=now, channel_id=channel.id)
            counts_7d = sum(1 for m in all_messages if _parse_ts(m["created_at"]) >= now - timedelta(days=7))
            counts_30d = sum(1 for m in all_messages if _parse_ts(m["created_at"]) >= now - timedelta(days=30))
            focus_messages = [m for m in all_messages if focus_start <= _parse_ts(m["created_at"]) < focus_end]
            buckets = _bucket_messages(focus_messages)
            period_value = {"7d": counts_7d, "30d": counts_30d, "all": len(all_messages)}[period]

            tiles = [StatTile(
                "Messages", str(period_value), f"({period_label})",
                f"7j {counts_7d} · 30j {counts_30d} · Tout {len(all_messages)}",
            )]
            stat_row = render_stat_row(CONTENT_WIDTH, tiles)

            ranking = [(_member_label(guild, uid), f"{n} messages") for uid, n in buckets["by_user"].most_common(10)]
            ranking_card = render_ranking_card(CONTENT_WIDTH, "Top membres", ranking)

            hour_chart = _render_hour_panel(CONTENT_WIDTH, 340, buckets["by_hour"], f"Activité par heure — {channel.name}")
            if buckets["by_day"]:
                date_range = _date_range(buckets["by_day"].keys())
                trend_chart = _render_trend_panel(
                    CONTENT_WIDTH, 340, date_range, [buckets["by_day"].get(d, 0) for d in date_range],
                    f"Messages dans le temps — {channel.name}",
                )
            else:
                trend_chart = render_empty_panel(CONTENT_WIDTH, 340)

            body_blocks = [stat_row, ranking_card, hour_chart, trend_chart]

        card = compose_card(
            header, body_blocks,
            f"Généré le {_format_date_fr(now)} · Période : {period_label} · ScoobyBot",
        )
        await interaction.followup.send(
            content=f"📊 **Statistiques de {channel.name}**",
            file=to_discord_file(card, "channelstat.png"),
        )


async def setup(bot):
    await bot.add_cog(StatCommands(bot))
