"""Daily state machine over the Morpho coverage markets.

Off-chain backing is the published comparison dated 2025-10-28. A run
before that date does not see those edges. Euler v2 did not answer on the
2026-10-23 pull, so its book is unreadable rather than empty. Listing
counts are not a vault-year, so false fires per vault-year stay unreadable.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
from fractions import Fraction
from pathlib import Path

from bot.rxb.machine import fresh_state, step
from bot.rxb.metrics import GEdge, GToken, cross_venue_dms, solve_graph

_DIR = Path(__file__).parent / "fixtures"
_DAILY = _DIR / "morpho_daily_coverage.json"
_LLAMA = _DIR / "defillama_band_2025.json"
_UNIVERSE = _DIR / "morpho_universe_2026-10-23.json"
_PUBLISHED = _DIR / "stream_elixir_2025.json"

_PUBLICATION = "2025-10-28"
_DISCLOSURE = date(2025, 11, 4)
_FAMILY = frozenset({"xUSD", "deUSD", "sdeUSD"})
_WRAPPER = "sdeUSD"
_WRAPPED = "deUSD"


def _day(ts: int) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).date().isoformat()


def _load(path: Path) -> dict | None:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return raw if isinstance(raw, dict) else None


def _published_edges() -> tuple[list[GToken], list[GEdge]] | None:
    raw = _load(_PUBLISHED)
    if raw is None:
        return None
    published = raw.get("published") if isinstance(raw.get("published"), dict) else None
    nodes = published.get("nodes") if published else None
    if not isinstance(nodes, list):
        return None
    tokens: list[GToken] = []
    edges: list[GEdge] = []
    for row in nodes:
        if not isinstance(row, dict) or not isinstance(row.get("token"), str):
            return None
        nav = row.get("nav")
        if isinstance(nav, bool) or not isinstance(nav, int):
            return None
        tokens.append(GToken(row["token"], nav))
        external = row.get("external")
        if isinstance(external, int) and not isinstance(external, bool) and external > 0:
            edges.append(GEdge(
                src=row["token"], dst=None, usd=external, kind="holding",
                verif="onchain", offchain_id="external_face",
            ))
        for claim in row.get("claims") or []:
            if not isinstance(claim, dict):
                return None
            amount = claim.get("amount")
            if isinstance(amount, bool) or not isinstance(amount, int):
                return None
            edges.append(GEdge(
                src=row["token"], dst=str(claim.get("to") or ""), usd=amount,
                kind="holding", verif="onchain",
            ))
    if not any(t.token == _WRAPPED for t in tokens):
        return None
    tokens.append(GToken(_WRAPPER, None))
    edges.append(GEdge(
        src=_WRAPPER, dst=_WRAPPED, usd=None, kind="holding", verif="onchain",
        weight=Fraction(1),
    ))
    return tokens, edges


def _loan_symbol(loan: object) -> str | None:
    if not isinstance(loan, dict):
        return None
    symbol = loan.get("symbol")
    return symbol if isinstance(symbol, str) else None


def _by_day(points: object, field: str) -> dict[str, object]:
    out: dict[str, object] = {}
    if not isinstance(points, list):
        return out
    for point in points:
        if not isinstance(point, dict):
            continue
        stamp = point.get("x")
        if isinstance(stamp, bool) or not isinstance(stamp, int):
            continue
        out[_day(stamp)] = stamp if field == "x" else point.get("y")
    return out


def _markets(daily: dict) -> list[dict]:
    rows = []
    for market in daily.get("markets") or []:
        if not isinstance(market, dict) or market.get("readable") is not True:
            continue
        collateral_raw = market.get("collateral")
        collateral = collateral_raw if isinstance(collateral_raw, dict) else {}
        symbol = collateral.get("symbol")
        series_raw = market.get("series")
        series = series_raw if isinstance(series_raw, dict) else {}
        borrow = _by_day(series.get("borrow"), "y")
        supply = _by_day(series.get("supply"), "y")
        stamps = _by_day(series.get("borrow"), "x")
        days = sorted(stamps)
        rows.append({
            "chain": market.get("chain"),
            "chain_id": market.get("chain_id"),
            "market_id": market.get("market_id"),
            "symbol": symbol,
            "loan": _loan_symbol(market.get("loan")),
            "days": days,
            "borrow": borrow,
            "supply": supply,
            "stamps": stamps,
            "first": days[0] if days else None,
            "last": days[-1] if days else None,
        })
    return rows


def _one_venue(market: dict | None) -> dict:
    if market is None:
        return {"state": None, "fired_on": None}
    walked = fresh_state()
    prev = None
    for day in market["days"]:
        ts = market["stamps"].get(day)
        if isinstance(ts, bool) or not isinstance(ts, int):
            continue
        dms = cross_venue_dms([{
            "borrow_now": market["borrow"].get(day),
            "borrow_prev": None if prev is None else market["borrow"].get(prev),
        }])
        walked = step(walked, {
            "ts": ts,
            "cbf": None,
            "uf": None,
            "lts": None,
            "dms": dms.get("dms"),
        })
        prev = day
    fired_ts = walked.get("fired_on")
    return {
        "state": walked.get("state"),
        "fired_on": None if not isinstance(fired_ts, int) else _day(fired_ts),
    }


def _graph_for(day: str, bundle: tuple[list[GToken], list[GEdge]] | None) -> dict[str, dict]:
    if bundle is None or day < _PUBLICATION:
        return {}
    solved = solve_graph(bundle[0], bundle[1])
    return solved


def _dms(markets: list[dict], symbols: frozenset[str] | set[str], day: str, previous: dict[tuple, str]) -> dict:
    venues = []
    for market in markets:
        if market["symbol"] not in symbols:
            continue
        if day not in market["borrow"]:
            continue
        key = (market["chain"], market["market_id"])
        prev_day = previous.get(key)
        venues.append({
            "borrow_now": market["borrow"].get(day),
            "borrow_prev": None if prev_day is None else market["borrow"].get(prev_day),
        })
    return cross_venue_dms(venues)


def _ratio(increase: Decimal, base: Decimal) -> str | None:
    if base == 0:
        return None
    quant = (increase / base).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)
    return format(quant, "f")


def _new_supply(market: dict, fired_on: str) -> str | None:
    """Positive supply steps after the fire day, through the disclosure, over fire-day supply."""
    if fired_on not in market["supply"] or market["supply"][fired_on] is None:
        return None
    base = Decimal(str(market["supply"][fired_on]))
    added = Decimal(0)
    prev = None
    for day in market["days"]:
        value = market["supply"].get(day)
        if value is None:
            prev = None
            continue
        cur = Decimal(str(value))
        if prev is not None and fired_on < day <= _DISCLOSURE.isoformat() and cur > prev:
            added += cur - prev
        prev = cur
    return _ratio(added, base)


def _depegs(llama: dict | None) -> list[dict]:
    if not llama:
        return []
    found = []
    for series in llama.get("series") or []:
        if not isinstance(series, dict):
            continue
        if series.get("readable") is not True:
            found.append({
                "token": series.get("token"),
                "chain": series.get("chain"),
                "readable": False,
                "reason": "price_unreadable",
            })
            continue
        points = []
        for point in series.get("points") or []:
            if not isinstance(point, dict) or not isinstance(point.get("t"), int):
                continue
            if point.get("price") is None:
                continue
            points.append((point["t"], Decimal(str(point["price"]))))
        points.sort()
        if not points:
            found.append({
                "token": series.get("token"),
                "chain": series.get("chain"),
                "readable": False,
                "reason": "price_unreadable",
            })
            continue
        first_day = _day(points[0][0])
        below = next(((_day(ts), str(px)) for ts, px in points if px < Decimal("0.95")), None)
        above_at_start = points[0][1] > Decimal("1.05")
        found.append({
            "token": series.get("token"),
            "chain": series.get("chain"),
            "readable": True,
            "first_day": first_day,
            "first_below_peg": None if below is None else below[0],
            "upside_already_outside": above_at_start,
        })
    return found


def run_v2() -> dict:
    daily = _load(_DAILY)
    if daily is None:
        return {"readable": False, "reason": "fixture_unreadable"}
    markets = _markets(daily)
    bundle = _published_edges()
    stream = [m for m in markets if m["symbol"] in _FAMILY]
    days = sorted({day for market in stream for day in market["days"]})
    state = {symbol: fresh_state() for symbol in _FAMILY}
    previous: dict[tuple, str] = {}
    token_out: dict[str, dict] = {}
    for day in days:
        graph = _graph_for(day, bundle)
        family: frozenset[str] | set[str] = _FAMILY if day >= _PUBLICATION else set()
        for symbol in _FAMILY:
            own = [m for m in stream if m["symbol"] == symbol and day in m["stamps"]]
            if not own:
                continue
            ts = min(m["stamps"][day] for m in own)
            metrics = graph.get(symbol) or {}
            # A wrapper's signal is the wrapped issuer's signal. sdeUSD has no
            # cycle of its own; its backing is deUSD from the publication date.
            if symbol == _WRAPPER and _WRAPPED in graph:
                metrics = graph[_WRAPPED]
            symbols = family if symbol in family else {symbol}
            dms = _dms(stream, symbols, day, previous)
            run = {
                "ts": ts,
                "cbf": metrics.get("cbf"),
                "uf": metrics.get("uf"),
                "lts": metrics.get("lts"),
                "dms": dms.get("dms"),
            }
            state[symbol] = step(state[symbol], run)
            token_out[symbol] = {
                "cbf": metrics.get("cbf"),
                "uf": metrics.get("uf"),
                "lts": metrics.get("lts"),
                "dms": dms.get("dms"),
                "state": state[symbol]["state"],
                "readable": metrics.get("readable") if day >= _PUBLICATION else False,
                "reason": None if day >= _PUBLICATION else "graph_not_published",
            }
        for market in stream:
            if day in market["stamps"]:
                previous[(market["chain"], market["market_id"])] = day

    fired = []
    supply = {}
    for market in stream:
        symbol = market["symbol"]
        fired_ts = state[symbol].get("fired_on")
        fired_on = None if not isinstance(fired_ts, int) else _day(fired_ts)
        if fired_on is None:
            continue
        lead = (_DISCLOSURE - date.fromisoformat(fired_on)).days
        fired.append({
            "chain": market["chain"],
            "pair": f"{market['loan']}/{symbol}",
            "market_id": market["market_id"],
            "collateral": symbol,
            "fired_on": fired_on,
            "lead_days": lead,
            "state": state[symbol]["state"],
        })
        supply[f"{market['chain']}:{symbol}"] = _new_supply(market, fired_on)

    usd3 = next((m for m in markets if m["symbol"] == "USD3"), None)
    # No published edges for this collateral. Passing an empty edge list
    # would report LTS = 1. The machine therefore sees no look-through,
    # only the one venue, which is a measured DMS of 0 once a prior day exists.
    usd3_state = _one_venue(usd3)
    universe = _load(_UNIVERSE) or {}
    leads = [row["lead_days"] for row in fired if row["collateral"] in {"xUSD", "sdeUSD"}]
    return {
        "readable": True,
        "publication": _PUBLICATION,
        "disclosure": _DISCLOSURE.isoformat(),
        "fired_on": fired[0]["fired_on"] if fired else None,
        "lead_days": leads[0] if leads and all(x == leads[0] for x in leads) else None,
        "fired": fired,
        "tokens": token_out,
        "usd3": {
            "covered": usd3 is not None,
            "history_starts": None if usd3 is None else usd3["first"],
            "history_ends": None if usd3 is None else usd3["last"],
            "fired_on": usd3_state["fired_on"],
            "state": usd3_state["state"],
        },
        "euler": {"readable": False, "reason": "euler_api_unreadable"},
        "new_supply_over_fire_day": supply,
        "false_fires_per_vault_year": None,
        "false_fires_reason": "listing_count_is_not_a_vault_year",
        "market_listing": universe.get("markets"),
        "listing_as_of": universe.get("pulled_at"),
        "depegs": _depegs(_load(_LLAMA)),
        "no_lookahead": True,
    }
