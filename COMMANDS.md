# Commandes de ScoobyBot

Préfixe par défaut : `!` (configurable via `BOT_PREFIX` dans `.env`).

## Modération (`cogs/moderation.py`)

| Commande | Permission requise | Description |
|---|---|---|
| `!kick <membre> [raison]` | Expulser des membres | Expulse un membre. |
| `!ban <membre> [raison]` | Bannir des membres | Bannit un membre. |
| `!unban <user_id>` | Bannir des membres | Débannit un utilisateur via son ID. |
| `!mute <membre> [minutes=10] [raison]` | Modérer les membres | Met un membre en sourdine (timeout) pour une durée donnée. |
| `!unmute <membre>` | Modérer les membres | Retire la sourdine d'un membre. |
| `!warn <membre> [raison]` | Gérer les messages | Ajoute un avertissement. Auto-mute à 3 avertissements, auto-kick à 5. |
| `!warnings <membre>` | Gérer les messages | Affiche l'historique des avertissements d'un membre. |
| `!clearwarnings <membre>` | Gérer les messages | Réinitialise les avertissements d'un membre. |
| `!purge <nombre>` | Gérer les messages | Supprime les `nombre` derniers messages du salon (max 100). |
| `!slowmode <secondes>` | Gérer les salons | Règle le mode lent du salon (0 pour désactiver, max 21600s). |

## Rôles (`cogs/roles.py`)

| Commande | Permission requise | Description |
|---|---|---|
| `!rolemenu "Titre" @rôle \| emoji \| label ; ...` | Gérer les rôles | Crée un menu de rôles avec des boutons. |
| `!rolemenu_delete <message_id>` | Gérer les rôles | Supprime un menu de rôles (message + suivi). |

Cliquer sur un bouton du menu ajoute ou retire le rôle correspondant (pas de commande, interaction directe).

## Musique (`cogs/music.py`)

| Commande | Description |
|---|---|
| `!join` | Fait rejoindre le bot au salon vocal de l'auteur. |
| `!play <recherche>` | Joue ou ajoute à la file un morceau (recherche YouTube). |
| `!queue` | Affiche la file d'attente. |
| `!skip` | Passe au morceau suivant. |
| `!pause` | Met la lecture en pause. |
| `!resume` | Reprend la lecture. |
| `!leave` | Déconnecte le bot du vocal et vide la file. |

## Statistiques (`cogs/stats.py`)

| Commande | Permission requise | Description |
|---|---|---|
| `!stats [@membre]` | — | Affiche le nombre de messages et le temps en vocal d'un membre (soi-même par défaut). |
| `!topmessages` | — | Classement des 10 membres les plus actifs en messages. |
| `!topvoice` | — | Classement des 10 membres avec le plus de temps vocal. |
| `!serverstats` | — | Statistiques globales du serveur (date de création, membres, messages et temps vocal totaux). |
| `!initialize [#salon]` | Administrateur | Recalcule l'historique des messages depuis le début du serveur (ou d'un seul salon). Protégé contre les doublons : un salon déjà comptabilisé ne peut pas être relancé. |

Le suivi des messages et du temps vocal se fait automatiquement en arrière-plan (pas de commande à lancer), et les données sont sauvegardées toutes les heures entre 4h et 14h (heure de Paris) ainsi qu'à l'arrêt propre du bot.
