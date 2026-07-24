# Commandes de ScoobyBot

Préfixe par défaut : `!` (configurable via `BOT_PREFIX` dans `.env`).

## Modération (`cogs/moderation.py`)

Toutes les commandes de ce cog nécessitent la permission **Administrateur** sur le serveur.

| Commande | Description |
|---|---|
| `!kick <membre> [raison]` | Expulse un membre. |
| `!ban <membre> [raison]` | Bannit un membre. |
| `!unban <user_id>` | Débannit un utilisateur via son ID. |
| `!mute <membre> [minutes=10] [raison]` | Met un membre en sourdine (timeout) pour une durée donnée. |
| `!unmute <membre>` | Retire la sourdine d'un membre. |
| `!warn <membre> [raison]` | Ajoute un avertissement. Auto-mute à 3 avertissements, auto-kick à 5. |
| `!warnings <membre>` | Affiche l'historique des avertissements d'un membre. |
| `!clearwarnings <membre>` | Réinitialise les avertissements d'un membre. |
| `!purge <nombre>` | Supprime les `nombre` derniers messages du salon (max 100). |
| `!slowmode <secondes>` | Règle le mode lent du salon (0 pour désactiver, max 21600s). |

## Rôles (`cogs/roles.py`)

| Commande | Permission requise | Description |
|---|---|---|
| `!rolemenu "Titre" @rôle \| emoji \| label ; ...` | Gérer les rôles | Crée un menu de rôles avec des boutons. |
| `!rolemenu_delete <message_id>` | Gérer les rôles | Supprime un menu de rôles (message + suivi). |
| `!autorole [@rôle]` | Gérer les rôles | Définit le rôle donné automatiquement aux nouveaux membres. Sans argument, désactive le rôle automatique. |

Cliquer sur un bouton du menu ajoute ou retire le rôle correspondant (pas de commande, interaction directe).

Quand un rôle automatique est configuré, il est attribué dès qu'un membre rejoint le serveur (`on_member_join`).

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
| `!card [@membre]` | — | Génère une image "carte de membre" (photo de profil, messages, temps vocal, citation). Sans citation définie, affiche une réplique Scooby-Doo aléatoire. |
| `!setquote <membre> <texte>` | Administrateur | Définit la citation affichée sur la carte d'un membre. |
| `!topmessages` | — | Classement des 10 membres les plus actifs en messages. |
| `!topvoice` | — | Classement des 10 membres avec le plus de temps vocal. |
| `!serverstats` | — | Statistiques globales du serveur (date de création, membres, messages et temps vocal totaux). |
| `!servercard` | — | Génère une image "carte du serveur" (icône, date de création, membres, messages et temps vocal totaux). |
| `!initialize [#salon]` | Administrateur | Recalcule l'historique des messages depuis le début du serveur (ou d'un seul salon). Protégé contre les doublons : un salon déjà comptabilisé ne peut pas être relancé. |

Le suivi des messages et du temps vocal se fait automatiquement en arrière-plan (pas de commande à lancer). Sauvegarde toutes les 5 minutes, sauf entre 4h et 14h (heure de Paris) où elle passe à toutes les heures, ainsi qu'à l'arrêt propre du bot.
