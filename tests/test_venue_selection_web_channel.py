"""The bot half of web-set venue selection.

`control_pull`'s own docstring records the failure this file exists to prevent,
in a different control: the website showed a user as PAUSED while every
confirmed trade still went to the exchange. Believing your trades are simulated
when they are real is the worst direction for that to fail in, and believing
your book is spread across two venues when every order goes to one is the same
shape with the same cost.

So the rule for this channel is one line: **the ack reports what the STORE
holds, never what the request asked for.** A refusal that acks the requested
value tells the website a lie it will then display with a tick beside it.
"""
from __future__ import annotations

import pytest

from bot.utils.control_pull import _apply_venue_selection


@pytest.fixture(autouse=True)
def _isolated_store(tmp_path, monkeypatch):
    """A selection store in a temp dir, and a credential store that says
    bitget/bybit are connected. Nothing touches real state."""
    import bot.core.venue_selection as vs
    from bot.core.venue_selection import VenueSelectionStore
    monkeypatch.setattr(vs, "_STORE", VenueSelectionStore(path=str(tmp_path / "sel.json")))

    class _Creds:
        def list_venues(self, uid):
            return ["bitget", "bybit"]

    monkeypatch.setattr("bot.core.exchange_credentials.get_credential_store",
                        lambda: _Creds())
    return vs._STORE


# ── nothing proposed ─────────────────────────────────────────────────────

def test_none_means_no_change_and_does_not_clear(_isolated_store):
    """`None` is "the user touched some other control". Reading it as "clear my
    venues" would drop somebody's selection every time they moved their margin
    cap — and the only symptom would be their book quietly concentrating."""
    _isolated_store.set_selection("7", ["bitget", "bybit"])
    assert _apply_venue_selection("7", None) == "bitget,bybit"
    assert _isolated_store.raw_selection("7") == ["bitget", "bybit"]


def test_empty_string_DOES_clear():
    """The other half of the same distinction. '' is a deliberate "turn
    multi-venue off", and it must actually do it."""
    from bot.core.venue_selection import get_venue_selection_store
    st = get_venue_selection_store()
    st.set_selection("7", ["bitget"])
    assert _apply_venue_selection("7", "") == ""
    assert st.raw_selection("7") == []


# ── the ack reports the store, not the request ───────────────────────────

def test_a_successful_apply_acks_what_was_stored():
    from bot.core.venue_selection import get_venue_selection_store
    assert _apply_venue_selection("7", "bitget,bybit") == "bitget,bybit"
    assert get_venue_selection_store().raw_selection("7") == ["bitget", "bybit"]


def test_a_REFUSED_selection_acks_the_unchanged_state_not_the_request():
    """THE test for this channel. `okx` is not connected, so the store refuses.
    Acking 'bitget,okx' anyway would put okx on the website with a tick beside
    it — the user believes they are trading a venue the bot will never route
    to, and nothing anywhere says otherwise."""
    from bot.core.venue_selection import get_venue_selection_store
    st = get_venue_selection_store()
    st.set_selection("7", ["bitget"])

    acked = _apply_venue_selection("7", "bitget,okx")
    assert acked == "bitget", (
        f"the ack reported {acked!r} — the website would show a venue the bot "
        "refused to select")
    assert st.raw_selection("7") == ["bitget"], "a refused write was applied"


def test_a_refusal_does_not_partially_apply():
    """Storing the recognised half would answer a request nobody made: the user
    asked for A and B, and silently keeping A leaves them believing B is live."""
    from bot.core.venue_selection import get_venue_selection_store
    st = get_venue_selection_store()
    assert _apply_venue_selection("7", "okx,gate") == ""
    assert st.raw_selection("7") == []


def test_an_unreadable_store_does_not_crash_the_whole_control_pull():
    """One bad row must not stop the other users' controls from applying. The
    caller wraps each row, so this asserts the helper raises rather than
    silently returning a plausible-looking string."""
    import bot.core.venue_selection as vs

    class _Broken:
        def set_selection(self, *a, **k):
            raise RuntimeError("disk gone")

        def raw_selection(self, uid):
            raise RuntimeError("disk gone")

    vs._STORE = _Broken()
    with pytest.raises(RuntimeError):
        _apply_venue_selection("7", "bitget")


# ── it is wired into the channel that carries it ─────────────────────────

def test_the_pull_applies_venues_and_acks_them():
    """#58 for a sync channel: a helper the puller never calls moves nothing,
    and the website would show an empty selection for ever."""
    import inspect

    from bot.utils import control_pull
    src = inspect.getsource(control_pull.process_pending_controls)
    assert '_apply_venue_selection(tg, r.get("venues"))' in src, (
        "the pulled venues field is never applied")
    assert '"venues": applied_venues' in src, (
        "the ack does not carry the venues, so the website can never learn "
        "what the bot actually holds")
