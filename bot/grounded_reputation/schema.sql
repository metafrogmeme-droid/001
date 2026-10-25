-- Grounded Reputation v0.1. Postgres. Ten tables.
-- block_time / from_time / to_time are unix seconds. NULL means the source
-- did not provide a time. A missing time is not stored as zero.
-- wallet NULL means the agent wallet was cleared, not that it is the zero address.
-- payment_pk NULL is allowed more than once. A non-null payment_pk is unique,
-- so one payment backs at most one rating.

CREATE TABLE IF NOT EXISTS agents (
    chain_id bigint NOT NULL,
    agent_id numeric NOT NULL,
    owner text,
    agent_uri text,
    registered_block bigint,
    PRIMARY KEY (chain_id, agent_id)
);

CREATE TABLE IF NOT EXISTS agent_wallet_history (
    chain_id bigint NOT NULL,
    agent_id numeric NOT NULL,
    wallet text,
    from_block bigint NOT NULL,
    to_block bigint,
    from_time bigint,
    to_time bigint
);

CREATE TABLE IF NOT EXISTS feedback (
    chain_id bigint NOT NULL,
    agent_id numeric NOT NULL,
    client text NOT NULL,
    feedback_index bigint NOT NULL,
    value numeric,
    value_decimals integer,
    tag1 text,
    tag2 text,
    endpoint text,
    feedback_uri text,
    feedback_hash text,
    block bigint,
    block_time bigint,
    tx_hash text,
    revoked boolean NOT NULL DEFAULT false,
    PRIMARY KEY (chain_id, agent_id, client, feedback_index)
);

CREATE TABLE IF NOT EXISTS feedback_files (
    feedback_pk text PRIMARY KEY,
    fetched_at timestamptz,
    content_sha text,
    keccak_ok boolean,
    raw jsonb,
    pop_from text,
    pop_to text,
    pop_chain bigint,
    pop_txhash text,
    pop_nonce text
);

CREATE TABLE IF NOT EXISTS payments (
    chain_id bigint NOT NULL,
    tx_hash text NOT NULL,
    log_index integer NOT NULL,
    token text,
    payer text,
    payee text,
    amount numeric,
    decimals integer,
    block bigint,
    block_time bigint,
    method text NOT NULL CHECK (method IN ('eip3009', 'permit2', 'plain')),
    facilitator text,
    auth_nonce text,
    PRIMARY KEY (chain_id, tx_hash, log_index)
);

CREATE TABLE IF NOT EXISTS feedback_payment_match (
    feedback_pk text PRIMARY KEY,
    payment_pk text UNIQUE,
    class text NOT NULL CHECK (class IN (
        'backed_direct',
        'backed_escrow_unattributed',
        'proof_failed',
        'proof_tampered',
        'no_proof',
        'uri_unresolvable'
    )),
    reason text,
    checked_at timestamptz,
    rule_version text NOT NULL
);

CREATE TABLE IF NOT EXISTS address_funding (
    chain_id bigint NOT NULL,
    address text NOT NULL,
    first_funder text,
    first_fund_block bigint,
    label text,
    PRIMARY KEY (chain_id, address)
);

CREATE TABLE IF NOT EXISTS clusters (
    cluster_id text NOT NULL,
    method text NOT NULL,
    root text,
    member text NOT NULL,
    chain_id bigint NOT NULL
);

CREATE TABLE IF NOT EXISTS known_escrows (
    chain_id bigint NOT NULL,
    address text NOT NULL,
    protocol text NOT NULL,
    attribution_method text NOT NULL,
    PRIMARY KEY (chain_id, address)
);

CREATE TABLE IF NOT EXISTS snapshots (
    id bigserial PRIMARY KEY,
    agent_pk text NOT NULL,
    counts jsonb NOT NULL,
    seal_hash text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

INSERT INTO known_escrows (chain_id, address, protocol, attribution_method)
SELECT 8453, '0xef4364fe4487353df46eb7c811d4fac78b856c7f', 'acp', 'unattributed'
WHERE NOT EXISTS (
    SELECT 1 FROM known_escrows
    WHERE chain_id = 8453
      AND address = '0xef4364fe4487353df46eb7c811d4fac78b856c7f'
);
