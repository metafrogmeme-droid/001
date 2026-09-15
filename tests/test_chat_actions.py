"""
PR P — chat that acts for everyone.

Pins the three chat-action paths added on top of the router/gateway:

  * "backtest SOL" carries the symbol into run_backtest at FULL confidence
    (a single needs_symbol rule would demote bare "run a backtest" to a
    0.5-confidence partial and drop it to LLM chat) and the skill can find
    the frozen benchmark snapshot for a base coin;
  * "why no trade on BTC?" reaches the whynot skill (the real recorded
    rejection) instead of being swallowed by the broader check_risk rule;
  * a bare directional ask on the web ("long ETH") routes to analyze_asset —
    a setup card with the agent's own entry/SL/TP — and NEVER proposes or
    executes a trade (SL discipline: only "buy X <e> sl <s> tp <t>" proposes);
  * Telegram-only router intents (scan_swing, status) are aliased to the
    closest registered skill so a web ask acts instead of degrading to chat;
  * stance intents on the web get an honest pointer (personal pref on Home /
    global stance is an operator control), not silent LLM fallthrough.
"""

import contextlib
import json
from types import SimpleNamespace

from aiohttp.test_utils import TestClient, TestServer

from bot.nlp.intent_router import IntentRouter
from bot.skills.skill_registry import RunBacktestSkill
from bot.web import user_gateway as ug

SECRET = "s" * 32
HDRS = {"X-Gateway-Secret": SECRET}


# ── Router: backtest + whynot reachability ───────────────────────────────────

class TestChatActionRouting:
    def setup_method(self):
        self.router = IntentRouter()

    def test_backtest_with_symbol_carries_symbol_full_confidence(self):
        r = self.router.classify_rules("backtest SOL")
        assert r.skill == "run_backtest"
        assert r.confidence == 1.0
        assert r.kwargs.get("symbol") == "SOL/USDT"

    def test_bare_backtest_keeps_full_confidence(self):
        # The generic form must NOT be demoted to a 0.5 partial by the
        # symbol-bearing rule — that would drop it to LLM chat.
        r = self.router.classify_rules("run a backtest")
        assert r.skill == "run_backtest"
        assert r.confidence == 1.0
        assert "symbol" not in r.kwargs

    def test_why_no_trade_on_symbol_reaches_whynot(self):
        # Must NOT be swallowed by the broader "no trade" -> check_risk rule.
        r = self.router.classify_rules("why no trade on BTC?")
        assert r.skill == "whynot"
        assert r.confidence == 1.0

    def test_why_didnt_you_trade_reaches_whynot(self):
        r = self.router.classify_rules("why didn't you trade eth?")
        assert r.skill == "whynot"

    def test_explain_the_rejection_reaches_whynot(self):
        r = self.router.classify_rules("explain the rejection")
        assert r.skill == "whynot"

    def test_check_risk_still_matches_sit_out(self):
        r = self.router.classify_rules("should I sit out?")
        assert r.skill == "check_risk"


# ── Skill: frozen-snapshot discovery for a chat symbol ───────────────────────

def _write_manifest(bench_dir, dataset, symbols):
    d = bench_dir / dataset
    d.mkdir(parents=True)
    (d / "manifest.json").write_text(json.dumps(
        {"symbols": {s: {"bars": 999} for s in symbols}}))


class TestFindDatasetForSymbol:
    def test_base_coin_matches_perp_symbol(self, tmp_path, monkeypatch):
        monkeypatch.setattr(RunBacktestSkill, "_BENCH_DIR", tmp_path)
        _write_manifest(tmp_path, "majors_1h",
                        ["BTC/USDT:USDT", "SOL/USDT:USDT"])
        assert RunBacktestSkill.find_dataset_for_symbol("sol") == \
            ("majors_1h", "SOL/USDT:USDT")

    def test_usdt_suffix_and_pair_forms_match(self, tmp_path, monkeypatch):
        monkeypatch.setattr(RunBacktestSkill, "_BENCH_DIR", tmp_path)
        _write_manifest(tmp_path, "majors_1h", ["SOL/USDT:USDT"])
        for ask in ("SOLUSDT", "SOL/USDT", "SOL"):
            assert RunBacktestSkill.find_dataset_for_symbol(ask) == \
                ("majors_1h", "SOL/USDT:USDT"), ask

    def test_unknown_coin_is_none(self, tmp_path, monkeypatch):
        monkeypatch.setattr(RunBacktestSkill, "_BENCH_DIR", tmp_path)
        _write_manifest(tmp_path, "majors_1h", ["BTC/USDT:USDT"])
        assert RunBacktestSkill.find_dataset_for_symbol("XYZ") is None
        assert RunBacktestSkill.find_dataset_for_symbol("") is None

    def test_missing_bench_dir_fails_soft(self, tmp_path, monkeypatch):
        monkeypatch.setattr(RunBacktestSkill, "_BENCH_DIR",
                            tmp_path / "nope")
        assert RunBacktestSkill.find_dataset_for_symbol("SOL") is None

    def test_corrupt_manifest_fails_soft(self, tmp_path, monkeypatch):
        monkeypatch.setattr(RunBacktestSkill, "_BENCH_DIR", tmp_path)
        d = tmp_path / "bad"
        d.mkdir()
        (d / "manifest.json").write_text("{not json")
        assert RunBacktestSkill.find_dataset_for_symbol("SOL") is None

    async def test_execute_unknown_symbol_falls_back_to_synthetic(
            self, tmp_path, monkeypatch):
        # A symbol with no frozen snapshot must never error out of chat —
        # it degrades to the labelled synthetic smoke test.
        monkeypatch.setattr(RunBacktestSkill, "_BENCH_DIR", tmp_path)
        out = await RunBacktestSkill().execute(
            SimpleNamespace(), symbol="ZZZ", bars=120)
        assert "Synthetic data" in out
        assert RunBacktestSkill._running is False


# ── Gateway: bare directional + aliases + stance honesty ─────────────────────
# Minimal fakes mirroring tests/test_web_gateway.py (kept local — the test
# modules are intentionally import-independent).

class FakeUsers:
    def __init__(self, users=None):
        self._users = dict(users or {})

    def get(self, tg):
        return self._users.get(str(tg))

    def register(self, tg, name="", auto_role=""):
        return self._users.setdefault(
            str(tg), {"authorized": True, "role": "trader",
                      "can_trade_live": False, "name": name})

    def has_permission(self, tg, cmd):
        return self.permission_denial(tg, cmd) is None

    def permission_denial(self, tg, cmd):
        """UserStore's reason-returning gate: None allows, else "role" /
        "stale_session". The web gateway asks for the REASON because the two
        need different messages — one needs an admin, the other clears with
        /start — and both used to print the role wording."""
        return None

    def get_tier(self, tg):
        """The tier gate reads this; a double that omits it survives only
        because _web_skill_denied wraps the gate in try/except — i.e. by
        accident, on a path that swallows the AttributeError."""
        return "basic"

    def is_admitted(self, tg):
        """Whether an admin's /approve admitted this id — the second door into
        the allowlist beside the env vars. No admissions here, so the allowlist
        behaves exactly as it did before admission existed."""
        return False

    def live_trading_revoked(self, *_a, **_k):

        """Nobody here is under an explicit operator ban.


        Separate from `can_trade_live` because `register()` writes

        `can_trade_live: False` on every account as the paper-only

        DEFAULT — a default and a decision are different facts, and the

        live gate asks for both.

        """

        return False


    def can_trade_live(self, tg):
        return False


class FakeIntent:
    def __init__(self, skill="", confidence=0.0):
        self.skill = skill
        self.confidence = confidence
        self.kwargs = {}
        self.source = "rules"
        self.is_social = False

    @property
    def matched(self):
        return bool(self.skill)


class FakeSkill:
    def __init__(self):
        self.calls = []

    async def execute(self, engine, **kw):
        self.calls.append(kw)
        return "<b>skill reply</b>"


class FakeIdeaSkill:
    def __init__(self):
        self.calls = []

    async def execute(self, engine, **kw):
        self.calls.append(kw)
        idea = SimpleNamespace(
            id="TI-analyzed77", asset="ETH/USDT",
            direction=SimpleNamespace(value="LONG"),
            entry_price=2500.0, stop_loss=2450.0, take_profit=2700.0,
            risk_reward_ratio=4.0, confidence=0.8)
        engine._pending_ideas[idea.id] = idea
        return "<b>analysis</b>"


class FakeConvos:
    def append(self, uid, role, content, metadata=None):
        pass

    def get_recent(self, uid, limit=None):
        return []


class FakeHandler:
    def __init__(self, *, users=None, intent=None, skills=None, real_router=False):
        self.users = FakeUsers(users)
        self._limiter = SimpleNamespace(allow=lambda uid: True)
        # `real_router` because the stub below answers the SAME intent for
        # every text, so a test of a branch the ROUTER selects would be
        # testing the stub. The bare-directional tests need the real rules:
        # their branch used to be a private regex in `user_gateway` and is
        # `place_order` now, which only `IntentRouter` can produce.
        self.intent_router = (IntentRouter() if real_router else SimpleNamespace(
            classify_rules=lambda text: intent or FakeIntent()))
        self.registry = SimpleNamespace(get=lambda name: (skills or {}).get(name))
        self.conversations = FakeConvos()
        self.llm_calls = []

    def _allowlist_ids(self):
        return set()

    def _can_trade_live(self, tg):
        return False

    async def _llm_chat(self, q, user_id="", user_name="", is_admin=False,
                        public=False, profile_note="", return_meta=False,
                        **kw):
        self.llm_calls.append(q)
        if return_meta:
            return "llm answer", {"provider": "x", "model": "y"}
        return "llm answer"


class FakeEngine:
    def __init__(self):
        self._pending_ideas = {}
        self._manual_margin_override = {}
        self.confirm_calls = []
        self.user_portfolios = SimpleNamespace(get=lambda uid: None)

    def _is_operator_user(self, uid):
        return False

    async def confirm_trade(self, trade_id, user_id=""):
        self.confirm_calls.append((trade_id, user_id))
        return "executed"


AUTHED = {"7": {"authorized": True, "role": "trader"}}


@contextlib.asynccontextmanager
async def gateway_client(engine, handler):
    app = ug.build_gateway(engine, handler)
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        yield client
    finally:
        await client.close()


async def test_bare_long_routes_to_analyze_with_setup_card(monkeypatch):
    monkeypatch.setattr(ug, "_GATEWAY_SECRET", SECRET)
    engine = FakeEngine()
    analyzer = FakeIdeaSkill()
    handler = FakeHandler(users=AUTHED, skills={"analyze_asset": analyzer},
                          real_router=True)
    async with gateway_client(engine, handler) as c:
        r = await c.post("/chat", json={"telegram_id": "7", "text": "long ETH"},
                         headers=HDRS)
        assert r.status == 200
        data = await r.json()
        # A BARE DIRECTIONAL IS A REQUEST TO OPEN, and it is the router's
        # `place_order` now rather than this file's own regex — one reading
        # for both surfaces. The setup still rides with it, because the door
        # it names is a grammar that demands an entry, a stop and a target
        # and "long ETH" carries none of the three; what changed is that the
        # reply now SAYS nothing was bought, which the analysis card alone
        # never did.
        assert data["intent"] == "place_order"
        assert "Nothing has been placed." in data["reply_html"]
        assert data["analysis_html"] == "<b>analysis</b>"
        # the symbol is the router's reading, not a bare upper-cased token
        assert analyzer.calls[0]["symbol"] == "ETH/USDT"
        # Setup card is a READ-ONLY hint — nothing was proposed or executed.
        assert data["setup"]["symbol"] == "ETH"
        assert "pending_trade" not in data
        assert engine.confirm_calls == []
        assert handler.llm_calls == []


async def test_bare_paper_short_routes_to_analyze(monkeypatch):
    monkeypatch.setattr(ug, "_GATEWAY_SECRET", SECRET)
    analyzer = FakeIdeaSkill()
    handler = FakeHandler(users=AUTHED, skills={"analyze_asset": analyzer},
                          real_router=True)
    async with gateway_client(FakeEngine(), handler) as c:
        r = await c.post("/chat",
                         json={"telegram_id": "7", "text": "paper short sol"},
                         headers=HDRS)
        assert r.status == 200
        _d = await r.json()
        assert _d["intent"] == "place_order"
        assert "Nothing has been placed." in _d["reply_html"]
        assert analyzer.calls[0]["symbol"] == "SOL/USDT"


async def test_directional_with_levels_still_proposes_not_analyzes(monkeypatch):
    # The strict "buy X <entry> sl <sl> tp <tp>" form must keep its exact
    # prior meaning: a pending PROPOSAL (never an analysis, never an
    # execution). The bare-directional rewrite must not intercept it.
    monkeypatch.setattr(ug, "_GATEWAY_SECRET", SECRET)
    analyzer = FakeIdeaSkill()
    handler = FakeHandler(users=AUTHED, skills={"analyze_asset": analyzer})
    async with gateway_client(FakeEngine(), handler) as c:
        r = await c.post("/chat",
                         json={"telegram_id": "7",
                               "text": "buy SOL 71 sl 70 tp 76"},
                         headers=HDRS)
        assert r.status == 200
        data = await r.json()
        assert "pending_trade" in data
        assert analyzer.calls == []


async def test_stance_intent_gets_honest_pointer_not_llm(monkeypatch):
    monkeypatch.setattr(ug, "_GATEWAY_SECRET", SECRET)
    handler = FakeHandler(users=AUTHED,
                          intent=FakeIntent("stance_aggressive", 1.0))
    async with gateway_client(FakeEngine(), handler) as c:
        r = await c.post("/chat",
                         json={"telegram_id": "7", "text": "go aggressive"},
                         headers=HDRS)
        assert r.status == 200
        data = await r.json()
        assert data["intent"] == "stance_aggressive"
        assert "personal risk preference" in data["reply_html"]
        assert "aggressive" in data["reply_html"]
        assert handler.llm_calls == []


async def test_scan_swing_reaches_the_skill_the_table_names(monkeypatch):
    """`scan_swing` exists only as a Telegram command handler; on the web it
    must reach a REGISTERED skill instead of degrading to generic LLM chat.

    WHICH skill is `skill_doors.SCAN_DISPATCH`'s answer, and this test used to
    spell `scan_market` — the web's own third copy of that mapping, which sent
    all five modes to the shallow movers scan while Telegram ran two of them
    through `deepscan` and three through `pro_scan`. Same words, different
    engine, decided by which client you typed them into.

    The ARGUMENTS are asserted beside the name for the reason the alignment
    needed them: every scan skill is `execute(self, engine, **kwargs)` and
    `intent.kwargs` is empty for all five rules, so a dispatcher that reads
    the skill and not its mode answers a swing scan with the intraday card
    and raises nothing.
    """
    from bot.nlp.skill_doors import dispatch_kwargs, dispatches_to

    monkeypatch.setattr(ug, "_GATEWAY_SECRET", SECRET)
    scan = FakeSkill()
    ran = dispatches_to("scan_swing")
    handler = FakeHandler(users=AUTHED,
                          intent=FakeIntent("scan_swing", 1.0),
                          skills={ran: scan})
    async with gateway_client(FakeEngine(), handler) as c:
        r = await c.post("/chat",
                         json={"telegram_id": "7", "text": "swing scan"},
                         headers=HDRS)
        assert r.status == 200
        data = await r.json()
        assert data["reply_html"] == "<b>skill reply</b>"
        assert len(scan.calls) == 1
        wanted = dispatch_kwargs("scan_swing")
        assert wanted, "the table names no arguments for a timeframe scan"
        for k, v in wanted.items():
            assert scan.calls[0].get(k) == v, (k, scan.calls[0])
        assert handler.llm_calls == []


async def test_status_is_answered_by_the_status_card_not_the_portfolio(monkeypatch):
    """It ALIASED to `get_portfolio` — a question about the engine answered
    with the account card, which makes no engine claim at all."""
    monkeypatch.setattr(ug, "_GATEWAY_SECRET", SECRET)
    port = FakeSkill()
    handler = FakeHandler(users=AUTHED,
                          intent=FakeIntent("status", 1.0),
                          skills={"get_portfolio": port})

    async def _card(user_id, lang="en", *, surface="telegram"):
        return f"<b>Engine</b> for {user_id} via {surface}"

    handler.status_card_text = _card
    async with gateway_client(FakeEngine(), handler) as c:
        r = await c.post("/chat",
                         json={"telegram_id": "7", "text": "bot status"},
                         headers=HDRS)
        assert r.status == 200
        body = await r.json()
        assert body["intent"] == "status"
        assert "<b>Engine</b> for 7 via web" == body["reply_html"]
        assert port.calls == [], "the account card answered it again"


# ── The place door on the WEB, driven ───────────────────────────────────────
# Both of these exist because a mutation round said so. The Telegram arm has
# equivalents that kill their mutations; the web arm had none, so blanking its
# permission gate and swapping `place_target` for `symbol_mentioned` each left
# 554 tests green. A guard that only covers one of two surfaces is the shape
# CLAUDE.md records about `TestItIsActuallyReached` being one file short.

async def test_a_role_that_may_not_read_still_gets_the_web_door(monkeypatch):
    """THE DOOR IS UNGATED AND THE READ IS NOT. The branch calls the skill
    directly, which skips the gate every other web route to `analyze_asset`
    goes through — without it a viewer typing "buy eth" is handed a read
    their role forbids."""
    monkeypatch.setattr(ug, "_GATEWAY_SECRET", SECRET)
    analyzer = FakeIdeaSkill()
    handler = FakeHandler(users=AUTHED, skills={"analyze_asset": analyzer},
                          real_router=True)
    handler.users.permission_denial = lambda uid, perm: "role"
    async with gateway_client(FakeEngine(), handler) as c:
        r = await c.post("/chat", json={"telegram_id": "7", "text": "buy eth"},
                         headers=HDRS)
        assert r.status == 200
        data = await r.json()
        assert data["intent"] == "place_order"
        assert "Nothing has been placed." in data["reply_html"]
        assert "is below" not in data["reply_html"]
        assert "analysis_html" not in data
        assert analyzer.calls == [], "the gated read ran for a denied role"


async def test_two_assets_named_is_the_web_door_alone(monkeypatch):
    """`symbol_mentioned` answers the FIRST (ETH); `place_target` answers
    None. A branch re-deriving the target its own way is indistinguishable
    from this one on every SINGLE-asset fixture, so the phrase has to be one
    that routes AND names two."""
    monkeypatch.setattr(ug, "_GATEWAY_SECRET", SECRET)
    analyzer = FakeIdeaSkill()
    handler = FakeHandler(users=AUTHED, skills={"analyze_asset": analyzer},
                          real_router=True)
    async with gateway_client(FakeEngine(), handler) as c:
        r = await c.post("/chat", json={
            "telegram_id": "7",
            "text": "place a limit order on eth and btc"}, headers=HDRS)
        assert r.status == 200
        data = await r.json()
        assert data["intent"] == "place_order"
        assert "Nothing has been placed." in data["reply_html"]
        assert analyzer.calls == [], "a setup was read for a half-answered ask"
