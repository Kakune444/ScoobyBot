# ScoobyBot
Kakune's Discord Bot

# Bot Discord — Modération, Rôles, Musique

Bot Discord tout-en-un : modération façon MEE6, distribution de rôles par bouton (comme Zira), et lecteur de musique avec file d'attente.

## Fonctionnalités

- **Modération** : kick, ban/unban, mute (timeout natif), warn avec sanctions automatiques, purge, slowmode
- **Rôles** : menu de rôles à récupérer via boutons, persistant après redémarrage du bot
- **Musique** : lecture depuis YouTube, file d'attente, pause/reprise/skip

---

## Installation

### Prérequis
- Python 3.10+
- FFmpeg installé et accessible dans le PATH (`ffmpeg -version` pour vérifier)

### Étapes

```bash
git clone <ton-repo>
cd discordbot
pip install -r requirements.txt
```

Crée ton fichier de config :
```bash
cp .env.example .env
```
Puis édite `.env` :
```
DISCORD_TOKEN=colle_ton_token_ici
BOT_PREFIX=!
```

### Créer et configurer le bot sur Discord

1. Va sur https://discord.com/developers/applications, crée une nouvelle application
2. Onglet Bot → Add Bot → copie le token dans `.env`
3. Active ces deux intents privilégiés dans l'onglet Bot : `SERVER MEMBERS INTENT` et `MESSAGE CONTENT INTENT`
4. Onglet OAuth2 → URL Generator :
   - Scopes : `bot`
   - Permissions : Kick Members, Ban Members, Moderate Members, Manage Messages, Manage Roles, Manage Channels, Connect, Speak, Send Messages, Embed Links, Read Message History
5. Ouvre l'URL générée et invite le bot sur ton serveur

### Lancer

```bash
python bot.py
```

---

## Commandes

### Modération
Nécessite les permissions correspondantes sur le serveur (kick/ban/manage_messages/etc.).

- `!kick @membre [raison]` — expulse un membre
- `!ban @membre [raison]` — bannit un membre
- `!unban <id>` — débannit via son ID
- `!mute @membre [minutes] [raison]` — timeout natif Discord (défaut : 10 min)
- `!unmute @membre` — retire le timeout
- `!warn @membre [raison]` — avertit un membre. 3 warns = mute auto 1h, 5 warns = kick auto
- `!warnings @membre` — liste les avertissements d'un membre
- `!clearwarnings @membre` — réinitialise les avertissements
- `!purge <nombre>` — supprime N messages (max 100)
- `!slowmode <secondes>` — configure le slowmode du salon

### Rôles par bouton

```
!rolemenu "Choisis ton rôle" @Gamer | 🎮 | Gamer ; @Artiste | 🎨 | Artiste
```

Format de chaque rôle : `@mention | emoji | label`, séparés par `;`. Ça génère un embed avec un bouton par rôle — clic pour ajouter, reclic pour retirer.

- `!rolemenu "<titre>" @role1 | emoji | label ; ...` — crée le menu
- `!rolemenu_delete <message_id>` — supprime un menu existant

### Musique

- `!join` — rejoint ton salon vocal
- `!play <recherche ou lien>` — cherche sur YouTube et joue (ou ajoute à la file si déjà en lecture)
- `!queue` — affiche la file d'attente
- `!skip` — passe au morceau suivant
- `!pause` / `!resume` — pause / reprise
- `!leave` — quitte le vocal et vide la file

---

## Structure du projet

```
discordbot/
├── bot.py                 point d'entrée, charge les cogs
├── cogs/
│   ├── moderation.py       kick, ban, mute, warn, purge, slowmode
│   ├── roles.py            menu de rôles par bouton
│   └── music.py            lecteur de musique + file d'attente
├── data/
│   ├── warnings.json        avertissements par serveur/membre
│   └── role_menus.json      menus de rôles persistants
├── requirements.txt
├── .env.example
└── README.md
```

---

## Notes et limites

Le bot doit avoir un rôle placé au-dessus des rôles qu'il distribue ou modère (hiérarchie Discord classique), sinon les actions échouent silencieusement.

Les données (`warnings.json`, `role_menus.json`) sont stockées en fichiers locaux. Sur un hébergeur sans stockage persistant (certains plans Railway/Render en conteneur éphémère), elles seront perdues à chaque redéploiement — prévoir un volume persistant ou migrer vers une vraie base (SQLite/Postgres) si tu veux quelque chose de fiable sur la durée.

La lecture audio passe par yt-dlp et FFmpeg. Si YouTube change son format d'API, il faudra faire un `pip install -U yt-dlp` régulièrement pour que ça continue de fonctionner.

Pour un usage 24/7, héberge sur un VPS, Railway, Render, ou un Raspberry Pi qui tourne en continu.

## Hébergement

- **VPS** (OVH, Hetzner...) : contrôle total, un service systemd ou une session screen/tmux suffit
- **Railway / Render** : déploiement simple depuis GitHub, attention au stockage éphémère
- **Docker** : possible d'ajouter un Dockerfile si besoin