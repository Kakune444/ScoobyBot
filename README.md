# ScoobyBot
Kakune's Discord Bot
made by Kakune. on Discord 
# Bot Discord — Modération, Rôles, Musique, Économie, Statistiques

Bot Discord tout-en-un : modération façon MEE6, distribution de rôles par bouton (comme Zira), lecteur de musique avec file d'attente, et statistiques serveur/membres façon StatBot (mais avec un vrai suivi événementiel en base, pas des compteurs qui se réinitialisent). Le bot répond avec des répliques de Scooby-Doo à chaque action. Toutes les commandes sont des **slash commands** (`/nom`).

## Fonctionnalités

- **Modération** : kick, ban/unban, mute (timeout natif), warn avec sanctions automatiques, purge, slowmode
- **Rôles** : menu de rôles à récupérer via boutons (persistant après redémarrage), rôle automatique à l'arrivée d'un membre
- **Musique** : lecture depuis YouTube, file d'attente, pause/reprise/skip, déconnexion automatique après 10 min d'inactivité
- **Économie** : 5 coins par heure en vocal, 0,5 coin par message valide, solde séparé par serveur et protection anti-spam
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

### Railway et blocage YouTube

Sur Railway, YouTube peut bloquer les requêtes de `yt-dlp` avec le message « Sign in to confirm you're not a bot ». Le projet épingle `yt-dlp 2026.8.19`, installe son extra EJS, Deno 2.9.5 et le plugin `bgutil-ytdlp-pot-provider 1.3.1`. Les cookies sont acceptés via `YOUTUBE_COOKIES_B64` ou un fichier `cookies.txt` non versionné.

Pour la configurer :

1. Ouvre une fenêtre privée, connecte-toi à YouTube, puis visite `https://www.youtube.com/robots.txt` dans cette même fenêtre.
2. Exporte uniquement les cookies de `youtube.com` avec une extension fiable au format Netscape, puis ferme la fenêtre privée sans rouvrir cet onglet.
3. Encode le fichier `cookies.txt` en base64 et colle le résultat dans la variable Railway `YOUTUBE_COOKIES_B64`, puis redéploie le service.

Sous PowerShell, l'encodage se fait ainsi (la valeur doit rester secrète) :

```powershell
$bytes = [IO.File]::ReadAllBytes(".\cookies.txt")
[Convert]::ToBase64String($bytes)
```

Ne commite jamais `cookies.txt` ni sa valeur base64. Utilise de préférence un compte YouTube dédié : `yt-dlp` avertit que l'utilisation de cookies peut entraîner une limitation ou un bannissement du compte. En local, tu peux aussi définir `YOUTUBE_COOKIES_FILE` vers le chemin du fichier ; sans variable, le bot cherche `cookies.txt` à la racine du projet. Au démarrage, il affiche uniquement l'existence, la taille et l'état du format Netscape, jamais les valeurs.

Pour le PO Token Provider recommandé, crée un second service Railway dans le même projet/environnement à partir de l'image Docker `brainicism/bgutil-ytdlp-pot-provider:1.3.1-deno`. Laisse ce service écouter sur son port interne `4416`, puis ajoute au service du bot la variable `YOUTUBE_POT_BASE_URL` avec une référence privée du type `http://${{bgutil-pot.RAILWAY_PRIVATE_DOMAIN}}:4416` (en adaptant `bgutil-pot` au nom du service). Le bot utilise alors `youtubepot-bgutilhttp:base_url=...`; aucune URL publique ni secret n'est nécessaire. Le provider HTTP est préférable au mode script pour un bot 24/7 car il évite de lancer Deno à chaque requête.

La commande `/ytdlpdiagnostic` (Administrateur) teste par défaut `https://www.youtube.com/watch?v=msa8KUwXbz0` sans télécharger le média complet et distingue cookies absents/invalides, provider PO Token indisponible, runtime/EJS, problème réseau Railway et refus YouTube. Elle ne renvoie jamais les logs verbose bruts, les headers ou les secrets.
Puis édite `.env` avec ton token Discord et tes identifiants Supabase (voir section suivante).

### Configurer Supabase

1. Crée un projet sur [supabase.com](https://supabase.com)
2. Dans le SQL Editor du projet, colle [`supabase/economy.sql`](./supabase/economy.sql) et exécute-le — cette migration crée une ligne `coin_balances` par membre et peut être relancée. Elle suppose que les tables statistiques `members` et `voice_sessions` existent déjà.
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
- `/play <recherche ou lien>` — cherche sur YouTube et joue (ou ajoute à la file si déjà en lecture) ; utilise les cookies et le PO Token Provider configurés sur Railway
- `/ytdlpdiagnostic [url]` — (Administrateur) vérifie yt-dlp/YouTube sans téléchargement complet
- `/queue` — affiche la file d'attente
- `/skip` — passe au morceau suivant
- `/pause` / `/resume` — pause / reprise
- `/leave` — quitte le vocal et vide la file

Le bot quitte automatiquement le vocal après 10 minutes sans rien en lecture (pause comprise), que le salon soit vide ou non.

### Économie

- `/eco` — aide sur l'économie (gains + commandes)
- `/balance` — affiche ton solde de coins sur le serveur actuel
- `/slots <mise>` — joue à la machine à sous avec une mise fixe de 1, 5, 10 ou 100 coins
- `/roulette <mise>` — roulette européenne (RTP 97,30 %), mise 1-1 000 000

Les membres gagnent **5 coins par heure de vocal**, au prorata du temps réellement passé, et **0,5 coin par message**. Le temps de vocal actif est inclus immédiatement dans `/balance`, puis enregistré comme transaction à la déconnexion. Les messages des bots, les commandes adressées au bot, les doublons identiques dans les 30 secondes et le sixième message (ou suivant) envoyé dans une fenêtre de 10 secondes ne rapportent rien. Les soldes sont persistants dans Supabase et séparés serveur par serveur. `/slots` utilise des rouleaux pondérés, une animation en card et un RTP théorique de **97,03 %** (paire ×1,6 ; triples ×4, 💎×10, 7️⃣×30) ; les parties sont enregistrées dans `slot_games`. `/roulette` est une vraie roulette européenne (un seul zéro, RTP **97,30 %**) : Plein 35:1, Rouge/Noir, Pair/Impair, Manque/Passe 1:1, Douzaine/Colonne 2:1, avec mise de 1 à 1 000 000 coins ; les parties sont enregistrées dans `roulette_games`.

### Statistiques

Le suivi (messages, vocal, réactions, invitations, boosts, commandes) se fait automatiquement en arrière-plan et s'écrit dans Supabase événement par événement — aucune commande à lancer pour l'alimenter, et les commandes ci-dessous lisent directement Supabase sans jamais rescanner l'historique Discord. Chaque commande répond avec une seule image 1280×708 (card Pillow au layout inspiré de Statbot : badges de dates, classement, chiffres par sous-fenêtres, tops, graphique superposé messages/vocal). Les emojis des noms sont rendus via Twemoji (téléchargés au premier usage puis cachés en mémoire).

- `/serverstat [periode]` — card du serveur : top membres (messages/vocal), messages et heures vocales, top salons, graphique d'activité
- `/userstat [membre] [periode]` — card d'un membre : classement serveur (Message #X / Vocale #X), messages, heures vocales, top salons, graphique
- `/channelstat [salon] [periode]` — card d'un salon (texte ou vocal) : rang du salon, messages, heures vocales, top membres, graphique

`periode` (`7 jours` / `14 jours` / `30 jours` / `Tout`, défaut **14 jours**) pilote toute la card ; les trois sous-fenêtres des blocs chiffres s'y adaptent (ex. 14j → 1j/7j/14j).

Quatre commandes admin complètent les données au lieu de simplement les lire :
- `/initialize <salon>` — importe l'historique des messages d'un salon (texte, vocal ou fil) dans Supabase ; sans risque de doublon même en relançant la commande plus tard
- `/initializeall` — pareil, mais sur tout le serveur d'un coup (salons texte, chat des vocaux, fils actifs)
- `/addtime <salon> <membre> <minutes>` — crédite manuellement du temps vocal à un membre (rattrapage, correction)
- `/importvoice <membres> <salons>` — importe l'historique vocal depuis deux CSV Statbot joints (l'historique vocal n'étant pas reconstituable via l'API Discord, contrairement aux messages)

Référence complète et à jour de toutes les commandes : [`COMMANDS.md`](./COMMANDS.md).

---

## Structure du projet

```
ScoobyBot/
├── bot.py                     point d'entrée, charge les cogs, sync des slash commands
├── supabase_client.py         client Supabase (lecture/écriture) partagé par les cogs
├── cardkit.py                  rendu des cards stats (Pillow, 1280×708, layout Statbot) + emojis Twemoji
├── DESIGN.md                  spécification technique des nouvelles fonctionnalités économie (pay, prêts)
├── supabase/
│   └── economy.sql             soldes, transactions JSONB + journal des parties slots et roulette
├── cogs/
│   ├── moderation.py           kick, ban, mute, warn, purge, slowmode
│   ├── roles.py                menu de rôles par bouton
│   ├── music.py                lecteur de musique + file d'attente + auto-disconnect
│   ├── economy.py              gains de coins + /balance + /slots + /roulette + anti-spam
│   ├── stats.py                capture des événements (messages, vocal, réactions, invitations, boosts) → Supabase
│   ├── statcommands.py         /serverstat /userstat /channelstat — lecture Supabase + composition de la card (cardkit.py)
│   ├── blabla.py                réponse automatique aux pavés de texte
│   └── scooby_quotes.py        répliques de Scooby-Doo affichées après chaque action
├── tests/
│   └── test_youtube_diagnostics.py validation locale des cookies et de l'URL de diagnostic
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

La lecture audio passe par yt-dlp et FFmpeg. Si YouTube change son format d'API, il faudra mettre à jour `yt-dlp[default]` régulièrement pour que ça continue de fonctionner.

Pour un usage 24/7, héberge sur un VPS, Railway, Render, ou un Raspberry Pi qui tourne en continu.

## Hébergement

- **VPS** (OVH, Hetzner...) : contrôle total, un service systemd ou une session screen/tmux suffit
- **Railway / Render** : déploiement simple depuis GitHub (ce projet inclut `railpack.json` pour Railway, qui installe notamment FFmpeg au build)
- **Docker** : possible d'ajouter un Dockerfile si besoin
