#!/usr/bin/env python3
"""Day-0 production readout (improvement plan item D0).

Run it on the bot box, from the checkout the bot runs from::

    python3 scripts/production_readout.py --markdown > /tmp/d0-readout.md
    python3 scripts/production_readout.py --json > /tmp/d0-readout.json

and paste the markdown into the appendix of
``docs/adr/0001-day0-production-readout.md``.

READ-ONLY, and that is the whole contract:

- it opens nothing for writing and creates no file or directory (not even
  bytecode: ``main`` sets ``sys.dont_write_bytecode`` before any import);
- it sends nothing over the network (no venue, no model, no website);
- it prints no secret. It reads the named flags and caps through
  ``bot.config``; no secret-bearing key is read into anything it prints, and
  it prints no P&L, balance or other money figure beyond the configured caps
  and budget the plan asks for.

Importing ``bot.config`` normally runs the secrets vault's
``seed_and_restore``, which WRITES the vault file and can create its master
key. ``main`` replaces that call with a no-op before anything imports
``bot.config``. The vault restores secrets only, never the flags and caps this
readout prints, so the values below are the ones the bot resolves.

It also never imports ``bot.core.live_executor`` or anything that imports
``bot.utils.logger``: that module creates ``logs/`` and opens four log files
for append at import.

THREE VALUES, NEVER TWO. Every field is a reading (``"state": "read"``),
``"absent"`` (the file is not there, with why) or ``"unreadable"`` (it is
there and could not be read, with the exception's class). A file this could
not read is never a ledger of zero rows, and a log directory nobody can read
is never "no budget-exhausted audits".
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Optional

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

READ, ABSENT, UNREADABLE = "read", "absent", "unreadable"

#: How many of the newest filled closes the venue-priced share is read over.
RECENT_CLOSES = 50


def _read(**data: Any) -> dict:
    return {"state": READ, **data}


def _absent(reason: str, **data: Any) -> dict:
    return {"state": ABSENT, "reason": reason, **data}


def _unreadable(why: BaseException | str, **data: Any) -> dict:
    """``error`` is an exception's CLASS, never its text (a message can echo a
    path or a value); ``reason`` is a shape this could not read."""
    if isinstance(why, BaseException):
        return {"state": UNREADABLE, "error": type(why).__name__, **data}
    return {"state": UNREADABLE, "reason": why, **data}


def _sorted_counts(counts: Mapping[str, int]) -> dict[str, int]:
    return dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))


# ── Flags, caps and budgets ────────────────────────────────────────────────

_WEB_LIVE = "bot.web.web_live_gate.feature_enabled"

#: (env name, where the code holds the resolved value, unit). A dotted path is
#: read off ``bot.config.CONFIG``, so the value printed is the one the bot
#: resolved (bounds, clamps, the .env-over-process precedence), never the raw
#: text. WEB_LIVE_TRADING_ENABLED is not on CONFIG; it is read through the one
#: reader the gateway asks.
FLAGS: tuple[tuple[str, str, str], ...] = (
    ("AUTO_CONFIRM_LIVE_ENABLED", "auto_confirm_live_enabled", "switch"),
    ("AUTO_CONFIRM_THRESHOLD", "auto_confirm_threshold", "confidence floor, 0 to 1"),
    ("AUTO_CONFIRM_USE_CALIBRATED", "auto_confirm_use_calibrated", "switch"),
    ("MICRO_MAX_POSITION_USD", "execution.max_live_position_usd", "USD of MARGIN per position"),
    ("MICRO_MAX_TOTAL_EXPOSURE", "execution.max_live_total_exposure_usd", "USD of MARGIN across positions"),
    ("MICRO_MAX_OPEN_POSITIONS", "execution.max_live_open_positions", "positions"),
    ("LLM_DAILY_BUDGET_USD", "llm.daily_budget_usd", "USD per UTC day, chat and thesis together"),
    ("LLM_CHAT_BUDGET_SHARE", "llm.chat_budget_share", "share of the daily budget chat may spend"),
    ("LLM_DAILY_LIMIT", "llm.daily_call_limit", "thesis model calls per UTC day"),
    ("PER_USER_LIVE_ENABLED", "per_user_live_enabled", "switch"),
    ("WEB_LIVE_TRADING_ENABLED", _WEB_LIVE, "switch"),
    ("LIVE_OPEN_TO_KEY_HOLDERS", "live_open_to_key_holders", "switch"),
    ("PAPER_SIM_OPT_IN_ENABLED", "paper_sim_opt_in_enabled", "switch"),
    ("LIVE_TRADING_ENABLED", "live_trading_enabled", "switch"),
    ("SIMULATION_MODE", "simulation_mode", "switch"),
    ("DEFAULT_LEVERAGE", "exchange.default_leverage", "x (notional = margin x leverage)"),
    ("EXCHANGE_MIN_ROUNDUP_MAX_MULT", "exchange.exchange_min_roundup_max_mult",
     "largest venue-minimum round-up, x the risk-sized quantity"),
)


def _dotted(obj: Any, path: str) -> Any:
    for part in path.split("."):
        obj = getattr(obj, part)
    return obj


def env_unread_record(config_module: Any) -> dict:
    """``config.ENV_UNREAD``: every numeric knob whose value did not parse and
    runs its default, ``{key: {"reason", "default"}}``. A key read twice by
    the config (a bounded cap and its growth twin) is listed once."""
    record = getattr(config_module, "ENV_UNREAD", None)
    if not isinstance(record, list):
        return _absent("this build keeps no ENV_UNREAD record")
    rows: dict[str, dict] = {}
    for r in record:
        if isinstance(r, (tuple, list)) and len(r) >= 3:
            rows.setdefault(str(r[0]), {"reason": str(r[1]), "default": r[2]})
    return _read(keys=rows)


def _bool_text_unrecognised(config_module: Any, name: str) -> Optional[bool]:
    """Whether the environment holds text for ``name`` that the config's own
    boolean reader does not recognise, and so fell back to its default.

    Asked of the reader itself rather than of a copy of its vocabulary: for
    set, non-empty text the reader answers the same whatever the default
    unless it did not recognise the text. None when this build has no such
    reader to ask. It reads the process environment because that is what
    ``CONFIG`` was built from (``.env`` is loaded into it at import)."""
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return False
    reader = getattr(config_module, "_env_bool", None)
    if not callable(reader):
        return None
    log = logging.getLogger(getattr(config_module, "__name__", "bot.config"))
    was = log.disabled
    log.disabled = True  # the reader warns on every probe; the import already did
    try:
        return bool(reader(name, True) != reader(name, False))
    finally:
        log.disabled = was


def read_flags(config_module: Any, env: Mapping[str, str]) -> dict:
    """Every row of ``FLAGS``, three-valued. A numeric value that did not parse
    is the code default in force, with ``ENV_UNREAD``'s reason; a switch whose
    text the reader did not recognise is the default in force, said so."""
    cfg = getattr(config_module, "CONFIG", None)
    unread = env_unread_record(config_module)
    unread_keys: dict = unread["keys"] if unread["state"] == READ else {}
    out: dict[str, dict] = {}
    for name, where, unit in FLAGS:
        try:
            if where == _WEB_LIVE:
                from bot.web.web_live_gate import feature_enabled
                value = feature_enabled(dict(env))
            else:
                value = _dotted(cfg, where)
        except Exception as exc:  # noqa: BLE001 -- a reading that raised is unreadable
            out[name] = _unreadable(exc, unit=unit)
            continue
        if isinstance(value, float) and not math.isfinite(value):
            out[name] = _unreadable("not a finite number", unit=unit)
            continue
        entry = _read(value=value, unit=unit,
                      source="environment" if name in env else "code default")
        if name in unread_keys:
            entry["source"] = "code default (the environment's value did not parse)"
            entry["env_unread"] = unread_keys[name]["reason"]
        elif isinstance(value, bool) and where != _WEB_LIVE:
            odd = _bool_text_unrecognised(config_module, name)
            if odd is None:
                entry["env_check"] = "whether the text is a recognised boolean could not be asked"
            elif odd:
                entry["source"] = "code default (the environment's text is not a recognised boolean)"
                entry["env_unread"] = "not a recognised boolean"
        out[name] = entry
    return out


# ── Ledgers ────────────────────────────────────────────────────────────────

def _parse_time(raw: Any) -> Optional[datetime]:
    """An ISO-8601 time as UTC, or None. Every writer read here stamps an
    aware UTC time; a naive one is read as UTC, which is what the bot writes."""
    if not isinstance(raw, str) or not raw.strip():
        return None
    text = raw.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        when = datetime.fromisoformat(text)
    except ValueError:
        return None
    return when if when.tzinfo is not None else when.replace(tzinfo=timezone.utc)


class _Span:
    """The first and last time a ledger's rows state, and how many state one."""

    def __init__(self) -> None:
        self.first: Optional[datetime] = None
        self.last: Optional[datetime] = None
        self.dated = 0

    def add(self, when: Optional[datetime]) -> None:
        if when is None:
            return
        self.dated += 1
        if self.first is None or when < self.first:
            self.first = when
        if self.last is None or when > self.last:
            self.last = when

    def reading(self, rows: int) -> dict:
        if rows == 0:
            return _absent("the file holds no rows")
        if self.first is None or self.last is None:
            return _absent("no row states a readable time")
        return _read(first=self.first.isoformat(), last=self.last.isoformat(),
                     undated_rows=rows - self.dated)


def _missing(path: Path, shown: str, what: str = "file") -> Optional[dict]:
    """None when ``path`` is there; otherwise the field that says why not:
    absent when it is not there, unreadable when asking raised."""
    try:
        if path.exists():
            return None
    except OSError as exc:
        return _unreadable(exc, path=shown)
    return _absent(f"no {what} at this path", path=shown)


def scan_jsonl(path: Path, shown: str, time_key: str,
               tallies: Optional[Mapping[str, Callable[[dict], str]]] = None) -> dict:
    """One JSONL ledger, streamed (the order-flow file grows every tick). A
    line that will not parse, or is not an object, is a counted bad line and
    never silently dropped; a file that cannot be opened is unreadable."""
    missing = _missing(path, shown)
    if missing is not None:
        return missing
    tallies = tallies or {}
    rows = bad = 0
    span = _Span()
    counts: dict[str, Counter[str]] = {k: Counter() for k in tallies}
    try:
        with open(path, "rb") as fh:
            for line in fh:
                if not line.strip():
                    continue
                try:
                    obj = json.loads(line)
                except ValueError:  # UnicodeDecodeError is one
                    bad += 1
                    continue
                if not isinstance(obj, dict):
                    bad += 1
                    continue
                rows += 1
                span.add(_parse_time(obj.get(time_key)))
                for key, word_of in tallies.items():
                    counts[key][word_of(obj)] += 1
    except OSError as exc:
        return _unreadable(exc, path=shown)
    out = _read(path=shown, rows=rows, bad_lines=bad, dates=span.reading(rows))
    for key in tallies:
        out[key] = _sorted_counts(counts[key])
    return out


def _load_json(path: Path, shown: str) -> tuple[dict, Any]:
    """``(field, data)``: data is the parsed JSON only when field is read."""
    missing = _missing(path, shown)
    if missing is not None:
        return missing, None
    try:
        with open(path, "rb") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as exc:
        return _unreadable(exc, path=shown), None
    return _read(path=shown), data


def source_family(source: Any) -> str:
    """A thesis ``source`` as LLM, RULE_ENGINE or other. The analyzer writes
    ``LLM_<tier>``, ``LLM_FALLBACK_<provider>`` and ``..._CACHED`` for a model
    answer, and ``RULE_ENGINE`` or ``RULE_ENGINE_<why>`` for every rule path;
    anything else, an absent source included, is ``other`` and is never
    folded into either."""
    text = str(source or "")
    if text.startswith("LLM"):
        return "LLM"
    if text.startswith("RULE_ENGINE"):
        return "RULE_ENGINE"
    return "other"


def decision_type(decision: Any) -> str:
    """A decision row's type. Outcome rows spell the trade or audit id into
    the field (``OUTCOME:<trade>``, ``RESULT_FOR:<audit>``); the type is the
    part before the colon, so the tally counts kinds rather than ids."""
    text = str(decision or "").strip()
    if not text:
        return "(not recorded)"
    return text.split(":", 1)[0]


def _word(value: Any) -> str:
    text = str(value or "").strip()
    return text or "(not recorded)"


def llm_rule_share(calibration: dict) -> dict:
    """The LLM vs RULE_ENGINE share of the calibration rows, off the same scan."""
    if calibration["state"] != READ:
        return {k: v for k, v in calibration.items() if k != "path"}
    n = calibration["rows"]
    if n == 0:
        return _absent("the calibration file holds no rows")
    # Every family named, a family no row fell in included: the scan counted
    # every row, so a family it never met is a counted zero.
    counts = Counter({"LLM": 0, "RULE_ENGINE": 0, "other": 0})
    counts.update(calibration["by_source_family"])
    return _read(rows=n, counts=dict(counts),
                 percent={k: round(100 * v / n, 1) for k, v in counts.items()})


# ── Closed trades ──────────────────────────────────────────────────────────

def price_basis(row: dict) -> str:
    """How one closed record's exit was priced, from the record's own fields.

    ``ticker``: ``close_lookup.is_ticker_priced`` (every ticker spelling).
    ``no_exit_price``: the record holds no exit price (an unpriced or unread
    close: ``unread``, ``unpriced_after_N_retries``, ``final_fill_unread``).
    ``unrecorded``: an exit price and no ``fill_source`` (an older build, or
    the emergency close, which records none) -- not counted as venue-priced.
    ``venue``: a non-ticker source and an exit price on record."""
    from bot.core.close_lookup import is_ticker_priced
    from bot.core.position_telemetry import price_on_record

    src = row.get("fill_source")
    if is_ticker_priced(src):
        return "ticker"
    if price_on_record(row.get("close_price")) is None:
        return "no_exit_price"
    if not str(src or "").strip():
        return "unrecorded"
    return "venue"


def read_closed_trades(path: Path, shown: str, last: int = RECENT_CLOSES) -> tuple[dict, dict]:
    """``(ledger, venue_priced)`` for the operator's closed-trade record.

    The executor writes a JSON LIST, oldest first; anything else is what its
    own loader refuses, so it is unreadable here too. The venue-priced share
    is read over the newest ``last`` FILLED closes (``parity.partition``'s
    reading: every row but the non-fills), in the file's order."""
    field, data = _load_json(path, shown)
    if field["state"] != READ:
        share = {k: v for k, v in field.items() if k != "path"}
        return field, share
    if not isinstance(data, list):
        bad = _unreadable("not a list of rows, which is what the executor writes", path=shown)
        return bad, {k: v for k, v in bad.items() if k != "path"}
    rows = [r for r in data if isinstance(r, dict)]
    span = _Span()
    for r in rows:
        span.add(_parse_time(r.get("closed_at")))
    ledger = _read(path=shown, rows=len(rows), bad_rows=len(data) - len(rows),
                   dates=span.reading(len(rows)))
    try:
        from bot.backtest.parity import cause_sentence, inferred_causes, partition
        parts = partition(rows)
    except Exception as exc:  # noqa: BLE001 -- a reader that raised is unreadable
        ledger["kinds"] = _unreadable(exc)
        return ledger, _unreadable(exc)
    ledger["kinds"] = _read(counts={k: len(v) for k, v in parts.items()})
    non_fills = {id(r) for r in parts["non_fills"]}
    filled = [r for r in rows if id(r) not in non_fills]
    window = filled[-last:] if last > 0 else []
    if not window:
        return ledger, _absent(f"no filled close on record ({len(rows)} row(s) read, none a fill)")
    basis: dict[str, int] = {"venue": 0, "ticker": 0, "no_exit_price": 0, "unrecorded": 0}
    for r in window:
        basis[price_basis(r)] += 1
    n = len(window)
    share = _read(window=n, asked=last, counts=basis,
                  venue_priced_percent=round(100 * basis["venue"] / n, 1),
                  ticker_causes=inferred_causes(window))
    sentence = cause_sentence(share["ticker_causes"])
    if sentence:
        share["ticker_cause_sentence"] = sentence
    return ledger, share


def _globbed(base: Path, pattern: str) -> tuple[list[Path], Optional[BaseException]]:
    try:
        return sorted(p for p in base.glob(pattern) if p.is_file()), None
    except OSError as exc:
        return [], exc


def read_per_user_closed(state_dir: Path) -> dict:
    """Per-user and per-venue closed-trade files, in aggregate only: their
    names carry user ids, which do not belong in a committed document."""
    paths: list[Path] = []
    for pattern in ("closed_trades_*.json", "venue/*/closed_trades_*.json"):
        found, err = _globbed(state_dir, pattern)
        if err is not None:
            return _unreadable(err)
        paths += found
    if not paths:
        return _absent("no per-user or per-venue closed-trade file found")
    rows = 0
    failed: Counter[str] = Counter()
    for p in paths:
        field, data = _load_json(p, "")
        if field["state"] != READ:
            failed[field.get("error") or field["state"]] += 1
            continue
        if not isinstance(data, list):
            failed["not a list of rows"] += 1
            continue
        rows += sum(1 for r in data if isinstance(r, dict))
    return _read(files=len(paths), files_unreadable=_sorted_counts(failed),
                 rows=rows, rows_are_a_floor=bool(failed))


# ── Positions with unread margin ───────────────────────────────────────────

#: The origins the adoption paths write: an orphan found on the venue, and
#: this bot's own resting limit order found again. Both are written with the
#: margin unread when the venue did not state it.
ADOPTION_ORIGINS = ("adopted", "reclaimed")


def margin_on_record(row: dict) -> Optional[float]:
    """A position row's margin, or None when the record holds none.

    ``live_executor.position_size_basis`` is the reading of a position's
    margin (``cost_usd`` when it is a finite number above zero). It cannot be
    imported here without opening the bot's log files (see the module
    docstring), so this asks ``position_telemetry.price_on_record``, which
    applies the same test, and a test pins the two to agree."""
    from bot.core.position_telemetry import price_on_record

    return price_on_record(row.get("cost_usd"))


def read_positions(state_dir: Path, root: Path) -> dict:
    """The operator's and every per-user positions file, as last saved: how
    many rows, by origin, and how many carry no margin on record."""
    operator = state_dir / "live_positions.json"
    others: list[Path] = []
    for pattern in ("live_positions_*.json", "venue/*/live_positions_*.json"):
        found, err = _globbed(state_dir, pattern)
        if err is not None:
            return _unreadable(err)
        others += found
    files: list[dict] = []
    rows: list[dict] = []
    op_field, op_data = _load_json(operator, _shown(root, operator))
    candidates = [(op_field, op_data)] + [_load_json(p, "per-user book") for p in others]
    for field, data in candidates:
        if field["state"] == READ and not isinstance(data, dict):
            field = _unreadable("not a map of rows, which is what the executor writes", path=field["path"])
        files.append(dict(field))
        if field["state"] == READ:
            rows += [r for r in data.values() if isinstance(r, dict)]
    read_files = [f for f in files if f["state"] == READ]
    failed = [f for f in files if f["state"] == UNREADABLE]
    if not read_files:
        if failed:
            return _unreadable("no positions file could be read", files=files)
        return _absent("no positions file found", files=files)
    by_origin: Counter[str] = Counter()
    margin_unread = adopted_margin_unread = marked = 0
    for r in rows:
        origin = _word(r.get("origin"))
        by_origin[origin] += 1
        if margin_on_record(r) is None:
            margin_unread += 1
            if origin in ADOPTION_ORIGINS:
                adopted_margin_unread += 1
        unread_names = r.get("adoption_unread")
        if isinstance(unread_names, list) and "margin" in unread_names:
            marked += 1
    return _read(files=files, rows=len(rows), by_origin=_sorted_counts(by_origin),
                 margin_unread=margin_unread, adopted_margin_unread=adopted_margin_unread,
                 marked_margin_unread=marked, counts_are_a_floor=bool(failed))


# ── Logs: budget-exhausted audits and tick health ──────────────────────────

#: The audit messages that say a budget stopped a model call, matched on the
#: message's start (the figures inside it do not matter and are not printed).
#: A test reads the analyzer and the chat handler and requires these to be
#: the budget audits they write, both ways.
BUDGET_AUDITS: tuple[tuple[str, str], ...] = (
    ("thesis_dollar_budget", "LLM daily dollar budget exhausted"),
    ("thesis_call_limit", "LLM daily budget exhausted"),
    ("chat_budget", "Chat LLM budget exhausted"),
)

#: Tick-health audit rows, by (action, result): the nearest thing to a
#: monitor-pass gap this build records. The gap itself is not recorded.
TICK_AUDITS: tuple[tuple[str, str, str], ...] = (
    ("tick_phase_timeout", "tick_phase", "TIMEOUT"),
    ("tick_hard_timeout", "tick", "HARD_TIMEOUT"),
    ("monitor_loop_stalled", "monitor_liveness", "CRITICAL"),
)

_LOG_NAME = re.compile(r"^(?!audit_chain)[\w.-]+\.jsonl(\.\d+)?$")
_PREFILTER = (b"budget exhausted", b'"tick', b"monitor_liveness")


def _classify_log(entry: dict) -> Optional[str]:
    message = str(entry.get("message") or "")
    for label, prefix in BUDGET_AUDITS:
        if message.startswith(prefix):
            return label
    action, result = entry.get("action"), entry.get("result")
    for label, want_action, want_result in TICK_AUDITS:
        if action == want_action and result == want_result:
            return label
    return None


def scan_logs(logs_dir: Path, shown: str) -> dict:
    """Count the budget and tick audit rows across every JSON log in
    ``logs_dir`` (``<channel>.jsonl`` and its rotations ``.1`` to ``.5``). The
    audit chain is a different record and is not read."""
    missing = _missing(logs_dir, shown, "logs directory")
    if missing is not None:
        return missing
    try:
        names = sorted(p.name for p in logs_dir.iterdir() if _LOG_NAME.match(p.name))
    except OSError as exc:
        return _unreadable(exc, path=shown)
    if not names:
        return _absent("no JSON log files in the logs directory", path=shown)
    labels = [b[0] for b in BUDGET_AUDITS] + [t[0] for t in TICK_AUDITS]
    counts = {k: 0 for k in labels}
    spans = {k: _Span() for k in labels}
    failed: dict[str, str] = {}
    bad = 0
    for name in names:
        try:
            with open(logs_dir / name, "rb") as fh:
                for line in fh:
                    if not any(n in line for n in _PREFILTER):
                        continue
                    try:
                        entry = json.loads(line)
                    except ValueError:
                        bad += 1
                        continue
                    if not isinstance(entry, dict):
                        bad += 1
                        continue
                    label = _classify_log(entry)
                    if label is None:
                        continue
                    counts[label] += 1
                    spans[label].add(_parse_time(entry.get("ts")))
        except OSError as exc:
            failed[name] = type(exc).__name__
    if len(failed) == len(names):
        return _unreadable("no log file could be read", path=shown, files_unreadable=failed)
    audits = {}
    for label in labels:
        audits[label] = _read(count=counts[label],
                              dates=spans[label].reading(counts[label]) if counts[label]
                              else _absent("no such line"))
    return _read(path=shown, files=[n for n in names if n not in failed],
                 files_unreadable=failed, counts_are_a_floor=bool(failed),
                 candidate_lines_unparsed=bad, audits=audits,
                 monitor_pass_gap=_absent("this build records no per-pass monitor gap; plan item D3 adds it"))


# ── Eligibility ────────────────────────────────────────────────────────────

def read_eligibility_state(root: Path) -> dict:
    """``live_eligibility.read_eligibility`` for the code in ``root``. The
    running process hashed its code when it started, so a checkout changed
    since then answers for the new code, not the running one."""
    try:
        from bot.core.live_eligibility import read_eligibility
        got = read_eligibility(root=root)
    except Exception as exc:  # noqa: BLE001 -- the reader never raises; its import can
        return _unreadable(exc)
    return _read(verdict=got.state, reason=got.reason,
                 strategy_hash_prefix=(got.strategy_hash[:12] or None), stage=got.stage)


# ── The whole readout ──────────────────────────────────────────────────────

def _anchored(root: Path, raw: Optional[str], default: str) -> Path:
    """A relative state path anchored at ``root``, an absolute one as given.
    The bot resolves ``RUNECLAW_STATE_DIR`` against its working directory,
    which the launcher and the systemd unit set to the checkout."""
    p = Path((raw or "").strip() or default).expanduser()
    return p if p.is_absolute() else root / p


def _shown(root: Path, p: Path) -> str:
    try:
        return p.relative_to(root).as_posix()
    except ValueError:
        return p.as_posix()


def build_readout(root: Path, config_module: Any, env: Optional[Mapping[str, str]] = None,
                  *, now: Optional[datetime] = None, last: int = RECENT_CLOSES) -> dict:
    """The readout as one JSON-able dict. Reads files under ``root``; writes
    nothing. ``config_module`` is ``bot.config`` (or None when it could not
    be imported, and every flag says so)."""
    root = Path(root)
    env = dict(os.environ) if env is None else dict(env)
    state_dir = _anchored(root, env.get("RUNECLAW_STATE_DIR"), "data")
    learning = root / "data" / "learning"
    of_path = _anchored(root, env.get("OF_SNAPSHOT_PATH"), "data/learning/order_flow_snapshots.jsonl")

    if config_module is None:
        flags = {name: _unreadable("bot.config could not be imported", unit=unit) for name, _, unit in FLAGS}
        env_unread = _unreadable("bot.config could not be imported")
    else:
        flags = read_flags(config_module, env)
        env_unread = env_unread_record(config_module)

    calibration = scan_jsonl(
        learning / "llm_calibration.jsonl", _shown(root, learning / "llm_calibration.jsonl"), "ts",
        {"by_source_family": lambda r: source_family(r.get("llm_source")),
         "by_source": lambda r: _word(r.get("llm_source"))})
    order_flow = scan_jsonl(of_path, _shown(root, of_path), "ts")
    decisions = scan_jsonl(
        learning / "decision_memory.jsonl", _shown(root, learning / "decision_memory.jsonl"),
        "timestamp_utc",
        {"by_decision_type": lambda r: decision_type(r.get("decision")),
         "by_source": lambda r: _word(r.get("source"))})
    closed_path = state_dir / "closed_trades.json"
    closed, venue_priced = read_closed_trades(closed_path, _shown(root, closed_path), last)

    return {
        "readout": "production_readout",
        "schema": 1,
        "generated_at": (now or datetime.now(timezone.utc)).isoformat(),
        "flags": flags,
        "env_unread": env_unread,
        "ledgers": {
            "llm_calibration": calibration,
            "order_flow_snapshots": order_flow,
            "decision_memory": decisions,
            "closed_trades": closed,
            "closed_trades_per_user": read_per_user_closed(state_dir),
        },
        "llm_rule_share": llm_rule_share(calibration),
        "venue_priced_closes": venue_priced,
        "logs": scan_logs(root / "logs", "logs"),
        "positions": read_positions(state_dir, root),
        "eligibility": read_eligibility_state(root),
    }


# ── Markdown ───────────────────────────────────────────────────────────────

def _state_text(field: dict) -> str:
    """The words for a field that is not a reading."""
    if field["state"] == ABSENT:
        return f"absent ({field['reason']})"
    why = field.get("error") or field.get("reason") or "no reason recorded"
    return f"unreadable ({why})"


def _value_text(value: Any) -> str:
    if isinstance(value, bool):
        return "on" if value else "off"
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


def _counts_text(counts: Mapping[str, int]) -> str:
    return " · ".join(f"{k} ×{v}" for k, v in counts.items()) or "none"


def _dates_text(dates: dict) -> str:
    if dates["state"] != READ:
        return _state_text(dates)
    text = f"{dates['first'][:16].replace('T', ' ')} to {dates['last'][:16].replace('T', ' ')} UTC"
    if dates["undated_rows"]:
        text += f" ({dates['undated_rows']} row(s) state no readable time)"
    return text


def render_markdown(r: dict) -> str:
    out = [f"## Appendix: production readout, {r['generated_at'][:16].replace('T', ' ')} UTC", "",
           "Produced by `scripts/production_readout.py` (read-only). Every figure it could not "
           "read says `absent` or `unreadable`; none is a zero standing in for an absence.", "",
           "### Flags, caps and budgets (as `bot.config` resolves them)", "",
           "| Setting | Value in force | Unit | Source |", "|---|---|---|---|"]
    for name, f in r["flags"].items():
        if f["state"] != READ:
            out.append(f"| `{name}` | {_state_text(f)} | {f.get('unit', '')} | - |")
            continue
        source = f["source"] + (f"; {f['env_unread']}" if f.get("env_unread") else "")
        out.append(f"| `{name}` | {_value_text(f['value'])} | {f['unit']} | {source} |")
    eu = r["env_unread"]
    if eu["state"] != READ:
        out.append(f"\n- numeric knobs that did not parse: {_state_text(eu)}")
    elif eu["keys"]:
        out.append("\n- numeric knobs that did not parse and run their default: "
                   + ", ".join(f"`{k}` ({v['reason']}; default {_value_text(v['default'])})"
                               for k, v in eu["keys"].items()))
    else:
        out.append("\n- numeric knobs that did not parse: none (config.ENV_UNREAD is empty)")

    out += ["", "### Ledgers", "", "| Ledger | Path | Rows | Date range |", "|---|---|---|---|"]
    names = {"llm_calibration": "LLM calibration", "order_flow_snapshots": "order-flow snapshots",
             "decision_memory": "decision memory", "closed_trades": "closed trades (operator)"}
    for key, label in names.items():
        led = r["ledgers"][key]
        if led["state"] != READ:
            out.append(f"| {label} | `{led.get('path', '')}` | {_state_text(led)} | - |")
            continue
        extra = led["bad_lines"] if "bad_lines" in led else led["bad_rows"]
        rows = f"{led['rows']}" + (f" (+{extra} unreadable row(s), not counted)" if extra else "")
        out.append(f"| {label} | `{led['path']}` | {rows} | {_dates_text(led['dates'])} |")
    out.append("")
    dec = r["ledgers"]["decision_memory"]
    if dec["state"] == READ:
        out.append(f"- decision rows by type: {_counts_text(dec['by_decision_type'])}")
        out.append(f"- decision rows by source: {_counts_text(dec['by_source'])}")
    closed = r["ledgers"]["closed_trades"]
    if closed["state"] == READ:
        kinds = closed["kinds"]
        out.append("- closed-trade rows by kind (parity's partition): "
                   + (_counts_text(kinds["counts"]) if kinds["state"] == READ else _state_text(kinds)))
    pu = r["ledgers"]["closed_trades_per_user"]
    if pu["state"] != READ:
        out.append(f"- per-user / per-venue closed-trade files: {_state_text(pu)}")
    else:
        line = f"- per-user / per-venue closed-trade files: {pu['files']} found, {pu['rows']} row(s) read"
        if pu["files_unreadable"]:
            line += (f"; unreadable: {_counts_text(pu['files_unreadable'])}, so the row count "
                     f"is a floor")
        out.append(line)

    out += ["", "### LLM vs RULE_ENGINE share (llm_calibration rows)", ""]
    share = r["llm_rule_share"]
    if share["state"] != READ:
        out.append(f"- {_state_text(share)}")
    else:
        out.append(f"- over {share['rows']} row(s): "
                   + " · ".join(f"{k} {share['counts'][k]} ({share['percent'][k]}%)" for k in share["counts"]))
        cal = r["ledgers"]["llm_calibration"]
        out.append(f"- by recorded source: {_counts_text(cal['by_source'])}")

    out += ["", "### Venue-priced share of the most recent filled closes (operator record)", ""]
    vp = r["venue_priced_closes"]
    if vp["state"] != READ:
        out.append(f"- {_state_text(vp)}")
    else:
        c = vp["counts"]
        out.append(f"- over the newest {vp['window']} filled close(s) (asked for {vp['asked']}): "
                   f"venue-priced {c['venue']} "
                   f"({vp['venue_priced_percent']}%) · ticker-priced {c['ticker']} · no exit price "
                   f"{c['no_exit_price']} · no fill source recorded {c['unrecorded']}")
        if c["unrecorded"]:
            out.append("- a close with no fill source recorded is not counted as venue-priced, so "
                       "the share is a floor")
        if vp.get("ticker_cause_sentence"):
            out.append(f"- ticker-priced: {vp['ticker_cause_sentence']}")

    out += ["", "### Budget-exhausted audits and tick health (logs)", ""]
    logs = r["logs"]
    if logs["state"] != READ:
        out.append(f"- `{logs.get('path', 'logs')}`: {_state_text(logs)}")
    else:
        out.append(f"- read {len(logs['files'])} log file(s)"
                   + (f"; could not read {', '.join(f'{k} ({v})' for k, v in logs['files_unreadable'].items())}, "
                      f"so every count is a floor" if logs["files_unreadable"] else "")
                   + (f"; {logs['candidate_lines_unparsed']} candidate line(s) would not parse"
                      if logs["candidate_lines_unparsed"] else ""))
        for label, a in logs["audits"].items():
            span = f" ({_dates_text(a['dates'])})" if a["count"] else ""
            out.append(f"- {label.replace('_', ' ')}: {a['count']}{span}")
        out.append(f"- monitor-pass gap: {_state_text(logs['monitor_pass_gap'])}")
        out.append("- the logs rotate (10 MB x 5 per channel), so the counts cover what rotation "
                   "kept, not the bot's lifetime")

    out += ["", "### Adopted positions with unread margin", ""]
    pos = r["positions"]
    if pos["state"] != READ:
        out.append(f"- {_state_text(pos)}")
    else:
        out.append(f"- rows open or resting: {pos['rows']} · by origin: {_counts_text(pos['by_origin'])}")
        out.append(f"- margin not on record: {pos['margin_unread']}, of them adopted or reclaimed: "
                   f"{pos['adopted_margin_unread']} (rows still marked `margin` in adoption_unread: "
                   f"{pos['marked_margin_unread']})")
    for f in (pos.get("files") or []):
        if f["state"] == UNREADABLE:
            out.append(f"- `{f.get('path') or 'a positions file'}`: {_state_text(f)}"
                       + (" (the counts above are a floor)" if pos["state"] == READ else ""))

    out += ["", "### Live eligibility (autonomous live orders)", ""]
    el = r["eligibility"]
    if el["state"] != READ:
        out.append(f"- {_state_text(el)}")
    else:
        stage = f" · stage {el['stage']}" if el["stage"] else ""
        prefix = el["strategy_hash_prefix"] or "(unidentified)"
        out.append(f"- verdict: **{el['verdict']}**{stage} · strategy `{prefix}`")
        out.append(f"- {el['reason']}")
        out.append("- the hash is of the `bot/` code in this checkout; the running process hashed its "
                   "code when it started")
    return "\n".join(out) + "\n"


# ── Entry point ────────────────────────────────────────────────────────────

def import_config_without_vault() -> Any:
    """``bot.config`` with the secrets vault's self-heal replaced by a no-op,
    so the import writes nothing. None when the config cannot be imported (a
    setting it refuses to start with raises SystemExit, whose text names the
    value and is not printed)."""
    try:
        import bot.core.secrets_vault as vault

        def _no_seed() -> dict:
            return {"seeded": [], "restored": [], "unreadable": []}

        vault.seed_and_restore = _no_seed  # type: ignore[assignment]
    except Exception:  # noqa: BLE001 -- config guards its own vault import too
        pass
    try:
        import bot.config as config_module
    except (Exception, SystemExit):  # noqa: BLE001
        return None
    return config_module


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    fmt = parser.add_mutually_exclusive_group()
    fmt.add_argument("--json", action="store_true", help="print the readout as JSON")
    fmt.add_argument("--markdown", action="store_true", help="print the ADR appendix (the default)")
    parser.add_argument("--root", default=str(_REPO),
                        help="the checkout the bot runs from (default: this one)")
    parser.add_argument("--last", type=int, default=RECENT_CLOSES,
                        help="how many recent filled closes the venue-priced share reads")
    args = parser.parse_args(argv)
    # No .pyc beside the bot's modules either: an import that writes bytecode
    # is a write into the checkout this promises to leave as it found it. Set
    # here, not at import, so a test that loads this module keeps its own.
    sys.dont_write_bytecode = True
    config_module = import_config_without_vault()
    readout = build_readout(Path(args.root), config_module, os.environ, last=args.last)
    if args.json:
        sys.stdout.write(json.dumps(readout, indent=2, default=str) + "\n")
    else:
        sys.stdout.write(render_markdown(readout))
    return 0


if __name__ == "__main__":
    sys.exit(main())
