"""Grounded Reputation v0.1.

The flag is off unless it is 1 or true. A rating is backed only when the file
hash matches, the payer is the rater, the payee is the agent wallet at the
payment block, and the payment sits inside the window. One payment backs one
rating. An escrow payee is labelled and not treated as the agent wallet.
"""
from __future__ import annotations

import json
import sqlite3

import pytest

from bot.grounded_reputation import (
    ESCROW_LABEL,
    FOOTER,
    authorization_topic,
    build_reading,
    build_wallet_spans,
    chain_allowed,
    classify_feedbacks,
    connect,
    decode_receipt,
    default_escrows,
    ensure,
    flag_on,
    inspect_file,
    postgres_ddl,
    reading_from_payload,
    transfer_topic,
    usdc_token,
    wallet_at,
)
from bot.grounded_reputation.__main__ import main
from bot.grounded_reputation.chains import ACP_PAYMENT_MANAGER
from bot.grounded_reputation.reading import bucket_edges
from bot.grounded_reputation.wallet import is_clear_transfer
from bot.hrc.keccak import keccak256

CHAIN = 84532
CLIENT = "0x" + "11" * 20
WALLET = "0x" + "22" * 20
OWNER = "0x" + "33" * 20
OTHER = "0x" + "44" * 20
FACILITATOR = "0x" + "55" * 20
TX = "0x" + "ab" * 32
TX2 = "0x" + "cd" * 32
NONCE = "0x" + "ef" * 32
T0 = 1_700_000_000
WINDOW = 30 * 86400
ZERO = "0x" + "00" * 20


def _payment(**over: object) -> dict[str, object]:
    base: dict[str, object] = {
        "chain_id": CHAIN,
        "tx_hash": TX,
        "log_index": 1,
        "token": usdc_token(CHAIN),
        "payer": CLIENT,
        "payee": WALLET,
        "raw_units": 500_000,
        "decimals": 6,
        "block": 50,
        "block_time": T0,
        "method": "plain",
        "facilitator": None,
        "authorizer": None,
        "auth_nonce": None,
        "status_ok": True,
    }
    base.update(over)
    return base


def _proof(**over: object) -> dict[str, object]:
    base: dict[str, object] = {
        "tx_hash": TX,
        "nonce": None,
        "chain_id": CHAIN,
        "from_address": CLIENT,
        "to_address": WALLET,
    }
    base.update(over)
    return base


def _feedback(**over: object) -> dict[str, object]:
    proof = _proof()
    base: dict[str, object] = {
        "chain_id": CHAIN,
        "agent_id": 1,
        "client": CLIENT,
        "feedback_index": 0,
        "feedback_uri": "https://example.test/r.json",
        "feedback_hash": "0x" + "11" * 32,
        "block": 60,
        "block_time": T0 + 10,
        "revoked": False,
        "file": {
            "resolved": True,
            "ipfs": False,
            "keccak_ok": True,
            "proof": proof,
            "reason": None,
        },
    }
    base.update(over)
    return base


def _spans() -> list[dict[str, object]]:
    return [{
        "wallet": WALLET,
        "from_block": 1,
        "to_block": None,
        "from_time": T0 - 100,
        "to_time": None,
    }]


def _run(
    feedbacks: list[dict[str, object]],
    payments: list[dict[str, object]],
    spans: list[dict[str, object]] | None = None,
    escrows: list[dict[str, object]] | None = None,
    *,
    history: bool = True,
    window: int = WINDOW,
    agent_chain: int = CHAIN,
) -> list[dict[str, object]]:
    return classify_feedbacks(
        feedbacks,
        payments,
        _spans() if spans is None else spans,
        [] if escrows is None else escrows,
        window_seconds=window,
        history_readable=history,
        agent_chain_id=agent_chain,
    )


def _agent(chain: int = CHAIN) -> dict[str, object]:
    return {"chain_id": chain, "agent_id": 1, "owner": OWNER, "operators": []}


def test_flag_is_off_unless_the_value_is_1_or_true():
    assert flag_on({}) is False
    assert flag_on({"GROUNDED_REPUTATION_ENABLED": ""}) is False
    assert flag_on({"GROUNDED_REPUTATION_ENABLED": "0"}) is False
    assert flag_on({"GROUNDED_REPUTATION_ENABLED": "yes"}) is False
    assert flag_on({"GROUNDED_REPUTATION_ENABLED": "on"}) is False
    assert flag_on({"GROUNDED_REPUTATION_ENABLED": "1"}) is True
    assert flag_on({"GROUNDED_REPUTATION_ENABLED": " TRUE "}) is True


def test_mainnet_read_stays_off(capsys, monkeypatch):
    assert chain_allowed(84532, {}) is True
    assert chain_allowed(8453, {}) is False
    assert chain_allowed(8453, {"GROUNDED_REPUTATION_ALLOW_MAINNET_READ": "1"}) is True
    refused = reading_from_payload(
        {"agent": _agent(8453), "feedbacks": [], "payments": [], "wallet_spans": []},
        {},
    )
    assert refused["counts"] is None
    assert refused["error"] == "chain_refused"
    monkeypatch.delenv("GROUNDED_REPUTATION_ENABLED", raising=False)
    assert main(["prog", "classify"]) == 0
    body = json.loads(capsys.readouterr().out)
    assert body["error"] == "flag_off"
    assert body["counts"] is None


def test_hash_is_checked_before_the_proof_is_trusted():
    payload = {"proofOfPayment": {"txHash": TX, "chainId": CHAIN, "fromAddress": CLIENT, "toAddress": WALLET}}
    raw = json.dumps(payload).encode("utf-8")
    digest = "0x" + keccak256(raw).hex()
    good = inspect_file(raw, digest, "https://example.test/r.json")
    assert good["keccak_ok"] is True
    assert good["proof"]["tx_hash"] == TX
    bad = inspect_file(raw, "0x" + "22" * 32, "https://example.test/r.json")
    assert bad["keccak_ok"] is False
    assert bad["proof"] is None
    ipfs = inspect_file(raw, "0x" + "22" * 32, "ipfs://bafytest")
    assert ipfs["keccak_ok"] is None
    assert ipfs["proof"]["tx_hash"] == TX
    tampered = _feedback()
    tampered["file"] = {
        "resolved": True,
        "ipfs": False,
        "keccak_ok": False,
        "proof": _proof(),
        "reason": "hash_mismatch",
    }
    row = _run([tampered], [_payment()])[0]
    assert row["class"] == "proof_tampered"
    assert row["reason"] == "hash_mismatch"
    assert row["payment_key"] is None


def test_a_matching_payment_is_backed_and_a_missing_proof_is_not_zero_backed():
    row = _run([_feedback()], [_payment()])[0]
    assert row["class"] == "backed_direct"
    assert row["reason"] is None
    missing = _feedback()
    missing["file"] = {"resolved": True, "ipfs": False, "keccak_ok": True, "proof": None, "reason": None}
    none = _run([missing], [_payment()])[0]
    assert none["class"] == "no_proof"
    unread = _feedback()
    unread["file"] = {"resolved": False, "ipfs": False, "keccak_ok": None, "proof": None, "reason": "fetch_failed"}
    lost = _run([unread], [_payment()])[0]
    assert lost["class"] == "uri_unresolvable"
    assert lost["reason"] == "fetch_failed"
    absent = reading_from_payload({}, {})
    assert absent["indexed"] is False
    assert absent["counts"] is None


def test_payer_must_be_the_rater_and_the_facilitator_is_not_the_payer():
    moved = _payment(payer=OTHER)
    assert _run([_feedback()], [moved])[0]["reason"] == "payer_mismatch"
    claimed = _feedback()
    claimed["file"]["proof"] = _proof(from_address=OTHER)
    assert _run([claimed], [_payment()])[0]["reason"] == "payer_mismatch"
    logs = [
        {
            "address": usdc_token(CHAIN),
            "topics": [authorization_topic(), _topic(CLIENT), NONCE],
            "data": "0x",
            "logIndex": "0x0",
        },
        _transfer(CLIENT, WALLET, 500_000, 1),
    ]
    decoded = decode_receipt(
        _receipt(FACILITATOR, OTHER, logs),
        chain_id=CHAIN,
        block_time=T0,
    )
    assert decoded["readable"] is True
    payment = decoded["payments"][0]
    assert payment["method"] == "eip3009"
    assert payment["payer"] == CLIENT
    assert payment["facilitator"] == FACILITATOR
    assert payment["payer"] != payment["facilitator"]
    assert payment["authorizer"] == CLIENT
    feedback = _feedback()
    feedback["file"]["proof"] = _proof(nonce=NONCE, tx_hash=None)
    # The receipt hash is whatever decode kept. Point the proof at that hash.
    feedback["file"]["proof"]["tx_hash"] = payment["tx_hash"]
    row = _run([feedback], [payment])[0]
    assert row["class"] == "backed_direct"


def test_permit2_payer_is_the_token_from_not_the_transaction_sender():
    decoded = decode_receipt(
        _receipt(FACILITATOR, "0x402085c248EeA27D92E8b30b2C58ed07f9E20001", [_transfer(CLIENT, WALLET, 500_000, 4)]),
        chain_id=CHAIN,
        block_time=T0,
    )
    payment = decoded["payments"][0]
    assert payment["method"] == "permit2"
    assert payment["payer"] == CLIENT
    assert payment["facilitator"] == FACILITATOR
    feedback = _feedback()
    feedback["file"]["proof"] = _proof(tx_hash=payment["tx_hash"])
    assert _run([feedback], [payment])[0]["class"] == "backed_direct"


def test_payee_is_the_wallet_at_the_payment_block_and_a_transfer_clears_it():
    assert is_clear_transfer(ZERO) is False
    assert is_clear_transfer(OWNER) is True
    spans = build_wallet_spans([
        {"kind": "set", "block": 10, "log_index": 1, "wallet": WALLET, "time": T0 - 50},
        {"kind": "clear", "block": 40, "log_index": 2, "time": T0 - 5},
        {"kind": "set", "block": 80, "log_index": 3, "wallet": OTHER, "time": T0 + 50},
    ])
    assert wallet_at(spans, 15)["wallet"] == WALLET
    cleared = wallet_at(spans, 40)
    assert cleared["wallet"] is None
    assert cleared["readable"] is True
    assert wallet_at(spans, 90)["wallet"] == OTHER
    assert _run([_feedback()], [_payment(payee=OTHER)], spans=spans)[0]["reason"] == "payee_mismatch"
    # The payment block is 50, which is inside the cleared span.
    assert _run([_feedback()], [_payment()], spans=spans)[0]["reason"] == "payee_mismatch"
    assert _run([_feedback()], [_payment()], history=False)[0]["reason"] == "wallet_unreadable"


def test_the_payment_must_fall_inside_the_window():
    late = _payment(block_time=T0 + 11)
    assert _run([_feedback()], [late])[0]["reason"] == "payment_after_feedback"
    same = _payment(block_time=T0 + 10)
    assert _run([_feedback()], [same])[0]["class"] == "backed_direct"
    edge = _feedback(block_time=T0 + WINDOW)
    assert _run([edge], [_payment()])[0]["class"] == "backed_direct"
    past = _feedback(block_time=T0 + WINDOW + 1)
    assert _run([past], [_payment()])[0]["reason"] == "past_window"
    timeless = _payment(block_time=None)
    assert _run([_feedback()], [timeless])[0]["reason"] == "time_unreadable"


def test_wrong_chain_wrong_token_missing_tx_and_failed_tx():
    other_chain = _feedback()
    other_chain["file"]["proof"] = _proof(chain_id=1)
    assert _run([other_chain], [_payment()])[0]["reason"] == "wrong_chain"
    assert _run([_feedback()], [_payment(token="0x" + "ab" * 20)])[0]["reason"] == "wrong_token"
    assert _run([_feedback()], [])[0]["reason"] == "tx_not_found"
    assert _run([_feedback()], [_payment(status_ok=False)])[0]["reason"] == "tx_failed"
    by_nonce = _feedback()
    by_nonce["file"]["proof"] = _proof(tx_hash=None, nonce=NONCE)
    assert _run([by_nonce], [_payment(auth_nonce=NONCE)])[0]["class"] == "backed_direct"
    ambiguous = _run(
        [by_nonce],
        [_payment(auth_nonce=NONCE), _payment(tx_hash=TX2, log_index=2, auth_nonce=NONCE)],
    )[0]
    assert ambiguous["reason"] == "nonce_ambiguous"


def test_one_payment_backs_one_rating():
    earlier = _feedback(feedback_index=4, block=10, block_time=T0 + 5)
    later = _feedback(feedback_index=1, block=90, block_time=T0 + 50)
    rows = _run([later, earlier], [_payment()])
    by_index = {row["feedback"]["feedback_index"]: row for row in rows}
    assert by_index[4]["class"] == "backed_direct"
    assert by_index[1]["reason"] == "reused"
    assert by_index[1]["payment_key"] is None
    unread = _feedback(feedback_index=7, block=None, block_time=T0 + 1)
    stolen = _run([unread, earlier], [_payment()])
    by_unread = {row["feedback"]["feedback_index"]: row for row in stolen}
    assert by_unread[4]["class"] == "backed_direct"
    assert by_unread[7]["reason"] == "reused"
    reading = build_reading(
        _agent(),
        [later, earlier],
        [_payment()],
        _spans(),
        [],
        [],
        window_seconds=WINDOW,
        edges=bucket_edges({}),
        history_readable=True,
        env={},
    )
    assert reading["counts"]["backed_direct"] == 1
    assert reading["counts"]["failed"] == 1


def test_escrow_is_labelled_and_not_attributed():
    escrow = default_escrows()[0]
    assert escrow["chain_id"] == 8453
    assert escrow["address"] == ACP_PAYMENT_MANAGER.lower()
    assert escrow["attribution_method"] == "unattributed"
    pay = _payment(chain_id=8453, token=usdc_token(8453), payee=escrow["address"])
    feedback = _feedback(chain_id=8453)
    feedback["file"]["proof"] = _proof(chain_id=8453, to_address=escrow["address"])
    row = _run([feedback], [pay], spans=[], escrows=default_escrows(), agent_chain=8453)[0]
    assert row["class"] == "backed_escrow_unattributed"
    assert row["label"] == ESCROW_LABEL
    assert row["label"] == "escrow, can't attribute"
    wrong_payer = _payment(chain_id=8453, token=usdc_token(8453), payee=escrow["address"], payer=OTHER)
    missed = _run([feedback], [wrong_payer], spans=[], escrows=default_escrows(), agent_chain=8453)[0]
    assert missed["reason"] == "payer_mismatch"
    assert missed["label"] is None


def test_value_buckets_and_graph_signals_are_not_a_verdict():
    from decimal import Decimal

    edges = bucket_edges({})
    assert edges == (Decimal("0.01"), Decimal("1"), Decimal("10"))
    reading = build_reading(
        _agent(),
        [_feedback()],
        [_payment()],
        _spans(),
        [{
            "chain_id": CHAIN,
            "address": CLIENT,
            "first_funder": OWNER,
            "first_fund_block": 55,
            "label": None,
        }],
        [],
        window_seconds=WINDOW,
        edges=edges,
        history_readable=True,
        env={},
    )
    assert reading["counts"]["backed_direct"] == 1
    labels = [item["label"] for item in reading["value_buckets"]]
    assert labels[1] == "$0.01 to under $1"
    assert reading["value_buckets"][1]["count"] == 1
    assert reading["signals"]["owner_adjacent"][0]["funder"] == OWNER
    assert reading["footer"] == FOOTER
    assert reading["summary"] is None
    assert reading["ai_written"] is False
    blob = json.dumps(reading).lower()
    assert "self-payment" not in blob
    assert "trustworthy" not in blob
    assert "scam" not in blob
    unpriced = build_reading(
        _agent(),
        [_feedback()],
        [_payment(raw_units=None)],
        _spans(),
        [],
        [],
        window_seconds=WINDOW,
        edges=edges,
        history_readable=True,
        env={},
    )
    assert unpriced["counts"]["backed_direct"] == 1
    assert unpriced["amount_unreadable"] == 1
    assert all(item["count"] == 0 for item in unpriced["value_buckets"])
    broken = reading_from_payload(
        {"agent": _agent(), "feedbacks": [], "payments": [], "wallet_spans": []},
        {"GROUNDED_REPUTATION_WINDOW_DAYS": "nope"},
    )
    assert broken["counts"] is None
    assert broken["error"] == "window_unreadable"


def test_a_labelled_funder_is_reported_and_not_clustered():
    exchange = "0x" + "66" * 20
    second = "0x" + "77" * 20
    first = _feedback()
    other = _feedback(client=second, feedback_index=2)
    other["file"] = {"resolved": True, "ipfs": False, "keccak_ok": True, "proof": None, "reason": None}
    reading = build_reading(
        _agent(),
        [first, other],
        [_payment()],
        _spans(),
        [
            {"chain_id": CHAIN, "address": CLIENT, "first_funder": exchange, "first_fund_block": 1, "label": None},
            {"chain_id": CHAIN, "address": second, "first_funder": exchange, "first_fund_block": 1, "label": None},
            {
                "chain_id": CHAIN,
                "address": exchange,
                "first_funder": None,
                "first_fund_block": None,
                "label": "example-exchange",
            },
        ],
        [],
        window_seconds=WINDOW,
        edges=None,
        history_readable=True,
        env={},
    )
    signals = reading["signals"]["common_first_funder"]
    assert signals
    assert all(item["excluded_from_cluster"] is True for item in signals)
    assert reading["signals"]["clusters"] == []


def test_wash_is_a_signal_and_not_a_claim_about_the_parties():
    back = _payment(
        tx_hash=TX2,
        log_index=3,
        payer=WALLET,
        payee=CLIENT,
        block_time=T0 + 100,
        raw_units=500_000,
    )
    reading = build_reading(
        _agent(),
        [_feedback()],
        [_payment(), back],
        _spans(),
        [],
        [],
        window_seconds=WINDOW,
        edges=None,
        history_readable=True,
        env={},
    )
    assert reading["signals"]["wash"]
    assert reading["signals"]["self_loop_suspect"]
    joined = " ".join(item["text"] for item in reading["signals"]["wash"])
    assert "self-payment" not in joined.lower()


def test_schema_has_the_ten_tables_and_one_payment_key(tmp_path):
    sql = postgres_ddl()
    for table in (
        "agents",
        "agent_wallet_history",
        "feedback",
        "feedback_files",
        "payments",
        "feedback_payment_match",
        "address_funding",
        "clusters",
        "known_escrows",
        "snapshots",
    ):
        assert f"CREATE TABLE IF NOT EXISTS {table} " in sql
    assert "payment_pk text UNIQUE" in sql
    conn = connect(tmp_path / "grounded.sqlite")
    ensure(conn)
    row = conn.execute("SELECT address, attribution_method FROM known_escrows WHERE chain_id = 8453").fetchone()
    assert row["address"] == ACP_PAYMENT_MANAGER.lower()
    assert row["attribution_method"] == "unattributed"
    conn.execute(
        "INSERT INTO feedback_payment_match (feedback_pk, payment_pk, class, reason, rule_version) "
        "VALUES ('a', '84532:tx:1', 'backed_direct', NULL, 'v0.1')"
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO feedback_payment_match (feedback_pk, payment_pk, class, reason, rule_version) "
            "VALUES ('b', '84532:tx:1', 'backed_direct', NULL, 'v0.1')"
        )
    conn.rollback()
    conn.execute(
        "INSERT INTO feedback_payment_match (feedback_pk, payment_pk, class, reason, rule_version) "
        "VALUES ('c', NULL, 'no_proof', 'absent', 'v0.1')"
    )
    conn.execute(
        "INSERT INTO feedback_payment_match (feedback_pk, payment_pk, class, reason, rule_version) "
        "VALUES ('d', NULL, 'no_proof', 'absent', 'v0.1')"
    )
    conn.commit()
    assert conn.execute("SELECT COUNT(*) FROM feedback_payment_match").fetchone()[0] == 2


def test_transfer_topic_is_the_erc20_topic():
    assert transfer_topic() == "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"


def _topic(addr: str) -> str:
    return "0x" + addr[2:].rjust(64, "0")


def _transfer(frm: str, to: str, units: int, log_index: int) -> dict[str, object]:
    return {
        "address": usdc_token(CHAIN),
        "topics": [transfer_topic(), _topic(frm), _topic(to)],
        "data": hex(units),
        "logIndex": hex(log_index),
    }


def _receipt(sender: str, to: str, logs: list[dict[str, object]]) -> dict[str, object]:
    return {
        "status": "0x1",
        "from": sender,
        "to": to,
        "transactionHash": TX,
        "blockNumber": "0x32",
        "logs": logs,
    }
