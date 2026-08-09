-- ===========================================================================
-- ScoobyBot — schéma Supabase / Postgres pour les stats event-based
-- ===========================================================================
--
-- À exécuter une fois dans l'éditeur SQL d'un projet Supabase (SQL Editor >
-- New query > coller > Run). Ce fichier n'est pas encore branché au code du
-- bot : c'est la proposition de schéma, à valider avant le câblage
-- (cogs/stats.py, cogs/statcommands.py, supabase_client.py).
--
-- Conventions :
--   - Tous les identifiants Discord (guild/channel/user/message) sont des
--     snowflakes 64 bits -> BIGINT (int4 plafonne à ~2.1 milliards, largement
--     insuffisant).
--   - Tous les timestamps sont en TIMESTAMPTZ, stockés en UTC. Les
--     répartitions horaires/jour-de-semaine se calculent à la lecture avec
--     `AT TIME ZONE 'Europe/Paris'`.
--   - Le bot se connecte avec la clé service_role (contourne toujours RLS).
--     RLS est activée sur chaque table sans policy permissive : ça ne change
--     rien au fonctionnement du bot aujourd'hui, mais protège par défaut si
--     une clé anon/publique est introduite plus tard (ex. dashboard web).
--   - Aucun contenu de message n'est stocké nulle part (vie privée) : on ne
--     garde que des identifiants et des métadonnées.
-- ===========================================================================


-- ---------------------------------------------------------------------------
-- messages : un enregistrement par message suivi.
--
-- message_id est directement le snowflake Discord du message (pas de clé
-- surrogate) : ça rend chaque insertion naturellement idempotente via
-- `ON CONFLICT (message_id) DO NOTHING`, utile si un futur backfill doit être
-- interrompu et relancé sans risquer de compter deux fois les mêmes messages.
-- ---------------------------------------------------------------------------
CREATE TABLE messages (
    message_id  BIGINT      PRIMARY KEY,
    guild_id    BIGINT      NOT NULL,
    channel_id  BIGINT      NOT NULL,
    user_id     BIGINT      NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL
);

-- Scans serveur entier : classements messages, contributeurs actifs par
-- période, heatmap horaire, jour de la semaine.
CREATE INDEX idx_messages_guild_created ON messages (guild_id, created_at);
-- /channelstat : classement des salons, activité d'un salon dans le temps.
CREATE INDEX idx_messages_guild_channel_created ON messages (guild_id, channel_id, created_at);
-- /userstat : stats d'un membre, streak (jours consécutifs avec message).
CREATE INDEX idx_messages_guild_user_created ON messages (guild_id, user_id, created_at);

ALTER TABLE messages ENABLE ROW LEVEL SECURITY;


-- ---------------------------------------------------------------------------
-- voice_sessions : une ligne par session vocale (join -> leave).
-- left_at NULL = session toujours en cours.
--
-- Calcul de durée sur une période arbitraire [window_start, window_end] :
-- il faut CLAMPER chaque session aux bornes de la fenêtre, pas juste filtrer
-- sur joined_at — sinon une session commencée avant la fenêtre (ou toujours
-- ouverte) serait sous-comptée ou carrément ignorée.
--
--   SELECT SUM(
--     EXTRACT(EPOCH FROM (
--       LEAST(COALESCE(left_at, now()), :window_end)
--       - GREATEST(joined_at, :window_start)
--     ))
--   )
--   FROM voice_sessions
--   WHERE guild_id = :guild_id
--     AND joined_at < :window_end
--     AND COALESCE(left_at, now()) > :window_start
-- ---------------------------------------------------------------------------
CREATE TABLE voice_sessions (
    session_id  BIGSERIAL   PRIMARY KEY,
    guild_id    BIGINT      NOT NULL,
    channel_id  BIGINT      NOT NULL,
    user_id     BIGINT      NOT NULL,
    joined_at   TIMESTAMPTZ NOT NULL,
    left_at     TIMESTAMPTZ NULL,
    CONSTRAINT chk_voice_sessions_order CHECK (left_at IS NULL OR left_at >= joined_at)
);

CREATE INDEX idx_voice_sessions_guild_joined  ON voice_sessions (guild_id, joined_at);
CREATE INDEX idx_voice_sessions_guild_user    ON voice_sessions (guild_id, user_id, joined_at);
CREATE INDEX idx_voice_sessions_guild_channel ON voice_sessions (guild_id, channel_id, joined_at);
-- Retrouver rapidement "y a-t-il déjà une session ouverte pour ce membre ?"
-- (utile pour la réconciliation au démarrage du bot après un redéploiement).
CREATE INDEX idx_voice_sessions_open ON voice_sessions (guild_id, user_id) WHERE left_at IS NULL;

ALTER TABLE voice_sessions ENABLE ROW LEVEL SECURITY;


-- ---------------------------------------------------------------------------
-- members : petite table dénormalisée pour 3 informations qui ne se
-- dérivent pas facilement en live depuis l'API Discord ou les autres tables.
--   - first_message_at : aucun équivalent live, uniquement dérivable de
--     l'historique événementiel.
--   - guild_joined_at : duplique discord.Member.joined_at, mais survit aux
--     membres qui quittent ensuite le serveur.
--   - last_activity_at : évite un MAX() sur 4 tables à chaque lecture.
-- ---------------------------------------------------------------------------
CREATE TABLE members (
    guild_id          BIGINT      NOT NULL,
    user_id           BIGINT      NOT NULL,
    first_message_at  TIMESTAMPTZ NULL,
    guild_joined_at   TIMESTAMPTZ NULL,
    last_activity_at  TIMESTAMPTZ NULL,
    PRIMARY KEY (guild_id, user_id)
);

ALTER TABLE members ENABLE ROW LEVEL SECURITY;


-- ---------------------------------------------------------------------------
-- emoji_events : log unifié des réactions ajoutées ET des emojis utilisés
-- dans le texte des messages — même forme d'événement dans les deux cas,
-- seule la colonne `source` change. Ça permet "top emoji tous usages
-- confondus" en un seul GROUP BY, sans UNION entre deux tables.
--
-- Emoji custom Discord : emoji_id + emoji_name + is_animated renseignés,
-- is_custom = true. Emoji unicode : emoji_id NULL, emoji_name = le
-- caractère unicode lui-même, is_custom = false.
--
-- Note : les réactions n'ont pas de timestamp fourni par Discord
-- (RawReactionActionEvent n'en expose pas) — created_at pour
-- source='reaction' est donc l'heure de traitement côté bot (décalage de
-- l'ordre de la milliseconde, sans impact à la granularité horaire utilisée
-- par les stats).
-- ---------------------------------------------------------------------------
CREATE TABLE emoji_events (
    event_id     BIGSERIAL   PRIMARY KEY,
    guild_id     BIGINT      NOT NULL,
    channel_id   BIGINT      NOT NULL,
    message_id   BIGINT      NOT NULL,
    user_id      BIGINT      NOT NULL,
    source       TEXT        NOT NULL CHECK (source IN ('reaction', 'message')),
    emoji_id     BIGINT      NULL,
    emoji_name   TEXT        NOT NULL,
    is_custom    BOOLEAN     NOT NULL DEFAULT false,
    is_animated  BOOLEAN     NOT NULL DEFAULT false,
    created_at   TIMESTAMPTZ NOT NULL
);

CREATE INDEX idx_emoji_events_guild_user_created   ON emoji_events (guild_id, user_id, created_at);
CREATE INDEX idx_emoji_events_guild_source_created ON emoji_events (guild_id, source, created_at);
CREATE INDEX idx_emoji_events_guild_emoji          ON emoji_events (guild_id, emoji_id, emoji_name);

ALTER TABLE emoji_events ENABLE ROW LEVEL SECURITY;


-- ---------------------------------------------------------------------------
-- invites : miroir en cache des invitations du serveur (guild.invites() +
-- guild.vanity_invite()), rafraîchi via on_invite_create / on_invite_delete
-- / on_member_join. Nécessite que le bot ait la permission "Gérer le
-- serveur" (manage_guild) — pas seulement un intent.
-- ---------------------------------------------------------------------------
CREATE TABLE invites (
    invite_code  TEXT        PRIMARY KEY,
    guild_id     BIGINT      NOT NULL,
    inviter_id   BIGINT      NULL,
    uses         INT         NOT NULL DEFAULT 0,
    max_uses     INT         NULL,
    is_vanity    BOOLEAN     NOT NULL DEFAULT false,
    created_at   TIMESTAMPTZ NULL,
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_invites_guild ON invites (guild_id);

ALTER TABLE invites ENABLE ROW LEVEL SECURITY;


-- ---------------------------------------------------------------------------
-- invite_uses : log "qui a rejoint via quelle invitation / quel inviteur".
-- invite_code / inviter_id NULL quand l'attribution n'a pas pu être résolue
-- (lien de découverte, arrivée via widget, race entre deux joins simultanés,
-- invitation vanity sans inviteur identifiable...) — le join est quand même
-- enregistré, juste sans attribution.
-- ---------------------------------------------------------------------------
CREATE TABLE invite_uses (
    id           BIGSERIAL   PRIMARY KEY,
    guild_id     BIGINT      NOT NULL,
    invite_code  TEXT        NULL REFERENCES invites (invite_code) ON DELETE SET NULL,
    inviter_id   BIGINT      NULL,
    member_id    BIGINT      NOT NULL,
    joined_at    TIMESTAMPTZ NOT NULL
);

CREATE INDEX idx_invite_uses_guild_inviter ON invite_uses (guild_id, inviter_id, joined_at);
CREATE INDEX idx_invite_uses_guild_member  ON invite_uses (guild_id, member_id, joined_at);

ALTER TABLE invite_uses ENABLE ROW LEVEL SECURITY;


-- ---------------------------------------------------------------------------
-- command_usage : un enregistrement par commande slash utilisée. Compte les
-- tentatives (déclenché avant les vérifications de permission par commande),
-- pas seulement les succès — une /kick refusée pour permissions insuffisantes
-- compte quand même comme une utilisation de /kick.
-- ---------------------------------------------------------------------------
CREATE TABLE command_usage (
    id            BIGSERIAL   PRIMARY KEY,
    guild_id      BIGINT      NULL,
    user_id       BIGINT      NOT NULL,
    command_name  TEXT        NOT NULL,
    used_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_command_usage_guild_command ON command_usage (guild_id, command_name, used_at);
CREATE INDEX idx_command_usage_guild_user    ON command_usage (guild_id, user_id, used_at);

ALTER TABLE command_usage ENABLE ROW LEVEL SECURITY;


-- ---------------------------------------------------------------------------
-- boosts : une ligne par période de boost serveur (même forme que
-- voice_sessions). unboosted_at NULL = boost actif. Détecté en diffant
-- member.premium_since entre deux on_member_update.
-- ---------------------------------------------------------------------------
CREATE TABLE boosts (
    boost_id      BIGSERIAL   PRIMARY KEY,
    guild_id      BIGINT      NOT NULL,
    user_id       BIGINT      NOT NULL,
    boosted_at    TIMESTAMPTZ NOT NULL,
    unboosted_at  TIMESTAMPTZ NULL
);

CREATE INDEX idx_boosts_guild ON boosts (guild_id, boosted_at);
CREATE INDEX idx_boosts_open  ON boosts (guild_id, user_id) WHERE unboosted_at IS NULL;

ALTER TABLE boosts ENABLE ROW LEVEL SECURITY;
