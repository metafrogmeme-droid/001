"""Plant the roster, assert what the card says.

`/users` is what an operator reads before typing `/approve <id>`, `/revoke
<id>` or tapping admit — all three take the id it just printed. It printed
`u["telegram_id"][-8:]`.

`6307156912` came out as `07156912`, which is not a key in the store, so the
next command answers "not found". And the shortening was INVISIBLE: 8- and
9-digit Telegram ids exist, so a row reading `71461243` might be whole or might
be a tail, with nothing to distinguish them. On the roster this was written
against, five rows gave themselves away only by a leading `0` — which no real
Telegram id has. The rest were ambiguous by construction.

Same shape as a partial sum printed as a total. The rule is not "never
shorten"; it is that a shortened value must not wear the format of a complete
one.

RED HERRING, planted in test_a_short_id_is_not_marked: a genuinely 8-digit
Telegram id. It looks exactly like what the old code produced from a 10-digit
one, and a fix that marked everything — or marked nothing — would be
indistinguishable from correct on a roster of only long ids.
"""
from __future__ import annotations

from bot.formatters.user_roster import ELLIPSIS, MAX_ID, cells, render_table


def _col(line: str, token: str) -> int:
    """The CELL offset a token starts at — not its string index. With emoji in
    a row the two differ, which is the whole subject of the tests below."""
    return cells(line[:line.index(token)])


def _u(tid, name="Ann", role="trader", tier="basic", authorized=True):
    return {"telegram_id": tid, "name": name, "role": role, "tier": tier,
            "authorized": authorized}


def _table(users, live=lambda _i: False, **kw):
    return "\n".join(render_table(users, live, **kw))


def _body(users, live=lambda _i: False, **kw):
    """The data rows only — no <pre>, header or rule."""
    lines = render_table(users, live, **kw)
    return [ln for ln in lines if ln.startswith(" ") and "─" not in ln][1:]


def _ids(users, **kw):
    """The ID CELL of each row, exactly.

    Substring-matching the whole card cannot do this job: `6307156912 `
    contains `07156912 `, so "the truncation is gone" passes against the very
    output it is meant to reject. Ids never contain spaces, so the first token
    of a row is the whole cell and nothing else.
    """
    return [ln.split()[0] for ln in _body(users, **kw)]


# ── the id is usable ─────────────────────────────────────────────────

def test_a_full_telegram_id_is_printed_in_full():
    """The founding case. The operator has to be able to type this back in,
    so the cell must equal the id — not merely contain it."""
    assert _ids([_u("6307156912")]) == ["6307156912"]


def test_a_web_id_survives_intact():
    """`web:90001` came out as `eb:90001` — a different string entirely, and
    one the gateway would never recognise."""
    assert _ids([_u("web:90001", name="pbdes2022")]) == ["web:90001"]


def test_every_id_on_a_real_roster_is_complete():
    """The production shape: one admin, hand-approved teammates, web accounts."""
    users = ([_u("6307156912", role="admin", tier="admin")]
             + [_u(str(700000000 + i), name=f"Teammate{i}") for i in range(10)]
             + [_u(f"web:9000{i}", role="paper") for i in (1, 2, 3)])
    assert _ids(users, limit=15) == [u["telegram_id"] for u in users[-15:]]


# ── when it must shorten, it says so ─────────────────────────────────

def test_an_over_long_id_is_marked_as_shortened():
    long_id = "web:" + "9" * 30
    out = _table([_u(long_id)])
    assert long_id not in out
    assert ELLIPSIS in out, (
        "shortened without a marker — the defect was invisibility, not length")


def test_a_short_id_is_not_marked():
    """RED HERRING. A genuine 8-digit Telegram id looks exactly like what the
    old truncation produced from a 10-digit one. Marking it would tell the
    operator their real id is partial; marking nothing brings the bug back."""
    out = _table([_u("71461243")])
    assert "71461243" in out
    assert ELLIPSIS not in out


def test_the_marked_and_unmarked_cases_are_distinguishable():
    """The whole point, stated as one assertion: two ids of the same rendered
    length, one whole and one not, must not read the same."""
    whole = _table([_u("7" * MAX_ID)])
    cut = _table([_u("7" * (MAX_ID + 5))])
    assert ELLIPSIS not in whole
    assert ELLIPSIS in cut


# ── absent is not a value ────────────────────────────────────────────

def test_a_missing_tier_does_not_render_as_basic():
    """`u.get("tier", "basic")` asserted a real tier for a record that has
    none — the same shape as `.get("pnl", 0)`."""
    out = _table([{"telegram_id": "1", "name": "Ann", "role": "trader",
                   "authorized": True}])
    assert "basic" not in out
    assert "?" in out


def test_a_missing_role_does_not_render_as_anything_real():
    """Asserted on the ROLE cell, not the card: MODE legitimately says `paper`
    on every non-live row, so a card-wide `"paper" not in out` would fail on
    correct output — and pass only on output that had lost the mode column."""
    row = _body([{"telegram_id": "1", "name": "Ann", "authorized": True}])[0]
    role_cell = row.split()[2]                    # "✓trader" / "?…"
    for role in ("trader", "paper", "viewer", "admin", "pending"):
        assert role not in role_cell


def test_an_absent_authorized_flag_is_not_a_denial():
    """register() writes the key for everyone, so a record without it is odd
    enough to be worth seeing rather than folding into ✗."""
    absent = _table([{"telegram_id": "1", "name": "Ann", "role": "trader",
                      "tier": "basic"}])
    explicit = _table([_u("1", authorized=False)])
    assert "✗" not in absent
    assert "✗" in explicit


def test_an_authorized_user_still_reads_as_authorized():
    assert "✓" in _table([_u("1", authorized=True)])


def test_a_json_integer_boolean_is_not_reported_as_unknown():
    """Found by a surviving mutant. An identity check (`is True`) separates
    absence correctly and then misreads `authorized: 1` — which a hand-edited
    or older file can carry — as a record nobody can read. Absence is what
    needs isolating; after that, truthiness is the right question."""
    assert "✓" in _table([_u("1", authorized=1)])
    assert "✗" in _table([_u("1", authorized=0)])


# ── the rest of the row still works ──────────────────────────────────

def test_live_and_paper_come_from_the_caller():
    assert "LIVE" in _table([_u("1")], live=lambda _i: True)
    assert "paper" in _table([_u("1")], live=lambda _i: False)


def test_the_mode_lookup_gets_the_real_id_not_the_rendered_one():
    """_can_trade_live keys on the store id. Handing it the shortened string
    would answer for a user that does not exist — and `web:` ids are
    structurally paper-only precisely by prefix, which a tail-slice removes."""
    seen = []
    _table([_u("web:" + "9" * 30)], live=lambda i: seen.append(i) or False)
    assert seen == ["web:" + "9" * 30]


def test_the_limit_keeps_the_most_recent():
    users = [_u(str(i), name=f"U{i}") for i in range(20)]
    out = _table(users, limit=5)
    assert "U19" in out and "U15" in out
    assert "U14" not in out


def test_columns_line_up_when_ids_differ_in_length():
    """A ragged table is how the fixed 10-wide column came to truncate: the
    width has to come from the data."""
    rows = _body([_u("1"), _u("6307156912"), _u("web:90001")])
    assert len({_col(ln, "trader") for ln in rows}) == 1, (
        "the role column does not start at the same offset on every row")


# ── width is cells, not code points ──────────────────────────────────

def test_an_emoji_counts_as_two_cells():
    """`len()` says one. Every monospace renderer says two, which is why the
    rows belonging to users who decorate their names were always the crooked
    ones."""
    assert cells("💗") == 2
    assert cells("ab") == 2
    assert len("💗") == 1


def test_joiners_and_variation_selectors_occupy_nothing():
    """Counting a zero-width character as 1 is the same error mirrored."""
    assert cells("‍") == 0          # ZWJ
    assert cells("️") == 0          # VS16
    assert cells("é") == 1         # e + combining acute


def test_an_emoji_name_does_not_shift_its_own_row():
    """The visible defect, asserted where it shows: two rows, one plain name
    and one with emoji, and the ROLE column starts at the same cell."""
    rows = _body([_u("6307156912", name="Robert"),
                  _u("6307156913", name="Kiiwi🍎🔍"),
                  _u("6307156914", name="Asiwaju 🎅🎄")])
    assert len({_col(ln, "trader") for ln in rows}) == 1


def test_a_wide_name_still_fits_its_column():
    """Two emoji are four cells, so a name of them must not eat the next
    column — the width has to be computed in the same unit as the padding."""
    rows = _body([_u("1", name="🍎🍎🍎"), _u("2", name="abc")])
    assert len({_col(ln, "trader") for ln in rows}) == 1


def test_an_empty_roster_does_not_explode():
    assert render_table([], lambda _i: False)


def test_a_nameless_user_is_not_blank():
    out = _table([{"telegram_id": "1", "role": "trader", "tier": "basic",
                   "authorized": True}])
    assert "?" in out


def test_a_name_that_is_shortened_says_so():
    out = _table([_u("1", name="A" * 40)])
    assert ELLIPSIS in out


def test_an_emoji_name_does_not_leave_a_dangling_joiner():
    """The roster is full of emoji names. Slicing code points can cut a ZWJ
    sequence and leave the joiner trailing, which renders as a stray box."""
    out = _table([_u("1", name="👨‍👩‍👧‍👦" * 5)])
    assert not any(ln.rstrip().endswith("‍") for ln in out.split("\n"))
