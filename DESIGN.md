# Spécification technique — Système économique

## 1. Transfert P2P : `/pay <user> <amount>`

### 1.1 Objectif
Permettre à un membre d'envoyer des coins à un autre membre du même serveur, de façon atomique et idempotente.

### 1.2 Workflow

```
Utilisateur A → /pay @B 50
  1. Valide que A ≠ B
  2. Valide que amount > 0
  3. Valide que A a solde >= amount (incluant vocal en cours)
  4. Appelle la RPC transfer_coins (atomique)
  5. Répond avec confirmation + scooby_quote
```

### 1.3 Schéma base de données

```sql
CREATE TABLE IF NOT EXISTS coin_transfers (
    transfer_id   UUID PRIMARY KEY,
    guild_id      BIGINT NOT NULL,
    from_user_id  BIGINT NOT NULL,
    to_user_id    BIGINT NOT NULL,
    amount        NUMERIC(20, 2) NOT NULL CHECK (amount > 0),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (from_user_id <> to_user_id)
);

CREATE INDEX IF NOT EXISTS coin_transfers_guild_user_idx
    ON coin_transfers (guild_id, from_user_id, created_at DESC);
```

### 1.4 RPC `transfer_coins`

| Paramètre | Type | Description |
|---|---|---|
| p_transfer_id | UUID | ID unique de transfert (idempotence) |
| p_guild_id | BIGINT | Serveur |
| p_from_user_id | BIGINT | Expéditeur |
| p_to_user_id | BIGINT | Destinataire |
| p_amount | NUMERIC(20,2) | Montant (strictement positif) |

**Logique :**
1. Vérifier qu'un transfert avec ce `transfer_id` existe déjà dans `coin_transfers`. Si oui, retourner simplement (idempotent).
2. `SELECT ... FOR UPDATE` sur les deux lignes `coin_balances` (from puis to) — ordre déterministe par `(guild_id, user_id)` pour éviter les deadlocks.
3. Matérialiser le vocal en cours de l'expéditeur (comme dans `play_slots`).
4. Vérifier `balance >= amount`.
5. Débiter l'expéditeur : `balance = balance - amount`.
6. Créditer le destinataire : `balance = balance + amount`.
7. Insérer la ligne `coin_transfers`.
8. Retourner les nouveaux soldes.

**Source de vérité idempotente :** `transfer_id` (UUID généré par le bot).

### 1.5 Validation côté bot

- `A == B` → refus immédiat
- `amount <= 0` → refus immédiat
- `solde < amount` (lecture `get_coin_balance`) → refus avec message
- Erreur `INSUFFICIENT_COINS` de la RPC (solde changé entre-temps, ex. vocal) → message explicite

---

## 2. Système de prêts P2P

### 2.1 Cycle de vie d'un prêt

```
INITIATED → ACCEPTED → ACTIVE → CLOSED
                              ↘ DEFAULTED
```

| Status | Définition |
|---|---|
| `pending` | Offre créée par le prêteur, pas encore acceptée |
| `active` | Accepté par l'emprunteur, fonds transférés |
| `closed` | Remboursé intégralement (principal + intérêts) |
| `defaulted` | Non remboursé après `DEFAULT_DAYS` jours |

### 2.2 Commandes

#### `/loan <user> <amount> <interest_percentage>`

| Paramètre | Type | Contrainte |
|---|---|---|
| user | discord.Member | ≠ bot ≠ soi-même |
| amount | float | 1–100 000, <= solde du prêteur |
| interest_percentage | float | 0–1000 (0 = sans intérêt) |

**Logique :**
1. Valide les paramètres.
2. Vérifie que l'emprunteur n'a pas déjà un prêt `pending` ou `active` avec ce prêteur.
3. Vérifie le solde du prêteur (déduit immédiatement et séquestré dans `loan_escrow` ou simplement marqué comme réservé).
4. **Ne débite pas encore** — crée une ligne `loans` avec `status = 'pending'`.
5. Répond : « Prêt de X coins proposé à @user à Y %. Il/elle doit utiliser `/loanaccept` pour recevoir les fonds. »

#### `/loanaccept`

**Logique :**
1. Cherche la plus récente ligne `loans` où `borrower_id = interaction.user.id` et `status = 'pending'` dans la guild.
2. Si aucune → « Aucune offre de prêt en attente. »
3. Appelle la RPC `accept_loan` qui atomiquement :
   a. Vérifie que la ligne `loans` est toujours `pending`.
   b. `SELECT ... FOR UPDATE` sur le solde du prêteur.
   c. Matérialise le vocal du prêteur.
   d. Vérifie que le prêteur a encore assez.
   e. Vérifie le vocal de l'emprunteur (pour que son `balance` reflète son vrai solde à l'instant T, même si on ne débite que le prêteur).
   f. Débite le prêteur, crédite l'emprunteur.
   g. Passe `status = 'active'`, enregistre `accepted_at`.
   h. Calcule `total_owed = principal * (1 + interest_rate / 100)`.
4. Répond avec confirmation.

#### `/loanpay [amount]`

| Paramètre | Type | Défaut |
|---|---|---|
| amount | float | `total_owed` (remboursement total) |

**Logique :**
1. Cherche les prêts `active` où `borrower_id = interaction.user.id`.
2. Si plusieurs, liste les prêts actifs et demande de préciser (ou utilise une sélection via un `autocomplete`).
3. Valide que `amount > 0` et `amount <= total_owed`.
4. Appelle la RPC `repay_loan` qui atomiquement :
   a. `SELECT ... FOR UPDATE` sur le solde de l'emprunteur.
   b. Matérialise son vocal en cours.
   c. Vérifie `balance >= amount`.
   d. Débite l'emprunteur, crédite le prêteur.
   e. Met à jour `total_owed -= amount`.
   f. Si `total_owed <= 0` → `status = 'closed'`.
5. Répond avec le montant restant dû, ou "Prêt remboursé intégralement !"

### 2.3 Schéma base de données

```sql
CREATE TABLE IF NOT EXISTS loans (
    loan_id          UUID PRIMARY KEY,
    guild_id         BIGINT NOT NULL,
    lender_id        BIGINT NOT NULL,
    borrower_id      BIGINT NOT NULL,
    principal        NUMERIC(20, 2) NOT NULL CHECK (principal > 0),
    interest_rate    NUMERIC(8, 4) NOT NULL CHECK (interest_rate >= 0),
    total_owed       NUMERIC(20, 2) NOT NULL,
    status           TEXT NOT NULL DEFAULT 'pending'
                         CHECK (status IN ('pending', 'active', 'closed', 'defaulted')),
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    accepted_at      TIMESTAMPTZ,
    closed_at        TIMESTAMPTZ,
    CHECK (lender_id <> borrower_id),
    CHECK (total_owed >= 0),
    CHECK (accepted_at IS NULL OR status IN ('active', 'closed', 'defaulted')),
    CHECK (closed_at IS NULL OR status IN ('closed', 'defaulted'))
);

CREATE INDEX IF NOT EXISTS loans_borrower_status_idx
    ON loans (guild_id, borrower_id, status);

CREATE INDEX IF NOT EXISTS loans_lender_status_idx
    ON loans (guild_id, lender_id, status);
```

### 2.4 RPC `accept_loan`

| Paramètre | Type |
|---|---|
| p_loan_id | UUID |
| p_guild_id | BIGINT |
| p_lender_id | BIGINT |
| p_borrower_id | BIGINT |

**Logique :** voir 2.2 — débite le prêteur, crédite l'emprunteur, passe `status = 'active'`.

### 2.5 RPC `repay_loan`

| Paramètre | Type |
|---|---|
| p_loan_id | UUID |
| p_guild_id | BIGINT |
| p_borrower_id | BIGINT |
| p_lender_id | BIGINT |
| p_amount | NUMERIC(20,2) |

### 2.6 Gestion des défauts de paiement

Les prêts ont une durée implicite de `DEFAULT_DAYS = 90` jours. Le bot ne forcera pas le remboursement, mais :

- À chaque exécution (`/balance`, `/slots`, etc.), un check non-bloquant est fait.
- Un mécanisme de cron (ou vérification au démarrage dans `on_ready`) marque les prêts `active` avec `accepted_at + 90j < now()` → `defaulted`.
- Un prêt `defaulted` est visible par le prêteur via une future commande `/loans list`.

Note : la première version n'inclut pas de cron. Les prêts ne passent pas automatiquement en défaut ; un admin peut les clôturer manuellement plus tard.

---

## 3. Commande d'aide : `/eco`

### 3.1 Signature

`/eco` — Aucun paramètre. Guild-only.

### 3.2 Réponse

Embed Discord avec couleurs et champs listant toutes les commandes d'économie accessibles à l'utilisateur :

```
📊 Économie — ScoobyBot

Gains
  💬 Message       0,5 coin par message valide (anti-spam)
  🎤 Vocal         5 coins/heure au prorata

Commandes
  💰 /balance      Affiche ton solde de coins
  🎰 /slots [mise]  Machine à sous (mise: 1, 5, 10, 100)
  💸 /pay [user] [amount]  Envoie des coins à un membre
  📋 /loans [subcmd]  Système de prêts (/loans offer, /loans accept, /loans pay, /loans list)

Footer: "Propulsé par ScoobyBot"
```

Note : `/loans` est un groupe de commandes (slash command group) qui regroupe `/loans offer`, `/loans accept`, `/loans pay`, `/loans list`.

