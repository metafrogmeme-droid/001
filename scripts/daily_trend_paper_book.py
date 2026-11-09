#!/usr/bin/env python3
"""The paper book and its writer, for the sealed daily trend forward test.

Design and rule: docs/research/prereg/daily_trend_paper_forward_2026-11/
(PREREGISTRATION.md, config.json, ADDENDUM_1.md). Paper only: this places
nothing on any venue, reads no trading or signing key, and changes nothing in
the bot. It refuses to run in a live mainnet environment.

    python3 scripts/daily_trend_paper_book.py decide        # daily, 00:40 UTC
    python3 scripts/daily_trend_paper_book.py fill          # daily, 01:10 UTC
    python3 scripts/daily_trend_paper_book.py head          # the chain head
    python3 scripts/daily_trend_paper_book.py export-heads --out FILE
    python3 scripts/daily_trend_paper_book.py selftest --chain /tmp/x.jsonl

One book, two readers. The writer and ``daily_trend_paper_eval.py`` both run
``simulate`` on the same public data with the rule from the pinned commit, so
the writer's daily record can be checked against a replay nobody controls.

Every decision of day D is a fixed function of Bitget's public 1h candles and
funding history and of ``bot.core.daily_trend.read_book`` at the commit
config.json pins. The rule runs in a separate interpreter inside a
``git archive`` of that commit, unpacked to a temporary directory outside the
repository, with an environment that carries nothing but PATH and HOME.

Accounting (config.json ``paper_execution``): the book starts flat at equity
1.0. On day D the book is marked at the 00:00 UTC close (the close of the last
1h candle that opened before D), the rule's target weights are read off the
daily bars that closed at 00:00, and the trades fill at the close of the 1h
candle 00:00-01:00, buys 0.05% above it and sells 0.05% below, paying 0.06%
of traded notional. Funding is charged at each published settlement on the
quantity held at that moment; a settlement at the fill time is paid on the
quantity before the fill.
"""
from __future__ import annotations

import argparse
import bisect
import contextlib
import gzip
import hashlib
import io
import json
import math
import os
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterator, Mapping, Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

UTC = timezone.utc
STUDY = ROOT / "docs/research/prereg/daily_trend_paper_forward_2026-11"
CONFIG_PATH = STUDY / "config.json"
#: The sealed configuration's sha256 (PREREGISTRATION.md). A config.json that
#: is not this file is refused, never read.
CONFIG_SHA256 = "db1669e6faa6393d1957a88912c21dafc92652edd5a142c393a86c35e25cac74"

HOUR_MS = 3_600_000
DAY_MS = 86_400_000
WEEK_MS = 7 * DAY_MS
#: Daily bars the pinned rule is handed before each decision. It needs 121
#: (the 120-day lookback's prior close); 130 leaves room for a missing day.
WARMUP_DAYS = 130
EARLIEST_CLOSE_WEEKS = 26
HARD_CLOSE_WEEKS = 52
MIN_CLOSED_EPISODES = 150
MAX_DRAWDOWN = 0.15
#: A decision counts as live-covered when it was read at or before this long
#: after its day's 00:00 UTC (the fill time).
COVER_BY_MS = HOUR_MS

DECISION = "DT_DECISION"
FILLS = "DT_FILLS"
HEAD_PUBLISHED = "DT_HEAD_PUBLISHED"
HEAD_PUBLISH_FAILED = "DT_HEAD_PUBLISH_FAILED"
ACTOR = "daily_trend_paper"

API = "https://api.bitget.com"
CHAIN_ENV = "DAILY_TREND_PAPER_CHAIN"
CHAIN_DEFAULT = "data/learning/daily_trend_paper_chain.jsonl"
CACHE_ENV = "DAILY_TREND_PAPER_CACHE"
CACHE_DEFAULT = "data/learning/daily_trend_paper_cache"


class Refused(RuntimeError):
    """A run this module will not make, with the reason a person reads."""


# ── the sealed configuration ────────────────────────────────────────────────

def sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def load_config(path: Path = CONFIG_PATH, expected: str = CONFIG_SHA256) -> dict:
    """config.json, only when its bytes are the sealed ones."""
    raw = path.read_bytes()
    got = sha256_bytes(raw)
    if got != expected:
        raise Refused(f"config.json sha256 {got[:12]} is not the sealed {expected[:12]}")
    cfg: dict = json.loads(raw)
    return cfg


def parse_utc_ms(text: str) -> int:
    return int(datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp() * 1000)


def iso_day(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=UTC).date().isoformat()


def day_floor(ms: int) -> int:
    return ms - ms % DAY_MS


@dataclass(frozen=True)
class Costs:
    fee: float
    slippage: float

    def scaled(self, k: float) -> "Costs":
        return Costs(self.fee * k, self.slippage * k)


@dataclass(frozen=True)
class Study:
    """What the writer and the evaluator read out of config.json."""

    preset: dict
    bases: tuple[str, ...]
    symbols: tuple[str, ...]
    band: float
    costs: Costs
    stress: float
    window_open: int
    commit: str
    rule_files: dict

    @property
    def earliest_close(self) -> int:
        return self.window_open + EARLIEST_CLOSE_WEEKS * WEEK_MS

    @property
    def hard_close(self) -> int:
        return self.window_open + HARD_CLOSE_WEEKS * WEEK_MS


def study_from(cfg: dict) -> Study:
    preset = cfg["strategy"]["preset"]
    symbols = tuple(str(s) for s in preset["symbols"])
    from bot.core.strategy_gate import _base
    pe = cfg["paper_execution"]
    rc = cfg["strategy"]["rule_code"]
    return Study(
        preset=preset,
        bases=tuple(_base(s) for s in symbols),
        symbols=symbols,
        band=float(preset["daily_trend"]["band"]),
        costs=Costs(float(pe["fee_per_side"]), float(pe["slippage_per_side"])),
        stress=float(pe["stress_multiplier"]),
        window_open=parse_utc_ms(cfg["window"]["opens"]),
        commit=str(rc["commit"]),
        rule_files={k: v["sha256"] for k, v in rc["files"].items()},
    )


# ── market data ─────────────────────────────────────────────────────────────

@dataclass
class Market:
    """One market's closed 1h candles ([open_ms, o, h, l, c, v], ascending,
    one per open time) and its funding settlements ([ms, rate], ascending)."""

    rows: list[list[float]] = field(default_factory=list)
    funding: list[tuple[int, float]] = field(default_factory=list)
    _opens: list[int] = field(init=False, repr=False, default_factory=list)

    def __post_init__(self) -> None:
        self._opens = [int(r[0]) for r in self.rows]

    def close_of(self, open_ms: int) -> Optional[float]:
        i = bisect.bisect_left(self._opens, open_ms)
        if i < len(self._opens) and self._opens[i] == open_ms:
            return float(self.rows[i][4])
        return None

    def last_close_before(self, ms: int, not_before: int) -> Optional[float]:
        """Close of the last candle that opened before ``ms``, when it opened
        at or after ``not_before``; None otherwise."""
        i = bisect.bisect_left(self._opens, ms) - 1
        if i >= 0 and self._opens[i] >= not_before:
            return float(self.rows[i][4])
        return None

    def window(self, start_ms: int, end_ms: int) -> list[list[float]]:
        lo = bisect.bisect_left(self._opens, start_ms)
        hi = bisect.bisect_left(self._opens, end_ms)
        return self.rows[lo:hi]


Data = dict[str, Market]          # keyed by base ticker ("BTC")


def _num(x: object) -> Optional[float]:
    try:
        f = float(x)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def clean_rows(raw: list, now_ms: int) -> list[list[float]]:
    """Closed candles only, one per open time, ascending. A row whose numbers
    do not read is dropped, never zero-filled."""
    out: dict[int, list[float]] = {}
    for r in raw or []:
        try:
            t = int(r[0])
        except (TypeError, ValueError, IndexError):
            continue
        vals = [_num(v) for v in list(r[1:6])]
        if len(vals) < 5 or any(v is None for v in vals):
            continue
        if t + HOUR_MS > now_ms:
            continue
        out[t] = [float(t)] + [float(v) for v in vals if v is not None]
    return [out[k] for k in sorted(out)]


# ── public fetch (network; never reached by a test) ────────────────────────

def _get(path: str) -> dict:
    req = urllib.request.Request(API + path, headers={"User-Agent": "paper-forward-test"})
    for attempt in range(5):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                d: dict = json.loads(r.read())
            if d.get("code") == "00000":
                return d
            raise RuntimeError(f"{path.split('?')[0]}: code {d.get('code')}")
        except Exception:
            if attempt == 4:
                raise
            time.sleep(2 ** attempt)
    raise AssertionError("unreachable")


def fetch_candles(symbol: str, start_ms: int, end_ms: int,
                  get: Callable[[str], dict] = _get) -> list:
    """Hourly candles opening in [start_ms, end_ms), paged back from the end
    100 at a time (the endpoint earlier studies used)."""
    rows: dict[int, list] = {}
    cursor = end_ms
    while cursor > start_ms:
        page = get(f"/api/v3/market/history-candles?category=USDT-FUTURES&symbol={symbol}"
                   f"&interval=1H&limit=100&endTime={cursor}").get("data") or []
        if not page:
            break
        for r in page:
            rows[int(r[0])] = r
        earliest = min(int(r[0]) for r in page)
        if earliest >= cursor:
            break
        cursor = earliest - 1
        time.sleep(0.12)
    return [rows[k] for k in sorted(rows) if start_ms <= k < end_ms]


def fetch_funding(symbol: str, start_ms: int, get: Callable[[str], dict] = _get) -> list:
    """[[settlement_ms, rate], ...] from start_ms on, newest pages first."""
    out: dict[int, float] = {}
    page = 1
    while True:
        rows = get(f"/api/v2/mix/market/history-fund-rate?symbol={symbol}"
                   f"&productType=USDT-FUTURES&pageSize=100&pageNo={page}").get("data") or []
        if not rows:
            break
        for r in rows:
            rate = _num(r.get("fundingRate"))
            if rate is not None:
                out[int(r["fundingTime"])] = rate
        if min(int(r["fundingTime"]) for r in rows) < start_ms:
            break
        page += 1
        time.sleep(0.12)
    return [[k, out[k]] for k in sorted(out) if k >= start_ms]


def load_market_files(directory: Path, study: Study) -> Data:
    """Markets from ``<SYMBOL>.json.gz`` files ({"rows": [...], "funding": [...]})."""
    data: Data = {}
    for sym, base in zip(study.symbols, study.bases):
        p = directory / f"{sym}.json.gz"
        if not p.is_file():
            continue
        d = json.loads(gzip.decompress(p.read_bytes()))
        data[base] = Market(rows=[list(map(float, r)) for r in d.get("rows") or []],
                            funding=[(int(t), float(x)) for t, x in d.get("funding") or []])
    return data


def write_market_file(directory: Path, symbol: str, rows: list, funding: list) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    p = directory / f"{symbol}.json.gz"
    body = json.dumps({"rows": rows, "funding": funding}, separators=(",", ":")).encode()
    p.write_bytes(gzip.compress(body, mtime=0))
    return p


def refresh_cache(study: Study, cache: Path, now_ms: int,
                  get: Callable[[str], dict] = _get) -> Data:
    """The writer's own copy of the public data, extended to ``now_ms``. The
    evaluator never reads it: it fetches its own after the close."""
    start = study.window_open - WARMUP_DAYS * DAY_MS
    for sym in study.symbols:
        p = cache / f"{sym}.json.gz"
        rows: list = []
        funding: list = []
        if p.is_file():
            d = json.loads(gzip.decompress(p.read_bytes()))
            rows, funding = d.get("rows") or [], d.get("funding") or []
        since = int(rows[-1][0]) + HOUR_MS if rows else start
        rows = clean_rows(rows + fetch_candles(sym, since, now_ms, get), now_ms)
        f_since = int(funding[-1][0]) + 1 if funding else study.window_open
        merged = {int(t): float(x) for t, x in funding}
        merged.update({int(t): float(x) for t, x in fetch_funding(sym, f_since, get)})
        funding = [[k, merged[k]] for k in sorted(merged) if k <= now_ms]
        write_market_file(cache, sym, rows, funding)
    return load_market_files(cache, study)


# ── the pinned rule ─────────────────────────────────────────────────────────

_RULE_RUNNER = r"""
import bisect, json, sys
from bot.core.daily_trend import read_book
req = json.load(sys.stdin)
warm = int(req["warmup_ms"])
bars = req["bars"]
opens = {b: [int(r[0]) for r in rows] for b, rows in bars.items()}
out = []
for cut in req["cutoffs"]:
    sl = {}
    for b, rows in bars.items():
        lo = bisect.bisect_left(opens[b], cut - warm)
        hi = bisect.bisect_left(opens[b], cut)
        sl[b] = rows[lo:hi]
    book = read_book(sl, req["preset"])
    reads = book["reads"]
    out.append({"cutoff": cut, "n_book": book["n_book"],
                "targets": {b: (reads[b].weight if b in reads else None) for b in req["bases"]}})
json.dump(out, sys.stdout)
"""


@contextlib.contextmanager
def pinned_rule(study: Study, repo: Path = ROOT) -> Iterator[Path]:
    """A temporary directory, outside the repository, holding ``bot/`` as it
    stands at the pinned commit, after its rule files' sha256 are checked."""
    proc = subprocess.run(["git", "-C", str(repo), "archive", "--format=tar", study.commit, "bot"],
                          capture_output=True)
    if proc.returncode != 0:
        raise Refused(f"commit {study.commit[:9]} is not in this checkout (git archive failed)")
    archive = proc.stdout
    with tempfile.TemporaryDirectory(prefix="dt_paper_rule_") as tmp:
        with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
            tar.extractall(tmp, filter="data")
        for rel, want in study.rule_files.items():
            got = sha256_bytes((Path(tmp) / rel).read_bytes())
            if got != want:
                raise Refused(f"{rel} at {study.commit[:9]} is {got[:12]}, sealed {want[:12]}")
        yield Path(tmp)


def rule_targets(data: Data, study: Study, cutoffs: list[int], rule_dir: Path) -> dict[int, dict]:
    """{cutoff_ms: {"n_book": n, "targets": {base: weight or None}}} from the
    rule in ``rule_dir``, run in its own interpreter with an empty environment.
    None is a market the rule could not read that day, never a zero."""
    if not cutoffs:
        return {}
    lo = min(cutoffs) - WARMUP_DAYS * DAY_MS
    hi = max(cutoffs)
    req = {"preset": study.preset, "bases": list(study.bases), "cutoffs": sorted(cutoffs),
           "warmup_ms": WARMUP_DAYS * DAY_MS,
           "bars": {b: m.window(lo, hi) for b, m in data.items()}}
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(rule_dir),
           "PYTHONDONTWRITEBYTECODE": "1", "PYTHONHASHSEED": "0"}
    proc = subprocess.run([sys.executable, "-c", _RULE_RUNNER], cwd=str(rule_dir),
                          input=json.dumps(req), capture_output=True, text=True, env=env)
    if proc.returncode != 0:
        tail = (proc.stderr or "").strip().splitlines()[-1:] or ["no output"]
        raise Refused(f"the pinned rule did not run: {tail[0][:200]}")
    return {int(r["cutoff"]): {"n_book": r["n_book"], "targets": r["targets"]}
            for r in json.loads(proc.stdout)}


# ── the book ────────────────────────────────────────────────────────────────

@dataclass
class Trade:
    base: str
    dq: float
    fill: float
    exec_price: float
    fee: float


@dataclass
class Day:
    """Day D: the mark, what the rule asked for, and what was traded.

    A weight is None when the market is held and no mark could price it: an
    unknown, never a zero. A market not held has weight 0.0, a measurement."""

    day: int
    equity: float
    targets: dict[str, Optional[float]]
    held: dict[str, Optional[float]]  # weight before the trade, at the mark
    after: dict[str, Optional[float]]  # weight the day trades to (held when untouched)
    trades: list[Trade]
    filled: bool                      # False: the fill price was not there yet
    unfilled: list[str]
    unpriced: list[str]               # a target with no mark to size it by


@dataclass
class Episode:
    base: str
    open_day: int
    eq_open: float
    close_day: Optional[int] = None
    flows: float = 0.0
    fees: float = 0.0
    slippage: float = 0.0
    funding: float = 0.0
    traded: float = 0.0               # traded notional, for turnover

    @property
    def ret(self) -> float:
        return self.flows / self.eq_open


@dataclass
class Book:
    days: list[Day]
    closed: list[Episode]
    open: dict[str, Episode]
    cash: float = 1.0
    qty: dict[str, float] = field(default_factory=dict)
    funding_carried: int = 0
    marks_missing: int = 0


def wants_trade(t: Optional[float], q: float, w: Optional[float], band: float) -> bool:
    """config.json ``rebalance``: exit at a zero target, enter from flat, and
    otherwise trade only when the target is more than ``band`` of the held
    weight away. An unread target trades nothing; a held weight that cannot be
    priced cannot be rebalanced, only exited."""
    if t is None:
        return False
    if t == 0.0:
        return q > 0.0
    if q == 0.0:
        return True
    if w is None:
        return False
    return abs(t - w) > band * w


def simulate(data: Data, study: Study, targets: dict[int, dict], days: list[int],
             costs: Costs, fill_last: bool = True) -> Book:
    """Run the paper book over ``days`` (day starts, ms, ascending, the first
    the window's open). ``fill_last`` False stops the last day at its
    decision, for a writer that runs before the fill candle has closed."""
    book = Book(days=[], closed=[], open={})
    qty = book.qty
    next_funding = {b: 0 for b in data}

    def settle(through_ms: int) -> None:
        for b, m in data.items():
            i = next_funding[b]
            while i < len(m.funding) and m.funding[i][0] <= through_ms:
                t, rate = m.funding[i]
                i += 1
                q = qty.get(b, 0.0)
                if q == 0.0 or t < days[0]:
                    continue
                px = m.close_of(t - HOUR_MS)
                if px is None:
                    px = m.last_close_before(t, t - DAY_MS)
                    book.funding_carried += 1
                if px is None:
                    continue
                pay = q * px * rate
                book.cash -= pay
                ep = book.open.get(b)
                if ep is not None:
                    ep.flows -= pay
                    ep.funding += pay
            next_funding[b] = i

    for i, d in enumerate(days):
        settle(d)
        marks = {b: data[b].last_close_before(d, d - DAY_MS) if b in data else None
                 for b in study.bases}
        equity = book.cash
        for b, q in qty.items():
            px = marks.get(b)
            if px is None:
                book.marks_missing += 1
                px = data[b].last_close_before(d, 0)
            if px is not None:
                equity += q * px
        tg = (targets.get(d) or {}).get("targets") or {}
        day_targets: dict[str, Optional[float]] = {b: tg.get(b) for b in study.bases}
        held: dict[str, Optional[float]] = {}
        after: dict[str, Optional[float]] = {}
        wanted: dict[str, float] = {}
        unpriced: list[str] = []
        for b in study.bases:
            px = marks[b]
            q = qty.get(b, 0.0)
            w: Optional[float] = 0.0 if q == 0.0 else (q * px / equity if px is not None else None)
            held[b] = w
            after[b] = w
            t = day_targets[b]
            if t is None or not wants_trade(t, q, w, study.band):
                continue
            if t == 0.0:
                wanted[b] = 0.0
            elif px is None:
                unpriced.append(b)
                continue
            else:
                wanted[b] = t * equity / px
            after[b] = t
        last = i == len(days) - 1
        settle(d + HOUR_MS)
        trades: list[Trade] = []
        unfilled: list[str] = []
        filled = not (last and not fill_last)
        if filled:
            for b, q_star in wanted.items():
                fill = data[b].close_of(d)
                if fill is None:
                    unfilled.append(b)
                    after[b] = held[b]
                    continue
                q = qty.get(b, 0.0)
                dq = q_star - q
                ex = fill * (1 + costs.slippage) if dq > 0 else fill * (1 - costs.slippage)
                fee = abs(dq) * fill * costs.fee
                flow = -dq * ex - fee
                book.cash += flow
                if q == 0.0:
                    book.open[b] = Episode(base=b, open_day=d, eq_open=equity)
                ep = book.open[b]
                ep.flows += flow
                ep.fees += fee
                ep.slippage += abs(dq) * fill * costs.slippage
                ep.traded += abs(dq) * fill
                if q_star == 0.0:
                    qty.pop(b, None)
                    ep.close_day = d
                    book.closed.append(book.open.pop(b))
                else:
                    qty[b] = q_star
                trades.append(Trade(b, dq, fill, ex, fee))
        book.days.append(Day(d, equity, day_targets, held, after, trades, filled, unfilled, unpriced))
    return book


def mark_book(book: Book, data: Data, at_ms: int) -> float:
    """Book equity at ``at_ms`` (a 00:00 UTC mark), open positions marked."""
    eq = book.cash
    for b, q in book.qty.items():
        px = data[b].last_close_before(at_ms, 0)
        if px is not None:
            eq += q * px
    return eq


def days_between(start_ms: int, end_ms: int) -> list[int]:
    """Day starts in [start_ms, end_ms)."""
    return list(range(day_floor(start_ms), end_ms, DAY_MS))


# ── the chain ───────────────────────────────────────────────────────────────

def chain_path() -> Path:
    from bot.utils.paths import env_state_path
    return env_state_path(CHAIN_ENV, CHAIN_DEFAULT)


def open_chain(path: Path):  # -> AuditChain
    from bot.utils.audit_chain import AuditChain
    chain = AuditChain(str(path))
    # AuditChain signs every 50th entry with the attestation key. This book
    # reads no key, so it never reaches that path (each run appends at most
    # three entries to a fresh instance; this makes the bound explicit).
    chain._auto_sign_interval = 10 ** 12
    return chain


def read_chain(path: Path) -> list[dict]:
    """Every entry, oldest first. Lines that are not entries are skipped here;
    ``AuditChain.verify`` is where they are reported."""
    if not path.is_file():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if isinstance(d, dict) and "entry_hash" in d:
            out.append(d)
    return out


def running_rule_sha256(study: Study) -> dict[str, Optional[str]]:
    out: dict[str, Optional[str]] = {}
    for rel in study.rule_files:
        p = ROOT / rel
        out[rel] = sha256_bytes(p.read_bytes()) if p.is_file() else None
    return out


def decision_payload(day: Day, study: Study, read_at: datetime) -> dict:
    return {
        "day": iso_day(day.day),
        "read_at": read_at.isoformat(timespec="seconds"),
        "config_sha256": CONFIG_SHA256,
        "rule_commit": study.commit,
        "running_rule_sha256": running_rule_sha256(study),
        "mark_equity": day.equity,
        "targets": day.targets,
        "held": day.held,
        "after": day.after,
        "orders": sorted(b for b in study.bases if day.after[b] != day.held[b]),
        "unpriced": day.unpriced,
    }


def fills_payload(day: Day, costs: Costs) -> dict:
    return {
        "day": iso_day(day.day),
        "price_source": "Bitget USDT-M public 1h candle 00:00-01:00 UTC, close",
        "fee_per_side": costs.fee,
        "slippage_per_side": costs.slippage,
        "fills": [{"base": t.base, "dq": t.dq, "price": t.fill, "exec_price": t.exec_price,
                   "fee": t.fee} for t in day.trades],
        "unfilled": day.unfilled,
    }


def entries_for(entries: list[dict], event: str, day: str) -> list[dict]:
    return [e for e in entries if e.get("event_type") == event
            and (e.get("payload") or {}).get("day") == day]


# ── publishing the head ─────────────────────────────────────────────────────

Publisher = Callable[[str], str]


def telegram_publisher(env: Mapping[str, str]) -> Optional[Publisher]:
    """Posts to the operator's alert chat (the variables health_check.sh
    uses). The token is only ever sent to api.telegram.org, never logged."""
    token = (env.get("TELEGRAM_BOT_TOKEN") or "").strip()
    chat = (env.get("TELEGRAM_ALERT_CHAT_ID") or "").strip()
    if not token or not chat:
        return None

    def post(text: str) -> str:
        body = urllib.parse.urlencode({"chat_id": chat, "text": text}).encode()
        req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage", data=body)
        with urllib.request.urlopen(req, timeout=20) as r:
            d = json.loads(r.read())
        if not d.get("ok"):
            raise RuntimeError("telegram refused the message")
        return str((d.get("result") or {}).get("message_id"))
    return post


def publish_head(chain, path: Path, publisher: Optional[Publisher], day: str) -> bool:
    """Post the head and record that it was posted (or why not)."""
    entries = read_chain(path)
    if not entries:
        return False
    head = entries[-1]
    if publisher is None:
        chain.append(HEAD_PUBLISH_FAILED, {"day": day, "head_sequence": head["sequence"],
                                           "reason": "no publisher configured"}, ACTOR)
        return False
    text = (f"Daily trend paper test {day}: chain head #{head['sequence']} "
            f"sha256 {head['entry_hash']}")
    try:
        ref = publisher(text)
    except Exception as exc:  # noqa: BLE001 - the class is recorded, never the message
        chain.append(HEAD_PUBLISH_FAILED, {"day": day, "head_sequence": head["sequence"],
                                           "reason": type(exc).__name__}, ACTOR)
        return False
    chain.append(HEAD_PUBLISHED, {"day": day, "head_sequence": head["sequence"],
                                  "head_hash": head["entry_hash"], "message_ref": ref}, ACTOR)
    return True


# ── the writer ──────────────────────────────────────────────────────────────

def refuse_live(env: Mapping[str, str]) -> None:
    from bot.core.testnet_book import LIVE, deployment_kind
    if deployment_kind(env) == LIVE:
        raise Refused("this environment is live mainnet; the paper writer runs only on a paper process")


def _book_through(study: Study, data: Data, today: int, rule_dir: Path,
                  fill_last: bool) -> Book:
    days = days_between(study.window_open, today + DAY_MS)
    targets = rule_targets(data, study, days, rule_dir)
    return simulate(data, study, targets, days, study.costs, fill_last=fill_last)


def run_decide(study: Study, chain_file: Path, data: Data, now: datetime, rule_dir: Path,
               publisher: Optional[Publisher], selftest: bool = False) -> str:
    now_ms = int(now.timestamp() * 1000)
    today = day_floor(now_ms)
    if not selftest and today < study.window_open:
        raise Refused(f"the window opens {iso_day(study.window_open)}; nothing is recorded before it")
    if not selftest and today >= study.hard_close:
        raise Refused("the window's hard close has passed")
    day_iso = iso_day(today)
    entries = read_chain(chain_file)
    if entries_for(entries, DECISION, day_iso):
        return f"{day_iso}: decision already recorded"
    st = study if not selftest else _with_open(study, today)
    book = _book_through(st, data, today, rule_dir, fill_last=False)
    chain = open_chain(chain_file)
    chain.append(DECISION, decision_payload(book.days[-1], st, now), ACTOR)
    posted = publish_head(chain, chain_file, publisher, day_iso)
    late = now_ms - today > COVER_BY_MS
    return (f"{day_iso}: decision recorded{' (after 01:00 UTC: not covered)' if late else ''}; "
            f"head {'published' if posted else 'NOT published'}")


def run_fill(study: Study, chain_file: Path, data: Data, now: datetime, rule_dir: Path,
             selftest: bool = False) -> str:
    now_ms = int(now.timestamp() * 1000)
    today = day_floor(now_ms)
    day_iso = iso_day(today)
    entries = read_chain(chain_file)
    if not entries_for(entries, DECISION, day_iso):
        raise Refused(f"{day_iso}: no decision recorded to fill")
    if entries_for(entries, FILLS, day_iso):
        return f"{day_iso}: fills already recorded"
    st = study if not selftest else _with_open(study, today)
    book = _book_through(st, data, today, rule_dir, fill_last=True)
    day = book.days[-1]
    if not any(m.close_of(today) is not None for m in data.values()):
        raise Refused(f"{day_iso}: the 00:00-01:00 candle has not closed yet")
    open_chain(chain_file).append(FILLS, fills_payload(day, st.costs), ACTOR)
    return f"{day_iso}: {len(day.trades)} fill(s) recorded, {len(day.unfilled)} unfilled"


def _with_open(study: Study, open_ms: int) -> Study:
    from dataclasses import replace
    return replace(study, window_open=open_ms)


def export_heads(path: Path) -> str:
    """One line per entry: sequence, entry sha256, timestamp, event, day. The
    weekly file committed under the study's heads/ directory."""
    lines = [f"{e['sequence']} {e['entry_hash']} {e['timestamp']} {e['event_type']} "
             f"{(e.get('payload') or {}).get('day', '-')}" for e in read_chain(path)]
    return "\n".join(lines) + ("\n" if lines else "")


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("decide", "fill", "selftest"):
        s = sub.add_parser(name)
        s.add_argument("--chain", type=Path, default=None)
        s.add_argument("--cache", type=Path, default=None)
        if name == "selftest":
            s.add_argument("--publish", action="store_true")
    sub.add_parser("head").add_argument("--chain", type=Path, default=None)
    ex = sub.add_parser("export-heads")
    ex.add_argument("--chain", type=Path, default=None)
    ex.add_argument("--out", type=Path, required=True)
    args = ap.parse_args(argv)
    chain_file = args.chain or chain_path()
    try:
        if args.cmd == "head":
            entries = read_chain(chain_file)
            print(f"#{entries[-1]['sequence']} {entries[-1]['entry_hash']}" if entries else "empty")
            return 0
        if args.cmd == "export-heads":
            args.out.write_text(export_heads(chain_file), encoding="utf-8")
            print(f"wrote {args.out}")
            return 0
        refuse_live(os.environ)
        study = study_from(load_config())
        from bot.utils.paths import env_state_path
        cache = args.cache or env_state_path(CACHE_ENV, CACHE_DEFAULT)
        now = datetime.now(UTC)
        now_ms = int(now.timestamp() * 1000)
        if args.cmd == "selftest":
            if args.chain is None or args.chain.resolve() == chain_path().resolve():
                raise Refused("selftest writes only to a --chain of its own, never the test's chain")
            st = _with_open(study, day_floor(now_ms))
            data = refresh_cache(st, cache, now_ms)
            with pinned_rule(study) as rule_dir:
                pub = telegram_publisher(os.environ) if args.publish else None
                print(run_decide(study, chain_file, data, now, rule_dir, pub, selftest=True))
                with contextlib.suppress(Refused):
                    print(run_fill(study, chain_file, data, now, rule_dir, selftest=True))
            return 0
        data = refresh_cache(study, cache, now_ms)
        with pinned_rule(study) as rule_dir:
            if args.cmd == "decide":
                print(run_decide(study, chain_file, data, now, rule_dir, telegram_publisher(os.environ)))
            else:
                print(run_fill(study, chain_file, data, now, rule_dir))
        return 0
    except Refused as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())


__all__ = ["Book", "Costs", "Day", "Episode", "Market", "Refused", "Study", "DECISION", "FILLS",
           "HEAD_PUBLISHED", "HEAD_PUBLISH_FAILED", "load_config", "study_from", "simulate",
           "rule_targets", "pinned_rule", "days_between", "mark_book", "read_chain",
           "open_chain", "run_decide", "run_fill", "publish_head", "export_heads",
           "refuse_live", "wants_trade"]
