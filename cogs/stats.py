import asyncio
import re

import discord
import emoji
from discord.ext import commands

from supabase_client import (
    close_voice_session,
    delete_invite,
    end_boost,
    fire_and_forget,
    get_cached_invite_uses,
    get_open_boost_user_ids,
    get_open_voice_sessions,
    insert_message,
    open_voice_session,
    record_command_usage,
    record_emoji_event,
    record_invite_use,
    start_boost,
    touch_member,
    upsert_invite,
)

CUSTOM_EMOJI_RE = re.compile(r"<(a?):(\w+):(\d+)>")


def _extract_emojis(content: str) -> list[dict]:
    entries = []
    for match in CUSTOM_EMOJI_RE.finditer(content):
        animated, name, emoji_id = match.groups()
        entries.append({
            "emoji_id": int(emoji_id),
            "emoji_name": name,
            "is_custom": True,
            "is_animated": bool(animated),
        })

    # Retire les emojis custom déjà trouvés avant le scan unicode, pour éviter
    # de matcher des caractères à l'intérieur de la syntaxe <a:nom:id>.
    stripped = CUSTOM_EMOJI_RE.sub("", content)
    for match in emoji.emoji_list(stripped):
        entries.append({
            "emoji_id": None,
            "emoji_name": match["emoji"],
            "is_custom": False,
            "is_animated": False,
        })

    return entries


class Stats(commands.Cog):
    """Capture les événements (messages, vocal, réactions, invitations, boosts,
    commandes) et les écrit dans Supabase. Aucune commande ici — la lecture des
    stats se fait dans cogs/statcommands.py."""

    def __init__(self, bot):
        self.bot = bot
        self._invite_locks: dict[int, asyncio.Lock] = {}

    def _invite_lock(self, guild_id: int) -> asyncio.Lock:
        return self._invite_locks.setdefault(guild_id, asyncio.Lock())

    # -- messages -----------------------------------------------------------

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or message.guild is None:
            return

        now = message.created_at
        fire_and_forget(insert_message(
            message_id=message.id,
            guild_id=message.guild.id,
            channel_id=message.channel.id,
            user_id=message.author.id,
            created_at=now,
        ))
        fire_and_forget(touch_member(
            guild_id=message.guild.id,
            user_id=message.author.id,
            activity_at=now,
            is_message=True,
        ))

        for entry in _extract_emojis(message.content):
            fire_and_forget(record_emoji_event(
                guild_id=message.guild.id,
                channel_id=message.channel.id,
                message_id=message.id,
                user_id=message.author.id,
                source="message",
                created_at=now,
                **entry,
            ))

    # -- réactions ------------------------------------------------------------

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        if payload.guild_id is None or payload.member is None or payload.member.bot:
            return

        now = discord.utils.utcnow()
        reaction_emoji = payload.emoji
        fire_and_forget(record_emoji_event(
            guild_id=payload.guild_id,
            channel_id=payload.channel_id,
            message_id=payload.message_id,
            user_id=payload.user_id,
            source="reaction",
            emoji_id=reaction_emoji.id,
            emoji_name=reaction_emoji.name,
            is_custom=reaction_emoji.id is not None,
            is_animated=reaction_emoji.animated,
            created_at=now,
        ))
        fire_and_forget(touch_member(guild_id=payload.guild_id, user_id=payload.user_id, activity_at=now))

    # -- vocal ----------------------------------------------------------------

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
        if member.bot or before.channel == after.channel:
            return

        now = discord.utils.utcnow()
        if before.channel is not None:
            fire_and_forget(close_voice_session(
                guild_id=member.guild.id, user_id=member.id, channel_id=before.channel.id, left_at=now,
            ))
        if after.channel is not None:
            fire_and_forget(open_voice_session(
                guild_id=member.guild.id, user_id=member.id, channel_id=after.channel.id, joined_at=now,
            ))
            fire_and_forget(touch_member(guild_id=member.guild.id, user_id=member.id, activity_at=now))

    # -- membres / invitations --------------------------------------------------

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        now = discord.utils.utcnow()
        fire_and_forget(touch_member(
            guild_id=member.guild.id,
            user_id=member.id,
            activity_at=now,
            guild_joined_at=member.joined_at or now,
        ))
        asyncio.create_task(self._attribute_invite(member))

    async def _attribute_invite(self, member: discord.Member):
        guild = member.guild
        if not guild.me.guild_permissions.manage_guild:
            return

        async with self._invite_lock(guild.id):
            try:
                current_invites = await guild.invites()
            except discord.Forbidden:
                return

            cached_uses = await get_cached_invite_uses(guild_id=guild.id)

            used_code = None
            used_inviter = None
            for invite in current_invites:
                prior = cached_uses.get(invite.code, 0)
                if invite.uses is not None and invite.uses > prior:
                    used_code = invite.code
                    used_inviter = invite.inviter.id if invite.inviter else None
                    break

            for invite in current_invites:
                fire_and_forget(upsert_invite(
                    invite_code=invite.code,
                    guild_id=guild.id,
                    inviter_id=invite.inviter.id if invite.inviter else None,
                    uses=invite.uses or 0,
                    max_uses=invite.max_uses,
                    is_vanity=False,
                    created_at=invite.created_at,
                ))

            fire_and_forget(record_invite_use(
                guild_id=guild.id,
                invite_code=used_code,
                inviter_id=used_inviter,
                member_id=member.id,
                joined_at=discord.utils.utcnow(),
            ))

    @commands.Cog.listener()
    async def on_invite_create(self, invite: discord.Invite):
        fire_and_forget(upsert_invite(
            invite_code=invite.code,
            guild_id=invite.guild.id,
            inviter_id=invite.inviter.id if invite.inviter else None,
            uses=invite.uses or 0,
            max_uses=invite.max_uses,
            is_vanity=False,
            created_at=invite.created_at,
        ))

    @commands.Cog.listener()
    async def on_invite_delete(self, invite: discord.Invite):
        fire_and_forget(delete_invite(invite_code=invite.code))

    # -- boosts -----------------------------------------------------------------

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        if before.premium_since == after.premium_since:
            return

        if after.premium_since is not None and before.premium_since is None:
            fire_and_forget(start_boost(guild_id=after.guild.id, user_id=after.id, boosted_at=after.premium_since))
        elif after.premium_since is None and before.premium_since is not None:
            fire_and_forget(end_boost(guild_id=after.guild.id, user_id=after.id, unboosted_at=discord.utils.utcnow()))

    # -- commandes ----------------------------------------------------------------

    async def log_command_usage(self, interaction: discord.Interaction):
        command = interaction.command
        if command is None:
            return
        fire_and_forget(record_command_usage(
            guild_id=interaction.guild_id,
            user_id=interaction.user.id,
            command_name=command.qualified_name,
            used_at=discord.utils.utcnow(),
        ))

    # -- réconciliation au démarrage ----------------------------------------------

    @commands.Cog.listener()
    async def on_ready(self):
        for guild in self.bot.guilds:
            asyncio.create_task(self._reconcile_guild(guild))

    async def _reconcile_guild(self, guild: discord.Guild):
        now = discord.utils.utcnow()

        currently_connected = {
            (channel.id, member.id)
            for channel in guild.voice_channels
            for member in channel.members
            if not member.bot
        }
        try:
            open_sessions = await get_open_voice_sessions(guild_id=guild.id)
        except Exception as e:
            print(f"Erreur de réconciliation vocale ({guild.id}) : {e}")
            open_sessions = []
        db_open = {(row["channel_id"], row["user_id"]) for row in open_sessions}

        # Des sessions restées ouvertes en base alors que plus personne n'est
        # connecté (ex. le bot a redémarré pendant que quelqu'un était en
        # vocal) : on les clôture pour ne pas accumuler des heures fantômes.
        for channel_id, user_id in db_open - currently_connected:
            fire_and_forget(close_voice_session(guild_id=guild.id, user_id=user_id, channel_id=channel_id, left_at=now))
        # À l'inverse, des membres déjà connectés sans session ouverte en base.
        for channel_id, user_id in currently_connected - db_open:
            fire_and_forget(open_voice_session(guild_id=guild.id, user_id=user_id, channel_id=channel_id, joined_at=now))

        if guild.me.guild_permissions.manage_guild:
            try:
                for invite in await guild.invites():
                    fire_and_forget(upsert_invite(
                        invite_code=invite.code,
                        guild_id=guild.id,
                        inviter_id=invite.inviter.id if invite.inviter else None,
                        uses=invite.uses or 0,
                        max_uses=invite.max_uses,
                        is_vanity=False,
                        created_at=invite.created_at,
                    ))
            except discord.Forbidden:
                pass

        try:
            open_boost_ids = await get_open_boost_user_ids(guild_id=guild.id)
        except Exception as e:
            print(f"Erreur de réconciliation des boosts ({guild.id}) : {e}")
            open_boost_ids = set()
        for member in guild.premium_subscribers:
            if member.id not in open_boost_ids:
                fire_and_forget(start_boost(guild_id=guild.id, user_id=member.id, boosted_at=member.premium_since or now))


async def setup(bot):
    await bot.add_cog(Stats(bot))
