import asyncio
import hashlib
import random
import re
import uuid
from collections import defaultdict, deque

import discord
from discord import app_commands
from discord.ext import commands

from cardkit import (
    collect_emojis,
    fetch_emoji_images,
    render_roulette_card,
    render_slots_card,
    to_discord_file,
)
from cogs.scooby_quotes import scooby_quote
from supabase_client import (
    award_coins,
    close_voice_session,
    fire_and_forget,
    get_coin_balance,
    play_roulette,
    play_slots,
)


MESSAGE_REWARD = 0.5
VOICE_COINS_PER_HOUR = 5
SPAM_WINDOW_SECONDS = 10
SPAM_MAX_MESSAGES = 5
DUPLICATE_WINDOW_SECONDS = 30

# Probabilités validées avant implémentation : les poids totalisent 100.
# Les gains sont des retours bruts (mise comprise), pas des gains nets.
SLOT_SYMBOLS = ("🍒", "🍋", "🍇", "🔔", "💎", "7️⃣")
SLOT_WEIGHTS = (30, 24, 18, 12, 10, 6)
SLOT_PAIR_MULTIPLIER = 1.6
SLOT_TRIPLE_MULTIPLIER = 4
SLOT_TRIPLE_DIAMOND_MULTIPLIER = 10
SLOT_TRIPLE_SEVEN_MULTIPLIER = 30
SLOT_TRIPLE_MULTIPLIERS = {
    "🍒": SLOT_TRIPLE_MULTIPLIER,
    "🍋": SLOT_TRIPLE_MULTIPLIER,
    "🍇": SLOT_TRIPLE_MULTIPLIER,
    "🔔": SLOT_TRIPLE_MULTIPLIER,
    "💎": SLOT_TRIPLE_DIAMOND_MULTIPLIER,
    "7️⃣": SLOT_TRIPLE_SEVEN_MULTIPLIER,
}
SLOT_BETS = (1, 5, 10, 100)
SLOT_ANIMATION_FRAMES = 9
SLOT_ANIMATION_INTERVAL = 0.30
_SLOT_RNG = random.SystemRandom()

# Roulette européenne (un seul zéro). Paiements : Plein 35:1, 1:1 pour
# Rouge/Noir/Pair/Impair/Manque/Passe, 2:1 pour Douzaine/Colonne.
ROULETTE_TYPES = {
    "plein": "Plein (numéro)",
    "rouge": "Rouge",
    "noir": "Noir",
    "pair": "Pair",
    "impair": "Impair",
    "manque": "Manque (1-18)",
    "passe": "Passe (19-36)",
    "douzaine": "Douzaine",
    "colonne": "Colonne",
}
ROULETTE_ODDS = {
    "plein": "35:1", "rouge": "1:1", "noir": "1:1", "pair": "1:1",
    "impair": "1:1", "manque": "1:1", "passe": "1:1",
    "douzaine": "2:1", "colonne": "2:1",
}
ROULETTE_RED_NUMBERS = {1, 3, 5, 7, 9, 12, 14, 16, 18, 19, 21, 23, 25, 27, 30, 32, 34, 36}
ROULETTE_MIN_BET = 1
ROULETTE_MAX_BET = 1000000


def _roulette_color(number: int) -> str:
    if number == 0:
        return "vert"
    return "rouge" if number in ROULETTE_RED_NUMBERS else "noir"


def _roulette_multiplier(bet_type: str, param: str | None, number: int) -> float:
    """Retour brut (mise comprise) : 36 / 2 / 3 / 0."""
    if bet_type == "plein":
        return 36.0 if param == str(number) else 0.0
    color = _roulette_color(number)
    if bet_type == "rouge":
        return 2.0 if color == "rouge" else 0.0
    if bet_type == "noir":
        return 2.0 if color == "noir" else 0.0
    if bet_type == "pair":
        return 2.0 if number != 0 and number % 2 == 0 else 0.0
    if bet_type == "impair":
        return 2.0 if number != 0 and number % 2 == 1 else 0.0
    if bet_type == "manque":
        return 2.0 if 1 <= number <= 18 else 0.0
    if bet_type == "passe":
        return 2.0 if 19 <= number <= 36 else 0.0
    if bet_type == "douzaine":
        ranges = {"1-12": range(1, 13), "13-24": range(13, 25), "25-36": range(25, 37)}
        return 3.0 if number in ranges.get(param, ()) else 0.0
    if bet_type == "colonne":
        col = {"1ere": 1, "2eme": 2, "3eme": 0}
        return 3.0 if number != 0 and number % 3 == col.get(param, -1) else 0.0
    return 0.0


def _roulette_bet_label(bet_type: str, param: str | None) -> str:
    if bet_type == "plein":
        return f"Plein n°{param}"
    if bet_type == "douzaine":
        return f"Douzaine {param}"
    if bet_type == "colonne":
        return f"Colonne {param}"
    return ROULETTE_TYPES[bet_type]


def _format_coins(amount: float) -> str:
    return f"{amount:.2f}".rstrip("0").rstrip(".")


def _random_slot_symbol() -> str:
    return _SLOT_RNG.choices(SLOT_SYMBOLS, weights=SLOT_WEIGHTS, k=1)[0]


def _spin_slot() -> list[str]:
    return [_random_slot_symbol() for _ in range(3)]


def _evaluate_slot(reels: list[str]) -> tuple[str, float]:
    """Retourne le type de résultat et le multiplicateur de retour brut."""
    if reels[0] == reels[1] == reels[2]:
        return f"triple_{reels[0]}", SLOT_TRIPLE_MULTIPLIERS[reels[0]]
    if reels[0] == reels[1] or reels[0] == reels[2] or reels[1] == reels[2]:
        return "pair", SLOT_PAIR_MULTIPLIER
    return "loss", 0


def _slot_result_text(result: str, reels: list[str], net: float) -> str:
    if result == "loss":
        return f"PERDU  •  -{_format_coins(abs(net))} coins"
    if result == "pair":
        return f"PAIRE  •  +{_format_coins(net)} coins"
    return f"TRIPLE {reels[0]}  •  +{_format_coins(net)} coins"


def _slot_animation_status(frame: int) -> str:
    if frame < 2:
        return "🎰 Les rouleaux tournent…"
    if frame < 5:
        return "Rouleau 1 arrêté…"
    if frame < 9:
        return "Rouleaux 1 et 2 arrêtés…"
    return "Résultat"


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

    @app_commands.command(name="slots", description="Jouer à la machine à sous")
    @app_commands.describe(mise="Mise fixe : 1, 5, 10 ou 100 coins")
    @app_commands.choices(
        mise=[app_commands.Choice(name=f"{bet} coins", value=bet) for bet in SLOT_BETS]
    )
    @app_commands.guild_only()
    async def slots(self, interaction: discord.Interaction, mise: app_commands.Choice[int]):
        """Joue un spin et applique le débit/crédit dans une RPC atomique."""
        bet = mise.value
        guild_id = interaction.guild.id
        user_id = interaction.user.id

        await interaction.response.defer()

        try:
            balance_before = await get_coin_balance(guild_id=guild_id, user_id=user_id)
        except Exception as error:
            print(f"Erreur Supabase (lecture solde slots) : {error}")
            await interaction.followup.send(
                "Impossible de vérifier ton solde pour le moment. Réessaie dans quelques instants.",
                ephemeral=True,
            )
            return

        if balance_before < bet:
            await interaction.followup.send(
                f"Solde insuffisant : il te faut **{_format_coins(bet)} coins** "
                f"et tu en as **{_format_coins(balance_before)}**.",
                ephemeral=True,
            )
            return

        reels = _spin_slot()
        result, multiplier = _evaluate_slot(reels)
        payout = round(bet * multiplier, 2)
        game_id = str(uuid.uuid4())

        try:
            outcome = await play_slots(
                game_id=game_id,
                guild_id=guild_id,
                user_id=user_id,
                bet=bet,
                reel_1=reels[0],
                reel_2=reels[1],
                reel_3=reels[2],
                result=result,
                payout=payout,
            )
        except Exception as error:
            if "INSUFFICIENT_COINS" in str(error):
                await interaction.followup.send(
                    "Ton solde a changé entre-temps : tu n'as plus assez de coins pour cette mise.",
                    ephemeral=True,
                )
                return
            print(f"Erreur Supabase (partie slots) : {error}")
            await interaction.followup.send(
                "La partie n'a pas pu être enregistrée. Aucun coin n'a été débité.",
                ephemeral=True,
            )
            return

        try:
            emoji_map = await fetch_emoji_images(collect_emojis(*SLOT_SYMBOLS, "🎰"))
        except Exception as error:
            # La partie est déjà enregistrée ; une panne du CDN ne doit pas
            # empêcher l'envoi de la card (les emojis seront simplement omis).
            print(f"Erreur Twemoji (animation slots) : {error}")
            emoji_map = {}
        animated_reels = _spin_slot()
        image = render_slots_card(
            reels=animated_reels,
            balance=balance_before,
            bet=bet,
            status="🎰 Les rouleaux tournent…",
            emoji_map=emoji_map,
        )
        message = await interaction.followup.send(
            file=to_discord_file(image, "slots.png"),
            wait=True,
        )

        # 0,6 s avant le premier arrêt, puis 0,9 s et enfin 1,2 s :
        # les rouleaux s'arrêtent progressivement sans mitrailler Discord.
        stop_frames = (2, 5, 9)
        for frame in range(1, SLOT_ANIMATION_FRAMES + 1):
            await asyncio.sleep(SLOT_ANIMATION_INTERVAL)
            animated_reels = [
                reels[index] if frame >= stop_frames[index] else _random_slot_symbol()
                for index in range(3)
            ]
            is_final = frame == SLOT_ANIMATION_FRAMES
            status = (
                _slot_result_text(result, reels, outcome["net"])
                if is_final
                else _slot_animation_status(frame)
            )
            image = render_slots_card(
                reels=animated_reels,
                balance=outcome["balance"] if is_final else balance_before,
                bet=bet,
                status=status,
                payout=outcome["payout"] if is_final else None,
                net=outcome["net"] if is_final else None,
                emoji_map=emoji_map,
            )
            try:
                await message.edit(
                    attachments=[to_discord_file(image, "slots.png")]
                )
            except discord.HTTPException as error:
                print(f"Erreur Discord (animation slots) : {error}")
                break

    @app_commands.command(name="eco", description="Aide sur l'économie du serveur")
    @app_commands.guild_only()
    async def eco(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="📊 Économie — ScoobyBot",
            description="Comment gagner des coins et les dépenser.",
            color=discord.Color.gold(),
        )
        embed.add_field(
            name="🎁 Gains",
            value=(
                "`💬 Message` — **0,5 coin** par message valide (anti-spam).\n"
                "`🎤 Vocal` — **5 coins/heure** au prorata du temps passé."
            ),
            inline=False,
        )
        embed.add_field(
            name="💰 Soldes",
            value="`/balance` — affiche ton solde de coins sur ce serveur.",
            inline=False,
        )
        embed.add_field(
            name="🎰 Machine à sous",
            value=(
                "`/slots [mise]` — mises : 1, 5, 10 ou 100 coins.\n"
                "• Paire ×1,6 · triple ×4 · 💎×10 · 7️⃣×30"
            ),
            inline=False,
        )
        embed.add_field(
            name="🎡 Roulette",
            value=(
                "`/roulette` — roulette européenne (RTP 97,30 %).\n"
                "• 1:1 (Rouge/Noir, Pair/Impair, Manque/Passe)\n"
                "• 2:1 (Douzaine, Colonne) · 35:1 (Plein)"
            ),
            inline=False,
        )
        embed.set_footer(text="Propulsé par ScoobyBot")
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="roulette", description="Jouer à la roulette européenne")
    @app_commands.describe(mise="Le montant de la mise (1 à 1 000 000 coins)")
    @app_commands.guild_only()
    async def roulette(self, interaction: discord.Interaction, mise: int):
        if mise < ROULETTE_MIN_BET or mise > ROULETTE_MAX_BET:
            await interaction.response.send_message(
                f"Mise entre **{ROULETTE_MIN_BET}** et **{ROULETTE_MAX_BET}** coins.",
                ephemeral=True,
            )
            return

        try:
            balance = await get_coin_balance(
                guild_id=interaction.guild.id,
                user_id=interaction.user.id,
            )
        except Exception as error:
            print(f"Erreur Supabase (solde roulette) : {error}")
            await interaction.response.send_message(
                "Impossible de vérifier ton solde pour le moment.",
                ephemeral=True,
            )
            return

        if mise > balance:
            await interaction.response.send_message(
                f"Solde insuffisant : il te faut **{_format_coins(mise)} coins** "
                f"et tu en as **{_format_coins(balance)}**.",
                ephemeral=True,
            )
            return

        try:
            emoji_map = await fetch_emoji_images(collect_emojis("🎡", "✅", "❌"))
        except Exception as error:
            print(f"Erreur Twemoji (roulette) : {error}")
            emoji_map = {}

        image = render_roulette_card(
            state="bet",
            balance=balance,
            bet=mise,
            bet_label="Choisis un type de mise",
            emoji_map=emoji_map,
        )
        view = _roulette_bet_view(interaction.user.id, mise)
        await interaction.response.send_message(
            file=to_discord_file(image, "roulette.png"),
            view=view,
        )

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

    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction):
        if interaction.type != discord.InteractionType.component:
            return
        custom_id = interaction.data.get("custom_id", "")
        if not custom_id.startswith("roulettebet:"):
            return
        parts = custom_id.split(":")
        if len(parts) != 4:
            return
        _, user_id, bet_type, amount = parts
        if int(user_id) != interaction.user.id:
            await interaction.response.send_message(
                "Cette roulette ne t'appartient pas.", ephemeral=True
            )
            return
        await interaction.response.send_modal(
            _RouletteModal(bet_type, interaction.user.id, int(amount))
        )


def _roulette_bet_view(user_id: int, amount: int) -> discord.ui.View:
    """Boutons de mise persistants sous l'image (pattern roles.py)."""
    labels = {
        "rouge": "🔴 Rouge", "noir": "⚫ Noir", "pair": "Pair", "impair": "Impair",
        "manque": "1-18", "passe": "19-36", "plein": "🎯 Plein",
    }
    view = discord.ui.View(timeout=None)
    for bet_type, label in labels.items():
        view.add_item(discord.ui.Button(
            style=discord.ButtonStyle.secondary,
            label=label,
            custom_id=f"roulettebet:{user_id}:{bet_type}:{amount}",
        ))
    for bet_type, label in (("douzaine", "Douzaine"), ("colonne", "Colonne")):
        view.add_item(discord.ui.Button(
            style=discord.ButtonStyle.primary,
            label=label,
            custom_id=f"roulettebet:{user_id}:{bet_type}:{amount}",
        ))
    return view


class _RouletteModal(discord.ui.Modal):
    """Modal : montant (pré-rempli, modifiable) + numéro/douzaine/colonne."""

    def __init__(self, bet_type: str, user_id: int, amount: int):
        super().__init__(title=f"{ROULETTE_TYPES[bet_type]} ({ROULETTE_ODDS[bet_type]})")
        self.bet_type = bet_type
        self.user_id = user_id
        self.amount_input = discord.ui.TextInput(
            label="Mise (coins)", min_length=1, max_length=10,
            default=str(amount),
            placeholder=f"{ROULETTE_MIN_BET} – {ROULETTE_MAX_BET}",
        )
        self.add_item(self.amount_input)
        if bet_type == "plein":
            self.number_input = discord.ui.TextInput(
                label="Numéro (0-36)", min_length=1, max_length=2, placeholder="0 à 36",
            )
            self.add_item(self.number_input)
        elif bet_type in ("douzaine", "colonne"):
            options = (
                [("1-12", "Douzaine 1 (1-12)"), ("13-24", "Douzaine 2 (13-24)"), ("25-36", "Douzaine 3 (25-36)")]
                if bet_type == "douzaine"
                else [("1ere", "Colonne 1"), ("2eme", "Colonne 2"), ("3eme", "Colonne 3")]
            )
            self.param_select = discord.ui.Select(
                placeholder="Choisis…",
                options=[discord.SelectOption(label=label, value=value) for value, label in options],
            )
            self.add_item(self.param_select)

    async def on_submit(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "Cette roulette ne t'appartient pas.", ephemeral=True
            )
            return

        try:
            amount = round(float(self.amount_input.value.replace(",", ".")), 2)
        except ValueError:
            await interaction.response.send_message("Montant invalide.", ephemeral=True)
            return
        if amount < ROULETTE_MIN_BET or amount > ROULETTE_MAX_BET:
            await interaction.response.send_message(
                f"Mise entre **{ROULETTE_MIN_BET}** et **{ROULETTE_MAX_BET}** coins.",
                ephemeral=True,
            )
            return

        bet_type = self.bet_type
        if bet_type == "plein":
            try:
                num = int(self.number_input.value)
            except ValueError:
                num = -1
            if num < 0 or num > 36:
                await interaction.response.send_message("Numéro invalide (0-36).", ephemeral=True)
                return
            param = str(num)
        elif bet_type in ("douzaine", "colonne"):
            param = self.param_select.values[0]
        else:
            param = None

        guild_id = interaction.guild.id
        user_id = self.user_id
        bet_label = _roulette_bet_label(bet_type, param)

        try:
            balance = await get_coin_balance(guild_id=guild_id, user_id=user_id)
        except Exception as error:
            print(f"Erreur Supabase (solde roulette) : {error}")
            await interaction.response.send_message(
                "Impossible de vérifier ton solde. Réessaie dans quelques instants.",
                ephemeral=True,
            )
            return

        if amount > balance:
            await interaction.response.send_message(
                f"Solde insuffisant : il te faut **{_format_coins(amount)} coins** "
                f"et tu en as **{_format_coins(balance)}**.",
                ephemeral=True,
            )
            return

        await interaction.response.defer()

        result_num = random.randint(0, 36)
        color = _roulette_color(result_num)
        multiplier = _roulette_multiplier(bet_type, param, result_num)
        payout = round(amount * multiplier, 2)
        game_id = str(uuid.uuid4())

        try:
            outcome = await play_roulette(
                game_id=game_id, guild_id=guild_id, user_id=user_id,
                bet_type=bet_type, bet_param=param, bet=amount,
                result_num=result_num, payout=payout,
            )
        except Exception as error:
            if "INSUFFICIENT_COINS" in str(error):
                await interaction.followup.send(
                    "Ton solde a changé entre-temps : pas assez de coins pour cette mise.",
                    ephemeral=True,
                )
                return
            print(f"Erreur Supabase (roulette) : {error}")
            await interaction.followup.send(
                "La partie n'a pas pu être enregistrée. Aucun coin débité.",
                ephemeral=True,
            )
            return

        try:
            emoji_map = await fetch_emoji_images(collect_emojis("🎡", "✅", "❌"))
        except Exception as error:
            print(f"Erreur Twemoji (roulette) : {error}")
            emoji_map = {}

        def _render(state: str, frame: int = 0, *, view=None) -> tuple:
            return to_discord_file(render_roulette_card(
                state=state, frame=frame,
                result_num=result_num if state == "land" else None,
                color_label=color if state == "land" else None,
                balance=outcome["balance"],
                bet=amount,
                bet_label=f"{bet_label} ({ROULETTE_ODDS[bet_type]})",
                emoji_map=emoji_map,
                payout=outcome["payout"] if state == "land" else None,
                net=outcome["net"] if state == "land" else None,
            ), "roulette.png")

        # State 2 : bille lancée (3 frames)
        for frame in range(3):
            await interaction.edit_original_response(
                content=None, attachments=[_render("spin", frame)], view=None,
            )
            await asyncio.sleep(0.45)

        # State 3 : atterrissage + gains (3 frames)
        for frame in range(3):
            await interaction.edit_original_response(
                content=None, attachments=[_render("land", frame)],
                view=None if frame < 2 else _roulette_bet_view(user_id, amount),
            )
            await asyncio.sleep(0.55)


async def setup(bot):
    await bot.add_cog(Economy(bot))
