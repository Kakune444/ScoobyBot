-- ScoobyBot — système économie / coins
-- À exécuter dans Supabase après le schéma existant des statistiques.
-- Une ligne dans coin_balances = un membre sur un serveur.

CREATE TABLE IF NOT EXISTS coin_balances (
    guild_id     BIGINT NOT NULL,
    user_id      BIGINT NOT NULL,
    balance      NUMERIC(20, 2) NOT NULL DEFAULT 0,
    transactions JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (guild_id, user_id)
);

ALTER TABLE coin_balances ENABLE ROW LEVEL SECURITY;

-- Migration de l'ancien ledger séparé, s'il existe déjà dans la base.
DO $$
BEGIN
    IF to_regclass('public.coin_transactions') IS NOT NULL THEN
        EXECUTE $migration$
            INSERT INTO coin_balances (guild_id, user_id, balance, transactions)
            SELECT
                guild_id,
                user_id,
                SUM(amount),
                jsonb_object_agg(
                    reason || ':' || source_id,
                    jsonb_build_object(
                        'transaction_id', reason || ':' || source_id,
                        'amount', amount,
                        'reason', reason,
                        'source_id', source_id,
                        'created_at', created_at
                    )
                )
            FROM public.coin_transactions
            GROUP BY guild_id, user_id
            ON CONFLICT (guild_id, user_id) DO UPDATE SET
                balance = EXCLUDED.balance,
                transactions = EXCLUDED.transactions,
                updated_at = now()
        $migration$;
    END IF;
END;
$$ LANGUAGE plpgsql;

-- Migration de l'ancien champ members.coins, s'il existe.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'members'
          AND column_name = 'coins'
    ) THEN
        INSERT INTO coin_balances (guild_id, user_id, balance)
        SELECT guild_id, user_id, coins
        FROM members
        WHERE coins <> 0
        ON CONFLICT (guild_id, user_id) DO NOTHING;
    END IF;
END;
$$ LANGUAGE plpgsql;

DROP TABLE IF EXISTS coin_transactions CASCADE;
ALTER TABLE members DROP COLUMN IF EXISTS coins;

CREATE OR REPLACE FUNCTION award_coins(
    p_guild_id BIGINT,
    p_user_id BIGINT,
    p_amount NUMERIC,
    p_reason TEXT,
    p_source_id TEXT
) RETURNS NUMERIC AS $$
DECLARE
    v_key TEXT := p_reason || ':' || p_source_id;
    v_amount NUMERIC(20, 2) := ROUND(p_amount, 2);
    v_balance NUMERIC;
    v_transactions JSONB;
BEGIN
    IF v_amount <= 0 THEN
        RAISE EXCEPTION 'Le montant de coins doit être positif';
    END IF;

    INSERT INTO coin_balances (guild_id, user_id)
    VALUES (p_guild_id, p_user_id)
    ON CONFLICT (guild_id, user_id) DO NOTHING;

    SELECT balance
    INTO v_balance
    FROM coin_balances
    WHERE guild_id = p_guild_id AND user_id = p_user_id
    FOR UPDATE;

    IF v_transactions ? v_key THEN
        RETURN v_balance;
    END IF;

    UPDATE coin_balances
    SET balance = balance + v_amount,
        transactions = transactions || jsonb_build_object(
            v_key,
            jsonb_build_object(
                'transaction_id', v_key,
                'amount', v_amount,
                'reason', p_reason,
                'source_id', p_source_id,
                'created_at', now()
            )
        ),
        updated_at = now()
    WHERE guild_id = p_guild_id AND user_id = p_user_id
    RETURNING balance INTO v_balance;

    RETURN v_balance;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION close_voice_session_and_award(
    p_guild_id BIGINT,
    p_user_id BIGINT,
    p_channel_id BIGINT,
    p_left_at TIMESTAMPTZ,
    p_coins_per_hour NUMERIC DEFAULT 5
) RETURNS void AS $$
DECLARE
    v_session voice_sessions%ROWTYPE;
    v_amount NUMERIC(20, 2);
BEGIN
    FOR v_session IN
        UPDATE voice_sessions
        SET left_at = p_left_at
        WHERE guild_id = p_guild_id
          AND user_id = p_user_id
          AND channel_id = p_channel_id
          AND left_at IS NULL
        RETURNING *
    LOOP
        v_amount := ROUND(
            EXTRACT(EPOCH FROM (p_left_at - v_session.joined_at)) * p_coins_per_hour / 3600,
            2
        );
        IF v_amount > 0 THEN
            PERFORM award_coins(
                p_guild_id,
                p_user_id,
                v_amount,
                'voice',
                v_session.session_id::TEXT
            );
        END IF;
    END LOOP;
END;
$$ LANGUAGE plpgsql;

-- Le temps de la session vocale en cours est visible immédiatement dans
-- /balance. La transaction JSONB est, elle, ajoutée définitivement à la
-- fermeture de la session par close_voice_session_and_award.
CREATE OR REPLACE FUNCTION get_coin_balance(
    p_guild_id BIGINT,
    p_user_id BIGINT
) RETURNS NUMERIC AS $$
    SELECT
        COALESCE((
            SELECT balance
            FROM coin_balances
            WHERE guild_id = p_guild_id AND user_id = p_user_id
        ), 0)
        + COALESCE((
            SELECT SUM(
                EXTRACT(EPOCH FROM (now() - joined_at)) * 5 / 3600
            )
            FROM voice_sessions
            WHERE guild_id = p_guild_id
              AND user_id = p_user_id
              AND left_at IS NULL
        ), 0);
$$ LANGUAGE sql STABLE;

-- Parties de machine à sous. game_id est fourni par le bot et rend la RPC
-- idempotente : une nouvelle tentative réseau de la même partie ne rejoue pas
-- le débit/crédit.
CREATE TABLE IF NOT EXISTS slot_games (
    game_id    UUID PRIMARY KEY,
    guild_id   BIGINT NOT NULL,
    user_id    BIGINT NOT NULL,
    bet        NUMERIC(20, 2) NOT NULL CHECK (bet IN (1, 5, 10, 100)),
    reel_1     TEXT NOT NULL,
    reel_2     TEXT NOT NULL,
    reel_3     TEXT NOT NULL,
    result     TEXT NOT NULL,
    payout     NUMERIC(20, 2) NOT NULL CHECK (payout >= 0),
    net        NUMERIC(20, 2) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (net = payout - bet)
);

CREATE INDEX IF NOT EXISTS slot_games_guild_user_created_idx
    ON slot_games (guild_id, user_id, created_at DESC);

ALTER TABLE slot_games ENABLE ROW LEVEL SECURITY;

CREATE OR REPLACE FUNCTION play_slots(
    p_game_id UUID,
    p_guild_id BIGINT,
    p_user_id BIGINT,
    p_bet NUMERIC,
    p_reel_1 TEXT,
    p_reel_2 TEXT,
    p_reel_3 TEXT,
    p_result TEXT,
    p_payout NUMERIC
) RETURNS TABLE(new_balance NUMERIC, payout NUMERIC, net NUMERIC) AS $$
DECLARE
    v_existing slot_games%ROWTYPE;
    v_balance NUMERIC;
    v_payout NUMERIC(20, 2) := ROUND(p_payout, 2);
    v_net NUMERIC(20, 2);
    v_expected_payout NUMERIC(20, 2);
    v_expected_result TEXT;
    v_key TEXT := 'slots:' || p_game_id::TEXT;
    v_now TIMESTAMPTZ := now();
    v_voice_amount NUMERIC(20, 2);
    v_voice_key TEXT;
    v_session voice_sessions%ROWTYPE;
BEGIN
    -- Idempotence : une requête répétée pour le même game_id ne touche plus
    -- au solde et renvoie le résultat déjà enregistré.
    SELECT *
    INTO v_existing
    FROM slot_games
    WHERE game_id = p_game_id;

    IF FOUND THEN
        SELECT balance
        INTO v_balance
        FROM coin_balances
        WHERE guild_id = v_existing.guild_id AND user_id = v_existing.user_id;

        RETURN QUERY SELECT v_balance, v_existing.payout, v_existing.net;
        RETURN;
    END IF;

    IF p_bet NOT IN (1, 5, 10, 100) THEN
        RAISE EXCEPTION 'INVALID_SLOT_BET';
    END IF;
    IF v_payout < 0 OR v_payout > p_bet * 30 THEN
        RAISE EXCEPTION 'INVALID_SLOT_PAYOUT';
    END IF;
    IF p_reel_1 NOT IN ('🍒', '🍋', '🍇', '🔔', '💎', '7️⃣')
       OR p_reel_2 NOT IN ('🍒', '🍋', '🍇', '🔔', '💎', '7️⃣')
       OR p_reel_3 NOT IN ('🍒', '🍋', '🍇', '🔔', '💎', '7️⃣') THEN
        RAISE EXCEPTION 'INVALID_SLOT_SYMBOL';
    END IF;

    IF p_reel_1 = p_reel_2 AND p_reel_2 = p_reel_3 THEN
        v_expected_payout := ROUND(p_bet * CASE p_reel_1
            WHEN '💎' THEN 10
            WHEN '7️⃣' THEN 30
            ELSE 4
        END, 2);
        v_expected_result := 'triple_' || p_reel_1;
    ELSIF p_reel_1 = p_reel_2
       OR p_reel_1 = p_reel_3
       OR p_reel_2 = p_reel_3 THEN
        v_expected_payout := ROUND(p_bet * 1.6, 2);
        v_expected_result := 'pair';
    ELSE
        v_expected_payout := 0;
        v_expected_result := 'loss';
    END IF;

    IF v_payout <> v_expected_payout OR p_result <> v_expected_result THEN
        RAISE EXCEPTION 'INVALID_SLOT_RESULT';
    END IF;

    -- Même ordre de verrous que close_voice_session_and_award (vocal puis
    -- solde), afin de pouvoir dépenser le temps vocal déjà écoulé sans créer
    -- de deadlock au moment où un membre quitte le vocal.
    FOR v_session IN
        SELECT *
        FROM voice_sessions
        WHERE guild_id = p_guild_id
          AND user_id = p_user_id
          AND left_at IS NULL
        FOR UPDATE
    LOOP
        NULL;
    END LOOP;

    INSERT INTO coin_balances (guild_id, user_id)
    VALUES (p_guild_id, p_user_id)
    ON CONFLICT (guild_id, user_id) DO NOTHING;

    SELECT balance
    INTO v_balance
    FROM coin_balances
    WHERE guild_id = p_guild_id AND user_id = p_user_id
    FOR UPDATE;

    -- Deux appels concurrents avec le même identifiant peuvent avoir passé le
    -- premier SELECT avant que le premier ne valide sa partie. Le verrou du
    -- solde permet de refaire ce contrôle avant tout nouveau débit.
    SELECT *
    INTO v_existing
    FROM slot_games
    WHERE game_id = p_game_id;

    IF FOUND THEN
        RETURN QUERY SELECT v_balance, v_existing.payout, v_existing.net;
        RETURN;
    END IF;

    -- Le solde affiché par /balance inclut le vocal en cours. On le matérialise
    -- ici avant le contrôle de mise, puis on avance joined_at pour ne jamais
    -- comptabiliser deux fois la même durée à la déconnexion.
    FOR v_session IN
        SELECT *
        FROM voice_sessions
        WHERE guild_id = p_guild_id
          AND user_id = p_user_id
          AND left_at IS NULL
        FOR UPDATE
    LOOP
        v_voice_amount := ROUND(
            EXTRACT(EPOCH FROM (v_now - v_session.joined_at)) * 5 / 3600,
            2
        );
        IF v_voice_amount > 0 THEN
            v_voice_key := 'voice:' || v_session.session_id::TEXT || ':' || p_game_id::TEXT;
            UPDATE coin_balances
            SET balance = balance + v_voice_amount,
                transactions = transactions || jsonb_build_object(
                    v_voice_key,
                    jsonb_build_object(
                        'transaction_id', v_voice_key,
                        'amount', v_voice_amount,
                        'reason', 'voice',
                        'source_id', v_session.session_id::TEXT,
                        'created_at', v_now
                    )
                ),
                updated_at = v_now
            WHERE guild_id = p_guild_id AND user_id = p_user_id;

            v_balance := v_balance + v_voice_amount;
            UPDATE voice_sessions
            SET joined_at = v_now
            WHERE session_id = v_session.session_id;
        END IF;
    END LOOP;

    IF v_balance < p_bet THEN
        RAISE EXCEPTION 'INSUFFICIENT_COINS';
    END IF;

    v_net := ROUND(v_payout - p_bet, 2);

    INSERT INTO slot_games (
        game_id, guild_id, user_id, bet,
        reel_1, reel_2, reel_3, result, payout, net
    ) VALUES (
        p_game_id, p_guild_id, p_user_id, ROUND(p_bet, 2),
        p_reel_1, p_reel_2, p_reel_3, p_result, v_payout, v_net
    );

    UPDATE coin_balances
    SET balance = balance + v_net,
        transactions = transactions || jsonb_build_object(
            v_key,
            jsonb_build_object(
                'transaction_id', v_key,
                'amount', v_net,
                'reason', 'slots',
                'source_id', p_game_id::TEXT,
                'bet', ROUND(p_bet, 2),
                'payout', v_payout,
                'net', v_net,
                'reels', jsonb_build_array(p_reel_1, p_reel_2, p_reel_3),
                'result', p_result,
                'created_at', now()
            )
        ),
        updated_at = now()
    WHERE guild_id = p_guild_id AND user_id = p_user_id
    RETURNING balance INTO v_balance;

    RETURN QUERY SELECT v_balance, v_payout, v_net;
END;
$$ LANGUAGE plpgsql;
