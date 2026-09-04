"""\"1 Trade Setup Found\" beside \"Pending Ideas: 2\", with nothing explaining it.

Two counters over the same collection:

  * `/status` prints `len(engine._pending_ideas)` — every idea held.
  * `/latest_signal` prints the subset at or above
    `CONFIG.risk.signal_display_min_confidence` (default 70%).

Both are individually true, which is what makes the pair misleading: the
operator reads two headline numbers on one screen and nothing on either card
accounts for the gap. `below_note` already handled the case where NOTHING
clears the line; the MIXED case — some above, some below — said nothing at
all, and reads as "the engine found one idea" when it found two.

The header is built inline in a 14k-line handler, so this locks the wiring;
the arithmetic it protects is `len(all) - len(shown)`.
"""
import io

from tests.source_scan import code_only


def _code():
    return code_only(
        io.open("bot/skills/telegram_handler.py", encoding="utf-8").read())


def _header_block():
    code = _code()
    i = code.index('f"{\'s\' if len(pending) > 1 else \'\'} Found</b>"')
    return code[max(0, i - 900):i + 900]


def test_the_hidden_count_is_computed_from_a_fresh_read():
    # The re-scan branches above refresh `pending` and NOT the `all_pending`
    # taken at entry, so trusting the entry-time list would understate the
    # gap after exactly the path that produces it.
    block = _header_block()
    assert "_all_now = list(self.engine.pending_ideas)" in block
    assert "_hidden = max(0, len(_all_now) - len(pending))" in block


def test_the_header_says_how_many_were_filtered_out():
    block = _header_block()
    assert "if _hidden and not below_note:" in block
    assert "confidence line, not shown" in block


def test_it_names_the_number_status_shows():
    # Naming the other surface's total is the point: it is what turns two
    # disagreeing numbers into one reconciled reading.
    assert "/status counts all" in _header_block()


def test_nothing_hidden_adds_no_note():
    # `if _hidden` — a run where everything clears the line renders exactly
    # as it did before.
    assert "if _hidden and not below_note:" in _header_block()


def test_the_below_line_case_is_not_double_reported():
    # `below_note` already says the whole list is under the bar. Adding
    # "N more below the line" underneath it would be the same fact twice.
    assert "and not below_note" in _header_block()
