# Commandes de ScoobyBot

Toutes les commandes sont des **slash commands** (`/nom`), avec autocomplétion native dans Discord — il n'y a plus de préfixe texte.

## Modération (`cogs/moderation.py`)

Visibles par défaut uniquement aux membres avec la permission **Administrateur** (un admin de serveur peut ajuster qui a accès à chaque commande depuis Discord > Paramètres du serveur > Intégrations).

| Commande | Description |
|---|---|
| `/kick <member> [raison]` | Expulse un membre. |
| `/ban <member> [raison]` | Bannit un membre. |
| `/unban <user_id>` | Débannit un utilisateur via son ID. |
| `/mute <member> [minutes=10] [raison]` | Met un membre en sourdine (timeout) pour une durée donnée. |
| `/unmute <member>` | Retire la sourdine d'un membre. |
| `/warn <member> [raison]` | Ajoute un avertissement. Auto-mute à 3 avertissements, auto-kick à 5. |
| `/warnings <member>` | Affiche l'historique des avertissements d'un membre. |
| `/clearwarnings <member>` | Réinitialise les avertissements d'un membre. |
| `/purge <nombre>` | Supprime `nombre` messages du salon (1-100, imposé par Discord). |
| `/slowmode <secondes>` | Règle le mode lent du salon (0-21600, imposé par Discord ; 0 = désactivé). |

## Rôles (`cogs/roles.py`)

Visibles par défaut aux membres avec la permission **Gérer les rôles**.

| Commande | Description |
|---|---|
| `/rolemenu <contenu>` | Crée un menu de rôles avec des boutons. Format : `"Titre" @rôle1 \| emoji \| label1 ; @rôle2 \| emoji \| label2`. |
| `/rolemenu_delete <message_id>` | Supprime un menu de rôles (message + suivi). |
| `/autorole [role]` | Définit le rôle donné automatiquement aux nouveaux membres. Sans argument, désactive le rôle automatique. |

Cliquer sur un bouton du menu ajoute ou retire le rôle correspondant (pas de commande, interaction directe).

Quand un rôle automatique est configuré, il est attribué dès qu'un membre rejoint le serveur (`on_member_join`).

## Musique (`cogs/music.py`)

| Commande | Description |
|---|---|
| `/join [channel]` | Fait rejoindre le bot à un salon vocal (le tien par défaut, ou celui précisé). |
| `/play <recherche>` | Joue ou ajoute à la file un morceau (recherche ou lien YouTube). |
| `/queue` | Affiche la file d'attente. |
| `/skip` | Passe au morceau suivant. |
| `/pause` | Met la lecture en pause. |
| `/resume` | Reprend la lecture. |
| `/leave` | Déconnecte le bot du vocal et vide la file. |

Le bot quitte automatiquement le salon vocal après **10 minutes d'inactivité** (rien en lecture, y compris en pause) — que le salon soit vide ou non. Le minuteur se réinitialise à chaque nouvelle lecture et se coupe dès qu'on reprend la lecture (`/resume`).

## Statistiques (`cogs/stats.py` + `cogs/statcommands.py` + `cardkit.py`)

Le suivi (messages, sessions vocales, réactions, invitations, boosts, commandes utilisées) se fait automatiquement en arrière-plan et s'écrit événement par événement dans Supabase — aucune commande à lancer pour ça, et aucun recalcul depuis l'historique Discord au moment de consulter les stats (`cogs/stats.py` capture les événements, `cogs/statcommands.py` ne fait que lire et compose une image via `cardkit.py`).

Chaque commande répond avec **une seule image** (une "card" générée avec Pillow/matplotlib, style dashboard sombre) regroupant chiffres, classements et graphiques — pas d'embed Discord séparé, juste un titre minimal dans le message.

Les trois commandes de lecture prennent un paramètre optionnel **`periode`** (`7 jours` / `30 jours` / `Tout`, défaut `30 jours`) qui filtre les classements, salons et graphiques affichés dans la card — les totaux 7j/30j/Tout en tête de card, eux, sont toujours affichés ensemble.

| Commande | Description |
|---|---|
| `/serverstat [periode]` | Messages et heures vocales (7j/30j/Tout) ; top 10 salons texte et vocaux ; top membres messages et vocal (classements séparés) ; contributeurs actifs ; membres les plus réactifs ; jour le plus actif ; dates de création du serveur et d'ajout du bot ; graphiques : activité par heure, et messages vs heures vocales dans le temps. |
| `/userstat [membre] [periode]` | Messages et heures vocales (7j/30j/Tout) ; classement serveur (messages et vocal séparés) ; top salons texte/vocaux du membre ; streak de jours consécutifs actifs ; emojis les plus utilisés (messages + réactions) ; dates d'arrivée et de création du compte ; graphiques : activité par heure et dans le temps. |
| `/channelstat [salon] [periode]` | Pour un salon texte : messages (7j/30j/Tout), top membres, activité par heure et dans le temps. Pour un salon vocal : heures vocales (7j/30j/Tout), top membres, activité dans le temps. Salon courant par défaut. |

Le suivi démarre à partir du moment où ce système est déployé — pas de recalcul de l'historique antérieur (l'ancienne commande `!initialize` a été retirée avec le reste des anciennes commandes stats).

## Blabla (`cogs/blabla.py`)

Pas de commande : un écouteur de messages. Si quelqu'un d'autre que `kakune.` envoie un message de plus de 100 caractères (liens exclus du décompte), le bot répond `blablablabla 😴😴😴 RATIO` et réagit à sa propre réponse avec 🔥.
