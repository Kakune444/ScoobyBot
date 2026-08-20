import asyncio
import base64
import os
import tempfile

import discord
import yt_dlp
from discord import app_commands
from discord.ext import commands

from cogs.scooby_quotes import scooby_quote

YTDL_OPTIONS = {
    "format": "bestaudio/best",
    "noplaylist": True,
    "default_search": "ytsearch",
    "quiet": True,
    "nocheckcertificate": True,
    "ignoreerrors": False,
}


def _configure_youtube_auth():
    """Configure an optional YouTube cookie file without storing it in the repo."""
    cookie_file = os.getenv("YOUTUBE_COOKIES_FILE")
    encoded_cookies = os.getenv("YOUTUBE_COOKIES_B64")

    if encoded_cookies:
        try:
            cookie_data = base64.b64decode("".join(encoded_cookies.split()), validate=True)
            if not cookie_data.startswith((b"# Netscape HTTP Cookie File", b"# HTTP Cookie File")):
                raise ValueError("le fichier n'est pas au format Netscape attendu")

            temporary_cookie_file = tempfile.NamedTemporaryFile(
                mode="wb",
                prefix="scoobybot-youtube-",
                suffix=".txt",
                delete=False,
            )
            with temporary_cookie_file:
                temporary_cookie_file.write(cookie_data)
            os.chmod(temporary_cookie_file.name, 0o600)
            cookie_file = temporary_cookie_file.name
        except (ValueError, OSError) as error:
            print(f"Configuration YouTube invalide : {error}")
            return

    if cookie_file:
        YTDL_OPTIONS["cookiefile"] = cookie_file

    user_agent = os.getenv("YOUTUBE_USER_AGENT")
    if user_agent:
        YTDL_OPTIONS["http_headers"] = {"User-Agent": user_agent}


_configure_youtube_auth()

FFMPEG_OPTIONS = {
    "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
    "options": "-vn",
}

ytdl = yt_dlp.YoutubeDL(YTDL_OPTIONS)

INACTIVITY_TIMEOUT_SECONDS = 10 * 60


class Music(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.queues = {}
        self.inactivity_tasks = {}

    def cog_unload(self):
        for task in self.inactivity_tasks.values():
            task.cancel()
        self.inactivity_tasks.clear()

    def _queue(self, guild_id):
        return self.queues.setdefault(guild_id, [])

    def _cancel_inactivity_timer(self, guild_id):
        task = self.inactivity_tasks.pop(guild_id, None)
        if task is not None and not task.done():
            task.cancel()

    def _reset_inactivity_timer(self, guild: discord.Guild, channel: discord.abc.Messageable):
        self._cancel_inactivity_timer(guild.id)
        self.inactivity_tasks[guild.id] = asyncio.create_task(self._inactivity_watchdog(guild, channel))

    async def _inactivity_watchdog(self, guild: discord.Guild, channel: discord.abc.Messageable):
        try:
            await asyncio.sleep(INACTIVITY_TIMEOUT_SECONDS)
        except asyncio.CancelledError:
            return

        self.inactivity_tasks.pop(guild.id, None)
        voice_client = guild.voice_client
        if voice_client is None or (voice_client.is_playing() and not voice_client.is_paused()):
            return

        self._queue(guild.id).clear()
        await voice_client.disconnect()
        try:
            await channel.send(f"👋 Déconnecté du vocal après 10 minutes d'inactivité.\n💬 *{scooby_quote()}*")
        except discord.HTTPException:
            pass

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        if member.id != self.bot.user.id or after.channel is not None:
            return
        self._cancel_inactivity_timer(member.guild.id)
        self._queue(member.guild.id).clear()

    async def _extract(self, query):
        loop = asyncio.get_running_loop()
        data = await loop.run_in_executor(None, lambda: ytdl.extract_info(query, download=False))
        if "entries" in data:
            data = data["entries"][0]
        return {"title": data.get("title", "Titre inconnu"), "url": data["url"]}

    async def _play_next(self, guild: discord.Guild, channel: discord.abc.Messageable):
        queue = self._queue(guild.id)
        voice_client = guild.voice_client
        if voice_client is None:
            return
        if not queue:
            self._reset_inactivity_timer(guild, channel)
            return

        track = queue.pop(0)

        try:
            source = discord.FFmpegPCMAudio(track["url"], **FFMPEG_OPTIONS)
        except discord.ClientException as e:
            await channel.send(f"❌ Impossible de lancer **{track['title']}** : {e}")
            return

        def after(error):
            if error:
                print(f"Erreur de lecture : {error}")
                asyncio.run_coroutine_threadsafe(
                    channel.send(f"⚠️ La lecture de **{track['title']}** s'est interrompue : {error}"),
                    self.bot.loop,
                )

            fut = asyncio.run_coroutine_threadsafe(self._play_next(guild, channel), self.bot.loop)
            try:
                fut.result()
            except Exception as e:
                print(f"Erreur après lecture : {e}")
                asyncio.run_coroutine_threadsafe(
                    channel.send(f"❌ Erreur lors du passage au morceau suivant : {e}"),
                    self.bot.loop,
                )

        voice_client.play(source, after=after)
        self._cancel_inactivity_timer(guild.id)
        await channel.send(f"▶️ Lecture : **{track['title']}**\n💬 *{scooby_quote()}*")

    @app_commands.command(name="join", description="Faire rejoindre le bot dans un salon vocal")
    @app_commands.describe(channel="Le salon vocal à rejoindre (par défaut : ton salon vocal actuel)")
    @app_commands.guild_only()
    async def join(self, interaction: discord.Interaction, channel: discord.VoiceChannel = None):
        if channel is None:
            if interaction.user.voice is None:
                await interaction.response.send_message(
                    "❌ Tu dois être dans un salon vocal, ou préciser le paramètre `channel`.",
                    ephemeral=True,
                )
                return
            channel = interaction.user.voice.channel

        await interaction.response.defer()

        try:
            voice_client = interaction.guild.voice_client
            if voice_client is not None:
                await voice_client.move_to(channel)
            else:
                await channel.connect()
        except (discord.ClientException, discord.HTTPException, asyncio.TimeoutError) as e:
            await interaction.followup.send(f"❌ Impossible de rejoindre {channel.mention} : {e}")
            return

        voice_client = interaction.guild.voice_client
        if not voice_client.is_playing():
            self._reset_inactivity_timer(interaction.guild, interaction.channel)

        await interaction.followup.send(f"✅ Rejoint **{channel.name}**.\n💬 *{scooby_quote()}*")

    @app_commands.command(name="play", description="Jouer ou ajouter un morceau à la file d'attente")
    @app_commands.describe(recherche="Titre, artiste ou URL YouTube à jouer")
    @app_commands.guild_only()
    async def play(self, interaction: discord.Interaction, recherche: str):
        if interaction.guild.voice_client is None and interaction.user.voice is None:
            await interaction.response.send_message("❌ Tu dois être dans un salon vocal.", ephemeral=True)
            return

        await interaction.response.defer()

        if interaction.guild.voice_client is None:
            try:
                await interaction.user.voice.channel.connect()
            except (discord.ClientException, discord.HTTPException, asyncio.TimeoutError) as e:
                await interaction.followup.send(f"❌ Impossible de rejoindre ton salon vocal : {e}")
                return

        try:
            track = await self._extract(recherche)
        except Exception as e:
            await interaction.followup.send(f"❌ Impossible de trouver ce morceau : {e}")
            return

        queue = self._queue(interaction.guild.id)
        queue.append(track)

        voice_client = interaction.guild.voice_client
        if voice_client.is_playing() or voice_client.is_paused():
            await interaction.followup.send(f"➕ Ajouté à la file : **{track['title']}**\n💬 *{scooby_quote()}*")
        else:
            await self._play_next(interaction.guild, interaction.channel)
            try:
                await interaction.delete_original_response()
            except discord.HTTPException:
                pass

    @app_commands.command(name="queue", description="Afficher la file d'attente actuelle")
    @app_commands.guild_only()
    async def queue_cmd(self, interaction: discord.Interaction):
        queue = self._queue(interaction.guild.id)
        if not queue:
            await interaction.response.send_message("La file d'attente est vide.", ephemeral=True)
            return

        lines = [f"{i + 1}. {t['title']}" for i, t in enumerate(queue)]
        embed = discord.Embed(title="File d'attente", description="\n".join(lines), color=discord.Color.green())
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="skip", description="Passer au morceau suivant")
    @app_commands.guild_only()
    async def skip(self, interaction: discord.Interaction):
        voice_client = interaction.guild.voice_client
        if voice_client is None or not voice_client.is_playing():
            await interaction.response.send_message("❌ Rien n'est en cours de lecture.", ephemeral=True)
            return
        voice_client.stop()
        await interaction.response.send_message(f"⏭️ Morceau suivant.\n💬 *{scooby_quote()}*")

    @app_commands.command(name="pause", description="Mettre la lecture en pause")
    @app_commands.guild_only()
    async def pause(self, interaction: discord.Interaction):
        voice_client = interaction.guild.voice_client
        if voice_client is None or not voice_client.is_playing():
            await interaction.response.send_message("❌ Rien n'est en cours de lecture.", ephemeral=True)
            return
        voice_client.pause()
        self._reset_inactivity_timer(interaction.guild, interaction.channel)
        await interaction.response.send_message(f"⏸️ Lecture en pause.\n💬 *{scooby_quote()}*")

    @app_commands.command(name="resume", description="Reprendre la lecture en pause")
    @app_commands.guild_only()
    async def resume(self, interaction: discord.Interaction):
        voice_client = interaction.guild.voice_client
        if voice_client is None or not voice_client.is_paused():
            await interaction.response.send_message("❌ Rien n'est en pause.", ephemeral=True)
            return
        voice_client.resume()
        self._cancel_inactivity_timer(interaction.guild.id)
        await interaction.response.send_message(f"▶️ Reprise de la lecture.\n💬 *{scooby_quote()}*")

    @app_commands.command(name="leave", description="Déconnecter le bot du salon vocal")
    @app_commands.guild_only()
    async def leave(self, interaction: discord.Interaction):
        voice_client = interaction.guild.voice_client
        if voice_client is None:
            await interaction.response.send_message("❌ Je ne suis pas connecté à un salon vocal.", ephemeral=True)
            return
        self._cancel_inactivity_timer(interaction.guild.id)
        self._queue(interaction.guild.id).clear()
        await voice_client.disconnect()
        await interaction.response.send_message(f"👋 Déconnecté du vocal.\n💬 *{scooby_quote()}*")


async def setup(bot):
    await bot.add_cog(Music(bot))
