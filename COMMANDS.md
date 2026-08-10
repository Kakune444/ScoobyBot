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

Chaque commande répond avec **une seule image 1280×708** (card générée avec Pillow, layout inspiré de Statbot) : header avatar + badges de dates, bloc classement, blocs Messages / Activité vocale sur trois sous-fenêtres, top salons/membres, et graphique superposé messages (vert) vs vocal (rose) sans axes. Les emojis dans les noms sont rendus via des images Twemoji (téléchargées au premier usage puis cachées) — le premier rendu après un démarrage nécessite donc un accès réseau.

Les trois commandes de lecture prennent un paramètre optionnel **`periode`** (`7 jours` / `14 jours` / `30 jours` / `Tout`, défaut **`14 jours`**) qui pilote toute la card ; les trois sous-fenêtres des blocs chiffres suivent la période : 7j → 1j/3j/7j · 14j → 1j/7j/14j · 30j → 1j/7j/30j · Tout → 1j/7j/Tout.

| Commande | Description |
|---|---|
| `/serverstat [periode]` | Card du serveur : badges création serveur / ajout du bot ; bloc « Top membres » (meilleur membre messages et vocal) ; messages et heures vocales par sous-fenêtres ; top salon texte + top salon vocal ; graphique d'activité. |
| `/userstat [membre] [periode]` | Card d'un membre (réplique de la référence) : badges création du compte / arrivée sur le serveur ; bloc « Classement serveur » (Message #X / Vocale #X sur la période) ; messages et heures vocales ; top salons du membre ; graphique d'activité. |
| `/channelstat [salon] [periode]` | Card d'un salon (texte ou vocal, salon courant par défaut) : badges création / type ; bloc « Classement serveur » (rang du salon en messages et en vocal) ; messages et heures vocales du salon ; top membres du salon ; graphique d'activité. |

Le suivi démarre à partir du moment où ce système est déployé — pas de recalcul automatique de l'historique antérieur, sauf lancement manuel de `/initialize` (voir ci-dessous).

### Écritures manuelles (admin)

Réservées aux administrateurs — corrigent ou complètent les données Supabase, contrairement aux trois commandes ci-dessus qui ne font que lire.

| Commande | Description |
|---|---|
| `/initialize <channel>` | Scanne l'historique des messages d'un salon (texte, vocal ou fil) et l'importe dans Supabase. Idempotent : chaque message n'est compté qu'une fois (`message_id` en clé primaire), donc relancer la commande — même sur un salon déjà importé — ne crée jamais de doublon. Peut prendre plusieurs minutes sur un gros historique. |
| `/initializeall` | Comme `/initialize`, mais sur **tout le serveur** : salons texte (annonces comprises), chat des salons vocaux et des stages, et fils actifs (les fils archivés ne sont pas parcourus). Idempotent aussi ; un seul import à la fois par serveur. |
| `/addtime <salon> <membre> <minutes>` | Crédite manuellement `minutes` de temps vocal à `membre` dans `salon` (rattrapage d'une session non trackée, correction). Insère une session vocale synthétique déjà close ; répond avec les nouveaux totaux du membre. |
| `/importvoice <membres> <salons>` | Importe l'historique vocal depuis deux CSV Statbot joints (heures par membre + heures par salon, colonnes `rank,name/username,id,count`). Les totaux par membre et par salon sont conservés exactement ; le croisement membre↔salon est réparti au prorata (le CSV ne le contient pas). Les sessions synthétiques sont datées plus de 30 jours avant l'arrivée du bot : elles ne comptent que dans « Tout », jamais dans les fenêtres 7j/14j/30j. Relançable — chaque exécution remplace la précédente. |

## Blabla (`cogs/blabla.py`)

Pas de commande : un écouteur de messages. Si quelqu'un d'autre que `kakune.` envoie un message de plus de 100 caractères (liens exclus du décompte), le bot répond `blablablabla 😴😴😴 RATIO` et réagit à sa propre réponse avec 🔥.
