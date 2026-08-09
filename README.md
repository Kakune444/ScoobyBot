# ScoobyBot
Kakune's Discord Bot
made by Kakune. on Discord 
# Bot Discord — Modération, Rôles, Musique, Statistiques

Bot Discord tout-en-un : modération façon MEE6, distribution de rôles par bouton (comme Zira), lecteur de musique avec file d'attente, et statistiques serveur/membres façon StatBot (mais avec un vrai suivi événementiel en base, pas des compteurs qui se réinitialisent). Le bot répond avec des répliques de Scooby-Doo à chaque action. Toutes les commandes sont des **slash commands** (`/nom`).

## Fonctionnalités

- **Modération** : kick, ban/unban, mute (timeout natif), warn avec sanctions automatiques, purge, slowmode
- **Rôles** : menu de rôles à récupérer via boutons (persistant après redémarrage), rôle automatique à l'arrivée d'un membre
- **Musique** : lecture depuis YouTube, file d'attente, pause/reprise/skip, déconnexion automatique après 10 min d'inactivité
- **Statistiques** : messages, sessions vocales, réactions, invitations, boosts et commandes suivis événement par événement dans Supabase — classements et graphiques sur 7 jours / 30 jours / tout l'historique via `/serverstat`, `/userstat`, `/channelstat`

---

## Installation

### Prérequis
- Python 3.10+
- FFmpeg installé et accessible dans le PATH (`ffmpeg -version` pour vérifier)
- Un projet [Supabase](https://supabase.com) (gratuit) pour les statistiques

### Étapes

```bash
git clone <ton-repo>
cd ScoobyBot
pip install -r requirements.txt
```

Crée ton fichier de config :
```bash
cp .env.example .env
```
Puis édite `.env` avec ton token Discord et tes identifiants Supabase (voir section suivante).

### Configurer Supabase

1. Crée un projet sur [supabase.com](https://supabase.com)
2. Dans le SQL Editor du projet, colle le contenu de [`supabase/schema.sql`](./supabase/schema.sql) et exécute-le (une seule fois) — ça crée les tables et fonctions nécessaires aux stats
3. Dans Project Settings > API, récupère l'**URL du projet** (`SUPABASE_URL`) et la clé **`service_role`** (`SUPABASE_KEY`) — surtout pas la clé `anon`/publique, la `service_role` a le même niveau de sensibilité que `DISCORD_TOKEN` et ne doit jamais être commitée ni exposée côté client

### Créer et configurer le bot sur Discord

1. Va sur https://discord.com/developers/applications, crée une nouvelle application
2. Onglet Bot → Add Bot → copie le token dans `.env`
3. Active ces deux intents privilégiés dans l'onglet Bot : `SERVER MEMBERS INTENT` et `MESSAGE CONTENT INTENT`
4. Onglet OAuth2 → URL Generator :
   - Scopes : `bot`, `applications.commands`
   - Permissions : Kick Members, Ban Members, Moderate Members, Manage Messages, Manage Roles, Manage Channels, **Manage Server** (nécessaire pour attribuer les invitations utilisées aux nouveaux membres), Connect, Speak, Send Messages, Embed Links, Read Message History
5. Ouvre l'URL générée et invite le bot sur ton serveur

Si le bot est déjà présent sur un serveur, la permission **Manage Server** doit être réattribuée manuellement à son rôle (changer l'URL OAuth2 ne rétroagit pas sur une invitation déjà acceptée).

### Lancer

```bash
python bot.py
```

Au démarrage, le bot synchronise ses slash commands sur le serveur de dev défini par `DEV_GUILD_ID` dans `bot.py` (propagation quasi instantanée) ; le sync global pour tous les serveurs est en commentaire dans `bot.py`, à activer quand les commandes sont stables.

---

## Commandes

### Modération
Visibles par défaut aux membres avec la permission Administrateur.

- `/kick <membre> [raison]` — expulse un membre
- `/ban <membre> [raison]` — bannit un membre
- `/unban <id>` — débannit via son ID
- `/mute <membre> [minutes] [raison]` — timeout natif Discord (défaut : 10 min)
- `/unmute <membre>` — retire le timeout
- `/warn <membre> [raison]` — avertit un membre. 3 warns = mute auto 1h, 5 warns = kick auto
- `/warnings <membre>` — liste les avertissements d'un membre
- `/clearwarnings <membre>` — réinitialise les avertissements
- `/purge <nombre>` — supprime N messages (max 100)
- `/slowmode <secondes>` — configure le slowmode du salon

### Rôles par bouton

```
/rolemenu contenu:"Choisis ton rôle" @Gamer | 🎮 | Gamer ; @Artiste | 🎨 | Artiste
```

Format de chaque rôle : `@mention | emoji | label`, séparés par `;`. Ça génère un embed avec un bouton par rôle — clic pour ajouter, reclic pour retirer.

- `/rolemenu <contenu>` — crée le menu
- `/rolemenu_delete <message_id>` — supprime un menu existant
- `/autorole [role]` — attribue automatiquement ce rôle à chaque nouveau membre ; sans argument, désactive

### Musique

- `/join [channel]` — rejoint ton salon vocal, ou celui précisé
- `/play <recherche ou lien>` — cherche sur YouTube et joue (ou ajoute à la file si déjà en lecture)
- `/queue` — affiche la file d'attente
- `/skip` — passe au morceau suivant
- `/pause` / `/resume` — pause / reprise
- `/leave` — quitte le vocal et vide la file

Le bot quitte automatiquement le vocal après 10 minutes sans rien en lecture (pause comprise), que le salon soit vide ou non.

### Statistiques

Le suivi (messages, vocal, réactions, invitations, boosts, commandes) se fait automatiquement en arrière-plan et s'écrit dans Supabase événement par événement — aucune commande à lancer pour l'alimenter, et les commandes ci-dessous lisent directement Supabase sans jamais rescanner l'historique Discord. Chaque commande répond avec une seule image 1280×708 (card Pillow au layout inspiré de Statbot : badges de dates, classement, chiffres par sous-fenêtres, tops, graphique superposé messages/vocal). Les emojis des noms sont rendus via Twemoji (téléchargés au premier usage puis cachés en mémoire).

- `/serverstat [periode]` — card du serveur : top membres (messages/vocal), messages et heures vocales, top salons, graphique d'activité
- `/userstat [membre] [periode]` — card d'un membre : classement serveur (Message #X / Vocale #X), messages, heures vocales, top salons, graphique
- `/channelstat [salon] [periode]` — card d'un salon (texte ou vocal) : rang du salon, messages, heures vocales, top membres, graphique

`periode` (`7 jours` / `14 jours` / `30 jours` / `Tout`, défaut **14 jours**) pilote toute la card ; les trois sous-fenêtres des blocs chiffres s'y adaptent (ex. 14j → 1j/7j/14j).

Deux commandes admin complètent les données au lieu de simplement les lire :
- `/initialize [channel]` — importe l'historique des messages d'un salon (ou de tous) dans Supabase ; sans risque de doublon même en relançant la commande plus tard
- `/addtime <salon> <membre> <minutes>` — crédite manuellement du temps vocal à un membre (rattrapage, correction)

Référence complète et à jour de toutes les commandes : [`COMMANDS.md`](./COMMANDS.md).

---

## Structure du projet

```
ScoobyBot/
├── bot.py                     point d'entrée, charge les cogs, sync des slash commands
├── supabase_client.py         client Supabase (lecture/écriture) partagé par les cogs
├── cardkit.py                  rendu des cards stats (Pillow, 1280×708, layout Statbot) + emojis Twemoji
├── supabase/
│   └── schema.sql              schéma complet (tables + fonctions) à exécuter une fois sur le projet Supabase
├── cogs/
│   ├── moderation.py           kick, ban, mute, warn, purge, slowmode
│   ├── roles.py                menu de rôles par bouton
│   ├── music.py                lecteur de musique + file d'attente + auto-disconnect
│   ├── stats.py                capture des événements (messages, vocal, réactions, invitations, boosts) → Supabase
│   ├── statcommands.py         /serverstat /userstat /channelstat — lecture Supabase + composition de la card (cardkit.py)
│   ├── blabla.py                réponse automatique aux pavés de texte
│   └── scooby_quotes.py        répliques de Scooby-Doo affichées après chaque action
├── data/
│   ├── warnings.json            avertissements par serveur/membre
│   ├── role_menus.json          menus de rôles persistants
│   └── autorole.json            rôle automatique par serveur
├── requirements.txt
├── .env.example
├── COMMANDS.md                 référence complète des commandes
└── README.md
```

---

## Notes et limites

Le bot doit avoir un rôle placé au-dessus des rôles qu'il distribue ou modère (hiérarchie Discord classique), sinon les actions échouent — les commandes de modération renvoient désormais un message d'erreur explicite dans ce cas plutôt que d'échouer silencieusement.

Les stats vivent dans Supabase (Postgres durable) : elles survivent aux redéploiements, contrairement à l'ancien système en fichier JSON. Le suivi démarre au moment où ce système est déployé — pas de recalcul de l'historique antérieur.

**Risque résiduel** : `warnings.json`, `role_menus.json` et `autorole.json` restent stockés en fichiers locaux dans `data/`. Sur un hébergeur à conteneur éphémère (Railway sans volume persistant, par exemple), ils seraient perdus à chaque redéploiement — à garder en tête si tu veux les rendre aussi durables que les stats.

La lecture audio passe par yt-dlp et FFmpeg. Si YouTube change son format d'API, il faudra faire un `pip install -U yt-dlp` régulièrement pour que ça continue de fonctionner.

Pour un usage 24/7, héberge sur un VPS, Railway, Render, ou un Raspberry Pi qui tourne en continu.

## Hébergement

- **VPS** (OVH, Hetzner...) : contrôle total, un service systemd ou une session screen/tmux suffit
- **Railway / Render** : déploiement simple depuis GitHub (ce projet inclut `railpack.json` pour Railway, qui installe notamment FFmpeg au build)
- **Docker** : possible d'ajouter un Dockerfile si besoin
