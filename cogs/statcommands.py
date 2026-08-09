import io
from collections import Counter, defaultdict
from datetime import datetime, time, timedelta, timezone
from typing import Union
from zoneinfo import ZoneInfo

import discord
import matplotlib

matplotlib.use("Agg")  # doit précéder l'import de pyplot : pas de serveur d'affichage sur Railway
import matplotlib.pyplot as plt
from discord import app_commands
from discord.ext import commands

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

PERIOD_CHOICES = [
    app_commands.Choice(name="7 jours", value="7d"),
    app_commands.Choice(name="30 jours", value="30d"),
    app_commands.Choice(name="Tout", value="all"),
]

# Palette (skill dataviz — pas de contenu de message, uniquement du dénombrement ;
# thème sombre pour matcher l'UI Discord par défaut).
SURFACE = "#1a1a19"
INK_PRIMARY = "#ffffff"
INK_SECONDARY = "#c3c2b7"
GRIDLINE = "#2c2c2a"
BASELINE = "#383835"
SERIES_MESSAGES = "#3987e5"  # catégoriel slot 1 (bleu, dark)
SERIES_VOICE = "#d95926"     # catégoriel slot 2 (orange, dark)


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


def _parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value)


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


# ---------------------------------------------------------------------------
# Graphiques (matplotlib, thème sombre — voir skill dataviz pour les specs)
# ---------------------------------------------------------------------------

def _style_axes(ax) -> None:
    ax.set_facecolor(SURFACE)
    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.spines["bottom"].set_visible(True)
    ax.spines["bottom"].set_color(BASELINE)
    ax.tick_params(colors=INK_SECONDARY, labelsize=9)
    ax.set_axisbelow(True)


def _figure_to_file(fig, filename: str) -> discord.File:
    buffer = io.BytesIO()
    fig.savefig(buffer, format="png", facecolor=SURFACE, bbox_inches="tight")
    plt.close(fig)
    buffer.seek(0)
    return discord.File(buffer, filename=filename)


def render_hour_chart(hour_counts: Counter, title: str) -> discord.File:
    fig, ax = plt.subplots(figsize=(8, 3.3), dpi=150)
    fig.patch.set_facecolor(SURFACE)
    _style_axes(ax)
    hours = list(range(24))
    values = [hour_counts.get(h, 0) for h in hours]
    ax.bar(hours, values, color=SERIES_MESSAGES, width=0.7)
    ax.set_xticks(range(0, 24, 3))
    ax.set_xticklabels([f"{h}h" for h in range(0, 24, 3)])
    ax.xaxis.grid(False)
    ax.yaxis.grid(True, color=GRIDLINE, linewidth=1)
    ax.set_title(title, fontsize=12, loc="left", color=INK_PRIMARY, pad=12)
    fig.tight_layout()
    return _figure_to_file(fig, "heures.png")


def render_single_trend_chart(dates: list, values: list[float], title: str, color: str = SERIES_MESSAGES) -> discord.File:
    fig, ax = plt.subplots(figsize=(8, 3.3), dpi=150)
    fig.patch.set_facecolor(SURFACE)
    _style_axes(ax)
    ax.plot(dates, values, color=color, linewidth=2, solid_capstyle="round")
    ax.fill_between(dates, values, color=color, alpha=0.10)
    ax.yaxis.grid(True, color=GRIDLINE, linewidth=1)
    ax.xaxis.grid(False)
    fig.autofmt_xdate(rotation=30)
    ax.set_title(title, fontsize=12, loc="left", color=INK_PRIMARY, pad=12)
    fig.tight_layout()
    return _figure_to_file(fig, "activite.png")


def render_dual_trend_chart(dates: list, messages_values: list[float], voice_hours_values: list[float], title: str) -> discord.File:
    # Deux mesures d'échelles différentes (messages = compte, vocal = heures) :
    # jamais un double axe Y, on empile deux sous-graphiques à hue unique
    # partageant l'axe des dates (small multiples).
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(8, 5.2), dpi=150, sharex=True)
    fig.patch.set_facecolor(SURFACE)
    for ax, values, color, label in (
        (ax1, messages_values, SERIES_MESSAGES, "Messages"),
        (ax2, voice_hours_values, SERIES_VOICE, "Heures vocales"),
    ):
        _style_axes(ax)
        ax.plot(dates, values, color=color, linewidth=2, solid_capstyle="round")
        ax.fill_between(dates, values, color=color, alpha=0.10)
        ax.yaxis.grid(True, color=GRIDLINE, linewidth=1)
        ax.xaxis.grid(False)
        ax.set_ylabel(label, color=INK_SECONDARY, fontsize=9)
    fig.autofmt_xdate(rotation=30)
    ax1.set_title(title, fontsize=12, loc="left", color=INK_PRIMARY, pad=12)
    fig.tight_layout()
    return _figure_to_file(fig, "activite.png")


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
        await interaction.response.defer()

        guild = interaction.guild
        now = discord.utils.utcnow()

        # Messages : un seul fetch depuis le début du suivi, tout le reste
        # (compteurs 7j/30j/Tout, classements, heatmap horaire) se dérive en Python.
        all_messages = await get_messages(guild_id=guild.id, window_start=EPOCH, window_end=now)
        counts_7d = sum(1 for m in all_messages if _parse_ts(m["created_at"]) >= now - timedelta(days=7))
        counts_30d = sum(1 for m in all_messages if _parse_ts(m["created_at"]) >= now - timedelta(days=30))
        focus_start, focus_end = _window(period)
        focus_messages = [m for m in all_messages if focus_start <= _parse_ts(m["created_at"]) < focus_end]
        buckets = _bucket_messages(focus_messages)

        # Vocal : une requête par fenêtre (agrégation faite côté Postgres).
        voice_7d = await get_voice_seconds_breakdown(guild_id=guild.id, window_start=now - timedelta(days=7), window_end=now)
        voice_30d = await get_voice_seconds_breakdown(guild_id=guild.id, window_start=now - timedelta(days=30), window_end=now)
        voice_all = await get_voice_seconds_breakdown(guild_id=guild.id, window_start=EPOCH, window_end=now)
        voice_focus = {"7d": voice_7d, "30d": voice_30d, "all": voice_all}[period]

        embed = discord.Embed(title=f"📊 Statistiques de {guild.name}", color=discord.Color.teal())
        embed.add_field(
            name="💬 Messages",
            value=f"7j : **{counts_7d}**\n30j : **{counts_30d}**\nTout : **{len(all_messages)}**",
            inline=True,
        )
        embed.add_field(
            name="🎙️ Heures vocales",
            value=(
                f"7j : **{_format_duration(sum(r['seconds'] for r in voice_7d))}**\n"
                f"30j : **{_format_duration(sum(r['seconds'] for r in voice_30d))}**\n"
                f"Tout : **{_format_duration(sum(r['seconds'] for r in voice_all))}**"
            ),
            inline=True,
        )

        created_ts = int(guild.created_at.timestamp())
        bot_added_ts = int(guild.me.joined_at.timestamp()) if guild.me.joined_at else None
        embed.add_field(
            name="📅 Dates",
            value=(
                f"Serveur créé : <t:{created_ts}:D>\n"
                + (f"Bot ajouté : <t:{bot_added_ts}:D>" if bot_added_ts else "")
            ),
            inline=False,
        )

        lines = []
        for cid, n in buckets["by_channel"].most_common(10):
            channel = guild.get_channel(cid)
            name = channel.mention if channel else f"#{cid}"
            lines.append(f"{name} — {n} messages")
        if lines:
            embed.add_field(name="🏆 Top salons texte (messages)", value="\n".join(lines), inline=False)

        voice_by_channel = Counter()
        for row in voice_focus:
            voice_by_channel[row["channel_id"]] += row["seconds"]
        lines = []
        for cid, seconds in voice_by_channel.most_common(10):
            channel = guild.get_channel(cid)
            name = channel.mention if channel else f"#{cid}"
            lines.append(f"{name} — {_format_duration(seconds)}")
        if lines:
            embed.add_field(name="🏆 Top salons vocaux (heures)", value="\n".join(lines), inline=False)

        lines = []
        for uid, n in buckets["by_user"].most_common(10):
            member = guild.get_member(uid)
            name = member.display_name if member else f"Utilisateur {uid}"
            lines.append(f"**{name}** — {n} messages")
        if lines:
            embed.add_field(name="🗣️ Top membres (messages)", value="\n".join(lines), inline=True)

        voice_by_user = Counter()
        for row in voice_focus:
            voice_by_user[row["user_id"]] += row["seconds"]
        lines = []
        for uid, seconds in voice_by_user.most_common(10):
            member = guild.get_member(uid)
            name = member.display_name if member else f"Utilisateur {uid}"
            lines.append(f"**{name}** — {_format_duration(seconds)}")
        if lines:
            embed.add_field(name="🎙️ Top membres (vocal)", value="\n".join(lines), inline=True)

        embed.add_field(name="👥 Contributeurs actifs", value=str(len(buckets["active_users"])), inline=True)

        reactions = await get_emoji_events(guild_id=guild.id, window_start=focus_start, window_end=focus_end, source="reaction")
        reactive_users = Counter(r["user_id"] for r in reactions)
        lines = []
        for uid, n in reactive_users.most_common(10):
            member = guild.get_member(uid)
            name = member.display_name if member else f"Utilisateur {uid}"
            lines.append(f"**{name}** — {n} réactions")
        if lines:
            embed.add_field(name="😄 Membres les plus réactifs", value="\n".join(lines), inline=True)

        if buckets["by_weekday"]:
            top_weekday, _ = buckets["by_weekday"].most_common(1)[0]
            embed.add_field(name="📆 Jour le plus actif", value=WEEKDAY_NAMES[top_weekday], inline=True)

        files = [render_hour_chart(buckets["by_hour"], "Activité par heure (messages)")]

        all_days = sorted(buckets["by_day"].keys())
        if all_days:
            date_range = [all_days[0] + timedelta(days=i) for i in range((all_days[-1] - all_days[0]).days + 1)]
            voice_rows = await get_voice_sessions_overlapping(guild_id=guild.id, window_start=focus_start, window_end=focus_end)
            voice_daily = _bucket_voice_daily(voice_rows, focus_start, focus_end)
            files.append(render_dual_trend_chart(
                date_range,
                [buckets["by_day"].get(d, 0) for d in date_range],
                [voice_daily.get(d, 0.0) / 3600 for d in date_range],
                "Activité dans le temps",
            ))

        await interaction.followup.send(embed=embed, files=files)

    # -- /userstat --------------------------------------------------------------

    @app_commands.command(name="userstat", description="Statistiques d'un membre : messages, vocal, classement, streak")
    @app_commands.describe(membre="Le membre à consulter (toi par défaut)", periode="Fenêtre pour les classements/salons/graphique")
    @app_commands.choices(periode=PERIOD_CHOICES)
    @app_commands.guild_only()
    async def userstat(self, interaction: discord.Interaction, membre: discord.Member = None, periode: app_commands.Choice[str] = None):
        member = membre or interaction.user
        period = periode.value if periode else "30d"
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

        voice_7d = await get_voice_seconds_breakdown(guild_id=guild.id, window_start=now - timedelta(days=7), window_end=now, user_id=member.id)
        voice_30d = await get_voice_seconds_breakdown(guild_id=guild.id, window_start=now - timedelta(days=30), window_end=now, user_id=member.id)
        voice_all = await get_voice_seconds_breakdown(guild_id=guild.id, window_start=EPOCH, window_end=now, user_id=member.id)
        if period == "7d":
            voice_focus = voice_7d
        elif period == "30d":
            voice_focus = voice_30d
        else:
            voice_focus = voice_all

        # Classement serveur (Tout, messages + vocal) — dérivé du même fetch.
        messages_rank = None
        ranking = buckets_all["by_user"].most_common()
        for i, (uid, _n) in enumerate(ranking, start=1):
            if uid == member.id:
                messages_rank = i
                break

        voice_all_guild = await get_voice_seconds_breakdown(guild_id=guild.id, window_start=EPOCH, window_end=now)
        voice_totals_by_user = Counter()
        for row in voice_all_guild:
            voice_totals_by_user[row["user_id"]] += row["seconds"]
        voice_rank = None
        for i, (uid, _s) in enumerate(voice_totals_by_user.most_common(), start=1):
            if uid == member.id:
                voice_rank = i
                break

        embed = discord.Embed(title=f"📊 Statistiques de {member.display_name}", color=discord.Color.teal())
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(
            name="💬 Messages",
            value=f"7j : **{user_messages_7d}**\n30j : **{user_messages_30d}**\nTout : **{user_messages_all}**",
            inline=True,
        )
        embed.add_field(
            name="🎙️ Heures vocales",
            value=(
                f"7j : **{_format_duration(sum(r['seconds'] for r in voice_7d))}**\n"
                f"30j : **{_format_duration(sum(r['seconds'] for r in voice_30d))}**\n"
                f"Tout : **{_format_duration(sum(r['seconds'] for r in voice_all))}**"
            ),
            inline=True,
        )
        embed.add_field(
            name="🏆 Classement serveur",
            value=f"Messages : **#{messages_rank or '—'}**\nVocal : **#{voice_rank or '—'}**",
            inline=True,
        )

        top_channels = user_buckets["by_channel"].most_common(5)
        lines = [f"{(guild.get_channel(cid).mention if guild.get_channel(cid) else f'#{cid}')} — {n} messages" for cid, n in top_channels]
        if lines:
            embed.add_field(name="📍 Top salons texte", value="\n".join(lines), inline=True)

        user_voice_channels = Counter()
        for row in voice_focus:
            user_voice_channels[row["channel_id"]] += row["seconds"]
        lines = [f"{(guild.get_channel(cid).mention if guild.get_channel(cid) else f'#{cid}')} — {_format_duration(s)}" for cid, s in user_voice_channels.most_common(5)]
        if lines:
            embed.add_field(name="📍 Top salons vocaux", value="\n".join(lines), inline=True)

        member_days = await get_distinct_message_days(guild_id=guild.id, user_id=member.id)
        streak = _compute_streak(member_days)
        embed.add_field(name="🔥 Streak", value=f"{streak} jour(s) consécutif(s)", inline=True)

        user_emojis = await get_emoji_events(guild_id=guild.id, window_start=focus_start, window_end=focus_end, user_id=member.id)
        emoji_counts = Counter(e["emoji_name"] for e in user_emojis)
        if emoji_counts:
            top_emojis = " ".join(f"{name}×{n}" for name, n in emoji_counts.most_common(10))
            embed.add_field(name="😄 Emojis les plus utilisés (messages + réactions)", value=top_emojis, inline=False)

        joined_ts = int(member.joined_at.timestamp()) if member.joined_at else None
        created_ts = int(member.created_at.timestamp())
        embed.add_field(
            name="📅 Dates",
            value=(
                (f"Arrivé sur le serveur : <t:{joined_ts}:D>\n" if joined_ts else "")
                + f"Compte créé : <t:{created_ts}:D>"
            ),
            inline=False,
        )

        files = [render_hour_chart(user_buckets["by_hour"], f"Activité par heure — {member.display_name}")]
        all_days = sorted(user_buckets["by_day"].keys())
        if all_days:
            date_range = [all_days[0] + timedelta(days=i) for i in range((all_days[-1] - all_days[0]).days + 1)]
            files.append(render_single_trend_chart(
                date_range,
                [user_buckets["by_day"].get(d, 0) for d in date_range],
                f"Messages dans le temps — {member.display_name}",
                color=SERIES_MESSAGES,
            ))

        await interaction.followup.send(embed=embed, files=files)

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
        await interaction.response.defer()

        guild = interaction.guild
        now = discord.utils.utcnow()
        focus_start, focus_end = _window(period)
        is_voice = isinstance(channel, discord.VoiceChannel)

        embed = discord.Embed(title=f"📊 Statistiques de {channel.name}", color=discord.Color.teal())
        files = []

        if is_voice:
            voice_7d = await get_voice_seconds_breakdown(guild_id=guild.id, window_start=now - timedelta(days=7), window_end=now, channel_id=channel.id)
            voice_30d = await get_voice_seconds_breakdown(guild_id=guild.id, window_start=now - timedelta(days=30), window_end=now, channel_id=channel.id)
            voice_all = await get_voice_seconds_breakdown(guild_id=guild.id, window_start=EPOCH, window_end=now, channel_id=channel.id)
            voice_focus = {"7d": voice_7d, "30d": voice_30d, "all": voice_all}[period]

            embed.add_field(
                name="🎙️ Heures vocales",
                value=(
                    f"7j : **{_format_duration(sum(r['seconds'] for r in voice_7d))}**\n"
                    f"30j : **{_format_duration(sum(r['seconds'] for r in voice_30d))}**\n"
                    f"Tout : **{_format_duration(sum(r['seconds'] for r in voice_all))}**"
                ),
                inline=False,
            )

            by_user = Counter()
            for row in voice_focus:
                by_user[row["user_id"]] += row["seconds"]
            lines = []
            for uid, seconds in by_user.most_common(10):
                member = guild.get_member(uid)
                name = member.display_name if member else f"Utilisateur {uid}"
                lines.append(f"**{name}** — {_format_duration(seconds)}")
            if lines:
                embed.add_field(name="🏆 Top membres", value="\n".join(lines), inline=False)

            voice_rows = await get_voice_sessions_overlapping(guild_id=guild.id, window_start=focus_start, window_end=focus_end, channel_id=channel.id)
            voice_daily = _bucket_voice_daily(voice_rows, focus_start, focus_end)
            if voice_daily:
                all_days = sorted(voice_daily.keys())
                date_range = [all_days[0] + timedelta(days=i) for i in range((all_days[-1] - all_days[0]).days + 1)]
                files.append(render_single_trend_chart(
                    date_range,
                    [voice_daily.get(d, 0.0) / 3600 for d in date_range],
                    f"Heures vocales dans le temps — {channel.name}",
                    color=SERIES_VOICE,
                ))
        else:
            all_messages = await get_messages(guild_id=guild.id, window_start=EPOCH, window_end=now, channel_id=channel.id)
            counts_7d = sum(1 for m in all_messages if _parse_ts(m["created_at"]) >= now - timedelta(days=7))
            counts_30d = sum(1 for m in all_messages if _parse_ts(m["created_at"]) >= now - timedelta(days=30))
            focus_messages = [m for m in all_messages if focus_start <= _parse_ts(m["created_at"]) < focus_end]
            buckets = _bucket_messages(focus_messages)

            embed.add_field(
                name="💬 Messages",
                value=f"7j : **{counts_7d}**\n30j : **{counts_30d}**\nTout : **{len(all_messages)}**",
                inline=False,
            )

            lines = []
            for uid, n in buckets["by_user"].most_common(10):
                member = guild.get_member(uid)
                name = member.display_name if member else f"Utilisateur {uid}"
                lines.append(f"**{name}** — {n} messages")
            if lines:
                embed.add_field(name="🏆 Top membres", value="\n".join(lines), inline=False)

            files.append(render_hour_chart(buckets["by_hour"], f"Activité par heure — {channel.name}"))
            all_days = sorted(buckets["by_day"].keys())
            if all_days:
                date_range = [all_days[0] + timedelta(days=i) for i in range((all_days[-1] - all_days[0]).days + 1)]
                files.append(render_single_trend_chart(
                    date_range,
                    [buckets["by_day"].get(d, 0) for d in date_range],
                    f"Messages dans le temps — {channel.name}",
                    color=SERIES_MESSAGES,
                ))

        await interaction.followup.send(embed=embed, files=files)


async def setup(bot):
    await bot.add_cog(StatCommands(bot))
