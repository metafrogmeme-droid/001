"""A frozen dataset's holes are filled in its own window, never by a new one.

Every dataset frozen before the pager kept each page's first bar carries six
missing hours a symbol. Re-running the freezer would replace the window (it
reads up to now), so every card measured on it would move because the market
moved. `snapshot.repair_dataset` fills the named holes instead, and only where
the venue's hour either side equals the stored bar, every field: that is the
evidence the missing hour belongs to the same series.

Driven against a venue stand-in that answers ``since``/``until`` as Bitget
does, over a real dataset written by the real freezer helpers.
"""
from __future__ import annotations

import asyncio
import gzip
import json
from datetime import datetime, timezone

import pytest

from bot.backtest import snapshot as snap
from bot.backtest.data_loader import DataLoader

HOUR = 3_600_000
T0 = int(datetime(2026, 5, 1, tzinfo=timezone.utc).timestamp() * 1000)
N = 48
HOLES = (10, 30)
SYM = "BTC/USDT:USDT"


def _row(i: int) -> list:
    """Hour ``i``'s bar: distinct values, so a wrong hour is a wrong bar."""
    return [T0 + i * HOUR, 100.0 + i, 101.5 + i, 99.25 + i, 100.5 + i, 1000.0 + 7 * i]


def _iso(i: int) -> str:
    return datetime.fromtimestamp((T0 + i * HOUR) / 1000, timezone.utc).isoformat()


def _frozen(tmp_path):
    """A one-symbol dataset frozen with two holes, as the old pager left it."""
    bars = DataLoader.from_ohlcv_list([_row(i) for i in range(N) if i not in HOLES], SYM)
    DataLoader.save_csv(bars, str(tmp_path / f"{snap.safe_symbol(SYM)}.csv.gz"))
    man = snap.build_manifest(tmp_path, "1h", N, {SYM: bars},
                              created_at=datetime(2026, 7, 4, tzinfo=timezone.utc))
    (tmp_path / "manifest.json").write_text(json.dumps(man, indent=2) + "\n")
    assert man["symbols"][SYM]["missing"] == [_iso(i) for i in HOLES]
    return man


class Venue:
    """Every hour's bar before ``until``, at most four, from ``since``.

    ``until`` is exclusive, as on both of Bitget's candle endpoints.
    ``exclusive``: only bars after ``since``, as its recent candles answer;
    otherwise the bar at ``since`` too, as its history candles do.
    """

    def __init__(self, rows=None, exclusive=False):
        self.rows = {r[0]: r for r in (rows or [_row(i) for i in range(N)])}
        self.exclusive = exclusive
        self.calls: list[tuple] = []

    async def fetch(self, symbol, since, until):
        self.calls.append((symbol, since, until))
        return [list(self.rows[k]) for k in sorted(self.rows)
                if (k > since if self.exclusive else k >= since) and k < until][:4]


def _repair(tmp_path, venue):
    return asyncio.run(snap.repair_dataset(
        tmp_path, venue.fetch, repaired_at=datetime(2026, 10, 12, tzinfo=timezone.utc)))


def _lines(tmp_path):
    with gzip.open(tmp_path / f"{snap.safe_symbol(SYM)}.csv.gz", "rt") as fh:
        return fh.read().splitlines()


@pytest.mark.parametrize("exclusive", [False, True], ids=["history", "recent"])
def test_the_holes_are_filled_inside_the_same_window(tmp_path, exclusive):
    before = _frozen(tmp_path)
    old_lines = _lines(tmp_path)
    man = _repair(tmp_path, Venue(exclusive=exclusive))
    entry = man["symbols"][SYM]
    assert entry["missing"] == [] and entry["repaired"] == [_iso(i) for i in HOLES]
    assert entry["bars"] == N and entry["first"] == before["symbols"][SYM]["first"]
    assert entry["last"] == before["symbols"][SYM]["last"], "the window did not move"
    assert man["created_at"] == before["created_at"], "the freeze time is the window's"
    assert man["dataset_hash"] != before["dataset_hash"]
    assert man["repairs"] == [{"at": "2026-10-12T00:00:00+00:00", "filled": 2,
                               "source": snap.REPAIR_SOURCE}]
    bars = DataLoader.from_csv(str(tmp_path / entry["file"]))
    assert [b.close for b in bars] == [100.5 + i for i in range(N)]
    new_lines = _lines(tmp_path)
    assert [ln for ln in new_lines if ln not in old_lines] == [
        new_lines[1 + i] for i in HOLES], "every stored row is kept byte for byte"
    assert snap.verify_dataset(tmp_path) == (True, [])
    assert json.loads((tmp_path / "manifest.json").read_text()) == man


def test_each_hole_is_asked_with_the_hour_either_side(tmp_path):
    _frozen(tmp_path)
    venue = Venue()
    _repair(tmp_path, venue)
    assert venue.calls == [(SYM, T0 + (i - 2) * HOUR, T0 + (i + 2) * HOUR) for i in HOLES]


def _rows_with(changed: int, **field):
    rows = [_row(i) for i in range(N)]
    idx = {"open": 1, "high": 2, "low": 3, "close": 4, "volume": 5}
    for k, v in field.items():
        rows[changed][idx[k]] = v
    return rows


def test_a_neighbour_that_differs_leaves_the_hole_named(tmp_path):
    """The hour after the first hole differs only in volume; the hour before
    the second hole differs only in its close."""
    before = _frozen(tmp_path)
    file_before = (tmp_path / f"{snap.safe_symbol(SYM)}.csv.gz").read_bytes()
    rows = _rows_with(HOLES[0] + 1, volume=1.0)
    rows[HOLES[1] - 1][4] = 0.0
    man = _repair(tmp_path, Venue(rows))
    assert man["symbols"][SYM]["missing"] == [_iso(i) for i in HOLES]
    assert "repaired" not in man["symbols"][SYM] and "repairs" not in man
    assert man["dataset_hash"] == before["dataset_hash"]
    assert (tmp_path / f"{snap.safe_symbol(SYM)}.csv.gz").read_bytes() == file_before
    assert snap.verify_dataset(tmp_path) == (True, [])


def test_only_the_hole_with_both_neighbours_equal_is_filled(tmp_path):
    _frozen(tmp_path)
    man = _repair(tmp_path, Venue(_rows_with(HOLES[1] + 1, high=1.0)))
    assert man["symbols"][SYM]["repaired"] == [_iso(HOLES[0])]
    assert man["symbols"][SYM]["missing"] == [_iso(HOLES[1])]
    assert man["repairs"][0]["filled"] == 1
    assert snap.verify_dataset(tmp_path) == (True, [])


def test_an_hour_the_venue_does_not_answer_stays_missing(tmp_path):
    _frozen(tmp_path)
    rows = [_row(i) for i in range(N) if i != HOLES[0]]
    man = _repair(tmp_path, Venue(rows))
    assert man["symbols"][SYM]["missing"] == [_iso(HOLES[0])]
    assert man["symbols"][SYM]["repaired"] == [_iso(HOLES[1])]


def test_the_verifier_holds_a_repaired_bar_to_the_file(tmp_path):
    _frozen(tmp_path)
    man = _repair(tmp_path, Venue())
    man["symbols"][SYM]["repaired"].append(_iso(N + 5))
    (tmp_path / "manifest.json").write_text(json.dumps(man, indent=2) + "\n")
    ok, problems = snap.verify_dataset(tmp_path)
    assert not ok and problems == [f"{SYM}: 1 repaired bar(s) not in the file"]
