"""Scan-stream signal keys must not collide — a dropped call has no receipt.

The manual /scan path builds stream rows keyed (symbol, direction, MINUTE).
That is not unique: two scans inside the same minute on the same pair produce
the same key, and the website UPSERTs the second onto the first — silently
discarding a call Telegram already published. Since every stream row now
carries a Provable Calls seal, a dropped row means a published call with no
receipt at all.

Verified against the live UPSERT before fixing: the second call's prices were
discarded and the row kept the first call's facts. Seal INTEGRITY held (the
stored seal still matched its stored payload — the receipt never lied), which
is exactly the failure mode the design should have: lose a row, never forge
one. The key now carries a digest of the decision facts so both calls survive.

Outcomes attach through the ENGINE's own stream (keyed by idea.id), never
here, so nothing depends on the old key shape.
"""

import importlib

scan_skill = importlib.import_module("bot.skills.scan_skill")


def _card(entry, sl, tp, symbol="BTC", direction="LONG"):
    return {"symbol": symbol, "direction": direction, "score": 0.8,
            "entry": str(entry), "stop_loss": str(sl), "tp1": str(tp),
            "trigger": "Bull Flag", "thesis": "t", "rr": "2.0"}


def _payload(cards, ts="2026-07-25 12:00 UTC"):
    return {"timestamp": ts, "regime": {"label": "TREND_UP"}, "entry_cards": cards}


def test_same_minute_different_calls_get_different_keys():
    rows = scan_skill._scan_signal_rows(
        _payload([_card(60000, 59000, 62000), _card(65000, 64000, 67000)]))
    assert len(rows) == 2
    keys = {r["signal_key"] for r in rows}
    assert len(keys) == 2, f"two distinct calls collapsed onto one key: {keys}"
    # Both still carry the human-readable prefix — the digest only disambiguates.
    for r in rows:
        assert r["signal_key"].startswith("BTC-LONG-"), r["signal_key"]


def test_the_same_call_re_pushed_keeps_its_key():
    """Idempotence is the whole point of a stable key: a re-push must UPSERT
    the same row rather than mint a duplicate."""
    a = scan_skill._scan_signal_rows(_payload([_card(60000, 59000, 62000)]))
    b = scan_skill._scan_signal_rows(_payload([_card(60000, 59000, 62000)]))
    assert a[0]["signal_key"] == b[0]["signal_key"]
    # A different minute is still a different call.
    c = scan_skill._scan_signal_rows(
        _payload([_card(60000, 59000, 62000)], ts="2026-07-25 12:01 UTC"))
    assert c[0]["signal_key"] != a[0]["signal_key"]


def test_digest_is_pure_and_covers_every_decision_fact():
    base = _card(60000, 59000, 62000)
    d = scan_skill._facts_digest(base)
    assert d == scan_skill._facts_digest(dict(base)), "must be deterministic"
    assert len(d) == 6 and all(ch in "0123456789abcdef" for ch in d)
    # Moving ANY of entry / stop / target changes the key.
    for k, v in (("entry", "60001"), ("stop_loss", "58999"), ("tp1", "62001")):
        moved = dict(base, **{k: v})
        assert scan_skill._facts_digest(moved) != d, f"{k} did not affect the key"


def test_symbol_and_direction_still_separate_calls():
    rows = scan_skill._scan_signal_rows(_payload([
        _card(60000, 59000, 62000),
        _card(60000, 61000, 58000, direction="SHORT"),
        _card(60000, 59000, 62000, symbol="ETH"),
    ]))
    assert len({r["signal_key"] for r in rows}) == 3


def test_scan_message_points_at_verification_without_faking_a_link():
    """Telegram is where screenshots are born, so the scan message says the
    calls are sealed and where to check them. It must NOT fabricate a
    per-call link: the message is rendered before the stream push assigns
    keys, and a 404 link would be worse than no link at all."""
    from pathlib import Path
    src = Path("bot/skills/scan_skill.py").read_text(encoding="utf-8")
    assert "Every call above is sealed when it is made" in src
    assert "/provable" in src and "/dashboard#signals" in src
    # The footer only appears when there ARE setups — never on an empty scan.
    assert "if setup_lines:" in src
    # Per-call links are allowed ONLY because they are derived from the payload
    # that actually gets pushed (see the link tests below) — never guessed.
    assert "_scan_verify_links(_scan_payload)" in src


def _scan_result(price=60000.0, atr=500.0, direction="LONG"):
    return {"sym": "BTC/USDT", "price": price, "atr": atr, "dir": direction,
            "score": 0.8, "rsi": 55, "vol_ratio": 1.4, "patterns": []}


def test_the_sealed_call_is_the_call_telegram_published():
    """The scan message advertises a PULLBACK entry ("(+pullback)"); the
    sealed website record used to store the raw spot price instead, so the
    receipt described a call no user was ever shown — and its R:R was
    computed off a different entry. Both sides must now agree by formula."""
    r = _scan_result()
    payload = scan_skill._build_scan_payload([r], None)
    card = payload["entry_cards"][0]

    price, atr = r["price"], r["atr"]
    published_entry = round(price - atr * 0.3, 8)          # what Telegram shows
    assert float(card["entry"]) == published_entry, "record must match the message"
    assert float(card["entry"]) != price, "the raw spot price is NOT the call"

    # R:R must be measured from that same entry, not from spot.
    sl, tp1 = float(card["stop_loss"]), float(card["tp1"])
    expected_rr = round(abs(tp1 - published_entry) / abs(published_entry - sl), 2)
    assert float(card["rr"]) == expected_rr

    # SHORT mirrors it — the pullback sits ABOVE spot.
    short = scan_skill._build_scan_payload([_scan_result(direction="SHORT")], None)
    scard = short["entry_cards"][0]
    assert float(scard["entry"]) == round(price + atr * 0.3, 8)
    assert float(scard["entry"]) > price


def test_the_stream_row_carries_that_same_entry_into_the_seal():
    """End of the chain: the row the website seals must hold the published
    entry, or the receipt certifies the wrong call."""
    payload = scan_skill._build_scan_payload([_scan_result()], None)
    rows = scan_skill._scan_signal_rows(payload)
    assert rows, "a scoring setup should produce a stream row"
    assert rows[0]["entry_price"] == float(payload["entry_cards"][0]["entry"])
    assert rows[0]["entry_price"] != 60000.0


def test_verify_links_come_from_the_rows_that_get_sealed():
    """A verify link may only ever point at a key the record really carries.
    Building it from the SAME payload rows is what makes that true — a guessed
    key would 404, and a broken proof link is worse than no link."""
    payload = scan_skill._build_scan_payload([_scan_result()], None)
    rows = scan_skill._scan_signal_rows(payload)
    links = scan_skill._scan_verify_links(payload)
    assert rows and links, "a scoring setup should yield a row and a link"
    for row in rows:
        url = links[(row["symbol"], row["direction"])]
        # The exact stored key appears in the URL, percent-encoded.
        from urllib.parse import quote
        assert url.endswith("/call/" + quote(row["signal_key"], safe="")), url
    # A setup the payload never produced gets NO link (honest omission).
    assert ("DOGE", "LONG") not in links


def test_the_pushed_payload_is_the_one_the_links_were_built_from(monkeypatch):
    """Rebuilding the payload inside the push would restamp the scan instant
    and mint different keys — every link in the message would then 404."""
    sent = {}

    def fake_scan(p):
        sent["scan"] = p

    def fake_signals(rows):
        sent["rows"] = rows

    import bot.utils.website_sync as ws
    monkeypatch.setattr(ws, "sync_scan_in_background", fake_scan, raising=False)
    monkeypatch.setattr(ws, "sync_signals_in_background", fake_signals, raising=False)

    payload = scan_skill._build_scan_payload([_scan_result()], None)
    links = scan_skill._scan_verify_links(payload)
    scan_skill._push_scan_to_dashboard([_scan_result()], None, payload=payload)

    assert sent.get("scan") is payload, "the caller's payload must be pushed as-is"
    for row in sent["rows"]:
        assert (row["symbol"], row["direction"]) in links
        assert row["signal_key"] in links[(row["symbol"], row["direction"])]
