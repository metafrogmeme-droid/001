"""The Live Market Validation page states the recorded stress test, and no account money.

`docs/gitbook/live-validation.md` is served on the website at /docs/live-validation. Until
2026-10-15 it showed a stress-test screenshot (180 runs, 855 trades) beside a table that said
500 runs and 889 trades, a card of claims that had gone stale ("180 Unit Tests Passing",
"Simulation-First: No Real Money" above a table saying live trading was enabled), a heading that
named the exchange sub-account, and its backtests in dollars, all under "unedited output from
actual runs" and with no date.

The page now cites ONE record, `backtest_deep_results.json`: what `python3 run_deep_backtest.py`
wrote, with the commit and time in its own meta. This file holds the page's stress-test tables to
that record, the record's summary to `summarise()` over its own rows (so it was not edited by
hand), and every row to the generator parameters the script draws it with. The backtest sections
may not print a dollar figure: a backtest's balance is reported as a percentage of itself.
"""
from __future__ import annotations

import importlib.util
import json
import re
import statistics
from pathlib import Path

import pytest

from bot.backtest.metrics import share_profitable

ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / "docs" / "gitbook" / "live-validation.md"
RECORD = ROOT / "backtest_deep_results.json"
CITE = "`backtest_deep_results.json`"
STRESS_HEADING = "## Stress test"
BACKTEST_HEADING = "### Backtest"
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
NO_MEAN = "n/a (no run traded)"


@pytest.fixture(scope="module")
def script():
    spec = importlib.util.spec_from_file_location("run_deep_backtest", ROOT / "run_deep_backtest.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def load_record() -> dict:
    return json.loads(RECORD.read_text(encoding="utf-8"))


def _valid(record: dict) -> list[dict]:
    return [r for r in record["results"] if "error" not in r]


def _pct(x) -> str:
    return NO_MEAN if x is None else f"{x:+.2f}%"


def human_date(iso: str) -> str:
    y, m, d = iso[:10].split("-")
    return f"{int(d)} {MONTHS[int(m) - 1]} {y}"


def summary_rows(record: dict) -> list[tuple[str, str]]:
    """The stress-test summary table, label and cell, in the page's order, from the record's summary."""
    s, meta = record["summary"], record["meta"]
    means = s["means_over_traded_runs"]
    failed = meta["error_runs"]
    median = s["median_profit_factor"]
    undefined = s["runs_profit_factor_undefined"]
    return [
        ("Runs completed", f"{meta['valid_runs']} of {meta['total_runs']}" + (f" ({failed} failed)" if failed else "")),
        ("Runs that traded", f"{s['runs_that_traded']} ({s['runs_idle']} did not trade)"),
        ("Trades", f"{s['total_trades']:,}"),
        ("Average return, runs that traded", _pct(means["return_pct"])),
        ("Best run", _pct(s["best_return_pct"])),
        ("Worst run", _pct(s["worst_return_pct"])),
        ("Profitable runs", f"{s['profitable_runs']} of {s['runs_that_traded']} that traded; "
                            f"{s['profitable_runs']} of {s['profitable_runs_total']} in all"),
        ("Median profit factor", NO_MEAN if median is None else f"{median:.2f}"
         + (f" ({undefined} runs had no losing trade, so no ratio)" if undefined else "")),
        ("Average win rate, runs that traded",
         NO_MEAN if means["win_rate"] is None else f"{means['win_rate'] * 100:.1f}%"),
        ("Average max drawdown, runs that traded",
         NO_MEAN if means["max_drawdown_pct"] is None else f"{means['max_drawdown_pct']:.2f}%"),
        ("Worst drawdown", f"{s['worst_drawdown_pct']:.2f}%"),
        ("Runs past 20% drawdown", f"{s['crashed_runs']}"),
    ]


REGIME_HEADER = ("Regime", "Runs", "Traded", "Trades", "Average return, runs that traded", "Profitable runs")


def regime_rows(record: dict) -> list[tuple[str, ...]]:
    """The per-regime table, in the order the script runs the regimes, means over the runs that traded."""
    valid = _valid(record)
    order: list[str] = []
    for r in record["results"]:
        if r["regime"] not in order:
            order.append(r["regime"])
    rows = []
    for regime in order:
        rs = [r for r in valid if r["regime"] == regime]
        traded = [r for r in rs if r["total_trades"] > 0]
        shr = share_profitable((r["total_return_pct"], r["total_trades"]) for r in rs)
        mean = statistics.fmean(r["total_return_pct"] for r in traded) if traded else None
        rows.append((regime, str(len(rs)), str(len(traded)), f"{sum(r['total_trades'] for r in rs):,}",
                     _pct(mean), f"{shr.profitable} of {shr.traded}"))
    return rows


def _section(page: str, heading: str) -> str:
    start = page.index(heading)
    nxt = re.search(r"^##? ", page[start + len(heading):], re.M)
    return page[start: start + len(heading) + nxt.start()] if nxt else page[start:]


def _table_rows(text: str) -> list[tuple[str, ...]]:
    rows = []
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("|") or re.match(r"^\|[\s:|-]+\|$", line):
            continue
        rows.append(tuple(c.strip().strip("*").strip() for c in line.strip("|").split("|")))
    return rows


def test_the_stress_tables_are_the_record():
    page = PAGE.read_text(encoding="utf-8")
    assert page.count(CITE) == 1, f"the page must cite {CITE} once"
    record = load_record()
    rows = _table_rows(_section(page, STRESS_HEADING))
    want = summary_rows(record)
    assert rows[0] == ("Measure", "Result"), rows[0]
    assert rows[1:1 + len(want)] == want
    assert rows[1 + len(want)] == REGIME_HEADER
    assert rows[2 + len(want):] == regime_rows(record)


def test_the_page_dates_the_run_and_names_its_commit():
    page = PAGE.read_text(encoding="utf-8")
    prov = load_record()["meta"]["provenance"]
    section = _section(page, STRESS_HEADING)
    assert f"`{prov['commit'][:7]}`" in section, "the page does not name the commit the record ran at"
    assert human_date(prov["run_at"]) in section, "the page does not date the run"
    assert human_date(prov["run_at"]) in page.split("---")[0], "the opening does not date the run"


def test_the_record_is_the_scripts_output_over_its_full_grid(script):
    record = load_record()
    meta = record["meta"]
    assert (meta["symbols"], meta["regimes"], meta["seeds"], meta["bars_per_run"]) == (
        len(script.SYMBOLS), len(script.REGIMES), len(script.SEEDS), script.BARS)
    assert (meta["data_source"], meta["used_synthetic"]) == ("synthetic", True)
    prov = meta["provenance"]
    assert re.fullmatch(r"[0-9a-f]{40}", prov["commit"] or ""), "the record does not name its commit"
    assert prov["tree_clean"] is True, "the record was produced from an engine or script that was not committed"
    assert prov["command"] == script.COMMAND
    runs = {(s["symbol"], g["label"], seed): (s, g)
            for s in script.SYMBOLS for g in script.REGIMES for seed in script.SEEDS}
    assert sorted((r["symbol"], r["regime"], r["seed"]) for r in record["results"]) == sorted(runs)
    for r in _valid(record):
        sym, regime = runs[(r["symbol"], r["regime"], r["seed"])]
        assert r["data_source"] == "synthetic"
        assert r["generator"] == json.loads(json.dumps(script.generator_params(sym, regime, r["seed"])))
    assert record["summary"] == json.loads(json.dumps(script.summarise(_valid(record)))), (
        "the record's summary is not summarise() over its own rows")


def test_the_backtests_print_no_dollar_figure_and_the_page_names_no_account():
    page = PAGE.read_text(encoding="utf-8")
    for heading in (BACKTEST_HEADING, STRESS_HEADING):
        dollars = re.findall(r"\$\s?\d[\d,.]*", _section(page, heading))
        assert not dollars, f"{heading}: {dollars}; a backtest's results are a percentage of its own balance"
    assert not re.search(r"\bAG-[A-Z0-9]{6,}\b", page), "the page names an exchange sub-account"
    for gone in ("live-validation-backtest.png", "live-validation-evidence.png"):
        assert gone not in page and not (PAGE.parent / gone).exists(), f"{gone} carried claims that had gone stale"
    assert "unedited" not in page.lower(), "the screenshot is a crop; the page may not call it unedited"
