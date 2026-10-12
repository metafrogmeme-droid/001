"""The freezer keeps every bar of every page, and names a hole it cannot fill.

Bitget answers a candle read's ``since`` two ways: its history candles include
the bar at ``since``, and its recent candles (about the last two months) return
only the bars after it. The pager started each page one bar past the last one
it held, so on recent pages it lost that bar: every frozen dataset carried six
missing bars per symbol, one every 201 hours, and every backtest read across
each hole as if the hour had not happened. Those datasets are repaired in
their own windows now
(`tests/test_a_frozen_dataset_s_holes_are_filled_in_its_own_window.py`). Driven against a venue that answers
each way; the manifest names a hole, and the verifier holds it to the file.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone

import pytest

from bot.backtest import snapshot as snap
from bot.backtest.data_loader import DataLoader, fetch_closed_ohlcv, missing_bars
from bot.backtest.models import BacktestBar

HOUR = 3_600_000
T0 = int(datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
N = 1_500


class Venue:
    """Hourly bars T0..T0+(N-1)h, answering ``since`` as Bitget's endpoint does.

    ``exclusive``: only bars after ``since`` (its recent candles); otherwise
    the bar at ``since`` too (its history candles). ``until`` is inclusive and
    a page holds at most ``limit`` bars. Records every call.
    """

    def __init__(self, exclusive: bool, holes: frozenset[int] = frozenset()):
        self.exclusive = exclusive
        self.rows = [[T0 + i * HOUR, 1.0, 2.0, 0.5, 1.5, 10.0]
                     for i in range(N) if i not in holes]
        self.calls: list[dict] = []

    def milliseconds(self) -> int:
        # Seven minutes into the hour after the last bar: in life that hour's
        # bar is forming and dropped, so a window of ``limit`` hours holds
        # ``limit - 1`` closed bars, under either answer to ``since``.
        return T0 + N * HOUR + 7 * 60_000

    def parse_timeframe(self, tf: str) -> int:
        assert tf == "1h"
        return 3600

    async def fetch_ohlcv(self, symbol, timeframe, since=None, limit=None, params=None):
        self.calls.append({"since": since, "limit": limit, "params": params})
        if since is None:
            return [list(r) for r in self.rows[-limit:]]
        until = (params or {}).get("until", float("inf"))
        page = [r for r in self.rows
                if (r[0] > since if self.exclusive else r[0] >= since) and r[0] <= until]
        return [list(r) for r in page[:limit]]


def _read(venue, limit, symbol="ETH/USDT:USDT"):
    return asyncio.run(fetch_closed_ohlcv(venue, symbol, "1h", limit))


@pytest.mark.parametrize("exclusive", [True, False], ids=["recent", "history"])
def test_every_page_keeps_its_first_bar(exclusive):
    venue = Venue(exclusive)
    rows = _read(venue, 1_000)
    assert len(venue.calls) > 4, "the read did not page"
    assert missing_bars(rows, HOUR) == []
    assert [r[0] for r in rows] == [T0 + i * HOUR for i in range(N - 999, N)]


def test_the_recent_venue_does_skip_the_bar_at_since():
    """The fixture can produce the state: asked from a bar it leaves that bar out,
    so a page started one bar past the last one held loses a bar."""
    venue = Venue(exclusive=True)
    since = T0 + 10 * HOUR
    page = asyncio.run(venue.fetch_ohlcv("X", "1h", since=since, limit=5,
                                         params={"until": since + 5 * HOUR}))
    assert page[0][0] == since + HOUR
    history = Venue(exclusive=False)
    page = asyncio.run(history.fetch_ohlcv("X", "1h", since=since, limit=5,
                                           params={"until": since + 5 * HOUR}))
    assert page[0][0] == since


def test_one_page_is_one_call_and_a_venue_hole_is_named_not_filled():
    venue = Venue(exclusive=True, holes=frozenset({N - 50}))
    rows = _read(venue, 150)
    assert len(venue.calls) == 1 and venue.calls[0]["since"] is None
    assert missing_bars(rows, HOUR) == [T0 + (N - 50) * HOUR]
    # Paged, the same venue hole is still a hole and still named.
    paged = _read(Venue(exclusive=True, holes=frozenset({N - 50})), 1_000)
    assert missing_bars(paged, HOUR) == [T0 + (N - 50) * HOUR]


def _bars(holes=()):
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return [BacktestBar(timestamp=start + timedelta(hours=i), open=1.0, high=2.0, low=0.5,
                        close=1.5, volume=10.0, symbol="ETH/USDT:USDT")
            for i in range(300) if i not in holes]


def test_the_manifest_names_each_hole_and_the_verifier_holds_it(tmp_path):
    whole, holed = _bars(), _bars(holes=(100, 101))
    for name, bars in (("ETH/USDT:USDT", holed), ("BTC/USDT:USDT", whole)):
        DataLoader.save_csv(bars, str(tmp_path / f"{snap.safe_symbol(name)}.csv.gz"))
    man = snap.build_manifest(tmp_path, "1h", 300,
                              {"ETH/USDT:USDT": holed, "BTC/USDT:USDT": whole})
    assert man["symbols"]["BTC/USDT:USDT"]["missing"] == []
    assert man["symbols"]["ETH/USDT:USDT"]["missing"] == [
        "2026-01-05T04:00:00+00:00", "2026-01-05T05:00:00+00:00"]
    (tmp_path / "manifest.json").write_text(json.dumps(man, indent=2) + "\n")
    assert snap.verify_dataset(tmp_path) == (True, [])
    # A manifest that says less than the file is a drift, both ways.
    man["symbols"]["ETH/USDT:USDT"]["missing"] = []
    (tmp_path / "manifest.json").write_text(json.dumps(man, indent=2) + "\n")
    ok, problems = snap.verify_dataset(tmp_path)
    assert not ok and problems == ["ETH/USDT:USDT: missing bars differ from manifest"]
    # And one that names a hole the file does not have.
    man["symbols"]["ETH/USDT:USDT"]["missing"] = [
        "2026-01-05T04:00:00+00:00", "2026-01-05T05:00:00+00:00"]
    man["symbols"]["BTC/USDT:USDT"]["missing"] = ["2026-01-05T04:00:00+00:00"]
    (tmp_path / "manifest.json").write_text(json.dumps(man, indent=2) + "\n")
    ok, problems = snap.verify_dataset(tmp_path)
    assert not ok and problems == ["BTC/USDT:USDT: missing bars differ from manifest"]


#: The datasets frozen before the fix. Each carried the pager's six holes a
#: symbol, and each was repaired in its own window (`snapshot --repair`,
#: 2026-10-12) rather than re-frozen over a new one.
REPAIRED = ("alts_1h", "alts_1h_v2", "alts_1h_v3", "corr_dense_1h",
            "majors_1h", "majors_1h_v2", "majors_1h_v3")


def test_the_committed_datasets_name_their_holes():
    """Every committed dataset names its holes (none, or the hours the venue
    would not vouch for), and the verifier holds the file to that list."""
    import glob
    import os
    manifests = sorted(glob.glob(os.path.join(snap.benchmark_root(), "*", "manifest.json")))
    assert manifests
    for path in manifests:
        man = json.loads(open(path, encoding="utf-8").read())
        for sym, entry in man["symbols"].items():
            assert "missing" in entry, (path, sym)
        assert snap.verify_dataset(os.path.dirname(path)) == (True, []), path


@pytest.mark.parametrize("name", REPAIRED)
def test_the_pager_s_holes_are_filled_in_the_datasets_it_left_them_in(name):
    """Six hours a symbol were filled, none is left, and the manifest says
    which hours came from the repair and when."""
    import os
    path = os.path.join(snap.benchmark_root(), name, "manifest.json")
    man = json.loads(open(path, encoding="utf-8").read())
    for sym, entry in man["symbols"].items():
        assert entry["missing"] == [], (name, sym)
        assert len(entry["repaired"]) == 6, (name, sym)
    assert [r["filled"] for r in man["repairs"]] == [6 * len(man["symbols"])]
    assert man["repairs"][0]["source"] == snap.REPAIR_SOURCE
