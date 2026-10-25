"""SQLite mirror of the Postgres schema.

Tests and the local admin index use sqlite. ``schema.sql`` is the Postgres
DDL. Column names match. A missing database file is not opened and not
created by a read.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from bot.grounded_reputation.addrs import norm_addr
from bot.grounded_reputation.chains import ACP_CHAIN_ID, ACP_PAYMENT_MANAGER

_DDL = Path(__file__).with_name("schema.sql")

_SQLITE = """
CREATE TABLE IF NOT EXISTS agents (
    chain_id INTEGER NOT NULL,
    agent_id TEXT NOT NULL,
    owner TEXT,
    agent_uri TEXT,
    registered_block INTEGER,
    PRIMARY KEY (chain_id, agent_id)
);

CREATE TABLE IF NOT EXISTS agent_wallet_history (
    chain_id INTEGER NOT NULL,
    agent_id TEXT NOT NULL,
    wallet TEXT,
    from_block INTEGER NOT NULL,
    to_block INTEGER,
    from_time INTEGER,
    to_time INTEGER
);

CREATE TABLE IF NOT EXISTS feedback (
    chain_id INTEGER NOT NULL,
    agent_id TEXT NOT NULL,
    client TEXT NOT NULL,
    feedback_index INTEGER NOT NULL,
    value TEXT,
    value_decimals INTEGER,
    tag1 TEXT,
    tag2 TEXT,
    endpoint TEXT,
    feedback_uri TEXT,
    feedback_hash TEXT,
    block INTEGER,
    block_time INTEGER,
    tx_hash TEXT,
    revoked INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (chain_id, agent_id, client, feedback_index)
);

CREATE TABLE IF NOT EXISTS feedback_files (
    feedback_pk TEXT PRIMARY KEY,
    fetched_at TEXT,
    content_sha TEXT,
    keccak_ok INTEGER,
    raw TEXT,
    pop_from TEXT,
    pop_to TEXT,
    pop_chain INTEGER,
    pop_txhash TEXT,
    pop_nonce TEXT
);

CREATE TABLE IF NOT EXISTS payments (
    chain_id INTEGER NOT NULL,
    tx_hash TEXT NOT NULL,
    log_index INTEGER NOT NULL,
    token TEXT,
    payer TEXT,
    payee TEXT,
    amount TEXT,
    decimals INTEGER,
    block INTEGER,
    block_time INTEGER,
    method TEXT NOT NULL CHECK (method IN ('eip3009', 'permit2', 'plain')),
    facilitator TEXT,
    auth_nonce TEXT,
    PRIMARY KEY (chain_id, tx_hash, log_index)
);

CREATE TABLE IF NOT EXISTS feedback_payment_match (
    feedback_pk TEXT PRIMARY KEY,
    payment_pk TEXT UNIQUE,
    class TEXT NOT NULL CHECK (class IN (
        'backed_direct',
        'backed_escrow_unattributed',
        'proof_failed',
        'proof_tampered',
        'no_proof',
        'uri_unresolvable'
    )),
    reason TEXT,
    checked_at TEXT,
    rule_version TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS address_funding (
    chain_id INTEGER NOT NULL,
    address TEXT NOT NULL,
    first_funder TEXT,
    first_fund_block INTEGER,
    label TEXT,
    PRIMARY KEY (chain_id, address)
);

CREATE TABLE IF NOT EXISTS clusters (
    cluster_id TEXT NOT NULL,
    method TEXT NOT NULL,
    root TEXT,
    member TEXT NOT NULL,
    chain_id INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS known_escrows (
    chain_id INTEGER NOT NULL,
    address TEXT NOT NULL,
    protocol TEXT NOT NULL,
    attribution_method TEXT NOT NULL,
    PRIMARY KEY (chain_id, address)
);

CREATE TABLE IF NOT EXISTS snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_pk TEXT NOT NULL,
    counts TEXT NOT NULL,
    seal_hash TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


def postgres_ddl() -> str:
    """The committed Postgres DDL, verbatim."""
    return _DDL.read_text(encoding="utf-8")


def connect(path: Path) -> sqlite3.Connection:
    """Open a writable database. The parent directory is created."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def open_read(path: Path) -> sqlite3.Connection | None:
    """Open an existing database read-only. A missing file is None."""
    if not path.is_file():
        return None
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def ensure(conn: sqlite3.Connection) -> None:
    """Create the mirror and seed the Base ACP escrow."""
    conn.executescript(_SQLITE)
    escrow = norm_addr(ACP_PAYMENT_MANAGER)
    conn.execute(
        """
        INSERT INTO known_escrows (chain_id, address, protocol, attribution_method)
        SELECT ?, ?, 'acp', 'unattributed'
        WHERE NOT EXISTS (
            SELECT 1 FROM known_escrows WHERE chain_id = ? AND address = ?
        )
        """,
        (ACP_CHAIN_ID, escrow, ACP_CHAIN_ID, escrow),
    )
    conn.commit()


def load_payload(conn: sqlite3.Connection, chain_id: int, agent_id: int) -> dict[str, object] | None:
    """The bundle for one indexed agent, or None when that agent has no row."""
    agent = conn.execute(
        "SELECT chain_id, agent_id, owner, agent_uri, registered_block FROM agents WHERE chain_id = ? AND agent_id = ?",
        (chain_id, str(agent_id)),
    ).fetchone()
    if agent is None:
        return None
    operators: list[str] = []
    feedbacks = []
    for row in conn.execute(
        """
        SELECT chain_id, agent_id, client, feedback_index, value, value_decimals, tag1, tag2,
               endpoint, feedback_uri, feedback_hash, block, block_time, tx_hash, revoked
        FROM feedback WHERE chain_id = ? AND agent_id = ?
        """,
        (chain_id, str(agent_id)),
    ):
        item = {key: row[key] for key in row.keys()}
        item["agent_id"] = agent_id
        item["revoked"] = bool(row["revoked"])
        item["file"] = _file_for(conn, chain_id, agent_id, row["client"], row["feedback_index"], row["feedback_uri"])
        feedbacks.append(item)
    spans = [
        {key: row[key] for key in row.keys()}
        for row in conn.execute(
            """
            SELECT wallet, from_block, to_block, from_time, to_time
            FROM agent_wallet_history WHERE chain_id = ? AND agent_id = ?
            """,
            (chain_id, str(agent_id)),
        )
    ]
    payments = []
    for row in conn.execute(
        """
        SELECT chain_id, tx_hash, log_index, token, payer, payee, amount, decimals, block,
               block_time, method, facilitator, auth_nonce
        FROM payments
        """
    ):
        item = {key: row[key] for key in row.keys()}
        item["raw_units"] = _amount(row["amount"])
        item["status_ok"] = True
        item["authorizer"] = row["payer"] if row["method"] == "eip3009" else None
        payments.append(item)
    funding = [
        {key: row[key] for key in row.keys()}
        for row in conn.execute(
            "SELECT chain_id, address, first_funder, first_fund_block, label FROM address_funding"
        )
    ]
    escrows = [
        {key: row[key] for key in row.keys()}
        for row in conn.execute(
            "SELECT chain_id, address, protocol, attribution_method FROM known_escrows"
        )
    ]
    return {
        "agent": {
            "chain_id": chain_id,
            "agent_id": agent_id,
            "owner": agent["owner"],
            "agent_uri": agent["agent_uri"],
            "registered_block": agent["registered_block"],
            "operators": operators,
        },
        "feedbacks": feedbacks,
        "payments": payments,
        "wallet_spans": spans,
        "funding": funding,
        "escrows": escrows,
        "history_readable": True,
    }


def _file_for(
    conn: sqlite3.Connection,
    chain_id: int,
    agent_id: int,
    client: object,
    index: object,
    uri: object,
) -> dict[str, object] | None:
    key = f"{chain_id}:{agent_id}:{client}:{index}"
    row = conn.execute(
        """
        SELECT keccak_ok, pop_from, pop_to, pop_chain, pop_txhash, pop_nonce
        FROM feedback_files WHERE feedback_pk = ?
        """,
        (key,),
    ).fetchone()
    if row is None:
        return None
    proof = None
    if row["pop_txhash"] or row["pop_nonce"]:
        proof = {
            "tx_hash": row["pop_txhash"],
            "nonce": row["pop_nonce"],
            "chain_id": row["pop_chain"],
            "from_address": row["pop_from"],
            "to_address": row["pop_to"],
        }
    keccak = None if row["keccak_ok"] is None else bool(row["keccak_ok"])
    return {
        "resolved": True,
        "ipfs": False,
        "keccak_ok": keccak,
        "proof": proof,
        "reason": None if keccak is not False else "hash_mismatch",
        "feedback_uri": uri,
    }


def _amount(value: object) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip() != "":
        try:
            return int(value, 10)
        except ValueError:
            return None
    return None


def save_snapshot(conn: sqlite3.Connection, agent_pk: str, counts_json: str, seal_hash: str) -> None:
    conn.execute(
        "INSERT INTO snapshots (agent_pk, counts, seal_hash) VALUES (?, ?, ?)",
        (agent_pk, counts_json, seal_hash),
    )
    conn.commit()
