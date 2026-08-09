"""The Paper Arena's door, and the four pairs it must not conflate.

The Arena is the zero-friction on-ramp — same virtual stake for everyone, no
exchange keys. Eighteen endpoints on the web, and no door from the chat where
the members are. This is the door.

Unlike /leaderboard, the data is NOT on this process's disk: the Arena lives in
the web app's database, so every read is a network hop, and a network hop has a
failure mode the leaderboard never had. Which makes these the four pairs the
card exists to keep apart — each is a real reading on one side and an absence on
the other, and each renders identically under the shapes CLAUDE.md lists:

    unreadable board       vs   a board with nobody on it
    no season configured   vs   the site could not be reached
    a season not started   vs   a season running with no closes yet
    a close of exactly 0%  vs   a close whose return could not be computed

RED HERRING, in TestZeroIsAReading: a row returning exactly ``0.0`` sits beside
one whose ``pct`` is ``None``. The first is a member who closed flat — real,
measured, and theirs. `float(x or 0)` renders the second as the first, and
`>= 0` marks the unreadable one a win.

SECOND RED HERRING, in TestVirtualIsAClaim: a payload with the standings intact
but no ``virtual: true``. Everything renders, nothing looks broken, and the four
words that stop "+12.4%" being read as real money are the only thing missing.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

import pytest

from bot.formatters.arena_cards import STANDINGS_ROWS, render_arena

NOW = datetime(2026, 8, 9, 12, 0, tzinfo=timezone.utc)

SEASON = {
    "virtual": True,
    "season": {"name": "Opening Bell", "status": "live",
               "starts_at": "2026-08-01T00:00:00.000Z",
               "ends_at": "2026-08-12T16:00:00.000Z"},
    "rows": [
        {"rank": 1, "handle": "kestrel", "return_pct": 12.44, "sealed": 9, "closes": 9},
        {"rank": 2, "handle": "nightjar", "return_pct": 8.1, "sealed": 1, "closes": 4},
        {"rank": 3, "handle": "you", "return_pct": 0.0, "sealed": 3, "closes": 3},
    ],
}
TAPE = {
    "virtual": True, "traders": 41, "trades_24h": 128,
    "rows": [
        {"handle": "kestrel", "symbol": "BTC/USDT", "direction": "long",
         "pct": 2.1, "reason": "tp", "key": "k1"},
        {"handle": "harrier", "symbol": "SOL/USDT", "direction": "short",
         "pct": -0.8, "reason": "sl", "key": None},
    ],
}


def plain(card: str) -> str:
    return re.sub(r"<[^>]+>", "", card)


class TestUnreadableIsNotEmpty:
    def test_both_halves_dead_says_it_could_not_be_read(self):
        card = plain(render_arena(None, None))
        assert "could not be read" in card
        assert "not an empty floor" in card

    def test_it_publishes_no_numbers_at_all(self):
        """The failure card must not carry a count, a percent or a rank —
        every one of them would be about a floor this process never reached."""
        card = plain(render_arena(None, None))
        assert not re.search(r"\d+\s*(traders|closes)|%", card)

    def test_a_dead_tape_does_not_blank_a_live_season(self):
        """The omit strategy: a composite view where one dead source must not
        take the others with it."""
        card = plain(render_arena(SEASON, None, now=NOW))
        assert "kestrel" in card and "12.44%" in card
        assert "tape could not be read" in card

    def test_a_dead_season_does_not_blank_a_live_tape(self):
        card = plain(render_arena(None, TAPE))
        assert "harrier" in card
        assert "could not be read" in card

    def test_a_dead_tape_never_prints_a_zero_pulse(self):
        """`0 traders` under an unreachable site is a measurement nobody made."""
        assert "0 traders" not in plain(render_arena(SEASON, None, now=NOW))


class TestNoSeasonIsNotAnOutage:
    def test_a_readable_null_season_says_none_is_running(self):
        card = plain(render_arena({"virtual": True, "season": None}, TAPE))
        assert "No season is running" in card
        assert "could not be read" not in card

    def test_which_is_different_from_an_unreadable_one(self):
        readable = plain(render_arena({"virtual": True, "season": None}, TAPE))
        dead = plain(render_arena(None, TAPE))
        assert readable != dead


class TestNotStartedIsNotNobodyScored:
    UPCOMING = {"virtual": True,
                "season": {"name": "Autumn Cup", "status": "upcoming",
                           "starts_at": "2026-09-01T00:00:00.000Z",
                           "ends_at": "2026-09-30T00:00:00.000Z"}}

    def test_an_upcoming_season_says_it_has_not_started(self):
        card = plain(render_arena(self.UPCOMING, TAPE, now=NOW))
        assert "Not started" in card

    def test_it_does_not_claim_an_empty_board(self):
        card = plain(render_arena(self.UPCOMING, TAPE, now=NOW))
        assert "No closes inside the window" not in card

    def test_a_live_season_with_no_closes_says_that_instead(self):
        live = {"virtual": True, "season": {"name": "Opening Bell", "status": "live",
                "ends_at": "2026-08-12T16:00:00.000Z"}, "rows": []}
        card = plain(render_arena(live, TAPE, now=NOW))
        assert "No closes inside the window" in card
        assert "Not started" not in card

    def test_an_upcoming_season_counts_to_its_OPENING(self):
        """Counting to ends_at put '51d 12h left' beside a season that had not
        opened — which reads as time left to compete in a closed competition."""
        card = plain(render_arena(self.UPCOMING, None, now=NOW))
        assert "starts in 22d 12h" in card
        assert "left" not in card

    def test_a_live_season_counts_to_its_CLOSE(self):
        card = plain(render_arena(SEASON, None, now=NOW))
        assert "3d 4h left" in card

    def test_an_unparseable_clock_is_omitted_not_guessed(self):
        broken = {"virtual": True, "season": {"name": "X", "status": "live",
                  "ends_at": "not-a-date"}, "rows": []}
        card = plain(render_arena(broken, None, now=NOW))
        assert "left" not in card and "0h" not in card

    def test_an_elapsed_clock_does_not_render_negative_time(self):
        past = {"virtual": True, "season": {"name": "X", "status": "live",
                "ends_at": "2020-01-01T00:00:00.000Z"}, "rows": []}
        assert "-" not in plain(render_arena(past, None, now=NOW)).split("\n")[2]


class TestZeroIsAReading:
    def _tape(self, pct):
        return {"virtual": True, "rows": [{"handle": "a", "symbol": "BTC/USDT",
                "direction": "long", "pct": pct, "reason": "tp"}]}

    def test_a_flat_close_prints_as_zero_percent(self):
        assert "+0.00%" in plain(render_arena(None, self._tape(0.0)))

    def test_an_uncomputable_close_prints_as_unknown(self):
        card = plain(render_arena(None, self._tape(None)))
        assert "—" in card and "0.00%" not in card

    def test_the_two_differ(self):
        assert plain(render_arena(None, self._tape(0.0))) != \
               plain(render_arena(None, self._tape(None)))

    def test_an_unreadable_close_is_not_marked_a_win(self):
        """`>= 0` would mark it ▲ — the shape this repo has been bitten by."""
        assert "▲" not in plain(render_arena(None, self._tape(None)))

    def test_a_flat_close_is_marked_flat_not_won(self):
        card = plain(render_arena(None, self._tape(0.0)))
        assert "=" in card and "▲" not in card

    def test_a_losing_close_is_marked_down(self):
        assert "▼" in plain(render_arena(None, self._tape(-1.5)))


class TestVirtualIsAClaim:
    def test_it_is_stated_when_the_source_asserts_it(self):
        assert "virtual funds" in plain(render_arena(SEASON, TAPE, now=NOW))

    def test_it_is_not_promised_when_the_source_stops_saying_so(self):
        """The second red herring: everything else renders, and the four words
        that stop '+12.44%' reading as real money are what went missing."""
        card = plain(render_arena({"season": None}, {"rows": [], "traders": 2}))
        assert "virtual funds" not in card

    def test_and_the_absence_is_flagged_rather_than_silent(self):
        card = plain(render_arena({"season": None}, {"rows": [], "traders": 2}))
        assert "did not confirm" in card

    def test_one_asserting_half_is_enough(self):
        assert "virtual funds" in plain(render_arena(SEASON, None, now=NOW))


class TestTheProofColumnAndCounts:
    def test_sealed_counts_render_as_a_fraction(self):
        card = plain(render_arena(SEASON, None, now=NOW))
        assert "9/9" in card and "1/4" in card

    def test_absent_seal_counts_do_not_become_zero(self):
        """`0/0` reads as 'none of this is provable'; it means 'not told'."""
        s = {"virtual": True, "season": SEASON["season"],
             "rows": [{"rank": 1, "handle": "a", "return_pct": 1.0}]}
        card = plain(render_arena(s, None, now=NOW))
        assert "0/0" not in card and "—" in card

    def test_a_missing_pulse_count_is_omitted_not_zeroed(self):
        card = plain(render_arena(None, {"virtual": True, "rows": [], "traders": 7}))
        assert "7 traders" in card
        assert "closes in 24h" not in card

    def test_a_real_zero_pulse_is_shown(self):
        card = plain(render_arena(
            None, {"virtual": True, "rows": [], "traders": 0, "trades_24h": 0}))
        assert "0 traders" in card and "0 closes in 24h" in card

    def test_a_boolean_is_not_mistaken_for_a_count(self):
        card = plain(render_arena(
            None, {"virtual": True, "rows": [], "traders": True}))
        assert "True traders" not in card and "1 traders" not in card


class TestAnUnknownReasonIsNotInvented:
    def _tape(self, reason):
        return {"virtual": True, "rows": [{"handle": "a", "symbol": "BTC/USDT",
                "direction": "long", "pct": 1.0, "reason": reason}]}

    @pytest.mark.parametrize("raw,shown", [("tp", "TP"), ("sl", "SL"),
                                           ("liquidated", "LIQ"), ("manual", "manual")])
    def test_the_known_vocabulary_renders(self, raw, shown):
        assert shown in plain(render_arena(None, self._tape(raw)))

    def test_an_unheard_of_reason_renders_as_unknown(self):
        card = plain(render_arena(None, self._tape("teleported")))
        assert "teleported" not in card

    def test_it_is_not_silently_relabelled_a_manual_close(self):
        assert "manual" not in plain(render_arena(None, self._tape("teleported")))


class TestNoDollarFigureReachesTheArena:
    def test_the_card_is_money_free(self):
        assert "$" not in render_arena(SEASON, TAPE, now=NOW)

    def test_a_leak_withholds_the_card_rather_than_publishing_it(self):
        """A season name is operator-typed free text and this card is one tap
        from a group chat."""
        s = {"virtual": True, "season": {"name": "The $1,000 Cup", "status": "live",
             "ends_at": "2026-08-12T16:00:00.000Z"}, "rows": []}
        card = render_arena(s, None, now=NOW)
        assert "$" not in card
        assert "withheld" in card

    def test_it_shares_the_other_public_surfaces_guard(self):
        from bot.formatters import arena_cards, share_invite
        assert arena_cards.assert_no_money is share_invite.assert_no_money


class TestTheDisplayCutIsStated:
    def test_extra_ranked_rows_are_counted(self):
        rows = [{"rank": i, "handle": f"h{i}", "return_pct": 1.0,
                 "sealed": 1, "closes": 1} for i in range(1, 15)]
        card = plain(render_arena({**SEASON, "rows": rows}, None, now=NOW))
        assert f"{14 - STANDINGS_ROWS} more ranked below" in card

    def test_a_viewer_outside_the_standings_is_told_so(self):
        card = plain(render_arena(SEASON, TAPE, "ghost", now=NOW))
        assert "no closes in this window yet" in card.lower()

    def test_a_ranked_viewer_is_marked_not_scolded(self):
        card = plain(render_arena(SEASON, TAPE, "you", now=NOW))
        assert "◀" in card
        assert "no closes in this window" not in card.lower()


class TestTheCommandIsRegisteredAndGuarded:
    def test_arena_is_registered(self):
        import pathlib
        src = pathlib.Path("bot/skills/telegram_handler.py").read_text(encoding="utf-8")
        assert '("arena", self._cmd_arena)' in src

    def test_it_carries_a_permission_a_role_actually_holds(self):
        import ast
        import pathlib
        from bot.utils.user_store import ROLE_PERMISSIONS
        src = pathlib.Path("bot/skills/telegram_handler.py").read_text(encoding="utf-8")
        decs = next(([ast.unparse(d) for d in n.decorator_list]
                     for n in ast.walk(ast.parse(src))
                     if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                     and n.name == "_cmd_arena"), [])
        assert any("guard" in d for d in decs), f"ungated: {decs}"
        perm = re.search(r"guard\('([a-z_]+)'\)", " ".join(decs)).group(1)
        holders = [r for r, p in ROLE_PERMISSIONS.items() if perm in p]
        assert "trader" in holders and "viewer" in holders, (
            f"{perm!r} held by {holders} — invisible to the roles the "
            "catalogue advertises it to")

    def test_the_blocking_fetch_does_not_stall_the_event_loop(self):
        """arena_pull uses urllib, which blocks. Called directly in an async
        handler it freezes every other command in the process for up to the
        full timeout — on the one command whose whole risk is a slow site."""
        import pathlib
        src = pathlib.Path("bot/skills/telegram_handler.py").read_text(encoding="utf-8")
        body = src[src.index("async def _cmd_arena"):src.index("def _viewer_board_handle")]
        assert "to_thread" in body, "blocking network read on the event loop"


class TestTheFetcherReportsFailureAsFailure:
    def test_a_non_dict_body_is_unreadable_not_empty(self, monkeypatch):
        from bot.utils import arena_pull

        class _Resp:
            status = 200
            def read(self): return b'["not", "an", "object"]'
            def __enter__(self): return self
            def __exit__(self, *a): return False
        monkeypatch.setattr(arena_pull.urllib.request, "urlopen",
                            lambda *a, **k: _Resp())
        assert arena_pull.fetch_season() is None

    def test_a_transport_error_is_none(self, monkeypatch):
        from bot.utils import arena_pull

        def _boom(*_a, **_k):
            raise OSError("connection refused")
        monkeypatch.setattr(arena_pull.urllib.request, "urlopen", _boom)
        assert arena_pull.fetch_tape() is None

    def test_a_readable_body_comes_back(self, monkeypatch):
        from bot.utils import arena_pull

        class _Resp:
            status = 200
            def read(self): return b'{"season": null, "virtual": true}'
            def __enter__(self): return self
            def __exit__(self, *a): return False
        monkeypatch.setattr(arena_pull.urllib.request, "urlopen",
                            lambda *a, **k: _Resp())
        assert arena_pull.fetch_season() == {"season": None, "virtual": True}

    def test_the_public_read_carries_no_shared_secret(self, monkeypatch):
        """These endpoints are public and do not check it. Sending it anyway
        would put the sync credential on a path that never needs it."""
        from bot.utils import arena_pull
        seen = {}

        def _capture(req, *_a, **_k):
            seen["headers"] = dict(getattr(req, "headers", {}) or {})
            raise OSError("stop here")
        monkeypatch.setattr(arena_pull.urllib.request, "urlopen", _capture)
        arena_pull.fetch_tape()
        joined = " ".join(seen["headers"]).lower()
        assert "secret" not in joined and "authorization" not in joined

    def test_virtual_is_only_true_when_asserted(self):
        from bot.utils.arena_pull import is_virtual
        assert is_virtual({"virtual": True}) is True
        for lie in ({}, {"virtual": False}, {"virtual": "true"}, {"virtual": 1}, None):
            assert is_virtual(lie) is False
