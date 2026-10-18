""""Deep scan" in the web chat runs the deep scan, even while a batch runs.

18 October, a phone screenshot: "Deepscan" typed into AI Chat came back as
"[deepscan] result: Scan on file — TIA/USDT, FIL/USDT, … (20 pairs)". That is
`_gateway_scan_cache`, the autonomous scan's selected pairs, which the web
serves for a scan request while a batch holds the scan lock. A deep scan is a
pattern read across the universe; it takes no scan lock and runs under its
own web budget, so it had no reason to be held, and holding it meant it never
ran and the Deep Scan page never received a block.

Both arms, driven through the web turn: during a batch the deep scan runs and
its card is the answer; the plain scan still answers from the cache and runs
nothing.
"""
from __future__ import annotations

from types import SimpleNamespace

from bot.nlp.conversation_store import ConversationStore
from tests.test_a_routed_answer_is_in_the_transcript import _web, _web_turn

CACHE = "Scan on file — TIA/USDT, FIL/USDT (20 pairs)"


def _busy_engine():
    return SimpleNamespace(firewall_scan=lambda *a, **kw: None, _pending_ideas={},
                           _scan_batch_running=True,
                           _gateway_scan_cache={"reply_html": CACHE, "pairs": 20})


def _drive(monkeypatch, phrase):
    ran: list[str] = []
    ug, handler = _web(monkeypatch, ConversationStore())

    def _get(name):
        async def _exec(engine, **kw):
            ran.append(name)
            return f"<b>{name} card</b>"
        return SimpleNamespace(execute=_exec)

    handler.registry = SimpleNamespace(get=_get)
    handler.users.permission_denial = lambda uid, perm: None
    return _web_turn(ug, handler, phrase, engine=_busy_engine()), ran


def test_a_deep_scan_during_a_batch_runs_and_is_not_the_pair_list(monkeypatch):
    body, ran = _drive(monkeypatch, "deep scan")
    assert ran == ["deepscan"], ran
    assert body["reply_html"] == "<b>deepscan card</b>"
    assert "Scan on file" not in body["reply_html"]
    assert "scan_read" not in body


def test_the_plain_scan_during_a_batch_still_answers_from_the_card_on_file(monkeypatch):
    body, ran = _drive(monkeypatch, "scan")
    assert ran == [], "a held scan must not run while the batch holds the lock"
    assert body["reply_html"] == CACHE and body.get("scan_read") == "cached"
