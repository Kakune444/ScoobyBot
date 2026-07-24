import re

from discord.ext import commands

EXEMPT_USERNAME = "kakune."
MIN_LENGTH = 100
URL_RE = re.compile(r"https?://\S+")


class Blabla(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot or message.guild is None:
            return
        if message.author.name.lower() == EXEMPT_USERNAME:
            return

        text_without_links = URL_RE.sub("", message.content)
        if len(text_without_links) > MIN_LENGTH:
            reply = await message.channel.send("blablablabla 😴😴😴 RATIO")
            await reply.add_reaction("🔥")


async def setup(bot):
    await bot.add_cog(Blabla(bot))
