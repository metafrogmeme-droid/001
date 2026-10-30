"""A tier is granted once, read once, and buys what its card says.

Before this, three things were called a tier and none of them agreed:

- `/set_tier` wrote a grant into the member store, and the ladder
  (`tier_gate.FEATURE_MIN_TIER`) never read it. With the token gate off
  nobody was refused anything; with it on, only a stake counted, so a
  granted Elite was refused /backtest.
- `user_store.TIER_FEATURES` listed what each tier bought. Nothing read it,
  Pro was Basic word for word, and Elite listed live-trading eligibility,
  which the tier model rules out (docs/TIER_MODEL.md §6).
- The website's plan card had its own table: "Higher live caps", "Live
  trading eligibility", "Early access". No tier granted any of them.

Now the tier is `tier_gate.tier_reading` (the higher of the grant and the
stake), the ladder enforces it when the operator switches it on
(`TIER_GATE_ENABLED`), and every card renders `plan_ladder`, which is read
from the things that enforce it. Driven against the real member store.
"""
from __future__ import annotations

import ast
import importlib
from pathlib import Path

import pytest

import bot.token.tier_gate as tg
from bot.utils.user_store import TIER_RANK, TIERS, UserStore

ROOT = Path(__file__).resolve().parent.parent
WALLET = "So11111111111111111111111111111111111111112"
MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
GATE_ENV = ("TIER_GATE_ENABLED", "TOKEN_TIER_GATE_ENABLED", "RCLAW_MINT",
            "RCLAW_STAKING_PROGRAM", "RCLAW_TIER_FEATURES", "RCLAW_TIER_PRO_MIN",
            "RCLAW_TIER_ELITE_MIN", "FREE_CHAT_QUOTA_ENABLED", "XAI_API_KEY",
            "FREE_CHAT_DAILY_LIMIT")


@pytest.fixture
def gate(monkeypatch):
    """The gate module, reloaded under a controlled environment."""
    def _load(**env):
        for k in GATE_ENV:
            monkeypatch.delenv(k, raising=False)
        for k, v in env.items():
            monkeypatch.setenv(k, v)
        return importlib.reload(tg)
    yield _load
    for k in GATE_ENV:
        monkeypatch.delenv(k, raising=False)
    importlib.reload(tg)


@pytest.fixture
def store(tmp_path):
    st = UserStore(str(tmp_path / "users.json"))
    for uid, tier in (("101", "basic"), ("102", "pro"), ("103", "elite"),
                      ("104", "admin")):
        st.register(uid, name=f"m{uid}")
        assert st.set_tier(uid, tier)
    # The operator's ROLE, which is what holds operator-only commands. A tier
    # word is not a role, and this fixture keeps the two apart on purpose.
    assert st.authorize("104", "admin", by="999")
    return st


class _RaisingStore:
    """A store whose grant read raises; everything else is the real one."""
    def __init__(self, real):
        self._real = real

    def get_tier(self, uid):
        raise OSError("disk")

    def __getattr__(self, name):
        return getattr(self._real, name)


# ── the ladder reads the grant ─────────────────────────────────────────────

def test_with_both_switches_off_nothing_is_refused(gate, store):
    mod = gate()
    assert mod.tiers_enforced() is False
    for uid in ("101", "102", "103"):
        for feature in mod.FEATURE_MIN_TIER:
            assert mod.check_user(store, uid, feature) == (True, "ok")


def test_a_granted_tier_is_what_the_ladder_reads(gate, store):
    """BOTH ARMS, at each rung: the tier at the rung passes, the one below is
    refused, and the reason is "insufficient", not a wallet."""
    mod = gate(TIER_GATE_ENABLED="1")
    assert mod.tiers_enforced() is True and mod.gate_enabled() is False
    for feature, need in mod.FEATURE_MIN_TIER.items():
        for uid, tier in (("101", "basic"), ("102", "pro"), ("103", "elite"),
                          ("104", "admin")):
            allowed, reason = mod.check_user(store, uid, feature)
            if TIER_RANK[tier] >= TIER_RANK[need]:
                assert (allowed, reason) == (True, "ok"), (feature, tier)
            else:
                assert (allowed, reason) == (False, "insufficient"), (feature, tier)


def test_the_safety_commands_answer_at_every_tier(gate, store):
    mod = gate(TIER_GATE_ENABLED="1")
    for feature in mod.NEVER_GATED:
        assert mod.check_user(store, "101", feature) == (True, "ok")


def test_an_unreadable_grant_is_said_not_served_as_basic(gate, store):
    mod = gate(TIER_GATE_ENABLED="1")
    broken = _RaisingStore(store)
    assert mod.check_user(broken, "103", "run_backtest") == (False, "tier_unreadable")
    said = mod.refusal_message("tier_unreadable", "the backtest", "run_backtest")
    assert "Can't read your tier" in said
    assert "feature" not in said, "an unreadable grant was answered with an upgrade pitch"
    assert mod.effective_tier(broken, "103") is None


def test_a_word_the_bot_does_not_rank_is_no_grant(gate, store):
    mod = gate(TIER_GATE_ENABLED="1")
    store._users["102"]["tier"] = "premium"
    assert mod.check_user(store, "102", "deepscan") == (False, "insufficient")
    reading = mod.tier_reading(store, "102")
    assert reading["tier"] == "basic" and reading["granted"] == "premium"
    assert reading["granted_known"] is False


def test_a_grant_that_suffices_never_reads_the_chain(gate, store, monkeypatch):
    """With the token gate on, an Elite grant was refused unless a stake
    also said Elite. It now passes on the grant, without an RPC."""
    mod = gate(TOKEN_TIER_GATE_ENABLED="true", RCLAW_MINT=MINT)

    def no_chain(*a, **k):
        raise AssertionError("a sufficient grant read the chain")
    monkeypatch.setattr(mod, "_resolve_wallet", no_chain)
    assert mod.check_user(store, "103", "run_backtest") == (True, "ok")


def test_the_tier_is_the_higher_of_the_grant_and_the_stake(gate, store, monkeypatch):
    mod = gate(TOKEN_TIER_GATE_ENABLED="true", RCLAW_MINT=MINT,
               RCLAW_TIER_PRO_MIN="10000", RCLAW_TIER_ELITE_MIN="100000")
    monkeypatch.setattr(mod, "_resolve_wallet", lambda users, uid: WALLET)
    monkeypatch.setattr(mod, "wallet_verified", lambda users, uid: True)
    monkeypatch.setattr(mod, "balance_of", lambda w: 50_000.0)  # a Pro stake
    # A Basic grant with a Pro stake reads Pro, and runs a Pro feature.
    assert mod.effective_tier(store, "101") == "pro"
    assert mod.check_user(store, "101", "deepscan") == (True, "ok")
    assert mod.check_user(store, "101", "run_backtest") == (False, "insufficient")
    # An Elite grant with the same stake reads Elite: the grant is higher.
    assert mod.effective_tier(store, "103") == "elite"


# ── what a tier buys, read from what enforces it ──────────────────────────

def test_no_tier_lists_live_trading(gate):
    mod = gate()
    text = repr(mod.plan_ladder()).lower()
    assert "live" not in text, text
    assert not hasattr(__import__("bot.utils.user_store", fromlist=["x"]), "TIER_FEATURES")


def test_the_ladder_follows_the_operator_override(gate):
    """One reading: the card is FEATURE_MIN_TIER, so retuning the ladder
    retunes the card with it."""
    mod = gate(RCLAW_TIER_FEATURES="deepscan:elite,patterns:basic")
    rows = {r["tier"]: [u["feature"] for u in r["unlocks"]] for r in mod.plan_ladder()["tiers"]}
    assert "deepscan" in rows["elite"] and "deepscan" not in rows["pro"]
    assert all("patterns" not in v for v in rows.values())


def test_a_command_the_role_refuses_is_not_offered(gate, store):
    """/patterns is on the ladder at Pro and held by no role but the
    operator's. The card offered it to every Pro member."""
    mod = gate()
    pro_cmds = [c for r in mod.plan_ladder(store, "102")["tiers"] if r["tier"] == "pro"
                for u in r["unlocks"] for c in u["commands"]]
    assert "patterns" not in pro_cmds and "deepscan" in pro_cmds, pro_cmds
    op_cmds = [c for r in mod.plan_ladder(store, "104")["tiers"] if r["tier"] == "pro"
               for u in r["unlocks"] for c in u["commands"]]
    assert "patterns" in op_cmds, op_cmds


def test_the_chat_line_is_the_quota_in_force(gate, monkeypatch):
    mod = gate(FREE_CHAT_QUOTA_ENABLED="1", FREE_CHAT_DAILY_LIMIT="7")
    rows = {r["tier"]: r for r in mod.plan_ladder()["tiers"]}
    assert rows["basic"]["chat_per_day"] == 7
    assert rows["pro"]["chat_per_day"] is None and rows["elite"]["chat_per_day"] is None
    mod = gate(FREE_CHAT_QUOTA_ENABLED="0")
    assert all(r["chat_per_day"] is None for r in mod.plan_ladder()["tiers"])


def test_the_quota_exemption_is_the_tier_list(gate):
    from bot.web import chat_quota
    for tier in TIERS:
        assert chat_quota.is_quota_exempt(tier) is (TIER_RANK[tier] >= TIER_RANK["pro"])
    assert chat_quota.is_quota_exempt("premium") is False


def test_every_gated_command_is_registered_under_its_guard():
    """FEATURE_COMMANDS names, for each gated feature, the command a person
    types and the permission its handler's `@guard` asks. Read from the
    handler: every `_token_gate_blocks(update, display, feature)` site is a
    `_cmd_<command>` decorated `@guard(<permission>)`, and the pair must be
    the one the table names. A card cannot then offer an internal name or a
    command under the wrong permission."""
    from bot.skills.telegram_handler import TelegramHandler
    from tests.source_scan import handler_sources

    found: dict[str, set] = {}
    for path in handler_sources():
        for node in ast.walk(ast.parse(Path(path).read_text(encoding="utf-8"))):
            if not isinstance(node, ast.AsyncFunctionDef) or not node.name.startswith("_cmd_"):
                continue
            perm = None
            for d in node.decorator_list:
                if (isinstance(d, ast.Call) and getattr(d.func, "id", "") == "guard"
                        and d.args and isinstance(d.args[0], ast.Constant)):
                    perm = d.args[0].value
            for call in ast.walk(node):
                if (isinstance(call, ast.Call)
                        and getattr(call.func, "attr", "") == "_token_gate_blocks"):
                    feature = (call.args[2].value if len(call.args) > 2
                               and isinstance(call.args[2], ast.Constant) else "premium_scan")
                    found.setdefault(feature, set()).add((node.name[len("_cmd_"):], perm))
    assert found, "the walk found no gated command"
    # Two-way: every gated site is named, and every name is a gated site.
    assert set(found) == set(tg.FEATURE_COMMANDS), (sorted(found), sorted(tg.FEATURE_COMMANDS))
    for feature, pairs in found.items():
        assert set(tg.FEATURE_COMMANDS[feature]) == pairs, (feature, pairs)
    for pairs in tg.FEATURE_COMMANDS.values():
        for cmd, _perm in pairs:
            assert hasattr(TelegramHandler, f"_cmd_{cmd}"), cmd


# ── /set_tier, the mirror, /me ─────────────────────────────────────────────

def _set_tier(store, monkeypatch, *args):
    """Drive the real `/set_tier` body on a host carrying the real store;
    return (replies, messages sent to the member, pushes to the website)."""
    import asyncio
    import types
    from unittest.mock import AsyncMock

    from bot.skills.access_commands import AccessCommands
    from bot.utils import website_sync

    pushes = []
    monkeypatch.setattr(website_sync, "sync_tiers_in_background",
                        lambda m, **kw: pushes.append(dict(m)))
    replies = []

    async def send(update, text, *a, **k):
        replies.append(text)
    # `_get_tg_id` names who made the change: the one write path records it
    # (bot/core/member_admin.py), as the real handler's host contract supplies.
    host = types.SimpleNamespace(users=store, _is_admin=lambda u: True,
                                 _lang=lambda u: "en", _send=send,
                                 _get_tg_id=lambda u: "999")
    bot = types.SimpleNamespace(send_message=AsyncMock())
    ctx = types.SimpleNamespace(args=list(args), bot=bot)
    asyncio.run(AccessCommands._cmd_set_tier(host, object(), ctx))
    return replies, bot.send_message.await_args_list, pushes


def test_set_tier_grants_a_website_member(store, monkeypatch):
    store.register("web:7", name="w7")
    replies, told, pushes = _set_tier(store, monkeypatch, "web:7", "pro")
    assert store.get_tier("web:7") == "pro", replies
    assert pushes and pushes[-1].get("web:7") == "pro", pushes
    assert told == [], "a website-only member has no chat to write to"


def test_set_tier_never_makes_a_website_member_the_operator(store, monkeypatch):
    store.register("web:8", name="w8")
    replies, _told, pushes = _set_tier(store, monkeypatch, "web:8", "admin")
    assert store.get_tier("web:8") == "basic"
    assert pushes == []
    assert "linked Telegram account" in replies[-1], replies
    # The same word for a Telegram member is the operator's to give.
    _set_tier(store, monkeypatch, "101", "admin")
    assert store.get_tier("101") == "admin"


def test_set_tier_says_changed_not_upgraded(store, monkeypatch):
    _replies, told, _pushes = _set_tier(store, monkeypatch, "103", "basic")
    assert store.get_tier("103") == "basic"
    text = told[-1].kwargs["text"]
    assert "Your tier changed" in text and "Upgraded" not in text, text


def test_set_tier_refuses_a_lookalike_id(store, monkeypatch):
    for bad in ("web:\u0661\u0662", "\uff11\uff12", "web:", "12a"):
        replies, _told, pushes = _set_tier(store, monkeypatch, bad, "pro")
        assert pushes == [] and "Invalid Telegram ID" in replies[-1], (bad, replies)


def test_the_tier_push_goes_in_pages_and_a_failed_page_fails_it(monkeypatch):
    from bot.utils import website_sync as ws
    sent = []

    def post(path, body, **kw):
        sent.append(len(body["tiers"]))
        return {"ok": True, "updated": len(body["tiers"])}
    monkeypatch.setattr(ws, "_post", post)
    monkeypatch.setattr(ws, "_last_tiers_sent", "")
    tiers = {str(i): "basic" for i in range(1, 1202)}
    assert ws.sync_tiers(tiers) is True
    assert sent == [500, 500, 201], sent
    # A failed page fails the push and is not remembered, so it resends.
    sent.clear()
    tiers["1"] = "pro"
    monkeypatch.setattr(ws, "_post", lambda p, b, **k: None)
    assert ws.sync_tiers(tiers) is False
    monkeypatch.setattr(ws, "_post", post)
    assert ws.sync_tiers(tiers) is True and sent == [500, 500, 201]
    # A one-member push at link time is sent, and does not stand for the map.
    sent.clear()
    assert ws.sync_tiers({"55": "pro"}, remember=False) is True and sent == [1]
    sent.clear()
    assert ws.sync_tiers(tiers) is True and sent == [], "the full map was re-sent"


def test_a_web_grant_follows_the_member_when_they_link(store):
    from bot.skills.user_middleware import _carry_web_grant, _tier_words
    store.register("web:7", name="w7")
    assert store.set_tier("web:7", "elite")
    store.register("555", name="tg")
    _carry_web_grant(store, 7, "555")
    assert store.get_tier("555") == "elite"
    # Never downward: a higher grant on the chat stays.
    assert store.set_tier("web:7", "pro")
    _carry_web_grant(store, 7, "555")
    assert store.get_tier("555") == "elite"
    assert _tier_words(store, "555") == "Elite"
    assert _tier_words(None, "555") == "could not be read"
    assert _tier_words(_RaisingStore(store), "555") == "could not be read"


def test_linking_carries_the_web_grant_and_tells_the_website(store, monkeypatch):
    """Driven through the real /link: a member granted Elite as `web:7`
    links Telegram, the bot reads them by chat id from then on, and the
    grant comes with them; the reply names the bot's tier, and the website
    is sent this one member's tier without it standing for the whole map."""
    import asyncio
    import io
    import json
    import types

    from bot.skills import user_middleware as um
    from bot.utils import website_sync

    store.register("web:7", name="w7")
    assert store.set_tier("web:7", "elite")
    store.register("4242", name="ann")
    pushes = []
    monkeypatch.setattr(website_sync, "sync_tiers_in_background",
                        lambda m, **kw: pushes.append((dict(m), kw)))

    class _Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False
    monkeypatch.setattr(um.urllib.request, "urlopen", lambda req, timeout=0: _Resp(
        json.dumps({"user_id": 7, "email": "a@b.c", "plan": "free"}).encode()))
    monkeypatch.setattr(um, "get_user_by_chat_id", lambda chat_id: None)
    monkeypatch.setattr(um, "_user_lang", lambda chat_id: "en")
    monkeypatch.setattr(um, "_ensure_local_user", lambda *a: None)
    monkeypatch.setattr(um, "link_telegram", lambda *a: True)
    replies = []

    class _Msg:
        async def reply_text(self, text, **kw):
            replies.append(text)
    upd = types.SimpleNamespace(effective_chat=types.SimpleNamespace(id=4242, type="private"),
                                effective_user=types.SimpleNamespace(username="ann"),
                                message=_Msg())
    ctx = types.SimpleNamespace(args=["tok123"], application=types.SimpleNamespace(
        bot_data={"telegram_handler": types.SimpleNamespace(users=store)}))
    asyncio.run(um.cmd_link(upd, ctx))
    assert store.get_tier("4242") == "elite"
    assert pushes == [({"4242": "elite"}, {"remember": False})], pushes
    assert "Elite" in replies[-1] and "free" not in replies[-1], replies


def test_the_raw_grant_is_read_only_where_the_grant_is_the_subject():
    """Every other surface asks `tier_gate` (the grant, or a stake above it).
    Two-way: a new raw read fails, and a listed one that is gone fails."""
    allowed = {
        # Comparing two stored grants to carry one at link time.
        ("bot/skills/user_middleware.py", "_carry_web_grant"),
        # Mirroring the stored grant to the website's plan column.
        ("bot/skills/user_middleware.py", "cmd_link"),
        # The operator's stance control: an operator check, not an entitlement.
        ("bot/utils/control_pull.py", "pull_and_apply_stance"),
        # A Stars checkout: whether a paid period changes the stored grant,
        # the same comparison `UserStore.grant_paid_tier` makes when it lands.
        ("bot/skills/upgrade_commands.py", "_on_pre_checkout"),
    }
    seen = set()
    for path in sorted((ROOT / "bot").rglob("*.py")):
        rel = path.relative_to(ROOT).as_posix()
        if rel in ("bot/token/tier_gate.py", "bot/utils/user_store.py"):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for call in ast.walk(fn):
                if isinstance(call, ast.Call) and getattr(call.func, "attr", "") == "get_tier":
                    seen.add((rel, fn.name))
    assert seen == allowed, (sorted(seen - allowed), sorted(allowed - seen))
