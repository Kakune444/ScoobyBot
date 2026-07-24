import asyncio

import discord
import yt_dlp
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

FFMPEG_OPTIONS = {
    "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
    "options": "-vn",
}

ytdl = yt_dlp.YoutubeDL(YTDL_OPTIONS)


class Music(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.queues = {}

    def _queue(self, guild_id):
        return self.queues.setdefault(guild_id, [])

    async def _extract(self, query):
        loop = asyncio.get_running_loop()
        data = await loop.run_in_executor(None, lambda: ytdl.extract_info(query, download=False))
        if "entries" in data:
            data = data["entries"][0]
        return {"title": data.get("title", "Titre inconnu"), "url": data["url"]}

    async def _play_next(self, ctx):
        queue = self._queue(ctx.guild.id)
        if not queue or ctx.voice_client is None:
            return

        track = queue.pop(0)
        source = discord.FFmpegPCMAudio(track["url"], **FFMPEG_OPTIONS)

        def after(error):
            if error:
                print(f"Erreur de lecture : {error}")
            fut = asyncio.run_coroutine_threadsafe(self._play_next(ctx), self.bot.loop)
            try:
                fut.result()
            except Exception as e:
                print(f"Erreur après lecture : {e}")

        ctx.voice_client.play(source, after=after)
        await ctx.send(f"▶️ Lecture : **{track['title']}**\n💬 *{scooby_quote()}*")

    @commands.command(name="join")
    async def join(self, ctx):
        if ctx.author.voice is None:
            await ctx.send("❌ Tu dois être dans un salon vocal.")
            return

        channel = ctx.author.voice.channel
        if ctx.voice_client is not None:
            await ctx.voice_client.move_to(channel)
        else:
            await channel.connect()
        await ctx.send(f"✅ Rejoint **{channel.name}**.\n💬 *{scooby_quote()}*")

    @commands.command(name="play")
    async def play(self, ctx, *, recherche: str):
        if ctx.voice_client is None:
            if ctx.author.voice is None:
                await ctx.send("❌ Tu dois être dans un salon vocal.")
                return
            await ctx.author.voice.channel.connect()

        async with ctx.typing():
            try:
                track = await self._extract(recherche)
            except Exception as e:
                await ctx.send(f"❌ Impossible de trouver ce morceau : {e}")
                return

        queue = self._queue(ctx.guild.id)
        queue.append(track)

        if ctx.voice_client.is_playing() or ctx.voice_client.is_paused():
            await ctx.send(f"➕ Ajouté à la file : **{track['title']}**\n💬 *{scooby_quote()}*")
        else:
            await self._play_next(ctx)

    @commands.command(name="queue")
    async def queue_cmd(self, ctx):
        queue = self._queue(ctx.guild.id)
        if not queue:
            await ctx.send("La file d'attente est vide.")
            return

        lines = [f"{i + 1}. {t['title']}" for i, t in enumerate(queue)]
        embed = discord.Embed(title="File d'attente", description="\n".join(lines), color=discord.Color.green())
        await ctx.send(embed=embed)

    @commands.command(name="skip")
    async def skip(self, ctx):
        if ctx.voice_client is None or not ctx.voice_client.is_playing():
            await ctx.send("❌ Rien n'est en cours de lecture.")
            return
        ctx.voice_client.stop()
        await ctx.send(f"⏭️ Morceau suivant.\n💬 *{scooby_quote()}*")

    @commands.command(name="pause")
    async def pause(self, ctx):
        if ctx.voice_client is None or not ctx.voice_client.is_playing():
            await ctx.send("❌ Rien n'est en cours de lecture.")
            return
        ctx.voice_client.pause()
        await ctx.send(f"⏸️ Lecture en pause.\n💬 *{scooby_quote()}*")

    @commands.command(name="resume")
    async def resume(self, ctx):
        if ctx.voice_client is None or not ctx.voice_client.is_paused():
            await ctx.send("❌ Rien n'est en pause.")
            return
        ctx.voice_client.resume()
        await ctx.send(f"▶️ Reprise de la lecture.\n💬 *{scooby_quote()}*")

    @commands.command(name="leave")
    async def leave(self, ctx):
        if ctx.voice_client is None:
            await ctx.send("❌ Je ne suis pas connecté à un salon vocal.")
            return
        self._queue(ctx.guild.id).clear()
        await ctx.voice_client.disconnect()
        await ctx.send(f"👋 Déconnecté du vocal.\n💬 *{scooby_quote()}*")


async def setup(bot):
    await bot.add_cog(Music(bot))
