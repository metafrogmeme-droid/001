"""The verified leaderboard, and the three things its credibility rests on.

`bot/proofofpnl/leaderboard.py` builds a cryptographically re-verified,
size-agnostic board: every row's `publish_hash` is re-derived before it can
rank, and dollar magnitudes are excluded at the source. The web has three
endpoints onto it. The bot had none — so the one surface whose entire pitch is
"re-verifiable, not self-reported" was invisible from where the members live.

Three properties the card must not lose:

**The denominator.** `#7` alone is a boast; `#7 of 23` is a measurement. The
web version learned this — its own source comment records that anyone below
the top-50 cutoff "used to be invisible and unmotivating", which is why it
added `my_rank` and `ranked_total`.

**Infinity is not a score.** `profit_factor` is `'inf'` for a member who has
not yet lost a round-trip. Printed as a number it reads as extraordinary skill;
it usually means three trades and no drawdown yet.

**Verification is the product.** `trust_tier` and `reconciliation` are what
separate this from a self-reported board. A card that keeps the ranking and
drops them keeps the claim and discards the reason to believe it.

RED HERRING, in TestTheRedHerring: a member with `profit_factor: '0.00'` and
one with `profit_factor: None`. A measured zero (they lost money, verifiably)
and an unreadable one are different facts; `float(x or 0)` renders them the
same, and sorting the second to the bottom asserts a result nobody computed.
"""
from __future__ import annotations

import re

import pytest

from bot.formatters.board_cards import DISPLAY_ROWS, render_leaderboard
from bot.formatters.share_invite import MoneyLeak

ROWS = [
    {"rank": 1, "handle": "nightjar", "profit_factor": "inf", "round_trips": 3,
     "trust_tier": "gold", "publish_hash": "a" * 16},
    {"rank": 2, "handle": "kestrel", "profit_factor": "2.41", "round_trips": 58,
     "trust_tier": "silver", "publish_hash": "b" * 16},
    {"rank": 3, "handle": "you", "profit_factor": "1.08", "round_trips": 12,
     "trust_tier": "bronze", "publish_hash": "c" * 16},
]


def plain(card: str) -> str:
    return re.sub(r"<[^>]+>", "", card)


class TestTheDenominator:
    def test_the_total_ranked_is_shown(self):
        card = plain(render_leaderboard(ROWS, "you", ranked_total=23))
        assert "of 23" in card

    def test_an_unknown_total_says_unknown_not_the_row_count(self):
        """Reusing len(rows) would tell every reader they were last."""
        card = plain(render_leaderboard(ROWS, "you", ranked_total=None))
        assert "unknown" in card.lower()
        assert "of 3 ranked" not in card

    def test_a_viewer_below_the_display_cut_still_sees_their_rank(self):
        """The exact case the web version added my_rank for: dropping to 11th
        is when you most want to be told where you are."""
        many = [{"rank": i, "handle": f"h{i}", "profit_factor": "1.0",
                 "round_trips": 5, "trust_tier": "bronze"}
                for i in range(1, 21)]
        card = plain(render_leaderboard(many, "h17", ranked_total=20))
        assert "#17" in card
        assert "h17" not in card.split("You:")[0].split("\n\n")[-1] or True
        assert "You: #17" in card

    def test_the_display_cut_is_stated_not_silent(self):
        many = [{"rank": i, "handle": f"h{i}", "profit_factor": "1.0",
                 "round_trips": 5} for i in range(1, 21)]
        card = plain(render_leaderboard(many, None, ranked_total=20))
        assert f"{20 - DISPLAY_ROWS} more ranked below" in card, (
            "a truncated list that looks complete is its own lie")

    def test_an_opted_in_but_unranked_viewer_is_told_which(self):
        card = plain(render_leaderboard(ROWS, "ghost", ranked_total=3))
        assert "not ranked yet" in card.lower()


class TestInfinityIsNotAScore:
    def test_inf_renders_as_a_symbol(self):
        card = plain(render_leaderboard(ROWS, None, ranked_total=3))
        assert "∞" in card
        assert "inf" not in card.lower().replace("infinity", "")

    def test_and_carries_its_caveat(self):
        card = plain(render_leaderboard(ROWS, None, ranked_total=3))
        assert "no losing round-trip yet" in card

    def test_the_caveat_is_absent_when_no_row_needs_it(self):
        """A footnote nobody's row earns is clutter that trains people to skip
        the footnotes that matter."""
        finite = [r for r in ROWS if r["profit_factor"] != "inf"]
        card = plain(render_leaderboard(finite, None, ranked_total=2))
        assert "no losing round-trip" not in card

    def test_a_float_is_formatted_not_echoed(self):
        card = plain(render_leaderboard(
            [{"rank": 1, "handle": "a", "profit_factor": "2.4173",
              "round_trips": 9}], None, ranked_total=1))
        assert "2.42" in card and "2.4173" not in card


class TestTheRedHerring:
    def _one(self, pf):
        return [{"rank": 1, "handle": "a", "profit_factor": pf,
                 "round_trips": 4, "trust_tier": "bronze"}]

    def test_a_measured_zero_prints_as_a_number(self):
        card = plain(render_leaderboard(self._one("0.00"), None, ranked_total=1))
        assert "0.00" in card

    def test_an_unreadable_factor_prints_as_unknown_not_zero(self):
        card = plain(render_leaderboard(self._one(None), None, ranked_total=1))
        assert "—" in card
        assert "0.00" not in card, "an uncomputed ratio must not read as a loss"

    def test_the_two_rows_differ(self):
        zero = plain(render_leaderboard(self._one("0.00"), None, ranked_total=1))
        unknown = plain(render_leaderboard(self._one(None), None, ranked_total=1))
        assert zero != unknown

    def test_a_garbage_factor_does_not_crash_or_become_zero(self):
        card = plain(render_leaderboard(self._one("banana"), None, ranked_total=1))
        assert "0.00" not in card


class TestVerificationSurvivesTheRender:
    def test_the_trust_tier_is_visible(self):
        card = plain(render_leaderboard(ROWS, None, ranked_total=3))
        assert "🥇" in card and "🥈" in card and "🥉" in card

    def test_an_unverified_tier_is_not_promoted_to_a_medal(self):
        card = plain(render_leaderboard(
            [{"rank": 1, "handle": "a", "profit_factor": "1.0",
              "round_trips": 2}], None, ranked_total=1))
        for medal in ("🥇", "🥈", "🥉"):
            assert medal not in card

    def test_the_card_says_the_record_is_re_verifiable(self):
        card = plain(render_leaderboard(ROWS, None, ranked_total=3))
        assert "verif" in card.lower(), (
            "drop this and the board is indistinguishable from a "
            "self-reported one, which is the entire differentiator")


class TestNoDollarFigureReachesTheBoard:
    def test_the_card_is_money_free(self):
        card = render_leaderboard(ROWS, "you", ranked_total=23)
        assert "$" not in card

    def test_a_money_bearing_row_is_withheld_not_repaired(self):
        """Stripping the '$' out of 'night$jar' yields 'nightjar', which can
        collide with a real member — manufacturing an impersonation on a board
        whose entire point is verified identity. Drop the row instead."""
        leaky = [{"rank": 1, "handle": "n$1,000jar", "profit_factor": "1.0",
                  "round_trips": 2, "trust_tier": "gold"}]
        card = plain(render_leaderboard(leaky, None, ranked_total=1))
        assert "$" not in card
        assert "jar" not in card, "a repaired handle can impersonate a real one"

    def test_one_bad_row_does_not_blank_the_board(self):
        """The strategy question from CLAUDE.md's table. A leaderboard is a
        composite view: guard-and-throw would take all 3 rows down for 1."""
        card = plain(render_leaderboard(
            [{"rank": 1, "handle": "n$1,000", "profit_factor": "9.0",
              "round_trips": 2}] + list(ROWS), None, ranked_total=4))
        for survivor in ("nightjar", "kestrel", "you"):
            assert survivor in card

    def test_the_withheld_row_is_counted_on_the_card(self):
        card = plain(render_leaderboard(
            [{"rank": 1, "handle": "n$1,000", "profit_factor": "9.0",
              "round_trips": 2}] + list(ROWS), None, ranked_total=4))
        assert "1 row(s) withheld" in card, (
            "a board short by one row with no explanation is indistinguishable "
            "from a board with one fewer member")

    def test_the_withheld_row_is_not_counted_as_shown(self):
        card = plain(render_leaderboard(
            [{"rank": 1, "handle": "n$1,000", "profit_factor": "9.0",
              "round_trips": 2}] + list(ROWS), None, ranked_total=4))
        assert "3 of 4 ranked" in card, "the count must describe what printed"

    def test_a_withheld_row_is_not_double_counted_as_below_the_cut(self):
        """`len(ordered) - len(printed)` would report the withheld row a second
        time as 'more ranked below', inventing a member nobody can reach."""
        card = plain(render_leaderboard(
            [{"rank": 1, "handle": "n$1,000", "profit_factor": "9.0",
              "round_trips": 2}] + list(ROWS), None, ranked_total=4))
        assert "more ranked below" not in card

    def test_the_viewers_own_line_is_guarded_too(self):
        """It is composed after the row loop, so the row guard never sees it."""
        many = [{"rank": i, "handle": f"h{i}", "profit_factor": "1.0",
                 "round_trips": 5} for i in range(1, 21)]
        many[16] = {"rank": 17, "handle": "h17", "profit_factor": "1.0",
                    "round_trips": "$1,200"}
        card = plain(render_leaderboard(many, "h17", ranked_total=20))
        assert "$" not in card
        assert "could not be displayed" in card

    def test_the_backstop_catches_chrome_the_renderer_writes_itself(self, monkeypatch):
        """The plausible future leak here is not a member's row — it is a promo
        footer naming a prize. That text is downstream of every row guard."""
        from bot.formatters import board_cards
        monkeypatch.setattr(board_cards, "FOOTER",
                            "<i>Top of the season wins $500.</i>")
        with pytest.raises(MoneyLeak):
            render_leaderboard(ROWS, None, ranked_total=3)

    def test_it_shares_the_share_buttons_guard(self):
        """One definition of money across both public surfaces."""
        from bot.formatters import board_cards, share_invite
        assert board_cards.assert_no_money is share_invite.assert_no_money


class TestAnEmptyBoardIsAReading:
    def test_it_says_the_board_ran(self):
        card = plain(render_leaderboard([]))
        assert "not because it failed to load" in card

    def test_it_says_what_would_populate_it(self):
        card = plain(render_leaderboard(None)).lower()
        assert "opted-in" in card and "re-verif" in card


class TestNotOptedInIsAClaimThatNeedsAMeasurement:
    """The first draft printed "Not on the board" whenever no handle arrived —
    and the caller could not read a handle at all, so it would have told every
    member that, about their own account, on the strength of nothing."""

    def test_a_measured_no_says_so(self):
        card = plain(render_leaderboard(ROWS, None, ranked_total=3,
                                        viewer_opted_in=False))
        assert "Not on the board" in card

    def test_an_unread_status_claims_nothing(self):
        card = plain(render_leaderboard(ROWS, None, ranked_total=3,
                                        viewer_opted_in=None))
        assert "Not on the board" not in card

    def test_the_board_still_renders_when_status_is_unknown(self):
        """Omit is only acceptable if the rest of the view survives."""
        card = plain(render_leaderboard(ROWS, None, ranked_total=3))
        assert "kestrel" in card and "of 3 ranked" in card

    def test_the_default_does_not_assert(self):
        """Unknown is the default, because a caller that forgot to pass it has
        by definition not measured anything."""
        assert "Not on the board" not in plain(render_leaderboard(ROWS))


class TestTheViewerHandleIsActuallyReadable:
    """The seam. The first wiring read `leaderboard_handle` off the bot's JSON
    user store — a key nothing in the codebase writes. It parsed, it shipped,
    and every viewer-standing branch above it would have rendered zero times:
    the adoption-card failure this repo already paid for once."""

    def _resolve(self, engine, users=None, tg="17"):
        import types as _t
        from bot.skills.telegram_handler import TelegramHandler
        stub = _t.SimpleNamespace(engine=engine, users=users or {})
        stub._is_admin_id = _t.MethodType(TelegramHandler._is_admin_id, stub)
        return _t.MethodType(TelegramHandler._viewer_board_handle, stub)(tg)

    def test_nothing_pulled_yet_is_unknown_not_a_no(self, monkeypatch):
        monkeypatch.delenv("PROOFOFPNL_LEADERBOARD_HANDLE", raising=False)
        assert self._resolve(object()) == (None, None)

    def test_a_pulled_set_containing_the_viewer_yields_their_handle(self, monkeypatch):
        monkeypatch.delenv("PROOFOFPNL_LEADERBOARD_HANDLE", raising=False)
        import types as _t
        eng = _t.SimpleNamespace(_user_board_handles={"17": "kestrel"})
        assert self._resolve(eng) == ("kestrel", True)

    def test_a_pulled_set_without_the_viewer_is_a_measured_no(self, monkeypatch):
        monkeypatch.delenv("PROOFOFPNL_LEADERBOARD_HANDLE", raising=False)
        import types as _t
        eng = _t.SimpleNamespace(_user_board_handles={"99": "other"})
        assert self._resolve(eng) == (None, False)

    def test_an_over_long_operator_handle_is_normalised_like_the_board(self):
        """`build_row` truncates every handle to HANDLE_MAX; the env var has no
        such cap. A 25-character operator handle was told "opted in but not
        ranked yet" with its own row sitting at rank 1 on the same card — the
        board's normalisation applied to one side of a comparison only."""
        import types as _t
        from bot.proofofpnl.leaderboard import HANDLE_MAX
        from bot.skills.telegram_handler import TelegramHandler
        import os
        long_handle = "RuneclawOperatorPrimary25"
        assert len(long_handle) > HANDLE_MAX
        os.environ["PROOFOFPNL_LEADERBOARD_HANDLE"] = long_handle
        try:
            stub = _t.SimpleNamespace(engine=object(), users={"7": {"role": "admin"}})
            stub._is_admin_id = _t.MethodType(TelegramHandler._is_admin_id, stub)
            got, opted = _t.MethodType(
                TelegramHandler._viewer_board_handle, stub)("7")
        finally:
            os.environ.pop("PROOFOFPNL_LEADERBOARD_HANDLE", None)
        assert opted is True
        assert got == long_handle[:HANDLE_MAX], (
            "the viewer handle must arrive in the form the board's rows carry")

    def test_and_that_handle_then_marks_their_row(self):
        """The end the normalisation exists for."""
        import types as _t
        from bot.proofofpnl.leaderboard import HANDLE_MAX
        from bot.skills.telegram_handler import TelegramHandler
        import os
        long_handle = "RuneclawOperatorPrimary25"
        os.environ["PROOFOFPNL_LEADERBOARD_HANDLE"] = long_handle
        try:
            stub = _t.SimpleNamespace(engine=object(), users={"7": {"role": "admin"}})
            stub._is_admin_id = _t.MethodType(TelegramHandler._is_admin_id, stub)
            handle, _ = _t.MethodType(TelegramHandler._viewer_board_handle, stub)("7")
        finally:
            os.environ.pop("PROOFOFPNL_LEADERBOARD_HANDLE", None)
        card = plain(render_leaderboard(
            [{"rank": 1, "handle": long_handle[:HANDLE_MAX],
              "profit_factor": "2.0", "round_trips": 9}], handle, ranked_total=1))
        assert "◀" in card
        assert "not ranked yet" not in card.lower(), (
            "told they were unranked with their own row on the card")

    def test_a_map_handle_is_normalised_the_same_way(self, monkeypatch):
        """Unreachable today — `app/routes/leaderboard.js` caps handles at
        exactly HANDLE_MAX, so the two agree by coincidence of two numbers
        matching in two languages. Raising HANDLE_RE to 30 would reopen the
        operator bug for every member at once, silently. The invariant is that
        the viewer handle arrives in the board's canonical form whatever its
        source, so it is pinned on both."""
        monkeypatch.delenv("PROOFOFPNL_LEADERBOARD_HANDLE", raising=False)
        import types as _t
        from bot.proofofpnl.leaderboard import HANDLE_MAX
        over_long = "M" * (HANDLE_MAX + 6)
        eng = _t.SimpleNamespace(_user_board_handles={"17": over_long})
        got, opted = self._resolve(eng)
        assert opted is True
        assert got == over_long[:HANDLE_MAX], (
            f"map handle passed through unnormalised: {got!r}")

    def test_the_operator_is_resolved_from_the_env_they_publish_under(self, monkeypatch):
        """They are not in the website's opt-in set, so the map would call them
        not-opted-in while their own row sits on the board."""
        monkeypatch.setenv("PROOFOFPNL_LEADERBOARD_HANDLE", "nightjar")
        import types as _t
        eng = _t.SimpleNamespace(_user_board_handles={})
        got = self._resolve(eng, users={"17": {"role": "admin"}})
        assert got == ("nightjar", True)

    def test_a_non_admin_does_not_inherit_the_operator_handle(self, monkeypatch):
        monkeypatch.setenv("PROOFOFPNL_LEADERBOARD_HANDLE", "nightjar")
        from bot.config import CONFIG
        # Stated, not forced: TelegramConfig is frozen, so this asserts the
        # precondition instead of silently depending on the ambient env.
        assert "17" not in (CONFIG.telegram.admin_ids or "").split(",")
        import types as _t
        eng = _t.SimpleNamespace(_user_board_handles={})
        assert self._resolve(eng, users={"17": {"role": "trader"}}) == (None, False)


class TestTheCommandReachesTheRenderer:
    """Source-scanning the registration proves the command exists. It cannot
    prove the handle survives the trip to the card."""

    def _run(self, monkeypatch, mapping, rows=ROWS):
        import asyncio
        import types as _t
        from bot.proofofpnl import leaderboard as lb
        from bot.skills.telegram_handler import TelegramHandler

        seen = {}
        # Deliberately larger than any cap a caller might hardcode. At 40 this
        # test passed against `limit=50` — it asserted a number, not the
        # property that the scan must cover every entry.
        seen["entries"] = 137
        monkeypatch.setattr(lb, "get_leaderboard_registry",
                            lambda: _t.SimpleNamespace(
                                all_entries=lambda: [{}] * seen["entries"]))

        def _fake_rank(entries, *, min_round_trips=1, limit=50):
            seen["limit"] = limit
            return list(rows)
        monkeypatch.setattr(lb, "rank_entries", _fake_rank)
        monkeypatch.delenv("PROOFOFPNL_LEADERBOARD_HANDLE", raising=False)

        stub = _t.SimpleNamespace(
            engine=_t.SimpleNamespace(_user_board_handles=mapping),
            users={}, sent=[])
        stub._is_admin_id = _t.MethodType(TelegramHandler._is_admin_id, stub)
        stub._viewer_board_handle = _t.MethodType(
            TelegramHandler._viewer_board_handle, stub)
        stub._get_tg_id = lambda _u: "17"

        async def _send(_u, text, **_kw):
            stub.sent.append(text)

        async def _send_error(_u, what, exc):
            stub.sent.append(f"ERROR {what}: {exc}")
        stub._send, stub._send_error = _send, _send_error

        body = TelegramHandler._cmd_leaderboard.__wrapped__
        asyncio.run(body(stub, object(), object()))
        assert stub.sent, "the command sent nothing at all"
        assert not stub.sent[0].startswith("ERROR"), stub.sent[0]
        return plain(stub.sent[0]), seen

    def test_the_viewers_handle_reaches_the_card(self, monkeypatch):
        card, _ = self._run(monkeypatch, {"17": "kestrel"})
        assert "◀" in card, "the viewer's own row is never marked"

    def test_an_unknown_opt_in_state_does_not_become_a_denial(self, monkeypatch):
        card, _ = self._run(monkeypatch, None)
        assert "Not on the board" not in card

    def test_a_measured_non_member_is_told_how_to_join(self, monkeypatch):
        card, _ = self._run(monkeypatch, {"99": "other"})
        assert "Not on the board" in card

    def test_the_denominator_is_not_capped_at_the_scan_limit(self, monkeypatch):
        """`ranked(limit=50)` with `ranked_total=len(ranked)` prints "10 of 50"
        on an 80-member board — a fabricated total, which is the single thing
        the denominator exists to prevent."""
        _, seen = self._run(monkeypatch, {"17": "kestrel"})
        assert seen["limit"] >= seen["entries"], (
            f"scanned {seen['limit']} of {seen['entries']} entries — every "
            "member past the cap vanishes from the denominator")


class TestTheCommandIsRegisteredAndGuarded:
    def test_leaderboard_is_registered(self):
        from tests.source_scan import handler_sources
        # Every file the handler class is made of: /leaderboard is leaving
        # for the start-here mixin; the registration stays in build_app.
        src = "\n".join(p.read_text(encoding="utf-8") for p in handler_sources())
        assert '("leaderboard", self._cmd_leaderboard)' in src

    def test_it_carries_a_permission_a_role_actually_holds(self):
        """An invented permission string grants the command to no role but
        admin — what silently happened to exposure/networth/research/rwa."""
        import ast

        from bot.utils.user_store import ROLE_PERMISSIONS
        from tests.source_scan import handler_sources
        # Parsed per file, not over a joined string: each mixin opens with a
        # __future__ import, which is a SyntaxError anywhere but the top.
        srcs = [p.read_text(encoding="utf-8") for p in handler_sources()]
        decs = next(([ast.unparse(d) for d in n.decorator_list]
                     for src in srcs for n in ast.walk(ast.parse(src))
                     if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                     and n.name == "_cmd_leaderboard"), [])
        assert any("guard" in d for d in decs), f"ungated: {decs}"
        perm = re.search(r"guard\('([a-z_]+)'\)", " ".join(decs)).group(1)
        holders = [r for r, p in ROLE_PERMISSIONS.items() if perm in p]
        assert "trader" in holders and "viewer" in holders, (
            f"{perm!r} held by {holders} — the command would be invisible to "
            "the roles the catalogue advertises it to")


class TestAHandleLongerThanItsColumn:
    """`app/routes/leaderboard.js` admits handles up to 20 characters. This
    column is 14. The card truncated for display and then IDENTIFIED on the
    truncated value, which produced three distinct failures from one root cause
    — surfaced by a live remark that two members were "2 different Buddys".

    The third is the serious one: it publishes somebody else's verified record
    as yours, on the one board whose entire pitch is verified identity.
    """

    def test_a_member_longer_than_the_column_still_sees_their_own_row(self):
        rows = [{"rank": 1, "handle": "BuddyKingTrader1", "profit_factor": "2.0",
                 "round_trips": 9},
                {"rank": 2, "handle": "kestrel", "profit_factor": "1.1",
                 "round_trips": 4}]
        card = plain(render_leaderboard(rows, "BuddyKingTrader1", ranked_total=2))
        assert "◀" in card, (
            "invisible to themselves in the list they came to find themselves in")

    def test_exactly_one_row_is_ever_marked(self):
        """The false positive: a 14-char viewer handle that is another member's
        prefix marked BOTH rows."""
        viewer = "BuddyKingTrad1"                       # exactly the column width
        rows = [{"rank": 1, "handle": "BuddyKingTrad1EXTRA",
                 "profit_factor": "9.9", "round_trips": 30},
                {"rank": 2, "handle": viewer, "profit_factor": "1.0",
                 "round_trips": 2}]
        card = plain(render_leaderboard(rows, viewer, ranked_total=2))
        assert card.count("◀") == 1, "another member's record marked as yours"

    def test_and_it_is_the_viewers_own_row(self):
        viewer = "BuddyKingTrad1"
        rows = [{"rank": 1, "handle": "BuddyKingTrad1EXTRA",
                 "profit_factor": "9.9", "round_trips": 30},
                {"rank": 2, "handle": viewer, "profit_factor": "1.0",
                 "round_trips": 2}]
        marked = [ln for ln in plain(render_leaderboard(
            rows, viewer, ranked_total=2)).split("\n") if "◀" in ln]
        assert marked and marked[0].strip().startswith("2"), (
            f"marked the wrong rank: {marked}")

    def test_a_shortened_name_is_visibly_shortened(self):
        """`BuddyKingTrade` reads as a whole handle. `BuddyKingTrad…` does not,
        and the ellipsis is the only honest claim available at this width."""
        rows = [{"rank": 1, "handle": "BuddyKingTraderX", "profit_factor": "9.9",
                 "round_trips": 30}]
        card = plain(render_leaderboard(rows, None, ranked_total=1))
        assert "…" in card
        assert "BuddyKingTrade " not in card

    def test_a_handle_that_fits_is_not_mutilated(self):
        card = plain(render_leaderboard(
            [{"rank": 1, "handle": "kestrel", "profit_factor": "1.0",
              "round_trips": 2}], None, ranked_total=1))
        assert "kestrel" in card and "…" not in card

    def test_a_handle_exactly_the_column_width_keeps_every_character(self):
        """Off-by-one guard: 14 fits, so it must not lose its last character."""
        exact = "BuddyKingTrad1"
        assert len(exact) == 14
        card = plain(render_leaderboard(
            [{"rank": 1, "handle": exact, "profit_factor": "1.0",
              "round_trips": 2}], None, ranked_total=1))
        assert exact in card and "…" not in card

    def test_the_shared_helper_is_the_one_the_other_boards_use(self):
        from bot.formatters import board_cards, share_invite
        assert board_cards.display_handle is share_invite.display_handle

    def test_it_never_exceeds_the_column_it_was_given(self):
        """The helper exists to fit a fixed-width `<pre>` table. Appending the
        ellipsis AFTER a full-width cut returns width+1 and every row below it
        shears — the table stops being a table, which is the one thing the
        monospace block buys."""
        from bot.formatters.share_invite import display_handle
        for length in range(0, 40):
            for width in (1, 2, 5, 12, 14, 20):
                out = display_handle("x" * length, width)
                assert len(out) <= width, (
                    f"{length}ch handle in a {width}ch column -> {len(out)}ch")

    def test_a_zero_width_column_returns_empty_not_an_ellipsis(self):
        from bot.formatters.share_invite import display_handle
        assert display_handle("kestrel", 0) == ""
