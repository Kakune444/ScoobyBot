import asyncio
import os

from supabase import AsyncClient, acreate_client

_client: AsyncClient | None = None


async def get_client() -> AsyncClient:
    global _client
    if _client is None:
        url = os.environ["SUPABASE_URL"]
        key = os.environ["SUPABASE_KEY"]
        _client = await acreate_client(url, key)
    return _client


def fire_and_forget(coro) -> None:
    """Schedule a Supabase write in the background: never blocks the caller, never crashes on failure."""
    name = getattr(coro, "__qualname__", str(coro))
    task = asyncio.create_task(coro)

    def _log_if_failed(finished: asyncio.Task) -> None:
        if finished.cancelled():
            return
        error = finished.exception()
        if error is not None:
            print(f"Erreur Supabase ({name}) : {error}")

    task.add_done_callback(_log_if_failed)


# ---------------------------------------------------------------------------
# messages / members
# ---------------------------------------------------------------------------

async def insert_message(*, message_id, guild_id, channel_id, user_id, created_at):
    client = await get_client()
    await client.table("messages").insert({
        "message_id": message_id,
        "guild_id": guild_id,
        "channel_id": channel_id,
        "user_id": user_id,
        "created_at": created_at.isoformat(),
    }).execute()


async def bulk_insert_messages(rows: list[dict]) -> int:
    """rows : [{"message_id", "guild_id", "channel_id", "user_id", "created_at": datetime}, ...]
    Idempotent sur message_id (conflits ignorés) — utilisé par /initialize, relançable sans jamais dupliquer."""
    if not rows:
        return 0
    client = await get_client()
    chunk_size = 500
    for i in range(0, len(rows), chunk_size):
        chunk = rows[i:i + chunk_size]
        payload = [{**row, "created_at": row["created_at"].isoformat()} for row in chunk]
        await client.table("messages").upsert(payload, on_conflict="message_id", ignore_duplicates=True).execute()
    return len(rows)


async def touch_member(*, guild_id, user_id, activity_at, guild_joined_at=None, is_message=False):
    client = await get_client()
    await client.rpc("touch_member", {
        "p_guild_id": guild_id,
        "p_user_id": user_id,
        "p_activity_at": activity_at.isoformat(),
        "p_guild_joined_at": guild_joined_at.isoformat() if guild_joined_at else None,
        "p_is_message": is_message,
    }).execute()


# ---------------------------------------------------------------------------
# voice_sessions
# ---------------------------------------------------------------------------

async def open_voice_session(*, guild_id, user_id, channel_id, joined_at):
    client = await get_client()
    await client.table("voice_sessions").insert({
        "guild_id": guild_id,
        "user_id": user_id,
        "channel_id": channel_id,
        "joined_at": joined_at.isoformat(),
    }).execute()


async def close_voice_session(*, guild_id, user_id, channel_id, left_at):
    client = await get_client()
    await (
        client.table("voice_sessions")
        .update({"left_at": left_at.isoformat()})
        .eq("guild_id", guild_id)
        .eq("user_id", user_id)
        .eq("channel_id", channel_id)
        .is_("left_at", "null")
        .execute()
    )


async def insert_completed_voice_session(*, guild_id, user_id, channel_id, joined_at, left_at):
    """Session déjà close, insérée directement — utilisé par /addtime pour créditer du temps manuellement."""
    client = await get_client()
    await client.table("voice_sessions").insert({
        "guild_id": guild_id,
        "user_id": user_id,
        "channel_id": channel_id,
        "joined_at": joined_at.isoformat(),
        "left_at": left_at.isoformat(),
    }).execute()


async def bulk_insert_voice_sessions(rows: list[dict]) -> int:
    """rows : [{"guild_id", "user_id", "channel_id", "joined_at": datetime, "left_at": datetime}, ...]
    Sessions déjà closes, insérées par lots — utilisé par /importvoice."""
    if not rows:
        return 0
    client = await get_client()
    chunk_size = 500
    for i in range(0, len(rows), chunk_size):
        chunk = rows[i:i + chunk_size]
        payload = [
            {**row, "joined_at": row["joined_at"].isoformat(), "left_at": row["left_at"].isoformat()}
            for row in chunk
        ]
        await client.table("voice_sessions").insert(payload).execute()
    return len(rows)


async def delete_voice_sessions_ending_at(*, guild_id, left_at):
    """Supprime les sessions se terminant exactement à left_at. Toutes les sessions
    synthétiques d'un /importvoice partagent le même left_at (leur marqueur) :
    les effacer avant de réinsérer rend l'import relançable sans doublon."""
    client = await get_client()
    await (
        client.table("voice_sessions")
        .delete()
        .eq("guild_id", guild_id)
        .eq("left_at", left_at.isoformat())
        .execute()
    )


async def get_open_voice_sessions(*, guild_id):
    """Returns [{"user_id": ..., "channel_id": ...}, ...] for every session still open in this guild."""
    client = await get_client()
    res = (
        await client.table("voice_sessions")
        .select("user_id, channel_id")
        .eq("guild_id", guild_id)
        .is_("left_at", "null")
        .execute()
    )
    return res.data


# ---------------------------------------------------------------------------
# emoji_events (réactions + emojis dans le texte des messages)
# ---------------------------------------------------------------------------

async def record_emoji_event(*, guild_id, channel_id, message_id, user_id, source, emoji_id, emoji_name, is_custom, is_animated, created_at):
    client = await get_client()
    await client.table("emoji_events").insert({
        "guild_id": guild_id,
        "channel_id": channel_id,
        "message_id": message_id,
        "user_id": user_id,
        "source": source,
        "emoji_id": emoji_id,
        "emoji_name": emoji_name,
        "is_custom": is_custom,
        "is_animated": is_animated,
        "created_at": created_at.isoformat(),
    }).execute()


# ---------------------------------------------------------------------------
# invites / invite_uses
# ---------------------------------------------------------------------------

async def upsert_invite(*, invite_code, guild_id, inviter_id, uses, max_uses, is_vanity, created_at):
    client = await get_client()
    await client.table("invites").upsert({
        "invite_code": invite_code,
        "guild_id": guild_id,
        "inviter_id": inviter_id,
        "uses": uses,
        "max_uses": max_uses,
        "is_vanity": is_vanity,
        "created_at": created_at.isoformat() if created_at else None,
    }, on_conflict="invite_code").execute()


async def delete_invite(*, invite_code):
    client = await get_client()
    await client.table("invites").delete().eq("invite_code", invite_code).execute()


async def get_cached_invite_uses(*, guild_id):
    """Returns {invite_code: uses} for every invite currently cached for this guild."""
    client = await get_client()
    res = await client.table("invites").select("invite_code, uses").eq("guild_id", guild_id).execute()
    return {row["invite_code"]: row["uses"] for row in res.data}


async def record_invite_use(*, guild_id, invite_code, inviter_id, member_id, joined_at):
    client = await get_client()
    await client.table("invite_uses").insert({
        "guild_id": guild_id,
        "invite_code": invite_code,
        "inviter_id": inviter_id,
        "member_id": member_id,
        "joined_at": joined_at.isoformat(),
    }).execute()


# ---------------------------------------------------------------------------
# command_usage
# ---------------------------------------------------------------------------

async def record_command_usage(*, guild_id, user_id, command_name, used_at):
    client = await get_client()
    await client.table("command_usage").insert({
        "guild_id": guild_id,
        "user_id": user_id,
        "command_name": command_name,
        "used_at": used_at.isoformat(),
    }).execute()


# ---------------------------------------------------------------------------
# boosts
# ---------------------------------------------------------------------------

async def start_boost(*, guild_id, user_id, boosted_at):
    client = await get_client()
    await client.table("boosts").insert({
        "guild_id": guild_id,
        "user_id": user_id,
        "boosted_at": boosted_at.isoformat(),
    }).execute()


async def end_boost(*, guild_id, user_id, unboosted_at):
    client = await get_client()
    await (
        client.table("boosts")
        .update({"unboosted_at": unboosted_at.isoformat()})
        .eq("guild_id", guild_id)
        .eq("user_id", user_id)
        .is_("unboosted_at", "null")
        .execute()
    )


async def get_open_boost_user_ids(*, guild_id):
    client = await get_client()
    res = (
        await client.table("boosts")
        .select("user_id")
        .eq("guild_id", guild_id)
        .is_("unboosted_at", "null")
        .execute()
    )
    return {row["user_id"] for row in res.data}


# ---------------------------------------------------------------------------
# Lecture — utilisé par cogs/statcommands.py (/serverstat /userstat /channelstat)
# ---------------------------------------------------------------------------

# PostgREST (Supabase) tronque silencieusement toute réponse à 1000 lignes par
# défaut : les lectures de lignes brutes doivent donc paginer, sinon tout ce qui
# dépasse (ex. un salon avec 2000+ messages importés par /initialize) est perdu
# à la lecture. Le tri sur la clé primaire rend la pagination stable.
_PAGE_SIZE = 1000


async def get_messages(*, guild_id, window_start, window_end, channel_id=None, user_id=None):
    """Messages bruts (channel_id, user_id, created_at) sur la fenêtre — agrégés côté Python."""
    client = await get_client()
    rows = []
    offset = 0
    while True:
        query = (
            client.table("messages")
            .select("channel_id, user_id, created_at")
            .eq("guild_id", guild_id)
            .gte("created_at", window_start.isoformat())
            .lt("created_at", window_end.isoformat())
        )
        if channel_id is not None:
            query = query.eq("channel_id", channel_id)
        if user_id is not None:
            query = query.eq("user_id", user_id)
        res = await query.order("message_id").range(offset, offset + _PAGE_SIZE - 1).execute()
        rows.extend(res.data)
        if len(res.data) < _PAGE_SIZE:
            return rows
        offset += _PAGE_SIZE


async def get_voice_sessions_overlapping(*, guild_id, window_start, window_end, channel_id=None, user_id=None):
    """Sessions vocales brutes qui chevauchent la fenêtre (pour un bucketing par jour côté Python)."""
    client = await get_client()
    rows = []
    offset = 0
    while True:
        query = (
            client.table("voice_sessions")
            .select("channel_id, user_id, joined_at, left_at")
            .eq("guild_id", guild_id)
            .lt("joined_at", window_end.isoformat())
            .or_(f"left_at.is.null,left_at.gte.{window_start.isoformat()}")
        )
        if channel_id is not None:
            query = query.eq("channel_id", channel_id)
        if user_id is not None:
            query = query.eq("user_id", user_id)
        res = await query.order("session_id").range(offset, offset + _PAGE_SIZE - 1).execute()
        rows.extend(res.data)
        if len(res.data) < _PAGE_SIZE:
            return rows
        offset += _PAGE_SIZE


async def get_voice_seconds_breakdown(*, guild_id, window_start, window_end, user_id=None, channel_id=None):
    """Appelle voice_seconds_breakdown(...) — durée vocale clampée, groupée par (user_id, channel_id)."""
    client = await get_client()
    res = await client.rpc("voice_seconds_breakdown", {
        "p_guild_id": guild_id,
        "p_window_start": window_start.isoformat(),
        "p_window_end": window_end.isoformat(),
        "p_user_id": user_id,
        "p_channel_id": channel_id,
    }).execute()
    return res.data


async def get_emoji_events(*, guild_id, window_start, window_end, source=None, user_id=None):
    client = await get_client()
    rows = []
    offset = 0
    while True:
        query = (
            client.table("emoji_events")
            .select("emoji_id, emoji_name, is_custom, is_animated, user_id, source")
            .eq("guild_id", guild_id)
            .gte("created_at", window_start.isoformat())
            .lt("created_at", window_end.isoformat())
        )
        if source is not None:
            query = query.eq("source", source)
        if user_id is not None:
            query = query.eq("user_id", user_id)
        res = await query.order("event_id").range(offset, offset + _PAGE_SIZE - 1).execute()
        rows.extend(res.data)
        if len(res.data) < _PAGE_SIZE:
            return rows
        offset += _PAGE_SIZE


async def get_distinct_message_days(*, guild_id, user_id):
    """Appelle distinct_message_days(...) — jours (Europe/Paris) avec >=1 message, triés du plus récent au plus ancien."""
    client = await get_client()
    res = await client.rpc("distinct_message_days", {"p_guild_id": guild_id, "p_user_id": user_id}).execute()
    return [row["day"] for row in res.data]


async def get_member(*, guild_id, user_id):
    client = await get_client()
    res = (
        await client.table("members")
        .select("first_message_at, guild_joined_at, last_activity_at")
        .eq("guild_id", guild_id)
        .eq("user_id", user_id)
        .execute()
    )
    return res.data[0] if res.data else None
