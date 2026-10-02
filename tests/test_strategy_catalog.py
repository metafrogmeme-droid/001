"""MARKETPLACE Phase 1 — the Strategy-Agent catalogue.

Every card in the catalogue is a REAL engine preset (never a fabricated agent),
its "how it trades" line is DERIVED from the live config (so it can't drift from
what the agent actually does), and the whole surface is §4-safe: strategy design
+ regime + qualitative risk only — never a dollar figure, never a claimed return.
The public gateway route serves it with no auth.
"""
import inspect
import json

from bot.core import strategy_catalog as sc
from bot.core.live_eligibility import (
    ELIGIBLE,
    MISSING,
    UNREADABLE,
    read_eligibility,
    record_path,
)
from bot.skills.skill_registry import RunStrategySkill


def test_catalog_is_exactly_the_real_presets():
    cards = sc.catalog()
    assert cards, "catalogue must not be empty"
    # One card per real preset — no invented agents, none dropped.
    card_names = {c["name"] for c in cards}
    preset_labels = {cfg.get("label", key.title())
                     for key, cfg in RunStrategySkill.PRESETS.items()}
    assert card_names == preset_labels
    assert len(cards) == len(RunStrategySkill.PRESETS)


def test_every_card_has_the_marketplace_shape():
    for c in sc.catalog():
        for field in ("id", "name", "icon", "tagline", "how", "regime",
                      "risk", "risk_label", "horizon", "run"):
            assert field in c, f"card missing {field}: {c}"
        assert c["id"] and c["how"].startswith("Trades ")
        # The run alias is a real chat/Telegram shortcut for this agent.
        assert c["run"]


def test_catalog_is_section4_safe_no_dollar_amounts():
    blob = repr(sc.catalog())
    assert "$" not in blob, "no dollar sign may appear in the public catalogue"
    # No DOLLAR metric keys leak onto a card. (Percent metrics like
    # total_return_pct and symbols like BTC/USDT are §4-safe — the rule is no
    # absolute dollar amounts, not "no percentages".)
    lowered = blob.lower()
    # (Not "balance" — the "🟡 Balanced" risk label legitimately contains it.)
    for leaky in ("net_pnl", "total_pnl", "final_equity", "avg_win_usd",
                  "max_drawdown_usd", "'balance'"):
        assert leaky not in lowered


def test_how_it_trades_is_derived_from_real_config():
    # Dip Sniper's line must reflect the ACTUAL preset thresholds, not editorial.
    dip = sc.get_agent("dip-sniper")
    assert dip is not None
    cfg = RunStrategySkill.PRESETS["dip sniper"]
    how = dip["how"]
    if cfg.get("rsi_threshold") is not None:
        assert f"RSI below {cfg['rsi_threshold']}" in how
    if cfg.get("rsi_min") is not None:
        assert f"RSI at or above {cfg['rsi_min']}" in how
    if cfg.get("direction") == "short_only":
        assert "short only" in how
    if cfg.get("confidence_threshold") is not None:
        assert f"{round(cfg['confidence_threshold'] * 100)}%" in how
    # Safe Scalper trades the most-liquid pairs (top3_volume) — derived phrasing.
    scalp = sc.get_agent("safe-scalper")
    assert scalp is not None
    if RunStrategySkill.PRESETS["safe scalper"].get("symbols") == "top3_volume":
        assert "most-liquid" in scalp["how"]


def test_get_agent_slug_and_miss():
    assert sc.get_agent("dip-sniper")["name"]
    assert sc.get_agent("Dip Sniper")["id"] == "dip-sniper"  # slug-normalised
    assert sc.get_agent("does-not-exist") is None
    assert sc.get_agent("") is None


def test_catalog_fail_soft_on_bad_source(monkeypatch):
    # If the preset source can't be read, the catalogue is empty, never a crash.
    import bot.skills.skill_registry as reg
    monkeypatch.delattr(reg.RunStrategySkill, "PRESETS", raising=False)
    assert sc.catalog() == []


def test_copy_follow_shows_the_verdict_and_withholds_profit_factor_below_one(tmp_path, monkeypatch):
    """Profit factor, trade count, folds, and a discovery mark are on the card.

    A measured profit factor below 1 is not listed for copy/follow until an
    eligibility artefact for that preset says it survives. A missing verdict
    is not a profit factor of 0 and it is not an offer. The artefact is not
    the running-strategy record the live gate reads.
    """
    cards = {c["id"]: c for c in sc.catalog()}
    for aid in ("full-scan", "safe-scalper", "eth-ma-trend"):
        card = cards[aid]
        assert card["copy_follow"] is False
        assert card["copy_follow_reason"] == "below_one"
        score = card["scorecard"]
        assert score["data_mark"] == "discovery"
        assert score["folds"] is None
        assert isinstance(score["metrics"]["total_trades"], int)
        assert score["metrics"]["profit_factor"] < 1
    for aid in ("dip-sniper", "momentum-hunter"):
        card = cards[aid]
        assert card["copy_follow"] is True
        assert card["copy_follow_reason"] == "offered"
        assert card["scorecard"]["data_mark"] == "discovery"
        assert card["scorecard"]["folds"] is None
        assert card["scorecard"]["metrics"]["profit_factor"] >= 1
    for aid in ("daily-vol-rotation", "alt-sweep"):
        assert cards[aid]["copy_follow"] is False
        assert cards[aid]["copy_follow_reason"] == "no_verdict"

    scored = {"metrics": {"profit_factor": 1, "total_trades": 0}, "data_mark": "discovery"}
    assert sc.follow_listing(scored, MISSING) == (True, "offered")
    below = {"metrics": {"profit_factor": 0.99, "total_trades": 4}, "data_mark": "discovery"}
    assert sc.follow_listing(below, MISSING) == (False, "below_one")
    zero = {"metrics": {"profit_factor": 0, "total_trades": 1}, "data_mark": "discovery"}
    assert sc.follow_listing(zero, MISSING) == (False, "below_one")
    assert sc.follow_listing(zero, ELIGIBLE) == (True, "offered")
    assert sc.follow_listing(zero, UNREADABLE) == (False, "eligibility_unreadable")
    unread = {"metrics": {"profit_factor": None, "total_trades": 4}, "data_mark": "discovery"}
    assert sc.follow_listing(unread, MISSING) == (False, "no_verdict")
    assert sc.follow_listing(None, ELIGIBLE) == (False, "no_verdict")
    assert sc.follow_listing({"omitted": "No track record is published."}, ELIGIBLE) == (
        False, "no_verdict")

    monkeypatch.setattr(sc, "_ELIGIBILITY_ROOT", str(tmp_path))
    body = {"schema": 1, "strategy_hash": "presets/full-scan",
            "verdict": "survives", "stage": "minimum"}
    path = record_path("presets/full-scan", tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(body))
    again = {c["id"]: c for c in sc.catalog()}
    assert again["full-scan"]["copy_follow"] is True
    assert again["full-scan"]["copy_follow_reason"] == "offered"
    assert again["safe-scalper"]["copy_follow"] is False
    assert read_eligibility("a" * 64, tmp_path).state == MISSING


def test_folds_pass_through_and_a_missing_block_is_not_zero(tmp_path, monkeypatch):
    card = {
        "dataset": "majors_1h", "dataset_hash": "abc", "honest": True,
        "metrics": {
            "profit_factor": 1.2, "total_trades": 3, "total_return_pct": 1,
            "win_rate": 0.5, "max_drawdown_pct": 1, "sharpe_ratio": 0.1,
        },
        "folds": {"requested": 6, "run": 6, "profitable": 0},
    }
    directory = tmp_path / "scorecards"
    directory.mkdir()
    path = directory / "dip-sniper.json"
    path.write_text(json.dumps(card))
    monkeypatch.setattr(sc, "_SCORECARD_DIR", str(directory))
    loaded = sc._load_scorecard("dip-sniper")
    assert loaded["folds"] == {"requested": 6, "run": 6, "profitable": 0}
    assert loaded["data_mark"] == "discovery"
    assert loaded["metrics"]["profit_factor"] == 1.2
    card["folds"] = {"run": "6", "profitable": True}
    path.write_text(json.dumps(card))
    assert sc._load_scorecard("dip-sniper")["folds"] is None
    card.pop("folds")
    path.write_text(json.dumps(card))
    assert sc._load_scorecard("dip-sniper")["folds"] is None


def test_public_gateway_route_is_registered_no_auth():
    src = inspect.getsource(__import__("bot.web.user_gateway", fromlist=["x"]))
    assert 'add_get("/public/strategies", handle_strategies_public)' in src
    h = inspect.getsource(
        __import__("bot.web.user_gateway", fromlist=["x"]).handle_strategies_public)
    # Public by construction — no per-user guard, and it serves the catalogue.
    assert "_guard_user" not in h
    assert "strategy_catalog.catalog()" in h
