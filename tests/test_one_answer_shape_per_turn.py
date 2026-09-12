"""One answer shape per turn, and never one the surface cannot source.

`intent_router._detect_reply_mode` has classified every free-text message into
six modes -- quick / full_scan / execution / bot / beginner / standard -- since
it was written, and `IntentResult.reply_mode` has carried the answer down every
chat path. NOTHING outside that module ever read it. Not the Telegram handler
that computes it three hundred lines above the model call, not either web
route, not the prompt.

The prompt meanwhile carried FIVE length rules at once -- "keep answers short
and actionable", "quick questions = 2-4 lines", "scans = ~10-15 lines", "keep
Quick Mode under 50 words, Full Scan under 300 words", plus a six-section SCAN
FORMAT -- on every turn, including "thanks". It also named "Quick Mode" and
"Full Scan" as though the model had been told which one it was in. It had not.

So the fix is one contract per turn, selected by the mode that was already
being computed, and the numbers in the contracts are the prompt's OWN numbers.
Nothing here is a new opinion about length.

THE SECOND HALF IS THE PART THAT COULD HAVE GONE WRONG. Two of the six
contracts ask for NUMBERS: `full_scan` wants six sections of structure and
momentum, `execution` wants an entry, a stop and a target. Public (anonymous
website) chat is served from a STATIC prompt with no ticker block, no portfolio
and no history -- it cannot source one. `_public_chat_turn` already refuses the
scan-shaped asks that `needs_live_market_data` catches, but that predicate is
about LIVE DATA and the mode is about ANSWER SHAPE, and they disagree on real
sentences: "full analysis of ETH" and "trade plan for btc" are False to the
predicate and data-backed to the mode. Handing those two contracts to the
public prompt would have put a scan skeleton, or an entry/stop/target, on the
one surface with nothing to fill it -- reintroducing the thing the gate exists
to prevent, one layer underneath it.
"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from bot.nlp.intent_router import detect_reply_mode, needs_live_market_data
from bot.skills.chat_runtime import (
    _PUBLIC_REPLY_CONTRACTS,
    _REPLY_CONTRACTS,
    DATA_BACKED_MODES,
    DEFAULT_REPLY_MODE,
    reply_contract,
)

SHAPE_HEADING = "HOW LONG AND WHAT SHAPE"


def reply_modes() -> tuple:
    """The modes with a contract.

    This was a `reply_modes()` helper in chat_runtime.py until
    `test_no_new_unreachable_functions` pointed out that its only caller was
    this file -- "tests were the only caller", which is the exact shape that
    ratchet exists to catch, arrived at while fixing a different instance of
    it. Nothing in production needs the list; the test that does can read the
    map.
    """
    return tuple(sorted(_REPLY_CONTRACTS))


def _run(coro):
    return asyncio.run(coro)


# ── the contract is one, and it is the turn's own ───────────────────────────

def test_a_turn_carries_exactly_one_answer_shape():
    """The whole point. Five rules at once is the defect; one is the fix."""
    for mode in reply_modes():
        for public in (False, True):
            block = reply_contract(mode, public=public)
            assert block.count(SHAPE_HEADING) == 1, (mode, public)
            assert block.count("THIS TURN:") == 1, (mode, public)


def test_no_contract_contains_another_contracts_body():
    """A mode must not smuggle in a second shape. Bodies are compared whole,
    so a contract that quoted another one would fail here rather than reach a
    model that then has two lengths to satisfy."""
    for mode, body in _REPLY_CONTRACTS.items():
        rendered = reply_contract(mode)
        for other, other_body in _REPLY_CONTRACTS.items():
            if other == mode:
                continue
            assert other_body not in rendered, f"{mode} carries {other}"


def test_every_mode_the_router_can_detect_has_a_contract():
    """`reply_modes()` and the router's outputs are pinned against each other.
    A mode the router emits and this map lacks falls silently to `standard` --
    the quiet half of the defect, because the reply still looks fine."""
    corpus = [
        "long or short?", "is it safe?", "yes or no?",              # quick
        "grid bot", "dca logic", "is this setup bot ready",         # bot
        "where do i enter", "give me a signal", "trade plan for btc",  # execution
        "scan btc", "full analysis of ETH", "deep dive",            # full_scan
        "explain what a liquidity sweep is", "how does funding work",  # beginner
        "what is runeclaw", "thanks", "should i buy eth now",       # standard
    ]
    seen = {detect_reply_mode(c) for c in corpus}
    assert seen == set(reply_modes()), (
        "the corpus must reach every mode, and every mode it reaches must "
        f"have a contract; corpus={sorted(seen)} contracts={reply_modes()}")


def test_an_unknown_mode_falls_back_to_standard_not_to_nothing():
    """A turn with NO contract is a turn back under the five-at-once prompt."""
    for junk in ("", "   ", "zzz", "Full_Scan_v2", None):
        assert reply_contract(junk) == reply_contract(DEFAULT_REPLY_MODE), junk
    assert SHAPE_HEADING in reply_contract("zzz")


def test_the_mode_lookup_is_case_and_whitespace_tolerant():
    assert reply_contract("  FULL_SCAN ") == reply_contract("full_scan")


def test_the_default_is_a_real_mode_not_a_stand_in():
    """Defaulting costs no honesty HERE and would cost it elsewhere: a
    contract shapes how an answer reads and asserts nothing to the user, which
    is exactly why a price or a P&L may not default the same way."""
    assert DEFAULT_REPLY_MODE in _REPLY_CONTRACTS
    assert DEFAULT_REPLY_MODE in reply_modes()


# ── the five rules the prompt used to carry all at once ─────────────────────

REMOVED_RULES = (
    "Keep answers short and actionable",
    "ANSWER LENGTH:",
    "SCAN FORMAT",
    "Keep Quick Mode under 50 words",
    "ALWAYS END WITH",
    # Six, not five. The closing-line rule was stated TWICE -- once as
    # "ALWAYS END WITH" at the foot of the prompt and once as step 5 of HOW TO
    # RESPOND, three hundred characters apart, both unconditional. Removing one
    # and leaving the other is how five competing rules accumulated to begin
    # with, and it is what the first pass at this fix did.
    "End with what to watch next",
)


def test_the_base_prompt_no_longer_states_a_length_rule_of_its_own():
    """Each literal is long and distinctive, because asserting a SHORT string
    is absent is the assertion that keeps misfiring in this repo -- "0.0%"
    matched inside "(default 10.0%)" once."""
    from bot.skills.telegram_handler import TelegramHandler
    base = TelegramHandler._CHAT_SYSTEM_PROMPT
    for rule in REMOVED_RULES:
        assert rule not in base, f"{rule!r} is back in the base prompt"


def test_the_base_prompt_never_names_a_mode_it_does_not_set():
    """It said "Quick Mode" and "Full Scan" to a model that had not been told
    which it was in. Those words belong in a contract that is present only on
    the turn it describes."""
    from bot.skills.telegram_handler import TelegramHandler
    base = TelegramHandler._CHAT_SYSTEM_PROMPT
    assert "Quick Mode" not in base
    assert "Full Scan" not in base


def test_the_prompt_still_tells_the_model_to_read_before_it_asks():
    """The one rewrite rather than a deletion: step 2 said "if info is
    missing, ask one quick question" on a surface that now carries read-only
    TOOLS. Asking the user for something a tool could fetch is the chat
    equivalent of rendering an unread field."""
    from bot.skills.telegram_handler import TelegramHandler
    base = TelegramHandler._CHAT_SYSTEM_PROMPT
    assert "READ it if a tool can get it" in base


# ── the public surface cannot source a number ───────────────────────────────

def test_the_data_backed_modes_are_exactly_the_ones_with_a_public_override():
    """One named set drives both, so the override map cannot drift out of
    step with the modes that need it."""
    assert DATA_BACKED_MODES == frozenset(_PUBLIC_REPLY_CONTRACTS)
    assert DATA_BACKED_MODES <= frozenset(reply_modes())


def test_a_data_backed_contract_never_reaches_the_public_prompt():
    for mode in DATA_BACKED_MODES:
        authed = reply_contract(mode, public=False)
        public = reply_contract(mode, public=True)
        assert authed != public, mode
        assert _REPLY_CONTRACTS[mode] not in public, mode


def test_the_public_scan_contract_prints_no_scan_skeleton():
    """An empty six-heading scan reads as an analysis that found nothing,
    which is the same false negative as a 0.00% for an unreadable price."""
    public = reply_contract("full_scan", public=True)
    for heading in ("Structure", "Momentum", "Setup quality", "Long scenario"):
        assert heading not in public, heading
    assert "no live market feed" in public.lower()


def test_the_public_execution_contract_forbids_a_number_in_plan_shape():
    public = reply_contract("execution", public=True)
    assert "Do NOT print an entry, a stop, a target or a size" in public
    assert "including as an example" in public, (
        "a number in plan shape is read as a plan whatever it is labelled")


def test_the_modes_the_public_gate_does_not_catch_are_the_reason_for_the_override():
    """The two predicates genuinely disagree, and this is the proof rather
    than an assertion about it. If `needs_live_market_data` is ever widened to
    cover these, this test says so by failing -- and the override becomes
    belt-and-braces rather than the only thing standing there."""
    escapes = [t for t in ("full analysis of ETH", "trade plan for btc",
                           "deep dive", "where do i enter")
               if not needs_live_market_data(t)]
    assert escapes, "the public gate no longer lets any data-backed ask through"
    for text in escapes:
        assert detect_reply_mode(text) in DATA_BACKED_MODES, text


def test_a_mode_the_public_surface_can_honour_is_unchanged_by_public():
    """The override SELECTS a contract; it does not suppress one. A visitor
    asking a beginner question gets the same beginner contract a member does."""
    for mode in set(reply_modes()) - DATA_BACKED_MODES:
        assert reply_contract(mode, public=True) == reply_contract(mode), mode


# ── the contract reaches the prompt ─────────────────────────────────────────

class _Recorder:
    """A stand-in `_llm_chat` that records the kwargs its callers pass."""

    def __init__(self, meta=True):
        self.calls: list[dict] = []
        self._meta = meta

    async def __call__(self, question, **kw):
        self.calls.append(dict(kw, question=question))
        if kw.get("return_meta"):
            return "answer", {"provider": "grok", "model": "grok-4.3"}
        return "answer"

    @property
    def mode(self) -> str:
        assert len(self.calls) == 1, self.calls
        return self.calls[0].get("reply_mode", "<not passed>")


def _request(handler, body, engine=None):
    async def _json():
        return body

    return SimpleNamespace(
        app={"tg_handler": handler, "engine": engine or SimpleNamespace()},
        json=_json, headers={}, remote="1.2.3.4")


# ── wiring: public ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("text,expected", [
    ("how does funding work", "beginner"),
    ("grid bot", "bot"),
    ("full analysis of ETH", "full_scan"),
    ("what is runeclaw", "standard"),
])
def test_public_chat_hands_the_turns_shape_to_the_model(text, expected):
    """Driven, not grepped. The scan a mutation would survive is one that
    keeps the kwarg and passes a constant."""
    from bot.web import user_gateway as ug
    rec = _Recorder(meta=False)
    handler = SimpleNamespace(_llm_chat=rec)
    resp = _run(ug._public_chat_turn(_request(handler, {"text": text})))
    assert json.loads(resp.text)["intent"] == "chat"
    assert rec.mode == expected


def test_public_chat_detects_its_own_mode_because_it_has_no_intent():
    """An anonymous visitor has no account to dispatch a skill against, so
    this route never builds an `IntentResult` -- which is why the mode is
    detected here and `detect_reply_mode` is exported at all."""
    from bot.web import user_gateway as ug
    rec = _Recorder(meta=False)
    handler = SimpleNamespace(_llm_chat=rec)
    _run(ug._public_chat_turn(_request(handler, {"text": "grid bot"})))
    assert rec.calls[0]["public"] is True, (
        "the public flag must travel with the mode, or the override never runs")


# ── wiring: the prompt the model actually receives ──────────────────────────

class _Conversations:
    def __init__(self):
        self.rows: list = []

    def get_recent_as_llm_messages(self, user_id, limit=8, drop_trailing_user=False):
        return []

    def append(self, uid, role, content, metadata=None):
        self.rows.append((uid, role, content, metadata or {}))


def _llm_stub():
    """The narrowest `self` `_llm_chat` will run against -- the shape
    `test_chat_tool_calling.py` established, with no registry so the tool-less
    branch is taken and one provider call is made."""
    from bot.core.cost import CostTracker
    from bot.skills.chat_runtime import _CHAT_NO_TOOLS_RULE
    from bot.skills.telegram_handler import TelegramHandler
    return SimpleNamespace(
        engine=SimpleNamespace(cost=CostTracker(), analyzer=None),
        conversations=_Conversations(),
        _build_chat_system_prompt=lambda user_id, user_name="": (
            "BASE PROMPT\n" + _CHAT_NO_TOOLS_RULE + "\nPERSONALITY"),
        _PUBLIC_CHAT_SYSTEM_PROMPT=TelegramHandler._PUBLIC_CHAT_SYSTEM_PROMPT,
        _is_admin=lambda update: False,
        _note_chat_llm_failure=lambda reason="": None,
    )


@pytest.fixture
def prompts(monkeypatch):
    """Capture every system prompt `_llm_chat` hands a provider."""
    from dataclasses import replace

    import bot.skills.telegram_handler as th_mod
    from bot.llm.provider import BYOK, LLMConfig, LLMProvider
    BYOK.reset()
    seen: list[str] = []

    async def _complete(client, cfg, sys_p, q, **kw):
        seen.append(sys_p)
        return "answer"

    monkeypatch.setattr(th_mod, "llm_complete", _complete)
    monkeypatch.setattr(
        th_mod, "resolve_tier_config",
        lambda *a, **kw: LLMConfig(provider=LLMProvider.GROK, api_key="k",
                                   model="grok-4.3"))
    monkeypatch.setattr(th_mod, "create_llm_client", lambda cfg: object())
    monkeypatch.setattr(th_mod, "resolve_profile_note", lambda note, uid: "")
    monkeypatch.setattr(th_mod, "CONFIG", replace(
        th_mod.CONFIG, llm=replace(th_mod.CONFIG.llm, api_key="")))
    for env in ("GEMINI_API_KEY", "ANTHROPIC_API_KEY", "ALIBABA_API_KEY"):
        monkeypatch.delenv(env, raising=False)
    yield seen
    BYOK.reset()


@pytest.mark.parametrize("mode", sorted(_REPLY_CONTRACTS))
def test_the_turns_contract_reaches_the_model(prompts, mode):
    from bot.skills.telegram_handler import TelegramHandler as H
    _run(H._llm_chat(_llm_stub(), "q", user_id="u1", reply_mode=mode))
    assert len(prompts) == 1
    assert _REPLY_CONTRACTS[mode] in prompts[0], mode
    assert prompts[0].count(SHAPE_HEADING) == 1, "one document, one shape rule"


def test_the_contract_is_the_last_instruction_in_the_document(prompts):
    """Appended last so it is the most recent thing the model reads -- and so
    that nothing added later (language, web search, profile) can be read as
    overriding the shape of this one turn."""
    from bot.skills.telegram_handler import TelegramHandler as H
    _run(H._llm_chat(_llm_stub(), "q", user_id="u1", reply_mode="quick",
                     reply_lang="es"))
    body = prompts[0]
    assert body.rstrip().endswith(_REPLY_CONTRACTS["quick"].rstrip())
    assert "LANGUAGE: Write your ENTIRE reply in Spanish" in body, (
        "the language directive still arrives; it is simply no longer last")


def test_a_caller_that_passes_no_mode_still_gets_exactly_one_contract(prompts):
    """Every OTHER `_llm_chat` caller -- vision, contract studio, research --
    passes no mode on purpose, and must not fall back to no contract at all."""
    from bot.skills.telegram_handler import TelegramHandler as H
    _run(H._llm_chat(_llm_stub(), "q", user_id="u1"))
    assert prompts[0].count(SHAPE_HEADING) == 1
    assert _REPLY_CONTRACTS[DEFAULT_REPLY_MODE] in prompts[0]


@pytest.mark.parametrize("mode", sorted(DATA_BACKED_MODES))
def test_the_public_prompt_never_receives_the_data_backed_contract(prompts, mode):
    """The one that would have mattered. `public=True` selects the override,
    so a scan skeleton and an entry/stop/target cannot reach the surface that
    has no feed to fill them."""
    from bot.skills.telegram_handler import TelegramHandler as H
    _run(H._llm_chat(_llm_stub(), "q", user_id="", public=True, reply_mode=mode))
    body = prompts[0]
    assert _REPLY_CONTRACTS[mode] not in body, mode
    assert _PUBLIC_REPLY_CONTRACTS[mode] in body, mode
    assert body.count(SHAPE_HEADING) == 1


def test_the_authed_prompt_does_receive_it(prompts):
    """The mirror of the test above -- the override must be about the SURFACE,
    not a quiet deletion of two contracts."""
    from bot.skills.telegram_handler import TelegramHandler as H
    _run(H._llm_chat(_llm_stub(), "q", user_id="u1", reply_mode="execution"))
    assert _REPLY_CONTRACTS["execution"] in prompts[0]


# ── wiring: the two signed-in surfaces ──────────────────────────────────────

def _telegram_handler(tmp_path, recorder):
    """The Telegram surface, as far as `_handle_message`'s chat fallback."""
    from bot.nlp.intent_router import IntentRouter
    from bot.skills.telegram_handler import TelegramHandler as H
    from bot.utils.user_store import SELF_ADMISSION_BY, SELF_ADMISSION_ROLE, UserStore

    class _Risk:
        circuit_breaker_active = False

        def pending_retrip_reason(self):
            return ""

    h = H.__new__(H)
    h.users = UserStore(tmp_path / "users.json")
    h.engine = SimpleNamespace(risk=_Risk(), _halted=False, _pending_ideas={},
                               _user_risk={}, _user_store=h.users,
                               pending_ideas=[])
    h.registry = SimpleNamespace(get=lambda n: None,
                                 dispatch=lambda *a, **kw: asyncio.sleep(0))
    h._limiter = SimpleNamespace(allow=lambda uid: True)
    h.conversations = SimpleNamespace(append=lambda *a, **kw: None,
                                      get=lambda *a, **kw: [])
    h.forwarder = SimpleNamespace(detect_group=lambda *a, **kw: None)
    h._pending_limit_input = {}
    h.intent_router = IntentRouter()
    h.sent: list = []

    async def _send(update, text, **kwargs):
        h.sent.append(text)

    async def _false(*a, **kw):
        return False

    h._send = _send
    h._llm_chat = recorder
    h._request_operator_admission = _false
    h._send_photo = _false
    h.users.register("4242", name="Walkin")
    h.users.authorize("4242", role=SELF_ADMISSION_ROLE, by=SELF_ADMISSION_BY)
    return h


def _update(uid, text):
    msg = SimpleNamespace(text=text)

    async def _noop(*a, **kw):
        return None

    msg.reply_text = _noop
    chat = SimpleNamespace(id=int(uid), type="private", title="",
                           send_chat_action=_noop)
    return SimpleNamespace(
        effective_user=SimpleNamespace(id=int(uid), first_name="T",
                                       language_code="en"),
        effective_chat=chat, message=msg, callback_query=None)


@pytest.fixture
def _tg_config():
    for mod in ("bot.skills.telegram_handler", "bot.core.engine"):
        mc = patch(f"{mod}.CONFIG").start()
        mc.telegram.chat_id = "1"
        mc.telegram.admin_ids = ""
        mc.telegram.live_trader_ids = ""
        mc.paper_auto_accept = False
        mc.per_user_live_enabled = False
        mc.is_live.return_value = False
        mc.llm.chat_streaming_enabled = False
    yield
    patch.stopall()


@pytest.mark.asyncio
async def test_telegram_chat_hands_the_turns_shape_to_the_model(
        tmp_path, _tg_config, monkeypatch):
    """The value was computed at the top of `_handle_message` and dropped
    three hundred lines before the call that names its vocabulary. Driven end
    to end because a source scan cannot tell a kwarg that is PRESENT from one
    that is READ -- the exact distinction this repo has been burned by twice."""
    from bot.web import chat_quota
    monkeypatch.setattr(chat_quota, "quota_enabled", lambda: False)
    rec = _Recorder()
    h = _telegram_handler(tmp_path, rec)
    await h._handle_message(_update("4242", "explain what a liquidity sweep is"), None)
    assert rec.mode == "beginner"


@pytest.mark.asyncio
async def test_the_telegram_mode_follows_the_message_not_a_constant(
        tmp_path, _tg_config, monkeypatch):
    """A mutation that hard-codes one mode keeps the kwarg and survives any
    single-message drive."""
    from bot.web import chat_quota
    monkeypatch.setattr(chat_quota, "quota_enabled", lambda: False)
    seen = []
    for text, expected in (("explain what a liquidity sweep is", "beginner"),
                           ("grid bot", "bot"),
                           ("what is runeclaw", "standard")):
        rec = _Recorder()
        h = _telegram_handler(tmp_path, rec)
        await h._handle_message(_update("4242", text), None)
        seen.append((rec.mode, expected))
    assert [a for a, _ in seen] == [b for _, b in seen], seen
    assert len({a for a, _ in seen}) == 3, "three messages, three shapes"


def _web_handler(recorder):
    from bot.nlp.intent_router import IntentRouter
    return SimpleNamespace(
        intent_router=IntentRouter(),
        registry=SimpleNamespace(get=lambda n: None),
        conversations=_Conversations(),
        users=SimpleNamespace(get_tier=lambda uid: "elite",
                              is_authorized=lambda uid: True,
                              get_role=lambda uid: "trader"),
        _llm_chat=recorder,
    )


@pytest.mark.parametrize("text,expected", [
    ("explain what a liquidity sweep is", "beginner"),
    ("grid bot", "bot"),
    ("what is runeclaw", "standard"),
])
def test_web_chat_hands_the_turns_shape_to_the_model(monkeypatch, text, expected):
    from bot.web import user_gateway as ug
    monkeypatch.setattr(ug, "_guard_user", lambda *a, **kw: None)
    monkeypatch.setattr(ug, "_is_admin_id", lambda h, uid: False)
    monkeypatch.setattr(ug, "build_profile_note", lambda p: "")
    rec = _Recorder()
    handler = _web_handler(rec)
    engine = SimpleNamespace(firewall_scan=lambda *a, **kw: None,
                             _pending_ideas={})
    resp = _run(ug._chat_turn(_request(
        handler, {"telegram_id": "4242", "text": text}, engine=engine)))
    assert json.loads(resp.text)["intent"] == "chat"
    assert rec.mode == expected


# ── the routing fast-exit was answering a question about shape ─────────────

PURE_SOCIAL = (
    "hey", "hi", "hello", "thanks", "thank you", "ty", "bye", "gn", "ok",
    "okay", "sure", "yep", "cool", "nice", "got it", "np", "lol", "haha",
    "how are you", "who are you", "what are you", "what can you do",
    "tell me about yourself", "you there", "are you alive", "see ya",
    "peace", "all good", "makes sense",
)


def test_every_actually_social_message_still_answers_in_the_general_shape():
    """The list the fix's own comment promises. `classify_rules`' social
    branch used to hard-code `standard`; it detects now, and this is the proof
    that detecting costs nothing on the messages the branch is FOR."""
    from bot.nlp.intent_router import IntentRouter, _is_social_message
    router = IntentRouter()
    for text in PURE_SOCIAL:
        assert _is_social_message(text), f"{text!r} is not reaching the branch"
        assert router.classify_rules(text).reply_mode == DEFAULT_REPLY_MODE, text


@pytest.mark.parametrize("text,expected", [
    ("grid bot", "bot"),
    ("dca logic", "bot"),
    ("bro can you explain what a liquidity sweep is", "beginner"),
])
def test_a_real_question_routed_as_social_keeps_its_own_shape(text, expected):
    """`_is_social_message` calls anything of three words or fewer social
    unless it carries a trading word, and matches a leading `bro|dude|mate`.
    Routing it to chat is right; telling the prompt it is small talk is not."""
    from bot.nlp.intent_router import IntentRouter, _is_social_message
    result = IntentRouter().classify_rules(text)
    assert _is_social_message(text) and result.is_social, text
    assert result.skill == "", "routing is unchanged -- still no skill"
    assert result.reply_mode == expected, text


def test_the_intent_carries_the_same_reading_the_router_would_give():
    """One reading, four return paths. A branch that computes its own answer
    is a second answer, which is the rule this repo states about `MIN_RATED`."""
    from bot.nlp.intent_router import IntentRouter
    router = IntentRouter()
    corpus = list(PURE_SOCIAL) + [
        "grid bot", "scan btc", "explain what a sweep is", "where do i enter",
        "full analysis of ETH", "long or short?", "what is runeclaw",
        "analyze", "check my portfolio", "thanks bro",
    ]
    for text in corpus:
        assert router.classify_rules(text).reply_mode == detect_reply_mode(text), text


# ── what this file does NOT claim ───────────────────────────────────────────
#
# It does not check that a contract's PROSE is right for its mode. A mutation
# that rewrites the beginner contract's opening in the standard contract's
# words survives every test here, and killing it would mean pinning literals
# out of a document written to be reworded -- the `/portfolio` label failure
# this repo records, where the grep passed with the label present and the list
# moved on top of it. Two structural properties underneath the prose are
# checkable, and they are the ones that matter: a contract must SAY something
# about shape, and no two modes may say the same thing.

def test_every_contract_bounds_the_length_it_asks_for():
    """A contract that states no bound is the absence of a rule wearing the
    name of one -- and the absence of a rule is what five competing rules
    amounted to."""
    import re
    bound = re.compile(r"\b(under \d+ words|\d+-\d+ lines)\b", re.IGNORECASE)
    for mode, body in list(_REPLY_CONTRACTS.items()) + list(
            _PUBLIC_REPLY_CONTRACTS.items()):
        assert bound.search(body), f"{mode} states no length bound"


def test_no_two_modes_carry_the_same_contract():
    """Six modes, six documents. A copy-paste that gave two modes one shape
    would leave the router detecting a distinction the prompt cannot express."""
    bodies = list(_REPLY_CONTRACTS.values())
    assert len(set(bodies)) == len(bodies)
    for mode, body in _PUBLIC_REPLY_CONTRACTS.items():
        assert body not in _REPLY_CONTRACTS.values(), mode


# ── wiring: the fourth surface, which has no router in front of it ──────────

def test_the_api_facade_hands_the_turns_shape_to_the_model():
    """`chat_facade.ask()` is how an external PROGRAM reaches the one chat
    brain (api_bridge, the MCP `ask` tool). It runs no skill resolution and no
    intent classification, so like public chat it has no `IntentResult` --
    which is exactly why it was easy to miss when wiring the other three."""
    from bot.nlp import chat_facade
    rec = _Recorder()
    handler = SimpleNamespace(_llm_chat=rec, conversations=_Conversations())
    _run(chat_facade.ask(handler, "explain what a liquidity sweep is",
                         user_id="u1"))
    assert rec.mode == "beginner"


def test_the_api_facade_carries_its_public_flag_with_the_mode():
    """A public API turn is served the same account-free prompt the website
    is, so it needs the same override -- the flag and the mode must arrive
    together or the override never runs."""
    from bot.nlp import chat_facade
    rec = _Recorder()
    handler = SimpleNamespace(_llm_chat=rec, conversations=_Conversations())
    _run(chat_facade.ask(handler, "full analysis of ETH", public=True))
    call = rec.calls[0]
    assert call["reply_mode"] == "full_scan" and call["public"] is True


def test_every_general_chat_surface_delivers_the_turns_shape():
    """The corollary this repo states as a rule: ask which OTHER surface makes
    the same claim before calling the fix done. Four do -- Telegram, the web's
    signed-in chat, public chat and the API facade -- and the fourth was found
    by enumerating `_llm_chat`'s callers rather than by remembering them.

    The four left out are deliberate, and they are counted here so the
    omission is a decision on record rather than a gap: two vision handlers,
    the contract studio and research each build a purpose-written prompt whose
    shape is the whole point of the call, and a general answer contract on top
    of one would be the two-shapes-at-once defect in a new place.

    Read with `ast`, not by slicing text. The first draft split on the literal
    and cut each call at its first `)`, which is the one closing
    `sanitize_chat_input(text)` -- so every call looked like it carried no
    keywords at all. A parse knows where a call ends; a substring does not.
    """
    import ast
    import inspect

    from bot.nlp import chat_facade
    from bot.skills import telegram_handler
    from bot.web import user_gateway

    carriers, plain = [], []
    for mod in (user_gateway, chat_facade, telegram_handler):
        tree = ast.parse(inspect.getsource(mod))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            if not (isinstance(fn, ast.Attribute) and fn.attr == "_llm_chat"):
                continue
            where = f"{mod.__name__}:{node.lineno}"
            kw = {k.arg for k in node.keywords}
            (carriers if "reply_mode" in kw else plain).append(where)

    assert len(carriers) == 4, (
        "telegram chat + web authed + public + api facade", carriers)
    assert len(plain) == 4, (
        "two vision handlers + contract studio + research", plain)
    # And the total is pinned, because the failure this whole test is about is
    # a caller nobody enumerated. A new `_llm_chat` call site fails here until
    # somebody decides which list it belongs in.
    assert len(carriers) + len(plain) == 8, "a new caller appeared -- decide"
