"""Five guards on what the chat says RAN, driven rather than scanned.

Audit E5–E8 (2026-09-12) found five places where the chat's account of its
own execution was wrong, each in a different layer:

1. POST /chat/record wrote the WEBSITE's own answer in the `[intent] result:`
   shape — the shape both tool rules define for the model as "written by the
   runtime after a tool really ran". `[networth] result:` names a tool no
   surface holds, so on Telegram the model was told to call it again.
2. The tools rule was swapped into the prompt ONCE per turn, above the
   candidate loop; the vision candidate attaches images and no tools, so an
   operator sending a chart was answered by a model told to CALL tools it had
   not been given. And the rule listed subjects (costs, macro, rejections) as
   though every caller held a tool for each, while the catalogue is filtered
   per role, tier and surface.
3. When `TelegramStream.finish` declined (dead stream, or an answer over 4000
   characters) the checked answer went out as a fresh message and the
   provisional one stayed: the model's RAW output, before `_chat_ret` checked
   it, with a caret on the end, directly above the answer that was checked.
4. The account purge never reached the conversation store — `clear_user` had
   no caller outside a test — and the store replays an append-only JSONL on
   every boot, so even an in-memory clear would have lasted until the next
   restart. The widened sweep found the authority-envelope store beside it.
5. The fabrication refusal said "I cannot run one from this chat" — true on
   the public surface, false on the two where the model holds tools — and
   `run_tool` handed the model its OWN record markers back (a TRUNCATED
   prefix, a TIMED OUT line), so a faithful quotation of real evidence was
   struck out as an invention and replaced with that refusal.

Every guard here plants the state and reads what came out; the one source
scan pins wiring the other tests cannot reach.
"""
from __future__ import annotations

import asyncio
import json
import pathlib
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import bot.skills.telegram_handler as th_mod
from bot.core.cost import CostTracker
from bot.llm.provider import BYOK, LLMConfig, LLMProvider
from bot.nlp import chat_tools
from bot.nlp.conversation_store import ConversationStore
from bot.nlp.fabricated_tool_calls import REFUSAL, find_fabricated_marker
from bot.nlp.skill_memory import (
    MEMORY_CAP,
    card_shown_memory,
    command_reply_memory,
    not_run_memory,
    routed_answer_memory,
    skill_failure_memory,
    skill_result_memory,
    skill_unavailable_memory,
    web_answer_memory,
)
from bot.skills.chat_runtime import (
    _CHAT_NO_TOOLS_RULE,
    _CHAT_TOOLS_RULE,
    TelegramStream,
    _chat_ret,
    tools_rule_for,
)
from bot.skills.skill_permissions import SKILL_PERMISSION
from bot.skills.telegram_handler import TelegramHandler as H
from bot.utils.user_store import UserStore
from bot.web import user_gateway as ug


def _run(coro):
    return asyncio.run(coro)


# ── 1. the website's answer is recorded as the website's ───────────────────

def test_the_websites_answer_is_recorded_as_the_websites_not_as_a_tool_result():
    rec = web_answer_memory("networth", "<b>Net worth</b> ~$12,400 across 2 venues")
    assert rec.startswith(
        "[networth] shown by the website (its own reading; no bot tool ran):\n")
    assert "Net worth ~$12,400 across 2 venues" in rec
    assert "] result:" not in rec


def test_a_long_website_answer_says_it_was_cut_and_fits_the_store():
    body = "y" * (MEMORY_CAP + 300)
    rec = web_answer_memory("research", body)
    assert "TRUNCATED" in rec and str(MEMORY_CAP) in rec and str(MEMORY_CAP + 300) in rec
    assert rec.endswith("y" * MEMORY_CAP)
    # conversation_store persists content[:2000]; a record longer than that is
    # silently shortened AGAIN on the way to disk (skill_memory's own rule).
    assert len(web_answer_memory("a" * 40, body)) <= 2000


def test_an_empty_website_answer_is_said_not_left_blank():
    rec = web_answer_memory("alerts", "<br/>")
    assert rec.startswith("[alerts] shown by the website") and "no text" in rec


def _gateway_handler(tmp_path):
    h = SimpleNamespace()
    h.users = UserStore(tmp_path / "users.json")
    h.conversations = ConversationStore()
    h.engine = SimpleNamespace()
    h._limiter = SimpleNamespace(allow=lambda uid: True)
    return h


def _request(handler, body: dict):
    async def _json():
        return body

    return SimpleNamespace(app={"tg_handler": handler, "engine": handler.engine},
                           json=_json)


@pytest.fixture(autouse=True)
def _gateway_config(monkeypatch):
    with patch("bot.web.user_gateway.CONFIG") as mc:
        mc.telegram.admin_ids = ""
        mc.telegram.chat_id = "1"
        mc.per_user_live_enabled = False
        monkeypatch.setattr(ug, "_guard_user",
                            lambda tg_handler, tg_id, command="", name="": None)
        yield


def test_the_record_route_writes_the_websites_shape(tmp_path):
    h = _gateway_handler(tmp_path)
    resp = _run(ug.handle_chat_record(_request(h, {
        "telegram_id": "web:7", "text": "what's my net worth?",
        "reply": "<b>Net worth</b> ~$12,400", "intent": "networth"})))
    assert resp.status == 200
    rec = h.conversations.get_recent("web:7", limit=10)[1].content
    assert rec.startswith("[networth] shown by the website")
    assert "] result:" not in rec
    # And the guard that polices the MODEL's replies knows the shape, so a
    # model copying it is caught like every other record it might copy.
    assert find_fabricated_marker(rec) == 0


def test_an_over_long_reply_is_refused_before_it_is_read(tmp_path):
    h = _gateway_handler(tmp_path)
    resp = _run(ug.handle_chat_record(_request(h, {
        "telegram_id": "web:7", "text": "q",
        "reply": "x" * (ug._MAX_RECORD_REPLY_LEN + 1), "intent": "research"})))
    assert resp.status == 400
    assert h.conversations.get_recent("web:7", limit=10) == []
    resp = _run(ug.handle_chat_record(_request(h, {
        "telegram_id": "web:7", "text": "q",
        "reply": "x" * ug._MAX_RECORD_REPLY_LEN, "intent": "research"})))
    assert resp.status == 200, "the ceiling is a ceiling, not a step below it"


# ── 5. the guard's vocabulary is the memory layer's, and the model never gets a marker ──

_RECORDS = [
    skill_result_memory("scan_market", "rows"),
    skill_result_memory("scan_market", "x" * (MEMORY_CAP + 1)),
    skill_result_memory("scan_market", None),
    skill_failure_memory("costs"),
    skill_unavailable_memory("learning"),
    routed_answer_memory("help", "the card"),
    routed_answer_memory("help", None),
    routed_answer_memory("help", "x" * (MEMORY_CAP + 1)),
    card_shown_memory("status"),
    not_run_memory("pro_scan", "a staked tier is needed"),
    web_answer_memory("networth", "$12,400"),
    web_answer_memory("networth", None),
    command_reply_memory("networth", ["$12,400"]),
    command_reply_memory("networth", []),
    command_reply_memory("help", ["x" * (MEMORY_CAP + 1)]),
]


@pytest.mark.parametrize("record", _RECORDS, ids=[r.split("\n")[0][:40] for r in _RECORDS])
def test_every_record_the_runtime_writes_is_in_the_guards_vocabulary(record):
    """The guard's docstring claims "the whole vocabulary skill_memory
    writes"; for a long time it knew four words of nine records. A model
    copying "[status] SHOWN, CONTENTS NOT RECORDED" was claiming a card it
    never sent, in a shape nothing policed."""
    assert find_fabricated_marker(record) == 0
    assert find_fabricated_marker("Sure, here it is:\n" + record) == 18


class _Store:
    def __init__(self):
        self.rows = []

    def append(self, uid, role, content, metadata=None):
        self.rows.append((uid, role, content, metadata or {}))


class _Skill:
    def __init__(self, fn):
        self.fn = fn

    async def execute(self, engine, **kw):
        return await self.fn(**kw)


def _tool_handler(skills: dict):
    return SimpleNamespace(engine=SimpleNamespace(), conversations=_Store(),
                           registry=SimpleNamespace(get=lambda n: skills.get(n)))


async def _rows(**kw):
    return "<b>BTC</b> +4.1%"


async def _long(**kw):
    return "z" * (MEMORY_CAP * 2)


async def _nothing(**kw):
    return ""


async def _slow(**kw):
    await asyncio.sleep(5)
    return "late"


@pytest.mark.parametrize("case", [
    "read", "truncated", "no_output", "timed_out", "not_offered",
    "no_such_tool", "not_run",
])
def test_what_the_model_reads_back_never_carries_a_record_marker(case):
    """`run_tool` records `[name] …` into the store and hands the model the
    words. Two outcomes handed the marker along — the TRUNCATED prefix and
    the timeout line — so a model quoting its own evidence ("the scan timed
    out") tripped the guard written for inventions, and the refusal put in
    its place said no tool had run. The store keeps the marker; the model's
    copy has every other word."""
    skills = {"scan_market": _Skill({"read": _rows, "truncated": _long,
                                     "no_output": _nothing, "timed_out": _slow,
                                     "not_offered": _rows}.get(case, _rows)),
              "whynot": _Skill(_rows)}
    h = _tool_handler(skills)
    if case == "not_offered":
        out = _run(chat_tools.run_tool(h, "u1", "scan_market", {}, set()))
        assert out.startswith("UNAVAILABLE")
    elif case == "no_such_tool":
        out = _run(chat_tools.run_tool(_tool_handler({}), "u1", "scan_market", {},
                                       {"scan_market"}))
        assert out.startswith("UNAVAILABLE")
    elif case == "not_run":
        out = _run(chat_tools.run_tool(h, "u1", "whynot", {"symbol": "../etc"},
                                       {"whynot"}))
        assert out.startswith("NOT RUN")
    else:
        out = _run(chat_tools.run_tool(h, "u1", "scan_market", {}, {"scan_market"},
                                       timeout=1.0))
        row = h.conversations.rows[-1][2]
        assert row.startswith("[scan_market] "), "the STORE keeps the marker"
        assert find_fabricated_marker(row) == 0
        if case == "read":
            assert out == "BTC +4.1%"
        elif case == "truncated":
            assert out.startswith(
                f"(TRUNCATED — first {MEMORY_CAP} of {MEMORY_CAP * 2} characters; "
                "the rest is not recorded):\n" + "z" * 10)
        elif case == "no_output":
            assert out.startswith("NO OUTPUT")
        elif case == "timed_out":
            assert out.startswith("TIMED OUT after 1s") and "Nothing was measured" in out
            assert row.startswith("[scan_market] TIMED OUT after 1s")
    assert find_fabricated_marker(out) is None, out
    assert "[scan_market]" not in out and "[whynot]" not in out


def test_the_refusal_does_not_claim_the_bot_cannot_run_tools():
    """True on the public surface, false on the two where the model is
    offered tools — there it HAD tools and called none, and telling the user
    otherwise is the fabrication's cousin."""
    low = REFUSAL.lower()
    assert "cannot run" not in low and "can't run" not in low and "can not run" not in low
    assert "nothing ran" in low
    assert _chat_ret("[PENDING] scanning...", None, False) == REFUSAL


# ── 2. one rule about tools, the one that is true of THIS call ─────────────

def test_the_tools_rule_names_what_is_offered_and_nothing_else():
    rule = tools_rule_for({"get_portfolio", "check_risk"})
    assert rule.startswith(_CHAT_TOOLS_RULE)
    assert "check_risk, get_portfolio" in rule and rule.endswith("\n\n")
    assert "cannot be measured from here" in rule
    assert tools_rule_for(set()) == _CHAT_NO_TOOLS_RULE
    assert tools_rule_for(None) == _CHAT_NO_TOOLS_RULE
    assert "and a tool offered on this turn reads it" in _CHAT_TOOLS_RULE


class _Users:
    def __init__(self, held):
        self.held = set(held)

    def permission_denial(self, uid, perm):
        return None if perm in self.held else "role"

    def get(self, uid):
        return {"role": "viewer"}


class _Conversations:
    def __init__(self):
        self.rows = []

    def get_recent_as_llm_messages(self, user_id, limit=8, drop_trailing_user=False):
        return []

    def append(self, uid, role, content, metadata=None):
        self.rows.append((uid, role, content, metadata or {}))


def _stub():
    return SimpleNamespace(
        engine=SimpleNamespace(cost=CostTracker(), analyzer=None),
        conversations=_Conversations(),
        _build_chat_system_prompt=lambda user_id, user_name="", surface="telegram": (
            "system prompt\n" + _CHAT_NO_TOOLS_RULE + "PERSONALITY"),
        _is_admin=lambda update: False,
        _note_chat_llm_failure=lambda reason="": None,
        registry=SimpleNamespace(get=lambda n: object()),
        users=_Users(set(SKILL_PERMISSION.values())),
    )


@pytest.fixture
def _chat(monkeypatch):
    def _tier(provider):
        monkeypatch.setattr(
            th_mod, "resolve_tier_config",
            lambda *a, **kw: LLMConfig(provider=provider, api_key="k", model="m"))

    for env in ("GEMINI_API_KEY", "ANTHROPIC_API_KEY", "ALIBABA_API_KEY"):
        monkeypatch.delenv(env, raising=False)
    monkeypatch.setattr(th_mod, "CONFIG", replace(
        th_mod.CONFIG, llm=replace(th_mod.CONFIG.llm, api_key="",
                                   chat_tools_enabled=True)))
    monkeypatch.setattr(th_mod, "create_llm_client", lambda cfg: object())
    monkeypatch.setattr(th_mod, "resolve_profile_note", lambda note, uid: "")
    from bot.token import tier_gate
    monkeypatch.setattr(tier_gate, "check_user", lambda users, uid, f: (True, "ok"))
    BYOK.reset()
    yield _tier
    BYOK.reset()


def test_the_vision_candidate_is_told_it_has_no_tools(_chat, monkeypatch):
    """Images ride without tools (the two are not combined), so the prompt
    that candidate reads must carry the no-tools rule — it used to carry the
    tools rule, swapped in once for the whole turn."""
    _chat(LLMProvider.ANTHROPIC)
    plain, tooled = [], []

    async def _complete(client, cfg, sys_p, q, **kw):
        plain.append((sys_p, kw.get("images")))
        return "plain"

    async def _with_tools(*a, **kw):
        tooled.append(1)
        return "tooled"

    monkeypatch.setattr(th_mod, "llm_complete", _complete)
    monkeypatch.setattr(th_mod, "llm_complete_with_tools", _with_tools)
    out = _run(H._llm_chat(_stub(), "what is this chart saying?", user_id="u1",
                           is_admin=True, images=[{"media_type": "image/png",
                                                   "data": "aGk="}]))
    assert out == "plain" and tooled == []
    sys_p, images = plain[0]
    assert images, "the images were dropped, so this was not the vision path"
    assert _CHAT_NO_TOOLS_RULE in sys_p
    assert _CHAT_TOOLS_RULE not in sys_p, "told to call tools it was not given"


def test_the_tool_candidate_is_told_which_tools_it_holds(_chat, monkeypatch):
    _chat(LLMProvider.GROK)
    tooled = []

    async def _with_tools(client, cfg, sys_p, q, tools, tool_executor, **kw):
        tooled.append((sys_p, sorted(t["name"] for t in tools)))
        return "tooled"

    monkeypatch.setattr(th_mod, "llm_complete_with_tools", _with_tools)
    assert _run(H._llm_chat(_stub(), "what do I hold?", user_id="u1")) == "tooled"
    sys_p, names = tooled[0]
    assert _CHAT_TOOLS_RULE in sys_p
    assert _CHAT_NO_TOOLS_RULE not in sys_p, "one document, one rule about tools"
    assert "- The tools offered on THIS turn are: " + ", ".join(names) in sys_p


# ── 3. the provisional message comes down when it cannot become the answer ──

class _Msg:
    def __init__(self, *, delete_fails=False, edit_fails=False, deletable=True):
        self.deleted = False
        self.edits = []
        self._df, self._ef = delete_fails, edit_fails
        if deletable:
            async def delete():
                if self._df:
                    raise RuntimeError("Message can't be deleted")
                self.deleted = True
            self.delete = delete

    async def edit_text(self, text, parse_mode=None, **kw):
        if self._ef:
            raise RuntimeError("Flood control exceeded")
        self.edits.append((text, parse_mode))


def test_retract_deletes_the_provisional_message():
    msg = _Msg()
    assert _run(TelegramStream(msg).retract()) == "deleted"
    assert msg.deleted and msg.edits == []


def test_retract_edits_down_to_an_ellipsis_when_delete_is_refused():
    msg = _Msg(delete_fails=True)
    assert _run(TelegramStream(msg).retract()) == "edited"
    assert msg.edits == [("…", None)] and not msg.deleted


def test_retract_says_failed_when_nothing_could_be_done():
    msg = _Msg(delete_fails=True, edit_fails=True)
    assert _run(TelegramStream(msg).retract()) == "failed"


def test_retract_without_a_message_is_nothing_and_without_delete_edits():
    assert _run(TelegramStream(None).retract()) == "nothing"
    msg = _Msg(deletable=False)
    assert _run(TelegramStream(msg).retract()) == "edited"


def test_a_dead_stream_still_retracts():
    """`dead` turns EDITING off for the turn; the retract is the one edit
    that must still be tried, because what the last edit left is the raw
    text this exists to remove."""
    msg = _Msg()
    s = TelegramStream(msg)
    s.dead = True
    assert _run(s.retract()) == "deleted"


def test_the_handler_retracts_between_the_declined_finish_and_the_fresh_send():
    """Wiring, which no fake can reach: the retract sits after `finish`
    declines and BEFORE the checked answer goes out, so the screen never
    shows the raw text above the checked one. Comments stripped first — the
    comment above that block names both calls."""
    from tests.source_scan import code_only
    src = code_only(pathlib.Path("bot/skills/telegram_handler.py").read_text())
    i = src.index("await _stream.finish(_final)")
    j = src.index("await _stream.retract()", i)
    k = src.index("await self._send(update, _final)", j)
    assert i < j < k
    assert src.count("await _stream.retract()") == 1


# ── 4. the purge reaches the conversation, on disk too ─────────────────────

def _conversations(tmp_path):
    store = ConversationStore(persist_path=tmp_path / "conv.jsonl")
    store.append("web:7", "user", "what's my net worth?")
    store.append("web:7", "assistant", "[networth] shown by the website:\n$12,400")
    store.set_summary("web:7", "holds ETH, asks about NEAR")
    store.append("web:8", "user", "hello")
    return store


def test_clear_user_erases_memory_disk_and_the_summary(tmp_path):
    store = _conversations(tmp_path)
    assert "NEAR" in store.build_context_prompt("web:7"), "the summary was never planted"
    assert store.clear_user("web:7") is True
    assert store.get_recent("web:7", limit=10) == []
    assert "NEAR" not in store.build_context_prompt("web:7")
    assert store.get_recent("web:8", limit=10)[0].content == "hello"
    # A restart replays the JSONL: nothing of web:7 may come back.
    reloaded = ConversationStore(persist_path=tmp_path / "conv.jsonl")
    assert reloaded.get_recent("web:7", limit=10) == []
    assert "NEAR" not in reloaded.build_context_prompt("web:7")
    assert reloaded.get_recent("web:8", limit=10)[0].content == "hello"
    rows = [json.loads(line) for line in (tmp_path / "conv.jsonl").read_text().splitlines()
            if line.strip()]
    assert all(r["user_id"] != "web:7" for r in rows) and rows


def test_clear_user_is_false_for_a_user_it_never_held(tmp_path):
    store = _conversations(tmp_path)
    assert store.clear_user("web:9") is False
    assert (tmp_path / "conv.jsonl").read_text().count("\n") == 4


def test_clear_user_reaches_rows_memory_no_longer_holds(tmp_path):
    """LRU eviction drops a user from memory and leaves their rows on disk;
    a deletion that only asks memory would answer 'none' over a file that
    still names them."""
    store = _conversations(tmp_path)
    store._conversations.pop("web:7")
    store._user_contexts.pop("web:7", None)
    assert store.clear_user("web:7") is True
    assert "web:7" not in (tmp_path / "conv.jsonl").read_text()


def test_a_disk_fault_is_raised_not_swallowed(tmp_path, monkeypatch):
    store = _conversations(tmp_path)
    import bot.nlp.conversation_store as cs

    def _boom(*a, **kw):
        raise OSError("read-only file system")

    monkeypatch.setattr(cs, "atomic_write_text", _boom)
    with pytest.raises(OSError):
        store.clear_user("web:7")


class _Ctx:
    def __init__(self):
        self.rowcount = 0

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params):
        return self


def _stub_the_other_stores(monkeypatch, tmp_path):
    from bot.core import exchange_credentials, user_leverage_store, user_memory_store, user_strategy_store
    from bot.db import models
    from bot.guardian import user_authority_store as uas
    monkeypatch.setattr(exchange_credentials, "get_credential_store",
                        lambda: SimpleNamespace(delete=lambda uid: False))
    monkeypatch.setattr(ug, "_profile_store", SimpleNamespace(clear=lambda uid: False))
    for mod in (user_memory_store, user_leverage_store, user_strategy_store):
        monkeypatch.setattr(mod, "clear", lambda uid: False)
    monkeypatch.setattr(models, "settings_user_id", lambda tg: 5)
    monkeypatch.setattr(models, "purge_user_data", lambda uid: {"notes": "none"})
    monkeypatch.setattr(models, "get_db", lambda: _Ctx())
    auth = uas.UserAuthorityStore(str(tmp_path / "auth.json"))
    auth._envelopes["web:7"] = {"mode": "enforce", "revoked": False}
    monkeypatch.setattr(uas, "get_user_authority_store", lambda: auth)
    return auth


def _purge(h, tg_id):
    resp = _run(ug.handle_account_purge(_request(h, {"telegram_id": tg_id})))
    return resp.status, json.loads(resp.body.decode("utf-8"))


def test_the_purge_erases_the_conversation_and_the_authority_envelope(tmp_path, monkeypatch):
    auth = _stub_the_other_stores(monkeypatch, tmp_path)
    h = _gateway_handler(tmp_path)
    h.conversations = _conversations(tmp_path)
    h.users = SimpleNamespace(forget=lambda uid: False)
    status, body = _purge(h, "web:7")
    assert status == 200 and body["purged"] is True, body
    assert body["stores"]["conversation_memory"] == "deleted"
    assert body["stores"]["live_authority"] == "deleted"
    assert h.conversations.get_recent("web:7", limit=10) == []
    assert "web:7" not in (tmp_path / "conv.jsonl").read_text()
    assert auth.get("web:7") is None
    # Somebody the bot never talked to: a count, not a failure.
    status, body = _purge(h, "web:9")
    assert status == 200 and body["stores"]["conversation_memory"] == "none"
    assert body["stores"]["live_authority"] == "none"


def test_a_conversation_the_disk_would_not_give_up_is_error_and_409(tmp_path, monkeypatch):
    _stub_the_other_stores(monkeypatch, tmp_path)
    h = _gateway_handler(tmp_path)
    h.conversations = _conversations(tmp_path)
    h.users = SimpleNamespace(forget=lambda uid: False)
    import bot.nlp.conversation_store as cs

    def _boom(*a, **kw):
        raise OSError("read-only file system")

    monkeypatch.setattr(cs, "atomic_write_text", _boom)
    status, body = _purge(h, "web:7")
    assert status == 409 and body["purged"] is False
    assert body["stores"]["conversation_memory"] == "error"


def test_a_handler_with_no_conversation_store_is_error_not_none(tmp_path, monkeypatch):
    _stub_the_other_stores(monkeypatch, tmp_path)
    h = _gateway_handler(tmp_path)
    del h.conversations
    h.users = SimpleNamespace(forget=lambda uid: False)
    status, body = _purge(h, "web:7")
    assert status == 409 and body["stores"]["conversation_memory"] == "error"


def test_the_handler_takes_the_raw_text_down_when_the_final_edit_cannot_land(tmp_path):
    """DRIVEN, because the source scan above cannot see reachability: an
    `if False:` around the retract keeps every literal it looks for. Stream
    a fragment shaped like a fabricated result onto the provisional message
    (so the raw text really was on screen), answer past Telegram's 4000
    characters so `finish` declines, and read the screen."""
    from tests.test_free_text_obeys_the_role_gate import OPERATOR, _update
    from tests.test_free_text_obeys_the_role_gate import _handler as _tg_handler

    h = _tg_handler(tmp_path)
    patches = []
    for mod in ("bot.skills.telegram_handler", "bot.core.engine"):
        p = patch(f"{mod}.CONFIG")
        mc = p.start()
        patches.append(p)
        mc.telegram.chat_id = OPERATOR
        mc.telegram.admin_ids = ""
        mc.telegram.live_trader_ids = ""
        mc.paper_auto_accept = False
        mc.per_user_live_enabled = False
        mc.is_live.return_value = False
        mc.llm.chat_streaming_enabled = True
    try:
        prov = _Msg()
        upd = _update(OPERATOR, "I can't stop thinking about crypto")

        async def _reply(*a, **kw):
            return prov

        upd.message.reply_text = _reply

        async def _llm(question, **kw):
            on_event = kw.get("on_event")
            assert on_event is not None, "the streamed path was not taken"
            await on_event({"type": "delta",
                            "text": "[analyze_asset] result: RSI 48, go long " * 3})
            answer = "a" * 4100          # past 4000: `finish` must decline
            return (answer, {"provider": "grok", "model": "m"}) if kw.get("return_meta") else answer

        h._llm_chat = _llm
        _run(h._handle_message(upd, None))
    finally:
        for p in patches:
            p.stop()
    assert prov.edits and "[analyze_asset] result" in prov.edits[0][0], (
        "the raw fragment never reached the screen, so nothing was tested")
    assert prov.deleted is True, "the unchecked text stayed above the answer"
    assert any("aaaa" in s for s in h.sent), "the checked answer never went out"
