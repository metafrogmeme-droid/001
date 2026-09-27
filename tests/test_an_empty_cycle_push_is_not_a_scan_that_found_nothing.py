"""An empty summary push is not a scan that found nothing.

THE PANEL SAID THE RISK GATE WAS DOING ITS JOB OVER A PUSH THAT RAN NO SCAN.
The dashboard's setups panel had one empty state and one sentence for it:

    "No qualifying setups in the last scan -- the gate is doing its job."

Driven, that was false three ways, and the third is the expensive one.

ONE: IT FIRED WHEN NO SCAN HAD RUN. The bot's autonomous cycle calls
`_push_scan_summary_to_website`, which calls `_build_scan_payload([], engine)`
for the circuit-breaker block and the regime -- its own docstring says the rest
"would stay placeholder". That payload carried `entry_cards: []` and
`symbols: {}`, and `app/routes/sync.js` replaces the stored scan WHOLESALE
(only the deep-scan block was carried forward), so the last manual `/scan`'s
cards were wiped within a cycle and the panel reported the absence as a risk
control working.

TWO: THE GATE WAS NEVER CONSULTED. `entry_cards` is filtered by
`r["score"] >= SETUP_SCORE_FLOOR` -- the SCANNER's own score. The risk gate
runs at confirm time, which the panel's own footer already says
("Confirmations run through its risk gate"). Attributing the absence to the
gate names a control that never ran, which is the `/vault` hint shape pointed
at a panel: a card naming a control that did nothing.

THREE: A FAILED READ RENDERED AS THE GATE WORKING. The card loop skips a
candidate whose ATR it could not read, and before `record_atr` kept
significant digits every sub-cent asset recorded `0.0` -- so a universe of
cheap assets produced zero cards and the panel called that discipline. It also
skips a candidate whose direction it could not read, the same shape one field
over.

So the producer OMITS the two blocks when it scanned nothing (an empty list is
a scan that found nothing, which is a different fact), and publishes
`entry_cards_read` -- what was scanned, what cleared the floor, what was shown,
and the two READ FAILURES -- beside the cards it describes.
"""

from types import SimpleNamespace

import pytest

from bot.skills.scan_skill import (
    ENTRY_CARDS_SHOWN,
    SETUP_SCORE_FLOOR,
    _build_scan_payload,
)


def _row(sym="BTC/USDT", *, score=0.7, direction="LONG", atr=100.0, price=63000.0):
    """One `_scan_symbol()` row, with every field the payload reads."""
    return {
        "sym": sym, "score": score, "dir": direction, "price": price,
        "rsi": 55.0, "atr": atr, "vol_ratio": 1.4, "patterns": [],
    }


class TestASummaryPushOmitsWhatItDidNotScan:
    """The push that runs no scan must not look like a scan."""

    def test_a_summary_push_omits_the_cards_and_the_symbols(self):
        payload = _build_scan_payload([], engine=None, scanned=False)
        # OMITTED, not sent empty. An empty list is a scan that found nothing;
        # an absent block is a push that scanned nothing, and the ingest can
        # only tell them apart if the producer does.
        assert "entry_cards" not in payload
        assert "symbols" not in payload

    def test_a_summary_push_omits_the_reading_too(self):
        """The reading describes the scan that produced the cards beside it.

        Sent on a summary push it would REPLACE the last real scan's reading
        while the ingest carried that scan's cards forward -- the counts and
        the list describing two different scans."""
        payload = _build_scan_payload([], engine=None, scanned=False)
        assert "entry_cards_read" not in payload

    def test_a_summary_push_still_carries_what_it_is_for(self):
        """It exists to refresh the breaker and the regime. Both still ride."""
        payload = _build_scan_payload([], engine=None, scanned=False)
        assert isinstance(payload.get("circuit_breaker"), dict)
        assert isinstance(payload.get("regime"), dict)
        assert isinstance(payload.get("features"), dict)

    def test_a_scan_that_found_nothing_sends_an_empty_list(self):
        """The other fact, and it is a reading: a scan ran and found nothing."""
        payload = _build_scan_payload([], engine=None)
        assert payload["entry_cards"] == []
        assert payload["symbols"] == {}
        assert payload["entry_cards_read"]["results"] == 0

    def test_the_engines_cycle_push_says_it_scanned_nothing(self):
        """The one production caller that passes no results.

        Read rather than driven: `_push_scan_summary_to_website` is a 60-line
        method behind an engine, a scanner and an HTTP push, and the claim is
        one keyword at one call site."""
        import ast
        import inspect
        import textwrap

        from bot.core.engine import RuneClawEngine

        src = inspect.getsource(RuneClawEngine._push_scan_summary_to_website)
        tree = ast.parse(textwrap.dedent(src))
        calls = [
            n for n in ast.walk(tree)
            if isinstance(n, ast.Call)
            and getattr(n.func, "id", None) == "_build_scan_payload"
        ]
        assert len(calls) == 1, "the summary push builds one payload"
        kw = {k.arg: k.value for k in calls[0].keywords}
        assert "scanned" in kw, (
            "the cycle summary must say it ran no scan, or the ingest cannot "
            "tell its empty list from a scan that found nothing"
        )
        assert isinstance(kw["scanned"], ast.Constant)
        assert kw["scanned"].value is False


class TestTheReadingSaysWhyThereAreNoCards:
    """Every count the panel needs, from where the knowledge is."""

    def test_a_scan_that_read_nothing(self):
        r = _build_scan_payload([], engine=None)["entry_cards_read"]
        assert r["results"] == 0 and r["above_floor"] == 0 and r["cards"] == 0

    def test_symbols_read_and_none_above_the_floor(self):
        rows = [_row("BTC/USDT", score=0.1), _row("ETH/USDT", score=0.39)]
        r = _build_scan_payload(rows, engine=None)["entry_cards_read"]
        assert r["results"] == 2
        assert r["above_floor"] == 0
        assert r["cards"] == 0
        assert r["no_atr"] == 0 and r["no_direction"] == 0

    def test_the_floor_is_the_scanners_own_and_travels(self):
        """A second copy of the threshold in the browser is a second answer."""
        r = _build_scan_payload([_row()], engine=None)["entry_cards_read"]
        assert r["floor"] == SETUP_SCORE_FLOOR
        assert r["shown_max"] == ENTRY_CARDS_SHOWN

    def test_a_candidate_with_no_readable_atr_is_a_failed_read(self):
        """The sub-cent case: `record_atr` used to floor every one at 0.0."""
        rows = [_row("PEPE/USDT", atr=0.0, price=0.0000112)]
        p = _build_scan_payload(rows, engine=None)
        assert p["entry_cards"] == []
        r = p["entry_cards_read"]
        assert r["above_floor"] == 1
        assert r["no_atr"] == 1
        assert r["no_direction"] == 0
        assert r["cards"] == 0

    def test_a_candidate_with_no_readable_direction_is_a_failed_read(self):
        rows = [_row("SUI/USDT", direction="")]
        p = _build_scan_payload(rows, engine=None)
        assert p["entry_cards"] == []
        r = p["entry_cards_read"]
        assert r["above_floor"] == 1 and r["no_direction"] == 1 and r["no_atr"] == 0

    @pytest.mark.parametrize("atr", [None, 0.0, -1.0, float("nan")])
    def test_every_unreadable_atr_counts_as_one(self, atr):
        rows = [_row(atr=atr)]
        r = _build_scan_payload(rows, engine=None)["entry_cards_read"]
        assert r["no_atr"] == 1, atr

    def test_the_two_read_failures_are_counted_apart(self):
        """Their remedies differ -- a venue that priced nothing, and a move
        nobody could read -- so folding them would send a reader to the wrong
        one, which is the scan partial's own rule about errors and budget."""
        rows = [_row("A/USDT", atr=0.0), _row("B/USDT", direction="?"),
                _row("C/USDT")]
        p = _build_scan_payload(rows, engine=None)
        r = p["entry_cards_read"]
        assert (r["no_atr"], r["no_direction"], r["cards"]) == (1, 1, 1)
        assert len(p["entry_cards"]) == 1

    def test_the_counts_close_over_what_was_considered(self):
        """cards + no_atr + no_direction == considered, driven rather than
        asserted of the code: a taxonomy that does not close is the
        `analysed = attempts - gave_up` shape one loop over."""
        rows = [_row(f"S{i}/USDT", atr=(0.0 if i % 3 == 0 else 100.0),
                     direction=("" if i % 3 == 1 else "LONG"))
                for i in range(12)]
        r = _build_scan_payload(rows, engine=None)["entry_cards_read"]
        assert r["cards"] + r["no_atr"] + r["no_direction"] == r["considered"]

    def test_considered_is_bounded_by_what_is_shown(self):
        rows = [_row(f"S{i}/USDT") for i in range(ENTRY_CARDS_SHOWN + 5)]
        p = _build_scan_payload(rows, engine=None)
        r = p["entry_cards_read"]
        assert r["above_floor"] == ENTRY_CARDS_SHOWN + 5
        assert r["considered"] == ENTRY_CARDS_SHOWN
        assert r["cards"] == ENTRY_CARDS_SHOWN == len(p["entry_cards"])

    def test_the_reading_rides_on_a_payload_that_has_cards(self):
        """It is not an empty-state field: the panel states the bound it shows
        from the same block, so it has to be there when there ARE cards."""
        p = _build_scan_payload([_row()], engine=None)
        assert len(p["entry_cards"]) == 1
        assert p["entry_cards_read"]["cards"] == 1


class TestTheFloorIsNotTheGate:
    """The claim the panel used to make, refused where the filter lives."""

    def test_the_filter_reads_the_scanners_score_and_nothing_else(self):
        """A source read, and stated as one: the claim is about which QUANTITY
        the comparison reads, which no drive of the payload can see."""
        import ast
        import inspect

        import bot.skills.scan_skill as ss

        # RAW source, parsed: `code_only` blanks docstrings, and this body
        # opens with one, so the stripped copy no longer parses at all. An AST
        # walk cannot see a comment in any case.
        src = inspect.getsource(ss._build_scan_payload)
        tree = ast.parse(src)
        floors = [
            n for n in ast.walk(tree)
            if isinstance(n, ast.Compare)
            and any(getattr(c, "id", None) == "SETUP_SCORE_FLOOR"
                    for c in n.comparators)
        ]
        assert len(floors) == 1, "one floor, and it is the scanner's score"
        left = floors[0].left
        assert isinstance(left, ast.Subscript)
        assert isinstance(left.slice, ast.Constant)
        assert left.slice.value == "score", (
            "the setups filter reads the scan row's own score; a risk-gate "
            "verdict would have to come from the risk engine, which this "
            "function never calls"
        )

    def test_the_payload_builder_consults_no_risk_gate(self):
        """Nothing here evaluates an idea, so nothing here may be reported as
        the gate's doing."""
        import ast
        import inspect

        import bot.skills.scan_skill as ss

        # Every CALL the builder makes, by AST rather than by text: a comment
        # in this function names the risk gate (correctly, to say the gate runs
        # at confirm time), and a text scan would read its own explanation as
        # the defect -- `code_only` cannot be used here because it blanks the
        # docstring and the body no longer parses.
        tree = ast.parse(inspect.getsource(ss._build_scan_payload))
        called = set()
        for n in ast.walk(tree):
            if isinstance(n, ast.Call):
                f = n.func
                called.add(getattr(f, "id", None) or getattr(f, "attr", None))
        for spelling in ("evaluate", "confirm_trade", "_evaluate_locked"):
            assert spelling not in called, spelling


class TestTheFiveEngineAttributesStillRide:
    """A summary push with a partial engine must not raise: it is the push
    that runs every cycle, and a raise there is a dashboard that stops
    updating at all."""

    def test_a_bare_engine_does_not_raise(self):
        payload = _build_scan_payload([], SimpleNamespace(), scanned=False)
        assert "entry_cards" not in payload
        assert isinstance(payload.get("circuit_breaker"), dict)

    def test_a_partial_engine_with_results_does_not_raise(self):
        payload = _build_scan_payload([_row()], SimpleNamespace())
        assert len(payload["entry_cards"]) == 1
        assert payload["entry_cards_read"]["cards"] == 1
