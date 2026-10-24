-- Kink Cap reserve tape and quotes. Postgres. Nullable rate columns stay
-- null when a source did not produce them. A missing utilization is not
-- inserted as zero.

CREATE TABLE IF NOT EXISTS reserve_samples (
    ts_utc timestamptz NOT NULL,
    src text NOT NULL,
    token integer NOT NULL,
    total_supplied numeric NOT NULL,
    total_borrowed numeric NOT NULL,
    util numeric,
    borrow_rate numeric,
    supply_rate numeric,
    l1_block bigint
);

CREATE TABLE IF NOT EXISTS quotes (
    id bigserial PRIMARY KEY,
    series_id text NOT NULL,
    side text NOT NULL,
    notional numeric NOT NULL,
    premium numeric,
    model_ver text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
