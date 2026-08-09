# ScoobyBot
Kakune's Discord Bot
made by Kakune. on Discord 
# Bot Discord — Modération, Rôles, Musique

Bot Discord tout-en-un : modération façon MEE6, distribution de rôles par bouton (comme Zira), lecteur de musique avec file d'attente, et statistiques serveur/membres façon StatBot. Le bot répond avec des répliques de Scooby-Doo à chaque action.

## Fonctionnalités

- **Modération** : kick, ban/unban, mute (timeout natif), warn avec sanctions automatiques, purge, slowmode
- **Rôles** : menu de rôles à récupérer via boutons (persistant après redémarrage), rôle automatique à l'arrivée d'un membre
- **Musique** : lecture depuis YouTube, file d'attente, pause/reprise/skip
- **Statistiques** : messages et temps vocal suivis par membre et par serveur, classements, recalcul de l'historique complet, carte de membre personnalisable

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
- `!autorole [@rôle]` — attribue automatiquement ce rôle à chaque nouveau membre ; sans argument, désactive

### Musique

- `!join` — rejoint ton salon vocal
- `!play <recherche ou lien>` — cherche sur YouTube et joue (ou ajoute à la file si déjà en lecture)
- `!queue` — affiche la file d'attente
- `!skip` — passe au morceau suivant
- `!pause` / `!resume` — pause / reprise
- `!leave` — quitte le vocal et vide la file

Le bot quitte automatiquement le vocal après 10 minutes sans rien en lecture (pause comprise), que le salon soit vide ou non.

### Statistiques

Le suivi des messages et du temps vocal se fait automatiquement, aucune commande à lancer. Sauvegarde automatique toutes les 5 minutes, sauf entre 4h et 14h (heure de Paris) où elle passe à toutes les heures, plus une sauvegarde à l'arrêt propre du bot.

- `!stats [@membre]` — messages envoyés + temps en vocal (soi-même par défaut)
- `!card [@membre]` — carte de membre en image (photo de profil, stats, citation)
- `!setquote <membre> <texte>` (administrateur) — définit la citation affichée sur la carte d'un membre
- `!topmessages` — top 10 des plus bavards
- `!topvoice` — top 10 du temps passé en vocal
- `!serverstats` — date de création du serveur, membres, messages et temps vocal totaux
- `!servercard` — carte du serveur en image (icône, date de création, membres, messages et temps vocal totaux)
- `!initialize [#salon]` (administrateur) — recalcule tout l'historique des messages depuis le début du serveur, ou d'un seul salon. Protégé contre les doublons (un salon déjà comptabilisé ne peut pas être relancé).

Référence complète et à jour de toutes les commandes : [`COMMANDS.md`](./COMMANDS.md).

---

## Structure du projet

```
discordbot/
├── bot.py                     point d'entrée, charge les cogs
├── cogs/
│   ├── moderation.py           kick, ban, mute, warn, purge, slowmode
│   ├── roles.py                menu de rôles par bouton
│   ├── music.py                lecteur de musique + file d'attente
│   ├── stats.py                statistiques messages/vocal, initialize
│   └── scooby_quotes.py        répliques de Scooby-Doo affichées après chaque action
├── data/
│   ├── warnings.json            avertissements par serveur/membre
│   ├── role_menus.json          menus de rôles persistants
│   ├── autorole.json            rôle automatique par serveur
│   ├── stats.json               messages/temps vocal par serveur/membre
│   └── initialized_channels.json  salons déjà comptabilisés par !initialize
├── requirements.txt
├── .env.example
├── COMMANDS.md                 référence complète des commandes
└── README.md
```

---

## Notes et limites

Le bot doit avoir un rôle placé au-dessus des rôles qu'il distribue ou modère (hiérarchie Discord classique), sinon les actions échouent silencieusement.

Les données (`warnings.json`, `role_menus.json`, `autorole.json`, `stats.json`, `initialized_channels.json`) sont stockées en fichiers locaux. Sur un hébergeur sans stockage persistant (certains plans Railway/Render en conteneur éphémère), elles seront perdues à chaque redéploiement — prévoir un volume persistant ou migrer vers une vraie base (SQLite/Postgres) si tu veux quelque chose de fiable sur la durée.

`!initialize` ne peut pas reconstituer le temps vocal passé : Discord ne conserve pas d'historique des présences vocales. Seuls les messages peuvent être recalculés depuis le début du serveur ; le suivi du temps vocal démarre à partir du moment où le bot tourne.

La lecture audio passe par yt-dlp et FFmpeg. Si YouTube change son format d'API, il faudra faire un `pip install -U yt-dlp` régulièrement pour que ça continue de fonctionner.

Pour un usage 24/7, héberge sur un VPS, Railway, Render, ou un Raspberry Pi qui tourne en continu.

## Hébergement

- **VPS** (OVH, Hetzner...) : contrôle total, un service systemd ou une session screen/tmux suffit
- **Railway / Render** : déploiement simple depuis GitHub, attention au stockage éphémère
- **Docker** : possible d'ajouter un Dockerfile si besoin

Pour héberger gratuitement sans passer par ton PC : voir [`DEPLOIEMENT_GRATUIT.txt`](./DEPLOIEMENT_GRATUIT.txt).