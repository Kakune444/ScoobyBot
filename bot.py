import asyncio
import os

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")

# TODO: remplace par l'ID de ton serveur de dev (clic droit sur le serveur > Copier l'ID,
# mode développeur à activer dans Discord > Paramètres > Avancés).
DEV_GUILD_ID = 873270256192335934
DEV_GUILD = discord.Object(id=DEV_GUILD_ID)

intents = discord.Intents.default()
intents.members = True
intents.message_content = True


class ScoobyCommandTree(app_commands.CommandTree):
    """Journalise chaque utilisation de commande slash dans Supabase (cogs.stats),
    avant même les vérifications de permission propres à chaque commande."""

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.type == discord.InteractionType.application_command:
            stats_cog = self.client.get_cog("Stats")
            if stats_cog is not None:
                await stats_cog.log_command_usage(interaction)
        return True


bot = commands.Bot(
    command_prefix=commands.when_mentioned,
    intents=intents,
    help_command=None,
    tree_cls=ScoobyCommandTree,
)

INITIAL_EXTENSIONS = (
    "cogs.moderation",
    "cogs.roles",
    "cogs.music",
    "cogs.economy",
    "cogs.stats",
    "cogs.statcommands",
    "cogs.blabla",
)


@bot.event
async def on_ready():
    print(f"Connecté en tant que {bot.user} (id: {bot.user.id})")

    try:
        # Copie les commandes globales vers le serveur de dev puis sync sur ce seul serveur :
        # propagation quasi instantanée, idéal pendant le développement.
        bot.tree.copy_global_to(guild=DEV_GUILD)
        synced = await bot.tree.sync(guild=DEV_GUILD)
        print(f"🔧 {len(synced)} commande(s) synchronisée(s) sur le serveur de dev {DEV_GUILD_ID}.")

        # Sync global pour la prod (propagation jusqu'à ~1h sur tous les serveurs) :
        # à décommenter quand les commandes sont stables et prêtes à être déployées partout.
        # synced_global = await bot.tree.sync()
        # print(f"🌍 {len(synced_global)} commande(s) synchronisée(s) globalement.")
    except Exception as e:
        print(f"❌ Erreur lors de la synchronisation des commandes : {e}")


@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.MissingPermissions):
        message = "❌ Tu n'as pas la permission d'utiliser cette commande."
    elif isinstance(error, app_commands.BotMissingPermissions):
        message = f"❌ Il me manque une permission pour faire ça : {', '.join(error.missing_permissions)}"
    elif isinstance(error, app_commands.CheckFailure):
        message = "❌ Cette commande n'est pas utilisable ici."
    else:
        print(f"Erreur de commande : {error}")
        message = "❌ Une erreur est survenue lors de l'exécution de la commande."

    if interaction.response.is_done():
        await interaction.followup.send(message, ephemeral=True)
    else:
        await interaction.response.send_message(message, ephemeral=True)


async def main():
    async with bot:
        for extension in INITIAL_EXTENSIONS:
            await bot.load_extension(extension)
        await bot.start(TOKEN)


if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit("DISCORD_TOKEN manquant : configure ton fichier .env")
    asyncio.run(main())
