import hashlib
import re
from collections import defaultdict, deque

import discord
from discord import app_commands
from discord.ext import commands

from cogs.scooby_quotes import scooby_quote
from supabase_client import award_coins, close_voice_session, fire_and_forget, get_coin_balance


MESSAGE_REWARD = 0.5
VOICE_COINS_PER_HOUR = 5
SPAM_WINDOW_SECONDS = 10
SPAM_MAX_MESSAGES = 5
DUPLICATE_WINDOW_SECONDS = 30


def _format_coins(amount: float) -> str:
    return f"{amount:.2f}".rstrip("0").rstrip(".")


class Economy(commands.Cog):
    """Crédite les membres pour leur activité et expose leur solde."""

    def __init__(self, bot):
        self.bot = bot
        self._recent_messages: dict[tuple[int, int], deque[tuple[float, str]]] = defaultdict(deque)

    def _starts_bot_command(self, message: discord.Message) -> bool:
        if self.bot.user is None:
            return False
        content = message.content.lstrip()
        return content.startswith((f"<@{self.bot.user.id}>", f"<@!{self.bot.user.id}>"))

    def _is_rewardable_message(self, message: discord.Message) -> bool:
        if message.author.bot or message.guild is None or self._starts_bot_command(message):
            return False

        content = re.sub(r"\s+", " ", message.content.strip().lower())
        if not content and not message.attachments:
            return False
        if not content:
            content = f"[attachment:{len(message.attachments)}]"

        now = discord.utils.utcnow().timestamp()
        key = (message.guild.id, message.author.id)
        history = self._recent_messages[key]
        while history and history[0][0] < now - DUPLICATE_WINDOW_SECONDS:
            history.popleft()

        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        is_duplicate = any(previous_hash == content_hash for _, previous_hash in history)
        recent_count = sum(timestamp >= now - SPAM_WINDOW_SECONDS for timestamp, _ in history)
        history.append((now, content_hash))

        return not is_duplicate and recent_count < SPAM_MAX_MESSAGES

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if not self._is_rewardable_message(message):
            return

        fire_and_forget(award_coins(
            guild_id=message.guild.id,
            user_id=message.author.id,
            amount=MESSAGE_REWARD,
            reason="message",
            source_id=message.id,
        ))

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ):
        if member.bot or before.channel == after.channel or before.channel is None:
            return

        # La session est enregistrée par Stats ; Economy la clôture et la crédite
        # atomiquement, une seule fois, au prorata du temps passé.
        fire_and_forget(close_voice_session(
            guild_id=member.guild.id,
            user_id=member.id,
            channel_id=before.channel.id,
            left_at=discord.utils.utcnow(),
            coins_per_hour=VOICE_COINS_PER_HOUR,
        ))

    @app_commands.command(name="balance", description="Afficher ton solde de coins")
    @app_commands.guild_only()
    async def balance(self, interaction: discord.Interaction):
        amount = await get_coin_balance(
            guild_id=interaction.guild.id,
            user_id=interaction.user.id,
        )
        await interaction.response.send_message(
            f"💰 {interaction.user.mention}, tu as **{_format_coins(amount)} coins**.\n"
            f"💬 *{scooby_quote()}*"
        )


async def setup(bot):
    await bot.add_cog(Economy(bot))
