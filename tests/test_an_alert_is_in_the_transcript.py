"""An alert the bot sent unprompted is a turn the model can be asked about.

THE ALERT LOOP IS THE DOOR THAT SPEAKS FIRST, and it was the last one with no
record. `_REPLY_CAPTURE`'s own comment named it — "the free-text path and the
alert loop untouched" — and the free-text half was closed two slices ago. Here
nobody types anything, so there is no user turn to hang a record on and no tool
result to file it under: "what was that about?" reached a model with nothing in
its history, and the only downstream guard refuses a fabricated
`[skill] result:` block, which a narrated alert is not.

Four things are driven here, never scanned:

* the RING — bounded, dated, kept apart from the conversation, and not a
  mention of anything;
* PERSISTENCE, including the trap: `_maybe_compact` rewrites the file from
  in-memory state, so a row type it does not re-emit is not pruned, it is
  DESTROYED (the `secrets_vault._load_vault` shape);
* the PROMPT BLOCK — three outcomes, and wired into the prompt both surfaces
  build;
* the two WRITE SITES — the monitor's `_dispatch` (34 alert types) and the
  event hooks in `alerts_monitor` (a trade CLOSED, a limit FILLED, positions
  ADOPTED, and an auto-confirmed trade that was PLACED).
"""
from __future__ import annotations

import asyncio
import json
import time
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest

from bot.nlp.conversation_store import ConversationStore
from bot.skills.telegram_handler import _ALERTS_HEAD, _unprompted_alerts_block

NOW = 1_800_000_000.0
ALICE = "111"


def _store(tmp_path=None, **kw):
    kw.setdefault("max_messages_per_user", 50)
    kw.setdefault("max_users", 200)
    if tmp_path is not None:
        kw["persist_path"] = str(tmp_path / "conv.jsonl")
    return ConversationStore(**kw)


# ── the ring ─────────────────────────────────────────────────────────────

class TestTheRingHoldsWhatWasSent:
    def test_a_recorded_alert_comes_back_dated_and_named(self):
        s = _store()
        s.note_alert(ALICE, "SL_PROXIMITY", "STOP LOSS APPROACHING\nETH/USDT",
                     at=NOW)
        assert s.recent_alerts(ALICE) == [
            {"at": NOW, "kind": "SL_PROXIMITY",
             "text": "STOP LOSS APPROACHING\nETH/USDT"}]

    def test_it_is_bounded_and_the_oldest_goes(self):
        s = _store()
        for i in range(s.NOTIFICATIONS_MAX + 4):
            s.note_alert(ALICE, "X", f"alert {i}", at=NOW + i)
        rows = s.recent_alerts(ALICE)
        assert len(rows) == s.NOTIFICATIONS_MAX
        assert rows[0]["text"] == "alert 4" and rows[-1]["text"] == "alert 11"

    def test_the_caller_gets_a_copy(self):
        s = _store()
        s.note_alert(ALICE, "X", "one", at=NOW)
        got = s.recent_alerts(ALICE)
        got[0]["text"] = "edited"
        got.append({"at": NOW, "kind": "Y", "text": "planted"})
        assert s.recent_alerts(ALICE) == [
            {"at": NOW, "kind": "X", "text": "one"}]

    def test_an_empty_body_is_not_an_alert(self):
        s = _store()
        s.note_alert(ALICE, "X", "", at=NOW)
        s.note_alert(ALICE, "X", "   \n  ", at=NOW)
        s.note_alert(ALICE, "X", "<b></b>", at=NOW)
        assert s.recent_alerts(ALICE) == []

    def test_a_long_body_says_it_was_cut(self):
        s = _store()
        body = "x" * (s.NOTIFICATION_MAX_CHARS + 37)
        s.note_alert(ALICE, "X", body, at=NOW)
        kept = s.recent_alerts(ALICE)[0]["text"]
        assert kept.startswith("x" * s.NOTIFICATION_MAX_CHARS)
        assert "37 more characters not recorded" in kept

    def test_a_time_that_is_not_a_time_is_not_manufactured(self):
        """0.0 is this file's word for NOT ON RECORD. `now` would be a
        confident claim about a moment the caller could not name."""
        s = _store()
        # `True` is in the list because `turn_time` refuses a bool on purpose:
        # `isinstance(True, int)` is True, so a bare numeric check would read
        # it as the unix second 1. `None` is NOT in it — that is the caller
        # saying "now", which the test below drives.
        bad_times = (float("inf"), float("nan"), -5.0, 0, "when", True)
        for bad in bad_times:
            s.note_alert(ALICE, "X", f"body {bad}", at=bad)
        assert [r["at"] for r in s.recent_alerts(ALICE)] == \
            [0.0] * len(bad_times)

    def test_now_is_used_when_no_time_is_given(self):
        s = _store()
        before = time.time()
        s.note_alert(ALICE, "X", "body")
        assert before <= s.recent_alerts(ALICE)[0]["at"] <= time.time()

    def test_it_is_not_a_conversation_turn(self):
        """An alert is a NOTIFICATION. `max_messages_per_user` is 50 and the
        advisory budget alone is twelve an hour, so an alert appended as a
        message evicts the user's own conversation inside about four hours —
        and `append` pushes what it prunes into `pending_summary`, so the
        rolling note would then summarise the bot talking to itself."""
        s = _store()
        s.append(ALICE, "user", "how is ETH doing?")
        for i in range(60):
            s.note_alert(ALICE, "BLACK_SWAN", f"anomaly {i}", at=NOW + i)
        turns = [(m.role, m.content) for m in s.get_recent(ALICE, limit=50)]
        assert turns == [("user", "how is ETH doing?")]
        assert s.get_context(ALICE).pending_summary == []

    def test_an_alert_is_not_a_mention(self):
        """The bot naming ETH/USDT in a stop-loss card is the BOT discussing
        it. `last_discussed_asset` is a claim about the USER."""
        s = _store()
        s.note_alert(ALICE, "SL_PROXIMITY", "ETH/USDT stop approaching",
                     at=NOW)
        ctx = s.get_context(ALICE)
        assert ctx.last_discussed_asset == ""
        assert ctx.asset_mentions == {}

    def test_an_unknown_user_has_no_alerts_rather_than_raising(self):
        assert _store().recent_alerts("nobody") == []


# ── persistence, and the compaction trap ─────────────────────────────────

class TestTheRingSurvivesARestart:
    def test_a_recorded_alert_is_reloaded(self, tmp_path):
        s = _store(tmp_path)
        s.note_alert(ALICE, "SL_PROXIMITY", "STOP LOSS\nETH/USDT", at=NOW)
        again = _store(tmp_path)
        assert again.recent_alerts(ALICE) == [
            {"at": NOW, "kind": "SL_PROXIMITY", "text": "STOP LOSS\nETH/USDT"}]

    def test_a_reloaded_alert_is_still_not_a_turn(self, tmp_path):
        s = _store(tmp_path)
        s.append(ALICE, "user", "hi")
        s.note_alert(ALICE, "X", "an alert", at=NOW)
        again = _store(tmp_path)
        assert [m.content for m in again.get_recent(ALICE, limit=50)] == ["hi"]
        assert again.get_context(ALICE).last_discussed_asset == ""

    def test_a_row_with_no_time_says_so_rather_than_reading_as_the_epoch(
            self, tmp_path):
        p = tmp_path / "conv.jsonl"
        p.write_text(json.dumps({
            "user_id": ALICE, "role": "alert", "content": "an alert",
            "timestamp": 0, "metadata": {}}) + "\n")
        s = _store(tmp_path)
        assert s.recent_alerts(ALICE) == [
            {"at": 0.0, "kind": "alert", "text": "an alert"}]

    def test_an_empty_row_on_disk_does_not_evict_a_real_one(self, tmp_path):
        """The ring is BOUNDED, so a row nothing can render would push out a
        notification somebody can ask about. `note_alert` refuses the same
        row on the way in."""
        p = tmp_path / "conv.jsonl"
        rows = [json.dumps({"user_id": ALICE, "role": "alert", "content": "",
                            "timestamp": NOW, "metadata": {}})
                for _ in range(20)]
        rows.append(json.dumps({"user_id": ALICE, "role": "alert",
                                "content": "the real one", "timestamp": NOW,
                                "metadata": {"kind": "SL_PROXIMITY"}}))
        p.write_text("\n".join(rows) + "\n")
        assert [r["text"] for r in _store(tmp_path).recent_alerts(ALICE)] == [
            "the real one"]

    def test_the_ring_survives_compaction(self, tmp_path):
        """THE TRAP. `_maybe_compact` rewrites the file from IN-MEMORY state,
        so a row type the rewrite does not re-emit is not pruned, it is
        DESTROYED — the `secrets_vault._load_vault` shape, where the reader
        dropped what it could not open and the writer then saved the map
        wholesale."""
        s = _store(tmp_path)
        s.note_alert(ALICE, "SL_PROXIMITY", "STOP LOSS ETH/USDT", at=NOW)
        # Outgrow the threshold so the next load really compacts.
        with open(tmp_path / "conv.jsonl", "a") as f:
            for i in range(ConversationStore.COMPACT_THRESHOLD_LINES + 10):
                f.write(json.dumps({
                    "user_id": ALICE, "role": "user", "content": f"m{i}",
                    "timestamp": NOW, "metadata": {}}) + "\n")
        after_load = _store(tmp_path)
        assert after_load.recent_alerts(ALICE) == [
            {"at": NOW, "kind": "SL_PROXIMITY", "text": "STOP LOSS ETH/USDT"}]
        body = (tmp_path / "conv.jsonl").read_text()
        assert body.count('"role": "alert"') == 1, "compaction really ran"
        # And a THIRD process reads what compaction wrote.
        assert _store(tmp_path).recent_alerts(ALICE) == \
            after_load.recent_alerts(ALICE)

    def test_a_purge_erases_the_ring_in_memory_and_on_disk(self, tmp_path):
        s = _store(tmp_path)
        s.note_alert(ALICE, "X", "alice was told this", at=NOW)
        s.note_alert("222", "X", "bob was told this", at=NOW)
        assert s.clear_user(ALICE) is True
        assert s.recent_alerts(ALICE) == []
        again = _store(tmp_path)
        assert again.recent_alerts(ALICE) == []
        assert [r["text"] for r in again.recent_alerts("222")] == [
            "bob was told this"]


# ── the prompt block ─────────────────────────────────────────────────────

def _row(text, kind="SL_PROXIMITY", at=NOW - 3 * 3600):
    return {"at": at, "kind": kind, "text": text}


class TestTheBlockSaysWhatItIs:
    def test_an_unreadable_ring_says_so_rather_than_claiming_silence(self):
        """`None` is a read that FAILED. "You have sent them nothing" is a
        confident negative assembled from a read that never happened."""
        out = _unprompted_alerts_block(None, NOW)
        assert "could not be read just now" in out
        assert "do not know what you sent" in out.lower()
        assert "none on record" not in out

    def test_an_empty_ring_is_a_claim_about_the_RECORD(self):
        out = _unprompted_alerts_block([], NOW)
        assert "none on record" in out
        assert "not about their account" in out
        assert "could not be read" not in out

    def test_a_row_carries_its_age_its_kind_and_its_lines(self):
        out = _unprompted_alerts_block(
            [_row("STOP LOSS APPROACHING\nETH/USDT - Entry $3,000.0000")], NOW)
        assert "[3 h ago - SL_PROXIMITY]" in out
        assert "    STOP LOSS APPROACHING\n    ETH/USDT - Entry $3,000.0000" in out

    def test_a_row_with_no_time_says_so_rather_than_reading_as_56_years(self):
        out = _unprompted_alerts_block([_row("an alert", at=0.0)], NOW)
        assert "[time not on record - SL_PROXIMITY]" in out
        assert " y ago" not in out

    def test_the_block_says_who_said_it_and_when_it_was_true(self):
        """A stop-loss card from three hours ago names a price, and a model
        restating it as the current one is the fabrication this exists to
        prevent."""
        out = _unprompted_alerts_block([_row("an alert")], NOW)
        assert "YOU sent these" in out
        assert "the user did not say them" in out
        assert "no chat tool produced them" in out
        assert "never restate a figure from one as the state now" in out
        assert "never claim to have sent one that is not listed here" in out

    def test_every_outcome_carries_the_same_heading(self):
        for rows in (None, [], [_row("an alert")]):
            assert _ALERTS_HEAD in _unprompted_alerts_block(rows, NOW)

    def test_junk_rows_are_dropped_and_do_not_become_an_error_state(self):
        out = _unprompted_alerts_block(
            [None, "a string", {}, _row("the real one"), {"text": "   "}], NOW)
        assert "the real one" in out and out.count("\n  - [") == 1

    def test_a_ring_of_nothing_renderable_is_not_reported_as_a_failed_read(self):
        out = _unprompted_alerts_block([{"text": ""}], NOW)
        assert "none on record" in out and "could not be read" not in out


class TestTheBlockIsInThePromptBothSurfacesBuild:
    """DRIVEN, not scanned: a scan cannot see whether the block is reached,
    and the web chat builds its prompt through this same method."""

    def _prompt(self, conversations):
        from tests.test_chat_prompt_describes_only_the_callers_book import _handler
        from tests.test_the_paper_prompt_is_as_honest_as_the_live_one import _paper_engine, _paper_pf
        h = _handler(_paper_engine(_paper_pf()))
        h.conversations = conversations
        with pytest.MonkeyPatch.context() as mp:
            import bot.config
            mp.setattr(type(bot.config.CONFIG), "is_live",
                       lambda self: False, raising=False)
            from bot.skills.telegram_handler import TelegramHandler as H
            return H._build_chat_system_prompt(h, ALICE)

    def test_a_recorded_alert_reaches_the_prompt(self):
        s = _store()
        s.note_alert(ALICE, "SL_PROXIMITY", "STOP LOSS APPROACHING ETH/USDT")
        out = self._prompt(s)
        assert _ALERTS_HEAD in out
        assert "STOP LOSS APPROACHING ETH/USDT" in out

    def test_another_users_alert_does_not_reach_this_prompt(self):
        s = _store()
        s.note_alert("999", "SL_PROXIMITY", "BOB WAS TOLD THIS")
        out = self._prompt(s)
        assert "BOB WAS TOLD THIS" not in out and "none on record" in out

    def test_a_store_that_raises_says_could_not_be_read(self):
        class _Raises:
            build_context_prompt = staticmethod(lambda *a, **k: "")

            def recent_alerts(self, uid):
                raise RuntimeError("store down")

        out = self._prompt(_Raises())
        assert "could not be read just now" in out
        assert "none on record" not in out


# ── the write sites ──────────────────────────────────────────────────────

class _Users:
    """The admission store, by id. `authorized` alone is not admission —
    `register()` marks a stranger authorized, which is the F-2 hole
    `_is_allowlisted` closes — so the handler asks both questions."""

    def __init__(self, admitted=(), authorized=None):
        self._admitted = set(admitted)
        self._authorized = set(
            admitted if authorized is None else authorized)

    def get(self, tg_id):
        return ({"authorized": True, "role": "trader"}
                if str(tg_id) in self._authorized else None)

    def is_admitted(self, tg_id):
        return str(tg_id) in self._admitted


def _recorder(store, users, allowlist=()):
    """The handler's own `_note_unprompted`, bound to the smallest host that
    can answer its two questions."""
    from bot.skills.telegram_handler import TelegramHandler as H
    host = NS(conversations=store, users=users,
              _allowlist_ids=lambda: set(allowlist))
    for name in ("_note_unprompted", "_transcript_id", "_access_state"):
        setattr(host, name, getattr(H, name).__get__(host))
    return host


class TestOnlyAnAdmittedUserGetsATranscript:
    def test_an_admitted_user_is_recorded(self):
        s = _store()
        _recorder(s, _Users([ALICE]))._note_unprompted(ALICE, "X", "an alert")
        assert [r["text"] for r in s.recent_alerts(ALICE)] == ["an alert"]

    def test_a_chat_that_is_not_a_user_is_not_recorded(self):
        """`_enabled_chats` is a WATCH LIST and the operator chat ids are a
        config value: neither is a measurement of who this bot has admitted.
        The store evicts its least-recent users at 200."""
        s = _store()
        _recorder(s, _Users([]))._note_unprompted("77", "X", "an alert")
        assert s.recent_alerts("77") == [] and s.user_count() == 0

    def test_authorized_but_not_allowlisted_is_not_admitted(self):
        """`register()` marks a stranger `authorized`. That is the hole
        `_is_allowlisted` exists to close, and reading `authorized` alone
        would reopen it here."""
        s = _store()
        users = _Users(admitted=[], authorized=[ALICE])
        _recorder(s, users, allowlist=("999",))._note_unprompted(
            ALICE, "X", "an alert")
        assert s.recent_alerts(ALICE) == []

    def test_an_empty_id_is_refused(self):
        """An empty id is a refusal, not a key - the shape the limit-input
        arming was cured of, where a row was armed under a key nothing could
        match. `user_count` reads the CONVERSATION map and the ring is in
        the context map, so it cannot see this at all: the first draft of
        this assertion asked it, and the mutation survived."""
        s = _store()
        _recorder(s, _Users([""]))._note_unprompted("", "X", "an alert")
        assert s.recent_alerts("") == []
        assert s.get_context("") is None

    def test_a_store_fault_never_reaches_the_send_path(self):
        """This is bookkeeping ABOUT a message already delivered."""
        class _Broken:
            def note_alert(self, *a, **k):
                raise RuntimeError("store down")

        _recorder(_Broken(), _Users([ALICE]))._note_unprompted(
            ALICE, "X", "an alert")   # must not raise

    def test_the_two_admission_spellings_agree(self):
        """`_transcript_user` asks `_is_allowlisted(update)`; `_transcript_id`
        asks `_access_state(tg_id)`. They are the same rule in the two shapes
        their callers have, and NOTHING compared them — so this does, over
        every combination, because two spellings of an admission rule that
        nothing compares are two answers about whose history survives."""
        from bot.skills.telegram_handler import TelegramHandler as H
        for allowlist in ((), ("111",), ("999",)):
            for admitted in ((), ("111",)):
                for authorized in ((), ("111",)):
                    host = NS(users=_Users(admitted, authorized),
                              _allowlist_ids=lambda a=allowlist: set(a),
                              _get_tg_id=lambda u: ALICE)
                    for name in ("_transcript_id", "_transcript_user",
                                 "_access_state", "_is_allowlisted"):
                        setattr(host, name, getattr(H, name).__get__(host))
                    update = NS(effective_user=NS(id=int(ALICE)))
                    assert host._transcript_id(ALICE) == \
                        host._transcript_user(update), (
                            allowlist, admitted, authorized)


class TestTheMonitorRecordsWhatItDelivered:
    def _monitor(self, store, users, chats, allowlist=()):
        from bot.core.proactive_monitor import ProactiveMonitor
        m = ProactiveMonitor.__new__(ProactiveMonitor)
        m.engine = NS()
        m._enabled_chats = set(chats)
        m._chart_fn = None
        m._admin_fn = None
        m._record_fn = _recorder(store, users, allowlist)._note_unprompted
        return m

    def _dispatch(self, monitor, alert, failing=()):
        sent = []

        async def send_fn(chat_id, msg, *a):
            if str(chat_id) in failing:
                raise RuntimeError("telegram said no")
            sent.append((str(chat_id), msg))

        asyncio.run(monitor._dispatch(alert, send_fn))
        return sent

    def _alert(self, **kw):
        from bot.core.proactive_monitor import Alert
        base = dict(alert_type="SL_PROXIMITY", severity="WARNING",
                    title="SL Proximity: ETH/USDT",
                    body="STOP LOSS APPROACHING\nETH/USDT")
        base.update(kw)
        return Alert(**base)

    def test_a_delivered_alert_reaches_that_chats_transcript(self):
        s = _store()
        m = self._monitor(s, _Users([ALICE]), chats=(ALICE,))
        assert self._dispatch(m, self._alert(user_id=ALICE))
        rows = s.recent_alerts(ALICE)
        assert len(rows) == 1 and rows[0]["kind"] == "SL_PROXIMITY"
        assert "STOP LOSS APPROACHING" in rows[0]["text"]

    def test_the_record_is_the_message_as_sent(self):
        """WHAT WAS SENT, not what was built: the severity icon and the
        held-back note are part of what the reader saw, and the record is
        that text as the MODEL reads it - `plain_text`, the one reading the
        other seven records already share."""
        from bot.nlp.skill_memory import plain_text
        s = _store()
        m = self._monitor(s, _Users([ALICE]), chats=(ALICE,))
        sent = self._dispatch(m, self._alert(
            user_id=ALICE, body="STOP LOSS <b>ETH/USDT</b>"))
        kept = s.recent_alerts(ALICE)[0]["text"]
        assert kept == plain_text(sent[0][1])
        from bot.core.proactive_monitor import _SEVERITY_ICON
        assert _SEVERITY_ICON["WARNING"] in kept, \
            "the severity icon the reader saw"
        assert "<b>" not in kept and "ETH/USDT" in kept

    def test_a_send_that_failed_is_not_recorded(self):
        """A record above the await would tell the model the bot said
        something nobody received."""
        s = _store()
        m = self._monitor(s, _Users([ALICE]), chats=(ALICE,))
        assert self._dispatch(m, self._alert(user_id=ALICE),
                              failing=(ALICE,)) == []
        assert s.recent_alerts(ALICE) == []

    def test_it_is_per_recipient(self):
        """One chat can fail while another succeeds."""
        s = _store()
        m = self._monitor(s, _Users([ALICE, "222", "333"]),
                          chats=(ALICE, "222", "333"))
        self._dispatch(m, self._alert(user_id=None, audience="all"),
                       failing=("222",))
        assert [bool(s.recent_alerts(c)) for c in (ALICE, "222", "333")] == \
            [True, False, True]

    def test_another_users_alert_is_not_in_this_users_transcript(self):
        """#170's narrowing is what makes this safe to record at all: a leaked
        position in the wrong transcript would put the leak into the model's
        evidence too."""
        s = _store()
        m = self._monitor(s, _Users([ALICE, "222"]), chats=(ALICE, "222"))
        self._dispatch(m, self._alert(user_id=ALICE))
        assert s.recent_alerts("222") == []
        assert len(s.recent_alerts(ALICE)) == 1

    def test_a_monitor_with_no_recorder_still_sends(self):
        """Unset, nothing is recorded — which is exactly what every build
        before this one did."""
        from bot.core.proactive_monitor import ProactiveMonitor
        m = ProactiveMonitor.__new__(ProactiveMonitor)
        m.engine = NS()
        m._enabled_chats = {ALICE}
        m._chart_fn = None
        m._admin_fn = None
        assert self._dispatch(m, self._alert(user_id=ALICE))


# ── the event hooks, driven through the real start_monitor ───────────────

class _HookEngine:
    """Records the callbacks `start_monitor` installs, so each can be CALLED.

    A scan of those hook bodies cannot see whether the record is reached,
    which is the one thing being asked.
    """

    def __init__(self):
        self.cbs: dict = {}
        self.live_executor = None

    def set_close_notify_callback(self, fn):
        self.cbs["close"] = fn

    def set_fill_notify_callback(self, fn):
        self.cbs["fill"] = fn

    def set_sync_notify_callback(self, fn):
        self.cbs["sync"] = fn

    def set_adopt_notify_callback(self, fn):
        self.cbs["adopt"] = fn

    def set_auto_confirm_notify_callback(self, fn):
        self.cbs["auto"] = fn


def _started(store, users, chat_id, *, failing=False):
    """Run the real `start_monitor` and hand back what it installed."""
    import dataclasses

    import bot.config as bc
    from bot.core.proactive_monitor import ProactiveMonitor
    from bot.skills.telegram_handler import TelegramHandler as H

    original = bc.CONFIG.telegram
    object.__setattr__(bc.CONFIG, "telegram", dataclasses.replace(
        original, chat_id=chat_id, admin_ids=chat_id))

    mon = ProactiveMonitor.__new__(ProactiveMonitor)
    mon._enabled_chats, mon._chart_fn, mon._admin_fn = set(), None, None
    mon._record_fn, mon._dispatch = None, AsyncMock()
    mon.hydrate = lambda: None
    for attr in ("chart", "admin", "record", "dm", "anomaly_prefs"):
        setattr(mon, f"set_{attr}_fn",
                (lambda name: lambda fn: setattr(mon, f"_{name}_fn", fn))(attr))
    mon._configured_operator_chats = lambda: {chat_id}

    async def _never_runs(send_fn):
        return None

    mon.run = _never_runs

    sent: list = []

    async def _send(**kw):
        if failing:
            raise RuntimeError("telegram said no")
        sent.append(kw)

    bot_obj = NS(send_message=AsyncMock(side_effect=_send),
                 send_photo=AsyncMock(side_effect=_send))
    engine = _HookEngine()
    host = H.__new__(H)
    host.engine, host.monitor, host.users = engine, mon, users
    host.conversations = store
    host.forwarder = NS(set_bot=lambda b: None, post_signal=AsyncMock())
    host._allowlist_ids = lambda: set()
    host._monitor_task = None
    asyncio.run(host.start_monitor(bot_obj))
    return NS(host=host, monitor=mon, engine=engine, sent=sent,
              restore=lambda: object.__setattr__(bc.CONFIG, "telegram",
                                                 original))


class TestTheHooksRecordWhatTheyDelivered:
    def _drive(self, name, *args, failing=False, monkeypatch=None):
        s = _store()
        w = _started(s, _Users([ALICE]), ALICE, failing=failing)
        try:
            if monkeypatch is not None:
                monkeypatch.setenv("TELEGRAM_CHAT_ID", ALICE)
                monkeypatch.delenv("ADMIN_CHAT_ID", raising=False)
            asyncio.run(w.engine.cbs[name](*args))
        finally:
            w.restore()
        return s, w

    def test_the_monitor_installs_the_one_recorder(self):
        """One recorder for both alert doors — a second copy would be a
        second answer about who may have a transcript."""
        s = _store()
        w = _started(s, _Users([ALICE]), ALICE)
        w.restore()
        assert w.monitor._record_fn == w.host._note_unprompted

    def test_a_filled_limit_reaches_the_transcript(self):
        s, _ = self._drive("fill", "LIMIT FILLED ETH/USDT @ $3,000")
        rows = s.recent_alerts(ALICE)
        assert [r["kind"] for r in rows] == ["LIMIT_FILLED"]
        assert "LIMIT FILLED ETH/USDT" in rows[0]["text"]

    def test_a_closed_trade_reaches_the_transcript(self):
        s, _ = self._drive("close", "ETH/USDT LONG closed\nPnL: +$12.34")
        rows = s.recent_alerts(ALICE)
        assert [r["kind"] for r in rows] == ["TRADE_CLOSED"]
        assert "PnL: +$12.34" in rows[0]["text"]

    def test_an_exchange_sync_reaches_the_transcript(self):
        s, _ = self._drive("sync", "Adopted untracked position ETH/USDT")
        assert [r["kind"] for r in s.recent_alerts(ALICE)] == ["EXCHANGE_SYNC"]

    def test_adopted_positions_reach_the_transcript(self, monkeypatch):
        s, _ = self._drive("adopt", ["ETH/USDT"], monkeypatch=monkeypatch)
        assert [r["kind"] for r in s.recent_alerts(ALICE)] == [
            "POSITIONS_ADOPTED"]

    def test_an_auto_confirmed_TRADE_reaches_the_transcript(self, monkeypatch):
        """A TRADE WAS PLACED and the model did not know: no command was
        typed and no button was tapped, so neither of the two doors that
        already record could see it."""
        idea = NS(asset="ETH/USDT", direction=NS(value="LONG"),
                  confidence=0.91, entry_price=3000.0, stop_loss=2900.0,
                  take_profit=3300.0, id="I-1")
        s, _ = self._drive("auto", idea, "<b>Filled</b> 0.5 ETH",
                           monkeypatch=monkeypatch)
        rows = s.recent_alerts(ALICE)
        assert [r["kind"] for r in rows] == ["AUTO_CONFIRMED"]
        assert "AUTO-CONFIRMED TRADE" in rows[0]["text"]

    def test_the_stalled_monitor_notice_reaches_the_transcript(self):
        s = _store()
        w = _started(s, _Users([ALICE]), ALICE)
        try:
            asyncio.run(w.engine._monitor_stale_callback(120.0))
        finally:
            w.restore()
        assert [r["kind"] for r in s.recent_alerts(ALICE)] == [
            "MONITOR_STALLED"]

    def test_a_send_that_failed_records_nothing(self):
        s, w = self._drive("fill", "LIMIT FILLED ETH/USDT", failing=True)
        assert w.sent == [] and s.recent_alerts(ALICE) == []

    def test_a_chat_that_is_not_an_admitted_user_records_nothing(self):
        s = _store()
        w = _started(s, _Users([]), ALICE)
        try:
            asyncio.run(w.engine.cbs["fill"]("LIMIT FILLED ETH/USDT"))
        finally:
            w.restore()
        assert len(w.sent) == 1, "the operator is still TOLD"
        assert s.user_count() == 0, "and no transcript is created for them"


class TestEverySendInTheHooksGoesThroughTheOneWalk:
    """A chokepoint six call sites must remember is not a chokepoint.

    These hooks had SEVEN send loops between them, each with its own
    `try`/`except pass`, and a record added to six of them is the `/setllm`
    ten-of-eleven shape: the branch written next is the one that forgets.
    The drives above prove `_notify_chats` records; this proves nothing
    sends around it.
    """

    #: Each blessed sender, with the reason it is not `_notify_chats`.
    BLESSED = {
        # The monitor's OWN alert sender: `_dispatch` records through
        # `_record_fn`, so a second record here would be two.
        "_send_fn": "the monitor's alert sender, recorded by _dispatch",
        # A reply to ONE person that must RAISE, because the website acks it.
        "_dm_fn": "a price-alert trip, acked back to the website",
        # A PNG beside an alert already recorded by _dispatch.
        "_signal_card_fn": "the signal card image, not a message",
        "_notify_chats": "the one walk",
    }

    def _sends(self, source=None):
        import ast
        import inspect
        import textwrap

        from bot.skills import alerts_monitor
        from tests.source_scan import code_only

        src = textwrap.dedent(code_only(source or inspect.getsource(
            alerts_monitor.AlertsMonitor.start_monitor)))
        tree = ast.parse(src)
        found: list[tuple[str, str]] = []

        def walk(node, owner):
            for child in ast.iter_child_nodes(node):
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    walk(child, child.name)
                    continue
                if (isinstance(child, ast.Call)
                        and isinstance(child.func, ast.Attribute)
                        and child.func.attr.startswith("send_")
                        and isinstance(child.func.value, ast.Name)
                        and child.func.value.id in ("bot", "_bot_ref")):
                    found.append((owner, child.func.attr))
                walk(child, owner)

        walk(tree, "start_monitor")
        return found

    def test_no_hook_sends_around_the_walk(self):
        stray = [(o, a) for o, a in self._sends() if o not in self.BLESSED]
        assert not stray, (
            "a send outside the one walk records nothing: " + repr(stray))

    def test_the_walk_really_is_where_the_sends_are(self):
        owners = {o for o, _ in self._sends()}
        assert "_notify_chats" in owners, "the walk sends"
        assert len(self._sends()) >= 5, self._sends()

    def test_the_rule_can_fail(self):
        """A rule no input can reach is a claim that there is a check, so it
        is driven on a PLANTED tree where the rule is the only thing in
        play — the shape the methods ratchet's own guards use."""
        planted = (
            "async def start_monitor(self, bot):\n"
            "    async def _on_something_new(msg):\n"
            "        await bot.send_message(chat_id=1, text=msg)\n")
        found = self._sends(planted)
        assert found == [("_on_something_new", "send_message")], found
        assert [o for o, _ in found if o not in self.BLESSED] == [
            "_on_something_new"]


class TestTheThirdUnpromptedSender:
    """A delivered price-alert trip is as unprompted as either other door.

    Found by the sweep this repo requires before calling a fix done - the
    slice started from `_dispatch` and the event hooks, and leaving this one
    would be "fixing two left the third" inside the fix for it. The user
    armed a tripwire on the website; being told here, in this chat, that it
    fired is a turn they can see and the model could not.
    """

    def _monitor(self, store, users, rows, *, failing=False):
        from bot.core.proactive_monitor import ProactiveMonitor
        m = ProactiveMonitor.__new__(ProactiveMonitor)
        m._record_fn = _recorder(store, users)._note_unprompted
        m._last_trip_poll = 0.0
        from collections import deque
        m._delivered_trip_ids = deque(maxlen=500)
        self.sent = []

        async def _dm(chat_id, text):
            if failing:
                raise RuntimeError("bot blocked")
            self.sent.append((chat_id, text))

        m._dm_fn = _dm
        self.rows = rows
        return m

    def _run(self, m, monkeypatch):
        # The stage imports these from `web_data_pull` INSIDE the method, so
        # the module attribute is what a patch has to reach - patching the
        # monitor's own module changed nothing, and the first draft of this
        # fixture read the silence as the code not recording.
        from bot.utils import web_data_pull as wdp
        monkeypatch.setattr(wdp, "fetch_alert_trips", lambda limit=50: self.rows)
        monkeypatch.setattr(wdp, "ack_alert_trips", lambda acks: True)
        # A monotonic clock starts near zero on a freshly booted host, so a
        # `0.0` "never polled" can read as RECENT and skip the poll - the trap
        # `live_balance_cached` is on record for. Pin it well behind.
        m._last_trip_poll = time.monotonic() - m.TRIP_POLL_INTERVAL - 1.0
        asyncio.run(m._deliver_web_alert_trips())

    ROWS = [{"id": 7, "telegram_id": ALICE, "title": "BTC below 100k",
             "body": "BTC/USDT crossed below $100,000"}]

    def test_a_delivered_trip_reaches_that_chats_transcript(self, monkeypatch):
        s = _store()
        m = self._monitor(s, _Users([ALICE]), self.ROWS)
        self._run(m, monkeypatch)
        assert len(self.sent) == 1
        rows = s.recent_alerts(ALICE)
        assert [r["kind"] for r in rows] == ["PRICE_ALERT"]
        from bot.nlp.skill_memory import plain_text
        assert rows[0]["text"] == plain_text(self.sent[0][1])
        assert "BTC/USDT crossed below $100,000" in rows[0]["text"]

    def test_a_trip_that_could_not_be_delivered_is_not_recorded(
            self, monkeypatch):
        """`_dm_fn` RAISES where the alert sender swallows, so reaching the
        record is a delivery - and the ack says failed."""
        s = _store()
        m = self._monitor(s, _Users([ALICE]), self.ROWS, failing=True)
        self._run(m, monkeypatch)
        assert self.sent == [] and s.recent_alerts(ALICE) == []

    def test_a_chat_that_is_not_an_admitted_user_is_still_told(
            self, monkeypatch):
        s = _store()
        m = self._monitor(s, _Users([]), self.ROWS)
        self._run(m, monkeypatch)
        assert len(self.sent) == 1, "the trip is delivered"
        assert s.user_count() == 0 and s.recent_alerts(ALICE) == []
