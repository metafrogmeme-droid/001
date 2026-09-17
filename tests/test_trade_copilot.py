"""Trade co-pilot — pre-registered predictions C1–C6.

C1 geometry validation; C2 reward:risk flag; C3 stop-distance flags; C4 size vs
equity; C5 engine-bias alignment + exposure notes; C6 determinism + advice-only
(no side effects). Pure module.

C5's two predictions are the ones worth reading twice. They pass `engine_bias`
and `existing_exposure` straight into `review()` — and for the life of this
file NOTHING IN THE PRODUCT EVER SUPPLIED EITHER, so both branches were proved
in a place no production caller could reach. That is the `SKILL_TO_FEATURE`
rot, and `tests/test_the_copilot_says_what_it_checked.py` is where the wiring
is now driven.
"""
from bot.core import trade_copilot as cp


def _good_long():
    return {"direction": "LONG", "symbol": "SOL", "entry": 100.0, "sl": 97.0, "tp": 109.0}


# ── C1 — geometry ─────────────────────────────────────────────────────

def test_c1_wrong_side_stop_is_invalid():
    r = cp.review({"direction": "LONG", "symbol": "SOL", "entry": 100, "sl": 101, "tp": 110})
    assert r["verdict"] == "invalid"
    assert r["flags"][0]["level"] == "block"


def test_c1_valid_long_geometry_ok():
    r = cp.review(_good_long())
    # PARTIAL, not clear. A ticket with valid geometry and nothing else supplied
    # had answered "clear" and "score 100/100" with three of the five subjects
    # silently skipped; this assertion used to accept that. It is the
    # measurement now: the two price-derived checks ran, the three that need a
    # book or the engine did not, and the review says which.
    assert r["verdict"] == "partial"
    assert r["score_basis"] == {"applied": 2, "total": 4}
    assert [u["name"] for u in r["unchecked"]] == [
        "size_vs_equity", "engine_bias", "existing_exposure"]
    assert r["rr"] == 3.0                       # reward 9 / risk 3
    assert r["stop_pct"] == 3.0


def test_c1_short_geometry():
    r = cp.review({"direction": "SHORT", "symbol": "ETH", "entry": 100, "sl": 103, "tp": 91})
    assert r["verdict"] != "invalid"
    assert r["rr"] == 3.0


# ── C2 — reward:risk ──────────────────────────────────────────────────

def test_c2_low_rr_flags_and_lowers_score():
    r = cp.review({"direction": "LONG", "symbol": "SOL", "entry": 100, "sl": 97, "tp": 102})
    assert r["rr"] == 0.67
    assert any("Reward:risk" in f["msg"] for f in r["flags"])
    assert r["verdict"] == "caution"
    assert r["score"] < 100


# ── C3 — stop distance ────────────────────────────────────────────────

def test_c3_tight_stop_flagged():
    r = cp.review({"direction": "LONG", "symbol": "SOL", "entry": 100, "sl": 99.8, "tp": 101})
    assert any("wicked out" in f["msg"] for f in r["flags"])


def test_c3_wide_stop_flagged():
    r = cp.review({"direction": "LONG", "symbol": "SOL", "entry": 100, "sl": 80, "tp": 160})
    assert any("wide stop" in f["msg"] for f in r["flags"])


# ── C4 — size vs equity ───────────────────────────────────────────────

def test_c4_heavy_concentration_flagged():
    r = cp.review({**_good_long(), "margin": 500}, equity_usd=1000)
    assert any("concentration" in f["msg"] for f in r["flags"])


def test_c4_reasonable_size_noted_not_flagged():
    r = cp.review({**_good_long(), "margin": 50}, equity_usd=1000)
    assert not any("concentration" in f["msg"] for f in r["flags"])
    assert any("% of equity" in n for n in r["notes"])


# ── C5 — bias alignment + exposure ────────────────────────────────────

def test_c5_counter_bias_is_caution():
    r = cp.review(_good_long(), engine_bias="short")
    assert any("counter to the engine" in f["msg"] for f in r["flags"])
    assert r["verdict"] == "caution"


def test_c5_aligned_bias_noted():
    r = cp.review(_good_long(), engine_bias="long")
    assert any("Aligned" in n for n in r["notes"])


def test_c5_stacking_exposure_note():
    r = cp.review(_good_long(), existing_exposure="long")
    assert any("stacks the position" in n for n in r["notes"])


def test_c5_hedge_exposure_note():
    r = cp.review(_good_long(), existing_exposure="short")
    assert any("hedge/reduce" in n for n in r["notes"])


# ── C6 — determinism ──────────────────────────────────────────────────

def test_c6_deterministic():
    a = cp.review(_good_long(), equity_usd=1000, engine_bias="short", existing_exposure="long")
    b = cp.review(_good_long(), equity_usd=1000, engine_bias="short", existing_exposure="long")
    assert a == b


def test_c6_human_readable_renders():
    txt = cp.human_readable(cp.review(_good_long(), engine_bias="short"))
    assert "R:R" in txt and "score" in txt
