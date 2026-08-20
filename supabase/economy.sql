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

    SELECT balance, transactions
    INTO v_balance, v_transactions
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
