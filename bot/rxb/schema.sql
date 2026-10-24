-- Recursion Breaker graph. Unreadable metrics are NULL, not zero.
-- Apply on Postgres. The bot does not open a database unless RXB_GRAPH_DSN is set.

CREATE TABLE tokens (
    addr bytea NOT NULL,
    chain integer NOT NULL,
    kind text NOT NULL CHECK (kind IN ('stable', 'yield', 'lst', 'wrapper', 'credit')),
    issuer text,
    nav_usd numeric,
    nav_src text,
    ts timestamptz NOT NULL,
    PRIMARY KEY (addr, chain, ts)
);

CREATE TABLE edges (
    src_token bytea NOT NULL,
    dst_token bytea,
    offchain_id text,
    usd numeric,
    kind text NOT NULL CHECK (kind IN ('holding', 'loan', 'collateral', 'mint_backing')),
    verif text NOT NULL CHECK (verif IN ('onchain', 'por', 'zk', 'signed', 'none')),
    first_seen timestamptz NOT NULL,
    last_seen timestamptz NOT NULL,
    CHECK (
        (dst_token IS NOT NULL AND offchain_id IS NULL)
        OR (dst_token IS NULL AND offchain_id IS NOT NULL)
    )
);

CREATE TABLE markets (
    chain integer NOT NULL,
    protocol text NOT NULL,
    id text NOT NULL,
    loan bytea,
    collateral bytea,
    lltv numeric,
    borrow_usd numeric,
    supply_usd numeric,
    ts timestamptz NOT NULL,
    PRIMARY KEY (chain, protocol, id, ts)
);

CREATE TABLE scc_runs (
    run_id text NOT NULL,
    ts timestamptz NOT NULL,
    scc_id text NOT NULL,
    members text[] NOT NULL,
    cbf numeric,
    uf numeric,
    lts numeric,
    dms numeric,
    fired boolean,
    PRIMARY KEY (run_id, scc_id)
);
