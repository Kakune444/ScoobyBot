-- ScoobyBot — migration économie / coins
-- À exécuter dans Supabase après schema.sql.
-- Cette migration peut être relancée sur une base existante.

ALTER TABLE members
    ADD COLUMN IF NOT EXISTS coins NUMERIC(20, 2) NOT NULL DEFAULT 0;

CREATE TABLE IF NOT EXISTS coin_transactions (
    transaction_id BIGSERIAL PRIMARY KEY,
    guild_id       BIGINT NOT NULL,
    user_id        BIGINT NOT NULL,
    amount         NUMERIC(20, 2) NOT NULL CHECK (amount > 0),
    reason         TEXT NOT NULL CHECK (reason IN ('message', 'voice')),
    source_id      TEXT NOT NULL,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (guild_id, user_id, reason, source_id)
);

CREATE INDEX IF NOT EXISTS idx_coin_transactions_guild_user
    ON coin_transactions (guild_id, user_id, created_at);

ALTER TABLE coin_transactions ENABLE ROW LEVEL SECURITY;

CREATE OR REPLACE FUNCTION award_coins(
    p_guild_id BIGINT,
    p_user_id BIGINT,
    p_amount NUMERIC,
    p_reason TEXT,
    p_source_id TEXT
) RETURNS NUMERIC AS $$
DECLARE
    v_balance NUMERIC;
BEGIN
    IF p_amount <= 0 THEN
        RAISE EXCEPTION 'Le montant de coins doit être positif';
    END IF;

    INSERT INTO coin_transactions (guild_id, user_id, amount, reason, source_id)
    VALUES (p_guild_id, p_user_id, ROUND(p_amount, 2), p_reason, p_source_id)
    ON CONFLICT (guild_id, user_id, reason, source_id) DO NOTHING;

    IF FOUND THEN
        INSERT INTO members (guild_id, user_id, coins)
        VALUES (p_guild_id, p_user_id, ROUND(p_amount, 2))
        ON CONFLICT (guild_id, user_id) DO UPDATE
        SET coins = members.coins + EXCLUDED.coins;
    END IF;

    SELECT members.coins
    INTO v_balance
    FROM members
    WHERE members.guild_id = p_guild_id
      AND members.user_id = p_user_id;

    RETURN COALESCE(v_balance, 0);
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
    UPDATE voice_sessions
    SET left_at = p_left_at
    WHERE guild_id = p_guild_id
      AND user_id = p_user_id
      AND channel_id = p_channel_id
      AND left_at IS NULL
    RETURNING * INTO v_session;

    IF FOUND THEN
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
    END IF;
END;
$$ LANGUAGE plpgsql;
