"""A turn the token window leaves out is queued for the dated note.

The model reads the newest 6,000 tokens; the store keeps fifty rows; only the
cap's prune queued a turn for the summary. A turn between the two edges was in
neither: "I am paper only, never suggest a live order", followed by fifteen
exchanges with a portfolio card each, was out of the window, not pruned, and
`take_pending_summary` returned nothing.
"""
from bot.nlp.conversation_store import ConversationStore

CARD = "[get_portfolio] result: " + "equity line " * 60


def _flood(store, uid, n):
    for i in range(n):
        store.append(uid, "user", f"how is my book {i}")
        store.append(uid, "assistant", CARD, metadata={"skill": "get_portfolio"})
        store.append(uid, "assistant", f"answer {i}")


def _window(store, uid, budget=6000):
    return store.get_recent_as_llm_messages(uid, token_budget=budget)


def test_the_instruction_out_of_the_window_is_queued_once():
    store = ConversationStore(max_messages_per_user=50)
    store.append("u", "user", "INSTRUCTION: I am paper only, never suggest a live order")
    store.append("u", "assistant", "Noted.")
    _flood(store, "u", 15)
    assert len(store.get_recent("u", limit=10**6)) < 50, "under the cap: the cap queues nothing"
    view = _window(store, "u", budget=2000)
    assert not any("INSTRUCTION" in m["content"] for m in view)
    pending = store.take_pending_summary("u")
    assert any("INSTRUCTION" in str(t) for t in pending), "the left-out turn is queued"
    # The next window does not queue it again, and the cap's prune does not
    # either once it reaches it.
    _window(store, "u", budget=2000)
    assert not any("INSTRUCTION" in str(t) for t in store.take_pending_summary("u"))
    _flood(store, "u", 3)
    again = store.take_pending_summary("u")
    assert not any("INSTRUCTION" in str(t) for t in again)


def test_a_window_that_holds_everything_queues_nothing():
    store = ConversationStore(max_messages_per_user=50)
    store.append("u", "user", "INSTRUCTION: paper only")
    store.append("u", "assistant", "Noted.")
    view = _window(store, "u")
    assert any("INSTRUCTION" in m["content"] for m in view)
    assert store.take_pending_summary("u") == []


def test_the_cap_still_queues_what_the_window_never_saw():
    store = ConversationStore(max_messages_per_user=6)
    for i in range(5):
        store.append("u", "user", f"q{i}")
    store.append("u", "user", "q5")
    store.append("u", "user", "q6")
    pending = store.take_pending_summary("u")
    assert [t.get("content") for t in pending if isinstance(t, dict)] == ["q0"]


def test_every_turn_is_folded_exactly_once_across_windows_and_prunes():
    # Small cap, small window, and turns arriving between windows: each turn
    # that has left the window or the store reaches the queue once.
    store = ConversationStore(max_messages_per_user=8)
    queued: list[str] = []
    said: list[str] = []
    for step in range(30):
        text = f"turn-{step:02d} " + "words " * 30
        said.append(text.split()[0])
        store.append("u", "user" if step % 2 == 0 else "assistant", text)
        if step % 3 == 2:
            _window(store, "u", budget=120)
            queued += [str(t.get("content", "")).split()[0] for t in store.take_pending_summary("u")]
    view = _window(store, "u", budget=120)
    queued += [str(t.get("content", "")).split()[0] for t in store.take_pending_summary("u")]
    in_view = [m["content"] for m in view]
    shown = {w for w in said if any(w in c for c in in_view)}
    assert shown and len(shown) < len(said), "the window left some turns out"
    for w in said:
        if w in shown:
            continue
        assert queued.count(w) == 1, (w, queued.count(w))


def test_a_reply_the_window_drops_from_its_front_is_queued():
    # The window must open on a user turn, so a reply cut at its front is
    # dropped from the view. It is then in neither the view nor the note
    # unless the queue starts from the turn the view actually opens on.
    store = ConversationStore(max_messages_per_user=50)
    store.append("u", "user", "INSTRUCTION " + "paper only " * 300)
    store.append("u", "assistant", "REPLY-X noted")
    store.append("u", "user", "q")
    store.append("u", "assistant", "ans")
    view = _window(store, "u", budget=200)
    assert view == [{"role": "user", "content": "q"}, {"role": "assistant", "content": "ans"}]
    pending = [str(t.get("content", "")) for t in store.take_pending_summary("u")]
    assert any("REPLY-X" in t for t in pending), pending
    assert any("INSTRUCTION" in t for t in pending), pending


def test_a_user_who_left_memory_and_came_back_has_every_prune_folded():
    store = ConversationStore(max_messages_per_user=4, max_users=1)
    store.append("u", "user", "OLD " + "words " * 300)
    store.append("u", "assistant", "a")
    store.append("u", "user", "q")
    store.append("u", "assistant", "ans")
    _window(store, "u", budget=100)
    assert any("OLD" in str(t) for t in store.take_pending_summary("u"))
    # Another user takes the one memory slot, idle "u" is evicted, and comes
    # back with an empty history. What the window had queued is gone with it.
    import time as _t
    for rows in store._conversations.values():
        for r in rows:
            r.timestamp = _t.time() - 10 ** 6
    store.append("v", "user", "hello")
    assert "u" not in store._conversations
    for i in range(5):
        store.append("u", "user", f"N{i}")
    folded = [str(t.get("content", "")) for t in store.take_pending_summary("u")]
    assert "N0" in folded, folded


def test_a_window_built_on_rows_the_store_has_since_pruned_queues_nothing():
    store = ConversationStore(max_messages_per_user=4)
    for i in range(4):
        store.append("u", "user", f"r{i}")
    snapshot = list(store._conversations["u"])
    store.append("u", "user", "r4")
    store.append("u", "user", "r5")
    assert [str(t.get("content")) for t in store.take_pending_summary("u")] == ["r0", "r1"]
    # The window computed on the old snapshot finishes after the prune: its
    # indexes no longer name the stored rows, so it queues nothing.
    store._queue_left_out("u", snapshot, 3)
    assert store.take_pending_summary("u") == []
