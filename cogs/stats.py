import asyncio
import csv
import io
import re
from datetime import datetime, timedelta, timezone
from typing import Union

import discord
import emoji
from discord import app_commands
from discord.ext import commands

from cogs.scooby_quotes import scooby_quote
from supabase_client import (
    bulk_insert_messages,
    bulk_insert_voice_sessions,
    delete_voice_sessions_ending_at,
    close_voice_session,
    delete_invite,
    end_boost,
    fire_and_forget,
    get_cached_invite_uses,
    get_open_boost_user_ids,
    get_open_voice_sessions,
    get_voice_seconds_breakdown,
    insert_completed_voice_session,
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
EPOCH = datetime(2015, 1, 1, tzinfo=timezone.utc)


def _format_duration(seconds: float) -> str:
    seconds = int(seconds)
    hours, remainder = divmod(seconds, 3600)
    minutes = remainder // 60
    if hours:
        return f"{hours}h{minutes:02d}"
    return f"{minutes}min"


def _parse_statbot_csv(data: bytes) -> dict[int, float]:
    """CSV export Statbot (colonnes rank, name/username, id, count) → {id: count}.
    Seules les colonnes id et count sont lues : l'encodage souvent cabossé des
    noms avec emojis n'a aucun impact."""
    text = data.decode("utf-8-sig", errors="replace")
    totals: dict[int, float] = {}
    for row in csv.DictReader(io.StringIO(text)):
        try:
            entry_id = int(row["id"])
            count = float(row["count"])
        except (KeyError, TypeError, ValueError):
            continue
        totals[entry_id] = totals.get(entry_id, 0.0) + count
    return totals


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
    commandes) et les écrit dans Supabase. La lecture des stats se fait dans
    cogs/statcommands.py ; les deux commandes ici (/initialize, /addtime) sont
    des écritures manuelles (backfill / correction), pas de la lecture."""

    def __init__(self, bot):
        self.bot = bot
        self._invite_locks: dict[int, asyncio.Lock] = {}
        self._initializing_guilds: set[int] = set()

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

    # -- commandes manuelles (admin) -----------------------------------------------

    async def _import_history(self, interaction: discord.Interaction, channels: list, scope_label: str):
        """Scanne l'historique des salons donnés et importe les messages dans
        Supabase. Idempotent (message_id en clé primaire) : relançable sans
        jamais créer de doublon. Un seul import à la fois par serveur."""
        guild = interaction.guild
        if guild.id in self._initializing_guilds:
            await interaction.response.send_message("⏳ Un import est déjà en cours sur ce serveur.", ephemeral=True)
            return

        self._initializing_guilds.add(guild.id)
        try:
            cutoff = discord.utils.utcnow()

            await interaction.response.defer()
            status = await interaction.followup.send(
                f"🔎 Import de l'historique des messages sur {scope_label}... "
                "Ça peut prendre plusieurs minutes, merci de patienter.\n"
                "-# Chaque message n'est compté qu'une fois, même en relançant la commande plus tard."
            )

            try:
                total = 0
                buffer = []
                channels_done = 0

                for ch in channels:
                    perms = ch.permissions_for(guild.me)
                    if perms.view_channel and perms.read_message_history:
                        try:
                            async for message in ch.history(limit=None, before=cutoff, oldest_first=True):
                                if message.author.bot:
                                    continue
                                buffer.append({
                                    "message_id": message.id,
                                    "guild_id": guild.id,
                                    "channel_id": ch.id,
                                    "user_id": message.author.id,
                                    "created_at": message.created_at,
                                })
                                if len(buffer) >= 500:
                                    total += await bulk_insert_messages(buffer)
                                    buffer = []
                        except (discord.Forbidden, discord.HTTPException):
                            pass

                    channels_done += 1
                    if len(channels) > 1 and (channels_done % 3 == 0 or channels_done == len(channels)):
                        await status.edit(content=f"🔎 Import en cours... {channels_done}/{len(channels)} salons traités, {total} messages traités jusqu'ici.")

                if buffer:
                    total += await bulk_insert_messages(buffer)

                await status.edit(content=f"✅ Import terminé : {total} messages traités sur {scope_label}.\n💬 *{scooby_quote()}*")
            except Exception as e:
                await status.edit(content=f"❌ L'import a échoué en cours de route : {e}")
        finally:
            self._initializing_guilds.discard(guild.id)

    @app_commands.command(name="initialize", description="Importer l'historique des messages d'un salon dans les statistiques")
    @app_commands.describe(channel="Le salon à scanner (texte, vocal ou fil)")
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    async def initialize(
        self,
        interaction: discord.Interaction,
        channel: Union[discord.TextChannel, discord.VoiceChannel, discord.Thread],
    ):
        await self._import_history(interaction, [channel], f"le salon {channel.mention}")

    @app_commands.command(name="initializeall", description="Importer l'historique des messages de tous les salons du serveur")
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    async def initializeall(self, interaction: discord.Interaction):
        guild = interaction.guild
        # Tout ce qui contient des messages : salons texte (annonces comprises),
        # chat des salons vocaux et des stages, et fils actuellement actifs.
        # Limite connue : les fils archivés ne sont pas parcourus.
        channels = [*guild.text_channels, *guild.voice_channels, *guild.stage_channels, *guild.threads]
        await self._import_history(interaction, channels, f"{len(channels)} salon(s)")

    @app_commands.command(name="addtime", description="Ajouter manuellement du temps vocal à un membre (rattrapage / correction)")
    @app_commands.describe(salon="Le salon vocal concerné", membre="Le membre à créditer", minutes="Le nombre de minutes à ajouter")
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    async def addtime(
        self,
        interaction: discord.Interaction,
        salon: discord.VoiceChannel,
        membre: discord.Member,
        minutes: app_commands.Range[int, 1, 100000],
    ):
        guild = interaction.guild
        now = discord.utils.utcnow()
        joined_at = now - timedelta(minutes=minutes)
        await interaction.response.defer()

        await insert_completed_voice_session(
            guild_id=guild.id, user_id=membre.id, channel_id=salon.id, joined_at=joined_at, left_at=now,
        )
        fire_and_forget(touch_member(guild_id=guild.id, user_id=membre.id, activity_at=now))

        rows = await get_voice_seconds_breakdown(guild_id=guild.id, window_start=EPOCH, window_end=now, user_id=membre.id)
        total_membre = sum(r["seconds"] for r in rows)
        total_salon = sum(r["seconds"] for r in rows if r["channel_id"] == salon.id)

        await interaction.followup.send(
            f"✅ {minutes} min ajoutées à {membre.mention} dans {salon.mention}.\n"
            f"Total de {membre.mention} dans ce salon (Tout) : **{_format_duration(total_salon)}**\n"
            f"Total de {membre.mention} tous salons (Tout) : **{_format_duration(total_membre)}**\n"
            f"💬 *{scooby_quote()}*"
        )

    @app_commands.command(name="importvoice", description="Importer des totaux d'heures vocales depuis des CSV Statbot (membres + salons)")
    @app_commands.describe(
        membres="CSV des heures vocales par membre (colonnes rank, username, id, count — count en heures)",
        salons="CSV des heures vocales par salon (colonnes rank, name, id, count — count en heures)",
    )
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    async def importvoice(self, interaction: discord.Interaction, membres: discord.Attachment, salons: discord.Attachment):
        guild = interaction.guild
        await interaction.response.defer()

        try:
            member_hours = _parse_statbot_csv(await membres.read())
            channel_hours = _parse_statbot_csv(await salons.read())
        except Exception as e:
            await interaction.followup.send(f"❌ Impossible de lire les CSV : {e}")
            return
        if not member_hours or not channel_hours:
            await interaction.followup.send(
                "❌ Aucune ligne exploitable trouvée — vérifie que les fichiers sont bien "
                "les exports Statbot avec les colonnes `rank,name/username,id,count`."
            )
            return

        total_member_hours = sum(member_hours.values())
        total_channel_hours = sum(channel_hours.values())

        # Le CSV ne donne que les totaux par membre ET par salon, jamais le
        # croisement membre×salon : on répartit les heures de chaque membre au
        # prorata du poids de chaque salon. Les deux totaux (par membre et par
        # salon) restent exacts ; seul le croisement est une estimation.
        #
        # Toutes les sessions synthétiques se terminent au même instant, placé
        # 31 jours AVANT l'arrivée du bot : comme les fenêtres 7j/14j/30j
        # reculent d'au plus 30 jours depuis maintenant (et « maintenant »
        # avance), elles ne peuvent jamais y entrer — cet historique ne compte
        # que dans « Tout ». Ce left_at commun et stable (dérivé de joined_at,
        # pas de l'heure d'exécution) sert aussi de marqueur pour rendre
        # l'import relançable sans doublon.
        anchor = guild.me.joined_at or discord.utils.utcnow()
        end = anchor - timedelta(days=31)
        await delete_voice_sessions_ending_at(guild_id=guild.id, left_at=end)

        rows = []
        for user_id, hours in member_hours.items():
            for channel_id, channel_weight in channel_hours.items():
                seconds = hours * 3600 * (channel_weight / total_channel_hours)
                if seconds < 1:
                    continue
                rows.append({
                    "guild_id": guild.id,
                    "user_id": user_id,
                    "channel_id": channel_id,
                    "joined_at": end - timedelta(seconds=seconds),
                    "left_at": end,
                })
        inserted = await bulk_insert_voice_sessions(rows)

        gap_warning = ""
        if total_channel_hours and abs(total_member_hours - total_channel_hours) / total_channel_hours > 0.02:
            gap_warning = (
                f"\n⚠️ Les deux CSV ne totalisent pas pareil "
                f"({_format_duration(total_member_hours * 3600)} côté membres vs "
                f"{_format_duration(total_channel_hours * 3600)} côté salons) — "
                "vérifie qu'ils viennent du même export."
            )

        await interaction.followup.send(
            f"✅ Import vocal terminé : **{_format_duration(total_member_hours * 3600)}** répartis sur "
            f"{len(member_hours)} membres et {len(channel_hours)} salons "
            f"({inserted} sessions synthétiques, antérieures au {end.strftime('%d/%m/%Y')}).\n"
            "-# Relancer la commande remplace le précédent import au lieu de s'y ajouter. "
            "Le croisement membre↔salon est estimé au prorata (le CSV ne le contient pas) ; "
            "les totaux par membre et par salon, eux, sont exacts."
            f"{gap_warning}\n💬 *{scooby_quote()}*"
        )

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
